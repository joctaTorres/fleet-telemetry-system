Feature: Provision the "Frontend Web (Browser)" Grafana dashboard
  As an operator of the fleet telemetry system
  I want a file-provisioned Grafana dashboard that visualizes the browser-served
  React dashboard's OpenTelemetry traces — its document-load span, its REST
  snapshot fetch/XHR client spans, and the browser -> frontend-api joined trace —
  all under service.name=fleet-dashboard-web
  So that after a real browser loads the SPA the "Frontend Web (Browser)"
  dashboard populates from Tempo, closing the browser-trace half (and the whole)
  of the replication-and-browser-traces phase end to end

  # Vertical-slice scope: this is the CLOSING change of the
  # replication-and-browser-traces phase and owns ONLY the dashboard view JSON.
  # Its upstream, "instrument-browser-web", is complete: the SPA ships an
  # @opentelemetry/sdk-trace-web bootstrap that, when the OTLP endpoint build arg
  # is set, registers a WebTracerProvider with resource
  # service.name=fleet-dashboard-web, installs the document-load + fetch + XHR
  # instrumentations, exports OTLP/HTTP to Alloy (CORS handled), and injects a
  # W3C traceparent onto the cross-origin REST snapshot fetches so the trace
  # joins the frontend-api server spans. The data flows but nothing visualizes it.
  # This change adds exactly one dashboard file and NOTHING else: it does NOT
  # touch the browser SDK wiring, the CORS / build-arg / compose plumbing, the
  # datasource/provider provisioning (owned by "grafana-provisioning"), or the
  # replication probe / metric / dashboard (already complete).

  Background:
    Given the Alloy -> Tempo + Prometheus backbone from
      "observability-stack-compose" is running on the compose network
    And Grafana has a Tempo datasource (uid "tempo") and a file-based dashboard
      provider auto-loading any JSON dropped into "docker/grafana/dashboards/"
      (from "grafana-provisioning")
    And "instrument-browser-web" makes the SPA emit service.name=fleet-dashboard-web
      traces (a document-load span plus fetch/XHR client spans for the
      "GET /vehicles", "GET /vehicles/anomalies/latest", and "GET /zones/counts"
      snapshot reads) exported OTLP/HTTP to Alloy and joined to frontend-api via
      traceparent
    And the browser SDK emits traces only (no browser-side custom metrics), so
      the dashboard's panels are Tempo-backed rather than Prometheus-backed

  Scenario: The dashboard JSON is auto-provisioned on a clean boot
    Given a clean "docker compose up -d --wait"
    When Grafana finishes starting
    Then a dashboard titled exactly "Frontend Web (Browser)" is loaded from
      "docker/grafana/dashboards/"
    And "GET /api/search?query=Frontend%20Web" returns at least one result
    And Grafana startup logs show no provisioning error for the dashboard
    And no compose or provisioning change is required because the mount and
      provider already exist

  Scenario: Panels bind to the provisioned Tempo datasource uid, not auto-generated ids
    Given the dashboard JSON
    Then every panel's datasource references uid "tempo"
    And the dashboard renders identically across "docker compose down/up"

  Scenario: The dashboard lists recent browser traces after the SPA is loaded
    Given the stack is up and a real browser has loaded "http://localhost:8080"
      so the @opentelemetry/sdk-trace-web SDK ran and flushed
    When the "Browser traces" panel queries Tempo by
      "service.name=fleet-dashboard-web"
    Then the panel lists recent fleet-dashboard-web traces
    And Tempo "GET /api/search?tags=service.name=fleet-dashboard-web" returns
      >=1 trace

  Scenario: The document-load span is visible
    Given a fleet-dashboard-web trace from a real SPA load
    When the document-load panel filters fleet-dashboard-web spans for the
      document-load / page-load span
    Then the panel surfaces the document-load span (with its resource-fetch /
      page-load timings) so SPA load latency is visible

  Scenario: The REST snapshot fetch spans are visible
    Given a fleet-dashboard-web trace from a real SPA load
    When the fetch/XHR panel lists the client spans for the snapshot reads
      ("GET /vehicles", "GET /vehicles/anomalies/latest", "GET /zones/counts")
    Then the panel surfaces a client span per snapshot request under
      service.name fleet-dashboard-web

  Scenario: The browser -> frontend-api joined trace is drillable
    Given a fleet-dashboard-web fetch span that carried a traceparent to the
      frontend API
    When the trace is opened from the dashboard
    Then the same trace contains the frontend-api server span parented under the
      browser fetch span, so the trace spans
      service.name fleet-dashboard-web -> frontend-api end to end

  Scenario: The title is the proof contract
    Given the phase proof hits "GET /api/search?query=Frontend%20Web"
    Then the dashboard "title" contains the exact string "Frontend Web"
    And the chosen title "Frontend Web (Browser)" satisfies the phase proof and
      matches the phase dashboard name

  Scenario: The change is JSON-only and leaves upstream work untouched
    Given this is the closing change of the "replication-and-browser-traces" phase
    Then it adds only "docker/grafana/dashboards/frontend-web-browser.json"
    And it does not modify the backbone services, the datasource/provider
      provisioning, the browser SDK instrumentation, or the replication
      probe/metric/dashboard (all owned by upstream changes)
    And "docker-compose.test.yml" is unchanged and the existing pytest/vitest
      suites still pass

  Scenario: The phase proof-of-work passes end to end
    Given a clean "docker compose up -d --wait"
    And a real headless browser has loaded "http://localhost:8080" so the SDK
      flushed, after a short ingest delay
    Then Tempo returns >=1 trace for service.name=fleet-dashboard-web
    And a dashboard search for "Frontend Web" returns >=1 result
    And a dashboard search for "Primary/Replica Streaming" also returns >=1
      result (the replication dashboard shipped earlier in the phase), so both
      phase dashboards are provisioned and the phase closes
