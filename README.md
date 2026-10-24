# Sentio One

**An AI agent platform your company hosts for its own people.**

Sentio One gives a business one central AI agent gateway, sign-in through the company's own identity provider, and a
separate workspace for every person, so each employee gets an agent that remembers their work, uses their tools and
never sees anyone else's. IT decides who gets which workspace from the groups it already manages.

It is built on [Hermes Agent](https://github.com/NousResearch/hermes-agent) (MIT, by Nous Research): the agent itself,
its memory, skills, scheduled jobs, browser and terminal tools, and messaging integrations are Hermes. Sentio One adds
what a company needs to run it for many people.

---

## Why a business would use it

| You need | Sentio One |
|---|---|
| One deployment you control | One gateway per company, on your own infrastructure. Profiles, conversations and settings live on a volume you back up. |
| Staff sign in with company accounts | Any OIDC provider that issues a `groups` claim (Keycloak, Authentik, Entra, Okta, ...). No separate passwords. |
| Each person gets their own agent | A **profile** per person: its own memory, skills, settings and history. People reach only the profiles they are granted. |
| IT controls access without a new admin tool | Access comes from IdP groups: `hermes-profile-<name>` grants a profile, one admin group grants everything. Or send an **invite link** for a single profile. |
| A simple client for staff | A thin desktop app with no agent runtime of its own. One installer serves every company; the link tells it where to connect. |
| Your choice of model | Bring your own provider keys or subscriptions (Command Code, Codex, OpenRouter, Anthropic, OpenAI, and the other providers Hermes supports). |
| Agents that do real work | Everything Hermes does: persistent memory, learned skills, scheduled jobs, browser and terminal control, delegation to sub-agents, and messaging channels such as Slack, Telegram and Discord. |

---

## How it works

```
  Desktop app (remote-first)                    Company gateway, one per company
  ┌──────────────────────────────┐   https    ┌──────────────────────────────────────────────┐
  │ hermes://connect?url=&invite= │ ────────▶ │ Cloudflare Tunnel → Traefik → gateway         │
  │ confirm the server, sign in   │            │  • company sign-in (OIDC) and groups          │
  │ (company SSO)                 │ ◀──────── │  • who may use which profile (admins, groups) │
  │ no local agent runtime        │ websocket  │  • invites, admin Access page                 │
  └──────────────────────────────┘            │  • profiles + sessions on a persistent volume │
                                               │  • its own Docker daemon, for sandboxing      │
                                               │    agent tools                                │
                                               └──────────────────────────────────────────────┘
```

1. **IT deploys a gateway** for the company (one Dokploy project; a ready-made compose file is included).
2. **IT connects the company's identity provider** and decides who is an admin and who gets which profile.
3. **People install the desktop app** and open an invite link (or enter the gateway address). The app shows the server
   for confirmation, they sign in with their company account, and they land in their own workspace.

The desktop app is **remote-first**: connecting to the company gateway is the default experience, and running an agent
locally is an optional extra, not a requirement.

---

## What Sentio One adds to Hermes

- **Per-user, per-profile access control** enforced across live sessions, the REST API and the profile-selecting sockets,
  failing closed: an unknown person reaches nothing.
- **Invite links** that grant one profile, single-use by default, expiring, revocable, never granting admin.
- **An admin Access page** to create and revoke invites and to see the access they granted.
- **A pinned per-company policy** the people using it cannot edit, with a healthcheck that fails if the policy is missing.
- **A per-company deployment** for Dokploy: the gateway plus its own Docker daemon, so agent tools can be sandboxed per company.
- **A remote-first desktop client** with a confirm-before-connect screen for `hermes://connect` links.
- **Fork CI** that tests the access model, invites, desktop client and deployment file on every change.

Every difference from upstream, and why, is recorded in
[`docs/private-fork/FORK_DECISIONS.md`](docs/private-fork/FORK_DECISIONS.md).

---

## Status

**Pre-release.** The access model, invites, admin page, desktop client and deployment files are built and covered by
automated tests that run on every change. Sentio One has not yet been deployed for a customer, and some items are still
open before that happens, chiefly making the sandbox mandatory for everyone's agent tools. The checklist is in section 5 of
[`FORK_DECISIONS.md`](docs/private-fork/FORK_DECISIONS.md).

---

## Run it

- **Deploy a company gateway:** [`deploy/dokploy/README.md`](deploy/dokploy/README.md) (environment, domain, tunnel, IdP, first-deploy checklist).
- **Invite someone:** as an admin, open the dashboard's **Access** page and create an invite for a profile; send the link.
  Design and API: [`docs/private-fork/INVITES_DESIGN.md`](docs/private-fork/INVITES_DESIGN.md).
- **Desktop client builds and releases:** every merge to `main` builds the Light installer and keeps it as a downloadable workflow
  artifact. To publish a release, run `scripts/fork/bump-version.sh X.Y.Z` and merge the pull request: the merge tags `vX.Y.Z` and the
  release build publishes `HermesLight-*.msix`. Setup and details: [`docs/private-fork/RELEASING.md`](docs/private-fork/RELEASING.md).
  To build by hand: `scripts/bundles/desktop.py --commit <sha> --variant light` from a clean checkout.

## Working on it

- Hosted on GitHub; `upstream` is `NousResearch/hermes-agent` (read-only, nothing is sent back). Upstream updates arrive on
  the `upstream-merge` branch through a pull request, following the checklist in
  [`FORK_DECISIONS.md`](docs/private-fork/FORK_DECISIONS.md) section 3.
- Tests: always `scripts/run_tests.sh`, never bare `pytest`. Lint: `python scripts/check`. Desktop and web: `npm run typecheck`
  and `npx vitest run` in `apps/desktop` and `web`. See [`AGENTS.md`](AGENTS.md) and [`CONTRIBUTING.md`](CONTRIBUTING.md).
- Fork documents: [`docs/private-fork/`](docs/private-fork/) (access control plan and audit, invite design, route inventory).

## License

MIT. Hermes Agent is © Nous Research; Sentio One's additions are released under the same license. See [`LICENSE`](LICENSE).
