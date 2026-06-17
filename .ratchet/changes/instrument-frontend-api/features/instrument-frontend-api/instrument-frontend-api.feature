Feature: Instrument the frontend read API for OTLP traces and request metrics
  As an operator of the fleet telemetry system
  I want the frontend (dashboard) API (FastAPI) to emit OTLP traces and request
  metrics to Alloy whenever an OTLP endpoint is configured
  So that GET traffic against the read endpoints — and the replica reads they run
  — shows up as live service.name=frontend-api spans in Tempo and as live
  request-rate / latency / error series in Prometheus, ready for the
  "Frontend API & WebSockets" dashboard to visualize

  Background:
    Given the shared "app.otel" bootstrap from "otel-bootstrap-python" is present
    And the Alloy -> Tempo + Prometheus backbone from "observability-stack-compose"
      is running on the compose network
    And the frontend service in the runtime "docker-compose.yml" sends OTLP/HTTP
      to Alloy via "OTEL_EXPORTER_OTLP_ENDPOINT"
    And the service identity is "service.name=frontend-api"

  Scenario: The frontend app installs OTel on startup behind the endpoint env
    Given the frontend FastAPI app module is imported
    When the app starts up with "OTEL_EXPORTER_OTLP_ENDPOINT" pointing at Alloy
    Then "configure_otel" has been called with service name "frontend-api"
    And the FastAPI app has been instrumented through the shared bootstrap helper
    And no OTLP endpoint or credentials are hard-coded in the app source

  Scenario: A GET read produces a frontend-api server span in Tempo
    Given the stack is up and OTLP export is configured
    When a request is made to a frontend read endpoint such as "GET /vehicles"
    Then a server span for that request is exported over OTLP/HTTP to Alloy
    And the span's resource carries "service.name=frontend-api"
    And the span is queryable in Tempo by "service.name=frontend-api"
    And the span describes the matched read route

  Scenario: The replica read for a request is visible as a child span
    Given the stack is up and OTLP export is configured
    When a frontend read endpoint serves a request from the streaming read replica
    Then a child span covering the replica read hangs under the request's server span
    And the child span is attributable to "service.name=frontend-api"
    And the read span makes the replica-backed read seam visible in the trace

  Scenario: Request metrics for rate, latency, and errors reach Prometheus
    Given the stack is up and OTLP export is configured
    When read requests are driven against the frontend API
    Then the frontend API records a request count metric carrying the HTTP method,
      route, and response status as attributes
    And it records a request duration metric for latency
    And those metrics are exported over OTLP/HTTP to Alloy and remote-written to
      Prometheus
    And the request-rate, latency, and error series are queryable in Prometheus
      for "service.name=frontend-api"

  Scenario: A rejected request is still observable as an error
    Given the stack is up and OTLP export is configured
    When a request that fails validation (for example "GET /anomalies" missing a
      required query parameter) is rejected with 422
    Then the request is still counted in the request metric with its 4xx status
    And the failed request remains attributable to "service.name=frontend-api"

  Scenario: The frontend service is wired to Alloy in the runtime compose file
    Given the runtime "docker-compose.yml"
    Then the "frontend" service sets "OTEL_EXPORTER_OTLP_ENDPOINT" to Alloy's
      OTLP/HTTP endpoint on the compose network
    And the frontend service starts only after the backbone is available
    And the endpoint value comes from compose config, not from app source

  Scenario: No OTLP endpoint configured leaves the app fully functional
    Given "OTEL_EXPORTER_OTLP_ENDPOINT" is unset
    When the frontend app starts and serves its read endpoints
    Then startup does not raise and requests are served normally
    And no collector is required for the process to run
    And no spans or metrics are exported

  Scenario: The test harness is left untouched
    Given the separate "docker-compose.test.yml" harness
    When OTel instrumentation is wired into the frontend service
    Then "docker-compose.test.yml" gains no OTLP endpoint and no collector
    And the existing pytest suites still pass with no running collector

  Scenario: WebSocket lifecycle metrics and the dashboard are out of scope here
    Given this change instruments only the frontend read surface and replica reads
    Then it does not add the active-WebSocket-connections gauge or broadcast counter
      (owned by "instrument-websocket-lifecycle")
    And it does not author the "Frontend API & WebSockets" dashboard
      (owned by "frontend-api-dashboard")
