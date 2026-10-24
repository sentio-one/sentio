"""Which routed profiles use the company's own model keys (private fork, docs/private-fork/FORK_DECISIONS.md D15).

A routed profile's secret scope is its own ``.env`` (profiles are islands), so a sandboxed profile starts with no model
key. Sharing is an admin-side choice kept in the launch home, never in the profile's own files, and it carries provider
API keys only: never bot tokens, tool secrets or the rest of the launch environment. The profile's own ``.env`` still
wins, so a profile that is given its own key keeps it.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

FILE_NAME = "profile_key_sharing.json"
_KEY_SUFFIX = "_API_KEY"
_URL_SUFFIX = "_BASE_URL"


def _path() -> Path:
    from hermes_constants import get_routing_process_hermes_home

    return get_routing_process_hermes_home() / FILE_NAME


def sharing_profiles() -> frozenset:
    """Profiles an admin let use the company's keys. An unreadable or malformed file shares nothing."""
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return frozenset()
    except (OSError, ValueError):
        logger.warning("%s is unreadable: no profile shares the company's keys", FILE_NAME)
        return frozenset()
    names = raw.get("profiles") if isinstance(raw, dict) else None
    return frozenset(n for n in names if isinstance(n, str)) if isinstance(names, list) else frozenset()


def set_sharing(profile: str, enabled: bool) -> None:
    """Let *profile* (a named, non-launch profile) use the company's keys, or stop."""
    from hermes_cli.profiles import normalize_profile_name, validate_profile_name
    from utils import atomic_json_write

    name = normalize_profile_name(profile.strip())
    validate_profile_name(name)
    if name == "default":
        raise ValueError("the launch profile already has the company's keys")
    current = sharing_profiles()
    updated = current | {name} if enabled else current - {name}
    atomic_json_write(_path(), {"profiles": sorted(updated)}, mode=0o600)


def _shared_names() -> frozenset:
    """Provider API keys and the base URLs that go with them, as the config declares them (category ``provider``)."""
    from hermes_cli.config import OPTIONAL_ENV_VARS

    return frozenset(
        name for name, meta in OPTIONAL_ENV_VARS.items()
        if meta.get("category") == "provider"
        and (name.endswith(_KEY_SUFFIX) and meta.get("password") or name.endswith(_URL_SUFFIX)))


def shared_provider_credentials() -> dict:
    """The company's provider API keys: those in the launch process environment and in the launch ``.env``."""
    from agent.secret_scope import load_env_file
    from hermes_constants import get_routing_process_hermes_home

    names = _shared_names()
    launch_env = load_env_file(get_routing_process_hermes_home() / ".env")
    return {**{n: os.environ[n] for n in names if os.environ.get(n)},
            **{n: v for n, v in launch_env.items() if n in names and v}}


def shared_credentials_for(hermes_home: Path) -> dict:
    """What the admin shared with the profile living at *hermes_home*, or nothing."""
    home = Path(hermes_home)
    if home.parent.name != "profiles" or home.name not in sharing_profiles():
        return {}
    return shared_provider_credentials()
