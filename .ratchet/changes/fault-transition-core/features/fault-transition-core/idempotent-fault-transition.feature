Feature: Fault transitions are idempotent under concurrency and duplicate delivery
  As the telemetry system
  I want concurrent fault events and duplicate (at-least-once) fault deliveries
  for the same vehicle to converge on exactly one outcome — one cancelled
  mission, one maintenance record, status fault
  So that the vehicle-row lock plus a transition guard and a uniqueness
  constraint make a redelivered or racing fault a safe no-op, never a
  double-cancel or a duplicate maintenance record

  Background:
    Given the persistence layer runs against a migrated, seeded Postgres
    And the transition guard skips the work when the vehicle is already in fault
    And a uniqueness constraint prevents a second open maintenance record per vehicle
    And all fault handling for a vehicle serializes behind SELECT ... FOR UPDATE on
      its vehicles row

  Scenario: A duplicate fault transition is a no-op
    Given vehicle "agv-1" exists with status "moving" and an active mission
    And vehicle "agv-1" has already been transitioned to fault
    When vehicle "agv-1" is transitioned to fault a second time
    Then vehicle "agv-1" still has status "fault"
    And exactly one mission is cancelled for vehicle "agv-1"
    And exactly one maintenance record exists for vehicle "agv-1"

  Scenario: The transition guard reports whether it applied
    Given vehicle "agv-1" exists with status "moving"
    When vehicle "agv-1" is transitioned to fault
    Then the first transition reports that it applied the change
    When vehicle "agv-1" is transitioned to fault again
    Then the second transition reports that it made no change

  Scenario: Concurrent fault transitions for one vehicle apply exactly once
    Given vehicle "agv-1" exists with status "moving" and an active mission
    When many fault transitions for vehicle "agv-1" are run concurrently
    Then exactly one of them cancels the active mission
    And exactly one maintenance record exists for vehicle "agv-1"
    And vehicle "agv-1" ends with status "fault"

  Scenario: A vehicle can fault again after it is repaired
    Given vehicle "agv-1" was transitioned to fault and its maintenance was resolved
    And vehicle "agv-1" was returned to status "moving" with a new active mission
    When vehicle "agv-1" is transitioned to fault again
    Then the new active mission is cancelled
    And a new maintenance record exists for the second fault episode
