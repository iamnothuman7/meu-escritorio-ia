from unittest.mock import patch

from django.contrib.auth import authenticate, get_user_model
from django.test import Client, TestCase, override_settings

from office.models import Routine, Workspace


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class PlatformAdminTests(TestCase):
    route = "/administracao/"
    email = "marina@example.invalid"
    password = "Heliotrope-Saturn-79!"

    def setUp(self):
        self.user_model = get_user_model()
        self.admin = self.user_model.objects.create_superuser(
            "platform-root", "admin@example.invalid", "Paper-Kite-95!Fern"
        )
        self.user = self.user_model.objects.create_user("existing-owner", "existing@example.invalid")
        Workspace.objects.create(owner=self.user, name="Private Workplace Hidden From Platform")

    def payload(self, **changes):
        return {
            "email": self.email,
            "first_name": "Marina",
            "password": self.password,
            "password_confirmation": self.password,
            **changes,
        }

    def test_console_requires_both_staff_and_superuser_permissions(self):
        self.assertRedirects(self.client.get(self.route), "/login/?next=/administracao/", fetch_redirect_response=False)
        for staff, superuser in [(False, False), (True, False), (False, True)]:
            with self.subTest(staff=staff, superuser=superuser):
                self.user.is_staff = staff
                self.user.is_superuser = superuser
                self.user.save(update_fields=["is_staff", "is_superuser"])
                self.client.force_login(self.user)
                self.assertEqual(self.client.get(self.route).status_code, 403)
                self.assertEqual(self.client.post(self.route, self.payload()).status_code, 403)
        self.assertFalse(self.user_model.objects.filter(email=self.email).exists())

    def test_creation_uses_permanent_password_and_unprivileged_account(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.route, self.payload(email=self.email.upper(), is_staff="1", is_superuser="1"))
        self.assertRedirects(response, self.route, fetch_redirect_response=False)
        account = self.user_model.objects.get(email=self.email)
        self.assertEqual(account.username, self.email)
        self.assertEqual(account.first_name, "Marina")
        self.assertFalse(account.is_staff)
        self.assertFalse(account.is_superuser)
        self.assertTrue(account.check_password(self.password))
        self.assertNotEqual(account.password, self.password)
        self.assertEqual(authenticate(username=self.email, password=self.password), account)
        workspace = Workspace.objects.get(owner=account)
        self.assertEqual(workspace.ai_mode, "offline")
        self.assertFalse(workspace.description)
        self.assertFalse(Routine.objects.filter(workspace=workspace, enabled=True).exists())
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.admin.pk)

        listing = self.client.get(self.route)
        self.assertContains(listing, "Conta operacional criada.")
        self.assertContains(listing, self.email)
        self.assertNotContains(listing, self.password)
        self.assertNotContains(listing, account.password)

    def test_weak_mismatched_and_duplicate_accounts_are_rejected(self):
        self.client.force_login(self.admin)
        for payload in [
            self.payload(password="123456789", password_confirmation="123456789"),
            self.payload(password_confirmation="Different-Plants-68!"),
            self.payload(email=self.user.email.upper()),
        ]:
            with self.subTest(email=payload["email"]):
                response = self.client.post(self.route, payload)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors)
                self.assertNotContains(response, payload["password"])
        self.assertEqual(self.user_model.objects.count(), 2)

    def test_console_lists_operational_identity_without_workspace_data(self):
        self.client.force_login(self.admin)
        response = self.client.get(self.route)
        self.assertContains(response, self.user.email)
        self.assertNotContains(response, self.admin.email)
        self.assertNotContains(response, "Private Workplace Hidden From Platform")
        self.assertIn("no-store", response["Cache-Control"])

    def test_platform_admin_is_redirected_before_workspace_creation(self):
        self.client.force_login(self.admin)
        self.assertRedirects(self.client.get("/app/"), self.route, fetch_redirect_response=False)
        self.assertFalse(Workspace.objects.filter(owner=self.admin).exists())

    def test_failed_workspace_creation_rolls_back_account(self):
        self.client.force_login(self.admin)
        with patch("office.platform_admin.services.bootstrap_workspace", side_effect=RuntimeError("test failure")):
            with self.assertLogs("django.request", level="ERROR"), self.assertRaises(RuntimeError):
                self.client.post(self.route, self.payload())
        self.assertFalse(self.user_model.objects.filter(email=self.email).exists())

    def test_account_creation_requires_csrf(self):
        guarded = Client(enforce_csrf_checks=True)
        guarded.force_login(self.admin)
        self.assertEqual(guarded.post(self.route, self.payload()).status_code, 403)
        self.assertFalse(self.user_model.objects.filter(email=self.email).exists())
