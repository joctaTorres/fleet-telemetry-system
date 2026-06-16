"""The stateless ingestion API.

A dedicated FastAPI application — kept separate from the future frontend API per
the telemetry-architecture standard — exposing the single write route a vehicle
needs: ``POST /telemetry``. The request path is exactly validate → write to
Postgres → return. The endpoint holds no authoritative in-process aggregate and
publishes to no broker; the dashboard's stream comes from CDC in a later phase.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, status

from .models import TelemetryEvent, VehicleStatusUpdate
from .persistence import persist_telemetry, set_vehicle_status, transition_to_fault

app = FastAPI(title="Fleet Telemetry Ingestion API")


@app.post("/telemetry", status_code=status.HTTP_201_CREATED)
def post_telemetry(event: TelemetryEvent) -> dict[str, str]:
    """Validate a telemetry reading and persist it.

    FastAPI/Pydantic validates the body into ``TelemetryEvent`` first: a
    schema-invalid body (bad status, out-of-range battery, missing or unknown
    field) is rejected with 422 before this handler runs, so nothing is
    persisted. A valid event is committed synchronously via ``persist_telemetry``
    — appended to ``raw_events`` and upserted into ``vehicle_current_state`` in
    one transaction — and the route returns 201 Created.
    """
    persist_telemetry(event)
    return {"status": "accepted"}


@app.post("/vehicles/{vehicle_id}/status")
def post_vehicle_status(
    vehicle_id: str, update: VehicleStatusUpdate
) -> dict[str, object]:
    """Set a vehicle's authoritative status.

    FastAPI/Pydantic validates the body into ``VehicleStatusUpdate`` first, so a
    schema-invalid request (unknown status, unknown field) is rejected with 422
    before this handler runs and nothing is written.

    A transition to ``fault`` delegates to the proven ``transition_to_fault``
    seam — one row-locked, idempotent transaction that cancels the active mission
    and opens exactly one maintenance record — adding no transaction logic of its
    own; all concurrency correctness stays in that handler. Every other status is
    a thin guarded update via ``set_vehicle_status``.

    Returns 200 with ``applied`` reporting whether the call changed state
    (``True``) or was an idempotent no-op (``False``); an unknown vehicle is a
    clean ``404`` (no row written), not a 500.
    """
    try:
        if update.status == "fault":
            applied = transition_to_fault(vehicle_id, update.reason)
        else:
            applied = set_vehicle_status(vehicle_id, update.status)
            if not applied:
                raise LookupError(vehicle_id)
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown vehicle: {vehicle_id}",
        )
    return {"vehicle_id": vehicle_id, "status": update.status, "applied": applied}
