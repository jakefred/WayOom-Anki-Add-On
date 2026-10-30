"""Atomic, allowlisted nonsecret persistence under the add-on's user_files."""

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from threading import RLock

from .validation import PROVIDERS, ValidationError

_LOCK = RLock()
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


def job_tag(request_id):
    if type(request_id) is not str or not _ID.fullmatch(request_id):
        raise ValidationError("Invalid job ID")
    return f"wayoom::job::{request_id}"


class ProfileStore:
    def __init__(self, user_files, profile):
        if type(profile) is not str or not profile:
            raise ValidationError("Expected a profile identity")
        self.profile = profile
        self.directory = Path(user_files) / sha256(profile.encode("utf-8")).hexdigest()

    @staticmethod
    def _read(path, default):
        try:
            with path.open(encoding="utf-8") as source:
                return json.load(source)
        except FileNotFoundError:
            return deepcopy(default)
        except (ValueError, UnicodeError) as error:
            raise ValidationError("WayOom local data needs repair") from error

    @staticmethod
    def _write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=".write-", delete=False) as output:
                temporary = Path(output.name)
                json.dump(value, output, ensure_ascii=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def settings(self):
        with _LOCK:
            value = self._read(self.directory / "settings.json", {})
            self._check_settings(value)
            return value

    @staticmethod
    def _check_settings(value):
        if type(value) is not dict or set(value) - {
            "panelVisible", "notetypeId", "ownedNotetypeIds", "activeProviderId", "setupComplete", "providers"
        }:
            raise ValidationError("Unsupported settings fields")
        for key in ("panelVisible", "setupComplete"):
            if key in value and type(value[key]) is not bool:
                raise ValidationError("Invalid settings flag")
        if "notetypeId" in value and (type(value["notetypeId"]) is not int or value["notetypeId"] <= 0):
            raise ValidationError("Invalid stored note-type ID")
        if "ownedNotetypeIds" in value and (type(value["ownedNotetypeIds"]) is not list
                or any(type(item) is not int or item <= 0 for item in value["ownedNotetypeIds"])):
            raise ValidationError("Invalid owned note-type IDs")
        if ("activeProviderId" in value and value["activeProviderId"] is not None
                and (type(value["activeProviderId"]) is not str or value["activeProviderId"] not in PROVIDERS)):
            raise ValidationError("Invalid stored provider")
        providers = value.get("providers", {})
        if type(providers) is not dict or set(providers) - PROVIDERS:
            raise ValidationError("Invalid provider settings")
        for provider in providers.values():
            if type(provider) is not dict or set(provider) - {"credentialRef", "modelId", "connectionStatus"}:
                raise ValidationError("Only nonsecret provider references may be saved")
            for key, item in provider.items():
                if type(item) is not str or not item or len(item) > 256:
                    raise ValidationError("Invalid provider setting")
                if key == "connectionStatus" and item not in {"untested", "verified"}:
                    raise ValidationError("Invalid connection status")

    def update_settings(self, **patch):
        with _LOCK:
            value = {**self.settings(), **deepcopy(patch)}
            self._check_settings(value)
            self._write(self.directory / "settings.json", value)

    def _job_path(self, request_id):
        job_tag(request_id)
        return self.directory / "jobs" / f"{request_id}.json"

    def job(self, request_id):
        with _LOCK:
            value = self._read(self._job_path(request_id), None)
            if value is not None and (type(value) is not dict or value.get("requestId") != request_id):
                raise ValidationError("Invalid pending-job record")
            return value

    def jobs(self):
        with _LOCK:
            return [self.job(path.stem) for path in sorted((self.directory / "jobs").glob("*.json"))]

    def prepare(self, request):
        """Do not persist topic, drafts, keys, or provider request/response bodies."""
        record = {"requestId": request["requestId"], "cardCount": request["cardCount"],
                  "destination": deepcopy(request["destination"]),
                  "providerId": request["providerId"], "state": "pending"}
        with _LOCK:
            prior = self.job(request["requestId"])
            if prior is not None:
                raise ValidationError("This job already has a saved commit record")
            self._write(self._job_path(request["requestId"]), record)
        return record

    def resolve(self, request_id, state, outcome=None):
        if state not in {"committed", "not_committed", "undone", "uncertain", "acknowledged"}:
            raise ValidationError("Invalid commit state")
        if outcome is not None and (type(outcome) is not dict or set(outcome) - {
            "requestId", "count", "deckId", "deckName", "notetypeId", "noteIds", "cardIds", "jobTag"
        }):
            raise ValidationError("Unsupported completion fields")
        with _LOCK:
            value = self.job(request_id)
            if value is None:
                raise ValidationError("No pending-job record")
            value["state"] = state
            if outcome is not None:
                value["outcome"] = deepcopy(outcome)
            self._write(self._job_path(request_id), value)

    def acknowledge(self, request_id):
        """Resolve a user's inspection without touching any collection data."""
        with _LOCK:
            record = self.job(request_id)
            if record is None or record["state"] != "uncertain":
                raise ValidationError("Only uncertain jobs can be acknowledged")
            self.resolve(request_id, "acknowledged")

    def prune_history(self, keep=100):
        """Bound terminal history; unresolved commit evidence is never removed."""
        with _LOCK:
            paths = [self._job_path(record["requestId"]) for record in self.jobs()
                     if record["state"] not in {"pending", "uncertain"}]
            paths.sort(key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)
            for path in paths[keep:]:
                path.unlink()

    def record_artifacts(self, request_id, *, deck_id=None, notetype_id=None,
                         deck_name=None, notetype_fingerprint=None):
        with _LOCK:
            value = self.job(request_id)
            if value is None or value["state"] != "pending":
                raise ValidationError("No pending artifact record")
            for key, item in (("createdDeckId", deck_id), ("createdTypeId", notetype_id)):
                if item is not None:
                    if type(item) is not int or item <= 0:
                        raise ValidationError("Invalid artifact ID")
                    value[key] = item
            if deck_name is not None:
                if type(deck_name) is not str or not deck_name or len(deck_name) > 100:
                    raise ValidationError("Invalid artifact name")
                value["createdDeckName"] = deck_name
            if notetype_fingerprint is not None:
                if type(notetype_fingerprint) is not str or not re.fullmatch(r"[0-9a-f]{64}", notetype_fingerprint):
                    raise ValidationError("Invalid artifact fingerprint")
                value["createdTypeFingerprint"] = notetype_fingerprint
            self._write(self._job_path(request_id), value)
