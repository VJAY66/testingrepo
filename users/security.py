from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from users.models import LoginAttempt


DEFAULT_LOGIN_ATTEMPT_LIMIT = 8
DEFAULT_LOGIN_ATTEMPT_WINDOW_SECONDS = 300


def login_attempt_limit():
    return int(getattr(settings, 'LOGIN_ATTEMPT_LIMIT', DEFAULT_LOGIN_ATTEMPT_LIMIT))


def login_attempt_window_seconds():
    return int(getattr(settings, 'LOGIN_ATTEMPT_WINDOW_SECONDS', DEFAULT_LOGIN_ATTEMPT_WINDOW_SECONDS))


def client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def is_login_rate_limited(request, username, source=LoginAttempt.SOURCE_WEB):
    ip = client_ip(request)
    normalized_username = (username or '').strip().lower() or '-'
    cutoff = timezone.now() - timedelta(seconds=login_attempt_window_seconds())

    # Lock by IP + username pair to avoid broad lockouts for shared IPs.
    failed_count = LoginAttempt.objects.filter(
        source=source,
        successful=False,
        created_at__gte=cutoff,
        username=normalized_username,
        ip_address=ip,
    ).count()
    return failed_count >= login_attempt_limit()


def record_login_attempt(request, username, successful, source=LoginAttempt.SOURCE_WEB):
    normalized_username = (username or '').strip().lower() or '-'
    user_agent = request.META.get('HTTP_USER_AGENT', '')[:255]
    LoginAttempt.objects.create(
        username=normalized_username,
        ip_address=client_ip(request),
        user_agent=user_agent,
        source=source,
        successful=successful,
    )
