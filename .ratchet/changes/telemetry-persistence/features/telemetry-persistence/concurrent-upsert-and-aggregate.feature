Feature: Concurrency-safe upserts and aggregate fleet state
  As the read side of the fleet monitor
  I want the per-status fleet aggregate derived from the per-vehicle current-state table
  So that the aggregate is internally consistent under MVCC and free of lost or double-counted upserts under concurrent writes

  Background:
    Given a Postgres database migrated with the vehicle_current_state table

  Scenario: The aggregate is computed by GROUP BY over the current-state table
    Given vehicle_current_state holds the latest status for several distinct vehicles
    When the fleet-state aggregate is read
    Then it returns a count per status keyed by idle, moving, charging and fault
    And the counts are produced by a single GROUP BY over vehicle_current_state, not a materialized counter

  Scenario: Statuses with no vehicles report zero
    Given vehicle_current_state contains only vehicles with status "moving"
    When the fleet-state aggregate is read
    Then idle, charging and fault each report a count of 0
    And moving reports the number of distinct vehicles

  Scenario: Concurrent upserts across distinct vehicles are all reflected, last event wins
    Given 50 distinct vehicles across mixed statuses
    When telemetry events for all 50 vehicles are persisted concurrently
    Then vehicle_current_state holds exactly one row per vehicle
    And each row matches that vehicle's last persisted event
    And the per-status counts from the aggregate sum to 50

  Scenario: Repeated upserts for one vehicle never multiply its contribution
    Given vehicle "v-3" has status "idle"
    When several events for "v-3" are persisted concurrently
    Then vehicle_current_state still has exactly one row for "v-3"
    And the row reflects the last committed event
    And "v-3" contributes exactly one to the aggregate counts
