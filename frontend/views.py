from django.conf import settings
from django.contrib.auth.models import User
from users.models import Profile
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login, authenticate, logout
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
import requests as _http_requests
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.core.cache import cache
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.utils.http import url_has_allowed_host_and_scheme
from django.db.models import Q, F, Count, Max, IntegerField, ExpressionWrapper
from django.db import DataError, IntegrityError
from django.utils import timezone
from datetime import timedelta
import json
import random
import re
import uuid

from discussions.models import CATEGORY_CHOICES, STOCK_CATEGORY_CHOICES, REVIEW_TYPE_CHOICES, Post, Comment, Debate, DebateMessage, DebateParticipant, CommentModeratorBlock, CommentReaction, PostFollow, PostView, PostAction, Notification, PostEditHistory, CommentEditHistory, DebateMessageEditHistory, DebateMessageReaction, DebateMessageReport, ProfileReport, Poll, PollOption, PollVote, PollComment, PollCommentReaction, Question, Answer, AnswerVote, Review, ReviewReaction, ReviewComment, ReviewCommentReaction, PollAction, PollFollow, QuestionAction, QuestionFollow, ReviewAction, ReviewFollow, ObserverVote, CommentReport, HashtagFollow, DebateView, PostSeries, PostSeriesItem, Story, StoryView, FeedScore, PostInsight, ReadLater, DirectMessage, LinkPreview, DMRequest, PostReport, LiveDebateRoom, LiveDebateMessage, LiveDebateVote, PollPrediction, StockPrediction
from discussions.signals import notify_post_author
from users.models import Follow, UserBlock, UserMute, SaveCollection, CollectionItem, MutedKeyword, Achievement, ACHIEVEMENT_DEFS, DEFAULT_NOTIFICATION_PREFS, CloseFriend, UserSuggestion, PushSubscription, UserBan, ProfileView, FollowRequest, UserList
from users.security import is_login_rate_limited, record_login_attempt
from discussions.limits import has_reached_daily_post_limit
from utils.moderation import check_content_moderation
import markdown as _markdown
import bleach as _bleach
from django.utils.safestring import mark_safe


_EMOJI_TOKEN_RE = re.compile(r'__EMJ__([0-9A-F]{5,6})__')
_OPEN_ENDED_START_RE = re.compile(r'^(what|why|how|when|where|which|who|whom|whose)\b', re.IGNORECASE)


import datetime as _dt

def _update_streak(profile):
    """Advance or protect a user's activity streak. Called whenever they post or comment.

    Rules:
    - Same day as last_activity_date → no change.
    - Consecutive day → increment streak.
    - Missed exactly 1 day AND grace not used this week → activate grace, keep streak.
    - Anything else → reset streak to 1.
    """
    today = _dt.date.today()
    last = profile.last_activity_date

    if last is None or last == today:
        # First activity ever, or already counted today
        profile.last_activity_date = today
        if profile.streak_days == 0:
            profile.streak_days = 1
        profile.save(update_fields=['last_activity_date', 'streak_days'])
        return

    delta = (today - last).days

    if delta == 1:
        # Consecutive day
        profile.streak_days += 1
        profile.last_activity_date = today
        profile.save(update_fields=['streak_days', 'last_activity_date'])
    elif delta == 2:
        # Missed exactly one day — check grace
        grace_used = profile.streak_grace_used_at
        week_ago = today - _dt.timedelta(days=7)
        grace_available = grace_used is None or grace_used < week_ago
        if grace_available:
            # Grace absorbs the missed day; streak continues
            profile.streak_grace_used_at = today - _dt.timedelta(days=1)
            profile.streak_days += 1
            profile.last_activity_date = today
            profile.save(update_fields=['streak_days', 'last_activity_date', 'streak_grace_used_at'])
        else:
            profile.streak_days = 1
            profile.last_activity_date = today
            profile.save(update_fields=['streak_days', 'last_activity_date'])
    else:
        # Gap too large — reset
        profile.streak_days = 1
        profile.last_activity_date = today
        profile.save(update_fields=['streak_days', 'last_activity_date'])


def _notif_pref(user, notif_type, channel):
    """Return True if user has opted in to the given notification type/channel."""
    try:
        prefs = user.profile.notification_prefs or {}
    except Exception:
        prefs = {}
    type_prefs = prefs.get(notif_type) or DEFAULT_NOTIFICATION_PREFS.get(notif_type) or {}
    default_type_prefs = DEFAULT_NOTIFICATION_PREFS.get(notif_type) or {}
    if channel in type_prefs:
        return bool(type_prefs[channel])
    return bool(default_type_prefs.get(channel, True))


def _in_quiet_hours(profile):
    """Return True if current local time falls within the user's configured quiet hours."""
    if not profile or not profile.quiet_hours_start or not profile.quiet_hours_end:
        return False
    now_time = timezone.localtime(timezone.now()).time().replace(second=0, microsecond=0)
    start = profile.quiet_hours_start
    end = profile.quiet_hours_end
    if start <= end:
        return start <= now_time < end
    # Overnight range e.g. 22:00 – 08:00
    return now_time >= start or now_time < end


def _send_notification_email(user, subject, body, notif_type='mention'):
    """Fire-and-forget notification email; skips silently if no address, opted out, or quiet hours."""
    if not getattr(user, 'email', None):
        return
    if not _notif_pref(user, notif_type, 'email'):
        return
    try:
        profile = user.profile
    except Exception:
        profile = None
    if _in_quiet_hours(profile):
        return
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=True)
    except Exception:
        pass


def _check_new_ip_login(request, user):
    """Send a security alert email when a user logs in from an IP address we haven't seen before."""
    if not getattr(user, 'email', None):
        return
    from users.security import client_ip
    from users.models import LoginAttempt
    ip = client_ip(request)
    ip_count = LoginAttempt.objects.filter(username=user.username, ip_address=ip, successful=True).count()
    total_count = LoginAttempt.objects.filter(username=user.username, successful=True).count()
    if ip_count == 1 and total_count > 1:
        user_agent = request.META.get('HTTP_USER_AGENT', 'Unknown')[:200]
        subject = 'New sign-in to your PickASide account'
        body = (
            f'Hi {user.username},\n\n'
            f'We noticed a sign-in to your PickASide account from a new location.\n\n'
            f'IP address: {ip}\n'
            f'Device: {user_agent}\n'
            f'Time: {timezone.now().strftime("%Y-%m-%d %H:%M UTC")}\n\n'
            f'If this was you, you can ignore this email.\n'
            f'If you did not sign in, please change your password immediately:\n'
            f'{settings.SITE_URL}/account/password-change/\n\n'
            f'You can also review recent sign-ins at:\n'
            f'{settings.SITE_URL}/account/login-activity/\n\n'
            f'— The PickASide team'
        )
        try:
            send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=True)
        except Exception:
            pass


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


PRE_JOIN_LIMIT = 5  # max participants per side while a debate is still pending


def _pending_side_counts(debate):
    rows = (DebateParticipant.objects
            .filter(debate=debate, is_active=True)
            .values('side').annotate(n=Count('id')))
    counts = {r['side']: r['n'] for r in rows}
    return counts.get('yes', 0), counts.get('no', 0)


def _can_pre_join(yes_count, no_count, side):
    """True when a new participant may join `side` on a pending debate.
    Each side can be at most 1 ahead of the other, up to PRE_JOIN_LIMIT."""
    my_count = yes_count if side == 'yes' else no_count
    other_count = no_count if side == 'yes' else yes_count
    return my_count <= other_count and my_count < PRE_JOIN_LIMIT


def _debate_primary_sides(debate):
    if debate.poll_comment_id:
        return 'no', 'yes'  # initiator='no', target='yes' by convention for polls
    if debate.review_comment_id:
        _side_map = {'agree': 'yes', 'disagree': 'no'}
        target_side = _side_map.get(debate.review_comment.side, 'yes')
        return _opposite_side(target_side), target_side
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


def post_reaction_users(request, post_id):
    action = request.GET.get('action', '')
    valid = ['hot', 'debatable', 'agree', 'surprising', 'like']
    if action not in valid:
        return JsonResponse({'success': False, 'error': 'Invalid action'}, status=400)
    post = get_object_or_404(Post, id=post_id)
    actions = PostAction.objects.filter(post=post, action=action).select_related('user', 'user__profile').order_by('-created_at')[:50]
    users = []
    for a in actions:
        p = getattr(a.user, 'profile', None)
        users.append({
            'username': a.user.username,
            'avatar_url': p.get_picture_url if p else '',
        })
    return JsonResponse({'success': True, 'users': users, 'action': action})


@login_required
def _post_action_counts(post):
    rows = PostAction.objects.filter(post=post).values('action').annotate(n=Count('id'))
    result = {row['action']: row['n'] for row in rows}
    return {
        'like': result.get('like', 0),
        'save': result.get('save', 0),
        'repost': result.get('repost', 0),
        'hot': result.get('hot', 0),
        'debatable': result.get('debatable', 0),
        'agree': result.get('agree', 0),
        'surprising': result.get('surprising', 0),
    }


_LIKE_MILESTONES = [10, 50, 100, 500]

def _check_like_milestone(post):
    like_count = PostAction.objects.filter(post=post, action='like').count()
    if like_count not in _LIKE_MILESTONES:
        return
    message = f'Your post "{post.title[:60]}" reached {like_count} likes!'
    Notification.objects.get_or_create(
        user=post.user,
        notification_type='author_like_milestone',
        message=message,
    )


@login_required
@require_POST
def post_action(request, post_id):
    action = request.POST.get('action')
    if action not in ['like', 'save', 'repost', 'hot', 'debatable', 'agree', 'surprising']:
        return JsonResponse({'success': False, 'error': 'Invalid action.'}, status=400)

    post = get_object_or_404(Post, id=post_id)

    if post.user == request.user and action in ['like', 'hot', 'debatable', 'agree', 'surprising']:
        return JsonResponse({'success': False, 'error': 'Cannot react to your own post.'}, status=400)

    if action in ['hot', 'debatable', 'agree', 'surprising']:
        existing_action = PostAction.objects.filter(user=request.user, post=post, action=action).first()
        if existing_action:
            existing_action.delete()
            status = 'removed'
        else:
            try:
                PostAction.objects.create(user=request.user, post=post, action=action)
                status = 'added'
            except IntegrityError:
                status = 'added'

        return JsonResponse({
            'success': True,
            'action': action,
            'status': status,
            'counts': _post_action_counts(post),
        })

    if action in ['like', 'save']:
        existing_action = PostAction.objects.filter(user=request.user, post=post, action=action).first()
        if existing_action:
            existing_action.delete()
            status = 'removed'
        else:
            try:
                PostAction.objects.create(user=request.user, post=post, action=action)
                status = 'added'
                if action == 'like':
                    _check_like_milestone(post)
                if action == 'save':
                    notify_post_author(post, 'author_save', request.user)
            except IntegrityError:
                # If two add requests race, keep it liked/saved instead of crashing.
                status = 'added'

        return JsonResponse({
            'success': True,
            'action': action,
            'status': status,
            'counts': _post_action_counts(post),
        })

    if action == 'repost':
        existing_action = PostAction.objects.filter(user=request.user, post=post, action=action).first()
        if existing_action:
            existing_action.delete()
            _remove_repost_copy_for_user(request.user, post)

            return JsonResponse({
                'success': True,
                'action': action,
                'status': 'removed',
                'counts': _post_action_counts(post),
            })

        quote_content = request.POST.get('quote_content', '').strip()[:500]
        try:
            PostAction.objects.create(user=request.user, post=post, action=action, quote_content=quote_content)
            notify_post_author(post, 'author_repost', request.user)
        except IntegrityError:
            return JsonResponse({
                'success': True,
                'action': action,
                'status': 'added',
                'counts': _post_action_counts(post),
            })
        new_post = Post.objects.create(
            id=str(uuid.uuid4()),
            user=request.user,
            title=post.title,
            content=post.content,
            category=post.category,
            hashtags=post.hashtags,
        )

        return JsonResponse({
            'success': True,
            'action': action,
            'status': 'reposted',
            'repost_id': new_post.id,
            'quote_content': quote_content,
            'counts': _post_action_counts(post),
        })


def _content_action_toggle(request, model_cls, follow_cls, obj_field, obj):
    """Generic toggle handler for like/save/repost/follow on any content type."""
    action = request.POST.get('action')
    if action not in ('like', 'save', 'repost', 'follow'):
        return JsonResponse({'success': False, 'error': 'Invalid action.'}, status=400)

    if action == 'follow':
        existing = follow_cls.objects.filter(user=request.user, **{obj_field: obj}).first()
        if existing:
            existing.delete()
            status = 'removed'
        else:
            follow_cls.objects.create(user=request.user, **{obj_field: obj})
            status = 'added'
        count = follow_cls.objects.filter(**{obj_field: obj}).count()
        return JsonResponse({'success': True, 'action': 'follow', 'status': status, 'count': count})

    existing = model_cls.objects.filter(user=request.user, action=action, **{obj_field: obj}).first()
    if existing:
        existing.delete()
        status = 'removed'
    else:
        try:
            model_cls.objects.create(user=request.user, action=action, **{obj_field: obj})
            status = 'added'
        except IntegrityError:
            status = 'added'

    count = model_cls.objects.filter(action=action, **{obj_field: obj}).count()
    return JsonResponse({'success': True, 'action': action, 'status': status, 'count': count})


@login_required
@require_POST
def poll_action(request, poll_id):
    poll = get_object_or_404(Poll, id=poll_id)
    return _content_action_toggle(request, PollAction, PollFollow, 'poll', poll)


@login_required
@require_POST
def question_action(request, question_id):
    question = get_object_or_404(Question, id=question_id)
    return _content_action_toggle(request, QuestionAction, QuestionFollow, 'question', question)


@login_required
@require_POST
def review_action(request, review_id):
    review = get_object_or_404(Review, id=review_id)
    return _content_action_toggle(request, ReviewAction, ReviewFollow, 'review', review)


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
    base_posts = Post.objects.filter(id__isnull=False, is_draft=False).exclude(id='')
    return base_posts.annotate(
        like_count=Count('actions', filter=Q(actions__action='like'), distinct=True),
        save_count=Count('actions', filter=Q(actions__action='save'), distinct=True),
        repost_count=Count('actions', filter=Q(actions__action='repost'), distinct=True),
        hot_count=Count('actions', filter=Q(actions__action='hot'), distinct=True),
        debatable_count=Count('actions', filter=Q(actions__action='debatable'), distinct=True),
        agree_count=Count('actions', filter=Q(actions__action='agree'), distinct=True),
        surprising_count=Count('actions', filter=Q(actions__action='surprising'), distinct=True),
        yes_count=Count('comments', filter=Q(comments__vote_type='yes'), distinct=True),
        no_count=Count('comments', filter=Q(comments__vote_type='no'), distinct=True),
        comment_count=Count('comments', distinct=True),
        conversation_count=Count('debates', distinct=True),
        author_posts_count=Count('user__posts', distinct=True),
        view_count=Count('views', distinct=True),
    ).select_related('user', 'user__profile')


def _enrich_posts_for_feed(posts, user):
    _auth_user_id = getattr(user, 'id', None) if getattr(user, 'is_authenticated', False) else None
    for post in posts:
        yes_count = getattr(post, 'yes_count', 0) or 0
        no_count = getattr(post, 'no_count', 0) or 0
        post.author_avatar = _safe_avatar_url(post.user)
        # Anonymous masking — hide identity unless it's your own post or you're a moderator
        if post.is_anonymous and post.user_id != _auth_user_id:
            post.display_username = 'Anonymous'
            post.display_avatar = None
        else:
            post.display_username = post.user.username
            post.display_avatar = post.author_avatar
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
    hot_post_ids = set()
    debatable_post_ids = set()
    agree_post_ids = set()
    surprising_post_ids = set()
    if post_ids:
        post_actions = PostAction.objects.filter(
            user=user,
            post__in=post_ids,
            action__in=['like', 'save', 'repost', 'hot', 'debatable', 'agree', 'surprising']
        ).values('post_id', 'action')
        for item in post_actions:
            if item['action'] == 'like':
                liked_post_ids.add(item['post_id'])
            elif item['action'] == 'save':
                saved_post_ids.add(item['post_id'])
            elif item['action'] == 'repost':
                reposted_post_ids.add(item['post_id'])
            elif item['action'] == 'hot':
                hot_post_ids.add(item['post_id'])
            elif item['action'] == 'debatable':
                debatable_post_ids.add(item['post_id'])
            elif item['action'] == 'agree':
                agree_post_ids.add(item['post_id'])
            elif item['action'] == 'surprising':
                surprising_post_ids.add(item['post_id'])

    read_later_ids = set(
        ReadLater.objects.filter(user=user, post__in=post_ids).values_list('post_id', flat=True)
    ) if post_ids else set()

    author_ids = {post.user_id for post in posts if post.user_id != user.id}
    followed_author_ids = set(
        Follow.objects.filter(follower=user, following_id__in=author_ids).values_list('following_id', flat=True)
    ) if author_ids else set()

    for post in posts:
        post.is_liked = post.id in liked_post_ids
        post.is_saved = post.id in saved_post_ids
        post.is_reposted = post.id in reposted_post_ids
        post.is_hot = post.id in hot_post_ids
        post.is_debatable = post.id in debatable_post_ids
        post.is_agree = post.id in agree_post_ids
        post.is_surprising = post.id in surprising_post_ids
        post.is_read_later = post.id in read_later_ids
        post.is_followed_author = post.user_id in followed_author_ids


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


def _filter_muted_posts(posts, user):
    """Filter out posts that contain any of the user's muted keywords."""
    if not getattr(user, 'is_authenticated', False):
        return posts
    keywords = list(MutedKeyword.objects.filter(user=user).values_list('keyword', flat=True))
    if not keywords:
        return posts
    filtered = []
    for p in posts:
        text = f"{p.title} {p.content} {p.hashtags}".lower()
        if not any(kw.lower() in text for kw in keywords):
            filtered.append(p)
    return filtered


def _follow_suggestions(user, limit=5):
    """Return Profile objects the viewer might want to follow.

    Uses pre-computed UserSuggestion rows when available; falls back to a
    lightweight category-overlap scan so the sidebar is never empty.
    """
    if not user.is_authenticated:
        return []

    # Try pre-computed suggestions first
    precomputed = list(
        UserSuggestion.objects.filter(user=user)
        .select_related('suggested_user', 'suggested_user__profile')
        .order_by('-score')[:limit]
    )
    if precomputed:
        return [s.suggested_user.profile for s in precomputed]

    # Fallback: category overlap
    profile = getattr(user, 'profile', None)
    cats = list(profile.interested_categories or []) if profile else []
    already_following = set(
        Follow.objects.filter(follower=user).values_list('following_id', flat=True)
    )
    already_following.add(user.id)
    qs = Profile.objects.select_related('user').exclude(user_id__in=already_following)
    if cats:
        qs = qs.filter(
            interested_categories__isnull=False
        ).exclude(interested_categories=[])
    suggestions = []
    for p in qs.order_by('-reputation_score')[:50]:
        shared = len(set(p.interested_categories or []) & set(cats)) if cats else 0
        suggestions.append((shared, p))
    suggestions.sort(key=lambda x: -x[0])
    return [p for _, p in suggestions[:limit]]


def _blocked_user_ids(user):
    """Return set of user IDs to exclude: users the current user blocked and users who blocked them."""
    if not user.is_authenticated:
        return set()
    blocked = set(UserBlock.objects.filter(blocker=user).values_list('blocked_id', flat=True))
    blocking_me = set(UserBlock.objects.filter(blocked=user).values_list('blocker_id', flat=True))
    return blocked | blocking_me


def _muted_user_ids(user):
    """Return set of user IDs muted by the current user (posts hidden from feeds, one-way)."""
    if not user.is_authenticated:
        return set()
    return set(UserMute.objects.filter(muter=user).values_list('muted_id', flat=True))


def index(request):
    """Home page with trending posts and categories"""
    active_category = request.GET.get('category', '').strip()
    active_content_type = request.GET.get('content_type', '').strip()
    search_query = request.GET.get('q', '').strip()
    annotated_posts = _annotated_feed_posts_queryset()
    if active_category:
        annotated_posts = annotated_posts.filter(category=active_category)
    if active_content_type in ('discussion', 'stock_prediction'):
        annotated_posts = annotated_posts.filter(post_type=active_content_type)
    if search_query:
        annotated_posts = annotated_posts.filter(
            Q(title__icontains=search_query) | Q(content__icontains=search_query)
        )
    blocked_ids = _blocked_user_ids(request.user)
    muted_ids = _muted_user_ids(request.user)
    exclude_ids = blocked_ids | muted_ids
    if exclude_ids:
        annotated_posts = annotated_posts.exclude(user_id__in=exclude_ids)

    # Boost posts whose hashtags match the user's followed topics
    user_followed_tags = set()
    if request.user.is_authenticated:
        user_followed_tags = set(
            HashtagFollow.objects.filter(user=request.user).values_list('tag', flat=True)
        )
    if user_followed_tags:
        from django.db.models import BooleanField, Case, When, Value as _V
        _tag_q = Q()
        for _t in list(user_followed_tags)[:20]:
            _tag_q |= Q(hashtags__icontains=_t)
        annotated_posts = annotated_posts.annotate(
            has_followed_tag=Case(When(_tag_q, then=_V(True)), default=_V(False), output_field=BooleanField())
        ).order_by('-has_followed_tag', '-like_count', '-comment_count', '-conversation_count', '-author_posts_count', '-created_at')
    else:
        annotated_posts = annotated_posts.order_by(
            '-like_count', '-comment_count', '-conversation_count', '-author_posts_count', '-created_at',
        )

    paginator = Paginator(annotated_posts, 10)
    page_obj = paginator.get_page(request.GET.get('page'))
    posts = list(page_obj.object_list)
    _enrich_posts_for_feed(posts, request.user)
    posts = _filter_muted_posts(posts, request.user)

    # Stories bar: active (non-expired) stories from followed users + own
    stories_bar = []
    if request.user.is_authenticated:
        following_ids = list(
            Follow.objects.filter(follower=request.user).values_list('following_id', flat=True)
        )
        story_user_ids = following_ids + [request.user.id]
        now_ts = timezone.now()
        stories_bar = list(
            Story.objects.filter(user_id__in=story_user_ids, expires_at__gt=now_ts)
            .select_related('user')
            .order_by('-created_at')[:30]
        )
        viewed_ids = set(
            StoryView.objects.filter(viewer=request.user, story__in=stories_bar)
            .values_list('story_id', flat=True)
        )
        for s in stories_bar:
            s.viewer_has_seen = s.id in viewed_ids

    context = {
        'posts': posts,
        'page_obj': page_obj,
        'active_tab': 'trending',
        'categories': get_frontend_categories(),
        'active_category': active_category,
        'active_content_type': active_content_type,
        'search_query': search_query,
        'is_suggested_page': False,
        'follow_suggestions': _follow_suggestions(request.user),
        'trending_sidebar': _get_trending_hashtags(),
        'rising_creators': _get_rising_creators(limit=5),
        'user_followed_tags': user_followed_tags,
        'stories_bar': stories_bar,
    }
    if active_category and request.user.is_authenticated:
        from discussions.models import CategoryFollow as _CF
        context['user_follows_category'] = _CF.objects.filter(user=request.user, category=active_category).exists()
    else:
        context['user_follows_category'] = False
    return render(request, 'frontend/index.html', context)


@login_required
def suggested(request):
    """Redirects to For You feed — kept for backwards-compat with onboarding links."""
    from django.shortcuts import redirect
    return redirect('for_you_feed')

def category(request, category_name):
    """Category page showing posts in a specific category"""
    posts = Post.objects.filter(category=category_name).order_by('-created_at')

    context = {
        'category_name': category_name,
        'posts': posts,
    }
    return render(request, 'frontend/category.html', context)

def _get_related_posts(post, limit=4):
    """Return up to `limit` posts related to `post` by category and shared hashtags."""
    if not post.category:
        return []

    _cache_key = f'related_posts:{post.id}'
    cached = cache.get(_cache_key)
    if cached is not None:
        return cached

    candidates = (
        Post.objects.filter(category=post.category, is_draft=False)
        .exclude(id=post.id)
        .annotate(like_count=Count('actions', filter=Q(actions__action='like')))
        .select_related('user')
        [:50]
    )

    post_tags = set(
        t.lstrip('#').lower()
        for t in re.split(r'[\s,]+', post.hashtags or '')
        if t.strip()
    )

    def _score(p):
        if post_tags:
            p_tags = set(
                t.lstrip('#').lower()
                for t in re.split(r'[\s,]+', p.hashtags or '')
                if t.strip()
            )
            return len(post_tags & p_tags)
        return 0

    scored = sorted(candidates, key=lambda p: (_score(p), p.like_count), reverse=True)
    result = scored[:limit]
    cache.set(_cache_key, result, 300)
    return result


def discussion(request, post_id):
    """Discussion page for a specific post"""
    # Lightweight fetch: only the annotations the discussion page actually renders.
    # Avoids the 14 COUNT(DISTINCT…) subqueries that _annotated_feed_posts_queryset uses.
    post = (
        Post.objects
        .filter(id=post_id, is_draft=False)
        .exclude(id='')
        .select_related('user', 'user__profile')
        .annotate(
            like_count=Count('actions', filter=Q(actions__action='like'), distinct=True),
            save_count=Count('actions', filter=Q(actions__action='save'), distinct=True),
            repost_count=Count('actions', filter=Q(actions__action='repost'), distinct=True),
            hot_count=Count('actions', filter=Q(actions__action='hot'), distinct=True),
            debatable_count=Count('actions', filter=Q(actions__action='debatable'), distinct=True),
            agree_count=Count('actions', filter=Q(actions__action='agree'), distinct=True),
            surprising_count=Count('actions', filter=Q(actions__action='surprising'), distinct=True),
            comment_count=Count('comments', distinct=True),
            conversation_count=Count('debates', distinct=True),
            view_count=Count('views', distinct=True),
        )
        .first()
    )
    if not post:
        messages.error(request, 'This discussion is no longer available.')
        return redirect('index')

    # Audience access control — enforce followers/close_friends restrictions
    if post.audience != 'public' and request.user != post.user:
        if not request.user.is_authenticated:
            messages.error(request, 'You must be logged in to view this post.')
            return redirect('login')
        if post.audience == 'followers':
            is_following = post.user.follower_links.filter(follower=request.user).exists()
            if not is_following:
                messages.error(request, 'This post is only visible to followers.')
                return redirect('index')
        elif post.audience == 'close_friends':
            is_close_friend = post.user.close_friends_list.filter(friend=request.user).exists()
            if not is_close_friend:
                messages.error(request, 'This post is only visible to close friends.')
                return redirect('index')

    # Stock predictions have their own dedicated detail page.
    if post.post_type == Post.POST_TYPE_STOCK:
        return redirect('stock_prediction_detail', post_id=post_id)

    # Keep detail-page counters and action state in sync with home/suggested feeds.
    _enrich_posts_for_feed([post], request.user)

    is_post_creator = request.user == post.user
    if request.user.is_authenticated:
        PostView.objects.get_or_create(user=request.user, post=post)

    comment_sort = request.GET.get('sort', 'top')
    if comment_sort not in ('top', 'newest', 'oldest'):
        comment_sort = 'top'

    comments = Comment.objects.filter(post=post).select_related('user', 'user__profile', 'reply_to', 'reply_to__user').annotate(
        reaction_score=ExpressionWrapper(F('likes') - F('dislikes'), output_field=IntegerField())
    )

    yes_vote_count = comments.filter(vote_type='yes').count()
    no_vote_count = comments.filter(vote_type='no').count()

    visible_comments = comments.exclude(content='')
    if comment_sort == 'newest':
        _comment_order = ('-created_at',)
    elif comment_sort == 'oldest':
        _comment_order = ('created_at',)
    else:
        _comment_order = ('-reaction_score', '-likes', 'created_at')
    _COMMENT_PAGE_SIZE = 75
    yes_qs = visible_comments.filter(vote_type='yes').order_by(*_comment_order)
    no_qs  = visible_comments.filter(vote_type='no').order_by(*_comment_order)

    # Query top comments before slicing (cannot filter a sliced queryset)
    top_yes_comment = yes_qs.filter(reaction_score__gt=0).first()
    top_no_comment  = no_qs.filter(reaction_score__gt=0).first()

    yes_comments = yes_qs[:_COMMENT_PAGE_SIZE]
    no_comments  = no_qs[:_COMMENT_PAGE_SIZE]

    # Pinned comment for this post (only one can be pinned at a time)
    pinned_comment = Comment.objects.filter(post=post, is_pinned=True).select_related('user', 'user__profile').first()

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

        # Pending debates — show "Join Debate" to everyone except the target
        # (accepted debates already in debate_lookup take priority)
        pending_debates_qs = list(
            Debate.objects.filter(
                post=post,
                target_id__in=visible_comment_owners,
                status='pending',
            ).order_by('target_id', '-created_at')
        )
        if pending_debates_qs:
            pending_ids = [d.id for d in pending_debates_qs]
            user_prejoined_set = set(
                DebateParticipant.objects.filter(
                    debate_id__in=pending_ids,
                    user=request.user,
                ).values_list('debate_id', flat=True)
            )
            pending_counts = {
                (row['debate_id'], row['side']): row['total']
                for row in DebateParticipant.objects.filter(
                    debate_id__in=pending_ids,
                    is_active=True,
                ).values('debate_id', 'side').annotate(total=Count('id'))
            }
            pending_by_target = {}
            for d in pending_debates_qs:
                if d.target_id not in pending_by_target:
                    pending_by_target[d.target_id] = d

            for target_id, debate in pending_by_target.items():
                if target_id in debate_lookup:
                    continue  # accepted debate already there
                yes_pre = pending_counts.get((debate.id, 'yes'), 0)
                no_pre  = pending_counts.get((debate.id, 'no'),  0)
                already_in = debate.id in user_prejoined_set or debate.initiator_id == request.user.id
                if already_in:
                    mode  = 'waiting'
                    label = 'Waiting…'
                else:
                    mode  = 'join'
                    label = 'Join Debate'
                debate_lookup[target_id] = {
                    'id': debate.id,
                    'mode': mode,
                    'label': label,
                    'chat_url': f'/debates/{debate.id}/chat/',
                    'pre_join_yes': yes_pre,
                    'pre_join_no':  no_pre,
                }

    for comment in yes_comments:
        debate_state = debate_lookup.get(comment.user_id)
        completed_state = completed_lookup.get(comment.user_id) if request.user.is_authenticated else None
        comment.debate_action_mode = debate_state['mode'] if debate_state else 'start'
        comment.debate_action_label = debate_state['label'] if debate_state else 'Start Debate'
        comment.debate_chat_url = debate_state['chat_url'] if debate_state else ''
        comment.debate_pre_join_yes = debate_state.get('pre_join_yes') if debate_state else None
        comment.debate_pre_join_no  = debate_state.get('pre_join_no')  if debate_state else None
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
        comment.debate_pre_join_yes = debate_state.get('pre_join_yes') if debate_state else None
        comment.debate_pre_join_no  = debate_state.get('pre_join_no')  if debate_state else None
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

    post_has_comments = bool(yes_vote_count or no_vote_count)
    can_manage_post_today = is_post_creator and _can_manage_created_today(request.user, post.created_at)
    show_post_submitted = is_post_creator and request.GET.get('created') == '1'

    is_following_post = False
    user_has_reported_post = False
    if request.user.is_authenticated:
        # Batch three per-user boolean checks into two queries instead of three.
        _user_post_flags = PostAction.objects.filter(
            user=request.user, post=post
        ).values_list('action', flat=True)
        is_following_post = PostFollow.objects.filter(user=request.user, post=post).exists()
        user_has_reported_post = PostReport.objects.filter(post=post, reporter=request.user).exists()

    _views_cache_key = f'post_views_count:{post_id}'
    views_count = cache.get(_views_cache_key)
    if views_count is None:
        views_count = PostView.objects.filter(post=post).count()
        cache.set(_views_cache_key, views_count, 120)

    if is_post_creator:
        from django.db.models.functions import TruncDate
        daily_views = list(
            PostView.objects.filter(post=post, viewed_at__gte=timezone.now() - timedelta(days=7))
            .annotate(day=TruncDate('viewed_at'))
            .values('day')
            .annotate(count=Count('id'))
            .order_by('day')
        )
        unique_viewers = PostView.objects.filter(post=post).values('user').distinct().count()
        like_count = PostAction.objects.filter(post=post, action='like').count()
        save_count = PostAction.objects.filter(post=post, action='save').count()
        repost_count = PostAction.objects.filter(post=post, action='repost').count()
        # comment velocity: comments in last 24h vs previous 24h
        now = timezone.now()
        recent_comments = Comment.objects.filter(post=post, created_at__gte=now - timedelta(hours=24)).count()
        prev_comments = Comment.objects.filter(
            post=post,
            created_at__gte=now - timedelta(hours=48),
            created_at__lt=now - timedelta(hours=24),
        ).count()
        velocity_delta = recent_comments - prev_comments

        analytics = {
            'total_views': views_count,
            'unique_viewers': unique_viewers,
            'daily_views': [{'day': str(d['day']), 'count': d['count']} for d in daily_views],
            'yes_pct': round(yes_percentage, 1),
            'no_pct': round(no_percentage, 1),
            'like_count': like_count,
            'save_count': save_count,
            'repost_count': repost_count,
            'recent_comments': recent_comments,
            'velocity_delta': velocity_delta,
            'max_daily_views': max((d['count'] for d in daily_views), default=1),
        }
    else:
        analytics = None

    # Series membership for navigator
    series_context = None
    series_item = post.series_items.select_related('series').first()
    if series_item:
        all_items = list(series_item.series.items.select_related('post').order_by('order'))
        current_idx = next((i for i, it in enumerate(all_items) if it.post_id == post.id), None)
        series_context = {
            'series': series_item.series,
            'items': all_items,
            'current_idx': current_idx,
            'prev_item': all_items[current_idx - 1] if current_idx and current_idx > 0 else None,
            'next_item': all_items[current_idx + 1] if current_idx is not None and current_idx < len(all_items) - 1 else None,
        }

    from discussions.models import PostReminder as _PR
    post.has_reminder = (
        request.user.is_authenticated
        and _PR.objects.filter(user=request.user, post=post, is_sent=False).exists()
    )

    # Accepted co-authors for display
    from discussions.models import PostCoAuthor as _PCADisc
    accepted_coauthors = list(
        _PCADisc.objects.filter(post=post, accepted=True)
        .select_related('user').order_by('created_at')
    )

    context = {
        'post': post,
        'accepted_coauthors': accepted_coauthors,
        'post_display_content': _render_markdown(_normalize_post_content(post.content)),
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
        'is_moderator': _is_configured_moderator(request.user) if request.user.is_authenticated else False,
        'reveal_identity': is_post_creator or (_is_configured_moderator(request.user) if request.user.is_authenticated else False),
        'show_post_submitted': show_post_submitted,
        'can_edit_post': can_manage_post_today and not post_has_comments,
        'can_delete_post': can_manage_post_today,
        'post_has_comments': post_has_comments,
        'post_change_locked_message': 'This account can only edit or delete posts created today.' if is_post_creator and not can_manage_post_today else '',
        'pinned_comment': pinned_comment,
        'top_yes_comment_id': top_yes_comment.id if top_yes_comment else '',
        'top_no_comment_id': top_no_comment.id if top_no_comment else '',
        'is_following_post': is_following_post,
        'views_count': views_count,
        'analytics': analytics,
        'related_posts': _get_related_posts(post),
        'series_context': series_context,
        'comment_sort': comment_sort,
        'post_report_reasons': PostReport.REASON_CHOICES,
        'user_has_reported_post': user_has_reported_post,
    }
    return render(request, 'frontend/discussion.html', context)

@login_required
def profile(request):
    """User profile page"""
    user_posts = Post.objects.filter(user=request.user, is_draft=False).exclude(id='').order_by('-is_pinned', '-created_at')
    user_drafts = Post.objects.filter(user=request.user, is_draft=True).exclude(id='').order_by('-created_at')
    pinned_post = Post.objects.filter(user=request.user, is_pinned=True, is_draft=False).first()
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
    profile_picture_url = (
        profile_obj.profile_picture.url
        if profile_obj and profile_obj.profile_picture
        and profile_obj.profile_picture.storage.exists(profile_obj.profile_picture.name)
        else ''
    )
    profile_last_seen = profile_obj.last_seen if profile_obj else None
    now = timezone.now()
    is_online = bool(profile_last_seen and profile_last_seen >= now - timedelta(minutes=5))
    presence_label = 'Active now' if is_online else _presence_label(profile_last_seen, now=now)

    followers_qs = request.user.follower_links.select_related('follower__profile').order_by('-created_at')
    following_qs = request.user.following_links.select_related('following__profile').order_by('-created_at')

    user_reviews = Review.objects.filter(user=request.user, is_deleted_by_moderation=False).order_by('-created_at')
    user_questions = Question.objects.filter(user=request.user, is_deleted_by_moderation=False).order_by('-created_at')
    user_polls = Poll.objects.filter(user=request.user).order_by('-created_at')
    user_answers = Answer.objects.filter(user=request.user, is_deleted_by_moderation=False).select_related('question').order_by('-created_at')
    user_stock_predictions = StockPrediction.objects.filter(post__user=request.user).select_related('post').order_by('-post__created_at')

    def _card(u):
        p = getattr(u, 'profile', None)
        return {'username': u.username, 'avatar_url': p.get_picture_url if p else ''}

    debate_participations_count = user_debate_participations.count()
    completed_debates = user_debate_participations.filter(debate__status='completed').count()

    save_collections = list(
        SaveCollection.objects.filter(user=request.user).annotate(
            item_count=Count('items')
        ).order_by('name')
    )

    muted_keywords = list(MutedKeyword.objects.filter(user=request.user).values_list('keyword', flat=True))

    user_series = list(PostSeries.objects.filter(user=request.user).annotate(post_count=Count('items')))

    user_achievements = list(Achievement.objects.filter(user=request.user).values_list('code', flat=True))
    user_achievements_set = set(user_achievements)
    achievement_details = [
        {'code': d[0], 'icon': d[1], 'label': d[2], 'desc': d[3]}
        for d in ACHIEVEMENT_DEFS if d[0] in user_achievements_set
    ]

    # Activity heatmap: count posts+comments per day for last 364 days (cached 1 hour)
    from django.core.cache import cache as _cache
    from django.db.models.functions import TruncDate as _TruncDate
    import json as _json
    import datetime as _dt
    _heatmap_cache_key = f'heatmap:{request.user.id}'
    activity_heatmap_json = _cache.get(_heatmap_cache_key)
    if activity_heatmap_json is None:
        _heatmap_start = timezone.now().date() - timedelta(days=363)
        _post_counts = {
            str(r['day']): r['n']
            for r in Post.objects.filter(
                user=request.user, is_draft=False,
                created_at__date__gte=_heatmap_start
            ).annotate(day=_TruncDate('created_at')).values('day').annotate(n=Count('id'))
        }
        _comment_counts = {
            str(r['day']): r['n']
            for r in Comment.objects.filter(
                user=request.user,
                created_at__date__gte=_heatmap_start
            ).annotate(day=_TruncDate('created_at')).values('day').annotate(n=Count('id'))
        }
        _all_days = {}
        _d = _heatmap_start
        while _d <= timezone.now().date():
            key = str(_d)
            _all_days[key] = _post_counts.get(key, 0) + _comment_counts.get(key, 0)
            _d += _dt.timedelta(days=1)
        activity_heatmap_json = _json.dumps(_all_days)
        _cache.set(_heatmap_cache_key, activity_heatmap_json, 3600)

    blocked_users = list(
        UserBlock.objects.filter(blocker=request.user)
        .select_related('blocked', 'blocked__profile')
        .order_by('-created_at')
    )

    # Profile completion score
    _completion_steps = [
        ('Avatar', bool(profile_obj and profile_obj.profile_picture)),
        ('Bio', bool(profile_obj and (profile_obj.bio or '').strip())),
        ('Website', bool(profile_obj and (profile_obj.website or '').strip())),
        ('Interests', bool(profile_obj and profile_obj.interested_categories)),
        ('First Post', Post.objects.filter(user=request.user, is_draft=False).exists()),
        ('First Debate', user_debate_participations.exists()),
        ('First Poll', user_polls.exists()),
        ('Verified', bool(profile_obj and profile_obj.is_verified)),
    ]
    completion_steps = _completion_steps
    completion_score = sum(1 for _, done in _completion_steps if done)
    completion_total = len(_completion_steps)
    completion_pct = round(completion_score / completion_total * 100)

    # Reputation score: likes received + 2× best answers
    _likes_rx = PostAction.objects.filter(post__user=request.user, action='like').count()
    computed_reputation = _likes_rx
    if profile_obj and profile_obj.reputation_score != computed_reputation:
        try:
            profile_obj.reputation_score = computed_reputation
            profile_obj.save(update_fields=['reputation_score'])
        except Exception:
            pass

    # Streak: consecutive days of activity
    import datetime as _dt
    _today = timezone.now().date()
    current_streak = profile_obj.streak_days if profile_obj else 0

    # Post reactions feed (emoji reactions the user gave)
    user_reactions_feed = list(
        PostAction.objects.filter(
            user=request.user, action__in=['hot', 'debatable', 'agree', 'surprising']
        ).select_related('post', 'post__user').order_by('-created_at')[:50]
    )

    # Content calendar: posts per day for last 60 days + scheduled
    from django.db.models.functions import TruncDate as _TruncDate2
    _cal_start = _today - _dt.timedelta(days=59)
    _cal_counts = {
        str(r['day']): r['n']
        for r in Post.objects.filter(
            user=request.user, is_draft=False, created_at__date__gte=_cal_start
        ).annotate(day=_TruncDate2('created_at')).values('day').annotate(n=Count('id'))
    }
    _sched = list(
        Post.objects.filter(user=request.user, is_draft=True, scheduled_for__isnull=False)
        .values_list('scheduled_for__date', 'title')
    )
    content_calendar_json = json.dumps({
        'counts': {str(k): v for k, v in _cal_counts.items()},
        'scheduled': [[str(d), t] for d, t in _sched],
    })

    # Endorsements received (grouped by topic)
    from users.models import Endorsement as _Endorsement
    try:
        _endorsements = list(
            _Endorsement.objects.filter(endorsed=request.user)
            .select_related('endorser').order_by('topic', '-created_at')
        )
        _etopics = {}
        for e in _endorsements:
            _etopics.setdefault(e.topic, []).append(e.endorser.username)
        endorsements_by_topic = [
            {'topic': t, 'endorsers': names, 'count': len(names)}
            for t, names in _etopics.items()
        ]
    except Exception:
        endorsements_by_topic = []

    # Category follows
    from discussions.models import CategoryFollow as _CatFollow
    try:
        followed_categories = list(
            _CatFollow.objects.filter(user=request.user).values_list('category', flat=True)
        )
    except Exception:
        followed_categories = []

    # Co-authoring: drafts where user is invited as co-author
    from discussions.models import PostCoAuthor as _PCA
    try:
        coauthor_invites = list(
            _PCA.objects.filter(user=request.user, accepted__isnull=True)
            .select_related('post', 'invited_by').order_by('-created_at')
        )
    except Exception:
        coauthor_invites = []

    # Cross-post analytics summary for the Analytics tab
    from discussions.models import PostInsight as _PI
    _pub_post_ids = list(
        Post.objects.filter(user=request.user, is_draft=False)
        .values_list('id', flat=True)
    )
    _total_views = PostView.objects.filter(post_id__in=_pub_post_ids).count()
    _total_likes = PostAction.objects.filter(post_id__in=_pub_post_ids, action='like').count()
    _total_saves = PostAction.objects.filter(post_id__in=_pub_post_ids, action='save').count()
    _total_comments = Comment.objects.filter(post_id__in=_pub_post_ids).count()
    _total_engagements = _total_likes + _total_saves + _total_comments
    _engagement_rate = round(_total_engagements / _total_views * 100, 1) if _total_views > 0 else 0.0

    # Top 5 posts by view count
    _top_posts = list(
        Post.objects.filter(user=request.user, is_draft=False)
        .annotate(
            view_count=Count('views', distinct=True),
            like_count=Count('actions', filter=Q(actions__action='like'), distinct=True),
        )
        .order_by('-view_count')[:5]
    )

    # 30-day daily view trend across all posts
    _thirty_ago = timezone.now().date() - timedelta(days=29)
    _daily_views_qs = (
        _PI.objects.filter(post_id__in=_pub_post_ids, date__gte=_thirty_ago)
        .values('date')
        .annotate(total=Count('unique_viewers'))
        .order_by('date')
    )
    _daily_map = {str(r['date']): r['total'] for r in _daily_views_qs}
    import datetime as _adt2
    _analytics_daily = []
    for _i in range(29, -1, -1):
        _d = (timezone.now() - timedelta(days=_i)).date()
        _analytics_daily.append({'date': _d.strftime('%b %d'), 'views': _daily_map.get(str(_d), 0)})

    _analytics_summary = {
        'total_views': _total_views,
        'total_likes': _total_likes,
        'total_saves': _total_saves,
        'total_comments': _total_comments,
        'engagement_rate': _engagement_rate,
        'top_posts': _top_posts,
        'daily_json': json.dumps(_analytics_daily),
    }

    context = {
        'user_posts': user_posts,
        'user_reposts': user_reposts,
        'user_debate_participations': user_debate_participations,
        'user_saved': user_saved,
        'user_followed_posts': user_followed_posts,
        'user_reviews': user_reviews,
        'user_questions': user_questions,
        'user_polls': user_polls,
        'user_answers': user_answers,
        'user_stock_predictions': user_stock_predictions,
        'avatar_url': avatar_url,
        'profile_picture_url': profile_picture_url,
        'is_online': is_online,
        'presence_label': presence_label,
        'followers_count': request.user.follower_links.count(),
        'following_count': request.user.following_links.count(),
        'followers_list': [_card(f.follower) for f in followers_qs],
        'following_list': [_card(f.following) for f in following_qs],
        'profile_bio': profile_obj.bio if profile_obj else '',
        'profile_website': profile_obj.website if profile_obj else '',
        'debate_participations_count': debate_participations_count,
        'completed_debates_count': completed_debates,
        'save_collections': save_collections,
        'trust_badge': profile_obj.trust_badge if profile_obj else ('', '', ''),
        'trust_level': profile_obj.trust_level if profile_obj else 'new',
        'user_drafts': user_drafts,
        'pinned_post': pinned_post,
        'muted_keywords': muted_keywords,
        'user_series': user_series,
        'user_achievements': user_achievements,
        'achievement_details': achievement_details,
        'notification_prefs': profile_obj.notification_prefs if profile_obj else {},
        'default_notification_prefs': DEFAULT_NOTIFICATION_PREFS,
        'notification_prefs_json': json.dumps(profile_obj.notification_prefs if profile_obj else {}),
        'default_notification_prefs_json': json.dumps(DEFAULT_NOTIFICATION_PREFS),
        'activity_heatmap_json': activity_heatmap_json,
        'blocked_users': blocked_users,
        'completion_steps': completion_steps,
        'completion_score': completion_score,
        'completion_total': completion_total,
        'completion_pct': completion_pct,
        'computed_reputation': computed_reputation,
        'current_streak': current_streak,
        'user_reactions_feed': user_reactions_feed,
        'content_calendar_json': content_calendar_json,
        'endorsements_by_topic': endorsements_by_topic,
        'followed_categories': followed_categories,
        'coauthor_invites': coauthor_invites,
        'allow_mentions_from': profile_obj.allow_mentions_from if profile_obj else 'everyone',
        'mention_allow_choices': Profile.MENTION_ALLOW_CHOICES,
        'streak_grace_used_at': profile_obj.streak_grace_used_at if profile_obj else None,
        'analytics_summary': _analytics_summary,
    }
    return render(request, 'frontend/profile.html', context)

@login_required
@require_POST
def upload_profile_picture(request):
    
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
    
    # Validate file type via magic bytes (not just the client-supplied Content-Type header)
    header = file.read(12)
    file.seek(0)
    _MAGIC = [
        b'\xff\xd8\xff',                            # JPEG
        b'\x89PNG\r\n\x1a\n',                       # PNG
        b'GIF87a', b'GIF89a',                       # GIF
        b'RIFF',                                    # WebP (verified below)
    ]
    is_webp = header[:4] == b'RIFF' and header[8:12] == b'WEBP'
    if not any(header.startswith(sig) for sig in _MAGIC[:4]) and not is_webp:
        return JsonResponse({'success': False, 'error': 'Invalid file type. Only JPEG, PNG, GIF, and WebP allowed'}, status=400)
    # Also reject RIFF that is NOT WebP
    if header[:4] == b'RIFF' and not is_webp:
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

    profile_obj = Profile.objects.filter(user=profile_user).first()
    avatar_url = profile_obj.get_picture_url if profile_obj else ''
    profile_last_seen = profile_obj.last_seen if profile_obj else None
    now = timezone.now()
    is_online = bool(profile_last_seen and profile_last_seen >= now - timedelta(minutes=5))
    presence_label = 'Active now' if is_online else _presence_label(profile_last_seen, now=now)

    # Check if current user is following this user
    is_following = False
    is_blocked = False
    is_muted = False
    request_pending = False
    is_close_friend = False
    is_private = bool(profile_obj and profile_obj.is_private)
    if request.user.is_authenticated:
        is_following = Follow.objects.filter(follower=request.user, following=profile_user).exists()
        is_blocked = UserBlock.objects.filter(blocker=request.user, blocked=profile_user).exists()
        is_muted = UserMute.objects.filter(muter=request.user, muted=profile_user).exists()
        if is_private and not is_following and request.user != profile_user:
            request_pending = FollowRequest.objects.filter(
                from_user=request.user, to_user=profile_user, status='pending'
            ).exists()
        is_close_friend = profile_user.close_friends_list.filter(friend=request.user).exists()

    # Gate content for private profiles
    is_own_profile = request.user == profile_user
    can_see_content = is_own_profile or is_following or not is_private

    # Filter posts by audience: owners see all; followers see public+followers;
    # close friends see all; everyone else sees only public.
    _base_posts = Post.objects.filter(user=profile_user).exclude(id='').order_by('-created_at')
    if is_own_profile:
        user_posts = _base_posts
    elif is_close_friend:
        user_posts = _base_posts
    elif is_following:
        user_posts = _base_posts.filter(audience__in=('public', 'followers'))
    else:
        user_posts = _base_posts.filter(audience='public')

    follows_you_back = False
    user_endorsements = []
    if request.user.is_authenticated:
        follows_you_back = Follow.objects.filter(
            follower=profile_user, following=request.user
        ).exists()
        from users.models import Endorsement as _EndQ
        try:
            _e = list(_EndQ.objects.filter(endorsed=profile_user).select_related('endorser').order_by('topic', '-created_at'))
            _et = {}
            for e in _e:
                _et.setdefault(e.topic, []).append(e.endorser.username)
            user_endorsements = [{'topic': t, 'endorsers': ns, 'count': len(ns)} for t, ns in _et.items()]
        except Exception:
            user_endorsements = []

    # Record profile view (skip self-views, unauthenticated, and opted-out profiles)
    if (request.user.is_authenticated
            and request.user != profile_user
            and profile_obj
            and not profile_obj.hide_profile_views):
        ProfileView.objects.update_or_create(
            viewer=request.user,
            viewed=profile_user,
            defaults={},
        )
        # Notify the viewed user (once per viewer — deduped by unique constraint)
        Notification.objects.get_or_create(
            user=profile_user,
            notification_type='profile_view',
            message=f'{request.user.username} viewed your profile.',
        )

    user_reviews = Review.objects.filter(user=profile_user, is_deleted_by_moderation=False).order_by('-created_at')
    user_questions = Question.objects.filter(user=profile_user, is_deleted_by_moderation=False).order_by('-created_at')
    user_polls = Poll.objects.filter(user=profile_user).order_by('-created_at')

    is_moderator = _is_configured_moderator(request.user) if request.user.is_authenticated else False

    up_achievements = list(Achievement.objects.filter(user=profile_user).values_list('code', flat=True))
    up_achievements_set = set(up_achievements)
    up_achievement_details = [
        {'code': d[0], 'icon': d[1], 'label': d[2], 'desc': d[3]}
        for d in ACHIEVEMENT_DEFS if d[0] in up_achievements_set
    ]

    # Profile highlights
    from users.models import ProfileHighlight
    highlights = list(
        ProfileHighlight.objects.filter(user=profile_user)
        .select_related('post', 'post__user')
        .order_by('order', '-created_at')[:6]
    )
    user_highlight_post_ids = set()
    if request.user.is_authenticated:
        user_highlight_post_ids = set(
            ProfileHighlight.objects.filter(user=request.user).values_list('post_id', flat=True)
        )

    # Activity heatmap for public profile (post + comment counts per day, last 364 days)
    import json as _upjson, datetime as _updt
    from django.db.models.functions import TruncDate as _UpTruncDate
    _up_heatmap_start = timezone.now().date() - timedelta(days=363)
    _up_post_counts = {
        str(r['day']): r['n']
        for r in Post.objects.filter(
            user=profile_user, is_draft=False,
            created_at__date__gte=_up_heatmap_start,
        ).annotate(day=_UpTruncDate('created_at')).values('day').annotate(n=Count('id'))
    }
    _up_comment_counts = {
        str(r['day']): r['n']
        for r in Comment.objects.filter(
            user=profile_user,
            created_at__date__gte=_up_heatmap_start,
        ).annotate(day=_UpTruncDate('created_at')).values('day').annotate(n=Count('id'))
    }
    _up_all_days = {}
    _up_d = _up_heatmap_start
    while _up_d <= timezone.now().date():
        _up_key = str(_up_d)
        _up_all_days[_up_key] = _up_post_counts.get(_up_key, 0) + _up_comment_counts.get(_up_key, 0)
        _up_d += _updt.timedelta(days=1)
    up_activity_heatmap_json = _upjson.dumps(_up_all_days)

    # Mutual followers — people the viewer follows who also follow profile_user
    mutual_followers = []
    mutual_followers_count = 0
    if request.user.is_authenticated and request.user != profile_user:
        viewer_following_ids = set(
            Follow.objects.filter(follower=request.user).values_list('following_id', flat=True)
        )
        profile_follower_ids = set(
            Follow.objects.filter(following=profile_user).values_list('follower_id', flat=True)
        )
        mutual_ids = viewer_following_ids & profile_follower_ids
        mutual_followers_count = len(mutual_ids)
        if mutual_ids:
            mutual_followers = list(
                User.objects.filter(id__in=list(mutual_ids)[:3]).values_list('username', flat=True)
            )

    # Debate win/loss/draw record (derived from observer votes)
    _dp_qs = DebateParticipant.objects.filter(user=profile_user, debate__status='completed')
    _debate_played = _dp_qs.values('debate_id').distinct().count()
    _yes_wins = ObserverVote.objects.filter(
        debate__participants__user=profile_user,
        debate__participants__side='yes',
        winner_side='yes',
    ).values('debate').distinct().count()
    _no_wins = ObserverVote.objects.filter(
        debate__participants__user=profile_user,
        debate__participants__side='no',
        winner_side='no',
    ).values('debate').distinct().count()
    _debate_wins = _yes_wins + _no_wins
    _debate_draws = _dp_qs.filter(debate__outcome='draw').values('debate_id').distinct().count()
    _debate_losses = max(0, _debate_played - _debate_wins - _debate_draws)

    # Public series for this profile
    public_user_series = []
    if can_see_content:
        public_user_series = list(
            PostSeries.objects.filter(user=profile_user)
            .annotate(post_count=Count('items'))
            .order_by('-created_at')
        )

    context = {
        'profile_user': profile_user,
        'user_posts': user_posts if can_see_content else [],
        'user_reviews': user_reviews if can_see_content else [],
        'user_questions': user_questions if can_see_content else [],
        'user_polls': user_polls if can_see_content else [],
        'user_series': public_user_series,
        'pinned_posts': list(Post.objects.filter(user=profile_user, is_pinned=True, is_draft=False).order_by('-updated_at')[:3]) if can_see_content else [],
        'avatar_url': avatar_url,
        'is_online': is_online,
        'presence_label': presence_label,
        'followers_count': profile_user.follower_links.count(),
        'following_count': profile_user.following_links.count(),
        'is_following': is_following,
        'is_own_profile': is_own_profile,
        'is_private': is_private,
        'can_see_content': can_see_content,
        'request_pending': request_pending,
        'is_blocked': is_blocked,
        'is_muted': is_muted,
        'profile_bio': profile_obj.bio if profile_obj else '',
        'profile_website': profile_obj.website if profile_obj else '',
        'debate_participations_count': DebateParticipant.objects.filter(user=profile_user).count(),
        'debate_played': _debate_played,
        'debate_wins': _debate_wins,
        'debate_losses': _debate_losses,
        'debate_draws': _debate_draws,
        'debate_win_rate': round(_debate_wins / _debate_played * 100) if _debate_played else 0,
        'trust_badge': profile_obj.trust_badge if profile_obj else ('', '', ''),
        'trust_level': profile_obj.trust_level if profile_obj else 'new',
        'is_verified': profile_obj.is_verified if profile_obj else False,
        'is_moderator': is_moderator,
        'user_achievements': up_achievements,
        'achievement_details': up_achievement_details,
        'follows_you_back': follows_you_back,
        'user_endorsements': user_endorsements,
        'highlights': highlights,
        'user_highlight_post_ids': user_highlight_post_ids,
        'activity_heatmap_json': up_activity_heatmap_json,
        'hide_profile_views': profile_obj.hide_profile_views if profile_obj else False,
        'profile_views_count': (
            ProfileView.objects.filter(viewed=profile_user).count()
            if profile_obj and not profile_obj.hide_profile_views and request.user == profile_user
            else None
        ),
        'streak_days': profile_obj.streak_days if profile_obj else 0,
        'mutual_followers': mutual_followers,
        'mutual_followers_count': mutual_followers_count,
        'viewer_lists': (
            list(UserList.objects.filter(creator=request.user).values('id', 'name'))
            if request.user.is_authenticated and not is_own_profile else []
        ),
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
    query = request.GET.get('q', '').strip()
    active_tab = request.GET.get('tab', 'posts')
    sort = request.GET.get('sort', 'newest')        # newest | oldest | most_liked
    date_range = request.GET.get('date_range', '')  # today | week | month | ''

    post_results = []
    review_results = []
    question_results = []
    poll_results = []

    if query:
        from django.utils import timezone as _tz
        now = _tz.now()
        date_filter = {}
        if date_range == 'today':
            date_filter['created_at__date'] = now.date()
        elif date_range == 'week':
            date_filter['created_at__gte'] = now - timedelta(days=7)
        elif date_range == 'month':
            date_filter['created_at__gte'] = now - timedelta(days=30)

        if sort == 'most_liked':
            post_order = '-like_count'
        elif sort == 'oldest':
            post_order = 'created_at'
        else:
            post_order = '-created_at'

        post_qs = Post.objects.filter(
            Q(title__icontains=query) | Q(content__icontains=query), is_draft=False
        ).filter(**date_filter).annotate(like_count=Count('actions', filter=Q(actions__action='like')))

        if sort == 'most_liked':
            post_qs = post_qs.order_by('-like_count', '-created_at')
        elif sort == 'oldest':
            post_qs = post_qs.order_by('created_at')
        else:
            post_qs = post_qs.order_by('-created_at')

        post_results = list(post_qs[:50])

        review_qs = Review.objects.filter(
            is_deleted_by_moderation=False
        ).filter(
            Q(subject__icontains=query) | Q(content__icontains=query)
        ).filter(**date_filter)
        review_results = list(review_qs.order_by(post_order.replace('like_count', 'created_at').replace('-like_count', '-created_at'))[:50])

        question_qs = Question.objects.filter(
            is_deleted_by_moderation=False
        ).filter(
            Q(title__icontains=query) | Q(content__icontains=query)
        ).filter(**date_filter)
        question_results = list(question_qs.order_by(post_order.replace('like_count', 'created_at').replace('-like_count', '-created_at'))[:50])

        poll_qs = Poll.objects.filter(
            Q(title__icontains=query) | Q(description__icontains=query)
        ).filter(**date_filter)
        poll_results = list(poll_qs.order_by(post_order.replace('like_count', 'created_at').replace('-like_count', '-created_at'))[:50])

    totals = {
        'posts': len(post_results),
        'reviews': len(review_results),
        'questions': len(question_results),
        'polls': len(poll_results),
    }
    total_all = sum(totals.values())

    context = {
        'query': query,
        'active_tab': active_tab,
        'post_results': post_results,
        'review_results': review_results,
        'question_results': question_results,
        'poll_results': poll_results,
        'totals': totals,
        'total_all': total_all,
        'sort': sort,
        'date_range': date_range,
        'sort_options': [('newest', 'Newest'), ('oldest', 'Oldest'), ('most_liked', 'Most Liked')],
        'date_options': [('', 'All time'), ('today', 'Today'), ('week', 'This week'), ('month', 'This month')],
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


def mention_suggestions(request):
    q = request.GET.get('q', '').strip().lstrip('@')
    if len(q) < 1:
        return JsonResponse({'users': []})

    from django.contrib.auth.models import User as _User

    # Priority tiers: 3 = following, 2 = follower, 1 = debated, 0 = everyone
    following_ids = set()
    follower_ids = set()
    interacted_ids = set()

    if request.user.is_authenticated:
        following_ids = set(
            Follow.objects.filter(follower=request.user).values_list('following_id', flat=True)
        )
        follower_ids = set(
            Follow.objects.filter(following=request.user).values_list('follower_id', flat=True)
        )
        debate_qs = Debate.objects.filter(Q(initiator=request.user) | Q(target=request.user))
        for d in debate_qs.values('initiator_id', 'target_id'):
            interacted_ids.add(d['initiator_id'])
            interacted_ids.add(d['target_id'])
        interacted_ids.discard(request.user.id)

    candidates = list(
        _User.objects.filter(username__icontains=q, is_active=True)
        .exclude(id=request.user.id if request.user.is_authenticated else 0)
        .select_related('profile')[:40]
    )

    # Filter by each candidate's allow_mentions_from setting
    def _allowed(u):
        try:
            setting = u.profile.allow_mentions_from
        except Exception:
            return True
        if setting == 'nobody':
            return False
        if setting == 'followers':
            # The candidate allows only people they follow (i.e. requester must be in their following list)
            return u.id in following_ids or (request.user.is_authenticated and u.id in follower_ids)
        return True  # 'everyone'

    def _priority(uid):
        if uid in following_ids:
            return 3
        if uid in follower_ids:
            return 2
        if uid in interacted_ids:
            return 1
        return 0

    scored = sorted(
        [u for u in candidates if _allowed(u)],
        key=lambda u: (
            -_priority(u.id),
            not u.username.lower().startswith(q.lower()),
            u.username.lower(),
        )
    )[:8]

    result = []
    for u in scored:
        avatar = ''
        try:
            avatar = u.profile.get_picture_url or ''
        except Exception:
            pass
        result.append({'username': u.username, 'avatar': avatar})

    return JsonResponse({'users': result})


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

    # In-app notifications grouped by type
    raw_notifs = list(
        Notification.objects.filter(user=request.user)
        .select_related('post')
        .order_by('notification_type', '-created_at')
    )
    # Group by notification_type
    from collections import defaultdict as _dd
    grouped = _dd(list)
    for n in raw_notifs:
        grouped[n.notification_type].append(n)
    grouped_notifs = [
        {
            'type': ntype,
            'label': dict(Notification.NOTIFICATION_TYPES).get(ntype, ntype),
            'items': items,
            'unread_count': sum(1 for n in items if not n.is_read),
            'latest': items[0] if items else None,
        }
        for ntype, items in grouped.items()
    ]
    grouped_notifs.sort(key=lambda x: -x['unread_count'])
    total_unread = sum(g['unread_count'] for g in grouped_notifs)

    context = {
        'debates': debates,
        'post_notifications': Notification.objects.filter(
            user=request.user
        ).select_related('post').order_by('-created_at')[:30],
        'is_configured_moderator': _is_configured_moderator(request.user),
        'moderation_reports': moderation_reports,
        'profile_reports': profile_reports,
        'grouped_notifs': grouped_notifs,
        'total_unread': total_unread,
    }
    return render(request, 'frontend/notifications.html', context)


@login_required
@require_POST
def mark_all_notifications_read(request):
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    return JsonResponse({'success': True})


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
    chats = _build_chat_payload_for_user(request.user, only_active=True)
    return JsonResponse({'success': True, 'chats': chats})


def _build_chat_payload_for_user(user, only_active=False):
    participations = DebateParticipant.objects.filter(
        user=user,
        debate__status='accepted',
        is_banned=False,
    )
    if only_active:
        participations = participations.filter(is_active=True)

    participations = list(participations.select_related(
        'debate__post',
        'debate__poll',
        'debate__initiator__profile',
        'debate__target__profile',
    ))

    if not participations:
        return []

    # Batch-fetch the latest non-system message per debate in one query.
    debate_ids = [p.debate_id for p in participations]
    from django.db.models import Max
    latest_ids = (
        DebateMessage.objects
        .filter(debate_id__in=debate_ids, is_system=False)
        .values('debate_id')
        .annotate(max_id=Max('id'))
        .values_list('max_id', flat=True)
    )
    last_msgs = {
        m.debate_id: m
        for m in DebateMessage.objects.filter(
            id__in=latest_ids
        ).select_related('sender')
    }

    cutoff = timezone.now() - timedelta(minutes=5)

    chats = []
    for p in participations:
        debate = p.debate
        opponent = debate.target if user.id == debate.initiator_id else debate.initiator
        last_msg = last_msgs.get(debate.id)

        try:
            opp_avatar = opponent.profile.get_picture_url
        except Exception:
            opp_avatar = ''

        opp_is_online = _is_user_online(opponent, cutoff=cutoff)

        if debate.post_id:
            context_url = f'/discussion/{debate.post_id}/'
        elif debate.poll_id:
            context_url = f'/polls/{debate.poll_id}/'
        else:
            context_url = ''

        chats.append({
            'id': str(debate.id),
            'post_id': str(debate.post_id) if debate.post_id else '',
            'context_url': context_url,
            'title': debate.context_title,
            'opponent': opponent.username,
            'opponent_avatar': opp_avatar,
            'opponent_is_online': opp_is_online,
            'is_active': p.is_active,
            'last_message': _decode_chat_content_from_storage(last_msg.content)[:100] if last_msg else None,
            'last_message_sender': last_msg.sender.username if last_msg else None,
            'last_message_time': last_msg.created_at.strftime('%b %d, %H:%M') if last_msg else None,
            'last_message_at': last_msg.created_at.isoformat() if last_msg else None,
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


def notification_stream(request):
    """Server-Sent Events endpoint — pushes notification count when it changes."""
    if not request.user.is_authenticated:
        from django.http import HttpResponse
        return HttpResponse(status=401)

    import time as _time

    def _event_gen(user):
        last_count = -1
        # max ~5 min per connection, then client reconnects
        for _ in range(60):
            try:
                count = Notification.objects.filter(user=user, is_read=False).count()
                if count != last_count:
                    last_count = count
                    import json as _json
                    yield f'data: {_json.dumps({"count": count})}\n\n'
                _time.sleep(5)
            except Exception:
                break
        yield 'data: {"reconnect":true}\n\n'

    from django.http import StreamingHttpResponse
    response = StreamingHttpResponse(_event_gen(request.user), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response


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
            _check_new_ip_login(request, user)
            try:
                _profile = user.profile
                if _profile.totp_enabled and _profile.totp_secret:
                    request.session.cycle_key()
                    request.session['totp_pending_user_id'] = user.id
                    _redir = next_url or '/'
                    return redirect(f'/account/2fa/login/?next={_redir}')
            except Exception:
                pass
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
    if _is_public_rate_limited(request, 'check_username', limit=20, window_seconds=60):
        return JsonResponse({'available': False, 'message': 'Too many requests'}, status=429)
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
def my_interests(request):
    followed_tags = list(HashtagFollow.objects.filter(user=request.user).order_by('tag'))
    from discussions.models import CategoryFollow as _CF
    followed_categories = list(_CF.objects.filter(user=request.user).values_list('category', flat=True))
    all_categories = [c[0] for c in CATEGORY_CHOICES]
    return render(request, 'frontend/my_interests.html', {
        'followed_tags': followed_tags,
        'followed_categories': followed_categories,
        'all_categories': all_categories,
    })


@login_required
def my_reminders(request):
    """Show all pending (unsent) post reminders for the logged-in user."""
    from discussions.models import PostReminder as _RemView
    reminders = list(
        _RemView.objects.filter(user=request.user, is_sent=False)
        .select_related('post', 'post__user')
        .order_by('remind_at')
    )
    return render(request, 'frontend/my_reminders.html', {'reminders': reminders})


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
        return redirect('onboarding_step2')

    # If user visits again after already setting interests, redirect away
    if already_set and request.GET.get('force') != '1':
        return redirect('suggested')

    return render(request, 'frontend/interests_onboarding.html', {
        'all_categories': all_categories,
        'selected_categories': profile.interested_categories if profile else [],
    })


@login_required
def onboarding_step2(request):
    """Step 2 of onboarding: follow suggested people."""
    if request.method == 'POST':
        usernames = request.POST.getlist('follow')
        for uname in usernames:
            try:
                target = User.objects.get(username=uname)
                if target != request.user:
                    Follow.objects.get_or_create(follower=request.user, following=target)
            except User.DoesNotExist:
                pass
        return redirect('onboarding_step3')

    already_following_ids = Follow.objects.filter(follower=request.user).values_list('following_id', flat=True)
    suggestions = list(
        UserSuggestion.objects.filter(user=request.user)
        .select_related('suggested_user')[:8]
    )
    suggested_users = [s.suggested_user for s in suggestions]

    if len(suggested_users) < 4:
        popular = list(
            User.objects.exclude(id=request.user.id)
            .exclude(id__in=already_following_ids)
            .annotate(_fc=Count('follower_links'))
            .order_by('-_fc')[:8]
        )
        seen_ids = {u.id for u in suggested_users}
        for u in popular:
            if u.id not in seen_ids:
                suggested_users.append(u)
                seen_ids.add(u.id)
            if len(suggested_users) >= 8:
                break

    profiles = {
        p.user_id: p
        for p in Profile.objects.filter(user__in=[u for u in suggested_users])
    }
    for u in suggested_users:
        u.cached_profile = profiles.get(u.id)

    return render(request, 'frontend/onboarding_step2.html', {
        'suggested_users': suggested_users[:8],
    })


@login_required
def onboarding_step3(request):
    """Step 3 of onboarding: complete profile bio and website."""
    profile = Profile.objects.filter(user=request.user).first()

    if request.method == 'POST':
        bio = request.POST.get('bio', '').strip()[:280]
        website = request.POST.get('website', '').strip()
        if website and not (website.startswith('https://') or website.startswith('http://')):
            website = ''
        if profile:
            if bio:
                profile.bio = bio
            if website:
                profile.website = website
            profile.save(update_fields=['bio', 'website'])
        return redirect('suggested')

    return render(request, 'frontend/onboarding_step3.html', {'profile': profile})


@login_required
def notification_center(request):
    """Dedicated notification inbox with type filtering and pagination."""
    notif_type = request.GET.get('type', '').strip()
    qs = Notification.objects.filter(user=request.user).select_related('post')
    if notif_type:
        qs = qs.filter(notification_type=notif_type)

    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    unread_count = Notification.objects.filter(user=request.user, is_read=False).count()

    return render(request, 'frontend/notification_center.html', {
        'notifications': page_obj.object_list,
        'page_obj': page_obj,
        'notif_type': notif_type,
        'notification_types': Notification.NOTIFICATION_TYPES,
        'unread_count': unread_count,
    })


@login_required
@require_POST
def mark_notification_read(request, notif_id):
    """Mark a single Notification as read."""
    notif = get_object_or_404(Notification, id=notif_id, user=request.user)
    notif.is_read = True
    notif.save(update_fields=['is_read'])
    return JsonResponse({'success': True})


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
    """Create a new post (or save as draft)."""
    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        content = _normalize_post_content(request.POST.get('content', ''))
        category = request.POST.get('category', '').strip() or 'Others'
        hashtags = request.POST.get('hashtags', '').strip()
        accepted_rules = request.POST.get('accepted_rules', '0').strip()
        save_as_draft = request.POST.get('save_draft') == '1'
        mood = request.POST.get('mood', '').strip()
        valid_moods = [m[0] for m in Post.MOOD_CHOICES]
        if mood not in valid_moods:
            mood = ''
        reply_restriction = request.POST.get('reply_restriction', Post.REPLY_EVERYONE).strip()
        valid_restrictions = [r[0] for r in Post.REPLY_CHOICES]
        if reply_restriction not in valid_restrictions:
            reply_restriction = Post.REPLY_EVERYONE

        if not title:
            messages.error(request, 'Please enter a question title.')
            return redirect('ask_question')

        if not save_as_draft and accepted_rules != '1':
            messages.error(request, 'Please review and accept the ask question instructions before posting.')
            return redirect('ask_question')

        combined_text = f"{title} {content}".strip()
        if combined_text and check_content_moderation(combined_text):
            messages.error(request, 'Your post contains abusive language and cannot be posted.')
            return redirect('ask_question')

        if not save_as_draft:
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

        hashtag_list = Post.parse_hashtags(hashtags, max_tags=5)
        processed_hashtags = ', '.join(hashtag_list)

        # Handle scheduled publishing
        scheduled_for_dt = None
        scheduled_for_raw = request.POST.get('scheduled_for', '').strip()
        if scheduled_for_raw and save_as_draft:
            try:
                from django.utils.dateparse import parse_datetime
                naive_dt = parse_datetime(scheduled_for_raw)
                if naive_dt is not None:
                    if timezone.is_naive(naive_dt):
                        scheduled_for_dt = timezone.make_aware(naive_dt)
                    else:
                        scheduled_for_dt = naive_dt
                    if scheduled_for_dt <= timezone.now():
                        messages.error(request, 'Scheduled time must be in the future.')
                        return redirect('ask_question')
            except Exception:
                pass

        # Handle quote post
        quoted_post_id = (request.POST.get('quoted_post_id') or '').strip()
        quoted_post_obj = None
        if quoted_post_id:
            try:
                quoted_post_obj = Post.objects.get(id=quoted_post_id)
            except Post.DoesNotExist:
                pass

        is_anonymous = request.POST.get('is_anonymous') == '1'

        yes_label = request.POST.get('yes_label', '').strip()[:50] or 'Yes'
        no_label = request.POST.get('no_label', '').strip()[:50] or 'No'

        try:
            post = Post.objects.create(
                id=str(uuid.uuid4()),
                user=request.user,
                title=title,
                content=content,
                category=category,
                hashtags=processed_hashtags,
                is_draft=save_as_draft,
                scheduled_for=scheduled_for_dt,
                quoted_post=quoted_post_obj,
                mood=mood,
                reply_restriction=reply_restriction,
                is_anonymous=is_anonymous,
                yes_label=yes_label,
                no_label=no_label,
            )

            if not post or not post.id:
                messages.error(request, 'Failed to create post')
                return redirect('index')

            if save_as_draft:
                messages.success(request, 'Draft saved.')
                _maybe_award_achievements(request.user)
                return redirect('profile')

            try:
                _update_streak(request.user.profile)
            except Exception:
                pass
            _notify_mentions(request.user, f"{title} {content}", post)
            _maybe_award_achievements(request.user)
            return redirect(f'/discussion/{post.id}/?created=1')
        except Exception as e:
            messages.error(request, f'Failed to create post: {str(e)}')
            return redirect('index')

    return redirect('index')


@login_required
@require_POST
def publish_draft(request, post_id):
    """Publish a saved draft post."""
    post = get_object_or_404(Post, id=post_id, user=request.user, is_draft=True)

    limit_reached, _, limit = has_reached_daily_post_limit(request.user)
    if limit_reached:
        return JsonResponse({'success': False, 'error': f'You can create up to {limit} posts per day.'}, status=429)

    post.is_draft = False
    post.scheduled_for = None
    post.save(update_fields=['is_draft', 'scheduled_for', 'updated_at'])
    return JsonResponse({'success': True, 'url': f'/discussion/{post.id}/?created=1'})


@login_required
@require_POST
def reschedule_draft(request, post_id):
    """Update or clear the scheduled_for time on a draft post."""
    post = get_object_or_404(Post, id=post_id, user=request.user, is_draft=True)
    scheduled_for_raw = request.POST.get('scheduled_for', '').strip()
    if not scheduled_for_raw:
        post.scheduled_for = None
        post.save(update_fields=['scheduled_for', 'updated_at'])
        return JsonResponse({'success': True, 'scheduled_for': None})
    from django.utils.dateparse import parse_datetime
    try:
        naive_dt = parse_datetime(scheduled_for_raw)
        if naive_dt is None:
            return JsonResponse({'success': False, 'error': 'Invalid datetime format.'}, status=400)
        scheduled_dt = timezone.make_aware(naive_dt) if timezone.is_naive(naive_dt) else naive_dt
        if scheduled_dt <= timezone.now():
            return JsonResponse({'success': False, 'error': 'Scheduled time must be in the future.'}, status=400)
        post.scheduled_for = scheduled_dt
        post.save(update_fields=['scheduled_for', 'updated_at'])
        return JsonResponse({'success': True, 'scheduled_for': scheduled_dt.isoformat()})
    except Exception:
        return JsonResponse({'success': False, 'error': 'Could not parse datetime.'}, status=400)


@login_required
@require_POST
def pin_post(request, post_id):
    """Toggle pin on a post for the author's profile. Up to 3 posts can be pinned."""
    MAX_PINS = 3
    post = get_object_or_404(Post, id=post_id, user=request.user, is_draft=False)
    if post.is_pinned:
        post.is_pinned = False
        post.save(update_fields=['is_pinned', 'updated_at'])
        return JsonResponse({'success': True, 'is_pinned': False})
    current_pins = Post.objects.filter(user=request.user, is_pinned=True).count()
    if current_pins >= MAX_PINS:
        return JsonResponse({'success': False, 'error': f'You can pin at most {MAX_PINS} posts. Unpin one first.'}, status=400)
    post.is_pinned = True
    post.save(update_fields=['is_pinned', 'updated_at'])
    return JsonResponse({'success': True, 'is_pinned': True})


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


def post_edit_history(request, post_id):
    """Return edit history for a post as JSON."""
    post = get_object_or_404(Post, id=post_id)
    entries = list(
        PostEditHistory.objects.filter(post=post).order_by('-edited_at').values(
            'original_title', 'original_content', 'edited_at'
        )
    )
    for e in entries:
        e['edited_at'] = e['edited_at'].strftime('%b %-d, %Y %I:%M %p')
    return JsonResponse({'history': entries, 'current_title': post.title, 'current_content': post.content})


def comment_edit_history(request, comment_id):
    """Return edit history for a comment as JSON."""
    comment = get_object_or_404(Comment, id=comment_id)
    entries = list(
        CommentEditHistory.objects.filter(comment=comment).order_by('-edited_at').values(
            'original_content', 'edited_at'
        )
    )
    for e in entries:
        e['edited_at'] = e['edited_at'].strftime('%b %-d, %Y %I:%M %p')
    return JsonResponse({'history': entries, 'current_content': comment.content})


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
        _raw_conf = request.POST.get('confidence_score', '')
        try:
            confidence_score = max(1, min(10, int(_raw_conf))) if _raw_conf else None
        except (ValueError, TypeError):
            confidence_score = None

        if not vote_type or vote_type not in ['yes', 'no']:
            return handle_error('Invalid vote type')

        # Enforce reply_restriction
        restriction = getattr(post, 'reply_restriction', Post.REPLY_EVERYONE)
        if restriction != Post.REPLY_EVERYONE and request.user != post.user:
            if restriction == Post.REPLY_NOBODY:
                return handle_error('The author has disabled replies on this post.')
            elif restriction == Post.REPLY_FOLLOWERS:
                is_follower = Follow.objects.filter(follower=request.user, following=post.user).exists()
                if not is_follower:
                    return handle_error('Only followers of this author can reply.')
            elif restriction == Post.REPLY_CLOSE_FRIENDS:
                is_close_friend = CloseFriend.objects.filter(user=post.user, friend=request.user).exists()
                if not is_close_friend:
                    return handle_error('Only close friends of this author can reply.')

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
            if content:
                _notify_mentions(request.user, content, post)

            if is_ajax:
                return JsonResponse(build_vote_payload('Comment submitted!', has_comment=True))

            messages.success(request, 'Comment submitted!')
            return redirect('discussion', post_id=post_id)
        else:
            # First time voting - content is optional
            if content and check_content_moderation(content):
                return handle_error('Your comment contains abusive language and cannot be posted.')

            reply_to_comment = None
            reply_to_id = request.POST.get('reply_to_id', '').strip()
            if reply_to_id and content:
                try:
                    reply_to_comment = Comment.objects.get(id=reply_to_id, post=post)
                except Comment.DoesNotExist:
                    pass

            Comment.objects.create(
                id=str(uuid.uuid4()),
                post=post,
                user=request.user,
                vote_type=vote_type,
                content=content if content else '',
                reply_to=reply_to_comment,
                is_anonymous=request.POST.get('is_anonymous') == '1',
                confidence_score=confidence_score,
            )
            try:
                _update_streak(request.user.profile)
            except Exception:
                pass
            if content:
                _notify_mentions(request.user, content, post)
            if reply_to_comment and reply_to_comment.user != request.user:
                Notification.objects.create(
                    user=reply_to_comment.user,
                    post=post,
                    notification_type='reply',
                    message=f'@{request.user.username} replied to your comment on "{post.title}".',
                )
                _send_notification_email(
                    reply_to_comment.user,
                    f'New reply on "{post.title}"',
                    f'@{request.user.username} replied to your comment:\n\n"{content[:280]}"\n\n'
                    f'View it at: {settings.SITE_URL}/discussion/{post.id}/',
                    notif_type='reply',
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

def _handle_start_review_debate(request, review_comment_id):
    """Start or join a debate on a review comment."""
    try:
        review_comment = ReviewComment.objects.select_related('review', 'user').get(id=review_comment_id)
    except ReviewComment.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Comment not found.'}, status=404)

    target_user = review_comment.user
    review = review_comment.review

    if request.user == target_user:
        return JsonResponse({'success': False, 'error': 'Cannot debate with yourself.'})

    if _is_blocked_by_comment_owner(target_user, request.user):
        return JsonResponse({'success': False, 'error': 'You are not allowed to send debate requests to this commenter.'})

    if DebateParticipant.objects.filter(user=request.user, is_banned=True, debate__review_comment=review_comment).exists():
        return JsonResponse({'success': False, 'error': 'You were removed from this debate and cannot start it again.'})

    # Must have voted on the review
    try:
        user_reaction = ReviewReaction.objects.get(review=review, user=request.user)
    except ReviewReaction.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Please vote Agree or Disagree on the review before starting a debate.'}, status=400)

    # Must be on opposite side
    if user_reaction.reaction == review_comment.side:
        return JsonResponse({'success': False, 'error': 'You can only debate someone from the opposite side.'}, status=400)

    # Map agree→yes, disagree→no for DebateParticipant.side
    side_map = {'agree': 'yes', 'disagree': 'no'}
    desired_side = side_map[user_reaction.reaction]

    accepted_debate = Debate.objects.filter(
        review_comment=review_comment, target=target_user, status='accepted'
    ).order_by('-updated_at').first()

    if accepted_debate:
        _ensure_debate_core_participants(accepted_debate)
        active_counts = {
            item['side']: item['total']
            for item in DebateParticipant.objects.filter(debate=accepted_debate, is_active=True).values('side').annotate(total=Count('id'))
        }
        chosen_side = _pick_debate_side_for_user(accepted_debate, desired_side, active_counts=active_counts, fallback_side=desired_side)
        participant = DebateParticipant.objects.filter(debate=accepted_debate, user=request.user).first()

        if participant and participant.is_banned:
            return JsonResponse({'success': False, 'error': 'You were removed from this conversation.'})
        if participant and participant.is_active:
            return JsonResponse({'success': True, 'message': 'You are already in this conversation.', 'redirect_url': f'/debates/{accepted_debate.id}/chat/'})

        yes_active = active_counts.get('yes', 0)
        no_active = active_counts.get('no', 0)
        conversation_full = (
            accepted_debate.yes_supporters > 0 and accepted_debate.no_supporters > 0
            and yes_active >= accepted_debate.yes_supporters
            and no_active >= accepted_debate.no_supporters
        )
        if conversation_full:
            if not participant:
                DebateParticipant.objects.create(debate=accepted_debate, user=request.user, side=chosen_side, is_active=False)
            return JsonResponse({'success': True, 'queued': True, 'message': 'Debate is full. You can view it.', 'redirect_url': f'/debates/{accepted_debate.id}/chat/'})

        if participant:
            participant.is_active = True
            participant.left_at = None
            participant.save(update_fields=['is_active', 'left_at'])
        else:
            DebateParticipant.objects.create(debate=accepted_debate, user=request.user, side=chosen_side, is_active=True)

        if not accepted_debate.end_controller_id:
            _set_end_controller_with_fallback(accepted_debate, preferred_side=chosen_side)

        return JsonResponse({'success': True, 'message': 'Joined debate!', 'redirect_url': f'/debates/{accepted_debate.id}/chat/'})

    # Check for an existing pending debate on this review comment
    pending = Debate.objects.filter(review_comment=review_comment, status='pending').order_by('-created_at').first()
    if pending:
        # Initiator is already participant #1 — just return waiting state
        if pending.initiator == request.user:
            return JsonResponse({'success': True, 'waiting': True,
                                 'message': 'Challenge already sent. Waiting for them to accept…',
                                 'debate_id': pending.id})
        # Already pre-joined
        if DebateParticipant.objects.filter(debate=pending, user=request.user).exists():
            return JsonResponse({'success': True, 'waiting': True,
                                 'message': 'You already pre-joined. Waiting for the debate to start.',
                                 'debate_id': pending.id})
        # Try to pre-join
        yes_pre, no_pre = _pending_side_counts(pending)
        if not _can_pre_join(yes_pre, no_pre, desired_side):
            my_count = yes_pre if desired_side == 'yes' else no_pre
            if my_count >= PRE_JOIN_LIMIT:
                return JsonResponse({'success': False,
                                     'error': f'Your side is already full ({PRE_JOIN_LIMIT}/{PRE_JOIN_LIMIT} pre-joined).'})
            return JsonResponse({'success': False,
                                 'error': 'The other side needs to catch up first. Try again shortly.'})
        DebateParticipant.objects.create(debate=pending, user=request.user, side=desired_side, is_active=True)
        yes_pre2, no_pre2 = _pending_side_counts(pending)
        return JsonResponse({'success': True, 'pre_joined': True,
                             'message': 'Pre-joined! Waiting for the debate to start.',
                             'debate_id': pending.id,
                             'pre_join_yes': yes_pre2,
                             'pre_join_no': no_pre2})

    debate = Debate.objects.create(
        id=str(uuid.uuid4()),
        review_comment=review_comment,
        initiator=request.user,
        target=target_user,
        status='pending',
    )
    # Auto-add initiator as participant #1 on their side
    DebateParticipant.objects.create(debate=debate, user=request.user, side=desired_side, is_active=True)
    return JsonResponse({'success': True, 'message': 'Challenge sent! Others can now pre-join while you wait.',
                         'debate_id': debate.id})


def _handle_start_poll_debate(request, poll_comment_id):
    """Start or join a debate on a poll comment."""
    try:
        poll_comment = PollComment.objects.select_related('poll', 'option', 'user').get(id=poll_comment_id)
    except PollComment.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Comment not found.'}, status=404)

    target_user = poll_comment.user
    poll = poll_comment.poll

    if request.user == target_user:
        return JsonResponse({'success': False, 'error': 'Cannot debate with yourself.'})

    if _is_blocked_by_comment_owner(target_user, request.user):
        return JsonResponse({'success': False, 'error': 'You are not allowed to send debate requests to this commentor.'})

    if DebateParticipant.objects.filter(
        user=request.user,
        is_banned=True,
        debate__poll_comment=poll_comment,
    ).exists():
        return JsonResponse({'success': False, 'error': 'You were removed from this comment debate and cannot start it again.'})

    try:
        user_vote = PollVote.objects.get(poll=poll, user=request.user)
    except PollVote.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Please vote on this poll first before starting a debate.'}, status=400)

    if user_vote.option_id == poll_comment.option_id:
        return JsonResponse({'success': False, 'error': 'You can only start a debate with someone who chose a different option.'}, status=400)

    accepted_debate = Debate.objects.filter(
        poll=poll,
        target=target_user,
        status='accepted',
    ).order_by('-updated_at').first()

    if accepted_debate:
        _ensure_debate_core_participants(accepted_debate)
        active_counts = {
            item['side']: item['total']
            for item in DebateParticipant.objects.filter(
                debate=accepted_debate, is_active=True
            ).values('side').annotate(total=Count('id'))
        }
        desired_side = 'no'
        chosen_side = _pick_debate_side_for_user(
            accepted_debate, desired_side, active_counts=active_counts, fallback_side='no'
        )
        yes_active = active_counts.get('yes', 0)
        no_active = active_counts.get('no', 0)
        conversation_full = (
            accepted_debate.yes_supporters > 0 and accepted_debate.no_supporters > 0
            and yes_active >= accepted_debate.yes_supporters
            and no_active >= accepted_debate.no_supporters
        )
        participant = DebateParticipant.objects.filter(debate=accepted_debate, user=request.user).first()

        if participant and participant.is_banned:
            return JsonResponse({'success': False, 'error': 'The commentor removed you from this conversation.'})
        if participant and participant.is_active:
            return JsonResponse({'success': True, 'message': 'You are already in this conversation.', 'redirect_url': f'/debates/{accepted_debate.id}/chat/'})

        if conversation_full:
            if not participant:
                DebateParticipant.objects.create(debate=accepted_debate, user=request.user, side=chosen_side, is_active=False)
            return JsonResponse({'success': True, 'queued': True, 'message': 'Conversation is full. You can view the debate.', 'redirect_url': f'/debates/{accepted_debate.id}/chat/'})

        if participant:
            participant.is_active = True
            participant.left_at = None
            participant.save(update_fields=['is_active', 'left_at'])
        else:
            DebateParticipant.objects.create(debate=accepted_debate, user=request.user, side=chosen_side, is_active=True)

        if not accepted_debate.end_controller_id:
            _set_end_controller_with_fallback(accepted_debate, preferred_side=chosen_side)

        return JsonResponse({'success': True, 'message': 'Joined debate!', 'redirect_url': f'/debates/{accepted_debate.id}/chat/'})

    reusable_completed = Debate.objects.filter(
        poll=poll,
        target=target_user,
        status='completed',
    ).order_by('-updated_at').first()

    if reusable_completed:
        pending_for_target = Debate.objects.filter(target=target_user, status='pending').exclude(id=reusable_completed.id)
        if pending_for_target.count() >= 10:
            return JsonResponse({'success': False, 'queued': True, 'error': 'You are in queue. This user already has 10 pending requests.'})

        reusable_completed.poll_comment = poll_comment
        reusable_completed.initiator = request.user
        reusable_completed.status = 'pending'
        reusable_completed.end_controller = None
        reusable_completed.end_controller_side = ''
        reusable_completed.save(update_fields=['poll_comment', 'initiator', 'status', 'end_controller', 'end_controller_side', 'updated_at'])

        DebateParticipant.objects.filter(debate=reusable_completed).exclude(
            user_id__in=[reusable_completed.initiator_id, reusable_completed.target_id]
        ).update(is_active=False, left_at=timezone.now())

        DebateMessage.objects.create(
            debate=reusable_completed,
            sender=request.user,
            content=f"{request.user.username} requested to restart the poll debate.",
            is_system=True,
        )
        return JsonResponse({'success': True, 'message': 'Debate restart request sent!', 'debate_id': reusable_completed.id})

    # Determine which side the requesting user is on (for poll debates, initiator='no')
    initiator_desired_side = 'no'

    # Check for an existing pending debate on this poll comment
    pending = Debate.objects.filter(poll_comment=poll_comment, status='pending').order_by('-created_at').first()
    if pending:
        if pending.initiator == request.user:
            return JsonResponse({'success': True, 'waiting': True,
                                 'message': 'Challenge already sent. Waiting for them to accept…',
                                 'debate_id': pending.id})
        if DebateParticipant.objects.filter(debate=pending, user=request.user).exists():
            return JsonResponse({'success': True, 'waiting': True,
                                 'message': 'You already pre-joined. Waiting for the debate to start.',
                                 'debate_id': pending.id})
        # Side for pre-joiner: same as initiator_desired_side (no) unless they voted same as target
        pre_join_side = 'yes' if user_vote.option_id == poll_comment.option_id else 'no'
        yes_pre, no_pre = _pending_side_counts(pending)
        if not _can_pre_join(yes_pre, no_pre, pre_join_side):
            my_count = yes_pre if pre_join_side == 'yes' else no_pre
            if my_count >= PRE_JOIN_LIMIT:
                return JsonResponse({'success': False,
                                     'error': f'Your side is already full ({PRE_JOIN_LIMIT}/{PRE_JOIN_LIMIT} pre-joined).'})
            return JsonResponse({'success': False,
                                 'error': 'The other side needs to catch up first. Try again shortly.'})
        DebateParticipant.objects.create(debate=pending, user=request.user, side=pre_join_side, is_active=True)
        yes_pre2, no_pre2 = _pending_side_counts(pending)
        return JsonResponse({'success': True, 'pre_joined': True,
                             'message': 'Pre-joined! Waiting for the debate to start.',
                             'debate_id': pending.id,
                             'pre_join_yes': yes_pre2,
                             'pre_join_no': no_pre2})

    debate = Debate.objects.create(
        id=str(uuid.uuid4()),
        poll_comment=poll_comment,
        poll=poll,
        initiator=request.user,
        target=target_user,
        status='pending',
    )
    # Auto-add initiator as participant #1 on their side
    DebateParticipant.objects.create(debate=debate, user=request.user, side=initiator_desired_side, is_active=True)
    return JsonResponse({'success': True, 'message': 'Challenge sent! Others can now pre-join while you wait.',
                         'debate_id': debate.id})


@login_required
@require_POST
def start_debate(request):
    """Start a debate with another user"""
    poll_comment_id = request.POST.get('poll_comment_id')
    if poll_comment_id:
        return _handle_start_poll_debate(request, poll_comment_id)

    review_comment_id = request.POST.get('review_comment_id')
    if review_comment_id:
        return _handle_start_review_debate(request, review_comment_id)

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

            Notification.objects.create(
                user=target_user,
                post=comment.post,
                notification_type='author_debate',
                message=f"@{request.user.username} wants to restart the debate with you.",
            )

            return JsonResponse({'success': True, 'message': 'Debate restart request sent!', 'debate_id': reusable_completed.id})

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

        # Check for an existing pending debate on this comment (any initiator)
        pending_debate = Debate.objects.filter(comment=comment, status='pending').order_by('-created_at').first()
        if pending_debate:
            # Initiator is already participant #1 — return waiting state
            if pending_debate.initiator == request.user:
                return JsonResponse({'success': True, 'waiting': True,
                                     'message': 'Challenge already sent. Waiting for them to accept…',
                                     'debate_id': pending_debate.id})
            # Already pre-joined
            if DebateParticipant.objects.filter(debate=pending_debate, user=request.user).exists():
                return JsonResponse({'success': True, 'waiting': True,
                                     'message': 'You already pre-joined. Waiting for the debate to start.',
                                     'debate_id': pending_debate.id})
            # Determine side for pre-joiner
            if desired_side not in ('yes', 'no'):
                return JsonResponse({'success': False,
                                     'error': 'Please vote yes or no on the post before pre-joining.'}, status=400)
            yes_pre, no_pre = _pending_side_counts(pending_debate)
            if not _can_pre_join(yes_pre, no_pre, desired_side):
                my_count    = yes_pre if desired_side == 'yes' else no_pre
                other_count = no_pre  if desired_side == 'yes' else yes_pre
                my_label    = desired_side.upper()
                other_label = 'NO' if desired_side == 'yes' else 'YES'
                if my_count >= PRE_JOIN_LIMIT:
                    return JsonResponse({
                        'success': False, 'can_view': True,
                        'debate_id': pending_debate.id,
                        'error': (
                            f'The {my_label} side is already full ({PRE_JOIN_LIMIT}/{PRE_JOIN_LIMIT}). '
                            f'You can view the debate while you wait for a spot.'
                        ),
                    })
                return JsonResponse({
                    'success': False, 'can_view': True,
                    'debate_id': pending_debate.id,
                    'error': (
                        f"You can't join the {my_label} side yet — there "
                        f"{'is only' if other_count == 1 else 'are only'} {other_count} "
                        f"{other_label} user{'s' if other_count != 1 else ''} so far. "
                        f"You can view the debate until a {other_label} user joins to keep it balanced."
                    ),
                })
            DebateParticipant.objects.create(debate=pending_debate, user=request.user,
                                             side=desired_side, is_active=True)
            yes_pre2, no_pre2 = _pending_side_counts(pending_debate)
            return JsonResponse({'success': True, 'pre_joined': True,
                                 'message': 'Pre-joined! Waiting for the debate to start.',
                                 'debate_id': pending_debate.id,
                                 'pre_join_yes': yes_pre2,
                                 'pre_join_no': no_pre2})

        # No existing pending debate — create one
        pending_for_target = Debate.objects.filter(target=target_user, status='pending')
        total_pending_count = pending_for_target.count()
        if total_pending_count >= 10:
            return JsonResponse({
                'success': False,
                'queued': True,
                'error': 'This user already has 10 open challenges. Please wait for them to respond.'
            })

        debate = Debate.objects.create(
            id=str(uuid.uuid4()),
            comment=comment,
            post=comment.post,
            initiator=request.user,
            target=target_user,
            status='pending'
        )
        # Auto-add initiator as participant #1 on their side
        DebateParticipant.objects.create(debate=debate, user=request.user,
                                         side=desired_side or _opposite_side(comment.vote_type),
                                         is_active=True)
        Notification.objects.create(
            user=target_user,
            post=comment.post,
            notification_type='author_debate',
            message=f"@{request.user.username} challenged you to a debate.",
        )
        return JsonResponse({'success': True, 'message': 'Challenge sent! Others can now pre-join while you wait.',
                             'debate_id': debate.id})
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
            # Reuse limits from a previously accepted debate for the same context.
            if debate.post_id:
                prev_filter = {'post': debate.post}
            elif debate.poll_id:
                prev_filter = {'poll': debate.poll}
            else:
                prev_filter = {}
            if prev_filter:
                previous_accepted = Debate.objects.filter(
                    **prev_filter,
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
                yes_pre_req, no_pre_req = _pending_side_counts(debate)
                return JsonResponse({
                    'success': False,
                    'requires_counts': True,
                    'error': 'Please set participant limits for this debate first.',
                    'pre_join_yes': yes_pre_req,
                    'pre_join_no': no_pre_req,
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

        # Enforce minimums from pre-joiners already in the debate
        yes_pre, no_pre = _pending_side_counts(debate)
        if yes_supporters > 0 and yes_supporters < yes_pre:
            return JsonResponse({
                'success': False,
                'error': f'YES side already has {yes_pre} pre-joined participant(s). Limit cannot be below {yes_pre}.',
                'pre_join_yes': yes_pre,
                'pre_join_no': no_pre,
            })
        if no_supporters > 0 and no_supporters < no_pre:
            return JsonResponse({
                'success': False,
                'error': f'NO side already has {no_pre} pre-joined participant(s). Limit cannot be below {no_pre}.',
                'pre_join_yes': yes_pre,
                'pre_join_no': no_pre,
            })
        # If acceptor didn't specify limits, auto-floor to at least the current counts
        if yes_supporters == 0 and no_supporters == 0 and limits_source is None:
            # Still requires the acceptor to set explicit limits — handled above
            pass

        debate.status = 'accepted'
        debate.yes_supporters = yes_supporters
        debate.no_supporters = no_supporters
        debate.save()

        _ensure_debate_core_participants(debate)
        if not debate.end_controller_id:
            preferred = debate.comment.vote_type if debate.comment_id else 'yes'
            _set_end_controller_with_fallback(debate, preferred_side=preferred)

        if not debate.messages.exists():
            DebateMessage.objects.create(
                debate=debate,
                sender=request.user,
                content=f"Debate accepted. Suggested participants: Yes {yes_supporters}, No {no_supporters}."
            )

        # Notify the post author that a debate started on their post (only for post debates)
        if debate.post_id:
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
    """Reject a debate request (or reject a counter-proposal as the initiator)."""
    try:
        debate = Debate.objects.get(id=debate_id)
        if debate.status == 'countered' and debate.initiator == request.user:
            debate.status = 'rejected'
            debate.save()
        elif debate.target == request.user and debate.status in ('pending', 'countered'):
            debate.status = 'rejected'
            debate.save()
        else:
            messages.error(request, 'You cannot reject this debate.')
            return redirect('debate_inbox')
        messages.success(request, 'Debate rejected.')
    except Debate.DoesNotExist:
        messages.error(request, 'Debate not found.')
    return redirect('debate_inbox')


@login_required
@require_POST
def cancel_debate(request, debate_id):
    """Initiator cancels their own pending debate challenge."""
    try:
        debate = Debate.objects.get(id=debate_id, initiator=request.user, status='pending')
    except Debate.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Debate not found or cannot be cancelled.'}, status=404)
    debate.status = 'rejected'
    debate.save(update_fields=['status', 'updated_at'])
    return JsonResponse({'success': True})


@login_required
@require_POST
def counter_debate(request, debate_id):
    """Target user proposes a counter-topic/side instead of accepting or rejecting."""
    try:
        debate = Debate.objects.get(id=debate_id, target=request.user, status='pending')
    except Debate.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Debate not found or already actioned.'}, status=404)

    counter_topic = (request.POST.get('counter_topic') or '').strip()[:200]
    counter_side = (request.POST.get('counter_side') or '').strip()
    if not counter_topic:
        return JsonResponse({'success': False, 'error': 'A counter-topic is required.'}, status=400)
    if counter_side not in ('yes', 'no', ''):
        return JsonResponse({'success': False, 'error': 'Invalid side.'}, status=400)

    debate.counter_topic = counter_topic
    debate.counter_side = counter_side
    debate.status = 'countered'
    debate.save(update_fields=['counter_topic', 'counter_side', 'status', 'updated_at'])

    # Notify the initiator
    Notification.objects.create(
        user=debate.initiator,
        notif_type='debate_request',
        message=f"@{request.user.username} sent a counter-proposal for your debate challenge.",
        related_user=request.user,
    )
    _send_notification_email(
        debate.initiator,
        f"Counter-proposal from @{request.user.username}",
        f"@{request.user.username} wants to debate a different topic: \"{counter_topic}\".\n"
        f"Visit your debate inbox to accept or reject.",
        notif_type='debate_request',
    )
    return JsonResponse({'success': True})


@login_required
@require_POST
def accept_counter_debate(request, debate_id):
    """Initiator accepts the counter-proposal, turning it into a full debate."""
    try:
        debate = Debate.objects.get(id=debate_id, initiator=request.user, status='countered')
    except Debate.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Debate not found.'}, status=404)

    debate.status = 'accepted'
    debate.save(update_fields=['status', 'updated_at'])

    _ensure_debate_core_participants(debate)
    if not debate.end_controller_id:
        preferred = debate.comment.vote_type if debate.comment_id else 'yes'
        _set_end_controller_with_fallback(debate, preferred_side=preferred)

    if not debate.messages.exists():
        DebateMessage.objects.create(
            debate=debate,
            sender=request.user,
            content=f"Counter-proposal accepted. New topic: {debate.counter_topic}",
        )

    Notification.objects.create(
        user=debate.target,
        notif_type='debate_request',
        message=f"@{request.user.username} accepted your counter-proposal!",
        related_user=request.user,
    )
    return JsonResponse({'success': True, 'redirect_url': f'/debates/{debate.id}/chat/'})


@login_required
def debate_inbox(request):
    participations = DebateParticipant.objects.filter(
        user=request.user
    ).select_related(
        'debate', 'debate__post', 'debate__initiator', 'debate__target',
        'debate__initiator__profile', 'debate__target__profile',
    ).order_by('-debate__created_at')

    debates_with_status = []
    for p in participations:
        d = p.debate
        opponent = d.target if d.initiator == request.user else d.initiator
        expires_at = (d.created_at + timedelta(hours=48)) if d.status == 'pending' else None
        debates_with_status.append({
            'debate': d,
            'opponent': opponent,
            'side': p.side,
            'status': d.status,
            'post': d.post,
            'expires_at': expires_at,
            'is_initiator': d.initiator == request.user,
        })

    pending_count = sum(1 for x in debates_with_status if x['status'] == 'pending')

    return render(request, 'frontend/debate_inbox.html', {
        'debates': debates_with_status,
        'pending_count': pending_count,
    })


@login_required
@xframe_options_sameorigin
def debate_chat(request, debate_id):
    """Two-person debate chat room"""
    debate = get_object_or_404(
        Debate.objects.select_related('initiator', 'target', 'post', 'poll'),
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

    # Record this user as a current spectator/viewer
    if request.user.is_authenticated:
        DebateView.objects.update_or_create(debate=debate, user=request.user, defaults={})

    active_cutoff = timezone.now() - timedelta(seconds=90)
    spectator_count = debate.spectators.filter(last_seen__gte=active_cutoff).count()

    opponent = debate.target if request.user == debate.initiator else debate.initiator

    # Observer votes tally for sidebar
    from discussions.models import ObserverVote as _OV
    obs_yes = _OV.objects.filter(debate=debate, winner_side='yes').count()
    obs_no = _OV.objects.filter(debate=debate, winner_side='no').count()
    user_is_participant = False
    user_obs_vote = None
    if request.user.is_authenticated:
        user_is_participant = DebateParticipant.objects.filter(debate=debate, user=request.user).exists()
        ov = _OV.objects.filter(debate=debate, voter=request.user).first()
        user_obs_vote = ov.winner_side if ov else None

    from django.urls import reverse as _reverse
    if debate.post_id:
        _context_url = _reverse('discussion', args=[str(debate.post_id)])
        _context_title = debate.post.title if debate.post else ''
    elif debate.poll_id:
        _context_url = _reverse('poll_detail', args=[str(debate.poll_id)])
        _context_title = debate.poll.question if debate.poll else ''
    else:
        _context_url = _reverse('index')
        _context_title = ''

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
        'spectator_count': spectator_count,
        'obs_yes': obs_yes,
        'obs_no': obs_no,
        'user_is_participant': user_is_participant,
        'user_obs_vote': user_obs_vote,
        'context_url': _context_url,
        'context_title': _context_title,
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

    # Refresh viewer's last_seen for spectator count
    DebateView.objects.update_or_create(debate=debate, user=request.user, defaults={})
    active_cutoff = timezone.now() - timedelta(seconds=90)
    spectator_count = debate.spectators.filter(last_seen__gte=active_cutoff).count()

    obs_yes = ObserverVote.objects.filter(debate=debate, winner_side='yes').count()
    obs_no = ObserverVote.objects.filter(debate=debate, winner_side='no').count()

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
        'last_message_at': raw_messages[-1].created_at.isoformat() if raw_messages else None,
        'spectator_count': spectator_count,
        'obs_yes': obs_yes,
        'obs_no': obs_no,
        'opponent_last_read_msg_id': (
            DebateParticipant.objects.filter(debate=debate, user=opponent)
            .values_list('last_read_message_id', flat=True).first() or 0
        ),
    })


@login_required
@require_POST
def mark_debate_read(request, debate_id):
    """Mark all messages in a debate as read up to the latest message."""
    debate = get_object_or_404(Debate, id=debate_id)
    participation = get_object_or_404(DebateParticipant, debate=debate, user=request.user)
    latest_id = DebateMessage.objects.filter(debate=debate).aggregate(
        max_id=Max('id')
    )['max_id'] or 0
    if latest_id > participation.last_read_message_id:
        participation.last_read_message_id = latest_id
        participation.save(update_fields=['last_read_message_id'])
    return JsonResponse({'success': True, 'last_read': latest_id})


@login_required
@require_POST
def save_theme_preference(request):
    """Persist the user's dark/light theme choice to their profile."""
    theme = (request.POST.get('theme') or '').strip().lower()
    if theme not in ('light', 'dark'):
        return JsonResponse({'success': False, 'error': 'Invalid theme'}, status=400)
    try:
        profile = request.user.profile
        profile.theme = theme
        profile.save(update_fields=['theme'])
    except Exception:
        pass
    return JsonResponse({'success': True})


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
    debate_title = debate.context_title
    for moderator in moderators:
        Notification.objects.create(
            user=moderator,
            post=debate.post if debate.post_id else None,
            notification_type='moderation_alert',
            message=(
                f"Message report in debate '{debate_title}': "
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
        post=debate.post if debate.post_id else None,
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
def moderate_post_report(request, report_id):
    """Moderator review action for reported posts."""
    if not _is_configured_moderator(request.user):
        return JsonResponse({'success': False, 'error': 'Only configured moderators can review post reports.'}, status=403)

    report = get_object_or_404(
        PostReport.objects.select_related('post', 'post__user', 'reporter'),
        id=report_id,
    )

    if report.status != 'pending':
        return JsonResponse({'success': True, 'message': 'This post report was already reviewed.'})

    action = (request.POST.get('action') or '').strip().lower()
    if action not in {'dismiss', 'warn_author', 'delete_post'}:
        return JsonResponse({'success': False, 'error': 'Invalid moderation action.'}, status=400)

    post = report.post
    author = post.user

    if action == 'dismiss':
        report.status = 'dismissed'
        report.save(update_fields=['status', 'updated_at'])
        return JsonResponse({'success': True, 'message': 'Post report dismissed.'})

    if action == 'delete_post':
        post.is_deleted_by_moderation = True
        post.moderation_reason = f'Deleted following report #{report_id}: {report.get_reason_display()}'
        post.save(update_fields=['is_deleted_by_moderation', 'moderation_reason', 'updated_at'])
        Notification.objects.create(
            user=author,
            post=post,
            notification_type='moderation_warning',
            message='Your post was removed after a moderation review. Please follow community guidelines.',
        )
        report.status = 'actioned'
        report.save(update_fields=['status', 'updated_at'])
        return JsonResponse({'success': True, 'message': 'Post removed and author notified.'})

    # warn_author
    Notification.objects.create(
        user=author,
        post=post,
        notification_type='moderation_warning',
        message=(
            f'Your post "{post.title[:80]}" was reported and reviewed by moderators. '
            'Please follow community guidelines.'
        ),
    )
    report.status = 'reviewed'
    report.save(update_fields=['status', 'updated_at'])
    return JsonResponse({'success': True, 'message': 'Author warned and post report marked reviewed.'})


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
@login_required
def debate_status(request, debate_id):
    """Lightweight endpoint polled by pre-joiners while waiting for the debate to be accepted."""
    try:
        debate = Debate.objects.only('id', 'status', 'initiator_id').get(id=debate_id)
        # Any participant (not just initiator) may poll
        if not DebateParticipant.objects.filter(debate=debate, user=request.user).exists():
            return JsonResponse({'error': 'Not found'}, status=404)
    except Debate.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)
    data = {'status': debate.status}
    if debate.status == 'accepted':
        data['redirect_url'] = f'/debates/{debate.id}/chat/'
    elif debate.status == 'pending':
        yes_pre, no_pre = _pending_side_counts(debate)
        data['pre_join_yes'] = yes_pre
        data['pre_join_no'] = no_pre
    return JsonResponse(data)


def debate_info(request, debate_id):
    """Return debate metadata as JSON for the floating chat manager"""
    debate = get_object_or_404(
        Debate.objects.select_related('initiator', 'target', 'post', 'poll'),
        id=debate_id
    )
    _ensure_debate_core_participants(debate)
    participation = DebateParticipant.objects.filter(debate=debate, user=request.user).first()

    if not participation:
        return JsonResponse({'success': False, 'error': 'Access denied'}, status=403)

    opponent = debate.target if request.user == debate.initiator else debate.initiator
    can_post = debate.status == 'accepted' and participation.is_active and not participation.is_banned

    # Build the URL the user should land on when clicking the chat title.
    if debate.post_id:
        context_url = f'/discussion/{debate.post_id}/'
    elif debate.poll_id:
        context_url = f'/polls/{debate.poll_id}/'
    elif debate.review_comment_id:
        try:
            review_id = debate.review_comment.review_id
            context_url = f'/reviews/{review_id}/'
        except Exception:
            context_url = ''
    else:
        context_url = ''

    last_msg = DebateMessage.objects.filter(debate=debate, is_system=False).order_by('-created_at').first()
    last_message_at = last_msg.created_at.isoformat() if last_msg else None

    return JsonResponse({
        'success': True,
        'debate': {
            'id': str(debate.id),
            'title': debate.context_title,
            'opponent': opponent.username,
            'opponent_avatar': _safe_avatar_url(opponent),
            'active_participants': _active_participants_payload(debate, request.user),
            'yes_supporters': debate.yes_supporters,
            'no_supporters': debate.no_supporters,
            'post_id': str(debate.post_id) if debate.post_id else '',
            'context_url': context_url,
            'last_message_at': last_message_at,
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

    poll_ids = [p.id for p in page_obj]
    user_poll_votes = {}
    user_poll_actions = {}
    user_poll_follows = set()
    if request.user.is_authenticated:
        for v in PollVote.objects.filter(poll_id__in=poll_ids, user=request.user):
            user_poll_votes[v.poll_id] = v
        for a in PollAction.objects.filter(poll_id__in=poll_ids, user=request.user):
            user_poll_actions.setdefault(a.poll_id, set()).add(a.action)
        user_poll_follows = set(PollFollow.objects.filter(poll_id__in=poll_ids, user=request.user).values_list('poll_id', flat=True))

    from django.db.models import Count as _Count
    like_counts = {r['poll_id']: r['c'] for r in PollAction.objects.filter(poll_id__in=poll_ids, action='like').values('poll_id').annotate(c=_Count('id'))}
    save_counts = {r['poll_id']: r['c'] for r in PollAction.objects.filter(poll_id__in=poll_ids, action='save').values('poll_id').annotate(c=_Count('id'))}
    repost_counts = {r['poll_id']: r['c'] for r in PollAction.objects.filter(poll_id__in=poll_ids, action='repost').values('poll_id').annotate(c=_Count('id'))}
    follow_counts = {r['poll_id']: r['c'] for r in PollFollow.objects.filter(poll_id__in=poll_ids).values('poll_id').annotate(c=_Count('id'))}

    # Pre-fetch all option vote counts in one query to avoid N+1
    _poll_ids_page = [p.id for p in page_obj]
    _opt_vote_counts = {
        row['id']: row['vc']
        for row in PollOption.objects.filter(poll_id__in=_poll_ids_page)
        .annotate(vc=Count('votes'))
        .values('id', 'vc')
    }
    _poll_total_votes = {
        row['poll_id']: row['tv']
        for row in PollVote.objects.filter(poll_id__in=_poll_ids_page)
        .values('poll_id')
        .annotate(tv=Count('id'))
    }

    polls_data = []
    for poll in page_obj:
        opts = list(poll.options.all())
        total = _poll_total_votes.get(poll.id, 0)
        user_vote = user_poll_votes.get(poll.id)
        u_actions = user_poll_actions.get(poll.id, set())
        opt_data = []
        for opt in opts:
            cnt = _opt_vote_counts.get(opt.id, 0)
            pct = round(cnt / total * 100, 1) if total > 0 else 0
            opt_data.append({'option': opt, 'vote_count': cnt, 'percentage': pct})
        polls_data.append({
            'poll': poll,
            'options': opts,
            'option_data': opt_data,
            'total_votes': total,
            'user_vote': user_vote,
            'like_count': like_counts.get(poll.id, 0),
            'save_count': save_counts.get(poll.id, 0),
            'repost_count': repost_counts.get(poll.id, 0),
            'follow_count': follow_counts.get(poll.id, 0),
            'is_liked': 'like' in u_actions,
            'is_saved': 'save' in u_actions,
            'is_reposted': 'repost' in u_actions,
            'is_following': poll.id in user_poll_follows,
        })

    return render(request, 'frontend/polls_list.html', {
        'polls_data': polls_data,
        'page_obj': page_obj,
        'categories': get_frontend_categories(),
        'active_category': category_filter,
    })


@login_required
def create_poll(request):
    """Poll creation page."""

    categories = [{'name': c[0]} for c in CATEGORY_CHOICES]

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        option1 = request.POST.get('option1', '').strip()
        option2 = request.POST.get('option2', '').strip()
        category = request.POST.get('category', '').strip()
        hashtags_raw = request.POST.get('hashtags', '').strip()
        description = request.POST.get('description', '').strip()
        expires_at_raw = request.POST.get('expires_at', '').strip()

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

        expires_at = None
        if expires_at_raw:
            from datetime import datetime
            try:
                expires_at = timezone.make_aware(datetime.fromisoformat(expires_at_raw))
                if expires_at <= timezone.now():
                    errors['expires_at'] = 'Expiry date must be in the future.'
            except ValueError:
                errors['expires_at'] = 'Invalid date format.'

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

        is_anonymous = request.POST.get('is_anonymous') == 'on'

        poll = Poll.objects.create(
            id=str(uuid.uuid4()),
            user=request.user,
            title=title,
            description=description,
            category=category,
            hashtags=','.join(Post.parse_hashtags(hashtags_raw)),
            expires_at=expires_at,
            is_anonymous=is_anonymous,
        )
        PollOption.objects.create(poll=poll, text=option1, order=0)
        PollOption.objects.create(poll=poll, text=option2, order=1)

        _maybe_award_achievements(request.user)
        return redirect('poll_detail', poll_id=poll.id)

    return render(request, 'frontend/create_poll.html', {'categories': categories})


def poll_detail(request, poll_id):
    """Poll detail page with vote chart and side comments."""
    poll = get_object_or_404(Poll, id=poll_id, is_deleted_by_moderation=False)
    options = list(poll.options.annotate(vote_count=Count('votes')))
    total_votes = sum(opt.vote_count for opt in options)

    user_vote = None
    if request.user.is_authenticated:
        try:
            user_vote = PollVote.objects.get(poll=poll, user=request.user)
        except PollVote.DoesNotExist:
            pass

    now = timezone.now()
    online_cutoff = now - timedelta(minutes=5)

    # Pre-build debate state lookups for all poll comments
    debate_lookup = {}
    completed_lookup = {}
    blocked_pc_ids = set()
    if request.user.is_authenticated and user_vote:
        all_comment_owner_ids = list(
            PollComment.objects.filter(
                poll=poll, is_deleted_by_moderation=False
            ).exclude(user=request.user).values_list('user_id', flat=True).distinct()
        )
        if all_comment_owner_ids:
            accepted_debates = list(
                Debate.objects.filter(
                    poll=poll,
                    target_id__in=all_comment_owner_ids,
                    status='accepted',
                ).order_by('target_id', '-updated_at')
            )
            latest_accepted = {}
            for d in accepted_debates:
                if d.target_id not in latest_accepted:
                    latest_accepted[d.target_id] = d

            if latest_accepted:
                d_ids = [d.id for d in latest_accepted.values()]
                active_side_counts = {
                    (item['debate_id'], item['side']): item['total']
                    for item in DebateParticipant.objects.filter(
                        debate_id__in=d_ids, is_active=True
                    ).values('debate_id', 'side').annotate(total=Count('id'))
                }
                user_participation = {
                    p.debate_id: p
                    for p in DebateParticipant.objects.filter(
                        debate_id__in=d_ids, user=request.user
                    )
                }
                for target_id, d in latest_accepted.items():
                    part = user_participation.get(d.id)
                    if part:
                        mode, label = 'view', 'View Debate'
                    else:
                        yes_act = active_side_counts.get((d.id, 'yes'), 0)
                        no_act = active_side_counts.get((d.id, 'no'), 0)
                        full = (d.yes_supporters > 0 and d.no_supporters > 0
                                and yes_act >= d.yes_supporters and no_act >= d.no_supporters)
                        mode = 'view' if full else 'join'
                        label = 'View Debate' if full else 'Join Debate'
                    debate_lookup[target_id] = {'id': d.id, 'mode': mode, 'label': label, 'chat_url': f'/debates/{d.id}/chat/'}

            for d in Debate.objects.filter(
                poll=poll, target_id__in=all_comment_owner_ids, status='completed'
            ).order_by('target_id', '-updated_at'):
                if d.target_id not in completed_lookup:
                    completed_lookup[d.target_id] = d

            blocked_pc_ids = set(
                DebateParticipant.objects.filter(
                    user=request.user, is_banned=True, debate__poll=poll
                ).values_list('debate__poll_comment_id', flat=True)
            )

    option_data = []
    for opt in options:
        cnt = opt.vote_count
        pct = round(cnt / total_votes * 100, 1) if total_votes > 0 else 0
        comments = opt.comments.filter(is_deleted_by_moderation=False).select_related('user', 'user__profile')
        comments_with_reaction = []
        for c in comments:
            user_reaction = None
            if request.user.is_authenticated:
                try:
                    r = PollCommentReaction.objects.get(comment=c, user=request.user)
                    user_reaction = r.reaction
                except PollCommentReaction.DoesNotExist:
                    pass
            try:
                p = c.user.profile
                last_seen = p.last_seen
                avatar_url = p.get_picture_url
            except Exception:
                last_seen = None
                avatar_url = None

            # Debate state for this comment
            debate_state = debate_lookup.get(c.user_id)
            completed_state = completed_lookup.get(c.user_id) if request.user.is_authenticated else None
            show_debate = False
            debate_mode = debate_state['mode'] if debate_state else 'start'
            debate_label = debate_state['label'] if debate_state else 'Start Debate'
            debate_chat_url = debate_state['chat_url'] if debate_state else ''
            if request.user.is_authenticated and request.user != c.user:
                user_chose_different = user_vote and user_vote.option_id != c.option_id
                show_debate = bool(debate_state) or user_chose_different
            is_blocked = c.id in blocked_pc_ids
            if is_blocked and debate_mode == 'start':
                debate_mode = 'blocked'
                debate_label = 'Debate Blocked'
                show_debate = True

            comments_with_reaction.append({
                'comment': c,
                'user_reaction': user_reaction,
                'is_online': bool(last_seen and last_seen >= online_cutoff),
                'presence_label': _presence_label(last_seen, now=now),
                'avatar_url': avatar_url,
                'show_debate_action': show_debate,
                'debate_action_mode': debate_mode,
                'debate_action_label': debate_label,
                'debate_chat_url': debate_chat_url,
                'show_debate_view_link': bool(completed_state and not debate_state),
                'debate_view_url': f'/debates/{completed_state.id}/chat/' if (completed_state and not debate_state) else '',
            })
        option_data.append({
            'option': opt,
            'vote_count': cnt,
            'percentage': pct,
            'comments': comments_with_reaction,
        })

    user_ranked_options = set()
    if request.user.is_authenticated and poll.allows_ranked_choice:
        from discussions.models import RankedChoiceVote as _RCV
        user_ranked_options = set(
            _RCV.objects.filter(user=request.user, poll=poll).values_list('option_id', flat=True)
        )

    # Poll predictions
    user_prediction = None
    prediction_stats = {}
    if request.user.is_authenticated:
        user_prediction = PollPrediction.objects.filter(poll=poll, user=request.user).first()
    if poll.is_expired or not poll.is_active:
        total_preds = PollPrediction.objects.filter(poll=poll).count()
        for opt in options:
            cnt = PollPrediction.objects.filter(poll=poll, predicted_option=opt).count()
            prediction_stats[opt.id] = {
                'count': cnt,
                'pct': round(cnt / total_preds * 100, 1) if total_preds > 0 else 0,
            }
    can_predict = (
        request.user.is_authenticated
        and not (poll.is_expired or not poll.is_active)
        and user_prediction is None
        and not user_vote
    )

    return render(request, 'frontend/poll.html', {
        'poll': poll,
        'options': options,
        'option_data': option_data,
        'total_votes': total_votes,
        'user_vote': user_vote,
        'voted_option_id': str(user_vote.option_id) if user_vote else None,
        'show_voters': not poll.is_anonymous,
        'user_has_ranked': bool(user_ranked_options),
        'user_prediction': user_prediction,
        'prediction_stats': prediction_stats,
        'can_predict': can_predict,
    })


@require_POST
def poll_vote(request, poll_id):
    """AJAX endpoint — cast or change a poll vote."""
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required'}, status=401)

    poll = get_object_or_404(Poll, id=poll_id, is_deleted_by_moderation=False)

    if poll.user == request.user:
        return JsonResponse({'error': 'Cannot vote on your own poll.'}, status=403)

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

    try:
        PollVote.objects.create(user=request.user, poll=poll, option=option)
    except IntegrityError:
        return JsonResponse({'error': 'You have already voted on this poll.'}, status=400)

    opts_annotated = list(poll.options.annotate(vote_count=Count('votes')))
    total = sum(o.vote_count for o in opts_annotated)
    options_out = []
    for opt in opts_annotated:
        cnt = opt.vote_count
        pct = round(cnt / total * 100, 1) if total > 0 else 0
        options_out.append({'id': opt.id, 'text': opt.text, 'vote_count': cnt, 'percentage': pct})

    return JsonResponse({'success': True, 'voted_option_id': option.id, 'total_votes': total, 'options': options_out})


@login_required
@require_POST
def poll_ranked_vote(request, poll_id):
    """Submit ranked-choice preferences for a poll that allows_ranked_choice."""
    from discussions.models import RankedChoiceVote as _RCV
    poll = get_object_or_404(Poll, id=poll_id, is_deleted_by_moderation=False, allows_ranked_choice=True)
    if poll.is_expired or not poll.is_active:
        return JsonResponse({'success': False, 'error': 'Poll is closed'}, status=400)
    try:
        data = json.loads(request.body)
        rankings = data.get('rankings', [])  # [{option_id, rank}, ...]
    except (json.JSONDecodeError, ValueError, TypeError):
        return JsonResponse({'success': False, 'error': 'Invalid request'}, status=400)
    if not rankings or len(rankings) > 10:
        return JsonResponse({'success': False, 'error': 'Provide 1–10 rankings'}, status=400)
    option_ids = [r.get('option_id') for r in rankings]
    ranks = [r.get('rank') for r in rankings]
    if len(set(ranks)) != len(ranks):
        return JsonResponse({'success': False, 'error': 'Duplicate ranks not allowed'}, status=400)
    valid_options = set(poll.options.values_list('id', flat=True))
    if not all(oid in valid_options for oid in option_ids):
        return JsonResponse({'success': False, 'error': 'Invalid option'}, status=400)
    _RCV.objects.filter(user=request.user, poll=poll).delete()
    _RCV.objects.bulk_create([
        _RCV(user=request.user, poll=poll,
             option_id=r['option_id'], rank=r['rank'])
        for r in rankings
    ])
    return JsonResponse({'success': True, 'ranked': len(rankings)})


@login_required
@require_POST
def predict_poll(request, poll_id):
    """Submit or update a prediction for which option will win the poll."""
    poll = get_object_or_404(Poll, id=poll_id, is_deleted_by_moderation=False)
    if poll.is_expired or not poll.is_active:
        return JsonResponse({'success': False, 'error': 'This poll has already closed.'}, status=400)
    if PollVote.objects.filter(poll=poll, user=request.user).exists():
        return JsonResponse({'success': False, 'error': 'You have already voted — predictions are for before voting.'}, status=400)

    try:
        option_id = int(request.POST.get('option_id', 0))
    except (ValueError, TypeError):
        return JsonResponse({'success': False, 'error': 'Invalid option.'}, status=400)

    option = get_object_or_404(PollOption, id=option_id, poll=poll)
    prediction, created = PollPrediction.objects.update_or_create(
        poll=poll, user=request.user,
        defaults={'predicted_option': option},
    )
    return JsonResponse({'success': True, 'option_id': option.id, 'option_text': option.text, 'created': created})


def _resolve_poll_predictions(poll):
    """Called when a poll closes: mark each prediction correct/incorrect and award rep."""
    options = list(poll.options.annotate(vote_count=Count('votes')).order_by('-vote_count'))
    if not options:
        return
    winning_option = options[0]
    # Only award if there's a clear winner (not tied)
    if len(options) > 1 and options[0].vote_count == options[1].vote_count:
        winning_option = None

    predictions = PollPrediction.objects.filter(poll=poll, was_correct__isnull=True).select_related('user__profile')
    for pred in predictions:
        if winning_option is None:
            pred.was_correct = False  # draw — no reward
        else:
            pred.was_correct = (pred.predicted_option_id == winning_option.id)
        pred.save(update_fields=['was_correct'])
        if pred.was_correct:
            try:
                prof = pred.user.profile
                prof.reputation_score = max(0, prof.reputation_score + 5)
                prof.save(update_fields=['reputation_score'])
            except Exception:
                pass


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

    # Mention notifications in polls
    usernames = _parse_mentions(content)
    if usernames:
        mentioned_users = User.objects.filter(username__in=usernames).exclude(id=request.user.id)
        for mu in mentioned_users:
            Notification.objects.create(
                user=mu,
                post=None,
                notification_type='mention',
                message=f'@{request.user.username} mentioned you in a poll comment on "{poll.title}".',
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


# ─── Question Views ─────────────────────────────────────────────────────────────

def questions_list(request):
    category_filter = request.GET.get('category', '').strip()
    search_query = request.GET.get('q', '').strip()
    qs = Question.objects.filter(is_deleted_by_moderation=False)
    if category_filter:
        qs = qs.filter(category=category_filter)
    if search_query:
        qs = qs.filter(Q(title__icontains=search_query) | Q(content__icontains=search_query))

    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get('page', 1))
    q_ids = [q.id for q in page_obj]

    user_q_actions = {}
    user_q_follows = set()
    if request.user.is_authenticated:
        for a in QuestionAction.objects.filter(question_id__in=q_ids, user=request.user):
            user_q_actions.setdefault(a.question_id, set()).add(a.action)
        user_q_follows = set(QuestionFollow.objects.filter(question_id__in=q_ids, user=request.user).values_list('question_id', flat=True))

    from django.db.models import Count as _Count
    like_counts = {r['question_id']: r['c'] for r in QuestionAction.objects.filter(question_id__in=q_ids, action='like').values('question_id').annotate(c=_Count('id'))}
    save_counts = {r['question_id']: r['c'] for r in QuestionAction.objects.filter(question_id__in=q_ids, action='save').values('question_id').annotate(c=_Count('id'))}
    repost_counts = {r['question_id']: r['c'] for r in QuestionAction.objects.filter(question_id__in=q_ids, action='repost').values('question_id').annotate(c=_Count('id'))}
    follow_counts = {r['question_id']: r['c'] for r in QuestionFollow.objects.filter(question_id__in=q_ids).values('question_id').annotate(c=_Count('id'))}

    questions_data = []
    for q in page_obj:
        u_actions = user_q_actions.get(q.id, set())
        questions_data.append({
            'question': q,
            'like_count': like_counts.get(q.id, 0),
            'save_count': save_counts.get(q.id, 0),
            'repost_count': repost_counts.get(q.id, 0),
            'follow_count': follow_counts.get(q.id, 0),
            'is_liked': 'like' in u_actions,
            'is_saved': 'save' in u_actions,
            'is_reposted': 'repost' in u_actions,
            'is_following': q.id in user_q_follows,
        })

    return render(request, 'frontend/questions_list.html', {
        'questions_data': questions_data,
        'page_obj': page_obj,
        'categories': get_frontend_categories(),
        'active_category': category_filter,
        'search_query': search_query,
    })


def ask_general_question(request):
    if not request.user.is_authenticated:
        from django.urls import reverse
        return redirect(f"{reverse('login')}?next={request.path}")

    categories = [{'name': c[0]} for c in CATEGORY_CHOICES]

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        content = request.POST.get('content', '').strip()
        category = request.POST.get('category', '').strip()
        hashtags_raw = request.POST.get('hashtags', '').strip()

        errors = {}
        if not title:
            errors['title'] = 'Question title is required.'
        elif len(title) > 255:
            errors['title'] = 'Title must be 255 characters or fewer.'
        if not category:
            errors['category'] = 'Category is required.'

        if not errors:
            combined = f"{title} {content}".strip()
            if check_content_moderation(combined):
                errors['title'] = 'Your question contains inappropriate content.'

        if errors:
            return render(request, 'frontend/ask_general_question.html', {
                'categories': categories,
                'errors': errors,
                'form_data': request.POST,
            })

        question = Question.objects.create(
            id=str(uuid.uuid4()),
            user=request.user,
            title=title,
            content=content,
            category=category,
            hashtags=','.join(Post.parse_hashtags(hashtags_raw)),
        )
        return redirect('question_detail', question_id=question.id)

    return render(request, 'frontend/ask_general_question.html', {'categories': categories})


def question_detail(request, question_id):
    question = get_object_or_404(Question, id=question_id, is_deleted_by_moderation=False)
    answers = question.answers.filter(is_deleted_by_moderation=False).order_by('-upvotes', 'created_at')

    user_answer = None
    user_votes = {}
    if request.user.is_authenticated:
        user_answer = question.answers.filter(user=request.user, is_deleted_by_moderation=False).first()
        voted_answer_ids = AnswerVote.objects.filter(
            user=request.user, answer__question=question
        ).values_list('answer_id', 'vote')
        user_votes = {aid: v for aid, v in voted_answer_ids}

    answers_with_data = []
    for ans in answers:
        answers_with_data.append({
            'answer': ans,
            'user_vote': user_votes.get(ans.id),
            'is_best': question.best_answer_id == ans.id,
        })

    return render(request, 'frontend/question_detail.html', {
        'question': question,
        'answers_with_data': answers_with_data,
        'user_answer': user_answer,
        'total_answers': answers.count(),
    })


@require_POST
def post_answer(request, question_id):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required'}, status=401)

    question = get_object_or_404(Question, id=question_id, is_deleted_by_moderation=False)

    try:
        data = json.loads(request.body)
        content = (data.get('content') or '').strip()
    except (json.JSONDecodeError, TypeError):
        return JsonResponse({'error': 'Invalid request'}, status=400)

    if not content:
        return JsonResponse({'error': 'Answer cannot be empty.'}, status=400)

    if question.answers.filter(user=request.user, is_deleted_by_moderation=False).exists():
        return JsonResponse({'error': 'You have already answered this question.'}, status=400)

    if check_content_moderation(content):
        return JsonResponse({'error': 'Your answer contains inappropriate content.'}, status=400)

    answer = Answer.objects.create(
        id=str(uuid.uuid4()),
        question=question,
        user=request.user,
        content=content,
    )
    Question.objects.filter(id=question_id).update(answer_count=F('answer_count') + 1)

    return JsonResponse({
        'success': True,
        'answer': {
            'id': answer.id,
            'content': answer.content,
            'username': request.user.username,
            'upvotes': 0,
            'downvotes': 0,
            'created_at': answer.created_at.strftime('%b %d, %Y'),
        },
    })


@require_POST
def vote_answer(request, answer_id):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required'}, status=401)

    answer = get_object_or_404(Answer, id=answer_id, is_deleted_by_moderation=False)

    if answer.user_id == request.user.id:
        return JsonResponse({'error': 'Cannot vote on your own answer.'}, status=403)

    try:
        data = json.loads(request.body)
        vote_type = data.get('vote', 'up')
    except (json.JSONDecodeError, TypeError):
        return JsonResponse({'error': 'Invalid request'}, status=400)

    if vote_type not in ('up', 'down'):
        return JsonResponse({'error': 'Invalid vote type.'}, status=400)

    vote_obj, created = AnswerVote.objects.get_or_create(
        answer=answer, user=request.user, defaults={'vote': vote_type}
    )
    if not created:
        if vote_obj.vote == vote_type:
            vote_obj.delete()
            action = 'removed'
        else:
            vote_obj.vote = vote_type
            vote_obj.save(update_fields=['vote', 'updated_at'])
            action = 'changed'
    else:
        action = 'added'

    upvotes = AnswerVote.objects.filter(answer=answer, vote='up').count()
    downvotes = AnswerVote.objects.filter(answer=answer, vote='down').count()
    Answer.objects.filter(id=answer_id).update(upvotes=upvotes, downvotes=downvotes)

    return JsonResponse({'success': True, 'action': action, 'upvotes': upvotes, 'downvotes': downvotes})


@require_POST
def mark_best_answer(request, answer_id):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required'}, status=401)

    answer = get_object_or_404(Answer, id=answer_id, is_deleted_by_moderation=False)
    question = answer.question

    if question.user_id != request.user.id:
        return JsonResponse({'error': 'Only the question author can mark the best answer.'}, status=403)

    if question.best_answer_id == answer.id:
        question.best_answer = None
    else:
        question.best_answer = answer
    question.save(update_fields=['best_answer', 'updated_at'])

    return JsonResponse({'success': True, 'best_answer_id': str(question.best_answer_id) if question.best_answer_id else None})


# ─── Review Views ────────────────────────────────────────────────────────────────

def reviews_list(request):
    type_filter = request.GET.get('type', '').strip()
    search_query = request.GET.get('q', '').strip()
    qs = Review.objects.filter(is_deleted_by_moderation=False)
    if type_filter:
        qs = qs.filter(subject_type=type_filter)
    if search_query:
        qs = qs.filter(Q(subject__icontains=search_query) | Q(content__icontains=search_query))

    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    rev_ids = [r.id for r in page_obj]
    user_rev_reactions = {}
    user_rev_actions = {}
    user_rev_follows = set()
    if request.user.is_authenticated:
        for r in ReviewReaction.objects.filter(review_id__in=rev_ids, user=request.user):
            user_rev_reactions[r.review_id] = r.reaction
        for a in ReviewAction.objects.filter(review_id__in=rev_ids, user=request.user):
            user_rev_actions.setdefault(a.review_id, set()).add(a.action)
        user_rev_follows = set(ReviewFollow.objects.filter(review_id__in=rev_ids, user=request.user).values_list('review_id', flat=True))

    from django.db.models import Count as _Count
    like_counts = {r['review_id']: r['c'] for r in ReviewAction.objects.filter(review_id__in=rev_ids, action='like').values('review_id').annotate(c=_Count('id'))}
    save_counts = {r['review_id']: r['c'] for r in ReviewAction.objects.filter(review_id__in=rev_ids, action='save').values('review_id').annotate(c=_Count('id'))}
    repost_counts = {r['review_id']: r['c'] for r in ReviewAction.objects.filter(review_id__in=rev_ids, action='repost').values('review_id').annotate(c=_Count('id'))}
    follow_counts = {r['review_id']: r['c'] for r in ReviewFollow.objects.filter(review_id__in=rev_ids).values('review_id').annotate(c=_Count('id'))}

    reviews_data = []
    for review in page_obj:
        u_actions = user_rev_actions.get(review.id, set())
        reviews_data.append({
            'review': review,
            'user_reaction': user_rev_reactions.get(review.id),
            'like_count': like_counts.get(review.id, 0),
            'save_count': save_counts.get(review.id, 0),
            'repost_count': repost_counts.get(review.id, 0),
            'follow_count': follow_counts.get(review.id, 0),
            'is_liked': 'like' in u_actions,
            'is_saved': 'save' in u_actions,
            'is_reposted': 'repost' in u_actions,
            'is_following': review.id in user_rev_follows,
        })

    return render(request, 'frontend/reviews_list.html', {
        'reviews_data': reviews_data,
        'page_obj': page_obj,
        'review_types': REVIEW_TYPE_CHOICES,
        'active_type': type_filter,
        'search_query': search_query,
    })


@login_required
def create_review(request):
    subject_types = Review.SUBJECT_TYPE_CHOICES
    categories = [{'name': c[0]} for c in CATEGORY_CHOICES]

    if request.method == 'POST':
        subject = request.POST.get('subject', '').strip()
        subject_type = request.POST.get('subject_type', '').strip()
        rating_raw = request.POST.get('rating', '').strip()
        content = request.POST.get('content', '').strip()
        category = request.POST.get('category', '').strip()
        hashtags_raw = request.POST.get('hashtags', '').strip()

        errors = {}
        if not subject:
            errors['subject'] = 'Subject is required.'
        if not subject_type:
            errors['subject_type'] = 'Subject type is required.'
        if not rating_raw:
            errors['rating'] = 'Rating is required.'
        else:
            try:
                rating = int(rating_raw)
                if rating < 1 or rating > 5:
                    errors['rating'] = 'Rating must be between 1 and 5.'
            except ValueError:
                errors['rating'] = 'Invalid rating.'
                rating = None
        if not content:
            errors['content'] = 'Review content is required.'
        if not category:
            errors['category'] = 'Category is required.'

        if not errors:
            combined = f"{subject} {content}".strip()
            if check_content_moderation(combined):
                errors['content'] = 'Your review contains inappropriate content.'

        if errors:
            return render(request, 'frontend/create_review.html', {
                'subject_types': subject_types,
                'categories': categories,
                'errors': errors,
                'form_data': request.POST,
            })

        review = Review.objects.create(
            id=str(uuid.uuid4()),
            user=request.user,
            subject=subject,
            subject_type=subject_type,
            rating=int(rating_raw),
            content=content,
            category=category,
            hashtags=','.join(Post.parse_hashtags(hashtags_raw)),
            is_verified=False,
        )
        return redirect('review_detail', review_id=review.id)

    return render(request, 'frontend/create_review.html', {
        'subject_types': subject_types,
        'categories': categories,
    })


def review_detail(request, review_id):
    review = get_object_or_404(Review, id=review_id, is_deleted_by_moderation=False)
    comments = review.comments.filter(is_deleted_by_moderation=False).select_related('user', 'user__profile')

    user_reaction = None
    if request.user.is_authenticated:
        try:
            r = ReviewReaction.objects.get(review=review, user=request.user)
            user_reaction = r.reaction
        except ReviewReaction.DoesNotExist:
            pass

    # Build per-comment reaction map
    user_comment_reactions = {}
    if request.user.is_authenticated:
        for rcr in ReviewCommentReaction.objects.filter(comment__review=review, user=request.user):
            user_comment_reactions[rcr.comment_id] = rcr.reaction

    # Active debate for current user on this review
    user_debate = None
    if request.user.is_authenticated:
        user_debate = Debate.objects.filter(
            review_comment__review=review,
            status='accepted',
            participants__user=request.user,
            participants__is_active=True,
        ).first()

    def enrich(c):
        return {
            'comment': c,
            'user_reaction': user_comment_reactions.get(c.id),
            'active_debate': c.debates.filter(status='accepted').first(),
        }

    agree_comments = [enrich(c) for c in comments if c.side == 'agree']
    disagree_comments = [enrich(c) for c in comments if c.side == 'disagree']

    user_has_commented = (
        request.user.is_authenticated and
        comments.filter(user=request.user).exists()
    )

    return render(request, 'frontend/review_detail.html', {
        'review': review,
        'user_reaction': user_reaction,
        'agree_comments': agree_comments,
        'disagree_comments': disagree_comments,
        'user_debate': user_debate,
        'user_has_commented': user_has_commented,
    })


@require_POST
def react_to_review(request, review_id):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required'}, status=401)

    review = get_object_or_404(Review, id=review_id, is_deleted_by_moderation=False)

    if review.user_id == request.user.id:
        return JsonResponse({'error': 'Cannot react to your own review.'}, status=403)

    try:
        data = json.loads(request.body)
        reaction_type = data.get('reaction', 'agree')
    except (json.JSONDecodeError, TypeError):
        return JsonResponse({'error': 'Invalid request'}, status=400)

    if reaction_type not in ('agree', 'disagree'):
        return JsonResponse({'error': 'Invalid reaction.'}, status=400)

    reaction, created = ReviewReaction.objects.get_or_create(
        review=review, user=request.user, defaults={'reaction': reaction_type}
    )
    if not created:
        if reaction.reaction == reaction_type:
            reaction.delete()
            action = 'removed'
        else:
            reaction.reaction = reaction_type
            reaction.save(update_fields=['reaction', 'updated_at'])
            action = 'changed'
    else:
        action = 'added'

    agree = ReviewReaction.objects.filter(review=review, reaction='agree').count()
    disagree = ReviewReaction.objects.filter(review=review, reaction='disagree').count()
    Review.objects.filter(id=review_id).update(agree_count=agree, disagree_count=disagree)

    return JsonResponse({'success': True, 'action': action, 'agree_count': agree, 'disagree_count': disagree})


@require_POST
def create_review_comment(request, review_id):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required'}, status=401)

    review = get_object_or_404(Review, id=review_id, is_deleted_by_moderation=False)

    try:
        data = json.loads(request.body)
        content = (data.get('content') or '').strip()
        side = (data.get('side') or '').strip()
    except (json.JSONDecodeError, TypeError):
        return JsonResponse({'error': 'Invalid request'}, status=400)

    if not content:
        return JsonResponse({'error': 'Comment cannot be empty.'}, status=400)

    if side not in ('agree', 'disagree'):
        return JsonResponse({'error': 'You must pick a side (agree or disagree) before commenting.'}, status=400)

    # Validate side matches the user's actual vote
    try:
        user_reaction = ReviewReaction.objects.get(review=review, user=request.user)
        if user_reaction.reaction != side:
            return JsonResponse({'error': f'You voted {user_reaction.reaction} — you can only comment on that side.'}, status=400)
    except ReviewReaction.DoesNotExist:
        return JsonResponse({'error': 'Please vote Agree or Disagree on the review before commenting.'}, status=400)

    # One comment per user per review
    if ReviewComment.objects.filter(review=review, user=request.user).exists():
        return JsonResponse({'error': 'You have already posted a comment on this review.'}, status=400)

    if check_content_moderation(content):
        return JsonResponse({'error': 'Your comment contains inappropriate content.'}, status=400)

    try:
        comment = ReviewComment.objects.create(
            id=str(uuid.uuid4()),
            review=review,
            user=request.user,
            content=content,
            side=side,
        )
    except Exception:
        return JsonResponse({'error': 'Failed to save comment. Please try again.'}, status=500)

    try:
        picture_url = request.user.profile.get_picture_url() if hasattr(request.user, 'profile') else ''
    except Exception:
        picture_url = ''

    return JsonResponse({
        'success': True,
        'comment': {
            'id': comment.id,
            'content': comment.content,
            'username': request.user.username,
            'picture_url': picture_url,
            'side': side,
            'likes': 0,
            'dislikes': 0,
            'created_at': comment.created_at.strftime('%b %d, %Y'),
        },
    })


@require_POST
def like_review_comment(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required'}, status=401)

    try:
        data = json.loads(request.body)
        comment_id = data.get('comment_id')
        reaction_type = data.get('reaction', 'like')
    except (json.JSONDecodeError, TypeError):
        return JsonResponse({'error': 'Invalid request'}, status=400)

    comment = get_object_or_404(ReviewComment, id=comment_id)

    if comment.user_id == request.user.id:
        return JsonResponse({'error': 'Cannot react to your own comment.'}, status=403)

    reaction, created = ReviewCommentReaction.objects.get_or_create(
        comment=comment, user=request.user, defaults={'reaction': reaction_type}
    )
    if not created and reaction.reaction != reaction_type:
        reaction.reaction = reaction_type
        reaction.save(update_fields=['reaction', 'updated_at'])

    likes = ReviewCommentReaction.objects.filter(comment=comment, reaction='like').count()
    dislikes = ReviewCommentReaction.objects.filter(comment=comment, reaction='dislike').count()
    comment.likes = likes
    comment.dislikes = dislikes
    comment.save(update_fields=['likes', 'dislikes', 'updated_at'])

    return JsonResponse({'success': True, 'likes': likes, 'dislikes': dislikes})


@login_required
@require_POST
def pin_review_comment(request, review_id):
    review = get_object_or_404(Review, id=review_id, is_deleted_by_moderation=False)
    if review.user != request.user:
        return JsonResponse({'error': 'Only the review author can pin comments.'}, status=403)
    comment_id = (request.POST.get('comment_id') or '').strip()
    comment = get_object_or_404(ReviewComment, id=comment_id, review=review)
    # Toggle: unpin if already pinned, otherwise unpin all on that side and pin this one
    if comment.is_pinned:
        comment.is_pinned = False
        comment.save(update_fields=['is_pinned'])
        return JsonResponse({'success': True, 'pinned': False})
    ReviewComment.objects.filter(review=review, side=comment.side, is_pinned=True).update(is_pinned=False)
    comment.is_pinned = True
    comment.save(update_fields=['is_pinned'])
    return JsonResponse({'success': True, 'pinned': True})


# ─── Leaderboard ─────────────────────────────────────────────────────────────

def leaderboard(request):
    from django.contrib.auth.models import User as AuthUser

    period = request.GET.get('period', 'alltime')
    category = request.GET.get('category', '').strip()
    tab = request.GET.get('tab', 'posts')   # 'posts' or 'debates'

    # ── Posts leaderboard ────────────────────────────────────────────────────
    post_filter = Q(posts__is_draft=False, posts__is_deleted_by_moderation=False)
    if period == 'week':
        post_filter &= Q(posts__created_at__gte=timezone.now() - timedelta(days=7))
    if category:
        post_filter &= Q(posts__category=category)

    users = AuthUser.objects.annotate(
        post_count=Count('posts', distinct=True, filter=post_filter),
        total_likes=Count('post_actions', distinct=True, filter=Q(post_actions__action='like')),
    ).filter(post_count__gt=0).order_by('-post_count', '-total_likes')[:50]

    board = []
    for rank, u in enumerate(users, start=1):
        p = getattr(u, 'profile', None)
        board.append({
            'rank': rank,
            'username': u.username,
            'avatar_url': p.get_picture_url if p else '',
            'post_count': u.post_count,
            'total_likes': u.total_likes,
            'reputation': getattr(p, 'reputation_score', 0) if p else 0,
            'trust_level': p.trust_level if p else 'new',
        })

    # ── Debate leaderboard ────────────────────────────────────────────────────
    debate_cutoff = timezone.now() - timedelta(days=7) if period == 'week' else None

    # Count debates played (as initiator or target, completed only)
    debate_qs = Debate.objects.filter(status='completed')
    if debate_cutoff:
        debate_qs = debate_qs.filter(updated_at__gte=debate_cutoff)

    # Build per-user debate stats from ObserverVote majorities
    from collections import defaultdict
    debate_stats_map = defaultdict(lambda: {'played': 0, 'wins': 0, 'spectators': 0})

    completed_debates = list(debate_qs.select_related('initiator', 'target').prefetch_related('observer_votes'))
    for debate in completed_debates:
        yes_votes = sum(1 for v in debate.observer_votes.all() if v.winner_side == 'yes')
        no_votes = sum(1 for v in debate.observer_votes.all() if v.winner_side == 'no')
        total_obs = yes_votes + no_votes

        try:
            init_side = debate.participants.get(user=debate.initiator).side
            target_side = 'no' if init_side == 'yes' else 'yes'
        except Exception:
            init_side, target_side = 'yes', 'no'

        winner_side = None
        if yes_votes > no_votes:
            winner_side = 'yes'
        elif no_votes > yes_votes:
            winner_side = 'no'

        for participant_user, side in [(debate.initiator, init_side), (debate.target, target_side)]:
            uid = participant_user.id
            debate_stats_map[uid]['played'] += 1
            debate_stats_map[uid]['spectators'] += total_obs
            if winner_side and side == winner_side:
                debate_stats_map[uid]['wins'] += 1
            debate_stats_map[uid]['_user'] = participant_user

    # Also count live room wins
    try:
    
        live_qs = LiveDebateRoom.objects.filter(status='closed', winner_side__in=['yes', 'no'])
        if debate_cutoff:
            live_qs = live_qs.filter(ended_at__gte=debate_cutoff)
        for room in live_qs.select_related('yes_debater', 'no_debater'):
            winner_user = room.yes_debater if room.winner_side == 'yes' else room.no_debater
            loser_user = room.no_debater if room.winner_side == 'yes' else room.yes_debater
            if winner_user:
                uid = winner_user.id
                debate_stats_map[uid]['played'] += 1
                debate_stats_map[uid]['wins'] += 1
                debate_stats_map[uid].setdefault('_user', winner_user)
            if loser_user:
                uid = loser_user.id
                debate_stats_map[uid]['played'] += 1
                debate_stats_map[uid].setdefault('_user', loser_user)
    except Exception:
        pass

    debate_board_raw = [
        (uid, stats) for uid, stats in debate_stats_map.items()
        if stats.get('played', 0) > 0 and '_user' in stats
    ]
    debate_board_raw.sort(key=lambda x: (-x[1]['wins'], -x[1]['played']))

    debate_board = []
    for rank, (uid, stats) in enumerate(debate_board_raw[:50], start=1):
        u = stats['_user']
        p = getattr(u, 'profile', None)
        played = stats['played']
        wins = stats['wins']
        debate_board.append({
            'rank': rank,
            'username': u.username,
            'avatar_url': p.get_picture_url if p else '',
            'wins': wins,
            'played': played,
            'win_rate': round(wins / played * 100) if played else 0,
            'spectators': stats['spectators'],
            'reputation': getattr(p, 'reputation_score', 0) if p else 0,
        })

    # ── Streak leaderboard ───────────────────────────────────────────────────
    from users.models import Profile as _Profile
    streak_qs = _Profile.objects.filter(streak_days__gt=0).select_related('user').order_by('-streak_days')[:50]
    streak_board = []
    for rank, p in enumerate(streak_qs, start=1):
        streak_board.append({
            'rank': rank,
            'username': p.user.username,
            'avatar_url': p.get_picture_url,
            'streak_days': p.streak_days,
            'reputation': p.reputation_score,
        })

    # ── Reputation leaderboard ───────────────────────────────────────────────
    rep_qs = _Profile.objects.filter(reputation_score__gt=0).select_related('user').order_by('-reputation_score')[:50]
    reputation_board = []
    for rank, p in enumerate(rep_qs, start=1):
        reputation_board.append({
            'rank': rank,
            'username': p.user.username,
            'avatar_url': p.get_picture_url,
            'reputation': p.reputation_score,
            'streak_days': p.streak_days,
            'trust_level': p.trust_level,
        })

    # Collect usernames the current user already follows so the template
    # can render the correct initial follow button state.
    followed_usernames = set()
    requested_usernames = set()
    if request.user.is_authenticated:
        from users.models import Follow as _Follow, FollowRequest as _FollowRequest
        followed_usernames = set(
            _Follow.objects.filter(follower=request.user)
            .values_list('following__username', flat=True)
        )
        requested_usernames = set(
            _FollowRequest.objects.filter(from_user=request.user, status='pending')
            .values_list('to_user__username', flat=True)
        )

    def _follow_state(username):
        if username == (request.user.username if request.user.is_authenticated else ''):
            return 'self'
        if username in followed_usernames:
            return 'following'
        if username in requested_usernames:
            return 'requested'
        return 'follow'

    for entry in board:
        entry['follow_state'] = _follow_state(entry['username'])
    for entry in debate_board:
        entry['follow_state'] = _follow_state(entry['username'])
    for entry in streak_board:
        entry['follow_state'] = _follow_state(entry['username'])
    for entry in reputation_board:
        entry['follow_state'] = _follow_state(entry['username'])

    return render(request, 'frontend/leaderboard.html', {
        'board': board,
        'debate_board': debate_board,
        'streak_board': streak_board,
        'reputation_board': reputation_board,
        'period': period,
        'active_category': category,
        'categories': CATEGORY_CHOICES,
        'tab': tab,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Trending Hashtags
# ─────────────────────────────────────────────────────────────────────────────

def trending_hashtags(request):
    """Show top hashtags by usage across posts, polls, questions, and reviews in the last 7 days."""
    from datetime import timedelta
    cutoff = timezone.now() - timedelta(days=7)
    tag_counts = {}

    for model in [Post, Poll, Question, Review]:
        qs = model.objects.filter(created_at__gte=cutoff).exclude(hashtags='').values_list('hashtags', flat=True)
        for raw in qs:
            for tag in Post.parse_hashtags(raw, max_tags=20):
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

    trending = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:50]
    max_count = trending[0][1] if trending else 1

    tag_list = [
        {'tag': tag, 'count': count, 'weight': round((count / max_count) * 100)}
        for tag, count in trending
    ]

    followed_tags_set = set()
    if request.user.is_authenticated:
        followed_tags_set = set(
            HashtagFollow.objects.filter(user=request.user).values_list('tag', flat=True)
        )

    return render(request, 'frontend/trending_hashtags.html', {
        'tag_list': tag_list,
        'period_days': 7,
        'followed_tags_set': followed_tags_set,
    })


@login_required
def scheduled_posts(request):
    """List the current user's draft posts that have a scheduled_for time set."""
    posts = (
        Post.objects.filter(user=request.user, is_draft=True, scheduled_for__isnull=False)
        .order_by('scheduled_for')
    )
    drafts_unscheduled = (
        Post.objects.filter(user=request.user, is_draft=True, scheduled_for__isnull=True)
        .order_by('-created_at')[:20]
    )
    return render(request, 'frontend/scheduled_posts.html', {
        'scheduled': posts,
        'drafts': drafts_unscheduled,
        'now': timezone.now(),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Activity Feed
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def activity_feed(request):
    """Show recent activity from users the current user follows."""
    following_ids = list(request.user.following_links.values_list('following_id', flat=True))

    if not following_ids:
        return render(request, 'frontend/activity.html', {
            'events': [],
            'has_following': False,
        })

    cutoff = timezone.now() - timedelta(days=14)

    events = []

    # Posts created by followed users
    for post in Post.objects.filter(user_id__in=following_ids, created_at__gte=cutoff).select_related('user', 'user__profile').order_by('-created_at')[:30]:
        events.append({
            'type': 'post',
            'actor': post.user,
            'avatar': _safe_avatar_url(post.user),
            'text': f'posted a discussion',
            'title': post.title,
            'url': f'/discussion/{post.id}/',
            'ts': post.created_at,
        })

    # Debates started by followed users
    for debate in Debate.objects.filter(initiator_id__in=following_ids, created_at__gte=cutoff, status__in=['accepted', 'completed']).select_related('initiator', 'initiator__profile', 'post', 'poll').order_by('-created_at')[:20]:
        events.append({
            'type': 'debate',
            'actor': debate.initiator,
            'avatar': _safe_avatar_url(debate.initiator),
            'text': 'started a debate',
            'title': debate.context_title,
            'url': f'/debates/{debate.id}/chat/',
            'ts': debate.created_at,
        })

    # Likes by followed users
    for action in PostAction.objects.filter(user_id__in=following_ids, action='like', created_at__gte=cutoff).select_related('user', 'user__profile', 'post').order_by('-created_at')[:20]:
        events.append({
            'type': 'like',
            'actor': action.user,
            'avatar': _safe_avatar_url(action.user),
            'text': 'liked a discussion',
            'title': action.post.title,
            'url': f'/discussion/{action.post_id}/',
            'ts': action.created_at,
        })

    # New follows of the current user
    for follow in Follow.objects.filter(following=request.user, created_at__gte=cutoff).select_related('follower', 'follower__profile').order_by('-created_at')[:10]:
        events.append({
            'type': 'follow',
            'actor': follow.follower,
            'avatar': _safe_avatar_url(follow.follower),
            'text': 'started following you',
            'title': '',
            'url': f'/user/{follow.follower.username}/',
            'ts': follow.created_at,
        })

    events.sort(key=lambda e: e['ts'], reverse=True)

    type_filter = request.GET.get('type', '')
    filtered = [e for e in events if not type_filter or e['type'] == type_filter]

    return render(request, 'frontend/activity.html', {
        'events': filtered[:60],
        'all_events': events[:60],
        'active_type': type_filter,
        'has_following': True,
        'follow_suggestions': _follow_suggestions(request.user),
        'trending_sidebar': _get_trending_hashtags(),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Debate Transcript (public read-only view)
# ─────────────────────────────────────────────────────────────────────────────

def debate_transcript(request, debate_id):
    """Public read-only transcript for a completed or accepted debate."""
    debate = get_object_or_404(Debate, id=debate_id)

    messages_qs = DebateMessage.objects.filter(
        debate=debate,
    ).select_related('sender', 'sender__profile').order_by('created_at')

    side_map = _sender_side_map(debate)

    message_list = []
    for msg in messages_qs:
        message_list.append({
            'id': msg.id,
            'sender': msg.sender.username,
            'avatar': _safe_avatar_url(msg.sender),
            'content': _decode_chat_content_from_storage(msg.content),
            'side': side_map.get(msg.sender_id, ''),
            'is_system': msg.is_system,
            'is_edited': msg.is_edited,
            'created_at': msg.created_at,
            'is_deleted': msg.is_deleted_by_moderation,
        })

    # Observer vote tallies
    yes_votes = ObserverVote.objects.filter(debate=debate, winner_side='yes').count()
    no_votes = ObserverVote.objects.filter(debate=debate, winner_side='no').count()
    user_observer_vote = None
    if request.user.is_authenticated:
        ov = ObserverVote.objects.filter(debate=debate, voter=request.user).first()
        user_observer_vote = ov.winner_side if ov else None

    participants = DebateParticipant.objects.filter(debate=debate).select_related('user', 'user__profile')

    return render(request, 'frontend/debate_transcript.html', {
        'debate': debate,
        'message_list': message_list,
        'participants': participants,
        'yes_votes': yes_votes,
        'no_votes': no_votes,
        'user_observer_vote': user_observer_vote,
        'can_vote': request.user.is_authenticated and user_observer_vote is None and debate.status == 'completed',
    })


# ─────────────────────────────────────────────────────────────────────────────
# Debate Recap Card
# ─────────────────────────────────────────────────────────────────────────────

def debate_recap(request, debate_id):
    """Shareable recap card for a completed debate."""
    debate = get_object_or_404(
        Debate.objects.select_related(
            'initiator', 'initiator__profile',
            'target', 'target__profile',
            'post', 'poll',
        ),
        id=debate_id,
        status='completed',
    )

    participants = list(
        DebateParticipant.objects.filter(debate=debate)
        .select_related('user', 'user__profile')
    )
    side_map = {p.user_id: p.side for p in participants}

    yes_votes = ObserverVote.objects.filter(debate=debate, winner_side='yes').count()
    no_votes = ObserverVote.objects.filter(debate=debate, winner_side='no').count()
    total_votes = yes_votes + no_votes

    # Determine winner side by observer vote majority
    if yes_votes > no_votes:
        winner_side = 'yes'
    elif no_votes > yes_votes:
        winner_side = 'no'
    else:
        winner_side = None  # draw or no votes

    # Identify winner user
    winner_user = None
    if winner_side:
        for p in participants:
            if p.side == winner_side:
                winner_user = p.user
                break

    # Message stats
    msg_counts = {}
    total_messages = 0
    for p in participants:
        c = DebateMessage.objects.filter(debate=debate, sender=p.user, is_system=False).count()
        msg_counts[p.user_id] = c
        total_messages += c

    # Duration
    first_msg = DebateMessage.objects.filter(debate=debate).order_by('created_at').first()
    last_msg = DebateMessage.objects.filter(debate=debate).order_by('created_at').last()
    duration_minutes = None
    if first_msg and last_msg and first_msg != last_msg:
        delta = last_msg.created_at - first_msg.created_at
        duration_minutes = max(1, int(delta.total_seconds() / 60))

    context_title = debate.context_title or 'a discussion'

    # Open Graph image description (text-based since no image rendering)
    og_description = (
        f"Debate recap: {debate.initiator.username} vs {debate.target.username} "
        f"on '{context_title}'. "
        + (f"Winner: @{winner_user.username}. " if winner_user else "No majority winner. ")
        + f"{total_votes} spectator vote{'s' if total_votes != 1 else ''}."
    )

    return render(request, 'frontend/debate_recap.html', {
        'debate': debate,
        'participants': participants,
        'side_map': side_map,
        'yes_votes': yes_votes,
        'no_votes': no_votes,
        'total_votes': total_votes,
        'winner_side': winner_side,
        'winner_user': winner_user,
        'msg_counts': msg_counts,
        'total_messages': total_messages,
        'duration_minutes': duration_minutes,
        'context_title': context_title,
        'og_description': og_description,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Observer Vote
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@require_POST
def observer_vote(request, debate_id):
    """Cast or update an observer vote on who argued best."""
    debate = get_object_or_404(Debate, id=debate_id, status__in=('accepted', 'completed'))
    winner_side = request.POST.get('winner_side')
    if winner_side not in ('yes', 'no'):
        return JsonResponse({'success': False, 'error': 'Invalid side.'}, status=400)

    if DebateParticipant.objects.filter(debate=debate, user=request.user).exists():
        return JsonResponse({'success': False, 'error': 'Participants cannot vote on their own debate.'}, status=400)

    ObserverVote.objects.update_or_create(
        debate=debate,
        voter=request.user,
        defaults={'winner_side': winner_side},
    )

    yes_votes = ObserverVote.objects.filter(debate=debate, winner_side='yes').count()
    no_votes = ObserverVote.objects.filter(debate=debate, winner_side='no').count()
    return JsonResponse({'success': True, 'yes_votes': yes_votes, 'no_votes': no_votes, 'your_vote': winner_side})


# ─────────────────────────────────────────────────────────────────────────────
# User Blocking
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@require_POST
def block_user(request):
    username = request.POST.get('username', '').strip()
    if not username:
        return JsonResponse({'success': False, 'error': 'Username required.'}, status=400)
    if username == request.user.username:
        return JsonResponse({'success': False, 'error': 'You cannot block yourself.'}, status=400)
    target = get_object_or_404(User, username=username)
    _, created = UserBlock.objects.get_or_create(blocker=request.user, blocked=target)
    return JsonResponse({'success': True, 'blocked': True, 'created': created})


@login_required
@require_POST
def unblock_user(request):
    username = request.POST.get('username', '').strip()
    if not username:
        return JsonResponse({'success': False, 'error': 'Username required.'}, status=400)
    target = get_object_or_404(User, username=username)
    deleted, _ = UserBlock.objects.filter(blocker=request.user, blocked=target).delete()
    return JsonResponse({'success': True, 'blocked': False, 'removed': deleted > 0})


# ─── Mute / Unmute User ───────────────────────────────────────────────────────

@login_required
@require_POST
def mute_user(request):
    username = request.POST.get('username', '').strip()
    if not username:
        return JsonResponse({'success': False, 'error': 'Username required.'}, status=400)
    if username == request.user.username:
        return JsonResponse({'success': False, 'error': 'You cannot mute yourself.'}, status=400)
    target = get_object_or_404(User, username=username)
    _, created = UserMute.objects.get_or_create(muter=request.user, muted=target)
    return JsonResponse({'success': True, 'muted': True, 'created': created})


@login_required
@require_POST
def unmute_user(request):
    username = request.POST.get('username', '').strip()
    if not username:
        return JsonResponse({'success': False, 'error': 'Username required.'}, status=400)
    target = get_object_or_404(User, username=username)
    deleted, _ = UserMute.objects.filter(muter=request.user, muted=target).delete()
    return JsonResponse({'success': True, 'muted': False, 'removed': deleted > 0})


@login_required
def muted_users_list(request):
    mutes = UserMute.objects.filter(muter=request.user).select_related('muted', 'muted__profile').order_by('-created_at')
    return render(request, 'frontend/muted_users.html', {'mutes': mutes})


# ─────────────────────────────────────────────────────────────────────────────
# Profile Bio / Website Update
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@require_POST
def update_profile_bio(request):
    bio = (request.POST.get('bio') or '').strip()[:280]
    website = (request.POST.get('website') or '').strip()[:200]
    if website and not (website.startswith('https://') or website.startswith('http://')):
        return JsonResponse({'success': False, 'error': 'Website must start with http:// or https://'}, status=400)

    if bio and check_content_moderation(bio):
        return JsonResponse({'success': False, 'error': 'Bio contains abusive language.'}, status=400)

    profile, _ = Profile.objects.get_or_create(user=request.user, defaults={'username': request.user.username})
    profile.bio = bio
    profile.website = website
    profile.save(update_fields=['bio', 'website', 'updated_at'])
    return JsonResponse({'success': True, 'bio': profile.bio, 'website': profile.website})


# ─────────────────────────────────────────────────────────────────────────────
# Comment Reporting
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@require_POST
def report_post_comment(request):
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, AttributeError):
        data = request.POST

    comment_id = data.get('comment_id')
    reason = data.get('reason', 'abusive_language')
    details = (data.get('details') or '').strip()[:500]

    if not comment_id:
        return JsonResponse({'success': False, 'error': 'comment_id required.'}, status=400)

    comment = get_object_or_404(Comment, id=comment_id)

    if comment.user == request.user:
        return JsonResponse({'success': False, 'error': 'You cannot report your own comment.'}, status=400)

    valid_reasons = {'abusive_language', 'spam', 'misinformation', 'harassment', 'other'}
    if reason not in valid_reasons:
        reason = 'other'

    try:
        CommentReport.objects.create(
            comment=comment,
            reporter=request.user,
            reason=reason,
            details=details,
        )
    except IntegrityError:
        return JsonResponse({'success': False, 'error': 'You already reported this comment.'}, status=400)

    return JsonResponse({'success': True, 'message': 'Comment reported. Our team will review it.'})


# ─────────────────────────────────────────────────────────────────────────────
# Debate Stats JSON endpoint (used by profile page)
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def debate_stats(request, username=None):
    if username:
        target_user = get_object_or_404(User, username=username)
    else:
        target_user = request.user

    participations = DebateParticipant.objects.filter(user=target_user).select_related('debate')
    total = participations.count()
    completed = participations.filter(debate__status='completed').count()
    active = participations.filter(debate__status='accepted', is_active=True).count()

    yes_wins = ObserverVote.objects.filter(
        debate__participants__user=target_user,
        debate__participants__side='yes',
        winner_side='yes',
    ).values('debate').distinct().count()
    no_wins = ObserverVote.objects.filter(
        debate__participants__user=target_user,
        debate__participants__side='no',
        winner_side='no',
    ).values('debate').distinct().count()

    return JsonResponse({
        'success': True,
        'username': target_user.username,
        'total_debates': total,
        'completed_debates': completed,
        'active_debates': active,
        'observer_wins': yes_wins + no_wins,
        'posts_count': Post.objects.filter(user=target_user).count(),
        'comments_count': Comment.objects.filter(user=target_user).count(),
    })


# ─── @Mention helpers ──────────────────────────────────────────────────────────

import re as _re

def _parse_mentions(content):
    """Return list of unique lowercase usernames found in @mention syntax."""
    return list(dict.fromkeys(
        m.lower() for m in _re.findall(r'@([A-Za-z0-9_]+)', content or '')
    ))

def _notify_mentions(author, content, post):
    """Create mention notifications for all @mentioned users in content."""
    usernames = _parse_mentions(content)
    if not usernames:
        return
    mentioned_users = User.objects.filter(username__in=usernames).exclude(id=author.id)
    for user in mentioned_users:
        Notification.objects.create(
            user=user,
            post=post,
            notification_type='mention',
            message=f'@{author.username} mentioned you in a comment on "{post.title}".',
        )
        _send_notification_email(
            user,
            f'@{author.username} mentioned you',
            f'You were mentioned in a comment on "{post.title}".\n\n'
            f'"{content[:280]}"\n\n'
            f'View it at: {settings.SITE_URL}/discussion/{post.id}/',
            notif_type='mention',
        )


# ─── Hashtag following ─────────────────────────────────────────────────────────

@login_required
@require_POST
def follow_hashtag(request):
    tag = (request.POST.get('tag') or '').strip().lower().lstrip('#')
    if not tag or len(tag) > 40:
        return JsonResponse({'success': False, 'error': 'Invalid tag.'}, status=400)

    hf, created = HashtagFollow.objects.get_or_create(user=request.user, tag=tag)
    if not created:
        hf.delete()
        return JsonResponse({'success': True, 'following': False})
    return JsonResponse({'success': True, 'following': True})


@login_required
def hashtag_followed_feed(request):
    followed_tags = list(
        HashtagFollow.objects.filter(user=request.user).values_list('tag', flat=True)
    )

    posts, polls, questions, reviews, stock_predictions = [], [], [], [], []
    if followed_tags:
        tag_filter = _re.compile(r'\b(?:' + '|'.join(_re.escape(t) for t in followed_tags) + r')\b', _re.I)

        def _matches(obj):
            return bool(tag_filter.search(getattr(obj, 'hashtags', '') or ''))

        def _sp_matches(sp):
            sym = (sp.stock_symbol or '').lower()
            return any(sym == t.lower() or t.lower() in sym for t in followed_tags)

        # Load everything in one request — tabs switch client-side with no reload
        raw_posts = _annotated_feed_posts_queryset().order_by('-created_at')[:200]
        posts = [p for p in raw_posts if _matches(p)][:30]
        _enrich_posts_for_feed(posts, request.user)

        raw_polls = list(Poll.objects.filter(is_active=True).order_by('-created_at')[:200])
        polls = [p for p in raw_polls if _matches(p)][:20]

        raw_qs = list(Question.objects.filter(is_deleted_by_moderation=False).order_by('-created_at')[:200])
        questions = [q for q in raw_qs if _matches(q)][:20]

        raw_reviews = list(Review.objects.filter(is_deleted_by_moderation=False).order_by('-created_at')[:200])
        reviews = [r for r in raw_reviews if _matches(r)][:20]

        raw_sp = list(StockPrediction.objects.filter(
            status__in=['active', 'resolved']
        ).select_related('post', 'post__user').order_by('-post__created_at')[:200])
        stock_predictions = [sp for sp in raw_sp if _sp_matches(sp)][:20]

    return render(request, 'frontend/hashtag_feed.html', {
        'followed_tags': followed_tags,
        'posts': posts,
        'polls': polls,
        'questions': questions,
        'reviews': reviews,
        'stock_predictions': stock_predictions,
    })


# ─── Moderation dashboard ──────────────────────────────────────────────────────

@login_required
def moderation_dashboard(request):
    if not _is_configured_moderator(request.user):
        from django.http import Http404
        raise Http404

    comment_reports = list(CommentReport.objects.filter(
        status='pending'
    ).select_related('comment', 'comment__post', 'comment__user', 'reporter').order_by('-created_at')[:50])

    message_reports = list(DebateMessageReport.objects.filter(
        status='pending'
    ).select_related('message', 'message__debate', 'reporter', 'reported_user').order_by('-created_at')[:50])

    profile_reports = list(ProfileReport.objects.filter(
        status='pending'
    ).select_related('reporter', 'reported_user').order_by('-created_at')[:50])

    post_reports = list(PostReport.objects.filter(
        status='pending'
    ).select_related('post', 'post__user', 'reporter').order_by('-created_at')[:50])

    total_pending = len(comment_reports) + len(message_reports) + len(profile_reports) + len(post_reports)

    return render(request, 'frontend/moderation_dashboard.html', {
        'comment_reports': comment_reports,
        'message_reports': message_reports,
        'profile_reports': profile_reports,
        'post_reports': post_reports,
        'total_pending': total_pending,
    })


# ─── Save collections ──────────────────────────────────────────────────────────

@login_required
@require_POST
def create_collection(request):
    name = (request.POST.get('name') or '').strip()
    if not name or len(name) > 60:
        return JsonResponse({'success': False, 'error': 'Name must be 1–60 characters.'}, status=400)
    collection, created = SaveCollection.objects.get_or_create(user=request.user, name=name)
    if not created:
        return JsonResponse({'success': False, 'error': 'You already have a collection with that name.'}, status=400)
    return JsonResponse({'success': True, 'id': collection.id, 'name': collection.name})


@login_required
@require_POST
def add_to_collection(request):
    collection_id = request.POST.get('collection_id')
    post_id = (request.POST.get('post_id') or '').strip()
    tag = (request.POST.get('tag') or '').strip()[:40]
    if not post_id:
        return JsonResponse({'success': False, 'error': 'post_id is required.'}, status=400)
    collection = get_object_or_404(SaveCollection, id=collection_id, user=request.user)
    post = get_object_or_404(Post, id=post_id)
    item, created = CollectionItem.objects.get_or_create(collection=collection, post=post)
    if tag:
        item.tag = tag
        item.save(update_fields=['tag'])
    return JsonResponse({'success': True, 'created': created})


@login_required
@require_POST
def remove_from_collection(request):
    collection_id = (request.POST.get('collection_id') or '').strip()
    post_id = (request.POST.get('post_id') or '').strip()
    if not collection_id or not post_id:
        return JsonResponse({'success': False, 'error': 'Missing parameters.'}, status=400)
    collection = get_object_or_404(SaveCollection, id=collection_id, user=request.user)
    CollectionItem.objects.filter(collection=collection, post_id=post_id).delete()
    return JsonResponse({'success': True})


@login_required
@require_POST
def delete_collection(request, collection_id):
    collection = get_object_or_404(SaveCollection, id=collection_id, user=request.user)
    collection.delete()
    return JsonResponse({'success': True})


@login_required
def collection_detail(request, collection_id):
    collection = get_object_or_404(SaveCollection, id=collection_id, user=request.user)
    active_tag = request.GET.get('tag', '').strip()
    items_qs = collection.items.select_related('post', 'post__user').order_by('-added_at')
    if active_tag:
        items_qs = items_qs.filter(tag=active_tag)
    all_tags = list(
        collection.items.exclude(tag='').values_list('tag', flat=True).distinct().order_by('tag')
    )
    return render(request, 'frontend/collection_detail.html', {
        'collection': collection,
        'items': items_qs,
        'active_tag': active_tag,
        'all_tags': all_tags,
    })


@login_required
@require_POST
def tag_collection_item(request):
    item_id = request.POST.get('item_id')
    tag = (request.POST.get('tag') or '').strip()[:40]
    item = get_object_or_404(CollectionItem, id=item_id, collection__user=request.user)
    item.tag = tag
    item.save(update_fields=['tag'])
    return JsonResponse({'success': True, 'tag': item.tag})


# ─── User verification (moderator only) ───────────────────────────────────────

@login_required
@require_POST
def toggle_verify_user(request, username):
    if not _is_configured_moderator(request.user):
        return JsonResponse({'success': False, 'error': 'Only moderators can verify users.'}, status=403)
    target_user = get_object_or_404(User, username=username)
    profile = get_object_or_404(Profile, user=target_user)
    profile.is_verified = not profile.is_verified
    profile.save(update_fields=['is_verified', 'updated_at'])
    if profile.is_verified:
        Notification.objects.create(
            user=target_user,
            post=None,
            notification_type='system',
            message='Your account has been verified by a moderator. You now have a verified badge.',
        )
        _send_notification_email(
            target_user,
            'Your account is now verified',
            'Congratulations! A moderator has verified your account on PickASide. '
            'You will now display a verified badge on your profile.',
        )
    return JsonResponse({'success': True, 'is_verified': profile.is_verified})


# ─── Muted Keywords ───────────────────────────────────────────────────────────

@login_required
@require_POST
def add_muted_keyword(request):
    keyword = (request.POST.get('keyword') or '').strip().lower()
    if not keyword:
        return JsonResponse({'success': False, 'error': 'Keyword is required.'}, status=400)
    if len(keyword) > 60:
        return JsonResponse({'success': False, 'error': 'Keyword too long (max 60 chars).'}, status=400)
    obj, created = MutedKeyword.objects.get_or_create(user=request.user, keyword=keyword)
    return JsonResponse({'success': True, 'keyword': keyword, 'created': created})


@login_required
@require_POST
def remove_muted_keyword(request):
    kw_id = request.POST.get('id', '').strip()
    if kw_id:
        MutedKeyword.objects.filter(user=request.user, id=kw_id).delete()
        return JsonResponse({'success': True})
    keyword = (request.POST.get('keyword') or '').strip().lower()
    if not keyword:
        return JsonResponse({'success': False, 'error': 'Keyword or id is required.'}, status=400)
    MutedKeyword.objects.filter(user=request.user, keyword=keyword).delete()
    return JsonResponse({'success': True, 'keyword': keyword})


# ─── Post Series ──────────────────────────────────────────────────────────────

@login_required
def create_series(request):
    if request.method == 'POST':
        title = (request.POST.get('title') or '').strip()
        description = (request.POST.get('description') or '').strip()
        if not title:
            return render(request, 'frontend/create_series.html', {'error': 'Title is required.'})
        series = PostSeries.objects.create(
            id=str(uuid.uuid4()),
            user=request.user,
            title=title,
            description=description,
        )
        return redirect('series_detail', series_id=series.id)
    return render(request, 'frontend/create_series.html', {})


def series_detail(request, series_id):
    series = get_object_or_404(PostSeries, id=series_id)
    items = series.items.select_related('post', 'post__user').order_by('order')
    return render(request, 'frontend/series_detail.html', {
        'series': series,
        'items': items,
    })


@login_required
@require_POST
def add_post_to_series(request, series_id):
    series = get_object_or_404(PostSeries, id=series_id, user=request.user)
    post_id = (request.POST.get('post_id') or '').strip()
    if not post_id:
        return JsonResponse({'success': False, 'error': 'post_id is required.'}, status=400)
    post = get_object_or_404(Post, id=post_id)
    max_order = series.items.aggregate(m=Max('order'))['m'] or 0
    try:
        PostSeriesItem.objects.create(series=series, post=post, order=max_order + 1)
    except IntegrityError:
        pass
    return JsonResponse({'success': True})


@login_required
@require_POST
def remove_from_series(request, series_id):
    series = get_object_or_404(PostSeries, id=series_id, user=request.user)
    post_id = (request.POST.get('post_id') or '').strip()
    if not post_id:
        return JsonResponse({'success': False, 'error': 'post_id is required.'}, status=400)
    PostSeriesItem.objects.filter(series=series, post_id=post_id).delete()
    return JsonResponse({'success': True})


# ─── Notification Preferences ────────────────────────────────────────────────

@login_required
@require_POST
@login_required
def notification_prefs_page(request):
    """Render the notification preferences settings page."""
    profile = Profile.objects.filter(user=request.user).first()
    current_prefs = profile.notification_prefs if profile else {}
    quiet_start = profile.quiet_hours_start.strftime('%H:%M') if profile and profile.quiet_hours_start else ''
    quiet_end = profile.quiet_hours_end.strftime('%H:%M') if profile and profile.quiet_hours_end else ''
    return render(request, 'frontend/notification_prefs.html', {
        'current_prefs': current_prefs,
        'default_prefs': DEFAULT_NOTIFICATION_PREFS,
        'quiet_hours_start': quiet_start,
        'quiet_hours_end': quiet_end,
        'allow_mentions_from': profile.allow_mentions_from if profile else 'everyone',
        'mention_allow_choices': Profile.MENTION_ALLOW_CHOICES,
    })


@login_required
def update_notification_prefs(request):
    """Update the authenticated user's notification preferences."""
    if request.method == 'GET':
        return notification_prefs_page(request)
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Invalid JSON.'}, status=400)

    new_prefs = data.get('prefs')
    if not isinstance(new_prefs, dict):
        return JsonResponse({'success': False, 'error': 'prefs must be an object.'}, status=400)

    valid_keys = set(DEFAULT_NOTIFICATION_PREFS.keys())
    for key in new_prefs:
        if key not in valid_keys:
            return JsonResponse({'success': False, 'error': f'Unknown preference key: {key}'}, status=400)

    profile = Profile.objects.filter(user=request.user).first()
    if not profile:
        return JsonResponse({'success': False, 'error': 'Profile not found.'}, status=404)

    current = dict(profile.notification_prefs or {})
    for notif_type, channels in new_prefs.items():
        if not isinstance(channels, dict):
            continue
        if notif_type not in current:
            current[notif_type] = {}
        for channel, value in channels.items():
            current[notif_type][channel] = bool(value)

    profile.notification_prefs = current
    profile.save(update_fields=['notification_prefs', 'updated_at'])
    return JsonResponse({'success': True, 'prefs': current})


@login_required
@require_POST
def update_quiet_hours(request):
    """Save or clear the user's quiet-hours window."""
    profile = Profile.objects.filter(user=request.user).first()
    if not profile:
        return JsonResponse({'success': False, 'error': 'Profile not found.'}, status=404)

    raw_start = (request.POST.get('quiet_hours_start') or '').strip()
    raw_end = (request.POST.get('quiet_hours_end') or '').strip()

    import datetime as _dt
    def _parse_time(s):
        if not s:
            return None
        try:
            return _dt.datetime.strptime(s, '%H:%M').time()
        except ValueError:
            return None

    if raw_start == '' and raw_end == '':
        profile.quiet_hours_start = None
        profile.quiet_hours_end = None
    else:
        t_start = _parse_time(raw_start)
        t_end = _parse_time(raw_end)
        if t_start is None or t_end is None:
            return JsonResponse({'success': False, 'error': 'Invalid time format. Use HH:MM.'}, status=400)
        if t_start == t_end:
            return JsonResponse({'success': False, 'error': 'Start and end time cannot be the same.'}, status=400)
        profile.quiet_hours_start = t_start
        profile.quiet_hours_end = t_end

    profile.save(update_fields=['quiet_hours_start', 'quiet_hours_end', 'updated_at'])
    return JsonResponse({
        'success': True,
        'quiet_hours_start': profile.quiet_hours_start.strftime('%H:%M') if profile.quiet_hours_start else '',
        'quiet_hours_end': profile.quiet_hours_end.strftime('%H:%M') if profile.quiet_hours_end else '',
    })


@login_required
@require_POST
def update_mention_setting(request):
    """Update the authenticated user's @mention permission setting."""
    profile = Profile.objects.filter(user=request.user).first()
    if not profile:
        return JsonResponse({'success': False, 'error': 'Profile not found.'}, status=404)
    value = (request.POST.get('allow_mentions_from') or '').strip()
    valid = {c[0] for c in Profile.MENTION_ALLOW_CHOICES}
    if value not in valid:
        return JsonResponse({'success': False, 'error': 'Invalid value.'}, status=400)
    profile.allow_mentions_from = value
    profile.save(update_fields=['allow_mentions_from', 'updated_at'])
    return JsonResponse({'success': True, 'allow_mentions_from': value})


# ─── Achievement Badges ───────────────────────────────────────────────────────

def _maybe_award_achievements(user):
    """Check and award newly-earned achievements for the given user."""
    try:
        profile = user.profile
    except Exception:
        profile = None

    posts_count = user.posts.filter(is_draft=False).count()
    earned_codes = []

    if posts_count >= 1:
        earned_codes.append('first_post')
    if posts_count >= 10:
        earned_codes.append('contributor')

    debates_initiated = user.debate_participations.filter(
        debate__initiator=user
    ).values('debate_id').distinct().count()
    if debates_initiated >= 5:
        earned_codes.append('debate_starter')

    likes_total = PostAction.objects.filter(post__user=user, action='like').count()
    if likes_total >= 50:
        earned_codes.append('top_voice')

    if Answer.objects.filter(user=user, question__best_answer__user=user).exists():
        earned_codes.append('helpful')

    polls_count = Poll.objects.filter(user=user).count()
    if polls_count >= 10:
        earned_codes.append('poll_master')

    if profile and profile.is_verified:
        earned_codes.append('verified_voice')

    from django.utils import timezone as _tz
    if ((_tz.now() - user.date_joined).days >= 30):
        earned_codes.append('veteran')

    # Reputation badges
    try:
        rep = user.profile.reputation_score
        if rep >= 100:
            earned_codes.append('rep_100')
        if rep >= 500:
            earned_codes.append('rep_500')
        streak = user.profile.streak_days
        if streak >= 7:
            earned_codes.append('streak_7')
        if streak >= 30:
            earned_codes.append('streak_30')
    except Exception:
        pass

    # Challenger badge
    from discussions.models import ChallengeEntry as _CE2
    if _CE2.objects.filter(user=user).exists():
        earned_codes.append('challenger')

    # Hot author badge
    if Post.objects.filter(user=user, is_hot=True).exists():
        earned_codes.append('hot_author')

    # Debate winner badge skipped: Debate model has no 'winner' field

    for code in earned_codes:
        Achievement.objects.get_or_create(user=user, code=code)


# ─── Trending Topics Sidebar ──────────────────────────────────────────────────

def _get_trending_hashtags(limit=10):
    cache_key = 'trending_hashtags_sidebar'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    from datetime import timedelta as _timedelta
    from collections import defaultdict

    now = timezone.now()
    # Three time windows with decaying weights: most recent activity scores highest
    windows = [
        (now - _timedelta(hours=2),  now,                              4.0),
        (now - _timedelta(hours=6),  now - _timedelta(hours=2),        2.0),
        (now - _timedelta(hours=24), now - _timedelta(hours=6),        1.0),
    ]

    scores = defaultdict(float)
    for start, end, weight in windows:
        for model, field in [(Post, 'hashtags'), (Poll, 'hashtags')]:
            for raw in model.objects.filter(
                created_at__gte=start, created_at__lt=end
            ).values_list(field, flat=True):
                if not raw:
                    continue
                for token in str(raw).replace(',', ' ').split():
                    tag = token.strip().lstrip('#').lower()
                    if tag:
                        scores[tag] += weight

    top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:limit]
    result = [{'tag': tag, 'count': round(score)} for tag, score in top if tag]
    cache.set(cache_key, result, 900)  # 15 min cache
    return result


def _get_rising_creators(limit=20):
    """Return users ranked by follower gains + post engagement over the last 7 days."""
    cache_key = f'rising_creators_{limit}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    from discussions.models import PostAction as _PostAction
    week_ago = timezone.now() - timedelta(days=7)

    # New followers gained this week per user
    new_follows = (
        Follow.objects
        .filter(created_at__gte=week_ago)
        .values('following_id')
        .annotate(new_followers=Count('id'))
    )
    follower_gain = {row['following_id']: row['new_followers'] for row in new_follows}

    # Post engagement (all actions) on posts published this week
    engagement = (
        _PostAction.objects
        .filter(created_at__gte=week_ago, post__is_draft=False, post__is_deleted_by_moderation=False)
        .values('post__user_id')
        .annotate(actions=Count('id'))
    )
    eng_map = {row['post__user_id']: row['actions'] for row in engagement}

    # Combine: 3× follower gain + 1× engagement actions
    all_ids = set(follower_gain) | set(eng_map)
    scored = sorted(
        all_ids,
        key=lambda uid: follower_gain.get(uid, 0) * 3 + eng_map.get(uid, 0),
        reverse=True,
    )[:limit]

    if not scored:
        cache.set(cache_key, [], 900)
        return []

    users = User.objects.filter(id__in=scored).select_related('profile')
    user_map = {u.id: u for u in users}

    result = []
    for uid in scored:
        u = user_map.get(uid)
        if not u:
            continue
        result.append({
            'user': u,
            'new_followers': follower_gain.get(uid, 0),
            'engagement': eng_map.get(uid, 0),
        })

    cache.set(cache_key, result, 900)  # 15 min cache
    return result


@login_required
def trending_users(request):
    creators = _get_rising_creators(limit=30)
    # Annotate is_following for the current user
    if creators:
        following_ids = set(
            Follow.objects.filter(follower=request.user)
            .values_list('following_id', flat=True)
        )
        for item in creators:
            item['is_following'] = item['user'].id in following_ids
            item['is_self'] = item['user'] == request.user
    return render(request, 'frontend/trending_users.html', {'creators': creators})


# ─── Endorsements ─────────────────────────────────────────────────────────────

@login_required
@require_POST
def endorse_user(request, username):
    from users.models import Endorsement as _End
    target = get_object_or_404(User, username=username)
    if target == request.user:
        return JsonResponse({'success': False, 'error': 'Cannot endorse yourself'}, status=400)
    topic = (request.POST.get('topic') or '').strip()[:60]
    if not topic:
        return JsonResponse({'success': False, 'error': 'Topic required'}, status=400)
    _, created = _End.objects.get_or_create(endorser=request.user, endorsed=target, topic=topic)
    count = _End.objects.filter(endorsed=target, topic=topic).count()
    return JsonResponse({'success': True, 'created': created, 'count': count, 'topic': topic})


# ─── Category Follows ─────────────────────────────────────────────────────────

@login_required
@require_POST
def follow_category(request):
    from discussions.models import CategoryFollow as _CF
    category = (request.POST.get('category') or '').strip()
    if not category:
        return JsonResponse({'success': False, 'error': 'category required'}, status=400)
    obj, created = _CF.objects.get_or_create(user=request.user, category=category)
    if not created:
        obj.delete()
        return JsonResponse({'success': True, 'action': 'unfollowed'})
    return JsonResponse({'success': True, 'action': 'followed'})


# ─── Post Expiry ──────────────────────────────────────────────────────────────

@login_required
@require_POST
def set_post_expiry(request, post_id):
    post = get_object_or_404(Post, id=post_id, user=request.user)
    closes_at_raw = (request.POST.get('closes_at') or '').strip()
    if closes_at_raw:
        try:
            from django.utils.dateparse import parse_datetime
            dt = parse_datetime(closes_at_raw)
            if dt:
                post.closes_at = timezone.make_aware(dt) if timezone.is_naive(dt) else dt
                post.save(update_fields=['closes_at'])
                return JsonResponse({'success': True})
        except Exception:
            pass
        return JsonResponse({'success': False, 'error': 'Invalid date'}, status=400)
    post.closes_at = None
    post.save(update_fields=['closes_at'])
    return JsonResponse({'success': True})


# ─── Debate Rematch ───────────────────────────────────────────────────────────

@login_required
@require_POST
def request_rematch(request, debate_id):
    original = get_object_or_404(Debate, id=debate_id, status='completed')
    if request.user not in (original.initiator, original.target):
        return JsonResponse({'success': False, 'error': 'Not a participant'}, status=403)
    opponent = original.target if original.initiator == request.user else original.initiator
    # Check no existing pending rematch
    existing = Debate.objects.filter(rematch_of=original, status='pending').first()
    if existing:
        return JsonResponse({'success': False, 'error': 'Rematch already requested'})
    import uuid as _uuid
    new_debate = Debate.objects.create(
        id=str(_uuid.uuid4()),
        post=original.post,
        initiator=request.user,
        target=opponent,
        status='pending',
        rematch_of=original,
        yes_supporters=original.yes_supporters,
        no_supporters=original.no_supporters,
    )
    # Notify opponent
    Notification.objects.create(
        user=opponent,
        post=original.post,
        notification_type='author_debate',
        message=f'@{request.user.username} challenged you to a rematch debate on "{original.post.title}"',
    )
    return JsonResponse({'success': True, 'debate_id': new_debate.id})


# ─── Post Appeal ──────────────────────────────────────────────────────────────

@login_required
def appeal_post(request, post_id):
    from discussions.models import PostAppeal as _Appeal
    post = get_object_or_404(Post, id=post_id, user=request.user)
    existing = _Appeal.objects.filter(post=post, user=request.user).first()
    if request.method == 'POST':
        if existing and existing.status != 'rejected':
            return JsonResponse({'success': False, 'error': 'Appeal already submitted'}, status=400)
        reason = (request.POST.get('reason') or '').strip()
        if len(reason) < 10:
            return JsonResponse({'success': False, 'error': 'Please explain your appeal (min 10 chars)'}, status=400)
        if existing and existing.status == 'rejected':
            existing.reason = reason
            existing.status = 'pending'
            existing.save(update_fields=['reason', 'status', 'updated_at'])
            appeal = existing
        else:
            appeal = _Appeal.objects.create(post=post, user=request.user, reason=reason)
        return JsonResponse({'success': True, 'appeal_id': appeal.id})
    return render(request, 'frontend/appeal_post.html', {
        'post': post, 'existing_appeal': existing
    })


@login_required
def review_appeal(request, appeal_id):
    from discussions.models import PostAppeal as _Appeal
    if not _is_configured_moderator(request.user):
        return JsonResponse({'success': False, 'error': 'Forbidden'}, status=403)
    appeal = get_object_or_404(_Appeal, id=appeal_id)
    if request.method == 'POST':
        decision = request.POST.get('decision')
        note = (request.POST.get('note') or '').strip()
        if decision not in ('approved', 'rejected'):
            return JsonResponse({'success': False, 'error': 'Invalid decision'}, status=400)
        appeal.status = decision
        appeal.moderator_note = note
        appeal.save(update_fields=['status', 'moderator_note', 'updated_at'])
        if decision == 'approved':
            appeal.post.is_deleted_by_moderation = False
            appeal.post.is_flagged = False
            appeal.post.save(update_fields=['is_deleted_by_moderation', 'is_flagged'])
        Notification.objects.create(
            user=appeal.user,
            post=appeal.post,
            notification_type='moderation_warning',
            message=f'Your appeal for "{appeal.post.title}" was {decision}.{" Moderator note: " + note if note else ""}',
        )
        return JsonResponse({'success': True})
    return render(request, 'frontend/appeal_post.html', {
        'post': appeal.post, 'existing_appeal': appeal, 'is_moderator_view': True
    })


# ─── Community Challenges ─────────────────────────────────────────────────────

def challenges_list(request):
    from discussions.models import Challenge as _Ch
    active = list(_Ch.objects.filter(is_active=True).order_by('-starts_at')[:10])
    past = list(_Ch.objects.filter(is_active=False).order_by('-ends_at')[:10])
    user_entries = set()
    if request.user.is_authenticated:
        from discussions.models import ChallengeEntry as _CE
        user_entries = set(
            _CE.objects.filter(user=request.user).values_list('challenge_id', flat=True)
        )
    return render(request, 'frontend/challenges.html', {
        'active_challenges': active,
        'past_challenges': past,
        'user_entries': user_entries,
    })


@login_required
@require_POST
def enter_challenge(request, challenge_id):
    from discussions.models import Challenge as _Ch, ChallengeEntry as _CE
    challenge = get_object_or_404(_Ch, id=challenge_id, is_active=True)
    post_id = (request.POST.get('post_id') or '').strip()
    if not post_id:
        return JsonResponse({'success': False, 'error': 'post_id required'}, status=400)
    post = get_object_or_404(Post, id=post_id, user=request.user, is_draft=False)
    _, created = _CE.objects.get_or_create(challenge=challenge, post=post, user=request.user)
    return JsonResponse({'success': True, 'created': created})


@login_required
def create_challenge(request):
    if not _is_configured_moderator(request.user):
        from django.http import Http404
        raise Http404
    from discussions.models import Challenge as _Ch
    if request.method == 'POST':
        title = (request.POST.get('title') or '').strip()
        desc = (request.POST.get('description') or '').strip()
        starts = request.POST.get('starts_at', '').strip()
        ends = request.POST.get('ends_at', '').strip()
        if not all([title, desc, starts, ends]):
            return render(request, 'frontend/create_challenge.html', {'error': 'All fields required'})
        from django.utils.dateparse import parse_datetime
        try:
            s = timezone.make_aware(parse_datetime(starts))
            e = timezone.make_aware(parse_datetime(ends))
            _Ch.objects.create(title=title, description=desc, starts_at=s, ends_at=e,
                               created_by=request.user, category=request.POST.get('category', ''))
            return redirect('challenges_list')
        except Exception as ex:
            return render(request, 'frontend/create_challenge.html', {'error': str(ex)})
    return render(request, 'frontend/create_challenge.html', {'categories': get_frontend_categories()})


# ─── Co-authoring ─────────────────────────────────────────────────────────────

@login_required
@require_POST
def invite_coauthor(request, post_id):
    from discussions.models import PostCoAuthor as _PCA
    post = get_object_or_404(Post, id=post_id, user=request.user, is_draft=True)
    username = (request.POST.get('username') or '').strip()
    invitee = get_object_or_404(User, username=username)
    if invitee == request.user:
        return JsonResponse({'success': False, 'error': 'Cannot invite yourself'}, status=400)
    _, created = _PCA.objects.get_or_create(
        post=post, user=invitee, defaults={'invited_by': request.user}
    )
    Notification.objects.create(
        user=invitee,
        post=post,
        notification_type='mention',
        message=f'@{request.user.username} invited you to co-author the draft "{post.title}"',
    )
    return JsonResponse({'success': True, 'created': created})


@login_required
@require_POST
def respond_coauthor_invite(request, post_id):
    from discussions.models import PostCoAuthor as _PCA
    invite = get_object_or_404(_PCA, post_id=post_id, user=request.user, accepted__isnull=True)
    action = request.POST.get('action')
    if action == 'accept':
        invite.accepted = True
    else:
        invite.accepted = False
    invite.save(update_fields=['accepted'])
    return JsonResponse({'success': True, 'action': action})


# ─── Hot posts helper ─────────────────────────────────────────────────────────

def _mark_hot_posts(posts):
    """Tag posts with is_hot_now=True if they got 10+ reactions in the last hour."""
    if not posts:
        return posts
    cutoff = timezone.now() - timedelta(hours=1)
    post_ids = [p.id for p in posts]
    hot_ids = set(
        PostAction.objects.filter(
            post_id__in=post_ids,
            action__in=['like', 'hot', 'agree', 'surprising'],
            created_at__gte=cutoff,
        ).values('post_id').annotate(n=Count('id')).filter(n__gte=10).values_list('post_id', flat=True)
    )
    for p in posts:
        p.is_hot_now = p.id in hot_ids
    return posts


# ─── Spam Score (used by moderation dashboard) ────────────────────────────────

def _compute_spam_score(post):
    """Return 0-100 spam likelihood score based on simple heuristics."""
    score = 0
    content = (post.title or '') + ' ' + (post.content or '')
    import re as _re
    links = len(_re.findall(r'https?://', content))
    score += min(links * 15, 45)
    words = content.split()
    if words:
        caps_ratio = sum(1 for w in words if w.isupper() and len(w) > 2) / len(words)
        score += int(caps_ratio * 30)
    account_age_days = (timezone.now() - post.user.date_joined).days
    if account_age_days < 1:
        score += 25
    elif account_age_days < 7:
        score += 10
    return min(score, 100)


# ─── For You Feed (Personalised Algorithm) ────────────────────────────────────

@login_required
def for_you_feed(request):
    """Instagram-style personalised feed using pre-computed FeedScore."""
    active_content_type = request.GET.get('content_type', '').strip()
    user_followed_tags = set(
        HashtagFollow.objects.filter(user=request.user).values_list('tag', flat=True)
    )

    scored_post_ids = (
        FeedScore.objects
        .filter(user=request.user)
        .order_by('-score')
        .values_list('post_id', flat=True)[:100]
    )

    if scored_post_ids:
        id_list = list(scored_post_ids)
        annotated = _annotated_feed_posts_queryset().filter(id__in=id_list)
        if active_content_type in ('discussion', 'stock_prediction'):
            annotated = annotated.filter(post_type=active_content_type)
        id_to_post = {p.id: p for p in annotated}
        posts_qs = [id_to_post[pid] for pid in id_list if pid in id_to_post]
    else:
        interests = list(request.user.profile.interested_categories or [])
        base_qs = _annotated_feed_posts_queryset().filter(is_draft=False, is_deleted_by_moderation=False)
        if interests:
            base_qs = base_qs.filter(category__in=interests)
        if active_content_type in ('discussion', 'stock_prediction'):
            base_qs = base_qs.filter(post_type=active_content_type)
        posts_qs = list(base_qs.order_by('-created_at')[:50])

    # Inject recent followed-hashtag posts not already in the scored list
    if user_followed_tags and active_content_type not in ('discussion', 'stock_prediction'):
        scored_ids_set = {p.id for p in posts_qs}
        _tag_q = Q()
        for _t in list(user_followed_tags)[:20]:
            _tag_q |= Q(hashtags__icontains=_t)
        topic_posts = list(
            _annotated_feed_posts_queryset()
            .filter(_tag_q, is_draft=False, is_deleted_by_moderation=False)
            .exclude(id__in=scored_ids_set)
            .order_by('-created_at')[:20]
        )
        # Interleave: insert a topic post every 4 scored posts
        merged, ti = [], 0
        for i, p in enumerate(posts_qs):
            merged.append(p)
            if (i + 1) % 4 == 0 and ti < len(topic_posts):
                merged.append(topic_posts[ti])
                ti += 1
        merged.extend(topic_posts[ti:])
        posts_qs = merged

    muted_ids = _muted_user_ids(request.user)
    blocked_ids = _blocked_user_ids(request.user)
    excluded = muted_ids | blocked_ids
    if excluded:
        posts_qs = [p for p in posts_qs if p.user_id not in excluded]

    paginator = Paginator(posts_qs, 10)
    page_obj = paginator.get_page(request.GET.get('page'))
    posts = list(page_obj.object_list)
    _enrich_posts_for_feed(posts, request.user)
    posts = _filter_muted_posts(posts, request.user)

    active_stories = _get_active_stories_for_user(request.user)

    context = {
        'posts': posts,
        'page_obj': page_obj,
        'active_tab': 'for_you',
        'active_content_type': active_content_type,
        'is_suggested_page': False,
        'categories': get_frontend_categories(),
        'active_category': '',
        'follow_suggestions': _follow_suggestions(request.user),
        'trending_sidebar': _get_trending_hashtags(),
        'active_stories': active_stories,
        'user_followed_tags': user_followed_tags,
    }
    return render(request, 'frontend/index.html', context)


# ─── Following Feed ────────────────────────────────────────────────────────────

@login_required
def following_feed(request):
    """Chronological feed of posts from people the current user follows."""
    following_ids = list(Follow.objects.filter(follower=request.user).values_list('following_id', flat=True))
    blocked_ids = _blocked_user_ids(request.user)
    muted_ids = _muted_user_ids(request.user)
    excluded = blocked_ids | muted_ids
    safe_following = [uid for uid in following_ids if uid not in excluded]

    base_qs = _annotated_feed_posts_queryset().filter(
        user_id__in=safe_following,
        is_draft=False,
        is_deleted_by_moderation=False,
    ).order_by('-created_at')

    paginator = Paginator(base_qs, 10)
    page_obj = paginator.get_page(request.GET.get('page'))
    posts = list(page_obj.object_list)
    _enrich_posts_for_feed(posts, request.user)
    posts = _filter_muted_posts(posts, request.user)

    context = {
        'posts': posts,
        'page_obj': page_obj,
        'active_tab': 'following',
        'is_suggested_page': False,
        'categories': get_frontend_categories(),
        'active_category': '',
        'follow_suggestions': _follow_suggestions(request.user),
        'trending_sidebar': _get_trending_hashtags(),
        'following_count': len(following_ids),
    }
    return render(request, 'frontend/index.html', context)


def latest_feed(request):
    """Chronological feed of all posts, newest first."""
    active_category = request.GET.get('category', '').strip()
    active_content_type = request.GET.get('content_type', '').strip()
    blocked_ids = _blocked_user_ids(request.user)
    muted_ids = _muted_user_ids(request.user)
    exclude_ids = blocked_ids | muted_ids

    base_qs = _annotated_feed_posts_queryset().filter(
        is_draft=False,
        is_deleted_by_moderation=False,
    ).order_by('-created_at')

    if active_category:
        base_qs = base_qs.filter(category=active_category)
    if active_content_type in ('discussion', 'stock_prediction'):
        base_qs = base_qs.filter(post_type=active_content_type)
    if exclude_ids:
        base_qs = base_qs.exclude(user_id__in=exclude_ids)

    paginator = Paginator(base_qs, 10)
    page_obj = paginator.get_page(request.GET.get('page'))
    posts = list(page_obj.object_list)
    _enrich_posts_for_feed(posts, request.user)
    posts = _filter_muted_posts(posts, request.user)

    context = {
        'posts': posts,
        'page_obj': page_obj,
        'active_tab': 'latest',
        'active_content_type': active_content_type,
        'is_suggested_page': False,
        'categories': get_frontend_categories(),
        'active_category': active_category,
        'follow_suggestions': _follow_suggestions(request.user),
        'trending_sidebar': _get_trending_hashtags(),
        'rising_creators': _get_rising_creators(limit=5),
        'user_followed_tags': set(),
        'stories_bar': [],
        'user_follows_category': False,
    }
    if active_category and request.user.is_authenticated:
        from discussions.models import CategoryFollow as _CF
        context['user_follows_category'] = _CF.objects.filter(user=request.user, category=active_category).exists()
    return render(request, 'frontend/index.html', context)


# ─── Explore / Discover Page ──────────────────────────────────────────────────

def explore(request):
    """Discover content outside your network — all four content types."""
    category_filter = request.GET.get('category', '').strip()
    search_q = request.GET.get('q', '').strip()
    section = request.GET.get('section', 'all').strip()
    if section not in ('all', 'pick_a_side', 'reviews', 'questions', 'stocks'):
        section = 'all'
    # When searching for a hashtag (e.g. #bitcoin), also match tags stored without the # prefix
    _bare_tag = search_q.lstrip('#') if search_q.startswith('#') else None

    # Base exclusion filters for logged-in users
    exclude_user_ids = set()
    if request.user.is_authenticated:
        following_ids = set(Follow.objects.filter(follower=request.user).values_list('following_id', flat=True))
        blocked_ids = _blocked_user_ids(request.user)
        muted_ids = _muted_user_ids(request.user)
        exclude_user_ids = following_ids | blocked_ids | muted_ids

    def _base_posts_qs():
        qs = _annotated_feed_posts_queryset().filter(is_draft=False, is_deleted_by_moderation=False)
        if request.user.is_authenticated:
            qs = qs.exclude(user_id__in=exclude_user_ids).exclude(user=request.user)
        return qs

    # ── Section: all ─────────────────────────────────────────────────────────
    if section == 'all' and not search_q:
        pick_posts_qs = _base_posts_qs().filter(post_type=Post.POST_TYPE_DISCUSSION)
        if category_filter:
            pick_posts_qs = pick_posts_qs.filter(category=category_filter)
        pick_posts = list(pick_posts_qs.order_by('-is_hot', '-like_count', '-comment_count', '-created_at')[:6])
        _enrich_posts_for_feed(pick_posts, request.user)

        stock_qs = _base_posts_qs().filter(post_type=Post.POST_TYPE_STOCK)
        stock_posts = list(stock_qs.order_by('-like_count', '-comment_count', '-created_at')[:4])
        _enrich_posts_for_feed(stock_posts, request.user)

        reviews_qs = Review.objects.filter(is_deleted_by_moderation=False).select_related('user', 'user__profile').order_by('-created_at')
        if request.user.is_authenticated:
            reviews_qs = reviews_qs.exclude(user_id__in=exclude_user_ids).exclude(user=request.user)
        recent_reviews = list(reviews_qs[:4])

        questions_qs = Question.objects.filter(is_deleted_by_moderation=False).select_related('user', 'user__profile').order_by('-answer_count', '-created_at')
        if request.user.is_authenticated:
            questions_qs = questions_qs.exclude(user_id__in=exclude_user_ids).exclude(user=request.user)
        recent_questions = list(questions_qs[:4])

        context = {
            'section': 'all',
            'pick_posts': pick_posts,
            'stock_posts': stock_posts,
            'recent_reviews': recent_reviews,
            'recent_questions': recent_questions,
            'categories': get_frontend_categories(),
            'active_category': category_filter,
            'search_query': '',
            'trending_tags': _get_explore_trending_tags(),
            'hot_today': _get_explore_hot_today(),
        }
        return render(request, 'frontend/explore.html', context)

    # ── Section: specific or search ──────────────────────────────────────────
    posts = []
    page_obj = None
    reviews_data = []
    questions_data = []
    stock_data = []

    if section in ('all', 'pick_a_side'):
        base_qs = _base_posts_qs().filter(post_type=Post.POST_TYPE_DISCUSSION)
        if category_filter:
            base_qs = base_qs.filter(category=category_filter)
        if search_q:
            _hq = Q(hashtags__icontains=search_q) | Q(hashtags__icontains=_bare_tag) if _bare_tag else Q(hashtags__icontains=search_q)
            base_qs = base_qs.filter(Q(title__icontains=search_q) | Q(content__icontains=search_q) | _hq)
        explore_posts = base_qs.order_by('-is_hot', '-like_count', '-comment_count', '-created_at')
        paginator = Paginator(explore_posts, 12)
        page_obj = paginator.get_page(request.GET.get('page'))
        posts = list(page_obj.object_list)
        _enrich_posts_for_feed(posts, request.user)
        posts = _filter_muted_posts(posts, request.user)

    elif section == 'stocks':
        base_qs = _base_posts_qs().filter(post_type=Post.POST_TYPE_STOCK)
        if category_filter:
            base_qs = base_qs.filter(category=category_filter)
        if search_q:
            _hq = Q(hashtags__icontains=search_q) | Q(hashtags__icontains=_bare_tag) if _bare_tag else Q(hashtags__icontains=search_q)
            base_qs = base_qs.filter(Q(title__icontains=search_q) | _hq)
        paginator = Paginator(base_qs.order_by('-like_count', '-comment_count', '-created_at'), 12)
        page_obj = paginator.get_page(request.GET.get('page'))
        posts = list(page_obj.object_list)
        _enrich_posts_for_feed(posts, request.user)

    elif section == 'reviews':
        qs = Review.objects.filter(is_deleted_by_moderation=False).select_related('user', 'user__profile').order_by('-created_at')
        if request.user.is_authenticated:
            qs = qs.exclude(user_id__in=exclude_user_ids).exclude(user=request.user)
        if search_q:
            _hq = Q(hashtags__icontains=search_q) | Q(hashtags__icontains=_bare_tag) if _bare_tag else Q(hashtags__icontains=search_q)
            qs = qs.filter(Q(subject__icontains=search_q) | Q(content__icontains=search_q) | _hq)
        paginator = Paginator(qs, 12)
        page_obj = paginator.get_page(request.GET.get('page'))
        reviews_data = list(page_obj.object_list)

    elif section == 'questions':
        qs = Question.objects.filter(is_deleted_by_moderation=False).select_related('user', 'user__profile').order_by('-answer_count', '-created_at')
        if request.user.is_authenticated:
            qs = qs.exclude(user_id__in=exclude_user_ids).exclude(user=request.user)
        if search_q:
            _hq = Q(hashtags__icontains=search_q) | Q(hashtags__icontains=_bare_tag) if _bare_tag else Q(hashtags__icontains=search_q)
            qs = qs.filter(Q(title__icontains=search_q) | Q(content__icontains=search_q) | _hq)
        paginator = Paginator(qs, 12)
        page_obj = paginator.get_page(request.GET.get('page'))
        questions_data = list(page_obj.object_list)

    context = {
        'section': section,
        'posts': posts,
        'page_obj': page_obj,
        'reviews_data': reviews_data,
        'questions_data': questions_data,
        'categories': get_frontend_categories(),
        'active_category': category_filter,
        'search_query': search_q,
        'trending_tags': _get_explore_trending_tags(),
        'hot_today': _get_explore_hot_today(),
    }
    return render(request, 'frontend/explore.html', context)


def _get_explore_trending_tags():
    cached = cache.get('explore_trending_tags')
    if cached is not None:
        return cached
    _tag_counts: dict = {}
    _tag_cutoff = timezone.now() - timedelta(days=7)
    for _model in [Post, Poll, Question, Review]:
        for _raw in _model.objects.filter(created_at__gte=_tag_cutoff).exclude(hashtags='').values_list('hashtags', flat=True):
            for _t in Post.parse_hashtags(_raw, max_tags=20):
                _tag_counts[_t] = _tag_counts.get(_t, 0) + 1
    _max_count = max(_tag_counts.values(), default=1)
    result = sorted(
        [{'tag': t, 'count': c, 'weight': round(c / _max_count * 100)} for t, c in _tag_counts.items()],
        key=lambda x: x['count'], reverse=True,
    )[:15]
    cache.set('explore_trending_tags', result, 600)  # 10 minutes
    return result


def _get_explore_hot_today():
    cached = cache.get('explore_hot_today')
    if cached is not None:
        return cached
    _hot_cutoff = timezone.now() - timedelta(hours=24)
    result = list(
        _annotated_feed_posts_queryset()
        .filter(is_hot=True, created_at__gte=_hot_cutoff, is_draft=False, is_deleted_by_moderation=False)
        .order_by('-like_count')[:5]
    )
    cache.set('explore_hot_today', result, 300)  # 5 minutes
    return result


# ─── Stories ──────────────────────────────────────────────────────────────────

def _get_active_stories_for_user(user):
    """Return stories from followed users that haven't expired yet."""
    now = timezone.now()
    if not user.is_authenticated:
        return []
    following_ids = list(Follow.objects.filter(follower=user).values_list('following_id', flat=True))
    return list(
        Story.objects.filter(
            user_id__in=following_ids + [user.id],
            expires_at__gt=now,
        ).select_related('user', 'user__profile').order_by('-created_at')[:30]
    )


def stories_list(request):
    """Stories page listing active stories from followed users."""
    now = timezone.now()
    if request.user.is_authenticated:
        following_ids = list(Follow.objects.filter(follower=request.user).values_list('following_id', flat=True))
        stories = Story.objects.filter(
            user_id__in=following_ids + [request.user.id],
            expires_at__gt=now,
        ).select_related('user', 'user__profile').order_by('user_id', '-created_at')
    else:
        stories = Story.objects.filter(expires_at__gt=now).select_related('user', 'user__profile').order_by('-created_at')[:30]

    # Group by user
    from itertools import groupby
    grouped = []
    for uid, group in groupby(stories, key=lambda s: s.user_id):
        group_list = list(group)
        grouped.append({
            'user': group_list[0].user,
            'stories': group_list,
            'has_unread': request.user.is_authenticated and any(
                not StoryView.objects.filter(story=s, viewer=request.user).exists()
                for s in group_list
            ),
        })

    context = {'story_groups': grouped}
    return render(request, 'frontend/stories.html', context)


@login_required
def create_story(request):
    if request.method == 'POST':
        _story_key = f'story_rate_{request.user.id}'
        if cache.get(_story_key, 0) >= 10:
            return JsonResponse({'error': 'Story limit reached. You can post up to 10 stories per day.'}, status=429)
        content = request.POST.get('content', '').strip()
        _VALID_BG = {'#0ea5e9','#8b5cf6','#10b981','#f97316','#ef4444','#ec4899','#f59e0b','#1e293b','#6366f1','#14b8a6'}
        bg_color = request.POST.get('bg_color', '#0ea5e9')
        if bg_color not in _VALID_BG:
            bg_color = '#0ea5e9'
        image = request.FILES.get('image')

        if not content and not image:
            return JsonResponse({'error': 'Provide text or an image.'}, status=400)

        if image:
            if image.size > 10 * 1024 * 1024:
                return JsonResponse({'error': 'Image must be under 10 MB.'}, status=400)
            header = image.read(12)
            image.seek(0)
            is_webp = header[:4] == b'RIFF' and header[8:12] == b'WEBP'
            _STORY_MAGIC = [b'\xff\xd8\xff', b'\x89PNG\r\n\x1a\n', b'GIF87a', b'GIF89a']
            if not any(header.startswith(sig) for sig in _STORY_MAGIC) and not is_webp:
                return JsonResponse({'error': 'Image must be JPEG, PNG, GIF, or WebP.'}, status=400)

        story = Story.objects.create(
            user=request.user,
            content=content,
            image=image,
            bg_color=bg_color,
            expires_at=timezone.now() + timedelta(hours=24),
        )
        cache.set(_story_key, cache.get(_story_key, 0) + 1, 86400)
        return JsonResponse({'success': True, 'story_id': story.id})
    return JsonResponse({'error': 'POST required'}, status=405)


def view_story(request, story_id):
    story = get_object_or_404(Story, id=story_id)
    if story.is_expired:
        return JsonResponse({'error': 'Story has expired'}, status=410)
    if request.user.is_authenticated:
        StoryView.objects.get_or_create(story=story, viewer=request.user)
    data = {
        'id': story.id,
        'user': story.user.username,
        'content': story.content,
        'bg_color': story.bg_color,
        'image_url': story.image.url if story.image else '',
        'expires_at': story.expires_at.isoformat(),
        'views_count': story.views.count(),
    }
    return JsonResponse(data)


@login_required
@require_POST
def delete_story(request, story_id):
    story = get_object_or_404(Story, id=story_id, user=request.user)
    story.delete()
    return JsonResponse({'success': True})


# ─── Creator Analytics ────────────────────────────────────────────────────────

@login_required
def analytics_dashboard(request):
    """Aggregate creator analytics dashboard for the logged-in user."""
    now = timezone.now()
    user = request.user

    user_posts = Post.objects.filter(user=user, is_draft=False, is_deleted_by_moderation=False)

    # All-time totals
    total_posts = user_posts.count()
    total_likes = PostAction.objects.filter(post__user=user, action='like').count()
    total_saves = PostAction.objects.filter(post__user=user, action='save').count()
    total_comments = Comment.objects.filter(post__user=user).count()
    total_views = PostView.objects.filter(post__user=user).count()
    total_debates = Debate.objects.filter(post__user=user).count()
    total_followers = user.follower_links.count()

    # Follower growth: followers gained in last 30 days
    thirty_ago = now - timedelta(days=30)
    new_followers_30d = user.follower_links.filter(created_at__gte=thirty_ago).count()

    # Top 5 posts by likes
    top_by_likes = list(
        user_posts.annotate(
            like_count=Count('actions', filter=Q(actions__action='like'), distinct=True),
            comment_count=Count('comments', distinct=True),
        ).order_by('-like_count')[:5]
    )

    # Top 5 posts by comments
    top_by_comments = list(
        user_posts.annotate(
            like_count=Count('actions', filter=Q(actions__action='like'), distinct=True),
            comment_count=Count('comments', distinct=True),
        ).order_by('-comment_count')[:5]
    )

    # Posts per day for last 30 days
    from django.db.models.functions import TruncDate as _TruncDate
    post_by_day = {
        str(r['day']): r['n']
        for r in user_posts.filter(created_at__gte=thirty_ago)
        .annotate(day=_TruncDate('created_at'))
        .values('day')
        .annotate(n=Count('id'))
    }

    # Follower growth per day for last 30 days
    follower_by_day = {
        str(r['day']): r['n']
        for r in user.follower_links.filter(created_at__gte=thirty_ago)
        .annotate(day=_TruncDate('created_at'))
        .values('day')
        .annotate(n=Count('id'))
    }

    daily_chart = []
    for i in range(29, -1, -1):
        day = (now - timedelta(days=i)).date()
        key = str(day)
        daily_chart.append({
            'date': day.strftime('%b %d'),
            'posts': post_by_day.get(key, 0),
            'followers': follower_by_day.get(key, 0),
        })

    # Likes gained in last 30 days
    likes_30d = PostAction.objects.filter(
        post__user=user, action='like', created_at__gte=thirty_ago
    ).count()

    # Engagement rate: (likes + comments + saves) / views * 100
    engagement_rate = round((total_likes + total_comments + total_saves) / total_views * 100, 1) if total_views > 0 else 0

    # Category breakdown of user's posts
    from django.db.models import FloatField
    cat_breakdown = list(
        user_posts.values('category').annotate(n=Count('id')).order_by('-n')[:6]
    )

    context = {
        'total_posts': total_posts,
        'total_likes': total_likes,
        'total_saves': total_saves,
        'total_comments': total_comments,
        'total_views': total_views,
        'total_debates': total_debates,
        'total_followers': total_followers,
        'new_followers_30d': new_followers_30d,
        'likes_30d': likes_30d,
        'engagement_rate': engagement_rate,
        'top_by_likes': top_by_likes,
        'top_by_comments': top_by_comments,
        'daily_chart': daily_chart,
        'daily_chart_json': json.dumps(daily_chart),
        'cat_breakdown': cat_breakdown,
    }
    return render(request, 'frontend/analytics_dashboard.html', context)


@login_required
def creator_analytics(request, post_id):
    post = get_object_or_404(Post, id=post_id, user=request.user)
    now = timezone.now()

    total_views = PostView.objects.filter(post=post).count()
    total_likes = PostAction.objects.filter(post=post, action='like').count()
    total_saves = PostAction.objects.filter(post=post, action='save').count()
    total_comments = Comment.objects.filter(post=post).count()
    total_debates = Debate.objects.filter(post=post).count()

    # All-time reaction breakdown
    reaction_breakdown = {}
    for action_choice, _ in PostAction.ACTION_CHOICES:
        reaction_breakdown[action_choice] = PostAction.objects.filter(post=post, action=action_choice).count()

    # Last 30 days daily data (multi-series: views, likes, comments)
    insights_by_date = {
        ins.date: ins
        for ins in PostInsight.objects.filter(post=post, date__gte=(now - timedelta(days=29)).date())
    }
    daily_data = []
    for i in range(29, -1, -1):
        day = (now - timedelta(days=i)).date()
        ins = insights_by_date.get(day)
        daily_data.append({
            'date': day.strftime('%b %d'),
            'views': ins.unique_viewers if ins else 0,
            'likes': ins.likes_count if ins else 0,
            'comments': ins.comments_count if ins else 0,
            'saves': ins.saves_count if ins else 0,
        })

    # Week-over-week comparison (last 7 days vs prior 7 days)
    this_week_views = sum(d['views'] for d in daily_data[-7:])
    prev_week_views = sum(d['views'] for d in daily_data[-14:-7])
    this_week_likes = sum(d['likes'] for d in daily_data[-7:])
    prev_week_likes = sum(d['likes'] for d in daily_data[-14:-7])

    def _pct_change(cur, prev):
        if prev == 0:
            return None
        return round((cur - prev) / prev * 100, 1)

    views_wow = _pct_change(this_week_views, prev_week_views)
    likes_wow = _pct_change(this_week_likes, prev_week_likes)

    # Best hour to post: group all PostActions on this post by hour-of-day
    from django.db.models.functions import ExtractHour
    hour_counts = (
        PostAction.objects.filter(post=post, action='like')
        .annotate(hour=ExtractHour('created_at'))
        .values('hour')
        .annotate(cnt=Count('id'))
        .order_by('-cnt')
    )
    best_hour = None
    if hour_counts:
        best_h = hour_counts[0]['hour']
        period = 'AM' if best_h < 12 else 'PM'
        display_h = best_h % 12 or 12
        best_hour = f"{display_h} {period}"

    # Engagement rate = (likes + comments + saves) / views * 100
    total_engagements = total_likes + total_comments + total_saves
    engagement_rate = round((total_engagements / total_views * 100), 1) if total_views > 0 else 0

    # Recent viewers (last 30, with profile data)
    recent_viewers = list(
        PostView.objects.filter(post=post)
        .select_related('user', 'user__profile')
        .order_by('-viewed_at')[:30]
    )

    # Unique viewer count by day bucket for the sparkline (already in daily_data)
    # Top engagers: users who liked, commented, or reacted — sorted by total actions
    from collections import defaultdict
    engager_scores = defaultdict(lambda: {'user': None, 'likes': 0, 'comments': 0, 'reactions': 0})

    for pa in PostAction.objects.filter(post=post).select_related('user', 'user__profile'):
        e = engager_scores[pa.user_id]
        e['user'] = pa.user
        if pa.action == 'like':
            e['likes'] += 1
        else:
            e['reactions'] += 1

    for cm in Comment.objects.filter(post=post).select_related('user', 'user__profile'):
        e = engager_scores[cm.user_id]
        e['user'] = cm.user
        e['comments'] += 1

    top_engagers = sorted(
        [v for v in engager_scores.values() if v['user']],
        key=lambda x: x['likes'] * 3 + x['comments'] * 2 + x['reactions'],
        reverse=True,
    )[:10]

    context = {
        'post': post,
        'total_views': total_views,
        'total_likes': total_likes,
        'total_saves': total_saves,
        'total_comments': total_comments,
        'total_debates': total_debates,
        'reaction_breakdown': reaction_breakdown,
        'daily_data': daily_data,
        'daily_data_json': json.dumps(daily_data),
        'engagement_rate': engagement_rate,
        'reading_time': post.reading_time_minutes,
        'word_count': post.word_count,
        'views_wow': views_wow,
        'likes_wow': likes_wow,
        'best_hour': best_hour,
        'recent_viewers': recent_viewers,
        'top_engagers': top_engagers,
        'unique_viewer_count': len(set(v.user_id for v in recent_viewers)),
    }
    return render(request, 'frontend/creator_analytics.html', context)


@login_required
def refresh_post_insight(request, post_id):
    """Recalculate and store today's PostInsight for a post (called on-demand)."""
    post = get_object_or_404(Post, id=post_id, user=request.user)
    today = timezone.now().date()
    insight, _ = PostInsight.objects.get_or_create(post=post, date=today)
    insight.unique_viewers = PostView.objects.filter(post=post).count()
    insight.likes_count = PostAction.objects.filter(post=post, action='like').count()
    insight.saves_count = PostAction.objects.filter(post=post, action='save').count()
    insight.comments_count = Comment.objects.filter(post=post).count()
    insight.debates_count = Debate.objects.filter(post=post).count()
    insight.save()
    return JsonResponse({'success': True})


# ─── Close Friends ────────────────────────────────────────────────────────────

@login_required
def close_friends_list_view(request):
    """Manage close friends list."""
    close_friends = CloseFriend.objects.filter(user=request.user).select_related('friend', 'friend__profile')
    following = Follow.objects.filter(follower=request.user).select_related('following', 'following__profile')
    cf_ids = set(close_friends.values_list('friend_id', flat=True))

    context = {
        'close_friends': close_friends,
        'following': following,
        'cf_ids': cf_ids,
    }
    return render(request, 'frontend/close_friends.html', context)


@login_required
def toggle_close_friend(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    friend_id = request.POST.get('user_id')
    try:
        friend = User.objects.get(id=friend_id)
    except User.DoesNotExist:
        return JsonResponse({'error': 'User not found'}, status=404)

    if friend == request.user:
        return JsonResponse({'error': 'Cannot add yourself'}, status=400)

    cf, created = CloseFriend.objects.get_or_create(user=request.user, friend=friend)
    if not created:
        cf.delete()
        return JsonResponse({'action': 'removed', 'username': friend.username})
    return JsonResponse({'action': 'added', 'username': friend.username})


# ─── User Suggestions (People You May Know) ───────────────────────────────────

@login_required
def people_you_may_know(request):
    """People You May Know page using pre-computed UserSuggestion."""
    suggestions = (
        UserSuggestion.objects
        .filter(user=request.user)
        .select_related('suggested_user', 'suggested_user__profile')
        .order_by('-score')[:30]
    )

    # Fallback: 2nd-degree follows if no pre-computed data
    if not suggestions.exists():
        following_ids = set(Follow.objects.filter(follower=request.user).values_list('following_id', flat=True))
        candidates = (
            Follow.objects.filter(follower_id__in=following_ids)
            .exclude(following=request.user)
            .exclude(following_id__in=following_ids)
            .select_related('following', 'following__profile')
            .values('following_id', 'following__username')
            .annotate(c=Count('id'))
            .order_by('-c')[:20]
        )
        suggestions = None
        context = {
            'suggestions': None,
            'fallback_candidates': candidates,
        }
    else:
        context = {
            'suggestions': suggestions,
            'fallback_candidates': None,
        }

    # Enrich with follow state
    if request.user.is_authenticated:
        followed_ids = set(Follow.objects.filter(follower=request.user).values_list('following_id', flat=True))
        context['followed_ids'] = followed_ids

    return render(request, 'frontend/people_you_may_know.html', context)


# ─── Post Audience Update ─────────────────────────────────────────────────────

@login_required
@require_POST
def update_post_audience(request, post_id):
    post = get_object_or_404(Post, id=post_id, user=request.user)
    audience = request.POST.get('audience', 'public')
    if audience not in ('public', 'followers', 'close_friends'):
        return JsonResponse({'error': 'Invalid audience'}, status=400)
    post.audience = audience
    post.save(update_fields=['audience', 'updated_at'])
    return JsonResponse({'success': True, 'audience': audience})


# ─── Profile Highlights ───────────────────────────────────────────────────────

@login_required
@require_POST
def toggle_highlight(request):
    from users.models import ProfileHighlight
    post_id = (request.POST.get('post_id') or '').strip()
    post_obj = get_object_or_404(Post, id=post_id)
    hl, created = ProfileHighlight.objects.get_or_create(user=request.user, post=post_obj)
    if not created:
        hl.delete()
        return JsonResponse({'success': True, 'highlighted': False})
    count = ProfileHighlight.objects.filter(user=request.user).count()
    if count > 6:
        hl.delete()
        return JsonResponse({'success': False, 'error': 'Max 6 highlights allowed'}, status=400)
    return JsonResponse({'success': True, 'highlighted': True})


# ─── Debate Round Timer ───────────────────────────────────────────────────────

@login_required
@require_POST
def debate_set_timer(request, debate_id):
    debate = get_object_or_404(Debate, id=debate_id, status='accepted')
    if request.user not in [debate.initiator, debate.target]:
        return JsonResponse({'error': 'Not a participant'}, status=403)
    try:
        minutes = max(5, min(60, int(request.POST.get('minutes', 10))))
    except (ValueError, TypeError):
        minutes = 10
    debate.round_duration_minutes = minutes
    debate.round_ends_at = timezone.now() + timedelta(minutes=minutes)
    debate.save(update_fields=['round_duration_minutes', 'round_ends_at', 'updated_at'])
    return JsonResponse({'success': True, 'ends_at': debate.round_ends_at.isoformat(), 'minutes': minutes})


def debate_timer_status(request, debate_id):
    debate = get_object_or_404(Debate, id=debate_id)
    remaining = None
    if debate.round_ends_at:
        remaining = max(0, int((debate.round_ends_at - timezone.now()).total_seconds()))
    return JsonResponse({
        'round_duration_minutes': debate.round_duration_minutes,
        'ends_at': debate.round_ends_at.isoformat() if debate.round_ends_at else None,
        'remaining_seconds': remaining,
    })


# ─── Read Later Queue ─────────────────────────────────────────────────────────

@login_required
def read_later_list(request):
    if request.method == 'POST' and request.POST.get('clear_done'):
        ReadLater.objects.filter(user=request.user, is_read=True).delete()
        return redirect('read_later')
    items = ReadLater.objects.filter(user=request.user).select_related(
        'post', 'post__user', 'post__user__profile'
    )
    unread = items.filter(is_read=False)
    done = items.filter(is_read=True)
    return render(request, 'frontend/read_later.html', {
        'unread': unread,
        'done': done,
    })


@login_required
@require_POST
def toggle_read_later(request):
    post_id = (request.POST.get('post_id') or '').strip()
    post_obj = get_object_or_404(Post, id=post_id)
    rl, created = ReadLater.objects.get_or_create(user=request.user, post=post_obj)
    if not created:
        rl.delete()
        return JsonResponse({'success': True, 'saved': False})
    return JsonResponse({'success': True, 'saved': True})


@login_required
@require_POST
def mark_read_later_done(request):
    post_id = (request.POST.get('post_id') or '').strip()
    ReadLater.objects.filter(user=request.user, post_id=post_id).update(is_read=True)
    return JsonResponse({'success': True})


# ─── Mutual Draw (Agree to Disagree) ─────────────────────────────────────────

@login_required
@require_POST
def debate_propose_draw(request, debate_id):
    debate = get_object_or_404(Debate, id=debate_id, status='accepted')
    if request.user not in [debate.initiator, debate.target]:
        return JsonResponse({'error': 'Not a participant'}, status=403)
    if debate.outcome == 'draw':
        return JsonResponse({'success': True, 'already_draw': True})
    if debate.draw_proposed_by_id and debate.draw_proposed_by_id != request.user.id:
        # Other side already proposed — auto-accept
        debate.outcome = 'draw'
        debate.status = 'completed'
        debate.draw_proposed_by = None
        debate.save(update_fields=['outcome', 'status', 'draw_proposed_by', 'updated_at'])
        return JsonResponse({'success': True, 'draw_accepted': True})
    debate.draw_proposed_by = request.user
    debate.save(update_fields=['draw_proposed_by', 'updated_at'])
    return JsonResponse({'success': True, 'draw_proposed': True})


# ─── Reaction Insights (also used in discussion page) ────────────────────────

def post_reaction_insights(request, post_id):
    """Same as post_reaction_users but accessible from discussion page."""
    action = request.GET.get('action', '')
    if action not in ['hot', 'debatable', 'agree', 'surprising', 'like']:
        return JsonResponse({'success': False, 'error': 'Invalid action'}, status=400)
    post = get_object_or_404(Post, id=post_id)
    actions = PostAction.objects.filter(post=post, action=action).select_related(
        'user', 'user__profile'
    ).order_by('-created_at')[:50]
    users = []
    for a in actions:
        p = getattr(a.user, 'profile', None)
        users.append({'username': a.user.username, 'avatar_url': p.get_picture_url if p else ''})
    return JsonResponse({'success': True, 'users': users, 'action': action,
                         'label': dict(Post.MOOD_CHOICES).get(action, action)})


# ── Account Deletion ────────────────────────────────────────────────────────

@login_required
def account_delete(request):
    if request.method == 'POST':
        confirm = request.POST.get('confirm_username', '').strip()
        if confirm != request.user.username:
            messages.error(request, 'Username did not match. Account not deleted.')
            return redirect('account_delete')
        try:
            profile = request.user.profile
            profile.deletion_requested_at = timezone.now()
            profile.save(update_fields=['deletion_requested_at'])
        except Exception:
            pass
        logout(request)
        messages.success(request, 'Your account has been scheduled for deletion. You have 30 days to log back in and cancel.')
        return redirect('index')
    return render(request, 'frontend/account_delete.html')


@login_required
@require_POST
def account_delete_cancel(request):
    try:
        profile = request.user.profile
        profile.deletion_requested_at = None
        profile.save(update_fields=['deletion_requested_at'])
        messages.success(request, 'Account deletion cancelled. Welcome back!')
    except Exception:
        pass
    return redirect('profile')


# ── Password Change ──────────────────────────────────────────────────────────

from django.contrib.auth import update_session_auth_hash

@login_required
def password_change(request):
    if request.method == 'POST':
        _pw_key = f'pw_change_rate_{request.user.id}'
        _pw_count = cache.get(_pw_key, 0)
        if _pw_count >= 5:
            messages.error(request, 'Too many attempts. Please wait 15 minutes.')
            return redirect('password_change')
        cache.set(_pw_key, _pw_count + 1, 900)
        current = request.POST.get('current_password', '')
        new_pw = request.POST.get('new_password', '')
        confirm = request.POST.get('confirm_password', '')
        if not request.user.check_password(current):
            messages.error(request, 'Current password is incorrect.')
            return redirect('password_change')
        if len(new_pw) < 8:
            messages.error(request, 'New password must be at least 8 characters.')
            return redirect('password_change')
        if new_pw != confirm:
            messages.error(request, 'Passwords do not match.')
            return redirect('password_change')
        request.user.set_password(new_pw)
        request.user.save()
        update_session_auth_hash(request, request.user)
        messages.success(request, 'Password changed successfully.')
        return redirect('profile')
    return render(request, 'frontend/password_change.html')


# ── Private DMs ──────────────────────────────────────────────────────────────

@login_required
def dm_list(request):
    from django.db.models import Max, Subquery, OuterRef
    # Get the latest message per conversation partner
    user = request.user
    sent = DirectMessage.objects.filter(sender=user).values('recipient').annotate(last=Max('created_at'))
    received = DirectMessage.objects.filter(recipient=user).values('sender').annotate(last=Max('created_at'))
    partner_ids = set()
    for r in sent:
        partner_ids.add(r['recipient'])
    for r in received:
        partner_ids.add(r['sender'])
    conversations = []
    for pid in partner_ids:
        partner = User.objects.filter(id=pid).select_related('profile').first()
        if not partner:
            continue
        last_msg = DirectMessage.objects.filter(
            Q(sender=user, recipient=partner) | Q(sender=partner, recipient=user)
        ).order_by('-created_at').first()
        unread = DirectMessage.objects.filter(sender=partner, recipient=user, is_read=False).count()
        conversations.append({'partner': partner, 'last_msg': last_msg, 'unread': unread})
    conversations.sort(key=lambda x: x['last_msg'].created_at if x['last_msg'] else timezone.now(), reverse=True)
    return render(request, 'frontend/dm_list.html', {'conversations': conversations})


@login_required
def dm_thread(request, username):
    partner = get_object_or_404(User, username=username)
    if partner == request.user:
        return redirect('dm_list')

    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    if request.method == 'POST':
        _dm_key = f'dm_rate_{request.user.id}'
        _dm_count = cache.get(_dm_key, 0)
        if _dm_count >= 30:
            return JsonResponse({'error': 'Too many messages. Please wait a minute.'}, status=429)
        cache.set(_dm_key, _dm_count + 1, 60)
        content = request.POST.get('content', '').strip()[:2000]
        if content:
            msg = DirectMessage.objects.create(sender=request.user, recipient=partner, content=content)
            if is_ajax:
                return JsonResponse({'id': msg.id, 'time': timezone.localtime(msg.created_at).strftime('%-I:%M %p')})
        if is_ajax:
            return JsonResponse({'id': None})
        return redirect('dm_thread', username=username)

    messages_qs = DirectMessage.objects.filter(
        Q(sender=request.user, recipient=partner) | Q(sender=partner, recipient=request.user)
    ).order_by('created_at')
    # Mark received messages as read
    DirectMessage.objects.filter(sender=partner, recipient=request.user, is_read=False).update(is_read=True)
    return render(request, 'frontend/dm_thread.html', {
        'partner': partner,
        'messages': messages_qs,
    })


@login_required
def dm_thread_poll(request, username):
    """Long-poll endpoint: returns new messages and read-receipt watermark."""
    partner = get_object_or_404(User, username=username)
    after_id = int(request.GET.get('after', 0) or 0)

    # Mark partner's messages as read
    DirectMessage.objects.filter(sender=partner, recipient=request.user, is_read=False).update(is_read=True)

    # New messages since last seen id
    new_msgs = DirectMessage.objects.filter(
        Q(sender=request.user, recipient=partner) | Q(sender=partner, recipient=request.user),
        id__gt=after_id,
    ).order_by('created_at')

    # Highest id of current user's messages that the partner has read
    read_up_to = (
        DirectMessage.objects.filter(sender=request.user, recipient=partner, is_read=True)
        .order_by('-id').values_list('id', flat=True).first() or 0
    )

    return JsonResponse({
        'read_up_to': read_up_to,
        'messages': [
            {
                'id': m.id,
                'content': m.content,
                'is_own': m.sender_id == request.user.id,
                'time': timezone.localtime(m.created_at).strftime('%-I:%M %p'),
            }
            for m in new_msgs
        ],
    })


# ─── Ban / Unban User ─────────────────────────────────────────────────────────

@login_required
def ban_user(request, username):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    if not _is_configured_moderator(request.user):
        return JsonResponse({'error': 'Forbidden'}, status=403)
    target = get_object_or_404(User, username=username)
    ban_type = request.POST.get('ban_type', UserBan.BAN_TYPE_TEMPORARY)
    reason = request.POST.get('reason', '').strip()
    if not reason:
        return JsonResponse({'error': 'Reason required'}, status=400)
    if len(reason) > 1000:
        return JsonResponse({'error': 'Reason must be under 1000 characters'}, status=400)
    if ban_type not in {UserBan.BAN_TYPE_WARNING, UserBan.BAN_TYPE_TEMPORARY, UserBan.BAN_TYPE_PERMANENT}:
        return JsonResponse({'error': 'Invalid ban type'}, status=400)
    expires_at = None
    if ban_type == UserBan.BAN_TYPE_TEMPORARY:
        try:
            days = int(request.POST.get('days', 7))
            if not (1 <= days <= 3650):
                return JsonResponse({'error': 'Days must be between 1 and 3650'}, status=400)
        except (ValueError, TypeError):
            days = 7
        expires_at = timezone.now() + timedelta(days=days)
    UserBan.objects.filter(user=target, is_active=True).update(is_active=False)
    ban = UserBan.objects.create(
        user=target,
        banned_by=request.user,
        ban_type=ban_type,
        reason=reason,
        expires_at=expires_at,
        is_active=True,
    )
    return JsonResponse({'success': True, 'ban_id': ban.id})


@login_required
def unban_user(request, username):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    if not _is_configured_moderator(request.user):
        return JsonResponse({'error': 'Forbidden'}, status=403)
    target = get_object_or_404(User, username=username)
    updated = UserBan.objects.filter(user=target, is_active=True).update(is_active=False)
    return JsonResponse({'success': True, 'deactivated': updated})


# ─── Post Embed ───────────────────────────────────────────────────────────────

def post_embed(request, post_id):
    post = get_object_or_404(Post, id=post_id)
    post.like_count = PostAction.objects.filter(post=post, action='like').count()
    post.comment_count = Comment.objects.filter(post=post).count()
    return render(request, 'frontend/post_embed.html', {'post': post})


# ─── DM Requests ─────────────────────────────────────────────────────────────

@login_required
def dm_requests_list(request):
    """List pending DM requests for the current user."""
    requests_qs = (
        DMRequest.objects
        .filter(recipient=request.user, status=DMRequest.STATUS_PENDING)
        .select_related('sender', 'sender__profile')
        .order_by('-created_at')
    )
    return render(request, 'frontend/dm_requests.html', {'requests': requests_qs})


@login_required
def dm_request_send(request, username):
    """Send (or display) a DM request to a user you don't mutually follow."""
    partner = get_object_or_404(User, username=username)
    if partner == request.user:
        return JsonResponse({'error': 'Cannot message yourself'}, status=400)

    # Check if they already mutually follow each other — no request needed
    follows_them = Follow.objects.filter(follower=request.user, following=partner).exists()
    follows_back = Follow.objects.filter(follower=partner, following=request.user).exists()
    if follows_them and follows_back:
        return redirect('chats')

    existing = DMRequest.objects.filter(sender=request.user, recipient=partner).first()

    if request.method == 'POST':
        if existing and existing.status == DMRequest.STATUS_ACCEPTED:
            return JsonResponse({'success': True})
        msg_text = request.POST.get('message', '').strip()[:300]
        if existing:
            existing.message = msg_text
            existing.status = DMRequest.STATUS_PENDING
            existing.save(update_fields=['message', 'status', 'updated_at'])
        else:
            DMRequest.objects.create(sender=request.user, recipient=partner, message=msg_text)
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'json' in request.headers.get('Accept', ''):
            return JsonResponse({'success': True})
        return redirect('chats')

    return render(request, 'frontend/dm_request_needed.html', {
        'partner': partner,
        'existing_request': existing,
    })


@login_required
@require_POST
def dm_delete(request):
    msg_id = request.POST.get('message_id')
    msg = get_object_or_404(DirectMessage, id=msg_id, sender=request.user)
    msg.delete()
    return JsonResponse({'success': True})


@login_required
def dm_unread_count(request):
    count = DirectMessage.objects.filter(recipient=request.user, is_read=False).count()
    return JsonResponse({'count': count})


# ── Push Notifications ───────────────────────────────────────────────────────

@login_required
@require_POST
def push_subscribe(request):
    try:
        data = json.loads(request.body)
        endpoint = data.get('endpoint', '').strip()
        p256dh = data.get('keys', {}).get('p256dh', '').strip()
        auth = data.get('keys', {}).get('auth', '').strip()
        if not (endpoint and p256dh and auth):
            return JsonResponse({'error': 'Invalid subscription data'}, status=400)
        ua = request.META.get('HTTP_USER_AGENT', '')[:300]
        PushSubscription.objects.update_or_create(
            endpoint=endpoint,
            defaults={'user': request.user, 'p256dh': p256dh, 'auth': auth, 'user_agent': ua},
        )
        return JsonResponse({'success': True})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
@require_POST
def push_unsubscribe(request):
    try:
        data = json.loads(request.body)
        endpoint = data.get('endpoint', '')
        PushSubscription.objects.filter(user=request.user, endpoint=endpoint).delete()
        return JsonResponse({'success': True})
    except Exception:
        return JsonResponse({'success': False})


def push_vapid_public_key(request):
    return JsonResponse({'publicKey': getattr(settings, 'VAPID_PUBLIC_KEY', '')})


def _send_web_push(user, title, body, url=''):
    """Send a web push to all of a user's subscribed browsers.

    Requires pywebpush and VAPID keys in settings. Silently skips if unavailable.
    """
    try:
        from pywebpush import webpush
    except ImportError:
        return

    vapid_private = getattr(settings, 'VAPID_PRIVATE_KEY', '')
    vapid_email = getattr(settings, 'VAPID_ADMIN_EMAIL', '')
    if not vapid_private or not vapid_email:
        return

    import json as _json
    payload = _json.dumps({'title': title, 'body': body, 'url': url})
    stale_ids = []
    for sub in PushSubscription.objects.filter(user=user):
        try:
            webpush(
                subscription_info={
                    'endpoint': sub.endpoint,
                    'keys': {'p256dh': sub.p256dh, 'auth': sub.auth},
                },
                data=payload,
                vapid_private_key=vapid_private,
                vapid_claims={'sub': f'mailto:{vapid_email}'},
            )
        except Exception as exc:
            if hasattr(exc, 'response') and getattr(exc.response, 'status_code', None) == 410:
                stale_ids.append(sub.id)
    if stale_ids:
        PushSubscription.objects.filter(id__in=stale_ids).delete()


# ── Login Activity Log ────────────────────────────────────────────────────────

@login_required
def login_activity(request):
    from users.models import LoginAttempt
    attempts = LoginAttempt.objects.filter(
        username=request.user.username
    ).order_by('-created_at')[:100]
    return render(request, 'frontend/login_activity.html', {'attempts': attempts})


# ── Active Sessions ───────────────────────────────────────────────────────────

@login_required
def active_sessions(request):
    from django.contrib.sessions.models import Session
    now = timezone.now()
    current_key = request.session.session_key
    user_sessions = []
    for s in Session.objects.filter(expire_date__gt=now):
        try:
            data = s.get_decoded()
        except Exception:
            continue
        if str(data.get('_auth_user_id')) == str(request.user.id):
            user_sessions.append({
                'session_key': s.session_key,
                'expire_date': s.expire_date,
                'is_current': s.session_key == current_key,
            })
    user_sessions.sort(key=lambda x: (not x['is_current'], x['expire_date']))
    return render(request, 'frontend/active_sessions.html', {'sessions': user_sessions})


@login_required
@require_POST
def revoke_session(request):
    from django.contrib.sessions.models import Session
    from django.contrib import messages as django_messages

    if request.POST.get('revoke_all'):
        current_key = request.session.session_key
        now = timezone.now()
        revoked = 0
        for s in Session.objects.filter(expire_date__gt=now):
            if s.session_key == current_key:
                continue
            try:
                if str(s.get_decoded().get('_auth_user_id')) == str(request.user.id):
                    s.delete()
                    revoked += 1
            except Exception:
                pass
        django_messages.success(request, f'Revoked {revoked} other session(s).')
        return redirect('active_sessions')

    key = request.POST.get('session_key', '')
    if not key or key == request.session.session_key:
        django_messages.error(request, 'Cannot revoke the current session.')
        return redirect('active_sessions')
    try:
        s = Session.objects.get(session_key=key)
        decoded = s.get_decoded()
        if str(decoded.get('_auth_user_id')) != str(request.user.id):
            django_messages.error(request, 'Not your session.')
            return redirect('active_sessions')
        s.delete()
        django_messages.success(request, 'Session revoked.')
    except Session.DoesNotExist:
        django_messages.error(request, 'Session not found.')
    return redirect('active_sessions')


# ── Link Preview ─────────────────────────────────────────────────────────────

def _is_ssrf_safe_url(url):
    """Return False if the URL resolves to a private/loopback/link-local address."""
    import ipaddress
    import socket
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        return False
    host = parsed.hostname or ''
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(socket.gethostbyname(host))
    except Exception:
        return False
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified)


@login_required
def link_preview(request):
    url = request.GET.get('url', '').strip()
    if not url:
        return JsonResponse({'error': 'No URL'}, status=400)
    if not _is_ssrf_safe_url(url):
        return JsonResponse({'error': 'Invalid URL'}, status=400)
    try:
        preview = LinkPreview.objects.filter(url=url, fetch_failed=False).first()
        if preview and (timezone.now() - preview.fetched_at).days < 7:
            return JsonResponse({
                'title': preview.title,
                'description': preview.description,
                'image': preview.image_url,
                'site_name': preview.site_name,
            })
        import requests as _req
        from bs4 import BeautifulSoup as _BS
        resp = _req.get(
            url, timeout=5, headers={'User-Agent': 'PickAsideBot/1.0'},
            allow_redirects=False, stream=True,
        )
        raw = b''
        for chunk in resp.iter_content(1024):
            raw += chunk
            if len(raw) >= 1024 * 1024:
                break
        soup = _BS(raw, 'html.parser')
        def og(prop):
            t = soup.find('meta', property=prop) or soup.find('meta', attrs={'name': prop})
            return (t.get('content') or '') if t else ''
        title = og('og:title') or og('twitter:title') or (soup.title.string if soup.title else '') or ''
        desc = og('og:description') or og('twitter:description') or og('description') or ''
        image = og('og:image') or og('twitter:image') or ''
        site_name = og('og:site_name') or ''
        # Strip any HTML tags from OG values before storing
        import html
        title = html.unescape(re.sub(r'<[^>]+>', '', title)).strip()
        desc = html.unescape(re.sub(r'<[^>]+>', '', desc)).strip()
        site_name = html.unescape(re.sub(r'<[^>]+>', '', site_name)).strip()
        LinkPreview.objects.update_or_create(url=url, defaults={
            'title': title[:300], 'description': desc[:500],
            'image_url': image[:500], 'site_name': site_name[:100], 'fetch_failed': False,
        })
        return JsonResponse({'title': title, 'description': desc, 'image': image, 'site_name': site_name})
    except Exception:
        LinkPreview.objects.update_or_create(url=url, defaults={'fetch_failed': True})
        return JsonResponse({'error': 'Could not fetch preview'}, status=200)


# ── Trending Categories ───────────────────────────────────────────────────────

def trending_categories(request):
    from django.utils import timezone as _tz
    since = _tz.now() - timedelta(days=7)
    rows = (
        Post.objects.filter(created_at__gte=since, is_draft=False, is_deleted_by_moderation=False)
        .values('category')
        .annotate(
            post_count=Count('id'),
            total_likes=Count('actions', filter=Q(actions__action='like')),
            total_comments=Count('comments'),
        )
        .order_by('-total_likes', '-total_comments', '-post_count')[:20]
    )
    results = []
    for r in rows:
        score = r['total_likes'] * 3 + r['total_comments'] * 2 + r['post_count']
        results.append({'category': r['category'], 'post_count': r['post_count'], 'score': score})
    results.sort(key=lambda x: x['score'], reverse=True)
    cache.set('trending_categories', results, 3600)
    return JsonResponse({'trending': results})


# ── Report Outcome Notifications ──────────────────────────────────────────────

def _notify_report_outcome(reporter, outcome, content_type='content'):
    """Send in-app notification to the reporter about the outcome of their report."""
    if outcome == 'upheld':
        msg = f'Your report on a {content_type} was reviewed and action was taken. Thank you for keeping PickASide safe.'
    else:
        msg = f'Your report on a {content_type} was reviewed. No action was taken at this time.'
    Notification.objects.create(
        user=reporter,
        notification_type='moderation_warning',
        message=msg,
    )


# ── Cookie Consent ────────────────────────────────────────────────────────────

@require_POST
def cookie_consent(request):
    response = JsonResponse({'success': True})
    response.set_cookie('cookie_consent', 'accepted', max_age=365*24*3600, httponly=False, samesite='Lax')
    return response


# ── Privacy Policy ────────────────────────────────────────────────────────────

def privacy_policy(request):
    return render(request, 'frontend/privacy_policy.html')


# ── Markdown Post Rendering ──────────────────────────────────────────────────

_MD_ALLOWED_TAGS = [
    'a', 'abbr', 'acronym', 'b', 'blockquote', 'br', 'caption', 'code', 'col',
    'colgroup', 'dd', 'del', 'details', 'dfn', 'div', 'dl', 'dt', 'em',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr', 'i', 'img', 'ins', 'kbd',
    'li', 'ol', 'p', 'pre', 'q', 's', 'samp', 'small', 'span', 'strong',
    'sub', 'summary', 'sup', 'table', 'tbody', 'td', 'tfoot', 'th', 'thead',
    'tr', 'tt', 'u', 'ul', 'var',
]
_MD_ALLOWED_ATTRS = {
    '*': ['class'],
    'a': ['href', 'title', 'rel'],
    'img': ['src', 'alt', 'title', 'width', 'height'],
    'td': ['align'], 'th': ['align'],
}
_MD_ALLOWED_PROTOCOLS = ['http', 'https', 'mailto']


def _render_markdown(content):
    """Convert markdown content to sanitized safe HTML (bleach allowlist prevents XSS)."""
    if not content:
        return ''
    html = _markdown.markdown(
        content,
        extensions=['fenced_code', 'codehilite', 'tables', 'nl2br', 'toc'],
        extension_configs={
            'codehilite': {'css_class': 'highlight', 'guess_lang': False},
        },
    )
    clean = _bleach.clean(
        html,
        tags=_MD_ALLOWED_TAGS,
        attributes=_MD_ALLOWED_ATTRS,
        protocols=_MD_ALLOWED_PROTOCOLS,
        strip=True,
    )
    return mark_safe(clean)


@login_required
@require_POST
def dm_request_respond(request, request_id):
    """Accept or reject a DM request."""
    dm_req = get_object_or_404(DMRequest, id=request_id, recipient=request.user)
    action = request.POST.get('action', '')
    if action == 'accept':
        dm_req.status = DMRequest.STATUS_ACCEPTED
    elif action == 'reject':
        dm_req.status = DMRequest.STATUS_REJECTED
    else:
        return JsonResponse({'error': 'Invalid action'}, status=400)
    dm_req.save(update_fields=['status', 'updated_at'])
    return JsonResponse({'success': True, 'status': dm_req.status})


# ─── Advanced Search ──────────────────────────────────────────────────────────

def search_advanced(request):
    query = request.GET.get('q', '').strip()
    category = request.GET.get('category', '').strip()
    sort = request.GET.get('sort', 'newest')
    date_range = request.GET.get('date_range', '')
    min_likes = request.GET.get('min_likes', '')
    min_comments = request.GET.get('min_comments', '')
    author = request.GET.get('author', '').strip()
    has_debate = bool(request.GET.get('has_debate'))

    results = []
    total = 0

    if query or category or author or date_range or min_likes or min_comments or has_debate:
        qs = Post.objects.filter(is_draft=False, is_deleted_by_moderation=False)
        if query:
            qs = qs.filter(Q(title__icontains=query) | Q(content__icontains=query) | Q(hashtags__icontains=query))
        if category:
            qs = qs.filter(category=category)
        if author:
            qs = qs.filter(user__username__icontains=author)
        if date_range:
            now = timezone.now()
            if date_range == 'today':
                qs = qs.filter(created_at__date=now.date())
            elif date_range == 'week':
                qs = qs.filter(created_at__gte=now - timedelta(days=7))
            elif date_range == 'month':
                qs = qs.filter(created_at__gte=now - timedelta(days=30))
            elif date_range == 'year':
                qs = qs.filter(created_at__gte=now - timedelta(days=365))
        qs = qs.annotate(
            like_count=Count('actions', filter=Q(actions__action='like'), distinct=True),
            comment_count=Count('comments', distinct=True),
            debate_count=Count('debates', filter=Q(debates__status='accepted'), distinct=True),
        )
        if min_likes:
            try:
                qs = qs.filter(like_count__gte=int(min_likes))
            except ValueError:
                pass
        if min_comments:
            try:
                qs = qs.filter(comment_count__gte=int(min_comments))
            except ValueError:
                pass
        if has_debate:
            qs = qs.filter(debate_count__gt=0)
        sort_map = {
            'newest': '-created_at',
            'oldest': 'created_at',
            'most_liked': '-like_count',
            'most_discussed': '-comment_count',
        }
        qs = qs.order_by(sort_map.get(sort, '-created_at'))
        qs = qs.select_related('user', 'user__profile')
        total = qs.count()

        paginator = Paginator(qs, 20)
        page_obj = paginator.get_page(request.GET.get('page'))
        results = page_obj.object_list
    else:
        page_obj = None

    return render(request, 'frontend/search_advanced.html', {
        'query': query,
        'category': category,
        'sort': sort,
        'date_range': date_range,
        'min_likes': min_likes,
        'min_comments': min_comments,
        'author': author,
        'has_debate': has_debate,
        'results': results,
        'total': total,
        'page_obj': page_obj,
        'categories': CATEGORY_CHOICES,
    })


# ─── Related Posts ────────────────────────────────────────────────────────────

def related_posts_api(request, post_id):
    """Return up to 5 related posts (same category, exclude current)."""
    post = get_object_or_404(Post, id=post_id)
    qs = (
        Post.objects
        .filter(category=post.category, is_draft=False)
        .exclude(id=post.id)
        .annotate(like_count=Count('actions', filter=Q(actions__action='like'), distinct=True))
        .order_by('-created_at')
        .select_related('user')[:5]
    )
    data = [{'id': str(p.id), 'title': p.title, 'username': p.user.username, 'like_count': p.like_count} for p in qs]
    return JsonResponse({'related': data})


# ─── SSE v2 (notifications + DM count) ───────────────────────────────────────

def notification_stream_v2(request):
    """SSE endpoint that streams both notification count and pending DM request count."""
    if not request.user.is_authenticated:
        from django.http import HttpResponse
        return HttpResponse(status=401)

    import time as _time

    def _event_gen(user):
        import json as _json
        last_notifs = -1
        last_dms = -1
        last_notif_id = None
        for _ in range(60):
            try:
                notifs = Notification.objects.filter(user=user, is_read=False).count()
                dms = DMRequest.objects.filter(recipient=user, status=DMRequest.STATUS_PENDING).count()
                payload = {}
                if notifs != last_notifs or dms != last_dms:
                    payload['notifications'] = notifs
                    payload['dms'] = dms
                # Detect a new notification and emit its message for rich toasts
                latest = Notification.objects.filter(user=user, is_read=False).order_by('-created_at').first()
                if latest and latest.id != last_notif_id and last_notif_id is not None:
                    payload['latest_message'] = latest.message[:120]
                    payload['latest_type'] = latest.notification_type
                if latest:
                    last_notif_id = latest.id
                elif last_notif_id is None:
                    last_notif_id = -1
                if payload:
                    last_notifs = notifs
                    last_dms = dms
                    yield f'data: {_json.dumps(payload)}\n\n'
                _time.sleep(5)
            except Exception:
                break
        yield 'data: {"reconnect":true}\n\n'

    from django.http import StreamingHttpResponse
    response = StreamingHttpResponse(_event_gen(request.user), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response

# ─── Bookmarks ────────────────────────────────────────────────────────────────

@login_required
def bookmarks(request):
    q = request.GET.get('q', '').strip()
    category = request.GET.get('category', '').strip()

    qs = PostAction.objects.filter(
        user=request.user, action='save'
    ).select_related('post', 'post__user', 'post__user__profile').order_by('-created_at')

    if q:
        qs = qs.filter(Q(post__title__icontains=q) | Q(post__content__icontains=q))
    if category:
        qs = qs.filter(post__category=category)

    paginator = Paginator(qs, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    saved_categories = list(
        PostAction.objects.filter(user=request.user, action='save')
        .values_list('post__category', flat=True)
        .distinct()
        .order_by('post__category')
    )

    user_collections = list(
        SaveCollection.objects.filter(user=request.user).annotate(
            item_count=Count('items')
        ).order_by('name')
    )

    return render(request, 'frontend/bookmarks.html', {
        'page_obj': page_obj,
        'bookmarks': page_obj.object_list,
        'q': q,
        'category': category,
        'saved_categories': saved_categories,
        'total': qs.count(),
        'user_collections': user_collections,
    })


@login_required
@require_POST
def toggle_bookmark(request, post_id):
    post = get_object_or_404(Post, id=post_id)
    existing = PostAction.objects.filter(user=request.user, post=post, action='save').first()
    if existing:
        existing.delete()
        saved = False
    else:
        try:
            PostAction.objects.create(user=request.user, post=post, action='save')
            notify_post_author(post, 'author_save', request.user)
        except IntegrityError:
            pass
        saved = True
    save_count = PostAction.objects.filter(post=post, action='save').count()
    return JsonResponse({'success': True, 'saved': saved, 'save_count': save_count})


# ─── User-Curated Lists ───────────────────────────────────────────────────────

@login_required
def user_lists(request):
    from users.models import UserList as _UL, UserListMember as _ULM
    lists = _UL.objects.filter(creator=request.user).annotate(
        member_count=Count('members', distinct=True)
    )
    # Membership counts for lists the user is IN (from others)
    memberships = _ULM.objects.filter(user=request.user).select_related('lst__creator')
    return render(request, 'frontend/user_lists.html', {
        'lists': lists,
        'memberships': memberships,
    })


@login_required
@require_POST
def create_list(request):
    from users.models import UserList as _UL
    name = request.POST.get('name', '').strip()[:60]
    description = request.POST.get('description', '').strip()[:200]
    is_private = request.POST.get('is_private') == '1'
    if not name:
        return JsonResponse({'success': False, 'error': 'List name is required.'}, status=400)
    lst = _UL.objects.create(
        id=str(uuid.uuid4()),
        creator=request.user,
        name=name,
        description=description,
        is_private=is_private,
    )
    return JsonResponse({'success': True, 'list_id': lst.id, 'name': lst.name})


@login_required
@require_POST
def delete_list(request, list_id):
    from users.models import UserList as _UL
    lst = get_object_or_404(_UL, id=list_id, creator=request.user)
    lst.delete()
    return JsonResponse({'success': True})


@login_required
def list_detail(request, list_id):
    from users.models import UserList as _UL
    lst = get_object_or_404(_UL, id=list_id)
    if lst.is_private and lst.creator != request.user:
        from django.http import Http404
        raise Http404
    members = lst.members.select_related('user', 'user__profile')
    member_ids = [m.user_id for m in members]
    blocked_ids = _blocked_user_ids(request.user)
    muted_ids = _muted_user_ids(request.user)
    excluded = blocked_ids | muted_ids
    safe_member_ids = [uid for uid in member_ids if uid not in excluded]

    posts = []
    if safe_member_ids:
        posts_qs = (
            _annotated_feed_posts_queryset()
            .filter(user_id__in=safe_member_ids, is_draft=False, is_deleted_by_moderation=False)
            .order_by('-created_at')
        )
        paginator = Paginator(posts_qs, 15)
        page_obj = paginator.get_page(request.GET.get('page'))
        posts = list(page_obj.object_list)
        _enrich_posts_for_feed(posts, request.user)
        posts = _filter_muted_posts(posts, request.user)
    else:
        page_obj = Paginator([], 15).get_page(1)

    # Is the viewing user a member of this list?
    is_member = request.user.is_authenticated and lst.members.filter(user=request.user).exists()
    user_lists_for_add = []
    if request.user.is_authenticated:
        from users.models import UserList as _UL2
        user_lists_for_add = list(_UL2.objects.filter(creator=request.user).values('id', 'name'))

    return render(request, 'frontend/list_detail.html', {
        'lst': lst,
        'members': members,
        'posts': posts,
        'page_obj': page_obj,
        'is_owner': lst.creator == request.user,
        'is_member': is_member,
        'user_lists_for_add': user_lists_for_add,
    })


@login_required
@require_POST
def add_list_member(request, list_id):
    from users.models import UserList as _UL, UserListMember as _ULM
    lst = get_object_or_404(_UL, id=list_id, creator=request.user)
    username = request.POST.get('username', '').strip()
    try:
        target = User.objects.get(username=username)
    except User.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'User not found.'}, status=404)
    if target == request.user:
        return JsonResponse({'success': False, 'error': 'You cannot add yourself to your own list.'}, status=400)
    _ULM.objects.get_or_create(lst=lst, user=target)
    count = lst.members.count()
    return JsonResponse({'success': True, 'username': target.username, 'member_count': count})


@login_required
@require_POST
def remove_list_member(request, list_id):
    from users.models import UserList as _UL, UserListMember as _ULM
    lst = get_object_or_404(_UL, id=list_id, creator=request.user)
    username = request.POST.get('username', '').strip()
    try:
        target = User.objects.get(username=username)
    except User.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'User not found.'}, status=404)
    _ULM.objects.filter(lst=lst, user=target).delete()
    count = lst.members.count()
    return JsonResponse({'success': True, 'member_count': count})


@login_required
@require_POST
def add_to_list_from_profile(request):
    """Add a user to one of the viewer's lists from a profile page."""
    from users.models import UserList as _UL, UserListMember as _ULM
    list_id = request.POST.get('list_id', '').strip()
    username = request.POST.get('username', '').strip()
    lst = get_object_or_404(_UL, id=list_id, creator=request.user)
    try:
        target = User.objects.get(username=username)
    except User.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'User not found.'}, status=404)
    if target == request.user:
        return JsonResponse({'success': False, 'error': 'Cannot add yourself.'}, status=400)
    _, created = _ULM.objects.get_or_create(lst=lst, user=target)
    return JsonResponse({'success': True, 'created': created, 'list_name': lst.name})


# ─── Blocked Users Management ─────────────────────────────────────────────────

@login_required
def blocked_users_list(request):
    blocks = UserBlock.objects.filter(blocker=request.user).select_related('blocked', 'blocked__profile').order_by('-created_at')
    return render(request, 'frontend/blocked_users.html', {'blocks': blocks})


# ─── Muted Keywords Management ────────────────────────────────────────────────

@login_required
def muted_keywords_page(request):
    keywords = MutedKeyword.objects.filter(user=request.user).order_by('keyword')
    return render(request, 'frontend/muted_keywords.html', {'keywords': keywords})


# ─── Post Report ──────────────────────────────────────────────────────────────

@login_required
@require_POST
def report_post(request, post_id):
    post = get_object_or_404(Post, id=post_id)
    if post.user == request.user:
        return JsonResponse({'error': 'Cannot report your own post'}, status=400)
    reason = request.POST.get('reason', 'spam').strip()
    valid_reasons = [r[0] for r in PostReport.REASON_CHOICES]
    if reason not in valid_reasons:
        reason = 'spam'
    details = request.POST.get('details', '').strip()[:500]
    _, created = PostReport.objects.get_or_create(
        post=post,
        reporter=request.user,
        defaults={'reason': reason, 'details': details},
    )
    if not created:
        return JsonResponse({'success': False, 'already_reported': True})
    return JsonResponse({'success': True})


# ─── Pinned Comments ──────────────────────────────────────────────────────────

@login_required
@require_POST
def pin_comment(request, comment_id):
    comment = get_object_or_404(Comment, id=comment_id)
    if comment.post.user != request.user:
        return JsonResponse({'error': 'Only the post author can pin comments'}, status=403)
    Comment.objects.filter(post=comment.post, is_pinned=True).update(is_pinned=False)
    comment.is_pinned = True
    comment.save(update_fields=['is_pinned'])
    return JsonResponse({'success': True, 'pinned': True})


@login_required
@require_POST
def unpin_comment(request, comment_id):
    comment = get_object_or_404(Comment, id=comment_id)
    if comment.post.user != request.user:
        return JsonResponse({'error': 'Only the post author can unpin comments'}, status=403)
    comment.is_pinned = False
    comment.save(update_fields=['is_pinned'])
    return JsonResponse({'success': True, 'pinned': False})


# ─── Trending Debates Page ────────────────────────────────────────────────────

def trending_debates(request):
    tab = request.GET.get('tab', 'pick_a_side')
    TABS = ['pick_a_side', 'stock', 'review', 'question']
    if tab not in TABS:
        tab = 'pick_a_side'

    now = timezone.now()
    cutoff = now - timedelta(days=7)

    def _score(created_at, raw):
        hours = max(1.0, (now - created_at).total_seconds() / 3600.0)
        return raw / (hours ** 0.6)

    # Cache ranked items per tab for 5 minutes (scores shift slowly)
    cache_key = f'trending_tab_{tab}'
    items = cache.get(cache_key)

    if items is None:
        items = []

        if tab == 'pick_a_side':
            qs = (
                Post.objects
                .filter(is_draft=False, is_deleted_by_moderation=False,
                        post_type=Post.POST_TYPE_DISCUSSION, created_at__gte=cutoff)
                .annotate(
                    comment_count=Count('comments', distinct=True),
                    like_count=Count('actions', filter=Q(actions__action='like'), distinct=True),
                    debate_count=Count('debates', distinct=True),
                    view_count=Count('views', distinct=True),
                    hot_count=Count('actions', filter=Q(actions__action='hot'), distinct=True),
                )
                .select_related('user', 'user__profile')[:150]
            )
            scored = sorted(
                qs,
                key=lambda p: _score(p.created_at,
                    p.like_count * 3 + p.comment_count * 2 + p.debate_count * 5
                    + p.hot_count * 4 + p.view_count * 0.1),
                reverse=True,
            )[:30]
            items = [{'kind': 'post', 'obj': p, 'rank': i + 1} for i, p in enumerate(scored)]

        elif tab == 'stock':
            qs = (
                Post.objects
                .filter(is_draft=False, is_deleted_by_moderation=False,
                        post_type=Post.POST_TYPE_STOCK, created_at__gte=cutoff)
                .annotate(
                    comment_count=Count('comments', distinct=True),
                    like_count=Count('actions', filter=Q(actions__action='like'), distinct=True),
                    debate_count=Count('debates', distinct=True),
                    view_count=Count('views', distinct=True),
                )
                .select_related('user', 'user__profile')[:150]
            )
            scored = sorted(
                qs,
                key=lambda p: _score(p.created_at,
                    p.like_count * 3 + p.comment_count * 2 + p.debate_count * 5
                    + p.view_count * 0.1),
                reverse=True,
            )[:30]
            items = [{'kind': 'post', 'obj': p, 'rank': i + 1} for i, p in enumerate(scored)]

        elif tab == 'review':
            qs = (
                Review.objects
                .filter(is_deleted_by_moderation=False, created_at__gte=cutoff)
                .annotate(
                    action_count=Count('actions', distinct=True),
                    comment_count=Count('comments', distinct=True),
                )
                .select_related('user', 'user__profile')[:150]
            )
            scored = sorted(
                qs,
                key=lambda r: _score(r.created_at,
                    r.agree_count * 3 + r.disagree_count * 2
                    + r.action_count * 2 + r.comment_count * 2),
                reverse=True,
            )[:30]
            items = [{'kind': 'review', 'obj': r, 'rank': i + 1} for i, r in enumerate(scored)]

        elif tab == 'question':
            qs = (
                Question.objects
                .filter(is_deleted_by_moderation=False, created_at__gte=cutoff)
                .annotate(
                    action_count=Count('actions', distinct=True),
                )
                .select_related('user', 'user__profile')[:150]
            )
            scored = sorted(
                qs,
                key=lambda q: _score(q.created_at,
                    q.answer_count * 4 + q.action_count * 2),
                reverse=True,
            )[:30]
            items = [{'kind': 'question', 'obj': q, 'rank': i + 1} for i, q in enumerate(scored)]

        cache.set(cache_key, items, 300)  # cache for 5 minutes

    if request.user.is_authenticated and tab in ('pick_a_side', 'stock'):
        _enrich_posts_for_feed([i['obj'] for i in items if i['kind'] == 'post'], request.user)

    tabs = [
        ('pick_a_side', 'Pick a Side', '⚔️'),
        ('stock', 'Stock', '📈'),
        ('review', 'Review', '⭐'),
        ('question', 'General Question', '❓'),
    ]
    return render(request, 'frontend/trending_debates.html', {
        'items': items,
        'active_tab': tab,
        'tabs': tabs,
    })


# ─── "What You Missed" API ────────────────────────────────────────────────────

@login_required
def what_you_missed(request):
    profile = request.user.profile
    last_seen = profile.last_seen
    if not last_seen:
        return JsonResponse({'posts': []})
    posts = (
        Post.objects
        .filter(is_draft=False, is_deleted_by_moderation=False, created_at__gt=last_seen)
        .annotate(like_count=Count('actions', filter=Q(actions__action='like'), distinct=True))
        .order_by('-like_count', '-created_at')
        .select_related('user')[:5]
    )
    data = [{'id': str(p.id), 'title': p.title, 'username': p.user.username, 'like_count': p.like_count} for p in posts]
    return JsonResponse({'posts': data, 'since': last_seen.isoformat()})


# ─── Share Post to DM ─────────────────────────────────────────────────────────

@login_required
@require_POST
def share_post_to_dm(request, post_id):
    post = get_object_or_404(Post, id=post_id, is_draft=False)
    recipient_username = request.POST.get('recipient', '').strip()
    if not recipient_username:
        return JsonResponse({'error': 'Recipient required'}, status=400)
    recipient = get_object_or_404(User, username=recipient_username)
    if recipient == request.user:
        return JsonResponse({'error': 'Cannot share to yourself'}, status=400)
    share_text = f"[Shared post] {post.title} — /discussion/{post.id}/"
    dm = DirectMessage.objects.create(
        sender=request.user,
        recipient=recipient,
        content=share_text,
    )
    return JsonResponse({'success': True, 'dm_id': dm.id})


# ─── User Activity Heatmap API ────────────────────────────────────────────────

def user_activity_heatmap(request, username):
    target_user = get_object_or_404(User, username=username)
    from django.db.models.functions import TruncDate
    cutoff = timezone.now().date() - timedelta(days=364)
    post_counts = (
        Post.objects
        .filter(user=target_user, is_draft=False, created_at__date__gte=cutoff)
        .annotate(day=TruncDate('created_at'))
        .values('day')
        .annotate(count=Count('id'))
    )
    comment_counts = (
        Comment.objects
        .filter(user=target_user, created_at__date__gte=cutoff)
        .annotate(day=TruncDate('created_at'))
        .values('day')
        .annotate(count=Count('id'))
    )
    day_map = {}
    for row in post_counts:
        key = row['day'].isoformat()
        day_map[key] = day_map.get(key, 0) + row['count']
    for row in comment_counts:
        key = row['day'].isoformat()
        day_map[key] = day_map.get(key, 0) + row['count']
    return JsonResponse({'heatmap': day_map})


# ─── Bulk Notification Management ─────────────────────────────────────────────

@login_required
@require_POST
def bulk_notifications(request):
    action = request.POST.get('action', '')
    notif_type = request.POST.get('type', '')
    qs = Notification.objects.filter(user=request.user)
    if notif_type:
        qs = qs.filter(notification_type=notif_type)
    if action == 'mark_all_read':
        updated = qs.update(is_read=True)
        return JsonResponse({'success': True, 'updated': updated})
    elif action == 'delete_all':
        deleted, _ = qs.delete()
        return JsonResponse({'success': True, 'deleted': deleted})
    return JsonResponse({'error': 'Unknown action'}, status=400)


# ─── Export My Data ───────────────────────────────────────────────────────────

@login_required
def export_my_data(request):
    import zipfile, io
    user = request.user
    profile = user.profile

    profile_data = {
        'username': user.username,
        'email': user.email,
        'bio': profile.bio,
        'website': profile.website,
        'joined': user.date_joined.isoformat(),
        'reputation': profile.reputation_score,
    }

    posts_data = list(
        Post.objects.filter(user=user, is_draft=False)
        .values('id', 'title', 'content', 'category', 'created_at')
        .order_by('-created_at')
    )
    for p in posts_data:
        p['id'] = str(p['id'])
        p['created_at'] = p['created_at'].isoformat()

    comments_data = list(
        Comment.objects.filter(user=user)
        .values('id', 'post_id', 'content', 'vote_type', 'created_at')
        .order_by('-created_at')
    )
    for c in comments_data:
        c['id'] = str(c['id'])
        c['post_id'] = str(c['post_id'])
        c['created_at'] = c['created_at'].isoformat()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('profile.json', json.dumps(profile_data, indent=2))
        zf.writestr('posts.json', json.dumps(posts_data, indent=2))
        zf.writestr('comments.json', json.dumps(comments_data, indent=2))
    buf.seek(0)

    from django.http import HttpResponse
    response = HttpResponse(buf.read(), content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="pickaside-data-{user.username}.zip"'
    return response


# ─── Two-Factor Authentication (TOTP) ────────────────────────────────────────

@login_required
def totp_setup(request):
    import pyotp, qrcode, io, base64
    profile = request.user.profile
    if not profile.totp_secret:
        profile.totp_secret = pyotp.random_base32()
        profile.save(update_fields=['totp_secret'])
    totp = pyotp.TOTP(profile.totp_secret)
    provisioning_uri = totp.provisioning_uri(
        name=request.user.email or request.user.username,
        issuer_name='PickASide',
    )
    img = qrcode.make(provisioning_uri)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    qr_b64 = base64.b64encode(buf.getvalue()).decode()
    return render(request, 'frontend/totp_setup.html', {
        'qr_b64': qr_b64,
        'secret': profile.totp_secret,
        'enabled': profile.totp_enabled,
    })


@login_required
@require_POST
def totp_verify_setup(request):
    import pyotp
    profile = request.user.profile
    code = request.POST.get('code', '').strip().replace(' ', '')
    if not profile.totp_secret:
        return JsonResponse({'error': 'No secret generated'}, status=400)
    totp = pyotp.TOTP(profile.totp_secret)
    if totp.verify(code, valid_window=1):
        profile.totp_enabled = True
        profile.save(update_fields=['totp_enabled'])
        return JsonResponse({'success': True})
    return JsonResponse({'error': 'Invalid code'}, status=400)


@login_required
@require_POST
def totp_disable(request):
    import pyotp
    profile = request.user.profile
    code = request.POST.get('code', '').strip().replace(' ', '')
    if profile.totp_enabled and profile.totp_secret:
        totp = pyotp.TOTP(profile.totp_secret)
        if not totp.verify(code, valid_window=1):
            return JsonResponse({'error': 'Invalid code'}, status=400)
    profile.totp_enabled = False
    profile.totp_secret = ''
    profile.save(update_fields=['totp_enabled', 'totp_secret'])
    return JsonResponse({'success': True})


def totp_login_verify(request):
    """Shown after normal login when 2FA is enabled. Verifies TOTP then completes login."""
    import pyotp
    pending_user_id = request.session.get('totp_pending_user_id')
    if not pending_user_id:
        return redirect('login')
    pending_user = get_object_or_404(User, id=pending_user_id)

    _totp_fail_key = f'totp_fails_{pending_user_id}'
    _totp_fails = cache.get(_totp_fail_key, 0)
    if _totp_fails >= 5:
        del request.session['totp_pending_user_id']
        cache.delete(_totp_fail_key)
        messages.error(request, 'Too many failed attempts. Please log in again.')
        return redirect('login')

    if request.method == 'POST':
        code = request.POST.get('code', '').strip().replace(' ', '')
        try:
            profile = pending_user.profile
        except Exception:
            return redirect('login')
        totp = pyotp.TOTP(profile.totp_secret)
        if totp.verify(code, valid_window=1):
            del request.session['totp_pending_user_id']
            cache.delete(_totp_fail_key)
            login(request, pending_user, backend='django.contrib.auth.backends.ModelBackend')
            return redirect(request.POST.get('next', '/'))
        cache.set(_totp_fail_key, _totp_fails + 1, 900)
        messages.error(request, 'Invalid authenticator code.')
    return render(request, 'frontend/totp_login.html', {'next': request.GET.get('next', '/')})


# ─── Post Similarity Check API ────────────────────────────────────────────────

def post_similarity_check(request):
    title = request.GET.get('title', '').strip()
    if len(title) < 10:
        return JsonResponse({'similar': []})
    words = [w for w in re.split(r'\W+', title.lower()) if len(w) > 3]
    if not words:
        return JsonResponse({'similar': []})
    q = Q()
    for w in words[:6]:
        q |= Q(title__icontains=w)
    similar = (
        Post.objects
        .filter(q, is_draft=False, is_deleted_by_moderation=False)
        .exclude(user=request.user if request.user.is_authenticated else None)
        .values('id', 'title', 'user__username')
        .order_by('-created_at')[:5]
    )
    data = [{'id': str(p['id']), 'title': p['title'], 'username': p['user__username']} for p in similar]
    return JsonResponse({'similar': data})



# ─── Live Debate Rooms ────────────────────────────────────────────────────────

def _check_live_room_transitions(room):
    now = timezone.now()
    if room.status == LiveDebateRoom.STATUS_LIVE and room.ends_at and room.ends_at <= now:
        room.status = LiveDebateRoom.STATUS_VOTING
        room.ended_at = now + timedelta(minutes=5)
        room.save(update_fields=['status', 'ended_at'])
        LiveDebateMessage.objects.create(
            room=room, sender=room.creator,
            content='⏱️ Time is up! Vote for the side that argued best.',
            is_system=True,
        )
    elif room.status == LiveDebateRoom.STATUS_VOTING and room.ended_at and room.ended_at <= now:
        yes_v = room.yes_votes
        no_v = room.no_votes
        winner = 'yes' if yes_v > no_v else ('no' if no_v > yes_v else '')
        room.status = LiveDebateRoom.STATUS_CLOSED
        room.winner_side = winner
        room.save(update_fields=['status', 'winner_side'])


def live_debate_rooms(request):
    live_rooms = list(LiveDebateRoom.objects.filter(status=LiveDebateRoom.STATUS_LIVE).select_related('yes_debater', 'no_debater'))
    voting_rooms = list(LiveDebateRoom.objects.filter(status=LiveDebateRoom.STATUS_VOTING).select_related('yes_debater', 'no_debater'))
    open_rooms = list(LiveDebateRoom.objects.filter(status=LiveDebateRoom.STATUS_OPEN).select_related('creator', 'yes_debater', 'no_debater'))
    return render(request, 'frontend/live_debate_rooms.html', {
        'live_rooms': live_rooms,
        'voting_rooms': voting_rooms,
        'open_rooms': open_rooms,
    })


def live_debate_room_detail(request, room_id):
    room = get_object_or_404(LiveDebateRoom, id=room_id)
    _check_live_room_transitions(room)
    msgs = list(room.messages.select_related('sender').order_by('created_at'))
    can_join = (
        request.user.is_authenticated
        and room.status == LiveDebateRoom.STATUS_OPEN
        and request.user != room.yes_debater
        and request.user != room.no_debater
        and not room.is_full
    )
    user_is_debater = request.user.is_authenticated and (
        request.user == room.yes_debater or request.user == room.no_debater
    )
    user_vote = None
    if request.user.is_authenticated:
        _vote = LiveDebateVote.objects.filter(room=room, voter=request.user).first()
        if _vote:
            user_vote = _vote.winner_side
    return render(request, 'frontend/live_debate_room.html', {
        'room': room,
        'messages': msgs,
        'can_join': can_join,
        'user_is_debater': user_is_debater,
        'user_vote': user_vote,
    })


@login_required
@require_POST
def create_live_debate_room(request):
    title = request.POST.get('title', '').strip()
    description = request.POST.get('description', '').strip()
    creator_side = request.POST.get('creator_side', 'yes')
    duration_raw = request.POST.get('duration', '10')
    if not title:
        return JsonResponse({'success': False, 'error': 'Title is required.'}, status=400)
    try:
        duration = max(5, min(60, int(duration_raw)))
    except (ValueError, TypeError):
        duration = 10
    room = LiveDebateRoom.objects.create(
        id=str(uuid.uuid4()),
        title=title,
        description=description,
        creator=request.user,
        duration_minutes=duration,
        yes_debater=request.user if creator_side == 'yes' else None,
        no_debater=request.user if creator_side == 'no' else None,
    )
    LiveDebateMessage.objects.create(
        room=room, sender=request.user,
        content=f'🎙️ Room created by @{request.user.username}. Waiting for an opponent to join.',
        is_system=True,
    )
    return JsonResponse({'success': True, 'room_id': room.id})


@login_required
@require_POST
def join_live_debate_room(request, room_id):
    room = get_object_or_404(LiveDebateRoom, id=room_id)
    if room.status != LiveDebateRoom.STATUS_OPEN:
        return JsonResponse({'success': False, 'error': 'Room is no longer open.'}, status=400)
    if room.yes_debater == request.user or room.no_debater == request.user:
        return JsonResponse({'success': False, 'error': 'You are already in this room.'}, status=400)
    if room.is_full:
        return JsonResponse({'success': False, 'error': 'Room is full.'}, status=400)
    if not room.yes_debater:
        room.yes_debater = request.user
    else:
        room.no_debater = request.user
    if room.is_full:
        room.status = LiveDebateRoom.STATUS_LIVE
        room.started_at = timezone.now()
        room.ends_at = room.started_at + timedelta(minutes=room.duration_minutes)
        room.save(update_fields=['yes_debater', 'no_debater', 'status', 'started_at', 'ends_at'])
        LiveDebateMessage.objects.create(
            room=room, sender=request.user,
            content=f'🔴 Debate started! @{room.yes_debater.username} (Yes) vs @{room.no_debater.username} (No). You have {room.duration_minutes} minutes.',
            is_system=True,
        )
    else:
        room.save(update_fields=['yes_debater', 'no_debater'])
        LiveDebateMessage.objects.create(
            room=room, sender=request.user,
            content=f'👋 @{request.user.username} joined. Waiting for one more debater…',
            is_system=True,
        )
    return JsonResponse({'success': True})


@login_required
@require_POST
def live_debate_send_message(request, room_id):
    room = get_object_or_404(LiveDebateRoom, id=room_id)
    if room.status != LiveDebateRoom.STATUS_LIVE:
        return JsonResponse({'success': False, 'error': 'Debate is not live.'}, status=400)
    if request.user != room.yes_debater and request.user != room.no_debater:
        return JsonResponse({'success': False, 'error': 'Only debaters can send messages.'}, status=403)
    content = request.POST.get('content', '').strip()
    if not content:
        return JsonResponse({'success': False, 'error': 'Message is empty.'}, status=400)
    if len(content) > 1000:
        return JsonResponse({'success': False, 'error': 'Message too long.'}, status=400)
    msg = LiveDebateMessage.objects.create(room=room, sender=request.user, content=content)
    side = 'yes' if request.user == room.yes_debater else 'no'
    return JsonResponse({'success': True, 'message': {
        'id': msg.id,
        'content': msg.content,
        'username': request.user.username,
        'side': side,
        'is_system': False,
        'created_at': msg.created_at.strftime('%H:%M'),
    }})


def live_debate_poll_messages(request, room_id):
    room = get_object_or_404(LiveDebateRoom, id=room_id)
    _check_live_room_transitions(room)
    since_id = int(request.GET.get('since', 0) or 0)
    new_msgs = list(room.messages.filter(id__gt=since_id).select_related('sender').order_by('created_at'))
    msgs_data = []
    for m in new_msgs:
        side = 'yes' if m.sender == room.yes_debater else ('no' if m.sender == room.no_debater else '')
        msgs_data.append({
            'id': m.id,
            'content': m.content,
            'username': m.sender.username,
            'side': side,
            'is_system': m.is_system,
            'created_at': m.created_at.strftime('%H:%M'),
        })
    return JsonResponse({
        'messages': msgs_data,
        'status': room.status,
        'yes_votes': room.yes_votes,
        'no_votes': room.no_votes,
    })


@login_required
@require_POST
def live_debate_vote(request, room_id):
    room = get_object_or_404(LiveDebateRoom, id=room_id)
    if room.status != LiveDebateRoom.STATUS_VOTING:
        return JsonResponse({'success': False, 'error': 'Voting is not open.'}, status=400)
    if request.user == room.yes_debater or request.user == room.no_debater:
        return JsonResponse({'success': False, 'error': 'Debaters cannot vote.'}, status=403)
    side = request.POST.get('side', '')
    if side not in ('yes', 'no'):
        return JsonResponse({'success': False, 'error': 'Invalid side.'}, status=400)
    LiveDebateVote.objects.get_or_create(room=room, voter=request.user, defaults={'winner_side': side})
    return JsonResponse({'success': True, 'yes_votes': room.yes_votes, 'no_votes': room.no_votes})


@login_required
def who_viewed_profile(request):
    """Show the current user a list of people who viewed their profile in the last 30 days."""
    profile_obj = Profile.objects.filter(user=request.user).first()
    if profile_obj and profile_obj.hide_profile_views:
        return render(request, 'frontend/who_viewed_profile.html', {'hidden': True})

    cutoff = timezone.now() - timedelta(days=30)
    views = (
        ProfileView.objects.filter(viewed=request.user, viewed_at__gte=cutoff)
        .select_related('viewer__profile')
        .order_by('-viewed_at')
    )
    return render(request, 'frontend/who_viewed_profile.html', {
        'views': views,
        'hidden': False,
    })


@login_required
@require_POST
def set_post_reminder(request, post_id):
    """Create or update a reminder for a post. Clears it when preset is 'clear'."""
    from discussions.models import PostReminder
    post = get_object_or_404(Post, id=post_id, is_draft=False)
    preset = request.POST.get('preset', '').strip()

    if preset == 'clear':
        PostReminder.objects.filter(user=request.user, post=post).delete()
        return JsonResponse({'success': True, 'cleared': True})

    now = timezone.now()
    if preset == '1h':
        remind_at = now + timedelta(hours=1)
    elif preset == 'tomorrow':
        remind_at = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    elif preset == 'next_week':
        remind_at = (now + timedelta(weeks=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    else:
        custom_raw = request.POST.get('remind_at', '').strip()
        if not custom_raw:
            return JsonResponse({'success': False, 'error': 'No time provided.'}, status=400)
        from django.utils.dateparse import parse_datetime
        try:
            naive = parse_datetime(custom_raw)
            if naive is None:
                raise ValueError
            remind_at = timezone.make_aware(naive) if timezone.is_naive(naive) else naive
        except (ValueError, Exception):
            return JsonResponse({'success': False, 'error': 'Invalid datetime.'}, status=400)
        preset = 'custom'

    if remind_at <= now:
        return JsonResponse({'success': False, 'error': 'Reminder time must be in the future.'}, status=400)

    PostReminder.objects.update_or_create(
        user=request.user, post=post,
        defaults={'remind_at': remind_at, 'preset': preset, 'is_sent': False},
    )
    return JsonResponse({'success': True, 'remind_at': remind_at.isoformat()})


def debate_hall_of_fame(request):
    """Public archive of top-rated completed debates."""
    period = request.GET.get('period', 'all_time')
    category = request.GET.get('category', '').strip()

    qs = (
        Debate.objects.filter(status='completed')
        .select_related('initiator', 'target', 'post', 'poll')
        .annotate(
            vote_count=Count('observer_votes', distinct=True),
            msg_count=Count('messages', distinct=True),
            yes_votes=Count(
                'observer_votes',
                filter=Q(observer_votes__winner_side='yes'),
                distinct=True,
            ),
            no_votes=Count(
                'observer_votes',
                filter=Q(observer_votes__winner_side='no'),
                distinct=True,
            ),
        )
    )

    now = timezone.now()
    if period == 'this_week':
        qs = qs.filter(updated_at__gte=now - timedelta(days=7))
    elif period == 'this_month':
        qs = qs.filter(updated_at__gte=now - timedelta(days=30))

    if category:
        qs = qs.filter(post__category=category)

    qs = qs.order_by('-vote_count', '-msg_count', '-updated_at')

    paginator = Paginator(qs, 15)
    page_obj = paginator.get_page(request.GET.get('page'))

    debates = list(page_obj.object_list)
    for d in debates:
        if d.yes_votes > d.no_votes:
            d.winner_label = 'Yes'
            d.winner_user = d.initiator if getattr(d, 'end_controller_side', '') == 'yes' else None
        elif d.no_votes > d.yes_votes:
            d.winner_label = 'No'
            d.winner_user = None
        else:
            d.winner_label = 'Draw' if d.outcome == 'draw' else 'Tied'
            d.winner_user = None

    categories = [c[0] for c in CATEGORY_CHOICES]

    return render(request, 'frontend/debate_hall_of_fame.html', {
        'debates': debates,
        'page_obj': page_obj,
        'period': period,
        'category': category,
        'categories': categories,
        'total': page_obj.paginator.count,
    })


@login_required
def follow_requests_list(request):
    """Page listing incoming pending follow requests."""
    pending = FollowRequest.objects.filter(
        to_user=request.user, status='pending'
    ).select_related('from_user', 'from_user__profile').order_by('-created_at')
    return render(request, 'frontend/follow_requests.html', {
        'requests': pending,
        'count': pending.count(),
    })


@login_required
@require_POST
def approve_follow_request(request, req_id):
    """Approve a follow request — creates a Follow and marks request approved."""
    freq = get_object_or_404(FollowRequest, id=req_id, to_user=request.user, status='pending')
    Follow.objects.get_or_create(follower=freq.from_user, following=request.user)
    freq.status = FollowRequest.STATUS_APPROVED
    freq.save(update_fields=['status', 'updated_at'])
    _send_notification_email(
        freq.from_user,
        f'@{request.user.username} approved your follow request',
        f'Good news! @{request.user.username} approved your follow request.\n\n'
        f'Visit their profile: {getattr(settings, "SITE_URL", "")}/user/{request.user.username}/',
        notif_type='follow',
    )
    return JsonResponse({'success': True})


@login_required
@require_POST
def deny_follow_request(request, req_id):
    """Deny a follow request."""
    freq = get_object_or_404(FollowRequest, id=req_id, to_user=request.user, status='pending')
    freq.status = FollowRequest.STATUS_DENIED
    freq.save(update_fields=['status', 'updated_at'])
    return JsonResponse({'success': True})


@login_required
@require_POST
def toggle_profile_privacy(request):
    """Toggle the is_private flag on the user's profile."""
    profile, _ = Profile.objects.get_or_create(user=request.user, defaults={'username': request.user.username})
    profile.is_private = not profile.is_private
    profile.save(update_fields=['is_private'])
    if not profile.is_private:
        # Profile went public — auto-approve all pending requests
        pending = FollowRequest.objects.filter(to_user=request.user, status='pending')
        for freq in pending:
            Follow.objects.get_or_create(follower=freq.from_user, following=request.user)
        pending.update(status=FollowRequest.STATUS_APPROVED)
    return JsonResponse({'success': True, 'is_private': profile.is_private})


# ═══════════════════════════════════════════════════════════════════════════════
# STOCK PREDICTIONS
# ═══════════════════════════════════════════════════════════════════════════════

_PRICE_CACHE_SECONDS = 600  # 10 minutes

def _fetch_live_price(symbol):
    """Fetch live price for a stock or crypto symbol. Returns float or None."""
    sym = symbol.strip().upper()
    # Yahoo Finance works for stocks (HDFC.NS, AAPL) and crypto (BTC-USD, DOGE-USD)
    for yf_sym in [sym, f"{sym}-USD"]:
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_sym}"
            r = _http_requests.get(url, timeout=6, headers={'User-Agent': 'Mozilla/5.0'})
            if r.status_code == 200:
                data = r.json()
                price = data['chart']['result'][0]['meta']['regularMarketPrice']
                if price and float(price) > 0:
                    return float(price)
        except Exception:
            pass
    # CoinGecko fallback for crypto
    try:
        coin_id = sym.lower().replace('-usd', '').replace('-usdt', '')
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd"
        r = _http_requests.get(url, timeout=6)
        if r.status_code == 200:
            data = r.json()
            if coin_id in data:
                return float(data[coin_id]['usd'])
    except Exception:
        pass
    return None


def _refresh_live_price(sp):
    """Update sp.live_price if stale (>10 min). Returns updated sp."""
    from django.utils import timezone as tz
    now = tz.now()
    stale = sp.price_updated_at is None or (now - sp.price_updated_at).total_seconds() > _PRICE_CACHE_SECONDS
    if stale and sp.status == StockPrediction.STATUS_ACTIVE:
        price = _fetch_live_price(sp.stock_symbol)
        if price:
            sp.live_price = price
            sp.price_updated_at = now
            sp.save(update_fields=['live_price', 'price_updated_at'])
    return sp


@login_required
def create_stock_prediction(request):
    """Render/process the stock prediction creation form."""
    valid_stock_cats = [c[0] for c in STOCK_CATEGORY_CHOICES]
    if request.method == 'GET':
        return render(request, 'frontend/create_stock_prediction.html', {
            'stock_categories': STOCK_CATEGORY_CHOICES,
        })

    # POST
    stock_symbol = request.POST.get('stock_symbol', '').strip().upper()
    stock_name = request.POST.get('stock_name', '').strip()
    currency = request.POST.get('currency', 'USD').strip().upper() or 'USD'
    direction = request.POST.get('direction', '').strip()
    stock_category = request.POST.get('stock_category', '').strip()
    if stock_category not in valid_stock_cats:
        stock_category = 'Other'
    target_price_raw = request.POST.get('target_price', '').strip()
    target_date_raw = request.POST.get('target_date', '').strip()
    entry_price_raw = request.POST.get('entry_price', '').strip()
    hashtags_raw = request.POST.get('hashtags', '').strip()

    valid_currencies = [c[0] for c in StockPrediction.CURRENCY_CHOICES]
    if currency not in valid_currencies:
        currency = 'USD'

    errors = []
    if not stock_symbol:
        errors.append('Stock symbol is required.')
    if direction not in ('up', 'down', 'above', 'below'):
        errors.append('Prediction direction is required.')
    if not target_price_raw:
        errors.append('Target price is required.')
    if not target_date_raw:
        errors.append('Target date is required.')

    target_price = None
    try:
        target_price = float(target_price_raw)
        if target_price <= 0:
            errors.append('Target price must be positive.')
    except (ValueError, TypeError):
        if target_price_raw:
            errors.append('Invalid target price value.')

    try:
        from datetime import date as _date
        target_date = _date.fromisoformat(target_date_raw)
        if target_date <= timezone.now().date():
            errors.append('Target date must be in the future.')
    except (ValueError, TypeError):
        errors.append('Invalid target date.')
        target_date = None

    entry_price = None
    if entry_price_raw:
        try:
            entry_price = float(entry_price_raw)
        except (ValueError, TypeError):
            pass

    # Auto-fetch entry price if not provided; also validates the symbol
    fetched_price = None
    if stock_symbol:
        fetched_price = _fetch_live_price(stock_symbol)
    if not entry_price:
        if fetched_price:
            entry_price = fetched_price
        elif stock_symbol and not errors:
            errors.append(
                f"Could not find a live price for '{stock_symbol}'. "
                "Please verify the ticker symbol is correct, or enter the current price manually."
            )

    # Compute predicted_change_pct from entry price and target price.
    # Clamped to DecimalField(max_digits=8, decimal_places=2) range.
    predicted_change_pct = None
    if entry_price and target_price:
        raw_pct = (target_price - entry_price) / entry_price * 100
        predicted_change_pct = round(max(-999999.99, min(999999.99, raw_pct)), 2)

    # Build auto title if not submitted
    title = request.POST.get('title', '').strip()
    if not title and stock_symbol and direction and target_price:
        dir_word = 'Up' if direction in ('up', 'above') else 'Down'
        title = f"I think {stock_symbol} will go {dir_word} to {target_price:.2f} {currency} by {target_date}"

    if not title:
        errors.append('Could not generate a title — please fill in all fields.')

    _form_data = {
        'stock_symbol': stock_symbol,
        'stock_name': stock_name,
        'currency': currency,
        'direction': direction,
        'stock_category': stock_category,
        'target_price': target_price_raw,
        'target_date': target_date_raw,
        'entry_price': entry_price_raw,
        'hashtags': hashtags_raw,
    }

    if errors:
        for err in errors:
            messages.error(request, err)
        return render(request, 'frontend/create_stock_prediction.html', {
            'form_data': _form_data,
            'stock_categories': STOCK_CATEGORY_CHOICES,
        })

    if check_content_moderation(title):
        messages.error(request, 'Your post contains abusive language.')
        return render(request, 'frontend/create_stock_prediction.html', {'form_data': _form_data})

    hashtag_list = Post.parse_hashtags(hashtags_raw, max_tags=5)

    post = Post.objects.create(
        id=str(uuid.uuid4()),
        user=request.user,
        title=title,
        content='',
        category=stock_category,
        hashtags=', '.join(hashtag_list),
        post_type=Post.POST_TYPE_STOCK,
        yes_label='Agree',
        no_label='Disagree',
    )

    StockPrediction.objects.create(
        post=post,
        stock_symbol=stock_symbol,
        stock_name=stock_name,
        currency=currency,
        target_price=round(target_price, 6) if target_price else 0,
        target_date=target_date,
        direction=direction,
        predicted_change_pct=predicted_change_pct,
        entry_price=entry_price,
        live_price=entry_price,
        price_updated_at=timezone.now() if entry_price else None,
    )

    return redirect('stock_prediction_detail', post_id=post.id)


def stock_prediction_detail(request, post_id):
    """Detail page for a stock prediction."""
    post = get_object_or_404(Post, id=post_id, post_type=Post.POST_TYPE_STOCK)

    # Audience access control
    if post.audience != 'public' and request.user != post.user:
        if not request.user.is_authenticated:
            messages.error(request, 'You must be logged in to view this post.')
            return redirect('login')
        if post.audience == 'followers':
            if not post.user.follower_links.filter(follower=request.user).exists():
                messages.error(request, 'This post is only visible to followers.')
                return redirect('index')
        elif post.audience == 'close_friends':
            if not post.user.close_friends_list.filter(friend=request.user).exists():
                messages.error(request, 'This post is only visible to close friends.')
                return redirect('index')

    try:
        sp = post.stock_prediction
    except StockPrediction.DoesNotExist:
        from django.http import Http404
        raise Http404

    # Refresh live price if stale
    sp = _refresh_live_price(sp)

    is_post_creator = request.user.is_authenticated and request.user == post.user

    _comment_order = ['-likes', 'created_at']
    comments = Comment.objects.filter(post=post).select_related('user', 'user__profile')
    visible_comments = comments.exclude(is_deleted_by_moderation=True)

    agree_comments = visible_comments.filter(vote_type='yes').order_by(*_comment_order)
    disagree_comments = visible_comments.filter(vote_type='no').order_by(*_comment_order)

    agree_count = comments.filter(vote_type='yes').count()
    disagree_count = comments.filter(vote_type='no').count()
    total = agree_count + disagree_count
    agree_pct = round(agree_count / total * 100, 1) if total else 0
    disagree_pct = round(100 - agree_pct, 1) if total else 0

    user_comment = None
    user_has_voted = False
    user_vote_type = None
    user_has_commented = False
    debate_lookup = {}
    blocked_comment_ids = set()
    if request.user.is_authenticated:
        user_comment = Comment.objects.filter(post=post, user=request.user).first()
        if user_comment:
            user_has_voted = True
            user_vote_type = user_comment.vote_type
            user_has_commented = bool((user_comment.content or '').strip())

        visible_comment_owners = list(visible_comments.values_list('user_id', flat=True).distinct())
        accepted_debates = list(
            Debate.objects.filter(
                post=post, target_id__in=visible_comment_owners, status='accepted',
            ).order_by('target_id', '-updated_at')
        )
        latest_accepted_by_target = {}
        for debate in accepted_debates:
            if debate.target_id not in latest_accepted_by_target:
                latest_accepted_by_target[debate.target_id] = debate
        if latest_accepted_by_target:
            debate_ids = [d.id for d in latest_accepted_by_target.values()]
            active_side_counts = {
                (item['debate_id'], item['side']): item['total']
                for item in DebateParticipant.objects.filter(
                    debate_id__in=debate_ids, is_active=True,
                ).values('debate_id', 'side').annotate(total=Count('id'))
            }
            user_participation = {
                p.debate_id: p
                for p in DebateParticipant.objects.filter(debate_id__in=debate_ids, user=request.user)
            }
            for target_id, debate in latest_accepted_by_target.items():
                mode = 'join'
                label = 'Join Debate'
                participant = user_participation.get(debate.id)
                if participant:
                    mode = 'view'
                    label = 'View Debate'
                else:
                    yes_active = active_side_counts.get((debate.id, 'yes'), 0)
                    no_active = active_side_counts.get((debate.id, 'no'), 0)
                    if (debate.yes_supporters > 0 and debate.no_supporters > 0
                            and yes_active >= debate.yes_supporters and no_active >= debate.no_supporters):
                        mode = 'view'
                        label = 'View Debate'
                debate_lookup[target_id] = {'id': debate.id, 'mode': mode, 'label': label, 'chat_url': f'/debates/{debate.id}/chat/'}

        completed_lookup = {}
        for debate in Debate.objects.filter(post=post, target_id__in=visible_comment_owners, status='completed').order_by('target_id', '-updated_at'):
            if debate.target_id not in completed_lookup:
                completed_lookup[debate.target_id] = debate

        pending_debates_qs = list(Debate.objects.filter(post=post, target_id__in=visible_comment_owners, status='pending').order_by('target_id', '-created_at'))
        if pending_debates_qs:
            pending_ids = [d.id for d in pending_debates_qs]
            user_prejoined_set = set(DebateParticipant.objects.filter(debate_id__in=pending_ids, user=request.user).values_list('debate_id', flat=True))
            pending_by_target = {}
            for d in pending_debates_qs:
                if d.target_id not in pending_by_target:
                    pending_by_target[d.target_id] = d
            for target_id, debate in pending_by_target.items():
                if target_id in debate_lookup:
                    continue
                already_in = debate.id in user_prejoined_set or debate.initiator_id == request.user.id
                mode = 'waiting' if already_in else 'join'
                label = 'Waiting…' if already_in else 'Join Debate'
                debate_lookup[target_id] = {'id': debate.id, 'mode': mode, 'label': label, 'chat_url': f'/debates/{debate.id}/chat/'}

        all_visible_ids = [c.id for c in [*agree_comments, *disagree_comments]]
        if all_visible_ids:
            blocked_comment_ids = set(
                DebateParticipant.objects.filter(
                    user=request.user, is_banned=True, debate__comment_id__in=all_visible_ids,
                ).values_list('debate__comment_id', flat=True)
            )

    def _annotate_stock_comments(cmt_list):
        for cmt in cmt_list:
            debate_state = debate_lookup.get(cmt.user_id)
            completed_state = completed_lookup.get(cmt.user_id) if request.user.is_authenticated else None
            cmt.debate_action_mode = debate_state['mode'] if debate_state else 'start'
            cmt.debate_action_label = debate_state['label'] if debate_state else 'Start Debate'
            cmt.debate_chat_url = debate_state.get('chat_url', '') if debate_state else ''
            cmt.debate_pre_join_yes = None
            cmt.debate_pre_join_no = None
            cmt.show_debate_action = False
            if request.user.is_authenticated and request.user != cmt.user:
                cmt.show_debate_action = bool(debate_state) or is_post_creator or (user_has_voted and user_vote_type != cmt.vote_type)
            cmt.show_debate_view_link = bool(completed_state and not debate_state)
            cmt.debate_view_url = f'/debates/{completed_state.id}/chat/' if (completed_state and not debate_state) else ''
            cmt.debate_start_blocked = cmt.id in blocked_comment_ids
            if cmt.debate_start_blocked and cmt.debate_action_mode == 'start':
                cmt.debate_action_mode = 'blocked'
                cmt.debate_action_label = 'Debate Blocked'
                cmt.show_debate_action = True

    if request.user.is_authenticated:
        _annotate_stock_comments(list(agree_comments))
        _annotate_stock_comments(list(disagree_comments))
    else:
        for cmt in [*agree_comments, *disagree_comments]:
            cmt.show_debate_action = False
            cmt.debate_action_mode = 'start'
            cmt.debate_action_label = 'Start Debate'
            cmt.debate_chat_url = ''
            cmt.debate_pre_join_yes = None
            cmt.debate_pre_join_no = None
            cmt.show_debate_view_link = False
            cmt.debate_view_url = ''
            cmt.debate_start_blocked = False

    is_following_post = False
    if request.user.is_authenticated and not is_post_creator:
        is_following_post = PostFollow.objects.filter(user=request.user, post=post).exists()

    is_saved = False
    if request.user.is_authenticated:
        is_saved = PostAction.objects.filter(user=request.user, post=post, action='save').exists()

    top_agree = agree_comments.first()
    top_disagree = disagree_comments.first()

    # Compute movement % for live tracking
    movement_pct = None
    if sp.live_price and sp.entry_price:
        ep = float(sp.entry_price)
        lp = float(sp.live_price)
        if ep > 0:
            movement_pct = round((lp - ep) / ep * 100, 2)

    live_pts = sp.get_live_pts() if (sp.live_price and sp.entry_price) else None

    moderator_usernames = {
        str(n).strip().lower()
        for n in (getattr(settings, 'MODERATOR_USERNAMES', []) or [])
        if str(n).strip()
    }
    is_moderator = request.user.is_authenticated and request.user.username.lower() in moderator_usernames

    context = {
        'post': post,
        'sp': sp,
        'is_post_creator': is_post_creator,
        'is_moderator': is_moderator,
        'agree_comments': agree_comments,
        'disagree_comments': disagree_comments,
        # keep old names for backward compat with template
        'bullish_comments': agree_comments,
        'bearish_comments': disagree_comments,
        'bull_count': agree_count,
        'bear_count': disagree_count,
        'agree_count': agree_count,
        'disagree_count': disagree_count,
        'total_votes': total,
        'bull_pct': agree_pct,
        'bear_pct': disagree_pct,
        'agree_pct': agree_pct,
        'disagree_pct': disagree_pct,
        'user_has_voted': user_has_voted,
        'user_vote_type': user_vote_type,
        'user_has_commented': user_has_commented,
        'user_comment': user_comment,
        'top_agree_id': top_agree.id if top_agree else None,
        'top_disagree_id': top_disagree.id if top_disagree else None,
        'top_bull_id': top_agree.id if top_agree else None,
        'top_bear_id': top_disagree.id if top_disagree else None,
        'movement_pct': movement_pct,
        'live_pts': live_pts,
        'is_following_post': is_following_post,
        'is_saved': is_saved,
    }
    return render(request, 'frontend/stock_prediction.html', context)


@login_required
def create_stock_comment(request, post_id):
    """Vote + comment on a stock prediction (Bullish or Bearish)."""
    post = get_object_or_404(Post, id=post_id, post_type=Post.POST_TYPE_STOCK)
    is_json = request.content_type == 'application/json'
    is_ajax = is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    def _payload():
        bc = post.comments.filter(vote_type='yes').count()
        nc = post.comments.filter(vote_type='no').count()
        total = bc + nc
        return {
            'success': True,
            'bull_count': bc,
            'bear_count': nc,
            'bull_pct': round(bc / total * 100, 1) if total else 0,
            'bear_pct': round(nc / total * 100, 1) if total else 0,
            'reload_required': True,
        }

    def _err(msg, status=400):
        if is_ajax:
            return JsonResponse({'success': False, 'error': msg}, status=status)
        messages.error(request, msg)
        return redirect('stock_prediction_detail', post_id=post_id)

    if request.method != 'POST':
        return _err('Method not allowed.', 405)

    if is_json:
        import json as _json
        try:
            body = _json.loads(request.body)
        except (ValueError, TypeError):
            body = {}
        vote_type = body.get('vote_type')
        content = (body.get('content') or '').strip()
        _rc = body.get('confidence_score', '')
        is_anon = bool(body.get('is_anonymous', False))
    else:
        vote_type = request.POST.get('vote_type')
        content = request.POST.get('content', '').strip()
        _rc = request.POST.get('confidence_score', '')
        is_anon = request.POST.get('is_anonymous') == '1'

    try:
        confidence = max(1, min(10, int(_rc))) if _rc else 5
    except (ValueError, TypeError):
        confidence = 5

    if vote_type not in ('yes', 'no'):
        return _err('Invalid side selected.')

    if content and check_content_moderation(content):
        return _err('Your comment contains abusive language.')

    existing = Comment.objects.filter(post=post, user=request.user).first()
    if existing:
        if existing.content:
            return _err('You have already commented on this prediction.')
        existing.vote_type = vote_type
        existing.confidence_score = confidence
        if content:
            existing.content = content
        existing.save(update_fields=['vote_type', 'confidence_score', 'content', 'updated_at'])
        if is_ajax:
            return JsonResponse(_payload())
        return redirect('stock_prediction_detail', post_id=post_id)

    Comment.objects.create(
        id=str(uuid.uuid4()),
        post=post,
        user=request.user,
        vote_type=vote_type,
        content=content,
        confidence_score=confidence,
        is_anonymous=is_anon,
    )
    try:
        _update_streak(request.user.profile)
    except Exception:
        pass

    if is_ajax:
        return JsonResponse(_payload())
    return redirect('stock_prediction_detail', post_id=post_id)


@login_required
def resolve_stock_prediction(request, post_id):
    """Admin/creator resolves the prediction with the actual stock price."""
    post = get_object_or_404(Post, id=post_id, post_type=Post.POST_TYPE_STOCK)
    sp = get_object_or_404(StockPrediction, post=post)

    moderator_usernames = {
        str(n).strip().lower()
        for n in (getattr(settings, 'MODERATOR_USERNAMES', []) or [])
        if str(n).strip()
    }
    is_moderator = request.user.username.lower() in moderator_usernames
    if not (request.user == post.user or is_moderator):
        return JsonResponse({'success': False, 'error': 'Not authorised.'}, status=403)

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)

    raw_price = request.POST.get('resolved_price', '').strip()
    try:
        resolved_price = float(raw_price)
        if resolved_price <= 0:
            raise ValueError
    except (ValueError, TypeError):
        return JsonResponse({'success': False, 'error': 'Invalid price.'}, status=400)

    outcome = sp.compute_outcome(resolved_price)
    sp.status = StockPrediction.STATUS_RESOLVED
    sp.outcome = outcome
    sp.resolved_price = resolved_price
    sp.resolved_at = timezone.now()
    sp.resolved_by = request.user
    sp.save()

    return JsonResponse({
        'success': True,
        'outcome': outcome,
        'resolved_price': resolved_price,
        'redirect_url': f'/stocks/{post_id}/',
    })


def fetch_live_stock_price(request, post_id):
    """AJAX endpoint: refresh live price for a stock prediction."""
    post = get_object_or_404(Post, id=post_id, post_type=Post.POST_TYPE_STOCK)
    try:
        sp = post.stock_prediction
    except StockPrediction.DoesNotExist:
        return JsonResponse({'success': False}, status=404)

    price = _fetch_live_price(sp.stock_symbol)
    if price:
        sp.live_price = price
        sp.price_updated_at = timezone.now()
        sp.save(update_fields=['live_price', 'price_updated_at'])
        movement_pct = None
        if sp.entry_price and float(sp.entry_price) > 0:
            movement_pct = round((price - float(sp.entry_price)) / float(sp.entry_price) * 100, 2)
        live_pts = sp.get_live_pts(price)
        return JsonResponse({
            'success': True,
            'live_price': price,
            'movement_pct': movement_pct,
            'live_pts': live_pts,
            'updated_at': sp.price_updated_at.strftime('%b %d, %I:%M %p'),
        })
    return JsonResponse({'success': False, 'error': 'Could not fetch price'})


def _build_leaderboard_data(limit=10):
    """Shared leaderboard computation for list + leaderboard pages."""
    resolved_sps = StockPrediction.objects.filter(
        status=StockPrediction.STATUS_RESOLVED
    ).values_list('post_id', 'outcome')
    resolved_map = {pid: outcome for pid, outcome in resolved_sps}

    if not resolved_map:
        return [], 0

    comments = Comment.objects.filter(
        post_id__in=resolved_map.keys()
    ).values('user_id', 'user__username', 'post_id', 'vote_type', 'confidence_score')

    from collections import defaultdict
    user_stats = defaultdict(lambda: {'username': '', 'total': 0, 'correct': 0, 'w_sum': 0, 'w_total': 0})
    for c in comments:
        uid = c['user_id']
        outcome = resolved_map.get(c['post_id'], '')
        correct = (c['vote_type'] == 'yes' and outcome == StockPrediction.OUTCOME_CORRECT) or \
                  (c['vote_type'] == 'no' and outcome == StockPrediction.OUTCOME_WRONG)
        conf = c['confidence_score'] or 5
        user_stats[uid]['username'] = c['user__username']
        user_stats[uid]['total'] += 1
        if correct:
            user_stats[uid]['correct'] += 1
        user_stats[uid]['w_sum'] += conf if correct else 0
        user_stats[uid]['w_total'] += conf

    profiles = {p.user_id: p for p in Profile.objects.filter(user_id__in=user_stats.keys())}

    board = []
    for uid, s in user_stats.items():
        if s['total'] == 0:
            continue
        accuracy = round(s['correct'] / s['total'] * 100, 2)
        weighted = round(s['w_sum'] / s['w_total'] * 100, 2) if s['w_total'] else 0
        profile = profiles.get(uid)
        board.append({
            'username': s['username'],
            'total': s['total'],
            'correct': s['correct'],
            'accuracy': accuracy,
            'weighted_accuracy': weighted,
            'avatar': profile.get_picture_url if profile else '',
        })
    board.sort(key=lambda x: -x['weighted_accuracy'])
    return board[:limit], len(resolved_map)


def discussions_list(request):
    """Dedicated Pick a Side page — browse all discussion posts."""
    from discussions.models import CATEGORY_CHOICES as _CAT_CHOICES
    sort = request.GET.get('sort', 'trending')
    category_filter = request.GET.get('category', '').strip()
    search_query = request.GET.get('q', '').strip()

    qs = Post.objects.filter(
        post_type=Post.POST_TYPE_DISCUSSION,
        is_draft=False,
        is_deleted_by_moderation=False,
    ).select_related('user', 'user__profile').annotate(
        comment_count=Count('comments', distinct=True),
        like_count=Count('actions', filter=Q(actions__action='like'), distinct=True),
        debate_count=Count('debates', distinct=True),
        hot_count=Count('actions', filter=Q(actions__action='hot'), distinct=True),
    )

    if category_filter:
        qs = qs.filter(category=category_filter)
    if search_query:
        qs = qs.filter(
            Q(title__icontains=search_query) |
            Q(content__icontains=search_query) |
            Q(hashtags__icontains=search_query)
        )

    if sort == 'latest':
        qs = qs.order_by('-created_at')
    elif sort == 'debated':
        qs = qs.order_by('-debate_count', '-comment_count', '-created_at')
    else:
        cutoff = timezone.now() - timedelta(days=7)
        qs = qs.filter(created_at__gte=cutoff).order_by(
            '-hot_count', '-like_count', '-debate_count', '-comment_count', '-created_at'
        )

    paginator = Paginator(qs, 15)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'frontend/discussions_list.html', {
        'page_obj': page_obj,
        'sort': sort,
        'category_filter': category_filter,
        'search_query': search_query,
        'categories': _CAT_CHOICES,
    })


def stocks_list(request):
    """Browse all stock predictions with leaderboard sidebar."""
    status_filter = request.GET.get('status', 'active')
    symbol_filter = request.GET.get('symbol', '').strip().upper()
    category_filter = request.GET.get('category', '').strip()
    search_query = request.GET.get('q', '').strip()

    qs = StockPrediction.objects.select_related('post', 'post__user', 'post__user__profile')
    if status_filter in ('active', 'resolved', 'expired'):
        qs = qs.filter(status=status_filter)
    if symbol_filter:
        qs = qs.filter(stock_symbol__icontains=symbol_filter)
    if category_filter:
        qs = qs.filter(post__category=category_filter)
    if search_query:
        qs = qs.filter(
            Q(post__title__icontains=search_query) | Q(stock_symbol__icontains=search_query)
        )

    from django.db.models import Count
    qs = qs.annotate(
        bull_count=Count('post__comments', filter=Q(post__comments__vote_type='yes')),
        bear_count=Count('post__comments', filter=Q(post__comments__vote_type='no')),
    ).order_by('-created_at')

    paginator = Paginator(qs, 15)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    # Cache the leaderboard sidebar — it changes rarely
    leaderboard_cache_key = 'stocks_leaderboard_10'
    leaderboard_data = cache.get(leaderboard_cache_key)
    if leaderboard_data is None:
        leaderboard_data = _build_leaderboard_data(limit=10)
        cache.set(leaderboard_cache_key, leaderboard_data, 300)
    leaderboard, total_resolved = leaderboard_data

    return render(request, 'frontend/stocks_list.html', {
        'page_obj': page_obj,
        'status_filter': status_filter,
        'symbol_filter': symbol_filter,
        'category_filter': category_filter,
        'search_query': search_query,
        'stock_categories': STOCK_CATEGORY_CHOICES,
        'leaderboard': leaderboard,
        'total_resolved': total_resolved,
    })


def stock_leaderboard(request):
    """Full accuracy leaderboard for stock predictors."""
    leaderboard, total_resolved = _build_leaderboard_data(limit=50)
    return render(request, 'frontend/stock_leaderboard.html', {
        'leaderboard': leaderboard,
        'total_resolved': total_resolved,
    })
