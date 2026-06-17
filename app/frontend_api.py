"""The frontend (dashboard) API.

A dedicated FastAPI application — kept separate from the stateless ingestion API
per the telemetry-architecture standard ("two separate APIs, do not merge them")
— exposing the read surface the dashboard needs: ``GET /fleet/state``,
``GET /vehicles``, ``GET /vehicles/anomalies/latest``, ``GET /zones/counts`` and
``GET /anomalies``. Each route derives its result fresh
from the database on every request; the app holds no authoritative in-process
counter that could diverge from committed state.

Every read — the one-shot snapshot sent on WebSocket connect and the three REST
endpoints — is served from the streaming read replica (``replica_connection``),
so connect-time and query read load is isolated from the primary's write path per
the telemetry-architecture standard (ADR-0001, D1/D5). This closes the documented
deviation from ``websocket-fanout``, where the snapshot was read from the primary
until the replica existed. The live delta path is independent of the replica:
patches arrive via the CDC -> Redis -> WebSocket stream, so a small replication
lag never affects them.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import datetime

import redis.asyncio as redis
from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry import metrics, trace
from opentelemetry.context import Context
from opentelemetry.metrics import CallbackOptions, Observation
from starlette.concurrency import run_in_threadpool

from .config import get_redis_url
from .db import replica_connection
from .events import (
    EVENT_CHANNEL,
    SNAPSHOT,
    TRACE_CONTEXT_KEY,
    extract_trace_context,
    strip_trace_context,
)
from .otel import configure_otel, instrument_fastapi_app
from .persistence import (
    aggregate_fleet_state,
    current_vehicle_states,
    latest_anomaly_per_vehicle,
    recent_anomalies,
    zone_entry_counts,
)

# Service identity for every span and metric this process emits. Kept as a
# module constant so the wiring below and the tests bind to the same value.
SERVICE_NAME = "frontend-api"


class ConnectionRegistry:
    """Async-safe set of live WebSocket connections.

    Holds *connections*, not authoritative fleet state — any frontend instance
    can serve any client, and the snapshot is always derived fresh from the
    database. A single ``asyncio.Lock`` guards membership so concurrent
    connect/disconnect and fan-out cannot race. ``broadcast`` sends a message to
    every registered connection and drops any connection that errors on send, so
    a dead client never blocks fan-out to the rest.
    """

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def add(self, ws: WebSocket) -> None:
        async with self._lock:
            self._connections.add(ws)

    async def remove(self, ws: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(ws)

    def size(self) -> int:
        """Return the current number of live connections.

        A lock-free read of the membership set's length — ``len`` on a ``set`` is
        atomic under the GIL, so the active-connections gauge callback can observe
        it on each metric collection cycle without contending for the
        connect/disconnect lock or risking a stale parallel counter. It is the
        single source of truth the gauge reports on.
        """
        return len(self._connections)

    async def broadcast(self, message: str) -> None:
        """Send ``message`` to every connection; drop those that error on send."""
        async with self._lock:
            targets = list(self._connections)
        dead: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_text(message)
            except Exception:  # noqa: BLE001 - a failed send means a dead client
                dead.append(ws)
        if dead:
            async with self._lock:
                self._connections.difference_update(dead)
        # Best-effort fan-out metric: count this broadcast event once. Recorded
        # after the send loop so it can never block or fail delivery to live
        # clients, and ``suppress`` keeps a misconfigured meter from breaking
        # fan-out. The dead-client drop above is left exactly as it was.
        with suppress(Exception):
            BROADCAST_COUNTER.add(1)


registry = ConnectionRegistry()


async def _redis_subscriber(client: redis.Redis) -> None:
    """Subscribe to the event channel and fan every message out to all clients.

    One subscription per frontend process: the pub/sub fan-in is shared and the
    WebSocket fan-out is per-connection. Each published message is forwarded
    verbatim — the frontend synthesizes nothing, it only emits what it observes
    on the channel. Cancelled cleanly on shutdown, unsubscribing on the way out.
    """
    pubsub = client.pubsub()
    await pubsub.subscribe(EVENT_CHANNEL)
    try:
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            data = message["data"]
            if isinstance(data, bytes):
                data = data.decode()
            await _fan_out(data)
    finally:
        with suppress(Exception):
            await pubsub.unsubscribe(EVENT_CHANNEL)
        with suppress(Exception):
            await pubsub.aclose()


async def _fan_out(data: str) -> None:
    """Re-parent on the published trace, strip the carrier, and broadcast.

    Each Redis message carries the cdc-consumer's W3C trace context in the
    envelope's reserved out-of-band carrier. We extract it and open the subscribe
    span parented on that remote context, so the frontend's spans join the
    cdc-consumer trace across the pub/sub hop; the WebSocket broadcast is then a
    child span of it (``service.name=frontend-api``). The carrier is stripped
    before forwarding so connected clients still receive the verbatim
    ``{type, payload}`` shape.

    Safe-by-default and non-perturbing: an envelope with no carrier (or one that
    is not JSON) extracts to an empty context — a new root span — and is still
    forwarded; with no OTLP endpoint the spans are no-ops; and an extract/strip
    failure can never break the fan-out (delivery falls back to the raw message).
    """
    forward = data
    parent: Context | None = None
    try:
        envelope = json.loads(data)
    except Exception:  # noqa: BLE001 - non-JSON: forward verbatim, untraced
        envelope = None
    if isinstance(envelope, dict):
        parent = extract_trace_context(envelope)
        if TRACE_CONTEXT_KEY in envelope:
            # Re-serialize only when a carrier was actually present, so an
            # un-instrumented message is forwarded byte-for-byte as before.
            strip_trace_context(envelope)
            forward = json.dumps(envelope)
    # CONSUMER kind so Tempo's service-graphs processor pairs this span with the
    # cdc-consumer's PRODUCER publish span and renders the cdc-consumer ->
    # frontend-api pub/sub edge. The re-parenting on the carried context is
    # unchanged, so the phase-3 connected-trace structure is preserved.
    with _tracer.start_as_current_span(
        "redis.subscribe", context=parent, kind=trace.SpanKind.CONSUMER
    ) as span:
        span.set_attribute("messaging.system", "redis")
        span.set_attribute("messaging.source", EVENT_CHANNEL)
        with _tracer.start_as_current_span("ws.broadcast") as broadcast_span:
            broadcast_span.set_attribute("ws.active_connections", registry.size())
            await registry.broadcast(forward)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start the Redis subscriber on startup, cancel it cleanly on shutdown."""
    client = redis.from_url(get_redis_url())
    task = asyncio.create_task(_redis_subscriber(client))
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await client.aclose()


app = FastAPI(title="Fleet Telemetry Frontend API", lifespan=lifespan)


def install_observability(app: FastAPI) -> metrics.Meter:
    """Wire OTel into ``app`` through the shared :mod:`app.otel` bootstrap.

    Installs the global trace/metric providers (a no-op when
    ``OTEL_EXPORTER_OTLP_ENDPOINT`` is unset) and instruments the FastAPI app so
    incoming GET reads and the ``/ws`` upgrade produce ``service.name=frontend-api``
    server spans. No SDK or exporter wiring is duplicated here — all of it lives in
    :func:`app.otel.configure_otel`. Returns the meter the request metrics below
    are recorded on.
    """
    configure_otel(SERVICE_NAME)
    instrument_fastapi_app(app)
    return metrics.get_meter(__name__)


_meter = install_observability(app)
_tracer = trace.get_tracer(__name__)

# Explicit, stable request metrics for the rate / latency / error panels. Names
# and attributes are fixed here so the downstream "Frontend API & WebSockets"
# dashboard binds to them deterministically rather than to auto-emitted metric
# names. Recorded from the HTTP middleware below, they cover every response —
# including the 422 rejections raised by validation before a route handler runs
# — so the error series is populated. Following the ingestion convention, these
# translate to ``frontend_requests_total`` / ``frontend_request_duration_*`` in
# Prometheus. Module-level so tests can swap in an in-memory meter.
REQUEST_COUNTER = _meter.create_counter(
    "frontend.requests",
    unit="1",
    description="Count of frontend API HTTP requests by method, route, and status.",
)
REQUEST_DURATION = _meter.create_histogram(
    "frontend.request.duration",
    unit="ms",
    description="Duration of frontend API HTTP requests in milliseconds.",
)

# --- WebSocket lifecycle telemetry ---------------------------------------------
# The live half of the frontend surface: a gauge of how many WebSocket clients are
# connected right now and a counter of fan-out events. Following the same dotted
# convention as the request metrics above, these translate to
# ``frontend_ws_active_connections`` and ``frontend_ws_broadcasts_total`` in
# Prometheus, the names the downstream "Frontend API & WebSockets" dashboard binds
# to. Both hang off the global meter installed by the shared bootstrap, so with
# ``OTEL_EXPORTER_OTLP_ENDPOINT`` unset they are no-ops that export nothing and
# never raise.


def _make_active_connections_callback(source: ConnectionRegistry):
    """Build an observable-gauge callback that reports ``source``'s live size.

    Observing the registry size on each collection cycle — rather than mutating a
    parallel counter on connect/disconnect — means the gauge can never drift from
    the actual set of live connections across connect/disconnect/dead-client-drop
    races. Returned as a factory so a test can bind the same callback to an
    in-memory meter and an isolated registry.
    """

    def _observe(_options: CallbackOptions):
        yield Observation(source.size())

    return _observe


# Observable (callback) gauge — the active-connections headline signal. Created at
# module scope so tests can rebuild the same instrument against an in-memory meter.
# Deliberately carries no unit: a dimensionless ("1") unit makes the OTLP->Prometheus
# remote-write translation append a ``_ratio`` suffix, which would break the
# dashboard's bind to ``frontend_ws_active_connections``. A plain count gauge keeps
# the shipped Prometheus name exactly ``frontend_ws_active_connections``.
WS_ACTIVE_CONNECTIONS = _meter.create_observable_gauge(
    "frontend.ws.active_connections",
    callbacks=[_make_active_connections_callback(registry)],
    description="Number of live WebSocket connections currently registered.",
)

# Fan-out counter — incremented once per broadcast in ConnectionRegistry.broadcast.
BROADCAST_COUNTER = _meter.create_counter(
    "frontend.ws.broadcasts",
    unit="1",
    description="Count of WebSocket fan-out (broadcast) events to all clients.",
)


def _route_template(request: Request) -> str:
    """Return the matched route's path template (e.g. ``/vehicles``).

    Falls back to the raw request path when no route matched (a 404), keeping the
    metric label bounded to the handful of declared routes rather than raw URLs.
    """
    endpoint = request.scope.get("endpoint")
    if endpoint is not None:
        for route in app.router.routes:
            if getattr(route, "endpoint", None) is endpoint:
                return route.path
    return request.url.path


@app.middleware("http")
async def record_request_metrics(request: Request, call_next):
    """Record the request count + duration for every response, including 422s.

    The middleware wraps routing, so it observes both the 200 success path and
    the 422 validation rejection (which FastAPI returns before a handler runs, as
    on ``GET /anomalies`` with bad query params), tagging each with the HTTP
    method, route template, and response status. WebSocket upgrades bypass the
    HTTP middleware stack and are covered by ASGI auto-instrumentation instead.
    """
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000.0
    attributes = {
        "http.method": request.method,
        "http.route": _route_template(request),
        "http.status_code": response.status_code,
    }
    REQUEST_COUNTER.add(1, attributes)
    REQUEST_DURATION.record(duration_ms, attributes)
    return response


# The dashboard is served from a different origin (its own host port) than this
# API, so the browser's REST fetches are cross-origin. Allow them via CORS.
# Origins are env-configurable (comma-separated); default "*" suits the local
# runtime/demo. WebSocket upgrades are not subject to CORS.
#
# Allowed request headers are likewise env-configurable; the default "*" covers
# the W3C "traceparent" header the browser @opentelemetry/sdk-trace-web fetch
# instrumentation injects on the cross-origin snapshot calls. Because the browser
# now sends a non-safelisted header, those fetches become preflighted, so this
# must permit "traceparent" or the joined browser->frontend-api trace is lost.
_cors_origins = os.environ.get("CORS_ALLOW_ORIGINS", "*").split(",")
_cors_headers = os.environ.get("CORS_ALLOW_HEADERS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors_origins],
    allow_methods=["*"],
    allow_headers=[h.strip() for h in _cors_headers],
)


async def _build_snapshot() -> dict:
    """Build the one-shot connect snapshot fresh from the replica read seams.

    Derived on every connect from ``aggregate_fleet_state()`` and
    ``zone_entry_counts()`` read against the replica (run in a worker thread, as
    they are blocking DB reads), so it always reflects committed state, isolates
    connect-time reads from the primary, and the app retains no authoritative
    copy.
    """
    fleet = await run_in_threadpool(aggregate_fleet_state, replica_connection)
    zones = await run_in_threadpool(zone_entry_counts, replica_connection)
    return {"type": SNAPSHOT, "payload": {"fleet": fleet, "zones": zones}}


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    """Accept a dashboard connection, snapshot once, then stream deltas.

    On connect the client receives a single ``snapshot`` envelope built fresh
    from the database read seams; thereafter it receives individual state
    patches fanned out from the Redis channel by the lifespan subscriber. The
    connection is held open (reading to detect disconnect) until the client goes
    away, at which point it is unregistered.
    """
    with _tracer.start_as_current_span("ws /ws") as span:
        await websocket.accept()
        await registry.add(websocket)
        span.set_attribute("ws.active_connections", registry.size())
        try:
            with _tracer.start_as_current_span("ws.snapshot") as snapshot_span:
                snapshot = await _build_snapshot()
                snapshot_span.set_attribute("db.replica", True)
                snapshot_span.set_attribute(
                    "fleet.zones.count", len(snapshot["payload"]["zones"])
                )
                await websocket.send_text(json.dumps(snapshot))
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            await registry.remove(websocket)


@app.get("/fleet/state")
def get_fleet_state() -> dict[str, int]:
    """Return the current aggregate fleet state as per-status vehicle counts.

    Calls the existing ``aggregate_fleet_state()`` — a single ``GROUP BY status``
    over ``vehicle_current_state`` in one MVCC snapshot — and returns 200 OK with
    a JSON object keyed by status: ``{"idle": n, "moving": n, "charging": n,
    "fault": n}``. All four status keys are always present; statuses with no
    vehicles report ``0``. Read from the replica to isolate query load from the
    primary's write path.
    """
    return aggregate_fleet_state(replica_connection)


@app.get("/vehicles")
def get_vehicles() -> list[dict]:
    """Return every vehicle's current ``(vehicle_id, status, battery_pct)``.

    Calls the existing ``current_vehicle_states()`` — a single
    ``SELECT ... FROM vehicle_current_state`` in one MVCC snapshot ordered by
    ``vehicle_id`` — and returns 200 OK with a JSON list of per-vehicle rows.
    This is the REST snapshot the dashboard's live list renders from on load,
    before it switches to the granular ``vehicle_state_changed`` WS patch stream.
    Read from the replica to isolate this load-time read from the primary's write
    path, like the other frontend reads.

    The replica read is wrapped in a child span so the trace shows the read seam
    nested under the request's server span, rather than an opaque server span.
    """
    # CLIENT kind + a peer db identity (server.address) so Tempo's service-graphs
    # processor forms a virtual db node and draws the frontend-api -> replica edge.
    with _tracer.start_as_current_span(
        "replica.read vehicle_current_state", kind=trace.SpanKind.CLIENT
    ) as span:
        span.set_attribute("db.system", "postgresql")
        span.set_attribute("server.address", "replica")
        rows = current_vehicle_states(replica_connection)
        span.set_attribute("db.replica", True)
        span.set_attribute("fleet.vehicles.count", len(rows))
    return rows


@app.get("/vehicles/anomalies/latest")
def get_latest_anomalies() -> list[dict]:
    """Return each vehicle's most-recent anomaly: one row per vehicle.

    Thin adapter over the existing ``latest_anomaly_per_vehicle()`` read seam — a
    single ``SELECT DISTINCT ON (vehicle_id) ... ORDER BY vehicle_id, detected_at
    DESC`` in one MVCC snapshot. Returns 200 OK with a JSON list of anomaly
    objects (``vehicle_id``, ``anomaly_type``, ``detail``, ``detected_at``), one
    per vehicle that has ever had an anomaly; vehicles with none are absent. This
    is the connect-time anomaly snapshot each dashboard row renders from before it
    switches to the granular ``anomaly_detected`` WS patch stream. Like the other
    frontend reads, it is derived fresh from the replica, so the app holds no
    authoritative in-process state and query load stays off the primary.
    """
    return latest_anomaly_per_vehicle(replica_connection)


@app.get("/zones/counts")
def get_zone_counts() -> dict[str, int]:
    """Return the live per-zone entry totals.

    Calls the existing ``zone_entry_counts()`` — a single
    ``SELECT zone_id, entry_count FROM zone_counts`` in one MVCC snapshot — and
    returns 200 OK with a JSON object keyed by zone id:
    ``{"zone-01": n, ..., "zone-20": n}``. Because the seed guarantees a row per
    known zone, all ~20 zones are always present; never-entered zones report
    ``0``. Read from the replica to isolate query load from the primary.
    """
    return zone_entry_counts(replica_connection)


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
    reads, the result is derived fresh from the replica, so the app holds no
    authoritative in-process state and query load stays off the primary.
    """
    return recent_anomalies(vehicle_id, since, until, replica_connection)
