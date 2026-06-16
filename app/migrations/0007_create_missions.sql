-- Vehicle missions. The fault transition cancels a vehicle's active mission by
-- UPDATE ... WHERE vehicle_id = $1 AND status = 'active'. The partial unique
-- index enforces at most one active mission per vehicle, so the cancel targets a
-- single row and a duplicate fault cannot leave two active missions behind.
CREATE TABLE IF NOT EXISTS missions (
    mission_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    vehicle_id TEXT        NOT NULL,
    status     TEXT        NOT NULL
               CHECK (status IN ('active', 'cancelled', 'completed'))
               DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- At most one active mission per vehicle (declarative invariant for the cancel).
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_mission_per_vehicle
    ON missions (vehicle_id) WHERE status = 'active';

-- Backs the cancel lookup UPDATE missions ... WHERE vehicle_id = $1 AND status = ...
CREATE INDEX IF NOT EXISTS idx_missions_vehicle_status
    ON missions (vehicle_id, status);
