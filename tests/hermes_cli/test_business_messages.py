"""Tests for ``hermes messages`` CLI subcommands (Task C1)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock
import pytest

from gateway.business.models import AccountKey, Candidate, ContextEvent, Principal, Scope
from gateway.business.render import DeliveryPart, DeliveryPlan
from gateway.business.service import BusinessService
from gateway.business.store import BusinessStore
from hermes_cli.business_messages import format_message_item, run_messages_command


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


def test_cli_json_reports_held_and_delivery_distinctly(store, service):
    scope = _scope()
    account = _account()
    store.bind_contact(scope.runtime_profile, scope.transport_profile, scope.account_id, [scope.contact_id])
    store.record_ingress(scope, ContextEvent("e1", "message", "customer", "Hi", None, "inbound"), source_id="src-1")
    ep = store.episode_for_source(scope, "src-1")

    # 1. Held outbox item
    candidate = Candidate("text", "Held draft", {}, (), (), ())
    rev1 = store.append_revision(scope, ep, candidate, expected_revision_id=None, policy_version="v1", lesson_version=0)
    part1 = DeliveryPart(0, "text", {"text": "Held draft"}, None)
    plan1 = DeliveryPlan("p1", rev1.id, scope, ep.context_version, "v1", (part1,), 2000000, "sha256:d1")
    store.save_plan(plan1)
    rec_held = store.submit_revision(scope, rev1.id, request_id="req-held-1", now_ms=1000)

    held_json = format_message_item(store, scope, rec_held.id)
    assert held_json["state"] == "held"

    # 2. Accepted outbox item (approved and claimed/sent)
    store.record_ingress(scope, ContextEvent("e2", "message", "customer", "Hi 2", None, "inbound"), source_id="src-2")
    ep2 = store.episode_for_source(scope, "src-2")
    candidate2 = Candidate("text", "Accepted draft", {}, (), (), ())
    rev2 = store.append_revision(scope, ep2, candidate2, expected_revision_id=None, policy_version="v1", lesson_version=0)
    part2 = DeliveryPart(0, "text", {"text": "Accepted draft"}, None)
    plan2 = DeliveryPlan("p2", rev2.id, scope, ep2.context_version, "v1", (part2,), 2000000, "sha256:d2")
    store.save_plan(plan2)
    rec_accepted = store.submit_revision(scope, rev2.id, request_id="req-accepted-2", now_ms=1000)
    principal = Principal("operator-1", "default", frozenset({"approve"}), "human")
    store.approve_revision(principal, scope, revision_id=rev2.id, plan_digest=plan2.digest, now_ms=1000)

    att = store.claim_part(account, now_ms=1000)
    assert att is not None
    store.update_attempt_state(att.id, state="accepted")

    accepted_json = format_message_item(store, scope, rec_accepted.id)
    assert accepted_json["state"] == "accepted"
