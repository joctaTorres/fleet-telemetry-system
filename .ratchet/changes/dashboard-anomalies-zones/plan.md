# dashboard-anomalies-zones

## Why

Phase 6 (`react-dashboard`) needs a small React + TypeScript dashboard a floor
manager exercises live: the 50 vehicles with current status + battery, the most
recent anomaly per vehicle, and per-zone entry counts — all updating from
granular WebSocket patches with no full-list re-render and no polling.

The **root** slice `dashboard-shell-live-list` already shipped the hard,
reusable spine: the React/TS/Vite scaffold, the `web` compose service, the typed
snapshot-then-stream transport (already declaring the `anomaly_detected` and
`zone_count_changed` payload types), and the live vehicle list that applies each
`vehicle_state_changed` patch to *only* the affected row (no poll, no full
re-render).

This follow-on slice closes the phase by adding the two remaining surfaces on top
of that exact same machinery: the **most-recent anomaly per vehicle** (on each
vehicle row) and the **live per-zone entry-count tiles**, both driven by the
`anomaly_detected` / `zone_count_changed` WS patches the transport already
surfaces. It lands the phase `llm-judge` proof
(`docker compose -f docker-compose.test.yml run --rm web npm run test:ui`).

The backend already emits all three derived patches over CDC → Redis → WebSocket
(phase 5) and the frontend API already serves replica-backed reads. Zone counts
already have a REST read (`GET /zones/counts` → `zone_entry_counts`). The only
backend gap is the connect-time snapshot of the *most-recent anomaly per
vehicle*: `GET /anomalies` (`recent_anomalies`) filters by vehicle + time range
but does not give one latest row per vehicle. This slice adds that one thin read
seam so each row renders its current anomaly from committed state on load.

## What Changes

- **Anomaly store** `web/src/anomalyStore.ts`: a `vehicle_id`-keyed map of the
  most-recent anomaly per vehicle, seeded from the anomaly snapshot and advanced
  by `anomaly_detected` patches (last-detected-wins per vehicle). Treated
  immutably, mirroring `vehicleStore.ts`: applying a patch returns a new map in
  which only the patched vehicle's anomaly is a fresh reference, so a memoized
  row skips re-rendering the other 49.

- **Zone store** `web/src/zoneStore.ts`: a `zone_id`-keyed map of entry counts,
  seeded from the per-zone snapshot and advanced by `zone_count_changed` patches
  (last-write-wins per zone), with the same immutable per-id apply so only the
  patched tile re-renders.

- **Vehicle row shows its latest anomaly**: extend `web/src/VehicleRow.tsx` (and
  the row's props) to render the vehicle's most-recent anomaly_type when present;
  the row stays memoized so an `anomaly_detected` patch re-renders only that row.

- **Zone tiles** `web/src/ZoneTiles.tsx` + `web/src/ZoneTile.tsx`: render one
  memoized tile per zone (zone_id + entry_count) from the zone store; a
  `zone_count_changed` patch re-renders only the affected tile.

- **Wire both into the dashboard**: extend `web/src/App.tsx` / the list container
  so the single transport subscription also folds `anomaly_detected` into the
  anomaly store and `zone_count_changed` into the zone store — still one
  snapshot-then-stream subscription, no second poll path. The vehicle row reads
  its anomaly from the anomaly store keyed by `vehicle_id`.

- **Per-vehicle latest-anomaly REST seam (thin backend)**: add
  `latest_anomaly_per_vehicle()` to `app/persistence.py` — one
  `SELECT DISTINCT ON (vehicle_id) vehicle_id, anomaly_type, detail, detected_at
  FROM anomalies ORDER BY vehicle_id, detected_at DESC` in a single MVCC snapshot
  via the `ConnFactory` seam (replica-capable), mirroring `recent_anomalies` /
  `current_vehicle_states` — and a `GET /vehicles/anomalies/latest` route on
  `app/frontend_api.py` returning that list from `replica_connection`. The zone
  snapshot reuses the existing `GET /zones/counts`.

- **Component-test proof** `web/src/__tests__/*.test.tsx`: see Design → Testing.
  This is the phase `npm run test:ui` (`llm-judge`) target.

- **AI build-log** `docs/ai-build-logs/*.md` + an index line.

## Design

- **Vertical-slice scope.** The thin completion of the phase: the two remaining
  surfaces (anomaly-per-vehicle, zone tiles) layered onto the already-proven
  transport and granular per-id apply/render. No new transport, no new live path,
  no router/state library — just two more `vehicle_id`/`zone_id`-keyed stores and
  two more memoized render surfaces folded into the one existing subscription.

- **Reuse the proven spine.** The transport from `dashboard-shell-live-list`
  already parses and types `anomaly_detected` and `zone_count_changed`; this
  slice only adds the stores and views that consume them. The granular-apply →
  granular-render property (immutable per-id map + `React.memo`) is copied
  verbatim from `vehicleStore`/`VehicleRow`.

- **Snapshot-then-stream, never poll.** Both surfaces seed from a one-shot REST
  read on load and thereafter live only on WS patches. The anomaly snapshot comes
  from the new `GET /vehicles/anomalies/latest`; the zone snapshot reuses
  `GET /zones/counts`. No interval timer, no refetch — a new anomaly or a zone
  tick surfaces on the patch-driven render, matching the phase success criterion.

- **Granular apply, granular render.** Anomalies are keyed by `vehicle_id`, zone
  counts by `zone_id`; each patch replaces only its one entry (fresh reference)
  and leaves every other entry's reference stable, so the memoized row/tile
  re-renders alone. This is the property the phase proof asserts via render-count
  evidence — no full-list re-render, no page refresh.

- **Patch contract is the backend's contract.** Anomaly and zone payload shapes
  (`vehicle_id, anomaly_type, detail, detected_at` and `zone_id, entry_count`)
  are taken verbatim from `app/events.py` / the CDC translate payload already
  encoded in `transport.ts`. The UI synthesizes nothing.

- **Reads from the replica, write path untouched.** The new latest-anomaly read
  goes through the `ConnFactory`/`replica_connection` seam like every other
  frontend read (ADR-0001 D1/D5); the zone read already does. Connect-time read
  load stays off the primary; the live delta path is independent of the replica.

- **Testing.** `web/src/__tests__` component tests run under Vitest + React
  Testing Library with jsdom and a **mocked** REST + WS transport (no backend
  topology), kept light to fit the apply window — this is the phase proof command
  `docker compose -f docker-compose.test.yml run --rm web npm run test:ui`:
  (a) mounting the dashboard seeded with a REST snapshot (vehicles + per-vehicle
  latest anomalies + per-zone counts) renders each vehicle row's current anomaly
  and one tile per zone with its count, with no polling timer started;
  (b) dispatching an `anomaly_detected` patch for one `vehicle_id` updates that
  row's anomaly and, by render-count/`React.memo` evidence, re-renders **only**
  that row — other rows and all zone tiles do not re-render and the page is not
  refreshed;
  (c) dispatching a `zone_count_changed` patch for one `zone_id` updates only that
  tile's count, re-rendering **only** that tile, with no full-grid rebuild;
  (d) an `anomaly_detected` / `zone_count_changed` for an unknown id leaves
  existing rows/tiles intact (no drop/duplicate); two patches for the same id
  resolve last-write-wins for that one row/tile only.
  Passing is the Judge confirming the rendered-DOM / render-count evidence shows
  granular per-row and per-tile updates from each patch with no full-list
  re-render or refresh.

## Tasks

- [x] 1.1 Implement `web/src/anomalyStore.ts`: a `vehicle_id`-keyed map of the most-recent anomaly per vehicle; seed from the anomaly snapshot and apply each `anomaly_detected` patch by id (last-detected-wins; unknown id returns the same map reference; only the patched entry gets a fresh reference)
- [x] 1.2 Implement `web/src/zoneStore.ts`: a `zone_id`-keyed map of entry counts; seed from the per-zone snapshot and apply each `zone_count_changed` patch by id (last-write-wins; unknown id returns the same reference; only the patched entry gets a fresh reference)
- [x] 2.1 Extend `web/src/VehicleRow.tsx` (and its props) to render the vehicle's most-recent anomaly_type when present and nothing when absent; keep the row memoized so an `anomaly_detected` patch re-renders only that row
- [x] 2.2 Implement `web/src/ZoneTiles.tsx` + `web/src/ZoneTile.tsx`: one memoized tile per zone showing zone_id + entry_count; a `zone_count_changed` patch re-renders only the affected tile, never the whole grid
- [x] 3.1 Wire both stores into `web/src/App.tsx` / the list container: fold `anomaly_detected` into the anomaly store and `zone_count_changed` into the zone store within the single existing snapshot-then-stream subscription (no second poll path); pass each vehicle's latest anomaly into its row
- [x] 3.2 On load, fetch the anomaly snapshot (new `/vehicles/anomalies/latest`) and the zone snapshot (existing `/zones/counts`) exactly once each via the transport — no interval/refetch — and seed the two stores
- [x] 4.1 Add `latest_anomaly_per_vehicle()` to `app/persistence.py`: `SELECT DISTINCT ON (vehicle_id) vehicle_id, anomaly_type, detail, detected_at FROM anomalies ORDER BY vehicle_id, detected_at DESC` in one MVCC snapshot via the `ConnFactory` seam (replica-capable), mirroring `recent_anomalies` / `current_vehicle_states`
- [x] 4.2 Add `GET /vehicles/anomalies/latest` to `app/frontend_api.py` returning the per-vehicle latest-anomaly list from `replica_connection` (the anomaly REST snapshot the rows render from)
- [x] 5.1 Proof test: mounting the dashboard seeded with a REST snapshot (vehicles + per-vehicle latest anomalies + per-zone counts) renders each row's current anomaly and one tile per zone with its count, and starts no polling timer
- [x] 5.2 Proof test: an `anomaly_detected` patch updates only the affected vehicle's anomaly — assert via render-count/memo evidence that other rows and all zone tiles do not re-render and the page is not refreshed
- [x] 5.3 Proof test: a `zone_count_changed` patch updates only the affected tile's count — assert via render-count/memo evidence that other tiles and all vehicle rows do not re-render and the grid is not rebuilt
- [x] 5.4 Proof test: an unknown-id `anomaly_detected` / `zone_count_changed` leaves existing rows/tiles intact (no drop/duplicate); two patches for the same id resolve last-write-wins for that one row/tile only
- [x] 5.5 Confirm `docker compose -f docker-compose.test.yml run --rm web npm run test:ui` passes (Judge confirms granular per-row and per-tile updates with no full-list re-render or refresh) — lands the phase proof
- [x] 6.1 Write the AI build-log report to `docs/ai-build-logs/*.md` and append one line to `docs/ai-build-logs/index.md`
