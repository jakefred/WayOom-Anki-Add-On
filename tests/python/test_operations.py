"""Tests of GUI scheduling/profile guards with explicit GUI test doubles.

Actual Anki persistence and rendering are tested separately in integration/.
"""

from copy import deepcopy
import json
from pathlib import Path
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

from wayoom.operations import add_cards_in_background
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
