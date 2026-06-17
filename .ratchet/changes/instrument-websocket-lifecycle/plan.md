# instrument-websocket-lifecycle

## Why

The `frontend-api-websockets` phase wants the dashboard's live surface visible end
to end: opening the SPA (or holding any WS client open) should make a live
connection count and fan-out volume appear in Grafana, with WebSocket traces in
Tempo. The previous change, `instrument-frontend-api`, already wired the frontend
FastAPI app through the shared `app.otel` bootstrap (`configure_otel("frontend-api")`
+ `instrument_fastapi_app(app)`) and pointed the runtime `frontend` service at
Alloy, so GET reads already produce `service.name=frontend-api` server spans and
request metrics. What is still missing is the *live* half of the surface: the
`/ws` connection lifecycle and the Redis-driven fan-out emit no custom telemetry,
so the phase's headline signal — `frontend_ws_active_connections > 0` while a
client is connected — does not exist yet.

This change owns the WebSocket lifecycle half of the slice. It is the second of
three changes in the phase; `frontend-api-dashboard` then binds panels to the
gauge, the broadcast counter, and the request metrics this and the prior change
emit. Its output — a live active-connections gauge, a broadcast counter, and
WebSocket lifecycle spans, all under `service.name=frontend-api` — is exactly the
data the dashboard visualizes.

## What Changes

- Add a custom **active-WebSocket-connections gauge** to `app/frontend_api.py`,
  sourced from `ConnectionRegistry` membership so it can never diverge from the
  set of live connections it reports on. Created off the global meter installed by
  the shared bootstrap. Following the ingestion/frontend metric convention it is
  named so it translates to `frontend_ws_active_connections` in Prometheus (the
  exact name kept in sync with the OTLP->remote-write translation, per the phase
  proof note). An observable gauge that reads the current registry size on collect
  is preferred so connect/disconnect races cannot leak a stale value.
- Add a custom **broadcast counter** incremented in `ConnectionRegistry.broadcast`
  (or its caller) once per fan-out, so the dashboard can show fan-out volume/rate.
  Named to translate to `frontend_ws_broadcasts_total` in Prometheus. Recording
  the metric must never block or break fan-out to live clients, and the existing
  dead-client drop behavior is preserved exactly.
- Wrap the **`/ws` connection lifecycle** in an explicit span under the request's
  WebSocket scope so Tempo shows a `service.name=frontend-api` WebSocket trace
  spanning accept -> snapshot -> stream -> disconnect, with the connect-time
  snapshot read visible within it (the snapshot already reads the replica seams).
- Add unit tests asserting the gauge tracks registry membership (up on connect,
  down on disconnect, zero when empty), the broadcast counter increments on
  fan-out, and the no-endpoint path stays a green no-op — all without a running
  collector.

## Design

- **Vertical-slice scope.** This change adds exactly the WebSocket lifecycle
  telemetry: the active-connections gauge, the broadcast counter, and a connection
  lifecycle span. It reuses, and does not re-touch, everything the prior change
  shipped — the `app.otel` bootstrap call, `instrument_fastapi_app`, the request
  metrics, and the runtime `docker-compose.yml` OTLP wiring on the `frontend`
  service are all already in place. It does **not** author the "Frontend API &
  WebSockets" dashboard JSON (that is `frontend-api-dashboard`) and does **not**
  add Grafana datasources/providers (already shipped by `grafana-provisioning`).
- **Reuse the bootstrap, don't re-wire the SDK.** The gauge and counter are
  created from `metrics.get_meter(__name__)` / the module `_meter` already present
  in `frontend_api.py`; the lifecycle span from the module `_tracer`. No
  TracerProvider / MeterProvider / exporter wiring is added here.
- **Gauge sourced from the single source of truth.** `ConnectionRegistry` already
  owns the live-connection set behind an `asyncio.Lock`. The gauge observes that
  set's size rather than maintaining a parallel counter, so it cannot drift from
  reality across connect/disconnect/dead-client-drop. An observable (callback)
  gauge reading the size on each collection cycle is the safest shape.
- **Fan-out correctness is sacrosanct.** The broadcast counter is incremented
  around the existing `broadcast` logic without changing how dead clients are
  detected and dropped; metric recording is best-effort and never on a path that
  could block or fail the send loop.
- **Stable, dashboard-bindable names.** Metric names are fixed in source so the
  downstream dashboard binds deterministically: gauge -> `frontend_ws_active_connections`,
  counter -> `frontend_ws_broadcasts_total` in Prometheus. If the OTLP->remote-write
  translation yields a different suffix, the shipped metric name and the phase
  proof query are kept in sync.
- **Safe-by-default and idempotent.** With `OTEL_EXPORTER_OTLP_ENDPOINT` unset the
  gauge and counter hang off the no-op API meter: they record nothing, require no
  collector, and never raise. `docker-compose.test.yml` and the pytest suite stay
  green, and the WebSocket snapshot + delta stream behaves exactly as today.
- **Proof-of-work for this change** (the metric/trace half of the phase proof; the
  dashboard half is `frontend-api-dashboard`): from a clean `docker compose up -d
  --wait`, hold a WS client open against `ws://localhost:8002/ws`, then
  `max(frontend_ws_active_connections)` in Prometheus is > 0 and Tempo returns >=1
  `service.name=frontend-api` trace covering the WebSocket connection.

## Tasks

- [x] 1.1 Add an active-WebSocket-connections observable gauge in
      `app/frontend_api.py`, created off the existing module meter and reading the
      live `ConnectionRegistry` size on collection; name it so it translates to
      `frontend_ws_active_connections` in Prometheus. Keep it module-level so tests
      can swap an in-memory meter.
- [x] 1.2 Expose the current connection count from `ConnectionRegistry` (e.g. a
      lock-free size read) for the gauge callback to observe, without changing the
      add/remove/broadcast semantics.
- [x] 1.3 Add a broadcast counter incremented once per fan-out in
      `ConnectionRegistry.broadcast` (or its caller), named to translate to
      `frontend_ws_broadcasts_total`; ensure recording never blocks or breaks
      fan-out and the dead-client drop behavior is unchanged.
- [x] 1.4 Wrap the `/ws` connection lifecycle in an explicit span off the module
      tracer (accept -> snapshot -> stream -> disconnect) so Tempo shows a
      `service.name=frontend-api` WebSocket trace with the snapshot read visible
      within it.
- [x] 1.5 Add unit tests: the gauge tracks registry membership (up on connect,
      down on disconnect, zero when empty) and the broadcast counter increments on
      fan-out, using an in-memory meter and no running collector.
- [x] 1.6 Confirm the no-endpoint path: with `OTEL_EXPORTER_OTLP_ENDPOINT` unset
      the app starts, `/ws` serves snapshot + deltas, and the gauge/counter are
      no-ops that export nothing and never raise.
- [x] 1.7 Bring the stack up and hold a WS client open against
      `ws://localhost:8002/ws`; confirm `max(frontend_ws_active_connections) > 0`
      in Prometheus and Tempo returns >=1 `service.name=frontend-api` trace for the
      WebSocket connection.
- [x] 1.8 Confirm `docker-compose.test.yml` is unchanged and the existing pytest
      suite still passes with no running collector.
