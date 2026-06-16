"""The stateless ingestion API.

A dedicated FastAPI application — kept separate from the future frontend API per
the telemetry-architecture standard — exposing the single write route a vehicle
needs: ``POST /telemetry``. The request path is exactly validate → write to
Postgres → return. The endpoint holds no authoritative in-process aggregate and
publishes to no broker; the dashboard's stream comes from CDC in a later phase.
"""

from __future__ import annotations

from fastapi import FastAPI, status

from .models import TelemetryEvent
from .persistence import persist_telemetry

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
