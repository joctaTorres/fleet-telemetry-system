Feature: k6 drives a continuous ~50-vehicle 1 Hz load that never stops on its own
  As an operator who wants the running system observably live under load
  I want the `k6` service to stream a steady ~50-vehicle, 1 Hz fleet load
  continuously on `docker compose up`, rather than running one finite batch and
  exiting
  So that committed writes — and therefore the CDC deltas, Redis events, and live
  dashboard updates — keep flowing for as long as the stack is up

  Background:
    Given a root-level `/k6` directory holding the fleet simulation script
    And a `k6` service in the runtime `docker-compose.yml` using the official
      `grafana/k6` image that fires on `up` once the ingestion and frontend APIs
      are healthy, with target base URLs read from environment variables

  Scenario: The k6 executor is continuous, not a finite one-shot batch
    Given the k6 script's `options.scenarios`
    When the executor is inspected
    Then it is a continuous executor (for example `constant-vus` with an
      effectively unbounded duration, or `constant-arrival-rate` at ~50 iters/s),
      not `per-vu-iterations` with a fixed iteration count
    And the load sustains roughly 50 telemetry events per second across the fleet
      for as long as it runs

  Scenario: The k6 service keeps running while the stack is up
    Given the runtime stack is up
    When a minute has passed since `k6` started
    Then the `k6` service is still `Up` and still driving load, rather than having
      exited after a single batch
    And it is configured to keep driving (a long/effectively-unbounded duration
      and/or a restart policy) so the load does not stop on its own

  Scenario: Each unit of load is still a stateful per-vehicle tick
    Given the continuous load is running
    When a vehicle's tick is emitted
    Then it carries that vehicle's evolving state (position, speed, battery,
      status) across ticks for the 50 vehicles `v-0 .. v-49`, moving and draining
      on a normal tick — the per-vehicle statefulness is preserved, not reduced to
      random per-request fire

  Scenario: Telemetry keeps the deployed canonical shape and field names
    Given a vehicle emits a telemetry event under the continuous load
    When the request body is formed
    Then it uses the deployed ingestion model's field names exactly —
      `recorded_at` for the ISO-8601 timestamp and `pos_x`/`pos_y` for the
      position — which are intentional and accepted as 201 by the served
      `POST /telemetry`
    And those field names are NOT renamed to `timestamp`/`lat`/`lon`

  Scenario: Crossings, shift-change charging, and faults still occur under load
    Given the continuous load is running
    When the simulation advances over time
    Then vehicles still periodically cross into realistic warehouse zone ids drawn
      from `app.models.ZONES` so seeded zone counts grow
    And a subset still converges on the `charging_bay_*` zones at shift change and
      recovers battery while reporting status "charging"
    And vehicles still occasionally fault so anomalies accumulate

  Scenario: Checks and thresholds still gate the run under continuous load
    Given the continuous k6 run is driving the fleet
    When k6 evaluates its checks and thresholds
    Then telemetry writes are still checked to return 201, and the read endpoints
      `GET /vehicles`, `GET /zones/counts`, `GET /vehicles/anomalies/latest` are
      still checked to reflect the load
    And thresholds on ingest latency, error rate, and check pass-rate are still
      defined so a breach surfaces as a non-zero result
