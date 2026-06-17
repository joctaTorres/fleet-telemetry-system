"""Unit tests for the frontend API's OTel wiring (plan tasks 1.1-1.3, 1.6, 1.7).

These run fully in-process: no collector, no running Alloy, and — by exercising
only the 422 rejection path, which FastAPI returns before any route handler runs
— no database either. They assert the app installs OTel through the shared
``app.otel`` bootstrap and that its explicit request metric is recorded with the
HTTP method, route, and response status as attributes, including the error path.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import frontend_api
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import SpanKind


def test_app_is_instrumented_through_bootstrap():
    """1.2 — the real frontend app was instrumented via the shared helper."""
    assert getattr(frontend_api.app, "_is_instrumented_by_opentelemetry", False)


def test_install_observability_uses_bootstrap_with_service_name(
    monkeypatch: pytest.MonkeyPatch,
):
    """1.1/1.2 — wiring calls configure_otel("frontend-api") + the FastAPI helper."""
    assert frontend_api.SERVICE_NAME == "frontend-api"

    calls: dict = {}
    monkeypatch.setattr(
        frontend_api, "configure_otel", lambda name: calls.setdefault("name", name)
    )
    monkeypatch.setattr(
        frontend_api,
        "instrument_fastapi_app",
        lambda app: calls.setdefault("app", app),
    )

    test_app = FastAPI()
    frontend_api.install_observability(test_app)

    assert calls["name"] == "frontend-api"
    assert calls["app"] is test_app


def _record_point(reader: InMemoryMetricReader, name: str):
    """Return the single number data point recorded for metric ``name``, or None."""
    data = reader.get_metrics_data()
    for resource_metric in data.resource_metrics:
        for scope_metric in resource_metric.scope_metrics:
            for metric in scope_metric.metrics:
                if metric.name == name:
                    points = list(metric.data.data_points)
                    return points[0] if points else None
    return None


def test_request_metric_records_method_route_and_status(
    monkeypatch: pytest.MonkeyPatch,
):
    """1.3 — a rejected (422) GET is counted with method/route/status, no DB.

    ``GET /anomalies`` requires ``vehicle_id``/``since``/``until`` query params;
    omitting them is rejected with 422 by validation before the handler (and thus
    any replica read) runs, exercising the error series without a database.
    """
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    meter = provider.get_meter("test")
    monkeypatch.setattr(
        frontend_api, "REQUEST_COUNTER", meter.create_counter("frontend.requests")
    )
    monkeypatch.setattr(
        frontend_api,
        "REQUEST_DURATION",
        meter.create_histogram("frontend.request.duration"),
    )

    client = TestClient(frontend_api.app)
    resp = client.get("/anomalies")  # missing required query params
    assert resp.status_code == 422  # validation rejects before any handler/DB call

    count_point = _record_point(reader, "frontend.requests")
    assert count_point is not None
    assert count_point.value == 1
    assert count_point.attributes["http.method"] == "GET"
    assert count_point.attributes["http.route"] == "/anomalies"
    assert count_point.attributes["http.status_code"] == 422

    duration_point = _record_point(reader, "frontend.request.duration")
    assert duration_point is not None
    assert duration_point.count == 1
    assert duration_point.attributes["http.status_code"] == 422


def _in_memory_meter():
    """Return ``(reader, meter)`` over a self-contained in-memory MeterProvider."""
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    return reader, provider.get_meter("test")


def test_active_connections_gauge_tracks_registry_membership():
    """1.5 — the gauge reads the live registry size: up, down, zero, no collector.

    Builds the same observable gauge the module installs, but bound to an isolated
    registry and an in-memory meter, then drives connect/disconnect through the
    real ``add``/``remove`` API and asserts each collection observes the current
    membership — so the gauge can never diverge from the live connection set.
    """
    reader, meter = _in_memory_meter()
    registry = frontend_api.ConnectionRegistry()
    meter.create_observable_gauge(
        "frontend.ws.active_connections",
        callbacks=[frontend_api._make_active_connections_callback(registry)],
    )

    def gauge_value():
        point = _record_point(reader, "frontend.ws.active_connections")
        return None if point is None else point.value

    async def scenario():
        assert gauge_value() == 0  # zero when empty
        ws_a, ws_b = object(), object()
        await registry.add(ws_a)
        await registry.add(ws_b)
        assert gauge_value() == 2  # up on connect
        await registry.remove(ws_a)
        assert gauge_value() == 1  # down on disconnect
        await registry.remove(ws_b)
        assert gauge_value() == 0  # back to zero

    asyncio.run(scenario())


def test_broadcast_counter_increments_once_per_fanout(monkeypatch):
    """1.5 — the broadcast counter ticks once per fan-out, delivery intact."""
    reader, meter = _in_memory_meter()
    monkeypatch.setattr(
        frontend_api,
        "BROADCAST_COUNTER",
        meter.create_counter("frontend.ws.broadcasts"),
    )
    registry = frontend_api.ConnectionRegistry()

    sent: list[str] = []

    class FakeWS:
        async def send_text(self, message: str) -> None:
            sent.append(message)

    async def scenario():
        await registry.add(FakeWS())
        await registry.broadcast("patch-1")
        await registry.broadcast("patch-2")

    asyncio.run(scenario())

    point = _record_point(reader, "frontend.ws.broadcasts")
    assert point is not None
    assert point.value == 2  # once per broadcast call
    assert sent == ["patch-1", "patch-2"]  # delivery unchanged


def test_broadcast_drops_dead_client_and_still_counts(monkeypatch):
    """1.5 — a send error drops only that client; the fan-out is still counted."""
    reader, meter = _in_memory_meter()
    monkeypatch.setattr(
        frontend_api,
        "BROADCAST_COUNTER",
        meter.create_counter("frontend.ws.broadcasts"),
    )
    registry = frontend_api.ConnectionRegistry()

    live_sent: list[str] = []

    class LiveWS:
        async def send_text(self, message: str) -> None:
            live_sent.append(message)

    class DeadWS:
        async def send_text(self, message: str) -> None:
            raise RuntimeError("client gone")

    live = LiveWS()

    async def scenario():
        await registry.add(live)
        await registry.add(DeadWS())
        await registry.broadcast("patch")

    asyncio.run(scenario())

    # The dead client was dropped; only the live one remains and received it.
    assert registry.size() == 1
    assert live_sent == ["patch"]
    point = _record_point(reader, "frontend.ws.broadcasts")
    assert point is not None and point.value == 1


def _in_memory_tracer():
    """Return ``(exporter, tracer)`` over a self-contained in-memory provider."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter, provider.get_tracer("test")


def test_redis_subscribe_span_is_consumer_kind(monkeypatch):
    """Service-graph — the subscribe span is CONSUMER so Tempo pairs it with the
    cdc-consumer's PRODUCER publish span and draws the pub/sub edge."""
    exporter, tracer = _in_memory_tracer()
    monkeypatch.setattr(frontend_api, "_tracer", tracer)

    asyncio.run(frontend_api._fan_out('{"type": "vehicle_state_changed"}'))

    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert "redis.subscribe" in spans
    assert spans["redis.subscribe"].kind is SpanKind.CONSUMER
    assert spans["redis.subscribe"].attributes["messaging.system"] == "redis"


def test_replica_read_span_is_client_kind_with_db_peer(monkeypatch):
    """Service-graph — the replica read is a CLIENT span with a db.system + peer
    server.address so Tempo forms a virtual db node and draws frontend-api ->
    replica."""
    exporter, tracer = _in_memory_tracer()
    monkeypatch.setattr(frontend_api, "_tracer", tracer)
    monkeypatch.setattr(frontend_api, "current_vehicle_states", lambda conn: [])
    monkeypatch.setattr(frontend_api, "replica_connection", object(), raising=False)

    assert frontend_api.get_vehicles() == []

    spans = {span.name: span for span in exporter.get_finished_spans()}
    span = spans["replica.read vehicle_current_state"]
    assert span.kind is SpanKind.CLIENT
    assert span.attributes["db.system"] == "postgresql"
    assert span.attributes["server.address"] == "replica"


def test_ws_instruments_are_a_no_op_without_a_collector():
    """1.6 — with no OTLP endpoint the gauge/counter never raise and stay green.

    The module-level instruments hang off the default (no-op) providers when
    ``OTEL_EXPORTER_OTLP_ENDPOINT`` is unset, so size reads and broadcasts work
    without any running collector.
    """
    registry = frontend_api.ConnectionRegistry()

    async def scenario():
        assert registry.size() == 0
        await registry.broadcast("nobody-home")  # no clients, real no-op counter

    asyncio.run(scenario())  # must not raise
