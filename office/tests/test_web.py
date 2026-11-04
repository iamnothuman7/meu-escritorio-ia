from datetime import timedelta
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from office import services
from office.models import (
    Approval, Delivery, FinancialEntry, Lead, Project, Routine, Settlement,
    SystemState, Task, Workspace,
)


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class OnboardingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.environment = patch.dict(os.environ, {'NABI_SETUP_TOKEN': ''})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_first_run_creates_local_user_workspace_and_authenticated_session(self):
        self.assertEqual(self.client.get('/setup/').status_code, 200)
        response = self.client.post('/setup/', {
            'username': 'emmanuel', 'name': 'Emmanuel', 'password': 'Nabi-Local-2026!Ax7',
        })
        self.assertRedirects(response, '/app/', fetch_redirect_response=False)
        user = get_user_model().objects.get(username='emmanuel')
        self.assertTrue(user.check_password('Nabi-Local-2026!Ax7'))
        self.assertTrue(user.is_staff)
        self.assertNotEqual(user.password, 'Nabi-Local-2026!Ax7')
        self.assertEqual(Workspace.objects.filter(owner=user).count(), 1)
        self.assertEqual(str(user.pk), self.client.session['_auth_user_id'])
        self.assertEqual(self.client.get('/app/').status_code, 200)
        self.assertRedirects(self.client.get('/setup/'), '/login/', fetch_redirect_response=False)

    def test_weak_password_does_not_create_account(self):
        response = self.client.post('/setup/', {'username': 'emmanuel', 'password': '123'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(get_user_model().objects.exists())

    def test_hosted_setup_without_configuration_token_fails_closed(self):
        with patch.dict(os.environ, {'NABI_SETUP_TOKEN': '', 'NABI_HTTPS': '1'}):
            self.assertEqual(self.client.get('/login/').status_code, 200)
            self.assertEqual(self.client.get('/setup/').status_code, 403)
            self.assertEqual(self.client.post('/setup/', {
                'username': 'intruder', 'password': 'Disallowed-bootstrap-7Nk!',
            }).status_code, 403)
            self.assertFalse(get_user_model().objects.exists())

    def test_hosted_setup_requires_token_then_removes_it_from_url(self):
        secret = 'test-only-owner-setup-token-7kS9'
        with patch.dict(os.environ, {'NABI_SETUP_TOKEN': secret}):
            self.assertEqual(self.client.get('/login/').status_code, 200)
            self.assertEqual(self.client.get('/setup/').status_code, 403)
            self.assertEqual(self.client.get('/setup/?token=wrong').status_code, 403)
            self.assertEqual(self.client.get('/setup/', {'token': 'não-é-o-token'}).status_code, 403)
            self.assertEqual(self.client.post('/setup/', {
                'username': 'intruder', 'password': 'Unauthorized-first-user9!',
            }).status_code, 403)
            self.assertFalse(get_user_model().objects.exists())
            response = self.client.get('/setup/', {'token': secret})
            self.assertRedirects(response, '/setup/', fetch_redirect_response=False)
            self.assertTrue(self.client.session['setup_authorized'])
            self.assertNotIn(secret, response.url)
            self.assertEqual(self.client.get('/setup/').status_code, 200)
            response = self.client.post('/setup/', {
                'username': 'authorized-owner', 'password': 'Velvet-Marble-7nK!2026',
            })
            self.assertRedirects(response, '/app/', fetch_redirect_response=False)
            owner = get_user_model().objects.get(username='authorized-owner')
            self.assertTrue(owner.is_staff)
            visitor = Client()
            self.assertRedirects(visitor.get('/setup/'), '/login/', fetch_redirect_response=False)
            self.assertRedirects(visitor.get('/setup/', {'token': secret}), '/login/', fetch_redirect_response=False)
            visitor.post('/setup/', {'username': 'second-owner', 'password': 'No-new-owner-password9!'})
            self.assertEqual(get_user_model().objects.count(), 1)


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class OfficeWebTests(TestCase):
    def setUp(self):
        cache.clear()
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.override = override_settings(MEDIA_ROOT=Path(self.media.name))
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.owner = get_user_model().objects.create_user('owner', password='Local-office-test7!')
        self.other_user = get_user_model().objects.create_user('other', password='Other-office-test8!')
        self.workspace = services.bootstrap_workspace(self.owner, name='Empresa A')
        self.second = services.bootstrap_workspace(self.owner, name='Empresa B')
        self.foreign = services.bootstrap_workspace(self.other_user, name='Empresa alheia secreta')
        self.agent = self.workspace.agents.filter(slug='luma').first()
        self.client.force_login(self.owner)
        self.select_workspace(self.workspace)

    def select_workspace(self, workspace):
        session = self.client.session
        session['workspace_id'] = str(workspace.pk)
        session.save()

    def make_delivery(self, workspace, title='Entrega restrita', kind='content'):
        task = Task.objects.create(workspace=workspace, agent=workspace.agents.first(),
                                   title=title, objective='Objetivo autorizado', status='review')
        delivery = Delivery.objects.create(workspace=workspace, task=task, title=title,
                                           body='Conteúdo confidencial da empresa.', kind=kind)
        approval = Approval.objects.create(workspace=workspace, delivery=delivery,
                                           payload_hash=services.delivery_hash(delivery),
                                           expires_at=timezone.now() + timedelta(days=1))
        return task, delivery, approval

    def test_anonymous_requests_cannot_read_or_modify_data(self):
        visitor = Client()
        self.assertEqual(visitor.get('/login/').status_code, 200)
        for path in ['/app/', '/team/', '/tasks/', '/approvals/', '/work/', '/settings/', '/export/', '/api/v1/dashboard']:
            with self.subTest(path=path):
                response = visitor.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response.url.startswith('/login/?next='))
        for path in ['/tasks/create/', '/work/action/', '/settings/save/', '/office/pause/', '/backup/']:
            with self.subTest(path=path):
                self.assertEqual(visitor.post(path, {}).status_code, 302)

    def test_login_uses_private_app_and_rejects_external_next_url(self):
        for next_url in ['', 'https://malicious.invalid/']:
            visitor = Client()
            response = visitor.post('/login/', {
                'username': 'owner', 'password': 'Local-office-test7!', 'next': next_url,
            })
            self.assertRedirects(response, '/app/', fetch_redirect_response=False)

    def test_session_tampering_falls_back_to_owned_workspace(self):
        self.select_workspace(self.foreign)
        response = self.client.get('/api/v1/dashboard')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['workspace'], str(self.workspace.pk))
        self.assertNotContains(response, 'Empresa alheia secreta')

    def test_task_delivery_approval_and_art_are_scoped_to_active_workspace(self):
        for workspace in [self.second, self.foreign]:
            task, delivery, approval = self.make_delivery(workspace)
            requests = [
                ('get', f'/tasks/{task.pk}/', {}),
                ('post', f'/tasks/{task.pk}/action/', {'action': 'run'}),
                ('get', f'/deliveries/{delivery.pk}/download/', {}),
                ('get', f'/deliveries/{delivery.pk}/art/', {}),
                ('post', f'/deliveries/{delivery.pk}/edit/', {'version': 1, 'body': 'alterado'}),
                ('post', f'/approvals/{approval.pk}/decision/', {'decision': 'approved', 'hash': approval.payload_hash}),
            ]
            for method, path, data in requests:
                with self.subTest(workspace=workspace.name, path=path):
                    self.assertEqual(getattr(self.client, method)(path, data).status_code, 404)
            task.refresh_from_db()
            delivery.refresh_from_db()
            approval.refresh_from_db()
            self.assertEqual(task.status, 'review')
            self.assertEqual(delivery.body, 'Conteúdo confidencial da empresa.')
            self.assertEqual(approval.status, 'pending')

    def test_foreign_workspace_and_agent_cannot_be_selected(self):
        self.assertEqual(self.client.post('/workspace/switch/', {'workspace': self.foreign.pk}).status_code, 404)
        response = self.client.post('/tasks/create/', {
            'agent': self.foreign.agents.first().pk, 'title': 'Tarefa indevida',
            'objective': 'Não deve ser criada', 'kind': 'summary',
        })
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Task.objects.filter(title='Tarefa indevida').exists())

    def test_export_contains_only_active_company_and_excludes_policy_owner_credentials(self):
        Task.objects.create(workspace=self.workspace, title='Incluída A', objective='A')
        Task.objects.create(workspace=self.second, title='Excluída B', objective='B')
        Task.objects.create(workspace=self.foreign, title='Excluída C', objective='C')
        self.workspace.policy = {'private_marker': 'policy-must-not-export'}
        self.workspace.save()
        response = self.client.get('/export/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Incluída A')
        self.assertNotContains(response, 'Excluída B')
        self.assertNotContains(response, 'Excluída C')
        self.assertNotContains(response, 'policy-must-not-export')
        self.assertNotIn('owner', response.json()['records']['Workspace'][0]['fields'])
        self.assertNotIn('policy', response.json()['records']['Workspace'][0]['fields'])
        self.assertIn('attachment', response['Content-Disposition'])

    def test_logo_requires_ownership_and_rejects_svg_upload(self):
        self.foreign.logo.save('private.png', SimpleUploadedFile('private.png', b'\x89PNG\r\n\x1a\nprivate'), save=True)
        self.assertEqual(self.client.get(f'/logo/{self.foreign.pk}/').status_code, 404)
        self.assertEqual(self.client.get(f'/logo/{self.workspace.pk}/').status_code, 404)
        response = self.client.post('/settings/save/', {
            'action': 'brand', 'name': self.workspace.name, 'brand_color': '#36b6a1',
            'logo': SimpleUploadedFile('attack.svg', b'<svg onload="alert(1)"></svg>', content_type='image/svg+xml'),
        })
        self.assertEqual(response.status_code, 302)
        self.workspace.refresh_from_db()
        self.assertFalse(self.workspace.logo)

    def test_create_task_is_idempotent_and_can_enqueue_without_calling_provider(self):
        data = {'agent': self.agent.pk, 'title': 'Resumo local', 'objective': 'Organizar prioridades',
                'kind': 'summary', 'request_id': 'request-123', 'enqueue': '1'}
        first = self.client.post('/tasks/create/', data)
        second = self.client.post('/tasks/create/', data)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(first.url, second.url)
        tasks = Task.objects.filter(workspace=self.workspace, title='Resumo local')
        self.assertEqual(tasks.count(), 1)
        self.assertEqual(tasks.get().status, 'queued')
        self.assertEqual(tasks.get().mode, 'offline')
        self.assertEqual(tasks.get().runs.count(), 0)
        response = self.client.post('/tasks/create/', {**data, 'objective': 'Carga diferente'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(tasks.count(), 1)

    def test_edit_requires_current_version_and_invalidates_previous_approval(self):
        task, delivery, old_approval = self.make_delivery(self.workspace)
        self.client.post(f'/deliveries/{delivery.pk}/edit/', {'version': '1', 'body': 'Nova legenda'})
        delivery.refresh_from_db()
        old_approval.refresh_from_db()
        self.assertEqual(delivery.version, 2)
        self.assertEqual(delivery.body, 'Nova legenda')
        self.assertEqual(old_approval.status, 'expired')
        self.assertEqual(delivery.approvals.filter(status='pending').count(), 1)
        response = self.client.post(f'/deliveries/{delivery.pk}/edit/', {'version': '1', 'body': 'Edição antiga'})
        self.assertEqual(response.status_code, 409)
        delivery.refresh_from_db()
        self.assertEqual(delivery.body, 'Nova legenda')

    def test_finance_partial_settlement_reversal_and_overpayment_validation(self):
        response = self.client.post('/work/action/', {
            'action': 'finance', 'tab': 'finance', 'description': 'Serviço',
            'kind': 'income', 'amount': '1500,00',
        })
        self.assertEqual(response.status_code, 302)
        entry = FinancialEntry.objects.get(workspace=self.workspace, description='Serviço')
        self.assertEqual(entry.amount_cents, 150000)
        self.client.post('/work/action/', {'action': 'settle', 'tab': 'finance', 'id': entry.pk, 'amount': '500', 'paid_on': '2026-11-04'})
        self.assertEqual(entry.settled_cents, 50000)
        self.assertEqual(entry.remaining_cents, 100000)
        self.client.post('/work/action/', {'action': 'settle', 'tab': 'finance', 'id': entry.pk, 'amount': '100', 'reverse': '1', 'reason': 'Correção comprovada'})
        self.assertEqual(entry.settled_cents, 40000)
        self.assertEqual(Settlement.objects.filter(entry=entry).count(), 2)
        self.client.post('/work/action/', {'action': 'settle', 'id': entry.pk, 'amount': '1200'})
        self.assertEqual(entry.settled_cents, 40000)
        self.client.post('/work/action/', {'action': 'settle', 'id': entry.pk, 'amount': '500', 'reverse': '1', 'reason': 'Excessivo'})
        self.assertEqual(Settlement.objects.filter(entry=entry).count(), 2)
        self.assertEqual(self.client.get('/work/?tab=finance').status_code, 200)

    def test_finance_rejects_other_company_and_invalid_precision(self):
        foreign = FinancialEntry.objects.create(workspace=self.foreign, description='Restrito', kind='income', amount_cents=1000)
        self.assertEqual(self.client.post('/work/action/', {'action': 'settle', 'id': foreign.pk, 'amount': '1'}).status_code, 404)
        for amount in ['NaN', 'Infinity', '-1', '0', '1.001']:
            self.client.post('/work/action/', {'action': 'finance', 'description': 'Inválido', 'kind': 'income', 'amount': amount})
        self.assertFalse(FinancialEntry.objects.filter(workspace=self.workspace).exists())

    def test_provider_selection_requires_explicit_valid_current_policy(self):
        valid = {'action': 'provider', 'ai_mode': 'openai', 'ai_model': 'model-chosen-by-user',
                 'confirmed': '1', 'expires_at': str(timezone.localdate() + timedelta(days=7)),
                 'limit_usd': '2.00', 'input_per_million': '0.25', 'output_per_million': '2.00'}
        invalid_variants = [
            {**valid, 'confirmed': ''}, {**valid, 'ai_model': ''},
            {**valid, 'expires_at': str(timezone.localdate())},
            {**valid, 'limit_usd': '-1'}, {**valid, 'input_per_million': 'NaN'},
            {**valid, 'ai_mode': 'chatgpt'},
        ]
        for data in invalid_variants:
            self.client.post('/settings/save/', data)
            self.workspace.refresh_from_db()
            self.assertEqual(self.workspace.ai_mode, 'offline')
            self.assertEqual(self.workspace.policy, {})
        self.client.post('/settings/save/', valid)
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.ai_mode, 'openai')
        self.assertTrue(self.workspace.policy['budget']['confirmed'])
        self.assertEqual(self.workspace.policy['budget']['limit_usd'], '2.00')
        self.assertEqual(self.workspace.ai_model, 'model-chosen-by-user')

    def test_routines_start_disabled_and_need_confirmation_to_enable(self):
        data = {'action': 'routine', 'tab': 'routines', 'agent': self.agent.pk,
                'name': 'Resumo de operação', 'objective': 'Conferir tarefas locais',
                'kind': 'summary', 'interval_minutes': 60}
        self.client.post('/work/action/', data)
        routine = Routine.objects.get(workspace=self.workspace, name='Resumo de operação')
        self.assertFalse(routine.enabled)
        self.client.post('/work/action/', {'action': 'routine_toggle', 'id': routine.pk})
        routine.refresh_from_db()
        self.assertFalse(routine.enabled)
        self.client.post('/work/action/', {'action': 'routine_toggle', 'id': routine.pk, 'confirm': '1'})
        routine.refresh_from_db()
        self.assertTrue(routine.enabled)
        self.assertIsNotNone(routine.next_run)

    def test_host_csrf_and_origin_protection_and_post_only_actions(self):
        guarded = Client(enforce_csrf_checks=True)
        guarded.force_login(self.owner)
        self.assertEqual(guarded.post('/office/pause/', {}).status_code, 403)
        token = 'a' * 32
        guarded.cookies['csrftoken'] = token
        self.assertEqual(guarded.post('/office/pause/', {'csrfmiddlewaretoken': token}, HTTP_ORIGIN='https://malicious.invalid').status_code, 403)
        self.assertEqual(self.client.get('/health', HTTP_HOST='malicious.invalid').status_code, 400)
        for path in ['/tasks/create/', '/work/action/', '/settings/save/', '/office/pause/', '/backup/']:
            self.assertEqual(self.client.get(path).status_code, 405)
        self.assertFalse(SystemState.objects.filter(paused=True).exists())

    def test_pause_return_to_cannot_redirect_to_untrusted_url(self):
        for destination in ['https://malicious.invalid', '//malicious.invalid', '/\\malicious.invalid', '/tasks/?next=evil']:
            response = self.client.post('/office/pause/', {'return_to': destination})
            self.assertRedirects(response, '/app/', fetch_redirect_response=False)
        response = self.client.post('/office/pause/', {'return_to': '/tasks/'})
        self.assertRedirects(response, '/tasks/', fetch_redirect_response=False)

    def test_dashboard_api_never_leaks_other_company_tasks(self):
        Task.objects.create(workspace=self.workspace, title='Visível', objective='A')
        Task.objects.create(workspace=self.second, title='Segredo outra empresa', objective='B')
        response = self.client.get('/api/v1/dashboard')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item['title'] for item in response.json()['tasks']], ['Visível'])

    def test_nonstaff_cannot_create_or_download_full_installation_backup(self):
        self.assertFalse(self.owner.is_staff)
        with patch('office.backup.create_backup') as backup:
            response = self.client.post('/backup/')
        self.assertEqual(response.status_code, 403)
        backup.assert_not_called()

    def test_malformed_entity_ids_are_not_found(self):
        requests = [
            ('get', '/tasks/not-an-integer/'),
            ('post', '/tasks/not-an-integer/action/'),
            ('get', '/deliveries/not-an-integer/download/'),
            ('get', '/deliveries/not-an-integer/art/'),
            ('post', '/deliveries/not-an-integer/edit/'),
            ('post', '/approvals/not-an-integer/decision/'),
            ('get', '/logo/not-an-integer/'),
        ]
        for method, path in requests:
            with self.subTest(path=path):
                self.assertEqual(getattr(self.client, method)(path).status_code, 404)

    def test_lead_sources_reject_executable_or_non_web_schemes(self):
        for source in ['javascript:alert(1)', 'data:text/html,<script>alert(1)</script>', 'ftp://example.com/file', '//example.com']:
            with self.subTest(source=source):
                response = self.client.post('/work/action/', {
                    'action': 'lead', 'tab': 'leads', 'company': 'Fonte inválida',
                    'source_url': source,
                })
                self.assertEqual(response.status_code, 302)
                self.assertFalse(Lead.objects.filter(workspace=self.workspace, company='Fonte inválida').exists())
        self.client.post('/work/action/', {
            'action': 'lead', 'tab': 'leads', 'company': 'Fonte informada',
            'source_url': 'https://example.com/empresa',
        })
        self.assertEqual(Lead.objects.get(workspace=self.workspace, company='Fonte informada').source_url, 'https://example.com/empresa')

    def test_public_landing_does_not_expose_authenticated_company_context(self):
        Task.objects.create(workspace=self.workspace, title='Tarefa confidencial A', objective='Restrito')
        visitor = Client()
        for client in [visitor, self.client]:
            with self.subTest(authenticated=client is self.client):
                response = client.get('/')
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'Nabi Office')
                self.assertContains(response, 'href="/app/"' if client is self.client else 'href="/login/"')
                self.assertNotContains(response, '/setup/')
                self.assertNotContains(response, 'Empresa A')
                self.assertNotContains(response, 'Empresa B')
                self.assertNotContains(response, 'Empresa alheia secreta')
                self.assertNotContains(response, 'Tarefa confidencial A')


class PublicSurfaceTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_landing_and_login_are_public_before_owner_setup(self):
        with patch.dict(os.environ, {'NABI_SETUP_TOKEN': 'private-test-token', 'NABI_HTTPS': '1'}):
            landing = self.client.get('/')
            self.assertEqual(landing.status_code, 200)
            self.assertContains(landing, 'href="/login/"')
            self.assertNotContains(landing, 'private-test-token')
            self.assertNotContains(landing, '/setup/')
            login = self.client.get('/login/')
            self.assertEqual(login.status_code, 200)
            self.assertContains(login, 'name="username"')
            self.assertContains(login, 'name="password"')
            self.assertNotContains(login, '/setup/')
            self.assertRedirects(self.client.get('/app/'), '/login/?next=/app/', fetch_redirect_response=False)
            response = self.client.post('/login/', {'username': 'not-created', 'password': 'Not-an-account-9!A'})
            self.assertEqual(response.status_code, 200)
            self.assertFalse(get_user_model().objects.exists())

    def test_installation_assets_are_public_and_icons_exist(self):
        manifest = self.client.get('/manifest.webmanifest')
        self.assertEqual(manifest.status_code, 200)
        data = manifest.json()
        self.assertTrue(data['icons'])
        for icon in data['icons']:
            self.assertTrue(icon['src'].startswith('/static/'))
            self.assertIsNotNone(finders.find(icon['src'].removeprefix('/static/')))
        service_worker = self.client.get('/sw.js')
        self.assertEqual(service_worker.status_code, 200)
