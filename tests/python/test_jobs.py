from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import unittest

from wayoom.jobs import ConnectionTestPermit, JobCoordinator
from wayoom.validation import ValidationError


class JobTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.jobs = JobCoordinator(clock=lambda: self.now)
        self.col = object()
        self.jobs.open_profile("a", self.col)
        self.request = {"requestId": "ignored", "destination": {"mode": "existing", "deckId": 1},
                        "cardCount": 100, "topic": "Biology", "difficulty": "intermediate",
                        "providerId": "anthropic"}
        self.settings = {"modelId": "test", "credentialRef": "opaque-reference"}

    def start(self):
        self.request = self.jobs.start("a", self.request, self.settings)["request"]
        return self.request["requestId"]

    def test_limits_and_attempts_include_failed_transport(self):
        rid = self.start()
        self.assertEqual(self.jobs.snapshot("a")["callLimit"], 12)
        for batch in range(4):
            for _ in range(3):
                self.jobs.consume_call("a", rid, "anthropic", batch=batch)
            with self.assertRaises(ValidationError):
                self.jobs.consume_call("a", rid, "anthropic", batch=batch)
        self.assertEqual(self.jobs.snapshot("a")["callsUsed"], 12)

    def test_naming_precedes_cards_and_adds_three_attempts(self):
        self.request["destination"] = {"mode": "new", "name": None}
        rid = self.start()
        self.assertEqual(self.jobs.snapshot("a")["callLimit"], 15)
        with self.assertRaises(ValidationError):
            self.jobs.consume_call("a", rid, "anthropic")
        self.jobs.consume_call("a", rid, "anthropic", stage="naming")
        with self.assertRaises(ValidationError):
            self.jobs.naming_complete("a", rid, " ")
        self.assertEqual(self.jobs.naming_complete("a", rid, "Biology::Cells"), "Biology — Cells")
        self.jobs.consume_call("a", rid, "anthropic")
        with self.assertRaises(ValidationError):
            self.jobs.consume_call("a", rid, "anthropic", stage="naming")

    def test_snapshot_and_provider_are_bound(self):
        rid = self.start()
        self.settings["modelId"] = "changed"
        snapshot = self.jobs.snapshot("a")
        snapshot["request"]["topic"] = "changed"
        self.assertEqual(self.jobs.snapshot("a")["request"]["topic"], "Biology")
        self.assertEqual(self.jobs.consume_call("a", rid, "anthropic")["modelId"], "test")
        for args in (("a", rid, "openai"), ("a", "stale", "anthropic"), ("b", rid, "anthropic")):
            with self.assertRaises(ValidationError):
                self.jobs.consume_call(*args)

    def test_repeated_start_is_blocked_and_cancel_aborts_late_work(self):
        rid = self.start()
        with self.assertRaises(ValidationError):
            self.start()
        aborted = []
        self.jobs.register_abort("a", rid, lambda: aborted.append(True))
        self.jobs.cancel("a", rid)
        self.assertEqual(aborted, [True])
        with self.assertRaises(ValidationError):
            self.jobs.begin_write("a", rid, self.col, self.request)
        self.assertNotEqual(self.start(), rid)

    def test_timeout_and_reopened_same_collection_invalidate_job(self):
        rid = self.start()
        self.now = 600
        with self.assertRaisesRegex(ValidationError, "timed out"):
            self.jobs.consume_call("a", rid, "anthropic")
        rid = self.start()
        self.jobs.open_profile("a", self.col)
        with self.assertRaises(ValidationError):
            self.jobs.begin_write("a", rid, self.col, self.request)

    def test_changed_payload_and_cancel_during_insertion_are_rejected(self):
        rid = self.start()
        altered = {**deepcopy(self.request), "topic": "Other"}
        with self.assertRaises(ValidationError):
            self.jobs.begin_write("a", rid, self.col, altered)
        self.jobs.begin_write("a", rid, self.col, self.request)
        with self.assertRaises(ValidationError):
            self.jobs.cancel("a", rid)
        self.now = 700
        self.jobs.finish("a", rid, success=True)
        self.assertEqual(self.jobs.snapshot("a")["state"], "complete")

    def test_concurrent_reservations_cannot_exceed_three(self):
        rid = self.start()
        def reserve(_):
            try:
                self.jobs.consume_call("a", rid, "anthropic")
                return 1
            except ValidationError:
                return 0
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(reserve, range(20))), 3)

    def test_connection_test_permit_and_secret_rejection(self):
        permit = ConnectionTestPermit()
        permit.consume()
        with self.assertRaises(ValidationError):
            permit.consume()
        with self.assertRaises(ValidationError):
            self.jobs.start("a", self.request, {"apiKey": "never-save"})

    def test_timeout_poll_aborts_inflight_request_and_allows_new_job(self):
        rid = self.start()
        abort = []
        self.jobs.register_abort("a", rid, lambda: abort.append(True))
        self.now = 601
        self.assertEqual(self.jobs.snapshot("a")["state"], "failed")
        self.assertEqual(abort, [True])
        self.assertNotEqual(self.start(), rid)

    def test_validation_can_resume_calls_without_resetting_budgets(self):
        rid = self.start()
        self.jobs.consume_call("a", rid, "anthropic")
        self.jobs.begin_validation("a", rid)
        self.jobs.consume_call("a", rid, "anthropic")
        self.assertEqual(self.jobs.snapshot("a")["state"], "generating")
        self.assertEqual(self.jobs.snapshot("a")["callsUsed"], 2)
        self.jobs.begin_validation("a", rid)
        self.jobs.register_abort("a", rid, lambda: None)

    def test_failed_retry_retains_name_but_cancel_or_changed_input_discards_it(self):
        self.request["destination"] = {"mode": "new", "name": None}
        rid = self.start()
        self.jobs.consume_call("a", rid, "anthropic", stage="naming")
        self.jobs.naming_complete("a", rid, "Cells")
        self.jobs.fail("a", rid)
        retry = self.start()
        self.assertNotEqual(retry, rid)
        self.assertEqual(self.jobs.snapshot("a")["deckName"], "Cells")
        self.jobs.consume_call("a", retry, "anthropic")
        self.assertEqual(self.jobs.snapshot("a")["callsUsed"], 1)
        self.jobs.cancel("a", retry)
        self.assertIsNone(self.jobs.snapshot("a")["deckName"])
        rid = self.start()
        self.assertIsNone(self.jobs.snapshot("a")["deckName"])
        self.jobs.consume_call("a", rid, "anthropic", stage="naming")
        self.jobs.naming_complete("a", rid, "Cells")
        self.jobs.fail("a", rid)
        self.request["topic"] = "Changed"
        self.start()
        self.assertIsNone(self.jobs.snapshot("a")["deckName"])
