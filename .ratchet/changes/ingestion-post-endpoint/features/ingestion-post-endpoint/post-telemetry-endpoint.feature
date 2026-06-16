Feature: Ingest a telemetry event over HTTP
  As a vehicle (an HTTP client)
  I want to POST a telemetry event to the stateless ingestion API
  So that a validated reading is durably persisted to Postgres and reflected as the
  vehicle's authoritative current state

  Background:
    Given the ingestion API is running against a migrated Postgres database
    And the API exposes "POST /telemetry"

  Scenario: A valid telemetry event is accepted and persisted
    Given no rows exist for vehicle "v-12"
    When a client POSTs a telemetry event for "v-12" with status "moving" and battery_pct 78
    Then the response status is 201
    And vehicle_current_state has exactly one row for "v-12" with status "moving" and battery_pct 78
    And raw_events contains exactly one row for "v-12"

  Scenario: A later event for the same vehicle upserts its current state
    Given vehicle "v-12" has an accepted event with status "moving"
    When a client POSTs a newer event for "v-12" with status "charging" and battery_pct 80
    Then the response status is 201
    And vehicle_current_state still has exactly one row for "v-12"
    And that row reflects status "charging" and battery_pct 80
    And raw_events contains two rows for "v-12"

  Scenario: An event with an out-of-range status is rejected
    When a client POSTs an event for "v-9" with status "teleporting" and battery_pct 50
    Then the response status is 422
    And no row exists for "v-9" in vehicle_current_state
    And no row exists for "v-9" in raw_events

  Scenario: An event with battery_pct outside 0..100 is rejected
    When a client POSTs an event for "v-9" with status "idle" and battery_pct 150
    Then the response status is 422
    And no row exists for "v-9" in vehicle_current_state

  Scenario: An event missing a required field is rejected
    When a client POSTs a body for "v-9" that omits status
    Then the response status is 422
    And no row exists for "v-9" in vehicle_current_state

  Scenario: An event carrying an unknown field is rejected
    When a client POSTs an event for "v-9" with status "idle", battery_pct 50 and an extra field "speed"
    Then the response status is 422
    And no row exists for "v-9" in vehicle_current_state

  Scenario: The ingestion endpoint is stateless — validate, write, return
    Given the ingestion API holds no authoritative in-process aggregate
    When a client POSTs a valid telemetry event
    Then the request path only validates the event and writes to Postgres before returning
    And it publishes to no Redis instance or message broker in the request path
