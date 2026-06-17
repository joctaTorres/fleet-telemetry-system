"""By-absence comms-loss watchdog integration tests.

Drives the ``detect_comms_loss(now)`` sweep seam directly with an injected
``now`` (deterministic, no sleeps) against the real Postgres from
``docker-compose.test.yml``. Covers the strict timeout boundary, the
once-per-silence-episode idempotency guard, and re-flagging after a vehicle
recovers and goes silent again.

A vehicle's last-seen state is seeded the real way — a clean telemetry event
through ``persist_telemetry`` — so its ``recorded_at`` is what the sweep reads.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models import TelemetryEvent
from app.persistence import detect_comms_loss, persist_telemetry

from .helpers import anomaly_types

BASE = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)


def _at(seconds: float) -> datetime:
    return BASE + timedelta(seconds=seconds)


def _seen(vehicle_id: str, when: datetime) -> None:
    """Record a clean reading so the vehicle's last-seen time is ``when``."""
    persist_telemetry(
        TelemetryEvent(
            vehicle_id=vehicle_id,
            status="moving",
            battery_pct=80,
            speed_mps=2,
            recorded_at=when,
        )
    )


# ── 4.1 Strict timeout boundary ─────────────────────────────────────────────


def test_comms_loss_fires_only_past_strict_timeout():
    # One sweep at now=_at(6): cutoff is _at(1) (now - 5s), strict (<).
    _seen("v-gap", _at(0))     # gap 6s  → strictly older than cutoff → flagged
    _seen("v-exact", _at(1))   # gap 5s  → recorded_at == cutoff → NOT flagged
    _seen("v-recent", _at(2))  # gap 4s  → newer than cutoff → NOT flagged

    flagged = detect_comms_loss(now=_at(6))

    assert flagged == 1
    assert anomaly_types("v-gap") == ["comms_loss"]
    assert "comms_loss" not in anomaly_types("v-exact")
    assert "comms_loss" not in anomaly_types("v-recent")


# ── 4.2 Idempotent: one comms_loss per silence episode ──────────────────────


def test_continuing_silence_flags_once_then_reflags_after_recovery():
    _seen("v", _at(0))

    # First sweep past the timeout flags the vehicle exactly once.
    assert detect_comms_loss(now=_at(6)) == 1
    assert anomaly_types("v").count("comms_loss") == 1

    # Subsequent sweeps during the same silence must not re-fire.
    assert detect_comms_loss(now=_at(7)) == 0
    assert detect_comms_loss(now=_at(8)) == 0
    assert anomaly_types("v").count("comms_loss") == 1

    # The vehicle reports again (recorded_at advances), then goes silent anew.
    _seen("v", _at(10))
    assert detect_comms_loss(now=_at(16)) == 1
    assert anomaly_types("v").count("comms_loss") == 2


def test_no_vehicles_means_no_flags():
    assert detect_comms_loss(now=_at(100)) == 0
