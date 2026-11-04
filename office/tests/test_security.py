from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from office.middleware import LocalSecurityMiddleware, TrustedProxyMiddleware


@override_settings(AUTH_POST_LIMIT=10, AUTH_POST_WINDOW_SECONDS=300)
class SecurityMiddlewareTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.factory = RequestFactory()
        self.middleware = TrustedProxyMiddleware(LocalSecurityMiddleware(lambda request: HttpResponse('ok')))

    def tearDown(self):
        cache.clear()

    def test_authentication_posts_throttle_without_changing_other_routes(self):
        for _ in range(10):
            self.assertEqual(self.middleware(self.factory.post('/login/', REMOTE_ADDR='198.51.100.7')).status_code, 200)
        response = self.middleware(self.factory.post('/setup/', REMOTE_ADDR='198.51.100.7'))
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response['Retry-After'], '300')
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertIn("frame-ancestors 'none'", response['Content-Security-Policy'])
        self.assertEqual(self.middleware(self.factory.get('/login/', REMOTE_ADDR='198.51.100.7')).status_code, 200)
        self.assertEqual(self.middleware(self.factory.post('/tasks/create/', REMOTE_ADDR='198.51.100.7')).status_code, 200)
        self.assertEqual(self.middleware(self.factory.post('/login/', REMOTE_ADDR='198.51.100.8')).status_code, 200)

    def test_only_loopback_proxy_can_supply_client_ip(self):
        for index in range(11):
            response = self.middleware(self.factory.post('/login/', REMOTE_ADDR='198.51.100.7', HTTP_X_REAL_IP=f'203.0.113.{index}'))
        self.assertEqual(response.status_code, 429)
        self.assertEqual(self.middleware(self.factory.post('/login/', REMOTE_ADDR='127.0.0.1', HTTP_X_REAL_IP='203.0.113.7')).status_code, 200)

    @override_settings(SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'))
    def test_untrusted_forwarded_scheme_is_removed(self):
        probe = TrustedProxyMiddleware(lambda request: HttpResponse(str(request.is_secure())))
        response = probe(self.factory.get('/', REMOTE_ADDR='198.51.100.7', HTTP_X_FORWARDED_PROTO='https'))
        self.assertEqual(response.content, b'False')
        response = probe(self.factory.get('/', REMOTE_ADDR='127.0.0.1', HTTP_X_FORWARDED_PROTO='https'))
        self.assertEqual(response.content, b'True')
