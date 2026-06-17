Feature: Provision the "Primary/Replica Streaming" Grafana dashboard
  As an operator of the fleet telemetry system
  I want a "Primary/Replica Streaming" dashboard auto-provisioned into Grafana
  that visualizes the live streaming-replication lag (bytes and seconds) the
  replication probe emits for the primary "db" and the hot-standby "replica"
  So that, from a clean `docker compose up`, I can watch how far the read-path
  standby trails the primary without importing anything by hand

  # Vertical-slice scope: this change owns ONLY the "Primary/Replica Streaming"
  # dashboard JSON and any wiring strictly needed to provision it. It does NOT
  # touch the replication probe / metric (owned by "replication-lag-metric",
  # already complete), the datasource/provider provisioning, or the browser SDK
  # and its dashboard. It is the smallest change that turns the already-flowing
  # replication-lag series into the visible dashboard the phase goal requires.

  Background:
    Given the Grafana provisioning from "grafana-provisioning" is in place, with
      a Tempo datasource at uid "tempo" and a Prometheus datasource at uid
      "prometheus", and a file-based dashboard provider that auto-loads any JSON
      dropped into "docker/grafana/dashboards" (mounted read-only at
      "/var/lib/grafana/dashboards")
    And the change "replication-lag-metric" already emits, when an OTLP endpoint
      is configured, the Prometheus series "pg_replication_lag_bytes" (primary
      view, WAL bytes a standby has not yet replayed) and
      "pg_replication_lag_seconds" (replica view, seconds the last replayed
      transaction trails now()), each carrying a per-standby label
    And no upstream change has authored a "Primary/Replica Streaming" dashboard
      JSON yet

  Scenario: The dashboard JSON is dropped into the provisioned dashboards folder
    Given the provisioned dashboards directory "docker/grafana/dashboards"
    When the "Primary/Replica Streaming" dashboard JSON file is added to that
      directory
    Then the file is a valid Grafana dashboard model with title
      "Primary/Replica Streaming"
    And it requires no manual import — the provider auto-loads it on startup
    And it is committed to the repo so a clean `docker compose up` includes it

  Scenario: The dashboard binds to the provisioned Prometheus datasource by uid
    Given the "Primary/Replica Streaming" dashboard JSON
    Then its panels reference the Prometheus datasource by uid "prometheus"
    And no panel hard-codes a Grafana auto-generated datasource id, so the
      dashboard survives a `docker compose down/up`

  Scenario: Grafana auto-provisions the dashboard on a clean startup
    Given the observability stack is started with "docker compose up -d --wait"
    When Grafana finishes provisioning
    Then a dashboard search for "Primary/Replica Streaming" returns at least one
      result
    And Grafana's startup logs report no dashboard provisioning errors for it

  Scenario: A byte-lag panel shows live WAL replay lag
    Given the stack is up and the replication probe is sampling
    Then the dashboard has a panel showing replication byte lag over time
    And that panel queries the Prometheus series "pg_replication_lag_bytes"
    And the panel is broken down by, or filterable on, the per-standby label so
      each connected standby is distinguishable

  Scenario: A seconds-lag panel shows how far behind in wall-clock time
    Given the stack is up and the replication probe is sampling
    Then the dashboard has a panel showing replication lag in seconds over time
    And that panel queries the Prometheus series "pg_replication_lag_seconds"

  Scenario: A current-lag stat panel surfaces the headline numbers
    Given the stack is up and the replication probe is sampling
    Then the dashboard has a single-stat (or gauge) panel showing the current
      maximum byte lag and/or seconds lag across standbys
    And it uses "max(pg_replication_lag_bytes)" and/or
      "max(pg_replication_lag_seconds)" so the headline matches the phase proof

  Scenario: The exact Prometheus series names are confirmed empirically
    Given the OTel-to-Prometheus name normalization can rewrite metric names
    When the panel queries are authored
    Then the series names bound by the panels are confirmed against the running
      Prometheus ("/api/v1/label/__name__/values" or "/api/v1/series"), not
      assumed, and the panels read live data once the probe has sampled

  Scenario: The phase byte-lag proof-of-work passes
    Given a clean "docker compose up -d --wait" with time for the probe to sample
    Then querying Prometheus with "max(pg_replication_lag_bytes)" returns a
      non-null value
    And Grafana's dashboard search for "Primary/Replica Streaming" returns at
      least one result

  Scenario: The change touches only dashboard provisioning, not the rest
    Given this change owns only the replication dashboard view
    Then it adds only the dashboard JSON (and any wiring strictly needed to
      provision it) under "docker/grafana/"
    And it does not change the replication probe, the datasource/provider
      provisioning, or any service instrumentation
    And the separate "docker-compose.test.yml" harness is left untouched and the
      existing pytest/vitest suites still pass
