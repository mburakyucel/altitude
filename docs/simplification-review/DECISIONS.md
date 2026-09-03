# Simplification decisions — 2026-09-02

Burak recorded these decisions in conversation on 2026-09-02, after a read-only review of `main`
(`a4ca71c`), the five draft PRs from the earlier autonomous simplification attempt, and the
incident history. They are the paradigm the module-by-module work implements. The facts they were
based on remain in [CURRENT_SYSTEM.md](CURRENT_SYSTEM.md), [FLOWS.md](FLOWS.md), and
[CANDIDATE_WORK.md](CANDIDATE_WORK.md).

Stated goal: **elegant, simple, modular, human-readable, robust.**

## What the review found

- The 08-31 cutover (PR #115) is the only point in the history where the code got smaller. Since
  then about 3,000 Python and 2,600 test lines grew back, almost all defensive machinery.
- The dominant pattern: Altitude re-proves facts it just established (cleanup re-verifies the merge
  `land.py` performed; `hooks/guard.py` re-implements the Git hooks `git_policy.py` installs;
  transcript bundles are rewritten on every event; the resume path re-reads its own snapshot four
  times because it kills its own worker on every message).
- Draft PRs #138, #139, #140, #141, #142 add roughly 10,600 production lines and delete no file
  from `main`; #138 and #140 cannot run. They were closed on 2026-09-02 with the evidence in their
  closing comments. The only extracted change is the parent-directory fsync in `atomic_write`.
  Nothing else is rebuilt from a `simplify/*` branch.

## Decisions

| # | Decision | Detail |
| --- | --- | --- |
| 1 | **L3 creates the task and it starts immediately.** | L3 does not scope or research. L2 reads the repository, sizes the work, and when it is large or ambiguous its first output is a short proposal on which it blocks for Burak's go. A proposal is an L2 that blocks with a plan before writing code; no separate mechanism. |
| 2 | **Messages queue and are read at the worker's next checkpoint**, exactly like Claude Code's queued input. | No kill-and-resume. For Claude a hook feeds pending messages at the next turn boundary; for Codex the message is folded in when the current `codex exec` turn ends or on resume. An explicit Stop remains for aborting a worker. |
| 3 | **Merge by default.** | L2 merges when checks are green. It holds the PR for Burak only for major UX changes, cost-accruing infrastructure (for example CDK), identity, or security. A hold report describes the strategy and the decisions made, not the code. |
| 4 | **Faults are project-scoped, two-tier.** | Tier one, code: transient failures are retried; a quota stop parks the task until the window reopens or starts a fresh attempt on the other engine from saved progress; each writes one task event. Tier two, L3: anything left after that (retries exhausted, git mismatch, unaccounted worker). The fault blocks only its own task and records an incident; the project's L3 receives it as one queued message, records the learning, and either fixes it directly if trivial or creates one ordinary task. An incident raised by a repair task does not wake L3 again; it goes to the Inbox. No global fuse, no clear command, no claim slots or backoff. A merged Altitude change marks restart pending; when no L2 is running the UI shows a banner with a restart button (auto-restart when idle may follow once proven). |
| 5 | **Helpers are engine-native.** | The L2 uses its engine's own subagents (Claude Code Agent tool, Codex equivalent). Altitude does not track L1s or reviewers. Customization of helper paradigms lives in engine-native files (agent definitions, skills, hooks), not in Altitude. The L2 persona must state the delegation and context-hygiene expectations briefly, and keep a small `progress.md` (goal, done, next, how to verify) refreshed at milestones, never as a log. Auto-compact for Claude L2s stays as the explicit `autoCompactWindow` Altitude already passes. |
| 6 | **One execution contract for both engines.** | Every worker is an untrusted process in its worktree. The only door into Altitude is the `alt` CLI, validated by the backend against the task record under the project lock. Codex keeps its native sandbox as containment but uses the same door. The inert-action broker, action schemas, Codex-specific personas, the cgroup unit dance, and the shell-inspecting guard hook go. If part of the contract fights an engine's native operating model, the contract adapts; a second contract is not built. Quota routing stays because two subscriptions is the reason for two engines. |

## Working rules for every module PR

1. One module, one branch, one PR, cut from `origin/main` in a worktree. Never from a `simplify/*`
   branch. The module's tests, docs, persona text, and schema changes travel in the same PR.
2. Deletion first. A PR reduces production lines, or is a bug fix under fifty lines. No foundations,
   no dormant modules, no compatibility readers, no "temporary" code.
3. Every defensive check that is added names the incident it prevents in the PR body. A check that
   defends against Altitude's own design is removed with the design.
4. The PR body answers the six review questions from [README.md](README.md) in at most fifteen
   lines: behavior provided, dependents, still wanted, fewer owners, what is removed, what proves
   parity.
5. Full Python and web suites on every PR. After any change to dispatch, engines, or landing, one
   real tiny task end to end: chat, task, PR, checks, merge, archive.
6. Burak decides the six paradigm questions above and any new one of the same altitude; module-level
   calls are made by the implementing session and reported in the PR. Ask Burak only for a decision
   not recorded here.
7. The service stays stopped until decision 6 has landed and the end-to-end task passes. Restart is
   a separately authorized step.

## Phase order

| Phase | Module | Becomes | Gate |
| --- | --- | --- | --- |
| 0 | Closed PRs, `atomic_write` fsync, this record | done 2026-09-02 | — |
| 1a | `hooks/guard.py` (1,003) | deny-list of about fifty lines: service lifecycle, reserved ports, `ALTITUDE_HOME` removal | none |
| 1b | `transcript.py`, `state.py` per-event rewrite | live view over the provider's own JSONL; no bundles | none |
| 1c | `dispatch.cleanup_after_done` family (~460) | ancestor-of-`origin/main` and no live worker, then remove worktree and branch | none |
| 1d | `github_intake.py` (231) | fetch the issue once at task creation, inline into the request | none |
| 1e | `digest.py` TTS, incident id allocator, routing snapshot | drop, timestamp id, reason string. The four resume fields (`resume_after`, `resume_answer`, `resume_prefix`, `resume_exact_prompt`) move to phase 3: decision 2 replaces kill-and-resume steering, so consolidating them first would be done twice | none |
| 2 | `recovery.py`, `incidents.py`, `server.tick` | done 2026-09-03: fuse deleted; a fault blocks its task, files an incident, and queues one L3 message; restart banner and button in the following PR (#154 removed the fuse). A quota stop parks a pinned task; the fresh attempt on the other engine arrived with the Codex L2 door (phase 5) | decision 4 |
| 3 | `dispatch.py` steering, `tasks.py` identity, `server.on_l2_finished` | done 2026-09-03: messages queue in the task inbox and reach a Claude L2 through `hooks/inbox.py` (after a tool call or before it stops), a Codex L2 at its next turn; `dispatch.resume` is the one relaunch path and `dispatch.stop` the explicit abort; identity is slug + attempt + provider session (`dispatch_id`, `l2_token`, the resume-answer fields, and the pending-action claim protocol deleted) | decision 2 |
| 4 | `l1.py`, `schemas/review.json`, `personas/l1.md`, `personas/reviewer.md`, helper status | done 2026-09-03: `l1.py`, `schemas/review.json`, the L1 and reviewer personas, `alt l1`, the `request_helpers` action, the `l1_runs` status/monitor fields, and `route.pick_engine(other_than=)` deleted; `personas/l2.md` and `personas/l2_codex.md` carry brief delegation and context-hygiene guidance and ask for a small `progress.md` (goal, done, next, how to verify) | decision 5 |
| 5 | `engines.py`, `actions.py`, `l3_actions.py`, `l3.py`, action schemas, Codex personas | done 2026-09-03 in three PRs: #158 deleted the shell-inspecting guard hook; #159 put the Codex L2 on the `alt` door (`actions.py`, `schemas/l2_action.json`, `personas/l2_codex.md`, the L2 permission profile, containment proof, and environment allowlist deleted; `bin/alt` fences an L2 to its task and attempt; a window stop starts a fresh attempt on the other engine from `progress.md`); the third PR put the L3 on the same door (`l3_actions.py`, `schemas/l3_action.json`, `personas/l3_codex.md`, `codex_isolation_config`, the sandbox preflight, and the unit-emptiness proof deleted; the GitHub-issue two-phase approval went with the broker, so a Codex L3 uses `gh` like a Claude L3). Codex keeps its native workspace-write sandbox inside a transient user unit | decision 6 |
| 6 | `land.py`, `server.py`, `bin/alt`, `verify.py`, `git_policy.py`, `config.py` | done 2026-09-03: kept; dead branches removed (`land.py` undeclared-lease and not-prefetched paths, `git_policy` compatibility aliases and `head=` parameters, `config` unread constants, the `/api/install-statusline` route); the `alt project` and `alt state` parsers that phase 4 dropped by accident are back with a test; `verify.py` needed nothing | after 3 |
| 7 | `web/src` | done 2026-09-03: the Project page's direct task creation and the server's `new`/`verify` task actions deleted, so Chat is the only web intake; the task page is one header with Conversation and Live session tabs; the transcript reads every turn of the Codex thread from the job root (sources come from the task record, not event fields) and classifies Codex messages and file changes | after 3 |
| 8 | `tests/`, `docs/` | tests done 2026-09-03: `tests/support.py` is the one fixture (throwaway runtime home per process, a registered project per case, shared git and `gh` fakes, quiet engines); the 44 per-module isolation shims, the thread races and their machinery, and the duplicate cases are gone. Docs follow in the next PR: rewrite `ARCHITECTURE.md` and `SESSION_LIFECYCLE.md` to the final shape; retire this directory to a one-page record | continuous |

Target shape: Python near half its current size, hooks under a hundred lines, tests proportional to
product behavior. A task is a directory a person can read.
