# 02 - Proposed architecture

> Draft target for review. It is intentionally more specific than the active
> [Architecture](../../ARCHITECTURE.md), but it is not active policy.

## Design rules

1. **Models decide; code enforces.** L3 and L2 use judgment about decomposition, review, and how to
   respond. Code enforces ownership, isolation, scope, publication, containment, and launch safety.
2. **One owner, one fact, one authority.** A task has one logical L2 owner and exactly one current
   physical owner generation. Each durable fact has one canonical record; prompts and UI responses
   are derived projections.
3. **Scope the response to the blast radius.** A local failure cannot freeze unrelated work. The
   global fuse is reserved for uncertainty about shared writers, containment, base integrity, or
   durable control-plane state.
4. **Do deterministic work deterministically.** Recheck observable state, retry a typed idempotent
   operation, or hold the affected scope before spending another model turn.
5. **Task execution, deployment, and maintenance are separate lifecycles.** Merging a task does not
   update the running checkout; archiving a task does not require deleting its worktree.
6. **Provider differences stop at adapters.** Claude and Codex may launch and resume differently,
   but expose the same worker state and terminal outcome to the control plane.
7. **Compatibility is a migration, not an architecture.** Every fallback reader has a versioned
   migration and deletion gate.
8. **Normal views are human views.** Technical event logs, raw state, and transcript detail remain
   opt-in diagnostics rather than the main conversation.

## Logical components

These are responsibility boundaries, not a requirement to create one file or service for each box.
The refactor should prefer fewer modules after responsibilities have been removed.

### 1. Application command layer

One trusted in-process command API owns all durable mutations whether the caller is HTTP, CLI,
Claude, or a Codex action broker. Commands include:

- register/remove project;
- create a task from a current L3 action or the active recovery episode's single task claim;
- record task conversation and steer/resume the current L2;
- reject, block, resume, or hold merge;
- apply a normalized worker outcome;
- record/classify fault evidence and clear recovery;
- record deployment pending/activated; and
- create an explicitly authorized GitHub issue.

Every command obtains the relevant cross-process lock, validates authority and current generation,
and commits one atomic authoritative transition before returning a typed result. The canonical
aggregate record, append-only record, or atomically renamed new directory written by that command
contains a monotonic revision and a stable transition envelope: command/transition id, event kind,
subject, actor, timestamp, and payload hash (plus the bounded event payload when it is not already
the canonical append record). Only after that state write is fsynced does the command append the
same transition id to `events.jsonl`.

State and the audit stream are intentionally not claimed to be one filesystem transaction. At
startup and under the same lock before the next mutation of that aggregate, the command layer
reconciles its latest transition envelope: a missing or partial audit row is appended again, and
physical duplicate rows with the same transition id collapse to one logical event for every
reader/export. Event-first is forbidden. Therefore a crash can delay an audit projection but cannot
invent an unapplied event or permanently lose the last committed event; no second mutation can hide
the gap. Kill tests cover state rename, event append, and reconciliation. HTTP and CLI code do no
direct state mutation. This removes current divergence such as project/task creation being
implemented separately in the server, CLI, and L3 broker.

A filesystem write can be atomic; a process launch, provider call, or GitHub effect cannot be part of
that same atomic write. The command layer therefore has one small typed operation-journal utility.
It is not a general workflow engine: only `l3_turn`, `worker_transition`, `settlement`,
`issue_publication`, and `recovery_transition` are valid variants. `recovery_transition` has only
`task_claim` and `clearance` subtypes; every variant has a fixed stage table.
Before an external effect, the command records a stable operation/effect id and deterministic target;
afterward it records the observed receipt. Crash recovery reconciles that exact target before retrying
and never asks a model to repeat a deterministic effect. No subsystem invents its own timestamp lease
or ad hoc “in progress” flag after its variant is migrated.

### 2. Project coordinator (L3)

L3 remains a flexible conversation, not a workflow engine. It may:

- answer roadmap, architecture, prioritization, or status questions directly;
- create one well-briefed L2 task when Burak asks for execution;
- create a proposal task when Burak explicitly asks for design rather than implementation;
- surface project-level FYIs and choices;
- inspect and resolve operational recovery; and
- delegate the active recovery episode's one L2 if code is genuinely necessary.

L3 does not relay ordinary L2 questions, automatically pull backlog issues, close every clean task,
spawn L1s, or create a chain of healing work. Claude and Codex keep separate provider sessions. A
provider change receives a bounded human-chat handoff; it is a new provider conversation, never a
fabricated transcript resume.

All L3 turns are serialized across processes. Either only the service invokes them, with CLI calls
routed through the service, or an explicit offline operator command proves the service stopped and
acquires the same kernel file lock. The proposal selects the service as the ordinary mutation path;
an expiring owner file or process-local thread lock is not authority.

Each accepted human message has an id and one `l3_turn` journal entry covering provider selection,
physical launch, native session result, response append, and any inert action application. On a
crash, the named process unit/session/result is reconciled and the response/action id is applied at
most once. This preserves separate provider sessions without pretending that a lock alone makes a
network/model turn atomic.

### 3. Task and logical L2 owner

The canonical `TaskRecord` contains product state, not every derived observation:

```text
identity: project, slug, title, source
request: immutable request/snapshot reference
state: queued | running | settling | blocked
scope: normalized optional repo-relative publication paths
code_allowed, control_contract_version
provider_policy: requested preference/reserve policy
merge_hold: null or explicit reason
generation: null or OwnerGeneration
active_operation: null or worker-transition/settlement journal reference
publication_attempts: ordered immutable PublicationReceipt references
result: null or final PublicationReceipt/NoCodeReceipt reference
blocked: null or typed reason/question/attention/resume condition
created, updated, schema_version
```

Terminal `done` and `rejected` records move to the archive immediately and do not remain in the
active task list.

`OwnerGeneration` is the only ownership fence:

```text
attempt_id
provider
model
route_observation_reference
provider_session_id
physical_worker_id
process_unit_id
capability_id
worktree
branch
base_sha
```

One locked `require_current_generation` operation is reused by messages, block/resume, outcome
application, helper launch, publication, and completion. Callers do not reimplement partial tuples.
The capability remains private and is never rendered in UI, transcripts, issues, PRs, or logs.
Every physical start or resume atomically installs a new generation after proving the prior physical
worker stopped. A genuine provider resume may keep `provider_session_id`, but it receives a new
`attempt_id`, `physical_worker_id`, and `capability_id`; every prior action is therefore stale. The
logical L2 still owns the task end to end across these serial generations.

`worker_transition` is the crash boundary for launch, steering, stop, and rejection. Before spawning,
the task records the target generation id, deterministic process-unit name, provider request, and
message id with stage `planned`. Stages then record `prior_stopped`, `launched`, `bound`, and
`delivered/finished`. After a crash, Altitude inspects that named process unit and durable provider
output before deciding whether launch occurred; it never guesses from a timestamp or invokes resume
twice. Rejection and blocked transitions cannot finish until this journal proves the prior L2 and
every task-owned helper process unit terminal/empty and reconciles their receipts.

### 4. Task lifecycle and settlement

```text
queued -> running -> settling -> archive(done)
             ^     |   |
             |     |   +-> blocked
             |     |          |
             +-----+----------+  (new fenced generation on continue/resume)

queued/running/blocked/settling -> archive(rejected)
```

- `queued`: durable request exists; no worker owns it.
- `running`: one logical L2 owns the task through exactly one current physical generation. A
  replacement preserves task ownership and genuine provider-session continuity, but installs a new
  generation only after proving the old physical writer stopped.
- `settling`: the physical worker has exited and one normalized outcome is being processed. The
  outcome is journaled and idempotent, so a service restart resumes the same settlement without a
  model turn. A `continue` result, completed helper result, addressed finding, or recoverable
  post-merge failure may install a new generation and return the same logical task to `running`.
- `blocked`: a user choice, scoped operational condition, provider window, or publication hold
  prevents progress. The reason names who/what can unblock it. A blocked task has no physical worker
  that can still write; the transition completes only after stop/containment proof. If that proof is
  unavailable, ownership is uncertain and the appropriate safety fuse remains active.
- archive: terminal outcome and receipt are durable; the task leaves normal active context.

Rejection from a live state follows the same stop-before-terminal rule. The diagram describes
product state, not permission to move or delete a task directory while any L2/helper writer may
still exist.

The current `reported` state, `status.json.verified`, `l3_handled`, `completion_requested`, and stranded
report promotion are replaced by `settling` plus one idempotent outcome journal. L3 is notified only
when the outcome actually contains a project-level decision, FYI, follow-up proposal, open finding,
or merge hold. Clean verified completion closes mechanically.

Human attention is explicit rather than another closeout flag. A decision, question, open finding,
or merge hold becomes a typed open attention item referenced by the blocked task; resolving it records
the decision/message id and either resumes settlement or installs a new worker generation. A
non-blocking FYI or follow-up proposal is appended once to canonical project/task events and may be
archived with the task. Project attention views are derived from open task attention plus those
events. D7 decides only whether non-blocking FYIs also have a durable read/ack Inbox; it does not
control task correctness. There is no separate `l3_handled` truth to get stranded after a crash.

### 5. Provider adapters and normalized outcomes

One provider interface exposes:

```text
start(request, worktree, policy) -> WorkerIdentity
resume(provider_session_id, message, worktree, policy) -> WorkerIdentity
stop(physical_worker_id) -> StopReceipt
status(physical_worker_id) -> WorkerStatus
usage(provider_session_id) -> UsageObservation
transcript_sources(generation) -> bounded local sources
```

Provider implementations retain necessary differences:

- Claude may use its authenticated CLI/job registry and its current command guard/backend
  capabilities. Independently of the optional full-containment decision D4, every Claude physical
  worker must run in a tracked process unit whose entire process tree can be stopped and proven
  empty; parent-process exit or provider job disappearance alone is not settlement proof.
- Codex remains in an explicit permission profile and transient cgroup with scrubbed environment;
  no trusted action is interpreted until the entire containment unit is empty.

Routing consumes provider-neutral observations without pretending the quotas are interchangeable.
The configured weekly reserve/remaining budget is the primary capacity signal; a currently open
short/session window does not justify draining a nearly exhausted weekly allowance. Short-window
capacity, task/provider compatibility, and an explicit user preference are secondary inputs. An
unknown or exhausted provider holds only that provider, the decision and observation freshness are
recorded, and the other provider remains eligible. L3, L2, and optional helpers use this same
policy; provider reserves are operator policy rather than per-task orchestration machinery.

Both providers produce the same untrusted internal `WorkerOutcome`. The request may be journaled
before exit, but trusted settlement cannot act on it until the registered physical process unit is
proven empty:

```text
publish(commit_message, optional_pr_title, request_merge, evidence)
complete_no_code(digest, evidence)
block(reason_or_question, resume_condition)
continue(reason, optional_helper_requests)
```

These are model-declared outcomes, not the complete worker-status taxonomy. The adapter separately
normalizes `live`, `clean_exit_without_outcome`, `quota_limited`, `capacity_limited`,
`provider_failed`, `malformed_outcome`, `process_missing`, and `ownership_uncertain`. Typed command
policy then either reroutes/resumes the same logical task, blocks it with evidence and a retry
condition, or activates the global fuse when another writer cannot be ruled out. An abnormal exit is
never fabricated into a model outcome.

The transport may differ: Codex returns an inert strict schema; Claude may initially use a validated
task command/report adapter. The target is identical semantics and control-plane settlement, not
identical sandbox internals. Claude's shell guard cannot be deleted until the replacement boundary
has been penetration-tested and used successfully in real tasks.

Steering an L2 stops its current physical process and resumes the same native provider session when
that provider supports genuine resume. It installs a new fenced generation but does not replay the
entire transcript as a fabricated prompt. A provider switch or intentional context rotation creates
a new provider session with a bounded human-readable handoff and is displayed as such. Context and
quota percentages are timestamped provider observations, not task-state authority; revised provider
measurements may legitimately move upward or downward without implying that the task changed
sessions.

In the target state, a Claude worker may still edit, test, and send human task replies directly, but
its publish/complete command only journals an inert outcome request. It does not commit, push, merge,
or archive while the physical worker can still write. Trusted settlement performs those side effects
after the whole registered process unit is empty. The existing Claude filesystem/command guard stays
unless D4 later approves and proves full containment; process ownership is a Phase 2 prerequisite,
not a claim that Claude already has Codex's sandbox. Without both changes, provider outcomes are not
actually normalized and the same-writer settlement guarantee would be false.

### 6. Optional helpers and review

L1/reviewer help remains optional and owned by L2. The platform-managed helper mechanism initially
stays because it provides cross-provider selection, bounded subleases, independent sessions, and
patch capture without transferring PR ownership.

The reduced helper contract contains only:

- role (`implementer` or `reviewer`);
- bounded brief;
- optional provider/model preference;
- normalized sub-scope;
- parent commit and owner generation;
- patch or structured findings; and
- terminal status.

Remove PR parsing, PR aggregation, helper publication, and cleanup coupling. Native provider
subagents may later replace platform-managed helpers only if they preserve isolation, bounded scope,
observable status, and the L2's sole-publisher rule.

A `continue` outcome that requests helpers keeps the task in `settling`, creates bounded helper
records idempotently, and waits without a live L2 writer. Completed patch/findings receipts are
attached to that settlement; Altitude then installs a new L2 generation with their concise results
and returns the task to `running`. Helper records own their prompt, parent generation/commit,
process unit, patch or findings, and terminal receipt. They never own a PR or task completion.
Rejection/terminal archive stops or reconciles every owned helper unit before the task leaves the
active set.

### 7. Scope and concurrency

Default repository WIP is one L2. This is especially important for Altitude while it is self-hosting.
Parallelism remains available across repositories and inside a task through optional helpers.

Under the recommended D1 policy, a blocked code task retains the repository slot because its branch
and unresolved publication still belong to that logical owner. It must be resumed, completed,
rejected, or explicitly converted to a GitHub issue before another top-level L2 starts. Altitude
does not silently park work and pretend its collision risk disappeared. This conservative rule can
be amended during review, but doing so requires an explicit multi-branch conflict policy.

One narrowly defined exception exists for an active global recovery episode. Trusted reconciliation
must first bring any ordinary SettlementJournal/external effect to an unambiguous stable boundary;
a task with unresolved commit/push/PR/merge state cannot be preempted. After every ordinary
worker/helper is checkpointed, stopped, and proven empty, the task becomes `state=blocked` with
`blocked.kind=preempted_by_episode`, and the episode may temporarily assign the repository slot to
its single recovery L2. The ordinary branch/session are preserved but cannot resume, settle, or
publish. After clearance, a new `worker_transition` owns base/worktree revalidation and fresh
workspace/rebase preparation before a new generation can reclaim the slot; conflicts remain typed
blocked evidence until deliberately resumed. This is exclusive writer preemption, not concurrent
WIP or silent parking.

Task paths become a normalized publication scope, not a natural-language predictive lock. Rules:

- paths are repo-relative, normalized, and validated by one function;
- an explicit scope is enforced by the trusted publisher;
- an undeclared scope is legal only when project policy explicitly permits it. In that mode the
  trusted publisher derives and records the actual changed paths; the L2 does not self-authorize a
  broader lease after editing;
- brace expansion, annotations, broad-directory suppression, and separate scheduling/landing
  semantics are removed; and
- WIP greater than one is an explicit later capability with a simple, documented conflict policy,
  not the default architecture.

This preserves collision safety using isolated worktrees, sole publication, pinned PR pairs, and
serial landing without maintaining a custom predictive path scheduler for the default case.

### 8. GitHub issue intake and backlog writes

When a request explicitly identifies a GitHub issue, the trusted intake command fetches and
validates it before the task becomes dispatchable. It stores one immutable, repository-bound
snapshot referenced by the request. If hydration fails, intake fails visibly before any L2 starts.
Resume never has to repair missing brief context.

When Burak explicitly asks L3 to create a GitHub issue, that message is authorization for the
trusted broker to publish a bounded, secret-scanned issue in the registered repository. If L3
proposes preserving something without an explicit user request, it returns a draft for approval.
This retains the important external-write boundary without requiring a second magic phrase after an
already explicit instruction. The `issue_publication` journal binds repository, content hash, draft
or user-message authority, and one stable effect id; after a crash it searches/reconciles that exact
effect before any retry, preventing duplicate issues.

### 9. Trusted publication and verification

Publication remains a deterministic privileged transaction. It validates:

- current owner generation and stopped/settled worker as required by provider policy;
- task worktree and branch identity;
- immutable fetched base SHA and task provenance trailers;
- changed files against the canonical scope;
- merge hold;
- authenticated GitHub access before committing;
- remote branch ownership/non-destructive push;
- exact PR base/head pair;
- repository-defined required checks, or the exact local merge-candidate suite when no CI exists;
- merge result and exact merge SHA; and
- applicable post-merge check for that merge SHA.

The task's `SettlementJournal` makes the non-atomic transaction explicit. It is keyed by outcome id
and owner generation and has fixed stages for final-worktree snapshot, verification/review, commit,
push, PR discovery/creation, head/base revalidation, checks, merge, post-merge observation, final
receipt, an applicable deployment contribution, attention projection, and archive. The deployment
stage writes intent containing service identity, receipt id, and merge SHA before updating the
`DeploymentRecord`; its receipt records the deterministic contribution key. Replay accepts an
already-present matching key and refuses a conflicting one, so a crash between publication receipt
and deployment contribution cannot strand merged deployable work. Each external stage records
intent before the effect and an observed immutable receipt afterward. Replay first reconciles the
deterministic branch, PR pair, check run, merge SHA, or deployment contribution; it never blindly
repeats an effect. This is the sole settlement state machine—events and UI labels are projections,
not additional completion flags.

It writes one `PublicationReceipt`:

```text
task/attempt/generation
branch, base_sha, head_sha
pr_number, pr_state
required_checks: names + one canonical verdict
local_candidate_test: null or command/result/candidate_sha
merge_requested, merge_hold
merged, merge_sha
main_check: null or exact run/check result for merge_sha
deployment_target: null or deployable service/repository identity
verified_at, verifier_version
```

Status, closeout, cleanup, and UI consume this receipt. They do not rediscover PR numbers or apply
different definitions of skipped/successful checks. Settlement may append a
`VerificationReceipt` to its journal before finalization; the final publication receipt itself is
immutable and references the exact verification inputs. The receipt is publication truth.
Whether its merge is currently deployed is derived from the separate deployment record and Git
ancestry, not written back into every task receipt.

A task normally has one publication attempt, but the architecture does not lie about a failure after
merge. A failed required main check or newly confirmed open finding leaves the original receipt
immutable, blocks the same logical task, and may resume that L2 on a fresh fetched base/new branch for
one corrective publication attempt. The new `worker_transition` journals fresh worktree/branch/base
registration and reconciliation before it installs that generation. Ordered receipt references replace heuristic PR-number
aggregation. No recursive healing task is created, and the final task result names the receipt that
actually satisfied completion.

### 10. Fault classification, incidents, and recovery

Evidence recording and operational actuation are separate operations. Every fault has a typed scope:

| Scope | Examples | Effect |
| --- | --- | --- |
| advisory | TTS/UI projection/telemetry/transcript snapshot/cleanup refusal | Record and display; no execution hold |
| task/project | failed resume or a task-worktree defect whose common Git/owner integrity is still proven | Block the affected task or project; other projects continue |
| provider | quota/capacity/provider sandbox unavailable with no escaped writer | Hold/reroute eligible work for that provider |
| global | containment not proven empty, an orphan writer can affect shared state, or durable control-plane integrity is uncertain | Publish global fuse before any further launch |

Classification is a small static mapping at each trusted boundary, not a central extensible rules
engine and not an L3 prompt. A worktree symptom becomes global if common-directory, protected-ref,
repository-alias, or other-owner integrity cannot still be proven. Each typed condition defines a
deterministic recheck where possible. Resolved advisory/task/project/provider conditions clear or
resume mechanically.

One global `operational-holds.json` registry is canonical for active task-reference, project, and
provider holds; task records reference an applicable operational hold rather than copying it. Each
entry has stable condition id, scope/subject, evidence reference, `retry_at`, attempts, and clear
recheck condition. Clearing appends an incident-ledger amendment referencing the hold/evidence id,
then removes the active entry; no second hold-history authority is created. A global condition
instead owns `recovery.json` with bounded evidence and one durable L3 attention request. Replaceable
quota observations can open/clear a provider hold through a trusted
command, but are not themselves control state. This gives restart-safe scope ownership without
recreating per-project mirror files.

L3 may inspect, perform brokered reconciliation, clear the fuse with a reason after health is proven,
or create the episode's single recovery L2 when code is required. The recovery L2 bypasses only the
faults it was explicitly created to repair; a new containment/ownership fault stops it too. A failed
recovery turn retries the same wake with backoff and does not file another incident or create another
task. Service restart/unmask remains outside L3 authority without Burak's separate authorization.

The one recovery-task slot uses `recovery_transition(task_claim)`: persist one deterministic task id in
`claiming`, create or reconcile exactly that task through the command layer, then finalize the
reference. A crash cannot lose the slot between two files or open permission for a second task.

Clearance uses `recovery_transition(clearance)` with an episode/revision-derived idempotency key.
It records intent in the still-active episode, appends or reconciles exactly one clearance receipt,
then removes `recovery.json`. A crash resumes those stages; `clearing` is an operation stage, not a
lasting cleared episode state.

Repository WIP and fault blast radius are distinct policies. A provider fault does not hold another
repository or a task already eligible for the other provider; the affected task is rerouted when
safe. Under recommended D1, an unresolved blocked code task still retains its own repository slot.
That conservative collision choice is visible and reviewable rather than misreported as a global
fault.

Incident storage becomes one append-only private structured ledger with an optional project/task
scope on each record. Project views, Markdown, UI rows, FYIs, and summaries are derived. An incident
never creates a task by itself. Corrections append a new entry with `amends_incident_id`, stable
evidence references, and author/time; they do not rewrite prior evidence or require Markdown parsing.

### 11. Transcripts and human conversation

Three data classes remain deliberately separate:

1. project chat: Burak/L3 conversation;
2. task conversation: Burak/current-L2 human-readable messages; and
3. diagnostic transcript: observable provider and platform events.

The live transcript endpoint derives source paths only from the current registered generation,
redacts credential-shaped material, bounds default tool output, and exposes raw observable records
only in opt-in diagnostic mode.

Portable snapshots occur at dispatch, worker replacement/resume, settlement, and archive, or append
incrementally. Ordinary task events do not trigger a full reread/rewrite/checksum of all provider
logs. Indefinite provider-native retention and portable export remain a review choice; live viewing
does not depend on them.

### 12. Read models, API, CLI, and UI

The control plane derives explicit read models:

- overview: projects, attention queue, concise FYIs, WIP, quota, global recovery;
- project: L3 summary, active task summaries, archive tail, project incidents;
- task: canonical state, conversation, publication receipt, current worker summary;
- monitor: provider quota/availability, sessions, service/recovery health; and
- diagnostic: events, raw state projections, transcript, maintenance leftovers.

Normal project/task responses do not include raw `STATE.md`, full event logs, internal counters, or
implementation records. Those move behind diagnostic endpoints/views.

Chat is the only high-level L3 intake. Task is the only full L2 conversation surface. Remove direct
web task creation that bypasses L3, manual dispatch, duplicate task-message composers, digest audio,
and dead administrative endpoints. CLI remains the operator interface, but internal worker actions
are clearly separated and use the same application commands as HTTP and brokers.

Remove public generic `task new`; it is an unnecessary second intake authority. If the service is
down, the operator restores it or records future work in GitHub rather than creating partially
managed runtime state offline. The only non-L3 task creation is the internal episode-bound
recovery transition, which still passes normal WIP/launch gates.

### 13. Deployment and maintenance

When a merged publication affects a running service, the applicable deployment-contribution stage
of settlement appends its immutable `PublicationReceipt` reference and merge SHA to that service's
single `DeploymentRecord`. It does not fast-forward the deployed checkout or restart anything. The
contribution is keyed by service, receipt id, and merge SHA and is idempotent across journal replay.
The deployment record owns:

```text
service/repository identity
activated_sha + successful ActivationReceipt
pending_target_sha + ordered contributing publication references
activation: null or fixed ActivationOperation
last_failed_candidate + bounded diagnostics
schema_version
```

`ActivationOperation` is the deployment subsystem's own closed crash state machine, not a sixth
generic journal variant. It records `attempt_id`, candidate/source/bundle/unit hashes, prior PID, and
fixed stages:

```text
claimed -> candidate_built -> quiesced -> old_service_stopped
        -> source_assets_installed -> new_service_started -> verified
        -> failed (from any stage, retaining last completed receipt)
```

Each stage records intent before its process/filesystem effect and the observed receipt after.
Restart reconciles the exact PID, checkout SHA, installed
hashes, and health before advancing; it never infers progress from the operator command's exit.
Pre-install failure may abandon staged files and clear the gate. Failure at or after
`old_service_stopped` retains the gate and continues forward/revert-fix under D5.

Several merges before a restart advance one pending target and retain every contributing receipt.
Activation of candidate `C` satisfies exactly the pending merges that are ancestors of `C`; a merge
that lands after `C` was claimed remains pending. Task receipts are never mutated from `pending` to
`activated`, so publication truth and deployment truth cannot disagree.

An explicitly authorized operator command owns source activation, web build, restart, and health
verification. The simplest acceptable first target is:

1. claim candidate `C`, set a durable activation gate, and prove all model/task mutations idle;
2. fetch a detached exact candidate, run preflight, and build/validate its web bundle and unit before
   changing the installed checkout;
3. quiesce timers, stop the old service, and prove its complete process unit empty;
4. fast-forward the installed checkout to `C` and install the already staged bundle/unit;
5. start the service once and verify a new PID, API, UI, schema, bundle hash, and source SHA;
6. append an `ActivationReceipt`, satisfy only contributing merges contained in `C`, and release the
   gate; or
7. if failure occurs after source activation, leave the service stopped and the target unsatisfied,
   with `state=failed` and diagnostics. Do not continue an old process against new dynamic assets or
   silently claim rollback.

A versioned-release/symlink design is justified only if automatic full-code rollback is a product
requirement; it must not be smuggled into this simplification as incidental machinery. Until then,
source rollback remains a reviewed revert PR, while the command may retain its current web-bundle
rollback before source activation. The default acceptance drill is failure plus reviewed revert/fix,
not automatic code rollback.

Worktree/ref cleanup becomes a separate conservative maintenance command or periodic non-blocking
collector. Automatic deletion is allowed only with an exact verified merged receipt, a clean owned
worktree, and a proven-stopped worker. Any uncertainty leaves the artifact and records maintenance
debt as an audit event/read-model item; it does not create another authoritative cleanup record and
never activates global recovery.

### 14. Canonical runtime artifacts

Target durable artifacts are intentionally few:

```text
ALTITUDE_HOME/
  projects.json                    # sole registration + repository/project policy authority
  operational-holds.json           # active task-ref/project/provider holds; absent/empty when clear
  recovery.json                    # absent when no global episode
  recovery-clearances.jsonl
  incidents.jsonl                 # canonical private evidence, project/task scope optional
  deployments/                    # state + append-only activation/failure receipts per service
  quota/                           # provider observations, replaceable cache
  <project>/
    l3.json                        # provider sessions; no compatibility mirrors
    chat.jsonl
    events.jsonl                   # project audit
    operations/                    # typed L3-turn and issue-publication journals
    inbox.jsonl                    # only if FYIs have a real read/ack lifecycle
    tasks/<slug>/
      task.json                    # canonical TaskRecord
      request.md + optional immutable external snapshot
      briefs/                      # immutable rendered execution contract by generation
      conversation.jsonl
      events.jsonl
      operations/                  # worker-transition and settlement journals
      worker/                      # provider-owned attempt records
      outcomes/                    # immutable untrusted WorkerOutcome by generation/effect id
      helpers/                     # bounded prompt/process/patch-or-findings/terminal receipts
      receipts/                    # immutable Publication/Verification/NoCode receipts by attempt
      progress.md                  # optional L2 checkpoint, not control state
      transcripts/                 # only per approved retention policy
    archive/<slug>/                # same terminal task directory
```

Remove derived `hold.json`, duplicated PR lists, edit counters, write-only digest files, session
mirrors, report variants, and multiple incident indexes after migration. `STATE.md` may remain as a
generated prompt cache, but is never read as authority and can be regenerated from canonical state.

## Consistency trace against desired mechanisms

| Desired mechanism | Target owner | Simplifications that must not violate it |
| --- | --- | --- |
| L3 high-level coordination | L3 + application commands | Removing direct web task creation reinforces this boundary |
| Direct L2 steering | Task conversation + owner generation | Generation consolidation preserves exact routing while removing duplicate checks |
| L2 autonomy / small direct tasks | L2 persona + normalized outcome | Schema reduction cannot reintroduce a mandatory helper/review pipeline |
| Optional L1/reviewer | Helper contract | Removing old L1 PR logic does not remove optional help |
| Collision avoidance | Worktree, WIP default 1, sole publisher, pinned PR | Path scheduler can shrink only after concurrency policy changes |
| PR reviewability | Trusted publication receipt | Removing `report.json` is safe only after receipt and independent verification exist |
| Dual providers / weekly quota | Route + provider adapters | Removing compatibility fields waits for state migration, not provider removal |
| Codex security boundary | Containment + inert broker | Schema simplification must remain strict and broker-side validation fail-closed |
| Incident learning | Canonical incident ledger | Fault scoping changes actuation, not evidence retention |
| Aggressive self-healing | Deterministic reconciliation + L3 global recovery | Fewer healing tasks means more direct reconciliation, not less recovery autonomy |
| Human-readable UI | Read models + Chat/Task | Diagnostics remain available but leave normal conversation surfaces |
| Exact transcript visibility | Live transcript projection | Snapshot cadence/retention can change without removing live observability |
| Explicit restart authority | Operator deployment command | Removing self-deploy makes the authorization boundary clearer |

## Explicit non-targets

- A microservice split.
- A database merely to replace small atomic local records.
- A general workflow DSL, intent classifier, or recovery-rule engine.
- Automatic GitHub backlog consumption.
- Mandatory decomposition, proposal, critique, or review stages.
- A new architecture persona or healing-agent hierarchy.
- Automatic restarts by L3 or L2.
- Removing a safety check before its replacement invariant is implemented and tested.
