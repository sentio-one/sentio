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
- IdP choice and claim shape (section 6).
- Whether non-admins may edit their profile's config (`terminal.backend`, plugins).

## 6. IdP

Recommended: Keycloak. Fallback for a small team: Authentik. If the company already has Entra/Okta/Google,
use it directly and only add a broker if it cannot emit a groups claim. Requirements to verify against
`plugins/dashboard_auth/self_hosted`: authorization code + PKCE, public client, discovery + JWKS,
`groups` array in the ID token (not only userinfo), stable `sub`.

## 7. Private fork workflow

- `origin` = private GitLab. `upstream` = NousResearch/hermes-agent, push disabled.
- `main` mirrors upstream. All work lives on `private/main` (or `private/access-control`).
- Update: `git fetch upstream && git checkout main && git merge --ff-only upstream/main`, then
  `git checkout private/main && git merge main` (merge, not rebase, so published history is stable).
- Keep the diff small and in new files where possible (`gateway/access_policy.py`) to ease merges.
- Never run `gh pr create` against upstream. Disable the pre-push hook's upstream targets.
