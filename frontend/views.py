from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from django.conf import settings
from django.contrib.auth.models import User
from users.models import Profile
from users.serializers import UserSerializer, UserRegistrationSerializer, ProfileSerializer
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login, authenticate, logout
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.db.models import Q, F, Count, IntegerField, ExpressionWrapper
from django.db import DataError
from django.utils import timezone
from datetime import timedelta
import json
import re
import uuid

from discussions.models import CATEGORY_CHOICES, Post, Comment, Debate, DebateMessage, DebateParticipant, CommentModeratorBlock, CommentReaction, PostFollow, PostView, PostAction, Notification, PostEditHistory, CommentEditHistory, DebateMessageEditHistory
from users.models import Follow
from discussions.limits import has_reached_daily_post_limit


_EMOJI_TOKEN_RE = re.compile(r'__EMJ__([0-9A-F]{5,6})__')


def _manual_editor_username():
    return (getattr(settings, 'MANUAL_EDITOR_USERNAME', '') or '').strip()


def _manual_editor_password():
    return getattr(settings, 'MANUAL_EDITOR_PASSWORD', '') or ''


def _manual_editor_email():
    return (getattr(settings, 'MANUAL_EDITOR_EMAIL', '') or '').strip()


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
    if initiator_participant.side != initiator_side:
        initiator_participant.side = initiator_side
        initiator_participant.save(update_fields=['side'])

    target_participant, _ = DebateParticipant.objects.get_or_create(
        debate=debate,
        user=debate.target,
        defaults={'side': target_side, 'is_active': True}
    )
    if target_participant.side != target_side:
        target_participant.side = target_side
        target_participant.save(update_fields=['side'])


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
    """Return avatar URL if profile exists, else empty string."""
    try:
        return user.profile.avatar_url or ''
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

FRONTEND_CATEGORY_STYLES = {
    'Technology': {'icon': '💻', 'color': 'bg-blue-500/10 text-blue-400 border-blue-500/20'},
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


@login_required
@require_POST
def post_action(request, post_id):
    action = request.POST.get('action')
    if action not in ['like', 'save', 'repost']:
        return JsonResponse({'success': False, 'error': 'Invalid action.'}, status=400)

    post = get_object_or_404(Post, id=post_id)
    existing_action = PostAction.objects.filter(user=request.user, post=post, action=action).first()

    if action in ['like', 'save']:
        if existing_action:
            existing_action.delete()
            status = 'removed'
        else:
            PostAction.objects.create(user=request.user, post=post, action=action)
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
        if existing_action:
            like_count = PostAction.objects.filter(post=post, action='like').count()
            save_count = PostAction.objects.filter(post=post, action='save').count()
            repost_count = PostAction.objects.filter(post=post, action='repost').count()
            return JsonResponse({
                'success': True,
                'action': action,
                'status': 'already_reposted',
                'counts': {
                    'like': like_count,
                    'save': save_count,
                    'repost': repost_count
                }
            })

        PostAction.objects.create(user=request.user, post=post, action=action)
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


def index(request):
    """Home page with trending posts and categories"""
    trending_posts = Post.objects.filter(id__isnull=False).exclude(id='').annotate(
        like_count=Count('actions', filter=Q(actions__action='like')),
        save_count=Count('actions', filter=Q(actions__action='save')),
        repost_count=Count('actions', filter=Q(actions__action='repost')),
        yes_count=Count('comments', filter=Q(comments__vote_type='yes')),
        no_count=Count('comments', filter=Q(comments__vote_type='no'))
    ).order_by('-created_at')[:10]
    categories = get_frontend_categories()

    suggested_posts = []
    if request.user.is_authenticated:
        following_users = request.user.following_links.values_list('following', flat=True)
        watched_post_ids = PostView.objects.filter(user=request.user).values_list('post_id', flat=True)

        if following_users:
            followed_posts = Post.objects.filter(user__in=following_users).exclude(id__in=watched_post_ids).exclude(id='').annotate(
                like_count=Count('actions', filter=Q(actions__action='like')),
                save_count=Count('actions', filter=Q(actions__action='save')),
                repost_count=Count('actions', filter=Q(actions__action='repost')),
                yes_count=Count('comments', filter=Q(comments__vote_type='yes')),
                no_count=Count('comments', filter=Q(comments__vote_type='no'))
            ).order_by('-created_at')[:10]
            if followed_posts.exists():
                suggested_posts = list(followed_posts)
        if not suggested_posts:
            suggested_posts = list(Post.objects.exclude(id__in=watched_post_ids).exclude(id='').annotate(
                like_count=Count('actions', filter=Q(actions__action='like')),
                save_count=Count('actions', filter=Q(actions__action='save')),
                repost_count=Count('actions', filter=Q(actions__action='repost')),
                yes_count=Count('comments', filter=Q(comments__vote_type='yes')),
                no_count=Count('comments', filter=Q(comments__vote_type='no'))
            ).order_by('-created_at')[:10])

    active_tab = request.GET.get('tab', 'trending')
    if active_tab == 'suggested' and not request.user.is_authenticated:
        active_tab = 'trending'
    if active_tab == 'suggested':
        posts = suggested_posts
    else:
        posts = trending_posts

    for post in posts:
        yes_count = getattr(post, 'yes_count', 0) or 0
        no_count = getattr(post, 'no_count', 0) or 0
        total_votes = yes_count + no_count
        if total_votes > 0:
            post.yes_percentage = (yes_count * 100.0) / total_votes
            post.no_percentage = 100.0 - post.yes_percentage
        else:
            post.yes_percentage = 0.0
            post.no_percentage = 0.0

    if request.user.is_authenticated:
        post_ids = [post.id for post in posts]
        liked_post_ids = set()
        saved_post_ids = set()
        reposted_post_ids = set()
        if post_ids:
            post_actions = PostAction.objects.filter(
                user=request.user,
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

    context = {
        'posts': posts,
        'active_tab': active_tab,
        'categories': categories,
    }
    return render(request, 'frontend/index.html', context)

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
    post = get_object_or_404(Post, id=post_id)
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

                participant = user_participation.get(debate.id)
                if participant:
                    mode = 'view'
                    label = 'View Debate'
                elif user_has_voted and user_vote_type in ('yes', 'no'):
                    side_limit = debate.yes_supporters if user_vote_type == 'yes' else debate.no_supporters
                    active_side_count = active_side_counts.get((debate.id, user_vote_type), 0)
                    if side_limit and active_side_count >= side_limit:
                        mode = 'view'
                        label = 'View Debate'

                debate_lookup[target_id] = {
                    'id': debate.id,
                    'mode': mode,
                    'label': label,
                    'chat_url': f'/debates/{debate.id}/chat/',
                }

    for comment in yes_comments:
        debate_state = debate_lookup.get(comment.user_id)
        comment.debate_action_mode = debate_state['mode'] if debate_state else 'start'
        comment.debate_action_label = debate_state['label'] if debate_state else 'Start Debate'
        comment.debate_chat_url = debate_state['chat_url'] if debate_state else ''

    for comment in no_comments:
        debate_state = debate_lookup.get(comment.user_id)
        comment.debate_action_mode = debate_state['mode'] if debate_state else 'start'
        comment.debate_action_label = debate_state['label'] if debate_state else 'Start Debate'
        comment.debate_chat_url = debate_state['chat_url'] if debate_state else ''

    now = timezone.now()
    online_cutoff = now - timedelta(minutes=5)
    for comment in [*yes_comments, *no_comments]:
        last_seen = _get_last_seen(comment.user)
        comment.is_online = bool(last_seen and last_seen >= online_cutoff)
        comment.presence_label = _presence_label(last_seen, now=now)

    post_has_comments = comments.exists()
    is_post_creator = request.user == post.user
    can_manage_post_today = is_post_creator and _can_manage_created_today(request.user, post.created_at)
    show_post_submitted = is_post_creator and request.GET.get('created') == '1'

    is_following_post = False
    if request.user.is_authenticated:
        is_following_post = PostFollow.objects.filter(user=request.user, post=post).exists()

    context = {
        'post': post,
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
    user_saved = PostAction.objects.filter(
        user=request.user, action='save'
    ).select_related('post', 'post__user').order_by('-created_at')

    for post in user_posts:
        post.can_delete_today = _can_manage_created_today(request.user, post.created_at)

    for action in user_reposts:
        action.can_remove_today = _can_manage_created_today(request.user, action.created_at)

    profile_obj = Profile.objects.filter(user=request.user).first()
    avatar_url = profile_obj.avatar_url if profile_obj else ''
    profile_last_seen = profile_obj.last_seen if profile_obj else None
    now = timezone.now()
    is_online = bool(profile_last_seen and profile_last_seen >= now - timedelta(minutes=5))
    presence_label = 'Active now' if is_online else _presence_label(profile_last_seen, now=now)

    followers_qs = request.user.follower_links.select_related('follower__profile').order_by('-created_at')
    following_qs = request.user.following_links.select_related('following__profile').order_by('-created_at')

    def _card(u):
        p = getattr(u, 'profile', None)
        return {'username': u.username, 'avatar_url': p.avatar_url if p else ''}

    context = {
        'user_posts': user_posts,
        'user_reposts': user_reposts,
        'user_saved': user_saved,
        'avatar_url': avatar_url,
        'is_online': is_online,
        'presence_label': presence_label,
        'followers_count': request.user.follower_links.count(),
        'following_count': request.user.following_links.count(),
        'followers_list': [_card(f.follower) for f in followers_qs],
        'following_list': [_card(f.following) for f in following_qs],
    }
    return render(request, 'frontend/profile.html', context)

def user_profile(request, username):
    """Public user profile page"""
    try:
        profile_user = User.objects.get(username=username)
    except User.DoesNotExist:
        from django.http import Http404
        raise Http404("User not found")

    user_posts = Post.objects.filter(user=profile_user).exclude(id='').order_by('-created_at')

    profile_obj = Profile.objects.filter(user=profile_user).first()
    avatar_url = profile_obj.avatar_url if profile_obj else ''
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
            'avatar_url': p.avatar_url or '',
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
        if debate.status == 'accepted':
            _ensure_debate_core_participants(debate)
            participation = DebateParticipant.objects.filter(debate=debate, user=request.user).first()
            is_view_only = bool(participation and participation.is_banned)
            can_post = bool(participation and participation.is_active and not participation.is_banned)
            debate.user_is_view_only = is_view_only
            debate.user_can_post = can_post
            debate.user_is_active_participant = can_post
            debate.user_can_rejoin = bool(participation and not participation.is_active and not participation.is_banned)
            debate.user_can_leave = can_post
            debate.user_can_end = bool(
                can_post and debate.end_controller_id == request.user.id
            )
            debate.user_can_moderate = request.user.id == debate.target_id
            if debate.user_can_moderate:
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
            debate.user_can_moderate = False
            debate.moderatable_participants = []

    context = {
        'debates': debates,
        'post_notifications': Notification.objects.filter(
            user=request.user
        ).select_related('post').order_by('-created_at')[:30],
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
def chat_list(request):
    """Return list of active chats sorted by most recent message."""
    from discussions.models import DebateMessage as DMsg
    participations = DebateParticipant.objects.filter(
        user=request.user,
        debate__status='accepted',
        is_banned=False,
    ).select_related(
        'debate__post',
        'debate__initiator',
        'debate__target',
    )

    chats = []
    for p in participations:
        debate = p.debate
        opponent = debate.target if request.user.id == debate.initiator_id else debate.initiator
        last_msg = DMsg.objects.filter(
            debate=debate,
            is_system=False,
        ).order_by('-created_at').first()

        try:
            opp_avatar = opponent.profile.avatar_url or ''
        except Exception:
            opp_avatar = ''

        chats.append({
            'id': str(debate.id),
            'title': debate.post.title,
            'opponent': opponent.username,
            'opponent_avatar': opp_avatar,
            'is_active': p.is_active,
            'last_message': last_msg.content[:100] if last_msg else None,
            'last_message_sender': last_msg.sender.username if last_msg else None,
            'last_message_time': last_msg.created_at.strftime('%b %d, %H:%M') if last_msg else None,
            'last_message_id': last_msg.id if last_msg else 0,
            'updated_at': debate.updated_at.isoformat() if debate.updated_at else '',
        })

    chats.sort(key=lambda x: x['last_message_id'] or 0, reverse=True)
    return JsonResponse({'success': True, 'chats': chats})


@login_required
def notification_count(request):
    pending_count = Debate.objects.filter(
        target=request.user,
        status='pending'
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
    next_url = request.POST.get('next') or request.GET.get('next') or ''

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        if username == _manual_editor_username() and _manual_editor_password():
            _ensure_manual_editor_user()

        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            messages.success(request, 'Welcome back!')
            if next_url:
                return redirect(next_url)
            return redirect('index')
        else:
            messages.error(request, 'Invalid credentials')

    return render(request, 'frontend/login.html', {'next': next_url})

def register_view(request):
    """Registration page"""
    next_url = request.POST.get('next') or request.GET.get('next') or ''

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
            return render(request, 'frontend/register.html')

        # Password validation
        if len(password) < 8:
            messages.error(request, 'Password must be at least 8 characters')
            return render(request, 'frontend/register.html')

        if not any(char.isupper() for char in password):
            messages.error(request, 'Password must contain an uppercase letter')
            return render(request, 'frontend/register.html')

        if not any(char.islower() for char in password):
            messages.error(request, 'Password must contain a lowercase letter')
            return render(request, 'frontend/register.html')

        if not any(char.isdigit() for char in password):
            messages.error(request, 'Password must contain a number')
            return render(request, 'frontend/register.html')

        if not any(char in '!@#$%^&*(),.?":{}|<>' for char in password):
            messages.error(request, 'Password must contain a special character')
            return render(request, 'frontend/register.html')

        try:
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password
            )
            messages.success(request, 'Account created successfully! Please login.')
            if next_url:
                return redirect(f'/login/?next={next_url}')
            return redirect('login')
        except Exception as e:
            messages.error(request, f'Registration failed: {str(e)}')

    return render(request, 'frontend/register.html', {'next': next_url})

def logout_view(request):
    """Logout view"""
    logout(request)
    messages.success(request, 'Logged out successfully')
    return redirect('index')

@login_required
@require_POST
def create_post(request):
    """Create a new post"""
    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        content = request.POST.get('content', '').strip()
        category = request.POST.get('category', '').strip()
        hashtags = request.POST.get('hashtags', '').strip()

        if not title or not category:
            messages.error(request, 'Title and category are required')
            return redirect('index')

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
    # Delete the duplicated post copy (same user, title, content, category)
    Post.objects.filter(
        user=request.user,
        title=original.title,
        content=original.content,
        category=original.category,
    ).exclude(id=original.id).delete()
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

    if request.method == 'POST':
        vote_type = request.POST.get('vote_type')
        content = request.POST.get('content', '').strip()

        if not vote_type or vote_type not in ['yes', 'no']:
            messages.error(request, 'Invalid vote type')
            return redirect('discussion', post_id=post_id)

        # Enforce a single comment per user per post.
        existing_comment = Comment.objects.filter(post=post, user=request.user).first()
        if existing_comment:
            existing_has_content = bool((existing_comment.content or '').strip())

            if existing_has_content:
                messages.error(request, 'You can comment only once on a post.')
                return redirect('discussion', post_id=post_id)

            if not content:
                messages.error(request, 'Comment content is required')
                return redirect('discussion', post_id=post_id)

            existing_comment.vote_type = vote_type
            existing_comment.content = content
            existing_comment.save(update_fields=['vote_type', 'content', 'updated_at'])
            messages.success(request, 'Comment submitted!')
            return redirect('discussion', post_id=post_id)
        else:
            # First time voting - content is optional
            Comment.objects.create(
                id=str(uuid.uuid4()),
                post=post,
                user=request.user,
                vote_type=vote_type,
                content=content if content else ''
            )

            messages.success(request, 'Vote submitted!')
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

        reaction, created = CommentReaction.objects.get_or_create(
            comment=comment,
            user=request.user,
            defaults={'reaction': action}
        )

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

        user_comment = Comment.objects.filter(post=comment.post, user=request.user).first()
        if not user_comment:
            return JsonResponse({'success': False, 'error': 'Please vote yes or no first before starting a debate.'}, status=400)

        accepted_debate = Debate.objects.filter(
            post=comment.post,
            target=target_user,
            status='accepted'
        ).order_by('-updated_at').first()

        if accepted_debate and user_comment:
            _ensure_debate_core_participants(accepted_debate)
            side = user_comment.vote_type
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

            active_side_count = DebateParticipant.objects.filter(
                debate=accepted_debate,
                side=side,
                is_active=True
            ).count()

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
            target=target_user
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
def debate_chat(request, debate_id):
    """Two-person debate chat room"""
    debate = get_object_or_404(
        Debate.objects.select_related('initiator', 'target', 'post'),
        id=debate_id
    )

    if debate.status != 'accepted':
        messages.error(request, 'This conversation is not active.')
        return redirect('notifications')

    _ensure_debate_core_participants(debate)
    participation = DebateParticipant.objects.filter(debate=debate, user=request.user).first()

    if not participation:
        messages.error(request, 'You do not have access to this debate.')
        return redirect('notifications')

    raw_messages = debate.messages.select_related('sender', 'reply_to__sender').all()
    messages_list = [
        {
            'id': message.id,
            'content': _decode_chat_content_from_storage(message.content),
            'sender': message.sender.username,
            'sender_id': message.sender_id,
            'sender_avatar': _safe_avatar_url(message.sender),
            'sender_initial': (message.sender.username[:1] or '?').upper(),
            'reply_to': ({
                'id': message.reply_to.id,
                'sender': message.reply_to.sender.username,
                'content': _decode_chat_content_from_storage(message.reply_to.content),
            } if message.reply_to_id else None),
            'is_own': message.sender_id == request.user.id,
            'can_remove_sender': request.user.id == debate.target_id and message.sender_id != request.user.id,
            'is_edited': message.is_edited,
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
        'is_active_participant': participation.is_active and not participation.is_banned,
        'can_post': participation.is_active and not participation.is_banned,
        'can_rejoin': (not participation.is_active) and (not participation.is_banned),
        'is_view_only': participation.is_banned,
        'can_end_chat': participation.is_active and not participation.is_banned and debate.end_controller_id == request.user.id,
        'current_controller_name': debate.end_controller.username if debate.end_controller else '',
        'current_controller_side': debate.end_controller_side,
        'can_moderate_chat': request.user.id == debate.target_id,
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

    messages = [
        {
            'id': message.id,
            'content': _decode_chat_content_from_storage(message.content),
            'sender': message.sender.username,
            'sender_id': message.sender_id,
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
            'created_at': message.created_at.strftime('%b %d, %I:%M %p'),
            'created_date_label': message.created_at.strftime('%b %d, %Y'),
            'created_time': message.created_at.strftime('%I:%M %p'),
        }
        for message in debate.messages.select_related('sender', 'reply_to__sender').all()
    ]

    return JsonResponse({
        'success': True,
        'messages': messages,
        'active_participants': _active_participants_payload(debate, request.user),
        'yes_supporters': debate.yes_supporters,
        'no_supporters': debate.no_supporters,
        'user_is_active': participation.is_active and not participation.is_banned,
        'can_post': participation.is_active and not participation.is_banned,
        'can_rejoin': (not participation.is_active) and (not participation.is_banned),
        'is_view_only': participation.is_banned,
        'can_end_chat': participation.is_active and not participation.is_banned and debate.end_controller_id == request.user.id,
        'debate_status': debate.status,
        'current_controller_name': debate.end_controller.username if debate.end_controller else '',
        'current_controller_side': debate.end_controller_side,
        'can_moderate_chat': request.user.id == debate.target_id,
    })


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
    can_post = participation.is_active and not participation.is_banned
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
            'can_rejoin': (not participation.is_active) and (not participation.is_banned),
            'is_view_only': participation.is_banned,
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
