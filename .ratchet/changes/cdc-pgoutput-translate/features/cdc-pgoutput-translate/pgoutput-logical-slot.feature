Feature: Tap the primary's WAL through a single pgoutput logical slot
  As the CDC tier of the telemetry system
  I want exactly one consumer to read a pgoutput logical replication slot bound
  to a publication over the three watched tables, decode the binary protocol,
  and confirm processed WAL back to the primary
  So that the event stream is sourced from logical decoding (not LISTEN/NOTIFY,
  not dual-write, not replica polling), stays a single-reader construct, and
  cannot retain WAL unboundedly (ADR-0001 D3; telemetry-architecture standard)

  Background:
    Given the Postgres primary from docker-compose.test.yml has wal_level=logical
      and free replication slots
    And a publication exists naming exactly the three watched tables:
      vehicle_current_state, anomalies, zone_counts

  Scenario: The publication covers only the watched tables
    When the publication's member tables are listed
    Then they are exactly vehicle_current_state, anomalies and zone_counts
    And no other table (raw_events, missions, maintenance_records, vehicles,
      schema_migrations) is a member

  Scenario: The slot uses the pgoutput plugin and the replication protocol
    Given the consumer ensures its logical slot exists on startup
    Then the slot's output plugin is "pgoutput" (the built-in binary logical
      decoding output), not test_decoding
    And the consumer reads it over the streaming replication protocol with the
      publication name and a pgoutput protocol version, receiving binary
      Begin/Relation/Insert/Update/Commit messages

  Scenario: Exactly one consumer reads the slot
    Given the consumer is attached to the slot and streaming
    When a second reader attempts to start replication on the same slot
    Then it is refused because the slot is already active for one reader
    And the system relies on a single CDC consumer, never two active readers

  Scenario: The consumer confirms processed WAL so the slot does not grow unbounded
    Given the consumer has decoded and published events up to a WAL position
    When it sends a standby status update acknowledging that position
    Then the slot's confirmed-flush position advances to at least that point
    And WAL up to the acknowledged position is no longer retained by the slot
