"""Python-only secrets. Settings, status, and errors contain references only."""
from hashlib import sha256
import importlib
from pathlib import Path
import platform
import re
import sys
from threading import RLock
from uuid import uuid4

from .storage import ProfileStore
from .validation import PROVIDERS, ValidationError


class CredentialError(ValidationError):
    pass


class ProtectedStorageUnavailable(CredentialError):
    pass


def protected_backend():
    """Never use keyring's configurable/discovered third-party backends."""
    vendor = Path(__file__).resolve().parents[1] / "vendor" / "runtime"
    paths = [vendor / "common"]
    if sys.platform == "linux":
        if platform.machine().lower() not in {"x86_64", "amd64"}:
            raise ProtectedStorageUnavailable("Protected storage is unavailable; session-only storage is available.")
        paths += [vendor / "linux" / "common", vendor / "linux" / f"cp{sys.version_info.major}{sys.version_info.minor}"]
    if any(not path.is_dir() for path in paths):
        raise ProtectedStorageUnavailable("Protected storage is unavailable; session-only storage is available.")
    for path in reversed(paths):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    modules = {"win32": ("keyring.backends.Windows", "WinVaultKeyring"),
               "darwin": ("keyring.backends.macOS", "Keyring"),
               "linux": ("keyring.backends.SecretService", "Keyring")}
    try:
        module, name = modules[sys.platform]
        backend = getattr(importlib.import_module(module), name)()
        if backend.priority <= 0:
            raise RuntimeError()
        return backend
    except Exception:
        raise ProtectedStorageUnavailable("Protected storage is unavailable; session-only storage is available.") from None


class CredentialService:
    def __init__(self, backend_factory=protected_backend):
        self._factory = backend_factory
        self._sessions = {}
        self._lock = RLock()

    @staticmethod
    def _check_provider(provider):
        if type(provider) is not str or provider not in PROVIDERS:
            raise CredentialError("Unknown provider")

    @staticmethod
    def _check_ref(reference):
        if type(reference) is not str or not re.fullmatch(r"(?:os|session):[0-9a-f]{32}", reference):
            raise CredentialError("Invalid credential reference")

    @staticmethod
    def _service(store, provider):
        # Separate installations and profiles, without putting profile names in the OS store.
        installation = sha256(str(store.directory.parent.resolve()).encode()).hexdigest()[:24]
        return f"WayOom/{installation}/{store.directory.name}/{provider}"

    def _session_id(self, store, provider, reference):
        return (str(store.directory.resolve()), provider, reference)

    def _index(self, store):
        entries = store._read(store.directory / "credentials.json", [])
        if type(entries) is not list:
            raise CredentialError("Credential cleanup records need repair")
        for entry in entries:
            if type(entry) is not dict or set(entry) != {"providerId", "credentialRef"}:
                raise CredentialError("Credential cleanup records need repair")
            self._check_provider(entry["providerId"])
            self._check_ref(entry["credentialRef"])
        return entries

    def _remember(self, store, provider, reference):
        entries = self._index(store)
        entry = {"providerId": provider, "credentialRef": reference}
        if entry not in entries:
            store._write(store.directory / "credentials.json", [*entries, entry])

    def _forget(self, store, provider, reference):
        entries = [entry for entry in self._index(store)
                   if entry != {"providerId": provider, "credentialRef": reference}]
        store._write(store.directory / "credentials.json", entries)

    def read(self, store, provider, reference):
        """Call only immediately before a transport request. No fallback to a new ref."""
        self._check_provider(provider)
        self._check_ref(reference)
        with self._lock:
            if reference.startswith("session:"):
                value = self._sessions.get(self._session_id(store, provider, reference))
                if value is None:
                    raise CredentialError("This session-only key is unavailable. Open settings to add a key.")
                return value
            try:
                value = self._factory().get_password(self._service(store, provider), reference)
            except Exception:
                raise ProtectedStorageUnavailable("Protected storage is unavailable. Open settings or use session-only storage.") from None
            if not value:
                raise CredentialError("The saved key is unavailable. Open settings to add a key.")
            return value

    def summary(self, store):
        with self._lock:
            settings = store.settings()
            providers = {}
            for provider, config in settings.get("providers", {}).items():
                available = False
                reference = config.get("credentialRef")
                if reference:
                    try:
                        available = bool(self.read(store, provider, reference))
                    except CredentialError:
                        pass
                providers[provider] = {"available": available,
                                       "storage": "session" if reference and reference.startswith("session:") else "os",
                                       "connectionStatus": config.get("connectionStatus", "untested"),
                                       "modelId": config.get("modelId", "")}
            available = [provider for provider, value in providers.items() if value["available"]]
            active = settings.get("activeProviderId")
            if len(providers) == 1 and len(available) == 1:
                active = available[0]
            elif active not in available:
                active = None
            return {"providers": providers, "activeProviderId": active, "setupRequired": active is None}

    def save_key(self, store, provider, key, *, session_only=False, model_id=None):
        self._check_provider(provider)
        if type(key) is not str or not key.strip() or len(key) > 8192 or any(ord(c) < 32 or ord(c) == 127 for c in key):
            raise CredentialError("Enter a nonblank API key without control characters")
        key = key.strip()
        if model_id is not None and (type(model_id) is not str or not model_id.strip() or len(model_id) > 256
                                     or any(ord(c) < 32 or ord(c) == 127 for c in model_id)):
            raise CredentialError("Enter a valid model ID")
        reference = f"{'session' if session_only else 'os'}:{uuid4().hex}"
        with self._lock:
            settings = store.settings()
            providers = settings.get("providers", {})
            prior = providers.get(provider, {}).get("credentialRef")
            # Journal intent before OS writes: Remove all can find an interrupted save.
            if not session_only:
                self._remember(store, provider, reference)
                try:
                    self._factory().set_password(self._service(store, provider), reference, key)
                except Exception:
                    raise ProtectedStorageUnavailable("Protected storage could not save the key. Select session-only storage to use it for this session.") from None
            else:
                self._sessions[self._session_id(store, provider, reference)] = key
            config = {"credentialRef": reference, "connectionStatus": "untested"}
            selected_model = model_id or providers.get(provider, {}).get("modelId")
            if selected_model:
                config["modelId"] = selected_model.strip()
            providers[provider] = config
            # If this write fails, the cleanup journal still names the OS key.
            active = provider if len(providers) == 1 else settings.get("activeProviderId")
            store.update_settings(providers=providers, activeProviderId=active, setupComplete=True)
            if prior:
                self._delete(store, provider, prior)
            return reference

    def _delete(self, store, provider, reference):
        self._check_ref(reference)
        if reference.startswith("session:"):
            self._sessions.pop(self._session_id(store, provider, reference), None)
        else:
            try:
                backend = self._factory()
                service = self._service(store, provider)
                if backend.get_password(service, reference) is not None:
                    backend.delete_password(service, reference)
                if backend.get_password(service, reference) is not None:
                    raise RuntimeError()
            except Exception:
                raise CredentialError("Key deletion could not be confirmed. Retry removal before uninstalling, or revoke the key with its provider.") from None
            self._forget(store, provider, reference)

    def remove_key(self, store, provider):
        self._check_provider(provider)
        with self._lock:
            settings = store.settings()
            providers = settings.get("providers", {})
            refs = {entry["credentialRef"] for entry in self._index(store) if entry["providerId"] == provider}
            current = providers.get(provider, {}).get("credentialRef")
            if current:
                refs.add(current)
            for reference in sorted(refs):
                self._delete(store, provider, reference)
            providers.pop(provider, None)
            active = settings.get("activeProviderId")
            if active == provider or active not in providers:
                active = next(iter(providers)) if len(providers) == 1 else None
            store.update_settings(providers=providers, activeProviderId=active)

    def select_provider(self, store, provider):
        self._check_provider(provider)
        if not self.summary(store)["providers"].get(provider, {}).get("available"):
            raise CredentialError("Add an available key for the selected provider")
        store.update_settings(activeProviderId=provider, setupComplete=True)

    def remove_all(self, user_files):
        """Includes every profile and orphaned OS writes recorded before a crash."""
        failed = False
        with self._lock:
            root = Path(user_files).resolve()
            self._sessions = {key: value for key, value in self._sessions.items()
                              if Path(key[0]).parent != root}
            for directory in sorted(root.glob("*")):
                if not directory.is_dir() or directory.is_symlink() or not re.fullmatch(r"[0-9a-f]{64}", directory.name):
                    continue
                store = ProfileStore.__new__(ProfileStore)
                store.directory, store.profile = directory, directory.name
                for provider in sorted(PROVIDERS):
                    try:
                        self.remove_key(store, provider)
                    except Exception:
                        failed = True
            if failed:
                raise CredentialError("Some key removals could not be confirmed. Retry Remove all keys before uninstalling, or revoke keys with the providers.") from None

    def close_profile(self, store):
        with self._lock:
            directory = str(store.directory.resolve())
            self._sessions = {key: value for key, value in self._sessions.items() if key[0] != directory}
