# AI Build Log — apply status-update-endpoint

- **Session id:** 08ddfba8-20260616
- **Session name:** apply — status-update-endpoint
- **Step:** apply
- **Change:** status-update-endpoint
- **Batch / phase:** fleet-telemetry-service / fault-transition
- **Date:** 2026-06-16

## Brief

The HTTP half of the `fault-transition` phase. The root change
`fault-transition-core` already shipped the `vehicles` / `missions` /
`maintenance_records` tables and the row-locked, idempotent
`transition_to_fault(vehicle_id, reason)` handler (proven under concurrency by
`test_fault_transition_core.py`), but that behavior had no network surface. This
change adds `POST /vehicles/{vehicle_id}/status` to the stateless ingestion
(write) API as a thin adapter: a `fault` update delegates to the proven seam — no
new transaction logic — and the HTTP path inherits its idempotency. It lands the
phase proof-of-work `tests/integration/test_fault_transition.py`. All plan tasks
1.1–5.1 completed; the integration suite passes against a real Postgres
(71/71, exit 0).

## Artifacts written

- `app/models.py` — added `VehicleStatusUpdate` (`status: VehicleStatus`,
  optional `reason: str | None`, `extra="forbid"`), so a schema-invalid body
  (unknown status / unknown field) is rejected with 422 before the handler runs.
  (1.1)
- `app/persistence.py` — added `set_vehicle_status(vehicle_id, status) -> bool`
  for the non-fault path: a single guarded
  `UPDATE vehicles SET status = $2, updated_at = now() WHERE vehicle_id = $1`,
  returning `rowcount > 0` so the route maps a missing vehicle to 404 rather than
  a silent success. (1.2)
- `app/ingestion_api.py` — added `POST /vehicles/{vehicle_id}/status` accepting
  `VehicleStatusUpdate`. When `status == "fault"` it calls
  `transition_to_fault(vehicle_id, reason)` (2.2); otherwise it calls
  `set_vehicle_status` and raises `LookupError` if no row matched (2.3). A
  `LookupError` from either path maps to `404 Not Found` (2.4); Pydantic handles
  422. Returns 200 with `{vehicle_id, status, applied}` where `applied` reports
  whether the call changed state (`True`) or was an idempotent no-op (`False`).
- `tests/integration/test_fault_transition.py` — phase proof-of-work, the
  ingestion app driven in-process with FastAPI `TestClient` against the real
  Postgres, reusing the fault-domain seed/read helpers (3.1):
  - (4.1) single `fault` POST → 200, mission cancelled, exactly one maintenance
    record, status fault, `applied: true`.
  - (4.2) duplicate sequential `fault` POST → no-op: one cancelled mission, one
    record, status fault; `applied: true` then `false`.
  - (4.3) **phase proof** — 20 concurrent `fault` POSTs for one vehicle (thread
    pool) leave exactly one cancelled mission and one maintenance record, status
    fault; exactly one request reports `applied: true`.
  - (4.4) `fault` POST for an unknown vehicle → 404, no maintenance record.
  - (4.5) schema-invalid status → 422, vehicle status and active mission
    unchanged.

## Design alignment

- **Vertical-slice scope:** the thinnest HTTP write path that proves the phase
  goal end to end — POST a `fault` update → existing handler cancels the active
  mission, opens one maintenance record, flips status to fault. The CDC
  `vehicle_state_changed` event and the WebSocket/dashboard are out of scope
  (phases 5–6).
- **Reuse the proven seam; do not reimplement.** The fault path is a one-line
  delegation to `transition_to_fault`; the route adds no lock, guard, or insert
  of its own. All concurrency correctness — the `FOR UPDATE` lock, the transition
  guard, and the `uq_open_maintenance_per_vehicle` uniqueness backstop — stays in
  the handler.
- **Idempotency inherited, not re-added.** Because every fault update funnels
  through the row-locked, transition-guarded handler, concurrent and
  at-least-once HTTP deliveries converge on exactly one cancelled mission and one
  maintenance record; the response merely reports which call applied.
- **Write API, per `telemetry-architecture`.** A status update mutates
  authoritative state, so it lives on the stateless ingestion API alongside
  `POST /telemetry`; the frontend API stays read-only.
- **Status codes:** 200 for an accepted update (including an idempotent no-op);
  404 for an unknown vehicle (no row written); 422 for a schema-invalid body.

## Outcome

`docker compose -f docker-compose.test.yml run --rm api pytest
tests/integration/test_fault_transition.py` → exit 0, 5 passed (api image
rebuilt so the new source/tests are present in the container). Full suite
`tests/integration` → 71 passed (66 prior + 5 new). Plan tasks 1.1–5.1 checked
off. This completes the `fault-transition` phase's end-to-end slice.
