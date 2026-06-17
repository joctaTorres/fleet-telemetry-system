# replication-lag-metric

## Why

The `replication-and-browser-traces` phase closes the two flows the
observability backbone still cannot see. This change owns the first half: making
Postgres **streaming replication lag** an observable, queryable metric.

The stack already runs a primary `db` (logical WAL, physical streaming) and a
streaming hot-standby `replica` (pg_basebackup bootstrap) that the frontend
reads from. Today nothing measures how far behind the standby is — the
replication health of the read path is a black box. The phase proof asserts
Prometheus can answer `max(pg_replication_lag_bytes)`, and the follow-on
`replication-dashboard` change needs a live series to bind a "Primary/Replica
Streaming" dashboard to.

This change adds a small, self-contained Python probe that periodically samples
`pg_stat_replication` on the primary and the replay position/timestamp on the
replica, computes lag in bytes and seconds, and emits it as a custom OTel
gauge over the existing OTLP/HTTP -> Alloy -> Prometheus path. Its output — a
`pg_replication_lag_bytes` (and `pg_replication_lag_seconds`) series in
Prometheus — is exactly what `replication-dashboard` visualizes.

## What Changes

- Add a new probe module `app/replication_probe.py` with a
  `python -m app.replication_probe` entry point: a long-lived loop that calls
  `configure_otel("replication-probe")` once at startup (reading the OTLP
  endpoint from the environment only, no SDK re-wiring) and registers the
  replication-lag instrument(s) on a meter from the shared bootstrap.
- Compute **byte lag** from the primary (`DATABASE_URL`): read the standby
  row(s) from `pg_stat_replication` and compute
  `pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn)`.
- Compute **seconds lag** from the replica (`REPLICA_URL`): read
  `pg_last_xact_replay_timestamp()` / `pg_last_wal_replay_lsn()` and derive the
  seconds-behind value (`now()` minus the last replay timestamp).
- Choose the instrument name/unit so the **Prometheus series is exactly**
  `pg_replication_lag_bytes` (the phase proof queries it), avoiding the
  gauge unit-suffix / `_ratio` pitfall; also expose a seconds series
  (e.g. `pg_replication_lag_seconds`). Read the emitted names empirically from
  the running Prometheus rather than assuming.
- Wire the probe as its own runtime service in `docker-compose.yml`:
  `DATABASE_URL` (primary), `REPLICA_URL` (replica),
  `OTEL_EXPORTER_OTLP_ENDPOINT=http://alloy:4318` (env-overridable), starting
  after `db`, `replica`, and `alloy` are available.
- Add unit tests for the lag computations (fed from fixtures, no live DB/
  collector) and the safe no-endpoint path; keep the existing pytest suite and
  `docker-compose.test.yml` green and untouched.

## Design

- **Vertical-slice scope.** This change adds exactly the probe and its custom
  metric. It does **not** author the "Primary/Replica Streaming" dashboard JSON
  (that is `replication-dashboard`), does **not** touch Grafana provisioning,
  and does **not** touch the browser SDK or its dashboard. It proves only that a
  replication-lag series is queryable in Prometheus.
- **Reuse the bootstrap, don't re-wire the SDK.** Like the CDC consumer, the
  probe is a long-lived loop (not a FastAPI app), so there is no
  `instrument_fastapi_app`: it calls `configure_otel("replication-probe")` once
  and pulls a meter off the global provider. All exporter/provider wiring stays
  in `app.otel`.
- **Two connections, two sources of truth.** Byte lag is authoritative on the
  primary (`pg_stat_replication.replay_lsn` vs `pg_current_wal_lsn()`); seconds
  lag is authoritative on the replica (`pg_last_xact_replay_timestamp()`). The
  probe holds both `DATABASE_URL` and `REPLICA_URL`, mirroring how the frontend
  already splits primary/replica.
- **Name the series deterministically.** Per the known OTLP→Prometheus
  normalization (dots→underscores, unit suffix; a unit of `1` yields a `_ratio`
  suffix), the instrument name/unit is chosen so the byte series lands as
  exactly `pg_replication_lag_bytes`, and the actual `__name__` values are read
  from the running Prometheus before the downstream dashboard binds to them.
- **Resilient, never perturbs replication.** The probe only issues read-only
  monitoring queries; it never writes, never touches the replication slot, and a
  transient query error or a momentarily-absent standby row is swallowed for
  that sample so the loop keeps running.
- **Safe-by-default.** With `OTEL_EXPORTER_OTLP_ENDPOINT` unset the process
  installs no exporter and raises nothing, keeping pytest and plain local boot
  green.
- **Runtime-only compose change.** Only the runtime `docker-compose.yml` gains
  the new service; `docker-compose.test.yml` is untouched.
- **Proof-of-work for this change** (the byte half of the phase proof; the
  dashboard half belongs to `replication-dashboard`): from a clean
  `docker compose up -d --wait`, Prometheus answers
  `max(pg_replication_lag_bytes)` with a non-null value.

## Tasks

- [x] 1. Add `app/replication_probe.py` with a `python -m app.replication_probe`
      entry point: a long-lived periodic loop that calls
      `configure_otel("replication-probe")` once at startup (endpoint from the
      environment only) and obtains a meter from the shared bootstrap.
- [x] 2. Implement byte-lag sampling against the primary (`DATABASE_URL`):
      read `pg_stat_replication` and compute
      `pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn)`; factor the
      computation so SQL results can be fed from fixtures.
- [x] 3. Implement seconds-lag sampling against the replica (`REPLICA_URL`):
      read `pg_last_xact_replay_timestamp()` / `pg_last_wal_replay_lsn()` and
      derive the seconds-behind value.
- [x] 4. Register the lag instrument(s) on the bootstrap meter and choose the
      name/unit so the Prometheus byte series is exactly
      `pg_replication_lag_bytes` (avoid the unit-suffix / `_ratio` pitfall);
      also expose a seconds series (e.g. `pg_replication_lag_seconds`).
- [x] 5. Add the probe as a runtime service in `docker-compose.yml` with
      `DATABASE_URL`, `REPLICA_URL`, and
      `OTEL_EXPORTER_OTLP_ENDPOINT=http://alloy:4318` (env-overridable), ordered
      after `db`, `replica`, and `alloy` are available.
- [x] 6. Make the loop resilient: a transient query error or a missing standby
      row records no value for that sample and does not crash the loop; queries
      are read-only and never touch the replication slot.
- [x] 7. Add unit tests: byte lag from primary inputs, seconds lag from replica
      inputs, and the safe no-endpoint path (no exporter installed); keep the
      existing pytest suite green with no running collector.
- [x] 8. Confirm empirically: bring the stack up with
      `docker compose up -d --wait`, read the emitted series names from
      Prometheus (`/api/v1/label/__name__/values`), and verify
      `max(pg_replication_lag_bytes)` returns a non-null value.
- [x] 9. Confirm `docker-compose.test.yml` is unchanged and the existing
      pytest/vitest suites still pass.
