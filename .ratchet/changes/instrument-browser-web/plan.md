# instrument-browser-web

## Why

The `replication-and-browser-traces` phase closes the last two flows. The
replication half (`replication-lag-metric` + `replication-dashboard`) is
complete. This change owns the **browser instrumentation half**: make the
React dashboard SPA emit OpenTelemetry traces so that loading
`http://localhost:8080` in a real browser produces a
`service.name=fleet-dashboard-web` trace in Tempo that **joins** the existing
`frontend-api` server spans.

Two foundations are reused as-is. `instrument-frontend-api` already produces
`service.name=frontend-api` server spans for the snapshot reads
(`GET /vehicles`, `GET /vehicles/anomalies/latest`, `GET /zones/counts`), and
the `observability-stack-compose` backbone exposes Alloy's OTLP/HTTP receiver on
host port 4318 as the single front door. What is missing is the browser side:
the SPA today ships no `@opentelemetry/sdk-trace-web` SDK, sends no traceparent
on its fetches, and exports nothing — so no browser telemetry exists and the
distributed trace stops at the frontend-api server span.

This change is the first of two in the browser half: the follow-on
`browser-web-dashboard` authors the **"Frontend Web (Browser)"** Grafana
dashboard JSON that visualizes the data this change produces. Its output —
`fleet-dashboard-web` document-load + fetch spans in Tempo, joined to
frontend-api — is the data that downstream change extends and visualizes.

## What Changes

- Add the browser OTel SDK packages to `web/package.json` (the
  `@opentelemetry/sdk-trace-web` family: web tracer provider + batch span
  processor, the OTLP/HTTP trace exporter, the W3C trace-context propagator, and
  the `document-load` + `fetch`/`xml-http-request` auto-instrumentations).
- Add a browser OTel bootstrap module under `web/src/` (e.g. `web/src/otel.ts`)
  that, when an OTLP endpoint is configured:
  - registers a `WebTracerProvider` whose resource carries
    `service.name = "fleet-dashboard-web"`,
  - installs the `document-load`, `fetch`, and `xml-http-request`
    instrumentations,
  - configures an OTLP/HTTP trace exporter pointed at Alloy, and
  - sets the W3C trace-context propagator and a `propagateTraceHeaderCorsUrls`
    (or equivalent) so `traceparent` is injected onto the cross-origin fetches
    to the frontend API origin.
  With no endpoint configured it is a **safe no-op** (installs nothing, exports
  nothing) so a plain `vite build`, `tsc --noEmit`, and the vitest suite are
  unaffected.
- Initialize the bootstrap from `web/src/main.tsx` **before** `createRoot(...)
  .render(...)`, so document-load and the first snapshot fetches are captured.
- Configure the browser OTLP endpoint from a **VITE_\* build arg** (e.g.
  `VITE_OTLP_TRACES_ENDPOINT`, browser-origin `http://localhost:4318/v1/traces`),
  threaded through `web/Dockerfile.runtime` and the `dashboard` compose service
  in the existing `VITE_API_BASE_URL` / `VITE_WS_URL` style. No host/port in
  source; same-origin / unset default = no-op.
- **CORS plumbing strictly required for the browser path:**
  - Configure Alloy's `otelcol.receiver.otlp` HTTP block with a `cors` stanza so
    the browser's cross-origin OTLP preflight + POST from origin
    `http://localhost:8080` is accepted.
  - Ensure the frontend API's existing `CORSMiddleware` permits the
    `traceparent` request header (today `allow_origins` is `*` but the default
    `allow_headers` does not include `traceparent`), so the now-preflighted
    snapshot fetches are not blocked.
- Add a focused test that the browser bootstrap is a no-op with no endpoint and,
  when given an endpoint, registers a provider whose resource carries
  `service.name=fleet-dashboard-web` — without requiring a running collector.

## Design

- **Vertical-slice scope.** This change instruments exactly the browser SDK
  flow: document-load + the REST snapshot fetches, exported OTLP/HTTP to Alloy,
  with traceparent joining frontend-api. It does **not** author the
  "Frontend Web (Browser)" dashboard JSON (that is `browser-web-dashboard`),
  does **not** add Grafana datasources/providers (shipped by
  `grafana-provisioning`), and does **not** touch the replication probe / metric
  or its dashboard (already complete). The WebSocket connect is covered only as
  far as it falls out of the fetch/document-load slice; the dedicated WS-connect
  span is not load-bearing for this change's proof and is left out of the thin
  slice. This change proves only that a real browser load yields a queryable
  `fleet-dashboard-web` trace in Tempo joined to frontend-api.
- **Reuse the standard browser SDK, don't hand-roll spans.** Lean on
  `@opentelemetry/sdk-trace-web` plus the official `document-load` /
  `fetch` / `xml-http-request` instrumentations and the
  `@opentelemetry/exporter-trace-otlp-http` exporter, mirroring how the Python
  side reuses a single bootstrap. Use the project `opentelemetry` skill for the
  Grafana-stack OTLP conventions rather than improvising.
- **service.name is the proof contract.** The phase proof searches Tempo for
  `tags=service.name=fleet-dashboard-web`, so the resource `service.name` must be
  exactly `fleet-dashboard-web` (the name locked in the batch manifest).
- **The join is via traceparent on the fetch, not magic.** The browser fetch
  instrumentation injects W3C `traceparent` onto the cross-origin snapshot
  requests; the frontend API (already instrumented) extracts it and parents its
  server span under the browser fetch span. The only new requirement is that
  both CORS surfaces (Alloy receiver for the export, frontend-api for the
  traceparent header) permit the cross-origin traffic — otherwise the browser
  silently drops the header / the export.
- **Config from the environment, nothing hard-coded.** The browser OTLP endpoint
  is a build-time `VITE_*` arg supplied only by compose, mirroring
  `VITE_API_BASE_URL` / `VITE_WS_URL`. No endpoint in source; unset = no-op.
- **Safe-by-default and idempotent.** With no endpoint the SPA behaves exactly as
  today: no SDK installed, no exporter, no collector required, nothing thrown.
  This keeps `tsc --noEmit`, `vite build`, and the vitest suite green and lets
  `main.tsx` call the bootstrap unconditionally.
- **Runtime-only plumbing.** Changes land in `web/` (SDK + bootstrap), the
  runtime `web/Dockerfile.runtime` + `docker-compose.yml` `dashboard` service
  (build arg), the Alloy config (receiver CORS), and the frontend API CORS
  headers. `docker-compose.test.yml` and the test harness are untouched.
- **Proof-of-work for this change** (the browser-trace half of the phase proof,
  whose dashboard half is `browser-web-dashboard`): from a clean
  `docker compose up -d --wait`, a real **headless browser** loads
  `http://localhost:8080` so the `@opentelemetry/sdk-trace-web` SDK runs and
  flushes; after a short delay, Tempo returns ≥1 trace for
  `service.name=fleet-dashboard-web`, ideally one whose fetch span parents a
  `frontend-api` server span (the join). A headless mechanism is used because
  `curl` cannot execute the JS SDK (Playwright MCP is available, or
  `npx playwright` / a docker'd chromium).

## Tasks

- [x] 1 Add the `@opentelemetry/sdk-trace-web` family to `web/package.json`
      (web provider + batch span processor, OTLP/HTTP trace exporter, W3C
      trace-context propagator, `document-load` + `fetch` + `xml-http-request`
      instrumentations) and lock them in `package-lock.json`; confirm
      `tsc --noEmit` still type-checks.
- [x] 2 Add `web/src/otel.ts`: a browser OTel bootstrap that, given an OTLP
      endpoint, registers a `WebTracerProvider` with resource
      `service.name=fleet-dashboard-web`, installs the document-load + fetch +
      XHR instrumentations, wires the OTLP/HTTP exporter, and sets the W3C
      propagator with `propagateTraceHeaderCorsUrls` covering the frontend API
      origin. With no endpoint it installs nothing (safe no-op).
- [x] 3 Initialize the bootstrap from `web/src/main.tsx` before
      `createRoot(...).render(...)` so document-load and the first snapshot
      fetches are captured.
- [x] 4 Read the browser OTLP endpoint from a `VITE_*` build arg (browser-origin
      `http://localhost:4318/v1/traces`), thread it through
      `web/Dockerfile.runtime` and the `dashboard` compose service in the
      existing `VITE_API_BASE_URL` / `VITE_WS_URL` style; default unset = no-op.
- [x] 5 Add a `cors` stanza to Alloy's `otelcol.receiver.otlp` HTTP block in
      `docker/alloy/config.alloy` so the browser's cross-origin OTLP preflight +
      POST from `http://localhost:8080` is accepted.
- [x] 6 Ensure the frontend API `CORSMiddleware` permits the `traceparent`
      request header so the preflighted cross-origin snapshot fetches succeed
      (extend `allow_headers` / `allow_origins` as needed,
      environment-overridable, no regression to the existing REST CORS).
- [x] 7 Add a focused test: the browser bootstrap is a no-op with no endpoint
      and, given an endpoint, registers a provider whose resource carries
      `service.name=fleet-dashboard-web` — no running collector required; keep
      the vitest suite and `tsc --noEmit` green.
- [x] 8 Bring the stack up (`docker compose up -d --wait`) and load
      `http://localhost:8080` in a **real headless browser** (Playwright MCP /
      `npx playwright` / docker'd chromium) so the SDK runs and flushes; confirm
      via the browser network panel that OTLP POSTs to Alloy succeed (CORS OK)
      and `traceparent` is sent on the snapshot fetches.
- [x] 9 Run the **browser-trace half of the phase proof**: after the headless
      load + a short delay, Tempo returns ≥1 trace for
      `service.name=fleet-dashboard-web`; verify (ideally) it joins a
      `frontend-api` server span via the fetch traceparent.
- [x] 10 Confirm `docker-compose.test.yml` is unchanged and the existing pytest /
      vitest suites still pass (the browser bootstrap stays a no-op under the
      test build).
