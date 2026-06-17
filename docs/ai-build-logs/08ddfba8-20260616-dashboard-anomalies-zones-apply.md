# AI Build Log — apply dashboard-anomalies-zones

- **Session id:** 08ddfba8-20260616
- **Session name:** apply — dashboard-anomalies-zones
- **Step:** apply
- **Change:** dashboard-anomalies-zones
- **Batch / phase:** fleet-telemetry-service / react-dashboard
- **Date:** 2026-06-16

## Brief

Follow-on slice that closes the `react-dashboard` phase. The root slice
`dashboard-shell-live-list` shipped the reusable spine — the Vite/React/TS
scaffold, the `web` compose service, the typed snapshot-then-stream transport,
and the granular per-id apply/render for the 50-vehicle list. This slice layers
the two remaining surfaces onto that exact machinery: the **most-recent anomaly
per vehicle** (on each row) and the **live per-zone entry-count tiles**, both
driven by the `anomaly_detected` / `zone_count_changed` patches the transport
already types. It lands the phase llm-judge proof
(`docker compose -f docker-compose.test.yml run --rm web npm run test:ui`).

## What I built

- **Anomaly store** (`web/src/anomalyStore.ts`): a `vehicle_id`-keyed map of the
  most-recent anomaly per vehicle, seeded from the anomaly snapshot and advanced
  by `anomaly_detected` patches (last-detected-wins). Immutable per-id apply like
  `vehicleStore` — only the patched vehicle's anomaly becomes a fresh reference.
  Unlike the fixed fleet, an anomaly can first appear live, so a patch for a
  vehicle with no prior anomaly **adds** it (a first-ever anomaly surfaces
  immediately); no phantom row results because rows are driven by the vehicle
  store, never by this map.

- **Zone store** (`web/src/zoneStore.ts`): a `zone_id`-keyed count map seeded
  from `/zones/counts` and advanced by `zone_count_changed` (last-write-wins).
  Mirrors `vehicleStore` exactly — the zone set is a closed universe, so a known
  zone replaces only that count and an unknown zone returns the **same** map
  reference (React bails out). Counts are primitive numbers, so untouched tiles'
  props compare equal.

- **Row anomaly** (`web/src/VehicleRow.tsx`): added an optional `anomaly` prop;
  the memoized row renders its `anomaly_type` when present and nothing when
  absent, so an `anomaly_detected` patch re-renders only that one row.

- **Zone tiles** (`web/src/ZoneTiles.tsx` + `web/src/ZoneTile.tsx`): one
  `React.memo`'d tile per zone (zone id + count); a `zone_count_changed` patch
  re-renders only the affected tile. The grid is itself memoized so a
  vehicle/anomaly patch never re-runs it.

- **Single-subscription wiring** (`web/src/App.tsx`): lifted the data layer into
  the App container so one component owns all three stores and the **single**
  snapshot-then-stream subscription. On load it fires three one-shot REST reads
  (`/vehicles`, `/vehicles/anomalies/latest`, `/zones/counts`) — no interval, no
  refetch — and the one `transport.subscribe` folds each delta into exactly one
  store. `VehicleList` became presentational (a pure view over the vehicle +
  anomaly maps), both `VehicleList` and `ZoneTiles` memoized so an unrelated
  patch doesn't re-run the other surface.

- **Transport seams** (`web/src/transport.ts`): added `fetchAnomalies()`
  (`GET /vehicles/anomalies/latest`) and `fetchZones()` (`GET /zones/counts`) to
  the `Transport` interface and `createHttpTransport`, plus the `AnomalySnapshotRow`
  / `ZoneCountsSnapshot` types. The WS path is unchanged.

- **Per-vehicle latest-anomaly REST seam (backend)**:
  `latest_anomaly_per_vehicle()` in `app/persistence.py` — one
  `SELECT DISTINCT ON (vehicle_id) vehicle_id, anomaly_type, detail, detected_at
  FROM anomalies ORDER BY vehicle_id, detected_at DESC` in a single MVCC snapshot
  via the `ConnFactory` seam (replica-capable), mirroring `recent_anomalies` /
  `current_vehicle_states` — and `GET /vehicles/anomalies/latest` in
  `app/frontend_api.py` returning it from `replica_connection`. The zone snapshot
  reuses the existing `GET /zones/counts`. Write path untouched.

- **Component-test proof** (`web/src/__tests__/`): new `dashboard.test.tsx` (the
  phase proof), `vehicleList.test.tsx` rewritten as a presentational unit test,
  and two added `transport.test.ts` cases for the new REST seams.

## Proof of work

`docker compose -f docker-compose.test.yml run --rm web npm run test:ui` —
**14 tests passed, exit 0** (image rebuilt first; the `web` Dockerfile bakes
source via `COPY . .`). Coverage in `dashboard.test.tsx`:

- **5.1** mounting `<App>` seeded with a mocked REST snapshot (50 vehicles + 10
  per-vehicle latest anomalies + 20 per-zone counts) renders each seeded row's
  anomaly_type and one tile per zone with its count; each snapshot fetched
  exactly once and still once after advancing fake timers 60s; `setInterval`
  never called (no polling).
- **5.2** an `anomaly_detected` patch for a vehicle with **no** prior anomaly
  surfaces it immediately and — by per-row/per-tile `onRender` evidence — only
  that row re-renders (+1); zero other rows and zero zone tiles re-render; still
  50 rows / 20 tiles (page not rebuilt).
- **5.3** a `zone_count_changed` patch updates only the affected tile (+1 render);
  zero other tiles and zero vehicle rows re-render; still 20 tiles (grid not
  rebuilt).
- **5.4** an unknown-id `zone_count_changed` / `anomaly_detected` leaves all
  rows/tiles intact (no drop/duplicate, no re-render); two patches for one id
  resolve last-write-wins for that one row/tile only (neighbours untouched).

`tsc --noEmit` is clean; `app/persistence.py` + `app/frontend_api.py` byte-compile
cleanly and the new read mirrors the proven replica read seams exactly.

## Notes / decisions

- **Anomaly apply is an upsert, not ignore-on-unknown** (the one deliberate
  deviation from the plan's per-task wording, which copied `vehicleStore`'s
  same-reference-for-unknown rule). Anomalies are open-ended: most vehicles start
  clean and gain their first anomaly live, so the phase criterion "a fault or
  anomaly surfaces immediately" requires adding a brand-new anomaly. Granularity
  is preserved — only the patched entry gets a fresh reference — and the
  no-phantom-row guarantee comes from rows being sourced from the vehicle store.
  The **zone** store keeps the closed-universe same-reference rule verbatim.
- **Single subscription** is enforced structurally: App is the sole owner of the
  one `transport.subscribe`; `VehicleList`/`ZoneTiles` are presentational and
  memoized, so cross-surface patches never re-run the unaffected surface.
- Scope held to the plan: no new transport, no new live path, no router/state
  library — two more id-keyed stores and two more memoized surfaces folded into
  the existing subscription, plus one thin replica-backed read.
