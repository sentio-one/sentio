# Per-company gateway on Dokploy

One Dokploy **Compose** project per company, all from this repository and branch, each with its own Environment
values, volume and hostname. Dokploy rebuilds and redeploys when the selected branch changes.

**Status: written and unit-tested, never run in Docker.** There is no Docker on the machine this was written on, so
the image build, the container start and the healthcheck inside it have not been exercised. The tests in
`tests/gateway/test_dokploy_compose.py` and `tests/gateway/test_access_managed_config.py` cover the compose file's
safety properties and the policy loading through the real loaders. Do the first deploy on a throwaway company and
read the checklist at the end before putting a customer on it.

## What the pieces do

| File | Role |
|---|---|
| `docker-compose.yml` | two services: `hermes` (gateway + web gateway, nothing published on the host) and `dockerd` (the company's own Docker-in-Docker daemon, the sandbox for agent terminals) |
| `managed/config.yaml` | the **pinned access policy**, mounted read-only at `/etc/hermes`; wins over the profile's own config |
| `healthcheck.py` | marks the container unhealthy if the port is closed **or the access policy is not active** |

Why pinned and unhealthy-on-missing: an invalid or absent managed file silently turns the access policy **off**
(upstream fails open, and then every signed-in user is an admin). The healthcheck turns that into "Traefik stops
routing to this container". The pinned file also stops a granted user, or an agent editing `/opt/data/config.yaml`,
from adding themselves as an admin.

## Environment (Dokploy → project → Environment), per company

| Variable | Required | Example / meaning |
|---|---|---|
| `COMPANY_SLUG` | no | `acme`, only labels the image |
| `PUBLIC_URL` | yes | `https://acme.gw.example.com`, the address users reach through the tunnel |
| `OIDC_ISSUER` | yes | the company IdP's issuer URL (`https://accounts.google.com` for Google, see below) |
| `OIDC_CLIENT_ID` | yes | client id registered at that IdP |
| `OIDC_CLIENT_SECRET` | no | leave empty for a public (PKCE) client |
| `ADMIN_EMAILS` | yes | comma-separated emails that are admins, e.g. `you@yourcompany.example`. Counts only when the IdP marked the email verified. Start with the operator's; add the customer's admin on handover |
| `ADMIN_GROUP` | no | IdP group whose members are also admins, only for an IdP that emits a `groups` claim |
| `COMMANDCODE_API_KEY` | as needed | this company's Command Code key (provider `commandcode`) |
| `OPENROUTER_API_KEY` / `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | as needed | this company's other model keys |

### Sign-in with Google (first-choice IdP, free)

Decided 2026-10-24 (`docs/private-fork/ACCESS_CONTROL_PLAN.md` section 6). No per-user or per-login charge, and no billing account.
1. Google Cloud Console → a project → APIs & Services → OAuth consent screen. User type **External**, scopes `openid`, `email`,
   `profile` only. Then **publish it to production**: in Testing mode only listed test users (100 at most) can sign in and the
   login expires after 7 days. Basic scopes normally need no Google app review; Google may ask you to confirm the app name,
   logo and domain.
2. Credentials → Create OAuth client ID → **Web application**. Authorized redirect URI: `<PUBLIC_URL>/auth/callback` (the route in
   `hermes_cli/dashboard_auth/routes.py`). One client can serve every company, each gateway adding its own redirect URI, or make
   one client per company to keep them apart.
3. Set `OIDC_ISSUER=https://accounts.google.com`, `OIDC_CLIENT_ID`, and `OIDC_CLIENT_SECRET` (Google's web client always has a
   secret, so unlike a pure PKCE client it is required here). Not yet exercised against Google: the browser login and the desktop
   app's native sign-in flow (tracked with O5 in `FORK_DECISIONS.md`).

Google emits **no `groups` claim**, so admins are named in `ADMIN_EMAILS` (the operator first: sign in with that Google account and
you are an admin) and per-profile access comes from invites, not `hermes-profile-<name>` groups. Set `email` on every invite.
Changing `ADMIN_EMAILS` needs a redeploy of the company stack. Residual risk: the match is on the verified email, so an
address that is later reassigned (a Workspace admin renaming an account) would carry the admin role with it.

Codex is a subscription login, not an env key: one subscription per company. After the first deploy open a terminal in the
`hermes` container (Dokploy → Terminal) and run `hermes auth` for Codex as the admin; the login is stored in that profile's
auth file on the `hermes-data` volume.

A missing required value aborts the deploy (`${VAR:?...}`), so a project cannot start half-configured.

## Create a company

1. **Dokploy → Create Compose → Source: Git.** Repository: `github.com/sentio-one/sentio` (Dokploy needs a deploy key or GitHub App if the repo is private), branch: the one you deploy from, compose
   path `./deploy/dokploy/docker-compose.yml`. Turn on auto-deploy for the branch.
2. **Environment tab:** fill the table above.
3. **Domains tab:** host = the company's hostname, service = `hermes`, container port = `9119`. HTTPS off at Traefik
   if Cloudflare terminates TLS (the tunnel talks plain http to Traefik).
4. **Cloudflare Tunnel:** a public hostname for that company pointing at Traefik's http entry point (typically
   `http://localhost:80`, wherever your tunnel can reach Traefik). A wildcard such as `*.gw.example.com` means a new
   company needs no tunnel change. The service URL must be plain **`http://localhost:80`**: an entry set to
   `https://localhost:443` gives Cloudflare's 502 "Bad gateway" with the host marked Error, because Traefik serves this route
   only over http (the Domains tab has HTTPS off). `PUBLIC_URL` must start with `https://` and have no trailing dot, or the
   OAuth redirect is wrong (the gateway logs a warning and ignores it).
5. **IdP (Keycloak/Authentik/Entra/Okta...):** register a client for this gateway.
   - Redirect URI: `<PUBLIC_URL>/auth/callback`. As far as I read the auth routes, the desktop's loopback sign-in goes
     through the gateway, so the IdP does not need the desktop's loopback addresses. Verify with a real sign-in.
   - Admins are the verified emails in `ADMIN_EMAILS`. To use groups instead (or as well), the ID token must carry a
     **`groups`** claim (Keycloak: a Group Membership mapper with full path off; Entra: group claims, mind the 200-group
     overflow; Google has none): users who should reach a profile are in `hermes-profile-<profile>` and admins are in
     `ADMIN_GROUP`. Without groups, profile access comes from invites.
6. **Deploy**, then check the list below.

## Checklist after the first deploy

- Container reports **healthy** (not just running). `unhealthy: no access policy is active` means the managed file did
  not load: check the `/etc/hermes` mount and that the three required variables are set.
- Sign in with an **admin** user from a browser at `PUBLIC_URL`; then with a **non-admin** user and confirm host panels
  (files, git, plugins, update) are refused and only granted profiles appear.
- Open the Light desktop client, connect to `PUBLIC_URL`, sign in, confirm the roster shows only granted profiles.
- WebSockets survive an idle minute or two through the tunnel (Cloudflare closes idle connections after about 100
  seconds; the gateway has keepalive code but it has not been tested through a tunnel here).
- Stop the container and start it again; profiles and sessions are still there (the `hermes-data` volume).

## Inviting people

As an admin, open the dashboard's **Access** page: create an invite, copy the link it shows once, and revoke invites or
person-level access later. The same thing over HTTP: `POST /api/access/invites {"profile": "alpha"}` as an admin returns a `hermes://connect?...` link once; the person opens
it in the desktop app, confirms the server, signs in, and the invitation is redeemed. List and revoke with
`GET /api/access/invites`, `DELETE /api/access/invites/{id}?revoke_grants=true`, `GET /api/access/grants`. Access that comes from IdP groups is not listed on that page;
manage it in the IdP.

## Operating it

- **Backups:** the `hermes-data` volume holds everything this company owns (profiles, sessions, `access_grants.db`).
  Back it up on a schedule and test a restore. Dokploy's volume backups or a `docker run --rm -v ... tar` job both work.
- **Updates:** push to the deploy branch; Dokploy rebuilds the image on the server. The first build is slow (the image
  compiles and installs a lot); later ones reuse Docker layers.
- **Rollback:** redeploy the previous commit; the volume is untouched. A schema change in a newer image is the one thing
  a rollback cannot undo, so back up before upgrading.
- **Upstream merges:** the pinned policy and invite store are fork-only code; merge upstream on a branch, run the tests
  (`scripts/run_tests.sh tests/gateway tests/hermes_cli`) and deploy a throwaway company first.

## Limits that matter

- **Sandbox: how it works.** The main (launch) profile runs its tools inside the `hermes` container, because its
  agent provisions the other profiles (D13). Every other profile under an `access:` policy has `TERMINAL_ENV=docker` and the
  managed sandbox pins (`docker_volumes: []`, `docker_extra_args: []`, `docker_forward_env: []`, `docker_env: {}`,
  `docker_mount_cwd_to_workspace: false`, `container_persistent: false`, `docker_image: nikolaik/python-nodejs:python3.11-nodejs20`,
  `container_cpu: 1`, `container_memory: 5120`, `container_disk: 51200`) enforced server-side in
  `tools/terminal_scope.build_profile_terminal_scope` via `tools/terminal_scope_sandbox.py`. Even if a user or agent edits a
  routed profile's `config.yaml` or `.env` to ask for `backend: local`, `/:/host`, `--privileged`, or `docker_forward_env`, the
  gateway overwrites it back to the isolated Docker sandbox (`docs/private-fork/SANDBOX_PLAN.md`).
- **Host execution surfaces outside the terminal backend are closed for sandboxed profiles.** For any non-launch profile under an
  `access:` policy, stdio MCP servers (`mcp_servers`), `quick_commands` with `type: exec`, shell hooks (`hooks`), host-local
  browser launch (`bot_desktop.placement: gateway`), and cron `script` / `monitor_script` jobs fail closed and log
  (`tests/tools/test_sandbox_host_surfaces.py`).
- **`dockerd` is privileged** (Docker-in-Docker needs it), so whoever can speak to its API owns the Dokploy host. It holds no
  company data and no secrets, has no domain, and listens on **no network port at all**: only a unix socket in a volume shared
  with `hermes`, restricted to the gateway user's gid. This matters because a sandboxed container runs on the daemon's own bridge
  and can reach any tcp port it serves (it did, in an earlier version of this file that used port 2375). Do not add a `ports`,
  `expose` or tcp `--host`, and do not mount the host's `docker.sock` into any service.
  `scripts/fork/verify-dind-isolation.sh` proves both the daemon isolation and an end-to-end routed profile `terminal_tool` call
  against real Docker and runs in `Fork CI`.
- **Sandbox egress filtering:** `deploy/dokploy/dockerd-entrypoint.sh` installs `DOCKER-USER` and `INPUT` rules inside `dockerd`
  that drop traffic from sandbox bridges (`! -i eth0`) to RFC 1918 (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), link-local
  (`169.254.0.0/16`), loopback, CGNAT, the compose network, and local ports on `dockerd` itself, while keeping public internet
  access (`ESTABLISHED,RELATED` + `RETURN`).
- Static per-user rules (`access.users` / `access.admins`) are deliberately empty in the pinned file. If a company's IdP
  cannot emit groups, fork `managed/config.yaml` for that company rather than loosening the shared one.
- The compose file builds with `build:`. If server builds become a problem, build in GitHub Actions, push to GHCR and switch
  the compose `image:` to that tag (Dokploy pulls with a read-only token).
