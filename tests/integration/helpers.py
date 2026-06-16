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


def vehicle_status(vehicle_id: str) -> str | None:
    """Return the authoritative status for a vehicle, or None if no row exists."""
    with psycopg.connect(get_dsn()) as conn:
        row = conn.execute(
            "SELECT status FROM vehicles WHERE vehicle_id = %s",
            (vehicle_id,),
        ).fetchone()
    return row[0] if row is not None else None


def active_mission_count(vehicle_id: str) -> int:
    """Return how many active missions the vehicle currently has."""
    with psycopg.connect(get_dsn()) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM missions "
            "WHERE vehicle_id = %s AND status = 'active'",
            (vehicle_id,),
        ).fetchone()
    return row[0]


def mission_status_counts(vehicle_id: str) -> dict[str, int]:
    """Return per-status mission counts for a vehicle."""
    with psycopg.connect(get_dsn()) as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) FROM missions WHERE vehicle_id = %s "
            "GROUP BY status",
            (vehicle_id,),
        ).fetchall()
    return {status: n for status, n in rows}


def maintenance_record_count(vehicle_id: str) -> int:
    """Return how many maintenance records exist for a vehicle."""
    with psycopg.connect(get_dsn()) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM maintenance_records WHERE vehicle_id = %s",
            (vehicle_id,),
        ).fetchone()
    return row[0]


def maintenance_records(vehicle_id: str) -> list[tuple]:
    """Return (mission_id, reason, resolved_at) for a vehicle's records."""
    with psycopg.connect(get_dsn()) as conn:
        return conn.execute(
            "SELECT mission_id, reason, resolved_at FROM maintenance_records "
            "WHERE vehicle_id = %s ORDER BY id",
            (vehicle_id,),
        ).fetchall()


def seed_vehicle(vehicle_id: str, status: str = "idle") -> None:
    """Insert a vehicles row at the given status."""
    with psycopg.connect(get_dsn()) as conn:
        conn.execute(
            "INSERT INTO vehicles (vehicle_id, status) VALUES (%s, %s)",
            (vehicle_id, status),
        )
        conn.commit()


def seed_active_mission(vehicle_id: str) -> int:
    """Insert an active mission for the vehicle and return its mission_id."""
    with psycopg.connect(get_dsn()) as conn:
        row = conn.execute(
            "INSERT INTO missions (vehicle_id, status) VALUES (%s, 'active') "
            "RETURNING mission_id",
            (vehicle_id,),
        ).fetchone()
        conn.commit()
    return row[0]


def anomaly_types(vehicle_id: str) -> list[str]:
    """Return the anomaly_type values recorded for a vehicle, time-ordered."""
    with psycopg.connect(get_dsn()) as conn:
        rows = conn.execute(
            "SELECT anomaly_type FROM anomalies WHERE vehicle_id = %s "
            "ORDER BY detected_at, id",
            (vehicle_id,),
        ).fetchall()
    return [r[0] for r in rows]
