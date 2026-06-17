# instrument-ingestion-api

## Why

The `ingestion-trace-backbone` phase wants one real flow visible end to end:
k6 drives `POST /telemetry` and a user sees live ingestion spans in Tempo and
live request-rate / latency / error panels in Grafana. The two foundations are
already in place — `otel-bootstrap-python` added the reusable `app.otel`
bootstrap (`configure_otel` + `instrument_fastapi_app`), and
`observability-stack-compose` stood up the Alloy → Tempo + Prometheus + Grafana
backbone. Nothing yet connects them: the ingestion API does not call the
bootstrap, and the `ingestion` service in compose has no OTLP endpoint, so no
ingestion telemetry is produced.

This change owns that connection. It is the instrumentation half of the slice
(`after: [otel-bootstrap-python, observability-stack-compose]`): wire the
shared bootstrap into `app/ingestion_api.py`, emit request metrics for the
rate/latency/error panels, and point the `ingestion` service at Alloy's OTLP
endpoint in the runtime compose file. Its output — `service.name=ingestion-api`
traces in Tempo and request metrics in Prometheus — is the data the downstream
`ingestion-dashboard` change visualizes.

## What Changes

- Wire OTel into `app/ingestion_api.py` using only the shared `app.otel`
  bootstrap:
  - call `configure_otel("ingestion-api")` at module/startup time, and
  - call `instrument_fastapi_app(app)` so incoming requests produce server
    spans under `service.name=ingestion-api`.
  Both are safe no-ops when `OTEL_EXPORTER_OTLP_ENDPOINT` is unset, so import,
  pytest, and plain local boot are unaffected.
- Emit **request metrics** for the rate / latency / error panels, keyed by HTTP
  method, route, and response status, obtained from the global meter installed
  by `configure_otel` and exported over OTLP/HTTP to Alloy (which remote-writes
  to Prometheus). Cover both successful (201) and rejected (422) requests so
  the error series is populated.
- Point the runtime `ingestion` service at Alloy in `docker-compose.yml`: set
  `OTEL_EXPORTER_OTLP_ENDPOINT` to Alloy's OTLP/HTTP endpoint
  (`http://alloy:4318`) over the compose network, with an env-overridable
  default in the existing `*_PORT` / env style, and ensure the service starts
  after the backbone is available.
- Add unit tests asserting the ingestion app installs OTel through the bootstrap
  and records its request metric, without requiring a running collector.

## Design

- **Vertical-slice scope.** This change instruments exactly one service for one
  flow. It does **not** add Grafana datasources or the dashboard provider (that
  is `grafana-provisioning`), does **not** author the "Ingestion API" dashboard
  JSON (that is `ingestion-dashboard`), and does **not** instrument the frontend
  API, CDC consumer, or browser (later phases). It proves only that
  `POST /telemetry` produces queryable `ingestion-api` traces in Tempo and
  request metrics in Prometheus.
- **Reuse the bootstrap, don't re-wire the SDK.** All SDK setup lives in
  `app.otel`; this change calls `configure_otel` + `instrument_fastapi_app` and
  adds only the ingestion-specific request metric. No TracerProvider /
  MeterProvider / exporter wiring is duplicated here.
- **Traces from auto-instrumentation, metrics explicit and stable.** Server
  spans come from `FastAPIInstrumentor` via the bootstrap helper. Request
  metrics are recorded explicitly (a request counter + a duration measure with
  method / route / status attributes) so the names and labels are stable and
  the downstream dashboard can bind panels to them deterministically — rather
  than depending on auto-emitted metric naming.
- **Config from the environment, nothing hard-coded.** The OTLP endpoint is read
  from `OTEL_EXPORTER_OTLP_ENDPOINT`, set only by compose, mirroring the
  existing `DATABASE_URL` / `REDIS_URL` convention. No endpoint or credentials
  in app source.
- **Safe-by-default and idempotent.** With no endpoint set the app behaves
  exactly as today: no exporter, no collector required, nothing raised. This
  keeps `docker-compose.test.yml` and the pytest suite green and lets the
  ingestion app call the bootstrap unconditionally.
- **Runtime-only compose change.** Only the runtime `docker-compose.yml`
  `ingestion` service gains the OTLP endpoint and backbone ordering;
  `docker-compose.test.yml` is untouched.
- **Proof-of-work for this change** (a subset of the phase proof, which also
  needs `grafana-provisioning` + `ingestion-dashboard`): from a clean
  `docker compose up -d --wait` with k6 driving load, Tempo returns ≥1 trace for
  `service.name=ingestion-api` originating from `POST /telemetry`, and
  Prometheus exposes the ingestion request-rate / latency / error series.

## Tasks

- [x] 4.1 Wire `configure_otel("ingestion-api")` into `app/ingestion_api.py` so
      it runs at app startup, reading the endpoint from the environment only.
- [x] 4.2 Instrument the ingestion FastAPI app via `instrument_fastapi_app(app)`
      so `POST /telemetry` requests produce `service.name=ingestion-api` server
      spans, callable whether or not an endpoint is set.
- [x] 4.3 Add an explicit request metric (count + duration) recorded on each
      request with HTTP method, route, and response status attributes, obtained
      from the global meter; cover both the 201 and 422 paths so the error
      series populates.
- [x] 4.4 Set `OTEL_EXPORTER_OTLP_ENDPOINT` to Alloy's OTLP/HTTP endpoint
      (`http://alloy:4318`) on the `ingestion` service in `docker-compose.yml`,
      env-overridable, and order it after the backbone is available.
- [x] 4.5 Confirm the no-endpoint path: with `OTEL_EXPORTER_OTLP_ENDPOINT` unset
      the app starts and serves requests with no exporter and no collector.
- [x] 4.6 Add unit tests: the app installs OTel through the bootstrap and records
      its request metric without a running collector; keep the no-op path green.
- [x] 4.7 Bring the stack up with k6 driving `POST /telemetry`; confirm Tempo
      returns ≥1 `service.name=ingestion-api` trace for the route and Prometheus
      exposes the request-rate / latency / error series.
      (VERIFIED end-to-end: `docker compose up -d --wait` with k6 driving load —
      Tempo returns ingestion-api traces rooted at `POST /telemetry`, and
      Prometheus serves `ingestion_requests_total` (rate ~50 req/s incl. 422/404
      error series) and `ingestion_request_duration_milliseconds_*` (p95 ~39ms).)
- [x] 4.8 Confirm `docker-compose.test.yml` is unchanged and the existing pytest
      suite still passes with no running collector.
