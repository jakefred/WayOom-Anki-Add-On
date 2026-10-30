"""Native Qt credential UI. Never embedded in or serialized to the webview."""
from aqt.qt import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                    QGroupBox, QLabel, QLineEdit, QPushButton, QScrollArea,
                    QVBoxLayout, QWidget)

from .credentials import CredentialError
from .settings import DISCLOSURE, PROVIDER_INFO


class SettingsDialog(QDialog):
    def __init__(self, parent, controller, on_changed=lambda: None):
        super().__init__(parent)
        self.controller, self.on_changed = controller, on_changed
        self.setWindowTitle("WayOom setup and settings")
        self.setModal(True)
        self.resize(660, 760)
        layout = QVBoxLayout(self)
        disclosure = QLabel(DISCLOSURE)
        disclosure.setWordWrap(True)
        layout.addWidget(disclosure)
        self.active = QComboBox()
        self.active.addItem("Choose active provider", None)
        for provider, (name, _url) in PROVIDER_INFO.items():
            self.active.addItem(name, provider)
        active_form = QFormLayout()
        active_form.addRow("Active provider", self.active)
        layout.addLayout(active_form)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        providers_layout = QVBoxLayout(content)
        self.rows = {}
        for provider, (name, url) in PROVIDER_INFO.items():
            box = QGroupBox(name)
            form = QFormLayout(box)
            key = QLineEdit()
            key.setEchoMode(QLineEdit.EchoMode.Password)
            key.setPlaceholderText("Leave blank to keep the saved key")
            key.setMaxLength(8192)
            model = QLineEdit()
            model.setMaxLength(256)
            model.setPlaceholderText("Optional model override")
            session = QCheckBox("Store a new/replacement key for this session only")
            remove = QCheckBox("Remove saved key on Save")
            status = QLabel()
            test = QPushButton("Test connection")
            test.setEnabled(False)
            test.setToolTip("Connection testing is not available yet.")
            link = QLabel(f'<a href="{url}">Manage or revoke {name} keys</a>')
            link.setOpenExternalLinks(True)
            form.addRow("API key", key)
            form.addRow("Model", model)
            form.addRow(status)
            form.addRow(session)
            form.addRow(remove)
            form.addRow(test)
            form.addRow(link)
            providers_layout.addWidget(box)
            self.rows[provider] = dict(key=key, model=model, session=session, remove=remove, status=status, test=test)
        scroll.setWidget(content)
        layout.addWidget(scroll)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        remove_all = QPushButton("Remove all keys — every profile")
        remove_all.clicked.connect(self.remove_all)
        layout.addWidget(remove_all)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.reload()

    def reload(self):
        state = self.controller.summary()
        for provider, row in self.rows.items():
            config = state["providers"].get(provider, {})
            row["model"].setText(config.get("modelId", ""))
            row["remove"].setChecked(False)
            row["session"].setChecked(config.get("storage") == "session")
            status = "No available key"
            if config.get("available"):
                storage = "session only" if config["storage"] == "session" else "OS protected storage"
                status = f"Key configured ({storage}); connection {config['connectionStatus']}"
            row["status"].setText(status)
        self.active.setCurrentIndex(max(0, self.active.findData(state["activeProviderId"])))

    def clear_keys(self):
        for row in self.rows.values():
            row["key"].clear()

    def save(self):
        changes = {provider: {"key": row["key"].text(), "modelId": row["model"].text(),
                              "sessionOnly": row["session"].isChecked(), "remove": row["remove"].isChecked()}
                   for provider, row in self.rows.items()}
        try:
            self.controller.save(changes, self.active.currentData())
        except CredentialError as error:
            self.message.setText(f"{error} Some changes may already be saved; check the status before retrying.")
        except Exception:
            self.message.setText("Settings could not be fully saved. Check the saved status and retry.")
        else:
            self.accept()
        finally:
            changes.clear()
            self.clear_keys()
            self.reload()
            self.on_changed()

    def remove_all(self):
        try:
            self.controller.remove_all()
        except CredentialError as error:
            self.message.setText(str(error))
        except Exception:
            self.message.setText("Key removal could not be confirmed. Retry before uninstalling.")
        else:
            self.message.setText("All keys removed from every saved profile. Setup is required.")
        finally:
            self.clear_keys()
            self.reload()
            self.on_changed()

    def done(self, result):
        self.clear_keys()
        super().done(result)
