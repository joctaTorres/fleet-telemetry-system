# AI Build Log — apply dashboard-shell-live-list

- **Session id:** 08ddfba8-20260616
- **Session name:** apply — dashboard-shell-live-list
- **Step:** apply
- **Change:** dashboard-shell-live-list
- **Batch / phase:** fleet-telemetry-service / react-dashboard
- **Date:** 2026-06-16

## Brief

Root slice of the `react-dashboard` phase. It carries the React + TypeScript +
Vite scaffold under `web/`, the `web` compose service, and the typed
snapshot-then-stream transport — and proves the hardest part of the phase
contract on the simplest surface: the **live 50-vehicle list**. Fetch the REST
snapshot once, open the WebSocket, then apply each `vehicle_state_changed` patch
to *only* the affected row — no polling, no full-list re-render. The follow-on
`dashboard-anomalies-zones` reuses this exact transport and patch-apply shape to
surface the latest anomaly per vehicle and the live per-zone tiles, landing the
phase llm-judge proof.

## What I built

- **Vite + React + TS scaffold** (`web/`): `package.json` (React 18, Vite 5,
  Vitest 2, React Testing Library, jsdom; `dev`/`build`/`test:ui` scripts),
  `tsconfig.json`, `vite.config.ts` (Vitest jsdom + global RTL setup), `index.html`,
  `src/main.tsx`, `src/App.tsx`, `src/test-setup.ts`. Framework-light — no router,
  no UI framework, no state library beyond React.

- **Typed transport** (`web/src/transport.ts`): a discriminated `PatchEvent`
  union for the three envelopes (`vehicle_state_changed | anomaly_detected |
  zone_count_changed`), payloads taken verbatim from `app/events.py` /
  `_build_payload` in `app/cdc_consumer.py` (the vehicle payload carries
  `vehicle_id`, `status`, `battery_pct`). `parsePatch` drops unknown/malformed
  `type`s (and the connect `snapshot` envelope) without throwing.
  `createHttpTransport` fetches `GET /vehicles` exactly once and opens a
  `WebSocket('/ws')`; `fetch` + `WebSocket` are injectable so tests need no
  backend. The `Transport` interface is the seam the UI depends on.

- **Granular store + components**: `web/src/vehicleStore.ts` seeds a
  `vehicle_id`-keyed map and applies a patch by id — known id replaces only that
  vehicle's object (others keep their reference; key position unchanged →
  stable order), unknown id returns the **same** map reference so React bails
  out. `web/src/VehicleRow.tsx` is `React.memo`'d so only the row whose `vehicle`
  reference changed re-renders. `web/src/VehicleList.tsx` runs one effect:
  one-shot snapshot then subscribe — no interval, no refetch.

- **Per-vehicle REST seam (backend)**: `current_vehicle_states()` in
  `app/persistence.py` — one `SELECT vehicle_id, status, battery_pct FROM
  vehicle_current_state ORDER BY vehicle_id` in a single MVCC snapshot via the
  `ConnFactory` seam (replica-capable) — and `GET /vehicles` in
  `app/frontend_api.py` returning it from `replica_connection`, mirroring the
  existing `/fleet/state` and `/zones/counts` read seams. Write path untouched.

- **`web` compose service** (`docker-compose.test.yml` + `web/Dockerfile` +
  `web/.dockerignore`): a Node-20 image whose default exercise is
  `npm run test:ui`, with no db/replica/redis/cdc dependency — the proof mocks
  the transport. `.dockerignore` excludes host `node_modules` so alpine installs
  its own native binaries.

- **Component-test proof** (`web/src/__tests__/`): `vehicleList.test.tsx` and
  `transport.test.ts`.

## Proof of work

`docker compose -f docker-compose.test.yml run --rm web npm run test:ui` — **9
tests passed, exit 0**. Coverage:

- **6.1** mounting seeded with a 50-vehicle REST snapshot renders exactly 50
  rows each showing status + battery; `fetchSnapshot` called exactly once and
  still once after advancing fake timers 60s; `setInterval` never called (no
  polling).
- **6.2** a `vehicle_state_changed` patch updates only the affected row's
  status/battery — render-count evidence (`onRender` per-row callback) shows the
  patched row's count incremented by exactly one and **zero** other rows
  re-rendered; still 50 rows (page not rebuilt). A fault flip surfaces on the
  patch with no refetch.
- **6.3** an unknown `vehicle_id` leaves all 50 rows intact (no drop/duplicate,
  no re-render); two patches for one id resolve last-write-wins for that row only
  (neighbours untouched); unknown-`type` and malformed frames are dropped at the
  transport without throwing.

`tsc --noEmit` is clean. Backend files parse cleanly; `current_vehicle_states` /
`GET /vehicles` mirror the proven replica read seams exactly.

## Notes / decisions

- **Unknown `vehicle_id` → ignore** (return the same map reference) rather than
  add a row: the list is the fixed seeded fleet, and same-reference lets React
  skip re-rendering entirely — the cleanest "leaves existing rows intact"
  evidence. The feature scenario explicitly permits ignore-or-add.
- **Render-count seam**: `VehicleRow` takes an optional `onRender(id)` the list
  forwards as a *stable* callback, so it never defeats the memo and the proof
  reads it directly as per-row render evidence.
- Scope held to the plan: anomalies-per-vehicle and zone tiles are deferred to
  `dashboard-anomalies-zones`, which reuses this transport and patch-apply shape.
