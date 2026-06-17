Feature: Read recent anomalies filtered by vehicle and time range
  As the telemetry system
  I want a persistence read seam that returns the anomalies for a given vehicle
  within a [since, until] time range, served by an index on (vehicle_id,
  detected_at)
  So that the follow-on GET /anomalies endpoint can answer a filtered, indexed
  read without changing the query, and rows outside the window are excluded

  Background:
    Given the anomalies table is indexed on (vehicle_id, detected_at)
    And the read seam takes a vehicle_id and an inclusive [since, until] range
    And the HTTP endpoint that exposes this read is a separate follow-on change

  Scenario: Only the requested vehicle's anomalies are returned
    Given anomalies exist for vehicle "agv-1" and vehicle "agv-2"
    When recent anomalies are read for vehicle "agv-1" over a window covering both
    Then only anomalies for "agv-1" are returned

  Scenario: Anomalies inside the window are returned and those outside excluded
    Given vehicle "agv-1" has an anomaly detected before the window starts
    And vehicle "agv-1" has an anomaly detected inside the window
    And vehicle "agv-1" has an anomaly detected after the window ends
    When recent anomalies are read for "agv-1" over that window
    Then only the anomaly detected inside the window is returned

  Scenario: The window bounds are inclusive
    Given vehicle "agv-1" has an anomaly detected exactly at the window's since
    And vehicle "agv-1" has an anomaly detected exactly at the window's until
    When recent anomalies are read for "agv-1" over that window
    Then both boundary anomalies are returned

  Scenario: A vehicle with no anomalies in range returns nothing
    Given vehicle "agv-9" has no anomalies in the requested window
    When recent anomalies are read for "agv-9" over that window
    Then no anomalies are returned

  Scenario: The read is served by the (vehicle_id, detected_at) index
    Given the anomalies table is indexed on (vehicle_id, detected_at)
    When recent anomalies are read for a vehicle over a [since, until] range
    Then the query filters by vehicle_id and detected_at and uses that index
