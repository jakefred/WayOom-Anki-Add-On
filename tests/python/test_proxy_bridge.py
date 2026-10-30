"""Proxy and authorization faults with fake HTTP connections and fake secrets."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from threading import Event, get_ident
import unittest
from unittest.mock import Mock, patch

from wayoom.bridge import Bridge
from wayoom.credentials import CredentialService
from wayoom.jobs import JobCoordinator
from wayoom.proxy import MAX_REQUEST, MAX_RESPONSE, PLACEHOLDER, ProviderProxy, ProxyError, validate_http
from wayoom.runtime import ApplicationRuntime

SECRET = "fake-secret-only-in-python"


class ManualExecutor:
    def __init__(self): self.work = []
    def submit(self, work): self.work.append(work)
    def run(self): self.work.pop(0)()
    def shutdown(self, **kwargs): pass


class Response:
    def __init__(self, status=200, body=b'{"ok":true}', headers=None):
        self.status, self.body, self.headers = status, body, headers or {}
    def getheader(self, name, default=None): return self.headers.get(name, default)
    def read(self, amount): return self.body[:amount]


class Connection:
    sock = None
    def __init__(self, response=None):
        self.response = response or Response()
        self.sent = []
        self.closed = False
    def connect(self): self.thread = get_ident()
    def request(self, method, path, body, headers): self.sent.append((method, path, body, headers))
    def getresponse(self): return self.response
    def close(self): self.closed = True


class ProxyBridgeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.now = 0
        self.credentials = CredentialService(lambda: self.fail("No real credential store"))
        self.runtime = ApplicationRuntime(Path(directory.name), object(), credentials=self.credentials)
        self.runtime.coordinator = JobCoordinator(clock=lambda: self.now)
        with patch("wayoom.runtime.recover_in_background") as recovery:
            self.runtime.open_profile("a", object())
            recovery.call_args.kwargs["on_complete"]({})
        self.reference = self.credentials.save_key(self.runtime.store, "openai", SECRET, session_only=True, model_id="fixture")
        self.request = {"requestId": "ignored", "destination": {"mode": "existing", "deckId": 1},
                        "cardCount": 1, "topic": "Fixture", "difficulty": "beginner", "providerId": "openai"}
        self.job = self.runtime.start_job(self.request, {"modelId": "fixture", "credentialRef": self.reference})
        self.executor = ManualExecutor()
        self.connections = []
        self.response = Response()
        def factory(host, timeout):
            connection = Connection(self.response)
            self.connections.append(connection)
            return connection
        self.proxy = ProviderProxy(self.credentials, factory, self.executor)
        self.messages = []
        self.bridge = Bridge(self.runtime, self.messages.append, lambda callback: callback(), proxy=self.proxy, clock=lambda: self.now)
        self.addCleanup(self.bridge.close)
        self.counter = 0

    def http(self, **patches):
        return {"url": "https://api.openai.com/v1/responses", "method": "POST",
                "headers": {"authorization": f"Bearer {PLACEHOLDER}", "content-type": "application/json"},
                "body": '{"model":"fixture"}', **patches}

    def rpc(self, op, payload):
        self.counter += 1
        rid = f"unit-{self.counter}"
        self.bridge.receive(json.dumps({"v": 1, "id": rid, "op": op, "payload": payload}))
        return rid

    def fetch(self, **patches):
        return self.rpc("fetch", {"owner": self.job["request"]["requestId"], "kind": "job", "stage": "cards",
                                  "batch": 0, "request": self.http(), **patches})

    def response_for(self, rid):
        return next(message for message in self.messages if message.get("id") == rid)

    def test_https_host_path_and_header_allowlist_rejects_before_transport(self):
        for url in ("http://api.openai.com/v1/responses", "https://api.openai.com.evil/v1/responses",
                    "https://api.openai.com/v1/responses?key=x", "https://api.openai.com/v1/responses#x",
                    "https://user@api.openai.com/v1/responses", "https://api.openai.com:443/v1/responses",
                    "https://api.openai.com/v1/../responses", "https://api.anthropic.com/v1/messages"):
            with self.subTest(url=url), self.assertRaises(ProxyError):
                validate_http("openai", self.http(url=url))
        for headers in ({"authorization": "No placeholder"}, {"host": "evil", "authorization": PLACEHOLDER},
                        {"authorization": PLACEHOLDER + "\r\nHost: evil"}):
            rid = self.fetch(request=self.http(headers=headers))
            self.assertFalse(self.response_for(rid)["ok"])
        self.assertEqual(self.connections, [])
        self.assertEqual(self.runtime.coordinator.snapshot("a")["callsUsed"], 0)

    def test_injects_key_only_on_worker_and_scrubs_echoed_key_and_response_headers(self):
        self.response = Response(401, json.dumps({"error": SECRET}).encode(), {"X-Echo": SECRET})
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            rid = self.fetch()
            self.assertEqual(self.connections, [])
            self.executor.run()
        response = self.response_for(rid)
        self.assertEqual(response["result"]["status"], 401)
        self.assertIn("[redacted]", response["result"]["body"])
        self.assertNotIn(SECRET, json.dumps(self.messages) + output.getvalue())
        self.assertEqual(self.connections[0].sent[0][3]["authorization"], f"Bearer {SECRET}")
        self.assertEqual(response["result"]["headers"], {"content-type": "application/json"})

    def test_every_failed_transport_consumes_attempt_and_fourth_call_never_runs(self):
        with patch.object(Connection, "getresponse", side_effect=RuntimeError(SECRET)):
            for _ in range(3):
                rid = self.fetch()
                self.executor.run()
                self.assertEqual(self.response_for(rid)["error"]["category"], "network")
        rid = self.fetch()
        self.assertFalse(self.response_for(rid)["ok"])
        self.assertEqual(len(self.connections), 3)
        self.assertEqual(self.runtime.coordinator.snapshot("a")["callsUsed"], 3)
        self.assertNotIn(SECRET, json.dumps(self.messages))

    def test_unicode_escaped_echo_cannot_reconstruct_secret_in_sdk_json(self):
        escaped = "".join(f"\\u{ord(char):04X}" for char in SECRET)
        self.response = Response(401, ('{"error":{"message":"' + escaped + '"}}').encode())
        rid = self.fetch()
        self.executor.run()
        body = self.response_for(rid)["result"]["body"]
        self.assertNotIn(SECRET, json.dumps(json.loads(body)))
        self.assertEqual(json.loads(body)["error"]["message"], "[redacted]")

    def test_nested_json_output_is_scrubbed_before_sdk_parses_the_inner_object(self):
        escaped = "".join(f"\\u{ord(char):04X}" for char in SECRET)
        inner = '{"front":"' + escaped + '"}'
        self.response = Response(200, json.dumps({"text": inner}).encode())
        rid = self.fetch()
        self.executor.run()
        envelope = json.loads(self.response_for(rid)["result"]["body"])
        self.assertEqual(json.loads(envelope["text"])["front"], "[redacted]")

    def test_redirect_and_oversized_compressed_and_unbounded_bodies_are_refused(self):
        for response, category in ((Response(302, headers={"Location": "https://evil.invalid"}), "redirect"),
                                   (Response(headers={"Content-Length": str(MAX_RESPONSE + 1)}), "oversized"),
                                   (Response(body=b"x" * (MAX_RESPONSE + 1)), "oversized")):
            self.response = response
            rid = self.fetch()
            self.executor.run()
            self.assertEqual(self.response_for(rid)["error"]["category"], category)
        self.assertEqual(len(self.connections), 3)
        rid = self.fetch(request=self.http(body="x" * (MAX_REQUEST + 1)))
        self.assertEqual(self.response_for(rid)["error"]["category"], "oversized")

    def test_compressed_response_is_rejected_without_decompression(self):
        self.response = Response(headers={"Content-Encoding": "gzip"})
        rid = self.fetch()
        self.executor.run()
        self.assertEqual(self.response_for(rid)["error"]["category"], "refused")

    def test_cancellation_timeout_and_late_response_cannot_resolve_twice(self):
        rid = self.fetch()
        self.runtime.coordinator.cancel("a", self.job["request"]["requestId"])
        self.assertEqual(self.response_for(rid)["error"]["category"], "canceled")
        self.executor.run()
        self.assertEqual(len([item for item in self.messages if item.get("id") == rid]), 1)
        self.assertEqual(self.connections, [])
        self.job = self.runtime.start_job(self.request, {"modelId": "fixture", "credentialRef": self.reference})
        rid = self.fetch()
        self.now = 601
        self.bridge.poll()
        self.assertEqual(self.response_for(rid)["error"]["category"], "timeout")
        self.assertTrue(any(item.get("event") == "owner-ended" for item in self.messages))
        self.executor.run()
        self.assertEqual(len([item for item in self.messages if item.get("id") == rid]), 1)

    def test_unknown_job_wrong_batch_and_stale_profile_cannot_send(self):
        for patches in ({"owner": "unknown"}, {"batch": 1}, {"kind": "test"}):
            rid = self.fetch(**patches)
            self.assertFalse(self.response_for(rid)["ok"])
        rid = self.fetch()
        self.bridge.close()
        self.runtime.close_profile()
        self.executor.run()
        self.assertEqual(self.connections, [])
        self.assertEqual(self.response_for(rid)["error"]["category"], "canceled")

    def test_connection_permit_is_native_only_single_use_and_verified_only_after_http_success(self):
        self.runtime.coordinator.fail("a", self.job["request"]["requestId"])
        self.rpc("ready", {})
        completed = []
        owner = self.bridge.begin_test("openai", completed.append)
        rid = self.rpc("test-result", {"owner": owner, "category": "success"})
        self.assertFalse(self.response_for(rid)["ok"])
        payload = {"owner": owner, "kind": "test", "stage": "test", "batch": 0, "request": self.http()}
        rid = self.rpc("fetch", payload)
        self.executor.run()
        self.assertTrue(self.response_for(rid)["ok"])
        again = self.rpc("fetch", payload)
        self.assertFalse(self.response_for(again)["ok"])
        self.rpc("test-result", {"owner": owner, "category": "success"})
        self.assertEqual(completed, ["success"])
        self.assertEqual(self.runtime.store.settings()["providers"]["openai"]["connectionStatus"], "verified")
        self.assertEqual(len(self.connections), 1)
        owner = self.bridge.begin_test("openai", completed.append)
        self.rpc("test-result", {"owner": owner, "category": "network"})
        self.assertEqual(self.runtime.store.settings()["providers"]["openai"]["connectionStatus"], "verified")

    def test_connection_test_expires_and_cannot_be_replayed(self):
        self.runtime.coordinator.fail("a", self.job["request"]["requestId"])
        self.rpc("ready", {})
        completed = []
        owner = self.bridge.begin_test("openai", completed.append)
        self.now = 31
        self.bridge.poll()
        self.assertEqual(completed, ["timeout"])
        rid = self.rpc("fetch", {"owner": owner, "kind": "test", "stage": "test", "batch": 0, "request": self.http()})
        self.assertFalse(self.response_for(rid)["ok"])
        self.assertEqual(self.connections, [])

    def test_worker_runs_off_main_and_delivers_only_through_dispatch(self):
        queue, done = [], Event()
        def dispatch(callback):
            queue.append(callback)
            done.set()
        executor = ThreadPoolExecutor(max_workers=1)
        self.proxy.executor = executor
        self.bridge.dispatch = dispatch
        rid = self.fetch()
        self.assertTrue(done.wait(2))
        self.assertNotEqual(self.connections[0].thread, get_ident())
        self.assertFalse(any(message.get("id") == rid for message in self.messages))
        queue.pop(0)()
        self.assertTrue(self.response_for(rid)["ok"])
        executor.shutdown(wait=True)

    def test_cancel_during_blocked_http_closes_connection_and_discards_late_success(self):
        entered, release = Event(), Event()
        original = Connection.getresponse
        def blocked(connection):
            entered.set()
            release.wait(2)
            return original(connection)
        executor = ThreadPoolExecutor(max_workers=1)
        self.proxy.executor = executor
        try:
            with patch.object(Connection, "getresponse", blocked):
                rid = self.fetch()
                self.assertTrue(entered.wait(2))
                self.runtime.coordinator.cancel("a", self.job["request"]["requestId"])
                self.assertTrue(self.connections[0].closed)
                self.assertEqual(self.response_for(rid)["error"]["category"], "canceled")
                release.set()
                executor.shutdown(wait=True)
            self.assertEqual(len([item for item in self.messages if item.get("id") == rid]), 1)
        finally:
            release.set()
            executor.shutdown(wait=True)

    def test_protocol_version_replays_and_missing_placeholders_cannot_create_calls(self):
        self.bridge.receive(json.dumps({"v": 2, "id": "bad-version", "op": "fetch", "payload": {}}))
        self.assertEqual(self.connections, [])
        message = {"v": 1, "id": "replay", "op": "fetch", "payload": {
            "owner": self.job["request"]["requestId"], "kind": "job", "stage": "cards", "batch": 0, "request": self.http()}}
        self.bridge.receive(json.dumps(message))
        self.bridge.receive(json.dumps(message))
        self.executor.run()
        self.bridge.receive(json.dumps(message))
        self.assertEqual(len(self.connections), 1)
        self.assertEqual(self.runtime.coordinator.snapshot("a")["callsUsed"], 1)

    def test_cancel_after_headers_shuts_down_original_socket_even_if_connection_detaches_it(self):
        from wayoom.proxy import Transfer
        connection = Connection()
        connection.sock = Mock()
        original_socket = connection.sock
        transfer = Transfer()
        transfer.attach(connection)
        transfer.connected()
        connection.sock = None  # http.client can detach a Connection: close response
        transfer.cancel()
        original_socket.shutdown.assert_called_once()
        self.assertTrue(connection.closed)

    def test_commit_uses_only_python_snapshot_and_rejects_caller_request(self):
        rid = self.rpc("commit", {"requestId": self.job["request"]["requestId"], "request": self.request, "result": {}})
        self.assertFalse(self.response_for(rid)["ok"])
        with patch.object(self.runtime, "add_cards") as add:
            self.rpc("commit", {"requestId": self.job["request"]["requestId"], "result": {}})
            self.assertEqual(add.call_args.args[0], self.job["request"])
        with patch.object(self.runtime, "add_cards") as add:
            rid = self.rpc("commit", {"requestId": "stale", "result": {}})
            add.assert_not_called()
            self.assertFalse(self.response_for(rid)["ok"])
