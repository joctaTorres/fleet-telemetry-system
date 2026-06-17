# frontend-api-dashboard

## Why

This is the **last** change of the `frontend-api-websockets` phase and the one
that makes the slice visible. The three upstream changes are done: the backbone
is up (`observability-stack-compose`), Grafana has Tempo + Prometheus
datasources and a file-based dashboard provider auto-loading any JSON dropped
into `docker/grafana/dashboards/` (`grafana-provisioning`), the frontend read
API emits `service.name=frontend-api` traces plus the `frontend.requests`
counter and `frontend.request.duration` (ms) histogram
(`instrument-frontend-api`), and the `/ws` WebSocket lifecycle emits the
`frontend.ws.active_connections` gauge and `frontend.ws.broadcasts` counter and
its own connection-lifecycle spans (`instrument-websocket-lifecycle`).

Everything needed to *see* the flow exists except the view itself. This change
owns exactly that: author the **"Frontend API & WebSockets"** Grafana dashboard
JSON and drop it into the provisioned folder so a clean `docker compose up`
preloads it and, with a WS client connected and GET reads flowing, its
request-rate / latency / error panels, its live-connection and broadcast-rate
panels, and its trace view all populate. With this in place the phase
proof-of-work passes end to end.

## What Changes

- Add `docker/grafana/dashboards/frontend-api.json` — a Grafana dashboard model
  with `title: "Frontend API & WebSockets"`. The phase proof hits
  `GET /api/search?query=Frontend%20API`, so the title must contain the exact
  string `Frontend API`; the chosen title satisfies that and matches the phase
  dashboard name. It is picked up automatically by the existing file provider;
  no compose or provisioning change is required because the mount and provider
  already exist.
- Bind panels to the **provisioned datasource uids** (`prometheus` for metrics,
  `tempo` for traces) rather than to Grafana auto-generated ids, so the
  dashboard is reproducible across `docker compose down/up`.
- Panels (the REST rate / latency / error trio, the two WebSocket signals the
  phase goal calls out, and the trace view that closes the loop):
  - **Active WebSocket connections** — a stat/time-series panel over the gauge
    derived from `frontend.ws.active_connections`
    (`frontend_ws_active_connections` in Prometheus) — the phase headline signal,
    non-zero whenever a client is connected.
  - **REST request rate** — time series of `rate(...)` over the series derived
    from the `frontend.requests` counter, broken down by / filterable on
    `http.route` so reads like `GET /vehicles` are visible.
  - **REST latency** — at least one quantile (e.g. p95) via `histogram_quantile`
    over the buckets derived from the `frontend.request.duration` (ms) histogram.
  - **REST errors** — error rate or ratio isolating non-2xx responses via the
    `http.status_code` attribute on the `frontend.requests` series (the 422 path
    from the instrumentation populates this).
  - **WebSocket broadcast rate** — time series of `rate(...)` over the series
    derived from the `frontend.ws.broadcasts` counter, showing fan-out volume.
  - **Frontend API traces** — a Tempo-backed panel listing recent
    `service.name=frontend-api` traces, drillable into the `GET` and `/ws`
    server spans.
- Derive the **exact Prometheus metric names** empirically at implementation
  time. OTLP -> Alloy -> Prometheus remote-write rewrites OTel metric names
  (dots to underscores, counter `_total` suffix, histogram unit +
  `_bucket`/`_sum`/`_count`); rather than guessing, bring the stack up with a WS
  client + GET load and query Prometheus
  (`/api/v1/label/__name__/values` or `/api/v1/series`) to read the real series
  names, then wire the panel queries to them. Keep the active-connections query
  in sync with the shipped `frontend_ws_active_connections` gauge name (the phase
  proof queries exactly that).

## Design

- **Vertical-slice scope — JSON only.** This change adds one dashboard file and
  nothing else. It does **not** touch the backbone services, the datasource or
  provider provisioning, or the frontend / WebSocket instrumentation — those are
  owned by the upstream changes and are already complete. It is the smallest
  change that turns the already-flowing telemetry into the visible
  "Frontend API & WebSockets" dashboard the phase goal requires.
- **Mirror the ingestion-dashboard pattern.** This is the frontend twin of the
  shipped `ingestion-api.json`; reuse its panel conventions, schema version, and
  datasource-by-uid binding so the two dashboards are consistent.
- **Bind by fixed uid, not by id or name.** `grafana-provisioning` deliberately
  pinned `uid: tempo` and `uid: prometheus` so dashboards are portable; this
  change relies on that and references those uids directly in every panel's
  `datasource`. No auto-generated ids leak into the JSON.
- **Names are stable upstream, translated downstream.** The metric *instrument*
  names (`frontend.requests`, `frontend.request.duration`,
  `frontend.ws.active_connections`, `frontend.ws.broadcasts`) and the span
  service name (`frontend-api`) are contracts fixed by the instrumentation
  changes; the dashboard binds to those. The *Prometheus* series names are a
  function of the OTLP->Prometheus naming convention, so they are read from the
  running Prometheus rather than assumed.
- **Title is the proof contract.** The phase proof hits
  `GET /api/search?query=Frontend%20API`, so the dashboard `title` must contain
  `Frontend API` exactly; `Frontend API & WebSockets` satisfies it.
- **Runtime-only, harness untouched.** The only addition is a file under
  `docker/grafana/dashboards/`; `docker-compose.test.yml` and the pytest/vitest
  suites are not touched and stay green.
- **Proof-of-work for this change == the phase proof.** As the closing change of
  the phase, its proof is the phase blackbox: from a clean
  `docker compose up -d --wait` with a WS client connected,
  `frontend_ws_active_connections > 0` in Prometheus, Tempo returns >=1
  `service.name=frontend-api` trace, and a dashboard search for "Frontend API"
  returns >=1 result — with the panels populated.

## Tasks

- [x] 1. Bring the stack up (`docker compose up -d --wait`), hold a `/ws` client
      open and drive GET reads, then read the **real Prometheus series names**
      for `frontend.requests`, `frontend.request.duration`,
      `frontend.ws.active_connections`, and `frontend.ws.broadcasts` (query
      `/api/v1/label/__name__/values` or `/api/v1/series`), recording the exact
      names and label keys the panel queries will use; confirm
      `frontend_ws_active_connections` is the gauge name.
- [x] 2. Author `docker/grafana/dashboards/frontend-api.json` as a valid Grafana
      dashboard model with `title: "Frontend API & WebSockets"`, scaffolding the
      six panels below; bind each metric panel's `datasource` to uid
      `prometheus` and the trace panel's to uid `tempo`.
- [x] 3. Add the **Active WebSocket connections** panel over the
      `frontend_ws_active_connections` gauge (the phase headline signal).
- [x] 4. Add the **REST request rate** panel: a time series of `rate(...)` over
      the `frontend.requests`-derived counter, broken down by / filterable on
      `http.route` so reads like `GET /vehicles` are visible.
- [x] 5. Add the **REST latency** panel: at least one quantile (e.g. p95) via
      `histogram_quantile` over the `frontend.request.duration` (ms) buckets.
- [x] 6. Add the **REST errors** panel: error rate or ratio isolating non-2xx
      responses via `http.status_code` on the `frontend.requests` series
      (confirm it populates by driving a 422 alongside 200s).
- [x] 7. Add the **WebSocket broadcast rate** panel: a time series of `rate(...)`
      over the `frontend.ws.broadcasts`-derived counter.
- [x] 8. Add the **Frontend API traces** panel: a Tempo-backed panel listing
      recent `service.name=frontend-api` traces, drillable into the `GET` and
      `/ws` server spans.
- [x] 9. Clean-boot the stack (`docker compose up -d --wait`) and confirm Grafana
      auto-provisions the dashboard: `GET /api/search?query=Frontend%20API`
      returns >=1 result and startup logs show no provisioning error for it.
- [x] 10. With a WS client connected and GET load flowing, confirm all six panels
      populate (active connections, request rate, latency quantile, error series,
      broadcast rate, and live frontend-api traces) by inspecting panel data /
      the rendered dashboard.
- [x] 11. Run the **phase proof-of-work** end to end (`frontend_ws_active_connections > 0`
      in Prometheus AND Tempo `service.name=frontend-api` trace search returns
      >=1 AND "Frontend API" dashboard search returns >=1) and confirm it passes.
- [x] 12. Confirm `docker-compose.test.yml` is unchanged and the existing
      pytest/vitest suites still pass.
