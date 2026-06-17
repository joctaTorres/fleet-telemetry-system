# zone-counts-increment

## Why

Phase 2 (`zone-traversal-counter`) needs per-zone entry counts that stay exact under a
burst of concurrent entries to the same zone (shift-change convergence). This change is
the **root** of that phase: it adds the `zone_counts` table, seeds the ~20 known zones,
extends the telemetry event with an optional `zone_entered`, and wires a single
server-side atomic increment into the existing `persist_telemetry` write path. The
read-only HTTP surface `GET /zones/counts` is the follow-on change
(`zone-counts-endpoint`); this change stops at a persistence-level read seam so the
increment can be proven end-to-end without first building the endpoint.

## What Changes

- **Migration** `app/migrations/0003_create_zone_counts.sql`: create `zone_counts`
  (`zone_id TEXT PRIMARY KEY`, `entry_count INTEGER NOT NULL DEFAULT 0`).
- **Hardcoded zone constant**: a startup constant listing the ~20 known zone ids
  (alongside `STATUSES` in `app/models.py`).
- **Seed**: insert one `zone_counts` row per known zone, `entry_count = 0`, idempotently
  (`ON CONFLICT (zone_id) DO NOTHING`) so re-runs never duplicate a row or reset a count.
  Run as part of the migrate/startup path.
- **Model**: add `zone_entered: str | None = None` to `TelemetryEvent` (defaults to null;
  most events carry no zone entry).
- **Atomic increment** in `persist_telemetry`: when `event.zone_entered` is non-null,
  issue exactly `UPDATE zone_counts SET entry_count = entry_count + 1 WHERE zone_id = $1`
  inside the *same* transaction as the raw-event insert and the current-state upsert. When
  `zone_entered` is null, no counter statement runs.
- **Read seam** `zone_entry_counts()` in `app/persistence.py`: a single `SELECT zone_id,
  entry_count FROM zone_counts` returning the live per-zone totals (all seeded zones). The
  next change's `GET /zones/counts` will call this unchanged.
- **Integration test** `tests/integration/test_zone_increment.py` proving the slice
  (see Design → Testing).

## Design

- **Vertical-slice scope.** The thinnest slice that proves the phase goal at the data
  layer: telemetry event with `zone_entered` → `persist_telemetry` → atomic counter
  increment → read back. The HTTP endpoint, WebSocket push, and the `zone_count_changed`
  event are explicitly out of scope (later changes/phases).
- **Concurrency correctness lives in the database — mandated by the telemetry-architecture
  standard.** The counter is advanced by one server-side `UPDATE ... SET entry_count =
  entry_count + 1 WHERE zone_id = $1`, a row-locked read-modify-write inside Postgres. The
  forbidden application-level `SELECT` count → `+1` → `UPDATE` is NOT used — it loses
  updates under concurrency. This mirrors how `vehicle_current_state` keeps correctness in
  the DB via `INSERT ... ON CONFLICT DO UPDATE`.
- **Same transaction as the rest of the write.** The increment joins the existing
  `conn.transaction()` block in `persist_telemetry`, so a committed event reflects the raw
  row, the current-state upsert, and the zone increment together — or, on failure, none of
  them. No dual-write window.
- **Null zone_entered is the common case.** Most telemetry carries no zone entry; the
  increment statement only runs when `zone_entered` is non-null, leaving all counters
  untouched otherwise.
- **Seed is idempotent and all-zones.** Reading per-zone counts always returns all ~20
  zones — never-entered zones report `0` — because the seed guarantees a row per zone, the
  same way the fleet aggregate zero-fills every status key.
- **Reuse, don't reinvent.** Extends the existing model, persistence module, and migration
  runner; introduces no new framework or datastore (tech-stack standard).
- **Testing.** `tests/integration/test_zone_increment.py` runs against the real Postgres
  from `docker-compose.test.yml`: (a) persisting one event with `zone_entered` increments
  exactly that zone and no other; (b) a null `zone_entered` event leaves all counts at 0;
  (c) firing N concurrent `zone_entered` events for one zone yields `entry_count == N`
  exactly (zero lost increments); (d) `zone_entry_counts()` returns all ~20 seeded zones
  with live totals. The phase proof-of-work `tests/integration/test_zone_counts.py` (which
  also asserts `GET /zones/counts`) is completed by the follow-on `zone-counts-endpoint`
  change.

## Tasks

- [x] 1.1 Add migration `app/migrations/0003_create_zone_counts.sql` creating `zone_counts` (`zone_id TEXT PRIMARY KEY`, `entry_count INTEGER NOT NULL DEFAULT 0`)
- [x] 1.2 Add the hardcoded ~20-zone startup constant (e.g. `ZONES`) to `app/models.py`
- [x] 1.3 Seed one `zone_counts` row per known zone with `entry_count = 0`, idempotently (`ON CONFLICT (zone_id) DO NOTHING`), wired into the migrate/startup path
- [x] 2.1 Add `zone_entered: str | None = None` to `TelemetryEvent` in `app/models.py`
- [x] 2.2 In `persist_telemetry`, when `zone_entered` is non-null, run `UPDATE zone_counts SET entry_count = entry_count + 1 WHERE zone_id = %(zone_id)s` inside the existing transaction; run no counter statement when it is null
- [x] 2.3 Add `zone_entry_counts()` to `app/persistence.py` — a single `SELECT zone_id, entry_count FROM zone_counts` returning live per-zone totals for all seeded zones
- [x] 3.1 Integration test: one event with `zone_entered` increments exactly that zone to 1 and changes no other zone
- [x] 3.2 Integration test: an event with `zone_entered=null` leaves every zone's `entry_count` at 0
- [x] 3.3 Integration test: N concurrent `zone_entered` events for one zone yield `entry_count == N` exactly (no lost or double-counted increments)
- [x] 3.4 Integration test: `zone_entry_counts()` returns all ~20 seeded zones with their live totals
- [x] 4.1 Write the AI build-log report to `docs/ai-build-logs/*.md` and append one line to `docs/ai-build-logs/index.md`
