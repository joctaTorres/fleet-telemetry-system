# otel-bootstrap-python

## Why

The `ingestion-trace-backbone` phase needs the ingestion API (FastAPI) to emit OTLP
traces and custom request metrics to Alloy. Before any service can be instrumented, the
project needs the OpenTelemetry Python SDK on the dependency graph and a single, reusable
bootstrap that turns an OTLP endpoint + service name into installed trace/metric providers.

This change owns that foundation. It is the first change in the phase and has no `after`
dependency: it adds the OTel dependencies and a shared `app.otel` module. The downstream
`instrument-ingestion-api` change consumes it (`after: [otel-bootstrap-python,
observability-stack-compose]`) by calling one entry point from the ingestion app, and the
later frontend/CDC instrumentation reuses the same module rather than re-wiring the SDK.

## What Changes

- Add the OpenTelemetry Python runtime dependencies to `pyproject.toml`:
  the SDK, the OTLP/HTTP exporter, and the FastAPI instrumentation.
- Add `app/otel.py`, a shared bootstrap module exposing:
  - `configure_otel(service_name)` — build a `Resource` carrying `service.name`, install a
    global `TracerProvider` (with a `BatchSpanProcessor` over the OTLP/HTTP span exporter)
    and a global `MeterProvider` (with a `PeriodicExportingMetricReader` over the OTLP/HTTP
    metric exporter), reading the endpoint from the environment.
  - a small FastAPI instrumentation helper (wrapping
    `FastAPIInstrumentor.instrument_app`) so a service instruments its app in one call.
- Make configuration **safe and idempotent**: when `OTEL_EXPORTER_OTLP_ENDPOINT` is unset
  the call is a no-op (tracers/meters still resolve to working no-op-or-default providers,
  nothing raises, no collector required), and calling `configure_otel` twice does not
  install a second conflicting provider.
- Add unit tests for the module's behavior (in-process, no running collector).

## Design

- **Vertical-slice scope.** This is the thinnest reusable instrumentation foundation that
  the phase needs — dependencies + one bootstrap module + tests. It does **not** wire the
  ingestion API (that is `instrument-ingestion-api`), does **not** touch `docker-compose.yml`
  or stand up Alloy/Tempo/Prometheus/Grafana (that is `observability-stack-compose` /
  `grafana-provisioning`), and does **not** add dashboards. It proves only that the module
  configures providers correctly and degrades safely.
- **Config from the environment, like `app.config`.** The OTLP endpoint comes from
  `OTEL_EXPORTER_OTLP_ENDPOINT`; `service.name` is passed explicitly by the caller (and
  reflected as a resource attribute). No endpoint or credentials are hard-coded — mirroring
  the existing `DATABASE_URL`/`REDIS_URL` convention in `app/config.py`.
- **Transport: OTLP over HTTP/protobuf (Alloy port 4318).** Chosen over gRPC so the Python
  services and the later browser SDK (`@opentelemetry/sdk-trace-web`, OTLP/HTTP) speak the
  same protocol to Alloy, and to avoid a grpc runtime dependency. The exact endpoint URL
  the ingestion service points at is set by compose env in the downstream change.
- **Safe-by-default.** No collector runs during pytest or plain local boot, so a missing
  `OTEL_EXPORTER_OTLP_ENDPOINT` must never break startup or tests. Bootstrap is a no-op in
  that case; instrumentation helpers remain callable. This keeps `docker-compose.test.yml`
  and existing tests green and lets the downstream change call `configure_otel`
  unconditionally.
- **Idempotent.** `configure_otel` guards against double-installation so repeated calls
  (e.g. import-time + app-startup) don't register conflicting providers.
- **Reuse, don't reinvent.** Use upstream `opentelemetry-sdk` and
  `opentelemetry-instrumentation-fastapi`; this change adds only the project-specific glue.
- **Testing.** Exercise the module in-process: assert a resource `service.name`, assert
  spans/metrics can be produced without raising, assert the no-endpoint no-op path, and
  assert idempotency. No network exporter or running Alloy is required for this slice.

## Tasks

- [x] 1.1 Add OpenTelemetry runtime dependencies to `pyproject.toml` (`opentelemetry-sdk`,
      `opentelemetry-exporter-otlp-proto-http`, `opentelemetry-instrumentation-fastapi`)
      and refresh the lockfile.
- [x] 1.2 Add `app/otel.py` with `configure_otel(service_name)` building a `service.name`
      `Resource` and installing global Tracer/Meter providers over the OTLP/HTTP exporters,
      reading the endpoint from `OTEL_EXPORTER_OTLP_ENDPOINT`.
- [x] 1.3 Make `configure_otel` a safe no-op when `OTEL_EXPORTER_OTLP_ENDPOINT` is unset,
      and idempotent on repeated calls.
- [x] 1.4 Add the FastAPI instrumentation helper (wrapping `FastAPIInstrumentor`) that an
      app instruments itself with in one call, callable with or without an endpoint set.
- [x] 1.5 Add unit tests covering: resource `service.name`, span/metric recording without
      error, the no-endpoint no-op path, and idempotency.
- [x] 1.6 Confirm the existing test suite still passes (no collector required) and the
      ingestion/frontend processes still boot with the new dependency present.
