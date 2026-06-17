# ingestion-post-endpoint

## Why

The `telemetry-persistence` change built the correct data tier — schema, the atomic
`persist_telemetry(event)` write path, and the `aggregate_fleet_state()` read — but no
network surface. A vehicle still cannot send a reading. This change adds the **stateless
ingestion API's** first route, `POST /telemetry`, turning the persistence layer into a
reachable end-to-end slice: an HTTP client emits an event, it is validated and persisted,
and it becomes the vehicle's authoritative current state.

It is one of the two HTTP changes in the `ingest-and-fleet-state` phase; `GET /fleet/state`
is the separate `fleet-state-endpoint` change. Together they satisfy the phase
proof-of-work; this change owns the write half.

## What Changes

- Add a **FastAPI ingestion application** (a dedicated app instance, kept separate from the
  future frontend API per the telemetry-architecture standard) exposing `POST /telemetry`.
- The route accepts a JSON body, validates it into the existing `TelemetryEvent` model
  (Pydantic, `extra="forbid"`, status enum `idle|moving|charging|fault`, `battery_pct`
  in 0..100, non-empty `vehicle_id`), and on success calls the existing
  `persist_telemetry(event)`, then returns **201 Created**.
- Schema-invalid bodies (bad status, out-of-range battery, missing field, unknown field)
  are rejected with **422** by FastAPI/Pydantic validation, and nothing is persisted.
- Wire the app so it is importable for in-process testing and runnable in the container.

## Design

- **Vertical-slice scope:** the thinnest HTTP write path that proves the phase goal end to
  end — request → validation → `persist_telemetry` → Postgres. No anomaly detection, no
  CDC/Redis, no read endpoint; those are out of scope for this change.
- **Stateless ingestion, per the telemetry-architecture standard.** The request path is
  exactly *validate → write to Postgres → return*. The endpoint holds no authoritative
  in-process aggregate and MUST NOT publish to Redis or any broker in the request path
  (the dashboard's stream comes from CDC in a later phase, not from the writer).
- **Separate APIs.** The ingestion app is its own FastAPI instance so the dashboard's
  read/WebSocket surface is never merged into the writer.
- **Reuse, don't reinvent.** Validation is the existing `TelemetryEvent`; the write is the
  existing atomic `persist_telemetry`. This change adds only the HTTP adapter.
- **Status code.** `201 Created` — the event is synchronously committed before the response
  returns, so the resource exists when the client sees success.
- **Testing.** Exercised in-process with FastAPI's `TestClient` (ASGI) against the real
  Postgres from `docker-compose.test.yml`; this needs `httpx` as a dev/test dependency.
  A running ASGI server (uvicorn) is not required for this slice and is deferred.

## Tasks

- [x] 1.1 Add `httpx` to the dev dependency group (required by FastAPI's `TestClient`)
- [x] 1.2 Add `app/ingestion_api.py` defining a dedicated FastAPI `app` instance for the stateless ingestion service
- [x] 2.1 Implement `POST /telemetry`: accept the JSON body as a `TelemetryEvent`, returning 422 on schema-invalid input
- [x] 2.2 On valid input, call `persist_telemetry(event)` and return `201 Created` (validate → write → return; no broker publish)
- [x] 3.1 Integration test: POST a valid event → 201, and `raw_events` + `vehicle_current_state` each hold the expected row
- [x] 3.2 Integration test: POST a later event for the same vehicle → still one current-state row, reflecting the latest event; raw appended
- [x] 3.3 Integration test: POST schema-invalid bodies (bad status, battery out of 0..100, missing field, unknown field) → 422 and nothing persisted
- [x] 4.1 Write the AI build log report to `docs/ai-build-logs/*.md` and append one line to `docs/ai-build-logs/index.md`
