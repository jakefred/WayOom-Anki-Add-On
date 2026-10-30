from pathlib import Path
import json
from threading import current_thread, main_thread
# import the main window object (mw) from aqt
from aqt import gui_hooks, mw
from aqt.qt import QAction, QDockWidget, QTimer, Qt, QWidget
from aqt.webview import AnkiWebView
from .wayoom.runtime import ApplicationRuntime
from .wayoom.validation import ValidationError
from .wayoom.credentials import CredentialError
from .wayoom.bridge import Bridge

sidebar = None
settings_dialog = None
provider_bridge = None
runtime = ApplicationRuntime(Path(__file__).with_name("user_files"), mw)
status_timer = QTimer(mw)
status_timer.setInterval(500)
menu_action = QAction("WayOom", mw)
menu_action.setCheckable(True)
menu_action.setEnabled(False)
mw.form.menuTools.addAction(menu_action)
mw.addonManager.setWebExports(__name__, r"web/dist/.*\.js")

# Add the HTML sidebar to Anki's main window.
def add_sidebar() -> None:
    global sidebar
    if sidebar is not None:
        return
    sidebar = QDockWidget("WayOom", mw)
    sidebar.setObjectName("WayOomSidebar")
    sidebar.setFeatures(
        QDockWidget.DockWidgetFeature.NoDockWidgetFeatures
    )

    sidebar.setTitleBarWidget(QWidget())
    sidebar.setMinimumWidth(500)

    web = AnkiWebView(sidebar)
    sidebar.setWidget(web)
    web.set_bridge_command(lambda command: panel_command(web, command), web)

    html = Path(__file__).with_name("panel.html").read_text(
        encoding="utf-8"
    )
    addon_id = mw.addonManager.addonFromModule(__name__)
    web.stdHtml(html, js=[f"/_addons/{addon_id}/web/dist/panel.js"])

    mw.addDockWidget(
        Qt.DockWidgetArea.LeftDockWidgetArea, sidebar
    )


def profile_opened() -> None:
    global provider_bridge
    runtime.open_profile(mw.pm.name, mw.col)
    add_sidebar()
    if provider_bridge is not None:
        provider_bridge.close()
    web, epoch = sidebar.widget(), runtime._epoch

    def dispatch(callback):
        if current_thread() is main_thread():
            callback()
        else:
            mw.taskman.run_on_main(callback)

    def deliver(message):
        if sidebar is not None and sidebar.widget() is web and runtime._epoch == epoch:
            encoded = json.dumps(message, ensure_ascii=True)
            web.eval(f"window.wayoomBridge && window.wayoomBridge.receive({encoded});")

    provider_bridge = Bridge(runtime, deliver, dispatch)
    visible = runtime.store.settings().get("panelVisible", True)
    sidebar.setVisible(visible)
    refresh_status()
    status_timer.start()
    menu_action.setEnabled(True)
    menu_action.setChecked(visible)
    if not runtime.store.settings().get("setupComplete", False):
        show_settings()


def show_settings():
    global settings_dialog
    if runtime.store is None or settings_dialog is not None:
        return
    from .wayoom.settings import SettingsController
    from .wayoom.settings_dialog import SettingsDialog
    store = runtime.store
    epoch = runtime._epoch

    def guard():
        if runtime.store is not store or runtime._epoch != epoch:
            raise ValidationError("The active profile changed. Reopen settings.")
        runtime.settings_guard()
        if provider_bridge and provider_bridge.testing:
            raise CredentialError("Wait for the connection test to finish before changing settings")

    def changed():
        if runtime.store is store and runtime._epoch == epoch:
            runtime.refresh_configuration()
            refresh_status()

    def test_connection(provider, callback):
        guard()
        if provider_bridge is None:
            raise CredentialError("The panel is still loading. Try again shortly.")
        return provider_bridge.begin_test(provider, callback)

    dialog = SettingsDialog(mw, SettingsController(runtime.credentials, store, guard), changed,
                            test_connection=test_connection)
    settings_dialog = dialog
    try:
        dialog.exec()
    finally:
        if provider_bridge:
            provider_bridge.cancel_tests()
        dialog.clear_keys()
        dialog.deleteLater()
        settings_dialog = None
        changed()


def refresh_status() -> None:
    if provider_bridge is not None:
        provider_bridge.poll()
    if sidebar is not None:
        state = json.dumps(runtime.panel_state(), ensure_ascii=True)
        sidebar.widget().eval(f"window.wayoomSetState && window.wayoomSetState({state});")


def panel_command(web, command):
    # This handler belongs only to the live WayOom webview. Repair is a guarded,
    # serialized operation; inspection searches use only saved job IDs.
    if sidebar is None or sidebar.widget() is not web or runtime.store is None:
        return None
    if command.startswith("wayoom:v1:rpc:"):
        if provider_bridge:
            provider_bridge.receive(command[len("wayoom:v1:rpc:"):])
    elif command == "wayoom:v1:settings":
        show_settings()
    elif command == "wayoom:v1:view-cards" and runtime.outcome:
        runtime.view_cards(runtime.outcome["requestId"])
    elif command == "wayoom:v1:cancel":
        job = runtime.coordinator.snapshot(runtime.profile)
        if job and job["state"] in {"generating", "validating"}:
            runtime.coordinator.cancel(runtime.profile, job["request"]["requestId"])
            refresh_status()
    elif command.startswith(("wayoom:v1:inspect:", "wayoom:v1:acknowledge:")):
        action, request_id = command[len("wayoom:v1:"):].split(":", 1)
        try:
            record = runtime.store.job(request_id)
            if record is not None and record["state"] == "uncertain":
                if action == "inspect":
                    runtime.view_cards(request_id)
                else:
                    runtime.acknowledge_job(request_id)
                refresh_status()
        except ValidationError:
            return None
    elif command == "wayoom:v1:repair-note-type":
        try:
            runtime.repair_note_type()
            refresh_status()
        except ValidationError:
            return None
    return None


def toggle_sidebar(_checked=False) -> None:
    if runtime.store is None:
        return
    add_sidebar()
    visible = sidebar.isHidden()
    runtime.store.update_settings(panelVisible=visible)
    sidebar.setVisible(visible)
    menu_action.setChecked(visible)
    refresh_status()

def close_sidebar() -> None:
    global sidebar, provider_bridge
    if provider_bridge is not None:
        provider_bridge.close()
        provider_bridge = None
    runtime.close_profile()
    if settings_dialog is not None:
        settings_dialog.reject()
    status_timer.stop()
    menu_action.setEnabled(False)
    menu_action.setChecked(False)
    if sidebar is None:
        return
    sidebar.widget().cleanup()
    mw.removeDockWidget(sidebar)
    sidebar.deleteLater()
    sidebar = None


menu_action.triggered.connect(toggle_sidebar)
status_timer.timeout.connect(refresh_status)
gui_hooks.profile_did_open.append(profile_opened)
gui_hooks.profile_will_close.append(close_sidebar)
