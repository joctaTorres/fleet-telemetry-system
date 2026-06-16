# AI Build Log — Propose Standard: Real-Time Telemetry Architecture

- **Session id:** 8b137e8-20260616182352
- **Session name:** propose-standard — fleet telemetry monitoring architecture
- **Step:** propose-standard
- **Standard authored:** `.ratchet/standards/telemetry-architecture.md` (tag: `telemetry-architecture`)
- **Change:** none (authoring a standard does not create a change)

## Brief

Authored a new project standard capturing the system architecture decision for the
fleet telemetry monitoring service (ADR provided by the user). The standard encodes the
chosen design — CDC-based event propagation within a Postgres primary/replica split and a
Redis fan-out layer — as concrete, checkable guidelines that `/rct:propose` and
`/rct:verify` apply to every change.

### Guidelines captured

- **Topology:** two separate APIs — stateless ingestion (write-only, never publishes to a
  broker in-request) and frontend (WebSocket push + REST snapshot, no authoritative state).
- **Read/write separation:** writes to a single primary; REST reads from a streaming read
  replica; the real-time path does not depend on the replica.
- **Event propagation:** CDC from the logical replication slot → Redis → WebSocket. Dual-write,
  `LISTEN/NOTIFY`, and replica polling are explicitly rejected. Single CDC consumer with
  hot-standby HA via bookmarked LSN. Anomaly detection stays synchronous in the ingest txn.
- **Concurrency control (DB-enforced):** atomic `x = x + 1` counters (no app-level
  read-then-write), pessimistic `SELECT … FOR UPDATE` + idempotent multi-table fault
  transitions, and fleet aggregate derived from a per-vehicle current-state upsert table.
- **Operational safety:** monitor logical slot lag with a WAL retention cap and alert.

## Outcome

Standard written successfully. It is now loaded automatically by `/rct:propose` (baked into
every plan) and `/rct:verify` (checked against every change). No change directory was created,
per the command guardrails. Tag `telemetry-architecture` confirmed unique against existing
standards (`tech-stack`, `ai-build-logging`).
