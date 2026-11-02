"""Application lifecycle and authorized entry points for the generation UI."""
from hashlib import sha256
import json

from .jobs import JobCoordinator
from .operations import add_cards_in_background, recover_in_background, repair_type_in_background
from .storage import ProfileStore, job_tag
from .validation import ValidationError
from .credentials import CredentialService, CredentialError
from .models import model_id


class ApplicationRuntime:
    def __init__(self, user_files, parent, credentials=None):
        self.user_files = user_files
        self.parent = parent
        self.coordinator = JobCoordinator()
        self.credentials = credentials if credentials is not None else CredentialService()
        self.configuration = {"providers": {}, "activeProviderId": None, "setupRequired": True}
        self.profile = self.collection = self.store = None
        self.ready = False
        self.recovered = {}
        self.outcome = None
        self.error = None
        self.failure_action = None
        self.repairing = False
        self._epoch = 0

    def open_profile(self, profile, collection):
        self.close_profile()
        self.profile, self.collection = profile, collection
        self.store = ProfileStore(self.user_files, profile)
        self.refresh_configuration()
        self.coordinator.open_profile(profile, collection)
        epoch = self._epoch

        def complete(recovered):
            if epoch == self._epoch:
                self.recovered = recovered
                self.ready = True

        def failure(_error):
            if epoch == self._epoch:
                self.ready = False
                self.error = "Previous jobs could not be recovered. Reopen the profile to retry."
                self.failure_action = "reopen-profile"

        return recover_in_background(self.parent, collection, coordinator=self.coordinator,
                                     store=self.store, profile=profile,
                                     on_complete=complete, on_failure=failure)

    def close_profile(self):
        self._epoch += 1
        if self.profile is not None:
            self.coordinator.close_profile(self.profile)
        if self.store is not None:
            self.credentials.close_profile(self.store)
        self.profile = self.collection = self.store = None
        self.ready = False
        self.recovered = {}
        self.outcome = None
        self.error = None
        self.failure_action = None
        self.repairing = False
        self.configuration = {"providers": {}, "activeProviderId": None, "setupRequired": True}

    def refresh_configuration(self):
        if self.store is not None:
            self.configuration = self.credentials.summary(self.store)

    def settings_guard(self):
        job = self.coordinator.snapshot(self.profile)
        if job and job["state"] in {"generating", "validating", "adding"}:
            raise CredentialError("Cancel or finish the current generation before changing settings")

    def start_configured_job(self, request):
        """Production generation entry: Python selects nonsecret provider references."""
        self.refresh_configuration()
        active = self.configuration["activeProviderId"]
        if self.configuration["setupRequired"] or not active:
            raise CredentialError("Setup required: configure an available provider key")
        config = self.store.settings()["providers"][active]
        settings = {key: config[key] for key in ("credentialRef", "modelId") if key in config}
        settings["modelId"] = model_id(active, config)
        return self.start_job({**request, "providerId": active}, settings)

    def start_job(self, request, settings):
        if not self.ready or self.repairing:
            raise ValidationError("Wait for previous jobs to be reconciled before generating")
        if any(item["state"] == "uncertain" for item in self.recovered.values()):
            raise ValidationError("A previous job needs inspection before generating")
        if any(item["state"] in {"pending", "uncertain"} for item in self.store.jobs()):
            raise ValidationError("A previous commit must be reconciled before generating")
        self.store.prune_history()
        job = self.coordinator.start(self.profile, request, settings)
        self.outcome = None
        self.error = self.failure_action = None
        return job

    def fail_generation(self, request_id, category, stage):
        job = self.coordinator.snapshot(self.profile)
        if not job or job["request"]["requestId"] != request_id or job["state"] in {"adding", "complete"}:
            raise ValidationError("Unknown or non-generating job")
        if job["state"] in {"generating", "validating"}:
            self.coordinator.fail(self.profile, request_id, category=category)
        if job["state"] != "canceled":
            self.error = f"{stage.capitalize()} failed ({category})."
            self.failure_action = "settings" if category in {"authentication", "credentials", "model", "quota"} else "retry"

    def add_cards(self, request, result, on_complete=None, on_failure=None):
        if not self.ready or self.repairing:
            raise ValidationError("Job recovery must finish before insertion")
        epoch = self._epoch
        request_id = request["requestId"]
        saved = self.store.job(request_id)
        if saved is None:
            self.coordinator.begin_validation(self.profile, request["requestId"])

        def complete(completed):
            if epoch == self._epoch:
                self.outcome = completed.get("outcome")
                if on_complete:
                    on_complete(completed)

        def failure(error, stage="Adding cards"):
            if epoch == self._epoch:
                from .cards import NoteTypeRepairRequired
                record = self.store.job(request_id)
                if record and record["state"] == "committed":
                    self.outcome = record.get("outcome")
                    self.error = self.failure_action = None
                    if on_complete:
                        on_complete({"state": "committed", "outcome": self.outcome})
                    return
                job = self.coordinator.snapshot(self.profile)
                if job and job["request"]["requestId"] == request_id:
                    if job["state"] in {"generating", "validating"}:
                        self.coordinator.fail(self.profile, request_id)
                    elif job["state"] == "adding":
                        self.coordinator.finish(self.profile, request_id, success=False)
                if record and record["state"] in {"pending", "uncertain"}:
                    self.error = "Adding cards could not be confirmed. Reopen the profile to inspect the saved job."
                    self.failure_action = "reopen-profile"
                else:
                    reason = str(error) if isinstance(error, ValidationError) else "The operation failed."
                    self.error = f"{stage} failed: {reason}"
                    self.failure_action = "repair-note-type" if isinstance(error, NoteTypeRepairRequired) else "retry"
                if on_failure:
                    on_failure()

        try:
            return add_cards_in_background(self.parent, self.collection, request, result,
                                           coordinator=self.coordinator, store=self.store,
                                           profile=self.profile, on_complete=complete, on_failure=failure)
        except Exception as error:
            failure(error, "Validating cards")
            raise

    def acknowledge_job(self, request_id):
        if not self.ready or self.store is None:
            raise ValidationError("Job recovery must finish before acknowledgment")
        self.store.acknowledge(request_id)
        self.recovered.pop(request_id, None)

    def repair_note_type(self):
        if not self.ready or self.repairing or self.store is None:
            raise ValidationError("Wait for recovery or repair to finish")
        job = self.coordinator.snapshot(self.profile)
        if job and job["state"] in {"generating", "validating", "adding"}:
            raise ValidationError("Wait for the current job to finish")
        if any(item["state"] in {"pending", "uncertain"} for item in self.store.jobs()):
            raise ValidationError("Inspect unresolved jobs before repairing")
        epoch = self._epoch
        self.repairing = True

        def complete():
            if epoch == self._epoch:
                self.repairing = False
                self.error = self.failure_action = None

        def failure(_error):
            if epoch == self._epoch:
                self.repairing = False
                self.error = "Note-type repair failed. Retry the repair."
                self.failure_action = "repair-note-type"

        try:
            return repair_type_in_background(self.parent, self.collection,
                                             coordinator=self.coordinator, store=self.store,
                                             profile=self.profile, on_complete=complete, on_failure=failure)
        except Exception as error:
            failure(error)
            raise

    def view_cards(self, request_id):
        if not self.ready:
            raise ValidationError("No active profile")
        record = self.store.job(request_id)
        if record is None:
            raise ValidationError("Unknown job")
        from aqt import dialogs
        browser = dialogs.open("Browser", self.parent)
        browser.search_for(f"tag:{job_tag(request_id)}")
        return browser

    def panel_state(self):
        state = self._panel_state()
        settings = self.store.settings() if self.store else {}
        # Changes to model/key references invalidate drafts without exposing the
        # references or profile identity to the form. Availability matters too.
        active = self.configuration["activeProviderId"]
        provider_config = settings.get("providers", {}).get(active, {})
        configuration = {"active": active, "credentialRef": provider_config.get("credentialRef"),
                         "modelId": model_id(active, provider_config) if active else None,
                         "available": self.configuration["providers"].get(active, {}).get("available", False)}
        configuration_token = sha256(json.dumps(configuration, sort_keys=True).encode()).hexdigest()
        unresolved = self.store and any(item["state"] in {"pending", "uncertain"} for item in self.store.jobs())
        can_generate = bool(self.profile is not None and self.ready and not self.repairing
                            and not unresolved and not self.configuration["setupRequired"]
                            and state["state"] not in {"generating", "validating", "adding"}
                            and state.get("action") not in {"inspect", "reopen-profile", "repair-note-type"})
        if state["state"] == "ready" and self.configuration["setupRequired"]:
            state["message"] = "Setup required. Open Settings to configure a provider."
        return {**state, "setupRequired": self.configuration["setupRequired"],
                "configuredProvider": self.configuration["activeProviderId"],
                "profileEpoch": self._epoch, "profileReady": self.profile is not None and self.ready,
                "configurationToken": configuration_token, "canGenerate": can_generate}

    def _panel_state(self):
        recovery_jobs = [item["requestId"] for item in self.store.jobs()
                         if item["state"] == "uncertain"] if self.store and self.ready else []
        if recovery_jobs:
            return {"state": "failed", "message": "Inspect previous jobs, then acknowledge them to enable generation. Acknowledgment deletes no cards.",
                    "canView": False, "recoveryJobs": recovery_jobs, "action": "inspect"}
        if self.repairing:
            return {"state": "repairing", "message": "Creating a replacement note type…", "canView": False}
        if self.error:
            return {"state": "failed", "message": self.error, "canView": False,
                    "action": self.failure_action}
        if self.profile is None:
            return {"state": "ready", "message": "Ready", "canView": False}
        if not self.ready:
            return {"state": "recovering", "message": "Checking previous jobs…", "canView": False}
        job = self.coordinator.snapshot(self.profile)
        if job is None:
            return {"state": "ready", "message": "Ready", "canView": False}
        state = job["state"]
        labels = {"generating": "Generating", "validating": "Validating", "adding": "Adding cards — finishing",
                  "complete": "Complete", "failed": "Failed", "canceled": "Canceled"}
        message = labels[state]
        if state == "failed":
            message = "Generation timed out." if job.get("failureCategory") == "timeout" else "Generation failed."
            return {"state": state, "message": message, "canView": False,
                    "provider": job["request"]["providerId"], "action": "retry"}
        if state == "complete" and self.outcome:
            message = f"Added {self.outcome['count']} cards to {self.outcome['deckName']}"
        elif state in {"generating", "validating"}:
            if job["stage"] == "naming":
                message = "Naming deck"
            else:
                message = f"{labels[state]} — batch {job['batch'] + 1} of {(job['request']['cardCount'] + 24) // 25}; {job['acceptedCount']} validated drafts"
        return {"state": state, "message": message, "canView": bool(self.outcome),
                "provider": job["request"]["providerId"]}
