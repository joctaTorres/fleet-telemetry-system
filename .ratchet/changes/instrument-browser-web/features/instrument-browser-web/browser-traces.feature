Feature: Instrument the React dashboard with @opentelemetry/sdk-trace-web
  As an operator of the fleet telemetry system
  I want the browser-served React dashboard (SPA) to emit OpenTelemetry traces
  for its document load and its REST snapshot fetches, exporting OTLP/HTTP to
  Alloy with CORS handled, and to inject W3C trace context onto those fetches
  So that loading http://localhost:8080 produces a service.name=fleet-dashboard-web
  trace in Tempo that joins to the frontend-api server spans, closing the
  browser-trace half of the replication-and-browser-traces phase

  # Vertical-slice scope: this change owns ONLY the browser-side OTel SDK wiring
  # (the @opentelemetry/sdk-trace-web bootstrap, its document-load + fetch/XHR
  # instrumentation, the OTLP/HTTP-to-Alloy exporter, the traceparent
  # propagation onto the cross-origin REST fetches, and the CORS plumbing
  # strictly required for a browser to POST OTLP and to send traceparent on the
  # snapshot calls). It does NOT author the "Frontend Web (Browser)" Grafana
  # dashboard JSON — that is the separate downstream change "browser-web-dashboard".
  # It does NOT touch the replication probe / metric or its dashboard (owned by
  # "replication-lag-metric" / "replication-dashboard", already complete). It is
  # the smallest change that makes a real browser load of the SPA emit a
  # fleet-dashboard-web trace that reaches Tempo and joins frontend-api.

  Background:
    Given the runtime "docker-compose.yml" serves the built React dashboard on
      host port 8080 via the "dashboard" service, the frontend read API on host
      port 8002 via the "frontend" service, and Grafana Alloy's OTLP/HTTP
      receiver on host port 4318 via the "alloy" service
    And the browser reaches the frontend API cross-origin (the SPA origin is
      "http://localhost:8080", the REST calls target "http://localhost:8002")
    And "instrument-frontend-api" already produces service.name=frontend-api
      server spans for the "GET /vehicles", "GET /vehicles/anomalies/latest",
      and "GET /zones/counts" snapshot reads
    And the dashboard build bakes browser-origin config via VITE_* build args in
      "web/Dockerfile.runtime" / the "dashboard" compose service, with
      same-origin / no-op defaults so a plain "vite build" and the vitest suite
      are unaffected

  Scenario: The browser SDK bootstrap is wired and initialized before render
    Given the dashboard web app under "web/"
    When the app boots in the browser
    Then a browser OpenTelemetry bootstrap built on "@opentelemetry/sdk-trace-web"
      is initialized before the React app renders (from "web/src/main.tsx")
    And it registers a WebTracerProvider whose resource carries
      service.name "fleet-dashboard-web"
    And the @opentelemetry browser packages are added to "web/package.json"

  Scenario: A document-load trace is emitted on SPA load
    Given the instrumented dashboard is loaded in a browser
    Then the document-load instrumentation emits a document-load span (with its
      resource-fetch / page-load child timings) under service.name
      "fleet-dashboard-web"

  Scenario: The REST snapshot fetches are traced and carry trace context
    Given the instrumented dashboard is loaded in a browser
    When the SPA issues its one-time REST snapshot fetches ("GET /vehicles",
      "GET /vehicles/anomalies/latest", "GET /zones/counts") against the
      frontend API
    Then the fetch/XHR instrumentation emits a client span per request under
      service.name "fleet-dashboard-web"
    And a W3C "traceparent" header is injected onto each cross-origin request to
      the frontend API origin so the frontend-api server span shares the trace

  Scenario: Browser OTLP export reaches Alloy with CORS handled
    Given the instrumented dashboard running at origin "http://localhost:8080"
    When the SDK flushes spans over OTLP/HTTP
    Then it POSTs them to Alloy's OTLP/HTTP traces endpoint (host port 4318,
      configured from a VITE_* build arg, not hard-coded in source)
    And Alloy's OTLP receiver answers the browser's CORS preflight so the
      cross-origin export is not blocked
    And the frontend API's CORS configuration permits the "traceparent" request
      header so the preflighted snapshot fetches are not blocked

  Scenario: A real headless browser load produces a fleet-dashboard-web trace in Tempo
    Given a clean "docker compose up -d --wait"
    When a real headless browser loads "http://localhost:8080" so the
      @opentelemetry/sdk-trace-web SDK runs and flushes
    And a short flush/ingest delay elapses
    Then Tempo returns at least one trace for tags
      "service.name=fleet-dashboard-web"

  Scenario: The browser trace joins the frontend-api spans
    Given a browser-emitted fleet-dashboard-web trace whose fetch span carried a
      traceparent to the frontend API
    Then the frontend-api server span for that snapshot read is part of the same
      trace (the browser fetch span is its parent), so the trace spans
      service.name fleet-dashboard-web -> frontend-api

  Scenario: No-op / same-origin defaults keep build and tests green
    Given the OTLP endpoint build arg is unset (a plain "vite build" or the
      vitest run)
    Then the browser bootstrap installs no exporter and requires no collector,
      and emits nothing — it is a safe no-op
    And "npm run test:ui" (vitest) and "tsc --noEmit" still pass with the new
      SDK wiring in place

  Scenario: The change touches only the browser SDK slice, not the dashboard view
    Given this change owns only the browser-side instrumentation
    Then it adds the @opentelemetry browser SDK wiring under "web/" plus only the
      CORS / build-arg / compose plumbing strictly needed for a browser to export
      OTLP to Alloy and to send traceparent to the frontend API
    And it does NOT author the "Frontend Web (Browser)" Grafana dashboard JSON
      (owned by "browser-web-dashboard")
    And it leaves "docker-compose.test.yml" untouched and the existing pytest /
      vitest suites still pass
