-- Extend the per-vehicle current-state row so the *previous* persisted reading
-- carries everything the stateful anomaly rules compare against: speed (stuck),
-- and x/y position in metres (teleport). New columns are nullable / defaulted so
-- existing rows and earlier-phase events remain valid; a first-ever reading has
-- no prior row, so stateful rules simply do not fire.
ALTER TABLE vehicle_current_state
    ADD COLUMN IF NOT EXISTS speed_mps DOUBLE PRECISION NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS pos_x     DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS pos_y     DOUBLE PRECISION;
