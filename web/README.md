# Altitude web UI

Vite, React, and TypeScript SPA styled with Tailwind over `design/tokens.css`. TanStack Query and
zod own the API boundary; react-router owns navigation; vitest and Testing Library cover behavior.

- Build for the Python server: `make web` from the repository root. It installs from the frozen
  lockfile and emits `web/dist/`, including the SPA fallback used by `altd`.
- Develop: `pnpm dev`. Vite proxies `/api` and `/digest.wav` to a local server.
- Test: `pnpm test`. Typecheck and production build: `pnpm build`.

The shell has three destinations: Needs you, the projects, and Monitor (`design/wireframes/SPEC.md`
§2.1). A project page is the conversation with L3 and the work panel; a task page has Conversation
and Live session, and `/report` under it is the task's report view. L3 creates tasks from the
conversation; the UI never creates one. Shared API, mutation, toast, and test helpers live under
`src/data/` and `src/test/`; the conversation's rows, the system line, and the one composer live
under `src/components/`.

The project header's **Design boards** opens `/design/<project>`, the generated viewer described
in [`design/wireframes/README.md`](../design/wireframes/README.md). The boards and spec jointly
record the visual design and rules. The viewer reads the committed HTML directly, so a board
update needs regeneration with `gen.py` and a checkout update, without rebuilding the SPA.
