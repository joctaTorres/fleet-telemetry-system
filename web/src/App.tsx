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

import { memo, useEffect, useMemo, useState } from "react";
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

const WS_OPEN = 1;

/** Connection-health badge shown in the dashboard header. */
const ConnectionIndicator = memo(function ConnectionIndicator({
  connected,
}: {
  connected: boolean;
}) {
  return (
    <span
      className={`connection-indicator ${connected ? "connected" : "disconnected"}`}
      data-testid="connection-indicator"
    >
      <span className="connection-dot" aria-hidden="true" />
      <span className="connection-label">
        {connected ? "Connected" : "Disconnected"}
      </span>
    </span>
  );
});

function EmptyState({ message }: { message: string }) {
  return <p className="empty-message">{message}</p>;
}

export function App({ transport, onRowRender, onTileRender }: AppProps) {
  const resolved = useMemo(
    () => transport ?? createHttpTransport(),
    [transport],
  );

  const [vehicles, setVehicles] = useState<VehicleMap>(EMPTY_VEHICLES);
  const [anomalies, setAnomalies] = useState<AnomalyMap>(EMPTY_ANOMALIES);
  const [zones, setZones] = useState<ZoneMap>(EMPTY_ZONES);
  const [dataStatus, setDataStatus] = useState<"loading" | "error" | "ok">(
    "loading",
  );
  const [announcement, setAnnouncement] = useState("");

  // Initialize connection state from the transport if it exposes readyState;
  // otherwise default to true so eyeballed tests without the hook show healthy.
  const initialConnected =
    typeof resolved.readyState === "number"
      ? resolved.readyState === WS_OPEN
      : true;
  const [connected, setConnected] = useState(initialConnected);

  useEffect(() => {
    let active = true;
    setDataStatus("loading");

    // (a) three one-shot REST snapshots — no interval, no refetch.
    void Promise.all([
      resolved
        .fetchSnapshot()
        .then((snap) => active && setVehicles(seedVehicles(snap))),
      resolved
        .fetchAnomalies()
        .then((snap) => active && setAnomalies(seedAnomalies(snap))),
      resolved
        .fetchZones()
        .then((snap) => active && setZones(seedZones(snap))),
    ])
      .then(() => active && setDataStatus("ok"))
      .catch(() => active && setDataStatus("error"));

    // (b) one live subscription; each delta folds into exactly one store. Unknown
    // ids return the same map reference (vehicles/zones), so React bails out.
    const unsubscribe = resolved.subscribe((event) => {
      switch (event.type) {
        case "vehicle_state_changed":
          setVehicles((current) => applyVehiclePatch(current, event.payload));
          break;
        case "anomaly_detected":
          setAnomalies((current) => applyAnomalyPatch(current, event.payload));
          if (active) {
            setAnnouncement(
              `Anomaly on ${event.payload.vehicle_id}: ${event.payload.anomaly_type}`,
            );
          }
          break;
        case "zone_count_changed":
          setZones((current) => applyZonePatch(current, event.payload));
          break;
      }
    });

    // (c) connection-health indicator driven by the WebSocket readyState, no poll.
    const unsubscribeConnection = resolved.onConnectionChange?.((state) => {
      if (active) setConnected(state);
    });

    return () => {
      active = false;
      unsubscribeConnection?.();
      unsubscribe();
    };
  }, [resolved]);

  const isLoading = dataStatus === "loading";
  const isError = dataStatus === "error";
  const isOk = dataStatus === "ok";

  return (
    <main className="dashboard">
      <header className="dashboard-header">
        <h1 className="dashboard-title">Fleet Telemetry</h1>
        <ConnectionIndicator connected={connected} />
      </header>

      {(isLoading || isError) && (
        <div
          className={`dashboard-message ${isLoading ? "loading-message" : "error-message"}`}
          data-testid={isLoading ? "loading-state" : "error-state"}
        >
          {isLoading
            ? "Loading vehicle and zone data…"
            : "Could not load fleet data. Please try again later."}
        </div>
      )}

      <div className="dashboard-grid">
        <section className="dashboard-section" aria-label="vehicles">
          <h2 className="section-heading">Vehicles</h2>
          {isOk && vehicles.size === 0 ? (
            <EmptyState message="No vehicles are currently available." />
          ) : (
            <VehicleList
              vehicles={vehicles}
              anomalies={anomalies}
              onRowRender={onRowRender}
            />
          )}
        </section>

        <section className="dashboard-section" aria-label="zones">
          <h2 className="section-heading">Zone entries</h2>
          {isOk && zones.size === 0 ? (
            <EmptyState message="No zones are currently configured." />
          ) : (
            <ZoneTiles zones={zones} onTileRender={onTileRender} />
          )}
        </section>
      </div>

      <div
        aria-live="polite"
        aria-atomic="true"
        className="sr-only"
        data-testid="anomaly-announcements"
      >
        {announcement}
      </div>
    </main>
  );
}
