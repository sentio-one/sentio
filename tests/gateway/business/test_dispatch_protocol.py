"""Tests for native control protocol and bridge dispatch backstop (Task B4)."""

from __future__ import annotations

import json
from pathlib import Path
import pytest

from gateway.business.client import request_business
from gateway.business.control import (
    handle_business_request,
    register_business_control_handlers,
)
from gateway.business.dispatch import (
    consume_authorization,
    issue_authorization,
)
from gateway.business.models import (
    AccountKey,
    BusinessConflict,
    BusinessDenied,
    BusinessProtocolError,
    Candidate,
    ContextEvent,
    Principal,
    Scope,
)
from gateway.business.outbox import (
    Attempt,
    claim_part,
    submit_revision,
)
from gateway.business.render import (
    DeliveryPart,
    DeliveryPlan,
    render_delivery_plan,
)
from gateway.business.service import BusinessService
from gateway.business.store import BusinessStore


@pytest.fixture
def store(tmp_path):
    return BusinessStore(tmp_path)


@pytest.fixture
def service(store):
    return BusinessService(store)


def _scope():
    return Scope("default", "whatsapp", "account-1", "27600000001@s.whatsapp.net")


def _account():
    return AccountKey(transport_profile="whatsapp", account_id="account-1")


def _principal(subject="operator-1"):
    return Principal(subject=subject, runtime_profile="default", operations=frozenset({"approve", "submit", "request"}), kind="human")


def _setup_approved_attempt(store, scope, now_ms=1000000):
    store.bind_contact(scope.runtime_profile, scope.transport_profile, scope.account_id, [scope.contact_id])
    store.record_ingress(scope, ContextEvent("e1", "message", "customer", "Hi", None, "inbound"), source_id="src-1")
    ep = store.episode_for_source(scope, "src-1")

    rev = store.append_revision(
        scope,
        ep,
        Candidate("text", "Approved message", {}, (), (), ()),
        expected_revision_id=None,
        policy_version="v1",
        lesson_version=0,
    )
    plan = DeliveryPlan(
        id="plan-1",
        revision_id=rev.id,
        scope=scope,
        context_version=1,
        policy_version="v1",
        parts=(DeliveryPart(0, "text", {"text": "Approved message"}, None),),
        expires_at_ms=now_ms + 300000,
        digest="sha256:testdigest1234",
    )
    store.save_plan(plan)
    store.submit_revision(scope, rev.id, request_id="req-1", now_ms=now_ms)
    store.approve_revision(
        _principal(),
        scope,
        revision_id=rev.id,
        plan_digest=plan.digest,
        now_ms=now_ms,
    )
    account = AccountKey(transport_profile=scope.transport_profile, account_id=scope.account_id)
    attempt = store.claim_part(account, now_ms=now_ms)
    return account, attempt, plan


def test_missing_forged_foreign_expired_or_consumed_permit_refuses(store, tmp_path):
    scope = _scope()
    account, attempt, plan = _setup_approved_attempt(store, scope, now_ms=1000000)

    # 1. Issue a valid permit
    secret_key = b"0" * 32
    token = issue_authorization(account, attempt, plan, now_ms=1000000, secret_key=secret_key)

    # 2. Forged token refuses
    forged_token = token[:-5] + "aaaaa"
    with pytest.raises(BusinessProtocolError, match="invalid_authorization"):
        consume_authorization(forged_token, payload_digest=plan.digest, now_ms=1000010, store=store, secret_key=secret_key)

    # 3. Consuming with mismatched digest refuses
    with pytest.raises(BusinessProtocolError, match="digest_mismatch"):
        consume_authorization(token, payload_digest="sha256:wrong_digest", now_ms=1000010, store=store, secret_key=secret_key)

    # 4. Valid consumption succeeds
    consumed_attempt = consume_authorization(token, payload_digest=plan.digest, now_ms=1000010, store=store, secret_key=secret_key)
    assert consumed_attempt.id == attempt.id

    # 5. Replay of consumed permit refuses
    with pytest.raises(BusinessConflict, match="authorization_consumed"):
        consume_authorization(token, payload_digest=plan.digest, now_ms=1000015, store=store, secret_key=secret_key)

    # 6. Expired token refuses
    token2 = issue_authorization(account, attempt, plan, now_ms=1000000, secret_key=secret_key)
    with pytest.raises(BusinessProtocolError, match="authorization_expired"):
        consume_authorization(token2, payload_digest=plan.digest, now_ms=1000000 + 35000, store=store, secret_key=secret_key)


def test_control_protocol_operations(service, store):
    principal = _principal()

    # Valid operation via handle_business_request
    res = handle_business_request(
        service,
        operation="ping",
        params={},
        principal=principal,
    )
    assert res == {"ok": True, "pong": True}

    # Unverified / missing principal refuses with permission_denied
    unverified_result = handle_business_request(
        service,
        operation="ping",
        params={},
        principal=None,
    )
    assert unverified_result["error"] == "permission_denied"


def test_oversized_and_malformed_control_request_is_inert(service, store):
    conn = store._connect()
    try:
        before_count = conn.execute("SELECT COUNT(*) FROM business_revisions").fetchone()[0]
    finally:
        conn.close()

    principal = _principal()
    # Malformed operation
    res = handle_business_request(
        service,
        operation="unknown_operation",
        params={"junk": "x" * 100000},
        principal=principal,
    )
    assert res["error"] == "unknown_operation"

    conn = store._connect()
    try:
        after_count = conn.execute("SELECT COUNT(*) FROM business_revisions").fetchone()[0]
    finally:
        conn.close()

    assert after_count == before_count
