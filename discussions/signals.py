from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings
from .models import Comment, Notification, PostFollow


def _email_post_author(post, actor_user):
    """Send a one-time email to the post author when they get their first comment."""
    author = post.user
    if not getattr(author, 'email', None) or author.id == actor_user.id:
        return
    try:
        prefs = author.profile.notification_prefs or {}
    except Exception:
        prefs = {}
    from users.models import DEFAULT_NOTIFICATION_PREFS
    type_prefs = prefs.get('author_comment') or DEFAULT_NOTIFICATION_PREFS.get('follow', {})
    if not type_prefs.get('email', True):
        return
    try:
        send_mail(
            f'@{actor_user.username} commented on your post',
            f'@{actor_user.username} commented on your post "{post.title}".\n\n'
            f'View it: {getattr(settings, "SITE_URL", "")}/discussion/{post.id}/',
            settings.DEFAULT_FROM_EMAIL,
            [author.email],
            fail_silently=True,
        )
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Author notification helpers
# ---------------------------------------------------------------------------

_AUTHOR_FIRST_MESSAGES = {
    'author_comment': lambda actor: f'{actor.username} commented on your post.',
    'author_debate':  lambda actor: f'{actor.username} started a debate on your post.',
    'author_repost':  lambda actor: f'{actor.username} reposted your post.',
    'author_save':    lambda actor: f'{actor.username} saved your post.',
}

_AUTHOR_BATCH_LABELS = {
    'author_comment': 'new comments on your post',
    'author_debate':  'new debates started on your post',
    'author_repost':  'people reposted your post',
    'author_save':    'people saved your post',
}


def _batch_window_seconds(first_notif_time):
    """Return the aggregation window (seconds) based on age of first notification."""
    age = (timezone.now() - first_notif_time).total_seconds()
    if age < 86400:       # Day 1  — hourly
        return 3600
    elif age < 172800:    # Day 2  — every 5 hours
        return 18000
    else:                 # Day 3+ — every 12 hours
        return 43200


def notify_post_author(post, notification_type, actor_user):
    """
    Notify the post author about activity on their post with progressive batching:
      - 1st event ever        → immediate individual notification
      - Day 1 after first     → aggregate, max 1 notification per hour
      - Day 2                 → aggregate, max 1 notification per 5 hours
      - Day 3+                → aggregate, max 1 notification per 12 hours
    """
    if post.user_id == actor_user.id:
        return  # Never notify about own actions

    existing_qs = Notification.objects.filter(
        user=post.user,
        post=post,
        notification_type=notification_type,
    ).order_by('created_at')

    first_notif = existing_qs.first()

    if first_notif is None:
        # Very first event — notify immediately
        Notification.objects.create(
            user=post.user,
            post=post,
            notification_type=notification_type,
            message=_AUTHOR_FIRST_MESSAGES[notification_type](actor_user),
            count=1,
            actors=[actor_user.username],
        )
        if notification_type == 'author_comment':
            _email_post_author(post, actor_user)
        return

    batch_window = _batch_window_seconds(first_notif.created_at)
    last_notif = existing_qs.last()
    time_since_last = (timezone.now() - last_notif.created_at).total_seconds()

    if time_since_last >= batch_window:
        # Batch window expired — start a fresh notification
        Notification.objects.create(
            user=post.user,
            post=post,
            notification_type=notification_type,
            message=_AUTHOR_FIRST_MESSAGES[notification_type](actor_user),
            count=1,
            actors=[actor_user.username],
        )
    else:
        # Still within the window — increment the existing batch counter
        actors_list = list(last_notif.actors or [])
        if actor_user.username not in actors_list:
            actors_list.append(actor_user.username)
        new_count = last_notif.count + 1
        label = _AUTHOR_BATCH_LABELS[notification_type]
        last_notif.count = new_count
        last_notif.actors = actors_list
        last_notif.message = f'{new_count} {label}.'
        last_notif.is_read = False  # Surface it again
        last_notif.save(update_fields=['count', 'message', 'actors', 'is_read'])


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

@receiver(post_save, sender=Comment)
def notify_post_followers(sender, instance, created, **kwargs):
    if not created:
        return

    post = instance.post

    # 1. Notify the post author about the new comment
    notify_post_author(post, 'author_comment', instance.user)

    # 2. Notify followers (existing every-5-comments logic)
    followers = PostFollow.objects.filter(post=post).exclude(user=instance.user).select_related('user')

    for follow in followers:
        comments_since_follow = Comment.objects.filter(
            post=post,
            created_at__gte=follow.created_at
        ).count()

        if comments_since_follow > 0 and comments_since_follow % 5 == 0:
            Notification.objects.create(
                user=follow.user,
                post=post,
                notification_type='post_activity',
                message=f'{comments_since_follow} new comments on "{post.title}" since you followed it.'
            )
