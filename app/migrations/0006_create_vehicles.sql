-- Authoritative per-vehicle row and the FOR UPDATE lock anchor for the fault
-- transition (per the telemetry-architecture standard and ADR D6). The fault
-- handler serializes all fault handling for a vehicle by taking
-- SELECT 1 FROM vehicles WHERE vehicle_id = $1 FOR UPDATE on this row, then
-- flipping `status` to 'fault' inside the same transaction.
--
-- Intentionally separate from vehicle_current_state (migration 0002), which is
-- the high-churn last-reading upsert/aggregate table — not a lock anchor.
CREATE TABLE IF NOT EXISTS vehicles (
    vehicle_id TEXT PRIMARY KEY,
    status     TEXT        NOT NULL
               CHECK (status IN ('idle', 'moving', 'charging', 'fault'))
               DEFAULT 'idle',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
