"""Connection-pool access to the single Postgres primary.

A lazily-created :class:`psycopg_pool.ConnectionPool` is shared process-wide so
concurrent writers reuse pooled connections instead of opening one per request.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg_pool import ConnectionPool

from .config import get_dsn, get_pool_max_size, get_replica_dsn

_pool: ConnectionPool | None = None
_replica_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    """Return the process-wide connection pool, creating it on first use."""
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=get_dsn(),
            min_size=1,
            max_size=get_pool_max_size(),
            open=True,
        )
    return _pool


def get_replica_pool() -> ConnectionPool:
    """Return the read-replica connection pool, creating it on first use.

    A separate pool against ``REPLICA_URL`` so read traffic (the frontend connect
    snapshot) is served from the streaming standby, isolated from the primary's
    write path. The replica is read-only, so callers must only issue reads.
    """
    global _replica_pool
    if _replica_pool is None:
        _replica_pool = ConnectionPool(
            conninfo=get_replica_dsn(),
            min_size=1,
            max_size=get_pool_max_size(),
            open=True,
        )
    return _replica_pool


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    """Borrow a pooled connection (primary) for the duration of the ``with`` block."""
    with get_pool().connection() as conn:
        yield conn


@contextmanager
def replica_connection() -> Iterator[psycopg.Connection]:
    """Borrow a pooled read-replica connection for the ``with`` block.

    Used by the frontend's connect-snapshot read seams; the replica streams
    physically from the primary, so a committed write is reflected here after a
    small replication lag. Reads only — the standby rejects writes.
    """
    with get_replica_pool().connection() as conn:
        yield conn


def close_pool() -> None:
    """Close and discard both pools (used by tests for clean teardown)."""
    global _pool, _replica_pool
    if _pool is not None:
        _pool.close()
        _pool = None
    if _replica_pool is not None:
        _replica_pool.close()
        _replica_pool = None
