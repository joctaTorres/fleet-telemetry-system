"""Integration-test fixtures: a real, migrated Postgres primary + read replica.

These tests run against the services defined in ``docker-compose.test.yml``: a
Postgres primary (``DATABASE_URL``), a streaming physical read replica
(``REPLICA_URL``), and Redis (``REDIS_URL``). The schema is created once per
session on the primary and streams to the replica; each test starts from empty
tables.
"""

from __future__ import annotations

import json
import queue
import threading
import time

import psycopg
import pytest
import redis as redis_sync

from app.cdc import SLOT_NAME
from app.cdc_consumer import CdcConsumer, drop_slot
from app.config import get_dsn, get_redis_url, get_replica_dsn
from app.db import close_pool
from app.events import EVENT_CHANNEL
from app.migrate import run_migrations


def _wait_for_db(timeout_s: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_s
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(get_dsn(), connect_timeout=3) as conn:
                conn.execute("SELECT 1")
            return
        except Exception as err:  # noqa: BLE001 - retry until ready
            last_err = err
            time.sleep(0.5)
    raise RuntimeError(f"Postgres did not become ready: {last_err}")


def _wait_for_replica(timeout_s: float = 60.0) -> None:
    """Wait until the read replica is a streaming standby with the migrated schema.

    The replica must report ``pg_is_in_recovery()`` true (it is a hot standby) and
    have replayed the migrated schema from the primary (so the frontend connect
    snapshot can read ``zone_counts``). This slice exercises only physical
    streaming replication — there is no dependency on any CDC logical slot here;
    the slot arrives with the CDC follow-on slices.
    """
    deadline = time.monotonic() + timeout_s
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(get_replica_dsn(), connect_timeout=3) as rconn:
                in_recovery = rconn.execute("SELECT pg_is_in_recovery()").fetchone()[0]
                # Confirms the streamed schema has arrived on the standby.
                rconn.execute("SELECT 1 FROM zone_counts LIMIT 1").fetchone()
            if in_recovery:
                return
            last_err = RuntimeError("replica is not in recovery (not a standby)")
        except Exception as err:  # noqa: BLE001 - retry until ready
            last_err = err
        time.sleep(0.5)
    raise RuntimeError(f"read replica did not become ready: {last_err}")


def wait_replica_caught_up(timeout_s: float = 5.0) -> None:
    """Block until the replica has replayed up to the primary's current WAL.

    The frontend reads are served from the streaming standby, which trails the
    primary by a small async-replication lag; a test that writes on the primary
    and then asserts on a replica-derived read waits here first so the assertion
    is against caught-up state rather than racing replication.
    """
    deadline = time.monotonic() + timeout_s
    with psycopg.connect(get_dsn()) as pconn:
        target = pconn.execute("SELECT pg_current_wal_lsn()").fetchone()[0]
    with psycopg.connect(get_replica_dsn()) as rconn:
        while time.monotonic() < deadline:
            replayed = rconn.execute(
                "SELECT pg_last_wal_replay_lsn() >= %s", (target,)
            ).fetchone()[0]
            if replayed:
                return
            time.sleep(0.02)
    raise AssertionError("replica did not catch up to the primary within the bound")


def rolled_back_primary_write(sql: str, params: dict) -> None:
    """Execute ``sql`` on the primary inside a transaction that is rolled back.

    Used by the uncommitted-write proof: physical replication ships WAL, and an
    aborted transaction's changes are never committed, so they never become
    visible on the standby. Uses a raw (non-pooled) connection so the rollback is
    unambiguous.
    """
    with psycopg.connect(get_dsn()) as conn:
        conn.execute(sql, params)
        conn.rollback()


def wait_cdc_service_streaming(timeout_s: float = 30.0) -> None:
    """Block until the standalone ``cdc`` service is actively streaming the slot.

    Polls ``pg_replication_slots`` on the primary for :data:`app.cdc.SLOT_NAME`
    with a live reader (``active = true``). The standalone CDC service comes up at
    compose time, before migrations run, so it may still be in bounded-backoff
    retry (publication / watched tables not yet created) when a proof begins.
    Gating the end-to-end assertion on an active reader keeps it from racing
    service startup. Used only by the phase proof, which exercises the real
    service rather than the in-process ``cdc_stream`` fixture.
    """
    deadline = time.monotonic() + timeout_s
    last: object = None
    with psycopg.connect(get_dsn(), autocommit=True) as conn:
        while time.monotonic() < deadline:
            row = conn.execute(
                "SELECT active FROM pg_replication_slots WHERE slot_name = %s",
                (SLOT_NAME,),
            ).fetchone()
            last = row
            if row is not None and row[0]:
                return
            time.sleep(0.1)
    raise RuntimeError(
        f"standalone CDC service is not streaming slot {SLOT_NAME!r} "
        f"(last pg_replication_slots row: {last}); is the `cdc` service up?"
    )


def wait_frontend_subscribed(timeout_s: float = 5.0) -> None:
    """Block until a frontend Redis subscriber is attached to the event channel.

    The frontend's lifespan ``SUBSCRIBE`` completes asynchronously after the app
    starts; the phase proof must not ``POST`` before it is attached, because Redis
    pub/sub does not buffer and the CDC service (not the test) is the publisher, so
    an early delta would be lost with no way to replay it. Polls
    ``PUBSUB NUMSUB`` until at least one subscriber is present.
    """
    client = redis_sync.Redis.from_url(get_redis_url())
    try:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            numsub = client.pubsub_numsub(EVENT_CHANNEL)
            if numsub and numsub[0][1] >= 1:
                return
            time.sleep(0.02)
    finally:
        client.close()
    raise AssertionError("frontend Redis subscriber never attached to the channel")


class WsReader:
    """A single background reader draining a ``TestClient`` WebSocket to a queue.

    ``TestClient.receive_json`` blocks with no timeout — the threaded-receive
    pattern from ``test_ws_fanout.py``. Spawning a *fresh* blocking receive per
    poll is unsafe across polls: a timed-out poll leaves its thread still blocked
    on ``receive_json``, and a later message can be stolen by that orphan rather
    than reaching the active poll. So one reader thread runs for the connection's
    lifetime and every delta lands in one queue; pollers read from the queue. The
    thread is a daemon, so a deliberate "no delta" wait never blocks exit.
    """

    def __init__(self, ws) -> None:
        self._q: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._run, args=(ws,), daemon=True)
        self._thread.start()

    def _run(self, ws) -> None:
        try:
            while True:
                self._q.put(ws.receive_json())
        except Exception:  # noqa: BLE001 - connection closed; stop reading
            return

    def next_within(self, timeout_s: float) -> dict | None:
        """Return the next delta within ``timeout_s``, else ``None``."""
        try:
            return self._q.get(timeout=timeout_s)
        except queue.Empty:
            return None

    def matching(self, predicate, timeout_s: float) -> dict | None:
        """Return the first delta satisfying ``predicate`` within ``timeout_s``.

        A single committed ``POST`` can derive more than one event (a ``fault``
        reading both upserts current state *and* records a ``fault_status``
        anomaly), and the autouse table reset publishes zone-counter resets around
        each test. This drains intervening deltas and returns only the one the
        proof asserts on, or ``None`` if none arrives within the (sub-second)
        bound — measured against a single deadline across all reads.
        """
        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            event = self.next_within(remaining)
            if event is None:
                return None
            if predicate(event):
                return event


@pytest.fixture(scope="session", autouse=True)
def _migrated_db():
    _wait_for_db()
    run_migrations()
    _wait_for_replica()
    yield
    close_pool()


@pytest.fixture(autouse=True)
def _clean_tables():
    """Reset event tables and zone counters before each test for isolation.

    ``raw_events``, ``vehicle_current_state``, ``anomalies``, and the
    fault-domain tables (``vehicles``, ``missions``, ``maintenance_records``) are
    truncated; ``zone_counts`` keeps its seeded rows (one per known zone) but
    every counter is reset to 0, so each test starts from a freshly-seeded
    baseline. All writes go to the primary and stream to the replica.
    """
    with psycopg.connect(get_dsn()) as conn:
        conn.execute(
            "TRUNCATE raw_events, vehicle_current_state, anomalies, "
            "vehicles, missions, maintenance_records"
        )
        conn.execute("UPDATE zone_counts SET entry_count = 0")
        conn.commit()
    yield


@pytest.fixture
def redis_client():
    """A synchronous Redis client for the *test side* to publish state patches.

    The frontend API subscribes asynchronously inside its lifespan; the test
    stands in for the not-yet-built CDC consumer by publishing patches directly
    on the channel. The keyspace is flushed before and after each test so no
    message survives between tests.
    """
    client = redis_sync.Redis.from_url(get_redis_url())
    client.flushdb()
    yield client
    client.flushdb()
    client.close()


def publish_event(client: redis_sync.Redis, event: dict) -> int:
    """Publish a JSON event envelope on the fleet channel.

    Returns the number of subscribers that received it (Redis ``PUBLISH``
    reply), letting a test confirm the frontend subscriber is attached.
    """
    return client.publish(EVENT_CHANNEL, json.dumps(event))


class CdcEventStream:
    """A running CDC consumer plus a Redis subscriber reading what it publishes.

    The consumer decodes the WAL on a background thread and publishes derived
    event envelopes to ``fleet:events``; this object listens on that channel so a
    test can pull the next envelope. The subscription is attached *before* the
    test writes, and Redis pub/sub does not buffer, so every event the consumer
    produces during the test is captured.
    """

    def __init__(self, pubsub: redis_sync.client.PubSub) -> None:
        self._pubsub = pubsub

    def read_next(self, timeout_s: float = 2.0) -> dict | None:
        """Return the next event envelope within ``timeout_s``, else ``None``."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            message = self._pubsub.get_message(
                ignore_subscribe_messages=True, timeout=0.05
            )
            if message is not None and message.get("type") == "message":
                data = message["data"]
                if isinstance(data, bytes):
                    data = data.decode()
                return json.loads(data)
        return None


@pytest.fixture
def cdc_stream():
    """Run the singleton CDC consumer in-process for the duration of one test.

    The slot is dropped and re-created fresh on consumer startup, *after* the
    autouse ``_clean_tables`` reset has committed, so the slot's restart position
    is past the cleanup and those writes never decode into spurious events. The
    consumer streams on a daemon thread; we wait until it has issued
    ``START_REPLICATION`` (``streaming`` set) before yielding, so a write in the
    test body is guaranteed to be tailed. Teardown stops the thread and drops the
    slot so no WAL is retained between tests.
    """
    drop_slot()  # start each test from a fresh slot positioned after cleanup
    client = redis_sync.Redis.from_url(get_redis_url())
    client.flushdb()
    pubsub = client.pubsub()
    pubsub.subscribe(EVENT_CHANNEL)
    # Drain the subscribe confirmation so the channel is live before any write.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if pubsub.get_message(timeout=0.1) is not None:
            break

    stop = threading.Event()
    consumer = CdcConsumer()
    error: list[BaseException] = []

    def _run() -> None:
        try:
            consumer.run(stop)
        except BaseException as err:  # noqa: BLE001 - surfaced after join
            error.append(err)

    thread = threading.Thread(target=_run, name="cdc-consumer", daemon=True)
    thread.start()
    if not consumer.streaming.wait(timeout=15):
        stop.set()
        raise RuntimeError(
            f"CDC consumer did not start streaming: "
            f"{error[0] if error else 'timed out'}"
        )

    try:
        yield CdcEventStream(pubsub)
    finally:
        stop.set()
        thread.join(timeout=10)
        pubsub.close()
        client.close()
        drop_slot()
        if error:
            raise error[0]
