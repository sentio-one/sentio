"""Durable business admission ledger: every inbound is recorded before a plugin hook can skip it.

Observation is strictly additive. It never drops, rewrites or reorders an event, so shadow mode
cannot change legacy dispatch; the runner exposes the store as ``_business_store`` only for a
profile whose business mode is not ``off``, and every other profile takes the ``None`` fast path.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from gateway.business.models import BusinessConflict, ContextEvent, EpisodeRef, Scope
from gateway.session_identity import identity_of

logger = logging.getLogger(__name__)

_VERDICTS = frozenset({"no_reply", "escalate", "failed", "refused"})
_NEEDS_HUMAN = frozenset({"escalate", "failed"})


def _source_id(event: Any) -> str:
    source = event.source
    platform = getattr(getattr(source, "platform", None), "value", "unknown")
    key = event.message_id
    if not key:
        # A delivery without a provider id still needs one stable key across replay.
        key = hashlib.sha256(f"{source.chat_id}\x00{event.text}\x00{event.timestamp.isoformat()}".encode()).hexdigest()
    return f"{platform}:{source.chat_id}:{key}:{event.edit_revision or ''}"


def _classify(event: Any, source_id: str) -> ContextEvent:
    meta = event.metadata or {}
    if meta.get("reaction_target_id"):
        return ContextEvent(source_id, "reaction", "customer", str(meta.get("reaction_emoji") or ""),
                            str(meta["reaction_target_id"]), "typed reaction")
    if meta.get("whatsapp_from_owner") is True:
        return ContextEvent(source_id, "owner_reply", "owner", event.text, event.reply_to_message_id,
                            "owner-typed message")
    if getattr(getattr(event, "message_type", None), "value", "") in ("voice", "audio"):
        # The transcript is provenance supplied by the adapter; a placeholder body is never text.
        transcript = meta.get("voice_transcript")
        return ContextEvent(source_id, "voice", "customer", transcript if isinstance(transcript, str) else "",
                            event.reply_to_message_id, "recognized voice transcript" if transcript else "no transcript")
    return ContextEvent(source_id, "message", "customer", event.text, event.reply_to_message_id, "inbound message")


def _resolve(runner: Any, event: Any) -> tuple[Any, Scope, str] | None:
    """``(store, scope, source_id)`` for the receiving account, never the launch default bot."""
    store = getattr(runner, "_business_store", None)
    source = getattr(event, "source", None)
    if store is None or source is None:
        return None
    from gateway.business.turns import is_business_mode_active
    if not is_business_mode_active(runner, source):
        return None
    identity = identity_of(source)
    if identity is not None:
        runtime, transport = identity.runtime_profile, identity.transport_profile
    else:
        runtime = transport = getattr(source, "profile", None) or "default"
    identifiers = [value for value in (source.chat_id, source.user_id) if value]
    if not identifiers:
        return None
    try:
        # One WhatsApp account per transport profile: the profile owns the adapter's credential.
        scope = store.bind_contact(runtime, transport, transport, identifiers)
    except BusinessConflict:
        logger.warning("business ingress held: contact aliases collide for chat %s", source.chat_id)
        return None
    return store, scope, _source_id(event)


async def observe_ingress(runner: Any, event: Any) -> EpisodeRef | None:
    """Record *event* durably before any hook can skip it; None when business mode is not configured."""
    resolved = _resolve(runner, event)
    if resolved is None:
        return None
    store, scope, source_id = resolved
    return store.record_ingress(scope, _classify(event, source_id), source_id=source_id)


async def finish_ingress(runner: Any, event: Any, *, verdict: str, reason: str) -> None:
    """Attach the outcome of an observed event; a skipped or failed message becomes a human task."""
    if verdict not in _VERDICTS:
        raise ValueError(f"unknown ingress verdict: {verdict}")
    resolved = _resolve(runner, event)
    if resolved is None:
        return
    store, scope, source_id = resolved
    episode = store.episode_for_source(scope, source_id)
    store.record_verdict(scope, episode.id, kind=verdict, reason=reason)
    if verdict in _NEEDS_HUMAN:
        store.put_task(scope, dedupe_key=f"episode:{episode.id}", reason=reason, refs=(episode.id,))
