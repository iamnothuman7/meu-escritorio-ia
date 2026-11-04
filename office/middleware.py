import hashlib
import ipaddress

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse


def trusted_proxy(request):
    try:
        return ipaddress.ip_address(request.META.get('REMOTE_ADDR', '')).is_loopback
    except ValueError:
        return False


class TrustedProxyMiddleware:
    """Only the loopback reverse proxy may assert the original scheme/client IP.

    Deployment must bind Waitress to loopback and have nginx overwrite these
    headers. Run this before Django SecurityMiddleware inspects the scheme.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not trusted_proxy(request):
            for header in ('HTTP_X_FORWARDED_PROTO', 'HTTP_X_REAL_IP', 'HTTP_X_FORWARDED_FOR', 'HTTP_X_FORWARDED_HOST'):
                request.META.pop(header, None)
        return self.get_response(request)


class LocalSecurityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == 'POST' and request.path.rstrip('/') in ('/login', '/setup') and self.rate_limited(request):
            response = HttpResponse('Muitas tentativas de acesso. Aguarde cinco minutos e tente novamente.', status=429, content_type='text/plain; charset=utf-8')
            response['Retry-After'] = str(settings.AUTH_POST_WINDOW_SECONDS)
        else:
            response = self.get_response(request)
        response['Content-Security-Policy'] = "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
        response['Referrer-Policy'] = 'same-origin'
        if not request.path.startswith('/static/'):
            response['Cache-Control'] = 'no-store'
        return response

    def rate_limited(self, request):
        client_ip = request.META.get('REMOTE_ADDR', '')
        if trusted_proxy(request):
            candidate = request.META.get('HTTP_X_REAL_IP', '')
            try:
                client_ip = str(ipaddress.ip_address(candidate))
            except ValueError:
                pass
        key = 'nabi-auth-post:' + hashlib.sha256(client_ip.encode('utf-8')).hexdigest()
        if cache.add(key, 1, timeout=settings.AUTH_POST_WINDOW_SECONDS):
            count = 1
        else:
            try:
                count = cache.incr(key)
            except ValueError:
                # The original window expired between add() and incr().
                if cache.add(key, 1, timeout=settings.AUTH_POST_WINDOW_SECONDS):
                    count = 1
                else:
                    count = cache.incr(key)
        return count > settings.AUTH_POST_LIMIT
