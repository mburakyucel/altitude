# Altitude architecture

> **Candidate-source status.** This document describes the current Phase 1B.3 integration candidate.
> Production remains stopped and byte-frozen at
> `97e11979bdc0814ad5067eab717f999d1c251437`; no ActivationReceipt exists. Sections explicitly
> labeled dormant are implemented facts with no normal caller. The comprehensive-simplification
> pack describes planned target behavior, not behavior activated by this source.

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

Enabled autonomous roles run on Codex; Claude launch paths are byte-inert after the Phase 1A
NO-GO. Fresh L2 dispatch records one provider choice and keeps that provider for the attempt. L3
keeps the canonical resumable Codex conversation. Historical Claude session identity remains
read-only observation/cleanup evidence and is never selected or resumed. See
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

Legacy Claude rows, hooks, and process records remain only as read-only stopped-state evidence and
for exact stop/cleanup. No autonomous path launches or resumes Claude; the retained backend checks
cannot be used as a routing or mutation fallback. Same-UID environment, TTY, and caller flags are
not operator authentication. Activation must prove every legacy Claude unit/process empty. Codex
workers have no control capability or
Git-publication authority. A Codex L2 may write only in its task worktree under an explicit
permission profile; its Git common directory and Altitude state are outside that writable surface,
and hosted tools and model-command network access are disabled. The inner Codex sandbox hides host PIDs, while the
entire process tree runs in a transient user cgroup. Only after the unit is empty does a trusted broker validate its
strict, inert final action and perform any requested state change or landing operation.

Codex L3 uses the same containment and broker boundary. It receives a disposable writable runtime directory while
Altitude's compact state and the selected project checkout are mounted as explicit read-only inputs. This gives the
Codex runtime the small amount of scratch space it needs without giving the coordinator write access to either source.
The target L3 projection is generation-keyed and redacted: bounded recent human chat; each active
task's title, state, blocked reason, direct question, merge hold, and publication references; and the
current recovery epoch. It excludes provider stores, raw logs, home paths, secrets, credentials, and
broker capabilities. The projection lives beneath the expected read-only runtime root and is rejected
when its generation does not match the claimed L3 turn.
The user manager creates the transient containment service, so Altitude keeps its own `NoNewPrivileges` hardening
while nested bwrap initializes inside the dedicated service. The outer launcher alone receives the user-session bus;
the Codex child starts from an empty environment rebuilt from a narrow allowlist, with that bus, its runtime socket
tree, ambient service credentials, and scoped L2 capabilities removed. A deterministic host canary verifies that the
inner sandbox cannot see or signal a known host PID; the trusted host can still stop the whole cgroup.
A GitHub-issue action can save only the exact current user message under a title quoted from it. It
remains a private draft: this candidate has no publication-approval action. Publication stays
dormant until Phase 1C.4 supplies exact-repository replay and a trusted command; secret-shaped
content is still refused.
L1 implementers receive narrower write subleases; the
trusted wrapper verifies that their parent commit did not move and captures their changes as a
patch for the owning L2 to evaluate.

Remote PR checks execute Python and web test/typecheck/build in independent checkouts of the same
exact candidate SHA. The
base-owned workflow accepts only same-repository heads, pins its actions and Node version, has
read-only repository permission, persists no checkout credential, exposes no token or secret, and
runs candidate commands under an empty environment with disposable HOME and cache directories.

### Physical ownership foundation and L3/L2 adoption

`engines.py` defines one closed physical transition. Codex L3 and L2 owners actively use it in this
candidate; managed-helper adoption remains planned:
`planned -> prior_stopped -> spawned -> bound -> result_observed -> empty -> complete | failed`.
Its deterministic unit, physical generation, provider-session request, stable message id, and optional
recovery episode/permit revision are immutable intent protected by one stable digest. Receipts have closed
stage-specific shapes, and the record revision is derived exactly from its receipts and durable error. L3
embeds it in `l3.json`; L2 embeds it in the active TaskRecord; the later helper adopter must embed it
in its existing authoritative aggregate. Each adopter persists through the durable state primitives.
The transition creates no sidecar
record, journal, lock, or artifact family. A recorded pre-spawn error reconciles through
`launched=false`, `bound=false`, its exact error result, and proven empty before failure, without a
provider effect. After `prior_stopped`, an empty/collected unit with neither an error nor a durable
result is ambiguous—the launch may have run and exited—so reconciliation returns
`ownership_uncertain` instead of relaunching. An unknown or populated unit can never become terminal.

Provider-neutral managed-unit helpers expose exact manager/cgroup observation and whole-unit spawn/stop while
reusing the existing Codex containment mechanics. A read-only reconciliation helper correlates the exact unit
with a durable result marker carrying the same intent digest and returns a crash-stage decision; disagreement
refuses as `ownership_uncertain` rather than guessing or relaunching.

Codex L3 is the first adopter. `l3.json` embeds the current turn, provider request, deterministic
unit, exact service-instance claim/replacement receipt, recovery permit, receipts, and keyed delivery claim;
each generation has its own result marker as
non-authoritative output in
the existing disposable L3 runtime family. A generation-keyed answer/event spool is preterminal crash evidence:
its host-written header binds the physical intent, it can become the sole marker only after the unit is empty and
the event stream proves one complete turn, and it is deleted after terminal delivery is retired. At the
`prior_stopped` crash window, that exact bounded spool is checked after unit emptiness and promoted before the
generic ambiguity decision; no spool remains ambiguous, while an incomplete or mismatched spool becomes an inert
failure and never causes relaunch. Each turn gets a new physical generation even when it resumes the
same provider thread. A process-local lock reduces duplicate callbacks; project-lock compare-and-swap elects the
sole `planned -> prior_stopped` launcher and every receipt/delivery transition. No long-held turn flock or second
owner exists. The timer schedules reconciliation through the existing deduplicated background runner, so provider
or delivery work never runs inline in the timer. A replacement records an explicit service-manager-proven delivery
handoff; it reconciles receipts but never relaunches the ambiguous `prior_stopped` boundary. Failed turns retain
their proven-empty unit and exact provider thread as the next turn's prior physical/session owner. Every turn binds
either the exact active recovery episode/revision/claim or the canonical inactive recovery epoch; the managed child
and broker recheck it before launch/message, result persistence, delivery, and each short local mutation. Browser
APIs receive only scalar session telemetry, never the
request, physical transition, or recovery claim. Unknown legacy ownership, an ambiguous collected unit, a changed
result, or a stale recovery observation fails closed. Delivery reuses the existing keyed action record and chat row:
absent/complete evidence resumes mechanically, while an ambiguous non-reconcilable `applying` action remains the
same `reconciliation_required` fence. B2 deliberately exposes no reset/disposition command; Phase 3 must add its
trusted command before activation. Remote issue publication, task resume, recovery repair delegation, and L3
recovery hold/clear are likewise rejected before action claim until 1C.4/B3/Phase 3G supplies their final authority
boundaries.

The model cannot read `ALTITUDE_HOME`. The host writes one bounded, aggressively redacted disposable projection
containing coordination-safe task facts and recent chat, then grants read access only to that exact regular file and
the selected checkout. Parent symlinks and changed projection bytes refuse. The Codex adapter caps answer and event
output while producing it; cap, drain, or timeout terminates the Codex process, after which the outer managed L3 unit
must be proven empty before settlement. Helpers do not consume the shared transition yet.

Codex L2 ownership is the second adopter. The active TaskRecord embeds one closed owner operation: exact
prompt/message, Git-preparation receipt, physical transition, bounded result, optional successor, stop intent,
and the canonical recovery epoch or episode/revision. The task-owned `owner_command` seam is the sole short-lock
serializer for B3 owner plans, compare-and-swap decisions, receipts, and projections. Dispatch performs or
reconciles Git, manager, and provider effects outside that lock and returns only typed, intent-bound receipts for
the seam to persist. Git preparation is its own closed state machine:
`planned -> applying -> prepared -> complete`. A short `planned -> applying` CAS elects the only Git-effect
intent. The existing publication-settlement lock serializes the entire idempotent fetch/worktree effect through
the `prepared` receipt CAS; if its process exits, any later exact-request reconciler can finish `applying`, while
a concurrent waiter re-reads `prepared` without repeating the effect. A receipt is written only after proving the
fetched origin commit is an ancestor of worktree `HEAD`; an untouched initial worktree must equal that commit,
while a resume may contain only task-trailed descendants. `prepared` has the exact Git receipt and no
physical authority; `complete` binds the deterministic physical intent. A stale recovery fence leaves `prepared` unchanged, and an exact retry may rebind it to the
current inactive recovery epoch without repeating Git. Only the physical `planned -> prior_stopped` winner may
spawn; observers reconcile and never relaunch.
Steering first persists the exact successor and stop intent, then proves the old unit empty before a new generation
resumes the same Codex thread. Rejection likewise persists cancellation before stop and cannot archive until the
embedded transition is terminal with an exact empty receipt. Only preparation `planned` can produce the exact
`cancelled-before-effect` receipt. Cancellation at `applying` leaves a non-archivable stop for the serialized Git reconciler to
settle; cancellation at `prepared` binds an error generation and never launches a provider. A superseded `planned`
request is closed before effect and its exact successor is promoted without Git, provider launch, or physical
history for the cancelled request. The server timer only schedules the existing deduplicated L2 reconciliation
worker; provider effects and receipt persistence never run inline in the timer. That worker has one bounded
continuation pass for replay-safe preparation, binding, physical-planned, terminal-successor, direct-steering, and
blocked-resume boundaries. It uses the existing recovery/WIP/lease and physical fences and never relaunches
`prior_stopped`. Before an admission hold is applied, the pass may only reconcile, invalidate, stop, and settle an
already-launched physical generation; the hold still gates Git, binding, successor promotion, and provider launch.
A recovery-invalidated blocked resume stays blocked under the hold and cannot be projected to `running`; after
clearance its canonical failure is surfaced and only its exact stale retry intent is consumed. Blocked resume
waits for the exact current request and physical generation's positive bound
receipt, fencing transition id, generation, and intent digest against same-request replacement; then one
`owner_command` CAS revalidates task/owner/message/retry identity, moves `blocked -> running`, clears only that
matching persisted retry intent, and writes one state event carrying the exact persisted answer. Concurrent replay
is idempotent. The blocked caller carries the exact non-secret completion expectation through its wait, so a fresh
`running` reload after a background winner succeeds only when retry fields are wholly absent and the exact bound
request/generation is unchanged; competing generations, partial residue, and reblocked identity refuse.

Each terminal physical owner generation (successful or failed) is projected into the TaskRecord's bounded
generation history with its exact transition,
managed-unit, message, event/result identities, result hash, and session evidence. Live transcript lookup validates
that generation roster and derives only those event paths; it does not scan a legacy job directory or infer an
owner from a PID or timestamp. The active task path is likewise read directly, so archive rename cannot create a
second claimant or ghost task directory. Task/project APIs omit the prompt, capability, physical record, pending
action, and deferred answer. Legacy Codex PID/job/timestamp authority and its fallback readers are deleted; legacy
Claude records remain read-only stopped-state evidence.

Phase 1B.3 is integrated on top of the reviewed Phase 1A and Phase 1B.2 fixes in this candidate. The current
mechanical whole-candidate count is recorded in §04; branch-local and pre-rebase deltas are not accounting inputs.
No new service, timer,
endpoint, lock family, dependency, or persistent artifact family is introduced. Phase 1B.4 remains the helper
adopter. PR 1C.1 replaces the temporary action/result compatibility seam, PR 1C.5 folds continuation and terminal
generation handling into settlement, Phase 3B removes session mirrors and compacts migrated journals, and PR 10B
may delete consumed legacy Claude evidence only after the successful activation/cutover receipts.

Phase 1B.2 accounting is intentionally explicit. The initial review snapshot added **361 backend lines**
(`engines.py` +47, `l3.py` +283, `l3_actions.py` +29, `server.py` +2). Closing the instance-handoff,
ambiguous-action, producer-cap, exact-stop, recovery-ABA, disclosure, and timer findings brought the standalone
`c2606e6` review snapshot to **+948 backend/CLI lines** against `768a47c`: `engines.py` +188,
`l3.py` +593, `l3_actions.py` -10, `recovery.py` +167, `server.py` +12, and `bin/alt` -2. Post-integration
receipt, bounded-error, deep-parse, and retirement-fence fixes add 30 logical B2 backend lines (`l3.py` +18 and
`l3_actions.py` +12), so the current logical B2 delta is **+978** (`l3.py` +611 and `l3_actions.py` +2;
the other component deltas are unchanged). The closed persona adds two net lines and the schema is line-neutral.
The seven B2 test modules were +1,041 net at `c2606e6`; their current logical delta is **+1,122 net** after
81 lines of hostile-test coverage. A separate +3-line Claude safety integration-test adjustment is not B2 test
accounting. Later manifest/deployment conflict resolutions are likewise excluded here; §04 reports the exact whole
candidate count.
There is no new dependency, service, endpoint, timer, selector, or semantic authority. Existing `l3.json`,
`l3-actions/`, and `l3-codex-runtime/` families are reused; the runtime adds generation-keyed disposable evidence,
and existing recovery hold/launch locks are reused. The smaller PID/lock-only design was rejected because it could
silently relaunch ambiguous work; post-read caps, whole-home access, and ordinary failed action rows could
respectively retain a live process, disclose credentials, and repeat an ambiguous mutation. PR 1C.4 deletes duplicate action-effect
state, PR 1C.5 deletes L3 closeout flags, and Phase 3B removes session mirrors and compacts migrated journals.
Those named deletions must recover this temporary growth under the final hard gates: at most 12,180 permanent
runnable lines, 9,000 backend/CLI/hook/restart lines, 43 runnable files including 24 backend files,
and 25 artifact families.

### Dormant deployment authority

`deployment.py` defines one strict `altitude.deployment/v2` record per service. It can be initialized or
upgraded from the exact Phase 2A `v1` shape only through explicit offline migration/test APIs under the
same deployment lock; normal reads never guess or auto-migrate a format. No server, CLI, timer, task
settlement, startup, staging, or restart path imports it. A canonical record contains service and repository
identity, one immutable bootstrap anchor, `activated_sha`, bounded legacy-pending evidence, ordered immutable
merge contributions and qualifications, exact operator-provenance ranges, and the record hash. It has no
maintenance owner, activation operation/receipt, build, install, stop, start, or health behavior.

An exact stable `running_install` runtime manifest supplies both the loaded bootstrap SHA and
`activated_sha`. A stopped bootstrap instead requires the exact frozen stopped-install manifest plus the
same bounded operator authorization, frozen-state/raw-evidence hashes, and an explicit evidence limitation;
its `activated_sha` is null and the anchor is not an activation receipt. Unknown or conflicting identity
refuses initialization. Legacy `restart-pending.json` bytes normalize only to bounded hash/evidence plus an
unreconciled blocker; their head never becomes loaded truth, qualification, contribution, or a launch fence.
Atomic replacement supplies file and parent-directory durability, and exact replay is byte-idempotent while
a different initializer conflicts without changing the record.

A contribution records only the immutable publication-receipt id/hash and observed merge SHA. Its replay
key is receipt id plus merge SHA; retry observation time is metadata and cannot create another fact, while a
changed receipt hash conflicts. A separate
qualification is mechanically derived from the exact merge-SHA main check, open-finding count, and canonical
merge hold. Any one makes it blocked. Only an eligible qualification for a distinct, strictly later
contribution may supersede an earlier blocked qualification; history is never rewritten. Known blocked contributions cannot be hidden by operator
coverage. External commits require an operator-provenance receipt for the exact contiguous first-parent range,
including its ordered-commit digest and authorization. Eligibility is a pure projection over a caller-supplied
anchor-through-candidate first-parent chain: every commit must be covered, every known contribution must be
qualified or explicitly corrected, and imported recognized legacy-pending evidence must be reconciled against
the exact fully covered anchor-to-marker chain. Unrecognized or conflicting evidence remains a blocker. The
record reserves `satisfied_contributions` as Phase 4's activation high-water: Phase 2B initializes and validates
it as empty but exposes no writer, so historical contributions can be retired only by a later successful
activation receipt. This
projection neither decides that the candidate is latest remote main nor performs an activation effect.

## Recovery

System faults are deduplicated into private incident evidence and activate a global recovery fuse.
The launch permit is checked for both fresh and resumed work, including the final launch boundary.
Ordinary work stays held while recovery is active. The current L3 recovery turn may inspect and
coordinate, but recovery-task delegation is dormant until Phase 3 supplies the exact episode command;
it returns one needed corrective task or proposal as a human-readable recommendation instead.
Incident records are evidence only and never create tasks, personas, or follow-up work.
Separately, the active fuse carries one durable, deduplicated L3 attention request. The
server runs at most one recovery turn for it at a time, retains failed turns with bounded backoff, and
audits successful handling. The turn leaves the fuse active and reports the next bounded coordination
or recommendation to Burak; it cannot publish an issue, delegate repair, or clear recovery in this phase.
The canonical recovery record is never deleted on clearance: it atomically becomes a closed inactive record with
a monotonic epoch and the exact latest clearance receipt, then projects that receipt idempotently into the audit
JSONL. Proven-none L3 turns bind the epoch, so `none -> hold -> clear` cannot masquerade as unchanged state.
Material active evidence advances the one episode revision and invalidates old claims. The history is audit only;
it never actuates a launch or mutation.

The service lifecycle is separate from source changes. Production is currently stopped and frozen;
the planned comprehensive cutover has not run. Ordinary source changes never start, stop, mask,
unmask, or restart the service. Activation requires separate authorization, the detached activation
runner, an ActivationReceipt, and post-change health verification.

## Interfaces and storage

The Python server owns state transitions and JSON APIs. The React app provides Inbox, Projects,
Chat, and Monitor navigation plus project/task detail routes. Task chat is a human-readable Burak/L2
conversation; operational events remain an audit detail.

The current authenticated TLS certificate, private-key, CA paths, and client-trust arrangement are
unchanged and outside this simplification. No source phase is a TLS migration.

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

Runtime files live under `ALTITUDE_HOME`. Active architecture documents label candidate, dormant,
planned, and activated behavior explicitly. Hooks supply temporary Claude-side command guardrails
and telemetry only for read-only legacy Claude observation, stop, and cleanup; they are not an
active launch or command boundary. Permission profiles, process containment, and backend validation
form the Codex execution boundary.
Superseded designs remain in Git history, not in the active tree.

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

### Runtime identity and migration preflight

The read-only `alt manifest --role ...` has three closed identities. Before `bin/alt` imports any
Altitude module for `serve` or a running manifest/preflight, it captures every runtime input's bytes
and device/inode/size/mtime/ctime identity, then loads Altitude from those captured source bytes;
timestamp bytecode caches are never eligible and executable Python symlinks are rejected before import.
`running_install` revalidates that unchanged capture, the
loaded Altitude module origins, and the exact `alt serve` manager configuration against a positive MainPID/start identity in the observed
cgroup. `stopped_install` derives B from manager ExecStart, requires inactive/dead plus an empty
cgroup, reads B without importing it, and compares every installed byte/configuration fact to an
immutable prior receipt. That prior is normally `running_install`; the already-stopped first
activation instead uses a `stopped_install` with `prior_kind=bootstrap_freeze`, bound to the exact
raw prerequisite evidence bytes, the copied frozen-state tree digest, a closed service/provider-unit/
worktree/ref/process roster, and bounded operator authorization. It never fabricates or backdates
a running receipt. `detached_candidate` requires an explicit full candidate SHA, matches all runtime
and build inputs to that commit, hashes its unit template and web bundle, and never claims the old
manager points to C. Unknown, absent, mismatched, or unqueryable role evidence is invalid.
`GET /api/manifest` returns only one canonical immutable redacted byte snapshot created at startup:
hashes and status are visible, but host paths, commands, PIDs, evidence bodies, and diagnostics are
not. If startup capture was absent, the endpoint returns an explicit unavailable response and never
re-reads mutable source to synthesize identity.

`alt preflight --home <copied-home> --role ...` performs a deterministic inventory without locking,
normalizing, networking, or writing the copy or repository. It enumerates closed active
monitor/project/task/provider/operation shapes, recovery/hold/incident evidence, and every current
artifact families. Each family keeps its declared source-owner roster separate from a mechanically
observed active-consumer count: a counted source owner must contain both a read seam and a literal
family token; provider/external declarations are never silently counted as observed code. Its exact
§07 roster reports permanent,
temporary, support, policy/template, and test categories separately; new or missing production paths
block. The Python writer inventory classifies built-in/`os.open`, `fdopen`, temporary-file/directory,
permission, path-write, subprocess, and equivalent current calls; TypeScript, shell, and static inputs
have their own explicit dependency/effect analysis contracts. Unknown active entries, nested control
fields, provider evidence, owner variants, or required identity facts make cutover ineligible.
Project holds and live provider jobs must settle; statusline records and service manager state use
closed accepted fields and states, so new provider or manager shapes cannot silently pass cutover.
Unknown archived entries are reported for a separate
read-only archive decoder and never enter active dispatch, resume, or settlement. An unregistered
project is archive-only only when its complete namespace is exactly one real `archive/` directory
and no active monitor state refers to it.

The legacy preflight and every real-state compatibility importer remain shipped through the first
successful real production ActivationReceipt. Disposable success or pre-activation cleanup cannot
remove them; only the separately reviewed PR 10B may do so after frozen-state and cutover receipts exist.
