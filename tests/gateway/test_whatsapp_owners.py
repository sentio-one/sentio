"""Owner admission for a shared WhatsApp bot."""
from types import SimpleNamespace

import pytest

from gateway.whatsapp_owners import is_owner, owner_gate, parse_owners

OWNERS = ["+27820000001"]


def test_parse_owners():
    assert parse_owners(["+27820000001", " ", 27820000002, None]) == ["+27820000001", "27820000002"]
    assert parse_owners("27820000001") == []
    assert parse_owners(None) == []


@pytest.mark.parametrize("sender", ["27820000001@s.whatsapp.net", "27820000001:7@s.whatsapp.net", "+27820000001"])
def test_owner_aliases(sender):
    assert is_owner(OWNERS, sender)


@pytest.mark.parametrize("sender", [None, "27820000009@s.whatsapp.net", "27820000001@g.us", "27820000001@broadcast", "27820000001@newsletter", "status@broadcast"])
def test_non_people_are_not_owners(sender):
    assert not is_owner(OWNERS, sender)
    if sender:
        assert not is_owner([sender], "27820000001@s.whatsapp.net")


@pytest.mark.parametrize("platform,transport,runtime,owners,user_id,expected", [
    ("whatsapp", "default", "default", OWNERS, "27820000009@s.whatsapp.net", False),
    ("whatsapp_cloud", "default", "default", OWNERS, "27820000009@s.whatsapp.net", False),
    ("whatsapp", "default", "default", [], "other", None),
    ("telegram", "default", "default", OWNERS, "other", None),
    ("whatsapp", "default", "sentio-sales", OWNERS, "other", None),
    ("whatsapp", "team_b", "team_b", OWNERS, "other", None),
    ("whatsapp", "default", "default", OWNERS, "27820000001@s.whatsapp.net", None),
])
def test_gate_scope(platform, transport, runtime, owners, user_id, expected):
    identity = SimpleNamespace(transport_profile=transport, runtime_profile=runtime)
    assert owner_gate(owners, platform=platform, user_id=user_id, identity=identity, primary_profile="default") is expected


def test_restored_source_is_outside_gate():
    assert owner_gate(OWNERS, platform="whatsapp", user_id="other", identity=None, primary_profile="default") is None
