"""Persistence operations for telemetry events and fleet-state aggregation.

Concurrency correctness lives in the database, per the telemetry-architecture
standard: the per-vehicle current state is maintained with a single server-side
``INSERT ... ON CONFLICT (vehicle_id) DO UPDATE`` (no application read-then-write,
no lost-update window), and the fleet aggregate is a ``GROUP BY status`` over that
table rather than a materialized counter that concurrent writers would race on.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timedelta

import psycopg

from .db import connection
from .models import (
    COMMS_LOSS_TIMEOUT_SECONDS,
    LOW_BATTERY_PCT,
    OVERSPEED_MPS,
    STATUSES,
    STUCK_MIN_SECONDS,
    STUCK_SPEED_MPS,
    TELEPORT_MPS,
    TelemetryEvent,
)

_INSERT_RAW = """
INSERT INTO raw_events (vehicle_id, status, battery_pct, recorded_at)
VALUES (%(vehicle_id)s, %(status)s, %(battery_pct)s, %(recorded_at)s)
"""

# Read the vehicle's previous persisted reading. Run before the upsert so the
# stateful rules compare the incoming event against the prior state, not itself.
_SELECT_PRIOR = """
SELECT status, battery_pct, speed_mps, pos_x, pos_y, recorded_at
FROM vehicle_current_state
WHERE vehicle_id = %(vehicle_id)s
"""

# Upsert the authoritative per-vehicle row. Last committed event wins. Now also
# carries speed/position so the next event's stateful rules have them.
_UPSERT_CURRENT = """
INSERT INTO vehicle_current_state
    (vehicle_id, status, battery_pct, speed_mps, pos_x, pos_y, recorded_at)
VALUES
    (%(vehicle_id)s, %(status)s, %(battery_pct)s, %(speed_mps)s,
     %(pos_x)s, %(pos_y)s, %(recorded_at)s)
ON CONFLICT (vehicle_id) DO UPDATE
SET status      = EXCLUDED.status,
    battery_pct = EXCLUDED.battery_pct,
    speed_mps   = EXCLUDED.speed_mps,
    pos_x       = EXCLUDED.pos_x,
    pos_y       = EXCLUDED.pos_y,
    recorded_at = EXCLUDED.recorded_at,
    updated_at  = now()
"""

_INSERT_ANOMALY = """
INSERT INTO anomalies (vehicle_id, anomaly_type, detail, detected_at)
VALUES (%(vehicle_id)s, %(anomaly_type)s, %(detail)s, %(detected_at)s)
"""

# By-absence comms-loss sweep. One statement: flag every vehicle whose last
# reading is strictly older than the cutoff (now - timeout) and that does not
# already carry a comms_loss anomaly at or after that reading. The NOT EXISTS
# correlation makes the sweep idempotent — a silent vehicle is flagged exactly
# once per episode and re-arms only once it reports a newer reading (its
# recorded_at advances past the prior anomaly's detected_at).
_INSERT_COMMS_LOSS = """
INSERT INTO anomalies (vehicle_id, anomaly_type, detail, detected_at)
SELECT v.vehicle_id, 'comms_loss', %(detail)s, %(now)s
FROM vehicle_current_state v
WHERE v.recorded_at < %(cutoff)s
  AND NOT EXISTS (
      SELECT 1
      FROM anomalies a
      WHERE a.vehicle_id = v.vehicle_id
        AND a.anomaly_type = 'comms_loss'
        AND a.detected_at >= v.recorded_at
  )
"""

# Indexed range scan over (vehicle_id, detected_at); bounds inclusive on both
# ends. Backs the follow-on GET /anomalies endpoint unchanged.
_RECENT_ANOMALIES = """
SELECT vehicle_id, anomaly_type, detail, detected_at
FROM anomalies
WHERE vehicle_id = %(vehicle_id)s
  AND detected_at >= %(since)s
  AND detected_at <= %(until)s
ORDER BY detected_at
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

# Pessimistic row lock on the authoritative vehicle row. Taken first inside the
# fault transaction so all fault handling for one vehicle serializes on it — the
# mission cancel, the maintenance insert, and the status flip commit together.
_LOCK_VEHICLE = """
SELECT status
FROM vehicles
WHERE vehicle_id = %(vehicle_id)s
FOR UPDATE
"""

# Cancel the (at most one) active mission for the vehicle, capturing its id.
_CANCEL_ACTIVE_MISSION = """
UPDATE missions
SET status = 'cancelled'
WHERE vehicle_id = %(vehicle_id)s AND status = 'active'
RETURNING mission_id
"""

# Open a maintenance record. ON CONFLICT DO NOTHING against the partial unique
# index uq_open_maintenance_per_vehicle is the declarative idempotency backstop.
_INSERT_MAINTENANCE = """
INSERT INTO maintenance_records (vehicle_id, mission_id, reason)
VALUES (%(vehicle_id)s, %(mission_id)s, %(reason)s)
ON CONFLICT DO NOTHING
"""

_SET_VEHICLE_FAULT = """
UPDATE vehicles
SET status = 'fault', updated_at = now()
WHERE vehicle_id = %(vehicle_id)s
"""

# Guarded non-fault status update. A single UPDATE keyed on the (existing)
# vehicle row; rowcount tells the caller whether a row matched, so a missing
# vehicle is a clean "no row updated" rather than a silent success.
_SET_VEHICLE_STATUS = """
UPDATE vehicles
SET status = %(status)s, updated_at = now()
WHERE vehicle_id = %(vehicle_id)s
"""

_ZONE_COUNTS = """
SELECT zone_id, entry_count
FROM zone_counts
"""


def detect_anomalies(
    event: TelemetryEvent, prior: tuple | None
) -> list[tuple[str, str | None]]:
    """Return the ``(anomaly_type, detail)`` pairs the event triggers.

    Stateless rules are evaluated on the event alone; stateful rules compare it
    against ``prior`` — the vehicle's previous persisted reading as
    ``(status, battery_pct, speed_mps, pos_x, pos_y, recorded_at)`` — and fire
    none when ``prior`` is ``None`` (a first-ever reading). Comparisons are
    strict, so threshold-exact values do not fire. Rules are independent: one
    event can trigger several.
    """
    anomalies: list[tuple[str, str | None]] = []

    # ── Stateless: on the event itself ──────────────────────────────────────
    if event.status == "fault":
        anomalies.append(("fault_status", None))
    if event.error_codes:
        anomalies.append(("error_codes", ",".join(event.error_codes)))
    if event.battery_pct < LOW_BATTERY_PCT and event.status != "charging":
        anomalies.append(("low_battery", f"battery_pct={event.battery_pct}"))
    if event.speed_mps > OVERSPEED_MPS:
        anomalies.append(("overspeed", f"speed_mps={event.speed_mps}"))

    # ── Stateful: vs the previous persisted reading ─────────────────────────
    if prior is None:
        return anomalies

    p_status, p_battery, p_speed, p_x, p_y, p_recorded_at = prior
    dt = (event.recorded_at - p_recorded_at).total_seconds()

    # stuck: still "moving" but effectively stationary for ≥ the dwell window.
    if (
        p_status == "moving"
        and event.status == "moving"
        and p_speed < STUCK_SPEED_MPS
        and event.speed_mps < STUCK_SPEED_MPS
        and dt >= STUCK_MIN_SECONDS
    ):
        anomalies.append(("stuck", f"speed_mps={event.speed_mps}, dt={dt}s"))

    # teleport: implied speed (euclidean distance over the interval) too high.
    if (
        dt > 0
        and None not in (p_x, p_y, event.pos_x, event.pos_y)
    ):
        distance = math.hypot(event.pos_x - p_x, event.pos_y - p_y)
        implied = distance / dt
        if implied > TELEPORT_MPS:
            anomalies.append(("teleport", f"implied_mps={implied:.3f}"))

    # battery_rising: charge level climbing while the vehicle is not charging.
    if event.battery_pct > p_battery and event.status != "charging":
        anomalies.append(
            ("battery_rising", f"{p_battery} -> {event.battery_pct}")
        )

    return anomalies


def persist_telemetry(event: TelemetryEvent) -> None:
    """Append the raw event, upsert current state, and write anomalies atomically.

    All writes happen in one transaction, so a committed event is reflected in
    ``raw_events``, ``vehicle_current_state``, the ``zone_counts`` increment (when
    ``zone_entered`` is non-null), and any ``anomalies`` rows together, or, on any
    failure, none of them. The vehicle's prior reading is read *before* the upsert
    so the stateful rules compare against the previous state; when no threshold is
    crossed, no anomaly row is written.
    """
    params = {
        "vehicle_id": event.vehicle_id,
        "status": event.status,
        "battery_pct": event.battery_pct,
        "speed_mps": event.speed_mps,
        "pos_x": event.pos_x,
        "pos_y": event.pos_y,
        "recorded_at": event.recorded_at,
    }
    with connection() as conn:
        with conn.transaction():
            prior = conn.execute(
                _SELECT_PRIOR, {"vehicle_id": event.vehicle_id}
            ).fetchone()
            anomalies = detect_anomalies(event, prior)
            conn.execute(_INSERT_RAW, params)
            conn.execute(_UPSERT_CURRENT, params)
            if event.zone_entered is not None:
                conn.execute(_INCREMENT_ZONE, {"zone_id": event.zone_entered})
            for anomaly_type, detail in anomalies:
                conn.execute(
                    _INSERT_ANOMALY,
                    {
                        "vehicle_id": event.vehicle_id,
                        "anomaly_type": anomaly_type,
                        "detail": detail,
                        "detected_at": event.recorded_at,
                    },
                )


#: A connection factory: a no-arg callable returning a connection context
#: manager. Defaults to the primary ``connection``; the frontend's connect
#: snapshot passes ``replica_connection`` so reads come from the standby.
ConnFactory = Callable[[], AbstractContextManager[psycopg.Connection]]


def aggregate_fleet_state(conn_factory: ConnFactory = connection) -> dict[str, int]:
    """Return per-status vehicle counts across all distinct vehicles.

    Computed by a single ``GROUP BY status`` over ``vehicle_current_state`` in
    one MVCC snapshot, so the counts always sum to the number of distinct
    vehicles. Statuses with no vehicles are reported as zero. ``conn_factory``
    selects the source: the primary by default, the read replica when the
    frontend builds its connect snapshot.
    """
    counts: dict[str, int] = {status: 0 for status in STATUSES}
    with conn_factory() as conn:
        for status, n in conn.execute(_AGGREGATE).fetchall():
            counts[status] = n
    return counts


def zone_entry_counts(conn_factory: ConnFactory = connection) -> dict[str, int]:
    """Return the live per-zone entry totals for all seeded zones.

    A single ``SELECT zone_id, entry_count FROM zone_counts`` read in one MVCC
    snapshot. Because the seed guarantees a row per known zone, this always
    reports all ~20 zones — never-entered zones report ``0``. The follow-on
    ``GET /zones/counts`` change consumes this read seam unchanged.
    ``conn_factory`` selects primary (default) vs replica, as above.
    """
    with conn_factory() as conn:
        return {
            zone_id: entry_count
            for zone_id, entry_count in conn.execute(_ZONE_COUNTS).fetchall()
        }


def recent_anomalies(
    vehicle_id: str,
    since: datetime,
    until: datetime,
    conn_factory: ConnFactory = connection,
) -> list[dict]:
    """Return one vehicle's anomalies within an inclusive ``[since, until]`` range.

    A single indexed range scan over the ``(vehicle_id, detected_at)`` composite
    index: filters by ``vehicle_id`` and ``detected_at`` between the bounds
    (inclusive on both ends), ordered by ``detected_at``. The
    ``GET /anomalies`` endpoint consumes this read seam unchanged.
    ``conn_factory`` selects primary (default) vs replica, as with the other
    frontend read seams.
    """
    params = {"vehicle_id": vehicle_id, "since": since, "until": until}
    with conn_factory() as conn:
        return [
            {
                "vehicle_id": row[0],
                "anomaly_type": row[1],
                "detail": row[2],
                "detected_at": row[3],
            }
            for row in conn.execute(_RECENT_ANOMALIES, params).fetchall()
        ]


def transition_to_fault(vehicle_id: str, reason: str | None = None) -> bool:
    """Atomically transition a vehicle to ``fault``, cancelling its active mission.

    Runs as one ``conn.transaction()`` that first takes a pessimistic row lock on
    the authoritative ``vehicles`` row (``SELECT status ... FOR UPDATE``), per the
    telemetry-architecture standard and ADR D6. Locking serializes *all* fault
    handling for that vehicle, so concurrent fault events cannot interleave.

    Idempotent via two layers, to tolerate at-least-once delivery and concurrency:

    - *Transition guard (primary):* under the lock, if the vehicle is already in
      ``fault`` the handler short-circuits and returns ``False`` — no mission is
      cancelled and no maintenance record is written. The second of two
      concurrent/duplicate transitions always observes ``fault`` and no-ops.
    - *Uniqueness backstop (declarative):* the maintenance insert uses
      ``ON CONFLICT DO NOTHING`` against the partial unique index
      ``uq_open_maintenance_per_vehicle``, so at most one open record per vehicle
      exists even if a write reached the insert twice.

    On a real transition it cancels the active mission (if any), opens one
    maintenance record, flips ``status`` to ``fault``, and returns ``True``.
    Raises ``LookupError`` if the vehicle row does not exist (existence is a
    precondition; auto-registration is out of scope).
    """
    with connection() as conn:
        with conn.transaction():
            row = conn.execute(_LOCK_VEHICLE, {"vehicle_id": vehicle_id}).fetchone()
            if row is None:
                raise LookupError(f"unknown vehicle: {vehicle_id}")
            if row[0] == "fault":
                return False

            cancelled = conn.execute(
                _CANCEL_ACTIVE_MISSION, {"vehicle_id": vehicle_id}
            ).fetchone()
            mission_id = cancelled[0] if cancelled is not None else None

            conn.execute(
                _INSERT_MAINTENANCE,
                {
                    "vehicle_id": vehicle_id,
                    "mission_id": mission_id,
                    "reason": reason,
                },
            )
            conn.execute(_SET_VEHICLE_FAULT, {"vehicle_id": vehicle_id})
            return True


def set_vehicle_status(vehicle_id: str, status: str) -> bool:
    """Set a vehicle's status with a single guarded UPDATE (non-fault path).

    The fault transition has its own row-locked, idempotent handler
    (``transition_to_fault``); this is the thin path for the non-fault statuses
    (``idle`` / ``moving`` / ``charging``), where a vehicle is simply a row whose
    status flips. Returns ``True`` when a row matched and was updated, ``False``
    when the vehicle does not exist — letting the route map a missing vehicle to
    ``404`` rather than reporting a silent success.
    """
    with connection() as conn:
        with conn.transaction():
            result = conn.execute(
                _SET_VEHICLE_STATUS,
                {"vehicle_id": vehicle_id, "status": status},
            )
            return result.rowcount > 0


def detect_comms_loss(now: datetime) -> int:
    """Flag vehicles that have gone silent past the comms-loss timeout.

    A *by-absence* rule: unlike the event-triggered anomalies, there is no
    incoming reading to ride on, so this runs as a standalone sweep (driven on an
    interval by ``app/watchdog.py``) rather than inside the ingest transaction.

    In one transaction it inserts a ``comms_loss`` anomaly (``detected_at = now``)
    for every vehicle in ``vehicle_current_state`` whose last ``recorded_at`` is
    **strictly older** than ``now - COMMS_LOSS_TIMEOUT_SECONDS`` and that does not
    already carry a ``comms_loss`` anomaly at or after that reading. The
    ``NOT EXISTS`` guard makes the sweep idempotent — a continuing silence is
    flagged once, and a vehicle re-arms only after it reports a newer reading.
    Returns the number of vehicles flagged on this pass.
    """
    cutoff = now - timedelta(seconds=COMMS_LOSS_TIMEOUT_SECONDS)
    params = {
        "now": now,
        "cutoff": cutoff,
        "detail": f"no event for over {COMMS_LOSS_TIMEOUT_SECONDS}s",
    }
    with connection() as conn:
        with conn.transaction():
            return conn.execute(_INSERT_COMMS_LOSS, params).rowcount
