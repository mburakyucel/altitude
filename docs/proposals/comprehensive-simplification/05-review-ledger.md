# Approved decisions and review ledger

> Recorded 2026-09-02 from Burak's stated operating model and explicit instruction to remove
> ambiguity, use independently reviewed module PRs, and merge safe increments. A later change to one
> of these choices requires an explicit architecture amendment; implementations may not silently
> choose another behavior.

## Product decisions

| ID | Decision | Accepted consequence | Rejected alternative |
| --- | --- | --- | --- |
| D1 | **One active top-level L2 per repository.** Cross-project work and helpers inside that task may run concurrently. A blocked task retains the slot except for the explicit recovery-preemption protocol. | Less top-level parallelism; far less scheduling and collision machinery. | Predictive path leases and WIP greater than one. |
| D2 | **Keep live transcripts and durable lifecycle/terminal snapshots.** Provider-native evidence is retained with the archived task until an operator deletes that archive; no independent retention scheduler is added. | Storage grows with retained archives and remains private. | Per-event portable duplication or a new automated retention service. |
| D3 | **Keep platform-managed optional helpers/reviewers.** They share the owner/helper process-transition implementation, have bounded subscopes, return patches/findings, and never own publication. | A small helper record remains because it provides provider-neutral visibility and collision control. | Untracked provider-native subagents as the sole helper mechanism. |
| D4 | **Resolved no-go: Codex is the sole autonomous engine.** The Phase 1A foreground Claude proof was not completed without spending a provider turn, so no source path launches/resumes Claude and legacy guard/backend checks remain only through stopped-state reconciliation. | Claude is not conditionally eligible in the accepted target; later enablement requires a separate architecture amendment and real proof. | Treating `claude --bg`, actor environment, TTY, or a same-UID flag/token as ownership or operator authentication. |
| D5 | **Activate the latest verified `origin/main` observed at the post-gate revalidation boundary through a detached, operator-authorized runner.** Preflight happens before source swap; an external merge after that boundary remains pending for the next activation; post-swap failure leaves the maintenance gate held and service stopped. | No automatic full-code rollback and no claim that a local gate locks all GitHub writers. | Versioned-release/symlink machinery or a special external branch-lock capability. |
| D6 | **An explicit Burak request authorizes the exact bounded GitHub issue publication.** Model-originated suggestions remain local drafts. Stable non-secret effect markers provide exactly-once reconciliation. | A second approval phrase is unnecessary when the initiating request is already explicit. | Title/content matching or automatic publication of model suggestions. |
| D7 | **Remove Inbox as durable control state.** Blocking questions/findings are typed task attention; recent non-blocking FYIs are read from canonical events. | There is no separate seen/unseen acknowledgement queue. | A second completion-adjacent inbox lifecycle. |

## Operational decisions

| ID | Decision | Reason |
| --- | --- | --- |
| O1 | Unknown quota telemetry is eligible uncertainty, not exhaustion; use the configured default and create a provider hold only after an actual launch/quota failure. | Observability failure must not halt L3 recovery or all providers. |
| O2 | Codex weekly allowance is the primary capacity score; its short window is an availability gate. Claude observations are legacy telemetry, never routing eligibility. | Preserve Codex weekly quota without retaining a conditional dual-provider target. |
| O3 | Normal systemd restart-on-crash remains enabled self-healing. Planned activation temporarily suppresses restart loops until exact candidate health succeeds. | Unplanned process crashes and authorized source activation are different events. |
| O4 | One shared stage/receipt helper supports exactly six closed operation kinds embedded in their domain records: L3 turn, worker transition, settlement, issue publication, recovery transition, and deployment transition. Recovery claim/clearance are fixed subtypes; activation is the sole deployment subtype. | Count state machines honestly without creating a workflow engine or a temporary cutover publisher. |
| O5 | New active task/recovery formats cut over only after their v1 domain is empty. No global runtime format selector or dual active writer is allowed. | A drained domain needs a one-shot cutover receipt, not another state machine. |
| O6 | Python produces a versioned JSON wire contract and TypeScript validates it with explicit Zod schemas and shared fixtures. | Clear cross-language agreement without code generation or a fictional shared library. |
| O7 | The final numeric budgets in `04-migration-validation.md` are acceptance gates. | “Simpler” must be measurable and tests cannot hide production growth. |
| O8 | Physical process ownership, remote-effect reconciliation, deployment qualification, and application-command ownership land before maintenance can acknowledge. The detached activation runner is the first production gate claimant; no temporary legacy-restart adapter is added. | Exclusive drain must prove facts mechanically, and adding a second transitional restart workflow would recreate the authority ambiguity being removed. |
| O9 | Production remains stopped and its state hash-frozen throughout implementation. Source PRs use copied homes/disposable units; no implementation merge, checkout movement, or disposable success is production activation. | This removes mixed old/new self-hosting and the temporary cutover publisher. The first real activation remains separately operator-authorized and retains all real-state importers until its receipt exists. |

## Simplification decisions

| ID | Status | Final meaning |
| --- | --- | --- |
| S01 | **Accepted, amended** | Faults use closed effect/blast-radius types. Unknown mappings remain global until characterized; safe-order recovery write closes launch first. |
| S02 | **Accepted** | Task base validation is separate from installed deployment checkout synchronization. |
| S03 | **Accepted, amended** | States are `queued/running/settling/blocked/done/rejected`; owner/helper transition and settlement operations are fixed and domain-embedded. |
| S04 | **Accepted, amended** | Immutable publication receipts distinguish merge contribution from deployment qualification; one DeploymentRecord owns activation. |
| S05 | **Accepted, resolved by D4 no-go** | Outcomes normalize on the contained Codex path only; legacy Claude readers remain non-authoritative until stopped-state retirement and never become target writers. |
| S06 | **Accepted** | Scheduler self-deploy and completion-time forensic cleanup are removed after replacement activation/maintenance paths exist. |
| S07 | **Accepted** | Each fact has one canonical record; views never actuate behavior. |
| S08 | **Accepted** | Repository WIP is one; task paths are publication scope, not scheduling prediction. |
| S09 | **Accepted** | GitHub issues hydrate once before queueing and never on resume. |
| S10 | **Accepted, amended** | Action-specific untrusted contracts plus one shared outcome. Every outcome variant has one closed observations block for findings, decisions/questions, FYIs, follow-ups, deviations, usage/spend, and observed merge hold. Human reply stays in task conversation; trusted code derives outcome/effect identity and rechecks canonical hold/effects. No model schema may convey trusted verification/publication/deployment facts. |
| S11 | **Accepted** | Remove digest/TTS, edit counts, dead endpoints/fields/hooks, old helper PR parsing, and other enumerated cosmetic/compatibility paths. |
| S12 | **Accepted** | Chat is high-level L3 intake; Task is direct L2 steering. Direct web/public CLI task creation is removed. |
| S13 | **Accepted** | Live transcript plus lifecycle/terminal snapshots; no per-event portable-export workflow. |
| S14 | **Accepted, amended** | Move command families one at a time over v1 state, deleting each old writer; do not perform one giant control-plane cutover. |
| S15 | **Accepted, amended** | Drain active domains, switch once, and retain only isolated read-only archive decoders selected by D2. |

## Mandatory consistency checks

Before merging any module PR, its reviewer must answer all applicable questions with source/test
evidence:

### Authority and process ownership

- Which single record owns this fact after the PR?
- Which old writer and active fallback reader are deleted?
- Can a stale owner/helper generation call this path?
- Is every prior physical writer provably empty before replacement, settlement, rejection, or archive?
- For a recovery task, does the generation carry and validate episode/permit revision?

### External effects and crash behavior

- What stable effect id and deterministic target are written before the effect?
- How does restart query/reconcile an effect that succeeded before its local receipt?
- Does JSONL tail repair preserve a valid unterminated row and truncate an invalid partial row?
- Are atomic replace, append, and parent-directory durability claims matched by tests?

### Publication and deployment

- Are worker/model claims excluded from trusted receipt fields?
- Can failed checks, unresolved findings, or a merge hold ever become activatable?
- Is merge independent from installed source/assets/service state?
- Can the detached bootstrap forward-repair a candidate that broke installed deploy code?

### Recovery

- Is the launch fuse written before incident enrichment can fail?
- Can a scoped failure freeze unrelated work?
- Can waiting-for-operator spend another model turn without new evidence/revision?
- Can a recovery fault recurse into another incident, task, or wake?

### Interfaces and reduction

- Does every adapter call the same application command?
- Does the API payload follow the versioned Python/Zod fixture contract?
- Does remote Node execution retain the sanitized candidate boundary?
- Do the before/after line, file, artifact, writer, timer, and dependency counts meet the phase budget?
- Are the active architecture documents updated and stale instructions deleted?

## Review disposition rule

An independent reviewer classifies findings as blocker, important, or optional. Blocker and important
findings must be fixed or explicitly rejected with evidence before merge. Optional cleanup may become
a GitHub issue only when it is outside the accepted phase and does not leave two authorities or a
known safety gap. A green test suite does not overrule a valid architectural blocker.
