# KICKOFF — brief for a build session

*Written 2026-08-29 at the end of the design session; the build started the same day in that session. **Current state is in `docs/PROGRESS.md` — read it first**, then this file. The phase-0 list below is kept as the reference for what "complete" means; the progress table says what is actually done.*

## Read, in this order

0. `docs/PROGRESS.md` — what exists, what was verified, what was deviated from, what is next.
1. `docs/ROLES.md` — the binding altitude contract (what reaches Burak, what each level owns). Personas are generated from it.
2. `docs/ARCHITECTURE.md` — **v1 is authoritative**; Appendix A is deferred and must not be built.
3. `docs/DECISIONS.md` — 30 decisions; settled ones (1, 15, 29, 10, 30) are not up for debate, proposed ones are the default unless building reveals a real problem (then say so in the report, do not silently deviate).
4. `docs/BUILDING-BLOCKS.md` — the verified CLI facts, especially "Verified by hand on this machine".
5. `docs/VISION.md` for the why, `docs/LANDSCAPE.md` and `docs/BEFORE-BUILDING.md` only if you need to know why something was rejected.

## What to build (phase 0, in this order)

1. **Repo layout** (decision 26): `bin/`, `altitude/` (Python 3.12 stdlib package), `personas/`, `rules/{global,stacks/<stack>}/`, `schemas/`, `templates/`, `web/` (one HTML page + a little JS, no build step), `docs/`. Runtime state in `~/.altitude/` (`projects.json`, `<project>/…`). A `Makefile` or `bin/alt-dev` to run the server in the foreground; a systemd user unit like `tutor.service` for real use.
2. **State library + lifecycle** (ARCHITECTURE §1–2): task folders, `status.json` written temp-then-rename, `events.log`, regenerated `STATE.md`, stable dispatch ids `<slug>-<attempt>`, `reconcile`.
3. **Personas** `personas/{l3,l2,l1,proposal,critic,reviewer}.md` generated from `ROLES.md`, plus `schemas/{proposal,report}.json` and the report-contract template. Keep each persona under ~120 lines; the Decision rule and its worked examples go in verbatim.
4. **L3 turn runner** (§3): `claude -p [--resume id] --append-system-prompt-file personas/l3.md --permission-mode auto --allowedTools … --output-format stream-json --include-partial-messages`, `CLAUDE*` env cleared, serialized per project, usage recorded, rotation at the warn threshold from `STATE.md`. Copy the process handling from `~/Projects/system-design/audio/server.py` (`run_claude`, `run_claude_stream`) — it is proven.
5. **Proposal + critic** (§4, §7): fresh `claude -p --json-schema schemas/proposal.json --permission-mode plan`; critic on Codex (`codex exec --json --output-schema … -s read-only < /dev/null`) for L-class only.
6. **Dispatch + done signal** (§5): `claude --bg --name <project>/<slug>-<n> -w <slug> --append-system-prompt-file personas/l2.md --permission-mode auto --max-turns N '<brief>'`; poll `claude agents --json` every ~30 s; `verify-report`; report-landed L3 turn with the **post-mortem section** (§8).
7. **Spend envelope** (§9, decision 31, `ROLES.md` table): class table in the launcher (`--max-turns`, envelope into `status.json`), the `PreToolUse` subagent-count hook (verify the tool name is `Agent` in 2.1.251 and that a hook can block it), the quota reserve line in the dispatcher (default 70% of the 5-hour window), spend fields in the report; check whether `--max-budget-usd` counts on a subscription seat.
8. **Self-improvement** (§8): `alt incident|rule` operations, `docs/incidents/I-###.md` + `docs/RULES.md` templates, auto-created "apply rule" S-task, weekly audit turn; **scopes** (decision 32): `rules/global/RULES.md` compiled into the personas, `rules/stacks/<stack>/` appended per project from `projects.json`'s `stacks` list, `alt rule promote`, the cross-project incident index `~/.altitude/incidents.jsonl`. Seed `rules/global/` with the rules already in `ROLES.md` (altitude, verify-per-artifact, envelope, report contract), each with a ledger entry whose origin is this design session. This is a v1 feature, not polish.
9. **Web app** (§6): five views — Inbox (Decision queue with Approve/Reject/Revise/Park, FYI tail, WIP), Project (task cards with live L2 status from `claude agents --json`, PRs, message-the-L2, attach command), Chat (streamed; `/idea`, `/backlog`), Monitor (context % + 5h/7d quota per session from the statusline files), Listen (Kokoro via `~/Projects/voice-tutor/hooks/speak.py --stdin-text`, reuse the pocketbook player). Bind to the WireGuard address (`10.88.0.1`), no auth, no TLS, phone-first CSS. Every view is a rendering of files the server also exposes as JSON.
10. **Intake** (§6a): `/idea` → L3 files or parks; `/backlog` → `gh issue list --state open --json number,title,labels,body` → L3 proposes batches.
11. **Gold set + scorecard** (§10): the labeled past Decision/FYI items from career-platform's `.claude/progress/orchestrator-*.md`, and the scorecard fields in `STATE.md`.

**First milestone:** Burak, from his phone, opens the page, types one M-class request for career-platform, approves L3's proposal with a button, and an L2 takes it to *done* (merged, main green, deploy healthy) and the report shows up in Inbox as an FYI — with the post-mortem pass having run. Stop there and report; do not start phase 2.

## Constraints

- **Do not modify** `~/Projects/career-platform` or any other source repo except through a dispatched L2 that opens a PR. Do not change `~/.claude/settings.json` or the global statusline without saying so in the report (the monitor wrapper is meant to *wrap* the existing `~/.claude/statusline.sh`, not replace it).
- Subscription CLIs only (decision 1). No Agent SDK, no API keys, no framework, no database (decision 25). If you think a framework is needed, write that as a finding, keep going with stdlib.
- Nested `claude`/`codex` launches need the `CLAUDE*` environment variables unset; `codex exec` must be run with `< /dev/null`.
- `claude rm` refuses worktrees with unpushed commits; design cleanup around that (push or explicitly discard).
- Kill criterion (decision 20): if L3 gives ≥10% wrong Decision/FYI answers over the first 20 tasks, the design is wrong — report it, do not patch the persona ad hoc.
- Report in the fixed contract from `ROLES.md`/`ARCHITECTURE.md` §5 when you finish, including anything you deviated from and why.

## Verified on this machine (do not re-verify, do check versions if something breaks)

- Claude Code 2.1.251: `claude --bg --name <name> [-w <slug>] --append-system-prompt-file <persona> --permission-mode auto --max-turns N '<prompt>'` (prompt is positional; `--bg` conflicts with `-p`). Worktree auto-created at `<repo>/.claude/worktrees/<slug>`. `claude agents --json --all` → `id, name, sessionId, cwd, startedAt, status (busy|idle), state (done)`. `claude attach|logs|rm|respawn`.
- Codex 0.151.0: `codex exec --json --output-schema <file> -o <file> -s read-only -C <dir> --skip-git-repo-check < /dev/null`; `codex exec resume|fork`.
- Statusline JSON carries `context_window.used_percentage` and `rate_limits.five_hour|seven_day.used_percentage`.
- Pocketbook server (`~/Projects/system-design/audio/server.py`) runs `claude -p` on the subscription seat with resumable sessions over WireGuard — the pattern for the L3 runner and the web app.
- Kokoro at `127.0.0.1:8880`, `speak.py --stdin-text`.

## Open — ask Burak only through the page or at the end

default L3 engine (Claude assumed), approval policy per project, second-opinion scope, and whether driving the logged-in `claude` binary this way is within Anthropic's subscription terms (Burak said he would check).
