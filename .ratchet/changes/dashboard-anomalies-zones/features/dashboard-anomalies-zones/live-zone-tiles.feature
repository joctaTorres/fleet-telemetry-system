Feature: Live per-zone entry-count tiles
  As a floor manager watching the fleet dashboard
  I want a tile per zone showing its current entry count, seeded from the REST
  snapshot and ticking live as vehicles enter zones — without re-rendering the
  other tiles and without polling
  So that zone traffic is visible in real time alongside the vehicle list

  Background:
    Given a React + TypeScript dashboard mounted with a mocked transport
    And the transport is seeded with a REST snapshot of the per-zone entry
      counts (zone_id -> entry_count) for the ~20 seeded zones
    And the dashboard subscribes to the transport's WebSocket patch stream

  Scenario: The zone tiles render from the REST snapshot on load
    When the dashboard mounts
    Then it renders exactly one tile per zone in the snapshot
    And each tile shows that zone's zone_id and current entry_count
    And no polling timer is started — the counts are not refetched on an interval

  Scenario: A zone_count_changed patch updates only the affected tile
    Given the zone tiles are rendered from the snapshot
    When a zone_count_changed patch arrives for one zone_id carrying a new
      entry_count
    Then that zone's tile shows the new entry_count
    And only that one tile re-renders — the other zone tiles are not re-rendered
    And the page is not refreshed and the tile grid is not rebuilt

  Scenario: Counts tick live, not at a poll interval
    Given a zone currently shows an entry_count in the rendered grid
    When a zone_count_changed patch arrives raising that zone's entry_count
    Then its tile reflects the new count on the next render tick, with no
      intervening poll or manual refresh

  Scenario: Successive patches for one zone resolve last-write-wins for that tile
    Given the zone tiles are rendered from the snapshot
    When two zone_count_changed patches arrive in order for the same zone_id
    Then that zone's tile reflects the entry_count from the most recent patch
    And no other tile is affected

  Scenario: A zone snapshot read does not touch the write path
    Given the frontend API serves the per-zone snapshot
    When the dashboard requests it on load
    Then the per-zone counts are read through the existing replica connection
      seam, reusing the GET /zones/counts read
    And the write path / primary is not touched to serve the read
