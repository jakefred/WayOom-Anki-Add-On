"""Optional offline real-Qt smoke test; never loads a personal Anki profile."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qt-path", type=Path, required=True, help="Separate aqt dependency directory")
    parser.add_argument("--output-dir", type=Path, help="Optional screenshots/evidence outside the disposable base")
    args = parser.parse_args(argv)
    qt_path = args.qt_path.resolve()
    sys.path[:0] = [str(qt_path), str(ROOT)]
    dll_handles = []
    if sys.platform == "win32":
        for name in ("win32", "win32/lib", "pywin32_system32"):
            path = qt_path / name
            if path.is_dir():
                sys.path.insert(0, str(path))
                dll_handles.append(os.add_dll_directory(str(path)))
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    # No --no-sandbox / --disable-web-security flags: browser security stays on.
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu"
    from aqt.qt import (QApplication, QCoreApplication, QFont, QFontDatabase, Qt, QLineEdit, QTimer, QWebEngineView,
                        QWebEnginePage, QWebEngineProfile, QUrl, QObject, pyqtSlot, QT_VERSION_STR, PYQT_VERSION_STR)
    from PyQt6.QtWebEngineCore import QWebEngineUrlRequestInterceptor
    from PyQt6.QtTest import QTest
    from PyQt6.QtWebChannel import QWebChannel
    from aqt.webview import _create_bridge_script
    from wayoom.bridge import Bridge
    from wayoom.credentials import CredentialService
    from wayoom.settings import SettingsController
    from wayoom.settings_dialog import SettingsDialog
    from wayoom.storage import ProfileStore

    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication(["WayOom-offscreen-smoke"])
    if sys.platform == "win32":
        # The offscreen platform does not discover Windows fonts automatically.
        font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/segoeui.ttf"
        QFontDatabase.addApplicationFont(str(font_path))
        app.setFont(QFont("Segoe UI", 10))
    app.setApplicationName("WayOom offscreen smoke")
    result = {"passed": False, "qt": QT_VERSION_STR, "pyqt": PYQT_VERSION_STR,
              "scope": "Native settings and Chromium form only; no Anki main-window/OS-store/live-provider certification"}
    callback_errors = []
    def callback_error(kind, error, tb):
        # aqt installs a debugger hook; assertions in Qt callbacks must fail the run.
        callback_errors.append(kind.__name__)
        traceback.print_exception(kind, error, tb)
        app.exit(1)
    sys.excepthook = callback_error
    root = ROOT / ".test-tmp"
    root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="qt-smoke-base-", dir=root) as temporary:
        base = Path(temporary)
        # This controller can only use a fake session key; OS access is forbidden.
        def forbidden(): raise AssertionError("No real credential store in Qt smoke")
        credentials = CredentialService(forbidden)
        store = ProfileStore(base / "user_files", "disposable-smoke")
        controller = SettingsController(credentials, store)
        def fake_catalog(provider, callback):
            assert provider == "openai"
            callback({"models": [{"id": "gpt-fixture", "name": "Example text model", "inputTokens": 128000},
                                 {"id": "gpt-other", "name": "Another text model"}]})
        def fake_test(provider, callback):
            providers = store.settings()["providers"]
            providers[provider].update(connectionStatus="verified", verifiedModelId=providers[provider]["modelId"])
            store.update_settings(providers=providers)
            callback("success")
        for dark in (False, True):
            dialog = SettingsDialog(None, controller, welcome=True, dark=dark,
                                    load_models=fake_catalog, test_connection=fake_test)
            dialog.open()
            app.processEvents()
            if args.output_dir:
                args.output_dir.mkdir(parents=True, exist_ok=True)
                dialog.grab().save(str(args.output_dir / f"welcome-{'dark' if dark else 'light'}.png"))
            dialog.setup_now.click()
            app.processEvents()
            if args.output_dir:
                dialog.grab().save(str(args.output_dir / f"setup-start-{'dark' if dark else 'light'}.png"))
            assert dialog.key.echoMode() == QLineEdit.EchoMode.Password
            dialog.key.setText("sk-proj-offscreen-fake-key")
            assert dialog.provider.currentData() == "openai"
            dialog.key.setFocus()
            QTest.keyClick(dialog.key, Qt.Key.Key_Return)
            assert dialog.isVisible() and not dialog._closed and not dialog.key.hasFocus()
            dialog.session.setChecked(True)
            dialog.connect_button.click()
            assert dialog.model.currentData() == "gpt-fixture" and not dialog.done_button.isEnabled()
            QTest.keyClick(dialog.model, Qt.Key.Key_Return)
            assert dialog.isVisible() and not dialog._closed
            dialog.test_button.click()
            assert dialog.done_button.isEnabled()
            app.processEvents()
            if args.output_dir:
                dialog.grab().save(str(args.output_dir / f"settings-{'dark' if dark else 'light'}.png"))
                dialog.model.showPopup()
                app.processEvents()
                dialog.model.view().grab().save(str(args.output_dir / f"models-{'dark' if dark else 'light'}.png"))
                dialog.model.hidePopup()
            dialog.done_button.click()
            assert dialog._closed and not dialog.key.text()
            dialog.deleteLater()
            reopened = SettingsDialog(None, controller, dark=dark, load_models=fake_catalog, test_connection=fake_test)
            reopened.open()
            app.processEvents()
            assert "Saved key" in reopened.key.placeholderText()
            reopened.key.setText("unsaved-key")
            reopened.reject()
            assert not reopened.key.text()
            reopened.deleteLater()
        result["nativeEnterVerificationReopenAndCancel"] = True

        class LocalOnly(QWebEngineUrlRequestInterceptor):
            def interceptRequest(self, info):
                url = info.requestUrl()
                allowed = url.scheme() in {"about", "data", "qrc"}
                if url.scheme() == "file":
                    allowed = Path(url.toLocalFile()).resolve().is_relative_to(base.resolve())
                if not allowed: info.block(True)

        profile = QWebEngineProfile(app)
        profile.setPersistentStoragePath(str(base / "web-storage"))
        profile.setCachePath(str(base / "web-cache"))
        interceptor = LocalOnly(profile)
        profile.setUrlRequestInterceptor(interceptor)
        view = QWebEngineView()
        page = QWebEnginePage(profile, view)
        view.setPage(page)
        view.resize(560, 1100)
        view.show()
        # Use Anki's actual asynchronous DocumentReady bridge, not an early pycmd stub.
        native_commands = []
        state = {"state": "ready", "message": "Ready", "configuredProvider": "openai", "profileReady": True,
                 "profileEpoch": 1, "configurationToken": "smoke", "canGenerate": True}
        runtime = SimpleNamespace(_epoch=1, store=store, profile="disposable-smoke", credentials=credentials,
                                  settings_guard=lambda: None, panel_state=lambda: state)
        def deliver(message):
            page.runJavaScript("window.wayoomBridge.receive(" + json.dumps(message) + ");")
        def fake_execute(_store, provider, reference, request, *_args):
            assert credentials.read(_store, provider, reference) == "sk-proj-offscreen-fake-key"
            response = {"id": "resp_fake", "object": "response", "created_at": 1, "model": "gpt-fixture",
                        "status": "completed", "incomplete_details": None,
                        "output": [{"type": "message", "role": "assistant", "id": "msg_fake", "status": "completed",
                                    "content": [{"type": "output_text", "text": '{"ok":true}', "annotations": []}]}],
                        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}
            return {"status": 200, "headers": {"content-type": "application/json"}, "body": json.dumps(response)}
        proxy = SimpleNamespace(executor=SimpleNamespace(submit=lambda work: work()), execute=fake_execute, close=lambda: None)
        bridge = Bridge(runtime, deliver, lambda work: work(), proxy=proxy)
        class NativeBridge(QObject):
            @pyqtSlot(str, result=str)
            def cmd(self, command):
                if command == "domDone":
                    page.runJavaScript("window.wayoomConnect && window.wayoomConnect();")
                elif command.startswith("wayoom:v1:rpc:"):
                    message = json.loads(command[len("wayoom:v1:rpc:"):])
                    if message["op"] == "destinations":
                        deliver({"v": 1, "id": message["id"], "ok": True,
                                 "result": {"decks": [{"deckId": 1, "name": "Biology::Cells"}]}})
                    else:
                        bridge.receive(json.dumps(message))
                else:
                    native_commands.append(command)
                return "null"
        native = NativeBridge()
        channel = QWebChannel(page)
        channel.registerObject("py", native)
        page.setWebChannel(channel)
        page.scripts().insert(_create_bridge_script())
        bundle = (ROOT / "web/dist/panel.js").read_text(encoding="utf-8")
        # Match Anki stdHtml: scripts appear BEFORE the form markup.
        html = "<!doctype html><html><body><script>" + bundle.replace("</script", "<\\/script") + "</script>" + (ROOT / "panel.html").read_text(encoding="utf-8") + "</body></html>"
        source = base / "panel.html"
        source.write_text(html, encoding="utf-8")

        def checked(value):
            try:
                evidence = json.loads(value)
                assert evidence == {"engine": "function", "topic": "Typing survives", "focus": "topic",
                                    "invalid": "true", "inert": "<img src=x onerror=alert(1)>",
                                    "settings": True}
                assert native_commands == ["wayoom:v1:settings"]
                result["chromiumFormValidationFocusAndInertState"] = True
                page.runJavaScript("""
document.documentElement.classList.remove('night-mode');
""", lambda _value: QTimer.singleShot(200, light_done))
            except Exception:
                app.exit(1)

        def light_done():
            if args.output_dir:
                args.output_dir.mkdir(parents=True, exist_ok=True)
                view.grab().save(str(args.output_dir / "panel-light.png"))
            page.runJavaScript("document.documentElement.classList.add('night-mode')",
                               lambda _value: QTimer.singleShot(200, dark_done))

        def dark_done():
            if args.output_dir: view.grab().save(str(args.output_dir / "panel-dark.png"))
            result["passed"] = True
            app.exit(0)

        def exercise():
            script = '''
const topic = document.querySelector('#topic');
topic.value = 'Typing survives';
document.querySelector('#destination').value = '1';
document.querySelector('#card-count').value = '0';
document.querySelector('#generation-form').requestSubmit();
document.querySelector('#settings').click();
topic.focus();
for(let i=0;i<20;i++) window.wayoomSetState(STATE);
window.wayoomSetState({...STATE,message:'<img src=x onerror=alert(1)>'});
JSON.stringify({engine:typeof window.wayoomGeneration.run,topic:topic.value,
  focus:document.activeElement.id,invalid:document.querySelector('#card-count').getAttribute('aria-invalid'),
  inert:document.querySelector('#message').textContent,settings:typeof pycmd === 'function'});
'''.replace("STATE", json.dumps(state))
            page.runJavaScript(script, checked)

        connected_dialog = None
        def test_complete(category):
            assert category == "success", category
            assert not credentials.summary(store)["setupRequired"]
            result["ankiQWebChannelAndSdkModelTest"] = True
            # Let SettingsDialog render the actual callback before checking it.
            QTimer.singleShot(50, finish_connected_test)

        def finish_connected_test():
            assert connected_dialog.done_button.isEnabled()
            assert "Test passed" in connected_dialog.message.text()
            assert connected_dialog.message.isVisible()
            if args.output_dir:
                connected_dialog.grab().save(str(args.output_dir / "connected-test-success.png"))
            connected_dialog.reject()
            QTimer.singleShot(300, exercise)

        def begin_connected_test():
            nonlocal connected_dialog
            if not bridge.client_ready:
                page.runJavaScript("window.wayoomConnect && window.wayoomConnect();")
                QTimer.singleShot(50, begin_connected_test)
                return
            # Reset previous fake verification: this test must really reach the SDK/bridge.
            providers = store.settings()["providers"]
            providers["openai"]["connectionStatus"] = "untested"
            providers["openai"].pop("verifiedModelId", None)
            store.update_settings(providers=providers)
            def connection(provider, callback):
                def complete(category):
                    callback(category)
                    test_complete(category)
                return bridge.begin_test(provider, complete)
            connected_dialog = SettingsDialog(None, controller, dark=True, load_models=fake_catalog,
                                               test_connection=connection)
            connected_dialog.open()
            assert not connected_dialog.done_button.isEnabled()
            QTest.mouseClick(connected_dialog.test_button, Qt.MouseButton.LeftButton)
            assert "Testing selected model" in connected_dialog.message.text()

        def loaded(ok):
            if not ok: app.exit(1); return
            QTimer.singleShot(50, begin_connected_test)

        page.loadFinished.connect(loaded)
        QTimer.singleShot(30000, lambda: app.exit(1))
        page.load(QUrl.fromLocalFile(str(source)))
        code = app.exec()
        result["passed"] = result["passed"] and not callback_errors
        bridge.close()
        view.close()
        page.deleteLater()
        view.deleteLater()
        app.processEvents()
        profile.deleteLater()
        app.processEvents()
        credentials.close_profile(store)
        if args.output_dir:
            (args.output_dir / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2))
        return code if result["passed"] else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"Offscreen smoke unavailable/failed ({type(error).__name__}). See TESTING.md; no real profile was loaded.", file=sys.stderr)
        sys.exit(1)
