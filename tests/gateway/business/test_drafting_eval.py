"""Offline evaluation and replay of sanitized drafting cases (Task A7)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from gateway.business.models import Scope
from gateway.config import Platform
from gateway.platforms.event import MessageEvent
from gateway.session import SessionSource

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "fixtures" / "business" / "drafting_cases.jsonl"


def _load_cases():
    assert FIXTURE_PATH.is_file(), f"Fixture missing: {FIXTURE_PATH}"
    cases = []
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


@pytest.mark.asyncio
async def test_drafting_cases_replay_evaluation(business_gateway):
    cases = _load_cases()
    assert len(cases) == 24, f"Expected 24 drafting cases, found {len(cases)}"

    results = []
    start_time = time.monotonic()

    for idx, case in enumerate(cases):
        case_id = case["case_id"]
        chat = case["chat"] or f"276000000{idx:02d}@s.whatsapp.net"
        source = SessionSource(platform=Platform.WHATSAPP, chat_id=chat, user_id=chat, profile="default")

        # Customer message
        lead_text = case.get("lead_message") or "Hi"
        event = MessageEvent(text=lead_text, source=source, message_id=f"case-{case_id}")

        t0 = time.monotonic()
        episode = await business_gateway.receive(event)
        assert episode is not None

        # Working agent legacy result
        agent_text = case.get("agent_draft") or case.get("agent_original") or "Hello"
        legacy_result = {
            "final_response": agent_text,
            "messages": [{"role": "user", "content": lead_text}],
            "api_calls": 1,
            "failed": False,
        }

        receipt = await business_gateway.finish(legacy_result, event=event)
        elapsed = time.monotonic() - t0

        results.append({
            "case_id": case_id,
            "kind": case.get("kind"),
            "failure_class": case.get("failure_class"),
            "receipt_state": receipt.state if receipt else None,
            "latency": elapsed,
        })

    total_duration = time.monotonic() - start_time

    # Verification:
    # 1. Every case was evaluated
    assert len(results) == 24
    # 2. Native socket was NEVER invoked
    assert business_gateway.socket_calls == []
    # 3. All receipts have valid states
    for res in results:
        assert res["receipt_state"] in {"held", "attention", "failed", "no_reply", "escalate", "blocked"}

    # Quality / coverage / cost report summary
    held_count = sum(1 for r in results if r["receipt_state"] == "held")
    attention_count = sum(1 for r in results if r["receipt_state"] == "attention")
    avg_latency = total_duration / len(results)

    print(
        f"\n--- Shadow Drafting Evaluation Summary ---\n"
        f"Total cases: {len(results)}\n"
        f"Held (ready for human review): {held_count}\n"
        f"Attention (flagged): {attention_count}\n"
        f"Auxiliary calls: {len(business_gateway.aux_calls)}\n"
        f"Average latency: {avg_latency:.3f}s\n"
        f"Native sends dispatched: {len(business_gateway.socket_calls)} (must be 0)\n"
    )
