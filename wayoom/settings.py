"""Non-Qt settings controller. Secrets are transient method arguments only."""
from .credentials import CredentialError

PROVIDER_INFO = {
    "openai": ("OpenAI", "https://platform.openai.com/api-keys"),
    "anthropic": ("Anthropic", "https://platform.claude.com/settings/keys"),
    "google": ("Google Gemini", "https://aistudio.google.com/apikey"),
    "xai": ("xAI", "https://console.x.ai/"),
    "mistral": ("Mistral AI", "https://console.mistral.ai/api-keys/"),
}

DISCLOSURE = (
    "API usage is billed by your selected provider. Your topic and generated content are processed "
    "by that provider. Fronts of generated cards in the target deck may also be sent to avoid "
    "duplicates; unrelated decks and other card contents are not uploaded as context. "
    "Use a dedicated WayOom API key and set a low spending limit where your provider offers one. "
    "WayOom does not enable or enforce a provider spending limit. "
    "Protected storage uses your operating system's credential store. If unavailable, select "
    "session-only storage explicitly; those keys are forgotten when this profile closes. "
    "Before uninstalling, use Remove all keys. This covers every profile in this installation. "
    "You can revoke keys through the provider links below."
)


class SettingsController:
    def __init__(self, credentials, store, guard=lambda: None):
        self.credentials, self.store, self.guard = credentials, store, guard

    def summary(self):
        return self.credentials.summary(self.store)

    def connect(self, provider, key, session_only=False):
        self.guard()
        if provider not in PROVIDER_INFO:
            raise CredentialError("Choose the company that issued your API key")
        if key.strip():
            from .catalog import key_problem, detect_provider
            problem = key_problem(key)
            if problem:
                raise CredentialError(problem)
            detected = detect_provider(key)
            if detected and detected != provider:
                raise CredentialError("This key looks like it belongs to a different company")
            self.credentials.save_key(self.store, provider, key, session_only=session_only)
        elif not self.summary()["providers"].get(provider, {}).get("available"):
            raise CredentialError("Paste your API key to begin")

    def choose_model(self, provider, model):
        self.guard()
        if not self.summary()["providers"].get(provider, {}).get("available"):
            raise CredentialError("Connect an available key first")
        if type(model) is not str or not model or len(model) > 256 or any(ord(c) < 33 or ord(c) > 126 for c in model):
            raise CredentialError("Choose a model from the dropdown")
        settings = self.store.settings()
        config = settings["providers"][provider]
        from .models import model_id
        if model_id(provider, config) != model:
            config.update(modelId=model, connectionStatus="untested")
            config.pop("verifiedModelId", None)
            self.store.update_settings(providers=settings["providers"])

    def finish(self, provider, model):
        self.guard()
        config = self.summary()["providers"].get(provider, {})
        if not config.get("available") or config.get("connectionStatus") != "verified" or config.get("modelId") != model:
            raise CredentialError("Verify the selected model before finishing setup")
        self.credentials.select_provider(self.store, provider)

    def remove(self, provider):
        self.guard()
        self.credentials.remove_key(self.store, provider)

    def save(self, changes, active):
        self.guard()
        for provider, change in changes.items():
            if provider not in PROVIDER_INFO:
                raise CredentialError("Unknown provider")
            if change.get("key") and change.get("remove"):
                raise CredentialError("Choose either replacement or removal for a provider")
            model = change.get("modelId", "")
            if len(model) > 256 or any(ord(c) < 32 or ord(c) == 127 for c in model):
                raise CredentialError("Model IDs must be at most 256 characters without control characters")
        for provider, change in changes.items():
            if change.get("remove"):
                self.credentials.remove_key(self.store, provider)
            elif change.get("key", "").strip():
                self.credentials.save_key(self.store, provider, change["key"],
                                          session_only=change.get("sessionOnly", False),
                                          model_id=change.get("modelId", "").strip() or None)
            else:
                settings = self.store.settings()
                config = settings.get("providers", {}).get(provider)
                model = change.get("modelId", "").strip()
                if config and config.get("modelId", "") != model:
                    if model:
                        config["modelId"] = model
                    else:
                        config.pop("modelId", None)
                    config["connectionStatus"] = "untested"
                    config.pop("verifiedModelId", None)
                    self.store.update_settings(providers=settings["providers"])
        state = self.summary()
        available = [provider for provider, config in state["providers"].items() if config["available"]]
        if not available:
            raise CredentialError("Setup required: add at least one available provider key")
        if len(available) == 1:
            active = available[0]
        if active not in available:
            raise CredentialError("Choose an available active provider")
        self.credentials.select_provider(self.store, active)

    def remove_all(self):
        self.guard()
        self.credentials.remove_all(self.store.directory.parent)
