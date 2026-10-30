"""Real Anki backend tests. Every test owns a new disposable collection.

Missing Anki is a failure, not a skipped green integration suite.
"""

from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from anki.collection import Collection
from anki.consts import MODEL_CLOZE

from wayoom.cards import create_basic_notetype, insert_basic_cards
from wayoom.validation import ValidationError


class RenderedHTML(HTMLParser):
    def __init__(self, value):
        super().__init__(convert_charrefs=True)
        self.tags, self.text = [], []
        self.feed(value)

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)

    def handle_data(self, data):
        self.text.append(data)


class AnkiCardIntegrationTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2] / ".test-tmp"
        root.mkdir(exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(prefix="anki-test-", dir=root)
        self.addCleanup(self.directory.cleanup)
        self.path = str(Path(self.directory.name) / "collection.anki2")
        self.col = Collection(self.path)
        self.addCleanup(lambda: self.col.close())
        self.deck_id = self.col.decks.id("Integration Test::Destination")
        self.other_deck = self.col.decks.id("Unrelated Deck")
        self.notetype_id = create_basic_notetype(self.col)

    def request(self, count=1, deck_id=None):
        return {
            "requestId": "integration-job",
            "destination": {"mode": "existing", "deckId": deck_id or self.deck_id},
            "cardCount": count,
            "topic": "Biology",
            "difficulty": "intermediate",
            "providerId": "anthropic",
        }

    def result(self, count=1, prefix="Question"):
        return {"deckName": None, "cards": [{"front": f"{prefix} {i}?", "back": f"Answer {i}"} for i in range(count)]}

    def insert(self, result=None, request=None, notetype_id=None):
        return insert_basic_cards(self.col, request or self.request(), result or self.result(), notetype_id or self.notetype_id)

    def assert_no_cards(self):
        self.assertEqual(self.col.card_count(), 0)
        self.assertEqual(self.col.note_count(), 0)

    def test_one_item_creates_one_reviewable_card_in_selected_deck(self):
        self.insert()
        self.assertEqual(self.col.card_count(), 1)
        note = self.col.get_note(self.col.find_notes("")[0])
        self.assertEqual(note.mid, self.notetype_id)
        self.assertEqual(note["Front"], "Question 0?")
        self.assertEqual(note["Back"], "Answer 0")
        self.assertEqual(note.tags, ["wayoom", "wayoom::job::integration-job"])
        cards = note.cards()
        self.assertEqual(len(cards), 1)
        card = cards[0]
        self.assertEqual(card.did, self.deck_id)
        self.assertEqual(card.ord, 0)
        self.assertEqual(card.queue, 0)  # New and eligible for Anki's scheduler.
        self.assertEqual(card.type, 0)
        self.assertIn("Question 0?", card.question())
        self.assertNotIn("Answer 0", card.question())
        self.assertIn("Answer 0", card.answer())

    def test_two_items_create_exactly_two_cards(self):
        self.insert(self.result(2), self.request(2))
        self.assertEqual(self.col.card_count(), 2)
        self.assertEqual(self.col.note_count(), 2)
        for cid in self.col.find_cards(""):
            self.assertEqual(self.col.get_card(cid).did, self.deck_id)

    def test_100_items_create_exactly_100_cards_and_one_undo_step(self):
        self.insert(self.result(100), self.request(100))
        self.assertEqual(self.col.card_count(), 100)
        for cid in self.col.find_cards(""):
            card = self.col.get_card(cid)
            self.assertEqual(card.did, self.deck_id)
            self.assertIn("Question", card.question())
            self.assertIn("Answer", card.answer())
        self.col.undo()
        self.assert_no_cards()

    def test_batch_undo_preserves_existing_cards_and_scheduling(self):
        self.insert(self.result(prefix="Existing"), self.request(deck_id=self.other_deck))
        existing_id = self.col.find_cards("")[0]
        original = self.col.get_card(existing_id)
        before = (original.did, original.due, original.ivl, original.queue, original.reps)
        self.insert(self.result(2), self.request(2))
        self.assertEqual(self.col.card_count(), 3)
        self.col.undo()
        self.assertEqual(self.col.find_cards(""), [existing_id])
        existing = self.col.get_card(existing_id)
        self.assertEqual((existing.did, existing.due, existing.ivl, existing.queue, existing.reps), before)

    def test_created_cards_survive_close_and_reopen(self):
        self.insert()
        ids = self.col.find_cards("")
        self.col.close()
        self.col = Collection(self.path)
        self.assertEqual(self.col.find_cards(""), ids)
        self.assertIn("Answer 0", self.col.get_card(ids[0]).answer())

    def test_javascript_zod_output_roundtrips_into_real_anki_cards(self):
        script = Path(__file__).resolve().parents[1] / "js" / "validate-stdin.mjs"
        wire_input = {"request": self.request(2), "result": self.result(2)}
        wire_input["result"]["cards"][0]["front"] = "  Cafe\u0301 & 日本語?  "
        completed = subprocess.run(
            ["node", str(script)], input=json.dumps(wire_input),
            text=True, encoding="utf-8", capture_output=True, check=True,
        )
        validated = json.loads(completed.stdout)
        self.insert(validated["result"], validated["request"])
        self.assertEqual(self.col.card_count(), 2)
        questions = ["".join(RenderedHTML(self.col.get_card(cid).question()).text) for cid in self.col.find_cards("")]
        self.assertTrue(any("Café & 日本語?" in question for question in questions))

    def test_card_count_mismatch_rolls_back_only_the_new_batch(self):
        self.insert(self.result(prefix="Existing"))
        existing = self.col.find_cards("")
        with patch.object(self.col, "card_ids_of_note", return_value=[]):
            with self.assertRaisesRegex(ValidationError, "expected cards"):
                self.insert(self.result(2), self.request(2))
        self.assertEqual(self.col.find_cards(""), existing)

    def test_readback_exception_rolls_back_only_the_new_batch(self):
        for method in ("card_ids_of_note", "get_card"):
            with self.subTest(method=method):
                self.insert(self.result(prefix=f"Existing {method}"))
                existing_cards = self.col.find_cards("")
                existing_notes = self.col.find_notes("")
                decks = self.col.decks.all_names_and_ids()
                notetypes = self.col.models.all_names_and_ids()
                with patch.object(self.col, method, side_effect=RuntimeError("Readback failed")):
                    with self.assertRaisesRegex(RuntimeError, "Readback failed"):
                        self.insert(self.result(2), self.request(2))
                self.assertEqual(self.col.find_cards(""), existing_cards)
                self.assertEqual(self.col.find_notes(""), existing_notes)
                self.assertEqual(self.col.decks.all_names_and_ids(), decks)
                self.assertEqual(self.col.models.all_names_and_ids(), notetypes)

    def test_failed_backend_write_does_not_undo_previous_batch(self):
        self.insert(self.result(prefix="Existing"))
        existing_cards = self.col.find_cards("")
        existing_notes = self.col.find_notes("")
        original_add = self.col.add_notes

        def invalidate_second(requests):
            requests[1].note.mid = 987654321
            return original_add(requests)

        with patch.object(self.col, "add_notes", side_effect=invalidate_second):
            with self.assertRaises(Exception):
                self.insert(self.result(2), self.request(2))
        self.assertEqual(self.col.find_cards(""), existing_cards)
        self.assertEqual(self.col.find_notes(""), existing_notes)
    def test_html_and_entities_remain_inert_when_anki_renders_them(self):
        payload = '<script>pycmd("erase")</script><img src=x onerror="evil()"> & &#91;sound:missing.mp3&#93;'
        result = {"deckName": None, "cards": [{"front": payload, "back": "A < B & C > D\n日本語 😀"}]}
        self.insert(result)
        card = self.col.get_card(self.col.find_cards("")[0])
        question = RenderedHTML(card.question())
        answer = RenderedHTML(card.answer())
        self.assertNotIn("script", question.tags)
        self.assertNotIn("img", question.tags)
        self.assertIn(payload, "".join(question.text))
        self.assertIn("A < B & C > D", "".join(answer.text))
        self.assertIn("日本語 😀", "".join(answer.text))
        self.assertIn("br", answer.tags)
        self.assertEqual(card.question_av_tags(), [])
        self.assertEqual(card.answer_av_tags(), [])

    def test_product_length_boundaries_render_in_anki(self):
        result = {"deckName": None, "cards": [{"front": "😀" * 500, "back": "x" * 2000}]}
        self.insert(result)
        card = self.col.get_card(self.col.find_cards("")[0])
        self.assertIn("😀" * 500, card.question())
        self.assertIn("x" * 2000, card.answer())

    def test_invalid_last_item_leaves_no_partial_batch(self):
        result = self.result(2)
        result["cards"][1]["back"] = "   "
        with self.assertRaises(ValidationError):
            self.insert(result, self.request(2))
        self.assert_no_cards()

    def test_raw_bridge_payload_cannot_bypass_validation(self):
        request = self.request()
        request["cardCount"] = True
        with self.assertRaises(ValidationError):
            self.insert(request=request)
        self.assert_no_cards()

    def test_unsupported_content_never_reaches_anki_write(self):
        for value in ("{{c1::Paris}}", "[sound:audio.mp3]", "[latex]x[/latex]", "a\x1fb", "\u200b"):
            with self.subTest(value=value):
                result = {"deckName": None, "cards": [{"front": value, "back": "Answer"}]}
                with self.assertRaises(ValidationError):
                    self.insert(result)
                self.assert_no_cards()

    def test_deleted_deck_does_not_fall_back_to_default(self):
        self.col.decks.remove([self.deck_id])
        with self.assertRaises(ValidationError):
            self.insert()
        self.assert_no_cards()

    def test_filtered_deck_rejected(self):
        filtered_id = self.col.decks.new_filtered("Filtered Test")
        with self.assertRaises(ValidationError):
            self.insert(request=self.request(deck_id=filtered_id))
        self.assert_no_cards()

    def test_deleted_note_type_is_rejected_without_recreating_it(self):
        self.col.models.remove(self.notetype_id)
        count = len(self.col.models.all_names_and_ids())
        with self.assertRaises(ValidationError):
            self.insert()
        self.assertEqual(len(self.col.models.all_names_and_ids()), count)
        self.assert_no_cards()

    def test_cloze_note_type_is_rejected(self):
        cloze = next(model for model in self.col.models.all() if model["type"] == MODEL_CLOZE)
        with self.assertRaises(ValidationError):
            self.insert(notetype_id=cloze["id"])
        self.assert_no_cards()

    def test_reversed_template_is_rejected_instead_of_doubling_card_count(self):
        model = self.col.models.get(self.notetype_id)
        template = self.col.models.new_template("Reverse")
        template["qfmt"], template["afmt"] = "{{Back}}", "{{Front}}"
        self.col.models.add_template(model, template)
        self.col.models.save(model)
        with self.assertRaises(ValidationError):
            self.insert()
        self.assert_no_cards()

    def test_customized_template_is_not_overwritten(self):
        model = self.col.models.get(self.notetype_id)
        model["tmpls"][0]["qfmt"] = "Custom: {{Front}}"
        self.col.models.save(model)
        with self.assertRaises(ValidationError):
            self.insert()
        self.assertEqual(self.col.models.get(self.notetype_id)["tmpls"][0]["qfmt"], "Custom: {{Front}}")
        self.assert_no_cards()

    def test_template_deck_override_cannot_redirect_cards(self):
        model = self.col.models.get(self.notetype_id)
        model["tmpls"][0]["did"] = self.other_deck
        self.col.models.save(model)
        with self.assertRaises(ValidationError):
            self.insert()
        self.assert_no_cards()

    def test_renamed_field_is_rejected_without_overwriting_it(self):
        model = self.col.models.get(self.notetype_id)
        model["flds"][0]["name"] = "Question"
        model["tmpls"][0]["qfmt"] = "{{Question}}"
        self.col.models.save(model)
        with self.assertRaises(ValidationError):
            self.insert()
        self.assertEqual(self.col.models.get(self.notetype_id)["flds"][0]["name"], "Question")
        self.assert_no_cards()

    def test_renamed_note_type_is_found_by_id(self):
        model = self.col.models.get(self.notetype_id)
        model["name"] = "Personalized name"
        self.col.models.save(model)
        self.insert()
        self.assertEqual(self.col.card_count(), 1)

    def test_existing_duplicate_rejected_and_original_preserved(self):
        result = {"deckName": None, "cards": [{"front": "Straße?", "back": "Original"}]}
        self.insert(result)
        original_ids = self.col.find_cards("")
        duplicate = {"deckName": None, "cards": [{"front": " STRASSE? ", "back": "Different answer"}]}
        with self.assertRaises(ValidationError):
            self.insert(duplicate)
        self.assertEqual(self.col.find_cards(""), original_ids)
        self.assertIn("Original", self.col.get_card(original_ids[0]).answer())

    def test_duplicate_check_is_scoped_to_destination_deck(self):
        self.insert(request=self.request(deck_id=self.other_deck))
        self.insert()
        self.assertEqual(self.col.card_count(), 2)

    def test_duplicate_after_html_roundtrip_is_rejected(self):
        result = {"deckName": None, "cards": [{"front": "A < B?\nWhy?", "back": "Answer"}]}
        self.insert(result)
        with self.assertRaises(ValidationError):
            self.insert(result)
        self.assertEqual(self.col.card_count(), 1)

    def test_actual_backend_transaction_rolls_back_if_second_note_is_invalid(self):
        original_add = self.col.add_notes

        def invalidate_second(requests):
            requests[1].note.mid = 987654321  # Inject a backend failure after local validation.
            return original_add(requests)

        with patch.object(self.col, "add_notes", side_effect=invalidate_second):
            with self.assertRaises(Exception) as caught:
                self.insert(self.result(2), self.request(2))
        self.assertNotIsInstance(caught.exception, ValidationError)
        self.assert_no_cards()


if __name__ == "__main__":
    unittest.main()
