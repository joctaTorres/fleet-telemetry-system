// Design/UX tests for the React dashboard: responsive layout, status badges,
// accessible battery bar, anomaly badges, aria-live announcements, connection
// indicator, loading/error/empty states. Drives <App> with a mocked transport
// so no backend topology is required.

import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { App } from "../App";
import type {
  AnomalySnapshotRow,
  PatchEvent,
  PatchHandler,
  Transport,
  VehicleSnapshotRow,
  ZoneCountsSnapshot,
} from "../transport";

const WS_OPEN = 1;
const WS_CLOSED = 3;

function makeVehicle(
  id: string,
  status: string,
  battery_pct: number,
): VehicleSnapshotRow {
  return { vehicle_id: id, status, battery_pct };
}

type FetchState = "ok" | "loading" | "error";

interface MockTransportResult {
  transport: Transport;
  emit: (event: PatchEvent) => void;
  setConnected: (connected: boolean) => void;
  fetchSnapshot: ReturnType<typeof vi.fn>;
}

function makeMockTransport(options: {
  vehicles?: VehicleSnapshotRow[];
  anomalies?: AnomalySnapshotRow[];
  zones?: ZoneCountsSnapshot;
  state?: FetchState;
  connected?: boolean;
} = {}): MockTransportResult {
  const {
    vehicles = [],
    anomalies = [],
    zones = {},
    state = "ok",
    connected = true,
  } = options;

  let handler: PatchHandler | null = null;
  const connectionListeners = new Set<(connected: boolean) => void>();
  let isConnected = connected;

  const never = () => new Promise<never>(() => {});

  const fetchSnapshot = vi.fn(async () => {
    if (state === "error") throw new Error("snapshot failed");
    if (state === "loading") await never();
    return vehicles;
  });

  return {
    transport: {
      fetchSnapshot,
      fetchAnomalies: vi.fn(async () => {
        if (state === "loading") await never();
        return anomalies;
      }),
      fetchZones: vi.fn(async () => {
        if (state === "loading") await never();
        return zones;
      }),
      get readyState() {
        return isConnected ? WS_OPEN : WS_CLOSED;
      },
      onConnectionChange(cb) {
        connectionListeners.add(cb);
        cb(connected);
        return () => connectionListeners.delete(cb);
      },
      subscribe(h) {
        handler = h;
        return () => {
          handler = null;
        };
      },
    },
    emit(event: PatchEvent) {
      handler?.(event);
    },
    setConnected(value: boolean) {
      isConnected = value;
      for (const cb of connectionListeners) cb(value);
    },
    fetchSnapshot,
  };
}

async function mountApp(options: Parameters<typeof makeMockTransport>[0] = {}) {
  const mock = makeMockTransport(options);
  await act(async () => {
    render(<App transport={mock.transport} />);
    // let snapshot promises settle before assertions.
    for (let i = 0; i < 5; i++) await Promise.resolve();
  });
  return mock;
}

describe("dashboard design and UX", () => {
  it("renders the dashboard shell, grid, and section headings", async () => {
    await mountApp({
      vehicles: [makeVehicle("v-1", "moving", 67)],
      zones: { inbound_dock_a: 3 },
    });

    expect(document.querySelector("main.dashboard")).toBeInTheDocument();
    expect(document.querySelector(".dashboard-grid")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Vehicles" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Zone entries" }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("connection-indicator")).toHaveTextContent(
      "Connected",
    );
  });

  it.each([
    ["moving"],
    ["idle"],
    ["fault"],
    ["offline"],
  ] as const)("renders status '%s' as a styled badge", async (status) => {
    await mountApp({
      vehicles: [makeVehicle("v-status", status, 80)],
    });

    const badge = screen.getByTestId("status-v-status");
    expect(badge).toHaveTextContent(status);
    expect(badge).toHaveClass("status-badge", `status-${status}`);
  });

  it("renders battery as an accessible progress bar", async () => {
    await mountApp({
      vehicles: [makeVehicle("v-battery", "idle", 67)],
    });

    expect(screen.getByTestId("battery-v-battery")).toHaveTextContent("67%");
    const progress = screen.getByLabelText(
      "Battery 67%",
    ) as HTMLProgressElement;
    expect(progress.tagName.toLowerCase()).toBe("progress");
    expect(progress).toHaveAttribute("max", "100");
    expect(progress).toHaveAttribute("value", "67");
  });

  it("shows a distinct low-battery state with visible text", async () => {
    await mountApp({
      vehicles: [makeVehicle("v-low", "idle", 12)],
    });

    const progress = screen.getByLabelText(
      "Battery 12%",
    ) as HTMLProgressElement;
    expect(progress).toHaveClass("battery-progress", "low");
    expect(screen.getByTestId("battery-v-low")).toHaveTextContent("low");
  });

  it("shows a styled anomaly badge and does not show one when absent", async () => {
    await mountApp({
      vehicles: [makeVehicle("v-anomaly", "moving", 80)],
      anomalies: [
        {
          vehicle_id: "v-anomaly",
          anomaly_type: "hard_braking",
          detail: null,
          detected_at: "2026-06-16T00:00:00Z",
        },
      ],
    });

    const badge = screen.getByTestId("anomaly-v-anomaly");
    expect(badge).toHaveTextContent("hard_braking");
    expect(badge).toHaveClass("anomaly-badge");
  });

  it("announces newly arriving anomalies through the aria-live region", async () => {
    const mock = await mountApp({
      vehicles: [makeVehicle("v-live", "moving", 80)],
    });

    expect(screen.getByTestId("anomaly-announcements")).toHaveTextContent("");

    await act(async () => {
      mock.emit({
        type: "anomaly_detected",
        payload: {
          vehicle_id: "v-live",
          anomaly_type: "swerving",
          detail: null,
          detected_at: "2026-06-16T01:00:00Z",
        },
      });
      await Promise.resolve();
    });

    const badge = screen.getByTestId("anomaly-v-live");
    expect(badge).toHaveTextContent("swerving");
    expect(screen.getByTestId("anomaly-announcements")).toHaveTextContent(
      "Anomaly on v-live: swerving",
    );
  });

  it("shows a connected indicator and switches to disconnected on close", async () => {
    const mock = await mountApp({
      vehicles: [makeVehicle("v-1", "moving", 80)],
      connected: true,
    });

    const indicator = screen.getByTestId("connection-indicator");
    expect(indicator).toHaveClass("connected");
    expect(indicator).toHaveTextContent("Connected");

    await act(async () => {
      mock.setConnected(false);
      await Promise.resolve();
    });

    expect(indicator).toHaveClass("disconnected");
    expect(indicator).toHaveTextContent("Disconnected");
  });

  it("shows a loading state while snapshots are in flight", async () => {
    await mountApp({
      vehicles: [makeVehicle("v-1", "moving", 80)],
      state: "loading",
    });

    const loading = screen.getByTestId("loading-state");
    expect(loading).toHaveTextContent("Loading vehicle and zone data");
    expect(screen.queryByTestId("vehicle-row-v-1")).toBeNull();
  });

  it("shows an error state when the snapshot fetch fails", async () => {
    await mountApp({
      state: "error",
    });

    const error = screen.getByTestId("error-state");
    expect(error).toHaveTextContent("Could not load fleet data");
  });

  it("shows an empty message when no vehicles are returned", async () => {
    await mountApp({
      vehicles: [],
      zones: { inbound_dock_a: 3 },
    });

    expect(
      screen.getByText("No vehicles are currently available."),
    ).toBeInTheDocument();
    expect(screen.queryByTestId(/^vehicle-row-/)).toBeNull();
  });

  it("shows an empty message when no zones are returned", async () => {
    await mountApp({
      vehicles: [makeVehicle("v-1", "moving", 80)],
      zones: {},
    });

    expect(
      screen.getByText("No zones are currently configured."),
    ).toBeInTheDocument();
    expect(screen.queryByTestId(/^zone-tile-/)).toBeNull();
  });
});
