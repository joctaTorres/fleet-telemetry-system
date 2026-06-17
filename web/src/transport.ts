// Typed snapshot-then-stream transport for the dashboard.
//
// One module owns the data layer: (a) it fetches the per-vehicle REST snapshot
// exactly once on load — there is no interval or refetch here — and (b) it opens
// the WebSocket and surfaces each message as a typed patch event. The patch
// envelope contract is taken verbatim from `app/events.py` / the CDC translate
// payload (`_build_payload` in `app/cdc_consumer.py`), so the UI consumes exactly
// what the running stack publishes and synthesizes nothing.
//
// The Transport interface is injectable so component tests can drive the UI with
// a mocked REST + WS — no backend topology required.

/** A single row of the `GET /vehicles` REST snapshot the list renders from. */
export interface VehicleSnapshotRow {
  vehicle_id: string;
  status: string;
  battery_pct: number;
}

/**
 * A single row of the `GET /vehicles/anomalies/latest` snapshot — the most-recent
 * anomaly per vehicle. Same shape as the live {@link AnomalyDetectedPayload}, so
 * the anomaly store seeds and patches from one type.
 */
export type AnomalySnapshotRow = AnomalyDetectedPayload;

/**
 * The `GET /zones/counts` snapshot: an object keyed by `zone_id` whose value is
 * the live entry count. The backend guarantees a row per seeded zone.
 */
export type ZoneCountsSnapshot = Record<string, number>;

/** `vehicle_state_changed` payload — `vehicle_current_state` translate. */
export interface VehicleStateChangedPayload {
  vehicle_id: string;
  status: string;
  battery_pct: number;
}

/** `anomaly_detected` payload — `anomalies` translate. */
export interface AnomalyDetectedPayload {
  vehicle_id: string;
  anomaly_type: string;
  detail: string | null;
  detected_at: string;
}

/** `zone_count_changed` payload — `zone_counts` translate. */
export interface ZoneCountChangedPayload {
  zone_id: string;
  entry_count: number;
}

/**
 * A discriminated union of the three delta envelopes published on the Redis
 * `fleet:events` channel and fanned out over `/ws`. The connect `snapshot`
 * envelope is intentionally absent: it is not one of the live delta types, so it
 * is dropped by {@link parsePatch} (the vehicle list is seeded from REST).
 */
export type PatchEvent =
  | { type: "vehicle_state_changed"; payload: VehicleStateChangedPayload }
  | { type: "anomaly_detected"; payload: AnomalyDetectedPayload }
  | { type: "zone_count_changed"; payload: ZoneCountChangedPayload };

export type PatchHandler = (event: PatchEvent) => void;

// Local constants so this file does not depend on the global `WebSocket` constructor
// being present at evaluation time (tests inject a FakeWebSocket and may lack a
// global WebSocket object).
const WS_OPEN = 1;
const WS_CLOSED = 3;

/**
 * The data seam the UI depends on. Implementations: {@link createHttpTransport}
 * in the running app, a mock in tests.
 */
export interface Transport {
  /** Fetch the per-vehicle REST snapshot. Called exactly once on load. */
  fetchSnapshot(): Promise<VehicleSnapshotRow[]>;
  /**
   * Fetch the most-recent-anomaly-per-vehicle REST snapshot. Called exactly once
   * on load to seed the anomaly store before the live stream takes over.
   */
  fetchAnomalies(): Promise<AnomalySnapshotRow[]>;
  /**
   * Fetch the per-zone entry-count REST snapshot. Called exactly once on load to
   * seed the zone store before the live stream takes over.
   */
  fetchZones(): Promise<ZoneCountsSnapshot>;
  /** Current WebSocket readyState (or closed), for the connection indicator. */
  readonly readyState?: number;
  /**
   * Subscribe to WebSocket open/close changes for the connection indicator.
   * Optional for test mocks.
   */
  onConnectionChange?(callback: (connected: boolean) => void): () => void;
  /** Open the live patch stream; returns an unsubscribe/close function. */
  subscribe(handler: PatchHandler): () => void;
}

const KNOWN_TYPES: ReadonlySet<string> = new Set([
  "vehicle_state_changed",
  "anomaly_detected",
  "zone_count_changed",
]);

/**
 * Validate one decoded WS message into a typed {@link PatchEvent}, or `null` if
 * its `type` is unknown/malformed. Returning `null` (never throwing) is what
 * keeps an unexpected message from killing the stream.
 */
export function parsePatch(raw: unknown): PatchEvent | null {
  if (typeof raw !== "object" || raw === null) return null;
  const obj = raw as Record<string, unknown>;
  if (typeof obj.type !== "string" || !KNOWN_TYPES.has(obj.type)) return null;
  if (typeof obj.payload !== "object" || obj.payload === null) return null;
  return { type: obj.type, payload: obj.payload } as PatchEvent;
}

export interface HttpTransportOptions {
  /** Base URL for the REST read; default same-origin (Vite proxies `/vehicles`). */
  baseUrl?: string;
  /** WebSocket URL; default derived from `window.location` (`/ws`). */
  wsUrl?: string;
  /** Injectable `fetch` (tests pass a mock; default is the global). */
  fetchFn?: typeof fetch;
  /** Injectable WebSocket ctor (tests pass a fake; default is the global). */
  WebSocketImpl?: typeof WebSocket;
}

function defaultWsUrl(): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws`;
}

/**
 * Build-time override for the REST base URL. When the dashboard is built and
 * served by the runtime stack, the `dashboard` compose service injects
 * `VITE_API_BASE_URL` (e.g. `http://localhost:8002`) so the browser reaches the
 * runtime frontend API. Unset in dev/test, so the same-origin default holds and
 * the Vite proxy / existing unit tests are unaffected. No host/port is in source.
 */
function envApiBaseUrl(): string | undefined {
  const v = import.meta.env?.VITE_API_BASE_URL;
  return typeof v === "string" && v.length > 0 ? v : undefined;
}

/** Build-time override for the WS URL (`VITE_WS_URL`); see {@link envApiBaseUrl}. */
function envWsUrl(): string | undefined {
  const v = import.meta.env?.VITE_WS_URL;
  return typeof v === "string" && v.length > 0 ? v : undefined;
}

/**
 * The production transport: REST snapshot via `fetch('/vehicles')` (once), live
 * patches via a `WebSocket('/ws')`. Each socket message is JSON-parsed and run
 * through {@link parsePatch}; unknown/malformed types are dropped silently. No
 * interval, no refetch — the only source of updates after load is the socket.
 */
export function createHttpTransport(opts: HttpTransportOptions = {}): Transport {
  // Resolution order: explicit option (tests) → build-time VITE_* env (runtime
  // served dashboard) → same-origin default (dev / unit tests). The env values
  // are additive overrides only, so the same-origin default is unchanged when
  // they are absent and existing tests / `npm run dev` stay green.
  const baseUrl = opts.baseUrl ?? envApiBaseUrl() ?? "";
  const wsUrl = opts.wsUrl ?? envWsUrl() ?? defaultWsUrl();
  const fetchFn = opts.fetchFn ?? fetch;
  const WS = opts.WebSocketImpl ?? WebSocket;

  let socket: WebSocket | undefined;
  const connectionListeners = new Set<(connected: boolean) => void>();

  function notifyConnection(connected: boolean) {
    for (const cb of connectionListeners) {
      cb(connected);
    }
  }

  return {
    async fetchSnapshot(): Promise<VehicleSnapshotRow[]> {
      const res = await fetchFn(`${baseUrl}/vehicles`);
      return (await res.json()) as VehicleSnapshotRow[];
    },
    async fetchAnomalies(): Promise<AnomalySnapshotRow[]> {
      const res = await fetchFn(`${baseUrl}/vehicles/anomalies/latest`);
      return (await res.json()) as AnomalySnapshotRow[];
    },
    async fetchZones(): Promise<ZoneCountsSnapshot> {
      const res = await fetchFn(`${baseUrl}/zones/counts`);
      return (await res.json()) as ZoneCountsSnapshot;
    },
    get readyState() {
      return socket?.readyState ?? WS_CLOSED;
    },
    onConnectionChange(callback) {
      connectionListeners.add(callback);
      callback(socket ? socket.readyState === WS_OPEN : false);
      return () => {
        connectionListeners.delete(callback);
      };
    },
    subscribe(handler: PatchHandler): () => void {
      socket = new WS(wsUrl);
      const onMessage = (ev: MessageEvent) => {
        let decoded: unknown;
        try {
          decoded = JSON.parse(typeof ev.data === "string" ? ev.data : "");
        } catch {
          return; // malformed JSON — drop, never throw
        }
        const event = parsePatch(decoded);
        if (event !== null) handler(event);
      };
      const onOpen = () => notifyConnection(true);
      const onClose = () => notifyConnection(false);
      socket.addEventListener("open", onOpen);
      socket.addEventListener("close", onClose);
      socket.addEventListener("message", onMessage);
      return () => {
        socket?.removeEventListener("open", onOpen);
        socket?.removeEventListener("close", onClose);
        socket?.removeEventListener("message", onMessage);
        socket?.close();
      };
    },
  };
}
