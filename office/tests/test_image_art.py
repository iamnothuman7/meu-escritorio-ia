import base64
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.utils import timezone
from PIL import Image

from office import image_art, providers, services
from office.models import Approval, Delivery, DeliveryImage, Task


def jpeg(color="white"):
    output = BytesIO()
    Image.new("RGB", (16, 16), color).save(output, format="JPEG")
    return output.getvalue()


class DeliveryImageTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("image-owner")
        self.workspace = services.bootstrap_workspace(self.owner)
        self.luma = self.workspace.agents.get(slug="luma")

    def caption(self, workspace=None):
        workspace = workspace or self.workspace
        task = Task.objects.create(workspace=workspace, agent=workspace.agents.get(slug="luma"),
                                   kind="content", status="review", title="Ideias para a semana", objective="Criar publicação")
        delivery = Delivery.objects.create(workspace=workspace, task=task, kind="content", title=task.title,
                                           body="TITULO DA ARTE: Novas ideias\nSUBTITULO DA ARTE: Planeje a semana\nCTA DA ARTE: Conheça\nLegenda: Conte suas ideias.")
        Approval.objects.create(workspace=workspace, delivery=delivery, payload_hash=services.delivery_hash(delivery),
                                expires_at=timezone.now() + timedelta(days=1))
        return delivery

    def test_luma_brand_mode_renders_without_an_api_key_and_replaces_approval(self):
        self.workspace.policy = {"image_automation": "brand"}
        self.workspace.save(update_fields=["policy"])
        task = Task.objects.create(workspace=self.workspace, agent=self.luma, kind="content", status="queued",
                                   title="Novas ideias", objective="Criar publicação")
        seen = {}

        def caption_provider(mode, model, instructions, prompt, limit):
            seen["instructions"] = instructions
            return {"body": "Título: Novas ideias\nTITULO DA ARTE: Novas ideias\nSUBTITULO DA ARTE: Planeje a semana\nCTA DA ARTE: Conheça\nLegenda: Conte suas ideias."}

        with patch("office.image_art.credentials.load_api_key") as load, patch("office.image_art.providers._post") as post:
            self.assertTrue(services.process_one(provider=caption_provider))
            delivery = task.deliveries.get()
            old_approval = delivery.approvals.get()
            self.assertEqual(delivery.generated_image.status, "queued")
            with self.assertRaisesMessage(ValidationError, "Aguarde a imagem"):
                services.approve_delivery(delivery, "approve", old_approval.payload_hash)
            self.assertTrue(services.process_one())
        load.assert_not_called()
        post.assert_not_called()
        self.assertIn("TITULO DA ARTE:", seen["instructions"])
        delivery.refresh_from_db()
        old_approval.refresh_from_db()
        asset = delivery.generated_image
        self.assertEqual(asset.status, "ready", asset.error)
        self.assertEqual(asset.mode, "brand")
        self.assertIsNone(asset.attempted_at)
        with Image.open(BytesIO(asset.data)) as picture:
            self.assertEqual(picture.size, (1080, 1080))
            self.assertEqual(picture.format, "JPEG")
        self.assertEqual(delivery.version, 2)
        self.assertEqual(old_approval.status, "expired")
        with self.assertRaises(ValidationError):
            services.approve_delivery(delivery, "approve", old_approval.payload_hash)
        current = delivery.approvals.get(status="pending")
        self.assertEqual(services.approve_delivery(delivery, "approve", current.payload_hash).status, "approved")

    def test_changed_caption_does_not_generate_from_a_stale_request(self):
        delivery = self.caption()
        asset = image_art.request_generation(delivery, expected_version=1)
        delivery.body += " Conteúdo atualizado."
        delivery.save(update_fields=["body"])
        with patch("office.brand_art.render") as render:
            self.assertTrue(services.process_one())
        render.assert_not_called()
        asset.refresh_from_db()
        self.assertEqual(asset.status, "failed")
        self.assertIsNone(asset.data)

    def test_stored_image_bytes_are_verified_when_approving(self):
        delivery = self.caption()
        asset = image_art.attach_existing_image(delivery, jpeg(), expected_version=1)
        delivery.refresh_from_db()
        current = delivery.approvals.get(status="pending")
        DeliveryImage.objects.filter(pk=asset.pk).update(data=jpeg("black"))
        with self.assertRaisesMessage(ValidationError, "não corresponde"):
            services.approve_delivery(delivery, "approve", current.payload_hash)
        current.refresh_from_db()
        self.assertEqual(current.status, "pending")

    def test_paid_generation_uses_own_key_and_limits_attempts_across_workspaces(self):
        delivery = self.caption()
        second = self.caption(services.bootstrap_workspace(self.owner, name="Segunda empresa"))
        payload = {"data": [{"b64_json": base64.b64encode(jpeg()).decode("ascii")}]}
        with patch("office.image_art.credentials.load_api_key", return_value="test-personal-key") as load:
            image_art.request_generation(delivery, expected_version=1, mode="openai")
            with patch("office.image_art.providers._post", return_value=payload) as post:
                self.assertTrue(services.process_one())
            self.assertEqual(post.call_args.args[0], image_art.IMAGE_ENDPOINT)
            self.assertEqual(post.call_args.args[1]["n"], 1)
            self.assertEqual(post.call_args.args[2]["Authorization"], "Bearer test-personal-key")
            load.assert_called_with(self.owner.pk, "openai")
            with self.assertRaisesMessage(ValidationError, "uma tentativa"):
                image_art.request_generation(second, expected_version=1, mode="openai")

    def test_failed_paid_call_still_counts_after_local_render(self):
        delivery = self.caption()
        second = self.caption()
        with patch("office.image_art.credentials.load_api_key", return_value="test-personal-key"):
            image_art.request_generation(delivery, expected_version=1, mode="openai")
            with patch("office.image_art.providers._post", side_effect=providers.ProviderError("Falha simulada")):
                self.assertTrue(services.process_one())
            image_art.request_generation(delivery, expected_version=1, mode="brand")
            with patch("office.brand_art.render", return_value=jpeg()):
                self.assertTrue(services.process_one())
            with self.assertRaisesMessage(ValidationError, "uma tentativa"):
                image_art.request_generation(second, expected_version=1, mode="openai")

    def test_rotated_key_requires_new_paid_authorization(self):
        delivery = self.caption()
        with patch("office.image_art.credentials.load_api_key", side_effect=["test-key-before", "test-key-after"]):
            asset = image_art.request_generation(delivery, expected_version=1, mode="openai")
            with patch("office.image_art.providers._post") as post:
                self.assertTrue(services.process_one())
        post.assert_not_called()
        asset.refresh_from_db()
        self.assertEqual(asset.status, "failed")
        self.assertIsNone(asset.attempted_at)

    def test_preview_and_download_are_private_to_the_current_workspace(self):
        delivery = self.caption()
        data = jpeg()
        image_art.attach_existing_image(delivery, data, expected_version=1)
        path = f"/deliveries/{delivery.pk}/image/"
        self.assertEqual(self.client.get(path).status_code, 302)
        self.client.force_login(self.owner)
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, data)
        self.assertEqual(response["Content-Type"], "image/jpeg")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("attachment", self.client.get(path + "?download=1")["Content-Disposition"])

        outsider = get_user_model().objects.create_user("outsider")
        services.bootstrap_workspace(outsider)
        stranger = Client()
        stranger.force_login(outsider)
        self.assertEqual(stranger.get(path).status_code, 404)

    def test_paid_request_requires_explicit_confirmation(self):
        delivery = self.caption()
        self.client.force_login(self.owner)
        with patch("office.image_art.credentials.load_api_key") as load:
            response = self.client.post(f"/deliveries/{delivery.pk}/image/request/", {"version": "1", "image_mode": "openai"})
        load.assert_not_called()
        self.assertRedirects(response, "/approvals/", fetch_redirect_response=False)
        self.assertFalse(DeliveryImage.objects.filter(delivery=delivery).exists())
