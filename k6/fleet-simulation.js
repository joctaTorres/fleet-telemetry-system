// k6 fleet load simulation — a stateful ~50-vehicle, 1 Hz fleet driving the
// RUNNING telemetry stack through its served HTTP APIs, asserting behavior with
// checks and thresholds. Fires on `docker compose up` (the `k6` service).
//
// The load is CONTINUOUS: a `constant-vus` executor holds 50 VUs for an
// effectively-unbounded duration, so the fleet streams ~50 events/s steadily for
// as long as the stack is up (load — and therefore the CDC deltas and live
// dashboard updates — never stops on its own).
//
// One VU == one vehicle (v-0 .. v-49). Each VU owns a single vehicle_id for its
// whole run and carries mutable state across ~1 Hz ticks (position, speed,
// battery, status) — held per-VU across iterations — so the load is a stateful
// simulation rather than random per-request fire. Each tick:
//   - moves the vehicle (updates pos + speed) and drains battery on a normal tick;
//   - periodically crosses a zone boundary, setting `zone_entered` to a realistic
//     ZONES id (so the seeded zone's entry_count increments and its tile moves);
//   - during the shift-change window, a subset converges on the charging bays,
//     reports status "charging", and recovers battery instead of draining;
//   - occasionally faults — POST /vehicles/{id}/status "fault" and/or telemetry
//     that trips a real anomaly threshold — so anomalies accumulate.
//
// CANONICAL SHAPE vs. THE RUNNING API. The conceptual canonical telemetry sample
// is { vehicle_id, timestamp, lat, lon, battery_pct, speed_mps, status,
// error_codes, zone_entered }. The deployed ingestion model
// (app.models.TelemetryEvent, extra="forbid") names those same fields
// `recorded_at` (the ISO-8601 timestamp), `pos_x`/`pos_y` (the lat/lon position),
// so the POST body below uses the API's field names — semantically the canonical
// shape, mapped onto what the served `POST /telemetry` actually validates and
// accepts as 201. Sending the literal `timestamp`/`lat`/`lon` keys would be
// rejected 422 by the forbid-extra model.

import http from "k6/http";
import { check, sleep } from "k6";
import { Counter, Rate, Trend } from "k6/metrics";

// ── Targets (no hard-coded hosts; supplied by the compose `k6` service) ───────
const INGEST = __ENV.INGESTION_BASE_URL || "http://localhost:8001";
const FRONTEND = __ENV.FRONTEND_BASE_URL || "http://localhost:8002";

const FLEET_SIZE = Number(__ENV.FLEET_SIZE || 50);
// Continuous run: each VU loops one ~1 Hz stateful tick per iteration for the
// whole DURATION, so the fleet sustains ~FLEET_SIZE events/s for as long as the
// stack is up. DURATION defaults to an effectively-unbounded window for the live
// demo and is env-overridable (e.g. a short bound for a CI smoke run).
const DURATION = __ENV.DURATION || "720h"; // effectively unbounded for `up`
// Phase length (in ticks) of one simulated "shift": the move/drain → crossing →
// shift-change-charging → fault cycle repeats on this period so behaviour keeps
// recurring continuously rather than once-per-run.
const SHIFT_PERIOD = Number(__ENV.SHIFT_PERIOD || 60);

// The 20 realistic warehouse zone ids, mirroring app.models.ZONES in order. A
// crossing tick draws `zone_entered` from these so the increment lands on a
// seeded zone. Charging bays are addressed by name during the shift change.
const ZONES = [
  "inbound_dock_a", "inbound_dock_b", "receiving_staging", "aisle_a", "aisle_b",
  "aisle_c", "high_bay_1", "high_bay_2", "bulk_storage", "pick_zone_1",
  "pick_zone_2", "pack_station", "sort_belt", "outbound_dock_a", "outbound_dock_b",
  "shipping_staging", "charging_bay_1", "charging_bay_2", "charging_bay_3",
  "maintenance_bay",
];
const CHARGING_BAYS = ["charging_bay_1", "charging_bay_2", "charging_bay_3"];

// ── Custom metrics ────────────────────────────────────────────────────────────
const ingestLatency = new Trend("ingest_latency", true); // p95 threshold below
const ingestErrors = new Rate("ingest_errors");
const telemetrySent = new Counter("telemetry_sent");
const zoneCrossings = new Counter("zone_crossings");
const faultsInjected = new Counter("faults_injected");

// ── Options: 50 VUs, one per vehicle, driving a CONTINUOUS load. The
// `constant-vus` executor holds FLEET_SIZE VUs for the whole (effectively
// unbounded) DURATION; each VU runs ONE stateful ~1 Hz tick per iteration, so the
// fleet sustains roughly FLEET_SIZE events/second steadily for as long as the
// stack is up — load (and therefore the CDC deltas and live dashboard updates)
// never stops on its own. Thresholds gate pass/fail and make a breach exit
// non-zero so a regression is visible. ───────────────────────────────────────
export const options = {
  scenarios: {
    fleet: {
      executor: "constant-vus",
      vus: FLEET_SIZE,
      duration: DURATION,
    },
  },
  thresholds: {
    // p95 ingest latency under a stated bound.
    ingest_latency: ["p(95)<750"],
    // error rate on writes under a small fraction.
    ingest_errors: ["rate<0.05"],
    // overall check pass-rate high.
    checks: ["rate>0.95"],
    // k6's own request failure rate stays low.
    http_req_failed: ["rate<0.05"],
  },
};

function iso(date) {
  return date.toISOString();
}

// Post one telemetry event in the API's accepted shape, checking 201 + latency.
function postTelemetry(body) {
  const res = http.post(`${INGEST}/telemetry`, JSON.stringify(body), {
    headers: { "Content-Type": "application/json" },
    tags: { name: "post_telemetry" },
  });
  ingestLatency.add(res.timings.duration);
  ingestErrors.add(res.status !== 201);
  check(res, { "telemetry POST returns 201": (r) => r.status === 201 });
  telemetrySent.add(1);
  return res;
}

// ── Per-VU state that must persist ACROSS iterations ──────────────────────────
// With the continuous `constant-vus` executor each VU calls the default function
// once per iteration, so the per-vehicle simulation state (position, speed,
// battery, status, tick counter) is held module-scope, keyed by __VU, and
// initialized exactly once per VU. This keeps the load a stateful per-vehicle
// simulation that evolves over time rather than random per-request fire.
const vuState = {};

function initState() {
  return {
    pos_x: 100 + (__VU % 10) * 5, // metres; spread the fleet out
    pos_y: 100 + Math.floor(__VU / 10) * 5,
    speed_mps: 1.2,
    battery_pct: 60 + (__VU % 40), // 60..99 to start
    status: "moving",
    lastZoneIdx: __VU % ZONES.length,
    tick: 0, // monotonically increasing per-VU tick counter across iterations
  };
}

export default function () {
  // Each VU owns exactly one vehicle for its whole run (v-0 .. v-49).
  const vehicleId = `v-${__VU - 1}`;
  if (vuState[__VU] === undefined) vuState[__VU] = initState();
  const state = vuState[__VU];

  // One ~1 Hz tick per iteration. The move/drain → crossing → shift-charging →
  // fault cycle recurs every SHIFT_PERIOD ticks, so the behaviour keeps repeating
  // continuously for the whole run rather than once. `phase` is the position
  // within the current shift cycle.
  const tick = state.tick++;
  const phase = tick % SHIFT_PERIOD;

  const now = new Date();
  let zoneEntered = null;
  let errorCodes = [];

  // Shift-change convergence window: a slice of each shift cycle during which a
  // subset of vehicles head to the charging bays and recover battery.
  const shiftStart = Math.floor(SHIFT_PERIOD * 0.5);
  const shiftEnd = Math.floor(SHIFT_PERIOD * 0.7);
  const convergesAtShift = __VU % 4 === 0; // ~1/4 of the fleet converges
  const inShift = phase >= shiftStart && phase < shiftEnd;

  if (inShift && convergesAtShift) {
    // ── Shift-change: converge on a charging bay, charge, recover battery ──
    const bay = CHARGING_BAYS[__VU % CHARGING_BAYS.length];
    state.status = "charging";
    state.speed_mps = 0; // parked at the bay
    // Recover battery while charging (capped at 100). status=="charging" so the
    // backend's battery_rising / low_battery rules correctly do NOT fire.
    state.battery_pct = Math.min(100, state.battery_pct + 4);
    // On the tick we arrive at the bay, report the crossing into it.
    if (phase === shiftStart) {
      zoneEntered = bay;
      zoneCrossings.add(1);
    }
  } else {
    // ── Normal tick: move + drain ─────────────────────────────────────────
    state.status = "moving";
    state.speed_mps = 0.8 + Math.random() * 2.0; // 0.8..2.8 m/s (under overspeed)
    state.pos_x += state.speed_mps; // advance position so it genuinely moves
    state.pos_y += state.speed_mps * 0.3;
    state.battery_pct = Math.max(0, state.battery_pct - 0.5); // drain
    if (state.battery_pct <= 20) state.battery_pct = 60 + (__VU % 40); // recharge floor

    // Periodically cross a zone boundary (~every 6 ticks, phase-shifted per VU).
    if ((tick + __VU) % 6 === 0) {
      state.lastZoneIdx = (state.lastZoneIdx + 1) % ZONES.length;
      // Draw from the non-charging zones for ordinary movement.
      const zone = ZONES[state.lastZoneIdx];
      zoneEntered = zone;
      zoneCrossings.add(1);
    }
  }

  // ── Occasional fault injection so anomalies accumulate ────────────────────
  // A small slice of vehicles trips a real anomaly threshold on a given tick.
  let injectFaultStatus = false;
  if (!inShift && phase > 5) {
    const faultRoll = (tick * 7 + __VU * 13) % 100;
    if (faultRoll < 4) {
      // overspeed: speed_mps > 5 trips the overspeed rule.
      state.status = "moving";
      state.speed_mps = 6 + Math.random() * 4; // > 5
      faultsInjected.add(1);
    } else if (faultRoll < 7) {
      // low_battery: battery_pct < 15 while not charging trips low_battery.
      state.status = "moving";
      state.battery_pct = Math.random() * 14; // < 15
      faultsInjected.add(1);
    } else if (faultRoll < 9) {
      // error_codes present trips the error_codes rule, and we also drive the
      // authoritative status to "fault" via the status route below.
      errorCodes = ["E_MOTOR_OVERHEAT"];
      injectFaultStatus = true;
      faultsInjected.add(1);
    }
  }

  postTelemetry({
    vehicle_id: vehicleId,
    recorded_at: iso(now), // canonical ISO-8601 timestamp
    pos_x: state.pos_x, // canonical lat
    pos_y: state.pos_y, // canonical lon
    battery_pct: Number(state.battery_pct.toFixed(2)),
    speed_mps: Number(state.speed_mps.toFixed(2)),
    status: state.status,
    error_codes: errorCodes,
    zone_entered: zoneEntered,
  });

  if (injectFaultStatus) {
    // The vehicle now exists (a telemetry POST upserted its current state), so
    // the fault status transition resolves to 200, atomically cancelling its
    // mission and opening a maintenance record on the backend.
    const fres = http.post(
      `${INGEST}/vehicles/${vehicleId}/status`,
      JSON.stringify({ status: "fault", reason: "k6 injected fault" }),
      { headers: { "Content-Type": "application/json" }, tags: { name: "post_status" } },
    );
    check(fres, { "fault status POST returns 200": (r) => r.status === 200 });
    state.status = "fault";
  }

  // ── Read-back checks: the served frontend API reflects the load ───────────
  // Exercised periodically (once per shift cycle, by VU 1) so they reflect the
  // sustained load without every VU hammering the read endpoints every tick.
  if (__VU === 1 && phase === shiftEnd) {
    const vehicles = http.get(`${FRONTEND}/vehicles`, { tags: { name: "get_vehicles" } });
    check(vehicles, {
      "GET /vehicles returns 200": (r) => r.status === 200,
      "GET /vehicles lists vehicles with status+battery": (r) => {
        const list = r.json();
        return (
          Array.isArray(list) &&
          list.length > 0 &&
          list.every((v) => "vehicle_id" in v && "status" in v && "battery_pct" in v)
        );
      },
    });

    const zones = http.get(`${FRONTEND}/zones/counts`, { tags: { name: "get_zones" } });
    check(zones, {
      "GET /zones/counts returns 200": (r) => r.status === 200,
      "GET /zones/counts shows growing counts": (r) => {
        const counts = r.json();
        const total = Object.values(counts).reduce((a, b) => a + b, 0);
        return total > 0; // at least some entries have landed under load
      },
      "GET /zones/counts has all 20 realistic zones": (r) =>
        Object.keys(r.json()).length === 20,
    });

    const anomalies = http.get(`${FRONTEND}/vehicles/anomalies/latest`, {
      tags: { name: "get_anomalies" },
    });
    check(anomalies, {
      "GET /vehicles/anomalies/latest returns 200": (r) => r.status === 200,
      "GET /vehicles/anomalies/latest returns anomalies under load": (r) => {
        const list = r.json();
        return Array.isArray(list) && list.length > 0;
      },
    });
  }

  sleep(1); // ~1 Hz per vehicle
}
