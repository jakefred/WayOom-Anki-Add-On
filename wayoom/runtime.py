"""Application lifecycle and authorized entry points for the future generator UI."""

from .jobs import JobCoordinator
from .operations import add_cards_in_background, recover_in_background
from .storage import ProfileStore, job_tag
from .validation import ValidationError


class ApplicationRuntime:
    def __init__(self, user_files, parent):
        self.user_files = user_files
        self.parent = parent
        self.coordinator = JobCoordinator()
        self.profile = self.collection = self.store = None
        self.ready = False
        self.recovered = {}
        self.outcome = None
        self.error = None
        self._epoch = 0

    def open_profile(self, profile, collection):
        self.close_profile()
        self.profile, self.collection = profile, collection
        self.store = ProfileStore(self.user_files, profile)
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

        return recover_in_background(self.parent, collection, coordinator=self.coordinator,
                                     store=self.store, profile=profile,
                                     on_complete=complete, on_failure=failure)

    def close_profile(self):
        self._epoch += 1
        if self.profile is not None:
            self.coordinator.close_profile(self.profile)
        self.profile = self.collection = self.store = None
        self.ready = False
        self.recovered = {}
        self.outcome = None
        self.error = None

    def start_job(self, request, settings):
        if not self.ready:
            raise ValidationError("Wait for previous jobs to be reconciled before generating")
        if any(item["state"] == "uncertain" for item in self.recovered.values()):
            raise ValidationError("A previous job needs inspection before generating")
        if any(item["state"] in {"pending", "uncertain"} for item in self.store.jobs()):
            raise ValidationError("A previous commit must be reconciled before generating")
        job = self.coordinator.start(self.profile, request, settings)
        self.outcome = None
        return job

    def add_cards(self, request, result, on_complete=None):
        if not self.ready:
            raise ValidationError("Job recovery must finish before insertion")
        epoch = self._epoch
        saved = self.store.job(request["requestId"])
        if saved is None:
            self.coordinator.begin_validation(self.profile, request["requestId"])

        def complete(completed):
            if epoch == self._epoch:
                self.outcome = completed.get("outcome")
                if on_complete:
                    on_complete(completed)

        def failure(_error):
            if epoch == self._epoch:
                self.ready = False
                self.error = "The job could not finish. Reopen the profile to check its saved result."

        return add_cards_in_background(self.parent, self.collection, request, result,
                                       coordinator=self.coordinator, store=self.store,
                                       profile=self.profile, on_complete=complete, on_failure=failure)

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
        if self.error:
            return {"state": "failed", "message": self.error, "canView": False}
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
        if state == "complete" and self.outcome:
            message = f"Added {self.outcome['count']} cards to {self.outcome['deckName']}"
        return {"state": state, "message": message, "canView": bool(self.outcome),
                "provider": job["request"]["providerId"]}
