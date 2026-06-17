# improve-web-dashboard-design

## Why

The `@web` dashboard uses semantic class names but has no actual CSS, no design tokens, and no visual feedback for loading, errors, empty data, or live WebSocket health. Fleet operators currently see plain-text status, battery percentages, and anomalies, making it hard to scan state at a glance. This change improves the existing UI’s readability, accessibility, and trustworthiness without introducing new pages or components.

## What Changes

- Add a design-token CSS file (`src/index.css`) and import it in `main.tsx` to establish color, typography, spacing, radii, and shadow tokens.
- Style the existing dashboard shell and sections using the semantic class names already present in `App.tsx`, `VehicleList`, `VehicleRow`, `ZoneTiles`, and `ZoneTile`; no new React components or routes are added.
- Implement a responsive dashboard layout: a header with connection status and a main content grid that stacks vehicles over zones on narrow viewports and shows them side-by-side on wide viewports.
- Render vehicle status as a colored badge with visible text, and render battery level as a styled progress bar with an accessible label plus a low-battery variant.
- Render vehicle anomalies as prominent badges and add an `aria-live` region in `App.tsx` so that newly arriving anomalies are announced to assistive technology.
- Add dashboard-level feedback states: loading while initial REST snapshots are fetched, error when fetch fails, and empty messages when vehicles or zones lists are empty.
- Add a connection-health indicator to the header that reflects the WebSocket open/closed state.
- Add or update tests in `web/src/__tests__/` covering layout, status/battery rendering, anomaly badges, loading/error/empty states, and the connection indicator.
- Reference feature files:
  - `features/web-dashboard-design/responsive-layout.feature`
  - `features/web-dashboard-design/vehicle-status-and-battery.feature`
  - `features/web-dashboard-design/anomalies-and-connection.feature`
  - `features/web-dashboard-design/dashboard-states.feature`

## Design

**Tooling choices**

- All styling is delivered through a single global CSS file using CSS custom properties. This keeps the existing React + TypeScript + Vite stack intact, adds no runtime dependencies, and avoids CSS-in-JS or component-library sprawl. Vite processes the stylesheet when it is imported in `main.tsx`.
- No new components are created; the existing presentational/container split is preserved, as are the immutable stores and `React.memo` memoization. Only presentational markup and class names are adjusted where necessary.

**Responsive layout**

- The dashboard shell uses CSS Grid. On viewports narrower than `640px` the vehicles and zones sections stack vertically and fill the width. On viewports `1024px` and wider the two sections share a single horizontal row with a gutter. Headings and consistent internal spacing create visual grouping.

**Accessibility**

- Status badges pair color with text so color is never the only signal.
- Battery is rendered with a native `<progress>` element (or `role="progressbar"`) plus a visible percentage and an `aria-label` describing the value.
- A visually hidden but screen-reader-visible `aria-live="polite"` region in `App.tsx` announces new anomalies.
- Focus styles on any newly interactive elements (if focusable) use the design-token focus ring.
- Color choices target WCAG 2.1 AA contrast for normal text.

**Real-time behavior**

- The connection indicator reads the existing WebSocket `readyState` without introducing polling, long-polling, or fetch loops. This preserves the telemetry-architecture requirement that dashboard updates arrive via WebSocket push.
- Loading/error/empty states are driven by React state already present in `App.tsx`; no changes to `transport.ts`, stores, or data flow are required.

**AI build logging (per `ai-build-logging` standard)**

- After this propose step completes, write a session report to `docs/ai-build-logs/<session-id>-improve-web-dashboard-design-propose.md` and append one line to `docs/ai-build-logs/index.md`. The same pattern is planned for the apply and verify steps.

## Tasks

- [x] 1.1 Create `web/src/index.css` with design tokens and a global baseline
- [x] 1.2 Import `index.css` in `web/src/main.tsx`
- [x] 2.1 Style `App.tsx` shell, header, and responsive vehicles/zones grid
- [x] 2.2 Style `VehicleList` and `VehicleRow` layout and spacing
- [x] 3.1 Add status badge rendering and styles for moving/idle/fault/offline statuses
- [x] 3.2 Add battery progress bar with accessible label and low-battery variant
- [x] 4.1 Style `ZoneTiles` grid and `ZoneTile` cards
- [x] 5.1 Style anomaly badges and add the `aria-live` announcement region in `App.tsx`
- [x] 5.2 Add the WebSocket connection-health indicator to the header
- [x] 6.1 Implement loading state while initial REST snapshots are in flight
- [x] 6.2 Implement error state when snapshot fetch fails
- [x] 6.3 Implement empty states for empty vehicle and zone lists
- [x] 7.1 Add/update tests for responsive layout, status badges, battery bar, anomaly badge, aria-live, connection indicator, loading, error, and empty states
- [x] 7.2 Run `pnpm test` and `pnpm type-check` in `web/` and fix any failures
- [x] 7.3 Update `docs/ai-build-logs` with the propose-session report and index entry
