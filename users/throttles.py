from rest_framework.throttling import SimpleRateThrottle


class LoginRateThrottle(SimpleRateThrottle):
    scope = 'login'

    def get_cache_key(self, request, view):
        ident = self.get_ident(request)
        username = (request.data.get('username') or '').strip().lower() or '-'
        return self.cache_format % {
            'scope': self.scope,
            'ident': f'{ident}:{username}',
        }
