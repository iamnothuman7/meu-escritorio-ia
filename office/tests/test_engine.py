from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from office.models import Approval, Delivery, Event, FinancialEntry, Routine, RoutineOccurrence, Run, Settlement, SystemState, Task
from office.services import approve_delivery, bootstrap_workspace, delivery_hash, finance_totals, process_one, queue_task, schedule_due


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class EngineTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="owner", password="not-a-real-password")
        self.workspace = bootstrap_workspace(self.owner)
        self.other = bootstrap_workspace(self.owner, name="Outra empresa")
        self.agent = self.workspace.agents.get(slug="luma")

    def task(self, **kwargs):
        defaults = {"workspace": self.workspace, "agent": self.agent, "title": "Pedido", "objective": "Preparar uma entrega"}
        defaults.update(kwargs)
        return Task.objects.create(**defaults)

    def fake_provider(self, *args):
        return {"body": "TESTE SIMULADO: rascunho de laboratório.", "usage": {"test_fake": True}}

    def test_bootstrap_empty_real_workspace(self):
        self.assertEqual(self.workspace.agents.filter(enabled=True).count(), 6)
        self.assertEqual(self.workspace.agents.count(), 8)
        self.assertFalse(self.workspace.routine_set.filter(enabled=True).exists())
        self.assertEqual(self.workspace.task_set.count(), 0)
        self.assertEqual(finance_totals(self.workspace)["cash_cents"], 0)

    def test_cross_workspace_agent_rejected(self):
        with self.assertRaises(ValidationError):
            self.task(agent=self.other.agents.first())

    def test_cross_workspace_delivery_rejected(self):
        task = self.task()
        with self.assertRaises(ValidationError):
            Delivery.objects.create(workspace=self.other, task=task, title="Outro", body="Texto")

    def test_cross_workspace_event_rejected(self):
        with self.assertRaises(ValidationError):
            Event.objects.create(workspace=self.other, task=self.task(), kind="test", message="test")

    def test_queue_idempotent(self):
        task = self.task(kind="summary")
        queue_task(task)
        queue_task(task)
        self.assertEqual(Event.objects.filter(task=task, kind="task.queued").count(), 1)
        self.assertTrue(process_one())
        queue_task(task)
        self.assertFalse(process_one())
        self.assertEqual(task.runs.count(), 1)
        self.assertEqual(task.deliveries.count(), 1)

    def test_global_pause_blocks_queue_and_worker(self):
        task = self.task(kind="summary")
        queue_task(task)
        SystemState.objects.create(pk=1, paused=True)
        with self.assertRaises(ValidationError):
            queue_task(self.task())
        self.assertFalse(process_one())
        self.assertFalse(task.runs.exists())

    def test_workspace_pause_isolated(self):
        task = self.task(kind="summary")
        queue_task(task)
        self.workspace.paused = True
        self.workspace.save()
        self.assertFalse(process_one())
        second = self.task(workspace=self.other, agent=self.other.agents.first(), kind="summary")
        queue_task(second)
        self.assertTrue(process_one())
        second.refresh_from_db()
        self.assertEqual(second.status, "review")

    def test_disabled_agent_blocks_queue(self):
        self.agent.enabled = False
        self.agent.save()
        with self.assertRaises(ValidationError):
            queue_task(self.task())

    def test_offline_generation_is_blocked_no_fake_delivery(self):
        task = self.task()
        queue_task(task)
        process_one()
        task.refresh_from_db()
        self.assertEqual(task.status, "blocked")
        self.assertFalse(task.deliveries.exists())
        self.assertEqual(task.runs.get().reservation_usd, Decimal(0))

    def test_simulation_exists_only_by_test_injection(self):
        task = self.task()
        queue_task(task)
        process_one(provider=self.fake_provider)
        task.refresh_from_db()
        self.assertEqual(task.status, "review")
        self.assertIn("TESTE SIMULADO", task.deliveries.get().body)
        self.assertTrue(task.runs.get().usage["test_fake"])

    def test_approval_exact_hash_and_single_use(self):
        task = self.task(kind="summary")
        queue_task(task)
        process_one()
        delivery = task.deliveries.get()
        with self.assertRaises(ValidationError):
            approve_delivery(delivery, "approved", "incorrect")
        approval = approve_delivery(delivery, "approved", delivery_hash(delivery))
        self.assertEqual(approval.status, "approved")
        task.refresh_from_db()
        self.assertEqual(task.status, "completed")
        with self.assertRaises(ValidationError):
            approve_delivery(delivery, "approved", delivery_hash(delivery))

    def test_edit_invalidates_approval(self):
        task = self.task(kind="summary")
        queue_task(task)
        process_one()
        delivery = task.deliveries.get()
        original_hash = delivery_hash(delivery)
        delivery.body += " Alterado."
        delivery.save()
        self.assertEqual(delivery.version, 2)
        self.assertEqual(delivery.approvals.get().status, "expired")
        with self.assertRaises(ValidationError):
            approve_delivery(delivery, "approved", original_hash)

    def test_expired_approval_not_accepted(self):
        task = self.task(kind="summary")
        queue_task(task)
        process_one()
        delivery = task.deliveries.get()
        delivery.approvals.update(expires_at=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(ValidationError):
            approve_delivery(delivery, "approved", delivery_hash(delivery))

    def test_summary_only_contains_current_workspace(self):
        self.task(title="SEGREDO OUTRA EMPRESA", workspace=self.other, agent=self.other.agents.first())
        task = self.task(kind="summary")
        queue_task(task)
        process_one()
        self.assertNotIn("SEGREDO OUTRA EMPRESA", task.deliveries.get().body)

    def test_schedule_idempotent_and_consolidates_downtime(self):
        now = timezone.now()
        routine = Routine.objects.create(workspace=self.workspace, agent=self.agent, name="Rotina", objective="Resumo", kind="summary",
                                         interval_minutes=60, enabled=True, next_run=now - timedelta(days=2))
        self.assertEqual(schedule_due(now), 1)
        self.assertEqual(schedule_due(now), 0)
        self.assertEqual(RoutineOccurrence.objects.filter(routine=routine).count(), 1)
        routine.refresh_from_db()
        self.assertEqual(routine.next_run, now + timedelta(minutes=60))

    def test_schedule_respects_global_pause(self):
        Routine.objects.create(workspace=self.workspace, agent=self.agent, name="Rotina", objective="Resumo", kind="summary", enabled=True, next_run=timezone.now())
        SystemState.objects.create(pk=1, paused=True)
        self.assertEqual(schedule_due(), 0)
        self.assertFalse(RoutineOccurrence.objects.exists())

    def test_finance_partial_and_reversal(self):
        income = FinancialEntry.objects.create(workspace=self.workspace, description="Recebimento", kind="income", amount_cents=550000)
        expense = FinancialEntry.objects.create(workspace=self.workspace, description="Despesa", kind="expense", amount_cents=200000)
        Settlement.objects.create(entry=income, amount_cents=400000)
        Settlement.objects.create(entry=expense, amount_cents=130000)
        self.assertEqual(finance_totals(self.workspace), {"cash_cents":270000,"receivable_cents":150000,"payable_cents":70000,"projected_cents":350000})
        self.assertEqual(income.status, "Parcial")
        Settlement.objects.create(entry=income, amount_cents=-100000, reason="Correção do recebimento")
        self.assertEqual(income.settlements.count(), 2)
        self.assertEqual(finance_totals(self.workspace)["cash_cents"], 170000)
        self.assertEqual(finance_totals(self.other)["cash_cents"], 0)

    def test_finance_invalid_settlement_rejected(self):
        entry = FinancialEntry.objects.create(workspace=self.workspace, description="Teste", kind="income", amount_cents=100)
        with self.assertRaises(ValidationError):
            Settlement.objects.create(entry=entry, amount_cents=101)
        with self.assertRaises(ValidationError):
            Settlement.objects.create(entry=entry, amount_cents=-1, reason="Sem movimento original")
        with self.assertRaises(ValidationError):
            Settlement.objects.create(entry=entry, amount_cents=0)

    def test_movements_are_immutable(self):
        entry = FinancialEntry.objects.create(workspace=self.workspace, description="Teste", kind="income", amount_cents=100)
        settlement = Settlement.objects.create(entry=entry, amount_cents=50)
        settlement.amount_cents = 40
        with self.assertRaises(ValidationError):
            settlement.save()

    def test_unknown_budget_never_calls_provider(self):
        from unittest.mock import patch
        self.workspace.ai_mode = "openai"
        self.workspace.ai_model = "explicit-model"
        self.workspace.save()
        task = self.task()
        queue_task(task)
        with patch("office.providers.generate") as generate:
            process_one()
            generate.assert_not_called()
        task.refresh_from_db()
        self.assertEqual(task.status, "blocked")

    def test_instructions_version_snapshot(self):
        task = self.task()
        original = self.agent.instructions
        queue_task(task)
        process_one(provider=self.fake_provider)
        self.agent.instructions = "Novas instruções."
        self.agent.save()
        self.assertEqual(self.agent.version, 2)
        self.assertIn(original, task.runs.get().instructions_snapshot)
        self.assertEqual(task.runs.get().agent_version, 1)

    def api_policy(self, **changes):
        budget = {"confirmed": True, "provider": "openai", "model": "explicit-model",
                  "expires_at": (timezone.now() + timedelta(days=1)).isoformat(), "limit_usd": "1.00",
                  "input_per_million": "1.00", "output_per_million": "2.00", "max_output_tokens": 1024,
                  "max_input_chars": 24000, "max_calls_per_day": 5}
        budget.update(changes)
        self.workspace.ai_mode = "openai"
        self.workspace.ai_model = "explicit-model"
        self.workspace.policy = {"budget": budget}
        self.workspace.save()

    def test_budget_cap_blocks_before_provider(self):
        from unittest.mock import patch
        self.api_policy(limit_usd="0.000001")
        task = self.task()
        queue_task(task)
        with patch("office.providers.generate") as generate:
            process_one()
            generate.assert_not_called()
        self.assertIn("Limite local", task.runs.get().error)

    def test_policy_cannot_follow_different_model(self):
        from unittest.mock import patch
        self.api_policy()
        self.workspace.ai_model = "different-model"
        self.workspace.save()
        task = self.task()
        queue_task(task)
        with patch("office.providers.generate") as generate:
            process_one()
            generate.assert_not_called()

    def test_uncertain_provider_preserves_reservation_no_retry(self):
        from unittest.mock import patch
        from office.providers import ProviderError
        self.api_policy()
        task = self.task()
        queue_task(task)
        with patch("office.providers.generate", side_effect=ProviderError("Rede indisponível", uncertain=True)) as generate:
            process_one()
            process_one()
            self.assertEqual(generate.call_count, 1)
        run = task.runs.get()
        self.assertGreater(run.reservation_usd, 0)
        self.assertTrue(run.usage["pending_usage"])
        self.assertEqual(run.provider, "openai")
        self.assertFalse(task.deliveries.exists())

    def test_success_reconciles_usage_and_counts_free_price_calls(self):
        from unittest.mock import patch
        self.api_policy(input_per_million="0", output_per_million="0", max_calls_per_day=1)
        task = self.task()
        queue_task(task)
        with patch("office.providers.generate", return_value={"body":"Rascunho", "usage":{"input_tokens":100,"output_tokens":100}}) as generate:
            process_one()
            other = self.task()
            queue_task(other)
            process_one()
            self.assertEqual(generate.call_count, 1)
        self.assertEqual(task.runs.get().usage["cost_usd"], "0.000000")
        other.refresh_from_db()
        self.assertEqual(other.status, "blocked")

    def test_worker_os_lock_rejects_second_instance(self):
        import tempfile
        from django.core.management.base import CommandError
        from office.management.commands.runworker import instance_lock
        with tempfile.TemporaryDirectory() as directory, override_settings(DATA_DIR=directory):
            with instance_lock():
                with self.assertRaises(CommandError):
                    with instance_lock():
                        self.fail("Segundo executor não pode adquirir o lock.")

    def test_worker_recovery_does_not_repeat_interrupted_request(self):
        import io
        import tempfile
        from django.core.management import call_command
        task = self.task(status="running", mode="openai")
        Run.objects.create(task=task, provider="openai", reservation_usd=Decimal("0.01"),
                           usage={"provider_called": True, "pending_usage": True})
        with tempfile.TemporaryDirectory() as directory, override_settings(DATA_DIR=directory):
            call_command("runworker", once=True, stdout=io.StringIO())
        task.refresh_from_db()
        self.assertEqual(task.status, "blocked")
        self.assertEqual(task.runs.count(), 1)
        self.assertEqual(task.runs.get().reservation_usd, Decimal("0.01"))
        self.assertTrue(task.runs.get().usage["pending_usage"])
