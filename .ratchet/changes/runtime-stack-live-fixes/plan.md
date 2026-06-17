# runtime-stack-live-fixes

## Why

The `runtime-stack-k6-sim` change made the system *servable* with one command and
proved the REST snapshot renders, but the live-update path is dead in the runtime:
the `fleet_cdc_slot` confirmed-flush LSN stays frozen, a live `SUBSCRIBE
fleet:events` during load receives ZERO messages, the `cdc` service logs only
"starting CDC consumer supervisor" once and goes silent, and load stops because
k6 ran one finite batch and exited. This change fixes the two diagnosed root
causes so committed writes actually become live deltas the dashboard renders, and
formalizes the two cross-origin/WS fixes the live path also depends on.

## What Changes

- **CDC liveness (root cause 1)** — implements
  `features/runtime-cdc-liveness/cdc-live-delta-path.feature`:
  - The runtime `docker-compose.yml` `cdc` service gates on the one-shot
    `migrate` completing (`migrate: { condition: service_completed_successfully }`)
    in addition to `db`/`redis` `service_healthy`, mirroring the `ingestion`
    service, so `cdc` first attempts to stream only after the publication and
    watched tables exist.
  - `app/cdc_consumer.py`'s long-lived supervisor (`run_forever`) is hardened so
    its retry/backoff is effectively unbounded and long-lived: it never exhausts
    retries or exits on a missing/late publication, slot, or table, nor on a
    transient mid-stream error — it keeps retrying with a bounded short backoff
    and recovers when the underlying condition clears. **No** change to the
    decode/translate/publish core.
- **Continuous load (root cause 2)** — implements
  `features/runtime-continuous-load/k6-continuous-load.feature`:
  - `/k6/fleet-simulation.js` switches from the finite `per-vu-iterations`
    (`iterations: 1`) executor to a continuous executor (`constant-vus` at
    `FLEET_SIZE` VUs with an effectively-unbounded duration), so the fleet streams
    a steady ~50-vehicle, 1 Hz load for as long as the stack is up. The stateful
    per-vehicle model, the canonical telemetry shape with the deployed field names
    (`recorded_at`/`pos_x`/`pos_y` — **NOT** renamed), the realistic zone ids, the
    shift-change charging convergence, the fault injection, and all checks +
    thresholds are preserved.
  - The runtime `docker-compose.yml` `k6` service keeps driving load (a long
    duration in-script and/or a `restart` policy) so it stays `Up`.
- **Cross-origin REST + WS (kept/formalized)** — implements
  `features/runtime-cross-origin-ws/cors-and-websocket.feature`:
  - `app/frontend_api.py` keeps the env-driven `CORSMiddleware` (already applied to
    the working tree) so the dashboard's cross-origin REST is allowed.
  - `pyproject.toml` / `uv.lock` keep the `websockets` dependency (already applied)
    so uvicorn can complete the `ws://…/ws` upgrade with a 101 instead of 404.

This change re-shapes no data flow and renames no telemetry fields. It does NOT
run the final Playwright/llm-judge proof (the parent re-drives that).

## Design

**CDC `depends_on` gate.** The diagnosis showed `cdc` started before `migrate`,
so its supervisor checked for `fleet_events_pub` while it did not yet exist; the
bounded-backoff loop then either wedged or appeared silent. Adding
`migrate: { condition: service_completed_successfully }` to the `cdc` service —
exactly as `ingestion`/`frontend` already do — makes the publication, watched
tables, and seeded `zone_counts` present the first time the consumer connects,
removing the cold-start race entirely. (The publication + slot are themselves
correct: `fleet_events_pub` exists and the slot is active.)

**Supervisor hardening.** `run_forever` already loops `_publication_exists()` →
`ensure_slot()` → `CdcConsumer().run(stop)` and catches stream exceptions, but its
backoff grows exponentially toward a 10 s cap and — critically — the "not ready"
and "never-streamed failure" branches let the backoff drift to its ceiling with no
guarantee it re-tightens, and any unhandled error path could end the loop. The fix
keeps the loop running forever until `stop` (clean shutdown), guarantees the
readiness probe and the stream attempt are *both* retried indefinitely with a
bounded short backoff (so a late/transient publication, slot, or table is absorbed
quickly and recovered from), logs every wait/restart so the service is never
silent, and ensures no exception escapes the supervised body. The slot-anchoring
invariant is preserved: the slot is still only created *after* the publication
exists (anchoring it ahead would wedge decoding on pre-publication WAL). The
decode/translate/publish core and the SIGTERM/SIGINT clean-shutdown path are
untouched. This honors the **telemetry-architecture** standard (single CDC reader,
Redis as the only fan-out, slot confirmed-flush advanced via standby feedback) and
**tech-stack** (no new dependency — psycopg `pq` layer only).

**Continuous k6 executor.** `constant-vus` with `FLEET_SIZE` VUs and a long
`duration` (env-overridable, default effectively unbounded for a demo) keeps each
VU looping its stateful per-vehicle tick at ~1 Hz indefinitely, so ~50 events/s
flow continuously. The per-VU `export default` body is restructured so the
per-vehicle state lives across iterations (initialized once per VU) and the tick
logic — move/drain, periodic realistic-zone crossing, shift-change charging
convergence with battery recovery, occasional fault injection tripping real
anomaly thresholds and `POST /vehicles/{id}/status` "fault" — runs each iteration,
with the read-back checks (`GET /vehicles`, `/zones/counts`,
`/vehicles/anomalies/latest`) still exercised periodically so they reflect the
sustained load. Thresholds (p95 ingest latency, error rate, check pass-rate,
`http_req_failed`) are unchanged so a breach still exits non-zero. The telemetry
body keeps the deployed `recorded_at`/`pos_x`/`pos_y` field names (the
`extra="forbid"` model rejects `timestamp`/`lat`/`lon`). The compose `k6` service
runs long-lived; a `restart` policy and/or a long in-script `duration` keeps it
`Up` driving load. This honors **tech-stack** (grafana/k6 image, no new tool) and
**telemetry-architecture** (load drives the real served write path; CDC remains
the sole producer of deltas).

**CORS + websockets (kept).** Both are already in the working tree from the parent
and are formalized here as tasks to verify-and-keep, not re-introduce: the
env-driven `CORSMiddleware` in `app/frontend_api.py`
(`CORS_ALLOW_ORIGINS`, default `*`) and the `websockets>=12` dependency in
`pyproject.toml` + `uv.lock`. Per **tech-stack**, the dependency is managed with
`uv` (no pip/requirements.txt) and `requires-python >= 3.14` is unchanged.

**Logging (ai-build-logging).** After the apply work completes, a markdown report
is written to `docs/ai-build-logs/<session-id>-<short-name>.md` and one line is
appended to `docs/ai-build-logs/index.md`.

**Boundary.** No telemetry field rename. No data-flow re-shape. The final
Playwright/llm-judge proof and any git commit are left to the parent; this change
verifies the live path mechanically (slot LSN advancing, a `fleet:events` message,
a non-snapshot WS patch, CORS header, WS 101, k6 still `Up`) and keeps the existing
pytest + `npm run test:ui` suites green.

## Tasks

- [x] 1.1 In the runtime `docker-compose.yml`, confirm/add to the `cdc` service's `depends_on` the `migrate: { condition: service_completed_successfully }` condition alongside `db`/`redis` `service_healthy`, mirroring the `ingestion` service
- [x] 2.1 Harden `app/cdc_consumer.py`'s `run_forever` supervisor so the readiness wait (publication/tables absent) retries indefinitely with a bounded short backoff and never exits or exhausts retries
- [x] 2.2 Harden `run_forever` so a transient mid-stream stream failure is logged and the stream is restarted indefinitely (no path terminates the loop except `stop`), and ensure every wait/restart is logged so the service is never silent
- [x] 2.3 Preserve the slot-anchoring invariant (create the slot only after the publication exists) and leave the decode/translate/publish core and the SIGTERM/SIGINT clean-shutdown path unchanged
- [x] 3.1 In `/k6/fleet-simulation.js`, replace the finite `per-vu-iterations`/`iterations: 1` executor with a continuous `constant-vus` executor at `FLEET_SIZE` VUs and an effectively-unbounded (env-overridable) duration
- [x] 3.2 Restructure the per-VU body so each VU's per-vehicle state persists across iterations and one ~1 Hz stateful tick runs per iteration (move + drain on a normal tick)
- [x] 3.3 Keep the realistic-zone crossings, the shift-change charging-bay convergence with battery recovery, and the occasional fault injection (anomaly-tripping telemetry + `POST /vehicles/{id}/status` "fault") under the continuous loop
- [x] 3.4 Keep the canonical telemetry shape with the deployed field names `recorded_at`/`pos_x`/`pos_y` (do NOT rename to `timestamp`/`lat`/`lon`) and `zone_entered` null on non-crossing ticks
- [x] 3.5 Keep the read-back checks (`GET /vehicles`, `/zones/counts`, `/vehicles/anomalies/latest` reflect the load) exercised periodically and keep the thresholds (p95 ingest latency, error rate, check pass-rate, `http_req_failed`) so a breach exits non-zero
- [x] 3.6 In the runtime `docker-compose.yml`, ensure the `k6` service stays `Up` driving load via a long in-script duration and/or a `restart` policy
- [x] 4.1 Verify and keep the env-driven `CORSMiddleware` in `app/frontend_api.py` (`CORS_ALLOW_ORIGINS`, default `*`, FastAPI `CORSMiddleware`) for cross-origin REST from the dashboard origin
- [x] 4.2 Verify and keep the `websockets` dependency in `pyproject.toml` and `uv.lock` (uv-managed, `requires-python >= 3.14` unchanged) so the WS upgrade returns 101
- [x] 5.1 Bring the stack up `docker compose up -d --build`; confirm `migrate` exits 0 and `cdc` starts after it
- [x] 5.2 Confirm CDC is live: `fleet_cdc_slot.confirmed_flush_lsn` advances across two samples under load (db `fleet`) and a `redis-cli subscribe fleet:events` receives ≥1 real event message under load
- [x] 5.3 Confirm a WebSocket client against `ws://localhost:8002/ws` receives ≥1 non-snapshot patch under load, and `redis-cli pubsub numsub fleet:events` ≥ 1 while connected
- [x] 5.4 Confirm the REST CORS header (`curl -i -H 'Origin: http://localhost:8080' http://localhost:8002/vehicles` → `access-control-allow-origin`) and that the WS handshake returns 101
- [x] 5.5 Confirm `k6` is still `Up` after a minute (load continuous)
- [x] 5.6 Keep the existing suites green: `docker compose -f docker-compose.test.yml run --rm api pytest` and `... run --rm web npm run test:ui`
- [x] 5.7 `docker compose down -v` so the parent starts clean (do NOT open a browser / run the llm-judge / commit)
- [x] 6.1 Write the AI build-log report to `docs/ai-build-logs/*.md` and append one line to `docs/ai-build-logs/index.md`
