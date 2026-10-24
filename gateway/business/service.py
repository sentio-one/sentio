"""Orchestrating BusinessService for native WhatsApp supervised drafting and shadow evaluation."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Callable

from gateway.business.config import BusinessConfig, load_business_config
from gateway.business.context import build_context
from gateway.business.decision import decision_from_result
from gateway.business.models import (
    EpisodeRef,
    Receipt,
    Scope,
    WorkDecision,
)
from gateway.business.policy import collect_policy
from gateway.business.review import prepare_draft
from gateway.business.store import BusinessStore

logger = logging.getLogger(__name__)


class BusinessService:
    """Wires admission, policy, context, composition, review, and task tracking."""

    def __init__(
        self,
        store: BusinessStore,
        *,
        config_resolver: Callable[[str | Scope], BusinessConfig | None] | BusinessConfig | None = None,
        runtime_scope: Any = None,
    ):
        self.store = store
        self._config_resolver = config_resolver
        self.runtime_scope = runtime_scope

    def resolve_config(self, scope: Scope | str) -> BusinessConfig | None:
        if isinstance(self._config_resolver, BusinessConfig):
            return self._config_resolver
        if callable(self._config_resolver):
            try:
                cfg = self._config_resolver(scope)
                if isinstance(cfg, BusinessConfig):
                    return cfg
            except TypeError:
                if isinstance(scope, Scope):
                    return self._config_resolver(scope.runtime_profile)
                raise
        # Default fallback: check config_readonly
        try:
            from hermes_cli.config import load_config_readonly

            cfg_dict = load_config_readonly()
            business_dict = (
                ((cfg_dict.get("platforms") or {}).get("whatsapp") or {}).get("extra") or {}
            ).get("business") or {}
            return load_business_config(business_dict, supervised_latch=False)
        except Exception as exc:
            logger.debug("Business config resolution fallback failed: %s", exc, exc_info=True)
            return None

    async def handle_turn(
        self,
        scope: Scope,
        episode: EpisodeRef,
        result: dict[str, Any],
        *,
        main_runtime: dict[str, Any],
    ) -> Receipt:
        """Handle one working agent turn under configured business mode."""
        config = self.resolve_config(scope)
        if config is None or config.mode == "off":
            return Receipt(
                id=str(uuid.uuid4()),
                state="failed",
                revision_id=None,
                reason="business_mode_off",
            )

        # Runtime failure bypasses both shadow decision and composition
        if result.get("failed") is True or bool(result.get("error")):
            error_msg = str(result.get("error") or result.get("final_response") or "turn_failed")
            self.store.record_verdict(scope, episode.id, kind="failed", reason=error_msg)
            self.store.put_task(scope, dedupe_key=f"episode:{episode.id}", reason=error_msg, refs=(episode.id,))
            return Receipt(id=str(uuid.uuid4()), state="failed", revision_id=None, reason=error_msg)

        policy = await collect_policy(scope, episode)
        snapshot = build_context(self.store, scope, episode, policy=policy, lesson_version=0)

        if config.mode == "shadow":
            from gateway.business.turns import shadow_decision

            decision = await shadow_decision(snapshot, result, main_runtime=main_runtime)
        else:
            decision = decision_from_result(result)

        if decision.kind == "failed":
            self.store.record_verdict(scope, episode.id, kind="failed", reason=decision.reason)
            self.store.put_task(scope, dedupe_key=f"episode:{episode.id}", reason=decision.reason, refs=(episode.id,))
            return Receipt(id=str(uuid.uuid4()), state="failed", revision_id=None, reason=decision.reason)

        if decision.kind == "no_reply":
            self.store.record_verdict(scope, episode.id, kind="no_reply", reason=decision.reason)
            return Receipt(id=str(uuid.uuid4()), state="no_reply", revision_id=None, reason=decision.reason)

        if decision.kind == "escalate":
            self.store.record_verdict(scope, episode.id, kind="escalate", reason=decision.reason)
            self.store.put_task(scope, dedupe_key=f"episode:{episode.id}", reason=decision.reason, refs=(episode.id,))
            return Receipt(id=str(uuid.uuid4()), state="escalate", revision_id=None, reason=decision.reason)

        # decision.kind == "compose"
        receipt = await prepare_draft(
            self.store,
            snapshot,
            decision,
            policy=policy,
            main_runtime=main_runtime,
        )
        return receipt
