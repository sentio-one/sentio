# Sentio One fork: decisions and upstream merge guide

Single index of where this fork deliberately differs from upstream (NousResearch/hermes-agent), why, and what
to re-check when pulling upstream in. The detailed design lives in `ACCESS_CONTROL_PLAN.md` and
`INVITES_DESIGN.md`; this file is the map over them plus everything those two do not cover.

- **Upstream base:** `cb757fac59` (2026-10-23), merged on the `upstream-merge` branch (merge log: section 6). The first base was
  `e5eb6b36fe` (2026-10-15), the parent of the first fork commit `a2295b5f22`; `git diff e5eb6b36fe <first-fork-merge>` is the
  fork as first written.
- **How upstream is pulled in (L2):** branch `upstream-merge`, `git fetch upstream && git merge upstream/main`, section 3, a PR.
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
  the work in this document was committed straight to `main` once, before that rule.) Branch protection on `main` has been
  enforced since 2026-10-24 (O11).
- **L3. The repo stays public until it is cleaned up and the upstream merge has landed, then goes private.** The README is
  rewritten then for what the Sentio One agent is for a business (it currently reads as a fork note on top of upstream's README).
- **L4. The go-live gate (section 5) must be green before a real customer is on a gateway.** The unenforced sandbox (O1) is the
  biggest item on it. It is deliberately deferred, not forgotten.
- **L5. A release is a version bump promoted to `stable`** (decided 2026-10-23; amended 2026-10-30 by L6). `scripts/fork/bump-version.sh next`,
  a PR, a merge to `main`, a promotion to `stable`; then `tag-release.yml` tags `vX.Y.Z` and `release-light.yml` builds and publishes. Every other merge to `main` builds an artifact and
  releases nothing as a version, but refreshes one rolling pre-release (`main-latest`, never marked Latest) so there is a stable place
  to find and be notified of the newest build. Needs the owner's `RELEASE_TOKEN` secret (`RELEASING.md`). Merges are not all version bumps: only one commit in the
  fork era changed the version, so tagging every merge had nothing to name the tag after.
- **L6. `stable` is the line clients and testers follow** (decided 2026-10-30). A long-lived `stable` branch, promoted from `main` by
  reviewed PR (`scripts/fork/promote-stable.sh`), protected like `main` (`.github/rulesets/stable.json`). Every client Dokploy
  project deploys from it, so merging to `stable` updates them all; release tags are cut from it. `main` stays the hot line and
  keeps the rolling `main-latest` pre-release. Versions: first stable release `0.3.0`, patch `0.3.1` to `0.3.9`, then `0.4.0`.
  Client self-update is deferred: testers reinstall the unsigned NSIS `.exe`. Design and plan:
  `2026-10-30-stable-branch-design.md`, `2026-10-30-stable-branch-plan.md`.

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
- **`group_providers` (default `self-hosted`)**: group grants count only for these providers, so another
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
- A profile's enabled dashboard-plugin UI assets are served **without auth** at `/dashboard-plugins/@<profile>/<plugin>/...` (upstream
  design: `<script src>` cannot carry a token). Suffix-allowlisted, traversal-checked, and an unknown profile answers 404 like an
  unknown plugin, so there is no profile-name oracle; but anyone who can reach the gateway URL can read those static assets.
- **Do not install the `advanced-delegation` catalog plugin on a company gateway** (`plugin-catalog/advanced-delegation.yaml`). Its
  `profile_routes` backend runs subagents in another profile's scope, which no grant check covers. It is not in the tree, and
  installing agent plugins is admin-only, so the hole is closed only while nobody installs it. Pinning `delegation.backend` is the
  fix if it is ever needed (O10).
- Token-mode WS leg now carries issuer and groups. Sessions owned under the old issuer-less subject are not
  visible to that user afterwards. Irrelevant for a fresh deployment; matters only when migrating one.

### D10. Release and housekeeping
- Version `0.0.0` to `0.1.0` in `pyproject.toml`, `uv.lock` and `apps/desktop/package.json`. `uv.lock` had been left
  at `0.0.0`, which made `python scripts/run_tests.sh`'s environment bootstrap fail (`uv lock --check`); fixed with `uv lock`.
- README: rewritten for a business audience (what Sentio One is, why a company would use it, how it works, honest status);
  upstream's README is gone from `README.md` and `.gitattributes` marks it `merge=ours`. Run `git config merge.ours.driver true` once per clone
  so upstream merges keep ours without a conflict. The translated `README.es.md`, `README.zh-CN.md`, `README.ur-pk.md` still describe upstream
  Hermes and are not updated. (Earlier: fork section above upstream's README.) `architecture/` (diagrams) and `.plugin-skills/` (plugin builder
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
  workflows added by a merge arrive enabled. A workflow is upstream's when its file exists in `upstream/main`, so every fork-owned
  workflow (`fork-ci.yml`, `release-light.yml`, `tag-release.yml`, `protection-drift.yml`, anything added later) is kept without a list.
- **`Release Light Desktop`** (`release-light.yml`): every push to `main` (except docs-only) builds the MSIX and keeps it as an
  artifact named after the commit for 30 days; a `v*` tag also publishes it as a GitHub release, from a separate job. The build job
  is read-only; only the publish job holds a write token. Actions pinned to the SHAs upstream uses. The installer is unsigned.
- **`Tag release`** (`tag-release.yml`, L5): on a merge that changes `apps/desktop/package.json` or `pyproject.toml`, tags the version
  if it has no tag. Pushed with the owner's fine-grained `RELEASE_TOKEN`, because the built-in token can neither start the release
  workflow nor get past the `release-tags` ruleset.
- **Remotes:** `origin` = GitHub, `upstream` = NousResearch/hermes-agent with push set to a dead URL.
- **Deploy: Dokploy**, one Compose project per company (D1, D7).
- **Model providers:** Command Code (`COMMANDCODE_API_KEY`, upstream `plugins/model-providers/commandcode`, now in the compose
  file) and Codex, **one subscription per company**, logged in by the company's admin with `hermes auth` inside the container
  (OAuth, stored with the profile on the `hermes-data` volume; not an env key).

### D12. Sandbox: non-main profiles run tools in Docker-in-Docker (built and enforced in code; Dokploy throwaway deploy remains)
Resolves A6 and O1 in code. Built:
- `deploy/dokploy/docker-compose.yml` has a `dockerd` service (`docker:27.5.1-dind`, pinned, privileged, no domain, own `dockerd-data`
  volume). It listens on **no tcp port**, only on a unix socket (`/var/run/dind/docker.sock`, group = the gateway user's gid) in
  a `dockerd-sock` volume shared with `hermes`, which gets `DOCKER_HOST=unix://...` and waits for it to be healthy.
  *Correction (2026-10-23):* the first version listened on tcp 2375. Verified in real Docker that a sandboxed container reached
  that API at the bridge gateway (`172.17.0.1:2375/version`), and the API of a privileged daemon is root on the host, so that
  version was an escape route. Fixed by the socket-only design; `scripts/fork/verify-dind-isolation.sh` (run by `Fork CI`) fails on
  the old design and passes on the new one.
  **No host `docker.sock` anywhere**: it would be root on the Dokploy host and reach other companies' containers.
  `test_agent_sandboxes_run_on_the_companys_own_docker_daemon_never_the_hosts` fails if that changes.
- `deploy/dokploy/dockerd-entrypoint.sh` installs `DOCKER-USER` and `INPUT` firewall rules inside `dockerd` that drop packets from
  sandbox bridges (`! -i eth0`) to RFC 1918 (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), link-local (`169.254.0.0/16`),
  loopback, CGNAT, the compose network, and local ports on `dockerd` itself, while keeping public internet access (`ESTABLISHED,RELATED`
  + `RETURN`). Verified by `scripts/fork/verify-dind-isolation.sh` and `tests/gateway/test_dokploy_compose.py`.
- `managed/config.yaml` pins `terminal.docker_image: nikolaik/python-nodejs:python3.11-nodejs20`, `docker_volumes: []`,
  `docker_extra_args: []`, `docker_forward_env: []`, `docker_env: {}`, `docker_mount_cwd_to_workspace: false`,
  `container_persistent: false`, `container_cpu: 1`, `container_memory: 5120`, `container_disk: 51200`.
- `tools/terminal_scope_sandbox.py` is called by `tools/terminal_scope.build_profile_terminal_scope`, `set_terminal_scope`, and
  `terminal_env`: for any non-launch profile under an `access:` policy, it applies the managed overlay and overwrites the scope to
  `TERMINAL_ENV=docker` with empty volumes, empty extra args, empty forwarded env, ephemeral tmpfs (`/workspace` and `/root`),
  pinned image, and bounded CPU/memory/disk regardless of what the profile's own `config.yaml` or `.env` requests
  (`tests/tools/test_terminal_scope_sandbox_gap.py`). Task-level overrides (`_task_env_overrides`), host bind mounts
  (`_readonly_skill_mount_args`, `credential_files`), and env passthrough (`env_passthrough`, `remote_common`) are also blocked for
  sandboxed profiles.
- Host-execution surfaces outside the terminal backend (`mcp_servers` stdio commands, `quick_commands` `type: exec`, shell hooks,
  local host browser launch / `bot_desktop.placement: gateway`, and cron `script` / `monitor_script` jobs) fail closed and log for
  sandboxed profiles (`tests/tools/test_sandbox_host_surfaces.py`).
- End-to-end proof in `scripts/fork/verify-dind-isolation.sh` + `scripts/fork/verify_dind_profile_e2e.py` (run in `Fork CI`) drives a
  real routed profile's `terminal_tool` against the live `dind` service with a hostile `config.yaml` and `.env`, asserting the command
  runs inside an unprivileged container on `dockerd`, sees no gateway secrets or `/opt/data`, cannot reach the daemon API or compose
  network, and has `CapDrop=[ALL]`, `no-new-privileges`, `Memory=5120m`, `NanoCpus=1e9`, and `PidsLimit=2048`.
- Not pinned in `managed/config.yaml` on purpose: `terminal.backend`. The main profile must keep the local backend because its agent
  provisions the others (D13), and `tools/terminal_scope_sandbox.py` forces `TERMINAL_ENV=docker` only for non-launch profiles.

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
- `hermes profile create <name>` (`hermes_cli/profiles_initialization.py`, `hermes_cli/profile_cmd.py`) stamps
  `terminal.backend: docker` into the new profile's `config.yaml` when an `access:` policy is active and prints the
  `hermes-profile-<name>` IdP group grant notice (`tests/hermes_cli/test_profile_sandbox_provisioning.py`), accompanied by the
  bundled `skills/autonomous-ai-agents/profile-provisioning/SKILL.md` skill (O3).

### D14. Sandbox follow-ups and IdP (decided 2026-10-24)
Owner decisions after the sandbox merged (details in `SANDBOX_PLAN.md` section 4 and phase 6, `ACCESS_CONTROL_PLAN.md` section 6):
- Non-main profiles keep MCP, quick commands, hooks and the browser. The interim fail-closed phase 2 becomes an admin-approved
  allowlist plus running those surfaces inside the profile's sandbox (O12). A sandbox that cripples a profile is a failed design.
- Egress: private ranges stay blocked; per-template egress later. One main profile manages the others for the MVP; admins who are
  not main run sandboxed. Image pin deferred. Limits come from profile templates named by the work they do.
- **IdP: Google first (free), Microsoft Entra second, Pocket ID if neither.** Keycloak is no longer the recommendation. There is no
  public signup: access comes only from invites and configured admins. Google has no `groups` claim, so an admin bootstrap by
  verified email was needed before a real company could use it (O13, built).
- **The operator is the first admin of every company gateway** (owner's decision 2026-10-24): `ADMIN_EMAILS` is set per company in
  Dokploy, starting with the operator's address, and the customer's admin is added on handover. This is a trust statement to
  make to customers, not a footnote: the main profile is host-level inside the gateway (D13), and the operator already holds the
  Dokploy host, so an operator admin can reach that company's data. Whether the operator's address stays on after handover is
  a per-customer agreement; removing it is a redeploy.

### D15. A routed profile uses the company's model keys only if an admin chooses (decided and built 2026-10-24)
Found on the first invited sign-in: a sandboxed profile's chat failed with "No usable credentials found for provider 'commandcode'".
Profiles are islands, so a routed profile's secret scope is its own `.env` and nothing of the launch profile's, and the main
agent cannot copy a company key because provider keys are not visible to its terminal. Copying keys into each profile was rejected
(duplicate secrets, rotation drift). Instead:
- An admin-side flag per profile, kept in the launch home (`profile_key_sharing.json`, written by `gateway/profile_key_sharing.py`),
  never in the profile's own files. `agent.secret_scope.build_profile_secret_scope` adds the company's provider API keys (names the
  config declares as category `provider`: `*_API_KEY` and `*_BASE_URL`, read from the launch process environment and launch `.env`)
  to that profile's scope; the profile's own `.env` still wins. Bot tokens, tool secrets and OAuth logins are never shared.
- `hermes profile create <name> --share-keys`, `hermes profile share-keys <name> [--off]`, and `hermes profile show` prints which
  applies. The command records the choice only; it never reads or prints a key. The profile-provisioning skill makes the main agent
  ask the admin "company keys or its own?" before creating a profile.
- Trade-off to state to customers: shared keys mean shared spend (no per-person limit or attribution), and one rotation covers all.
- Not built: the dashboard's Create profile dialog checkbox (O14).

### D16. A sandboxed company profile gets a business skill baseline, not every bundled skill (decided and built 2026-10-24)
Found on the first invited profile: `sentio-sales` arrived with 54 bundled skills (claude-code, ascii-video, songwriting, p5js,
architecture diagrams...) and, worst, `profile-provisioning`, the admin's how-to for creating profiles. Upstream seeds every
bundled skill into every profile, which suits a developer, not an employee of a customer.
- `tools/skills_sync_company.py` decides, and `tools/skills_sync.sync_skills` obeys it: for a non-launch profile under an `access:`
  policy it seeds the essentials (`hermes-agent`) plus `DEFAULT_COMPANY_BASELINE` (docx, pdf, xlsx, powerpoint,
  document-to-action-items, meeting-action-items, weekly-review-planning, grounded-citations, competitor-news-monitor). The launch
  profile keeps the full set. `profile-provisioning` is never seeded into a sandboxed profile, whatever the baseline says.
- The baseline is configurable per deployment with `skills.company_baseline` in the managed config; profile templates (O12)
  will name it per kind of work. Later `hermes update` syncs respect it, so the rest do not come back.
- `hermes profile trim-skills <name>` fixes profiles created earlier: it removes unmodified bundled skills outside the baseline and
  keeps anything the user edited or installed.
- The default baseline is a starting guess for office work, not a decision about any customer's job: tune it per company.


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
| `tools/terminal_scope.py`, `tools/terminal_tool.py`, `tools/environments/docker.py`, `tools/environments/remote_common.py`, `tools/credential_files.py`, `tools/env_passthrough.py` | calls into `tools/terminal_scope_sandbox.py` to enforce the Docker sandbox, strip task overrides, and block host bind mounts / env passthrough for non-launch profiles under an `access:` policy | Keep the `terminal_scope_sandbox` hooks. |
| `hermes_cli/managed_scope.py` | `_merge_managed_overlay` overwrites empty dict leaves (`docker_env: {}`) instead of deep-merging 0 keys | Keep `_merge_managed_overlay`. |
| `tools/mcp_tool_config.py`, `tools/mcp_tool_transport.py`, `gateway/run_inbound_hooks.py`, `agent/shell_hooks.py`, `tools/bot_desktop/placement.py`, `tools/browser_session.py`, `tools/browser_launcher.py`, `tools/browser_lightpanda.py`, `cron/jobs_invariants.py`, `cron/scheduler_script.py` | fail closed on host-level execution surfaces (`mcp_servers` stdio, `quick_commands` `type: exec`, shell hooks, host browser, cron `script`/`monitor_script`) for sandboxed profiles | Keep `is_sandboxed_profile()` checks. |
| `hermes_cli/profiles_initialization.py`, `hermes_cli/profile_cmd.py` | stamp `terminal.backend: docker` and print the `hermes-profile-<name>` IdP group grant notice when `access:` is active | Keep `_stamp_sandbox_backend_if_access_policy` and `_print_access_policy_grant_notice`. |

Fork-only new files (no conflict possible, but they depend on upstream APIs): `gateway/access_policy.py`,
`access_grants.py`, `session_access.py`; `hermes_cli/dashboard_auth/access.py`, `access_http.py`;
`hermes_cli/web_routers/access_invites.py`; `tools/terminal_scope_sandbox.py`; `skills/autonomous-ai-agents/profile-provisioning/SKILL.md`;
`web/src/pages/AccessPage.tsx`, `lib/api-access.ts`, `lib/access-nav.ts`,
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
     tests/hermes_cli/test_auth_me_access.py tests/hermes_cli/test_profile_sandbox_provisioning.py \
     tests/plugins/dashboard_auth/test_session_groups.py \
     tests/scripts/test_check_github_protection.py \
     tests/tools/test_terminal_scope_sandbox_gap.py tests/tools/test_sandbox_host_surfaces.py
   scripts/run_tests.sh tests/gateway tests/hermes_cli
   python scripts/check
   cd apps/desktop && npm run typecheck && npx vitest run
   cd web && npx vitest run
   ```
   Baseline on 2026-10-23 (Linux): the 12 Python files above pass (174 tests), web 31, desktop UI 59, electron 13, both
   typechecks and `python scripts/check` (12 checks) are clean. Failures seen earlier on the Windows dev host
   (`TestEnvWriteDenylist`, `test_profiles.py`/`test_web_server.py`, `fcntl`) did not reproduce on Linux: treat them as host noise.
   Once per clone: `git config merge.ours.driver true` (keeps the fork's `README.md` through upstream merges, see `.gitattributes`).
9. **Merge the PR with a merge commit, never squash or rebase.** A squash drops upstream's commits from our ancestry, so the next
   `git merge upstream/main` re-conflicts on every file the fork touched. (Turn off squash and rebase merging in the repo settings.)
10. **Update this file:** new base commit, new decisions, anything resolved by hand.

## 4. Open items

- **O0. The GitHub repository is public** (`sentio-one/sentio`, checked 2026-10-23), while the README and these docs call the
  fork private. It publishes the access-control audit, the known gaps (D9), the deployment layout and the invite design.
  Decided (L3): go private after cleanup and the upstream merge; until then do not add anything more sensitive to it. At that
  point also give Dokploy a deploy key, and check the plan still enforces rulesets on a private repository (branch protection is
  already applied, O11; the business README rewrite is done).
- **O1. Make the sandbox real: IMPLEMENTED IN CODE AND VERIFIED IN DOCKER 2026-10-24** (`SANDBOX_PLAN.md` phases 0–5; throwaway-company Dokploy deploy in O5 remains before go-live).
  `tools/terminal_scope_sandbox.py` enforces `TERMINAL_ENV=docker` and all managed pins (`docker_volumes: []`, `docker_extra_args: []`,
  `docker_forward_env: []`, `docker_env: {}`, `docker_mount_cwd_to_workspace: false`, `container_persistent: false`, pinned image,
  CPU/memory/disk limits) in `build_profile_terminal_scope` for every non-launch profile under an `access:` policy, closes host-execution
  surfaces outside the backend (`tests/tools/test_sandbox_host_surfaces.py`), filters private-range egress in `dockerd-entrypoint.sh`,
  and verifies end-to-end against live `dind` in `scripts/fork/verify-dind-isolation.sh`.
- **O2. PTY child still uses the host-scoped internal credential.** Binding it to the PTY's profile server-side
  (`mint_ticket(extra=...)`) was planned (A3) and is not done.
- **O3. A "create a profile" skill/convention for the main agent: DONE 2026-10-24.** `hermes profile create <name>` stamps
  `terminal.backend: docker` and prints the `hermes-profile-<name>` IdP group grant note when `access:` is active
  (`tests/hermes_cli/test_profile_sandbox_provisioning.py`), paired with `skills/autonomous-ai-agents/profile-provisioning/SKILL.md`.
- **O4. Invite open questions:** link reuse policy (default single-use stands), who sends the email (admin copies the link
  today), JIT profile creation from a template (not built).
- **O5. Partly exercised on a live Dokploy host (2026-10-24):** the compose stack deployed on Dokploy at `one-agent.sentiolangley.com`
  behind a Cloudflare Tunnel, a real Google sign-in as admin (both containers healthy), an invite redeemed by a second Google account
  (through the browser, not the desktop deep link), and the sandbox against a real routed profile on that host: the profile's terminal
  tool ran in an unprivileged container on the company's daemon (no `/opt/data`, no company keys, no Docker socket, private and
  link-local ranges blocked, public internet reachable; `Privileged=false`, `CapDrop=[ALL]`, `no-new-privileges`, 5 GiB, 1 CPU,
  2,048 pids, read-only root, no binds; see the Hindsight update "sandbox checks on the live sentio-sales profile PASSED").
  Still not exercised: file tools, `execute_code`, MCP, quick commands and the browser against the live sandbox, the chat PTY's
  own reach (O2), an LLM-driven session in the desktop app, the desktop app's native sign-in and the invite deep link, the Light
  release workflow on a real tag, WebSocket keepalive through the tunnel, a redeploy keeping `dockerd-data`, a backup and restore.
- **O6. Merge upstream.** 3,200 commits behind with 3 known conflicts (header). Do it on a branch, then section 3.
- **O7. Strings** for the Access page and connect dialog are English only.
- **O8.** `.commandcode/taste/taste.md` is an empty tracked file; likely stray.
- **O9. How "remote-first, optional local" is delivered (L1). DECIDED 2026-10-23: option A, one remote-first installer** with an
  on-demand local install; options B and C are rejected. What is still open is the design of A (the two questions at the end of this item).
  Original analysis follows. What exists: upstream builds three desktop variants
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

- **O10. Cross-profile capabilities arriving from upstream need a grant check or a pin.** Found in merge 1: `profile_routes` delegation
  (above). Each upstream merge should grep for features that name or run in another profile (`profile_routes`, `profile_home`,
  `@{profile}`, `host_scoped`) and decide per feature. Candidate for the delegation case: pin `delegation.backend: default` in
  `managed/config.yaml`.

- **O11. Repository protection: APPLIED 2026-10-24** (found missing 2026-10-23). Both original rulesets targeted a branch named
  `protect-main`, which does not exist, so `main` was unprotected; tags and `upstream-merge` had no rules; squash and rebase merging
  were on. The owner applied `.github/rulesets/` with `scripts/fork/apply-github-protection.sh --apply --replace-legacy`; the checker
  ends `github protection: ok`, GitHub reports the rules it enforces on `main` (deletion, non-fast-forward, pull request, required
  checks) and on `upstream-merge`, and the weekly `Protection drift` workflow passes. Details and the review flow:
  `docs/private-fork/REPO_PROTECTION.md`. **Only the owner account `sentio-one` can change it** (the `TheophilusChinomona` account has
  write, not admin). Still ahead: before going private, confirm the plan enforces rulesets on a private repository. One-time per clone: `gh repo set-default sentio-one/sentio`, because
  with an `upstream` remote `gh` resolves to NousResearch/hermes-agent and a bare `gh pr create` would target upstream.

- **O12. Sandbox phase 6: allowlist, profile templates, tools running inside the sandbox** (D14). Not started. Phase 2's fail-closed
  behaviour stays until it lands.
- **O13. Admin bootstrap without a `groups` claim: BUILT 2026-10-24** (`access.admin_emails`, `Session.verified_email`,
  `ADMIN_EMAILS` required and `ADMIN_GROUP` optional in compose; `tests/gateway/test_access_admin_emails.py`). Not yet exercised
  against real Google sign-in (with O5). Open: a way to hand over or remove the operator admin without a redeploy.
- **O14. Dashboard "Create profile" with a "use the company's keys" choice** (D15): the REST create route and its dialog do not
  carry the choice yet; today it is the CLI flag (and the main agent's question). Next to the allowlist/templates work (O12).
## 5. Go-live gate (all must be true before a customer is on a gateway)

- [ ] Sandbox enforced (O1): implemented, verified in Docker in `Fork CI`, and verified for tool execution on the live pilot gateway (2026-10-24, see O5). Tick when the remaining surfaces listed in O5 are covered and the PTY gap (O2) is closed or accepted in writing.
- [x] The first admin signs in through Google on a real deploy and is admin (2026-10-24, `one-agent.sentiolangley.com` on Dokploy; the first attempt exposed the provider-name bug fixed in PR #15).
- [ ] PTY child no longer holds the host-scoped internal credential (O2).
- [ ] First real deploy on Dokploy passed the checklist in `deploy/dokploy/README.md`; `dockerd` survives a redeploy.
- [ ] An invite redeemed end to end against that gateway; WebSocket keepalive tested through the Cloudflare Tunnel.
- [ ] Upstream merged through a PR (O6) and `Fork CI` green on it.
- [ ] Repository private, branch protection on, README rewritten for the business audience (O0).
- [ ] Desktop delivery decided and built (O9); installer signing decision made.
- [ ] A backup and restore of `hermes-data` and `dockerd-data` tested.
- [x] Repository protection applied and `scripts/fork/check-github-protection.sh` green (O11), 2026-10-24.

## 6. Upstream merge log

### Merge 1: `e5eb6b36fe` -> `cb757fac59` (2026-10-23, 3,200 upstream commits, branch `upstream-merge`)

Three textual conflicts, all resolved so the fork's rules still hold:
- `gateway/session_controls.py` `_sibling_for`: upstream replaced the inline `profile_scope != 'host'` test with its `host_scoped`
  property; the fork routes by grant first. Resolved as `if self._allowed is None and not self.host_scoped:`.
- `gateway/session_identity.py`: upstream added `AUTH_SUBJECT_PREFIX`, `is_authenticated_identity`,
  `authenticated_principal_digest`; the fork extracted `dashboard_subject`. Resolved so `authenticated_subject` calls
  `dashboard_subject`, which builds `AUTH_SUBJECT_PREFIX + json.dumps([provider, issuer, sub])`: **the string is byte-identical**, so
  existing grants (keyed on it) are not orphaned.
- `gateway/session_group_controls.py` `_profiles`: upstream now appends every profile under the root to the roster (including
  ones nothing serves) and returns `install_id`. The grant filter is placed **after** that discovery. Putting it before would have
  leaked unserved profiles to non-admins. Guard: `test_a_profile_on_disk_that_nothing_serves_stays_off_a_granted_roster`
  (red without the filter, green with it).

Reviewed because they auto-merged in security-relevant files, and found sound:
- `web_server_chat.py`: upstream's native-ticket WS identity (`provider: "local"`) gets no grant under a configured policy (not in
  `group_providers`, no static row), so it fails closed.
- `ws_legacy_fallback.py`: upstream dropped its `browser.manage` process-global refusal; the fork's per-verb grant check still runs
  on every non-handshake verb, so a granted user cannot reach another profile's browser.
- New `host_scoped` call sites in group controls only ever restrict a non-admin further.
- New REST routes: `/api/providers/oauth/nous/handoff` is profile-class (takes `profile`) and correctly gated;
  `/api/delegation/*` is now **explicitly admin-only** in `access_http.py` (it lists every profile and can configure cross-profile
  routing), with a test; `/dashboard-plugins/@{profile}/...` is public by upstream design (D9).
- New canonical verbs (`session.export`, `clarify.lock`, billing, free tier) go through the authority/legacy grant checks.
- `scripts/gen_gateway_contracts.py` regenerates byte-identical output, so `allowed_profiles` and upstream's contract changes agree.
- Upstream bumped the PM-pinned Python (3.14.7 to 3.14.9) and the toolchain lock; nothing fork-specific depends on it.
- New upstream workflows to disable on GitHub after this lands: `catalog-tests.yml`, `windows-desktop-attach-e2e.yml`
  (`scripts/fork/disable-upstream-workflows.sh --apply`).

Verification of merge 1 (2026-10-23, Linux):
- `python scripts/check`: 12 checks ok (health 0 blocking; the case-collisions check fails while conflicts are unstaged, because git lists
  each unmerged path three times: stage the resolutions first).
- Fork tests: 12 Python files, 177 tests pass (3 new guards, each shown red without its fix). Web 480/480 (68 files), desktop UI 59 and
  Electron 13 for the fork's files, both typechecks clean, regenerated gateway contract identical.
- Broad run (`tests/gateway tests/hermes_cli tests/tui_gateway tests/plugins/dashboard_auth`): 3,693 files, 32,627 passed, 478 skipped,
  23 files with failures, all accounted for by running the same files on pure upstream `cb757fac59`:
  - 21 files fail with **identical counts on pure upstream** (frozen-provider-key tests, and the plugin install/update/enable tests,
    which error in setup on this host; most likely the machine has no git identity and no `~/.gitconfig`). Not caused by the merge.
  - `test_session_mutation_compress_managed.py`: 150-200 s test timing out under 8-way load; passes alone.
  - `test_update_finish.py` (2 tests): run a real TUI build when the checkout has `node_modules`; they skip on upstream's tree and on ours
    with `node_modules` removed (14 passed, 2 skipped, identical). They failed here only because of a partial `npm ci --ignore-scripts`.
- Test debris: a test in the broad run creates an untracked `owned/` directory in the repo root; delete it before committing.
