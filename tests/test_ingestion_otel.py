"""Unit tests for the ingestion API's OTel wiring (plan tasks 4.1–4.3, 4.6).

These run fully in-process: no collector, no running Alloy, and — by exercising
only the 422 rejection path, which FastAPI returns before any route handler runs
— no database either. They assert the app installs OTel through the shared
``app.otel`` bootstrap and that its explicit request metric is recorded with the
HTTP method, route, and response status as attributes.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import ingestion_api
from app.models import TelemetryEvent
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import SpanKind


def test_app_is_instrumented_through_bootstrap():
    """4.2 — the real ingestion app was instrumented via the shared helper."""
    assert getattr(ingestion_api.app, "_is_instrumented_by_opentelemetry", False)


def test_install_observability_uses_bootstrap_with_service_name(
    monkeypatch: pytest.MonkeyPatch,
):
    """4.1/4.2 — wiring calls configure_otel("ingestion-api") + the FastAPI helper."""
    assert ingestion_api.SERVICE_NAME == "ingestion-api"

    calls: dict = {}
    monkeypatch.setattr(
        ingestion_api, "configure_otel", lambda name: calls.setdefault("name", name)
    )
    monkeypatch.setattr(
        ingestion_api,
        "instrument_fastapi_app",
        lambda app: calls.setdefault("app", app),
    )

    test_app = FastAPI()
    ingestion_api.install_observability(test_app)

    assert calls["name"] == "ingestion-api"
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
    """4.3 — a rejected (422) POST is counted with method/route/status, no DB."""
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    meter = provider.get_meter("test")
    monkeypatch.setattr(
        ingestion_api, "REQUEST_COUNTER", meter.create_counter("ingestion.requests")
    )
    monkeypatch.setattr(
        ingestion_api,
        "REQUEST_DURATION",
        meter.create_histogram("ingestion.request.duration"),
    )

    client = TestClient(ingestion_api.app)
    resp = client.post("/telemetry", json={"not": "a valid telemetry event"})
    assert resp.status_code == 422  # validation rejects before any handler/DB call

    count_point = _record_point(reader, "ingestion.requests")
    assert count_point is not None
    assert count_point.value == 1
    assert count_point.attributes["http.method"] == "POST"
    assert count_point.attributes["http.route"] == "/telemetry"
    assert count_point.attributes["http.status_code"] == 422

    duration_point = _record_point(reader, "ingestion.request.duration")
    assert duration_point is not None
    assert duration_point.count == 1
    assert duration_point.attributes["http.status_code"] == 422


def test_persist_is_wrapped_in_a_client_db_span(monkeypatch: pytest.MonkeyPatch):
    """Service-graph — the persist seam emits a CLIENT span with db.system + a
    peer server.address so Tempo forms a virtual db node and draws the
    ingestion-api -> db edge. ``persist_telemetry`` is stubbed: no real Postgres."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(ingestion_api, "_tracer", provider.get_tracer("test"))

    persisted: list = []
    monkeypatch.setattr(
        ingestion_api, "persist_telemetry", lambda event: persisted.append(event)
    )

    event = TelemetryEvent(vehicle_id="v-1", status="moving", battery_pct=55.0)
    assert ingestion_api.post_telemetry(event) == {"status": "accepted"}
    assert len(persisted) == 1  # the wrapped write still ran exactly once

    spans = {span.name: span for span in exporter.get_finished_spans()}
    span = spans["db.write vehicle_current_state"]
    assert span.kind is SpanKind.CLIENT
    assert span.attributes["db.system"] == "postgresql"
    assert span.attributes["server.address"] == "db"
