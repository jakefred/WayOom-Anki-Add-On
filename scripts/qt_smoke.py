"""Optional offline real-Qt smoke test; never loads a personal Anki profile."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

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
    from aqt.qt import (QApplication, QCoreApplication, Qt, QLineEdit, QTimer, QWebEngineView,
                        QWebEnginePage, QWebEngineProfile, QUrl, QT_VERSION_STR, PYQT_VERSION_STR)
    from PyQt6.QtWebEngineCore import QWebEngineUrlRequestInterceptor
    from wayoom.credentials import CredentialService
    from wayoom.settings import SettingsController
    from wayoom.settings_dialog import SettingsDialog
    from wayoom.storage import ProfileStore

    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication(["WayOom-offscreen-smoke"])
    app.setApplicationName("WayOom offscreen smoke")
    result = {"passed": False, "qt": QT_VERSION_STR, "pyqt": PYQT_VERSION_STR,
              "scope": "Native settings and Chromium form only; no Anki main-window/OS-store/live-provider certification"}
    root = ROOT / ".test-tmp"
    root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="qt-smoke-base-", dir=root) as temporary:
        base = Path(temporary)
        # This controller can only use a fake session key; OS access is forbidden.
        def forbidden(): raise AssertionError("No real credential store in Qt smoke")
        credentials = CredentialService(forbidden)
        store = ProfileStore(base / "user_files", "disposable-smoke")
        dialog = SettingsDialog(None, SettingsController(credentials, store))
        assert all(row["key"].echoMode() == QLineEdit.EchoMode.Password for row in dialog.rows.values())
        dialog.rows["openai"]["key"].setText("offscreen-fake-key")
        dialog.reject()
        assert all(not row["key"].text() for row in dialog.rows.values())
        dialog.deleteLater()
        result["maskedNativeSettingsAndCancel"] = True

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
        stub = '''
window.pycmd = command => {
  const m = JSON.parse(command.slice('wayoom:v1:rpc:'.length));
  queueMicrotask(() => window.wayoomBridge.receive({v:1,id:m.id,ok:true,
    result:m.op==='destinations'?{decks:[{deckId:1,name:'Biology::Cells'}]}:{}}));
};
'''
        bundle = (ROOT / "web/dist/panel.js").read_text(encoding="utf-8")
        html = (ROOT / "panel.html").read_text(encoding="utf-8") + "<script>" + stub + bundle.replace("</script", "<\\/script") + "</script>"
        source = base / "panel.html"
        source.write_text(html, encoding="utf-8")
        state = {"state": "ready", "message": "Ready", "configuredProvider": "openai", "profileReady": True,
                 "profileEpoch": 1, "configurationToken": "smoke", "canGenerate": True}

        def checked(value):
            try:
                evidence = json.loads(value)
                assert evidence == {"engine": "function", "topic": "Typing survives", "focus": "topic",
                                    "invalid": "true", "inert": "<img src=x onerror=alert(1)>"}
                result["chromiumFormValidationFocusAndInertState"] = True
                page.runJavaScript("""
document.documentElement.style.colorScheme='light';
const style=document.createElement('style'); style.id='smoke-light';
style.textContent='body{color:#202124!important;background:#fff!important}.error{color:#b3261e!important}';
document.head.append(style);
""", lambda _value: QTimer.singleShot(200, light_done))
            except Exception:
                app.exit(1)

        def light_done():
            if args.output_dir:
                args.output_dir.mkdir(parents=True, exist_ok=True)
                view.grab().save(str(args.output_dir / "panel-light.png"))
            page.runJavaScript("document.querySelector('#smoke-light').remove(); document.documentElement.style.colorScheme='dark'; document.body.classList.add('nightMode')",
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
topic.focus();
for(let i=0;i<20;i++) window.wayoomSetState(STATE);
window.wayoomSetState({...STATE,message:'<img src=x onerror=alert(1)>'});
JSON.stringify({engine:typeof window.wayoomGeneration.run,topic:topic.value,
  focus:document.activeElement.id,invalid:document.querySelector('#card-count').getAttribute('aria-invalid'),
  inert:document.querySelector('#message').textContent});
'''.replace("STATE", json.dumps(state))
            page.runJavaScript(script, checked)

        def loaded(ok):
            if not ok: app.exit(1); return
            page.runJavaScript(f"window.wayoomSetState({json.dumps(state)});", lambda _value: QTimer.singleShot(300, exercise))

        page.loadFinished.connect(loaded)
        QTimer.singleShot(30000, lambda: app.exit(1))
        page.load(QUrl.fromLocalFile(str(source)))
        code = app.exec()
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
