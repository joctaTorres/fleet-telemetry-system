"""Proof-of-work for ``cdc-pgoutput-translate``: the CDC *source*, end to end.

This is the change's own proof — not the phase blackbox proof
(``test_realtime_ws.py``), which the ``cdc-end-to-end-ws`` follow-on lands once
the consumer is wired into the running topology. Here the risky seam is proven in
isolation: commit a watched-table change on the primary, and assert the correct
event JSON lands on the ``fleet:events`` Redis channel — decoded from the WAL
through a real ``pgoutput`` logical slot by the in-process consumer, with no
replica, no WebSocket, and no dual-write.

Runs against the real primary + Redis from ``docker-compose.test.yml``; the
``cdc_stream`` fixture starts the consumer on a background thread and exposes the
next-event reader.
"""

from __future__ import annotations

import psycopg

from app.cdc import PUBLICATION_NAME, WATCHED_TABLES
from app.config import get_dsn
from app.events import ANOMALY_DETECTED, VEHICLE_STATE_CHANGED, ZONE_COUNT_CHANGED
from app.models import TelemetryEvent
from app.persistence import persist_telemetry

from .conftest import rolled_back_primary_write

#: Sub-second propagation bound, matching the phase success criteria. The decode
#: path is fast; a committed change should surface as an event well within this.
SUB_SECOND = 1.0


def _commit(sql: str, params: dict) -> None:
    """Execute ``sql`` on the primary in its own committed transaction."""
    with psycopg.connect(get_dsn(), autocommit=True) as conn:
        conn.execute(sql, params)


def test_vehicle_upsert_yields_one_state_changed(cdc_stream):
    """4.1 — a committed current-state upsert → exactly one vehicle_state_changed."""
    persist_telemetry(
        TelemetryEvent(vehicle_id="veh-1", status="moving", battery_pct=72)
    )

    event = cdc_stream.read_next(SUB_SECOND)
    assert event is not None
    assert event["type"] == VEHICLE_STATE_CHANGED
    assert event["payload"] == {
        "vehicle_id": "veh-1",
        "status": "moving",
        "battery_pct": 72.0,
    }
    # Exactly one: the raw_events insert in the same transaction is not published.
    assert cdc_stream.read_next(0.5) is None


def test_anomaly_insert_yields_one_anomaly_detected(cdc_stream):
    """4.2 — a committed anomalies insert → exactly one anomaly_detected."""
    _commit(
        "INSERT INTO anomalies (vehicle_id, anomaly_type, detail, detected_at) "
        "VALUES (%(vehicle_id)s, %(anomaly_type)s, %(detail)s, now())",
        {"vehicle_id": "veh-2", "anomaly_type": "overspeed", "detail": "speed_mps=9"},
    )

    event = cdc_stream.read_next(SUB_SECOND)
    assert event is not None
    assert event["type"] == ANOMALY_DETECTED
    assert event["payload"]["vehicle_id"] == "veh-2"
    assert event["payload"]["anomaly_type"] == "overspeed"
    assert event["payload"]["detail"] == "speed_mps=9"
    assert cdc_stream.read_next(0.5) is None


def test_zone_increment_yields_one_zone_count_changed(cdc_stream):
    """4.3 — a committed zone-counter increment → one zone_count_changed (new count)."""
    _commit(
        "UPDATE zone_counts SET entry_count = entry_count + 1 "
        "WHERE zone_id = %(zone_id)s",
        {"zone_id": "inbound_dock_a"},
    )

    event = cdc_stream.read_next(SUB_SECOND)
    assert event is not None
    assert event["type"] == ZONE_COUNT_CHANGED
    # Counters are reset to 0 before each test, so the increment yields exactly 1.
    assert event["payload"] == {"zone_id": "inbound_dock_a", "entry_count": 1}
    assert cdc_stream.read_next(0.5) is None


def test_each_envelope_carries_its_contract_type(cdc_stream):
    """4.4 — each derived envelope carries the correct app.events ``type``."""
    persist_telemetry(
        TelemetryEvent(vehicle_id="veh-3", status="idle", battery_pct=40)
    )
    assert cdc_stream.read_next(SUB_SECOND)["type"] == VEHICLE_STATE_CHANGED

    _commit(
        "INSERT INTO anomalies (vehicle_id, anomaly_type, detail, detected_at) "
        "VALUES (%(vehicle_id)s, 'low_battery', 'battery_pct=9', now())",
        {"vehicle_id": "veh-3"},
    )
    assert cdc_stream.read_next(SUB_SECOND)["type"] == ANOMALY_DETECTED

    _commit(
        "UPDATE zone_counts SET entry_count = entry_count + 1 "
        "WHERE zone_id = %(zone_id)s",
        {"zone_id": "inbound_dock_b"},
    )
    assert cdc_stream.read_next(SUB_SECOND)["type"] == ZONE_COUNT_CHANGED


def test_rolled_back_write_yields_no_event(cdc_stream):
    """4.5 — an aborted write against a watched table emits nothing.

    pgoutput frames changes by Begin/Commit, so an uncommitted transaction never
    decodes into an event. A committed sentinel afterward proves the absence is
    real — the consumer is alive and would have delivered a committed change.
    """
    rolled_back_primary_write(
        "UPDATE zone_counts SET entry_count = entry_count + 1 "
        "WHERE zone_id = %(zone_id)s",
        {"zone_id": "receiving_staging"},
    )
    assert cdc_stream.read_next(SUB_SECOND) is None

    _commit(
        "UPDATE zone_counts SET entry_count = entry_count + 1 "
        "WHERE zone_id = %(zone_id)s",
        {"zone_id": "receiving_staging"},
    )
    sentinel = cdc_stream.read_next(SUB_SECOND)
    assert sentinel is not None
    assert sentinel["type"] == ZONE_COUNT_CHANGED


def test_non_watched_table_write_yields_no_event(cdc_stream):
    """4.6 — a committed write to an unpublished table (raw_events) emits nothing."""
    _commit(
        "INSERT INTO raw_events (vehicle_id, status, battery_pct, recorded_at) "
        "VALUES (%(vehicle_id)s, 'moving', 55, now())",
        {"vehicle_id": "veh-4"},
    )
    assert cdc_stream.read_next(SUB_SECOND) is None

    # A committed watched-table write still flows, proving the stream is live.
    _commit(
        "UPDATE zone_counts SET entry_count = entry_count + 1 "
        "WHERE zone_id = %(zone_id)s",
        {"zone_id": "aisle_a"},
    )
    assert cdc_stream.read_next(SUB_SECOND)["type"] == ZONE_COUNT_CHANGED


def test_publication_members_are_exactly_the_watched_tables():
    """4.7 — the publication names exactly the three watched tables, no more."""
    with psycopg.connect(get_dsn(), autocommit=True) as conn:
        members = {
            row[0]
            for row in conn.execute(
                "SELECT tablename FROM pg_publication_tables WHERE pubname = %s",
                (PUBLICATION_NAME,),
            ).fetchall()
        }
    assert members == set(WATCHED_TABLES)
    assert members == {"vehicle_current_state", "anomalies", "zone_counts"}
