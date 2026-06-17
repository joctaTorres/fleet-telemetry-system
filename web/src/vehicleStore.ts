// The vehicle list's state model: a `vehicle_id`-keyed map seeded from the REST
// snapshot, advanced by `vehicle_state_changed` patches.
//
// The map is treated immutably. Applying a patch returns a *new* map in which
// only the patched vehicle's object is a fresh reference — every untouched
// vehicle keeps its exact prior object reference. That reference stability is
// what lets `React.memo` on the row skip re-rendering the other 49 rows: the
// granular-apply here is what makes the granular-render possible.

import type { VehicleSnapshotRow, VehicleStateChangedPayload } from "./transport";

export interface Vehicle {
  vehicle_id: string;
  status: string;
  battery_pct: number;
}

export type VehicleMap = ReadonlyMap<string, Vehicle>;

/** Seed a `vehicle_id`-keyed map from the REST snapshot (insertion order kept). */
export function seedVehicles(snapshot: readonly VehicleSnapshotRow[]): VehicleMap {
  const map = new Map<string, Vehicle>();
  for (const row of snapshot) {
    map.set(row.vehicle_id, {
      vehicle_id: row.vehicle_id,
      status: row.status,
      battery_pct: row.battery_pct,
    });
  }
  return map;
}

/**
 * Apply one `vehicle_state_changed` patch by id, last-write-wins.
 *
 * - Known id: returns a new map with only that vehicle replaced by a fresh
 *   object (status + battery from the patch); all other rows keep their object
 *   reference, and the key's position is unchanged so row order is stable.
 * - Unknown id: returns the **same** map reference unchanged. The list is the
 *   fixed fleet seeded from the snapshot; a patch for a vehicle we never loaded
 *   neither drops nor duplicates an existing row, and returning the same
 *   reference lets React bail out of re-rendering entirely.
 */
export function applyVehiclePatch(
  current: VehicleMap,
  patch: VehicleStateChangedPayload,
): VehicleMap {
  if (!current.has(patch.vehicle_id)) {
    return current;
  }
  const next = new Map(current);
  next.set(patch.vehicle_id, {
    vehicle_id: patch.vehicle_id,
    status: patch.status,
    battery_pct: patch.battery_pct,
  });
  return next;
}
