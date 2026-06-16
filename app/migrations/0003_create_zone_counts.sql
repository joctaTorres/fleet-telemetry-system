-- One row per known zone holding its running entry counter. zone_id is the
-- primary key so the write path advances the counter with a single server-side
-- UPDATE zone_counts SET entry_count = entry_count + 1 WHERE zone_id = $1 — a
-- row-locked read-modify-write inside Postgres with no application-level
-- SELECT-then-UPDATE window, mirroring how vehicle_current_state (migration 0002)
-- keeps concurrency correctness in the database.
CREATE TABLE IF NOT EXISTS zone_counts (
    zone_id     TEXT    PRIMARY KEY,
    entry_count INTEGER NOT NULL DEFAULT 0
);
