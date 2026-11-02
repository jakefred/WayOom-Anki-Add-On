import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wayoom.bridge import Bridge
from wayoom.credentials import CredentialService
from wayoom.models import DEFAULT_MODELS
from wayoom.runtime import ApplicationRuntime
from wayoom.validation import PROVIDERS


class GenerationControlTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.credentials = CredentialService(lambda: self.fail("No real OS store"))
        self.runtime = ApplicationRuntime(Path(directory.name), object(), credentials=self.credentials)
        with patch("wayoom.runtime.recover_in_background") as recovery:
            self.runtime.open_profile("fixture", object())
            recovery.call_args.kwargs["on_complete"]({})
        self.credentials.save_key(self.runtime.store, "openai", "fake-key", session_only=True)
        providers = self.runtime.store.settings()["providers"]
        providers["openai"].update(connectionStatus="verified", verifiedModelId=DEFAULT_MODELS["openai"]["modelId"])
        self.runtime.store.update_settings(providers=providers)
        self.request = {"requestId": "ignored", "destination": {"mode": "new", "name": None},
                        "cardCount": 100, "topic": "Biology", "difficulty": "intermediate", "providerId": "openai"}
        self.messages = []
        self.bridge = Bridge(self.runtime, self.messages.append, lambda callback: callback())
        self.addCleanup(self.bridge.close)
        self.sequence = 0

    def rpc(self, op, payload):
        self.sequence += 1
        rid = f"control-{self.sequence}"
        self.bridge.receive(json.dumps({"v": 1, "id": rid, "op": op, "payload": payload}))
        return next(item for item in self.messages if item.get("id") == rid)

    def test_one_documented_live_untested_default_per_provider_and_blank_override(self):
        self.assertEqual(set(DEFAULT_MODELS), PROVIDERS)
        for config in DEFAULT_MODELS.values():
            self.assertEqual(config["liveStatus"], "untested")
            self.assertTrue(config["source"].startswith("https://"))
        self.assertNotIn("modelId", self.runtime.store.settings()["providers"]["openai"])
        summary = self.credentials.summary(self.runtime.store)["providers"]["openai"]
        self.assertEqual(summary["modelId"], DEFAULT_MODELS["openai"]["modelId"])
        self.assertEqual(summary["modelOverride"], "")
        job = self.runtime.start_configured_job(self.request)
        self.assertEqual(job["settings"]["modelId"], DEFAULT_MODELS["openai"]["modelId"])

    def test_start_is_idempotent_and_cancellation_tombstone_prevents_a_late_start(self):
        payload = {"request": self.request, "startToken": "start-1"}
        first = self.rpc("start", payload)["result"]
        second = self.rpc("start", payload)["result"]
        self.assertEqual(first["request"]["requestId"], second["request"]["requestId"])
        self.rpc("cancel-start", {"startToken": "start-1"})
        self.assertEqual(self.runtime.coordinator.snapshot("fixture")["state"], "canceled")
        self.assertFalse(self.rpc("start", payload)["ok"])
        self.rpc("cancel-start", {"startToken": "before-start"})
        self.assertFalse(self.rpc("start", {**payload, "startToken": "before-start"})["ok"])

    def test_retry_proof_and_discard_control_retained_name_and_preserve_call_budgets(self):
        job = self.rpc("start", {"request": self.request, "startToken": "first"})["result"]
        rid = job["request"]["requestId"]
        coordinator = self.runtime.coordinator
        coordinator.consume_call("fixture", rid, "openai", stage="naming")
        coordinator.naming_complete("fixture", rid, "Cells")
        self.rpc("progress", {"requestId": rid, "stage": "validating", "batch": 0, "acceptedCount": 25})
        self.assertEqual(coordinator.snapshot("fixture")["callsUsed"], 1)
        self.rpc("fail", {"requestId": rid, "stage": "cards", "category": "network"})
        retry = self.rpc("start", {"request": self.request, "startToken": "retry"})["result"]
        self.assertNotEqual(retry["request"]["requestId"], rid)
        self.assertEqual(retry["retryOf"], rid)
        self.assertEqual(retry["deckName"], "Cells")
        self.assertEqual(retry["callsUsed"], 0)
        rid = retry["request"]["requestId"]
        self.rpc("fail", {"requestId": rid, "stage": "cards", "category": "network"})
        self.rpc("discard-retained", {"requestId": rid})
        fresh = self.rpc("start", {"request": self.request, "startToken": "fresh"})["result"]
        self.assertIsNone(fresh["retryOf"])
        self.assertIsNone(fresh["deckName"])

    def test_progress_cannot_override_limits_or_fail_a_different_job(self):
        job = self.runtime.start_configured_job(self.request)
        rid = job["request"]["requestId"]
        for batch, accepted in ((4, 0), (0, 101), (True, 1)):
            self.assertFalse(self.rpc("progress", {"requestId": rid, "stage": "cards", "batch": batch, "acceptedCount": accepted})["ok"])
        self.assertFalse(self.rpc("fail", {"requestId": "stale", "stage": "cards", "category": "network"})["ok"])
        self.assertEqual(self.runtime.coordinator.snapshot("fixture")["state"], "generating")

    def test_default_connection_verification_is_bound_to_effective_model(self):
        self.rpc("ready", {})
        completed = []
        owner = self.bridge.begin_test("openai", completed.append)
        self.bridge._tests[owner]["httpSuccess"] = True  # only the HTTP result is doubled here
        self.rpc("test-result", {"owner": owner, "category": "success"})
        self.assertEqual(self.credentials.summary(self.runtime.store)["providers"]["openai"]["connectionStatus"], "verified")
        with patch.dict(DEFAULT_MODELS["openai"], {"modelId": "changed-default"}):
            self.assertEqual(self.credentials.summary(self.runtime.store)["providers"]["openai"]["connectionStatus"], "untested")

    def test_changed_model_or_profile_never_approves_old_draft_reuse(self):
        job = self.runtime.start_configured_job(self.request)
        rid = job["request"]["requestId"]
        self.runtime.fail_generation(rid, "network", "cards")
        providers = self.runtime.store.settings()["providers"]
        providers["openai"]["modelId"] = "custom-model"
        providers["openai"]["verifiedModelId"] = "custom-model"  # model verification is doubled for this retry test
        self.runtime.store.update_settings(providers=providers)
        retry = self.runtime.start_configured_job(self.request)
        self.assertIsNone(retry["retryOf"])
        self.assertEqual(retry["settings"]["modelId"], "custom-model")
        self.runtime.fail_generation(retry["request"]["requestId"], "network", "cards")
        self.runtime.coordinator.open_profile("fixture", self.runtime.collection)
        fresh = self.runtime.start_configured_job(self.request)
        self.assertIsNone(fresh["retryOf"])
        self.assertNotEqual(fresh["sessionId"], retry["sessionId"])
