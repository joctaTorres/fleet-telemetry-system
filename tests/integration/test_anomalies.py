"""Phase proof-of-work: anomaly detection write path + ``GET /anomalies`` read.

The end-to-end slice for the ``anomaly-detection-and-query`` phase. Drives the
**ingestion** app (``POST /telemetry`` crossing each default threshold) and the
**frontend** app (``GET /anomalies``) against the same real Postgres from
``docker-compose.test.yml``, both in-process via FastAPI's ``TestClient`` (ASGI,
no running uvicorn).

Asserts each default anomaly class fires exactly when its threshold is crossed
and not otherwise, that the detected anomaly is reachable end to end over HTTP,
and that ``GET /anomalies`` returns a vehicle's in-window rows (inclusive bounds)
and excludes rows outside the window and rows for other vehicles. The by-absence
comms-loss-gap scenario is appended by the ``comms-loss-watchdog`` change.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.frontend_api import app as frontend_app
from app.ingestion_api import app as ingestion_app
from app.persistence import detect_comms_loss

BASE = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)


def _at(seconds: float) -> datetime:
    return BASE + timedelta(seconds=seconds)


@pytest.fixture
def ingest() -> TestClient:
    return TestClient(ingestion_app)


@pytest.fixture
def frontend() -> TestClient:
    return TestClient(frontend_app)


def _post(ingest: TestClient, **event) -> None:
    event.setdefault("battery_pct", 80)
    if isinstance(event.get("recorded_at"), datetime):
        event["recorded_at"] = event["recorded_at"].isoformat()
    resp = ingest.post("/telemetry", json=event)
    assert resp.status_code == 201, resp.text


def _get_types(frontend: TestClient, vehicle_id: str, since: datetime, until: datetime):
    resp = frontend.get(
        "/anomalies",
        params={
            "vehicle_id": vehicle_id,
            "since": since.isoformat(),
            "until": until.isoformat(),
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return body, [row["anomaly_type"] for row in body]


# ── 3.1 Each default anomaly class fires exactly when its threshold is crossed ─
# Driven through the ingestion write path and read back via GET /anomalies.


def test_fault_status_fires_only_when_status_is_fault(ingest, frontend):
    _post(ingest, vehicle_id="f-yes", status="fault", recorded_at=_at(0))
    _post(ingest, vehicle_id="f-no", status="moving", recorded_at=_at(0))

    _, yes = _get_types(frontend, "f-yes", _at(-1), _at(1))
    _, no = _get_types(frontend, "f-no", _at(-1), _at(1))
    assert "fault_status" in yes
    assert "fault_status" not in no


def test_error_codes_fire_only_when_non_empty(ingest, frontend):
    _post(ingest, vehicle_id="e-yes", status="moving", error_codes=["E101"],
          recorded_at=_at(0))
    _post(ingest, vehicle_id="e-no", status="moving", error_codes=[],
          recorded_at=_at(0))

    _, yes = _get_types(frontend, "e-yes", _at(-1), _at(1))
    _, no = _get_types(frontend, "e-no", _at(-1), _at(1))
    assert "error_codes" in yes
    assert "error_codes" not in no


def test_low_battery_fires_below_threshold_only_when_not_charging(ingest, frontend):
    _post(ingest, vehicle_id="b-low", status="moving", battery_pct=12,
          recorded_at=_at(0))
    _post(ingest, vehicle_id="b-thr", status="moving", battery_pct=15,
          recorded_at=_at(0))  # exactly at threshold: strict, must not fire
    _post(ingest, vehicle_id="b-chg", status="charging", battery_pct=12,
          recorded_at=_at(0))  # low but charging: must not fire

    _, low = _get_types(frontend, "b-low", _at(-1), _at(1))
    _, thr = _get_types(frontend, "b-thr", _at(-1), _at(1))
    _, chg = _get_types(frontend, "b-chg", _at(-1), _at(1))
    assert "low_battery" in low
    assert "low_battery" not in thr
    assert "low_battery" not in chg


def test_overspeed_fires_only_above_limit(ingest, frontend):
    _post(ingest, vehicle_id="o-fast", status="moving", speed_mps=6,
          recorded_at=_at(0))
    _post(ingest, vehicle_id="o-lim", status="moving", speed_mps=5,
          recorded_at=_at(0))  # exactly at limit: strict, must not fire

    _, fast = _get_types(frontend, "o-fast", _at(-1), _at(1))
    _, lim = _get_types(frontend, "o-lim", _at(-1), _at(1))
    assert "overspeed" in fast
    assert "overspeed" not in lim


def test_stuck_fires_only_after_dwell_window(ingest, frontend):
    # ≥10s moving-but-stationary fires; <10s does not.
    _post(ingest, vehicle_id="s-stuck", status="moving", speed_mps=0.05,
          recorded_at=_at(0))
    _post(ingest, vehicle_id="s-stuck", status="moving", speed_mps=0.05,
          recorded_at=_at(11))
    _post(ingest, vehicle_id="s-recent", status="moving", speed_mps=0.05,
          recorded_at=_at(0))
    _post(ingest, vehicle_id="s-recent", status="moving", speed_mps=0.05,
          recorded_at=_at(4))

    _, stuck = _get_types(frontend, "s-stuck", _at(-1), _at(20))
    _, recent = _get_types(frontend, "s-recent", _at(-1), _at(20))
    assert "stuck" in stuck
    assert "stuck" not in recent


def test_teleport_fires_only_above_implied_speed_limit(ingest, frontend):
    # 100 m in 1s → 100 m/s implied; 100 m in 10s → 10 m/s implied.
    _post(ingest, vehicle_id="t-yes", status="moving", pos_x=0, pos_y=0,
          recorded_at=_at(0))
    _post(ingest, vehicle_id="t-yes", status="moving", pos_x=100, pos_y=0,
          recorded_at=_at(1))
    _post(ingest, vehicle_id="t-no", status="moving", pos_x=0, pos_y=0,
          recorded_at=_at(0))
    _post(ingest, vehicle_id="t-no", status="moving", pos_x=100, pos_y=0,
          recorded_at=_at(10))

    _, yes = _get_types(frontend, "t-yes", _at(-1), _at(20))
    _, no = _get_types(frontend, "t-no", _at(-1), _at(20))
    assert "teleport" in yes
    assert "teleport" not in no


def test_battery_rising_fires_only_when_not_charging(ingest, frontend):
    _post(ingest, vehicle_id="r-yes", status="moving", battery_pct=40,
          recorded_at=_at(0))
    _post(ingest, vehicle_id="r-yes", status="moving", battery_pct=45,
          recorded_at=_at(1))
    _post(ingest, vehicle_id="r-chg", status="charging", battery_pct=40,
          recorded_at=_at(0))
    _post(ingest, vehicle_id="r-chg", status="charging", battery_pct=45,
          recorded_at=_at(1))

    _, yes = _get_types(frontend, "r-yes", _at(-1), _at(20))
    _, chg = _get_types(frontend, "r-chg", _at(-1), _at(20))
    assert "battery_rising" in yes
    assert "battery_rising" not in chg


def test_clean_event_produces_no_anomalies(ingest, frontend):
    _post(ingest, vehicle_id="clean", status="moving", battery_pct=80,
          speed_mps=2, recorded_at=_at(0))
    body, _ = _get_types(frontend, "clean", _at(-1), _at(1))
    assert body == []


# ── 2.2 / end-to-end reachability ───────────────────────────────────────────


def test_anomaly_reachable_end_to_end_over_http(ingest, frontend):
    """POST a threshold-crossing event, then GET it back for that vehicle."""
    _post(ingest, vehicle_id="agv-5", status="fault", battery_pct=12,
          speed_mps=6, error_codes=["E1"], recorded_at=_at(0))

    body, types = _get_types(frontend, "agv-5", _at(-1), _at(1))
    assert set(types) == {"fault_status", "error_codes", "low_battery", "overspeed"}
    # The detected anomaly carries its type, detail and detected_at.
    assert all({"vehicle_id", "anomaly_type", "detail", "detected_at"} <= row.keys()
               for row in body)
    assert all(row["vehicle_id"] == "agv-5" for row in body)


# ── 2.1 / 3.2 window + vehicle filtering ────────────────────────────────────


def _fault(ingest: TestClient, vehicle_id: str, when: datetime) -> None:
    _post(ingest, vehicle_id=vehicle_id, status="fault", recorded_at=when)


def test_get_anomalies_filters_by_window_and_vehicle(ingest, frontend):
    since, until = _at(0), _at(100)

    # agv-1: one before, three inside (both boundaries + interior), one after.
    _fault(ingest, "agv-1", _at(-10))
    _fault(ingest, "agv-1", since)  # exactly at since (inclusive)
    _fault(ingest, "agv-1", _at(50))
    _fault(ingest, "agv-1", until)  # exactly at until (inclusive)
    _fault(ingest, "agv-1", _at(110))
    # agv-2: one inside the window — must be excluded from agv-1's read.
    _fault(ingest, "agv-2", _at(50))

    body, _ = _get_types(frontend, "agv-1", since, until)

    assert all(row["vehicle_id"] == "agv-1" for row in body)
    detected = [datetime.fromisoformat(row["detected_at"]) for row in body]
    assert detected == [since, _at(50), until]  # in-window, ordered, inclusive


def test_get_anomalies_empty_for_vehicle_without_rows(ingest, frontend):
    body, _ = _get_types(frontend, "agv-9", _at(0), _at(100))
    assert body == []


# ── 5.1 By-absence comms-loss gap (the watchdog seam) ───────────────────────


def test_comms_loss_gap_flagged_and_readable_via_http(ingest, frontend):
    """A vehicle silent past the 5s timeout is flagged by the watchdog sweep and
    read back over GET /anomalies; one still within the timeout is not."""
    # Both report a clean reading once, then nothing more arrives for them.
    _post(ingest, vehicle_id="cl-silent", status="moving", recorded_at=_at(0))
    _post(ingest, vehicle_id="cl-fresh", status="moving", recorded_at=_at(4))

    # Run the watchdog sweep at now=_at(6): cutoff is _at(1) (strict). cl-silent's
    # last reading (_at(0)) is older than the cutoff → comms_loss; cl-fresh's
    # (_at(4)) is within the timeout → no flag.
    detect_comms_loss(now=_at(6))

    _, silent = _get_types(frontend, "cl-silent", _at(0), _at(10))
    _, fresh = _get_types(frontend, "cl-fresh", _at(0), _at(10))
    assert "comms_loss" in silent
    assert "comms_loss" not in fresh
