Feature: Atomically count zone entries inside the ingest transaction
  As the telemetry system
  I want each telemetry event that carries a non-null zone_entered to increment that
  zone's counter with a single server-side atomic UPDATE
  So that under a burst of concurrent entries to the same zone (shift-change
  convergence) every single entry is counted, with zero lost updates, and counters
  never diverge from committed state

  Background:
    Given the frontend/persistence layer runs against a migrated, seeded Postgres
    And the telemetry event model carries an optional "zone_entered" zone id that
      defaults to null
    And concurrency correctness is enforced in the database, per the
      telemetry-architecture standard

  Scenario: A zone entry increments exactly that zone's counter
    Given zone "zone-03" has entry_count 0
    When a telemetry event with zone_entered "zone-03" is persisted
    Then zone "zone-03" has entry_count 1
    And no other zone's entry_count changes

  Scenario: A null zone_entered leaves all counters unchanged
    Given the seeded per-zone counts are all 0
    When a telemetry event with zone_entered null is persisted
    Then every zone's entry_count is still 0

  Scenario: The increment is a single server-side read-modify-write
    Given a telemetry event with a non-null zone_entered is being persisted
    Then the counter is advanced by one statement of the form
      "UPDATE zone_counts SET entry_count = entry_count + 1 WHERE zone_id = $1"
    And the row-locked read-modify-write happens inside Postgres
    And the application does NOT issue a SELECT-then-UPDATE on the counter

  Scenario: The increment shares the ingest transaction
    Given a telemetry event with a non-null zone_entered
    When persistence commits the raw event and the vehicle current-state upsert
    Then the zone increment is committed in the same transaction
    And if the transaction fails the increment is rolled back with the rest

  Scenario: No lost updates under a burst of concurrent entries to one zone
    Given zone "zone-07" has entry_count 0
    When N telemetry events with zone_entered "zone-07" are persisted concurrently
    Then zone "zone-07" has entry_count exactly N
    And no increment is lost or double-counted

  Scenario: Per-zone counts read back the live totals
    Given a mix of zone entries and null-zone events have been persisted
    When the per-zone counts are read back
    Then all ~20 seeded zones are present
    And each zone's entry_count equals the number of committed entries to that zone
