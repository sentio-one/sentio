# Invite links: admin invites a user to a profile (design, not yet built)

Status: design for review. Builds on `ACCESS_CONTROL_PLAN.md` (phases 0-3 done). Nothing here is implemented.

## Goal

A company admin invites a person to one profile with a link. The person clicks it, the Light desktop
app opens pointed at that company's gateway, they sign in with the company SSO, and they land with
access to exactly that profile. One Light installer serves every company: the gateway URL arrives in
the link, never in the build.

```
hermes://connect?url=https://gateway.acme.example&invite=<token>
```

## Principles

1. **An invite grants a profile; it never authenticates.** The user still signs in through the
   company IdP. The invite binds their verified `(provider, issuer, sub)` to a profile grant.
2. **The link fills the form, it never connects on its own.** The app shows the gateway address and
   asks the user to confirm. A forged link can only point at a server the user can read and chose to
   sign in to.
3. **The server decides.** Scope, expiry and use limits live in the gateway's store, not in the token.
4. **Fail closed, no oracle.** A bad, expired, used or revoked token returns one generic refusal.

## Pieces

### 1. Grant store (gateway)
New `gateway/access_grants.py`: a small SQLite table in the launch profile home
(`access_grants.db`, written through the existing atomic/locked patterns), holding:

- `invites(id, token_hash, profile, created_by, created_at, expires_at, max_uses, uses, email_hint, revoked_at)`
- `grants(subject, profile, source, invite_id, granted_at, revoked_at)`

`AccessPolicy.grant_for` gains the store as a third source next to IdP groups and static config:
`profiles = groups ∪ config ∪ store`. Admin stays config/group driven (an invite can never create an admin).
Tokens: 32 random bytes, only the SHA-256 is stored, compared in constant time.

### 2. Admin API (gateway REST)
All admin-only, behind the role gate from phase 4:

- `POST /api/access/invites` `{profile, ttl_hours?, max_uses?, email?}` returns `{invite_id, link}` once.
- `GET /api/access/invites` lists with status (never returns the token).
- `DELETE /api/access/invites/{id}` revokes, and with `?revoke_grants=true` also revokes grants it created.
- `GET/DELETE /api/access/grants` to see and revoke per-user grants.

`POST /api/access/invites/redeem {token}` is the one non-admin route: any authenticated session, rate
limited per subject and per IP, atomic "check, count a use, write grant" in one transaction.

Defaults: single use, 7 days. Multi-use "team links" only when the admin sets `max_uses > 1`.
If `email` is set, the verified ID-token email (`email_verified` true) must match.

### 3. Desktop (Light and full)
- Handle `hermes://connect?url=&invite=` in `main.ts` next to the existing deep-link routes.
  Accept only `https` (plain `http` for loopback), no credentials in the URL, token length bounded.
- Show a confirmation: the gateway host, large, with the inviter's company shown only if the gateway reports it.
- Open the remote form with the URL filled, run the existing probe and OAuth sign-in, then call redeem once
  signed in. The token passes through the main process only; never logged, never in renderer storage,
  dropped after redeem or failure.
- If the app already has a connection, "Connect to a new gateway" is added alongside, never replaced.
- Cold start: the link arrives in argv (Win/Linux) or `open-url` (mac); the existing deep-link queue handles it.

### 4. Provisioning (optional, later)
An invite may carry `create_profile_from: <template>` so the first redeem creates `profiles/<name>`
from a template (the JIT idea in the main plan). Off by default; separate from the grant logic.

## Order of work (and why)

1. **Phase 4 first: REST role gate + filtered roster + `runtime.describe`.** Today any authenticated user
   can reach host-level REST routes (`/api/files`, git, plugin install, update). An invited non-admin must
   not, so this blocks everything below.
2. **Grant store + policy merge + admin API + redeem** (backend, fully testable without the desktop).
3. **Desktop deep link + confirm + redeem.**
4. **Docs and CI build** (Light installer on a Windows runner).

## Open decisions (need an owner)

- **Tenancy:** one gateway per company (recommended: matches the current single-launch-profile policy
  design) or one shared multi-tenant gateway (would need a tenant dimension in every check).
- **Link reuse:** keep "single use by default"; allow team links only by explicit admin choice. Agreed?
- **Who sends the email:** the admin copies the link (simplest, no mail dependency), or the gateway sends
  mail through the company's SMTP (more setup).
- **Sandbox (A6)** is still unmade and bounds how much a granted user can do on the host. Invites make
  onboarding easy, so this should be decided before real customers are invited.

## Threats considered

- **Leaked link:** single use plus expiry plus optional email binding; revocation; no token in logs.
- **Forged link to a hostile gateway:** visible host confirmation; the user still signs in to a gateway they read.
- **Token guessing:** 256-bit tokens, hashed at rest, rate-limited redeem, generic errors.
- **Invite privilege escalation:** invites grant only named profiles, never admin; admin only via config/groups.
- **Replay after revocation:** grants are re-read at each WebSocket connect (A8); open sockets live until they close.
