// The per-zone entry-count grid: one memoized `ZoneTile` per zone, rendered from
// the `zone_id`-keyed `ZoneMap`. Presentational — it owns no transport and no
// state; the dashboard container folds each `zone_count_changed` patch into the
// store (via `applyZonePatch`) and passes the resulting map down. Because the
// store keeps untouched zones' counts stable, only the patched tile re-renders;
// the grid is never rebuilt.
//
// Memoized itself so a vehicle/anomaly patch (which leaves `zones` referentially
// unchanged) does not even re-run this grid.

import { memo } from "react";
import type { ZoneMap } from "./zoneStore";
import { ZoneTile } from "./ZoneTile";

export interface ZoneTilesProps {
  zones: ZoneMap;
  /** Test-only: forwarded to each tile as `onRender` for render-count evidence. */
  onTileRender?: (zoneId: string) => void;
}

function ZoneTilesImpl({ zones, onTileRender }: ZoneTilesProps) {
  return (
    <ul className="zone-tiles" data-testid="zone-tiles">
      {[...zones.entries()].map(([zoneId, count]) => (
        <ZoneTile
          key={zoneId}
          zoneId={zoneId}
          count={count}
          onRender={onTileRender}
        />
      ))}
    </ul>
  );
}

export const ZoneTiles = memo(ZoneTilesImpl);
