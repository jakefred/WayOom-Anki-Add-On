from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.quality_review import AXES, LEVELS, main, prepare, summarize


class QualityReviewTests(unittest.TestCase):
    def sample(self):
        return {"schemaVersion": 1, "runs": [
            {"providerId": "openai", "modelId": "fixture", "topic": f"Topic {topic}", "difficulty": level,
             "cards": [{"front": f"Question {index}", "back": "Answer"} for index in range(5)]}
            for topic in range(3) for level in LEVELS]}

    def rated(self):
        review = prepare(self.sample())
        review["reviewer"] = "Human reviewer"
        for card in review["cards"]:
            card["ratings"] = {axis: True for axis in AXES}
        return review

    def test_prepare_is_unrated_and_preserves_all_levels_topics_and_cards(self):
        review = prepare(self.sample())
        self.assertEqual(len(review["cards"]), 45)
        self.assertEqual(review["reviewer"], "")
        self.assertTrue(all(value is None for card in review["cards"] for value in card["ratings"].values()))
        report = summarize(review)
        self.assertFalse(report["releaseApproved"])
        self.assertFalse(report["samples"][0]["difficultyThresholdMet"])
        self.assertEqual(report["samples"][0]["missingRatings"], 225)

    def test_threshold_is_at_least_80_percent_per_level_and_never_approves_release(self):
        review = self.rated()
        beginner = [card for card in review["cards"] if card["difficulty"] == "beginner"]
        for card in beginner[:3]: card["ratings"]["difficultyMatch"] = False
        report = summarize(review)
        self.assertTrue(report["samples"][0]["difficultyThresholdMet"])
        self.assertEqual(report["samples"][0]["levels"]["beginner"]["difficultyPercent"], 80)
        self.assertFalse(report["releaseApproved"])
        beginner[3]["ratings"]["difficultyMatch"] = False
        self.assertFalse(summarize(review)["samples"][0]["difficultyThresholdMet"])

    def test_missing_ratings_reviewer_or_representative_coverage_never_pass(self):
        for change in (lambda r: r.update(reviewer=""),
                       lambda r: r["cards"][0]["ratings"].update(correctness=None),
                       lambda r: r.update(cards=[card for card in r["cards"] if card["difficulty"] != "advanced"]),
                       lambda r: r.update(cards=r["cards"][:5])):
            review = self.rated(); change(review)
            self.assertFalse(summarize(review)["samples"][0]["difficultyThresholdMet"])

    def test_reports_all_quality_dimensions_without_claiming_automatic_correctness(self):
        review = self.rated()
        for axis in AXES: review["cards"][0]["ratings"][axis] = False
        self.assertEqual(summarize(review)["samples"][0]["concerns"], {axis: 1 for axis in AXES})

    def test_invalid_types_duplicate_ids_and_invalid_generated_cards_are_rejected(self):
        for change in (lambda r: r["cards"][0]["ratings"].update(correctness=1),
                       lambda r: r["cards"][0]["ratings"].update(clarity="true"),
                       lambda r: r["cards"].append(deepcopy(r["cards"][0])),
                       lambda r: r["cards"][0].update(front="")):
            review = self.rated(); change(review)
            with self.assertRaises(ValueError): summarize(review)

    def test_cli_never_overwrites_a_review_and_incomplete_report_exits_nonzero(self):
        from contextlib import redirect_stderr, redirect_stdout
        import io
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "sample.json", Path(directory) / "review.json"
            source.write_text(json.dumps(self.sample()), encoding="utf-8")
            self.assertEqual(main(["prepare", str(source), "--output", str(output)]), 0)
            before = output.read_bytes()
            logs = io.StringIO()
            with redirect_stderr(logs):
                self.assertEqual(main(["prepare", str(source), "--output", str(output)]), 2)
            self.assertEqual(before, output.read_bytes())
            with redirect_stdout(logs): self.assertEqual(main(["report", str(output)]), 1)
