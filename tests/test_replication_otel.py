"""Unit tests for the replication-lag probe (plan tasks 1-4, 6, 7).

These run fully in-process: no collector, no running Alloy, and no Postgres. They
feed the pure lag computations from fixtures, drive a single sample against a
fake connection to assert the gauges record, and confirm the safe no-endpoint
path stays green — exactly the contract the downstream "Primary/Replica
Streaming" dashboard binds to.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from app import replication_probe


# ── pure computations (tasks 2, 3) ──────────────────────────────────────────
def test_byte_lag_samples_translates_rows():
    """2 — pg_stat_replication rows become (standby, lag_bytes) pairs."""
    rows = [("walreceiver", 4096), ("other", 0)]
    assert replication_probe.byte_lag_samples(rows) == [
        ("walreceiver", 4096),
        ("other", 0),
    ]


def test_byte_lag_samples_skips_null_lag_and_defaults_name():
    """2/6 — a standby with no replay_lsn yet records no value; blank name fallback."""
    rows = [("", 8192), ("absent", None)]
    assert replication_probe.byte_lag_samples(rows) == [("standby", 8192)]


def test_seconds_lag_value_from_replay_timestamp():
    """3 — seconds lag is now() minus the last replay timestamp."""
    now = datetime(2026, 6, 17, 12, 0, 30, tzinfo=timezone.utc)
    replay = now - timedelta(seconds=12.5)
    assert replication_probe.seconds_lag_value(replay, now) == 12.5


def test_seconds_lag_value_floors_at_zero_and_handles_missing():
    """3/6 — clock skew never yields negative lag; a fresh standby records nothing."""
    now = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)
    ahead = now + timedelta(seconds=0.4)  # replica clock slightly ahead
    assert replication_probe.seconds_lag_value(ahead, now) == 0.0
    assert replication_probe.seconds_lag_value(None, now) is None


# ── gauge recording against an in-memory meter (tasks 4, 7) ──────────────────
def _collect_points(reader: InMemoryMetricReader) -> dict:
    """Collect once and map every gauge name to its first data point.

    A synchronous gauge only reports values set since the previous collection, so
    ``get_metrics_data()`` must be called a single time and the result searched —
    a second call would drain to empty and drop metrics.
    """
    data = reader.get_metrics_data()
    points: dict = {}
    if data is None:
        return points
    for resource_metric in data.resource_metrics:
        for scope_metric in resource_metric.scope_metrics:
            for metric in scope_metric.metrics:
                pts = list(metric.data.data_points)
                if pts:
                    points[metric.name] = pts[0]
    return points


class _FakeCursorResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    """A context-manager connection whose execute() returns canned rows by query."""

    def __init__(self, responses):
        self._responses = responses

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, *args):
        for needle, rows in self._responses.items():
            if needle in sql:
                return _FakeCursorResult(rows)
        raise AssertionError(f"unexpected query: {sql}")


def test_sample_once_records_both_gauges(monkeypatch):
    """4/7 — a sample sets pg.replication.lag_bytes and pg.replication.lag_seconds."""
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader]).get_meter("test")
    monkeypatch.setattr(
        replication_probe, "LAG_BYTES", meter.create_gauge("pg.replication.lag_bytes")
    )
    monkeypatch.setattr(
        replication_probe,
        "LAG_SECONDS",
        meter.create_gauge("pg.replication.lag_seconds"),
    )

    now = datetime(2026, 6, 17, 12, 0, 5, tzinfo=timezone.utc)
    monkeypatch.setattr(replication_probe, "_now", lambda: now)

    responses_by_dsn = {
        "primary": {"pg_stat_replication": [("walreceiver", 2048)]},
        "replica": {
            "pg_last_xact_replay_timestamp": [(now - timedelta(seconds=3),)]
        },
    }

    def fake_connect(dsn, *args, **kwargs):
        return _FakeConn(responses_by_dsn[dsn])

    monkeypatch.setattr(replication_probe.psycopg, "connect", fake_connect)

    replication_probe.sample_once("primary", "replica")

    points = _collect_points(reader)
    byte_point = points.get("pg.replication.lag_bytes")
    assert byte_point is not None
    assert byte_point.value == 2048
    assert byte_point.attributes["application_name"] == "walreceiver"

    sec_point = points.get("pg.replication.lag_seconds")
    assert sec_point is not None
    assert sec_point.value == 3.0


def test_sample_once_swallows_source_errors(monkeypatch):
    """6 — a failing primary query never blocks the replica sample, never raises."""
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader]).get_meter("test")
    monkeypatch.setattr(
        replication_probe, "LAG_BYTES", meter.create_gauge("pg.replication.lag_bytes")
    )
    monkeypatch.setattr(
        replication_probe,
        "LAG_SECONDS",
        meter.create_gauge("pg.replication.lag_seconds"),
    )

    now = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(replication_probe, "_now", lambda: now)

    def fake_connect(dsn, *args, **kwargs):
        if dsn == "primary":
            raise RuntimeError("primary unreachable")
        return _FakeConn(
            {"pg_last_xact_replay_timestamp": [(now - timedelta(seconds=1),)]}
        )

    monkeypatch.setattr(replication_probe.psycopg, "connect", fake_connect)

    replication_probe.sample_once("primary", "replica")  # must not raise

    points = _collect_points(reader)
    assert "pg.replication.lag_bytes" not in points
    sec_point = points.get("pg.replication.lag_seconds")
    assert sec_point is not None and sec_point.value == 1.0


# ── startup wiring + safe-by-default (tasks 1, 7) ────────────────────────────
def test_run_forever_installs_otel_through_bootstrap(monkeypatch):
    """1 — process startup calls configure_otel("replication-probe") via the bootstrap."""
    assert replication_probe.SERVICE_NAME == "replication-probe"

    calls: dict = {}
    monkeypatch.setattr(
        replication_probe,
        "configure_otel",
        lambda name: calls.setdefault("name", name),
    )
    monkeypatch.setattr(replication_probe, "get_dsn", lambda: "primary")
    monkeypatch.setattr(replication_probe, "get_replica_dsn", lambda: "replica")
    monkeypatch.setattr(replication_probe, "sample_once", lambda *a: None)

    import threading

    stop = threading.Event()
    stop.set()  # loop exits on the first check, after configure_otel has run
    replication_probe.run_forever(stop)

    assert calls["name"] == "replication-probe"


def test_instruments_are_module_level_off_the_bootstrap_meter():
    """1/4 — both gauges come from the shared bootstrap meter, swappable in tests."""
    assert isinstance(
        replication_probe._meter,
        type(replication_probe.metrics.get_meter(__name__)),
    )
    assert replication_probe.LAG_BYTES is not None
    assert replication_probe.LAG_SECONDS is not None


def test_sample_is_a_no_op_path_without_a_collector(monkeypatch):
    """7 — with the default (no-op) gauges, a sample records but never raises."""
    now = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(replication_probe, "_now", lambda: now)

    def fake_connect(dsn, *args, **kwargs):
        if dsn == "primary":
            return _FakeConn({"pg_stat_replication": [("walreceiver", 64)]})
        return _FakeConn(
            {"pg_last_xact_replay_timestamp": [(now - timedelta(seconds=2),)]}
        )

    monkeypatch.setattr(replication_probe.psycopg, "connect", fake_connect)
    replication_probe.sample_once("primary", "replica")  # no-op providers, no raise
