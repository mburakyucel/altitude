# Migration and validation plan

> This plan is executable only after the decisions in `05-review-ledger.md` are recorded. Before the
> separately authorized first activation, a source phase is complete only when its replacement is
> the sole normal path in reviewed candidate source, its superseded source writer is deleted, its
> tests pass on copied/disposable state, and candidate-source architecture documentation describes
> that merged behavior. This does not claim deployed use: real-state importer deletion, production
> cutover completion, and deployed-document activation remain pending until the ActivationReceipt.

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
| Runnable backend, CLI, hooks/guard, and restart tool | 10,695 lines / 32 files |
| Non-test web source | 2,683 lines / 15 files |
| Web build/config source | 195 lines / 5 files |
| Total permanent runnable source | 13,573 lines / 52 files |
| Personas, schemas, and templates (reported separately) | 496 lines / 13 files |
| Python tests | 10,258 lines / 54 files |
| Web tests and harness (reported separately) | 1,097 lines / 10 files |
| Service/CI/build support outside runnable-source count | 134 lines / 3 files |
| Baseline checks | 489 Python tests; 39 web tests; production web build |

The current integration candidate is intentionally expansionary while replacements are
dormant or only partly adopted: **18,114 permanent runnable lines / 56 files**, comprising **15,092 backend lines / 35
files**, **2,827 web lines / 16 files**, and **195 web-build lines / 5 files**. The temporary real-state
preflight is **1,510 lines / 1 file** and is reported separately. Separately counted candidate inputs
are **498 persona/schema/template lines / 13 files**, **247 support lines / 3 files**, and one
**294-line test-only cross-runtime contract fixture**; `web/README.md` is **13 lines / 1 file** of
non-runtime documentation. The candidate must retire at least **5,934
permanent lines and 13 files overall**, including **6,092 backend lines and 11 backend files**, to meet
the final target. This is implementation debt, not evidence of simplification.

Final budgets are hard review gates:

- no more than **12,180 permanent runnable lines** (at least 10% net reduction);
- no more than **9,000 backend/CLI/hook/restart lines** (at least 15% reduction);
- no more than **43 runnable production files**, including **24 backend/CLI/hook/restart files**, and
  **25 permanent named artifact families**;
- no more than **400 persona/schema/template lines in 10 files** and no more than **250
  service/CI/build-support lines in 3 files**; these categories cannot absorb runnable complexity;
- exactly one mutation authority per domain, no more than six mutable control authorities, and
  exactly six closed durable operation kinds;
- no new long-lived daemon/permanent service, timer loop, database, workflow DSL, plugin registry,
  or production dependency; counted transient per-turn process units are permitted;
- tests may grow and are reported separately; deleting tests to meet a production budget is forbidden.

The exact baseline roster, counting procedure, future-file rule, and normative target roster are in
[`07-baseline-and-target.md`](07-baseline-and-target.md). A new production path cannot escape the
budget by being omitted from that roster; its reviewer must classify it before merge.

An artifact family is one canonical lifecycle/authority and its path pattern, not every instance of
that pattern or every inert reader. Mirrors and caches count if runtime code persists/reads them;
multiple files inside one provider-worker or transcript bundle count once when they have one owner
and deletion lifecycle. Empty flock files at colocated paths count as one synchronization family
because one primitive creates/reads them and they contain no semantic state. An isolated read-only
legacy decoder does not create another control family; a second active writer or independently
retained mutable lifecycle always does. Generated
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

The target families, using the same rule, currently enumerate 25 and may only shrink:

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
- fail-closed Codex containment and temporary legacy Claude guard/backend validation;
- Codex weekly-first routing; unknown telemetry is uncertainty, not exhaustion;
- exact transcript access checks and credential-shaped redaction;
- private durable incident evidence and a launch fuse for uncertain shared-safety faults;
- operator authorization for planned source activation/restart;
- GitHub issue hydration before dispatch and explicit authorization for issue publication;
- one active top-level L2 per repository; optional helpers remain children of that owner.

The current authenticated TLS certificate, key, CA paths, and client-trust arrangement are explicitly
out of scope. This migration neither moves nor redesigns them.

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

## Prerequisite — freeze production once

This migration deliberately does not self-host. Before the first implementation PR:

1. finish and archive each clean v1 task, or stop its workers/helpers, preserve useful branch/session
   evidence, and reject it under the current rules;
2. reconcile every pending commit/push/PR/merge/issue effect and close the current recovery episode
   through the current verified clearance path;
3. record the loaded source, unit, main PID/start identity, cgroup, state-home path, registered
   provider units, Git worktrees/refs, and a byte inventory/hash of the frozen runtime state;
4. stop the service and prove its cgroup plus every known L3/L2/L1/helper/provider unit empty; an
   escaped or ambiguous existing writer is a global blocker, not something “disabled” prospectively;
5. disable every runtime mutation ingress and keep the production state home byte-frozen; and
6. run the Phase 0 preflight before any later checkout operation and again before activation. Any
   unexplained state/hash/process drift stops the migration.

All implementation PRs are source-only. They run against copied state homes and disposable service
units; no test or merge claims to have migrated production. The installed production checkout is not
updated merely because an implementation or merge was authorized. If an exact manual checkout
operation is separately authorized while stopped, it must re-prove the freeze before and after and
must not start the service. Production's first use of the new behavior is the separately authorized
detached activation after the source refactor; all refactor commits receive mechanically checked
operator-provenance coverage because no production settlement code existed to receipt them.

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

Identity has three explicit roles, never one permissive mismatch flag:

- `running_install` is captured at ordinary startup after Phase 0B exists and is supplied/revalidated
  after maintenance acknowledgement. The loaded entry, installed manager ExecStart checkout/unit/
  drop-ins, observed source, and running process identity must agree. It is unavailable for this
  already-stopped one-time bootstrap and must not be backdated.
- `stopped_install` is activation manifest B. It requires the service and its cgroup to be empty,
  hashes the installed checkout/entry/unit/drop-ins without importing or executing B, and requires
  every corresponding byte and manager configuration fact to match the immutable prior-install
  receipt (`running_install` normally, bootstrap freeze receipt for the already-stopped first
  activation). Manager inactive state is required; an absent/unqueryable manager or drifted installed
  byte blocks.
- `detached_candidate` is activation manifest C. Its entry/source tree must equal its pinned
  candidate commit and it reports candidate bundle/unit-template hashes, but it makes no claim about
  the still-old manager ExecStart.

Activation compares one strict stopped-install manifest B with one strict candidate manifest C and
persists the immutable prior-install receipt hash, both new manifest hashes, and the intended B->C
target. A mismatch inside any role blocks; B != C is the expected transition, not evidence to ignore.

Because Phase 0B source lands after this migration's one-time production stop, it must not backdate a
`running_install` receipt. For the first activation only, preflight combines the prerequisite's raw
loaded-source/PID/unit/state evidence with a fresh exact `stopped_install` observation into an
immutable bootstrap freeze/provenance receipt. It names every unavailable running fact and the
operator authorization; it never labels the stopped observation “running.” Missing source SHA,
installed bytes, manager configuration, state hash, or process-empty proof remains a hard blocker.
After the first activation, ordinary starts create native `running_install` receipts.

The preflight records:

- active tasks by state and physical worker/provider identity;
- active recovery episode/holds and incident identities;
- every legacy artifact family and active consumer count;
- production lines/files, writer call sites, timer paths, mutation endpoints, and dependencies;
- installed checkout, remote main, loaded source, unit, and web bundle identity.

Unknown active state blocks every later cutover. Unknown archived state stays accessible only through
an isolated read-only archive decoder; it is never imported by active dispatch/resume/settlement.
The legacy inventory/preflight is temporary but remains shipped through the first successful
production activation; only PR 10B may delete it after its frozen-state and cutover receipts exist.

### PR 0C: lock order and durable I/O

Define one lock order before adding any writer:

```text
activation/maintenance -> recovery -> project -> task -> operation -> Git publication
```

No reverse acquisition is allowed. Tests deliberately contend every adjacent pair.

Introduce two small primitives behind the existing public helper shapes:

- atomic replace: write, fsync file, rename, fsync parent directory;
- keyed append: under the owner lock, treat every unterminated tail as uncommitted, exclude it from
  reads/deduplication, truncate it before append, refuse malformed LF-terminated rows, append one
  newline-delimited record, fsync, and deduplicate by stable id.

This PR is foundation-only. It may harden the central atomic JSON and append/read helpers without
changing their record shapes, and may make the existing project lock participate in the order. It
does not convert recovery/fault/resume/L1/publication wrappers, add a runtime transition envelope,
or change a domain lifecycle. A checked-in static inventory classifies every remaining legacy lock
and direct JSON/JSONL writer; a new unclassified writer fails tests. Each Phase 3 command-family PR
then adopts state-first stable transition envelopes and before-next-mutation audit reconciliation
for the owner it migrates, while deleting that family's legacy exception. Power-loss durability is
claimed only after the fsync tests pass on the supported filesystem; otherwise the documented
guarantee is process-crash durability.

### PR 0D: dormant boundary contracts

Define and test the closed, writer-free contracts required by later moves: `WorkerOutcome`,
publication scope, task/operational projection, provider quota observation, and application-command
result. Define the versioned JSON task/operational wire fixtures here as well: Python validators own
the producer schema and the existing TypeScript runtime validator has an explicit matching schema.
They are dormant types, validators, and fixtures only—no adapter, state writer, or behavior selector
may branch on them in this PR. Later PRs adopt each contract and delete its superseded shape in the
same increment.

PR 0D.1 corrects the initially omitted WorkerOutcome evidence surface without activating it. Every
variant requires the same closed, non-authoritative observations block named in 02; human replies
and trusted-derived outcome/effect identity remain outside it. Shared fixtures must be consumed by
both Python and Zod. This complete boundary raises the two production contract files' explicit cap
from 449 to 581 lines (+132): the exact addition is the matching typed block plus two validators, not
a runtime writer, adapter, compatibility selector, or new artifact family. Any further increase
requires a new reviewed justification.

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
- Primitive kill tests cover atomic replace; exclusion and truncation of every unterminated tail
  before append, including a complete JSON object without LF; refusal without mutation for malformed
  or non-object LF-terminated rows; duplicate ids; and cross-process lock release.
- Dormant contracts reject unknown variants and have zero runtime producers or consumers.
- Remote Python and web checks execute the same exact sanitized candidate under the static boundary.
- No task/recovery/provider/publication lifecycle behavior changes before these facts are recorded.

## Phase 1 — physical ownership and reconciled external effects on v1

Maintenance cannot honestly drain work until every process and remote effect has a durable identity.
This phase advances those prerequisites. It does not add a maintenance claimant, restart the service,
or change activation behavior.

### PR 1A: Claude supervision go/no-go

The proof could not be completed locally without spending a provider turn, so this gate resolved
no-go: Codex is the sole autonomous L3/L2/L1/helper engine and no source path launches or resumes
Claude. Legacy Claude observation and physical stop/remove remain only while production is stopped.
Before this source can run, activation must prove every legacy unit/process empty; no actor variable,
TTY, same-UID token, `claude --bg` row, or stale PID substitutes for that proof.

### PRs 1B.1-1B.4: one physical transition, adopted by owner family

PR 1B.1's closed physical record and PR 1B.2's Codex L3 adoption are active candidate source.
PR 1B.3 L2-owner adoption and PR 1B.4 managed-helper adoption remain planned. Each adoption deletes
that family's prior PID/job-row/timestamp
ownership writer and fallback reader before merging. The shared transition is:

```text
planned -> prior_stopped -> spawned -> bound -> result_observed -> empty -> complete/failed
```

The following owner boundary is the planned PR 1B.3 path, not active L2 behavior yet.
`TaskCommands` alone takes the task lock and serializes `task.json`. It
stores the deterministic unit, physical generation, provider-session request, and stable message id,
then compare-and-swaps `planned -> prior_stopped` before releasing the lock. Only that winner sends
an immutable task-revision/transition-digest request to `WorkerCommands`. WorkerCommands performs or
reconciles the physical effect and returns a typed receipt; it never writes `task.json` or embedded
`active_operation`. TaskCommands reacquires the task lock, rejects stale revision/digest/generation,
and serializes the receipt and any owner binding. No task lock is held during provider work.
Resume creates a new physical generation even when the provider conversation id continues. No next
writer starts until the prior unit is proven empty. Recovery work also persists
`{episode_id, permit_revision}` and every launch/message/result/publication rechecks it. The active
PR 1B.2 L3 path already uses its project-lock current-turn CAS and durable receipts; a different
message receives explicit busy/deferred. Process-local locks remain local optimization only. Unknown legacy ownership
fails closed instead of being declared empty. A static process inventory must be empty before the
next sub-PR; no maintenance claimant exists during this sequence.

### PRs 1C.1-1C.5: outcomes and reconciled external effects on v1

- **1C.1 — normalized outcomes:** adopt strict `WorkerOutcome` for enabled L2 adapters and delete the
  parallel Claude-report/Codex-action authority fields. Blocking/no-code/continue remain state-only.
- **1C.2 — settlement claim and local verification:** add the fixed task settlement record, exact
  worktree/scope/base snapshot, and commit intent. Delete report-promotion and completion-request
  writers for migrated paths; this PR cannot push or call GitHub.
- **1C.3 — task publication:** move push, exact PR discovery/create, checks, merge, post-merge
  observation, immutable publication receipt, and direct CLI landing into that
  settlement. Every stage stores intent before effect and reconciles by task/generation marker and
  exact remote identity; timestamp/title matching is forbidden.
- **1C.4 — issue publication:** move the repository-bound issue marker/create/reconcile path into the
  one project issue operation and delete the L3 action journal's duplicate effect state.
- **1C.5 — continuation, attention, and terminalization:** move helper-result continuation,
  new-owner-generation creation, project attention/FYI projection, terminal state, and archive move
  into settlement, deleting stranded report promotion, old finalizers, and L3 closeout flags.

Deployment contribution and qualification are deliberately absent here; Phase 2 adopts the
publication receipt into `DeploymentRecord`. Worker reports remain untrusted throughout.

### Phase 1 gate

- Kill around every provider spawn/bind/result/stop/empty boundary; an exact unit is empty or the
  operation refuses.
- A real tiny Codex resume is mandatory. Claude tests prove launch/resume is unreachable and legacy
  targets remain held; there is no conditional Claude mutation acceptance path.
- Kill around commit/push/PR/check/merge/issue creation; retry reconciles the same marker/effect.
- Static inventory proves no model process or remote mutation exists without an owning transition.
- No maintenance acknowledgement or source activation path exists yet.

## Phase 2 — deployment contribution and qualification authority

The writer-free DeploymentRecord fact modules may be reviewed and merged after Phase 0 because they
have no runtime caller or selector. That narrow ordering exception does not complete Phase 2 and does
not authorize adoption: settlement contribution, maintenance, and activation remain blocked until the
complete Phase 1 ownership/effect gate passes.

### PR 2A: minimal DeploymentRecord and truthful baseline

Create one strict per-service `DeploymentRecord`, but no maintenance claimant or activation runner.
Seed `activated_sha` only from a mechanically identified active-service manifest. Also accept the
immutable prerequisite freeze receipt as the one-time `bootstrap_anchor`: exact observed loaded SHA
when available, otherwise an explicit operator-provenance anchor naming the stopped checkout SHA,
frozen state hash, evidence limitation, and authorization. It is not an ActivationReceipt. A stopped
or unverifiable service has `activated_sha=null`; an unknown/ambiguous bootstrap anchor blocks first
activation rather than forcing ancestry from null. Import restart-pending data only as bounded
`legacy_pending` evidence/blocker, never as activation truth, a launch fence, or a qualified
contribution. Source staging and ordinary startup cannot change `activated_sha`.

Phase 2A implements this as one 344-line permanent module plus one 271-line focused test module. The
single `deployments/<service>.json` record and colocated lock are the already-budgeted DeploymentRecord
artifact/synchronization family, not a new family beyond the target inventory. The production addition
contains only closed validation, baseline normalization, lock-protected atomic persistence, and readback;
it has zero runtime consumer/writer and no service or remote effect. Later deployment phases must extend or
replace this module within the repository-wide final budget rather than create a parallel authority.

### PR 2B: contribution and qualification

A merge contribution and permission to activate are different facts:

- every merged publication contributes its immutable receipt and merge SHA;
- only a successful final qualification marks the contribution deployable;
- failed main checks, unresolved findings, or a merge hold are activation blockers;
- a corrective receipt explicitly supersedes the failed receipt without mutating history; and
- activation refuses unless every contribution between the effective ancestry anchor and current latest-main target
  is qualified or explicitly superseded.

The effective ancestry anchor is `activated_sha` when non-null and otherwise the one-time
`bootstrap_anchor.sha`; no other fallback exists. Walk the exact first-parent ancestry from that
effective anchor through the candidate. Every commit must be covered by a qualified contribution or
an immutable operator-provenance receipt stored inside the deployment artifact family. An externally
written or imported commit is unqualified by default; the operator receipt names its exact SHA/range
and authorization without fabricating check evidence. Bootstrap/import tests cover mixed Altitude and
external commits, gaps, rewrites, and conflicting receipts.

Import remaining legacy gap/marker evidence, establish a mechanically verified baseline, and refuse
eligibility until it is reconciled. The old `restart-pending.json` is a derived compatibility marker,
not a launch fence. This phase records eligibility only; it cannot build, install, stop, start, or
acknowledge maintenance.

PR 2B extends the same record in place to schema `altitude.deployment/v2`; it does not add a journal,
sidecar, runtime selector, or second lock. One explicit offline upgrader validates the exact Phase 2A `v1`
shape, preserves its anchor and legacy evidence, and atomically replaces it under the existing deployment lock;
ordinary reads refuse `v1` and never auto-migrate. Contributions contain only publication-receipt identity/hash,
merge SHA, and observation time. Their service-local replay key is receipt id plus merge SHA, excluding retry
time; a changed receipt hash conflicts. Qualifications contain the exact merge-SHA main-check evidence,
open-finding evidence, canonical merge-hold observation, and explicit supersession references. Their
eligible/blocked decision is derived and rejects contradiction. Only an eligible qualification on a distinct,
strictly later corrective contribution can supersede a blocked qualification. Operator provenance covers only a supplied
contiguous first-parent range bound by start/end/count/ordered digest and authorization. The eligibility
projection accepts the whole anchor-through-candidate chain, gives known contributions precedence over
operator coverage, and returns explicit uncovered/unqualified/legacy blockers. It never reads Git or the
network and does not claim the candidate is current remote main; the later detached runner owns that proof.
Recognized legacy pending evidence is reconciled only by an exact, fully covered anchor-to-marker chain. Its
immutable identity binds the ordered-commit and original coverage digests; later projections revalidate the
same ancestry and require that every prefix commit remains mechanically covered, while allowing stronger
append-only evidence to replace the original coverage choice. Unrecognized or tampered evidence stays blocked. The v2
record also initializes `satisfied_contributions=[]` but Phase 2B exposes no writer: Phase 4 activation must
atomically advance that high-water from a successful receipt before historical contributions may be excluded
from later ancestry projections.

This extension grows the permanent deployment module beyond Phase 2A because it contains the complete closed
fact validation and pure eligibility projection, not a second workflow or compatibility path. Its review must
report exact line growth and later deployment phases must extend or compress this module inside the accepted
repository-wide final budget; they may not add another deployment authority.

### Phase 2 gate

- Publication and qualification have one mechanically observed receipt path.
- Every first-parent ancestry gap from the mechanically selected effective anchor blocks
  activation eligibility.
- DeploymentRecord has no activation operation and no maintenance owner yet.
- Legacy checkout fast-forward is staging divergence, never loaded-source truth.

## Phase 3 — one application-command authority over v1 state

Do not perform a giant command-layer rewrite. Move one command family at a time while the v1 record
shape remains active. Each PR deletes its old direct writer immediately.

Required PR order:

1. **3A — project registration/removal**;
2. **3B — L3 turn/session state and action journals**: make `sessions[provider]` canonical, add a
   dormant stopped-state importer for selected-session mirrors, and reconcile or refuse every
   pending `l3-actions`/issue draft by its stable effect id before deleting that writer;
3. **3C — task intake**: creation, immutable GitHub hydration, request, and brief creation;
4. **3D — task conversation/steering**;
5. **3E — lifecycle control**: block/resume/reject/merge-hold;
6. **3F — outcome/settlement/publication requests** (the Phase 1 external-effect operations remain
   the effect owner);
7. **3G — fault/hold/recovery commands over v1 records**; and
8. **3H — bounded issue publication command**; and
9. **3I — project/L3/action/event cutover callbacks and closure**: expose the stopped-state project
   registry, L3 session, pending action/draft, and project-event importers as fixed typed callbacks
   for the later ActivationOperation; no caller exists yet. Fixture execution records each domain's
   intent/count/hash/cutover receipt and static inventory proves no normal-runtime fallback selector
   or old active writer remains.

Project/task event filenames and transition ids migrate with the command family that owns their
source record, not in a final cleanup sweep. Each PR includes a stopped-state fixture importer or an
explicit proof that the frozen production family is empty. Transcript-bundle version reconciliation
belongs to Phase 9 and remains read-only until then.

HTTP, CLI, Codex brokers, server timers, and tests call the same in-process command. Stopped-state
Claude importers/decoders are evidence-only and never call a command. Adapters parse/authorize and
return typed results; they never write state themselves.
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
temporary restart adapter is added: PR 4A's gate is dormant until PR 4B's detached runner becomes
fully testable behind the guard; final PR 10A.2 later makes that runner the first and only production
claimant after every cutover callback exists.

### PRs 4A.1-4A.5: complete admission closure, claimant disabled

- **4A.1 — dormant gate:** extend DeploymentRecord with one maintenance latch and activation slot;
  add writer-preferring shared admission, exclusive claim, re-entry rules, and strict owner
  capability. No production path may claim it.
- **4A.2 — application ingress:** make HTTP POST, mutating CLI, and every Phase 3 command acquire
  shared admission before recovery/project/task/operation/Git locks; keep reads byte-stable.
- **4A.3 — model and external effects:** cover L3/L2/L1 process boundaries, settlement, Git/GitHub,
  issue publication, and recovery actuation through their durable result.
- **4A.4 — background and host ingress:** cover timers, cleanup, mutating snapshots, quota caches,
  transcript export, digest remnants, and external hooks; make hidden mutating reads pure or
  classify them as mutations.
- **4A.5 — closure proof:** generated static inventory and race tests prove every mutation/model/
  effect ingress participates. Keep the dormant-claim guard through final source closure; until then
  no code can acknowledge maintenance.

Exclusive acquisition waits for shared holders, proves every Phase 1 unit and effect operation
empty/reconciled, and only then writes acknowledgement. It never guesses from task labels, provider
rows, quiet time, or bare PIDs. Shared-to-exclusive upgrade is forbidden. An acknowledged candidate
starts health-only; timers/mutations enable only when activation receipt and gate release are
recorded atomically.

### PRs 4B.1-4B.4: detached latest-main activation, claimant still disabled

- **4B.1 — resolver/builder:** detached remote resolution, qualification/ancestry check, strict
  prior-install-A/candidate-C comparison, tests, web build, and staged hashes; no service or gate effect.
- **4B.2 — service transaction:** fixed stop/install/health-only start/verify/restart-policy stages
  behind a fake service-manager adapter; no production claimant.
- **4B.3 — forward-repair bootstrap:** system-Git locator/re-exec path and failed-held replay that do
  not import installed candidate code; still disabled for production.
- **4B.4 — dormant integration:** bind the maintenance owner, current Phase 3 cutover callbacks, and
  closed ActivationOperation behind the dormant guard; prove the whole disposable E2E and static
  absence of any second claimant. Production invocation remains impossible until the final
  pre-activation enablement PR after every required callback exists.

Build and exercise the complete activation runner against disposable installations. Source merge
does not install or invoke it in production. On a later separately authorized use, it first
acknowledges the maintenance gate, then records live `running_install` receipt A while a quiesced old
service remains up. For the already-stopped first activation only, A is instead an exact bootstrap
`stopped_install` bound to the immutable freeze/provenance receipt. It then fetches a detached exact
checkout of latest qualified `origin/main`, records that checkout's
strict `detached_candidate` manifest C, runs C's preflight/tests/build, and validates the intended
installed-to-C repository/state/unit transition. After it suppresses restart and proves the old unit
empty, it records strict `stopped_install` manifest B and requires B's installed bytes/configuration
to match A. A, B, and C hashes, entries, unit/template hashes, state versions, and C commit enter the
ActivationOperation before source change. Candidate validation never pretends manager ExecStart
already points to C; stopped validation never executes B or tolerates internal/A-to-B mismatch. The
runner then passes explicit installed-checkout/state-home targets to C's deploy tool. `make restart`
only locates and re-execs this detached tool before importing installed candidate code.

```text
claimed -> gate_acknowledged -> prior_install_recorded -> restart_policy_suppressed
        -> candidate_resolved -> candidate_built -> remote_revalidated
        -> prior_install_revalidated -> old_service_stopped -> stopped_install_revalidated
        -> source_assets_installed -> state_cutovers_applied
        -> candidate_started_health_only -> verified -> verification_recorded
        -> restart_policy_restored -> activation_recorded -> complete
        -> failed_released (only before old_service_stopped; old source/service unchanged)
        -> failed_held (after suppressed old-identity loss or old_service_stopped; retaining evidence and gate)
```

For first activation, `claimed` is also the atomic bootstrap of missing deployment authority. Under
the deployment lock it requires record absence, exact frozen state/bootstrap stopped-install/
process-empty evidence, a stable authorization/attempt id, and a read-only preclaim remote target.
One atomic replace creates the bootstrap anchor, exact operator-provenance coverage through that
target, held latch, and sole ActivationOperation. Retry with the same identities reconciles; a
different initializer or mismatched hash refuses. Later remote advancement adds exact coverage under
the same bounded authorization without changing the anchor.

The runner stages source/web/unit compatibility without importing installed code, refetches before
source change, and restarts resolution/build if remote main advanced. After revalidation, later
external merges wait for the next activation. It suppresses restart immediately after recording A,
then revalidates the exact old PID/start/source after candidate build. If that identity disappeared
or changed, the operation records the mismatch, idempotently stops/proves the entire unit empty,
records `old_service_stopped(reason=identity_lost)`, and ends `failed_held`; it never releases the gate
or adopts the replacement PID as A. Otherwise it stops the old service and
proves its cgroup empty, revalidates stopped B against A, installs the exact staged assets, applies
and reconciles every selected state cutover from detached C, starts health-only, verifies
PID/start/source/schema/API/SPA/bundle, restores normal crash policy, and atomically records
activation, satisfied contributions, enablement, and gate release.

Before old-service stop, an ordinary preparation failure restores policy, proves the unchanged old
generation, and records `failed_released`. On the already-stopped first activation, that same outcome
instead proves unchanged stopped A/B plus frozen state and leaves the service stopped. Old-identity
loss/replacement is the explicit exception: the authority advances through the idempotent empty-unit
boundary and records `failed_held`, as do all failures after `old_service_stopped`.
The installed unit fixes `StartLimitIntervalSec=120`, `StartLimitBurst=3`, `RestartSec=5`; the runner
receipts `reset-failed` and caps each attempt at three candidate generations. Forward repair uses
system Git plus a reviewed state-compatible detached candidate even if installed imports/Makefile are
broken. A new explicit authorization names the terminal failed attempt, proves its claimant and every
service process empty, reconciles installed SHA/cutover receipts, and atomically transfers the held
gate owner through one fixed repair capability; it never releases the gate between attempts or
reopens the old generation cap. No separately installed mutable bootstrap or second deployment
selector exists.

### Phase 4 tests

- Race every admitted entry against exclusive claim; no mutation starts after acknowledgement.
- Kill/race absent DeploymentRecord initialization; the same authorization reconciles one held
  attempt and a conflicting initializer cannot create an anchor, latch, or operation.
- Unknown L3/L2/helper ownership or external-effect state refuses drain.
- Kill every activation, restart-policy, receipt, and gate stage.
- Crash the old service to zero PID and race a replacement PID between prior-install recording and
  candidate build; each path empties the unit and reaches `failed_held`. Kill/replay while recording
  the mismatch, stopping the replacement, proving emptiness, and persisting the stopped boundary.
- Break installed imports/Makefile; detached forward repair still works.
- Pre-stop failure preserves old source/service; post-stop failure leaves one held gate and no loop.
- Race failed-held repair takeover; only a newly authorized attempt with reconciled evidence can
  atomically own the still-held gate, and no task/model/old attempt runs in between.
- Run a disposable activation E2E; never use the production service as the experiment.

## Phase 5 — delete legacy self-deploy while production is stopped

### PR 5A: remove the obsolete authority

Delete `dispatch.pull_after_done`, scheduler checkout fast-forward, restart-pending compatibility,
their tests/fields, and every startup path that interprets checkout movement as loaded-code truth.
This is an ordinary reviewed source PR landed outside Altitude while the prerequisite production
freeze remains intact. It neither claims the maintenance gate nor writes a synthetic deployment
receipt. The eventual first ActivationReceipt covers the exact first-parent refactor range through
operator provenance and installs the already self-deploy-free candidate.

### Phase 5 gate

- Production state, source, assets, unit, and service remain unchanged by the PR itself.
- Static search proves self-deploy and restart-pending code absent in the candidate.
- Deployment transition still has activation as its only subtype and the detached runner remains
  the first/only claimant.

## Phase 6 — one-shot task v2 cutover

### PRs 6A.1-6A.3: build one cutover candidate

- **6A.1 — dormant consumers/import contract:** land v2 API/UI/read-model consumers and a strict
  stopped-state importer; prove them against fixed fixtures with no active selection branch.
- **6A.2 — v2 task commands:** implement the sole v2 reader/writer set and activation-owned import
  hook, still exercised only in copied homes/disposable activation.
- **6A.3 — remove active-v1 source paths:** delete v1 report/verified/reported/dispatching writers and
  fallback readers from the candidate. Retain the real-state importer and isolated legacy archive
  decoder; neither may be deleted before a successful production ActivationReceipt.

At the eventual separately authorized activation, acquire and acknowledge the maintenance gate
before the final emptiness proof and keep it through health-only start/format switch. Before switching:

- finish and archive a v1 task; or
- stop every owned process, preserve useful work in a GitHub issue/branch, and reject it.

Stop the old service and recheck that no active/resumable v1 task, operation, or helper remains.
Fresh active tasks then use only
`task.json` schema v2. Archived `status.json` tasks stay immutable audit evidence through an
isolated read-only decoder used only by archive queries/exports; active mutation, dispatch, resume,
worker, and settlement code cannot import it. There is no `state-formats.json`, dual writer,
migration-hold selector, or translated live owner.

The v2 task states are `queued | running | settling | blocked | done | rejected`. `done` and
`rejected` carry canonical terminal actor/time/reason or result receipt. Moving a terminal directory
to archive changes storage only, never lifecycle truth.

Before source change, the embedded activation operation records a task-domain cutover intent with
old/new schema, the empty-domain proof, and candidate SHA. After the health-only candidate proves the
new reader/writer set, `activation_recorded` appends the immutable task cutover receipt to that same
ActivationReceipt. A crash before the receipt leaves the gate held; replay reconciles installed SHA,
the process condition required by the parent stage, and domain emptiness before emitting it or fails
closed. Before `candidate_started_health_only`, zero service PIDs are required; at and after that
stage, the one exact health-only PID/generation is required. The receipt is audit evidence, not a
runtime format selector.

### Phase 6 tests

- Reject while owner/helpers exit; archive only after all units are empty.
- No-code completion refuses a changed worktree.
- Unknown v2 state refuses before any provider/network effect.
- Disposable activation imports the exact frozen-state fixture once and cannot delete/skip the
  importer merely because that fixture passed.

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
- Actual Codex launch/quota failure creates a Codex hold. It never falls back to another engine;
  retry or resume waits for Codex eligibility or explicit operator action.
- Persist the selected observation plus policy version/hash, not a mutable reserve policy in tasks.
- Weekly comparable allowance is primary; short windows are availability gates.

### PRs 7C.1-7C.4: typed boundary results and non-global holds

- **7C.1 — dormant types, registry, and importer:** define closed effect-result/blast-radius types,
  one canonical hold registry command, and the stopped-state importer with race/kill fixtures; no
  caller switches and no normal-runtime writer is enabled.
- **7C.2 — task/provider callers:** migrate task- and provider-scoped failure callers and delete only
  their old hold writers; unknown mappings remain global until characterized.
- **7C.3 — project/repository callers:** migrate project- and repository-scoped Git/publication/
  cleanup callers and delete only their old writers.
- **7C.4 — system boundary and activation hook:** migrate the remaining explicitly global callers,
  prove observability/cosmetic failures cannot actuate a hold, attach the hold importer/switch to the
  single ActivationOperation, and prove static closure. Global failures continue to invoke the v1
  RecoveryCommands through its one existing adapter until the atomic recovery-v2 switch in 8D; this
  PR changes their typed result/scope, not their recovery record authority.

At the eventual activation-owned cutover under the acknowledged gate, preserve every frozen hold's
stable id, scope, retry evidence, and revision; write/reconcile the import before enabling the new
writer; and record counts/hashes in the ActivationReceipt. Source PRs retain the real-state importer
through successful activation. Kill/replay proves no lost, duplicate, or briefly absent hold.

## Phase 8 — recovery v2 and incident evidence

### PRs 8A-8D: one recovery cutover, built in reviewable slices

- **8A — dormant recovery/incident contracts:** strict v2 episode, logical wake/physical claim,
  clearance/inactive epoch, UUID incident/amendment, and cutover-receipt validators plus fixed fixtures; no writer.
- **8B — stopped-state importer:** import legacy Markdown/amendments/indexes and frozen clear recovery
  state into a staged keyed ledger plus a canonical inactive recovery epoch, with row-level
  kill/replay tests. It cannot enable v2 or delete
  its input and remains shipped through the first production activation.
- **8C.1 — dormant fuse and incident commands:** implement state-first launch fuse plus keyed incident
  append behind a no-caller boundary; keep all v1 runtime callers/writers unchanged.
- **8C.2 — supervisor wake ownership:** implement one logical wake per revision, replaceable physical
  claim, and capped-backoff abandonment/retry with fixture-only callers; keep v1 unchanged.
- **8C.3 — recovery task and permit:** implement at-most-one recovery L2, waiting-operator behavior,
  and episode permit fencing with fixture-only callers; keep v1 unchanged.
- **8C.4 — dormant clearance:** implement keyed clearance receipt and active-to-inactive transition
  against fixtures only while preserving the current inactive-epoch ABA fence; keep
  v1 runtime clearance unchanged.
- **8D — activation hook and closure:** attach the single recovery/incident import-and-switch to the
  parent ActivationOperation, switch all runtime callers to the complete v2 command set in this one
  integration PR, and delete all v1 active writers/readers together; static inventory proves no v1
  active writer/fallback selector remains. Dormant implementation in 8C keeps this cutover PR small
  and prevents a mixed v1/v2 episode from ever becoming a normal-runtime state.
  A disposable activation E2E is required, but is not evidence that production migrated.

The eventual real cutover runs only as the separately authorized activation under the acknowledged
maintenance gate, with the frozen old service stopped and no active v1 recovery episode or recovery
task. Recheck emptiness and the prerequisite state hash. Clear/close the existing episode through
current verified rules before the freeze; do not translate a live supervisor or permit a v1 fault
writer to race the switch.

Use the same activation-owned cutover contract as Phase 6: persist recovery-domain old/new schema,
inactive-epoch/no-active-episode proof, and candidate SHA as intent before source change; after health-only verification,
append the immutable recovery cutover receipt inside the ActivationReceipt. Crash replay reconciles
installed SHA, domain emptiness, and the process condition required by the parent stage while the gate
remains held: zero service PIDs before `candidate_started_health_only`, then one exact health-only
PID/generation. No active reader consults the receipt to select a format.

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
- clearance is a keyed embedded operation: reconcile its receipt, then replace the active episode
  with a canonical `state=inactive` record. The record retains a monotonic epoch, cleared episode
  and revision, prior epoch, and receipt digest; then reconcile the audit row. A no-active permit
  binds that epoch, and Phase 8/v2 must preserve the ABA fence.

PR 8D deletes legacy active recovery attention retries, duplicate project-hold writers, and
incident-index writers together only after the complete candidate E2Es pass. Real-state importers and isolated audit
readers remain through the successful production ActivationReceipt; only a later post-activation PR
may delete them.

Kill tests cover task-claim intent/create/finalize, a failed or rejected first recovery task, new
evidence after the slot is spent, recursive recovery-origin fault refusal, logical-wake claim and
abandoned-claim replacement before/after `retry_at`, capped-backoff replay, clearance receipt/inactive epoch,
runtime incident append, every legacy incident import row, and cutover-receipt reconciliation. Every
case retains one episode, one physical wake claim at a time, and no more than one repair task.

## Phase 9 — product surfaces and conservative cleanup

### PRs 9A.1-9A.3: one wire contract and projections

- **9A.1 — backend projections:** adopt the dormant Python task/operational wire validators and
  consolidate status/monitor/server reads without changing the UI.
- **9A.2 — web boundary:** adopt matching Zod validators and shared fixtures, then simplify one UI
  route family at a time.
- **9A.3 — conversation/evidence cutover hook:** reconcile transcript bundle versions and legacy
  L3/action/event projections through isolated read-only decoders; attach the transcript importer and
  schema switch to the single ActivationOperation with intent/count/hash/receipt tests; remove active
  fallback reads only when the frozen-state importer/decoder inventory proves coverage. The L3,
  action, and project-event state cutovers themselves remain owned by PR 3I rather than being
  reimplemented here.

There is no fictional cross-language library or generated mutable authority. Status, CLI, API,
briefs, and UI are read-only projections from canonical records.

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

After journal parity is proved, PR 9B deletes the legacy `altd.log` writer and file. The existing
supervisor-owned system journal remains the only service-log source.

### Phase 9 tests

- Contract fixtures fail closed on unknown versions/extra authority fields.
- Chat/task/live transcript E2Es preserve steering generation checks.
- Web remote checks run on the exact candidate.
- Cleanup refuses dirty, unowned, live, unmerged, or ambiguous artifacts without affecting dispatch.

## Phase 10 — pre-activation final deletion and documentation

### PR 10A.1: source finalization while production remains frozen

Delete every temporary selector, compatibility **writer**, active fallback reader, superseded
persona, schema, endpoint, field, test fixture, and proposal instruction that describes rejected
behavior. PR 10A.1 also deletes `monitor/hook-faults.log` and the runtime hook-fault drain after fault/event
parity. The legacy `hooks/guard.py`, `hooks/statusline-monitor.sh`, and their Claude settings remain
temporary through the first ActivationReceipt because they are part of the frozen legacy-empty
proof, not the permanent target. Retain only those named temporary checks, the explicitly enumerated
real-state importers, and isolated archive decoders needed by the first production activation. They
are reported separately as temporary migration source and cannot select normal runtime behavior.
Git history is the archive; active context contains one architecture.

Run static searches for every deleted name and report intentional read-only archive-decoder matches.
Record final production/artifact/writer/timer/dependency counts against Phase 0. Run:

- full Python suite;
- web tests, typecheck, and production build;
- all kill/race tests;
- disposable activation and forward-repair E2E; this is source-integration evidence only and never
  activates, installs, or restarts production;
- disposable real tiny Codex L3 -> L2 -> PR/check/merge path;
- no real Claude turn; tests instead prove every launch/resume path closed and the activation manifest
  proves every legacy Claude unit/process empty;
- task defect, Codex outage/hold/retry, and global recovery E2Es; and
- restart inspection proving no worker/helper/service process remains unexpectedly.

Do not restart the production Altitude service merely because implementation merged. Activation and
restart remain a separate explicit operator action.

### PR 10A.2: sole claimant enablement

One small integration PR removes the dormant activation guard only after static inventory proves the
closed cutover callback list contains project/L3/action/event, task, operational-hold,
recovery/incident, and transcript domains; every callback has a kill/replay fixture; the complete
disposable activation/forward-repair drill is green; and no second maintenance claimant exists. It
adds no new importer, state format, or fallback. Merging it makes the detached runner eligible for a
later explicit operator authorization; it does not invoke, install, or restart production.

### PR 10B: post-activation importer deletion

Only after the separately authorized production activation has a successful receipt with exact
import counts/source hashes/cutover receipts may a later reviewed PR delete the consumed real-state
importers, `hooks/guard.py`, `hooks/statusline-monitor.sh`, their retired Claude settings, and tests
that exist only for those legacy paths. That later candidate is installed through another explicit
activation. Until then, the
pre-activation repository is considered source-refactor complete but intentionally retains bounded
migration code; disposable success or elapsed time is never a deletion signal.

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
