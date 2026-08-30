# Architecture — v1 (authoritative)

*Rewritten 2026-08-29 after two independent reviews (`BEFORE-BUILDING.md` §6) found the previous draft described two architectures at once; revised the same day when Burak asked for a lightweight web app up front (decision 29), which replaces the Remote-Control-first surface. This section is what gets built first. Everything deferred lives in Appendix A. Vocabulary and the altitude contract: `ROLES.md`. Decisions: `DECISIONS.md` (15 settled; the rest proposed).*

## One paragraph

One small Python process — **`altd`, the same shape as the pocketbook server** (`system-design/audio/server.py`: stdlib `http.server`, bound to the WireGuard address, threads) — serves a lightweight **web app** Burak opens on his phone or laptop and runs everything behind it. Each registered project has an **L3**: a headless, resumable Claude Code session (`claude -p --resume <id> --append-system-prompt-file personas/l3.md --output-format stream-json`), invoked one turn at a time by the server when Burak sends a message, when an idea arrives, or when an L2 finishes — exactly the proven `/ask` and `/askdoc` mechanism. The app shows the cross-project **Decision queue** with real Approve / Reject / Ask buttons (the only way to approve), each project's tasks and their L2 status (from `claude agents --json`), context and quota per session, a chat with L3, an idea box, the backlog triage view, and a Listen tab (Kokoro). L3 dispatches one **L2 per task as `claude --bg --name`** in a worktree from a one-page brief; L2s own the task to *done* (merged, pipeline green, deploy healthy, review addressed) and write a fixed-contract report; the server notices `state: done` by polling `claude agents --json`, runs `verify-report`, and gives L3 a turn to digest. State is files with one writer per task (the server, via the `alt` library), written atomically, reconciled on a timer. Remote Control + `AskUserQuestion` remain available as a secondary surface (start L3 interactively when wanted), not a dependency.

## Shape

```
 phone / laptop over WireGuard (10.88.0.0/24) ──► http://10.88.0.1:<port>   (the Altitude web app; no auth: the tunnel is the auth)
   │ Decision queue · Approve/Reject/Ask · tasks & L2 status · monitor · chat with L3 · ideas · backlog · Listen
   ▼
 altd  (one Python stdlib process on the PC, systemd unit like tutor.service; serves the page, owns all state writes)
   │  per project: L3 turn = claude -p --resume <l3-session> --append-system-prompt-file personas/l3.md
   │               --output-format stream-json (streamed to the page)   ← the pocketbook /ask pattern
   │  state: ~/.altitude/<project>/STATE.md  tasks/<slug>/{request,proposal,brief,report,digest}.md  status.json  events.log
   │  research/proposal: fresh `claude -p --json-schema` · critic (V) on the other engine for L-class
   │  dispatch: claude --bg --name <project>/<slug>-<n> -w <slug> --append-system-prompt-file personas/l2.md … '<brief>'
   │  L2 done: poll `claude agents --json` (state: done) → verify-report → L3 turn "report landed"
   │  timers: reconcile (status vs agents/git/gh) · digest (Decision queue, WIP limits) · Kokoro render
   ▼
 L2 (per task) → L1 subagents in worktrees → PRs → review (V) → merge → main green → deploy checked → report
                                                     (career-platform ENGINEERING.md flow, unchanged)
```

## 1. State — files, one writer, atomic

`~/.altitude/<project>/` (outside the repo; projects stay self-sufficient because proposals, briefs and reports also land in the repo through the L2's normal flow — `docs/proposals/`, `docs/briefs/`, `.claude/progress/`).

- `STATE.md` — L3's whole working memory, capped ~2k words: project one-liner; standing constraints (pointers to `DECISIONS.md`, `ENGINEERING.md`, never-list); in-flight tasks (one block each: class, status, waiting-on, L2 session name, next expected event); needs-Burak (ranked); recently completed (one line each, 7 days); parked. Regenerated from the `status.json` files by `state.sh`, never hand-edited.
- `tasks/<slug>/` — `request.md` (verbatim ask), `proposal.md` + `proposal.json`, `brief.md`, `report.md` + `report.json`, `digest.md`, `status.json`, `events.log` (append-only), `qa.md` (every question L3 answered on the L2's behalf, with the decision it cited).
- `archive/<slug>/` + `archive/INDEX.md` — one line per finished task.
- `observations.md`, `RULES.md` — the ledgers, as conventions (§8).

**Invariants (from the reviews):** one canonical writer per task folder — `altd`, through the `alt` library (L3 asks for transitions through tools the server exposes; nothing else writes); every write is temp-file-then-rename; every dispatch has a stable id `<slug>-<attempt>` that is also the L2's session `--name`; any process may die at any time and everything resumes from `status.json`; `reconcile.sh` (cron, and on every L3 start) compares each task's `status.json` with `claude agents --json`, the worktree, the branch and `gh pr view`, and either repairs the status or writes an FYI ("orchestrator for `obs-metrics-1` not found; branch has 2 unmerged commits; PR #140 open — resume or abandon?").

## 2. Task lifecycle — five states, one writer

`requested → proposed → approved → running → reported → done` (side states: `rejected`, `parked`, `blocked`). Transitions happen only inside `altd` (`alt.task.new|propose|brief|approve|dispatch|report|done|park|block`), which writes `status.json` atomically, appends to `events.log`, and regenerates `STATE.md`. L3 requests transitions by calling the same operations as tools (`--allowedTools "Bash(alt *)"` — a thin CLI that talks to the running server over localhost, so there is still one writer); the web app's buttons call them directly. L3 never edits state files.

**Approval is only ever a button.** The Decision card shows L3's question and options (recommended first, then alternatives, Revise, Park); the tap is recorded with the question and chosen option verbatim in `events.log`. A "go" typed in chat is not an approval for any class (decision 5). When L3 is run interactively under Remote Control instead (secondary surface), `AskUserQuestion` plays the same role.

## 3. The L3 session — headless, resumable, driven by the server

Per project, `altd` keeps one L3 session id in `~/.altitude/<project>/l3.json`. A turn is `claude -p --resume <id> --append-system-prompt-file personas/l3.md --permission-mode auto --allowedTools "Read,Grep,Glob,Bash(alt *),Bash(git log*),Bash(git diff --stat*),Bash(gh pr view*),Bash(gh pr list*),Bash(gh issue *),Bash(gh run *)" --output-format stream-json --include-partial-messages` run in the project root with the `CLAUDE*` environment cleared (as `server.py` does); the first turn omits `--resume` and records the new id. Turns are serialized per project; the page streams the text. Triggers for a turn: a chat message, an idea, a backlog sweep request, an L2 reaching `done` ("report landed for `<slug>`; verified: …"), a reconcile finding, and the morning digest. No `Edit`/`Write`: L3 thinks, reads, asks, and calls `alt`. It may use in-process subagents for *reading* only.

Persona essentials (generated from `ROLES.md`, the source of truth): the Decision rule and its worked examples; **every answer given on an L2's behalf cites the controlling decision or doc section, and if the docs are ambiguous or conflict, the question escalates**; for the first 20 tasks every such answer is also an FYI quoting it; digests lead with Decisions and are ≤8 sentences; status is always read from `status.json`, never remembered; the standard loop and size classes.

**Context:** the server reads `usage` from every L3 turn's stream-json and shows it on the page; at the warn threshold it finishes the queued turn and then **rotates** — starts a fresh session whose first turn is "read `STATE.md` and continue" — which is cheap and deterministic because `STATE.md` is the memory. Compaction (with a "Compact Instructions" block and the `SessionStart(compact)` re-injection hook) is the safety net only. L2/L1 sessions are watched through the global statusline command, which writes `context_window.used_percentage` and the 5h/7d quota percentages per session to `~/.altitude/monitor/<session>.json` for the page.

**Context lines (decision 12, settled).** Per engine in `config.CONTEXT_LINES`: Claude warn 25% / act 30% of a 200k window, Codex native compaction at its ~256k limit (warn 80%). Every Claude process Altitude launches gets `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=30` (percent used; honoured by `-p` and `--bg` sessions and inherited by the L1s an L2 spawns), so compaction is the in-turn backstop; between turns the L3 rotates to a fresh session from its checkpoint at the act line. The monitor shows `context_state` (ok/warn/act) per session against its engine's lines.

## 4. Research → proposal → critique

*Build note (2026-08-29):* the server runs the proposal agent automatically for every requested M/L task and gives L3 a `proposal-ready` turn to apply the Decision rule (card, FYI-only, auto-approve for S, or park); L3 never calls the proposal agent itself. FYI-class M proposals (no question, no always-list hit) are approved by the server with an FYI (decision 13).

A **fresh** headless session per request: `claude -p --output-format json --json-schema ~/.altitude/schemas/proposal.json --append-system-prompt-file ~/.altitude/personas/proposal.md --permission-mode plan` in the repo (or `codex exec --output-schema` when Claude quota is tight). Schema: `summary_for_burak` (≤120 words), `options[]` (what it buys, what it costs, when the other wins), `recommendation`, `decisions_needed[]` (one-line question + default each), `always_list_classes_touched[]`, `affected_areas`, `estimate` (tokens, wall-clock), `risks`, `open_questions`, `size_class`. For **L-class** tasks a critic runs on the other engine before Burak sees anything (rubric in `ROLES.md` → V); for M-class, only when L3 asks for it; never for S.

## 5. Dispatch, report contract, done signal

Brief = the career-platform template (goal, definition of done, change class, never-list reminder, token budget, "questions at proposal time only — mid-run take the default and record a deviation", "open the PR and stop" for always-list classes). Dispatch (shape verified by hand): `claude --bg --name <project>/<slug>-<n> -w <slug> --append-system-prompt-file ~/.altitude/personas/l2.md --permission-mode auto --max-turns N "$(cat brief.md)"` — the brief is the positional argument (`--bg` conflicts with `-p`); the worktree is `<repo>/.claude/worktrees/<slug>`; the L2's `sessionId` and short `id` come from `claude agents --json`. The project's own `CLAUDE.md`/`ENGINEERING.md` bind the L2 unchanged. `altd` records the L2's `id`/`sessionId` from `claude agents --json` and polls that list every ~30 s; when the task's session shows `state: done` (or vanishes), it runs `verify-report`, marks the task `reported` (or `blocked` if the report is missing or contradicted), and gives L3 a turn. A `Stop` hook of type `http` posting to `altd` is an optional accelerator, not a dependency. An L2 that sits **idle without a report for 150 s** is treated as waiting for input (a permission prompt or a question): the task is blocked with the `claude attach <id>` command in the reason and the card's *Resume* sends Burak's note into the session.

**Report contract** (`report.md` + `report.json`, written by the L2 as its last action into the task folder *and* into `.claude/progress/`): *Landed* (PRs, merge SHAs, `main` run ids, deploy status) · *Review* (findings addressed / dismissed with reason) · *Deviations from brief* · *Decisions* (≤3, one question + default each) · *FYI* · *Blocked* · *Follow-ups* · *Spend*. `verify-report.sh` checks every claim against `gh pr view`, `gh run list`, `git log origin/main` before L3 reads it; a mismatch is a quality signal and an FYI.

**The L2 owns the task to done** (`ROLES.md`): independent review before merge for M/L (`codex review` or a fresh Claude review agent — never the author; S-class relies on CI plus the existing self-merge rules), findings triaged and either fixed or dismissed with a reason, PRs merged where the class allows, the `main` run watched, the deployment checked, pipeline/deploy failures fixed (L1 for real fixes, minor fix for trivial ones, never by weakening CI), revert/rollback per project rules if a fix fails once. The report is written only when all of that is true, and `verify-report.sh` checks the claims — *Landed* must show merge SHAs and green run ids, and a deploy status where applicable. L2s never wait on a human: if blocked on an always-list item they checkpoint and exit; `altd` resumes them with the answer (`claude --bg --name <same> --resume <sessionId> '<answer>'`, or a new `-p` turn) once Burak has tapped. Waiting on CI (`gh run watch`) is normal L2 work.

**Parallelism contract (decision 39).** Isolation: L2 worktree per task (`.claude/worktrees/<slug>`, branch `worktree-<slug>`, from committed `main`), nested L1 worktrees, PR-only integration, `cleanup_after_done` prunes merged worktrees. Scheduling guards in `dispatch.wip_hold`, in this order: rule-application serialization → **file lease** (`task.paths` or `proposal.files`; overlap = equal or directory prefix; hold reason names the other task and paths) → WIP per project/machine → **session ceiling** from `claude agents` → quota reserve. The brief carries the task's lease and the other running leases. Shared services are protected in every session by `hooks/guard.py` (PreToolUse on Bash: unit restarts, ufw/wg, service ports, Altitude home, force-push) and by `ALTITUDE_TIMERS=0` serve-only instances for smoke tests. What is *not* prevented: two tasks with undeclared paths editing the same file — that surfaces as a PR conflict the L2 resolves in its worktree.

## 6. The web app (decision 29) — Decisions, FYIs, the cross-project queue

Served by `altd` on the WireGuard address next to the pocketbook, one static page plus JSON endpoints, phone-first layout. Views:

- **Inbox** — the cross-project Decision queue: each card is one question with options, the two facts that matter, and Approve / Reject / Revise / Park buttons; ranked blocked-first, then class L→M, then age. Below it the FYI tail per project, collapsible. WIP limits (default 3 running L2s per project, 8 per machine) are applied here: an approved task waits in `approved` until a slot frees, visibly.
- **Projects** — every folder under the configured roots (default `~/Projects/*`) is listed; an unmanaged one has a *Start L3* button that registers it (`projects.json`: path, stacks, approval policy) and creates its L3 session; a managed one shows its L3 (session, context %, last turn) and its running L2s at a glance.
- **Project** — the L3 for this project on top, then tasks by state with class and age; each task card shows the L2's live status (`busy/idle/done` from `claude agents --json`), its worktree/branch, PR links, context % and spend, the latest progress-file excerpt, and buttons: open report, message the L2 (mirrored into `qa.md`), attach command to copy.
- **Chat** — the L3 conversation, streamed; the same box takes ideas (`/idea …`) and backlog sweeps (`/backlog`).
- **Monitor** — every session's context % and the seat's 5h/7d quota, with warn/act colouring; rotation events.
- **Listen** — digests as Kokoro-rendered episodes, reusing the pocketbook player; the morning digest is generated by a timer and pushed as a notification line.

No auth (holding a tunnel key is the auth), no TLS (the tunnel is the encryption) — the pocketbook's posture. The queue, WIP accounting, and digest text are deterministic (no model); only the chat and the digest *prose* involve L3.

**Stack (decision 37).** From 0.2 the web app is a Vite/React 19/TypeScript SPA (`web/`), react-router 8 + TanStack Query 5, Tailwind 4 with `web/design/tokens.css`, built with pnpm into `web/dist`, which `altd` serves statically; the JSON API and NDJSON chat stream above are unchanged and are the contract between the two. The 0.1 vanilla `index.html/app.js/style.css` is the reference for feature parity until the SPA lands.

## 6a. Intake — ideas and the backlog (decisions 27–28)

`alt idea <project> "<text>"` writes `ideas/<timestamp>.md` and tells L3; L3 evaluates it against the project's docs and either creates a task (`alt task new … --class S|M|L --from idea`) with a one-line reason, or parks it with a reason — both are FYIs, unless the idea is itself Decision-class. `alt backlog <project>` runs `gh issue list --state open --json number,title,labels,body` into `backlog/candidates.md`; L3 triages each into S/M/L (or "not now"), groups by dependency, and proposes a batch sized to the free WIP slots as one Decision ("dispatch #126, #128, #130 as S; #127 as M?"). The tool itself is a registered project, so ideas about `alt` take the same path.

## 7. Validation tiering (decision 13, revised)

Always: deterministic report verification (`verify-report.sh`). M and L: independent PR review before merge. L only (M on request): proposal critique on the other engine. Off for S. Each validator is a fresh session with a rubric and a findings-with-severity output; blocking findings return to the author's level.

## 8. Self-improvement — incidents → rules/skills, with provenance (v1, decisions 10/14/30)

This is the loop Burak cares most about: when something *clearly* goes wrong, the system notices **without being told**, records why concisely, learns the right-sized lesson, and says so.

**Detection — L3's post-mortem pass.** Every "report landed" turn (§3) ends with a fixed section in the turn template: *did anything clearly go wrong?* The server pre-computes the signals so L3 does not have to hunt: `verify-report` contradictions; non-empty *Deviations* or *Blocked* sections; a revert; a failed CI retry; an L2 hitting `--max-turns` or the context act threshold; a task past 2× its estimate in time or spend, or an envelope cap hit; the same question asked by two L2s; a reviewer finding whose tag was seen before in this project; and Burak's corrections in chat ("that should have been an FYI", "why did it do X again"). Any one of these opens an incident; none of them requires Burak.

**Incident file — short, auditable, not loaded into sessions.** `docs/incidents/I-###.md` in the project repo (next to `docs/RULES.md`): what happened (≤3 lines), evidence (events.log lines, PR, run id, report excerpt), root cause (one line), *generalizable?*, mechanism chosen, ids of the rule/skill it produced, status. Incidents are read only by the audit and by a human asking "why do we have this rule?" — never injected into L1/L2/L3 contexts.

**Right-sized lesson — the mechanism choice, in this order.**
1. *Would the cause recur across unrelated tasks?* → one **generalized rule** in `CLAUDE.md` (a constraint, one or two lines, tagged `[R-###]`). Generalize as far as the evidence supports and no further.
2. *Does it recur in one workflow step* (e.g. the same git-commit failure pattern, the same deploy check missed)? → a **specific instruction** in the section or skill that owns that step, not a global rule.
3. *Is the fix a procedure* (several steps, tooling, a checklist)? → a **skill** (`.claude/skills/<name>/SKILL.md`), referenced from the step that needs it.
4. *One-off?* → incident only, `status: watch`; a second incident with the same root cause links the first and promotes it to 1–3.
Every rule/skill entry in `docs/RULES.md` carries the five fields of decision 14 (where, origin = incident id + evidence, failure prevented, expected effect + how to verify, status `probation → active → stale/retired`). Rule text has a length cap; a rule that needs a paragraph is a skill.

**Scope — where a lesson lives (decision 32).** Each rule/skill has a scope, chosen with one question: *what does the cause depend on?*
- the repo's own code, decisions, or product → **project** scope: the repo's `CLAUDE.md` / `.claude/skills/` / `docs/RULES.md`, as above;
- the tech stack (language, framework, cloud, CI system — e.g. "CDK diffs against the wrong account", "pytest fixtures in `conftest.py` shadow…") → **stack** scope: `rules/stacks/<stack>/` in this repo (RULES.md + snippets); a project declares its stacks in `~/.altitude/projects.json` and the launcher appends the matching active snippets to every brief and persona for that project;
- how agents work at all (altitude, verification per artifact, spend envelope, git hygiene, report contract) → **global** scope: `rules/global/RULES.md` in this repo, compiled into the `## Rules` section of the L1/L2/L3 personas, so every project gets it on the next dispatch.
Default is **project**; promotion needs evidence, not speculation: when the post-mortem finds a root-cause tag that already exists in another project's incident (the server keeps a cross-project index `~/.altitude/incidents.jsonl` of `{project, id, tags, scope, rule}`), it proposes *promote to stack/global*, linking both incidents. The weekly audit also **demotes**: a global or stack rule whose incidents all come from one project goes back to that project. Burak's own `~/.claude/CLAUDE.md` is never written by the system — it is his voice; global learnings travel through the personas instead, and a conflict between the two is reported as a Decision.

**Ownership.** Global and stack scopes belong to the tool's own project (decision 28: this repo is a registered project with its own L3). A project L3 that wants a promotion calls `alt rule promote I-### --scope stack:<name>|global`, which files an S-task on the tool's project; that L3 applies it through the same incident → ledger → PR → FYI-with-veto path. Nothing is global until it has been merged here.

**Applying it.** L3 never edits the repo (ROLES), so applying is an **S-class task** the server creates automatically: an L2 (cheap model) writes the incident file, the ledger entry, and the rule/skill change, opens the PR and merges it. Non-guardrail changes are **FYI-with-veto** (decision 10, settled): the FYI reads *"I-017: `<what went wrong>` → added R-023 to CLAUDE.md `<section>`; veto = revert PR #n"*. Guardrail/never-list changes, deleting a hand-written rule or skill, and anything touching the always-list stay Decisions.

**Audit.** A weekly timer gives L3 an "audit" turn over `docs/RULES.md`: for each `probation`/`active` rule — did its incident class recur (then the rule failed: tighten or turn into a skill), was it exercised, is its origin still true (then `stale` → `retired` with a reason, as an FYI). `observations.md` (repeated questions, inconsistent L2 behaviour, quiet inefficiencies that are not incidents) feeds the same turn. Retirement is also an FYI-with-veto; the incident file stays.

**System faults (decision 36).** Altitude's own failures are incidents too, filed against the `altitude` project by `improve.system_fault(kind, detail, project=, task=)`: an `I-###` with tags `system-fault, <kind>`, an inbox line prefixed `SYSTEM FAULT`, and a per-kind record in `monitor/faults.json` (first/last/count/incident; one incident per kind per 24 h). Callers: the workflow thread wrapper (`spawn`), `tick`, the critic when Codex yields no critique (`verdict: unavailable` → the L3 parks the task until fixed), the verifier when `gh` fails (`verdict: fault` → task blocked as *verifier fault*, not judged), `cleanup_after_done` git errors, TTS, the quota monitor when no statusline snapshot exists (reserve line unenforced), and hooks (which cannot reach the server and append to `monitor/hook-faults.log`, drained each tick). Removed fallbacks: no Codex→Claude critic, no empty agent list on `claude agents` failure (it raises), no loopback bind (exit 1, systemd retries after 20 s), no defaulting on corrupt JSON (`read_json` raises; only a missing file yields the default).

## 9. Enforcement of the altitude contract

L3: no write tools. L2: a `PostToolUse` hook on `Edit|Write|MultiEdit` counts edited files/lines per session and writes an FYI when the "minor fix" limits are exceeded; `--max-turns` and the token budget in the brief bound the run.

**Spend envelope (decision 31, `ROLES.md`).** The brief's envelope is enforced by three mechanisms, none of which rely on the model's restraint: (a) the launcher passes `--max-turns` from the class table and writes the envelope into `status.json`; (b) a `PreToolUse` hook on the subagent tool (`Agent|Task`, per-session counter in `~/.altitude/monitor/<session>.json`) returns a block past the launch cap with the message *"envelope reached — checkpoint and report Blocked: envelope"*; (c) `altd` computes the seat's 5h/7d quota from the statusline files and holds all new dispatches and L1 fan-out advice while the 5-hour window is past the **reserve line** (default 70%; Burak's own interactive sessions always keep the remainder), showing the hold on the Monitor view. Spend per task (`usage` from stream-json, turns, subagent launches, retries) is recorded in `status.json` and reported in the *Spend* section against the estimate; > 2× estimate or a cap hit is a post-mortem signal (§8). `--max-budget-usd` is passed as a belt-and-braces cap if it turns out to count on subscription seats (verify in the build session). L1: paths in the brief, checked by the reviewer. The context/quota monitor (§3) covers every session because the statusline command is global.

## 10. Phase 0 — build list, scorecard, kill criterion

Build, in order (stack and layout: decisions 25–26): (1) `personas/{l3,l2,l1,proposal,critic,reviewer}.md` generated from `ROLES.md`; (2) `schemas/{proposal,report}.json`; (3) `altd` — the server (state library + `task` operations, L3 turn runner, L2 poller, verify-report, reconcile and digest timers, monitor endpoint) — with `bin/alt` as the thin localhost client L3 and shell use; the statusline monitor wrapper; (3b) the web app's five views; (3c) the envelope: class table in the launcher, the subagent-count `PreToolUse` hook, the quota reserve line in the dispatcher, spend fields in `status.json`/the report; (3d) the post-mortem section of the report-landed turn template, `alt incident|rule|rule promote` operations, the auto-created "apply rule" S-task, the cross-project incident index, `rules/global` compiled into personas and `rules/stacks/<stack>` appended per project, and the weekly audit timer (with cross-project promotion/demotion candidates); (4) **a gold set**: the Decision/FYI items from career-platform's nine existing orchestrator reports, hand-labeled by Burak in one sitting, used as few-shot examples in `l3.md` and as the persona's regression test; (5) start L3 on career-platform and run real tasks through it. Per task, one scorecard line: *escalated / answered-from-docs (cited) / should-have-escalated / misclassified-FYI*. **Kill criterion:** after 20 tasks, if ≥10% of L3's answers were wrong or Burak's items-per-task did not fall, stop, keep the L2 brief rules (which are valuable alone), and drop L3. Budget unit: tokens per brief; the seat's 5h/7d percentages for quota. Policy check (`LANDSCAPE.md`) before step 5.

## Honest risks

1. **The persona is the product.** Burak's corrections are written back into `ROLES.md` examples and the gold set; the scorecard is how we know it is working.
2. **Summaries of summaries.** Every digest links its artifact; "why" is answered by reading it; `verify-report.sh` keeps the chain honest.
3. **One seat, many sessions.** Validation is tiered by class; WIP limits and the quota monitor pause dispatch before the rate limit; Codex takes overflow.
4. **Moving primitives.** `claude --bg`/`claude agents --json` are new; they sit at the edges (dispatch, done-signal), the core is files, the server, and personas — and the L3 mechanism is the one the pocketbook has run for weeks.
5. **Subscription policy.** Settle before phase 0 step 5 (`LANDSCAPE.md`).

---

## Appendix A — deferred design (phase 2+, build only when a metric asks)

Kept from the first draft so the ideas are not lost; none of it is v1.

- **L2 auto-rotation** at the act threshold (handoff from the progress file into a new `claude --bg` on the same worktree) and quality heuristics; v1 only warns and lets L3/Burak decide.
- **Remote Control as a first-class surface** (L3 interactive, `AskUserQuestion` approvals) — available today as a secondary path; polish only if the web app proves insufficient on the go.
- **Telegram channel plugin** as a convenience surface.
- **Engine adapter** (`start/alive/logs/send/collect`) with a Codex implementation so Codex can be an L2, not just a validator/alternate L1; `codex app-server` approval RPCs forwarded to the phone.
- **Rule fitness tallies**: helpful/harmful counts per rule (claude-evolve/ACE), skill-invocation counting from transcripts, auto-retirement PRs — v1's weekly audit is judgment over incidents, not statistics.
- **Quality heuristics** in the monitor: repeated tool errors, edit–test loops, spend past 2× estimate, deviation counts → instruct, rotate, escalate.
- **`/insights` and transcript mining** as extra inputs to the weekly audit.
