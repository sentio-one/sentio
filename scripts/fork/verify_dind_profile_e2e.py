#!/usr/bin/env python3
"""End-to-end verification of a routed profile's Docker sandbox against the live dind service.

Invoked by ``scripts/fork/verify-dind-isolation.sh <dockerd-container-name> <dind-socket-path>``.
Drives ``install_and_reset_profile_terminal_scope`` + ``terminal_tool`` against the real
Docker-in-Docker daemon and verifies:
1. Even when a routed profile (`alpha`) configures `backend: local`, `docker_volumes: ["/:/host"]`,
   `docker_extra_args: ["--privileged"]`, `docker_forward_env: ["HERMES_DASHBOARD_OIDC_CLIENT_SECRET",
   "OPENROUTER_API_KEY"]`, and `container_memory: 0`, its command executes inside an unprivileged
   container on `dockerd`, not on the host/gateway.
2. Gateway secrets (`HERMES_DASHBOARD_OIDC_CLIENT_SECRET`, `OPENROUTER_API_KEY`, `COMMANDCODE_API_KEY`)
   do not reach the container environment or `/proc/1/environ`.
3. Host/gateway paths (`/opt/data`, launch profile files, and `docker.sock`) are invisible inside
   the container.
4. `docker inspect` of the sandbox container confirms `Privileged=false`, `CapDrop=[ALL]`,
   `SecurityOpt` includes `no-new-privileges`, `PidsLimit=2048`, `Memory=5368709120` (5120 MB),
   `NanoCpus=1000000000` (1 CPU), and bounded disk (`StorageOpt` or `ReadonlyRootfs` + `/workspace` tmpfs).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _make_docker_wrapper(wrapper_dir: Path, dind_container: str, dind_sock: str) -> Path:
    """Create a `docker` CLI shim that execs `docker -H unix://<dind_sock>` inside the dind container."""
    real_docker = shutil.which("docker") or "/usr/bin/docker"
    wrapper = wrapper_dir / "docker"
    wrapper.write_text(
        f"""#!/bin/sh
if [ -t 0 ]; then
  exec "{real_docker}" exec -e DOCKER_HOST="unix://{dind_sock}" "{dind_container}" docker "$@"
else
  exec "{real_docker}" exec -i -e DOCKER_HOST="unix://{dind_sock}" "{dind_container}" docker "$@"
fi
""",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return wrapper


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: verify_dind_profile_e2e.py <dind_container> <dind_sock>", file=sys.stderr)
        return 2
    dind_container = sys.argv[1]
    dind_sock = sys.argv[2]

    with tempfile.TemporaryDirectory(prefix="hermes-dind-e2e-") as tmp:
        tmp_path = Path(tmp)
        wrapper_dir = tmp_path / "bin"
        wrapper_dir.mkdir()
        wrapper = _make_docker_wrapper(wrapper_dir, dind_container, dind_sock)

        launch = tmp_path / "opt-data"
        alpha = launch / "profiles" / "alpha"
        alpha.mkdir(parents=True)
        (launch / "gateway-secret.txt").write_text("TOP_SECRET_LAUNCH_DATA", encoding="utf-8")

        managed = tmp_path / "managed"
        managed.mkdir()
        managed_src = REPO_ROOT / "deploy" / "dokploy" / "managed" / "config.yaml"
        managed_text = managed_src.read_text(encoding="utf-8-sig")
        (managed / "config.yaml").write_text(managed_text, encoding="utf-8")

        # Write a maximally hostile profile config and .env in alpha
        hostile_cfg = {
            "terminal": {
                "backend": "local",
                "docker_image": "evil/rootkit:latest",
                "docker_volumes": ["/:/host", f"{launch}:/opt/data"],
                "docker_extra_args": ["--privileged", "--network=host"],
                "docker_mount_cwd_to_workspace": True,
                "docker_forward_env": [
                    "HERMES_DASHBOARD_OIDC_CLIENT_SECRET",
                    "OPENROUTER_API_KEY",
                    "COMMANDCODE_API_KEY",
                ],
                "docker_env": {"INJECTED_EVIL": "yes"},
                "container_cpu": 0,
                "container_memory": 0,
                "container_disk": 0,
            }
        }
        (alpha / "config.yaml").write_text(json.dumps(hostile_cfg), encoding="utf-8")
        (alpha / ".env").write_text(
            "TERMINAL_ENV=local\n"
            'TERMINAL_DOCKER_VOLUMES=["/:/host"]\n'
            'TERMINAL_DOCKER_EXTRA_ARGS=["--privileged"]\n'
            'TERMINAL_DOCKER_FORWARD_ENV=["HERMES_DASHBOARD_OIDC_CLIENT_SECRET","OPENROUTER_API_KEY"]\n'
            "HERMES_DASHBOARD_OIDC_CLIENT_SECRET=profile-attempted-secret\n",
            encoding="utf-8",
        )

        os.environ["PATH"] = f"{wrapper_dir}:{os.environ.get('PATH', '')}"
        os.environ["HERMES_HOME"] = str(launch)
        os.environ["HERMES_MANAGED_DIR"] = str(managed)
        os.environ["HERMES_ADMIN_GROUP"] = "acme-admins"
        os.environ["HERMES_DASHBOARD_OIDC_CLIENT_SECRET"] = "super-secret-oidc-val"
        os.environ["OPENROUTER_API_KEY"] = "sk-or-super-secret-key"
        os.environ["COMMANDCODE_API_KEY"] = "cc-super-secret-key"

        from hermes_cli import managed_scope
        from hermes_constants import reset_hermes_home_override, set_hermes_home_override
        import tools.environments.docker as docker_mod
        from tools.terminal_scope import install_and_reset_profile_terminal_scope
        from tools.terminal_tool import cleanup_all_environments, terminal_tool

        docker_mod._docker_executable = str(wrapper)
        managed_scope.invalidate_managed_cache()

        cids: list[str] = []
        token = set_hermes_home_override(alpha)
        try:
            with install_and_reset_profile_terminal_scope(alpha):
                raw_res = terminal_tool(
                    command=(
                        "echo CONTAINER_MARKER=$(cat /proc/1/cgroup 2>/dev/null | head -n 1); "
                        "echo OIDC=${HERMES_DASHBOARD_OIDC_CLIENT_SECRET:-EMPTY}; "
                        "echo OR=${OPENROUTER_API_KEY:-EMPTY}; "
                        "echo CC=${COMMANDCODE_API_KEY:-EMPTY}; "
                        "echo EVIL=${INJECTED_EVIL:-EMPTY}; "
                        "PID1_ENV=$(tr '\\0' '\\n' < /proc/1/environ 2>/dev/null); "
                        "echo \"$PID1_ENV\" | grep -qE 'OIDC|OPENROUTER|COMMANDCODE|INJECTED_EVIL|super-secret' || echo PID1_CLEAN=1; "
                        "test ! -e /host/var/run/dind/docker.sock && echo NO_HOST_MOUNT=1; "
                        "test ! -e /opt/data/gateway-secret.txt && echo NO_OPT_DATA=1"
                    ),
                    task_id="e2e-alpha",
                )
                res = json.loads(raw_res)
                output = res.get("output", "")
                assert res.get("exit_code") == 0, f"terminal_tool failed: {res}"
                assert "OIDC=EMPTY" in output, f"OIDC secret leaked: {output}"
                assert "OR=EMPTY" in output, f"OPENROUTER secret leaked: {output}"
                assert "CC=EMPTY" in output, f"COMMANDCODE secret leaked: {output}"
                assert "EVIL=EMPTY" in output, f"docker_env leaked: {output}"
                assert "PID1_CLEAN=1" in output, f"/proc/1/environ leaked secrets: {output}"
                assert "NO_HOST_MOUNT=1" in output, f"/:/host mount was not blocked: {output}"
                assert "NO_OPT_DATA=1" in output, f"/opt/data was visible inside sandbox: {output}"

                # Inspect the running container inside dind to verify security & resource limits
                ps = subprocess.run(
                    [str(wrapper), "ps", "-q", "--filter", "label=hermes-agent=1"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=True,
                )
                cids = [line.strip() for line in ps.stdout.splitlines() if line.strip()]
                assert len(cids) == 1, f"Expected 1 sandbox container on dind, found: {cids}"
                inspect_raw = subprocess.run(
                    [str(wrapper), "inspect", cids[0]],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=True,
                ).stdout
                info = json.loads(inspect_raw)[0]
                host_cfg = info["HostConfig"]
                assert host_cfg.get("Privileged") is False, f"Container is privileged: {host_cfg}"
                assert "ALL" in (host_cfg.get("CapDrop") or []), f"CapDrop missing ALL: {host_cfg.get('CapDrop')}"
                assert any(
                    "no-new-privileges" in opt for opt in (host_cfg.get("SecurityOpt") or [])
                ), f"SecurityOpt missing no-new-privileges: {host_cfg.get('SecurityOpt')}"
                assert host_cfg.get("Memory") == 5120 * 1024 * 1024, f"Unexpected Memory limit: {host_cfg.get('Memory')}"
                assert host_cfg.get("NanoCpus") == 1_000_000_000, f"Unexpected NanoCpus limit: {host_cfg.get('NanoCpus')}"
                assert host_cfg.get("PidsLimit") == 2048, f"Unexpected PidsLimit: {host_cfg.get('PidsLimit')}"
                storage_opt = host_cfg.get("StorageOpt") or {}
                tmpfs_cfg = host_cfg.get("Tmpfs") or {}
                assert "size=51200m" in tmpfs_cfg.get("/workspace", ""), f"Unexpected /workspace tmpfs: {tmpfs_cfg}"
                assert storage_opt.get("size") == "51200m" or host_cfg.get("ReadonlyRootfs") is True, (
                    f"Container disk is unbounded (neither StorageOpt size=51200m nor ReadonlyRootfs): {host_cfg}"
                )
                if host_cfg.get("ReadonlyRootfs") is True:
                    ro_probe = json.loads(
                        terminal_tool(
                            command="touch /workspace/ok && ! touch /etc/unbounded-root-write 2>/dev/null && echo RO_ROOT_OK=1",
                            task_id="e2e-alpha",
                        )
                    )
                    assert "RO_ROOT_OK=1" in ro_probe.get("output", ""), f"Rootfs was writable: {ro_probe}"
        finally:
            cleanup_all_environments()
            subprocess.run([str(wrapper), "rm", "-f", *cids], capture_output=True, timeout=30, check=False)
            docker_mod._docker_executable = None
            reset_hermes_home_override(token)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
