# Migration and validation plan

This plan moves Altitude from the current overlapping safety mechanisms to the reduced architecture
without weakening the properties that matter: one logical owner of a task, an isolated writer,
trusted publication, fail-closed recovery, and operator-controlled deployment. It is deliberately
incremental, but it is not a long-lived dual architecture. Each phase introduces one complete
compatibility boundary, proves it in a release, and deletes the superseded path only after an
explicit gate.

The autonomy boundary is normative throughout this plan:

- L3 may diagnose and mechanically recover an episode, clear it after objective probes pass, or
  delegate that episode's one recovery L2 without asking Burak first.
- An episode may never create a second recovery task, even if the first task fails, is rejected, or
  uncovers another fault. A fault caused by recovery is evidence in the same episode, not a new
  healing workflow.
- Neither L3, L2, a recovery task, a timer, nor a merge may restart or replace the running service.
  They may record that an operator restart is required. Only a separately authorized operator
  deployment may perform the restart. It must verify the activated source and web bundle and follow
  the rollback policy Burak selects in D5.

## Safety properties that may not regress

The migration is stopped if any phase cannot preserve all of these invariants.

1. **One writer.** A task has one logical L2 owner. Replacement stops and proves the old physical
   worker dead before starting the new worker. Dispatch, session, worker, and capability identities
   fence every message and state-changing action.
2. **Repository isolation.** Code work happens only in the task's registered worktree and branch.
   The Git common directory and protected branch remain outside a model's publication authority.
   Every accepted commit has task provenance and every change reaches `main` through the guarded PR
   path.
3. **Trusted effects.** Model output is a request, not authority. The trusted backend revalidates
   current identity, task state, publication scope, merge policy, and recovery state immediately
   before an effect. Contained workers must be empty before their output is interpreted.
4. **Atomic durable state.** Runtime records are written atomically under the appropriate
   cross-process lock. Readers either understand a record's schema version or refuse it; they never
   guess through a partial migration.
5. **Fail-closed launches.** A global recovery episode is published before launch settlement and is
   checked at the last worker-launch boundary. A task- or provider-scoped fault blocks only its
   affected scope, but may not accidentally become permissive.
6. **Non-fanning recovery.** One episode has one durable supervisor, at most one recovery attempt in
   flight, capped retry delay, and at most one recovery task ever. Persistent retry remains durable
   and visible; new evidence cannot fan out sessions or tasks.
7. **No autonomous service lifecycle.** Merge may add a pending DeploymentRecord contribution; it cannot pull the live
   source tree or restart the service. Deployment requires an operator action, an idle proof, an
   exact remote source candidate, a staged web bundle, a new healthy PID, and the selected D5
   rollback policy.
8. **Conservative cleanup.** Failure to prove that an artifact is dead leaves it in place. Cleanup
   uncertainty cannot delete another task's work and is not itself a global system fault.
9. **Audit continuity.** Existing task, transcript, incident, fault, clearance, PR, and deployment
   evidence is retained. Simplifying the active representation must not rewrite historical facts.

## Migration rules

These rules prevent a release from combining half of the old behavior with half of the new one.

- A state writer changes only after the same release contains its new reader, validator, and
  one-shot importer. Dual-read is allowed for one compatibility window; dual-write is not. An
  importer stages complete side-by-side v2 files, fsyncs them, records counts/hashes, and atomically
  updates the affected domain entry in one `state-formats.json` selector map last while the service
  is quiescent. Domains such as `tasks` and `recovery_incidents` can cut over in different phases
  without deselecting each other. Distributed files are not falsely described as one atomic write.
- Before an authoritative schema cutover, deploy a compatibility release that can validate/read the
  new generation while it still writes the old one. The later cutover runs with service mutations
  and timers stopped. The candidate first starts in migration-hold mode: it validates staged v2
  directly but cannot run timers, providers, or mutations. Switching the selector is itself the
  first authoritative v2 mutation and the forward-only boundary. Before it, v1 remains selected;
  whether prior code can resume still depends on D5 (recommended exact-checkout activation may need
  a reviewed revert/fix, while versioned releases may restore it). After it, restart/recovery must
  continue on v2 and never roll state back by dropping facts.
- A broker contract is selected by the task's persisted `control_contract_version`, never inferred
  from which code happens to be running. An old contract is not resumed by a release that has
  removed its enforcement.
- Small preparatory PRs are encouraged when they are dormant or behavior-preserving: characterization
  tests, readers, typed definitions, and migration dry-runs may land independently. One narrow writer
  cutover then switches schema, backend, CLI/API/UI consumers, tests, and documentation together.
  A compatibility reader may survive one later phase; the old writer may not. This avoids both giant
  implementation PRs and two simultaneously active architectures.
- A source merge is not activation. A phase becomes active only when an operator activates an exact
  remote commit and proves the new process. Under recommended D5, candidate assets are built from a
  detached checkout, then the old service is stopped before the installed checkout changes; a failed
  post-swap activation leaves the service stopped rather than running a mixed release. If D5 selects
  versioned releases, source and assets instead switch as one immutable unit.
- Destructive compatibility deletion is always a separate release from the behavioral cutover.
  It requires the deletion gates in this document and retained deployable rollback/revert input.
- Feature flags are allowed only for a short, explicit shadow comparison. They may not leave two
  state machines accepting mutations. A cutover flag is persisted once under the migration lock,
  not selected independently in each process.

### Dependency order

| Phase | Requires | Why the dependency is hard |
| --- | --- | --- |
| 0. Baseline | recorded D1-D7 choices and S01-S15 dispositions | A migrator cannot safely infer product policy or approved scope from legacy fields |
| 1. Publication/deployment activation boundary | Phase 0 | Later merges must have trusted receipts and stop mutating the running checkout automatically |
| 2. Commands/task/outcome | Phase 1 | Canonical task state can consume the already trusted publication/deployment boundary in one held cutover |
| 3. Repository WIP/scope | Phase 2 and accepted D1 | Predictive path scheduling cannot disappear before canonical publication scope and sole settlement exist |
| 4. Typed operations | Phase 2 | Deterministic retry needs the idempotent outcome/receipt journal |
| 5. Recovery v2 | Phases 2 and 4 | Fault scope cannot narrow until every caller has a typed conservative classification |
| 6. Cleanup | Phases 1, 2, and 4 | Cleanup becomes non-critical only after deployment and settlement stop depending on it |
| 7. Projections/surfaces | Phases 2, 5, and 6 | UI/CLI fields are removed only after canonical owners and recovery views exist |
| 8. Claude authority | Phase 2, Phase 7, approved D4 | Normalized outcomes come first; authority replacement is optional and security-sensitive |
| 9. Compatibility deletion | every accepted prior phase and a zero-use soak | Deletion is proof of migration, never its first step |

## Phase 0 — baseline, probes, and a migration manifest

### Prerequisites

- Burak records D1-D7 and marks S01-S15 accepted, amended, deferred, or rejected in the review
  ledger. The phase plan uses the recommended defaults only where the recorded decision agrees. In
  particular, D2 controls transcript retention, D3 controls
  long-term managed helpers, D4 controls whether Claude's authority boundary is replaced, D5 selects
  checkout activation versus versioned releases, D6 selects issue-publication approval, and D7
  selects Inbox persistence. A phase that depends on an unresolved choice does not start.
- The current recovery episode, if any, is allowed to finish under the current architecture. Do not
  clear it merely to begin this migration. No recovery-state format or recovery behavior cutover is
  allowed while an old episode is active.
- All active tasks and workers are inventoried by project, including blocked tasks, pending broker
  actions, L1/reviewer children, Codex containment units, Claude jobs, and worktrees.
- The deployed commit, running PID, service unit hash, web-bundle hash, runtime schema versions, and
  pending-restart record are captured in a read-only baseline receipt.
- The full Python suite and web tests/build pass from a clean exact `origin/main` checkout.

### Changes

Add a versioned, read-only runtime manifest endpoint and a corresponding local inspection command.
The manifest reports, without secrets:

- activated source SHA and deployment-attempt identifier;
- runtime-state schema and broker-contract versions;
- service PID/start time and web-bundle hash;
- active recovery episode id and schema, if present;
- counts of active tasks by control-contract version;
- whether deployment is pending and the target SHA.

Add a migration preflight command that performs no writes by default. It validates every active task,
worker record, pending action, recovery record, and configured project; lists legacy records; and
prints the exact phase gates that are open or closed. A separate `--write` mode may exist for later
operator-run importers, but it is not used in this phase.

Create a small checked-in schema/contract version table, not a dynamic registry or plugin system.
State records that participate in migration gain an
explicit version when they are next written; absence means the documented legacy version. Unknown
versions fail startup with a deterministic configuration exit status rather than being silently
coerced. The same Phase 0 release updates the systemd unit's `RestartPreventExitStatus` for that
deterministic refusal so an incompatible state cannot cause a restart loop.

### Validation gate

- Unit tests prove that the manifest describes the code loaded by the running process rather than
  merely reporting a mutable checkout's current HEAD, and that secrets and absolute private
  evidence paths are absent.
- The preflight is run twice against a copied `ALTITUDE_HOME`; its output is identical and the copy
  has no changed files.
- Fixtures cover every currently observed legacy task/recovery/incident shape and one deliberately
  unknown version. The unknown version is refused before HTTP bind or worker polling.
- Record the baseline receipt. Every later phase compares its observable task and recovery counts to
  this receipt.

### Rollback/stop conditions

This phase has no state migration. Roll back the code if the manifest cannot identify the exact code
serving the UI/API, or if preflight changes state. Do not proceed with an unexplained live worker,
duplicate task owner, malformed active recovery record, dirty deployment checkout, or missing task
provenance.

## Phase 1 — separate task bases, merge, and operator activation

This phase comes first because every later behavioral change needs a clear activation boundary. It
removes the current coupling in which task dispatch requires the mutable deployment checkout to be
exact and post-merge cleanup fast-forwards files underneath a running Python process.

### Exact changes

Bootstrap is explicit because the PR that removes v1 self-deploy is initially landed by v1 code.
First land and separately activate a behavior-preserving bootstrap PR that adds: the permanent
trusted PublicationReceipt writer, initial DeploymentRecord writer, the permanent
SettlementJournal envelope for the final-receipt/deployment-contribution tail, final-boundary
durable launch gate, and a temporary one-way `legacy_auto | operator_pending` deployment-mode selector. Its own
merge may still follow the documented legacy fast-forward and therefore requires the existing idle
hold plus an immediately authorized restart; this is the last acknowledged exception to “merge is
not activation.” After bootstrap activation, a trusted reconciliation records that bootstrap merge
and activated SHA, tests the gate, and—while idle—switches the selector to `operator_pending`.
Rollback of that selector is forbidden. Only then may the narrow self-deploy-removal cutover PR
merge. Bootstrap code writes its receipt/DeploymentRecord contribution and, because operator mode is
already selected, cannot fast-forward the checkout.

1. Split `git_policy.fetch_and_require_exact_base` into two explicit policies. Task dispatch/resume
   fetches the remote and returns an immutable `origin/main` SHA, then validates only the registered
   task worktree/branch/base against that SHA. The task policy still verifies canonical origin and
   installed protected-reference/publication hooks; it simply does not require local deployment
   `main` to equal the remote. Exact local deployment `main` and a clean checkout remain requirements
   of operator activation and service preflight, not task provenance.
2. Introduce the permanent trusted `PublicationReceipt` at the existing landing boundary before
   introducing `DeploymentRecord`. The receipt captures the current mechanically observed task,
   branch, base/head, scope, checks, PR, merge SHA, merge hold, and deployment target; model report
   fields are no longer authority for those facts. Change merge completion (`dispatch.pull_after_done`
   and its callers) to use the SettlementJournal's deployment-contribution intent/receipt stage to
   update the deployable service's one idempotent `DeploymentRecord`: append that immutable receipt
   reference and merge SHA, then advance the exact pending target. Replay reconciles the stable
   service/receipt/merge key before writing, including after a crash between receipt creation and
   contribution. It no longer pulls the service checkout,
   swaps a web bundle, edits a unit, or restarts anything. Additional merges retain their ordered
   provenance; task receipts remain immutable and never carry mutable `pending/activated` state.
3. Keep `make restart` as the operator deployment command. Under the recommended D5 choice it
   performs an early idle check, claims candidate `C`, and sets a durable activation gate. From a
   detached exact candidate it runs read-only preflight/migration validation, discovers a supported
   Node/pnpm toolchain without a hard-coded patch path, and builds/tests the staged web bundle and
   unit before changing the installed checkout. It then quiesces timers/mutations, stops the old
   service, proves the service cgroup empty, fast-forwards the checkout to `C`, installs the staged
   assets/unit, starts once, and verifies:
   - a new PID and start time;
   - the expected source SHA and state-schema compatibility from the manifest API;
   - API, SPA shell, and static-asset hash health;
   - no active or newly launched worker crossed the cutover.
4. Retain pre-source-swap cleanup/rollback of a staged web candidate. Under recommended D5, a failure
   after installed source changes sets activation `state=failed` with an unsatisfied `target_sha`,
   bounded diagnostics, and the service stopped; it requires a reviewed revert/fix PR and another
   authorized activation. The durable activation gate remains set until a healthy activation; it is
   not cleared merely because the command exited. The command must not claim full-code rollback. If Burak selects automatic full-code rollback in
   D5, replace this step—not supplement it—with a versioned release containing Python, source assets,
   unit, and web bundle, an atomic `current` pointer, and verified whole-release restoration. Do not
   maintain both activation mechanisms.
5. Preserve the Phase 0 deterministic `RestartPreventExitStatus`; transient runtime crashes may
   retain bounded systemd restart policy.
6. After candidate `C` is healthy, append one `ActivationReceipt` and satisfy exactly the pending
   merge SHAs that are ancestors of `C`. A merge landing after `C` was claimed remains pending. A
   failed candidate remains both `failed` and unsatisfied. Unit installation and `daemon-reload` are
   part of the same operator transaction when required.
7. Import an existing `restart-pending.json` into the canonical deployment record before deleting
   it. Preserve target SHA, changed components, task/PR references, and time. If the old record cannot
   identify an exact remote target, activation blocks for operator reconciliation rather than
   declaring it satisfied.

Before the narrow cutover merges, prove the bootstrap release and `operator_pending` selector are
active, place the service under its tested durable gate, and prove all workers/L3 turns are idle.
After merge, allow no launch until a separately authorized `make restart` activates and verifies the
new code. Merging does not authorize that restart. Once accepted, remove the legacy mode/selector;
later merges leave both service process and installed checkout unchanged until operator activation.

### Tests and real acceptance

- Bootstrap acceptance: reconcile its own legacy-merged SHA into a trusted receipt/activated record,
  prove the launch gate at the final worker boundary, switch the one-way operator selector, then land
  a disposable PR and prove bootstrap code records it without changing the installed checkout. The
  self-deploy-removal cutover refuses unless all four facts are present.
- Unit/integration: task dispatch during deployment lag pins fetched `origin/main` while a dirty,
  diverged, or wrongly based task worktree still refuses. Operator activation rejects a missing
  source file, stale SHA, dirty installed checkout, wrong `ALTITUDE_HOME`, unsupported state version,
  malformed unit, and incomplete web bundle before restart.
- Disposable-service E2E: under recommended D5, prove a pre-swap failure leaves source/service
  unchanged, a post-swap failure leaves the service stopped with an unsatisfied failed target, then
  repair through a reviewed revert/fix and activate it. If versioned releases are
  selected, activate A, fail B, and prove A is restored in full (Python SHA, unit, web, API).
- Production acceptance, with explicit authorization: activate the phase from an exact SHA; observe
  a new PID and matching manifest; exercise `/api/overview`, project chat read, task-status read, and
  the SPA; verify all prior task/recovery records still exist and no worker is live.
- Merge one harmless documentation-only test PR through the normal path. Prove the service remains
  on its prior activated SHA, only the canonical deployment record changes, and no pull/restart
  command appears in the journal. Merge another PR after candidate `C` is claimed and prove
  activating `C` leaves the later contribution pending.

### Deletion gate

After two successful operator activations, delete scheduler-side checkout fast-forwarding and the
publication-settlement workaround that exists only to fence that interval. Keep staged web swapping
and web rollback under recommended D5; delete them only if the selected versioned-release design
replaces them and a whole-release rollback test passes.

Stop if the running manifest does not exactly match the selected SHA, if a worker appears during the
idle/activation interval, if failure reporting overstates the selected rollback guarantee, or if any
merge changes the installed checkout or running process without an operator action.

## Phase 2 — canonical commands, task ownership, outcome, and settlement

This phase creates the smaller architecture's authoritative records before removing any old state.
It is the dependency for WIP simplification, typed fault actuation, UI removal, and cleanup reduction.

### Exact changes

1. Add one application-command layer for every durable mutation. HTTP, CLI, Claude's validated task
   adapter, Codex brokers, and timers become authority adapters that call the same command. Each
   command owns lock acquisition, authority/generation validation, one atomic transition, one audit
   event, and a typed return value. The service is the ordinary mutation path; narrowly named offline
   operator commands require it stopped and acquire the same kernel lock. A migrated command has no
   remaining direct-write route.
2. Define and version the canonical contracts described in the target architecture:
   - `TaskRecord` with `queued | running | settling | blocked`, normalized scope, merge hold, current
     owner generation, `code_allowed`, control-contract/provider policy, ordered publication
     attempts, final result receipt, and typed attention/block; one `active_operation` reference
     covers worker transition or settlement rather than parallel flags;
   - one current `OwnerGeneration` containing attempt, provider/model/route observation,
     session/worker/process-unit/capability, worktree, branch, and base SHA, validated by one
     `require_current_generation` operation;
   - provider-neutral `WorkerStatus` and `WorkerOutcome` variants;
   - the closed typed operation-journal utility with fixed `l3_turn`, `worker_transition`,
     `settlement`, `issue_publication`, and `recovery_transition` variants; recovery has only
     `task_claim` and `clearance` subtypes;
   - one `SettlementJournal` keyed by outcome/action id and owner generation with fixed intent/receipt
     stages for workspace snapshot, verification, commit, push, PR, checks, merge, post-merge,
     final receipt, an applicable idempotent DeploymentRecord contribution, attention/helper
     continuation, and archive;
   - immutable `PublicationReceipt`, `VerificationReceipt`, and `NoCodeReceipt` types. Publication
     owns commit/head/base, changed scope, local/remote checks, PR, merge, merge hold, deployment
     target identity, and evidence references; mutable deployment state stays in Phase 1's record;
   - one canonical non-global operational-hold registry; and
   - one typed operational projection consumed by status, server, CLI, briefs, and UI.
3. Make `worker_transition` the only physical launch/replace/stop journal. Persist the next
   generation id, deterministic process-unit name, provider request, and message id before launch.
   Every start/resume mints a new attempt/worker/capability; genuine resume may retain the native
   provider session. Reconcile the named unit/result after a crash instead of reissuing resume.
4. Change both providers' terminal path to journal a strict untrusted outcome. For Claude,
   publish/complete commands cease performing commit/push/merge/archive while the physical writer
   can still run. Independently of optional D4 filesystem containment, launch each Claude worker in
   a tracked process unit whose descendants can be stopped and proven empty; retain the current
   guard. Codex keeps its stronger containment boundary.
5. Trusted settlement acts only after the whole registered provider process unit is empty. It
   advances the fixed journal, reconciles every recorded external target before retry, and writes an
   immutable receipt. `continue` may launch bounded helpers idempotently and then install a new L2
   generation; a decision/open finding/merge hold becomes typed blocked attention; clean publication
   or no-code completion archives mechanically. A failed post-merge requirement preserves the first
   receipt and may resume the same task for a corrective publication attempt; its new
   `worker_transition` journals fresh worktree/branch/base registration and reconciliation before
   installing the generation. Rejection/archive likewise stops and reconciles every task-owned L2
   and helper process unit before terminal state.
6. Replace `reported`, `status.json.verified`, `l3_handled`, `completion_requested`, report promotion,
   and stranded closeout with `settling`, typed attention, and the journal/receipts. Non-blocking FYIs
   and follow-up proposals append once to canonical events; D7 controls only read acknowledgement.
7. Make issue hydration a canonical intake command. An explicitly referenced issue must have one
   validated immutable repository-bound snapshot before the task becomes queued. Resume never
   fetches missing issue context. Issue publication uses its stable journal/effect id and follows D6.
8. Convert `schemas/l2_action.json`, `schemas/l3_action.json`, `schemas/report.json`, and review
   findings to discriminated variants. Model schemas share only untrusted outcome/finding definitions;
   trusted receipt fields are backend-only. The broker rejects unknown/extra fields and stale
   generations.
9. Normalize abnormal provider exits separately from model outcomes and map them to retry/reroute,
   a canonical scoped hold, task attention, or global ownership fault. Preserve weekly-reserve-first
   routing, provider-local unknown/exhausted behavior, and the recorded reason/freshness.

### Data migration and compatibility

Do not translate a live legacy ownership contract. Before the authorized idle cutover, every v1 task
must finish under v1, be explicitly rejected after its worker is proven stopped, or have its useful
request/progress preserved in a GitHub issue for a future fresh v2 task. Pending broker actions and
L1/reviewer runs must settle under v1. This deliberately trades a one-time drain for deleting a
permanent resume adapter and its ambiguous authority.

With no active/resumable v1 task, no L3 turn/helper/action/settlement live, and the service in
migration-hold mode, the importer:

1. inventories archived `status.json`, reports, `status.json.verified`, action journals, issue
   snapshots, provider worker records, L1 records, and transcript manifests;
2. stages exact v2 archive receipts when legacy facts reconcile, retaining the original provider
   report as explicitly untrusted historical evidence;
3. indexes ambiguous historical archives behind an isolated versioned read-only audit decoder that
   is never imported by active dispatch/resume/mutation paths;
4. writes and fsyncs all side-by-side v2 files plus count/hash migration receipt and validates them;
5. atomically switches the `tasks` entry in `state-formats.json` last, preserving every other domain
   entry. That map update is the first task-v2 mutation and makes that domain forward-only; only then
   may the candidate enable timers/mutations.

Absence inference for legacy provider/capability fields exists only inside the offline archive
importer. No active task is created with inferred authority, and no component writes old
report/state fields after selection.

### Tests and real acceptance

- Command parity tests submit every mutation through HTTP, CLI, Claude adapter, Codex broker, and
  timer where applicable. They assert the same validation, result, state, and audit event. Static
  search plus tests prove no authority adapter writes task/project/recovery state directly.
- Kill ordinary local commands after authoritative state/directory rename, during audit append, and
  before the next mutation. Startup/next-command reconciliation emits the stable transition id;
  event readers collapse duplicate physical rows, and no applied transition is missing logically.
- Kill launch/steering before and after transition intent, prior-worker stop, provider spawn, process
  bind, provider result, session-id capture, and message delivery. Recovery finds the deterministic
  process unit/result; exactly one current generation and one delivered message remain.
- Kill settlement before and after process-unit empty proof, local commit, push, PR creation, check
  observation, remote merge, publication-receipt write, DeploymentRecord-contribution intent,
  contribution application/receipt, archive move, and FYI projection. Restart completes exactly
  once with no stranded deployment target, conflicting contribution, duplicate commit/PR/merge/FYI,
  or model turn.
- Race stale/current messages, helper requests, outcomes, and publication. Every path calls the same
  generation check; a stale capability never appears in logs, transcripts, issues, PRs, or API.
- Reject a task while L2 and several helpers are live or exiting. The terminal transition stops or
  reconciles every owned process unit/receipt before archive and releases the repository slot only
  after all are empty.
- Kill an L3 turn and an issue publication around provider/action/GitHub boundaries; response and
  issue effect ids apply once. Exercise helper continuation through `settling -> running` with a new
  L2 generation, and exercise a failed post-merge check followed by a corrective publication in the
  same task.
- Run migration fixtures for every archived legacy state, old provider default, old issue envelope,
  report/`status.json.verified` disagreement, malformed record, selector-map update, and candidate
  migration-hold startup. Rerun is byte-stable; a failure before the `tasks` entry update leaves task
  v1 selected, while a failure after it must recover that domain forward on v2. Other domain entries
  remain byte-identical. An attempted live-v1-task cutover refuses.
- Real E2E on each enabled provider: fresh task, steering/resume, optional helper, inert outcome,
  settlement, PR/check/merge, archive, project FYI when warranted, and transcript boundary snapshot.
  Kill/restart once during settlement in a disposable instance and reconcile the same effect. For
  Claude, prove the complete process unit—not only the CLI parent/job record—is empty first.
- Real no-code E2E proves unchanged branch/base and archives from the same outcome machinery.
- Routing characterization/E2E covers weekly reserve before short-window availability, one provider
  exhausted/unknown while the other remains eligible, explicit preference, and provider switch as a
  new session/generation rather than a fabricated resume.

### Deletion gate

After the v1 active set is empty, every archive is converted or explicitly indexed as legacy
audit-only, and a full release reports zero old-writer/active compatibility reads, delete old task
state transitions, report promotion, `status.json.verified` authority, stranded-report recovery,
duplicate generation checks, resume-time issue hydration, and parallel action/report definitions.
Isolated versioned historical decoders may remain under the selected audit/retention policy; they are
not active compatibility. Do not delete evidence referenced by receipts or transcript bundles.

Stop if settlement can run while its physical writer is live, an existing remote effect cannot be
reconciled idempotently, any caller bypasses the command layer, an ambiguous task is guessed into v2,
or a clean completion still requires an unrelated L3 model turn.

## Phase 3 — one control plane and one active L2 per repository

This phase reduces collision prevention before predictive path scheduling and cleanup machinery are removed. It must
land after the operator-activation boundary so all callers switch scheduling semantics together.

### Exact changes

1. Route ordinary server chat, recovery attention, worker commands, and mutating CLI requests through
   Phase 2's application commands. Commands use one documented kernel file-lock order across
   processes; the lock releases on process death. An owner receipt may aid diagnostics but never
   expires authority. Narrow offline recovery/deployment commands require the service stopped and
   acquire the same kernel lock.
2. Remove `dispatching` timestamp leasing and separate dispatch-claim authority. The command lock
   serializes the short state transition; the persisted `worker_transition` journal reconciles the
   long process-launch boundary. All launch paths use that same pair.
3. Set repository L2 WIP to one, keyed by canonical repository identity rather than project label.
   `queued` tasks may accumulate, but the repository slot is held by any task that is `running`,
   `settling`, or `blocked`, or has a pending worker transition, unapplied outcome/publication, or
   live helper/worker. A stopped `blocked` task still owns its branch and blocks the next task until
   it is resumed, completed, rejected, or converted to an external issue. There is no silent parking
   state.
4. Preserve each task's declared `paths`, but redefine them as trusted publication scope and L1
   sublease input—not as a scheduler mutex between L2s. A project may require a nonempty normalized
   scope before code dispatch or explicitly allow undeclared scope; in the latter mode the trusted
   publisher derives and records actual changed paths and the L2 cannot broaden its authority after
   editing. A no-code task declares `paths: []` plus `code_allowed: false`.
5. Keep parallelism across different repositories and bounded L1/reviewer helpers within the one
   owning L2. Two project configurations that point at the same repository share the WIP gate. L1
   parent-commit fencing and non-overlapping subleases remain.
6. Make recovery preemption explicit: an active global episode stops ordinary fresh/resumed launches.
   First reconcile every ordinary SettlementJournal/external effect to an unambiguous stable
   block/archive boundary; unresolved commit/push/PR/merge state forbids slot transfer. Then
   checkpoint, stop, and prove all workers/helpers empty, set `state=blocked` with
   `blocked.kind=preempted_by_episode`, and temporarily assign the single repository slot to the one
   recovery L2. The preserved branch/session cannot resume, settle, or publish. After clearance a
   new `worker_transition` owns base/worktree revalidation and fresh-workspace/rebase preparation
   before installing a generation. Recovery never overlaps its writer; this is the only blocked-task
   WIP exception.

### Compatibility and data handling

Phase 2 already requires the v1 active/resumable set to be empty. New v2 tasks persist normalized
publication scope, `control_contract_version`, and `code_allowed` at creation. Project policy
explicitly chooses required scope or trusted derivation at publication; the cutover does not infer
that choice. Historical `BROAD_CLAIMS`, annotations, and brace expansion remain readable only in the
isolated archive decoder and never enter new scheduling.

### Tests and real acceptance

- Race server chat, CLI chat, and recovery attention in separate processes. Exactly one L3 turn may
  start; losers receive the current owner/try-again result without writing a second conversation
  action.
- Race timer dispatch and a service-routed operator request. Exactly one worker transition/generation
  is persisted; process death is reconciled without an expiring timestamp lease or duplicate worker.
- Queue two same-repository tasks with disjoint paths, including a fixture with two project aliases,
  and prove the second cannot launch. Complete the first and prove the second launches. In another
  repository, prove a task can run concurrently. Separately place the first task in stopped
  `blocked` and crash-recovering `settling`; each must continue to hold the repository slot.
- Inject global recovery while an ordinary task has (a) a live helper, (b) a clean blocked branch,
  and (c) a settlement crash after remote merge. Preemption refuses (a)/(c) until process/remote
  reconciliation reaches a stable boundary, then records the typed blocked reason and admits only
  the recovery task. After clearance, the new worker transition revalidates/rebases or remains
  visibly conflict-blocked before any ordinary writer starts.
- Refuse a code-capable task with an empty scope under a require-scope project and any broad
  ambiguous scope. Under an allow-undeclared project, prove the trusted publisher records actual
  changes without accepting model-selected scope. Prove a declared no-code task can complete only
  while Git confirms its branch is unchanged.
- Real E2E: one tiny task follows L3 -> L2 -> checks -> PR -> merge -> archive while a second task
  remains queued. Verify one writer, provenance trailers, the publication-scope check, worker
  emptiness, and a pending contribution in the DeploymentRecord with no service activation.

### Deletion gate

After all live tasks carry normalized publication scopes and a full release has run at repository
WIP=1,
delete path-overlap scheduling, broad-claim suppression, pending-resume lease ordering, and their UI
hold projections. Do not delete publication-scope validation or L1 subleases.

Stop if WIP=1 strands an unidentifiable writer, if a blocked task can be bypassed without an explicit
terminal transition, if separate processes can create two dispatches, or if two L3 turns can pass the
same kernel lock/journal boundary.

If D1 rejects repository WIP=1, stop here. Removing predictive lease scheduling then requires a
separately reviewed concurrency policy and adversarial multi-PR tests; this migration must not assume
that worktree isolation alone prevents base and publication collisions.

## Phase 4 — typed operational outcomes instead of model retry

The current broad exception paths can turn deterministic publication races, local task errors, and
optional subsystem failures into another model turn or a global recovery hold. This phase introduces
typed outcomes before changing recovery scope.

### Exact changes

1. Use two small closed enums rather than a central recovery-rule engine:
   - boundary-local effect result: `applied | already_applied | retryable_conflict | needs_author`;
   - fault blast radius: `advisory | task | project | provider | global`.

   Each trusted boundary owns a static exhaustive mapping from its concrete errors to those enums and
   a deterministic recheck where applicable. There is no runtime rule registration, symptom regex,
   or model classification. Base/head movement maps to `retryable_conflict`; an exact already-merged
   PR maps to `already_applied`; publication-scope violations map to task `needs_author`; quota maps
   to a provider hold; proven project configuration defects map to a project hold; and uncertain
   ownership/shared-state integrity maps global. A worktree error is task-local only after common
   Git/other-owner integrity remains proven. TTS/digest/edit telemetry and conservative cleanup
   refusal are advisory.

2. Split the single `LandError` catch. Base/head movement gets one broker-owned retry with a fresh
   immutable pair. An already-merged result is reconciled from the remote merge receipt. A missing
   or violated publication scope blocks directly. Only a content/policy problem that actually needs
   author judgment resumes L2.
3. Make every broker action idempotent using its persisted action id plus dispatch identity. A crash
   after a remote effect but before local finalization replays reconciliation, never the model's
   request.
4. Replace generic `except Exception -> system_fault` call sites with an explicit boundary mapping.
   Unknown exceptions at trusted state/ownership boundaries remain `global_safety`; unknown errors
   in optional presentation/telemetry paths become local evidence plus a visible degraded flag.
5. Give provider and verifier outages one durable `retry_at`, one in-flight attempt, capped backoff,
   and visible escalation. A fresh trusted quota/check observation can make the same hold due again;
   retries neither wake a model merely to wait nor create parallel timers/tasks.

### Tests and real acceptance

- Fault-inject a PR pair move before merge, after remote merge, and before local receipt write. Each
  case performs at most one merge, produces one trusted receipt, and consumes no correction turn.
- Replay every broker action after a simulated process crash. The second application is a read-only
  reconciliation or returns the prior result.
- Inject TTS, digest, passive edit telemetry, cleanup, provider, verifier, invalid task branch, and
  ownership-unknown failures. Assert their exact scope and prove only ownership/shared-state cases
  trip the global launch gate.
- Assert one transactional retry and serialized provider/verifier retry: at most one due claim, a
  delay that reaches but never exceeds its cap, visible escalation, and immediate reconsideration
  only when fresh trusted availability evidence arrives.
- Real E2E: move the PR base during a disposable task and prove trusted retry/finalization finishes
  without another L2 message. Confirm the service stays on its currently activated SHA.

### Deletion gate

Instrument the legacy `system_fault` and broad `LandError` adapters for one release. When every
caller uses a typed effect result/scope and the adapter count remains zero through the real E2E, delete the
generic adapters and tests that require model retry for infrastructure outcomes.

Stop if an unknown failure is mapped permissively, a retry can duplicate a remote effect, an
evidence-only fault hides a worker-ownership uncertainty, or a durable retry can fan out claims,
timers, model turns, or tasks.

## Phase 5 — recovery episode v2 and canonical fault evidence

This is the behavior cutover that removes healing chains and stale duplicated holds while retaining
L3's chosen autonomy. It depends on typed outcomes so only true shared-safety failures arrive as
global recovery episodes.

### Canonical model

Use one versioned canonical episode record under the global recovery lock. It contains:

- `episode_id`, active state (`open | repairing | waiting_operator`), and opened/updated timestamps;
- bounded references to canonical incident evidence plus causal `origin_episode`/`origin_task`;
- objective clearance probes (the active file's presence is the global launch gate);
- L3 supervisor revision, claim, attempts, `next_attempt`, capped-backoff policy, and escalation
  visibility;
- null, claiming, or finalized `repair_task_id` operation;
- `restart_required` as an operator request, never an executable action;
- and no derived project/UI state.

Clearance appends an immutable receipt and removes `recovery.json` under the lock; `cleared` is not an
active episode state. The clearance and incident ledgers retain history.

The append-only structured fault/incident ledger is the canonical evidence history. Markdown
incidents, FYIs, monitor summaries, task/project events, and chat notices become projections that
carry the canonical evidence id. Projection failure appends at most one bounded advisory event; it
never records the same fault as a new fault or creates another durable view-state machine. Existing
Markdown incidents and JSONL ledgers remain immutable historical evidence.

Project `hold.json` stops mirroring the global fuse. Status and UI derive the project view from the
canonical episode plus task/provider/project holds. There is one source of launch truth.

### Recovery supervisor rules

1. A new global-safety fault atomically opens an episode and closes ordinary launch permission
   before any L3 work. Repeated or new evidence updates the same open episode and increments its
   supervisor revision when it materially changes the required response.
2. The server claims one L3 supervisor turn at a time under the cross-process L3 lock. Failure keeps
   the same durable wake, schedules one retry with persisted exponential backoff capped at the
   configured maximum, and never creates another wake/incident/task. Attempts remain visible and
   cross an operator-escalation threshold, but there is no hard cap that abandons self-healing.
3. L3 may run allowlisted mechanical reconciliation, clear the episode when its recorded probes
   pass, or request the episode's one recovery L2. No prior Burak approval is required for
   these recovery choices.
4. Recovery task creation uses `recovery_transition(task_claim)`: persist `claiming` with one
   deterministic task id, create or reconcile exactly that id through the application command, then
   finalize `repair_task_id`. A crash at either boundary resumes the same operation; it cannot spend
   the slot without a recoverable target or create a second task. Once finalized, rejection,
   failure, archive, or later evidence cannot reopen it.
5. A recovery L2 receives `source=recovery`, `origin_episode`, a narrow scope, and no task-creation or
   service-lifecycle capability. Backend validation rejects a recovery-task action that attempts to
   create/delegate work, change the episode id, clear without probes, or invoke service management.
6. A fault produced by L3 recovery or the recovery task inherits `origin_episode` and is appended to
   that same episode. It may tighten the global gate, stop a writer whose ownership became unsafe, or
   move to `waiting_operator`; it may not create another episode/task while the origin episode is
   open. Material new global evidence invalidates the recovery task's bypass revision. If it can
   affect writer ownership, containment, or the repair's assumptions, the current repair worker is
   stopped; otherwise a trusted check or L3 may renew that same task's permit for the new revision.
   The exemption is never a permanent task-identity bypass.
7. Mechanical recovery is a closed allowlist of idempotent trusted reconcilers, such as rereading an
   exact remote merge receipt, expiring an abandoned claim after proving no worker exists, or
   rebuilding a derived status view. Arbitrary shell commands, source edits, Git publication, unit
   changes, and service restart are not mechanical recovery.
8. If healthy behavior requires new code, the single recovery L2 may publish it normally. The merge
   contributes to the DeploymentRecord; L3 then records `restart_required` and the episode waits for an
   authorized operator deployment. It does not run `make restart`.
9. A state that needs Burak's decision or an authorized deployment may show `waiting_operator`, but
   the same episode remains durable. Safe mechanical probes may continue at capped intervals; L3
   retries remain one at a time and may recognize that the external condition has changed. Waiting
   never authorizes the missing action.
10. Clearance requires named objective probes appropriate to the fault: shared state readable,
   ownership known, containment empty, task/worktree identity valid, and/or remote receipt settled.
   L3 may clear after they pass. `recovery_transition(clearance)` uses an
   episode/revision-derived idempotency key, records `clearing` intent in the active episode, appends
   or reconciles one clearance receipt, then removes `recovery.json`. Restart resumes the same stages,
   so a crash cannot duplicate the receipt or leave an unreconcilable active episode. Burak retains
   the same clearance authority.

### Data migration

Recovery v2 does not cut over while a v1 recovery hold is active. Once no episode is active and no
recovery task is live:

1. Run the importer under the old recovery, launch, and new migration locks while the service is
   stopped as part of an authorized release activation.
2. Validate legacy `recovery-hold.json`, `faults.json`, per-project `hold.json`, incident Markdown,
   per-project incident ledgers, and the global incident index. Copy historical fault summaries into
   the new evidence ledger with `legacy_source` and stable content hashes; do not rewrite the source
   files.
3. Stage the v2 incident ledger, non-global hold registry, last legacy clearance cursor, and
   migration receipt side-by-side. With no active episode, stage no `recovery.json`. Fsync and
   validate counts/hashes, then atomically update only the `recovery_incidents` entry in
   `state-formats.json`; the already-selected `tasks` entry is unchanged.
4. Start the new release in migration-hold mode, validate that the selected recovery domain treats
   absence as globally clear, then enable v2 writes. Legacy files are read-only audit inputs. Derived
   project holds are ignored only after that domain entry and receipt validate.

The operator command checks this gate against candidate migration logic before it fast-forwards the
installed checkout or stops the current process. If a v1 episode remains active, activation refuses
and the current process continues. Do not translate an in-flight attention claim or recovery task by
inference. An emergency can be handled and cleared under v1, then migrated normally.

### Tests and real acceptance

- Property/race tests interleave fault publication, launch permission, L3 process death, task claim,
  new evidence, clearance, and process death. No trace admits an ordinary launch after the global
  gate linearization point or more than one supervisor/repair owner.
- Assert that a repair task can be created once and only once across failure, rejection, archive,
  process restart, new fault kinds, and attempted new episode creation with the same origin. Kill
  between `claiming`, exact task creation, and finalized reference; recovery creates or finds the same
  task id and never loses or duplicates the slot.
- Kill clearance before intent, after its keyed receipt append, and before/after active-file removal.
  Recovery reconciles exactly one receipt and ends with no active episode; it never opens launches
  merely because one of those filesystem effects partially completed.
- Add material global evidence while the recovery L2 is live. Prove its old bypass revision no
  longer authorizes launch/resume; unsafe ownership evidence stops it, while unrelated evidence can
  only renew the same task after explicit trusted/L3 revalidation.
- Fail many consecutive L3 attention attempts. Prove there is never more than one claim or one due
  retry, the delay reaches but never exceeds its cap, escalation becomes visible, and every attempt
  remains on the same wake/episode. Then supply Burak input and prove it advances that episode, not a
  new task.
- Inject each typed fault scope. Task/provider/project faults do not freeze unrelated projects;
  shared-state/unknown-writer faults do. Escalating scope updates the same canonical episode.
- Simulate every projection write failing. Canonical evidence and launch behavior remain correct and
  only a deduplicated advisory event appears.
- Migration fixtures cover empty history, repeated fault kinds, duplicate legacy index rows, stale
  derived project holds, clearance history, and malformed input. Re-running a successful importer
  changes nothing; malformed input leaves no v2 commit marker.
- Isolated real recovery E2E A: inject a mechanically reconcilable fault, allow L3 to reconcile and
  clear it without approval, and prove no L2 was created.
- Isolated real recovery E2E B: inject a fault requiring code, allow L3 to create the episode's one
  recovery L2, publish/merge its tiny change, then provoke a second fault from that task. Prove a
  second recovery task is refused, no restart occurs, and the episode waits on the recorded operator
  deployment if activation is needed.
- After an explicitly authorized test deployment, prove L3 can run the clearance probes and clear the
  same episode. Verify one clearance receipt and no orphan worker/cgroup.

### Deletion gate

After at least one mechanical and one delegated recovery acceptance run plus a release with zero
legacy-read diagnostics, delete v1 attention/repair state, per-project recovery-hold mirrors,
24-hour incident fan-out logic, and Markdown mutation as an operational dependency. Keep legacy
evidence files archived and readable by an explicit audit command.

Stop if a new evidence item can be lost after the supervisor says it is handled, if a spent repair
slot can be reclaimed, if a recovery-origin fault can open a recursive task, if any clearance lacks
its probes/reason, or if any recovery path can invoke service lifecycle operations.

## Phase 6 — conservative finalization and cleanup

Cleanup is made non-critical only after publication is idempotent and deployment no longer depends
on the task worktree or live checkout.

### Exact changes

- Define task completion as a durable verified publication/no-code receipt plus archival. Cleanup is
  not part of completion and cannot change a completed task back into a system fault.
- Reduce automatic cleanup to one safe case: the exact registered task worktree, its worker proven
  stopped/containment empty, no dirty or ignored task output, a verified merge/no-code receipt, and
  branch identity still owned by the archived task. A compare-and-delete lock protects the final
  removal.
- Any failed proof emits one bounded `cleanup_deferred` evidence item and leaves the worktree and
  branch intact. It neither activates recovery nor spends a model turn.
- Move ambiguous L1/reviewer artifacts, stale branch discovery, squash-equivalence analysis, and
  old namespace cleanup to an operator-visible `alt prune --dry-run`. Destructive prune requires
  exact selections and repeats all ownership checks.
- Remove source fast-forward/restart-pending behavior from cleanup; deployment receipts already own
  it in Phase 1.

### Tests and real acceptance

- Fault-inject every cleanup proof and filesystem operation. The only allowed outcomes are exact
  owned removal or intact artifacts plus `cleanup_deferred`.
- Race cleanup with status reads, transcript export, a stale process record, and a newly created
  similarly named task. No transcript/evidence or other task path may be removed.
- Complete and merge a real tiny task, verify archive/transcript before cleanup, verify no worker,
  then either prove its exact worktree was removed or inspect the specific non-blocking defer reason.
- Run `alt prune --dry-run` twice and prove it is read-only and stable.

### Deletion gate

After a release shows completion independent of cleanup and all retained artifacts are visible to
the operator, delete ancestry/squash heuristics and generic cleanup-to-global-fault call sites. Keep
the final exact ownership/worker/dirty checks; simplification is never permission for broader
deletion.

Stop on any ambiguous ownership, any cleanup-triggered global hold, any lost transcript/evidence, or
any difference between dry-run targets and the later revalidated target set.

## Phase 7 — canonical projections, sessions, transcripts, and product surfaces

Only after task, publication, fault, and recovery facts are canonical should the API, CLI, prompts,
and UI stop exposing the old mechanisms. This phase also removes optional telemetry and dead product
surfaces whose failures currently participate in operations.

### Exact changes

1. Build overview, project, task, monitor, incident, and transcript read models from the canonical
   command records. Server handlers, CLI status, `STATE.md`, briefs, and React use the same typed
   projection library. `STATE.md` remains a regenerable prompt cache, never authority.
2. Make Chat the only high-level L3 intake and Task the direct L2 conversation. Remove direct web
   and public CLI task creation, manual dispatch, project approval controls, and any generic action
   endpoint that bypasses the application command layer. Preserve project registration/removal with explicit
   confirmation, task steering, merge holds, transcript viewing, archive/evidence inspection, and
   operator recovery controls.
3. Make `l3.json.sessions[provider]` the sole L3 provider-session map. Import old top-level/current
   session mirrors once, retain genuine separate provider continuity, and provide only bounded human
   chat handoff on a provider change.
4. Consolidate quota/availability and worker liveness into the operational projection while
   preserving raw provider observations needed for weekly-first routing. Delete model-edit counts,
   edit-count hooks/files/readers, raw-agent fields that do not support ownership, the dead
   statusline-install endpoint, digest Markdown/audio/TTS, its timer, and unused web audio hooks.
   Presentation/telemetry failure is advisory.
5. Remove obsolete helper PR fields/parsing and keep helper patch/findings/parent-generation facts.
   If D3 selects native provider helpers later, treat that as a separate migration with equivalent
   scope, status, and sole-publisher evidence; do not mix helper contracts in this core cutover.
6. Apply D2 to transcript retention. Live opt-in viewing, generation fencing, redaction, boundary
   snapshots, validation, and portable audit remain. A finite/operator-managed retention choice is
   implemented only for archived evidence and never as part of task cleanup.
7. Apply D7 in one slice. If Inbox is an acknowledgement queue, add a canonical acknowledged/archive
   lifecycle and migrate its durable entries. If it is a read model, derive decisions/recent FYIs
   from canonical events and remove `inbox.jsonl`/unused `seen` state. Do not keep both.
8. Simplify CLI/internal commands after every caller uses application commands. Public operator and
   worker commands keep their stable semantics; scheduler/poll/verify/raw-engine operations move to
   an explicit internal/debug namespace and cannot become a second mutation authority.

### Data migration and compatibility

- Import L3 session mirrors per provider with stable session ids and last-handled human-chat cursor.
  A conflict between two purported canonical sessions blocks that project's migration.
- Regenerate projections from canonical task/publication/incident records and compare them to old
  views. Differences in derived formatting are allowed; missing active tasks, decisions, merge
  holds, findings, provider limits, or recovery state are not.
- Historical digest/audio/edit-count/statusline artifacts are inventoried, then treated as
  disposable only after Burak's retention decision; they are never copied into canonical state.
- Old transcript schema versions retain explicit read/validate support according to D2. Unknown
  versions remain private opaque evidence rather than being discarded.
- Existing project approval is not silently converted into a permanent merge policy. Historical
  task merge holds remain audit evidence; removed project-level approval is reported in preflight.

### Tests and real acceptance

- Contract-test each read model against API, CLI, prompt, and UI consumers. A canonical fixture must
  render the same active task, decision, merge hold, fault scope, quota status, and publication
  receipt everywhere.
- React route tests cover the primary flows: start in Chat, L3 creates a task, open Task, steer the
  exact generation, inspect merge/decision/FYI, opt into live transcript, inspect Monitor/recovery,
  and register/remove a project. Assert removed New Task/Dispatch/approval/digest/audio controls are
  absent.
- Switch a real L3 conversation Claude -> Codex -> Claude and prove separate resumable provider
  sessions plus bounded missed-human-message handoff, with no tool-log replay or fabricated resume.
- Trigger removed TTS/edit/digest and statusline-install-HTTP equivalents and prove no timer, route,
  hook fault, file write, or recovery episode exists. The retained quota statusline hook continues
  to feed routing. Static consumer search must be empty before deletion.
- Validate/export old and new transcript fixtures, kill at each promised snapshot boundary, and
  prove secrets/capabilities are absent. Apply the selected archive retention only in a copied home.
- Exercise the selected Inbox behavior end to end, including acknowledgement/archive if retained or
  deterministic event-derived disappearance if removed.

### Deletion gate

After one soak release shows zero old-session/view/endpoint/field readers, delete L3 session mirrors,
duplicate server/status projections, direct web task/dispatch routes, project approval, digest/TTS,
edit telemetry, dead statusline HTTP code, obsolete helper PR data, and the D7-rejected Inbox path.
Retain audit-only readers explicitly required by D2 and the migration receipt.

Stop if a primary workflow requires a removed bypass, a provider session is collapsed or guessed,
a UI projection can disagree with trusted command state, a transcript promise is weakened without
the D2 decision, or optional observability can still actuate recovery.

## Phase 8 — optional Claude authority replacement

This phase runs only if D4 selects replacing Claude's current authority boundary. The recommended D4
choice is to defer it: keep the shell guard and trusted backend checks, while Phase 2 already
normalizes terminal outcome settlement. The guard is complex, but removing it before an equivalent
boundary is proved would weaken enforcement.

### Exact changes

1. Record the precise approved D4 replacement and its threat model. It may be OS/filesystem
   containment, or a smaller allowlisted command wrapper plus trusted backend authority; do not
   assume Codex-identical sandbox internals. Phase 2's process-unit ownership/empty proof remains in
   either design.
2. Persist a new `control_contract_version` on fresh dispatch. The trusted broker applies the same
   untrusted outcome semantics, while provider-specific launch, resume, authentication, transcript,
   usage, and process observation remain explicit.
3. Keep Git's protected-reference hooks and backend provenance/merge checks. They protect operator
   and non-model Git paths too and are not replaced by model containment.
4. Remove only the authority made unnecessary by the approved replacement. If OS containment is
   selected, strip direct mutation capability, service-session bus, ambient credentials, and the
   shell guard. If an allowlisted wrapper is selected, retain the minimal required credential/auth
   path and prove the backend still owns every durable/Git effect. Passive telemetry remains optional
   and evidence-only.

### Compatibility and deletion gate

Old Claude tasks are pinned to their prior contract. Before the selected authority cutover, either let them
finish under a release that still contains the old guard/backend or explicitly stop and redispatch
them as a new attempt. Never resume a direct-control session after deleting its guard.

If D4 remains deferred, retain `hooks/guard.py`, its tests, and its settings wiring and mark this
phase deferred; they are accepted provider-specific safety complexity, not dead code. If D4 is
approved, run shadow evaluation only on inert recorded commands; it must not accept effects. After
selected-boundary Claude dispatch, resume, helper, publication, blocking, and cancellation E2Es pass
and the manifest shows no active prior-control task, delete only the guard/session/parser/direct
routes made redundant by that selected boundary. Retain `reference-transaction`, `pre-push`, and
trusted backend validation; keep pre-commit/pre-merge hooks if their remaining early feedback is
worth their small cost.

### Tests and real acceptance

- Run the adversarial suite required by the recorded D4 threat model. OS containment must cover
  state/Git-common writes, host PID/signalling, nested escape, DBus/service control, credentials,
  network, malformed action, stale identity, and output before the process unit is empty. A smaller
  wrapper must prove command/parser bypass resistance plus backend-only Git/state/service effects;
  both designs prove descendant ownership and empty process unit.
- Real E2E on each provider: fresh L2, Burak steering/resume, optional L1, PR/check/merge, archive,
  transcript validation, and containment-empty proof.
- Crash the broker after worker exit and replay the inert action; prove idempotent finalization.
- Explicitly attempt `make restart` and `systemctl --user restart altitude` from both worker types;
  the selected boundary must deny them and the backend must have no lifecycle action to honor.

Stop if Claude requires broader filesystem/process/service authority than the declared adapter, if a
legacy task can be resumed without its legacy enforcement, or if provider-neutral broker behavior
diverges for the same action.

## Phase 9 — remove compatibility code and simplify the active documentation

This phase contains no new behavior. It is a deletion release after all accepted prior gates are
satisfied; explicitly deferred D3/D4/D5 alternatives retain the protections their decisions require.

Delete:

- path-overlap L2 scheduling and broad-claim heuristics;
- checkout self-deploy/publication settlement; delete web-only rollback only if D5's selected
  versioned-release mechanism replaces it;
- generic model retry for infrastructure/transaction outcomes;
- v1 recovery attention, repair, duplicated project holds, and operational dependence on mutable
  incident Markdown;
- automatic complex cleanup and cleanup-triggered global faults;
- Claude's direct-control shell parser and routes, only if optional Phase 8 was approved and completed;
- active mutation/resume compatibility readers whose counters remained zero for the required soak;
- migration-only preflight write modes/importers, shadow counters, temporary format selector, and
  migration-hold branches after all current state is canonical and receipts are retained.

Keep:

- atomic state writes and project/control/launch locks;
- task dispatch/session/worker/capability identity fences;
- isolated worktrees, task provenance, protected Git refs, PR checks, merge holds, and trusted
  landing;
- Codex containment, all-provider empty-process-tree proof, inert broker actions, and transcript audit;
- one active L2 per repository and bounded L1/reviewer children;
- typed scope/outcome handling, one canonical recovery episode, one autonomous recovery L2 maximum,
  one-at-a-time durable attention with capped backoff, objective clearance, and clearance history;
- the canonical DeploymentRecord/activation receipts, operator-only exact-SHA activation, full health verification, honest
  failure reporting, and the rollback guarantee selected in D5;
- conservative exact cleanup and durable historical evidence.

Retain isolated versioned read-only decoders required by the selected archive/transcript retention
policy. They are audit tooling, not active compatibility: dispatch, resume, commands, settlement,
recovery, and normal projections may not import them.

Update `README.md`, `docs/ARCHITECTURE.md`, `docs/SESSION_LIFECYCLE.md`, schemas, personas, CLI help,
and operational runbooks in the same deletion release. Superseded designs remain in Git history and
legacy evidence remains available through the audit reader; the active docs describe only the
reduced architecture.

## Cross-phase acceptance matrix

No phase is complete on unit tests alone. The following matrix is the minimum release evidence.
Until the Makefile is consolidated, every implementation PR runs at least:

```sh
make test
(cd web && pnpm install --frozen-lockfile && pnpm test && pnpm build)
git diff --check
```

It must also pass required remote Python and web test/typecheck/build checks against the exact candidate
head/base pair. After the Makefile consolidation, one documented top-level target must run the same
Python, web-test, typecheck/build, and formatting gates; removing a command is not a simplification
unless the replacement demonstrably covers it.

| Capability | Automated gate | Real acceptance gate |
| --- | --- | --- |
| State and locks | full Python suite; multiprocess race/fault injection; copied-home migration fixtures | manifest/task counts match before and after activation |
| Normal code path | broker, provenance, scope, PR-pair, idempotence tests | L3 -> L2 -> checks -> PR -> merge -> archive; no service mutation |
| No-code path | unchanged-branch proof and stale identity tests | small research task completes with no commit/worktree change |
| Resume/steering | operation-journal kill/race tests for both providers | Burak message replaces physical worker with a new generation while logical task ownership stays fenced |
| Helpers | parent commit, sublease, patch, and containment tests | one bounded L1/reviewer result returns only to its L2 |
| Fault scope | exhaustive typed mapping and unknown-boundary tests | local fault leaves unrelated project running; global fault closes launch gate |
| Recovery | episode model/property tests; serialized capped-backoff retry; one-task-ever tests | one mechanical and one delegated episode; no recursive task/restart |
| Cleanup | compare-and-delete and adversarial ownership tests | archive/transcript survive; exact worktree removal or safe defer |
| Deployment | detached candidate, gate, idle, stop-before-swap, health, failed-state, and selected D5 tests | authorized new-PID activation plus the selected failure/revert or full-rollback drill |
| Process/security boundary | all-provider process-unit tests; Codex adversarial suite; D4-selected Claude suite if approved | real task per enabled engine and no remaining worker/process unit |

For each real E2E, retain a private acceptance receipt containing activated source SHA, project/task,
dispatch/action ids, PR/merge SHA when applicable, old/new PID for deployment, containment result,
assertions, and timestamps. Do not put transcript contents, credentials, or private incident detail in
the receipt.

## Final completion criteria

The reduced architecture is complete only when all of the following are true:

- the running service reports the exact activated source SHA and web-bundle hash; if D5 selects
  versioned releases, it also proves all source-controlled assets come from that immutable release;
- a merge can only add a pending DeploymentRecord contribution, and only an authorized operator activation
  can change the source SHA loaded by the running service;
- each repository admits one active L2, while declared paths serve publication scope rather than an L2
  concurrency algorithm;
- all trusted boundaries return typed effect results/scopes and the legacy generic-fault/retry counters are zero;
- global recovery has one canonical episode, one-at-a-time durable L3 supervision with capped
  backoff and visible escalation, one recovery task ever, no recursive origin, and no
  service-lifecycle capability;
- all active workers use a supported persisted control contract, and no removed contract can be
  resumed;
- cleanup is outside task correctness and can only remove an exactly proven owned artifact;
- active mutation/resume compatibility readers report zero use for a complete soak release and
  deletion gates have been recorded; isolated audit decoders are outside active paths and the
  selected D5 revert/full-rollback path remains available and tested;
- the full automated suite, web suite/build, multiprocess races, both recovery E2Es, normal task E2E,
  enabled-provider E2Es, and the selected operator failure-plus-revert or full-rollback drill all pass.

If any criterion is not met, leave the required audit decoder and prior deployable source available,
stop at the last proven phase, and record the failed gate. Do not compensate by adding another
timer, recovery task, state mirror, or automatic restart.
