"""Test-only JSON-line peer; socket-free by default, opt-in loopback HTTP."""
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import socket
from pathlib import Path
import sys
import tempfile
from threading import Event, Lock, Thread
from types import SimpleNamespace
from unittest.mock import patch

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from wayoom.bridge import Bridge
from wayoom.credentials import CredentialService
from wayoom.proxy import ProviderProxy
from wayoom.runtime import ApplicationRuntime

FAKE_KEY = "fake-python-only-credential-123"
records = []
mode = {"delay": False, "error": None, "network": False}
engine_script = []
generated_count = 0
release = Event()
release.set()
write_lock = Lock()


def output(message):
    with write_lock:
        print(json.dumps(message), flush=True)


def fake_response(path, body, headers):
    model = body.get("model", "gemini-test")
    auth = [name.lower() for name, value in headers.items() if FAKE_KEY in value]
    records.append({"path": path, "model": model, "authNames": auth, "method": "POST"})
    if mode["delay"]:
        release.wait(3)
    if mode["error"]:
        status = mode["error"]
        data = json.dumps({"error": {"message": f"Provider echoed {FAKE_KEY}", "type": "fixture_error", "code": "fixture_error"}}).encode()
        return status, data
    text = '{"ok":true}'
    if mode.get("generation"):
        global generated_count
        def strings(value):
            if isinstance(value, str): return [value]
            if isinstance(value, list): return [text for item in value for text in strings(item)]
            if isinstance(value, dict): return [text for item in value.values() for text in strings(item)]
            return []
        prompt = "\n".join(strings(body))
        naming = 'Return only {"deckName"' in prompt
        requested = re.search(r"Return exactly (\d+) new cards", prompt)
        count = int(requested[1]) if requested else 0
        records[-1].update(stage="naming" if naming else "cards", count=count)
        step = engine_script.pop(0) if engine_script else {"auto": True}
        if isinstance(step, dict) and "httpError" in step:
            return step["httpError"], b'{"error":{"message":"Fixture failure","type":"server_error","code":"server_error"}}'
        if isinstance(step, dict) and "rawText" in step:
            text = step["rawText"]
        elif step == {"auto": True}:
            if naming:
                text = json.dumps({"deckName": "Biology::Cells"})
            else:
                cards = []
                for _ in range(count):
                    generated_count += 1
                    cards.append({"front": f"Engine question {generated_count}", "back": "A concise answer"})
                text = json.dumps({"cards": cards})
        else:
            text = json.dumps(step)
    if path.endswith("/responses"):
        response = {"id": "resp_fake", "object": "response", "created_at": 1, "model": model,
                    "status": "completed", "incomplete_details": None,
                    "output": [{"type": "message", "role": "assistant", "id": "msg_fake", "status": "completed",
                                "content": [{"type": "output_text", "text": text, "annotations": []}]}],
                    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}
    elif path.endswith("/messages"):
        response = {"id": "msg_fake", "type": "message", "role": "assistant", "model": model,
                    "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None,
                    "usage": {"input_tokens": 1, "output_tokens": 1}}
    elif path.endswith(":generateContent"):
        response = {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP", "index": 0}],
                    "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2}}
    else:
        response = {"id": "chat_fake", "object": "chat.completion", "created": 1, "model": model,
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
    data = json.dumps(response).encode()
    return 200, data


class MemoryConnection:
    sock = None
    def connect(self): pass
    def close(self): pass
    def request(self, method, path, body, headers): self.path, self.body, self.headers = path, body, headers
    def getresponse(self):
        status, data = fake_response(self.path, json.loads(self.body), self.headers)
        return type("Response", (), {"status": status, "getheader": lambda self, name, default=None: default,
                                     "read": lambda self, size: data[:size]})()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        status, data = fake_response(self.path, body, self.headers)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


temporary_root = Path(__file__).resolve().parents[2] / ".test-tmp"
temporary_root.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(prefix="anki-test-engine-" if "--generation" in sys.argv else "bridge-peer-", dir=temporary_root) as directory:
    server = None
    if "--loopback" in sys.argv:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
    class NoSocket(socket.socket):
        def __new__(cls, *args, **kwargs):
            raise AssertionError("Default SDK spike must remain socket-free")
    socket_guard = patch("socket.socket", NoSocket) if server is None else None
    if socket_guard:
        socket_guard.start()
    credentials = CredentialService(lambda: (_ for _ in ()).throw(AssertionError("No real OS store")))
    runtime = ApplicationRuntime(Path(directory), object(), credentials=credentials)
    collection = None
    if "--generation" in sys.argv:
        from anki.collection import Collection
        collection = Collection(str(Path(directory) / "collection.anki2"))
        class Operation:
            def __init__(self, parent, op, success=None): self.op, self.completed, self.failed = op, success, None
            def success(self, callback): self.completed = callback; return self
            def failure(self, callback): self.failed = callback; return self
            def run_in_background(self):
                try: result = self.op(collection)
                except Exception as error:
                    if self.failed: self.failed(error)
                    else: raise
                else:
                    if self.completed: self.completed(result)
        gui_operations = patch.dict("sys.modules", {"aqt.operations": SimpleNamespace(CollectionOp=Operation, QueryOp=Operation)})
        gui_operations.start()
    with patch("wayoom.runtime.recover_in_background") as recovery:
        runtime.open_profile("fixture", collection if collection is not None else object())
        recovery.call_args.kwargs["on_complete"]({})
    # Production URL allowlisting still runs; only this test's connection factory
    # maps allowed HTTPS hosts to the disposable HTTP loopback server.
    def connection_factory(host, timeout):
        if mode["network"]:
            raise OSError(f"Fake network failure containing {FAKE_KEY}")
        return HTTPConnection("127.0.0.1", server.server_port, timeout=timeout) if server else MemoryConnection()
    proxy = ProviderProxy(credentials, connection_factory=connection_factory)
    bridge = Bridge(runtime, output, lambda callback: callback(), proxy=proxy)
    for line in sys.stdin:
        message = json.loads(line)
        operation = message.get("op")
        if operation == "fixture-engine-setup":
            provider = message["payload"]["provider"]
            credentials.save_key(runtime.store, provider, FAKE_KEY, session_only=True)
            credentials.select_provider(runtime.store, provider)
            engine_script[:] = message["payload"].get("script", [])
            mode.update(generation=True)
            destination = message["payload"].get("destination", {"mode": "new", "name": None})
            if destination == {"mode": "existing"}:
                destination = {"mode": "existing", "deckId": collection.decks.id("Engine existing deck")}
            request = {"requestId": "ignored", "destination": destination,
                       "cardCount": message["payload"].get("cardCount", 100), "topic": "Biology",
                       "difficulty": "intermediate", "providerId": provider}
            bridge.reply(message["id"], {"request": request, "beforeCount": collection.card_count(), "records": len(records)})
        elif operation == "fixture-engine-script":
            engine_script[:] = message["payload"]["script"]
            bridge.reply(message["id"], {})
        elif operation == "fixture-engine-stats":
            bridge.reply(message["id"], {"count": collection.card_count(), "decks": [deck.name for deck in collection.decks.all_names_and_ids()],
                                        "records": records, "job": runtime.coordinator.snapshot(runtime.profile)})
        elif operation == "fixture-start":
            job = runtime.coordinator.snapshot(runtime.profile)
            if job and job["state"] in {"generating", "validating"}:
                runtime.coordinator.fail(runtime.profile, job["request"]["requestId"])
            provider = message["payload"]["provider"]
            model = {"openai": "gpt-test", "anthropic": "claude-test", "google": "gemini-test", "xai": "grok-test", "mistral": "mistral-test"}[provider]
            credentials.save_key(runtime.store, provider, FAKE_KEY, session_only=True, model_id=model)
            credentials.select_provider(runtime.store, provider)
            request = {"requestId": "ignored", "destination": {"mode": "existing", "deckId": 1}, "cardCount": 1,
                       "topic": "Fixture", "difficulty": "beginner", "providerId": provider}
            bridge.reply(message["id"], runtime.start_configured_job(request))
        elif operation == "fixture-test":
            job = runtime.coordinator.snapshot(runtime.profile)
            if job and job["state"] in {"generating", "validating"}:
                runtime.coordinator.fail(runtime.profile, job["request"]["requestId"])
            owner = bridge.begin_test(message["payload"]["provider"],
                                      lambda category: output({"v": 1, "event": "fixture-native-result", "category": category}))
            bridge.reply(message["id"], {"owner": owner})
        elif operation == "fixture-stats":
            bridge.reply(message["id"], {"records": records, "settings": runtime.store.settings()})
        elif operation == "fixture-mode":
            mode.update({"delay": False, "error": None, "network": False, **message["payload"]})
            release.clear() if mode["delay"] else release.set()
            bridge.reply(message["id"], {})
        else:
            bridge.receive(line)
    bridge.close()
    runtime.close_profile()
    if collection is not None:
        collection.close()
        gui_operations.stop()
    if server:
        server.shutdown()
        server.server_close()
    if socket_guard:
        socket_guard.stop()
