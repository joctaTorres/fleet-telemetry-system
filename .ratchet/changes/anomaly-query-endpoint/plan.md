# anomaly-query-endpoint

## Why

The `anomaly-detection` change built the root of phase 3
(`anomaly-detection-and-query`): the `anomalies` table indexed on
`(vehicle_id, detected_at)`, the synchronous stateless + stateful detection wired
into `persist_telemetry`, and the `recent_anomalies(vehicle_id, since, until)`
read seam in `app/persistence.py`. But those detected anomalies have no network
surface yet: the dashboard cannot read them. This change adds the read half of the
phase, `GET /anomalies`, turning the existing read seam into a reachable REST
endpoint and completing the phase's end-to-end slice (POST telemetry that crosses
a threshold writes an anomaly → GET reads it back, filtered by vehicle and time
range). It also begins landing the phase proof-of-work
`tests/integration/test_anomalies.py`.

The remaining sibling change `comms-loss-watchdog` adds the by-absence (background)
detector and the comms-loss-gap scenario of the phase proof. That scenario is
explicitly out of scope here.

## What Changes

- Add `GET /anomalies` to the existing **frontend API** (`app/frontend_api.py`),
  the dedicated FastAPI app kept separate from the stateless ingestion API per the
  telemetry-architecture standard.
- The route takes a required `vehicle_id` and an inclusive `[since, until]` time
  range (`since` / `until` as ISO-8601 timestamp query params), calls the existing
  `recent_anomalies(vehicle_id, since, until)`, and returns **200 OK** with a JSON
  list of anomaly objects (`vehicle_id`, `anomaly_type`, `detail`, `detected_at`),
  ordered by `detected_at`. A vehicle with no matching anomalies returns an empty
  list.
- Begin the phase proof-of-work integration test
  `tests/integration/test_anomalies.py`: drive the ingestion API (POST telemetry
  that crosses each default threshold) and the frontend API (`GET /anomalies`)
  against the same database, asserting each default anomaly class fires exactly
  when its threshold is crossed and not otherwise, and that `GET /anomalies`
  returns the matching rows for a vehicle within the requested `[since, until]`
  window and excludes those outside it and those for other vehicles. The
  comms-loss-gap scenario is appended by the `comms-loss-watchdog` change.

## Design

- **Vertical-slice scope:** the thinnest HTTP read path that proves the phase goal
  — request → `recent_anomalies()` → JSON. No WebSocket, no CDC/Redis, no
  `anomaly_detected` push; those belong to later phases. The by-absence comms-loss
  watchdog and its phase-proof scenario belong to the `comms-loss-watchdog` change.
  This change adds only the HTTP adapter and its share of the proof-of-work test.
- **Reuse the read seam, don't reinvent.** `recent_anomalies()` already returns a
  vehicle's anomalies within an inclusive `[since, until]` range via a single
  indexed range scan over the `(vehicle_id, detected_at)` composite index, ordered
  by `detected_at`. The endpoint is a thin adapter over it — mirroring how
  `GET /zones/counts` wraps `zone_entry_counts()` and `GET /fleet/state` wraps
  `aggregate_fleet_state()`. The query is consumed unchanged, so the endpoint is an
  indexed read rather than a table scan.
- **Separate APIs, per the telemetry-architecture standard.** The read endpoint
  goes on the frontend API, *not* the ingestion API: the ingestion API MUST stay
  stateless and write-only (`validate → detect → write → return`). The frontend API
  already owns `GET /fleet/state` and `GET /zones/counts`; this adds a third read
  route to the same app.
- **No authoritative in-process state.** The frontend API derives the result fresh
  from the `anomalies` table on each request; it holds no in-process buffer that
  could diverge from committed state. Bounds are inclusive on both ends, matching
  the read seam.
- **Scoped deviation — reads from the primary, not a replica.** Consistent with the
  other frontend reads: this phase is scoped to a single Postgres (no replica/CDC/
  Redis yet), so the read is served from the primary. A later read/write-split phase
  moves it to the replica; no standard change is required.
- **Testing.** Exercised in-process with FastAPI's `TestClient` against the real
  Postgres from `docker-compose.test.yml`. The proof-of-work test
  `tests/integration/test_anomalies.py` drives the ingestion app (POST telemetry
  crossing each threshold) and the frontend app (`GET /anomalies`) against the same
  database, covering the per-class detection, the vehicle filter, and the
  `[since, until]` window inclusion/exclusion (inclusive bounds). The comms-loss-gap
  scenario is completed by the `comms-loss-watchdog` change.

## Tasks

- [x] 1.1 Add `GET /anomalies` to `app/frontend_api.py`: a required `vehicle_id` plus inclusive `[since, until]` timestamp query params, calling the existing `recent_anomalies(vehicle_id, since, until)` and returning 200 with the matching anomalies as a JSON list ordered by `detected_at`
- [x] 1.2 A vehicle with no anomalies in the requested window returns 200 with an empty list
- [x] 2.1 Integration test: `GET /anomalies` returns a vehicle's in-window anomalies and excludes rows outside the `[since, until]` window (inclusive bounds) and rows for other vehicles
- [x] 2.2 Integration test: anomalies are reachable end to end — POST a telemetry event that crosses a threshold to the ingestion API, then `GET /anomalies` for that vehicle over a covering window returns the detected anomaly
- [x] 3.1 Proof-of-work test `tests/integration/test_anomalies.py`: each default anomaly class fires exactly when its threshold is crossed and not otherwise, asserted via the ingestion API write path and the `GET /anomalies` read
- [x] 3.2 Proof-of-work test `tests/integration/test_anomalies.py`: `GET /anomalies` returns the matching rows for a given vehicle within the requested `[since, until]` window and excludes those outside it (the comms-loss-gap scenario is appended by `comms-loss-watchdog`)
- [x] 4.1 Write the AI build-log report to `docs/ai-build-logs/*.md` and append one line to `docs/ai-build-logs/index.md`
