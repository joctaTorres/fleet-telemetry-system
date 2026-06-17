Feature: The served dashboard can reach the frontend API cross-origin over REST and WS
  As a browser loading the dashboard from its own host port
  I want the frontend API to permit cross-origin REST requests and to accept
  WebSocket upgrades
  So that the dashboard's snapshot fetch and live patch stream are not blocked
  when the dashboard origin (`:8080`) differs from the API origin (`:8002`)

  Background:
    Given the dashboard is served on its own host port, a different origin from the
      frontend API
    And the frontend API is served by `uvicorn app.frontend_api:app`

  Scenario: REST responses carry a cross-origin allow header
    Given the dashboard origin differs from the frontend API origin
    When the browser issues a cross-origin REST request such as
      `GET /vehicles` with an `Origin: http://localhost:8080` header
    Then the frontend API answers with an `access-control-allow-origin` header so
      the browser does not block the response
    And the allowed origins are configurable from the environment (defaulting to a
      permissive value suitable for the local runtime), via FastAPI's
      `CORSMiddleware`

  Scenario: The WebSocket upgrade succeeds with a 101 handshake
    Given the frontend API process has a WebSocket protocol implementation
      available (the `websockets` dependency is installed and locked)
    When a client opens `ws://localhost:8002/ws`
    Then the server completes the handshake with HTTP 101 Switching Protocols
      rather than returning 404
    And the live patch stream can flow over that socket
