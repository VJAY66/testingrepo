from celery import shared_task
from django.utils import timezone
from datetime import timedelta
from django.core.mail import send_mail
from django.conf import settings


@shared_task
def send_weekly_digest():
    """Email weekly top-5 posts to subscribed users."""
    from django.contrib.auth.models import User
    from discussions.models import Post, PostAction
    from django.db.models import Count, Q

    since = timezone.now() - timedelta(days=7)
    top_posts = (
        Post.objects.filter(created_at__gte=since, is_draft=False, is_deleted_by_moderation=False)
        .annotate(like_count=Count('actions', filter=Q(actions__action='like')))
        .order_by('-like_count')[:5]
    )
    if not top_posts:
        return

    post_lines = '\n'.join(
        f'  • {p.title} by @{p.user.username} ({p.like_count} likes)\n    {settings.SITE_URL}/discussion/{p.id}/'
        for p in top_posts
    )

    cutoff = timezone.now() - timedelta(days=30)
    active_users = User.objects.filter(
        profile__last_seen__gte=cutoff,
        email__isnull=False,
    ).exclude(email='').select_related('profile')

    body_template = (
        'Hi {username},\n\n'
        'Here are the top discussions this week on PickASide:\n\n'
        '{posts}\n\n'
        'Join the conversation at {site_url}\n\n'
        '— The PickASide Team\n\n'
        'To unsubscribe from weekly digests, update your notification preferences: {site_url}/profile/'
    )

    for user in active_users:
        try:
            prefs = (user.profile.notification_prefs or {}).get('weekly_digest', {})
            if not prefs.get('email', True):
                continue
            send_mail(
                subject='PickASide Weekly Digest \U0001f525',
                message=body_template.format(
                    username=user.username,
                    posts=post_lines,
                    site_url=settings.SITE_URL,
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=True,
            )
        except Exception:
            pass


@shared_task
def cleanup_deleted_accounts():
    """Hard-delete user accounts that requested deletion more than 30 days ago."""
    from django.contrib.auth.models import User
    cutoff = timezone.now() - timedelta(days=30)
    to_delete = User.objects.filter(profile__deletion_requested_at__lte=cutoff)
    count = to_delete.count()
    to_delete.delete()
    return f'Deleted {count} accounts'


@shared_task
def publish_scheduled_posts():
    """Publish any draft posts whose scheduled_for time has passed."""
    from discussions.models import Post
    now = timezone.now()
    published = Post.objects.filter(is_draft=True, scheduled_for__lte=now).update(is_draft=False, scheduled_for=None)
    return f'Published {published} scheduled posts'


@shared_task
def fetch_link_preview_task(url, post_id):
    """
    Fetch OG / meta tags from the given URL and save a LinkPreview record
    associated with the given post_id.
    """
    import requests
    from bs4 import BeautifulSoup

    try:
        from frontend.models import LinkPreview
    except ImportError:
        return f'LinkPreview model not found — skipping preview for {url}'

    try:
        response = requests.get(url, timeout=10, headers={'User-Agent': 'PickASide/1.0'})
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return f'Failed to fetch {url}: {exc}'

    soup = BeautifulSoup(response.text, 'html.parser')

    def _og(prop):
        tag = soup.find('meta', property=f'og:{prop}') or soup.find('meta', attrs={'name': f'og:{prop}'})
        return (tag.get('content') or '').strip() if tag else ''

    title = _og('title') or (soup.title.string.strip() if soup.title else '')
    description = _og('description') or ''
    image = _og('image') or ''
    site_name = _og('site_name') or ''

    try:
        from discussions.models import Post
        post = Post.objects.get(pk=post_id)
    except Exception as exc:  # noqa: BLE001
        return f'Post {post_id} not found: {exc}'

    LinkPreview.objects.update_or_create(
        url=url,
        post=post,
        defaults={
            'title': title[:255],
            'description': description[:500],
            'image_url': image[:500],
            'site_name': site_name[:120],
        },
    )
    return f'LinkPreview saved for {url} on post {post_id}.'


@shared_task
def notify_expired_polls():
    """Notify poll followers of closed polls and resolve predictions."""
    from django.core.management import call_command
    call_command('notify_expired_polls')


@shared_task
def send_post_reminders():
    """Fire in-app notifications for due post reminders."""
    from django.core.management import call_command
    call_command('send_post_reminders')


@shared_task
def expire_pending_debates():
    """Auto-reject debate requests that have been pending for over 48 hours."""
    from django.core.management import call_command
    call_command('expire_pending_debates')


@shared_task
def compute_feed_scores():
    """Recompute personalised feed scores for active users."""
    from django.core.management import call_command
    call_command('compute_feed_scores')


@shared_task
def cleanup_expired_stories():
    """Hard-delete stories that have passed their expires_at timestamp."""
    from discussions.models import Story
    deleted, _ = Story.objects.filter(expires_at__lt=timezone.now()).delete()
    return f'Deleted {deleted} expired story/stories'


@shared_task
def normalize_post_content():
    """Normalize whitespace in Post.content (trim, collapse blank lines)."""
    from django.core.management import call_command
    call_command('normalize_post_content', apply=True)
