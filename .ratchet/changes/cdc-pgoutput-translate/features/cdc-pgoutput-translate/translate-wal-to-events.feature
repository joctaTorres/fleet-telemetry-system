Feature: Translate committed watched-table changes into Redis events
  As the real-time event source for the dashboard
  I want a singleton CDC consumer to decode the primary's pgoutput WAL stream
  for the three watched tables and publish one derived state patch per change
  So that committed changes — and only committed changes — become
  vehicle_state_changed / anomaly_detected / zone_count_changed events on the
  Redis channel, as a deterministic function of the WAL with no dual-write
  (ADR-0001 Option B / D3; telemetry-architecture standard)

  Background:
    Given the test topology from docker-compose.test.yml runs a Postgres
      primary configured for logical decoding (wal_level=logical) and Redis
    And a logical replication slot using the pgoutput plugin exists on the
      primary, bound to a publication covering only the three watched tables
    And the CDC consumer is tailing that slot and subscribers can observe the
      single Redis channel "fleet:events"
    And every published message is a JSON envelope carrying a "type" — one of
      vehicle_state_changed, anomaly_detected, zone_count_changed — and a
      "payload", per the shared contract in app/events.py

  Scenario: A committed vehicle_current_state upsert becomes vehicle_state_changed
    Given a telemetry write commits on the primary that upserts vehicle "agv-7"
      to status "fault" with a battery percentage
    When the consumer decodes the resulting pgoutput Insert/Update message
    Then a single "vehicle_state_changed" event is published to "fleet:events"
    And its payload identifies vehicle "agv-7" and carries the committed status
      "fault" and battery percentage from the row

  Scenario: A committed anomalies insert becomes anomaly_detected
    Given a telemetry write commits on the primary that inserts an anomaly row
      of type "low_battery" for vehicle "agv-7"
    When the consumer decodes the resulting pgoutput Insert message
    Then a single "anomaly_detected" event is published to "fleet:events"
    And its payload identifies vehicle "agv-7" and the anomaly_type "low_battery"
      with the row's detail and detected_at

  Scenario: A committed zone_counts increment becomes zone_count_changed
    Given the atomic UPDATE zone_counts SET entry_count = entry_count + 1 commits
      for zone "zone-3" on the primary
    When the consumer decodes the resulting pgoutput Update message
    Then a single "zone_count_changed" event is published to "fleet:events"
    And its payload identifies zone "zone-3" and the new entry_count

  Scenario: An uncommitted write produces no event
    Given a write against a watched table is opened in a transaction on the
      primary and then rolled back
    When the consumer processes the WAL stream
    Then no event is published to "fleet:events" for that aborted write
    And the event stream reflects only committed state, never an in-flight one

  Scenario: A write to a non-watched table produces no event
    Given a row is committed on the primary in a table outside the publication
      (for example raw_events or missions)
    When the consumer processes the WAL stream
    Then no event is published to "fleet:events" for that change
    And only the three watched tables produce events

  Scenario: The ingestion API is never the event source
    Given the stateless ingestion API handles "POST /telemetry"
    When a telemetry event is ingested and committed
    Then the ingestion API writes only to Postgres and publishes nothing to Redis
    And the corresponding "fleet:events" message originates solely from the CDC
      consumer decoding the WAL, never from the write path
