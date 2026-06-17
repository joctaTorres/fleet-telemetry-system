# observability-stack-compose

## Why

The `ingestion-trace-backbone` phase needs a running observability backbone before
anything can be instrumented, provisioned, or visualized. `otel-bootstrap-python` added
the SDK and a reusable bootstrap module, but there is nowhere for that telemetry to go
yet: no OTLP collector, no trace store, no metric store, no Grafana.

This change owns the infrastructure half of the slice. It adds the four observability
services — **Grafana Alloy** (OTLP receiver), **Grafana Tempo** (traces), **Prometheus**
(metrics), and **Grafana** (visualization) — to the repo-root `docker-compose.yml` and
wires Alloy to fan OTLP traces into Tempo and OTLP metrics into Prometheus. It is the
`after` dependency for `grafana-provisioning` (datasources + dashboard provider) and a
co-dependency for `instrument-ingestion-api` (which points the ingestion service at
Alloy's OTLP endpoint).

## What Changes

- Add four services to the repo-root `docker-compose.yml`:
  - `alloy` — Grafana Alloy running as the OTLP collector: an `otelcol.receiver.otlp`
    listening on 4317 (gRPC) and 4318 (HTTP), wired to `otelcol.exporter.otlphttp` →
    Tempo for traces and `otelcol.exporter.prometheus` → `prometheus.remote_write` →
    Prometheus for metrics. Expose 4318 (and 12345 for the Alloy UI) on the host.
  - `tempo` — Grafana Tempo in single-binary mode with local block storage, its OTLP
    receivers enabled, HTTP API on 3200 exposed to the host.
  - `prometheus` — Prometheus with the remote-write receiver enabled and a scrape config
    for itself and Alloy's internal metrics; HTTP on 9090 exposed to the host.
  - `grafana` — Grafana with anonymous/admin access, HTTP on 3000 exposed to the host.
    No datasources or dashboards are provisioned here (that is `grafana-provisioning`).
- Add the minimal config files the services need under `docker/`:
  - `docker/alloy/config.alloy` — the River pipeline (OTLP in → Tempo + Prometheus out).
  - `docker/tempo/tempo.yaml` — single-binary Tempo (local storage + OTLP receivers).
  - `docker/prometheus/prometheus.yml` — scrape config (self + alloy).
- Add named volumes for Tempo, Prometheus, and Grafana so data survives a restart, in
  the same style as the existing `db-data` / `replica-data` / `redis-data` volumes.
- Add healthchecks so `docker compose up -d --wait` blocks until the backbone is ready.
- Use environment-overridable host port mappings (e.g. `GRAFANA_PORT`, `TEMPO_PORT`,
  `PROMETHEUS_PORT`, `ALLOY_OTLP_PORT`) mirroring the existing `*_PORT` convention.

## Design

- **Vertical-slice scope.** This change ships only the *backbone*: the four containers,
  their wiring, and their config. It deliberately does **not**:
  - provision Grafana datasources or a dashboard provider (`grafana-provisioning`),
  - instrument the ingestion API or point it at Alloy (`instrument-ingestion-api`),
  - add the "Ingestion API" dashboard (`ingestion-dashboard`).
  The slice proves the pipeline can receive OTLP and route it to Tempo + Prometheus, and
  that Grafana is up — the substrate the rest of the phase builds on.
- **Stack decisions are locked by the batch manifest.** Collector = Alloy, traces =
  Tempo, metrics = Prometheus, transport = OTLP/HTTP (port 4318) so Python services and
  the later browser SDK speak the same protocol. This change does not re-decide them.
- **Alloy as the single OTLP front door.** All services (and later the browser) send to
  Alloy; Alloy is the only component that knows about Tempo and Prometheus. Traces go out
  over OTLP/HTTP to Tempo; metrics are converted and pushed to Prometheus via remote
  write (Prometheus started with `--web.enable-remote-write-receiver`).
- **Config over the compose network, nothing hard-coded in app source.** Service-to-
  service endpoints resolve by compose DNS (`tempo:4318`, `prometheus:9090`,
  `alloy:4318`); host port mappings come from the environment with sensible defaults,
  matching the existing compose conventions. No credentials in source.
- **Runtime-only, test harness untouched.** All additions go in the repo-root
  `docker-compose.yml`; `docker-compose.test.yml` is not modified, so the pytest/vitest
  harness keeps the same shape and stays green.
- **Reuse upstream images and idiomatic config.** Use the official `grafana/alloy`,
  `grafana/tempo`, `prom/prometheus`, and `grafana/grafana` images with minimal, idiomatic
  config. Lean on the project `opentelemetry` skill for the Alloy River pipeline syntax
  and Grafana-stack wiring rather than inventing config.
- **Proof-of-work for this change** (subset of the phase proof, which also needs the
  downstream instrumentation/provisioning): from a clean `docker compose up -d --wait`,
  all four services report healthy; `http://localhost:3000/api/health`,
  Tempo's ready endpoint on `:3200`, and `http://localhost:9090/-/ready` all answer; and
  a single OTLP trace POSTed to Alloy's `:4318` endpoint becomes queryable in Tempo.

## Tasks

- [x] 2.1 Add `docker/tempo/tempo.yaml` — single-binary Tempo with local block storage and
      OTLP receivers (gRPC 4317 + HTTP 4318) enabled, HTTP API on 3200.
- [x] 2.2 Add `docker/prometheus/prometheus.yml` — scrape config for Prometheus itself and
      Alloy's internal metrics endpoint.
- [x] 2.3 Add `docker/alloy/config.alloy` — `otelcol.receiver.otlp` (4317 + 4318) routed to
      `otelcol.exporter.otlphttp` → Tempo (traces) and `otelcol.exporter.prometheus` →
      `prometheus.remote_write` → Prometheus (metrics).
- [x] 2.4 Add the `tempo` service to `docker-compose.yml`: image, mounted config, named
      volume, host port (`TEMPO_PORT` → 3200), and a healthcheck on its ready endpoint.
- [x] 2.5 Add the `prometheus` service: image, mounted config,
      `--web.enable-remote-write-receiver`, named volume, host port
      (`PROMETHEUS_PORT` → 9090), healthcheck on `/-/ready`.
- [x] 2.6 Add the `alloy` service: image, mounted `config.alloy`, host ports
      (`ALLOY_OTLP_PORT` → 4318 and the 12345 UI), `depends_on` tempo + prometheus,
      healthcheck on the Alloy ready endpoint.
- [x] 2.7 Add the `grafana` service: image, admin/anonymous access env, named volume, host
      port (`GRAFANA_PORT` → 3000), `depends_on` tempo + prometheus, healthcheck on
      `/api/health`. No datasource/dashboard provisioning in this change.
- [x] 2.8 Add `tempo-data`, `prometheus-data`, and `grafana-data` named volumes; keep all
      host port mappings environment-overridable.
- [x] 2.9 Bring the stack up (`docker compose up -d --wait`), send one OTLP trace to Alloy's
      `:4318` endpoint, and confirm it is queryable in Tempo and that Grafana/Prometheus
      ready endpoints answer.
- [x] 2.10 Confirm `docker-compose.test.yml` is unchanged and the existing pytest/vitest
      suites still pass.
