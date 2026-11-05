"""HTMX must preserve the same session, tenant and approval rules as native HTTP."""
from datetime import timedelta
from html.parser import HTMLParser
import json
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from office.models import Approval, Delivery, Event, Task
from office.services import bootstrap_workspace, delivery_hash


class Elements(HTMLParser):
    void = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self, html):
        super().__init__()
        self.stack, self.elements = [], []
        self.feed(html)

    def handle_starttag(self, tag, attributes):
        node = {'tag': tag, 'attrs': dict(attributes), 'parents': list(self.stack)}
        self.elements.append(node)
        if tag not in self.void:
            self.stack.append(node)

    def handle_startendtag(self, tag, attributes):
        self.handle_starttag(tag, attributes)
        if tag not in self.void:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]['tag'] == tag:
                self.stack = self.stack[:index]
                break

    def by_id(self, identifier):
        return next(node for node in self.elements if node['attrs'].get('id') == identifier)

    @staticmethod
    def inherited(node, name):
        for current in [node] + list(reversed(node['parents'])):
            if name in current['attrs']:
                return current['attrs'][name]
        return None


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class HtmxIntegrationTests(TestCase):
    hx = {'HTTP_HX_REQUEST': 'true', 'HTTP_HX_BOOSTED': 'true'}

    def setUp(self):
        self.owner = get_user_model().objects.create_user('htmx-owner', password='Local-htmx-test7!')
        self.workspace = bootstrap_workspace(self.owner, name='Empresa HTMX')
        self.other = bootstrap_workspace(self.owner, name='Empresa isolada')
        self.agent = self.workspace.agents.get(slug='luma')
        self.client.force_login(self.owner)
        session = self.client.session
        session['workspace_id'] = str(self.workspace.pk)
        session.save()

    def task_data(self, **changes):
        data = {'agent': self.agent.pk, 'title': 'Resumo HTMX', 'objective': 'Conferir tarefas reais',
                'kind': 'summary', 'request_id': 'htmx-idempotency-key', 'enqueue': '1'}
        data.update(changes)
        return data

    def assert_hx_location(self, response, path=None):
        self.assertEqual(response.status_code, 200)
        self.assertIn('HX-Location', response)
        payload = json.loads(response['HX-Location'])
        location = payload['path']
        self.assertFalse(urlsplit(location).scheme)
        self.assertFalse(urlsplit(location).netloc)
        self.assertTrue(location.startswith('/'))
        self.assertFalse(location.startswith('//'))
        self.assertNotIn('\\', location)
        self.assertEqual(payload['target'], '#app-shell')
        self.assertEqual(payload['select'], '#app-shell')
        self.assertEqual(payload['swap'], 'outerHTML')
        self.assertIn('no-store', response['Cache-Control'])
        if path:
            self.assertEqual(location, path)
        return location

    def make_delivery(self, workspace=None):
        workspace = workspace or self.workspace
        task = Task.objects.create(workspace=workspace, agent=workspace.agents.first(), title='Entrega em revisão', objective='Revisar', status='review')
        delivery = Delivery.objects.create(workspace=workspace, task=task, title='Entrega em revisão', body='Texto exato autorizado')
        approval = Approval.objects.create(workspace=workspace, delivery=delivery, payload_hash=delivery_hash(delivery), expires_at=timezone.now() + timedelta(days=1))
        return task, delivery, approval

    def test_htmx_header_never_bypasses_authentication(self):
        visitor = Client()
        for method, path, data in [('get', '/app/', {}), ('post', '/tasks/create/', self.task_data())]:
            with self.subTest(path=path):
                response = getattr(visitor, method)(path, data, **self.hx)
                self.assertEqual(response.status_code, 200)
                self.assertIn('HX-Redirect', response)
                self.assertTrue(response['HX-Redirect'].startswith('/login/'))
                self.assertNotIn('HX-Location', response)
        self.assertFalse(Task.objects.exists())

    def test_htmx_post_still_requires_csrf(self):
        guarded = Client(enforce_csrf_checks=True)
        guarded.force_login(self.owner)
        self.assertEqual(guarded.post('/tasks/create/', self.task_data(), **self.hx).status_code, 403)
        guarded.get('/app/')
        token = guarded.cookies['csrftoken'].value
        response = guarded.post('/tasks/create/', {**self.task_data(), 'csrfmiddlewaretoken': token}, **self.hx)
        self.assert_hx_location(response)
        self.assertEqual(Task.objects.count(), 1)

    def test_htmx_create_preserves_idempotency_and_payload_conflict(self):
        first = self.client.post('/tasks/create/', self.task_data(), **self.hx)
        second = self.client.post('/tasks/create/', self.task_data(), **self.hx)
        self.assertEqual(self.assert_hx_location(first), self.assert_hx_location(second))
        task = Task.objects.get()
        self.assertEqual(task.status, 'queued')
        self.assertFalse(task.runs.exists())
        conflict = self.client.post('/tasks/create/', self.task_data(objective='Carga diferente'), **self.hx)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(Task.objects.count(), 1)

    def test_htmx_decision_preserves_scope_exact_hash_and_single_use(self):
        task, delivery, approval = self.make_delivery()
        _, _, foreign_approval = self.make_delivery(self.other)
        self.assertEqual(self.client.post(f'/approvals/{foreign_approval.pk}/decision/', {'decision': 'approved', 'hash': foreign_approval.payload_hash}, **self.hx).status_code, 404)
        invalid = self.client.post(f'/approvals/{approval.pk}/decision/', {'decision': 'approved', 'hash': 'wrong'}, **self.hx)
        self.assert_hx_location(invalid, '/approvals/')
        approval.refresh_from_db()
        self.assertEqual(approval.status, 'pending')
        valid = self.client.post(f'/approvals/{approval.pk}/decision/', {'decision': 'approved', 'hash': delivery_hash(delivery)}, **self.hx)
        self.assert_hx_location(valid, '/approvals/')
        self.client.post(f'/approvals/{approval.pk}/decision/', {'decision': 'approved', 'hash': delivery_hash(delivery)}, **self.hx)
        approval.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(approval.status, 'approved')
        self.assertEqual(task.status, 'completed')
        self.assertEqual(Event.objects.filter(task=task, kind='approval.decided').count(), 1)

    def test_post_location_cannot_become_an_external_redirect(self):
        for destination in ['https://evil.invalid', '//evil.invalid', '/\\evil.invalid']:
            response = self.client.post('/office/pause/', {'return_to': destination}, **self.hx)
            self.assert_hx_location(response, '/app/')

    def test_filters_return_current_workspace_shell_with_no_store(self):
        Task.objects.create(workspace=self.workspace, title='Encontrável A', objective='A')
        Task.objects.create(workspace=self.workspace, title='Oculta pelo filtro B', objective='B')
        Task.objects.create(workspace=self.other, title='Encontrável segredo outra empresa', objective='C')
        response = self.client.get('/tasks/', {'q': 'Encontrável'}, **self.hx)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Encontrável A')
        self.assertNotContains(response, 'Oculta pelo filtro B')
        self.assertNotContains(response, 'Encontrável segredo outra empresa')
        self.assertContains(response, 'id="app-shell"')
        self.assertNotContains(response, '<!doctype html>')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('hx-request', response.get('Vary', '').lower())

    def test_shell_configuration_disables_private_history_and_eval(self):
        parsed = Elements(self.client.get('/app/').content.decode())
        body = next(node for node in parsed.elements if node['tag'] == 'body')
        self.assertEqual(body['attrs'].get('hx-history'), 'false')
        shell = parsed.by_id('app-shell')
        self.assertEqual(Elements.inherited(shell, 'hx-target'), '#app-shell')
        self.assertEqual(Elements.inherited(shell, 'hx-select'), '#app-shell')
        self.assertEqual(Elements.inherited(shell, 'hx-swap'), 'outerHTML')
        dialog = parsed.by_id('new-task')
        self.assertTrue(any(parent['attrs'].get('id') == 'app-shell' for parent in dialog['parents']))
        meta = next(node for node in parsed.elements if node['tag'] == 'meta' and node['attrs'].get('name') == 'htmx-config')
        config = json.loads(meta['attrs']['content'])
        for key in ['allowEval', 'allowScriptTags', 'historyRestoreAsHxRequest']:
            self.assertIs(config[key], False)
        self.assertIs(config['selfRequestsOnly'], True)
        self.assertEqual(config['historyCacheSize'], 0)
        scripts = [node['attrs'].get('src', '') for node in parsed.elements if node['tag'] == 'script']
        self.assertTrue(any('htmx' in source and source.startswith('/static/') for source in scripts))

    def test_downloads_exports_backup_and_logout_use_native_navigation(self):
        for route in ['/settings/', '/tasks/']:
            response = self.client.get(route)
            parsed = Elements(response.content.decode())
            special = [node for node in parsed.elements if node['attrs'].get('href') == '/export/' or node['attrs'].get('action') in ('/backup/', '/logout/')]
            self.assertTrue(special)
            for node in special:
                self.assertEqual(Elements.inherited(node, 'hx-boost'), 'false')
        _, delivery, _ = self.make_delivery()
        response = self.client.get(f'/tasks/{delivery.task_id}/')
        parsed = Elements(response.content.decode())
        link = next(node for node in parsed.elements if node['attrs'].get('href') == f'/deliveries/{delivery.pk}/download/')
        self.assertEqual(Elements.inherited(link, 'hx-boost'), 'false')
        download = self.client.get(f'/deliveries/{delivery.pk}/download/', **self.hx)
        self.assertIn('attachment', download['Content-Disposition'])
        self.assertNotIn('HX-Location', download)

    def test_polling_only_requests_the_current_task_and_stops_at_terminal_state(self):
        task = Task.objects.create(workspace=self.workspace, agent=self.agent, title='Execução', objective='Resumo', kind='summary', status='queued')
        for state in ['queued', 'running', 'completed', 'blocked', 'failed', 'cancelled']:
            task.status = state
            task.save(update_fields=['status'])
            response = self.client.get(f'/tasks/{task.pk}/', **self.hx)
            parsed = Elements(response.content.decode())
            live = parsed.by_id('task-live')
            pollers = [node for node in parsed.elements if 'data-live-refresh' in node['attrs'] and live in node['parents']]
            if state in ('queued', 'running'):
                self.assertEqual(len(pollers), 1)
                poller = pollers[0]
                self.assertEqual(poller['attrs'].get('hx-get'), f'/tasks/{task.pk}/')
                self.assertRegex(poller['attrs'].get('hx-trigger', ''), r'every\s+\d+s')
                self.assertEqual(poller['attrs'].get('hx-select'), '#task-live')
                self.assertEqual(poller['attrs'].get('hx-target'), '#task-live')
                self.assertNotIn('hx-select', live['attrs'], 'O polling não pode mudar a seleção dos formulários descendentes.')
            else:
                self.assertNotIn('hx-get', live['attrs'])
                self.assertEqual(pollers, [])

    def test_history_restore_get_returns_complete_document_without_cached_snapshot(self):
        response = self.client.get('/tasks/', HTTP_HX_HISTORY_RESTORE_REQUEST='true')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<!doctype html>')
        self.assertContains(response, 'id="app-shell"')
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('hx-history-restore-request', response.get('Vary', '').lower())

    def test_private_get_routes_return_fragments_and_direct_access_keeps_full_document(self):
        for route in ['/app/', '/team/', '/tasks/', '/work/', '/approvals/', '/settings/']:
            with self.subTest(route=route):
                fragment = self.client.get(route, **self.hx)
                self.assertEqual(fragment.status_code, 200)
                self.assertNotContains(fragment, '<!doctype html>')
                self.assertContains(fragment, 'id="app-shell"', count=1)
                self.assertContains(fragment, '<title>')
                self.assertIn('no-store', fragment['Cache-Control'])
                full = self.client.get(route)
                self.assertContains(full, '<!doctype html>')
                self.assertContains(full, 'id="app-shell"', count=1)

    def test_native_logout_clears_session_and_private_requests_need_login_again(self):
        response = self.client.post('/logout/')
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('_auth_user_id', self.client.session)
        response = self.client.get('/app/', **self.hx)
        self.assertIn('HX-Redirect', response)
        self.assertIn('no-store', response['Cache-Control'])
