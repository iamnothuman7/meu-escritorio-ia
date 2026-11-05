import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from office import legal_prospecting as legal, services
from office.models import Approval, Delivery, Lead, Routine, RoutineOccurrence, Run, Task


class LegalProspectingTests(TestCase):
    def setUp(self):
        owner = get_user_model().objects.create_user("radar-legal-owner")
        self.workspace = services.bootstrap_workspace(owner)
        self.radar = self.workspace.agents.get(slug="radar")
        self.routine = Routine.objects.create(
            workspace=self.workspace, agent=self.radar, kind=legal.KIND, name=legal.ROUTINE_NAME,
            objective=legal.ROUTINE_OBJECTIVE, interval_minutes=legal.INTERVAL_MINUTES,
        )
        self.now = timezone.now()
        self.task = self.occurrence(self.now)

    def occurrence(self, when, kind=legal.KIND):
        task = Task.objects.create(
            workspace=self.workspace, agent=self.radar, kind=kind,
            title=legal.ROUTINE_NAME, objective=legal.ROUTINE_OBJECTIVE,
        )
        RoutineOccurrence.objects.create(routine=self.routine, task=task, scheduled_for=when)
        return task

    def candidate(self, company="Tavares Advocacia", city="Fortaleza", url="https://www.tavares-legal.example/contato"):
        return {"company": company, "city": city, "source_url": url, "opportunity": "contato",
                "evidence": "A página de contato orienta a procurar o endereço no rodapé."}

    def result(self, rows, urls=(), status="sourced"):
        return {"body": json.dumps({"candidates": rows}),
                "usage": {"research_status": status, "sources": [{"url": url, "title": "Página institucional"} for url in urls]}}

    def test_legacy_occurrence_does_not_offset_region_and_public_brief_is_scoped(self):
        self.occurrence(self.now - timedelta(days=1), kind="research")
        Lead.objects.create(workspace=self.workspace, company="Araripe Advogados", city="Crato",
                            source_url="https://araripe.example/contato",
                            notes="anotação comercial privada", contact="contato-privado@example.com")
        Lead.objects.create(workspace=self.workspace, company="Sem Fonte Advocacia", city="Fortaleza")
        foreign_owner = get_user_model().objects.create_user("outro-tenant")
        foreign = services.bootstrap_workspace(foreign_owner)
        Lead.objects.create(workspace=foreign, company="Outra Empresa Advocacia", city="Sobral",
                            source_url="https://outra.example/pagina")
        first = legal.request_context(self.task)
        parsed = json.loads(first)
        self.assertEqual(parsed["regiao"], "Fortaleza")
        self.assertEqual(parsed["nao_repetir"], [{"empresa": "Araripe Advogados", "cidade": "Crato", "dominio": "araripe.example"}])
        for forbidden in ("anotação comercial privada", "contato-privado", "Sem Fonte", "Outra Empresa"):
            self.assertNotIn(forbidden, first)
        second = self.occurrence(self.now + timedelta(hours=6))
        self.assertEqual(json.loads(legal.request_context(second))["regiao"], "Região Metropolitana")

    def test_only_cited_law_firms_in_scope_with_public_observations_are_retained(self):
        good = self.candidate()
        invented = self.candidate("Fictícia Advocacia", url="https://inventada.example/contato")
        outside = self.candidate("Cariri Advogados", city="Crato", url="https://cariri.example/contato")
        individual = self.candidate("Pessoa Física", url="https://individual.example/contato")
        social = self.candidate("Rede Advocacia", url="https://www.linkedin.com/company/rede")
        private = self.candidate("Privada Advocacia", url="https://privada.example/contato")
        private["evidence"] = "A página indica escrever para alguém@privada.example."
        subjective = self.candidate("Subjetiva Advocacia", url="https://subjetiva.example/")
        subjective["evidence"] = "O escritório certamente se beneficiaria de modernização."
        rows = [invented, outside, individual, social, private, subjective, good]
        body, usage = legal.prepare_result(self.task, self.result(rows, [row["source_url"] for row in rows[1:]]))
        self.assertEqual([row["company"] for row in usage["legal_candidates"]], [good["company"]])
        self.assertEqual(usage["legal_candidates"][0]["website"], "https://www.tavares-legal.example")
        self.assertEqual(len(usage["citations"]), 1)
        self.assertEqual([source["url"] for source in usage["sources"]], [good["source_url"]])
        citation = usage["citations"][0]
        self.assertEqual(body[citation["start_index"]:citation["end_index"]], good["source_url"])
        self.assertNotIn("Fictícia", body)
        self.assertFalse(Lead.objects.exists())

    def test_prose_missing_sources_and_wrong_page_are_not_candidate_evidence(self):
        good = self.candidate()
        for result in (
            {"body": f"Consulte {good['source_url']}", "usage": {"research_status": "sourced"}},
            self.result([good], [good["source_url"]], status="no_sources"),
            self.result([good], ["https://www.tavares-legal.example/"]),
        ):
            with self.subTest(result=result):
                body, usage = legal.prepare_result(self.task, result)
                self.assertIn("Nenhum novo candidato", body)
                self.assertEqual(usage["legal_candidates"], [])
                self.assertEqual(usage["citations"], [])

    def test_batch_is_bound_to_reviewed_text_and_import_is_idempotent(self):
        good = self.candidate()
        body, usage = legal.prepare_result(self.task, self.result([good], [good["source_url"]]))
        delivery = Delivery.objects.create(workspace=self.workspace, task=self.task, kind=legal.KIND,
                                           title=legal.ROUTINE_NAME, body=body)
        run = Run.objects.create(task=self.task, status="completed", provider="chatgpt", output=body, usage=usage)
        rows = legal.candidates_for(delivery)
        self.assertEqual(rows, usage["legal_candidates"])
        self.assertEqual(legal.import_candidates(self.workspace, rows), 1)
        self.assertEqual(legal.import_candidates(self.workspace, rows), 0)
        lead = Lead.objects.get(workspace=self.workspace)
        self.assertEqual((lead.company, lead.segment, lead.contact), (good["company"], "Advocacia", ""))
        lead.do_not_contact = True
        lead.save()
        alias = self.candidate("Escritório Tavares Advocacia", url="https://tavares-legal.example/servicos")
        _, duplicate_usage = legal.prepare_result(self.task, self.result([alias], [alias["source_url"]]))
        self.assertEqual(duplicate_usage["legal_candidates"], [])
        self.assertTrue(Lead.objects.get(pk=lead.pk).do_not_contact)

        run.usage = {**usage, "legal_candidates": [{**rows[0], "evidence": "O link foi substituído depois da revisão."}]}
        run.save(update_fields=["usage"])
        self.assertEqual(legal.candidates_for(delivery), [])
        run.usage = usage
        run.save(update_fields=["usage"])
        self.assertEqual(legal.candidates_for(delivery), rows)
        delivery.body += " Modificado depois da revisão."
        self.assertEqual(legal.candidates_for(delivery), [])

    def test_bound_four_candidates_deduplicate_normalized_name_and_host(self):
        rows = [self.candidate("Advocacia São José", url="https://sao-jose.example/contato"),
                self.candidate("Advocacia SAO JOSE", url="https://outro-host.example/contato"),
                self.candidate("Marca Alternativa Advocacia", url="https://www.sao-jose.example/servicos")]
        rows += [self.candidate(f"Advocacia Teste {index}", url=f"https://teste-{index}.example/contato")
                 for index in range(8)]
        _, usage = legal.prepare_result(self.task, self.result(rows, [row["source_url"] for row in rows]))
        self.assertEqual([row["company"] for row in usage["legal_candidates"]],
                         ["Advocacia São José", "Advocacia Teste 0", "Advocacia Teste 1", "Advocacia Teste 2"])

    def test_pending_batch_suppresses_duplicate_suggestions_without_blocking_import(self):
        good = self.candidate()
        result = self.result([good], [good["source_url"]])
        body, usage = legal.prepare_result(self.task, result)
        delivery = Delivery.objects.create(workspace=self.workspace, task=self.task, kind=legal.KIND,
                                           title=legal.ROUTINE_NAME, body=body)
        Run.objects.create(task=self.task, status="completed", provider="chatgpt", output=body, usage=usage)
        approval = Approval.objects.create(
            workspace=self.workspace, delivery=delivery, payload_hash=services.delivery_hash(delivery),
            expires_at=timezone.now() + timedelta(days=1),
        )
        self.assertIn(good["company"], legal.request_context(self.task))
        _, pending_usage = legal.prepare_result(self.task, result)
        self.assertEqual(pending_usage["legal_candidates"], [])

        approval.expires_at = timezone.now() - timedelta(seconds=1)
        approval.save(update_fields=["expires_at"])
        _, expired_usage = legal.prepare_result(self.task, result)
        self.assertEqual(expired_usage["legal_candidates"], usage["legal_candidates"])

        approval.expires_at = timezone.now() + timedelta(days=1)
        approval.save(update_fields=["expires_at"])
        self.assertEqual(legal.import_candidates(self.workspace, legal.candidates_for(delivery)), 1)
