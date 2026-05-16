from django.conf import settings

from discussions.models import Debate, DebateParticipant, Notification


def adsense(request):
    return {
        'GOOGLE_ADSENSE_CLIENT': getattr(settings, 'GOOGLE_ADSENSE_CLIENT', ''),
        'GOOGLE_ADSENSE_ENABLED': getattr(settings, 'GOOGLE_ADSENSE_ENABLED', False),
    }


def _safe_profile_avatar(user):
    try:
        return user.profile.get_picture_url or ''
    except Exception:
        return ''


def notification_counts(request):
    if not request.user.is_authenticated:
        return {
            "pending_notifications_count": 0,
            "active_chat_debates": [],
            "current_user_avatar": "",
        }

    pending_notifications_count = Debate.objects.filter(
        target=request.user,
        status='pending'
    ).count()

    moderator_usernames = {
        str(name).strip().lower()
        for name in (getattr(settings, 'MODERATOR_USERNAMES', []) or [])
        if str(name).strip()
    }
    if request.user.username.lower() in moderator_usernames:
        pending_notifications_count += Notification.objects.filter(
            user=request.user,
            notification_type='moderation_alert',
            is_read=False,
        ).count()

    if pending_notifications_count > 10:
        pending_notifications_count = 10

    active_chat_debates = []
    participations = DebateParticipant.objects.filter(
        user=request.user,
        debate__status='accepted',
        is_active=True,
        is_banned=False,
    ).select_related(
        'debate__post',
        'debate__initiator',
        'debate__target',
        'debate__end_controller',
    ).order_by('-debate__updated_at')[:12]

    for participation in participations:
        debate = participation.debate
        opponent = debate.target if request.user.id == debate.initiator_id else debate.initiator
        active_chat_debates.append({
            'id': str(debate.id),
            'post_id': str(debate.post_id),
            'title': debate.post.title,
            'opponent': opponent.username,
            'opponent_avatar': _safe_profile_avatar(opponent),
            'yes_supporters': debate.yes_supporters,
            'no_supporters': debate.no_supporters,
            'user_is_active': participation.is_active,
            'can_end_chat': participation.is_active and debate.end_controller_id == request.user.id,
            'current_controller_name': debate.end_controller.username if debate.end_controller else '',
            'current_controller_side': debate.end_controller_side,
            'debate_status': debate.status,
            'can_moderate_chat': request.user.id == debate.target_id,
        })

    return {
        "pending_notifications_count": pending_notifications_count,
        "active_chat_debates": active_chat_debates,
        "current_user_avatar": _safe_profile_avatar(request.user),
    }
