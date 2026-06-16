"""Integration-test fixtures: a real, migrated Postgres database.

These tests run against the Postgres service defined in
``docker-compose.test.yml`` (connection via ``DATABASE_URL``). The schema is
created once per session; each test starts from empty tables.
"""

from __future__ import annotations

import time

import psycopg
import pytest

from app.config import get_dsn
from app.db import close_pool
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


@pytest.fixture(scope="session", autouse=True)
def _migrated_db():
    _wait_for_db()
    run_migrations()
    yield
    close_pool()


@pytest.fixture(autouse=True)
def _clean_tables():
    """Reset event tables and zone counters before each test for isolation.

    ``raw_events``, ``vehicle_current_state``, ``anomalies``, and the
    fault-domain tables (``vehicles``, ``missions``, ``maintenance_records``) are
    truncated; ``zone_counts`` keeps its seeded rows (one per known zone) but
    every counter is reset to 0, so each test starts from a freshly-seeded
    baseline.
    """
    with psycopg.connect(get_dsn()) as conn:
        conn.execute(
            "TRUNCATE raw_events, vehicle_current_state, anomalies, "
            "vehicles, missions, maintenance_records"
        )
        conn.execute("UPDATE zone_counts SET entry_count = 0")
        conn.commit()
    yield
