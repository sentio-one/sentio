"""Tests for shadow gateway wiring and audience-separation contracts (Task A7)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.error_classifier import FailoverReason, classify_api_error
from gateway.business.models import Scope
from gateway.business.turns import handle_business_turn
from gateway.config import Platform
from gateway.platforms.event import MessageEvent
from gateway.session import SessionSource


def _source(profile="default", chat_id="27600000001@s.whatsapp.net", user_id="27600000001@s.whatsapp.net"):
    return SessionSource(
        platform=Platform.WHATSAPP,
        chat_id=chat_id,
        user_id=user_id,
        profile=profile,
    )


@pytest.mark.asyncio
async def test_shadow_never_dispatches(business_gateway):
    source = _source()
    event = MessageEvent(text="What are your school pricing plans?", source=source, message_id="inbound-1")
    episode = await business_gateway.receive(event)
    assert episode is not None

    legacy_result = {
        "final_response": "We have CAPS-aligned material for Grades 4-12.",
        "messages": [{"role": "user", "content": "What are your school pricing plans?"}],
        "api_calls": 1,
        "failed": False,
    }

    shadow_receipt = await business_gateway.finish(legacy_result, event=event)
    assert shadow_receipt is not None
    assert shadow_receipt.state == "blocked"
    assert shadow_receipt.reason == "policy_unavailable"
    assert business_gateway.socket_calls == []


@pytest.mark.asyncio
async def test_silence_failure_and_progress_are_not_drafts(business_gateway):
    source = _source()

    # 1. Intentional silence -> no_reply receipt
    event_silence = MessageEvent(text="Thanks", source=source, message_id="inbound-silence")
    await business_gateway.receive(event_silence)
    silence_result = {"final_response": "", "silent": True, "api_calls": 1, "failed": False}
    no_reply_receipt = await business_gateway.finish(silence_result, event=event_silence)
    assert no_reply_receipt.state == "no_reply"

    # 2. Working-turn failure -> failure task queued, 0 composer calls
    event_fail = MessageEvent(text="Break", source=source, message_id="inbound-fail")
    await business_gateway.receive(event_fail)

    aux_calls_before = len(business_gateway.aux_calls)
    failure_result = {"failed": True, "error": "rate_limit_exceeded", "final_response": ""}
    fail_receipt = await business_gateway.finish(failure_result, event=event_fail)
    assert fail_receipt.state == "failed"

    # Verify task queued and no auxiliary composer call
    failure_tasks = [t for t in business_gateway.operator_tasks if t.reason == "rate_limit_exceeded"]
    failure_task_count = len(failure_tasks)
    assert failure_task_count == 1

    composer_calls = [
        c for c in business_gateway.aux_calls[aux_calls_before:]
        if c.get("task") == "business_compose" and "candidate" in json.dumps(c.get("messages", []))
    ]
    composer_call_count_for_failure = len(composer_calls)
    assert composer_call_count_for_failure == 0

    # 3. Progress output leaked in final_response -> not accepted as customer candidate
    event_prog = MessageEvent(text="Status?", source=source, message_id="inbound-prog")
    await business_gateway.receive(event_prog)

    # If the working agent produced tool progress text
    prog_result = {
        "final_response": "⏳ Working — 3 min — iteration 7, receiving stream response",
        "api_calls": 7,
        "failed": False,
    }
    # Set override so aux compose fails candidate validation on progress
    business_gateway.aux_response_override = json.dumps({
        "kind": "compose",
        "reason": "progress_leak",
        "intent": "leaked progress",
        "fact_ids": [],
        "action_ids": [],
    })
    prog_receipt = await business_gateway.finish(prog_result, event=event_prog)
    assert prog_receipt is not None

    scope = Scope(source.profile, "whatsapp", source.profile, source.chat_id)
    revisions = business_gateway.store.list_revisions(scope)
    customer_candidates_containing_progress = [
        r for r in revisions if "⏳" in getattr(r.candidate, "text", "")
    ]
    assert customer_candidates_containing_progress == []


@pytest.mark.asyncio
async def test_personal_turn_still_uses_existing_delivery(tmp_path):
    # Unconfigured runner (no _business_service)
    unconfigured_runner = SimpleNamespace(_business_store=None, _business_service=None)
    source = _source()
    event = MessageEvent(text="Hello personal", source=source, message_id="m-personal")

    expected_legacy_response = "Hello from personal assistant!"
    legacy_result = {
        "final_response": expected_legacy_response,
        "messages": [],
        "api_calls": 1,
        "failed": False,
    }

    turn_receipt = await handle_business_turn(unconfigured_runner, event, legacy_result)
    assert turn_receipt is None
    # Legacy response is delivered unchanged
    personal_response = legacy_result["final_response"]
    assert personal_response == expected_legacy_response


def test_insufficient_credit_error_classification():
    # Reproduce HTTP 400 insufficient credit envelope
    class MockHttp400Error(Exception):
        status_code = 400

    err = MockHttp400Error("HTTP 400: insufficient credits on account")
    classified = classify_api_error(err, provider="openai")
    assert classified.reason == FailoverReason.billing
