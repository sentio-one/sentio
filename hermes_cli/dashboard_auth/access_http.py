"""Per-user authorization of dashboard REST routes (``access:`` policy, private fork).

The gate in ``middleware.py`` authenticates every ``/api/*`` call but, before this, every signed-in
user reached every route. With an access policy configured, a non-admin is limited to:

* ``open``    - account and discovery routes any signed-in user needs;
* ``profile`` - routes that act on one profile, only on a profile the user was granted;
* ``admin``   - everything else, which is the default for a route nobody classified.

A route is host-level (the filesystem, git, plugin install, update, the gateway process, other
profiles' lifecycle) or unclassified => ``admin``. Without a policy nothing here runs, so single-user
installs are unchanged.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from starlette.responses import JSONResponse, Response

_log = logging.getLogger(__name__)

# Host-level even where the handler also takes a ``profile`` (``/api/fs`` reads any path on the host).
_ADMIN_PREFIXES = (
    "/api/fs", "/api/files", "/api/media", "/api/git", "/api/gateway", "/api/hermes", "/api/ops",
    "/api/local-models", "/api/logs", "/api/actions", "/api/host", "/api/system", "/api/ssh",
    "/api/dashboard/agent-plugins", "/api/dashboard/plugins/catalog", "/api/dashboard/plugins/rescan",
    "/api/providers/validate", "/api/providers/custom-endpoints/validate",
    "/api/messaging/telegram/onboarding", "/api/messaging/whatsapp/onboarding",
    # Delegation routing names every profile on the host and a ``profile_routes`` backend runs subagents in
    # another profile's scope: a granted user must not configure or even list it.
    "/api/delegation")
_OPEN_PREFIXES = ("/api/auth",)
_OPEN_EXACT = frozenset({("GET", "/api/health"), ("GET", "/api/health/idle"), ("GET", "/api/config/defaults"),
                         # Any signed-in user redeems their own invite; managing invites stays admin.
                         ("POST", "/api/access/invites/redeem")})
_PROFILE_PREFIXES = (
    "/api/config", "/api/env", "/api/sessions", "/api/skills", "/api/tools", "/api/mcp", "/api/model",
    "/api/memory", "/api/curator", "/api/learning", "/api/cron", "/api/analytics", "/api/credentials",
    "/api/providers", "/api/chat", "/api/status", "/api/egress", "/api/portal", "/api/webhooks",
    "/api/messaging", "/api/dashboard", "/api/shared-metrics", "/api/audio", "/api/pairing")
# These read an absent / empty / ``current`` selector as "every profile": a grant-scoped caller must name one.
_EXPLICIT_PREFIXES = ("/api/sessions", "/api/cron/jobs", "/api/profiles/")
# ``/api/profiles/<aggregate>``: the selector keys the handler reads, all required.
_AGGREGATES = {
    "/api/profiles/sessions": ("profile",), "/api/profiles/projects/tree": ("profile",),
    "/api/profiles/sessions/pull-requests": ("profile",),
    "/api/profiles/sessions/sidebar": ("profile", "recents_profile")}
# ``/api/profiles/<name>/<sub>``: methods that act on that one profile's own settings.
_PROFILE_SUBROUTES = {"soul": {"GET", "PUT"}, "description": {"PUT"}, "model": {"PUT"}, "desktop-overlay": {"GET"},
                      "setup-command": {"GET"}, "describe-auto": {"POST"}}


def _under(path: str, prefixes) -> bool:
    return any(path == p or path.startswith(p + "/") for p in prefixes)


def _profiles_class(method: str, path: str) -> str:
    if path in _AGGREGATES:
        return "profile"
    if path in ("/api/profiles", "/api/profiles/active"):
        return "open" if method == "GET" else "admin"
    parts = path.split("/")  # ['', 'api', 'profiles', name, sub...]
    if len(parts) == 5 and method in _PROFILE_SUBROUTES.get(parts[4], ()):
        return "profile"
    return "admin"


def classify(method: str, path: str) -> str:
    """``open`` | ``profile`` | ``admin`` for one route; default-deny for anything unlisted."""
    path = path.rstrip("/") or "/"
    method = method.upper()
    if path == "/api/profiles" or path.startswith("/api/profiles/"):
        return _profiles_class(method, path)
    if _under(path, _ADMIN_PREFIXES):
        return "admin"
    if (method, path) in _OPEN_EXACT or _under(path, _OPEN_PREFIXES):
        return "open"
    return "profile" if _under(path, _PROFILE_PREFIXES) else "admin"


async def _selectors(request: Any) -> list:
    """Every profile selector the request carries, in the places the handlers read them: ``?profile=``,
    ``?recents_profile=``, a JSON body ``profile`` and the ``/api/profiles/<name>`` path segment."""
    values = [*request.query_params.getlist("profile"), *request.query_params.getlist("recents_profile")]
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if not content_type or content_type == "application/json" or content_type.endswith("+json"):
        try:
            body = await request.json()
        except (ValueError, UnicodeError):
            body = None  # route validation still owns malformed JSON
        if isinstance(body, dict) and "profile" in body:
            values.append(body["profile"] if isinstance(body["profile"], str) else "")
    parts = request.url.path.split("/")
    if len(parts) >= 5 and parts[1:3] == ["api", "profiles"] and request.url.path not in _AGGREGATES:
        values.append(parts[3])
    return values


def _named(value: str, explicit: bool) -> Optional[str]:
    from hermes_cli.dashboard_auth.access import _requested_profile_name

    text = value.strip().lower()
    if text == "all" or (explicit and text in ("", "current")):
        return None
    return _requested_profile_name(value)


async def _profile_refused(request: Any, granted: frozenset) -> bool:
    path = request.url.path.rstrip("/")
    values = await _selectors(request)
    required = _AGGREGATES.get(path, ())
    if any(key not in request.query_params for key in required):
        return True
    explicit = _under(path, _EXPLICIT_PREFIXES) or path.startswith("/api/profiles/")
    if not values:
        values = [""]
        if explicit:
            return True
    return any(_named(value, explicit) not in granted for value in values)


def _refusal(request: Any, subject: str, reason: str, detail: str) -> Response:
    _log.warning("rest access denied: subject=%s method=%s path=%s reason=%s",
                 subject, request.method, request.url.path, reason)
    return JSONResponse({"detail": detail}, status_code=403)


async def enforce(request: Any, session: Any, policy: Any) -> Optional[Response]:
    """The refusal for *request* under *policy*, or None to serve it. Stamps
    ``request.state.access_allowed_profiles`` (None = not restricted) for handlers that filter."""
    request.state.access_allowed_profiles = None
    if not policy.configured:
        return None
    from hermes_cli.dashboard_auth.access import grant_for_identity

    grant = grant_for_identity(policy, provider=session.provider, issuer=session.issuer, sub=session.user_id,
                               groups=session.groups, verified_email=session.verified_email)
    if grant.admin:
        return None
    request.state.access_allowed_profiles = grant.profiles
    route = classify(request.method, request.url.path)
    subject = policy.subject_key(session.provider, session.issuer, session.user_id)
    if route == "admin":
        return _refusal(request, subject, "admin_required", "admin_required")
    if route == "profile" and await _profile_refused(request, grant.profiles):
        return _refusal(request, subject, "profile_not_granted", "profile_scope_mismatch")
    return None
