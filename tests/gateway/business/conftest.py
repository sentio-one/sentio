"""Fixtures for native WhatsApp business configuration and shadow gateway tests."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gateway.business.config import BusinessConfig
from gateway.business.ingress import observe_ingress
from gateway.business.lifecycle import stop_business_service
from gateway.business.models import TaskRecord
from gateway.business.service import BusinessService
from gateway.business.store import BusinessStore
from gateway.business.turns import handle_business_turn


@pytest.fixture
def business_config():
    """Return the minimal disabled business configuration."""
    return {}


class BusinessGatewayHarness:
    """Harness exposing receive(event), finish(result), restart(), and inspection points."""

    def __init__(self, tmp_path: Path, *, mode: str = "shadow"):
        self.tmp_path = Path(tmp_path)
        self.mode = mode
        self.config = BusinessConfig(
            mode=mode,
            required_policies=(),
            operators=("operator-1",),
            rewrite_attempts=2,
            approval_ttl_seconds=86400,
            queue_ttl_seconds=604800,
            governor=None,
        )
        self.store = BusinessStore(self.tmp_path)
        self.aux_calls: list[dict[str, Any]] = []
        self.socket_calls: list[dict[str, Any]] = []
        self.aux_response_override = None
        self._last_event = None
        self._init_service()

    def _init_service(self):
        self.service = BusinessService(
            self.store,
            config_resolver=lambda scope: self.config,
        )
        self.runner = SimpleNamespace(
            _business_store=self.store,
            _business_service=self.service,
            config=SimpleNamespace(
                platforms={
                    "whatsapp": {
                        "extra": {
                            "business": dataclasses.asdict(self.config),
                        }
                    }
                }
            ),
            _main_runtime={"provider": "test", "model": "test-aux-model"},
        )

    @property
    def operator_tasks(self) -> list[TaskRecord]:
        conn = self.store._connect()
        try:
            rows = conn.execute(
                "SELECT task_id, dedupe_key, payload FROM business_tasks ORDER BY task_id"
            ).fetchall()
            tasks = []
            for row in rows:
                p = json.loads(row["payload"])
                tasks.append(
                    TaskRecord(
                        id=row["task_id"],
                        dedupe_key=row["dedupe_key"],
                        reason=p.get("reason", ""),
                        refs=tuple(p.get("refs", ())),
                    )
                )
            return tasks
        finally:
            conn.close()

    async def receive(self, event):
        self._last_event = event
        return await observe_ingress(self.runner, event)

    async def finish(self, result, event=None):
        target_event = event or self._last_event
        return await handle_business_turn(self.runner, target_event, result)

    def restart(self):
        stop_business_service(self.runner)
        self.store = BusinessStore(self.tmp_path)
        self._init_service()


@pytest.fixture
def business_gateway(tmp_path, monkeypatch):
    harness = BusinessGatewayHarness(tmp_path, mode="shadow")

    async def fake_async_call_llm(*, task, main_runtime, messages, tools=None, timeout=60, max_tokens=2048):
        harness.aux_calls.append(
            {
                "task": task,
                "main_runtime": main_runtime,
                "messages": messages,
                "tools": tools or [],
                "timeout": timeout,
                "max_tokens": max_tokens,
            }
        )
        if callable(harness.aux_response_override):
            return harness.aux_response_override(task=task, messages=messages)
        if harness.aux_response_override is not None:
            return harness.aux_response_override

        if task == "business_compose":
            # Check whether this was a decision prompt or candidate composition prompt
            sys_content = messages[0]["content"] if messages else ""
            if "decision" in sys_content.lower() or "workdecision" in sys_content.lower():
                return json.dumps(
                    {
                        "kind": "compose",
                        "reason": "reply_needed",
                        "intent": "answer customer inquiry",
                        "fact_ids": [],
                        "action_ids": [],
                    }
                )
            return json.dumps(
                {
                    "kind": "text",
                    "text": "Hello, here is the information you requested.",
                    "data": {},
                    "fact_ids": [],
                    "completed_action_ids": [],
                    "credential_slots": [],
                }
            )
        if task == "business_review":
            return json.dumps(
                {
                    "recommendation": "approve",
                    "reasons": [],
                    "flags": [],
                    "confidence": 0.95,
                }
            )
        return "{}"

    monkeypatch.setattr("agent.auxiliary_client.async_call_llm", fake_async_call_llm)
    monkeypatch.setattr("gateway.business.compose.async_call_llm", fake_async_call_llm)
    monkeypatch.setattr("gateway.business.review.async_call_llm", fake_async_call_llm)
    monkeypatch.setattr("gateway.business.turns.async_call_llm", fake_async_call_llm)

    return harness
