# AI Build Log — apply runtime-stack-live-fixes

- **Session id:** 9f2c7a1d-20260616234227
- **Session name:** apply — runtime-stack-live-fixes
- **Step:** apply
- **Change:** runtime-stack-live-fixes
- **Batch / phase:** standalone (not in any batch)
- **Date:** 2026-06-16

## Brief

Fixed the runtime live-update path that `runtime-stack-k6-sim` left dead, so
committed writes under load actually become live CDC deltas the dashboard renders.
Two diagnosed root causes plus two kept cross-origin/WS fixes.

**Root cause 1 — CDC live-delta path dead in the runtime.**
- Runtime `docker-compose.yml` `cdc` service confirmed gating on
  `migrate: { condition: service_completed_successfully }` (alongside `db`/`redis`
  `service_healthy`), mirroring `ingestion`, so `cdc` first attempts to stream only
  after the publication + watched tables exist.
- Hardened `app/cdc_consumer.py`'s `run_forever` supervisor: the retry is now
  **unbounded in count and time** — the readiness probe (publication/tables absent,
  primary unreachable) and the stream attempt (late/missing slot or table, primary
  blip, transient mid-stream error) are each retried indefinitely with a bounded
  short backoff, no path exits the loop except `stop`, and every wait/restart is
  logged so the service is never silent. The slot-anchoring invariant (create the
  slot only after the publication exists) and the decode/translate/publish core and
  SIGTERM/SIGINT clean shutdown were left untouched.

**Root cause 2 — k6 not continuous.**
- `/k6/fleet-simulation.js` switched from finite `per-vu-iterations`
  (`iterations: 1`) to a continuous `constant-vus` executor at `FLEET_SIZE` VUs with
  an effectively-unbounded `DURATION` (default `720h`, env-overridable). The per-VU
  body was restructured so per-vehicle state persists across iterations (module-scope
  map keyed by `__VU`), one ~1 Hz stateful tick runs per iteration, and the
  move/drain → realistic-zone crossing → shift-change charging convergence → fault
  injection cycle recurs every `SHIFT_PERIOD` ticks. Canonical telemetry shape kept
  with the deployed field names (`recorded_at`/`pos_x`/`pos_y` — NOT renamed). All
  checks and the four thresholds (p95 ingest latency, error rate, check pass-rate,
  `http_req_failed`) preserved. Compose `k6` service gained `restart: unless-stopped`
  so it keeps driving load for the life of the stack.

**Kept/formalized (parent's working-tree fixes).**
- `app/frontend_api.py`: env-driven `CORSMiddleware` (`CORS_ALLOW_ORIGINS`, default
  `*`) verified present and kept.
- `pyproject.toml` + `uv.lock`: `websockets>=12` (and `uvicorn`) verified present and
  kept so uvicorn completes the WS upgrade with 101 instead of 404.

## Outcome

`docker compose up -d --build` came up healthy. Verified:
- `migrate` exited 0 at 02:34:48.744Z; `cdc` started at 02:34:48.878Z (after it).
  `cdc` logged `CDC consumer streaming slot fleet_cdc_slot` — no longer silent.
- `fleet_cdc_slot.confirmed_flush_lsn` advanced under load: `0/3170A48` → `0/322ABE8`.
- `redis-cli subscribe fleet:events` received real `vehicle_state_changed` messages
  during load.
- A WebSocket client on `ws://…:8002/ws` received the connect snapshot then a live
  non-snapshot patch (`vehicle_state_changed` v-3); `pubsub numsub fleet:events` = 1
  while connected (the single shared frontend subscription that fans out to WS).
- CORS: `GET /vehicles` with `Origin: http://localhost:8080` →
  `access-control-allow-origin: *`. WS handshake → `101 Switching Protocols`.
- `k6` still `Up` after 2+ minutes, 50/50 VUs, 6700+ iterations, continuous.
- Suites green across the two intended slot-ownership topologies: 89 passed (cdc
  down) + 5 `test_realtime_ws.py` (cdc up) = 94/94 integration; web `test:ui` 14/14.
  The hardened supervisor was observed recovering live in the test run — it logged
  `publication fleet_events_pub not present yet; retrying in 2.0s … 4.0s … 8.0s`
  then streamed once migrate created the publication.

Then `docker compose down -v` (and the test compose down -v) so the parent starts
clean. No browser opened, no llm-judge run, **no git commit** — the Playwright/
llm-judge proof and commit are left to the parent.

Plan tasks: 19/20 complete (only the build-log task 6.1 — this report — remained,
now done). Standards honored: telemetry-architecture, tech-stack, ai-build-logging.
