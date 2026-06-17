"""Read-replica proof-of-work for the ``read-replica-split`` change.

This is the change's *own* proof — not the phase blackbox proof
(``test_realtime_ws.py``), which the CDC follow-on slices land once the event
source exists. Here the streaming physical read replica is proven end to end: a
committed write on the primary streams to the standby, and the frontend's read
seams — served from the replica pool — reflect it, while the write path stays on
the primary and the standby rejects writes.

Runs against the real primary + streaming replica from ``docker-compose.test.yml``.
"""

from __future__ import annotations

import asyncio

import psycopg
import pytest

from app.config import get_replica_dsn
from app.db import connection, replica_connection
from app.events import SNAPSHOT
from app.frontend_api import _build_snapshot, get_fleet_state, get_zone_counts
from app.models import TelemetryEvent
from app.persistence import aggregate_fleet_state, persist_telemetry, zone_entry_counts

from .conftest import rolled_back_primary_write, wait_replica_caught_up

#: Tables the migrations create on the primary that must stream to the standby.
_MIGRATED_TABLES = ("raw_events", "vehicle_current_state", "anomalies", "zone_counts")


def test_replica_is_hot_standby_with_migrated_schema():
    """6.1 — the replica is a hot standby that has the streamed migrated tables."""
    with psycopg.connect(get_replica_dsn()) as rconn:
        in_recovery = rconn.execute("SELECT pg_is_in_recovery()").fetchone()[0]
        assert in_recovery is True
        for table in _MIGRATED_TABLES:
            present = rconn.execute(
                "SELECT to_regclass(%s) IS NOT NULL", (f"public.{table}",)
            ).fetchone()[0]
            assert present, f"replica is missing streamed migrated table {table}"


def test_committed_write_reflected_in_replica_read_seams():
    """6.2 — a committed primary write is reflected by the replica read seams."""
    persist_telemetry(
        TelemetryEvent(
            vehicle_id="rv1", status="moving", battery_pct=80, zone_entered="zone-07"
        )
    )
    persist_telemetry(TelemetryEvent(vehicle_id="rv2", status="idle", battery_pct=55))
    wait_replica_caught_up()

    fleet = aggregate_fleet_state(replica_connection)
    zones = zone_entry_counts(replica_connection)
    assert fleet == {"idle": 1, "moving": 1, "charging": 0, "fault": 0}
    assert zones["zone-07"] == 1
    assert zones["zone-01"] == 0  # an untouched zone still reports zero

    # The REST handlers, which read through the replica pool, agree.
    assert get_fleet_state() == fleet
    assert get_zone_counts()["zone-07"] == 1


def test_uncommitted_write_not_visible_until_committed():
    """6.3 — a rolled-back primary write never appears on the replica."""
    rolled_back_primary_write(
        "UPDATE zone_counts SET entry_count = entry_count + 5 "
        "WHERE zone_id = %(zone_id)s",
        {"zone_id": "zone-09"},
    )
    wait_replica_caught_up()
    # The aborted increment is never visible on the standby.
    assert zone_entry_counts(replica_connection)["zone-09"] == 0

    # A genuinely committed increment, by contrast, does become visible — proving
    # the replica is live and streaming, not merely lagging.
    persist_telemetry(
        TelemetryEvent(
            vehicle_id="rv3", status="moving", battery_pct=40, zone_entered="zone-09"
        )
    )
    wait_replica_caught_up()
    assert zone_entry_counts(replica_connection)["zone-09"] == 1


def test_replica_rejects_direct_write():
    """6.4 — the standby is read-only and rejects a direct write."""
    with psycopg.connect(get_replica_dsn(), autocommit=True) as rconn:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            rconn.execute(
                "UPDATE zone_counts SET entry_count = entry_count + 1 "
                "WHERE zone_id = 'zone-01'"
            )


def test_frontend_reads_via_replica_writes_via_primary():
    """6.5 — reads derive through the replica pool; the write path targets primary."""
    # The two pools point at different servers: the read pool at the read-only
    # standby, the write pool at the writable primary.
    with replica_connection() as rconn:
        assert rconn.execute("SELECT pg_is_in_recovery()").fetchone()[0] is True
    with connection() as pconn:
        assert pconn.execute("SELECT pg_is_in_recovery()").fetchone()[0] is False

    # The ingestion write path lands on the primary immediately (no replication
    # needed to observe it there).
    persist_telemetry(TelemetryEvent(vehicle_id="rv5", status="charging", battery_pct=20))
    with connection() as pconn:
        on_primary = pconn.execute(
            "SELECT status FROM vehicle_current_state WHERE vehicle_id = 'rv5'"
        ).fetchone()
    assert on_primary == ("charging",)

    # The frontend connect snapshot and REST reads — served from the replica —
    # reflect the committed write once replication catches up.
    wait_replica_caught_up()
    snapshot = asyncio.run(_build_snapshot())
    assert snapshot["type"] == SNAPSHOT
    assert snapshot["payload"]["fleet"]["charging"] == 1
    assert get_fleet_state()["charging"] == 1
