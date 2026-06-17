# read-replica-split

## Why

Phase 5 (`realtime-cdc-websocket`) introduces the real-time infrastructure: a
streaming read replica, a singleton CDC consumer tailing the logical slot, Redis
pub/sub, and a stateful frontend API. The root `websocket-fanout` already added
Redis and the WebSocket fan-out surface, but it served its connect snapshot from
the **primary** as a documented, scoped deviation — the replica did not exist
yet.

This change introduces that **streaming physical read replica** and moves the
frontend's read surface onto it, closing the deviation. It is the second slice
of the phase and the foundation the CDC slices build on: it lands the
primary/replica split the telemetry-architecture standard mandates (frontend
reads from the standby, writes go to the primary) and the shared seams the CDC
consumer will reuse (the replica pool, the connection-factory read seams, the
replication-ready compose topology, the replica-aware conftest).

It deliberately stops **before** CDC. The logical-slot consumer that decodes the
WAL into Redis events (`cdc-pgoutput-translate`) and the full `POST -> WS` phase
proof (`cdc-end-to-end-ws`) are explicitly out of scope here. This slice's own
proof needs only physical replication: write to the primary, then the frontend
snapshot — read from the replica — reflects the committed write. This mirrors how
earlier roots (`fault-transition-core`, `websocket-fanout`) proved their seam and
left the follow-on to land the full phase proof.

## What Changes

- **Config** `app/config.py`: add `get_replica_dsn()` reading `REPLICA_URL` from
  the environment with no hard-coded fallback (raise `ConfigError` when missing),
  mirroring `get_dsn()`.
- **Connection pool** `app/db.py`: add a process-wide replica `ConnectionPool`
  built from `get_replica_dsn()` (`get_replica_pool()` + a `replica_connection()`
  context manager), separate from the primary pool. Reads only — the standby
  rejects writes. `close_pool()` tears down both pools for clean test teardown.
- **Read seams** `app/persistence.py`: give the frontend read seams
  (`aggregate_fleet_state`, `zone_entry_counts`, `recent_anomalies`) a connection
  factory parameter (a no-arg callable returning a connection context manager)
  that defaults to the primary `connection`, so a caller can route a read to the
  replica without changing the SQL. The write path (`persist_telemetry`, the
  fault handler) is untouched and stays on the primary.
- **Frontend** `app/frontend_api.py`: build the WebSocket connect snapshot and
  serve the REST reads (`GET /fleet/state`, `GET /zones/counts`, `GET /anomalies`)
  through `replica_connection`, isolating connect-time and query read load from
  the primary's write path. The live delta path (Redis -> WS) is unaffected — it
  never depended on the replica.
- **Test stack** `docker-compose.test.yml`: configure the primary for physical
  replication (`wal_level`, `max_wal_senders`, `max_replication_slots`, and a
  `host replication ... trust` `pg_hba` rule via a primary-init mount) and add a
  `replica` service that bootstraps from the primary with `pg_basebackup` and runs
  as a hot standby streaming from it, with a healthcheck. Set
  `REPLICA_URL=postgresql://...@replica:5432/...` on the `api` container and add
  `replica` (`condition: service_healthy`) to its `depends_on`. CDC and Redis
  belong to the other slices; this change adds only what the replica needs.
- **Docker assets** `docker/`: a primary-init SQL/script adding the replication
  `pg_hba` rule, and a `replica-entrypoint.sh` that runs `pg_basebackup` then
  starts the standby.
- **Test fixtures** `tests/integration/conftest.py`: wait for the replica to come
  up as a streaming standby that has replayed the migrated schema, and a
  `wait_replica_caught_up()` helper that blocks until the replica has replayed up
  to the primary's current WAL position so a write-then-read assertion is not
  racing replication. No dependency on the CDC slot in this slice.
- **Proof-of-work test** `tests/integration/test_replica_snapshot.py` (this
  change's proof — *not* the phase proof): see Design -> Testing.

## Design

- **Vertical-slice scope.** The thinnest slice that proves the read replica end to
  end: a committed write on the primary -> physical streaming replication ->
  replica pool -> connection-factory read seam -> frontend snapshot reflects it.
  The CDC consumer, the Redis event production, the real `POST -> WS` flow, and the
  full phase proof `tests/integration/test_realtime_ws.py` are out of scope (the
  CDC follow-ons). No event/Redis machinery is touched here.
- **Primary/replica split.** Per the telemetry-architecture standard, the frontend
  serves its reads from the streaming standby while every write goes to the
  primary. The split is realized with two connection pools and a connection-factory
  parameter on the read seams; the write path keeps using the primary and is not
  modified, so the ingestion API is unaffected.
- **Physical, not logical, replication.** The read replica is a physical hot
  standby (`pg_basebackup` + streaming WAL replay), independent of the logical slot
  the CDC consumer will use later. The primary is configured once for both
  (`wal_level=logical` already satisfies physical streaming), but this slice
  exercises only the physical standby.
- **Replication lag is explicit in tests, not in production reads.** The standby
  trails the primary by a small async lag. Production reads accept that (the
  snapshot is "recent committed state"); tests that write then assert call
  `wait_replica_caught_up()` first so the assertion is against caught-up state
  rather than racing replication.
- **Closes the documented deviation.** `websocket-fanout` served the snapshot from
  the primary as a scoped, documented deviation pending the replica. This change
  removes that deviation: the snapshot and REST reads now come from the replica, as
  the standard requires. The real-time delta path still does not depend on the
  replica.
- **Stateful connections, not stateful data.** Unchanged from `websocket-fanout`:
  the frontend holds only the live-connection registry and derives every read fresh
  from the database (now the replica), so any frontend instance can serve any
  client.
- **Config from the environment.** `REPLICA_URL` is read from the environment with
  no baked-in fallback, matching `get_dsn()`/`get_redis_url()` and the tech-stack
  standard's "configuration from the environment, no hard-coded connection
  strings".
- **Reuse, don't reinvent.** Extends the existing config helpers, the existing
  `psycopg_pool` pooling, and the existing read seams (adding only a connection
  factory); introduces no new datastore or framework — the same Postgres engine,
  now with a standby.
- **Testing.** `tests/integration/test_replica_snapshot.py` runs against the real
  primary + streaming replica from `docker-compose.test.yml`:
  (a) the replica is a hot standby (`pg_is_in_recovery()` true) that has replayed
  the migrated schema;
  (b) a fleet/zone write committed on the primary, after `wait_replica_caught_up()`,
  is reflected by a snapshot built from the replica read seams (and by the REST
  reads served from the replica);
  (c) an uncommitted write on the primary is not visible on the replica until it
  commits and the replica catches up;
  (d) the replica rejects a direct write (read-only standby);
  (e) the frontend snapshot/REST reads are derived through the replica pool, while
  the ingestion write path still targets the primary.
  The full phase proof-of-work command
  `docker compose -f docker-compose.test.yml run --rm api pytest tests/integration/test_realtime_ws.py`
  is landed by the CDC follow-on (`cdc-end-to-end-ws`), which supplies the event
  source.

## Tasks

- [x] 1.1 Add `get_replica_dsn()` to `app/config.py` reading `REPLICA_URL` from the environment with no hard-coded fallback (raise `ConfigError` when missing), mirroring `get_dsn()`
- [x] 1.2 Add a replica `ConnectionPool` to `app/db.py`: `get_replica_pool()` (built from `get_replica_dsn()`) and a `replica_connection()` context manager, separate from the primary pool; extend `close_pool()` to tear down both pools
- [x] 2.1 Give the frontend read seams in `app/persistence.py` (`aggregate_fleet_state`, `zone_entry_counts`, `recent_anomalies`) a connection-factory parameter defaulting to the primary `connection`, without changing their SQL
- [x] 2.2 Confirm the write path (`persist_telemetry`, the fault handler) is unchanged and still uses the primary pool
- [x] 3.1 Route the frontend WebSocket connect snapshot in `app/frontend_api.py` to build from the read seams against `replica_connection`
- [x] 3.2 Route the frontend REST reads (`GET /fleet/state`, `GET /zones/counts`, `GET /anomalies`) to the replica via the connection factory, and update the module docstring to reflect that the snapshot now comes from the replica (closing the documented primary-read deviation)
- [x] 4.1 Configure the primary for physical replication in `docker-compose.test.yml` (`wal_level`, `max_wal_senders`, `max_replication_slots`) and mount a primary-init asset under `docker/` adding the `host replication ... trust` `pg_hba` rule
- [x] 4.2 Add a `replica` service to `docker-compose.test.yml` that bootstraps via `pg_basebackup` and runs as a hot standby (a `docker/replica-entrypoint.sh`), with a healthcheck
- [x] 4.3 Set `REPLICA_URL` on the `api` container and add `replica` (`condition: service_healthy`) to its `depends_on`
- [x] 5.1 In `tests/integration/conftest.py`, wait for the replica to be a streaming standby that has replayed the migrated schema, and add a `wait_replica_caught_up()` helper that blocks until the replica has replayed up to the primary's current WAL position (no CDC-slot dependency in this slice)
- [x] 6.1 Proof test: the replica reports `pg_is_in_recovery()` true and has the migrated tables (streamed from the primary)
- [x] 6.2 Proof test: a fleet/zone write committed on the primary, after `wait_replica_caught_up()`, is reflected by a snapshot built from the replica read seams
- [x] 6.3 Proof test: an uncommitted write on the primary is not visible on the replica until it commits and the replica catches up
- [x] 6.4 Proof test: the replica rejects a direct write (read-only standby)
- [x] 6.5 Proof test: the frontend snapshot/REST reads are derived through the replica pool while the ingestion write path still targets the primary
- [x] 6.6 Land the proof test at `tests/integration/test_replica_snapshot.py` and confirm it passes against the real primary + streaming replica from `docker-compose.test.yml`
- [x] 7.1 Write the AI build-log report to `docs/ai-build-logs/*.md` and append one line to `docs/ai-build-logs/index.md`
