"""Real-time event channel contract.

A single Redis pub/sub channel carries every derived state patch as a JSON
envelope: a ``type`` — one of the three watched-table derivations — plus its
``payload``. The frontend API subscribes to this channel and forwards each
message verbatim to connected WebSocket clients; the ``cdc-consumer`` follow-on
is what *produces* these messages from the WAL. Defined in one place so the
publisher (CDC, later) and the subscriber (the frontend, here) share the
contract.

The envelope also reserves one out-of-band field, :data:`TRACE_CONTEXT_KEY`, for
the W3C trace context (``traceparent``/``tracestate``). It is transport metadata,
never application data: the cdc-consumer **injects** the active context into it at
its Redis publish seam and the frontend **extracts** it on subscribe to re-parent
its spans, joining the two services into one distributed trace across the pub/sub
hop. The carrier is stripped before a message reaches browser clients, so the
client-visible ``{type, payload}`` contract is unchanged. The inject/extract
helpers and the single :class:`TraceContextTextMapPropagator` wiring live here so
both services share one propagator and the carrier contract stays in one place.
"""

from __future__ import annotations

from opentelemetry.context import Context
from opentelemetry.trace.propagation.tracecontext import (
    TraceContextTextMapPropagator,
)

#: The single Redis pub/sub channel carrying all derived state patches.
EVENT_CHANNEL = "fleet:events"

#: The three event types, one per watched table.
VEHICLE_STATE_CHANGED = "vehicle_state_changed"
ANOMALY_DETECTED = "anomaly_detected"
ZONE_COUNT_CHANGED = "zone_count_changed"

#: Envelope ``type`` used for the one-shot snapshot sent on WebSocket connect.
SNAPSHOT = "snapshot"

#: Every valid delta event type. The snapshot type is intentionally excluded —
#: it is produced by the frontend on connect, never published on the channel.
EVENT_TYPES = frozenset(
    {VEHICLE_STATE_CHANGED, ANOMALY_DETECTED, ZONE_COUNT_CHANGED}
)

#: Reserved out-of-band envelope key carrying the W3C trace context for the one
#: Redis pub/sub hop. A nested object (a plain string->string map the standard
#: propagator writes ``traceparent``/``tracestate`` into), distinct from ``type``
#: and ``payload`` so the application contract is untouched. Stripped from the
#: envelope before the message is forwarded to WebSocket clients.
TRACE_CONTEXT_KEY = "_trace"

#: One shared W3C propagator (``traceparent``/``tracestate``) over a plain dict
#: carrier — no custom header format — used by both the publisher's inject and the
#: subscriber's extract. With no OTLP endpoint installed the active context holds
#: only the no-op span, so inject writes nothing and the round-trip is a harmless
#: no-op.
_PROPAGATOR = TraceContextTextMapPropagator()


def inject_trace_context(envelope: dict) -> dict:
    """Inject the active trace context into ``envelope``'s reserved carrier.

    Writes the W3C ``traceparent``/``tracestate`` for the currently-active span
    into a fresh dict stored under :data:`TRACE_CONTEXT_KEY`, so a subscriber can
    re-parent its spans onto this trace. Only attaches the carrier when the
    propagator actually produced one (a real, recording span), so the no-op path
    leaves the envelope exactly ``{type, payload}``. Defensive by contract:
    mutates and returns ``envelope`` in place and never raises, so a propagation
    error can never gate the publish at the call site.
    """
    try:
        carrier: dict[str, str] = {}
        _PROPAGATOR.inject(carrier)
        if carrier:
            envelope[TRACE_CONTEXT_KEY] = carrier
    except Exception:  # noqa: BLE001 - inject must never gate the publish
        pass
    return envelope


def extract_trace_context(envelope: dict) -> Context:
    """Extract the remote trace context carried in ``envelope``, if any.

    Returns a :class:`Context` suitable as the ``context=`` parent for a new span,
    reconstructed from the W3C carrier under :data:`TRACE_CONTEXT_KEY`. An envelope
    with no carrier (a legacy/un-instrumented publisher, or the no-op path) yields
    an empty context — the span simply starts a new root — so a carrier-less event
    is still handled and broadcast. Never raises.
    """
    try:
        carrier = envelope.get(TRACE_CONTEXT_KEY)
        if isinstance(carrier, dict):
            return _PROPAGATOR.extract(carrier, context=Context())
    except Exception:  # noqa: BLE001 - extract must never break fan-out
        pass
    return Context()


def strip_trace_context(envelope: dict) -> dict:
    """Remove the trace-context carrier so the forwarded message is unchanged.

    Pops :data:`TRACE_CONTEXT_KEY` if present and returns ``envelope`` in place, so
    the message fanned out to WebSocket clients keeps exactly the ``{type, payload}``
    shape — the trace context is transport metadata, never client-visible data.
    """
    envelope.pop(TRACE_CONTEXT_KEY, None)
    return envelope
