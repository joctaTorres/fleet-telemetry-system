"""The frontend (dashboard) API.

A dedicated FastAPI application — kept separate from the stateless ingestion API
per the telemetry-architecture standard ("two separate APIs, do not merge them")
— exposing the read surface the dashboard needs: ``GET /fleet/state``,
``GET /zones/counts`` and ``GET /anomalies``. Each route derives its result fresh
from the database on every request; the app holds no authoritative in-process
counter that could diverge from committed state.

Scoped deviation: the standard serves REST reads from a streaming read replica.
This phase is explicitly scoped to a single Postgres (no replica/CDC/Redis yet),
so the read is served from the primary here. Concurrency correctness stays in the
database — the aggregate is a single ``GROUP BY`` snapshot — so the propagation
mechanism is unchanged; a later read/write-split phase moves this read to the
replica.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import FastAPI, Query

from .persistence import aggregate_fleet_state, recent_anomalies, zone_entry_counts

app = FastAPI(title="Fleet Telemetry Frontend API")


@app.get("/fleet/state")
def get_fleet_state() -> dict[str, int]:
    """Return the current aggregate fleet state as per-status vehicle counts.

    Calls the existing ``aggregate_fleet_state()`` — a single ``GROUP BY status``
    over ``vehicle_current_state`` in one MVCC snapshot — and returns 200 OK with
    a JSON object keyed by status: ``{"idle": n, "moving": n, "charging": n,
    "fault": n}``. All four status keys are always present; statuses with no
    vehicles report ``0``.
    """
    return aggregate_fleet_state()


@app.get("/zones/counts")
def get_zone_counts() -> dict[str, int]:
    """Return the live per-zone entry totals.

    Calls the existing ``zone_entry_counts()`` — a single
    ``SELECT zone_id, entry_count FROM zone_counts`` in one MVCC snapshot — and
    returns 200 OK with a JSON object keyed by zone id:
    ``{"zone-01": n, ..., "zone-20": n}``. Because the seed guarantees a row per
    known zone, all ~20 zones are always present; never-entered zones report
    ``0``.
    """
    return zone_entry_counts()


@app.get("/anomalies")
def get_anomalies(
    vehicle_id: str = Query(..., min_length=1),
    since: datetime = Query(...),
    until: datetime = Query(...),
) -> list[dict]:
    """Return one vehicle's anomalies within an inclusive ``[since, until]`` range.

    Thin adapter over the existing ``recent_anomalies(vehicle_id, since, until)``
    read seam — a single indexed range scan over the ``(vehicle_id, detected_at)``
    composite index, ordered by ``detected_at``. ``vehicle_id`` is required;
    ``since`` and ``until`` are ISO-8601 timestamps and the bounds are inclusive
    on both ends. Returns 200 OK with a JSON list of anomaly objects
    (``vehicle_id``, ``anomaly_type``, ``detail``, ``detected_at``); a vehicle
    with no matching anomalies returns an empty list. Like the other frontend
    reads, the result is derived fresh from the database, so the app holds no
    authoritative in-process state.
    """
    return recent_anomalies(vehicle_id, since, until)
