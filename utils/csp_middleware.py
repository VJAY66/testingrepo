class ContentSecurityPolicyMiddleware:
    """Add a Content-Security-Policy header to every response.

    The policy is intentionally permissive for inline scripts/styles because
    the app uses extensive inline JS and CSS via Tailwind + Django templates.
    The critical wins are:
      - frame-ancestors 'self'  → clickjacking protection; allows same-origin iframes (chat panel)
      - object-src 'none'       → blocks Flash/plugin attacks
      - base-uri 'self'         → prevents <base> tag injection
      - block-all-mixed-content → forces HTTPS sub-resources
    """

    _POLICY = "; ".join([
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
            "cdn.tailwindcss.com cdnjs.cloudflare.com cdn.jsdelivr.net",
        "style-src 'self' 'unsafe-inline' "
            "fonts.googleapis.com cdnjs.cloudflare.com cdn.jsdelivr.net",
        "font-src 'self' fonts.gstatic.com data:",
        "img-src 'self' data: blob: *",
        "media-src 'self' blob:",
        "connect-src 'self' tenor.googleapis.com",
        "frame-src 'self'",
        "frame-ancestors 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "block-all-mixed-content",
    ])

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if "Content-Security-Policy" not in response:
            response["Content-Security-Policy"] = self._POLICY
        return response
