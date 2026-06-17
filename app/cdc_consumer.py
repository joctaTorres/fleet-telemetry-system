"""The singleton CDC consumer: WAL -> pgoutput decode -> Redis fan-out source.

This is the sole *producer* on the ``fleet:events`` channel. It taps the
primary's write-ahead log through a single ``pgoutput`` logical replication slot
bound to :data:`app.cdc.PUBLICATION_NAME`, decodes the binary
Begin/Relation/Insert/Update/Commit messages itself, translates each watched
table's row change into the :mod:`app.events` envelope, and publishes the JSON to
Redis. Because pgoutput frames changes by ``Begin``/``Commit``, only *committed*
transactions ever emit an event — the event stream is a deterministic function of
the committed WAL, never a dual-write, and an aborted write produces nothing.

The replication protocol (``START_REPLICATION ... LOGICAL``, the CopyBoth stream
of XLogData/keepalive messages, and the standby status-update feedback that
advances the slot's confirmed-flush position) is driven directly against libpq
through psycopg's ``pq`` layer — no extra dependency. The consumer runs either
on a background thread for the duration of a test (via the conftest fixture) or
as its own long-lived ``cdc`` compose service through :func:`run_forever` /
:func:`main` (``python -m app.cdc_consumer``), which supervises a single
:class:`CdcConsumer` with bounded-backoff restart and clean SIGTERM/SIGINT
shutdown. The decode/translate/publish core is identical in both; the supervisor
is purely a process wrapper.
"""

from __future__ import annotations

import json
import logging
import select
import signal
import struct
import threading
import time

import psycopg
import redis as redis_sync
from psycopg import pq
from psycopg.conninfo import make_conninfo

from .cdc import PUBLICATION_NAME, SLOT_NAME, TABLE_EVENT_TYPES
from .config import get_dsn, get_redis_url
from .events import EVENT_CHANNEL

#: Seconds between the unix epoch and the Postgres epoch (2000-01-01), used to
#: stamp standby status-update feedback messages.
_PG_EPOCH_DELTA = 946_684_800

#: Sentinel for an UPDATE column reported as "unchanged TOAST" by pgoutput. None
#: of the watched columns are TOAST-able, so this never reaches a payload.
_UNCHANGED = object()


class _Reader:
    """Cursor over a pgoutput message buffer, reading big-endian network fields."""

    __slots__ = ("buf", "pos")

    def __init__(self, buf: bytes) -> None:
        self.buf = buf
        self.pos = 0

    def int8(self) -> int:
        v = self.buf[self.pos]
        self.pos += 1
        return v

    def char(self) -> bytes:
        b = self.buf[self.pos : self.pos + 1]
        self.pos += 1
        return b

    def int16(self) -> int:
        (v,) = struct.unpack_from("!h", self.buf, self.pos)
        self.pos += 2
        return v

    def int32(self) -> int:
        (v,) = struct.unpack_from("!i", self.buf, self.pos)
        self.pos += 4
        return v

    def int64(self) -> int:
        (v,) = struct.unpack_from("!q", self.buf, self.pos)
        self.pos += 8
        return v

    def string(self) -> str:
        end = self.buf.index(0, self.pos)
        s = self.buf[self.pos : end].decode("utf-8")
        self.pos = end + 1
        return s

    def take(self, n: int) -> bytes:
        b = self.buf[self.pos : self.pos + n]
        self.pos += n
        return b


def ensure_slot() -> None:
    """Ensure the pgoutput logical slot exists on the primary (create if absent).

    A logical slot is the single-reader construct the architecture mandates; it
    reserves WAL from its creation point so the consumer can stream every change
    committed afterwards. Created via the SQL function on an ordinary connection —
    idempotent, so a restart with the slot already present is a no-op.
    """
    with psycopg.connect(get_dsn(), autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_replication_slots WHERE slot_name = %s",
            (SLOT_NAME,),
        ).fetchone()
        if exists is None:
            conn.execute(
                "SELECT pg_create_logical_replication_slot(%s, 'pgoutput')",
                (SLOT_NAME,),
            )


def drop_slot() -> None:
    """Drop the logical slot if it exists (test setup/teardown helper).

    Dropping releases all WAL the slot was retaining. Used by the proof fixture to
    start each test from a slot positioned *after* the per-test cleanup, so cleanup
    writes are never decoded into spurious events.
    """
    with psycopg.connect(get_dsn(), autocommit=True) as conn:
        conn.execute(
            "SELECT pg_drop_replication_slot(slot_name) "
            "FROM pg_replication_slots WHERE slot_name = %s",
            (SLOT_NAME,),
        )


class CdcConsumer:
    """Streams the logical slot, decodes pgoutput, and publishes derived events.

    One instance == one reader of the slot. :meth:`run` blocks on a background
    thread until ``stop`` is set; :attr:`streaming` is set once
    ``START_REPLICATION`` has put the connection into CopyBoth so a caller can
    wait until the consumer is actively tailing before producing writes.
    """

    def __init__(self) -> None:
        self.streaming = threading.Event()
        #: OID -> (relation name, [column names in table order]); filled as
        #: Relation messages arrive, before the Insert/Update that references them.
        self._relations: dict[int, tuple[str, list[str]]] = {}
        self._last_lsn = 0
        self._last_feedback = 0.0
        self._pgconn: pq.PGconn | None = None
        self._redis: redis_sync.Redis | None = None

    # ── lifecycle ──────────────────────────────────────────────────────────
    def run(self, stop: threading.Event) -> None:
        """Open the replication stream and pump it until ``stop`` is set."""
        self._redis = redis_sync.Redis.from_url(get_redis_url())
        ensure_slot()
        conninfo = make_conninfo(get_dsn(), replication="database")
        pgconn = pq.PGconn.connect(conninfo.encode())
        if pgconn.status != pq.ConnStatus.OK:
            raise RuntimeError(
                f"replication connection failed: {pgconn.error_message.decode()}"
            )
        self._pgconn = pgconn
        try:
            self._start_replication()
            self._pump(stop)
        finally:
            try:
                self._send_feedback(force=True)
            except Exception:  # noqa: BLE001 - best-effort on the way out
                pass
            pgconn.finish()
            self._redis.close()

    def _start_replication(self) -> None:
        cmd = (
            f"START_REPLICATION SLOT {SLOT_NAME} LOGICAL 0/0 "
            f"(proto_version '1', publication_names '{PUBLICATION_NAME}')"
        )
        res = self._pgconn.exec_(cmd.encode())
        if res.status != pq.ExecStatus.COPY_BOTH:
            raise RuntimeError(
                f"START_REPLICATION did not enter CopyBoth: "
                f"{self._pgconn.error_message.decode()}"
            )
        self._last_feedback = time.time()
        self.streaming.set()

    def _pump(self, stop: threading.Event) -> None:
        pgconn = self._pgconn
        while not stop.is_set():
            ret, data = pgconn.get_copy_data(1)  # async: 0 == nothing buffered yet
            if ret == -1:  # stream ended
                break
            if ret == -2:  # error
                raise RuntimeError(
                    f"replication stream error: {pgconn.error_message.decode()}"
                )
            if ret == 0:
                # Nothing decoded yet: wait for the socket, pull bytes, retry. The
                # short timeout doubles as the periodic-feedback / stop cadence.
                readable, _, _ = select.select([pgconn.socket], [], [], 0.2)
                if readable:
                    pgconn.consume_input()
                self._send_feedback()
                continue
            self._on_copy_message(bytes(data))
            self._send_feedback()

    # ── replication-protocol framing ───────────────────────────────────────
    def _on_copy_message(self, msg: bytes) -> None:
        """Dispatch one CopyData payload: XLogData ('w') or keepalive ('k')."""
        tag = msg[:1]
        if tag == b"w":  # XLogData: 1 + Int64 start + Int64 end + Int64 time
            wal_end = struct.unpack_from("!q", msg, 9)[0]
            self._last_lsn = max(self._last_lsn, wal_end)
            self._decode_pgoutput(msg[25:])
        elif tag == b"k":  # Primary keepalive: Int64 end + Int64 time + reply flag
            wal_end, _ts, reply = struct.unpack_from("!qqB", msg, 1)
            self._last_lsn = max(self._last_lsn, wal_end)
            if reply:
                self._send_feedback(force=True)

    def _send_feedback(self, force: bool = False) -> None:
        """Confirm the processed LSN so the slot's confirmed-flush advances.

        Reporting the received position as written/flushed/applied lets the server
        release WAL up to it — honoring the operational-safety guideline against
        unbounded slot growth. Rate-limited to ~1s unless ``force`` (a keepalive
        asked for a reply, or we are shutting down).
        """
        now = time.time()
        if not force and now - self._last_feedback < 1.0:
            return
        micros = int((now - _PG_EPOCH_DELTA) * 1_000_000)
        lsn = self._last_lsn
        # 'r' standby status update: written, flushed, applied LSN, clock, reply=0.
        buf = b"r" + struct.pack("!qqqqB", lsn, lsn, lsn, micros, 0)
        self._pgconn.put_copy_data(buf)
        self._pgconn.flush()
        self._last_feedback = now

    # ── pgoutput decode + translate ────────────────────────────────────────
    def _decode_pgoutput(self, payload: bytes) -> None:
        r = _Reader(payload)
        tag = r.char()
        if tag == b"R":  # Relation: cache OID -> (name, columns)
            self._cache_relation(r)
        elif tag == b"I":  # Insert
            oid = r.int32()
            r.char()  # 'N' new-tuple marker
            self._emit(oid, self._read_tuple(r, oid))
        elif tag == b"U":  # Update
            oid = r.int32()
            kind = r.char()
            if kind in (b"K", b"O"):  # old (key/full) tuple precedes the new one
                self._read_tuple(r, oid)
                kind = r.char()
            # kind is now 'N': the new tuple
            self._emit(oid, self._read_tuple(r, oid))
        # Begin ('B'), Commit ('C'), Delete ('D'), Truncate ('T'), Origin ('O'),
        # Type ('Y'), and proto-v2 stream messages need no action: a watched event
        # is only ever an Insert/Update of a watched table, and the Begin/Commit
        # framing already guarantees we are inside a committed transaction.

    def _cache_relation(self, r: _Reader) -> None:
        oid = r.int32()
        r.string()  # namespace (always 'public' here)
        relname = r.string()
        r.int8()  # replica identity setting
        ncols = r.int16()
        columns: list[str] = []
        for _ in range(ncols):
            r.int8()  # column flags (1 == part of the key)
            columns.append(r.string())
            r.int32()  # type OID
            r.int32()  # type modifier
        self._relations[oid] = (relname, columns)

    def _read_tuple(self, r: _Reader, oid: int) -> dict[str, object]:
        """Decode a TupleData into ``{column_name: value}`` (text representation)."""
        _relname, columns = self._relations[oid]
        ncols = r.int16()
        values: dict[str, object] = {}
        for i in range(ncols):
            kind = r.char()
            name = columns[i]
            if kind == b"n":  # null
                values[name] = None
            elif kind == b"u":  # unchanged TOAST
                values[name] = _UNCHANGED
            else:  # 't' text (pgoutput v1 default) or 'b' binary
                length = r.int32()
                values[name] = r.take(length).decode("utf-8")
        return values

    def _emit(self, oid: int, values: dict[str, object]) -> None:
        relname, _columns = self._relations[oid]
        event_type = TABLE_EVENT_TYPES.get(relname)
        if event_type is None:  # not a watched table — nothing to publish
            return
        envelope = {"type": event_type, "payload": _build_payload(relname, values)}
        self._redis.publish(EVENT_CHANNEL, json.dumps(envelope))


def _build_payload(relname: str, v: dict[str, object]) -> dict[str, object]:
    """Translate a decoded row into its event payload, per the watched table."""
    if relname == "vehicle_current_state":
        return {
            "vehicle_id": v["vehicle_id"],
            "status": v["status"],
            "battery_pct": _as_float(v["battery_pct"]),
        }
    if relname == "anomalies":
        return {
            "vehicle_id": v["vehicle_id"],
            "anomaly_type": v["anomaly_type"],
            "detail": v["detail"],
            "detected_at": v["detected_at"],
        }
    # zone_counts
    return {"zone_id": v["zone_id"], "entry_count": _as_int(v["entry_count"])}


def _as_float(value: object) -> float | None:
    return None if value is None or value is _UNCHANGED else float(value)  # type: ignore[arg-type]


def _as_int(value: object) -> int | None:
    return None if value is None or value is _UNCHANGED else int(value)  # type: ignore[arg-type]


# ── long-lived supervisor (the standalone ``cdc`` service) ──────────────────
#: Restart backoff bounds. Short enough that the startup window (publication /
#: watched tables not yet migrated when the service comes up) is absorbed
#: quickly; capped so a prolonged primary outage does not hot-loop.
_BACKOFF_INITIAL = 0.5
_BACKOFF_MAX = 10.0

log = logging.getLogger("app.cdc_consumer")


def _publication_exists() -> bool:
    """True once the pgoutput publication the slot streams has been created.

    The ``cdc`` service starts before migrations run, so on a cold start the
    publication does not exist yet. Creating the slot in that window would anchor
    it *ahead* of the publication and wedge decoding on pre-publication WAL — a
    historic catalog snapshot in which the publication is invisible yields
    ``publication "fleet_events_pub" does not exist`` for every change. The
    supervisor therefore waits for the publication before it ever creates the
    slot, so the slot's consistent point is anchored after it.
    """
    with psycopg.connect(get_dsn(), autocommit=True) as conn:
        row = conn.execute(
            "SELECT 1 FROM pg_publication WHERE pubname = %s",
            (PUBLICATION_NAME,),
        ).fetchone()
    return row is not None


def run_forever(stop: threading.Event | None = None) -> None:
    """Run the singleton CDC consumer as a long-lived, supervised process.

    Loops ``ensure_slot()`` + ``CdcConsumer().run(stop)``, restarting the stream
    with bounded exponential backoff on any transient failure. The most important
    case is the window at container start: the ``cdc`` service comes up at compose
    time, before migrations run, so the publication and watched tables may not
    exist yet. The supervisor waits (without creating the slot) until the
    publication exists — anchoring the slot ahead of it would wedge decoding on
    pre-publication WAL — then creates the slot and streams. A transient failure
    or primary blip mid-stream is retried the same way. Returns only when ``stop``
    is set (clean shutdown).

    Adds **no** decode/translate/publish logic — it is purely the process wrapper
    around the proven :class:`CdcConsumer`.
    """
    if stop is None:
        stop = threading.Event()
    backoff = _BACKOFF_INITIAL
    while not stop.is_set():
        try:
            ready = _publication_exists()
        except Exception as err:  # noqa: BLE001 - primary not reachable yet
            log.warning("cannot reach primary to check publication: %s", err)
            ready = False
        if not ready:
            log.info(
                "publication %s not present yet; waiting before streaming",
                PUBLICATION_NAME,
            )
            if stop.wait(backoff):
                break
            backoff = min(backoff * 2, _BACKOFF_MAX)
            continue

        consumer = CdcConsumer()
        try:
            ensure_slot()
            consumer.run(stop)
        except Exception as err:  # noqa: BLE001 - supervise: log and retry
            log.warning("CDC consumer stream failed, restarting: %s", err)
        if stop.is_set():
            break
        # A consumer that actually started streaming before failing hit a
        # transient mid-stream error: reset to the short backoff. One that never
        # streamed keeps growing its backoff.
        if consumer.streaming.is_set():
            backoff = _BACKOFF_INITIAL
        if stop.wait(backoff):
            break
        backoff = min(backoff * 2, _BACKOFF_MAX)


def _install_signal_handlers(stop: threading.Event) -> None:
    """Set ``stop`` on SIGTERM/SIGINT so the consumer shuts down cleanly.

    Setting the event lets the running :class:`CdcConsumer` exit its pump loop and
    send its final standby status update (advancing the slot's confirmed-flush
    position) so the slot is not left wedged — rather than the process being
    killed mid-stream.
    """

    def _handle(signum: int, _frame: object) -> None:
        log.info("received signal %s; stopping CDC consumer", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)


def main() -> None:
    """Entry point for ``python -m app.cdc_consumer``: supervise until signalled."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    stop = threading.Event()
    _install_signal_handlers(stop)
    log.info("starting CDC consumer supervisor")
    run_forever(stop)
    log.info("CDC consumer supervisor stopped")


if __name__ == "__main__":  # pragma: no cover - container entrypoint
    main()
