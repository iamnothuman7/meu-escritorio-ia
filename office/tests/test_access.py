from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.forms import SetPasswordForm
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from office.access import ACCESS_CODE_LIFETIME, INVALID_ACCESS, MAX_ATTEMPTS, access_code_digest
from office.models import OwnerAccessCode, Workspace


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class OwnerAccessTests(TestCase):
    email = "owner@example.invalid"
    code = "05128734"
    old_password = "Old-Canvas-7Lm!"
    new_password = "New-Telescope-4Qm!"

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.owner = get_user_model().objects.create_user(self.email, self.email, self.old_password, is_staff=True)
        self.workspace = Workspace.objects.create(owner=self.owner, name="Empresa anterior")
        self.record = OwnerAccessCode.objects.create(user=self.owner, digest=access_code_digest(self.owner.pk, self.code),
                                                     expires_at=timezone.now() + ACCESS_CODE_LIFETIME)

    def data(self, **changes):
        return {"email": self.email, "code": self.code, "new_password1": self.new_password,
                "new_password2": self.new_password, **changes}

    def test_case_insensitive_redemption_preserves_owner_and_revokes_old_login(self):
        old_session = Client()
        self.assertTrue(old_session.login(username=self.email, password=self.old_password))
        self.assertFalse(Client().login(username=self.email, password=self.code))
        response = self.client.post("/access/", self.data(email=self.email.upper()))
        self.assertRedirects(response, "/login/", fetch_redirect_response=False)
        self.assertFalse(OwnerAccessCode.objects.filter(pk=self.record.pk).exists())
        self.assertEqual(self.workspace.owner_id, self.owner.pk)
        self.assertEqual(get_user_model().objects.count(), 1)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password(self.new_password))
        self.assertFalse(self.owner.check_password(self.old_password))
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertRedirects(old_session.get("/app/"), "/login/?next=/app/", fetch_redirect_response=False)
        self.assertTrue(Client().login(username=self.email, password=self.new_password))
        replay = self.client.post("/access/", self.data(new_password1="Replay-Wood-4rY!", new_password2="Replay-Wood-4rY!"))
        self.assertEqual(replay.context["error"], INVALID_ACCESS)

    def test_six_incorrect_guesses_prevent_redemption_even_from_other_ips(self):
        for attempt in range(MAX_ATTEMPTS):
            response = self.client.post("/access/", self.data(code="00000000"), REMOTE_ADDR=f"192.0.2.{attempt + 1}")
            self.assertEqual(response.context["error"], INVALID_ACCESS)
        self.record.refresh_from_db()
        self.assertEqual(self.record.attempts, MAX_ATTEMPTS)
        final = self.client.post("/access/", self.data(), REMOTE_ADDR="192.0.2.100")
        self.assertEqual(final.context["error"], INVALID_ACCESS)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password(self.old_password))

    def test_expired_and_unknown_codes_share_the_same_error(self):
        OwnerAccessCode.objects.filter(pk=self.record.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        for email in (self.email, "unknown@example.invalid"):
            response = self.client.post("/access/", self.data(email=email))
            self.assertEqual(response.context["error"], INVALID_ACCESS)
            self.assertEqual(response.context["email"], email)
        self.record.refresh_from_db()
        self.assertEqual(self.record.attempts, 0)

    def test_permanent_password_policy_rejects_weak_choice_without_consuming_code(self):
        response = self.client.post("/access/", self.data(new_password1="13572468", new_password2="13572468"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["error"])
        self.assertNotEqual(response.context["error"], INVALID_ACCESS)
        self.assertNotContains(response, self.code)
        self.assertNotContains(response, "13572468")
        self.record.refresh_from_db()
        self.assertEqual(self.record.attempts, 0)
        self.assertRedirects(self.client.post("/access/", self.data()), "/login/", fetch_redirect_response=False)

    def test_rotation_between_validation_and_claim_cannot_overwrite_password(self):
        new_digest = access_code_digest(self.owner.pk, "92850163")
        save = SetPasswordForm.save

        def rotate_then_save(form, commit):
            OwnerAccessCode.objects.filter(pk=self.record.pk).update(digest=new_digest)
            return save(form, commit=commit)

        with patch.object(SetPasswordForm, "save", rotate_then_save):
            response = self.client.post("/access/", self.data())
        self.assertEqual(response.context["error"], INVALID_ACCESS)
        self.owner.refresh_from_db()
        self.record.refresh_from_db()
        self.assertTrue(self.owner.check_password(self.old_password))
        self.assertEqual(self.record.digest, new_digest)
        self.assertEqual(self.record.attempts, 0)

    def test_access_uses_csrf_and_shared_auth_rate_limit(self):
        guarded = Client(enforce_csrf_checks=True)
        self.assertEqual(guarded.post("/access/", self.data()).status_code, 403)
        response = guarded.get("/access/")
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertIn("no-store", response["Cache-Control"])
        cache.clear()
        with self.settings(AUTH_POST_LIMIT=2):
            self.client.post("/access/", self.data(code="00000000"))
            self.client.post("/access/", self.data(code="00000000"))
            response = self.client.post("/access/", self.data())
        self.assertEqual(response.status_code, 429)
        self.record.refresh_from_db()
        self.assertEqual(self.record.attempts, 2)
