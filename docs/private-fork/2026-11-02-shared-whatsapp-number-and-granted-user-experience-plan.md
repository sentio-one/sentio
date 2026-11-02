# Shared WhatsApp Number and Granted-User Experience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for small and medium plans, or superpowers:subagent-driven-development when the plan is large enough that per-task subagents and review repay their cost. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One WhatsApp number serves many people (owner on the default profile, everyone else on their own sandboxed profile, unrouted senders refused), and a non-admin desktop user gets their granted profile, its workspace files, and a Sentio identity.

**Architecture:** Reuse what exists. WhatsApp: `gateway.profile_routes` already routes a sender to a profile; add an explicit `gateway.owners` list and an admission gate so an unrouted sender can never fall through to the host-level default profile, plus one shared `whatsapp_users` module behind a CLI and admin-only REST endpoints. Desktop: make `GET /api/profiles/active` follow the caller's grant, scope the existing managed-files policy and the `/api/fs` routes to the granted profile's `workspace/` directory, and change the default `SOUL.md` text (the existing in-place upgrade path moves old profiles over).

**Tech Stack:** Python 3 (gateway, `hermes_cli`, FastAPI/Starlette), pytest through `scripts/run_tests.sh`, argparse.

**Spec:** `docs/private-fork/2026-11-02-shared-whatsapp-number-and-granted-user-experience.md` (read it first; the "Spec corrections" section at its end overrides earlier text where they differ).

## Execution status ? 2026-11-02 handoff

Branch: `feat/shared-whatsapp-granted-user`. Tasks 1-11 implementation and scoped reviews are complete, with Task 7 limited to dashboard diagnosis. **Task 12 broad regression, whole-branch review, deployment and live acceptance are deferred to the next machine.** This is not release approval. The [portable handoff](2026-11-02-shared-whatsapp-number-and-granted-user-experience-handoff.md) carries commit mapping, test receipts, limitations and exact next commands; local ignored `.superpowers/` evidence will not follow a clone.

| Task | Recorded status | Remaining verification |
|---|---|---|
| 1 | Routing baseline: 3 passed; reviewed | Real phones/bridge |
| 2 | Owners gate: 31 passed; defaults extraction reviewed | Real unrouted refusal |
| 3 | Shared user service: 28 passed after review fixes | Broad regression; optional deletion cannot reconstruct deleted data |
| 4 | CLI: 34 passed with service; reviewed | Live setup/restart |
| 5 | REST: 35 passed; reviewed | Live authenticated deployment |
| 6 | WhatsApp guide and D17 complete; reviewed | None for documentation |
| 7 | Dashboard baseline recorded; scoped review passed | Native desktop/logs/model answer remain pending (provider 401) |
| 8 | Granted active profile: 41 passed; reviewed | Live post-deployment discovery |
| 9 | Granted managed files: Windows 81 passed/8 skipped; Linux 37 passed, final focused 13 passed; reviewed | Broad `tests/hermes_cli/ -k "files or managed"`; native Windows reparse |
| 10 | Granted fs + secure staging review fix: Linux 37 passed; Windows 139 passed/20 skipped; re-reviewed | Broad `tests/hermes_cli/ -k "fs or files or managed or hosted"`; live desktop |
| 11 | SOUL and real prompt build complete; review approved. Fresh native Linux 177 passed/4 skipped, exit 0 | Successful model identity answer; upstream Hermes helper conflict remains intentional |
| 12 | Full `python scripts/check`: **12 checks OK**, exit 0 after explicit subprocess-check lint fix | Fork decisions D18+; broad suites; whole-branch review; deployment; real phones and desktop |

Checkboxes below record performed work, not endorsement of every illustrative snippet. The spec corrections and reviewed final implementation supersede snippets. Partial verification steps stay unchecked with completed/deferred subparts. No broad suite or live acceptance is implied.

## Global Constraints

- Tests run through `scripts/run_tests.sh <path> [-k name]`, never bare `pytest`. Never write to `~/.hermes`; the `_isolate_hermes_home` fixture redirects `HERMES_HOME`.
- Test placement mirrors the source tree (`tests/gateway/`, `tests/hermes_cli/`). No issue numbers in filenames. Behaviour tests only: never read source text, no new `sleep`, no change-detector tests.
- Paths come from `get_hermes_home()` / `hermes_cli.profiles.get_profile_dir()`; never hardcode `~/.hermes`.
- `config.yaml` is written only through `hermes_cli.config.atomic_config_write` (additive) or `atomic_config_replace`; `.env` only through `hermes_cli.config.save_env_value`.
- Facade + siblings: new behaviour goes in new sibling modules, not in facades (`gateway/run.py`, `hermes_cli/main.py`). New functions: cyclomatic complexity <= 20, <= 300 lines, nesting <= 6.
- No defensive clutter, no dead code. Comments explain WHY, not WHAT.
- Each fix is shown failing on the base branch before its implementation is written (red first).
- Installs with no `access:` policy and no `gateway.owners` behave exactly as before.
- `/api/git`, `/api/media`, `/api/gateway`, `/api/hermes` and the rest of the admin-only prefixes in `hermes_cli/dashboard_auth/access_http.py` stay admin-only.
- Commit messages end with: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`
- Work on branch `feat/shared-whatsapp-granted-user` created from `docs/shared-whatsapp-granted-user`. Rebase onto `main` before merging. Run `python scripts/check` before the final task's commit.

---

## File Structure

**Workstream 1 (WhatsApp)**
- Create `gateway/whatsapp_owners.py`: `parse_owners`, `is_owner`, `owner_gate`. One purpose: decide whether an unrouted WhatsApp sender may run on the primary profile.
- Modify `gateway/config.py`, `gateway/config_loader.py`, `hermes_cli/config_defaults.py`: the `gateway.owners` key.
- Modify `gateway/authz_mixin.py`: one call to `owner_gate` at the top of `_principal_authorized`.
- Create `hermes_cli/whatsapp_users.py`: `add_user`, `list_users`, `remove_user` (the one implementation).
- Create `hermes_cli/whatsapp_users_cli.py`: argparse handlers that call it.
- Modify `hermes_cli/subcommands/whatsapp.py`, `hermes_cli/main_platform_setup.py`: sub-subcommands and dispatch.
- Create `hermes_cli/web_routers/whatsapp_users.py`; modify `hermes_cli/web_server.py`: admin-only endpoints.
- Tests: `tests/gateway/test_whatsapp_shared_number_routing.py`, `tests/gateway/test_whatsapp_owners.py`, `tests/hermes_cli/test_whatsapp_users.py`, `tests/hermes_cli/test_whatsapp_users_cli.py`, `tests/hermes_cli/test_whatsapp_users_api.py`.

**Workstream 2 (desktop)**
- Modify `hermes_cli/web_routers/profiles.py`: `GET /api/profiles/active` follows the grant.
- Modify `hermes_cli/web_server_files.py`: `_managed_files_policy` returns a grant-scoped root; new `restricted_files_root`.
- Modify `hermes_cli/web_routers/files.py`: `/api/fs/*` handlers jail restricted callers.
- Modify `hermes_cli/dashboard_auth/access_http.py`: `/api/fs` and `/api/files` become grant-scoped instead of admin-only.
- Modify `hermes_cli/default_soul.py`: Sentio `DEFAULT_SOUL_MD`, old text added to the legacy list.
- Tests: `tests/hermes_cli/test_granted_user_active_profile.py`, `tests/hermes_cli/test_granted_user_files.py`, extend `tests/hermes_cli/test_dashboard_rest_access.py`, `tests/hermes_cli/test_default_soul_identity.py`.

**Docs:** `website/docs/user-guide/messaging/whatsapp.md`, `docs/private-fork/FORK_DECISIONS.md`.

---

## Workstream 1: shared WhatsApp number

### Task 1: Prove the shared-number routing path (baseline probe)

**Files:**
- Create: `tests/gateway/test_whatsapp_shared_number_routing.py`

**Interfaces:**
- Consumes: `gateway.session_identity.resolve_identity`, `gateway.profile_routing.parse_profile_routes`, `gateway.run.GatewayRunner`.
- Produces: the `rig` fixture and `_dm` helper in this file, reused by Task 2.

This task changes no production code. It answers the open question from the spec: does a shared WhatsApp bot already route by sender to a satellite profile, in the identity shapes the bridge sends (`chatId` is a JID, a LID sender matches only through the bridge's `lid-mapping-*.json` files)?

- [x] **Step 1: Write the probe**

```python
"""One shared WhatsApp number: the owner runs on the default profile, routed people on their own.

Real ``GatewayRunner`` resolvers against a temp ``HERMES_HOME`` (same rig as
``test_multiplex_transport_matrix.py``), with WhatsApp DM identities in the shapes the bridge sends:
``chatId`` is a JID, and a LID sender only matches a bare-number route through the bridge's
``lid-mapping-*.json`` files.
"""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.pairing import PairingStore
from gateway.platforms.base import BasePlatformAdapter
from gateway.profile_routing import parse_profile_routes
from gateway.session_identity import resolve_identity

WHATSAPP = Platform("whatsapp")
OWNER = "27820000001"
SALES = "27820000002"
SALES_LID = "111222333"


class _Stub(BasePlatformAdapter):
    pass


_Stub.__abstractmethods__ = frozenset()


def _stub(runner):
    adapter = _Stub.__new__(_Stub)
    adapter.platform, adapter.gateway_runner, adapter.label = WHATSAPP, runner, "SHARED"
    adapter.config = PlatformConfig(enabled=True, extra={})
    adapter._pending_messages, adapter._active_sessions = {}, {}
    return adapter


@pytest.fixture
def rig(tmp_path, monkeypatch):
    from gateway.run import GatewayRunner

    home = tmp_path / "hh"
    (home / "profiles" / "sentio-sales").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    served = [("default", home), ("sentio-sales", home / "profiles" / "sentio-sales")]

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(multiplex_profiles=True)
    runner.config.platforms = {WHATSAPP: PlatformConfig(enabled=True, extra={})}
    runner.config.profile_routes = parse_profile_routes(
        [{"name": "sales", "platform": "whatsapp", "profile": "sentio-sales", "chat_id": SALES}])
    runner.pairing_store = PairingStore(profile="default")
    runner.pairing_stores = {}
    runner._primary_profile_name = "default"
    shared = _stub(runner)
    runner.adapters = {WHATSAPP: shared}
    runner._profile_adapters = {"sentio-sales": {}}
    with patch("hermes_cli.profiles.profiles_to_serve", return_value=served), \
            patch("hermes_cli.profiles.get_profile_dir",
                  side_effect=lambda n: home if n == "default" else home / "profiles" / n), \
            patch("hermes_cli.profiles.profile_exists", return_value=True):
        yield SimpleNamespace(runner=runner, shared=shared, home=home)


def _dm(rig, jid):
    source = rig.shared.build_source(chat_id=jid, chat_type="dm", user_id=jid)
    return resolve_identity(source, runner=rig.runner)


def test_owner_number_runs_on_the_default_profile(rig):
    identity = _dm(rig, f"{OWNER}@s.whatsapp.net")
    assert (identity.transport_profile, identity.runtime_profile) == ("default", "default")


def test_routed_number_runs_on_its_own_profile_in_jid_form(rig):
    identity = _dm(rig, f"{SALES}@s.whatsapp.net")
    assert (identity.transport_profile, identity.runtime_profile) == ("default", "sentio-sales")


def test_routed_number_is_found_when_the_sender_arrives_as_a_lid(rig):
    from hermes_constants import get_hermes_dir

    session = get_hermes_dir("platforms/whatsapp/session", "whatsapp/session")
    session.mkdir(parents=True, exist_ok=True)
    (session / f"lid-mapping-{SALES}.json").write_text(json.dumps(SALES_LID), encoding="utf-8")
    identity = _dm(rig, f"{SALES_LID}@lid")
    assert identity.runtime_profile == "sentio-sales"
```

- [x] **Step 2: Run the probe**

Run: `scripts/run_tests.sh tests/gateway/test_whatsapp_shared_number_routing.py -v`
Expected: 3 PASS. This is the one task whose tests are expected to pass on the base: they verify existing behaviour.

- [x] **Step 3: If any probe fails, stop**

A failure means the shared-bot to satellite path has a gap for WhatsApp (the spec's top risk). Do not continue. Record the failing test, its traceback and your diagnosis under "Risks" in the spec, commit that, and report to the user before building anything else. If all pass, continue.

- [x] **Step 4: Commit**

```bash
git switch -c feat/shared-whatsapp-granted-user
git add tests/gateway/test_whatsapp_shared_number_routing.py
git commit -m "test: pin shared WhatsApp number routing to owner and satellite profiles

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `gateway.owners` and the admission gate

**Files:**
- Create: `gateway/whatsapp_owners.py`
- Modify: `gateway/config.py` (field line 652, `to_dict` ~705-709, `from_dict` ~781-804)
- Modify: `gateway/config_loader.py:93` (bridge row)
- Modify: `hermes_cli/config_defaults.py:2108` (default + comment)
- Modify: `gateway/authz_mixin.py:653-658` (`_principal_authorized`)
- Test: `tests/gateway/test_whatsapp_owners.py` (new), `tests/gateway/test_whatsapp_shared_number_routing.py` (extend)

**Interfaces:**
- Consumes: `gateway.session_identity.identity_of(source)` returning an object with `transport_profile` and `runtime_profile` (or `None`); `gateway.whatsapp_identity.expand_whatsapp_aliases(identifier) -> set[str]`.
- Produces: `parse_owners(raw) -> list[str]`; `is_owner(owners: list[str], user_id: str | None) -> bool`; `owner_gate(owners, *, platform: str, user_id, identity, primary_profile: str) -> bool | None` (`False` = refuse, `None` = no opinion); `GatewayConfig.owners: list[str]`.

- [x] **Step 1: Write the failing unit tests** in `tests/gateway/test_whatsapp_owners.py`

```python
"""The owner gate: an unrouted WhatsApp sender may run on the primary profile only if listed in gateway.owners."""
from types import SimpleNamespace

from gateway.whatsapp_owners import is_owner, owner_gate, parse_owners

OWNERS = ["+27820000001"]


def _identity(transport="default", runtime="default"):
    return SimpleNamespace(transport_profile=transport, runtime_profile=runtime)


def _gate(owners=OWNERS, platform="whatsapp", user_id="27820000009@s.whatsapp.net", identity="same"):
    identity = _identity() if identity == "same" else identity
    return owner_gate(owners, platform=platform, user_id=user_id, identity=identity, primary_profile="default")


def test_parse_owners_keeps_non_empty_values_only():
    assert parse_owners(["+27820000001", " ", 27820000002, None]) == ["+27820000001", "27820000002"]
    assert parse_owners("27820000001") == []
    assert parse_owners(None) == []


def test_owner_matches_across_plus_jid_and_device_forms():
    assert is_owner(OWNERS, "27820000001@s.whatsapp.net")
    assert is_owner(OWNERS, "27820000001:7@s.whatsapp.net")
    assert not is_owner(OWNERS, "27820000009@s.whatsapp.net")
    assert not is_owner(OWNERS, None)


def test_gate_refuses_an_unrouted_non_owner():
    assert _gate() is False


def test_gate_has_no_opinion_when_it_does_not_apply():
    assert _gate(owners=[]) is None
    assert _gate(platform="telegram") is None
    assert _gate(identity=None) is None
    assert _gate(user_id="27820000001@s.whatsapp.net") is None
    assert _gate(identity=_identity(runtime="sentio-sales")) is None  # a route re-homed it
    assert _gate(identity=_identity(transport="team_b", runtime="team_b")) is None  # a secondary's own bot
```

- [x] **Step 2: Extend the probe file with the admission tests** (append to `tests/gateway/test_whatsapp_shared_number_routing.py`)

```python
def _authorized(rig, jid):
    source = rig.shared.build_source(chat_id=jid, chat_type="dm", user_id=jid)
    resolve_identity(source, runner=rig.runner)
    return rig.runner._is_user_authorized(source)


def test_unrouted_number_is_refused_when_owners_are_set(rig, monkeypatch):
    # Everyone is admitted by the allowlist layer, so only the owner gate decides.
    monkeypatch.setenv("GATEWAY_ALLOW_ALL_USERS", "true")
    rig.runner.config.owners = [OWNER]
    assert _authorized(rig, f"{OWNER}@s.whatsapp.net") is True
    assert _authorized(rig, f"{SALES}@s.whatsapp.net") is True
    assert _authorized(rig, "27820000009@s.whatsapp.net") is False


def test_without_owners_nothing_changes(rig, monkeypatch):
    monkeypatch.setenv("GATEWAY_ALLOW_ALL_USERS", "true")
    assert _authorized(rig, "27820000009@s.whatsapp.net") is True


def test_owners_reach_the_runtime_config(tmp_path, monkeypatch):
    from gateway.config import load_gateway_config

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text("gateway:\n  owners:\n    - '+27820000001'\n", encoding="utf-8")
    assert load_gateway_config().owners == ["+27820000001"]
```

If `_is_user_authorized` raises `AttributeError` on the bare runner, add the missing attribute in the `rig` fixture (mirror `tests/gateway/test_buzz_authz.py::_make_runner`); do not change production code for it.

- [x] **Step 3: Run to confirm they fail**

Run: `scripts/run_tests.sh tests/gateway/test_whatsapp_owners.py tests/gateway/test_whatsapp_shared_number_routing.py -v`
Expected: `test_whatsapp_owners.py` fails at import (`ModuleNotFoundError: gateway.whatsapp_owners`); in the probe file `test_unrouted_number_is_refused_when_owners_are_set` and `test_owners_reach_the_runtime_config` FAIL (the first asserts `False`, gets `True`; the second has no `owners` attribute).

- [x] **Step 4: Implement the module** `gateway/whatsapp_owners.py`

```python
"""Owner gate for a WhatsApp number shared by several people.

The shared bot belongs to the default (launch) profile, which has host-level access. A sender that is
admitted but matches no ``profile_routes`` entry runs there, so with ``gateway.owners`` set an unrouted
sender must be an owner. Routed senders and a secondary profile's own bot are not this gate's business.
"""

from __future__ import annotations

from typing import Any, Optional

_WHATSAPP_PLATFORMS = frozenset({"whatsapp", "whatsapp_cloud"})


def parse_owners(raw: Any) -> list[str]:
    """Owner numbers from ``gateway.owners``; anything that is not a list is no owners."""
    if not isinstance(raw, list):
        return []
    return [text for value in raw if value is not None and (text := str(value).strip())]


def _aliases(identifier: str) -> set[str]:
    from gateway.whatsapp_identity import expand_whatsapp_aliases

    return expand_whatsapp_aliases(str(identifier))


def is_owner(owners: list[str], user_id: Optional[str]) -> bool:
    """True when *user_id* is one of *owners*, whichever of number/JID/LID either side is spelled in."""
    sender = _aliases(user_id) if user_id else set()
    return bool(sender) and any(sender & _aliases(owner) for owner in owners)


def owner_gate(
    owners: list[str], *, platform: str, user_id: Optional[str], identity: Any, primary_profile: str,
) -> Optional[bool]:
    """``False`` = refuse the sender. ``None`` = no opinion, normal authorization continues.

    Applies only to a WhatsApp event that arrived on the primary profile's bot and stayed on the primary
    profile. A restored source has no identity and was admitted when it first arrived."""
    if not owners or platform not in _WHATSAPP_PLATFORMS or identity is None:
        return None
    if identity.transport_profile != primary_profile or identity.runtime_profile != primary_profile:
        return None
    return None if is_owner(owners, user_id) else False
```

- [x] **Step 5: Wire the config key**

In `gateway/config.py`, after the `profile_routes` field (line 652):

```python
    profile_routes: list = field(default_factory=list)  # gateway/profile_routing.py
    owners: list = field(default_factory=list)  # gateway/whatsapp_owners.py
```

In `to_dict`, after the `profile_routes` list entry (the `for r in self.profile_routes` / `],` lines):

```python
                for r in self.profile_routes
            ],
            "owners": list(self.owners),
```

In `from_dict`, next to the existing lazy import and the `profile_routes=` argument:

```python
        from gateway.profile_routing import parse_profile_routes
        from gateway.whatsapp_owners import parse_owners
```
```python
            profile_routes=parse_profile_routes(data.get("profile_routes") or []),
            owners=parse_owners(data.get("owners")),
```

In `gateway/config_loader.py`, after the `profile_routes` row (line 93):

```python
    ("owners", "owners", "none", lambda v: isinstance(v, list), None),
```

In `hermes_cli/config_defaults.py`, after `"profile_routes": [],` (line 2108):

```python
        # WhatsApp numbers that may run on the default profile of a shared number
        # (gateway/whatsapp_owners.py). Set: an admitted sender with no profile route is refused unless
        # listed here. Empty = no gate (upstream behaviour).
        "owners": [],
```

- [x] **Step 6: Call the gate from `_principal_authorized`**

In `gateway/authz_mixin.py`, between the `trusted_inbound` return and `adapter_profile = ...` (lines 653-658):

```python
        # Webhook events are HMAC-verified; a ``trusted_inbound`` platform's events come from the
        # service its adapter authenticated to (no human sender to allowlist).
        if source.platform == Platform.WEBHOOK or getattr(_registry_entry(source.platform), "trusted_inbound", False) is True:
            return True

        from gateway.session_identity import identity_of
        from gateway.whatsapp_owners import owner_gate
        # Before every allow-all/allowlist grant: a shared number's unrouted sender must not reach the
        # host-level primary profile just because the allowlist layer admitted them.
        if owner_gate(
            getattr(getattr(self, "config", None), "owners", None),
            platform=getattr(source.platform, "value", source.platform),
            user_id=source.user_id, identity=identity_of(source),
            primary_profile=getattr(self, "_primary_profile_name", None) or "default",
        ) is False:
            return False

        adapter_profile = self._adapter_profile_for_source(source)
```

- [x] **Step 7: Run to confirm everything passes**

Run: `scripts/run_tests.sh tests/gateway/test_whatsapp_owners.py tests/gateway/test_whatsapp_shared_number_routing.py tests/gateway/test_multiplex_transport_matrix.py -v`
Expected: all PASS (the matrix file proves existing routing is untouched).

- [x] **Step 8: Commit**

```bash
git add gateway/whatsapp_owners.py gateway/config.py gateway/config_loader.py gateway/authz_mixin.py hermes_cli/config_defaults.py tests/gateway/test_whatsapp_owners.py tests/gateway/test_whatsapp_shared_number_routing.py
git commit -m "feat: gateway.owners gate keeps unrouted WhatsApp senders off the default profile

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `whatsapp_users` module (add, list, remove)

**Files:**
- Create: `hermes_cli/whatsapp_users.py`
- Test: `tests/hermes_cli/test_whatsapp_users.py`

**Interfaces:**
- Consumes: `hermes_cli.profiles.create_profile(name, no_alias=...)`, `delete_profile(name, yes=True)`, `profile_exists`, `get_profile_dir`, `normalize_profile_name`, `validate_profile_name`, `get_active_profile_name`; `hermes_cli.config.read_user_config_raw(path)`, `atomic_config_write(path, data)`, `get_env_value`, `save_env_value`; `gateway.whatsapp_owners.parse_owners` / `is_owner`; `gateway.whatsapp_identity.normalize_whatsapp_identifier`.
- Produces:
  - `class WhatsAppUsersError(ValueError)`
  - `@dataclass(frozen=True) class WhatsAppUser: number: str; profile: str; status: str` where `status` is `"owner" | "live" | "missing"`
  - `add_user(name: str, number: str) -> WhatsAppUser`
  - `list_users() -> list[WhatsAppUser]`
  - `remove_user(target: str, *, delete_profile: bool = False) -> WhatsAppUser`

Design notes: routes use `chat_id: <bare number>` (not `user_id`): WhatsApp DM routes must match number/JID/LID, and only `chat_id` matching is alias-aware (Task 1 proves it). The allowlist is the existing `WHATSAPP_ALLOWED_USERS` in the default profile's `.env`. All of it runs against the default profile only.

- [x] **Step 1: Write the failing tests** `tests/hermes_cli/test_whatsapp_users.py`

```python
"""add/list/remove for people on the shared WhatsApp number (default profile owns routes + allowlist)."""
from pathlib import Path

import pytest

from hermes_cli import whatsapp_users as wu
from hermes_cli.config import atomic_config_write, get_env_value, read_user_config_raw

OWNER = "27820000001"
SALES = "27820000002"


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    atomic_config_write(home / "config.yaml", {"gateway": {"owners": [f"+{OWNER}"]}})
    return home


def _routes(home):
    return (read_user_config_raw(home / "config.yaml").get("gateway") or {}).get("profile_routes") or []


def test_add_user_creates_profile_route_and_allowlist_entry(home):
    user = wu.add_user("sentio-sales", f"+{SALES}")
    assert user == wu.WhatsAppUser(number=SALES, profile="sentio-sales", status="live")
    assert (home / "profiles" / "sentio-sales").is_dir()
    assert _routes(home) == [{"name": "whatsapp-sentio-sales", "platform": "whatsapp",
                              "profile": "sentio-sales", "chat_id": SALES}]
    assert SALES in (get_env_value("WHATSAPP_ALLOWED_USERS") or "").split(",")


def test_add_user_twice_changes_nothing(home):
    wu.add_user("sentio-sales", SALES)
    wu.add_user("sentio-sales", f"{SALES}@s.whatsapp.net")
    assert len(_routes(home)) == 1
    assert (get_env_value("WHATSAPP_ALLOWED_USERS") or "").split(",").count(SALES) == 1


def test_add_user_refuses_without_owners(home):
    atomic_config_write(home / "config.yaml", {"gateway": {"owners": []}})
    with pytest.raises(wu.WhatsAppUsersError, match="owners"):
        wu.add_user("sentio-sales", SALES)
    assert not (home / "profiles" / "sentio-sales").exists()


@pytest.mark.parametrize("number", ["", "abc", "123@g.us", "status@broadcast", "12"])
def test_add_user_refuses_bad_numbers(home, number):
    with pytest.raises(wu.WhatsAppUsersError):
        wu.add_user("sentio-sales", number)


def test_add_user_refuses_an_owner_number_and_a_number_routed_elsewhere(home):
    with pytest.raises(wu.WhatsAppUsersError, match="owner"):
        wu.add_user("sentio-sales", OWNER)
    wu.add_user("sentio-sales", SALES)
    with pytest.raises(wu.WhatsAppUsersError, match="already"):
        wu.add_user("other", SALES)


def test_failure_after_the_profile_exists_rolls_everything_back(home, monkeypatch):
    def boom(number):
        raise RuntimeError("env write failed")

    monkeypatch.setattr(wu, "_allow_number", boom)
    with pytest.raises(RuntimeError):
        wu.add_user("sentio-sales", SALES)
    assert not (home / "profiles" / "sentio-sales").exists()
    assert _routes(home) == []


def test_failure_never_deletes_a_profile_that_already_existed(home, monkeypatch):
    from hermes_cli.profiles import create_profile

    create_profile("sentio-sales", no_alias=True)
    monkeypatch.setattr(wu, "_allow_number", lambda number: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(RuntimeError):
        wu.add_user("sentio-sales", SALES)
    assert (home / "profiles" / "sentio-sales").is_dir()
    assert _routes(home) == []


def test_remove_user_drops_route_and_allowlist_but_keeps_the_profile(home):
    wu.add_user("sentio-sales", SALES)
    removed = wu.remove_user("sentio-sales")
    assert removed.number == SALES
    assert _routes(home) == []
    assert SALES not in (get_env_value("WHATSAPP_ALLOWED_USERS") or "").split(",")
    assert (home / "profiles" / "sentio-sales").is_dir()


def test_remove_user_by_number_and_with_delete_profile(home):
    wu.add_user("sentio-sales", SALES)
    wu.remove_user(f"+{SALES}", delete_profile=True)
    assert not (home / "profiles" / "sentio-sales").exists()


def test_list_users_reports_owners_live_and_missing(home):
    import shutil

    wu.add_user("sentio-sales", SALES)
    shutil.rmtree(home / "profiles" / "sentio-sales")
    users = wu.list_users()
    assert wu.WhatsAppUser(OWNER, "default", "owner") in users
    assert wu.WhatsAppUser(SALES, "sentio-sales", "missing") in users
```

- [x] **Step 2: Run to confirm they fail**

Run: `scripts/run_tests.sh tests/hermes_cli/test_whatsapp_users.py -v`
Expected: collection error `ModuleNotFoundError: hermes_cli.whatsapp_users` (every test red).

- [x] **Step 3: Implement** `hermes_cli/whatsapp_users.py`

```python
"""People on the shared WhatsApp number: one profile each, routed by number (private fork).

The default profile owns the routes (``gateway.profile_routes``), the owner list (``gateway.owners``) and
the shared bot's allowlist (``WHATSAPP_ALLOWED_USERS``). CLI and dashboard both call this module, as they
share ``create_profile``. See docs/private-fork/2026-11-02-shared-whatsapp-number-and-granted-user-experience.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hermes_cli.config import (
    atomic_config_write, get_env_value, read_user_config_raw, save_env_value,
)

ALLOWLIST_ENV = "WHATSAPP_ALLOWED_USERS"
_NON_USER_SUFFIXES = ("@g.us", "@broadcast", "@newsletter")


class WhatsAppUsersError(ValueError):
    """A request this module refuses, with a message fit to show the operator."""


@dataclass(frozen=True)
class WhatsAppUser:
    number: str
    profile: str
    status: str  # "owner" | "live" | "missing"


def _config_path() -> Path:
    from hermes_cli.profiles import get_profile_dir

    return get_profile_dir("default") / "config.yaml"


def _require_default_profile() -> None:
    from hermes_cli.profiles import get_active_profile_name

    if get_active_profile_name() != "default":
        raise WhatsAppUsersError("Run this against the default profile (omit -p): it owns the shared number.")


def _normalize_number(raw: str) -> str:
    from gateway.whatsapp_identity import normalize_whatsapp_identifier

    text = str(raw or "").strip()
    if text.lower().endswith(_NON_USER_SUFFIXES):
        raise WhatsAppUsersError(f"{text!r} is a group or broadcast, not a person's number.")
    number = normalize_whatsapp_identifier(text)
    if not number.isascii() or not number.isdigit() or not 7 <= len(number) <= 15:
        raise WhatsAppUsersError(f"{raw!r} is not a phone number (digits with country code, e.g. 27820000002).")
    return number


def _same_person(left: str, right: str) -> bool:
    from gateway.whatsapp_identity import expand_whatsapp_aliases

    return bool(expand_whatsapp_aliases(left) & expand_whatsapp_aliases(right))


def _read() -> tuple[dict[str, Any], dict[str, Any]]:
    data = read_user_config_raw(_config_path())
    gateway = data.setdefault("gateway", {})
    if not isinstance(gateway, dict):
        raise WhatsAppUsersError("config.yaml 'gateway' is not a mapping; fix it first.")
    return data, gateway


def _whatsapp_routes(gateway: dict[str, Any]) -> list[dict[str, Any]]:
    return [r for r in (gateway.get("profile_routes") or [])
            if isinstance(r, dict) and r.get("platform") == "whatsapp" and r.get("chat_id") is not None]


def _owners(gateway: dict[str, Any]) -> list[str]:
    from gateway.whatsapp_owners import parse_owners

    return parse_owners(gateway.get("owners"))


def _write_routes(data: dict[str, Any], gateway: dict[str, Any], routes: list[dict[str, Any]]) -> None:
    gateway["profile_routes"] = routes
    atomic_config_write(_config_path(), data)


def _allowed() -> list[str]:
    return [item.strip() for item in (get_env_value(ALLOWLIST_ENV) or "").split(",") if item.strip()]


def _allow_number(number: str) -> None:
    allowed = _allowed()
    if not any(_same_person(number, existing) for existing in allowed):
        save_env_value(ALLOWLIST_ENV, ",".join([*allowed, number]))


def _disallow_number(number: str) -> None:
    save_env_value(ALLOWLIST_ENV, ",".join(a for a in _allowed() if not _same_person(number, a)))


def _status(profile: str) -> str:
    from hermes_cli.profiles import profile_exists

    return "live" if profile_exists(profile) else "missing"


def add_user(name: str, number: str) -> WhatsAppUser:
    """Create *name*'s profile, route *number* to it and admit it. Idempotent; rolls back on failure."""
    from hermes_cli.profiles import (
        create_profile, delete_profile, normalize_profile_name, profile_exists, validate_profile_name,
    )
    from gateway.whatsapp_owners import is_owner

    _require_default_profile()
    canon = normalize_profile_name(name)
    validate_profile_name(canon)
    if canon == "default":
        raise WhatsAppUsersError("The default profile is the owner's; give the person their own profile name.")
    digits = _normalize_number(number)
    data, gateway = _read()
    owners = _owners(gateway)
    if not owners:
        raise WhatsAppUsersError("Set gateway.owners (your own number) in config.yaml first: "
                                 "without it an unrouted sender would run on the default profile.")
    if is_owner(owners, digits):
        raise WhatsAppUsersError(f"{digits} is an owner number; owners run on the default profile.")
    routes = _whatsapp_routes(gateway)
    for route in routes:
        if _same_person(str(route["chat_id"]), digits):
            if route.get("profile") == canon:
                _allow_number(digits)
                return WhatsAppUser(digits, canon, _status(canon))
            raise WhatsAppUsersError(f"{digits} is already routed to profile {route.get('profile')!r}.")

    created = route_written = False
    try:
        if not profile_exists(canon):
            create_profile(canon, no_alias=True)
            created = True
        all_routes = list(gateway.get("profile_routes") or [])
        all_routes.append({"name": f"whatsapp-{canon}", "platform": "whatsapp",
                           "profile": canon, "chat_id": digits})
        _write_routes(data, gateway, all_routes)
        route_written = True
        _allow_number(digits)
    except BaseException:
        if route_written:
            data, gateway = _read()
            _write_routes(data, gateway, [r for r in (gateway.get("profile_routes") or [])
                                          if not (isinstance(r, dict) and r.get("name") == f"whatsapp-{canon}")])
        if created:
            delete_profile(canon, yes=True)
        raise
    return WhatsAppUser(digits, canon, "live")


def list_users() -> list[WhatsAppUser]:
    _, gateway = _read()
    users = [WhatsAppUser(_normalize_number(owner), "default", "owner") for owner in _owners(gateway)]
    users += [WhatsAppUser(_normalize_number(str(r["chat_id"])), str(r.get("profile") or ""),
                           _status(str(r.get("profile") or "")))
              for r in _whatsapp_routes(gateway)]
    return users


def remove_user(target: str, *, delete_profile: bool = False) -> WhatsAppUser:
    """Drop the route and allowlist entry of a profile name or a number; keep the profile unless asked."""
    from hermes_cli.profiles import delete_profile as _delete_profile, normalize_profile_name

    _require_default_profile()
    data, gateway = _read()
    text = str(target or "").strip()
    # A target that parses as a phone number is a number; anything else is a profile name (so `team2` works).
    try:
        number_target, wanted_profile = _normalize_number(text), None
    except WhatsAppUsersError:
        number_target, wanted_profile = None, normalize_profile_name(text)
    match = next((r for r in _whatsapp_routes(gateway)
                  if (wanted_profile and r.get("profile") == wanted_profile)
                  or (number_target and _same_person(str(r["chat_id"]), number_target))), None)
    if match is None:
        raise WhatsAppUsersError(f"No WhatsApp route for {target!r}.")
    _write_routes(data, gateway, [r for r in (gateway.get("profile_routes") or []) if r is not match])
    number = _normalize_number(str(match["chat_id"]))
    _disallow_number(number)
    profile = str(match.get("profile") or "")
    if delete_profile:
        _delete_profile(profile, yes=True)
    return WhatsAppUser(number, profile, "missing" if delete_profile else _status(profile))
```

- [x] **Step 4: Run to confirm they pass**

Run: `scripts/run_tests.sh tests/hermes_cli/test_whatsapp_users.py -v`
Expected: all PASS. If `create_profile` in the temp home needs more fixture setup, copy the `profile_env` pattern from `tests/hermes_cli/test_profiles.py`.

- [x] **Step 5: Commit**

```bash
git add hermes_cli/whatsapp_users.py tests/hermes_cli/test_whatsapp_users.py
git commit -m "feat: add/list/remove people on the shared WhatsApp number

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `hermes whatsapp add-user | list-users | remove-user`

**Files:**
- Create: `hermes_cli/whatsapp_users_cli.py`
- Modify: `hermes_cli/subcommands/whatsapp.py:8-13`
- Modify: `hermes_cli/main_platform_setup.py:133-137` (`cmd_whatsapp`)
- Test: `tests/hermes_cli/test_whatsapp_users_cli.py`

**Interfaces:**
- Consumes: Task 3's `add_user`, `list_users`, `remove_user`, `WhatsAppUsersError`.
- Produces: `run(args) -> int` in `hermes_cli/whatsapp_users_cli.py` (exit code); argparse attribute `whatsapp_action` in `{"add-user","list-users","remove-user"}` with `name`/`number`, `target`/`delete_profile`.

- [x] **Step 1: Write the failing tests** `tests/hermes_cli/test_whatsapp_users_cli.py`

```python
"""``hermes whatsapp add-user|list-users|remove-user`` parse and print."""
import argparse
from pathlib import Path

import pytest

from hermes_cli.config import atomic_config_write
from hermes_cli.subcommands.whatsapp import build_whatsapp_parser
from hermes_cli.whatsapp_users_cli import run


def _parse(*argv):
    root = argparse.ArgumentParser()
    build_whatsapp_parser(root.add_subparsers(dest="command"), cmd_whatsapp=lambda args: None)
    return root.parse_args(["whatsapp", *argv])


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    atomic_config_write(home / "config.yaml", {"gateway": {"owners": ["+27820000001"]}})
    return home


def test_parser_reads_the_three_actions():
    add = _parse("add-user", "sentio-sales", "+27820000002")
    assert (add.whatsapp_action, add.name, add.number) == ("add-user", "sentio-sales", "+27820000002")
    assert _parse("list-users").whatsapp_action == "list-users"
    rm = _parse("remove-user", "sentio-sales", "--delete-profile")
    assert (rm.whatsapp_action, rm.target, rm.delete_profile) == ("remove-user", "sentio-sales", True)
    assert _parse().whatsapp_action is None  # bare `hermes whatsapp` stays the setup wizard


def test_add_then_list_then_remove_prints_the_state(home, capsys):
    assert run(_parse("add-user", "sentio-sales", "27820000002")) == 0
    out = capsys.readouterr().out
    assert "sentio-sales" in out and "27820000002" in out and "restart" in out.lower()
    assert "model key" in out.lower()

    assert run(_parse("list-users")) == 0
    listing = capsys.readouterr().out
    assert "27820000001" in listing and "owner" in listing and "live" in listing

    assert run(_parse("remove-user", "sentio-sales")) == 0
    assert "sentio-sales" in capsys.readouterr().out


def test_refusals_exit_non_zero_with_the_reason(home, capsys):
    assert run(_parse("add-user", "sentio-sales", "not-a-number")) == 1
    assert "phone number" in capsys.readouterr().err
```

- [x] **Step 2: Run to confirm they fail**

Run: `scripts/run_tests.sh tests/hermes_cli/test_whatsapp_users_cli.py -v`
Expected: FAIL (`ModuleNotFoundError: hermes_cli.whatsapp_users_cli`).

- [x] **Step 3: Implement** `hermes_cli/whatsapp_users_cli.py`

```python
"""``hermes whatsapp add-user | list-users | remove-user`` (logic: hermes_cli/whatsapp_users.py)."""

from __future__ import annotations

import sys

from hermes_cli import whatsapp_users as wu


def _add(args) -> None:
    user = wu.add_user(args.name, args.number)
    print(f"Added {user.profile} for +{user.number}.")
    print("  - The profile has no model key yet: give it one before they message (shared company model keys).")
    print("  - Routes load when the gateway starts: run `hermes gateway restart` to apply.")


def _list(args) -> None:
    users = wu.list_users()
    if not users:
        print("No people on the shared WhatsApp number yet.")
    for user in users:
        print(f"+{user.number:<16} {user.profile:<24} {user.status}")


def _remove(args) -> None:
    user = wu.remove_user(args.target, delete_profile=args.delete_profile)
    kept = "deleted" if args.delete_profile else "kept"
    print(f"Removed +{user.number} ({user.profile}); profile {kept}. Run `hermes gateway restart` to apply.")


_ACTIONS = {"add-user": _add, "list-users": _list, "remove-user": _remove}


def run(args) -> int:
    try:
        _ACTIONS[args.whatsapp_action](args)
    except wu.WhatsAppUsersError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0
```

- [x] **Step 4: Add the sub-subcommands** in `hermes_cli/subcommands/whatsapp.py` (replace `build_whatsapp_parser`)

```python
def build_whatsapp_parser(subparsers, *, cmd_whatsapp: Callable) -> None:
    """Attach the ``whatsapp`` subcommand to ``subparsers``."""
    whatsapp_parser = subparsers.add_parser(
        "whatsapp", help="Set up WhatsApp integration",
        description="Configure WhatsApp and pair via QR code")
    whatsapp_parser.set_defaults(func=cmd_whatsapp, whatsapp_action=None)
    actions = whatsapp_parser.add_subparsers(dest="whatsapp_action", metavar="ACTION")
    add = actions.add_parser(
        "add-user", help="Give a person their own profile on the shared WhatsApp number",
        description="Create the profile, route the number to it and admit it (default profile only).")
    add.add_argument("name", help="Profile name for the person, e.g. sentio-sales")
    add.add_argument("number", help="Their WhatsApp number with country code, e.g. +27820000002")
    actions.add_parser("list-users", help="List owners and the people routed on the shared number")
    remove = actions.add_parser(
        "remove-user", help="Stop routing a person (keeps their profile)",
        description="Remove the route and the allowlist entry; the profile and its data stay unless --delete-profile.")
    remove.add_argument("target", help="Profile name or WhatsApp number")
    remove.add_argument("--delete-profile", action="store_true", help="Also delete the profile and its data")
```

- [x] **Step 5: Dispatch in `cmd_whatsapp`** (`hermes_cli/main_platform_setup.py`)

```python
def cmd_whatsapp(args):
    """Set up WhatsApp: choose mode, configure, install bridge, pair via QR."""
    if getattr(args, "whatsapp_action", None):
        from hermes_cli.whatsapp_users_cli import run
        raise SystemExit(run(args))
    from hermes_cli.main import _require_tty, get_hermes_home
    _require_tty("whatsapp")
```

(The dispatch sits before `_require_tty` so the user commands work in a non-interactive shell.)

- [x] **Step 6: Run to confirm they pass**

Run: `scripts/run_tests.sh tests/hermes_cli/test_whatsapp_users_cli.py tests/hermes_cli/test_whatsapp_users.py -v`
Expected: all PASS.

- [x] **Step 7: Commit**

```bash
git add hermes_cli/whatsapp_users_cli.py hermes_cli/subcommands/whatsapp.py hermes_cli/main_platform_setup.py tests/hermes_cli/test_whatsapp_users_cli.py
git commit -m "feat: hermes whatsapp add-user/list-users/remove-user

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Admin-only dashboard endpoints

**Files:**
- Create: `hermes_cli/web_routers/whatsapp_users.py`
- Modify: `hermes_cli/web_server.py:782` (import) and `:812` (include)
- Modify: `tests/hermes_cli/test_dashboard_rest_access.py` (extend `PATHS`)
- Test: `tests/hermes_cli/test_whatsapp_users_api.py`

**Interfaces:**
- Consumes: Task 3 module.
- Produces: `GET /api/whatsapp/users`, `POST /api/whatsapp/users` (`{"name","number"}`), `DELETE /api/whatsapp/users/{target}?delete_profile=false`. Not under any listed prefix in `access_http.py`, so they are admin-only by default-deny; the test asserts it.

- [x] **Step 1: Write the failing tests** `tests/hermes_cli/test_whatsapp_users_api.py`

```python
"""Dashboard endpoints for the shared WhatsApp number: thin over hermes_cli.whatsapp_users."""
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hermes_cli.config import atomic_config_write
from hermes_cli.web_routers.whatsapp_users import router


@pytest.fixture
def client(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    atomic_config_write(home / "config.yaml", {"gateway": {"owners": ["+27820000001"]}})
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_add_list_remove_round_trip(client):
    created = client.post("/api/whatsapp/users", json={"name": "sentio-sales", "number": "+27820000002"})
    assert created.status_code == 200
    assert created.json() == {"number": "27820000002", "profile": "sentio-sales", "status": "live"}
    listing = client.get("/api/whatsapp/users").json()["users"]
    assert {"number": "27820000001", "profile": "default", "status": "owner"} in listing
    assert {"number": "27820000002", "profile": "sentio-sales", "status": "live"} in listing
    assert client.delete("/api/whatsapp/users/sentio-sales").status_code == 200
    assert client.get("/api/whatsapp/users").json()["users"] == [
        {"number": "27820000001", "profile": "default", "status": "owner"}]


def test_refusals_are_400_with_the_reason(client):
    bad = client.post("/api/whatsapp/users", json={"name": "sentio-sales", "number": "nope"})
    assert bad.status_code == 400
    assert "phone number" in bad.json()["detail"]
    assert client.delete("/api/whatsapp/users/ghost").status_code == 404
```

And in `tests/hermes_cli/test_dashboard_rest_access.py`, add to `PATHS` (so the existing admin/non-admin matrix covers them):

```python
    ("GET", "/api/whatsapp/users"), ("POST", "/api/whatsapp/users"), ("DELETE", "/api/whatsapp/users/alpha"),
```
Check how that file's non-admin test lists admin-only paths and add these three to the same expected-403 set.

- [x] **Step 2: Run to confirm they fail**

Run: `scripts/run_tests.sh tests/hermes_cli/test_whatsapp_users_api.py -v`
Expected: FAIL (`ModuleNotFoundError: hermes_cli.web_routers.whatsapp_users`).

- [x] **Step 3: Implement** `hermes_cli/web_routers/whatsapp_users.py`

```python
"""Admin-only REST over hermes_cli.whatsapp_users (the CLI's twin). Unlisted in access_http => admin only."""

from __future__ import annotations

import asyncio
from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from hermes_cli import whatsapp_users as wu

router = APIRouter()


class WhatsAppUserAdd(BaseModel):
    name: str
    number: str


@router.get("/api/whatsapp/users")
async def list_whatsapp_users():
    users = await asyncio.to_thread(wu.list_users)
    return {"users": [asdict(user) for user in users]}


@router.post("/api/whatsapp/users")
async def add_whatsapp_user(body: WhatsAppUserAdd):
    try:
        return asdict(await asyncio.to_thread(wu.add_user, body.name, body.number))
    except wu.WhatsAppUsersError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/api/whatsapp/users/{target}")
async def remove_whatsapp_user(target: str, delete_profile: bool = False):
    try:
        return asdict(await asyncio.to_thread(wu.remove_user, target, delete_profile=delete_profile))
    except wu.WhatsAppUsersError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
```

- [x] **Step 4: Register the router** in `hermes_cli/web_server.py`: add `whatsapp_users as _whatsapp_users_routes,` after `delegation as _delegation_routes,` in the import list (line ~785) and `app.include_router(_whatsapp_users_routes.router)` after `app.include_router(_skills_routes.router)` (line ~812).

- [x] **Step 5: Run to confirm they pass**

Run: `scripts/run_tests.sh tests/hermes_cli/test_whatsapp_users_api.py tests/hermes_cli/test_dashboard_rest_access.py -v`
Expected: all PASS, including the default-deny matrix for the three new paths.

- [x] **Step 6: Commit**

```bash
git add hermes_cli/web_routers/whatsapp_users.py hermes_cli/web_server.py tests/hermes_cli/test_whatsapp_users_api.py tests/hermes_cli/test_dashboard_rest_access.py
git commit -m "feat: admin-only dashboard endpoints for people on the shared WhatsApp number

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: WhatsApp docs and fork decision

**Files:**
- Modify: `website/docs/user-guide/messaging/whatsapp.md` (new section after "Multiple profiles")
- Modify: `docs/private-fork/FORK_DECISIONS.md` (new entry)

**Interfaces:** none (docs).

- [x] **Step 1: Add the user guide section** after the "Multiple profiles" section in `whatsapp.md`

````markdown
## One number, many people

A company can pair one WhatsApp number and give each person their own profile. You keep the default
profile; everyone else is routed to a sandboxed profile of their own.

```yaml
# default profile's config.yaml
gateway:
  owners: ["+27820000001"]        # your number(s): the only unrouted senders that may reach the default profile
```

```bash
hermes whatsapp add-user sentio-sales +27820000002   # profile + route + allowlist entry, in one step
hermes whatsapp list-users
hermes whatsapp remove-user sentio-sales             # keeps the profile; --delete-profile removes it too
hermes gateway restart                               # routes load at gateway start
```

With `gateway.owners` set, a sender who is admitted but has no route is refused instead of running on the
default profile. A new profile has no model key yet; grant one before the person messages.
`add-user` runs against the default profile only and refuses to run until `gateway.owners` is set.
````

- [x] **Step 2: Add the fork decision** to `docs/private-fork/FORK_DECISIONS.md` (next free number after the last `D`/`O` entry)

```markdown
- **Dxx. A shared WhatsApp number gates unrouted senders on an explicit `gateway.owners` list.** The shared bot belongs to the
  default (launch) profile, which has host-level access; an admitted sender without a `profile_routes` entry would run there.
  `gateway/whatsapp_owners.py` refuses such a sender unless listed. Routes for WhatsApp DMs use `chat_id` (number/JID/LID
  alias-aware), not `user_id` (exact match). Upstream merges: keep the `owner_gate` call at the top of
  `authz_mixin.py::_principal_authorized`.
```

- [x] **Step 3: Commit**

```bash
git add website/docs/user-guide/messaging/whatsapp.md docs/private-fork/FORK_DECISIONS.md
git commit -m "docs: one WhatsApp number, many people

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Workstream 2: granted-user experience (desktop)

### Task 7: Reproduce the non-admin desktop session (decides what Task 8 fixes)

**Files:**
- Create: `tests/hermes_cli/test_granted_user_active_profile.py` (written in Task 8; this task is a manual diagnosis)

**Interfaces:** none.

The spec's unconfirmed question: which profile did the non-admin session actually run under? Answer it before writing code.

- [ ] **Step 1: Reproduce** ? partial: existing dashboard session diagnosed; native desktop, gateway logs and successful model answer pending.

On the company gateway (or a local gateway with an `access:` policy configured), sign in to the desktop app with the OIDC account granted only `sentio-sales`. In the app, note: the active-profile pill, the agent's answer to "who are you?", and the file browser. In the gateway log (`hermes logs gateway`), search for `rest access denied:` lines and for which profile the new session was created under (`agent:<profile>:` in the session key).

- [x] **Step 2: Record the finding**

Under "Risks" in the spec, add one line: "Reproduced <date>: the session ran under `<profile>`; the identity text came from `<SOUL.md of that profile | DEFAULT_AGENT_IDENTITY>`."
- If the session ran under `default`: Task 8 (active profile) is the root cause of the label and probably the identity symptom; Task 11 still applies to profiles with no Sentio `SOUL.md`.
- If it ran under `sentio-sales`: Task 8 fixes only the label; the identity symptom is Task 11 alone.
Either way all of Tasks 8-11 stay in scope. Commit the spec edit:

```bash
git add docs/private-fork/2026-11-02-shared-whatsapp-number-and-granted-user-experience.md
git commit -m "docs: record which profile the non-admin desktop session ran under

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: `GET /api/profiles/active` follows the grant

**Files:**
- Modify: `hermes_cli/web_routers/profiles.py:890-908`
- Test: `tests/hermes_cli/test_granted_user_active_profile.py`

**Interfaces:**
- Consumes: `request.state.access_allowed_profiles` (`None` = unrestricted, else a `frozenset[str]` of granted profile names; stamped by `access_http.enforce`).
- Produces: for a restricted caller, `{"active": <p>, "current": <p>}` where `<p>` is the `?profile=` value when granted, else the alphabetically first granted profile; admins and ungated callers unchanged.

- [x] **Step 1: Write the failing test** `tests/hermes_cli/test_granted_user_active_profile.py`

```python
"""A grant-scoped caller is told its own profile as the active one, never the machine's ``default``."""
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from hermes_cli.web_routers.profiles import router


def _client(allowed):
    app = FastAPI()

    @app.middleware("http")
    async def stamp(request: Request, call_next):
        request.state.access_allowed_profiles = allowed
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


def test_a_granted_caller_gets_its_profile():
    body = _client(frozenset({"sentio-sales"})).get("/api/profiles/active").json()
    assert body == {"active": "sentio-sales", "current": "sentio-sales"}


def test_the_selector_wins_when_it_is_granted_otherwise_the_first_grant():
    client = _client(frozenset({"beta", "alpha"}))
    assert client.get("/api/profiles/active?profile=beta").json()["current"] == "beta"
    assert client.get("/api/profiles/active?profile=gamma").json()["current"] == "alpha"
    assert client.get("/api/profiles/active").json()["current"] == "alpha"


def test_an_unrestricted_caller_still_gets_the_machine_profile(monkeypatch):
    monkeypatch.setattr("hermes_cli.profiles.get_active_profile", lambda: "work")
    monkeypatch.setattr("hermes_cli.profiles.get_active_profile_name", lambda: "default")
    assert _client(None).get("/api/profiles/active").json() == {"active": "work", "current": "default"}
```

- [x] **Step 2: Run to confirm they fail**

Run: `scripts/run_tests.sh tests/hermes_cli/test_granted_user_active_profile.py -v`
Expected: the first two FAIL (`current` is the server's profile, not `sentio-sales`); the third passes.

- [x] **Step 3: Implement** — in `hermes_cli/web_routers/profiles.py`

```python
@router.get("/api/profiles/active")
async def get_active_profile_endpoint(request: Request, profile: Optional[str] = None):
    """``active`` is the sticky default written by ``hermes profile use`` (what new CLI
    invocations pick up); ``current`` is the profile this running dashboard is scoped to.

    A grant-scoped caller (``access:`` policy) is told a profile it may use: the machine's own ``default``
    is not theirs, and the desktop adopts ``current`` as the profile it works in."""
    from hermes_cli import profiles as profiles_mod

    granted = getattr(request.state, "access_allowed_profiles", None)
    if granted:
        chosen = profile if profile in granted else sorted(granted)[0]
        return {"active": chosen, "current": chosen}

    def _run():
```
(the rest of the function body is unchanged). A caller with an empty grant set falls through to the existing behaviour; its other routes are already refused by `access_http`.

- [x] **Step 4: Run to confirm they pass**

Run: `scripts/run_tests.sh tests/hermes_cli/test_granted_user_active_profile.py tests/hermes_cli/test_dashboard_rest_access.py -v`
Expected: all PASS.

- [x] **Step 5: Commit**

```bash
git add hermes_cli/web_routers/profiles.py tests/hermes_cli/test_granted_user_active_profile.py
git commit -m "fix: a grant-scoped caller's active profile is its granted profile, not default

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Grant-scoped workspace files for `/api/files`

**Files:**
- Modify: `hermes_cli/web_server_files.py:285-299` (`_managed_files_policy`) and add `restricted_files_root`
- Modify: `hermes_cli/dashboard_auth/access_http.py:26-45` (classification)
- Test: `tests/hermes_cli/test_granted_user_files.py`

**Interfaces:**
- Consumes: `request.state.access_allowed_profiles`; `hermes_cli.profiles.get_profile_dir(name)`; existing `_ensure_managed_root`, `_resolve_managed_path`, `_path_is_under`.
- Produces: `restricted_files_root(request) -> Path | None` in `web_server_files.py` (the profile's `workspace/` directory for a restricted caller, `None` otherwise); `_managed_files_policy` returns a locked policy on that root for a restricted caller; `/api/files` is reachable by a caller granted the profile named in `?profile=` (explicit selector required).

Why this seam: every `/api/files*` handler resolves paths through `_resolve_managed_path`, which already enforces a locked root (`..` refused, resolved target must stay under the root, symlinks resolved first). Scoping the policy scopes all of them at once.

- [x] **Step 1: Write the failing tests** `tests/hermes_cli/test_granted_user_files.py`

```python
"""A user granted a profile browses, edits and uploads only inside that profile's workspace/ directory."""
import base64
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from hermes_cli.web_routers.files import router
from hermes_cli.web_server_files import restricted_files_root


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    for name in ("sentio-sales", "other"):
        (home / "profiles" / name / "workspace").mkdir(parents=True)
    (home / "profiles" / "sentio-sales" / "workspace" / "notes.txt").write_text("hello", encoding="utf-8")
    (home / "profiles" / "sentio-sales" / "config.yaml").write_text("secret: 1", encoding="utf-8")
    (home / "profiles" / "other" / "workspace" / "theirs.txt").write_text("nope", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    return SimpleNamespace(home=home, ws=home / "profiles" / "sentio-sales" / "workspace")


def _client(granted):
    app = FastAPI()

    @app.middleware("http")
    async def stamp(request: Request, call_next):
        request.state.access_allowed_profiles = granted
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


def _data_url(text):
    return "data:text/plain;base64," + base64.b64encode(text.encode()).decode()


def test_root_is_the_granted_profiles_workspace(env):
    request = SimpleNamespace(state=SimpleNamespace(access_allowed_profiles=frozenset({"sentio-sales"})),
                              query_params={"profile": "sentio-sales"})
    assert restricted_files_root(request) == env.ws.resolve()
    request.state.access_allowed_profiles = None
    assert restricted_files_root(request) is None


def test_list_and_read_inside_the_workspace(env):
    client = _client(frozenset({"sentio-sales"}))
    listing = client.get("/api/files?profile=sentio-sales").json()
    assert [e["name"] for e in listing["entries"]] == ["notes.txt"]
    assert listing["locked_root"] == str(env.ws.resolve())
    read = client.get("/api/files/read?profile=sentio-sales&path=notes.txt")
    assert read.status_code == 200


def test_upload_mkdir_and_delete_inside_the_workspace(env):
    client = _client(frozenset({"sentio-sales"}))
    up = client.post("/api/files/upload?profile=sentio-sales",
                     json={"path": "report.txt", "data_url": _data_url("hi"), "overwrite": False})
    assert up.status_code == 200 and (env.ws / "report.txt").read_text() == "hi"
    assert client.post("/api/files/mkdir?profile=sentio-sales", json={"path": "docs"}).status_code == 200
    gone = client.request("DELETE", "/api/files?profile=sentio-sales", json={"path": "report.txt"})
    assert gone.status_code == 200 and not (env.ws / "report.txt").exists()


@pytest.mark.parametrize("path", ["../config.yaml", "../../other/workspace/theirs.txt", "/etc/passwd"])
def test_nothing_outside_the_workspace_is_reachable(env, path):
    client = _client(frozenset({"sentio-sales"}))
    assert client.get("/api/files/read", params={"profile": "sentio-sales", "path": path}).status_code in (400, 403, 404)


def test_a_symlink_out_of_the_workspace_is_refused(env):
    (env.ws / "escape").symlink_to(env.home / "profiles" / "sentio-sales" / "config.yaml")
    client = _client(frozenset({"sentio-sales"}))
    assert client.get("/api/files/read", params={"profile": "sentio-sales", "path": "escape"}).status_code in (403, 404)
```
(`symlink_to` needs symlink rights; on Windows mark `test_a_symlink_out_of_the_workspace_is_refused` with `@pytest.mark.platforms("posix")`.)

Extend `tests/hermes_cli/test_dashboard_rest_access.py` (same file as Task 5): a user granted `alpha` gets 200 on `GET /api/files?profile=alpha`, 403 `profile_scope_mismatch` on `?profile=beta` and with no selector, and still 403 `admin_required` on `/api/git/status` and `/api/media`.

- [x] **Step 2: Run to confirm they fail**

Run: `scripts/run_tests.sh tests/hermes_cli/test_granted_user_files.py -v`
Expected: collection error (`cannot import name 'restricted_files_root'`).

- [x] **Step 3: Implement the root** in `hermes_cli/web_server_files.py`; replace `_managed_files_policy`

```python
def restricted_files_root(request: Optional[Request]) -> Path | None:
    """The ``workspace/`` directory of the profile a grant-scoped caller named, else None.

    ``access_http.enforce`` already refused a selector the caller was not granted, so the name here is
    one they may use. None for an admin, an ungated host and for callers with no request (agent-side use)."""
    granted = getattr(getattr(request, "state", None), "access_allowed_profiles", None)
    if not granted:
        return None
    from hermes_cli.profiles import get_profile_dir

    name = str(request.query_params.get("profile") or "").strip().lower()
    return _ensure_managed_root(get_profile_dir(name if name in granted else sorted(granted)[0]) / "workspace")


def _managed_files_policy(request: Optional[Request], *, create_root: bool = True) -> ManagedFilesPolicy:
    scoped = restricted_files_root(request)
    if scoped is not None:
        return ManagedFilesPolicy(default_path=scoped, locked_root=scoped, can_change_path=False)

    raw_forced_root = os.environ.get(_MANAGED_FILES_ROOT_ENV, "").strip()
```
(the remainder of the function is unchanged.)

- [x] **Step 4: Open `/api/files` to grant-scoped callers** in `hermes_cli/dashboard_auth/access_http.py`

Remove `"/api/files"` from `_ADMIN_PREFIXES`, and add it to the profile-scoped lists:

```python
_ADMIN_PREFIXES = (
    "/api/fs", "/api/media", "/api/git", "/api/gateway", "/api/hermes", "/api/ops",
```
```python
_PROFILE_PREFIXES = (
    ...
    "/api/messaging", "/api/dashboard", "/api/shared-metrics", "/api/audio", "/api/pairing", "/api/files")
# These read an absent / empty / ``current`` selector as "every profile": a grant-scoped caller must name one.
_EXPLICIT_PREFIXES = ("/api/sessions", "/api/cron/jobs", "/api/profiles/", "/api/files")
```
Update the module docstring comment above `_ADMIN_PREFIXES` so it no longer lists `/api/files` as host-level: "`/api/files` is scoped to the granted profile's workspace by `web_server_files.restricted_files_root`; `/api/fs` stays admin-only until Task 10".

- [ ] **Step 5: Run to confirm they pass**

- [x] Focused suites and reviewed fixes passed as recorded in the execution table.
- [ ] Broad hermes_cli `-k` command below is deferred to Task 12.

Run: `scripts/run_tests.sh tests/hermes_cli/test_granted_user_files.py tests/hermes_cli/test_dashboard_rest_access.py tests/hermes_cli/test_dashboard_document_access.py -v`
Expected: all PASS. Then run `scripts/run_tests.sh tests/hermes_cli/ -k "files or managed"` to confirm admin behaviour on `/api/files` is unchanged.

- [x] **Step 6: Commit**

```bash
git add hermes_cli/web_server_files.py hermes_cli/dashboard_auth/access_http.py tests/hermes_cli/test_granted_user_files.py tests/hermes_cli/test_dashboard_rest_access.py
git commit -m "feat: grant-scoped /api/files confined to the granted profile's workspace

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Grant-scoped `/api/fs` (what the desktop file browser calls)

**Files:**
- Modify: `hermes_cli/web_routers/files.py` (`fs_list` 770, `fs_read_text` 800, `fs_write_text` 843, `fs_read_data_url` 926, `fs_download` 952, `fs_git_root` 983, `fs_default_cwd` 1000)
- Modify: `hermes_cli/dashboard_auth/access_http.py` (move `/api/fs` from admin to profile-scoped)
- Test: extend `tests/hermes_cli/test_granted_user_files.py`

**Interfaces:**
- Consumes: Task 9's `restricted_files_root(request)`; existing `_hosted_fs_read_guard(target, request)` (jails reads to `_managed_files_policy(request).locked_root`, so Task 9 already scopes list/read-text/read-data-url/download once those routes are reachable).
- Produces: all seven `/api/fs/*` routes safe for a restricted caller: reads jailed (existing guard), writes jailed (new), `git-root` and `default-cwd` jailed, SSH workspace adapters never used for a restricted caller.

Gaps to close (each found in the current handlers): (1) the SSH `backend` branch returns before `_hosted_fs_read_guard` runs; (2) `fs_write_text` has no request and no guard; (3) `fs_git_root` has no guard; (4) `fs_default_cwd` returns the host's cwd.

- [x] **Step 1: Extend the failing tests** (append to `tests/hermes_cli/test_granted_user_files.py`)

```python
def _fs(client, route, **params):
    return client.get(f"/api/fs/{route}", params={"profile": "sentio-sales", **params})


def test_fs_list_and_read_work_inside_the_workspace(env):
    client = _client(frozenset({"sentio-sales"}))
    assert [e["name"] for e in _fs(client, "list", path=str(env.ws)).json()["entries"]] == ["notes.txt"]
    assert _fs(client, "read-text", path=str(env.ws / "notes.txt")).json()["text"] == "hello"
    assert _fs(client, "read-data-url", path=str(env.ws / "notes.txt")).status_code == 200
    assert _fs(client, "download", path=str(env.ws / "notes.txt")).status_code == 200


@pytest.mark.parametrize("route", ["list", "read-text", "read-data-url", "download", "git-root"])
def test_fs_reads_outside_the_workspace_are_refused(env, route):
    client = _client(frozenset({"sentio-sales"}))
    outside = str(env.home / "profiles" / "other" / "workspace" / "theirs.txt")
    assert _fs(client, route, path=outside).status_code == 403
    assert _fs(client, route, path=str(env.home / "profiles" / "sentio-sales" / "config.yaml")).status_code == 403


def test_fs_write_text_is_confined_to_the_workspace(env):
    client = _client(frozenset({"sentio-sales"}))
    ok = client.post("/api/fs/write-text?profile=sentio-sales",
                     json={"path": str(env.ws / "notes.txt"), "content": "changed"})
    assert ok.status_code == 200 and (env.ws / "notes.txt").read_text() == "changed"
    bad = client.post("/api/fs/write-text?profile=sentio-sales",
                      json={"path": str(env.home / "profiles" / "sentio-sales" / "config.yaml"), "content": "pwn"})
    assert bad.status_code == 403
    assert (env.home / "profiles" / "sentio-sales" / "config.yaml").read_text() == "secret: 1"


def test_fs_default_cwd_is_the_workspace_and_never_the_host(env):
    assert _fs(_client(frozenset({"sentio-sales"})), "default-cwd").json()["cwd"] == str(env.ws.resolve())


def test_an_ssh_workspace_adapter_is_never_used_for_a_restricted_caller(env, monkeypatch):
    def boom(profile=None):
        raise AssertionError("SSH backend consulted for a restricted caller")

    monkeypatch.setattr("hermes_cli.web_routers.files._fs_backend", boom)
    client = _client(frozenset({"sentio-sales"}))
    assert _fs(client, "list", path=str(env.ws)).status_code == 200
```

Add to `test_dashboard_rest_access.py`: a user granted `alpha` gets 200 on `GET /api/fs/list?profile=alpha`, 403 on `?profile=beta` and with no selector; an admin still reaches `/api/fs/list`.

- [x] **Step 2: Run to confirm they fail**

Run: `scripts/run_tests.sh tests/hermes_cli/test_granted_user_files.py -v -k "fs"`
Expected: FAIL (the routes are admin-only in the classifier, so the test app is not at fault; with the middleware stub the handlers run unrestricted: reads outside return 200, write-text escapes, default-cwd returns the host cwd).

- [x] **Step 3: Add the restricted-scope helper** to `hermes_cli/web_server_files.py`

```python
def refuse_outside_restricted_root(request: Optional[Request], target: Path) -> Path | None:
    """For a grant-scoped caller, 403 unless *target* resolves inside its workspace; returns that root
    (None when the caller is not restricted). Reads reach the same check through ``_hosted_fs_read_guard``;
    this is for the routes that have no read guard (write, git-root)."""
    root = restricted_files_root(request)
    if root is not None and not _hosted_fs_path_allowed(root, target):
        raise HTTPException(status_code=403, detail="Path is outside your workspace")
    return root
```

- [x] **Step 4: Patch the seven handlers** in `hermes_cli/web_routers/files.py` (import `refuse_outside_restricted_root` and `restricted_files_root` beside the other `web_server_files` imports at line 32)

For `fs_list`, `fs_read_text`, `fs_read_data_url`, `fs_download` (they already call `_hosted_fs_read_guard` on the local path), skip the SSH adapter for a restricted caller:

```python
    backend = None if restricted_files_root(request) else await asyncio.to_thread(_fs_backend, profile)
```
`fs_download` and `fs_read_text` take `request: Request = None`; `restricted_files_root(None)` is `None`, so direct callers are unchanged.

For `fs_write_text` (add `request: Request` as the second parameter, ahead of `profile`):

```python
async def fs_write_text(payload: FsWriteText, request: Request, profile: Optional[str] = None):
    text = payload.content or ""
    restricted = restricted_files_root(request)
    backend = None if restricted else await asyncio.to_thread(_fs_backend, profile)
    ...
    target = _fs_path(payload.path, decode_fallback=False)
    refuse_outside_restricted_root(request, target)
```

For `fs_git_root` (add `request: Request`):

```python
    backend = None if restricted_files_root(request) else await asyncio.to_thread(_fs_backend, profile)
    ...
    target = _fs_path(path)
    refuse_outside_restricted_root(request, target)
```

For `fs_default_cwd` (add `request: Request`):

```python
    restricted = restricted_files_root(request)
    if restricted is not None:
        return {"cwd": str(restricted), "branch": ""}
    backend = await asyncio.to_thread(_fs_backend, profile)
```

- [x] **Step 5: Move `/api/fs` to profile-scoped** in `access_http.py`

Remove `"/api/fs"` from `_ADMIN_PREFIXES`, add it to `_PROFILE_PREFIXES` and `_EXPLICIT_PREFIXES`:

```python
_PROFILE_PREFIXES = (..., "/api/pairing", "/api/files", "/api/fs")
_EXPLICIT_PREFIXES = ("/api/sessions", "/api/cron/jobs", "/api/profiles/", "/api/files", "/api/fs")
```
Update the comment above `_ADMIN_PREFIXES`: files routes are scoped per grant by `restricted_files_root`; an admin keeps host-wide access.

- [ ] **Step 6: Run to confirm they pass**

- [x] Focused suites and reviewed fixes passed as recorded in the execution table.
- [ ] Broad hermes_cli `-k` command below is deferred to Task 12.

Run: `scripts/run_tests.sh tests/hermes_cli/test_granted_user_files.py tests/hermes_cli/test_dashboard_rest_access.py tests/hermes_cli/test_dashboard_document_access.py -v`
Expected: all PASS. Then run `scripts/run_tests.sh tests/hermes_cli/ -k "fs or files or managed or hosted"` to prove the admin and local paths did not change.

- [x] **Step 7: Commit**

```bash
git add hermes_cli/web_routers/files.py hermes_cli/web_server_files.py hermes_cli/dashboard_auth/access_http.py tests/hermes_cli/test_granted_user_files.py tests/hermes_cli/test_dashboard_rest_access.py
git commit -m "feat: grant-scoped /api/fs confined to the granted profile's workspace

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Sentio identity in the default `SOUL.md`

**Files:**
- Modify: `hermes_cli/default_soul.py`
- Test: `tests/hermes_cli/test_default_soul_identity.py`; check `tests/hermes_cli/test_config.py:58-110` and `tests/hermes_cli/test_home_init_soul_symlink.py` still pass

**Interfaces:**
- Consumes: existing `_ensure_default_soul_md(home)` (runs at `initialize_home`, upgrades a file whose content matches `_LEGACY_TEMPLATE_SOULS` in place), `finish_profile_layout` (seeds `DEFAULT_SOUL_MD` into every new profile).
- Produces: `DEFAULT_SOUL_MD` naming Sentio; the previous Hermes text kept as `_HERMES_DEFAULT_SOUL_MD` inside `_LEGACY_TEMPLATE_SOULS` so existing auto-seeded profiles upgrade and a hand-edited `SOUL.md` is never touched.

- [x] **Step 1: Write the failing tests** `tests/hermes_cli/test_default_soul_identity.py`

```python
"""Every profile introduces itself as Sentio; an auto-seeded Hermes SOUL.md upgrades, a customised one never does."""
from pathlib import Path

from hermes_cli.config import _ensure_default_soul_md
from hermes_cli.default_soul import DEFAULT_SOUL_MD, _HERMES_DEFAULT_SOUL_MD, is_legacy_template_soul


def test_the_default_identity_names_sentio_not_hermes():
    assert "Sentio" in DEFAULT_SOUL_MD and "Sentio One" in DEFAULT_SOUL_MD
    assert "Nous Research" not in DEFAULT_SOUL_MD


def test_the_old_hermes_text_is_recognised_as_an_untouched_template():
    assert is_legacy_template_soul(_HERMES_DEFAULT_SOUL_MD)
    assert is_legacy_template_soul(_HERMES_DEFAULT_SOUL_MD.replace("—", "--"))
    assert not is_legacy_template_soul(_HERMES_DEFAULT_SOUL_MD + "\nAlways answer in French.")


def test_an_existing_profile_with_the_old_default_is_upgraded_in_place(tmp_path):
    (tmp_path / "SOUL.md").write_text(_HERMES_DEFAULT_SOUL_MD, encoding="utf-8")
    _ensure_default_soul_md(tmp_path)
    assert (tmp_path / "SOUL.md").read_text(encoding="utf-8") == DEFAULT_SOUL_MD


def test_a_customised_soul_is_left_alone(tmp_path):
    custom = _HERMES_DEFAULT_SOUL_MD + "\nAlways answer in French."
    (tmp_path / "SOUL.md").write_text(custom, encoding="utf-8")
    _ensure_default_soul_md(tmp_path)
    assert (tmp_path / "SOUL.md").read_text(encoding="utf-8") == custom


def test_a_new_profile_is_seeded_with_the_sentio_identity(tmp_path, monkeypatch):
    from hermes_cli.profiles import create_profile

    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    path = create_profile("sentio-sales", no_alias=True)
    assert (path / "SOUL.md").read_text(encoding="utf-8") == DEFAULT_SOUL_MD
```

- [x] **Step 2: Run to confirm they fail**

Run: `scripts/run_tests.sh tests/hermes_cli/test_default_soul_identity.py -v`
Expected: collection error (`cannot import name '_HERMES_DEFAULT_SOUL_MD'`).

- [x] **Step 3: Implement** in `hermes_cli/default_soul.py`

Replace the header comment and `DEFAULT_SOUL_MD` definition:

```python
"""Default SOUL.md template seeded into HERMES_HOME on first run (Sentio One fork)."""

# The upstream Hermes default, kept verbatim: homes seeded with it carry no user intent, so
# is_legacy_template_soul() lets _ensure_default_soul_md() upgrade them to the Sentio text in place.
# agent/prompt_builder.py's DEFAULT_AGENT_IDENTITY (no SOUL.md at all) is left upstream on purpose to keep
# the merge surface small; see docs/private-fork/FORK_DECISIONS.md.
_BEHAVIOUR = (
    "Be direct: match the length of your reply to the weight of "
    "the ask — a one-line question gets a one-line answer, and finished work gets a short report of what "
    "changed, what's verified, and what's left, never a replay of the process. No filler (\"Great question,\" "
    "\"I'd be happy to\"), no restating the request back, no re-summarizing what you already said, no narrating "
    "tool calls the user can see. Plain claims over adjectives; when unsure, say so plainly. Agree because it's "
    "right, not because the user said it. Depth is earned — give it when the user asks for detail, teaches, or "
    "the stakes demand it, not by default."
)
_HERMES_DEFAULT_SOUL_MD = "You are Hermes Agent, built by Nous Research. " + _BEHAVIOUR
DEFAULT_SOUL_MD = (
    "You are Sentio, an AI agent powered by Sentio One, running in the Sentio One app. " + _BEHAVIOUR
)
```
In `_LEGACY_TEMPLATE_SOULS`, add the Hermes default and its ASCII-dashed variant, and keep the new text's variant:

```python
    # The upstream Hermes default (what every pre-Sentio profile was auto-seeded with).
    _HERMES_DEFAULT_SOUL_MD,
    _HERMES_DEFAULT_SOUL_MD.replace("—", "--"),
    # ASCII-dashed variant seeded by scripts/install.ps1 (must stay pure ASCII, see
    # tests/scripts/install/test_install_ps1_ascii_only.py); upgrading converges Windows installs on the em-dash text.
    DEFAULT_SOUL_MD.replace("—", "--"),
```
(remove the old `DEFAULT_SOUL_MD.replace("—", "--")` entry that this replaces; keep the two older generations above it.)

- [x] **Step 4: Run to confirm they pass**

Run: `scripts/run_tests.sh tests/hermes_cli/test_default_soul_identity.py tests/hermes_cli/test_config.py tests/hermes_cli/test_home_init_soul_symlink.py -v`
Expected: all PASS. If `test_config.py` has a test asserting the old text literally, update its fixture to import `_HERMES_DEFAULT_SOUL_MD` instead of changing the production behaviour.

- [x] **Step 5: Check the system prompt on a real build**

Run (from the repo root, with a temp home):

```bash
HERMES_HOME="$(mktemp -d)" python -c "
from hermes_cli.profiles import create_profile
p = create_profile('sentio-sales', no_alias=True)
print((p / 'SOUL.md').read_text()[:80])
"
```
Expected: the line starts `You are Sentio, an AI agent powered by Sentio One`. Then open `agent/prompt_builder.py` around `HERMES_AGENT_HELP_GUIDANCE` (lines 170-188): that text says "You run on Hermes Agent (by Nous Research)" and is injected when the `hermes-agent` skill is installed. Check (with the real prompt builder, `skip_context_files=False`, for a profile that has that skill) whether it appears in the assembled system prompt. If it does, do NOT change it in this task: add a line under "Risks" in the spec ("identity can still leak via HERMES_AGENT_HELP_GUIDANCE when the hermes-agent skill is installed") and tell the user.

- [x] **Step 6: Commit**

```bash
git add hermes_cli/default_soul.py tests/hermes_cli/test_default_soul_identity.py
git commit -m "feat: default SOUL.md introduces the agent as Sentio, upgrading untouched Hermes profiles

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Fork decisions, full check and final verification

**Files:**
- Modify: `docs/private-fork/FORK_DECISIONS.md`

**Interfaces:** none.

- [ ] **Step 1: Record the decisions** (append next free entries)

```markdown
- **Dxx. A grant-scoped caller's files are the granted profile's `workspace/` directory.** `/api/files` and the `/api/fs`
  routes the desktop file browser calls are no longer admin-only; `web_server_files.restricted_files_root` confines a
  restricted caller to `<profile home>/workspace` (reads through `_hosted_fs_read_guard`, writes and git-root through
  `refuse_outside_restricted_root`, SSH adapters never used). `/api/git` and `/api/media` stay admin-only. An admin keeps
  host-wide access. Each upstream merge: re-check every new `/api/fs/*` route for a guard.
- **Dxx. `GET /api/profiles/active` answers a grant-scoped caller with its granted profile**, not the machine's `default`.
- **Dxx. The default `SOUL.md` names Sentio One.** `default_soul.DEFAULT_SOUL_MD` changed; the Hermes text lives on in
  `_LEGACY_TEMPLATE_SOULS` so untouched profiles upgrade. `agent/prompt_builder.py::DEFAULT_AGENT_IDENTITY` stays upstream.
```

- [ ] **Step 2: Run the full lint and the touched suites**

- [x] Full Python `scripts/check`: 12 checks OK, exit 0 on 2026-11-02 after commit `6de7cf5bc3`.
- [ ] Broad touched suites deferred by user to the next machine.

Run: `python scripts/check`
Expected: no findings. Fix any ratchet finding by moving code into a sibling (never `# noqa`).

Run: `scripts/run_tests.sh tests/gateway/ tests/hermes_cli/`
Expected: all PASS (this proves `/api/fs`, `/api/files`, `authz_mixin` and `default_soul` changes broke nothing existing).

- [ ] **Step 3: Manual end-to-end on a real gateway** (the spec's acceptance check)

1. Default profile `config.yaml`: `gateway.owners: ["+<your number>"]`. Pair the shared number (`hermes whatsapp`). `hermes whatsapp add-user sentio-sales +<second phone>`; `hermes gateway restart`.
2. From your number: the default profile answers. From the second phone: `sentio-sales` answers. From a third, allowlisted-but-unrouted phone (add its number to `WHATSAPP_ALLOWED_USERS` by hand): no answer.
3. Desktop, signed in as the OIDC account granted only `sentio-sales`: the profile pill shows `sentio-sales`, the file browser lists that profile's `workspace/` (edit and upload work), the agent says it is Sentio, and `/api/git/status` still returns `admin_required`.
Record the outcome (pass/fail per line) in the spec under "Verification".

- [ ] **Step 4: Commit**

```bash
git add docs/private-fork/FORK_DECISIONS.md docs/private-fork/2026-11-02-shared-whatsapp-number-and-granted-user-experience.md
git commit -m "docs: fork decisions for the shared WhatsApp number and granted-user experience

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Self-review (run against the spec)

**Spec coverage**
- Workstream 1 owners list, gate, `add-user`/`list-users`/`remove-user`, dashboard endpoints, docs, `FORK_DECISIONS`: Tasks 2-6, 12. End-to-end verification first: Task 1, final manual check Task 12. Rollback and idempotence: Task 3 tests.
- Workstream 2 A (active profile): Tasks 7-8. B (workspace files, view/edit/upload/delete, `/api/fs` stays closed to unscoped use): Tasks 9-10. C (Sentio identity, existing profiles upgrade, custom `SOUL.md` untouched): Task 11. Repro of the unconfirmed profile question: Task 7.
- Out of scope per spec, not planned: per-sender tiers, pairing-code self-service, dashboard UI screens, model-key granting, hot-reload of routes.

**Placeholder scan:** none left. The only unverified signatures are named: Task 2 Step 2 (the bare `GatewayRunner` may need an attribute in the test fixture) and Task 3 Step 3 (the `remove_user` target-parsing rule, given in full).

**Type consistency:** `WhatsAppUser(number, profile, status)` and `WhatsAppUsersError` are defined in Task 3 and used unchanged in Tasks 4-5. `owner_gate(owners, *, platform, user_id, identity, primary_profile)` is defined in Task 2 and called with those names in the `authz_mixin` edit. `restricted_files_root(request)` is defined in Task 9 and used by name in Task 10. `_HERMES_DEFAULT_SOUL_MD` is defined and imported consistently in Task 11.
