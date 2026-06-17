# status-update-endpoint

## Why

Phase 4 (`fault-transition`) needs a vehicle status-update operation that, on
transition to fault, atomically cancels the vehicle's active mission and creates a
maintenance record — correct under concurrent and at-least-once delivery. The
**root** change `fault-transition-core` already shipped and proved that behavior
at the persistence layer: it added the `vehicles` / `missions` /
`maintenance_records` tables and the FOR UPDATE-locked, idempotent
`transition_to_fault(vehicle_id, reason)` handler, proven under concurrency by
`tests/integration/test_fault_transition_core.py`.

This follow-on change **completes the phase** by exposing the status-update
operation over HTTP and invoking that handler on transition to fault. It is the
thin end-to-end slice: an HTTP write route that delegates the fault case to the
existing seam — reusing, not reimplementing, the transaction and its idempotency
— and lands the full phase proof `tests/integration/test_fault_transition.py`.
This mirrors how `ingestion-post-endpoint` was a thin HTTP adapter over the
already-proven `persist_telemetry` write path.

## What Changes

- **Route** `POST /vehicles/{vehicle_id}/status` on the existing stateless
  ingestion API (`app/ingestion_api.py`) — the write API per the
  telemetry-architecture standard ("two separate APIs"). Body is a new Pydantic
  model `VehicleStatusUpdate` (`status: VehicleStatus`, optional `reason: str |
  None`). FastAPI/Pydantic rejects a schema-invalid body (unknown status, unknown
  field) with 422 before the handler runs, so nothing is written.
- **Fault delegation.** When the requested `status` is `fault`, the route calls
  the existing `transition_to_fault(vehicle_id, reason)` seam — no new transaction
  logic. The handler's boolean return is surfaced in the response so the client
  can see whether the transition applied (`True`) or was an idempotent no-op
  (`False`). The route adds no locking, guard, or insert of its own; all
  concurrency correctness stays in the proven handler.
- **Unknown vehicle → 404.** `transition_to_fault` raises `LookupError` when the
  `vehicles` row does not exist (existence is a precondition; auto-registration is
  out of scope). The route maps that to `404 Not Found` so an update for an
  unregistered vehicle is a clean client error, not a 500.
- **Non-fault statuses (thin, minimal).** Setting a vehicle to a non-fault status
  (`idle` / `moving` / `charging`) does a single guarded
  `UPDATE vehicles SET status = $2, updated_at = now() WHERE vehicle_id = $1` via a
  small `set_vehicle_status(vehicle_id, status)` persistence helper, returning 404
  if no row was updated. This keeps the route an honest "status-update operation"
  rather than a fault-only endpoint, while the *proven* concurrency path remains
  the fault transition. (Re-fault-after-repair and resolving maintenance stay in
  the handler/DB as already designed by the core change.)
- **Phase proof** `tests/integration/test_fault_transition.py`: the phase-4
  proof-of-work, driving the HTTP endpoint with FastAPI's `TestClient` against the
  real Postgres from `docker-compose.test.yml` (see Design → Testing). The
  fault-domain truncation and the seed/read helpers it needs
  (`seed_vehicle`, `seed_active_mission`, `vehicle_status`, `active_mission_count`,
  `maintenance_record_count`, `mission_status_counts`) already exist from
  `fault-transition-core`.

## Design

- **Vertical-slice scope.** The thinnest slice that proves the phase goal end to
  end over REST: POST a `fault` status update → the existing handler cancels the
  active mission, opens one maintenance record, and flips status to `fault` — and
  the HTTP path inherits the handler's idempotency under concurrent and duplicate
  delivery. The CDC `vehicle_state_changed` event and the WebSocket/dashboard are
  out of scope (phases 5–6). No new datastore, framework, or background process
  (tech-stack standard).
- **Reuse the proven seam; do not reimplement.** The fault path is a one-line
  delegation to `transition_to_fault`. Re-deriving the lock/guard/insert in the
  route would duplicate the exact logic `fault-transition-core` already proved and
  risk divergence. The route's only jobs are: validate the body, translate the
  path/body into a seam call, and map the seam's outcome (`bool` /
  `LookupError`) onto HTTP status + body.
- **Idempotency is inherited, not re-added.** Because every fault update funnels
  through the row-locked, transition-guarded handler with its uniqueness backstop,
  concurrent and at-least-once HTTP deliveries for the same vehicle converge on
  exactly one cancelled mission and one maintenance record with no extra work in
  the web layer. The response merely *reports* which call applied.
- **Why the ingestion (write) API.** A status update is a command that mutates
  authoritative state, so it belongs on the stateless write API alongside
  `POST /telemetry`, not the read-only frontend API. The frontend API stays
  read-only.
- **Status code choices.** 200 OK for an accepted update (including an idempotent
  no-op — the desired end state already holds, so it is success, not an error);
  404 for an unknown vehicle; 422 for a schema-invalid body (Pydantic). The
  response body carries an `applied` boolean (and the resulting `status`) so a
  caller can distinguish "I applied the change" from "already in that state".
- **Testing.** `tests/integration/test_fault_transition.py` runs the endpoint
  in-process via `TestClient` against the real Postgres:
  (a) a single `POST .../status` of `fault` for a vehicle with an active mission
  returns 200, cancels the mission, writes exactly one maintenance record, and
  sets `status = fault`;
  (b) a duplicate (sequential) fault POST is a no-op — still one cancelled mission,
  one maintenance record, status fault — and the response reports
  `applied: true` then `applied: false`;
  (c) **the phase proof:** many *concurrent* fault POSTs for one vehicle (a thread
  pool of `TestClient` requests against the real DB) leave exactly one cancelled
  mission and exactly one maintenance record, status fault;
  (d) a fault POST for an unknown vehicle returns 404 and writes no maintenance
  record;
  (e) a schema-invalid status returns 422 and writes nothing (status and mission
  unchanged).
  The full phase-4 proof-of-work command is
  `docker compose -f docker-compose.test.yml run --rm api pytest tests/integration/test_fault_transition.py`.

## Tasks

- [x] 1.1 Add a `VehicleStatusUpdate` Pydantic model (`status: VehicleStatus`, optional `reason: str | None`) to `app/models.py`, reusing the existing `VehicleStatus` literal and forbidding unknown fields like the other models
- [x] 1.2 Add a small `set_vehicle_status(vehicle_id, status) -> bool` helper to `app/persistence.py` for non-fault updates: a single guarded `UPDATE vehicles SET status = $2, updated_at = now() WHERE vehicle_id = $1`, returning whether a row was updated
- [x] 2.1 Add `POST /vehicles/{vehicle_id}/status` to `app/ingestion_api.py` accepting `VehicleStatusUpdate`
- [x] 2.2 In the route, when `status == "fault"`, delegate to the existing `transition_to_fault(vehicle_id, reason)` seam — no new transaction logic — and return 200 with an `applied` boolean reflecting its return value
- [x] 2.3 In the route, when `status` is non-fault, call `set_vehicle_status` and return 200 (`applied` reflecting whether the value changed)
- [x] 2.4 Map `LookupError` / an unknown vehicle row to `404 Not Found` (no row written); rely on Pydantic for 422 on a schema-invalid body
- [x] 3.1 Add an HTTP test helper/fixture (`TestClient(app)` for the ingestion API) if not already shared, reusing the existing fault-domain seed/read helpers
- [x] 4.1 Phase-proof test (single): a `fault` POST cancels the active mission, writes exactly one maintenance record, sets `status = fault`, and returns 200 with `applied: true`
- [x] 4.2 Phase-proof test (duplicate/sequential): a second identical `fault` POST is a no-op — one cancelled mission, one maintenance record, status fault — and reports `applied: true` then `applied: false`
- [x] 4.3 Phase-proof test (concurrent): many concurrent `fault` POSTs for one vehicle leave exactly one cancelled mission and exactly one maintenance record, status fault
- [x] 4.4 Phase-proof test (unknown vehicle): a `fault` POST for a vehicle with no row returns 404 and writes no maintenance record
- [x] 4.5 Phase-proof test (schema-invalid): a bad `status` value returns 422 and leaves the vehicle's status and active mission unchanged
- [x] 4.6 Land the test at `tests/integration/test_fault_transition.py` and confirm the phase proof passes: `docker compose -f docker-compose.test.yml run --rm api pytest tests/integration/test_fault_transition.py` exits 0
- [x] 5.1 Write the AI build-log report to `docs/ai-build-logs/*.md` and append one line to `docs/ai-build-logs/index.md`
