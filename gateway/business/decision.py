"""Structured decision extraction with runtime-failure precedence."""

from __future__ import annotations

from typing import Any

from gateway.business.models import BusinessProtocolError, WorkDecision, parse_decision


def decision_from_result(result: dict[str, Any]) -> WorkDecision:
    """Parse *result* from an agent turn into a verified WorkDecision.

    Runtime failures take strict precedence over any claimed customer decision.
    """
    if not isinstance(result, dict):
        raise BusinessProtocolError("invalid_result_envelope")

    if result.get("failed") is True or bool(result.get("error")):
        reason = str(result.get("error") or result.get("final_response") or "turn_failed")
        return WorkDecision(
            kind="failed",
            reason=reason,
            intent="",
            fact_ids=(),
            action_ids=(),
            policy_version=str(result.get("policy_version") or "unknown"),
        )

    decision_payload = result.get("decision")
    if isinstance(decision_payload, WorkDecision):
        return decision_payload
    if isinstance(decision_payload, dict):
        return parse_decision(decision_payload)

    if "kind" in result and "reason" in result and "intent" in result:
        return parse_decision(result)

    final_resp = str(result.get("final_response") or "").strip()
    if final_resp:
        from gateway.business.compose import _extract_json_payload
        try:
            return parse_decision(_extract_json_payload(final_resp))
        except BusinessProtocolError:
            # Working prose is retained as a protocol failure, never as customer copy.
            pass
    return WorkDecision(
        kind="failed",
        reason=f"unrecognized_result: {final_resp}" if final_resp else "unrecognized_result",
        intent="",
        fact_ids=(),
        action_ids=(),
        policy_version=str(result.get("policy_version") or "unknown"),
    )
