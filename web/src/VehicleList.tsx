// The vehicle list: one memoized `VehicleRow` per vehicle, rendered from the
// `vehicle_id`-keyed `VehicleMap` plus the `vehicle_id`-keyed `AnomalyMap`.
// Presentational — it owns no transport and no state; the dashboard container
// (App) holds the single snapshot-then-stream subscription and folds each
// `vehicle_state_changed` / `anomaly_detected` patch into the matching store,
// then passes both maps down.
//
// Each row reads its current state from `vehicles` and its most-recent anomaly
// from `anomalies` by id. Because both stores keep untouched entries'
// references stable, only the patched row's props change, so the memoized
// `VehicleRow` re-renders alone — never the whole list.
//
// Memoized itself so a zone patch (which leaves `vehicles`/`anomalies`
// referentially unchanged) does not even re-run this list.

import { memo } from "react";
import type { AnomalyMap } from "./anomalyStore";
import type { VehicleMap } from "./vehicleStore";
import { VehicleRow } from "./VehicleRow";

export interface VehicleListProps {
  vehicles: VehicleMap;
  anomalies: AnomalyMap;
  /** Test-only: forwarded to each row as `onRender` for render-count evidence. */
  onRowRender?: (vehicleId: string) => void;
}

function VehicleListImpl({ vehicles, anomalies, onRowRender }: VehicleListProps) {
  return (
    <ul className="vehicle-list" data-testid="vehicle-list">
      {[...vehicles.values()].map((vehicle) => (
        <VehicleRow
          key={vehicle.vehicle_id}
          vehicle={vehicle}
          anomaly={anomalies.get(vehicle.vehicle_id)}
          onRender={onRowRender}
        />
      ))}
    </ul>
  );
}

export const VehicleList = memo(VehicleListImpl);
