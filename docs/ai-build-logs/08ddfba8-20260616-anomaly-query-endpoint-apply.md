# AI Build Log — apply anomaly-query-endpoint

- **Session id:** 08ddfba8-20260616
- **Session name:** apply — anomaly-query-endpoint
- **Step:** apply
- **Change:** anomaly-query-endpoint
- **Batch / phase:** fleet-telemetry-service / anomaly-detection-and-query
- **Date:** 2026-06-16

## Brief

The read half of the `anomaly-detection-and-query` phase: the `anomaly-detection`
root change already wrote anomalies synchronously into the `anomalies` table and
exposed the `recent_anomalies(vehicle_id, since, until)` persistence read seam,
but those rows had no network surface. This change adds `GET /anomalies` to the
frontend API as a thin adapter over that seam, and lands the phase
proof-of-work integration test `tests/integration/test_anomalies.py`. Together
this completes the phase's end-to-end slice: POST telemetry that crosses a
threshold writes an anomaly → GET reads it back, filtered by vehicle and an
inclusive `[since, until]` window. The by-absence comms-loss watchdog and its
comms-loss-gap proof scenario remain the explicit out-of-scope follow-on change
`comms-loss-watchdog`. Plan tasks 1.1–4.1 completed; the phase proof passes
against a real Postgres (11/11), full suite 57/57 (exit 0).

## Artifacts written

- `app/frontend_api.py` — added `GET /anomalies`: a required `vehicle_id` plus an
  inclusive `[since, until]` range (ISO-8601 timestamp query params), calling the
  existing `recent_anomalies(vehicle_id, since, until)` and returning 200 with the
  matching anomaly objects (`vehicle_id`, `anomaly_type`, `detail`, `detected_at`)
  as a JSON list ordered by `detected_at`. A vehicle with no matching rows returns
  an empty list. (1.1, 1.2)
- `tests/integration/test_anomalies.py` — the phase proof-of-work. Drives the
  ingestion app (`POST /telemetry`) and the frontend app (`GET /anomalies`)
  in-process via FastAPI's `TestClient` against the same real Postgres:
  - Each default anomaly class fires exactly when its threshold is crossed and not
    otherwise, asserted through the HTTP write path and the `GET /anomalies` read —
    `fault_status`, `error_codes` (vs empty), `low_battery` (vs threshold-exact and
    vs charging), `overspeed` (vs at-limit), `stuck` (≥10s vs <10s), `teleport`
    (>15 m/s implied vs plausible), `battery_rising` (vs charging), and a clean
    event producing none. (3.1)
  - Anomalies are reachable end to end: a single threshold-crossing event POSTed to
    the ingestion API is read back via `GET /anomalies` with its type, detail and
    detected_at. (2.2)
  - `GET /anomalies` returns a vehicle's in-window rows ordered by `detected_at`,
    inclusive on both bounds, excluding rows before/after the window and rows for
    other vehicles; an empty list for a vehicle with no rows. (2.1, 3.2)

## Design alignment

- Per `telemetry-architecture`: the read endpoint goes on the **frontend** API,
  never the ingestion API — ingestion stays stateless and write-only. This is the
  third read route alongside `GET /fleet/state` and `GET /zones/counts`, and is a
  thin adapter over an existing read seam, mirroring how those wrap
  `aggregate_fleet_state()` and `zone_entry_counts()`.
- Reuse, don't reinvent: `recent_anomalies()` is consumed unchanged, so the
  endpoint is a single indexed range scan over the `(vehicle_id, detected_at)`
  composite index rather than a table scan. Bounds stay inclusive on both ends,
  matching the seam.
- No authoritative in-process state: the result is derived fresh from the
  `anomalies` table on each request; the frontend app holds no buffer that could
  diverge from committed state.
- Scoped deviation (unchanged from the sibling frontend reads): this phase is a
  single Postgres with no replica/CDC/Redis yet, so the read is served from the
  primary; a later read/write-split phase moves it to the replica. No standard
  change required.
- Vertical-slice scope: the thinnest HTTP read path that proves the phase goal —
  request → `recent_anomalies()` → JSON. No WebSocket, no CDC push, no comms-loss
  watchdog; those are later/sibling changes.

## Outcome

`docker compose -f docker-compose.test.yml run --rm api pytest
tests/integration/test_anomalies.py` → exit 0, 11 passed. Full suite
`tests/integration` → 57 passed (46 prior + 11 new). The api image was rebuilt so
the new source/test are present in the container. Plan tasks 1.1–4.1 checked off.
The comms-loss-gap scenario of the phase proof is appended by the follow-on
`comms-loss-watchdog` change, which also adds the by-absence detector.
