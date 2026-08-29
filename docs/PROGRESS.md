# PROGRESS — Altitude build log and roadmap

*The file that survives compaction. Updated at every step: what is done, what is in flight, what is next, and what was decided on the way. Older stages are kept as the archive. Convention (decision 33): every L2 brief demands the same file shape in its progress file.*

## Roadmap — phase 0 (first usable version)

| # | Stage | Status | Notes |
|---|---|---|---|
| 0 | Name, repo, progress file, docs renamed to Altitude | done | Altitude; `github.com/mburakyucel/altitude` (private) |
| 1 | State library + task lifecycle (`altitude/state.py`, `tasks.py`) | done | `altitude/{config,state,tasks}.py`; `tests/test_lifecycle.py` passes |
| 2 | Personas + schemas + templates | done | 6 personas, 4 schemas, brief/incident templates, `rules/global/RULES.md` R-001…R-007 (probation) |
| 3 | L3 turn runner (`l3.py`, `engines.py`) | done | `engines.py` (stdin prompt — `--allowedTools` is variadic), `l3.py`; real turn verified: 4 turns, 11.6% ctx |
| 4 | Proposal + critic (`propose.py`) | built, untested | `propose.py`: proposal agent (`--json-schema`, plan mode) + Codex critic with Claude fallback; server runs it automatically for M/L |
| 5 | Dispatch + done signal (`dispatch.py`, `verify.py`) | built, untested | `dispatch.py` (`claude --bg`, per-task `--settings` with hooks, poll), `verify.py` (gh pr/run checks, signals) |
| 6 | Spend envelope (`envelope.py`, `hooks/`) | done | class table in `tasks.py`, `hooks/subagent_cap.py` blocks past cap (tested), quota reserve in `monitor.py` (needs statusline wrapper) |
| 7 | Self-improvement (`improve.py`, `rules/`) | built, tested locally | `improve.py`: incidents, `rule propose` → S-task + FYI-with-veto, scopes with promotion evidence, audit input |
| 8 | Web app (`server.py`, `web/`) | built, smoke-tested | `server.py` + `web/`: Inbox, Projects (discover + Start L3), Project, Chat (streamed), Monitor, Listen; all endpoints 200 |
| 9 | Intake (`intake.py`) | built, untested | `intake.py`: `/idea`, `/backlog` (gh issue list) |
| 10 | Gold set + scorecard; first end-to-end task on a project | next | end-to-end on Altitude itself (decision 28), then career-platform when Burak says so |

## Log

- **2026-08-29** — Design complete (docs/*.md, 33 decisions). Build started in the design session. Stage 0: name settled (Altitude), docs renamed, git initialised, private repo created.
- **2026-08-29 (later)** — Stages 1–9 written (~1.9k lines Python + web). Verified: lifecycle test, CLI, cap hook, a real L3 turn, server endpoints + page. Not yet verified: proposal agent, `claude --bg` dispatch through the server, verify-report against GitHub, Codex critic.

## Decisions made while building (mirrored into `DECISIONS.md` when they matter beyond the build)

- The **server runs the proposal agent** for every requested M/L task and gives L3 a `proposal-ready` turn; L3 never calls it (keeps L3 turns short, one writer). Same for dispatch: approved tasks are launched by the timer when WIP/quota allow.
- **Prompts go to `claude -p` via stdin**, never positional: `--allowedTools` is variadic and swallows a trailing prompt.
- **Per-dispatch `--settings` file** carries the envelope hooks (PreToolUse subagent cap, PostToolUse edit count) and `ALTITUDE_*` env — nothing global is touched. Statusline wrapper is opt-in (`alt install-statusline`).
- **L2 context %** is read from the session transcript (`~/.claude/projects/*/<sid>.jsonl`, last assistant usage), not from the statusline, because headless sessions have no statusline.
- **FYI-class M proposals** (no question, no always-list hit) are approved automatically by the server with an FYI (decision 13), recorded in events as `note: auto`.

## Deviations from the design (say why)

- Stage order: web app (8) was built before intake (9) and the end-to-end test (10), to make the page available for the first real task.
