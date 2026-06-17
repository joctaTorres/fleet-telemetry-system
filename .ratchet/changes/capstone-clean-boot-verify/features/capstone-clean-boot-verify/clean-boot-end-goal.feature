Feature: A clean docker compose up delivers the whole observability end goal
  As the operator who owns the fleet telemetry system
  I want a single, fresh `docker compose up` to bring the entire stack with ALL
  required dashboards preloaded and real-time OTel data flowing from the k6
  fleet simulation — every per-flow dashboard, the top-level Fleet Observability
  Overview, the Tempo service graph of all critical flows, and end-to-end
  traceability
  So that the phase end goal is proven as one experience: nothing requires
  manual import or post-up wiring, and the whole thing is demonstrably alive
  from a clean state

  Background:
    Given every prior change of the all-dashboards-preloaded-capstone phase is
      complete: the seven per-flow dashboards (Ingestion API, Frontend API &
      WebSockets, CDC Consumer, Pub/Sub, Redis Fan-out, Primary/Replica
      Streaming, Frontend Web (Browser)) auto-provision from
      "docker/grafana/dashboards/"
    And the upstream "tempo-service-graph" change has Tempo's metrics_generator
      emitting "traces_service_graph_request_total" over the critical flows with
      the Tempo datasource service map wired to Prometheus
    And the upstream "fleet-overview-dashboard" change has authored the
      "Fleet Observability Overview" dashboard JSON as the eighth dashboard
    And the Docker VM is now 4 GiB so the full stack plus k6 plus the Tempo
      metrics_generator can stay healthy at once
    And this is the final capstone change: it runs the judged clean-boot
      end-goal acceptance and closes only the gaps that acceptance reveals — it
      does not re-author dashboards or re-instrument services unless the clean
      boot proves a gap

  Scenario: A fresh clean boot brings the whole stack up healthy with no manual steps
    Given a truly clean state produced by "docker compose down -v"
    When "docker compose up -d --wait" is run with no manual steps
    Then every service reaches a healthy/ready state within the compose wait
    And no service requires a post-up import, restart, or hand-wiring step
    And Grafana, Prometheus, Tempo, and the app services report ready on their
      health endpoints

  Scenario: All eight required dashboards are auto-preloaded
    Given the stack is up from the clean boot with no manual steps
    When Grafana is queried at "GET /api/search?type=dash-db"
    Then the result contains exactly these eight titles: "Ingestion API",
      "Frontend API & WebSockets", "CDC Consumer", "Pub/Sub", "Redis Fan-out",
      "Primary/Replica Streaming", "Frontend Web (Browser)", and
      "Fleet Observability Overview"
    And none of them was manually imported — each came from provisioning on boot

  Scenario: Every dashboard populates with live k6-driven data
    Given the eight dashboards are preloaded
    When k6 drives the fleet simulation for about 90 seconds and the SPA is
      loaded once in a headless browser
    Then each dashboard's panels return non-empty data for the load window
    And the Frontend Web (Browser) dashboard shows browser-sourced telemetry
      from the single SPA load
    And the Fleet Observability Overview shows live top-level health signals and
      resolves its links to every per-flow dashboard

  Scenario: The Tempo service graph covers all critical flows
    Given k6 has driven load and the SPA has been loaded once
    When Prometheus is queried for "traces_service_graph_request_total"
    Then the edge set covers all critical flows — including
      ingestion-api -> db, the cdc-consumer -> frontend-api Redis pub/sub hop,
      frontend-api -> replica, and fleet-dashboard-web -> frontend-api
    And the actual "client"/"server" label values are read empirically from the
      running Prometheus, not assumed

  Scenario: Every flow is traceable end to end in Tempo
    Given traces have flowed from the k6 load and the single SPA load
    When Tempo is searched for connected traces
    Then there is a connected trace spanning ingestion-api, cdc-consumer, Redis,
      and frontend-api for a single telemetry write
    And there is a connected browser trace (service.name fleet-dashboard-web)
      against frontend-api from the SPA load
    And these confirm the critical flows are traceable end to end with zero
      manual setup

  Scenario: The judge confirms the end goal holds with zero manual setup
    Given the clean-boot evidence is gathered: the dashboard list, each
      dashboard's panel data, the service-graph metrics, the connected
      ingestion/cdc/redis/frontend and browser traces, and dashboard screenshots
    When an llm-judge assesses whether the phase end goal holds
    Then the judge confirms all eight dashboards are auto-preloaded and showing
      real-time k6-driven data
    And the judge confirms the Tempo service graph covers all critical flows
    And the judge confirms all flows are traceable end to end
    And the judge confirms there was zero manual setup beyond
      "docker compose up"

  Scenario: The capstone closes only the gaps the clean boot reveals
    Given the clean-boot acceptance is the source of truth for the end goal
    When any gap surfaces (a dashboard not populating, a missing service-graph
      edge, a broken link, a flow not traceable, or a service not ready)
    Then the capstone applies the minimal fix needed for the clean boot to pass
      and records it, rather than re-authoring the upstream changes
    And if the clean boot already passes, no code change is required and the
      capstone records the gathered evidence as the proof
    And the separate "docker-compose.test.yml" harness is left untouched and the
      existing pytest/vitest suites still pass
