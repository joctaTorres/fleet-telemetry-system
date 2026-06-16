"""Persistence operations for telemetry events and fleet-state aggregation.

Concurrency correctness lives in the database, per the telemetry-architecture
standard: the per-vehicle current state is maintained with a single server-side
``INSERT ... ON CONFLICT (vehicle_id) DO UPDATE`` (no application read-then-write,
no lost-update window), and the fleet aggregate is a ``GROUP BY status`` over that
table rather than a materialized counter that concurrent writers would race on.
"""

from __future__ import annotations

from .db import connection
from .models import STATUSES, TelemetryEvent

_INSERT_RAW = """
INSERT INTO raw_events (vehicle_id, status, battery_pct, recorded_at)
VALUES (%(vehicle_id)s, %(status)s, %(battery_pct)s, %(recorded_at)s)
"""

# Upsert the authoritative per-vehicle row. Last committed event wins.
_UPSERT_CURRENT = """
INSERT INTO vehicle_current_state (vehicle_id, status, battery_pct, recorded_at)
VALUES (%(vehicle_id)s, %(status)s, %(battery_pct)s, %(recorded_at)s)
ON CONFLICT (vehicle_id) DO UPDATE
SET status      = EXCLUDED.status,
    battery_pct = EXCLUDED.battery_pct,
    recorded_at = EXCLUDED.recorded_at,
    updated_at  = now()
"""

# Advance one zone's counter with a single server-side, row-locked
# read-modify-write. No application-level SELECT-then-UPDATE, which would lose
# updates under a burst of concurrent entries to the same zone.
_INCREMENT_ZONE = """
UPDATE zone_counts
SET entry_count = entry_count + 1
WHERE zone_id = %(zone_id)s
"""

_AGGREGATE = """
SELECT status, COUNT(*) AS n
FROM vehicle_current_state
GROUP BY status
"""

_ZONE_COUNTS = """
SELECT zone_id, entry_count
FROM zone_counts
"""


def persist_telemetry(event: TelemetryEvent) -> None:
    """Append the raw event and upsert the vehicle's current state atomically.

    All writes happen in one transaction, so a committed event is reflected in
    ``raw_events``, ``vehicle_current_state``, and — when ``zone_entered`` is
    non-null — the ``zone_counts`` increment together, or, on any failure, none
    of them. When ``zone_entered`` is null no counter statement runs.
    """
    params = {
        "vehicle_id": event.vehicle_id,
        "status": event.status,
        "battery_pct": event.battery_pct,
        "recorded_at": event.recorded_at,
    }
    with connection() as conn:
        with conn.transaction():
            conn.execute(_INSERT_RAW, params)
            conn.execute(_UPSERT_CURRENT, params)
            if event.zone_entered is not None:
                conn.execute(_INCREMENT_ZONE, {"zone_id": event.zone_entered})


def aggregate_fleet_state() -> dict[str, int]:
    """Return per-status vehicle counts across all distinct vehicles.

    Computed by a single ``GROUP BY status`` over ``vehicle_current_state`` in
    one MVCC snapshot, so the counts always sum to the number of distinct
    vehicles. Statuses with no vehicles are reported as zero.
    """
    counts: dict[str, int] = {status: 0 for status in STATUSES}
    with connection() as conn:
        for status, n in conn.execute(_AGGREGATE).fetchall():
            counts[status] = n
    return counts


def zone_entry_counts() -> dict[str, int]:
    """Return the live per-zone entry totals for all seeded zones.

    A single ``SELECT zone_id, entry_count FROM zone_counts`` read in one MVCC
    snapshot. Because the seed guarantees a row per known zone, this always
    reports all ~20 zones — never-entered zones report ``0``. The follow-on
    ``GET /zones/counts`` change consumes this read seam unchanged.
    """
    with connection() as conn:
        return {
            zone_id: entry_count
            for zone_id, entry_count in conn.execute(_ZONE_COUNTS).fetchall()
        }
