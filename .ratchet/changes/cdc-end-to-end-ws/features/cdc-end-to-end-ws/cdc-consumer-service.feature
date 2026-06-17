Feature: Run the singleton CDC consumer as a long-lived topology service
  As the CDC tier of the telemetry system
  I want the proven CdcConsumer to run as one long-lived process wired into the
  compose topology — supervising its own replication stream, surviving the
  primary not yet being migrated, and shutting down cleanly — rather than only
  as an in-process test thread
  So that the real-time path runs against the production-shaped topology (a
  single reader of the logical slot, no second active consumer), exactly as the
  cdc-pgoutput-translate slice deferred to this follow-on

  Background:
    Given docker-compose.test.yml defines a "cdc" service built from the same
      image as the api, running the singleton consumer with DATABASE_URL and
      REDIS_URL read from the environment (no hard-coded connection string)
    And the consumer reuses the proven decode/translate/publish from
      app/cdc_consumer.py — this slice adds only the long-lived supervisor and
      the compose wiring, changing no decode logic

  Scenario: The consumer runs as a supervised long-lived process
    Given the cdc service is started as its own container
    When it boots
    Then it ensures its pgoutput logical slot exists and begins streaming the WAL
    And it keeps running, tailing the slot, until it is signalled to stop —
      it does not exit after a single transaction

  Scenario: The consumer tolerates the schema not being migrated yet
    Given the cdc service starts before the publication and watched tables exist
      on the primary (migrations run separately)
    When replication cannot yet stream because the publication is absent
    Then the consumer retries with bounded backoff rather than crashing for good
    And once the publication and tables exist it streams and publishes normally

  Scenario: Exactly one consumer reads the slot
    Given the cdc service is attached to the slot and streaming
    When a second reader attempts to start replication on the same slot
    Then it is refused because the slot is already active for one reader
    And the topology relies on this single consumer, never two active readers

  Scenario: The consumer shuts down cleanly
    Given the cdc service is streaming and has processed events up to a WAL
      position
    When the container receives a termination signal
    Then the consumer sends a final standby status update confirming that
      position and stops without leaving the slot wedged
    And WAL up to the acknowledged position is not retained unboundedly

  Scenario: The proof exercises the service, not the in-process test consumer
    Given the phase proof tests/integration/test_realtime_ws.py runs inside the
      api container against the running topology
    When it asserts the POST -> WebSocket path
    Then the events it observes are produced by the standalone cdc service
    And the in-process consumer fixture used by the cdc-pgoutput-translate proof
      is not what produces them
