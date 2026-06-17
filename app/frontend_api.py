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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import datetime

import redis.asyncio as redis
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from .config import get_redis_url
from .db import replica_connection
from .events import EVENT_CHANNEL, SNAPSHOT
from .persistence import (
    aggregate_fleet_state,
    current_vehicle_states,
    latest_anomaly_per_vehicle,
    recent_anomalies,
    zone_entry_counts,
)


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
            await registry.broadcast(data)
    finally:
        with suppress(Exception):
            await pubsub.unsubscribe(EVENT_CHANNEL)
        with suppress(Exception):
            await pubsub.aclose()


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

# The dashboard is served from a different origin (its own host port) than this
# API, so the browser's REST fetches are cross-origin. Allow them via CORS.
# Origins are env-configurable (comma-separated); default "*" suits the local
# runtime/demo. WebSocket upgrades are not subject to CORS.
_cors_origins = os.environ.get("CORS_ALLOW_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors_origins],
    allow_methods=["*"],
    allow_headers=["*"],
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
    await websocket.accept()
    await registry.add(websocket)
    try:
        await websocket.send_text(json.dumps(await _build_snapshot()))
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
    """
    return current_vehicle_states(replica_connection)


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
