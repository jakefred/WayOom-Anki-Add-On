"""Independent validation of untrusted data arriving at the Python boundary."""

import re
import unicodedata


class ValidationError(ValueError):
    pass


RESERVED = re.compile(r"\{\{c\d+::|\[(?:sound:|video:|latex\]|\${1,2}\])", re.I)
CONTROLS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
PROVIDERS = {"openai", "anthropic", "google", "xai", "mistral"}


def _object(value, keys, label):
    if type(value) is not dict or set(value) != set(keys):
        raise ValidationError(f"{label}: expected exactly {', '.join(keys)}")


def _integer(value, minimum, maximum, label):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValidationError(f"{label}: expected an integer from {minimum} to {maximum}")
    return value


def _text(value, maximum, label, card_field=False):
    if type(value) is not str:
        raise ValidationError(f"{label}: expected text")
    value = unicodedata.normalize("NFC", value).strip()
    visible = any(not c.isspace() and unicodedata.category(c) != "Cf" for c in value)
    if not visible or len(value) > maximum or CONTROLS.search(value):
        raise ValidationError(f"{label}: expected 1–{maximum} text characters without controls")
    if any(0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise ValidationError(f"{label}: invalid Unicode")
    if card_field and RESERVED.search(value):
        raise ValidationError(f"{label}: only plain-text Basic cards are supported")
    return value


def _deck_name(value):
    value = _text(value, 100, "deck name")
    if re.search(r"[\x00-\x1f\x7f-\x9f]", value):
        raise ValidationError("Deck names cannot contain control characters")
    if "::" in value:
        raise ValidationError("Use a top-level deck name")
    return value


def validate_generated_deck_name(value):
    if type(value) is str:
        value = re.sub(r" *:{2,} *", " — ", value)
    return _deck_name(value)


def question_key(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def validate_request(value):
    _object(value, ("requestId", "destination", "cardCount", "topic", "difficulty", "providerId"), "request")
    destination = value["destination"]
    if type(destination) is not dict:
        raise ValidationError("destination: expected an object")
    if destination.get("mode") == "existing":
        _object(destination, ("mode", "deckId"), "destination")
        destination = {"mode": "existing", "deckId": _integer(destination["deckId"], 1, 2**53 - 1, "deckId")}
    elif destination.get("mode") == "new":
        _object(destination, ("mode", "name"), "destination")
        name = destination["name"]
        if name is None or (type(name) is str and not name.strip()):
            name = None
        else:
            name = _deck_name(name)
        destination = {"mode": "new", "name": name}
    else:
        raise ValidationError("Unsupported destination mode")
    if type(value["difficulty"]) is not str or value["difficulty"] not in {"beginner", "intermediate", "advanced"}:
        raise ValidationError("Unsupported difficulty")
    if type(value["providerId"]) is not str or value["providerId"] not in PROVIDERS:
        raise ValidationError("Unsupported provider")
    return {
        "requestId": _text(value["requestId"], 128, "requestId"),
        "destination": destination,
        "cardCount": _integer(value["cardCount"], 1, 100, "cardCount"),
        "topic": _text(value["topic"], 2000, "topic"),
        "difficulty": value["difficulty"],
        "providerId": value["providerId"],
    }


def validate_generation(request, value):
    request = validate_request(request)
    _object(value, ("deckName", "cards"), "result")
    needs_name = request["destination"]["mode"] == "new" and request["destination"]["name"] is None
    if needs_name:
        name = validate_generated_deck_name(value["deckName"])
    else:
        if value["deckName"] is not None:
            raise ValidationError("AI must not rename the selected destination")
        name = None
    if type(value["cards"]) is not list or len(value["cards"]) != request["cardCount"]:
        raise ValidationError("Expected exactly the requested number of cards")
    cards, seen = [], set()
    for item in value["cards"]:
        _object(item, ("front", "back"), "card")
        front = _text(item["front"], 500, "front", card_field=True)
        back = _text(item["back"], 2000, "back", card_field=True)
        key = question_key(front)
        if key in seen:
            raise ValidationError("Duplicate question")
        seen.add(key)
        cards.append({"front": front, "back": back})
    return {"deckName": name, "cards": cards}
