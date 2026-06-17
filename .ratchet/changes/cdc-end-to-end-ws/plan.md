# cdc-end-to-end-ws

## Why

Phase 5 (`realtime-cdc-websocket`) requires committed changes to propagate to the
dashboard over WebSocket with sub-second latency, derived from the WAL with no
dual-write. Three prior slices each proved one segment of that path under
isolation and stopped at a seam:

- `websocket-fanout` made the frontend stateful (Redis subscribe → WebSocket
  fan-out) and proved Redis → WS by publishing patches to Redis **directly from
  the test**.
- `read-replica-split` added the streaming standby and moved the connect snapshot
  onto it.
- `cdc-pgoutput-translate` built the singleton CDC consumer (pgoutput logical
  slot → binary decode → translate → publish to `fleet:events`) and proved
  WAL → Redis with the consumer run **in-process on a test thread** by the
  `cdc_stream` fixture.

Every segment is green, but no test has yet driven the **whole** path through the
running topology, and the consumer has never run as anything but a test thread.
This change closes both gaps. It is the phase's terminal slice: it runs the
already-proven `CdcConsumer` as a **long-lived singleton compose service** wired
into `docker-compose.test.yml`, and lands the phase blackbox proof
`tests/integration/test_realtime_ws.py` — a real `POST → WebSocket` flow:
POST to the stateless ingestion API → commit on the primary → WAL → the standalone
CDC service decodes and publishes to Redis → the stateful frontend fans the patch
out → a connected WebSocket client receives it sub-second, with no event for an
uncommitted write.

It is deliberately **thin**: it writes no new decode logic, no new fan-out, no new
replica plumbing. The risky parts were isolated and proven upstream
(`cdc-pgoutput-translate` decoded pgoutput; `websocket-fanout` fanned Redis → WS;
`read-replica-split` served the snapshot). What remains is the integration seam —
a supervisor that runs the consumer forever, its compose service, and the
end-to-end proof. This mirrors how `cdc-pgoutput-translate` explicitly named
`cdc-end-to-end-ws` as the follow-on that "wires the consumer into the running
topology and lands the full `POST → WS` phase proof".

## What Changes

- **Long-lived consumer supervisor** `app/cdc_consumer.py`: add a `main()` /
  `run_forever()` entry point (and a `python -m app.cdc_consumer` hook) that runs
  the singleton consumer as a long-lived process:
  - **Supervise**: loop `ensure_slot()` + `CdcConsumer().run(stop)`, restarting the
    stream with **bounded backoff** on a transient failure — most importantly the
    period at container start when the publication / watched tables are not yet
    migrated (migrations run separately, after compose `up`). A missing
    publication or a primary blip is retried, not a fatal crash; once the schema
    exists the consumer streams normally.
  - **Clean shutdown**: install a SIGTERM/SIGINT handler that sets the consumer's
    `stop` event so it sends its final standby status update (advancing the slot's
    confirmed-flush position) and exits without leaving the slot wedged. No
    decode, translate, publish, or feedback logic from `cdc-pgoutput-translate`
    changes — this is purely the process wrapper around the proven `CdcConsumer`.
- **CDC compose service** `docker-compose.test.yml`: add a singleton **`cdc`**
  service built from the same image as `api`, running `python -m app.cdc_consumer`,
  with `DATABASE_URL` and `REDIS_URL` from the environment and `depends_on` on the
  primary (`db`) and `redis` being `service_healthy`. Exactly one instance — a
  logical slot is a single-reader construct. Update the file's header comment,
  which currently says the consumer "arrives with the … follow-on slices", to
  reflect that it is now a running service. The `replica` service and the api's
  config are unchanged.
- **Proof topology fixtures** `tests/integration/conftest.py`: add a small helper
  that confirms the standalone `cdc` service is actually streaming the slot
  (e.g. polling `pg_replication_slots` for `slot_name = fleet_cdc_slot` with an
  `active` reader) before the proof writes, so the end-to-end assertion is not
  racing service startup. Add a fresh Redis-subscriber / WS-receive helper the
  proof uses to read the next delta within the sub-second bound (reuse the
  threaded-receive pattern already in `test_ws_fanout.py`). The existing in-process
  `cdc_stream` fixture is left intact for the `cdc-pgoutput-translate` proof but is
  **not** used here — the phase proof must exercise the real service.
- **Phase proof-of-work** `tests/integration/test_realtime_ws.py` (this is the
  phase blackbox proof, the command in the phase definition): see Design → Testing.
- **AI build-log** `docs/ai-build-logs/*.md` + an index line.

## Design

- **Vertical-slice scope.** The thinnest slice that proves the phase goal end to
  end through the running topology: a committed `POST` → WAL → the standalone CDC
  service → Redis → the frontend WebSocket fan-out → a client receives the matching
  derived patch sub-second. It writes **no** new pgoutput decode, **no** new
  Redis → WS fan-out, **no** new replica reads — all three were isolated and proven
  upstream. The only new code is the long-lived supervisor, its compose service,
  and the proof.
- **Run the proven consumer as a service, not a thread.** `cdc-pgoutput-translate`
  proved `CdcConsumer` decode/translate/publish with an in-process thread; this
  slice runs that exact class in its own container so the proof exercises the
  production-shaped topology — a single reader of the logical slot, started
  independently of the test, surviving restart. HA (a standby resuming from the
  bookmarked LSN) remains out of scope; there is still exactly one active reader.
- **CDC, not dual-write — proven for real.** The ingestion API stays stateless and
  write-only and **must not** publish to Redis. The end-to-end proof now confirms
  this against the running stack: the `fleet:events` message a client receives is
  produced solely by the CDC service decoding the WAL, never by the write path.
  An uncommitted (rolled-back) write yields **no** event, because pgoutput frames
  on `Commit`.
- **Sub-second, not synchronous.** The bound asserted is the phase's sub-second
  propagation. The path is asynchronous (commit → WAL → decode → publish →
  fan-out) but bounded; the proof connects the WebSocket and subscribes *before*
  issuing the `POST`, so the delivering event is captured (Redis pub/sub does not
  buffer).
- **Startup ordering is tolerated, not assumed.** The `cdc` service comes up at
  compose time, before pytest runs `run_migrations()` inside the `api` container,
  so the publication and watched tables may not exist yet. The supervisor's
  bounded-backoff restart absorbs this window rather than depending on a strict
  service start order; the proof's "slot is active" helper gates the assertion on
  the consumer actually streaming.
- **Config from the environment.** The `cdc` service reads `DATABASE_URL` and
  `REDIS_URL` from the environment with no baked-in fallback, matching `get_dsn()`
  / `get_redis_url()` and the tech-stack standard.
- **Reuse, don't reinvent.** Reuses `CdcConsumer`, `ensure_slot`, the
  `app/events.py` contract, the frontend WebSocket app, the ingestion app, the
  replica snapshot, and the existing compose services; adds only a process wrapper
  and one service definition. No new datastore, broker, or framework.
- **Testing.** `tests/integration/test_realtime_ws.py` runs inside the `api`
  container (the phase command:
  `docker compose -f docker-compose.test.yml run --rm api pytest tests/integration/test_realtime_ws.py`)
  with the frontend and ingestion ASGI apps in-process (FastAPI `TestClient`, whose
  `with` block runs the frontend lifespan so its Redis subscriber attaches) against
  the real primary + replica + Redis + the standalone `cdc` service:
  (a) after confirming the `cdc` service is streaming and a client is connected to
  `/ws` (snapshot drained), a `POST` transitioning a vehicle to `fault` (committed
  on the primary) delivers a `vehicle_state_changed` event for that vehicle with
  status `fault` over the WebSocket within the sub-second bound;
  (b) a `POST /telemetry` with a zone entry delivers a `zone_count_changed` event
  for that zone with the incremented `entry_count`;
  (c) a `POST /telemetry` that commits a `low_battery` anomaly delivers an
  `anomaly_detected` event for that vehicle and type;
  (d) a rolled-back write against a watched table delivers **no** event within the
  bound;
  (e) the event a client receives originates from the CDC service, and the
  ingestion API publishes nothing to Redis (the write path is never the producer).
  Passing is exit code 0.

## Tasks

- [x] 1.1 Add a long-lived supervisor entry point to `app/cdc_consumer.py`: a `main()` / `run_forever()` that loops `ensure_slot()` + `CdcConsumer().run(stop)` with bounded backoff on transient failure (publication/tables not yet migrated, primary blip), plus a `python -m app.cdc_consumer` hook — changing no decode/translate/publish logic
- [x] 1.2 Install SIGTERM/SIGINT handling in the supervisor that sets the consumer's `stop` event for a clean shutdown (final standby status update sent, slot not left wedged)
- [x] 2.1 Add a singleton `cdc` service to `docker-compose.test.yml` built from the api image, running `python -m app.cdc_consumer`, with `DATABASE_URL`/`REDIS_URL` from the environment and `depends_on` `db` + `redis` (`condition: service_healthy`); exactly one instance (single slot reader)
- [x] 2.2 Update the `docker-compose.test.yml` header comment so it reflects that the CDC consumer now runs as a service (no longer "arrives with the follow-on slices"); leave `replica` and the api config unchanged
- [x] 3.1 In `tests/integration/conftest.py`, add a helper that confirms the standalone `cdc` service is streaming (poll `pg_replication_slots` for an active `fleet_cdc_slot` reader) before the proof writes, so the end-to-end assertion does not race service startup
- [x] 3.2 In `tests/integration/conftest.py`, add a fresh Redis-subscriber / WS-receive helper for reading the next delta within the sub-second bound (reuse the threaded-receive pattern from `test_ws_fanout.py`); leave the in-process `cdc_stream` fixture intact but unused by this proof
- [x] 4.1 Proof test: a committed `POST` transitioning a vehicle to `fault` delivers a `vehicle_state_changed` event (that vehicle, status `fault`) over the WebSocket within the sub-second bound
- [x] 4.2 Proof test: a `POST /telemetry` with a zone entry delivers a `zone_count_changed` event for that zone with the incremented `entry_count` sub-second
- [x] 4.3 Proof test: a `POST /telemetry` committing a `low_battery` anomaly delivers an `anomaly_detected` event for that vehicle/type sub-second
- [x] 4.4 Proof test: a rolled-back write against a watched table delivers no event within the bound (only committed state ever surfaces)
- [x] 4.5 Proof test: the received event is produced by the CDC service and the ingestion API publishes nothing to Redis (the write path is never the producer)
- [x] 4.6 Land the proof at `tests/integration/test_realtime_ws.py` and confirm `docker compose -f docker-compose.test.yml run --rm api pytest tests/integration/test_realtime_ws.py` exits 0 against the full running topology
- [x] 5.1 Write the AI build-log report to `docs/ai-build-logs/*.md` and append one line to `docs/ai-build-logs/index.md`
