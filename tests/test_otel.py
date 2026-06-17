"""Unit tests for the shared OTel bootstrap (plan tasks 1.2-1.5).

These run fully in-process: no collector, no network exporter, no running Alloy.
``configure_otel`` installs *global* trace/metric providers, and the OTel API
deliberately allows that only once per process; to keep each test isolated we
reset the module's idempotency guard and capture the providers handed to
``set_tracer_provider`` / ``set_meter_provider`` instead of mutating the real
process globals.
"""

from __future__ import annotations

import pytest

from app import otel
from app.otel import ENDPOINT_ENV_VAR, configure_otel
from opentelemetry.sdk.metrics.export import MetricExporter, MetricExportResult
from opentelemetry.sdk.resources import SERVICE_NAME
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

SERVICE = "ingestion-api"
ENDPOINT = "http://alloy:4318"


class _NoopSpanExporter(SpanExporter):
    """In-process span exporter so tests never touch the network."""

    def export(self, spans):  # noqa: D102
        return SpanExportResult.SUCCESS

    def shutdown(self):  # noqa: D102
        pass

    def force_flush(self, timeout_millis: int = 30_000) -> bool:  # noqa: D102
        return True


class _NoopMetricExporter(MetricExporter):
    """In-process metric exporter so tests never touch the network."""

    def export(self, metrics_data, timeout_millis: int = 10_000, **kwargs):  # noqa: D102
        return MetricExportResult.SUCCESS

    def force_flush(self, timeout_millis: int = 10_000) -> bool:  # noqa: D102
        return True

    def shutdown(self, timeout_millis: int = 10_000, **kwargs) -> None:  # noqa: D102
        pass


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Reset the bootstrap guard and capture the installed SDK providers.

    Avoids the OTel "providers can only be set once per process" constraint so
    every test sees a clean bootstrap and can inspect what *would* be installed,
    and swaps the real OTLP exporters for in-process no-ops so configure_otel's
    wiring is exercised without a collector or any network traffic.
    """
    monkeypatch.setattr(otel, "_configured", False)
    monkeypatch.setattr(otel, "OTLPSpanExporter", lambda *a, **k: _NoopSpanExporter())
    monkeypatch.setattr(
        otel, "OTLPMetricExporter", lambda *a, **k: _NoopMetricExporter()
    )
    box: dict = {"tracer": None, "meter": None}
    monkeypatch.setattr(
        otel.trace, "set_tracer_provider", lambda p: box.__setitem__("tracer", p)
    )
    monkeypatch.setattr(
        otel.metrics, "set_meter_provider", lambda p: box.__setitem__("meter", p)
    )
    return box


def test_configure_sets_resource_service_name(
    captured: dict, monkeypatch: pytest.MonkeyPatch
):
    """1.2 — with an endpoint set, the installed providers carry service.name."""
    monkeypatch.setenv(ENDPOINT_ENV_VAR, ENDPOINT)

    assert configure_otel(SERVICE) is True
    assert captured["tracer"] is not None
    assert captured["meter"] is not None
    assert captured["tracer"].resource.attributes[SERVICE_NAME] == SERVICE
    assert captured["meter"]._sdk_config.resource.attributes[SERVICE_NAME] == SERVICE


def test_spans_and_metrics_record_without_error(
    captured: dict, monkeypatch: pytest.MonkeyPatch
):
    """1.5 — producing a span and a metric against the providers never raises."""
    monkeypatch.setenv(ENDPOINT_ENV_VAR, ENDPOINT)

    configure_otel(SERVICE)

    tracer = captured["tracer"].get_tracer(__name__)
    with tracer.start_as_current_span("unit-span") as span:
        span.set_attribute("k", "v")

    meter = captured["meter"].get_meter(__name__)
    counter = meter.create_counter("unit_requests_total")
    counter.add(1, {"route": "/telemetry"})


def test_no_endpoint_is_a_safe_noop(
    captured: dict, monkeypatch: pytest.MonkeyPatch
):
    """1.3 — with no endpoint set, nothing is installed and nothing raises."""
    monkeypatch.delenv(ENDPOINT_ENV_VAR, raising=False)

    assert configure_otel(SERVICE) is False
    assert captured["tracer"] is None
    assert captured["meter"] is None


def test_empty_endpoint_is_treated_as_unset(
    captured: dict, monkeypatch: pytest.MonkeyPatch
):
    """1.3 — a blank/whitespace endpoint degrades to the no-op path."""
    monkeypatch.setenv(ENDPOINT_ENV_VAR, "   ")

    assert configure_otel(SERVICE) is False
    assert captured["tracer"] is None


def test_configure_is_idempotent(
    captured: dict, monkeypatch: pytest.MonkeyPatch
):
    """1.3 — a second call is a no-op and installs no second provider."""
    monkeypatch.setenv(ENDPOINT_ENV_VAR, ENDPOINT)

    assert configure_otel(SERVICE) is True
    first_tracer = captured["tracer"]

    assert configure_otel(SERVICE) is False
    # The captured provider is unchanged — no conflicting second install.
    assert captured["tracer"] is first_tracer


def test_instrument_fastapi_app_is_callable_without_endpoint(
    monkeypatch: pytest.MonkeyPatch,
):
    """1.4 — a service can instrument its app even with no endpoint set."""
    monkeypatch.delenv(ENDPOINT_ENV_VAR, raising=False)
    from fastapi import FastAPI

    app = FastAPI()
    # Must not raise; instrumentation hangs off the default API providers.
    otel.instrument_fastapi_app(app)
