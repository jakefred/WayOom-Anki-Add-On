from pathlib import Path
import json
from urllib.parse import quote
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
mw.addonManager.setWebExports(__name__, r"(?:web/dist/.*\.js|assets/WayOom Korean Brush Calligraphy App Icon\.png)")


def toolbar_link(links, toolbar) -> None:
    """Place the persistent WayOom toggle immediately after Anki's Sync link."""
    command = "wayoom-toggle"
    toolbar.link_handlers[command] = toggle_sidebar
    visible = sidebar is not None and not sidebar.isHidden() and runtime.store is not None
    enabled = runtime.store is not None
    addon_id = mw.addonManager.addonFromModule(__name__)
    icon = quote(f"/_addons/{addon_id}/assets/WayOom Korean Brush Calligraphy App Icon.png", safe="/")
    title = "Hide WayOom" if visible else "Show WayOom"
    inactive_style = "" if enabled else "opacity:.5;"
    # Crop only the image's empty margins. The visible brush lettering occupies
    # roughly x=50..1210, y=305..995 of the original 1254-square PNG.
    # Anki's links use em-sized text; this keeps the ink near that height.
    link = (
        f'<a class="hitem" id="wayoom-toggle" tabindex="-1" aria-label="WayOom" '
        f'aria-expanded="{str(visible).lower()}" aria-disabled="{str(not enabled).lower()}" '
        f'title="{title}" href="#" onclick="return pycmd(\'{command}\')" '
        f'style="vertical-align:middle;background:#fcf7e9;{inactive_style}">'
        f'<span style="display:inline-block;position:relative;overflow:hidden;'
        f'width:1.74em;height:1.05em;vertical-align:-.16em">'
        f'<img src="{icon}" alt="" aria-hidden="true" draggable="false" '
        f'style="position:absolute;width:1.881em;height:1.881em;left:-.075em;top:-.458em">'
        f'</span></a>'
    )
    # Anki calls this hook with fresh links on each draw. Guard in case another
    # hook invokes us twice against the same list.
    if not any('id="wayoom-toggle"' in item for item in links):
        sync_index = next((index for index, item in enumerate(links) if 'id="sync"' in item), len(links) - 1)
        links.insert(sync_index + 1, link)


def sync_toolbar_toggle() -> None:
    toolbar = getattr(mw, "toolbar", None)
    if toolbar is None:
        return
    visible = sidebar is not None and not sidebar.isHidden() and runtime.store is not None
    enabled = runtime.store is not None
    # The toolbar may be between draws; a missing node is harmless.
    toolbar.web.eval(
        "(() => { const link = document.getElementById('wayoom-toggle'); if (!link) return;"
        f"link.setAttribute('aria-expanded', {json.dumps(str(visible).lower())});"
        f"link.setAttribute('aria-disabled', {json.dumps(str(not enabled).lower())});"
        f"link.title = {json.dumps('Hide WayOom' if visible else 'Show WayOom')};"
        f"link.style.opacity = {json.dumps('' if enabled else '.5')};"
        "})()"
    )

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
    sync_toolbar_toggle()
    if runtime.configuration["setupRequired"]:
        show_settings(welcome=True)


def show_settings(welcome=False):
    global settings_dialog
    if runtime.store is None:
        return
    if settings_dialog is not None:
        settings_dialog.raise_()
        settings_dialog.activateWindow()
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

    def load_models(provider, callback):
        from .wayoom.catalog import ModelCatalog
        from .wayoom.proxy import Transfer
        guard()
        reference = store.settings().get("providers", {}).get(provider, {}).get("credentialRef")
        transfer = Transfer()
        catalog = ModelCatalog(runtime.credentials)
        def complete(future):
            if runtime.store is not store or runtime._epoch != epoch:
                return
            if store.settings().get("providers", {}).get(provider, {}).get("credentialRef") != reference:
                return
            try:
                result = future.result()
            except Exception:
                result = {"error": "provider"}
            if result.get("error") == "authentication":
                providers = store.settings()["providers"]
                providers[provider]["connectionStatus"] = "untested"
                providers[provider].pop("verifiedModelId", None)
                store.update_settings(providers=providers)
                changed()
            callback(result)
        mw.taskman.run_in_background(lambda: catalog.fetch(store, provider, reference, transfer),
                                     complete, uses_collection=False)
        return transfer.cancel

    dialog = SettingsDialog(mw, SettingsController(runtime.credentials, store, guard), changed,
                            test_connection=test_connection, load_models=load_models, welcome=welcome)
    settings_dialog = dialog
    bridge = provider_bridge
    def finished(_result):
        global settings_dialog
        if bridge:
            bridge.cancel_tests()
        dialog.clear_keys()
        dialog.deleteLater()
        if settings_dialog is dialog:
            settings_dialog = None
        changed()
    dialog.finished.connect(finished)
    # Avoid a nested event loop inside a webview bridge callback.
    dialog.open()


def refresh_status() -> None:
    if provider_bridge is not None:
        provider_bridge.poll()
    if sidebar is not None:
        if provider_bridge is not None and not provider_bridge.client_ready:
            # Anki queues eval until QWebChannel has installed pycmd (domDone).
            sidebar.widget().eval("window.wayoomConnect && window.wayoomConnect();")
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
    sync_toolbar_toggle()
    if visible:
        sidebar.widget().eval("window.wayoomPanel && window.wayoomPanel.refreshDecks();")
    else:
        mw.web.setFocus()
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
    sync_toolbar_toggle()
    if sidebar is None:
        return
    sidebar.widget().cleanup()
    mw.removeDockWidget(sidebar)
    sidebar.deleteLater()
    sidebar = None


menu_action.triggered.connect(toggle_sidebar)
gui_hooks.top_toolbar_did_init_links.append(toolbar_link)
status_timer.timeout.connect(refresh_status)
gui_hooks.profile_did_open.append(profile_opened)
gui_hooks.profile_will_close.append(close_sidebar)
