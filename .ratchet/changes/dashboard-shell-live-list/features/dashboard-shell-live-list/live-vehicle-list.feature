Feature: Live 50-vehicle list driven by granular WebSocket patches
  As a floor manager watching the fleet dashboard
  I want the list of vehicles to render from the connect snapshot and then
  update each row in place the instant a vehicle's state changes — never by
  polling and never by re-rendering the whole list
  So that a status flip or battery change for one vehicle surfaces immediately
  while the other 49 rows stay untouched

  Background:
    Given a React + TypeScript dashboard mounted with a mocked transport
    And the transport is seeded with a REST snapshot of 50 vehicles, each with a
      vehicle_id, a current status (idle|moving|charging|fault), and a
      battery_pct
    And the dashboard subscribes to the transport's WebSocket patch stream

  Scenario: The list renders from the REST snapshot on load
    When the dashboard mounts
    Then it renders exactly one row per vehicle in the snapshot (50 rows)
    And each row shows that vehicle's current status and battery_pct
    And no polling timer is started — the list does not refetch on an interval

  Scenario: A vehicle_state_changed patch updates only the affected row
    Given the list is rendered from the snapshot
    When a vehicle_state_changed patch arrives for one vehicle_id carrying a new
      status and battery_pct
    Then that vehicle's row shows the new status and battery_pct
    And only that one row re-renders — the other 49 rows are not re-rendered
    And the page is not refreshed and the full list is not rebuilt

  Scenario: A fault surfaces immediately, not at a poll interval
    Given a vehicle is currently "moving" in the rendered list
    When a vehicle_state_changed patch arrives flipping that vehicle to "fault"
    Then its row reflects "fault" on the next render tick, with no intervening
      poll or manual refresh

  Scenario: A patch for an unknown vehicle does not corrupt the list
    Given the list is rendered from the snapshot
    When a vehicle_state_changed patch arrives for a vehicle_id not in the
      snapshot
    Then the existing 50 rows are unchanged
    And the list either ignores the patch or adds a single new row, never
      dropping or duplicating an existing vehicle

  Scenario: Patches are applied by vehicle_id, last-write-wins per vehicle
    Given the list is rendered from the snapshot
    When two vehicle_state_changed patches arrive for the same vehicle_id in
      order
    Then that vehicle's row reflects the value from the most recent patch
    And no other row is affected
