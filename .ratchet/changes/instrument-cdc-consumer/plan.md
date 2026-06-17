# instrument-cdc-consumer

## Why

The `cdc-pubsub-redis-flow` phase wants the asynchronous critical path to become
one connected distributed trace: a single k6 telemetry write should yield ONE
Tempo trace spanning ingestion -> cdc-consumer -> redis publish -> frontend
subscribe -> WS broadcast, with "CDC Consumer", "Pub/Sub", and "Redis Fan-out"
dashboards populating live. That outcome takes three ordered changes. This is the
first: it makes the CDC consumer *itself* observable.

The two Python services on the synchronous side (ingestion, frontend) are already
instrumented through the shared `app.otel` bootstrap, and the Alloy -> Tempo +
Prometheus backbone is up. But the `cdc` service — the sole producer on the
`fleet:events` channel — emits nothing: it never calls the bootstrap, and the
`cdc` service in compose has no OTLP endpoint. So today the asynchronous middle of
the flow is a black box.

This change owns making that middle visible: wire the shared bootstrap into
`app/cdc_consumer.py` as `service.name=cdc-consumer`, wrap the decode and the
per-event Redis publish in spans, emit event-throughput / decode-lag /
publish-count metrics, and point the runtime `cdc` service at Alloy. Its output —
`cdc-consumer` spans in Tempo and CDC event metrics in Prometheus — is what the
follow-on `propagate-trace-context-redis` connects to the frontend (via the W3C
traceparent in the event envelope) and what `cdc-pubsub-redis-dashboards`
visualizes.

## What Changes

- Wire OTel into `app/cdc_consumer.py` using only the shared `app.otel`
  bootstrap. Because the consumer is a long-lived loop, not a FastAPI app, there
  is **no** `instrument_fastapi_app`: instead call `configure_otel("cdc-consumer")`
  once at process startup (in `main` / `run_forever`) and obtain a module-level
  tracer and meter via `trace.get_tracer` / `metrics.get_meter`. All are safe
  no-ops when `OTEL_EXPORTER_OTLP_ENDPOINT` is unset, so import, the conftest
  background-thread consumer, and pytest are unaffected.
- Add **decode and publish spans** on the decode/translate/publish core
  (`_decode_pgoutput` / `_emit`): a span covering the decode of a watched change
  and a per-event publish span wrapping the `_redis.publish(EVENT_CHANNEL, ...)`
  call, both carrying the event type / watched table as an attribute and resource
  `service.name=cdc-consumer`. The per-event publish span is deliberately the
  seam the next change injects the W3C traceparent into — **no injection happens
  here**.
- Emit **custom event metrics** for the rate / lag / publish panels: an
  events-published counter keyed by event type (throughput + publish count) and a
  decode-lag measure for per-change processing latency, recorded off the global
  meter installed by `configure_otel` and exported over OTLP/HTTP to Alloy (which
  remote-writes to Prometheus). Only watched-table changes are counted; a
  non-watched change records nothing and emits no publish span.
- Point the runtime `cdc` service at Alloy in `docker-compose.yml`: set
  `OTEL_EXPORTER_OTLP_ENDPOINT` to Alloy's OTLP/HTTP endpoint
  (`http://alloy:4318`) over the compose network, env-overridable in the existing
  style, and ensure the service starts after the backbone is available.
- Add unit tests asserting the consumer installs OTel through the bootstrap and
  records its event counter / decode-lag instrument and a publish span on a
  watched change (and nothing on a non-watched change), without requiring a
  running collector. Keep the no-op path green.

## Design

- **Vertical-slice scope.** This change instruments exactly one process for the
  asynchronous flow. It does **not** inject the Redis traceparent or link the
  frontend subscribe / WS broadcast spans into one connected trace (that is
  `propagate-trace-context-redis`), does **not** author the "CDC Consumer",
  "Pub/Sub", or "Redis Fan-out" dashboard JSON (that is
  `cdc-pubsub-redis-dashboards`), and does **not** touch Grafana provisioning. It
  proves only that decoded/published changes produce queryable
  `service.name=cdc-consumer` spans in Tempo and CDC event metrics in Prometheus.
- **Reuse the bootstrap, don't re-wire the SDK.** All SDK setup lives in
  `app.otel`; this change calls `configure_otel("cdc-consumer")` and pulls a
  tracer/meter off the globals. No TracerProvider / MeterProvider / exporter
  wiring is duplicated here. The process startup is the right place to call it —
  the decode/translate/publish core stays free of bootstrap concerns.
- **Spans are manual, metrics explicit and stable.** There is no ASGI
  auto-instrumentation for a replication loop, so the decode and publish spans
  are created explicitly around the existing decode/emit code. The metric names
  and label keys (event type) are chosen explicitly so the downstream dashboards
  can bind panels deterministically, mirroring how `instrument-ingestion-api`
  treated its request metrics.
- **Publish span = the propagation seam.** Wrapping each `_redis.publish` in its
  own span gives the next change a single, obvious place to inject the active
  span's W3C `traceparent` into the JSON envelope — without this change having to
  know about or change the envelope contract in `app.events`.
- **Config from the environment, nothing hard-coded.** The OTLP endpoint is read
  from `OTEL_EXPORTER_OTLP_ENDPOINT`, set only by compose, mirroring the existing
  `DATABASE_URL` / `REDIS_URL` convention. No endpoint or credentials in app
  source.
- **Safe-by-default, must not perturb the consumer.** With no endpoint set the
  process behaves exactly as today: no exporter, no collector, nothing raised —
  keeping `docker-compose.test.yml`, the conftest background-thread consumer, and
  pytest green. Critically, instrumentation must never block or break the pump
  loop, change the once-per-committed-change publish guarantee, or interfere with
  the standby status-update feedback that advances the slot's confirmed-flush, or
  with the unbounded supervised-restart behavior.
- **Runtime-only compose change.** Only the runtime `docker-compose.yml` `cdc`
  service gains the OTLP endpoint and backbone ordering;
  `docker-compose.test.yml` is untouched.
- **Proof-of-work for this change** (a subset of the phase proof, which also
  needs `propagate-trace-context-redis` for the connected trace and
  `cdc-pubsub-redis-dashboards` for the dashboards): from a clean
  `docker compose up -d --wait` with k6 driving telemetry writes, Tempo returns
  >=1 trace for `service.name=cdc-consumer` containing a decode and a publish
  span, and Prometheus exposes the CDC event-throughput / decode-lag /
  publish-count series.

## Tasks

- [x] 1. Call `configure_otel("cdc-consumer")` once at process startup
      (`main` / `run_forever` in `app/cdc_consumer.py`), reading the endpoint from
      the environment only; obtain a module-level tracer and meter from the
      shared bootstrap (no SDK re-wiring).
- [x] 2. Wrap the decode of a watched change in a span (around
      `_decode_pgoutput` / `_emit`) carrying the event type / watched table and
      resource `service.name=cdc-consumer`, exported over OTLP/HTTP to Alloy.
- [x] 3. Wrap each `_redis.publish(EVENT_CHANNEL, ...)` in a per-event publish
      span that is a child of / shares the trace with the decode span, carrying
      the event type — establishing the seam for the later traceparent injection
      without injecting anything here.
- [x] 4. Add custom metrics off the global meter: an events-published counter
      keyed by event type (throughput + publish count) and a decode-lag measure
      for per-change processing latency; record only for watched-table changes.
- [x] 5. Set `OTEL_EXPORTER_OTLP_ENDPOINT` to Alloy's OTLP/HTTP endpoint
      (`http://alloy:4318`) on the `cdc` service in `docker-compose.yml`,
      env-overridable, and order it after the backbone is available.
- [x] 6. Confirm the no-endpoint path and non-perturbation: with the env unset the
      process starts and decode/translate/publish runs normally with no exporter;
      a non-watched change records no metric and no publish span; the
      slot-advance feedback and supervised-restart behavior are unchanged.
- [x] 7. Add unit tests: the consumer installs OTel through the bootstrap and
      records its event counter / decode-lag instrument and a publish span on a
      watched change (and nothing on a non-watched change), with no running
      collector; keep the no-op path green and the conftest background-thread
      consumer working.
- [x] 8. Bring the stack up with k6 driving telemetry writes; confirm Tempo
      returns >=1 `service.name=cdc-consumer` trace with a decode and a publish
      span and Prometheus exposes the event-throughput / decode-lag /
      publish-count series.
- [x] 9. Confirm `docker-compose.test.yml` is unchanged and the existing pytest
      suite still passes with no running collector.
