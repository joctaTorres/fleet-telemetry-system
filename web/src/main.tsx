import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { initBrowserOtel } from "./otel";
import "./index.css";

// Start browser OpenTelemetry before the first render so the document-load span
// and the SPA's one-time REST snapshot fetches are captured. A safe no-op when
// no OTLP endpoint is configured (dev/test), so this stays unconditional.
initBrowserOtel();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
