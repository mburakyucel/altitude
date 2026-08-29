# PROGRESS — Altitude build log and roadmap

*The file that survives compaction. Updated at every step: what is done, what is in flight, what is next, and what was decided on the way. Older stages are kept as the archive. Convention (decision 33): every L2 brief demands the same file shape in its progress file.*

## Roadmap — phase 0 (first usable version)

| # | Stage | Status | Notes |
|---|---|---|---|
| 0 | Name, repo, progress file, docs renamed to Altitude | in progress | `altd` server, `alt` CLI, `~/.altitude/` state; private GitHub repo |
| 1 | State library + task lifecycle (`altitude/state.py`, `tasks.py`) | todo | folders, atomic `status.json`, `events.log`, `STATE.md` regen, dispatch ids, reconcile |
| 2 | Personas + schemas + templates | todo | generated from `ROLES.md`; `rules/global` compiled in |
| 3 | L3 turn runner (`l3.py`, `engines.py`) | todo | `claude -p --resume`, serialized per project, usage, rotation |
| 4 | Proposal + critic (`propose.py`) | todo | `--json-schema`; Codex critic for L |
| 5 | Dispatch + done signal (`dispatch.py`, `verify.py`) | todo | `claude --bg`, `claude agents --json` poll, verify-report, report-landed turn |
| 6 | Spend envelope (`envelope.py`, `hooks/`) | todo | class table, subagent-cap hook, quota reserve, spend fields |
| 7 | Self-improvement (`improve.py`, `rules/`) | todo | incidents, rules, scopes, promote, weekly audit |
| 8 | Web app (`server.py`, `web/`) | todo | Projects / Project / Inbox / Chat / Monitor / Listen |
| 9 | Intake (`intake.py`) | todo | `/idea`, `/backlog` via `gh` |
| 10 | Gold set + scorecard; first end-to-end task on a project | todo | milestone: one M task from the phone to *done* |

## Log

- **2026-08-29** — Design complete (docs/*.md, 33 decisions). Build started in the design session. Stage 0: name settled (Altitude), docs renamed, git initialised, private repo created.

## Decisions made while building (mirrored into `DECISIONS.md` when they matter beyond the build)

- (none yet)

## Deviations from the design (say why)

- (none yet)
