"""Zone-counter increment integration tests (plan tasks 3.1–3.4).

Run against the real, migrated + seeded Postgres from docker-compose.test.yml.
Each test starts from a freshly-seeded baseline (all zone counters at 0).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.models import ZONES, TelemetryEvent
from app.persistence import persist_telemetry, zone_entry_counts

from .helpers import zone_count


def test_zone_entry_increments_exactly_that_zone():
    """3.1 — one event with zone_entered increments exactly that zone to 1 and
    changes no other zone."""
    persist_telemetry(
        TelemetryEvent(
            vehicle_id="v-1", status="moving", battery_pct=50, zone_entered="receiving_staging"
        )
    )

    counts = zone_entry_counts()
    assert counts["receiving_staging"] == 1
    assert sum(counts.values()) == 1
    for zone_id, count in counts.items():
        if zone_id != "receiving_staging":
            assert count == 0


def test_null_zone_entered_leaves_all_counts_unchanged():
    """3.2 — an event with zone_entered=null leaves every zone's entry_count at 0."""
    persist_telemetry(
        TelemetryEvent(vehicle_id="v-2", status="idle", battery_pct=70)
    )

    counts = zone_entry_counts()
    assert set(counts) == set(ZONES)
    assert all(count == 0 for count in counts.values())


def test_concurrent_entries_to_one_zone_count_exactly_n():
    """3.3 — N concurrent zone_entered events for one zone yield entry_count == N
    exactly (no lost or double-counted increments)."""
    n = 50
    events = [
        TelemetryEvent(
            vehicle_id=f"v-{i:03d}",
            status="moving",
            battery_pct=float(i % 101),
            zone_entered="high_bay_1",
        )
        for i in range(n)
    ]

    with ThreadPoolExecutor(max_workers=25) as pool:
        list(pool.map(persist_telemetry, events))

    assert zone_count("high_bay_1") == n

    counts = zone_entry_counts()
    assert counts["high_bay_1"] == n
    assert sum(counts.values()) == n  # every increment landed on high_bay_1 only


def test_zone_entry_counts_returns_all_seeded_zones_with_live_totals():
    """3.4 — zone_entry_counts() returns all ~20 seeded zones with live totals."""
    persist_telemetry(
        TelemetryEvent(
            vehicle_id="a", status="moving", battery_pct=10, zone_entered="receiving_staging"
        )
    )
    persist_telemetry(
        TelemetryEvent(
            vehicle_id="b", status="moving", battery_pct=20, zone_entered="receiving_staging"
        )
    )
    persist_telemetry(
        TelemetryEvent(
            vehicle_id="c", status="idle", battery_pct=30, zone_entered="pick_zone_2"
        )
    )
    persist_telemetry(
        TelemetryEvent(vehicle_id="d", status="idle", battery_pct=40)  # null zone
    )

    counts = zone_entry_counts()

    assert set(counts) == set(ZONES)
    assert len(counts) == len(ZONES)
    assert counts["receiving_staging"] == 2
    assert counts["pick_zone_2"] == 1
    # Every other seeded zone reports 0 — never-entered zones are still present.
    entered = {"receiving_staging", "pick_zone_2"}
    for zone_id, count in counts.items():
        if zone_id not in entered:
            assert count == 0
