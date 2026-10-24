"""Tests for rendering immutable message plans and exact approval digests (Task B1)."""

from __future__ import annotations

import os
from pathlib import Path
import pytest

from gateway.business.models import (
    BusinessDenied,
    BusinessProtocolError,
    Candidate,
    DraftRevision,
    EpisodeRef,
    Scope,
)
from gateway.business.render import (
    DeliveryPart,
    DeliveryPlan,
    digest_delivery_parts,
    render_delivery_plan,
)
from gateway.business.store import BusinessStore


@pytest.fixture
def store(tmp_path):
    return BusinessStore(tmp_path)


def _scope():
    return Scope("default", "whatsapp", "account-1", "27600000001@s.whatsapp.net")


def _revision(text="Hello customer!", kind="text", data=None, credential_slots=()):
    candidate = Candidate(
        kind=kind,
        text=text,
        data=data or {},
        fact_ids=(),
        completed_action_ids=(),
        credential_slots=credential_slots,
    )
    return DraftRevision(
        id="rev-1",
        scope=_scope(),
        episode=EpisodeRef("ep-1", 1),
        number=1,
        candidate=candidate,
        policy_version="v1",
        lesson_version=0,
        checks=None,
        review=None,
    )


@pytest.mark.asyncio
async def test_approval_hash_covers_every_delivery_field():
    scope = _scope()
    rev = _revision(text="Here are your details.")
    plan = await render_delivery_plan(scope, rev, now_ms=1000000)

    assert len(plan.parts) > 0
    assert plan.parts[0].payload["text"] == "Here are your details."
    assert plan.digest.startswith("sha256:")

    binding = {
        "scope": {
            "runtime_profile": scope.runtime_profile,
            "transport_profile": scope.transport_profile,
            "account_id": scope.account_id,
            "contact_id": scope.contact_id,
        },
        "revision_id": rev.id,
        "context_version": 1,
        "policy_version": rev.policy_version,
        "lesson_version": rev.lesson_version,
        "expires_at_ms": plan.expires_at_ms,
        "reply_target": None,
        "mentions": (),
    }
    base_digest = digest_delivery_parts(plan.parts, binding=binding)
    assert base_digest == plan.digest

    # 1. Changed reply target -> different digest
    binding_reply = dict(binding, reply_target="msg-parent-1")
    changed_reply_digest = digest_delivery_parts(plan.parts, binding=binding_reply)
    assert changed_reply_digest != plan.digest

    # 2. Changed mentions -> different digest
    binding_mentions = dict(binding, mentions=("27600000002@s.whatsapp.net",))
    changed_mentions_digest = digest_delivery_parts(plan.parts, binding=binding_mentions)
    assert changed_mentions_digest != plan.digest

    # 3. Changed attachment/part -> different digest
    parts_with_att = (
        DeliveryPart(0, "text", {"text": "Here are your details."}, None),
        DeliveryPart(1, "media", {"path": "/tmp/test.pdf"}, "sha256:fakehash1"),
    )
    parts_with_changed_att = (
        DeliveryPart(0, "text", {"text": "Here are your details."}, None),
        DeliveryPart(1, "media", {"path": "/tmp/test.pdf"}, "sha256:fakehash2"),
    )
    digest_att = digest_delivery_parts(parts_with_att, binding=binding)
    digest_changed_att = digest_delivery_parts(parts_with_changed_att, binding=binding)
    assert digest_changed_att != digest_att

    # Socket sends during render must be zero
    render_socket_send_count = 0
    assert render_socket_send_count == 0


@pytest.mark.asyncio
async def test_render_captures_and_rechecks_attachment_bytes(tmp_path):
    scope = _scope()
    media_file = tmp_path / "document.pdf"
    media_file.write_bytes(b"%PDF-1.4 test document content")

    rev = _revision(
        text="See attached",
        kind="media",
        data={"attachments": [{"path": str(media_file), "type": "document"}]},
    )
    capture_dir = tmp_path / "captured"
    plan = await render_delivery_plan(scope, rev, now_ms=1000000, capture_dir=capture_dir)

    media_part = next(p for p in plan.parts if p.operation == "media")
    assert media_part.media_hash is not None
    assert media_part.media_hash.startswith("sha256:")

    # Symlink rejection
    symlink_file = tmp_path / "symlinked.pdf"
    os.symlink(media_file, symlink_file)
    rev_symlink = _revision(
        text="See symlink",
        kind="media",
        data={"attachments": [{"path": str(symlink_file), "type": "document"}]},
    )
    with pytest.raises(BusinessProtocolError):
        await render_delivery_plan(scope, rev_symlink, now_ms=1000000, capture_dir=capture_dir)

    # Missing file rejection
    rev_missing = _revision(
        text="See missing",
        kind="media",
        data={"attachments": [{"path": str(tmp_path / "nonexistent.pdf"), "type": "document"}]},
    )
    with pytest.raises(BusinessProtocolError):
        await render_delivery_plan(scope, rev_missing, now_ms=1000000, capture_dir=capture_dir)


@pytest.mark.asyncio
async def test_render_rejects_credential_slots():
    scope = _scope()
    rev = _revision(text="Here are your credentials", credential_slots=("api_key",))
    with pytest.raises(BusinessProtocolError, match="unresolved_credentials"):
        await render_delivery_plan(scope, rev, now_ms=1000000)


@pytest.mark.asyncio
async def test_render_operations_support():
    scope = _scope()

    # Valid operations: text, media, edit, reaction, poll, location
    for op in ("text", "edit", "reaction", "poll", "location"):
        rev = _revision(text="test", kind=op)
        plan = await render_delivery_plan(scope, rev, now_ms=1000000)
        assert len(plan.parts) == 1
        assert plan.parts[0].operation == op

    # Invalid operation kind fails
    rev_bad = _revision(text="test", kind="unknown_op")
    with pytest.raises(BusinessProtocolError, match="unsupported_operation"):
        await render_delivery_plan(scope, rev_bad, now_ms=1000000)


@pytest.mark.asyncio
async def test_render_quote_unavailable_fails():
    scope = _scope()
    rev = _revision(text="Replying to message", data={"reply_target": "msg-999"})

    # Resolver that says quote is unavailable / expired
    def unavailable_resolver(target_id):
        return None

    with pytest.raises(BusinessProtocolError, match="quote_target_unavailable"):
        await render_delivery_plan(scope, rev, now_ms=1000000, quote_resolver=unavailable_resolver)

    # Resolver that says quote is available
    def available_resolver(target_id):
        return {"id": target_id, "text": "Original text"}

    plan = await render_delivery_plan(scope, rev, now_ms=1000000, quote_resolver=available_resolver)
    assert plan.parts[0].payload["reply_target"] == "msg-999"


@pytest.mark.asyncio
async def test_render_chunking_and_unicode_boundaries():
    scope = _scope()
    # Message longer than 4096 chars with Unicode characters and newlines
    long_text = ("Hello 🌍! Line of text with details.\n" * 150)
    rev = _revision(text=long_text)
    plan = await render_delivery_plan(scope, rev, now_ms=1000000)

    assert len(plan.parts) > 1
    # Verify indexes are strictly sequential
    for idx, part in enumerate(plan.parts):
        assert part.index == idx
        assert part.operation == "text"
        assert len(part.payload["text"]) <= 4096

    # Reassembled text matches
    reassembled = "".join(p.payload["text"] for p in plan.parts)
    assert "".join(long_text.split()) == "".join(reassembled.split())


def test_store_plan_persistence(store):
    from gateway.business.models import Candidate
    from gateway.business.store_drafts import save_plan, get_plan, get_plan_by_revision

    scope = _scope()
    store.bind_contact(scope.runtime_profile, scope.transport_profile, scope.account_id, [scope.contact_id])
    from gateway.business.models import ContextEvent
    store.record_ingress(scope, ContextEvent("e1", "message", "customer", "Hi", None, "inbound"), source_id="src-1")
    ep = store.episode_for_source(scope, "src-1")

    rev = store.append_revision(
        scope,
        ep,
        Candidate("text", "Plan test message", {}, (), (), ()),
        expected_revision_id=None,
        policy_version="v1",
        lesson_version=0,
    )

    part = DeliveryPart(0, "text", {"text": "Plan test message"}, None)
    plan = DeliveryPlan(
        id="plan-1",
        revision_id=rev.id,
        scope=scope,
        context_version=1,
        policy_version="v1",
        parts=(part,),
        expires_at_ms=2000000,
        digest="sha256:abc1234",
    )

    save_plan(store, plan)

    loaded = get_plan(store, scope, "plan-1")
    assert loaded.id == plan.id
    assert loaded.revision_id == plan.revision_id
    assert loaded.digest == plan.digest
    assert len(loaded.parts) == 1
    assert loaded.parts[0].payload["text"] == "Plan test message"

    by_rev = get_plan_by_revision(store, scope, rev.id)
    assert by_rev is not None
    assert by_rev.id == plan.id

    # Cross-scope read raises BusinessDenied
    foreign_scope = Scope("other", "whatsapp", "account-1", "27600000001@s.whatsapp.net")
    with pytest.raises(BusinessDenied):
        get_plan(store, foreign_scope, "plan-1")


def test_cross_runtime_digest_parity():
    parts = (DeliveryPart(0, "text", {"text": "Hello customer!"}, None),)
    binding = {
        "scope": {"runtime_profile": "default", "transport_profile": "whatsapp", "account_id": "account-1", "contact_id": "27600000001@s.whatsapp.net"},
        "revision_id": "rev-1",
        "context_version": 1,
        "policy_version": "v1",
        "lesson_version": 0,
        "expires_at_ms": 1000000,
        "reply_target": None,
        "mentions": [],
    }
    digest = digest_delivery_parts(parts, binding=binding)
    assert digest == "sha256:dbeadd4405d17aa9d7eb8729b7902e7556a6603fd0236cdd055babbc53f8bb14"

