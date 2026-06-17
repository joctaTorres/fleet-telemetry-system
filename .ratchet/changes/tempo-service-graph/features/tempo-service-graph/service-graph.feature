Feature: Generate a Tempo service graph covering all critical fleet flows
  As an operator of the fleet telemetry system
  I want Tempo to derive service-graph metrics from the traces it already
  receives and remote-write them into Prometheus, with the Tempo datasource
  wired to render that graph in Grafana
  So that, from a clean `docker compose up` with k6 driving load, the
  "traces_service_graph_request_total" series exist and connect every critical
  flow (browser -> frontend-api, ingestion-api -> storage, and the
  cdc-consumer -> frontend-api pub/sub hop) — making all flows visible as one
  service graph with zero manual wiring

  Background:
    Given the observability backbone from earlier phases is in place: every
      instrumented service exports OTLP to Alloy, Alloy forwards traces to Tempo
      (grafana/tempo:2.7.2, single-binary, local storage) over OTLP/HTTP and
      remote-writes metrics to Prometheus
    And Prometheus is started with "--web.enable-remote-write-receiver", so any
      component can push samples to "http://prometheus:9090/api/v1/write"
    And Grafana provisioning declares a Tempo datasource at uid "tempo" and a
      Prometheus datasource at uid "prometheus"
    And the trace context already flows end to end: a single k6-driven telemetry
      write yields connected traces spanning ingestion-api, cdc-consumer and
      frontend-api, and the browser SDK (service.name=fleet-dashboard-web) emits
      connected spans against frontend-api
    And, before this change, Tempo runs receive-and-store only — no
      metrics_generator is configured, so no service-graph metrics exist

  Scenario: Tempo's metrics_generator is enabled with the service-graphs processor
    Given the Tempo config "docker/tempo/tempo.yaml"
    When the metrics_generator is configured
    Then it enables the "service-graphs" processor
    And it is given a local WAL/storage path for the generator
    And it remote-writes the generated series to
      "http://prometheus:9090/api/v1/write"
    And the overrides enable the "service-graphs" processor by default so the
      distributor feeds received traces to the generator
    And the Tempo image stays pinned to "grafana/tempo:2.7.2" (the 3.0 distroless
      image has no shell for the compose healthcheck and changed config keys)

  Scenario: Service-graph request metrics appear in Prometheus after load
    Given the stack is started with "docker compose up -d --wait" and k6 drives
      load for long enough for traces to flow and the generator to emit
    When Prometheus is queried for "traces_service_graph_request_total"
    Then the query returns at least one series
    And each series carries "client" and "server" labels identifying the two
      services on that edge

  Scenario: The service graph connects every critical flow
    Given "traces_service_graph_request_total" is populated
    Then the set of "client"/"server" edges covers the critical flows, including
      the browser -> frontend-api edge (client "fleet-dashboard-web",
      server "frontend-api") and the cdc-consumer -> frontend-api pub/sub hop
    And the exact service-name label values and the full edge set are read
      empirically from the running Prometheus rather than assumed, since they
      derive from the resource "service.name" on the live spans

  Scenario: The Tempo datasource renders the service graph in Grafana
    Given the provisioned Tempo datasource at uid "tempo"
    When its service-map configuration is wired to the Prometheus datasource
    Then the Tempo datasource's "serviceMap" points at the Prometheus datasource
      (uid "prometheus") so Grafana's Explore service-graph / node-graph view
      resolves the "traces_service_graph_*" series
    And this is the only datasource-provisioning change required — no panel or
      dashboard JSON is authored here (the Overview node-graph panel is owned by
      the downstream "fleet-overview-dashboard" change)

  Scenario: The service-graph generator is preloaded by a clean compose up
    Given a fresh checkout
    When "docker compose up -d --wait" is run with no manual steps
    Then Tempo starts the metrics_generator from its mounted config and begins
      remote-writing service-graph metrics once traces arrive
    And Tempo reports ready on its HTTP API (port 3200) with no fatal config or
      generator errors in its startup logs
    And nothing requires manual import or post-up wiring

  Scenario: The stack stays healthy with the generator running under load
    Given the metrics_generator adds memory pressure to Tempo
    Then the generator runs within the available Docker VM memory (now 4 GiB)
      without OOM-killing Tempo under k6 load
    And any memory-bounding generator settings applied are noted so the capstone
      clean-boot verify can rely on a healthy Tempo

  Scenario: Span kinds are corrected so all critical flows render as edges
    Given Tempo's service-graphs processor only builds edges from client/server
      or producer/consumer span pairs, and the connected end-to-end traces from
      earlier phases carry SPAN_KIND_INTERNAL spans across the async and db hops
    When the minimal, semantic-convention-correct span kinds are applied
    Then the "cdc.publish" span is SPAN_KIND_PRODUCER and the "redis.subscribe"
      span is SPAN_KIND_CONSUMER (both keeping their "messaging.*" attributes),
      so Tempo renders the cdc-consumer -> frontend-api pub/sub edge
    And the ingestion Postgres write is wrapped in a SPAN_KIND_CLIENT span with
      "db.system=postgresql" and "server.address=db", and the frontend
      "replica.read" span is SPAN_KIND_CLIENT with "server.address=replica", so
      the ingestion-api -> db and frontend-api -> replica edges form
    And the trace parent/child structure is unchanged, so the phase-3 connected
      trace ({cdc-consumer} && {frontend-api}) still returns at least one trace

  Scenario: The change touches only the trace backend and span kinds, not pipelines
    Given this is the first change of the all-dashboards-preloaded-capstone phase
    Then it modifies only Tempo config ("docker/tempo/tempo.yaml"), the minimal
      Tempo-datasource service-map wiring, and the minimal span-kind corrections
      needed for the service-graphs processor to draw every critical-flow edge
    And it does not change the Alloy pipeline, the Prometheus scrape config, or
      any dashboard JSON
    And the separate "docker-compose.test.yml" harness is left untouched and the
      existing pytest/vitest suites still pass
