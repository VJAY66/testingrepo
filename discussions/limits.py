from django.conf import settings
from django.utils import timezone

from discussions.models import Post


def get_daily_post_limit():
    return getattr(settings, 'DAILY_POST_LIMIT', 50)


def get_user_daily_post_count(user, day=None):
    if day is None:
        day = timezone.localdate()

    return Post.objects.filter(user=user, created_at__date=day).count()


def has_reached_daily_post_limit(user, day=None):
    limit = get_daily_post_limit()
    count = get_user_daily_post_count(user, day=day)
    return count >= limit, count, limit