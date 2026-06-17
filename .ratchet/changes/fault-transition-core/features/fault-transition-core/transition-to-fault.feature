Feature: Transition a vehicle to fault as one row-locked transaction
  As the telemetry system
  I want the fault transition for a vehicle to run as a single transaction that
  takes a pessimistic lock on the vehicle row (SELECT 1 FROM vehicles WHERE
  vehicle_id = $1 FOR UPDATE), cancels that vehicle's active mission, inserts a
  maintenance record, and sets the vehicle's status to fault
  So that the three writes commit together or not at all, and all fault handling
  for a given vehicle serializes behind its row lock, per the
  telemetry-architecture standard

  Background:
    Given the persistence layer runs against a migrated, seeded Postgres
    And a vehicles table holds one authoritative row per vehicle (the lock anchor)
      with a status of idle, moving, charging, or fault
    And a missions table holds at most one active mission per vehicle
    And a maintenance_records table records vehicles needing maintenance
    And the transition is exposed as a persistence call seam, not an HTTP route
      (the status-update endpoint and the full phase proof are a follow-on change)

  Scenario: A fault transition cancels the active mission and records maintenance
    Given vehicle "agv-1" exists with status "moving"
    And vehicle "agv-1" has an active mission
    When vehicle "agv-1" is transitioned to fault
    Then vehicle "agv-1" ends with status "fault"
    And that vehicle's previously active mission is cancelled
    And exactly one maintenance record exists for vehicle "agv-1"

  Scenario: The transition holds a FOR UPDATE lock on the vehicle row
    Given vehicle "agv-1" exists with status "moving"
    When vehicle "agv-1" is transitioned to fault
    Then the transaction first runs SELECT 1 FROM vehicles WHERE vehicle_id = $1
      FOR UPDATE before cancelling the mission or inserting the maintenance record
    And all three writes run in that same transaction

  Scenario: A fault with no active mission still records maintenance and sets fault
    Given vehicle "agv-2" exists with status "idle"
    And vehicle "agv-2" has no active mission
    When vehicle "agv-2" is transitioned to fault
    Then vehicle "agv-2" ends with status "fault"
    And exactly one maintenance record exists for vehicle "agv-2"
    And no mission is cancelled for vehicle "agv-2"

  Scenario: The three writes are atomic
    Given vehicle "agv-1" exists with status "moving" and an active mission
    When the fault transition transaction fails before commit
    Then the vehicle's status is unchanged
    And the mission is not cancelled
    And no maintenance record is written for that vehicle

  Scenario: Cancelling the active mission does not touch other vehicles' missions
    Given vehicle "agv-1" has an active mission
    And vehicle "agv-2" has an active mission
    When vehicle "agv-1" is transitioned to fault
    Then only vehicle "agv-1"'s mission is cancelled
    And vehicle "agv-2"'s mission remains active
