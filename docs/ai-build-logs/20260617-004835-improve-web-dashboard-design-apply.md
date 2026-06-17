# AI Build Log — apply improve-web-dashboard-design

- **Session id:** 20260617-004835
- **Session name:** apply — improve-web-dashboard-design
- **Step:** apply
- **Change:** improve-web-dashboard-design
- **Batch / phase:** web dashboard UX refresh
- **Date:** 2026-06-17

## Brief

Implemented the design/UX refresh of the existing `@web` React dashboard. No new pages or top-level components were added; all changes are scoped to the current shell, vehicle list, and zone tiles.

## What was implemented

- Created `web/src/index.css` with design tokens (colors, spacing, radii, shadows, typography) and a dark-themed global baseline, plus `.sr-only` and focus styles.
- Wired the stylesheet into `web/src/main.tsx`.
- Restyled `App.tsx` with a dashboard shell, header, connection-health indicator, and responsive CSS Grid that stacks vehicles over zones on narrow viewports and shows them side-by-side on wide viewports.
- Added dashboard-level feedback states:
  - loading banner while the three REST snapshots are in flight;
  - error banner when snapshot fetch fails;
  - empty messages when vehicle or zone lists are empty.
- Restyled `VehicleList`/`VehicleRow` with grid cell layout, status badges for `moving`/`idle`/`fault`/`offline`, and an accessible `<progress>` battery bar with a low-battery variant (< 20 %) and visible "low" text.
- Restyled `ZoneTiles`/`ZoneTile` grid and cards.
- Added a styled anomaly badge to each vehicle row.
- Added an `aria-live="polite"` announcement region that reports newly arriving anomalies.
- Added a connection-health indicator in the header that reads the WebSocket `readyState` via the transport's `onConnectionChange` hook; no polling or backend changes.
- Added `web/src/__tests__/dashboard-design.test.tsx` covering layout, status badges, battery bar, anomaly badges, aria-live announcements, the connection indicator, and loading/error/empty states.
- Updated `web/package.json` with `test` and `type-check` scripts.
- Exposed optional `readyState` / `onConnectionChange` on the `Transport` interface in `web/src/transport.ts`, keeping the existing snapshot/stream data path untouched.

## Design alignment

- Per `tech-stack`: React + TypeScript + Vite; styling delivered through a single global CSS file using CSS custom properties; no new runtime dependencies.
- Per `telemetry-architecture`: dashboard real-time path remains WebSocket push only; the connection indicator surfaces the existing WebSocket readyState without polling or new transport mechanisms.
- Per `ai-build-logging`: this report and the corresponding index entry are written as the final action of the apply step.

## Tests

- `npm run type-check` → clean (`tsc --noEmit` exit 0).
- `npm test` → 28/28 passed across 4 test files:
  - `src/__tests__/transport.test.ts` (7)
  - `src/__tests__/vehicleList.test.tsx` (3)
  - `src/__tests__/dashboard.test.tsx` (4)
  - `src/__tests__/dashboard-design.test.tsx` (14)

## Notes

- The project uses `npm` (evidenced by `web/package-lock.json` and README); the apply step ran `npm test` / `npm run type-check` rather than `pnpm` to match the existing lockfile and web compose command (`npm run test:ui`).
