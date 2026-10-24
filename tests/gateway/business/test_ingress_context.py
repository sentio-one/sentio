"""Tests for admission ledger observation and customer context building (Task A4)."""

import pytest
from pathlib import Path
from types import SimpleNamespace

from gateway.config import Platform
from gateway.platforms.event import MessageEvent, MessageType
from gateway.session import SessionSource
from gateway.session_identity import RoutingIdentity
from gateway.business.models import Scope
from gateway.business.policy import PolicyBundle
from gateway.business.store import BusinessStore
from gateway.business.ingress import observe_ingress, finish_ingress
from gateway.business.context import build_context


@pytest.fixture
def store(tmp_path):
    return BusinessStore(tmp_path)


@pytest.fixture
def runner(store):
    return SimpleNamespace(_business_store=store)


def _policy():
    return PolicyBundle(
        id="test-policy",
        version="v1",
        facts=(),
        actions=(),
        rules=(),
        criteria=(),
        missing=(),
    )


def _source(profile="default", chat_id="27600000001@s.whatsapp.net", user_id="27600000001@s.whatsapp.net"):
    return SessionSource(
        platform=Platform.WHATSAPP,
        chat_id=chat_id,
        user_id=user_id,
        profile=profile,
    )


@pytest.mark.asyncio
async def test_consumed_message_remains_in_worklist(runner, store):
    source = _source()
    event = MessageEvent(text="I need help with pricing", source=source, message_id="inbound-msg-1")
    episode = await observe_ingress(runner, event)
    assert episode is not None

    scope = store.bind_contact("default", "default", "default", [source.chat_id])
    await finish_ingress(runner, event, verdict="escalate", reason="pre_gateway_dispatch skipped")

    skipped_row = store.get_verdict(scope, episode.id)
    assert skipped_row.verdict == "escalate"

    tasks = store.list_tasks(scope)
    assert len(tasks) == 1
    task = tasks[0]
    assert task.refs == (skipped_row.episode_id,)


@pytest.mark.asyncio
async def test_duplicate_ingress_and_owner_reply(runner, store):
    source = _source()
    event = MessageEvent(text="Hello there", source=source, message_id="inbound-msg-dup")

    original = await observe_ingress(runner, event)
    duplicate = await observe_ingress(runner, event)
    assert duplicate.context_version == original.context_version

    scope = store.bind_contact("default", "default", "default", [source.chat_id])
    owner_event = MessageEvent(
        text="Hello, how can I help you?",
        source=source,
        message_id="owner-msg-1",
        metadata={"whatsapp_from_owner": True},
    )
    owner_ep = await observe_ingress(runner, owner_event)
    assert owner_ep is not None

    snapshot = build_context(store, scope, owner_ep, policy=_policy(), lesson_version=1)
    assert snapshot.events[-1].actor == "owner"


@pytest.mark.asyncio
async def test_voice_and_reaction_context(runner, store):
    source = _source()
    scope = store.bind_contact("default", "default", "default", [source.chat_id])

    voice_event = MessageEvent(
        text="",
        message_type=MessageType.VOICE,
        source=source,
        message_id="voice-msg-1",
        metadata={"voice_transcript": "recognized voice transcript"},
    )
    voice_ep = await observe_ingress(runner, voice_event)
    assert voice_ep is not None

    voice_snapshot = build_context(store, scope, voice_ep, policy=_policy(), lesson_version=1)
    assert "recognized voice transcript" in [e.text for e in voice_snapshot.events]

    # Voice message with missing transcript
    untranscribed_event = MessageEvent(
        text="",
        message_type=MessageType.VOICE,
        source=source,
        message_id="voice-msg-2",
        metadata={},
    )
    untranscribed_ep = await observe_ingress(runner, untranscribed_event)
    untranscribed_snapshot = build_context(store, scope, untranscribed_ep, policy=_policy(), lesson_version=1)
    assert any("voice_transcript" in item for item in untranscribed_snapshot.missing)

    offer_id = "offer-message-id-99"
    reaction_event = MessageEvent(
        text="",
        source=source,
        message_id="reaction-msg-1",
        metadata={"reaction_target_id": offer_id, "reaction_emoji": "👍"},
    )
    reaction_ep = await observe_ingress(runner, reaction_event)
    assert reaction_ep is not None

    reaction_snapshot = build_context(store, scope, reaction_ep, policy=_policy(), lesson_version=1)
    assert reaction_snapshot.events[-1].target_id == offer_id


@pytest.mark.asyncio
async def test_restored_secondary_never_uses_launch_account(runner, store, tmp_path):
    source = _source(profile="default", chat_id="27600000002@s.whatsapp.net", user_id="27600000002@s.whatsapp.net")
    identity = RoutingIdentity(
        transport_profile="sales-secondary",
        runtime_profile="default",
        authorization_home=tmp_path / "auth",
        runtime_home=tmp_path / "runtime",
        multiplexed=True,
    )
    source._identity = identity

    event = MessageEvent(text="Inbound on secondary", source=source, message_id="sec-msg-1")
    episode = await observe_ingress(runner, event)
    assert episode is not None

    scope = store.bind_contact("default", "sales-secondary", "sales-secondary", [source.chat_id])
    restored_snapshot = build_context(store, scope, episode, policy=_policy(), lesson_version=1)
    assert restored_snapshot.scope.transport_profile == "sales-secondary"
