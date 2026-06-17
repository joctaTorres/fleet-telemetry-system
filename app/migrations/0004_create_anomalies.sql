-- Detected anomalies, one row per anomaly fired. Written synchronously inside
-- the same transaction as the raw event and the current-state upsert (per the
-- telemetry-architecture standard), so an anomaly is committed exactly when its
-- threshold is crossed, atomically with the reading that caused it.
CREATE TABLE IF NOT EXISTS anomalies (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    vehicle_id   TEXT        NOT NULL,
    anomaly_type TEXT        NOT NULL,
    detail       TEXT,
    detected_at  TIMESTAMPTZ NOT NULL
);

-- Serves the filtered read seam recent_anomalies(vehicle_id, since, until):
-- an indexed range scan over (vehicle_id, detected_at) rather than a table scan.
CREATE INDEX IF NOT EXISTS idx_anomalies_vehicle_detected
    ON anomalies (vehicle_id, detected_at);
