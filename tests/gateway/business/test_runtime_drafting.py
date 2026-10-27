"""Production startup and drafting contracts missed by the original acceptance suite."""

import contextvars
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from gateway.business.config import BusinessConfig
from gateway.business.decision import decision_from_result
from gateway.business.lifecycle import start_business_service
from gateway.business.models import Candidate, CheckReport, ContextEvent, DraftRevision, Scope, WorkDecision
from gateway.business.policy import PolicyBundle
from gateway.business.readiness import _set_account_mode_row
from gateway.business.service import BusinessService
from gateway.business.store import BusinessStore


def _config(mode="supervised"):
    return BusinessConfig(mode, ("sales",), ("operator",), 0, 86400, 604800, None)


@pytest.mark.asyncio
async def test_supervised_startup_retains_service_and_durable_latch(tmp_path):
    from gateway.business.models import AccountKey

    store = BusinessStore(tmp_path)
    _set_account_mode_row(store, AccountKey("default", "default"), "supervised")
    runner = SimpleNamespace(hermes_home=tmp_path, config=SimpleNamespace(platforms={
        "whatsapp": SimpleNamespace(extra={"business": {"mode": "supervised"}}),
    }), adapters={})
    service = await start_business_service(runner)
    assert service is not None
    assert service.resolve_config(Scope("default", "default", "default", "c")).mode == "supervised"
    # A broken config after restart must retain the account's supervision requirement.
    runner.config.platforms["whatsapp"].extra["business"] = {"mode": "garbled"}
    service = await start_business_service(runner)
    cfg = service.resolve_config(Scope("default", "default", "default", "c"))
    assert cfg.mode == "supervised"
    assert cfg.extra["configuration_error"] == "invalid_mode"


@pytest.mark.asyncio
async def test_unlatched_supervised_startup_holds_instead_of_personal_fallback(tmp_path):
    runner = SimpleNamespace(hermes_home=tmp_path, config=SimpleNamespace(platforms={
        "whatsapp": {"extra": {"business": {"mode": "supervised"}}},
    }), adapters={})
    service = await start_business_service(runner)
    assert service is not None
    cfg = service.resolve_config(Scope("default", "default", "default", "c"))
    assert cfg.enabled
    assert cfg.extra["configuration_error"] == "supervised_latch_required"


def test_normal_final_response_json_is_a_decision_and_runtime_failure_wins():
    payload = {"kind": "compose", "reason": "answer", "intent": "answer stock question",
               "fact_ids": [], "action_ids": [], "policy_version": "v1"}
    result = {"final_response": "```json\n" + json.dumps(payload) + "\n```"}
    assert decision_from_result(result).kind == "compose"
    assert decision_from_result({**result, "failed": True}).kind == "failed"
    assert decision_from_result({"final_response": "Internal working prose"}).kind == "failed"


@pytest.mark.asyncio
async def test_supervised_checked_draft_creates_real_held_outbox(tmp_path, monkeypatch):
    store = BusinessStore(tmp_path)
    scope = store.bind_contact("default", "default", "default", ["27800000001@s.whatsapp.net"])
    episode = store.record_ingress(scope, ContextEvent("m1", "message", "customer", "Stock?", None, "inbound"), source_id="m1")
    policy = PolicyBundle("sales", "v1", (), (), ("Use verified facts",), (), ())

    async def context(*args, **kwargs):
        return policy

    async def checks(*args, **kwargs):
        return CheckReport(True, (), "v1")

    async def model(*, task, **kwargs):
        if task == "business_compose":
            return {"kind": "text", "text": "Let me confirm stock.", "data": {},
                    "fact_ids": [], "completed_action_ids": [], "credential_slots": []}
        return {"recommendation": "approve", "reasons": [], "policy_refs": []}

    monkeypatch.setattr("gateway.business.service.collect_policy", context)
    monkeypatch.setattr("gateway.business.review.check_candidate", checks)
    monkeypatch.setattr("gateway.business.compose.async_call_llm", model)
    monkeypatch.setattr("gateway.business.review.async_call_llm", model)
    service = BusinessService(store, config_resolver=_config())
    decision = {"kind": "compose", "reason": "answer", "intent": "confirm stock",
                "fact_ids": [], "action_ids": [], "policy_version": "v1"}
    receipt = await service.handle_turn(scope, episode, {"final_response": json.dumps(decision)}, main_runtime={})
    assert receipt.state == "held"
    with store._connect() as conn:
        row = conn.execute("SELECT revision_id, state FROM business_outbox WHERE outbox_id=?", (receipt.id,)).fetchone()
    assert row is not None
    assert row["state"] == "held"
    assert row["revision_id"] == receipt.revision_id
    assert store.get_plan_by_revision(scope, receipt.revision_id) is not None


@pytest.mark.asyncio
async def test_composer_and_reviewer_receive_conversation_identity_and_lessons(tmp_path, monkeypatch):
    from gateway.business.context import build_context
    from gateway.business.compose import compose_candidate
    from gateway.business.review import review_candidate

    store = BusinessStore(tmp_path)
    scope = Scope("sales", "default", "default", "customer-1")
    episode = store.record_ingress(scope, ContextEvent("e1", "message", "customer", "Can you confirm stock?", None, "inbound"), source_id="e1")
    policy = PolicyBundle("sales", "v1", (), (), (), (), ())
    snapshot = replace(build_context(store, scope, episode, policy=policy, lesson_version=4), confirmed_lessons=("Do not promise stock",))
    candidate = Candidate("text", "I will check.", {}, (), (), ())
    payloads = []

    async def model(*, task, messages, **kwargs):
        payloads.append(json.loads(messages[1]["content"]))
        if task == "business_compose":
            return {"kind": "text", "text": candidate.text, "data": {}, "fact_ids": [], "completed_action_ids": [], "credential_slots": []}
        return {"recommendation": "approve", "reasons": [], "policy_refs": []}

    monkeypatch.setattr("gateway.business.compose.async_call_llm", model)
    monkeypatch.setattr("gateway.business.review.async_call_llm", model)
    await compose_candidate(snapshot, WorkDecision("compose", "reply", "answer", (), (), "v1"), main_runtime={})
    await review_candidate(snapshot, DraftRevision("r1", scope, episode, 1, candidate, "v1", 4, None, None), main_runtime={})
    for payload in payloads:
        assert payload["scope"]["runtime_profile"] == "sales"
        assert payload["events"][0]["text"] == "Can you confirm stock?"
        assert payload["confirmed_lessons"] == ["Do not promise stock"]
        assert payload["lesson_version"] == 4


@pytest.mark.asyncio
async def test_policy_callback_worker_keeps_profile_context():
    from gateway.business.policy import _invoke

    profile = contextvars.ContextVar("business-test-profile", default="wrong")
    token = profile.set("sales")
    try:
        assert await _invoke(lambda: profile.get()) == "sales"
    finally:
        profile.reset(token)


def test_empty_pilot_report_has_no_fabricated_measurements(tmp_path):
    from gateway.business.workflow_readiness import pilot_report

    report = pilot_report(Scope("default", "default", "default", "c"), store=BusinessStore(tmp_path))
    assert report["evidence"]["p95_draft_latency_ms"] is None
    assert report["evidence"]["reviewer_agreement_rate"] is None


@pytest.mark.asyncio
async def test_configuration_presence_does_not_certify_runtime_readiness(tmp_path):
    from gateway.business.models import AccountKey
    from gateway.business.workflow_readiness import check_workflow_readiness

    config = replace(_config(), extra={"plugin_mode": "native"})
    service = BusinessService(BusinessStore(tmp_path), config_resolver=config)
    report = await check_workflow_readiness(service, AccountKey("default", "default"))
    assert report["ready"] is False
    assert "operator_controls_unavailable" in report["blockers"]
