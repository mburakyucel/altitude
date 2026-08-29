# Altitude

*Keep Burak at altitude.* An executive layer above coding-agent orchestrators: one long-lived **chief-of-staff agent per project** that turns a high-level request into research → proposal → approval → execution, dispatches orchestrator sessions (Claude Code / Codex, subscription-billed, headless), digests their reports, and keeps its own context small. The human stays in control at the decision points and reads everything from a phone.

## Docs

1. `docs/VISION.md` — the problem, the goal, the L1/L2/L3 model, principles, non-goals, success criteria. **Start here.**
2. `docs/BUILDING-BLOCKS.md` — what Claude Code and Codex already provide (verified 2026-08-29), and what that settles.
3. `docs/LANDSCAPE.md` — existing tools (Claude Code native, Paperclip, OpenClaw, Gas Town/Beads, Symphony, Codex, kanban/phone clients) scored against the requirement; verdict, what to borrow, policy risk, recommendation.
4. `docs/ROLES.md` — **binding altitude contract**: what Burak / L3 / L2 / L1 / validators own, hand up, never do, and how collapse is detected; the Decision-vs-FYI rule; the standard loop with size classes; model tiering.
5. `docs/ARCHITECTURE.md` — **v1, authoritative**: interactive Remote-Control L3, `claude --bg` L2s, file state with invariants, five-state lifecycle, `AskUserQuestion` approvals, report contract, cross-project Decision queue, validation tiering, ledgers as conventions, phase-0 build list with scorecard and kill criterion; Appendix A holds the deferred design.
6. `docs/DECISIONS.md` — proposed decisions with trade-offs, open questions for Burak, and a keep/adjust/defer assessment of the brainstormed ideas.
7. `docs/BEFORE-BUILDING.md` — the critical case: are viral tools beneficial, evidence from Burak's own runs, adopt vs fork vs build, the staged plan with metrics, where our own design deserves suspicion, independent reviews. **Read before committing to anything.**

Status 2026-08-29: **Burak decided to build** (decision 15 settled); settled since: web app in v1 (29), self-improvement loop in v1 (10, 30). Design is complete — the next session starts from `docs/KICKOFF.md`. Everything else in `DECISIONS.md` is proposed until he says otherwise.

## Context this builds on

- `~/Projects/career-platform/docs/ENGINEERING.md` — the existing orchestrator → worktree-subagent pattern, progress files, "Report for Burak", never-list.
- `~/Projects/system-design/selfhost/README.md` — WireGuard + always-on PC + phone-facing server that already runs `claude -p` on the subscription seat.
- `~/Projects/voice-tutor` — local Kokoro TTS and the Stop-hook speaker (audio channel).

- [docs/KICKOFF.md](docs/KICKOFF.md) — **start here in the build session**: read order, decisions to honour, phase-0 build list (web app and self-improvement loop included), verified facts, constraints, first milestone.
