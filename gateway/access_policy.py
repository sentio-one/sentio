"""Per-user profile access policy for a centrally hosted gateway (``access:`` in config.yaml).

Claim-driven with a config override. The IdP's ``groups`` claim is the source of truth
(``<profile_group_prefix><profile>`` grants a profile, ``admin_groups`` grants all); ``users`` and
``admins`` add static grants for IdPs that cannot emit groups. Everything is fail closed: an
unconfigured policy, an unknown caller and every malformed row resolve to no profiles.

Subjects are the dashboard's own ``(provider, issuer, sub)`` triple, never a bare ``sub`` or an
issuer host, so two issuers (or two tenants behind one issuer host) never share a grant. Group
grants count only for ``group_providers``: a provider whose claims Hermes does not trust for
authorization cannot mint a grant by emitting a matching group name.

Modeled on ``gateway/profile_routing.py``: validate profile names, log and skip bad rows.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional

logger = logging.getLogger(__name__)

DEFAULT_GROUP_PREFIX = "hermes-profile-"
DEFAULT_GROUP_PROVIDERS = frozenset({"self_hosted"})


def _profile(name: Any) -> Optional[str]:
    """The canonical profile id for *name*, or None when it is not a valid profile name."""
    if not isinstance(name, str):
        return None
    from hermes_cli.profiles import normalize_profile_name, validate_profile_name

    try:
        canonical = normalize_profile_name(name.strip())
        validate_profile_name(canonical)
    except ValueError:
        return None
    return canonical


@dataclass(frozen=True)
class Grant:
    """What one authenticated caller may reach. ``admin`` implies every served profile."""

    admin: bool = False
    profiles: frozenset = frozenset()

    def allows(self, profile: Any) -> bool:
        if self.admin:
            return True
        canonical = _profile(profile)
        return canonical is not None and canonical in self.profiles


@dataclass(frozen=True, eq=False)
class AccessPolicy:
    """Parsed ``access:`` section. ``configured`` is False when none was set, which callers treat
    as "no federated access control" (the local single-user path); a configured policy never
    widens beyond its rows."""

    configured: bool = False
    admin_groups: frozenset = frozenset()
    group_prefix: str = DEFAULT_GROUP_PREFIX
    group_providers: frozenset = DEFAULT_GROUP_PROVIDERS
    users: Mapping = field(default_factory=dict)  # subject -> frozenset of profiles
    admins: frozenset = frozenset()  # subjects

    @staticmethod
    def subject_key(provider: Any, issuer: Any, sub: Any) -> str:
        from gateway.session_identity import dashboard_subject

        return dashboard_subject(provider, issuer, sub)

    def grant_for(self, *, provider: str, issuer: str, sub: str, groups: Iterable[str] = (),
                  extra_profiles: Iterable[str] = ()) -> Grant:
        """``extra_profiles`` are stored grants (``gateway/access_grants.py``, e.g. redeemed invites): validated
        like every other source, and never an admin grant."""
        if not self.configured or not sub:
            return Grant()
        subject = self.subject_key(provider, issuer, sub)
        profiles = set(self.users.get(subject, ())) | {p for p in map(_profile, extra_profiles) if p}
        admin = subject in self.admins
        if provider in self.group_providers:
            groups = tuple(groups)
            admin = admin or any(g in self.admin_groups for g in groups)
            profiles |= self._group_profiles(groups)
        return Grant(admin=admin, profiles=frozenset(profiles))

    def _group_profiles(self, groups: Iterable[str]) -> set:
        if not self.group_prefix:
            return set()
        found = (_profile(g[len(self.group_prefix):]) for g in groups if g.startswith(self.group_prefix))
        return {p for p in found if p}


def _strings(value: Any, what: str) -> frozenset:
    if value is None:
        return frozenset()
    if not isinstance(value, list):
        logger.warning("Ignoring access.%s: expected a list, got %s", what, type(value).__name__)
        return frozenset()
    return frozenset(v.strip() for v in value if isinstance(v, str) and v.strip())


def _row_subject(row: Any, what: str) -> Optional[str]:
    """The subject key for one ``users``/``admins`` row, or None (logged) when it names no full triple."""
    parts = tuple(row.get(k) if isinstance(row, dict) else None for k in ("provider", "issuer", "sub"))
    if not all(isinstance(p, str) and p.strip() for p in parts):
        logger.warning("Skipping access.%s row: provider, issuer and sub are all required", what)
        return None
    return AccessPolicy.subject_key(*(p.strip() for p in parts))


def _parse_users(raw: Any) -> dict:
    users: dict = {}
    for row in raw if isinstance(raw, list) else ():
        subject = _row_subject(row, "users")
        names = row.get("profiles") if subject and isinstance(row, dict) else None
        if subject is None or not isinstance(names, list):
            if subject is not None:
                logger.warning("Skipping access.users row: profiles must be a list")
            continue
        valid = {p for p in map(_profile, names) if p}
        users[subject] = frozenset(users.get(subject, frozenset()) | valid)
    return users


def parse_access_policy(raw: Any) -> AccessPolicy:
    """Parse the ``access:`` mapping; anything but a non-empty mapping is an unconfigured policy."""
    if raw is None or raw == {}:
        return AccessPolicy()
    if not isinstance(raw, dict):
        logger.warning("Ignoring access: expected a mapping, got %s", type(raw).__name__)
        return AccessPolicy()
    prefix = raw.get("profile_group_prefix", DEFAULT_GROUP_PREFIX)
    if not isinstance(prefix, str) or not prefix:
        # An empty prefix would turn every group name into a profile name.
        logger.warning("access.profile_group_prefix is empty or invalid: group-to-profile grants are off")
        prefix = ""
    providers = _strings(raw.get("group_providers"), "group_providers") or DEFAULT_GROUP_PROVIDERS
    rows = raw.get("admins")
    admins = {s for s in (_row_subject(r, "admins") for r in rows) if s} if isinstance(rows, list) else set()
    return AccessPolicy(
        configured=True, admin_groups=_strings(raw.get("admin_groups"), "admin_groups"),
        group_prefix=prefix, group_providers=providers, users=_parse_users(raw.get("users")),
        admins=frozenset(admins))
