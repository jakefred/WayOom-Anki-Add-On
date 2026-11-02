"""Prepare human ratings and summarize NFR-08/AC-10; never call an AI provider."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wayoom.validation import validate_generation, validate_request

AXES = ("relevance", "correctness", "clarity", "distinctConcept", "difficultyMatch")
LEVELS = ("beginner", "intermediate", "advanced")


def prepare(value):
    if type(value) is not dict or set(value) != {"schemaVersion", "runs"} or value["schemaVersion"] != 1:
        raise ValueError("Expected a version 1 sample export")
    if type(value["runs"]) is not list or not 1 <= len(value["runs"]) <= 500:
        raise ValueError("Expected 1–500 sample runs")
    cards = []
    for index, run in enumerate(value["runs"]):
        if type(run) is not dict or set(run) != {"providerId", "modelId", "topic", "difficulty", "cards"}:
            raise ValueError("Invalid sample fields")
        if type(run["modelId"]) is not str or not run["modelId"].strip() or len(run["modelId"]) > 256:
            raise ValueError("Expected a model ID")
        if type(run["cards"]) is not list:
            raise ValueError("Expected cards")
        request = validate_request({"requestId": f"sample-{index}", "providerId": run["providerId"],
                                    "topic": run["topic"], "difficulty": run["difficulty"],
                                    "cardCount": len(run["cards"]), "destination": {"mode": "new", "name": "Quality review"}})
        result = validate_generation(request, {"deckName": None, "cards": run["cards"]})
        for number, card in enumerate(result["cards"]):
            cards.append({"id": f"run-{index + 1}-card-{number + 1}",
                          **{field: run[field] for field in ("providerId", "modelId", "topic", "difficulty")},
                          **card, "ratings": {axis: None for axis in AXES}, "notes": ""})
    return {"schemaVersion": 1, "reviewer": "", "cards": cards}


def summarize(review):
    if type(review) is not dict or set(review) != {"schemaVersion", "reviewer", "cards"} or review["schemaVersion"] != 1:
        raise ValueError("Expected a version 1 human review")
    if type(review["reviewer"]) is not str or type(review["cards"]) is not list or not review["cards"]:
        raise ValueError("Expected a reviewer and cards")
    groups = defaultdict(list)
    seen = set()
    for card in review["cards"]:
        if type(card) is not dict or set(card) != {"id", "providerId", "modelId", "topic", "difficulty", "front", "back", "ratings", "notes"}:
            raise ValueError("Invalid review card")
        if type(card["id"]) is not str or not card["id"] or card["id"] in seen:
            raise ValueError("Card IDs must be unique")
        seen.add(card["id"])
        if type(card["notes"]) is not str or type(card["ratings"]) is not dict or set(card["ratings"]) != set(AXES):
            raise ValueError("Expected all human rating axes")
        if any(value is not None and type(value) is not bool for value in card["ratings"].values()):
            raise ValueError("Ratings must be true, false, or null; no numeric/coerced scores")
        # Recheck the saved card contract, not just the first generated export.
        prepare({"schemaVersion": 1, "runs": [{**{field: card[field] for field in ("providerId", "modelId", "topic", "difficulty")},
                                              "cards": [{"front": card["front"], "back": card["back"]}]}]})
        groups[(card["providerId"], card["modelId"])].append(card)
    reports = []
    for (provider, model), cards in sorted(groups.items()):
        levels = {}
        coverage = True
        for level in LEVELS:
            sample = [card for card in cards if card["difficulty"] == level]
            topics = {card["topic"].strip() for card in sample}
            counts = [sum(card["topic"].strip() == topic for card in sample) for topic in topics]
            # A documented owner-tool sampling policy, not an invented SRS rule.
            representative = len(topics) >= 3 and all(count >= 5 for count in counts)
            coverage &= representative
            matching = sum(card["ratings"]["difficultyMatch"] is True for card in sample)
            levels[level] = {"cards": len(sample), "topics": len(topics), "representativeSample": representative,
                             "difficultyMatches": matching, "difficultyPercent": 100 * matching / len(sample) if sample else 0}
        missing = sum(value is None for card in cards for value in card["ratings"].values())
        complete = bool(review["reviewer"].strip()) and missing == 0
        concerns = {axis: sum(card["ratings"][axis] is False for card in cards) for axis in AXES}
        reports.append({"providerId": provider, "modelId": model, "cards": len(cards), "missingRatings": missing,
                        "reviewComplete": complete, "representativeSample": coverage, "levels": levels,
                        "concerns": concerns, "difficultyThresholdMet": complete and coverage
                        and all(levels[level]["difficultyMatches"] * 5 >= levels[level]["cards"] * 4 for level in LEVELS)})
    return {"schemaVersion": 1, "reviewer": review["reviewer"], "samples": reports,
            "releaseApproved": False, "note": "Human sample evidence only. Review relevance, correctness, clarity and duplicate-concept concerns; other release gates remain open."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "report"):
        sub = commands.add_parser(name)
        sub.add_argument("input", type=Path)
        sub.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.input.stat().st_size > 20 * 1024 * 1024:
            raise ValueError("Input exceeds 20 MiB")
        value = json.loads(args.input.read_text(encoding="utf-8"))
        result = prepare(value) if args.command == "prepare" else summarize(value)
        if args.command == "prepare" and not args.output:
            raise ValueError("prepare requires --output")
        if args.output:
            with args.output.open("x", encoding="utf-8", newline="\n") as output:
                json.dump(result, output, ensure_ascii=False, indent=2)
                output.write("\n")
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.command == "report" and not all(sample["difficultyThresholdMet"] for sample in result["samples"]):
            return 1
        return 0
    except Exception:
        print("Quality review failed: check input format, strict boolean ratings and a new output path. No input content was logged.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
