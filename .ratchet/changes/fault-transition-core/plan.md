# fault-transition-core

## Why

Phase 4 (`fault-transition`) needs a vehicle status-update operation that, on
transition to fault, atomically cancels the vehicle's active mission and creates a
maintenance record — correct under concurrent and at-least-once delivery. This
change is the **root** of that phase: it introduces the three tables the
transition spans (`vehicles` — the lock anchor and authoritative status,
`missions`, `maintenance_records`) and the transactional, FOR UPDATE-locked,
idempotent fault handler, proven at the persistence layer under concurrency. It
stops at a persistence call seam (`transition_to_fault`) so the
concurrency-correctness claim can be proven end-to-end against a real Postgres
before any HTTP surface is built.

The follow-on change `status-update-endpoint` completes the phase: it exposes the
vehicle status-update operation over HTTP, invokes this handler on transition to
fault, and lands the full phase proof `tests/integration/test_fault_transition.py`.
That endpoint and that phase proof are explicitly out of scope here — this change
ships and proves the handler itself, mirroring how `anomaly-detection` stopped at
a persistence read seam and left the endpoint and full phase proof to its
follow-ons.

## What Changes

- **Migration** `app/migrations/0006_create_vehicles.sql`: create `vehicles`
  (`vehicle_id TEXT PRIMARY KEY`, `status TEXT NOT NULL CHECK (status IN
  ('idle','moving','charging','fault')) DEFAULT 'idle'`, `updated_at TIMESTAMPTZ
  NOT NULL DEFAULT now()`). This is the authoritative vehicle row the fault
  transition locks with `SELECT 1 FROM vehicles WHERE vehicle_id = $1 FOR UPDATE`
  (per the ADR and the telemetry-architecture standard). It is intentionally
  separate from `vehicle_current_state` (the last-reading upsert/aggregate table),
  which is not a lock anchor.
- **Migration** `app/migrations/0007_create_missions.sql`: create `missions`
  (`mission_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY`, `vehicle_id TEXT
  NOT NULL`, `status TEXT NOT NULL CHECK (status IN ('active','cancelled',
  'completed')) DEFAULT 'active'`, `created_at TIMESTAMPTZ NOT NULL DEFAULT
  now()`), plus a partial unique index `uq_active_mission_per_vehicle ON missions
  (vehicle_id) WHERE status = 'active'` enforcing at most one active mission per
  vehicle, and an index on `(vehicle_id, status)` for the cancel lookup.
- **Migration** `app/migrations/0008_create_maintenance_records.sql`: create
  `maintenance_records` (`id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY`,
  `vehicle_id TEXT NOT NULL`, `mission_id BIGINT` (the cancelled mission, if any),
  `reason TEXT`, `opened_at TIMESTAMPTZ NOT NULL DEFAULT now()`, `resolved_at
  TIMESTAMPTZ`), plus the idempotency backstop: a partial unique index
  `uq_open_maintenance_per_vehicle ON maintenance_records (vehicle_id) WHERE
  resolved_at IS NULL` — at most one *open* maintenance record per vehicle.
- **Handler** `transition_to_fault(vehicle_id, reason=None) -> bool` in
  `app/persistence.py`: in one `conn.transaction()`, take `SELECT status FROM
  vehicles WHERE vehicle_id = $1 FOR UPDATE`; if the row is already `status =
  'fault'`, return `False` (idempotent no-op); otherwise `UPDATE missions SET
  status = 'cancelled' WHERE vehicle_id = $1 AND status = 'active' RETURNING
  mission_id`, `INSERT INTO maintenance_records (vehicle_id, mission_id, reason)
  VALUES (...) ON CONFLICT DO NOTHING`, `UPDATE vehicles SET status = 'fault',
  updated_at = now() WHERE vehicle_id = $1`, and return `True`.
- **Test-fixture update** (`tests/integration/conftest.py`): extend the
  per-test `_clean_tables` truncation to include `vehicles`, `missions`, and
  `maintenance_records` so each test starts from empty fault-domain tables.
- **Integration test** `tests/integration/test_fault_transition_core.py` proving
  the slice (see Design → Testing).

## Design

- **Vertical-slice scope.** The thinnest slice that proves the phase goal at the
  data layer: seed a vehicle + active mission → `transition_to_fault` → one
  cancelled mission, one maintenance record, `status = fault` — correct under
  concurrent and duplicate calls. The HTTP status-update operation, the
  `vehicle_state_changed` CDC event, and the full phase proof
  `tests/integration/test_fault_transition.py` are out of scope (the
  `status-update-endpoint` follow-on). This mirrors the `anomaly-detection` root,
  which stopped at a persistence seam and left the endpoint and full phase proof
  to its follow-ons.
- **One transaction, pessimistic row lock — mandated by the
  telemetry-architecture standard (and ADR D6).** The transition runs as a single
  `conn.transaction()` that first executes `SELECT ... FROM vehicles WHERE
  vehicle_id = $1 FOR UPDATE`. Locking the vehicle row serializes *all* fault
  handling for that vehicle, so concurrent fault events cannot interleave — the
  mission cancel, the maintenance insert, and the status flip commit together or
  not at all. `SELECT ... FOR UPDATE` is preferred over `SERIALIZABLE`: it is
  scoped to a single aggregate (the vehicle), avoids serialization-failure retry
  loops, and is exact.
- **Idempotency = transition guard + uniqueness constraint.** Two layers, as the
  standard requires, to tolerate at-least-once delivery and concurrency:
  - *Transition guard (primary):* under the row lock, if the vehicle is already
    `status = 'fault'` the handler short-circuits and returns `False` without
    cancelling a mission or inserting a record. Because the lock serializes
    handlers, the second of two concurrent/duplicate transitions always observes
    `fault` and no-ops.
  - *Uniqueness constraint (declarative backstop):* the partial unique index
    `uq_open_maintenance_per_vehicle (vehicle_id) WHERE resolved_at IS NULL`
    guarantees at most one open maintenance record per vehicle even if a write
    somehow reached the insert twice; the insert uses `ON CONFLICT DO NOTHING` so
    a duplicate is a silent no-op rather than an error. The active-mission partial
    unique index similarly guards the mission side.
- **Why a separate `vehicles` table.** The ADR and standard name `vehicles` as the
  FOR UPDATE lock anchor and the authoritative `status` the transition sets.
  `vehicle_current_state` already exists but is the telemetry last-reading/upsert
  table feeding the fleet aggregate — it is not the transition's lock anchor and
  is left untouched here. Keeping them separate avoids overloading the high-churn
  upsert row with a pessimistic lock taken on the fault path.
- **A fault always records maintenance; the mission cancel is conditional.**
  Entering fault always opens one maintenance record (the vehicle needs
  attention). The mission cancel only fires when an active mission exists, so a
  vehicle that faults while idle still transitions cleanly and records exactly one
  maintenance record with a null `mission_id`.
- **Re-fault after repair.** `resolved_at` frees the partial unique index: once a
  maintenance record is resolved and the vehicle is returned to a non-fault
  status, a later fault opens a fresh maintenance record — the idempotency key is
  the *open* episode, not the vehicle's lifetime.
- **Vehicle existence is a precondition.** The handler locks an existing
  `vehicles` row; auto-registering vehicles on ingest is out of scope. The
  integration test seeds the `vehicles` row (and any mission) it transitions.
- **Reuse, don't reinvent.** Extends the existing `app/persistence.py`,
  `app/db.py` connection helper, and the versioned migration runner; introduces no
  new datastore, framework, or background process (tech-stack standard).
- **Testing.** `tests/integration/test_fault_transition_core.py` runs against the
  real Postgres from `docker-compose.test.yml`: (a) a single transition cancels
  the active mission, writes exactly one maintenance record, and sets `status =
  fault`; (b) a duplicate (sequential) transition is a no-op — still one cancelled
  mission, one maintenance record, status fault — and `transition_to_fault`
  returns `True` then `False`; (c) many concurrent transitions for one vehicle
  (threads against the real DB) leave exactly one cancelled mission and exactly one
  maintenance record, status fault; (d) a fault with no active mission still writes
  exactly one maintenance record (null `mission_id`) and sets fault, cancelling no
  mission; (e) cancelling one vehicle's mission leaves another vehicle's active
  mission untouched. The full phase proof `tests/integration/test_fault_transition.py`
  (the HTTP status-update operation) is completed by `status-update-endpoint`.

## Tasks

- [x] 1.1 Add migration `app/migrations/0006_create_vehicles.sql` creating `vehicles` (`vehicle_id` PK, `status` CHECK in idle/moving/charging/fault DEFAULT 'idle', `updated_at` DEFAULT now()) — the FOR UPDATE lock anchor
- [x] 1.2 Add migration `app/migrations/0007_create_missions.sql` creating `missions` (`mission_id` identity PK, `vehicle_id`, `status` CHECK in active/cancelled/completed DEFAULT 'active', `created_at`) with partial unique index `uq_active_mission_per_vehicle (vehicle_id) WHERE status='active'` and an index on `(vehicle_id, status)`
- [x] 1.3 Add migration `app/migrations/0008_create_maintenance_records.sql` creating `maintenance_records` (`id` identity PK, `vehicle_id`, `mission_id`, `reason`, `opened_at`, `resolved_at`) with partial unique index `uq_open_maintenance_per_vehicle (vehicle_id) WHERE resolved_at IS NULL`
- [x] 2.1 Add `transition_to_fault(vehicle_id, reason=None) -> bool` to `app/persistence.py` running in one `conn.transaction()`
- [x] 2.2 In the handler, first take `SELECT status FROM vehicles WHERE vehicle_id = $1 FOR UPDATE`; return `False` when the vehicle is already in fault (transition guard / idempotent no-op)
- [x] 2.3 In the handler, `UPDATE missions SET status='cancelled' WHERE vehicle_id=$1 AND status='active' RETURNING mission_id` to cancel the active mission (if any) and capture its id
- [x] 2.4 In the handler, `INSERT INTO maintenance_records (vehicle_id, mission_id, reason) VALUES (...) ON CONFLICT DO NOTHING` (uniqueness-constraint backstop)
- [x] 2.5 In the handler, `UPDATE vehicles SET status='fault', updated_at=now() WHERE vehicle_id=$1` and return `True`
- [x] 3.1 Extend `tests/integration/conftest.py` `_clean_tables` to also truncate `vehicles`, `missions`, and `maintenance_records`
- [x] 3.2 Add test helpers (vehicle status, active-mission count, maintenance-record count) used by the test
- [x] 4.1 Integration test: a single transition cancels the active mission, writes exactly one maintenance record, and sets `status='fault'`
- [x] 4.2 Integration test: a duplicate (sequential) transition is a no-op (one cancelled mission, one maintenance record, status fault); `transition_to_fault` returns `True` then `False`
- [x] 4.3 Integration test: many concurrent transitions for one vehicle leave exactly one cancelled mission and exactly one maintenance record, status fault
- [x] 4.4 Integration test: a fault with no active mission writes exactly one maintenance record (null `mission_id`), sets fault, and cancels no mission
- [x] 4.5 Integration test: transitioning one vehicle does not cancel another vehicle's active mission
- [x] 5.1 Write the AI build-log report to `docs/ai-build-logs/*.md` and append one line to `docs/ai-build-logs/index.md`
