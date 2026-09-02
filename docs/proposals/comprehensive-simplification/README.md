# Comprehensive simplification proposal

> **Status: target candidate under independent re-review.** Previously recorded product decisions
> remain governing constraints, but this corrected document set is not settled until that review.
> Independent
> adversarial passes repeatedly rejected and then verified corrections to deployment,
> crash-consistency, process ownership, task state, migration, source freeze, and surface accounting.
> The freeze receipt pins the deployed
> behavior and its then-current [Architecture](../../ARCHITECTURE.md) plus
> [Engine and session lifecycle](../../SESSION_LIFECYCLE.md) until the separately authorized
> activation. Candidate-source versions of those documents update with each behavior PR.
> Module implementation uses the accepted dependency order and independent review; production
> activation/restart remains a separate action.

## Implementation status at this candidate

The terms below are exact. **Active candidate source** means a normal source path would use the change
if this candidate were activated. **Dormant** means code and tests exist but no normal source path may
call it. **Planned** means it is not implemented. None of these terms means deployed.

| Status | Candidate contents |
| --- | --- |
| Frozen production | Commit `97e11979bdc0814ad5067eab717f999d1c251437`; service stopped; state frozen; no `ActivationReceipt` |
| Active candidate source | Exact-candidate remote web CI and Codex-only autonomous-engine closure |
| Implemented but dormant | Durable I/O primitives; boundary contracts; physical-transition foundation; runtime manifest/preflight; DeploymentRecord baseline, contribution, and qualification facts |
| Planned | L3/L2/helper adoption after the physical foundation; settlement effects; application commands; task v2; scoped holds; recovery v2; projections/cleanup; activation runner and real-state cutovers |
| Production activation | Not performed and not authorized by any source merge or this proposal |

The [active architecture](../../ARCHITECTURE.md) identifies current candidate behavior and labels every
dormant foundation. The target architecture in this pack is a plan until its named adoption PR lands.

## Purpose

The first architecture cutover removed the mandatory proposal/critic/implementation pipeline and
established the intended L3 -> one L2 owner -> optional L1/reviewer -> PR model. Subsequent work added
provider-neutral sessions, containment, recovery, issue hydration, live transcripts, portable
transcripts, restart safety, and several fixes for races between those mechanisms. Many additions
protect real requirements. The resulting implementation nevertheless has too many coupled state
machines, duplicate facts, compatibility paths, and globally scoped reactions.

This pack proposes a holistic reduction. It does not justify a deletion merely because a file is
large or a path is inconvenient. Every proposed simplification must identify:

1. the desired product mechanism it preserves;
2. the invariant currently enforced by the code;
3. the smaller component that will own that invariant afterward;
4. its dependencies on other simplifications;
5. an observable acceptance test and rollback/stop condition; and
6. the compatibility data that must be migrated before old code is removed.

## Proposal pack

- [01 - Current system](01-current-system.md): current end-to-end flows, state and artifact map,
  ownership boundaries, and observed complexity. This is descriptive, not aspirational.
- [02 - Proposed architecture](02-proposed-architecture.md): target responsibilities, canonical
  records, state machines, recovery policy, provider boundary, publication, deployment, UI, and
  control-plane design.
- [03 - Component decisions](03-component-decisions.md): exhaustive keep/simplify/consolidate/remove/
  defer ledger for production modules, hooks, service/CLI/CI, schemas, personas, templates, web
  surfaces, runtime artifacts, docs/design, and test families.
- [04 - Migration and validation](04-migration-validation.md): phase ordering, migration gates,
  invariants, verification, real end-to-end tests, stop conditions, and rollback.
- [05 - Review ledger](05-review-ledger.md): recorded product/operational choices, accepted
  simplifications, and mandatory questions every implementation reviewer must answer.
- [06 - Adversarial review](06-adversarial-review.md): independent challenges to the first draft,
  their concrete dispositions, and the claims that still need reviewer verification.
- [07 - Baseline and target](07-baseline-and-target.md): exact line/file counting contract,
  baseline rosters, future-file classification, and the normative permanent target roster.

## Proposed architecture in one view

```text
Burak
  |-- project direction / roadmap <-----------------------> L3 coordinator
  |                                                           |
  |                                                           `-- create one task when execution is needed
  `-- task-specific steering <----------------------------> Task conversation
                                                               |
                                                     one logical L2 owner
                                                               |
                                  +----------------------------+---------------------------+
                                  |                            |                           |
                             direct work               optional bounded help       normalized outcome
                                                                                         |
                                                                                trusted settlement
                                                                                         |
                                                                        PR / checks / merge receipt
                                                                                         |
                                                                                archive + concise FYI

Fault evidence -> deterministic classification
  |-- advisory --------------------> record / display
  |-- task/project/provider scoped -> hold/retry only that scope
  `-- uncertain shared safety -----> global fuse -> L3 operational recovery
                                                   `-- at most one recovery L2 when code is needed

Merged source -> deployment pending -> separately authorized operator deployment/restart
Archived task -> conservative, non-blocking maintenance/pruning
```

## What remains non-negotiable

- L3 remains Burak's high-level project contact and uses model judgment rather than an intent
  classifier or mandatory planning pipeline.
- One L2 owns each task end to end. Burak talks to that L2 directly for task-specific steering.
- L2 may implement directly or use zero, one, or several optional bounded helpers/reviewers.
- Work is isolated by task worktree and branch. Every code change reaches `main` through a PR.
- The current logical task owner is fenced from stale workers and messages.
- Codex containment and trusted brokerage remain fail-closed. The Phase 1A foreground proof was not
  completed without spending a provider turn, so Codex is the sole autonomous target and no source
  path launches or resumes Claude; legacy Claude protections remain only for stopped-state evidence
  and cleanup.
- Publication verifies scope, provenance, the exact PR head/base pair, applicable checks, merge
  holds, and the merge result.
- Codex is the sole autonomous engine. There is no conditional Claude routing or fake cross-provider
  resume in the accepted target; enabling Claude later requires a separate architecture amendment and
  real foreground supervision proof.
- Before any source containing this decision runs in production, the stopped-production activation
  gate must prove every legacy Claude unit/process empty. Same-UID actor variables, TTYs, and CLI flags
  are not operator authentication.
- Incident evidence remains private and durable. A genuinely unsafe shared condition stops launches.
- L3 retains operational recovery autonomy and may delegate the active episode's single recovery
  L2. Incidents never recursively generate work.
- Planned/source-changing activation and restart remain separately authorized. Normal supervisor
  restart after an unplanned process crash is the explicitly selected self-healing exception.
- Live L2 transcript visibility remains opt-in and never claims hidden reasoning.

## Main reduction themes

| ID | Reduction | Preserved invariant |
| --- | --- | --- |
| S01 | Classify faults by blast radius instead of globally fusing every fault | Unsafe shared ownership/containment still stops every launch |
| S02 | Decouple task provenance from deployment-checkout synchronization | Every task still starts from a freshly fetched immutable remote base |
| S03 | Replace report/verified/reported/closeout stamps with one explicit settlement state | Worker exit and publication remain crash recoverable and idempotent |
| S04 | Create one trusted publication receipt and one check classifier | PR head/base, checks, holds, merge, and deployment facts remain mechanically verified |
| S05 | Normalize provider terminal outcomes behind one internal contract | Codex stays brokered; provider identity and real session continuity remain visible |
| S06 | Remove self-deploy and forensic cleanup from the task scheduler | Deployment remains explicit; unsafe cleanup refuses deletion without freezing work |
| S07 | Give every durable fact one canonical record and derive UI/prompt views | Audit history remains append-only; no view becomes a competing source of truth |
| S08 | Default to one L2 per repository and make paths publication scope, not predictive scheduling | Worktrees still isolate tasks and publication still enforces declared scope |
| S09 | Hydrate GitHub issue input once, before dispatch | External task content is trusted, immutable, repository-bound, and available to L2 |
| S10 | Use compact action-specific schemas and shared role instructions | Untrusted actions remain strict and provider boundaries remain explicit |
| S11 | Remove dead/cosmetic hooks, TTS, endpoints, fields, and compatibility code | Quota and safety telemetry remain; observability failure cannot halt execution |
| S12 | Make Chat the only high-level intake and Task the only task conversation | L3 coordination and direct L2 steering no longer have bypassing UI paths |
| S13 | Keep live transcripts; snapshot portable evidence at defined boundaries | Interruptions retain evidence without rebuilding every transcript after every event |
| S14 | Move CLI, HTTP, and model brokers onto one application-command layer | All mutations use identical validation and cross-process serialization |
| S15 | Migrate compatibility data once and delete fallback readers | Active context describes one architecture rather than old and new simultaneously |

## Reduction accounting

“Simpler” is an acceptance claim, not a reason by itself. Each implementation phase must attach a
before/after inventory covering production files and lines, durable record/artifact families, schema
variants, independent state writers, timer paths, user-visible mutation paths, compatibility
branches, and external dependencies. Tests and migration evidence are reported separately so adding
needed verification cannot disguise production growth.

Temporary read-only compatibility code is allowed only with an owner, counter, and deletion gate in
[04 - Migration and validation](04-migration-validation.md). A phase that introduces its replacement
but does not retire the superseded writer at the stated gate is incomplete. The final target must:

- retain one deployed service process and the existing file-backed storage model;
- have one application-command authority for durable mutations and no direct HTTP/CLI/model writes;
- have one canonical task record with embedded owner/settlement operation, current owner generation,
  publication receipt, global recovery episode, and incident ledger for their respective facts;
- have no scheduler-owned deployment, recursive recovery task, predictive top-level path scheduler,
  cosmetic fault actuator, or compatibility writer;
- have zero active uses of every fallback reader selected for deletion; and
- meet the numeric production, Python, artifact, authority, and operation budgets in the migration
  plan. Any exception must name the preserved invariant and receive explicit review.

## Implementation and review process

1. Implement the dependency-ordered module PRs in [04](04-migration-validation.md).
2. Update the active architecture in every behavior PR and delete superseded instructions.
3. Require an independent code/architecture review and green applicable checks before each merge.
4. Delete the replaced writer/reader in the same PR or its named immediately paired deletion PR.
5. Measure every phase against the fixed baseline and final budgets.
6. After the final phase, retain one concise active architecture and keep this pack only as
   non-normative migration evidence, or remove it if it causes context ambiguity.

## Scope and non-goals

This migration does not redesign the visual UI, discard preserved wireframes, weaken
Git/PR/containment controls, automatically drain unrelated GitHub backlog, or authorize production
activation/restart. It is also not a promise to split every large Python file: responsibilities are
reduced first; file boundaries follow the resulting ownership model.
