"""Async client for authenticated operator control requests."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from gateway.business.client import request_business
from gateway.business.models import BusinessDenied, BusinessProtocolError
from hermes_constants import get_hermes_home


async def send_operator_request(action: str, params: dict[str, Any] | None = None, *, home: Path | None = None) -> dict[str, Any]:
    """Run the blocking socket client away from the gateway event loop."""
    try:
        return await asyncio.to_thread(request_business, home or get_hermes_home(), operation=f'operator.{action}', params=params or {}, timeout=120)
    except (BusinessDenied, BusinessProtocolError) as exc:
        return {'ok':False,'error':str(exc) or 'permission_denied'}
