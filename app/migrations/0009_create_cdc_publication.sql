-- CDC publication for the singleton pgoutput consumer. Names EXACTLY the three
-- watched tables — never raw_events, missions, maintenance_records, vehicles, or
-- schema_migrations — so the logical slot streams only the changes that derive a
-- vehicle_state_changed / anomaly_detected / zone_count_changed event.
--
-- CREATE PUBLICATION has no IF NOT EXISTS, so the create is guarded for
-- idempotency: a re-run of the migration is a no-op once the publication exists.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_publication WHERE pubname = 'fleet_events_pub'
    ) THEN
        CREATE PUBLICATION fleet_events_pub
            FOR TABLE vehicle_current_state, anomalies, zone_counts;
    END IF;
END$$;

-- The two watched tables that receive UPDATEs (the zone counter increment and
-- the per-vehicle current-state upsert) must emit the key columns the translator
-- reads — zone_id and vehicle_id — in the replicated tuple. REPLICA IDENTITY FULL
-- guarantees every column is present on every UPDATE, so the decoder always has
-- zone_id/entry_count and vehicle_id/status/battery_pct without depending on
-- which columns happened to change. anomalies is insert-only, so its default
-- identity is sufficient. Setting the same identity again is a no-op (idempotent).
ALTER TABLE vehicle_current_state REPLICA IDENTITY FULL;
ALTER TABLE zone_counts REPLICA IDENTITY FULL;
