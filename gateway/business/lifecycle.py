"""Gateway lifecycle management for the native business service."""

from __future__ import annotations

import logging
from typing import Any

from gateway.business.config import BusinessConfig, load_business_config
from gateway.business.service import BusinessService
from gateway.business.store import BusinessStore
from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)


def _extract_raw_business_config(runner: Any) -> dict[str, Any]:
    config = getattr(runner, "config", None)
    platforms = getattr(config, "platforms", {}) or {}
    if isinstance(platforms, dict):
        whatsapp = platforms.get("whatsapp", {}) or {}
        if isinstance(whatsapp, dict):
            extra = whatsapp.get("extra", {}) or {}
            if isinstance(extra, dict) and "business" in extra:
                return extra["business"] or {}

    try:
        from hermes_cli.config import load_config_readonly

        cfg_dict = load_config_readonly()
        return (
            ((cfg_dict.get("platforms") or {}).get("whatsapp") or {}).get("extra") or {}
        ).get("business") or {}
    except Exception as exc:
        logger.debug("Config extraction fallback failed: %s", exc, exc_info=True)
        return {}


async def start_business_service(runner: Any) -> BusinessService | None:
    """Start BusinessService on *runner* if configured; None when off or unconfigured."""
    raw = _extract_raw_business_config(runner)
    if not raw or raw.get("mode", "off") == "off":
        runner._business_store = None
        runner._business_service = None
        return None

    try:
        business_config = load_business_config(raw, supervised_latch=False)
    except Exception as exc:
        logger.warning("Invalid business configuration, disabling business service: %s", exc, exc_info=True)
        runner._business_store = None
        runner._business_service = None
        return None

    if not business_config.enabled:
        runner._business_store = None
        runner._business_service = None
        return None

    store = getattr(runner, "_business_store", None)
    if store is None:
        home = getattr(runner, "hermes_home", None) or get_hermes_home()
        store = BusinessStore(home)
        runner._business_store = store

    service = BusinessService(
        store,
        config_resolver=lambda scope: business_config,
    )
    runner._business_service = service
    logger.info("Native WhatsApp business service started (mode=%s)", business_config.mode)

    control_server = getattr(runner, "_control_server", None)
    if control_server is not None:
        try:
            from gateway.business.control import register_business_control_handlers
            register_business_control_handlers(control_server, service)
        except Exception as exc:
            logger.warning("Failed to register business control handlers: %s", exc, exc_info=True)

    return service


def stop_business_service(runner: Any) -> None:
    """Stop the business service and release runner references."""
    runner._business_service = None
    runner._business_store = None
