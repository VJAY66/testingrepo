from django.conf import settings
from django.contrib.auth.models import User
from users.models import Profile
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login, authenticate, logout
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.core.cache import cache
from django.core.paginator import Paginator
from django.utils.http import url_has_allowed_host_and_scheme
from django.db.models import Q, F, Count, IntegerField, ExpressionWrapper
from django.db import DataError, IntegrityError
from django.utils import timezone
from datetime import timedelta
import json
import random
import re
import uuid

from discussions.models import CATEGORY_CHOICES, Post, Comment, Debate, DebateMessage, DebateParticipant, CommentModeratorBlock, CommentReaction, PostFollow, PostView, PostAction, Notification, PostEditHistory, CommentEditHistory, DebateMessageEditHistory, DebateMessageReaction, DebateMessageReport, ProfileReport, Poll, PollOption, PollVote, PollComment, PollCommentReaction
from discussions.signals import notify_post_author
from users.models import Follow
from users.security import is_login_rate_limited, record_login_attempt
from discussions.limits import has_reached_daily_post_limit
from utils.moderation import check_content_moderation


_EMOJI_TOKEN_RE = re.compile(r'__EMJ__([0-9A-F]{5,6})__')
_OPEN_ENDED_START_RE = re.compile(r'^(what|why|how|when|where|which|who|whom|whose)\b', re.IGNORECASE)


def _normalize_post_content(content):
    """Trim post content and collapse repeated empty lines to reduce visual gaps."""
    text = (content or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    # Keep intentional paragraph spacing, but avoid huge blank blocks.
    return re.sub(r'\n{3,}', '\n\n', text)


def _is_yes_no_question(title):
    """Return True when title is likely answerable with yes/no."""
    text = re.sub(r'\s+', ' ', (title or '').strip())
    if not text:
        return False
    if not text.endswith('?'):
        return False

    lowered = text.lower().lstrip('"\'(“”‘’[{')
    if _OPEN_ENDED_START_RE.match(lowered):
        return False

    # Keep this permissive so users are guided by popup examples, not rigid starter words.
    return True


def _public_rate_limit_key(request, scope):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR', 'unknown')
    return f'public_rate_limit:{scope}:{ip}'


def _is_public_rate_limited(request, scope, limit, window_seconds):
    key = _public_rate_limit_key(request, scope)
    current = cache.get(key)
    if current is None:
        cache.set(key, 1, window_seconds)
        return False
    if current >= limit:
        return True
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, current + 1, window_seconds)
    return False


def _manual_editor_username():
    return (getattr(settings, 'MANUAL_EDITOR_USERNAME', '') or '').strip()


def _manual_editor_password():
    return getattr(settings, 'MANUAL_EDITOR_PASSWORD', '') or ''


def _manual_editor_email():
    return (getattr(settings, 'MANUAL_EDITOR_EMAIL', '') or '').strip()


def _configured_moderator_usernames():
    raw = getattr(settings, 'MODERATOR_USERNAMES', []) or []
    return {str(name).strip().lower() for name in raw if str(name).strip()}


def _is_configured_moderator(user):
    if not getattr(user, 'is_authenticated', False):
        return False
    return user.username.lower() in _configured_moderator_usernames()


def _moderator_users():
    names = list(_configured_moderator_usernames())
    if not names:
        return User.objects.none()
    return User.objects.filter(username__iregex=r'^(' + '|'.join(re.escape(name) for name in names) + r')$')


def _safe_next_url(request, next_url):
    if not next_url:
        return ''
    if url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return ''


def _ensure_manual_editor_user():
    username = _manual_editor_username()
    password = _manual_editor_password()
    email = _manual_editor_email()

    if not username or not password:
        return None

    user, _ = User.objects.get_or_create(username=username, defaults={'email': email})
    update_fields = []

    if email and user.email != email:
        user.email = email
        update_fields.append('email')

    if not user.check_password(password):
        user.set_password(password)
        update_fields.append('password')

    if not user.is_active:
        user.is_active = True
        update_fields.append('is_active')

    if update_fields:
        user.save(update_fields=update_fields)

    return user


def _is_manual_editor_user(user):
    configured_username = _manual_editor_username()
    return bool(
        configured_username
        and getattr(user, 'is_authenticated', False)
        and user.username == configured_username
    )


def _can_manage_created_today(user, created_at):
    if not _is_manual_editor_user(user):
        return True
    return timezone.localdate(created_at) == timezone.localdate()


def _encode_chat_content_for_storage(content):
    """Encode astral emoji into ASCII-safe tokens for DBs without utf8mb4."""
    if not content:
        return ''
    out = []
    for ch in content:
        code = ord(ch)
        if code > 0xFFFF:
            out.append(f'__EMJ__{code:06X}__')
        else:
            out.append(ch)
    return ''.join(out)


def _decode_chat_content_from_storage(content):
    if not content:
        return ''
    return _EMOJI_TOKEN_RE.sub(lambda m: chr(int(m.group(1), 16)), content)


def _opposite_side(side):
    return 'no' if side == 'yes' else 'yes'


def _debate_primary_sides(debate):
    target_side = debate.comment.vote_type
    initiator_side = _opposite_side(target_side)
    return initiator_side, target_side


def _ensure_debate_core_participants(debate):
    initiator_side, target_side = _debate_primary_sides(debate)

    initiator_participant, _ = DebateParticipant.objects.get_or_create(
        debate=debate,
        user=debate.initiator,
        defaults={'side': initiator_side, 'is_active': True}
    )
    initiator_updated_fields = []
    if initiator_participant.side != initiator_side:
        initiator_participant.side = initiator_side
        initiator_updated_fields.append('side')
    if not initiator_participant.is_active:
        initiator_participant.is_active = True
        initiator_updated_fields.append('is_active')
    if initiator_participant.left_at is not None:
        initiator_participant.left_at = None
        initiator_updated_fields.append('left_at')
    if initiator_updated_fields:
        initiator_participant.save(update_fields=initiator_updated_fields)

    target_participant, _ = DebateParticipant.objects.get_or_create(
        debate=debate,
        user=debate.target,
        defaults={'side': target_side, 'is_active': True}
    )
    target_updated_fields = []
    if target_participant.side != target_side:
        target_participant.side = target_side
        target_updated_fields.append('side')
    if not target_participant.is_active:
        target_participant.is_active = True
        target_updated_fields.append('is_active')
    if target_participant.left_at is not None:
        target_participant.left_at = None
        target_updated_fields.append('left_at')
    if target_updated_fields:
        target_participant.save(update_fields=target_updated_fields)


def _pick_debate_side_for_user(debate, desired_side, active_counts=None, fallback_side=None):
    """Choose a side for joining an accepted debate, preferring sides with room."""
    if desired_side in ('yes', 'no'):
        return desired_side

    if active_counts is None:
        active_counts = {
            item['side']: item['total']
            for item in DebateParticipant.objects.filter(
                debate=debate,
                is_active=True,
            ).values('side').annotate(total=Count('id'))
        }

    yes_active = active_counts.get('yes', 0)
    no_active = active_counts.get('no', 0)
    yes_room = (debate.yes_supporters <= 0) or (yes_active < debate.yes_supporters)
    no_room = (debate.no_supporters <= 0) or (no_active < debate.no_supporters)

    if yes_room and not no_room:
        return 'yes'
    if no_room and not yes_room:
        return 'no'
    if yes_room and no_room:
        if yes_active < no_active:
            return 'yes'
        if no_active < yes_active:
            return 'no'
        if fallback_side in ('yes', 'no'):
            return fallback_side
        return 'yes'

    # Both sides are full; return a deterministic side for queue placement.
    if fallback_side in ('yes', 'no'):
        return fallback_side
    return 'yes'


def _set_end_controller_with_fallback(debate, preferred_side, exclude_user_id=None):
    same_side = DebateParticipant.objects.filter(
        debate=debate,
        side=preferred_side,
        is_active=True,
    )
    if exclude_user_id:
        same_side = same_side.exclude(user_id=exclude_user_id)
    same_side = same_side.order_by('joined_at').select_related('user')

    chosen = same_side.first()
    if not chosen:
        opposite = DebateParticipant.objects.filter(
            debate=debate,
            side=_opposite_side(preferred_side),
            is_active=True,
        )
        if exclude_user_id:
            opposite = opposite.exclude(user_id=exclude_user_id)
        chosen = opposite.order_by('joined_at').select_related('user').first()

    if chosen:
        debate.end_controller = chosen.user
        debate.end_controller_side = chosen.side
    else:
        debate.end_controller = None
        debate.end_controller_side = ''

    debate.save(update_fields=['end_controller', 'end_controller_side', 'updated_at'])


def _safe_avatar_url(user):
    """Return best available picture URL if profile exists, else empty string."""
    try:
        return user.profile.get_picture_url
    except Profile.DoesNotExist:
        return ''


def _is_user_online(user, cutoff=None):
    if cutoff is None:
        cutoff = timezone.now() - timedelta(minutes=5)

    try:
        last_seen = user.profile.last_seen
    except Profile.DoesNotExist:
        return False

    return bool(last_seen and last_seen >= cutoff)


def _get_last_seen(user):
    try:
        return user.profile.last_seen
    except Profile.DoesNotExist:
        return None


def _presence_label(last_seen, now=None):
    if not last_seen:
        return 'Last seen unavailable'

    if now is None:
        now = timezone.now()

    delta = now - last_seen
    seconds = max(int(delta.total_seconds()), 0)

    if seconds < 60:
        return 'Last seen just now'

    minutes = seconds // 60
    if minutes < 60:
        return f'Last seen {minutes}m ago'

    hours = minutes // 60
    if hours < 24:
        return f'Last seen {hours}h ago'

    days = hours // 24
    return f'Last seen {days}d ago'


def _is_blocked_by_comment_owner(comment_owner, blocked_user):
    return CommentModeratorBlock.objects.filter(
        comment_owner=comment_owner,
        blocked_user=blocked_user,
    ).exists()


def _active_participants_payload(debate, viewer):
    now = timezone.now()
    online_cutoff = now - timedelta(minutes=5)
    participants = DebateParticipant.objects.filter(
        debate=debate,
        is_banned=False,
    ).select_related('user', 'user__profile').order_by('joined_at')

    payload = []
    for participant in participants:
        last_seen = _get_last_seen(participant.user)
        payload.append({
            'id': participant.user_id,
            'username': participant.user.username,
            'side': participant.side,
            'is_active': participant.is_active,
            'is_self': participant.user_id == viewer.id,
            'is_online': bool(last_seen and last_seen >= online_cutoff),
            'presence_label': _presence_label(last_seen, now=now),
            'avatar_url': _safe_avatar_url(participant.user),
            'can_remove': viewer.id == debate.target_id and participant.user_id != viewer.id,
        })

    return payload


def _sender_side_map(debate):
    """Return {user_id: side} for all participants of a debate."""
    return {
        p.user_id: p.side
        for p in DebateParticipant.objects.filter(debate=debate).only('user_id', 'side')
    }


def _debate_message_reaction_maps(message_ids, viewer_id):
    """Return reaction counters and viewer reaction lookup for debate messages."""
    if not message_ids:
        return {}, {}, {}

    base_qs = DebateMessageReaction.objects.filter(message_id__in=message_ids)
    likes_map = {
        row['message_id']: row['total']
        for row in base_qs.filter(reaction='like').values('message_id').annotate(total=Count('id'))
    }
    dislikes_map = {
        row['message_id']: row['total']
        for row in base_qs.filter(reaction='dislike').values('message_id').annotate(total=Count('id'))
    }
    viewer_map = {
        row['message_id']: row['reaction']
        for row in base_qs.filter(user_id=viewer_id).values('message_id', 'reaction')
    }
    return likes_map, dislikes_map, viewer_map

FRONTEND_CATEGORY_STYLES = {
    'Technology': {'icon': '💻', 'color': 'bg-blue-500/10 text-blue-400 border-blue-500/20'},
    'Relationships': {'icon': '💞', 'color': 'bg-rose-500/10 text-rose-400 border-rose-500/20'},
    'Photography': {'icon': '📷', 'color': 'bg-sky-500/10 text-sky-400 border-sky-500/20'},
    'Music': {'icon': '🎵', 'color': 'bg-fuchsia-500/10 text-fuchsia-400 border-fuchsia-500/20'},
    'Painting': {'icon': '🎨', 'color': 'bg-indigo-500/10 text-indigo-400 border-indigo-500/20'},
    'Fashion': {'icon': '👗', 'color': 'bg-pink-500/10 text-pink-400 border-pink-500/20'},
    'Beauty': {'icon': '💄', 'color': 'bg-purple-500/10 text-purple-400 border-purple-500/20'},
    'Medicenes': {'icon': '💊', 'color': 'bg-lime-500/10 text-lime-400 border-lime-500/20'},
    'Sports': {'icon': '⚽', 'color': 'bg-green-500/10 text-green-400 border-green-500/20'},
    'Science': {'icon': '🔬', 'color': 'bg-purple-500/10 text-purple-400 border-purple-500/20'},
    'History': {'icon': '🏺', 'color': 'bg-stone-500/10 text-stone-400 border-stone-500/20'},
    'Politics': {'icon': '🏛️', 'color': 'bg-red-500/10 text-red-400 border-red-500/20'},
    'Entertainment': {'icon': '🎬', 'color': 'bg-yellow-500/10 text-yellow-400 border-yellow-500/20'},
    'Health': {'icon': '🏥', 'color': 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'},
    'Business': {'icon': '📊', 'color': 'bg-indigo-500/10 text-indigo-400 border-indigo-500/20'},
    'Gadgets': {'icon': '📱', 'color': 'bg-slate-500/10 text-slate-400 border-slate-500/20'},
    'Vehicles': {'icon': '🚗', 'color': 'bg-amber-500/10 text-amber-400 border-amber-500/20'},
    'Education': {'icon': '📚', 'color': 'bg-orange-500/10 text-orange-400 border-orange-500/20'},
    'Travel': {'icon': '✈️', 'color': 'bg-cyan-500/10 text-cyan-400 border-cyan-500/20'},
    'Food': {'icon': '🍕', 'color': 'bg-pink-500/10 text-pink-400 border-pink-500/20'},
    'Investment': {'icon': '💰', 'color': 'bg-amber-500/10 text-amber-400 border-amber-500/20'},
    'Astrology': {'icon': '⭐', 'color': 'bg-violet-500/10 text-violet-400 border-violet-500/20'},
    'Spirituality': {'icon': '🕊️', 'color': 'bg-teal-500/10 text-teal-400 border-teal-500/20'},
    'Others': {'icon': '🧩', 'color': 'bg-zinc-500/10 text-zinc-400 border-zinc-500/20'},
}


def get_frontend_categories():
    categories = []
    for category_name, _ in CATEGORY_CHOICES:
        category_style = FRONTEND_CATEGORY_STYLES.get(
            category_name,
            {'icon': '🗂️', 'color': 'bg-slate-500/10 text-slate-400 border-slate-500/20'},
        )
        categories.append({'name': category_name, **category_style})
    return categories


def _remove_repost_copy_for_user(user, original_post):
    """Delete one repost copy for a user that mirrors the original post."""
    repost_copy = (
        Post.objects.filter(
            user=user,
            title=original_post.title,
            content=original_post.content,
            category=original_post.category,
            hashtags=original_post.hashtags,
        )
        .exclude(id=original_post.id)
        .order_by('-created_at')
        .first()
    )
    if repost_copy:
        repost_copy.delete()
        return True
    return False


@login_required
@require_POST
def post_action(request, post_id):
    action = request.POST.get('action')
    if action not in ['like', 'save', 'repost']:
        return JsonResponse({'success': False, 'error': 'Invalid action.'}, status=400)

    post = get_object_or_404(Post, id=post_id)
    if action in ['like', 'save']:
        existing_action = PostAction.objects.filter(user=request.user, post=post, action=action).first()
        if existing_action:
            existing_action.delete()
            status = 'removed'
        else:
            try:
                PostAction.objects.create(user=request.user, post=post, action=action)
                status = 'added'
                if action == 'save':
                    notify_post_author(post, 'author_save', request.user)
            except IntegrityError:
                # If two add requests race, keep it liked/saved instead of crashing.
                status = 'added'

        # Get updated counts
        like_count = PostAction.objects.filter(post=post, action='like').count()
        save_count = PostAction.objects.filter(post=post, action='save').count()
        repost_count = PostAction.objects.filter(post=post, action='repost').count()

        return JsonResponse({
            'success': True,
            'action': action,
            'status': status,
            'counts': {
                'like': like_count,
                'save': save_count,
                'repost': repost_count
            }
        })

    if action == 'repost':
        existing_action = PostAction.objects.filter(user=request.user, post=post, action=action).first()
        if existing_action:
            existing_action.delete()
            _remove_repost_copy_for_user(request.user, post)

            like_count = PostAction.objects.filter(post=post, action='like').count()
            save_count = PostAction.objects.filter(post=post, action='save').count()
            repost_count = PostAction.objects.filter(post=post, action='repost').count()
            return JsonResponse({
                'success': True,
                'action': action,
                'status': 'removed',
                'counts': {
                    'like': like_count,
                    'save': save_count,
                    'repost': repost_count
                }
            })

        try:
            PostAction.objects.create(user=request.user, post=post, action=action)
            notify_post_author(post, 'author_repost', request.user)
        except IntegrityError:
            # Another request already created the repost action; return current counters.
            like_count = PostAction.objects.filter(post=post, action='like').count()
            save_count = PostAction.objects.filter(post=post, action='save').count()
            repost_count = PostAction.objects.filter(post=post, action='repost').count()
            return JsonResponse({
                'success': True,
                'action': action,
                'status': 'added',
                'counts': {
                    'like': like_count,
                    'save': save_count,
                    'repost': repost_count
                }
            })
        new_post = Post.objects.create(
            id=str(uuid.uuid4()),
            user=request.user,
            title=post.title,
            content=post.content,
            category=post.category,
            hashtags=post.hashtags,
        )

        like_count = PostAction.objects.filter(post=post, action='like').count()
        save_count = PostAction.objects.filter(post=post, action='save').count()
        repost_count = PostAction.objects.filter(post=post, action='repost').count()

        return JsonResponse({
            'success': True,
            'action': action,
            'status': 'reposted',
            'repost_id': new_post.id,
            'counts': {
                'like': like_count,
                'save': save_count,
                'repost': repost_count
            }
        })


@require_GET
def post_likes(request, post_id):
    post = get_object_or_404(Post, id=post_id)
    liked_actions = (
        PostAction.objects.filter(post=post, action='like')
        .select_related('user', 'user__profile')
        .order_by('-updated_at', '-created_at')
    )

    liked_users = [
        {
            'username': item.user.username,
            'avatar_url': _safe_avatar_url(item.user),
            'profile_url': f'/user/{item.user.username}/',
        }
        for item in liked_actions
    ]

    return JsonResponse({
        'success': True,
        'post_id': post.id,
        'count': len(liked_users),
        'liked_users': liked_users,
    })


def _annotated_feed_posts_queryset():
    base_posts = Post.objects.filter(id__isnull=False).exclude(id='')
    return base_posts.annotate(
        like_count=Count('actions', filter=Q(actions__action='like'), distinct=True),
        save_count=Count('actions', filter=Q(actions__action='save'), distinct=True),
        repost_count=Count('actions', filter=Q(actions__action='repost'), distinct=True),
        yes_count=Count('comments', filter=Q(comments__vote_type='yes'), distinct=True),
        no_count=Count('comments', filter=Q(comments__vote_type='no'), distinct=True),
        comment_count=Count('comments', distinct=True),
        conversation_count=Count('debates', distinct=True),
        author_posts_count=Count('user__posts', distinct=True),
    ).select_related('user', 'user__profile')


def _enrich_posts_for_feed(posts, user):
    for post in posts:
        yes_count = getattr(post, 'yes_count', 0) or 0
        no_count = getattr(post, 'no_count', 0) or 0
        post.author_avatar = _safe_avatar_url(post.user)
        total_votes = yes_count + no_count
        if total_votes > 0:
            post.yes_percentage = (yes_count * 100.0) / total_votes
            post.no_percentage = 100.0 - post.yes_percentage
        else:
            post.yes_percentage = 0.0
            post.no_percentage = 0.0

    if not getattr(user, 'is_authenticated', False):
        return

    post_ids = [post.id for post in posts]
    liked_post_ids = set()
    saved_post_ids = set()
    reposted_post_ids = set()
    if post_ids:
        post_actions = PostAction.objects.filter(
            user=user,
            post__in=post_ids,
            action__in=['like', 'save', 'repost']
        ).values('post_id', 'action')
        for item in post_actions:
            if item['action'] == 'like':
                liked_post_ids.add(item['post_id'])
            elif item['action'] == 'save':
                saved_post_ids.add(item['post_id'])
            elif item['action'] == 'repost':
                reposted_post_ids.add(item['post_id'])

    for post in posts:
        post.is_liked = post.id in liked_post_ids
        post.is_saved = post.id in saved_post_ids
        post.is_reposted = post.id in reposted_post_ids


def _build_suggested_posts_for_user(user, annotated_posts):
    """
    Priority order:
    1. Posts from followed users in the user's interested categories
    2. Posts from non-followed users in the user's interested categories
    3. Highest engagement posts where people the user has interacted with commented/liked
    4. Remaining posts by latest timestamp
    """
    # Fetch user's interested categories and following set
    try:
        interested_cats = list(user.profile.interested_categories or [])
    except Exception:
        interested_cats = []

    following_user_ids = set(user.following_links.values_list('following_id', flat=True))
    watched_post_ids = set(PostView.objects.filter(user=user).values_list('post_id', flat=True))

    selected_ids = set()

    # ── Tier 1: Following + interested category ─────────────────────────────
    tier1 = []
    if interested_cats and following_user_ids:
        tier1 = list(
            annotated_posts
            .filter(user_id__in=following_user_ids, category__in=interested_cats)
            .exclude(id__in=watched_post_ids)
            .order_by('-created_at')
        )
    elif following_user_ids:
        # No categories set — fall back to all followed posts
        tier1 = list(
            annotated_posts
            .filter(user_id__in=following_user_ids)
            .exclude(id__in=watched_post_ids)
            .order_by('-created_at')
        )
    selected_ids.update(p.id for p in tier1)

    # ── Tier 2: Non-following + interested category ──────────────────────────
    tier2 = []
    if interested_cats:
        tier2 = list(
            annotated_posts
            .filter(category__in=interested_cats)
            .exclude(user_id__in=following_user_ids)
            .exclude(id__in=selected_ids)
            .order_by('-like_count', '-comment_count', '-created_at')
        )
        selected_ids.update(p.id for p in tier2)

    # ── Tier 3: Engagement-based — authors the user has interacted with ──────
    interaction_scores = {}
    liked_authors = (
        PostAction.objects.filter(user=user, action='like')
        .exclude(post__user=user)
        .values('post__user_id')
        .annotate(total=Count('id'))
    )
    commented_authors = (
        Comment.objects.filter(user=user)
        .exclude(post__user=user)
        .values('post__user_id')
        .annotate(total=Count('id'))
    )
    for row in liked_authors:
        uid = row['post__user_id']
        interaction_scores[uid] = interaction_scores.get(uid, 0) + row['total']
    for row in commented_authors:
        uid = row['post__user_id']
        interaction_scores[uid] = interaction_scores.get(uid, 0) + row['total']

    tier3 = []
    if interaction_scores:
        top_author_ids = sorted(interaction_scores, key=interaction_scores.get, reverse=True)[:10]
        tier3 = list(
            annotated_posts
            .filter(user_id__in=top_author_ids)
            .exclude(id__in=selected_ids)
            .order_by('-like_count', '-comment_count', '-created_at')
        )
        selected_ids.update(p.id for p in tier3)

    # ── Tier 4: Everything else — latest timestamp with light shuffle ────────
    remaining = list(annotated_posts.exclude(id__in=selected_ids).order_by('-created_at'))
    recent_pool_size = 60
    recent_pool = remaining[:recent_pool_size]
    random.shuffle(recent_pool)
    tier4 = recent_pool + remaining[recent_pool_size:]

    return tier1 + tier2 + tier3 + tier4


def index(request):
    """Home page with trending posts and categories"""
    annotated_posts = _annotated_feed_posts_queryset()
    trending_posts = annotated_posts.order_by(
        '-like_count',
        '-comment_count',
        '-conversation_count',
        '-author_posts_count',
        '-created_at',
    )

    paginator = Paginator(trending_posts, 10)
    page_obj = paginator.get_page(request.GET.get('page'))
    posts = list(page_obj.object_list)
    _enrich_posts_for_feed(posts, request.user)

    context = {
        'posts': posts,
        'page_obj': page_obj,
        'active_tab': 'trending',
        'categories': get_frontend_categories(),
        'is_suggested_page': False,
    }
    return render(request, 'frontend/index.html', context)


@login_required
def suggested(request):
    """Suggested feed page based on follow graph and engagement preferences."""
    annotated_posts = _annotated_feed_posts_queryset()
    search_query = request.GET.get('q', '').strip()
    if search_query:
        annotated_posts = annotated_posts.filter(
            Q(title__icontains=search_query)
            | Q(content__icontains=search_query)
            | Q(category__icontains=search_query)
            | Q(user__username__icontains=search_query)
            | Q(hashtags__icontains=search_query)
        ).distinct()

    ordered_posts = _build_suggested_posts_for_user(request.user, annotated_posts)

    paginator = Paginator(ordered_posts, 25)
    page_obj = paginator.get_page(request.GET.get('page'))
    posts = list(page_obj.object_list)
    _enrich_posts_for_feed(posts, request.user)

    context = {
        'posts': posts,
        'page_obj': page_obj,
        'active_tab': 'suggested',
        'is_suggested_page': True,
        'search_query': search_query,
    }
    return render(request, 'frontend/suggested.html', context)

def category(request, category_name):
    """Category page showing posts in a specific category"""
    posts = Post.objects.filter(category=category_name).order_by('-created_at')

    context = {
        'category_name': category_name,
        'posts': posts,
    }
    return render(request, 'frontend/category.html', context)

def discussion(request, post_id):
    """Discussion page for a specific post"""
    post = _annotated_feed_posts_queryset().filter(id=post_id).first()
    if not post:
        messages.error(request, 'This discussion is no longer available.')
        return redirect('index')

    # Keep detail-page counters and action state in sync with home/suggested feeds.
    _enrich_posts_for_feed([post], request.user)

    is_post_creator = request.user == post.user
    if request.user.is_authenticated:
        PostView.objects.get_or_create(user=request.user, post=post)

    comments = Comment.objects.filter(post=post).select_related('user', 'user__profile').annotate(
        reaction_score=ExpressionWrapper(F('likes') - F('dislikes'), output_field=IntegerField())
    )

    yes_vote_count = comments.filter(vote_type='yes').count()
    no_vote_count = comments.filter(vote_type='no').count()

    visible_comments = comments.exclude(content='')
    yes_comments = visible_comments.filter(vote_type='yes').order_by('-reaction_score', '-likes', 'created_at')
    no_comments = visible_comments.filter(vote_type='no').order_by('-reaction_score', '-likes', 'created_at')

    # Only award top badges when a comment has a positive net reaction.
    top_yes_comment = yes_comments.filter(reaction_score__gt=0).first()
    top_no_comment = no_comments.filter(reaction_score__gt=0).first()

    total_votes = yes_vote_count + no_vote_count
    yes_percentage = (yes_vote_count / total_votes * 100) if total_votes > 0 else 0
    no_percentage = (no_vote_count / total_votes * 100) if total_votes > 0 else 0

    user_has_voted = False
    user_vote_type = None
    user_has_commented = False
    debate_lookup = {}
    blocked_comment_ids = set()
    if request.user.is_authenticated:
        user_comment = comments.filter(user=request.user).first()
        if user_comment:
            user_has_voted = True
            user_vote_type = user_comment.vote_type
            user_has_commented = bool((user_comment.content or '').strip())

        visible_comment_owners = list(visible_comments.values_list('user_id', flat=True).distinct())
        accepted_debates = list(
            Debate.objects.filter(
                post=post,
                target_id__in=visible_comment_owners,
                status='accepted',
            ).order_by('target_id', '-updated_at')
        )

        latest_accepted_by_target = {}
        for debate in accepted_debates:
            if debate.target_id not in latest_accepted_by_target:
                latest_accepted_by_target[debate.target_id] = debate

        if latest_accepted_by_target:
            debate_ids = [debate.id for debate in latest_accepted_by_target.values()]
            active_side_counts = {
                (item['debate_id'], item['side']): item['total']
                for item in DebateParticipant.objects.filter(
                    debate_id__in=debate_ids,
                    is_active=True,
                ).values('debate_id', 'side').annotate(total=Count('id'))
            }
            user_participation = {
                participant.debate_id: participant
                for participant in DebateParticipant.objects.filter(
                    debate_id__in=debate_ids,
                    user=request.user,
                )
            }

            for target_id, debate in latest_accepted_by_target.items():
                mode = 'join'
                label = 'Join Debate'
                desired_side = user_vote_type if user_vote_type in ('yes', 'no') else None
                if desired_side is None and request.user.id == post.user_id:
                    desired_side = _opposite_side(debate.comment.vote_type)

                participant = user_participation.get(debate.id)
                if participant:
                    mode = 'view'
                    label = 'View Debate'
                else:
                    yes_active = active_side_counts.get((debate.id, 'yes'), 0)
                    no_active = active_side_counts.get((debate.id, 'no'), 0)
                    yes_limit = debate.yes_supporters
                    no_limit = debate.no_supporters
                    conversation_full = (
                        yes_limit > 0 and no_limit > 0
                        and yes_active >= yes_limit and no_active >= no_limit
                    )

                    if conversation_full:
                        mode = 'view'
                        label = 'View Debate'
                        debate_lookup[target_id] = {
                            'id': debate.id,
                            'mode': mode,
                            'label': label,
                            'chat_url': f'/debates/{debate.id}/chat/',
                        }
                        continue

                    chosen_side = _pick_debate_side_for_user(
                        debate,
                        desired_side,
                        active_counts={'yes': yes_active, 'no': no_active},
                        fallback_side=_opposite_side(debate.comment.vote_type),
                    )
                    side_limit = debate.yes_supporters if chosen_side == 'yes' else debate.no_supporters
                    active_side_count = yes_active if chosen_side == 'yes' else no_active
                    if side_limit and active_side_count >= side_limit:
                        mode = 'view'
                        label = 'View Debate'

                debate_lookup[target_id] = {
                    'id': debate.id,
                    'mode': mode,
                    'label': label,
                    'chat_url': f'/debates/{debate.id}/chat/',
                }

        completed_lookup = {}
        completed_debates = Debate.objects.filter(
            post=post,
            target_id__in=visible_comment_owners,
            status='completed',
        ).order_by('target_id', '-updated_at')
        for debate in completed_debates:
            if debate.target_id not in completed_lookup:
                completed_lookup[debate.target_id] = debate

    for comment in yes_comments:
        debate_state = debate_lookup.get(comment.user_id)
        completed_state = completed_lookup.get(comment.user_id) if request.user.is_authenticated else None
        comment.debate_action_mode = debate_state['mode'] if debate_state else 'start'
        comment.debate_action_label = debate_state['label'] if debate_state else 'Start Debate'
        comment.debate_chat_url = debate_state['chat_url'] if debate_state else ''
        comment.show_debate_action = False
        if request.user.is_authenticated and request.user != comment.user:
            comment.show_debate_action = bool(debate_state) or is_post_creator or (user_has_voted and user_vote_type != comment.vote_type)
        comment.show_debate_view_link = False
        comment.debate_view_url = ''
        if completed_state and not debate_state:
            comment.show_debate_view_link = True
            comment.debate_view_url = f'/debates/{completed_state.id}/chat/'

    if request.user.is_authenticated:
        visible_comment_ids = [comment.id for comment in [*yes_comments, *no_comments]]
        if visible_comment_ids:
            blocked_comment_ids = set(
                DebateParticipant.objects.filter(
                    user=request.user,
                    is_banned=True,
                    debate__comment_id__in=visible_comment_ids,
                ).values_list('debate__comment_id', flat=True)
            )

    for comment in no_comments:
        debate_state = debate_lookup.get(comment.user_id)
        completed_state = completed_lookup.get(comment.user_id) if request.user.is_authenticated else None
        comment.debate_action_mode = debate_state['mode'] if debate_state else 'start'
        comment.debate_action_label = debate_state['label'] if debate_state else 'Start Debate'
        comment.debate_chat_url = debate_state['chat_url'] if debate_state else ''
        comment.show_debate_action = False
        if request.user.is_authenticated and request.user != comment.user:
            comment.show_debate_action = bool(debate_state) or is_post_creator or (user_has_voted and user_vote_type != comment.vote_type)
        comment.show_debate_view_link = False
        comment.debate_view_url = ''
        if completed_state and not debate_state:
            comment.show_debate_view_link = True
            comment.debate_view_url = f'/debates/{completed_state.id}/chat/'

    for comment in [*yes_comments, *no_comments]:
        comment.debate_start_blocked = bool(request.user.is_authenticated and comment.id in blocked_comment_ids)
        if comment.debate_start_blocked and comment.debate_action_mode == 'start':
            comment.debate_action_mode = 'blocked'
            comment.debate_action_label = 'Debate Blocked'
            comment.show_debate_action = True

    now = timezone.now()
    online_cutoff = now - timedelta(minutes=5)
    for comment in [*yes_comments, *no_comments]:
        last_seen = _get_last_seen(comment.user)
        comment.is_online = bool(last_seen and last_seen >= online_cutoff)
        comment.presence_label = _presence_label(last_seen, now=now)

    post_has_comments = comments.exists()
    can_manage_post_today = is_post_creator and _can_manage_created_today(request.user, post.created_at)
    show_post_submitted = is_post_creator and request.GET.get('created') == '1'

    is_following_post = False
    if request.user.is_authenticated:
        is_following_post = PostFollow.objects.filter(user=request.user, post=post).exists()

    context = {
        'post': post,
        'post_display_content': _normalize_post_content(post.content),
        'yes_comments': yes_comments,
        'no_comments': no_comments,
        'yes_vote_count': yes_vote_count,
        'no_vote_count': no_vote_count,
        'total_votes': total_votes,
        'yes_percentage': yes_percentage,
        'no_percentage': no_percentage,
        'user_has_voted': user_has_voted,
        'user_vote_type': user_vote_type,
        'user_has_commented': user_has_commented,
        'is_post_creator': is_post_creator,
        'show_post_submitted': show_post_submitted,
        'can_edit_post': can_manage_post_today and not post_has_comments,
        'can_delete_post': can_manage_post_today,
        'post_has_comments': post_has_comments,
        'post_change_locked_message': 'This account can only edit or delete posts created today.' if is_post_creator and not can_manage_post_today else '',
        'top_yes_comment_id': top_yes_comment.id if top_yes_comment else '',
        'top_no_comment_id': top_no_comment.id if top_no_comment else '',
        'is_following_post': is_following_post,
    }
    return render(request, 'frontend/discussion.html', context)

@login_required
def profile(request):
    """User profile page"""
    user_posts = Post.objects.filter(user=request.user).exclude(id='').order_by('-created_at')
    user_reposts = PostAction.objects.filter(
        user=request.user, action='repost'
    ).select_related('post', 'post__user').order_by('-created_at')
    user_debate_participations = DebateParticipant.objects.filter(
        user=request.user
    ).select_related(
        'debate',
        'debate__post',
        'debate__initiator',
        'debate__target',
    ).order_by('-debate__created_at')
    user_saved = PostAction.objects.filter(
        user=request.user, action='save'
    ).select_related('post', 'post__user', 'post__user__profile').order_by('-created_at')

    user_followed_posts = PostFollow.objects.filter(
        user=request.user
    ).select_related('post', 'post__user', 'post__user__profile').order_by('-created_at')

    for post in user_posts:
        post.can_delete_today = _can_manage_created_today(request.user, post.created_at)

    for action in user_reposts:
        action.can_remove_today = _can_manage_created_today(request.user, action.created_at)

    profile_obj = Profile.objects.filter(user=request.user).first()
    avatar_url = profile_obj.get_picture_url if profile_obj else ''
    profile_picture_url = profile_obj.profile_picture.url if profile_obj and profile_obj.profile_picture else ''
    profile_last_seen = profile_obj.last_seen if profile_obj else None
    now = timezone.now()
    is_online = bool(profile_last_seen and profile_last_seen >= now - timedelta(minutes=5))
    presence_label = 'Active now' if is_online else _presence_label(profile_last_seen, now=now)

    followers_qs = request.user.follower_links.select_related('follower__profile').order_by('-created_at')
    following_qs = request.user.following_links.select_related('following__profile').order_by('-created_at')

    def _card(u):
        p = getattr(u, 'profile', None)
        return {'username': u.username, 'avatar_url': p.get_picture_url if p else ''}

    context = {
        'user_posts': user_posts,
        'user_reposts': user_reposts,
        'user_debate_participations': user_debate_participations,
        'user_saved': user_saved,
        'user_followed_posts': user_followed_posts,
        'avatar_url': avatar_url,
        'profile_picture_url': profile_picture_url,
        'is_online': is_online,
        'presence_label': presence_label,
        'followers_count': request.user.follower_links.count(),
        'following_count': request.user.following_links.count(),
        'followers_list': [_card(f.follower) for f in followers_qs],
        'following_list': [_card(f.following) for f in following_qs],
    }
    return render(request, 'frontend/profile.html', context)

def upload_profile_picture(request):
    """Handle profile picture upload"""
    if not request.user.is_authenticated:
        return JsonResponse({'success': False, 'error': 'Not authenticated'}, status=401)
    
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)
    
    if 'profile_picture' not in request.FILES:
        return JsonResponse({'success': False, 'error': 'No file provided'}, status=400)
    
    try:
        profile = Profile.objects.get(user=request.user)
    except Profile.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Profile not found'}, status=404)
    
    file = request.FILES['profile_picture']
    
    # Validate file size (max 5MB)
    if file.size > 5 * 1024 * 1024:
        return JsonResponse({'success': False, 'error': 'File size exceeds 5MB'}, status=400)
    
    # Validate file type
    allowed_types = ['image/jpeg', 'image/png', 'image/gif', 'image/webp']
    if file.content_type not in allowed_types:
        return JsonResponse({'success': False, 'error': 'Invalid file type. Only JPEG, PNG, GIF, and WebP allowed'}, status=400)
    
    # Delete old picture if exists
    if profile.profile_picture:
        profile.profile_picture.delete()
    
    # Save new picture
    profile.profile_picture = file
    profile.save()
    
    return JsonResponse({
        'success': True,
        'message': 'Profile picture updated successfully',
        'profile_picture_url': profile.profile_picture.url if profile.profile_picture else ''
    })

def user_profile(request, username):
    """Public user profile page"""
    try:
        profile_user = User.objects.get(username=username)
    except User.DoesNotExist:
        from django.http import Http404
        raise Http404("User not found")

    user_posts = Post.objects.filter(user=profile_user).exclude(id='').order_by('-created_at')

    profile_obj = Profile.objects.filter(user=profile_user).first()
    avatar_url = profile_obj.get_picture_url if profile_obj else ''
    profile_last_seen = profile_obj.last_seen if profile_obj else None
    now = timezone.now()
    is_online = bool(profile_last_seen and profile_last_seen >= now - timedelta(minutes=5))
    presence_label = 'Active now' if is_online else _presence_label(profile_last_seen, now=now)

    # Check if current user is following this user
    is_following = False
    if request.user.is_authenticated:
        is_following = Follow.objects.filter(follower=request.user, following=profile_user).exists()

    context = {
        'profile_user': profile_user,
        'user_posts': user_posts,
        'avatar_url': avatar_url,
        'is_online': is_online,
        'presence_label': presence_label,
        'followers_count': profile_user.follower_links.count(),
        'following_count': profile_user.following_links.count(),
        'is_following': is_following,
        'is_own_profile': request.user == profile_user,
    }
    return render(request, 'frontend/user_profile.html', context)


@login_required
@require_POST
def report_user_profile(request, username):
    """Report a user profile to configured moderators."""
    reported_user = get_object_or_404(User, username=username)

    if reported_user.id == request.user.id:
        return JsonResponse({'success': False, 'error': 'You cannot report your own profile.'}, status=400)

    details = (request.POST.get('details') or '').strip()
    if len(details) < 10:
        return JsonResponse({
            'success': False,
            'error': 'Please explain the reason clearly (at least 10 characters).'
        }, status=400)

    report, created = ProfileReport.objects.get_or_create(
        reporter=request.user,
        reported_user=reported_user,
        defaults={
            'reason': 'profile_concern',
            'details': details,
        },
    )

    if not created:
        if details and details != (report.details or '').strip():
            report.details = details
            report.save(update_fields=['details', 'updated_at'])
        return JsonResponse({'success': True, 'message': 'Profile already reported. Your latest reason has been shared for moderator review.'})

    context_post = (
        Post.objects.filter(user=reported_user).order_by('-created_at').first()
        or Post.objects.filter(user=request.user).order_by('-created_at').first()
        or Post.objects.order_by('-created_at').first()
    )

    moderators = _moderator_users().exclude(id=request.user.id)
    if context_post:
        for moderator in moderators:
            Notification.objects.create(
                user=moderator,
                post=context_post,
                notification_type='moderation_alert',
                message=(
                    f"Profile report: {request.user.username} reported {reported_user.username}. "
                    f"Details: {details[:180]}"
                ),
            )

    return JsonResponse({'success': True, 'message': 'Profile reported. Moderators have been notified.'})

def search(request):
    """Search page"""
    query = request.GET.get('q', '')
    results = []

    if query:
        results = Post.objects.filter(
            Q(title__icontains=query) | Q(content__icontains=query)
        ).order_by('-created_at')

    context = {
        'query': query,
        'results': results,
    }
    return render(request, 'frontend/search.html', context)


def quick_search(request):
    """Lightweight JSON search used by the navbar search overlay."""
    if _is_public_rate_limited(request, scope='quick_search', limit=60, window_seconds=60):
        return JsonResponse({'success': False, 'error': 'Too many requests. Please try again shortly.'}, status=429)

    query = request.GET.get('q', '').strip()
    if not query:
        return JsonResponse({'success': True, 'results': []})

    posts = Post.objects.filter(
        Q(title__icontains=query) | Q(content__icontains=query)
    ).select_related('user').order_by('-created_at')[:8]

    results = [
        {
            'id': post.id,
            'title': post.title,
            'category': post.category,
            'author': post.user.username,
            'created_at': post.created_at.strftime('%b %d, %Y'),
            'url': f'/discussion/{post.id}/',
        }
        for post in posts
    ]

    return JsonResponse({'success': True, 'results': results})

def search_users(request):
    """Lightweight JSON user search used by the navbar search overlay."""
    if _is_public_rate_limited(request, scope='search_users', limit=60, window_seconds=60):
        return JsonResponse({'success': False, 'error': 'Too many requests. Please try again shortly.'}, status=429)

    query = request.GET.get('q', '').strip()
    if not query:
        return JsonResponse({'success': True, 'users': []})

    profiles = Profile.objects.filter(
        username__icontains=query
    ).select_related('user').order_by('username')[:8]

    users = [
        {
            'username': p.username,
            'posts_count': p.posts_count,
            'followers_count': p.followers_count,
            'url': f'/user/{p.username}/',
            'avatar_url': p.get_picture_url,
        }
        for p in profiles
    ]

    return JsonResponse({'success': True, 'users': users})


def hashtag_search(request):
    """Search posts by hashtag"""
    raw_tag = request.GET.get('tag', '').strip()
    parsed = Post.parse_hashtags(raw_tag, max_tags=1)
    tag = parsed[0] if parsed else ''
    results = Post.objects.none()

    if tag:
        # Candidate set first, then exact tag match via parser for legacy/new formats.
        candidates = Post.objects.filter(hashtags__icontains=tag).order_by('-created_at')
        matching_ids = [post.id for post in candidates if tag in post.get_hashtags_list()]
        results = Post.objects.filter(id__in=matching_ids).order_by('-created_at')

    context = {
        'tag': tag,
        'results': results,
        'is_hashtag_search': True,
    }
    return render(request, 'frontend/search.html', context)


def hashtag_suggestions(request):
    """Return hashtag suggestions while user types."""
    if _is_public_rate_limited(request, scope='hashtag_suggestions', limit=90, window_seconds=60):
        return JsonResponse({'success': False, 'error': 'Too many requests. Please try again shortly.'}, status=429)

    raw_query = request.GET.get('q', '').strip()
    parsed = Post.parse_hashtags(raw_query, max_tags=1)
    query = parsed[0] if parsed else raw_query.lower().lstrip('#').strip()

    if not query:
        return JsonResponse({'success': True, 'hashtags': []})

    counter = {}
    hashtag_rows = (
        Post.objects.exclude(hashtags='')
        .order_by('-created_at')
        .values_list('hashtags', flat=True)[:1000]
    )

    for row in hashtag_rows:
        for tag in Post.parse_hashtags(row, max_tags=20):
            if tag.startswith(query):
                counter[tag] = counter.get(tag, 0) + 1

    top = sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:8]
    suggestions = [
        {
            'tag': tag,
            'count': count,
            'url': f'/search/hashtags/?tag={tag}',
        }
        for tag, count in top
    ]

    return JsonResponse({'success': True, 'hashtags': suggestions})

@login_required
def ask_question(request):
    """Ask question page"""
    categories = get_frontend_categories()
    return render(request, 'frontend/ask_question.html', {'categories': categories})

@login_required
def notifications(request):
    """Notifications page showing debates"""
    # Show incoming pending requests first so users focus on actionable notifications.
    incoming_pending = Debate.objects.filter(
        target=request.user,
        status='pending'
    ).order_by('-created_at')[:10]

    outgoing_and_resolved = Debate.objects.filter(
        Q(initiator=request.user) | Q(target=request.user)
    ).exclude(status='pending').order_by('-created_at')

    debates = list(incoming_pending) + list(outgoing_and_resolved)

    for debate in debates:
        if debate.status in ('accepted', 'completed'):
            _ensure_debate_core_participants(debate)
            participation = DebateParticipant.objects.filter(debate=debate, user=request.user).first()
            is_completed = debate.status == 'completed'
            is_view_only = bool(participation and (participation.is_banned or is_completed))
            can_post = bool(
                debate.status == 'accepted'
                and participation
                and participation.is_active
                and not participation.is_banned
            )
            debate.user_is_view_only = is_view_only
            debate.user_can_post = can_post
            debate.user_is_active_participant = can_post
            debate.user_can_rejoin = bool(
                debate.status == 'accepted'
                and participation
                and not participation.is_active
                and not participation.is_banned
            )
            debate.user_can_leave = bool(debate.status == 'accepted' and can_post)
            debate.user_can_end = bool(
                can_post and debate.end_controller_id == request.user.id
            )
            debate.user_can_view_chat = bool(participation)
            debate.user_can_moderate = request.user.id == debate.target_id
            if debate.user_can_moderate and debate.status == 'accepted':
                debate.moderatable_participants = list(
                    DebateParticipant.objects.filter(
                        debate=debate,
                        is_active=True,
                        is_banned=False,
                    ).exclude(user_id=request.user.id).select_related('user').order_by('joined_at')
                )
            else:
                debate.moderatable_participants = []
        else:
            debate.user_is_view_only = False
            debate.user_can_post = False
            debate.user_is_active_participant = False
            debate.user_can_rejoin = False
            debate.user_can_leave = False
            debate.user_can_end = False
            debate.user_can_view_chat = False
            debate.user_can_moderate = False
            debate.moderatable_participants = []

    moderation_reports = []
    profile_reports = []
    if _is_configured_moderator(request.user):
        moderation_reports = list(
            DebateMessageReport.objects.filter(status='pending')
            .select_related('debate__post', 'message', 'reporter', 'reported_user')
            .order_by('-created_at')[:50]
        )

        report_counts = {
            row['reported_user_id']: row
            for row in DebateMessageReport.objects.values('reported_user_id').annotate(
                total_reports=Count('id'),
                pending_reports=Count('id', filter=Q(status='pending')),
                actioned_reports=Count('id', filter=Q(status='actioned')),
                dismissed_reports=Count('id', filter=Q(status='dismissed')),
            )
        }

        for report in moderation_reports:
            report.message_preview = _decode_chat_content_from_storage(report.message.content)
            counts = report_counts.get(report.reported_user_id, {})
            report.total_reports = counts.get('total_reports', 0)
            report.pending_reports = counts.get('pending_reports', 0)
            report.actioned_reports = counts.get('actioned_reports', 0)
            report.dismissed_reports = counts.get('dismissed_reports', 0)

        profile_reports = list(
            ProfileReport.objects.filter(status='pending')
            .select_related('reporter', 'reported_user')
            .order_by('-created_at')[:50]
        )
        profile_report_counts = {
            row['reported_user_id']: row
            for row in ProfileReport.objects.values('reported_user_id').annotate(
                total_reports=Count('id'),
                pending_reports=Count('id', filter=Q(status='pending')),
                reviewed_reports=Count('id', filter=Q(status='reviewed')),
                dismissed_reports=Count('id', filter=Q(status='dismissed')),
            )
        }
        for report in profile_reports:
            counts = profile_report_counts.get(report.reported_user_id, {})
            report.total_reports = counts.get('total_reports', 0)
            report.pending_reports = counts.get('pending_reports', 0)
            report.reviewed_reports = counts.get('reviewed_reports', 0)
            report.dismissed_reports = counts.get('dismissed_reports', 0)

    context = {
        'debates': debates,
        'post_notifications': Notification.objects.filter(
            user=request.user
        ).select_related('post').order_by('-created_at')[:30],
        'is_configured_moderator': _is_configured_moderator(request.user),
        'moderation_reports': moderation_reports,
        'profile_reports': profile_reports,
    }
    return render(request, 'frontend/notifications.html', context)


@login_required
@require_POST
def unfollow_post(request):
    """Unfollow a post from a notification — stops future comment notifications."""
    post_id = request.POST.get('post_id') or (json.loads(request.body).get('post_id') if request.content_type == 'application/json' else None)
    if not post_id:
        return JsonResponse({'success': False, 'error': 'post_id required'}, status=400)
    deleted, _ = PostFollow.objects.filter(user=request.user, post_id=post_id).delete()
    return JsonResponse({'success': True, 'unfollowed': deleted > 0})


@login_required
@require_POST
def dismiss_notification(request):
    """Mark an author-type notification as read / dismissed."""
    try:
        data = json.loads(request.body)
        notif_id = data.get('notification_id')
    except (json.JSONDecodeError, AttributeError):
        notif_id = request.POST.get('notification_id')
    if not notif_id:
        return JsonResponse({'success': False, 'error': 'notification_id required'}, status=400)
    updated = Notification.objects.filter(id=notif_id, user=request.user).update(is_read=True)
    return JsonResponse({'success': True, 'dismissed': updated > 0})


@login_required
def chat_list(request):
    """Return list of active chats sorted by most recent message."""
    chats = _build_chat_payload_for_user(request.user, only_active=False)
    return JsonResponse({'success': True, 'chats': chats})


def _build_chat_payload_for_user(user, only_active=False):
    participations = DebateParticipant.objects.filter(
        user=user,
        debate__status='accepted',
        is_banned=False,
    )
    if only_active:
        participations = participations.filter(is_active=True)

    participations = participations.select_related(
        'debate__post',
        'debate__initiator',
        'debate__target',
    )

    chats = []
    for p in participations:
        debate = p.debate
        opponent = debate.target if user.id == debate.initiator_id else debate.initiator
        last_msg = DebateMessage.objects.filter(
            debate=debate,
            is_system=False,
        ).order_by('-created_at').first()

        try:
            opp_avatar = opponent.profile.get_picture_url
        except Exception:
            opp_avatar = ''

        chats.append({
            'id': str(debate.id),
            'post_id': str(debate.post_id),
            'title': debate.post.title,
            'opponent': opponent.username,
            'opponent_avatar': opp_avatar,
            'is_active': p.is_active,
            'last_message': _decode_chat_content_from_storage(last_msg.content)[:100] if last_msg else None,
            'last_message_sender': last_msg.sender.username if last_msg else None,
            'last_message_time': last_msg.created_at.strftime('%b %d, %H:%M') if last_msg else None,
            'last_message_id': last_msg.id if last_msg else 0,
            'updated_at': debate.updated_at.isoformat() if debate.updated_at else '',
        })

    chats.sort(key=lambda x: x['last_message_id'] or 0, reverse=True)
    return chats


@login_required
def chats(request):
    """Dedicated page listing all active conversations."""
    chats_data = _build_chat_payload_for_user(request.user, only_active=True)
    requested_chat = str(request.GET.get('chat', '')).strip()
    chat_ids = {str(item.get('id')) for item in chats_data}
    selected_chat_id = requested_chat if requested_chat in chat_ids else (str(chats_data[0]['id']) if chats_data else '')
    return render(request, 'frontend/chats.html', {
        'chats': chats_data,
        'selected_chat_id': selected_chat_id,
    })


@login_required
def notification_count(request):
    pending_count = Debate.objects.filter(
        target=request.user,
        status='pending'
    ).count()

    # Unread author notifications (comment / debate / repost / save on own posts)
    pending_count += Notification.objects.filter(
        user=request.user,
        notification_type__in=['author_comment', 'author_debate', 'author_repost', 'author_save'],
        is_read=False,
    ).count()

    if _is_configured_moderator(request.user):
        pending_count += Notification.objects.filter(
            user=request.user,
            notification_type='moderation_alert',
            is_read=False,
        ).count()

    accepted_ids = list(
        DebateParticipant.objects.filter(
            user=request.user,
            debate__status='accepted',
            is_active=True,
            is_banned=False,
        ).values_list('debate_id', flat=True)
    )

    return JsonResponse({
        'success': True,
        'pending_notifications_count': min(pending_count, 10),
        'accepted_debate_ids': [str(x) for x in accepted_ids],
    })

def login_view(request):
    """Login page"""
    raw_next = request.POST.get('next') or request.GET.get('next') or ''
    next_url = _safe_next_url(request, raw_next)

    if request.method == 'POST':
        identifier = (request.POST.get('username') or '').strip()
        password = request.POST.get('password')

        if is_login_rate_limited(request, identifier, source='web'):
            messages.error(request, 'Too many login attempts. Please try again in a few minutes.')
            return render(request, 'frontend/login.html', {'next': next_url})

        if identifier == _manual_editor_username() and _manual_editor_password():
            _ensure_manual_editor_user()

        user = authenticate(request, username=identifier, password=password)
        if user is None and '@' in identifier:
            matched_user = User.objects.filter(email__iexact=identifier).first()
            if matched_user is not None:
                user = authenticate(request, username=matched_user.username, password=password)

        if user is not None:
            record_login_attempt(request, user.username, successful=True, source='web')
            login(request, user)
            Profile.objects.update_or_create(
                user=user,
                defaults={
                    'username': user.username,
                    'last_seen': timezone.now(),
                },
            )
            messages.success(request, 'Welcome back!')
            if next_url:
                return redirect(next_url)
            return redirect('index')
        else:
            record_login_attempt(request, identifier, successful=False, source='web')
            messages.error(request, 'Invalid credentials')

    return render(request, 'frontend/login.html', {'next': next_url})

def check_username(request):
    """AJAX endpoint — returns availability of a username."""
    username = request.GET.get('username', '').strip()
    if not username:
        return JsonResponse({'available': False, 'message': 'Enter a username'})
    if len(username) < 3:
        return JsonResponse({'available': False, 'message': 'Too short (min 3 chars)'})
    if len(username) > 150:
        return JsonResponse({'available': False, 'message': 'Too long (max 150 chars)'})
    import re as _re
    if not _re.match(r'^[\w.@+-]+$', username):
        return JsonResponse({'available': False, 'message': 'Only letters, digits and @/./+/-/_ allowed'})
    taken = User.objects.filter(username__iexact=username).exists()
    if taken:
        return JsonResponse({'available': False, 'message': 'Username already taken'})
    return JsonResponse({'available': True, 'message': 'Username available'})


def register_view(request):
    """Registration page"""
    raw_next = request.POST.get('next') or request.GET.get('next') or ''
    next_url = _safe_next_url(request, raw_next)

    if request.method == 'POST':
        username = request.POST.get('username')
        email = request.POST.get('email')
        password = request.POST.get('password')
        confirm_password = request.POST.get('confirm_password')

        if User.objects.filter(username=username).exists():
            messages.error(request, 'Username is already taken. Please choose another one.')
            return render(request, 'frontend/register.html', {'next': next_url})

        if password != confirm_password:
            messages.error(request, 'Passwords do not match')
            return render(request, 'frontend/register.html', {'next': next_url})

        # Password validation
        if len(password) < 8:
            messages.error(request, 'Password must be at least 8 characters')
            return render(request, 'frontend/register.html', {'next': next_url})

        if not any(char.isupper() for char in password):
            messages.error(request, 'Password must contain an uppercase letter')
            return render(request, 'frontend/register.html', {'next': next_url})

        if not any(char.islower() for char in password):
            messages.error(request, 'Password must contain a lowercase letter')
            return render(request, 'frontend/register.html', {'next': next_url})

        if not any(char.isdigit() for char in password):
            messages.error(request, 'Password must contain a number')
            return render(request, 'frontend/register.html', {'next': next_url})

        if not any(char in '!@#$%^&*(),.?":{}|<>' for char in password):
            messages.error(request, 'Password must contain a special character')
            return render(request, 'frontend/register.html', {'next': next_url})

        try:
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password
            )
            login(request, user)
            return redirect('interests_onboarding')
        except Exception as e:
            messages.error(request, f'Registration failed: {str(e)}')

    return render(request, 'frontend/register.html', {'next': next_url})

@login_required
def interests_onboarding(request):
    """Post-signup category interest selection page."""
    all_categories = [c[0] for c in CATEGORY_CHOICES]
    profile = Profile.objects.filter(user=request.user).first()
    already_set = bool(profile and profile.interested_categories)

    if request.method == 'POST':
        selected = request.POST.getlist('categories')
        valid = [c for c in selected if c in all_categories]
        if profile:
            profile.interested_categories = valid
            profile.save(update_fields=['interested_categories'])
        return redirect('suggested')

    # If user visits again after already setting interests, redirect away
    if already_set and request.GET.get('force') != '1':
        return redirect('suggested')

    return render(request, 'frontend/interests_onboarding.html', {
        'all_categories': all_categories,
        'selected_categories': profile.interested_categories if profile else [],
    })


@login_required
@require_POST
def save_interests(request):
    """AJAX endpoint to save interested categories."""
    try:
        data = json.loads(request.body)
        selected = data.get('categories', [])
    except (json.JSONDecodeError, AttributeError):
        selected = request.POST.getlist('categories')

    all_categories = [c[0] for c in CATEGORY_CHOICES]
    valid = [c for c in selected if c in all_categories]

    profile, _ = Profile.objects.get_or_create(user=request.user, defaults={'username': request.user.username})
    profile.interested_categories = valid
    profile.save(update_fields=['interested_categories'])

    return JsonResponse({'success': True, 'saved': valid})


def logout_view(request):
    """Logout view"""
    logout(request)
    messages.success(request, 'Logged out successfully')
    return redirect('index')


@login_required
@require_POST
def mark_offline(request):
    """Handle unload pings without forcing last_seen backwards.

    Presence should stay online while any tab is open. We rely on the normal
    heartbeat timeout window for offline transitions rather than hard-setting an
    old timestamp on unload, which can race with active page requests.
    """
    request.session.pop('dh_last_seen_epoch', None)
    return JsonResponse({'success': True})


@login_required
@require_POST
def create_post(request):
    """Create a new post"""
    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        content = _normalize_post_content(request.POST.get('content', ''))
        category = request.POST.get('category', '').strip()
        hashtags = request.POST.get('hashtags', '').strip()
        accepted_rules = request.POST.get('accepted_rules', '0').strip()

        if not title or not category:
            messages.error(request, 'Title and category are required')
            return redirect('index')

        if accepted_rules != '1':
            messages.error(request, 'Please review and accept the ask question instructions before posting.')
            return redirect('ask_question')

        combined_text = f"{title} {content}".strip()
        if combined_text and check_content_moderation(combined_text):
            messages.error(request, 'Your post contains abusive language and cannot be posted.')
            return redirect('ask_question')

        limit_reached, _, limit = has_reached_daily_post_limit(request.user)
        if limit_reached:
            messages.error(request, f'You can create up to {limit} posts per day.')
            return redirect('index')

        long_hashtags = [
            token.lstrip('#')
            for token in re.findall(r'#?[A-Za-z0-9_]+', hashtags)
            if len(token.lstrip('#')) > Post.HASHTAG_MAX_LENGTH
        ]
        if long_hashtags:
            messages.error(request, f'Each hashtag must be at most {Post.HASHTAG_MAX_LENGTH} characters.')
            return redirect('ask_question')

        # Process hashtags from free input (comma/space separated, with or without '#').
        hashtag_list = Post.parse_hashtags(hashtags, max_tags=5)
        processed_hashtags = ', '.join(hashtag_list)

        try:
            post = Post.objects.create(
                id=str(uuid.uuid4()),
                user=request.user,
                title=title,
                content=content,
                category=category,
                hashtags=processed_hashtags
            )
            
            if not post or not post.id:
                messages.error(request, 'Failed to create post')
                return redirect('index')
            
            return redirect(f'/discussion/{post.id}/?created=1')
        except Exception as e:
            messages.error(request, f'Failed to create post: {str(e)}')
            return redirect('index')

    return redirect('index')


@login_required
@require_POST
def update_post(request, post_id):
    """Update post title only before any comment exists."""
    post = get_object_or_404(Post, id=post_id)

    if post.user != request.user:
        return JsonResponse({'success': False, 'error': 'You can only edit your own post.'}, status=403)

    if not _can_manage_created_today(request.user, post.created_at):
        return JsonResponse({'success': False, 'error': 'This account can only edit posts created today.'}, status=403)

    if Comment.objects.filter(post=post).exists():
        return JsonResponse({'success': False, 'error': 'You cannot edit this post after comments are added.'})

    title = request.POST.get('title', '').strip()

    if not title:
        return JsonResponse({'success': False, 'error': 'Title is required.'})

    if check_content_moderation(title):
        return JsonResponse({'success': False, 'error': 'Your post title contains abusive language and cannot be saved.'}, status=400)

    PostEditHistory.objects.create(
        post=post,
        original_title=post.title,
        original_content=post.content,
    )
    post.title = title
    post.is_edited = True
    post.save(update_fields=['title', 'is_edited', 'updated_at'])

    return JsonResponse({'success': True, 'message': 'Post updated successfully.'})


@login_required
@require_POST
def delete_post(request, post_id):
    """Delete a post and cascade delete comments and debates."""
    post = get_object_or_404(Post, id=post_id)

    if post.user != request.user:
        return JsonResponse({'success': False, 'error': 'You can only delete your own post.'}, status=403)

    if not _can_manage_created_today(request.user, post.created_at):
        return JsonResponse({'success': False, 'error': 'This account can only delete posts created today.'}, status=403)

    post.delete()
    return JsonResponse({'success': True, 'message': 'Post deleted successfully.', 'redirect_url': '/'})

@login_required
@require_POST
def remove_repost(request, post_id):
    """Remove a repost action and the duplicated post created during reposting."""
    original = get_object_or_404(Post, id=post_id)
    action = PostAction.objects.filter(user=request.user, post=original, action='repost').first()
    if not action:
        return JsonResponse({'success': False, 'error': 'Repost not found.'}, status=404)
    if not _can_manage_created_today(request.user, action.created_at):
        return JsonResponse({'success': False, 'error': 'This account can only remove reposts created today.'}, status=403)
    _remove_repost_copy_for_user(request.user, original)
    action.delete()
    return JsonResponse({'success': True})


@login_required
@require_POST
def remove_follower(request):
    """Remove a follower from the current user's followers list."""
    username = request.POST.get('username', '').strip()
    if not username:
        return JsonResponse({'success': False, 'error': 'Username is required.'}, status=400)

    if username == request.user.username:
        return JsonResponse({'success': False, 'error': 'You cannot remove yourself.'}, status=400)

    target_user = User.objects.filter(username=username).first()
    if not target_user:
        return JsonResponse({'success': False, 'error': 'User not found.'}, status=404)

    deleted_count, _ = Follow.objects.filter(follower=target_user, following=request.user).delete()
    if not deleted_count:
        return JsonResponse({'success': False, 'error': 'This user is not following you.'}, status=404)

    return JsonResponse({
        'success': True,
        'removed_username': username,
        'followers_count': request.user.follower_links.count(),
    })


@login_required
@require_POST
def create_comment(request, post_id):
    """Create a comment on a post"""
    post = get_object_or_404(Post, id=post_id)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    def build_vote_payload(message, has_comment):
        yes_vote_count = post.comments.filter(vote_type='yes').count()
        no_vote_count = post.comments.filter(vote_type='no').count()
        total_votes = yes_vote_count + no_vote_count
        yes_percentage = (yes_vote_count / total_votes * 100) if total_votes > 0 else 0
        no_percentage = (no_vote_count / total_votes * 100) if total_votes > 0 else 0
        return {
            'success': True,
            'message': message,
            'yes_vote_count': yes_vote_count,
            'no_vote_count': no_vote_count,
            'yes_percentage': yes_percentage,
            'no_percentage': no_percentage,
            'has_comment': has_comment,
            'reload_required': has_comment,
        }

    def handle_error(message, status=400):
        if is_ajax:
            return JsonResponse({'success': False, 'error': message}, status=status)
        messages.error(request, message)
        return redirect('discussion', post_id=post_id)

    if request.method == 'POST':
        vote_type = request.POST.get('vote_type')
        content = request.POST.get('content', '').strip()

        if not vote_type or vote_type not in ['yes', 'no']:
            return handle_error('Invalid vote type')

        # Enforce a single comment per user per post.
        existing_comment = Comment.objects.filter(post=post, user=request.user).first()
        if existing_comment:
            existing_has_content = bool((existing_comment.content or '').strip())

            if content and check_content_moderation(content):
                return handle_error('Your comment contains abusive language and cannot be posted.')

            if existing_has_content:
                return handle_error('You can comment only once on a post.')

            if not content:
                existing_comment.vote_type = vote_type
                existing_comment.save(update_fields=['vote_type', 'updated_at'])

                if is_ajax:
                    return JsonResponse(build_vote_payload('Vote updated!', has_comment=False))

                messages.success(request, 'Vote updated!')
                return redirect('discussion', post_id=post_id)

            existing_comment.vote_type = vote_type
            existing_comment.content = content
            existing_comment.save(update_fields=['vote_type', 'content', 'updated_at'])

            if is_ajax:
                return JsonResponse(build_vote_payload('Comment submitted!', has_comment=True))

            messages.success(request, 'Comment submitted!')
            return redirect('discussion', post_id=post_id)
        else:
            # First time voting - content is optional
            if content and check_content_moderation(content):
                return handle_error('Your comment contains abusive language and cannot be posted.')

            Comment.objects.create(
                id=str(uuid.uuid4()),
                post=post,
                user=request.user,
                vote_type=vote_type,
                content=content if content else ''
            )

            if is_ajax:
                return JsonResponse(build_vote_payload('Vote submitted!' if not content else 'Comment submitted!', has_comment=bool(content)))

            messages.success(request, 'Vote submitted!' if not content else 'Comment submitted!')
            return redirect('discussion', post_id=post_id)

    return redirect('discussion', post_id=post_id)

@login_required
@require_POST
def like_comment(request):
    """Like or dislike a comment"""
    comment_id = request.POST.get('comment_id')
    action = request.POST.get('action')  # 'like' or 'dislike'

    if action not in ['like', 'dislike']:
        return JsonResponse({'success': False, 'error': 'Invalid reaction'})

    try:
        comment = Comment.objects.get(id=comment_id)

        if comment.user_id == request.user.id:
            return JsonResponse({'success': False, 'error': 'You cannot react to your own comment.'}, status=403)

        try:
            reaction, created = CommentReaction.objects.get_or_create(
                comment=comment,
                user=request.user,
                defaults={'reaction': action}
            )
        except IntegrityError:
            # Handle concurrent taps on mobile that can race against unique constraints.
            reaction = CommentReaction.objects.filter(comment=comment, user=request.user).first()
            if not reaction:
                return JsonResponse({'success': False, 'error': 'Could not apply reaction. Please try again.'}, status=409)
            created = False

        if not created and reaction.reaction != action:
            reaction.reaction = action
            reaction.save(update_fields=['reaction', 'updated_at'])

        likes_count = CommentReaction.objects.filter(comment=comment, reaction='like').count()
        dislikes_count = CommentReaction.objects.filter(comment=comment, reaction='dislike').count()

        comment.likes = likes_count
        comment.dislikes = dislikes_count
        comment.save(update_fields=['likes', 'dislikes', 'updated_at'])

        return JsonResponse({'success': True, 'likes': likes_count, 'dislikes': dislikes_count})
    except Comment.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Comment not found'})

@login_required
@require_POST
def start_debate(request):
    """Start a debate with another user"""
    comment_id = request.POST.get('comment_id')

    try:
        comment = Comment.objects.get(id=comment_id)
        target_user = comment.user

        if request.user == target_user:
            return JsonResponse({'success': False, 'error': 'Cannot debate with yourself'})

        if _is_blocked_by_comment_owner(target_user, request.user):
            return JsonResponse({'success': False, 'error': 'You are not allowed to send debate requests to this commentor.'})

        if DebateParticipant.objects.filter(
            user=request.user,
            is_banned=True,
            debate__comment=comment,
        ).exists():
            return JsonResponse({
                'success': False,
                'error': 'You were removed from this comment debate and cannot start it again.'
            })

        user_comment = Comment.objects.filter(post=comment.post, user=request.user).first()
        desired_side = user_comment.vote_type if user_comment else None
        if desired_side is None and request.user == comment.post.user:
            desired_side = _opposite_side(comment.vote_type)

        accepted_debate = Debate.objects.filter(
            post=comment.post,
            target=target_user,
            status='accepted'
        ).order_by('-updated_at').first()

        if desired_side not in ('yes', 'no') and not accepted_debate:
            return JsonResponse({'success': False, 'error': 'Please vote yes or no first before starting a debate.'}, status=400)

        if (
            accepted_debate is None
            and request.user != comment.post.user
            and desired_side == comment.vote_type
        ):
            return JsonResponse({
                'success': False,
                'error': 'You can only start a debate with comments from the opposite side.'
            }, status=400)

        reusable_completed = Debate.objects.filter(
            post=comment.post,
            target=target_user,
            status='completed',
        ).order_by('-updated_at').first()

        if reusable_completed:
            pending_for_target = Debate.objects.filter(target=target_user, status='pending').exclude(id=reusable_completed.id)
            category_pending_count = pending_for_target.filter(comment__vote_type=comment.vote_type).count()
            total_pending_count = pending_for_target.count()
            side_label = 'YES' if comment.vote_type == 'yes' else 'NO'

            if category_pending_count >= 5:
                return JsonResponse({
                    'success': False,
                    'queued': True,
                    'error': f'You are in queue. {side_label} queue is full (5/5), commentor still not responding to existing requests.'
                })

            if total_pending_count >= 10:
                return JsonResponse({
                    'success': False,
                    'queued': True,
                    'error': 'You are in queue. This user already has 10 pending requests, commentor still not responding to existing requests.'
                })

            reusable_completed.comment = comment
            reusable_completed.initiator = request.user
            reusable_completed.status = 'pending'
            reusable_completed.end_controller = None
            reusable_completed.end_controller_side = ''
            reusable_completed.save(update_fields=['comment', 'initiator', 'status', 'end_controller', 'end_controller_side', 'updated_at'])

            DebateParticipant.objects.filter(debate=reusable_completed).exclude(
                user_id__in=[reusable_completed.initiator_id, reusable_completed.target_id]
            ).update(is_active=False, left_at=timezone.now())

            DebateMessage.objects.create(
                debate=reusable_completed,
                sender=request.user,
                content=f"{request.user.username} requested to restart the debate.",
                is_system=True,
            )

            return JsonResponse({'success': True, 'message': 'Debate restart request sent!'})

        if accepted_debate:
            _ensure_debate_core_participants(accepted_debate)

            active_counts = {
                item['side']: item['total']
                for item in DebateParticipant.objects.filter(
                    debate=accepted_debate,
                    is_active=True,
                ).values('side').annotate(total=Count('id'))
            }
            desired_side = _pick_debate_side_for_user(
                accepted_debate,
                desired_side,
                active_counts=active_counts,
                fallback_side=_opposite_side(comment.vote_type),
            )

            side = desired_side
            yes_active = active_counts.get('yes', 0)
            no_active = active_counts.get('no', 0)
            conversation_full = (
                accepted_debate.yes_supporters > 0 and accepted_debate.no_supporters > 0
                and yes_active >= accepted_debate.yes_supporters
                and no_active >= accepted_debate.no_supporters
            )
            side_limit = accepted_debate.yes_supporters if side == 'yes' else accepted_debate.no_supporters
            participant = DebateParticipant.objects.filter(
                debate=accepted_debate,
                user=request.user,
            ).first()

            if participant and participant.side != side:
                participant.side = side
                participant.save(update_fields=['side'])

            if participant and participant.is_banned:
                return JsonResponse({
                    'success': False,
                    'error': 'The commentor removed you from this conversation. You cannot participate again.'
                })

            if participant and participant.is_active:
                return JsonResponse({
                    'success': True,
                    'message': 'You are already in this conversation.',
                    'redirect_url': f'/debates/{accepted_debate.id}/chat/'
                })

            if conversation_full:
                if not participant:
                    DebateParticipant.objects.create(
                        debate=accepted_debate,
                        user=request.user,
                        side=side,
                        is_active=False,
                    )
                return JsonResponse({
                    'success': True,
                    'queued': True,
                    'message': 'Conversation participant limit is reached. You can view the debate.',
                    'redirect_url': f'/debates/{accepted_debate.id}/chat/'
                })

            active_side_count = active_counts.get(side, 0)

            if side_limit and active_side_count >= side_limit:
                side_label = 'YES' if side == 'yes' else 'NO'
                if not participant:
                    DebateParticipant.objects.create(
                        debate=accepted_debate,
                        user=request.user,
                        side=side,
                        is_active=False,
                    )
                return JsonResponse({
                    'success': True,
                    'queued': True,
                    'message': f'{side_label} side is full right now. You can view the conversation.',
                    'redirect_url': f'/debates/{accepted_debate.id}/chat/'
                })

            if participant:
                participant.is_active = True
                participant.left_at = None
                participant.save(update_fields=['is_active', 'left_at'])
                joined_message = 'You rejoined the conversation.'
            else:
                DebateParticipant.objects.create(
                    debate=accepted_debate,
                    user=request.user,
                    side=side,
                    is_active=True,
                )
                joined_message = 'You joined the active conversation.'

            if not accepted_debate.end_controller_id:
                _set_end_controller_with_fallback(accepted_debate, preferred_side=side)

            return JsonResponse({
                'success': True,
                'message': joined_message,
                'redirect_url': f'/debates/{accepted_debate.id}/chat/'
            })

        # Check if debate already exists
        existing_debate = Debate.objects.filter(
            comment=comment,
            initiator=request.user,
            target=target_user,
            status='pending',
        ).exists()

        if existing_debate:
            return JsonResponse({'success': False, 'error': 'Debate request already sent'})

        pending_for_target = Debate.objects.filter(target=target_user, status='pending')
        category_pending_count = pending_for_target.filter(comment__vote_type=comment.vote_type).count()
        total_pending_count = pending_for_target.count()
        side_label = 'YES' if comment.vote_type == 'yes' else 'NO'

        if category_pending_count >= 5:
            return JsonResponse({
                'success': False,
                'queued': True,
                'error': f'You are in queue. {side_label} queue is full (5/5), commentor still not responding to existing requests.'
            })

        if total_pending_count >= 10:
            return JsonResponse({
                'success': False,
                'queued': True,
                'error': 'You are in queue. This user already has 10 pending requests, commentor still not responding to existing requests.'
            })

        Debate.objects.create(
            id=str(uuid.uuid4()),
            comment=comment,
            post=comment.post,
            initiator=request.user,
            target=target_user,
            status='pending'
        )

        return JsonResponse({'success': True, 'message': 'Debate request sent!'})
    except Comment.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Comment not found'})

@login_required
@require_POST
def report_comment(request):
    """Report a comment"""
    comment_id = request.POST.get('comment_id')

    try:
        comment = Comment.objects.get(id=comment_id)
        # For now, just mark as reported or something. In a real app, you'd have a Report model.
        # Here, perhaps just increment a report count or send notification.
        # Since no Report model, just return success.
        return JsonResponse({'success': True, 'message': 'Comment reported successfully.'})
    except Comment.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Comment not found'})

@login_required
@require_POST
def update_comment(request):
    """Update a user's own comment"""
    comment_id = request.POST.get('comment_id')
    content = request.POST.get('content', '').strip()

    if not content:
        return JsonResponse({'success': False, 'error': 'Comment content cannot be empty'})

    if check_content_moderation(content):
        return JsonResponse({'success': False, 'error': 'Your comment contains abusive language and cannot be saved.'}, status=400)

    try:
        comment = Comment.objects.get(id=comment_id)
    except Comment.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Comment not found'})

    if comment.user != request.user:
        return JsonResponse({'success': False, 'error': 'You can only edit your own comment'})

    CommentEditHistory.objects.create(
        comment=comment,
        original_content=comment.content,
    )
    comment.content = content
    comment.is_edited = True
    comment.save(update_fields=['content', 'is_edited', 'updated_at'])
    return JsonResponse({'success': True, 'message': 'Comment updated successfully', 'content': comment.content, 'is_edited': True})

@login_required
@require_POST
def accept_debate(request, debate_id):
    """Accept a debate request"""
    try:
        debate = Debate.objects.get(id=debate_id, target=request.user)
        yes_supporters_raw = request.POST.get('yes_supporters', '').strip()
        no_supporters_raw = request.POST.get('no_supporters', '').strip()

        limits_source = None

        # If this debate already has limits set, reuse them.
        if debate.yes_supporters > 0 or debate.no_supporters > 0:
            limits_source = (debate.yes_supporters, debate.no_supporters)
        else:
            # Reuse limits from a previously accepted debate for the same target and post.
            previous_accepted = Debate.objects.filter(
                post=debate.post,
                target=debate.target,
                status='accepted'
            ).exclude(id=debate.id).filter(
                Q(yes_supporters__gt=0) | Q(no_supporters__gt=0)
            ).order_by('-updated_at').first()

            if previous_accepted:
                limits_source = (previous_accepted.yes_supporters, previous_accepted.no_supporters)

        if yes_supporters_raw == '' and no_supporters_raw == '':
            if limits_source:
                yes_supporters, no_supporters = limits_source
            else:
                return JsonResponse({
                    'success': False,
                    'requires_counts': True,
                    'error': 'Please set participant limits for this debate first.'
                })
        else:
            if yes_supporters_raw == '' or no_supporters_raw == '':
                return JsonResponse({
                    'success': False,
                    'error': 'Please provide both YES and NO participant limits.'
                })
            try:
                yes_supporters = max(int(yes_supporters_raw), 0)
                no_supporters = max(int(no_supporters_raw), 0)
            except ValueError:
                return JsonResponse({'success': False, 'error': 'Participant counts must be valid numbers'})

        debate.status = 'accepted'
        debate.yes_supporters = yes_supporters
        debate.no_supporters = no_supporters
        debate.save()

        _ensure_debate_core_participants(debate)
        if not debate.end_controller_id:
            _set_end_controller_with_fallback(debate, preferred_side=debate.comment.vote_type)

        if not debate.messages.exists():
            DebateMessage.objects.create(
                debate=debate,
                sender=request.user,
                content=f"Debate accepted. Suggested participants: Yes {yes_supporters}, No {no_supporters}."
            )

        # Notify the post author that a debate started on their post
        notify_post_author(debate.post, 'author_debate', debate.initiator)

        return JsonResponse({
            'success': True,
            'message': 'Debate accepted!',
            'redirect_url': f'/debates/{debate.id}/chat/'
        })
    except Debate.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Debate not found'})

@login_required
@require_POST
def reject_debate(request, debate_id):
    """Reject a debate request"""
    try:
        debate = Debate.objects.get(id=debate_id, target=request.user)
        debate.status = 'rejected'
        debate.save()
        messages.success(request, 'Debate rejected!')
    except Debate.DoesNotExist:
        messages.error(request, 'Debate not found')

    return redirect('notifications')


@login_required
@xframe_options_sameorigin
def debate_chat(request, debate_id):
    """Two-person debate chat room"""
    debate = get_object_or_404(
        Debate.objects.select_related('initiator', 'target', 'post'),
        id=debate_id
    )

    if debate.status not in ('accepted', 'completed'):
        messages.error(request, 'This conversation is not active.')
        return redirect('notifications')

    _ensure_debate_core_participants(debate)
    participation = DebateParticipant.objects.filter(debate=debate, user=request.user).first()

    # Non-participants can view any completed debate as read-only spectators
    is_spectator = participation is None
    if is_spectator and debate.status != 'completed':
        messages.error(request, 'You do not have access to this debate.')
        return redirect('notifications')

    raw_messages = list(debate.messages.select_related('sender', 'reply_to__sender').all())
    message_ids = [message.id for message in raw_messages]
    likes_map, dislikes_map, viewer_reaction_map = _debate_message_reaction_maps(message_ids, request.user.id)
    side_map = _sender_side_map(debate)
    messages_list = [
        {
            'id': message.id,
            'content': _decode_chat_content_from_storage(message.content),
            'sender': message.sender.username,
            'sender_id': message.sender_id,
            'sender_side': side_map.get(message.sender_id, ''),
            'sender_avatar': _safe_avatar_url(message.sender),
            'sender_initial': (message.sender.username[:1] or '?').upper(),
            'reply_to': ({
                'id': message.reply_to.id,
                'sender': message.reply_to.sender.username,
                'content': _decode_chat_content_from_storage(message.reply_to.content),
            } if message.reply_to_id else None),
            'is_own': message.sender_id == request.user.id,
            'can_remove_sender': (not is_spectator) and request.user.id == debate.target_id and message.sender_id != request.user.id,
            'is_edited': message.is_edited,
            'likes_count': likes_map.get(message.id, 0),
            'dislikes_count': dislikes_map.get(message.id, 0),
            'user_reaction': viewer_reaction_map.get(message.id, ''),
            'created_at': message.created_at,
            'created_date_label': message.created_at.strftime('%b %d, %Y'),
            'created_time': message.created_at.strftime('%I:%M %p'),
        }
        for message in raw_messages
    ]

    opponent = debate.target if request.user == debate.initiator else debate.initiator
    context = {
        'debate': debate,
        'messages_list': messages_list,
        'opponent_avatar': _safe_avatar_url(opponent),
        'active_participants': _active_participants_payload(debate, request.user),
        'is_active_participant': (not is_spectator) and participation.is_active and not participation.is_banned,
        'can_post': (not is_spectator) and debate.status == 'accepted' and participation.is_active and not participation.is_banned,
        'can_rejoin': (not is_spectator) and (not participation.is_active) and (not participation.is_banned),
        'is_view_only': is_spectator or (participation.is_banned if participation else False) or debate.status == 'completed',
        'can_end_chat': (not is_spectator) and debate.status == 'accepted' and participation.is_active and not participation.is_banned and debate.end_controller_id == request.user.id,
        'current_controller_name': debate.end_controller.username if debate.end_controller else '',
        'current_controller_side': debate.end_controller_side,
        'can_moderate_chat': (not is_spectator) and request.user.id == debate.target_id,
    }
    return render(request, 'frontend/debate_chat.html', context)


@login_required
@require_POST
def send_debate_message(request, debate_id):
    """Send a message in a debate chat"""
    debate = get_object_or_404(Debate, id=debate_id)

    if debate.status != 'accepted':
        return JsonResponse({'success': False, 'error': 'Conversation is not active'})

    _ensure_debate_core_participants(debate)
    participation = DebateParticipant.objects.filter(debate=debate, user=request.user).first()

    if not participation:
        return JsonResponse({'success': False, 'error': 'Access denied'})

    if participation.is_banned:
        return JsonResponse({'success': False, 'error': 'The commentor removed you from this conversation.'}, status=403)

    if not participation.is_active:
        return JsonResponse({'success': False, 'error': 'You left this conversation. Rejoin to send messages.'})

    content = request.POST.get('content', '')
    if not content.strip():
        return JsonResponse({'success': False, 'error': 'Message cannot be empty'})

    # Check content moderation
    if check_content_moderation(content):
        return JsonResponse({'success': False, 'error': 'Your message contains abusive language and cannot be sent.'})

    reply_to = None
    reply_to_id = request.POST.get('reply_to_id', '').strip()
    if reply_to_id:
        try:
            reply_to = DebateMessage.objects.get(id=int(reply_to_id), debate=debate)
        except (ValueError, DebateMessage.DoesNotExist):
            return JsonResponse({'success': False, 'error': 'Invalid reply target'})

    try:
        message = DebateMessage.objects.create(
            debate=debate,
            sender=request.user,
            reply_to=reply_to,
            content=_encode_chat_content_for_storage(content)
        )
    except DataError:
        return JsonResponse({'success': False, 'error': 'Unable to store this message text. Please try a shorter one.'}, status=400)

    return JsonResponse({
        'success': True,
        'message': {
            'id': message.id,
            'content': _decode_chat_content_from_storage(message.content),
            'sender': message.sender.username,
            'sender_id': message.sender_id,
            'sender_side': participation.side,
            'sender_avatar': _safe_avatar_url(message.sender),
            'sender_initial': (message.sender.username[:1] or '?').upper(),
            'reply_to': ({
                'id': reply_to.id,
                'sender': reply_to.sender.username,
                'content': _decode_chat_content_from_storage(reply_to.content),
            } if reply_to else None),
            'is_own': True,
            'can_remove_sender': request.user.id == debate.target_id and message.sender_id != request.user.id,
            'is_system': False,
            'is_edited': message.is_edited,
            'likes_count': 0,
            'dislikes_count': 0,
            'user_reaction': '',
            'created_at': message.created_at.strftime('%b %d, %I:%M %p'),
            'created_date_label': message.created_at.strftime('%b %d, %Y'),
            'created_time': message.created_at.strftime('%I:%M %p'),
        }
    })


@login_required
@require_POST
def update_debate_message(request, debate_id, message_id):
    """Edit own debate message and record original in history."""
    debate = get_object_or_404(Debate, id=debate_id)
    message = get_object_or_404(DebateMessage, id=message_id, debate=debate)

    if message.sender != request.user:
        return JsonResponse({'success': False, 'error': 'You can only edit your own messages.'}, status=403)

    if message.is_system:
        return JsonResponse({'success': False, 'error': 'System messages cannot be edited.'}, status=400)

    content = request.POST.get('content', '').strip()
    if not content:
        return JsonResponse({'success': False, 'error': 'Message cannot be empty.'})

    # Check content moderation
    if check_content_moderation(content):
        return JsonResponse({'success': False, 'error': 'Your message edit contains abusive language and cannot be saved.'})

    DebateMessageEditHistory.objects.create(
        message=message,
        original_content=message.content,
    )
    message.content = _encode_chat_content_for_storage(content)
    message.is_edited = True
    message.save(update_fields=['content', 'is_edited'])

    return JsonResponse({'success': True, 'content': content, 'is_edited': True})


@login_required
def debate_messages(request, debate_id):
    """Fetch messages for a debate chat"""
    debate = get_object_or_404(Debate, id=debate_id)

    _ensure_debate_core_participants(debate)
    participation = DebateParticipant.objects.filter(debate=debate, user=request.user).first()

    if not participation:
        return JsonResponse({'success': False, 'error': 'Access denied'}, status=403)

    opponent = debate.target if request.user == debate.initiator else debate.initiator

    raw_messages = list(debate.messages.select_related('sender', 'reply_to__sender').all())
    message_ids = [message.id for message in raw_messages]
    likes_map, dislikes_map, viewer_reaction_map = _debate_message_reaction_maps(message_ids, request.user.id)
    side_map = _sender_side_map(debate)

    messages = [
        {
            'id': message.id,
            'content': _decode_chat_content_from_storage(message.content),
            'sender': message.sender.username,
            'sender_id': message.sender_id,
            'sender_side': side_map.get(message.sender_id, ''),
            'sender_avatar': _safe_avatar_url(message.sender),
            'sender_initial': (message.sender.username[:1] or '?').upper(),
            'reply_to': ({
                'id': message.reply_to.id,
                'sender': message.reply_to.sender.username,
                'content': _decode_chat_content_from_storage(message.reply_to.content),
            } if message.reply_to_id else None),
            'is_own': message.sender_id == request.user.id,
            'can_remove_sender': request.user.id == debate.target_id and message.sender_id != request.user.id,
            'is_system': message.is_system,
            'is_edited': message.is_edited,
            'likes_count': likes_map.get(message.id, 0),
            'dislikes_count': dislikes_map.get(message.id, 0),
            'user_reaction': viewer_reaction_map.get(message.id, ''),
            'created_at': message.created_at.strftime('%b %d, %I:%M %p'),
            'created_date_label': message.created_at.strftime('%b %d, %Y'),
            'created_time': message.created_at.strftime('%I:%M %p'),
        }
        for message in raw_messages
    ]

    return JsonResponse({
        'success': True,
        'messages': messages,
        'opponent': opponent.username,
        'opponent_avatar': _safe_avatar_url(opponent),
        'active_participants': _active_participants_payload(debate, request.user),
        'yes_supporters': debate.yes_supporters,
        'no_supporters': debate.no_supporters,
        'user_is_active': debate.status == 'accepted' and participation.is_active and not participation.is_banned,
        'can_post': debate.status == 'accepted' and participation.is_active and not participation.is_banned,
        'can_rejoin': debate.status == 'accepted' and (not participation.is_active) and (not participation.is_banned),
        'is_view_only': participation.is_banned or debate.status == 'completed',
        'can_end_chat': debate.status == 'accepted' and participation.is_active and not participation.is_banned and debate.end_controller_id == request.user.id,
        'debate_status': debate.status,
        'current_controller_name': debate.end_controller.username if debate.end_controller else '',
        'current_controller_side': debate.end_controller_side,
        'can_moderate_chat': request.user.id == debate.target_id,
    })


@login_required
@require_POST
def react_to_debate_message(request, debate_id, message_id):
    """React to a debate message with like/dislike (mobile long-press)"""
    debate = get_object_or_404(Debate, id=debate_id)

    if debate.status != 'accepted':
        return JsonResponse({'success': False, 'error': 'Conversation is not active'}, status=400)

    _ensure_debate_core_participants(debate)
    participation = DebateParticipant.objects.filter(debate=debate, user=request.user).first()

    if not participation:
        return JsonResponse({'success': False, 'error': 'Access denied'}, status=403)

    if participation.is_banned:
        return JsonResponse({'success': False, 'error': 'You cannot react to messages.'}, status=403)

    message = get_object_or_404(DebateMessage, id=message_id, debate=debate)

    if message.sender_id == request.user.id:
        return JsonResponse({'success': False, 'error': 'You cannot react to your own messages.'}, status=400)
    reaction_type = request.POST.get('reaction_type', '').strip().lower()

    if reaction_type not in ['like', 'dislike']:
        return JsonResponse({'success': False, 'error': 'Invalid reaction type'}, status=400)

    reaction, created = DebateMessageReaction.objects.get_or_create(
        message=message,
        user=request.user,
        defaults={'reaction': reaction_type}
    )
    if not created and reaction.reaction != reaction_type:
        reaction.reaction = reaction_type
        reaction.save(update_fields=['reaction', 'updated_at'])

    likes_count = DebateMessageReaction.objects.filter(message=message, reaction='like').count()
    dislikes_count = DebateMessageReaction.objects.filter(message=message, reaction='dislike').count()

    return JsonResponse({
        'success': True,
        'message': f'{reaction_type.title()}d message successfully',
        'reaction_type': reaction_type,
        'message_id': message_id,
        'likes_count': likes_count,
        'dislikes_count': dislikes_count,
        'user_reaction': reaction_type,
    })


@login_required
@require_POST
def report_debate_message(request, debate_id, message_id):
    """Report an abusive debate message to configured moderators."""
    debate = get_object_or_404(Debate, id=debate_id)
    _ensure_debate_core_participants(debate)

    participation = DebateParticipant.objects.filter(debate=debate, user=request.user).first()
    if not participation:
        return JsonResponse({'success': False, 'error': 'Access denied'}, status=403)

    if participation.is_banned:
        return JsonResponse({'success': False, 'error': 'You cannot report from this conversation.'}, status=403)

    message = get_object_or_404(DebateMessage, id=message_id, debate=debate)
    if message.sender_id == request.user.id:
        return JsonResponse({'success': False, 'error': 'You cannot report your own message.'}, status=400)

    details = (request.POST.get('details') or '').strip()
    if len(details) < 10:
        return JsonResponse({
            'success': False,
            'error': 'Please clearly explain what is wrong with this message (at least 10 characters).'
        }, status=400)

    report, created = DebateMessageReport.objects.get_or_create(
        message=message,
        reporter=request.user,
        defaults={
            'debate': debate,
            'reported_user': message.sender,
            'reason': 'abusive_language',
            'details': details,
        },
    )

    if not created:
        if details and not report.details:
            report.details = details
            report.save(update_fields=['details', 'updated_at'])
        return JsonResponse({'success': True, 'message': 'This message is already reported. Moderators will review it.'})

    moderators = _moderator_users().exclude(id=request.user.id)
    preview = _decode_chat_content_from_storage(message.content)
    for moderator in moderators:
        Notification.objects.create(
            user=moderator,
            post=debate.post,
            notification_type='moderation_alert',
            message=(
                f"Message report in debate '{debate.post.title}': "
                f"{request.user.username} reported {message.sender.username}. "
                f"Reason: {details[:180]}. "
                f"Preview: {preview[:120]}"
            ),
        )

    return JsonResponse({'success': True, 'message': 'Message reported. Moderators have been notified.'})


@login_required
@require_POST
def moderate_debate_message_report(request, report_id):
    """Moderator review action for reported debate messages."""
    if not _is_configured_moderator(request.user):
        return JsonResponse({'success': False, 'error': 'Only configured moderators can review reports.'}, status=403)

    report = get_object_or_404(
        DebateMessageReport.objects.select_related('debate__post', 'message', 'reported_user', 'reporter'),
        id=report_id,
    )

    if report.status != 'pending':
        return JsonResponse({'success': True, 'message': 'This report was already reviewed.'})

    action = (request.POST.get('action') or '').strip().lower()
    note = (request.POST.get('note') or '').strip()
    warning_message = (request.POST.get('warning_message') or '').strip()
    confirm_phrase = (request.POST.get('confirm_phrase') or '').strip()

    if action not in {'dismiss', 'delete_warn', 'delete_user'}:
        return JsonResponse({'success': False, 'error': 'Invalid moderation action.'}, status=400)

    report.reviewed_by = request.user
    report.reviewed_at = timezone.now()
    report.resolution_note = note

    if action == 'dismiss':
        report.status = 'dismissed'
        report.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'resolution_note', 'updated_at'])
        return JsonResponse({'success': True, 'message': 'Report dismissed.'})

    if action == 'delete_user':
        if confirm_phrase.upper() != 'DELETE':
            return JsonResponse({'success': False, 'error': 'Type DELETE to confirm user deletion.'}, status=400)

        reported_user = report.reported_user
        if reported_user.id == request.user.id:
            return JsonResponse({'success': False, 'error': 'You cannot delete your own account.'}, status=400)
        if _is_configured_moderator(reported_user):
            return JsonResponse({'success': False, 'error': 'Configured moderator accounts cannot be deleted here.'}, status=400)

        username = reported_user.username
        reported_user.delete()
        return JsonResponse({'success': True, 'message': f"User '{username}' deleted successfully."})

    debate = report.debate
    reported_user = report.reported_user
    message_exists = DebateMessage.objects.filter(id=report.message_id).exists()
    if message_exists:
        DebateMessage.objects.filter(id=report.message_id).delete()
        DebateMessage.objects.create(
            debate=debate,
            sender=debate.target,
            content='A message was removed by moderation due to abusive content.',
            is_system=True,
        )

    if not warning_message:
        warning_message = (
            "Your message was removed after being reported by other users. "
            "Please keep discussions respectful and follow our community guidelines. "
            "Continued violations may lead to account restrictions or permanent removal."
        )

    Notification.objects.create(
        user=reported_user,
        post=debate.post,
        notification_type='moderation_warning',
        message=warning_message,
    )

    report.status = 'actioned'
    report.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'resolution_note', 'updated_at'])
    return JsonResponse({'success': True, 'message': 'Message deleted and warning sent.'})


@login_required
@require_POST
def moderate_profile_report(request, report_id):
    """Moderator review action for reported user profiles."""
    if not _is_configured_moderator(request.user):
        return JsonResponse({'success': False, 'error': 'Only configured moderators can review profile reports.'}, status=403)

    report = get_object_or_404(
        ProfileReport.objects.select_related('reporter', 'reported_user'),
        id=report_id,
    )

    if report.status != 'pending':
        return JsonResponse({'success': True, 'message': 'This profile report was already reviewed.'})

    action = (request.POST.get('action') or '').strip().lower()
    note = (request.POST.get('note') or '').strip()
    warning_message = (request.POST.get('warning_message') or '').strip()
    confirm_phrase = (request.POST.get('confirm_phrase') or '').strip()

    if action not in {'dismiss', 'warn', 'delete_user'}:
        return JsonResponse({'success': False, 'error': 'Invalid moderation action.'}, status=400)

    reported_user = report.reported_user

    if action == 'dismiss':
        report.status = 'dismissed'
        report.save(update_fields=['status', 'updated_at'])
        return JsonResponse({'success': True, 'message': 'Profile report dismissed.'})

    if action == 'delete_user':
        if confirm_phrase.upper() != 'DELETE':
            return JsonResponse({'success': False, 'error': 'Type DELETE to confirm user deletion.'}, status=400)

        if reported_user.id == request.user.id:
            return JsonResponse({'success': False, 'error': 'You cannot delete your own account.'}, status=400)
        if _is_configured_moderator(reported_user):
            return JsonResponse({'success': False, 'error': 'Configured moderator accounts cannot be deleted here.'}, status=400)

        username = reported_user.username
        reported_user.delete()
        report.status = 'reviewed'
        report.save(update_fields=['status', 'updated_at'])
        return JsonResponse({'success': True, 'message': f"User '{username}' deleted successfully."})

    if not warning_message:
        warning_message = (
            'Your profile was reported and reviewed by moderators. '
            'Please follow community guidelines. Continued violations may lead to restrictions.'
        )

    context_post = (
        Post.objects.filter(user=reported_user).order_by('-created_at').first()
        or Post.objects.filter(user=request.user).order_by('-created_at').first()
        or Post.objects.order_by('-created_at').first()
    )
    if not context_post:
        return JsonResponse({'success': False, 'error': 'Unable to create moderation notification context.'}, status=400)

    Notification.objects.create(
        user=reported_user,
        post=context_post,
        notification_type='moderation_warning',
        message=warning_message,
    )

    report.status = 'reviewed'
    if note:
        report.details = (report.details + ('\n\nModerator note: ' if report.details else 'Moderator note: ') + note)[:2000]
    report.save(update_fields=['status', 'details', 'updated_at'])
    return JsonResponse({'success': True, 'message': 'Warning sent and profile report marked reviewed.'})


@login_required
@require_POST
def increase_debate_limits(request, debate_id):
    debate = get_object_or_404(Debate, id=debate_id)

    if debate.status != 'accepted':
        return JsonResponse({'success': False, 'error': 'Only active conversations can update limits.'}, status=400)

    if request.user.id != debate.target_id:
        return JsonResponse({'success': False, 'error': 'Only the host can update participant limits.'}, status=403)

    yes_raw = (request.POST.get('yes_supporters') or '').strip()
    no_raw = (request.POST.get('no_supporters') or '').strip()

    if yes_raw == '' and no_raw == '':
        return JsonResponse({'success': False, 'error': 'Provide at least one limit to update.'}, status=400)

    next_yes = debate.yes_supporters
    next_no = debate.no_supporters

    if yes_raw != '':
        try:
            next_yes = int(yes_raw)
        except ValueError:
            return JsonResponse({'success': False, 'error': 'YES limit must be a valid number.'}, status=400)

    if no_raw != '':
        try:
            next_no = int(no_raw)
        except ValueError:
            return JsonResponse({'success': False, 'error': 'NO limit must be a valid number.'}, status=400)

    if next_yes < 0 or next_no < 0:
        return JsonResponse({'success': False, 'error': 'Participant limits cannot be negative.'}, status=400)

    if next_yes < debate.yes_supporters or next_no < debate.no_supporters:
        return JsonResponse({'success': False, 'error': 'You can only increase limits during conversation.'}, status=400)

    if next_yes == debate.yes_supporters and next_no == debate.no_supporters:
        return JsonResponse({
            'success': True,
            'message': 'Participant limits are unchanged.',
            'yes_supporters': debate.yes_supporters,
            'no_supporters': debate.no_supporters,
        })

    old_yes = debate.yes_supporters
    old_no = debate.no_supporters
    debate.yes_supporters = next_yes
    debate.no_supporters = next_no
    debate.save(update_fields=['yes_supporters', 'no_supporters', 'updated_at'])

    DebateMessage.objects.create(
        debate=debate,
        sender=request.user,
        content=f"Host updated participant limits: Yes {old_yes} -> {next_yes}, No {old_no} -> {next_no}.",
        is_system=True,
    )

    return JsonResponse({
        'success': True,
        'message': 'Participant limits increased successfully.',
        'yes_supporters': debate.yes_supporters,
        'no_supporters': debate.no_supporters,
    })


@login_required
def debate_info(request, debate_id):
    """Return debate metadata as JSON for the floating chat manager"""
    debate = get_object_or_404(
        Debate.objects.select_related('initiator', 'target', 'post'),
        id=debate_id
    )
    _ensure_debate_core_participants(debate)
    participation = DebateParticipant.objects.filter(debate=debate, user=request.user).first()

    if not participation:
        return JsonResponse({'success': False, 'error': 'Access denied'}, status=403)

    opponent = debate.target if request.user == debate.initiator else debate.initiator
    can_post = debate.status == 'accepted' and participation.is_active and not participation.is_banned
    return JsonResponse({
        'success': True,
        'debate': {
            'id': str(debate.id),
            'title': debate.post.title,
            'opponent': opponent.username,
            'opponent_avatar': _safe_avatar_url(opponent),
            'active_participants': _active_participants_payload(debate, request.user),
            'yes_supporters': debate.yes_supporters,
            'no_supporters': debate.no_supporters,
            'post_id': str(debate.post.id),
            'user_is_active': can_post,
            'can_post': can_post,
            'can_rejoin': debate.status == 'accepted' and (not participation.is_active) and (not participation.is_banned),
            'is_view_only': participation.is_banned or debate.status == 'completed',
            'can_end_chat': can_post and debate.end_controller_id == request.user.id,
            'current_controller_name': debate.end_controller.username if debate.end_controller else '',
            'current_controller_side': debate.end_controller_side,
            'debate_status': debate.status,
            'can_moderate_chat': request.user.id == debate.target_id,
        }
    })


@login_required
@require_POST
def leave_debate(request, debate_id):
    debate = get_object_or_404(Debate, id=debate_id)
    _ensure_debate_core_participants(debate)

    participation = DebateParticipant.objects.filter(debate=debate, user=request.user).first()
    if not participation:
        return JsonResponse({'success': False, 'error': 'Access denied'}, status=403)

    if participation.is_banned:
        return JsonResponse({'success': False, 'error': 'You were removed from this conversation.'}, status=403)

    if not participation.is_active:
        return JsonResponse({'success': False, 'error': 'You already left this conversation.'})

    participation.is_active = False
    participation.left_at = timezone.now()
    participation.save(update_fields=['is_active', 'left_at'])

    if debate.end_controller_id == request.user.id:
        _set_end_controller_with_fallback(
            debate,
            preferred_side=participation.side,
            exclude_user_id=request.user.id
        )

    DebateMessage.objects.create(
        debate=debate,
        sender=request.user,
        content=f"{request.user.username} left the conversation.",
        is_system=True,
    )

    return JsonResponse({'success': True, 'message': 'You left the conversation.'})


@login_required
@require_POST
def rejoin_debate(request, debate_id):
    debate = get_object_or_404(Debate, id=debate_id)
    _ensure_debate_core_participants(debate)

    participation = DebateParticipant.objects.filter(debate=debate, user=request.user).first()
    if not participation:
        return JsonResponse({'success': False, 'error': 'Access denied'}, status=403)

    if participation.is_banned:
        return JsonResponse({'success': False, 'error': 'The commentor removed you from this conversation.'}, status=403)

    if participation.is_active:
        return JsonResponse({'success': False, 'error': 'You are already in this conversation.'})

    side_limit = debate.yes_supporters if participation.side == 'yes' else debate.no_supporters
    active_side_count = DebateParticipant.objects.filter(
        debate=debate,
        side=participation.side,
        is_active=True
    ).count()

    if side_limit and active_side_count >= side_limit:
        side_label = 'YES' if participation.side == 'yes' else 'NO'
        return JsonResponse({'success': False, 'error': f'{side_label} side is full right now.'})

    participation.is_active = True
    participation.left_at = None
    participation.save(update_fields=['is_active', 'left_at'])

    if not debate.end_controller_id:
        _set_end_controller_with_fallback(debate, preferred_side=participation.side)

    DebateMessage.objects.create(
        debate=debate,
        sender=request.user,
        content=f"{request.user.username} rejoined the conversation.",
        is_system=True,
    )

    return JsonResponse({'success': True, 'message': 'You rejoined the conversation.'})


@login_required
@require_POST
def end_debate(request, debate_id):
    debate = get_object_or_404(Debate, id=debate_id)
    _ensure_debate_core_participants(debate)

    participation = DebateParticipant.objects.filter(debate=debate, user=request.user).first()
    if not participation or not participation.is_active:
        return JsonResponse({'success': False, 'error': 'Only active participants can end this chat.'}, status=403)

    if debate.end_controller_id != request.user.id:
        return JsonResponse({'success': False, 'error': 'You do not currently control ending this chat.'}, status=403)

    debate.status = 'completed'
    debate.save(update_fields=['status', 'updated_at'])

    DebateParticipant.objects.filter(debate=debate, is_active=True).update(is_active=False, left_at=timezone.now())

    return JsonResponse({'success': True, 'message': 'Conversation ended.'})


@login_required
@require_POST
def remove_debate_participant(request, debate_id):
    debate = get_object_or_404(Debate, id=debate_id)
    _ensure_debate_core_participants(debate)

    if request.user.id != debate.target_id:
        return JsonResponse({'success': False, 'error': 'Only the commentor can remove participants.'}, status=403)

    user_id = (request.POST.get('user_id') or '').strip()
    if not user_id.isdigit():
        return JsonResponse({'success': False, 'error': 'Invalid participant.'}, status=400)

    if int(user_id) == request.user.id:
        return JsonResponse({'success': False, 'error': 'You cannot remove yourself.'}, status=400)

    participation = DebateParticipant.objects.filter(debate=debate, user_id=int(user_id)).select_related('user').first()
    if not participation:
        return JsonResponse({'success': False, 'error': 'Participant not found.'}, status=404)

    participation.is_active = False
    participation.is_banned = True
    participation.left_at = timezone.now()
    participation.save(update_fields=['is_active', 'is_banned', 'left_at'])

    CommentModeratorBlock.objects.get_or_create(
        comment_owner=request.user,
        blocked_user=participation.user,
    )

    if debate.end_controller_id == participation.user_id:
        _set_end_controller_with_fallback(
            debate,
            preferred_side=participation.side,
            exclude_user_id=participation.user_id,
        )

    return JsonResponse({
        'success': True,
        'message': f'{participation.user.username} was removed from participation and can now only view this conversation.'
    })

# ─── Poll Views ────────────────────────────────────────────────────────────────

def polls_list(request):
    """Show paginated list of all active polls."""
    category_filter = request.GET.get('category', '').strip()
    polls_qs = Poll.objects.filter(is_deleted_by_moderation=False)
    if category_filter:
        polls_qs = polls_qs.filter(category=category_filter)

    paginator = Paginator(polls_qs, 20)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    polls_data = []
    for poll in page_obj:
        opts = list(poll.options.all())
        total = poll.votes.count()
        user_vote = None
        if request.user.is_authenticated:
            try:
                user_vote = PollVote.objects.get(poll=poll, user=request.user)
            except PollVote.DoesNotExist:
                pass
        opt_data = []
        for opt in opts:
            cnt = opt.votes.count()
            pct = round(cnt / total * 100, 1) if total > 0 else 0
            opt_data.append({'option': opt, 'vote_count': cnt, 'percentage': pct})
        polls_data.append({
            'poll': poll,
            'options': opts,
            'option_data': opt_data,
            'total_votes': total,
            'user_vote': user_vote,
        })

    categories = [{'name': c[0]} for c in CATEGORY_CHOICES]
    return render(request, 'frontend/polls_list.html', {
        'polls_data': polls_data,
        'page_obj': page_obj,
        'categories': categories,
        'active_category': category_filter,
    })


def create_poll(request):
    """Poll creation page."""
    if not request.user.is_authenticated:
        from django.urls import reverse
        return redirect(f"{reverse('login')}?next={request.path}")

    categories = [{'name': c[0]} for c in CATEGORY_CHOICES]

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        option1 = request.POST.get('option1', '').strip()
        option2 = request.POST.get('option2', '').strip()
        category = request.POST.get('category', '').strip()
        hashtags_raw = request.POST.get('hashtags', '').strip()
        description = request.POST.get('description', '').strip()

        errors = {}
        if not title:
            errors['title'] = 'Question is required.'
        if not option1:
            errors['option1'] = 'Option 1 is required.'
        if not option2:
            errors['option2'] = 'Option 2 is required.'
        if option1 and option2 and option1.lower() == option2.lower():
            errors['option2'] = 'Options must be different.'
        if not category:
            errors['category'] = 'Category is required.'

        if not errors:
            combined = f"{title} {option1} {option2} {description}".strip()
            if check_content_moderation(combined):
                errors['title'] = 'Your poll contains inappropriate content.'

        if errors:
            return render(request, 'frontend/create_poll.html', {
                'categories': categories,
                'errors': errors,
                'form_data': request.POST,
            })

        poll = Poll.objects.create(
            id=str(uuid.uuid4()),
            user=request.user,
            title=title,
            description=description,
            category=category,
            hashtags=','.join(Post.parse_hashtags(hashtags_raw)),
        )
        PollOption.objects.create(poll=poll, text=option1, order=0)
        PollOption.objects.create(poll=poll, text=option2, order=1)

        return redirect('poll_detail', poll_id=poll.id)

    return render(request, 'frontend/create_poll.html', {'categories': categories})


def poll_detail(request, poll_id):
    """Poll detail page with vote chart and side comments."""
    poll = get_object_or_404(Poll, id=poll_id, is_deleted_by_moderation=False)
    options = list(poll.options.all())
    total_votes = poll.votes.count()

    user_vote = None
    if request.user.is_authenticated:
        try:
            user_vote = PollVote.objects.get(poll=poll, user=request.user)
        except PollVote.DoesNotExist:
            pass

    option_data = []
    for opt in options:
        cnt = opt.votes.count()
        pct = round(cnt / total_votes * 100, 1) if total_votes > 0 else 0
        comments = opt.comments.filter(is_deleted_by_moderation=False).select_related('user')
        # Attach user reaction to each comment
        comments_with_reaction = []
        for c in comments:
            user_reaction = None
            if request.user.is_authenticated:
                try:
                    r = PollCommentReaction.objects.get(comment=c, user=request.user)
                    user_reaction = r.reaction
                except PollCommentReaction.DoesNotExist:
                    pass
            comments_with_reaction.append({'comment': c, 'user_reaction': user_reaction})
        option_data.append({
            'option': opt,
            'vote_count': cnt,
            'percentage': pct,
            'comments': comments_with_reaction,
        })

    return render(request, 'frontend/poll.html', {
        'poll': poll,
        'options': options,
        'option_data': option_data,
        'total_votes': total_votes,
        'user_vote': user_vote,
        'voted_option_id': str(user_vote.option_id) if user_vote else None,
    })


@require_POST
def poll_vote(request, poll_id):
    """AJAX endpoint — cast or change a poll vote."""
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required'}, status=401)

    poll = get_object_or_404(Poll, id=poll_id, is_deleted_by_moderation=False)

    try:
        data = json.loads(request.body)
        option_id = int(data.get('option_id', 0))
    except (json.JSONDecodeError, ValueError, TypeError):
        return JsonResponse({'error': 'Invalid request'}, status=400)

    try:
        option = PollOption.objects.get(id=option_id, poll=poll)
    except PollOption.DoesNotExist:
        return JsonResponse({'error': 'Invalid option'}, status=400)

    if PollVote.objects.filter(poll=poll, user=request.user).exists():
        return JsonResponse({'error': 'You have already voted on this poll.'}, status=400)

    PollVote.objects.create(user=request.user, poll=poll, option=option)

    total = poll.votes.count()
    options_out = []
    for opt in poll.options.all():
        cnt = opt.votes.count()
        pct = round(cnt / total * 100, 1) if total > 0 else 0
        options_out.append({'id': opt.id, 'text': opt.text, 'vote_count': cnt, 'percentage': pct})

    return JsonResponse({'success': True, 'voted_option_id': option.id, 'total_votes': total, 'options': options_out})


@require_POST
def create_poll_comment(request, poll_id):
    """AJAX endpoint — post a side comment on a poll."""
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required'}, status=401)

    poll = get_object_or_404(Poll, id=poll_id, is_deleted_by_moderation=False)

    try:
        data = json.loads(request.body)
        option_id = int(data.get('option_id', 0))
        content = (data.get('content') or '').strip()
    except (json.JSONDecodeError, ValueError, TypeError):
        return JsonResponse({'error': 'Invalid request'}, status=400)

    try:
        user_vote = PollVote.objects.get(poll=poll, user=request.user)
    except PollVote.DoesNotExist:
        return JsonResponse({'error': 'You must vote before commenting.'}, status=400)

    if user_vote.option_id != option_id:
        return JsonResponse({'error': 'You can only comment on the side you voted for.'}, status=403)

    if PollComment.objects.filter(poll=poll, user=request.user).exists():
        return JsonResponse({'error': 'You can only comment once per poll.'}, status=400)

    try:
        option = PollOption.objects.get(id=option_id, poll=poll)
    except PollOption.DoesNotExist:
        return JsonResponse({'error': 'Invalid option.'}, status=400)

    if not content:
        return JsonResponse({'error': 'Comment cannot be empty.'}, status=400)

    if check_content_moderation(content):
        return JsonResponse({'error': 'Your comment contains inappropriate content.'}, status=400)

    comment = PollComment.objects.create(
        id=str(uuid.uuid4()),
        poll=poll,
        user=request.user,
        option=option,
        content=content,
    )

    return JsonResponse({
        'success': True,
        'comment': {
            'id': comment.id,
            'content': comment.content,
            'username': request.user.username,
            'likes': 0,
            'dislikes': 0,
            'created_at': comment.created_at.strftime('%b %d, %Y'),
        },
    })


@require_POST
def like_poll_comment(request):
    """Toggle like/dislike on a poll comment."""
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required'}, status=401)

    try:
        data = json.loads(request.body)
        comment_id = data.get('comment_id')
        reaction_type = data.get('reaction', 'like')
    except (json.JSONDecodeError, TypeError):
        return JsonResponse({'error': 'Invalid request'}, status=400)

    comment = get_object_or_404(PollComment, id=comment_id)

    if comment.user_id == request.user.id:
        return JsonResponse({'error': 'Cannot react to your own comment.'}, status=403)

    reaction, created = PollCommentReaction.objects.get_or_create(
        comment=comment, user=request.user, defaults={'reaction': reaction_type}
    )
    if not created and reaction.reaction != reaction_type:
        reaction.reaction = reaction_type
        reaction.save(update_fields=['reaction', 'updated_at'])

    likes = PollCommentReaction.objects.filter(comment=comment, reaction='like').count()
    dislikes = PollCommentReaction.objects.filter(comment=comment, reaction='dislike').count()
    comment.likes = likes
    comment.dislikes = dislikes
    comment.save(update_fields=['likes', 'dislikes', 'updated_at'])

    return JsonResponse({'success': True, 'likes': likes, 'dislikes': dislikes})
