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
| `OIDC_ISSUER` | yes | the company IdP's issuer URL |
| `OIDC_CLIENT_ID` | yes | client id registered at that IdP |
| `OIDC_CLIENT_SECRET` | no | leave empty for a public (PKCE) client |
| `ADMIN_GROUP` | yes | IdP group whose members are admins, e.g. `hermes-admins` |
| `COMMANDCODE_API_KEY` | as needed | this company's Command Code key (provider `commandcode`) |
| `OPENROUTER_API_KEY` / `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | as needed | this company's other model keys |

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
   company needs no tunnel change.
5. **IdP (Keycloak/Authentik/Entra/Okta...):** register a client for this gateway.
   - Redirect URI: `<PUBLIC_URL>/auth/callback`. As far as I read the auth routes, the desktop's loopback sign-in goes
     through the gateway, so the IdP does not need the desktop's loopback addresses. Verify with a real sign-in.
   - The ID token must carry a **`groups`** claim (Keycloak: a Group Membership mapper with full path off; Entra: group
     claims, mind the 200-group overflow; Google Workspace has no groups claim). Users who should reach a profile are in
     `hermes-profile-<profile>`; admins are in `ADMIN_GROUP`.
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

- **Sandbox: how it is meant to work.** The main (launch) profile runs its tools inside the `hermes` container, because its
  agent provisions the other profiles. Every other profile is created with `terminal.backend: docker`, so its commands run in
  containers on the company's `dockerd`, which has no access to `/opt/data`. **This is the intended design, not what is
  enforced today:** the pinned `docker_*` keys in `managed/config.yaml` do not reach a routed profile's terminal policy (found by
  test, 2026-10-23), so a profile can still choose the local backend, add volumes or `--privileged`, or forward the gateway's
  environment into its container. The enforcement plan is `docs/private-fork/SANDBOX_PLAN.md`.
- **Sandbox: what is not enforced yet.** Nothing stops a granted user from setting their profile's `terminal.backend` back to
  `local`, which runs commands as the container's `hermes` user with every profile's files in reach. Until that is closed
  (`FORK_DECISIONS.md` O1) treat a grant as host-level trust inside the company; the container is the boundary **between
  companies**.
- **`dockerd` is privileged** (Docker-in-Docker needs it), so whoever can speak to its API owns the Dokploy host. It holds no
  company data and no secrets, has no domain, and listens on **no network port at all**: only a unix socket in a volume shared
  with `hermes`, restricted to the gateway user's gid. This matters because a sandboxed container runs on the daemon's own bridge
  and can reach any tcp port it serves (it did, in an earlier version of this file that used port 2375). Do not add a `ports`,
  `expose` or tcp `--host`, and do not mount the host's `docker.sock` into any service.
  `scripts/fork/verify-dind-isolation.sh` proves it against real Docker and runs in CI.
- **What a sandbox can still reach:** the internet, and any host service bound to all interfaces (SSH on port 22 was reachable
  from a test sandbox). Egress control is not done yet (`SANDBOX_PLAN.md`).
- Static per-user rules (`access.users` / `access.admins`) are deliberately empty in the pinned file. If a company's IdP
  cannot emit groups, fork `managed/config.yaml` for that company rather than loosening the shared one.
- The compose file builds with `build:`. If server builds become a problem, build in GitHub Actions, push to GHCR and switch
  the compose `image:` to that tag (Dokploy pulls with a read-only token).
