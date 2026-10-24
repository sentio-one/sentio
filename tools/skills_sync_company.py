"""Which bundled skills a sandboxed company profile is seeded with (private fork, docs/private-fork/FORK_DECISIONS.md D16).

A routed profile under an ``access:`` policy belongs to a customer's employee, not to a Hermes developer: it gets the
essentials plus a small business baseline instead of every bundled skill, and never ``profile-provisioning`` (the admin's
how-to for creating profiles). The launch profile keeps the full set. The baseline can be changed per deployment with
``skills.company_baseline`` in the managed config; profile templates (SANDBOX_PLAN phase 6) will name it per kind of work.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from agent.skill_utils import ESSENTIAL_SKILLS
from tools.terminal_scope_sandbox import is_sandboxed_profile

logger = logging.getLogger(__name__)

DEFAULT_COMPANY_BASELINE = frozenset({
    "docx", "pdf", "xlsx", "powerpoint", "document-to-action-items", "meeting-action-items",
    "weekly-review-planning", "grounded-citations", "competitor-news-monitor"})
# Never seeded into a sandboxed profile, whatever the baseline says.
ADMIN_ONLY_SKILLS = frozenset({"profile-provisioning"})


def _configured_baseline(home: Path) -> Optional[frozenset]:
    from gateway.config_loader import read_yaml_layers

    try:
        names = (read_yaml_layers(home).get("skills") or {}).get("company_baseline")
    except (OSError, ValueError):
        logger.warning("config.yaml of %s is unreadable: using the default company skill baseline", home)
        return None
    return frozenset(n for n in names if isinstance(n, str)) if isinstance(names, list) else None


def company_skill_filter(home: Path) -> Optional[frozenset]:
    """The only bundled skill names to seed into *home*, or None for every one (the launch profile)."""
    if not is_sandboxed_profile(home):
        return None
    baseline = _configured_baseline(home)
    return (ESSENTIAL_SKILLS | (DEFAULT_COMPANY_BASELINE if baseline is None else baseline)) - ADMIN_ONLY_SKILLS


def trim_to_company_baseline() -> dict:
    """Remove the unmodified bundled skills a sandboxed profile should not have, then sync. Anything the user edited,
    and anything not bundled, stays. Runs for the profile ``HERMES_HOME`` names."""
    from tools.skills_sync import _hermes_home, sync_skills
    from tools.skills_sync_bundled_ops import remove_pristine_bundled_skills

    keep = company_skill_filter(_hermes_home())
    removed = remove_pristine_bundled_skills(keep=keep) if keep is not None else {"removed": [], "skipped": []}
    sync_skills(quiet=True)
    return {"removed": removed["removed"], "kept_modified": [s["name"] for s in removed["skipped"]]}
