from django.utils import timezone

from users.models import Profile


class UpdateLastSeenMiddleware:
    """Persist a lightweight presence heartbeat for authenticated users."""

    SESSION_KEY = 'dh_last_seen_epoch'
    WRITE_INTERVAL_SECONDS = 60

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated:
            return response

        now = timezone.now()
        should_write = True
        last_seen_epoch = request.session.get(self.SESSION_KEY)

        if isinstance(last_seen_epoch, (int, float)):
            should_write = (now.timestamp() - float(last_seen_epoch)) >= self.WRITE_INTERVAL_SECONDS

        if should_write:
            Profile.objects.filter(user=user).update(last_seen=now)
            request.session[self.SESSION_KEY] = now.timestamp()

        return response
