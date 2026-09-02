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
first draft and successive corrected candidates were rejected as not implementation-ready. The
candidate incorporates the dispositions below, including the later clarity corrections, and is
under independent re-review. The checklist at the end must be rerun; this document is neither source
acceptance nor production activation evidence.

## Blocking findings

| ID | Finding | Risk in the rejected draft | Accepted disposition |
| --- | --- | --- | --- |
| A01 | Terminal task truth missing | `done/rejected` existed only as archive movement, losing canonical actor/reason/result | Task states include `done` and `rejected`; terminal metadata is durable before archive, which is storage only. |
| A02 | Deployment bootstrap deadlock | Self-deploy could be removed before a safe command could activate the behind-main checkout | The detached latest-main activation/forward-repair runner is landed and tested against disposable installations while production remains stopped; legacy self-deploy is then deleted as source. A later operator invokes the detached runner directly, so no intermediate production install is required. |
| A03 | Unqualified merge could activate | A failed post-merge check still contributed an activatable SHA | Merge contribution and qualification are separate; all included receipts must be qualified or explicitly superseded. |
| A04 | The migration grouped unrelated authorities into one giant cutover | Commands, task schema, providers, settlement, recovery, and projections changed at once while later phases claimed to move the same writers | Process ownership and effect reconciliation land first, command families migrate one at a time over v1 state, and task v2 switches only after a mechanical drain. |
| A05 | Maintenance gate covered only worker launch | L3, HTTP, timers, issue writes, cleanup, or settlement could mutate during activation | One gate covers every mutation/model/timer entry and has a durable quiescence acknowledgement; candidate starts health-only. |
| A06 | Exact candidate conflicted with protected refs | Claiming C while remote advanced to D required a new hidden ref capability | Activation always fetches and activates latest verified remote main; the older-candidate feature is removed. |
| A07 | Activation failure could restart-loop | `Restart=on-failure` contradicted “start once then remain stopped” | Normal crash self-healing remains; planned activation temporarily suppresses loops until verified and restores policy afterward. |
| A08 | Repair depended on broken installed code | A candidate could break Makefile/imports and make the next repair command unusable | A stable bootstrap locates a detached reviewed candidate before importing candidate Altitude code and passes explicit targets. |
| A09 | Claude descendant proof was assumed | `claude --bg` returns a short launcher/job row, not an owned process tree | Phase 1A resolved no-go without spending a provider turn: Codex is the sole autonomous target, all Claude launch/resume paths are closed, and activation requires every legacy Claude unit/process empty. |
| A10 | Global fault/open incident was not atomic | Separate incident and fuse files could leave evidence without a fuse or a fuse with no meaningful evidence | Reserve evidence id; write recovery/fuse first with bounded evidence and pending append; reconcile keyed incident second. |
| A11 | Partial JSONL replay was invalid | Appending after a truncated tail concatenated two malformed records; parent rename was not fsynced | One keyed append primitive repairs/truncates the final tail, deduplicates ids, fsyncs file; atomic replace fsyncs parent. |
| A12 | Completion and deployment had duplicate authorities | Task result, completion file, report, verified state, and pending marker could disagree | One task terminal/result receipt, immutable publication receipts, and one DeploymentRecord; duplicate files are removed/derived. |
| A13 | Maintenance acknowledgement preceded drain evidence | The gate was scheduled before Claude/L1/L3 physical identity and before durable Git/GitHub effect intent, so an exclusive lock could not distinguish empty work from an orphaned effect | Phase 1 establishes physical ownership and effect reconciliation, Phase 2 records qualification, Phase 3 closes mutation commands, and Phase 4 builds the gate/runner dormant. Final source closure enables that detached runner as the first claimant. |
| A14 | Incremental self-host activation was circular | Installing Phase 1-3 through legacy self-deploy could mix old daemon code with new CLI/hooks before safe ownership and activation existed | Production is drained, stopped, unit/process-empty checked, and state-hash frozen before implementation. Every PR is source-only and tested on copied homes/disposable units. Legacy self-deploy is deleted while stopped; a later separately authorized detached activation is the first production use of the new code. |
| A15 | Final deletion could remove the only real-state importer before first activation | Disposable migration success was not evidence that the frozen production home had migrated | Real-state importers remain in source through the first successful production ActivationReceipt. Their deletion is a separately reviewed post-activation PR and cannot be part of the pre-activation reduction claim. |
| A16 | First activation had no ancestry anchor | A stopped service initialized `activated_sha=null`, yet qualification required coverage from `activated_sha`, leaving no lower bound for the first refactor range | DeploymentRecord stores a distinct one-time bootstrap anchor from the frozen loaded manifest or an exact operator-provenance receipt. Unknown or ambiguous provenance refuses; it never fabricates an ActivationReceipt. |
| A17 | Candidate preflight conflated running, installed, and detached identities | Requiring manager `ExecStart` to match candidate C before installation either blocked every activation or weakened installed-checkout validation; after the service stopped, “loaded current” could not be re-observed honestly | Manifest has strict `running_install`, `stopped_install` B, and `detached_candidate` C roles. B revalidates inactive installed bytes/configuration against the immutable prior receipt (running normally, bootstrap freeze for the first activation) without executing old code; activation records all hashes and the intended B-to-C transition. |
| A18 | Retained real-state importers lacked executable activation ownership | L3 sessions/actions, events, and transcript compatibility could lose their last reader without being cut over, and the activation stage machine could start the candidate before import | Each domain supplies a fixed typed callback; the final claimant closure proves the closed list. `state_cutovers_applied` reconciles every selected domain receipt after stop/install and before any candidate process starts. All real importers remain through the successful receipt. |
| A19 | The activation tried to observe a stopped install before stopping the old service | B could not be inactive at candidate-build time, while first activation had no live old process to inspect | Candidate preparation records a prior install only after gate acknowledgement, stops/proves the unit empty, then records inactive B before source change. The already-stopped first activation uses an exact stopped manifest bound to immutable freeze/provenance evidence and never invents a running observation. |
| A20 | An incomplete refactor could enable the production claimant | Phase 4 originally made activation callable before later task/recovery/transcript cutover callbacks existed | Phase 4 builds and tests the runner behind a dormant guard. Final PR 10A.2 removes that guard only after every fixed callback and the full disposable forward-repair drill are present. |
| A21 | First activation assumed a DeploymentRecord and gate already existed in frozen production | Source-only PRs cannot create production authority before activation | The first `claimed` transaction atomically initializes the absent record, bootstrap anchor/provenance coverage, held latch, and one operation under the deployment lock. Same-id retry reconciles; conflicting initialization refuses. |

## Important findings

| ID | Finding | Accepted disposition |
| --- | --- | --- |
| B01 | Helpers lacked crash-safe launch/bind/result stages | Worker transition supports `subject_kind=owner|helper`; settlement references child helper transitions. |
| B02 | Recovery bypass revision was not fenced | Recovery generations persist episode id and permit revision; every trusted boundary validates them. |
| B03 | Global state-format selector added needless machinery | Active v1 domains drain; fresh v2 begins once; old archives use isolated read-only decoders. No selector/dual writer. |
| B04 | Deployment was semantically an uncounted sixth state machine | Six closed domain-embedded operation kinds are counted honestly; source activation is the deployment transition's only subtype. |
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
| B19 | Clearance/hold history could add a second active ledger | Domain-embedded keyed clearance replaces the active recovery episode with one canonical inactive epoch; cleared evidence remains audit-only. |
| B20 | Audit state and event files were falsely described as one transaction | State-first transition envelopes plus before-next-mutation reconciliation make events a delayed keyed projection, with kill tests. |
| B21 | Foundation work silently became a whole-system transition rewrite | Phase 0C now supplies only lock/durable-I/O primitives and an explicit legacy-callsite inventory; each command owner adopts envelopes and deletes its old writer in its own later PR. |
| B22 | “Small PR” labels concealed cross-system changes | Process, effect, admission, and recovery phases are split by owner/ingress family, with dormant primitives first and a final static-closure gate before any production claimant or format switch. |
| B23 | Numeric budgets could be changed by reclassification | `07-baseline-and-target.md` fixes the baseline commit, exact file rosters/counting rules, future-file classification, and the normative target roster. Artifact families are counted by canonical lifecycle/authority rather than by every inert lock instance or read-only decoder. |
| B24 | Publication finalization was split across independently unsafe PRs | Attention, terminal truth, and archive movement now land together in PR 1C.5; PR 1C.3 no longer owns archive finalization. |
| B25 | PR 4B still concealed a cross-system implementation | Resolver/builder, service transaction, forward repair, and dormant integration are separately reviewed; claimant enablement is a final small PR only after every cutover callback exists. |
| B26 | The recorded runnable baseline arithmetic was not reproducible | `07-baseline-and-target.md` records exact pinned rosters: 10,695 backend, 2,683 web, 195 web-build/config, totaling 13,573 lines in 52 files. |
| B27 | Archive-decoder prohibition contradicted retained history views | The prohibition applies to active mutation, dispatch, resume, worker, and settlement paths. A versioned isolated decoder may serve retained read-only archives. |
| B28 | UI and physical asset decisions disagreed with the target roster | Inbox is removed only after attention/FYI projection parity, optimistic mutation folds into the API module, provider personas render from one L2/L3 source each, and both action schemas consolidate into one physical schema. |
| B29 | Fault and recovery PRs still crossed several authorities | Fault adoption is split into dormant registry/importer, task/provider, project/repository, and system/activation slices. Recovery commands are split into incident/fuse, wake ownership, task/permit, clearance, and activation closure PRs. |
| B30 | “Phase complete” implied deployed behavior during the source freeze | Source-phase completion now means one reviewed candidate-source path with disposable evidence; deployed cutover and importer deletion remain explicitly incomplete until the real ActivationReceipt. |
| B31 | Recovery sub-PRs could expose a mixed v1/v2 episode | PRs 8C.1-8C.4 have fixture-only callers and leave v1 untouched. PR 8D performs the sole candidate-source caller switch and deletion after the complete dormant implementation is tested. |
| B32 | Activation replay assumed the old/candidate PID existed at every stage | Restart suppression now immediately follows prior-install capture, old identity is revalidated after build, and replay checks zero PID before candidate start versus one exact health-only generation afterward. First-activation `failed_released` preserves the stopped service. |
| B33 | The source inventory silently omitted three production files | The closed roster includes both new backend fact modules and the web contract validator; a test compares the real checkout against an independent roster. |
| B34 | Recovery clearance deleted the only stale-permit fence | The target persists one inactive monotonic epoch and binds no-active permits to it. |
| B35 | Provider wording still implied Claude routing/fallback | Codex is the sole autonomous engine; Claude is read-only legacy evidence until its named post-activation deletion. |
| B36 | Target counts retained temporary legacy hooks | The permanent target is 43 runnable files/24 backend files; guard/statusline files are temporary and deleted only after the first ActivationReceipt. |
| B37 | Persona decisions named incompatible physical paths | `personas/l2.md` and `personas/l3.md` are the sole target sources; provider-suffixed legacy copies retire after prompt-parity proof. |
| B38 | L3 serialization held a lock across a provider turn | A short durable current-turn CAS chooses one message; no project lock is held during provider work. |
| B39 | Deployment wording implied an atomic filesystem/process span | State changes use persisted stages and CAS; process effects occur between reconciled stages and are never called atomic. |
| B40 | Current, dormant, planned, and activated behavior were conflated | Every active document now pins frozen production, candidate source, dormant foundations, planned cutovers, and the separately authorized activation boundary. |
| B41 | Retained modules lacked one physical authority map | The architecture now maps every retained backend, adapter, web, artifact, and persona module to one owner and forbids adapters or projections from becoming writers. |

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
- Codex-only weekly-first routing, with Claude telemetry retained only as legacy observation until its
  stopped-state deletion gate;
- Codex containment/inert broker; legacy Claude guard/backend checks only until the stopped activation
  proves every legacy unit/process empty, with no conditional Claude target;
- trusted Git/PR/check/merge enforcement at all four native hook boundaries, with one portable
  checked-in dispatcher when its installation parity test passes;
- live transcripts plus bounded durable evidence;
- one service and file-backed storage; and
- primary UI routes, simplified around canonical read models.

## Final re-review checklist

Before the architecture PR is considered settled, an independent reviewer must confirm:

- `02`, `03`, `04`, and `05` agree on six operations, terminal states, deployment qualification,
  the resolved Codex-only target and legacy-empty activation prerequisite, unknown Codex quota,
  recovery fencing, and no format selector;
- every blocker and important finding above has an exact target owner and acceptance test;
- no corrected paragraph depends on a component scheduled later in the migration;
- numeric baseline commands are reproducible; and
- the proposal contains no parallel active architecture or instruction to ignore stale behavior.

Before each implementation merge, rerun the applicable checks from `05-review-ledger.md`. The
architecture is settled only as a plan; behavior is accepted only when source, tests, runtime
evidence, and active documentation agree.
