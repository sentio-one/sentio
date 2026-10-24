"""Behavior contracts for native WhatsApp business configuration and outcomes."""

import pytest

from gateway.business.config import BusinessConfigError, load_business_config
from gateway.business.models import (
    BusinessProtocolError,
    Candidate,
    WorkDecision,
    parse_candidate,
    parse_decision,
)


def test_invalid_config_cannot_disable_a_latched_account(business_config):
    assert load_business_config(business_config, supervised_latch=False).mode == "off"

    config = load_business_config({}, supervised_latch=False)
    assert config.rewrite_attempts == 2
    assert config.approval_ttl_seconds == 86400
    assert config.queue_ttl_seconds == 604800

    with pytest.raises(BusinessConfigError):
        load_business_config({"mode": "off"}, supervised_latch=True)


def test_rewrite_and_aux_defaults():
    from hermes_cli.config_defaults import DEFAULT_CONFIG

    auxiliary = DEFAULT_CONFIG["auxiliary"]
    for name in ("business_compose", "business_review", "business_lessons"):
        assert auxiliary[name]["provider"] == "auto"
        assert auxiliary[name]["model"] == ""
        assert auxiliary[name]["timeout"] == 60
    business = DEFAULT_CONFIG["platforms"]["whatsapp"]["extra"]["business"]
    assert business["mode"] == "off"


def test_invalid_outcome_is_not_customer_text():
    for payload in ("no reply sent", [], {"kind": "send"}, {"kind": "compose"}):
        with pytest.raises(BusinessProtocolError):
            parse_decision(payload)


def test_valid_decision_and_candidate_parse_to_frozen_records():
    decision = parse_decision(
        {
            "kind": "compose",
            "reason": "answer requested question",
            "intent": "provide requested hours",
            "fact_ids": ["fact-1"],
            "action_ids": [],
            "policy_version": "policy-1",
        }
    )
    candidate = parse_candidate(
        {
            "kind": "text",
            "text": "We are open until 5.",
            "data": {},
            "fact_ids": ["fact-1"],
            "completed_action_ids": [],
            "credential_slots": [],
        }
    )

    assert isinstance(decision, WorkDecision)
    assert isinstance(candidate, Candidate)
    assert decision.kind == "compose"
    assert candidate.text == "We are open until 5."
    with pytest.raises((AttributeError, TypeError)):
        decision.kind = "no_reply"


def test_envelopes_reject_unknown_fields_missing_references_and_oversize_json():
    valid = {
        "kind": "compose", "reason": "answer", "intent": "share hours",
        "fact_ids": [], "action_ids": [], "policy_version": "v1",
    }
    with pytest.raises(BusinessProtocolError):
        parse_decision({**valid, "customer_text": "secret"})
    del valid["action_ids"]
    with pytest.raises(BusinessProtocolError):
        parse_decision(valid)
    with pytest.raises(BusinessProtocolError):
        parse_decision({
            "kind": "compose", "reason": "x" * 65536, "intent": "x",
            "fact_ids": [], "action_ids": [], "policy_version": "v1",
        })


def test_candidate_data_is_schema_checked_and_detached_from_input():
    attachments = [{"id": "handle-1", "sha256": "a" * 64, "media_type": "image/jpeg"}]
    payload = {
        "kind": "media", "text": "", "data": {"attachments": attachments},
        "fact_ids": [], "completed_action_ids": [], "credential_slots": [],
    }
    candidate = parse_candidate(payload)
    attachments[0]["id"] = "/etc/passwd"
    assert candidate.data["attachments"][0]["id"] == "handle-1"

    payload["data"] = {"message_id": "m1", "extra": "ignored"}
    with pytest.raises(BusinessProtocolError):
        parse_candidate({**payload, "kind": "edit", "text": "updated"})
    with pytest.raises(BusinessProtocolError):
        parse_candidate({
            "kind": "location", "text": "", "data": {"latitude": True, "longitude": 1.0},
            "fact_ids": [], "completed_action_ids": [], "credential_slots": [],
        })
    payload["data"] = {
        "attachments": [{"id": "/etc/passwd", "sha256": "a" * 64, "media_type": "image/jpeg"}]
    }
    with pytest.raises(BusinessProtocolError):
        parse_candidate({**payload, "kind": "media", "text": ""})
    with pytest.raises(BusinessProtocolError):
        parse_candidate({
            "kind": "text", "text": "", "data": {}, "fact_ids": [],
            "completed_action_ids": [], "credential_slots": [],
        })


def test_invalid_governor_windows_and_boolean_scalars_are_rejected():
    from gateway.business.config import BusinessConfigError

    with pytest.raises(BusinessConfigError):
        load_business_config({"rewrite_attempts": True}, supervised_latch=False)
    with pytest.raises(BusinessConfigError):
        load_business_config({"approval_ttl_seconds": 0}, supervised_latch=False)
    with pytest.raises(BusinessConfigError):
        load_business_config({"governor": {"timezone": "Mars/Olympus"}}, supervised_latch=False)
