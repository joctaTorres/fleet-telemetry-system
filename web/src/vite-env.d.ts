/// <reference types="vite/client" />

// Typed build-time env the runtime dashboard reads in web/src/transport.ts. Both
// are optional: unset in dev/test (same-origin defaults hold), set by the
// runtime `dashboard` compose service to point at the served frontend API.
interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  readonly VITE_WS_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
