/// <reference types="vite/client" />

// Typed build-time env the runtime dashboard reads in web/src/transport.ts. Both
// are optional: unset in dev/test (same-origin defaults hold), set by the
// runtime `dashboard` compose service to point at the served frontend API.
interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_WS_URL?: string;
  // Browser-origin OTLP/HTTP traces endpoint for the @opentelemetry/sdk-trace-web
  // bootstrap (web/src/otel.ts). Set by the runtime `dashboard` compose service
  // to Alloy's host port (e.g. http://localhost:4318/v1/traces); unset in
  // dev/test so the browser tracing bootstrap is a safe no-op.
  readonly VITE_OTLP_TRACES_ENDPOINT?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
