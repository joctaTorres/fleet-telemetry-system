# websocket-fanout

## Why

Phase 5 (`realtime-cdc-websocket`) needs committed changes to propagate to the
dashboard over WebSocket with sub-second latency, derived from the WAL with no
dual-write. That phase has two halves: the **fan-out path** (an async WebSocket
surface fed by Redis pub/sub) and the **CDC source** (read replica + a singleton
consumer tailing the logical slot that publishes the derived events). This change
is the **root**: it introduces Redis and makes the frontend API stateful — an
async `@app.websocket` endpoint with a connection registry, a one-shot snapshot on
connect, and an async Redis subscriber that fans published state patches out to
every connected client. The REST read handlers stay exactly as they are.

It stops at the Redis channel as a seam: because there is no CDC consumer yet, the
slice is proven by **publishing state patches to Redis directly** from the test and
asserting a connected WebSocket client receives them sub-second. The follow-on
change `cdc-consumer` adds the read replica and the logical-slot consumer that
makes `POST -> WS` real and lands the full phase blackbox proof
`tests/integration/test_realtime_ws.py`. That replica, that consumer, and that
phase proof are explicitly out of scope here — exactly as `fault-transition-core`
stopped at a persistence seam and left the HTTP endpoint and full phase proof to
`status-update-endpoint`.

## What Changes

- **Dependency**: add the async Redis client (`redis` / redis-py with asyncio) via
  `uv add`. Redis is the pub/sub broker the telemetry-architecture standard already
  mandates for fan-out; it is not a second relational engine, so no standard change
  is required (the datastore of record remains PostgreSQL).
- **Config** `app/config.py`: add `get_redis_url()` reading `REDIS_URL` from the
  environment, with no hard-coded fallback — a missing value is a configuration
  error, mirroring `get_dsn()`.
- **Test stack** `docker-compose.test.yml`: add a `redis:7-alpine` service with a
  healthcheck and set `REDIS_URL=redis://redis:6379/0` on the `api` container (and
  add `redis` to its `depends_on` with `condition: service_healthy`).
- **Event channel + contract**: a single Redis pub/sub channel (e.g.
  `fleet:events`) carrying JSON messages, each with a `type` of
  `vehicle_state_changed`, `anomaly_detected`, or `zone_count_changed` plus its
  patch payload. The frontend forwards each message verbatim; the `cdc-consumer`
  follow-on is what *produces* these messages from the WAL.
- **Stateful frontend API** `app/frontend_api.py`:
  - A **connection registry** (a set of live `WebSocket` connections) with
    async-safe add/remove and a `broadcast()` that drops connections that error on
    send. It holds no authoritative fleet state — only the live connections.
  - A **WebSocket endpoint** `@app.websocket("/ws")`: accept the connection,
    register it, send a **one-shot snapshot** (built fresh from the existing
    `aggregate_fleet_state()` and `zone_entry_counts()` read seams), then keep the
    connection open to receive deltas until the client disconnects, unregistering
    on disconnect.
  - An **async Redis subscriber** started in a **FastAPI lifespan**: subscribes to
    the channel and, for every published message, calls `broadcast()` to fan it out
    to all connected clients; it is cancelled cleanly on shutdown.
  - The existing `GET /fleet/state`, `GET /zones/counts`, and `GET /anomalies`
    handlers are **unchanged**.
- **Test fixtures** `tests/integration/conftest.py`: a Redis fixture that flushes
  the channel/clients between tests and a small publish helper so a test can put a
  patch on the channel.
- **Proof-of-work test** `tests/integration/test_ws_fanout.py` (this change's
  proof — *not* the phase proof): see Design → Testing.

## Design

- **Vertical-slice scope.** The thinnest slice that proves the fan-out half of the
  phase end to end: connect a WebSocket client -> receive a one-shot snapshot ->
  publish a state patch to Redis -> the client receives that exact patch sub-second.
  The read replica, the CDC consumer tailing the logical slot, the real `POST -> WS`
  flow, and the full phase proof `tests/integration/test_realtime_ws.py` are out of
  scope (the `cdc-consumer` follow-on). This mirrors how `fault-transition-core`
  proved its handler at a seam and left the endpoint + full phase proof to its
  follow-on.
- **CDC, not dual-write — preserved.** The frontend only ever emits what it
  observes on the subscribed Redis channel; it synthesizes nothing. The ingestion
  API stays stateless and write-only and **must not** touch Redis — the event
  stream's source is CDC (arriving in the follow-on), never the writer. Proving the
  fan-out by publishing to Redis directly does not introduce a dual-write: the test
  is standing in for the not-yet-built CDC consumer, and the production publisher
  remains CDC.
- **Stateful connections, not stateful data.** Per the telemetry-architecture
  standard, the frontend API may hold WebSocket connections but **must not** hold
  authoritative state. The snapshot is derived fresh from the database read seams on
  each connect, and the only retained state is the connection registry — so any
  frontend instance can serve any client.
- **Snapshot on connect, then deltas.** The first WebSocket message is a one-shot
  snapshot of current fleet + zone state; everything after it is an individual
  patch. This gives the dashboard an immediate full render with no polling and no
  full re-render thereafter.
- **One async subscriber, many connections.** A single Redis subscription per
  frontend process (started in the lifespan) fans each message out to all
  registered connections, rather than one Redis subscription per client — the
  pub/sub fan-in is shared, the WebSocket fan-out is per-connection.
- **Scoped deviation — snapshot from the primary, not the replica.** The standard
  serves the initial snapshot from the streaming read replica. The replica arrives
  with `cdc-consumer`; until then the snapshot is read from the primary, consistent
  with the existing documented deviation in `app/frontend_api.py`. The real-time
  delta path does **not** depend on the replica, exactly as the standard requires.
- **Config from the environment.** `REDIS_URL` is read from the environment with no
  baked-in fallback, matching `get_dsn()` and the tech-stack standard's
  "configuration from the environment, no hard-coded connection strings".
- **Reuse, don't reinvent.** Extends the existing FastAPI frontend app, the
  `app/config.py` helper, and the existing read seams; introduces only the
  standard-mandated Redis broker and no new web framework or datastore (tech-stack
  standard).
- **Testing.** `tests/integration/test_ws_fanout.py` runs the frontend ASGI app
  in-process (FastAPI `TestClient` / `websocket_connect`, which runs the lifespan
  so the subscriber starts) against the real Postgres and Redis from
  `docker-compose.test.yml`:
  (a) on connect, the first WebSocket message is a one-shot snapshot reflecting the
  committed fleet and per-zone state;
  (b) after publishing a `vehicle_state_changed` patch to Redis, the connected
  client receives that exact patch over the WebSocket within a sub-second bound;
  (c) one patch of each of the three event types (`vehicle_state_changed`,
  `anomaly_detected`, `zone_count_changed`) is forwarded with its `type` preserved;
  (d) a patch is fanned out to **two** connected clients (both receive it);
  (e) with nothing published, the client receives no further message (the frontend
  emits only what it observes on the channel);
  (f) a disconnected client is removed from the registry and does not block fan-out
  to the still-connected client.
  The full phase proof-of-work command
  `docker compose -f docker-compose.test.yml run --rm api pytest tests/integration/test_realtime_ws.py`
  is landed by the `cdc-consumer` follow-on, which supplies the CDC source.

## Tasks

- [x] 1.1 Add the async Redis client dependency via `uv add redis` (redis-py asyncio); confirm `uv.lock` and `pyproject.toml` update
- [x] 1.2 Add `get_redis_url()` to `app/config.py` reading `REDIS_URL` from the environment with no hard-coded fallback (raise `ConfigError` when missing), mirroring `get_dsn()`
- [x] 1.3 Add a `redis:7-alpine` service with a healthcheck to `docker-compose.test.yml`, set `REDIS_URL=redis://redis:6379/0` on the `api` container, and add `redis` to its `depends_on` (`condition: service_healthy`)
- [x] 2.1 Define the event channel + patch envelope: a single channel constant (e.g. `fleet:events`) and the message contract (a `type` of `vehicle_state_changed` | `anomaly_detected` | `zone_count_changed` plus payload), forwarded verbatim
- [x] 2.2 Add a connection registry to `app/frontend_api.py`: an async-safe set of live `WebSocket` connections with add/remove and a `broadcast()` that drops connections that error on send
- [x] 2.3 Add the async Redis subscriber as a FastAPI lifespan task: subscribe to the channel on startup, fan each published message out via `broadcast()`, and cancel it cleanly on shutdown
- [x] 2.4 Add the `@app.websocket("/ws")` endpoint: accept, register, send the one-shot snapshot (built fresh from `aggregate_fleet_state()` + `zone_entry_counts()`), then hold the connection to stream deltas until disconnect, unregistering on disconnect
- [x] 2.5 Confirm the existing `GET /fleet/state`, `GET /zones/counts`, and `GET /anomalies` handlers are unchanged and the ingestion API still publishes nothing to Redis
- [x] 3.1 Add a Redis test fixture to `tests/integration/conftest.py` (flush between tests) and a small publish helper that puts a patch on the channel
- [x] 4.1 Proof test: on connect, the first WebSocket message is a one-shot snapshot reflecting committed fleet + per-zone state
- [x] 4.2 Proof test: a `vehicle_state_changed` patch published to Redis is received by the connected client over the WebSocket within a sub-second bound
- [x] 4.3 Proof test: one patch of each of the three event types is forwarded with its `type` preserved
- [x] 4.4 Proof test: a single published patch is fanned out to two connected clients (both receive it)
- [x] 4.5 Proof test: with nothing published, the client receives no further message (emits only what it observes on the channel)
- [x] 4.6 Proof test: a disconnected client is removed from the registry and does not block fan-out to the still-connected client
- [x] 4.7 Land the proof test at `tests/integration/test_ws_fanout.py` and confirm it passes against the real Postgres + Redis from `docker-compose.test.yml`
- [x] 5.1 Write the AI build-log report to `docs/ai-build-logs/*.md` and append one line to `docs/ai-build-logs/index.md`
