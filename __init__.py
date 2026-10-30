from pathlib import Path
import json
# import the main window object (mw) from aqt
from aqt import gui_hooks, mw
from aqt.qt import QAction, QDockWidget, QTimer, Qt, QWidget
from aqt.webview import AnkiWebView
from .wayoom.runtime import ApplicationRuntime

sidebar = None
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
    runtime.open_profile(mw.pm.name, mw.col)
    add_sidebar()
    visible = runtime.store.settings().get("panelVisible", True)
    sidebar.setVisible(visible)
    refresh_status()
    status_timer.start()
    menu_action.setEnabled(True)
    menu_action.setChecked(visible)


def refresh_status() -> None:
    if sidebar is not None:
        state = json.dumps(runtime.panel_state(), ensure_ascii=True)
        sidebar.widget().eval(f"window.wayoomSetState && window.wayoomSetState({state});")


def panel_command(web, command):
    # This handler belongs only to the live WayOom webview. It exposes no
    # collection-write, credential, URL, or arbitrary search commands.
    if sidebar is None or sidebar.widget() is not web or runtime.store is None:
        return None
    if command == "wayoom:v1:view-cards" and runtime.outcome:
        runtime.view_cards(runtime.outcome["requestId"])
    elif command == "wayoom:v1:cancel":
        job = runtime.coordinator.snapshot(runtime.profile)
        if job and job["state"] in {"generating", "validating"}:
            runtime.coordinator.cancel(runtime.profile, job["request"]["requestId"])
            refresh_status()
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
    global sidebar
    runtime.close_profile()
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
