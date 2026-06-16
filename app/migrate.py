"""Minimal versioned migration runner.

Applies the ``*.sql`` files in :mod:`app.migrations` in lexicographic order,
recording each applied filename in a ``schema_migrations`` table so re-runs are
idempotent. Run standalone with ``python -m app.migrate``.
"""

from __future__ import annotations

from pathlib import Path

from .db import connection

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

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
    return applied


if __name__ == "__main__":  # pragma: no cover - manual / container entrypoint
    newly = run_migrations()
    if newly:
        print("Applied migrations:", ", ".join(newly))
    else:
        print("No pending migrations.")
