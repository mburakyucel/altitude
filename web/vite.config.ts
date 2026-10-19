import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import { terminalRequest } from "./src/data/terminalRequest";

// Dev server proxies /api and /digest.wav to the altd ALTITUDE_DEV_API names, a backend run for development
// (docs/DEVELOPMENT.md#ui-development; a self-signed one is accepted, hence secure: false). Without it nothing is
// proxied: altd takes a forwarder on this computer for its clients, so it could not refuse one of its own agents
// (docs/ARCHITECTURE.md#altitudes-own-agents). `pnpm build` emits web/dist, which altd serves directly in production.
declare const process: { env: Record<string, string | undefined> };
const API = process.env.ALTITUDE_DEV_API;

export default defineConfig(({ command }) => {
  if (!API && command === "serve" && !process.env.VITEST) {
    console.warn("ALTITUDE_DEV_API is not set, so the dev server reaches no altd (docs/DEVELOPMENT.md#ui-development).");
  }
  return {
    plugins: [react(), tailwindcss()],
    server: {
      proxy: API ? {
        // The terminal is not proxied: behind the proxy altd would see the proxy as the client, and could no
        // longer refuse a request from one of its own agents (docs/ARCHITECTURE.md#operator-terminal).
        // altd accepts an action only from its own page: the dev page's requests carry altd's origin, and a
        // request from any other page keeps its own, which altd refuses.
        "/api": { target: API, secure: false, changeOrigin: true, bypass: (req) => {
          if (terminalRequest(req.url)) return false;
          if (req.headers.origin === `${"encrypted" in req.socket ? "https" : "http"}://${req.headers.host}`) {
            req.headers.origin = new URL(API).origin;
          }
        } },
        "/digest.wav": { target: API, secure: false, changeOrigin: true },
      } : undefined,
    },
    test: {
      environment: "jsdom",
      setupFiles: ["./src/vitest.setup.ts"],
      css: false,
      // Time limits end a failing test; they never budget a passing one. A Mac validation run on a host
      // busy with other tasks runs these tests up to ten times slower than an idle one (#617).
      testTimeout: 30_000,
    },
  };
});
