# fleet-state-endpoint

## Why

The `telemetry-persistence` change built `aggregate_fleet_state()` — a `GROUP BY status`
read over `vehicle_current_state` — and `ingestion-post-endpoint` added the `POST /telemetry`
write surface. But the aggregate has no network surface: the dashboard cannot yet read the
current fleet state. This change adds the read half of the `ingest-and-fleet-state` phase,
`GET /fleet/state`, turning the existing aggregate into a reachable REST endpoint and
completing the phase's end-to-end slice (POST writes → GET reads back).

## What Changes

- Add a **frontend API** (a dedicated FastAPI application, separate from the ingestion API
  per the telemetry-architecture standard) exposing `GET /fleet/state`.
- The route calls the existing `aggregate_fleet_state()` and returns **200 OK** with a JSON
  object of per-status counts: `{"idle": n, "moving": n, "charging": n, "fault": n}`. All
  four status keys are always present; statuses with no vehicles report `0`.
- Wire the app so it is importable for in-process testing and runnable in the container.
- Add the phase proof-of-work integration test
  `tests/integration/test_ingest_fleet_state.py`: concurrently POST telemetry for 50
  distinct vehicles across mixed statuses via the ingestion API, then assert
  `GET /fleet/state` returns per-status counts that sum to 50 and exactly match the last
  event per vehicle (no lost or double-counted upserts).

## Design

- **Vertical-slice scope:** the thinnest HTTP read path that proves the phase goal — request
  → `aggregate_fleet_state()` → JSON. No anomaly history, no WebSocket, no CDC/Redis; those
  are out of scope for this change.
- **Separate APIs, per the telemetry-architecture standard.** The read endpoint goes on a new
  **frontend API** instance, *not* on the ingestion API: the ingestion API MUST stay
  stateless and write-only (`validate → write → return`). Merging a read route onto it would
  violate the standard's "two separate APIs, do not merge them" rule.
- **No authoritative in-process aggregate.** The frontend API derives the aggregate fresh
  from the database on each request via the existing `GROUP BY` read; it holds no in-process
  counter that could diverge from committed state.
- **Concurrency correctness stays in the database.** Counts come from a single `GROUP BY
  status` over `vehicle_current_state` in one MVCC snapshot, so they always sum to the number
  of distinct vehicles and are safe under concurrent upserts — no application-level
  read-modify-write. This reuses `aggregate_fleet_state()` unchanged.
- **Scoped deviation — reads from the primary, not a replica.** The standard says REST reads
  MUST be served from a streaming read replica. This phase is explicitly scoped to a *single
  Postgres, no replica/CDC/Redis yet*, so `GET /fleet/state` reads the primary here. This is
  a deliberate, temporary stepping stone; the later read/write-split phase moves this read to
  the replica. No change to the standard is required — concurrency control stays in the DB
  and the propagation mechanism is unchanged.
- **Reuse, don't reinvent.** The aggregation already exists; this change adds only the HTTP
  adapter and the cross-API integration test.
- **Testing.** Exercised in-process with FastAPI's `TestClient` (ASGI, no running uvicorn)
  against the real Postgres from `docker-compose.test.yml`. The proof-of-work test drives the
  ingestion app (POST) and the frontend app (GET) against the same database.

## Tasks

- [x] 1.1 Add `app/frontend_api.py` defining a dedicated FastAPI `app` instance for the frontend (dashboard) service, separate from the ingestion API
- [x] 2.1 Implement `GET /fleet/state`: call `aggregate_fleet_state()` and return 200 with the per-status counts as JSON, all four status keys always present and zero-filled
- [x] 3.1 Integration test: GET `/fleet/state` over a mix of vehicle statuses returns 200 and the correct per-status counts, with absent statuses reported as 0
- [x] 3.2 Integration test: GET `/fleet/state` against an empty database returns all four statuses as 0
- [x] 3.3 Proof-of-work test `tests/integration/test_ingest_fleet_state.py`: concurrently POST telemetry for 50 distinct vehicles across mixed statuses via the ingestion app, then assert GET `/fleet/state` counts sum to 50 and exactly match the last event per vehicle (no lost or double-counted upserts)
- [x] 4.1 Write the AI build-log report to `docs/ai-build-logs/*.md` and append one line to `docs/ai-build-logs/index.md`
