Feature: Provision the "Ingestion API" Grafana dashboard
  As an operator of the fleet telemetry system
  I want an "Ingestion API" dashboard auto-provisioned into Grafana that
  visualizes the live request rate, latency, and error series and the
  ingestion-api traces produced by POST /telemetry
  So that, from a clean `docker compose up` with k6 driving load, I can watch
  the whole instrument -> pipeline -> ingest -> analyze loop for one real flow
  without importing anything by hand

  Background:
    Given the Grafana provisioning from "grafana-provisioning" is in place, with
      a Tempo datasource at uid "tempo" and a Prometheus datasource at uid
      "prometheus", and a file-based dashboard provider that auto-loads any JSON
      dropped into "docker/grafana/dashboards" (mounted read-only at
      "/var/lib/grafana/dashboards")
    And the instrumentation from "instrument-ingestion-api" emits, when an OTLP
      endpoint is configured, a request counter "ingestion.requests" and a
      request duration histogram "ingestion.request.duration" (milliseconds),
      each carrying "http.method", "http.route", and "http.status_code"
      attributes, plus "service.name=ingestion-api" server spans for
      "POST /telemetry"
    And no upstream change has authored any dashboard JSON yet

  Scenario: The dashboard JSON is dropped into the provisioned dashboards folder
    Given the provisioned dashboards directory "docker/grafana/dashboards"
    When the "Ingestion API" dashboard JSON file is added to that directory
    Then the file is a valid Grafana dashboard model with title "Ingestion API"
    And it requires no manual import — the provider auto-loads it on startup
    And it is committed to the repo so a clean `docker compose up` includes it

  Scenario: The dashboard binds to the provisioned datasources by fixed uid
    Given the "Ingestion API" dashboard JSON
    Then its metric panels reference the Prometheus datasource by uid "prometheus"
    And its trace panel references the Tempo datasource by uid "tempo"
    And no panel hard-codes a Grafana auto-generated datasource id, so the
      dashboard survives a `docker compose down/up`

  Scenario: Grafana auto-provisions the dashboard on a clean startup
    Given the observability stack is started with "docker compose up -d --wait"
    When Grafana finishes provisioning
    Then a dashboard search for "Ingestion API" returns at least one result
    And Grafana's startup logs report no dashboard provisioning errors for it

  Scenario: A request-rate panel shows live POST /telemetry throughput
    Given the stack is up and k6 is driving "POST /telemetry"
    Then the dashboard has a panel showing ingestion request rate over time
    And that panel queries the Prometheus series derived from
      "ingestion.requests" using a rate over the counter
    And the panel is broken down by, or filterable on, "http.route" so the
      "POST /telemetry" flow is visible

  Scenario: A latency panel shows request-duration quantiles
    Given the stack is up and k6 is driving "POST /telemetry"
    Then the dashboard has a panel showing ingestion request latency
    And that panel queries the Prometheus histogram derived from
      "ingestion.request.duration" (milliseconds)
    And it renders at least one latency quantile (for example p95) using the
      histogram buckets

  Scenario: An error panel shows the rejected-request series
    Given the stack is up and both accepted (201) and rejected (422) requests
      have been driven against the ingestion API
    Then the dashboard has a panel showing the ingestion error rate or error
      ratio
    And that panel isolates non-2xx responses using the "http.status_code"
      attribute from the "ingestion.requests" series
    And the panel is populated once at least one 4xx/5xx request has occurred

  Scenario: A traces panel surfaces live ingestion-api spans
    Given the stack is up and k6 is driving "POST /telemetry"
    Then the dashboard has a panel backed by the Tempo datasource
    And it lists recent traces for "service.name=ingestion-api"
    And selecting a trace opens the POST /telemetry server span in Grafana's
      trace view

  Scenario: The phase proof-of-work passes
    Given a clean "docker compose up -d --wait" with k6 running and time for
      data to flow
    Then "http://localhost:3000/api/health" answers 200
    And Tempo's search API returns at least one trace for
      "service.name=ingestion-api"
    And Grafana's dashboard search for "Ingestion API" returns at least one
      result

  Scenario: The change touches only dashboard provisioning, not the rest
    Given this is the final change of the ingestion-trace-backbone phase
    Then it adds only the dashboard JSON (and any wiring strictly needed to
      provision it) under "docker/grafana/"
    And it does not change the backbone services, the datasource/provider
      provisioning, or the ingestion instrumentation
    And the separate "docker-compose.test.yml" harness is left untouched and the
      existing pytest/vitest suites still pass
