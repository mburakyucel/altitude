import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Dev server proxies /api and /digest.wav to a locally running altd (self-signed TLS, hence
// secure: false). `pnpm build` emits web/dist, which altd serves directly in production.
declare const process: { env: Record<string, string | undefined> };
// ALTITUDE_DEV_API overrides the target (e.g. a plain-http altd started with ALTITUDE_TLS=0).
const API = process.env.ALTITUDE_DEV_API ?? "https://127.0.0.1:8890";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/api": { target: API, secure: false, changeOrigin: true },
      "/digest.wav": { target: API, secure: false, changeOrigin: true },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/vitest.setup.ts"],
    css: false,
  },
});
