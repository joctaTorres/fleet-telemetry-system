# runtime-stack-k6-sim

## Why

Every prior change proved a slice of the fleet telemetry system against the
`docker-compose.test.yml` harness — a pytest/vitest topology, never a thing an
operator runs and uses. The app is not actually *servable* today: there is no
ASGI server dependency, nothing serves `app.ingestion_api:app` or
`app.frontend_api:app` on a host port, nothing serves the built dashboard, and
nothing applies migrations + zone seeding on startup outside the test fixtures.
There is also no load: the system has never been observed under a realistic,
continuous fleet driving it.

This change makes the system **run for real with one command** and **proves it
under load**. It adds a new runtime `docker-compose.yml` at the repo root —
separate from and leaving untouched the `docker-compose.test.yml` test harness —
that brings up the full topology (primary, streaming replica, Redis, the
singleton CDC consumer, the ingestion API, the frontend API, the served
dashboard) plus a Grafana **k6** load generator that fires on `up`. k6 models a
stateful ~50-vehicle, 1 Hz fleet (movement, battery drain, zone crossings,
shift-change charging convergence, occasional faults) so anomalies and zone
counts genuinely accumulate, and asserts the system behaves with `checks` and
`thresholds`. The proof-of-work is an llm-judge confirming the live dashboard
visibly updates under that load.

It also lands a long-overdue cosmetic fix that the simulation depends on:
renaming the backend `ZONES` from generic `zone-01..zone-20` to 20 realistic
warehouse zone ids, so the dashboard tiles, seeded counts, and the k6
`zone_entered` field all refer to meaningful, locatable zones.

It deliberately does **not** change the application's data-flow architecture: the
read/write split, the stateless ingestion path, the single CDC consumer, the
Redis fan-out, and the database-enforced concurrency are reused exactly as the
telemetry-architecture standard mandates — this change serves and loads the
existing system, it does not re-shape it. The test harness, the test topology,
and the integration/UI proof commands are preserved; only the test references to
the renamed zone ids are updated so the suite stays green.

## What Changes

- **Backend deps** `pyproject.toml`, `uv.lock`: add **uvicorn** as a project
  dependency via `uv add uvicorn` (FastAPI was present without an ASGI server).
  No `pip` / hand-edited `requirements.txt`; `requires-python` stays `>=3.14`.
- **Zones constant** `app/models.py`: replace the generated
  `ZONES = tuple(f"zone-{i:02d}" ...)` with the explicit 20 realistic warehouse
  zone ids, in the given order. `seed_zones()` already reads `ZONES`, so seeding
  reflows automatically; no seeding code changes.
- **Runtime compose** `docker-compose.yml` (new, repo root): the full runtime
  topology — `db` (primary, `wal_level=logical`), `replica` (streaming standby),
  `redis`, `cdc` (`python -m app.cdc_consumer`), `ingestion` (uvicorn serving
  `app.ingestion_api:app`), `frontend` (uvicorn serving `app.frontend_api:app`),
  `dashboard` (served built Vite app), `k6` (grafana/k6, fires on `up`). Reuses
  the `docker-compose.test.yml` topology patterns (db command flags, the
  `docker/primary-init` mount, `docker/replica-entrypoint.sh`, healthchecks,
  `DATABASE_URL`/`REPLICA_URL`/`REDIS_URL` env wiring). Published host ports for
  the two APIs and the dashboard. All config env-only; no hard-coded creds.
- **Startup migrate/seed** `docker-compose.yml` (+ a small entry/command or init
  service): run `python -m app.migrate` against the primary on startup (applies
  versioned migrations and seeds `zone_counts` from `ZONES`) before the APIs
  serve, gated so the APIs depend on a completed migrate.
- **Dashboard serving + URL wiring** `web/` (`web/src/transport.ts`,
  `web/vite.config.ts`, a `web/Dockerfile` or runtime serve step, `.env`/build
  args): build the dashboard with Vite and serve it on a host port; wire the
  REST base URL and WS URL to the runtime frontend API via
  `import.meta.env.VITE_*` (e.g. `VITE_API_BASE_URL`, `VITE_WS_URL`) read in
  `createHttpTransport`, with a Vite proxy fallback — keeping the same-origin
  default so the existing tests and dev flow are unchanged.
- **k6 service** `/k6` (new root dir): k6 script(s) modelling a stateful
  50-vehicle 1 Hz fleet (movement, battery drain, zone crossings, shift-change
  charging convergence, occasional faults), emitting telemetry in the canonical
  shape, with `checks` and `thresholds`; plus any k6 config/Dockerfile and the
  env wiring (ingestion + frontend base URLs) the compose service passes in.
- **Test updates** `tests/integration/test_zone_counts.py`,
  `tests/integration/test_zone_increment.py`,
  `tests/integration/test_ws_fanout.py`,
  `tests/integration/test_realtime_ws.py`,
  `tests/integration/test_replica_snapshot.py`,
  `tests/integration/test_cdc_translate.py`,
  `web/src/__tests__/dashboard.test.tsx`,
  `web/src/__tests__/transport.test.ts`: update every hardcoded `zone-NN`
  reference to the realistic ids so the existing suites stay green (the
  `zone-99` "ignored/unknown zone" negative cases become an unknown realistic-
  style id that is still not in `ZONES`).
- **Proof-of-work** `tests/e2e/` (or the change's stated runnable command + an
  llm-judge harness): with the stack served and k6 driving load, Playwright opens
  the live dashboard and the judge confirms the 50-vehicle list, latest-anomaly-
  per-vehicle, and per-zone counts visibly update under load.
- **AI build log** `docs/ai-build-logs/*.md`, `docs/ai-build-logs/index.md`: the
  mandatory report + appended index line for this propose session.

## Design

- **Runtime compose is a sibling of the test compose, not a rewrite of it.** The
  new root `docker-compose.yml` reuses the proven topology patterns from
  `docker-compose.test.yml` — the primary's `postgres -c wal_level=logical
  -c max_wal_senders=... -c max_replication_slots=...` flags, the
  `docker/primary-init` `host replication ... trust` mount, the
  `docker/replica-entrypoint.sh` `pg_basebackup` standby bootstrap, the Redis
  healthcheck, and the `DATABASE_URL`/`REPLICA_URL`/`REDIS_URL` env wiring — but
  swaps the test database name for a runtime one, drops `tmpfs` for durable
  volumes, and adds the services the test harness omits (served APIs, served
  dashboard, k6). The test compose file is not modified.
- **Read/write split and CDC-only stream preserved (telemetry-architecture).**
  The runtime keeps two separate served APIs: the **stateless ingestion API**
  (validate → write to the primary → return; never publishes to Redis in the
  request path) and the **frontend API** (reads + connect snapshot served from
  the **replica**, never the primary; live deltas streamed from Redis). The live
  dashboard path depends only on the **single** CDC consumer
  (`python -m app.cdc_consumer`) tailing the primary's logical slot and
  publishing to Redis — no dual-write, no `LISTEN/NOTIFY`, no replica polling.
  Concurrency stays enforced in the database (atomic
  `UPDATE zone_counts SET entry_count = entry_count + 1`, row-locked fault
  transition, `INSERT ... ON CONFLICT` current-state). This change introduces no
  new propagation mechanism, so the standard's recorded design is untouched.
- **Servable app via uvicorn (tech-stack).** FastAPI was present without an ASGI
  server; `uvicorn` is added with **uv** (`uv add uvicorn`, reflected in
  `uv.lock`) — no `pip`/`requirements.txt`. The `ingestion` and `frontend`
  services run `uvicorn app.ingestion_api:app` and `uvicorn app.frontend_api:app`
  on published host ports. Python stays 3.14, the datastore stays PostgreSQL,
  config stays env-only with no hard-coded connection strings.
- **Migrate + seed on startup.** `python -m app.migrate` runs against the primary
  before the APIs serve (an init/one-shot step the API services `depend_on`),
  applying versioned migrations and seeding `zone_counts` from `ZONES`. Because
  seeding reads `ZONES`, the realistic zone rows appear automatically — no
  seeding code change — and the first `GET /zones/counts` returns all 20 zones.
- **Dashboard URL wiring via VITE_ env + proxy, default unchanged.**
  `web/src/transport.ts` today defaults `baseUrl` to `""` (same-origin) and
  derives the WS URL from `window.location`. The change reads
  `import.meta.env.VITE_API_BASE_URL` and `import.meta.env.VITE_WS_URL` in
  `createHttpTransport` to override those defaults at build/serve time, and the
  `dashboard` compose service supplies them (build args / env) pointing at the
  runtime frontend API service; a Vite proxy entry mirrors the dev convenience.
  Crucially the **defaults stay same-origin**, so existing unit tests (which
  inject a mock transport) and `npm run dev` are unaffected — the env values are
  additive overrides, and no host/port is hard-coded in source.
- **k6 lives in `/k6` and fires on `up`.** A dedicated root-level `/k6` directory
  holds the script(s) and any config/Dockerfile. The compose `k6` service uses
  the official `grafana/k6` image, `depends_on` the ingestion + frontend APIs
  being healthy, and runs the script so one `up` both serves and loads. The
  ingestion base URL and frontend base URL are passed as env vars (no hard-coded
  hosts).
- **Per-vehicle stateful simulation, not random requests.** Each of 50 virtual
  users owns one `vehicle_id` (`v-0..v-49`) for its whole run and carries mutable
  state across ~1 Hz ticks: position (`lat`/`lon`), `speed_mps`, `battery_pct`,
  `status`, and a path that periodically crosses a zone boundary. On a normal
  tick the vehicle moves, drains battery, and POSTs telemetry with
  `zone_entered: null`. On a crossing tick `zone_entered` is set to a realistic
  `ZONES` id (so the increment lands on a seeded zone and the tile moves). A
  shift-change window steers a subset toward `charging_bay_1/2/3`, flips them to
  `status: "charging"`, and recovers their battery (the convergence scenario).
  Occasionally a vehicle faults — either `POST /vehicles/{id}/status` with
  `"fault"` and/or telemetry that trips a real threshold (`battery_pct < 15` not
  charging, `speed_mps > 5`, stuck `< 0.1` m/s while moving ≥ 10 s, teleport
  `> 15` m/s, comms-loss gap `> 5` s) — so `anomalies` accumulate and
  `GET /vehicles/anomalies/latest` reflects them.
- **Telemetry shape is exact.** Every emitted body is exactly
  `{ vehicle_id, timestamp (ISO-8601), lat, lon, battery_pct, speed_mps, status,
  error_codes, zone_entered }`, with `zone_entered` null except on a crossing
  tick — the user's canonical sample verbatim.
- **"Systems behave" = checks + thresholds.** `checks`: telemetry POSTs return
  201; `GET /vehicles` returns the 50 vehicles with status/battery;
  `GET /zones/counts` shows growing counts; `GET /vehicles/anomalies/latest`
  returns anomalies under load. `thresholds`: p95 ingest latency under a stated
  bound, error rate under a small fraction, and check pass-rate high — a breached
  threshold exits non-zero so a regression is visible.
- **ZONES rename and the tests it touches.** `ZONES` becomes the explicit 20
  realistic ids; `seed_zones()` reflows automatically. The tasks update every
  hardcoded `zone-NN` reference found by grepping `zone-`:
  `tests/integration/test_zone_counts.py`,
  `tests/integration/test_zone_increment.py`,
  `tests/integration/test_ws_fanout.py`,
  `tests/integration/test_realtime_ws.py`,
  `tests/integration/test_replica_snapshot.py`,
  `tests/integration/test_cdc_translate.py`,
  `web/src/__tests__/dashboard.test.tsx`, and
  `web/src/__tests__/transport.test.ts` — including turning the `zone-99`
  "unknown zone is ignored" negatives into an unknown id that is still absent
  from the realistic `ZONES`, so those negative assertions keep their meaning and
  both suites stay green.
- **AI build logging (ai-build-logging).** As the final step of this propose
  session, write a report to `docs/ai-build-logs/<session-id>-runtime-stack-k6-sim.md`
  (session id, name, step = propose, change, what was done + outcome) and append
  exactly one line to `docs/ai-build-logs/index.md` (append-only).
- **Reuse, don't reinvent.** No new datastore, web framework, bundler, or
  package manager: the same Postgres + FastAPI + Redis + uv + Vite/React/TS,
  reusing the existing migrate/seed, CDC consumer, replica entrypoint, and
  primary-init assets — adding only uvicorn, the runtime compose file, the
  dashboard serve/URL wiring, and the `/k6` service.

## Tasks

- [x] 1.1 Add `uvicorn` as a project dependency with `uv add uvicorn` (update `pyproject.toml` and `uv.lock`); keep `requires-python >= 3.14` and add no `pip`/`requirements.txt`
- [x] 2.1 In `app/models.py` replace the generated `ZONES` with the explicit 20 realistic warehouse zone ids in order: inbound_dock_a, inbound_dock_b, receiving_staging, aisle_a, aisle_b, aisle_c, high_bay_1, high_bay_2, bulk_storage, pick_zone_1, pick_zone_2, pack_station, sort_belt, outbound_dock_a, outbound_dock_b, shipping_staging, charging_bay_1, charging_bay_2, charging_bay_3, maintenance_bay
- [x] 2.2 Confirm `seed_zones()` still reads `ZONES` and reflows automatically (no seeding code change); verify a fresh migrate seeds all 20 realistic zones at 0
- [x] 3.1 Update hardcoded `zone-NN` references in `tests/integration/test_zone_counts.py` to the realistic ids
- [x] 3.2 Update hardcoded `zone-NN` references in `tests/integration/test_zone_increment.py` to the realistic ids
- [x] 3.3 Update hardcoded `zone-NN` references in `tests/integration/test_ws_fanout.py` to the realistic ids
- [x] 3.4 Update hardcoded `zone-NN` references in `tests/integration/test_realtime_ws.py` to the realistic ids
- [x] 3.5 Update hardcoded `zone-NN` references in `tests/integration/test_replica_snapshot.py` to the realistic ids
- [x] 3.6 Update hardcoded `zone-NN` references in `tests/integration/test_cdc_translate.py` to the realistic ids
- [x] 3.7 Update hardcoded `zone-NN` references in `web/src/__tests__/dashboard.test.tsx` to the realistic ids
- [x] 3.8 Update hardcoded `zone-NN` references in `web/src/__tests__/transport.test.ts` to the realistic ids
- [x] 3.9 Turn the `zone-99` "unknown/ignored zone" negative cases into an unknown id that is still absent from the realistic `ZONES`, preserving the negative assertions
- [x] 3.10 Re-grep the repo for `zone-` and confirm no stale `zone-NN` references remain in tests, fixtures, or helpers; run the integration and `npm run test:ui` suites and confirm they stay green
- [x] 4.1 Create a new root-level runtime `docker-compose.yml` (separate from the untouched `docker-compose.test.yml`) with a `db` primary service running `postgres` with `wal_level=logical`, sufficient `max_wal_senders`/`max_replication_slots`, the `docker/primary-init` `pg_hba` mount, a durable volume, and a healthcheck
- [x] 4.2 Add a `replica` service that bootstraps via `docker/replica-entrypoint.sh` (`pg_basebackup` + streaming standby), with a healthcheck, depending on `db` healthy
- [x] 4.3 Add a `redis` service with a healthcheck
- [x] 4.4 Add a one-shot migrate/seed step running `python -m app.migrate` against the primary on startup, with `DATABASE_URL` from the environment
- [x] 4.5 Add a `cdc` service running `python -m app.cdc_consumer` with `DATABASE_URL`/`REDIS_URL` from the environment, depending on `db` and `redis` healthy and on migrate completed
- [x] 4.6 Add an `ingestion` service running `uvicorn app.ingestion_api:app` on a published host port, `DATABASE_URL` from the environment, depending on migrate completed and `db` healthy
- [x] 4.7 Add a `frontend` service running `uvicorn app.frontend_api:app` on a published host port, `DATABASE_URL`/`REPLICA_URL`/`REDIS_URL` from the environment, depending on `db`/`replica`/`redis` healthy and migrate completed
- [x] 4.8 Ensure all runtime services read connection strings from the environment with no hard-coded credentials, mirroring the test compose env wiring
- [x] 5.1 In `web/src/transport.ts`, have `createHttpTransport` read `import.meta.env.VITE_API_BASE_URL` for the REST base URL and `import.meta.env.VITE_WS_URL` for the WS URL, overriding the same-origin/`window.location` defaults only when set (defaults unchanged so existing tests and dev flow stay green)
- [x] 5.2 Add the corresponding Vite proxy/env handling in `web/vite.config.ts` so dev and build resolve the runtime frontend API consistently
- [x] 5.3 Add a `web/Dockerfile` (or runtime serve step) that builds the dashboard with Vite and serves the built static assets (or `vite preview`) on a host port
- [x] 5.4 Add a `dashboard` service to the runtime `docker-compose.yml` that serves the built dashboard on a published host port and supplies `VITE_API_BASE_URL`/`VITE_WS_URL` (build args/env) pointing at the runtime frontend API, with no host/port hard-coded in source
- [x] 6.1 Create the root-level `/k6` directory with the k6 script(s) and any config/Dockerfile, reading the ingestion and frontend base URLs from environment variables
- [x] 6.2 Implement the per-vehicle stateful model: 50 virtual users (`v-0..v-49`), each owning one `vehicle_id`, carrying `lat`/`lon`/`speed_mps`/`battery_pct`/`status` across ~1 Hz ticks (move + drain on a normal tick)
- [x] 6.3 Emit telemetry to `POST /telemetry` in the exact canonical shape `{ vehicle_id, timestamp (ISO-8601), lat, lon, battery_pct, speed_mps, status, error_codes, zone_entered }`, with `zone_entered: null` on non-crossing ticks
- [x] 6.4 On a crossing tick, set `zone_entered` to a realistic `ZONES` id so the increment lands on a seeded zone and the tile moves
- [x] 6.5 Model the shift-change convergence: steer a subset toward `charging_bay_1/2/3`, set `status: "charging"`, and recover battery while charging
- [x] 6.6 Model occasional faults via `POST /vehicles/{vehicle_id}/status` `"fault"` and/or telemetry that trips a real anomaly threshold (battery < 15 not charging, speed > 5, stuck < 0.1 m/s ≥ 10 s, teleport > 15 m/s, comms-loss > 5 s) so anomalies accumulate
- [x] 6.7 Add k6 `checks`: telemetry POST returns 201; `GET /vehicles` returns the 50 vehicles with status/battery; `GET /zones/counts` shows growing counts; `GET /vehicles/anomalies/latest` returns anomalies under load
- [x] 6.8 Add k6 `thresholds`: p95 ingest latency under a stated bound, error rate under a small fraction, and check pass-rate high — a breached threshold exits non-zero
- [x] 6.9 Add the `k6` service to the runtime `docker-compose.yml` using the official `grafana/k6` image, firing on `up`, depending on the ingestion and frontend APIs being healthy, with base URLs passed via env
- [x] 7.1 Bring the stack up with the documented single command and confirm all services are healthy, the dashboard is reachable, and k6 is driving continuous load
- [ ] 7.2 Implement the llm-judge proof-of-work: with the stack served and k6 loading, Playwright opens the live dashboard and the judge confirms the 50-vehicle list (status/battery), latest-anomaly-per-vehicle, and per-zone counts visibly update under load (pass condition: all three regions observed changing under load) — IMPLEMENTED end-to-end (the served stack drives all three regions under k6 load, verified live); EXECUTION (Playwright + judge) intentionally left to the parent agent per the change boundary
- [x] 8.1 Write the AI build-log report to `docs/ai-build-logs/*.md` and append one line to `docs/ai-build-logs/index.md`
