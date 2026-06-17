# replication-dashboard

## Why

This change is the view half of the replication flow in the
`replication-and-browser-traces` phase. Its upstream, `replication-lag-metric`,
is complete: a `python -m app.replication_probe` service samples
`pg_stat_replication` on the primary and `pg_last_xact_replay_timestamp()` on
the replica and emits two custom OTel gauges that land in Prometheus — by
design with no unit suffix — as exactly `pg_replication_lag_bytes` (primary
view) and `pg_replication_lag_seconds` (replica view), each tagged with a
per-standby label. The metric is queryable but nothing visualizes it.

This change owns exactly that: author the **"Primary/Replica Streaming"**
Grafana dashboard JSON and drop it into the already-provisioned dashboards
folder so a clean `docker compose up` preloads it and, with the probe sampling,
its byte-lag and seconds-lag panels populate from Prometheus. With this in
place the replication (byte-lag) half of the phase proof-of-work passes end to
end; the browser-trace half is owned by the separate `instrument-browser-web`
and `browser-web-dashboard` changes.

## What Changes

- Add `docker/grafana/dashboards/primary-replica-streaming.json` — a Grafana
  dashboard model with `title: "Primary/Replica Streaming"` (the exact string
  the phase proof searches for, URL-encoded as `Primary%2FReplica%20Streaming`),
  picked up automatically by the existing file provider; no compose or
  provisioning change is required because the mount and provider already exist.
- Bind every panel's `datasource` to the **provisioned Prometheus uid**
  (`prometheus`) rather than a Grafana auto-generated id, so the dashboard is
  reproducible across `docker compose down/up`.
- Panels:
  - **Byte lag over time** — time series of `pg_replication_lag_bytes`, broken
    down by / filterable on the per-standby label so each connected standby is
    a distinct series.
  - **Seconds lag over time** — time series of `pg_replication_lag_seconds`.
  - **Current lag stat** — a stat (or gauge) panel showing
    `max(pg_replication_lag_bytes)` and/or `max(pg_replication_lag_seconds)` so
    the headline number matches the phase proof query.
- Confirm the **exact Prometheus series names** empirically at implementation
  time by querying the running Prometheus
  (`/api/v1/label/__name__/values` or `/api/v1/series`) before wiring the panel
  queries — the metric was deliberately authored unit-free to land as
  `pg_replication_lag_bytes` / `pg_replication_lag_seconds`, but the panels bind
  to what Prometheus actually exposes, not to an assumption.

## Design

- **Vertical-slice scope — JSON only.** This change adds one dashboard file and
  nothing else. It does not touch the replication probe / metric, the datasource
  or provider provisioning, or any service instrumentation — those are owned by
  upstream changes and are already complete. It is the smallest change that
  turns the already-flowing replication-lag series into the visible dashboard
  the phase goal requires.
- **Bind by fixed uid, not by id or name.** `grafana-provisioning` pinned
  `uid: prometheus` so dashboards are portable; this change references that uid
  directly in every panel's `datasource`. No auto-generated ids leak in.
- **Title is the proof contract.** The phase proof-of-work hits
  `GET /api/search?query=Primary%2FReplica%20Streaming`, so the dashboard
  `title` must be exactly `Primary/Replica Streaming`. The file name and any
  folder grouping are incidental; the title is load-bearing.
- **Series names are stable upstream, translated downstream.** The metric
  instrument names are a contract fixed by `replication-lag-metric` (authored
  unit-free precisely to avoid a `_ratio`/unit suffix), but the panels still
  read the live `__name__` values from the running Prometheus before binding, so
  they stay correct against whatever Alloy actually remote-writes.
- **Reuse, don't reinvent, the Grafana model.** Match the existing dashboards
  under `docker/grafana/dashboards/` (schemaVersion 39, idiomatic time-series
  and stat panels, datasource by uid) and use the project `opentelemetry` skill
  for Grafana-stack panel conventions rather than hand-rolling unusual config.
- **Runtime-only, harness untouched.** The only addition is a file under
  `docker/grafana/dashboards/`; `docker-compose.test.yml` and the pytest/vitest
  suites are not touched and stay green.
- **Proof-of-work for this change == the replication half of the phase proof.**
  From a clean `docker compose up -d --wait`, `max(pg_replication_lag_bytes)`
  returns a non-null value in Prometheus and a dashboard search for
  "Primary/Replica Streaming" returns ≥1 result, with the panels populated.

## Tasks

- [x] 1 Bring the stack up (`docker compose up -d --wait`) and read the **real
      Prometheus series names** for the replication-lag gauges (query
      `/api/v1/label/__name__/values` or `/api/v1/series`), confirming
      `pg_replication_lag_bytes` / `pg_replication_lag_seconds` and recording the
      per-standby label key the panel queries will use.
      (Confirmed: `pg_replication_lag_bytes` carries label `application_name`
      with values `walreceiver`/`standby`; `pg_replication_lag_seconds` has no
      per-standby label — single series tagged `job=replication-probe`.)
- [x] 2 Author `docker/grafana/dashboards/primary-replica-streaming.json` as a
      valid Grafana dashboard model with `title: "Primary/Replica Streaming"`,
      matching the existing dashboards' schema; bind every panel's `datasource`
      to uid `prometheus`.
- [x] 3 Add the **byte-lag** time-series panel querying
      `pg_replication_lag_bytes`, broken down by / filterable on the per-standby
      label. (`max by (application_name) (pg_replication_lag_bytes)`.)
- [x] 4 Add the **seconds-lag** time-series panel querying
      `pg_replication_lag_seconds`.
- [x] 5 Add the **current-lag stat** panel using
      `max(pg_replication_lag_bytes)` and/or `max(pg_replication_lag_seconds)`
      so the headline matches the phase proof query. (Two stat panels:
      `max(pg_replication_lag_bytes)` and `max(pg_replication_lag_seconds)`.)
- [x] 6 Clean-boot the stack (`docker compose up -d --wait`) and confirm Grafana
      auto-provisions the dashboard: `GET /api/search?query=Primary%2FReplica%20Streaming`
      returns ≥1 result and startup logs show no provisioning errors for it.
- [x] 7 With the probe sampling, confirm the panels populate (byte lag, seconds
      lag, current-lag stat) by inspecting panel data / the rendered dashboard.
      (Each panel's exact query returns live data from Prometheus.)
- [x] 8 Run the **replication half of the phase proof-of-work**: confirm
      `max(pg_replication_lag_bytes)` returns a non-null value in Prometheus and
      the "Primary/Replica Streaming" dashboard search returns ≥1 result.
- [x] 9 Confirm `docker-compose.test.yml` is unchanged and the existing
      pytest/vitest suites still pass. (Harness file unchanged; full pytest is
      green — 130 pass — once the test-stack `cdc` service is up; the 5
      realtime_ws failures seen with bare `run --rm api` are the known
      cdc-not-started harness gap, not this change.)
