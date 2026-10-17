"""Admin invite API and the redeem route (private fork).

Through the real gate on a small app: an admin creates a profile-scoped invite, the invited person redeems
it after signing in, and the access they gained is the same one the REST gate and the WebSocket scope enforce.
"""
import json
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
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
ADMIN, ALICE, BOB = ["hermes-admins"], [], []


class _Stub(StubAuthProvider):
    name = "self_hosted"

    def verify_session(self, *, access_token):
        payload = _unsign(access_token)
        if payload is None or payload.get("exp", 0) <= int(time.time()):
            return None
        return Session(user_id=payload["sub"], email=payload.get("email", ""), display_name="", org_id="",
                       provider=self.name, expires_at=payload["exp"], access_token=access_token,
                       refresh_token="", issuer=ISSUER, groups=tuple(payload.get("groups", ())))


async def _echo(request):
    return JSONResponse({"ok": True})


@pytest.fixture
def world(tmp_path, monkeypatch):
    from hermes_cli.dashboard_auth.middleware import gated_auth_middleware
    from hermes_cli.web_routers import access_invites

    home = tmp_path / ".hermes"
    (home / "profiles" / "alpha").mkdir(parents=True)
    (home / "profiles" / "alpha" / "config.yaml").write_text("{}")
    (home / "profiles" / "beta").mkdir(parents=True)
    (home / "profiles" / "beta" / "config.yaml").write_text("{}")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("HERMES_DASHBOARD_PUBLIC_URL", raising=False)
    access_invites.reset_rate_limits()
    clear_providers()
    register_provider(_Stub())

    async def gate(request, call_next):
        return await gated_auth_middleware(request, call_next)

    app = FastAPI(middleware=[Middleware(BaseHTTPMiddleware, dispatch=gate)])
    app.include_router(access_invites.router)
    app.router.routes.append(Route("/api/config", _echo, methods=["GET"]))
    app.state.auth_required = True
    app.state.bound_host, app.state.bound_port = "gw.example", 443
    app.state.gateway_runner = SimpleNamespace(config=SimpleNamespace(access=POLICY))
    yield SimpleNamespace(client=TestClient(app, base_url="https://gw.example"), app=app, home=home)
    clear_providers()


def _call(world, who, method, url, *, sub="u1", email="", **kwargs):
    token = _sign({"sub": sub, "email": email, "name": "", "org_id": "", "groups": list(who),
                   "exp": int(time.time()) + 600})
    return world.client.request(method, url, headers={"authorization": f"Bearer {token}"}, **kwargs)


def _create(world, **body):
    response = _call(world, ADMIN, "POST", "/api/access/invites", sub="u-admin", json={"profile": "alpha", **body})
    assert response.status_code == 200, response.text
    return response.json()


def _token(created):
    return parse_qs(urlparse(created["link"]).query)["invite"][0]


def test_an_admin_creates_an_invite_and_gets_a_link_for_this_gateway(world):
    created = _create(world)
    link = urlparse(created["link"])
    query = parse_qs(link.query)
    assert (link.scheme, link.netloc) == ("hermes", "connect")
    assert query["url"] == ["https://gw.example"] and len(query["invite"][0]) >= 40
    assert created["invite_id"] and created["expires_at"] > time.time()


def test_the_link_uses_the_declared_public_url_when_there_is_one(world, monkeypatch):
    monkeypatch.setenv("HERMES_DASHBOARD_PUBLIC_URL", "https://hermes.acme.example/gw")
    assert parse_qs(urlparse(_create(world)["link"]).query)["url"] == ["https://hermes.acme.example/gw"]


def test_only_an_admin_manages_invites_and_grants(world):
    for method, url, kwargs in (("POST", "/api/access/invites", {"json": {"profile": "alpha"}}),
                                ("GET", "/api/access/invites", {}), ("DELETE", "/api/access/invites/x", {}),
                                ("GET", "/api/access/grants", {}), ("DELETE", "/api/access/grants?subject=s&profile=alpha", {})):
        assert _call(world, ALICE, method, url, **kwargs).status_code == 403, (method, url)


def test_redeeming_gives_the_invited_person_that_profile_and_only_that_profile(world):
    token = _token(_create(world))
    assert _call(world, ALICE, "GET", "/api/config?profile=alpha", sub="u-alice").status_code == 403
    redeemed = _call(world, ALICE, "POST", "/api/access/invites/redeem", sub="u-alice", json={"token": token})
    assert redeemed.status_code == 200 and redeemed.json() == {"profile": "alpha"}
    assert _call(world, ALICE, "GET", "/api/config?profile=alpha", sub="u-alice").status_code == 200
    assert _call(world, ALICE, "GET", "/api/config?profile=beta", sub="u-alice").status_code == 403
    assert _call(world, ALICE, "GET", "/api/config?profile=alpha", sub="u-bob").status_code == 403


def test_the_websocket_scope_picks_up_the_redeemed_grant(world):
    from hermes_cli.dashboard_auth.access import scope_for_identity

    token = _token(_create(world))
    _call(world, ALICE, "POST", "/api/access/invites/redeem", sub="u-alice", json={"token": token})
    identity = {"user_id": "u-alice", "provider": "self_hosted", "issuer": ISSUER}
    scoped, operator = scope_for_identity(identity, POLICY)
    assert scoped["allowed_profiles"] == ["alpha"] and operator is False
    other, _ = scope_for_identity({**identity, "user_id": "u-bob"}, POLICY)
    assert other["allowed_profiles"] == []


def test_every_failed_redeem_looks_the_same(world):
    spent = _token(_create(world))
    _call(world, ALICE, "POST", "/api/access/invites/redeem", sub="u-alice", json={"token": spent})
    revoked = _create(world)
    _call(world, ADMIN, "DELETE", f"/api/access/invites/{revoked['invite_id']}", sub="u-admin")
    bodies = set()
    for token in (spent, _token(revoked), "nope", ""):
        response = _call(world, ALICE, "POST", "/api/access/invites/redeem", sub="u-bob", json={"token": token})
        assert response.status_code == 400
        bodies.add(response.text)
    assert bodies == {json.dumps({"detail": "invalid_invite"}, separators=(",", ":"))} or len(bodies) == 1


def test_an_email_bound_invite_needs_the_signed_in_email(world):
    token = _token(_create(world, email="alice@corp.example"))
    wrong = _call(world, ALICE, "POST", "/api/access/invites/redeem", sub="u-bob", email="bob@corp.example",
                  json={"token": token})
    assert wrong.status_code == 400
    right = _call(world, ALICE, "POST", "/api/access/invites/redeem", sub="u-alice", email="Alice@Corp.Example",
                  json={"token": token})
    assert right.status_code == 200


def test_redeem_attempts_are_rate_limited_per_person(world):
    codes = [_call(world, ALICE, "POST", "/api/access/invites/redeem", sub="u-guesser", json={"token": f"bad{n}"}).status_code
             for n in range(14)]
    assert codes[:10] == [400] * 10 and set(codes[10:]) == {429}
    token = _token(_create(world))
    assert _call(world, ALICE, "POST", "/api/access/invites/redeem", sub="u-guesser", json={"token": token}).status_code == 429
    assert _call(world, ALICE, "POST", "/api/access/invites/redeem", sub="u-fresh", json={"token": token}).status_code == 200


def test_redeeming_needs_a_signed_in_session(world):
    assert world.client.post("/api/access/invites/redeem", json={"token": "x"}).status_code == 401


def test_without_an_access_policy_the_invite_routes_refuse_clearly(world):
    world.app.state.gateway_runner = SimpleNamespace(config=SimpleNamespace(access=parse_access_policy(None)))
    assert _call(world, ADMIN, "POST", "/api/access/invites", json={"profile": "alpha"}).status_code == 409
    assert _call(world, ALICE, "POST", "/api/access/invites/redeem", json={"token": "x"}).status_code == 409


def test_an_invite_must_name_a_real_profile_and_sane_options(world):
    for body in ({"profile": "ghost"}, {"profile": "../x"}, {"profile": "alpha", "max_uses": 0},
                 {"profile": "alpha", "ttl_hours": 99999}, {"profile": "alpha", "email": "nope"}):
        assert _call(world, ADMIN, "POST", "/api/access/invites", json=body).status_code in (400, 422), body
    assert _call(world, ADMIN, "POST", "/api/access/invites", json={}).status_code == 422
    assert _call(world, ADMIN, "POST", "/api/access/invites", json={"profile": "default"}).status_code == 200


def test_listing_shows_status_but_never_a_token(world):
    created = _create(world)
    listing = _call(world, ADMIN, "GET", "/api/access/invites").json()
    assert [(row["id"], row["status"], row["profile"]) for row in listing["invites"]] == [
        (created["invite_id"], "active", "alpha")]
    assert _token(created) not in json.dumps(listing)


def test_revoking_an_invite_with_grants_takes_the_access_away(world):
    created = _create(world, max_uses=2)
    _call(world, ALICE, "POST", "/api/access/invites/redeem", sub="u-alice", json={"token": _token(created)})
    assert _call(world, ALICE, "GET", "/api/config?profile=alpha", sub="u-alice").status_code == 200
    revoked = _call(world, ADMIN, "DELETE", f"/api/access/invites/{created['invite_id']}?revoke_grants=true",
                    sub="u-admin")
    assert revoked.status_code == 200
    assert _call(world, ALICE, "GET", "/api/config?profile=alpha", sub="u-alice").status_code == 403


def test_grants_can_be_listed_and_revoked_by_an_admin(world):
    _call(world, ALICE, "POST", "/api/access/invites/redeem", sub="u-alice", json={"token": _token(_create(world))})
    grants = _call(world, ADMIN, "GET", "/api/access/grants").json()["grants"]
    assert [g["profile"] for g in grants] == ["alpha"]
    subject = grants[0]["subject"]
    assert _call(world, ADMIN, "DELETE", "/api/access/grants", params={"subject": subject, "profile": "alpha"}).status_code == 200
    assert _call(world, ALICE, "GET", "/api/config?profile=alpha", sub="u-alice").status_code == 403
    assert _call(world, ADMIN, "DELETE", "/api/access/grants", params={"subject": subject, "profile": "alpha"}).status_code == 404


def test_invite_lifecycle_is_audited_and_the_token_is_never_logged(world, monkeypatch, caplog):
    from hermes_cli.web_routers import access_invites

    events = []
    monkeypatch.setattr(access_invites, "audit_log", lambda event, **fields: events.append((event.value, fields)))
    with caplog.at_level("DEBUG"):
        created = _create(world)
        token = _token(created)
        _call(world, ALICE, "POST", "/api/access/invites/redeem", sub="u-bad", json={"token": "wrong-token-value"})
        _call(world, ALICE, "POST", "/api/access/invites/redeem", sub="u-alice", json={"token": token})
        _call(world, ADMIN, "DELETE", f"/api/access/invites/{created['invite_id']}", sub="u-admin")
    assert [e for e, _ in events] == ["access_invite_created", "access_invite_redeem_failed",
                                      "access_invite_redeemed", "access_invite_revoked"]
    dump = json.dumps(events, default=str) + " ".join(r.getMessage() for r in caplog.records)
    assert token not in dump and "wrong-token-value" not in dump


def test_route_classification_for_the_invite_api():
    assert classify("POST", "/api/access/invites/redeem") == "open"
    for method, path in (("POST", "/api/access/invites"), ("GET", "/api/access/invites"),
                         ("DELETE", "/api/access/invites/abc"), ("GET", "/api/access/grants"),
                         ("DELETE", "/api/access/grants")):
        assert classify(method, path) == "admin", (method, path)


def test_the_real_dashboard_app_serves_the_invite_routes():
    from hermes_cli import web_server

    served = {(method, route.path) for route in web_server.app.routes
              for method in (getattr(route, "methods", None) or ())}
    for expected in (("POST", "/api/access/invites"), ("GET", "/api/access/invites"),
                     ("DELETE", "/api/access/invites/{invite_id}"), ("GET", "/api/access/grants"),
                     ("DELETE", "/api/access/grants"), ("POST", "/api/access/invites/redeem")):
        assert expected in served, expected
