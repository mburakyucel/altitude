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

## Phase 1 — physical ownership and reconciled external effects on v1

Maintenance cannot honestly drain work until every process and remote effect has a durable identity.
This phase advances those prerequisites. It does not add a maintenance claimant, restart the service,
or change activation behavior.

### PR 1A: Claude supervision go/no-go

Prove a foreground Claude transport can run wholly inside a deterministic Claude-specific user unit,
preserve genuine session resume, spool results, retain the current guard/backend checks, stop/kill
deterministically, and prove its cgroup descendants empty. If the proof fails or cannot run,
autonomous or mutating Claude L3/L2/L1/helper roles are disabled; explicitly operator-invoked
read-only use may remain. No drain or process record may infer emptiness from `claude --bg`, a
provider row, or a stale PID.

### PR 1B: one physical transition for every model process

Put L3 turns, L2 owners, and helpers for every enabled provider behind one provider-neutral physical
record embedded in its owning domain:

```text
planned -> prior_stopped -> spawned -> bound -> result_observed -> empty -> complete/failed
```

Intent stores a deterministic unit, physical generation, provider-session request, and stable message
id before launch. Resume creates a new physical generation even when the provider conversation id
continues. No next writer starts until the prior unit is proven empty. Recovery work also persists
`{episode_id, permit_revision}` and every launch/message/result/publication rechecks it. L3 receives a
durable current-turn claim; in-memory locks remain local optimization only. Unknown legacy ownership
fails closed instead of being declared empty.

### PR 1C: normalized outcomes, settlement, and remote-effect reconciliation on v1

Adopt the dormant strict `WorkerOutcome` and a fixed task settlement operation while v1 task records
remain authoritative. Settlement owns verification, commit, push, PR creation, checks, merge,
post-merge qualification, attention, and archive. Every external stage stores intent before the
effect, then records mechanically observed results. Queryable task/generation markers reconcile
ambiguous commit, PR, merge, and GitHub-issue retries; timestamp/title matching is forbidden.

Write one immutable publication receipt containing the exact generation, base/head, scope, commit,
PR pair, check verdicts, merge SHA, post-merge observation, and stable remote effect id. Issue
publication uses explicit user authority and a similarly bounded embedded operation. Direct CLI
landing enters the same task settlement; there is no unrecorded publication path. Worker reports
remain untrusted. Helper continuation creates a new owner generation and never a recursive healing
task.

### Phase 1 gate

- Kill around every provider spawn/bind/result/stop/empty boundary; an exact unit is empty or the
  operation refuses.
- A real tiny Codex resume is mandatory. Claude mutation tests are mandatory only if PR 1A enables it.
- Kill around commit/push/PR/check/merge/issue creation; retry reconciles the same marker/effect.
- Static inventory proves no model process or remote mutation exists without an owning transition.
- No maintenance acknowledgement or source activation path exists yet.

## Phase 2 — deployment contribution and qualification authority

### PR 2A: minimal DeploymentRecord and truthful baseline

Create one strict per-service `DeploymentRecord`, but no maintenance claimant or activation runner.
Seed `activated_sha` only from a mechanically identified active-service manifest. A stopped or
unverifiable service starts with `activated_sha=null`. Import restart-pending data only as bounded
`legacy_pending` evidence/blocker, never as activation truth, a launch fence, or a qualified
contribution. Source staging and ordinary startup cannot change `activated_sha`.

### PR 2B: contribution and qualification

A merge contribution and permission to activate are different facts:

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

Import remaining legacy gap/marker evidence, establish a mechanically verified baseline, and refuse
eligibility until it is reconciled. The old `restart-pending.json` is a derived compatibility marker,
not a launch fence. This phase records eligibility only; it cannot build, install, stop, start, or
acknowledge maintenance.

### Phase 2 gate

- Publication and qualification have one mechanically observed receipt path.
- Every first-parent ancestry gap blocks activation eligibility.
- DeploymentRecord has no activation operation and no maintenance owner yet.
- Legacy checkout fast-forward is staging divergence, never loaded-source truth.

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

## Phase 4 — one maintenance gate and independent activation runner

Process ownership, remote-effect reconciliation, qualification, and application commands now exist,
so drain evidence is mechanical rather than inferred. Legacy self-deploy still exists, but no
temporary restart adapter is added: PR 4A's gate is dormant until PR 4B's detached runner becomes its
first and only production claimant.

### PR 4A: complete admission closure

Extend DeploymentRecord with one maintenance latch and the approved activation-operation slot. Every
mutation/model/effect ingress takes a shared admission before any recovery/project/task/operation/Git
lock and holds it through its durable result. Exclusive acquisition is writer-preferring, waits for
shared holders, proves every Phase 1 unit and effect operation empty/reconciled, then writes one
acknowledgement. It never guesses from task labels, provider rows, quiet time, or bare PIDs.
Shared-to-exclusive upgrade is forbidden.

HTTP/CLI adapters, command boundaries, L3/L2/L1 final process boundaries, settlement/issue effects,
recovery changes, timers, cleanup, mutating snapshots, quota caches, and external hooks all
participate. Read-only health/manifest/static/status calls remain available and byte-stable. Hidden
mutating reads are made pure or classified as mutations. An acknowledged gate starts the service in
health-only mode; timers/mutations enable only when activation receipt and gate release are recorded
atomically.

### PR 4B: detached latest-main activation

Install and exercise the complete activation runner. It fetches a detached exact checkout of the
latest qualified `origin/main`, runs that candidate's preflight/tests/build, and passes explicit
installed-checkout/state-home targets to the candidate deploy tool. `make restart` only locates and
re-execs this detached tool before importing candidate code.

```text
claimed -> gate_acknowledged -> candidate_resolved -> candidate_built -> remote_revalidated
        -> restart_policy_suppressed -> old_service_stopped -> source_assets_installed
        -> candidate_started_health_only -> verified -> verification_recorded
        -> restart_policy_restored -> activation_recorded -> complete
        -> failed_released (only before old_service_stopped; old source/service unchanged)
        -> failed_held (from old_service_stopped onward; retaining evidence and gate)
```

The runner stages source/web/unit compatibility without importing installed code, refetches before
source change, and restarts resolution/build if remote main advanced. After revalidation, later
external merges wait for the next activation. It suppresses restart loops, stops the old service and
proves its cgroup empty, installs the exact staged assets, starts health-only, verifies
PID/start/source/schema/API/SPA/bundle, restores normal crash policy, and atomically records
activation, satisfied contributions, enablement, and gate release.

Before old-service stop, failure restores policy, proves the old generation unchanged, and records
`failed_released`. From old-service stop onward, failure leaves the service stopped and gate held.
The installed unit fixes `StartLimitIntervalSec=120`, `StartLimitBurst=3`, `RestartSec=5`; the runner
receipts `reset-failed` and caps each attempt at three candidate generations. Forward repair uses
system Git plus a reviewed state-compatible detached candidate even if installed imports/Makefile are
broken. No separately installed mutable bootstrap or second deployment selector exists.

### Phase 4 tests

- Race every admitted entry against exclusive claim; no mutation starts after acknowledgement.
- Unknown L3/L2/helper ownership or external-effect state refuses drain.
- Kill every activation, restart-policy, receipt, and gate stage.
- Break installed imports/Makefile; detached forward repair still works.
- Pre-stop failure preserves old source/service; post-stop failure leaves one held gate and no loop.
- Run a disposable activation E2E; never use the production service as the experiment.

## Phase 5 — last self-deploy cutover

### PR 5A: bounded one-time cutover publisher

The installed PR 4B release contains one explicitly authorized publisher for one configured
repository/base/head/marker tuple. It is usable only by the acknowledged maintenance owner and has no
generic publication mode:

```text
claimed -> gate_acknowledged -> pr_revalidated -> merge_observed
        -> operator_provenance_receipted -> complete -> failed_held
```

It merges the narrow candidate that deletes `dispatch.pull_after_done`, scheduler checkout
fast-forward, restart-pending compatibility, their tests/fields, and the temporary publisher itself.
Regular settlement remains blocked. The still-installed command reconciles the exact merge by marker,
writes immutable operator provenance, compacts the operation, and never repeats or silently qualifies
a different SHA. A later separately authorized Phase 4 activation installs the candidate in which the
command is absent. Merge and activation are then permanently separate.

### Phase 5 gate

- Before later activation, publication changes receipts/DeploymentRecord only, never source/assets/
  unit/service.
- Static search proves self-deploy, restart-pending, and temporary-publisher code absent in candidate.
- Historical cutover truth is an immutable receipt, not a permanent operation variant.

## Phase 6 — one-shot task v2 cutover

### PR 6A: gated drain and one-shot task cutover

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

### PR 6B: delete dead v1 task paths

Remove the already inactive v1 readers, report/verified/reported/dispatching stamps,
compatibility writers, and obsolete tests. Retain only the isolated archive decoder selected by the
retention decision.

### Phase 6 tests

- Reject while owner/helpers exit; archive only after all units are empty.
- No-code completion refuses a changed worktree.
- Unknown v2 state refuses before any provider/network effect.

## Phase 7 — concurrency, intake, routing, and scoped faults

### PR 7A: repository WIP one

Set top-level repository WIP to one. Remove predictive scheduling leases, natural-language path
expansion, broad-path suppression, and resume ordering. Paths remain normalized publication scope.
Optional helpers may run concurrently only under the owning L2's disjoint subscopes.

A blocked ordinary task retains the repository slot. Recovery may preempt it only after its
settlement is stable and all owner/helper units are empty; the task becomes
`blocked(kind=preempted_by_episode)` and rebases through a new generation after clearance.

### PR 7B: intake and routing

- Hydrate GitHub issues once before queueing; immutable snapshot absence fails intake.
- Unknown quota remains eligible uncertainty and chooses the configured default.
- Actual launch/quota failure holds only that provider and may route a new generation elsewhere.
- Persist the selected observation plus policy version/hash, not a mutable reserve policy in tasks.
- Weekly comparable allowance is primary; short windows are availability gates.

### PR 7C: typed boundary results and non-global holds

Map every current fault caller to closed effect-result and blast-radius enums. Begin conservatively:
unknown classifications remain global until characterized. Move task/project/provider holds to one
canonical registry only when their race tests pass. The move is an activation-owned atomic cutover
under the acknowledged gate with the old service stopped: preserve every active hold's stable id,
scope, retry evidence, and revision; write/reconcile the import before enabling the new writer;
record counts/hashes in the ActivationReceipt; and delete every old writer in that same increment.
Kill/replay proves no lost, duplicate, or briefly absent hold. Observability/cosmetic failures never
actuate a hold.

## Phase 8 — recovery v2 and incident evidence

Cut over only as a planned activation under the acknowledged maintenance gate, with the old service
stopped and no active v1 recovery episode or recovery task. Recheck emptiness after stop. Clear/close
the existing episode through current verified rules first; do not translate a live supervisor or
permit a v1 fault writer to race the switch.

Use the same activation-owned cutover contract as Phase 6: persist recovery-domain old/new schema,
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

## Phase 9 — product surfaces and conservative cleanup

### PR 9A: one wire contract and projections

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

### PR 9B: cleanup

Terminal settlement no longer performs forensic worktree deletion. A conservative operator
maintenance command deletes only a clean, owned worktree with an exact merged receipt and proven
empty process units. Uncertainty leaves the artifact and emits one audit event; cleanup never opens
global recovery or blocks new work.

### Phase 9 tests

- Contract fixtures fail closed on unknown versions/extra authority fields.
- Chat/task/live transcript E2Es preserve steering generation checks.
- Web remote checks run on the exact candidate.
- Cleanup refuses dirty, unowned, live, unmerged, or ambiguous artifacts without affecting dispatch.

## Phase 10 — final deletion and documentation

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
- real tiny Claude path only if Phase 1A passed;
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
