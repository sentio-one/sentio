# Sandbox enforcement plan

Status: **implemented and verified in Docker (`scripts/fork/verify-dind-isolation.sh`).** Tracks `FORK_DECISIONS.md` O1 and the go-live gate (L4). Written 2026-10-23, implemented 2026-10-24.

## Goal

A person who is granted a profile must not be able to run code on the gateway container, read another profile's files, or take the
company's secrets, however they edit their own profile. Only the **main (launch) profile** is host-level, because its agent
provisions the other profiles (D13). Everything else runs its tools in a container on the company's own Docker daemon.

Rule of the design: **enforce on server-side facts at the one place a decision is made, fail closed, and treat profile-editable
config as a request, never as policy.** With no `access:` policy configured, behaviour stays exactly upstream's.

## 1. What is already true

| Fact | Evidence |
|---|---|
| The privileged daemon is unreachable from a sandbox (socket-only, group-restricted) | `scripts/fork/verify-dind-isolation.sh`, run in `Fork CI` (PR #3) |
| `execute_code` and the file tools share the terminal backend's environment, so they follow it into Docker | `tools/code_execution_tool.py` ("the environment the terminal/file tools share") |
| One long-lived container per profile in persistent mode, per session otherwise | `tools/terminal_tool.py` `_SessionScope` |
| A profile's complete terminal policy is decided in **one** function, and an unresolvable policy refuses to run | `tools/terminal_scope.py` `build_profile_terminal_scope`, `enforce_no_refusal` |

## 2. Findings (what is not true yet)

"Ran" = reproduced here. "Read" = from the code, not exercised end to end.

| # | Finding | Basis |
|---|---|---|
| G1 | **The managed pins do not reach a routed profile's sandbox.** `build_profile_terminal_scope` reads the profile's `config.yaml` and `.env` raw and ignores the managed overlay, so `terminal.docker_*` pinned in `managed/config.yaml` is not applied. A profile config with `docker_volumes: ["/:/host"]`, `docker_extra_args: ["--privileged"]`, `docker_mount_cwd_to_workspace: true` produced exactly those values in the scope. | **Ran** (`tests/tools/test_terminal_scope_sandbox_gap.py`) |
| G2 | **The backend is the profile's choice.** `terminal.backend` / `.env TERMINAL_ENV` of `local` yields `TERMINAL_ENV=local`: commands run in the gateway container as the `hermes` user. REST config writes are profile-class, so a granted user can set it. | **Ran** (scope); **read** (REST) |
| G3 | **A profile can forward the gateway's secrets into its container.** `docker_forward_env` names are resolved from the gateway process environment and the profile `.env`; the code comment says an explicit entry "bypasses the blocklist" (`tools/environments/docker.py`, `remote_common.resolve_passthrough_env`). The gateway's environment holds the OIDC client secret and the provider API keys. | **Ran** (the key reaches the scope); **read** (the forwarding itself; phase 0 proves it end to end) |
| G4 | **Mounts and flags can reach the daemon's own filesystem.** A `/:/host` volume is the *dind container's* root, which holds the daemon socket and `/var/lib/docker`; `--privileged` inside a privileged daemon reaches the host's devices. Either undoes PR #3. | **Read** (follows from G1 plus how dind works) |
| G5 | **Config can start host processes outside any backend.** `mcp_servers` entries are spawned as local subprocesses (`tools/mcp_tool_transport.py`, `stdio_client`); `/api/mcp` is profile-class. `quick_commands` of `type: exec` run "as a host shell of the gateway process" (`gateway/run_inbound_hooks.py:179`). Shell hooks run when consented, and a profile can set `hooks_auto_accept: true` itself (`agent/shell_hooks.py`). | **Read** |
| G6 | **A sandbox reaches the internet and any host service bound to all interfaces.** From a test sandbox, SSH on the Dokploy host's port 22 was reachable through the compose network's gateway. On a real server that includes anything listening on `0.0.0.0` (Traefik, the Dokploy UI). | **Ran** |
| G7 | **`env_type` can be overridden per task** (`_task_env_overrides`, meant for RL/benchmark rollouts). Who may register an override is unchecked here. | **Read**, unverified |
| G8 | **No resource limits are enforced.** A sandbox can fill the daemon's disk or fork-bomb it, taking the company's gateway down. Upstream has `container_*` limit keys; none are pinned. | **Read** |

## 3. Phases

Each phase is its own PR, with a test shown red before the fix and green after.

### Phase 0: pin the evidence (done)
- `tests/tools/test_terminal_scope_sandbox_gap.py`: contract tests for G1, G2, G3, G7, G8 plus passing guards
  (the launch profile keeps its backend; with no access policy nothing is forced).
- End-to-end test in `scripts/fork/verify_dind_profile_e2e.py` (invoked by `scripts/fork/verify-dind-isolation.sh` in `Fork CI`)
  proving `docker_forward_env`, `docker_volumes`, and `--privileged` from a hostile profile are blocked against the live dind.

### Phase 1: enforce at the seam (done — closes G1, G2, G3, G4, G7, G8 for the terminal)
- Sibling module `tools/terminal_scope_sandbox.py` hooked into `tools/terminal_scope.py` (`build_profile_terminal_scope`,
  `set_terminal_scope`, `terminal_env`).
- When an `access:` policy is configured **and** the profile home is not the launch home, **overwrites** (not defaults) the scope:
  `TERMINAL_ENV=docker`, `TERMINAL_DOCKER_VOLUMES=[]`, `TERMINAL_DOCKER_EXTRA_ARGS=[]`, `TERMINAL_DOCKER_FORWARD_ENV=[]`,
  `TERMINAL_DOCKER_ENV={}`, `TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE=false`, `TERMINAL_CONTAINER_PERSISTENT=false`, pinned image,
  and pinned resource limits (`container_cpu=1`, `container_memory=5120`, `container_disk=51200`).
- Applies the managed overlay in `build_profile_terminal_scope` and updates `hermes_cli/managed_scope.py` so empty dict leaves
  (`docker_env: {}`) overwrite user dicts.
- Blocks `_task_env_overrides` (`register_task_env_overrides` / `resolve_task_overrides` in `tools/terminal_tool.py`), host bind
  mounts (`tools/environments/docker.py::_readonly_skill_mount_args`, `tools/credential_files.py`), and env passthrough
  (`tools/env_passthrough.py`, `tools/environments/remote_common.py`) for sandboxed profiles.

### Phase 2: close the host-execution surfaces outside the backend (done — closes G5)
For a non-launch profile under an access policy, each of these fails closed and logs (`tests/tools/test_sandbox_host_surfaces.py`):
- `mcp_servers` stdio commands (`tools/mcp_tool_config.py`, `tools/mcp_tool_transport.py`; HTTP/SSE MCP remains allowed),
- `quick_commands` `type: exec` (`gateway/run_inbound_hooks.py`),
- shell hooks / `hooks_auto_accept` (`agent/shell_hooks.py`),
- local host browser launch (`tools/bot_desktop/placement.py`, `tools/browser_session.py`, `tools/browser_launcher.py`,
  `tools/browser_lightpanda.py`),
- cron `script` and `monitor_script` jobs (`cron/jobs_invariants.py`, `cron/scheduler_script.py`).

### Phase 3: egress (done — closes G6)
- Inside the daemon (`deploy/dokploy/dockerd-entrypoint.sh`, mounted by `deploy/dokploy/docker-compose.yml`), `DOCKER-USER` and
  `INPUT` rules drop traffic from sandbox bridges (`! -i eth0`) to the compose network, RFC 1918 (`10.0.0.0/8`, `172.16.0.0/12`,
  `192.168.0.0/16`), link-local (`169.254.0.0/16`), loopback, CGNAT, and the `dockerd` host while keeping public internet
  (`ESTABLISHED,RELATED` + `RETURN`). Verified by `scripts/fork/verify-dind-isolation.sh` and `tests/gateway/test_dokploy_compose.py`.

### Phase 4: provisioning (done — D13, O3)
- `hermes profile create <name>` (`hermes_cli/profiles_initialization.py`, `hermes_cli/profile_cmd.py`) stamps
  `terminal.backend: docker` into the new profile's `config.yaml` when an `access:` policy is active and prints the
  `hermes-profile-<name>` IdP group grant notice (`tests/hermes_cli/test_profile_sandbox_provisioning.py`).
- Bundled skill `skills/autonomous-ai-agents/profile-provisioning/SKILL.md` guides the main agent through provisioning a
  sandboxed profile and sharing the grant note.

### Phase 5: prove it end to end (done)
- `scripts/fork/verify-dind-isolation.sh` and `scripts/fork/verify_dind_profile_e2e.py` drive a real routed profile's
  `terminal_tool` through the docker backend against the live `dind` service and assert the sandbox sees none of the gateway's
  secrets, cannot see `/opt/data` or `/var/run/dind/docker.sock`, cannot reach the daemon API, compose network, or link-local
  metadata, and is bounded by `Privileged=false`, `CapDrop=[ALL]`, `no-new-privileges`, `Memory=5120m`, `NanoCpus=1e9`,
  `PidsLimit=2048`, and disk bounds (`StorageOpt size=51200m` when supported, or `ReadonlyRootfs=true` + `/workspace` tmpfs
  `size=51200m`). Runs in `Fork CI`.
- External operator step tracked in `FORK_DECISIONS.md` O5 before a customer is on a gateway: a throwaway-company deploy on a live
  Dokploy server + Cloudflare Tunnel + OIDC IdP using the checklist in `deploy/dokploy/README.md`.

## 4. Decisions for the owner

1. **MCP, quick commands, hooks, browser for non-main profiles:** (a) forbid, (b) run inside the profile's sandbox, (c) forbid unless
   an admin approves per profile. Recommendation: **(a) for now**, (b) later per surface; it is the only option that needs no new
   machinery and it fails closed.
2. **Egress:** open internet with private ranges blocked (phase 3 as written), an allow-list, or iron-proxy. Recommendation: block
   private ranges now; decide iron-proxy when the first real customer's credential model is known.
3. **Do admins who are not the main profile also run sandboxed?** Recommendation: **yes**. Only the launch profile is host-level, so
   the rule is one line and an admin's second profile is not a back door.
4. **Image:** a user-chosen `docker_image` is a supply-chain choice made by every user. Recommendation: pin one company image.
5. **Limits:** default CPU, memory, pids and disk per sandbox (numbers to choose with a real workload).

## 5. Out of scope and known limits

- A sandbox that reaches the internet can still exfiltrate what it can see (its own workspace and anything the user pastes in).
- Profiles share one OS user and one volume (`/opt/data`) for the **main** profile and for state; the sandbox protects code
  execution, not the application layer (that is the access policy's job).
- Docker-in-Docker needs a privileged container; the daemon holds no company data, but a kernel-level container escape is out of
  scope here. A stronger runtime (gVisor, Kata, a VM per company) is the next step if the threat model requires it.

## 6. Done means

`FORK_DECISIONS.md` go-live item "Sandbox enforced (O1)" is ticked when phases 1, 2, 3 and 5 are merged and verified on a throwaway
company, with the strict xfail markers gone.
