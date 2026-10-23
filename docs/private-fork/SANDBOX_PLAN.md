# Sandbox enforcement plan

Status: **plan, not built.** Tracks `FORK_DECISIONS.md` O1 and the go-live gate (L4). Written 2026-10-23.

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

### Phase 0: pin the evidence (this PR)
- `tests/tools/test_terminal_scope_sandbox_gap.py`: two **strict xfail** tests that state the contract (G1, G2, G3), and two passing guards
  (the launch profile keeps its backend; with no access policy nothing is forced). Strict means they fail the day they start passing.
- Still to add: an end-to-end test that a `docker_forward_env` entry reaches a container (G3), run against the dind in CI.

### Phase 1: enforce at the seam (closes G1, G2, G3, G4, G7 for the terminal)
- New sibling module `tools/terminal_scope_sandbox.py` (a hook of a few lines at the end of `build_profile_terminal_scope`; the
  health ratchet forbids growing the facade).
- When an `access:` policy is configured **and** the profile home is not the launch home, **overwrite** (not default) the scope:
  `TERMINAL_ENV=docker`, `TERMINAL_DOCKER_VOLUMES=[]`, `TERMINAL_DOCKER_EXTRA_ARGS=[]`, `TERMINAL_DOCKER_FORWARD_ENV=[]`,
  `TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE=false`, persistent container, and the pinned resource limits (G8).
- Apply the managed overlay in the builder for every profile, so the pins in `managed/config.yaml` become true (upstream gap, fork patch).
- "Launch home" is `get_routing_process_hermes_home()`, never a profile-supplied value. A scope that cannot be built stays a refusal.
- Block the `_task_env_overrides` path for such profiles (G7) after reading who calls it.
- Done when the two xfail tests pass (markers removed) and the guards still pass.

### Phase 2: close the host-execution surfaces outside the backend (G5)
For a non-launch profile under an access policy, each of these must stop running on the host. Decision per surface is in section 4.
- `mcp_servers` stdio commands, `quick_commands` `type: exec`, shell hooks (`hooks_auto_accept`), local browser, cron `script` jobs.
- Mechanism candidates: refuse in the loader (fail closed, logged), or run inside the profile's sandbox, or make the REST/RPC write of
  that config key admin-only. The loader is the safe choice because file edits and the agent's own tools also reach it.

### Phase 3: egress (G6)
- Inside the daemon, drop traffic from sandbox bridges to the compose network and RFC 1918 / link-local ranges and the host gateway,
  keeping the internet (rules in `DOCKER-USER`, applied by the `dockerd` service at start; verified by `verify-dind-isolation.sh`).
- Evaluate upstream's egress proxy (`egress:` in config, "iron-proxy"): the sandbox sees opaque tokens and real credentials are
  swapped in at egress, and it guards `docker_forward_env`, `docker_env` and `docker_extra_args` while enabled. It would also
  answer "never put real keys in a sandbox". Needs a decision (section 4) before building.

### Phase 4: provisioning (D13, O3)
- A skill and a CLI path for the main agent to create a profile with the docker backend and a `hermes-profile-<name>` grant note.
  Today nothing makes a new profile sandboxed except phase 1, which is why phase 1 comes first.

### Phase 5: prove it end to end
- Extend `verify-dind-isolation.sh`: drive a real profile's terminal tool through the docker backend against the dind and assert the
  sandbox sees none of the gateway's secrets, cannot see `/opt/data`, cannot reach host port 22 or the compose network, and is
  bounded by its limits. Runs in `Fork CI`.
- A throwaway-company deploy on Dokploy using the checklist in `deploy/dokploy/README.md`.

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
