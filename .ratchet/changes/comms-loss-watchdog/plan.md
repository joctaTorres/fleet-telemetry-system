# comms-loss-watchdog

## Why

The `anomaly-detection` change built the root of phase 3
(`anomaly-detection-and-query`) — the `anomalies` table, the synchronous
stateless + stateful detection in `persist_telemetry`, and the
`recent_anomalies` read seam — and `anomaly-query-endpoint` exposed it over
`GET /anomalies`. Both cover anomalies triggered **by an event arriving**. The
phase's remaining default rule is the odd one out: **comms loss — no event from a
vehicle for >5s**. It is a *by-absence* rule. There is no telemetry event to ride
on, so it cannot be detected inside the ingest transaction like the others; it
needs a background sweep over the last-seen times in `vehicle_current_state`.

This change is the final sibling of the phase. It adds the comms-loss timeout
constant, a `detect_comms_loss(now)` sweep seam in `app/persistence.py`, a thin
standalone watchdog process that runs the sweep on an interval, and the
comms-loss-gap scenario that completes the phase proof
`tests/integration/test_anomalies.py`. With it landed, every default rule in the
phase success criteria — stateless, stateful, and by-absence — fires exactly when
its condition is met and is queryable over `GET /anomalies`.

## What Changes

- **Threshold** (`app/models.py`): add `COMMS_LOSS_TIMEOUT_SECONDS = 5` to the
  default-threshold constant block, alongside the existing low-battery / overspeed
  / stuck / teleport constants.
- **Sweep seam** `detect_comms_loss(now)` in `app/persistence.py`: in one
  transaction, select every vehicle in `vehicle_current_state` whose last
  `recorded_at` is **strictly older** than `now - COMMS_LOSS_TIMEOUT_SECONDS`
  **and** that does not already have a `comms_loss` anomaly with
  `detected_at >=` that vehicle's last `recorded_at`, then `INSERT` one
  `comms_loss` anomaly per such vehicle with `detected_at = now`. The `NOT EXISTS`
  guard makes the sweep idempotent: a vehicle is flagged once per silence episode,
  and becomes eligible again only after it reports a newer reading. Reuses the
  existing `anomalies` table and `_INSERT_ANOMALY` shape — no migration, no new
  `anomaly_type` plumbing beyond the literal string.
- **Watchdog process** `app/watchdog.py`: a thin `run_watchdog()` loop that calls
  `detect_comms_loss(now=<current UTC time>)` every ~1s, plus a `__main__`
  entrypoint, run as a **separate process** from the stateless ingestion API. The
  loop is deliberately minimal — all behaviour lives in the testable seam.
- **Change-scoped integration test** `tests/integration/test_comms_loss.py`:
  drives `detect_comms_loss(now)` directly with an injected `now` (deterministic,
  no sleeps) against the real Postgres, covering the timeout boundary, the
  once-per-episode guard, and re-flagging after recovery.
- **Phase proof completion** — append the comms-loss-gap scenario(s) to
  `tests/integration/test_anomalies.py`: POST a reading via the ingestion API, run
  the watchdog sweep at a `now` past the timeout, and assert `comms_loss` is
  returned by `GET /anomalies`; and that a vehicle still within the timeout is not
  flagged.

## Design

- **Vertical-slice scope.** The thinnest slice that proves the by-absence rule end
  to end: last-seen state → `detect_comms_loss(now)` sweep → `comms_loss` insert →
  readable via the existing `GET /anomalies`. The interval loop is a trivial driver
  over the seam; the WebSocket/CDC push of the gap belongs to later phases and is
  out of scope.
- **By-absence detection is necessarily out-of-transaction — and that does not
  violate the telemetry-architecture standard.** The standard mandates that
  *event-triggered* anomaly detection stay synchronous inside the ingest
  transaction ("the `anomalies` INSERT is the event"). Comms loss has **no
  triggering event** — the signal is the *absence* of one — so there is nothing to
  attach a transaction to. The watchdog is a distinct by-absence class, not the
  forbidden "make event-driven detection async" move; it does not weaken any
  in-transaction guarantee. No standard change is required.
- **Runs as a separate process, keeping the ingestion API stateless.** The standard
  requires the ingestion API to be `validate → write → return` with no background
  state. So the sweep loop lives in its own `app/watchdog.py` process, not in the
  ingestion API's lifespan. It writes to the same `anomalies` table; the row is the
  only signal, so there is no in-process buffer that can diverge from committed
  state.
- **Idempotent: one comms_loss per silence episode.** Without a guard the sweep
  would re-flag a silent vehicle on every tick. The `NOT EXISTS` correlation —
  "no `comms_loss` row at or after this vehicle's current `recorded_at`" — fires
  exactly once per gap and re-arms automatically when the vehicle reports a newer
  reading (its `recorded_at` advances past the prior anomaly). This is enforced in
  the single sweep statement, not in application read-then-write state.
- **Strict timeout, matching the other rules.** `recorded_at < now - 5s` fires;
  exactly at 5s does not. Mirrors the strict comparisons used for battery 15 and
  speed 5 so the boundary is unambiguous.
- **Deterministic tests by injecting `now`.** The seam takes `now` as a parameter
  (the loop passes the real clock), exactly as the existing tests inject
  `recorded_at`, so the proof needs no sleeps or wall-clock races.
- **Reuse, don't reinvent.** Extends the existing model constants, persistence
  module, and `anomalies` table; introduces no new datastore and (beyond the thin
  loop process) no new framework — consistent with the tech-stack standard. At ~50
  vehicles the sweep is a trivial scan of `vehicle_current_state`; no new index is
  needed.
- **Testing.** `tests/integration/test_comms_loss.py` and the appended
  `tests/integration/test_anomalies.py` scenarios run against the real Postgres from
  `docker-compose.test.yml`. Proof-of-work for the phase:
  `docker compose -f docker-compose.test.yml run --rm api pytest
  tests/integration/test_anomalies.py` passes (exit 0) with the comms-loss gap now
  covered alongside every other default class.

## Tasks

- [x] 1.1 Add `COMMS_LOSS_TIMEOUT_SECONDS = 5` to the default-threshold constants in `app/models.py`
- [x] 2.1 Add `detect_comms_loss(now)` to `app/persistence.py`: in one transaction, select vehicles in `vehicle_current_state` whose `recorded_at` is strictly older than `now - COMMS_LOSS_TIMEOUT_SECONDS` and that lack a `comms_loss` anomaly with `detected_at >=` their `recorded_at`, and `INSERT` one `comms_loss` row per such vehicle with `detected_at = now`
- [x] 2.2 Ensure the sweep is idempotent — a continuing silence flags a vehicle once and re-arms only after it reports a newer reading
- [x] 3.1 Add `app/watchdog.py`: a thin `run_watchdog()` interval loop calling `detect_comms_loss(now=<current UTC time>)` plus a `__main__` entrypoint, as a separate process from the ingestion API
- [x] 4.1 Integration test `tests/integration/test_comms_loss.py`: a vehicle silent past 5s is flagged; one within 5s and one exactly at 5s are not (strict boundary)
- [x] 4.2 Integration test: a continuing silence does not re-fire (exactly one `comms_loss` per episode); a vehicle that reports again and then goes silent is flagged afresh
- [x] 5.1 Append the comms-loss-gap scenario to `tests/integration/test_anomalies.py`: POST a reading via the ingestion API, run the sweep at a `now` past the timeout, and assert `GET /anomalies` returns `comms_loss`; a vehicle within the timeout is not flagged
- [x] 5.2 Confirm the phase proof passes: `docker compose -f docker-compose.test.yml run --rm api pytest tests/integration/test_anomalies.py` exits 0 with the comms-loss gap covered
- [x] 6.1 Write the AI build-log report to `docs/ai-build-logs/*.md` and append one line to `docs/ai-build-logs/index.md`
