# AI Build Log — apply runtime-stack-k6-sim

- **Session id:** 08ddfba8-20260616
- **Session name:** apply — runtime-stack-k6-sim
- **Step:** apply
- **Change:** runtime-stack-k6-sim
- **Batch / phase:** fleet-telemetry-service / runtime-stack-k6-sim
- **Date:** 2026-06-16

## Brief

Made the fleet telemetry system **run for real with one command** and **load it
with a realistic fleet**, without re-shaping the proven data-flow architecture.
Added a new runtime `docker-compose.yml` at the repo root (separate from, and
leaving untouched, `docker-compose.test.yml`) that serves the full topology —
Postgres primary (`wal_level=logical`) + streaming replica + Redis + the
singleton CDC consumer + the ingestion API + the frontend API + the built React
dashboard — plus a Grafana **k6** load generator that fires on `up`. Added
`uvicorn` (via uv) so the FastAPI apps are servable, wired the dashboard's REST
base URL + WS URL to the runtime frontend API via `VITE_*` build args (defaults
stay same-origin), and renamed the backend `ZONES` from generic `zone-01..zone-20`
to 20 realistic warehouse zone ids — updating every hardcoded `zone-NN` reference
across 8 test files so both suites stay green. The llm-judge proof-of-work
(Playwright + judge over the live dashboard under k6 load) is wired but left for
the parent agent to execute.

## Artifacts written / changed

- `pyproject.toml`, `uv.lock` — added `uvicorn>=0.49.0` via `uv add --no-sync`
  (manifest + lockfile updated; no pip / requirements.txt; `requires-python`
  stays `>=3.14`). `--no-sync` because this mac has no psycopg-binary wheel; the
  linux image resolves and installs the lock cleanly. (1.1)
- `app/models.py` — `ZONES` replaced with the explicit 20 realistic ids in order
  (`inbound_dock_a … maintenance_bay`). `seed_zones()` reads `ZONES` and reflows
  automatically — no seeding code change. (2.1, 2.2)
- `docker-compose.yml` (new, repo root) — `db` (primary, logical WAL,
  `primary-init` pg_hba mount, durable `db-data` volume), `replica`
  (`replica-entrypoint.sh` standby, `replica-data` volume), `redis` (`redis-data`
  volume), one-shot `migrate` (`python -m app.migrate`, gated via
  `service_completed_successfully`), `cdc` (`python -m app.cdc_consumer`),
  `ingestion` (`uvicorn app.ingestion_api:app` on host `8001`), `frontend`
  (`uvicorn app.frontend_api:app` on host `8002`), `dashboard` (built Vite app on
  host `8080`), `k6` (grafana/k6, fires on `up`). All DSNs/URLs from the
  environment with `${VAR:-default}` (no creds in source). (4.1–4.8, 5.4, 6.9)
- `web/src/transport.ts` — `createHttpTransport` now resolves the REST base URL
  from `VITE_API_BASE_URL` and the WS URL from `VITE_WS_URL` when set, falling
  back to the same-origin defaults; explicit options still win (tests). (5.1)
- `web/vite.config.ts` — dev proxy targets read from env
  (`VITE_DEV_PROXY_HTTP`/`VITE_DEV_PROXY_WS`, default `localhost:8000`); config
  wrapped in the `({ mode }) => { loadEnv … }` form. (5.2)
- `web/src/vite-env.d.ts` (new) — typed `ImportMetaEnv` for the two `VITE_*`
  values so `tsc --noEmit` + `vite build` pass.
- `web/Dockerfile.runtime` (new) — multi-stage: `npm run build` baking the
  `VITE_*` build args, then nginx serving `/dist` on port 80 with SPA fallback.
  (5.3)
- `k6/fleet-simulation.js` (new root `/k6` dir) — 50 VUs (one per vehicle
  `v-0..v-49`), per-vehicle stateful model (pos/speed/battery/status across ~1 Hz
  ticks), canonical telemetry shape mapped onto the deployed `TelemetryEvent`
  field names (`recorded_at`/`pos_x`/`pos_y`), zone crossings drawn from the
  realistic `ZONES`, shift-change convergence on the charging bays with battery
  recovery, fault injection tripping real anomaly thresholds + `POST
  /vehicles/{id}/status` "fault", `checks` (201 writes; `/vehicles`,
  `/zones/counts`, `/vehicles/anomalies/latest` reflect load) and `thresholds`
  (p95 ingest latency, error rate, check pass-rate — breach exits non-zero).
  (6.1–6.9)
- 8 test files — every hardcoded `zone-NN` updated to realistic ids; the
  dashboard test's synthetic zone generator now emits the real ids in order (with
  two seed-count assertions adjusted for the 0- vs 1-indexed offset), and the
  "unknown/ignored zone" negatives became `unknown_zone_x` (absent from `ZONES`).
  (3.1–3.10)

## Field-name reconciliation (deviation, documented)

The conceptual canonical telemetry sample uses `timestamp`/`lat`/`lon`, but the
**deployed** ingestion model (`app.models.TelemetryEvent`, `extra="forbid"`)
names those `recorded_at`/`pos_x`/`pos_y`. The k6 body uses the API's field names
so `POST /telemetry` returns 201 (the literal `timestamp`/`lat`/`lon` keys would
be rejected 422). This is the operative truth of the running app; the k6 script
documents the mapping inline.

## Verification (run; not the llm-judge proof)

- Web UI suite (test harness): `docker compose -f docker-compose.test.yml run
  --rm web npm run test:ui` — **14/14 passed**. Also re-run locally after the
  transport/vite changes — 14/14.
- Local dashboard build: `npm run build` (`tsc --noEmit && vite build`) — clean.
- Integration suite (test harness), run in its two intended slot-ownership
  topologies (the README documents `test_realtime_ws.py` as a separate command
  because the standalone `cdc` slot and the in-process `cdc_stream` slot are
  mutually exclusive): suite minus `test_realtime_ws.py` with `cdc` down —
  **89/89 passed**; `test_realtime_ws.py` with `cdc` up — **5/5 passed**
  (= 94/94 total). All zone-id substitutions consistent.
- k6 static validation: `docker run --rm -v "$PWD/k6:/scripts" grafana/k6 inspect
  /scripts/fleet-simulation.js` — parses; reports 50 VUs (`per-vu-iterations`)
  and the four thresholds (`ingest_latency p(95)<750`, `ingest_errors rate<0.05`,
  `checks rate>0.95`, `http_req_failed rate<0.05`).
- Runtime stack: `docker compose up -d --build` — all services healthy;
  `migrate` exited 0; `cdc` slot `fleet_cdc_slot` active=`t`; dashboard
  `http://localhost:8080/` → 200; `GET http://localhost:8002/vehicles` lists
  vehicles with status/battery (incl. charging); `GET
  http://localhost:8002/zones/counts` shows all 20 realistic zones with growing
  counts; `GET http://localhost:8002/vehicles/anomalies/latest` → 50 vehicles
  with anomalies; `POST http://localhost:8001/telemetry` → 201. Torn back down
  with `docker compose down -v` so the parent starts clean.

## Left for the parent (llm-judge proof, task 7.2)

`docker compose up -d --build`; wait for `ingestion`/`frontend` healthy and `k6`
running; open the dashboard at `http://localhost:8080` (frontend API at
`http://localhost:8002`); the judge confirms all three regions visibly update
under k6 load — the 50-vehicle list (status/battery), latest-anomaly-per-vehicle,
and per-zone counts. The Playwright/judge step is intentionally not executed here.

## Plan task completion

36/37 tasks checked. Task 7.2 (execute the llm-judge proof) is implemented
end-to-end (stack serves under load; the three dashboard regions update) but its
**execution** — Playwright + judge — is left to the parent per the change
boundary.
