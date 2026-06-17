"""Aggregate + concurrency integration tests (plan tasks 4.3 and 4.4)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.models import STATUSES, TelemetryEvent
from app.persistence import aggregate_fleet_state, persist_telemetry

from .helpers import current_state, current_state_row_count


def test_aggregate_groups_by_status_and_zero_fills():
    """Aggregate reports all four statuses; absent ones are zero."""
    persist_telemetry(TelemetryEvent(vehicle_id="a", status="moving", battery_pct=50))
    persist_telemetry(TelemetryEvent(vehicle_id="b", status="moving", battery_pct=60))
    persist_telemetry(TelemetryEvent(vehicle_id="c", status="idle", battery_pct=70))

    counts = aggregate_fleet_state()

    assert set(counts) == set(STATUSES)
    assert counts == {"idle": 1, "moving": 2, "charging": 0, "fault": 0}


def test_fifty_distinct_vehicles_persisted_concurrently():
    """4.3 — 50 distinct vehicles persisted concurrently: one row each, counts
    sum to 50, and every row matches that vehicle's persisted event."""
    statuses = list(STATUSES)
    events = [
        TelemetryEvent(
            vehicle_id=f"v-{i:03d}",
            status=statuses[i % len(statuses)],
            battery_pct=float(i % 101),
        )
        for i in range(50)
    ]

    with ThreadPoolExecutor(max_workers=25) as pool:
        list(pool.map(persist_telemetry, events))

    counts = aggregate_fleet_state()
    assert sum(counts.values()) == 50

    expected: dict[str, int] = {s: 0 for s in STATUSES}
    for ev in events:
        assert current_state_row_count(ev.vehicle_id) == 1
        assert current_state(ev.vehicle_id) == (ev.status, ev.battery_pct)
        expected[ev.status] += 1

    assert counts == expected


def test_repeated_concurrent_upserts_for_one_vehicle_count_once():
    """4.4 — many concurrent events for one vehicle keep exactly one row that
    contributes exactly one to the aggregate."""
    events = [
        TelemetryEvent(vehicle_id="v-3", status="idle", battery_pct=float(i % 101))
        for i in range(30)
    ]

    with ThreadPoolExecutor(max_workers=15) as pool:
        list(pool.map(persist_telemetry, events))

    assert current_state_row_count("v-3") == 1

    counts = aggregate_fleet_state()
    assert counts["idle"] == 1
    assert sum(counts.values()) == 1

    # The surviving row must be one of the committed events for this vehicle.
    row = current_state("v-3")
    assert row is not None
    status, battery = row
    assert status == "idle"
    assert battery in {ev.battery_pct for ev in events}
