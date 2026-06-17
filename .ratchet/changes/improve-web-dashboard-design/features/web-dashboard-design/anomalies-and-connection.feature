Feature: Anomaly surfacing and connection health
  As a fleet operator
  I want anomalies to stand out and connection state to be visible
  So that I notice critical events and know when live updates are available

  Background:
    Given the dashboard is connected to the live telemetry WebSocket

  Scenario: Vehicle with an anomaly shows a prominent anomaly badge
    Given a vehicle with anomaly type "hard_braking"
    When its row is rendered
    Then the anomaly type is displayed inside a styled anomaly badge
    And the anomaly text is visible alongside other vehicle details

  Scenario: Newly arriving anomaly is announced to assistive technology
    Given a vehicle with no anomaly
    When a WebSocket update adds anomaly type "swerving" to that vehicle
    Then a styled anomaly badge appears for that vehicle
    And the anomaly is announced through an aria-live region

  Scenario: Connection indicator shows a connected state
    Given the WebSocket connection is open
    Then the dashboard header shows a connected indicator
    And the indicator includes a text label describing the connected state

  Scenario: Connection indicator shows a disconnected state
    Given the WebSocket connection closes unexpectedly
    Then the dashboard header shows a disconnected indicator
    And the indicator includes a text label describing the disconnected state
