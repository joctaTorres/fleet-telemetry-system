// Browser OpenTelemetry bootstrap for the fleet dashboard SPA.
//
// One module owns the browser tracing seam, mirroring how the Python services
// share a single `app/otel.py` bootstrap. When an OTLP traces endpoint is
// configured it registers a `WebTracerProvider` carrying
// `service.name=fleet-dashboard-web`, installs the official document-load +
// fetch + XHR instrumentations, exports spans OTLP/HTTP to Alloy, and injects
// the W3C `traceparent` header onto the cross-origin REST snapshot fetches so
// the browser trace joins the frontend-api server spans.
//
// With no endpoint configured it is a deliberate **no-op**: it installs nothing,
// exports nothing, and needs no running collector — so a plain `vite build`,
// `tsc --noEmit`, and the vitest suite are unaffected and `main.tsx` can call it
// unconditionally. The endpoint and the API origin come only from VITE_* build
// args; no host/port is hard-coded here.

import { ZoneContextManager } from "@opentelemetry/context-zone";
import { W3CTraceContextPropagator } from "@opentelemetry/core";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-http";
import { registerInstrumentations } from "@opentelemetry/instrumentation";
import { DocumentLoadInstrumentation } from "@opentelemetry/instrumentation-document-load";
import { FetchInstrumentation } from "@opentelemetry/instrumentation-fetch";
import { XMLHttpRequestInstrumentation } from "@opentelemetry/instrumentation-xml-http-request";
import { resourceFromAttributes, type Resource } from "@opentelemetry/resources";
import { BatchSpanProcessor } from "@opentelemetry/sdk-trace-base";
import { WebTracerProvider } from "@opentelemetry/sdk-trace-web";
import { ATTR_SERVICE_NAME } from "@opentelemetry/semantic-conventions";

/** The resource `service.name` the phase proof searches Tempo for; locked. */
export const SERVICE_NAME = "fleet-dashboard-web";

export interface BrowserOtelOptions {
  /**
   * Browser-origin OTLP/HTTP traces endpoint (e.g.
   * `http://localhost:4318/v1/traces`). Default: the `VITE_OTLP_TRACES_ENDPOINT`
   * build arg. Unset/empty = no-op.
   */
  otlpTracesEndpoint?: string;
  /**
   * The frontend API base URL the SPA fetches the REST snapshot from (e.g.
   * `http://localhost:8002`). Default: the `VITE_API_BASE_URL` build arg. When
   * cross-origin, its origin is added to `propagateTraceHeaderCorsUrls` so the
   * fetch instrumentation injects `traceparent` onto those requests.
   */
  apiBaseUrl?: string;
}

/** What {@link initBrowserOtel} returns when it actually installs the SDK. */
export interface BrowserOtelHandle {
  provider: WebTracerProvider;
  resource: Resource;
}

function envOtlpEndpoint(): string | undefined {
  const v = import.meta.env?.VITE_OTLP_TRACES_ENDPOINT;
  return typeof v === "string" && v.length > 0 ? v : undefined;
}

function envApiBaseUrl(): string | undefined {
  const v = import.meta.env?.VITE_API_BASE_URL;
  return typeof v === "string" && v.length > 0 ? v : undefined;
}

/**
 * The origin (scheme + host + port) of `apiBaseUrl`, as a regexp matching every
 * URL under it, or `undefined` if it is unset or unparseable. Used to scope
 * `traceparent` injection to the cross-origin frontend API only.
 */
function apiOriginMatcher(apiBaseUrl: string | undefined): RegExp | undefined {
  if (!apiBaseUrl) return undefined;
  try {
    const origin = new URL(apiBaseUrl).origin;
    const escaped = origin.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return new RegExp(`^${escaped}`);
  } catch {
    return undefined;
  }
}

/**
 * Initialize browser OpenTelemetry tracing. Safe to call unconditionally:
 * returns `null` (installing nothing) when no OTLP endpoint is configured, so it
 * is a no-op in dev/test and under a plain `vite build`.
 */
export function initBrowserOtel(
  opts: BrowserOtelOptions = {},
): BrowserOtelHandle | null {
  const endpoint = opts.otlpTracesEndpoint ?? envOtlpEndpoint();
  if (!endpoint) return null; // no collector configured → safe no-op

  const apiBaseUrl = opts.apiBaseUrl ?? envApiBaseUrl();
  const corsMatcher = apiOriginMatcher(apiBaseUrl);
  // Scope traceparent to the cross-origin frontend API; also skip the exporter's
  // own POST so the OTLP flush is never itself traced into a feedback loop.
  const propagateTraceHeaderCorsUrls = corsMatcher ? [corsMatcher] : [];
  const ignoreUrls = [new RegExp(endpoint.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))];

  const resource = resourceFromAttributes({ [ATTR_SERVICE_NAME]: SERVICE_NAME });

  const exporter = new OTLPTraceExporter({ url: endpoint });
  const provider = new WebTracerProvider({
    resource,
    spanProcessors: [new BatchSpanProcessor(exporter)],
  });

  // W3C trace-context propagation is what carries `traceparent` from the browser
  // fetch span onto the request, so frontend-api can parent its server span. The
  // ZoneContextManager propagates the active span context across the async hops
  // (await/Promise/setTimeout) between the document-load/interaction context and
  // the fetch, so fetch spans nest under that context instead of becoming
  // detached trace roots — which is what makes the browser->frontend-api trace
  // actually join up.
  provider.register({
    contextManager: new ZoneContextManager(),
    propagator: new W3CTraceContextPropagator(),
  });

  registerInstrumentations({
    tracerProvider: provider,
    instrumentations: [
      new DocumentLoadInstrumentation(),
      new FetchInstrumentation({ propagateTraceHeaderCorsUrls, ignoreUrls }),
      new XMLHttpRequestInstrumentation({
        propagateTraceHeaderCorsUrls,
        ignoreUrls,
      }),
    ],
  });

  return { provider, resource };
}
