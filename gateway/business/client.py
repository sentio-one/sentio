"""Authenticated local client for business control socket operations (Task B4)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from gateway.business.models import BusinessDenied, BusinessProtocolError


def request_business(
    home: Path,
    *,
    operation: str,
    params: dict[str, Any],
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Execute an authenticated request over the gateway control socket."""
    home = Path(home)
    # Discovery validates a named profile's multiplex owner without starting a gateway.
    if not (home / "gateway.sock").exists() and not (home / "gateway.sock.path").exists():
        from hermes_cli.gateway_runtime import control_home_for, discover_gateway_endpoint
        from hermes_constants import profile_name_for_home
        selected = profile_name_for_home(home)
        if selected and selected != "default":
            params = {"runtime_profile": selected, **params}
        observed = discover_gateway_endpoint(home, timeout=min(timeout, 2.0))
        home = control_home_for(home, observed.endpoint)
    request_dict = {
        "protocol": 1,
        "id": 1,
        "verb": "business.request",
        "params": {
            "operation": operation,
            "params": params,
        },
    }
    request_bytes = json.dumps(request_dict).encode("utf-8") + b"\n"
    if len(request_bytes) > 65536:
        raise BusinessProtocolError("request_too_large")

    deadline = time.monotonic() + timeout

    if os.name == "nt":
        from gateway.runtime_bootstrap_windows import query_runtime_control
        raw = query_runtime_control(home, request_bytes, timeout)
    else:
        from hermes_cli.gateway_runtime_discovery import connect_private
        from gateway.control_socket import _read_response_line

        try:
            with connect_private(home, timeout) as peer:
                peer.sendall(request_bytes)

                def read():
                    peer.settimeout(max(0.001, deadline - time.monotonic()))
                    return peer.recv(65536)

                raw = _read_response_line(read, deadline)
        except (OSError, TimeoutError) as exc:
            raise BusinessProtocolError("offline") from exc

    if not raw:
        raise BusinessProtocolError("empty_response")

    try:
        response = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise BusinessProtocolError("invalid_response") from exc

    if response.get("ok") is not True:
        err = response.get("error", "unknown_error")
        if err == "permission_denied":
            raise BusinessDenied()
        raise BusinessProtocolError(str(err))

    return response.get("result", {})
