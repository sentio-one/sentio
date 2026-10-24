"""Client for connecting to the business gateway for operator messages commands (Task C1)."""

from __future__ import annotations

import logging
from typing import Any

from gateway.business.client import request_business

logger = logging.getLogger(__name__)


async def send_operator_request(action: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Send an operator control request to the business gateway daemon."""
    payload = {"action": action, **(params or {})}
    try:
        resp = await request_business("operator", payload)
        if isinstance(resp, dict):
            return resp
        return {"status": "ok", "raw": resp}
    except Exception as exc:  # health: allow BLE001 -- control client errors return structured failures
        logger.debug("Failed sending operator request: %s", exc, exc_info=True)
        return {"error": str(exc)}
