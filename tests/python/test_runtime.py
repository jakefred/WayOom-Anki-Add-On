from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from wayoom.runtime import ApplicationRuntime
from wayoom.validation import ValidationError


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.runtime = ApplicationRuntime(Path(directory.name), object())
        self.recovery = patch("wayoom.runtime.recover_in_background").start()
        self.addCleanup(patch.stopall)
        self.col = object()
        self.request = {"requestId": "ignored", "destination": {"mode": "existing", "deckId": 1},
                        "cardCount": 1, "topic": "Biology", "difficulty": "intermediate", "providerId": "openai"}

    def test_start_waits_for_recovery_and_stale_callback_cannot_unlock_new_profile(self):
        self.runtime.open_profile("a", self.col)
        callback_a = self.recovery.call_args.kwargs["on_complete"]
        with self.assertRaises(ValidationError):
            self.runtime.start_job(self.request, {})
        self.runtime.open_profile("b", object())
        callback_a({})
        self.assertFalse(self.runtime.ready)
        self.recovery.call_args.kwargs["on_complete"]({})
        self.assertIsNotNone(self.runtime.start_job(self.request, {}))

    def test_recovery_failure_blocks_generation(self):
        self.runtime.open_profile("a", self.col)
        self.recovery.call_args.kwargs["on_failure"](RuntimeError("Recovery failed"))
        with self.assertRaises(ValidationError):
            self.runtime.start_job(self.request, {})

    def test_view_cards_search_uses_only_saved_job_tag(self):
        self.runtime.open_profile("a", self.col)
        self.recovery.call_args.kwargs["on_complete"]({})
        self.runtime.store.prepare({**self.request, "requestId": "job-1"})
        dialogs = Mock()
        with patch.dict("sys.modules", {"aqt": Mock(dialogs=dialogs)}):
            self.runtime.view_cards("job-1")
        dialogs.open.return_value.search_for.assert_called_once_with("tag:wayoom::job::job-1")
        with self.assertRaises(ValidationError):
            self.runtime.view_cards("unknown")

    def test_rejected_payload_and_definitive_failure_allow_retry(self):
        self.runtime.open_profile("a", self.col)
        self.recovery.call_args.kwargs["on_complete"]({})
        request = self.runtime.start_job(self.request, {})["request"]
        with patch("wayoom.runtime.add_cards_in_background", side_effect=ValidationError("Invalid output")):
            with self.assertRaises(ValidationError):
                self.runtime.add_cards(request, {})
        self.assertTrue(self.runtime.ready)
        self.assertEqual(self.runtime.coordinator.snapshot("a")["state"], "failed")
        self.assertEqual(self.runtime.panel_state()["action"], "retry")
        self.assertIn("Invalid output", self.runtime.panel_state()["message"])
        request = self.runtime.start_job(self.request, {})["request"]
        with patch("wayoom.runtime.add_cards_in_background") as add:
            self.runtime.add_cards(request, {})
            add.call_args.kwargs["on_failure"](ValidationError("A question already exists"))
        self.assertTrue(self.runtime.ready)
        self.assertEqual(self.runtime.coordinator.snapshot("a")["state"], "failed")
        self.runtime.start_job(self.request, {})

    def test_uncertain_job_can_be_viewed_and_acknowledged_without_collection_changes(self):
        self.runtime.open_profile("a", self.col)
        self.recovery.call_args.kwargs["on_complete"]({"old": {"state": "uncertain"}})
        self.runtime.store.prepare({**self.request, "requestId": "old"})
        self.runtime.store.resolve("old", "uncertain")
        with self.assertRaises(ValidationError):
            self.runtime.start_job(self.request, {})
        self.assertEqual(self.runtime.panel_state()["recoveryJobs"], ["old"])
        self.runtime.acknowledge_job("old")
        self.assertEqual(self.runtime.store.job("old")["state"], "acknowledged")
        self.runtime.start_job(self.request, {})

    def test_uncertain_write_blocks_retry_and_late_callback_cannot_change_new_profile(self):
        self.runtime.open_profile("a", self.col)
        self.recovery.call_args.kwargs["on_complete"]({})
        request = self.runtime.start_job(self.request, {})["request"]
        with patch("wayoom.runtime.add_cards_in_background") as add:
            self.runtime.add_cards(request, {})
            failure = add.call_args.kwargs["on_failure"]
            self.runtime.store.prepare(request)
            failure(OSError("Do not display raw backend error"))
        self.assertEqual(self.runtime.panel_state()["action"], "reopen-profile")
        self.assertNotIn("raw backend error", self.runtime.panel_state()["message"])
        with self.assertRaises(ValidationError):
            self.runtime.start_job(self.request, {})
        self.runtime.open_profile("b", object())
        self.recovery.call_args.kwargs["on_complete"]({})
        failure(RuntimeError("Late error"))
        self.assertIsNone(self.runtime.error)

    def test_repair_reports_action_and_does_not_unlock_a_new_profile(self):
        from wayoom.cards import NoteTypeRepairRequired
        self.runtime.open_profile("a", self.col)
        self.recovery.call_args.kwargs["on_complete"]({})
        request = self.runtime.start_job(self.request, {})["request"]
        with patch("wayoom.runtime.add_cards_in_background") as add:
            self.runtime.add_cards(request, {})
            add.call_args.kwargs["on_failure"](NoteTypeRepairRequired("Template modified"))
        self.assertEqual(self.runtime.panel_state()["action"], "repair-note-type")
        with patch("wayoom.runtime.repair_type_in_background") as repair:
            self.runtime.repair_note_type()
            done = repair.call_args.kwargs["on_complete"]
            with self.assertRaises(ValidationError):
                self.runtime.start_job(self.request, {})
            self.runtime.open_profile("b", object())
            done()
            self.assertFalse(self.runtime.ready)

    def test_error_after_durable_completion_reports_success(self):
        self.runtime.open_profile("a", self.col)
        self.recovery.call_args.kwargs["on_complete"]({})
        request = self.runtime.start_job(self.request, {})["request"]
        with patch("wayoom.runtime.add_cards_in_background") as add:
            self.runtime.add_cards(request, {})
            self.runtime.coordinator.begin_write("a", request["requestId"], self.col, request)
            self.runtime.coordinator.finish("a", request["requestId"], success=True)
            self.runtime.store.prepare(request)
            self.runtime.store.resolve(request["requestId"], "committed", {
                "requestId": request["requestId"], "count": 1, "deckName": "Biology"})
            add.call_args.kwargs["on_failure"](OSError("Settings failed after commit"))
        self.assertEqual(self.runtime.panel_state()["state"], "complete")
        self.assertEqual(self.runtime.panel_state()["message"], "Added 1 cards to Biology")
