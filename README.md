# Altitude

*Keep Burak at altitude.* Altitude is an experimental control surface for turning high-level work
into bounded agent execution while keeping the human at the decision points. The first implementation
overbuilt its orchestration and self-healing control plane; the runtime is now deliberately stopped
for an architecture reset. Start with the as-built map and agreed operating model below.

## Docs

1. `docs/ARCHITECTURE.md` — **start here**: current as-built system, agreed operating model,
   collision boundaries, recovery behavior, and restart boundary.
2. `docs/ROADMAP.md` — current stabilization sequence and restart-critical outcomes.
3. `design/wireframes/README.md` — first-draft product wireframes retained for later reassessment.

Status 2026-08-31: **stabilization reset**. The service is stopped and masked. Existing tasks,
sessions, rules, incidents, and worktrees are being archived instead of resumed. Future work is
consolidated in issues #104–#109; no old task should be replayed automatically.

GitHub CI has one base-owned `Remote tests / Python` check for pull requests and main pushes. It
checks out the exact event commit and runs the repository's Python tests with throwaway runtime
state; the former cgroup, manifest, and evidence-artifact gate is no longer part of the system.

## Runtime hold

Do not start or install the service during the reset. The commands below are retained only as
developer reference; using them requires an explicit human decision after the archive and
architecture review.

```
make web                      # build the SPA into web/dist (pnpm install --frozen-lockfile + vite build) — once, and after any web/ change
make run                      # local development server only; held during stabilization
make install-service          # held during stabilization
```

Web app: a Vite 7 / React 19 / TypeScript SPA in `web/` — Tailwind 4 over `web/design/tokens.css`, react-router, TanStack Query 5 polling the JSON API, zod at the edge — built by `make web` into `web/dist`, which altd serves with an SPA fallback (any unknown path returns `index.html`, so deep links and a phone refresh work). Six current routes: Inbox, Projects, Project, Task, Chat, and Monitor. The Python server owns the API; the SPA owns nothing but rendering.

Layout: `altitude/` (stdlib package: state, tasks, engines, l3, propose, dispatch, verify, improve, monitor, digest, intake, server) · `bin/alt` · `personas/` · `schemas/` · `templates/` · `hooks/` · `web/` · `systemd/` · `docs/`. Runtime state: `~/.altitude/` (`projects.json`, `<project>/{STATE.md,l3.json,chat.jsonl,inbox.jsonl,tasks/,archive/,incidents/}`, `monitor/`, `incidents.jsonl`).
