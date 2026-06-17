/// <reference types="vitest/config" />
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// Vite for the dev/build of the dashboard; Vitest config drives `npm run test:ui`
// (the phase proof command) headlessly under jsdom with a global RTL setup file.
//
// The dev proxy targets resolve from the environment (VITE_DEV_PROXY_HTTP /
// VITE_DEV_PROXY_WS) so `npm run dev` can point at any frontend API, defaulting
// to the local frontend on :8000 when unset — no host is hard-coded. The
// runtime-served dashboard does not use this proxy; it reaches the frontend API
// directly via the baked-in VITE_API_BASE_URL / VITE_WS_URL (see transport.ts).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const httpTarget = env.VITE_DEV_PROXY_HTTP || "http://localhost:8000";
  const wsTarget = env.VITE_DEV_PROXY_WS || "ws://localhost:8000";
  return {
    plugins: [react()],
    server: {
      // Dev convenience: proxy REST + WS to the frontend API so `fetch('/vehicles')`
      // and `new WebSocket('/ws')` resolve without CORS in `npm run dev`.
      proxy: {
        // `/vehicles` also covers the `/vehicles/anomalies/latest` anomaly snapshot.
        "/vehicles": httpTarget,
        "/zones": httpTarget,
        "/ws": { target: wsTarget, ws: true },
      },
    },
    test: {
      environment: "jsdom",
      globals: true,
      setupFiles: "./src/test-setup.ts",
      css: false,
    },
  };
});
