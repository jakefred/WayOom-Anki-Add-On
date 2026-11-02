"""Native-only model discovery. Fixed HTTPS routes; no generation or webview keys."""
from http.client import HTTPSConnection
import json
import re
from threading import Event, Timer
from urllib.parse import urlencode

from .credentials import CredentialError
from .models import DEFAULT_MODELS
from .proxy import MAX_RESPONSE, ProxyError, Transfer, scrub_body

# Model listing is separate from the SDK generation proxy and its POST allowlist.
CATALOGS = {
    "openai": ("api.openai.com", "/v1/models", "authorization"),
    "anthropic": ("api.anthropic.com", "/v1/models", "x-api-key"),
    "google": ("generativelanguage.googleapis.com", "/v1beta/models", "x-goog-api-key"),
    "xai": ("api.x.ai", "/v1/language-models", "authorization"),
    "mistral": ("api.mistral.ai", "/v1/models", "authorization"),
}


def detect_provider(key):
    """Hints only. Ambiguous keys require an explicit company, never probing."""
    for prefix, provider in (("sk-ant-", "anthropic"), ("sk-proj-", "openai"),
                             ("sk-svcacct-", "openai"), ("AIza", "google"), ("xai-", "xai")):
        if key.strip().startswith(prefix):
            return provider
    return None


def key_problem(key):
    key = key.strip()
    if not key:
        return "Paste your API key to begin."
    if len(key) > 8192 or any(ord(c) < 33 or ord(c) > 126 for c in key):
        return "The key contains spaces or unsupported characters. Paste the key only."
    return None


def parse_models(provider, payload):
    if type(payload) is not dict:
        raise ProxyError("malformed")
    rows = payload.get("models" if provider in {"google", "xai"} else "data")
    if type(rows) is not list or len(rows) > 3000:
        raise ProxyError("malformed")
    result = []
    for row in rows:
        if type(row) is not dict:
            continue
        model = row.get("name" if provider == "google" else "id")
        if provider == "google" and isinstance(model, str):
            model = model.removeprefix("models/")
        if type(model) is not str or not re.fullmatch(r"[A-Za-z0-9._:-]{1,256}", model):
            continue
        caps = row.get("capabilities") or {}
        if type(caps) is not dict:
            continue
        if provider == "openai" and (not re.match(r"(?:ft:)?(?:gpt-\d|o\d)", model)
                or re.search(r"audio|realtime|transcribe|search|deep-research|instruct", model)):
            continue
        if provider == "anthropic":
            structured = caps.get("structured_outputs") or {}
            if not model.startswith("claude-") or row.get("lifecycle") == "retired" or structured.get("supported") is False:
                continue
        if provider == "google" and ("generateContent" not in row.get("supportedGenerationMethods", [])
                or not model.startswith("gemini-") or re.search(r"image|tts|native-audio|robotics", model)):
            continue
        if provider == "xai" and "text" not in row.get("output_modalities", []):
            continue
        if provider == "mistral" and (caps.get("completion_chat") is not True or row.get("archived")):
            continue
        label = row.get("display_name") or row.get("displayName") or model
        if type(label) is not str or not label.strip() or len(label) > 256 or any(ord(c) < 32 for c in label):
            label = model
        item = {"id": model, "name": label}
        for target, sources in (("inputTokens", ("inputTokenLimit", "max_input_tokens", "max_context_length", "context_length")),
                                ("outputTokens", ("outputTokenLimit", "max_tokens"))):
            value = next((row[name] for name in sources if type(row.get(name)) is int and 0 < row[name] < 100_000_000), None)
            if value:
                item[target] = value
        result.append(item)
    return result


class ModelCatalog:
    def __init__(self, credentials, connection_factory=HTTPSConnection):
        self.credentials, self.connection_factory = credentials, connection_factory

    def fetch(self, store, provider, reference, transfer=None):
        """Run off the main thread. Return only normalized metadata or fixed errors."""
        transfer = transfer or Transfer()
        expired = Event()
        def timeout():
            expired.set()
            transfer.cancel()
        timer = Timer(30, timeout)
        timer.daemon = True
        key = connection = headers = None
        timer.start()
        try:
            if provider not in CATALOGS:
                raise ProxyError("refused")
            host, path, auth = CATALOGS[provider]
            key = self.credentials.read(store, provider, reference)
            headers = {auth: f"Bearer {key}" if auth == "authorization" else key,
                       "accept": "application/json", "accept-encoding": "identity"}
            if provider == "anthropic":
                headers["anthropic-version"] = "2023-06-01"
            params = {"limit": 1000} if provider == "anthropic" else {"pageSize": 1000} if provider == "google" else {}
            models, cursors = {}, set()
            for _page in range(3):
                if transfer.canceled.is_set():
                    raise ProxyError("canceled")
                connection = self.connection_factory(host, timeout=15)
                transfer.attach(connection)
                connection.connect()
                connection.auto_open = 0
                transfer.connected()
                connection.request("GET", path + ("?" + urlencode(params) if params else ""), headers=headers)
                response = connection.getresponse()
                if 300 <= response.status < 400:
                    raise ProxyError("redirect")
                if response.status in {401, 403} or provider == "google" and response.status == 400:
                    raise ProxyError("authentication")
                if response.status == 429:
                    raise ProxyError("rate-limit")
                if response.status != 200:
                    raise ProxyError("provider")
                if response.getheader("Content-Encoding", "identity").lower() != "identity":
                    raise ProxyError("refused")
                length = response.getheader("Content-Length")
                if length is not None and not 0 <= int(length) <= MAX_RESPONSE:
                    raise ProxyError("oversized")
                data = response.read(MAX_RESPONSE + 1)
                if len(data) > MAX_RESPONSE:
                    raise ProxyError("oversized")
                payload = json.loads(scrub_body(data.decode("utf-8"), key))
                connection.close()
                connection = None
                for model in parse_models(provider, payload):
                    models[model["id"]] = model
                cursor = payload.get("nextPageToken") if provider == "google" else (
                    payload.get("last_id") if provider == "anthropic" and payload.get("has_more") else None)
                if not cursor:
                    if transfer.canceled.is_set():
                        raise ProxyError("canceled")
                    default = DEFAULT_MODELS[provider]["modelId"]
                    return {"models": sorted(models.values(), key=lambda m: (m["id"] != default, m["name"].lower()))}
                if type(cursor) is not str or len(cursor) > 2048 or cursor in cursors:
                    raise ProxyError("malformed")
                cursors.add(cursor)
                params["pageToken" if provider == "google" else "after_id"] = cursor
            raise ProxyError("oversized")
        except ProxyError as error:
            return {"error": "timeout" if expired.is_set() else error.category}
        except CredentialError:
            return {"error": "credentials"}
        except TimeoutError:
            return {"error": "timeout"}
        except (ValueError, UnicodeError, RecursionError):
            return {"error": "malformed"}
        except Exception:
            return {"error": "timeout" if expired.is_set() else "canceled" if transfer.canceled.is_set() else "network"}
        finally:
            timer.cancel()
            key = headers = None
            if connection:
                try:
                    connection.close()
                except Exception:
                    pass
