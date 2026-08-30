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
- 2026-08-30 — PR #27 (rule-id reservation for pending drafts, I-003) merged after my own review: its L2 hit the launch cap (a `codex exec --help` probe counted as launch 2, the reviewer would have been 3) and stopped correctly with `blocked: envelope`. Two altd fixes from that: the poller now treats a landed `report.json` as finished (it had labelled the task "idle, needs input" because the idle check ran before the report check), and the cap hook ignores `--help`/`--version` probes.
- 2026-08-30 — Codex rejected every output schema (`--output-schema` must be strict: `additionalProperties:false` everywhere, every property required, optionals nullable, no nullable objects) so the critic had been silently falling back to Claude. All four schemas made strict and verified against `codex exec`; envelope merge from proposals now ignores nulls. First M-class flow ran end to end: proposal (26 turns) → critic (revise, 5 issues) → L3 verified the blocking issue in `verify.py` itself and parked the task with a re-proposal brief instead of sending Burak a $4/mo card built on a wrong premise.
- 2026-08-30 — Phone could not reach altd: ufw on this machine only allowed 8080/8443 on wg0 (pocketbook installers); 8890 needed `sudo ufw allow in on wg0 to any port 8890 proto tcp` (documented in README + `make install-service`). Server is TLS-only, so the URL is `https://`. Fixed `--json-schema` (Claude CLI wants the JSON text, not a path) — every M/L proposal had been failing; `tick()` now retries a proposal that left no `proposal.json` after 30 min. Server logs the first request per client address (requests were fully silent, which made this hard to diagnose).

- **2026-08-29** — Design complete (docs/*.md, 33 decisions). Build started in the design session. Stage 0: name settled (Altitude), docs renamed, git initialised, private repo created.
- **2026-08-29 (later)** — Stages 1–9 written (~1.9k lines Python + web). Verified: lifecycle test, CLI, cap hook, a real L3 turn, server endpoints + page. Not yet verified: proposal agent, `claude --bg` dispatch through the server, verify-report against GitHub, Codex critic.

- **2026-08-29 (evening)** — TLS over WireGuard (reusing the pocketbook CA; verified https 200 with that CA), user systemd unit, `alt tls-init`, `/ca.crt`. `docs/ROADMAP.md` written and mirrored to GitHub issues (`roadmap`, `m:*`, `idea` labels). First e2e task (`add-tests-for-the-rules-ledger-parser`) dispatched through the live server; L2 wrote roadmap first, one Sonnet L1, Codex reviewer — result pending.

- **2026-08-29 23:58 UTC** — **First end-to-end task landed through the live server**: `add-tests-for-the-rules-ledger-parser` (S). L2 wrote the roadmap first, one Sonnet L1 in a worktree, Codex reviewer, PR #1 squash-merged, `report.json` verified against GitHub (`verdict ok`, 24 turns, 2 launches, 0 retries, 3 deviations, roadmap complete). Learnings from its FYIs: (a) Safety Net / worktree-isolation hooks refused ~6 compound shell commands in the background session (~5 extra turns) — candidate for the first rule; (b) a Codex reviewer run through Bash was invisible to the subagent-cap hook → hook now also counts `codex exec` / `claude -p|--bg` in Bash commands; (c) L1 worktrees with merged branches linger → `cleanup_after_done` (claude rm + remove merged worktrees) added to the tick.

- **2026-08-30 00:00 UTC** — **The loop closed itself on task 1.** L3's report-landed turn: digest, `alt task done`, then the post-mortem filed **I-001** (Bash-launched reviewer invisible to the cap hook) and **I-002** (Safety Net refusing compound shell commands, ~5 turns lost), proposed two project rules, noticed both got id R-001 (ledger did not exist yet), parked the duplicate, and created two follow-up S tasks (fix the id collision; the L2's test-hygiene follow-ups) — dispatched by the service. Bugs this exposed and fixed in `altd`: two servers over one state double-dispatched a task (now a `dispatching` claim under the project lock); parking/rejecting a running task left its session alive (now `claude rm`); rule-application tasks ran concurrently on the same ledger (now one at a time). Service installed: `https://10.88.0.1:8890` (user unit, linger on).

## Decisions made while building (mirrored into `DECISIONS.md` when they matter beyond the build)

- The **server runs the proposal agent** for every requested M/L task and gives L3 a `proposal-ready` turn; L3 never calls it (keeps L3 turns short, one writer). Same for dispatch: approved tasks are launched by the timer when WIP/quota allow.
- **Prompts go to `claude -p` via stdin**, never positional: `--allowedTools` is variadic and swallows a trailing prompt.
- **Per-dispatch `--settings` file** carries the envelope hooks (PreToolUse subagent cap, PostToolUse edit count) and `ALTITUDE_*` env — nothing global is touched. Statusline wrapper is opt-in (`alt install-statusline`).
- **L2 context %** is read from the session transcript (`~/.claude/projects/*/<sid>.jsonl`, last assistant usage), not from the statusline, because headless sessions have no statusline.
- **FYI-class M proposals** (no question, no always-list hit) are approved automatically by the server with an FYI (decision 13), recorded in events as `note: auto`.

## Deviations from the design (say why)

- Stage order: web app (8) was built before intake (9) and the end-to-end test (10), to make the page available for the first real task.
