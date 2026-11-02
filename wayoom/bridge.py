"""Versioned, profile-bound RPC. All GUI delivery is dispatched to the main thread."""
from collections import deque
import json
import re
from threading import RLock
from time import monotonic
from uuid import uuid4

from .jobs import ConnectionTestPermit
from .proxy import MAX_REQUEST, ProviderProxy, ProxyError, Transfer, validate_http
from .validation import ValidationError
from .models import model_id

CATEGORIES = {"success", "authentication", "credentials", "model", "quota", "rate-limit", "timeout", "network",
              "refusal", "malformed", "refused", "canceled", "redirect", "oversized", "provider", "validation"}


class Bridge:
    def __init__(self, runtime, deliver, dispatch, proxy=None, clock=monotonic):
        self.runtime, self.deliver, self.dispatch = runtime, deliver, dispatch
        self.proxy = proxy if proxy is not None else ProviderProxy(runtime.credentials)
        self.clock = clock
        self.epoch = runtime._epoch
        self.store, self.profile = runtime.store, runtime.profile
        self._lock = RLock()
        self._seen = deque(maxlen=2048)
        self._replied = deque(maxlen=2048)
        self._pending = {}
        self._tests = {}
        self._closed = False
        self.client_ready = False
        self._ended = None
        self._start = None
        self._canceled_starts = deque(maxlen=256)

    def emit(self, message):
        # There is no raw provider error, credential, or outgoing header in these messages.
        def send():
            if self.runtime._epoch == self.epoch:
                self.deliver(message)
        self.dispatch(send)

    def reply(self, rpc_id, result=None, error=None):
        with self._lock:
            if rpc_id in self._replied:
                return
            self._replied.append(rpc_id)
        self.emit({"v": 1, "id": rpc_id, "ok": error is None,
                   **({"error": {"category": error}} if error else {"result": result})})

    def receive(self, encoded):
        rpc_id = None
        try:
            if self._closed or self.runtime._epoch != self.epoch or len(encoded.encode("utf-8")) > 2 * MAX_REQUEST + 32768:
                raise ProxyError("refused")
            message = json.loads(encoded)
            if type(message) is not dict or set(message) != {"v", "id", "op", "payload"} or type(message["v"]) is not int or message["v"] != 1:
                raise ProxyError("refused")
            rpc_id = message["id"]
            if type(rpc_id) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", rpc_id):
                rpc_id = None
                raise ProxyError("refused")
            if rpc_id in self._seen:
                # Do not resolve an existing request using a replay's response.
                return
            self._seen.append(rpc_id)
            op, payload = message["op"], message["payload"]
            if type(payload) is not dict:
                raise ProxyError("refused")
            if op == "ready" and not payload:
                self.client_ready = True
                self.reply(rpc_id, {})
            elif op == "destinations" and not payload:
                from .operations import list_destinations_in_background
                list_destinations_in_background(
                    self.runtime.parent, self.runtime.collection,
                    coordinator=self.runtime.coordinator, profile=self.profile,
                    on_complete=lambda decks: self.reply(rpc_id, {"decks": decks}),
                    on_failure=lambda _error: self.reply(rpc_id, error="refused"))
            elif op == "start" and set(payload) == {"request", "startToken"}:
                token = payload["startToken"]
                if not self._valid_token(token) or token in self._canceled_starts or self.testing:
                    raise ProxyError("refused")
                if self._start and self._start[0] == token:
                    job = self.runtime.coordinator.authorized_snapshot(self.profile, self._start[1],
                                                                       ("generating", "validating", "adding", "complete", "failed", "canceled"))
                else:
                    job = self.runtime.start_configured_job(payload["request"])
                    self._start = (token, job["request"]["requestId"])
                self.reply(rpc_id, job)
            elif op == "cancel-start" and set(payload) == {"startToken"}:
                token = payload["startToken"]
                if not self._valid_token(token):
                    raise ProxyError("refused")
                self._canceled_starts.append(token)
                if self._start and self._start[0] == token:
                    job = self.runtime.coordinator.snapshot(self.profile)
                    if job and job["request"]["requestId"] == self._start[1] and job["state"] in {"generating", "validating"}:
                        self.runtime.coordinator.cancel(self.profile, self._start[1])
                self.reply(rpc_id, {})
            elif op == "progress" and set(payload) == {"requestId", "stage", "batch", "acceptedCount"}:
                self.runtime.coordinator.progress(self.profile, payload["requestId"], payload["stage"], payload["batch"], payload["acceptedCount"])
                self.reply(rpc_id, {})
            elif op == "fail" and set(payload) == {"requestId", "category", "stage"}:
                if payload["category"] not in CATEGORIES - {"success", "canceled"} or payload["stage"] not in {"naming", "cards", "validating", "starting"}:
                    raise ProxyError("refused")
                self.runtime.fail_generation(payload["requestId"], payload["category"], payload["stage"])
                self.reply(rpc_id, {})
            elif op == "discard-retained" and set(payload) == {"requestId"}:
                self.runtime.coordinator.discard_retained(self.profile, payload["requestId"])
                self.reply(rpc_id, {})
            elif op == "job-status" and set(payload) == {"requestId"}:
                job = self.runtime.coordinator.authorized_snapshot(self.profile, payload["requestId"],
                                                                  ("generating", "validating", "adding", "complete", "failed", "canceled"))
                record = self.store.job(payload["requestId"])
                self.reply(rpc_id, {"state": job["state"], "commitState": record["state"] if record else None,
                                    "outcome": record.get("outcome") if record else None})
            elif op == "fetch":
                self._fetch(rpc_id, payload)
            elif op == "abort" and set(payload) == {"id"}:
                self.cancel(payload["id"], "canceled")
                self.reply(rpc_id, {})
            elif op == "test-result" and set(payload) == {"owner", "category"}:
                self._test_result(payload["owner"], payload["category"])
                self.reply(rpc_id, {})
            elif op == "naming-complete" and set(payload) == {"requestId", "deckName"}:
                name = self.runtime.coordinator.naming_complete(self.profile, payload["requestId"], payload["deckName"])
                self.reply(rpc_id, {"deckName": name})
            elif op == "validating" and set(payload) == {"requestId"}:
                self.runtime.coordinator.begin_validation(self.profile, payload["requestId"])
                self.reply(rpc_id, {})
            elif op == "commit" and set(payload) == {"requestId", "result"}:
                job = self.runtime.coordinator.authorized_snapshot(self.profile, payload["requestId"],
                                                                  ("generating", "validating", "complete"))
                self.runtime.add_cards(job["request"], payload["result"],
                                       on_complete=lambda completed: self.reply(rpc_id, {"state": completed["state"], "outcome": completed["outcome"]}),
                                       on_failure=lambda: self.reply(rpc_id, error="refused"))
            elif op == "duplicates" and set(payload) == {"requestId", "fronts"}:
                self._duplicates(rpc_id, payload)
            else:
                raise ProxyError("refused")
        except ProxyError as error:
            if rpc_id:
                self.reply(rpc_id, error=error.category)
        except Exception:
            if rpc_id:
                self.reply(rpc_id, error="refused")

    def _fetch(self, rpc_id, payload):
        if set(payload) != {"owner", "kind", "stage", "batch", "request"} or type(payload["owner"]) is not str:
            raise ProxyError("refused")
        owner = payload["owner"]
        is_test = payload["kind"] == "test"
        if is_test:
            test = self._tests.get(owner)
            if not test or self.clock() >= test["deadline"] or payload["stage"] != "test" or type(payload["batch"]) is not int or payload["batch"] != 0:
                raise ProxyError("refused")
            provider, settings, remaining = test["provider"], test["settings"], test["deadline"] - self.clock()
        elif payload["kind"] == "job":
            job = self.runtime.coordinator.authorized_snapshot(self.profile, owner)
            provider, settings, remaining = job["request"]["providerId"], job["settings"], job["remainingSeconds"]
        else:
            raise ProxyError("refused")
        checked = validate_http(provider, payload["request"])
        if not settings.get("credentialRef"):
            raise ProxyError("refused")
        with self._lock:
            if len(self._pending) >= 4 or any(item["owner"] == owner for item in self._pending.values()):
                raise ProxyError("refused")
        if is_test:
            test["permit"].consume()
        else:
            settings = self.runtime.coordinator.consume_call(self.profile, owner, provider,
                                                              stage=payload["stage"], batch=payload["batch"])
        transfer = Transfer()
        pending = {"owner": owner, "transfer": transfer, "deadline": self.clock() + min(60, remaining),
                   "unregister": lambda: None, "test": is_test}
        with self._lock:
            self._pending[rpc_id] = pending
        if not is_test:
            try:
                pending["unregister"] = self.runtime.coordinator.register_abort(
                    self.profile, owner, lambda: self.cancel(rpc_id, self.runtime.coordinator.snapshot(self.profile).get("failureCategory") or "canceled"))
            except Exception:
                self.cancel(rpc_id, "refused")
                return

        def work():
            try:
                result = self.proxy.execute(self.store, provider, settings["credentialRef"], checked, transfer, min(60, remaining))
            except Exception:
                result = {"error": "network"}
            with self._lock:
                item = self._pending.pop(rpc_id, None)
            if item is None:
                return
            item["unregister"]()
            with self._lock:
                test_record = self._tests.get(owner) if is_test else None
                if test_record:
                    test_record["httpSuccess"] = "status" in result and 200 <= result["status"] < 300
            self.reply(rpc_id, result if "error" not in result else None, result.get("error"))

        try:
            self.proxy.executor.submit(work)
        except Exception:
            self.cancel(rpc_id, "network")

    @staticmethod
    def _valid_token(token):
        return type(token) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", token) is not None

    def cancel(self, rpc_id, category):
        with self._lock:
            pending = self._pending.pop(rpc_id, None)
        if pending:
            pending["transfer"].cancel()
            pending["unregister"]()
            self.reply(rpc_id, error=category)

    def begin_test(self, provider, callback):
        if not self.client_ready or self._closed or self.runtime._epoch != self.epoch:
            raise ValidationError("The panel is still loading. Try again shortly.")
        if self._tests:
            raise ValidationError("Wait for the current connection test to finish")
        self.runtime.settings_guard()
        config = self.store.settings().get("providers", {}).get(provider, {})
        if not config.get("credentialRef"):
            raise ValidationError("Save a key before testing")
        owner = uuid4().hex
        settings = {**config, "modelId": model_id(provider, config)}
        self._tests[owner] = {"provider": provider, "settings": settings, "savedConfig": dict(config), "callback": callback,
                              "permit": ConnectionTestPermit(), "deadline": self.clock() + 30, "httpSuccess": False}
        self.emit({"v": 1, "event": "connection-test", "owner": owner,
                   "providerId": provider, "modelId": settings["modelId"]})
        return owner

    def _test_result(self, owner, category):
        if type(owner) is not str or type(category) is not str or category not in CATEGORIES:
            raise ProxyError("refused")
        test = self._tests.get(owner)
        if not test or self.clock() >= test["deadline"] or (category == "success" and not test["httpSuccess"]):
            raise ProxyError("refused")
        self._tests.pop(owner)
        for rpc_id, pending in list(self._pending.items()):
            if pending["owner"] == owner:
                self.cancel(rpc_id, "canceled")
        config = self.store.settings().get("providers", {}).get(test["provider"], {})
        if config == test["savedConfig"] and category in {"success", "authentication", "model"}:
            providers = self.store.settings()["providers"]
            providers[test["provider"]]["connectionStatus"] = "verified" if category == "success" else "untested"
            if category == "success":
                providers[test["provider"]]["verifiedModelId"] = test["settings"]["modelId"]
            else:
                providers[test["provider"]].pop("verifiedModelId", None)
            self.store.update_settings(providers=providers)
        test["callback"](category)

    def cancel_tests(self, category="canceled"):
        tests, self._tests = self._tests, {}
        for owner, test in tests.items():
            for rpc_id, pending in list(self._pending.items()):
                if pending["owner"] == owner:
                    self.cancel(rpc_id, category)
            self.emit({"v": 1, "event": "owner-ended", "owner": owner, "category": category})
            test["callback"](category)

    @property
    def testing(self):
        return bool(self._tests)

    def poll(self):
        job = self.runtime.coordinator.snapshot(self.profile)  # expires jobs and invokes registered aborts
        if job and job["state"] in {"failed", "canceled"} and self._ended != job["request"]["requestId"]:
            self._ended = job["request"]["requestId"]
            self.emit({"v": 1, "event": "owner-ended", "owner": self._ended,
                       "category": job.get("failureCategory") or "provider"})
        for rpc_id, pending in list(self._pending.items()):
            if self.clock() >= pending["deadline"]:
                self.cancel(rpc_id, "timeout")
        if any(self.clock() >= test["deadline"] for test in self._tests.values()):
            self.cancel_tests("timeout")

    def _duplicates(self, rpc_id, payload):
        from .operations import check_fronts_in_background
        job = self.runtime.coordinator.authorized_snapshot(self.profile, payload["requestId"])
        fronts = payload["fronts"]
        if type(fronts) is not list or len(fronts) > 100 or any(type(item) is not str or not 0 < len(item) <= 500 for item in fronts):
            raise ProxyError("refused")
        check_fronts_in_background(self.runtime.parent, self.runtime.collection, coordinator=self.runtime.coordinator,
                                   store=self.store, profile=self.profile, request=job["request"], fronts=list(fronts),
                                   on_complete=lambda result: self.reply(rpc_id, result),
                                   on_failure=lambda _error: self.reply(rpc_id, error="refused"))

    def close(self):
        self.cancel_tests()
        for rpc_id in list(self._pending):
            self.cancel(rpc_id, "canceled")
        self.emit({"v": 1, "event": "closed", "category": "canceled"})
        self._closed = True
        self.proxy.close()
