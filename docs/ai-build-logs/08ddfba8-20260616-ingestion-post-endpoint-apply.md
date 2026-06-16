# AI Build Log — apply ingestion-post-endpoint

- **Session id:** 08ddfba8-20260616
- **Session name:** apply — ingestion-post-endpoint
- **Step:** apply
- **Change:** ingestion-post-endpoint
- **Batch / phase:** fleet-telemetry-service / ingest-and-fleet-state
- **Date:** 2026-06-16

## Brief

Added the stateless ingestion API's first route, `POST /telemetry`, turning the
persistence layer into a reachable end-to-end write slice: an HTTP client emits an
event, it is validated, and persisted as the vehicle's authoritative current state.
All plan tasks 1.1–4.1 completed; the integration suite passes against a real Postgres.

## Artifacts written

- `pyproject.toml` — added `httpx>=0.27` to the dev group (FastAPI `TestClient`
  dependency); `uv.lock` re-resolved. (1.1)
- `app/ingestion_api.py` — a dedicated FastAPI `app` instance for the stateless
  ingestion service (kept separate from the future frontend API). (1.2) Defines
  `POST /telemetry`: the body is validated into the existing `TelemetryEvent`
  (Pydantic, `extra="forbid"`), so schema-invalid input is rejected with 422 before
  the handler runs (2.1); valid input calls the existing `persist_telemetry(event)`
  and returns 201 Created — validate → write → return, no broker publish. (2.2)
- `tests/integration/test_ingest_post.py` — in-process `TestClient` (ASGI) tests:
  valid POST → 201 with one `raw_events` + one `vehicle_current_state` row (3.1);
  a later POST for the same vehicle upserts the single current-state row and appends
  raw (3.2); schema-invalid bodies (bad status, battery out of 0..100, missing field,
  empty/missing `vehicle_id`, unknown field) → 422 and nothing persisted (3.3).

## Design alignment

- Per `telemetry-architecture`: the ingestion API is its own FastAPI instance, stateless,
  with the request path exactly validate → write to Postgres → return. No in-process
  aggregate, no Redis/broker publish in the write path (the dashboard stream comes from CDC
  in a later phase). No read endpoint here — `GET /fleet/state` is the separate
  `fleet-state-endpoint` change.
- Reuse, don't reinvent: validation is the existing `TelemetryEvent`; the write is the
  existing atomic `persist_telemetry`. This change adds only the HTTP adapter. 201 Created
  because the event is synchronously committed before the response returns.

## Outcome

`docker compose -f docker-compose.test.yml run --rm api pytest -q tests/integration/test_ingest_post.py`
→ exit 0, 9 passed. Full suite `tests/integration` → 15 passed (6 prior + 9 new). The api
image was rebuilt so the new source/tests are present in the container. Plan tasks 1.1–4.1
checked off.
