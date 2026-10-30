"""UI entry point for the insertion adapter; no AI network calls here."""

from copy import deepcopy

from .validation import ValidationError, validate_generation, validate_request


def add_cards_in_background(parent, expected_collection, request, result, *,
                            coordinator, store, profile, on_complete=None, on_failure=None):
    from aqt.operations import CollectionOp
    from anki.collection import OpChanges
    from .cards import commit_generation

    request = validate_request(deepcopy(request))
    result = validate_generation(request, deepcopy(result))

    completed = {}

    def add(col):
        if col is not expected_collection:
            raise ValidationError("The active Anki profile changed")
        outcome = commit_generation(col, request, result, store, coordinator, profile)
        completed.update(outcome)
        return outcome["changes"] if outcome["changes"] is not None else OpChanges()

    operation = CollectionOp(parent=parent, op=add)
    if on_complete is not None:
        operation.success(lambda _changes: on_complete(completed))
    if on_failure is not None:
        operation.failure(on_failure)
    return operation.run_in_background()


def recover_in_background(parent, expected_collection, *, coordinator, store, profile,
                          on_complete, on_failure):
    from aqt.operations import CollectionOp
    from anki.collection import OpChanges
    from .cards import reconcile_pending_jobs

    recovered = {}

    def recover(col):
        if col is not expected_collection:
            raise ValidationError("The active Anki profile changed")
        coordinator.require_profile(profile, col)
        recovered.update(reconcile_pending_jobs(col, store))
        changes = OpChanges()
        for item in recovered.values():
            changes.MergeFrom(item["changes"])
        return changes

    return (CollectionOp(parent=parent, op=recover)
            .success(lambda _changes: on_complete(recovered))
            .failure(on_failure).run_in_background())


def repair_type_in_background(parent, expected_collection, *, coordinator, store, profile,
                              on_complete, on_failure):
    from aqt.operations import CollectionOp
    from .cards import repair_basic_notetype

    def repair(col):
        if col is not expected_collection:
            raise ValidationError("The active Anki profile changed")
        coordinator.require_profile(profile, col)
        marker = col.add_custom_undo_entry("WayOom: Repair note type")
        repair_basic_notetype(col, store)
        return col.merge_undo_entries(marker)

    return (CollectionOp(parent=parent, op=repair)
            .success(lambda _changes: on_complete())
            .failure(on_failure).run_in_background())
