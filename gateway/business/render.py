"""Immutable rendered delivery plans and exact approval digests (Task B1)."""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import shutil
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from gateway.business.models import (
    BusinessProtocolError,
    DraftRevision,
    Scope,
)

ALLOWED_OPERATIONS = frozenset({"text", "media", "edit", "reaction", "poll", "location"})
DEFAULT_PLAN_TTL_MS = 300000  # 5 minutes


@dataclass(frozen=True)
class DeliveryPart:
    index: int
    operation: str
    payload: dict[str, Any]
    media_hash: str | None = None


@dataclass(frozen=True)
class DeliveryPlan:
    id: str
    revision_id: str
    scope: Scope
    context_version: int
    policy_version: str
    parts: tuple[DeliveryPart, ...]
    expires_at_ms: int
    digest: str
    encrypted_plan_handle: str | None = None


def split_long_message(message: str, max_length: int = 4096) -> list[str]:
    """Split a long text message on newlines or spaces into chunks."""
    text = str(message or "")
    if not text:
        return []
    if len(text) <= max_length:
        return [text]

    chunks = []
    remaining = text
    while len(remaining) > max_length:
        split_at = remaining.rfind("\n", 0, max_length)
        if split_at < max_length // 2:
            split_at = remaining.rfind(" ", 0, max_length)
        if split_at < 1:
            split_at = max_length

        chunks.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()

    if remaining:
        chunks.append(remaining)
    return chunks


def _canonical_json(obj: Any) -> bytes:
    """Format canonical JSON without NaN or Infinity, sorted keys, compact."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest_delivery_parts(
    parts: tuple[DeliveryPart, ...],
    *,
    binding: dict[str, Any],
    hmac_key: bytes | None = None,
) -> str:
    """Compute deterministic SHA-256 (or HMAC-SHA256) digest over binding and delivery parts."""
    scope_val = binding["scope"]
    if hasattr(scope_val, "runtime_profile"):
        scope_dict = {
            "runtime_profile": scope_val.runtime_profile,
            "transport_profile": scope_val.transport_profile,
            "account_id": scope_val.account_id,
            "contact_id": scope_val.contact_id,
        }
    else:
        scope_dict = {
            "runtime_profile": scope_val["runtime_profile"],
            "transport_profile": scope_val["transport_profile"],
            "account_id": scope_val["account_id"],
            "contact_id": scope_val["contact_id"],
        }

    canonical_binding = {
        "scope": scope_dict,
        "revision_id": str(binding["revision_id"]),
        "context_version": int(binding["context_version"]),
        "policy_version": str(binding["policy_version"]),
        "lesson_version": int(binding["lesson_version"]),
        "expires_at_ms": int(binding["expires_at_ms"]),
        "reply_target": binding.get("reply_target"),
        "mentions": sorted(binding.get("mentions") or []),
    }

    canonical_parts = [
        {
            "index": p.index,
            "operation": p.operation,
            "payload": p.payload,
            "media_hash": p.media_hash,
        }
        for p in parts
    ]

    canonical_payload = {
        "binding": canonical_binding,
        "parts": canonical_parts,
    }

    payload_bytes = _canonical_json(canonical_payload)
    if hmac_key is not None:
        hexdigest = hmac.new(hmac_key, payload_bytes, hashlib.sha256).hexdigest()
        return f"hmac-sha256:{hexdigest}"

    hexdigest = hashlib.sha256(payload_bytes).hexdigest()
    return f"sha256:{hexdigest}"


def _capture_attachment(path_str: str, *, capture_dir: Path | None = None, account_id: str = "") -> tuple[str, str]:
    """Validate, hash, and capture attachment bytes. Returns (captured_path, sha256_hash)."""
    if not path_str or not isinstance(path_str, str):
        raise BusinessProtocolError("invalid_attachment")

    # Reject symlinks explicitly
    if os.path.islink(path_str):
        raise BusinessProtocolError("symlinked_attachment")

    if not os.path.exists(path_str) or not os.path.isfile(path_str):
        raise BusinessProtocolError("missing_attachment")

    try:
        with open(path_str, "rb") as f:
            data = f.read()
    except OSError as err:
        raise BusinessProtocolError("unreadable_attachment") from err

    media_hash = f"sha256:{hashlib.sha256(data).hexdigest()}"
    final_path = path_str

    if capture_dir is not None:
        raw_hash = media_hash.split(":", 1)[1]
        dest_dir = capture_dir / account_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_file = dest_dir / raw_hash
        if not dest_file.exists():
            dest_file.write_bytes(data)
        final_path = str(dest_file)

    return final_path, media_hash


def _render_text_parts(text: str, reply_target: str | None, mentions: tuple[str, ...]) -> list[DeliveryPart]:
    chunks = split_long_message(text)
    if not chunks:
        chunks = [""]
    parts: list[DeliveryPart] = []
    for idx, chunk in enumerate(chunks):
        payload: dict[str, Any] = {"text": chunk}
        if idx == 0:
            if reply_target:
                payload["reply_target"] = reply_target
            if mentions:
                payload["mentions"] = list(mentions)
        parts.append(DeliveryPart(index=idx, operation="text", payload=payload, media_hash=None))
    return parts


def _render_media_parts(
    data: dict[str, Any],
    text: str,
    account_id: str,
    reply_target: str | None,
    mentions: tuple[str, ...],
    capture_dir: Path | None,
) -> list[DeliveryPart]:
    attachments = data.get("attachments")
    if not attachments:
        if "path" in data:
            attachments = [{"path": data["path"], "type": data.get("type", "document")}]
        else:
            attachments = []

    if not attachments:
        raise BusinessProtocolError("missing_attachment")

    parts: list[DeliveryPart] = []
    for idx, att in enumerate(attachments):
        att_path = att.get("path", "")
        captured_path, media_hash = _capture_attachment(att_path, capture_dir=capture_dir, account_id=account_id)
        payload = {
            "path": captured_path,
            "type": att.get("type", "document"),
            "caption": text if idx == 0 else "",
        }
        if idx == 0:
            if reply_target:
                payload["reply_target"] = reply_target
            if mentions:
                payload["mentions"] = list(mentions)
        parts.append(DeliveryPart(index=idx, operation="media", payload=payload, media_hash=media_hash))
    return parts


def _render_other_parts(
    operation: str,
    data: dict[str, Any],
    text: str,
    reply_target: str | None,
    mentions: tuple[str, ...],
) -> list[DeliveryPart]:
    payload = copy.deepcopy(data)
    if text:
        payload["text"] = text
    if reply_target:
        payload["reply_target"] = reply_target
    if mentions:
        payload["mentions"] = list(mentions)
    return [DeliveryPart(index=0, operation=operation, payload=payload, media_hash=None)]


async def render_delivery_plan(
    scope: Scope,
    revision: DraftRevision,
    *,
    now_ms: int,
    capture_dir: Path | None = None,
    default_ttl_ms: int = DEFAULT_PLAN_TTL_MS,
    quote_resolver: Callable[[str], Any | None] | None = None,
) -> DeliveryPlan:
    """Render an immutable DeliveryPlan from a DraftRevision without sending."""
    candidate = revision.candidate

    # Credentials rejected until Plan 03 / C3
    if candidate.credential_slots:
        raise BusinessProtocolError("unresolved_credentials")

    operation = candidate.kind
    if operation not in ALLOWED_OPERATIONS:
        raise BusinessProtocolError(f"unsupported_operation: {operation}")

    data = candidate.data or {}
    reply_target = data.get("reply_target") or data.get("reply_to")
    if reply_target is not None and quote_resolver is not None:
        quote = quote_resolver(reply_target)
        if quote is None:
            raise BusinessProtocolError(f"quote_target_unavailable: {reply_target}")

    mentions = tuple(sorted(data.get("mentions") or ()))

    if operation == "text":
        parts = _render_text_parts(candidate.text, reply_target, mentions)
    elif operation == "media":
        parts = _render_media_parts(data, candidate.text, scope.account_id, reply_target, mentions, capture_dir)
    else:
        parts = _render_other_parts(operation, data, candidate.text, reply_target, mentions)

    expires_at_ms = now_ms + default_ttl_ms
    binding = {
        "scope": scope,
        "revision_id": revision.id,
        "context_version": revision.episode.context_version if revision.episode else 1,
        "policy_version": revision.policy_version,
        "lesson_version": revision.lesson_version,
        "expires_at_ms": expires_at_ms,
        "reply_target": reply_target,
        "mentions": mentions,
    }

    digest = digest_delivery_parts(tuple(parts), binding=binding)
    plan_id = str(uuid.uuid4())

    return DeliveryPlan(
        id=plan_id,
        revision_id=revision.id,
        scope=scope,
        context_version=binding["context_version"],
        policy_version=revision.policy_version,
        parts=tuple(parts),
        expires_at_ms=expires_at_ms,
        digest=digest,
        encrypted_plan_handle=None,
    )

