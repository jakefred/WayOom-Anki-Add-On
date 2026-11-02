"""Native guided setup. Secrets never enter the webview."""
from aqt.qt import (QCheckBox, QComboBox, QDialog, QEvent,
                    QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, Qt,
                    QVBoxLayout, QWidget)
from .catalog import detect_provider, key_problem
from .settings import PROVIDER_INFO
from .validation import ValidationError

TEST_MESSAGES = {
    "success": "Verified. This key and model are ready to generate cards.",
    "authentication": "The provider rejected this key. Check or replace it and try again.",
    "model": "This model cannot be used for flashcards. Choose another model and test again.",
    "quota": "The provider reports insufficient credits. Check your API billing.",
    "rate-limit": "The provider is busy or rate limiting requests. Try again shortly.",
    "network": "Could not reach the provider. Check your connection and try again; the saved key was not marked invalid.",
    "timeout": "The check timed out. Try again.", "canceled": "The check was canceled.",
    "credentials": "The saved key is unavailable. Paste a key to reconnect.",
    "refusal": "The provider refused the test. Try another model.",
    "malformed": "The provider returned an unexpected response. Try another model or retry.",
    "redirect": "The provider redirected the request. The key was not forwarded.",
    "oversized": "The model list or response was too large. Try again later.",
    "refused": "The check could not run. Reopen Settings and try again.",
    "provider": "The provider check failed. Try again.",
    "local": "WayOom encountered a local application error while testing. Restart Anki and retry.",
}


class SettingsDialog(QDialog):
    def __init__(self, parent, controller, on_changed=lambda: None, test_connection=None,
                 load_models=None, welcome=False, dark=None):
        super().__init__(parent)
        self.controller, self.on_changed = controller, on_changed
        self.test_connection, self.load_models = test_connection, load_models
        self._closed = self._busy = self._refreshing = False
        self._cancel_load = None
        self._sequence = 0
        self._models = []
        self._replacing = False
        self.setObjectName("WayOomSettings")
        self.setWindowTitle("WayOom")
        self.setModal(True)
        self.resize(580, 720)
        if dark is None:
            try:
                from aqt.theme import theme_manager
                dark = theme_manager.night_mode
            except Exception:
                dark = False
        bg, fg, field, border, muted = ("#202124", "#f1f3f4", "#292a2d", "#64676d", "#c2c5cc") if dark else (
            "#ffffff", "#202124", "#f7f8fa", "#92969e", "#50555e")
        self._link_color = "#8ab4f8" if dark else "#2159a8"
        self.setStyleSheet(f"""
            QDialog#WayOomSettings {{ background: {bg}; color: {fg}; }}
            QLabel, QCheckBox {{ color: {fg}; background: transparent; }}
            QLabel#WayOomTitle {{ font-size: 30px; font-weight: 700; }}
            QLabel#WayOomSubtitle {{ color: {muted}; }}
            QScrollArea {{ border: 0; background: transparent; }}
            QWidget#SetupBody {{ background: {bg}; }}
            QLineEdit, QComboBox {{ color: {fg}; background: {field}; border: 1px solid {border}; border-radius: 5px; padding: 9px; min-height: 20px; }}
            QComboBox QAbstractItemView {{ color: {fg}; background: {field}; selection-background-color: #305fa6; selection-color: #ffffff; }}
            QPushButton {{ color: {fg}; background: {field}; border: 1px solid {border}; border-radius: 5px; padding: 9px 14px; }}
            QPushButton#Primary {{ color: #ffffff; background: #305fa6; border-color: #305fa6; }}
            QPushButton:disabled, QComboBox:disabled {{ color: {muted}; background: {bg}; }}
            QPushButton#Primary:disabled {{ color: {muted}; background: {bg}; border-color: {border}; }}
            QPushButton:focus, QLineEdit:focus, QComboBox:focus {{ border: 2px solid #729fe0; }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)
        title = self.label("WayOom")
        title.setObjectName("WayOomTitle")
        layout.addWidget(title)
        subtitle = self.label("Turn what you want to learn into flashcards.")
        subtitle.setObjectName("WayOomSubtitle")
        layout.addWidget(subtitle)
        self.message = self.label("")
        self.message.setObjectName("SetupResult")
        self.message.setMinimumHeight(64)
        self.message.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._message_colors = {"info": "#729fe0", "success": "#62ba85" if dark else "#267548",
                                "error": "#ff9b9b" if dark else "#ae2929"}
        self._message_background = field
        self.message.hide()
        layout.addWidget(self.message)
        self.welcome = QWidget()
        welcome_layout = QVBoxLayout(self.welcome)
        welcome_layout.addWidget(self.label("Connect an AI provider to get started. You’ll verify your API key, choose a model from a list, and test it before generating cards."))
        welcome_layout.addWidget(self.label("You can skip this now and return through Settings at any time."))
        welcome_layout.addWidget(self.label("You’ll need a provider API key. API usage is billed by your provider; an ordinary chat subscription may not include API credits."))
        self.setup_now = self.button("Set up now", self.begin_setup, primary=True)
        welcome_layout.addWidget(self.setup_now)
        welcome_layout.addStretch()
        layout.addWidget(self.welcome, 1)
        self.fields = QWidget()
        form_layout = QVBoxLayout(self.fields)
        form_layout.setContentsMargins(0, 0, 0, 0)
        form_layout.setSpacing(10)
        self.key = QLineEdit()
        self.key.setMinimumHeight(42)
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setMaxLength(8192)
        self.key.setPlaceholderText("Paste your API key")
        self.provider = QComboBox()
        self.provider.setMinimumHeight(42)
        self.provider.addItem("Detect from key, or choose company", None)
        for provider, (name, _url) in PROVIDER_INFO.items():
            self.provider.addItem(name, provider)
        form_layout.addWidget(self.label("1. API key"))
        form_layout.addWidget(self.key)
        self.saved_key = self.label("")
        form_layout.addWidget(self.saved_key)
        self.replace_key_button = self.button("Use a different key", self.replace_key)
        form_layout.addWidget(self.replace_key_button)
        form_layout.addWidget(self.label("Company"))
        form_layout.addWidget(self.provider)
        self.key_status = self.label("")
        form_layout.addWidget(self.key_status)
        self.session = QCheckBox("Keep a new key for this session only")
        form_layout.addWidget(self.session)
        self.connect_button = self.button("Verify key && load models", self.connect_provider, primary=True)
        form_layout.addWidget(self.connect_button)
        self.model = QComboBox()
        self.model.setMinimumHeight(42)
        form_layout.addWidget(self.label("2. Model"))
        form_layout.addWidget(self.model)
        self.model_info = self.label("Connect your key to see available text models.")
        form_layout.addWidget(self.model_info)
        self.test_button = self.button("Test selected model", self.test_provider)
        form_layout.addWidget(self.test_button)
        form_layout.addWidget(self.label("API usage is billed by tokens through your provider. Testing makes one small generation request."))
        self.manage_link = QLabel()
        self.manage_link.setOpenExternalLinks(True)
        form_layout.addWidget(self.manage_link)
        body = QWidget()
        body.setObjectName("SetupBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 8, 0)
        body_layout.setSpacing(14)
        body_layout.addWidget(self.fields)
        body_layout.addWidget(self.label("Your topic, generated content and generated fronts in the selected deck may be sent to this provider. Use a dedicated key with a low spending limit. Keys use OS protected storage unless you select session only."))
        cleanup = QHBoxLayout()
        self.remove_button = self.button("Remove selected key", self.remove_provider)
        self.remove_all_button = self.button("Remove all keys — every profile", self.remove_all)
        cleanup.addWidget(self.remove_button)
        cleanup.addWidget(self.remove_all_button)
        body_layout.addLayout(cleanup)
        body_layout.addWidget(self.label("Remove all keys before uninstalling. Local removal does not revoke a provider key."))
        body_layout.addStretch()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(body)
        layout.addWidget(self.scroll, 1)
        actions = QHBoxLayout()
        self.skip_button = self.button("Skip for now" if welcome else "Close", self.reject)
        self.done_button = self.button("Done", self.finish, primary=True)
        actions.addWidget(self.skip_button)
        actions.addStretch()
        actions.addWidget(self.done_button)
        layout.addLayout(actions)
        self.key.textChanged.connect(self.key_changed)
        self.provider.currentIndexChanged.connect(self.provider_changed)
        self.model.currentIndexChanged.connect(self.model_changed)
        for widget in (self.key, self.provider, self.model):
            widget.installEventFilter(self)
        state = self.controller.summary()
        self.provider.setCurrentIndex(max(0, self.provider.findData(state["activeProviderId"])))
        self.reload()
        self.welcome.setVisible(welcome)
        self.fields.setVisible(not welcome)
        self.scroll.setVisible(not welcome)
        self.done_button.setVisible(not welcome)
        if welcome:
            self.resize(580, 460)

    def show_message(self, text, tone="info"):
        self.message.setText(text)
        self.message.setVisible(bool(text))
        self.message.setStyleSheet(f"color: {self._message_colors[tone]}; background: {self._message_background}; "
                                  f"border: 2px solid {self._message_colors[tone]}; border-radius: 6px; "
                                  "padding: 12px; font-size: 16px; font-weight: 600;")
        if text:
            self.message.setFocus()

    def replace_key(self):
        if self._busy:
            return
        self._replacing = not self._replacing
        self.clear_keys()
        self.show_message("")
        self.update_controls()
        if self._replacing:
            self.key.setFocus()

    @staticmethod
    def label(text):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        return label

    @staticmethod
    def button(text, callback, primary=False):
        button = QPushButton(text)
        button.setAutoDefault(False)
        button.setDefault(False)
        if primary:
            button.setObjectName("Primary")
        button.clicked.connect(lambda _checked=False: callback())
        return button

    def begin_setup(self):
        self.welcome.hide()
        self.fields.show()
        self.scroll.show()
        self.done_button.show()
        self.skip_button.setText("Close setup")
        self.resize(580, 720)
        self.key.setFocus()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if obj in (self.provider, self.model) and obj.view().isVisible():
                return False
            self.focusNextChild()
            return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.focusNextChild()
            event.accept()
        else:
            super().keyPressEvent(event)

    def saved(self):
        return self._state["providers"].get(self.provider.currentData(), {})

    def reload(self):
        self._refreshing = True
        try:
            self._state = self.controller.summary()
            saved = self.saved()
            self.key.setPlaceholderText("Saved key — paste to replace" if saved.get("available") else "Paste your API key")
            self.session.setChecked(saved.get("storage") == "session")
            self._models = [{"id": saved["modelId"], "name": saved["modelId"]}] if saved.get("available") else []
            self.fill_models(saved.get("modelId"))
            name, url = PROVIDER_INFO.get(self.provider.currentData(), ("your provider", ""))
            self.manage_link.setText(f'<a style="color:{self._link_color}" href="{url}">Get or manage {name} API keys</a>' if url else "")
        finally:
            self._refreshing = False
        self.update_controls()

    def fill_models(self, selected=None):
        self.model.blockSignals(True)
        self.model.clear()
        self.model.addItem("Choose a model" if self._models else "Connect a key first", None)
        for item in self._models:
            self.model.addItem(item["name"], item["id"])
        self.model.setCurrentIndex(max(0, self.model.findData(selected)))
        self.model.blockSignals(False)

    def provider_changed(self, *_args):
        if self._refreshing:
            return
        detected = detect_provider(self.key.text())
        if detected and detected != self.provider.currentData():
            self.clear_keys()
        self._replacing = False
        self.show_message("")
        self.reload()

    def key_changed(self, *_args):
        if self._refreshing:
            return
        detected = detect_provider(self.key.text())
        if detected and detected != self.provider.currentData():
            self.provider.blockSignals(True)
            self.provider.setCurrentIndex(self.provider.findData(detected))
            self.provider.blockSignals(False)
            self.reload()
        self.show_message("")
        self.update_controls()

    def model_changed(self, *_args):
        if not self._refreshing:
            self.show_message("")
            self.update_controls()

    def update_controls(self):
        saved = self.saved()
        self.key.setPlaceholderText("Saved key — paste to replace" if saved.get("available") else "Paste your API key")
        key = self.key.text().strip()
        provider, model = self.provider.currentData(), self.model.currentData()
        dirty = bool(key)
        available = bool(saved.get("available"))
        entering = not available or self._replacing or dirty
        name = PROVIDER_INFO.get(provider, ("Provider", ""))[0]
        storage = "this session" if saved.get("storage") == "session" else "protected storage"
        self.saved_key.setText(f"{name} key saved in {storage}. You do not need to paste it again.")
        self.saved_key.setVisible(available)
        self.key.setVisible(entering)
        self.session.setVisible(entering)
        self.replace_key_button.setVisible(available)
        self.replace_key_button.setEnabled(not self._busy)
        self.replace_key_button.setText("Keep saved key" if self._replacing else "Use a different key")
        self.test_button.setText("Please wait…" if self._busy else "Test selected model")
        if dirty:
            problem = key_problem(key)
            status = problem or (f"Looks like a {PROVIDER_INFO[provider][0]} key. Verify it to confirm." if detect_provider(key)
                                 else "Key entered. Choose its company, then verify it.")
        elif saved.get("available"):
            status = "Saved key available. Refresh models or test the selected model."
        else:
            status = "Paste a key. We’ll detect the company where possible."
        self.key_status.setText(status)
        self.connect_button.setEnabled(not self._busy and bool(self.load_models) and bool(provider)
                                       and (not key_problem(key) if dirty else bool(saved.get("available"))))
        self.connect_button.setText("Verify key && load models" if dirty or not saved.get("available") else "Refresh available models")
        self.model.setEnabled(not self._busy and not dirty and bool(self._models))
        self.test_button.setEnabled(not self._busy and not dirty and bool(model) and bool(self.test_connection))
        verified = saved.get("connectionStatus") == "verified" and saved.get("modelId") == model and not dirty
        self.done_button.setEnabled(not self._busy and verified)
        self.key.setEnabled(not self._busy)
        self.provider.setEnabled(not self._busy)
        self.session.setEnabled(not self._busy)
        self.remove_button.setEnabled(not self._busy and bool(provider) and bool(saved))
        self.remove_all_button.setEnabled(not self._busy)
        item = next((item for item in self._models if item["id"] == model), {})
        limits = [f"{item[field]:,} {label} tokens" for field, label in (("inputTokens", "input/context"), ("outputTokens", "output")) if field in item]
        self.model_info.setText(("Verified and ready." if verified else "Select a model, then test it before finishing.")
                               + (" " + "; ".join(limits) + "." if limits else "") if model and not dirty
                               else "Connect your key to see available text models.")

    def clear_keys(self):
        self.key.blockSignals(True)
        self.key.clear()
        self.key.blockSignals(False)

    def connect_provider(self):
        if self._busy or not self.load_models:
            return
        provider = self.provider.currentData()
        try:
            self.controller.connect(provider, self.key.text(), self.session.isChecked())
        except ValidationError as error:
            self.show_message(str(error))
            return
        except Exception:
            self.show_message("The key could not be saved. Try again or choose session-only storage.")
            return
        self.clear_keys()
        self._replacing = False
        self._state = self.controller.summary()
        self._busy = True
        self._sequence += 1
        sequence = self._sequence
        self.show_message("Checking the key and loading available models…")
        self.update_controls()
        self.on_changed()
        def complete(result):
            if self._closed or sequence != self._sequence:
                return
            self._cancel_load = None
            self._busy = False
            self._state = self.controller.summary()
            if "error" in result:
                self._models = []
                self.fill_models()
                self.show_message(TEST_MESSAGES.get(result["error"], TEST_MESSAGES["provider"]), "error")
            else:
                self._models = result["models"]
                saved_model = self.saved().get("modelId")
                ids = [item["id"] for item in self._models]
                self.fill_models(saved_model if saved_model in ids else ids[0] if ids else None)
                self.show_message("Key accepted. Choose a model and test it." if ids else "Key accepted, but no compatible text models were returned. Check this key’s model permissions.")
                self.model.setFocus()
            self.update_controls()
        try:
            self._cancel_load = self.load_models(provider, complete)
        except Exception:
            complete({"error": "provider"})

    def test_provider(self):
        if self._busy or not self.test_connection or self.key.text().strip():
            return
        provider, model = self.provider.currentData(), self.model.currentData()
        if model not in [item["id"] for item in self._models]:
            return
        try:
            self.controller.choose_model(provider, model)
            self._busy = True
            self.show_message("Testing selected model…\nPlease wait for the result before finishing setup.")
            self.update_controls()
            self.message.setFocus()
            self.on_changed()
            def complete(category):
                if self._closed:
                    return
                self._busy = False
                self._state = self.controller.summary()
                self.show_message(("Test passed\n" if category == "success" else "Test failed\n") + TEST_MESSAGES.get(category, TEST_MESSAGES["provider"]), "success" if category == "success" else "error")
                self.update_controls()
                self.on_changed()
            self.test_connection(provider, complete)
        except ValidationError as error:
            self._busy = False
            self.show_message("Test could not start\n" + str(error), "error")
            self.update_controls()
        except Exception:
            self._busy = False
            self.show_message("Test failed\n" + TEST_MESSAGES["provider"], "error")
            self.update_controls()

    def finish(self):
        if self._busy or self.key.text().strip():
            return
        try:
            self.controller.finish(self.provider.currentData(), self.model.currentData())
        except ValidationError as error:
            self.show_message(str(error))
        except Exception:
            self.show_message("Settings could not be saved. Try again.")
        else:
            self.on_changed()
            self.accept()

    def remove_provider(self):
        self.remove_keys(all_profiles=False)

    def remove_all(self):
        self.remove_keys(all_profiles=True)

    def remove_keys(self, all_profiles):
        if self._busy:
            return
        try:
            if all_profiles:
                self.controller.remove_all()
            else:
                self.controller.remove(self.provider.currentData())
            self.show_message("All keys removed from every saved profile. Setup is required." if all_profiles else "Key removed. Connect a key to continue.")
        except ValidationError as error:
            self.show_message(str(error))
        except Exception:
            self.show_message("Key removal could not be confirmed. Retry before uninstalling.")
        finally:
            self.clear_keys()
            self.reload()
            self.on_changed()

    def done(self, result):
        self._closed = True
        self._sequence += 1
        if self._cancel_load:
            self._cancel_load()
            self._cancel_load = None
        self.clear_keys()
        super().done(result)
