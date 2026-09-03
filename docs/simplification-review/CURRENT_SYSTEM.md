# Current system at `main` (`97e1197`)

This document describes executable source on `main`. It deliberately excludes the unmerged
`simplify/*` implementation branches.

## High-level architecture

```text
Burak
  |
  +-- project chat ----------> HTTP server ----------> L3 provider turn
  |                                 |                      +-- Claude: scoped direct commands
  |                                 |                      |
  |                                 |                      +-- Codex: inert structured action
  |                                 |                                      |
  |                                 |                                      v
  |                                 |                                trusted L3 broker
  |                                 |
  |                                 +-- task page ---------> durable task conversation
  |                                                               |
  |                                                               v
  |                                                        current L2 attempt
  |                                                        in one worktree
  |                                                               |
  |                                   +---------------------------+------------------+
  |                                   |                           |                  |
  |                              subagents                 outcome/action      PR landing
  |                                   |                           |                  |
  |                                   +-------- findings/patch --->+-----------> checks/merge
  |
  +-- operator commands ------> `bin/alt` / restart script / trusted backend

fault evidence -> blocked task + incident index -> one queued L3 message
```

The system has one persistent Python server (`altitude.server`) plus provider, Git/GitHub, and
short-lived child processes. The React app is a client of the Python JSON/streaming
endpoints; it does not own durable state transitions.

## Runtime ownership and storage

[`altitude/state.py`](../../altitude/state.py) owns basic JSON/file durability, the per-project
cross-process lock, task/event reads, and `STATE.md` regeneration. Task status is stored under
`ALTITUDE_HOME`; task conversation and events are append-only JSONL files beside each task record.

[`altitude/tasks.py`](../../altitude/tasks.py) owns the explicit task lifecycle:

```text
queued   -> running | rejected
running  -> reported | blocked | rejected | done
blocked  -> running | reported | rejected
reported -> done | running | blocked | rejected
```

Task message/command paths apply different identity subsets: Burak's message may carry a displayed
dispatch/session/engine; L2 reply, completion, and landing use specific dispatch,
capability, Git, or lease fences, while other commands apply different subsets. The module also
stores FYIs and decisions, applies merge holds, and performs archival. It is not the only writer of
a TaskRecord: `tasks.py`, `dispatch.py`, `actions.py`, `l3_actions.py`, `server.py`, and `bin/alt`
contain direct task-record writes on this baseline.

The current logical/physical identity split is:

| Field | Current meaning |
| --- | --- |
| `dispatch_id` | one L2 attempt for a task |
| `l2_engine` | Claude or Codex selected for that attempt |
| `session_id` | provider conversation/thread requested on resume |
| `agent_id` | currently bound Claude job or Codex worker process |
| `l2_token` | capability fencing direct L2 mutations for the attempt |

## Component map

| Area | Current owner(s) | Current behavior |
| --- | --- | --- |
| HTTP scheduling and APIs | [`server.py`](../../altitude/server.py) | Deduplicated background jobs, periodic tick, L3/L2 callbacks, recovery wakeup, JSON and stream endpoints, static SPA serving. |
| L3 conversation | [`l3.py`](../../altitude/l3.py), `l3_actions.py` (deleted in phase 5c) | Selects Claude/Codex from quota evidence and keeps provider-specific resumable sessions. Claude runs with a direct scoped-CLI contract; contained Codex returns one structured coordination action for the trusted broker. |
| Task lifecycle/conversation | [`tasks.py`](../../altitude/tasks.py), [`state.py`](../../altitude/state.py) | Creates, transitions, blocks, resumes, reports, completes, and archives. Command paths use different check subsets; Burak messages reject stale displayed identities only when the client supplies them. |
| Dispatch and steering | [`dispatch.py`](../../altitude/dispatch.py) | Creates/validates task worktrees, renders briefs, selects provider, starts workers, replaces a running worker on steering, polls liveness, applies WIP/lease holds, and cleans up. |
| Provider execution | [`engines.py`](../../altitude/engines.py), [`route.py`](../../altitude/route.py), [`quota_codex.py`](../../altitude/quota_codex.py) | Claude CLI and Codex CLI adapters, quota-based routing, Codex sandbox/cgroup containment, worker records, stop/resume, and synchronous contained turns. |
| Codex L2 action broker | `actions.py`, `schemas/l2_action.json` (deleted in phase 5b) | Posted the common message and validated one action after contained worker exit; the Codex L2 now uses the `alt` door. |
| Git policy | [`git_policy.py`](../../altitude/git_policy.py), [`hooks/`](../../hooks) | Fetch/base checks, required task trailers, installed hooks, protected-ref enforcement, and service checkout preflight. |
| Publication | [`land.py`](../../altitude/land.py), [`verify.py`](../../altitude/verify.py) | Validates task authority/scope, commits, pushes, creates/reads PRs, checks exact head/base and CI or local tests, optionally merges, and verifies reports. |
| GitHub issue intake | [`github_intake.py`](../../altitude/github_intake.py) | Inlines the one explicitly referenced issue into the request when the task is created; a failed fetch refuses the task. |
| Faults/incidents | [`incidents.py`](../../altitude/incidents.py) | Blocks the faulting task, deduplicates faults into incident evidence, and queues one L3 message per incident (phase 2). |
| Transcript/status | [`transcript.py`](../../altitude/transcript.py), [`status.py`](../../altitude/status.py), [`monitor.py`](../../altitude/monitor.py) | Generation-fenced live transcript with redaction (portable bundles removed in phase 1b), task rollups, provider/session/context/quota monitoring. |
| Operator interface | [`bin/alt`](../../bin/alt), [`scripts/restart_altitude.py`](../../scripts/restart_altitude.py), `Makefile` | CLI commands, guarded restart/build/health workflow, tests, service install, and operational inspection. |
| Web UI | [`web/src`](../../web/src) | Projects, Inbox, project detail, L3 chat, task conversation, monitor, and live transcript (a task tab since phase 7). |

## Important current cross-cuts

### Two task-intake paths

Codex L3 may create a task through a validated L3 action; Claude L3 may use scoped `alt task new`.
Separately, the Project page's “new request for L3” form posts `action: "new"` directly to the
server, which calls `tasks.new` and schedules dispatch without an L3 turn. The label and behavior
therefore disagree. (Phase 7 removed the form; Chat is the only web intake.)

### Dual execution contracts

Claude L2/L3 sessions have a direct CLI contract and can call scoped `alt` commands, with hooks and
backend checks as guardrails. Codex L2/L3 turns are contained and return inert structured actions
for a trusted broker to execute. Maintaining both contracts causes branches in dispatch, liveness,
resume, cleanup, personas, environment handling, and tests.

### Worktree and PR isolation

Each L2 task normally owns `.claude/worktrees/<slug>` and `worktree-<slug>`. Dispatch validates the
base; `land` enforces scope, provenance, exact PR head/base, checks, and merge hold. The worktree
prevents ordinary file collisions, while shared Git metadata and publication still require trusted
serialization and revalidation.

### Current steering behavior

The task transcript is logically sequential, but the implementation is not a persistent interactive
session. `dispatch.message_l2` appends the human message and then enters the resume path.
`dispatch._resume_session_locked` stops the current physical worker, proves it stopped, reruns
worktree/recovery gates, and launches a replacement with the same provider session. Codex uses
`codex exec resume`; Claude uses `claude --resume`. This is actual `main` behavior, not merely a
proposal.

(Phase 7 removed the Live session composer.) The Live Session composer sent the displayed dispatch/session/engine identifiers. The normal Task
and Project composers do not send those optional fields, so their messages target the generation
current when the server acquires the task lock instead of reliably rejecting a stale page.

### Current self-healing behavior

`incidents.system_fault` blocks the faulting task, files one incident per kind per day, and leaves one
message in the project's L3 queue; `server.tick` drains hook faults, polls work, and delivers queued L3
messages when L3 is free. There is no global hold, repair slot, or automatic service restart.

## Known documentation boundary

[`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) and
[`docs/SESSION_LIFECYCLE.md`](../SESSION_LIFECYCLE.md) describe this current system at a product
level. The comprehensive proposal and its implementation experiments are intentionally kept out of
those current-runtime documents until a reviewed module is actually merged.

One more current evidence limitation matters during review: portable transcript snapshots retain
old Codex worker records, but the live transcript discovers current worker identities rather than
reading the portable bundle. A replaced worker's native JSONL is therefore not guaranteed to remain
visible in the live route even though it is preserved in the bundle.
