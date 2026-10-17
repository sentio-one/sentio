"""Container healthcheck for a per-company gateway: green only while the dashboard port is open AND the access
policy is active.

A malformed or missing managed config is FAIL-OPEN upstream (the policy silently turns off and every signed-in
user becomes an admin). Here it turns the container unhealthy instead, so Traefik stops routing to it.
Run inside the image: ``/opt/hermes/.venv/bin/python /opt/healthcheck.py`` (cwd /opt/hermes).
"""

from __future__ import annotations

import socket
import sys

# The image installs the repository at its WORKDIR; the compose file pins the dashboard port to match.
REPO_DIR = "/opt/hermes"
DASHBOARD_PORT = 9119
sys.path.insert(0, REPO_DIR)


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except OSError:
        return False


def policy_active() -> bool:
    """True when the gateway's own config loader sees a configured access policy (managed scope included)."""
    from gateway.config import load_gateway_config

    return load_gateway_config().access.configured


def main(port: int | None = None) -> int:
    port = DASHBOARD_PORT if port is None else port
    if not port_open(port):
        print(f"unhealthy: dashboard port {port} is not accepting connections", file=sys.stderr)
        return 1
    if not policy_active():
        print("unhealthy: no access policy is active (managed config missing or invalid)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
