// One zone's tile: zone id + live entry count. Memoized so the grid can
// re-render a single tile in place.
//
// `React.memo` does a shallow compare of props. Because `applyZonePatch` keeps
// every untouched zone's (primitive) count identical and the grid passes a
// *stable* `onRender` callback, only the patched tile's `count` changes — so
// only that tile's body re-runs; every other tile is skipped. The `onRender`
// prop is a test seam read as per-tile render-count evidence.

import { memo } from "react";

export interface ZoneTileProps {
  zoneId: string;
  count: number;
  /** Test-only: fired during render with this tile's id, for render-count proof. */
  onRender?: (zoneId: string) => void;
}

function ZoneTileImpl({ zoneId, count, onRender }: ZoneTileProps) {
  onRender?.(zoneId);
  return (
    <li
      role="listitem"
      className="zone-tile"
      data-testid={`zone-tile-${zoneId}`}
      data-zone-id={zoneId}
    >
      <span className="zone-id">{zoneId}</span>
      <span className="zone-count" data-testid={`zone-count-${zoneId}`}>
        {count}
      </span>
    </li>
  );
}

export const ZoneTile = memo(ZoneTileImpl);
