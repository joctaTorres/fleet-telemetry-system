"""The frontend (dashboard) API.

A dedicated FastAPI application — kept separate from the stateless ingestion API
per the telemetry-architecture standard ("two separate APIs, do not merge them")
— exposing the read surface the dashboard needs: ``GET /fleet/state`` and
``GET /zones/counts``. Each route derives its result fresh from the database on
every request; the app holds no authoritative in-process counter that could
diverge from committed state.

Scoped deviation: the standard serves REST reads from a streaming read replica.
This phase is explicitly scoped to a single Postgres (no replica/CDC/Redis yet),
so the read is served from the primary here. Concurrency correctness stays in the
database — the aggregate is a single ``GROUP BY`` snapshot — so the propagation
mechanism is unchanged; a later read/write-split phase moves this read to the
replica.
"""

from __future__ import annotations

from fastapi import FastAPI

from .persistence import aggregate_fleet_state, zone_entry_counts

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
