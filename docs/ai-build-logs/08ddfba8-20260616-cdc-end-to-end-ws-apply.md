# AI Build Log — apply cdc-end-to-end-ws

- **Session id:** 08ddfba8-20260616
- **Session name:** apply — cdc-end-to-end-ws
- **Step:** apply
- **Change:** cdc-end-to-end-ws
- **Batch / phase:** fleet-telemetry-service / realtime-cdc-websocket
- **Date:** 2026-06-16

## Brief

The **terminal slice** of the `realtime-cdc-websocket` phase: it drives the
*whole* real-time path through the running topology and lands the phase blackbox
proof. Three upstream slices each proved one segment under isolation —
`websocket-fanout` (Redis → WS, publishing to Redis from the test),
`read-replica-split` (connect snapshot from the standby), and
`cdc-pgoutput-translate` (WAL → Redis with the consumer run in-process on a test
thread). This slice closes the two remaining gaps: it runs the already-proven
`CdcConsumer` as a **long-lived singleton compose service** and proves the real
`POST → WAL → CDC service → Redis → frontend → WebSocket` flow end to end,
sub-second, with no event for an uncommitted write.

It is deliberately thin: no new pgoutput decode, no new Redis → WS fan-out, no new
replica reads. The only new code is the process supervisor around `CdcConsumer`,
its compose service, and the proof.

## Artifacts written

- `app/cdc_consumer.py` — the long-lived supervisor wrapping the unchanged
  `CdcConsumer` (no decode/translate/publish/feedback logic touched):
  - `run_forever(stop)` — supervises a single consumer: loops
    `ensure_slot()` + `CdcConsumer().run(stop)`, restarting with **bounded
    exponential backoff** (`0.5s → 10s`) on any transient failure, returning only
    when `stop` is set. (1.1)
  - `_publication_exists()` — gates slot creation on the publication existing. The
    `cdc` service comes up at compose time *before* migrations run; creating the
    slot in that window anchors its consistent point **ahead** of the publication
    and wedges decoding on pre-publication WAL (a historic catalog snapshot in
    which `fleet_events_pub` is invisible yields `publication … does not exist` for
    every change). The supervisor waits — without creating the slot — until the
    publication appears, then streams. (1.1)
  - `_install_signal_handlers(stop)` + `main()` + `python -m app.cdc_consumer`
    hook — SIGTERM/SIGINT set the consumer's `stop` event so it sends its final
    standby status update (advancing the slot's confirmed-flush position) and
    exits without wedging the slot. (1.2)
- `docker-compose.test.yml` — a singleton `cdc` service built from the api image,
  running `python -m app.cdc_consumer`, with `DATABASE_URL`/`REDIS_URL` from the
  environment (no source fallback) and `depends_on` `db` + `redis`
  (`service_healthy`); exactly one instance (a logical slot is single-reader).
  (2.1) Header comment updated to reflect the consumer now runs as a service; the
  `replica` service and the api config are unchanged. (2.2)
- `tests/integration/conftest.py` — proof topology helpers (the in-process
  `cdc_stream` fixture is left intact but unused by this proof):
  - `wait_cdc_service_streaming()` — polls `pg_replication_slots` for an `active`
    `fleet_cdc_slot` reader so the proof does not race the service's startup
    backoff. (3.1)
  - `wait_frontend_subscribed()` — polls `PUBSUB NUMSUB` so the proof never
    `POST`s before the frontend's lifespan subscriber is attached (the CDC service,
    not the test, is the publisher, and pub/sub does not buffer). (3.2)
  - `WsReader` — a *single* background thread draining the TestClient WebSocket
    into one queue, with `next_within` / `matching(predicate, timeout)`. One
    reader (vs a fresh blocking `receive_json` per poll) is required so a delta is
    never stolen by an orphaned reader left over from a prior timed-out poll. (3.2)
- `tests/integration/test_realtime_ws.py` — the phase blackbox proof (5 tests),
  both ASGI apps in-process (frontend via `TestClient` `with`-lifespan, ingestion
  via `TestClient`) against the real primary + replica + Redis + standalone `cdc`
  service:
  - committed fault telemetry `POST` → `vehicle_state_changed` (status `fault`)
    sub-second (4.1);
  - `POST` with a zone entry → `zone_count_changed` (incremented `entry_count`)
    sub-second (4.2);
  - `POST` committing a `low_battery` anomaly → `anomaly_detected` sub-second
    (4.3);
  - a rolled-back write against a watched table → **no** event, with a committed
    sentinel proving the stream is live (4.4);
  - the write path has no Redis reference at all and a clean `POST` yields exactly
    one delta (CDC is the sole producer; no dual-write) (4.5);
  - lands at `tests/integration/test_realtime_ws.py`, exit 0 against the full
    running topology (4.6).
- `docs/ai-build-logs/` — this report + an index line. (5.1)

## Verification

- Phase proof (cold start): `docker compose -f docker-compose.test.yml down -v` →
  `up -d --build db replica redis cdc` → `docker compose -f docker-compose.test.yml
  run --rm api pytest tests/integration/test_realtime_ws.py` → **5 passed**,
  exit 0. The cdc logs show the supervisor gating on the publication
  (`publication fleet_events_pub not present yet; waiting before streaming`) until
  migrations run, then streaming.
- Full integration suite (cdc service up), excluding the slot-exclusive
  `test_cdc_translate.py`: `pytest -q --ignore=tests/integration/test_cdc_translate.py`
  → **87 passed**, exit 0 — no regression.
- `test_cdc_translate.py` in isolation (cdc service stopped): **7 passed**, exit 0
  — the conftest additions are backward-compatible.

## Notes / decisions

- **Run the proven consumer as a service, not a thread.** The exact `CdcConsumer`
  class from `cdc-pgoutput-translate` runs in its own container; only the process
  wrapper is new. HA (a standby resuming from the bookmarked LSN) stays out of
  scope — there is still exactly one active reader.
- **Publication-gated slot creation (key fix).** The original supervisor created
  the slot immediately on the cold-start window before the publication existed,
  which wedged the consumer in a silent restart loop (`get_copy_data` returning
  `-1` after the server aborted decoding with `publication … does not exist`).
  Gating slot creation on the publication anchors the slot's consistent point
  after it, so only decodable (post-publication) WAL is ever streamed.
- **Single-slot contention is by design.** A logical slot is single-reader, so the
  in-process `cdc_stream` proof (`test_cdc_translate.py`) and the standalone `cdc`
  service cannot both own `fleet_cdc_slot` at once. Each change's proof is run by
  its own command, not together in one session against one live topology; the full
  suite was checked with `test_cdc_translate.py` excluded (service up) and that
  file verified separately with the service stopped.
- **CDC, not dual-write — proven for real.** The ingestion API stays write-only;
  the proof confirms the delivered event is produced solely by the CDC service
  (the write path contains no Redis reference, a clean POST yields exactly one
  delta) and that an aborted write yields nothing (pgoutput frames on Commit).
- **Canonical invocation.** The phase command is run against the already-running
  topology (`up -d` the infra + `cdc`, then `run --rm api pytest …`); the api
  service's `depends_on` is intentionally left unchanged, so the `cdc` service is
  brought up at compose time rather than pulled in as an api dependency.
