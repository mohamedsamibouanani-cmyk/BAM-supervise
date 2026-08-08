class SecurityHeadersMiddleware:
    """Small defense-in-depth layer for browser-facing security headers.

    The application uses local static assets. Inline script/style remain allowed because
    Django templates currently contain a small amount of inline dashboard JS and dynamic
    widths. frame-ancestors/base-uri/form-action still significantly reduce abuse surface.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault(
            'Content-Security-Policy',
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "font-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'",
        )
        response.setdefault('Referrer-Policy', 'same-origin')
        response.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
        response.setdefault('Cross-Origin-Opener-Policy', 'same-origin')
        return response
