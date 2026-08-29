# ROADMAP — Altitude

*High-level, by milestone. Each item is mirrored as a GitHub issue labelled `roadmap` + `m:<milestone>` so the project's own L3 can triage it with `/backlog` and Burak can talk about it in Chat. This file is the narrative; the issues are the queue; `docs/PROGRESS.md` is the log of what actually happened. Milestones are ordered by what unblocks the next one, not by date.*

## 0.1 — skeleton (done 2026-08-29)
State library and lifecycle, personas, L3 turn runner, proposal + critic, `claude --bg` dispatch with envelope hooks, verify-report, self-improvement loop (incidents → rules with scopes), `altd` + five-view web app, TLS over WireGuard, systemd unit, first end-to-end task on Altitude itself.

## 0.2 — first real project, calibrated
The point where Burak runs career-platform through Altitude from the phone and trusts the Decision/FYI split.
- Onboard career-platform (stacks, approval policy, never-list extraction from its `CLAUDE.md`).
- Gold set: label ~30 past Decision/FYI items from the orchestrator progress files; L3 scored against it; kill criterion (≥10 % wrong over 20 tasks) reported on the Monitor tab.
- Seat quota on the page: statusline wrapper installed, reserve line active, holds visible.
- Needs-input handling for L2s: the card's *Resume* proven; `AskUserQuestion` from an L2 surfaced as a card.
- Report verifier: roadmap-complete check, deploy health check from the brief's smoke command, `main` run lookup by branch.
- Morning digest rendered by Kokoro and playable from Listen; push line when a Decision appears.

## 0.3 — the improvement loop proven on real incidents
- First real incidents from career-platform tasks → project rules through the apply-rule S-task path (PR + FYI-with-veto).
- `rules/stacks/python`, `rules/stacks/cdk`, `rules/stacks/typescript` seeded from those incidents; promotion with cross-project evidence exercised once.
- Weekly audit turn retires or tightens at least one rule; audit output visible on the page.
- Burak's chat corrections ("that should have been an FYI") automatically become incidents.

## 0.4 — spend that is proportional, by measurement
- Spend accounting per task and per day (turns, subagent launches, retries, model tiers) on the Monitor tab; envelope tuned from the first 20 tasks.
- `--max-budget-usd` on subscription seats: verified and used, or dropped.
- Per-project daily budget and a machine-wide reserve; holds explained on the page.
- **Night shift (idea, guarded — not before 0.4 is measured):** at a scheduled time, if the 5-hour window is below X % and the 7-day below Y %, the project L3 picks ≤2 roadmap/backlog items of class ≤ M, runs them inside their envelopes, and stops; opt-in per project; every morning's digest leads with what the night shift did and spent. The guard is the reserve line, not good intentions.

## 0.5 — second engine
- Codex as an L2 through an engine adapter (`start / alive / logs / send / collect`); Codex critic and reviewer hardened; `codex app-server` approvals forwarded as cards.
- Model tiering per level tuned from spend data, not guesses.

## 0.6 — surfaces
- PWA manifest + install on the phone; push notifications for Decisions (secure context via the existing TLS).
- Direct L2 message thread on the task card (today: a resume with a note); `claude attach` link.
- Remote Control as a polished secondary surface; Telegram optional.

## 0.7 — many projects, unattended for hours
- Reconcile automation (missing sessions, stale worktrees, orphan PRs) with one-tap repair.
- L2 auto-rotation at the act threshold from the progress file; quality heuristics (edit–test loops, repeated tool errors).
- Cross-project queue polish: ranking, snooze, batch approve for S-class.

## 1.0 — open source
- Burak-specific bits (paths, WireGuard posture, Kokoro) behind config; an auth story for non-tunnel deployments.
- Tests and CI for the state library, verifier, hooks; a `docs/` tour for someone else's first project; license; security review of the "tunnel is the auth" posture.
