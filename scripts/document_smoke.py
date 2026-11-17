"""Optional local-only Qt document/OCR smoke; no personal profiles or providers."""
import argparse
from io import BytesIO
import json
import mimetypes
import os
from pathlib import Path
import sys
import tempfile
from threading import Thread
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit
from zipfile import ZipFile
import zlib

ROOT = Path(__file__).resolve().parents[1]


def pdf(objects):
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, value in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{number} 0 obj\n".encode() + value + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(output)


def stream(data, attributes=b""):
    return b"<< /Length " + str(len(data)).encode() + b" " + attributes + b" >>\nstream\n" + data + b"\nendstream"


def office(entries):
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        for name, text in entries.items():
            archive.writestr(name, text)
    return output.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qt-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--fixture-dir", type=Path, help="Optional manifest.json and multilingual OCR images")
    args = parser.parse_args()
    extra_cases = []
    if args.fixture_dir:
        fixture_dir = args.fixture_dir.resolve()
        extra_cases = json.loads((fixture_dir / "manifest.json").read_text(encoding="utf-8"))
        if not isinstance(extra_cases, list):
            raise ValueError("Fixture manifest must be a list")
        languages = {"eng", "spa", "fra", "deu", "por", "ita", "rus", "jpn", "kor",
                     "chi_sim", "chi_tra", "ara", "hin"}
        seen_files = set()
        for case in extra_cases:
            name = case["file"]
            if (Path(name).name != name or name in seen_files
                    or Path(name).suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp")):
                raise ValueError(f"Invalid fixture filename: {name}")
            seen_files.add(name)
            if case["language"] not in languages or not isinstance(case.get("alsoEnglish", False), bool):
                raise ValueError(f"Invalid fixture language: {name}")
            if (not isinstance(case["expected"], list) or not case["expected"]
                    or any(not isinstance(text, str) or not text.strip() for text in case["expected"])):
                raise ValueError(f"Fixture {name} needs expected text snippets")
    qt_path = args.qt_path.resolve()
    sys.path.insert(0, str(qt_path))
    handles = []
    if sys.platform == "win32":
        for name in ("win32", "win32/lib", "pywin32_system32"):
            path = qt_path / name
            if path.is_dir():
                sys.path.insert(0, str(path))
                handles.append(os.add_dll_directory(str(path)))
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu"
    from PyQt6.QtCore import QTimer, QUrl, QBuffer, QIODevice
    from PyQt6.QtGui import QFont, QFontDatabase, QImage, QPainter
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage, QWebEngineUrlRequestInterceptor

    app = QApplication(["WayOom-document-smoke"])
    if sys.platform == "win32":
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
        app.setFont(QFont("Segoe UI", 10))
    scan = QImage(800, 1000, QImage.Format.Format_RGB888)
    scan.fill(0xFFFFFF)
    painter = QPainter(scan)
    painter.setFont(QFont("Segoe UI", 22))
    painter.drawText(40, 150, "Mitochondria release energy for the cell.")
    painter.drawText(40, 220, "Cell membranes regulate transport.")
    painter.end()
    pixels = scan.bits().asstring(scan.sizeInBytes())
    def encode_image(image, format):
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, format, 95):
            raise RuntimeError(f"Qt could not create the {format} image fixture")
        return bytes(buffer.data())
    transparent = QImage(800, 1000, QImage.Format.Format_ARGB32)
    transparent.fill(0)
    painter = QPainter(transparent)
    painter.setFont(QFont("Segoe UI", 22))
    painter.drawText(40, 150, "Mitochondria release energy for the cell.")
    painter.drawText(40, 220, "Cell membranes regulate transport.")
    painter.end()
    png = encode_image(transparent, "PNG")
    image_relationship_type = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/image'
    image_doc = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body><w:p><w:r><w:t>Printed introduction.</w:t></w:r></w:p><w:p><w:drawing><a:blip r:embed="picture"/><a:blip r:embed="picture"/><a:blip r:link="external"/><a:blip r:embed="vector"/></w:drawing></w:p></w:body></w:document>'
    word_images = {
        'word/document.xml': image_doc,
        'word/_rels/document.xml.rels': f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="picture" Target="media/scan.png" Type="{image_relationship_type}"/><Relationship Id="external" Target="https://example.invalid/private.png" TargetMode="External" Type="{image_relationship_type}"/><Relationship Id="vector" Target="media/vector.svg" Type="{image_relationship_type}"/></Relationships>',
        'word/media/scan.png': png, 'word/media/vector.svg': b'<svg/>',
        'word/media/unused.png': b'not a valid image; must never be decoded',
    }
    fixtures = {
        "lecture.pdf": pdf([
            b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 7 0 R >> >> /Contents 4 0 R >>",
            stream(b"BT /F1 18 Tf 40 700 Td (First page: chloroplasts use sunlight.) Tj ET"),
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 7 0 R >> >> /Contents 6 0 R >>",
            stream(b"BT /F1 18 Tf 40 700 Td (Second page: membranes regulate transport.) Tj ET"),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        ]),
        "scan.pdf": pdf([
            b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /XObject << /Im0 5 0 R >> >> /Contents 4 0 R >>",
            stream(b"q 576 0 0 720 18 36 cm /Im0 Do Q"),
            stream(zlib.compress(pixels), b"/Type /XObject /Subtype /Image /Width 800 /Height 1000 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode"),
        ]),
        "lecture.docx": office({"word/document.xml": '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Cell biology lecture.</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Table fact: membrane transport.</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'}),
        "lecture.pptx": office({
            "ppt/presentation.xml": '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId id="1" r:id="rId2"/><p:sldId id="2" r:id="rId1"/></p:sldIdLst></p:presentation>',
            "ppt/_rels/presentation.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="slides/slide1.xml"/><Relationship Id="rId2" Target="slides/slide2.xml"/></Relationships>',
            "ppt/slides/slide1.xml": '<a:p xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:r><a:t>Excluded slide.</a:t></a:r></a:p>',
            "ppt/slides/slide2.xml": '<a:p xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:r><a:t>Selected slide: cell biology.</a:t></a:r></a:p>',
        }),
        "lecture.txt": b"Cell biology <img src=x onerror=alert(1)> stays literal.",
        "lecture.csv": b'concept,definition\n"Cell, membrane",Transport',
        "scan.png": png, "scan.jpg": encode_image(scan, "JPEG"), "scan.webp": encode_image(scan, "WEBP"),
        "illustrated.docx": office(word_images),
        "image-only.docx": office({**word_images, 'word/document.xml': image_doc.replace('Printed introduction.', '')}),
        "mixed.pdf": pdf([
            b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 6 0 R >> /XObject << /Im0 5 0 R >> >> /Contents 4 0 R >>",
            stream(b"BT /F1 18 Tf 40 760 Td (Printed introduction.) Tj ET q 576 0 0 680 18 36 cm /Im0 Do Q"),
            stream(zlib.compress(pixels), b"/Type /XObject /Subtype /Image /Width 800 /Height 1000 /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode"),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        ]),
    }
    for case in extra_cases:
        fixtures[case["file"]] = (fixture_dir / case["file"]).read_bytes()
    with ZipFile(BytesIO(fixtures['lecture.pptx'])) as presentation:
        slides = {name: presentation.read(name) for name in presentation.namelist()}
    for number in (1, 2):
        slides[f'ppt/slides/slide{number}.xml'] = f'<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><a:p><a:r><a:t>Slide {number} introduction.</a:t></a:r></a:p><a:blip r:embed="picture"/></p:sld>'
        slides[f'ppt/slides/_rels/slide{number}.xml.rels'] = f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="picture" Target="../media/{"scan" if number == 2 else "unused"}.png" Type="{image_relationship_type}"/></Relationships>'
    slides.update({'ppt/media/scan.png': png, 'ppt/media/unused.png': b'invalid image on unselected slide'})
    fixtures['illustrated.pptx'] = office(slides)
    state = {"profileEpoch": 1, "profileReady": True, "configuredProvider": "openai", "canGenerate": True, "state": "ready"}
    bootstrap = """window.pycmd = command => { const prefix='wayoom:v1:rpc:'; if (!command.startsWith(prefix)) return; const m=JSON.parse(command.slice(prefix.length)); if (!['ready','destinations','discard-retained'].includes(m.op)) throw new Error('Unexpected provider/job RPC: '+m.op); const result=m.op==='ready' ? {state:STATE} : m.op==='destinations' ? {decks:[{deckId:1,name:'Cells'}]} : {}; queueMicrotask(()=>window.wayoomBridge.receive({v:1,id:m.id,ok:true,result})); };""".replace("STATE", json.dumps(state))
    html = ("<!doctype html><meta charset=utf-8><script>" + bootstrap + "</script>" +
            (ROOT / "panel.html").read_text(encoding="utf-8").replace("__WAYOOM_LOGO_URL__", "/_addons/wayoom/assets/WayOom%20Korean%20Brush%20Calligraphy%20App%20Icon.png") +
            '<script src="/_addons/wayoom/web/dist/panel.js"></script>').encode()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            path = unquote(urlsplit(self.path).path)
            requests.append(path)
            content = None
            mime = "application/octet-stream"
            if path == "/smoke.html":
                content, mime = html, "text/html; charset=utf-8"
            elif path.startswith("/fixtures/"):
                content = fixtures.get(path.removeprefix("/fixtures/"))
            elif path == "/_addons/wayoom/assets/WayOom Korean Brush Calligraphy App Icon.png":
                content = (ROOT / "assets" / "WayOom Korean Brush Calligraphy App Icon.png").read_bytes()
                mime = "image/png"
            elif path.startswith("/_addons/wayoom/web/dist/"):
                target = (ROOT / "web/dist" / path.removeprefix("/_addons/wayoom/web/dist/")).resolve()
                if target.is_relative_to((ROOT / "web/dist").resolve()) and target.is_file():
                    content = target.read_bytes()
                    mime = {".js": "text/javascript", ".mjs": "text/javascript", ".wasm": "application/wasm"}.get(target.suffix, mimetypes.guess_type(target.name)[0] or mime)
            self.send_response(200 if content is not None else 404)
            self.send_header("Content-Type", mime)
            self.end_headers()
            self.wfile.write(content or b"Not found")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    blocked = []

    class LocalOnly(QWebEngineUrlRequestInterceptor):
        def interceptRequest(self, info):
            url = info.requestUrl()
            if url.scheme() in {"data", "blob"}:
                return
            if url.scheme() != "http" or url.host() != "127.0.0.1" or url.port() != server.server_port:
                blocked.append(url.toString())
                info.block(True)

    result = {"passed": False}
    with tempfile.TemporaryDirectory(prefix="document-smoke-", dir=ROOT / ".test-tmp") as temporary:
        profile = QWebEngineProfile()
        profile.setCachePath(temporary)
        profile.setPersistentStoragePath(temporary)
        interceptor = LocalOnly()
        profile.setUrlRequestInterceptor(interceptor)
        view = QWebEngineView()
        page = QWebEnginePage(profile, view)
        view.setPage(page)
        view.resize(500, 1250)
        script = r"""
        (async () => {
          const extraCases=EXTRA_CASES;
          const $=id=>document.getElementById(id), panel=window.wayoomPanel;
          const assert=(ok,message)=>{if(!ok)throw new Error(message)};
          const wait=async condition=>{const until=Date.now()+120000; while(!condition()){if(Date.now()>until)throw new Error('Document operation timed out'); await new Promise(r=>setTimeout(r,50));}};
          const attach=async name=>{const bytes=await(await fetch('/fixtures/'+name)).arrayBuffer(); const transfer=new DataTransfer(); transfer.items.add(new File([bytes],name)); $('document-file').files=transfer.files; $('document-file').dispatchEvent(new Event('change')); await wait(()=>!panel.documents.busy); assert(panel.documents.resource,$('document-error').textContent);};
          const read=async selection=>{$('document-pages').value=selection; $('document-pages').dispatchEvent(new Event('input')); $('document-read').click(); await wait(()=>!panel.documents.busy); assert(panel.documents.source,$('document-error').textContent);return panel.documents.source.text;};
          await wait(()=>panel.state.profileReady);
          await attach('lecture.pdf'); assert(panel.documents.resource.count===2,'PDF page count');
          assert($('generate').disabled,'Generate must wait for page selection');
          let text=await read('2'); assert(text.includes('Second page')&&!text.includes('First page'),'PDF selected page only');
          $('document-ocr').checked=true; $('document-ocr').dispatchEvent(new Event('change'));
          text=await read('2'); assert(/membranes/i.test(text)&&$('document-status').textContent.includes('English OCR'),'Forced OCR on a text page');
          $('document-pages').value='3'; $('document-pages').dispatchEvent(new Event('input')); $('document-read').click(); await wait(()=>!panel.documents.busy);
          assert(!panel.documents.source&&$('document-error').textContent,'Invalid range rejected');
          await attach('scan.pdf'); text=await read('1'); assert(/mitochondria/i.test(text)&&/transport/i.test(text),'English OCR: '+text);
          assert($('document-status').textContent.includes('English OCR'),'OCR status');
          await attach('lecture.docx'); assert(panel.documents.source.text.includes('Table fact'),'Word paragraphs and tables');
          await attach('lecture.pptx'); text=await read('1'); assert(text.includes('Selected slide')&&!text.includes('Excluded'),'Presentation order and slide selection');
          await attach('mixed.pdf'); text=await read('1'); assert(text.includes('Printed introduction')&&!/mitochondria/i.test(text),'PDF image OCR is opt-in when a text layer exists');
          $('document-ocr').checked=true; $('document-ocr').dispatchEvent(new Event('change'));
          text=await read('1'); assert(/mitochondria/i.test(text)&&/transport/i.test(text),'Mixed PDF image OCR');
          await attach('illustrated.docx'); assert(!/mitochondria/i.test(panel.documents.source.text),'Word image OCR is opt-in');
          assert(!$('document-ocr-option').hidden&&!$('document-local-hint').hidden,'Image scan checkbox and local token disclaimer');
          assert($('document-local-hint').textContent.includes('no API tokens'),'Local scanning disclaimer');
          $('document-ocr').checked=true; $('document-ocr').dispatchEvent(new Event('change'));
          assert(!panel.documents.source&&$('generate').disabled,'Changing OCR clears the previous source');
          text=await read(''); assert(/mitochondria/i.test(text)&&text.includes('Printed introduction'),'Word text plus image OCR');
          assert((text.match(/mitochondria/ig)||[]).length===1,'Repeated Word image is scanned/included once');
          assert($('document-status').textContent.includes('2 unsupported'),'Linked/vector images reported without fetching');
          await attach('image-only.docx'); assert(!panel.documents.source,'Image-only Word waits for opt-in');
          $('document-ocr').checked=true; $('document-ocr').dispatchEvent(new Event('change'));
          text=await read(''); assert(/mitochondria/i.test(text),'Image-only Word');
          await attach('illustrated.pptx'); text=await read('1'); assert(text.includes('Slide 2')&&!/mitochondria/i.test(text),'Slide image OCR is opt-in');
          $('document-ocr').checked=true; $('document-ocr').dispatchEvent(new Event('change'));
          text=await read('1'); assert(/mitochondria/i.test(text)&&text.includes('Slide 2'),'Selected slide image OCR only');
          for(const name of ['scan.png','scan.jpg','scan.webp']) {
            await attach(name); assert(!panel.documents.source,'Image waits for language selection'); text=await read('');
            assert(/mitochondria/i.test(text)&&/transport/i.test(text),'Real standalone image OCR: '+name+' '+text);
            assert($('document-ocr-option').hidden&&!$('document-local-hint').hidden,'Standalone images use local OCR');
          }
          for(const fixture of extraCases) {
            await attach(fixture.file);
            $('document-language').value=fixture.language;
            $('document-language').dispatchEvent(new Event('change'));
            $('document-english').checked=!!fixture.alsoEnglish;
            $('document-english').dispatchEvent(new Event('change'));
            text=await read('');
            const normalized=value=>value.normalize('NFC').replace(/\s+/gu,' ').trim();
            for(const expected of fixture.expected) assert(normalized(text).includes(normalized(expected)),
              `OCR fixture ${fixture.file} (${fixture.language}) missed ${expected}: ${text}`);
          }
          const imageBytes=await(await fetch('/fixtures/scan.png')).arrayBuffer();
          const pendingImage=panel.documents.attach(new File([imageBytes],'scan.png'));
          $('document-cancel').click(); await pendingImage;
          assert(!panel.documents.file&&!panel.documents.source,'Cancel image import ignores late results');
          await attach('lecture.txt'); assert($('document-preview').value.includes('<img'),'Inert text preview');
          assert($('document-ocr-option').hidden,'Text files have no embedded-image setting');
          await attach('lecture.csv'); assert(panel.documents.source.text.includes('"Cell, membrane",Transport'),'CSV structure');
          await attach('scan.pdf'); $('document-read').click(); $('document-cancel').click(); await new Promise(r=>setTimeout(r,300)); assert(!panel.documents.file&&!panel.documents.source,'Cancel clears source');
          await attach('illustrated.docx'); $('document-ocr').checked=true; $('document-ocr').dispatchEvent(new Event('change'));
          text=await read(''); $('topic').value='Focus on membrane transport.';
          await new Promise(r=>setTimeout(r,600));
          window.__documentSmoke={passed:true,checks:['PDF page selection','range errors','real English OCR','mixed PDF image OCR','DOCX images and tables','image-only DOCX','PPTX selected-image OCR','PNG transparency','JPEG OCR','WebP OCR','linked/unsupported images skipped','image cancellation','TXT inert preview','CSV structure','cancel'],source:panel.documents.source.selection};
        })().catch(error=>{window.__documentSmoke={passed:false,error:String(error)}});
        """.replace("EXTRA_CASES", json.dumps(extra_cases, ensure_ascii=False))
        started = False

        def loaded(ok):
            nonlocal started
            if ok and not started:
                started = True
                page.runJavaScript(script)

        def capture(value):
            if not value or value == "null":
                return
            poll.stop()
            result.update(json.loads(value))
            result["blockedExternalRequests"] = len(blocked)
            result["passed"] = result["passed"] and not blocked
            args.output_dir.mkdir(parents=True, exist_ok=True)
            view.grab().save(str(args.output_dir / "document-panel.png"))
            page.runJavaScript("document.documentElement.classList.add('night-mode')")
            def dark_capture():
                view.grab().save(str(args.output_dir / "document-panel-dark.png"))
                app.quit()
            QTimer.singleShot(500, dark_capture)

        page.loadFinished.connect(loaded)
        view.load(QUrl(origin + "/smoke.html"))
        view.show()
        poll = QTimer()
        poll.timeout.connect(lambda: page.runJavaScript("JSON.stringify(window.__documentSmoke||null)", capture))
        poll.start(250)
        QTimer.singleShot(180000, app.quit)
        app.exec()
        poll.stop()
        view.close()
        page.deleteLater()
        app.processEvents()
    server.shutdown()
    server.server_close()
    result["requestedLocalAssets"] = len(set(requests))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
