from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from office import legal_prospecting
from office.management.commands.configure_legal_prospecting import LEGACY_NAME
from office.models import Agent, Routine, RoutineOccurrence, Task, Workspace


class ConfigureLegalProspectingTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user('legal-owner')
        self.other_owner = get_user_model().objects.create_user('another-owner')
        self.workspace = Workspace.objects.create(owner=self.owner, name='NaBio', ai_mode='chatgpt', ai_model='model',
                                                  policy={'chatgpt_confirmed': True, 'unrelated': 'preserve'})
        self.foreign = Workspace.objects.create(owner=self.other_owner, name='Outra empresa')
        self.radar = Agent.objects.create(workspace=self.workspace, slug='radar', name='Radar', role='Prospecção')
        self.foreign_radar = Agent.objects.create(workspace=self.foreign, slug='radar', name='Radar', role='Prospecção')

    def configure(self, **changes):
        options = {'workspace': self.workspace.pk, 'owner': self.owner.pk, 'stdout': StringIO()}
        call_command('configure_legal_prospecting', **{**options, **changes})

    def make_routine(self, agent, *, name=LEGACY_NAME, kind='research'):
        return Routine.objects.create(workspace=agent.workspace, agent=agent, name=name, kind=kind,
                                      objective='Pesquisa anterior', interval_minutes=1440, enabled=True,
                                      next_run=timezone.now() + timedelta(days=1))

    @patch('office.services.provider_ready', return_value=True)
    def test_first_activation_is_due_and_repeat_preserves_schedule(self, ready):
        self.configure()
        routine = Routine.objects.get(workspace=self.workspace, kind=legal_prospecting.KIND)
        self.assertTrue(routine.enabled)
        self.assertEqual(routine.agent_id, self.radar.pk)
        self.assertEqual(routine.name, legal_prospecting.ROUTINE_NAME)
        self.assertEqual(routine.objective, legal_prospecting.ROUTINE_OBJECTIVE)
        self.assertEqual(routine.interval_minutes, 360)
        self.assertLessEqual(routine.next_run, timezone.now())
        self.workspace.refresh_from_db()
        self.foreign.refresh_from_db()
        self.assertEqual(self.workspace.policy, {'chatgpt_confirmed': True, 'unrelated': 'preserve', 'web_research': True})
        self.assertEqual(self.foreign.policy, {})
        scheduled = timezone.now() + timedelta(hours=6)
        Routine.objects.filter(pk=routine.pk).update(next_run=scheduled)
        self.configure()
        routine.refresh_from_db()
        self.assertEqual(routine.next_run, scheduled)
        self.assertEqual(Routine.objects.count(), 1)
        self.assertFalse(Task.objects.exists())

    @patch('office.services.provider_ready', return_value=True)
    def test_upgrades_exact_legacy_routine_in_place_and_preserves_occurrences(self, ready):
        legacy = self.make_routine(self.radar)
        other = self.make_routine(self.radar, name='Outra pesquisa do Radar')
        foreign = self.make_routine(self.foreign_radar)
        previous_task = Task.objects.create(workspace=self.workspace, agent=self.radar, title='Pesquisa anterior',
                                            objective='Pesquisa anterior', kind='research', status='review')
        occurrence = RoutineOccurrence.objects.create(routine=legacy, task=previous_task,
                                                       scheduled_for=timezone.now() - timedelta(days=1))
        self.configure()
        legacy.refresh_from_db()
        other.refresh_from_db()
        foreign.refresh_from_db()
        occurrence.refresh_from_db()
        previous_task.refresh_from_db()
        self.assertEqual(legacy.kind, legal_prospecting.KIND)
        self.assertEqual(legacy.interval_minutes, legal_prospecting.INTERVAL_MINUTES)
        self.assertLessEqual(legacy.next_run, timezone.now())
        self.assertEqual(occurrence.routine_id, legacy.pk)
        self.assertEqual(previous_task.kind, 'research')
        self.assertEqual(other.kind, 'research')
        self.assertEqual(foreign.kind, 'research')
        self.assertEqual(Routine.objects.count(), 3)

    def test_disable_only_target_routine_even_if_provider_is_unavailable(self):
        target = self.make_routine(self.radar, kind=legal_prospecting.KIND)
        untouched = self.make_routine(self.radar, name='Outras fontes públicas')
        self.workspace.paused = True
        self.workspace.policy['web_research'] = True
        self.workspace.save(update_fields=['paused', 'policy'])
        with patch('office.services.provider_ready', return_value=False) as ready:
            self.configure(disable=True)
            self.configure(disable=True)
        ready.assert_not_called()
        target.refresh_from_db()
        untouched.refresh_from_db()
        self.workspace.refresh_from_db()
        self.assertFalse(target.enabled)
        self.assertIsNone(target.next_run)
        self.assertTrue(untouched.enabled)
        self.assertTrue(self.workspace.policy['web_research'])

    def test_invalid_scope_prerequisites_or_duplicates_leave_database_unchanged(self):
        with self.assertRaises(CommandError):
            self.configure(workspace=self.foreign.pk)
        with patch('office.services.provider_ready', return_value=False):
            with self.assertRaises(CommandError):
                self.configure()
        Agent.objects.filter(pk=self.radar.pk).update(enabled=False)
        with patch('office.services.provider_ready', return_value=True):
            with self.assertRaises(CommandError):
                self.configure()
        self.assertFalse(Routine.objects.exists())
        self.workspace.refresh_from_db()
        self.assertNotIn('web_research', self.workspace.policy)

        self.make_routine(self.radar)
        self.make_routine(self.radar, kind=legal_prospecting.KIND)
        with self.assertRaisesMessage(CommandError, 'mais de uma rotina'):
            self.configure()
        self.assertEqual(Routine.objects.filter(kind='research').count(), 1)
