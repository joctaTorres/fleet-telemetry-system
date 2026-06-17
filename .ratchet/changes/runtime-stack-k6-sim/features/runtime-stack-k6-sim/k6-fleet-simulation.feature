Feature: k6 simulates a realistic 50-vehicle fleet and asserts the system behaves
  As an operator who wants to prove the running system under real load
  I want a Grafana k6 service that fires on `docker compose up`, drives a
  continuous ~50-vehicle 1 Hz load with each virtual user modelling one stateful
  vehicle, and asserts behavior with checks and thresholds
  So that anomalies and zone counts actually accumulate and "systems behave" is
  measured, not assumed

  Background:
    Given a root-level /k6 directory holding the k6 script(s) and any config or
      Dockerfile
    And a k6 service in the runtime docker-compose.yml using the official
      grafana/k6 image that starts on `up` after the ingestion and frontend APIs
      are healthy
    And the script targets the ingestion API for writes and the frontend API for
      read-back assertions, with both base URLs read from environment variables

  Scenario: k6 drives a continuous 50-vehicle 1 Hz load on up
    Given the runtime stack is up
    When the k6 service starts
    Then it runs 50 virtual users, one per vehicle (v-0 .. v-49), continuously
    And each virtual user emits roughly one telemetry event per second to
      POST /telemetry, sustaining about 50 events per second across the fleet

  Scenario: Each virtual user models one stateful vehicle that moves and drains
    Given a virtual user assigned a single vehicle_id for its whole run
    When it advances one simulation tick
    Then it updates that vehicle's lat/lon and speed_mps so the vehicle moves
    And it drains battery_pct over time
    And its next telemetry event carries the evolved state for that same vehicle,
      so the simulation is stateful per vehicle rather than random per request

  Scenario: Telemetry is emitted in the exact canonical shape
    Given a vehicle that has not just crossed a zone boundary
    When the virtual user emits a telemetry event
    Then the JSON body matches the canonical shape exactly: vehicle_id, an
      ISO-8601 timestamp, lat, lon, battery_pct, speed_mps, status,
      error_codes, and zone_entered
    And zone_entered is null on this event because the vehicle did not cross into
      a zone on this tick

  Scenario: Crossing into a zone sets zone_entered to a realistic zone id
    Given a vehicle whose path periodically crosses a zone boundary
    When the virtual user emits the telemetry event for the tick it crosses in
    Then zone_entered is set to one of the realistic warehouse zone ids (drawn
      from app.models.ZONES, e.g. aisle_b or pick_zone_1), not null
    And subsequent ticks inside or past that zone return zone_entered to null
    And the corresponding zone's entry count increments on the backend

  Scenario: Vehicles converge on charging bays at shift change
    Given the simulation models a shift-change convergence window
    When that window is active
    Then a subset of vehicles head toward and enter the charging_bay_1,
      charging_bay_2, or charging_bay_3 zones and report status "charging"
    And those vehicles' battery_pct recovers while charging rather than draining

  Scenario: Vehicles occasionally fault so anomalies accumulate
    Given the simulation occasionally drives a vehicle into a fault condition
    When a fault occurs
    Then the virtual user either POSTs status "fault" to
      POST /vehicles/{vehicle_id}/status and/or emits telemetry that trips an
      anomaly threshold (battery_pct below 15 while not charging, speed_mps above
      5, stuck below 0.1 m/s while moving for at least 10 s, a teleport jump above
      15 m/s, or a comms-loss gap above 5 s)
    And the backend records anomalies so GET /vehicles/anomalies/latest reflects
      them per vehicle

  Scenario: Checks confirm writes are accepted and reads reflect the load
    Given the k6 run is driving the fleet
    When k6 evaluates its checks
    Then telemetry writes are checked to return 201 from POST /telemetry
    And read endpoints are checked to reflect the load: GET /vehicles returns the
      50 vehicles with status and battery, GET /zones/counts shows entry counts
      growing, and GET /vehicles/anomalies/latest returns anomalies under load

  Scenario: Thresholds gate the run pass/fail
    Given the k6 run has gathered metrics
    When k6 evaluates its thresholds
    Then a threshold on ingest latency (e.g. p95 below a stated bound), a
      threshold on error rate (e.g. below a small fraction), and a threshold on
      check pass rate must all hold for the run to be considered passing
    And a failing threshold causes k6 to exit non-zero so a regression is visible
