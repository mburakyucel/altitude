# ROADMAP — Altitude

*High-level, by milestone. Each item is mirrored as a GitHub issue labelled `roadmap` + `m:<milestone>` so the project's own L3 can triage it with `/backlog` and Burak can talk about it in Chat. This file is the narrative; the issues are the queue; `docs/PROGRESS.md` is the log of what actually happened. Milestones are ordered by what unblocks the next one, not by date.*

## 0.1 — skeleton (done 2026-08-29)
State library and lifecycle, personas, L3 turn runner, proposal + critic, `claude --bg` dispatch with envelope hooks, verify-report, self-improvement loop (incidents → rules with scopes), `altd` + five-view web app, TLS over WireGuard, systemd unit, first end-to-end task on Altitude itself.

## 0.2 — first real project, calibrated

- **Web app on the career-platform front-end template** (decision 37): Vite/React 19/TS, react-router 8, TanStack Query 5, Tailwind 4 + tokens, Vitest; feature parity with 0.1 (Inbox, Projects → L3 + L2s, task modal, streamed Chat, Monitor, Listen), served from `web/dist` by altd. Pulled forward from 0.6.
