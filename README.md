# Altitude

*Keep Burak at altitude.* Altitude is an experimental control surface for turning high-level work
into bounded agent execution while keeping the human at the decision points. The first implementation
overbuilt its orchestration and self-healing control plane; the runtime is now deliberately stopped
for an architecture reset. Start with the as-built map and smaller target below.

## Docs

1. `docs/ARCHITECTURE.md` — **start here**: current as-built system, failure loop, complexity inventory, six-component target, invariants, and restart boundary.
2. `docs/VISION.md` — the original problem, product intent, principles, and success criteria.
3. `docs/ROLES.md` — the older L1/L2/L3 contract; historical input to the reset, not authority to restart it.
4. `docs/DECISIONS.md` and `docs/RULES.md` — historical decision and rule ledgers; they contain drift and must be reconciled before becoming binding again.
5. `docs/BEFORE-BUILDING.md`, `docs/BUILDING-BLOCKS.md`, and `docs/LANDSCAPE.md` — original research and constraints.

Status 2026-08-31: **stabilization reset**. The service is stopped and masked. Existing tasks,
sessions, rules, incidents, and worktrees are being archived instead of resumed. Future work is
consolidated in issues #104–#109; no old task should be replayed automatically.

## Context this builds on

- `~/Projects/career-platform/docs/ENGINEERING.md` — the existing orchestrator → worktree-subagent pattern, progress files, "Report for Burak", never-list.
- `~/Projects/system-design/selfhost/README.md` — WireGuard + always-on PC + phone-facing server that already runs `claude -p` on the subscription seat.
- `~/Projects/voice-tutor` — local Kokoro TTS and the Stop-hook speaker (audio channel).

- `docs/KICKOFF.md` is the historical build kickoff. It is retained for provenance and is not the
  current starting point.

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

Layout: `altitude/` (stdlib package: state, tasks, engines, l3, propose, dispatch, verify, improve, monitor, digest, intake, server) · `bin/alt` · `personas/` · `rules/{global,stacks}/` · `schemas/` · `templates/` · `hooks/` · `web/` · `systemd/` · `docs/`. Runtime state: `~/.altitude/` (`projects.json`, `<project>/{STATE.md,l3.json,chat.jsonl,inbox.jsonl,tasks/,archive/,incidents/}`, `monitor/`, `incidents.jsonl`).
