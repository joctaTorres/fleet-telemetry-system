"""Shared OpenTelemetry bootstrap for the fleet services.

A single, reusable entry point that turns an OTLP endpoint + a service name into
installed global trace/metric providers. Every instrumented service (ingestion
API now, frontend API and CDC consumer later) calls :func:`configure_otel` once
at startup and :func:`instrument_fastapi_app` to wire its FastAPI app — neither
service re-wires the SDK itself.

Configuration comes from the environment, mirroring the ``DATABASE_URL`` /
``REDIS_URL`` convention in :mod:`app.config`: the OTLP collector endpoint is
read from ``OTEL_EXPORTER_OTLP_ENDPOINT`` and ``service.name`` is passed
explicitly by the caller. The transport is OTLP over HTTP/protobuf (Alloy port
4318) so the Python services and the later browser SDK speak the same protocol.

Safe by default: when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is unset the bootstrap is
a no-op — no exporter is installed, nothing raises, and no collector is
required. This keeps pytest and plain local boot green. The call is also
idempotent: a second invocation does not install a second conflicting provider.
"""

from __future__ import annotations

import os

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
    OTLPMetricExporter,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

ENDPOINT_ENV_VAR = "OTEL_EXPORTER_OTLP_ENDPOINT"

# Guards against double-installation. ``configure_otel`` may be reached more than
# once (import-time + app startup, or in tests); the SDK warns and ignores a
# second ``set_*_provider``, so we short-circuit instead.
_configured = False


def _endpoint() -> str:
    """Return the trimmed OTLP endpoint, or an empty string when unset."""
    return os.environ.get(ENDPOINT_ENV_VAR, "").strip()


def configure_otel(service_name: str) -> bool:
    """Install global trace/metric providers exporting to OTLP/HTTP.

    Builds a :class:`Resource` carrying ``service.name`` and installs a global
    :class:`TracerProvider` (with a :class:`BatchSpanProcessor` over the
    OTLP/HTTP span exporter) and a global :class:`MeterProvider` (with a
    :class:`PeriodicExportingMetricReader` over the OTLP/HTTP metric exporter).
    The collector endpoint is read from ``OTEL_EXPORTER_OTLP_ENDPOINT``.

    Returns ``True`` when providers were installed, ``False`` when the call was a
    no-op. It is a no-op — never raising, never requiring a collector — when
    ``OTEL_EXPORTER_OTLP_ENDPOINT`` is unset, leaving the default no-op-ish API
    providers in place so ``trace.get_tracer`` / ``metrics.get_meter`` still
    return usable objects. The call is idempotent: a second invocation returns
    ``False`` without installing a conflicting provider.
    """
    global _configured
    if _configured:
        return False

    endpoint = _endpoint()
    if not endpoint:
        # Safe-by-default: no collector, no exporter, nothing installed. Mark as
        # configured so a later call stays a no-op rather than racing the
        # endpoint becoming set mid-process.
        _configured = True
        return False

    resource = Resource.create({SERVICE_NAME: service_name})

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tracer_provider)

    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())],
    )
    metrics.set_meter_provider(meter_provider)

    _configured = True
    return True


def instrument_fastapi_app(app: object) -> None:
    """Instrument a FastAPI app for request spans in one call.

    Wraps :meth:`FastAPIInstrumentor.instrument_app`. Safe to call whether or
    not an OTLP endpoint is set: with no endpoint the instrumentation hangs off
    the default API providers and produces no exported spans, so a service can
    call this unconditionally at startup.
    """
    # Imported lazily so importing this module never pulls in the ASGI
    # instrumentation machinery unless a service actually instruments an app.
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)
