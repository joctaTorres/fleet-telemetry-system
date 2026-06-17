Feature: Provision the "CDC Consumer", "Pub/Sub", and "Redis Fan-out" dashboards
  As an operator of the fleet telemetry system
  I want three Grafana dashboards auto-provisioned that visualize the
  asynchronous critical path (CDC decode/publish -> Redis pub/sub -> frontend
  fan-out), bound to the live cdc-consumer and frontend-api telemetry
  So that, from a clean `docker compose up` with k6 driving telemetry writes, I
  can watch event throughput, decode lag, publish/subscribe counts, fan-out
  delivery, and active/dropped clients populate from live data — and follow the
  single connected Tempo trace that already spans cdc-consumer and frontend-api
  — without importing anything by hand

  Background:
    Given the Grafana provisioning from "grafana-provisioning" is in place, with
      a Tempo datasource at uid "tempo" and a Prometheus datasource at uid
      "prometheus", and a file-based dashboard provider that auto-loads any JSON
      dropped into "docker/grafana/dashboards" (mounted read-only at
      "/var/lib/grafana/dashboards")
    And the instrumentation from "instrument-cdc-consumer" emits, when an OTLP
      endpoint is configured, "service.name=cdc-consumer" spans ("cdc.decode" and
      a per-event "cdc.publish"), a "cdc.events_published" counter and a
      "cdc.decode.lag" (ms) histogram, each keyed by a "cdc.event_type"
      attribute
    And the instrumentation already present in "app.frontend_api" emits
      "service.name=frontend-api" spans ("redis.subscribe" and "ws.broadcast"),
      a "frontend.ws.broadcasts" counter, and a "frontend.ws.active_connections"
      observable gauge
    And the trace context flows across the "fleet:events" channel
      ("propagate-trace-context-redis"), so a single k6 telemetry write yields
      ONE Tempo trace whose spans include BOTH cdc-consumer and frontend-api
    And no upstream change has authored the "CDC Consumer", "Pub/Sub", or
      "Redis Fan-out" dashboard JSON

  Scenario: The three dashboard JSON files are dropped into the provisioned folder
    Given the provisioned dashboards directory "docker/grafana/dashboards"
    When the "CDC Consumer", "Pub/Sub", and "Redis Fan-out" dashboard JSON files
      are added to that directory
    Then each is a valid Grafana dashboard model with title exactly
      "CDC Consumer", "Pub/Sub", and "Redis Fan-out" respectively
    And they require no manual import — the provider auto-loads them on startup
    And they are committed to the repo so a clean `docker compose up` includes them

  Scenario: Every panel binds to the provisioned datasources by fixed uid
    Given the three dashboards
    Then every metric panel references the Prometheus datasource by uid "prometheus"
    And every trace panel references the Tempo datasource by uid "tempo"
    And no panel hard-codes a Grafana auto-generated datasource id, so the
      dashboards survive a `docker compose down/up`

  Scenario: Grafana auto-provisions all three dashboards on a clean startup
    Given the observability stack is started with "docker compose up -d --wait"
    When Grafana finishes provisioning
    Then a dashboard search for "CDC Consumer" returns at least one result
    And a dashboard search for "Pub/Sub" returns at least one result
    And a dashboard search for "Redis Fan-out" returns at least one result
    And Grafana's startup logs report no dashboard provisioning errors for them

  Scenario: The "CDC Consumer" dashboard shows decode throughput and lag
    Given the stack is up and k6 is driving telemetry writes the consumer decodes
    Then the "CDC Consumer" dashboard has a panel showing CDC event throughput
      over time, querying the Prometheus series derived from
      "cdc.events_published" using a rate over the counter, broken down by or
      filterable on "cdc.event_type"
    And it has a panel showing decode lag, querying the Prometheus histogram
      derived from "cdc.decode.lag" (milliseconds) and rendering at least one
      quantile (for example p95) over the buckets
    And it has a Tempo-backed panel listing recent "service.name=cdc-consumer"
      traces, drillable into the "cdc.decode" / "cdc.publish" spans

  Scenario: The "Pub/Sub" dashboard shows publish and subscribe counts
    Given the stack is up and k6 is driving telemetry writes
    Then the "Pub/Sub" dashboard has a panel showing the publish rate to the
      "fleet:events" channel from the "cdc.events_published" series
    And it has a panel showing the subscribe / delivery rate on the frontend side
      from the "frontend.ws.broadcasts" series
    And it has a Tempo-backed panel surfacing the connected trace whose spans
      include BOTH "service.name=cdc-consumer" and "service.name=frontend-api",
      so the pub/sub hop is visible as one trace

  Scenario: The "Redis Fan-out" dashboard shows fan-out delivery and clients
    Given the stack is up, k6 is driving telemetry writes, and WebSocket clients
      are connected to "/ws"
    Then the "Redis Fan-out" dashboard has a panel showing fan-out (broadcast)
      delivery rate from the "frontend.ws.broadcasts" series
    And it has a panel showing the number of active WebSocket connections from the
      "frontend.ws.active_connections" gauge
    And it surfaces dropped clients (best-effort, derived from the
      active-connections gauge), since there is no dedicated dropped-client
      counter to bind to

  Scenario: Prometheus series names are read empirically, not guessed
    Given OTLP -> Alloy -> Prometheus remote-write rewrites OTel metric names
      (dots to underscores, counter "_total" suffix, histogram
      "_bucket"/"_sum"/"_count")
    When the panel queries are authored
    Then the exact Prometheus series names for "cdc.events_published",
      "cdc.decode.lag", "frontend.ws.broadcasts", and
      "frontend.ws.active_connections" are read from the running Prometheus
      ("/api/v1/label/__name__/values" or "/api/v1/series") and wired into the
      queries, rather than assumed

  Scenario: The phase proof-of-work passes end to end
    Given a clean "docker compose up -d --wait" with k6 running and time for data
      to flow
    Then Tempo's search API returns at least one trace whose spans include BOTH
      "resource.service.name=cdc-consumer" and "resource.service.name=frontend-api"
    And Grafana's dashboard search returns at least one result each for
      "CDC Consumer", "Pub/Sub", and "Redis Fan-out"
    And each dashboard's panels populate with live data

  Scenario: The change touches only dashboard provisioning, not the rest
    Given this is the final change of the cdc-pubsub-redis-flow phase
    Then it adds only the three dashboard JSON files (and any wiring strictly
      needed to provision them) under "docker/grafana/"
    And it does not change the cdc-consumer or frontend instrumentation, the
      Redis trace-context propagation, the datasource/provider provisioning, or
      the backbone services
    And the separate "docker-compose.test.yml" harness is left untouched and the
      existing pytest/vitest suites still pass
