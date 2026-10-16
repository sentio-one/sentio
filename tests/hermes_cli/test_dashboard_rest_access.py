"""REST routes honour the access grant: host-level routes are admin-only, profile routes are allowlisted
(private fork, phase 4).

Driven through the real ``gated_auth_middleware`` on a small app, so the check runs exactly where the
session is verified. ``classify`` is default-deny: a route nobody classified is admin-only.
"""
import inspect
import time

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from gateway.access_policy import parse_access_policy
from hermes_cli.dashboard_auth.access_http import classify
from hermes_cli.dashboard_auth.base import Session
from hermes_cli.dashboard_auth.registry import clear_providers, register_provider
from tests.hermes_cli.conftest_dashboard_auth import StubAuthProvider, _sign, _unsign

ISSUER = "https://sso.corp.example"
POLICY = parse_access_policy({"admin_groups": ["hermes-admins"]})
PATHS = [
    ("GET", "/api/auth/me"), ("POST", "/api/auth/ws-ticket"), ("GET", "/api/health"),
    ("GET", "/api/config"), ("PUT", "/api/config"), ("GET", "/api/config/defaults"),
    ("GET", "/api/sessions"), ("GET", "/api/skills"), ("GET", "/api/cron/jobs"),
    ("GET", "/api/profiles"), ("POST", "/api/profiles"), ("DELETE", "/api/profiles/alpha"),
    ("GET", "/api/profiles/alpha/soul"), ("GET", "/api/profiles/ALPHA/soul"), ("GET", "/api/profiles/beta/soul"),
    ("POST", "/api/profiles/alpha/open-terminal"), ("GET", "/api/profiles/sessions/sidebar"),
    ("GET", "/api/fs/list"), ("GET", "/api/files"), ("GET", "/api/git/status"),
    ("POST", "/api/dashboard/agent-plugins/install"), ("POST", "/api/hermes/update"),
    ("POST", "/api/gateway/drain"), ("POST", "/api/ops/backup"), ("GET", "/api/local-models/catalog"),
    ("GET", "/api/brand-new-route"),
]


class _GroupStub(StubAuthProvider):
    """Self-hosted-named stub whose session carries the ``groups`` of its token."""

    name = "self_hosted"

    def verify_session(self, *, access_token):
        payload = _unsign(access_token)
        if payload is None or payload.get("exp", 0) <= int(time.time()):
            return None
        return Session(user_id=payload["sub"], email="", display_name="", org_id="", provider=self.name,
                       expires_at=payload["exp"], access_token=access_token, refresh_token="",
                       issuer=ISSUER, groups=tuple(payload.get("groups", ())))


async def _echo(request):
    body = await request.body()
    return JSONResponse({"ok": True, "body": body.decode() or None})


def _client(policy=POLICY):
    from types import SimpleNamespace

    from hermes_cli.dashboard_auth.middleware import gated_auth_middleware

    async def gate(request, call_next):
        return await gated_auth_middleware(request, call_next)

    app = Starlette(routes=[Route(path, _echo, methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
                            for path in sorted({p for _, p in PATHS})],
                    middleware=[Middleware(BaseHTTPMiddleware, dispatch=gate)])

    app.state.auth_required = True
    app.state.bound_host, app.state.bound_port = "gw.example", 443
    app.state.gateway_runner = SimpleNamespace(config=SimpleNamespace(access=policy))
    return TestClient(app, base_url="https://gw.example")


@pytest.fixture(autouse=True)
def _provider():
    clear_providers()
    register_provider(_GroupStub())
    yield
    clear_providers()


def _call(client, groups, method, url, **kwargs):
    token = _sign({"sub": "u1", "email": "", "name": "", "org_id": "", "groups": list(groups),
                   "exp": int(time.time()) + 600})
    return client.request(method, url, headers={"authorization": f"Bearer {token}"}, **kwargs)


def _code(*args, **kwargs):
    return _call(*args, **kwargs).status_code


ALICE = ["hermes-profile-alpha"]
BOB_DEFAULT = ["hermes-profile-default", "hermes-profile-alpha"]


def test_without_a_policy_the_dashboard_is_unchanged():
    client = _client(parse_access_policy(None))
    for method, path in PATHS:
        assert _code(client, [], method, path) == 200, (method, path)


def test_an_admin_group_reaches_everything():
    client = _client()
    for method, path in PATHS:
        assert _code(client, ["hermes-admins"], method, path) == 200, (method, path)


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/fs/list"), ("GET", "/api/files"), ("GET", "/api/git/status"),
    ("POST", "/api/dashboard/agent-plugins/install"), ("POST", "/api/hermes/update"),
    ("POST", "/api/gateway/drain"), ("POST", "/api/ops/backup"), ("GET", "/api/local-models/catalog"),
    ("POST", "/api/profiles"), ("DELETE", "/api/profiles/alpha"), ("POST", "/api/profiles/alpha/open-terminal"),
    ("GET", "/api/brand-new-route"),
])
def test_host_level_and_unclassified_routes_are_admin_only(method, path):
    client = _client()
    for groups in (ALICE, [], BOB_DEFAULT):
        assert _code(client, groups, method, path + "?profile=alpha") == 403, (groups, method, path)


def test_a_few_account_routes_stay_open_to_any_signed_in_user():
    client = _client()
    for method, path in (("GET", "/api/auth/me"), ("POST", "/api/auth/ws-ticket"), ("GET", "/api/health"),
                         ("GET", "/api/config/defaults"), ("GET", "/api/profiles")):
        assert _code(client, [], method, path) == 200, path


def test_profile_routes_follow_the_granted_profiles():
    client = _client()
    assert _code(client, ALICE, "GET", "/api/config?profile=alpha") == 200
    assert _code(client, ALICE, "GET", "/api/config?profile=ALPHA") == 200
    for bad in ("beta", "default", "current", "all", "../alpha", ""):
        assert _code(client, ALICE, "GET", f"/api/config?profile={bad}") == 403, bad
    assert _code(client, ALICE, "GET", "/api/skills") == 403  # no profile = the launch profile, not granted
    assert _code(client, BOB_DEFAULT, "GET", "/api/skills") == 200
    assert _code(client, BOB_DEFAULT, "GET", "/api/skills?profile=current") == 200
    assert _code(client, [], "GET", "/api/config?profile=alpha") == 403


def test_every_profile_value_on_a_request_must_be_granted():
    client = _client()
    assert _code(client, ALICE, "GET", "/api/config?profile=alpha&profile=beta") == 403
    assert _code(client, ALICE, "GET", "/api/config?profile=beta&profile=alpha") == 403
    assert _code(client, ALICE, "GET", "/api/config?profile=alpha&profile=alpha") == 200


def test_a_profile_in_the_json_body_is_checked_and_the_body_still_reaches_the_handler():
    client = _client()
    ok = _call(client, ALICE, "PUT", "/api/config?profile=alpha", json={"profile": "alpha", "x": 1})
    assert ok.status_code == 200 and '"x": 1' in ok.json()["body"].replace('"x":1', '"x": 1')
    assert _code(client, ALICE, "PUT", "/api/config?profile=alpha", json={"profile": "beta"}) == 403
    assert _code(client, ALICE, "PUT", "/api/config", json={"profile": "beta"}) == 403
    assert _code(client, ALICE, "PUT", "/api/config?profile=beta", json={"profile": "alpha"}) == 403


def test_routes_that_never_accepted_the_current_shorthand_need_an_explicit_profile():
    client = _client()
    # /api/sessions and /api/cron/jobs treat an absent selector as "everything": never for a grant-scoped caller.
    assert _code(client, BOB_DEFAULT, "GET", "/api/sessions") == 403
    assert _code(client, BOB_DEFAULT, "GET", "/api/cron/jobs") == 403
    assert _code(client, BOB_DEFAULT, "GET", "/api/sessions?profile=current") == 403
    assert _code(client, ALICE, "GET", "/api/sessions?profile=alpha") == 200
    assert _code(client, ALICE, "GET", "/api/cron/jobs?profile=alpha") == 200
    assert _code(client, ALICE, "GET", "/api/sessions?profile=beta") == 403


def test_profile_subroutes_check_the_profile_in_the_path():
    client = _client()
    assert _code(client, ALICE, "GET", "/api/profiles/alpha/soul") == 200
    assert _code(client, ALICE, "GET", "/api/profiles/beta/soul") == 403
    assert _code(client, ALICE, "GET", "/api/profiles/ALPHA/soul") == 200


def test_cross_profile_aggregates_need_every_selector_granted_and_explicit():
    client = _client()
    base = "/api/profiles/sessions/sidebar"
    assert _code(client, ALICE, "GET", f"{base}?profile=alpha&recents_profile=alpha") == 200
    assert _code(client, ALICE, "GET", f"{base}?profile=alpha&recents_profile=beta") == 403
    assert _code(client, ALICE, "GET", f"{base}?profile=alpha") == 403
    assert _code(client, ALICE, "GET", f"{base}?profile=alpha&recents_profile=all") == 403
    assert _code(client, ALICE, "GET", base) == 403


def test_the_profile_list_endpoint_returns_only_granted_profiles(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from hermes_cli.web_routers import profiles as profiles_router

    async def rows():
        return {"profiles": [{"name": "default"}, {"name": "alpha"}, {"name": "beta"}], "active": "default"}

    monkeypatch.setattr(profiles_router, "_read_profiles", rows)

    def listed(allowed):
        request = SimpleNamespace(state=SimpleNamespace(access_allowed_profiles=allowed))
        return [p["name"] for p in asyncio.run(profiles_router.list_profiles_endpoint(request))["profiles"]]

    assert listed(None) == ["default", "alpha", "beta"]
    assert listed(frozenset({"alpha"})) == ["alpha"]
    assert listed(frozenset()) == []


def test_classification_is_default_deny_and_covers_the_real_app():
    from hermes_cli import web_server

    assert classify("GET", "/api/some/new/thing") == "admin"
    seen = {"open": 0, "profile": 0, "admin": 0}
    for route in web_server.app.routes:
        path = getattr(route, "path", "")
        if path.startswith("/api/"):
            for method in sorted(getattr(route, "methods", None) or {"GET"}):
                seen[classify(method, path)] += 1
    assert all(seen.values()), seen
    for prefix in ("/api/fs/list", "/api/files", "/api/git/status", "/api/ops/backup", "/api/gateway/start",
                   "/api/local-models/catalog", "/api/hermes/update", "/api/dashboard/agent-plugins/install"):
        assert classify("POST", prefix) == "admin" and classify("GET", prefix) == "admin", prefix


def test_a_profile_class_route_reads_its_profile_only_where_the_gate_looks():
    """The gate extracts a profile from ``?profile=``, ``?recents_profile=``, a JSON ``profile`` and the
    ``/api/profiles/{name}`` path. A profile-class handler that takes a profile from anywhere else would
    slip past it, so none may exist."""
    from hermes_cli import web_server

    gate_reads = {"profile", "recents_profile"}
    for route in web_server.app.routes:
        path = getattr(route, "path", "")
        endpoint = getattr(route, "endpoint", None)
        if not path.startswith("/api/") or endpoint is None:
            continue
        for method in getattr(route, "methods", None) or {"GET"}:
            if classify(method, path) != "profile":
                continue
            for name, param in inspect.signature(endpoint).parameters.items():
                if "profile" in name and not path.startswith("/api/profiles"):
                    assert name in gate_reads, (method, path, name)
                annotation = param.annotation
                fields = getattr(annotation, "model_fields", None) if inspect.isclass(annotation) else None
                for field in (fields or {}):
                    if "profile" in field:
                        assert field == "profile", (method, path, annotation.__name__, field)
