import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The Phase 7A FastAPI backend (api/app.py) is run separately with:
//   uvicorn api.app:app --reload --port 8000
// This dev server proxies /ws and /api so the frontend can be addressed as
// a single origin during development without hardcoding a backend host in
// application code. See src/lib/config.js for how the browser resolves the
// WebSocket URL in both dev (proxied) and preview/build (direct) modes.
const BACKEND_ORIGIN = process.env.VITE_BACKEND_ORIGIN || "http://localhost:8000";
const BACKEND_WS_ORIGIN = BACKEND_ORIGIN.replace(/^http/, "ws");

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Matches the routes api/app.py actually exposes (see docs/UI_SPEC.md
      // section 3 and api/app.py's module docstring) — there is no /api
      // prefix in Phase 7A, so we proxy each real route individually.
      "/ws": {
        target: BACKEND_WS_ORIGIN,
        ws: true,
      },
      "/health": {
        target: BACKEND_ORIGIN,
        changeOrigin: true,
      },
      "/demo": {
        target: BACKEND_ORIGIN,
        changeOrigin: true,
      },
    },
  },
});
