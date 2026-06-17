# Capstone Acceptance Verdict (Independent LLM-Judge)

## Result: PASS

The phase end goal holds. From a single clean `docker compose up`, all eight required Grafana dashboards are auto-provisioned and populated with live k6-driven data, the Tempo service-graph covers all critical flows, and all critical flows are traceable end to end with zero manual setup.

---

## What I checked

1. Read the text snapshot (`evidence/snapshot.txt`).
2. Viewed all 9 screenshots: fleet-overview, ingestion-api, frontend-api, cdc-consumer, pubsub, redis-fan-out, primary-replica-streaming, frontend-web-browser, spa-home.
3. Independently re-queried the still-running live stack:
   - Grafana dashboards: `GET /api/search?type=dash-db`
   - Service graph: Prometheus `traces_service_graph_request_total`
   - Tempo services: `/api/search/tag/service.name/values`
   - Fetched both named connected traces from Tempo (`/api/traces/<id>`) and inspected their service set and span chain.

---

## Confirmed facts

### 8/8 dashboards provisioned (live query)
CDC Consumer, Fleet Observability Overview, Frontend API & WebSockets, Frontend Web (Browser), Ingestion API, Primary/Replica Streaming, Pub/Sub, Redis Fan-out. TOTAL = 8.

### Dashboards show live data (screenshots)
- **Fleet Observability Overview** — 50.2 req/s ingestion throughput, 61.6 c/s CDC publish, 61.3 c/s Redis fan-out, 656 ms replication lag; populated service-graph node map; end-to-end pipeline throughput, p95 latency, and recent-traces panels all populated.
- **Ingestion API** — request-rate-by-route, p50/p95 latency, and recent-traces all populated; the non-2xx error panel rises only late/small on a healthy system.
- **Frontend API & WebSockets** — REST request rate, p50/p95 latency, WebSocket broadcast rate, recent traces all populated. "REST errors (non-2xx)" = No data — the single legitimately-empty non-2xx error panel on a healthy system (explicitly allowed). Active WebSocket connections = 0 at capture instant (k6 REST-driven load), not a failure.
- **CDC Consumer** — 61.39 ops/s total publish rate, throughput-by-type, decode p50/p95 lag, recent cdc traces all populated.
- **Pub/Sub** — publish rate (fleet-event) and decode-tag panels populated, recent traces present.
- **Redis Fan-out** — 61.3 ops/s broadcasts/sec, fan-out delivery rate populated; dropped-clients = 0 (healthy).
- **Primary/Replica Streaming** — 32.8 KiB byte lag, 651 ms seconds lag, both over-time series populated.
- **Frontend Web (Browser)** — browser traces, document.load, REST snapshot fetches, and browser->frontend joined-trace panels all populated.
- **spa-home** — live SPA rendering "Connected" with ~50 vehicles streaming and zone-entry counts.

### Service-graph edges (live Prometheus) — all critical flows present
- `ingestion-api -> db` = 27372 ✓
- `cdc-consumer -> frontend-api` (Redis pub/sub hop) = 33954 ✓
- `frontend-api -> replica` = 191 ✓
- `fleet-dashboard-web -> frontend-api` (browser) = 3 ✓
- (plus entry nodes `user -> ingestion-api` = 27516, `user -> frontend-api` = 209)

All four required critical flows are covered. Counters advanced between the snapshot (e.g. ingestion->db 25186) and my live re-query (27372), confirming the stack is genuinely live with k6 still driving load.

### Tempo services (live)
`['cdc-consumer', 'fleet-dashboard-web', 'frontend-api', 'ingestion-api']` — ingestion-api correctly present as its own node (see by-design note).

### Connected traces (fetched and inspected end to end)
- **Browser -> frontend-api joined trace** `60246640df958aec64ea9afa362cedf3` — services `{fleet-dashboard-web, frontend-api}` in one trace; spans `fleet-dashboard-web HTTP GET` -> `frontend-api GET /vehicles` -> `replica.read vehicle_current_state`. ✓
- **CDC -> Redis -> frontend connected trace** `1365d31f5ac00179528d554e6f74999` — services `{cdc-consumer, frontend-api}` in one trace; span chain `cdc.decode -> cdc.publish -> redis.subscribe -> ws.broadcast` exactly as required. ✓

---

## Caveats (none fatal)
- The only empty timeseries is "REST errors (non-2xx)" on the Frontend API dashboard — explicitly permitted (healthy system, no errors).
- "Active WebSocket connections" reads 0 because the live load is k6 REST-driven; broadcast/fan-out rates (~61 ops/s) confirm the WS pipeline is active. Not a gap against the end goal.

## By-design note (not a gap)
Trace context deliberately does NOT propagate across the Postgres logical-replication WAL boundary from `ingestion-api` to `cdc-consumer`. This is documented out of scope in the upstream "propagate-trace-context-redis" change. Consequently `ingestion-api` correctly appears as its own trace and as its own service-graph node (`user -> ingestion-api`, `ingestion-api -> db`), which is the expected and correct behavior.
