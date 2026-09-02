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
per run. An implementer receives a sublease, leaves the parent commit unchanged, and returns a
validated binary patch plus findings to L2. It does not commit, open a PR, or integrate its own
work. The L2 chooses whether to apply that patch; ownership never transfers.

L2 and L3 can run on Claude Code or Codex. Fresh L2 dispatch records one provider choice and keeps
that provider for the attempt. L3 keeps a separate resumable conversation on each provider. See
[Session lifecycle](SESSION_LIFECYCLE.md) for routing, message, resume, context, and cache semantics.

## Task lifecycle

```text
queued ─► running ─► reported ─► done/archive
   │          │           │
   └──────────┴───────────┴────► rejected/archive
              │
              └─► blocked ─► running
                         └─► rejected/archive
```

A no-code research or proposal task can go directly from `running` to `done/archive`; a git check
refuses that shortcut when the task branch changed. Code work uses the verified report path.

Queued tasks wait for WIP, lease, engine availability, and recovery gates. One provider's quota does
not globally freeze the other. Blocked means the current L2 needs an
answer or an operational hold has a recorded resume time. Deferral is not an active state: durable
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

Claude workers use a direct CLI contract: their scoped requests still cross the same backend
identity, lease, provenance, and merge-policy checks. Codex workers have no control capability or
Git-publication authority. A Codex L2 may write only in its task worktree under an explicit
permission profile; its Git common directory and Altitude state are outside that writable surface,
and hosted tools and model-command network access are disabled. The inner Codex sandbox hides host PIDs, while the
entire process tree runs in a transient user cgroup. Only after the unit is empty does a trusted broker validate its
strict, inert final action and perform any requested state change or landing operation.

Codex L3 uses the same containment and broker boundary. It receives a disposable writable runtime directory while
Altitude's compact state and the selected project checkout are mounted as explicit read-only inputs. This gives the
Codex runtime the small amount of scratch space it needs without giving the coordinator write access to either source.
The user manager creates the transient containment service, so Altitude keeps its own `NoNewPrivileges` hardening
while nested bwrap initializes inside the dedicated service. The outer launcher alone receives the user-session bus;
the Codex child starts from an empty environment rebuilt from a narrow allowlist, with that bus, its runtime socket
tree, ambient service credentials, and scoped L2 capabilities removed. A deterministic host canary verifies that the
inner sandbox cannot see or signal a known host PID; the trusted host can still stop the whole cgroup.
A GitHub-issue action can save only the exact current user message under a title quoted from it. It remains a private
draft until Burak sends the exact draft-specific approval phrase; secret-shaped content is still refused.
L1 implementers receive narrower write subleases; the
trusted wrapper verifies that their parent commit did not move and captures their changes as a
patch for the owning L2 to evaluate.

## Recovery

System faults are deduplicated into private incident evidence and activate a global recovery fuse.
The launch permit is checked for both fresh and resumed work, including the final launch boundary.
Ordinary work stays held while recovery is active. L3 may claim one recovery task; a second repair
is refused. Incident records are evidence only and never create tasks, personas, or follow-up work.
Separately, the active fuse carries one durable, deduplicated L3 attention request. The
server runs at most one recovery turn for it at a time, retains failed turns with bounded backoff, and
audits successful handling. After stability returns, L3 triages the evidence: narrow corrective follow-up
is an FYI, while broad architecture, policy, or system work is preserved for Burak as a proposal or issue.

The service lifecycle is separate from source changes. The architecture-cutover restart was
explicitly authorized, completed from verified main, and smoke-tested without replaying archived
work. Ordinary source changes never start, stop, mask, unmask, or restart the service; any later
lifecycle action requires separate authorization and post-change health verification.

## Interfaces and storage

The Python server owns state transitions and JSON APIs. The React app provides Inbox, Projects,
Chat, and Monitor navigation plus project/task detail routes. Task chat is a human-readable Burak/L2
conversation; operational events remain an audit detail.

Durable replacement writes fsync the temporary file, rename it over the destination, and fsync the
parent directory. One bounded JSONL primitive now owns L3 chat, task conversation, Inbox, incidents,
recovery clearances, and task/project events. Writers take an exclusive lock on the append file
itself; readers take a shared lock, so no persistent per-log sidecar authority is created. Each write
validates the existing stream before mutation, fsyncs the file and parent, and reconciles exact stable
keys. A valid unterminated object is preserved and newline-terminated. Only a syntactically
EOF-incomplete final JSON object (including a partial final UTF-8 code point after an otherwise
incomplete object prefix) may be truncated. A complete non-object, corrupt syntax, invalid UTF-8, or
corruption in an earlier row fails without changing the file. Valid legacy rows without the new key
remain readable; ordinary readers no longer hide malformed legacy rows.

Task `status.json` retains the latest authoritative transition envelope: version, stable transition
id, subject, actor, timestamp, event kind, bounded payload, and payload hash. Task lifecycle changes,
creation/completion requests, and merge-hold changes fsync that state first, then append the exact
transition id to `events.log`. Every later task save reconciles the retained envelope before it may
replace state. Thus failure after state replacement but before event append is repaired without a
second lifecycle change, and retrying the same transition does not duplicate its event. The audit
stream remains a derived projection rather than a second state authority.

The global outer-to-inner lock contract is activation/maintenance, recovery, project, task,
operation, then Git publication. The real recovery hold/launch, project, resume, helper, incident,
JSONL, and publication-settlement lock paths use these levels. Exact re-entry of an already-held
owner lock reuses its outer descriptor; reverse acquisition and same-level non-increasing paths are
refused. Resume and helper launch take the recovery barrier before project/task ownership, and Git
publication is innermost. A failed resume carries only an inert fault description through this
critical section; the public resume or steering entry point appends `resume-failed` and opens the
recovery incident only after every resume lock has unwound. Tests contend the production lock
contexts and the JSONL file descriptors, not substitute lock names.

Runtime files live under `ALTITUDE_HOME`. Source-controlled personas, schemas, templates, hooks,
and documentation describe only the current behavior. Hooks supply Claude-side command guardrails
and telemetry; permission profiles, process containment, and backend validation form the Codex
execution boundary. Superseded designs remain in Git history, not in the active tree.
