// The per-zone entry-count state model: a `zone_id`-keyed map seeded from the
// `/zones/counts` snapshot and advanced by `zone_count_changed` patches
// (last-write-wins per zone).
//
// Mirrors `vehicleStore.ts` exactly. The set of zones is fixed (the backend seed
// guarantees a row per known zone), so this is a closed universe like the fleet:
// a patch for a known zone replaces only that entry, and a patch for an unknown
// zone returns the *same* map reference unchanged. Counts are primitive numbers,
// so an untouched zone's entry compares equal across applies and the memoized
// `ZoneTile` re-renders only the patched tile.

import type { ZoneCountChangedPayload, ZoneCountsSnapshot } from "./transport";

export type ZoneMap = ReadonlyMap<string, number>;

/** Seed a `zone_id`-keyed count map from the `/zones/counts` snapshot. */
export function seedZones(snapshot: ZoneCountsSnapshot): ZoneMap {
  return new Map(Object.entries(snapshot));
}

/**
 * Apply one `zone_count_changed` patch by id, last-write-wins.
 *
 * - Known zone: returns a new map with only that zone's count replaced; every
 *   other zone keeps its (primitive) value, so its tile's props are unchanged.
 * - Unknown zone: returns the **same** map reference unchanged. The grid is the
 *   fixed set of seeded zones; a patch for a zone we never loaded neither drops
 *   nor adds a tile, and the same reference lets React bail out entirely.
 */
export function applyZonePatch(
  current: ZoneMap,
  patch: ZoneCountChangedPayload,
): ZoneMap {
  if (!current.has(patch.zone_id)) {
    return current;
  }
  const next = new Map(current);
  next.set(patch.zone_id, patch.entry_count);
  return next;
}
