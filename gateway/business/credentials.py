"""Explicit protected credential delivery and plan resolution (Task C3)."""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from agent.vault_store import VaultError, VaultStore, get_vault_store
from gateway.business.models import BusinessDenied, Scope
from gateway.business.permissions import authorize_operation
from gateway.business.render import (
    DEFAULT_PLAN_TTL_MS,
    DeliveryPart,
    DeliveryPlan,
    digest_delivery_parts,
)

if TYPE_CHECKING:
    from gateway.business.models import DraftRevision, Principal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CredentialGrant:
    id: str
    vault_item_id: str
    secret_version: str
    scope: Scope
    expires_at_ms: int
    actor: str


def _compute_secret_version(secret: dict[str, Any]) -> str:
    serialized = json.dumps(secret, sort_keys=True).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()[:16]


def grant_credential(
    principal: Principal,
    scope: Scope,
    *,
    vault_item_id: str,
    expires_at_ms: int,
    vault: VaultStore | None = None,
) -> CredentialGrant:
    """Grant an explicit, scoped credential release by an authorized operator."""
    if principal.runtime_profile != scope.runtime_profile:
        raise BusinessDenied("cross_profile_grant_denied")
    authorize_operation(principal, scope, "credential_grant")

    v = vault or get_vault_store()
    secret = v.resolve_secret(vault_item_id)
    secret_version = _compute_secret_version(secret)
    grant_id = f"cgrant-{uuid.uuid4().hex[:12]}"

    return CredentialGrant(
        id=grant_id,
        vault_item_id=vault_item_id,
        secret_version=secret_version,
        scope=scope,
        expires_at_ms=expires_at_ms,
        actor=principal.subject,
    )


async def prepare_credential_plan(
    scope: Scope,
    revision: DraftRevision,
    *,
    grants: tuple[CredentialGrant, ...],
    vault: VaultStore | None = None,
    now_ms: int | None = None,
) -> DeliveryPlan:
    """Render a protected delivery plan with encrypted payload and HMAC digest."""
    v = vault or get_vault_store()
    current_time = int(time.time() * 1000) if now_ms is None else now_ms
    expires_at_ms = current_time + DEFAULT_PLAN_TTL_MS

    # 1. Materialize secrets inside restricted service scope
    text = revision.candidate.text
    materialized_text = text
    grant_map = {g.scope.contact_id: g for g in grants if g.scope == scope}
    matching_grant = grant_map.get(scope.contact_id)

    if matching_grant:
        secret = v.resolve_secret(matching_grant.vault_item_id)
        # Materialize tokens like [CREDENTIAL:password]
        for slot in revision.candidate.credential_slots:
            val = secret.get(slot, "")
            placeholder = f"[CREDENTIAL:{slot}]"
            if placeholder in materialized_text:
                materialized_text = materialized_text.replace(placeholder, str(val))
            elif slot in secret and str(val) not in materialized_text:
                materialized_text = f"{materialized_text} {val}".strip()

    # 2. Materialized parts
    materialized_parts = (
        DeliveryPart(index=0, operation="text", payload={"text": materialized_text}),
    )

    # 3. Compute HMAC digest over materialized parts
    materialized_digest = digest_delivery_parts(materialized_parts, algorithm="hmac-sha256")

    # 4. Seal encrypted payload with scope binding
    binding = {
        "runtime_profile": scope.runtime_profile,
        "transport_profile": scope.transport_profile,
        "account_id": scope.account_id,
        "contact_id": scope.contact_id,
        "secret_version": matching_grant.secret_version if matching_grant else "",
        "vault_item_id": matching_grant.vault_item_id if matching_grant else "",
    }
    payload = {
        "parts": [
            {
                "index": p.index,
                "operation": p.operation,
                "payload": p.payload,
                "media_hash": p.media_hash,
            }
            for p in materialized_parts
        ]
    }
    handle = v.seal_message_payload(binding, payload)

    # 5. Masked preview parts stored in public database/outbox
    masked_text = text
    masked_parts = (
        DeliveryPart(
            index=0,
            operation="text",
            payload={"text": masked_text, "credential_handle": handle},
        ),
    )

    plan_id = f"cplan-{uuid.uuid4().hex[:12]}"
    return DeliveryPlan(
        id=plan_id,
        revision_id=revision.id,
        scope=scope,
        context_version=revision.number,
        policy_version=revision.policy_version,
        parts=masked_parts,
        expires_at_ms=expires_at_ms,
        digest=materialized_digest,
        encrypted_plan_handle=handle,
    )


def resolve_approved_plan(
    scope: Scope,
    plan: DeliveryPlan,
    *,
    vault: VaultStore | None = None,
) -> tuple[DeliveryPart, ...]:
    """Resolve and unseal materialized delivery parts for physical dispatch."""
    if not plan.encrypted_plan_handle:
        return plan.parts

    v = vault or get_vault_store()
    try:
        data = v.open_message_payload(plan.encrypted_plan_handle)
    except VaultError as exc:
        logger.warning("Failed to open sealed plan payload %s: %s", plan.encrypted_plan_handle, exc)
        return ()

    binding = data.get("binding", {})
    # Recipient verification: exact scope match required
    if (
        binding.get("runtime_profile") != scope.runtime_profile
        or binding.get("transport_profile") != scope.transport_profile
        or binding.get("account_id") != scope.account_id
        or binding.get("contact_id") != scope.contact_id
    ):
        logger.warning("Sealed payload recipient mismatch for scope %s vs binding %s", scope, binding)
        return ()

    # Secret rotation verification: check current vault secret version
    vault_item_id = binding.get("vault_item_id")
    expected_version = binding.get("secret_version")
    if vault_item_id and expected_version:
        try:
            current_secret = v.resolve_secret(vault_item_id)
            current_version = _compute_secret_version(current_secret)
            if current_version != expected_version:
                logger.warning(
                    "Vault item %s secret was rotated (version %s != %s)",
                    vault_item_id,
                    current_version,
                    expected_version,
                )
                return ()
        except VaultError as exc:
            logger.warning("Vault item %s resolution failed: %s", vault_item_id, exc)
            return ()

    raw_parts = data.get("payload", {}).get("parts", [])
    resolved = tuple(
        DeliveryPart(
            index=rp["index"],
            operation=rp["operation"],
            payload=rp["payload"],
            media_hash=rp.get("media_hash"),
        )
        for rp in raw_parts
    )
    return resolved
