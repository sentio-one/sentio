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


class BusinessConflict(RuntimeError):
    """A durable business operation lost an idempotency or revision race."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class BusinessDenied(PermissionError):
    """A requested business record is not owned by the supplied scope."""


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
    required_text = ("reason", "policy_version")
    if not all(_string(value[key]) for key in required_text):
        raise BusinessProtocolError("invalid_decision_text")
    if not _string(value["intent"], nonempty=value["kind"] == "compose"):
        raise BusinessProtocolError("invalid_decision_text")
    return WorkDecision(
        kind=value["kind"], reason=value["reason"], intent=value["intent"],
        fact_ids=_string_tuple(value["fact_ids"]), action_ids=_string_tuple(value["action_ids"]),
        policy_version=value["policy_version"],
    )


def _valid_text(data: dict[str, Any], text: str) -> bool:
    return not data and bool(text.strip())


def _valid_media(data: dict[str, Any], _: str) -> bool:
    if set(data) != {"attachments"} or not isinstance(data["attachments"], list) or not data["attachments"]:
        return False
    return all(_valid_attachment(item) for item in data["attachments"])


def _valid_attachment(item: object) -> bool:
    required = {"id", "sha256", "media_type"}
    allowed = required | {"file_name"}
    if not isinstance(item, dict) or not required <= set(item) or set(item) - allowed:
        return False
    if not _handle(item.get("id")) or not _string(item.get("media_type")):
        return False
    digest = item.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        return False
    name = item.get("file_name")
    return "file_name" not in item or (
        _string(name) and "/" not in name and "\\" not in name
    )


def _valid_edit(data: dict[str, Any], text: str) -> bool:
    return set(data) == {"message_id"} and _string(data["message_id"]) and bool(text.strip())


def _valid_reaction(data: dict[str, Any], _: str) -> bool:
    return set(data) == {"message_id", "emoji"} and all(_string(value) for value in data.values())


def _valid_poll(data: dict[str, Any], text: str) -> bool:
    if set(data) != {"options", "selectable_count"} or not text.strip():
        return False
    options, count = data["options"], data["selectable_count"]
    return (
        isinstance(options, list) and 2 <= len(options) <= 12
        and all(_string(option) for option in options)
        and len({option.strip() for option in options}) == len(options)
        and type(count) is int and 1 <= count <= len(options)
    )


def _valid_location(data: dict[str, Any], _: str) -> bool:
    allowed = {"latitude", "longitude", "name", "address"}
    if set(data) - allowed or not {"latitude", "longitude"} <= set(data):
        return False
    latitude, longitude = data["latitude"], data["longitude"]
    return (
        _valid_coordinate(latitude, -90, 90)
        and _valid_coordinate(longitude, -180, 180)
        and all(_string(data[key], nonempty=False) for key in ("name", "address") if key in data)
    )


def _valid_coordinate(value: object, lower: int, upper: int) -> bool:
    if type(value) is int:
        return lower <= value <= upper
    return type(value) is float and math.isfinite(value) and lower <= value <= upper


_CANDIDATE_DATA_VALIDATORS = {
    "text": _valid_text,
    "media": _valid_media,
    "edit": _valid_edit,
    "reaction": _valid_reaction,
    "poll": _valid_poll,
    "location": _valid_location,
}


def _valid_data(kind: str, data: object, text: str) -> bool:
    validator = _CANDIDATE_DATA_VALIDATORS[kind]
    return isinstance(data, dict) and validator(data, text)


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
