---
name: profile-provisioning
description: "Provision sandboxed user profiles and IdP group grants."
version: 1.0.0
author: Sentio One + Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [profiles, provisioning, sandbox, access-control, docker, oidc]
    category: autonomous-ai-agents
    related_skills: [hermes-agent]
---

# Profile Provisioning Skill

Creates and configures isolated Hermes profiles on a company gateway so each person works inside a Docker sandbox on the company's private daemon. It also outputs the Identity Provider (IdP) group name (`hermes-profile-<name>`) or invite instructions required to grant the user access. It does not modify the launch profile's own host-level backend.

## When to Use

- An administrator asks the main (launch) agent to onboard a teammate or create a new profile.
- You need to verify that a profile is pinned to the `docker` terminal backend and check its IdP group grant name.

## Prerequisites

- Run from the **main (launch) profile** on the gateway (`get_hermes_home()` is the root Hermes home). Non-launch profiles are sandboxed and cannot provision sibling profiles on the host.
- Company `access:` policy configured (for example via `/etc/hermes/config.yaml` when `HERMES_MANAGED_DIR=/etc/hermes`).

## How to Run

Use the `terminal` tool to run `hermes profile create <name>`:

```bash
hermes profile create alice --description "Alice's workspace profile"
```

When an `access:` policy is active, `hermes profile create` automatically stamps `terminal.backend: docker` into the new profile's `config.yaml` and prints the required IdP group grant (`hermes-profile-alice`).

## Quick Reference

| Action | Command |
|---|---|
| Create a fresh sandboxed profile | `hermes profile create <name> --description "<role>"` |
| Clone skills and SOUL from default | `hermes profile create <name> --clone` |
| Check profile status & list | `hermes profile list` |
| Inspect profile details | `hermes profile show <name>` |

## Procedure

1. **Validate the profile name**: Must be lowercase alphanumeric with hyphens/underscores (for example `alice` or `eng-bob`), never `default`.
2. **Create the profile**: Run `hermes profile create <name>` via `terminal`.
3. **Verify sandbox configuration**: Read `<profile_dir>/config.yaml` with `read_file` to confirm `terminal.backend: docker` is present. Even if edited later, the gateway enforces the Docker sandbox and resource limits for every non-launch profile when `access:` is configured.
4. **Share the grant instructions**: Tell the admin to either:
   - Assign the user to the IdP group `hermes-profile-<name>` in their OIDC provider, or
   - Generate a single-use `hermes://connect` invite link on the web dashboard's `/access` page.

## Pitfalls

- **Never copy messaging channel credentials without `--clone-channels`**: Two profiles sharing one bot token will fight over updates.
- **Never put secrets in `config.yaml`**: Per-profile API keys belong in the profile's `.env` file (`hermes -p <name> setup`), never in `config.yaml`.
- **Do not set `terminal.backend: local` on user profiles**: Under a company `access:` policy, the gateway overwrites non-launch profiles back to `docker` at runtime.

## Verification

Run `hermes profile show <name>` via `terminal` and confirm the profile directory exists, `config.yaml` contains `terminal.backend: docker`, and the CLI output shows the `hermes-profile-<name>` IdP group grant.
