"""Profile scope of a dashboard WebSocket, derived from the verified identity and the access policy.

``/api/ws`` used to stamp ``profile_scope: 'host'`` and ``operator=True`` on every authenticated
socket. With an ``access`` policy configured, only an admin (or a trusted server-side identity) keeps
that; everyone else gets the profiles their groups grant, none for an unknown user. Without a policy
the dashboard is unchanged (single-user / local installs).
"""

from __future__ import annotations

import logging
from typing import Any

from hermes_cli.dashboard_auth.ws_tickets import INTERNAL_PROVIDER, INTERNAL_USER_ID

# Identities only the server itself can produce (the PTY child's internal credential, and the
# launch-time session token on an ungated host); a federated provider cannot register these names.
_TRUSTED = frozenset({(INTERNAL_PROVIDER, INTERNAL_USER_ID), ('session-token', 'legacy-token-owner')})
# The only fields a federated identity forwards: scope and capabilities are never taken from it.
_FEDERATED_FIELDS = ('user_id', 'provider', 'issuer')


def policy_for_app(app: Any):
    """The launch profile's access policy: the running gateway's, else a fresh read of config.yaml."""
    runner = getattr(getattr(app, 'state', None), 'gateway_runner', None)
    config = getattr(runner, 'config', None)
    if config is not None and hasattr(config, 'access'):
        return config.access
    from gateway.config import load_gateway_config

    return load_gateway_config().access


def grant_for_identity(policy: Any, *, provider: str, issuer: str, sub: str, groups: Any = ()):
    """The caller's grant: IdP groups and config plus any stored grant (a redeemed invite). An unreadable
    grant store fails closed (no stored access), never open."""
    extra: Any = ()
    if policy.configured and sub:
        try:
            from gateway.access_grants import default_store

            extra = default_store().profiles_for(policy.subject_key(provider, issuer, sub))
        except Exception:  # health: allow BLE001 -- a broken store must not take the dashboard down or widen access
            logging.getLogger(__name__).exception("access grant store unreadable; ignoring stored grants")
    return policy.grant_for(provider=provider, issuer=issuer, sub=sub, groups=groups, extra_profiles=extra)


def scope_for_identity(identity: dict, policy: Any) -> tuple[dict, bool]:
    """``(identity to hand the gateway, operator)`` for one authenticated dashboard socket."""
    if not policy.configured or (identity.get('provider'), identity.get('user_id')) in _TRUSTED:
        return {**identity, 'profile_scope': 'host'}, True
    forwarded = {key: identity[key] for key in _FEDERATED_FIELDS if identity.get(key)}
    grant = grant_for_identity(policy, provider=str(identity.get('provider') or ''),
                               issuer=str(identity.get('issuer') or ''), sub=str(identity.get('user_id') or ''),
                               groups=identity.get('groups') or ())
    if grant.admin:
        return {**forwarded, 'profile_scope': 'host'}, True
    return {**forwarded, 'allowed_profiles': sorted(grant.profiles)}, False


def _requested_profile_name(raw: Any):
    """The canonical profile a ``?profile=`` selector names, or None when it is not a valid name.
    Absent, empty and ``current`` mean the launch profile, as in the routes that read the param."""
    from hermes_cli.profiles import normalize_profile_name, validate_profile_name
    from hermes_constants import get_routing_process_hermes_home, profile_name_for_home

    text = str(raw or '').strip()
    if not text or text.lower() == 'current':
        return profile_name_for_home(get_routing_process_hermes_home()) or 'default'
    try:
        name = normalize_profile_name(text)
        validate_profile_name(name)
    except ValueError:
        return None
    return name


def _ws_allowed_profiles(ws: Any):
    """The upgrade's granted profiles, ``None`` when it is not grant-scoped (admin, no policy, ungated
    host). A gated host with no stamped identity is never trusted: it gets an empty grant."""
    identity = getattr(ws, '_hermes_auth_identity', None)
    app = ws.app
    if identity is None:
        return frozenset() if getattr(app.state, 'auth_required', False) else None
    scoped, _ = scope_for_identity(identity, policy_for_app(app))
    allowed = scoped.get('allowed_profiles')
    return None if allowed is None else frozenset(allowed)


def ws_is_grant_scoped(ws: Any) -> bool:
    return _ws_allowed_profiles(ws) is not None


def ws_profile_permitted(ws: Any) -> bool:
    """True when the upgrade's ``?profile=`` is one its caller may use."""
    allowed = _ws_allowed_profiles(ws)
    if allowed is None:
        return True
    return _requested_profile_name(ws.query_params.get('profile')) in allowed
