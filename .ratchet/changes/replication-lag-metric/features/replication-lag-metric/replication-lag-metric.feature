Feature: Emit a custom OTel streaming-replication-lag metric
  As an operator of the fleet telemetry system
  I want a small Python probe that periodically measures Postgres streaming
  replication lag between the primary ("db") and the hot standby ("replica") and
  emits it as a custom OpenTelemetry metric over OTLP/HTTP to Alloy
  So that, from a clean `docker compose up`, Prometheus exposes a live
  replication-lag time series (bytes and/or seconds) that the downstream
  "Primary/Replica Streaming" dashboard can bind to

  # Vertical-slice scope: this change owns ONLY the probe + the custom metric and
  # its runtime wiring. It does NOT author the "Primary/Replica Streaming"
  # dashboard JSON (that is the follow-on "replication-dashboard"), and it does
  # NOT touch the browser SDK or its dashboard. It proves only that a
  # replication-lag series is queryable in Prometheus.

  Background:
    Given the runtime compose stack runs a Postgres primary "db"
      (wal_level=logical, physical streaming) and a streaming hot-standby
      "replica" bootstrapped via pg_basebackup
    And the shared "app.otel" bootstrap installs global trace/metric providers
      exporting OTLP/HTTP to Alloy when "OTEL_EXPORTER_OTLP_ENDPOINT" is set, and
      is a safe no-op when it is unset
    And Alloy remote-writes OTel metrics to Prometheus, normalizing metric names
      (dots to underscores, applying unit suffixes)

  Scenario: A replication probe process measures lag from primary and replica
    Given a new probe entry point "python -m app.replication_probe"
    When the probe starts
    Then it calls "configure_otel" once with a "replication-probe" service name,
      reading the OTLP endpoint from the environment only (no SDK re-wiring)
    And it obtains a meter from the shared bootstrap and registers the
      replication-lag instrument(s) against it
    And it runs as a long-lived periodic loop, re-measuring on a fixed interval
      without blocking or crashing on a transient query error

  Scenario: Byte lag is computed from the primary's pg_stat_replication
    Given the probe is connected to the primary via "DATABASE_URL"
    When it samples replication state
    Then it reads the standby row(s) from "pg_stat_replication" and computes the
      WAL byte lag as the difference between the primary's current WAL position
      ("pg_current_wal_lsn()") and the standby's "replay_lsn"
      (via "pg_wal_lsn_diff")
    And it records that value on the byte-lag gauge

  Scenario: Time lag is computed from the replica's replay timestamp
    Given the probe is connected to the standby via "REPLICA_URL"
    When it samples replication state
    Then it reads the standby's last replayed transaction timestamp
      ("pg_last_xact_replay_timestamp()") and/or "pg_last_wal_replay_lsn()" on
      the replica
    And it records the seconds-behind value (now() minus the last replay
      timestamp) on the seconds-lag gauge

  Scenario: The metric name surfaces in Prometheus as a bytes series
    Given the OTel-to-Prometheus name normalization (dots to underscores plus
      unit suffix) and the known "_ratio"/unit-suffix pitfall for gauges
    When the byte-lag instrument and its unit are chosen
    Then the resulting Prometheus series is exactly "pg_replication_lag_bytes"
    And the exact emitted series names are confirmed empirically from the running
      Prometheus ("/api/v1/label/__name__/values"), not assumed
    And a seconds-lag series (for example "pg_replication_lag_seconds") is also
      exposed

  Scenario: The probe is wired as a runtime compose service
    Given the runtime "docker-compose.yml"
    When the probe is added as its own service running
      "python -m app.replication_probe"
    Then it receives "DATABASE_URL" (primary) and "REPLICA_URL" (replica) in the
      existing connection-string style
    And it receives "OTEL_EXPORTER_OTLP_ENDPOINT=http://alloy:4318",
      env-overridable
    And it starts only after the "db", "replica", and "alloy" backbone are
      available
    And the separate "docker-compose.test.yml" harness is left untouched

  Scenario: Safe and resilient by default
    Given the probe started with no OTLP endpoint configured
    Then it runs with no exporter installed and nothing raised
    And when the standby is momentarily absent from "pg_stat_replication" (no
      connected standby row), the probe records no byte value for that sample and
      keeps looping rather than crashing

  Scenario: Unit tests cover the probe without a live collector or database
    Given the probe's lag computations are factored so the SQL results can be fed
      from fixtures
    Then a unit test asserts byte lag is computed from primary
      current-WAL / standby replay_lsn inputs
    And a unit test asserts seconds lag is computed from the replica replay
      timestamp
    And a unit test asserts the no-endpoint path installs no exporter and the
      existing pytest suite stays green with no running collector

  Scenario: Proof-of-work — Prometheus exposes the replication-lag series
    Given a clean "docker compose up -d --wait" with the probe running
    When Prometheus is queried with "max(pg_replication_lag_bytes)" after data
      has flowed
    Then the query returns a non-null scalar value
    And this is the byte half of the phase proof-of-work (the
      "Primary/Replica Streaming" dashboard is provisioned by the follow-on
      "replication-dashboard" change)
