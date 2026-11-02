"""Tests of GUI scheduling/profile guards with explicit GUI test doubles.

Actual Anki persistence and rendering are tested separately in integration/.
"""

from copy import deepcopy
import json
from pathlib import Path
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

from wayoom.operations import add_cards_in_background, repair_type_in_background, list_destinations_in_background
from wayoom.validation import ValidationError

FIXTURES = json.loads((Path(__file__).parents[1] / "fixtures" / "validation.json").read_text(encoding="utf-8"))


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.collection = object()
        self.parent = object()
        self.aqt = ModuleType("aqt")
        self.operations = ModuleType("aqt.operations")
        self.operations.CollectionOp = Mock()
        self.cards = ModuleType("wayoom.cards")
        self.cards.commit_generation = Mock(return_value={"changes": "changes", "outcome": {"count": 1}})
        self.coordinator, self.store = object(), object()
        collection_api = ModuleType("anki.collection")
        collection_api.OpChanges = Mock()
        self.modules = patch.dict("sys.modules", {
            "aqt": self.aqt, "aqt.operations": self.operations, "wayoom.cards": self.cards,
            "anki.collection": collection_api,
        })
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def enqueue(self, request=None, result=None):
        add_cards_in_background(self.parent, self.collection, request or FIXTURES["request"],
                                result or FIXTURES["result"], coordinator=self.coordinator,
                                store=self.store, profile="profile")
        return self.operations.CollectionOp.call_args.kwargs["op"]

    def test_schedules_collection_op_instead_of_writing_on_ui_thread(self):
        op = self.enqueue()
        self.assertIs(self.operations.CollectionOp.call_args.kwargs["parent"], self.parent)
        self.operations.CollectionOp.return_value.run_in_background.assert_called_once_with()
        self.cards.commit_generation.assert_not_called()
        self.assertEqual(op(self.collection), "changes")
        self.cards.commit_generation.assert_called_once()

    def test_profile_switch_blocks_the_write(self):
        op = self.enqueue()
        with self.assertRaisesRegex(ValidationError, "profile changed"):
            op(object())
        self.cards.commit_generation.assert_not_called()

    def test_invalid_payload_does_not_schedule_a_write(self):
        request = {**FIXTURES["request"], "cardCount": 101}
        with self.assertRaises(ValidationError):
            self.enqueue(request=request)
        self.operations.CollectionOp.assert_not_called()

    def test_inputs_are_snapshotted_before_background_execution(self):
        request, result = deepcopy(FIXTURES["request"]), deepcopy(FIXTURES["result"])
        op = self.enqueue(request, result)
        request["destination"]["deckId"] = 999
        result["cards"][0]["back"] = "Changed after submission"
        op(self.collection)
        args = self.cards.commit_generation.call_args.args
        self.assertEqual(args[1]["destination"]["deckId"], 1)
        self.assertEqual(args[2]["cards"][0]["back"], "DNA")

    def test_repair_is_serialized_and_checks_profile_before_mutating(self):
        self.cards.repair_basic_notetype = Mock()
        coordinator = Mock()
        col = Mock()
        repair_type_in_background(self.parent, col, coordinator=coordinator, store=self.store,
                                  profile="profile", on_complete=Mock(), on_failure=Mock())
        op = self.operations.CollectionOp.call_args.kwargs["op"]
        self.cards.repair_basic_notetype.assert_not_called()
        with self.assertRaises(ValidationError):
            op(object())
        self.cards.repair_basic_notetype.assert_not_called()
        op(col)
        coordinator.require_profile.assert_called_once_with("profile", col)
        self.cards.repair_basic_notetype.assert_called_once_with(col, self.store)
        col.merge_undo_entries.assert_called_once_with(col.add_custom_undo_entry.return_value)

    def test_destination_listing_is_read_only_and_checks_profile_before_reading(self):
        self.operations.QueryOp = Mock()
        self.cards.list_destinations = Mock(return_value=[{"deckId": 1, "name": "Full::Name"}])
        coordinator, done, failed = Mock(), Mock(), Mock()
        list_destinations_in_background(self.parent, self.collection, coordinator=coordinator,
                                        profile="a", on_complete=done, on_failure=failed)
        options = self.operations.QueryOp.call_args.kwargs
        self.assertIs(options["success"], done)
        self.cards.list_destinations.assert_not_called()
        with self.assertRaisesRegex(ValidationError, "profile changed"):
            options["op"](object())
        self.cards.list_destinations.assert_not_called()
        self.assertEqual(options["op"](self.collection), [{"deckId": 1, "name": "Full::Name"}])
        coordinator.require_profile.assert_called_once_with("a", self.collection)
        coordinator.require_profile.side_effect = ValidationError("Stale profile")
        with self.assertRaisesRegex(ValidationError, "Stale"):
            options["op"](self.collection)
        self.cards.list_destinations.assert_called_once()
