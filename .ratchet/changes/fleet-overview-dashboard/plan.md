# fleet-overview-dashboard

## Why

This is the second change of the `all-dashboards-preloaded-capstone` phase
(`after: [tempo-service-graph]`). The phase goal asks for a single
`docker compose up` experience with ALL required dashboards preloaded, plus a
top-level **"Fleet Observability Overview"** dashboard "linking every flow and a
Tempo service-graph view of all critical flows."

Seven per-flow dashboards already auto-provision from
`docker/grafana/dashboards/`: "Ingestion API", "Frontend API & WebSockets",
"CDC Consumer", "Pub/Sub", "Redis Fan-out", "Primary/Replica Streaming", and
"Frontend Web (Browser)". The upstream `tempo-service-graph` change is complete:
Tempo's `metrics_generator` now emits `traces_service_graph_request_total` over
the critical flows (ingestion-api -> db, cdc-consumer -> frontend-api across
Redis pub/sub, frontend-api -> replica, fleet-dashboard-web -> frontend-api), and
the Tempo datasource's service map is wired to Prometheus. Everything the
overview needs already flows; only the eighth dashboard — the overview itself —
is missing.

This change owns exactly that thin slice: author the **"Fleet Observability
Overview"** dashboard JSON and drop it into the already-provisioned dashboards
folder so a clean `docker compose up` preloads it. It links to each per-flow
dashboard, renders the Tempo-derived service graph of all critical flows, and
surfaces a few live top-level health signals — with zero manual import. It does
**not** run the full judged end-goal acceptance; that is the downstream
`capstone-clean-boot-verify` change (`after: [fleet-overview-dashboard]`).

## What Changes

- Add `docker/grafana/dashboards/fleet-overview.json` — a Grafana dashboard
  model with `title: "Fleet Observability Overview"` (the exact eighth title the
  capstone proof's `GET /api/search?type=dash-db` expects), picked up
  automatically by the existing file provider; no compose or provisioning change
  is required because the mount and provider already exist.
- **Links to every flow.** Wire navigable links from the overview to each of the
  seven per-flow dashboards — via dashboard `links` (by uid) and/or a shared tag
  link, plus optional per-panel data links — so an operator can jump into any
  flow from the landing page. Reference dashboards by their stable `uid`, not by
  an auto-generated id.
- **Service-graph panel.** Add a node-graph (or equivalent) panel that renders
  the critical-flow service graph from the Tempo-generated
  `traces_service_graph_*` series. Prefer a `nodegraph` panel backed by the
  Prometheus `traces_service_graph_request_total` / `*_total` series (the
  service map is already bound to Prometheus by `tempo-service-graph`); confirm
  at implementation time which form renders the connected edge set in this
  Grafana version.
- **Live top-level health panels.** Add a small set of at-a-glance stat / time
  series panels spanning the flows (e.g. ingestion throughput, websocket
  connections, CDC publish rate, Redis fan-out, replication lag) sourced from
  the existing Prometheus series the per-flow dashboards already use, so the
  overview populates with live k6-driven data.
- **Bind datasources by fixed uid.** Every metric panel binds `datasource` to
  uid `prometheus`; any trace/service-map panel binds to uid `tempo`, matching
  every other dashboard, so the overview survives `docker compose down/up`.
- Confirm the **exact Prometheus series names and service-graph label values**
  empirically against the running Prometheus
  (`/api/v1/label/__name__/values` or `/api/v1/series`) before wiring panel
  queries — names are OTel-normalized and the graph labels are produced at
  runtime, so the panels bind to what Prometheus actually exposes, not an
  assumption.

## Design

- **Vertical-slice scope — one JSON file.** This change adds a single dashboard
  file and nothing else. It does not touch the seven per-flow dashboards, the
  Tempo `metrics_generator` / service-graph config (owned by `tempo-service-graph`,
  complete), the datasource or provider provisioning, or any service
  instrumentation. It is the smallest change that closes the capstone's "overview
  dashboard linking every flow + service-graph view" requirement.
- **Title is the proof contract.** The capstone proof hits
  `GET /api/search?type=dash-db` and expects all eight titles, so this
  dashboard's `title` must be exactly `Fleet Observability Overview`. The file
  name and any folder grouping are incidental; the title is load-bearing.
- **Reuse the existing graph, don't regenerate it.** The service-graph data
  already exists — `tempo-service-graph` generates `traces_service_graph_*` and
  bound the Tempo service map to Prometheus. The overview only *renders* that
  graph; it adds no generator, connector, or datasource config.
- **Link by fixed uid.** Cross-dashboard links resolve the per-flow dashboards
  by their pinned `uid` (or a shared `tag`), consistent with how datasources are
  bound, so links keep working across `docker compose down/up`.
- **Reuse, don't reinvent, the Grafana model.** Match the existing dashboards
  under `docker/grafana/dashboards/` (schemaVersion 39, idiomatic time-series /
  stat panels, datasource by uid) and use the project `opentelemetry` skill for
  Grafana-stack panel and node-graph conventions rather than hand-rolling unusual
  config.
- **Runtime-only, harness untouched.** The only addition is a file under
  `docker/grafana/dashboards/`; `docker-compose.test.yml` and the pytest/vitest
  suites are not touched and stay green.
- **Proof-of-work for this change == the overview slice of the phase proof.**
  From a clean `docker compose up -d --wait` with k6 driving load and the SPA
  loaded once, `GET /api/search?type=dash-db` returns all eight titles (including
  "Fleet Observability Overview"), the overview's service-graph panel renders the
  connected critical-flow edges from `traces_service_graph_request_total`, its
  links resolve to the per-flow dashboards, and its health panels show live data
  — with no manual steps. The full judged end-goal acceptance is owned by
  `capstone-clean-boot-verify`.

## Tasks

- [x] 1 Bring the stack up (`docker compose up -d --wait`), drive k6 load and
      load the SPA once, then read the **real Prometheus series names** for the
      headline panels and the **service-graph series/labels**
      (`traces_service_graph_request_total` plus its `client`/`server` label
      values) via `/api/v1/label/__name__/values` and `/api/v1/series`; record
      the `uid`s of the seven per-flow dashboards from
      `GET /api/search?type=dash-db` for the cross-links.
- [x] 2 Author `docker/grafana/dashboards/fleet-overview.json` as a valid
      Grafana dashboard model with `title: "Fleet Observability Overview"`,
      matching the existing dashboards' schema (schemaVersion 39); bind every
      panel's `datasource` to uid `prometheus` (and `tempo` where a trace/service
      source is used).
- [x] 3 Add dashboard **links to every per-flow dashboard** (by uid and/or a
      shared tag), so the overview navigates to "Ingestion API", "Frontend API &
      WebSockets", "CDC Consumer", "Pub/Sub", "Redis Fan-out", "Primary/Replica
      Streaming", and "Frontend Web (Browser)".
- [x] 4 Add the **service-graph panel** rendering the critical-flow graph from
      the Tempo-generated `traces_service_graph_*` series (nodegraph or
      equivalent), and confirm it shows the ingestion, cdc/Redis pub/sub,
      replica-read, and browser -> frontend-api edges connected.
- [x] 5 Add the **live top-level health panels** (e.g. ingestion throughput,
      websocket connections, CDC publish rate, Redis fan-out, replication lag)
      querying the existing Prometheus series, confirmed empirically.
- [x] 6 Clean-boot the stack (`docker compose up -d --wait`) and confirm Grafana
      auto-provisions the overview: `GET /api/search?type=dash-db` returns all
      eight titles including "Fleet Observability Overview", and startup logs show
      no provisioning errors for it.
- [x] 7 With k6 driving load and the SPA loaded once, confirm the overview
      populates: the service-graph panel renders the connected critical flows,
      the cross-links resolve to the per-flow dashboards, and the health panels
      read live data.
- [x] 8 Confirm `docker-compose.test.yml` is unchanged and the existing
      pytest/vitest suites still pass.
