Feature: A committed POST propagates to a WebSocket client, end to end
  As the dashboard (a WebSocket client) and a vehicle (a POST client)
  I want a write committed through the stateless ingestion API to surface as a
  derived state patch on my open WebSocket within a sub-second bound, carried
  the whole way by CDC — POST -> Postgres commit -> WAL -> CDC consumer ->
  Redis -> WebSocket fan-out — with the ingestion API never touching Redis
  So that the full real-time path is proven against the running topology (not a
  test stand-in), as a deterministic function of the committed WAL with no
  dual-write (phase blackbox proof: tests/integration/test_realtime_ws.py;
  ADR-0001 Option B / D3)

  Background:
    Given the full test topology from docker-compose.test.yml is running: a
      Postgres primary, the streaming read replica, Redis, and the singleton
      CDC consumer service tailing the primary's pgoutput logical slot
    And the stateless ingestion API handles writes ("POST /telemetry",
      "POST /vehicles/{id}/status") against the primary
    And the stateful frontend API exposes the WebSocket endpoint "GET /ws":
      a one-shot snapshot from the replica on connect, then individual state
      patches fanned out from the Redis channel
    And a dashboard client is connected to "/ws" and has drained its snapshot

  Scenario: A committed fault status update reaches the client as vehicle_state_changed
    Given vehicle "agv-7" exists with an active mission
    When the client POSTs a status update transitioning "agv-7" to "fault" and
      the ingestion API commits it on the primary
    Then the connected WebSocket client receives a "vehicle_state_changed" event
      whose payload identifies "agv-7" with status "fault"
    And it arrives within a sub-second bound, produced by the CDC consumer
      decoding the WAL, never published by the ingestion API

  Scenario: A committed zone entry reaches the client as zone_count_changed
    When the client POSTs a telemetry reading for a vehicle entering "zone-03"
      and the ingestion API commits the atomic zone-counter increment
    Then the connected WebSocket client receives a "zone_count_changed" event
      whose payload identifies "zone-03" with the incremented entry_count
    And it arrives within a sub-second bound via CDC -> Redis -> WebSocket

  Scenario: A committed anomaly reaches the client as anomaly_detected
    When the client POSTs a telemetry reading that commits an anomaly row of
      type "low_battery" for a vehicle on the primary
    Then the connected WebSocket client receives an "anomaly_detected" event
      whose payload identifies that vehicle and anomaly_type "low_battery"
    And it arrives within a sub-second bound, sourced solely from the WAL

  Scenario: An uncommitted write produces no event on the WebSocket
    Given a write against a watched table is opened in a transaction on the
      primary and rolled back without committing
    When the WebSocket client waits for the sub-second bound
    Then it receives no event for that aborted write
    And the stream reflects only committed state, because the CDC consumer emits
      only on the pgoutput Commit, never an in-flight transaction

  Scenario: The ingestion API is never the publisher
    When a telemetry event is ingested and committed
    Then the ingestion API writes only to Postgres and publishes nothing to Redis
    And the "fleet:events" message the client receives originates solely from the
      CDC consumer service decoding the WAL, never from the write path
