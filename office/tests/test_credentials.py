import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from office import credentials, providers


class ProviderCredentialTests(SimpleTestCase):
    owner_key = "fake-openai-key-for-owner-101"
    other_key = "fake-openai-key-for-owner-202"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        override = override_settings(DATA_DIR=self.root, MEDIA_ROOT=self.root / "media", HTTPS_ENABLED=True)
        override.enable()
        self.addCleanup(override.disable)

    def test_credentials_are_encrypted_and_bound_to_account_and_provider(self):
        credentials.save_api_key(101, "openai", self.owner_key)
        credentials.save_api_key(202, "openai", self.other_key)
        self.assertTrue(credentials.has_api_key(101, "openai"))
        self.assertFalse(credentials.has_api_key(101, "claude"))
        self.assertFalse(credentials.has_api_key(303, "openai"))
        directory = self.root / "provider-credentials"
        source = directory / "101-openai.enc"
        encrypted = source.read_bytes()
        self.assertNotIn(self.owner_key.encode(), encrypted)
        self.assertFalse(source.is_relative_to(self.root / "media"))
        if os.name != "nt":
            self.assertEqual(source.stat().st_mode & 0o777, 0o600)
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        (directory / "202-openai.enc").write_bytes(encrypted)
        with self.assertRaises(credentials.CredentialError):
            credentials.load_api_key(202, "openai")

    def test_generation_sends_only_calling_account_key_and_removal_blocks(self):
        credentials.save_api_key(101, "openai", self.owner_key)
        credentials.save_api_key(202, "openai", self.other_key)
        response = {"status": "completed", "output": [{"type": "message", "content": [
            {"type": "output_text", "text": "Entrega de teste"},
        ]}]}
        with patch.object(providers, "_post", return_value=response) as post:
            providers.generate("openai", "test-model", "Instruções", "Pedido", owner_id=101)
            providers.generate("openai", "test-model", "Instruções", "Pedido", owner_id=202)
            self.assertEqual([call.args[2]["Authorization"] for call in post.call_args_list],
                             [f"Bearer {self.owner_key}", f"Bearer {self.other_key}"])
            post.reset_mock()
            credentials.delete_api_key(202, "openai")
            with self.assertRaises(providers.ProviderError):
                providers.generate("openai", "test-model", "Instruções", "Pedido", owner_id=202)
            post.assert_not_called()
        self.assertTrue(credentials.has_api_key(101, "openai"))

    def test_hosted_ignores_environment_even_for_explicit_legacy_owner(self):
        variables = {"OPENAI_API_KEY": self.owner_key, "NABI_LEGACY_PROVIDER_OWNER_ID": "101"}
        with patch.dict(os.environ, variables):
            self.assertFalse(providers.has_api_key("openai", owner_id=101))
            self.assertFalse(providers.has_api_key("openai", owner_id=202))
            with self.settings(HTTPS_ENABLED=False):
                self.assertTrue(providers.has_api_key("openai", owner_id=101))
                self.assertFalse(providers.has_api_key("openai", owner_id=202))

    def test_corrupt_credential_never_falls_back_to_bound_legacy_key(self):
        credentials.save_api_key(101, "openai", self.owner_key)
        (self.root / "provider-credentials" / "101-openai.enc").write_bytes(b"invalid ciphertext")
        variables = {"OPENAI_API_KEY": self.other_key, "NABI_LEGACY_PROVIDER_OWNER_ID": "101"}
        with self.settings(HTTPS_ENABLED=False), patch.dict(os.environ, variables):
            self.assertFalse(providers.has_api_key("openai", owner_id=101))
