# Altitude architecture

Altitude turns a project-level conversation into isolated, reviewable work. It has a small
coordination layer, one owner per task, and mechanical safety rails: model judgment chooses how much
decomposition a request needs; code enforces task ownership, isolation, launch holds, and the PR
boundary.

```text
Burak
  ├─ project direction and roadmap ───────────────► L3
  │                                                  │
  │                                                  └─ coordinates one task
  └─ task questions and steering ◄───────────────► L2 owner
                                                     ├─ may implement directly
                                                     └─ may delegate to its engine's own subagents

L2 worktree/branch ─► checks/review ─► PR ─► merge ─► archive task
system fault ─► blocked task + private incident ─► one queued L3 message
```

## Responsibilities

L3 maintains the project-level conversation, sees the active task set, and decides whether a
request can be answered directly or needs an L2. It does not run a mandatory planning pipeline and
cannot launch subagents directly. When code changes are needed, one L2 owns them.

L2 receives the request, repository context, lease, worktree, branch, and merge policy, and chooses
the lightest useful execution shape. Its conversation with Burak is stored apart from tool logs, so
Burak messages it directly without routing through L3. Messages queue on the task and reach the
worker at its next checkpoint; an explicit Stop aborts a worker. The attempt number fences every L2
command to the current attempt: an L2 may reply, block, complete, and land only its own task.

Helpers are engine-native. The L2 may delegate bounded slices to its engine's own subagents
(Claude Code's Agent tool, Codex's equivalent); Altitude does not track them, and ownership never
transfers. Helper customization lives in engine-native files (agent definitions, skills, hooks). The
L2 persona carries brief delegation and context-hygiene guidance and asks for a small `progress.md`
(goal, done, next, how to verify) refreshed at milestones, never kept as a log.

L2 and L3 can run on Claude Code or Codex. Fresh L2 dispatch records one provider choice and keeps
that provider for the attempt. L3 keeps a separate resumable conversation on each provider. See
[Session lifecycle](SESSION_LIFECYCLE.md) for identity, routing, messages, containment, and context.

## Task lifecycle

```text
queued   -> running | rejected
running  -> reported | blocked | rejected | done
blocked  -> running | reported | rejected
reported -> done | running | blocked | rejected
```

A no-code research or proposal task can go directly from `running` to `done/archive`; a git check
refuses that shortcut when the task branch changed. Code work uses the verified report path.

Queued tasks wait for WIP, lease, and engine availability gates. One provider's quota does not
globally freeze the other. Blocked is a persisted wait/intervention state: an L2 question, a timed
operational hold, a worker failure, a verifier fault, or a report gap. Deferral is not an active
state: durable future work belongs in a GitHub issue, and the task exits the active set.

`STATE.md` is regenerated from active task records and contains only work relevant to the next L3
turn. Archived tasks and incident history remain available as audit evidence without being loaded
into L3 context.

## Isolation and landing

Each task uses the isolated worktree path `.claude/worktrees/<slug>` and branch `worktree-<slug>`,
based on the exact fetched `origin/main`. Commits require the task provenance trailer. Protected
branches cannot be updated outside the guarded landing path. The trusted landing code validates the
lease and repository, commits, pushes, opens the PR, waits for configured checks, and merges only
when requested and allowed. A task may carry an explicit merge hold for Burak review.

Every worker is an untrusted process in its worktree, whichever engine runs it. Its only door into
Altitude is the `alt` CLI; the backend validates each command against the task record under the
project lock. Claude Code runs as a background job with Altitude's hooks for inbox delivery and
telemetry. Codex keeps its native workspace-write sandbox as containment and uses the same door;
Altitude reads its thread and usage from the worker's stdout JSONL. A turn that ends without a
report, a block, or a completion blocks the task as ended without a report, on either engine. A
Codex L3 turn is contained the same way from a disposable runtime directory under the project's
Altitude folder.

## Faults

A system fault is project-scoped and two-tier. Tier one is code: a temporary capacity stop is retried
with backoff; a usage-window stop starts a fresh attempt on the other engine from the task's
`progress.md`, or parks a task pinned to one engine until its window reopens; each writes one task
event. A Claude usage-window stop is recorded once for the machine, because the subscription is
machine-wide; Codex reports its limits per turn. Tier two is L3: whatever remains blocks only its own
task, files private incident evidence (one incident per fault kind per day), and leaves one message
in the project's L3 queue. The server delivers that message as a turn when L3 is free and an engine
is available; L3 records the learning on the incident and fixes the cause directly or creates one
ordinary task. An incident raised by that repair task (`--source recovery`) goes to the Inbox instead
of waking L3 again. A task blocked before any launch goes back to the queue when it is resumed.
Incident records are evidence only and never create tasks, personas, or follow-up work.

A merged Altitude change marks a restart pending. The web app shows a banner on every page and, once
no L2 is running and no L3 is busy, a Restart button that runs the guarded restart script as a
transient user unit outside altd's own cgroup. Ordinary source changes never start, stop, mask,
unmask, or restart the service; a lifecycle action needs separate authorization and post-change
health verification.

## Interfaces and storage

The Python server owns state transitions and JSON APIs. The React app provides Inbox, Projects,
Chat, and Monitor navigation plus project/task detail routes. Chat is the only way to create a task
from the web: the L3 turn creates it through `alt task new`. The composer's engine choice pins one
turn to Claude or Codex; on auto the project pin or the weekly quota decides. A task has two views. The Conversation
tab is the human-readable Burak/L2 exchange; the Live session tab shows the worker's own session log
(Claude's session JSONL, or every turn of the Codex thread) together with Altitude's task events.
Operational events remain an audit detail.

Runtime files live under `ALTITUDE_HOME`; a task is a directory a person can read. Source-controlled
personas, schemas, templates, and hooks describe current behaviour: `hooks/` holds the Git hooks
that `git_policy` installs into every managed repository, the Claude inbox hook, and the statusline
monitor. [SIMPLIFICATION.md](SIMPLIFICATION.md) records why the system has this shape.
