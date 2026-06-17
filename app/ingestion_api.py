"""The stateless ingestion API.

A dedicated FastAPI application — kept separate from the future frontend API per
the telemetry-architecture standard — exposing the single write route a vehicle
needs: ``POST /telemetry``. The request path is exactly validate → write to
Postgres → return. The endpoint holds no authoritative in-process aggregate and
publishes to no broker; the dashboard's stream comes from CDC in a later phase.
"""

from __future__ import annotations

import time

from fastapi import FastAPI, HTTPException, Request, status
from opentelemetry import metrics, trace

from .models import TelemetryEvent, VehicleStatusUpdate
from .otel import configure_otel, instrument_fastapi_app, route_template
from .persistence import persist_telemetry, set_vehicle_status, transition_to_fault

# Service identity for every span and metric this process emits. Kept as a
# module constant so the wiring below and the tests bind to the same value.
SERVICE_NAME = "ingestion-api"

app = FastAPI(title="Fleet Telemetry Ingestion API")


def install_observability(app: FastAPI) -> metrics.Meter:
    """Wire OTel into ``app`` through the shared :mod:`app.otel` bootstrap.

    Installs the global trace/metric providers (a no-op when
    ``OTEL_EXPORTER_OTLP_ENDPOINT`` is unset) and instruments the FastAPI app so
    incoming requests produce ``service.name=ingestion-api`` server spans. No SDK
    or exporter wiring is duplicated here — all of it lives in
    :func:`app.otel.configure_otel`. Returns the meter the request metrics below
    are recorded on.
    """
    configure_otel(SERVICE_NAME)
    instrument_fastapi_app(app)
    return metrics.get_meter(__name__)


_meter = install_observability(app)
# Module-level tracer so the persist seam below can emit a CLIENT db span and
# tests can swap it for an in-memory tracer.
_tracer = trace.get_tracer(__name__)

# NOTE: FastAPIInstrumentor also auto-emits its own http_server_* series (e.g.
# http_server_duration / http_server_active_requests); those are deliberately
# left unused — the dashboards bind to the stable explicit names below instead.
#
# Explicit, stable request metrics for the rate / latency / error panels. Names
# and attributes are fixed here so the downstream "Ingestion API" dashboard binds
# to them deterministically rather than to auto-emitted metric names. Recorded
# from the HTTP middleware below, they cover every response — including the 422
# rejections raised by validation before a route handler runs — so the error
# series is populated. Module-level so tests can swap in an in-memory meter.
REQUEST_COUNTER = _meter.create_counter(
    "ingestion.requests",
    unit="1",
    description="Count of ingestion API HTTP requests by method, route, and status.",
)
REQUEST_DURATION = _meter.create_histogram(
    "ingestion.request.duration",
    unit="ms",
    description="Duration of ingestion API HTTP requests in milliseconds.",
)


@app.middleware("http")
async def record_request_metrics(request: Request, call_next):
    """Record the request count + duration for every response, including 422s.

    The middleware wraps routing, so it observes both the 201 success path and
    the 422 validation rejection (which FastAPI returns before a handler runs),
    tagging each with the HTTP method, route template, and response status.
    """
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000.0
    attributes = {
        "http.method": request.method,
        "http.route": route_template(app, request),
        "http.status_code": response.status_code,
    }
    REQUEST_COUNTER.add(1, attributes)
    REQUEST_DURATION.record(duration_ms, attributes)
    return response


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
    # Wrap the synchronous Postgres write in a CLIENT span with a peer db
    # identity (server.address=db) so Tempo's service-graphs processor forms a
    # virtual db node and draws the ingestion-api -> db edge. The span nests
    # under the FastAPI server span, so the request trace structure is unchanged.
    with _tracer.start_as_current_span(
        "db.write vehicle_current_state", kind=trace.SpanKind.CLIENT
    ) as span:
        span.set_attribute("db.system", "postgresql")
        span.set_attribute("server.address", "db")
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
