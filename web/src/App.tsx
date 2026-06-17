// Dashboard shell and the single data owner. One component holds all three
// `id`-keyed stores (vehicles, latest-anomaly-per-vehicle, per-zone counts) and
// the *single* snapshot-then-stream subscription that drives every surface:
//
//   - on load, three one-shot REST reads seed the three stores — the vehicle
//     snapshot (`/vehicles`), the latest-anomaly snapshot
//     (`/vehicles/anomalies/latest`), and the zone snapshot (`/zones/counts`).
//     There is no interval and no refetch;
//   - thereafter the one `transport.subscribe` folds each delta into exactly one
//     store — `vehicle_state_changed`/`anomaly_detected` by `vehicle_id`,
//     `zone_count_changed` by `zone_id`. No second poll path.
//
// Each store's immutable per-id apply keeps untouched entries' references stable,
// so a patch re-renders only the affected row or tile (the memoized children
// below bail out otherwise) — never the whole list or grid, and never the page.

import { useEffect, useMemo, useState } from "react";
import { applyAnomalyPatch, seedAnomalies, type AnomalyMap } from "./anomalyStore";
import { createHttpTransport, type Transport } from "./transport";
import {
  applyVehiclePatch,
  seedVehicles,
  type VehicleMap,
} from "./vehicleStore";
import { applyZonePatch, seedZones, type ZoneMap } from "./zoneStore";
import { VehicleList } from "./VehicleList";
import { ZoneTiles } from "./ZoneTiles";

export interface AppProps {
  /** Injectable transport; the real app builds an HTTP/WS one same-origin. */
  transport?: Transport;
  /** Test-only: forwarded to each vehicle row for render-count evidence. */
  onRowRender?: (vehicleId: string) => void;
  /** Test-only: forwarded to each zone tile for render-count evidence. */
  onTileRender?: (zoneId: string) => void;
}

const EMPTY_VEHICLES: VehicleMap = new Map();
const EMPTY_ANOMALIES: AnomalyMap = new Map();
const EMPTY_ZONES: ZoneMap = new Map();

export function App({ transport, onRowRender, onTileRender }: AppProps) {
  const resolved = useMemo(() => transport ?? createHttpTransport(), [transport]);

  const [vehicles, setVehicles] = useState<VehicleMap>(EMPTY_VEHICLES);
  const [anomalies, setAnomalies] = useState<AnomalyMap>(EMPTY_ANOMALIES);
  const [zones, setZones] = useState<ZoneMap>(EMPTY_ZONES);

  useEffect(() => {
    let active = true;
    // (a) three one-shot REST snapshots — no interval, no refetch.
    void resolved.fetchSnapshot().then((snap) => {
      if (active) setVehicles(seedVehicles(snap));
    });
    void resolved.fetchAnomalies().then((snap) => {
      if (active) setAnomalies(seedAnomalies(snap));
    });
    void resolved.fetchZones().then((snap) => {
      if (active) setZones(seedZones(snap));
    });
    // (b) one live subscription; each delta folds into exactly one store. Unknown
    // ids return the same map reference (vehicles/zones), so React bails out.
    const unsubscribe = resolved.subscribe((event) => {
      switch (event.type) {
        case "vehicle_state_changed":
          setVehicles((current) => applyVehiclePatch(current, event.payload));
          break;
        case "anomaly_detected":
          setAnomalies((current) => applyAnomalyPatch(current, event.payload));
          break;
        case "zone_count_changed":
          setZones((current) => applyZonePatch(current, event.payload));
          break;
      }
    });
    return () => {
      active = false;
      unsubscribe();
    };
  }, [resolved]);

  return (
    <main>
      <h1>Fleet Telemetry</h1>
      <section aria-label="vehicles">
        <h2>Vehicles</h2>
        <VehicleList
          vehicles={vehicles}
          anomalies={anomalies}
          onRowRender={onRowRender}
        />
      </section>
      <section aria-label="zones">
        <h2>Zone entries</h2>
        <ZoneTiles zones={zones} onTileRender={onTileRender} />
      </section>
    </main>
  );
}
