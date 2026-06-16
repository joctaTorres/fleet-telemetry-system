"""Synchronous in-transaction anomaly detection + read-seam integration tests.

Runs against the real Postgres from ``docker-compose.test.yml``. Covers plan
tasks 5.1–5.4: each stateless rule fires only when its threshold is crossed,
each stateful rule fires only against an appropriate prior reading (and never on
a first event), a multi-violation event writes one row per rule, and
``recent_anomalies`` returns a vehicle's in-window rows (inclusive bounds) and
excludes everything else.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models import TelemetryEvent
from app.persistence import persist_telemetry, recent_anomalies

from .helpers import anomaly_types

BASE = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)


def _at(seconds: float) -> datetime:
    return BASE + timedelta(seconds=seconds)


# ── 5.1 Stateless rules ─────────────────────────────────────────────────────


def test_fault_status_fires():
    persist_telemetry(
        TelemetryEvent(vehicle_id="s-fault", status="fault", battery_pct=80)
    )
    assert anomaly_types("s-fault") == ["fault_status"]


def test_non_empty_error_codes_fire():
    persist_telemetry(
        TelemetryEvent(
            vehicle_id="s-err",
            status="moving",
            battery_pct=80,
            error_codes=["E101"],
        )
    )
    assert "error_codes" in anomaly_types("s-err")


def test_empty_error_codes_do_not_fire():
    persist_telemetry(
        TelemetryEvent(
            vehicle_id="s-noerr", status="moving", battery_pct=80, error_codes=[]
        )
    )
    assert "error_codes" not in anomaly_types("s-noerr")


def test_low_battery_while_not_charging_fires():
    persist_telemetry(
        TelemetryEvent(vehicle_id="s-low", status="moving", battery_pct=12)
    )
    assert "low_battery" in anomaly_types("s-low")


def test_low_battery_while_charging_does_not_fire():
    persist_telemetry(
        TelemetryEvent(vehicle_id="s-chg", status="charging", battery_pct=12)
    )
    assert "low_battery" not in anomaly_types("s-chg")


def test_battery_at_threshold_does_not_fire():
    persist_telemetry(
        TelemetryEvent(vehicle_id="s-thr", status="moving", battery_pct=15)
    )
    assert "low_battery" not in anomaly_types("s-thr")


def test_overspeed_above_limit_fires():
    persist_telemetry(
        TelemetryEvent(
            vehicle_id="s-fast", status="moving", battery_pct=80, speed_mps=6
        )
    )
    assert "overspeed" in anomaly_types("s-fast")


def test_speed_at_limit_does_not_fire():
    persist_telemetry(
        TelemetryEvent(
            vehicle_id="s-lim", status="moving", battery_pct=80, speed_mps=5
        )
    )
    assert "overspeed" not in anomaly_types("s-lim")


def test_clean_first_event_raises_no_anomalies():
    persist_telemetry(
        TelemetryEvent(
            vehicle_id="s-clean", status="moving", battery_pct=80, speed_mps=2
        )
    )
    assert anomaly_types("s-clean") == []


# ── 5.2 Stateful rules ──────────────────────────────────────────────────────


def test_stuck_fires_when_moving_and_slow_for_at_least_10s():
    persist_telemetry(
        TelemetryEvent(
            vehicle_id="st-stuck",
            status="moving",
            battery_pct=80,
            speed_mps=0.05,
            recorded_at=_at(0),
        )
    )
    persist_telemetry(
        TelemetryEvent(
            vehicle_id="st-stuck",
            status="moving",
            battery_pct=80,
            speed_mps=0.05,
            recorded_at=_at(11),
        )
    )
    assert "stuck" in anomaly_types("st-stuck")


def test_stuck_does_not_fire_when_recent():
    persist_telemetry(
        TelemetryEvent(
            vehicle_id="st-recent",
            status="moving",
            battery_pct=80,
            speed_mps=0.05,
            recorded_at=_at(0),
        )
    )
    persist_telemetry(
        TelemetryEvent(
            vehicle_id="st-recent",
            status="moving",
            battery_pct=80,
            speed_mps=0.05,
            recorded_at=_at(4),
        )
    )
    assert "stuck" not in anomaly_types("st-recent")


def test_teleport_fires_when_implied_speed_too_high():
    persist_telemetry(
        TelemetryEvent(
            vehicle_id="st-tp",
            status="moving",
            battery_pct=80,
            pos_x=0,
            pos_y=0,
            recorded_at=_at(0),
        )
    )
    persist_telemetry(
        TelemetryEvent(
            vehicle_id="st-tp",
            status="moving",
            battery_pct=80,
            pos_x=100,
            pos_y=0,
            recorded_at=_at(1),
        )
    )
    assert "teleport" in anomaly_types("st-tp")


def test_teleport_does_not_fire_for_plausible_speed():
    persist_telemetry(
        TelemetryEvent(
            vehicle_id="st-noTp",
            status="moving",
            battery_pct=80,
            pos_x=0,
            pos_y=0,
            recorded_at=_at(0),
        )
    )
    persist_telemetry(
        TelemetryEvent(
            vehicle_id="st-noTp",
            status="moving",
            battery_pct=80,
            pos_x=100,
            pos_y=0,
            recorded_at=_at(10),
        )
    )
    assert "teleport" not in anomaly_types("st-noTp")


def test_battery_rising_while_not_charging_fires():
    persist_telemetry(
        TelemetryEvent(
            vehicle_id="st-rise", status="moving", battery_pct=40, recorded_at=_at(0)
        )
    )
    persist_telemetry(
        TelemetryEvent(
            vehicle_id="st-rise", status="moving", battery_pct=45, recorded_at=_at(1)
        )
    )
    assert "battery_rising" in anomaly_types("st-rise")


def test_battery_rising_while_charging_is_normal():
    persist_telemetry(
        TelemetryEvent(
            vehicle_id="st-crise",
            status="charging",
            battery_pct=40,
            recorded_at=_at(0),
        )
    )
    persist_telemetry(
        TelemetryEvent(
            vehicle_id="st-crise",
            status="charging",
            battery_pct=45,
            recorded_at=_at(1),
        )
    )
    assert "battery_rising" not in anomaly_types("st-crise")


def test_stateful_rules_do_not_fire_on_first_reading():
    persist_telemetry(
        TelemetryEvent(
            vehicle_id="st-first",
            status="moving",
            battery_pct=80,
            speed_mps=0.05,
            pos_x=100,
            pos_y=100,
            recorded_at=_at(0),
        )
    )
    fired = anomaly_types("st-first")
    assert "stuck" not in fired
    assert "teleport" not in fired
    assert "battery_rising" not in fired


# ── 5.3 Multi-violation event ───────────────────────────────────────────────


def test_multi_violation_event_writes_one_row_per_rule():
    persist_telemetry(
        TelemetryEvent(
            vehicle_id="multi",
            status="fault",
            battery_pct=12,
            speed_mps=6,
            error_codes=["E1"],
        )
    )
    fired = sorted(anomaly_types("multi"))
    assert fired == ["error_codes", "fault_status", "low_battery", "overspeed"]


# ── 5.4 recent_anomalies read seam ──────────────────────────────────────────


def _fault(vehicle_id: str, when: datetime) -> None:
    persist_telemetry(
        TelemetryEvent(
            vehicle_id=vehicle_id, status="fault", battery_pct=80, recorded_at=when
        )
    )


def test_recent_anomalies_filters_by_vehicle_and_window():
    since, until = _at(0), _at(100)

    # agv-1: one before the window, three inside (incl. both boundaries), one after.
    _fault("agv-1", _at(-10))
    _fault("agv-1", since)  # exactly at since (inclusive)
    _fault("agv-1", _at(50))  # strictly inside
    _fault("agv-1", until)  # exactly at until (inclusive)
    _fault("agv-1", _at(110))  # after the window
    # agv-2: one inside the window — must be excluded from agv-1's read.
    _fault("agv-2", _at(50))

    rows = recent_anomalies("agv-1", since, until)

    assert all(r["vehicle_id"] == "agv-1" for r in rows)
    detected = [r["detected_at"] for r in rows]
    assert detected == [since, _at(50), until]  # in-window, ordered, inclusive


def test_recent_anomalies_returns_nothing_for_vehicle_without_rows():
    assert recent_anomalies("agv-9", _at(0), _at(100)) == []
