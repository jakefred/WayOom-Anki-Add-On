"""Native dialog logic with explicit Qt doubles; never touches a real keychain."""
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from wayoom.credentials import CredentialService
from wayoom.settings import DISCLOSURE, SettingsController
from wayoom.storage import ProfileStore


class Signal:
    def connect(self, callback):
        self.callback = callback


class Widget:
    EchoMode = SimpleNamespace(Password=2)
    StandardButton = SimpleNamespace(Save=1, Cancel=2)

    def __init__(self, *args):
        self.value = args[0] if args and isinstance(args[0], str) else ""
        self.checked, self.enabled = False, True
        self.items, self.index = [], 0
        self.clicked, self.accepted, self.rejected = Signal(), Signal(), Signal()

    def setText(self, value): self.value = value
    def text(self): return self.value
    def clear(self): self.value = ""
    def setChecked(self, value): self.checked = value
    def isChecked(self): return self.checked
    def setEnabled(self, value): self.enabled = value
    def setEchoMode(self, value): self.echo = value
    def addItem(self, text, data): self.items.append((text, data))
    def findData(self, data): return next((i for i, item in enumerate(self.items) if item[1] == data), -1)
    def setCurrentIndex(self, index): self.index = index
    def currentData(self): return self.items[self.index][1]
    def done(self, value): self.result = value
    def accept(self): self.done(1)
    def reject(self): self.done(0)
    def __getattr__(self, _name): return lambda *args: None


class SettingsDialogTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = ProfileStore(Path(directory.name), "a")
        # Session-only saves must not instantiate any OS backend.
        self.service = CredentialService(lambda: self.fail("Real backend must never be used"))
        self.controller = SettingsController(self.service, self.store)
        qt = ModuleType("aqt.qt")
        for name in ("QCheckBox", "QComboBox", "QDialog", "QDialogButtonBox", "QFormLayout",
                     "QGroupBox", "QLabel", "QLineEdit", "QPushButton", "QScrollArea", "QVBoxLayout", "QWidget"):
            setattr(qt, name, Widget)
        path = Path(__file__).resolve().parents[2] / "wayoom/settings_dialog.py"
        spec = importlib.util.spec_from_file_location("wayoom.settings_dialog", path)
        module = importlib.util.module_from_spec(spec)
        patches = patch.dict("sys.modules", {"aqt.qt": qt, "wayoom.settings_dialog": module})
        patches.start()
        self.addCleanup(patches.stop)
        spec.loader.exec_module(module)
        self.dialog = module.SettingsDialog(None, self.controller)

    def test_masked_inputs_empty_and_cancel_does_not_save(self):
        for row in self.dialog.rows.values():
            self.assertEqual(row["key"].echo, Widget.EchoMode.Password)
            self.assertEqual(row["key"].text(), "")
            self.assertFalse(row["test"].enabled)
        self.dialog.rows["openai"]["key"].setText("fake-unsaved")
        self.dialog.reject()
        self.assertEqual(self.dialog.rows["openai"]["key"].text(), "")
        self.assertEqual(self.store.settings(), {})

    def test_save_one_session_key_and_reopen_without_prefilling_secret(self):
        row = self.dialog.rows["openai"]
        row["key"].setText("fake-session")
        row["session"].setChecked(True)
        self.dialog.save()
        self.assertEqual(self.dialog.result, 1)
        self.assertTrue(self.store.settings()["setupComplete"])
        self.assertEqual(self.controller.summary()["activeProviderId"], "openai")
        self.assertEqual(row["key"].text(), "")
        self.assertIn("untested", row["status"].text())
        self.dialog.reload()
        self.assertEqual(row["key"].text(), "")
        self.assertNotIn("fake-session", row["status"].text())

    def test_remove_all_clears_unsaved_inputs_and_reports_setup_required(self):
        self.service.save_key(self.store, "openai", "fake-saved", session_only=True)
        self.dialog.rows["openai"]["key"].setText("fake-unsaved")
        self.dialog.remove_all()
        self.assertTrue(self.controller.summary()["setupRequired"])
        self.assertIn("All keys removed", self.dialog.message.text())
        self.assertEqual(self.dialog.rows["openai"]["key"].text(), "")

    def test_disclosures_cover_billing_privacy_spending_storage_and_uninstall(self):
        for text in ("billed", "topic", "Fronts", "unrelated decks", "dedicated", "spending limit",
                     "does not enable or enforce", "session-only", "uninstalling", "every profile", "revoke"):
            self.assertIn(text, DISCLOSURE)

    def test_native_test_passes_only_provider_and_callback_and_preserves_verified_on_network_error(self):
        self.service.save_key(self.store, "openai", "fake-saved", session_only=True, model_id="fixture")
        requests = []
        self.dialog.test_connection = lambda provider, callback: requests.append((provider, callback))
        self.dialog.reload()
        self.assertTrue(self.dialog.rows["openai"]["test"].enabled)
        self.dialog.test_provider("openai")
        self.assertEqual(requests[0][0], "openai")
        self.assertFalse(self.dialog.rows["openai"]["test"].enabled)
        requests[0][1]("network")
        self.assertIn("not marked invalid", self.dialog.message.text())
        self.assertTrue(self.dialog.rows["openai"]["test"].enabled)
        self.dialog.rows["openai"]["key"].setText("fake-unsaved")
        self.dialog.test_provider("openai")
        self.assertEqual(len(requests), 1)
        self.assertIn("Save", self.dialog.message.text())
        self.dialog.clear_keys()
        self.dialog.test_provider("openai")
        self.dialog.reject()
        requests[-1][1]("success")  # late callback must not mutate the closed dialog
        self.assertTrue(self.dialog._closed)
