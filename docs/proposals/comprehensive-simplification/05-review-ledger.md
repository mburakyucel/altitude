# 05 - Review ledger

> Draft review aid. Check this ledger against the current-system evidence, proposed architecture,
> exhaustive component decisions, and migration gates. Approval of one row does not authorize an
> implementation PR until its dependencies are approved too.

## Cross-cutting decisions

| ID | Proposed decision | Depends on | Why it is not safe in isolation | Evidence of completion |
| --- | --- | --- | --- | --- |
| S01 | Faults have advisory/task/project/provider/global scope | Canonical fault record; application commands | Narrowing the fuse before callers are classified could let an uncertain writer continue | Every fault caller is typed; global race tests remain; scoped E2E proves unrelated dispatch continues |
| S02 | Task dispatch/resume pins fetched `origin/main` without requiring local deployment `main` to match | Worktree validation; publication receipt | Removing the exact-main call without pinning and validating the task base weakens provenance | Dispatch during deployment lag starts from exact fetched SHA; dirty/diverged task worktree still refuses |
| S03 | Active task states become queued/running/settling/blocked with fixed worker-transition and settlement journals | Normalized outcome; process-unit ownership; archive migration | Deleting `reported` alone would lose launch/steer/closeout crash recovery | Kill/restart at every process and settlement boundary completes once with no duplicate generation/PR/FYI |
| S04 | One immutable publication receipt per actual attempt; one DeploymentRecord owns activation | Trusted landing; exact check policy; independent verification | Removing reports before canonical receipts exist loses audit; storing mutable deployment on tasks splits truth | Status/archive/cleanup read ordered task receipt references; one activation satisfies many merge receipts by ancestry |
| S05 | Claude and Codex expose one untrusted `WorkerOutcome` | Provider adapters; S03 | Forcing identical launch/security mechanics would weaken Codex or overbuild Claude; acting before descendant exit leaves a writer | Same fixtures settle equivalently; every provider process unit is empty; Codex containment remains fail-closed |
| S06 | Scheduler no longer self-deploys or performs forensic cleanup | S02; deployment command; maintenance command | Removing fast-forward without an activation path makes `make restart` unusable; deleting cleanup without retention visibility leaks silently | Merge marks pending; one authorized command activates exact SHA; leftovers are visible and non-blocking |
| S07 | One canonical record per fact, including non-global holds and deployment | S03/S04/S01 migrations | Deleting projections before consumers move creates missing context; retaining both indefinitely preserves contradictions | Consumer inventory reaches zero for removed fields/files; projections regenerate from task/hold/recovery/deployment authorities |
| S08 | Default repository WIP is one; paths are publication scope | Project policy migration; L1 contract | Removing lease scheduling while WIP remains >1 can reintroduce collisions | WIP-1 E2E; path enforcement at publication; explicit >1 remains disabled until separately designed |
| S09 | GitHub issues hydrate during intake only | Trusted command layer; immutable request reference | Deleting resume hydration before all active tasks have snapshots strands them | URL-only task either has snapshot before queueing or fails intake; resume never accesses GitHub for missing context |
| S10 | Action schemas use action-specific variants and one Outcome definition | S03/S05 | Loosening schemas without broker validation expands untrusted authority | Invalid/extra fields fail; every action has only relevant required fields; live provider test passes |
| S11 | Remove TTS, edit counts, dead endpoints/fields, old L1 PR handling | Caller/consumer inventory | Cosmetic code can still be a hidden quota or status dependency | Static consumer search is empty; Python/web tests and monitor/route E2E pass |
| S12 | Chat is L3 intake; Task is L2 conversation | Application commands; UI read models | Removing duplicate UI before canonical surfaces work degrades operator control | No direct `tasks.new` UI path; L3 creates a task; Task steering resumes exact generation |
| S13 | Transcript snapshots occur at lifecycle boundaries/incrementally | Retention decision; settlement boundaries | Removing per-event sync without terminal capture can lose interrupted evidence | Kill at launch/resume/settlement boundaries retains promised evidence; live transcript still updates |
| S14 | CLI/HTTP/brokers share one command layer, kernel locks, and a closed typed operation-journal utility | Command inventory; lock order | Partial migration creates two authorities; a lock alone cannot recover process/network effects | Mutation parity and crash tests; no direct state writes; only five fixed journal variants, no workflow DSL |
| S15 | Active compatibility paths are migrated and deleted; retained audit decoders are isolated/read-only | Versioned migrator; active/archive inventory | Deleting fallbacks before conversion strands evidence; importing historical decoders in active paths preserves ambiguity | Dry-run/applied receipts; zero active compatibility imports/counters; explicit decoder package only where retention requires |

## Proposed default choices requiring Burak's review

### D1 - Repository concurrency

**Recommendation:** default to one L2 per repository, including Altitude, until a concrete need and a
reviewed conflict policy justify more. Preserve cross-project concurrency and optional L1 parallelism
inside the owning task.

Why: isolated worktrees prevent file races but not two PRs moving the same base. Current predictive
leases require natural-language parsing, brace expansion, broad-path suppression, resume ordering,
and two different meanings for scheduling versus publication. WIP one removes most of that machinery.

Review question: Is concurrent top-level L2 work within one repository a required product capability
now, or may it be explicitly deferred?

### D2 - Transcript retention

**Recommendation:** keep live transcript viewing and durable boundary/terminal snapshots. Retain
portable export, but select a finite or operator-managed retention policy rather than making indefinite
provider-native duplication an implicit guarantee.

Review question: Do we need portable provider-native archives for every attempt, or is a redacted
canonical task timeline plus terminal evidence sufficient?

### D3 - Platform-managed L1s

**Recommendation:** retain the current managed helper concept during the core refactor, while removing
old PR parsing and cleanup coupling. Evaluate native provider subagents only afterward.

Review question: Must Altitude support cross-provider L1 selection and visible patch capture, or is
"L2 may use its provider's native subagents" sufficient long term?

### D4 - Claude authority

**Recommendation:** retain the Claude guard and backend authority checks initially. Normalize only its
terminal outcome and add mandatory per-worker process-unit ownership/empty proof. Consider a smaller
allowlisted wrapper or OS-contained Claude later, and remove the shell parser only after the precise
replacement threat model is approved and proved.

Review question: Is reducing the 1,000-line shell guard worth changing Claude's direct execution
model, or should that remain accepted provider-specific complexity?

### D5 - Deployment rollback

**Recommendation:** make the authorized operator command own source fast-forward, build, restart, and
activation proof. Build in a detached candidate first; stop the old service before swapping source.
A post-swap failure leaves the service stopped and target unsatisfied. Keep source recovery as a
reviewed revert/fix PR in the first simplification. Add versioned releases only if automatic full-code
rollback is explicitly required.

Review question: Is a full automatic rollback worth the extra release/symlink machinery, or is
preflight plus explicit revert acceptable for this local self-hosted service?

### D6 - GitHub issue publication

**Recommendation:** an explicit request from Burak to create an issue is sufficient authorization for
the bounded trusted broker. A model-originated suggestion remains a draft until approved.

Review question: Do you want every Codex-created issue to require a second exact approval phrase even
when your triggering message already explicitly requested publication?

### D7 - Project inbox/FYI lifecycle

**Recommendation:** keep FYIs only if they have a real acknowledgement/archive lifecycle. Otherwise
derive recent notifications from canonical project/task events and remove `inbox.jsonl` plus the unused
`seen` field. Blocking decisions/questions/findings remain typed task attention either way; Inbox is
never completion authority.

Review question: Should Inbox be a durable acknowledgement queue, or simply a read model of current
decisions and recent noteworthy events?

## Reviewer consistency checklist

### Ownership and concurrency

- Can two physical workers ever be current for the same task, and does replacement install a new
  generation only after the prior writer is proven stopped?
- Does every message, helper launch, outcome, publication, and resume use the same generation check?
- Can a crash before/after spawn, bind, session capture, or message delivery be reconciled without a
  duplicate provider resume or an unregistered writer?
- If WIP is one, has scheduling lease logic actually been deleted rather than left dormant?
- If WIP greater than one is retained, is its conflict policy explicit and mechanically tested?

### Provider neutrality and security

- Are role semantics common while provider security boundaries remain separate?
- Does a provider switch create an explicit new attempt rather than pretending to resume?
- Can any contained model choose paths, credentials, commands, base refs, tests, or service actions
  that should be selected by the trusted control plane?
- Is containment proven empty before any Codex side effect?
- Has any Claude protection been removed before an equivalent boundary is live?

### Publication and completion

- Is each PR/head/base/check/merge fact owned by its immutable publication receipt and mutable
  activation truth owned only by the DeploymentRecord?
- Does settlement survive termination at every external side-effect boundary?
- Are model-authored claims excluded from trusted verification/publication/deployment receipts?
- Can helper continuation and a post-merge correction return the same task from settling/blocked to
  a newly fenced running generation?
- Can a clean task complete without another L3 turn?
- Do open findings, decisions, FYIs, follow-ups, or merge holds still reach the correct human?
- Are required and intentionally skipped checks classified consistently?

### Recovery

- Can an advisory or task/project-local failure freeze unrelated projects or providers?
- Does every global condition represent actual uncertainty about shared safety?
- Can mechanically resolved conditions reconcile without another model turn?
- Can L3 still recover autonomously and delegate exactly one repair L2?
- Does a new global fault stop a recovery L2 that was exempted for an older fault?
- Can recovery failure recurse into another incident/task/wake?

### State and compatibility

- For each fact, which record is canonical and which views are derived?
- Do project/provider holds survive restart without duplicating task or UI state?
- Is every compatibility read paired with a migration and removal release?
- Can active and archived tasks both be migrated and audited?
- Are secrets/capabilities absent from logs, transcripts, issues, PRs, and migration reports?
- Will rejected proposal documents be deleted rather than left beside active architecture?

### Operations and UI

- Can merge, deployment, restart, and maintenance happen independently?
- Can one activation satisfy several immutable publication receipts while a later merge remains
  pending, without mutating archived tasks?
- Can cleanup refusal ever affect task execution?
- Does `make restart` identify and verify the activated source SHA?
- Is Chat the high-level L3 surface and Task the detailed L2 surface?
- Are exact transcripts and diagnostics available without dominating normal conversation views?
- Does CI cover every retained first-class runtime, including the web UI?

## Approval recording

Before implementation, record for each D1-D7 choice:

```text
decision:
rationale:
accepted consequences:
rejected alternative:
reviewer/date:
```

Then mark each S01-S15 as accepted, amended, deferred, or rejected. Implementation issues should name
the S IDs they advance, their prerequisite phase, and the exact legacy paths they delete when done.
