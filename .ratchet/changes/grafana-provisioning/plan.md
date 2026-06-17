# grafana-provisioning

## Why

`observability-stack-compose` brought up Grafana but deliberately provisioned
nothing — no datasources, no dashboard provider. Without provisioned datasources
Grafana has nothing to query Tempo or Prometheus with, and without a file-based
dashboard provider the downstream "Ingestion API" dashboard would have to be
imported by hand, breaking the "clean `docker compose up`, everything preloaded"
end goal.

This change owns the **Grafana-side wiring** of the slice: it provisions the
**Tempo** (traces) and **Prometheus** (metrics) datasources and registers a
**file-based dashboard provider** that auto-loads any dashboard JSON dropped into
a mounted folder. It is the `after` dependency for `ingestion-dashboard`, which
will simply drop a dashboard JSON file into the folder this change wires up.

## What Changes

- Add Grafana provisioning files under `docker/grafana/provisioning/`:
  - `datasources/datasources.yaml` — declares two datasources:
    - **Tempo** (`type: tempo`, fixed `uid: tempo`), URL `http://tempo:3200`
      over the compose network.
    - **Prometheus** (`type: prometheus`, fixed `uid: prometheus`), URL
      `http://prometheus:9090`, marked `isDefault: true`.
  - `dashboards/dashboards.yaml` — a file-based dashboard provider pointing at a
    mounted dashboards directory (e.g. `/var/lib/grafana/dashboards`) with
    `foldersFromFilesStructure` / update interval set idiomatically, so any JSON
    placed there is auto-imported.
- Add `docker/grafana/dashboards/` as the provider's target directory with a
  `.gitkeep` so the folder exists and mounts cleanly while still empty (the
  actual "Ingestion API" dashboard JSON lands here in `ingestion-dashboard`).
- Wire the `grafana` service in the repo-root `docker-compose.yml` to mount the
  provisioning tree and the dashboards directory read-only:
  - `./docker/grafana/provisioning:/etc/grafana/provisioning:ro`
  - `./docker/grafana/dashboards:/var/lib/grafana/dashboards:ro`
- Keep host port mappings and admin env exactly as `observability-stack-compose`
  left them; this change adds provisioning only, not new ports or images.

## Design

- **Vertical-slice scope.** This change ships only the Grafana provisioning
  *substrate*: two datasources and one dashboard provider. It deliberately does
  **not**:
  - author the "Ingestion API" dashboard JSON (`ingestion-dashboard`),
  - instrument any service or emit traces/metrics (`instrument-ingestion-api`).
  Its proof is that the datasources provision and health-check green and the
  provider is registered, so a later JSON drop is the only step left.
- **Stable, well-known uids.** The Tempo and Prometheus datasources use fixed
  uids (`tempo`, `prometheus`) so downstream dashboard JSON can bind panels to
  them by uid rather than by Grafana's auto-generated ids — making dashboards
  portable and reproducible across `docker compose down/up`.
- **File provisioning over the network, no secrets.** Datasource URLs resolve by
  compose DNS (`tempo:3200`, `prometheus:9090`); no credentials are written into
  the provisioning files (Grafana admin creds stay in the service env from the
  prior change). Provisioning files mount read-only.
- **Default datasource = Prometheus.** Metrics panels are the most common, so
  Prometheus is `isDefault`; Tempo is selected explicitly by trace panels and by
  the Explore trace view.
- **Runtime-only, test harness untouched.** All additions go in the repo-root
  `docker-compose.yml` and `docker/grafana/`; `docker-compose.test.yml` is not
  modified, so the pytest/vitest harness stays green.
- **Reuse idiomatic Grafana provisioning.** Use Grafana's standard
  `apiVersion: 1` provisioning schema for both datasources and the dashboard
  provider; lean on the project `opentelemetry` skill for the Grafana-stack
  datasource conventions rather than inventing config.
- **Proof-of-work for this change** (a subset of the phase proof, which also
  needs the downstream instrumentation + dashboard): from a clean
  `docker compose up -d --wait`, Grafana is healthy; `GET /api/datasources`
  lists a `tempo` and a `prometheus` datasource with their fixed uids;
  `GET /api/datasources/uid/tempo/health` and
  `GET /api/datasources/uid/prometheus/health` both succeed; and Grafana's
  startup logs show the dashboard provider loaded with no provisioning errors.

## Tasks

- [x] 3.1 Add `docker/grafana/provisioning/datasources/datasources.yaml` declaring
      the Tempo (`uid: tempo`, `http://tempo:3200`) and Prometheus
      (`uid: prometheus`, `http://prometheus:9090`, `isDefault: true`) datasources
      with `apiVersion: 1`.
- [x] 3.2 Add `docker/grafana/provisioning/dashboards/dashboards.yaml` — a
      file-based dashboard provider pointing at `/var/lib/grafana/dashboards`,
      configured to auto-load JSON dropped into that folder.
- [x] 3.3 Add `docker/grafana/dashboards/.gitkeep` so the provider's target
      directory exists and mounts while still empty (downstream changes fill it).
- [x] 3.4 Mount the provisioning tree and dashboards directory read-only into the
      `grafana` service in `docker-compose.yml`
      (`provisioning:/etc/grafana/provisioning:ro` and
      `dashboards:/var/lib/grafana/dashboards:ro`).
- [x] 3.5 Bring the stack up (`docker compose up -d --wait`) and confirm via
      `GET /api/datasources` that both datasources exist with their fixed uids.
- [x] 3.6 Run datasource health checks
      (`GET /api/datasources/uid/tempo/health` and `.../uid/prometheus/health`)
      and confirm both succeed.
- [x] 3.7 Confirm Grafana's startup logs show the dashboard provider loaded and
      report no datasource/dashboard provisioning errors.
- [x] 3.8 Confirm `docker-compose.test.yml` is unchanged and the existing
      pytest/vitest suites still pass.
