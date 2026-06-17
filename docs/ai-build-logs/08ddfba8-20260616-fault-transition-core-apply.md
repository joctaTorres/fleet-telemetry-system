# AI Build Log — apply fault-transition-core

- **Session id:** 08ddfba8-20260616
- **Session name:** apply — fault-transition-core
- **Step:** apply
- **Change:** fault-transition-core
- **Batch / phase:** fleet-telemetry-service / fault-transition
- **Date:** 2026-06-16

## Brief

Root change of the `fault-transition` phase: a persistence-layer fault handler
that, on transition to fault, atomically cancels the vehicle's active mission and
opens a maintenance record — correct under concurrent and at-least-once delivery.
Introduced the three tables the transition spans (`vehicles` — the FOR UPDATE
lock anchor and authoritative status; `missions`; `maintenance_records`) and the
transactional, row-locked, idempotent `transition_to_fault` handler, proven at
the data layer under concurrency. The HTTP status-update operation, the
`vehicle_state_changed` CDC event, and the full phase proof
`tests/integration/test_fault_transition.py` are out of scope — the
`status-update-endpoint` follow-on completes the phase. Plan tasks 1.1–5.1
completed; the slice suite passes against a real Postgres (5/5), full integration
suite 66/66 (exit 0).

## Artifacts written

- `app/migrations/0006_create_vehicles.sql` — creates `vehicles`
  (`vehicle_id` PK, `status` CHECK in idle/moving/charging/fault DEFAULT 'idle',
  `updated_at` DEFAULT now()), the FOR UPDATE lock anchor, kept separate from the
  high-churn `vehicle_current_state` upsert table. (1.1)
- `app/migrations/0007_create_missions.sql` — creates `missions`
  (`mission_id` identity PK, `vehicle_id`, `status` CHECK in
  active/cancelled/completed DEFAULT 'active', `created_at`) plus the partial
  unique index `uq_active_mission_per_vehicle (vehicle_id) WHERE status='active'`
  (at most one active mission per vehicle) and `idx_missions_vehicle_status
  (vehicle_id, status)` backing the cancel lookup. (1.2)
- `app/migrations/0008_create_maintenance_records.sql` — creates
  `maintenance_records` (`id` identity PK, `vehicle_id`, `mission_id`, `reason`,
  `opened_at`, `resolved_at`) plus the idempotency backstop partial unique index
  `uq_open_maintenance_per_vehicle (vehicle_id) WHERE resolved_at IS NULL` (at
  most one open record per vehicle). (1.3)
- `app/persistence.py` — `transition_to_fault(vehicle_id, reason=None) -> bool`
  in one `conn.transaction()`: (2.1) take `SELECT status FROM vehicles WHERE
  vehicle_id = $1 FOR UPDATE`; raise `LookupError` if absent; (2.2) return
  `False` if already `fault` (transition guard / idempotent no-op); (2.3)
  `UPDATE missions SET status='cancelled' WHERE vehicle_id=$1 AND status='active'
  RETURNING mission_id` (capturing the cancelled mission id, or `None`); (2.4)
  `INSERT INTO maintenance_records (...) ON CONFLICT DO NOTHING` (uniqueness
  backstop); (2.5) `UPDATE vehicles SET status='fault', updated_at=now()` and
  return `True`.
- `tests/integration/conftest.py` — `_clean_tables` now also truncates
  `vehicles`, `missions`, and `maintenance_records`. (3.1)
- `tests/integration/helpers.py` — added `vehicle_status`, `active_mission_count`,
  `mission_status_counts`, `maintenance_record_count`, `maintenance_records`, and
  `seed_vehicle` / `seed_active_mission` seed helpers. (3.2)
- `tests/integration/test_fault_transition_core.py` — (4.1) single transition
  cancels the mission, opens exactly one maintenance record (mission_id set,
  resolved_at null), sets fault; (4.2) duplicate sequential transition is a no-op,
  `True` then `False`; (4.3) 20 concurrent transitions for one vehicle settle to
  exactly one cancelled mission and one maintenance record, status fault, exactly
  one `True`; (4.4) fault with no active mission opens one record with null
  `mission_id`, sets fault, cancels nothing; (4.5) transitioning one vehicle
  leaves another's active mission untouched.

## Design alignment

- Per `telemetry-architecture` (and ADR D6): one transaction, pessimistic row
  lock. The handler first takes `SELECT ... FROM vehicles ... FOR UPDATE`, which
  serializes all fault handling for that vehicle — the mission cancel, the
  maintenance insert, and the status flip commit together or not at all. Chosen
  over `SERIALIZABLE`: scoped to one aggregate, no serialization-failure retry
  loops, exact.
- Idempotency is two layers, as the standard requires. Primary: the transition
  guard short-circuits when the row is already `fault`, so the second of two
  concurrent/duplicate transitions always observes `fault` and no-ops (the lock
  guarantees it sees the committed flip). Backstop: the partial unique indexes
  (`uq_open_maintenance_per_vehicle`, `uq_active_mission_per_vehicle`) plus
  `ON CONFLICT DO NOTHING` make a doubled write a silent no-op rather than a
  duplicate or error.
- A fault always opens exactly one maintenance record; the mission cancel is
  conditional on an active mission existing (null `mission_id` when idle).
- `resolved_at` frees the partial index, so a re-fault after repair opens a fresh
  record — the idempotency key is the open episode, not the vehicle's lifetime.
- Vehicle existence is a precondition: the handler locks an existing `vehicles`
  row; the test seeds it. Auto-registration on ingest is out of scope.
- Reuse, don't reinvent: extends `app/persistence.py`, the `app/db.py`
  connection helper, and the versioned migration runner; no new datastore,
  framework, or background process.

## Outcome

`docker compose -f docker-compose.test.yml run --rm api pytest
tests/integration/test_fault_transition_core.py` → exit 0, 5 passed. Full suite
`tests/integration` → 66 passed (61 prior + 5 new). The api image was rebuilt so
the new migrations/source/tests are present in the container. Plan tasks 1.1–5.1
checked off. The full phase proof `tests/integration/test_fault_transition.py`
(the HTTP status-update operation) is completed by the follow-on
`status-update-endpoint` change.
