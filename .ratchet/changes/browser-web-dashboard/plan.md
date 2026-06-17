# browser-web-dashboard

## Why

This is the **last** change of the `replication-and-browser-traces` phase and
the closing change of the whole batch's per-flow work — the one that makes the
browser slice visible. Every upstream dependency is done:

- the Alloy -> Tempo + Prometheus backbone is up
  (`observability-stack-compose`),
- Grafana has a Tempo datasource (uid `tempo`) and a file-based dashboard
  provider that auto-loads any JSON dropped into `docker/grafana/dashboards/`
  (`grafana-provisioning`),
- the replication half of the phase is complete and visible
  (`replication-lag-metric` + `replication-dashboard`, the
  "Primary/Replica Streaming" dashboard over `pg_replication_lag_bytes` /
  `pg_replication_lag_seconds`), and
- `instrument-browser-web` makes the React SPA emit
  `service.name=fleet-dashboard-web` traces: a document-load span plus
  fetch/XHR client spans for the `GET /vehicles`,
  `GET /vehicles/anomalies/latest`, and `GET /zones/counts` snapshot reads,
  exported OTLP/HTTP to Alloy (CORS handled) and joined to the `frontend-api`
  server spans via a W3C `traceparent` injected onto the cross-origin fetches.

Everything needed to *see* the browser flow exists except the view itself. This
change owns exactly that: author the **"Frontend Web (Browser)"** Grafana
dashboard JSON and drop it into the provisioned folder so a clean
`docker compose up` preloads it and, after a real browser loads the SPA, its
browser-trace panels populate from Tempo. With this in place the phase
proof-of-work passes end to end (both phase dashboards provisioned, browser
traces in Tempo).

## What Changes

- Add `docker/grafana/dashboards/frontend-web-browser.json` — a Grafana
  dashboard model with `title: "Frontend Web (Browser)"`. The phase proof hits
  `GET /api/search?query=Frontend%20Web`, so the title must contain the exact
  string `Frontend Web`; the chosen title satisfies that and matches the phase
  dashboard name. It is picked up automatically by the existing file provider;
  **no compose or provisioning change is required** because the mount and
  provider already exist.
- Bind every panel's `datasource` to the **provisioned Tempo uid** (`tempo`)
  rather than a Grafana auto-generated id, so the dashboard is reproducible
  across `docker compose down/up`.
- The browser SDK emits **traces only** (no browser-side custom metrics, and no
  Tempo metrics-generator / span-metrics is configured in this stack), so the
  panels are **Tempo-backed** (TraceQL / Tempo Search), not Prometheus-backed:
  - **Browser traces** — a Tempo Search / Traces panel listing recent
    `service.name=fleet-dashboard-web` traces, the headline panel that populates
    once a real browser has loaded the SPA.
  - **Document load** — a panel isolating the `document-load` / page-load span
    (with its resource-fetch / page-load child timings) so SPA load latency is
    visible.
  - **REST snapshot fetches** — a panel listing the fetch/XHR client spans for
    the one-time snapshot reads (`GET /vehicles`,
    `GET /vehicles/anomalies/latest`, `GET /zones/counts`) under
    `service.name=fleet-dashboard-web`.
  - **Browser -> frontend-api joined trace** — a panel / drilldown that opens a
    trace spanning `fleet-dashboard-web` -> `frontend-api`, proving the
    traceparent join (the browser fetch span parents the frontend-api server
    span).
- Confirm the **exact TraceQL / search syntax and span identities** empirically
  at implementation time by bringing the stack up, loading the SPA in a real
  headless browser, and inspecting the actual `fleet-dashboard-web` spans in
  Tempo (span names for document-load and the fetch client spans), then wire the
  panel queries to whatever the SDK actually emits rather than guessing.

## Design

- **Vertical-slice scope — JSON only.** This change adds one dashboard file and
  nothing else. It does **not** touch the backbone services, the datasource or
  provider provisioning, the browser OTel SDK wiring / CORS / build-arg / compose
  plumbing (owned by `instrument-browser-web`), or the replication
  probe/metric/dashboard (already complete). It is the smallest change that turns
  the already-flowing browser telemetry into the visible
  "Frontend Web (Browser)" dashboard the phase goal requires.
- **Mirror the shipped dashboard pattern.** This is the browser-trace twin of the
  shipped metric/trace dashboards (`ingestion-api.json`, `frontend-api.json`,
  `primary-replica-streaming.json`); reuse their schema version,
  datasource-by-uid binding, and Tempo panel conventions so the dashboards are
  consistent.
- **Tempo-backed by design.** Unlike the frontend-api and replication dashboards
  (which have Prometheus series to chart), the browser flow produces only traces
  in this stack — there are no browser custom metrics and no span-metrics
  generator. The dashboard therefore visualizes traces directly from Tempo; this
  is the correct thin slice, not a gap.
- **Bind by fixed uid, not by id or name.** `grafana-provisioning` deliberately
  pinned `uid: tempo` so dashboards are portable; this change references that uid
  directly in every panel's `datasource`. No auto-generated ids leak into the
  JSON.
- **service.name is the data contract.** The browser spans carry
  `service.name=fleet-dashboard-web` (locked by `instrument-browser-web` and the
  batch manifest); the dashboard binds its primary query to exactly that, the
  same tag the phase proof searches Tempo for.
- **Title is the proof contract.** The phase proof hits
  `GET /api/search?query=Frontend%20Web`, so the dashboard `title` must contain
  `Frontend Web` exactly; `Frontend Web (Browser)` satisfies it.
- **Runtime-only, harness untouched.** The only addition is a file under
  `docker/grafana/dashboards/`; `docker-compose.test.yml` and the pytest/vitest
  suites are not touched and stay green.
- **Proof-of-work for this change == the phase proof.** As the closing change of
  the phase, its proof is the phase blackbox: from a clean
  `docker compose up -d --wait`, after a **real headless browser** loads
  `http://localhost:8080` and a short ingest delay, Tempo returns >=1
  `service.name=fleet-dashboard-web` trace AND a dashboard search for
  "Frontend Web" returns >=1 result AND the "Primary/Replica Streaming"
  dashboard search also returns >=1 — both phase dashboards provisioned and the
  browser panels populated. (A headless mechanism — Playwright MCP / `npx
  playwright` / docker'd chromium — is required because `curl` cannot run the JS
  SDK.)

## Tasks

- [x] 1. Bring the stack up (`docker compose up -d --wait`), load
      `http://localhost:8080` in a **real headless browser** (Playwright MCP /
      `npx playwright` / docker'd chromium) so the SDK runs and flushes, then
      inspect the actual `service.name=fleet-dashboard-web` spans in Tempo —
      record the **real span names / identities** for the document-load span and
      the fetch/XHR client spans the panel queries will target.
- [x] 2. Author `docker/grafana/dashboards/frontend-web-browser.json` as a valid
      Grafana dashboard model with `title: "Frontend Web (Browser)"`,
      scaffolding the panels below; bind every panel's `datasource` to uid
      `tempo`.
- [x] 3. Add the **Browser traces** panel: a Tempo Search / Traces panel listing
      recent `service.name=fleet-dashboard-web` traces (the headline panel that
      populates after a real browser loads the SPA).
- [x] 4. Add the **Document load** panel isolating the `document-load` /
      page-load span (with its resource-fetch / page-load timings) so SPA load
      latency is visible.
- [x] 5. Add the **REST snapshot fetches** panel listing the fetch/XHR client
      spans for `GET /vehicles`, `GET /vehicles/anomalies/latest`, and
      `GET /zones/counts` under `service.name=fleet-dashboard-web`.
- [x] 6. Add the **Browser -> frontend-api joined trace** panel / drilldown that
      opens a trace spanning `fleet-dashboard-web` -> `frontend-api`, proving the
      traceparent join (browser fetch span parents the frontend-api server span).
- [x] 7. Clean-boot the stack (`docker compose up -d --wait`) and confirm Grafana
      auto-provisions the dashboard: `GET /api/search?query=Frontend%20Web`
      returns >=1 result and startup logs show no provisioning error for it.
- [x] 8. After a real headless-browser load of the SPA + a short delay, confirm
      the panels populate (browser traces listed, document-load span visible,
      snapshot fetch spans visible, joined `fleet-dashboard-web -> frontend-api`
      trace drillable) by inspecting panel data / the rendered dashboard.
- [x] 9. Run the **phase proof-of-work** end to end: from a clean
      `docker compose up -d --wait`, after a real headless browser loads
      `http://localhost:8080`, Tempo `service.name=fleet-dashboard-web` search
      returns >=1 AND dashboard searches for both "Frontend Web" and
      "Primary/Replica Streaming" return >=1; confirm it passes.
- [x] 10. Confirm `docker-compose.test.yml` is unchanged and the existing
      pytest/vitest suites still pass (this change is dashboard-JSON-only).
