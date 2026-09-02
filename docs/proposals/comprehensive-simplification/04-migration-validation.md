# Migration and validation plan

> This plan is executable only after the decisions in `05-review-ledger.md` are recorded. A phase is
> complete only when its replacement is active, its superseded writer is deleted, its tests pass,
> and its architecture documentation describes the behavior that actually shipped.

## Completion contract

The refactor is not complete because code moved or a new abstraction exists. It is complete only
when all of the following are true:

1. Each durable fact and external effect has one named owner.
2. Every model/provider boundary produces untrusted data; trusted code verifies effects.
3. One logical L2 owns a task; every physical owner/helper process is fenced and provably stopped.
4. A code change reaches `main` only through a reviewed PR and mechanically observed checks.
5. Merge, deployment activation, recovery, and cleanup are separate lifecycles.
6. No active reader or writer interprets both v1 and v2 task/recovery state.
7. Failure at every documented crash boundary has a deterministic replay or safe refusal.
8. The final production surface is measurably smaller than the 2026-09-02 baseline.

The recorded baseline is:

| Surface | Baseline |
| --- | ---: |
| Runnable backend, CLI, hooks/guard, and restart tool | 10,695 lines |
| Non-test web source | 2,683 lines |
| Web build/config source | 157 lines |
| Total permanent runnable source | 13,535 lines / 52 files |
| Personas, schemas, and templates (reported separately) | 496 lines / 13 files |
| Python tests | 10,258 lines / 54 files |
| Web tests and harness (reported separately) | 1,097 lines / 10 files |
| Service/CI/build support outside runnable-source count | 134 lines / 3 files |
| Baseline checks | 489 Python tests; 39 web tests; production web build |

Final budgets are hard review gates:

- no more than **12,180 permanent runnable lines** (at least 10% net reduction);
- no more than **9,000 backend/CLI/hook/restart lines** (at least 15% reduction);
- no more than **47 runnable production files** and **25 permanent named artifact families**;
- no more than **400 persona/schema/template lines in 10 files** and no more than **250
  service/CI/build-support lines in 3 files**; these categories cannot absorb runnable complexity;
- exactly one mutation authority per domain, no more than six mutable control authorities, and
  exactly six closed durable operation kinds;
- no new long-lived daemon/permanent service, timer loop, database, workflow DSL, plugin registry,
  or production dependency; counted transient per-turn process units are permitted;
- tests may grow and are reported separately; deleting tests to meet a production budget is forbidden.

An artifact family is one independently named persisted path/schema pattern with its own lifecycle or
reader/writer. Mirrors and caches count if runtime code persists/reads them; multiple files inside
one provider-worker or transcript bundle count once when they have one lifecycle. Generated
`web/dist`, dependencies, bytecode, screenshots, external provider-native stores, and the existing
supervisor-owned system journal do not count; explicitly named per-turn disposable scratch such as
`l3-codex-runtime/` also does not count because it is recreated and has no durable reader after the
turn. Altitude-owned TLS material does. The task-record/archive
bundle is one family because live v2 and immutable legacy task shapes share the same terminal archive
and operator-deletion lifecycle, even though the legacy decoder is isolated and read-only.
The baseline families are enumerated, rather than inferred differently per PR:

```text
projects; project/publication/resume locks; recovery-hold; recovery-clearances; restart-pending;
global incidents; monitor faults;
quota/statusline/usage observations; hook-fault drain; edit counts; live-worker cache;
project STATE; project events; project inbox; project hold; project incidents/Markdown; Claude settings;
L3 sessions; L3 chat; L3 actions; issue drafts; task status; request; brief; conversation;
task events; issue snapshot; report; task digest; progress; task Claude settings; helper bundle;
Codex worker bundle; transcript bundle; worktree/ref/provenance; report.md compatibility; global
digest/audio; runtime hooks directory; service log; TLS material
```

The target families, using the same rule, enumerate and are capped at exactly 25:

```text
projects; synchronization locks; operational holds; recovery episode/embedded transition;
recovery clearances; incidents;
deployment/maintenance/activation; quota observations; L3 sessions/embedded turn; L3 chat;
project events; issue publication operation; task record/archive bundle with embedded owner+settlement; request/snapshot;
generation briefs; task conversation; task events; worker bundle; outcomes; helper bundle; receipts;
progress; transcript bundle; worktree/ref/provenance; TLS material
```

If a preserved invariant makes a numeric budget impossible, the PR must identify the exact
invariant, rejected smaller design, and adjusted number. Silence is failure, not approval.

## Non-regression properties

Every PR must preserve these properties unless the PR explicitly replaces them with a stronger,
tested owner:

- protected branches, task provenance, worktree isolation, scope validation, and merge holds;
- stale-generation rejection for messages, outcomes, helpers, and publication;
- fail-closed Codex containment and existing Claude guard/backend validation;
- weekly-first provider routing; unknown telemetry is uncertainty, not exhaustion;
- exact transcript access checks and credential-shaped redaction;
- private durable incident evidence and a launch fuse for uncertain shared-safety faults;
- operator authorization for planned source activation/restart;
- GitHub issue hydration before dispatch and explicit authorization for issue publication;
- one active top-level L2 per repository; optional helpers remain children of that owner.

Normal systemd restart-on-crash remains allowed self-healing. Planned activation is different: the
activation runner suppresses restart loops until candidate health succeeds. A source merge never
restarts or updates the installed checkout by itself.

## Change and review protocol

Each implementation PR must contain:

1. the single module/responsibility being changed and the S/D decisions it implements;
2. its canonical owner before and after;
3. the old writer/reader/artifact deleted in that PR, or the exact immediately following deletion PR;
4. focused unit and crash-boundary tests plus the full applicable suites;
5. before/after production and artifact counts;
6. active architecture-document changes in the same PR;
7. an independent reviewer who did not implement the PR; and
8. a written disposition for every blocker/important review finding before merge.

Preparatory contracts may merge dormant only when they have no writer and no behavior branch. Once
a writer switches, the old writer is removed immediately. A read-only compatibility projection may
survive one release if it is labeled derived, has a counter, and has a named next deletion PR.

The merge order below is dependency order. Non-overlapping implementation and review may run in
parallel, but dependent branches rebase on the actually merged predecessor before final validation.

## Phase 0 — decisions, manifest, locks, and durable primitives

### PR 0A: approve one architecture

- Record D1-D7 and S01-S15 in `05-review-ledger.md`.
- Amend `02-proposed-architecture.md` and this plan until all reviewed contradictions are removed.
- Replace the active architecture documents when the first behavior PR merges; do not leave two
  normative architectures in the active tree.
- Retain the detailed current-system inventory as non-normative migration evidence only.

### PR 0B: runtime manifest and preflight

Add a read-only manifest reporting exact source SHA, supported state versions, web-bundle hash,
service unit identity, and build version. Add an offline preflight that reads a copied
`ALTITUDE_HOME`, inventories active/archived state, reports unknown shapes, and never mutates.

The preflight records:

- active tasks by state and physical worker/provider identity;
- active recovery episode/holds and incident identities;
- every legacy artifact family and active consumer count;
- production lines/files, writer call sites, timer paths, mutation endpoints, and dependencies;
- installed checkout, remote main, loaded source, unit, and web bundle identity.

Unknown active state blocks every later cutover. Unknown archived state stays accessible only through
an isolated read-only archive decoder; it is never imported by active dispatch/resume/settlement.

### PR 0C: lock order and durable I/O

Define one lock order before adding any writer:

```text
activation/maintenance -> recovery -> project -> task -> operation -> Git publication
```

No reverse acquisition is allowed. Tests deliberately contend every adjacent pair.

Replace scattered JSON/JSONL writes with two small primitives:

- atomic replace: write, fsync file, rename, fsync parent directory;
- keyed append: under the owner lock, validate the final JSONL record, truncate only a partial final
  record, append one newline-delimited record, fsync, and deduplicate/reconcile by stable id.

Every local authoritative transition writes state first with a stable transition envelope. The
audit projection is appended second and reconciled before the next mutation. Event-first is
forbidden. Power-loss durability is claimed only after the fsync tests pass on the supported
filesystem; otherwise the documented guarantee is process-crash durability.

### PR 0D: dormant boundary contracts

Define and test the closed, writer-free contracts required by later moves: `WorkerOutcome`,
publication scope, task/operational projection, provider quota observation, and application-command
result. Define the versioned JSON task/operational wire fixtures here as well: Python validators own
the producer schema and the existing TypeScript runtime validator has an explicit matching schema.
They are dormant types, validators, and fixtures only—no adapter, state writer, or behavior selector
may branch on them in this PR. Later PRs adopt each contract and delete its superseded shape in the
same increment.

### PR 0E: exact-candidate web CI

Before any web behavior changes, extend remote CI to run web test, typecheck, and production build
against the exact sanitized candidate. Preserve `pull_request_target` base ownership, exact
base/head validation, same-repository-only candidates, read-only permissions, pinned actions,
`persist-credentials: false`, no secrets/tokens, isolated HOME/state/cache, and `/usr/bin/env -i`
with a narrow Node/pnpm environment. Static workflow contract tests fail if any boundary regresses.

### Phase 0 gate

- Baseline tests pass: 489 Python, 39 web, typecheck/build.
- Preflight run twice on a copied home is byte-for-byte stable and changes no input.
- Manifest identifies loaded code, not merely mutable checkout HEAD.
- JSONL kill tests cover partial write, missing newline, duplicate id, and state-before-event death.
- Dormant contracts reject unknown variants and have zero runtime producers or consumers.
- Remote Python and web checks execute the same exact sanitized candidate under the static boundary.
- No runtime behavior changes before these facts are recorded.

## Phase 1 — maintenance gate and independent activation runner

Deployment must be safe before self-deployment is removed. This phase runs while legacy self-deploy
still exists.

### PR 1A: one maintenance gate and minimal deployment authority

Create the minimal per-service `DeploymentRecord` first. It owns the maintenance gate and embedded
activation operation; it is not introduced later by publication work. Seed its baseline only from
mechanical evidence: an active service manifest may establish `activated_sha`; a stopped or
unverifiable service starts with `activated_sha=null` and requires a successful first activation.
Import any legacy restart-pending marker as bounded `legacy_pending` evidence/blocker, never as a
launch fence or a qualified contribution.

Add the one durable maintenance/activation gate checked by every current mutation and model-entry path:

- HTTP and CLI mutations;
- L3 chat/turn start;
- L2/L1 fresh launch and resume at the final process boundary;
- worker outcome/settlement/publication;
- issue creation;
- recovery changes; and
- timer dispatch, cleanup, transcript snapshots that mutate, and status regeneration.

Read-only API/health/manifest calls remain available. Gate acquisition stops new intake, disables
timers, waits for current operations, L3 turns, and all worker/helper units to reach a durable empty
boundary, then writes an acknowledgement. It never guesses from task labels alone.
When the service is already stopped, the operator path may acknowledge only after systemd inactivity,
empty known process units, and no in-flight operation records are mechanically proved.

The new service starts behind the same gate in health-only mode. Mutations/timers enable only after
the activation receipt is durable and the gate is released.

### PR 1B: detached latest-main activation

Install and exercise the complete activation runner before changing merge behavior. The runner is
independent of the installed candidate: a stable bootstrap entry point fetches a detached exact
checkout of the latest verified `origin/main`, runs that candidate's preflight/tests/build, and then
passes explicit installed-checkout and state-home targets to the candidate deploy tool.

The default deliberately activates the latest verified remote main. It does not claim candidate
`C` and later install it after remote main advances to `D`; removing that feature avoids a special
protected-ref capability and a second pending-order policy.

The fixed activation stages, stored inside the already-created `DeploymentRecord`, are:

```text
claimed -> gate_acknowledged -> candidate_resolved -> candidate_built -> remote_revalidated
        -> restart_policy_suppressed -> old_service_stopped -> source_assets_installed
        -> candidate_started_health_only -> verified -> verification_recorded
        -> restart_policy_restored -> activation_recorded -> complete
        -> failed_released (only before old_service_stopped; old service/source unchanged)
        -> failed_held (from old_service_stopped onward; retaining evidence and gate)
```

The runner:

1. claims the activation attempt, acquires the maintenance gate, and proves acknowledgement;
2. fetches/resolves the then-latest verified remote main and records hashes/old PID;
3. prepares detached source, web bundle, service unit, and compatibility proof without importing
   the installed candidate's Altitude package;
4. refetches immediately before source change and restarts bounded resolution/build if remote main no
   longer equals the recorded candidate; after `remote_revalidated`, a later external merge is
   pending for the next activation because the gate blocks Altitude publication but never claims to
   control all GitHub writers;
5. temporarily suppresses systemd restart loops for this activation attempt;
6. stops the old service and proves its cgroup empty;
7. fast-forwards the clean installed checkout to the revalidated latest main and installs staged assets;
8. starts the first health-only generation and verifies PID/start time, source SHA, schema support, API,
   SPA shell, and bundle hash;
9. writes pending verification evidence, restores normal crash-restart policy while the gate still
   forces health-only mode, then atomically records activated SHA, successful receipt/cutovers,
   contribution satisfaction, enablement, and gate release in one DeploymentRecord replacement;
10. if the candidate exits after policy restoration but before release, records the bounded systemd
    restart generation, returns to health-only verification for the new PID, and never releases the
    gate until the latest generation passes every check; or
11. before old-service stop, restores its policy, proves old PID/source unchanged, and atomically
    records `failed_released` plus gate release; from old-service stop onward, leaves the gate held and
    service stopped when verification or systemd StartLimit is exhausted.

The installed unit and activation receipt fix `StartLimitIntervalSec=120`, `StartLimitBurst=3`, and
`RestartSec=5`. Immediately before the first candidate start the runner performs and receipts
`systemctl reset-failed`. Independently, ActivationOperation persists `candidate_generation` and
allows at most three total candidate generations for the attempt regardless of elapsed time; reaching
the cap stops the service and records `failed_held`. Fault tests cover reset, each generation, burst
exhaustion, and slow-spaced crashes beyond 120 seconds.

Forward repair uses a reviewed state-compatible candidate through a documented fallback composed
only of system Git plus that detached candidate's activation script, even if the installed
Makefile/imports are broken. No separately installed mutable bootstrap executable or artifact is
added; normal `make restart` is a convenience that locates and re-execs the detached candidate tool
before importing Altitude. After any state cutover, a behavior revert
must retain all selected readers; activation rejects a candidate that cannot read current state.

### Phase 1 tests

- Race every HTTP/CLI/timer/model entry against gate acquisition; no mutation begins after acknowledgement.
- Kill at every activation stage, including restart-policy changes and receipt/gate release.
- Deliberately break the candidate Makefile and imports; the stable detached repair path still works.
- Validate pre-install failure leaves old service/source unchanged.
- Validate pre-stop failure restores policy and atomically releases the gate as `failed_released`.
- Validate post-install health failure leaves one held gate, no restart loop, and an exact failed target.
- Run a disposable full activation E2E; do not use the production service as the experiment.

## Phase 2 — publication and deployment authority

### PR 2A: trusted publication receipt

At the existing landing boundary, write one immutable receipt from mechanically observed Git/GitHub
facts. Worker reports remain untrusted input. The receipt contains the exact task/generation,
base/head, scope, commit, PR pair, check verdicts, merge SHA, immutable post-merge observation, and stable
remote effect id.

Exactly-once remote reconciliation uses queryable markers: task/generation trailers on commits and a
bounded machine marker in the PR/issue body. Before retrying, trusted code queries the exact
repository/base/head/marker tuple. Timestamp or title matching is forbidden.

Shadow-compare the receipt with current readers, then move status/archive/cleanup to the receipt and
delete duplicate trusted report/verified fields.

### PR 2B: extend DeploymentRecord with contribution and qualification

Extend the Phase 1 DeploymentRecord transactionally. A merge contribution and permission to activate
are different facts:

- every merged publication contributes its immutable receipt and merge SHA;
- only a successful final qualification marks the contribution deployable;
- failed main checks, unresolved findings, or a merge hold are activation blockers;
- a corrective receipt explicitly supersedes the failed receipt without mutating history; and
- activation refuses unless every contribution between activated SHA and current latest-main target
  is qualified or explicitly superseded.

Walk the exact first-parent ancestry from `activated_sha` through the candidate. Every commit must be
covered by a qualified contribution or an immutable operator-provenance receipt stored inside the
deployment artifact family. An externally written or imported commit is unqualified by default; the
operator receipt names its exact SHA/range and authorization without fabricating check evidence.
Bootstrap/import tests cover mixed Altitude and external commits, gaps, rewrites, and conflicting
receipts.

Import any remaining legacy gap/marker evidence, establish a mechanically verified baseline, and
refuse activation until that baseline is reconciled. The old `restart-pending.json` is a derived
compatibility marker, not a launch fence. Delete it in PR 2C after all readers move to DeploymentRecord.

PR 2B also introduces and tests the bounded one-time cutover publisher used by PR 2C. Before PR 2C
may begin, a separately authorized activation must install this PR 2B release and record its exact
source/tool hash. The command is available from that installed prior release, is usable only by the
maintenance-gate owner for the one configured base/head/marker tuple, and has no generic publication
mode. Its operation is the cutover-publication subtype of the one deployment-transition family,
embedded in DeploymentRecord and mutually exclusive with source activation:

```text
claimed -> gate_acknowledged -> pr_revalidated -> merge_observed
        -> operator_provenance_receipted -> complete
        -> failed_held (from any pre-complete stage)
```

Each remote stage stores intent and reconciles the exact repository/base/head/marker before retry.

### PR 2C: last-v1 cutover and self-deploy deletion

No deployment-mode selector is needed. With the service stopped and the maintenance gate
acknowledged, one explicitly authorized temporary cutover publisher is the gate owner's only allowed
remote mutation. It revalidates the exact PR/base/head/checks, merges the narrow PR that deletes
`dispatch.pull_after_done`, scheduler source fast-forward, restart-pending compatibility, and their
tests/fields **and the temporary cutover publisher itself**, then the still-installed PR 2B command
writes/reconciles an immutable operator-provenance receipt for the exact merge SHA
inside the DeploymentRecord artifact family. Regular settlement/publication remains blocked. A
crash before the receipt is reconciled by the deterministic PR marker and exact merge identity; it
never repeats the merge or silently qualifies a different SHA. Delete the temporary publisher after
the cutover receipt is proved; the later activation installs the candidate in which that command is
already absent.

Before source activation, the installed PR 2B command compacts its completed cutover operation into
the immutable operator-provenance receipt and atomically clears the active deployment-operation
field. PR 2C removes the subtype code/schema from the candidate. The final DeploymentRecord accepts
only source activation; historical cutover truth remains the immutable receipt, not a permanent
operation variant.

Leave the installed checkout at the already-tested PR 2B release containing the Phase 1 runner and
the receipted one-time publisher. A later separately authorized
activation uses that installed runner to fetch, validate, install, and receipt the cutover candidate;
ordinary systemd `ExecStart` does not substitute for activation.

Test before merge that the new code path changes only receipts/DeploymentRecord and never source,
assets, units, or service state. After this PR, merge and activation are permanently separate and no
temporary deployment selector remains.

## Phase 3 — one application-command authority over v1 state

Do not perform a giant command-layer rewrite. Move one command family at a time while the v1 record
shape remains active. Each PR deletes its old direct writer immediately.

Recommended order:

1. project registration/removal;
2. task creation, immutable GitHub hydration, and brief creation;
3. task conversation/steering;
4. block/resume/reject/merge-hold;
5. outcome/settlement/publication requests;
6. fault/hold/recovery changes; and
7. bounded issue publication.

HTTP, CLI, Claude direct commands, Codex brokers, server timers, and tests call the same in-process
command. Adapters parse/authorize and return typed results; they never write state themselves.
Read-only projections may retain v1 response shapes temporarily but cannot actuate behavior.

Each command stores a stable transition id/revision and uses the Phase 0 event reconciliation.
External effects reference the domain-embedded operation described in the target architecture.

### Phase 3 gate

- Parity tests run every command through all applicable adapters and compare result/state/event.
- Static inventory proves zero direct writers remain for the migrated family before its PR merges.
- GitHub/provider kill tests reconcile stable effect ids without another model turn.
- Public generic `task new` and direct web task creation are removed after L3 intake parity passes.

## Phase 4 — physical process ownership and generation fencing

### PR 4A: Claude go/no-go spike

Prove a foreground Claude transport (`claude --print`/stream JSON or an equivalent supported
transport) can run wholly inside a deterministic Claude-specific user unit, preserve genuine session
resume, spool results, and retain current security properties:

- `NoNewPrivileges=yes` unless a reviewed necessity proves otherwise;
- minimal explicit environment and only required authentication access;
- current Claude hook/guard and backend capability validation;
- deterministic stop/kill and descendant-empty proof; and
- no background job escapes the unit.

If this cannot be proved, Claude remains available only for explicitly operator-invoked read-only
use; every autonomous or mutating Claude L3/L2/L1/helper role is disabled and Codex remains the
default. The target must not pretend that a `claude --bg` launcher PID owns the provider's background
process tree.

### PR 4B: shared worker transitions on v1

Put both providers and every owner/helper process behind one provider-neutral physical record and
domain-embedded transition with `subject_kind=owner|helper`:

```text
planned -> prior_stopped -> spawned -> bound -> result_observed -> empty -> complete/failed
```

Intent stores deterministic unit, generation, provider session request, and message/effect id before
launch. Resume creates a new physical generation even when the provider conversation id continues.
No next writer starts until the old unit is proven empty.

Recovery tasks also persist `{episode_id, permit_revision}` in their generation. Final launch and
every trusted message/outcome/publication compare it; renewal creates a new generation.

### Phase 4 tests

- Kill around every owner/helper spawn, bind, message delivery, result, stop, and empty receipt.
- Race stale/current messages and results; stale capabilities never reach logs, GitHub, or state.
- Stop an L2 with several helpers; all deterministic units become empty before terminal/archive.
- Real tiny Codex resume is mandatory. Real tiny Claude L3/L2/helper resume is mandatory only if the
  feasibility proof enables those autonomous roles.

## Phase 5 — normalized outcomes, settlement, and task v2

### PR 5A: adopt normalized outcomes and settlement on v1

Adopt the strict untrusted `WorkerOutcome` variants introduced dormant in PR 0D and add the fixed
settlement operation while current task records remain authoritative. Settlement owns verification,
commit, push, PR, checks, merge,
post-merge qualification, publication receipt, DeploymentRecord contribution, attention, and archive.
Every external stage stores intent then observed receipt and reconciles by deterministic target.

Helper continuation references child worker transitions and returns `settling -> running` with a new
owner generation. A post-merge correction creates a new branch/base/publication attempt in the same
logical task. It never spawns a recursive healing task.

### PR 5B: gated drain and one-shot task cutover

Before gate acquisition, land dormant v2 API/UI/read-model consumers and prove them against fixed
fixtures while v1 remains authoritative. They must have no active selection branch yet. The cutover
then switches every active task reader and writer together under the gate; there is no release in
which fresh v2 tasks exist behind v1-only consumers.

Acquire and acknowledge the maintenance gate before the final emptiness proof and keep it through
candidate health-only start/format switch. Before switching:

- finish and archive a v1 task; or
- stop every owned process, preserve useful work in a GitHub issue/branch, and reject it.

Stop the old service and recheck that no active/resumable v1 task, operation, or helper remains.
Fresh active tasks then use only
`task.json` schema v2. Archived `status.json` tasks stay immutable audit evidence through a
read-only decoder; active code cannot import that decoder. There is no `state-formats.json`, dual
writer, migration-hold selector, or translated live owner.

The v2 task states are `queued | running | settling | blocked | done | rejected`. `done` and
`rejected` carry canonical terminal actor/time/reason or result receipt. Moving a terminal directory
to archive changes storage only, never lifecycle truth.

Before source change, the embedded activation operation records a task-domain cutover intent with
old/new schema, the empty-domain proof, and candidate SHA. After the health-only candidate proves the
new reader/writer set, `activation_recorded` appends the immutable task cutover receipt to that same
ActivationReceipt. A crash before the receipt leaves the gate held; replay reconciles installed SHA,
PID, health-only mode, and domain emptiness before emitting it or fails closed. The receipt is audit
evidence, not a runtime format selector.

### PR 5C: delete dead v1 task paths

Remove the already inactive v1 readers, report/verified/reported/dispatching stamps,
compatibility writers, and obsolete tests. Retain only the isolated archive decoder selected by the
retention decision.

### Phase 5 tests

- Kill settlement before/after commit, push, PR creation, checks, merge, qualification, publication
  receipt, deployment contribution, terminal state, attention, and archive.
- Failed qualification blocks activation until a mechanically linked corrective receipt supersedes it.
- Reject while owner/helpers exit; archive only after all units are empty.
- No-code completion refuses a changed worktree.
- Unknown v2 state refuses before any provider/network effect.

## Phase 6 — concurrency, intake, routing, and scoped faults

### PR 6A: repository WIP one

Set top-level repository WIP to one. Remove predictive scheduling leases, natural-language path
expansion, broad-path suppression, and resume ordering. Paths remain normalized publication scope.
Optional helpers may run concurrently only under the owning L2's disjoint subscopes.

A blocked ordinary task retains the repository slot. Recovery may preempt it only after its
settlement is stable and all owner/helper units are empty; the task becomes
`blocked(kind=preempted_by_episode)` and rebases through a new generation after clearance.

### PR 6B: intake and routing

- Hydrate GitHub issues once before queueing; immutable snapshot absence fails intake.
- Unknown quota remains eligible uncertainty and chooses the configured default.
- Actual launch/quota failure holds only that provider and may route a new generation elsewhere.
- Persist the selected observation plus policy version/hash, not a mutable reserve policy in tasks.
- Weekly comparable allowance is primary; short windows are availability gates.

### PR 6C: typed boundary results and non-global holds

Map every current fault caller to closed effect-result and blast-radius enums. Begin conservatively:
unknown classifications remain global until characterized. Move task/project/provider holds to one
canonical registry only when their race tests pass. The move is an activation-owned atomic cutover
under the acknowledged gate with the old service stopped: preserve every active hold's stable id,
scope, retry evidence, and revision; write/reconcile the import before enabling the new writer;
record counts/hashes in the ActivationReceipt; and delete every old writer in that same increment.
Kill/replay proves no lost, duplicate, or briefly absent hold. Observability/cosmetic failures never
actuate a hold.

## Phase 7 — recovery v2 and incident evidence

Cut over only as a planned activation under the acknowledged maintenance gate, with the old service
stopped and no active v1 recovery episode or recovery task. Recheck emptiness after stop. Clear/close
the existing episode through current verified rules first; do not translate a live supervisor or
permit a v1 fault writer to race the switch.

Use the same activation-owned cutover contract as Phase 5: persist recovery-domain old/new schema,
empty-episode proof, and candidate SHA as intent before source change; after health-only verification,
append the immutable recovery cutover receipt inside the ActivationReceipt. Crash replay reconciles
installed SHA, health-only PID, and domain emptiness while the gate remains held. No active reader
consults the receipt to select a format.

The v2 episode persists immutable `episode_id`, `supervisor_project`, revision, fault/evidence refs,
state, optional current repair task, waiting reason, one logical wake id per revision, the current
physical claim if any, attempt, and durable capped-backoff `retry_at` with no total-attempt cap.
Opening a global fault is safety-first:

1. reserve a stable evidence id;
2. atomically write `recovery.json` first with bounded inline evidence and
   `incident_append_pending`—this is the launch-fuse linearization point;
3. append/reconcile the keyed incident row; and
4. clear the pending marker.

Incident identity is a new UUID with `(project_id, legacy_incident_id)` as a display alias. The
offline importer runs while stopped behind the acknowledged gate, preserves Markdown/amendment
history, and refuses conflicting base records. Each stable legacy alias is imported by the Phase 0
keyed idempotent append (or one atomic staged replacement before any v2 writer is enabled). Counts,
source hashes, and final ledger hash are recorded in the recovery cutover receipt. Kill/replay at
every row and before/after the receipt neither loses nor duplicates evidence.

Recovery behavior is direct:

- trusted probes/reconciliation run before a model;
- one persisted supervisor project receives one L3 wake per episode revision;
- only one physical claim may own that logical wake; abandoned-claim reconciliation preserves the
  wake id, advances capped backoff, and cannot complete unobserved work;
- L3 performs operational recovery and may claim exactly zero or one recovery L2 ever per episode;
  failure, rejection, archive, new evidence, or supervisor replacement never reopens that slot;
- code repair uses normal L2 ownership/PR/check rules plus episode permit revision;
- no incident creates a task, rule, persona, or another incident;
- `waiting_operator` runs only cheap mechanical probes; it does not spend model quota until Burak
  input, new evidence, or a deployment receipt changes the revision;
- clearance is a keyed embedded operation: receipt is reconciled, then active episode is removed.

After real deterministic, provider outage/reroute, task defect, and global safety E2Es pass, delete
legacy recovery attention retries, duplicate project holds, incident indexes, and compatibility
readers.

Kill tests cover task-claim intent/create/finalize, a failed or rejected first recovery task, new
evidence after the slot is spent, recursive recovery-origin fault refusal, logical-wake claim and
abandoned-claim replacement before/after `retry_at`, capped-backoff replay, clearance receipt/removal,
runtime incident append, every legacy incident import row, and cutover-receipt reconciliation. Every
case retains one episode, one physical wake claim at a time, and no more than one repair task.

## Phase 8 — product surfaces and conservative cleanup

### PR 8A: one wire contract and projections

Adopt and consolidate the versioned JSON wire schemas introduced dormant in PR 0D. Python produces
them; TypeScript validates them with its existing runtime validator. There is no fictional
cross-language library or generated mutable authority. Status, CLI, API, briefs, and UI are read-only
projections from canonical records.

Chat remains the L3 high-level surface. Task remains direct L2 steering plus opt-in exact transcript.
Remove direct task creation/manual dispatch, duplicate message endpoints/composers, digest audio,
write-only edit counts, and dead administrative fields/routes.

Remove Inbox as durable state under D7. Decisions/blockers come from typed task attention; recent
FYIs come from canonical events and disappear from the main active view when their task is terminal.

Remote CI already runs Python tests and web test/typecheck/build against the exact sanitized PR
candidate from Phase 0. This PR updates only its contract fixtures if the adopted wire schemas require
it; it does not introduce a later validation boundary.

### PR 8B: cleanup

Terminal settlement no longer performs forensic worktree deletion. A conservative operator
maintenance command deletes only a clean, owned worktree with an exact merged receipt and proven
empty process units. Uncertainty leaves the artifact and emits one audit event; cleanup never opens
global recovery or blocks new work.

### Phase 8 tests

- Contract fixtures fail closed on unknown versions/extra authority fields.
- Chat/task/live transcript E2Es preserve steering generation checks.
- Web remote checks run on the exact candidate.
- Cleanup refuses dirty, unowned, live, unmerged, or ambiguous artifacts without affecting dispatch.

## Phase 9 — final deletion and documentation

Delete every temporary selector, compatibility writer, active fallback reader, superseded persona,
schema, hook, endpoint, field, test fixture, and proposal instruction that describes rejected
behavior. Git history is the archive; active context contains one architecture.

Run static searches for every deleted name and report intentional read-only archive-decoder matches.
Record final production/artifact/writer/timer/dependency counts against Phase 0. Run:

- full Python suite;
- web tests, typecheck, and production build;
- all kill/race tests;
- disposable activation and forward-repair E2E;
- real tiny Codex L3 -> L2 -> PR/check/merge path;
- real tiny Claude path only if Phase 4A passed;
- task defect, provider outage/reroute, and global recovery E2Es; and
- restart inspection proving no worker/helper/service process remains unexpectedly.

Do not restart the production Altitude service merely because implementation merged. Activation and
restart remain a separate explicit operator action.

## Stop conditions

Stop the affected phase and do not merge when:

- a reviewer identifies an unresolved blocker or important contradiction;
- a PR adds a second authority, unlisted operation kind, timer, service, or compatibility writer;
- current safety is removed before replacement proof exists;
- a migration would infer a live worker/session/lease identity;
- a crash test can duplicate a model/GitHub/service effect;
- a failed or unresolved merge can become activatable;
- a candidate cannot forward-repair current state;
- applicable tests or remote checks are not green; or
- production/artifact counts grow without an explicitly accepted invariant justification.

Rollback before a state writer cutover means revert the behavior PR. After a one-way data cutover,
rollback means a state-compatible forward repair or behavior revert retaining selected readers; old
code that cannot read current state is not a rollback option.
