// Browser OTel bootstrap contract tests. These exercise web/src/otel.ts without
// a running collector: the bootstrap must be a safe no-op when no OTLP endpoint
// is configured, and when one is given it must register a provider whose
// resource carries the exact service.name the phase proof searches Tempo for.
import { ZoneContextManager } from "@opentelemetry/context-zone";
import { context } from "@opentelemetry/api";
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

  it("registers a ZoneContextManager so browser context propagates across async hops", () => {
    // Without a context manager, fetch spans become detached trace roots instead
    // of children of the document-load/interaction context, breaking the joined
    // browser->frontend-api trace. register({ contextManager: ... }) installs the
    // ZoneContextManager as the global context manager, so assert that the active
    // global context manager is exactly a ZoneContextManager instance.
    const handle = initBrowserOtel({
      otlpTracesEndpoint: "http://localhost:4318/v1/traces",
    });
    expect(handle).not.toBeNull();

    // _getContextManager() is the internal accessor the OTel context API exposes
    // on the global ContextAPI singleton; it returns the manager set by register.
    const activeManager = (
      context as unknown as { _getContextManager: () => unknown }
    )._getContextManager();
    expect(activeManager).toBeInstanceOf(ZoneContextManager);
  });
});
