Feature: Query recent anomalies over HTTP filtered by vehicle and time range
  As the dashboard (an HTTP client)
  I want to GET the anomalies for a given vehicle within a [since, until] time
  range from the frontend API
  So that I can review what went wrong for one vehicle in a window, served by an
  indexed read and never including rows outside the window or from other vehicles

  Background:
    Given the frontend API is running against a migrated Postgres database
    And the anomalies table is indexed on (vehicle_id, detected_at)
    And the API exposes "GET /anomalies"
    And the endpoint accepts a vehicle_id and an inclusive [since, until] range

  Scenario: Anomalies for the requested vehicle within the window are returned
    Given vehicle "agv-1" has an anomaly detected inside the requested window
    When a client GETs "/anomalies" for "agv-1" over that window
    Then the response status is 200
    And the body contains that anomaly with its type, detail, and detected_at

  Scenario: Only the requested vehicle's anomalies are returned
    Given anomalies exist for vehicle "agv-1" and vehicle "agv-2" in the window
    When a client GETs "/anomalies" for "agv-1" over that window
    Then the response status is 200
    And only anomalies for "agv-1" are returned
    And no anomaly for "agv-2" appears in the body

  Scenario: Anomalies outside the window are excluded
    Given vehicle "agv-1" has an anomaly detected before the window starts
    And vehicle "agv-1" has an anomaly detected inside the window
    And vehicle "agv-1" has an anomaly detected after the window ends
    When a client GETs "/anomalies" for "agv-1" over that window
    Then the response status is 200
    And only the anomaly detected inside the window is returned

  Scenario: The window bounds are inclusive
    Given vehicle "agv-1" has an anomaly detected exactly at the window's since
    And vehicle "agv-1" has an anomaly detected exactly at the window's until
    When a client GETs "/anomalies" for "agv-1" over that window
    Then both boundary anomalies are returned

  Scenario: A vehicle with no anomalies in range returns an empty result
    Given vehicle "agv-9" has no anomalies in the requested window
    When a client GETs "/anomalies" for "agv-9" over that window
    Then the response status is 200
    And the body is an empty list

  Scenario: Anomalies are reachable end to end from ingestion
    Given a telemetry event for "agv-5" that crosses an anomaly threshold is POSTed to the ingestion API
    When the event is committed and a client GETs "/anomalies" for "agv-5" over a covering window
    Then the response status is 200
    And the detected anomaly for "agv-5" appears in the body

  Scenario: The read path is separate from the write path
    Given the ingestion API owns "POST /telemetry" and detects anomalies in-transaction
    When the dashboard reads "/anomalies"
    Then it is served by the frontend API, a separate application from the ingestion API
    And the result is derived fresh from the anomalies table via the (vehicle_id, detected_at) index
    And the frontend API holds no authoritative in-process state of its own
