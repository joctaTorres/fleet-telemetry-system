Feature: Read live per-zone entry counts over HTTP
  As the dashboard (an HTTP client)
  I want to GET the current per-zone entry counts from the frontend API
  So that I can see how many times each zone has been entered, computed from
  committed database state and never diverging from it

  Background:
    Given the frontend API is running against a migrated, seeded Postgres database
    And the database has ~20 seeded zones, each with a zone_counts row
    And the API exposes "GET /zones/counts"

  Scenario: All seeded zones are reported with their live totals
    Given zone "zone-03" has entry_count 2
    And zone "zone-07" has entry_count 5
    And every other zone has entry_count 0
    When a client GETs "/zones/counts"
    Then the response status is 200
    And the body reports a count for every one of the ~20 seeded zones
    And "zone-03" is 2 and "zone-07" is 5
    And every other zone is 0

  Scenario: Never-entered zones are present and zero-filled
    Given no zone entries have been recorded
    When a client GETs "/zones/counts"
    Then the response status is 200
    And all ~20 seeded zones are present
    And every zone's count is 0

  Scenario: Counts reflect committed zone entries end to end
    Given N telemetry events with zone_entered "zone-11" are POSTed to the ingestion API
    When the events are committed and a client GETs "/zones/counts"
    Then "zone-11" reports exactly N
    And no other zone's count changes

  Scenario: A burst of concurrent entries to one zone is counted exactly
    Given zone "zone-09" starts at 0
    When N telemetry events with zone_entered "zone-09" are POSTed concurrently to the ingestion API
    And a client GETs "/zones/counts"
    Then "zone-09" reports exactly N
    And no increment is lost or double-counted

  Scenario: Null zone_entered events leave all counts unchanged
    Given the seeded per-zone counts are all 0
    When telemetry events with zone_entered null are POSTed to the ingestion API
    And a client GETs "/zones/counts"
    Then every zone's count is still 0

  Scenario: The read path is separate from the write path
    Given the ingestion API owns "POST /telemetry" and holds no authoritative counter
    When the dashboard reads "/zones/counts"
    Then it is served by the frontend API, a separate application from the ingestion API
    And the per-zone totals are derived fresh from zone_counts in one MVCC snapshot
    And the frontend API holds no authoritative in-process counter of its own
