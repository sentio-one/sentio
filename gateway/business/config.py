"""Validation for opt-in native WhatsApp business configuration."""

from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class BusinessConfigError(ValueError):
    """A business messaging configuration is invalid or conflicts with its latch."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class BusinessConfig:
    mode: str
    required_policies: tuple[str, ...]
    operators: tuple[str, ...]
    rewrite_attempts: int
    approval_ttl_seconds: int
    queue_ttl_seconds: int
    governor: dict[str, Any] | None

    @property
    def enabled(self) -> bool:
        return self.mode != "off"


_CONFIG_FIELDS = {
    "mode", "required_policies", "operators", "rewrite_attempts",
    "approval_ttl_seconds", "queue_ttl_seconds", "governor",
}
_CLASSES = {"cold", "followup", "reply"}
_GOVERNOR_FIELDS = {
    "timezone", "windows", "min_gap_seconds", "reply_min_gap_seconds", "jitter_seconds",
    "caps", "hard_max_per_day", "failure_window_seconds", "failure_count",
}


def _fail(code: str = "invalid_config") -> None:
    raise BusinessConfigError(code)


def _positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def _nonnegative_number(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _distinct_strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        _fail("invalid_string_list")
    if len(set(value)) != len(value):
        _fail("duplicate_string")
    return tuple(value)


def _valid_clock(value: object, *, end: bool = False) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d|24:00", value):
        return False
    return value != "24:00" or end


def _validate_windows(value: object) -> None:
    if not isinstance(value, dict) or set(value) != _CLASSES:
        _fail("invalid_windows")
    for entries in value.values():
        if not isinstance(entries, list):
            _fail("invalid_windows")
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"weekdays", "start", "end"}:
                _fail("invalid_windows")
            days = entry["weekdays"]
            if (not isinstance(days, list) or not days or any(type(day) is not int or day not in range(7) for day in days)
                    or len(set(days)) != len(days)):
                _fail("invalid_windows")
            if not _valid_clock(entry["start"]) or not _valid_clock(entry["end"], end=True) or entry["start"] == entry["end"]:
                _fail("invalid_windows")


def _validate_caps(value: object) -> None:
    if not isinstance(value, dict) or set(value) != _CLASSES:
        _fail("invalid_caps")
    for cap in value.values():
        if not isinstance(cap, dict) or set(cap) != {"per_hour", "per_day"}:
            _fail("invalid_caps")
        if any(limit is not None and not _positive_int(limit) for limit in cap.values()):
            _fail("invalid_caps")


def _validate_governor(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - _GOVERNOR_FIELDS:
        _fail("invalid_governor")
    required = _GOVERNOR_FIELDS - {"jitter_seconds"}
    if not required <= set(value):
        _fail("invalid_governor")
    try:
        timezone = value["timezone"]
        if not isinstance(timezone, str) or not timezone:
            _fail("invalid_timezone")
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        _fail("invalid_timezone")
    _validate_windows(value["windows"])
    _validate_caps(value["caps"])
    for key in ("min_gap_seconds", "reply_min_gap_seconds", "jitter_seconds"):
        if key in value and not _nonnegative_number(value[key]):
            _fail("invalid_governor")
    for key in ("failure_window_seconds",):
        if type(value[key]) not in (int, float) or not math.isfinite(value[key]) or value[key] <= 0:
            _fail("invalid_governor")
    for key in ("failure_count", "hard_max_per_day"):
        if not _positive_int(value[key]):
            _fail("invalid_governor")
    return copy.deepcopy(value)


def load_business_config(raw: dict, *, supervised_latch: bool) -> BusinessConfig:
    """Parse the YAML business mapping without consulting process or profile secrets."""
    if not isinstance(raw, dict) or set(raw) - _CONFIG_FIELDS:
        _fail()
    mode = raw.get("mode", "off")
    if mode not in {"off", "shadow", "supervised"}:
        _fail("invalid_mode")
    if type(supervised_latch) is not bool:
        _fail("invalid_latch")
    if supervised_latch and mode != "supervised":
        _fail("latched_mode_required")
    if mode == "supervised" and not supervised_latch:
        _fail("supervised_latch_required")
    policies = _distinct_strings(raw.get("required_policies", []))
    operators = _distinct_strings(raw.get("operators", []))
    rewrite_attempts = raw.get("rewrite_attempts", 2)
    approval_ttl = raw.get("approval_ttl_seconds", 86400)
    queue_ttl = raw.get("queue_ttl_seconds", 604800)
    if type(rewrite_attempts) is not int or not 0 <= rewrite_attempts <= 2:
        _fail("invalid_rewrite_attempts")
    if not _positive_int(approval_ttl) or not _positive_int(queue_ttl):
        _fail("invalid_ttl")
    return BusinessConfig(
        mode=mode, required_policies=policies, operators=operators,
        rewrite_attempts=rewrite_attempts, approval_ttl_seconds=approval_ttl,
        queue_ttl_seconds=queue_ttl, governor=_validate_governor(raw.get("governor")),
    )
