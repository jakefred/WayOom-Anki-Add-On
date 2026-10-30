"""Rollback control flow with backend doubles; real persistence is integration-tested."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

from wayoom.validation import ValidationError


class CardRollbackTests(unittest.TestCase):
    def setUp(self):
        # Load the real adapter without requiring a platform-specific Anki wheel.
        modules = {
            "anki": SimpleNamespace(),
            "anki.collection": SimpleNamespace(AddNoteRequest=SimpleNamespace, OpChanges=Mock),
            "anki.consts": SimpleNamespace(MODEL_STD=0),
            "anki.decks": SimpleNamespace(DeckId=int),
            "anki.models": SimpleNamespace(NotetypeId=int),
        }
        path = Path(__file__).resolve().parents[2] / "wayoom" / "cards.py"
        spec = importlib.util.spec_from_file_location("wayoom.cards_rollback_test", path)
        self.adapter = importlib.util.module_from_spec(spec)
        with patch.dict("sys.modules", modules):
            spec.loader.exec_module(self.adapter)

        self.col = Mock()
        self.col.decks.get.return_value = {"dyn": 0}
        self.col.models.get.return_value = {
            "type": 0,
            "flds": [{"name": "Front"}, {"name": "Back"}],
            "tmpls": [{"qfmt": self.adapter.QUESTION_TEMPLATE,
                       "afmt": self.adapter.ANSWER_TEMPLATE}],
        }
        self.col.find_notes.return_value = []
        self.col.new_note.return_value = MagicMock(id=123)
        self.col.card_ids_of_note.return_value = [456]
        self.col.get_card.return_value = SimpleNamespace(did=1)
        self.request = {
            "requestId": "rollback-test", "destination": {"mode": "existing", "deckId": 1},
            "cardCount": 1, "topic": "Biology", "difficulty": "intermediate",
            "providerId": "anthropic",
        }
        self.result = {"deckName": None, "cards": [{"front": "Question?", "back": "Answer"}]}

    def insert(self):
        return self.adapter.insert_basic_cards(self.col, self.request, self.result, 10)

    def test_readback_exceptions_undo_committed_batch_and_propagate(self):
        for method in ("card_ids_of_note", "get_card"):
            with self.subTest(method=method):
                self.col.undo.reset_mock()
                failure = RuntimeError("Readback failed")
                with patch.object(self.col, method, side_effect=failure):
                    with self.assertRaises(RuntimeError) as caught:
                        self.insert()
                self.assertIs(caught.exception, failure)
                self.col.undo.assert_called_once_with()

    def test_count_mismatch_undoes_exactly_once(self):
        self.col.card_ids_of_note.return_value = []
        with self.assertRaises(ValidationError):
            self.insert()
        self.col.undo.assert_called_once_with()

    def test_failed_backend_transaction_does_not_undo_previous_action(self):
        self.col.add_notes.side_effect = RuntimeError("Backend rejected batch")
        with self.assertRaises(RuntimeError):
            self.insert()
        self.col.undo.assert_not_called()

    def test_success_returns_changes_without_undo(self):
        self.assertIs(self.insert(), self.col.add_notes.return_value)
        self.col.undo.assert_not_called()
