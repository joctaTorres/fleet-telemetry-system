Feature: A single docker compose command serves the full runtime stack
  As an operator who wants to run the fleet telemetry system for real use
  I want one `docker compose up` at the repo root to bring up the whole app —
  primary, streaming replica, Redis, the singleton CDC consumer, the ingestion
  API, the frontend API, the served dashboard, and the k6 load generator
  So that the system runs end to end (write path, CDC propagation, read path,
  live dashboard) without any manual wiring, separate from the test harness

  Background:
    Given a runtime docker-compose.yml at the repository root, distinct from the
      untouched docker-compose.test.yml test harness
    And every connection string (DATABASE_URL, REPLICA_URL, REDIS_URL) is read
      from the environment with no credentials hard-coded into application code
    And the Postgres primary runs with wal_level=logical and enough wal senders
      and replication slots for both physical streaming and the logical CDC slot

  Scenario: One up command brings up every service of the runtime topology
    Given a clean checkout with no containers running
    When the operator runs `docker compose up` from the repository root
    Then a Postgres primary, a streaming read replica, and Redis come up healthy
    And the singleton CDC consumer (`python -m app.cdc_consumer`) starts and
      tails the primary's logical replication slot
    And the ingestion API and the frontend API each come up served and listening
    And the dashboard is served and reachable on its host port
    And the k6 load generator starts and begins driving the fleet

  Scenario: Migrations and zone seeding run on startup before the app serves
    Given the primary is healthy but the application schema has not been applied
    When the runtime stack starts
    Then `python -m app.migrate` applies the versioned migrations against the
      primary and seeds one zone_counts row per id in app.models.ZONES
    And the ingestion and frontend APIs only begin serving after the schema and
      zone rows exist, so the first reads return all ~20 seeded zones

  Scenario: The host can reach the APIs and the dashboard on published ports
    Given the runtime stack is up
    When the operator opens the dashboard host port in a browser and calls the
      frontend API host port
    Then the dashboard loads and the frontend API answers GET /vehicles,
      GET /vehicles/anomalies/latest, GET /zones/counts, and the /ws upgrade
    And the ingestion API host port accepts POST /telemetry and
      POST /vehicles/{vehicle_id}/status

  Scenario: The runtime stack preserves the read/write split and CDC-only stream
    Given the runtime stack is up and under load
    When telemetry is written and the dashboard updates
    Then the ingestion API only validates and writes to the primary and never
      publishes to Redis in the request path
    And the frontend API serves its REST reads and connect snapshot from the
      replica, never from the primary
    And the live dashboard deltas originate only from the single CDC consumer
      publishing to Redis, fanned out over WebSocket
