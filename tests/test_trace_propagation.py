"""Unit tests for the W3C trace-context propagation across the Redis hop (task 6).

These run fully in-process: no collector, no running Alloy, no Postgres, and no
real Redis. They drive the publisher seam (:meth:`CdcConsumer._emit`) and the
subscriber seam (:func:`app.frontend_api._fan_out`) directly against in-memory
tracers and a fake Redis / fake WebSocket, asserting the round-trip:

* the cdc-consumer injects a valid ``traceparent`` reflecting its ``cdc.publish``
  span into the envelope's reserved carrier;
* the frontend extracts that context and starts its ``redis.subscribe`` span as a
  child of the publish span (same trace id), with the ``ws.broadcast`` span nested
  under it — so a single trace spans both services;
* a carrier-less (or non-JSON) envelope still extracts cleanly and is still
  broadcast, and the carrier is stripped from the message clients receive.
"""

from __future__ import annotations

import asyncio
import json

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from app import cdc_consumer, frontend_api
from app.events import (
    EVENT_CHANNEL,
    TRACE_CONTEXT_KEY,
    extract_trace_context,
    inject_trace_context,
)


def _in_memory_tracer() -> tuple[InMemorySpanExporter, object]:
    """Return ``(exporter, tracer)`` over a recording in-memory TracerProvider."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter, provider.get_tracer("test")


def _span(exporter: InMemorySpanExporter, name: str):
    """Return the single finished span named ``name`` (or None)."""
    for span in exporter.get_finished_spans():
        if span.name == name:
            return span
    return None


def _traceparent_ids(carrier: dict) -> tuple[int, int]:
    """Parse ``(trace_id, parent_span_id)`` out of a W3C ``traceparent`` carrier."""
    _ver, trace_hex, span_hex, _flags = carrier["traceparent"].split("-")
    return int(trace_hex, 16), int(span_hex, 16)


class _FakeRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    def publish(self, channel: str, message: str) -> None:
        self.published.append((channel, message))


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, message: str) -> None:
        self.sent.append(message)


def _watched_consumer() -> tuple[cdc_consumer.CdcConsumer, _FakeRedis]:
    consumer = cdc_consumer.CdcConsumer()
    consumer._relations = {1: ("vehicle_current_state", ["vehicle_id", "status", "battery_pct"])}
    redis = _FakeRedis()
    consumer._redis = redis
    return consumer, redis


def _watched_values() -> dict[str, object]:
    return {"vehicle_id": "v-1", "status": "active", "battery_pct": "55.0"}


def test_emit_injects_traceparent_reflecting_the_publish_span(monkeypatch):
    """Inject writes a valid traceparent that points at the cdc.publish span."""
    exporter, tracer = _in_memory_tracer()
    monkeypatch.setattr(cdc_consumer, "_tracer", tracer)

    consumer, redis = _watched_consumer()
    consumer._emit(1, _watched_values())

    assert len(redis.published) == 1
    channel, message = redis.published[0]
    assert channel == EVENT_CHANNEL
    envelope = json.loads(message)
    # The carrier rides in the envelope, distinct from the unchanged application
    # contract fields.
    assert TRACE_CONTEXT_KEY in envelope
    assert set(envelope) >= {"type", "payload", TRACE_CONTEXT_KEY}
    assert "traceparent" in envelope[TRACE_CONTEXT_KEY]

    publish_span = _span(exporter, "cdc.publish")
    assert publish_span is not None
    trace_id, parent_id = _traceparent_ids(envelope[TRACE_CONTEXT_KEY])
    # The propagated traceparent reflects the publish span exactly.
    assert trace_id == publish_span.context.trace_id
    assert parent_id == publish_span.context.span_id


def test_subscriber_reparents_subscribe_span_on_the_injected_trace(monkeypatch):
    """Extract re-parents the subscribe span onto the publish span's trace."""
    # Build the publisher side: inject the active publish span into an envelope.
    pub_exporter, pub_tracer = _in_memory_tracer()
    with pub_tracer.start_as_current_span("cdc.publish") as publish_span:
        envelope = inject_trace_context(
            {"type": "vehicle_state_changed", "payload": {"vehicle_id": "v-1"}}
        )
    injected_trace_id = publish_span.context.trace_id
    injected_span_id = publish_span.context.span_id
    assert TRACE_CONTEXT_KEY in envelope

    # Subscriber side: a fresh in-memory tracer + isolated registry with one client.
    sub_exporter, sub_tracer = _in_memory_tracer()
    monkeypatch.setattr(frontend_api, "_tracer", sub_tracer)
    registry = frontend_api.ConnectionRegistry()
    monkeypatch.setattr(frontend_api, "registry", registry)
    ws = _FakeWS()

    async def scenario():
        await registry.add(ws)
        await frontend_api._fan_out(json.dumps(envelope))

    asyncio.run(scenario())

    subscribe_span = _span(sub_exporter, "redis.subscribe")
    broadcast_span = _span(sub_exporter, "ws.broadcast")
    assert subscribe_span is not None and broadcast_span is not None
    # The subscribe span joined the publish span's trace, parented on it.
    assert subscribe_span.context.trace_id == injected_trace_id
    assert subscribe_span.parent is not None
    assert subscribe_span.parent.span_id == injected_span_id
    # The broadcast span is a child of the subscribe span (one connected chain).
    assert broadcast_span.context.trace_id == injected_trace_id
    assert broadcast_span.parent.span_id == subscribe_span.context.span_id

    # The carrier is stripped: the client receives the verbatim {type, payload}.
    assert len(ws.sent) == 1
    forwarded = json.loads(ws.sent[0])
    assert forwarded == {"type": "vehicle_state_changed", "payload": {"vehicle_id": "v-1"}}
    assert TRACE_CONTEXT_KEY not in forwarded


def test_carrierless_envelope_still_broadcasts_verbatim(monkeypatch):
    """An envelope with no carrier extracts to an empty context and still fans out."""
    sub_exporter, sub_tracer = _in_memory_tracer()
    monkeypatch.setattr(frontend_api, "_tracer", sub_tracer)
    registry = frontend_api.ConnectionRegistry()
    monkeypatch.setattr(frontend_api, "registry", registry)
    ws = _FakeWS()

    raw = json.dumps({"type": "zone_count_changed", "payload": {"zone_id": "z-1"}})

    async def scenario():
        await registry.add(ws)
        await frontend_api._fan_out(raw)

    asyncio.run(scenario())

    # Delivered byte-for-byte (no carrier => no re-serialize).
    assert ws.sent == [raw]
    subscribe_span = _span(sub_exporter, "redis.subscribe")
    assert subscribe_span is not None
    # No remote parent: a carrier-less event starts a fresh root subscribe span.
    assert subscribe_span.parent is None


def test_fan_out_forwards_non_json_verbatim_without_raising(monkeypatch):
    """A non-JSON payload is forwarded verbatim and never breaks the fan-out."""
    _exporter, tracer = _in_memory_tracer()
    monkeypatch.setattr(frontend_api, "_tracer", tracer)
    registry = frontend_api.ConnectionRegistry()
    monkeypatch.setattr(frontend_api, "registry", registry)
    ws = _FakeWS()

    async def scenario():
        await registry.add(ws)
        await frontend_api._fan_out("not-json")  # must not raise

    asyncio.run(scenario())
    assert ws.sent == ["not-json"]


def test_inject_extract_round_trip_shares_the_trace():
    """The events helpers round-trip the trace id with no running collector."""
    _exporter, tracer = _in_memory_tracer()
    with tracer.start_as_current_span("publish") as span:
        envelope = inject_trace_context({"type": "x", "payload": {}})
        expected = span.context.trace_id
    ctx = extract_trace_context(envelope)
    from opentelemetry.trace import get_current_span

    extracted = get_current_span(ctx).get_span_context()
    assert extracted.trace_id == expected
    assert extracted.is_remote


def test_inject_is_a_no_op_without_a_recording_span():
    """With no active recording span the envelope stays {type, payload}."""
    # No tracer started => the current span is the non-recording default; inject
    # must not attach a carrier, so the no-op path leaves the contract untouched.
    envelope = inject_trace_context({"type": "x", "payload": {"a": 1}})
    assert TRACE_CONTEXT_KEY not in envelope
    # And extracting from a carrier-less envelope yields an empty context.
    assert extract_trace_context(envelope) is not None
