"""Test-only fake HTTPS injection into the actual owner peer; no live traffic."""
import json
from pathlib import Path
import os
import re
import socket
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import verification_peer

FAKE_KEY = "fake-environment-key-for-owner-peer"
count = 0


class FakeHTTPS:
    sock = None
    def __init__(self, host, timeout): pass
    def connect(self): pass
    def close(self): pass
    def request(self, method, path, body, headers):
        assert method == "POST" and path == "/v1/responses"
        assert any(FAKE_KEY in value for value in headers.values())
        assert FAKE_KEY not in body.decode()
        self.body = json.loads(body)
    def getresponse(self):
        global count
        prompt = json.dumps(self.body, ensure_ascii=False).replace('\\"', '"')
        requested = re.search(r"Return exactly (\d+) new cards", prompt)
        if 'Return only {"deckName"' in prompt:
            result = {"deckName": "Owner peer sample"}
        elif requested:
            cards = []
            for _ in range(int(requested[1])):
                count += 1
                cards.append({"front": f"Peer question {count}", "back": "Answer"})
            result = {"cards": cards}
        else:
            result = {"ok": True}
        text = json.dumps(result)
        body = json.dumps({"id": "resp_fake", "object": "response", "created_at": 1, "model": "fixture",
                           "status": "completed", "incomplete_details": None,
                           "output": [{"type": "message", "role": "assistant", "id": "msg_fake", "status": "completed",
                                       "content": [{"type": "output_text", "text": text, "annotations": []}]}],
                           "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}).encode()
        return type("Response", (), {"status": 200, "getheader": lambda self, name, default=None: default,
                                     "read": lambda self, size: body[:size]})()


class NoSocket(socket.socket):
    def __new__(cls, *args, **kwargs): raise AssertionError("Owner-tool test must be socket-free")


os.environ[verification_peer.KEY_ENV["openai"]] = FAKE_KEY
with patch.object(verification_peer, "HTTPSConnection", FakeHTTPS), patch("socket.socket", NoSocket):
    verification_peer.main(["--live", "--provider", "openai", "--model", "fixture"])
assert verification_peer.KEY_ENV["openai"] not in os.environ
