Feature: Seed the per-zone counter table at startup
  As the telemetry system
  I want a row in zone_counts for every known zone, created by a versioned migration
  So that every zone has a stable counter row to increment and to read back, and a
  read of per-zone counts always reports all ~20 zones — even those never entered

  Background:
    Given the system defines a hardcoded startup constant of the ~20 known zone ids
    And a versioned migration creates the "zone_counts" table with primary key
      "zone_id" and a non-null integer "entry_count"

  Scenario: Every known zone has a counter row, starting at zero
    Given a freshly migrated and seeded database
    When the per-zone counts are read back
    Then there is exactly one row per known zone
    And the number of rows equals the number of zones in the startup constant (~20)
    And every zone's entry_count is 0

  Scenario: Seeding is idempotent
    Given a database that has already been migrated and seeded
    When the migration and seed run again
    Then no zone row is duplicated
    And no existing entry_count is reset or lost

  Scenario: zone_id is the primary key
    Given the seeded "zone_counts" table
    Then "zone_id" is the primary key
    And the per-zone counter is the single row identified by that zone_id
