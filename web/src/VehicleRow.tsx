// One vehicle's row: status + battery. Memoized so the list can re-render a
// single row in place.
//
// `React.memo` does a shallow compare of props. Because `applyVehiclePatch`
// keeps every untouched vehicle's object reference identical, only the patched
// row's `vehicle` prop changes — so only that row's body re-runs; the other 49
// are skipped. The `onRender` prop is a test seam: the list passes a *stable*
// callback, so it never by itself defeats the memo, and the proof reads it as
// per-row render-count evidence.

import { memo } from "react";
import type { Anomaly } from "./anomalyStore";
import type { Vehicle } from "./vehicleStore";

export interface VehicleRowProps {
  vehicle: Vehicle;
  /**
   * This vehicle's most-recent anomaly, or `undefined` when it has none. Because
   * `applyAnomalyPatch` keeps every untouched vehicle's anomaly reference stable
   * (and absent vehicles stay `undefined`), only the patched row's `anomaly`
   * prop changes — so an `anomaly_detected` patch re-renders only that row.
   */
  anomaly?: Anomaly;
  /** Test-only: fired during render with this row's id, for render-count proof. */
  onRender?: (vehicleId: string) => void;
}

function VehicleRowImpl({ vehicle, anomaly, onRender }: VehicleRowProps) {
  onRender?.(vehicle.vehicle_id);
  return (
    <li
      role="listitem"
      data-testid={`vehicle-row-${vehicle.vehicle_id}`}
      data-vehicle-id={vehicle.vehicle_id}
    >
      <span className="vehicle-id">{vehicle.vehicle_id}</span>
      <span
        className="vehicle-status"
        data-testid={`status-${vehicle.vehicle_id}`}
      >
        {vehicle.status}
      </span>
      <span
        className="vehicle-battery"
        data-testid={`battery-${vehicle.vehicle_id}`}
      >
        {vehicle.battery_pct}%
      </span>
      {anomaly ? (
        <span
          className="vehicle-anomaly"
          data-testid={`anomaly-${vehicle.vehicle_id}`}
        >
          {anomaly.anomaly_type}
        </span>
      ) : null}
    </li>
  );
}

export const VehicleRow = memo(VehicleRowImpl);
