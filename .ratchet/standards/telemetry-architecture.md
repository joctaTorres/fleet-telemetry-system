---
tag: telemetry-architecture
---

# Real-Time Telemetry Architecture

> Concern: architecture (data flow, concurrency, real-time propagation)

## Intent

Lock in the system architecture for the fleet telemetry monitoring service so every
change preserves two hard-won properties: (1) the dashboard's event stream can never
diverge from committed database state, and (2) concurrency correctness is enforced at
the PostgreSQL layer, not in application code. The scale is modest (~50 vehicles × 1 Hz);
the difficulty is correctness under concurrency and low-latency propagation without
coupling the write path to the read path. Changes that reintroduce dual-write
inconsistency, application-level read-modify-write, or per-client DB polling regress the
core design.

## Guidelines

**Topology & responsibilities**

- There MUST be two separate APIs: a **stateless ingestion API** (vehicle telemetry
  writes) and a **frontend API** (dashboard reads + WebSocket push). Do not merge them.
- The ingestion API MUST be stateless: validate → write to Postgres → return. It holds
  no session state and no authoritative in-process aggregates, and it MUST NOT publish to
  Redis or any broker in the request path (the event stream comes from CDC, not the
  writer).
- The frontend API may hold WebSocket connections but MUST NOT hold authoritative state.
  On connect it sends a one-shot REST snapshot, then streams deltas from Redis, so any
  instance can serve any client.

**Read/write separation**

- Writes go to a single **Postgres primary**. REST reads (fleet aggregate, anomaly
  history) MUST be served from a **streaming read replica**, never from the primary.
- The dashboard's real-time path MUST NOT depend on the replica; the replica's
  millisecond replication lag is acceptable only for the best-effort initial snapshot.

**Event propagation (CDC, not dual-write)**

- Committed changes reach the dashboard via **Change Data Capture**: a single CDC consumer
  tails the primary's logical replication slot and publishes derived events to Redis;
  frontend instances subscribe and fan out over WebSocket.
- Dual-write (commit then publish in the request path), Postgres `LISTEN/NOTIFY`, and
  timer-polling the replica are REJECTED for real-time propagation and MUST NOT be
  introduced.
- Exactly one CDC consumer reads the logical slot (it is a single-reader construct); HA is
  provided by a hot standby that resumes from the bookmarked LSN, not by a second active
  reader.
- Anomaly detection MUST stay synchronous inside the ingestion transaction — the
  `anomalies` INSERT is the event. CDC observes rows; it does not make detection async.
- The dashboard MUST receive updates over **WebSocket push**, not polling.

**Concurrency control (enforced in the database)**

- Counters MUST be incremented with a single server-side atomic statement
  (`UPDATE zone_counts SET entry_count = entry_count + 1 WHERE zone_id = $1`). An
  application-level read-then-write (`SELECT` count → `+1` → `UPDATE`) is FORBIDDEN — it
  loses updates under concurrency.
- Multi-table fault transitions (cancel active mission + create maintenance record + set
  vehicle status) MUST run in one transaction guarded by a pessimistic row lock
  (`SELECT … FOR UPDATE` on the vehicle row), and MUST be idempotent (transition guard +
  uniqueness constraint) to tolerate at-least-once delivery.
- Aggregate fleet state MUST be derived from a per-vehicle current-state table
  (`INSERT … ON CONFLICT (vehicle_id) DO UPDATE`, then `GROUP BY status`), not from a
  materialized counter that concurrent writers race on.

**Operational safety**

- Logical replication slot lag MUST be monitored, with a WAL retention cap
  (`max_slot_wal_keep_size`) and an alert on retention growth, so a stalled CDC consumer
  cannot fill the primary's disk.

## Applies to

Every change touching telemetry ingestion, event propagation to the dashboard, the
read/write split, the CDC consumer, Redis fan-out, WebSocket delivery, or
concurrency-sensitive writes (counters, fault transitions, fleet-state aggregation). A
change that proposes a different propagation mechanism or moves concurrency control into
application code MUST update this standard first to record the decision.
