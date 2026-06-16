"""Frontend ``GET /zones/counts`` integration + phase proof-of-work tests.

These exercise the frontend and ingestion apps in-process with FastAPI's
``TestClient`` (ASGI, no running uvicorn) against the same real Postgres from
``docker-compose.test.yml``: the ingestion app writes (``POST /telemetry`` with
``zone_entered``), the frontend app reads the live per-zone totals back
(``GET /zones/counts``). Each test starts from a freshly-seeded baseline (all
zone counters at 0).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.frontend_api import app as frontend_app
from app.ingestion_api import app as ingestion_app
from app.models import ZONES, TelemetryEvent
from app.persistence import persist_telemetry


@pytest.fixture
def client() -> TestClient:
    return TestClient(frontend_app)


def test_get_zone_counts_freshly_seeded_all_zero(client: TestClient):
    """2.1 — against a freshly seeded database, GET returns 200 and all ~20
    zones with count 0."""
    resp = client.get("/zones/counts")

    assert resp.status_code == 200
    counts = resp.json()
    assert set(counts) == set(ZONES)
    assert len(counts) == len(ZONES)
    assert all(count == 0 for count in counts.values())


def test_get_zone_counts_reports_live_totals_others_unchanged(client: TestClient):
    """2.2 — after a mix of zone entries, GET reports each zone's live total and
    leaves every other zone at 0."""
    persist_telemetry(
        TelemetryEvent(vehicle_id="a", status="moving", battery_pct=10, zone_entered="zone-03")
    )
    persist_telemetry(
        TelemetryEvent(vehicle_id="b", status="moving", battery_pct=20, zone_entered="zone-03")
    )
    persist_telemetry(
        TelemetryEvent(vehicle_id="c", status="idle", battery_pct=30, zone_entered="zone-11")
    )

    resp = client.get("/zones/counts")

    assert resp.status_code == 200
    counts = resp.json()
    assert counts["zone-03"] == 2
    assert counts["zone-11"] == 1
    entered = {"zone-03", "zone-11"}
    for zone_id, count in counts.items():
        if zone_id not in entered:
            assert count == 0


def test_concurrent_zone_entries_counted_exactly_via_get():
    """3.1 — proof-of-work: N concurrent ``zone_entered`` events for one zone via
    the ingestion API yield ``GET /zones/counts`` reporting that zone's count
    == N exactly (no lost increments)."""
    n = 50
    ingest = TestClient(ingestion_app)

    def emit(i: int) -> None:
        resp = ingest.post(
            "/telemetry",
            json={
                "vehicle_id": f"v-{i:03d}",
                "status": "moving",
                "battery_pct": float(i % 101),
                "zone_entered": "zone-07",
            },
        )
        assert resp.status_code == 201

    with ThreadPoolExecutor(max_workers=25) as pool:
        list(pool.map(emit, range(n)))

    resp = TestClient(frontend_app).get("/zones/counts")

    assert resp.status_code == 200
    counts = resp.json()
    assert counts["zone-07"] == n
    assert sum(counts.values()) == n  # every increment landed on zone-07 only


def test_null_zone_entered_leaves_all_counts_unchanged():
    """3.2 — proof-of-work: events with ``zone_entered=null`` leave every zone's
    count unchanged (all 0)."""
    ingest = TestClient(ingestion_app)
    for i in range(10):
        resp = ingest.post(
            "/telemetry",
            json={
                "vehicle_id": f"n-{i:03d}",
                "status": "idle",
                "battery_pct": float(i),
            },
        )
        assert resp.status_code == 201

    resp = TestClient(frontend_app).get("/zones/counts")

    assert resp.status_code == 200
    counts = resp.json()
    assert set(counts) == set(ZONES)
    assert all(count == 0 for count in counts.values())


def test_get_zone_counts_returns_all_seeded_zones(client: TestClient):
    """3.3 — proof-of-work: GET returns all ~20 seeded zones, even when only some
    have been entered."""
    persist_telemetry(
        TelemetryEvent(vehicle_id="x", status="moving", battery_pct=50, zone_entered="zone-09")
    )

    resp = client.get("/zones/counts")

    assert resp.status_code == 200
    counts = resp.json()
    assert set(counts) == set(ZONES)
    assert len(counts) == len(ZONES) == 20
    assert counts["zone-09"] == 1
