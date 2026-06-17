Feature: The ingestion and frontend APIs and the dashboard are servable
  As an operator running the stack for real use
  I want the FastAPI apps served by an ASGI server and the built React dashboard
  served over HTTP, with the dashboard wired to the runtime API and WS endpoints
  So that the system is usable through a browser, not just exercised by tests

  Background:
    Given the backend targets Python 3.14 with dependencies managed by uv
    And the HTTP layer is FastAPI and the datastore is PostgreSQL
    And the dashboard is a Vite + React + TypeScript app

  Scenario: uvicorn is a backend dependency added through uv
    Given the project previously had no ASGI server dependency
    When the backend dependencies are resolved
    Then uvicorn is present as a project dependency added via uv (recorded in
      pyproject.toml and uv.lock), with no pip or hand-edited requirements.txt as
      the source of truth

  Scenario: The ingestion API is served on a host port
    Given the runtime stack is up
    When the ingestion service starts
    Then it serves `app.ingestion_api:app` with uvicorn on a published host port
    And POST /telemetry returns 201 for a valid telemetry event
    And POST /vehicles/{vehicle_id}/status applies a status transition

  Scenario: The frontend API is served on a host port
    Given the runtime stack is up
    When the frontend service starts
    Then it serves `app.frontend_api:app` with uvicorn on a published host port
    And GET /fleet/state, GET /vehicles, GET /vehicles/anomalies/latest,
      GET /zones/counts, and GET /anomalies all answer
    And the WS /ws endpoint accepts a connection and sends the connect snapshot

  Scenario: The built dashboard is served and reachable on a host port
    Given the React + TypeScript dashboard has been built with Vite
    When the dashboard service starts
    Then the built static assets (or a vite preview server) are served on a
      published host port
    And opening that port in a browser loads the live dashboard shell

  Scenario: The served dashboard talks to the runtime API and WS, not localhost
    Given the dashboard reads its REST base URL and WS URL from the transport
      layer in web/src/transport.ts, which currently defaults to same-origin
    When the dashboard is built and served in the runtime stack
    Then transport.ts is wired so the REST base URL and WS URL resolve to the
      runtime frontend API service via import.meta.env.VITE_* values and/or a
      Vite proxy, instead of defaulting only to same-origin
    And the dashboard's GET /vehicles, GET /vehicles/anomalies/latest,
      GET /zones/counts and /ws calls reach the runtime frontend API
    And no API host or port is hard-coded in source — the wiring is configured
      from the environment at build/serve time
