# tempo-service-graph

## Why

This is the first change of the `all-dashboards-preloaded-capstone` phase. The
phase goal requires "a Tempo service-graph view of all critical flows" so an
operator can see, in one graph, how the fleet flows connect: browser ->
frontend-api, ingestion-api -> storage, and the asynchronous cdc-consumer ->
(Redis pub/sub) -> frontend-api hop. The capstone proof-of-work explicitly
queries Prometheus for `traces_service_graph_request_total` and expects the
graph to cover all critical flows.

Today Tempo (grafana/tempo:2.7.2, single-binary, local storage) is
receive-and-store only: Alloy forwards traces to it, but Tempo derives no
metrics, so no service-graph series exist. The end-to-end trace context is
already in place from earlier phases — a single k6-driven write yields connected
ingestion-api / cdc-consumer / frontend-api traces, and the browser SDK
(`service.name=fleet-dashboard-web`) emits connected spans against frontend-api.
All the inputs the service graph needs already flow; only the generator is
missing.

This change owns exactly that thin slice: turn on Tempo's `metrics_generator`
with the `service-graphs` processor, remote-write the generated series to the
already-open Prometheus remote-write receiver, and wire the Tempo datasource's
service map to Prometheus so Grafana's Explore can render the graph. It does
**not** author any dashboard — the "Fleet Observability Overview" dashboard and
its node-graph panel are owned by the downstream `fleet-overview-dashboard`
change. The vertical slice proves the goal end to end by making
`traces_service_graph_request_total` queryable and connected across the critical
flows after a clean `docker compose up`.

## What Changes

- Edit `docker/tempo/tempo.yaml` to add a `metrics_generator` block:
  - enable the `service-graphs` processor,
  - give the generator a local WAL/storage path (under the existing
    `/var/tempo` named volume),
  - `remote_write` the generated series to `http://prometheus:9090/api/v1/write`,
  - add `overrides` (the Tempo 2.7 nested `defaults.metrics_generator.processors`
    form) enabling `service-graphs` by default so the distributor feeds received
    traces to the generator.
- Keep the Tempo image pinned to `grafana/tempo:2.7.2` — do **not** move to 3.0
  (distroless: no shell for the compose healthcheck, plus changed config keys).
- Wire the provisioned Tempo datasource's service map to Prometheus: add
  `jsonData.serviceMap.datasourceUid: prometheus` to the Tempo datasource in
  `docker/grafana/provisioning/datasources/datasources.yaml` so Grafana's
  Explore service-graph / node-graph view resolves the `traces_service_graph_*`
  series. This is the only datasource change.
- Apply minimal, semantic-convention-correct span-kind corrections so Tempo's
  service-graphs processor (which only pairs client/server or producer/consumer
  spans) can draw the asynchronous and db edges of the critical flows. The
  connected end-to-end traces already exist from earlier phases; only the span
  *kinds* change — never the trace parent/child structure (the phase-3
  connected-trace proof must still pass):
  - `app/cdc_consumer.py`: the `cdc.publish` span becomes `SpanKind.PRODUCER`
    (it already carries `messaging.system=redis` / `messaging.destination`).
  - `app/frontend_api.py`: the `redis.subscribe` span becomes
    `SpanKind.CONSUMER` (same `messaging.*` attributes) — this producer→consumer
    pair makes Tempo render the cdc-consumer → frontend-api pub/sub edge.
  - `app/ingestion_api.py`: the Postgres write is wrapped in a
    `SpanKind.CLIENT` span carrying `db.system=postgresql` and
    `server.address=db`, so a virtual db node forms the ingestion-api → db edge.
  - `app/frontend_api.py`: the `replica.read` span becomes `SpanKind.CLIENT`
    with `db.system=postgresql` / `server.address=replica`, so frontend-api →
    replica renders.
- No Alloy pipeline, Prometheus scrape config, or dashboard JSON is touched, and
  the trace parent/child structure is unchanged.

> **Scope note (expanded during apply).** This change originally declared "no
> service instrumentation is touched." Verification found the generator + config
> render the browser → frontend-api and virtual entry edges, but the async
> cdc → frontend pub/sub hop and the ingestion/frontend db hops never became
> service-graph *edges*: the connected spans were all `INTERNAL`, and Tempo's
> service-graphs processor only builds edges from client/server or
> producer/consumer pairs. To meet the phase goal ("a Tempo service-graph view
> of all critical flows"), scope was expanded — per an explicit decision — to the
> minimal span-kind corrections above. They are OTel-semantic-convention-correct
> and leave trace structure untouched.

## Design

- **Vertical-slice scope — data, not view.** This change produces and exposes
  the service-graph *metrics* (and makes them renderable via the Tempo
  datasource); it authors no dashboard. The Overview dashboard with its
  node-graph panel is the next change (`fleet-overview-dashboard`,
  `after: [tempo-service-graph]`). Smallest change that proves the graph exists
  and connects the flows.
- **Tempo-native generation, not an Alloy connector.** The metric name the proof
  expects — `traces_service_graph_request_total` — is what Tempo's
  `service-graphs` processor emits. Generating it inside Tempo keeps the trace
  backend the single source of the graph and reuses the already-open Prometheus
  remote-write receiver, rather than adding a servicegraph connector to the Alloy
  pipeline.
- **Edges come from the live spans.** Service-graph edges are built from
  client/server (and producer/consumer) span pairs that already span services
  thanks to existing context propagation — including the cdc-consumer ->
  frontend-api hop across `fleet:events`. Because Tempo's processor only pairs
  client/server or producer/consumer spans, the async pub/sub hop and the db
  hops required correcting the relevant spans' *kinds* (publish→PRODUCER,
  subscribe→CONSUMER, db write/read→CLIENT with a peer `server.address`); the
  trace structure is untouched. The exact `service.name` label values and the
  full edge set are read empirically from the running Prometheus, not assumed.
- **Reuse fixed uids.** The Tempo datasource service-map binds to the
  Prometheus datasource by its pinned uid `prometheus`, consistent with how every
  dashboard binds datasources, so it survives `docker compose down/up`.
- **Memory-aware.** The generator adds memory pressure to Tempo, which has
  OOM-killed under k6 load on the small Docker VM before; the VM is now 4 GiB.
  Bring the stack up under load and confirm Tempo stays healthy; if needed, apply
  minimal memory-bounding generator settings and note them for the capstone
  verify.
- **Runtime-only, harness untouched.** Only the runtime Tempo/datasource config
  changes; `docker-compose.test.yml` and the pytest/vitest suites are not
  touched and stay green.
- **Proof-of-work for this change.** From a clean `docker compose up -d --wait`
  with k6 driving load, Prometheus returns ≥1 `traces_service_graph_request_total`
  series with `client`/`server` labels, the edge set covers the critical flows
  (including browser -> frontend-api), and the Tempo datasource's serviceMap is
  wired to Prometheus — all with no manual steps.

## Tasks

- [x] 1 Add a `metrics_generator` block to `docker/tempo/tempo.yaml`: enable the
      `service-graphs` processor, set a local WAL/storage path under `/var/tempo`,
      and `remote_write` to `http://prometheus:9090/api/v1/write`.
- [x] 2 Add the Tempo `overrides` (2.7 nested `defaults.metrics_generator.processors`
      form) enabling `service-graphs` by default so received traces reach the
      generator; keep the image pinned to `grafana/tempo:2.7.2`.
- [x] 3 Wire the Tempo datasource service map to Prometheus by adding
      `jsonData.serviceMap.datasourceUid: prometheus` to the Tempo datasource in
      `docker/grafana/provisioning/datasources/datasources.yaml`.
- [x] 4 Clean-boot the stack (`docker compose up -d --wait`) and confirm Tempo
      starts the generator from its mounted config, reports ready on :3200, and
      logs no fatal config/generator errors.
- [x] 5 Drive k6 load long enough for traces to flow, then query Prometheus for
      `traces_service_graph_request_total` and confirm ≥1 series with `client`
      and `server` labels. (6 series after ~90s of k6 + one SPA load.)
- [x] 6 Read the edge set empirically and confirm it covers the critical flows
      (browser -> frontend-api, the cdc-consumer -> frontend-api pub/sub hop, and
      ingestion -> storage as instrumented); record the actual `service.name`
      label values seen. Edges seen: `cdc-consumer -> frontend-api`,
      `ingestion-api -> db`, `frontend-api -> replica`,
      `fleet-dashboard-web -> frontend-api`, plus virtual entry edges
      `user -> ingestion-api` and `user -> frontend-api`.
- [x] 7 Confirm Tempo stays healthy under sustained k6 load with the generator
      running (no OOM on the 4 GiB VM); if memory-bounding settings are applied,
      note them for the capstone verify. (Tempo held ~88 MiB / 3.8 GiB under
      load — well clear of OOM; no memory-bounding settings needed.)
- [x] 8 Confirm `docker-compose.test.yml` is unchanged and the existing
      pytest/vitest suites still pass. (test compose untouched; vitest 30/30;
      pytest green with the `cdc` service up — the realtime_ws e2e tests require
      it, per the known `run --rm api` quirk.)
- [x] 9 (scope-expansion) Set the `cdc.publish` span to `SpanKind.PRODUCER` in
      `app/cdc_consumer.py` and the `redis.subscribe` span to
      `SpanKind.CONSUMER` in `app/frontend_api.py` (keeping their `messaging.*`
      attributes) so Tempo renders the cdc-consumer → frontend-api pub/sub edge.
- [x] 10 (scope-expansion) Wrap the ingestion Postgres write in a
      `SpanKind.CLIENT` span (`db.system=postgresql`, `server.address=db`) in
      `app/ingestion_api.py`, and make the frontend `replica.read` a
      `SpanKind.CLIENT` span (`db.system=postgresql`, `server.address=replica`),
      so the ingestion-api → db and frontend-api → replica edges form — without
      altering trace parent/child structure.
- [x] 11 (scope-expansion) Add/adjust in-process unit tests asserting the new
      span kinds (cdc PRODUCER, subscribe CONSUMER, ingestion db CLIENT, replica
      CLIENT) — no collector required.
- [x] 12 (scope-expansion) Verify in one clean boot that
      `traces_service_graph_request_total` includes the cdc-consumer →
      frontend-api, ingestion-api → db, and fleet-dashboard-web → frontend-api
      edges, and that the phase-3 connected trace ({cdc-consumer} &&
      {frontend-api}) still returns ≥1. (All three edges present; connected
      trace search returned 5 traces — parent/child structure preserved.)
