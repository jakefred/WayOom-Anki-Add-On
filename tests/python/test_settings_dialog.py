"""Guided native setup with Qt doubles; real key events also run in qt_smoke.py."""
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch
from wayoom.credentials import CredentialService
from wayoom.settings import DISCLOSURE, SettingsController
from wayoom.storage import ProfileStore


class Signal:
    def __init__(self): self.callbacks = []
    def connect(self, callback): self.callbacks.append(callback)
    def emit(self, *args):
        for callback in self.callbacks: callback(*args)


class Widget:
    EchoMode = SimpleNamespace(Password=2)
    def __init__(self, *args):
        self.value = args[0] if args and isinstance(args[0], str) else ""
        self.checked, self.enabled, self.visible, self.blocked = False, True, True, False
        self.items, self.index = [], 0
        self.result = None
        self.clicked, self.textChanged, self.currentIndexChanged = Signal(), Signal(), Signal()
    def setText(self, value):
        self.value = value
        if not self.blocked: self.textChanged.emit(value)
    def text(self): return self.value
    def clear(self): self.value = ""; self.items = []; self.index = 0
    def setChecked(self, value): self.checked = value
    def isChecked(self): return self.checked
    def setEnabled(self, value): self.enabled = value
    def setVisible(self, value): self.visible = value
    def hide(self): self.visible = False
    def show(self): self.visible = True
    def setEchoMode(self, value): self.echo = value
    def setPlaceholderText(self, value): self.placeholder = value
    def setAutoDefault(self, value): self.auto_default = value
    def setDefault(self, value): self.default = value
    def blockSignals(self, value): self.blocked = value
    def addItem(self, text, data): self.items.append((text, data))
    def findData(self, data): return next((i for i, item in enumerate(self.items) if item[1] == data), -1)
    def setCurrentIndex(self, index):
        changed = index != self.index
        self.index = index
        if changed and not self.blocked: self.currentIndexChanged.emit(index)
    def currentData(self): return self.items[self.index][1] if self.items else None
    def done(self, value): self.result = value
    def accept(self): self.done(1)
    def reject(self): self.done(0)
    def __getattr__(self, _name): return lambda *args: None


class SettingsDialogTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = ProfileStore(Path(directory.name), "a")
        self.service = CredentialService(lambda: self.fail("No OS store"))
        self.controller = SettingsController(self.service, self.store)
        qt = ModuleType("aqt.qt")
        for name in ("QCheckBox", "QComboBox", "QDialog", "QFormLayout", "QHBoxLayout", "QLabel",
                     "QLineEdit", "QPushButton", "QScrollArea", "QVBoxLayout", "QWidget"):
            setattr(qt, name, Widget)
        qt.Qt = SimpleNamespace(TextFormat=SimpleNamespace(PlainText=0), Key=SimpleNamespace(Key_Return=1, Key_Enter=2),
                                FocusPolicy=SimpleNamespace(ClickFocus=1))
        qt.QEvent = SimpleNamespace(Type=SimpleNamespace(KeyPress=1))
        spec = importlib.util.spec_from_file_location("wayoom.settings_dialog", Path(__file__).resolve().parents[2] / "wayoom/settings_dialog.py")
        module = importlib.util.module_from_spec(spec)
        patches = patch.dict("sys.modules", {"aqt.qt": qt, "wayoom.settings_dialog": module})
        patches.start(); self.addCleanup(patches.stop)
        spec.loader.exec_module(module)
        self.loads, self.tests = [], []
        self.cancel = Mock()
        self.dialog = module.SettingsDialog(None, self.controller, welcome=True, dark=True,
            load_models=lambda provider, callback: (self.loads.append((provider, callback)), self.cancel)[1],
            test_connection=lambda provider, callback: self.tests.append((provider, callback)))

    def connect(self):
        self.dialog.key.setText("sk-proj-fake-only")
        self.dialog.session.setChecked(True)
        self.dialog.connect_provider()
        self.loads[-1][1]({"models": [{"id": "gpt-fixture", "name": "Fixture model", "inputTokens": 128000}]})

    def verify(self):
        self.dialog.test_provider()
        providers = self.store.settings()["providers"]
        providers["openai"].update(connectionStatus="verified", verifiedModelId="gpt-fixture")
        self.store.update_settings(providers=providers)
        self.tests[-1][1]("success")

    def test_first_run_title_flow_masking_and_skip_without_save(self):
        self.assertTrue(self.dialog.welcome.visible)
        self.assertFalse(self.dialog.fields.visible)
        self.assertEqual(self.dialog.key.echo, Widget.EchoMode.Password)
        self.assertEqual(self.dialog.key.placeholder, "Paste your API key")
        self.assertFalse(self.dialog.model.enabled)
        self.assertFalse(self.dialog.done_button.enabled)
        self.dialog.begin_setup()
        self.assertTrue(self.dialog.fields.visible)
        self.dialog.key.setText("sk-proj-unsaved")
        self.dialog.reject()
        self.assertEqual(self.dialog.key.text(), "")
        self.assertEqual(self.store.settings(), {})

    def test_key_detection_and_ambiguous_company_choice_preserve_typed_key(self):
        self.dialog.key.setText("sk-proj-example")
        self.assertEqual(self.dialog.provider.currentData(), "openai")
        self.assertTrue(self.dialog.connect_button.enabled)
        self.dialog.key.setText("key with spaces")
        self.assertFalse(self.dialog.connect_button.enabled)
        self.dialog.key.setText("opaque-mistral-key")
        self.dialog.provider.setCurrentIndex(self.dialog.provider.findData("mistral"))
        self.assertEqual(self.dialog.key.text(), "opaque-mistral-key")
        self.assertFalse(self.dialog.done_button.enabled)

    def test_key_then_catalog_then_model_verification_is_required_before_done(self):
        self.connect()
        self.assertEqual(self.loads[-1][0], "openai")
        self.assertEqual(self.dialog.key.text(), "")
        self.assertEqual(self.dialog.model.currentData(), "gpt-fixture")
        self.assertIn("128,000", self.dialog.model_info.text())
        self.assertTrue(self.dialog.test_button.enabled)
        self.assertFalse(self.dialog.done_button.enabled)
        self.dialog.finish()
        self.assertIsNone(self.dialog.result)
        self.assertTrue(self.service.summary(self.store)["setupRequired"])
        self.verify()
        self.assertTrue(self.dialog.done_button.enabled)
        self.dialog.finish()
        self.assertEqual(self.dialog.result, 1)
        self.assertFalse(self.service.summary(self.store)["setupRequired"])

    def test_model_change_and_new_key_disable_done_until_reverified(self):
        self.connect(); self.verify()
        self.dialog._models.append({"id": "gpt-other", "name": "Other"})
        self.dialog.fill_models("gpt-other")
        self.dialog.model_changed()
        self.assertFalse(self.dialog.done_button.enabled)
        self.dialog.test_provider()
        self.assertTrue(self.service.summary(self.store)["setupRequired"])
        self.tests[-1][1]("model")
        self.assertFalse(self.dialog.done_button.enabled)
        self.dialog.key.setText("sk-proj-replacement")
        self.assertFalse(self.dialog.model.enabled)
        self.assertFalse(self.dialog.test_button.enabled)

    def test_saved_key_has_persistent_identity_and_explicit_replacement(self):
        self.connect()
        self.assertFalse(self.dialog.key.visible)
        self.assertTrue(self.dialog.saved_key.visible)
        self.assertIn("OpenAI key saved in this session", self.dialog.saved_key.text())
        self.dialog.replace_key()
        self.assertTrue(self.dialog.key.visible)
        self.dialog.key.setText("sk-proj-replacement")
        self.dialog.replace_key()
        self.assertEqual(self.dialog.key.text(), "")
        self.assertFalse(self.dialog.key.visible)
        self.assertTrue(self.dialog.test_button.enabled)

    def test_test_progress_and_failure_are_visible_and_retest_recovers(self):
        self.connect()
        self.dialog.test_provider()
        self.assertTrue(self.dialog.message.visible)
        self.assertIn("Testing selected model", self.dialog.message.text())
        self.assertFalse(self.dialog.test_button.enabled)
        self.tests[-1][1]("network")
        self.assertIn("Test failed", self.dialog.message.text())
        self.assertTrue(self.dialog.message.visible)
        self.assertTrue(self.dialog.test_button.enabled)
        self.verify()
        self.assertIn("Test passed", self.dialog.message.text())

    def test_catalog_failure_and_close_cancel_work_and_ignore_late_results(self):
        self.dialog.key.setText("sk-proj-fake-only")
        self.dialog.session.setChecked(True)
        self.dialog.connect_provider()
        self.loads[-1][1]({"error": "authentication"})
        self.assertFalse(self.dialog.model.enabled)
        self.assertFalse(self.dialog.done_button.enabled)
        self.dialog.connect_provider()
        self.dialog.reject()
        self.cancel.assert_called_once()
        before = self.dialog.message.text()
        self.loads[-1][1]({"models": []})
        self.assertEqual(self.dialog.message.text(), before)

    def test_enter_advances_without_activating_default_done_button(self):
        self.dialog.focusNextChild = Mock()
        event = SimpleNamespace(key=lambda: 1, type=lambda: 1, accept=Mock())
        self.assertTrue(self.dialog.eventFilter(self.dialog.key, event))
        self.dialog.keyPressEvent(event)
        self.assertEqual(self.dialog.focusNextChild.call_count, 2)
        self.assertIsNone(self.dialog.result)
        self.assertFalse(self.dialog.done_button.auto_default)
        self.assertFalse(self.dialog.done_button.default)

    def test_network_failure_preserves_prior_verification_and_reopen_has_saved_placeholder(self):
        self.connect(); self.verify()
        self.dialog.reload()
        self.assertIn("Saved key", self.dialog.key.placeholder)
        self.dialog.test_provider()
        self.tests[-1][1]("network")
        self.assertIn("not marked invalid", self.dialog.message.text())
        self.assertTrue(self.dialog.done_button.enabled)

    def test_remove_all_clears_keys_and_blocks_completion(self):
        self.connect(); self.verify()
        self.dialog.remove_all()
        self.assertTrue(self.controller.summary()["setupRequired"])
        self.assertIn("All keys removed", self.dialog.message.text())
        self.assertFalse(self.dialog.done_button.enabled)

    def test_disclosures_cover_required_privacy_and_billing(self):
        for text in ("billed", "topic", "Fronts", "unrelated decks", "dedicated", "spending limit", "session-only", "uninstalling", "revoke"):
            self.assertIn(text, DISCLOSURE)
