"""Connection-pool access to the single Postgres primary.

A lazily-created :class:`psycopg_pool.ConnectionPool` is shared process-wide so
concurrent writers reuse pooled connections instead of opening one per request.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg_pool import ConnectionPool

from .config import get_dsn, get_pool_max_size

_pool: ConnectionPool | None = None


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


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    """Borrow a pooled connection for the duration of the ``with`` block."""
    with get_pool().connection() as conn:
        yield conn


def close_pool() -> None:
    """Close and discard the pool (used by tests for clean teardown)."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
