"""Frontend ``GET /fleet/state`` integration tests (plan tasks 3.1–3.2).

These exercise the frontend API in-process with FastAPI's ``TestClient`` (ASGI,
no running uvicorn) against the real Postgres from ``docker-compose.test.yml``.
The route returns the existing ``aggregate_fleet_state()`` as JSON.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.frontend_api import app
from app.models import STATUSES, TelemetryEvent
from app.persistence import persist_telemetry


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_get_fleet_state_returns_per_status_counts_absent_as_zero(client: TestClient):
    """3.1 — over a mix of statuses, GET returns 200 and per-status counts;
    statuses with no vehicles are reported as 0."""
    persist_telemetry(TelemetryEvent(vehicle_id="a", status="moving", battery_pct=50))
    persist_telemetry(TelemetryEvent(vehicle_id="b", status="moving", battery_pct=60))
    persist_telemetry(TelemetryEvent(vehicle_id="c", status="idle", battery_pct=70))

    resp = client.get("/fleet/state")

    assert resp.status_code == 200
    assert resp.json() == {"idle": 1, "moving": 2, "charging": 0, "fault": 0}


def test_get_fleet_state_empty_db_all_zero(client: TestClient):
    """3.2 — against an empty database, all four statuses are reported as 0."""
    resp = client.get("/fleet/state")

    assert resp.status_code == 200
    assert resp.json() == {s: 0 for s in STATUSES}
