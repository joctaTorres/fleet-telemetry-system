# AI Build Log — apply zone-counts-increment

- **Session id:** 08ddfba8-20260616
- **Session name:** apply — zone-counts-increment
- **Step:** apply
- **Change:** zone-counts-increment
- **Batch / phase:** fleet-telemetry-service / zone-traversal-counter
- **Date:** 2026-06-16

## Brief

Root change of the `zone-traversal-counter` phase: per-zone entry counts that
stay exact under a burst of concurrent entries to the same zone (shift-change
convergence). Added the `zone_counts` table, an idempotent seed of the ~20
hardcoded zones, an optional `zone_entered` on the telemetry event, a single
server-side atomic increment wired into the existing `persist_telemetry`
transaction, and a `zone_entry_counts()` persistence read seam. The HTTP surface
`GET /zones/counts` is the follow-on `zone-counts-endpoint` change; this change
stops at the persistence read seam. All plan tasks 1.1–4.1 completed; the
integration suite passes against a real Postgres (22/22, exit 0).

## Artifacts written

- `app/migrations/0003_create_zone_counts.sql` — creates `zone_counts`
  (`zone_id TEXT PRIMARY KEY`, `entry_count INTEGER NOT NULL DEFAULT 0`).
  `zone_id` as the primary key is what lets the write path advance the counter
  with a single row-locked `UPDATE ... WHERE zone_id = $1`. (1.1)
- `app/models.py` — added `ZONES`, the hardcoded ~20-zone startup constant
  (`zone-01`…`zone-20`) (1.2); added `zone_entered: str | None = None` to
  `TelemetryEvent`, defaulting to null (the common case carries no zone). (2.1)
- `app/migrate.py` — `seed_zones()` inserts one `zone_counts` row per id in
  `ZONES` at `entry_count = 0`, idempotently
  (`INSERT ... ON CONFLICT (zone_id) DO NOTHING`), and is called at the end of
  `run_migrations()` so the migrate/startup path always leaves all ~20 zones
  seeded. Re-runs never duplicate a row or reset a live count. (1.3)
- `app/persistence.py` — in `persist_telemetry`, when `event.zone_entered` is
  non-null, run exactly `UPDATE zone_counts SET entry_count = entry_count + 1
  WHERE zone_id = %(zone_id)s` inside the *same* `conn.transaction()` as the raw
  insert and the current-state upsert; when null, no counter statement runs.
  (2.2) Added `zone_entry_counts()` — a single `SELECT zone_id, entry_count FROM
  zone_counts` returning live per-zone totals for all seeded zones. (2.3)
- `tests/integration/test_zone_increment.py` — (3.1) one `zone_entered` event
  increments exactly that zone to 1 and changes no other; (3.2) a null
  `zone_entered` event leaves every zone at 0; (3.3) 50 concurrent `zone_entered`
  events for one zone yield `entry_count == 50` exactly (no lost/double counts);
  (3.4) `zone_entry_counts()` returns all ~20 seeded zones with live totals.
- `tests/integration/helpers.py` — added `zone_count(zone_id)` single-zone read
  helper.
- `tests/integration/conftest.py` — `_clean_tables` now also resets
  `zone_counts` (`UPDATE ... SET entry_count = 0`), keeping the seeded rows so
  each test starts from a freshly-seeded baseline.

## Design alignment

- Per `telemetry-architecture`: concurrency correctness lives in the database.
  The counter is advanced by one server-side row-locked read-modify-write
  (`UPDATE ... SET entry_count = entry_count + 1 WHERE zone_id = $1`); the
  rejected application-level SELECT-then-UPDATE — which loses updates under
  concurrency — is *not* used. This mirrors how `vehicle_current_state` keeps
  correctness in the DB via `INSERT ... ON CONFLICT DO UPDATE`.
- Same transaction as the rest of the write: a committed event reflects the raw
  row, the current-state upsert, and the zone increment together, or, on
  failure, none of them. No dual-write window.
- Seed is idempotent and all-zones, so a read of per-zone counts always reports
  all ~20 zones — never-entered zones report 0 — the same zero-fill discipline
  the fleet aggregate uses.
- Reuse, don't reinvent: extends the existing model, persistence module, and
  migration runner; no new framework or datastore.

## Outcome

`docker compose -f docker-compose.test.yml run --rm api pytest
tests/integration/test_zone_increment.py` → exit 0, 4 passed. Full suite
`tests/integration` → 22 passed (18 prior + 4 new). The api image was rebuilt so
the new source/tests are present in the container. Plan tasks 1.1–4.1 checked
off. The phase proof-of-work `tests/integration/test_zone_counts.py` (which also
asserts `GET /zones/counts`) is completed by the follow-on `zone-counts-endpoint`
change.
