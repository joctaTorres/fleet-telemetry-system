Feature: Propagate W3C trace context through the Redis event envelope
  As an operator of the fleet telemetry system
  I want the cdc-consumer to inject the active span's W3C trace context into the
  Redis pub/sub event envelope and the frontend API to extract it when it
  subscribes and fans the message out over WebSockets
  So that a single k6 telemetry write yields ONE connected Tempo trace whose
  spans span both service.name=cdc-consumer (decode -> publish) and
  service.name=frontend-api (redis subscribe -> WS broadcast), instead of two
  disconnected traces broken at the Redis hop

  Background:
    Given the "fleet:events" envelope contract in "app.events" is the one place
      the publisher (cdc-consumer) and the subscriber (frontend-api) share
    And the cdc-consumer already wraps each publish in a "cdc.publish" span and
      the frontend already wraps reads/WS in service.name=frontend-api spans
      (from "instrument-cdc-consumer" and the frontend instrumentation changes)
    And the Alloy -> Tempo + Prometheus backbone is running on the compose network
    And the standard W3C TraceContext propagator is used for inject/extract
    And the carrier lives out-of-band in the envelope, not inside the event payload

  Scenario: The envelope contract defines a shared out-of-band trace-context carrier
    Given the envelope is a JSON object "{type, payload}" defined in "app.events"
    When trace-context propagation is added to the contract
    Then "app.events" defines a single reserved carrier key (or nested object) for
      the W3C trace context, distinct from "type" and "payload"
    And it exposes shared inject/extract helpers so the publisher and subscriber
      never duplicate the propagator wiring
    And the "payload" content forwarded to browser clients is unchanged in shape

  Scenario: The cdc-consumer injects the active context at the publish seam
    Given the stack is up and OTLP export is configured
    When a watched WAL change is decoded and published to "fleet:events"
    Then inside the "cdc.publish" span the active W3C trace context is injected
      into the envelope carrier before the message is serialized and published
    And the injected "traceparent" reflects the trace and span id of the publish
      span (so the downstream parent is the publish span, not the decode span)
    And injection happens at the existing publish seam without changing the
      once-per-committed-change publish guarantee

  Scenario: The frontend extracts the context and links its subscribe span
    Given the stack is up and OTLP export is configured
    When the frontend Redis subscriber receives a message on "fleet:events"
    Then it extracts the W3C trace context from the envelope carrier
    And it starts a "frontend"/redis subscribe span whose parent is the extracted
      remote context, so the subscribe span shares the cdc-consumer trace
    And the WebSocket broadcast span is a child of that subscribe span
    And the broadcast span carries service.name=frontend-api

  Scenario: A single k6 write produces ONE trace spanning both services
    Given the stack is up with k6 driving telemetry writes
    When a committed write is decoded, published, subscribed, and broadcast
    Then Tempo contains a trace whose spans include BOTH
      resource.service.name=cdc-consumer and resource.service.name=frontend-api
    And the cdc-consumer publish span and the frontend subscribe/broadcast spans
      are part of that same single trace id, joined via the Redis traceparent

  Scenario: The browser WebSocket message contract is preserved
    Given a connected WebSocket client
    When an event is fanned out after trace context was injected
    Then the client receives the same "{type, payload}" message shape as before
    And the trace-context carrier is not leaked into the payload the client sees
    And the verbatim-forward behavior of the event content is unchanged

  Scenario: A message without trace context still broadcasts (backward compatible)
    Given the stack is up and OTLP export is configured
    When the subscriber receives an envelope that carries no trace-context carrier
    Then extraction yields an empty context and the subscribe span starts a new
      local trace rather than raising
    And the message is still fanned out to all clients exactly as before

  Scenario: With no OTLP endpoint configured propagation is a harmless no-op
    Given "OTEL_EXPORTER_OTLP_ENDPOINT" is unset on both services
    When changes are decoded, published, subscribed, and broadcast
    Then inject writes a no-op/empty carrier and extract yields an empty context
    And decode/translate/publish and subscribe/broadcast run exactly as today
    And no collector is required and nothing is raised on either side

  Scenario: Propagation never gates delivery on either side
    Given the stack is up under load
    When trace context is injected on publish and extracted on subscribe
    Then a failure to inject or extract never blocks or breaks the cdc pump loop
    And it never blocks or breaks the frontend fan-out to live clients
    And events are still published exactly once and delivered to every client

  Scenario: Unit tests prove the round-trip without a running collector
    Given the shared inject/extract helpers and the envelope contract
    Then a test asserts inject writes a valid "traceparent" into the envelope
      carrier from the active publish span
    And a test asserts the subscriber extracts that context so its span shares the
      injected trace id (the publish span is the parent)
    And a test asserts an envelope with no carrier still extracts cleanly and is
      still broadcast
    And the existing pytest suites still pass with no exporter running

  Scenario: Dashboards and upstream replication linkage are out of scope here
    Given this change only threads the Redis traceparent and links the spans
    Then it does not author the "CDC Consumer", "Pub/Sub", or "Redis Fan-out"
      dashboard JSON (owned by "cdc-pubsub-redis-dashboards")
    And it does not add Grafana datasources or providers
      (owned by "grafana-provisioning")
    And linking the upstream ingestion write across Postgres logical replication
      is best-effort and explicitly not required by this slice
