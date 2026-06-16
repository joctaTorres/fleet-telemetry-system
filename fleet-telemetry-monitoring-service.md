# **Build a fleet telemetry monitoring service**

You are building a small vertical slice of a fleet monitoring system for 50 autonomous industrial vehicles that emit telemetry at 1 Hz per vehicle. Each telemetry event is a JSON object with: `vehicle_id`, `timestamp`, `lat/lon`, `battery_pct`, `speed_mps`, `status` (one of `idle`, `moving`, `charging`, `fault`), `error_codes` (array of strings), and `zone_entered` **(string zone ID, or** `null` **— non-null only on the event where the vehicle just crossed into a new zone)**.

Example telemetry events:

```json
{
  "vehicle_id": "v-12",
  "timestamp": "...",
  "lat": 37.41,
  "lon": -122.08,
  "battery_pct": 78,
  "speed_mps": 1.2,
  "status": "moving",
  "error_codes": [],
  "zone_entered": null
}
{
  "vehicle_id": "v-12",
  "timestamp": "...",
  "lat": 37.41,
  "lon": -122.08,
  "battery_pct": 77,
  "speed_mps": 1.1,
  "status": "moving",
  "error_codes": [],
  "zone_entered": "charging_bay_1"
}
```

## **Deliver**

1. **A Python backend service**
    1. Accepts telemetry events via a POST endpoint, handling bursts of concurrent writes from multiple vehicles simultaneously
    2. Persists them to DB
    3. Detects anomalies in real-time (lets define an "anomaly" together)
    4. **Zone-traversal counter.** The warehouse floor is partitioned into a fixed set of named zones (~20 zones, defined at startup — provide them as a hardcoded constant). Telemetry events include a `zone_entered` field (a zone ID string or `null`) when a vehicle has just crossed into a new zone. When present, increment that zone's `entry_count` by 1. With 50 vehicles moving simultaneously over overlapping paths, multiple vehicles can enter the same zone at the same instant — your implementation must guarantee every entry is counted. Expose per-zone counts via a `GET /zones/counts` endpoint.

Plausible scenario: at shift change or end-of-shift, multiple vehicles converge on the charging zones simultaneously, producing concurrent `zone_entered` events for the same zone in the same second.

    5. Supports a vehicle **status update** operation: when a vehicle transitions to `fault`, its active mission must be atomically cancelled and a maintenance record created. Think carefully about concurrent writes and the correct isolation strategy.

    6. Exposes a REST endpoint to query recent anomalies filtered by vehicle and time range
    7. Exposes an endpoint to fetch the **current aggregate fleet state** (per-status counts across all 50 vehicles) that is safe under concurrent updates


2. **A small React + TypeScript dashboard** that:
    1. Shows a live list of the 50 vehicles with current status + battery
    2. Surfaces the most recent anomaly per vehicle
    3. Using websockets : Industrial systems prioritize immediate fault detection. If a 2-ton autonomous vehicle enters a fault state or throws an anomaly, the floor manager looking at the dashboard needs to know instantly, not at the next polling interval. Furthermore, watching a "live list" of 50 vehicles updating via Polling often results in a janky UI where all 50 items re-render simultaneously every 2 seconds. A WebSocket connection allows you to stream individual state patches (e.g., {"vehicle_id": "v-12", "zone_entered": "charging_bay_1"}), enabling smooth, granular updates in your React state.
    4. Per-zone entry counts, updating live.
