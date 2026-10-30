"""Profile-bound job authorization. No network or collection access lives here."""

from copy import deepcopy
from dataclasses import dataclass, field
from threading import RLock
from time import monotonic
from uuid import uuid4

from .validation import ValidationError, validate_generated_deck_name, validate_request


@dataclass
class _Job:
    request: dict
    settings: dict
    session: str
    deadline: float
    state: str = "generating"
    calls: dict = field(default_factory=dict)
    aborts: list = field(default_factory=list)
    name_ready: bool = False
    generated_name: str | None = None


class JobCoordinator:
    def __init__(self, clock=monotonic):
        self._clock = clock
        self._lock = RLock()
        self._sessions = {}
        self._jobs = {}

    def open_profile(self, profile, collection):
        self.close_profile(profile)
        with self._lock:
            self._jobs.pop(profile, None)
            self._sessions[profile] = (uuid4().hex, collection)

    def close_profile(self, profile):
        with self._lock:
            self._sessions.pop(profile, None)
            job = self._jobs.get(profile)
            if job and job.state not in {"complete", "failed", "canceled"}:
                job.state = "canceled"
            aborts = list(job.aborts) if job else []
            if job:
                job.aborts.clear()
        self._abort(aborts)

    @staticmethod
    def _abort(callbacks):
        for callback in callbacks:
            try:
                callback()
            except Exception:
                # Abort errors must not expose a provider exception or a secret.
                pass

    def start(self, profile, request, settings):
        # Only nonsecret references/model choices may be snapshotted.
        if type(settings) is not dict or set(settings) - {"credentialRef", "modelId"} or any(
            type(v) is not str or not v or len(v) > 256 for v in settings.values()
        ):
            raise ValidationError("Expected nonsecret model and credential references")
        request = validate_request({**deepcopy(request), "requestId": uuid4().hex})
        with self._lock:
            if profile not in self._sessions:
                raise ValidationError("No active profile")
            prior = self._jobs.get(profile)
            if prior and prior.state in {"generating", "validating"} and self._clock() >= prior.deadline:
                prior.state = "failed"
                self._abort(prior.aborts)
                prior.aborts.clear()
            if prior and prior.state in {"generating", "validating", "adding"}:
                raise ValidationError("A job is already running in this profile")
            session = self._sessions[profile][0]
            needs_name = request["destination"] == {"mode": "new", "name": None}
            self._jobs[profile] = _Job(request, deepcopy(settings), session,
                                      self._clock() + 600, name_ready=not needs_name)
            return self.snapshot(profile)

    def _require(self, profile, request_id, states):
        job = self._jobs.get(profile)
        session = self._sessions.get(profile)
        if not job or not session or job.session != session[0] or job.request["requestId"] != request_id:
            raise ValidationError("Unknown or stale job")
        if job.state not in states:
            raise ValidationError("Job is not authorized for this action")
        if job.state != "adding" and self._clock() >= job.deadline:
            job.state = "failed"
            self._abort(job.aborts)
            job.aborts.clear()
            raise ValidationError("Job timed out")
        return job

    def snapshot(self, profile):
        with self._lock:
            job = self._jobs.get(profile)
            if not job:
                return None
            if job.state in {"generating", "validating"} and self._clock() >= job.deadline:
                job.state = "failed"
                self._abort(job.aborts)
                job.aborts.clear()
            count = job.request["cardCount"]
            limit = 3 * ((count + 24) // 25)
            if job.request["destination"] == {"mode": "new", "name": None}:
                limit += 3
            return deepcopy({"request": job.request, "settings": job.settings,
                             "state": job.state, "callLimit": limit,
                             "callsUsed": sum(job.calls.values()), "deckName": job.generated_name})

    def require_profile(self, profile, collection):
        with self._lock:
            session = self._sessions.get(profile)
            if not session or session[1] is not collection:
                raise ValidationError("The active Anki profile changed")

    def consume_call(self, profile, request_id, provider_id, *, stage="cards", batch=0):
        """Reserve an attempt BEFORE transport; failed attempts are not refunded."""
        with self._lock:
            job = self._require(profile, request_id, {"generating"})
            if provider_id != job.request["providerId"]:
                raise ValidationError("Provider does not match this job")
            if stage == "naming":
                if job.name_ready or type(batch) is not int or batch != 0:
                    raise ValidationError("Naming is not required or already finished")
            elif stage == "cards":
                if not job.name_ready or type(batch) is not int or not 0 <= batch < (job.request["cardCount"] + 24) // 25:
                    raise ValidationError("Invalid batch or naming is unfinished")
            else:
                raise ValidationError("Unknown provider stage")
            key = (stage, batch)
            if job.calls.get(key, 0) >= 3:
                raise ValidationError("Provider attempt limit reached")
            job.calls[key] = job.calls.get(key, 0) + 1
            return deepcopy(job.settings)

    def naming_complete(self, profile, request_id, name):
        with self._lock:
            job = self._require(profile, request_id, {"generating"})
            if job.name_ready or not job.calls.get(("naming", 0)):
                raise ValidationError("Naming is already finished or no attempt was made")
            job.generated_name = validate_generated_deck_name(name)
            job.name_ready = True
            return job.generated_name

    def register_abort(self, profile, request_id, callback):
        with self._lock:
            job = self._require(profile, request_id, {"generating"})
            job.aborts.append(callback)
        return lambda: self._remove_abort(profile, callback)

    def _remove_abort(self, profile, callback):
        with self._lock:
            job = self._jobs.get(profile)
            if job and callback in job.aborts:
                job.aborts.remove(callback)

    def cancel(self, profile, request_id):
        with self._lock:
            job = self._require(profile, request_id, {"generating", "validating"})
            job.state = "canceled"
            aborts, job.aborts = job.aborts, []
        self._abort(aborts)

    def begin_write(self, profile, request_id, collection, request, result=None):
        with self._lock:
            job = self._require(profile, request_id, {"generating", "validating"})
            if self._sessions[profile][1] is not collection or job.request != validate_request(request):
                raise ValidationError("Profile or request changed before insertion")
            if not job.name_ready:
                raise ValidationError("Naming is unfinished")
            if job.generated_name is not None and (result is None or result["deckName"] != job.generated_name):
                raise ValidationError("Generated deck name changed before insertion")
            job.state = "adding"

    def begin_validation(self, profile, request_id):
        with self._lock:
            job = self._require(profile, request_id, {"generating", "validating"})
            job.state = "validating"

    def finish(self, profile, request_id, *, success):
        with self._lock:
            job = self._require(profile, request_id, {"adding"})
            job.state = "complete" if success else "failed"

    def fail(self, profile, request_id):
        with self._lock:
            job = self._require(profile, request_id, {"generating", "validating"})
            job.state = "failed"
            aborts, job.aborts = job.aborts, []
        self._abort(aborts)


class ConnectionTestPermit:
    """Create in Python for each explicit Test connection action, never in JS."""
    def __init__(self):
        self._lock = RLock()
        self._used = False

    def consume(self):
        with self._lock:
            if self._used:
                raise ValidationError("Connection test already used its provider call")
            self._used = True
