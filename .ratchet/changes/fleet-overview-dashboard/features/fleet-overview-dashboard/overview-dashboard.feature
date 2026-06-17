Feature: Provision the "Fleet Observability Overview" top-level dashboard
  As an operator of the fleet telemetry system
  I want a single "Fleet Observability Overview" dashboard auto-provisioned into
  Grafana that links to every per-flow dashboard and renders a Tempo-derived
  service-graph view of all critical flows, populated with live k6-driven data
  So that, from a clean `docker compose up`, I have one landing page that ties
  the whole system together and lets me jump into any flow — without importing,
  wiring, or arranging anything by hand

  # Vertical-slice scope: this change owns ONLY the "Fleet Observability
  # Overview" dashboard JSON (the 8th and final required dashboard) and any
  # wiring strictly needed to provision it. It does NOT touch the seven per-flow
  # dashboards already on disk, the Tempo metrics_generator / service-graph
  # config (owned by "tempo-service-graph", already complete), the datasource or
  # provider provisioning, or any service instrumentation. It is the smallest
  # change that turns the already-flowing per-flow metrics and the already-
  # generated `traces_service_graph_*` series into the single overview page the
  # capstone phase goal requires. The full judged clean-boot end-goal acceptance
  # is owned by the downstream "capstone-clean-boot-verify" change.

  Background:
    Given the Grafana provisioning from "grafana-provisioning" is in place, with
      a Tempo datasource at uid "tempo" (whose service map is bound to the
      Prometheus datasource by "tempo-service-graph") and a Prometheus datasource
      at uid "prometheus", and a file-based dashboard provider that auto-loads
      any JSON dropped into "docker/grafana/dashboards" (mounted read-only at
      "/var/lib/grafana/dashboards")
    And the seven per-flow dashboards already exist on disk and auto-provision:
      "Ingestion API", "Frontend API & WebSockets", "CDC Consumer", "Pub/Sub",
      "Redis Fan-out", "Primary/Replica Streaming", and "Frontend Web (Browser)"
    And the change "tempo-service-graph" already makes Tempo emit the Prometheus
      series "traces_service_graph_request_total" covering the critical flows
      (ingestion-api -> db, cdc-consumer -> frontend-api over Redis pub/sub,
      frontend-api -> replica, and fleet-dashboard-web -> frontend-api)
    And no upstream change has authored a "Fleet Observability Overview"
      dashboard JSON yet

  Scenario: The overview dashboard JSON is dropped into the provisioned folder
    Given the provisioned dashboards directory "docker/grafana/dashboards"
    When the "Fleet Observability Overview" dashboard JSON file is added to that
      directory
    Then the file is a valid Grafana dashboard model with title exactly
      "Fleet Observability Overview"
    And it requires no manual import — the provider auto-loads it on startup
    And it is committed to the repo so a clean `docker compose up` includes it

  Scenario: Grafana auto-provisions all eight dashboards on a clean startup
    Given the observability stack is started with "docker compose up -d --wait"
    When Grafana finishes provisioning
    Then "GET /api/search?type=dash-db" returns all eight dashboard titles,
      including "Fleet Observability Overview"
    And Grafana's startup logs report no dashboard provisioning errors for it

  Scenario: The overview links to every per-flow dashboard
    Given the "Fleet Observability Overview" dashboard JSON
    Then it surfaces a navigable link to each of the seven per-flow dashboards
      ("Ingestion API", "Frontend API & WebSockets", "CDC Consumer", "Pub/Sub",
      "Redis Fan-out", "Primary/Replica Streaming", "Frontend Web (Browser)")
    And the links resolve to the provisioned dashboards (by uid or by a shared
      tag), so an operator can jump from the overview into any flow with no
      manual setup

  Scenario: The overview renders a Tempo service-graph view of the critical flows
    Given the stack is up and k6 has driven load long enough for traces to flow
    Then the dashboard has a panel that renders the service graph of all critical
      flows from the Tempo-generated "traces_service_graph_*" series (a node-graph
      panel, or an equivalent panel querying "traces_service_graph_request_total")
    And the rendered graph shows the ingestion, cdc-consumer/Redis pub/sub,
      replica-read, and browser -> frontend-api edges connected end to end

  Scenario: The overview shows live top-level health for each flow
    Given the stack is up and k6 is driving load and the SPA has been loaded once
    Then the dashboard surfaces at-a-glance, live-updating signals spanning the
      flows (for example ingestion throughput, websocket connections, CDC publish
      rate, Redis fan-out, and replication lag) sourced from the existing
      Prometheus series
    And every panel binds its datasource by uid ("prometheus" for metric panels,
      "tempo" where a trace/service-map source is used), so the dashboard
      survives a `docker compose down/up`

  Scenario: The series and datasource bindings are confirmed empirically
    Given the OTel-to-Prometheus name normalization can rewrite metric names and
      the service-graph label values are produced at runtime
    When the overview panel queries are authored
    Then the series names and service-graph labels bound by the panels are
      confirmed against the running Prometheus
      ("/api/v1/label/__name__/values" or "/api/v1/series"), not assumed, and the
      panels read live data once k6 has driven load and the SPA has loaded

  Scenario: The change touches only the overview dashboard, not the rest
    Given this change owns only the top-level overview view
    Then it adds only the "Fleet Observability Overview" dashboard JSON (and any
      wiring strictly needed to provision it) under "docker/grafana/"
    And it does not change the seven per-flow dashboards, the Tempo
      metrics_generator / service-graph config, the datasource/provider
      provisioning, or any service instrumentation
    And the separate "docker-compose.test.yml" harness is left untouched and the
      existing pytest/vitest suites still pass
