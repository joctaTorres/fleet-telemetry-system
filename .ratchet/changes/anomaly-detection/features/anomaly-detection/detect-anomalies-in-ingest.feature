Feature: Detect anomalies synchronously inside the ingest transaction
  As the telemetry system
  I want each persisted telemetry event to be checked against the default anomaly
  rules — both stateless rules on the event itself and stateful rules against the
  vehicle's previous persisted reading — and every detected anomaly written to the
  anomalies table in the same transaction as the event
  So that an anomaly is committed exactly when its threshold is crossed, atomically
  with the reading that caused it, with no dual-write window and no missed or
  spurious detections

  Background:
    Given the persistence layer runs against a migrated, seeded Postgres
    And the telemetry event carries vehicle_id, status, battery_pct, recorded_at,
      and (for these rules) speed_mps, error_codes, and an x/y position in metres
    And vehicle_current_state holds the vehicle's previous persisted reading
      (status, battery_pct, speed_mps, position, recorded_at)
    And anomalies are written inside the same transaction as the raw event insert
      and the current-state upsert, per the telemetry-architecture standard
    And the by-absence comms-loss watchdog and the GET /anomalies endpoint are out
      of scope for this change (separate follow-on changes)

  # ── Stateless rules: evaluated on the event alone ──────────────────────────

  Scenario: A fault status raises a fault anomaly
    When a telemetry event with status "fault" is persisted
    Then an anomaly of type "fault_status" is written for that vehicle
    And it is detected at the event's recorded_at

  Scenario: Non-empty error_codes raise an error-codes anomaly
    When a telemetry event with a non-empty error_codes list is persisted
    Then an anomaly of type "error_codes" is written for that vehicle

  Scenario: An empty error_codes list raises no error-codes anomaly
    When a telemetry event with an empty error_codes list is persisted
    Then no anomaly of type "error_codes" is written for that vehicle

  Scenario: Low battery while not charging raises a low-battery anomaly
    When a telemetry event with battery_pct 12 and status "moving" is persisted
    Then an anomaly of type "low_battery" is written for that vehicle

  Scenario: Low battery while charging does not raise a low-battery anomaly
    When a telemetry event with battery_pct 12 and status "charging" is persisted
    Then no anomaly of type "low_battery" is written for that vehicle

  Scenario: Battery at the threshold does not raise a low-battery anomaly
    When a telemetry event with battery_pct 15 and status "moving" is persisted
    Then no anomaly of type "low_battery" is written for that vehicle

  Scenario: Speed above the limit raises an overspeed anomaly
    When a telemetry event with speed_mps 6 is persisted
    Then an anomaly of type "overspeed" is written for that vehicle

  Scenario: Speed at the limit does not raise an overspeed anomaly
    When a telemetry event with speed_mps 5 is persisted
    Then no anomaly of type "overspeed" is written for that vehicle

  Scenario: A clean event raises no anomalies
    When a telemetry event with status "moving", battery_pct 80, speed_mps 2,
      and no error_codes is persisted as the vehicle's first reading
    Then no anomaly is written for that vehicle

  # ── Stateful rules: evaluated against the previous persisted reading ───────

  Scenario: A vehicle stuck while moving for at least 10s raises a stuck anomaly
    Given the vehicle's previous reading was status "moving", speed_mps 0.05,
      recorded 11 seconds before this event
    When a telemetry event with status "moving" and speed_mps 0.05 is persisted
    Then an anomaly of type "stuck" is written for that vehicle

  Scenario: A slow-but-recent moving reading does not raise a stuck anomaly
    Given the vehicle's previous reading was status "moving", speed_mps 0.05,
      recorded 4 seconds before this event
    When a telemetry event with status "moving" and speed_mps 0.05 is persisted
    Then no anomaly of type "stuck" is written for that vehicle

  Scenario: Implied speed over 15 m/s between consecutive events raises a teleport
    Given the vehicle's previous reading was at position (0, 0) recorded 1 second
      before this event
    When a telemetry event at position (100, 0) is persisted
    Then an anomaly of type "teleport" is written for that vehicle

  Scenario: A plausible implied speed does not raise a teleport anomaly
    Given the vehicle's previous reading was at position (0, 0) recorded 10 seconds
      before this event
    When a telemetry event at position (100, 0) is persisted
    Then no anomaly of type "teleport" is written for that vehicle

  Scenario: Battery rising while not charging raises a battery-rising anomaly
    Given the vehicle's previous reading had battery_pct 40
    When a telemetry event with battery_pct 45 and status "moving" is persisted
    Then an anomaly of type "battery_rising" is written for that vehicle

  Scenario: Battery rising while charging is normal
    Given the vehicle's previous reading had battery_pct 40
    When a telemetry event with battery_pct 45 and status "charging" is persisted
    Then no anomaly of type "battery_rising" is written for that vehicle

  Scenario: Stateful rules do not fire on a vehicle's first reading
    When the first-ever telemetry event for a vehicle is persisted
    Then no stateful anomaly (stuck, teleport, battery_rising) is written for it

  # ── Atomicity ──────────────────────────────────────────────────────────────

  Scenario: Detected anomalies share the ingest transaction
    Given a telemetry event that crosses one or more anomaly thresholds
    When persistence commits the raw event and the current-state upsert
    Then the anomaly rows are committed in the same transaction
    And if the transaction fails the anomaly rows are rolled back with the rest
