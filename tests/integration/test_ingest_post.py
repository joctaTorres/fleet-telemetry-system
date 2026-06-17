"""HTTP ingestion write-path integration tests (plan tasks 3.1–3.3).

These exercise ``POST /telemetry`` in-process with FastAPI's ``TestClient``
(ASGI, no running uvicorn) against the real Postgres from
``docker-compose.test.yml``. The endpoint validates with ``TelemetryEvent`` and
persists via the existing ``persist_telemetry``; the assertions read the
database back through the shared helpers.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.ingestion_api import app

from .helpers import count_raw_events, current_state, current_state_row_count


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_post_valid_event_persists_raw_and_current_state(client: TestClient):
    """3.1 — a valid POST returns 201 and writes one raw + one current row."""
    resp = client.post(
        "/telemetry",
        json={"vehicle_id": "v-12", "status": "moving", "battery_pct": 78},
    )

    assert resp.status_code == 201
    assert count_raw_events("v-12") == 1
    assert current_state_row_count("v-12") == 1
    assert current_state("v-12") == ("moving", 78)


def test_post_later_event_upserts_current_state_and_appends_raw(client: TestClient):
    """3.2 — a later POST for the same vehicle upserts the one current row."""
    first = client.post(
        "/telemetry",
        json={"vehicle_id": "v-12", "status": "moving", "battery_pct": 78},
    )
    second = client.post(
        "/telemetry",
        json={"vehicle_id": "v-12", "status": "charging", "battery_pct": 80},
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert current_state_row_count("v-12") == 1
    assert current_state("v-12") == ("charging", 80)
    assert count_raw_events("v-12") == 2


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(
            {"vehicle_id": "v-1", "status": "exploded", "battery_pct": 50},
            id="bad-status",
        ),
        pytest.param(
            {"vehicle_id": "v-1", "status": "idle", "battery_pct": 150},
            id="battery-too-high",
        ),
        pytest.param(
            {"vehicle_id": "v-1", "status": "idle", "battery_pct": -1},
            id="battery-negative",
        ),
        pytest.param(
            {"vehicle_id": "v-1", "status": "idle"},
            id="missing-battery_pct",
        ),
        pytest.param(
            {"status": "idle", "battery_pct": 50},
            id="missing-vehicle_id",
        ),
        pytest.param(
            {"vehicle_id": "", "status": "idle", "battery_pct": 50},
            id="empty-vehicle_id",
        ),
        pytest.param(
            {
                "vehicle_id": "v-1",
                "status": "idle",
                "battery_pct": 50,
                "rogue": True,
            },
            id="unknown-field",
        ),
    ],
)
def test_post_schema_invalid_body_is_422_and_persists_nothing(
    client: TestClient, body: dict
):
    """3.3 — schema-invalid bodies are rejected with 422 and write nothing."""
    resp = client.post("/telemetry", json=body)

    assert resp.status_code == 422
    assert count_raw_events("v-1") == 0
    assert current_state_row_count("v-1") == 0
