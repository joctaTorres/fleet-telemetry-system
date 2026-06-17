"""Phase proof-of-work: end-to-end ingest → fleet-state slice (plan task 3.3).

Concurrently POST telemetry for 50 distinct vehicles across mixed statuses via
the ingestion app, then assert the frontend ``GET /fleet/state`` returns
per-status counts that sum to 50 and exactly match the last event per vehicle —
proving no upsert was lost or double-counted under concurrent writes.

Both apps run in-process with FastAPI's ``TestClient`` (ASGI, no running uvicorn)
against the same real Postgres from ``docker-compose.test.yml``: the ingestion
app writes, the frontend app reads back.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from app.frontend_api import app as frontend_app
from app.ingestion_api import app as ingestion_app
from app.models import STATUSES

# Each vehicle gets an initial status then a distinct final status, both POSTed
# sequentially within one worker so the "last event" is deterministic; the 50
# workers run concurrently so the writes genuinely race at the database.
_VEHICLES = [
    {
        "vehicle_id": f"v-{i:03d}",
        "initial": STATUSES[i % len(STATUSES)],
        "final": STATUSES[(i + 1) % len(STATUSES)],
        "battery_pct": float(i % 101),
    }
    for i in range(50)
]


def test_concurrent_ingest_then_fleet_state_matches_last_event_per_vehicle():
    """3.3 — proof-of-work: 50 vehicles, concurrent ingest, exact aggregate."""
    ingest = TestClient(ingestion_app)

    def emit(v: dict) -> None:
        for status in (v["initial"], v["final"]):
            resp = ingest.post(
                "/telemetry",
                json={
                    "vehicle_id": v["vehicle_id"],
                    "status": status,
                    "battery_pct": v["battery_pct"],
                },
            )
            assert resp.status_code == 201

    with ThreadPoolExecutor(max_workers=25) as pool:
        list(pool.map(emit, _VEHICLES))

    expected: dict[str, int] = {s: 0 for s in STATUSES}
    for v in _VEHICLES:
        expected[v["final"]] += 1

    resp = TestClient(frontend_app).get("/fleet/state")

    assert resp.status_code == 200
    counts = resp.json()
    assert sum(counts.values()) == 50
    assert counts == expected
