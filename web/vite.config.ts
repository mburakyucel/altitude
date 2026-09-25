import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import { terminalRequest } from "./src/data/terminalRequest";

// Dev server proxies /api and /digest.wav to a locally running altd (self-signed TLS, hence
// secure: false). `pnpm build` emits web/dist, which altd serves directly in production.
declare const process: { env: Record<string, string | undefined> };
// ALTITUDE_DEV_API overrides the target (e.g. a plain-http altd started with ALTITUDE_TLS=0).
const API = process.env.ALTITUDE_DEV_API ?? "https://127.0.0.1:8890";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      // The terminal is not proxied: behind the proxy altd would see the proxy as the client, and could no
      // longer refuse a request from one of its own agents (docs/ARCHITECTURE.md#operator-terminal).
      "/api": { target: API, secure: false, changeOrigin: true, bypass: (req) => (terminalRequest(req.url) ? false : undefined),
        // altd accepts an action only from its own page: the dev page's requests carry altd's origin, and
        // a request from any other page keeps its own, which altd refuses.
        configure: (proxy) => proxy.on("proxyReq", (out, req) => {
          const page = `${"encrypted" in req.socket ? "https" : "http"}://${req.headers.host}`;
          if (req.headers.origin === page) out.setHeader("origin", new URL(API).origin);
        }) },
      "/digest.wav": { target: API, secure: false, changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/vitest.setup.ts"],
    css: false,
  },
});
