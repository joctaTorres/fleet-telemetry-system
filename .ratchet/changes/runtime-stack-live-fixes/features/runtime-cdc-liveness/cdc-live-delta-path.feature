Feature: The runtime CDC consumer goes live and stays live so deltas reach Redis
  As an operator running the full stack with `docker compose up`
  I want the singleton `cdc` service to begin streaming the logical slot once the
  schema, publication and watched tables exist, and to keep retrying until they do
  rather than going silent after one start attempt
  So that committed writes under load become `fleet:events` messages and the live
  dashboard updates instead of showing only the frozen REST snapshot

  Background:
    Given the runtime `docker-compose.yml` brings up `db` (primary, wal_level
      logical), `redis`, a one-shot `migrate` (`python -m app.migrate`) that applies
      migrations and creates the `fleet_events_pub` publication, and the singleton
      `cdc` service (`python -m app.cdc_consumer`)
    And the `ingestion` service already gates its start on the `migrate` step
      completing successfully

  Scenario: The cdc service waits for the migrate step before it starts
    Given the runtime compose `cdc` service declares its dependencies
    When the stack is brought up
    Then `cdc` does not start until `migrate` has exited successfully, exactly as
      `ingestion` does — its `depends_on` includes
      `migrate: { condition: service_completed_successfully }` alongside the `db`
      and `redis` `service_healthy` conditions
    And so the publication, slot-able catalog and watched tables already exist the
      first time the consumer tries to stream

  Scenario: The supervisor keeps retrying until the publication appears
    Given the `cdc` consumer's long-lived supervisor is running
    And the `fleet_events_pub` publication or a watched table is not yet present
    When the supervisor checks for readiness and finds it missing
    Then it logs that it is waiting and retries after a bounded short backoff
    And it never exhausts its retries or exits — the retry/backoff is effectively
      unbounded and long-lived, so it recovers if the publication, slot or tables
      appear late or error transiently

  Scenario: The supervisor recovers from a transient mid-stream failure
    Given the `cdc` consumer is actively streaming the logical slot
    When the replication stream raises a transient error (a primary blip, or a
      table/publication change mid-stream)
    Then the supervisor logs the failure and restarts the stream rather than
      terminating the process
    And once the underlying condition clears, the consumer resumes streaming and
      publishing without operator intervention

  Scenario: Under load the slot advances and events reach Redis
    Given the stack is up and a continuous fleet load is driving committed writes
    When telemetry, status and zone-count writes commit on the primary
    Then `pg_replication_slots.confirmed_flush_lsn` for `fleet_cdc_slot` advances
      across two samples taken under load (it does not stay frozen)
    And a subscriber on the Redis `fleet:events` channel receives at least one real
      event message during the load window

  Scenario: A connected WebSocket client receives a live delta under load
    Given the `frontend` API is serving and subscribed to `fleet:events`
    And a WebSocket client is connected to `ws://localhost:8002/ws`
    When committed writes flow through the CDC path under load
    Then the client receives at least one non-snapshot patch within a few seconds
    And `redis-cli pubsub numsub fleet:events` reports at least one subscriber while
      that client is connected
