# dashboard-shell-live-list

## Why

Phase 6 (`react-dashboard`) needs a small React + TypeScript dashboard that a
floor manager exercises live: the 50 vehicles with current status + battery, the
most recent anomaly per vehicle, and per-zone entry counts — all updating from
granular WebSocket patches with no full-list re-render and no polling.

This is the **root** slice of that phase. It carries the React/TS/Vite scaffold,
the `web` compose service, and the typed snapshot-then-stream transport, and it
proves the hardest part of the phase contract on the simplest surface: the
**live vehicle list**. The whole real-time spine of the UI — fetch the REST
snapshot once, open the WebSocket, then apply each `vehicle_state_changed` patch
to *only* the affected row — is built and proven here. The follow-on
`dashboard-anomalies-zones` then reuses this exact transport and patch-apply
shape to surface the latest anomaly per vehicle and the live per-zone tiles, and
lands the phase llm-judge proof.

The backend already emits the three derived patches over CDC → Redis → WebSocket
(phase 5) and the frontend API already serves replica-backed reads and a `/ws`
stream. The only backend gap for the vehicle list is a per-vehicle REST read: the
existing snapshot exposes aggregate per-status counts and zone counts, but not the
per-vehicle rows the list renders. This slice adds that one thin read seam so the
list genuinely renders from committed state on load.

## What Changes

- **React + TypeScript scaffold under `web/`** (Vite): `package.json`,
  `tsconfig.json`, `vite.config.ts`, `index.html`, and an `src/` entry. Vitest +
  React Testing Library + jsdom for component tests. A `test:ui` npm script (the
  phase proof command target) and the standard `dev`/`build` scripts. Kept
  minimal — no router, no UI framework, no state library beyond React.

- **Typed transport** `web/src/transport.ts`: a single module that (a) fetches
  the per-vehicle REST snapshot once on load (no interval/poll) and (b) opens the
  WebSocket and surfaces each message as a typed event. A discriminated union of
  the three patch envelopes from `app/events.py`
  (`vehicle_state_changed` | `anomaly_detected` | `zone_count_changed`), each
  with its typed payload (the vehicle patch carries `vehicle_id`, `status`,
  `battery_pct`). Unknown/malformed `type`s are dropped, not thrown. The
  transport is injectable so tests can mock REST + WS without a backend.

- **Live vehicle list** `web/src/VehicleList.tsx` + `web/src/VehicleRow.tsx` and a
  small `web/src/vehicleStore.ts`: seed a `vehicle_id`-keyed map from the REST
  snapshot, render one row per vehicle (status + battery), and apply each
  `vehicle_state_changed` patch by id. Rows are memoized so a patch re-renders
  **only** the changed row — the other 49 do not re-render and the list is not
  rebuilt. No polling; the only source of updates after load is the WS stream.

- **`web` compose service** `docker-compose.test.yml`: add a `web` service built
  from `web/` (Node image) whose default exercise is `npm run test:ui`, so the
  phase command `docker compose -f docker-compose.test.yml run --rm web npm run
  test:ui` runs the component tests. No dependency on the db/replica/redis/cdc
  topology — the proof mocks the transport.

- **Per-vehicle REST snapshot (thin backend seam)**: add
  `current_vehicle_states()` to `app/persistence.py` — a single
  `SELECT vehicle_id, status, battery_pct FROM vehicle_current_state` in one MVCC
  snapshot, using the `ConnFactory` seam so it reads from the replica — and a
  `GET /vehicles` route on `app/frontend_api.py` that returns that list from
  `replica_connection`. This is the REST snapshot the list renders from; it
  mirrors the existing `aggregate_fleet_state` / `zone_entry_counts` read seams.

- **Component-test proof** `web/src/__tests__/*.test.tsx`: see Design → Testing.

- **AI build-log** `docs/ai-build-logs/*.md` + an index line.

## Design

- **Vertical-slice scope.** The thinnest slice that proves the phase's live-update
  contract end to end on one surface: scaffold + transport + the vehicle list.
  Anomalies-per-vehicle and zone tiles are deliberately deferred to
  `dashboard-anomalies-zones`, which reuses this transport and patch-apply shape
  and lands the phase llm-judge proof. The risky, reusable machinery (typed
  snapshot-then-stream transport, granular per-id patch apply, no-poll/no-full-
  re-render rendering) is built and proven here.

- **Snapshot-then-stream, never poll.** On load the transport fetches the REST
  vehicle snapshot exactly once and renders it; thereafter the list lives only on
  `vehicle_state_changed` WS patches. No interval timer, no refetch — a fault or
  battery change surfaces on the next render tick driven by the patch, not a poll
  boundary, matching the phase success criterion.

- **Granular apply, granular render.** Patches are keyed by `vehicle_id` into a
  stable per-vehicle map; `VehicleRow` is memoized on its vehicle's value so
  applying a patch re-renders only that row. This is the property the phase proof
  asserts via render-count evidence — no full-list re-render, no page refresh.

- **Patch contract is the backend's contract.** The transport's event union and
  the vehicle payload shape (`vehicle_id`, `status`, `battery_pct`) are taken
  verbatim from `app/events.py` / the CDC translate payload, so the dashboard
  consumes exactly what the running stack publishes. The UI synthesizes nothing.

- **Reads from the replica, write path untouched.** The new `GET /vehicles`
  snapshot read goes through the `ConnFactory`/`replica_connection` seam like every
  other frontend read (ADR-0001 D1/D5), so connect-time read load stays off the
  primary. The live delta path is independent of the replica.

- **Reuse, don't reinvent.** Backend side reuses the `ConnFactory` read seam,
  `replica_connection`, and the existing frontend app; adds one read function and
  one route. Frontend side stays framework-light (React + Vite + Vitest only).

- **Testing.** `web/src/__tests__` component tests run under Vitest + React
  Testing Library with jsdom and a **mocked** REST + WS transport (no backend
  topology), kept light to fit the apply window — this is the phase proof command
  `docker compose -f docker-compose.test.yml run --rm web npm run test:ui`:
  (a) mounting the dashboard seeded with a REST snapshot of 50 vehicles renders
  exactly 50 rows, each showing status + battery, with no polling timer started;
  (b) dispatching a `vehicle_state_changed` patch for one `vehicle_id` updates
  that row's status/battery and, by render-count/`React.memo` evidence, re-renders
  **only** that row — the other rows do not re-render and the page is not
  refreshed;
  (c) a patch for an unknown `vehicle_id` leaves the existing rows intact (no drop
  / no duplicate);
  (d) two patches for the same `vehicle_id` resolve last-write-wins for that row
  only;
  (e) a message with an unknown `type` is dropped without throwing.
  Passing is the Judge confirming the rendered-DOM / render-count evidence shows
  granular per-row updates from each patch with no full-list re-render or refresh.

## Tasks

- [x] 1.1 Scaffold a Vite + React + TypeScript app under `web/`: `package.json`, `tsconfig.json`, `vite.config.ts`, `index.html`, `src/main.tsx`, `src/App.tsx`; add Vitest + React Testing Library + jsdom dev deps and a `test:ui` script (plus `dev`/`build`)
- [x] 1.2 Configure Vitest (jsdom environment, RTL setup file) so `npm run test:ui` runs the component tests headlessly
- [x] 2.1 Define the typed patch contract in `web/src/transport.ts`: a discriminated union of `vehicle_state_changed` | `anomaly_detected` | `zone_count_changed` envelopes (with the vehicle payload carrying `vehicle_id`, `status`, `battery_pct`) matching `app/events.py`
- [x] 2.2 Implement the transport: fetch the per-vehicle REST snapshot exactly once on load (no interval/poll), then open the WebSocket and surface each message as a typed event; drop unknown/malformed `type`s without throwing; make REST + WS injectable for tests
- [x] 3.1 Implement `web/src/vehicleStore.ts`: seed a `vehicle_id`-keyed map from the REST snapshot and apply each `vehicle_state_changed` patch by id (last-write-wins per vehicle; unknown id does not drop/duplicate existing rows)
- [x] 3.2 Implement `web/src/VehicleList.tsx` + `web/src/VehicleRow.tsx`: render one row per vehicle (status + battery); memoize the row so a patch re-renders only the affected row, never the whole list; wire the list to the transport with no polling
- [x] 4.1 Add `current_vehicle_states()` to `app/persistence.py`: `SELECT vehicle_id, status, battery_pct FROM vehicle_current_state` in one MVCC snapshot via the `ConnFactory` seam (replica-capable), mirroring `aggregate_fleet_state` / `zone_entry_counts`
- [x] 4.2 Add `GET /vehicles` to `app/frontend_api.py` returning the per-vehicle list from `replica_connection` (the REST snapshot the list renders from)
- [x] 5.1 Add a `web` service to `docker-compose.test.yml` built from `web/` (Node image) so `docker compose -f docker-compose.test.yml run --rm web npm run test:ui` runs the component tests; no db/replica/redis/cdc dependency (proof mocks the transport)
- [x] 6.1 Proof test: mounting the dashboard seeded with a 50-vehicle REST snapshot renders 50 rows (status + battery) and starts no polling timer
- [x] 6.2 Proof test: a `vehicle_state_changed` patch updates only the affected row (status/battery) — assert via render-count/memo evidence that the other rows do not re-render and the page is not refreshed
- [x] 6.3 Proof test: an unknown-`vehicle_id` patch leaves existing rows intact (no drop/duplicate); two patches for the same id resolve last-write-wins for that row only; an unknown-`type` message is dropped without throwing
- [x] 6.4 Confirm `docker compose -f docker-compose.test.yml run --rm web npm run test:ui` passes (Judge confirms granular per-row updates with no full-list re-render or refresh)
- [x] 7.1 Write the AI build-log report to `docs/ai-build-logs/*.md` and append one line to `docs/ai-build-logs/index.md`
