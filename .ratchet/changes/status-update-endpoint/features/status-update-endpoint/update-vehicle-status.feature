Feature: Update a vehicle's status over HTTP
  As a fleet operator (HTTP client)
  I want a status-update operation that sets a vehicle's authoritative status and,
  on transition to fault, invokes the transactional fault handler that cancels the
  active mission and opens a maintenance record
  So that the fault transition proven at the persistence layer
  (fault-transition-core) is reachable end to end over REST, completing the phase

  Background:
    Given the ingestion API runs against a migrated, seeded Postgres
    And a vehicles table holds one authoritative row per vehicle (the lock anchor)
    And the persistence seam transition_to_fault(vehicle_id, reason) already exists
      and is idempotent under concurrency and at-least-once delivery
    And the status-update route delegates the fault case to that seam rather than
      reimplementing the transaction

  Scenario: Updating a vehicle to fault cancels its mission and records maintenance
    Given vehicle "agv-1" exists with status "moving"
    And vehicle "agv-1" has an active mission
    When a client POSTs a status update of "fault" for vehicle "agv-1"
    Then the response status is 200
    And vehicle "agv-1" ends with status "fault"
    And that vehicle's previously active mission is cancelled
    And exactly one maintenance record exists for vehicle "agv-1"

  Scenario: The fault response reports that the transition applied
    Given vehicle "agv-1" exists with status "moving"
    When a client POSTs a status update of "fault" for vehicle "agv-1"
    Then the response status is 200
    And the response indicates the transition was applied

  Scenario: A duplicate fault update over HTTP is a safe no-op
    Given vehicle "agv-1" exists with status "moving" and an active mission
    And vehicle "agv-1" has already been updated to "fault"
    When a client POSTs a status update of "fault" for vehicle "agv-1" again
    Then the response status is 200
    And the response indicates no change was applied
    And exactly one mission is cancelled for vehicle "agv-1"
    And exactly one maintenance record exists for vehicle "agv-1"

  Scenario: A status update for an unknown vehicle is rejected
    Given no vehicle row exists for "ghost-9"
    When a client POSTs a status update of "fault" for vehicle "ghost-9"
    Then the response status is 404
    And no maintenance record exists for vehicle "ghost-9"

  Scenario: A schema-invalid status update is rejected and writes nothing
    Given vehicle "agv-1" exists with status "moving" and an active mission
    When a client POSTs a status update of "exploded" for vehicle "agv-1"
    Then the response status is 422
    And vehicle "agv-1" still has status "moving"
    And that vehicle's active mission remains active
    And no maintenance record exists for vehicle "agv-1"
