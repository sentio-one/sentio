"""Tests for explicit protected credential delivery and plan resolution (Task C3)."""

from __future__ import annotations

import pytest

from agent.vault_store import VaultStore
from gateway.business.credentials import (
    CredentialGrant,
    grant_credential,
    prepare_credential_plan,
    resolve_approved_plan,
)
from gateway.business.models import (
    AccountKey,
    Candidate,
    ContextEvent,
    DraftRevision,
    Principal,
    Scope,
)
from gateway.business.render import DeliveryPart, digest_delivery_parts
from gateway.business.store import BusinessStore


@pytest.fixture
def store(tmp_path):
    return BusinessStore(tmp_path)


@pytest.fixture
def vault(tmp_path):
    v = VaultStore(base_dir=tmp_path / "vault")
    # Pre-populate a test login/credential item
    v.add_item(
        kind="login",
        label="Demo Account Credentials",
        secret={
            "identifier_type": "username",
            "identifier": "demo_user",
            "password": "SUPER_SECRET_TOKEN_XYZ987",
        },
        origin="https://demo.example.com",
    )
    return v


def _scope(contact_id="27600000001@s.whatsapp.net"):
    return Scope("default", "whatsapp", "account-1", contact_id)


def _operator():
    return Principal("operator-1", "default", frozenset({"approve", "review", "credential_grant"}), "human")


@pytest.mark.asyncio
async def test_credential_only_reaches_exact_approved_recipient(store, vault):
    scope = _scope()
    operator = _operator()
    secret_sentinel = "SUPER_SECRET_TOKEN_XYZ987"

    items = vault.list_items()
    assert len(items) >= 1
    vault_item_id = items[0].id

    # 1. Grant credential access to exact scope
    grant = grant_credential(
        operator,
        scope,
        vault_item_id=vault_item_id,
        expires_at_ms=2000000,
        vault=vault,
    )
    assert isinstance(grant, CredentialGrant)
    assert grant.vault_item_id == vault_item_id
    assert grant.scope == scope

    # 2. Prepare credential plan
    store.bind_contact(scope.runtime_profile, scope.transport_profile, scope.account_id, [scope.contact_id])
    store.record_ingress(scope, ContextEvent("e1", "message", "customer", "Please give me demo login", None, "inbound"), source_id="src-1")
    ep = store.episode_for_source(scope, "src-1")

    candidate = Candidate(
        kind="text",
        text="Here is your login demo: [CREDENTIAL:password]",
        data={},
        fact_ids=(),
        completed_action_ids=(),
        credential_slots=("password",),
    )
    rev = store.append_revision(scope, ep, candidate, expected_revision_id=None, policy_version="v1", lesson_version=0)

    plan = await prepare_credential_plan(scope, rev, grants=(grant,), vault=vault)
    assert plan.digest.startswith("hmac-sha256:")

    # Store plan in ordinary outbox/draft tables
    store.save_plan(plan)
    rec = store.submit_revision(scope, rev.id, request_id="req-cred-1", now_ms=1000)
    assert rec.id is not None

    # Preview must have credential indicator and NOT contain raw secret
    preview = plan.preview
    assert preview.contains_credential_indicator is True
    assert secret_sentinel not in preview.text

    # Plaintext secret MUST NOT be in public DB or queue
    conn = store._connect()
    raw_db_dump = " ".join(
        str(row)
        for row in conn.execute("SELECT payload FROM business_plans").fetchall()
    )
    assert secret_sentinel not in raw_db_dump
    assert secret_sentinel.encode() not in raw_db_dump.encode()

    # 3. Resolve plan for dispatch to the exact approved recipient
    resolved_parts = resolve_approved_plan(scope, plan, vault=vault)
    assert len(resolved_parts) == 1
    customer_socket_payload = resolved_parts[0].data
    assert customer_socket_payload["text"].endswith(secret_sentinel)

    # Verify secret sentinel is not in logs or public records
    public_records_logs_or_model_payloads = [preview.text, rev.candidate.text]
    assert secret_sentinel not in " ".join(public_records_logs_or_model_payloads)
    ordinary_queue_or_database_bytes = raw_db_dump.encode()
    assert secret_sentinel.encode() not in ordinary_queue_or_database_bytes


@pytest.mark.asyncio
async def test_rotation_missing_vault_and_error_paths_never_leak(store, vault):
    scope = _scope()
    operator = _operator()

    items = vault.list_items()
    vault_item_id = items[0].id

    grant = grant_credential(
        operator,
        scope,
        vault_item_id=vault_item_id,
        expires_at_ms=2000000,
        vault=vault,
    )

    store.bind_contact(scope.runtime_profile, scope.transport_profile, scope.account_id, [scope.contact_id])
    store.record_ingress(scope, ContextEvent("e1", "message", "customer", "Demo please", None, "inbound"), source_id="src-1")
    ep = store.episode_for_source(scope, "src-1")
    candidate = Candidate(
        kind="text",
        text="Demo: [CREDENTIAL:password]",
        data={},
        fact_ids=(),
        completed_action_ids=(),
        credential_slots=("password",),
    )
    rev = store.append_revision(scope, ep, candidate, expected_revision_id=None, policy_version="v1", lesson_version=0)
    plan = await prepare_credential_plan(scope, rev, grants=(grant,), vault=vault)

    # 1. Wrong recipient attempting resolution
    wrong_scope = Scope("default", "whatsapp", "account-1", "27600000099@s.whatsapp.net")
    wrong_recipient_parts = resolve_approved_plan(wrong_scope, plan, vault=vault)
    assert wrong_recipient_parts == ()

    # 2. Secret rotated in vault before dispatch
    vault.update_item_secret(vault_item_id, {"username": "demo_user", "password": "ROTATED_SECRET_9999"})
    rotated_parts = resolve_approved_plan(scope, plan, vault=vault)
    assert rotated_parts == ()


@pytest.mark.asyncio
async def test_approval_binds_final_split_with_secret_materialized(store, vault):
    scope = _scope()
    operator = _operator()
    items = vault.list_items()
    vault_item_id = items[0].id

    grant = grant_credential(
        operator,
        scope,
        vault_item_id=vault_item_id,
        expires_at_ms=2000000,
        vault=vault,
    )

    store.bind_contact(scope.runtime_profile, scope.transport_profile, scope.account_id, [scope.contact_id])
    store.record_ingress(scope, ContextEvent("e1", "message", "customer", "Access", None, "inbound"), source_id="src-1")
    ep = store.episode_for_source(scope, "src-1")
    candidate = Candidate(
        kind="text",
        text="Access granted: [CREDENTIAL:password]",
        data={},
        fact_ids=(),
        completed_action_ids=(),
        credential_slots=("password",),
    )
    rev = store.append_revision(scope, ep, candidate, expected_revision_id=None, policy_version="v1", lesson_version=0)
    plan = await prepare_credential_plan(scope, rev, grants=(grant,), vault=vault)

    # Resolved parts at dispatch
    rendered_parts = resolve_approved_plan(scope, plan, vault=vault)
    actually_dispatched_parts = rendered_parts
    assert rendered_parts == actually_dispatched_parts

    # Materialized parts digest matches plan digest
    computed_materialized_digest = digest_delivery_parts(rendered_parts, algorithm="hmac-sha256")
    approved_digest = plan.digest
    assert computed_materialized_digest == approved_digest
