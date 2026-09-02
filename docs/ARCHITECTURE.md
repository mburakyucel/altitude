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

L1 and reviewer runs are optional, tracked children of the L2 task. Their engine is selected from
the closed autonomous capability set, currently Codex only. An implementer receives a sublease,
leaves the parent commit unchanged, and returns a
validated binary patch plus findings to L2. It does not commit, open a PR, or integrate its own
work. The L2 chooses whether to apply that patch; ownership never transfers.

L2 and L3 currently run autonomously on Codex. Fresh L2 dispatch records the choice and keeps that
provider for the attempt. Historical Claude session identity is retained for observation and cleanup,
but it is not selected or resumed. See
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

Queued tasks wait for WIP, lease, engine availability, and recovery gates. A disabled provider pin
fails closed and does not fall through to Codex. Steering or resuming a legacy disabled-provider task
returns an inert provider hold before conversation, event, task, worker, incident, or recovery mutation.
Such a row does not consume runnable-provider WIP. Its normalized narrow lease still collides normally;
missing, empty, malformed, broad-only, or candidate-unknown scope is repository-uncertain and blocks new
mutation in that repository without freezing another repository.
Blocked means the current L2 needs an
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

New Claude workers are disabled: the CLI's foreground and resume flags were insufficient to prove
the required unit ownership, result spool, deterministic stop, and empty cgroup without a real turn.
The existing hooks, command guard, backend identity/lease/provenance checks, and read-only `alt agents`
inspection plus physical worker stop/remove remain for legacy evidence and cleanup. A legacy Claude
task's durable state is mutation-held by target provider, regardless of a caller's actor string. Same-UID
CLI environment, TTY, or flag values cannot distinguish an operator from an unsandboxed legacy worker, so
they are not authentication. Production activation of this source therefore requires the external stopped-
production gate to prove all legacy Claude units/processes empty first. Codex workers have no control capability or
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

Remote PR checks execute Python and web test/typecheck/build in independent checkouts of the same
exact candidate SHA. The
base-owned workflow accepts only same-repository heads, pins its actions and Node version, has
read-only repository permission, persists no checkout credential, exposes no token or secret, and
runs candidate commands under an empty environment with disposable HOME and cache directories.

### Dormant physical-ownership foundation

`engines.py` now defines one closed physical transition for later L3, L2-owner, and helper adoption:
`planned -> prior_stopped -> spawned -> bound -> result_observed -> empty -> complete | failed`.
Its deterministic unit, physical generation, provider-session request, stable message id, and optional
recovery episode/permit revision are immutable intent protected by one stable digest. Receipts have closed
stage-specific shapes, and the record revision is derived exactly from its receipts and durable error. Each
caller will embed the transition in its existing
authoritative aggregate and persist it with the durable state primitives; the transition creates no sidecar
record, journal, lock, or artifact family. A recorded pre-spawn error reconciles through
`launched=false`, `bound=false`, its exact error result, and proven empty before failure, without a
provider effect. After `prior_stopped`, an empty/collected unit with neither an error nor a durable
result is ambiguous—the launch may have run and exited—so reconciliation returns
`ownership_uncertain` instead of relaunching. An unknown or populated unit can never become terminal.

Provider-neutral managed-unit helpers expose exact manager/cgroup observation and whole-unit spawn/stop while
reusing the existing Codex containment mechanics. A read-only reconciliation helper correlates the exact unit
with a durable result marker carrying the same intent digest and returns a crash-stage decision; disagreement
refuses as `ownership_uncertain` rather than guessing or relaunching. This foundation is deliberately dormant: no production L3,
L2, helper, server, CLI, or timer path consumes it yet, and all current provider behavior remains unchanged.

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

The next control-plane boundary is defined, but deliberately dormant: `altitude/contracts.py` owns
closed Python contracts for worker outcomes, publication scope, quota observations, application
command results, and task/operational read projections. `web/src/data/contracts.ts` independently
expresses the matching task/operational wire schemas in the existing Zod runtime validator, and both
runtimes exercise the same versioned JSON fixtures. Every WorkerOutcome carries one closed
`observations` block for untrusted findings, decisions/questions, FYIs, follow-up proposals,
deviations, usage/spend, and the worker's merge-hold observation. It carries no outcome/effect id,
trusted verification/publication fact, or human reply; trusted settlement derives identity and
checks canonical hold state, while replies remain in the task conversation. No service, broker, API, CLI, or UI path imports
these contracts yet; each later adoption must replace its old producer and consumer together rather
than introduce a behavior selector or a second authority.

Runtime files live under `ALTITUDE_HOME`. Source-controlled personas, schemas, templates, hooks,
and documentation describe only the current behavior. Hooks supply Claude-side command guardrails
and telemetry for legacy Claude evidence; permission profiles, process containment, and backend
validation form the Codex
execution boundary. Superseded designs remain in Git history, not in the active tree.

### Durable I/O foundation

`state.py` owns three persistence contracts. Atomic replacement fsyncs the temporary file,
renames it, and fsyncs the parent directory. The keyed JSONL primitive locks the destination inode,
accepts only recursively valid JSON object rows with a nonempty string key, compares strict
canonical JSON (including boolean/integer/float distinctions), and rejects duplicate keys,
non-string object keys, non-finite numbers, unpaired surrogates, and conflicting id reuse. LF is the
only commit marker: CR is ordinary row content, malformed LF-terminated or non-object rows fail
without mutation, and readers never expose any unterminated final bytes. Append excludes that
uncommitted tail from deduplication—even when it parses as a complete object—and truncates it before
append.
This covers a kill at every byte of UTF-8 strings, numbers, literals, arrays, and nested objects
without guessing from decoder errors or promoting bytes that were never committed.
The primitive is deliberately dormant in this phase: no event, chat, incident, task, or recovery
producer has been switched to it.

New and migrated locks use this outer-to-inner order: activation/maintenance, recovery, project,
task, operation, Git publication. `project_lock` is the sole migrated runtime wrapper; exact
top-of-stack re-entry shares its held lock, same-level multi-lock acquisition uses canonical path
order, and the same canonical path or inode cannot be assigned another level or acquired through a
hard-link alias.
The remaining legacy wrappers are explicitly frozen by a multiplicity-preserving source-inventory
test: recovery state and launch, incident fault and allocation, L1 run, task resume, and publication
settlement. The same inventory covers legacy append writers (task conversation/FYI, task/project
events, L3 chat, incident index, clearance, process/log streams, Python hooks, and the shell
statusline temp/move)
plus aliased/bare JSON and atomic aggregate writers, writable open/os.open calls, path writers,
JSON dump/os.write, every stream write/truncate, Path and os/shutil rename/replace/removal/move,
replacement/copy calls, and raw locks including the foundation module. Runnable shell sources are
recursively enumerated under the closed production roots as well as scanned for writes. Counts are
per owning function rather than sets, so another call in an already-listed function also fails.
Their behavior and on-disk shapes are unchanged here; each moves only with its owning domain in the
later owner-family migration, when its legacy allowlist entry is deleted. No transition envelope,
replay scan, workflow selector, sidecar journal, dependency, or runtime artifact family is introduced
by this foundation.

The bounded source budget is explicit: production Python remains the same module set and changes
only `altitude/state.py`, from 222 to 521 lines (+299 net; 307 added, 8 removed). One 991-line
focused test module and this architecture note are the only new/expanded source artifacts. Runtime
dependencies, services, selectors, schemas, generated outputs, and durable artifact families each
increase by zero. The added production surface is the three reusable primitives above; domain
adapters and replay machinery are intentionally outside this phase.
