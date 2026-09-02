# 02 - Proposed architecture

> Accepted target as of 2026-09-02 after adversarial re-review. Source and active documentation move
> module by module, but the frozen production service uses none
> of the new behavior until one separately authorized activation applies every pending real-state
> cutover.

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
6. **Provider mechanics stop at adapters.** Codex is the sole target launcher/resumer and exposes one
   worker state and terminal outcome; legacy Claude adapters are observation/cleanup compatibility only.
7. **Compatibility is a migration, not an architecture.** Every fallback reader has a versioned
   migration and deletion gate.
8. **Normal views are human views.** Technical event logs, raw state, and transcript detail remain
   opt-in diagnostics rather than the main conversation.

## Logical components

These are responsibility boundaries, not a requirement to create one file or service for each box.
The refactor should prefer fewer modules after responsibilities have been removed.

### 1. Application command layer

One trusted in-process command API owns all durable mutations whether the caller is HTTP, operator
CLI, or a Codex action broker. Commands include:

- register/remove project;
- create a task from a current L3 action or the active recovery episode's single task claim;
- record task conversation and steer/resume the current L2;
- reject, block, resume, or hold merge;
- apply a normalized worker outcome;
- record/classify fault evidence and clear recovery;
- record deployment contributions, qualification, and activation receipts; and
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
the gap. The shared append primitive holds the owning lock, detects and truncates an incomplete final
JSONL row before appending, writes one newline-delimited record, fsyncs the file, and fsyncs its parent
directory when the file was created or replaced. Kill tests cover state rename, a torn final row,
event append, directory durability, and reconciliation. HTTP and CLI code do no direct state
mutation. This removes current divergence such as project/task creation being implemented separately
in the server, CLI, and L3 broker.

A filesystem write can be atomic; a process launch, provider call, or GitHub effect cannot be part of
that same atomic write. The command layer therefore has one small shared stage/receipt primitive,
embedded in the record that already owns the lifecycle rather than stored as a free-standing workflow
authority. The target has exactly six named operation families: L3 turn, owner/helper worker
transition, task settlement, issue publication, recovery transition, and deployment transition.
Recovery task claim and clearance are fixed subtypes of the one recovery-transition family.
Deployment transition has source activation as its sole subtype. The migration keeps production
stopped, so deleting legacy self-deploy requires no temporary publisher or second gate claimant.
Settlement contains its commit/push/PR/check/merge/deployment-qualification stages; it
does not count each effect as another operation family. Every family has a fixed stage table,
stable operation/effect ids, deterministic targets where the external system permits them, and
bounded observed receipts. Before an external effect, the owning record persists intent; afterward it
persists the receipt. Crash recovery reconciles that exact target before retrying and never asks a
model to repeat a deterministic effect. No subsystem invents a timestamp lease or ad hoc “in
progress" flag after its operation has migrated.

“Mutable control authority” means a trusted command API that can change canonical lifecycle state or
authorize an external effect. It is distinct from the six operation kinds above. Append-only audit,
untrusted worker output, provider-native sessions, read models, and generated projections are not
control authorities. The target has exactly these six:

| Authority/domain | Sole trusted API | Canonical control record(s) | Retires |
| --- | --- | --- | --- |
| Project coordination | `ProjectCommands` | `projects.json`, project `l3.json`, issue-publication operation | direct server/CLI/broker project, L3-turn, and issue-draft writers |
| Task lifecycle | `TaskCommands` | task `task.json`, including settlement | direct task/status/conversation/report/land writers |
| Worker lifecycle | `WorkerCommands` | owner/helper worker bundles plus their embedded task transition | provider-specific dispatch/L1 launch, stop, and live-cache writers |
| Operational conditions | `ConditionCommands` | `operational-holds.json` | project hold mirrors, quota holds, fault-spool actuators |
| Global recovery and incidents | `RecoveryCommands` | `recovery.json`, clearance/incident ledgers | recovery hold/attention and duplicate incident writers |
| Deployment and activation | `DeploymentCommands` | per-service `DeploymentRecord` and activation receipts | self-deploy, restart-pending, and restart-script state writers |

Trusted Git/GitHub/service executors are bounded effects invoked by the owning task, project, or
deployment operation; they cannot initiate lifecycle changes and therefore are not a seventh control
authority.

Creation of a fault, incident, or operational hold is state-first and keyed. For an incident without
a hold, the canonical keyed incident row is appended before derived events or views. For a condition
that must hold execution, the hold/recovery record first commits the stable condition id, bounded
evidence, and keyed incident intent; the command then appends or reconciles the exact incident row and
audit event. A crash can delay the historical evidence-ledger row for a held condition, but it cannot
leave the unsafe condition permissive merely because an append failed.

### 2. Project coordinator (L3)

L3 remains a flexible conversation, not a workflow engine. It may:

- answer roadmap, architecture, prioritization, or status questions directly;
- create one well-briefed L2 task when Burak asks for execution;
- create a proposal task when Burak explicitly asks for design rather than implementation;
- surface project-level FYIs and choices;
- inspect and resolve operational recovery; and
- delegate the active recovery episode's one L2 if code is genuinely necessary.

L3 does not relay ordinary L2 questions, automatically pull backlog issues, close every clean task,
spawn L1s, or create a chain of healing work. Codex owns the sole resumable target session. A retained
legacy Claude session is evidence only and is never handed off or fabricated into a Codex resume.

All L3 turns are serialized across processes. Either only the service invokes them, with CLI calls
routed through the service, or an explicit offline operator command proves the service stopped and
acquires the same kernel file lock. The proposal selects the service as the ordinary mutation path;
an expiring owner file or process-local thread lock is not authority.

Each accepted human message has an id and one L3-turn operation embedded in `l3.json`, covering
provider selection, physical launch, native session result, response append, and any inert action
application. It records the daemon/supervisor instance that owns the attempt; a replacement instance
may reconcile it but cannot silently claim to be the original owner. On a crash, the named process
unit/session/result is reconciled and the response/action id is applied at most once. This preserves
separate provider sessions without pretending that a lock alone makes a network/model turn atomic.

### 3. Task and logical L2 owner

The canonical `TaskRecord` contains product state, not every derived observation:

```text
identity: project, slug, title, source
request: immutable request/snapshot reference
state: queued | running | settling | blocked | done | rejected
scope: normalized optional repo-relative publication paths
code_allowed, control_contract_version
provider_policy: requested preference + selected global policy version/hash
merge_hold: null or explicit reason
generation: null or OwnerGeneration
active_operation: null or embedded owner-worker-transition/settlement operation
publication_attempts: ordered immutable PublicationReceipt references
result: null or final PublicationReceipt/NoCodeReceipt reference
blocked: null or typed reason/question/attention/resume condition
terminal: null or at/actor/reason metadata
created, updated, schema_version
```

`done` and `rejected` are durable terminal states. Their directories move to the archive immediately
and do not remain in the active task list, but directory location is storage only and never lifecycle
authority. A copied or restored terminal record remains unambiguously done or rejected.

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
recovery_episode_id: null or active episode id
recovery_permit_revision: null or exact permitted episode revision
```

One locked `require_current_generation` operation is reused by messages, block/resume, outcome
application, helper launch, publication, and completion. Callers do not reimplement partial tuples.
The capability remains private and is never rendered in UI, transcripts, issues, PRs, or logs.
Every physical start or resume atomically installs a new generation after proving the prior physical
worker stopped. A genuine provider resume may keep `provider_session_id`, but it receives a new
`attempt_id`, `physical_worker_id`, and `capability_id`; every prior action is therefore stale. The
logical L2 still owns the task end to end across these serial generations.

The shared worker-transition operation covers both the logical owner and every helper. Its subject is
`owner` or a stable helper id. Before spawning, the owning TaskRecord or helper record stores the
target generation/helper id, deterministic process-unit name, provider request, and message id with
stage `planned`. The exact stage enum is `planned -> prior_stopped -> spawned -> bound ->
result_observed -> empty -> complete | failed`. Owner transitions occupy
`TaskRecord.active_operation`; concurrently launched
helpers are child transitions referenced by the active settlement and embedded in their helper
records. After a crash, Altitude inspects each named process unit and durable provider output before
deciding whether launch occurred; it never guesses from a timestamp or invokes resume twice.
Rejection and blocked transitions cannot finish until the owner and every task-owned helper process
unit are terminal/empty and their receipts reconcile.

### 4. Task lifecycle and settlement

```text
queued -> running -> settling -> done -> archive storage
             ^     |   |
             |     |   +-> blocked
             |     |          |
             +-----+----------+  (new fenced generation on continue/resume)

queued/running/blocked/settling -> rejected -> archive storage
```

- `queued`: durable request exists; no worker owns it.
- `running`: one logical L2 owns the task through exactly one current physical generation. A
  replacement preserves task ownership and genuine provider-session continuity, but installs a new
  generation only after proving the old physical writer stopped.
- `settling`: the physical worker has exited and one normalized outcome is being processed. The
  outcome is durably staged and idempotent, so a service restart resumes the same settlement without a
  model turn. A `continue` result, completed helper result, addressed finding, or recoverable
  post-merge failure may install a new generation and return the same logical task to `running`.
- `blocked`: a user choice, scoped operational condition, provider window, or publication hold
  prevents progress. The reason names who/what can unblock it. A blocked task has no physical worker
  that can still write; the transition completes only after stop/containment proof. If that proof is
  unavailable, ownership is uncertain and the appropriate safety fuse remains active.
- `done`: a final trusted publication/no-code result and terminal metadata are durable.
- `rejected`: terminal metadata, including reason and actor, are durable; no final delivery receipt is
  implied.
- archive storage: a terminal task leaves normal active context without changing its state.

Rejection from a live state follows the same stop-before-terminal rule. The diagram describes
product state, not permission to move or delete a task directory while any L2/helper writer may
still exist.

The current `reported` state, `status.json.verified`, `l3_handled`, `completion_requested`, and stranded
report promotion are replaced by `settling` plus one embedded idempotent settlement operation. L3 is
notified only when the outcome actually contains a project-level decision, FYI, follow-up proposal,
open finding, or merge hold. Clean verified completion closes mechanically.

Human attention is explicit rather than another closeout flag. A decision, question, open finding,
or merge hold becomes a typed open attention item referenced by the blocked task; resolving it records
the decision/message id and either resumes settlement or installs a new worker generation. A
non-blocking FYI or follow-up proposal is appended once to canonical project/task events and may be
archived with the task. Project attention views are derived from open task attention plus those
events. Under D7 there is no separate durable Inbox/read-ack lifecycle and no `l3_handled` truth to
get stranded after a crash.

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

- Phase 1A could not prove foreground Claude ownership, genuine resume, result spooling, deterministic
  stop, and cgroup emptiness locally without spending a provider turn. The accepted target is therefore
  Codex-only for every autonomous L3, L2, and managed-helper turn. No source path launches or resumes
  Claude, and no provider-job row or parent exit is accepted as process-empty evidence. Legacy Claude
  guards/backend checks remain only for stopped-state observation and cleanup until the activation gate
  proves every legacy unit/process empty; later enablement requires a separate architecture amendment.
- Codex remains in an explicit permission profile and transient cgroup with scrubbed environment;
  no trusted action is interpreted until the entire containment unit is empty.

Routing records Codex weekly and short-window observations without inventing exhaustion from unknown
telemetry. An actual quota/capacity failure opens one provider hold with its retry condition. Claude
quota may remain visible as legacy observation but is never eligibility; explicit Claude pins fail
closed rather than falling through. L3, L2, and optional helpers use the same Codex-only capability
fact, without a conditional dual-provider branch.

The enabled Codex adapter produces the untrusted internal `WorkerOutcome`. The request may be persisted
before exit, but trusted settlement cannot act on it until the registered physical process unit is
proven empty:

```text
publish(commit_message, optional_pr_title, request_merge, evidence)
complete_no_code(digest, evidence)
block(reason_or_question, resume_condition)
continue(reason, optional_helper_requests)
```

Every variant also carries one required, closed `observations` block: typed review findings and
dispositions; decisions/questions; FYIs; follow-up proposals; deviations; normalized usage and
spend; and the worker's observed merge-hold reason. These values are untrusted settlement input,
not effect or control authority. Trusted code derives the outcome/effect id and re-reads canonical
task hold state; the model cannot supply either identity or verification/publication/deployment
facts. Human reply text stays exclusively in the fenced task conversation rather than being hidden
inside outcome authority.

These are model-declared outcomes, not the complete worker-status taxonomy. The adapter separately
normalizes `live`, `clean_exit_without_outcome`, `quota_limited`, `capacity_limited`,
`provider_failed`, `malformed_outcome`, `process_missing`, and `ownership_uncertain`. Typed command
policy then either reroutes/resumes the same logical task, blocks it with evidence and a retry
condition, or activates the global fuse when another writer cannot be ruled out. An abnormal exit is
never fabricated into a model outcome.

The transport may differ, but both produce an explicit versioned JSON wire envelope with a checked-in
schema. Python producers validate before persisting; Python consumers validate before acting; the
TypeScript client uses an explicit Zod runtime validator at the API boundary.
Codex returns the inert envelope directly. Legacy Claude outcome/report adapters remain read-only only
as long as archived/stopped-state compatibility requires them; they are not target writers. Claude's
shell guard cannot be deleted before every legacy unit/process is proven empty and its retained evidence
has a replacement reader.

Steering an L2 stops its current physical process and resumes the same Codex session. It installs a
new fenced generation but does not replay the entire transcript as a fabricated prompt. Intentional
context rotation creates a new Codex session with a bounded human-readable handoff and is displayed as such. Context and
quota percentages are timestamped provider observations, not task-state authority; revised provider
measurements may legitimately move upward or downward without implying that the task changed
sessions.

In the target state no Claude worker edits, replies, publishes, completes, or launches a helper. Every
legacy Claude task/publication target is held before effects, but same-UID CLI actor/environment values
are not an operator security boundary. The source may be activated only after the stopped-production
gate proves every legacy Claude unit/process empty; this prerequisite, plus removal of every Claude
launch path, is the honest closure for global CLI commands that have no task target.

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
records and their child worker transitions idempotently, and waits without a live L2 writer. Each
helper record uses the same exact transition as an owner—`planned -> prior_stopped -> spawned ->
bound -> result_observed -> empty -> complete | failed`—around its named process unit; several
helpers may progress independently while the parent settlement remains the one
top-level task operation. Completed patch/findings receipts are attached to that settlement; Altitude
then installs a new L2 generation with their concise results and returns the task to `running`.
Helper records own their prompt, parent generation/commit, process unit, patch or findings, and
terminal receipt. They never own a PR or task completion.
Rejection or a move to terminal archive storage stops or reconciles every owned helper unit before
the task leaves the active set.

### 7. Scope and concurrency

Default repository WIP is one L2. This is especially important for Altitude while it is self-hosting.
Parallelism remains available across repositories and inside a task through optional helpers.

Under the selected D1 policy, a blocked code task retains the repository slot because its branch
and unresolved publication still belong to that logical owner. It must be resumed, completed,
rejected, or explicitly converted to a GitHub issue before another top-level L2 starts. Altitude
does not silently park work and pretend its collision risk disappeared. This conservative rule can
be amended during review, but doing so requires an explicit multi-branch conflict policy.

One narrowly defined exception exists for an active global recovery episode. Trusted reconciliation
must first bring any ordinary settlement/external effect to an unambiguous stable boundary;
a task with unresolved commit/push/PR/merge state cannot be preempted. After every ordinary
worker/helper is checkpointed, stopped, and proven empty, the task becomes `state=blocked` with
`blocked.kind=preempted_by_episode`, and the episode may temporarily assign the repository slot to
its single recovery L2. The ordinary branch/session are preserved but cannot resume, settle, or
publish. After clearance, a new owner worker transition owns base/worktree revalidation and fresh
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
already explicit instruction. The keyed issue-publication operation binds repository, content hash,
draft or user-message authority, and one stable effect id. The broker places a stable, non-secret effect
marker in the issue body and records the exact repository plus content hash before creation. After an
ambiguous response or crash it searches and polls that marker before any retry; if uniqueness cannot
be proved, it blocks for reconciliation rather than risking a duplicate issue.

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

The task's embedded settlement operation makes the non-atomic transaction explicit. It is keyed by
outcome id and owner generation and has fixed stages for final-worktree snapshot,
verification/review, commit, push, PR discovery/creation, head/base revalidation, checks, merge,
post-merge observation, final receipt, deployment contribution/qualification, attention projection,
terminal state, and archive storage. A deterministic branch name, commit provenance, and stable,
non-secret marker in every created PR bind the remote effects to the task, settlement, base, and
expected head; an ambiguous response is reconciled by those markers and exact remote identity before
any repeat.

The deployment stage writes intent containing service identity, receipt id, merge SHA, and initial
qualification before updating the `DeploymentRecord`; its receipt records the deterministic
contribution key. A merge contribution says only that the SHA exists in remote history. Deployment
qualification separately says whether the contribution is eligible, blocked by a failed main check
or open finding, or superseded by a named corrective receipt. Replay accepts an already-present
matching key and refuses a conflict, so a crash between publication receipt and deployment update
cannot strand or silently qualify merged work. Each external stage records intent before the effect
and an observed immutable receipt afterward. Replay first reconciles the deterministic branch, PR
pair, check run, merge SHA, or deployment key; it never blindly repeats an effect. This is the sole
task settlement state machine—events and UI labels are projections, not additional completion flags.

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
`VerificationReceipt` to its staged receipts before finalization; the final publication receipt
itself is immutable and references the exact verification inputs. The receipt is publication truth.
Whether its merge is currently deployed is derived from the separate deployment record and Git
ancestry, not written back into every task receipt.

A task normally has one publication attempt, but the architecture does not lie about a failure after
merge. A failed required main check or newly confirmed open finding leaves the original receipt
immutable, marks its deployment contribution unqualified, blocks the same logical task, and may
resume that L2 on a fresh fetched base/new branch for one corrective publication attempt. The new
owner worker transition records fresh worktree/branch/base registration and reconciliation before it
installs that generation. A successful corrective receipt explicitly supersedes the named blocker and
qualifies the resulting remote-main target; it never rewrites the earlier receipt. Ordered receipt
references replace heuristic PR-number aggregation. No recursive healing task is created, and the
final task result names the receipt that actually satisfied completion.

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
recheck condition. Opening a condition first commits the keyed hold under the control lock and only
then appends/reconciles its incident and audit evidence. Clearing first records keyed clear intent,
appends or reconciles the incident-ledger amendment, and then removes the active entry; no second
hold-history authority is created. A global condition similarly publishes `recovery.json` before any
incident projection, with bounded evidence and one durable L3 attention request. Replaceable quota
observations are evidence, not control state: unknown/stale quota does not open a hold, while an
observed quota/capacity failure can open one through a trusted command. This gives restart-safe scope
ownership without recreating per-project mirror files.

L3 may inspect, perform brokered reconciliation, clear the fuse with a reason after health is proven,
or create the episode's single recovery L2 when code is required. The recovery L2 bypasses only the
faults it was explicitly created to repair; a new containment/ownership fault stops it too. A failed
recovery turn retries the same wake with backoff and does not file another incident or create another
task. Service restart/unmask remains outside L3 authority without Burak's separate authorization.

The episode records an immutable `supervisor_project` selecting the one L3 project conversation for
its lifetime. Each revision also records one logical wake id, durable `retry_at`, capped exponential
backoff, and no total-attempt cap. Separately, each recovery turn records at most one replaceable
physical claim identity consisting of service instance, claim id, logical wake id, episode revision,
and attempt. Only that physical identity may complete or fail the current turn; a new service
instance may mechanically mark an objectively abandoned claim, preserve the same logical wake,
advance attempt/backoff, and claim only when `retry_at` arrives. It cannot acknowledge work it did
not observe or silently change supervisor project.

The embedded recovery task-claim operation persists one deterministic task id in `claiming`, creates
or reconciles exactly that task through the command layer, then finalizes the reference. A crash
cannot lose the slot between two files or open permission for a second task. Every recovery owner
generation records the episode id and exact permit revision. Final launch and every message, helper,
outcome, publication, and completion command compare both values with the active episode. Material
new evidence increments the episode revision and immediately makes the old generation stale; renewal
requires objective revalidation and a new fenced generation.

The embedded recovery-clearance operation uses an episode/revision-derived idempotency key. It
records intent in the still-active episode, appends or reconciles exactly one clearance receipt, then
removes `recovery.json`. A crash resumes those stages; `clearing` is an operation stage, not a lasting
cleared episode state.

`waiting_operator` permits only named read-only mechanical probes and reconciliation of already
recorded effects. It cannot run another model turn, start/resume a worker, publish, mutate source or
state beyond probe receipts, or perform service lifecycle work. The episode stores the exact
`waiting_revision`. Human input, materially new fault evidence, a changed probe result, or a new
deployment/activation receipt increments the episode revision and mechanically moves it to `open`;
that new revision may wake its supervisor L3 once. An unchanged probe never changes revision or
spends a model turn. A separately authorized operator action may also resolve/clear the episode.

Repository WIP and fault blast radius are distinct policies. A Codex provider fault does not hold
another repository. A disabled legacy Claude row consumes no runnable-provider WIP, while its exact
normalized narrow path lease remains protected. Missing, empty, malformed, or wholly narrowed-away
scope is repository-uncertain and blocks new repository mutation without becoming a global hold;
uncertain physical ownership also remains protected from mutation or cleanup.
Under D1, an unresolved blocked Codex task still retains its own repository slot.
That conservative collision choice is visible and reviewable rather than misreported as a global
fault.

Incident storage becomes one append-only private structured ledger with an optional project/task
scope on each record. A new immutable UUID is canonical; `(project_id, legacy_incident_id)` is a
display/migration alias so per-project `I-001` values cannot collide. Project views, Markdown, UI
rows, FYIs, and summaries are derived. An incident never creates a task by itself. Corrections append
a new entry with `amends_incident_id`, stable evidence references, and author/time; they do not
rewrite prior evidence or require Markdown parsing.

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
logs. Under D2, private provider-native evidence remains with the archived task until an operator
deletes that archive; no independent retention scheduler is added. Live viewing does not depend on
portable export.

### 12. Read models, API, CLI, and UI

The control plane derives explicit read models:

- overview: projects, attention queue, concise FYIs, WIP, quota, global recovery;
- project: L3 summary, active task summaries, archive tail, project incidents;
- task: canonical state, conversation, publication receipt, current worker summary;
- monitor: provider quota/availability, sessions, service/recovery health; and
- diagnostic: events, raw state projections, transcript, maintenance leftovers.

Every HTTP and streamed event payload has an explicit versioned JSON wire schema. Python response
producers validate the payload against that schema before serving it; TypeScript consumers validate
at runtime rather than relying on compile-time types or permissive field picking. Schema generation
is optional, but shared fixtures must prove the Python producer and TypeScript validator accept and
reject the same contract before a wire-version change lands. Internal Python dataclasses are not
silently treated as the network schema.

Normal project/task responses do not include raw `STATE.md`, full event logs, internal counters, or
implementation records. Those move behind diagnostic endpoints/views.

Chat is the only high-level L3 intake. Task is the only full L2 conversation surface. Remove direct
web task creation that bypasses L3, manual dispatch, duplicate task-message composers, digest audio,
and dead administrative endpoints. CLI remains the operator interface, but internal worker actions
are clearly separated and use the same application commands as HTTP and brokers.

Remove public generic `task new`; it is an unnecessary second intake authority. If the service is
down, the operator restores it or records future work in GitHub rather than creating partially
managed runtime state offline. The only non-L3 task creation is the internal episode-bound recovery
task-claim operation, which still passes normal WIP/launch gates.

### 13. Deployment and maintenance

When a merged publication affects a running service, the applicable deployment-contribution stage
of settlement appends its immutable `PublicationReceipt` reference and merge SHA to that service's
single `DeploymentRecord`. It does not fast-forward the deployed checkout or restart anything. The
contribution is keyed by service, receipt id, and merge SHA and is idempotent across settlement
replay. Merge presence and deployment qualification are deliberately separate: a known failed main
check, open finding, or unresolved corrective attempt remains an activation blocker even though its
merge is present in remote history.
The deployment record owns:

```text
service/repository identity
bootstrap anchor: frozen source/state evidence used only before the first activation
activated_sha + successful ActivationReceipt
ordered merge contributions: receipt/merge SHA
qualification: eligible receipts + blockers + explicit superseding corrective receipts
ancestry coverage: qualified contribution or immutable operator-provenance receipt for every commit
deployment_operation: null or embedded ActivationOperation
last_failed_candidate + bounded diagnostics
schema_version
```

Phase 2A introduces only the baseline subset: strict service/repository identity, exactly one immutable
bootstrap-anchor variant, nullable `activated_sha`, bounded `legacy_pending`, schema/hash, and one
per-service lock. An exact stable `running_install` manifest is the sole source allowed to seed
`activated_sha`; a stopped bootstrap uses an operator-provenance anchor bound to the frozen stopped
manifest/state/raw evidence and leaves `activated_sha=null`. Unknown identity refuses initialization.
The legacy restart marker remains a blocker/evidence hash only. No Phase 2A module observes runtime state
itself or contains contribution, qualification, maintenance, activation, build, install, stop/start, or
health behavior; those fields and writers arrive only in their named later phases.

The embedded `ActivationOperation` is the final deployment-transition operation. Activation records
`attempt_id`, the bounded operator authorization receipt, the exact SHA selected later at the
post-gate resolution boundary, prior PID, and three non-interchangeable identity positions. The prior
position is a post-gate `running_install` for a live old service or, only for the already-stopped first
activation, an exact bootstrap `stopped_install` bound to the immutable freeze/provenance receipt.
Inactive `stopped_install` B is then revalidated byte-for-byte without executing old code, and
`detached_candidate` C is pinned to the chosen commit. It records each manifest/receipt hash,
source/bundle/unit hashes, and the intended B-to-C transition before using these fixed stages:

```text
claimed -> gate_acknowledged -> prior_install_recorded -> restart_policy_suppressed
        -> candidate_resolved -> candidate_built -> remote_revalidated
        -> prior_install_revalidated -> old_service_stopped -> stopped_install_revalidated
        -> source_assets_installed -> state_cutovers_applied
        -> candidate_started_health_only -> verified -> verification_recorded
        -> restart_policy_restored -> activation_recorded -> complete
        -> failed_released (only before old_service_stopped; old service/source unchanged)
        -> failed_held (after suppressed old-identity loss or old_service_stopped; retaining evidence and gate)
```

After `restart_policy_restored` but before gate release, an unexpected candidate exit is a receipted
bounded systemd restart generation, not an unobserved second start: the still-held gate forces the
new PID into health-only mode, the operation returns to `candidate_started_health_only`, and full
PID/source/schema/API/UI verification must pass again. The ActivationOperation itself persists
`candidate_generation` and enforces an absolute maximum of three total candidate generations for the
attempt, regardless of elapsed time. The repository-owned unit additionally fixes and the receipt
hashes `StartLimitIntervalSec=120`, `StartLimitBurst=3`, and `RestartSec=5` as defense in depth; the
runner resets the counter immediately before the first candidate start and records that effect.
Either limit transitions to `failed_held` with the service stopped. Fault tests cover counter reset,
every generation, burst exhaustion, and slow-spaced crashes beyond 120 seconds.

`state_cutovers_applied` means every selected importer has reconciled its stable domain receipt under
the still-held gate; it does not enable normal readers or writers. A crash re-enters the detached
candidate tool, verifies the installed SHA and each domain receipt, and resumes the first incomplete
cutover. No service starts while a selected cutover is partial or unknown.

The first activation initializes deployment authority rather than assuming it already exists. Under
the deployment lock, `claimed` requires an absent DeploymentRecord plus exact frozen state,
bootstrap stopped-install/process-empty evidence, a stable authorization/attempt id, and a read-only
preclaim remote target. One atomic replace creates the bootstrap anchor, exact operator-provenance
coverage through that target, held maintenance latch, and one ActivationOperation. Retrying the same
authorization reconciles that record; any different initializer or mismatched byte/hash refuses. If
the post-gate latest target advances, `candidate_resolved` appends exact additional coverage under
the same bounded “latest at revalidation” authorization; it never changes the anchor.

`verification_recorded` is only pending evidence. `activation_recorded` is one atomic
DeploymentRecord replacement that sets `activated_sha`, appends the successful ActivationReceipt and
any domain-cutover receipts, satisfies the covered contributions, enables mutations/timers, and
releases the maintenance gate. Before that atomic replace none of those success facts is visible; a
retained `failed_held` operation can contain verification evidence but never a successful receipt.

Each stage records intent before its process/filesystem effect and the observed receipt after.
Optional selected-domain cutovers are closed fields of this activation, not another operation kind.
They cover project/L3 session and pending-action reconciliation, event projections, task,
operational-hold, recovery, incident import, and transcript-bundle migrations as applicable and record
domain, old/new schema, drained/reconciled-domain evidence, candidate SHA, imported-row count/hash,
and derive progress from the parent stages: intent at `remote_revalidated`, stopped/empty proof at
`old_service_stopped`, applied/reconciled evidence at `state_cutovers_applied`, compatibility at
`verified`, and immutable cutover receipt at `activation_recorded`. Crash replay uses those same
receipts and never selects runtime format from the audit receipt.
The operator may stage a candidate speculatively, but after acquiring the maintenance gate it fetches
and verifies the then-latest `origin/main`, persists that exact SHA at `candidate_resolved`, and
rebuilds if the speculative SHA differed. Immediately before source change it fetches once more: a
changed SHA restarts resolution/build inside the same bounded preparation; an unchanged SHA records
`remote_revalidated`. Historical-SHA activation is not supported. The gate prevents Altitude's own
publication/state mutation, not external GitHub writers. A merge that occurs after the final
revalidation is simply pending for the next activation; “latest” means latest mechanically observed
at that explicit boundary. Restart
reconciles the exact PID, checkout SHA, installed hashes, and health before advancing; it never infers
progress from the operator command's exit.

The maintenance gate is checked at every durable mutation, model-turn start, worker/helper launch,
settlement/publication stage, recovery actuation, timer claim, and maintenance boundary. Once set,
new work refuses or waits; already-running work is stopped or reconciled to an idle boundary before
activation proceeds. Read-only status and the activation operation's own receipts remain available.
Several merges before authorization contribute to the single latest remote target, but activation
refuses while any included contribution has an unresolved qualification blocker or any remote-main
change since the ancestry anchor lacks a qualified contribution or immutable operator-provenance
receipt. After the first activation the anchor is `activated_sha`. Before it, a one-time bootstrap
anchor comes from the frozen pre-stop loaded manifest; if that observation was unavailable, an exact
operator-provenance receipt must name the stopped checkout SHA, state hash, evidence limitation, and
authorization without pretending it was an ActivationReceipt. An unknown/ambiguous anchor refuses.
Coverage is checked for every commit in the first-parent ancestry range from that anchor through the
candidate, not merely for contributions Altitude already knows about. Operator-provenance
receipts live immutably inside the deployment artifact family, name the exact commit/range and
authorization, and never imply successful checks that were not mechanically observed.
Task receipts are never mutated to carry activation state; deployment truth references them and
records the one exact activated SHA.

An explicitly authorized operator command owns source activation, web build, restart, and health
verification. A stable bootstrap locates and invokes the detached candidate deploy tool before
importing the installed checkout's Altitude package, so a broken installed Makefile/module cannot
block forward repair. Normal supervisor restart-on-crash remains enabled, but the activation attempt
temporarily suppresses restart loops until exact candidate health succeeds. The acceptable target is:

1. set the durable maintenance gate and prove every mutation/model/timer/worker/helper boundary idle;
   then capture the current `running_install` receipt while the quiesced old service remains up, or,
   for the already-stopped first activation, capture an exact `stopped_install` receipt and bind it to
   the frozen source/state/unit/process-empty evidence plus its explicit operator-provenance limitation;
2. immediately suppress old-service restart after recording prior-install A; then resolve/build the
   latest observed remote main, reject unresolved deployment blockers, and revalidate the remote SHA;
3. revalidate the exact old PID/start/source identity, then stop the old service and prove its
   complete process unit empty;
4. capture `stopped_install` B and prove its installed bytes/configuration still match the prior
   install receipt (running normally, bootstrap freeze for the first activation), then fast-forward
   the installed checkout to the revalidated SHA and install the staged bundle/unit;
5. run every selected state importer from detached C, reconcile each stable domain receipt, and only
   then start the first service generation in health-only mode and verify its PID, API, UI, schema, bundle hash, and
   source SHA while timers/mutations remain disabled;
6. persist pending verification evidence and restore normal crash restart while the gate still
   forces health-only behavior;
7. if systemd starts a new PID before release, record that bounded generation and repeat the full
   health-only verification; only the currently verified PID may then atomically record activated
   SHA/success receipt/contribution satisfaction, enable mutations/timers, and release the gate; or
8. if preparation fails before old-service stop, restore its restart policy and atomically record
   `failed_released` plus gate release only after verifying the unchanged/current old PID/source; for
   the already-stopped first activation, instead prove unchanged stopped A/B and frozen state and
   leave the service stopped. If the old PID disappears or changes after restart suppression, do not
   release: durably record the mismatch, issue an idempotent stop, prove the complete unit empty,
   record `old_service_stopped(reason=identity_lost)`, and end `failed_held` with restart still
   suppressed. Otherwise, if verification
   fails or StartLimit is exhausted after stop, leave the service stopped and target unsatisfied with
   `state=failed_held`. Do not continue an old process against new dynamic assets or
   silently claim rollback.

One authorization is one activation attempt with one deliberate first start. A bounded systemd
restart after policy restoration is a new receipted physical generation in that same attempt, not
another operator start; the gate keeps it health-only and requires full re-verification. A failed
attempt before old-service stop releases safely after proving the old instance unchanged; a failure
from old-service stop onward is terminal, stops the service, and retains the gate. Repair is forward-only: a reviewed
fix or revert lands on `origin/main`, and a new explicit operator authorization creates a new attempt
against that latest verified SHA. It names `repair_of=<failed attempt>`, proves the prior claimant and
all service processes empty, reconciles installed SHA plus every applied cutover receipt, and uses one
fixed broker capability to atomically transfer the still-held gate owner to the new attempt without a
release window. Missing/ambiguous evidence refuses. The old attempt remains terminal and its
three-generation cap cannot be reset except by this new explicit authorization. Pre-install failure
may discard staged files; it does not authorize reuse of the old attempt.

A versioned-release/symlink design is rejected for this simplification. Before source activation the
runner may discard staged source/assets. After source activation, repair is a reviewed
state-compatible forward fix or behavior revert that retains every selected reader; old code that
cannot read current state is not a rollback. The acceptance drill is failed candidate plus detached
forward repair, not automatic full-code rollback.

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
  operational-holds.json           # keyed active task-ref/project/provider holds; absent/empty when clear
  recovery.json                    # episode + supervisor/claim/clearance operations; absent when clear
  monitor/recovery-hold.lock       # retain current recovery lock inode/path; no split-lock rename
  monitor/recovery-launch.lock     # retain current launch lock inode/path
  recovery-clearances.jsonl
  incidents.jsonl                 # canonical private evidence, project/task scope optional
  deployments/                    # records plus new per-service activation lock
  quota/                           # provider observations, replaceable cache
  tls/                             # Altitude-owned certificate, key, and CA lifecycle
  <project>/
    .lock                          # retain current project lock inode/path
    .publication-settlement.lock   # retain current publication lock inode/path
    l3.json                        # provider sessions + embedded active L3 turn; no compatibility mirrors
    chat.jsonl
    events.jsonl                   # project audit
    issue-publications/            # the sole project-level operation records outside l3.json
    tasks/<slug>/
      .resume.lock                 # retain current task-resume lock inode/path
      task.json                    # canonical TaskRecord + embedded active owner/settlement operation
      request.md + optional immutable external snapshot
      briefs/                      # immutable rendered execution contract by generation
      conversation.jsonl
      events.jsonl
      worker/                      # provider-owned attempt records
      outcomes/                    # immutable untrusted WorkerOutcome by generation/effect id
      helpers/                     # bounded prompt + embedded worker transition + result receipt
      receipts/                    # immutable Publication/Verification/NoCode receipts by attempt
      progress.md                  # optional L2 checkpoint, not control state
      transcripts/                 # only per approved retention policy
    archive/<slug>/                # same terminal task lifecycle; v2 or isolated read-only legacy bundle
```

Existing helper-run `.lock` files remain colocated too. These fixed colocated flock patterns and the
new deployment lock are counted as one synchronization-lock family because they share the same
process-lifetime/empty-file contract. No PR renames an existing lock while an old writer can still
hold its inode; changing a lock path would require a separately reviewed stopped/gated all-writer
cutover and is not part of this simplification.

Repository worktrees, branches, task refs, and Git common-directory registrations remain external
Git artifacts referenced by the task-record/archive family; provider-native stores and the system
journal remain external owner stores rather than Altitude control state.

Remove derived `hold.json`, duplicated PR lists, edit counters, write-only digest files, session
mirrors, report variants, multiple incident indexes, persisted `STATE.md`, and the duplicate
`altd.log` after in-memory prompt and system-journal parity are proved.

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
