# Shared WhatsApp number and the granted-user experience

Status: design approved in conversation 2026-11-02; spec awaiting review. Private fork (`sentio-one/sentio`).

Two workstreams with one theme: a person sees and uses only their own profile.

- **Workstream 1:** one WhatsApp number serves many people, each routed to their own sandboxed profile, with the owner on the default profile.
- **Workstream 2:** a non-admin who signs in to the desktop app gets their granted profile, their workspace files, and a Sentio identity.

OpenClaw reference (docs.openclaw.ai, multi-agent routing): senders are bound to agents by `bindings` (most specific match wins); tools, sandbox and workspace are per agent, not per sender. The fork's equivalent already exists: `gateway.profile_routes` (sender to profile) plus per-profile Docker sandboxing. This spec adds what is missing around it. It adds no per-sender permission tiers inside one profile (rejected: breaks per-conversation prompt caching and the profiles-are-islands rule).

## Workstream 1: shared WhatsApp number

### Current state (verified in code)

- `gateway/profile_routing.py`: `ProfileRoute` matches `platform` + `user_id` (specificity 16), WhatsApp numbers/JIDs/LIDs normalised through `expand_whatsapp_aliases`. Routes are declared on the default profile; a route applies only to the bot of its `bot_profile` (default: the default profile's bot).
- `gateway/authz_mixin.py::_principal_authorized`: admission uses the receiving bot's allowlist (`WHATSAPP_ALLOWED_USERS`, pairing store, `dm_policy`), not the routed profile's `.env`.
- A sender admitted but matching no route runs on the default profile. The default profile is the host-level launch profile.
- No test or doc covers `profile_routes` with WhatsApp. `website/docs/user-guide/messaging/whatsapp.md` describes only one paired number per profile.
- Adding a person is manual: create profile, edit `profile_routes`, edit the allowlist.

### Decisions

1. The owner's number runs on the default profile (no route). This is the user's choice; the default profile is the one that can do everything.
2. Admission is explicit. New `gateway.owners` list (WhatsApp numbers) in the default profile's `config.yaml`. A WhatsApp sender is admitted only if they are in `owners` or match an enabled `user_id` route. Anyone else is ignored and never falls through to the default profile.
3. The check applies only when `owners` is set. Without it, behaviour is exactly today's.
4. A route to a missing or parked profile keeps today's behaviour: dropped with a warning, never run on the default profile.
5. Unknown senders are ignored (existing allowlist behaviour). A pairing-code self-service flow is out of scope for this spec.

### Components

- **Admission check** in `gateway/authz_mixin.py` (WhatsApp, only when `owners` is set). Reuses `expand_whatsapp_aliases` so number, JID and LID match as one identity. Group, broadcast and newsletter JIDs are never senders.
- **`hermes_cli/whatsapp_users.py`** (new). `add_user(name, number)`, `list_users()`, `remove_user(name_or_number, delete_profile=False)`. Single implementation called by the CLI and the dashboard router, as `create_profile` is shared across CLI, REST and TUI.
  - `add_user`: normalise the number; refuse if it is a group JID, already an owner, or already routed; refuse if `owners` is unset; `create_profile` (sandboxed automatically under the access policy, no model keys); write the route on the default profile via `atomic_config_write` (`platform: whatsapp`, `user_id`, `profile`, `bot_profile` unset); append the number to `WHATSAPP_ALLOWED_USERS` via `save_env_value`.
  - Rollback: a failure after the profile exists undoes the route and allowlist entry, and removes the profile only if this run created it. Repeating `add_user` for an existing mapping changes nothing and reports the state.
  - `remove_user`: disables the route and removes the allowlist entry; keeps the profile and its data unless `--delete-profile`.
  - `list_users`: number, profile, status (live / parked / missing), owners included.
- **CLI:** `hermes whatsapp add-user|list-users|remove-user`, under the existing `whatsapp` command.
- **Dashboard:** `hermes_cli/web_routers/whatsapp_users.py`, list/add/remove endpoints calling the same functions. Admin-only, classified `admin` in `hermes_cli/dashboard_auth/access_http.py`. Endpoints only; no new UI screens in this spec.
- **Model keys:** `add-user` does not grant them. Output says the profile still needs a key and points at the shared-company-model-keys flow.
- **Live gateway:** if a new route is not picked up by the profile rescan, the command says to restart the gateway. The plan's first task settles which.

### Verification and tests

First plan task (before new code): real-import test against a temp `HERMES_HOME`, two profiles behind one fake WhatsApp transport. Assert: owner number lands on default; routed number lands on its sandboxed profile; allowlisted number with no route is dropped and never reaches default. If the third fails on the base branch, that confirms the hole the `owners` check closes. If the first two fail, the cause is recorded here before anything else is built.

Behaviour tests: admission (owner, routed, unrouted refused, LID/JID form of an owner number); `add_user` all steps, rollback on injected failure, idempotent repeat, group-JID refusal, refusal when `owners` is unset; `remove_user` keeps the profile and the number is then refused; dashboard endpoints return 403 for a non-admin. Each fix is shown failing on the base. Tests run through `scripts/run_tests.sh`.

Docs: `whatsapp.md` gains a "One number, many people" section; `FORK_DECISIONS.md` records the `owners` rule.

## Workstream 2: granted-user experience (desktop)

### Current state (verified in code)

- `hermes_cli/dashboard_auth/access_http.py:26` lists `/api/fs`, `/api/files`, `/api/media`, `/api/git` as admin-only (host-level; `/api/fs` reads any path). The desktop file browser calls these, so a non-admin gets `admin_required`. This is by design and stays.
- `agent/prompt_builder.py:159` `DEFAULT_AGENT_IDENTITY` is "You are Hermes Agent, built by Nous Research". Only a profile's `SOUL.md` overrides it. `create_profile` copies `SOUL.md` only when cloning, so a fresh profile has none.
- `GET /api/profiles/active` (`hermes_cli/web_routers/profiles.py:890`) returns the server's own profile (`default`) regardless of the caller's grant. The desktop store (`apps/desktop/src/store/profile.ts:258`) adopts its `current` field.

Unconfirmed: which profile the reported session actually ran under. First plan task: reproduce with a non-admin login to decide whether the active-profile fix also resolves the identity symptom or only the label.

### Components

- **A. Active profile follows the grant.** For a non-admin, `/api/profiles/active` returns the profile their grant names (first granted when several; the switcher lists only granted profiles). Admins unchanged. Route stays `open`. No desktop change expected.
- **B. Profile-scoped workspace files.** New `hermes_cli/web_routers/workspace_files.py`: `/api/profiles/<name>/workspace/...` for list, read, write, mkdir, upload, delete. Classified `profile` in `access_http.py`, so the existing grant check applies to anyone granted that profile. `/api/fs` and `/api/files` stay admin-only. Paths resolve against the profile's workspace root; `..`, absolute paths and symlinks leaving the root are refused, checked on the resolved path; writes use the existing atomic-write helper. The desktop file browser uses this route for a granted non-admin connection and keeps `/api/fs` for admins and local mode. Access level: view, edit, upload and delete (user decision).
- **C. Sentio identity.** `create_profile` seeds a default `SOUL.md` ("Sentio, an AI agent powered by Sentio One, running in the Sentio One app") for new profiles; an existing `SOUL.md` is never overwritten. A one-time migration writes it only into existing profiles that lack one (e.g. `sentio-sales`). The upstream Hermes line in `prompt_builder.py` is left as the fallback to keep the merge surface small. Identity is read once at session start, so the system prompt stays stable for the conversation. Recorded in `FORK_DECISIONS.md`.

### Tests

Non-admin A lists, reads and writes profile A's workspace and gets 403 for profile B's; `../` and symlink escapes fail; `/api/fs` still returns `admin_required` for non-admins; `/api/profiles/active` returns the granted profile for non-admins and the server profile for admins; a new profile has a `SOUL.md` and a pre-existing one is untouched; the migration skips profiles that already have one.

## Out of scope

Per-sender permission tiers inside one profile; pairing-code self-service; dashboard UI screens for WhatsApp users; granting model keys; changes to `/api/fs` access rules.

## Risks

- The shared-bot to satellite path for WhatsApp is untested; the first task may surface fixes (Baileys number vs LID identity, session keys).
- Owner on the default profile means a hijacked owner number reaches host-level access. Mitigation is the explicit `owners` list; it is the user's chosen trade-off.
- `prompt_builder.py` and `/api/profiles/active` are touched or depended on near upstream code; each gets a `FORK_DECISIONS.md` entry for upstream merges.
