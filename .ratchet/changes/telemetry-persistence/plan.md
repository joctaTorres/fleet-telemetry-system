# telemetry-persistence

## Why

This is the root change of the `ingest-and-fleet-state` phase. Before there can be a
POST endpoint or a fleet-state read, the system needs a correct persistence foundation:
a Postgres schema and an idempotent write path that records every telemetry event and
keeps an authoritative per-vehicle current state. Getting concurrency correctness right
here (in the database, not in application code) is what makes the downstream endpoints
safe under bursts of writes.

## What Changes

- Introduce versioned Postgres migrations for two tables:
  - `raw_events` — append-only log of every telemetry event.
  - `vehicle_current_state` — one row per vehicle (PK `vehicle_id`) holding its latest reading.
- Add a persistence module exposing two operations:
  - `persist_telemetry(event)` — within one transaction, append to `raw_events` and
    upsert `vehicle_current_state` via `INSERT ... ON CONFLICT (vehicle_id) DO UPDATE`.
  - `aggregate_fleet_state()` — `SELECT status, COUNT(*) ... GROUP BY status` over
    `vehicle_current_state`, returning per-status counts for idle | moving | charging | fault.
- Read DB connection configuration from the environment (no hard-coded credentials).
- Add a test Postgres service (`docker-compose.test.yml`) so concurrency claims are
  exercised against a real Postgres.

This change does NOT add HTTP routes — `POST /telemetry` and `GET /fleet/state` are the
separate `ingestion-post-endpoint` and `fleet-state-endpoint` changes that depend on this one.

## Design

- **Vertical slice scope:** the thinnest persistence layer that proves the phase goal
  end to end at the data tier — schema + idempotent write + aggregate read — without the
  HTTP surface.
- **Concurrency in the database, per the telemetry-architecture standard.** The aggregate
  is derived from the per-vehicle current-state table (`ON CONFLICT DO UPDATE`, then
  `GROUP BY status`), never from a materialized counter that concurrent writers race on.
  The upsert is a single server-side statement, so there is no application-level
  read-then-write and no lost-update window. Last-event-wins per vehicle.
- **Atomicity.** The raw append and the current-state upsert happen in one transaction,
  so a committed event is always reflected in both tables (or neither).
- **MVCC consistency.** `aggregate_fleet_state()` reads a single consistent snapshot, so
  per-status counts always sum to the number of distinct vehicles, with no torn reads.
- **Tech stack:** Python 3.14, FastAPI app package, PostgreSQL, deps via uv; schema via
  versioned migrations; connection config from environment variables.

## Tasks

- [x] 1.1 Add Postgres + uv project scaffolding (`pyproject.toml`, `requires-python >=3.14`, DB driver) if not present
- [x] 1.2 Add `docker-compose.test.yml` with a Postgres service for integration tests
- [x] 1.3 Add environment-based DB connection config (no hard-coded credentials/DSN)
- [x] 2.1 Write a versioned migration creating `raw_events` (append-only, indexed by vehicle_id, timestamp)
- [x] 2.2 Write a versioned migration creating `vehicle_current_state` (PK `vehicle_id`, status CHECK in idle|moving|charging|fault)
- [x] 3.1 Implement `persist_telemetry(event)`: one transaction — INSERT into `raw_events` and `INSERT ... ON CONFLICT (vehicle_id) DO UPDATE` on `vehicle_current_state`
- [x] 3.2 Implement `aggregate_fleet_state()`: `SELECT status, COUNT(*) GROUP BY status`, returning zero for absent statuses
- [x] 4.1 Integration test: persisting first vs. later event creates then upserts the single current-state row and appends raw rows
- [x] 4.2 Integration test: raw append + current-state upsert are atomic (failure leaves neither row)
- [x] 4.3 Integration test: 50 distinct vehicles persisted concurrently → one row per vehicle, counts sum to 50, each row matches the vehicle's last event
- [x] 4.4 Integration test: repeated concurrent upserts for one vehicle keep exactly one row contributing once to the aggregate
- [x] 5.1 Write the AI build log report to `docs/ai-build-logs/*.md` and append one line to `docs/ai-build-logs/index.md`
