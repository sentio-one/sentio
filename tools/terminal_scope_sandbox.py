"""Server-side Docker sandbox enforcement for non-launch profiles under an access policy.

Only the launch (main) profile runs host-level tools so its agent can provision other
profiles (FORK_DECISIONS.md D13). Every other profile under an ``access:`` policy runs
its terminal tools inside an unprivileged container on the isolated Docker daemon with
operator-pinned volume, env, image, network, and resource-limit settings.
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import Any, Optional

from hermes_constants import get_hermes_home, get_routing_process_hermes_home

logger = logging.getLogger(__name__)

DEFAULT_SANDBOX_IMAGE = "nikolaik/python-nodejs:python3.11-nodejs20"
DEFAULT_SANDBOX_CPU = 1
DEFAULT_SANDBOX_MEMORY_MB = 5120
DEFAULT_SANDBOX_DISK_MB = 51200

_BLOCKED_TASK_OVERRIDE_KEYS = frozenset({
    "env_type",
    "docker_image",
    "singularity_image",
    "modal_image",
    "daytona_image",
    "cwd",
    "cwd_source",
    "workspace_key",
})


def _resolve_home(hermes_home: Optional[Path | str] = None) -> Path:
    raw = Path(hermes_home) if hermes_home is not None else get_hermes_home()
    return raw.expanduser().resolve(strict=False)


def _resolve_launch_home() -> Path:
    return Path(get_routing_process_hermes_home()).expanduser().resolve(strict=False)


def is_non_launch_profile(hermes_home: Optional[Path | str] = None) -> bool:
    """True when *hermes_home* (or the active home) is not the launch profile."""
    target = _resolve_home(hermes_home)
    launch = _resolve_launch_home()
    return target != launch or target.parent.name == "profiles"


def _has_access_policy(cfg: Any) -> bool:
    if not isinstance(cfg, dict):
        return False
    gw = cfg.get("gateway")
    gw_access = gw.get("access") if isinstance(gw, dict) else None
    top_access = cfg.get("access")
    merged: dict[str, Any] = {}
    if isinstance(gw_access, dict):
        merged.update(gw_access)
    if isinstance(top_access, dict):
        merged.update(top_access)
    if not merged:
        return False
    from gateway.access_policy import parse_access_policy

    return parse_access_policy(merged).configured


def _candidate_policy_homes(target: Path, launch: Path) -> list[Path]:
    homes: list[Path] = [launch]
    if target.parent.name == "profiles":
        root = target.parent.parent
        if root not in homes:
            homes.append(root)
    if target not in homes:
        homes.append(target)
    return homes


def access_policy_configured(hermes_home: Optional[Path | str] = None) -> bool:
    """True when an ``access:`` policy is configured in managed or gateway config."""
    with contextlib.suppress(Exception):
        from hermes_cli import managed_scope

        if _has_access_policy(managed_scope.load_managed_config()):
            return True

    target = _resolve_home(hermes_home)
    launch = _resolve_launch_home()
    with contextlib.suppress(Exception):
        from gateway.config_loader import read_yaml_layers

        for candidate in _candidate_policy_homes(target, launch):
            if _has_access_policy(read_yaml_layers(candidate)):
                return True
    return False


def is_sandboxed_profile(hermes_home: Optional[Path | str] = None) -> bool:
    """True when *hermes_home* is a non-launch profile governed by an ``access:`` policy."""
    return is_non_launch_profile(hermes_home) and access_policy_configured(hermes_home)


def _positive_limit(value: Any, default: float) -> int | float:
    if isinstance(value, bool) or value is None:
        return int(default) if float(default).is_integer() else default
    try:
        num = float(value)
    except (TypeError, ValueError):
        return int(default) if float(default).is_integer() else default
    if num <= 0:
        return int(default) if float(default).is_integer() else default
    return int(num) if num.is_integer() else num


def _operator_terminal_config(home: Path) -> dict[str, Any]:
    """Read operator-controlled ``terminal:`` settings from managed/launch config only."""
    with contextlib.suppress(Exception):
        from hermes_cli import managed_scope

        managed = managed_scope.load_managed_config()
        if isinstance(managed, dict) and isinstance(managed.get("terminal"), dict):
            return dict(managed["terminal"])

    launch = _resolve_launch_home()
    if home.parent.name == "profiles":
        launch = home.parent.parent
    with contextlib.suppress(Exception):
        from gateway.config_loader import read_yaml_layers

        layered = read_yaml_layers(launch)
        if isinstance(layered, dict) and isinstance(layered.get("terminal"), dict):
            return dict(layered["terminal"])
    return {}


def _safe_docker_network(raw: Any) -> str:
    net = str(raw or "").strip()
    if not net:
        return ""
    lowered = net.lower()
    if lowered == "host" or lowered.startswith("container:"):
        return ""
    return net


def enforce_sandbox_terminal_scope(
    home: Path,
    scope: dict[str, str],
    cfg: Optional[dict[str, Any]] = None,
) -> dict[str, str]:
    """Overwrite terminal scope keys for a sandboxed non-launch profile.

    Profile-editable ``config.yaml`` and ``.env`` values cannot switch the backend off
    ``docker`` or loosen mounts, extra args, env forwarding, image, network, or limits.
    """
    if not is_sandboxed_profile(home):
        return scope

    with contextlib.suppress(Exception):
        from hermes_cli import managed_scope

        managed_scope.apply_managed_overlay(dict(cfg or {}))

    op_terminal = _operator_terminal_config(home)
    image = str(op_terminal.get("docker_image") or DEFAULT_SANDBOX_IMAGE).strip() or DEFAULT_SANDBOX_IMAGE
    cpu = _positive_limit(op_terminal.get("container_cpu"), DEFAULT_SANDBOX_CPU)
    memory = _positive_limit(op_terminal.get("container_memory"), DEFAULT_SANDBOX_MEMORY_MB)
    disk = _positive_limit(op_terminal.get("container_disk"), DEFAULT_SANDBOX_DISK_MB)
    network = _safe_docker_network(op_terminal.get("docker_network"))

    scope["TERMINAL_ENV"] = "docker"
    scope["TERMINAL_DOCKER_IMAGE"] = image
    scope["TERMINAL_DOCKER_IMAGE_PINNED"] = "1"
    scope["TERMINAL_DOCKER_VOLUMES"] = "[]"
    scope["TERMINAL_DOCKER_EXTRA_ARGS"] = "[]"
    scope["TERMINAL_DOCKER_FORWARD_ENV"] = "[]"
    scope["TERMINAL_DOCKER_ENV"] = "{}"
    scope["TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE"] = "false"
    scope["TERMINAL_CONTAINER_PERSISTENT"] = "false"
    scope["TERMINAL_DOCKER_RUN_AS_HOST_USER"] = "false"
    scope["TERMINAL_DOCKER_SNAP_COMPAT"] = "false"
    scope["TERMINAL_DOCKER_SHARED_CONTAINER_KEY"] = ""
    scope["TERMINAL_CONTAINER_CPU"] = str(cpu)
    scope["TERMINAL_CONTAINER_MEMORY"] = str(memory)
    scope["TERMINAL_CONTAINER_DISK"] = str(disk)
    scope.pop("TERMINAL_CWD", None)
    if network:
        scope["TERMINAL_DOCKER_NETWORK"] = network
    else:
        scope.pop("TERMINAL_DOCKER_NETWORK", None)
    return scope


def _is_blocked_override_key(key: Any) -> bool:
    name = str(key)
    return name in _BLOCKED_TASK_OVERRIDE_KEYS or name.endswith("_image")


def sanitize_task_overrides(
    overrides: Optional[dict[str, Any]],
    hermes_home: Optional[Path | str] = None,
) -> dict[str, Any]:
    """Strip task-level overrides that would alter backend, image, or host cwd for a sandboxed profile."""
    if not overrides:
        return {}
    if not is_sandboxed_profile(hermes_home):
        return dict(overrides)
    blocked = [str(k) for k in overrides if _is_blocked_override_key(k)]
    if blocked:
        logger.warning(
            "Ignored task_env_overrides keys %s for sandboxed profile %s",
            sorted(blocked),
            _resolve_home(hermes_home),
        )
    return {k: v for k, v in overrides.items() if not _is_blocked_override_key(k)}


def sandbox_disk_resource_args(*, disk: int, storage_opt_supported: bool, persistent: bool) -> list[str]:
    """Return ``--storage-opt`` when supported, or ``--read-only`` for non-persistent sandboxed
    profiles so a container cannot write unbounded data to the daemon's overlay upperdir."""
    if storage_opt_supported:
        return ["--storage-opt", f"size={disk}m"]
    if not persistent and is_sandboxed_profile():
        return ["--read-only"]
    return []


def sandbox_workspace_tmpfs_size(disk: int) -> str:
    """Return the ``size=`` value for the ``/workspace`` tmpfs mount."""
    if disk > 0 and is_sandboxed_profile():
        return f"{int(disk)}m"
    return "10g"
