# Sentio One fork: decisions and upstream merge guide

Single index of where this fork deliberately differs from upstream (NousResearch/hermes-agent), why, and what
to re-check when pulling upstream in. The detailed design lives in `ACCESS_CONTROL_PLAN.md` and
`INVITES_DESIGN.md`; this file is the map over them plus everything those two do not cover.

- **Upstream base:** `e5eb6b36fe` (2026-10-15), the parent of the first fork commit `a2295b5f22`. On 2026-10-23 upstream
  `main` was `cb757fac59`, 3,200 commits ahead; a dry-run merge (`git merge-tree`) conflicted in exactly three files:
  `gateway/session_controls.py`, `session_group_controls.py`, `session_identity.py` (all hotspots in section 2).
  `git diff e5eb6b36fe HEAD` is the whole fork: 119 files, 39 modified upstream files, 80 added.
  After each upstream merge, record the new base here.
- **Evidence:** every row below was read from the diff against that base, not from memory.
- **Fork rule:** upstream is read-only; nothing goes back. Prefer new files over edits to upstream files.

## 0. Locked decisions (do not re-litigate)

- **L1. The desktop app is remote-first, with an optional local install.** A person opens the app, connects to their
  company gateway (usually by an invite link), and works there. Running an agent locally on their own machine is something
  they can choose to add, never the default path and never required. **How it is delivered is not decided**; see O9.
  D8 (Light = remote-only, never offers local) is the interim implementation of the remote half, not the final shape.
- **L2. Everything after the first push goes through a pull request**, including every upstream merge. (Decided 2026-10-23;
  the work in this document was committed straight to `main` once, before that rule.) Branch protection on `main` is not
  enabled yet; enable it with the repo-private step (O0).
- **L3. The repo stays public until it is cleaned up and the upstream merge has landed, then goes private.** The README is
  rewritten then for what the Sentio One agent is for a business (it currently reads as a fork note on top of upstream's README).
- **L4. The go-live gate (section 5) must be green before a real customer is on a gateway.** The unenforced sandbox (O1) is the
  biggest item on it. It is deliberately deferred, not forgotten.

## 1. Decisions

### D1. Hosting model: one gateway per company, thin client
One central gateway per company (Dokploy), a remote-only desktop client, per-user access to individual
profiles. Rejected for now: one shared multi-tenant gateway (it would need a tenant dimension in every check).
Profiles are **not** an OS boundary (SECURITY.md 2.2); see D9 for what that leaves open.

### D2. Access policy is claim-driven, fail closed, and off unless configured
`gateway/access_policy.py`, `access:` config root.
- IdP `groups` claim is the source of truth: `<profile_group_prefix><profile>` grants a profile,
  `admin_groups` grants everything. `users`/`admins` rows add static grants for IdPs without groups.
- **Subject = full `(provider, issuer, sub)` triple**, via `gateway.session_identity.dashboard_subject`.
  Never a bare `sub` or an issuer host (two tenants on one Entra host would collide).
- **`group_providers` (default `self_hosted`)**: group grants count only for these providers, so another
  provider cannot mint a grant by emitting a matching group name.
- Only the `groups` claim is read, not `roles`. An empty `profile_group_prefix` turns group grants off.
- **No `access:` configured = behaviour identical to upstream.** Every fork gate checks `policy.configured`
  first. This is what keeps local single-user installs working; do not weaken it.
- `access` is in `_EXTRA_KNOWN_ROOT_KEYS`, not `DEFAULT_CONFIG` (it grew `config_defaults.py` past its size
  ratchet), and is not in `GatewayConfig.to_dict` (the loader re-reads YAML).

### D3. Deny by default; no implicit host scope
Upstream stamped `profile_scope: host` and `operator=True` on every authenticated `/api/ws` socket. The fork
replaces that with `dashboard_auth/access.py::scope_for_identity`:
- Policy configured: admin keeps host scope and operator; everyone else gets `allowed_profiles` (empty for an
  unknown user) and `operator=False`. Caller-supplied scope fields are dropped.
- Explicit trusted identities keep host scope: the PTY child's internal credential and the launch-time
  session token (`_TRUSTED`). **Not yet bound to a profile** (see D9, open item O2).
- `operator=False` is deliberate: upstream `session:operator` lets the holder see other subjects' sessions
  and the whole Bot Chat roster, so per-user session privacy needs it off for non-admins.

### D4. Enforcement is layered, every plane checks
A grant is checked at each place a profile can be selected, because upstream has several independent ones:
1. `gateway/session_controls.py::AuthorityConnection`: `_sibling_for` checks the grant for the **own home**
   too (upstream returned early there), and `dispatch` checks the own authority for every verb except
   `_OWN_PROFILE_EXEMPT` (`ping`, `profiles.list`, `runtime.describe`).
2. `Principal.allowed_profiles` (None = not grant-scoped) carried on the actor.
3. Legacy RPC plane `tui_gateway/ws_legacy_fallback.py`: grant check before dispatch, `ADMIN_ONLY_METHODS`
   unreachable for grant-scoped actors, and `server._profile_home` wrapped by `grant_checked` (contextvar
   backstop for handlers that resolve a profile themselves).
4. WebSocket upgrades `/api/pty`, `/api/console` (via `_ws_gate`), `/api/audio/*`: refuse an ungranted
   `?profile=` with close 4403 `profile not granted` before accept. A grant-scoped PTY ignores `?cwd=`.
5. REST: `dashboard_auth/access_http.py::classify` returns `open | profile | admin`, **default admin** for
   anything unclassified, enforced from `middleware._authorized_call` on all three session paths.
6. Rosters filter to the grant: `GET /api/profiles`, `profiles.list` (canonical + legacy),
   `runtime.describe.allowed_profiles` (declared in `tui_gateway/contracts`, TypeScript regenerated).
- Refusals use the same code/reason an unknown profile gets (`profile_mismatch`, 4064), so a denial reveals
  nothing about what exists.

### D5. Grants are read when a connection opens
IdP group changes apply on the next connection; open sockets live until they close (finding A8). Accepted
revocation latency. Revoking an invite grant also applies at next connect.

### D6. Invites: an invite grants a profile, it never authenticates
`gateway/access_grants.py` (SQLite `access_grants.db` in the launch home), `web_routers/access_invites.py`.
- Token: 32 random bytes, only the SHA-256 stored, constant-time compare, shown once, never logged or audited.
- Redeem is one `BEGIN IMMEDIATE` transaction; one redemption per person per invite; a revoked person needs a
  fresh invite. All refusals are a single `InviteError`. TTL 1h to 30 days (default 7d), uses 1 to 1000 (default 1).
- Stored grants merge into `grant_for(extra_profiles=)`, **never admin**, only when a policy is configured.
  Unreadable store fails closed.
- Rate limit on redeem: 10 failures / 10 min per person, 30 per client address.
- Optional email binding trusts the IdP email claim (the session has no `email_verified`).
- `redeem` is the only non-admin route under `/api/access`; the rest are admin by the default-deny gate.
- Admin UI: `web/src/pages/AccessPage.tsx`, nav entry hidden for non-admins via `/api/auth/me.access`
  (`access_summary`, a hint only; routes enforce).

### D7. Managed, pinned per-company policy
`deploy/dokploy/managed/config.yaml` is mounted read-only and applied on top of the profile config through
**upstream's** `hermes_cli/managed_scope.py`, so neither a granted user nor an agent editing the profile's
config can change admins or group mapping. Every `access:` key is pinned on purpose: a key left out stays
editable. If a new `access:` key is added, add it to this file.

### D8. Light desktop artifact: remote-only, no company baked in (interim, see L1 and O9)
- `apps/desktop/electron/light-first-run.ts`: `payload === 'light'` stamp means the first run goes straight to
  the remote form; `createFirstRunSetupGate({canContinueLocal})` ignores a local-install request (logged).
- One installer serves every company: **no gateway URL in the build**. The URL arrives via
  `hermes://connect?url=&invite=`.
- Deep link: `lib/connect-link.ts` strict parser (https only, http for loopback, no credentials/query/fragment,
  lookalike hosts shown as punycode). The link only **fills the form after a confirmation screen showing the
  host**; it never connects on its own. Token held in memory only, dropped on cancel or after redeem.
  Goes through the existing `hermes://` channel (`use-desktop-integrations.ts`), no main-process change.
- Build: `scripts/bundles/desktop.py --variant light` (upstream's builder), run by
  `.github/workflows/release-light.yml` on tag `v*` (D11).

### D9. Accepted limits (decided to ship with, not fixed)
- A grant is the whole profile on REST; REST session routes are not filtered per owner, so two users sharing a
  profile see each other's sessions there (WebSocket plane isolates by subject). **One profile per person.**
- `multipart`/`urlencoded` bodies are not inspected for a `profile` field (query and JSON only).
- Service tokens and native tickets are trusted callers, outside the REST gate.
- `/api/pub` and `/api/events` are keyed by a client-chosen channel id (capability secret, not authorization).
- `runtime.describe` still returns the launch authority's `profile_id` (a home path) to a non-granted caller.
- Non-admin desktops lose the host file browser, git and update panels (admin-only by design).
- A granted user's PTY and agent tools run as the server OS user (see O1).
- Token-mode WS leg now carries issuer and groups. Sessions owned under the old issuer-less subject are not
  visible to that user afterwards. Irrelevant for a fresh deployment; matters only when migrating one.

### D10. Release and housekeeping
- Version `0.0.0` to `0.1.0` in `pyproject.toml`, `uv.lock` and `apps/desktop/package.json`. `uv.lock` had been left
  at `0.0.0`, which made `python scripts/run_tests.sh`'s environment bootstrap fail (`uv lock --check`); fixed with `uv lock`.
- README: fork section above upstream's README. `architecture/` (diagrams) and `.plugin-skills/` (plugin builder
  skills, verified against upstream `e5eb6b36`) are fork-only documentation.
- Comment-only trims in upstream files: `apps/desktop/electron/main.ts` **keeps** its trimmed comment, because restoring
  upstream's longer one puts the file 7 lines over its health-ratchet cap (`scripts/check` fails). `tui_gateway/server.py`
  was restored to upstream's comment (it passes). If a merge conflicts on that `main.ts` comment, take the fork's.

### D11. Hosting, CI and credentials (decided, done)
- **Source and CI: GitHub** (`github.com/sentio-one/sentio`). GitLab is retired and its files are removed.
- **`Fork CI`** (`.github/workflows/fork-ci.yml`, GitHub-hosted runners): `uv lock --check`, the fork's Python tests, a real
  `docker compose config` of the Dokploy file (and that it refuses to start without its required settings), web and desktop
  typecheck and the fork's JS tests. Keep its file lists equal to section 3.
- **Upstream's 55 workflows are disabled on GitHub, not deleted** (they target Blacksmith runners we do not have, and deleting
  them would make every merge a modify/delete conflict). `scripts/fork/disable-upstream-workflows.sh --apply` re-applies it;
  workflows added by a merge arrive enabled. Kept active: `Fork CI`, `Release Light Desktop`.
- **`Release Light Desktop`** builds on a `v*` tag and publishes the MSIX; a manual run only builds and uploads the artifact.
  Its actions are pinned to the same SHAs upstream uses. The installer is unsigned.
- **Remotes:** `origin` = GitHub, `upstream` = NousResearch/hermes-agent with push set to a dead URL.
- **Deploy: Dokploy**, one Compose project per company (D1, D7).
- **Model providers:** Command Code (`COMMANDCODE_API_KEY`, upstream `plugins/model-providers/commandcode`, now in the compose
  file) and Codex, **one subscription per company**, logged in by the company's admin with `hermes auth` inside the container
  (OAuth, stored with the profile on the `hermes-data` volume; not an env key).

### D12. Sandbox: non-main profiles run tools in Docker-in-Docker (partly built)
Resolves A6 in direction. Built:
- `deploy/dokploy/docker-compose.yml` has a `dockerd` service (`docker:27.5.1-dind`, pinned, privileged, no published port,
  no domain, own `dockerd-data` volume) and `hermes` gets `DOCKER_HOST=tcp://dockerd:2375` and waits for it to be healthy.
  **No host `docker.sock` anywhere**: it would be root on the Dokploy host and reach other companies' containers.
  `test_agent_sandboxes_run_on_the_companys_own_docker_daemon_never_the_hosts` fails if that changes.
- `managed/config.yaml` pins `terminal.docker_volumes: []`, `docker_extra_args: []`, `docker_mount_cwd_to_workspace: false`
  (the overlay is global, so it covers every profile and a user cannot loosen their sandbox).
- Not pinned on purpose: `terminal.backend`. The main profile must keep the local backend because its agent provisions the
  others (D13), and the managed overlay cannot differ per profile.
Not built, so **not yet a boundary between people in one company**:
- Nothing makes a non-main profile's agent use the docker backend, or stops a granted user from setting
  `terminal.backend: local` (the REST config routes are `profile` class and allow it). See O1 for the seam.
- Never run end to end: the sandbox image pull inside dind, per-profile containers (`docker_shared_container_key`), that file tools
  and in-process tools (MCP stdio, plugins, `execute_code`) really run inside the sandbox, that a profile's `.env`/`auth.json`
  is not mounted into it, and `dockerd` surviving a redeploy (volume `dockerd-data`).

### D13. The main profile's agent provisions the rest (decided)
The pre-set-up launch profile is the admin profile. You talk to its agent and it creates other profiles, configured for the
docker backend, the way it works locally today. This is the reason D12 leaves `terminal.backend` unpinned.
Consequences to keep in mind:
- The main profile's agent is **host-level inside the gateway container** (local backend, can write `/opt/data`, can use the
  company's `dockerd`). Only admins reach it (grant model), but anyone who can inject instructions into an admin's main-profile
  session can create profiles and write their configs. Treat that profile like root: keep untrusted content (inbound messaging
  platforms, webhooks, public skills) off it.
- Profile creation by RPC or REST stays admin-only; the agent path uses the CLI/files inside the container, so it is not
  covered by the access gate at all. That is intended, and is why the point above matters.
- A profile the agent provisions must get a docker backend and a group grant (`hermes-profile-<name>` in the IdP, or an
  invite). A convention or a skill for "create a profile" should do both; none exists yet (O3).

## 2. Upstream files the fork modifies (merge conflict map)

Highest risk first. "Keep" is what must survive a conflict resolution.

| File | Fork change | Keep / re-check |
|---|---|---|
| `gateway/session_controls.py` | `_OWN_PROFILE_EXEMPT`, `_require_granted`, `_allowed`, own-home check in `_sibling_for`, per-verb check in `dispatch`, `allowed_profiles` in `runtime.describe` | All of it. Upstream edits to `_sibling_for`/`dispatch` can silently bypass the grant: re-read both after any merge. |
| `tui_gateway/ws_legacy_fallback.py` | grant contextvar, `ADMIN_ONLY_METHODS`, `_grant_refusal`, `grant_checked`, `granted_profiles_scope` | New upstream legacy verbs that name or pick a profile need a decision: admin-only or checked. |
| `tui_gateway/server.py` | `@grant_checked` on `_profile_home` | The decorator. |
| `hermes_cli/web_routers/chat_ws.py` | `_profile_granted` in `_ws_gate`, `_pty_cwd_param`, `gateway_ws` uses `scope_for_identity` and `operator` | `operator=True` must never return unconditionally. |
| `hermes_cli/dashboard_auth/middleware.py` | `_authorized_call` on all three session paths | A new upstream session path (new `call_next`) must also go through it. |
| `hermes_cli/dashboard_auth/routes.py` | `access` in `/auth/me`, `groups` in ws-ticket mint, includes `access_invites` router | Ticket `extra.groups` is what carries groups to the socket. |
| `hermes_cli/web_server_chat.py` | `groups` from ticket, `issuer`+`groups` on the `?token=` leg | Both legs must stamp issuer and groups or subjects diverge. |
| `plugins/dashboard_auth/_shared.py` | `groups_from_claims`, `Session.groups` | New IdP plugins must populate `groups` through this. |
| `hermes_cli/dashboard_auth/base.py` | `Session.groups` | |
| `hermes_cli/web_routers/profiles.py`, `tui_gateway/methods_profiles.py`, `gateway/session_group_controls.py` | roster filtering | A new roster endpoint must filter too. |
| `hermes_cli/web_routers/audio.py` | `_ws_profile_permitted` on both streams | |
| `gateway/session_identity.py` | `dashboard_subject` extracted from `authenticated_subject` | The string format is what grants are keyed on. Changing it orphans every grant. |
| `gateway/config.py`, `config_loader.py`, `hermes_cli/config.py` | `access` field, bridge entry, known root key | |
| `gateway/session_contract.py`, `tui_gateway/contracts/canonical.py`, `apps/shared/src/gateway-contract.*` | `allowed_profiles` on `Principal` and `RuntimeDescribeResult` | Regenerate the contract after merges that touch either side. |
| `hermes_cli/dashboard_auth/audit.py` | four `ACCESS_INVITE_*` events | |
| `apps/desktop/src/components/desktop-install-overlay.tsx` | local-or-remote card extracted to `desktop-install-setup-choice.tsx`; `remoteOnly` early return | Heaviest desktop conflict (102 lines removed). Re-apply by hand rather than merge-resolving. |
| `apps/desktop/electron/main.ts`, `first-run-setup-gate.ts` | `firstRunPolicy`, `canContinueLocal`; trimmed comment in `resolveHermesBackend` | Keep the trimmed comment (size ratchet, D10). |
| `apps/desktop/src/components/remote-setup/{first-run.tsx,use-remote-setup.ts}` | optional `onBack`, `initialUrl`, `onApplied` | |
| `apps/desktop/src/app/contrib/wiring.tsx`, `hooks/use-desktop-integrations.ts` | `DeepLinkDialogs` replaces `McpInstallDeepLinkDialog`; `connect` deep link kind | Upstream additions to the dialog list must go into `DeepLinkDialogs`. |
| `apps/desktop/src/global.d.ts`, `i18n/en_first_run.ts`, `i18n/types_first_run.ts`, `i18n/overlay-gaps.json` | `remoteOnly`, English-only strings | Non-English locales fall back (listed in `overlay-gaps.json`). |
| `web/src/App.tsx` | `/access` route, nav item, `withoutAccessNav` | |
| `pyproject.toml`, `uv.lock`, `apps/desktop/package.json` | version `0.1.0` | After a merge that touches the version or deps run `hermes pm lock` (or the PM-pinned `uv lock`); `Fork CI` runs `uv lock --check`. |
| `README.md` | fork section on top | |

Fork-only new files (no conflict possible, but they depend on upstream APIs): `gateway/access_policy.py`,
`access_grants.py`, `session_access.py`; `hermes_cli/dashboard_auth/access.py`, `access_http.py`;
`hermes_cli/web_routers/access_invites.py`; `web/src/pages/AccessPage.tsx`, `lib/api-access.ts`, `lib/access-nav.ts`,
`hooks/useAccessSummary.ts`; `apps/desktop/src/lib/connect-link.ts`, `store/connect-link.ts`,
`app/contrib/connect-link-dialog.tsx`, `deep-link-dialogs.tsx`; `deploy/dokploy/*`; `scripts/fork/`;
`.github/workflows/fork-ci.yml`, `release-light.yml`.

## 3. After every upstream merge

1. **New routes:** any new `/api/*` route is admin-only until classified in `access_http.py`. Decide each:
   `open`, `profile` (and where it reads the profile), or `admin`. The contract test that walks the real app
   routes fails when a profile-class handler reads its profile somewhere the gate does not look.
2. **New profile selectors:** grep the merged range for new `profile` readers on WebSocket routes, legacy RPC
   verbs, and any new `session_authority`/`_sibling_for`-style router. Each needs a grant check.
3. **New `operator`/`profile_scope` stampers:** grep `operator=True` and `'profile_scope': 'host'` in the diff.
4. **New roster/list endpoints** must filter to the grant.
5. **New identity paths** (new auth provider, new token leg) must carry `issuer` and `groups`.
6. **Regenerate** the gateway contract TypeScript if `Principal` or `RuntimeDescribeResult` changed.
7. **Disable new upstream workflows:** `scripts/fork/disable-upstream-workflows.sh --apply`.
8. **Run:**
   ```
   scripts/run_tests.sh tests/gateway/test_access_policy.py tests/gateway/test_access_grants.py \
     tests/gateway/test_access_managed_config.py tests/gateway/test_profile_access_enforcement.py \
     tests/gateway/test_legacy_fallback_access.py tests/gateway/test_dokploy_compose.py \
     tests/hermes_cli/test_dashboard_rest_access.py tests/hermes_cli/test_dashboard_ws_profile_gate.py \
     tests/hermes_cli/test_dashboard_access_scope.py tests/hermes_cli/test_access_invites_api.py \
     tests/hermes_cli/test_auth_me_access.py tests/plugins/dashboard_auth/test_session_groups.py
   scripts/run_tests.sh tests/gateway tests/hermes_cli
   python scripts/check
   cd apps/desktop && npm run typecheck && npx vitest run
   cd web && npx vitest run
   ```
   Baseline on 2026-10-23 (Linux): the 12 Python files above pass (174 tests), web 31, desktop UI 59, electron 13, both
   typechecks and `python scripts/check` (12 checks) are clean. Failures seen earlier on the Windows dev host
   (`TestEnvWriteDenylist`, `test_profiles.py`/`test_web_server.py`, `fcntl`) did not reproduce on Linux: treat them as host noise.
9. **Update this file:** new base commit, new decisions, anything resolved by hand.

## 4. Open items

- **O0. The GitHub repository is public** (`sentio-one/sentio`, checked 2026-10-23), while the README and these docs call the
  fork private. It publishes the access-control audit, the known gaps (D9), the deployment layout and the invite design.
  Decided (L3): go private after cleanup and the upstream merge; until then do not add anything more sensitive to it. At that
  point also: enable branch protection with required `Fork CI` (L2), give Dokploy a deploy key, and rewrite the README for a
  business audience.
- **O1. Make the sandbox real (deferred on purpose, must be done before go-live, L4).** Needed: non-main profiles get `terminal.backend: docker`, and a granted user cannot flip it
  to `local`. Candidate seam: where the terminal backend is resolved (`tools/terminal_tool.py::_get_env_config`, scope-aware
  `_tenv("TERMINAL_ENV")`): when an access policy is configured and the active profile is not the launch profile, force
  `docker`. That edits a ratcheted upstream file, so it needs a small sibling module and a test on the real resolver. Then
  run D12's verify list on a throwaway company.
- **O2. PTY child still uses the host-scoped internal credential.** Binding it to the PTY's profile server-side
  (`mint_ticket(extra=...)`) was planned (A3) and is not done.
- **O3. A "create a profile" skill/convention for the main agent** that creates the profile with the docker backend, a
  `hermes-profile-<name>` grant note for the IdP, and optionally an invite. Also: whether non-admins may edit their profile
  config beyond the pinned keys.
- **O4. Invite open questions:** link reuse policy (default single-use stands), who sends the email (admin copies the link
  today), JIT profile creation from a template (not built).
- **O5. Not yet exercised anywhere:** the compose stack in real Docker/Dokploy (first deploy on a throwaway company), the
  Light release workflow on a real tag, an invite redeemed end to end, WebSocket keepalive through a Cloudflare Tunnel.
- **O6. Merge upstream.** 3,200 commits behind with 3 known conflicts (header). Do it on a branch, then section 3.
- **O7. Strings** for the Access page and connect dialog are English only.
- **O8.** `.commandcode/taste/taste.md` is an empty tracked file; likely stray.
- **O9. How "remote-first, optional local" is delivered (L1).** What exists: upstream builds three desktop variants
  (`bundled`, `store`, `light`, `scripts/bundles/desktop.py --variant`) and a first-run card offering "connect to existing" or
  "install/use local" (`SetupChoice`); non-bundled builds install the runtime on demand ("bootstrap"). The fork's only addition is
  `apps/desktop/electron/light-first-run.ts`: for `payload === 'light'` the card is skipped and local install is refused.
  Options:
  - **A. One artifact, remote-first, on-demand local.** First run opens the remote form (or the invite link's server); "Install a
    local agent on this computer" sits beside it and in Settings and runs upstream's bootstrap. One installer for everyone,
    matching "one installer serves every company". Code seam: widen `firstRunPolicy` from `{remoteOnly}` to a mode
    (`remote-only | remote-first | upstream`) and reorder the card. Costs: a runtime download on first local use, and
    signing/MSIX rules for code fetched after install.
  - **B. Two artifacts.** Keep Light (no runtime at all, for locked-down company machines) and add a Full build that is
    remote-first but carries or bootstraps local. Simple and keeps a hard "no agent runtime" guarantee. Costs: two installers,
    and moving from Light to Full is a reinstall.
  - **C. Light plus a downloadable local add-on** managed from the app. Closest to A with a smaller base installer; the most new code.
  Questions that must be answered first: can a company (the gateway's managed config) forbid the local install; does a local
  agent ever share sessions, profiles or credentials with the remote one (default: no, they are separate worlds); and the known
  limit that an invite link replaces the current server means remote plus local needs two coexisting connections.
  Recommendation, not a decision: A, with a build flag so a company that wants no local runtime can still ship Light.

## 5. Go-live gate (all must be true before a customer is on a gateway)

- [ ] Sandbox enforced (O1): non-main profiles cannot run on the local backend; verified on a throwaway company with D12's list.
- [ ] PTY child no longer holds the host-scoped internal credential (O2).
- [ ] First real deploy on Dokploy passed the checklist in `deploy/dokploy/README.md`; `dockerd` survives a redeploy.
- [ ] An invite redeemed end to end against that gateway; WebSocket keepalive tested through the Cloudflare Tunnel.
- [ ] Upstream merged through a PR (O6) and `Fork CI` green on it.
- [ ] Repository private, branch protection on, README rewritten for the business audience (O0).
- [ ] Desktop delivery decided and built (O9); installer signing decision made.
- [ ] A backup and restore of `hermes-data` and `dockerd-data` tested.
