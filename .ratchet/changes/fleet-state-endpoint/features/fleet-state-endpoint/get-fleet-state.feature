Feature: Read aggregate fleet state over HTTP
  As the dashboard (an HTTP client)
  I want to GET the current aggregate fleet state from the frontend API
  So that I can see how many vehicles are in each status, computed from committed
  database state and never diverging from it

  Background:
    Given the frontend API is running against a migrated Postgres database
    And the API exposes "GET /fleet/state"

  Scenario: Per-status counts across distinct vehicles
    Given vehicle "v-1" has current status "moving"
    And vehicle "v-2" has current status "moving"
    And vehicle "v-3" has current status "idle"
    And vehicle "v-4" has current status "charging"
    When a client GETs "/fleet/state"
    Then the response status is 200
    And the body reports counts of idle 1, moving 2, charging 1 and fault 0

  Scenario: Every status key is always present, zero-filled
    Given no vehicles have reported telemetry
    When a client GETs "/fleet/state"
    Then the response status is 200
    And the body reports counts of idle 0, moving 0, charging 0 and fault 0

  Scenario: Only the latest event per vehicle is counted
    Given vehicle "v-7" most recently reported status "charging"
    And vehicle "v-7" had earlier reported status "moving"
    When a client GETs "/fleet/state"
    Then "v-7" contributes exactly one to the charging count and zero to moving

  Scenario: Counts always sum to the number of distinct vehicles
    Given 50 distinct vehicles have each reported a current status
    When a client GETs "/fleet/state"
    Then the response status is 200
    And the per-status counts sum to 50

  Scenario: The aggregate is internally consistent under concurrent writes
    Given 50 distinct vehicles are POSTing telemetry to the ingestion API across mixed statuses
    When their events are committed and a client GETs "/fleet/state"
    Then the per-status counts sum to 50
    And each status count exactly matches the number of vehicles whose last committed event had that status
    And no upsert is lost or double-counted

  Scenario: The read path is separate from the write path
    Given the ingestion API owns "POST /telemetry" and holds no authoritative aggregate
    When the dashboard reads "/fleet/state"
    Then it is served by the frontend API, a separate application from the ingestion API
    And the aggregate is derived by GROUP BY over vehicle_current_state in one MVCC snapshot
    And the frontend API holds no authoritative in-process aggregate of its own
