Feature: Typed snapshot-then-stream transport over REST + WebSocket
  As the data layer of the fleet dashboard
  I want a single typed transport that fetches the REST snapshot once on load
  and then opens the WebSocket to receive individual state patches
  So that the UI is seeded from committed state and thereafter lives only on
  granular deltas — no polling and no full refetch

  Background:
    Given the dashboard runs under web/ as a Vite + React + TypeScript app
    And the frontend API exposes a REST read for the per-vehicle snapshot and a
      WebSocket endpoint streaming state patches
    And the patch envelope contract matches app/events.py: a "type" of
      vehicle_state_changed | anomaly_detected | zone_count_changed and a typed
      "payload"

  Scenario: The transport fetches the REST snapshot exactly once on load
    When the dashboard starts
    Then the transport issues one REST request for the vehicle snapshot
    And it does not schedule any repeat/interval refetch of that snapshot

  Scenario: The transport opens the WebSocket for live patches after the snapshot
    Given the REST snapshot has been fetched
    When the transport connects the WebSocket
    Then patches received on the socket are surfaced to the app as typed events
    And a vehicle_state_changed payload carries vehicle_id, status, and
      battery_pct, exactly as published on the Redis channel

  Scenario: The vehicle-snapshot REST read is served from the read replica
    Given the frontend API serves the per-vehicle snapshot
    When the dashboard requests it
    Then the per-vehicle list (vehicle_id, status, battery_pct) is read from the
      streaming replica via the existing replica connection seam
    And the write path / primary is not touched to serve the read

  Scenario: Unknown or malformed patch types are ignored, not fatal
    Given the WebSocket is open
    When a message arrives whose "type" is not one of the three known event
      types
    Then the transport drops it without throwing and the dashboard keeps running
