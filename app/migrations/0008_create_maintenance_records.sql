-- Maintenance records opened when a vehicle transitions to fault. mission_id is
-- the cancelled mission, if any (NULL when the vehicle faulted while idle).
--
-- The partial unique index is the idempotency backstop required by the
-- telemetry-architecture standard: at most one *open* (resolved_at IS NULL)
-- maintenance record per vehicle. Combined with the handler's transition guard,
-- concurrent/duplicate fault events cannot create duplicate open records — the
-- insert uses ON CONFLICT DO NOTHING so a duplicate is a silent no-op. Once a
-- record is resolved (resolved_at set), the partial index frees, so a later
-- re-fault after repair opens a fresh record.
CREATE TABLE IF NOT EXISTS maintenance_records (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    vehicle_id  TEXT        NOT NULL,
    mission_id  BIGINT,
    reason      TEXT,
    opened_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ
);

-- At most one open maintenance record per vehicle (idempotency backstop).
CREATE UNIQUE INDEX IF NOT EXISTS uq_open_maintenance_per_vehicle
    ON maintenance_records (vehicle_id) WHERE resolved_at IS NULL;
