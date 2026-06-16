# AI Build Logs — Session Index

Append-only log of AI build sessions. One line per session (id, name, brief). Newest
entries go at the bottom. See each session's report in this directory.

| Session id | Name | Brief |
| --- | --- | --- |
| 8b137e8-20260616182352 | propose-standard — fleet telemetry monitoring architecture | Authored `telemetry-architecture` standard: CDC propagation, primary/replica split, DB-enforced concurrency control. |
| 73c568d0-20260616 | propose — telemetry-persistence | Proposed root change: schema (`raw_events`, `vehicle_current_state`) + idempotent upsert write path and `GROUP BY` fleet aggregate; features + plan, no implementation. |
