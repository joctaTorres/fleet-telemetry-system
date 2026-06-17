# propagate-trace-context-redis

## Why

The `cdc-pubsub-redis-flow` phase wants the asynchronous critical path to be ONE
connected distributed trace: a single k6 telemetry write should yield one Tempo
trace spanning ingestion -> cdc-consumer -> redis publish -> frontend subscribe
-> WS broadcast. That outcome takes three ordered changes. The first,
`instrument-cdc-consumer`, made the CDC consumer observable — it now emits a
`cdc.decode` span and a per-event `cdc.publish` span and CDC event metrics. The
frontend already emits `service.name=frontend-api` spans for its reads and the
`/ws` lifecycle. But the two halves are **two separate traces**: the trace breaks
at the Redis pub/sub hop, because nothing carries the trace context across the
`fleet:events` channel.

This change owns closing exactly that gap. It threads the W3C trace context
through the Redis event envelope: the cdc-consumer **injects** the active context
at its publish seam, and the frontend **extracts** it on subscribe and parents its
subscribe + WebSocket broadcast spans on the remote context. The result is the
verifiable core of the phase proof — a single Tempo trace whose spans include both
`service.name=cdc-consumer` and `service.name=frontend-api`. The follow-on
`cdc-pubsub-redis-dashboards` visualizes this flow; it is not part of this change.

## What Changes

- Extend the `fleet:events` envelope contract in `app/events.py` with a single
  reserved, out-of-band carrier for the W3C trace context (a dedicated top-level
  key / nested object, distinct from `type` and `payload`), plus shared
  inject/extract helpers built on the standard `TraceContextTextMapPropagator`.
  Defining the carrier and the helpers in the one module both sides already import
  keeps the publisher and subscriber from duplicating propagator wiring and keeps
  the contract in a single place.
- Inject in the cdc-consumer **inside the existing `cdc.publish` span**
  (`_emit` in `app/cdc_consumer.py`): populate the envelope carrier from the active
  context before `json.dumps` + `self._redis.publish(...)`, so the propagated
  `traceparent` reflects the publish span. No new span, no change to the
  once-per-committed-change publish guarantee, and injection is defensive — it
  never gates the publish.
- Extract in the frontend `_redis_subscriber` (`app/frontend_api.py`): parse each
  message, pull the remote context out of the carrier, and start a subscribe span
  whose parent is that extracted context, then make the WebSocket broadcast a child
  span of it. This joins the frontend spans into the cdc-consumer trace via the
  Redis traceparent.
- Preserve the browser WS message contract: the trace-context carrier is stripped
  (or simply never part of `payload`) so connected clients still receive the same
  `{type, payload}` shape, forwarded verbatim, as before.
- Keep both sides safe-by-default and backward compatible: with no OTLP endpoint
  the propagator is a harmless no-op; an envelope with no carrier extracts to an
  empty context and is still fanned out; inject/extract failures never block or
  break the cdc pump loop or the frontend fan-out.
- Add unit tests for the round-trip (inject writes a valid `traceparent`; extract
  re-parents onto the injected trace; a carrier-less envelope still broadcasts)
  with no running collector, keeping the existing suites green.

## Design

- **Vertical-slice scope.** The thinnest change that proves the phase goal end to
  end: thread the traceparent across the one Redis hop and link the spans. It does
  **not** author any dashboard JSON (owned by `cdc-pubsub-redis-dashboards`), does
  **not** touch Grafana provisioning (owned by `grafana-provisioning`), and does
  **not** add new metrics — it only connects existing spans into one trace.
- **Contract lives in `app.events`.** The carrier key and the inject/extract
  helpers are defined once, in the module both services already import for
  `EVENT_CHANNEL` and the event types. Neither service re-wires the propagator;
  they call the shared helpers. This mirrors how the envelope `{type, payload}`
  contract is already centralized there.
- **Inject at the publish seam, parent on the publish span.** `instrument-cdc-consumer`
  deliberately left `cdc.publish` as the single injection seam. Injecting while
  that span is current makes the published `traceparent` point at the publish span,
  so the frontend subscribe span hangs off the publish span — the most meaningful
  parent for the Redis hop.
- **Out-of-band carrier, payload untouched.** Trace context is transport metadata,
  not application data, so it goes in a reserved envelope field and is removed
  before the message reaches browser clients. The event content forwarded over
  WebSockets keeps exactly its current shape — no client-visible contract change.
- **Standard W3C propagator only.** Use `opentelemetry`'s
  `TraceContextTextMapPropagator` (W3C `traceparent`/`tracestate`) against a plain
  dict carrier — no custom header format — so the join is interoperable and the
  no-op path is the SDK's own.
- **Safe-by-default and non-perturbing.** With `OTEL_EXPORTER_OTLP_ENDPOINT` unset,
  inject/extract are no-ops and both processes behave exactly as today. Under load,
  a propagation error must never block or break the cdc pump loop or the frontend
  fan-out, and delivery stays exactly-once on publish and to-every-client on
  broadcast.
- **Best-effort upstream link.** Joining the upstream ingestion HTTP write to this
  trace across Postgres logical replication is explicitly out of scope; this slice
  only guarantees the cdc-consumer <-> frontend-api join via the Redis traceparent,
  matching the refined phase proof-of-work.
- **Proof-of-work for this change** (the verifiable core of the phase proof,
  excluding the dashboards owned by the follow-on change): from a clean
  `docker compose up -d --wait` with k6 driving telemetry writes, Tempo returns
  >=1 trace whose spans include BOTH `resource.service.name=cdc-consumer` and
  `resource.service.name=frontend-api`, joined via the W3C traceparent carried in
  the `fleet:events` envelope.

## Tasks

- [x] 1. Extend the `fleet:events` envelope contract in `app/events.py`: define a
      single reserved, out-of-band trace-context carrier key (distinct from `type`
      and `payload`) and shared `inject_trace_context` / `extract_trace_context`
      helpers built on `TraceContextTextMapPropagator`, so both services share one
      propagator wiring.
- [x] 2. In `app/cdc_consumer.py` `_emit`, inject the active context into the
      envelope carrier **inside the existing `cdc.publish` span**, before
      `json.dumps` + `self._redis.publish(...)`, so the propagated `traceparent`
      reflects the publish span; keep injection defensive (never gates the publish)
      and the once-per-committed-change guarantee intact.
- [x] 3. In `app/frontend_api.py` `_redis_subscriber`, extract the remote context
      from each message's carrier and start a subscribe span parented on it, making
      the WebSocket broadcast a child span carrying `service.name=frontend-api`, so
      the frontend spans join the cdc-consumer trace.
- [x] 4. Strip the trace-context carrier from the message forwarded to WebSocket
      clients so they still receive the unchanged `{type, payload}` shape, verbatim.
- [x] 5. Ensure backward compatibility and safe defaults: an envelope with no
      carrier extracts to an empty context and is still broadcast; with
      `OTEL_EXPORTER_OTLP_ENDPOINT` unset inject/extract are no-ops and both
      processes behave exactly as today; inject/extract failures never block or
      break the cdc pump loop or the frontend fan-out.
- [x] 6. Add unit tests (no running collector): inject writes a valid `traceparent`
      into the carrier from the active publish span; the subscriber extracts that
      context so its span shares the injected trace id (publish span is the parent);
      a carrier-less envelope still extracts cleanly and is still broadcast; keep the
      existing pytest suites green.
- [x] 7. Bring the stack up with k6 driving telemetry writes and confirm Tempo
      returns >=1 trace whose spans include BOTH
      `resource.service.name=cdc-consumer` and `resource.service.name=frontend-api`
      joined via the Redis traceparent.
- [x] 8. Confirm scope boundaries: no dashboard JSON, no Grafana provisioning, and
      no new metrics are added here; `docker-compose.test.yml` and the conftest
      background-thread consumer remain untouched and green.
