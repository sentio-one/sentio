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


def _authorized(rig, jid):
    source = rig.shared.build_source(chat_id=jid, chat_type="dm", user_id=jid)
    resolve_identity(source, runner=rig.runner)
    return rig.runner._is_user_authorized(source)


def test_unrouted_number_is_refused_when_owners_are_set(rig, monkeypatch):
    monkeypatch.setenv("GATEWAY_ALLOW_ALL_USERS", "true")
    rig.runner.config.owners = [OWNER]
    assert _authorized(rig, f"{OWNER}@s.whatsapp.net") is True
    assert _authorized(rig, f"{SALES}@s.whatsapp.net") is True
    assert _authorized(rig, "27820000009@s.whatsapp.net") is False


def test_without_owners_nothing_changes(rig, monkeypatch):
    monkeypatch.setenv("GATEWAY_ALLOW_ALL_USERS", "true")
    assert _authorized(rig, "27820000009@s.whatsapp.net") is True


def test_owners_reach_runtime_config(tmp_path, monkeypatch):
    from gateway.config import load_gateway_config

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text("gateway:\n  owners:\n    - '+27820000001'\n", encoding="utf-8")
    config = load_gateway_config()
    assert config.owners == ["+27820000001"]
    assert GatewayConfig.from_dict(config.to_dict()).owners == config.owners


def test_mapped_lid_owner_is_admitted_but_other_lid_refused(rig, monkeypatch):
    from hermes_constants import get_hermes_dir

    monkeypatch.setenv("GATEWAY_ALLOW_ALL_USERS", "true")
    rig.runner.config.owners = [OWNER]
    session = get_hermes_dir("platforms/whatsapp/session", "whatsapp/session")
    session.mkdir(parents=True, exist_ok=True)
    (session / "lid-mapping-555666777.json").write_text(json.dumps(OWNER), encoding="utf-8")
    assert _authorized(rig, "555666777@lid") is True
    assert _authorized(rig, "888999000@lid") is False


def test_owner_still_requires_ordinary_admission(rig, monkeypatch):
    monkeypatch.delenv("GATEWAY_ALLOW_ALL_USERS", raising=False)
    monkeypatch.delenv("WHATSAPP_ALLOW_ALL_USERS", raising=False)
    monkeypatch.setenv("GATEWAY_ALLOWED_USERS", "27820000009")
    rig.runner.config.owners = [OWNER]
    assert _authorized(rig, f"{OWNER}@s.whatsapp.net") is False


def test_explicit_default_route_defers_to_ordinary_admission(rig, monkeypatch):
    rig.runner.config.owners = [OWNER]
    rig.runner.config.profile_routes = parse_profile_routes([
        {"platform": "whatsapp", "profile": "default", "chat_id": SALES},
    ])
    monkeypatch.setenv("GATEWAY_ALLOWED_USERS", SALES)
    assert _authorized(rig, f"{SALES}@s.whatsapp.net") is True
    assert _authorized(rig, "27820000009@s.whatsapp.net") is False
    monkeypatch.setenv("GATEWAY_ALLOWED_USERS", "27820000009")
    assert _authorized(rig, f"{SALES}@s.whatsapp.net") is False


def test_inactive_or_wrong_bot_route_cannot_bypass_owner_gate(rig, monkeypatch):
    rig.runner.config.owners = [OWNER]
    monkeypatch.setenv("GATEWAY_ALLOW_ALL_USERS", "true")
    rig.runner.config.profile_routes = parse_profile_routes([
        {"platform": "whatsapp", "profile": "default", "chat_id": SALES},
    ])
    rig.runner.config.multiplex_profiles = False
    assert _authorized(rig, f"{SALES}@s.whatsapp.net") is False
    rig.runner.config.multiplex_profiles = True
    rig.runner.config.profile_routes = parse_profile_routes([
        {"platform": "whatsapp", "profile": "default", "chat_id": SALES, "bot_profile": "other"},
    ])
    assert _authorized(rig, f"{SALES}@s.whatsapp.net") is False


@pytest.mark.parametrize("target", ["missing", "parked"])
def test_unserved_route_is_dropped_before_owner_admission(rig, target):
    from gateway.session_identity import identity_of

    rig.runner.config.owners = [OWNER]
    rig.runner.config.profile_routes = parse_profile_routes([
        {"platform": "whatsapp", "profile": target, "chat_id": OWNER},
    ])
    rig.runner.session_runtime_descriptor = {"parked_profiles": {target: "unavailable"} if target == "parked" else {}}
    source = rig.shared.build_source(chat_id=f"{OWNER}@s.whatsapp.net", chat_type="dm", user_id=f"{OWNER}@s.whatsapp.net")
    assert rig.runner._admit_primary_source(source, rig.home) is None
    assert source.profile_route_rejected is True
    assert identity_of(source) is None
