"""WebSocket fan-out proof-of-work for the ``websocket-fanout`` change.

This is the change's *own* proof — not the phase blackbox proof
(``test_realtime_ws.py``), which the ``cdc-consumer`` follow-on lands once the
CDC source exists. Here the fan-out half is proven at the Redis seam: the test
stands in for the not-yet-built CDC consumer by publishing state patches
directly on the channel, and asserts a connected WebSocket client receives them.

The frontend ASGI app runs in-process via FastAPI's ``TestClient`` (whose
``with`` block runs the lifespan, so the Redis subscriber starts) against the
real Postgres and Redis from ``docker-compose.test.yml``.
"""

from __future__ import annotations

import queue
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.events import (
    ANOMALY_DETECTED,
    SNAPSHOT,
    VEHICLE_STATE_CHANGED,
    ZONE_COUNT_CHANGED,
)
from app.frontend_api import app
from app.models import TelemetryEvent
from app.persistence import persist_telemetry

from .conftest import publish_event, wait_replica_caught_up

#: Upper bound on real-time delivery. The phase requires sub-second propagation.
SUB_SECOND = 1.0


@pytest.fixture
def client() -> TestClient:
    # The ``with`` block runs startup/shutdown, so the lifespan Redis subscriber
    # is attached for the duration of each test.
    with TestClient(app) as c:
        yield c


def _recv_json_within(ws, timeout_s: float):
    """Receive one JSON WS message within ``timeout_s``; return None on timeout.

    ``TestClient``'s ``receive_json`` blocks with no timeout, so it runs on a
    daemon thread and the result is collected through a queue. A daemon thread
    never blocks interpreter exit, so a deliberate timeout (the "no message"
    case) leaves nothing hanging.
    """
    out: queue.Queue = queue.Queue(maxsize=1)

    def _recv() -> None:
        try:
            out.put(("ok", ws.receive_json()))
        except Exception as err:  # noqa: BLE001 - surfaced to the caller
            out.put(("err", err))

    threading.Thread(target=_recv, daemon=True).start()
    try:
        kind, value = out.get(timeout=timeout_s)
    except queue.Empty:
        return None
    if kind == "err":
        raise value
    return value


def _publish_until_delivered(redis_client, event: dict, deadline_s: float = 2.0) -> None:
    """Publish ``event`` until the frontend subscriber is attached to receive it.

    The lifespan subscriber's ``SUBSCRIBE`` completes asynchronously, so an early
    ``PUBLISH`` can land with zero subscribers (Redis pub/sub does not buffer).
    Retry until ``PUBLISH`` reports a receiver, so exactly the delivering publish
    reaches the subscriber.
    """
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        if publish_event(redis_client, event) >= 1:
            return
        time.sleep(0.02)
    raise AssertionError("frontend Redis subscriber never attached to the channel")


def test_connect_sends_one_shot_snapshot(client: TestClient, redis_client):
    """4.1 — the first WS message is a snapshot of committed fleet + zone state."""
    persist_telemetry(TelemetryEvent(vehicle_id="a", status="moving", battery_pct=50))
    persist_telemetry(TelemetryEvent(vehicle_id="b", status="idle", battery_pct=60))
    persist_telemetry(
        TelemetryEvent(vehicle_id="c", status="moving", battery_pct=70, zone_entered="zone-01")
    )
    # The snapshot is now served from the streaming replica; wait out the small
    # async-replication lag so the assertion is against caught-up state.
    wait_replica_caught_up()

    with client.websocket_connect("/ws") as ws:
        snapshot = ws.receive_json()

    assert snapshot["type"] == SNAPSHOT
    assert snapshot["payload"]["fleet"] == {
        "idle": 1,
        "moving": 2,
        "charging": 0,
        "fault": 0,
    }
    zones = snapshot["payload"]["zones"]
    assert zones["zone-01"] == 1
    # All seeded zones are present, with never-entered zones reporting zero.
    assert zones["zone-02"] == 0


def test_published_patch_received_sub_second(client: TestClient, redis_client):
    """4.2 — a published vehicle_state_changed patch reaches the client sub-second."""
    patch = {
        "type": VEHICLE_STATE_CHANGED,
        "payload": {"vehicle_id": "v1", "status": "fault"},
    }
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # drain the snapshot

        started = time.monotonic()
        _publish_until_delivered(redis_client, patch)
        received = _recv_json_within(ws, SUB_SECOND)
        elapsed = time.monotonic() - started

    assert received == patch
    assert elapsed < SUB_SECOND


def test_all_three_event_types_forwarded_verbatim(client: TestClient, redis_client):
    """4.3 — one patch of each event type is forwarded with its type preserved."""
    patches = [
        {"type": VEHICLE_STATE_CHANGED, "payload": {"vehicle_id": "v1", "status": "idle"}},
        {"type": ANOMALY_DETECTED, "payload": {"vehicle_id": "v1", "anomaly_type": "overspeed"}},
        {"type": ZONE_COUNT_CHANGED, "payload": {"zone_id": "zone-03", "entry_count": 7}},
    ]
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # drain the snapshot

        for patch in patches:
            _publish_until_delivered(redis_client, patch)
            received = _recv_json_within(ws, SUB_SECOND)
            assert received == patch
            assert received["type"] == patch["type"]


def test_patch_fanned_out_to_two_clients(client: TestClient, redis_client):
    """4.4 — a single published patch is delivered to both connected clients."""
    patch = {"type": ZONE_COUNT_CHANGED, "payload": {"zone_id": "zone-05", "entry_count": 3}}
    with client.websocket_connect("/ws") as ws_a, client.websocket_connect("/ws") as ws_b:
        ws_a.receive_json()  # drain snapshots
        ws_b.receive_json()

        _publish_until_delivered(redis_client, patch)

        assert _recv_json_within(ws_a, SUB_SECOND) == patch
        assert _recv_json_within(ws_b, SUB_SECOND) == patch


def test_no_message_when_nothing_published(client: TestClient, redis_client):
    """4.5 — with nothing published, no further message arrives after the snapshot."""
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # the snapshot is the only message

        assert _recv_json_within(ws, 0.5) is None


def test_disconnected_client_does_not_block_fanout(client: TestClient, redis_client):
    """4.6 — a disconnected client is dropped and does not block the survivor."""
    patch = {"type": VEHICLE_STATE_CHANGED, "payload": {"vehicle_id": "v9", "status": "charging"}}
    with client.websocket_connect("/ws") as ws_survivor:
        ws_survivor.receive_json()  # drain snapshot

        with client.websocket_connect("/ws") as ws_gone:
            ws_gone.receive_json()
        # ws_gone is now disconnected; its registry entry is stale until the next
        # broadcast, which must drop it and still reach the survivor.

        _publish_until_delivered(redis_client, patch)

        assert _recv_json_within(ws_survivor, SUB_SECOND) == patch
