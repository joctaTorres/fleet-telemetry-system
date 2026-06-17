Feature: Provision the "Frontend API & WebSockets" Grafana dashboard
  As an operator of the fleet telemetry system
  I want a file-provisioned Grafana dashboard that visualizes the frontend read
  API's request rate / latency / errors, the live WebSocket connection count and
  fan-out rate, and recent service.name=frontend-api traces
  So that opening the React dashboard SPA (or any WS client) makes the live
  connection count and REST request rates appear in Grafana, with GET/WS traces
  drillable in Tempo, closing the frontend-api-websockets phase end to end

  Background:
    Given the Alloy -> Tempo + Prometheus backbone from
      "observability-stack-compose" is running on the compose network
    And Grafana has Tempo (uid "tempo") and Prometheus (uid "prometheus")
      datasources plus a file-based dashboard provider auto-loading any JSON
      dropped into "docker/grafana/dashboards/" (from "grafana-provisioning")
    And the frontend read API is instrumented for "service.name=frontend-api"
      traces and emits the "frontend.requests" counter and
      "frontend.request.duration" (ms) histogram (from "instrument-frontend-api")
    And the WebSocket lifecycle emits the "frontend.ws.active_connections" gauge
      and "frontend.ws.broadcasts" counter (from "instrument-websocket-lifecycle")
    And those instruments are remote-written to Prometheus with OTLP->Prometheus
      naming (dots to underscores, counter "_total" suffix, histogram unit +
      "_bucket"/"_sum"/"_count")

  Scenario: The dashboard JSON is auto-provisioned on a clean boot
    Given a clean "docker compose up -d --wait"
    When Grafana finishes starting
    Then a dashboard titled exactly "Frontend API & WebSockets" is loaded from
      "docker/grafana/dashboards/"
    And "GET /api/search?query=Frontend%20API" returns at least one result
    And Grafana startup logs show no provisioning error for the dashboard
    And no compose or provisioning change is required because the mount and
      provider already exist

  Scenario: Panels bind to the provisioned datasource uids, not auto-generated ids
    Given the dashboard JSON
    Then every metric panel's datasource references uid "prometheus"
    And every trace panel's datasource references uid "tempo"
    And the dashboard renders identically across "docker compose down/up"

  Scenario: The active WebSocket connections panel goes non-zero with a client
    Given the stack is up and at least one WebSocket client is connected to "/ws"
    When the "Active WebSocket connections" panel queries Prometheus for the
      gauge derived from "frontend.ws.active_connections"
    Then the panel shows a value greater than zero while the client is connected
    And "max(frontend_ws_active_connections)" in Prometheus is greater than zero
    And the panel returns to zero after every client disconnects

  Scenario: The REST request-rate panel reflects live GET traffic
    Given the stack is up and GET reads are driven against the frontend API
    When the "REST request rate" panel queries "rate(...)" over the series
      derived from the "frontend.requests" counter
    Then the panel shows a non-zero request rate broken down by / filterable on
      "http.route" so read endpoints such as "GET /vehicles" are visible

  Scenario: The latency panel shows a request-duration quantile
    Given the stack is up and reads are driven against the frontend API
    When the "REST latency" panel computes a quantile (for example p95) via
      "histogram_quantile" over the "frontend.request.duration" (ms) buckets
    Then the panel renders a latency series for the frontend read endpoints

  Scenario: The error panel isolates non-2xx responses
    Given the stack is up and a request that fails validation is rejected with 422
    When the "REST errors" panel filters the "frontend.requests" series on the
      "http.status_code" attribute for non-2xx responses
    Then the panel shows the error series populated by the rejected request

  Scenario: The broadcast-rate panel reflects WebSocket fan-out
    Given the stack is up and at least one WebSocket client is connected
    When state patches are fanned out to the connected clients
    Then the "WebSocket broadcast rate" panel shows a non-zero rate over the
      series derived from the "frontend.ws.broadcasts" counter

  Scenario: The traces panel lists live frontend-api spans
    Given the stack is up with GET and WebSocket traffic
    When the "Frontend API traces" panel queries Tempo by
      "service.name=frontend-api"
    Then the panel lists recent frontend-api spans
    And the GET and "/ws" server spans are drillable from the panel
    And Tempo "GET /api/search?tags=service.name=frontend-api" returns >=1 trace

  Scenario: The exact Prometheus series names are read empirically, not guessed
    Given OTLP -> Alloy -> Prometheus remote-write rewrites OTel metric names
    When the panel queries are authored
    Then the real series names are read from the running Prometheus
      ("/api/v1/label/__name__/values" or "/api/v1/series") and the panel queries
      are wired to whatever Alloy actually remote-writes
    And the active-connections query stays in sync with the shipped
      "frontend_ws_active_connections" gauge name

  Scenario: The change is JSON-only and leaves upstream work untouched
    Given this is the closing change of the "frontend-api-websockets" phase
    Then it adds only "docker/grafana/dashboards/frontend-api.json"
    And it does not modify the backbone services, the datasource/provider
      provisioning, or the frontend instrumentation (owned by upstream changes)
    And "docker-compose.test.yml" is unchanged and the existing pytest/vitest
      suites still pass
