# AI Build Log — apply read-replica-split

- **Session id:** 08ddfba8-20260616
- **Session name:** apply — read-replica-split
- **Step:** apply
- **Change:** read-replica-split
- **Batch / phase:** fleet-telemetry-service / realtime-cdc-websocket
- **Date:** 2026-06-16

## Brief

The second slice of the `realtime-cdc-websocket` phase and the **read-replica
half**: it introduces a streaming physical read replica and moves the frontend's
entire read surface onto it, closing the documented "snapshot from the primary"
deviation that `websocket-fanout` opened. Writes still go to the primary; the
frontend snapshot and the three REST reads (`GET /fleet/state`,
`GET /zones/counts`, `GET /anomalies`) are served from the standby through a
separate replica connection pool.

It deliberately stops **before** CDC. The logical-slot consumer that decodes the
WAL into Redis events and the full `POST → WS` phase proof
(`test_realtime_ws.py`) are the explicit follow-ons (`cdc-pgoutput-translate`,
`cdc-end-to-end-ws`). This slice's own proof needs only physical replication:
write on the primary, then a replica-derived read reflects it. All plan tasks
1.1–7.1 completed; the proof passes (5/5) and the full integration suite passes
(82/82, exit 0).

## Context — re-split cleanup

This change was carved out of a `cdc-consumer` change that overran its apply
window and was **parked** after a re-split into `read-replica-split` +
`cdc-pgoutput-translate` + `cdc-end-to-end-ws`. The working tree still carried
that attempt's mixed scaffolding, including CDC machinery that belongs to the
later slices. Per this change's plan ("CDC and Redis belong to the other slices";
conftest has "no dependency on the CDC slot in this slice"), the out-of-scope CDC
bits were removed so the tree matches the read-replica-split scope:

- Dropped the dangling `from app.cdc_consumer import SLOT_NAME` (that module does
  not exist in this slice) and the logical-slot wait / `_wait_slot_drained` /
  slot-drain-in-`_clean_tables` machinery from `conftest.py`.
- Removed the `cdc` service (which ran the non-existent `app.cdc_consumer`) and
  its `api` `depends_on` entry from `docker-compose.test.yml`.

Redis and the stateful frontend stay — they were landed by `websocket-fanout`,
not by this slice.

## Artifacts written

- `app/config.py` — `get_replica_dsn()` reading `REPLICA_URL` from the
  environment, no hard-coded fallback (raises `ConfigError`), mirroring
  `get_dsn()`. *(present from re-split scaffolding; confirmed.)* (1.1)
- `app/db.py` — a process-wide replica `ConnectionPool`: `get_replica_pool()`
  (from `get_replica_dsn()`) + a `replica_connection()` context manager, separate
  from the primary pool; `close_pool()` tears down both. *(present; confirmed.)*
  (1.2)
- `app/persistence.py` — gave `recent_anomalies` a `conn_factory` parameter
  defaulting to the primary `connection` (matching `aggregate_fleet_state` and
  `zone_entry_counts`), no SQL change; the write path (`persist_telemetry`,
  `transition_to_fault`, `set_vehicle_status`) is untouched and stays on the
  primary. (2.1, 2.2)
- `app/frontend_api.py` — routed the three REST reads to the replica
  (`aggregate_fleet_state(replica_connection)`, `zone_entry_counts(replica_connection)`,
  `recent_anomalies(..., replica_connection)`); the connect snapshot already read
  from the replica. Updated the module docstring to state that *every* read is
  served from the replica, closing the documented primary-read deviation. (3.1,
  3.2)
- `docker-compose.test.yml` — primary configured for physical replication
  (`wal_level=logical`, `max_wal_senders=10`, `max_replication_slots=10`) with a
  `docker/primary-init` mount for the `pg_hba` rule; a `replica` service that
  bootstraps via `pg_basebackup` and runs as a hot standby with a healthcheck;
  `REPLICA_URL=postgresql://…@replica:5432/…` on `api` and `replica`
  (`condition: service_healthy`) in its `depends_on`. Removed the out-of-scope
  `cdc` service. (4.1, 4.2, 4.3)
- `docker/primary-init/10-replication-hba.sh` (new) — appends a
  `host replication all all trust` rule to the primary's `pg_hba.conf` during
  init so the replica can stream (test-only trust over the compose network). (4.1)
- `docker/replica-entrypoint.sh` (new) — chowns the tmpfs data dir to `postgres`,
  waits for the primary, `pg_basebackup --wal-method=stream --write-recovery-conf`
  on first start, then `exec gosu postgres postgres` to run as a streaming hot
  standby. (4.2)
- `tests/integration/conftest.py` — `_wait_for_replica()` blocks until the
  replica reports `pg_is_in_recovery()` and has the streamed `zone_counts` schema
  (no CDC-slot dependency); `wait_replica_caught_up()` blocks until the replica
  has replayed up to the primary's `pg_current_wal_lsn()`; `rolled_back_primary_write`
  helper for the uncommitted-write proof. Kept the `redis_client` fixture +
  `publish_event` from `websocket-fanout`. (5.1)
- `tests/integration/test_replica_snapshot.py` (new) — this change's proof. (6.1–6.6)
- `tests/integration/test_ws_fanout.py` — added a `wait_replica_caught_up()`
  before the snapshot assertion, since the snapshot now reads from the replica (a
  direct consequence of 3.1).

## Proof (`test_replica_snapshot.py`)

- 6.1 — the replica reports `pg_is_in_recovery()` true and has the streamed
  migrated tables (`raw_events`, `vehicle_current_state`, `anomalies`,
  `zone_counts`).
- 6.2 — a committed fleet/zone write on the primary, after
  `wait_replica_caught_up()`, is reflected by the replica read seams and by the
  REST handlers (which read through the replica pool).
- 6.3 — a rolled-back primary write never appears on the replica, while a
  genuinely committed increment does (proving the standby is live, not lagging).
- 6.4 — a direct write on the replica raises `ReadOnlySqlTransaction`
  (read-only standby).
- 6.5 — the read pool reports `pg_is_in_recovery()` true and the write pool
  false; `persist_telemetry` lands on the primary immediately, and the frontend
  `_build_snapshot()` + `get_fleet_state()` (served from the replica) reflect it
  after catch-up.

## Design alignment

- **Primary/replica split.** Per the telemetry-architecture standard, the
  frontend serves reads from the streaming standby while every write goes to the
  primary — realized with two pools and a connection-factory parameter on the
  read seams; the write path is unmodified, so the ingestion API is unaffected.
- **Physical, not logical, replication.** The replica is a physical hot standby
  (`pg_basebackup` + streaming WAL replay), independent of the logical slot the
  CDC follow-on will use. `wal_level=logical` satisfies both.
- **Lag explicit in tests, not in production reads.** Production reads accept the
  small async lag ("recent committed state"); write-then-assert tests call
  `wait_replica_caught_up()` first.
- **Closes the documented deviation.** The snapshot and REST reads now come from
  the replica, as the standard requires; the live delta path never depended on it.
- **Config from the environment.** `REPLICA_URL` is read with no baked-in
  fallback, matching `get_dsn()`/`get_redis_url()`.

## Outcome

`docker compose -f docker-compose.test.yml run --build --rm api pytest
tests/integration/test_replica_snapshot.py` → exit 0, 5 passed (api image
rebuilt; replica service healthy). Full suite `tests/integration` → 82 passed
(77 prior + 5 new). Plan tasks 1.1–7.1 checked off. This lands the read-replica
half of the phase; the CDC source and the full phase proof `test_realtime_ws.py`
are the `cdc-pgoutput-translate` / `cdc-end-to-end-ws` follow-ons.
