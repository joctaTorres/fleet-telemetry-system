# AI Build Log — apply zone-counts-endpoint

- **Session id:** 08ddfba8-20260616
- **Session name:** apply — zone-counts-endpoint
- **Step:** apply
- **Change:** zone-counts-endpoint
- **Batch / phase:** fleet-telemetry-service / zone-traversal-counter
- **Date:** 2026-06-16

## Brief

The read half of the `zone-traversal-counter` phase. The root change
`zone-counts-increment` built the `zone_counts` table, the ~20-zone seed, the
atomic `UPDATE ... SET entry_count = entry_count + 1 WHERE zone_id = $1` in
`persist_telemetry`, and the `zone_entry_counts()` read seam — but those live
per-zone totals had no network surface. This change adds `GET /zones/counts` to
the frontend API as a thin adapter over `zone_entry_counts()`, completing the
phase's end-to-end slice (POST with `zone_entered` writes → GET reads the counts
back), and lands the phase proof-of-work `tests/integration/test_zone_counts.py`.
All plan tasks 1.1–4.1 completed; the integration suite passes against a real
Postgres (27/27, exit 0).

## Artifacts written

- `app/frontend_api.py` — added `GET /zones/counts`: calls the existing
  `zone_entry_counts()` and returns 200 OK with the live per-zone totals as JSON
  (`{"zone-01": n, …, "zone-20": n}`). Because the seed guarantees a row per
  known zone, all ~20 zones are always present; never-entered zones report `0`.
  Mirrors how `GET /fleet/state` wraps `aggregate_fleet_state()`. Updated the
  module docstring to list both read routes. (1.1)
- `tests/integration/test_zone_counts.py` — phase proof-of-work, both apps driven
  in-process with FastAPI `TestClient` against the same real Postgres:
  - (2.1) freshly-seeded DB → GET returns 200 and all ~20 zones at 0.
  - (2.2) after a mix of zone entries → GET reports each zone's live total and
    leaves every other zone at 0.
  - (3.1) 50 concurrent `zone_entered` events for one zone via the ingestion API
    → GET reports that zone's count `== 50` exactly (no lost increments).
  - (3.2) `zone_entered=null` events → every zone's count unchanged (all 0).
  - (3.3) GET returns all ~20 seeded zones even when only one was entered.

## Design alignment

- **Vertical-slice scope:** the thinnest HTTP read path that proves the phase
  goal — request → `zone_entry_counts()` → JSON. No WebSocket, no CDC/Redis, no
  `zone_count_changed` push; those belong to later phases.
- **Reuse the read seam.** The endpoint is a thin adapter over the existing
  single-snapshot `SELECT zone_id, entry_count FROM zone_counts`; concurrency
  correctness stays in the database (the atomic `UPDATE` from the root change),
  not in any in-process counter.
- **Separate APIs, per `telemetry-architecture`.** The read route goes on the
  frontend API (which already owns `GET /fleet/state`), keeping the ingestion API
  stateless and write-only.
- **All zones zero-filled.** The seed guarantees a row per zone, so the endpoint
  always returns all ~20 zones — the same zero-fill discipline as the fleet
  aggregate.
- **Scoped deviation:** read served from the primary (single Postgres this
  phase), consistent with `GET /fleet/state`; a later read/write-split phase
  moves it to a replica. No standard change required.

## Outcome

`docker compose -f docker-compose.test.yml run --rm api pytest
tests/integration/test_zone_counts.py` → exit 0, 5 passed (api image rebuilt so
the new source/tests are present in the container). Full suite
`tests/integration` → 27 passed (22 prior + 5 new). Plan tasks 1.1–4.1 checked
off. This completes the `zone-traversal-counter` phase's end-to-end slice.
