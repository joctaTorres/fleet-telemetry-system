"""Fault-transition persistence-layer integration tests (fault-transition-core).

Proves transition_to_fault against the real Postgres from docker-compose.test.yml:
a transition cancels the active mission, opens exactly one maintenance record, and
sets status='fault' — and stays correct under duplicate (sequential) and
concurrent (threaded) fault events for the same vehicle.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.persistence import transition_to_fault

from .helpers import (
    active_mission_count,
    maintenance_record_count,
    maintenance_records,
    mission_status_counts,
    seed_active_mission,
    seed_vehicle,
    vehicle_status,
)


def test_single_transition_cancels_mission_opens_one_record_sets_fault():
    """4.1 — one transition: mission cancelled, one maintenance record, fault."""
    seed_vehicle("v-1", status="moving")
    mission_id = seed_active_mission("v-1")

    assert transition_to_fault("v-1", reason="overheat") is True

    assert vehicle_status("v-1") == "fault"
    assert active_mission_count("v-1") == 0
    assert mission_status_counts("v-1") == {"cancelled": 1}
    assert maintenance_record_count("v-1") == 1
    (rec_mission_id, reason, resolved_at), = maintenance_records("v-1")
    assert rec_mission_id == mission_id
    assert reason == "overheat"
    assert resolved_at is None


def test_duplicate_sequential_transition_is_noop():
    """4.2 — a second sequential transition is a no-op; True then False."""
    seed_vehicle("v-2", status="moving")
    seed_active_mission("v-2")

    assert transition_to_fault("v-2") is True
    assert transition_to_fault("v-2") is False

    assert vehicle_status("v-2") == "fault"
    assert mission_status_counts("v-2") == {"cancelled": 1}
    assert maintenance_record_count("v-2") == 1


def test_concurrent_transitions_for_one_vehicle_settle_exactly_once():
    """4.3 — many concurrent transitions leave exactly one cancelled mission and
    exactly one maintenance record; status fault; exactly one call returns True."""
    seed_vehicle("v-3", status="moving")
    seed_active_mission("v-3")

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(lambda _: transition_to_fault("v-3"), range(20)))

    assert sum(1 for r in results if r is True) == 1
    assert vehicle_status("v-3") == "fault"
    assert active_mission_count("v-3") == 0
    assert mission_status_counts("v-3") == {"cancelled": 1}
    assert maintenance_record_count("v-3") == 1


def test_fault_with_no_active_mission_opens_record_with_null_mission():
    """4.4 — a fault while idle (no active mission) writes one maintenance record
    with a null mission_id, sets fault, and cancels nothing."""
    seed_vehicle("v-4", status="idle")

    assert transition_to_fault("v-4", reason="manual") is True

    assert vehicle_status("v-4") == "fault"
    assert mission_status_counts("v-4") == {}
    assert maintenance_record_count("v-4") == 1
    (rec_mission_id, reason, resolved_at), = maintenance_records("v-4")
    assert rec_mission_id is None
    assert reason == "manual"
    assert resolved_at is None


def test_transition_does_not_touch_another_vehicles_mission():
    """4.5 — transitioning one vehicle leaves another's active mission untouched."""
    seed_vehicle("v-5", status="moving")
    seed_active_mission("v-5")
    seed_vehicle("v-6", status="moving")
    seed_active_mission("v-6")

    assert transition_to_fault("v-5") is True

    assert vehicle_status("v-5") == "fault"
    assert mission_status_counts("v-5") == {"cancelled": 1}

    assert vehicle_status("v-6") == "moving"
    assert active_mission_count("v-6") == 1
    assert mission_status_counts("v-6") == {"active": 1}
    assert maintenance_record_count("v-6") == 0
