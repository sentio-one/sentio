"""``GET /api/auth/me`` tells the signed-in user what the access policy lets them do (private fork).

The dashboard uses it to hide admin-only navigation. It is a hint for the UI, not a gate: the routes enforce the
same policy on their own. It must never reveal more than the caller's own standing (no groups, no other users).
"""
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.testclient import TestClient

from gateway.access_policy import parse_access_policy
from hermes_cli.dashboard_auth.base import Session
from hermes_cli.dashboard_auth.registry import clear_providers, register_provider
from tests.hermes_cli.conftest_dashboard_auth import StubAuthProvider, _sign, _unsign

ISSUER = "https://sso.corp.example"
POLICY = parse_access_policy({"admin_groups": ["hermes-admins"]})


class _Stub(StubAuthProvider):
    name = "self_hosted"

    def verify_session(self, *, access_token):
        payload = _unsign(access_token)
        if payload is None or payload.get("exp", 0) <= int(time.time()):
            return None
        return Session(user_id=payload["sub"], email="a@corp.example", display_name="A", org_id="", provider=self.name,
                       expires_at=payload["exp"], access_token=access_token, refresh_token="", issuer=ISSUER,
                       groups=tuple(payload.get("groups", ())))


@pytest.fixture
def world(tmp_path, monkeypatch):
    from hermes_cli.dashboard_auth.middleware import gated_auth_middleware
    from hermes_cli.dashboard_auth.routes import router

    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    clear_providers()
    register_provider(_Stub())

    async def gate(request, call_next):
        return await gated_auth_middleware(request, call_next)

    app = FastAPI(middleware=[Middleware(BaseHTTPMiddleware, dispatch=gate)])
    app.include_router(router)
    app.state.auth_required = True
    app.state.bound_host, app.state.bound_port = "gw.example", 443
    app.state.gateway_runner = SimpleNamespace(config=SimpleNamespace(access=POLICY))
    yield SimpleNamespace(client=TestClient(app, base_url="https://gw.example"), app=app, home=home)
    clear_providers()


def _me(world, groups=(), sub="u1"):
    token = _sign({"sub": sub, "email": "", "name": "", "org_id": "", "groups": list(groups), "exp": int(time.time()) + 600})
    response = world.client.get("/api/auth/me", headers={"authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    return response.json()


def test_an_admin_is_told_so(world):
    assert _me(world, ["hermes-admins"])["access"] == {"configured": True, "admin": True, "profiles": []}


def test_a_member_sees_only_their_profiles_and_no_admin_flag(world):
    me = _me(world, ["hermes-profile-alpha", "hermes-profile-beta", "unrelated"])
    assert me["access"] == {"configured": True, "admin": False, "profiles": ["alpha", "beta"]}


def test_an_unknown_user_has_no_profiles(world):
    assert _me(world)["access"] == {"configured": True, "admin": False, "profiles": []}


def test_a_redeemed_invite_shows_up_as_a_profile(world):
    from gateway.access_grants import default_store

    store = default_store()
    created = store.create_invite(profile="default", created_by="admin")
    store.redeem(created.token, subject=POLICY.subject_key("self_hosted", ISSUER, "u-invited"))
    assert _me(world, sub="u-invited")["access"] == {"configured": True, "admin": False, "profiles": ["default"]}
    assert _me(world, sub="someone-else")["access"]["profiles"] == []


def test_without_a_policy_the_dashboard_is_unrestricted_but_says_it_is_not_configured(world):
    from gateway.access_policy import parse_access_policy as parse

    world.app.state.gateway_runner = SimpleNamespace(config=SimpleNamespace(access=parse(None)))
    assert _me(world)["access"] == {"configured": False, "admin": True, "profiles": []}


def test_the_existing_fields_stay_and_nothing_else_about_the_person_leaks(world):
    me = _me(world, ["hermes-admins", "secret-group"])
    assert {"user_id", "email", "display_name", "org_id", "provider", "expires_at"} <= set(me)
    assert "groups" not in me and "secret-group" not in str(me)


def test_the_summary_helper_treats_a_session_less_trusted_caller_as_admin():
    from hermes_cli.dashboard_auth.access import access_summary

    app = SimpleNamespace(state=SimpleNamespace(gateway_runner=SimpleNamespace(config=SimpleNamespace(access=POLICY))))
    assert access_summary(app, None) == {"configured": True, "admin": True, "profiles": []}
