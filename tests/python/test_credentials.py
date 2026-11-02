from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback
import unittest
from unittest.mock import Mock, patch

from wayoom.credentials import CredentialError, CredentialService, ProtectedStorageUnavailable, protected_backend
from wayoom.runtime import ApplicationRuntime
from wayoom.settings import SettingsController
from wayoom.storage import ProfileStore


class FakeVault:
    def __init__(self):
        self.values = {}

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[service, account] = value

    def delete_password(self, service, account):
        self.values.pop((service, account), None)


class CredentialTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.store = ProfileStore(self.root, "a")
        self.vault = FakeVault()
        self.service = CredentialService(lambda: self.vault)

    def test_saved_secret_never_enters_settings_summary_or_logs(self):
        output = io.StringIO()
        secret = "fake-private-key-never-serialize"
        with redirect_stdout(output), redirect_stderr(output):
            reference = self.service.save_key(self.store, "openai", secret)
            summary = self.service.summary(self.store)
        self.assertEqual(self.service.read(self.store, "openai", reference), secret)
        self.assertTrue(summary["setupRequired"])
        self.assertEqual(summary["providers"]["openai"]["connectionStatus"], "untested")
        self.assertNotIn(secret, output.getvalue() + json.dumps(summary))
        for path in self.root.rglob("*.json"):
            self.assertNotIn(secret, path.read_text())
        self.assertNotIn(secret, repr(self.service))
        reopened = CredentialService(lambda: self.vault)
        self.assertTrue(reopened.summary(ProfileStore(self.root, "a"))["setupRequired"])

    def test_profiles_and_installations_do_not_share_keys(self):
        reference = self.service.save_key(self.store, "openai", "fake-a")
        other = ProfileStore(self.root, "b")
        with self.assertRaises(CredentialError):
            self.service.read(other, "openai", reference)
        with self.assertRaises(CredentialError):
            self.service.read(ProfileStore(self.root / "other-install", "a"), "openai", reference)

    def test_protected_failure_requires_explicit_session_opt_in_and_redacts_exception(self):
        secret = "fake-key-echoed-in-backend-error"
        vault = Mock()
        vault.set_password.side_effect = RuntimeError(secret)
        service = CredentialService(lambda: vault)
        try:
            service.save_key(self.store, "openai", secret)
        except ProtectedStorageUnavailable:
            self.assertNotIn(secret, traceback.format_exc())
        else:
            self.fail("Protected save must not silently fall back")
        self.assertEqual(self.store.settings(), {})
        reference = service.save_key(self.store, "openai", secret, session_only=True)
        self.assertEqual(service.read(self.store, "openai", reference), secret)
        service.close_profile(self.store)
        self.assertTrue(service.summary(self.store)["setupRequired"])
        self.assertTrue(CredentialService(lambda: vault).summary(self.store)["setupRequired"])

    def test_replacement_gets_new_reference_removes_old_key_and_does_not_fall_back(self):
        old = self.service.save_key(self.store, "openai", "fake-old")
        new = self.service.save_key(self.store, "openai", "fake-new")
        self.assertNotEqual(old, new)
        with self.assertRaises(CredentialError):
            self.service.read(self.store, "openai", old)
        self.assertEqual(self.service.read(self.store, "openai", new), "fake-new")
        self.assertEqual(len(self.vault.values), 1)

    def test_remove_all_covers_all_profiles_session_keys_and_orphaned_writes(self):
        other = ProfileStore(self.root, "b")
        self.service.save_key(self.store, "openai", "fake-a")
        self.service.save_key(other, "anthropic", "fake-b")
        session = self.service.save_key(other, "google", "fake-session", session_only=True)
        with patch.object(self.store, "update_settings", side_effect=OSError("Disk failed")):
            with self.assertRaises(OSError):
                self.service.save_key(self.store, "mistral", "fake-orphan")
        self.assertEqual(len(self.vault.values), 3)
        self.service.remove_all(self.root)
        self.assertEqual(self.vault.values, {})
        for store in (self.store, other):
            self.assertEqual(store.settings()["providers"], {})
            self.assertEqual(self.service._index(store), [])
        with self.assertRaises(CredentialError):
            self.service.read(other, "google", session)

    def test_failed_delete_keeps_reference_and_reports_no_false_success(self):
        reference = self.service.save_key(self.store, "openai", "fake-keep")
        with patch.object(self.vault, "delete_password", side_effect=RuntimeError("fake-keep")):
            with self.assertRaises(CredentialError) as error:
                self.service.remove_all(self.root)
        self.assertNotIn("fake-keep", str(error.exception))
        self.assertEqual(self.store.settings()["providers"]["openai"]["credentialRef"], reference)
        self.assertTrue(self.service._index(self.store))
        self.service.remove_all(self.root)
        self.assertEqual(self.vault.values, {})

    def test_failed_replacement_cleanup_remains_discoverable(self):
        self.service.save_key(self.store, "openai", "fake-old")
        with patch.object(self.vault, "delete_password", side_effect=RuntimeError("locked")):
            with self.assertRaises(CredentialError):
                self.service.save_key(self.store, "openai", "fake-new")
        self.assertEqual(len(self.vault.values), 2)
        self.assertEqual(len(self.service._index(self.store)), 2)
        self.service.remove_all(self.root)
        self.assertEqual(self.vault.values, {})

    def test_unavailable_active_key_does_not_silently_select_another_provider(self):
        reference = self.service.save_key(self.store, "openai", "fake-active")
        self.service.save_key(self.store, "anthropic", "fake-other")
        self.vault.delete_password(self.service._service(self.store, "openai"), reference)
        state = self.service.summary(self.store)
        self.assertTrue(state["providers"]["anthropic"]["available"])
        self.assertTrue(state["setupRequired"])
        self.assertIsNone(state["activeProviderId"])

    def test_invalid_input_cannot_write_or_inject_references(self):
        for secret in (" ", "key\nheader", "x" * 8193):
            with self.assertRaises(CredentialError):
                self.service.save_key(self.store, "openai", secret)
        with self.assertRaises(CredentialError):
            self.service.read(self.store, "openai", "../elsewhere")
        self.assertEqual(self.vault.values, {})

    def test_active_selection_and_model_change_resets_verification(self):
        controller = SettingsController(self.service, self.store)
        controller.save({"openai": {"key": "fake-a", "modelId": "test-a"}}, None)
        controller.save({"anthropic": {"key": "fake-b", "modelId": "test-b"}}, "openai")
        self.assertEqual(controller.summary()["activeProviderId"], "openai")
        providers = self.store.settings()["providers"]
        providers["openai"]["connectionStatus"] = "verified"
        self.store.update_settings(providers=providers)
        controller.save({"openai": {"modelId": "new-model"}}, "openai")
        self.assertEqual(self.store.settings()["providers"]["openai"]["connectionStatus"], "untested")
        self.service.remove_key(self.store, "openai")
        self.assertEqual(controller.summary()["activeProviderId"], "anthropic")

    def test_runtime_refuses_unconfigured_jobs_and_never_exposes_keys(self):
        runtime = ApplicationRuntime(self.root, object(), credentials=self.service)
        request = {"requestId": "ignored", "destination": {"mode": "existing", "deckId": 1},
                   "cardCount": 1, "topic": "Biology", "difficulty": "intermediate", "providerId": "xai"}
        with patch("wayoom.runtime.recover_in_background") as recovery:
            runtime.open_profile("a", object())
            recovery.call_args.kwargs["on_complete"]({})
        with self.assertRaises(CredentialError):
            runtime.start_configured_job(request)
        reference = self.service.save_key(self.store, "openai", "fake-secret", session_only=True)
        runtime.refresh_configuration()
        with self.assertRaises(CredentialError):
            runtime.start_configured_job(request)
        providers = self.store.settings()["providers"]
        providers["openai"].update(connectionStatus="verified", verifiedModelId=runtime.configuration["providers"]["openai"]["modelId"])
        self.store.update_settings(providers=providers)
        job = runtime.start_configured_job(request)
        self.assertEqual(job["request"]["providerId"], "openai")
        self.assertEqual(job["settings"]["credentialRef"], reference)
        self.assertNotIn("fake-secret", json.dumps(runtime.panel_state()) + json.dumps(job))
        with self.assertRaises(CredentialError):
            SettingsController(self.service, self.store, runtime.settings_guard).remove_all()
        runtime.close_profile()
        self.assertTrue(self.service.summary(self.store)["setupRequired"])

    def test_vendor_imports_in_isolated_interpreter_without_using_real_store(self):
        root = Path(__file__).resolve().parents[2]
        script = ("import sys; sys.path.insert(0, sys.argv[1]); "
                  "import keyring, jaraco.classes, jaraco.context, jaraco.functools, more_itertools; "
                  "from importlib.metadata import version; assert version('keyring') == '25.7.0'; "
                  "import importlib; "
                  "importlib.import_module({'win32':'keyring.backends.Windows', 'darwin':'keyring.backends.macOS', "
                  "'linux':'keyring.backends.SecretService'}[sys.platform])")
        paths = [root / "vendor/runtime/common", root / "vendor/runtime/linux/common",
                 root / f"vendor/runtime/linux/cp{sys.version_info.major}{sys.version_info.minor}"]
        script = "import sys; sys.path[:0] = sys.argv[1:]; " + script
        result = subprocess.run([sys.executable, "-I", "-S", "-c", script, *map(str, paths)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_backend_selection_is_explicit_not_configurable_discovery(self):
        from types import SimpleNamespace
        for system, module, cls in (("win32", "Windows", "WinVaultKeyring"),
                                    ("darwin", "macOS", "Keyring"),
                                    ("linux", "SecretService", "Keyring")):
            backend = SimpleNamespace(priority=5)
            implementation = SimpleNamespace(**{cls: lambda: backend})
            with patch("wayoom.credentials.sys.platform", system), \
                    patch("wayoom.credentials.platform.machine", return_value="x86_64"), \
                    patch("wayoom.credentials.importlib.import_module", return_value=implementation) as importer:
                self.assertIs(protected_backend(), backend)
                importer.assert_called_once_with(f"keyring.backends.{module}")
