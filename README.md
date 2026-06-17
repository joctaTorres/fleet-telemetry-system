# 🚚 Fleet Telemetry Monitoring Service

Real-time monitoring for a fleet of ~50 autonomous industrial vehicles emitting
telemetry at 1 Hz. Built as a thin, **correctness-under-concurrency** vertical
slice: every zone entry counted, every fault transition atomic, a consistent
fleet aggregate, and committed changes streamed to a live dashboard over
WebSocket — derived from the Postgres WAL, with **no dual-write**.

> The hard part here isn't throughput (~50 writes/sec is trivial). It's
> **concurrency correctness** — lost-update-free counters, atomic multi-table
> fault handling, a consistent aggregate — plus delivering changes to the
> dashboard with low latency without coupling the write path to the read path.

Architecture rationale lives in [`docs/ADR.md`](docs/ADR.md)

AI Build log [`docs/ai-build-logs/`](docs/ai-build-logs/index.md)

**Built with [Ratchet](https://github.com/joctaTorres/ratchet)** ⚙️⚙️

---

## ⚡ Quick start — run it end to end

**One command** serves the entire system *and* drives a continuous 50-vehicle
fleet against it with [Grafana k6](#-load-simulation-grafana-k6):

```bash
docker compose up --build
```

Then open the live dashboard at **http://localhost:8080** 👈 — and be sure to
check out the **full observability system** at **[Grafana →
http://localhost:3000/dashboards](http://localhost:3000/dashboards)**, where every
flow is traced and dashboarded ([what's inside](#-observability--opentelemetry-end-to-end)).

You'll watch the floor in real time as k6 streams telemetry: the 50-vehicle list
(status + battery), the latest anomaly per vehicle, and the per-zone entry
counts all update live over WebSocket — no refresh, no polling. Vehicles drain,
fault, and converge on the charging bays at shift change.

| Service | URL |
|---|---|
| 📊 Dashboard | http://localhost:8080 |
| 📈 Grafana (observability) | http://localhost:3000/dashboards |
| Frontend API (REST + `ws://…/ws`) | http://localhost:8002 |
| Ingestion API | http://localhost:8001 |
| Tempo (traces) | http://localhost:3200 |
| Prometheus (metrics) | http://localhost:9090 |

Grafana opens straight to the dashboards (anonymous admin enabled — no login) with
**all 8 dashboards preloaded and live** from the k6 fleet — see
[Observability](#-observability--opentelemetry-end-to-end).

Tear it all down (and drop volumes) with `docker compose down -v`.

> The runtime stack (`docker-compose.yml`) and the test harness
> (`docker-compose.test.yml`) share one Compose project name — run only one at a
> time. To run the test suites instead, see [Testing & proofs](#-testing--proofs).

---

## ✨ What's inside

- **Stateless ingestion API** — `POST /telemetry` validates and persists each
  reading; absorbs bursts of concurrent writes. Pure DB writer, never touches Redis.
- **Zone-traversal counter** — atomic, lost-update-free `entry_count` per zone,
  exact even when many vehicles enter the same zone in the same instant.
- **Real-time anomaly detection** — stateless, stateful, and by-absence rules,
  evaluated synchronously inside the ingest transaction.
- **Atomic fault transition** — a `fault` flips the vehicle, cancels its active
  mission, and writes a maintenance record in one `FOR UPDATE`-locked transaction.
- **Consistent fleet aggregate** — per-status counts from an MVCC snapshot, no
  materialized counter to race on.
- **CDC → WebSocket fan-out** — a singleton consumer tails the primary's
  **pgoutput** logical slot, publishes derived events to Redis; the frontend API
  fans them to dashboards over WebSocket.
- **Live React + TypeScript dashboard** — 50-vehicle list with status/battery,
  latest anomaly per vehicle, and live zone counts — updated by **granular WS
  patches** (only the changed row/tile re-renders; no polling).

---

## 🏗️ Architecture

The primary's write-ahead log is tapped **twice** by independent mechanisms:
physical streaming replication feeds the read replica (REST reads), and logical
decoding feeds the CDC consumer (the event stream). The ingestion API is a pure,
stateless DB writer.

```mermaid
flowchart TD
  V["Vehicles ×50<br/>1 Hz telemetry"] -->|POST /telemetry| ING["Ingestion API<br/>stateless · write-only"]
  ING -->|single txn write| PG[("Postgres primary<br/>source of truth")]
  PG -->|physical streaming| RR[("Read replica<br/>REST snapshots")]
  PG -->|pgoutput logical slot| CDC["CDC consumer<br/>singleton · WAL → events"]
  CDC -->|publish fleet:events| RED[("Redis pub/sub")]
  RR -->|REST snapshot + history| FE["Frontend API<br/>WS push + REST"]
  RED -->|subscribe| FE
  FE -->|WebSocket patches| DASH["React dashboard"]
```

**Why CDC over a synchronous dual-write?** A DB commit and a separate broker
publish aren't one transaction — a crash between them diverges the event stream
from committed state. Sourcing the stream from the WAL makes it a *deterministic
function of what committed*, eliminating that whole class of bug. The trade-off
is operational: a singleton slot reader whose replication lag must be monitored.
Full options analysis in the [ADR](docs/ADR.md).

---

## 🔬 Load simulation (Grafana k6)

The runtime stack ships a [k6](https://k6.io) service
([`k6/fleet-simulation.js`](k6/fleet-simulation.js)) that fires automatically on
`docker compose up` and drives a **continuous, stateful** fleet — a realistic
stand-in for 50 vehicles on the warehouse floor, and the thing that makes the
dashboard move.

- **50 virtual users = 50 vehicles** (`v-0`…`v-49`). A `constant-vus` executor
  holds them for an effectively-unbounded duration, so the fleet streams ~50
  events/s steadily for as long as the stack is up.
- **Each VU is one stateful vehicle** — it carries its own position, speed,
  battery, and status across ticks and emits telemetry at ~1 Hz. It moves and
  drains, crosses into zones (driving the live counters), and on a recurring
  shift cycle converges on the `charging_bay_*` zones (the concurrent same-zone
  entry scenario) before recovering.
- **It exercises every anomaly path** — vehicles periodically trip low-battery,
  overspeed, stuck, teleport, and fault transitions, so anomalies and
  fault→maintenance handling actually fire under load.
- **It asserts the system behaves** — k6 `checks` (telemetry → `201`; the
  frontend reads reflect the load) and `thresholds` gate the run and make it
  exit non-zero on breach:

  | Threshold | Bound |
  |---|---|
  | p95 ingest latency | `< 750 ms` |
  | ingest error rate | `< 5%` |
  | check pass-rate | `> 95%` |
  | HTTP failure rate | `< 5%` |

Every event it sends flows through the CDC → Redis → WebSocket path and patches
the dashboard live. Tune it via env vars (all optional):

```bash
# k6 already runs inside `docker compose up`; re-run or tune it on demand:
FLEET_SIZE=50 DURATION=10m SHIFT_PERIOD=60 docker compose run --rm k6
```

`FLEET_SIZE` (vehicles/VUs), `DURATION` (run length, default effectively
unbounded), `SHIFT_PERIOD` (ticks between shift-change charging convergence),
plus `INGESTION_BASE_URL` / `FRONTEND_BASE_URL`.

---

## 🔭 Observability — OpenTelemetry end to end

The same `docker compose up` that runs the system also stands up a **self-hosted
[OpenTelemetry](https://opentelemetry.io) + Grafana stack** and wires every
service into it. There's nothing to configure: open **[Grafana →
http://localhost:3000/dashboards](http://localhost:3000/dashboards)** and all eight dashboards are
already there, populated in real time by the k6 fleet. No login (anonymous admin
is enabled for local use).

[![Fleet Observability Overview dashboard — a live KPI stat row (ingestion throughput, errors, WebSocket connections, CDC publish rate, Redis fan-out rate, replication lag), a critical-flow service graph, and end-to-end pipeline throughput / API latency panels](docs/images/fleet-system-overview.png)](docs/images/fleet-system-overview.png)

*The **Fleet Observability Overview** — live fleet KPIs, the critical-flow service
graph (`user → ingestion-api → db`, `cdc-consumer → frontend-api → replica`,
`browser → frontend-api`), and end-to-end pipeline throughput / latency, all driven
by the k6 fleet. One of eight dashboards, preloaded on `docker compose up`.*

Every component emits OTLP to a single front door — **Grafana Alloy** — which
fans **traces → Tempo** and **metrics → Prometheus**, both rendered in Grafana:

```mermaid
flowchart LR
  ING["Ingestion API"] --> ALLOY
  FE["Frontend API<br/>(REST + WS)"] --> ALLOY
  CDC["CDC consumer"] --> ALLOY
  RP["Replication probe"] --> ALLOY
  WEB["Browser SPA<br/>@opentelemetry/sdk-trace-web"] -->|OTLP/HTTP + CORS| ALLOY
  ALLOY["Grafana Alloy<br/>OTLP :4318"] -->|traces| TEMPO[("Tempo")]
  ALLOY -->|metrics| PROM[("Prometheus")]
  TEMPO --> GRAF["Grafana<br/>:3000"]
  PROM --> GRAF
```

**What's instrumented** — traces *and* metrics, end to end:

- **Ingestion / Frontend APIs** — FastAPI request spans, per-route rate / latency
  / error metrics, and `CLIENT` spans on the Postgres write & replica reads.
- **WebSocket layer** — a `/ws` lifecycle span, a live `frontend_ws_active_connections`
  gauge, and a fan-out broadcast counter.
- **CDC consumer** — WAL-decode and per-event publish (`PRODUCER`) spans, plus
  event-throughput and decode-lag metrics.
- **The async critical path is one connected trace.** The W3C `traceparent` is
  threaded through the Redis event envelope, so a single telemetry write yields
  **one trace** spanning `cdc.decode → cdc.publish → redis.subscribe (CONSUMER)
  → ws.broadcast` — the CDC → Redis → WebSocket fan-out, stitched together in
  Tempo. *(The ingestion→CDC hop is a deliberate trace-root break at the Postgres
  WAL boundary — the envelope carries transport metadata, never application data.)*
- **Streaming replication** — a small probe emits `pg_replication_lag_bytes` /
  `pg_replication_lag_seconds` as custom OTel metrics.
- **The browser** — the React SPA is instrumented with
  `@opentelemetry/sdk-trace-web` (document-load + fetch/XHR) and propagates
  `traceparent` onto its REST calls, so a page load is **one distributed trace
  from the browser into the Frontend API** (`fleet-dashboard-web → frontend-api`).

All instrumentation is **safe-by-default**: with no OTLP endpoint set, the
bootstrap is a no-op, so local runs and the test suites need no collector.

### Dashboards (auto-provisioned)

| Dashboard | What it shows |
|---|---|
| **Fleet Observability Overview** | Top-level health + links to every flow + a **Tempo service-graph** of all critical flows (`ingestion→db`, `cdc→frontend`, `frontend→replica`, `browser→frontend`) |
| **Ingestion API** | Request rate, p50/p95 latency, error rate, live ingestion traces |
| **Frontend API & WebSockets** | REST rates/latency, active WebSocket connections, broadcast rate, frontend traces |
| **CDC Consumer** | Event throughput by type, decode-lag quantiles, consumer traces |
| **Pub/Sub** | Publish vs. subscribe/delivery rates, the connected cdc↔frontend trace |
| **Redis Fan-out** | Fan-out delivery rate, active connections, dropped clients |
| **Primary/Replica Streaming** | Replication lag (bytes & seconds) over time |
| **Frontend Web (Browser)** | Browser traces — document load, REST fetch spans, browser→frontend-api join |

> **Traces** flush within seconds; custom **metrics** export on a ~60 s OTLP
> cadence (the Prometheus datasource is provisioned with `timeInterval: 60s` to
> match), so metric panels settle a minute or so after boot. Tempo is pinned to
> `2.7.2` (3.0 is distroless and can't run the shell healthcheck).

---

## 🔌 API reference

### Ingestion API — stateless, write-only

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/telemetry` | Validate a telemetry event → persist (raw append + current-state upsert + zone increment + anomaly detection), all in one transaction. `201`, or `422` on schema-invalid input (nothing persisted). |
| `POST` | `/vehicles/{vehicle_id}/status` | Vehicle status-update operation; a transition to `fault` atomically cancels the active mission and creates a maintenance record. |

### Frontend API — reads from the replica + live WebSocket

| Method | Route | Purpose |
|---|---|---|
| `WS` | `/ws` | Snapshot on connect, then a stream of individual state patches. |
| `GET` | `/fleet/state` | Per-status counts across the fleet (`idle`/`moving`/`charging`/`fault`). |
| `GET` | `/vehicles` | Current state (status + battery) for every vehicle. |
| `GET` | `/vehicles/anomalies/latest` | Most recent anomaly per vehicle. |
| `GET` | `/zones/counts` | Per-zone entry counts (all ~20 zones, zero-filled). |
| `GET` | `/anomalies` | Recent anomalies filtered by `vehicle_id` + `[since, until]`. |

A telemetry event:

```json
{
  "vehicle_id": "v-12",
  "timestamp": "2026-06-16T12:00:01Z",
  "lat": 37.41, "lon": -122.08,
  "battery_pct": 77,
  "speed_mps": 1.1,
  "status": "moving",
  "error_codes": [],
  "zone_entered": "zone-07"
}
```

WebSocket patches are one of three event types on the `fleet:events` channel:
`vehicle_state_changed`, `anomaly_detected`, `zone_count_changed`.

---

## 🔒 Concurrency correctness — the heart of it

All correctness is enforced **at the database layer**, not in application code.

| Concern | Strategy | Why it's safe |
|---|---|---|
| **Zone counter** | `UPDATE zone_counts SET entry_count = entry_count + 1 WHERE zone_id = $1` | Server-side read-modify-write under a row lock; concurrent increments to one zone serialize — none lost. An app-level `SELECT`→`+1`→`UPDATE` is **rejected** (two readers, one increment vanishes). |
| **Fault transition** | One txn holding `SELECT 1 FROM vehicles WHERE vehicle_id=$1 FOR UPDATE`, then cancel mission + insert maintenance record + set status | Locking the vehicle row serializes all fault handling for that vehicle. A transition guard + a partial-unique constraint make it idempotent under at-least-once / duplicate delivery — no double-cancel, no duplicate record. |
| **Fleet aggregate** | `vehicle_current_state` upserted per event; aggregate is `SELECT status, COUNT(*) … GROUP BY status` | 50 rows under one MVCC snapshot — internally consistent, no materialized counter to race on. Same table supplies the "previous reading" for stateful anomaly checks. |
| **Event stream** | CDC from the WAL, not a dual-write | The stream is a deterministic function of committed transactions; logical decoding emits only at commit, so a rolled-back write is never published. |

---

## 🚨 Anomaly rules

Detected synchronously inside the ingest transaction (the `anomalies` INSERT
*is* the event), plus a by-absence background watchdog.

| Class | Rule |
|---|---|
| **Stateless** | `status = fault`; non-empty `error_codes`; `battery_pct < 15` while not charging; `speed_mps > 5`. |
| **Stateful** (vs the prior persisted reading) | **stuck**: `moving` & `speed < 0.1` for ≥ 10 s · **teleport**: implied speed > 15 m/s between events · battery rising while not charging. |
| **By absence** (watchdog) | no event from a vehicle for > 5 s → `comms_loss`. |

Thresholds live in [`app/models.py`](app/models.py) as named constants.
Zones are a hardcoded startup constant of 20 realistic warehouse areas —
`inbound_dock_a`, `receiving_staging`, `aisle_a…c`, `high_bay_1/2`,
`pick_zone_1/2`, `pack_station`, `sort_belt`, `outbound_dock_a/b`,
`charging_bay_1/2/3`, `maintenance_bay`, … — seeded at startup.

---

## 🖥️ Running interactively

`docker compose up` (see [Quick start](#-quick-start--run-it-end-to-end)) is the
intended way to run the whole system — `docker-compose.yml` serves the primary +
streaming replica + Redis + CDC consumer, the uvicorn-served ingestion (`:8001`)
and frontend (`:8002`) APIs, the nginx-served dashboard (`:8080`), and the k6
load. Run migrations + zone seeding happen in a one-shot `migrate` step the APIs
wait on.

To run a service by hand against your own Postgres + Redis:

```bash
export DATABASE_URL=postgresql://fleet:fleet@localhost:5432/fleet
export REPLICA_URL=$DATABASE_URL          # single-node: replica = primary
export REDIS_URL=redis://localhost:6379/0

uv run python -m app.migrate              # apply migrations + seed the 20 zones
uv run uvicorn app.ingestion_api:app --port 8001
uv run uvicorn app.frontend_api:app  --port 8002
uv run python -m app.cdc_consumer         # singleton WAL → Redis publisher

cd web && npm install && npm run dev       # Vite dev server for the dashboard
```

The dashboard reads its API/WS targets from `VITE_API_BASE_URL` / `VITE_WS_URL`
(default same-origin), so point them at the frontend API when serving it apart.

---

## 🧪 Testing & proofs

Each capability ships behind an executable proof run against the real topology.

```bash
# Whole backend suite (primary + replica + redis + cdc)
docker compose -f docker-compose.test.yml run --rm api pytest

# Targeted proofs
docker compose -f docker-compose.test.yml run --rm api pytest tests/integration/test_ingest_fleet_state.py   # 50-vehicle concurrent aggregate
docker compose -f docker-compose.test.yml run --rm api pytest tests/integration/test_zone_counts.py          # 50 concurrent zone entries → count == 50
docker compose -f docker-compose.test.yml run --rm api pytest tests/integration/test_fault_transition.py     # concurrent + duplicate faults → exactly one cancel/record
docker compose -f docker-compose.test.yml run --rm api pytest tests/integration/test_anomalies.py            # every rule's fire/no-fire boundary
docker compose -f docker-compose.test.yml run --rm api pytest tests/integration/test_realtime_ws.py          # POST → WebSocket delta, sub-second

# Dashboard
docker compose -f docker-compose.test.yml run --rm web npm run test:ui
```

---

## 📁 Repository layout

```
app/
  ingestion_api.py        # stateless write API: POST /telemetry, /vehicles/{id}/status
  frontend_api.py         # read + WebSocket API (snapshot from replica, WS deltas)
  persistence.py          # atomic upsert, zone increment, anomaly detection, aggregate
  cdc.py / cdc_consumer.py# pgoutput logical decode → Redis (singleton)
  events.py               # fleet:events channel + the 3 event types
  watchdog.py             # by-absence comms-loss detector
  models.py               # TelemetryEvent, ZONES, anomaly thresholds
  migrate.py              # versioned migration runner + zone seeding
  migrations/             # 0001–0009 schema (+ CDC publication)
web/                      # Vite + React 18 + TypeScript dashboard
  src/                    # transport, stores, VehicleList/Row, ZoneTiles, anomalies
k6/fleet-simulation.js    # stateful 50-vehicle continuous load simulation
tests/integration/        # proofs against the real Docker topology
docker/                   # primary pg_hba + replica bootstrap (pg_basebackup)
docker-compose.yml        # runtime stack: serves the full app + k6 (one command)
docker-compose.test.yml   # test harness: real topology, runs the proof suites
docs/ADR.md               # architecture decision record
```

---

## 🧰 Tech stack

**Backend** — Python ≥ 3.14 · FastAPI · uvicorn · psycopg 3 (+ pool) · Pydantic
v2 · PostgreSQL 16 (logical replication, pgoutput) · Redis 7 · `uv`
**Frontend** — React 18 · TypeScript · Vite · Vitest + Testing Library · nginx
**Load** — Grafana k6 (stateful, continuous fleet simulation)
**Observability** — OpenTelemetry (traces + metrics) → Grafana Alloy → Tempo +
Prometheus → Grafana · `@opentelemetry/sdk-trace-web` in the browser
**Infra** — Docker Compose: primary + streaming replica + Redis + singleton CDC

---

## 🏭 How it was built

Constructed as a phased, anti-waterfall **Ratchet batch** — each phase a runnable
vertical slice behind an executable proof-of-work, decomposed lazily as the prior
phase's real shape emerged, and committed semantically once green.

| Phase | Ships |
|---|---|
| 1 | Telemetry ingestion + persistence + fleet aggregate |
| 2 | Zone-traversal counter (atomic increments) |
| 3 | Real-time anomaly detection + query |
| 4 | Atomic fault transition (mission cancel + maintenance record) |
| 5 | CDC → WebSocket propagation (pgoutput) |
| 6 | Live React + TypeScript dashboard |
| + | Single-command runtime stack + continuous k6 fleet simulation |
