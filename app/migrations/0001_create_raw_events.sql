-- Append-only log of every telemetry event ever received.
-- Never updated or deleted in the write path; the authoritative per-vehicle
-- view lives in vehicle_current_state (migration 0002).
CREATE TABLE IF NOT EXISTS raw_events (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    vehicle_id  TEXT        NOT NULL,
    status      TEXT        NOT NULL,
    battery_pct DOUBLE PRECISION NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Read patterns: per-vehicle history and time-ordered scans.
CREATE INDEX IF NOT EXISTS idx_raw_events_vehicle_id  ON raw_events (vehicle_id);
CREATE INDEX IF NOT EXISTS idx_raw_events_recorded_at ON raw_events (recorded_at);
