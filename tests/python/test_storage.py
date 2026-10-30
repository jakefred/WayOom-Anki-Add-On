from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wayoom.storage import ProfileStore, job_tag
from wayoom.validation import ValidationError


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.store = ProfileStore(self.root, "profile-a")

    def test_profiles_are_isolated_and_settings_survive_reopening(self):
        self.store.update_settings(panelVisible=False, setupComplete=True, notetypeId=12)
        self.assertFalse(ProfileStore(self.root, "profile-a").settings()["panelVisible"])
        self.assertEqual(ProfileStore(self.root, "../profile-b").settings(), {})
        self.assertEqual(self.store.directory.parent, self.root)

    def test_secret_fields_and_invalid_settings_are_rejected(self):
        for values in ({"apiKey": "secret"}, {"panelVisible": 1},
                       {"providers": {"openai": {"apiKey": "secret"}}}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                self.store.update_settings(**values)
        self.assertEqual(self.store.settings(), {})

    def test_interrupted_replace_preserves_previous_file(self):
        self.store.update_settings(panelVisible=False)
        with patch("wayoom.storage.os.replace", side_effect=OSError("Disk failure")):
            with self.assertRaises(OSError):
                self.store.update_settings(panelVisible=True)
        self.assertEqual(self.store.settings(), {"panelVisible": False})
        self.assertFalse(list(self.store.directory.glob(".write-*")))

    def test_pending_record_omits_content_and_cannot_be_overwritten(self):
        request = {"requestId": "job-1", "cardCount": 1, "destination": {"mode": "existing", "deckId": 1},
                   "providerId": "openai", "topic": "private-topic", "apiKey": "secret"}
        self.store.prepare(request)
        content = self.store._job_path("job-1").read_text()
        self.assertNotIn("private-topic", content)
        self.assertNotIn("secret", content)
        with self.assertRaises(ValidationError):
            self.store.prepare(request)
        self.store.resolve("job-1", "not_committed")
        self.assertEqual(self.store.jobs()[0]["state"], "not_committed")

    def test_job_ids_cannot_escape_directory_or_inject_anki_search(self):
        for value in ("../escape", "x y", 'x" or deck:*', "", "x::y"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                job_tag(value)
