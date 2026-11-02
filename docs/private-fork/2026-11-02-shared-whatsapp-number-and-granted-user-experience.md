# Shared WhatsApp number and the granted-user experience

Status: implementation and source review complete through `61b34c8ed2`; PR #36 is open at pushed head `4ee1c1c74e60f7499b3399c00e365cc8c2098b6e`, with CI partial/in progress. Linux lint passed and observed initial broad failures were reproduced on current main; replacement Linux broad totals and live acceptance remain pending. Private fork (`sentio-one/sentio`). See the [Task 12 verification and acceptance handoff](2026-11-02-shared-whatsapp-number-and-granted-user-experience-task12-verification.md) for portable receipts and the user checklist. No deployment, merge or live acceptance is claimed.

### Delivery verification status (2026-11-02)

- Rebased on current `main` `315e8949ee91a2f4308ae2be848c7970e9d8468a`; runtime/test head `e89baeb2b418f0f4bc8fe545c43c65d8306180fc`.
- Whole-branch source review through `61b34c8ed23b3732a224d8926cdd723748e4e314`: no Critical, Important or Minor findings.
- Native Windows focused batch: 15 files, 228 passed, 0 failed, 20 POSIX skips, exit 0. Windows `scripts/check`: 12 checks OK, zero health findings against current main.
- Linux `scripts/check` passed with exit 0 at `e89baeb2b418f0f4bc8fe545c43c65d8306180fc` versus current main `315e8949ee91a2f4308ae2be848c7970e9d8468a`. Current main reproduces `test_api_admission_off_loop` timing (0.60s vs 0.50s; feature initial 0.54s) and both `test_session_client_env` PATH-ordering failures (2 failed, 11 passed in that file; same two failures in feature broad run).
- Replacement Linux broad run is active and final totals remain pending. PR #36 is open at pushed head `4ee1c1c74e60f7499b3399c00e365cc8c2098b6e`; CI is partial/in progress with no final conclusion. Live desktop and phone acceptance remains pending after the PR is ready. Use the portable checklist linked above and record each outcome below when actually performed.

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

The 2026-11-02 dashboard diagnosis below found a `sentio-sales` runtime behind a `default` transport and active-profile label. Native desktop behaviour and the identity model response remain unverified.

### Components

- **A. Active profile follows the grant.** For a non-admin, `/api/profiles/active` returns the profile their grant names (first granted when several; the switcher lists only granted profiles). Admins unchanged. Route stays `open`. No desktop change expected.
- **B. Profile-scoped workspace files.** New `hermes_cli/web_routers/workspace_files.py`: `/api/profiles/<name>/workspace/...` for list, read, write, mkdir, upload, delete. Classified `profile` in `access_http.py`, so the existing grant check applies to anyone granted that profile. `/api/fs` and `/api/files` stay admin-only. Paths resolve against the profile's workspace root; `..`, absolute paths and symlinks leaving the root are refused, checked on the resolved path; writes use the existing atomic-write helper. The desktop file browser uses this route for a granted non-admin connection and keeps `/api/fs` for admins and local mode. Access level: view, edit, upload and delete (user decision).
- **C. Sentio identity.** `create_profile` seeds a default `SOUL.md` ("Sentio, an AI agent powered by Sentio One, running in the Sentio One app") for new profiles; an existing `SOUL.md` is never overwritten. A one-time migration writes it only into existing profiles that lack one (e.g. `sentio-sales`). The upstream Hermes line in `prompt_builder.py` is left as the fallback to keep the merge surface small. Identity is read once at session start, so the system prompt stays stable for the conversation. Recorded in `FORK_DECISIONS.md`.

### Tests

Non-admin A lists, reads and writes profile A's workspace and gets 403 for profile B's; `../` and symlink escapes fail; `/api/fs` still returns `admin_required` for non-admins; `/api/profiles/active` returns the granted profile for non-admins and the server profile for admins; a new profile has a `SOUL.md` and a pre-existing one is untouched; the migration skips profiles that already have one.

### Manual diagnosis: 2026-11-02 (Task 7)

Read-only inspection of `one-agent.sentiolangley.com` used an OIDC session with
`admin=false` and only the `sentio-sales` grant. `GET /api/profiles/active` returned
`default` with `current: default`. The file browser showed the same `admin_required`
denial returned by `/api/files?profile=sentio-sales`.

Reading the existing probe session `local-88b59369` through
`/api/sessions?profile=sentio-sales` showed a session key beginning
`agent:sentio-sales:local:dm:`, `profile_name: sentio-sales`, and
`transport_profile: default`. This is evidence that the observed dashboard TUI session
ran under `sentio-sales` despite the default label; Task 8 addresses the label, not a
proven runtime misroute. The messages endpoint showed `failed_turn`: HTTP 401
`Invalid Authorization` from CommandCode. There was no successful model identity
answer. The profile SOUL endpoint returned the untouched Hermes default template,
which is a source of the available Hermes identity text and motivates Task 11;
the assembled prompt and resulting model response were not verified.

Native desktop and gateway logs were unavailable through the native pipe. No new
prompts were sent during this inspection. This does not establish native desktop
runtime behaviour, log evidence, successful provider access, or an identity answer.
Tasks 8-11 remain in scope, with those verification limits retained.

## Out of scope

Per-sender permission tiers inside one profile; pairing-code self-service; dashboard UI screens for WhatsApp users; granting model keys; changes to `/api/fs` access rules.

## Risks

- Reproduced 2026-11-02 in the dashboard: the observed TUI session ran under `sentio-sales` (session metadata), while active-profile and file-access symptoms persisted. Its SOUL endpoint exposed the untouched Hermes default identity text; the model identity answer failed with provider HTTP 401, so its identity response and assembled prompt are unverified. Native desktop and gateway logs remain unverified; see the manual diagnosis above.

- The shared-bot to satellite path for WhatsApp is untested; the first task may surface fixes (Baileys number vs LID identity, session keys).
- Owner on the default profile means a hijacked owner number reaches host-level access. Mitigation is the explicit `owners` list; it is the user's chosen trade-off.
- `prompt_builder.py` and `/api/profiles/active` are touched or depended on near upstream code; each gets a `FORK_DECISIONS.md` entry for upstream merges.
- Routes are read when the gateway starts; `add-user` and `remove-user` tell the operator to restart the gateway. Hot reload is out of scope.
- Verified 2026-11-02 with the real prompt builder, isolated profile and installed bundled `hermes-agent` skill: identity can still leak via `HERMES_AGENT_HELP_GUIDANCE` when the `hermes-agent` skill is installed. Both the Sentio SOUL identity and Hermes helper guidance appear in the assembled prompt. A second real build without skill tools also includes `HERMES_AGENT_HELP_GUIDANCE_NO_SKILLS`, which names Hermes; the conflict extends beyond the installed-skill case. Both helper variants and `DEFAULT_AGENT_IDENTITY` remain upstream. No model API call was made, and the earlier provider HTTP 401 still prevents any claim about the resulting identity answer.
- `HERMES_AGENT_HELP_GUIDANCE` says "You run on Hermes Agent (by Nous Research)" when the `hermes-agent` skill is installed; the identity change does not touch it. The plan checks whether it reaches a Sentio profile's prompt.
- A sandboxed profile's `/workspace` inside Docker is ephemeral (tmpfs, no bind mounts), so files in `<profile home>/workspace` that a user uploads are not visible to the sandboxed agent. Making them visible is a separate decision (it needs a sandbox mount) and is out of scope here.

## Spec corrections (found while reading the code for the plan; these override the text above)

1. **WhatsApp routes use `chat_id`, not `user_id`.** `ProfileRoute.matches` compares `user_id` exactly, and the bridge sends `senderId` as a JID or LID. Only `chat_id` matching is alias-aware for WhatsApp (number, JID and LID collapse to one identity). A WhatsApp DM's `chat_id` is the person's JID, so `add-user` writes `chat_id: <bare number>`. Per-sender routing inside groups is out of scope.
2. **`remove-user` deletes the route entry** instead of disabling it (simpler, and re-adding the same person has no stale entry to reconcile). The profile is kept unless `--delete-profile`.
3. **Identity (Workstream 2C): a default `SOUL.md` is already seeded.** `finish_profile_layout` seeds `hermes_cli.default_soul.DEFAULT_SOUL_MD` into every new profile; it just says "Hermes Agent, built by Nous Research". The fix is to change that text and keep the old text in `_LEGACY_TEMPLATE_SOULS`, so the existing `initialize_home` upgrade path moves untouched profiles over. No new seeding step and no new migration; a hand-edited `SOUL.md` is never overwritten.
4. **Files (Workstream 2B): no new router.** The managed-files API (`/api/files*`) already resolves every path through a locked-root policy (`_managed_files_policy`), and the `/api/fs` reads already pass `_hosted_fs_read_guard`. The plan scopes that policy to the granted profile's `workspace/` directory for a restricted caller, closes the four `/api/fs` gaps (SSH adapter branch, `write-text`, `git-root`, `default-cwd`), and moves `/api/files` and `/api/fs` from admin-only to grant-scoped. `/api/git` and `/api/media` stay admin-only. The desktop client is unchanged.
