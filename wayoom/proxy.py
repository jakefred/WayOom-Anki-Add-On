"""Bounded non-streaming HTTPS transport. No provider request/response formats."""
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPSConnection
import json
from pathlib import Path
import re
import socket
from threading import Event, RLock
from urllib.parse import quote, urlsplit
from .credentials import CredentialError

PLACEHOLDER = "wayoom-bridge-placeholder"
MAX_REQUEST = 1024 * 1024
MAX_RESPONSE = 1024 * 1024
ROUTES = json.loads(Path(__file__).with_name("provider_routes.json").read_text(encoding="utf-8"))


class ProxyError(Exception):
    def __init__(self, category):
        super().__init__(category)
        self.category = category


def validate_http(provider, request):
    if type(request) is not dict or set(request) != {"url", "method", "headers", "body"}:
        raise ProxyError("refused")
    url, headers, body = request["url"], request["headers"], request["body"]
    if type(url) is not str or len(url) > 2048 or request["method"] != "POST":
        raise ProxyError("refused")
    route = ROUTES.get(provider)
    try:
        parsed = urlsplit(url)
        if (not route or parsed.scheme != "https" or parsed.netloc != route["host"]
                or parsed.query or parsed.fragment or parsed.username or parsed.password
                or not re.fullmatch(route.get("pathPattern", re.escape(route.get("path", ""))), parsed.path)):
            raise ProxyError("refused")
    except ValueError:
        raise ProxyError("refused") from None
    if type(body) is not str or PLACEHOLDER in body:
        raise ProxyError("refused")
    try:
        encoded = body.encode("utf-8")
    except UnicodeError:
        raise ProxyError("refused") from None
    if len(encoded) > MAX_REQUEST:
        raise ProxyError("oversized")
    if type(headers) is not dict or len(headers) > 40:
        raise ProxyError("refused")
    checked, found = {}, False
    for name, value in headers.items():
        if (type(name) is not str or not re.fullmatch(r"[A-Za-z0-9!#$&^_.+-]{1,100}", name)
                or type(value) is not str or len(value) > 8192
                or any(ord(c) < 32 or ord(c) > 126 for c in value)):
            raise ProxyError("refused")
        name = name.lower()
        if (name in checked or name in {"host", "content-length", "connection", "transfer-encoding",
                                       "cookie", "content-encoding", "accept-encoding"}
                or name.startswith(("proxy-", "sec-"))):
            raise ProxyError("refused")
        found |= PLACEHOLDER in value
        checked[name] = value
    if not found or sum(len(k) + len(v) for k, v in checked.items()) > 16384:
        raise ProxyError("refused")
    checked["accept-encoding"] = "identity"
    return parsed.hostname, parsed.path, checked, encoded


def redact(value, key):
    variants = {key, json.dumps(key, ensure_ascii=True)[1:-1],
                json.dumps(key, ensure_ascii=False)[1:-1], quote(key, safe="")}
    for variant in sorted(variants, key=len, reverse=True):
        if variant:
            value = value.replace(variant, "[redacted]")
    return value


def scrub_body(value, key):
    """Generic JSON sanitization handles mixed/uppercase Unicode escapes too.

    This does not interpret provider fields; provider parsing stays in the SDK.
    """
    try:
        parsed = json.loads(value)
    except ValueError:
        return redact(value, key)
    def scrub(item, depth=0):
        if depth > 64:
            raise ProxyError("malformed")
        if isinstance(item, str):
            # Structured output can itself be an encoded JSON string inside
            # the provider envelope. Scrub those layers before the SDK parses it.
            try:
                nested = json.loads(item)
            except ValueError:
                return redact(item, key)
            if isinstance(nested, (dict, list, str)):
                return json.dumps(scrub(nested, depth + 1), ensure_ascii=True)
            return redact(item, key)
        if isinstance(item, list):
            return [scrub(child, depth + 1) for child in item]
        if isinstance(item, dict):
            return {redact(name, key): scrub(child, depth + 1) for name, child in item.items()}
        return item
    return json.dumps(scrub(parsed), ensure_ascii=True)


class Transfer:
    def __init__(self):
        self.canceled = Event()
        self._lock = RLock()
        self._connection = None
        self._socket = None

    def attach(self, connection):
        with self._lock:
            self._connection = connection
            if self.canceled.is_set():
                try:
                    connection.close()
                except Exception:
                    pass
                raise ProxyError("canceled")

    def cancel(self):
        self.canceled.set()
        with self._lock:
            connection = self._connection
            if connection:
                try:
                    active_socket = self._socket or connection.sock
                    if active_socket:
                        active_socket.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    connection.close()
                except Exception:
                    pass

    def connected(self):
        with self._lock:
            self._socket = self._connection.sock
            if self.canceled.is_set():
                self.cancel()
                raise ProxyError("canceled")


class ProviderProxy:
    def __init__(self, credentials, connection_factory=HTTPSConnection, executor=None):
        self.credentials = credentials
        self.connection_factory = connection_factory
        self.executor = executor if executor is not None else ThreadPoolExecutor(max_workers=4, thread_name_prefix="wayoom-provider")

    def execute(self, store, provider, reference, checked, transfer, timeout):
        """Returns only scrubbed data or a fixed category; never raises raw errors."""
        connection = None
        key = None
        headers = None
        try:
            host, path, original_headers, body = checked
            if transfer.canceled.is_set():
                raise ProxyError("canceled")
            key = self.credentials.read(store, provider, reference)
            headers = {name: value.replace(PLACEHOLDER, key) for name, value in original_headers.items()}
            connection = self.connection_factory(host, timeout=timeout)
            transfer.attach(connection)
            connection.connect()
            # Never let http.client reconnect automatically after cancellation.
            connection.auto_open = 0
            transfer.connected()
            if transfer.canceled.is_set():
                raise ProxyError("canceled")
            connection.request("POST", path, body=body, headers=headers)
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise ProxyError("redirect")
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise ProxyError("refused")
            length = response.getheader("Content-Length")
            if length is not None and (int(length) < 0 or int(length) > MAX_RESPONSE):
                raise ProxyError("oversized")
            data = response.read(MAX_RESPONSE + 1)
            if len(data) > MAX_RESPONSE:
                raise ProxyError("oversized")
            if transfer.canceled.is_set():
                raise ProxyError("canceled")
            sanitized = scrub_body(data.decode("utf-8"), key)
            if len(sanitized.encode("utf-8")) > MAX_RESPONSE:
                raise ProxyError("oversized")
            return {"status": response.status, "headers": {"content-type": "application/json"}, "body": sanitized}
        except ProxyError as error:
            return {"error": error.category}
        except (TimeoutError, socket.timeout):
            return {"error": "timeout"}
        except CredentialError:
            return {"error": "credentials"}
        except (UnicodeError, ValueError, RecursionError):
            return {"error": "malformed"}
        except Exception:
            # No traceback/provider exception is passed to Qt, JS, or logging.
            return {"error": "canceled" if transfer.canceled.is_set() else "network"}
        finally:
            key = headers = None
            if connection:
                try:
                    connection.close()
                except Exception:
                    pass

    def close(self):
        self.executor.shutdown(wait=False, cancel_futures=True)
