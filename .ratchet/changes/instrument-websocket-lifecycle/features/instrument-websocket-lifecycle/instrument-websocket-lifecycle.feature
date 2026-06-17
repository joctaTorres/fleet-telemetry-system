Feature: Instrument the /ws WebSocket lifecycle with OTel spans and custom metrics
  As an operator of the fleet telemetry system
  I want the frontend API's WebSocket connection lifecycle and Redis-driven
  fan-out to emit OTLP traces and custom metrics — a live active-connections
  gauge and a broadcast counter — to Alloy whenever an OTLP endpoint is configured
  So that opening the dashboard SPA (or any WS client) makes the live connection
  count and fan-out volume appear in Prometheus and produces queryable
  service.name=frontend-api WebSocket spans in Tempo, feeding the downstream
  "Frontend API & WebSockets" dashboard

  Background:
    Given the frontend read API is already instrumented through the shared
      "app.otel" bootstrap by "instrument-frontend-api"
    And the frontend service in the runtime "docker-compose.yml" already sends
      OTLP/HTTP to Alloy via "OTEL_EXPORTER_OTLP_ENDPOINT"
    And the Alloy -> Tempo + Prometheus backbone is running on the compose network
    And the service identity is "service.name=frontend-api"
    And the global meter and tracer come from the shared bootstrap, never re-wired

  Scenario: A live gauge tracks the count of active WebSocket connections
    Given the stack is up and OTLP export is configured
    When a WebSocket client connects to "/ws" and is held open
    Then the active-WebSocket-connections gauge reports one connection
    And when a second client connects the gauge reports two
    And when a client disconnects the gauge decreases accordingly
    And the gauge is sourced from the ConnectionRegistry membership so it cannot
      diverge from the set of live connections it reports on
    And the gauge series carries "service.name=frontend-api" and is queryable in
      Prometheus while a client is connected

  Scenario: The gauge returns to zero when every client has disconnected
    Given the stack is up and OTLP export is configured
    When all WebSocket clients have disconnected
    Then the active-WebSocket-connections gauge reports zero
    And no connection leaks keep the gauge artificially above zero

  Scenario: A broadcast counter counts messages fanned out to clients
    Given the stack is up and OTLP export is configured
    And at least one WebSocket client is connected
    When a state patch published on the Redis event channel is fanned out to the
      connected clients
    Then a broadcast counter metric is incremented for the fan-out
    And the counter carries "service.name=frontend-api"
    And the broadcast-rate series is queryable in Prometheus

  Scenario: The WebSocket connection lifecycle produces a frontend-api span
    Given the stack is up and OTLP export is configured
    When a client connects to "/ws", receives its snapshot, and later disconnects
    Then a span covering the WebSocket connection lifecycle is exported over
      OTLP/HTTP to Alloy
    And the span's resource carries "service.name=frontend-api"
    And the span is queryable in Tempo by "service.name=frontend-api"
    And the connect-time snapshot read is visible within the WebSocket trace

  Scenario: Custom metrics are recorded off the global meter, no SDK re-wiring
    Given the frontend API module is imported
    Then the active-connections gauge and broadcast counter are created from the
      meter installed by the shared "app.otel" bootstrap
    And no TracerProvider, MeterProvider, or exporter is wired in this change
    And the metric instruments are module-level so tests can swap an in-memory meter

  Scenario: No OTLP endpoint configured leaves the WebSocket path fully functional
    Given "OTEL_EXPORTER_OTLP_ENDPOINT" is unset
    When the frontend app starts and a client connects to "/ws"
    Then startup does not raise and the snapshot + delta stream is served normally
    And no collector is required for the WebSocket path to run
    And the gauge and broadcast counter record against the no-op API meter and
      export nothing

  Scenario: The instrumentation does not perturb fan-out correctness
    Given the stack is up and OTLP export is configured
    When a client errors on send during a broadcast
    Then the dead client is still dropped from the registry as before
    And the active-connections gauge reflects the drop
    And recording the broadcast metric never blocks or breaks fan-out to live clients

  Scenario: The test harness is left untouched
    Given the separate "docker-compose.test.yml" harness
    When the WebSocket lifecycle instrumentation is wired in
    Then "docker-compose.test.yml" gains no OTLP endpoint and no collector
    And the existing pytest suites still pass with no running collector

  Scenario: The dashboard JSON is out of scope here
    Given this change instruments only the WebSocket lifecycle and fan-out
    Then it does not author the "Frontend API & WebSockets" dashboard JSON
      (owned by "frontend-api-dashboard")
    And it does not add Grafana datasources or providers
      (owned by "grafana-provisioning")
