"""Active-profile discovery respects grants even though its route is open."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from gateway.access_policy import parse_access_policy
from hermes_cli.web_routers.profiles import router
from tests.hermes_cli.test_dashboard_rest_access import _GroupStub, _call
from hermes_cli.dashboard_auth.registry import clear_providers, register_provider


def _client(allowed=None, *, gated=False, policy=None):
    app = FastAPI()

    @app.middleware("http")
    async def stamp(request: Request, call_next):
        if gated:
            from hermes_cli.dashboard_auth.middleware import gated_auth_middleware
            return await gated_auth_middleware(request, call_next)
        request.state.access_allowed_profiles = allowed
        return await call_next(request)

    app.state.auth_required = True
    app.state.bound_host, app.state.bound_port = "gw.example", 443
    app.state.gateway_runner = SimpleNamespace(config=SimpleNamespace(access=policy))
    app.include_router(router)
    return TestClient(app, base_url="https://gw.example")


@pytest.fixture(autouse=True)
def _machine_profile(monkeypatch):
    monkeypatch.setattr("hermes_cli.profiles.get_active_profile", lambda: "work")
    monkeypatch.setattr("hermes_cli.profiles.get_active_profile_name", lambda: "default")
    clear_providers()
    register_provider(_GroupStub())
    yield
    clear_providers()


@pytest.mark.parametrize("allowed,selector,chosen", [
    (frozenset({"sentio-sales"}), "", "sentio-sales"),
    (frozenset({"beta", "alpha"}), "?profile=beta", "beta"),
    (frozenset({"beta", "alpha"}), "?profile=gamma", "alpha"),
    (frozenset({"beta", "alpha"}), "", "alpha"),
    (None, "?profile=beta", None),
])
def test_active_profile_follows_grants(allowed, selector, chosen):
    response = _client(allowed).get("/api/profiles/active" + selector)
    assert response.status_code == 200
    assert response.json() == ({"active": chosen, "current": chosen} if chosen else
                               {"active": "work", "current": "default"})


def test_empty_grants_never_reveal_the_host_profile():
    response = _client(frozenset()).get("/api/profiles/active")
    assert response.status_code == 403


@pytest.mark.parametrize("groups,selector,configured,status,expected", [
    (["hermes-profile-sales"], "", True, 200, {"active": "sales", "current": "sales"}),
    (["hermes-profile-beta", "hermes-profile-alpha"], "?profile=beta", True, 200,
     {"active": "beta", "current": "beta"}),
    (["hermes-profile-beta", "hermes-profile-alpha"], "?profile=other", True, 200,
     {"active": "alpha", "current": "alpha"}),
    ([], "", True, 403, None),
    (["hermes-admins"], "?profile=sales", True, 200, {"active": "work", "current": "default"}),
    ([], "", False, 200, {"active": "work", "current": "default"}),
])
def test_real_access_middleware_discovery(groups, selector, configured, status, expected):
    policy = parse_access_policy({"admin_groups": ["hermes-admins"]} if configured else None)
    response = _call(_client(gated=True, policy=policy), groups, "GET", "/api/profiles/active" + selector)
    assert response.status_code == status
    if expected is not None:
        assert response.json() == expected
