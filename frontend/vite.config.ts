import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The browser talks only to the Vite origin; /api is proxied to the gateway, so no CORS is needed
// in development. GATEWAY_URL defaults to a gateway running on the Docker host.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.GATEWAY_URL ?? "http://host.docker.internal:8000",
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  test: { environment: "jsdom", globals: true },
});
