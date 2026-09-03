# Simplification record — 2026-09-02 to 2026-09-03

Burak reviewed `main` (`a4ca71c`) on 2026-09-02, closed the five draft PRs of the earlier autonomous
simplification attempt (#138–#142, evidence in their closing comments; the only extract was the
parent-directory fsync in `atomic_write`, #146), and recorded six paradigm decisions. The rebuild then
ran module by module from `main` in eight phases and finished on 2026-09-03. Stated goal:
**elegant, simple, modular, human-readable, robust.**

What the review found: the 08-31 cutover (`97e1197`, PR #115) was the only point where the code had
got smaller; about 3,000 Python and 2,600 test lines of defensive machinery had grown back since,
most of it re-proving facts Altitude had just established (cleanup re-verifying the merge that
`land.py` performed, a guard hook re-implementing the Git hooks `git_policy.py` installs, transcript
bundles rewritten on every event, a resume path that killed its own worker on every message).

## Decisions

| # | Decision | Detail |
| --- | --- | --- |
| 1 | **L3 creates the task and it starts immediately.** | L3 does not scope or research. L2 reads the repository, sizes the work, and when it is large or ambiguous its first output is a short proposal on which it blocks for Burak's go. A proposal is an L2 that blocks with a plan before writing code; no separate mechanism. |
| 2 | **Messages queue and are read at the worker's next checkpoint**, exactly like Claude Code's queued input. | No kill-and-resume. For Claude a hook feeds pending messages at the next turn boundary; for Codex the message is folded in when the current `codex exec` turn ends or on resume. An explicit Stop remains for aborting a worker. |
| 3 | **Merge by default.** | L2 merges when checks are green. It holds the PR for Burak only for major UX changes, cost-accruing infrastructure (for example CDK), identity, or security. A hold report describes the strategy and the decisions made, not the code. |
| 4 | **Faults are project-scoped, two-tier.** | Tier one, code: transient failures are retried; a quota stop parks the task until the window reopens or starts a fresh attempt on the other engine from saved progress; each writes one task event. Tier two, L3: anything left after that (retries exhausted, git mismatch, unaccounted worker). The fault blocks only its own task and records an incident; the project's L3 receives it as one queued message, records the learning, and either fixes it directly if trivial or creates one ordinary task. An incident raised by a repair task does not wake L3 again; it goes to the Inbox. No global fuse, no clear command, no claim slots or backoff. A merged Altitude change marks restart pending; when no L2 is running the UI shows a banner with a restart button (auto-restart when idle may follow once proven). |
| 5 | **Helpers are engine-native.** | The L2 uses its engine's own subagents (Claude Code Agent tool, Codex equivalent). Altitude does not track L1s or reviewers. Customization of helper paradigms lives in engine-native files (agent definitions, skills, hooks), not in Altitude. The L2 persona must state the delegation and context-hygiene expectations briefly, and keep a small `progress.md` (goal, done, next, how to verify) refreshed at milestones, never as a log. Auto-compact for Claude L2s stays as the explicit `autoCompactWindow` Altitude already passes. |
| 6 | **One execution contract for both engines.** | Every worker is an untrusted process in its worktree. The only door into Altitude is the `alt` CLI, validated by the backend against the task record under the project lock. Codex keeps its native sandbox as containment but uses the same door. The inert-action broker, action schemas, Codex-specific personas, the cgroup unit dance, and the shell-inspecting guard hook go. If part of the contract fights an engine's native operating model, the contract adapts; a second contract is not built. Quota routing stays because two subscriptions is the reason for two engines. |

Burak decides questions of this altitude; module-level calls are made by the implementing session and
reported in the PR. Ask him only for a decision not recorded here.

## Working rules that still apply to every PR

1. Deletion first. A PR reduces production lines, or is a bug fix under fifty lines. No foundations,
   no dormant modules, no compatibility readers, no "temporary" code. A decision-mandated feature is
   the stated exception.
2. Every defensive check that is added names the incident it prevents in the PR body. A check that
   defends against Altitude's own design is removed with the design.
3. The PR body answers, in at most fifteen lines: what user-visible or safety behaviour the module
   provides; which callers, records, external effects, and tests depend on it; whether the behaviour
   is still wanted; whether it can be expressed with fewer owners, states, artifacts, or compatibility
   paths; what is removed; and what test or end-to-end observation proves parity. "Simpler" is not
   evidence on its own, and an existing test is not evidence that a mechanism is still wanted.
4. Full Python and web suites on every PR. After any change to dispatch, engines, or landing, one real
   tiny task end to end: chat, task, PR, checks, merge, archive.

## What each phase deleted

| Phase | PRs | Removed | Kept or added |
| --- | --- | --- | --- |
| 0 | #145, #146 | the five `simplify/*` drafts | this record; `atomic_write` fsync |
| 1a | #147 | `hooks/guard.py` (1,003 lines) | a fifty-line deny-list, deleted again in 5a |
| 1b | #148 | transcript bundles and the per-event rewrite in `state.py` | live view over the provider's own JSONL |
| 1c | #149 | the `cleanup_after_done` family (~460 lines) | one rule: ancestor of `origin/main` and no live worker |
| 1d | #150 | `github_intake.py` | the issue fetched once at task creation and inlined |
| 1e | #151, #152, #153 | digest TTS, the incident id allocator, the routing snapshot | timestamp incident ids, a routing reason string |
| 2 | #154, #155 | `recovery.py`, the fuse, launch permits, hold/clear actions | `incidents.system_fault` blocks its task, files one incident per kind per day, queues one L3 message; restart banner and button |
| 3 | #156 | kill-and-resume steering, `dispatch_id`, `l2_token`, the resume-answer fields, the pending-action claim protocol | per-task `inbox.jsonl` and `conversation.jsonl`, `hooks/inbox.py`, `dispatch.resume` as the one relaunch path, identity = slug + attempt + provider session |
| 4 | #157 | `l1.py`, `schemas/review.json`, the L1 and reviewer personas, `alt l1`, `request_helpers`, `l1_runs` | delegation and context-hygiene guidance and `progress.md` in the L2 persona |
| 5 | #158, #159, #160 | the guard hook, `actions.py`, `l3_actions.py`, the action schemas, the Codex personas, `codex_isolation_config`, the sandbox preflight, the unit-emptiness proof, the GitHub-issue two-phase approval | `codex_exec` as a plain sandboxed turn; `bin/alt` fences an L2 to its task and attempt; a window stop starts a fresh attempt on the other engine |
| 6 | #161 | dead branches in `land.py`, `git_policy.py`, `config.py`, `server.py` | the `alt project` and `alt state` parsers, restored with a test |
| 7 | #162 | the Project page's direct task creation, the `new` and `verify` task actions | Conversation and Live session tabs; the transcript reads every turn of the Codex thread |
| 8 | #163, #164 | 44 per-module isolation shims, the race families and their machinery, the review directory | `tests/support.py` as the one fixture; `ARCHITECTURE.md` and `SESSION_LIFECYCLE.md` in their final shape; this page |

| Lines | Before (`97e1197`) | After phase 8 |
| --- | --- | --- |
| Production (`altitude/`, `bin/`, `hooks/`) | 10,427 | 6,125 |
| Tests (`tests/`) | 10,258 | 5,608 |

Operator subcommands with no caller (`alt task paths|brief|list`, `fyi`, `decisions`, `digest`,
`monitor`, `dispatch`, `verify`, `poll`, `chat`, `l3-reset`, `incident list`) are kept until Burak
decides. Auto-restart when idle stays a possible follow-up to decision 4.
