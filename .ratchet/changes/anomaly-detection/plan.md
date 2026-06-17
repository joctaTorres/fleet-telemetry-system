# anomaly-detection

## Why

Phase 3 (`anomaly-detection-and-query`) needs anomalies detected synchronously
inside the ingest transaction and written to an anomalies table, then made
queryable. This change is the **root** of that phase: it adds the `anomalies`
table, extends the telemetry event and the per-vehicle current-state row with the
fields the default rules reference, and wires the synchronous stateless + stateful
detection into the existing `persist_telemetry` write path so every detected
anomaly is committed in the same transaction as the reading that caused it. It
stops at a persistence-level read seam (`recent_anomalies`) so detection can be
proven end-to-end without first building the HTTP surface.

The follow-on changes complete the phase: `anomaly-query-endpoint` exposes
`GET /anomalies` over the read seam, and `comms-loss-watchdog` adds the
by-absence (background) detector. Those — and the full phase proof
`tests/integration/test_anomalies.py` (which also asserts the endpoint and a
comms-loss gap) — are explicitly out of scope here.

## What Changes

- **Migration** `app/migrations/0004_create_anomalies.sql`: create `anomalies`
  (`id` identity PK, `vehicle_id TEXT NOT NULL`, `anomaly_type TEXT NOT NULL`,
  `detail TEXT` for optional context, `detected_at TIMESTAMPTZ NOT NULL`), plus a
  composite index `idx_anomalies_vehicle_detected ON anomalies (vehicle_id,
  detected_at)` to serve the filtered read.
- **Migration** `app/migrations/0005_extend_current_state.sql`: add
  `speed_mps DOUBLE PRECISION NOT NULL DEFAULT 0`, `pos_x DOUBLE PRECISION`,
  `pos_y DOUBLE PRECISION` to `vehicle_current_state`, so the *previous* reading
  carries everything the stateful rules compare against.
- **Model** (`app/models.py`): extend `TelemetryEvent` with
  `speed_mps: float = 0` (ge=0), `error_codes: list[str] = []`, and
  `pos_x: float | None = None`, `pos_y: float | None = None`. Add a default
  thresholds constant block (LOW_BATTERY_PCT=15, OVERSPEED_MPS=5,
  STUCK_SPEED_MPS=0.1, STUCK_MIN_SECONDS=10, TELEPORT_MPS=15).
- **Detection** in `persist_telemetry`: inside the existing transaction, read the
  vehicle's prior `vehicle_current_state` row *before* the upsert, evaluate the
  default rules, upsert the new current state (now including speed/position), and
  `INSERT` one row per detected anomaly into `anomalies` (`detected_at` = the
  event's `recorded_at`). When nothing crosses a threshold, no anomaly row is
  written.
- **Read seam** `recent_anomalies(vehicle_id, since, until)` in
  `app/persistence.py`: a single indexed `SELECT ... WHERE vehicle_id = $1 AND
  detected_at >= $2 AND detected_at <= $3 ORDER BY detected_at` returning the
  matching rows. The next change's `GET /anomalies` calls this unchanged.
- **Integration test** `tests/integration/test_anomaly_detection.py` proving the
  slice (see Design → Testing).

## Design

- **Vertical-slice scope.** The thinnest slice that proves the phase goal at the
  data layer: telemetry event → `persist_telemetry` → synchronous detection →
  `anomalies` insert → indexed read back. The HTTP endpoint, the by-absence
  comms-loss watchdog, and the CDC `anomaly_detected` event are explicitly out of
  scope (later changes/phases). This mirrors the `zone-counts-increment` slice,
  which stopped at a persistence read seam and left the endpoint and full phase
  proof to its follow-on.
- **Detection is synchronous and in-transaction — mandated by the
  telemetry-architecture standard.** Anomalies are evaluated and inserted inside
  the same `conn.transaction()` as the raw-event insert and the current-state
  upsert. A committed event therefore reflects the raw row, the upsert, and any
  anomaly rows together, or — on failure — none of them. The anomaly INSERT *is*
  the event; there is no dual-write window and no async detector to drift.
- **Default rules (agreed in the phase success criteria).**
  - *Stateless (on the event):* `status == "fault"` → `fault_status`; non-empty
    `error_codes` → `error_codes`; `battery_pct < 15` while `status != charging` →
    `low_battery`; `speed_mps > 5` → `overspeed`. Independent — one event can fire
    several. Strict comparisons, so threshold-exact values (battery 15, speed 5)
    do not fire.
  - *Stateful (vs the previous persisted reading):* **stuck** = prior and current
    both `status == moving` with `speed_mps < 0.1` and `current.recorded_at -
    prior.recorded_at >= 10s`; **teleport** = euclidean distance between prior and
    current position over the inter-event interval implies `> 15 m/s` (guarded for
    `dt > 0`); **battery_rising** = `current.battery_pct > prior.battery_pct` while
    `status != charging`.
  - *By-absence:* comms-loss (no event for >5s) is **not** detected here — it is a
    background watchdog and the responsibility of the `comms-loss-watchdog` change.
- **Why the event/state schema grows.** The stateful rules need a previous reading
  that carries speed and position, and teleport needs position at all — so
  `speed_mps`/`pos_x`/`pos_y` are added to the event and persisted into
  `vehicle_current_state`. Positions are x/y metres so implied speed is a plain
  euclidean distance over `dt`. New columns are nullable / defaulted so existing
  rows and phase-1/2 events remain valid; a first-ever reading has no prior row,
  so stateful rules simply do not fire.
- **The filtered read is an indexed range scan.** `recent_anomalies` filters by
  `vehicle_id` and an inclusive `detected_at` range, served by the composite
  `(vehicle_id, detected_at)` index, so the follow-on endpoint is an indexed read
  rather than a table scan. Bounds are inclusive on both ends.
- **Reuse, don't reinvent.** Extends the existing model, persistence module, and
  migration runner; introduces no new framework, datastore, or background process
  in this change (tech-stack standard).
- **Testing.** `tests/integration/test_anomaly_detection.py` runs against the real
  Postgres from `docker-compose.test.yml`: (a) each stateless rule fires exactly
  when its threshold is crossed and not at the boundary (battery 15, speed 5) or
  for a clean event; (b) each stateful rule fires only against an appropriate
  prior reading and never on a vehicle's first event; (c) a multi-violation event
  writes one row per rule; (d) `recent_anomalies` returns a vehicle's anomalies
  inside a `[since, until]` window (inclusive bounds) and excludes those outside
  it and those for other vehicles. The full phase proof
  `tests/integration/test_anomalies.py` (GET /anomalies + comms-loss gap) is
  completed by the follow-on changes.

## Tasks

- [x] 1.1 Add migration `app/migrations/0004_create_anomalies.sql` creating `anomalies` (`id` identity PK, `vehicle_id`, `anomaly_type`, `detail`, `detected_at`) with index `idx_anomalies_vehicle_detected ON anomalies (vehicle_id, detected_at)`
- [x] 1.2 Add migration `app/migrations/0005_extend_current_state.sql` adding `speed_mps` (NOT NULL DEFAULT 0), `pos_x`, `pos_y` to `vehicle_current_state`
- [x] 2.1 Extend `TelemetryEvent` in `app/models.py` with `speed_mps: float = 0` (ge=0), `error_codes: list[str] = []`, `pos_x: float | None = None`, `pos_y: float | None = None`
- [x] 2.2 Add the default-threshold constants (low-battery 15, overspeed 5, stuck speed 0.1 / ≥10s, teleport 15 m/s) to `app/models.py`
- [x] 3.1 In `persist_telemetry`, read the prior `vehicle_current_state` row before the upsert and persist `speed_mps`/`pos_x`/`pos_y` in the upsert, all inside the existing transaction
- [x] 3.2 Evaluate the stateless rules (`fault_status`, `error_codes`, `low_battery`, `overspeed`) on the event, with strict threshold comparisons
- [x] 3.3 Evaluate the stateful rules (`stuck`, `teleport`, `battery_rising`) against the prior reading; fire none when there is no prior row
- [x] 3.4 `INSERT` one `anomalies` row per detected anomaly (`detected_at` = event `recorded_at`) inside the same transaction; write nothing when no threshold is crossed
- [x] 4.1 Add `recent_anomalies(vehicle_id, since, until)` to `app/persistence.py` — a single indexed `SELECT ... WHERE vehicle_id = $1 AND detected_at BETWEEN $2 AND $3 ORDER BY detected_at`
- [x] 5.1 Integration test: each stateless rule fires when its threshold is crossed and not at the boundary nor for a clean event
- [x] 5.2 Integration test: each stateful rule fires only against an appropriate prior reading and never on a vehicle's first event
- [x] 5.3 Integration test: a multi-violation event writes one anomaly row per triggered rule
- [x] 5.4 Integration test: `recent_anomalies` returns a vehicle's in-window anomalies (inclusive bounds) and excludes out-of-window rows and other vehicles' rows
- [x] 6.1 Write the AI build-log report to `docs/ai-build-logs/*.md` and append one line to `docs/ai-build-logs/index.md`
