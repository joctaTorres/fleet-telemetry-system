# cdc-pubsub-redis-dashboards

## Why

This is the **last** change of the `cdc-pubsub-redis-flow` phase and the one that
makes the asynchronous critical path *visible*. The two upstream changes are
done: `instrument-cdc-consumer` made the CDC consumer observable (it emits
`cdc.decode` and per-event `cdc.publish` spans under `service.name=cdc-consumer`,
plus a `cdc.events_published` counter and a `cdc.decode.lag` (ms) histogram, each
keyed by `cdc.event_type`), and `propagate-trace-context-redis` threaded the W3C
traceparent through the `fleet:events` envelope so a single k6 telemetry write
now yields ONE Tempo trace whose spans include both `service.name=cdc-consumer`
and `service.name=frontend-api`. The frontend side already emits
`redis.subscribe` / `ws.broadcast` spans, a `frontend.ws.broadcasts` counter, and
a `frontend.ws.active_connections` observable gauge.

Everything needed to *see* the flow exists except the views themselves. This
change owns exactly that: author the **"CDC Consumer"**, **"Pub/Sub"**, and
**"Redis Fan-out"** Grafana dashboard JSON and drop them into the provisioned
folder so a clean `docker compose up` preloads them and, with k6 driving
telemetry writes, their event-throughput / decode-lag / publish-subscribe /
fan-out / active-client panels populate from Prometheus and their trace panels
show the live connected cdc-consumer ↔ frontend-api trace from Tempo. With these
in place the phase proof-of-work passes end to end.

## What Changes

- Add three Grafana dashboard models under `docker/grafana/dashboards/` with
  titles exactly `CDC Consumer`, `Pub/Sub`, and `Redis Fan-out` (the exact
  strings the phase proof searches for), picked up automatically by the existing
  file provider; no compose or provisioning change is required because the mount
  and provider already exist.
- Bind every panel to the **provisioned datasource uids** (`prometheus` for
  metrics, `tempo` for traces) rather than to Grafana auto-generated ids, so the
  dashboards are reproducible across `docker compose down/up`.
- **CDC Consumer** dashboard panels:
  - **Event throughput** — time series of `rate(...)` over the Prometheus series
    derived from `cdc.events_published`, broken down by / filterable on
    `cdc.event_type`.
  - **Decode lag** — at least one quantile (e.g. p95) via `histogram_quantile`
    over the buckets derived from the `cdc.decode.lag` (ms) histogram.
  - **Traces** — a Tempo-backed panel listing recent `service.name=cdc-consumer`
    traces, drillable into the `cdc.decode` / `cdc.publish` spans.
- **Pub/Sub** dashboard panels:
  - **Publish rate** — rate over `cdc.events_published` (publishes onto the
    `fleet:events` channel).
  - **Subscribe / delivery rate** — rate over `frontend.ws.broadcasts` (the
    frontend subscriber fanning each message out).
  - **Connected trace** — a Tempo-backed panel surfacing the single trace whose
    spans include BOTH `service.name=cdc-consumer` and `service.name=frontend-api`,
    making the pub/sub hop visible as one trace.
- **Redis Fan-out** dashboard panels:
  - **Fan-out delivery rate** — rate over `frontend.ws.broadcasts`.
  - **Active connections** — current value of the
    `frontend.ws.active_connections` gauge.
  - **Dropped clients** — best-effort, derived from drops in the
    active-connections gauge (there is no dedicated dropped-client counter to
    bind to; surface what exists rather than inventing a new instrument).
- Derive the **exact Prometheus metric names** empirically at implementation
  time. OTLP → Alloy → Prometheus remote-write rewrites OTel names (dots to
  underscores, counter `_total` suffix, histogram unit + `_bucket`/`_sum`/
  `_count`); rather than guessing, bring the stack up with k6 and query
  Prometheus (`/api/v1/label/__name__/values` or `/api/v1/series`) to read the
  real series names, then wire the panel queries to them.

## Design

- **Vertical-slice scope — JSON only.** This change adds three dashboard files
  and nothing else. It does **not** touch the cdc-consumer or frontend
  instrumentation, the Redis trace-context propagation, the datasource/provider
  provisioning, or the backbone services — those are owned by the upstream
  changes and are already complete. It is the smallest change that turns the
  already-flowing telemetry into the three visible dashboards the phase goal
  requires.
- **Bind by fixed uid, not by id or name.** `grafana-provisioning` deliberately
  pinned `uid: tempo` and `uid: prometheus` so dashboards are portable; this
  change relies on that and references those uids directly in every panel's
  `datasource`. No auto-generated ids leak into the JSON. Mirrors how
  `ingestion-dashboard` and `frontend-api-dashboard` were authored.
- **Names are stable upstream, translated downstream.** The metric *instrument*
  names (`cdc.events_published`, `cdc.decode.lag`, `frontend.ws.broadcasts`,
  `frontend.ws.active_connections`), the `cdc.event_type` label, and the span
  service names (`cdc-consumer`, `frontend-api`) are contracts fixed upstream;
  the dashboards bind to those. The *Prometheus* series names are a function of
  the OTLP→Prometheus naming convention, so they are read from the running
  Prometheus rather than assumed.
- **Titles are the proof contract.** The phase proof hits
  `GET /api/search?query=...` for `CDC%20Consumer`, `Pub%2FSub`, and
  `Redis%20Fan-out`, so the dashboard `title` fields must be exactly
  `CDC Consumer`, `Pub/Sub`, and `Redis Fan-out`. File names and folder grouping
  are incidental; the titles are load-bearing.
- **Surface what exists; don't invent instruments.** "Dropped clients" in the
  phase goal has no dedicated counter; rather than adding a new instrument (which
  would expand scope into instrumentation), the Redis Fan-out dashboard derives
  it best-effort from the active-connections gauge. If a panel cannot be
  faithfully bound to a live series it is `log()`-noted, not faked.
- **Reuse, don't reinvent, the Grafana model.** Use a standard Grafana dashboard
  JSON model (schema version compatible with the pinned Grafana image), idiomatic
  time-series and table/traces panels, and the project `opentelemetry` skill for
  Grafana-stack panel + Tempo query conventions.
- **Runtime-only, harness untouched.** The only additions are files under
  `docker/grafana/dashboards/`; `docker-compose.test.yml` and the pytest/vitest
  suites are not touched and stay green.
- **Proof-of-work for this change == the phase proof.** As the closing change of
  the phase, its proof is the phase blackbox: from a clean
  `docker compose up -d --wait` with k6 running, Tempo returns ≥1 trace whose
  spans include BOTH `resource.service.name=cdc-consumer` and
  `=frontend-api`, and a dashboard search for each of "CDC Consumer", "Pub/Sub",
  and "Redis Fan-out" returns ≥1 result — with their panels populated.

## Tasks

- [x] 1. Bring the stack up with k6 driving telemetry writes and read the **real
      Prometheus series names** for `cdc.events_published`, `cdc.decode.lag`,
      `frontend.ws.broadcasts`, and `frontend.ws.active_connections` (query
      `/api/v1/label/__name__/values` or `/api/v1/series`), recording the exact
      names and label keys the panel queries will use.
- [x] 2. Author `docker/grafana/dashboards/cdc-consumer.json` as a valid Grafana
      dashboard model with `title: "CDC Consumer"`; bind metric panels to uid
      `prometheus` and the trace panel to uid `tempo`.
- [x] 3. CDC Consumer panels: **event throughput** (`rate(...)` over
      `cdc.events_published`, broken down by/filterable on `cdc.event_type`),
      **decode lag** (a quantile via `histogram_quantile` over `cdc.decode.lag`
      ms buckets), and **traces** (Tempo, recent `service.name=cdc-consumer`,
      drillable into `cdc.decode`/`cdc.publish`).
- [x] 4. Author `docker/grafana/dashboards/pubsub.json` with `title: "Pub/Sub"`;
      panels for **publish rate** (over `cdc.events_published`), **subscribe /
      delivery rate** (over `frontend.ws.broadcasts`), and a **connected-trace**
      Tempo panel surfacing the single trace spanning BOTH
      `service.name=cdc-consumer` and `service.name=frontend-api`.
- [x] 5. Author `docker/grafana/dashboards/redis-fan-out.json` with
      `title: "Redis Fan-out"`; panels for **fan-out delivery rate** (over
      `frontend.ws.broadcasts`), **active connections** (from
      `frontend.ws.active_connections`), and a **dropped-clients** panel
      (best-effort, derived from the active-connections gauge — note the
      limitation rather than inventing a counter).
- [x] 6. Clean-boot the stack (`docker compose up -d --wait`) and confirm Grafana
      auto-provisions all three: searches for `CDC Consumer`, `Pub/Sub`, and
      `Redis Fan-out` each return ≥1 result and startup logs show no provisioning
      errors for them.
- [x] 7. With k6 driving telemetry writes (and ≥1 WebSocket client connected for
      the fan-out panels), confirm every panel populates with live data by
      inspecting panel data / the rendered dashboards.
- [x] 8. Run the **phase proof-of-work** end to end: Tempo search returns ≥1
      trace whose spans include BOTH `resource.service.name=cdc-consumer` and
      `=frontend-api`, AND the three dashboard searches each return ≥1 result;
      confirm it exits 0.
- [x] 9. Confirm scope boundaries: only the three dashboard JSON files are added
      under `docker/grafana/`; no instrumentation, propagation, provisioning, or
      backbone change. Confirm `docker-compose.test.yml` is unchanged and the
      existing pytest/vitest suites still pass.
