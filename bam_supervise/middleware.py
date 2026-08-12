class SecurityHeadersMiddleware:
    """Defense-in-depth browser security headers for the supervision UI."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault(
            'Content-Security-Policy',
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "font-src 'self' data:; "
            "connect-src 'self'; "
            "object-src 'none'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'",
        )
        response.setdefault('Referrer-Policy', 'same-origin')
        response.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
        response.setdefault('Cross-Origin-Opener-Policy', 'same-origin')
        response.setdefault('Cross-Origin-Resource-Policy', 'same-origin')

        user = getattr(request, 'user', None)
        content_type = response.get('Content-Type', '')
        if user is not None and getattr(user, 'is_authenticated', False) and 'text/html' in content_type:
            response['Cache-Control'] = 'no-store, private'
            response['Pragma'] = 'no-cache'

        return response
