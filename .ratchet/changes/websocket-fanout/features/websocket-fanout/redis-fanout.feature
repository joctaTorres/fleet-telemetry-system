Feature: Fan out Redis-published state patches to connected WebSocket clients
  As the dashboard (a WebSocket client)
  I want the frontend API to subscribe to the Redis pub/sub channel and push
  every published state patch out over WebSocket to all connected clients
  So that committed changes reach the dashboard as granular deltas with
  sub-second latency, via CDC -> Redis -> WebSocket fan-out and never via the
  write path, per the telemetry-architecture standard

  Background:
    Given the frontend API runs against the real Postgres and Redis from
      docker-compose.test.yml
    And the frontend API subscribes to the Redis pub/sub channel on startup
    And the frontend API exposes a WebSocket endpoint "GET /ws"
    And in this slice there is no CDC consumer yet, so state patches are
      published to Redis directly by the test (the CDC source is a follow-on)

  Scenario: A published state patch is delivered to a connected client
    Given a client is connected to "/ws" and has received its initial snapshot
    When a "vehicle_state_changed" patch for vehicle "agv-1" is published to the
      Redis channel
    Then the connected client receives that same patch over the WebSocket
    And it is received within a sub-second bound

  Scenario: Each watched event type is forwarded as its own patch
    Given a client is connected to "/ws" and has received its initial snapshot
    When a "vehicle_state_changed" patch is published to the Redis channel
    And an "anomaly_detected" patch is published to the Redis channel
    And a "zone_count_changed" patch is published to the Redis channel
    Then the client receives a patch of each of the three event types
    And each patch carries a "type" field identifying which event it is

  Scenario: A patch is fanned out to every connected client
    Given two clients are connected to "/ws"
    When a single "zone_count_changed" patch is published to the Redis channel
    Then both connected clients receive that patch

  Scenario: Nothing is emitted when nothing is published
    Given a client is connected to "/ws" and has received its initial snapshot
    When no patch is published to the Redis channel
    Then the client receives no further WebSocket message
    And the frontend API only ever emits what it observes on the subscribed
      channel, never a patch it synthesizes itself

  Scenario: A disconnected client is dropped without blocking fan-out
    Given two clients are connected to "/ws"
    And one of them disconnects
    When a "vehicle_state_changed" patch is published to the Redis channel
    Then the still-connected client receives the patch
    And the disconnected client is removed from the connection registry

  Scenario: The ingestion API never touches Redis
    Given the stateless ingestion API handles "POST /telemetry"
    When a telemetry event is ingested
    Then the ingestion API writes only to Postgres and publishes nothing to Redis
    And the event stream to the dashboard originates from CDC -> Redis, not from
      the write path
