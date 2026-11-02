"""One shared WhatsApp number: owner on default, routed people on their own profiles.

Real GatewayRunner resolvers against a temp HERMES_HOME with bridge-shaped DM
identities: chatId is a JID; LID aliases come from lid-mapping-*.json files.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.pairing import PairingStore
from gateway.platforms.base import BasePlatformAdapter
from gateway.profile_routing import parse_profile_routes
from gateway.session_identity import resolve_identity

WHATSAPP = Platform("whatsapp")
OWNER = "27820000001"
SALES = "27820000002"
SALES_LID = "111222333"


class _Stub(BasePlatformAdapter):
    pass


_Stub.__abstractmethods__ = frozenset()


def _stub(runner):
    adapter = _Stub.__new__(_Stub)
    adapter.platform, adapter.gateway_runner, adapter.label = WHATSAPP, runner, "SHARED"
    adapter.config = PlatformConfig(enabled=True, extra={})
    adapter._pending_messages, adapter._active_sessions = {}, {}
    return adapter


@pytest.fixture
def rig(tmp_path, monkeypatch):
    from gateway.run import GatewayRunner

    home = tmp_path / "hh"
    (home / "profiles" / "sentio-sales").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    served = [("default", home), ("sentio-sales", home / "profiles" / "sentio-sales")]

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(multiplex_profiles=True)
    runner.config.platforms = {WHATSAPP: PlatformConfig(enabled=True, extra={})}
    runner.config.profile_routes = parse_profile_routes([
        {"name": "sales", "platform": "whatsapp", "profile": "sentio-sales", "chat_id": SALES},
    ])
    runner.pairing_store = PairingStore(profile="default")
    runner.pairing_stores = {}
    runner._primary_profile_name = "default"
    shared = _stub(runner)
    runner.adapters = {WHATSAPP: shared}
    runner._profile_adapters = {"sentio-sales": {}}
    with patch("hermes_cli.profiles.profiles_to_serve", return_value=served), \
            patch("hermes_cli.profiles.get_profile_dir",
                  side_effect=lambda n: home if n == "default" else home / "profiles" / n), \
            patch("hermes_cli.profiles.profile_exists", return_value=True):
        yield SimpleNamespace(runner=runner, shared=shared, home=home)


def _dm(rig, jid):
    source = rig.shared.build_source(chat_id=jid, chat_type="dm", user_id=jid)
    return resolve_identity(source, runner=rig.runner)


def test_owner_number_runs_on_the_default_profile(rig):
    identity = _dm(rig, f"{OWNER}@s.whatsapp.net")
    assert (identity.transport_profile, identity.runtime_profile) == ("default", "default")


def test_routed_number_runs_on_its_own_profile_in_jid_form(rig):
    identity = _dm(rig, f"{SALES}@s.whatsapp.net")
    assert (identity.transport_profile, identity.runtime_profile) == ("default", "sentio-sales")


def test_routed_number_is_found_when_the_sender_arrives_as_a_lid(rig):
    from hermes_constants import get_hermes_dir

    session = get_hermes_dir("platforms/whatsapp/session", "whatsapp/session")
    session.mkdir(parents=True, exist_ok=True)
    (session / f"lid-mapping-{SALES}.json").write_text(json.dumps(SALES_LID), encoding="utf-8")
    identity = _dm(rig, f"{SALES_LID}@lid")
    assert identity.runtime_profile == "sentio-sales"
