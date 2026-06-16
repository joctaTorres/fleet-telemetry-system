# AI Build Log — apply fleet-state-endpoint

- **Session id:** 08ddfba8-20260616
- **Session name:** apply — fleet-state-endpoint
- **Step:** apply
- **Change:** fleet-state-endpoint
- **Batch / phase:** fleet-telemetry-service / ingest-and-fleet-state
- **Date:** 2026-06-16

## Brief

Added the read half of the `ingest-and-fleet-state` phase: a dedicated frontend
(dashboard) FastAPI app exposing `GET /fleet/state`, turning the existing
`aggregate_fleet_state()` into a reachable REST endpoint. This completes the
phase's end-to-end slice (POST writes → GET reads back). All plan tasks 1.1–4.1
completed; the integration suite passes against a real Postgres (18/18, exit 0).

## Artifacts written

- `app/frontend_api.py` — a dedicated FastAPI `app` instance for the frontend
  (dashboard) service, kept separate from the stateless ingestion API per the
  telemetry-architecture standard. (1.1) Defines `GET /fleet/state`, which calls
  the existing `aggregate_fleet_state()` and returns 200 OK with the per-status
  counts as JSON — all four status keys always present, statuses with no vehicles
  zero-filled. The route derives the aggregate fresh from the DB each request;
  it holds no in-process counter. (2.1)
- `tests/integration/test_fleet_state_get.py` — in-process `TestClient` (ASGI)
  tests: GET over a mix of statuses returns 200 with correct per-status counts,
  absent statuses reported as 0 (3.1); GET against an empty database returns all
  four statuses as 0 (3.2).
- `tests/integration/test_ingest_fleet_state.py` — phase proof-of-work: drives
  the ingestion app (POST) and frontend app (GET) against the same Postgres.
  Concurrently POSTs telemetry for 50 distinct vehicles across mixed statuses
  (each vehicle emits an initial then a deterministic final status sequentially
  within its worker; the 50 workers race), then asserts `GET /fleet/state` counts
  sum to 50 and exactly match the last event per vehicle — no lost or
  double-counted upserts. (3.3)

## Design alignment

- Per `telemetry-architecture`: the read endpoint lives on its own frontend
  FastAPI instance, *not* merged onto the stateless write-only ingestion API
  ("two separate APIs, do not merge them"). No authoritative in-process
  aggregate; counts come from a single `GROUP BY status` MVCC snapshot, so they
  always sum to the number of distinct vehicles and are safe under concurrent
  upserts — concurrency correctness stays in the database.
- Scoped deviation: the standard serves REST reads from a streaming read replica.
  This phase is scoped to a single Postgres (no replica/CDC/Redis yet), so the
  read is served from the primary here — a deliberate, temporary stepping stone
  the later read/write-split phase moves to the replica. No change to the standard
  is required; the propagation mechanism is unchanged.
- Reuse, don't reinvent: the aggregation already existed; this change adds only
  the HTTP adapter and the cross-API integration test.

## Outcome

`docker compose -f docker-compose.test.yml run --rm api pytest tests/integration/test_ingest_fleet_state.py`
→ exit 0, 1 passed (proof-of-work). Full suite `tests/integration` → 18 passed
(15 prior + 3 new). The api image was rebuilt so the new source/tests are present
in the container. Plan tasks 1.1–4.1 checked off.
