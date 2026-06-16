# AI Build Log — apply comms-loss-watchdog

- **Session id:** 08ddfba8-20260616
- **Session name:** apply — comms-loss-watchdog
- **Step:** apply
- **Change:** comms-loss-watchdog
- **Batch / phase:** fleet-telemetry-service / anomaly-detection-and-query
- **Date:** 2026-06-16

## Brief

The final sibling of the `anomaly-detection-and-query` phase. The `anomaly-detection`
root change wrote the event-triggered anomalies synchronously inside the ingest
transaction, and `anomaly-query-endpoint` exposed them over `GET /anomalies`. The
phase's remaining default rule is the odd one out: **comms loss — no event from a
vehicle for >5s**. It is a *by-absence* rule with no triggering event to ride on,
so it cannot live inside the ingest transaction; it needs a background sweep over
the last-seen times in `vehicle_current_state`. This change adds the timeout
constant, the `detect_comms_loss(now)` sweep seam, a thin standalone watchdog
process, the change-scoped test, and the comms-loss-gap scenario that completes
the phase proof. Plan tasks 1.1–6.1 completed; the phase proof passes (12/12),
full integration suite 61/61 (exit 0).

## Artifacts written

- `app/models.py` — added `COMMS_LOSS_TIMEOUT_SECONDS = 5` to the default-threshold
  constant block, alongside low-battery / overspeed / stuck / teleport. (1.1)
- `app/persistence.py` — added `detect_comms_loss(now)`: in one transaction, a
  single `INSERT ... SELECT` flags every vehicle in `vehicle_current_state` whose
  last `recorded_at` is **strictly older** than `now - COMMS_LOSS_TIMEOUT_SECONDS`
  and that does not already carry a `comms_loss` anomaly with `detected_at >=` that
  reading, writing one `comms_loss` row per such vehicle with `detected_at = now`.
  The `NOT EXISTS` correlation makes the sweep idempotent — flagged once per silence
  episode, re-arming only after the vehicle reports a newer reading. Returns the
  number flagged. Reuses the existing `anomalies` table; no migration. (2.1, 2.2)
- `app/watchdog.py` — a thin `run_watchdog(interval)` loop calling
  `detect_comms_loss(now=datetime.now(timezone.utc))` every ~1s, plus a `__main__`
  entrypoint, run as a **separate process** from the stateless ingestion API. All
  behaviour lives in the seam; the loop is a trivial driver. (3.1)
- `tests/integration/test_comms_loss.py` — drives the seam directly with an injected
  `now` (deterministic, no sleeps) against the real Postgres: the strict timeout
  boundary (gap >5s flagged; exactly 5s and <5s not), the once-per-episode guard
  (continuing silence flags once, re-flags afresh after recovery), and the empty
  sweep. (4.1, 4.2)
- `tests/integration/test_anomalies.py` — appended the comms-loss-gap scenario to the
  phase proof: POST a reading via the ingestion API, run the watchdog sweep at a
  `now` past the timeout, and assert `GET /anomalies` returns `comms_loss`; a vehicle
  still within the timeout is not flagged. (5.1)

## Design alignment

- **By-absence detection is necessarily out-of-transaction — and does not violate
  the telemetry-architecture standard.** The standard mandates *event-triggered*
  detection stay synchronous inside the ingest transaction. Comms loss has no
  triggering event (the signal is the *absence* of one), so there is nothing to
  attach a transaction to. The watchdog is a distinct by-absence class, not the
  forbidden "make event-driven detection async" move; no in-transaction guarantee
  is weakened. No standard change required.
- **Separate process keeps the ingestion API stateless.** The sweep loop lives in
  its own `app/watchdog.py`, not the ingestion API's lifespan, preserving
  validate → write → return. It writes to the same `anomalies` table; the row is the
  only signal, so no in-process buffer can diverge from committed state.
- **Idempotent in one statement.** The `NOT EXISTS` guard ("no `comms_loss` at or
  after this vehicle's current `recorded_at`") fires exactly once per gap and
  re-arms when the vehicle's `recorded_at` advances — enforced in the single sweep
  statement, not in application read-then-write state.
- **Strict timeout, matching the other rules.** `recorded_at < now - 5s` fires;
  exactly at 5s does not — mirroring the strict comparisons used for battery 15 and
  speed 5.
- **Reuse, don't reinvent.** Extends the existing model constants, persistence
  module, and `anomalies` table; introduces no new datastore and, beyond the thin
  loop, no new framework. At ~50 vehicles the sweep is a trivial scan; no new index.

## Outcome

`docker compose -f docker-compose.test.yml run --rm api pytest
tests/integration/test_anomalies.py` → exit 0, 12 passed (11 prior + the
comms-loss gap). Change-scoped `test_comms_loss.py` → 3 passed. Full
`tests/integration` suite → 61 passed (57 prior + 4 new), exit 0. The api image
was rebuilt so the new source/tests are present in the container. Plan tasks
1.1–6.1 checked off. With this landed, every default rule in the phase success
criteria — stateless, stateful, and by-absence — fires exactly when its condition
is met and is queryable over `GET /anomalies`; the `anomaly-detection-and-query`
phase is complete.
