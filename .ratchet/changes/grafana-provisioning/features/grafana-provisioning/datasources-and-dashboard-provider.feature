Feature: Grafana provisioning — datasources and dashboard provider
  As an operator of the fleet telemetry system
  I want Grafana to come up with its Tempo and Prometheus datasources and a
  file-based dashboard provider already configured
  So that traces and metrics are queryable out of the box and any dashboard JSON
  dropped into the provisioning folder is auto-loaded with zero manual import

  Background:
    Given the observability backbone from "observability-stack-compose" is running
      (alloy, tempo, prometheus, grafana) on the compose network
    And Grafana is started with provisioning files mounted under its provisioning path
    And no datasource or dashboard is ever imported by hand

  Scenario: The stack comes up with provisioning applied
    When the stack is started with "docker compose up -d --wait"
    Then the "grafana" service reports healthy
    And "http://localhost:3000/api/health" returns status 200

  Scenario: The Tempo datasource is provisioned and healthy
    Given the stack is up
    When the provisioned datasources are listed via "GET /api/datasources"
    Then a datasource of type "tempo" exists with a stable uid
    And its URL targets Tempo over the compose network (the Tempo HTTP API)
    And a datasource health check against it succeeds

  Scenario: The Prometheus datasource is provisioned and healthy
    Given the stack is up
    When the provisioned datasources are listed via "GET /api/datasources"
    Then a datasource of type "prometheus" exists with a stable uid
    And it is marked as the default datasource
    And its URL targets Prometheus over the compose network
    And a datasource health check against it succeeds

  Scenario: A file-based dashboard provider is registered
    Given the stack is up
    Then Grafana has a dashboards provider that loads JSON from a mounted folder
    And the provider points at the dashboards directory that downstream changes
      (e.g. "ingestion-dashboard") will drop dashboard JSON into
    And Grafana logs no datasource or dashboard provisioning errors on startup

  Scenario: Datasource uids are stable so dashboards can reference them
    Given the provisioned datasources
    Then the Tempo datasource exposes a fixed, well-known uid
    And the Prometheus datasource exposes a fixed, well-known uid
    So that dashboard JSON can bind panels to those datasources by uid

  Scenario: Provisioning is config-driven and contains no secrets
    Given the provisioning files committed under "docker/grafana/"
    Then every datasource URL resolves over the compose network or from the environment
    And no credentials are baked into the provisioning files
    And the provisioning files are mounted read-only into the grafana container

  Scenario: The test harness is left untouched
    Given the separate "docker-compose.test.yml" harness
    When Grafana provisioning is wired into the runtime compose file
    Then "docker-compose.test.yml" gains no Grafana provisioning
    And the existing pytest/vitest suites still pass
