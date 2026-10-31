"""Admin invite API and the redeem route (see ``gateway/access_grants.py``).

An admin invites a person to one profile and gets a ``sentio://connect`` link; the person signs in with
the company SSO, the desktop redeems the token, and the grant is stored for their verified subject.
Managing invites and grants is admin-only (the gate's default for ``/api/access/*``); redeeming is open to any
signed-in user and rate-limited. The token is shown once and is never logged or audited.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from hermes_cli.dashboard_auth.access import policy_for_app
from hermes_cli.dashboard_auth.audit import AuditEvent, audit_log
from hermes_cli.dashboard_auth.prefix import prefix_from_request, resolve_public_url

_log = logging.getLogger(__name__)
router = APIRouter()

# Failed redeem attempts allowed per window, per person and per client address: a token is 256 bits, so this
# is about containing abuse and log noise, not about making guessing feasible.
_WINDOW_SECONDS = 600
_MAX_FAILURES = {"subject": 10, "ip": 30}
_failures: dict[str, deque] = defaultdict(deque)
_failures_lock = threading.Lock()


def reset_rate_limits() -> None:
    with _failures_lock:
        _failures.clear()


def _recent(key: str, now: float) -> deque:
    attempts = _failures[key]
    while attempts and attempts[0] <= now - _WINDOW_SECONDS:
        attempts.popleft()
    return attempts


def _locked_out(keys: dict[str, str], now: float) -> bool:
    with _failures_lock:
        return any(len(_recent(key, now)) >= _MAX_FAILURES[kind] for kind, key in keys.items())


def _record_failure(keys: dict[str, str], now: float) -> None:
    with _failures_lock:
        for key in keys.values():
            _recent(key, now).append(now)


class InviteCreate(BaseModel):
    profile: str
    ttl_hours: int = Field(default=24 * 7, ge=1, le=24 * 30)
    max_uses: int = Field(default=1, ge=1, le=1000)
    email: Optional[str] = None


class RedeemBody(BaseModel):
    token: str


def _configured_policy(request: Request):
    policy = policy_for_app(request.app)
    if not policy.configured:
        raise HTTPException(status_code=409, detail="access_policy_not_configured")
    return policy


def _actor(request: Request, policy) -> str:
    session = getattr(request.state, "session", None)
    if session is None:
        return "local"
    return policy.subject_key(session.provider, session.issuer, session.user_id)


def _store():
    from gateway.access_grants import default_store

    return default_store()


def _profile_exists(name: str) -> bool:
    if name == "default":
        return True
    from hermes_cli.profiles import get_profile_dir

    return get_profile_dir(name).is_dir()


def _link(request: Request, token: str) -> str:
    base = resolve_public_url() or str(request.base_url).rstrip("/") + prefix_from_request(request)
    return "sentio://connect?" + urlencode({"url": base, "invite": token})


@router.post("/api/access/invites")
async def create_invite(body: InviteCreate, request: Request):
    policy = _configured_policy(request)
    from gateway.access_policy import _profile

    name = _profile(body.profile)
    if name is None:
        raise HTTPException(status_code=400, detail="invalid_profile")
    if not _profile_exists(name):
        raise HTTPException(status_code=400, detail="unknown_profile")
    actor = _actor(request, policy)
    try:
        created = _store().create_invite(profile=name, created_by=actor, ttl_hours=body.ttl_hours,
                                         max_uses=body.max_uses, email=body.email)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    audit_log(AuditEvent.ACCESS_INVITE_CREATED, invite_id=created.invite_id, profile=name, created_by=actor,
              max_uses=body.max_uses, expires_at=created.expires_at)
    return {"invite_id": created.invite_id, "profile": name, "expires_at": created.expires_at,
            "link": _link(request, created.token)}


@router.get("/api/access/invites")
async def list_invites(request: Request):
    _configured_policy(request)
    return {"invites": _store().list_invites()}


@router.delete("/api/access/invites/{invite_id}")
async def revoke_invite(invite_id: str, request: Request, revoke_grants: bool = False):
    policy = _configured_policy(request)
    if not _store().revoke_invite(invite_id, revoke_grants=revoke_grants):
        raise HTTPException(status_code=404, detail="not_found")
    audit_log(AuditEvent.ACCESS_INVITE_REVOKED, invite_id=invite_id, revoked_by=_actor(request, policy),
              revoke_grants=revoke_grants)
    return {"revoked": True}


@router.get("/api/access/grants")
async def list_grants(request: Request):
    _configured_policy(request)
    return {"grants": _store().list_grants()}


@router.delete("/api/access/grants")
async def revoke_grant(subject: str, profile: str, request: Request):
    policy = _configured_policy(request)
    if not _store().revoke_grant(subject, profile):
        raise HTTPException(status_code=404, detail="not_found")
    audit_log(AuditEvent.ACCESS_INVITE_REVOKED, subject=subject, profile=profile, revoked_by=_actor(request, policy))
    return {"revoked": True}


@router.post("/api/access/invites/redeem")
async def redeem_invite(body: RedeemBody, request: Request):
    from gateway.access_grants import InviteError

    session = getattr(request.state, "session", None)
    if session is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    policy = _configured_policy(request)
    subject = policy.subject_key(session.provider, session.issuer, session.user_id)
    keys = {"subject": f"s:{subject}", "ip": f"ip:{request.client.host if request.client else '?'}"}
    now = time.time()
    if _locked_out(keys, now):
        raise HTTPException(status_code=429, detail="too_many_attempts")
    try:
        profile, invite_id = _store().redeem_with_id(body.token, subject=subject, email=session.email)
    except InviteError:
        _record_failure(keys, now)
        audit_log(AuditEvent.ACCESS_INVITE_REDEEM_FAILED, subject=subject)
        raise HTTPException(status_code=400, detail="invalid_invite") from None
    audit_log(AuditEvent.ACCESS_INVITE_REDEEMED, subject=subject, profile=profile, invite_id=invite_id)
    return {"profile": profile}
