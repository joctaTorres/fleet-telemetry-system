"""Streaming-replication lag probe: a custom OTel metric for the read path.

The runtime stack runs a primary ``db`` (logical WAL + physical streaming) and a
streaming hot-standby ``replica`` that the frontend reads from. Nothing else in
the system measures how far the standby trails the primary — the replication
health of the read path is otherwise a black box. This module closes that gap
with a small, self-contained probe that periodically samples replication state
and emits the lag as a custom OTel gauge over the existing OTLP/HTTP -> Alloy ->
Prometheus path.

Two sources of truth, mirroring how the frontend already splits primary/replica:

* **Byte lag** is authoritative on the *primary*: for each connected standby in
  ``pg_stat_replication`` we compute
  ``pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn)`` — the bytes of WAL the
  standby has not yet replayed.
* **Seconds lag** is authoritative on the *replica*: ``now()`` minus
  ``pg_last_xact_replay_timestamp()`` is how far behind in wall-clock time the
  standby's last replayed transaction is.

Like the CDC consumer, the probe is a long-lived loop (not a FastAPI app), so it
calls :func:`app.otel.configure_otel` once at startup (endpoint from the
environment only — no SDK re-wiring) and pulls a meter off the global provider.
The instruments are declared with **no unit** so the OTLP→Prometheus translation
leaves the names as exactly ``pg_replication_lag_bytes`` /
``pg_replication_lag_seconds`` — a dimensionless unit of ``1`` would add a
``_ratio`` suffix, and a byte unit would add a ``_bytes`` suffix, either of which
would break the dashboard binding and the phase proof's
``max(pg_replication_lag_bytes)`` query.

Resilient by construction: every query is read-only monitoring SQL that never
touches the replication slot, and the two sources are sampled independently — a
transient query error or a momentarily-absent standby row records no value for
that sample and never crashes the loop. Safe-by-default: with
``OTEL_EXPORTER_OTLP_ENDPOINT`` unset the bootstrap installs no exporter, so the
process records against the no-op provider and exports nothing.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
from datetime import datetime, timezone

import psycopg
from opentelemetry import metrics

from .config import get_dsn, get_replica_dsn
from .otel import configure_otel

#: Service identity for every metric this process emits. Module constant so the
#: startup wiring and the tests bind to the same value.
SERVICE_NAME = "replication-probe"

#: Seconds between samples; env-overridable so the cadence can be tuned without a
#: code change. Short enough that the dashboards read as "live".
_SAMPLE_INTERVAL = float(os.environ.get("REPLICATION_PROBE_INTERVAL", "5"))

# Module-level meter pulled straight off the shared bootstrap's global. It is a
# proxy until :func:`configure_otel` installs the real provider at process
# startup (in :func:`run_forever`); created here at import it stays a no-op when
# ``OTEL_EXPORTER_OTLP_ENDPOINT`` is unset, so import and pytest are unaffected.
# No exporter/provider wiring is duplicated — all of it lives in ``app.otel``.
_meter = metrics.get_meter(__name__)

# The two lag instruments. Declared with NO unit on purpose: the OTLP→Prometheus
# normalization turns dots into underscores and appends a unit suffix, so a unit
# would corrupt the series name the "Primary/Replica Streaming" dashboard and the
# phase proof bind to. With no unit these land as exactly
# ``pg_replication_lag_bytes`` and ``pg_replication_lag_seconds``. Synchronous
# gauges (set the current value each sample) match the periodic-loop shape.
# Module-level so tests can swap in an in-memory meter.
LAG_BYTES = _meter.create_gauge(
    "pg.replication.lag_bytes",
    description="WAL bytes a standby has not yet replayed (primary view).",
)
LAG_SECONDS = _meter.create_gauge(
    "pg.replication.lag_seconds",
    description="Seconds the standby's last replayed transaction trails now().",
)

log = logging.getLogger("app.replication_probe")


# ── pure lag computations (fed from fixtures in the unit tests) ──────────────
def byte_lag_samples(
    rows: list[tuple[object, object]],
) -> list[tuple[str, int]]:
    """Translate ``pg_stat_replication`` rows into ``(standby, lag_bytes)`` pairs.

    Each input row is ``(application_name, lag_bytes)`` as returned by the primary
    query. A row whose ``lag_bytes`` is ``NULL`` (a standby that has not yet
    reported a ``replay_lsn``) is skipped — no value is recorded for it this
    sample rather than recording a bogus zero. ``application_name`` is carried as
    a label so multiple standbys produce distinct series; an empty/None name
    falls back to ``"standby"``.
    """
    samples: list[tuple[str, int]] = []
    for application_name, lag_bytes in rows:
        if lag_bytes is None:
            continue
        name = str(application_name) if application_name else "standby"
        samples.append((name, int(lag_bytes)))
    return samples


def seconds_lag_value(
    replay_timestamp: datetime | None, now: datetime
) -> float | None:
    """Seconds the standby trails ``now`` given its last replay timestamp.

    ``replay_timestamp`` is ``pg_last_xact_replay_timestamp()`` from the replica
    (``None`` when the standby has replayed no transaction yet, e.g. a brand-new
    standby — in which case there is nothing meaningful to record, so return
    ``None``). The result is floored at ``0.0`` so a small clock skew can never
    surface as a nonsensical negative lag.
    """
    if replay_timestamp is None:
        return None
    return max(0.0, (now - replay_timestamp).total_seconds())


def _now() -> datetime:
    """Current UTC time; isolated so tests can compute deterministic lags."""
    return datetime.now(timezone.utc)


# ── per-source sampling (read-only; one source's failure never blocks the other) ─
def _sample_byte_lag(primary_dsn: str) -> None:
    """Sample byte lag from the primary's ``pg_stat_replication`` and record it.

    Read-only: a plain monitoring query that never touches the replication slot.
    Any failure (primary unreachable, transient error) is swallowed so the loop
    keeps running and the seconds-lag sample is still attempted.
    """
    try:
        with psycopg.connect(primary_dsn, autocommit=True) as conn:
            rows = conn.execute(
                "SELECT application_name, "
                "pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn) AS lag_bytes "
                "FROM pg_stat_replication"
            ).fetchall()
        for name, lag in byte_lag_samples(rows):
            LAG_BYTES.set(lag, {"application_name": name})
    except Exception as err:  # noqa: BLE001 - never crash the probe loop
        log.warning("byte-lag sample skipped (will retry): %s", err)


def _sample_seconds_lag(replica_dsn: str) -> None:
    """Sample seconds lag from the replica's replay timestamp and record it.

    Read-only against the standby; a missing replay timestamp (fresh standby) or
    a transient error records no value for this sample and never crashes the loop.
    """
    try:
        with psycopg.connect(replica_dsn, autocommit=True) as conn:
            row = conn.execute(
                "SELECT pg_last_xact_replay_timestamp(), pg_last_wal_replay_lsn()"
            ).fetchone()
        replay_ts = row[0] if row else None
        lag = seconds_lag_value(replay_ts, _now())
        if lag is not None:
            LAG_SECONDS.set(lag)
    except Exception as err:  # noqa: BLE001 - never crash the probe loop
        log.warning("seconds-lag sample skipped (will retry): %s", err)


def sample_once(primary_dsn: str, replica_dsn: str) -> None:
    """Take one byte-lag + one seconds-lag sample, independently and defensively."""
    _sample_byte_lag(primary_dsn)
    _sample_seconds_lag(replica_dsn)


def run_forever(stop: threading.Event | None = None) -> None:
    """Run the replication-lag probe as a long-lived, supervised loop.

    Installs OTel once through the shared bootstrap (a no-op when
    ``OTEL_EXPORTER_OTLP_ENDPOINT`` is unset; idempotent on restart), reads the
    primary/replica DSNs from the environment, then samples both sources every
    ``REPLICATION_PROBE_INTERVAL`` seconds until ``stop`` is set. Each sample is
    fully defensive (see :func:`sample_once`), so a transient outage on either
    database is retried on the next tick rather than ending the loop.
    """
    if stop is None:
        stop = threading.Event()
    configure_otel(SERVICE_NAME)
    primary_dsn = get_dsn()
    replica_dsn = get_replica_dsn()
    log.info(
        "replication probe sampling every %.1fs (primary + replica)",
        _SAMPLE_INTERVAL,
    )
    while not stop.is_set():
        sample_once(primary_dsn, replica_dsn)
        if stop.wait(_SAMPLE_INTERVAL):
            break


def _install_signal_handlers(stop: threading.Event) -> None:
    """Set ``stop`` on SIGTERM/SIGINT so the probe shuts down cleanly."""

    def _handle(signum: int, _frame: object) -> None:
        log.info("received signal %s; stopping replication probe", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)


def main() -> None:
    """Entry point for ``python -m app.replication_probe``: sample until signalled."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    stop = threading.Event()
    _install_signal_handlers(stop)
    log.info("starting replication probe")
    run_forever(stop)
    log.info("replication probe stopped")


if __name__ == "__main__":  # pragma: no cover - container entrypoint
    main()
