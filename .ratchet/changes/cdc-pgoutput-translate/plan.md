# cdc-pgoutput-translate

## Why

Phase 5 (`realtime-cdc-websocket`) propagates committed changes to the dashboard
over WebSocket with sub-second latency, derived from the WAL with no dual-write.
The two prior slices built the fan-out and the read surface: `websocket-fanout`
made the frontend stateful (Redis subscribe → WebSocket fan-out) and
`read-replica-split` moved the connect snapshot onto a streaming standby. Both
stopped at a seam: the Redis channel still has **no real producer** — the fan-out
slice published patches to Redis directly from its own test, standing in for a
CDC consumer that did not exist yet.

This change supplies that producer: the **singleton CDC consumer**. It taps the
primary's WAL through a single **pgoutput** logical replication slot bound to a
publication over the three watched tables, decodes the binary
Begin/Relation/Insert/Update/Commit messages, translates each watched table into
its event type (`vehicle_state_changed`, `anomaly_detected`, `zone_count_changed`),
and publishes the JSON envelope defined in `app/events.py` to the `fleet:events`
Redis channel. This is the CDC source the architecture of record mandates
(ADR-0001 Option B / D3): the event stream becomes a deterministic function of
the committed WAL, eliminating the dual-write inconsistency class, and the
ingestion API never touches Redis.

It deliberately isolates the **risky decode**. The proof is fast and narrow:
write to the primary, assert the correct event JSON lands on the Redis channel —
no replica, no WebSocket, no full topology. Wiring the consumer into the running
compose topology and landing the full `POST -> WS` phase proof
(`tests/integration/test_realtime_ws.py`) is the follow-on `cdc-end-to-end-ws`,
which is thin because `websocket-fanout` already does Redis → WS. This mirrors how
earlier roots (`fault-transition-core`, `websocket-fanout`) proved their seam
under isolation and left the full phase proof to their follow-on.

The CDC change was re-split for exactly this reason (batch manifest, 2026-06-16):
the original single CDC change overran the 10-minute apply window, so the decode
(this slice) and the topology wiring (`cdc-end-to-end-ws`) are now separate, each
sized to fit the window.

## What Changes

- **Event contract reuse** `app/events.py`: unchanged — this slice is the
  *producer* for the `EVENT_CHANNEL` / `VEHICLE_STATE_CHANGED` /
  `ANOMALY_DETECTED` / `ZONE_COUNT_CHANGED` constants the frontend already
  consumes. The publisher and subscriber share the one contract.
- **CDC plumbing constants** (e.g. in a new `app/cdc.py` or extending
  `app/events.py`): the publication name and slot name as module constants
  (identifiers, not secrets), plus the mapping from watched-table name to event
  type. The primary DSN comes from `get_dsn()` and the Redis URL from
  `get_redis_url()` — both already read from the environment with no hard-coded
  fallback (tech-stack standard).
- **Publication migration** `app/migrations/0009_create_cdc_publication.sql`: an
  idempotent `CREATE PUBLICATION` naming **exactly** the three watched tables
  (`vehicle_current_state`, `anomalies`, `zone_counts`) — never `raw_events`,
  `missions`, `maintenance_records`, `vehicles`, or `schema_migrations`. Replica
  identity is set so UPDATEs carry the key columns the translator needs
  (`zone_counts.zone_id`, `vehicle_current_state.vehicle_id`).
- **CDC consumer** `app/cdc_consumer.py`: the singleton consumer.
  - **Slot bootstrap**: on startup, ensure a logical slot with the **pgoutput**
    output plugin exists (create if absent), so the slot is the single-reader
    construct the standard requires.
  - **Stream**: open a replication-protocol connection to the primary, run
    `START_REPLICATION SLOT … LOGICAL` with the publication name and a pgoutput
    protocol version, and consume the binary CopyData message stream. If
    psycopg's replication-protocol access needs a helper, add a dependency via
    `uv add` (kept minimal); otherwise reuse the existing `psycopg[binary]`.
  - **Decode**: parse the pgoutput binary messages — `Relation` (table OID →
    name + column descriptors, cached), `Insert`, `Update` (new tuple), framed
    by `Begin`/`Commit` so only committed transactions emit events.
  - **Translate**: map the relation name to its event type and build the
    envelope payload from the decoded tuple — vehicle_id/status/battery_pct for
    `vehicle_state_changed`, vehicle_id/anomaly_type/detail/detected_at for
    `anomaly_detected`, zone_id/entry_count for `zone_count_changed`.
  - **Publish**: publish each envelope as JSON to `EVENT_CHANNEL` on Redis.
  - **Acknowledge**: send standby status updates confirming the processed LSN so
    the slot advances its confirmed-flush position and WAL is not retained
    unboundedly (operational-safety guideline).
- **Test fixtures** `tests/integration/conftest.py`: a fixture that runs the
  consumer in-process (a background thread / asyncio task) against the compose
  primary + Redis for the duration of a test and stops it cleanly, plus a helper
  to read the next message off `fleet:events`. (Running the consumer as its own
  compose service belongs to `cdc-end-to-end-ws`, which wires the full topology.)
- **Proof-of-work test** `tests/integration/test_cdc_translate.py` (this change's
  proof — *not* the phase proof): see Design → Testing.

## Design

- **Vertical-slice scope.** The thinnest slice that proves the CDC *source* end
  to end: commit a watched-table change on the primary → pgoutput logical slot →
  binary decode → translate → JSON envelope on the `fleet:events` Redis channel.
  The read replica path, the WebSocket fan-out, the real `POST -> WS` flow, and
  the full phase proof `tests/integration/test_realtime_ws.py` are out of scope
  (`cdc-end-to-end-ws`). No frontend or WebSocket code is touched here.
- **pgoutput, not test_decoding.** Per the manifest, the slot uses the built-in
  binary `pgoutput` plugin (the protocol real logical replication uses), not the
  text `test_decoding` plugin. This is the deliberately-isolated risk: the
  consumer decodes the binary Relation/Insert/Update messages itself.
- **CDC, not dual-write.** The event stream is a deterministic function of the
  committed WAL. The ingestion API stays write-only and **must not** publish to
  Redis; the CDC consumer is the sole producer. Uncommitted/aborted writes never
  produce an event because decoding only emits on `Commit`.
- **Single reader.** A logical slot is a single-reader construct: exactly one
  consumer reads it. HA (out of scope here) is a standby resuming from the
  bookmarked LSN, never a second active reader.
- **Anomaly detection stays synchronous.** CDC only *observes* the `anomalies`
  rows the ingestion transaction already wrote; it does not make detection async
  (telemetry-architecture standard). The `anomalies` INSERT is the event.
- **Bounded WAL retention.** The consumer confirms processed LSNs so the slot's
  confirmed-flush position advances and WAL up to that point is released —
  honoring the operational-safety guideline against unbounded slot growth.
- **Reuse, don't reinvent.** Reuses the `app/events.py` contract, the
  `app/config.py` env helpers, the existing migration runner, and the Redis
  client already in `pyproject.toml`; introduces no new datastore or broker. The
  publication/slot are new Postgres objects, not a new engine.
- **Config from the environment.** Connection strings come from `get_dsn()` /
  `get_redis_url()`; only the publication and slot *names* are in-source
  identifiers, consistent with the tech-stack standard.
- **Testing.** `tests/integration/test_cdc_translate.py` runs against the real
  primary + Redis from `docker-compose.test.yml`, with the consumer started
  in-process by the fixture:
  (a) a committed `vehicle_current_state` upsert yields exactly one
  `vehicle_state_changed` event whose payload matches the committed row;
  (b) a committed `anomalies` insert yields exactly one `anomaly_detected` event
  with the row's vehicle_id/anomaly_type/detail;
  (c) a committed `zone_counts` atomic increment yields exactly one
  `zone_count_changed` event with the new entry_count;
  (d) each envelope carries the correct `type` from the `app/events.py` contract;
  (e) an aborted (rolled-back) write against a watched table yields **no** event;
  (f) a committed write to a non-watched table (e.g. `raw_events`) yields **no**
  event;
  (g) the publication's member tables are exactly the three watched tables.
  The full phase proof-of-work command
  `docker compose -f docker-compose.test.yml run --rm api pytest tests/integration/test_realtime_ws.py`
  is landed by the follow-on `cdc-end-to-end-ws`, which wires the consumer into
  the running topology.

## Tasks

- [x] 1.1 Add CDC plumbing constants (publication name, slot name, watched-table → event-type mapping) in a new `app/cdc.py` (or extend `app/events.py`), reusing `EVENT_CHANNEL` and the three event-type constants from `app/events.py`
- [x] 1.2 Add `app/migrations/0009_create_cdc_publication.sql`: an idempotent `CREATE PUBLICATION` over exactly `vehicle_current_state`, `anomalies`, `zone_counts`, and set replica identity so UPDATEs carry the needed key columns (`zone_id`, `vehicle_id`)
- [x] 2.1 In `app/cdc_consumer.py`, ensure the pgoutput logical slot exists on startup (create with the `pgoutput` output plugin if absent), reusing `get_dsn()` for the primary connection
- [x] 2.2 Open a replication-protocol connection and `START_REPLICATION SLOT … LOGICAL` with the publication name + pgoutput protocol version, consuming the binary CopyData stream (add a minimal `uv add` dependency only if psycopg's replication-protocol access requires it)
- [x] 2.3 Decode the pgoutput binary messages: cache `Relation` (OID → table name + columns), parse `Insert`/`Update` new tuples, and frame on `Begin`/`Commit` so only committed transactions emit
- [x] 2.4 Translate each watched relation into its event envelope: `vehicle_current_state` → `vehicle_state_changed` (vehicle_id/status/battery_pct), `anomalies` → `anomaly_detected` (vehicle_id/anomaly_type/detail/detected_at), `zone_counts` → `zone_count_changed` (zone_id/entry_count)
- [x] 2.5 Publish each envelope as JSON to `EVENT_CHANNEL` via the Redis client built from `get_redis_url()`
- [x] 2.6 Send standby status updates confirming the processed LSN so the slot's confirmed-flush position advances and WAL is not retained unboundedly
- [x] 2.7 Confirm the ingestion API still publishes nothing to Redis (CDC is the sole producer)
- [x] 3.1 Add a `tests/integration/conftest.py` fixture that runs the consumer in-process (background thread/task) against the compose primary + Redis and stops it cleanly, plus a helper to read the next `fleet:events` message
- [x] 4.1 Proof test: a committed `vehicle_current_state` upsert yields exactly one `vehicle_state_changed` event whose payload matches the committed row
- [x] 4.2 Proof test: a committed `anomalies` insert yields exactly one `anomaly_detected` event with the row's vehicle_id/anomaly_type/detail
- [x] 4.3 Proof test: a committed `zone_counts` atomic increment yields exactly one `zone_count_changed` event with the new entry_count
- [x] 4.4 Proof test: each envelope carries the correct `type` from the `app/events.py` contract
- [x] 4.5 Proof test: an aborted (rolled-back) write against a watched table yields no event
- [x] 4.6 Proof test: a committed write to a non-watched table (e.g. `raw_events`) yields no event
- [x] 4.7 Proof test: the publication's member tables are exactly the three watched tables
- [x] 4.8 Land the proof test at `tests/integration/test_cdc_translate.py` and confirm it passes against the real primary + Redis from `docker-compose.test.yml`
- [x] 5.1 Write the AI build-log report to `docs/ai-build-logs/*.md` and append one line to `docs/ai-build-logs/index.md`
