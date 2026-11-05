import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from office import legal_prospecting as legal, services
from office.models import Approval, Lead, Routine, RoutineOccurrence, Run, Task


class LegalProspectingFlowTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('owner@legal.test')
        self.space = services.bootstrap_workspace(self.owner, name='NaBio', policy={'web_research': True, 'chatgpt_confirmed': True})
        self.space.ai_mode = 'chatgpt'
        self.space.ai_model = 'test-model'
        self.space.save(update_fields=['ai_mode', 'ai_model'])
        self.routine = Routine.objects.create(workspace=self.space, agent=self.space.agents.get(slug='radar'),
                                              name=legal.ROUTINE_NAME, kind=legal.KIND, objective=legal.ROUTINE_OBJECTIVE,
                                              interval_minutes=360, enabled=True, next_run=timezone.now())
        self.ready = patch('office.services.provider_ready', return_value=True)
        self.ready.start()
        self.addCleanup(self.ready.stop)

    def _provider(self, with_sources=True):
        site = 'https://firma-exemplo.com.br/servicos/'
        return {'body': json.dumps({'candidates': [
            {'company': 'Firma Exemplo Advocacia', 'city': 'Fortaleza', 'source_url': site,
             'opportunity': 'servicos', 'evidence': 'A página reúne todas as áreas em um único parágrafo sem links próprios.'},
        ]}), 'usage': {'research_status': 'sourced' if with_sources else 'not_used',
                      'sources': [{'url': site, 'title': 'Serviços'}] if with_sources else []}}

    def test_worker_saves_review_and_approval_imports_once(self):
        self.assertEqual(services.schedule_due(), 1)
        with patch('office.services.providers.generate', return_value=self._provider()) as generate:
            self.assertTrue(services.process_one())
            self.assertIs(generate.call_args.kwargs['web_research'], True)
        task = Task.objects.get(workspace=self.space, kind=legal.KIND)
        self.assertEqual(task.status, 'review')
        delivery = task.deliveries.get()
        approval = delivery.approvals.get()
        self.assertEqual(delivery.kind, legal.KIND)
        self.assertFalse(Lead.objects.filter(workspace=self.space).exists())
        self.client.force_login(self.owner)
        response = self.client.get('/approvals/')
        self.assertContains(response, 'Firma Exemplo Advocacia')
        self.assertContains(response, 'https://firma-exemplo.com.br/servicos/')
        self.assertContains(response, 'Aprovar e importar ao CRM')
        changed = self.client.post(f'/deliveries/{delivery.pk}/edit/', {'version': delivery.version, 'body': 'Alterada'})
        self.assertEqual(changed.status_code, 409)
        decision = f'/approvals/{approval.pk}/decision/'
        response = self.client.post(decision, {'decision': 'approved', 'hash': 'incorrect'})
        self.assertEqual(response['Location'], '/approvals/')
        self.assertFalse(Lead.objects.filter(workspace=self.space).exists())
        response = self.client.post(decision, {'decision': 'approved', 'hash': approval.payload_hash})
        self.assertEqual(response['Location'], '/work/?tab=leads#commercial-crm')
        self.client.post(decision, {'decision': 'approved', 'hash': approval.payload_hash})
        lead = Lead.objects.get(workspace=self.space)
        self.assertEqual(lead.company, 'Firma Exemplo Advocacia')
        self.assertEqual(lead.contact, '')

    def test_missing_provider_sources_finishes_without_review_or_leads(self):
        self.assertEqual(services.schedule_due(), 1)
        with patch('office.services.providers.generate', return_value=self._provider(with_sources=False)):
            self.assertTrue(services.process_one())
        task = Task.objects.get(workspace=self.space, kind=legal.KIND)
        self.assertEqual(task.status, 'completed')
        self.assertFalse(Approval.objects.filter(workspace=self.space).exists())
        self.assertFalse(Lead.objects.filter(workspace=self.space).exists())

    def test_daily_routine_limit_defers_radar_until_local_midnight(self):
        for index in range(services.PLAN_DAILY_CALLS):
            task = Task.objects.create(workspace=self.space, title='Rotina', objective='Preparar rascunho', status='completed')
            RoutineOccurrence.objects.create(routine=self.routine, task=task, scheduled_for=timezone.now() - timedelta(seconds=index))
            Run.objects.create(task=task, provider='chatgpt', status='completed', usage={'provider_called': True})
        now = timezone.now()
        self.assertEqual(services.schedule_due(now), 0)
        self.routine.refresh_from_db()
        local_next = timezone.localtime(self.routine.next_run)
        self.assertEqual(local_next.date(), timezone.localdate(now) + timedelta(days=1))
        self.assertEqual((local_next.hour, local_next.minute), (0, 0))
        self.assertFalse(Task.objects.filter(workspace=self.space, kind=legal.KIND).exists())
