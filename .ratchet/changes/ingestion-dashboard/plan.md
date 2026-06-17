# ingestion-dashboard

## Why

This is the **last** change of the `ingestion-trace-backbone` phase and the one
that makes the slice visible. The three upstream changes are done: the backbone
is up (`observability-stack-compose`), Grafana has Tempo + Prometheus
datasources and a file-based dashboard provider auto-loading any JSON dropped
into `docker/grafana/dashboards/` (`grafana-provisioning`), and the ingestion
API emits `service.name=ingestion-api` traces plus the `ingestion.requests`
counter and `ingestion.request.duration` histogram, each tagged with
`http.method` / `http.route` / `http.status_code` (`instrument-ingestion-api`).

Everything needed to *see* the flow exists except the view itself. This change
owns exactly that: author the **"Ingestion API"** Grafana dashboard JSON and
drop it into the provisioned folder so a clean `docker compose up` preloads it
and, with k6 driving `POST /telemetry`, its request-rate / latency / error
panels populate from Prometheus and its traces panel lists live ingestion-api
spans from Tempo. With this in place the phase proof-of-work passes end to end.

## What Changes

- Add `docker/grafana/dashboards/ingestion-api.json` — a Grafana dashboard model
  with `title: "Ingestion API"` (the exact string the phase proof searches for),
  picked up automatically by the existing file provider; no compose or
  provisioning change is required because the mount and provider already exist.
- Bind panels to the **provisioned datasource uids** (`prometheus` for metrics,
  `tempo` for traces) rather than to Grafana auto-generated ids, so the
  dashboard is reproducible across `docker compose down/up`.
- Panels (the rate / latency / error trio the phase goal calls for, plus the
  trace view that closes the loop):
  - **Request rate** — time series of `rate(...)` over the Prometheus series
    derived from the `ingestion.requests` counter, broken down by / filterable
    on `http.route` so the `POST /telemetry` flow is visible.
  - **Latency** — at least one quantile (e.g. p95) via `histogram_quantile`
    over the buckets derived from the `ingestion.request.duration` (ms)
    histogram.
  - **Errors** — error rate or error ratio isolating non-2xx responses via the
    `http.status_code` attribute on the `ingestion.requests` series (the 422
    path from the instrumentation populates this).
  - **Traces** — a Tempo-backed panel listing recent `service.name=ingestion-api`
    traces, drillable into the `POST /telemetry` server span.
- Derive the **exact Prometheus metric names** empirically at implementation
  time. OTLP → Alloy → Prometheus remote-write rewrites OTel metric names (dots
  to underscores, counter `_total` suffix, histogram unit + `_bucket`/`_sum`/
  `_count`); rather than guessing, bring the stack up with k6 and query
  Prometheus (`/api/v1/label/__name__/values` or `/api/v1/series`) to read the
  real series names, then wire the panel queries to them.

## Design

- **Vertical-slice scope — JSON only.** This change adds one dashboard file and
  nothing else. It does **not** touch the backbone services, the datasource or
  provider provisioning, or the ingestion instrumentation — those are owned by
  the three upstream changes and are already complete. It is the smallest change
  that turns the already-flowing telemetry into the visible "Ingestion API"
  dashboard the phase goal requires.
- **Bind by fixed uid, not by id or name.** `grafana-provisioning` deliberately
  pinned `uid: tempo` and `uid: prometheus` so dashboards are portable; this
  change relies on that and references those uids directly in every panel's
  `datasource`. No auto-generated ids leak into the JSON.
- **Names are stable upstream, translated downstream.** The metric *instrument*
  names (`ingestion.requests`, `ingestion.request.duration`) and the span
  service name (`ingestion-api`) are contracts fixed by
  `instrument-ingestion-api`; the dashboard binds to those. The *Prometheus*
  series names are a function of the OTLP→Prometheus naming convention, so they
  are read from the running Prometheus rather than assumed, keeping the panels
  correct against whatever Alloy actually remote-writes.
- **Title is the proof contract.** The phase proof-of-work hits
  `GET /api/search?query=Ingestion%20API`, so the dashboard `title` must be
  exactly `Ingestion API`. The file name and any folder grouping are
  incidental; the title is load-bearing.
- **Reuse, don't reinvent, the Grafana model.** Use a standard Grafana dashboard
  JSON model (schema version compatible with the pinned Grafana image),
  idiomatic time-series and table/traces panels, and the project
  `opentelemetry` skill for Grafana-stack panel + Tempo query conventions rather
  than hand-rolling unusual panel config.
- **Runtime-only, harness untouched.** The only addition is a file under
  `docker/grafana/dashboards/`; `docker-compose.test.yml` and the pytest/vitest
  suites are not touched and stay green.
- **Proof-of-work for this change == the phase proof.** As the closing change of
  the phase, its proof is the phase blackbox: from a clean
  `docker compose up -d --wait` with k6 running, Grafana is healthy, Tempo
  returns ≥1 `service.name=ingestion-api` trace, and a dashboard search for
  "Ingestion API" returns ≥1 result — with the four panels populated.

## Tasks

- [x] 5.1 Bring the stack up with k6 driving load and read the **real Prometheus
      series names** for the `ingestion.requests` counter and the
      `ingestion.request.duration` histogram (query
      `/api/v1/label/__name__/values` or `/api/v1/series`), recording the exact
      names and label keys the panel queries will use.
- [x] 5.2 Author `docker/grafana/dashboards/ingestion-api.json` as a valid
      Grafana dashboard model with `title: "Ingestion API"`, scaffolding the
      four panels below; bind each metric panel's `datasource` to uid
      `prometheus` and the trace panel's to uid `tempo`.
- [x] 5.3 Add the **request-rate** panel: a time series of `rate(...)` over the
      `ingestion.requests`-derived counter, broken down by / filterable on
      `http.route` so `POST /telemetry` is visible.
- [x] 5.4 Add the **latency** panel: at least one quantile (e.g. p95) via
      `histogram_quantile` over the `ingestion.request.duration` (ms) buckets.
- [x] 5.5 Add the **error** panel: error rate or ratio isolating non-2xx
      responses via `http.status_code` on the `ingestion.requests` series
      (confirm it populates by driving a 422 alongside 201s).
- [x] 5.6 Add the **traces** panel: a Tempo-backed panel listing recent
      `service.name=ingestion-api` traces, drillable into the `POST /telemetry`
      span.
- [x] 5.7 Restart/clean-boot the stack (`docker compose up -d --wait`) and
      confirm Grafana auto-provisions the dashboard: search
      `GET /api/search?query=Ingestion%20API` returns ≥1 result and startup logs
      show no provisioning errors for it.
- [x] 5.8 With k6 driving `POST /telemetry`, confirm all four panels populate
      (request rate, latency quantile, error series, and live ingestion-api
      traces) by inspecting panel data / the rendered dashboard.
- [x] 5.9 Run the **phase proof-of-work** end to end (Grafana health + Tempo
      `service.name=ingestion-api` trace search + "Ingestion API" dashboard
      search) and confirm it exits 0.
- [x] 5.10 Confirm `docker-compose.test.yml` is unchanged and the existing
      pytest/vitest suites still pass.
