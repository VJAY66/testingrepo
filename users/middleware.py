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


class BanMiddleware:
    """Block banned users from accessing the site (except logout/login)."""
    ALLOWED_PATHS = {'/logout/', '/login/', '/privacy/', '/static/'}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user and user.is_authenticated:
            for p in self.ALLOWED_PATHS:
                if request.path.startswith(p):
                    break
            else:
                try:
                    from users.models import UserBan
                    from django.utils import timezone as tz
                    now = tz.now()
                    UserBan.objects.filter(user=user, is_active=True, expires_at__lt=now).update(is_active=False)
                    active_ban = UserBan.objects.filter(user=user, is_active=True).first()
                    if active_ban:
                        from django.contrib.auth import logout
                        from django.shortcuts import redirect
                        from django.contrib import messages as _msgs
                        logout(request)
                        if active_ban.expires_at:
                            msg = f'Your account is suspended until {active_ban.expires_at.strftime("%b %d, %Y")}. Reason: {active_ban.reason}'
                        else:
                            msg = f'Your account has been permanently banned. Reason: {active_ban.reason}'
                        _msgs.error(request, msg)
                        return redirect('/login/')
                except ImportError:
                    pass
                except Exception as e:
                    from django.db import OperationalError, ProgrammingError
                    if isinstance(e, (OperationalError, ProgrammingError)) and 'userban' in str(e).lower():
                        # Table not yet created — migration pending; skip ban check silently.
                        pass
                    else:
                        import logging
                        logging.getLogger(__name__).error('BanMiddleware error: %s', e)
        return self.get_response(request)
