Feature: Phase proof — concurrent and duplicate fault updates converge over HTTP
  As the telemetry system
  I want concurrent and duplicate fault status-updates for the same vehicle,
  delivered over the HTTP endpoint, to converge on exactly one outcome
  So that the phase-4 proof tests/integration/test_fault_transition.py passes:
  the HTTP surface inherits the row-lock + transition-guard + uniqueness
  idempotency of the underlying handler — never a double-cancel or a duplicate
  maintenance record

  Background:
    Given the ingestion API runs against the real Postgres from
      docker-compose.test.yml
    And requests are driven in-process against the ASGI app (no running uvicorn)
    And the fault-domain tables (vehicles, missions, maintenance_records) start empty

  Scenario: Many concurrent fault updates for one vehicle apply exactly once
    Given vehicle "agv-1" exists with status "moving" and an active mission
    When many clients POST a status update of "fault" for vehicle "agv-1" concurrently
    Then exactly one of those updates cancels the active mission
    And exactly one maintenance record exists for vehicle "agv-1"
    And vehicle "agv-1" ends with status "fault"

  Scenario: A duplicate (sequential) fault update for one vehicle applies exactly once
    Given vehicle "agv-1" exists with status "moving" and an active mission
    When a client POSTs a status update of "fault" for vehicle "agv-1"
    And the same client POSTs the identical status update again
    Then exactly one mission is cancelled for vehicle "agv-1"
    And exactly one maintenance record exists for vehicle "agv-1"
    And vehicle "agv-1" ends with status "fault"
