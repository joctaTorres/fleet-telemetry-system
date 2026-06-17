# AI Build Log — apply: improve-web-dashboard-design

- **Session id:** 20260617-010558
- **Session name:** apply — improve-web-dashboard-design
- **Step:** apply
- **Change name:** improve-web-dashboard-design

## What was implemented

- Added a global design-token stylesheet (`web/src/index.css`) and wired it into `web/src/main.tsx`.
- Styled the existing dashboard shell (`App.tsx`): header with title and connection-health indicator, responsive CSS Grid that stacks vehicles over zones on narrow viewports and places them side-by-side on `≥1024px` screens, plus section headings and consistent spacing.
- Styled `VehicleList`/`VehicleRow`: row layout, vehicle-id typography, status badges for `moving`/`idle`/`fault`/`offline`, accessible `<progress>` battery bar, low-battery variant with visible `low` text, and prominent anomaly badges.
- Styled `ZoneTiles`/`ZoneTile`: auto-fill grid of raised cards with zone id and count.
- Added WebSocket connection-health indicator to the header, driven by the transport’s `readyState` / `onConnectionChange` seam (no polling).
- Added `aria-live="polite"` anomaly announcement region and dashboard feedback states:
  - loading state while the three REST snapshots are in flight,
  - error state when any snapshot fails,
  - empty states for empty vehicle and zone lists.
- Extended the `Transport` interface and production implementation to expose WebSocket ready state and connection-change notifications.
- Added/updated tests covering responsive layout, status badges, accessible battery bar, anomaly badge, `aria-live` announcements, connection indicator, loading/error/empty states; tightened the existing live-dashboard mock transport to satisfy the updated interface.

## Outcome

- `npm run type-check` in `web/`: clean (`tsc --noEmit` exit 0)
- `npm test` in `web/`: **28 passed** across `transport.test.ts`, `vehicleList.test.tsx`, `dashboard-design.test.tsx`, and `dashboard.test.tsx`

> Note: `pnpm` is installed but `web/` uses an `npm` lockfile (`package-lock.json`), so the commands were run via `npm` to respect the existing dependency state.
