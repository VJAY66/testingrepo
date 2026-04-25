from django.utils import timezone

from users.models import Profile


class UpdateLastSeenMiddleware:
    """Persist a lightweight presence heartbeat for authenticated users."""

    SESSION_KEY = 'dh_last_seen_epoch'
    WRITE_INTERVAL_SECONDS = 60

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user and user.is_authenticated:
            now = timezone.now()
            # Always write on POST — the user is actively doing something.
            # Throttle GETs to once per WRITE_INTERVAL_SECONDS to reduce DB load.
            is_post = request.method == 'POST'
            should_write = is_post
            if not is_post:
                last_seen_epoch = request.session.get(self.SESSION_KEY)
                if isinstance(last_seen_epoch, (int, float)):
                    should_write = (now.timestamp() - float(last_seen_epoch)) >= self.WRITE_INTERVAL_SECONDS
                else:
                    should_write = True

            if should_write:
                # Write before view execution so presence checks in the same
                # request immediately render the user as online.
                Profile.objects.filter(user=user).update(last_seen=now)
                request.session[self.SESSION_KEY] = now.timestamp()

        response = self.get_response(request)
        return response
