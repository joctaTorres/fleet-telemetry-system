Feature: Serve a one-shot snapshot on WebSocket connect, then stream deltas
  As the dashboard (a WebSocket client)
  I want a single current-state snapshot the moment I connect, and only
  incremental patches afterwards
  So that I can render the full fleet immediately and then apply granular
  deltas — no polling, no full re-render — and any frontend instance can serve
  any client because it holds no authoritative state

  Background:
    Given the frontend API runs against the real Postgres and Redis from
      docker-compose.test.yml
    And the frontend API exposes a WebSocket endpoint "GET /ws"
    And the snapshot is derived fresh from the database read seams
      (aggregate_fleet_state, zone_entry_counts) on connect

  Scenario: The first message on connect is a one-shot snapshot
    Given the database has committed fleet and zone state
    When a client connects to "/ws"
    Then the first message it receives is a snapshot of the current state
    And the snapshot reflects the committed fleet and per-zone counts
    And the snapshot is sent exactly once

  Scenario: After the snapshot the client receives only deltas
    Given a client has connected to "/ws" and received its snapshot
    When a "zone_count_changed" patch is published to the Redis channel
    Then the client receives that delta after the snapshot
    And no second full snapshot is sent

  Scenario: A later client gets a snapshot reflecting state at its connect time
    Given a client connected earlier and received its snapshot
    And state has since changed in the database
    When a second client connects to "/ws"
    Then the second client's snapshot reflects the current committed state
    And both clients thereafter receive the same published deltas

  Scenario: The frontend API holds no authoritative state
    Given a client is connected to "/ws"
    When the snapshot is built
    Then it is derived fresh from the database, not from an in-process aggregate
    And the frontend API keeps only the set of live connections, no authoritative
      fleet state of its own
