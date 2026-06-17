Feature: Reusable OpenTelemetry bootstrap for the Python services
  As a developer instrumenting the fleet telemetry Python services
  I want a single shared module that configures OTLP trace + metric export from
  the environment
  So that any FastAPI service (starting with the ingestion API) can emit spans
  and custom request metrics to Alloy with one call, and stays runnable in tests
  and local dev where no collector is present

  Background:
    Given a shared bootstrap module "app.otel" exists
    And it exposes a "configure_otel(service_name)" entry point
    And exporter configuration is read exclusively from the environment
    And no OTLP endpoint or credentials are hard-coded in source

  Scenario: Configuring with an OTLP endpoint wires trace and metric export
    Given the environment sets "OTEL_EXPORTER_OTLP_ENDPOINT" to an Alloy OTLP/HTTP URL
    When "configure_otel" is called with service name "ingestion-api"
    Then a global TracerProvider is installed that exports spans over OTLP/HTTP
    And a global MeterProvider is installed that periodically exports metrics over OTLP/HTTP
    And both providers carry a resource attribute "service.name" equal to "ingestion-api"

  Scenario: A span produced after configuration carries the service identity
    Given "configure_otel" was called with service name "ingestion-api"
    When a tracer obtained from the global provider starts and ends a span
    Then the exported span's resource includes "service.name=ingestion-api"

  Scenario: A custom request metric can be recorded through the configured meter
    Given "configure_otel" was called with service name "ingestion-api"
    When code obtains a meter from the global provider and records a counter increment
    Then the increment is held by the installed MeterProvider for periodic OTLP export
    And no exception is raised at record time

  Scenario: No OTLP endpoint configured is a safe no-op
    Given the environment does not set "OTEL_EXPORTER_OTLP_ENDPOINT"
    When "configure_otel" is called with service name "ingestion-api"
    Then it returns without raising
    And application code may still obtain tracers and meters and record telemetry without error
    And no network exporter is required for the process to run

  Scenario: Configuration is idempotent
    Given "configure_otel" has already been called once for "ingestion-api"
    When "configure_otel" is called a second time
    Then it does not raise and does not install a second conflicting provider

  Scenario: A FastAPI app can be instrumented through a single helper
    Given a FastAPI application instance
    When the bootstrap's FastAPI instrumentation helper is applied to that app
    Then incoming HTTP requests to that app produce server spans under the configured service name
    And the helper is callable whether or not an OTLP endpoint is configured
