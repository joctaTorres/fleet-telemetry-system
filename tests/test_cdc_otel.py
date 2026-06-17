"""Unit tests for the CDC consumer's OTel wiring (plan tasks 1, 4, 6, 7).

These run fully in-process: no collector, no running Alloy, no Postgres, and no
real Redis. They drive :meth:`CdcConsumer._emit` directly with a hand-built
relation cache and a fake Redis to assert the consumer records its event counter
and decode-lag instrument and emits a publish span on a watched change — and
records/publishes nothing on a non-watched change — all against an in-memory
meter and tracer, with the no-op path staying green.
"""

from __future__ import annotations

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from opentelemetry.trace import SpanKind

from app import cdc_consumer
from app.cdc import TABLE_EVENT_TYPES
from app.events import EVENT_CHANNEL


class _FakeRedis:
    """Captures ``publish`` calls so a watched change can be asserted offline."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    def publish(self, channel: str, message: str) -> None:
        self.published.append((channel, message))


def _consumer_with(relname: str) -> tuple[cdc_consumer.CdcConsumer, _FakeRedis]:
    """Build a consumer whose OID 1 maps to ``relname`` with a fake Redis."""
    consumer = cdc_consumer.CdcConsumer()
    consumer._relations = {1: (relname, ["vehicle_id", "status", "battery_pct"])}
    redis = _FakeRedis()
    consumer._redis = redis
    return consumer, redis


def _watched_values() -> dict[str, object]:
    return {"vehicle_id": "v-1", "status": "active", "battery_pct": "55.0"}


def test_run_forever_installs_otel_through_bootstrap(monkeypatch):
    """1 — process startup calls configure_otel("cdc-consumer") via the bootstrap.

    ``run_forever`` is stubbed to stop immediately after the bootstrap call so no
    Postgres/Redis connection is attempted; we only assert the service name wired.
    """
    assert cdc_consumer.SERVICE_NAME == "cdc-consumer"

    calls: dict = {}
    monkeypatch.setattr(
        cdc_consumer, "configure_otel", lambda name: calls.setdefault("name", name)
    )
    # Make the readiness probe drop straight out of the loop after the bootstrap.
    monkeypatch.setattr(cdc_consumer, "_publication_exists", lambda: False)
    import threading

    stop = threading.Event()
    stop.set()  # loop exits on the first check, after configure_otel has run
    cdc_consumer.run_forever(stop)

    assert calls["name"] == "cdc-consumer"


def _record_point(reader: InMemoryMetricReader, name: str):
    """Return the single number data point recorded for metric ``name``, or None."""
    data = reader.get_metrics_data()
    if data is None:  # nothing has been recorded on any instrument yet
        return None
    for resource_metric in data.resource_metrics:
        for scope_metric in resource_metric.scope_metrics:
            for metric in scope_metric.metrics:
                if metric.name == name:
                    points = list(metric.data.data_points)
                    return points[0] if points else None
    return None


def test_watched_change_records_metrics_and_publish_span(monkeypatch):
    """4/7 — a watched change ticks the counter + decode-lag and emits spans."""
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader]).get_meter("test")
    monkeypatch.setattr(
        cdc_consumer, "EVENT_COUNTER", meter.create_counter("cdc.events_published")
    )
    monkeypatch.setattr(
        cdc_consumer, "DECODE_LAG", meter.create_histogram("cdc.decode.lag")
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(cdc_consumer, "_tracer", provider.get_tracer("test"))

    consumer, redis = _consumer_with("vehicle_current_state")
    consumer._emit(1, _watched_values())

    # The event was published exactly once on the events channel.
    assert len(redis.published) == 1
    channel, _message = redis.published[0]
    assert channel == EVENT_CHANNEL

    event_type = TABLE_EVENT_TYPES["vehicle_current_state"]
    count_point = _record_point(reader, "cdc.events_published")
    assert count_point is not None
    assert count_point.value == 1
    assert count_point.attributes["cdc.event_type"] == event_type

    lag_point = _record_point(reader, "cdc.decode.lag")
    assert lag_point is not None
    assert lag_point.count == 1
    assert lag_point.attributes["cdc.event_type"] == event_type

    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert "cdc.decode" in spans and "cdc.publish" in spans
    # PRODUCER kind so Tempo's service-graphs processor pairs it with the
    # frontend's CONSUMER subscribe span and draws the pub/sub edge.
    assert spans["cdc.publish"].kind is SpanKind.PRODUCER
    assert spans["cdc.publish"].attributes["messaging.system"] == "redis"
    # The publish span shares the decode span's trace (the traceparent seam).
    assert (
        spans["cdc.publish"].context.trace_id
        == spans["cdc.decode"].context.trace_id
    )
    assert spans["cdc.publish"].parent.span_id == spans["cdc.decode"].context.span_id
    assert spans["cdc.publish"].attributes["cdc.event_type"] == event_type


def test_non_watched_change_records_nothing(monkeypatch):
    """6/7 — a change to an unwatched table publishes nothing, records nothing."""
    reader = InMemoryMetricReader()
    meter = MeterProvider(metric_readers=[reader]).get_meter("test")
    monkeypatch.setattr(
        cdc_consumer, "EVENT_COUNTER", meter.create_counter("cdc.events_published")
    )
    monkeypatch.setattr(
        cdc_consumer, "DECODE_LAG", meter.create_histogram("cdc.decode.lag")
    )

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(cdc_consumer, "_tracer", provider.get_tracer("test"))

    consumer, redis = _consumer_with("audit_log")  # not in TABLE_EVENT_TYPES
    consumer._emit(1, _watched_values())

    assert redis.published == []  # behaves exactly as before: nothing published
    assert _record_point(reader, "cdc.events_published") is None
    assert _record_point(reader, "cdc.decode.lag") is None
    assert exporter.get_finished_spans() == ()


def test_instruments_are_module_level_off_the_bootstrap_meter():
    """1/4 — counter + decode-lag come from the shared bootstrap meter, swappable."""
    assert isinstance(
        cdc_consumer._meter, type(cdc_consumer.metrics.get_meter(__name__))
    )
    assert cdc_consumer.EVENT_COUNTER is not None
    assert cdc_consumer.DECODE_LAG is not None


def test_emit_is_a_no_op_path_without_a_collector():
    """6 — with the default (no-op) providers, _emit publishes but never raises."""
    consumer, redis = _consumer_with("vehicle_current_state")
    consumer._emit(1, _watched_values())  # must not raise against no-op providers
    assert len(redis.published) == 1
