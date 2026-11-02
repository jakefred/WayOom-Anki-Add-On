"""Opt-in live verification peer. Uses only an ephemeral collection/session key."""
import argparse
from http.client import HTTPSConnection
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
from threading import Lock
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
KEY_ENV = {provider: f"WAYOOM_{provider.upper()}_API_KEY"
           for provider in ("openai", "anthropic", "google", "xai", "mistral")}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Required: permit billable provider traffic")
    parser.add_argument("--provider", choices=KEY_ENV, required=True)
    parser.add_argument("--model")
    args = parser.parse_args(argv)
    if not args.live:
        parser.error("--live is required; no key was read and no network call was made")
    from anki.collection import Collection
    from anki.buildinfo import version
    from wayoom.bridge import Bridge
    from wayoom.credentials import CredentialService
    from wayoom.proxy import ProviderProxy
    from wayoom.runtime import ApplicationRuntime

    logging.disable(logging.CRITICAL)
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    lock = Lock()

    def output(message):
        with lock:
            print(json.dumps(message), flush=True)

    # Observes a real request after TLS/send, before reading its response. The
    # owner can then test cancellation without racing a completed insertion.
    class ObservedHTTPS(HTTPSConnection):
        def request(self, *positional, **keywords):
            super().request(*positional, **keywords)
            output({"v": 1, "event": "verification-request-sent"})

    def no_os_store():
        raise RuntimeError("Live verification uses an environment/session key only")

    credentials = CredentialService(no_os_store)
    root = ROOT / ".test-tmp"
    root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="anki-test-live-", dir=root) as directory:
        collection = Collection(str(Path(directory) / "collection.anki2"))
        runtime = ApplicationRuntime(Path(directory) / "user_files", object(), credentials=credentials)
        # Real collection operations, synchronous scheduling. This peer does not
        # pretend to be a real Qt/webview test or access an actual Anki profile.
        class Operation:
            def __init__(self, parent, op, success=None): self.op, self.done, self.failed = op, success, None
            def success(self, callback): self.done = callback; return self
            def failure(self, callback): self.failed = callback; return self
            def run_in_background(self):
                try:
                    result = self.op(collection)
                except Exception as error:
                    if self.failed: self.failed(error)
                    else: raise
                else:
                    if self.done: self.done(result)
        sys.modules["aqt.operations"] = SimpleNamespace(CollectionOp=Operation, QueryOp=Operation)
        bridge = None
        try:
            runtime.open_profile("live-verification", collection)
            # Remove the process environment entry immediately after transferring
            # the key to Python's normal session-only credential service.
            key = os.environ.pop(KEY_ENV[args.provider], None)
            if not key or not key.strip():
                raise ValueError("Missing environment key")
            credentials.save_key(runtime.store, args.provider, key, session_only=True, model_id=args.model)
            del key
            credentials.select_provider(runtime.store, args.provider)
            runtime.refresh_configuration()
            proxy = ProviderProxy(credentials, connection_factory=lambda host, timeout: ObservedHTTPS(host, timeout=timeout))
            bridge = Bridge(runtime, output, lambda callback: callback(), proxy=proxy)
            for line in sys.stdin:
                try:
                    message = json.loads(line)
                    op = message.get("op")
                    if op == "verification-info" and message.get("payload") == {}:
                        config = runtime.configuration["providers"][args.provider]
                        bridge.reply(message["id"], {"providerId": args.provider, "modelId": config["modelId"],
                                                      "ankiVersion": version, "networkMode": "live"})
                    elif op == "verification-test" and message.get("payload") == {}:
                        owner = bridge.begin_test(args.provider, lambda category: output(
                            {"v": 1, "event": "verification-test-result", "category": category}))
                        bridge.reply(message["id"], {"owner": owner})
                    elif op == "verification-status" and message.get("payload") == {}:
                        job = runtime.coordinator.snapshot(runtime.profile)
                        bridge.reply(message["id"], {"cardCount": collection.card_count(), "state": job["state"] if job else None})
                    else:
                        bridge.receive(line)
                except Exception:
                    # No raw input, provider body, traceback or key on either pipe.
                    output({"v": 1, "event": "verification-local-error"})
        finally:
            if bridge: bridge.close()
            runtime.close_profile()
            collection.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Verification failed locally. Check the key environment variable, model, dependencies and connectivity.", file=sys.stderr)
        sys.exit(1)
