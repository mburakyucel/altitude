# Altitude architecture

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
system fault ─► private incident evidence ─► recovery fuse ─► L3 operational recovery
```

## Responsibilities

L3 maintains the project-level conversation, sees the active task set, and decides whether a
request can be answered directly or needs an L2. It does not run a mandatory planning pipeline and
cannot launch subagents directly. When code changes are needed, one L2 owns them.

L2 receives the request, repository context, lease, worktree, branch, and merge policy. It chooses
the lightest useful execution shape. Its human-facing conversation is stored separately from tool
logs, so Burak can steer it directly without routing every exchange through L3. Dispatch and
session identifiers fence messages and resumes against stale workers.

L1 and reviewer runs are optional, tracked children of the L2 task. Their engine may be selected
per run. Their output is input to L2; ownership never transfers.

## Task lifecycle

```text
queued ─► running ─► reported ─► done/archive
   │          │           │
   └──────────┴───────────┴────► rejected/archive
              │
              └─► blocked ─► running
                         └─► rejected/archive
```

Queued tasks wait for WIP, lease, quota, and recovery gates. Blocked means the current L2 needs an
answer or an operational hold has a recorded resume time. Deferral is not an active state: durable
future work belongs in a GitHub issue, and the task exits the active set.

`STATE.md` is regenerated from active task records and contains only work relevant to the next L3
turn. Archived tasks and incident history remain available as audit evidence without being loaded
into L3 context.

## Isolation and landing

Each task uses `.claude/worktrees/<slug>` and `worktree-<slug>`, based on the exact fetched
`origin/main`. Commits require the task provenance trailer. Protected branches cannot be updated
outside the guarded landing path. `alt land` validates the lease and repository, commits, pushes,
opens the PR, waits for configured checks, and merges only when requested and allowed. A task may
carry an explicit merge hold for Burak review.

## Recovery

System faults are deduplicated into private incident evidence and activate a global recovery fuse.
The launch permit is checked for both fresh and resumed work, including the final launch boundary.
Ordinary work stays held while recovery is active. L3 may claim one recovery task; a second repair
is refused. Incident records are evidence only and never create tasks, personas, or follow-up work.
Separately, the active fuse carries one durable, deduplicated L3 attention request. The
server runs at most one recovery turn for it at a time, retains failed turns with bounded backoff, and
audits successful handling. After stability returns, L3 triages the evidence: narrow corrective follow-up
is an FYI, while broad architecture, policy, or system work is preserved for Burak as a proposal or issue.

The service lifecycle is separate from source changes. The current deployment remains stopped and
runtime-masked until a separately authorized restart verifies the merged main commit and its CI.

## Interfaces and storage

The Python server owns state transitions and JSON APIs. The React app provides Inbox, Projects,
Chat, and Monitor navigation plus project/task detail routes. Task chat is a human-readable Burak/L2
conversation; operational events remain an audit detail.

Runtime files live under `ALTITUDE_HOME`. Source-controlled personas, schemas, templates, hooks,
and documentation describe only the current behavior. Superseded designs remain in Git history,
not in the active tree.
