# Altitude web UI

Vite + React 19 + TypeScript SPA, styled with Tailwind 4 over `design/tokens.css`
(light/dark/system via `data-theme`). Data layer: TanStack Query + zod (`src/data/api.ts`),
routing via react-router, tests with vitest + testing-library.

- **Build for altd**: `make web` (repo root) — installs with a frozen lockfile and emits
  `web/dist/`, which altd serves (SPA fallback; `/api/*` untouched).
- **Develop**: `pnpm dev` — Vite dev server proxying `/api` and `/digest.wav` to a locally
  running altd (self-signed TLS accepted). Needs node >= 22 and pnpm (corepack).
- **Test**: `pnpm test` (vitest, jsdom). Typecheck: `pnpm typecheck`.

Route components live in `src/routes/`; Inbox is implemented, the rest are stubs filled in
by later slices. Shared pieces for those slices: `src/data/api.ts` (query/mutation hooks,
`streamChat`), `src/data/useOptimisticMutation.ts`, `src/data/Toast.tsx`, and
`src/test/render.tsx` (`renderApp`).
