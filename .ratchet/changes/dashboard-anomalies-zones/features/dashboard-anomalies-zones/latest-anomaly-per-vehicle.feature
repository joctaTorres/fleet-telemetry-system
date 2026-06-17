Feature: Most-recent anomaly per vehicle, surfaced live on each row
  As a floor manager watching the fleet dashboard
  I want each vehicle row to show that vehicle's most recent anomaly and to
  update it the instant a new anomaly is detected — without re-rendering the
  other rows and without polling
  So that a newly flagged vehicle stands out immediately while the rest of the
  list stays untouched

  Background:
    Given a React + TypeScript dashboard mounted with a mocked transport
    And the transport is seeded with a REST snapshot of 50 vehicles and the
      most-recent anomaly per vehicle (vehicle_id, anomaly_type, detail,
      detected_at), reusing the snapshot-then-stream transport from
      dashboard-shell-live-list
    And the dashboard subscribes to the transport's WebSocket patch stream

  Scenario: Each row shows the vehicle's latest anomaly from the snapshot on load
    When the dashboard mounts
    Then a vehicle that has a most-recent anomaly in the snapshot shows that
      anomaly_type on its row
    And a vehicle with no anomaly in the snapshot shows no anomaly on its row
    And no polling timer is started — the anomaly is not refetched on an interval

  Scenario: An anomaly_detected patch updates only the affected vehicle's anomaly
    Given the list is rendered from the snapshot
    When an anomaly_detected patch arrives for one vehicle_id carrying a new
      anomaly_type, detail, and detected_at
    Then that vehicle's row shows the new anomaly_type as its most-recent anomaly
    And only that one row re-renders — the other 49 rows are not re-rendered
    And the page is not refreshed and the full list is not rebuilt

  Scenario: A new anomaly surfaces immediately, not at a poll interval
    Given a vehicle currently shows no anomaly in the rendered list
    When an anomaly_detected patch arrives for that vehicle_id
    Then its row reflects the new anomaly on the next render tick, with no
      intervening poll or manual refresh

  Scenario: Last-detected-wins when multiple anomalies arrive for one vehicle
    Given the list is rendered from the snapshot
    When two anomaly_detected patches arrive in order for the same vehicle_id
    Then that vehicle's row reflects the most recent anomaly (last patch)
    And no other row's anomaly is affected

  Scenario: An anomaly_detected patch for an unknown vehicle does not corrupt the list
    Given the list is rendered from the snapshot
    When an anomaly_detected patch arrives for a vehicle_id not in the snapshot
    Then the existing 50 rows and their anomalies are unchanged
    And no row is dropped or duplicated
