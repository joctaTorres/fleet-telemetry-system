Feature: Instrument the CDC consumer for OTLP traces and event metrics
  As an operator of the fleet telemetry system
  I want the long-lived CDC consumer (WAL -> pgoutput decode -> Redis fan-out)
  to emit OTLP traces and custom event metrics to Alloy whenever an OTLP
  endpoint is configured
  So that a committed telemetry write that reaches the WAL shows up as live
  cdc-consumer decode and publish spans in Tempo and as live event-throughput,
  decode-lag, and publish-count series in Prometheus, ready for the downstream
  "CDC Consumer" / "Pub/Sub" / "Redis Fan-out" dashboards to visualize and for
  the next change to thread the Redis traceparent through

  Background:
    Given the shared "app.otel" bootstrap from "otel-bootstrap-python" is present
    And the Alloy -> Tempo + Prometheus backbone from "observability-stack-compose"
      is running on the compose network
    And the standalone "cdc" service in the runtime "docker-compose.yml" runs
      "python -m app.cdc_consumer" as a long-lived process (not a FastAPI app)
    And the service identity is "service.name=cdc-consumer"
    And the global meter and tracer come from the shared bootstrap, never re-wired

  Scenario: The CDC consumer process installs OTel on startup behind the endpoint env
    Given the CDC consumer module is imported
    When the supervised process starts with "OTEL_EXPORTER_OTLP_ENDPOINT" pointing
      at Alloy
    Then "configure_otel" has been called with service name "cdc-consumer"
    And the consumer obtains its tracer and meter from the shared bootstrap
    And no OTLP endpoint or credentials are hard-coded in the app source

  Scenario: Decoding a watched WAL change produces a cdc-consumer span in Tempo
    Given the stack is up and OTLP export is configured
    When a committed telemetry write is decoded from the pgoutput stream into a
      watched-table change
    Then a span covering the decode of that change is exported over OTLP/HTTP to
      Alloy
    And the span's resource carries "service.name=cdc-consumer"
    And the span is queryable in Tempo by "service.name=cdc-consumer"
    And the span carries the event type / watched table as an attribute

  Scenario: Each published event produces a per-event publish span
    Given the stack is up and OTLP export is configured
    When a decoded change is translated into an event envelope and published to
      the "fleet:events" Redis channel
    Then a per-event publish span wrapping the Redis publish is exported to Alloy
    And the publish span is a child of (or shares the trace with) the decode span
      for that change
    And the publish span carries "service.name=cdc-consumer" and the event type
    And the publish span is the seam a later change uses to inject the W3C
      traceparent into the event envelope (no injection happens in this change)

  Scenario: Event throughput, decode lag, and publish counts reach Prometheus
    Given the stack is up and OTLP export is configured
    When k6 drives telemetry writes that the consumer decodes and publishes
    Then the consumer records an event-published counter keyed by event type
    And it records a decode-lag measure for the processing latency of each change
    And those metrics are exported over OTLP/HTTP to Alloy and remote-written to
      Prometheus
    And the event-throughput, decode-lag, and publish-count series are queryable
      in Prometheus for "service.name=cdc-consumer"

  Scenario: Custom instruments come off the global meter, no SDK re-wiring
    Given the CDC consumer module is imported
    Then the event counter and decode-lag instrument are created from the meter
      installed by the shared "app.otel" bootstrap
    And no TracerProvider, MeterProvider, or exporter is wired in this change
    And the instruments are module-level so tests can swap an in-memory meter

  Scenario: A non-watched change is not turned into a spurious event metric
    Given the stack is up and OTLP export is configured
    When the pgoutput stream carries a change to a table that is not watched
    Then no event-published metric is recorded for it
    And no publish span is emitted for it
    And the consumer behaves exactly as before, publishing nothing for that change

  Scenario: The cdc service is wired to Alloy in the runtime compose file
    Given the runtime "docker-compose.yml"
    Then the "cdc" service sets "OTEL_EXPORTER_OTLP_ENDPOINT" to Alloy's OTLP/HTTP
      endpoint on the compose network
    And the endpoint value comes from compose config, env-overridable, not from
      app source
    And the cdc service starts only after the backbone is available

  Scenario: Instrumentation does not perturb decode / publish / slot-advance behavior
    Given the stack is up and OTLP export is configured
    When the consumer streams, decodes, and publishes under load
    Then events are still published exactly once per committed watched change
    And the standby status-update feedback still advances the slot's confirmed-flush
    And recording spans or metrics never blocks or breaks the pump loop
    And the unbounded supervised-restart behavior is unchanged

  Scenario: No OTLP endpoint configured leaves the consumer fully functional
    Given "OTEL_EXPORTER_OTLP_ENDPOINT" is unset
    When the CDC consumer starts and streams the slot
    Then startup does not raise and decode/translate/publish runs normally
    And no collector is required for the process to run
    And spans and metrics record against the no-op API providers and export nothing

  Scenario: The test harness is left untouched
    Given the separate "docker-compose.test.yml" harness and the conftest
      background-thread consumer fixture
    When OTel instrumentation is wired into the CDC consumer
    Then "docker-compose.test.yml" gains no OTLP endpoint and no collector
    And the background-thread consumer used by tests still runs with no exporter
    And the existing pytest suites still pass with no running collector

  Scenario: Dashboards and Redis trace-context propagation are out of scope here
    Given this change instruments only the CDC consumer process
    Then it does not author the "CDC Consumer", "Pub/Sub", or "Redis Fan-out"
      dashboard JSON (owned by "cdc-pubsub-redis-dashboards")
    And it does not inject the W3C traceparent into the Redis event envelope nor
      link the frontend subscribe / WS broadcast spans
      (owned by "propagate-trace-context-redis")
    And it does not add Grafana datasources or providers
      (owned by "grafana-provisioning")
