"""Profile scope of a dashboard WebSocket, derived from the verified identity and the access policy.

``/api/ws`` used to stamp ``profile_scope: 'host'`` and ``operator=True`` on every authenticated
socket. With an ``access`` policy configured, only an admin (or a trusted server-side identity) keeps
that; everyone else gets the profiles their groups grant, none for an unknown user. Without a policy
the dashboard is unchanged (single-user / local installs).
"""

from __future__ import annotations

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


def scope_for_identity(identity: dict, policy: Any) -> tuple[dict, bool]:
    """``(identity to hand the gateway, operator)`` for one authenticated dashboard socket."""
    if not policy.configured or (identity.get('provider'), identity.get('user_id')) in _TRUSTED:
        return {**identity, 'profile_scope': 'host'}, True
    forwarded = {key: identity[key] for key in _FEDERATED_FIELDS if identity.get(key)}
    grant = policy.grant_for(provider=str(identity.get('provider') or ''), issuer=str(identity.get('issuer') or ''),
                             sub=str(identity.get('user_id') or ''), groups=identity.get('groups') or ())
    if grant.admin:
        return {**forwarded, 'profile_scope': 'host'}, True
    return {**forwarded, 'allowed_profiles': sorted(grant.profiles)}, False
