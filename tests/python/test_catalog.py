import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from wayoom.catalog import ModelCatalog, detect_provider, key_problem, parse_models
from wayoom.credentials import CredentialService
from wayoom.proxy import MAX_RESPONSE, Transfer
from wayoom.storage import ProfileStore


class CatalogTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = ProfileStore(Path(directory.name), "fixture")
        self.credentials = CredentialService(lambda: self.fail("No OS vault"))
        self.key = "fake-catalog-secret"

    def fetch(self, provider, replies, transfer=None):
        reference = self.credentials.save_key(self.store, provider, self.key, session_only=True)
        self.requests = []
        def factory(host, timeout):
            reply = replies.pop(0)
            conn = Mock()
            conn.request.side_effect = lambda method, path, headers: self.requests.append((host, method, path, headers))
            response = conn.getresponse.return_value
            response.status = reply.get("status", 200)
            response.getheader.side_effect = lambda name, default=None: reply.get("headers", {}).get(name, default)
            response.read.return_value = reply.get("body", json.dumps(reply.get("json", {})).encode())
            return conn
        return ModelCatalog(self.credentials, factory).fetch(self.store, provider, reference, transfer)

    def test_key_detection_is_local_and_ambiguous_keys_are_not_guessed(self):
        for key, provider in (("sk-proj-example", "openai"), ("sk-ant-api-example", "anthropic"),
                              ("AIzaExample", "google"), ("xai-example", "xai")):
            self.assertEqual(detect_provider(key), provider)
        for key in ("sk-shared-format", "opaque-mistral-key", ""):
            self.assertIsNone(detect_provider(key))
        self.assertIsNotNone(key_problem("key with space"))
        self.assertIsNone(key_problem("opaque-valid-key"))

    def test_five_catalogs_only_send_key_to_selected_company_and_filter_text_models(self):
        fixtures = {
            "openai": ({"data": [{"id": "gpt-5-mini"}, {"id": "gpt-image-1"}, {"id": "whisper-1"}]}, "api.openai.com", "authorization"),
            "anthropic": ({"data": [{"id": "claude-example", "display_name": "Claude Example", "max_input_tokens": 200000}]}, "api.anthropic.com", "x-api-key"),
            "google": ({"models": [{"name": "models/gemini-example", "supportedGenerationMethods": ["generateContent"], "outputTokenLimit": 8192}, {"name": "models/embedding-example", "supportedGenerationMethods": ["embedContent"]}]}, "generativelanguage.googleapis.com", "x-goog-api-key"),
            "xai": ({"models": [{"id": "grok-example", "output_modalities": ["text"]}, {"id": "image", "output_modalities": ["image"]}]}, "api.x.ai", "authorization"),
            "mistral": ({"data": [{"id": "mistral-example", "capabilities": {"completion_chat": True}}, {"id": "embed", "capabilities": {"completion_chat": False}}]}, "api.mistral.ai", "authorization"),
        }
        for provider, (payload, host, auth) in fixtures.items():
            with self.subTest(provider=provider):
                result = self.fetch(provider, [{"json": payload}])
                self.assertEqual(len(result["models"]), 1)
                self.assertEqual(len(self.requests), 1)
                self.assertEqual(self.requests[0][:2], (host, "GET"))
                self.assertIn(self.key, self.requests[0][3][auth])
                self.assertNotIn(self.key, self.requests[0][2])
                self.assertNotIn(self.key, json.dumps(result))

    def test_pagination_is_bounded_and_cursors_cannot_change_host_or_path(self):
        payload = {"models": [{"name": "models/gemini-one", "supportedGenerationMethods": ["generateContent"]}], "nextPageToken": "https://evil.example/?key=x"}
        result = self.fetch("google", [{"json": payload}, {"json": {"models": []}}])
        self.assertEqual(result["models"][0]["id"], "gemini-one")
        self.assertEqual(len(self.requests), 2)
        self.assertIn("pageToken=https%3A%2F%2Fevil.example", self.requests[1][2])
        self.assertEqual(self.requests[1][0], "generativelanguage.googleapis.com")
        result = self.fetch("google", [{"json": payload}, {"json": payload}])
        self.assertEqual(result, {"error": "malformed"})

    def test_rejected_key_redirect_size_and_raw_errors_are_safe(self):
        for reply, category in (({"status": 401}, "authentication"), ({"status": 302}, "redirect"),
                                ({"body": b"x" * (MAX_RESPONSE + 1)}, "oversized"),
                                ({"body": b"not JSON"}, "malformed")):
            self.assertEqual(self.fetch("openai", [reply]), {"error": category})
        reference = self.credentials.save_key(self.store, "openai", self.key, session_only=True)
        def broken(*args, **kwargs): raise RuntimeError(self.key)
        self.assertEqual(ModelCatalog(self.credentials, broken).fetch(self.store, "openai", reference), {"error": "network"})
        result = self.fetch("openai", [{"json": {"data": [{"id": "gpt-5-test", "display_name": self.key}]}}])
        self.assertEqual(len(result["models"]), 1)
        self.assertNotIn(self.key, json.dumps(result))

    def test_cancellation_prevents_network_and_unknown_capabilities_do_not_claim_token_limits(self):
        transfer = Transfer()
        transfer.cancel()
        self.assertEqual(self.fetch("openai", [], transfer), {"error": "canceled"})
        self.assertEqual(self.requests, [])
        self.assertEqual(parse_models("openai", {"data": [{"id": "gpt-5-mini"}]}),
                         [{"id": "gpt-5-mini", "name": "gpt-5-mini"}])
