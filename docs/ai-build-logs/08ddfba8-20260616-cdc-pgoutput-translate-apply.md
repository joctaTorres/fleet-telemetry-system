# AI Build Log — apply cdc-pgoutput-translate

- **Session id:** 08ddfba8-20260616
- **Session name:** apply — cdc-pgoutput-translate
- **Step:** apply
- **Change:** cdc-pgoutput-translate
- **Batch / phase:** fleet-telemetry-service / realtime-cdc-websocket
- **Date:** 2026-06-16

## Brief

The **CDC source half** of the `realtime-cdc-websocket` phase: the singleton CDC
consumer that supplies the producer the prior two slices (`websocket-fanout`,
`read-replica-split`) left as a seam. It taps the primary's WAL through a single
`pgoutput` logical replication slot bound to a publication over exactly the three
watched tables, decodes the binary Begin/Relation/Insert/Update/Commit messages
itself, translates each watched table into its `app.events` type, and publishes
the JSON envelope to the `fleet:events` Redis channel — the **sole** producer on
that channel. Because pgoutput frames changes by Commit, only committed
transactions emit, so the event stream is a deterministic function of the
committed WAL (no dual-write) and an aborted write produces nothing; the
ingestion API still touches no Redis.

The risky decode is deliberately isolated: the proof writes to the primary and
asserts the correct event JSON lands on Redis — no replica, no WebSocket, no full
topology. Wiring the consumer into the running compose topology and landing the
full `POST → WS` phase proof (`tests/integration/test_realtime_ws.py`) is the
follow-on `cdc-end-to-end-ws`. All plan tasks 1.1–5.1 completed; the proof passes
7/7 and the integration suite passes against real Postgres + Redis (89/89, exit 0).

## Artifacts written

- `app/cdc.py` — CDC plumbing constants: `PUBLICATION_NAME = "fleet_events_pub"`,
  `SLOT_NAME = "fleet_cdc_slot"`, the `TABLE_EVENT_TYPES` map (watched table →
  event type, reusing the three `app.events` constants), and `WATCHED_TABLES`.
  Connection strings still come from `get_dsn()` / `get_redis_url()`; only the
  object *names* are in-source identifiers. (1.1)
- `app/migrations/0009_create_cdc_publication.sql` — idempotent `CREATE
  PUBLICATION fleet_events_pub FOR TABLE vehicle_current_state, anomalies,
  zone_counts` (guarded by a `pg_publication` existence check, since `CREATE
  PUBLICATION` has no `IF NOT EXISTS`), plus `REPLICA IDENTITY FULL` on the two
  UPDATE-receiving tables so the replicated tuple always carries the key columns
  the translator reads (`zone_id`, `vehicle_id`). (1.2)
- `app/cdc_consumer.py` — the singleton consumer:
  - `ensure_slot()` / `drop_slot()` — create the `pgoutput` logical slot via
    `pg_create_logical_replication_slot` if absent (idempotent), and drop it
    (test setup/teardown). (2.1)
  - `CdcConsumer.run()` — opens a replication-protocol connection
    (`make_conninfo(get_dsn(), replication="database")` over psycopg's raw
    `pq.PGconn`, no extra dependency), issues `START_REPLICATION SLOT … LOGICAL
    0/0 (proto_version '1', publication_names 'fleet_events_pub')`, and pumps the
    CopyBoth stream with async `get_copy_data` + `select` on the socket. (2.2)
  - `_decode_pgoutput` / `_cache_relation` / `_read_tuple` — hand-rolled
    big-endian decode of the pgoutput v1 messages: cache `Relation` (OID → name +
    columns), parse `Insert` / `Update` new tuples; Begin/Commit framing means
    only committed work reaches `_emit`. (2.3)
  - `_build_payload` — translate per table: `vehicle_current_state` →
    `vehicle_state_changed` (vehicle_id/status/battery_pct), `anomalies` →
    `anomaly_detected` (vehicle_id/anomaly_type/detail/detected_at), `zone_counts`
    → `zone_count_changed` (zone_id/entry_count). (2.4)
  - `_emit` — publish each envelope as JSON to `EVENT_CHANNEL` via the Redis
    client from `get_redis_url()`. (2.5)
  - `_send_feedback` — send standby status updates ('r') confirming the processed
    LSN so the slot's confirmed-flush position advances and WAL is released (on a
    ~1s cadence and on a reply-requested keepalive / shutdown). (2.6)
- `tests/integration/conftest.py` — a `cdc_stream` fixture that drops + recreates
  the slot fresh after the autouse `_clean_tables` reset (so cleanup writes are
  never decoded), runs the consumer on a daemon thread, waits until it is
  streaming, and exposes a `CdcEventStream.read_next()` reader over a Redis
  subscriber attached before any test write. (3.1)
- `tests/integration/test_cdc_translate.py` — the change's proof (7 tests):
  committed upsert → one `vehicle_state_changed` matching the row (4.1); committed
  anomaly insert → one `anomaly_detected` (4.2); committed zone increment → one
  `zone_count_changed` with the new count (4.3); each envelope carries its
  contract `type` (4.4); a rolled-back write emits nothing, with a committed
  sentinel proving liveness (4.5); a committed `raw_events` (non-watched) write
  emits nothing (4.6); the publication's members are exactly the three watched
  tables (4.7). Lands and passes against the real primary + Redis (4.8). (4.1–4.8)
- `docs/ai-build-logs/` — this report + an index line. (5.1)

## Verification

- Change proof: `docker compose -f docker-compose.test.yml run --rm api pytest
  tests/integration/test_cdc_translate.py` → **7 passed**, exit 0.
- Full integration suite: `docker compose -f docker-compose.test.yml run --rm api
  pytest -q` → **89 passed**, exit 0 (was 82; +7 new).

## Notes / decisions

- **pgoutput streaming, not SQL polling.** Per the manifest the slot uses the
  built-in binary `pgoutput` plugin over the streaming replication protocol
  (`START_REPLICATION`), not `pg_logical_slot_get_changes` / `test_decoding`. The
  binary decode is the deliberately-isolated risk; it is driven through psycopg's
  `pq` layer directly against libpq, so **no new dependency** was added.
- **Fresh slot per test.** The autouse `_clean_tables` resets all zone counters,
  which would otherwise decode into ~20 spurious `zone_count_changed` events. The
  fixture drops and recreates the slot *after* that reset commits, positioning the
  slot's restart LSN past the cleanup, so only the test's own writes are seen.
- **No phase proof here.** `tests/integration/test_realtime_ws.py` (the full
  `POST → WS` blackbox) is intentionally out of scope and lands in
  `cdc-end-to-end-ws`, which wires the consumer into the running topology.
