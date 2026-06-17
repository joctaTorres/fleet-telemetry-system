# AI Build Log — apply websocket-fanout

- **Session id:** 08ddfba8-20260616
- **Session name:** apply — websocket-fanout
- **Step:** apply
- **Change:** websocket-fanout
- **Batch / phase:** fleet-telemetry-service / realtime-cdc-websocket
- **Date:** 2026-06-16

## Brief

The root change of the `realtime-cdc-websocket` phase and the **fan-out half** of
it: it introduces Redis as the pub/sub broker and makes the frontend API
stateful — an async `@app.websocket("/ws")` endpoint with a connection registry,
a one-shot snapshot on connect, and a single async Redis subscriber (started in a
FastAPI lifespan) that fans every published state patch out to all connected
clients. The REST read handlers are untouched and the ingestion API still touches
no Redis.

The slice stops at the Redis channel as a seam: there is no CDC consumer yet, so
the proof publishes state patches to Redis directly (standing in for the
not-yet-built consumer) and asserts a connected WebSocket client receives them
sub-second. The read replica, the logical-slot CDC consumer, the real `POST → WS`
flow, and the full phase blackbox `tests/integration/test_realtime_ws.py` are out
of scope — they land in the `cdc-consumer` follow-on. All plan tasks 1.1–5.1
completed; the integration suite passes against real Postgres + Redis (77/77,
exit 0).

## Artifacts written

- `pyproject.toml` / `uv.lock` — added the async Redis client (`redis>=8.0.0`,
  redis-py with asyncio) via `uv add --no-sync` (local sync is blocked by
  `psycopg-binary` lacking a free-threaded 3.14t wheel; the Docker `api` image
  builds on stock `python:3.14-slim` and installs it cleanly). (1.1)
- `app/config.py` — added `get_redis_url()` reading `REDIS_URL` from the
  environment with **no** hard-coded fallback, raising `ConfigError` when missing,
  mirroring `get_dsn()`. (1.2)
- `docker-compose.test.yml` — added a `redis:7-alpine` service with a
  `redis-cli ping` healthcheck, set `REDIS_URL=redis://redis:6379/0` on the `api`
  container, and added `redis` to its `depends_on` (`condition: service_healthy`).
  (1.3)
- `app/events.py` (new) — the event channel contract shared by the future CDC
  publisher and the frontend subscriber: the channel constant `fleet:events`, the
  three delta types (`vehicle_state_changed`, `anomaly_detected`,
  `zone_count_changed`), the `snapshot` envelope type, and an `EVENT_TYPES` set.
  (2.1)
- `app/frontend_api.py` — made the frontend API stateful without holding
  authoritative data:
  - `ConnectionRegistry` — an `asyncio.Lock`-guarded set of live `WebSocket`
    connections with `add`/`remove` and a `broadcast()` that sends to every
    connection and drops any that error on send. (2.2)
  - `_redis_subscriber()` + a FastAPI `lifespan` — one subscription per process
    on startup; each published message is forwarded verbatim via `broadcast()`;
    the task is cancelled and the client closed cleanly on shutdown. (2.3)
  - `@app.websocket("/ws")` — accept, register, send a one-shot `snapshot`
    envelope built fresh from `aggregate_fleet_state()` + `zone_entry_counts()`
    (run in a worker thread, since they are blocking DB reads), then hold the
    connection open (reading to detect disconnect) to stream deltas, unregistering
    on disconnect. (2.4)
  - `GET /fleet/state`, `GET /zones/counts`, `GET /anomalies` left unchanged;
    the ingestion API publishes nothing to Redis. (2.5)
- `tests/integration/conftest.py` — a `redis_client` fixture (synchronous client
  for the test side; `flushdb` before and after each test) and a `publish_event`
  helper that puts a JSON envelope on `fleet:events` and returns the subscriber
  count. (3.1)
- `tests/integration/test_ws_fanout.py` (new) — the change's proof-of-work, the
  frontend ASGI app driven in-process with `TestClient` (the `with` block runs the
  lifespan, so the subscriber attaches) against real Postgres + Redis:
  - (4.1) first WS message is a one-shot snapshot of committed fleet + per-zone
    state.
  - (4.2) a published `vehicle_state_changed` patch reaches the client within the
    sub-second bound.
  - (4.3) one patch of each of the three event types is forwarded with its `type`
    preserved.
  - (4.4) a single published patch is fanned out to two connected clients.
  - (4.5) with nothing published, no further message arrives after the snapshot.
  - (4.6) a disconnected client is dropped and does not block fan-out to the
    survivor.

## Design alignment

- **Vertical-slice scope.** The thinnest slice proving the fan-out half end to
  end: connect → snapshot → publish-to-Redis → client receives the exact patch
  sub-second. Replica, CDC consumer, real `POST → WS`, and the full phase proof
  are the `cdc-consumer` follow-on — mirroring how `fault-transition-core` proved
  at a seam and left the endpoint + phase proof to its follow-on.
- **CDC, not dual-write — preserved.** The frontend only emits what it observes on
  the subscribed channel; it synthesizes nothing. The ingestion API stays
  stateless/write-only and touches no Redis. Publishing to Redis from the test is
  the stand-in for the not-yet-built CDC consumer; the production publisher remains
  CDC.
- **Stateful connections, not stateful data.** Per `telemetry-architecture`, the
  only retained state is the connection registry; the snapshot is derived fresh
  from the DB read seams on each connect, so any frontend instance can serve any
  client.
- **Snapshot on connect, then deltas.** The first WS message is a full snapshot;
  everything after is an individual patch — immediate full render, no polling, no
  re-render.
- **One async subscriber, many connections.** A single Redis subscription per
  process fans each message out to all registered connections (pub/sub fan-in
  shared, WebSocket fan-out per-connection).
- **Scoped deviation — snapshot from the primary.** The replica arrives with
  `cdc-consumer`; until then the snapshot reads from the primary, consistent with
  the existing documented deviation. The delta path does not depend on the
  replica.
- **Config from the environment.** `REDIS_URL` is read with no baked-in fallback,
  matching `get_dsn()` and the tech-stack standard.

## Notes / deviations

- **Pub/sub delivery race.** Redis pub/sub does not buffer, and the lifespan
  subscriber's `SUBSCRIBE` completes asynchronously. The proof's
  `_publish_until_delivered` retries `PUBLISH` until it reports a receiver, so
  exactly the delivering publish reaches the subscriber — deterministic without a
  sleep.
- **Sub-second enforcement under `TestClient`.** `receive_json` blocks with no
  timeout, so the proof receives on a daemon thread bounded by `SUB_SECOND` (1.0s);
  a daemon thread leaves nothing hanging in the deliberate "no message" case (4.5).

## Outcome

`docker compose -f docker-compose.test.yml run --build --rm api pytest
tests/integration/test_ws_fanout.py` → exit 0, 6 passed (api image rebuilt so the
new source/tests + redis dep are present, and the `redis` service is healthy).
Full suite `tests/integration` → 77 passed (71 prior + 6 new). Plan tasks 1.1–5.1
checked off. This lands the fan-out half of the `realtime-cdc-websocket` phase;
the CDC source and full phase proof are the `cdc-consumer` follow-on.
