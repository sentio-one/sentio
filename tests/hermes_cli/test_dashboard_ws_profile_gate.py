"""Profile-selecting dashboard WebSockets honour the access grant (private fork, phase 3).

``/api/pty``, ``/api/console`` and ``/api/audio/*`` each read ``?profile=`` straight off the upgrade.
``ws_profile_permitted`` is the one check they share, run on the identity the upgrade gate stamped.
"""
from types import SimpleNamespace

import pytest

from gateway.access_policy import parse_access_policy
from hermes_cli.dashboard_auth.access import ws_is_grant_scoped, ws_profile_permitted

ISSUER = "https://sso.corp.example"
HOST = {"host": "fly-app.fly.dev"}  # the bound host, or the upgrade dies at the host/origin check
POLICY = parse_access_policy({"admin_groups": ["hermes-admins"]})


class _Query(dict):
    pass


def _ws(identity, *, policy=POLICY, auth_required=True, **query):
    gateway_runner = SimpleNamespace(config=SimpleNamespace(access=policy))
    app = SimpleNamespace(state=SimpleNamespace(gateway_runner=gateway_runner, auth_required=auth_required))
    ws = SimpleNamespace(app=app, query_params=_Query(query))
    if identity is not None:
        ws._hermes_auth_identity = identity
    return ws


def _alice(*groups):
    return {"user_id": "u1", "provider": "self_hosted", "issuer": ISSUER, "groups": list(groups)}


def test_a_granted_profile_is_permitted_and_others_are_not():
    ws = _ws(_alice("hermes-profile-alpha"), profile="alpha")
    assert ws_profile_permitted(ws) is True
    for other in ("beta", "default", "../alpha", "al pha", "x" * 200):
        assert ws_profile_permitted(_ws(_alice("hermes-profile-alpha"), profile=other)) is False, other


@pytest.mark.parametrize("spelling", ["Alpha", " alpha ", "ALPHA"])
def test_selector_case_and_whitespace_do_not_change_the_answer(spelling):
    assert ws_profile_permitted(_ws(_alice("hermes-profile-alpha"), profile=spelling)) is True
    assert ws_profile_permitted(_ws(_alice("hermes-profile-beta"), profile=spelling)) is False


def test_no_profile_param_means_the_launch_profile():
    assert ws_profile_permitted(_ws(_alice("hermes-profile-alpha"))) is False
    assert ws_profile_permitted(_ws(_alice("hermes-profile-alpha"), profile="current")) is False
    assert ws_profile_permitted(_ws(_alice("hermes-profile-default"))) is True


def test_admin_and_the_no_policy_dashboard_are_unrestricted():
    assert ws_profile_permitted(_ws(_alice("hermes-admins"), profile="beta")) is True
    assert ws_profile_permitted(_ws(_alice(), policy=parse_access_policy(None), profile="beta")) is True


def test_unknown_user_reaches_no_profile():
    assert ws_profile_permitted(_ws(_alice(), profile="alpha")) is False
    assert ws_profile_permitted(_ws(_alice())) is False


def test_missing_identity_is_allowed_only_on_an_ungated_host():
    assert ws_profile_permitted(_ws(None, auth_required=False, profile="beta")) is True
    assert ws_profile_permitted(_ws(None, auth_required=True, profile="beta")) is False


def test_grant_scoped_callers_are_identified_so_host_paths_can_be_dropped():
    assert ws_is_grant_scoped(_ws(_alice("hermes-profile-alpha"))) is True
    assert ws_is_grant_scoped(_ws(_alice("hermes-admins"))) is False
    assert ws_is_grant_scoped(_ws(_alice(), policy=parse_access_policy(None))) is False
    assert ws_is_grant_scoped(_ws(None, auth_required=False)) is False


def test_console_and_pty_sockets_refuse_an_ungranted_profile_before_accepting(tmp_path, monkeypatch):
    """End to end through the real routes: the upgrade is refused with 4403, not served."""
    import json

    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from hermes_cli import web_server
    from hermes_cli.dashboard_auth.ws_tickets import _reset_for_tests, mint_ticket
    from hermes_cli.web_routers import chat_ws

    (tmp_path / "config.yaml").write_text(json.dumps({"access": {"admin_groups": ["hermes-admins"]}}))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(chat_ws, "_DASHBOARD_EMBEDDED_CHAT_ENABLED", True)
    _reset_for_tests()
    prev = {k: getattr(web_server.app.state, k, None) for k in ("bound_host", "bound_port", "auth_required")}
    web_server.app.state.bound_host, web_server.app.state.bound_port = "fly-app.fly.dev", 443
    web_server.app.state.auth_required = True
    try:
        client = TestClient(web_server.app, base_url="https://fly-app.fly.dev")
        for route in ("console", "pty"):
            ticket = mint_ticket(user_id="u1", provider="self_hosted", issuer=ISSUER,
                                 extra={"groups": ["hermes-profile-alpha"]})
            with pytest.raises(WebSocketDisconnect) as refused:
                with client.websocket_connect(f"/api/{route}?ticket={ticket}&profile=beta", headers=HOST):
                    pass
            # The reason proves it was THIS gate, not the host/origin check that shares the close code.
            assert (refused.value.code, refused.value.reason) == (4403, "profile not granted"), route
        granted = mint_ticket(user_id="u1", provider="self_hosted", issuer=ISSUER,
                              extra={"groups": ["hermes-profile-alpha"]})
        with client.websocket_connect(f"/api/console?ticket={granted}&profile=alpha", headers=HOST) as ok:
            assert ok.receive_json()["type"] in {"ready", "error"}  # accepted, not refused at upgrade
    finally:
        _reset_for_tests()
        for key, value in prev.items():
            setattr(web_server.app.state, key, value)
