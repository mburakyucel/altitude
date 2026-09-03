# Altitude web UI

Vite, React, and TypeScript SPA styled with Tailwind over `design/tokens.css`. TanStack Query and
zod own the API boundary; react-router owns navigation; vitest and Testing Library cover behavior.

- Build for the Python server: `make web` from the repository root. It installs from the frozen
  lockfile and emits `web/dist/`, including the SPA fallback used by `altd`.
- Develop: `pnpm dev`. Vite proxies `/api` and `/digest.wav` to a local server.
- Test: `pnpm test`. Typecheck and production build: `pnpm build`.

The four primary navigation destinations are Inbox, Projects, Chat, and Monitor. Project and Task
are detail routes; a task has Conversation and Live session tabs, and tasks are created only through
Chat. Shared API, mutation, toast, and test helpers live under `src/data/` and
`src/test/`.
