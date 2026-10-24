# Per-user, per-profile access control: private fork plan (v2, audited)

Private fork only. Never PR upstream. Upstream is read-only, used to pull updates.

## 1. Goal

One central company gateway. Desktop clients connect remotely, sign in with company SSO (OIDC).
Each user gets a set of profiles. Admins get all profiles. Users cannot see or act on others.

## 2. Corrections from the audit of v1

| # | Finding | Required change |
|---|---|---|
| A1 | `_sibling_for` returns early for the connection's own home (`session_controls.py:113`) before any scope check, and `_PROFILE_IMPLICIT` verbs carry no `profile`. Every user can use the launch/default profile. | Check the allowlist when `AuthorityConnection` is built (empty capabilities if the primary profile is not allowed) and define how profile-less verbs pick a target authority. |
| A2 | "`allowed_profiles` absent => host scope" is fail-open. Internal credential, legacy token and any unstamped path get host scope. | Deny by default. Local native owner becomes an explicit marker (`local_owner`). Internal credential gets a narrow, explicit grant. |
| A3 | `/api/pty`, `/api/audio` x2 and `chat_ws.py:307` read `?profile=` unauthenticated. PTY child reconnects with host-scoped internal credential. | Add all WebSocket routes to the enforcement list. Bind the PTY child's profile server-side, via `mint_ticket(extra=...)`. |
| A4 | REST requests with no `?profile=` fall back to the current/launch profile. | Absent profile => the caller's home profile, or deny. |
| A5 | Non-profile REST routes (fs, cron, gateway, plugins, config, env) are open to any authenticated user. | Role-gate host-level routes to admin. Per-profile routes check the allowlist. |
| A6 | Profiles are not an OS boundary (SECURITY.md 2.2). Agent terminal/file tools can read sibling `.env`/`auth.json`. Users can edit their own `terminal.backend`. | Pick a sandbox posture (section 5) before shipping. |
| A7 | Subject key `issuer_host|user_id` collides across tenants on shared hosts (Entra). | Key on `(provider, full issuer, sub)`. Confirm whether `Session.user_id` is `sub` or email. |
| A8 | Groups resolved at ticket mint live for the socket's life. | Cap socket lifetime or re-check on a timer. Document the revocation latency. |
| A9 | `operator=False` for non-admins is untested. | Inventory what `session:operator` gates before flipping it. |

## 3. Design

Unchanged from v1 except the corrections above: claim-driven policy in `gateway/access_policy.py`,
`access:` config root, groups carried on `Session`, resolved at ticket mint, enforced at
`AuthorityConnection` construction, `_sibling_for`, REST middleware, WS routes, roster
(`profiles.list`, `GET /api/profiles`), `runtime.describe`, and the legacy `tui_gateway` plane
(not yet read; verify in phase 3).

## 4. Phases

0. Attack-surface inventory (every `profile` reader, every WS route, every host-level REST route,
   internal credential users). Output: a table in this file.
1. Policy + config + `Session.groups` + subject key.
2. Gateway enforcement: A1, A2, `Principal.allowed_profiles`, `_log_refusal` audit.
3. WS routes (A3) and legacy `tui_gateway` plane.
4. REST (A4, A5) + roster + `runtime.describe`.
5. Sandbox posture (A6) + provisioning (JIT from template, default off).
6. Docs (`multiplexing-gateway.md:322-328`, FAQ), manual E2E with a test IdP.

Tests throughout, red on base, via `scripts/run_tests.sh`. Add negative tests:
non-admin `session.create` cannot reach `default`; `/api/pty?profile=other` refused; no-profile REST
call does not read default; two tenants on one issuer host stay distinct.

## 5. Open decisions

- Sandbox posture: container terminal backend per user, or accept "grant = host code execution".
- IdP choice: decided 2026-10-24, Google first (section 6). The claim shape follows from it: no `groups` claim, so grants come from invites.
- Whether non-admins may edit their profile's config (`terminal.backend`, plugins).

## 6. IdP

**Decided 2026-10-24: Google sign-in is the first IdP** (Microsoft Entra second). Superseded: the earlier recommendation of
Keycloak, with Authentik as the fallback, which is more to run than an invite-only product with no public signup needs.

Why this works: the gateway owns who may do what. An invite binds a verified `(provider, issuer, sub)` to a profile
(`INVITES_DESIGN.md`), so the IdP only has to prove who someone is. "Invite only, no public signup" falls out of that: a
Google account that holds no invite and no admin entry gets no grant.

Cost: nothing. A Google OAuth client has no per-user or per-login charge and needs no billing account. Setup is in
`deploy/dokploy/README.md`.

What changes because Google's ID token has **no `groups` claim**:
- Per-profile grants come from invites (the store), never from `hermes-profile-<name>` groups. The group path stays for
  companies that bring an IdP that emits groups.
- **Admins are named by verified email (built 2026-10-24, D14/O13).** A Google `sub` is a numeric id nobody knows before a first
  sign-in, so `access.admins` rows are impractical for the first admin. `access.admin_emails` (pinned in the managed config from
  `ADMIN_EMAILS`, comma separated, case-insensitive) makes a person admin when the ID token's `email_verified` is exactly `true`
  and the provider is one in `group_providers`. `Session.verified_email` carries it, and it rides the WebSocket ticket like
  `groups`, so REST, WebSocket and the `/api/auth/me` summary all agree. `ADMIN_GROUP` is optional; `ADMIN_EMAILS` is required.
  The profile's own config cannot add an admin email (managed pin). Residual risk: a reassigned address keeps the role.
- Because the email is what an admin types into an invite, set `email` on every invite so the verified ID-token email must
  match (`INVITES_DESIGN.md`).

Fallbacks: Microsoft Entra (also free, multi-tenant app registration, the same no-groups caveat unless the company configures
a groups claim), then Pocket ID (one small self-hosted container, passkey-only) for a company with neither. Zitadel or WorkOS
AuthKit only when a customer needs SSO groups or SCIM. Clerk is ruled out by the owner.

Requirements any IdP must meet, verified against `plugins/dashboard_auth/self_hosted`: authorization code + PKCE, public
client, discovery + JWKS, stable `sub`; a `groups` array in the ID token (not only userinfo) only if group grants are used.

## 7. Private fork workflow

- (Superseded: hosting moved to GitHub, see `FORK_DECISIONS.md` D11.) `origin` = the fork, `upstream` = NousResearch/hermes-agent, push disabled.
- `main` mirrors upstream. All work lives on `private/main` (or `private/access-control`).
- Update: `git fetch upstream && git checkout main && git merge --ff-only upstream/main`, then
  `git checkout private/main && git merge main` (merge, not rebase, so published history is stable).
- Keep the diff small and in new files where possible (`gateway/access_policy.py`) to ease merges.
- Never run `gh pr create` against upstream. Disable the pre-push hook's upstream targets.

## 8. Phase 0 results: attack-surface inventory

Evidence: `docs/private-fork/phase0-routes.txt` (297 route handlers from an AST scan of
`hermes_cli/web_routers/*`, `web_server*.py`, `dashboard_auth/routes.py`). Method limits: "takes a
profile param" is a syntactic signal, not proof of correct scoping. Nothing here was run.

### 8.1 Gate model (verified)
`dashboard_auth/middleware.py` is a single authenticated-or-not gate over every `/api/*` path; only
`_GATE_PUBLIC_PREFIXES` bypass it. There is no per-route role, group or profile check. So any
authenticated user reaches every route below unless this plan adds a check.

### 8.2 WebSocket routes
| Route | Reads `?profile=` | Identity today | Gap |
|---|---|---|---|
| `/api/ws` | via RPC params | stamped `profile_scope: host`, `operator=True` for all | the core change (changes 8, 9) |
| `/api/pty` | yes, unauthenticated | gate only; spawns a TUI child as the server OS user | any user can launch an agent in any profile; child reconnects with internal credential |
| `/api/console` | yes | gate only | same class as `/api/pty` (not read in detail) |
| `/api/pub`, `/api/events` | no (channel id) | channel-keyed fan-out of the child's events | unverified: how secret the channel id is; events may carry another user's tool output |
| `/api/display/ws` | no | gate only | unverified |
| `/api/audio/transcribe-stream`, `/speak-stream` | yes | gate only | profile selects provider credentials, so a user can spend another profile's keys |
| kanban plugin `/events` | no | plugin route | unverified |

### 8.3 REST routes with no profile parameter (91 handlers, host-level)
These need an admin role gate, not an allowlist:
- `files.py` (10): list, read, download, stream, upload, mkdir, delete on the host filesystem.
- `git.py` (21): status, branch switch, commit, push, create-PR, worktrees.
- `dashboard_ui.py` (6): agent-plugin install, enable, disable, update, delete. Installing a plugin is host code execution.
- `actions.py`: `POST /api/hermes/update`, `/api/gateway/drain`, `/api/gateway/migrate`.
- `local_models.py` (13): model download, server start, sideload.
- `messaging.py`: Telegram and WhatsApp onboarding.
- `status.py`: `/api/host/identity`, `/api/ssh/ownership`, `/api/system/stats`, health.
- `profiles.py` mutations: `POST /api/profiles`, `DELETE`, `PATCH`, `/import`, `/export`, `/open-terminal`, `/active`.
- `/api/auth/ws-ticket` is the mint point (change 6).

### 8.4 REST routes that take a profile (171 explicit, 35 indirect)
By file (total, explicit param, indirect): config_env 15/12/0, cron 13/12/1, sessions 18/13/5,
skills 13/13/0, tools 12/12/0, mcp 12/9/0, models 7/7/0, oauth 6/6/0, ops 28/26/2, status 20/14/0,
analytics 4/4/0, messaging 11/5/1, memory_providers 3/3/0, audio 10/8/2, profiles 20/3/17.
Each needs an allowlist check after resolving the effective profile. Absent profile resolves to the
launch or active profile (finding A4), so resolution must happen before the check.
High risk inside this set: `config_env` (reads and writes `.env`, config), `oauth` (provider tokens),
`cron` (scheduled agent execution), `mcp` (spawns processes), `tools`, `skills`.
Note `files.py` has 8 handlers with a profile argument and 10 without, so the host-wide filesystem
routes are reachable both ways.

### 8.5 Legacy RPC plane (verified, changes the plan)
`tui_gateway/ws.py::_dispatch_request` falls back to the legacy `server.dispatch` for any method
the authority answers `-32601` to. The fallback requires only the interactive capability set
(`legacy_fallback_allowed`). It then honours an explicit `profile` param through
`server._profile_home(explicit)`, with no allowlist (`ws_legacy_fallback.py::_ticket_profile_request`).
Reachable this way: `profiles.create`, `profiles.configure`, `profiles.set_asset`, `profiles.list`,
`config.get/set`, connectors, and other sidecar verbs. So change 9 alone does not close the hole;
change 14 is mandatory and must sit in `_profile_home` (the single resolver) plus a deny-list for
host-level legacy verbs (`profiles.create`, `reload.*`, `config.set` on host keys).

### 8.6 Internal credential
`consume_internal_credential` returns `{user_id: server-internal, provider: server-internal}` with no
profile or capability limits. It is minted once per process, never expires, and is handed to the PTY
child via env (`web_server_chat.py:459-461`). Used by `/api/ws` and `/api/pub` from the child. Any
design must give this identity an explicit, narrow grant bound to the PTY's profile (server-side),
not host scope.

### 8.7 What `session:operator` does today (verified)
- `session_authority.py:138`: lets the holder act on sessions owned by other subjects.
- `session_group_controls.py:300-306`: removes the owner filter on the Bot Chat roster, so the holder sees every user's sessions.
- `session_bot.py:653`, `session_prune.py:28`: bot-chat delivery and prune.
So today every dashboard user is effectively an operator and can read other users' sessions inside a
profile. Setting `operator = is_admin` is therefore required for per-user session privacy, and the
Desktop's behaviour for non-operators needs a test pass (item A9).

### 8.8 Decisions this forces
1. Role gate for the 91 host-level handlers: a single `require_admin` dependency applied by route
   table, not per handler, so a new route defaults to admin-only.
2. Resolve the effective profile once in middleware (query, body, path, absent) and attach it to
   the request before the allowlist check.
3. Put the legacy-plane check in `_profile_home`, and treat `profiles.create` and similar as admin verbs.
4. Decide whether `/api/pty` and `/api/console` stay at all for non-admins; they run an agent as the
   server OS user, so they are the sandbox decision (A6) in route form.
5. Verify the three "unverified" WebSocket rows before phase 3.

## 9. Phase 1 status (policy, config, groups, subject key): done

Added `gateway/access_policy.py` (`parse_access_policy`, `AccessPolicy.grant_for`, `Grant.allows`),
`GatewayConfig.access`, the `access` bridge and root key, `Session.groups`, `groups_from_claims`, and
`session_identity.dashboard_subject` (the one subject string both session ownership and the policy use).
Tests: `tests/gateway/test_access_policy.py` (25), `tests/plugins/dashboard_auth/test_session_groups.py` (10).

Deviations from section 3, with reasons:
- **`users`/`admins` are lists of `{provider, issuer, sub}` rows**, not `host|sub` keys. The key is the
  full `(provider, issuer, sub)` triple (finding A7). `Session.user_id` is the `sub` claim, confirmed.
- **New `group_providers` (default `self_hosted`).** Group grants count only for these providers, so
  another provider (e.g. `nous`) cannot mint a grant by emitting a matching group name.
- **`org_id` folding of groups is kept** (existing test asserts it). Authorization reads
  `Session.groups` only. Only the `groups` claim is read; `roles` is not (different namespace).
- **`access` is not in `DEFAULT_CONFIG`** (it is in `_EXTRA_KNOWN_ROOT_KEYS`, like `profile_routes`);
  adding it grew `config_defaults.py` past its ratchet. **Not added to `GatewayConfig.to_dict`**: nothing
  needs to persist it, and the loader re-reads YAML.
- Empty `profile_group_prefix` turns group-to-profile grants off rather than mapping every group.

Verification: new tests green; related suites green (503 passed); `scripts/check --only health` ok. Two
`TestEnvWriteDenylist` near-miss cases fail identically on base (Windows case-insensitive env names).
`scripts/check` blob-size flags the untracked `architecture/*.html`, which is not part of this commit.
"red on base" here means import failure (the modules did not exist), not a behavioural red.

## 10. Phase 2 status (gateway enforcement): done, regression run noted below

What changed:
- `gateway/session_access.py`: `allowed_set`, `require_granted`. A denial raises `profile_mismatch`
  (the reason an unserved profile gets, so a denial reveals nothing about what exists) and logs the
  subject and requested selector at WARNING.
- `Principal.allowed_profiles` (None = not grant-scoped).
- `AuthorityConnection`: `_sibling_for` now checks the grant for the own-home match too (finding A1)
  and routes by grant when the identity carries `allowed_profiles`; `dispatch` checks the own authority
  for every verb except `ping`, so profile-less verbs (`session.create`, `prompt.submit`, ...) cannot
  run on the launch profile for a caller who was not granted it.
- `hermes_cli/dashboard_auth/access.py::scope_for_identity`: replaces the hard-coded
  `profile_scope: host` + `operator=True` in `/api/ws`. No policy => unchanged. Policy configured =>
  admin keeps host scope and operator; others get `allowed_profiles` (empty for an unknown user) and no
  operator; caller-supplied scope fields are dropped (A2). The internal credential and the launch-time
  session token are explicit trusted identities (host scope) until phase 3 binds the PTY child to a profile.
- Groups now flow: ticket mint (`extra.groups`) -> `_stamp_identity` -> `scope_for_identity`.
- The `?token=` WebSocket leg (token-mode Remote desktop) previously stamped identity WITHOUT issuer,
  so the same user got a different owner subject than via the ticket leg. It now carries issuer and groups.
  Consequence for an existing install: token-mode sessions owned under the issuer-less subject are no
  longer visible to that user. Irrelevant for a fresh private deployment; flag it if migrating one.

Known gaps left for later phases (so they are not mistaken for done):
- `profiles.list`, `runtime.describe` and the group-control roster are refused for a caller whose own
  (launch) profile is not granted, instead of being filtered. The desktop therefore cannot list
  profiles for a non-admin yet. Phase 4 adds the filtered roster and the `describe` fields (which must
  be declared in `tui_gateway/contracts/`).
- Legacy RPC fallback (`ws_legacy_fallback`) is not yet closed: a non-admin socket can still reach
  sidecar verbs with an explicit `profile`. Phase 3.
- REST, `/api/pty`, `/api/console`, `/api/audio/*`: phase 3/4.
- Grants are resolved when the socket opens; a revoked group applies to the next connection (A8).

## 11. Phase 3 status (legacy RPC plane, host verbs, WebSocket profile routes): done

- `tui_gateway/ws_legacy_fallback.py`: a grant-scoped actor's verb is refused (4064, the code an unknown
  profile gets) unless the profile it will act on is granted: explicit `profile`, else the named
  session's home, else the connection's home, else the launch profile. `ADMIN_ONLY_METHODS`
  (`profiles.create`, `display.install`, `diagnostics.share_nous`, `machine.facts`, `system.battery`,
  `wake.*`) and `session.active_list` are unreachable for grant-scoped actors. Handshake verbs stay open.
  The selection of those verbs is a judgement from their names; revisit if the desktop needs one.
- `server._profile_home` is wrapped by `grant_checked`: under a bound grant (contextvar set by
  `dispatch_legacy`) it refuses an ungranted home, the launch profile included. Backstop for handlers
  that resolve a profile themselves.
- `/api/pty`, `/api/console` (via `_ws_gate`) and `/api/audio/*` refuse an ungranted `?profile=` with
  close 4403 reason "profile not granted", before accept. An unstamped identity on a gated host gets an
  empty grant. A grant-scoped PTY ignores `?cwd=`.
- Tests: `test_legacy_fallback_access.py` (9), `test_dashboard_ws_profile_gate.py` (10, includes the real
  routes through the app; an earlier version passed for the wrong reason, the host/origin check shares
  close code 4403, so the test now asserts the close reason and has a granted-profile control).

Verification: new tests green; health ratchet clean. Broad run (324 files): 60 failures in 32 files; 28
fail identically on the base commit (Windows: no `fcntl`, path separators, slow peer processes); the other
4 pass when run alone (load flakes). No regression attributable to this change.

Residual, not fixed: `/api/pub` and `/api/events` are keyed by a client-chosen channel id (a random UUID
in the shipped clients), so they are a capability secret, not an authorization check. A granted user's PTY
still runs as the server OS user (decision A6). REST routes and the roster are phase 4.

## 12. Phase 4 status (REST role gate, roster, describe): done, regression run noted below

- `hermes_cli/dashboard_auth/access_http.py`: `classify(method, path)` returns `open | profile | admin`,
  default-deny. `enforce()` runs from the gate (`middleware._authorized_call`) on all three session paths
  (cookie, bearer, refreshed) once the session is verified, only when an access policy is configured.
  - `admin`: `/api/fs, files, media, git, gateway, hermes, ops, local-models, logs, actions, host, system,
    ssh`, agent-plugin install/enable/update/delete, provider `validate` (SSRF), bot onboarding, profile
    create/delete/rename/import/export/open-terminal/active-switch, and any route nobody classified.
  - `profile`: config, env, sessions, skills, tools, mcp, model, memory, cron, analytics, credentials,
    providers, chat, status, webhooks, messaging platforms, dashboard settings, audio, pairing, and
    `/api/profiles/<name>/{soul,description,model,desktop-overlay,setup-command,describe-auto}`.
    Every selector the request carries (`?profile=` repeated, `?recents_profile=`, JSON body `profile`,
    the `/api/profiles/<name>` segment) must be granted. An absent selector means the launch profile and
    needs it granted. `all` is never accepted. `/api/sessions`, `/api/cron/jobs` and `/api/profiles/*`
    treat an absent or `current` selector as "everything", so a grant-scoped caller must name a profile
    (mirrors `native_http._check_profile_scope`); the cross-profile aggregates need every selector explicit.
  - `open`: `/api/auth/*`, `GET /api/health`, `GET /api/config/defaults`, `GET /api/profiles`.
- `GET /api/profiles` and the RPC rosters (`profiles.list` canonical + legacy) return only granted profiles.
  `profiles.list` and `runtime.describe` no longer need the launch profile to be granted (finding from
  phase 2). `runtime.describe` carries `allowed_profiles` for a grant-scoped caller (absent otherwise;
  declared in `tui_gateway/contracts`, TypeScript regenerated).
- A contract test walks the REAL app routes: every profile-class handler reads its profile only where the
  gate looks, so a new upstream route that reads it elsewhere fails the test instead of slipping past.

Known limits (not fixed):
- A grant is access to the whole profile on REST: the REST session routes are not filtered per owner, so two
  users sharing one profile see each other's sessions there (the gateway WebSocket plane does isolate by
  subject). One profile per person avoids it.
- `multipart` / `urlencoded` bodies are not inspected for a `profile` field; only the query and JSON are.
- Service tokens and native tickets are trusted callers and are not subject to this gate.
- `runtime.describe` still returns the launch authority's `profile_id` (a home path) to a caller who is not
  granted it.
- Non-admin desktops lose the host file browser, git and update panels (admin-only by design).
- Upstream merges: a new `/api/*` route is admin-only until classified. Keep the contract test green.

Also in this change: the Light desktop artifact no longer carries a company gateway (one installer serves
every customer); the first-run choice card moved to its own component and the local-install guard moved
into the first-run gate, to stay within the repo's size ratchets.

Verification (phase 4): new tests green (55 across six files); health ratchet clean. Broader run over 38 existing
files: 620 passed, 11 failed in `test_profiles.py` and `test_web_server.py`; the same 11 fail identically on the
base commit (Windows host), so none is attributable to this change.
