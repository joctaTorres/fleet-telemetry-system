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

_AGGREGATE = """
SELECT status, COUNT(*) AS n
FROM vehicle_current_state
GROUP BY status
"""


def persist_telemetry(event: TelemetryEvent) -> None:
    """Append the raw event and upsert the vehicle's current state atomically.

    Both writes happen in one transaction, so a committed event is reflected in
    both ``raw_events`` and ``vehicle_current_state`` — or, on any failure,
    neither.
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
