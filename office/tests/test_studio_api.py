import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from office import services
from office.models import Agent, Approval, Delivery, Event, SystemState, Task, Workspace


STATE_URL = '/api/v1/studio/state/'
TASK_URL = '/api/v1/studio/tasks/'


class StudioApiTests(TestCase):
    def setUp(self):
        users = get_user_model().objects
        self.owner = users.create_user('studio-owner')
        self.other_owner = users.create_user('studio-other')
        self.workspace = Workspace.objects.create(owner=self.owner, name='NaBio', ai_mode='chatgpt', ai_model='model',
                                                  policy={'claw3d_studio': True, 'chatgpt_confirmed': True})
        self.second = Workspace.objects.create(owner=self.owner, name='Outra empresa 3D', policy={'claw3d_studio': True})
        self.foreign = Workspace.objects.create(owner=self.other_owner, name='Empresa externa privada',
                                                policy={'claw3d_studio': True})
        self.luma = Agent.objects.create(workspace=self.workspace, slug='luma', name='Luma', role='Marketing',
                                         instructions='Instruções privadas da empresa.')
        self.radar = Agent.objects.create(workspace=self.workspace, slug='radar', name='Radar', role='Prospecção')
        self.disabled = Agent.objects.create(workspace=self.workspace, slug='disabled', name='Desativado',
                                             role='Operações', enabled=False)
        self.second_agent = Agent.objects.create(workspace=self.second, slug='luma', name='Outra Luma', role='Marketing')
        self.foreign_agent = Agent.objects.create(workspace=self.foreign, slug='luma', name='Luma externa', role='Marketing')
        self.client.force_login(self.owner)

    def select_workspace(self, workspace):
        session = self.client.session
        session['workspace_id'] = str(workspace.pk)
        session.save()

    def submit(self, **changes):
        payload = {'workspace_id': self.workspace.pk, 'agent_id': self.luma.pk, 'title': 'Nova campanha',
                   'description': 'Prepare rascunhos para revisão humana.'}
        return self.client.post(TASK_URL, {**payload, **changes}, content_type='application/json')

    def task(self, agent, title, status):
        return Task.objects.create(workspace=agent.workspace, agent=agent, title=title,
                                   objective='Objetivo confidencial', status=status)

    def approval(self, task, expires_at):
        delivery = Delivery.objects.create(workspace=task.workspace, task=task, title='Entrega privada', body='Texto privado')
        return Approval.objects.create(workspace=task.workspace, delivery=delivery, expires_at=expires_at, payload_hash='hash')

    def test_state_returns_only_scoped_roster_and_minimal_task_data(self):
        running = self.task(self.luma, 'Em execução', 'running')
        queued = self.task(self.luma, 'Espera', 'queued')
        self.task(self.luma, 'Finalizada', 'completed')
        foreign = self.task(self.foreign_agent, 'Título externo privado', 'review')
        self.task(self.second_agent, 'Título de outra empresa própria', 'queued')
        now = timezone.now()
        self.approval(queued, now + timedelta(days=1))
        self.approval(running, now - timedelta(days=1))
        self.approval(foreign, now + timedelta(days=1))
        SystemState.objects.create(pk=1, worker_heartbeat=now)

        with patch('office.chatgpt_plan.status', return_value={
            'connected': True, 'owner_id': self.owner.pk, 'email': 'segredo@example.test',
        }):
            response = self.client.get(STATE_URL)
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response['Cache-Control'])
        state = response.json()
        self.assertEqual(state['workspace'], {'id': self.workspace.pk, 'name': 'NaBio', 'paused': False})
        self.assertEqual(state['provider'], {'label': 'Plano ChatGPT', 'connected': True})
        self.assertTrue(state['worker_online'])
        self.assertEqual(state['pending_approvals'], 1)
        self.assertEqual(state['approvals_url'], '/approvals/')
        self.assertEqual(state['tasks_url'], '/tasks/')
        self.assertEqual([agent['id'] for agent in state['agents']], [self.luma.pk, self.radar.pk, self.disabled.pk])
        luma, radar, disabled = state['agents']
        self.assertEqual(luma['task_kinds'], ['general', 'content'])
        self.assertEqual(luma['status'], 'running')
        self.assertEqual(luma['current_task'], {'id': running.pk, 'title': running.title,
                                                'status': 'running', 'url': f'/tasks/{running.pk}/'})
        self.assertEqual(luma['task_counts']['running'], 1)
        self.assertEqual(luma['task_counts']['queued'], 1)
        self.assertEqual(luma['task_counts']['completed'], 1)
        self.assertEqual(luma['task_counts']['review'], 0)
        self.assertEqual(radar['status'], 'idle')
        self.assertIsNone(radar['current_task'])
        self.assertEqual(disabled['status'], 'disabled')
        rendered = json.dumps(state, ensure_ascii=False)
        for private in ('segredo@example.test', 'Objetivo confidencial', 'Instruções privadas', 'Título externo privado',
                        'Título de outra empresa própria', 'Empresa externa privada', 'Texto privado', 'chatgpt_confirmed'):
            self.assertNotIn(private, rendered)

    def test_connection_of_another_owner_and_stale_heartbeat_are_unavailable(self):
        SystemState.objects.create(pk=1, worker_heartbeat=timezone.now() - timedelta(minutes=2))
        with patch('office.chatgpt_plan.status', return_value={'connected': True, 'owner_id': self.other_owner.pk}):
            state = self.client.get(STATE_URL).json()
        self.assertFalse(state['provider']['connected'])
        self.assertFalse(state['worker_online'])

    def test_workspace_flag_is_required_and_resolved_through_owner_session(self):
        self.select_workspace(self.foreign)
        self.assertEqual(self.client.get(STATE_URL).json()['workspace']['id'], self.workspace.pk)
        self.select_workspace(self.second)
        self.assertEqual(self.client.get(STATE_URL).json()['workspace']['id'], self.second.pk)
        self.second.policy = {'claw3d_studio': 'true'}
        self.second.save(update_fields=['policy'])
        self.assertEqual(self.client.get(STATE_URL).status_code, 404)
        self.assertEqual(self.submit(workspace_id=self.second.pk, agent_id=self.second_agent.pk).status_code, 404)
        self.assertFalse(Task.objects.exists())

    def test_authentication_and_platform_account_cannot_access_bridge(self):
        self.client.logout()
        self.assertEqual(self.client.get(STATE_URL).status_code, 401)
        self.assertEqual(self.submit().status_code, 401)
        admin = get_user_model().objects.create_superuser('studio-admin', password='test-only-password')
        self.client.force_login(admin)
        self.assertEqual(self.client.get(STATE_URL).status_code, 403)
        self.assertEqual(self.submit().status_code, 403)
        self.assertFalse(Workspace.objects.filter(owner=admin).exists())
        self.assertFalse(Task.objects.exists())

    def test_tasks_use_existing_queue_provider_and_review_flow(self):
        result = self.submit(kind='content', request_id='marketing-1')
        self.assertEqual(result.status_code, 201)
        data = result.json()
        task = Task.objects.get(pk=data['task']['id'])
        self.assertEqual(task.workspace_id, self.workspace.pk)
        self.assertEqual(task.agent_id, self.luma.pk)
        self.assertEqual(task.title, 'Nova campanha')
        self.assertEqual(task.objective, 'Prepare rascunhos para revisão humana.')
        self.assertEqual((task.status, task.kind, task.mode, task.priority), ('queued', 'content', 'chatgpt', 'normal'))
        self.assertEqual(data['task']['url'], f'/tasks/{task.pk}/')
        self.assertEqual(data['approvals_url'], '/approvals/')
        self.assertTrue(Event.objects.filter(workspace=self.workspace, task=task, kind='task.queued').exists())
        self.assertFalse(Approval.objects.exists())

        text = self.submit(agent_id=self.luma.pk, title='Só texto')
        self.assertEqual(text.status_code, 201)
        self.assertEqual(Task.objects.get(pk=text.json()['task']['id']).kind, 'general')

        self.assertTrue(services.process_one(provider=lambda *args: {'body': 'LEGENDA:\nTexto para revisar.'}))
        task.refresh_from_db()
        self.assertEqual(task.status, 'review')
        self.assertTrue(Approval.objects.filter(workspace=self.workspace, delivery__task=task, status='pending').exists())

    def test_public_research_requires_workspace_opt_in_and_own_provider(self):
        profile = {'connected': True, 'owner_id': self.owner.pk}
        with patch('office.chatgpt_plan.status', return_value=profile):
            self.assertEqual(self.submit(agent_id=self.radar.pk, kind='research').status_code, 400)
            self.workspace.policy = {**self.workspace.policy, 'web_research': True}
            self.workspace.save(update_fields=['policy'])
            state = self.client.get(STATE_URL).json()
            self.assertEqual(state['agents'][0]['task_kinds'], ['general', 'content', 'research'])
            self.assertEqual(state['agents'][1]['task_kinds'], ['general', 'research'])
            research = self.submit(agent_id=self.radar.pk, title='Pesquise fontes públicas', kind='research')
            self.assertEqual(research.status_code, 201)
            self.assertEqual(Task.objects.get(pk=research.json()['task']['id']).kind, 'research')

        with patch('office.chatgpt_plan.status', return_value={**profile, 'owner_id': self.other_owner.pk}):
            self.assertEqual(self.submit(agent_id=self.radar.pk, kind='research').status_code, 400)
        self.assertEqual(Task.objects.count(), 1)

    def test_retry_survives_new_session_and_rejects_different_payload(self):
        initial = self.submit(request_id='stable-submission')
        self.assertEqual(initial.status_code, 201)
        self.client.logout()
        self.client.force_login(self.owner)
        duplicate = self.submit(request_id='stable-submission')
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(initial.json()['task']['id'], duplicate.json()['task']['id'])
        self.assertEqual(self.submit(request_id='stable-submission', title='Outro pedido').status_code, 409)
        self.assertEqual(Task.objects.count(), 1)
        self.assertEqual(Event.objects.filter(kind='task.queued').count(), 1)

    def test_submit_rejects_cross_workspace_agent_and_stale_tab(self):
        for agent in (self.second_agent, self.foreign_agent):
            self.assertEqual(self.submit(agent_id=agent.pk).status_code, 404)
        self.assertEqual(self.submit(workspace_id=self.foreign.pk, agent_id=self.foreign_agent.pk).status_code, 409)
        self.select_workspace(self.second)
        self.assertEqual(self.submit().status_code, 409)
        self.assertFalse(Task.objects.exists())

    def test_submit_rejects_unsupported_input_disabled_agent_and_pause_without_partial_write(self):
        for change in ({'agent_id': True}, {'agent_id': 2**63}, {'title': 'x' * 181}, {'description': ''},
                       {'title': '\ud800'}, {'description': 'x\x00'}, {'kind': 'research'}, {'mode': 'openai'},
                       {'workspace_id': str(self.workspace.pk)}, {'agent_id': self.radar.pk, 'kind': 'content'},
                       {'request_id': 'space here'}):
            with self.subTest(change=change):
                self.assertEqual(self.submit(**change).status_code, 400)
        self.assertEqual(self.submit(agent_id=self.disabled.pk, request_id='retryable').status_code, 409)
        SystemState.objects.create(pk=1, paused=True)
        self.assertEqual(self.submit(request_id='retryable').status_code, 409)
        self.assertFalse(Task.objects.exists())
        self.assertFalse(Event.objects.exists())
        self.assertTrue(self.client.get(STATE_URL).json()['workspace']['paused'])
        SystemState.objects.filter(pk=1).update(paused=False)
        self.assertEqual(self.submit(request_id='retryable').status_code, 201)

    def test_method_body_limit_content_type_and_csrf(self):
        self.assertEqual(self.client.get(TASK_URL).status_code, 405)
        self.assertEqual(self.client.post(STATE_URL).status_code, 405)
        self.assertEqual(self.client.post(TASK_URL, {'title': 'test'}).status_code, 415)
        self.assertEqual(self.client.post(TASK_URL, '{', content_type='application/json').status_code, 400)
        self.assertEqual(self.client.post(TASK_URL, 'x' * 65537, content_type='application/json').status_code, 413)

        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        payload = {'workspace_id': self.workspace.pk, 'agent_id': self.luma.pk,
                   'title': 'Protegida', 'description': 'Solicitação protegida'}
        self.assertEqual(client.post(TASK_URL, payload, content_type='application/json').status_code, 403)
        self.assertFalse(Task.objects.exists())
        client.get('/tasks/')
        csrf = client.cookies['csrftoken'].value
        self.assertEqual(client.post(TASK_URL, payload, content_type='application/json', HTTP_X_CSRFTOKEN=csrf,
                                     HTTP_ORIGIN='https://outro-site.example').status_code, 403)
        self.assertFalse(Task.objects.exists())
        self.assertEqual(client.post(TASK_URL, payload, content_type='application/json', HTTP_X_CSRFTOKEN=csrf).status_code, 201)
