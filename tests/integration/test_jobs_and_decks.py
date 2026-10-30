"""Job recovery, artifact cleanup and combined Undo against real Anki."""

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from anki.collection import Collection

from wayoom.cards import commit_generation, list_destinations, reconcile_job
from wayoom.jobs import JobCoordinator
from wayoom.storage import ProfileStore
from wayoom.runtime import ApplicationRuntime
from wayoom.validation import ValidationError


class JobDeckIntegrationTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2] / ".test-tmp"
        root.mkdir(exist_ok=True)
        directory = tempfile.TemporaryDirectory(prefix="anki-test-jobs-", dir=root)
        self.addCleanup(directory.cleanup)
        self.path = str(Path(directory.name) / "collection.anki2")
        self.col = Collection(self.path)
        self.addCleanup(lambda: self.col.close())
        self.store = ProfileStore(Path(directory.name) / "user_files", "profile")
        self.jobs = JobCoordinator()
        self.jobs.open_profile("profile", self.col)
        self.request = {"requestId": "ignored", "destination": {"mode": "new", "name": "Biology"},
                        "cardCount": 2, "topic": "Biology", "difficulty": "intermediate", "providerId": "openai"}
        self.result = {"deckName": None, "cards": [{"front": "Q1?", "back": "A1"}, {"front": "Q2?", "back": "A2"}]}

    def start(self):
        self.request = self.jobs.start("profile", self.request, {"modelId": "test"})["request"]

    def commit(self):
        return commit_generation(self.col, self.request, self.result, self.store, self.jobs, "profile")

    def ids(self):
        return ({d.id for d in self.col.decks.all_names_and_ids()},
                {n.id for n in self.col.models.all_names_and_ids()}, set(self.col.find_cards("")))

    def test_new_deck_type_and_batch_are_one_undo_and_redo_action(self):
        before = self.ids()
        self.start()
        result = self.commit()
        self.assertEqual(result["outcome"]["count"], 2)
        self.assertEqual(result["outcome"]["deckName"], "Biology")
        self.assertEqual(len(self.col.find_cards("tag:" + result["outcome"]["jobTag"])), 2)
        self.col.undo()
        self.assertEqual(self.ids(), before)
        self.col.redo()
        self.assertEqual(self.col.card_count(), 2)
        self.assertEqual(self.col.decks.get(result["outcome"]["deckId"])["name"], "Biology")

    def test_collisions_use_visible_suffix_and_leave_original_deck(self):
        original = self.col.decks.id("biology")
        self.col.decks.id("Biology (2)")
        self.start()
        result = self.commit()
        self.assertEqual(result["outcome"]["deckName"], "Biology (3)")
        self.assertNotEqual(result["outcome"]["deckId"], original)
        self.col.undo()
        self.assertEqual(self.col.decks.get(original)["name"], "biology")

    def test_backend_failure_compensates_new_artifacts_and_preserves_existing_cards(self):
        existing_deck = self.col.decks.id("User deck")
        model = self.col.models.by_name("Basic")
        note = self.col.new_note(model)
        note["Front"], note["Back"] = "User front", "User back"
        self.col.add_note(note, existing_deck)
        before = self.ids()
        self.start()
        original = self.col.add_notes
        def reject(requests):
            requests[-1].note.mid = 987654321
            return original(requests)
        with patch.object(self.col, "add_notes", side_effect=reject):
            with self.assertRaises(Exception):
                self.commit()
        self.assertEqual(self.ids(), before)
        self.assertEqual(self.store.job(self.request["requestId"])["state"], "not_committed")

    def test_verification_failure_cleans_up_deck_and_type(self):
        before = self.ids()
        self.start()
        with patch.object(self.col, "card_ids_of_note", side_effect=RuntimeError("Readback failed")):
            with self.assertRaisesRegex(RuntimeError, "Readback failed"):
                self.commit()
        self.assertEqual(self.ids(), before)

    def test_invalid_batch_creates_no_artifacts_or_pending_record(self):
        before = self.ids()
        self.start()
        self.result["cards"][-1]["back"] = " "
        with self.assertRaises(ValidationError):
            self.commit()
        self.assertEqual(self.ids(), before)
        self.assertEqual(self.store.jobs(), [])

    def test_replayed_commit_returns_same_result_without_writing(self):
        self.start()
        first = self.commit()
        second = self.commit()
        self.assertEqual(first["outcome"], second["outcome"])
        self.assertIsNone(second["changes"])
        self.assertEqual(self.col.card_count(), 2)

    def test_restart_reconciles_commit_after_completion_record_write_failed(self):
        self.start()
        with patch.object(self.store, "resolve", side_effect=OSError("Disk failure")):
            with self.assertRaises(OSError):
                self.commit()
        self.assertEqual(self.store.job(self.request["requestId"])["state"], "pending")
        self.col.close()
        self.col = Collection(self.path)
        self.jobs.open_profile("profile", self.col)
        recovered = self.commit()
        self.assertEqual(recovered["state"], "committed")
        self.assertEqual(self.col.card_count(), 2)

    def test_undo_does_not_authorize_replaying_old_job(self):
        self.start()
        self.commit()
        self.col.undo()
        with self.assertRaises(ValidationError):
            self.commit()
        self.assertEqual(self.col.card_count(), 0)
        self.assertEqual(self.store.job(self.request["requestId"])["state"], "undone")

    def test_partial_tagged_result_blocks_retry(self):
        self.start()
        outcome = self.commit()["outcome"]
        self.col.remove_notes([outcome["noteIds"][0]])
        with self.assertRaisesRegex(ValidationError, "uncertain"):
            reconcile_job(self.col, self.store, self.request["requestId"])
        self.assertEqual(self.store.job(self.request["requestId"])["state"], "uncertain")
        self.assertEqual(self.col.card_count(), 1)

    def test_destination_listing_and_existing_subdeck_insertion(self):
        did = self.col.decks.id("Parent::Child")
        filtered = self.col.decks.new_filtered("Filtered")
        destinations = list_destinations(self.col)
        self.assertIn({"deckId": did, "name": "Parent::Child"}, destinations)
        self.assertNotIn(filtered, [d["deckId"] for d in destinations])
        self.request["destination"] = {"mode": "existing", "deckId": did}
        self.start()
        outcome = self.commit()["outcome"]
        self.assertEqual(outcome["deckId"], did)

    def test_ai_name_repair_and_supplied_name_preservation(self):
        self.request["destination"] = {"mode": "new", "name": None}
        self.result["deckName"] = "Biology::Cells"
        self.start()
        rid = self.request["requestId"]
        self.jobs.consume_call("profile", rid, "openai", stage="naming")
        self.jobs.naming_complete("profile", rid, "Biology::Cells")
        self.assertEqual(self.commit()["outcome"]["deckName"], "Biology — Cells")

    def test_canceled_job_never_writes(self):
        before = self.ids()
        self.start()
        self.jobs.cancel("profile", self.request["requestId"])
        with self.assertRaises(ValidationError):
            self.commit()
        self.assertEqual(self.ids(), before)

    def test_deleted_destination_rolls_back_provisioned_type(self):
        self.request["destination"] = {"mode": "existing", "deckId": 987654321}
        before = self.ids()
        self.start()
        with self.assertRaises(ValidationError):
            self.commit()
        self.assertEqual(self.ids(), before)

    def test_restart_before_insertion_cleans_recorded_empty_artifacts(self):
        before = self.ids()
        self.start()
        # BaseException simulates process interruption, bypassing normal cleanup.
        with patch.object(self.col, "add_notes", side_effect=SystemExit("Interrupted")):
            with self.assertRaises(SystemExit):
                self.commit()
        self.assertNotEqual(self.ids(), before)
        self.col.close()
        self.col = Collection(self.path)
        recovered = reconcile_job(self.col, self.store, self.request["requestId"])
        self.assertEqual(recovered["state"], "not_committed")
        self.assertEqual(self.ids(), before)
        self.assertTrue(recovered["changes"].deck)

    def test_recovery_does_not_delete_user_subdeck_in_abandoned_new_deck(self):
        self.start()
        with patch.object(self.col, "add_notes", side_effect=SystemExit("Interrupted")):
            with self.assertRaises(SystemExit):
                self.commit()
        child = self.col.decks.id("Biology::User child")
        with self.assertRaisesRegex(ValidationError, "subdecks"):
            reconcile_job(self.col, self.store, self.request["requestId"])
        self.assertIsNotNone(self.col.decks.get(child, default=False))
        self.assertEqual(self.store.job(self.request["requestId"])["state"], "uncertain")

    def test_unicode_normalized_name_collision_uses_suffix(self):
        original = self.col.decks.id("Café")
        self.request["destination"]["name"] = "Cafe\u0301"
        self.start()
        outcome = self.commit()["outcome"]
        self.assertEqual(outcome["deckName"], "Café (2)")
        self.assertNotEqual(outcome["deckId"], original)

    def test_recovery_preserves_user_customization_of_abandoned_type(self):
        self.start()
        with patch.object(self.col, "add_notes", side_effect=SystemExit("Interrupted")):
            with self.assertRaises(SystemExit):
                self.commit()
        record = self.store.job(self.request["requestId"])
        model = self.col.models.get(record["createdTypeId"])
        model["css"] += "\n/* user customization */"
        self.col.models.save(model)
        with self.assertRaisesRegex(ValidationError, "modified"):
            reconcile_job(self.col, self.store, self.request["requestId"])
        self.assertIn("user customization", self.col.models.get(model["id"])["css"])

    def test_failure_before_any_write_does_not_undo_previous_batch(self):
        self.start()
        self.commit()
        before = self.ids()
        self.request["destination"] = {"mode": "existing", "deckId": 987654321}
        self.start()
        with self.assertRaises(ValidationError):
            self.commit()
        self.assertEqual(self.ids(), before)

    def test_incompatible_owned_type_is_preserved_and_previous_cards_remain(self):
        self.start()
        outcome = self.commit()["outcome"]
        model = self.col.models.get(outcome["notetypeId"])
        model["tmpls"][0]["qfmt"] = "User: {{Front}}"
        self.col.models.save(model)
        before = self.ids()
        self.request["destination"] = {"mode": "new", "name": "Another deck"}
        self.start()
        with self.assertRaisesRegex(ValidationError, "modified"):
            self.commit()
        self.assertEqual(self.ids(), before)
        self.assertEqual(self.col.models.get(model["id"])["tmpls"][0]["qfmt"], "User: {{Front}}")

    def test_application_runtime_connects_recovery_authorization_and_background_write(self):
        # Only Qt scheduling is doubled; the runtime, storage and Anki writes
        # are real, matching the production CollectionOp callback contract.
        collection = self.col
        class Operation:
            def __init__(self, parent, op):
                self.op, self.completed, self.failed = op, None, None
            def success(self, callback):
                self.completed = callback
                return self
            def failure(self, callback):
                self.failed = callback
                return self
            def run_in_background(self):
                try:
                    changes = self.op(collection)
                except Exception as error:
                    if self.failed:
                        self.failed(error)
                    else:
                        raise
                else:
                    if self.completed:
                        self.completed(changes)
        runtime = ApplicationRuntime(self.store.directory.parent, object())
        with patch.dict("sys.modules", {"aqt.operations": SimpleNamespace(CollectionOp=Operation)}):
            runtime.open_profile("profile", self.col)
            self.assertTrue(runtime.ready)
            job = runtime.start_job(self.request, {"modelId": "test"})
            runtime.add_cards(job["request"], self.result)
            self.assertEqual(runtime.panel_state()["message"], "Added 2 cards to Biology")
            self.assertTrue(runtime.panel_state()["canView"])
            runtime.add_cards(job["request"], self.result)
            self.assertEqual(self.col.card_count(), 2)
            runtime.close_profile()
