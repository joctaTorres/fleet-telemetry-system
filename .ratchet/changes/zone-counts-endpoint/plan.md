# zone-counts-endpoint

## Why

The `zone-counts-increment` change built the root of phase 2 (`zone-traversal-counter`):
the `zone_counts` table, the ~20-zone seed, the atomic
`UPDATE ... SET entry_count = entry_count + 1 WHERE zone_id = $1` wired into
`persist_telemetry`, and the `zone_entry_counts()` read seam in `app/persistence.py`. But
those live per-zone totals have no network surface yet: the dashboard cannot read them.
This change adds the read half of the phase, `GET /zones/counts`, turning the existing read
seam into a reachable REST endpoint and completing the phase's end-to-end slice
(POST with `zone_entered` writes → GET reads the counts back). It also lands the phase
proof-of-work `tests/integration/test_zone_counts.py`.

## What Changes

- Add `GET /zones/counts` to the existing **frontend API** (`app/frontend_api.py`), the
  dedicated FastAPI app kept separate from the stateless ingestion API per the
  telemetry-architecture standard.
- The route calls the existing `zone_entry_counts()` and returns **200 OK** with a JSON
  object of live per-zone totals: `{"zone-01": n, ..., "zone-20": n}`. All ~20 seeded zones
  are always present; never-entered zones report `0`.
- Add the phase proof-of-work integration test `tests/integration/test_zone_counts.py`:
  fire N concurrent `zone_entered` events for one zone via the ingestion API and assert
  `GET /zones/counts` reports that zone's `entry_count == N` exactly (zero lost increments);
  `zone_entered=null` events leave all counts unchanged; the response lists all ~20 seeded
  zones.

## Design

- **Vertical-slice scope:** the thinnest HTTP read path that proves the phase goal —
  request → `zone_entry_counts()` → JSON. No WebSocket, no CDC/Redis, no
  `zone_count_changed` push; those belong to later phases. This change adds only the HTTP
  adapter and the proof-of-work test.
- **Reuse the read seam, don't reinvent.** `zone_entry_counts()` already returns the live
  per-zone totals from a single `SELECT zone_id, entry_count FROM zone_counts` in one MVCC
  snapshot. The endpoint is a thin adapter over it — mirroring how `GET /fleet/state` wraps
  `aggregate_fleet_state()`.
- **Separate APIs, per the telemetry-architecture standard.** The read endpoint goes on the
  frontend API, *not* the ingestion API: the ingestion API MUST stay stateless and
  write-only (`validate → write → return`). The frontend API already owns `GET /fleet/state`;
  this adds a second read route to the same app.
- **No authoritative in-process counter.** The frontend API derives the per-zone totals
  fresh from the database on each request; it holds no in-process counter that could diverge
  from committed state. Concurrency correctness stays in the database — the increment is the
  server-side atomic `UPDATE` from the root change, and the read is a single snapshot.
- **All zones always present, zero-filled.** Because the seed guarantees a row per known
  zone, the endpoint always returns all ~20 zones; never-entered zones report `0`, the same
  way the fleet aggregate zero-fills every status.
- **Scoped deviation — reads from the primary, not a replica.** Consistent with
  `GET /fleet/state`: this phase is scoped to a single Postgres (no replica/CDC/Redis yet),
  so the read is served from the primary. A later read/write-split phase moves it to the
  replica; no standard change is required.
- **Testing.** Exercised in-process with FastAPI's `TestClient` against the real Postgres
  from `docker-compose.test.yml`. The proof-of-work test `tests/integration/test_zone_counts.py`
  drives the ingestion app (POST telemetry with `zone_entered`) and the frontend app
  (GET `/zones/counts`) against the same database, covering the concurrent-burst,
  null-zone, and all-zones-present cases.

## Tasks

- [x] 1.1 Add `GET /zones/counts` to `app/frontend_api.py`: call `zone_entry_counts()` and return 200 with the live per-zone totals as JSON, all ~20 seeded zones always present and zero-filled
- [x] 2.1 Integration test: GET `/zones/counts` against a freshly seeded database returns 200 and all ~20 zones with count 0
- [x] 2.2 Integration test: after persisting a mix of zone entries, GET `/zones/counts` reports each zone's live total and leaves other zones unchanged
- [x] 3.1 Proof-of-work test `tests/integration/test_zone_counts.py`: fire N concurrent `zone_entered` events for one zone via the ingestion API, then assert GET `/zones/counts` reports that zone's count `== N` exactly (no lost increments)
- [x] 3.2 Proof-of-work test `tests/integration/test_zone_counts.py`: `zone_entered=null` events leave every zone's count unchanged
- [x] 3.3 Proof-of-work test `tests/integration/test_zone_counts.py`: GET `/zones/counts` returns all ~20 seeded zones
- [x] 4.1 Write the AI build-log report to `docs/ai-build-logs/*.md` and append one line to `docs/ai-build-logs/index.md`
