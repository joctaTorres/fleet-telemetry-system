Feature: Persist a telemetry event to Postgres
  As the stateless ingestion path
  I want each validated telemetry event to append a raw record and upsert the vehicle's current state in one transaction
  So that the authoritative per-vehicle state is always the vehicle's latest event and no committed write is lost

  Background:
    Given a Postgres database migrated with the raw_events and vehicle_current_state tables
    And vehicle_current_state has vehicle_id as its primary key

  Scenario: A first event for a vehicle creates its current-state row and a raw record
    Given no rows exist for vehicle "v-12"
    When a telemetry event for "v-12" with status "moving" and battery_pct 78 is persisted
    Then vehicle_current_state has exactly one row for "v-12" with status "moving" and battery_pct 78
    And raw_events contains exactly one row for "v-12"

  Scenario: A later event upserts the vehicle's current state and appends another raw record
    Given vehicle "v-12" has a persisted event with status "moving"
    When a newer telemetry event for "v-12" with status "charging" and battery_pct 80 is persisted
    Then vehicle_current_state still has exactly one row for "v-12"
    And that row reflects status "charging" and battery_pct 80
    And raw_events contains two rows for "v-12"

  Scenario: The raw append and the current-state upsert commit atomically
    Given a telemetry event for "v-7" that will fail the current-state upsert
    When the event is persisted within a single transaction
    Then neither a raw_events row nor a vehicle_current_state row exists for "v-7"

  Scenario: Connection configuration is read from the environment
    Given the database connection is configured from environment variables
    When the persistence layer opens a connection
    Then no credentials or connection string are hard-coded in source
