"""Durable account pacing, window calculation, and reservation store (Task B3)."""

from __future__ import annotations

import datetime
from typing import Any
from zoneinfo import ZoneInfo

from hermes_cli.sqlite_util import transaction
from gateway.business.governor import Eligibility
from gateway.business.models import AccountKey, Principal


def _is_inside_window(windows_for_class: list[dict[str, Any]], local_dt: datetime.datetime) -> bool:
    weekday = local_dt.weekday()
    time_mins = local_dt.hour * 60 + local_dt.minute

    for w in windows_for_class:
        sh, sm = map(int, w["start"].split(":"))
        start_mins = sh * 60 + sm
        eh, em = map(int, w["end"].split(":"))
        end_mins = eh * 60 + em

        if start_mins < end_mins:
            if weekday in w["weekdays"] and start_mins <= time_mins < end_mins:
                return True
        else:
            # Overnight window: e.g. 22:00 to 04:00
            if weekday in w["weekdays"] and time_mins >= start_mins:
                return True
            prev_weekday = (weekday - 1) % 7
            if prev_weekday in w["weekdays"] and time_mins < end_mins:
                return True
    return False


def _check_caps(db, account: AccountKey, message_class: str, now_ms: int, local_dt: datetime.datetime, tz: ZoneInfo, config: dict[str, Any]) -> str | None:
    # Hourly cap
    caps = config.get("caps", {}).get(message_class, {})
    per_hour = caps.get("per_hour")
    if per_hour is not None:
        one_hour_ago = now_ms - 3600000
        hourly_count = db.execute(
            "SELECT COUNT(*) FROM business_governor_attempts WHERE transport_profile=? AND account_id=? AND message_class=? AND timestamp_ms > ?",
            (account.transport_profile, account.account_id, message_class, one_hour_ago),
        ).fetchone()[0]
        if hourly_count >= per_hour:
            return "hourly_cap_exceeded"

    # Daily cap (per local calendar day)
    per_day = caps.get("per_day")
    local_midnight = datetime.datetime(local_dt.year, local_dt.month, local_dt.day, 0, 0, tzinfo=tz)
    midnight_ms = int(local_midnight.timestamp() * 1000)
    if per_day is not None:
        daily_count = db.execute(
            "SELECT COUNT(*) FROM business_governor_attempts WHERE transport_profile=? AND account_id=? AND message_class=? AND timestamp_ms >= ?",
            (account.transport_profile, account.account_id, message_class, midnight_ms),
        ).fetchone()[0]
        if daily_count >= per_day:
            return "daily_cap_exceeded"

    # Hard max per day
    hard_max = config.get("hard_max_per_day")
    if hard_max is not None:
        hard_count = db.execute(
            "SELECT COUNT(*) FROM business_governor_attempts WHERE transport_profile=? AND account_id=? AND timestamp_ms >= ?",
            (account.transport_profile, account.account_id, midnight_ms),
        ).fetchone()[0]
        if hard_count >= hard_max:
            return "hard_max_per_day_exceeded"

    return None


def governor_eligibility(
    store,
    account: AccountKey,
    *,
    message_class: str,
    now_ms: int,
    config: dict[str, Any] | None = None,
) -> Eligibility:
    """Evaluate account eligibility against governor policies."""
    if not config:
        return Eligibility(allowed=False, not_before_ms=None, reason="governor_unconfigured")

    conn = store._connect()
    with transaction(conn) as db:
        return _eval_eligibility(db, account, message_class, now_ms, config)


def _eval_eligibility(db, account: AccountKey, message_class: str, now_ms: int, config: dict[str, Any]) -> Eligibility:
    # 1. Paused check and clock regression
    row = db.execute(
        "SELECT paused, pause_reason, high_watermark_ms FROM business_governor_accounts WHERE transport_profile=? AND account_id=?",
        (account.transport_profile, account.account_id),
    ).fetchone()
    if row and row["paused"] and message_class != "operator":
        return Eligibility(allowed=False, not_before_ms=None, reason="account_paused")

    high_watermark = row["high_watermark_ms"] if row else 0
    if now_ms < high_watermark:
        return Eligibility(allowed=False, not_before_ms=high_watermark, reason="backward_clock_detected")

    # 2. In-flight check (only 1 concurrent reservation)
    in_flight = db.execute(
        "SELECT attempt_id, reserved_at_ms FROM business_governor_reservations WHERE transport_profile=? AND account_id=?",
        (account.transport_profile, account.account_id),
    ).fetchone()
    if in_flight:
        return Eligibility(allowed=False, not_before_ms=None, reason="in_flight_busy")

    # 3. Windows check
    tz = ZoneInfo(config["timezone"])
    local_dt = datetime.datetime.fromtimestamp(now_ms / 1000.0, tz=tz)
    windows_for_class = config.get("windows", {}).get(message_class, [])
    if not _is_inside_window(windows_for_class, local_dt):
        return Eligibility(allowed=False, not_before_ms=None, reason="outside_window")

    # 4. Min gap check
    gap_seconds = config.get("reply_min_gap_seconds", config.get("min_gap_seconds", 0)) if message_class == "reply" else config.get("min_gap_seconds", 0)
    gap_ms = int(gap_seconds * 1000)
    last_att = db.execute(
        "SELECT timestamp_ms FROM business_governor_attempts WHERE transport_profile=? AND account_id=? ORDER BY timestamp_ms DESC LIMIT 1",
        (account.transport_profile, account.account_id),
    ).fetchone()
    if last_att and (now_ms < last_att["timestamp_ms"] + gap_ms):
        return Eligibility(allowed=False, not_before_ms=last_att["timestamp_ms"] + gap_ms, reason="min_gap")

    # 5. Caps check
    cap_violation = _check_caps(db, account, message_class, now_ms, local_dt, tz, config)
    if cap_violation:
        return Eligibility(allowed=False, not_before_ms=None, reason=cap_violation)

    # 6. Circuit breaker check
    failure_count_thresh = config.get("failure_count")
    failure_window_sec = config.get("failure_window_seconds")
    if failure_count_thresh and failure_window_sec:
        fail_window_start = now_ms - int(failure_window_sec * 1000)
        recent_fails = db.execute(
            "SELECT COUNT(*) FROM business_governor_attempts WHERE transport_profile=? AND account_id=? AND success=0 AND timestamp_ms > ?",
            (account.transport_profile, account.account_id, fail_window_start),
        ).fetchone()[0]
        if recent_fails >= failure_count_thresh:
            return Eligibility(allowed=False, not_before_ms=None, reason="circuit_breaker_tripped")

    return Eligibility(allowed=True, not_before_ms=now_ms, reason=None)


def governor_reserve_attempt(
    store,
    account: AccountKey,
    attempt_id: str,
    *,
    message_class: str,
    now_ms: int,
    config: dict[str, Any] | None = None,
) -> Eligibility:
    """Atomically check eligibility and reserve attempt capacity."""
    if not config:
        return Eligibility(allowed=False, not_before_ms=None, reason="governor_unconfigured")

    conn = store._connect()
    with transaction(conn, immediate=True) as db:
        el = _eval_eligibility(db, account, message_class, now_ms, config)
        if not el.allowed:
            return el

        db.execute(
            "INSERT INTO business_governor_reservations(transport_profile, account_id, attempt_id, message_class, reserved_at_ms) VALUES(?,?,?,?,?)",
            (account.transport_profile, account.account_id, attempt_id, message_class, now_ms),
        )
        db.execute(
            "INSERT INTO business_governor_accounts(transport_profile, account_id, high_watermark_ms) VALUES(?,?,?) "
            "ON CONFLICT(transport_profile, account_id) DO UPDATE SET high_watermark_ms=MAX(high_watermark_ms, excluded.high_watermark_ms)",
            (account.transport_profile, account.account_id, now_ms),
        )
        return Eligibility(allowed=True, not_before_ms=now_ms, reason=None)


def governor_complete_attempt(
    store,
    account: AccountKey,
    attempt_id: str,
    *,
    submitted: bool,
    finished_at_ms: int,
) -> None:
    """Clear reservation and record attempt outcome if submitted."""
    conn = store._connect()
    with transaction(conn, immediate=True) as db:
        row = db.execute(
            "SELECT message_class FROM business_governor_reservations WHERE transport_profile=? AND account_id=? AND attempt_id=?",
            (account.transport_profile, account.account_id, attempt_id),
        ).fetchone()

        db.execute(
            "DELETE FROM business_governor_reservations WHERE transport_profile=? AND account_id=? AND attempt_id=?",
            (account.transport_profile, account.account_id, attempt_id),
        )

        message_class = row["message_class"] if row else "cold"
        if submitted:
            attempt = db.execute("SELECT state FROM business_attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
            success = not attempt or attempt["state"] not in ("failed", "unknown")
            db.execute(
                "INSERT INTO business_governor_attempts(transport_profile, account_id, message_class, timestamp_ms, success) VALUES(?,?,?,?,?)",
                (account.transport_profile, account.account_id, message_class, finished_at_ms, int(success)),
            )
            db.execute(
                "INSERT INTO business_governor_accounts(transport_profile, account_id, high_watermark_ms) VALUES(?,?,?) "
                "ON CONFLICT(transport_profile, account_id) DO UPDATE SET high_watermark_ms=MAX(high_watermark_ms, excluded.high_watermark_ms)",
                (account.transport_profile, account.account_id, finished_at_ms),
            )


def governor_pause_account(
    store,
    principal: Principal,
    account: AccountKey,
    *,
    reason: str,
    now_ms: int,
) -> None:
    """Pause an account."""
    conn = store._connect()
    with transaction(conn, immediate=True) as db:
        db.execute(
            "INSERT INTO business_governor_accounts(transport_profile, account_id, paused, pause_reason, high_watermark_ms) VALUES(?,?,1,?,?) "
            "ON CONFLICT(transport_profile, account_id) DO UPDATE SET paused=1, pause_reason=excluded.pause_reason, high_watermark_ms=MAX(high_watermark_ms, excluded.high_watermark_ms)",
            (account.transport_profile, account.account_id, reason, now_ms),
        )


def governor_resume_account(
    store,
    principal: Principal,
    account: AccountKey,
    *,
    now_ms: int,
) -> None:
    """Resume an account."""
    conn = store._connect()
    with transaction(conn, immediate=True) as db:
        db.execute(
            "INSERT INTO business_governor_accounts(transport_profile, account_id, paused, pause_reason, high_watermark_ms) VALUES(?,?,0,NULL,?) "
            "ON CONFLICT(transport_profile, account_id) DO UPDATE SET paused=0, pause_reason=NULL, high_watermark_ms=MAX(high_watermark_ms, excluded.high_watermark_ms)",
            (account.transport_profile, account.account_id, now_ms),
        )
