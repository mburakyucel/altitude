# Adversarial review and disposition

> This document records challenges to the draft. It is not an approval certificate. Reviewers
> should reproduce the evidence against the linked current-system inventory and source.

## Method and coverage

Three independent passes challenged the assembled proposal from different angles:

1. current-flow completeness: whether every present end-to-end flow has a legal target transition
   and crash boundary;
2. security and surface reduction: whether every keep/remove decision preserves its invariant and
   whether new machinery is honestly counted; and
3. migration/recovery operations: whether cutover, rollback claims, locks, recovery, and deployment
   remain safe across process death.

The [component register](03-component-decisions.md) was also checked mechanically against the tracked
tree: every tracked file outside this proposal appears literally in that register, either as its own
row or in an explicit group. Local Markdown link targets across the pack were resolved against the
proposal worktree. File coverage is not behavioral proof; the findings below are the behavioral
cross-check.

## Blocking findings and dispositions

| ID | Challenge | Why the first draft was unsafe/incomplete | Disposition in this revision | Reviewer recheck |
|---|---|---|---|---|
| A01 | Worker claims versus trusted facts | `report.json` risked making model-authored check/merge/deployment claims canonical | `WorkerOutcome` is explicitly untrusted; verification, publication, no-code, and deployment facts live only in trusted receipts/records. See [provider outcomes](02-proposed-architecture.md#5-provider-adapters-and-normalized-outcomes), [publication](02-proposed-architecture.md#9-trusted-publication-and-verification), and the [schema ledger](03-component-decisions.md#schemas). | Confirm no model schema can populate trusted receipt fields and old report readers label legacy claims correctly. |
| A02 | Claude descendant writer proof | Parent/job exit could leave a descendant writing while settlement publishes | Phase 2 now requires a tracked, provably empty process unit for every Claude physical worker while retaining the existing filesystem/command guard. Full filesystem containment remains D4. | Prove nested descendants cannot escape the unit and settlement refuses parent-only exit evidence. |
| A03 | Crash-safe dispatch and steering | The task states did not represent spawn-before-bind, stop-before-resume, or message delivery ambiguity | A fixed `worker_transition` journal persists deterministic generation/process-unit/message targets before launch and reconciles after crashes. See [task owner](02-proposed-architecture.md#3-task-and-logical-l2-owner). | Kill at every stage; verify one worker, one current generation, one delivery. |
| A04 | Helper continuation | `continue(helper_requests)` had no `settling -> running` path or durable helper artifacts | The lifecycle now permits a newly fenced generation after idempotent helper completion; helper records own prompt, parent fence, process, and patch/findings only. | Run helper success/failure/restart and ensure the helper never owns publication. |
| A05 | Settlement idempotency | “Replayable” was asserted without stages for commit/push/PR/check/merge/archive | `SettlementJournal` has a closed stage table with intent and observed receipt around every external effect. | Crash at every stage and verify remote reconciliation rather than duplicate effects. |
| A06 | Multiple publication attempts | A failed required main check after merge had no legal continuation | Immutable receipt attempts are ordered; the same logical task may block and resume on a fresh base/branch for a corrective attempt. | Inject post-merge failure and prove no receipt mutation or recursive healing task. |
| A07 | Deployment authority | Mutable deployment status was split among task receipts and a pending file | Task publication receipts are immutable. One per-service `DeploymentRecord` maps many merge receipts to activated SHA by ancestry and retains later merges as pending. | Claim candidate C, land another merge, activate C, and prove only ancestors are satisfied. |
| A08 | Mixed-release activation | Fast-forward/build failure could leave old Python reading new schemas/personas/hooks | Build and validate a detached candidate first; quiesce/stop the old service before source swap. A post-swap failure leaves the service stopped, gate held, and target failed/unsatisfied. | Fault every pre/post-swap boundary and verify the documented stopped-versus-unchanged result. |
| A09 | Scoped hold ownership | Project/provider holds had behavior but no durable canonical owner | `operational-holds.json` owns active non-global holds; tasks reference applicable holds. `recovery.json` is global only and UI/project hold files are derived/removed. | Restart with each scope active and verify only its declared blast radius remains held. |
| A10 | Human attention closeout | Removing `l3_handled` left no durable decision/finding/merge-hold resolution path | Blocking attention is typed task state with resolution reference; non-blocking FYIs are canonical events and D7 affects acknowledgement only. | Crash before/after attention projection/resolution and verify no lost or duplicate action. |
| A11 | L3 and issue external effects | A cross-process lock did not make a provider turn or `gh issue create` idempotent | Closed `l3_turn` and `issue_publication` journal variants bind human message, session, content/effect id, response, and action receipt. | Kill around provider response and GitHub creation; response/issue appears once. |
| A12 | Legacy owner migration | Translating live v1 tasks into new authority required inference or a permanent adapter | The migration now drains v1 tasks: finish, reject after stop proof, or preserve useful work in an issue. Fresh tasks alone receive v2 ownership. | Attempt cutover with every v1 active state; all must refuse until deliberately resolved. |
| A13 | Multi-file migration atomicity | The first plan called distributed file writes atomic and underspecified selector rollback | Importers stage/fsync complete side-by-side state and atomically update one domain in `state-formats.json` last while timers/mutations are disabled. The map preserves already-cut-over domains; its update is that domain's forward-only boundary. | Kill through staging/map update/enablement; never observe mixed writers or regress another domain selector. |
| A14 | Recovery task claim race | A crash could spend `repair_task_ever` before creating the one allowed task | `recovery_transition(task_claim)` uses one deterministic task id and `claiming -> create/reconcile -> finalized`; crash resumes the same operation. | Kill at both file boundaries and prove exactly one recoverable task id. |
| A15 | Recovery empty/clearance state | `cleared`/empty episode records added state, while append-then-remove could crash between effects | Active `recovery.json` has only open/repairing/waiting states. `recovery_transition(clearance)` uses one idempotency key to reconcile the receipt and finish removal; clearing is not a lasting episode state. | Kill before/after receipt append and removal; absence means clear only after the exact receipt/migration proof. |

## Important findings and dispositions

| ID | Challenge | Disposition | Remaining judgment |
|---|---|---|---|
| B01 | Ordinary CLI and service could remain two mutation authorities | The service is the normal command authority. Public direct `task new` is removed; future work can be recorded in GitHub while the service is down. Narrow deployment/recovery inspection commands use the same kernel locks, and diagnostic receipts never expire authority. | Verify no internal worker/recovery command is exposed as a second general intake path. |
| B02 | The fault classifier risked becoming a new recovery-rule engine | The target has two closed enums—effect result and blast radius—with static mappings owned at trusted boundaries. No runtime rule registry, regex classifier, or model decision is allowed. | Each implementation phase must demonstrate net removal under the reduction accounting. |
| B03 | WIP one makes a blocked task occupy its repository despite scoped faults | The target states this explicitly as the D1 collision policy, distinguishes it from a global fault, and reroutes the same task when another provider is eligible. | D1 remains a product choice; higher concurrency requires a separate conflict policy. |
| B04 | Provider routing behavior was preserved only rhetorically | Phase 2 now requires weekly-reserve-first, short-window-secondary, provider-local unknown/exhaustion, explicit preference, and switch/new-session tests. | Review the operator reserve policy, not a new per-task routing schema. |
| B05 | Project configuration had two possible authorities | `projects.json` is the sole registration/repository/project-policy authority; optional per-project policy files were removed from the target. | Confirm secrets remain references, not embedded project data. |
| B06 | Migration phases conflicted with small PRs | Dormant definitions/readers/tests may land in small PRs; one narrow writer cutover switches consumers together. Old and new writers never run concurrently. | Implementation issues must name preparatory versus cutover/deletion status. |
| B07 | S15 made historical evidence readability impossible | S15 now means zero active mutation/resume fallbacks. Selected versioned archive decoders are isolated read-only audit tooling and may remain. | D2/retention decides which decoders are necessary. |
| B08 | New migration/permanent components were not counted | [Target and migration component budget](03-component-decisions.md#target-and-migration-component-budget) classifies each new responsibility as permanent, temporary, deferred, or audit-only with a size/deletion constraint. | Reject an implementation that adds an unlisted permanent subsystem. |
| B09 | Frontend was first-class but remote CI could remain Python-only | The component ledger and migration matrix require remote web test/typecheck/build on the exact PR candidate. | The actual workflow change belongs to implementation, not this proposal. |
| B10 | Top-level “lease” terminology could preserve deleted scheduling logic | Top-level paths are consistently publication scope; only concurrent L1 helper scopes remain subleases. | Static searches form part of the final deletion gate. |
| B11 | Cleanup risked adding another ownership receipt | Cleanup proof derives from task/generation/publication/Git/process facts and appends an event; no cleanup authority record is added. | Cleanup remains non-critical and conservative. |
| B12 | D4 assumed only the most elaborate Claude replacement | Phase 8 follows the precisely approved threat model: OS containment or a smaller allowlisted wrapper plus backend authority. | Mandatory process-unit ownership is already Phase 2 and is not optional D4 scope. |
| B13 | Publication receipt could land without its DeploymentRecord contribution | The SettlementJournal now has an applicable contribution intent/receipt stage keyed by service, receipt id, and merge SHA. Replay accepts the exact existing contribution and refuses conflicts; Phase 1 bootstraps this permanent tail before removing self-deploy. | Kill tests must cover receipt-before-contribution and contribution-before-journal-receipt. |
| B14 | Atomic state plus audit was asserted across separate files | Each authoritative local transition retains a stable id/revision/event envelope. State is written first; startup and the next locked mutation reconcile the audit row, whose readers deduplicate by transition id. External effects continue to use only the five closed journals. | Prove no next mutation can overtake reconciliation and that truncated JSONL tails cannot erase the logical event. |

## Remaining review work

The findings above have proposed dispositions, not implementation proof. Burak still needs to record
D1-D7 and S01-S15 in the [review ledger](05-review-ledger.md). A reviewer should reject or amend the
proposal if any of these statements cannot be made concrete without adding another durable truth,
timer, workflow language, or authority path.

In particular, review these cross-document claims together:

- `TaskRecord`, operation journals, and receipts cover every transition in the
  [current-system flow inventory](01-current-system.md), including abnormal exits;
- mandatory process ownership plus provider-specific guards/containment preserve security while
  normalizing outcomes;
- task publication, deployment activation, recovery, and cleanup remain independent lifecycles;
- no migration phase resumes an authority contract whose enforcement has been removed; and
- the final deletion phase removes temporary migration machinery and active compatibility rather
  than leaving agents to interpret both architectures.
