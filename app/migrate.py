"""Minimal versioned migration runner.

Applies the ``*.sql`` files in :mod:`app.migrations` in lexicographic order,
recording each applied filename in a ``schema_migrations`` table so re-runs are
idempotent. Run standalone with ``python -m app.migrate``.
"""

from __future__ import annotations

from pathlib import Path

from .db import connection
from .models import ZONES

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# Idempotent: re-runs never duplicate a row (zone_id is the primary key) and
# never reset or lose an existing entry_count.
_SEED_ZONE = """
INSERT INTO zone_counts (zone_id, entry_count)
VALUES (%(zone_id)s, 0)
ON CONFLICT (zone_id) DO NOTHING
"""

_ENSURE_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def _migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def run_migrations() -> list[str]:
    """Apply all pending migrations. Returns the versions newly applied."""
    applied: list[str] = []
    with connection() as conn:
        with conn.transaction():
            conn.execute(_ENSURE_TABLE)
            rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
            done = {r[0] for r in rows}
            for path in _migration_files():
                version = path.name
                if version in done:
                    continue
                conn.execute(path.read_text())
                conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)",
                    (version,),
                )
                applied.append(version)
    seed_zones()
    return applied


def seed_zones() -> None:
    """Seed one ``zone_counts`` row per known zone, idempotently.

    Inserts a row at ``entry_count = 0`` for every id in
    :data:`app.models.ZONES`, skipping any zone already present
    (``ON CONFLICT (zone_id) DO NOTHING``). Safe to run repeatedly: re-runs
    never duplicate a row or reset a live count, so a read of per-zone counts
    always reports all ~20 zones.
    """
    with connection() as conn:
        with conn.transaction():
            for zone_id in ZONES:
                conn.execute(_SEED_ZONE, {"zone_id": zone_id})


if __name__ == "__main__":  # pragma: no cover - manual / container entrypoint
    newly = run_migrations()
    if newly:
        print("Applied migrations:", ", ".join(newly))
    else:
        print("No pending migrations.")
