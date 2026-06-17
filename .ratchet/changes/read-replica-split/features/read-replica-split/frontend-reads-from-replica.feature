Feature: Serve the frontend read surface from the read replica
  As the dashboard (a read-only client of the frontend API)
  I want the frontend's connect snapshot and REST reads served from the
  streaming read replica via a dedicated replica connection pool
  So that connect-time and query read load is isolated from the primary's write
  path, while the ingestion write path keeps using the primary unchanged — the
  primary/replica split the telemetry-architecture standard mandates

  Background:
    Given a process-wide replica connection pool built from REPLICA_URL,
      separate from the primary pool built from DATABASE_URL
    And the persistence read seams (aggregate_fleet_state, zone_entry_counts,
      recent_anomalies) accept a connection factory, defaulting to the primary
    And the frontend API passes the replica connection factory for the reads it
      serves to the dashboard

  Scenario: The connect snapshot is read from the replica
    Given fleet and zone state has been committed on the primary
    And the replica has caught up to the primary
    When a client connects and the one-shot snapshot is built
    Then the snapshot's fleet counts and per-zone counts are read through the
      replica connection pool, not the primary

  Scenario: A write to the primary is reflected in a later snapshot from the replica
    Given a client connects and receives an initial snapshot from the replica
    When a vehicle transitions to a new status, committed on the primary
    And the replica catches up to the primary
    Then a freshly built snapshot read from the replica reflects the new status

  Scenario: The frontend REST reads are served from the replica
    Given fleet, zone, and anomaly state has been committed on the primary
    And the replica has caught up
    When GET /fleet/state, GET /zones/counts, and GET /anomalies are called
    Then each result is derived through the replica connection pool
    And each result reflects the committed state present on the replica

  Scenario: The ingestion write path still targets the primary
    Given the stateless ingestion API handles POST /telemetry
    When a telemetry event is ingested
    Then the upsert and any zone/anomaly writes go to the primary via the
      primary pool
    And the read replica is never written to by the application

  Scenario: The frontend holds no authoritative state
    Given a client is connected to the frontend API
    When its snapshot or any REST read is built
    Then the value is derived fresh from the replica on each call
    And the frontend retains no in-process authoritative fleet state, so any
      frontend instance can serve any client
