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
