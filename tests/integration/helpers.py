"""Small query helpers shared across integration tests."""

from __future__ import annotations

import psycopg

from app.config import get_dsn


def count_raw_events(vehicle_id: str) -> int:
    with psycopg.connect(get_dsn()) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM raw_events WHERE vehicle_id = %s",
            (vehicle_id,),
        ).fetchone()
    return row[0]


def current_state(vehicle_id: str) -> tuple | None:
    """Return (status, battery_pct) for a vehicle, or None if no row exists."""
    with psycopg.connect(get_dsn()) as conn:
        return conn.execute(
            "SELECT status, battery_pct FROM vehicle_current_state "
            "WHERE vehicle_id = %s",
            (vehicle_id,),
        ).fetchone()


def current_state_row_count(vehicle_id: str) -> int:
    with psycopg.connect(get_dsn()) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM vehicle_current_state WHERE vehicle_id = %s",
            (vehicle_id,),
        ).fetchone()
    return row[0]


def zone_count(zone_id: str) -> int:
    """Return the live entry_count for a single zone."""
    with psycopg.connect(get_dsn()) as conn:
        row = conn.execute(
            "SELECT entry_count FROM zone_counts WHERE zone_id = %s",
            (zone_id,),
        ).fetchone()
    return row[0]
