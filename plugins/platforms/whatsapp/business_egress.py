"""Native submission before adapter formatting, with durable receipts for callers."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import uuid
from typing import Any

from gateway.business.models import Candidate, Scope
from gateway.platforms.base import SendResult
from gateway.whatsapp_identity import to_whatsapp_jid

def _candidate(operation: str, args: tuple, kwargs: dict) -> Candidate:
    def value(position, name, default=None):
        return args[position] if len(args) > position else kwargs.get(name, default)
    kind, text, data = _BUILDERS[operation](value, kwargs)
    return Candidate(kind, text, data, (), (), ())


def _send_parts(value, kwargs):
    reply = value(2, "reply_to")
    return "text", value(1, "content", ""), ({"reply_target": reply} if reply else {})


def _edit_parts(value, kwargs):
    return "edit", value(2, "content", ""), {"message_id": value(1, "message_id")}


def _media_parts(value, kwargs):
    return ("media", value(3, "caption", "") or "",
            {"attachments": [{"path": value(1, "file_path"), "type": value(2, "media_type", "document")}]})


def _poll_parts(value, kwargs):
    return ("poll", value(1, "question", ""),
            {"options": value(2, "options", []), "selectable_count": kwargs.get("selectable_count", 1)})


def _location_parts(value, kwargs):
    data = {"latitude": value(1, "latitude"), "longitude": value(2, "longitude")}
    data.update({k: kwargs[k] for k in ("name", "address") if kwargs.get(k)})
    return "location", "", data


_BUILDERS = {"send": _send_parts, "edit_message": _edit_parts, "_send_media_to_bridge": _media_parts,
             "send_poll": _poll_parts, "send_location": _location_parts}


async def maybe_submit(adapter: Any, operation: str, args: tuple, kwargs: dict) -> SendResult | None:
    if operation not in _BUILDERS:
        return None
    service = getattr(adapter, "_business_service", None)
    extra = getattr(getattr(adapter, "config", None), "extra", {}) or {}
    requested = (extra.get("business") or {}).get("mode") == "supervised"
    latch = getattr(adapter, "_session_path", None)
    latched = bool(latch and (latch / "business-latch.json").exists())
    if service is None and not requested and not latched:
        return None
    profile = getattr(adapter, "_hermes_profile_name", None) or "default"
    chat_id = args[0] if args else kwargs.get("chat_id", "")
    scope = Scope(profile, profile, profile, to_whatsapp_jid(chat_id))
    if service:
        cfg = service.resolve_config(scope)
        if cfg and cfg.mode in ("off", "shadow") and not latched:
            return None
    candidate = _candidate(operation, args, kwargs)
    if candidate.kind == "media":
        from gateway.business.render import capture_producer_media
        owner_home = service.store.db_path.parent if service else adapter._profile_home
        candidate.data["attachments"] = [capture_producer_media(item["path"], owner_home, item["type"])
                                         for item in candidate.data["attachments"]]
    request_id = str(uuid.uuid4())
    try:
        if service:
            from gateway.business.transport import submit_external
            receipt = await submit_external(scope, candidate, request_id=request_id, source_kind="adapter",
                                            service=service, main_runtime=getattr(service, "main_runtime", None))
            payload = asdict(receipt)
        else:
            from gateway.business.client import request_business
            payload = await asyncio.to_thread(request_business, adapter._profile_home,
                        operation="business.submit_candidate", params={"scope": asdict(scope), "candidate": asdict(candidate),
                        "request_id": request_id, "source_kind": "adapter"})
            if payload.get("ok") is False:
                raise ValueError(payload.get("error", "business_unavailable"))
        return SendResult(success=False, retryable=False, business_receipt=payload,
                          error=payload.get("reason") or "business_approval_required")
    except Exception as error:  # health: allow BLE001 -- any submission failure must fail closed as a held receipt, never fall through to a raw send
        return SendResult(success=False, retryable=False, error=str(error),
                          business_receipt={"id": request_id, "state": "held", "revision_id": None, "reason": str(error)})

async def submit_standalone(home, latch, chat_id, message, media, caption, mentions, force_document):
    """Standalone tools use the authenticated local submission API when latched."""
    import json
    from gateway.business.client import request_business
    from hermes_constants import profile_name_for_home
    record = json.loads(latch.read_text(encoding="utf-8-sig"))
    runtime = profile_name_for_home(home) or "default"
    scope = Scope(runtime, record["transport_profile"], record["account_id"], chat_id)
    if media:
        from .adapter import _bridge_media_type
        from gateway.business.render import capture_producer_media
        attachments = [capture_producer_media(item, record.get("home") or home, _bridge_media_type(item, voice, force_document))
                       for item, voice in media]
        candidate = Candidate("media", caption or message or "", {"attachments": attachments}, (), (), ())
    else:
        candidate = Candidate("text", message or "", {}, (), (), ())
    if mentions:
        candidate.data["mentions"] = mentions
    result = await asyncio.to_thread(request_business, record.get("home") or home,
                operation="business.submit_candidate", params={"runtime_profile": runtime, "scope": asdict(scope),
                "candidate": asdict(candidate), "request_id": str(uuid.uuid4()), "source_kind": "standalone"})
    if result.get("ok") is False:
        return {"success": False, "error": result.get("error"), "retryable": False}
    return {"success": False, "retryable": False, "business_receipt": result,
            "error": result.get("reason") or "business_approval_required"}


def latch_if_supervised(adapter: Any) -> None:
    """Persist the account latch at connect when the adapter is configured for supervised mode."""
    business = (adapter.config.extra or {}).get("business") or {}
    if business.get("mode") != "supervised":
        return
    from gateway.business.models import AccountKey
    from gateway.business.worker import latch_bridge_account
    profile = getattr(adapter, "_hermes_profile_name", None) or "default"
    runner = getattr(adapter, "gateway_runner", None)
    home = getattr(runner, "hermes_home", None) or adapter._profile_home
    latch_bridge_account(adapter, AccountKey(profile, profile), home)
