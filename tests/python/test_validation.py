from copy import deepcopy
import json
from pathlib import Path
import unittest

from wayoom.validation import ValidationError, validate_generation, validate_request

FIXTURES = json.loads((Path(__file__).parents[1] / "fixtures" / "validation.json").read_text(encoding="utf-8"))


class ValidationTests(unittest.TestCase):
    def test_shared_contract_cases(self):
        for case in FIXTURES["cases"]:
            with self.subTest(case=case["name"]):
                values = deepcopy({"request": FIXTURES["request"], "result": FIXTURES["result"]})
                for edit in case["edits"]:
                    target = values[edit["target"]]
                    for key in edit["path"][:-1]:
                        target = target[key]
                    key = edit["path"][-1]
                    if edit.get("delete"):
                        del target[key]
                    else:
                        target[key] = edit["value"]
                if case["valid"]:
                    result = validate_generation(values["request"], values["result"])
                    if "expectedDeckName" in case:
                        self.assertEqual(result["deckName"], case["expectedDeckName"])
                        self.assertEqual(validate_generation(values["request"], result), result)
                else:
                    with self.assertRaises(ValidationError):
                        validate_generation(values["request"], values["result"])

    def test_count_boundaries(self):
        for count in (1, 2, 100):
            with self.subTest(count=count):
                request = {**FIXTURES["request"], "cardCount": count}
                result = {"deckName": None, "cards": [{"front": f"Q{i}", "back": "A"} for i in range(count)]}
                self.assertEqual(len(validate_generation(request, result)["cards"]), count)

    def test_field_limits_count_unicode_codepoints(self):
        for field, maximum in (("front", 500), ("back", 2000)):
            for char in ("x", "😀"):
                with self.subTest(field=field, char=char):
                    result = deepcopy(FIXTURES["result"])
                    result["cards"][0][field] = char * maximum
                    validate_generation(FIXTURES["request"], result)
                    result["cards"][0][field] += char
                    with self.assertRaises(ValidationError):
                        validate_generation(FIXTURES["request"], result)

    def test_topic_length(self):
        validate_request({**FIXTURES["request"], "topic": "x" * 2000})
        with self.assertRaises(ValidationError):
            validate_request({**FIXTURES["request"], "topic": "x" * 2001})

    def test_full_unicode_casefold_and_normalization_duplicates(self):
        for a, b in (("Straße?", "STRASSE?"), ("Café?", "Cafe\u0301?"), ("DNA?", "ＤＮＡ?")):
            with self.subTest(a=a, b=b):
                result = {"deckName": None, "cards": [{"front": a, "back": "A"}, {"front": b, "back": "B"}]}
                with self.assertRaises(ValidationError):
                    validate_generation({**FIXTURES["request"], "cardCount": 2}, result)

    def test_python_boundary_rejects_non_json_types(self):
        for count in (True, 1.0, float("nan"), float("inf")):
            with self.subTest(count=count), self.assertRaises(ValidationError):
                validate_request({**FIXTURES["request"], "cardCount": count})
        for value in (None, [], "{}", 1):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                validate_request(value)

    def test_all_provider_difficulty_combinations(self):
        for provider in ("openai", "anthropic", "google", "xai", "mistral"):
            for difficulty in ("beginner", "intermediate", "advanced"):
                validate_generation({**FIXTURES["request"], "providerId": provider, "difficulty": difficulty}, FIXTURES["result"])

    def test_validation_normalizes_a_copy(self):
        result = deepcopy(FIXTURES["result"])
        result["cards"][0]["front"] = "  Cafe\u0301  "
        parsed = validate_generation(FIXTURES["request"], result)
        self.assertEqual(parsed["cards"][0]["front"], "Café")
        self.assertEqual(result["cards"][0]["front"], "  Cafe\u0301  ")


if __name__ == "__main__":
    unittest.main()
