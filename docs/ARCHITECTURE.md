# Altitude architecture

> **Scope:** This document describes executable `main` at commit `97e1197`. The comprehensive
> simplification branches are not merged or accepted. See the
> [2026-09-02 module-by-module review checkpoint](simplification-review/README.md) for the current
> code/candidate boundary and continuation instructions.

Altitude has a small coordination layer, one task owner, optional bounded helpers, and mechanical
safety rails. Model judgment chooses how much decomposition a request needs; code enforces task
ownership, isolation, launch holds, and the PR boundary.

```text
Burak
  ├─ project direction and roadmap ───────────────► L3
  │                                                  │
  │                                                  └─ coordinates one task
  └─ task questions and steering ◄───────────────► L2 owner
                                                     ├─ may implement directly
                                                     ├─ may use 0..N bounded L1s
                                                     └─ may use an independent reviewer

L2 worktree/branch ─► checks/review ─► PR ─► merge ─► archive task
system fault ─► blocked task + private incident ─► one queued L3 message
```

## Responsibilities

L3 maintains the project-level conversation, sees the active task set, and decides whether a
request can be answered directly or needs an L2. It does not run a mandatory planning pipeline and
cannot launch subagents directly. When code changes are needed, one L2 owns them.

L2 receives the request, repository context, lease, worktree, branch, and merge policy. It chooses
the lightest useful execution shape. Its human-facing conversation is stored separately from tool
logs, so Burak can steer it directly without routing every exchange through L3. Dispatch and
capability identifiers fence specific L2 reply, completion, landing, and helper paths; other command
paths apply different subsets of checks. Live Session steering also supplies the displayed
session/engine generation to reject stale views.
The normal Task and Project composers currently omit their displayed generation, so those requests
target the generation current when the server acquires the task lock; this is a known review item,
not a guarantee of stale-page rejection.

L1 and reviewer runs are optional, tracked children of the L2 task. Their engine may be selected
per run. An implementer receives a sublease, leaves the parent commit unchanged, and returns a
validated binary patch plus a summary to L2; a reviewer returns structured findings. Neither
commits, opens a PR, or integrates its own work. The L2 chooses what to use; ownership never transfers.

L2 and L3 can run on Claude Code or Codex. Fresh L2 dispatch records one provider choice and keeps
that provider for the attempt. L3 keeps a separate resumable conversation on each provider. See
[Session lifecycle](SESSION_LIFECYCLE.md) for routing, message, resume, context, and cache semantics.

## Task lifecycle

```text
queued   -> running | rejected
running  -> reported | blocked | rejected | done
blocked  -> running | reported | rejected
reported -> done | running | blocked | rejected
```

A no-code research or proposal task can go directly from `running` to `done/archive`; a git check
refuses that shortcut when the task branch changed. Code work uses the verified report path.

Queued tasks wait for WIP, lease, and engine availability gates. One provider's quota does
not globally freeze the other. Blocked is a persisted wait/intervention state: examples include an
L2 question, a timed operational hold, a worker/action failure, verifier fault, or report gap.
Deferral is not an active state: durable
future work belongs in a GitHub issue, and the task exits the active set.

`STATE.md` is regenerated from active task records and contains only work relevant to the next L3
turn. Archived tasks and incident history remain available as audit evidence without being loaded
into L3 context.

## Isolation and landing

Each task uses the isolated worktree path `.claude/worktrees/<slug>` and branch `worktree-<slug>`,
based on the exact fetched `origin/main`. Commits require the task provenance trailer. Protected
branches cannot be updated outside the guarded landing path. The trusted landing code validates the
lease and repository, commits, pushes, opens the PR, waits for configured checks, and merges only
when requested and allowed. A task may carry an explicit merge hold for Burak review.

Claude workers use a direct CLI contract: the backend applies the identity, clean-Git, lease,
provenance, and merge-policy checks relevant to each command and effect boundary. Codex workers have no control capability or
Git-publication authority. A Codex L2 may write only in its task worktree under an explicit
permission profile; its Git common directory and Altitude state are outside that writable surface,
and hosted tools and model-command network access are disabled. The inner Codex sandbox hides host PIDs, while the
entire process tree runs in a transient user cgroup. Only after the unit is empty does a trusted broker validate its
strict, inert final action and perform any requested state change or landing operation.

Codex L3 uses the same containment and broker boundary. It receives a disposable writable runtime directory while
the full Altitude runtime root (`ALTITUDE_HOME`) and the selected project checkout are explicit read-only roots. The
prompt normally points the coordinator at compact state, but the sandbox technically permits reads throughout those
roots. This gives the Codex runtime scratch space without write access to source or durable Altitude state.
The user manager creates the transient containment service, so Altitude keeps its own `NoNewPrivileges` hardening
while nested bwrap initializes inside the dedicated service. The outer launcher alone receives the user-session bus;
the Codex child starts from an empty environment rebuilt from a narrow allowlist, with that bus, its runtime socket
tree, ambient service credentials, and scoped L2 capabilities removed. The inner sandbox hides host PIDs; no
deterministic host-PID canary is implemented on this baseline. The trusted host can stop the whole cgroup.
A GitHub-issue action can save only the exact current user message under a title quoted from it. It remains a private
draft until Burak sends the exact draft-specific approval phrase; secret-shaped content is still refused.
L1 implementers receive narrower write subleases; the
trusted wrapper verifies that their parent commit did not move and captures their changes as a
patch for the owning L2 to evaluate.

## Faults

A system fault is project-scoped and two-tier. Tier one is code: a temporary capacity stop is retried
with backoff and a usage-window stop parks the task until the window reopens, each as one task event.
Tier two is L3: whatever remains blocks only its own task, files private incident evidence (one
incident per fault kind per day), and leaves one message in the project's L3 queue. The server
delivers that message as a turn when L3 is free and an engine is available; L3 records the learning
on the incident and fixes the cause directly or creates one ordinary task. An incident raised by that
repair task (`--source recovery`) goes to the Inbox instead of waking L3 again. There is no global
fuse, hold, clear command, or repair slot. A task blocked before any launch goes back to the queue
when it is resumed. Incident records are evidence only and never create tasks, personas, or
follow-up work.

The service lifecycle is separate from source changes. The architecture-cutover restart was
explicitly authorized, completed from verified main, and smoke-tested without replaying archived
work. Ordinary source changes never start, stop, mask, unmask, or restart the service; any later
lifecycle action requires separate authorization and post-change health verification.

## Interfaces and storage

The Python server owns state transitions and JSON APIs. The React app provides Inbox, Projects,
Chat, and Monitor navigation plus project/task detail routes. Task chat is a human-readable Burak/L2
conversation; operational events remain an audit detail.

The Project page's control labeled as a new request for L3 currently posts a direct task-creation
action to the server; it does not run an L3 turn. Project Chat is the actual L3 conversation entry.
That mismatch is retained here as current behavior pending the module-by-module UI/intake review.

Runtime files live under `ALTITUDE_HOME`. Source-controlled personas, schemas, templates, and hooks
describe current executable behavior. Documentation under `docs/simplification-review/` separately
catalogues unmerged candidates and must not be read as runtime behavior. Hooks supply Claude-side command guardrails
and telemetry; permission profiles, process containment, and backend validation form the Codex
execution boundary. Superseded designs remain in Git history, not in the active tree.
