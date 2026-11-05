from datetime import timedelta
from io import BytesIO
import os
from pathlib import Path
import tempfile
from time import time
from unittest.mock import patch
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.utils import timezone
from PIL import Image

from office import image_art, instagram, services
from office.models import Approval, Delivery, DeliveryImage, InstagramPublication, Task


class InstagramPublicationTests(TestCase):
    token = "fake-instagram-token-for-tests"
    account_id = "17841400001"

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        settings = self.settings(DATA_DIR=Path(directory.name))
        settings.enable()
        self.addCleanup(settings.disable)
        environment = patch.dict(os.environ, {"NABI_PUBLIC_ORIGIN": "https://office.example.com"})
        environment.start()
        self.addCleanup(environment.stop)
        owner = get_user_model().objects.create_user("instagram-owner")
        self.workspace = services.bootstrap_workspace(owner)
        self.task = Task.objects.create(
            workspace=self.workspace, agent=self.workspace.agents.get(slug="luma"),
            kind="content", status="review", title="Post", objective="Criar publicação",
        )
        self.delivery = Delivery.objects.create(
            workspace=self.workspace, task=self.task, kind="content", title="Post",
            body="TÍTULO DA ARTE: Bom dia\n**LEGENDA:** Um dia para criar.\nVenha conversar.\nBRIEFING: Uso interno",
        )
        jpeg = BytesIO()
        Image.new("RGB", (16, 16), "white").save(jpeg, format="JPEG")
        self.image = jpeg.getvalue()
        DeliveryImage.objects.create(
            delivery=self.delivery, status="ready", data=self.image, sha256=image_art._jpeg_digest(self.image),
            requested_version=self.delivery.version, requested_hash=services.delivery_hash(self.delivery),
        )
        self.approval = Approval.objects.create(
            workspace=self.workspace, delivery=self.delivery,
            payload_hash=services.delivery_hash(self.delivery, verify_image=True),
            expires_at=timezone.now() + timedelta(days=1),
        )

    def connect(self):
        with patch.object(instagram, "_graph", side_effect=[
            {"user_id": self.account_id, "username": "oficina"},
            {"data": [{"quota_usage": 0}]},
        ]) as graph:
            result = instagram.connect(self.workspace, self.token, self.account_id)
        self.assertEqual(graph.call_args_list[0].args, ("GET", "me", self.token))
        self.assertEqual(graph.call_args_list[1].args, ("GET", f"{self.account_id}/content_publishing_limit", self.token))
        return result

    def queue(self):
        self.connect()
        return instagram.approve_and_queue(self.delivery, self.approval.payload_hash, self.account_id)

    def test_connect_verifies_account_and_stores_only_encrypted_credentials(self):
        self.assertEqual(self.connect(), {"available": True, "connected": True,
                                         "account_id": self.account_id, "username": "oficina"})
        _, path = instagram._storage(self.workspace)
        self.assertNotIn(self.token.encode(), path.read_bytes())
        other = services.bootstrap_workspace(self.workspace.owner, name="Outra empresa")
        self.assertFalse(instagram.connection_status(other)["connected"])
        _, other_path = instagram._storage(other)
        other_path.write_bytes(path.read_bytes())
        self.assertFalse(instagram.connection_status(other)["connected"])
        with patch.object(instagram, "_graph", return_value={"user_id": "777", "username": "outra"}):
            with self.assertRaises(instagram.InstagramError):
                instagram.connect(self.workspace, self.token, self.account_id)
        self.assertEqual(instagram.connection_status(self.workspace)["account_id"], self.account_id)

    def test_publication_requires_explicit_approval_for_the_displayed_account(self):
        self.connect()
        with self.assertRaises(instagram.InstagramError):
            instagram.approve_and_queue(self.delivery, self.approval.payload_hash, "777")
        with self.assertRaises(ValidationError):
            instagram.approve_and_queue(self.delivery, "stale", self.account_id)
        services.approve_delivery(self.delivery, "approve", self.approval.payload_hash)
        self.assertFalse(InstagramPublication.objects.exists())
        with self.assertRaises(ValidationError):
            instagram.approve_and_queue(self.delivery, self.approval.payload_hash, self.account_id)
        self.assertFalse(InstagramPublication.objects.exists())

    def test_publication_form_requires_the_workspace_owner_and_csrf_token(self):
        self.connect()
        client = Client(enforce_csrf_checks=True)
        stranger = get_user_model().objects.create_user("other-instagram-owner")
        client.force_login(stranger)
        self.assertNotContains(client.get("/approvals/"), "Aprovar e publicar")
        path = f"/approvals/{self.approval.pk}/decision/"
        payload = {"decision": "publish", "hash": self.approval.payload_hash,
                   "instagram_account_id": self.account_id}
        self.assertEqual(client.post(path, payload, HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value).status_code, 404)

        client.force_login(self.workspace.owner)
        page = client.get("/approvals/")
        self.assertContains(page, "Aprovar e publicar")
        self.assertContains(page, "Um dia para criar.\nVenha conversar.")
        self.assertEqual(client.post(path, payload).status_code, 403)
        self.assertFalse(InstagramPublication.objects.exists())
        response = client.post(path, payload, HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
        self.assertRedirects(response, "/approvals/", fetch_redirect_response=False)
        self.assertEqual(InstagramPublication.objects.get().approval_id, self.approval.pk)

    def test_worker_sends_the_approved_caption_and_signed_image_once(self):
        job = self.queue()
        self.assertEqual(job.caption, "Um dia para criar.\nVenha conversar.")
        with self.assertRaises(instagram.InstagramError):
            instagram.approve_and_queue(self.delivery, self.approval.payload_hash, self.account_id)

        with patch.object(instagram, "_graph", side_effect=[
            {"id": "900"}, {"status_code": "FINISHED"}, {"id": "901"},
        ]) as graph:
            self.assertTrue(instagram.process_one_publication())
            job.refresh_from_db()
            self.assertEqual((job.status, job.container_id), ("waiting", "900"))
            upload = graph.call_args.kwargs["data"]
            self.assertEqual(upload["caption"], job.caption)
            image_path = urlsplit(upload["image_url"]).path
            response = self.client.get(image_path)
            self.assertEqual((response.status_code, response.content), (200, self.image))
            self.assertEqual(response["Content-Type"], "image/jpeg")
            self.assertIn("no-store", response["Cache-Control"])
            head = self.client.head(image_path)
            self.assertEqual((head.status_code, head["Content-Length"], head.content), (200, str(len(self.image)), b""))
            self.assertEqual(self.client.get(image_path.replace(f"/{job.pk}/", f"/{job.pk + 1}/")).status_code, 404)
            with patch("django.core.signing.time.time", return_value=time() + 1801):
                self.assertEqual(self.client.get(image_path).status_code, 404)
            InstagramPublication.objects.filter(pk=job.pk).update(next_check_at=timezone.now() - timedelta(seconds=1))
            self.assertTrue(instagram.process_one_publication())
            self.assertFalse(instagram.process_one_publication())
        self.assertEqual(graph.call_count, 3)
        self.assertEqual(graph.call_args.args, ("POST", f"{self.account_id}/media_publish", self.token))
        self.assertEqual(graph.call_args.kwargs["data"], {"creation_id": "900"})
        job.refresh_from_db()
        self.assertEqual((job.status, job.media_id), ("published", "901"))
        self.assertEqual(self.client.get(image_path).status_code, 404)

    def test_changed_delivery_revokes_the_image_link_and_stops_publication(self):
        job = self.queue()
        with patch.object(instagram, "_graph", return_value={"id": "900"}) as graph:
            self.assertTrue(instagram.process_one_publication())
        image_path = urlsplit(graph.call_args.kwargs["data"]["image_url"]).path
        self.delivery.body = "LEGENDA: Conteúdo que precisa de outra aprovação."
        self.delivery.save(update_fields=["body"])
        self.assertEqual(self.client.get(image_path).status_code, 404)
        InstagramPublication.objects.filter(pk=job.pk).update(next_check_at=timezone.now() - timedelta(seconds=1))
        with patch.object(instagram, "_graph") as graph:
            self.assertTrue(instagram.process_one_publication())
        graph.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")

    def test_uncertain_dispatch_and_worker_recovery_never_retry_publication(self):
        job = self.queue()
        InstagramPublication.objects.filter(pk=job.pk).update(status="waiting", container_id="900")
        with patch.object(instagram, "_graph", side_effect=[
            {"status_code": "FINISHED"}, instagram.InstagramError("Sem confirmação"),
        ]) as graph:
            self.assertTrue(instagram.process_one_publication())
            self.assertFalse(instagram.process_one_publication())
        self.assertEqual(graph.call_count, 2)
        job.refresh_from_db()
        self.assertEqual(job.status, "uncertain")

        InstagramPublication.objects.filter(pk=job.pk).update(status="publishing")
        instagram.recover_interrupted_publications()
        job.refresh_from_db()
        self.assertEqual(job.status, "uncertain")

    def test_caption_extraction_omits_internal_text_and_handles_empty_deliveries(self):
        for body in (None, "", "BRIEFING: Só instruções", "LEGENDA: " + "x" * 2201):
            with self.subTest(body=body):
                self.delivery.body = body
                self.assertEqual(instagram.caption_for(self.delivery), "")
