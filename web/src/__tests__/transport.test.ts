// Transport seam tests: REST snapshot fetched exactly once, WS messages surfaced
// as typed patch events, and unknown/malformed `type`s dropped without throwing
// (6.3's unknown-type clause). Uses an injected fake `fetch` + WebSocket — no
// backend.

import { describe, expect, it, vi } from "vitest";
import { createHttpTransport, parsePatch } from "../transport";
import type { PatchEvent } from "../transport";

class FakeWebSocket {
  url: string;
  closed = false;
  private listeners = new Map<string, Set<(ev: MessageEvent) => void>>();

  constructor(url: string) {
    this.url = url;
  }
  addEventListener(type: string, fn: (ev: MessageEvent) => void) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(fn);
  }
  removeEventListener(type: string, fn: (ev: MessageEvent) => void) {
    this.listeners.get(type)?.delete(fn);
  }
  close() {
    this.closed = true;
  }
  /** Test helper: deliver a raw text frame to message listeners. */
  deliver(data: string) {
    for (const fn of this.listeners.get("message") ?? []) {
      fn({ data } as MessageEvent);
    }
  }
}

describe("parsePatch", () => {
  it("accepts each of the three known delta types", () => {
    for (const type of [
      "vehicle_state_changed",
      "anomaly_detected",
      "zone_count_changed",
    ]) {
      expect(parsePatch({ type, payload: {} })?.type).toBe(type);
    }
  });

  it("drops unknown types, the connect snapshot, and malformed envelopes", () => {
    expect(parsePatch({ type: "snapshot", payload: {} })).toBeNull();
    expect(parsePatch({ type: "bogus", payload: {} })).toBeNull();
    expect(parsePatch({ type: "vehicle_state_changed" })).toBeNull(); // no payload
    expect(parsePatch("not an object")).toBeNull();
    expect(parsePatch(null)).toBeNull();
  });
});

describe("createHttpTransport", () => {
  it("fetches the REST snapshot exactly once, with no interval refetch", async () => {
    const rows = [{ vehicle_id: "v1", status: "idle", battery_pct: 80 }];
    const fetchFn = vi.fn(async () => ({ json: async () => rows }) as Response);
    const transport = createHttpTransport({
      fetchFn,
      WebSocketImpl: FakeWebSocket as unknown as typeof WebSocket,
    });

    const snap = await transport.fetchSnapshot();
    expect(snap).toEqual(rows);
    expect(fetchFn).toHaveBeenCalledTimes(1);
    expect(fetchFn).toHaveBeenCalledWith("/vehicles");
  });

  it("fetches the anomaly snapshot from /vehicles/anomalies/latest, once", async () => {
    const rows = [
      {
        vehicle_id: "v1",
        anomaly_type: "low_battery",
        detail: "battery_pct=4",
        detected_at: "2026-06-16T00:00:00Z",
      },
    ];
    const fetchFn = vi.fn(async () => ({ json: async () => rows }) as Response);
    const transport = createHttpTransport({
      fetchFn,
      WebSocketImpl: FakeWebSocket as unknown as typeof WebSocket,
    });

    const snap = await transport.fetchAnomalies();
    expect(snap).toEqual(rows);
    expect(fetchFn).toHaveBeenCalledTimes(1);
    expect(fetchFn).toHaveBeenCalledWith("/vehicles/anomalies/latest");
  });

  it("fetches the zone snapshot from /zones/counts, once", async () => {
    const counts = { "inbound_dock_a": 0, "inbound_dock_b": 3 };
    const fetchFn = vi.fn(async () => ({ json: async () => counts }) as Response);
    const transport = createHttpTransport({
      fetchFn,
      WebSocketImpl: FakeWebSocket as unknown as typeof WebSocket,
    });

    const snap = await transport.fetchZones();
    expect(snap).toEqual(counts);
    expect(fetchFn).toHaveBeenCalledTimes(1);
    expect(fetchFn).toHaveBeenCalledWith("/zones/counts");
  });

  it("surfaces a WS vehicle_state_changed frame as a typed event", () => {
    let socket!: FakeWebSocket;
    const WebSocketImpl = vi.fn((url: string) => {
      socket = new FakeWebSocket(url);
      return socket;
    }) as unknown as typeof WebSocket;
    const transport = createHttpTransport({ wsUrl: "ws://x/ws", WebSocketImpl });

    const received: PatchEvent[] = [];
    const unsubscribe = transport.subscribe((e) => received.push(e));

    socket.deliver(
      JSON.stringify({
        type: "vehicle_state_changed",
        payload: { vehicle_id: "v9", status: "fault", battery_pct: 5 },
      }),
    );

    expect(received).toHaveLength(1);
    expect(received[0]).toEqual({
      type: "vehicle_state_changed",
      payload: { vehicle_id: "v9", status: "fault", battery_pct: 5 },
    });

    unsubscribe();
    expect(socket.closed).toBe(true);
  });

  it("drops unknown-type and malformed frames without throwing", () => {
    let socket!: FakeWebSocket;
    const WebSocketImpl = vi.fn((url: string) => {
      socket = new FakeWebSocket(url);
      return socket;
    }) as unknown as typeof WebSocket;
    const transport = createHttpTransport({ wsUrl: "ws://x/ws", WebSocketImpl });

    const received: PatchEvent[] = [];
    transport.subscribe((e) => received.push(e));

    expect(() => {
      socket.deliver(JSON.stringify({ type: "snapshot", payload: { fleet: {} } }));
      socket.deliver(JSON.stringify({ type: "totally_unknown", payload: {} }));
      socket.deliver("}{ not json");
    }).not.toThrow();
    expect(received).toHaveLength(0);
  });
});
