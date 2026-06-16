"""Phase-4 (fault-transition) proof-of-work: the HTTP status-update endpoint.

Exercises ``POST /vehicles/{vehicle_id}/status`` in-process with FastAPI's
``TestClient`` (ASGI, no running uvicorn) against the real Postgres from
``docker-compose.test.yml``. A ``fault`` update delegates to the proven
``transition_to_fault`` seam, so the HTTP path inherits its idempotency: after
concurrent and duplicate fault POSTs for one vehicle, exactly one mission is
cancelled and exactly one maintenance record exists, and the vehicle ends in
``status='fault'``. Assertions read the database back through the shared
fault-domain helpers.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.ingestion_api import app

from .helpers import (
    active_mission_count,
    maintenance_record_count,
    mission_status_counts,
    seed_active_mission,
    seed_vehicle,
    vehicle_status,
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _fault(client: TestClient, vehicle_id: str, reason: str | None = None):
    body: dict[str, object] = {"status": "fault"}
    if reason is not None:
        body["reason"] = reason
    return client.post(f"/vehicles/{vehicle_id}/status", json=body)


def test_single_fault_post_cancels_mission_opens_one_record(client: TestClient):
    """4.1 — a fault POST cancels the active mission, writes exactly one
    maintenance record, sets fault, and returns 200 with applied=True."""
    seed_vehicle("v-1", status="moving")
    seed_active_mission("v-1")

    resp = _fault(client, "v-1", reason="overheat")

    assert resp.status_code == 200
    assert resp.json() == {"vehicle_id": "v-1", "status": "fault", "applied": True}
    assert vehicle_status("v-1") == "fault"
    assert active_mission_count("v-1") == 0
    assert mission_status_counts("v-1") == {"cancelled": 1}
    assert maintenance_record_count("v-1") == 1


def test_duplicate_sequential_fault_post_is_noop(client: TestClient):
    """4.2 — a second identical fault POST is a no-op: one cancelled mission, one
    maintenance record, status fault; applied=True then applied=False."""
    seed_vehicle("v-2", status="moving")
    seed_active_mission("v-2")

    first = _fault(client, "v-2")
    second = _fault(client, "v-2")

    assert first.status_code == 200
    assert first.json()["applied"] is True
    assert second.status_code == 200
    assert second.json()["applied"] is False

    assert vehicle_status("v-2") == "fault"
    assert mission_status_counts("v-2") == {"cancelled": 1}
    assert maintenance_record_count("v-2") == 1


def test_concurrent_fault_posts_settle_exactly_once(client: TestClient):
    """4.3 — phase proof: many concurrent fault POSTs for one vehicle leave
    exactly one cancelled mission and exactly one maintenance record, status
    fault; exactly one request reports applied=True."""
    seed_vehicle("v-3", status="moving")
    seed_active_mission("v-3")

    with ThreadPoolExecutor(max_workers=20) as pool:
        responses = list(pool.map(lambda _: _fault(client, "v-3"), range(20)))

    assert all(r.status_code == 200 for r in responses)
    assert sum(1 for r in responses if r.json()["applied"] is True) == 1
    assert vehicle_status("v-3") == "fault"
    assert active_mission_count("v-3") == 0
    assert mission_status_counts("v-3") == {"cancelled": 1}
    assert maintenance_record_count("v-3") == 1


def test_fault_post_for_unknown_vehicle_is_404(client: TestClient):
    """4.4 — a fault POST for a vehicle with no row returns 404 and writes no
    maintenance record."""
    resp = _fault(client, "ghost", reason="manual")

    assert resp.status_code == 404
    assert vehicle_status("ghost") is None
    assert maintenance_record_count("ghost") == 0


def test_schema_invalid_status_is_422_and_changes_nothing(client: TestClient):
    """4.5 — a bad status value returns 422 and leaves the vehicle's status and
    active mission unchanged."""
    seed_vehicle("v-5", status="moving")
    seed_active_mission("v-5")

    resp = client.post(
        "/vehicles/v-5/status", json={"status": "exploded", "reason": "x"}
    )

    assert resp.status_code == 422
    assert vehicle_status("v-5") == "moving"
    assert active_mission_count("v-5") == 1
    assert maintenance_record_count("v-5") == 0
