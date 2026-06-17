// Browser OTel bootstrap contract tests. These exercise web/src/otel.ts without
// a running collector: the bootstrap must be a safe no-op when no OTLP endpoint
// is configured, and when one is given it must register a provider whose
// resource carries the exact service.name the phase proof searches Tempo for.
import { describe, expect, it } from "vitest";
import { initBrowserOtel, SERVICE_NAME } from "../otel";

describe("initBrowserOtel", () => {
  it("is a no-op when no OTLP endpoint is configured", () => {
    // No endpoint option and VITE_OTLP_TRACES_ENDPOINT is unset under vitest, so
    // nothing is installed and no collector is required.
    expect(initBrowserOtel()).toBeNull();
    expect(initBrowserOtel({ otlpTracesEndpoint: "" })).toBeNull();
  });

  it("registers a provider whose resource carries service.name=fleet-dashboard-web", () => {
    const handle = initBrowserOtel({
      otlpTracesEndpoint: "http://localhost:4318/v1/traces",
      apiBaseUrl: "http://localhost:8002",
    });

    expect(handle).not.toBeNull();
    expect(handle!.provider).toBeDefined();
    expect(handle!.resource.attributes["service.name"]).toBe(
      "fleet-dashboard-web",
    );
    expect(SERVICE_NAME).toBe("fleet-dashboard-web");
  });
});
