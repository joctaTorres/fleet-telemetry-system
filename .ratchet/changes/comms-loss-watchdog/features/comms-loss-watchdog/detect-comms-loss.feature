Feature: Detect comms loss by absence with a background watchdog
  As the telemetry system
  I want a background sweep to flag any vehicle that has gone silent — no
  telemetry event for longer than the comms-loss timeout — and write a comms_loss
  anomaly for it, exactly once per silence episode
  So that a vehicle dropping off the network is surfaced even though, by
  definition, no event arrives to trigger in-transaction detection, and the gap
  is readable over GET /anomalies like every other anomaly class

  Background:
    Given the persistence layer runs against a migrated Postgres
    And vehicle_current_state holds each vehicle's last persisted reading,
      including its recorded_at
    And the comms-loss timeout is 5 seconds
    And comms loss is detected by a background sweep evaluated at a wall-clock
      "now", not inside any ingest transaction (there is no event to ride on)
    And the sweep writes comms_loss anomalies into the same anomalies table the
      other rules use, so GET /anomalies returns them unchanged

  # ── Core by-absence rule ────────────────────────────────────────────────────

  Scenario: A vehicle silent for longer than the timeout raises a comms_loss anomaly
    Given vehicle "agv-1" last reported at time T
    When the watchdog sweep runs at T plus 6 seconds
    Then a comms_loss anomaly is written for "agv-1"
    And it is detected at the sweep's now

  Scenario: A vehicle reporting within the timeout raises no comms_loss anomaly
    Given vehicle "agv-2" last reported at time T
    When the watchdog sweep runs at T plus 3 seconds
    Then no comms_loss anomaly is written for "agv-2"

  Scenario: A vehicle silent for exactly the timeout does not yet raise comms_loss
    Given vehicle "agv-3" last reported at time T
    When the watchdog sweep runs at exactly T plus 5 seconds
    Then no comms_loss anomaly is written for "agv-3"

  # ── Fire once per silence episode (idempotent across sweeps) ─────────────────

  Scenario: A continuing silence does not re-fire comms_loss on every sweep
    Given vehicle "agv-4" last reported at time T and is already flagged comms_loss
    When the watchdog sweep runs again later while "agv-4" is still silent
    Then no additional comms_loss anomaly is written for "agv-4"
    And exactly one comms_loss anomaly exists for "agv-4" for that silence

  Scenario: A vehicle that reports again and then goes silent can be flagged afresh
    Given vehicle "agv-5" was flagged comms_loss during an earlier silence
    And "agv-5" then reports a new telemetry event
    When "agv-5" goes silent again past the timeout and the sweep runs
    Then a second comms_loss anomaly is written for "agv-5"

  # ── Reachability over the read path ─────────────────────────────────────────

  Scenario: A detected comms-loss gap is queryable over GET /anomalies
    Given vehicle "agv-6" reported once and then went silent past the timeout
    And the watchdog sweep has run and flagged it
    When a client GETs "/anomalies" for "agv-6" over a covering window
    Then the response status is 200
    And a comms_loss anomaly for "agv-6" appears in the body

  # ── Separation from the stateless ingestion API ─────────────────────────────

  Scenario: The watchdog runs outside the stateless ingestion request path
    Given the ingestion API stays validate → detect → write → return with no
      background state, per the telemetry-architecture standard
    When comms loss is detected
    Then it is produced by a separate background watchdog process, not by the
      ingestion API request handler
    And the comms_loss row is the only signal — there is no in-process buffer that
      could diverge from committed state
