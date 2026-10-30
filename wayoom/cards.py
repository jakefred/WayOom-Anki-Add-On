"""Basic-card persistence for an existing deck and an explicitly provisioned type.

Call insert_basic_cards inside Anki's serialized CollectionOp, never directly
from a webview or network worker. Job orchestration and deck creation are separate.
"""

from html import escape, unescape
from hashlib import sha256
import json
import re

from anki.collection import AddNoteRequest, OpChanges
from anki.consts import MODEL_STD
from anki.decks import DeckId
from anki.models import NotetypeId

from .validation import ValidationError, question_key, validate_generation, validate_request
from .storage import job_tag

QUESTION_TEMPLATE = "{{Front}}"
ANSWER_TEMPLATE = '{{FrontSide}}\n\n<hr id="answer">\n\n{{Back}}'


def create_basic_notetype(col):
    """Provision an owned type; the caller must retain the returned stable ID."""
    model = col.models.new("WayOom Basic")
    for name in ("Front", "Back"):
        col.models.add_field(model, col.models.new_field(name))
    template = col.models.new_template("Card 1")
    template["qfmt"] = QUESTION_TEMPLATE
    template["afmt"] = ANSWER_TEMPLATE
    col.models.add_template(model, template)
    col.models.add(model)
    return model["id"]


def _require_basic_type(col, notetype_id):
    if type(notetype_id) is not int or notetype_id <= 0:
        raise ValidationError("Invalid note-type ID")
    model = col.models.get(NotetypeId(notetype_id))
    if model is None or model["type"] != MODEL_STD:
        raise ValidationError("A Basic WayOom note type is required")
    if [field["name"] for field in model["flds"]] != ["Front", "Back"] or len(model["tmpls"]) != 1:
        raise ValidationError("The WayOom fields or card templates were modified; repair the note type")
    template = model["tmpls"][0]
    if (template["qfmt"] != QUESTION_TEMPLATE or template["afmt"] != ANSWER_TEMPLATE
            or template.get("did")):
        raise ValidationError("The WayOom card template was modified; repair the note type")
    return model


def _as_html(value):
    return escape(value.replace("\r\n", "\n").replace("\r", "\n")).replace("\n", "<br>")


def insert_basic_cards(col, request, result, notetype_id, *, _rollback=True):
    request = validate_request(request)
    result = validate_generation(request, result)
    if request["destination"]["mode"] != "existing":
        raise ValidationError("Resolve a new deck before calling the insertion adapter")
    deck_id = DeckId(request["destination"]["deckId"])
    # Anki's default get() behavior can fall back to Default for an unknown ID.
    deck = col.decks.get(deck_id, default=False)
    if deck is None or deck.get("dyn"):
        raise ValidationError("Destination must be an existing normal deck")
    model = _require_basic_type(col, notetype_id)

    existing = set()
    for nid in col.find_notes(f"mid:{notetype_id}"):
        note = col.get_note(nid)
        if any(card.did == deck_id for card in note.cards()):
            front = unescape(re.sub(r"<br\s*/?>", "\n", note["Front"], flags=re.I))
            existing.add(question_key(front))
    if any(question_key(card["front"]) in existing for card in result["cards"]):
        raise ValidationError("A question already exists in the destination deck")

    requests = []
    for draft in result["cards"]:
        note = col.new_note(model)
        note["Front"] = _as_html(draft["front"])
        note["Back"] = _as_html(draft["back"])
        note.tags = ["wayoom", job_tag(request["requestId"])]
        requests.append(AddNoteRequest(note=note, deck_id=deck_id))

    changes = col.add_notes(requests)
    # add_notes() has committed at this point. Any verification failure must
    # undo that batch, including exceptions raised while reading it back.
    # Keep add_notes() outside this handler: a failed backend transaction must
    # not cause us to undo a preceding user action.
    try:
        card_ids = [cid for item in requests for cid in col.card_ids_of_note(item.note.id)]
        if len(card_ids) != request["cardCount"] or any(col.get_card(cid).did != deck_id for cid in card_ids):
            raise ValidationError("Anki did not create the expected cards in the destination deck")
    except Exception:
        if _rollback:
            col.undo()
        raise
    return changes


def list_destinations(col):
    """Keep full names and IDs, including existing subdecks; omit filtered decks."""
    return [{"deckId": item.id, "name": item.name}
            for item in col.decks.all_names_and_ids(include_filtered=False)]


def _new_destination(col, name):
    # Ask Anki to resolve names, so its normalization/case rules decide collision.
    candidate, suffix = name, 2
    while col.decks.id_for_name(candidate) is not None:
        tail = f" ({suffix})"
        candidate = name[:100 - len(tail)].rstrip() + tail
        suffix += 1
    deck = col.decks.new_deck()
    deck.name = candidate
    deck_id = col.decks.add_deck(deck).id
    resolved = col.decks.get(DeckId(deck_id), default=False)
    if resolved is None or "::" in resolved["name"]:
        raise ValidationError("Anki could not create a top-level deck")
    return deck_id


def _outcome(col, request_id, expected_count):
    tag = job_tag(request_id)
    note_ids = col.find_notes(f"tag:{tag}")
    if not note_ids:
        return None
    notes = [col.get_note(nid) for nid in note_ids]
    cards = [card for note in notes for card in note.cards()]
    decks = {card.did for card in cards}
    types = {note.mid for note in notes}
    if (len(notes) != expected_count or len(cards) != expected_count
            or any(len(note.cards()) != 1 for note in notes)
            or len(decks) != 1 or len(types) != 1):
        raise ValidationError("Job commit is uncertain; inspect its tagged cards before retrying")
    deck_id = next(iter(decks))
    deck = col.decks.get(DeckId(deck_id), default=False)
    if deck is None:
        raise ValidationError("The job's destination is unavailable")
    return {"requestId": request_id, "count": len(cards), "deckId": deck_id,
            "deckName": deck["name"], "notetypeId": next(iter(types)),
            "noteIds": list(note_ids), "cardIds": [card.id for card in cards], "jobTag": tag}


def reconcile_job(col, store, request_id):
    """Reconcile tags and clean abandoned empty artifacts; never reinsert here."""
    record = store.job(request_id)
    if record is None:
        return None
    changes = OpChanges()
    try:
        outcome = _outcome(col, request_id, record["cardCount"])
    except Exception:
        store.resolve(request_id, "uncertain")
        raise
    if outcome is None:
        if record["state"] in {"pending", "uncertain"}:
            try:
                changes.MergeFrom(_compensate(
                    col, request_id, record.get("createdDeckId"), record.get("createdTypeId"),
                    expected_deck_name=record.get("createdDeckName"),
                    expected_type=record.get("createdTypeFingerprint")))
            except Exception:
                store.resolve(request_id, "uncertain")
                raise
        state = "undone" if record["state"] in {"committed", "undone"} else "not_committed"
        store.resolve(request_id, state)
        return {"state": state, "outcome": record.get("outcome"), "changes": changes}
    store.resolve(request_id, "committed", outcome)
    store.update_settings(notetypeId=outcome["notetypeId"])
    return {"state": "committed", "outcome": outcome, "changes": changes}


def reconcile_pending_jobs(col, store):
    return {record["requestId"]: reconcile_job(col, store, record["requestId"])
            for record in store.jobs()}


def _type_fingerprint(col, notetype_id):
    model = col.models.get(NotetypeId(notetype_id))
    if model is None:
        return None
    owned = {key: model.get(key) for key in ("name", "type", "flds", "tmpls", "css")}
    return sha256(json.dumps(owned, sort_keys=True).encode("utf-8")).hexdigest()


def _compensate(col, request_id, created_deck, created_type, *, expected_deck_name=None, expected_type=None):
    """Fallback when a backend error has cleared Anki's undo history."""
    changes = OpChanges()
    notes = col.find_notes(f"tag:{job_tag(request_id)}")
    if notes:
        changes.MergeFrom(col.remove_notes(notes).changes)
    if created_deck and col.decks.get(DeckId(created_deck), default=False) is not None:
        if col.find_cards(f"did:{created_deck}"):
            raise ValidationError("New deck is not empty; automatic cleanup stopped")
        # An empty user-created child deck also makes parent deletion unsafe.
        deck_name = col.decks.get(DeckId(created_deck), default=False)["name"]
        if expected_deck_name is None or deck_name != expected_deck_name:
            raise ValidationError("New deck was modified; automatic cleanup stopped")
        if any(item.name.startswith(deck_name + "::") for item in col.decks.all_names_and_ids()):
            raise ValidationError("New deck has subdecks; automatic cleanup stopped")
        changes.MergeFrom(col.decks.remove([DeckId(created_deck)]).changes)
    if created_type and col.models.get(NotetypeId(created_type)) is not None:
        if expected_type is None or _type_fingerprint(col, created_type) != expected_type:
            raise ValidationError("New note type was modified; automatic cleanup stopped")
        if col.find_notes(f"mid:{created_type}"):
            raise ValidationError("New note type is in use; automatic cleanup stopped")
        changes.MergeFrom(col.models.remove(NotetypeId(created_type)))
    return changes


def commit_generation(col, request, result, store, coordinator, profile):
    """Authorized, replay-safe insertion with deck/type creation in one Undo."""
    request = validate_request(request)
    result = validate_generation(request, result)
    if store.profile != profile:
        raise ValidationError("Settings belong to a different profile")
    coordinator.require_profile(profile, col)
    prior = reconcile_job(col, store, request["requestId"])
    if prior is not None:
        if prior["state"] == "committed":
            return {**prior, "changes": None}
        raise ValidationError("This job was already attempted; start a new job after reconciliation")
    coordinator.begin_write(profile, request["requestId"], col, request, result)
    try:
        # Durable intent precedes every collection mutation.
        store.prepare(request)
    except Exception:
        coordinator.finish(profile, request["requestId"], success=False)
        raise

    target = None
    created_deck = created_type = None
    created_deck_name = created_type_fingerprint = None
    try:
        target = col.add_custom_undo_entry("WayOom: Add generated cards")
        settings = store.settings()
        notetype_id = settings.get("notetypeId")
        if notetype_id is None or col.models.get(NotetypeId(notetype_id)) is None:
            notetype_id = create_basic_notetype(col)
            created_type = notetype_id
            created_type_fingerprint = _type_fingerprint(col, created_type)
            store.record_artifacts(request["requestId"], notetype_id=created_type,
                                   notetype_fingerprint=created_type_fingerprint)
        else:
            _require_basic_type(col, notetype_id)
        if request["destination"]["mode"] == "new":
            name = request["destination"]["name"] or result["deckName"]
            deck_id = _new_destination(col, name)
            created_deck = deck_id
            created_deck_name = col.decks.get(DeckId(deck_id), default=False)["name"]
            store.record_artifacts(request["requestId"], deck_id=created_deck, deck_name=created_deck_name)
            resolved_request = {**request, "destination": {"mode": "existing", "deckId": deck_id}}
            resolved_result = {**result, "deckName": None}
        else:
            resolved_request, resolved_result = request, result
        insert_basic_cards(col, resolved_request, resolved_result, notetype_id, _rollback=False)
        outcome = _outcome(col, request["requestId"], request["cardCount"])
        if outcome is None:
            raise ValidationError("Inserted job could not be verified")
        changes = col.merge_undo_entries(target)
    except Exception:
        # Even an empty custom entry is ours. Merge only changes since that
        # marker, then undo once; never compensate by deleting user decks.
        if target is not None:
            try:
                col.merge_undo_entries(target)
                col.undo()
            except Exception:
                # Some backend failures clear the undo stack even though the
                # failed add_notes transaction itself rolls back. Remove only
                # this job's tagged notes and its newly created empty artifacts.
                try:
                    _compensate(col, request["requestId"], created_deck, created_type,
                                expected_deck_name=created_deck_name, expected_type=created_type_fingerprint)
                except Exception as error:
                    store.resolve(request["requestId"], "uncertain")
                    coordinator.finish(profile, request["requestId"], success=False)
                    raise ValidationError("Cleanup could not be confirmed; reconcile this job before retrying") from error
        coordinator.finish(profile, request["requestId"], success=False)
        store.resolve(request["requestId"], "not_committed")
        raise
    # A persistence error after the collection commits is an uncertain response,
    # not permission to insert again. The pending record and tags survive it.
    coordinator.finish(profile, request["requestId"], success=True)
    store.resolve(request["requestId"], "committed", outcome)
    store.update_settings(notetypeId=notetype_id)
    return {"changes": changes, "state": "committed", "outcome": outcome}
