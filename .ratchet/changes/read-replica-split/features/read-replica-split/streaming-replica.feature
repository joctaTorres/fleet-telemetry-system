Feature: A streaming physical read replica trailing the primary
  As the fleet telemetry system
  I want a hot-standby Postgres that streams physically from the primary and
  accepts reads but not writes
  So that the frontend's read surface can be served off the standby — isolating
  read load from the write path — while a committed write on the primary still
  becomes visible on the replica after a small replication lag (ADR-0001, D1/D5)

  Background:
    Given the test topology from docker-compose.test.yml runs a Postgres
      primary configured for physical replication (a replication-capable
      pg_hba rule, enough wal senders and replication slots)
    And a second Postgres bootstrapped via pg_basebackup that then runs as a
      hot standby streaming from the primary
    And both DSNs are read from the environment: DATABASE_URL for the primary
      and REPLICA_URL for the replica, with no connection string hard-coded

  Scenario: The replica is a streaming standby that has replayed the schema
    Given the primary has been migrated to the current schema
    When the replica has finished bootstrapping
    Then the replica reports that it is in recovery (a hot standby)
    And the migrated tables (vehicle_current_state, zone_counts, anomalies)
      exist on the replica because they streamed over from the primary

  Scenario: A committed write on the primary becomes visible on the replica
    Given a vehicle's state has been committed on the primary
    When the replica has replayed up to the primary's current WAL position
    Then reading that vehicle's state from the replica returns the committed
      values

  Scenario: An uncommitted write on the primary is not visible on the replica
    Given a write against the primary is opened in a transaction and not yet
      committed
    When the replica is read
    Then the replica does not reflect the uncommitted write
    And only after the primary transaction commits and the replica catches up
      does the value appear on the replica

  Scenario: The replica rejects writes
    Given a connection to the replica
    When a write (INSERT/UPDATE) is attempted against it
    Then the replica refuses the write because it is a read-only standby
