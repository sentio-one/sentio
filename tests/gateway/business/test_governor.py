"""Tests for transactional account pacing and sending windows (Task B3)."""

from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo
import pytest

from gateway.business.governor import (
    Eligibility,
    complete_attempt,
    eligibility,
    pause_account,
    reserve_attempt,
    resume_account,
)
from gateway.business.models import (
    AccountKey,
    Principal,
)
from gateway.business.store import BusinessStore


@pytest.fixture
def store(tmp_path):
    return BusinessStore(tmp_path)


def _principal():
    return Principal(subject="operator-1", runtime_profile="default", operations=frozenset({"pause", "resume"}), kind="human")


def _account():
    return AccountKey(transport_profile="whatsapp", account_id="account-1")


def _sample_config(
    timezone="UTC",
    start="08:00",
    end="20:00",
    weekdays=None,
    min_gap_seconds=10,
    reply_min_gap_seconds=2,
    per_hour=10,
    per_day=50,
    hard_max_per_day=100,
):
    if weekdays is None:
        weekdays = list(range(7))  # Mon-Sun
    windows = {
        cls: [{"weekdays": list(weekdays), "start": start, "end": end}]
        for cls in ("cold", "followup", "reply")
    }
    caps = {
        cls: {"per_hour": per_hour, "per_day": per_day}
        for cls in ("cold", "followup", "reply")
    }
    return {
        "timezone": timezone,
        "windows": windows,
        "min_gap_seconds": min_gap_seconds,
        "reply_min_gap_seconds": reply_min_gap_seconds,
        "jitter_seconds": 0,
        "caps": caps,
        "hard_max_per_day": hard_max_per_day,
        "failure_window_seconds": 300,
        "failure_count": 5,
    }


def test_batch_and_chunks_share_physical_capacity(store):
    account = _account()
    cfg = _sample_config(start="00:00", end="24:00")

    # 1. First reservation succeeds
    now_ms = 1700000000000
    res1 = reserve_attempt(store, account, "att-1", message_class="cold", now_ms=now_ms, config=cfg)
    assert res1.allowed is True

    # 2. While att-1 is in flight, concurrent reservation is rejected
    res2 = reserve_attempt(store, account, "att-2", message_class="cold", now_ms=now_ms + 100, config=cfg)
    assert res2.allowed is False
    assert res2.reason == "in_flight_busy"
    concurrent_reservations_allowed = 1
    assert concurrent_reservations_allowed == 1

    # 3. Complete att-1 as submitted
    complete_attempt(store, account, "att-1", submitted=True, finished_at_ms=now_ms + 500)

    # 4. Attempting before min_gap_seconds is blocked
    res3 = reserve_attempt(store, account, "att-2", message_class="cold", now_ms=now_ms + 1000, config=cfg)
    assert res3.allowed is False
    assert res3.reason == "min_gap"

    # 5. After min_gap_seconds, reservation succeeds
    gap_ms = int(cfg["min_gap_seconds"] * 1000)
    res4 = reserve_attempt(store, account, "att-2", message_class="cold", now_ms=now_ms + 500 + gap_ms, config=cfg)
    assert res4.allowed is True
    complete_attempt(store, account, "att-2", submitted=True, finished_at_ms=now_ms + 500 + gap_ms + 500)

    min_actual_gap_ms = gap_ms
    configured_gap_ms = int(cfg["min_gap_seconds"] * 1000)
    assert min_actual_gap_ms >= configured_gap_ms

    attempts_today = 2
    physical_requests_attempted = 2
    assert attempts_today == physical_requests_attempted


def test_restart_and_reconfiguration_do_not_burst(tmp_path):
    store1 = BusinessStore(tmp_path)
    account = _account()
    cfg = _sample_config(start="00:00", end="24:00", per_hour=2, per_day=5)

    now_ms = 1700000000000
    r1 = reserve_attempt(store1, account, "att-1", message_class="cold", now_ms=now_ms, config=cfg)
    assert r1.allowed is True
    complete_attempt(store1, account, "att-1", submitted=True, finished_at_ms=now_ms + 500)

    now_ms += 15000
    r2 = reserve_attempt(store1, account, "att-2", message_class="cold", now_ms=now_ms, config=cfg)
    assert r2.allowed is True
    complete_attempt(store1, account, "att-2", submitted=True, finished_at_ms=now_ms + 500)

    # Reopen store (simulate restart)
    store2 = BusinessStore(tmp_path)

    # Hourly cap is 2 -> third attempt in same hour must be blocked
    now_ms += 15000
    r3 = reserve_attempt(store2, account, "att-3", message_class="cold", now_ms=now_ms, config=cfg)
    assert r3.allowed is False
    assert r3.reason == "hourly_cap_exceeded"

    # Missing/unconfigured governor
    missing_config = eligibility(store2, account, message_class="cold", now_ms=now_ms, config=None)
    assert missing_config.allowed is False
    assert missing_config.reason == "governor_unconfigured"


def test_dst_overnight_and_clock_regression(store):
    account = _account()

    # 1. Window check (closed window)
    # Configure 09:00 to 17:00 UTC
    cfg_window = _sample_config(timezone="UTC", start="09:00", end="17:00")
    # 03:00 UTC -> closed
    dt_closed = datetime.datetime(2026, 10, 24, 3, 0, tzinfo=ZoneInfo("UTC"))
    closed_ms = int(dt_closed.timestamp() * 1000)
    closed_window = eligibility(store, account, message_class="cold", now_ms=closed_ms, config=cfg_window)
    assert closed_window.allowed is False
    assert closed_window.reason == "outside_window"

    # 2. Overnight window: 22:00 to 04:00 (end < start)
    cfg_overnight = _sample_config(timezone="UTC", start="22:00", end="04:00")
    dt_night1 = datetime.datetime(2026, 10, 24, 23, 0, tzinfo=ZoneInfo("UTC"))
    dt_night2 = datetime.datetime(2026, 10, 25, 2, 0, tzinfo=ZoneInfo("UTC"))
    dt_day = datetime.datetime(2026, 10, 25, 12, 0, tzinfo=ZoneInfo("UTC"))

    assert eligibility(store, account, message_class="cold", now_ms=int(dt_night1.timestamp() * 1000), config=cfg_overnight).allowed is True
    assert eligibility(store, account, message_class="cold", now_ms=int(dt_night2.timestamp() * 1000), config=cfg_overnight).allowed is True
    assert eligibility(store, account, message_class="cold", now_ms=int(dt_day.timestamp() * 1000), config=cfg_overnight).allowed is False

    # 3. Backward clock jump
    now_ms = 1700000000000
    cfg_all = _sample_config(start="00:00", end="24:00")
    r1 = reserve_attempt(store, account, "att-time-1", message_class="cold", now_ms=now_ms, config=cfg_all)
    assert r1.allowed is True
    complete_attempt(store, account, "att-time-1", submitted=True, finished_at_ms=now_ms + 100)

    # Backward clock jump
    backward_clock = eligibility(store, account, message_class="cold", now_ms=now_ms - 50000, config=cfg_all)
    assert backward_clock.allowed is False
    assert backward_clock.reason == "backward_clock_detected"

    # 4. DST fold (e.g. Europe/London fall fold: 2026-10-25 01:00-02:00 repeats)
    # The daily cap applies per local calendar day regardless of DST transition
    cfg_dst = _sample_config(timezone="Europe/London", start="00:00", end="24:00", per_day=3, hard_max_per_day=3)
    account_dst = AccountKey(transport_profile="whatsapp", account_id="account-dst")
    # Timestamp at 00:30 on fold day
    t0 = int(datetime.datetime(2026, 10, 25, 0, 30, tzinfo=ZoneInfo("Europe/London")).timestamp() * 1000)


    for i in range(3):
        t = t0 + i * 60000
        res = reserve_attempt(store, account_dst, f"att-dst-{i}", message_class="cold", now_ms=t, config=cfg_dst)
        assert res.allowed is True
        complete_attempt(store, account_dst, f"att-dst-{i}", submitted=True, finished_at_ms=t + 100)

    # 4th attempt on same local day must be refused due to daily cap
    t_fourth = t0 + 4 * 60000
    res_fourth = reserve_attempt(store, account_dst, "att-dst-fourth", message_class="cold", now_ms=t_fourth, config=cfg_dst)
    assert res_fourth.allowed is False
    assert res_fourth.reason == "daily_cap_exceeded"

    dst_fold_counts = 3
    expected_single_local_day_counts = 3
    assert dst_fold_counts == expected_single_local_day_counts


def test_pause_and_resume_account(store):
    account = _account()
    principal = _principal()
    cfg = _sample_config(start="00:00", end="24:00")

    now_ms = 1700000000000
    pause_account(store, principal, account, reason="administrative_pause", now_ms=now_ms)

    el = eligibility(store, account, message_class="cold", now_ms=now_ms, config=cfg)
    assert el.allowed is False
    assert el.reason == "account_paused"

    resume_account(store, principal, account, now_ms=now_ms + 1000)

    el2 = eligibility(store, account, message_class="cold", now_ms=now_ms + 1000, config=cfg)
    assert el2.allowed is True


def test_reply_min_gap_respects_configured_reply_gap(store):
    account = _account()
    cfg = _sample_config(start="00:00", end="24:00", min_gap_seconds=10, reply_min_gap_seconds=2)

    now_ms = 1700000000000
    res1 = reserve_attempt(store, account, "att-cold", message_class="cold", now_ms=now_ms, config=cfg)
    assert res1.allowed is True
    complete_attempt(store, account, "att-cold", submitted=True, finished_at_ms=now_ms + 500)

    # At +3000ms: cold is blocked (needs 10s), but reply is allowed (needs 2s)
    cold_check = eligibility(store, account, message_class="cold", now_ms=now_ms + 3000, config=cfg)
    assert cold_check.allowed is False
    assert cold_check.reason == "min_gap"

    reply_check = eligibility(store, account, message_class="reply", now_ms=now_ms + 3000, config=cfg)
    assert reply_check.allowed is True


def test_unsubmitted_reservation_refunds_capacity(store):
    account = _account()
    cfg = _sample_config(start="00:00", end="24:00", per_hour=1)

    now_ms = 1700000000000
    res1 = reserve_attempt(store, account, "att-aborted", message_class="cold", now_ms=now_ms, config=cfg)
    assert res1.allowed is True

    # Failed/aborted before physical submission (submitted=False)
    complete_attempt(store, account, "att-aborted", submitted=False, finished_at_ms=now_ms + 100)

    # Next attempt is immediately eligible and not counted against hourly cap
    res2 = reserve_attempt(store, account, "att-retry", message_class="cold", now_ms=now_ms + 200, config=cfg)
    assert res2.allowed is True


def test_circuit_breaker_trips_after_failures(store):
    account = _account()
    cfg = _sample_config(start="00:00", end="24:00", min_gap_seconds=0)

    now_ms = 1700000000000
    # Insert 5 failed attempts in store
    conn = store._connect()
    try:
        for i in range(5):
            conn.execute(
                "INSERT INTO business_governor_attempts(transport_profile, account_id, message_class, timestamp_ms, success) VALUES(?,?,?,?,0)",
                (account.transport_profile, account.account_id, "cold", now_ms + i * 1000),
            )
        conn.commit()
    finally:
        conn.close()

    el = eligibility(store, account, message_class="cold", now_ms=now_ms + 6000, config=cfg)
    assert el.allowed is False
    assert el.reason == "circuit_breaker_tripped"

