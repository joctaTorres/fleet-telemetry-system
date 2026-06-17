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
> Architecture rationale lives in [`docs/ADR.md`](docs/ADR.md).

---

## ⚡ Quick start

Everything runs through one Docker Compose stack that stands up the
**production-shaped topology** — Postgres primary (logical WAL) + streaming read
replica + Redis + the singleton CDC consumer — and exercises it end to end.

```bash
# Backend — full integration suite against the real topology
docker compose -f docker-compose.test.yml run --rm api pytest

# Real-time path, end to end:  POST → WAL → pgoutput CDC → Redis → WebSocket
docker compose -f docker-compose.test.yml run --rm api pytest tests/integration/test_realtime_ws.py

# Dashboard — React + TypeScript component tests (mocked transport)
docker compose -f docker-compose.test.yml run --rm web npm run test:ui

# Tear everything down (removes volumes)
docker compose -f docker-compose.test.yml down -v
```

That's the whole system, proven: concurrent zone bursts, atomic fault
transitions, anomaly detection, and a sub-second POST→WebSocket delta.

> **Note** — this is a proof-driven slice. The Compose file is the integration
> harness; the `api`/`web` containers default to running their test suites and
> expose no public ports. There is no standalone ASGI server wired up yet (see
> [Running interactively](#-running-interactively)).

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
Zones are a hardcoded startup constant of 20: `zone-01 … zone-20`.

---

## 🖥️ Running interactively

The Compose stack is a test harness (no exposed ports, no ASGI server). To drive
the system by hand, point the apps at a running Postgres + Redis and serve them
with an ASGI server (add `uvicorn` to the environment first):

```bash
export DATABASE_URL=postgresql://fleet:fleet@localhost:5432/fleet_test
export REPLICA_URL=$DATABASE_URL          # single-node: replica = primary
export REDIS_URL=redis://localhost:6379/0

uv run python -m app.migrate              # apply migrations + seed the 20 zones
uv run uvicorn app.ingestion_api:app --port 8001
uv run uvicorn app.frontend_api:app  --port 8002
uv run python -m app.cdc_consumer         # singleton WAL → Redis publisher

cd web && npm install && npm run dev       # Vite dev server for the dashboard
```

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
tests/integration/        # proofs against the real Docker topology
docker/                   # primary pg_hba + replica bootstrap (pg_basebackup)
docs/ADR.md               # architecture decision record
```

---

## 🧰 Tech stack

**Backend** — Python ≥ 3.14 · FastAPI · psycopg 3 (+ pool) · Pydantic v2 ·
PostgreSQL 16 (logical replication, pgoutput) · Redis 7 · `uv`
**Frontend** — React 18 · TypeScript · Vite · Vitest + Testing Library
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
