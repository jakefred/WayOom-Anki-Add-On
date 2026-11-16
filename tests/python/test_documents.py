from pathlib import Path
import tempfile
import unittest

from wayoom.jobs import JobCoordinator
from wayoom.storage import ProfileStore
from wayoom.validation import ValidationError, validate_request


class DocumentRequestTests(unittest.TestCase):
    def request(self):
        return {"requestId": "document-test", "destination": {"mode": "existing", "deckId": 1},
                "cardCount": 3, "topic": "", "difficulty": "beginner", "providerId": "openai",
                "source": {"name": "Lecture.pdf", "selection": "Pages 2", "text": "Selected source text"}}

    def test_source_validation_and_optional_instructions(self):
        request = self.request()
        self.assertEqual(validate_request(request), request)
        for bad in (None, {}, {**request["source"], "text": ""},
                    {**request["source"], "text": "x" * 60001}, {**request["source"], "path": "private"}):
            with self.assertRaises(ValidationError):
                validate_request({**request, "source": bad})
        request.pop("source")
        with self.assertRaises(ValidationError):
            validate_request(request)

    def test_retry_is_bound_to_selected_content_and_close_releases_source(self):
        coordinator = JobCoordinator()
        coordinator.open_profile("p", object())
        first = coordinator.start("p", self.request(), {})
        coordinator.fail("p", first["request"]["requestId"], category="network")
        same = coordinator.start("p", self.request(), {})
        self.assertEqual(same["retryOf"], first["request"]["requestId"])
        coordinator.fail("p", same["request"]["requestId"], category="network")
        changed = self.request()
        changed["source"]["text"] = "Different selected page"
        next_job = coordinator.start("p", changed, {})
        self.assertIsNone(next_job["retryOf"])
        coordinator.close_profile("p")
        self.assertNotIn("source", coordinator.snapshot("p")["request"])

    def test_commit_journal_never_persists_document_or_instructions(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = ProfileStore(Path(temporary), "p")
            request = self.request()
            request["topic"] = "Private guidance"
            store.prepare(request)
            data = str(store.job(request["requestId"]))
            for secret in ("Lecture.pdf", "Selected source text", "Private guidance", "source", "topic"):
                self.assertNotIn(secret, data)
