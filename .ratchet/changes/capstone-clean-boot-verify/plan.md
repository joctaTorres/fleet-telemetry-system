# capstone-clean-boot-verify

## Why

This is the final change of the `all-dashboards-preloaded-capstone` phase
(`after: [fleet-overview-dashboard]`) and the capstone of the whole
`fleet-otel-observability` batch. The phase goal is to deliver the end goal as a
single experience: a clean `docker compose up` brings the entire stack with ALL
required dashboards preloaded and real-time OTel data flowing from the k6 fleet
simulation, with a top-level "Fleet Observability Overview" dashboard linking
every flow and a Tempo service-graph view of all critical flows — and nothing
requiring manual import or post-up wiring.

Every building block is already in place from upstream changes:

- The seven per-flow dashboards auto-provision from `docker/grafana/dashboards/`
  (Ingestion API, Frontend API & WebSockets, CDC Consumer, Pub/Sub, Redis
  Fan-out, Primary/Replica Streaming, Frontend Web (Browser)).
- `tempo-service-graph` (complete) has Tempo's `metrics_generator` emitting
  `traces_service_graph_request_total` over the critical flows, with the Tempo
  datasource service map wired to Prometheus.
- `fleet-overview-dashboard` (complete) added the eighth dashboard,
  "Fleet Observability Overview", with links to every flow, a service-graph
  panel, and live health panels.

What is missing is the **proof, as one experience**: a single judged clean boot
that demonstrates the end goal actually holds with zero manual steps. That is
this change. The vertical slice is the clean-boot acceptance run itself — the
thinnest thing that proves the entire stack end to end — and the only code this
change writes is whatever minimal fix the clean boot reveals as still broken.

## What Changes

This is a verification/acceptance change, not a feature build. Its primary
deliverable is a reproducible, judged clean-boot run plus the evidence it
gathers; it touches code only to close gaps the clean boot surfaces.

- Run the judged end-goal acceptance from a truly clean state:
  `docker compose down -v` then `docker compose up -d --wait`, with no manual
  steps.
- Drive the k6 fleet simulation for ~90s and load the SPA once in a headless
  browser so every flow (ingestion, CDC, Redis fan-out, frontend/WebSockets,
  replica reads, and the browser) produces live data and traces.
- Gather the acceptance evidence:
  - Grafana dashboard list via `GET /api/search?type=dash-db` — expect all
    eight titles.
  - Each dashboard's panel data (confirm non-empty for the load window).
  - The Tempo service-graph metrics: query Prometheus for
    `traces_service_graph_request_total` and read the actual `client`/`server`
    edge set empirically.
  - Connected traces: an ingestion -> cdc -> redis -> frontend trace for one
    telemetry write, and a browser (`fleet-dashboard-web`) -> frontend-api
    trace from the SPA load.
  - Dashboard screenshots for the judge.
- Have the llm-judge assess whether the end goal holds (all 8 dashboards
  auto-preloaded and live, service graph covers all critical flows, all flows
  traceable end to end, zero manual setup).
- **Close only the gaps the clean boot reveals.** If a dashboard does not
  populate, a service-graph edge is missing, a cross-link is broken, a flow is
  not traceable, or a service is not ready at `--wait`, apply the minimal fix
  (e.g. a provisioning, datasource, compose healthcheck/depends_on, or query
  correction) and re-run the clean boot. Do not re-author the upstream
  dashboards or re-instrument services unless the clean boot proves a concrete
  gap. If the clean boot already passes, no code change is required and the
  gathered evidence is the proof.
- Leave `docker-compose.test.yml` and the pytest/vitest suites untouched; they
  stay green.

## Design

- **Vertical-slice scope — prove the whole, fix only the seams.** The end goal
  is an integration property: it can only be proven by booting the whole stack
  clean and watching live data flow. So the slice is the acceptance run itself,
  end to end across every service, dashboard, metric, and trace — the smallest
  thing that exercises the entire stack for the phase goal. Code changes are
  deliberately minimal and gap-driven, because every feature already exists; the
  capstone's job is to verify they compose into one clean-boot experience and to
  fix the seams (provisioning, ordering, datasource/uid binding, healthchecks)
  if they don't.
- **Clean state is load-bearing.** Use `docker compose down -v` to drop named
  volumes so the boot is genuinely fresh (no leftover Grafana DB, Tempo WAL, or
  Postgres state). The whole point is to prove preloading and auto-provisioning,
  which a dirty volume would mask.
- **Read empirically, never assume.** Dashboard titles come from the live
  `/api/search`, the service-graph edge set and its `client`/`server` labels
  come from the running Prometheus, and trace connectivity comes from actual
  Tempo search results. OTel-normalized names and runtime-produced labels are
  confirmed against what the stack actually exposes.
- **Memory-aware.** The full stack plus k6 plus the Tempo metrics_generator runs
  together on the 4 GiB Docker VM; confirm Tempo stays healthy under sustained
  k6 load during the ~90s window (it held well clear of OOM in
  `tempo-service-graph`).
- **Judged proof == the phase proof-of-work.** This change's proof-of-work is
  exactly the phase's llm-judge acceptance: from one clean
  `docker compose down -v` + `up -d --wait`, ~90s of k6, and one SPA load, the
  judge confirms all eight dashboards are auto-preloaded and live, the Tempo
  service graph covers all critical flows, every flow is traceable end to end,
  and there was zero manual setup beyond `docker compose up`.
- **Harness untouched.** Only runtime/provisioning seams are eligible to change;
  `docker-compose.test.yml` and the pytest/vitest suites are not touched and
  stay green.

## Tasks

- [x] 1 From a clean state, run `docker compose down -v` then
      `docker compose up -d --wait` with no manual steps and confirm every
      service reaches healthy/ready within the compose wait (Grafana,
      Prometheus, Tempo, Postgres primary/replica, Redis, ingestion-api,
      cdc-consumer, frontend-api, web, and the k6 simulation).
- [x] 2 Drive the k6 fleet simulation for ~90s and load the SPA once in a
      headless browser so every flow produces live data and traces.
- [x] 3 Query Grafana `GET /api/search?type=dash-db` and confirm all eight
      dashboard titles are present and auto-provisioned (Ingestion API,
      Frontend API & WebSockets, CDC Consumer, Pub/Sub, Redis Fan-out,
      Primary/Replica Streaming, Frontend Web (Browser), Fleet Observability
      Overview).
- [x] 4 Confirm each dashboard's panels return non-empty data for the load
      window, including the Frontend Web (Browser) dashboard showing
      browser-sourced telemetry and the Overview showing live health signals
      with working per-flow links.
- [x] 5 Query Prometheus for `traces_service_graph_request_total` and confirm,
      empirically from the actual `client`/`server` labels, that the edge set
      covers all critical flows (ingestion-api -> db, cdc-consumer ->
      frontend-api Redis pub/sub hop, frontend-api -> replica,
      fleet-dashboard-web -> frontend-api).
- [x] 6 Search Tempo and confirm end-to-end traceability: a connected
      ingestion -> cdc -> redis -> frontend trace for one telemetry write, and a
      connected browser (`fleet-dashboard-web`) -> frontend-api trace from the
      SPA load.
- [x] 7 Capture dashboard screenshots and assemble the full evidence bundle
      (dashboard list, per-dashboard panel data, service-graph metrics,
      connected traces, screenshots) for the judge.
- [x] 8 Have the llm-judge assess the end goal and confirm it holds: all eight
      dashboards auto-preloaded and live, service graph covers all critical
      flows, all flows traceable end to end, zero manual setup beyond
      `docker compose up`.
- [x] 9 If the clean boot reveals any gap (dashboard not populating, missing
      service-graph edge, broken link, untraceable flow, or a service not ready
      at `--wait`), apply the minimal fix, record it, and re-run the clean boot
      until the judge confirms the end goal — without re-authoring the upstream
      changes.
- [x] 10 Confirm `docker-compose.test.yml` is unchanged and the existing
      pytest/vitest suites still pass.
