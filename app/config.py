"""Database connection configuration, read exclusively from the environment.

No credentials or connection strings are hard-coded in source. The DSN is taken
from the ``DATABASE_URL`` environment variable; if it is absent we raise rather
than fall back to a baked-in default.
"""

from __future__ import annotations

import os


class ConfigError(RuntimeError):
    """Raised when required connection configuration is missing."""


def get_dsn() -> str:
    """Return the Postgres DSN from the environment.

    Reads ``DATABASE_URL``. There is intentionally no hard-coded credential or
    connection-string fallback — a missing value is a configuration error.
    """
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        raise ConfigError(
            "DATABASE_URL must be set in the environment; "
            "no DSN or credentials are hard-coded in source."
        )
    return dsn


def get_replica_dsn() -> str:
    """Return the read-replica Postgres DSN from the environment.

    Reads ``REPLICA_URL``. As with :func:`get_dsn`, there is intentionally no
    hard-coded fallback — a missing value is a configuration error. The frontend
    API serves its one-shot connect snapshot from the streaming read replica so
    read load is isolated from the write path (ADR-0001, D1/D5).
    """
    dsn = os.environ.get("REPLICA_URL", "").strip()
    if not dsn:
        raise ConfigError(
            "REPLICA_URL must be set in the environment; "
            "no replica DSN or credentials are hard-coded in source."
        )
    return dsn


def get_redis_url() -> str:
    """Return the Redis URL from the environment.

    Reads ``REDIS_URL``. As with :func:`get_dsn`, there is intentionally no
    hard-coded fallback — a missing value is a configuration error. Redis is the
    pub/sub broker the frontend API subscribes to for real-time fan-out.
    """
    url = os.environ.get("REDIS_URL", "").strip()
    if not url:
        raise ConfigError(
            "REDIS_URL must be set in the environment; "
            "no Redis URL is hard-coded in source."
        )
    return url


def get_pool_max_size() -> int:
    """Maximum connection-pool size, configurable via ``DB_POOL_MAX_SIZE``."""
    return int(os.environ.get("DB_POOL_MAX_SIZE", "20"))
