"""Authorized bridge writes and read-only checks against disposable real Anki."""
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from anki.collection import Collection
from wayoom.bridge import Bridge
from wayoom.cards import create_basic_notetype, insert_basic_cards
from wayoom.runtime import ApplicationRuntime


class BridgeCollectionTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2] / ".test-tmp"
        directory = tempfile.TemporaryDirectory(prefix="anki-test-bridge-", dir=root)
        self.addCleanup(directory.cleanup)
        self.col = Collection(str(Path(directory.name) / "collection.anki2"))
        self.addCleanup(self.col.close)
        self.runtime = ApplicationRuntime(Path(directory.name) / "user_files", object())
        with patch("wayoom.runtime.recover_in_background") as recovery:
            self.runtime.open_profile("fixture", self.col)
            recovery.call_args.kwargs["on_complete"]({})
        self.mid = create_basic_notetype(self.col)
        self.runtime.store.update_settings(notetypeId=self.mid)
        self.did = self.col.decks.id("Bridge destination")
        self.request = {"requestId": "ignored", "destination": {"mode": "existing", "deckId": self.did},
                        "cardCount": 1, "topic": "Fixture", "difficulty": "beginner", "providerId": "openai"}
        self.messages = []
        self.bridge = Bridge(self.runtime, self.messages.append, lambda callback: callback())
        self.addCleanup(self.bridge.close)
        collection = self.col
        class Operation:
            def __init__(self, parent, op, success=None):
                self.op, self.completed, self.failed = op, success, None
            def success(self, callback): self.completed = callback; return self
            def failure(self, callback): self.failed = callback; return self
            def run_in_background(self):
                try:
                    result = self.op(collection)
                except Exception as error:
                    if self.failed: self.failed(error)
                    else: raise
                else:
                    if self.completed: self.completed(result)
        operations = patch.dict("sys.modules", {"aqt.operations": SimpleNamespace(CollectionOp=Operation, QueryOp=Operation)})
        operations.start()
        self.addCleanup(operations.stop)
        self.counter = 0

    def rpc(self, op, payload):
        self.counter += 1
        rid = f"collection-{self.counter}"
        self.bridge.receive(json.dumps({"v": 1, "id": rid, "op": op, "payload": payload}))
        return next(message for message in self.messages if message.get("id") == rid)

    def test_authorized_commit_is_serialized_replay_safe_and_contains_no_changes_object(self):
        job = self.runtime.start_job(self.request, {})
        payload = {"requestId": job["request"]["requestId"], "result": {"deckName": None, "cards": [{"front": "<b>Question</b>", "back": "Answer"}]}}
        result = self.rpc("commit", payload)
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["outcome"]["count"], 1)
        self.assertNotIn("changes", result["result"])
        json.dumps(result)
        self.assertEqual(self.col.card_count(), 1)
        self.assertIn("&lt;b&gt;", self.col.get_note(self.col.find_notes("")[0])["Front"])
        self.rpc("commit", payload)
        self.assertEqual(self.col.card_count(), 1)
        self.col.undo()
        self.rpc("commit", payload)
        self.assertEqual(self.col.card_count(), 0)

    def test_destination_rpc_lists_full_normal_deck_ids_only_and_rejects_payloads(self):
        hierarchical = self.col.decks.id("Parent::Child")
        filtered = self.col.decks.new_filtered("Filtered")
        result = self.rpc("destinations", {})
        self.assertTrue(result["ok"])
        decks = result["result"]["decks"]
        self.assertIn({"deckId": hierarchical, "name": "Parent::Child"}, decks)
        self.assertNotIn(filtered, [deck["deckId"] for deck in decks])
        self.assertFalse(self.rpc("destinations", {"profile": "another"})["ok"])
        self.assertEqual(self.col.card_count(), 0)

    def test_full_deck_casefold_check_includes_filtered_card_beyond_200_front_hint(self):
        for offset in (0, 100, 200):
            count = 100 if offset < 200 else 1
            request = {**self.request, "requestId": f"seed-{offset}", "cardCount": count}
            cards = [{"front": "Straße?" if index == 0 else f"Question {index}", "back": "Answer"}
                     for index in range(offset, offset + count)]
            insert_basic_cards(self.col, request, {"deckName": None, "cards": cards}, self.mid)
        card = self.col.get_card(self.col.find_cards("tag:wayoom::job::seed-0")[0])
        card.odid, card.did = self.did, self.col.decks.new_filtered("Filtered")
        self.col.update_card(card)
        job = self.runtime.start_job(self.request, {})
        result = self.rpc("duplicates", {"requestId": job["request"]["requestId"], "fronts": ["STRASSE?", "New question", "NEW QUESTION"]})
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["conflicts"], [0, 2])
        self.assertEqual(len(result["result"]["avoidFronts"]), 200)
        self.assertNotIn("Straße?", result["result"]["avoidFronts"])
        self.assertEqual(self.col.card_count(), 201)

    def test_prompt_hint_excludes_manual_notes_while_duplicate_check_includes_them(self):
        note = self.col.new_note(self.col.models.get(self.mid))
        note["Front"] = "Private manual question?"
        note["Back"] = "Private answer"
        note.tags = ["wayoom"]
        self.col.add_note(note, self.did)
        unrelated = self.col.new_note(self.col.models.by_name("Basic"))
        unrelated["Front"] = "Unrelated tagged question?"
        unrelated["Back"] = "Private answer"
        unrelated.tags = ["wayoom"]
        self.col.add_note(unrelated, self.did)
        insert_basic_cards(self.col, {**self.request, "requestId": "generated-seed"},
                           {"deckName": None, "cards": [{"front": "Generated question?", "back": "Answer"}]}, self.mid)
        job = self.runtime.start_job(self.request, {})
        result = self.rpc("duplicates", {"requestId": job["request"]["requestId"],
                                         "fronts": ["PRIVATE MANUAL QUESTION?", "Generated question?",
                                                    "Unrelated tagged question?"]})
        self.assertTrue(result["ok"])
        self.assertEqual(result["result"]["conflicts"], [0, 1])
        self.assertEqual(result["result"]["avoidFronts"], ["Generated question?"])

    def test_canceled_commit_and_invalid_payload_cannot_write_or_stick_in_validation(self):
        job = self.runtime.start_job(self.request, {})
        response = self.rpc("commit", {"requestId": job["request"]["requestId"], "result": {"unexpected": "field"}})
        self.assertFalse(response["ok"])
        self.assertEqual(self.runtime.coordinator.snapshot("fixture")["state"], "failed")
        self.assertTrue(self.runtime.ready)
        job = self.runtime.start_job(self.request, {})
        self.runtime.coordinator.cancel("fixture", job["request"]["requestId"])
        response = self.rpc("commit", {"requestId": job["request"]["requestId"], "result": {"deckName": None, "cards": [{"front": "Q", "back": "A"}]}})
        self.assertFalse(response["ok"])
        self.assertEqual(self.col.card_count(), 0)

    def test_duplicate_prefetch_rejects_deleted_or_filtered_destination(self):
        for deck_id in (987654321, self.col.decks.new_filtered("Unavailable destination")):
            request = {**self.request, "destination": {"mode": "existing", "deckId": deck_id}}
            job = self.runtime.start_job(request, {})
            response = self.rpc("duplicates", {"requestId": job["request"]["requestId"], "fronts": []})
            self.assertFalse(response["ok"])
            self.assertEqual(self.col.card_count(), 0)
            self.runtime.coordinator.fail("fixture", job["request"]["requestId"])
