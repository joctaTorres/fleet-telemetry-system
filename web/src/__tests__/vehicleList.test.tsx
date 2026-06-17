// Presentational unit test for the vehicle list.
//
// `VehicleList` is now a pure view over a `VehicleMap` + `AnomalyMap`: the
// dashboard container (App) owns the single transport subscription and the
// stores (see dashboard.test.tsx for the snapshot-then-stream / no-poll proof).
// Here we prove the rendering contract in isolation:
//   - one row per vehicle showing status + battery, and its anomaly_type when
//     the anomaly map has one for that vehicle (nothing when absent);
//   - granular re-render: replacing only one vehicle's (or one anomaly's) object
//     reference re-renders only that row — the memoized rows bail out otherwise.

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { VehicleList } from "../VehicleList";
import type { Anomaly, AnomalyMap } from "../anomalyStore";
import type { Vehicle, VehicleMap } from "../vehicleStore";

function vehicles(n: number): VehicleMap {
  const map = new Map<string, Vehicle>();
  for (let i = 0; i < n; i++) {
    const id = `vehicle-${String(i).padStart(3, "0")}`;
    map.set(id, {
      vehicle_id: id,
      status: i % 2 === 0 ? "moving" : "idle",
      battery_pct: 50 + (i % 50),
    });
  }
  return map;
}

function anomaly(vehicleId: string, type: string): Anomaly {
  return {
    vehicle_id: vehicleId,
    anomaly_type: type,
    detail: null,
    detected_at: "2026-06-16T00:00:00Z",
  };
}

describe("VehicleList (presentational)", () => {
  it("renders one row per vehicle with status + battery, and its anomaly when present", () => {
    const anomalies: AnomalyMap = new Map([
      ["vehicle-002", anomaly("vehicle-002", "low_battery")],
    ]);
    render(<VehicleList vehicles={vehicles(50)} anomalies={anomalies} />);

    expect(screen.getAllByRole("listitem")).toHaveLength(50);
    expect(screen.getByTestId("status-vehicle-000")).toHaveTextContent("moving");
    expect(screen.getByTestId("battery-vehicle-000")).toHaveTextContent("50%");
    // a vehicle with a seeded anomaly shows its type...
    expect(screen.getByTestId("anomaly-vehicle-002")).toHaveTextContent(
      "low_battery",
    );
    // ...and one without renders no anomaly cell at all.
    expect(screen.queryByTestId("anomaly-vehicle-000")).toBeNull();
  });

  it("re-renders only the row whose vehicle reference changed", () => {
    const renders = new Map<string, number>();
    const onRowRender = (id: string) =>
      renders.set(id, (renders.get(id) ?? 0) + 1);
    const base = vehicles(50);
    const empty: AnomalyMap = new Map();

    const { rerender } = render(
      <VehicleList vehicles={base} anomalies={empty} onRowRender={onRowRender} />,
    );
    const baseline = new Map(renders);

    // replace only vehicle-007's object (fresh ref); every other ref is stable.
    const next = new Map(base);
    next.set("vehicle-007", {
      vehicle_id: "vehicle-007",
      status: "fault",
      battery_pct: 3,
    });
    rerender(
      <VehicleList vehicles={next} anomalies={empty} onRowRender={onRowRender} />,
    );

    expect(screen.getByTestId("status-vehicle-007")).toHaveTextContent("fault");
    expect(renders.get("vehicle-007")).toBe(
      (baseline.get("vehicle-007") ?? 0) + 1,
    );
    let others = 0;
    for (const [id, count] of renders) {
      if (id !== "vehicle-007" && count !== baseline.get(id)) others++;
    }
    expect(others).toBe(0);
  });

  it("re-renders only the row whose anomaly reference changed", () => {
    const renders = new Map<string, number>();
    const onRowRender = (id: string) =>
      renders.set(id, (renders.get(id) ?? 0) + 1);
    const base = vehicles(50);

    const { rerender } = render(
      <VehicleList
        vehicles={base}
        anomalies={new Map() as AnomalyMap}
        onRowRender={onRowRender}
      />,
    );
    const baseline = new Map(renders);

    // give vehicle-010 a fresh anomaly; no other vehicle gains an entry.
    const next: AnomalyMap = new Map([
      ["vehicle-010", anomaly("vehicle-010", "overspeed")],
    ]);
    rerender(
      <VehicleList vehicles={base} anomalies={next} onRowRender={onRowRender} />,
    );

    expect(screen.getByTestId("anomaly-vehicle-010")).toHaveTextContent(
      "overspeed",
    );
    expect(renders.get("vehicle-010")).toBe(
      (baseline.get("vehicle-010") ?? 0) + 1,
    );
    let others = 0;
    for (const [id, count] of renders) {
      if (id !== "vehicle-010" && count !== baseline.get(id)) others++;
    }
    expect(others).toBe(0);
  });
});
