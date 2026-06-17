/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Vite for the dev/build of the dashboard; Vitest config drives `npm run test:ui`
// (the phase proof command) headlessly under jsdom with a global RTL setup file.
export default defineConfig({
  plugins: [react()],
  server: {
    // Dev convenience: proxy REST + WS to the frontend API so `fetch('/vehicles')`
    // and `new WebSocket('/ws')` resolve without CORS in `npm run dev`.
    proxy: {
      // `/vehicles` also covers the `/vehicles/anomalies/latest` anomaly snapshot.
      "/vehicles": "http://localhost:8000",
      "/zones": "http://localhost:8000",
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test-setup.ts",
    css: false,
  },
});
