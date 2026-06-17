Feature: Instrument the ingestion API for OTLP traces and request metrics
  As an operator of the fleet telemetry system
  I want the ingestion API (FastAPI) to emit OTLP traces and request metrics to
  Alloy whenever an OTLP endpoint is configured
  So that k6-driven POST /telemetry traffic shows up as live ingestion-api spans
  in Tempo and as live request-rate / latency / error series in Prometheus,
  ready for the "Ingestion API" dashboard to visualize

  Background:
    Given the shared "app.otel" bootstrap from "otel-bootstrap-python" is present
    And the Alloy -> Tempo + Prometheus backbone from "observability-stack-compose"
      is running on the compose network
    And the ingestion service in the runtime "docker-compose.yml" sends OTLP/HTTP
      to Alloy via "OTEL_EXPORTER_OTLP_ENDPOINT"
    And the service identity is "service.name=ingestion-api"

  Scenario: The ingestion app installs OTel on startup behind the endpoint env
    Given the ingestion FastAPI app module is imported
    When the app starts up with "OTEL_EXPORTER_OTLP_ENDPOINT" pointing at Alloy
    Then "configure_otel" has been called with service name "ingestion-api"
    And the FastAPI app has been instrumented through the shared bootstrap helper
    And no OTLP endpoint or credentials are hard-coded in the app source

  Scenario: A POST /telemetry produces an ingestion-api server span in Tempo
    Given the stack is up and OTLP export is configured
    When a valid telemetry reading is sent to "POST /telemetry"
    Then a server span for that request is exported over OTLP/HTTP to Alloy
    And the span's resource carries "service.name=ingestion-api"
    And the span is queryable in Tempo by "service.name=ingestion-api"
    And the span describes the "POST /telemetry" route

  Scenario: Request metrics for rate, latency, and errors reach Prometheus
    Given the stack is up and OTLP export is configured
    When telemetry requests are driven against the ingestion API
    Then the ingestion API records a request count metric carrying the HTTP
      method, route, and response status as attributes
    And it records a request duration metric for latency
    And those metrics are exported over OTLP/HTTP to Alloy and remote-written to
      Prometheus
    And the request-rate, latency, and error series are queryable in Prometheus
      for "service.name=ingestion-api"

  Scenario: A rejected request is still observable as an error
    Given the stack is up and OTLP export is configured
    When a schema-invalid body is sent to "POST /telemetry" and rejected with 422
    Then the request is still counted in the request metric with its 4xx status
    And the failed request remains attributable to "service.name=ingestion-api"

  Scenario: The ingestion service is wired to Alloy in the runtime compose file
    Given the runtime "docker-compose.yml"
    Then the "ingestion" service sets "OTEL_EXPORTER_OTLP_ENDPOINT" to Alloy's
      OTLP/HTTP endpoint on the compose network
    And the ingestion service starts only after the backbone is available
    And the endpoint value comes from compose config, not from app source

  Scenario: No OTLP endpoint configured leaves the app fully functional
    Given "OTEL_EXPORTER_OTLP_ENDPOINT" is unset
    When the ingestion app starts and serves "POST /telemetry"
    Then startup does not raise and requests are served normally
    And no collector is required for the process to run
    And no spans or metrics are exported

  Scenario: The test harness is left untouched
    Given the separate "docker-compose.test.yml" harness
    When OTel instrumentation is wired into the ingestion service
    Then "docker-compose.test.yml" gains no OTLP endpoint and no collector
    And the existing pytest suites still pass with no running collector
