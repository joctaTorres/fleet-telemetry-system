// The most-recent-anomaly-per-vehicle state model: a `vehicle_id`-keyed map
// seeded from the anomaly REST snapshot and advanced by `anomaly_detected`
// patches (last-detected-wins per vehicle).
//
// The map is treated immutably, mirroring `vehicleStore.ts`. Applying a patch
// returns a *new* map in which only the patched vehicle's anomaly is a fresh
// reference — every other vehicle's anomaly object keeps its exact prior
// reference. That stability is what lets the memoized `VehicleRow` skip
// re-rendering the other 49 rows when one vehicle's anomaly arrives: the
// granular-apply here is what makes the granular-render possible.
//
// Unlike the fixed fleet in `vehicleStore`, anomalies appear over the vehicle's
// lifetime, so a patch for a vehicle that had no prior anomaly *adds* it (so a
// first-ever anomaly surfaces immediately, per the phase criterion). No phantom
// row results: rows are driven by the vehicle store, never by this map — an
// anomaly for a vehicle absent from the fleet simply renders nowhere.

import type { AnomalyDetectedPayload, AnomalySnapshotRow } from "./transport";

export interface Anomaly {
  vehicle_id: string;
  anomaly_type: string;
  detail: string | null;
  detected_at: string;
}

export type AnomalyMap = ReadonlyMap<string, Anomaly>;

function toAnomaly(row: AnomalyDetectedPayload): Anomaly {
  return {
    vehicle_id: row.vehicle_id,
    anomaly_type: row.anomaly_type,
    detail: row.detail,
    detected_at: row.detected_at,
  };
}

/** Seed a `vehicle_id`-keyed map from the latest-anomaly-per-vehicle snapshot. */
export function seedAnomalies(
  snapshot: readonly AnomalySnapshotRow[],
): AnomalyMap {
  const map = new Map<string, Anomaly>();
  for (const row of snapshot) {
    map.set(row.vehicle_id, toAnomaly(row));
  }
  return map;
}

/**
 * Apply one `anomaly_detected` patch by id, last-detected-wins.
 *
 * Returns a new map in which only the patched vehicle's anomaly is a fresh
 * object (added if the vehicle had none yet, replaced otherwise); every other
 * vehicle's anomaly keeps its object reference, so a memoized row re-renders
 * alone. The patch carries the full anomaly, so a single fold both surfaces a
 * brand-new anomaly and overwrites a stale one.
 */
export function applyAnomalyPatch(
  current: AnomalyMap,
  patch: AnomalyDetectedPayload,
): AnomalyMap {
  const next = new Map(current);
  next.set(patch.vehicle_id, toAnomaly(patch));
  return next;
}
