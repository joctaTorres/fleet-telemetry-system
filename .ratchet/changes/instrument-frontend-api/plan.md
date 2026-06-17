# instrument-frontend-api

## Why

The `frontend-api-websockets` phase wants the dashboard's read surface visible end
to end: opening the SPA (or hitting the REST endpoints) should make live
`service.name=frontend-api` spans appear in Tempo and live request-rate / latency
/ error panels appear in Grafana. The two foundations the previous phase shipped
are reused as-is — `otel-bootstrap-python` added the reusable `app.otel` bootstrap
(`configure_otel` + `instrument_fastapi_app`), and `observability-stack-compose`
stood up the Alloy → Tempo + Prometheus + Grafana backbone. The ingestion API is
already wired through that bootstrap; the frontend API is not, and the `frontend`
service in compose has no OTLP endpoint, so no frontend telemetry is produced.

This change owns the frontend read surface half of the slice. It is the first of
three changes in the phase: `instrument-websocket-lifecycle` (the active-connection
gauge and broadcast counter) and `frontend-api-dashboard` (the dashboard JSON)
build on it. Its output — `service.name=frontend-api` GET traces in Tempo and
frontend request metrics in Prometheus — is the data those downstream changes
extend and visualize.

## What Changes

- Wire OTel into `app/frontend_api.py` using only the shared `app.otel` bootstrap,
  mirroring the ingestion API:
  - call `configure_otel("frontend-api")` at app startup, and
  - call `instrument_fastapi_app(app)` so incoming requests produce server spans
    under `service.name=frontend-api`.
  Both are safe no-ops when `OTEL_EXPORTER_OTLP_ENDPOINT` is unset, so import,
  pytest, and plain local boot are unaffected.
- Emit **request metrics** for the rate / latency / error panels, keyed by HTTP
  method, route, and response status, obtained from the global meter installed by
  `configure_otel` and exported over OTLP/HTTP to Alloy (which remote-writes to
  Prometheus). Cover both successful (200) and rejected (422) requests so the
  error series is populated.
- Make the **replica read** for a request visible: wrap the replica-backed read a
  GET handler runs (the snapshot/list read against `replica_connection`) in a
  child span under the request's server span, so the trace shows the read seam
  rather than just an opaque server span.
- Point the runtime `frontend` service at Alloy in `docker-compose.yml`: set
  `OTEL_EXPORTER_OTLP_ENDPOINT` to Alloy's OTLP/HTTP endpoint
  (`http://alloy:4318`) over the compose network, env-overridable in the existing
  style, and order it after the backbone is available (as the `ingestion` service
  already is).
- Add unit tests asserting the frontend app installs OTel through the bootstrap
  and records its request metric, without requiring a running collector.

## Design

- **Vertical-slice scope.** This change instruments exactly the frontend read
  surface (GET endpoints + the replica reads behind them) for one flow. It does
  **not** add the active-WebSocket-connections gauge or the broadcast counter
  (those are `instrument-websocket-lifecycle`), does **not** author the
  "Frontend API & WebSockets" dashboard JSON (that is `frontend-api-dashboard`),
  and does **not** add Grafana datasources/providers (already shipped by
  `grafana-provisioning`). The `/ws` route is still covered passively by ASGI
  auto-instrumentation, but its dedicated lifecycle spans/metrics are deferred to
  the next change. This change proves only that GET reads produce queryable
  `frontend-api` traces in Tempo and request metrics in Prometheus.
- **Reuse the bootstrap, don't re-wire the SDK.** All SDK setup lives in
  `app.otel`; this change calls `configure_otel` + `instrument_fastapi_app` and
  adds only the frontend-specific request metric and replica-read span. No
  TracerProvider / MeterProvider / exporter wiring is duplicated here.
- **Mirror the ingestion instrumentation shape.** Server spans come from
  `FastAPIInstrumentor` via the bootstrap helper; request metrics are recorded
  explicitly from an HTTP middleware (a request counter + a duration measure with
  method / route / status attributes) so the names and labels are stable and the
  downstream dashboard binds panels to them deterministically. Metric names follow
  the ingestion convention (`frontend.requests` / `frontend.request.duration`),
  translating to `frontend_requests_total` / `frontend_request_duration_*` in
  Prometheus.
- **Config from the environment, nothing hard-coded.** The OTLP endpoint is read
  from `OTEL_EXPORTER_OTLP_ENDPOINT`, set only by compose, mirroring the existing
  `DATABASE_URL` / `REPLICA_URL` / `REDIS_URL` convention. No endpoint or
  credentials in app source.
- **Safe-by-default and idempotent.** With no endpoint set the app behaves exactly
  as today: no exporter, no collector required, nothing raised. This keeps
  `docker-compose.test.yml` and the pytest suite green and lets the frontend app
  call the bootstrap unconditionally.
- **Runtime-only compose change.** Only the runtime `docker-compose.yml`
  `frontend` service gains the OTLP endpoint and backbone ordering;
  `docker-compose.test.yml` is untouched.
- **Proof-of-work for this change** (a subset of the phase proof, which also needs
  `instrument-websocket-lifecycle` + `frontend-api-dashboard`): from a clean
  `docker compose up -d --wait` with GET traffic driven against the read endpoints
  (the `dashboard` healthcheck already hits `GET /vehicles`, plus a few curls),
  Tempo returns ≥1 trace for `service.name=frontend-api`, and Prometheus exposes
  the frontend request-rate / latency / error series.

## Tasks

- [x] 1.1 Wire `configure_otel("frontend-api")` into `app/frontend_api.py` so it
      runs at app startup, reading the endpoint from the environment only; keep
      `frontend-api` as a module constant the wiring and tests share.
- [x] 1.2 Instrument the frontend FastAPI app via `instrument_fastapi_app(app)`
      so GET reads produce `service.name=frontend-api` server spans, callable
      whether or not an endpoint is set.
- [x] 1.3 Add an explicit request metric (count + duration) recorded from an HTTP
      middleware on each response with HTTP method, route, and response status
      attributes, obtained from the global meter; cover both the 200 and 422
      paths so the error series populates.
- [x] 1.4 Wrap the replica-backed read a GET handler runs in a child span (named
      for the read seam, e.g. the snapshot/list read against `replica_connection`)
      so the replica read is visible nested under the request's server span.
- [x] 1.5 Set `OTEL_EXPORTER_OTLP_ENDPOINT` to Alloy's OTLP/HTTP endpoint
      (`http://alloy:4318`) on the `frontend` service in `docker-compose.yml`,
      env-overridable, and order it after the backbone (`alloy: service_started`)
      as the `ingestion` service already is.
- [x] 1.6 Confirm the no-endpoint path: with `OTEL_EXPORTER_OTLP_ENDPOINT` unset
      the app starts and serves its read endpoints with no exporter and no
      collector.
- [x] 1.7 Add unit tests: the app installs OTel through the bootstrap and records
      its request metric without a running collector; keep the no-op path green.
- [x] 1.8 Bring the stack up and drive GET traffic against the read endpoints;
      confirm Tempo returns ≥1 `service.name=frontend-api` trace and Prometheus
      exposes the request-rate / latency / error series.
- [x] 1.9 Confirm `docker-compose.test.yml` is unchanged and the existing pytest
      suite still passes with no running collector.
