Feature: Observability backbone in docker-compose
  As an operator of the fleet telemetry system
  I want the Alloy -> Tempo + Prometheus + Grafana backbone to come up with one
  `docker compose up`
  So that the downstream changes have a running OTLP pipeline and Grafana to
  provision datasources and dashboards against

  Background:
    Given the runtime stack defined in the repo-root "docker-compose.yml"
    And the existing services (db, replica, redis, migrate, cdc, ingestion,
      frontend, dashboard, k6) remain present and unchanged in behavior

  Scenario: The four observability services come up healthy
    When the stack is started with "docker compose up -d --wait"
    Then a "tempo" service is running and reports healthy
    And a "prometheus" service is running and reports healthy
    And a "grafana" service is running and reports healthy
    And an "alloy" service is running and reports healthy

  Scenario: Grafana answers on its host port
    Given the stack is up
    When a request is made to "http://localhost:3000/api/health"
    Then the response status is 200

  Scenario: Tempo answers on its host port
    Given the stack is up
    When a request is made to Tempo's status/ready endpoint on "http://localhost:3200"
    Then Tempo reports ready

  Scenario: Prometheus answers on its host port
    Given the stack is up
    When a request is made to "http://localhost:9090/-/ready"
    Then the response status is 200

  Scenario: Alloy receives OTLP and routes it to Tempo and Prometheus
    Given the stack is up
    And Alloy exposes an OTLP receiver on the gRPC (4317) and HTTP (4318) ports
    When a single OTLP trace is sent to Alloy's OTLP/HTTP endpoint
    Then the trace becomes queryable in Tempo
    And Alloy is configured to forward OTLP metrics to Prometheus via remote write

  Scenario: Prometheus is reachable as a remote-write sink and scrapes the pipeline
    Given the stack is up
    Then Prometheus accepts remote-write samples from Alloy
    And Prometheus scrapes its own and Alloy's internal metrics endpoints

  Scenario: Configuration is environment-driven, not hard-coded
    Given the observability services
    Then every cross-service endpoint (OTLP target, Tempo, Prometheus, Grafana)
      is resolved over the compose network or from the environment
    And no credentials are baked into application source
    And host port mappings are overridable via environment variables

  Scenario: The pytest/vitest harness is left untouched
    Given the separate "docker-compose.test.yml" harness
    When the observability backbone is added to the runtime compose file
    Then "docker-compose.test.yml" gains no observability services
    And the existing test suites still pass
