# Adversarial review and dispositions

> This is an audit trail, not proof that later implementation matches the design. Every behavior PR
> still requires independent review against the corrected architecture and migration gates.

## Review method

Three independent reviewers traced the proposal at commit `57886cf` against current main `97e1197`:

1. core task/L3/L2/publication flows and independently mergeable sequencing;
2. deployment, recovery, incidents, process ownership, quota, and crash/rollback behavior; and
3. all 146 tracked files, API/CLI/UI/CI surfaces, state artifacts, security boundaries, and numeric
   reduction accountability.

Mechanical checks had already confirmed every tracked file appears in the component ledger and all
local Markdown links resolve. Reviewers therefore concentrated on behavioral contradictions. The
first draft was rejected as not implementation-ready. The accepted revision incorporates the
following dispositions.

## Blocking findings

| ID | Finding | Risk in the rejected draft | Accepted disposition |
| --- | --- | --- | --- |
| A01 | Terminal task truth missing | `done/rejected` existed only as archive movement, losing canonical actor/reason/result | Task states include `done` and `rejected`; terminal metadata is durable before archive, which is storage only. |
| A02 | Deployment bootstrap deadlock | Self-deploy could be removed before an installed command could activate the behind-main checkout | The full detached latest-main activation/forward-repair runner is installed and tested while legacy activation still exists. |
| A03 | Unqualified merge could activate | A failed post-merge check still contributed an activatable SHA | Merge contribution and qualification are separate; all included receipts must be qualified or explicitly superseded. |
| A04 | Phase 2 was a giant contradictory cutover | Commands, task schema, providers, settlement, recovery, and projections changed at once while later phases claimed to move the same writers | Command families migrate one at a time over v1 state; process/outcome/settlement land before a drained task-v2 switch. |
| A05 | Maintenance gate covered only worker launch | L3, HTTP, timers, issue writes, cleanup, or settlement could mutate during activation | One gate covers every mutation/model/timer entry and has a durable quiescence acknowledgement; candidate starts health-only. |
| A06 | Exact candidate conflicted with protected refs | Claiming C while remote advanced to D required a new hidden ref capability | Activation always fetches and activates latest verified remote main; the older-candidate feature is removed. |
| A07 | Activation failure could restart-loop | `Restart=on-failure` contradicted “start once then remain stopped” | Normal crash self-healing remains; planned activation temporarily suppresses loops until verified and restores policy afterward. |
| A08 | Repair depended on broken installed code | A candidate could break Makefile/imports and make the next repair command unusable | A stable bootstrap locates a detached reviewed candidate before importing candidate Altitude code and passes explicit targets. |
| A09 | Claude descendant proof was assumed | `claude --bg` returns a short launcher/job row, not an owned process tree | A foreground supervised-resume feasibility PR is go/no-go. Failure narrows autonomous Claude authority rather than claiming false proof. |
| A10 | Global fault/open incident was not atomic | Separate incident and fuse files could leave evidence without a fuse or a fuse with no meaningful evidence | Reserve evidence id; write recovery/fuse first with bounded evidence and pending append; reconcile keyed incident second. |
| A11 | Partial JSONL replay was invalid | Appending after a truncated tail concatenated two malformed records; parent rename was not fsynced | One keyed append primitive repairs/truncates the final tail, deduplicates ids, fsyncs file; atomic replace fsyncs parent. |
| A12 | Completion and deployment had duplicate authorities | Task result, completion file, report, verified state, and pending marker could disagree | One task terminal/result receipt, immutable publication receipts, and one DeploymentRecord; duplicate files are removed/derived. |

## Important findings

| ID | Finding | Accepted disposition |
| --- | --- | --- |
| B01 | Helpers lacked crash-safe launch/bind/result stages | Worker transition supports `subject_kind=owner|helper`; settlement references child helper transitions. |
| B02 | Recovery bypass revision was not fenced | Recovery generations persist episode id and permit revision; every trusted boundary validates them. |
| B03 | Global state-format selector added needless machinery | Active v1 domains drain; fresh v2 begins once; old archives use isolated read-only decoders. No selector/dual writer. |
| B04 | Deployment was semantically an uncounted sixth state machine | Six closed domain-embedded operation kinds are counted honestly; the temporary cutover-publication subtype compacts to provenance and is deleted, leaving source activation as the final deployment transition. |
| B05 | No numeric simplification target existed | Fixed production/Python/artifact/authority budgets and per-PR before/after accounting are now merge gates. |
| B06 | “Shared typed projection library” was undefined across Python/TypeScript | One versioned JSON wire contract, Python producers, explicit Zod validators, and committed cross-runtime fixtures. |
| B07 | Remote web CI might run candidate package scripts with broader authority | Node tests/build retain exact head/base, same-repo, read-only, no-secret, isolated environment controls and static tests. |
| B08 | Issue publication reconciliation was underspecified | Preserve a stable repository-bound non-secret body marker and query exact repo/marker before create retry. |
| B09 | Unknown quota policy contradicted working routing | Unknown is recorded uncertainty and remains eligible; actual launch/quota failure creates provider-local hold. |
| B10 | Waiting-for-operator could spend model quota indefinitely | Only cheap mechanical probes run until input/evidence/deployment changes episode revision; then one L3 wake occurs. |
| B11 | Incident IDs collide across projects | New immutable UUID is canonical; `(project_id, legacy_id)` is a display alias; importer preserves amendments. |
| B12 | Recovery supervisor identity was omitted | Episode stores immutable supervisor project; new evidence cannot silently switch project sessions/locks. |
| B13 | Lock hierarchy arrived after dependent writers | Phase 0 defines/tests the full activation-to-publication lock order before deployment or command writers. |
| B14 | `restart-pending.json` was incorrectly called a launch fence | It is a derived marker; DeploymentRecord replaces status and the independently proved maintenance gate owns launch quiescence. |
| B15 | Rollback wording contradicted forward-only data | After a data cutover, only state-compatible forward repair/behavior revert is valid; activation checks supported formats. |
| B16 | Ledger classifications understated rewrites/migrations | `git_policy`, `state`, restart tool, event filenames, operations, Inbox, restart marker, and ROADMAP rows are corrected. |
| B17 | Multiple publication attempts lacked fresh corrective workspace ownership | Corrective attempts create a new fenced generation, fresh branch/worktree/base registration, and immutable receipt. |
| B18 | Recovery preemption conflicted with WIP one | External settlement stabilizes and units empty before explicit `preempted_by_episode`; ordinary task later rebases through new generation. |
| B19 | Clearance/hold history could add a second active ledger | Domain-embedded keyed clearance reconciles receipt/removal; cleared holds become incident/audit amendments, not active truth. |
| B20 | Audit state and event files were falsely described as one transaction | State-first transition envelopes plus before-next-mutation reconciliation make events a delayed keyed projection, with kill tests. |

## Reductions confirmed safe to pursue

The reviewers agreed these removals preserve the desired product mechanisms when their stated
dependencies land first:

- predictive top-level path scheduling under repository WIP one;
- scheduler self-deploy and completion-time forensic cleanup;
- direct web/public CLI task creation, manual dispatch/verify, and duplicate task-message composers;
- digest Markdown/audio/TTS, edit-count telemetry, dead approval/administrative fields and routes;
- old helper PR parsing, report-promotion flags, duplicate hold mirrors, and session mirrors;
- model-authored trusted verification/publication/deployment facts; and
- active compatibility writers/readers after drained one-shot cutovers.

These keeps remain justified:

- L3 high-level coordination and direct L2 steering;
- one L2 with optional bounded managed helpers/reviewer;
- dual-provider routing with weekly-first observations;
- Codex containment/inert broker and existing Claude guard until stronger proof;
- trusted Git/PR/check/merge enforcement and hook stubs;
- live transcripts plus bounded durable evidence;
- one service and file-backed storage; and
- primary UI routes, simplified around canonical read models.

## Final re-review checklist

Before the architecture PR is considered settled, a reviewer must confirm:

- `02`, `03`, `04`, and `05` agree on six operations, terminal states, deployment qualification,
  Claude prerequisite, unknown quota, recovery fencing, and no format selector;
- every blocker and important finding above has an exact target owner and acceptance test;
- no corrected paragraph depends on a component scheduled later in the migration;
- numeric baseline commands are reproducible; and
- the proposal contains no parallel active architecture or instruction to ignore stale behavior.

Before each implementation merge, rerun the applicable checks from `05-review-ledger.md`. The
architecture is settled only as a plan; behavior is accepted only when source, tests, runtime
evidence, and active documentation agree.
