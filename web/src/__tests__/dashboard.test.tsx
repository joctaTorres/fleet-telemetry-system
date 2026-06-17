// Phase proof — the live dashboard (vehicles + latest-anomaly-per-vehicle +
// per-zone tiles), driven by the single snapshot-then-stream subscription.
//
// Mounts <App> with a *mocked* transport (no backend topology): three one-shot
// REST snapshots seed the three stores, then patches are driven through the
// mock's one WS handler. Asserts:
//   (5.1) the snapshot renders each row's current anomaly and one tile per zone
//         with its count, fetching each snapshot exactly once and polling nothing;
//   (5.2) an anomaly_detected patch updates only the affected row's anomaly —
//         render-count/memo evidence shows other rows and all zone tiles do not
//         re-render and the page is not refreshed;
//   (5.3) a zone_count_changed patch updates only the affected tile — other tiles
//         and all vehicle rows do not re-render and the grid is not rebuilt;
//   (5.4) an unknown-id patch leaves rows/tiles intact (no drop/duplicate); two
//         patches for one id resolve last-write-wins for that row/tile only.

import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import type {
  AnomalySnapshotRow,
  PatchEvent,
  PatchHandler,
  Transport,
  VehicleSnapshotRow,
  ZoneCountsSnapshot,
} from "../transport";

function vehicleSnapshot(n: number): VehicleSnapshotRow[] {
  return Array.from({ length: n }, (_, i) => ({
    vehicle_id: `vehicle-${String(i).padStart(3, "0")}`,
    status: i % 2 === 0 ? "moving" : "idle",
    battery_pct: 50 + (i % 50),
  }));
}

// Latest anomaly for every 5th vehicle (10 of 50 seeded with an anomaly).
function anomalySnapshot(): AnomalySnapshotRow[] {
  const rows: AnomalySnapshotRow[] = [];
  for (let i = 0; i < 50; i += 5) {
    rows.push({
      vehicle_id: `vehicle-${String(i).padStart(3, "0")}`,
      anomaly_type: "low_battery",
      detail: `battery_pct=${i}`,
      detected_at: "2026-06-16T00:00:00Z",
    });
  }
  return rows;
}

// The realistic warehouse zone ids, in the same order as app.models.ZONES, so the
// seeded snapshot uses real ids and a tile's seed count is its index in this list.
const ZONE_IDS = [
  "inbound_dock_a",
  "inbound_dock_b",
  "receiving_staging",
  "aisle_a",
  "aisle_b",
  "aisle_c",
  "high_bay_1",
  "high_bay_2",
  "bulk_storage",
  "pick_zone_1",
  "pick_zone_2",
  "pack_station",
  "sort_belt",
  "outbound_dock_a",
  "outbound_dock_b",
  "shipping_staging",
  "charging_bay_1",
  "charging_bay_2",
  "charging_bay_3",
  "maintenance_bay",
];

function zoneSnapshot(n: number): ZoneCountsSnapshot {
  const zones: ZoneCountsSnapshot = {};
  for (let i = 0; i < n; i++) zones[ZONE_IDS[i]] = i;
  return zones;
}

interface MockTransport {
  transport: Transport;
  emit: (event: PatchEvent) => void;
  fetchSnapshot: ReturnType<typeof vi.fn>;
  fetchAnomalies: ReturnType<typeof vi.fn>;
  fetchZones: ReturnType<typeof vi.fn>;
  subscribed: () => boolean;
}

function makeMockTransport(): MockTransport {
  let handler: PatchHandler | null = null;
  const fetchSnapshot = vi.fn(async () => vehicleSnapshot(50));
  const fetchAnomalies = vi.fn(async () => anomalySnapshot());
  const fetchZones = vi.fn(async () => zoneSnapshot(20));
  return {
    transport: {
      fetchSnapshot,
      fetchAnomalies,
      fetchZones,
      subscribe(h: PatchHandler) {
        handler = h;
        return () => {
          handler = null;
        };
      },
    },
    emit(event: PatchEvent) {
      handler?.(event);
    },
    fetchSnapshot,
    fetchAnomalies,
    fetchZones,
    subscribed: () => handler !== null,
  };
}

interface RenderTallies {
  rows: Map<string, number>;
  tiles: Map<string, number>;
}

/** Mount the dashboard, flushing the three snapshot promises so it is seeded. */
async function mountSeeded(mock: MockTransport): Promise<RenderTallies> {
  const rows = new Map<string, number>();
  const tiles = new Map<string, number>();
  await act(async () => {
    render(
      <App
        transport={mock.transport}
        onRowRender={(id) => rows.set(id, (rows.get(id) ?? 0) + 1)}
        onTileRender={(id) => tiles.set(id, (tiles.get(id) ?? 0) + 1)}
      />,
    );
    // let the three fetch promises + the setStates they trigger flush.
    for (let i = 0; i < 5; i++) await Promise.resolve();
  });
  return { rows, tiles };
}

function deltaCount(now: Map<string, number>, baseline: Map<string, number>) {
  let changed = 0;
  for (const [id, count] of now) {
    if (count !== (baseline.get(id) ?? 0)) changed++;
  }
  return changed;
}

describe("live dashboard", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
  });
  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("renders the REST snapshot: each row's anomaly + one tile per zone, no polling (5.1)", async () => {
    const setIntervalSpy = vi.spyOn(globalThis, "setInterval");
    const mock = makeMockTransport();

    await mountSeeded(mock);

    // 50 vehicle rows; a seeded-anomaly vehicle shows its type, others show none.
    expect(screen.getAllByTestId(/^vehicle-row-/)).toHaveLength(50);
    expect(screen.getByTestId("anomaly-vehicle-000")).toHaveTextContent(
      "low_battery",
    );
    expect(screen.queryByTestId("anomaly-vehicle-001")).toBeNull();

    // one tile per seeded zone, each showing its count.
    expect(screen.getAllByTestId(/^zone-tile-/)).toHaveLength(20);
    expect(screen.getByTestId("zone-count-aisle_b")).toHaveTextContent("4");

    // each snapshot fetched exactly once...
    expect(mock.fetchSnapshot).toHaveBeenCalledTimes(1);
    expect(mock.fetchAnomalies).toHaveBeenCalledTimes(1);
    expect(mock.fetchZones).toHaveBeenCalledTimes(1);
    // ...and no interval refetch even after time passes.
    await act(async () => {
      vi.advanceTimersByTime(60_000);
      await Promise.resolve();
    });
    expect(mock.fetchSnapshot).toHaveBeenCalledTimes(1);
    expect(mock.fetchAnomalies).toHaveBeenCalledTimes(1);
    expect(mock.fetchZones).toHaveBeenCalledTimes(1);
    expect(setIntervalSpy).not.toHaveBeenCalled();
  });

  it("an anomaly_detected patch updates only the affected row (5.2)", async () => {
    const mock = makeMockTransport();
    const { rows, tiles } = await mountSeeded(mock);
    const rowBaseline = new Map(rows);
    const tileBaseline = new Map(tiles);

    // vehicle-003 had no seeded anomaly: a first-ever anomaly must surface live.
    await act(async () => {
      mock.emit({
        type: "anomaly_detected",
        payload: {
          vehicle_id: "vehicle-003",
          anomaly_type: "overspeed",
          detail: "speed_mps=9",
          detected_at: "2026-06-16T01:00:00Z",
        },
      });
      await Promise.resolve();
    });

    // the patched row now shows the new anomaly immediately...
    expect(screen.getByTestId("anomaly-vehicle-003")).toHaveTextContent(
      "overspeed",
    );
    // ...and ONLY that row re-rendered; no other row, no zone tile.
    expect(rows.get("vehicle-003")).toBe(
      (rowBaseline.get("vehicle-003") ?? 0) + 1,
    );
    let otherRows = 0;
    for (const [id, count] of rows) {
      if (id !== "vehicle-003" && count !== rowBaseline.get(id)) otherRows++;
    }
    expect(otherRows).toBe(0);
    expect(deltaCount(tiles, tileBaseline)).toBe(0);
    // page not rebuilt: still 50 rows and 20 tiles.
    expect(screen.getAllByTestId(/^vehicle-row-/)).toHaveLength(50);
    expect(screen.getAllByTestId(/^zone-tile-/)).toHaveLength(20);
  });

  it("a zone_count_changed patch updates only the affected tile (5.3)", async () => {
    const mock = makeMockTransport();
    const { rows, tiles } = await mountSeeded(mock);
    const rowBaseline = new Map(rows);
    const tileBaseline = new Map(tiles);

    await act(async () => {
      mock.emit({
        type: "zone_count_changed",
        payload: { zone_id: "aisle_b", entry_count: 999 },
      });
      await Promise.resolve();
    });

    // the patched tile reflects the new count...
    expect(screen.getByTestId("zone-count-aisle_b")).toHaveTextContent("999");
    // ...and ONLY that tile re-rendered; no other tile, no vehicle row.
    expect(tiles.get("aisle_b")).toBe((tileBaseline.get("aisle_b") ?? 0) + 1);
    let otherTiles = 0;
    for (const [id, count] of tiles) {
      if (id !== "aisle_b" && count !== tileBaseline.get(id)) otherTiles++;
    }
    expect(otherTiles).toBe(0);
    expect(deltaCount(rows, rowBaseline)).toBe(0);
    // grid not rebuilt: still 20 tiles.
    expect(screen.getAllByTestId(/^zone-tile-/)).toHaveLength(20);
  });

  it("ignores unknown ids and is last-write-wins per id (5.4)", async () => {
    const mock = makeMockTransport();
    const { rows, tiles } = await mountSeeded(mock);
    const rowBaseline = new Map(rows);
    const tileBaseline = new Map(tiles);

    // unknown zone id: no tile added/dropped, no tile or row re-render.
    // unknown vehicle id: no phantom row (rows come from the vehicle store).
    await act(async () => {
      mock.emit({
        type: "zone_count_changed",
        payload: { zone_id: "unknown_zone_x", entry_count: 7 },
      });
      mock.emit({
        type: "anomaly_detected",
        payload: {
          vehicle_id: "vehicle-999",
          anomaly_type: "teleport",
          detail: null,
          detected_at: "2026-06-16T02:00:00Z",
        },
      });
      await Promise.resolve();
    });
    expect(screen.getAllByTestId(/^zone-tile-/)).toHaveLength(20);
    expect(screen.queryByTestId("zone-tile-unknown_zone_x")).toBeNull();
    expect(screen.getAllByTestId(/^vehicle-row-/)).toHaveLength(50);
    expect(screen.queryByTestId("vehicle-row-vehicle-999")).toBeNull();
    expect(deltaCount(tiles, tileBaseline)).toBe(0);
    expect(deltaCount(rows, rowBaseline)).toBe(0);

    // two patches for the same id, in order: last write wins for that one
    // row/tile only, with no effect on neighbours.
    await act(async () => {
      mock.emit({
        type: "zone_count_changed",
        payload: { zone_id: "high_bay_2", entry_count: 100 },
      });
      mock.emit({
        type: "zone_count_changed",
        payload: { zone_id: "high_bay_2", entry_count: 200 },
      });
      mock.emit({
        type: "anomaly_detected",
        payload: {
          vehicle_id: "vehicle-010",
          anomaly_type: "stuck",
          detail: null,
          detected_at: "2026-06-16T03:00:00Z",
        },
      });
      mock.emit({
        type: "anomaly_detected",
        payload: {
          vehicle_id: "vehicle-010",
          anomaly_type: "fault_status",
          detail: null,
          detected_at: "2026-06-16T03:01:00Z",
        },
      });
      await Promise.resolve();
    });
    expect(screen.getByTestId("zone-count-high_bay_2")).toHaveTextContent("200");
    expect(screen.getByTestId("anomaly-vehicle-010")).toHaveTextContent(
      "fault_status",
    );
    // neighbours untouched.
    expect(screen.getByTestId("zone-count-high_bay_1")).toHaveTextContent("6");
    expect(screen.getByTestId("anomaly-vehicle-005")).toHaveTextContent(
      "low_battery",
    );
  });
});
