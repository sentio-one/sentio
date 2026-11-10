"""A gateway without a business block has no `business.request` verb; say so plainly."""
import contextlib
import json

import pytest

from gateway.business.client import request_business
from gateway.business.models import BusinessProtocolError


class _Peer:
    def __init__(self, reply: bytes):
        self._reply = reply

    def sendall(self, _data):
        pass

    def settimeout(self, _t):
        pass

    def recv(self, _n):
        reply, self._reply = self._reply, b""
        return reply


@pytest.mark.platforms("linux", "macos")
def test_unregistered_business_verb_explains_how_to_enable(tmp_path, monkeypatch):
    (tmp_path / "gateway.sock").touch()
    reply = json.dumps({"ok": False, "error": "unknown verb: 'business.request'", "protocol": 1}).encode() + b"\n"
    monkeypatch.setattr("hermes_cli.gateway_runtime_discovery.connect_private",
                        lambda *_a, **_k: contextlib.nullcontext(_Peer(reply)))

    with pytest.raises(BusinessProtocolError, match=r"platforms\.whatsapp\.extra\.business\.mode"):
        request_business(tmp_path, operation="operator.status", params={})
