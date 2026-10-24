"""Frozen business records and strict model-envelope parsers."""

from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import dataclass
from typing import Any


class BusinessProtocolError(ValueError):
    """An outcome or candidate envelope failed its wire contract."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Scope:
    runtime_profile: str
    transport_profile: str
    account_id: str
    contact_id: str


@dataclass(frozen=True)
class Principal:
    subject: str
    runtime_profile: str
    operations: frozenset[str]
    kind: str


@dataclass(frozen=True)
class EpisodeRef:
    id: str
    context_version: int


@dataclass(frozen=True)
class Fact:
    id: str
    text: str
    source: str
    version: str


@dataclass(frozen=True)
class ActionReceipt:
    id: str
    kind: str
    subject: str
    state: str
    proof_ref: str


@dataclass(frozen=True)
class ContextEvent:
    id: str
    kind: str
    actor: str
    text: str
    target_id: str | None
    evidence: str


@dataclass(frozen=True)
class WorkDecision:
    kind: str
    reason: str
    intent: str
    fact_ids: tuple[str, ...]
    action_ids: tuple[str, ...]
    policy_version: str


@dataclass(frozen=True)
class ContextSnapshot:
    id: str
    scope: Scope
    episode: EpisodeRef
    policy_version: str
    lesson_version: int
    events: tuple[ContextEvent, ...]
    facts: tuple[Fact, ...]
    actions: tuple[ActionReceipt, ...]
    rules: tuple[str, ...]
    review_criteria: tuple[str, ...]
    confirmed_lessons: tuple[str, ...]
    missing: tuple[str, ...]


@dataclass(frozen=True)
class Candidate:
    kind: str
    text: str
    data: dict[str, Any]
    fact_ids: tuple[str, ...]
    completed_action_ids: tuple[str, ...]
    credential_slots: tuple[str, ...]


@dataclass(frozen=True)
class CheckReport:
    ok: bool
    issues: tuple[dict[str, Any], ...]
    policy_version: str


@dataclass(frozen=True)
class ReviewVerdict:
    recommendation: str
    reasons: tuple[str, ...]
    policy_refs: tuple[str, ...]
    error_code: str | None


@dataclass(frozen=True)
class DraftRevision:
    id: str
    scope: Scope
    episode: EpisodeRef
    number: int
    candidate: Candidate
    policy_version: str
    lesson_version: int
    checks: CheckReport | None
    review: ReviewVerdict | None


@dataclass(frozen=True)
class Receipt:
    id: str
    state: str
    revision_id: str | None
    reason: str | None


def _envelope(payload: object, fields: set[str]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise BusinessProtocolError("invalid_envelope")
    try:
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise BusinessProtocolError("invalid_envelope") from None
    if len(encoded) > 65536:
        raise BusinessProtocolError("envelope_too_large")
    if set(payload) != fields:
        raise BusinessProtocolError("invalid_envelope_fields")
    return payload


def _string(value: object, *, nonempty: bool = True) -> bool:
    return isinstance(value, str) and (bool(value.strip()) or not nonempty)


def _handle(value: object) -> bool:
    return _string(value) and "/" not in value and "\\" not in value and value not in {".", ".."}


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not _string(item) for item in value):
        raise BusinessProtocolError("invalid_references")
    return tuple(value)


def parse_decision(payload: object) -> WorkDecision:
    fields = {"kind", "reason", "intent", "fact_ids", "action_ids", "policy_version"}
    value = _envelope(payload, fields)
    if not isinstance(value["kind"], str) or value["kind"] not in {"compose", "no_reply", "escalate", "failed"}:
        raise BusinessProtocolError("invalid_decision_kind")
    if not all(_string(value[key]) for key in ("reason", "intent", "policy_version")):
        raise BusinessProtocolError("invalid_decision_text")
    return WorkDecision(
        kind=value["kind"], reason=value["reason"], intent=value["intent"],
        fact_ids=_string_tuple(value["fact_ids"]), action_ids=_string_tuple(value["action_ids"]),
        policy_version=value["policy_version"],
    )


def _valid_data(kind: str, data: object, text: str) -> bool:
    if not isinstance(data, dict):
        return False
    if kind == "text":
        return not data and bool(text.strip())
    if kind == "media":
        if set(data) != {"attachments"} or not isinstance(data["attachments"], list) or not data["attachments"]:
            return False
        for item in data["attachments"]:
            if not isinstance(item, dict) or not {"id", "sha256", "media_type"} <= set(item) or set(item) - {"id", "sha256", "media_type", "file_name"}:
                return False
            if not _handle(item.get("id")) or not _string(item.get("media_type")):
                return False
            if not isinstance(item.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
                return False
            if "file_name" in item and (
                not _string(item["file_name"]) or "/" in item["file_name"] or "\\" in item["file_name"]
            ):
                return False
        return True
    if kind == "edit":
        return set(data) == {"message_id"} and _string(data["message_id"]) and bool(text.strip())
    if kind == "reaction":
        return set(data) == {"message_id", "emoji"} and all(_string(data[key]) for key in data)
    if kind == "poll":
        if set(data) != {"options", "selectable_count"} or not bool(text.strip()):
            return False
        options, count = data["options"], data["selectable_count"]
        return (
            isinstance(options, list) and 2 <= len(options) <= 12
            and all(_string(option) for option in options) and len({option.strip() for option in options}) == len(options)
            and type(count) is int and 1 <= count <= len(options)
        )
    if kind == "location":
        if set(data) - {"latitude", "longitude", "name", "address"} or not {"latitude", "longitude"} <= set(data):
            return False
        lat, lon = data["latitude"], data["longitude"]
        return (
            type(lat) in (int, float) and math.isfinite(lat) and -90 <= lat <= 90
            and type(lon) in (int, float) and math.isfinite(lon) and -180 <= lon <= 180
            and all(_string(data[key], nonempty=False) for key in ("name", "address") if key in data)
        )
    return False


def parse_candidate(payload: object) -> Candidate:
    fields = {"kind", "text", "data", "fact_ids", "completed_action_ids", "credential_slots"}
    value = _envelope(payload, fields)
    kind, text = value["kind"], value["text"]
    if not isinstance(kind, str) or kind not in {"text", "media", "edit", "reaction", "poll", "location"}:
        raise BusinessProtocolError("invalid_candidate_kind")
    if not _string(text, nonempty=False) or not _valid_data(kind, value["data"], text):
        raise BusinessProtocolError("invalid_candidate_data")
    return Candidate(
        kind=kind, text=text, data=copy.deepcopy(value["data"]),
        fact_ids=_string_tuple(value["fact_ids"]),
        completed_action_ids=_string_tuple(value["completed_action_ids"]),
        credential_slots=_string_tuple(value["credential_slots"]),
    )
