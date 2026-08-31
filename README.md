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

## Run

```
make web                      # build the SPA into web/dist (pnpm install --frozen-lockfile + vite build) — once, and after any web/ change
make run                      # altd on 127.0.0.1:8890 (open http://127.0.0.1:8890)
# Executive override: `alt task approve <slug>` on a requested task (or the *build now* button) skips the proposal/critic loop and dispatches
make install-service          # user systemd unit on https://10.88.0.1:8890 — reachable from the phone over WireGuard
                              # (ufw: sudo ufw allow in on wg0 to any port 8890 proto tcp — the pocketbook installers only opened 8080/8443)
bin/alt project add <name> --path ~/Projects/<name> --stacks python,cdk   # or press "Start L3" on the Projects tab
bin/alt -p <name> chat "…"    # talk to L3 from a terminal; the page does the same with streaming
bin/alt install-statusline    # optional: wraps ~/.claude/statusline.sh so the seat quota shows on the Monitor tab (edits ~/.claude/settings.json)
```

Web app: a Vite 7 / React 19 / TypeScript SPA in `web/` — Tailwind 4 over `web/design/tokens.css`, react-router, TanStack Query 5 polling the JSON API, zod at the edge — built by `make web` into `web/dist`, which altd serves with an SPA fallback (any unknown path returns `index.html`, so deep links and a phone refresh work). Seven routes: Inbox, Projects, Project, Task, Chat, Monitor, Listen. The Python server owns the API; the SPA owns nothing but rendering.

Layout: `altitude/` (stdlib package: state, tasks, engines, l3, propose, dispatch, verify, improve, monitor, digest, intake, server) · `bin/alt` · `personas/` · `rules/{global,stacks}/` · `schemas/` · `templates/` · `hooks/` · `web/` · `systemd/` · `docs/`. Runtime state: `~/.altitude/` (`projects.json`, `<project>/{STATE.md,l3.json,chat.jsonl,inbox.jsonl,tasks/,archive/,incidents/}`, `monitor/`, `incidents.jsonl`).
