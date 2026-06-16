-- One row per vehicle holding its latest reading. vehicle_id is the primary
-- key so the write path can upsert with INSERT ... ON CONFLICT (vehicle_id)
-- DO UPDATE — a single server-side statement with no read-then-write window.
-- The fleet aggregate is a GROUP BY over this table, not a materialized counter.
CREATE TABLE IF NOT EXISTS vehicle_current_state (
    vehicle_id  TEXT PRIMARY KEY,
    status      TEXT        NOT NULL
                CHECK (status IN ('idle', 'moving', 'charging', 'fault')),
    battery_pct DOUBLE PRECISION NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Supports GROUP BY status for the fleet-state aggregate.
CREATE INDEX IF NOT EXISTS idx_vehicle_current_state_status
    ON vehicle_current_state (status);
