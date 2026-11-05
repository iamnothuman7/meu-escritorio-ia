from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from office.models import Workspace


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class OfficeEntryTests(TestCase):
    def setUp(self):
        cache.clear()
        user_model = get_user_model()
        self.owner = user_model.objects.create_user('operadora', password='Entrada-teste-7!')
        self.nabio = Workspace.objects.create(owner=self.owner, name='NaBio', policy={'claw3d_studio': True})
        self.other = Workspace.objects.create(owner=self.owner, name='Outra empresa')
        outsider = user_model.objects.create_user('externo')
        self.foreign = Workspace.objects.create(owner=outsider, name='Empresa privada alheia', policy={'claw3d_studio': True})

    def test_root_login_opens_selector_and_respects_a_protected_destination(self):
        self.assertContains(self.client.get('/'), 'name="password"')
        response = self.client.post('/', {'username': self.owner.username, 'password': 'Entrada-teste-7!'})
        self.assertRedirects(response, '/escritorios/', fetch_redirect_response=False)
        response = self.client.get('/login/', {'next': '/approvals/'})
        self.assertRedirects(response, '/approvals/', fetch_redirect_response=False)
        self.assertRedirects(self.client.get('/login/', {'next': '/'}), '/escritorios/', fetch_redirect_response=False)

    def test_selector_requires_login_and_lists_only_owned_workspaces(self):
        self.assertRedirects(self.client.get('/escritorios/'), '/login/?next=/escritorios/', fetch_redirect_response=False)
        self.client.force_login(self.owner)
        response = self.client.get('/escritorios/')
        self.assertContains(response, 'NaBio')
        self.assertContains(response, 'Outra empresa')
        self.assertContains(response, 'value="studio"', count=1)
        self.assertNotContains(response, self.foreign.name)
        self.assertIn('no-store', response['Cache-Control'])

    def test_switch_checks_owner_and_eligibility_before_changing_session(self):
        self.client.force_login(self.owner)
        for pk in ('inválido', self.foreign.pk, self.other.pk):
            response = self.client.post('/workspace/switch/', {'workspace': pk, 'destination': 'studio'})
            self.assertEqual(response.status_code, 404)
            self.assertNotIn('workspace_id', self.client.session)
        response = self.client.post('/workspace/switch/', {'workspace': self.nabio.pk, 'destination': 'studio'})
        self.assertRedirects(response, '/office-3d/', fetch_redirect_response=False)
        self.assertEqual(self.client.session['workspace_id'], str(self.nabio.pk))
        response = self.client.post('/workspace/switch/', {'workspace': self.other.pk})
        self.assertRedirects(response, '/app/', fetch_redirect_response=False)
        self.assertEqual(self.client.session['workspace_id'], str(self.other.pk))

    @override_settings(AUTH_POST_LIMIT=1)
    def test_root_and_login_share_access_throttle(self):
        credentials = {'username': self.owner.username, 'password': 'incorreta'}
        self.assertEqual(self.client.post('/', credentials).status_code, 200)
        self.assertEqual(self.client.post('/login/', credentials).status_code, 429)
