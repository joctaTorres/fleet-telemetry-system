# AI Build Log — apply anomaly-detection

- **Session id:** 08ddfba8-20260616
- **Session name:** apply — anomaly-detection
- **Step:** apply
- **Change:** anomaly-detection
- **Batch / phase:** fleet-telemetry-service / anomaly-detection-and-query
- **Date:** 2026-06-16

## Brief

Root change of the `anomaly-detection-and-query` phase: detect anomalies
synchronously *inside* the ingest transaction and write them to an `anomalies`
table, then expose a persistence read seam for the filtered query. Added the
`anomalies` table (indexed on `(vehicle_id, detected_at)`), extended the
telemetry event and `vehicle_current_state` with the fields the default rules
reference (`speed_mps`, `error_codes`, `pos_x`/`pos_y`), wired the stateless +
stateful detection into the existing `persist_telemetry` transaction, and added
the `recent_anomalies(vehicle_id, since, until)` read seam. The `GET /anomalies`
endpoint and the by-absence comms-loss watchdog are explicit follow-on changes;
this change stops at the persistence read seam. Plan tasks 1.1–6.1 completed; the
slice integration suite passes against a real Postgres (19/19), full suite 46/46
(exit 0).

## Artifacts written

- `app/migrations/0004_create_anomalies.sql` — creates `anomalies`
  (`id` identity PK, `vehicle_id`, `anomaly_type`, `detail`, `detected_at`) plus
  the composite index `idx_anomalies_vehicle_detected (vehicle_id, detected_at)`
  that serves the filtered read as an indexed range scan. (1.1)
- `app/migrations/0005_extend_current_state.sql` — adds
  `speed_mps DOUBLE PRECISION NOT NULL DEFAULT 0`, `pos_x`, `pos_y` (nullable) to
  `vehicle_current_state` so the *previous* persisted reading carries what the
  stateful rules compare against. Uses `ADD COLUMN IF NOT EXISTS` to stay
  idempotent. (1.2)
- `app/models.py` — extended `TelemetryEvent` with `speed_mps: float = 0` (ge=0),
  `error_codes: list[str] = []`, and `pos_x`/`pos_y: float | None = None` (2.1);
  added the default-threshold constants `LOW_BATTERY_PCT=15`, `OVERSPEED_MPS=5`,
  `STUCK_SPEED_MPS=0.1`, `STUCK_MIN_SECONDS=10`, `TELEPORT_MPS=15`. (2.2)
- `app/persistence.py`:
  - `persist_telemetry` now reads the prior `vehicle_current_state` row
    (`_SELECT_PRIOR`) *before* the upsert and persists `speed_mps`/`pos_x`/`pos_y`
    in the (extended) upsert, all inside the existing `conn.transaction()`. (3.1)
  - `detect_anomalies(event, prior)` evaluates the stateless rules — `fault_status`,
    `error_codes`, `low_battery` (`battery_pct < 15` while not charging),
    `overspeed` (`speed_mps > 5`) — with strict comparisons so threshold-exact
    values do not fire (3.2); and the stateful rules vs the prior reading —
    `stuck` (both moving, both `speed < 0.1`, `dt ≥ 10s`), `teleport` (euclidean
    distance / `dt > 15 m/s`, guarded for `dt > 0` and positions present),
    `battery_rising` (`battery_pct` up while not charging) — firing none when
    there is no prior row. (3.3)
  - One `anomalies` row is inserted per detected anomaly (`detected_at` = event
    `recorded_at`) in the same transaction; nothing is written when no threshold
    is crossed. (3.4)
  - `recent_anomalies(vehicle_id, since, until)` — a single
    `WHERE vehicle_id = $1 AND detected_at >= $2 AND detected_at <= $3 ORDER BY
    detected_at` indexed range scan with inclusive bounds; the follow-on
    `GET /anomalies` consumes it unchanged. (4.1)
- `tests/integration/test_anomaly_detection.py` — (5.1) each stateless rule fires
  when crossed and not at the boundary (battery 15, speed 5) nor for a clean
  event; (5.2) each stateful rule fires only against an appropriate prior reading
  and never on a first event; (5.3) a multi-violation event writes exactly one
  row per triggered rule; (5.4) `recent_anomalies` returns a vehicle's in-window
  rows (inclusive `since`/`until`), excluding out-of-window rows and other
  vehicles' rows.
- `tests/integration/helpers.py` — added `anomaly_types(vehicle_id)` read helper.
- `tests/integration/conftest.py` — `_clean_tables` now also truncates
  `anomalies` so each test starts clean.

## Design alignment

- Per `telemetry-architecture`: detection is synchronous and in-transaction. The
  anomaly INSERTs share the same `conn.transaction()` as the raw-event insert and
  the current-state upsert, so a committed event reflects the raw row, the upsert,
  any zone increment, and any anomaly rows together — or, on failure, none of
  them. No dual-write window, no async detector to drift.
- The prior reading is read *before* the upsert so stateful rules compare the
  event against the previous state, not against itself.
- The filtered read is an indexed range scan over `(vehicle_id, detected_at)`
  with inclusive bounds, so the follow-on endpoint is an indexed read, not a
  table scan.
- New event/state columns are nullable / defaulted, so earlier-phase events and
  existing rows stay valid and a first-ever reading (no prior row) fires no
  stateful rule.
- Reuse, don't reinvent: extends the existing model, persistence module, and
  migration runner; no new framework, datastore, or background process.

## Outcome

`docker compose -f docker-compose.test.yml run --rm api pytest
tests/integration/test_anomaly_detection.py` → exit 0, 19 passed. Full suite
`tests/integration` → 46 passed (27 prior + 19 new). The api image was rebuilt so
the new source/tests are present in the container. Plan tasks 1.1–6.1 checked
off. The full phase proof `tests/integration/test_anomalies.py` (GET /anomalies +
a comms-loss gap) is completed by the follow-on `anomaly-query-endpoint` and
`comms-loss-watchdog` changes.
