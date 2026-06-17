"""Phase blackbox proof-of-work: the whole real-time path, end to end.

This is the ``realtime-cdc-websocket`` phase proof named in the phase definition
(``docker compose -f docker-compose.test.yml run --rm api pytest
tests/integration/test_realtime_ws.py``). Unlike the upstream slices — each of
which proved one segment under isolation — this drives the *entire* topology:

    POST /telemetry (stateless ingestion API)
      → commit on the primary
        → WAL → the standalone ``cdc`` service decodes pgoutput and publishes
          → Redis ``fleet:events``
            → the stateful frontend fans the patch out
              → a connected WebSocket client receives it, sub-second.

The CDC consumer runs as its own long-lived compose service (not the in-process
``cdc_stream`` fixture, which is deliberately unused here); the proof gates on it
actually streaming the slot before writing, so the assertion never races startup.
Both ASGI apps run in-process via FastAPI's ``TestClient`` (the frontend's ``with``
block runs its lifespan, attaching the Redis subscriber) against the real primary
+ replica + Redis + ``cdc`` service from ``docker-compose.test.yml``.
"""

from __future__ import annotations

import inspect

import psycopg
import pytest
from fastapi.testclient import TestClient

import app.ingestion_api as ingestion_module
import app.persistence as persistence_module
from app.config import get_dsn
from app.events import ANOMALY_DETECTED, VEHICLE_STATE_CHANGED, ZONE_COUNT_CHANGED
from app.frontend_api import app as frontend_app
from app.ingestion_api import app as ingestion_app

from .conftest import (
    WsReader,
    wait_cdc_service_streaming,
    wait_frontend_subscribed,
)

#: Upper bound on real-time delivery. The phase requires sub-second propagation
#: across the full async path (commit → WAL → decode → publish → fan-out).
SUB_SECOND = 1.0


@pytest.fixture
def frontend() -> TestClient:
    # The ``with`` block runs startup/shutdown, so the lifespan Redis subscriber
    # is attached for the duration of each test.
    with TestClient(frontend_app) as c:
        yield c


@pytest.fixture
def ingest() -> TestClient:
    # The ingestion API is stateless and synchronous — no lifespan needed.
    return TestClient(ingestion_app)


def _commit(sql: str, params: dict) -> None:
    """Execute ``sql`` on the primary in its own committed transaction."""
    with psycopg.connect(get_dsn(), autocommit=True) as conn:
        conn.execute(sql, params)


def _connect_ready_ws(frontend: TestClient):
    """Open a ``/ws`` connection, drain the snapshot, and confirm the live path.

    Returns ``(ws, reader)``: the entered WebSocket context manager and a
    :class:`WsReader` draining its deltas. On return the CDC service is confirmed
    streaming the slot and the frontend subscriber is confirmed attached, so a
    subsequent committed write will actually propagate. The reader is started
    *after* the snapshot is drained, so it captures only post-connect deltas.
    """
    wait_cdc_service_streaming()
    ws = frontend.websocket_connect("/ws")
    ws.__enter__()
    ws.receive_json()  # drain the one-shot snapshot
    reader = WsReader(ws)
    wait_frontend_subscribed()
    return ws, reader


def test_fault_post_delivers_vehicle_state_changed(
    frontend: TestClient, ingest: TestClient
):
    """4.1 — a committed fault telemetry POST → vehicle_state_changed sub-second."""
    ws, reader = _connect_ready_ws(frontend)
    try:
        resp = ingest.post(
            "/telemetry",
            json={"vehicle_id": "rt-fault", "status": "fault", "battery_pct": 80},
        )
        assert resp.status_code == 201

        event = reader.matching(
            lambda e: e["type"] == VEHICLE_STATE_CHANGED
            and e["payload"]["vehicle_id"] == "rt-fault",
            SUB_SECOND,
        )
    finally:
        ws.__exit__(None, None, None)

    assert event is not None
    assert event["payload"]["status"] == "fault"


def test_zone_entry_delivers_zone_count_changed(
    frontend: TestClient, ingest: TestClient
):
    """4.2 — a telemetry POST with a zone entry → zone_count_changed sub-second."""
    ws, reader = _connect_ready_ws(frontend)
    try:
        resp = ingest.post(
            "/telemetry",
            json={
                "vehicle_id": "rt-zone",
                "status": "moving",
                "battery_pct": 80,
                "zone_entered": "high_bay_1",
            },
        )
        assert resp.status_code == 201

        # Counters are reset to 0 before each test, so this entry yields exactly 1.
        # Filtering on the incremented value also skips the cleanup reset deltas.
        event = reader.matching(
            lambda e: e["type"] == ZONE_COUNT_CHANGED
            and e["payload"]["zone_id"] == "high_bay_1"
            and e["payload"]["entry_count"] == 1,
            SUB_SECOND,
        )
    finally:
        ws.__exit__(None, None, None)

    assert event is not None
    assert event["payload"] == {"zone_id": "high_bay_1", "entry_count": 1}


def test_low_battery_post_delivers_anomaly_detected(
    frontend: TestClient, ingest: TestClient
):
    """4.3 — a telemetry POST that commits a low_battery anomaly → anomaly_detected."""
    ws, reader = _connect_ready_ws(frontend)
    try:
        resp = ingest.post(
            "/telemetry",
            json={"vehicle_id": "rt-anom", "status": "idle", "battery_pct": 9},
        )
        assert resp.status_code == 201

        event = reader.matching(
            lambda e: e["type"] == ANOMALY_DETECTED
            and e["payload"]["vehicle_id"] == "rt-anom"
            and e["payload"]["anomaly_type"] == "low_battery",
            SUB_SECOND,
        )
    finally:
        ws.__exit__(None, None, None)

    assert event is not None
    assert event["payload"]["anomaly_type"] == "low_battery"


def test_rolled_back_write_delivers_no_event(frontend: TestClient):
    """4.4 — an aborted write against a watched table surfaces nothing.

    pgoutput frames by Begin/Commit, so an uncommitted transaction never decodes
    into an event. A committed sentinel afterward proves the absence is real — the
    CDC stream is alive and would have delivered a committed change.
    """
    ws, reader = _connect_ready_ws(frontend)
    try:
        # Abort an increment on pick_zone_2; logical decoding frames on Commit, so an
        # uncommitted change is never decoded and no pick_zone_2 delta should appear.
        with psycopg.connect(get_dsn()) as conn:
            conn.execute(
                "UPDATE zone_counts SET entry_count = entry_count + 1 "
                "WHERE zone_id = %(zone_id)s",
                {"zone_id": "pick_zone_2"},
            )
            conn.rollback()

        spurious = reader.matching(
            lambda e: e["type"] == ZONE_COUNT_CHANGED
            and e["payload"]["zone_id"] == "pick_zone_2",
            SUB_SECOND,
        )
        assert spurious is None

        # A committed sentinel on the same zone must still flow through.
        _commit(
            "UPDATE zone_counts SET entry_count = entry_count + 1 "
            "WHERE zone_id = %(zone_id)s",
            {"zone_id": "pick_zone_2"},
        )
        sentinel = reader.matching(
            lambda e: e["type"] == ZONE_COUNT_CHANGED
            and e["payload"]["zone_id"] == "pick_zone_2",
            SUB_SECOND,
        )
    finally:
        ws.__exit__(None, None, None)

    assert sentinel is not None
    assert sentinel["payload"]["entry_count"] == 1


def test_cdc_is_sole_producer_ingestion_never_publishes(
    frontend: TestClient, ingest: TestClient
):
    """4.5 — the delivered event comes from CDC; the write path publishes nothing.

    Two complementary checks: (1) the ingestion write path has no Redis reference
    at all, so it cannot be a producer; (2) a clean telemetry POST (no anomaly, no
    zone) derives exactly one delta — the CDC-decoded vehicle_state_changed — with
    no duplicate, as there would be if the write path also published.
    """
    # (1) The write path contains no Redis reference whatsoever.
    for module in (ingestion_module, persistence_module):
        source = inspect.getsource(module)
        assert "redis" not in source.lower(), (
            f"{module.__name__} references Redis; the write path must never publish"
        )

    # (2) Exactly one delta for a clean POST — CDC is the single producer.
    ws, reader = _connect_ready_ws(frontend)
    try:
        resp = ingest.post(
            "/telemetry",
            json={"vehicle_id": "rt-solo", "status": "idle", "battery_pct": 80},
        )
        assert resp.status_code == 201

        first = reader.matching(
            lambda e: e["payload"].get("vehicle_id") == "rt-solo",
            SUB_SECOND,
        )
        assert first is not None
        assert first["type"] == VEHICLE_STATE_CHANGED

        # No second delta for this vehicle — a dual-write would surface one here.
        duplicate = reader.matching(
            lambda e: e["payload"].get("vehicle_id") == "rt-solo",
            0.5,
        )
    finally:
        ws.__exit__(None, None, None)

    assert duplicate is None
