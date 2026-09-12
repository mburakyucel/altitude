# Altitude architecture

Altitude keeps a persistent project-level conversation with L3, the project's orchestrator. L3
discusses direction, architecture and priorities, dispatches directly reachable L2 task owners,
and receives their questions and reports needing follow-up. Several tasks can proceed in isolated
worktrees while that conversation continues. Model judgment favors small, safely mergeable increments
for large or complex issues when practical; code enforces task ownership, isolation, launch holds, and
the PR boundary. L3 maintains the issue breakdown from L2 replies and reports. Briefs name the increment,
acceptance, parent issue and remaining scope; completing a task need not complete that issue. See
[incremental delivery](CLI.md#incremental-issue-delivery).

```text
Burak
  ├─ project direction and roadmap ───────────────► L3
  │                                                  │
  │                                                  └─ dispatches L2 task owners
  └─ task questions and steering ◄───────────────► L2 owner
                                                     ├─ may implement directly
                                                     └─ may delegate to its engine's own subagents

L2 worktree/branch ─► checks/review ─► PR ─► merge ─► more authorized work or final report/archive
system fault ─► blocked task + private incident ─► one queued L3 message
L2 block ─► one queued L3 message ─► L3 answers (task resumes) or escalates (a Needs you card for the operator)
```

## Responsibilities

L3 maintains the project-level conversation, sees the active task set, and decides whether a
request can be answered directly or needs an L2. It does not run a mandatory planning pipeline and
cannot launch subagents directly. Its routine inspection goes through compact `alt` verbs for task
reports, recent messages and events, queue waits, repository/service state, PRs, and its own recent
shell commands. L3 turns persist bounded shell command text with their tool evidence, so repeated
ad-hoc commands are visible and can become stable verbs. Its process is read-only on the deployment
checkout on either engine; source changes always belong to one L2 worktree and PR.

The [L3 persona](../personas/l3.md) owns compiled handoffs and durable feedback handling. Material
project direction/decision changes and L2 findings/delivery prompt L3 to assess affected active owners
and send targeted updates through task messages. Each update cites sources, what changed and what it
supersedes, impact on the owner's work and uncertainty; proposals remain distinct from verified delivery
and operator authority from recommendations. Unchanged context does not trigger repeated nudges.
L3 executes supported administrative coordination and already-authorized continuation handoffs
through existing task operations, preserving remaining scope, acceptance, original evidence,
dependencies, holds and sessions for work that stays with its owner. New scope/provider decisions
or operator judgments still escalate; routine administration does not become an operator question.
The [L2 persona](../personas/l2.md) requires owners to reassess plans and open question members, record
sourced supersession through the existing resolver and continue within authorized scope. Still-relevant
operator decisions, proposal checkpoints, merge holds and fault-recovery verification retain their rules.
Operator feedback about system or role behavior follows the existing task/PR path to the narrowest
authoritative instruction source for future sessions unless scoped to a session or task; tentative
suggestions and one-off exceptions stay scoped. L3 distinguishes queued work, merged rules
and their effective loading. These are role instructions, with no automatic broadcast, runtime classifier
or memory store and no expansion of project-local or upstream reporting authority.

L2 receives the request, repository context, expected files, worktree, branch, and merge policy, and chooses
the lightest useful execution shape. Its conversation with Burak is stored apart from tool logs, so
Burak messages it directly without routing through L3. Messages queue on the task and reach the
worker at its next checkpoint; an explicit Stop ends a worker. Appending a message to a blocked
task also persists a due `resume_after` request, except non-waking coordinator discussion on a
faulted task and messages held by Stop. Stop records its identity when accepted, before termination,
so old or racing sends cannot restart the session. A correction must name the confirmed Stop it
observed; explicit Continue releases the same held inbox. An L3 CLI process stops there: altd coalesces that
request with timer and capacity-available wakes, then owns Git provenance validation and provider relaunch. A
durable resume claim fences competing wakes, holds service restart, and records the exact inbox batch and
replacement worker so a restarted daemon adopts rather than launches it again.
A claimed message is still queued. Successful input handoff and matching session initialization
record delivery for the exact bound batch; native hook attachments can independently prove delivery.
Inbox absence alone stays unconfirmed. Reading an inbox concurrently consumed by a resume sees an
empty queue, without turning that absence into delivery proof. A clean worker turn with queued steering resumes the saved
session, while engine failures and explicit question blocks retain their existing recovery paths.
No-code completion checks for accepted steering under the same task lock as archival. A pending
message keeps the saved owner session available for continuation instead of finalizing its earlier result.
An explicit question block needs a later message or resume request; pre-block inbox messages stay
available but cannot wake it. Each block or escalation supersedes earlier wake requests and stamps
the block identity checked by resume claims. A stale launch cannot clear a newer block: dispatch
binds only a queued task, and a superseded resume stops its unowned replacement and restores its
message batch while retaining the question.
Late worker faults retain incident evidence but cannot retag a newer question, accepted message wake,
explicit Stop or replacement worker. The fault handler checks the observed block, wake and worker
identities under the task lock. Resume
receipts consume only their own block and request, leaving a newer answer due.
Explicit `alt task resume`, `stop`, and `reject` calls also stop in the CLI after persisting one
`daemon-request` event with the task, actor, operation, and required reason. Altd checks the recorded
state and worker/session identity, refuses a stale target, and treats a retry of the same completed
request as idempotent while the terminal receipt still matches; an intervening lifecycle gets a new
identity-fenced request before altd relaunches, stops, or removes a worker.
`alt task handoff <slug> --engine <engine> --attempt <N> --reason "…"` uses this same coordinator
transport and daemon request for an exited owner blocked by worker death or a recognized usage
limit. It fences the observed attempt, worker/session and block, refuses live workers, active claims
and explicit pins, and requeues the same task. Its `next_engine` confines the next launch to that
engine's configured options and is consumed when dispatch binds the fresh attempt. Pins and
availability are checked again before execution and launch. `route.pick_task` supplies the same
target-aware availability and pin explanation to dispatch and the queue. A fresh attempt clears current
verification while retaining prior reports and delivery history. Ordinary resume remains unchanged.
The attempt number fences every L2 command to the current attempt: an L2 may reply, block, complete,
resolve a dilemma against its source message, and land only its own task.

Helpers are engine-native. The L2 may delegate bounded slices to its engine's own subagents
(Claude Code's Agent tool, Codex's equivalent); Altitude does not supervise them, and ownership never
transfers. Helper customization lives in engine-native files (agent definitions, skills, hooks). The
L2 persona carries brief delegation and context-hygiene guidance and asks for a small `progress.md`
(goal, done, next, how to verify) refreshed at milestones, never kept as a log.

L2 and L3 can run on Claude Code or Codex. Fresh L2 dispatch records one provider choice and keeps
that provider for the attempt. L3 keeps a separate resumable conversation on each provider. See
[Session lifecycle](SESSION_LIFECYCLE.md) for identity, routing, messages, containment, and context.

A fresh L3 session receives the latest 20 prior human conversation rows from its own project's
`chat.jsonl`, in append order, regardless of provider or the replaced session's `last_turn`.
Human rows have role `user` or `assistant` and trigger `chat` (an absent or empty trigger also means
chat). Server-triggered turns use those roles too, so trigger filtering precedes the 20-message
limit and reads past system traffic. The current `turn_id` is excluded. This historical context
contains only message text, limited to 800 characters per row with an explicit `[truncated]`
marker; stored tool evidence is not replayed. `STATE.md` supplies task state separately.
Resumed sessions rely on native history and receive only the cross-provider handoff:
up to 20 user/assistant rows from the latest 60 log rows, handled by a different provider after
that session's `last_turn`. Fresh context and resumed handoff are mutually exclusive.

`alt l3 search` provides on-demand historical evidence through the same project-bound coordinator
transport and CLI role gate. `l3.search` scans existing human `chat.jsonl` rows, the shared
`tasks.task_messages` projection (including status-backed decision messages), and active/archived
`report.json` string values and `digest.md`. It uses literal case-insensitive matching, retains the
newest bounded matches, and returns original text with immediate neighbors, dates, roles/recorded
authors and logical source references that survive task archival. Report/digest authors are unknown
where unrecorded; their dates explicitly mean file modification, never decision time. Resolved
evidence paths stay within the selected project. No engine transcripts, generated summaries, index,
external service, model call or state mutation participate.

Search scans the full local corpus rather than a recent-message slice; its cost grows with that
corpus and the existing broker timeout applies. Results default to five, cap at twenty, retain at
most 1,200 characters per excerpt, and fit within 64 KiB of serialized output. Count/output omission
and character offsets disclose incomplete context; corrupt/unreadable evidence fails explicitly.
Empty/missing evidence produces `no_results`. Every L3 turn advertises lookup, including native
resumes and engine changes. Historical evidence is not new authority: current instructions and task
records govern, and the coordinator checks temporary conditions and later corrections before action.
See [source references and bounds](CLI.md#historical-evidence-search).

Tasks and L3 session records carry `engine_model` and `engine_reasoning_effort`. The engine adapter
reads Codex's rollout `turn_context` after the thread starts, recording the actual model and effort
for the current turn. altd reads the provider home; the sandboxed worker does not. Missing or delayed
rollouts leave the observation unknown and are retried while the turn runs. The launch model pin is
kept separately, so recording a default does not turn it into an override on resume. Status, task
header chips, and the Monitor API expose the observation; old records remain readable.
`GET /api/task/<project>/<slug>` returns `engine_model` and `engine_reasoning_effort`;
`GET /api/monitor` session rows expose `model` beside `engine`, with `engine_reasoning_effort`
when available. An unknown Monitor model is an absent key rather than null.

Task creation records nullable `effort` (`high` or `xhigh` when explicit). `config.task_effort`
owns support and the High default for the supporting engine; `route.pick_task` filters Auto options
for explicit choices while keeping engine/model pins strict. Fresh dispatch resolves `launch_effort`
and persists it alongside `launch_model` before calling the shared worker launcher. The native CLI
receives that override on launch and every resume; the worker record retains the same launch evidence.
Tasks without the creation field retain native configuration, and resume never resolves a new default
or copies an observation into the launch choice. `alt task status` exposes `effort`, `launch_effort`
and `engine_reasoning_effort` separately. Resume clears the previous turn's observed effort until
the current worker reports it. Model compatibility stays with the native provider: unsupported
effort errors follow the existing task-local failure path, without downgrading effort or rerouting
that error. No model capability catalog, session migration or L3 effort control exists.

Both roles read two layers of rules. The personas in `personas/` are the global layer: how anyone
works under Altitude on any project, carrying nothing project-specific. The repository's own
instructions file is the project layer, owned by that repository's operator and read first.
Altitude's [AGENTS.md](../AGENTS.md) is authoritative; root `CLAUDE.md` contains only `@AGENTS.md`,
the native import that shares the same rules. The shared engine boundary selects root `AGENTS.md`
when present, otherwise `CLAUDE.md`, and names its absolute path on every L2 launch/resume and L3
turn. The instruction directs each role to follow references/imports and applicable directory rules.
L2 resolves against its task worktree; L3 resolves against the registered repository while its cwd
remains a disposable scratch directory. Discovery is repeated each turn, including native resumes.
Repositories with only the legacy file are read as they stand; Altitude neither rewrites their files
nor injects its own project policy. Brief boundary excerpts use that same rule-file selection.

Everything that encodes the operator, their providers, or their hardware sits behind a named seam.
The operator seam is one configured name and role, so personas, docs, and UI text say "the operator"
or read the configured name. The intended engine seam is `engines.py`, `route.py`, and `config.py`:
new engine-specific behavior belongs there; existing references outside it migrate when touched.
The capability seam is the local services — the speech socket,
`ffmpeg`, a GPU — each optional, detected, and degrading to an explicit unavailable state.
`tests/test_project_layers.py` holds the per-file counts of provider and operator names outside the
seams as a ratchet that can only fall.

## Engine integration boundary

Altitude coordinates ongoing CLI agent sessions. Codex and Claude Code are the current
integrations; the project workflow centers on the coordinator, task owner, isolated worktree and
checked PR, independently of which integration executes a turn. Both roles use the project's Auto
preferences or explicit pins and can operate with one installed engine and one available model.
The [setup guide](SETUP.md) describes the current manual configuration.

| Module | Integration responsibility |
| --- | --- |
| [`engines.py`](../altitude/engines.py) | Launch/resume/stop workers; engine arguments, environment and permissions; session identity, output, model/context observations and usage-limit signals. |
| [`route.py`](../altitude/route.py) | Select an engine/model from explicit pins or ordered Auto tiers, installation, rejection and quota evidence; expose the same routing reasons to callers and Monitor. |
| [`config.py`](../altitude/config.py) | Engine names, executable paths, defaults, preference syntax and context settings, alongside runtime configuration. |

The interface is internal and evolves with the integrations. Additional CLI engines such as
OpenCode are candidates for future support. Provider access is a separate integration concern:
subscriptions, direct API billing and services such as Bedrock are intended to fit the same
project workflow. Bedrock is an access service, not a CLI engine; neither OpenCode nor a Bedrock
setup is supported today. A new engine may
have different session, authentication, capability and usage-reporting models. Adapt the boundary
to preserve its native behavior rather than treating today's two launchers as a universal contract.

Auto uses the highest-priority configured tier with an eligible option. The default ties Codex's
default model with Claude Fable, followed by Opus; `ALTITUDE_PRIMARY_ENGINE` chooses only the default
tie order. Named, comparable seven-day account readings select within a tie. Unknown readings use
configured order, and L3 retains its current engine/model within that tier unless a competitor has
at least fifteen percentage points more weekly headroom. A higher available tier takes precedence.
Missing executables, exhausted windows and explicit provider rejections exclude the affected option;
unknown access remains eligible. Authentication rejections exclude the engine and model rejections
exclude only that model for thirty minutes. The router never derives model allowances from an
account-wide meter or subscription entitlement from a plan name. Safe pre-output rejection retries
are bounded to configured alternatives. Explicit pins never fall back, and an L2 resume retains its
attempt's engine, session and launch model. See [routing lifecycle](SESSION_LIFECYCLE.md#fresh-dispatch).

Current gaps are concrete: engine-specific references remain in session, dispatch, transcript and
telemetry code outside the seam. The launch environment filters
some engine variables and applies role/model/permission settings, so native access configurations
are not all passed through unchanged. The Codex coordinator uses `--ignore-user-config`, retaining
authentication while omitting user model/provider configuration; a project `l3_codex_model` can
supply a model override. Adding an engine still needs implementation and end-to-end
verification; it is not a registration-only plug-in operation. The [roadmap](ROADMAP.md#early-user-onboarding-and-public-release)
records these follow-up candidates without expanding this documentation milestone into a rewrite.

Engines retain responsibility for their execution tools, context management and native helpers.
Altitude supplies focused [role instructions](../personas/), repository context and delivery
boundaries. Execution strategy stays adaptable because a fixed sequence of stages and specialist
roles can outlive the model/tool assumptions behind it. Customization belongs in repository
instructions and the engines' native skills, hooks and agent facilities where appropriate.

## Task lifecycle

```text
queued   -> running | rejected
running  -> reported | blocked | rejected | done
blocked  -> running | reported | rejected
reported -> done | running | blocked | rejected
```

A no-code research or proposal task can go directly from `running` to `done/archive`; a git check
refuses that shortcut when the task branch changed. Code work uses the verified report path.

Queued tasks wait for WIP and engine availability gates. The default caps are 8 running tasks per
project and 80 across the machine. Overlapping declared paths are information in task status and
briefs; they do not hold dispatch or resume. One provider's quota does not
globally freeze the other. Blocked is a persisted wait/intervention state: an L2 question, a timed
operational hold, a worker failure, a verifier fault, or a report gap. An L2's question goes to L3
first, which answers from the record or escalates a dilemma to the operator. The durable dilemma
stays in Needs you while its answer is still needed, independently of the worker running or waiting. After a restart L3 receives the active tasks and resumes
faulted tasks only after verifying that their actual cause is gone. Deferral is not an active
state: durable future work belongs in a GitHub issue, and the task exits the active set.

Project registration stores `wip` only when explicitly supplied; the gate reads that override or
`config.WIP_PER_PROJECT` (8). The aggregate gate reads `config.machine_wip()`, the persistent machine
override or `config.WIP_PER_MACHINE` (80). Both dispatch and resume use these limits; status includes
the effective aggregate cap and each project's cap. On the first registry load, a one-time migration removes stored caps equal
to the legacy default of 3 and logs the affected projects, preserving approval and engine pins.
`alt project set <name> --wip N --reason "…"` and `--unset-wip --reason "…"` are available to
the operator and that project's L3; add and remove remain operator-only. Project caps are positive
integers up to the effective configured machine cap at registration or request time.
`alt machine set --wip N --reason "…"` and `--unset-wip --reason "…"` use the same settings request
and receipt implementation, with operator-only authority and a positive integer machine cap;
80 is a default, not a fixed ceiling. The machine override lives in `$ALTITUDE_HOME/settings.json`;
its `wip-request.json` and `events.jsonl` live alongside it. The machine event kind is `machine-set`.
Altd drains machine requests before project ticks, including when no projects are registered.
Altd applies each project's durable `wip-request.json` before task dispatch on its next tick,
regardless of task capacity, and records one `project-set` event with project, actor, reason, request
id and outcome in the project's `events.jsonl`. Identical pending requests and completed retries
whose WIP receipt still matches reuse the request and event. CLI and HTTP registration, removal,
engine pins and operational settings serialize registry writes under the project and registry locks.
Re-registering a project is the operator's deliberate act, and the last registry write wins.

Settings take effect without a PR, service restart or free WIP slot. Lowering a cap never terminates
workers or rewrites explicit project overrides, including overrides above a subsequently lowered
machine cap. Both gates must have capacity for a new launch; queued tasks and due resumes wait
until running counts fall below both caps. Reset removes only the selected override and restores
its default. `alt machine show` inspects active caps, defaults, overrides and request receipts;
see [inspect/set/reset examples](CLI.md#concurrency-limits).

Auto preference tiers use the same reason-bearing operational path:
`alt project set <name> --routing 'codex,claude:fable>claude:opus' --reason "…"`, or
`--unset-routing --reason "…"` to restore defaults. The operator and that project's L3 can change
them without a PR, restart or free task slot. Altd applies the request on its next tick and records
actor, reason and outcome. The project setting serves both L3 and fresh L2 routing; it changes no
explicit pin or existing L2 attempt. [CLI examples](CLI.md#automatic-routing-preferences) cover
single-model accounts and different orders and ties.

Project removal is L3 detachment: one operator action through `config.remove_project`, shared by
HTTP and CLI. It unregisters an idle project and ends its coordination. Queued, running, blocked
and reported tasks must finish or be rejected first, including blocked tasks with stopped workers.
Archived records are also inspected for live workers, dispatch/resume claims and unfinished task
operations. A project activity file lock fences removal against L3 turns, broker calls, report
handling and timer processing across processes. Task creation and operator queue admission
recheck registration under the project state lock, so removal cannot abandon racing work.

Removed projects leave the managed list; their broker is closed (CLI removal is noticed by the
next tick, with broker calls refused immediately). Repository files, remaining worktrees and the
project's Altitude directory stay on disk. Registration with the same project name and repository
attaches L3 again and restores saved conversations, task archives, provider sessions and queued
messages. The startup path drains the saved FIFO before any introductory turn. Registration
reports restored conversation history so First run waits for successful registration and opens
that history without interpreting its old replies or errors as a fresh startup result. Reset
remains a separate session rotation within a managed project.

`STATE.md` is regenerated from active task records and a bounded incident-reporting summary relevant
to the next L3 turn. The summary counts missing, failed, uncertain and confirmed fault-kind reports,
and shows up to five outcomes with gaps first. Archived tasks and full incident history remain audit
evidence available through inspection commands.

## Isolation and landing

Each task uses the isolated worktree path `.claude/worktrees/<slug>` and branch `worktree-<slug>`,
based on the exact fetched `origin/main`. Task commits require the task provenance trailer. Protected
branches cannot be updated outside the guarded landing path. The trusted landing code validates the
task ownership and repository, fetches the base, commits the selected index, pushes, opens the PR,
pins the current base/head pair, waits for configured checks, and merges only
when requested and allowed. A task may carry an explicit merge hold for Burak review.
The temporary local-check repository in `config.py` selects this project's operator-authorized
exception. Its landing runs `make check` on the existing synthetic merge candidate, with frozen
web dependencies, even when opening a PR without merging. Historical hosted results do not supply
its verdict. Active required hosted checks must be removed by the operator before this route can
run; identity, provenance, review and hold checks remain enforced. The task's `local-checks/<candidate>/`
retains logs, browser artifacts and a result binding command, base, head, candidate and tree.
Suite timeouts retain captured stdout and stderr with the timeout diagnostic and a failed result. A
successful current run writes a concise PR test line through the ordinary landing boundary.
See [policy, bootstrap and restoration](DEVELOPMENT.md#ci-and-candidate-identity).
Planned file lists guide coordination without limiting edits or landing. The owner stages selected
files or hunks and reviews `git diff --cached`; `alt land` commits exactly that index, preserving
unstaged and untracked work. Parallel tasks may edit shared paths; their briefs
name those paths and ask owners to rebase onto main before landing and keep shared-doc edits to
their own sections. If main moves, the owner runs `git rebase origin/main` in the task worktree;
an unresolved conflict is an ordinary `alt task block` to L3, never a system fault. Landing does
not resolve conflicts automatically.

A merge completes a delivery, while an active task can continue authorized work in the same
worktree, local branch and provider conversation. On the next `alt land`, the merged PR's final
head separates follow-up commits from already delivered history. Landing verifies the merge is
on fetched main and rebases only that follow-up, so squash commits are not replayed. An already
reconciled retry uses its common main ancestor. Selected staged changes are committed before
reconciliation. Dirty working files can prevent Git's rebase; landing does not auto-stash them.
Conflicts and raised rebase errors abort back to the task branch with committed and working edits
retained. Follow-up merge commits require owner reconciliation before landing, preserving edits
made in merge resolutions. Work already present on main produces a truthful merged retry.

The task's `delivery` records the current PR number/head, base SHA, publication branch and precise
timestamp; a publication in progress has no number/head. `prs` retains every delivery number, and
`delivery` events retain publication receipts and the preceding PR/head/merge evidence. PR creation
is recorded before check polling. Each new PR runs the existing candidate checks and review/hold
gate; a PR-specific hold release restores its hold for the next PR, while a later explicit
task-wide release stays effective. Adopted PR receipts remain in history when ordinary task work
continues after their merge. No-work retries preserve the current receipt and publish nothing.

Starting another delivery or claiming a resume invalidates previous completion verification.
Reported owners with recorded open-PR evidence expose the ordinary task composer and Resume action.
Message or coordinator-resume admission confirms that a recorded PR is still open through the existing
GitHub adapter, preserves the report and verification in a `report-superseded` event, and moves the
same owner into the existing blocked/resume path. No new attempt, provider, worktree or PR is
created, and merge holds and original decision evidence remain intact. A failed PR lookup refuses
the action before saving its text; after acceptance, wake failures retain the saved inbox request.

`report_after` marks follow-up work, and verification carries its owner identity, worker start and
block identity. Messages arriving at report handoff invalidate completion evidence; pending inbox
messages return to the ordinary resume path. Report application, verifier faults, stranded-report
recovery, automatic completion and report-turn receipts reject superseded work. The existing report
file remains readable until replaced, but cannot verify a later continuation. Done/archived/rejected
tasks remain outside continuation: archived worktrees may have been removed, and restoring their
execution context and ownership requires a separate product decision.
Report freshness includes the current delivery timestamp. Verification requires all recorded PRs,
the current published head on GitHub and in the clean worktree, and matching reported merge SHAs;
a clean worktree reconciled onto main after its merge also has no unpublished work. Pending work,
older reports and a raced delivery cannot complete the task. Recorded deliveries use the report
path even when the local branch has no remaining diff. Merges continue activating independently
of owner completion through the existing deployment observation.

L3 or the operator can update expected files with `alt task paths`; it replaces the `paths` list
and records the actor and previous list. This is coordination metadata, not a permission grant or
resume action. Owners need no file-list update to finish the authorized objective, including recovery;
the objective and explicit exclusions remain binding.

Issue intake fetches a single explicit project-local issue once and retains its URL and acceptance
text in `request.md`; it does not infer closure authority or scan backlog. The owner compares the full
issue scope and required acceptance with cumulative authorized deliveries. For a complete resolution it
supplies native closing keywords through `--pr-body-file` and repeats `--closes-issue N` on landing and
resumed merge calls.
The PR body is the durable closing link. Landing reads GitHub's `closingIssuesReferences` after PR
creation or edit and immediately before merge, refusing a missing or foreign-repository declared
link or a target other than GitHub's actual default branch. A merged retry validates declared links
too and routes missing historical linkage to L3. GitHub owns closure on default-branch merge.
No task issue registry or closure poller exists.
Reports verify delivery and retain closure evidence in FYIs, or route reconciliation to L3 through
follow-ups. Task archival alone does not close issues. Partial scope, design-only work, pending
operator acceptance and unrelated mentions do not warrant closing keywords or `--closes-issue`;
L3 records remaining scope on the open issue. Holds still gate merge.

For assigned existing external PRs, `alt land --adopt-pr N --expected-head SHA --reason "…"`
records the active immutable receipt in `adopted_pr` and a `pr-adopted` event under the project lock.
The current owner or operator can adopt; another active task cannot own that PR or branch.
Adoption requires the registered isolated worktree, a same-repository open PR targeting main,
and agreement between its observed head and origin. GitHub operations select the origin repository
explicitly. The owner reviews the original commits and complete PR diff against the assignment.
The local branch must contain the remote head.
Dry-run checks this evidence without recording adoption, committing or publishing.

An explicitly authorized sequence uses the same command with each next PR's observed head and
assignment reason. Before switching, landing verifies the previous PR's merge and original/latest
head ancestry on current main. It retains the previous receipt unchanged in `adoption_history`,
records the verified previous merge in the new receipt, and atomically selects the next active PR.
Retries preserve receipts; incomplete deliveries, reactivation of earlier receipts and concurrent
target changes refuse. Landing, resume provenance and recorded hold approval use the active receipt.
The history grants no additional provenance exceptions or authority over unrelated PRs.
If recorded approval released the previous PR's hold, the next adoption restores it with a fresh
hold generation. A later explicit task-wide release remains effective.

The receipt binds PR number/URL, origin, base, original branch/head, actor, attempt, reason and time.
Only unowned ancestors of that original head are exempt from task trailers; foreign task trailers
and later unowned commits refuse landing and resume. The original head must remain an ancestor.
The task keeps its local branch and publishes a fast-forward refspec to the original PR branch;
adopted pushes never retry with force. To incorporate main, the owner makes a merge commit with the
task trailer, preserving the adopted history. Adoption cannot be widened to a later external head.
The existing PR is reused, outstanding required reviews or requested changes and drafts block merge,
and the live task owner/hold/active receipt are rechecked before merging. The fetched base branch
is authoritative; lagging `baseRefOid` metadata does not replace it. Check evidence confirms the
current GitHub base target/head and exact candidate association, with real movement refusing merge.
Required checks from branch protection and active rules remain mandatory, including missing or
skipped checks. A nonrequired skipped job is exempt only when its immutable executed workflow and
PR event prove the supported main-push-only condition false, or prove
`github.event_name != 'pull_request'` false for a `pull_request` run. The inequality does not
exempt `pull_request_target` jobs; ambiguous conditions, source or association refuse.
At least one applicable check must actually pass under the hosted gate. Without CI, or under
this repository's temporary local policy, the full local suite runs on a clean merge candidate:
one parent for squash delivery, two for adopted history. Adopted PRs use a
GitHub merge commit and request no branch deletion. See the [supported workflow](CLI.md#adopt-an-existing-pr).

Operator authority travels through recorded task replies, UI decisions and original project chat. L3's project-bound
[`hold-merge --approval`](CLI.md#recorded-merge-approval) executes directly in altd. L3 cites the original
operator approval, owner's single-PR presentation, latest operator message it reviewed and, when the
approval has question context, that question and revision. The daemon reads the checkout-origin PR,
then validates and releases the hold under the project lock. L3 interprets the original sources and
every later operator correction: permission must concern this merge, with no unresolved condition,
ambiguity or revocation. Design acceptance, implementation approval, discussion and superseded
recommendations cannot authorize merge. This semantic judgment is explicit model responsibility;
the daemon checks provenance and scope, not arbitrary prose meaning. Neither a resolution alone nor
agent-authored text releases a hold.

Conversational resolution and merge reconciliation share original-source authority and viewed-revision
validation. A question answer used as merge approval requires the latest revision of that question,
an answered operator decision citing the same original message and source, no remaining question, and a matching
selected option for UI acceptance. The presentation precedes that question and approval. Task chat also
attaches resolved questions to later messages: when a fresh PR presentation follows that resolution,
a new conversational approval may cite the still-current revision as context only. The receipt marks
`question_context_only` and carries no earlier option; the earlier answer or superseded recommendation
supplies no merge authority. A later reaffirmation remains
a separate source; it cannot turn a superseded manual-repair question into acceptance. A new operator
message after either conversation's cited latest message refuses the request. Project sources use the
original user/chat turn ID through the shared strict source reader; assistant replies and triggered
coordinator traffic grant no authority. Corrupt or duplicate approval evidence refuses release.
Pending project chat also refuses release until admitted and reviewed; queue rows grant no authority.
The current hold generation and GitHub's PR update timestamp must precede the presentation.
A renewed hold or missing evidence refuses release. A later PR update requires a fresh integration
presentation when the original operator decision explicitly delegates
rebasing or overlap resolution within that PR outcome. L3 records that scope judgment in the reason;
the owner's later message names this PR alone and its current full head, after its latest update.
The original presentation and approval still follow the current hold. No scope expansion is authorized.
Without this explicit integration evidence, any later update refuses release.
Task approval URLs name only the observed PR. Project approval may name multiple PRs in the same
repository, including the target when explicit; each release remains independently task/PR-bound.
GitHub must report an open, non-draft,
same-repository PR targeting main on the task's publication branch and supplied head. The last recorded
task PR number must match when present; an adopted PR additionally binds its number, URL and original branch.

The hold generation is its latest `hold-merge` event, or the creation event for an initial hold;
hold changes and their events serialize under the same lock. Each hold change stores a fresh
`hold_merge_id` with its state and event; a write interrupted before its matching event refuses
approval even when the reason repeats. Approval validation reads events
strictly, so corrupt evidence cannot hide a later hold. One atomic task write clears `hold_merge`
and stores `merge_approval`, recording the actual coordinator actor, original operator approval,
source and both conversations' latest operator messages reviewed, question/revision and selected option
when present, original and optional integration presentations,
prior hold generation, PR URL/head and the coordinator's interpretation reason. A `release-merge` event carries the same receipt;
local evidence refusals record `merge-approval-refused`. The operation releases the observed hold;
it does not resume the task or merge the PR. Head binding is checked at release, and the owner
continues through the ordinary landing checks. Direct `--off` remains operator-only.

A project that deploys from its own checkout keeps that checkout at `origin/main`. Dispatch and
daemon-side resume fast-forward it before the provenance gate reads it, so a PR another task merged
while it was still running no longer refuses every launch in the window until that task's report
lands. A sandboxed coordinator never performs this fetch on behalf of a message. The move is the
same guarded fast-forward that runs after a task lands, and it happens only when the checkout is
clean, on main, and strictly behind: a dirty, diverged, ahead, or off-main checkout still refuses,
unchanged and untouched.

The reference-transaction hook allows writes that retain a protected ref's current logical tip,
including `pack-refs` writes whose old object ID is zero. Loose-ref pruning is allowed only when
its nonzero old tip matches both the current ref and its committed entry in the common
`packed-refs` file. Git's files backend prepares genuine packed deletions as zero-to-zero updates
before removing a loose copy; these remain blocked. Packing, repacking and fetch-triggered garbage
collection therefore preserve a lagging main and permit its subsequent guarded fast-forward.
Real-Git regressions exercise these transaction forms and deletion refusals in loose, packed and
mixed storage, including linked worktrees.

Dirty-checkout recovery uses `alt task preserve-checkout <slug> --reason "…"`, a durable daemon
request available to the operator and the project's L3 for an unlaunched blocked task. Under the
publication and project locks, altd requires dirty main exactly at fetched `origin/main`, then
copies the index for capture without changing main. A local `archive/checkout-<request-id>` branch
points to a working snapshot commit whose parent retains the staged tree and whose grandparent
is the original main commit. This preserves staged-only versions, tracked deletions and untracked
files; applying the net snapshot flattens staging intent. Ref creation refuses an existing branch.
Before cleanup, the task's `checkout_archive` object and `checkout-preserved` event record `branch`
and immutable `sha`; the event includes the request ID, actor and reason. Git cleans through the
captured index without recursing into submodules. Changed gitlinks and dirty nested repositories
refuse preservation; ignored files stay untouched, and ignored obstructions refuse cleanup. Detected
later index or captured-file edits refuse cleanup; operators pause edits during preservation. A
failure retains the archive and the blocked task.
An interrupted executing request refuses replay and names its archive for inspection, including
the window before the task receipt is written. Archives remain local until explicit operator
removal; Altitude never pushes or deletes them and never resumes a task as part of preservation.
Legacy `preserved_checkout` string SHAs and stash events remain readable; their stashes are neither
deleted nor converted. The task owner inspects and applies the snapshot in its own worktree,
reviews the authorized objective and publication scope, and uses the normal PR path.
The [recovery procedure](CLI.md#dirty-checkout-recovery) requires a separate resume after the
checkout passes the guard. Workerless `main-unpushed` tasks retain their fault and blocked reason
when a resume still fails that guard; a failed message wake leaves the inbox intact and does not
retry until another wake is requested.

Every worker is an untrusted process in its worktree, whichever engine runs it. Its only door into
Altitude is the `alt` CLI; the backend validates each command against the task record under the
project lock. Claude Code runs as a foreground CLI inside an independent transient unit with Altitude's
hooks for inbox delivery and telemetry. Codex keeps its native workspace-write sandbox inside the same
unit boundary and uses the same door; private worker records and output identify both engines' sessions
after restart. Worker status accepts systemd's `is-active` result `inactive` with exit code 4 for a
collected transient unit as termination evidence. Unknown states, bus failures and query timeouts
remain unavailable and refuse resume or stop confirmation; an active worker must be stopped before
its replacement launches. A turn that ends without a
report, a block, or a completion blocks the task with its result error or stderr tail, on either engine. A
Codex L3 uses a dedicated permission profile: only its fresh per-turn runtime directory is writable; the
deployment checkout and Altitude home are read-only, direct command networking and the user-service bus are denied,
and a required stdio MCP coordinator tool forwards structured requests to that project's role-fenced altd Unix socket.
The adapter runs isolated Python from the protected deployment checkout, outside the command sandbox; it
executes no shell commands and grants no filesystem or service authority to the model. Only this tool is
approved for unattended use; altd continues to authorize each verb. Linux Codex proxy mode denies creation
of AF_UNIX sockets, and its proxy Unix allowlist is macOS-only, so a filesystem read rule cannot enable the
shell wrappers. The command sandbox keeps networking disabled. The common session confinement version rotates
legacy conversations once before their next turn, so a resumed conversation cannot retain the old transport
instructions after activation; current conversations then resume normally. A Claude L3 turn uses an equivalent runtime cwd,
`dontAsk` with unattended prompts denied, restricted settings, only Read/Grep/Glob/Bash, no editing
tools, and exact read/`alt` command rules. Claude's runtime shims and the MCP coordinator tool send `alt` invocations plus authenticated GitHub
and service-status reads through the project-bound Unix socket; altd supplies the project independently of the request,
re-applies the L3 command door, accepts only flat task identifiers and stdin, and exposes no direct GitHub or service write command. Read-only Git and journal shims resolve against the deployment checkout. Claude's native Bash sandbox
is not enabled because this deployment host cannot create its required unprivileged bwrap namespace;
the permission boundary fails closed instead, while Codex retains its native filesystem sandbox.
`alt issue new --title "…" [--label …] -` and `alt issue comment <number> -` publish stdin through altd's login to the checkout-origin repository for L3 or the operator, refuse L2 and private evidence references under the AGENTS.md boundary, and record one project event with actor, title, and URL.
`alt issue close <number> --reason completed|not-planned` uses the same boundary for requested closure
or verified completion of an authorized delivery missing its closing link. L2 supplies the issue,
merged PR and complete-scope evidence through its reply and report follow-ups; L3 verifies and closes
with `completed` without another routine request. Unrelated backlog cleanup remains unauthorized.
The CLI and coordinator share an exact parser; the
operator's `/api/issue` endpoint and coordinator share operation and field validation. Close requires a
positive issue number and an explicit reason, accepts no body or publishing options, and passes
`not-planned` to GitHub as `not planned`. After GitHub succeeds, altd returns the checkout-origin issue
URL and appends an `issue-close` project event with actor, number, reason, title, and URL. L2 issue
permissions and the read-only `gh` broker remain unchanged.
New, comment and close target only the selected project's checkout origin, with no repository
override. L3 can explicitly file an Altitude defect with the separate create-only
`alt issue upstream` verb through the same transport. Its product target and public reproduction
contract are described under [faults](#faults); private evidence stays in the calling project.

## Faults

A system fault is project-scoped and two-tier. Tier one is code: a temporary capacity stop is retried
with backoff; a usage-limit stop starts a fresh attempt on an eligible configured alternative from
the task's `progress.md`, or parks the task when no alternative is eligible or it is explicitly pinned.
The engine seam reports allowance scope and an optional reset. A named model allowance excludes only
that model, never unrelated models or engines. An unknown reset creates no resume timer or global
timed hold; the existing routing observation expires after thirty minutes without claiming a reset.
A provider-reported reset schedules resumption. Fresh attempts retain the existing worktree, including
uncommitted work, and validate its branch and commit provenance. Task conversations, worker evidence,
PRs, expected files, questions and merge holds remain. Tier two is L3: whatever remains blocks only its own
task, files private incident evidence (one incident per source project and fault kind per 24-hour
window), and leaves an FYI and one message in that same project's L3 queue; a repeat of that kind
blocking another task or changing its details adds one line for L3 within that window. Full fault
reasons are the task-local observations: unchanged blockers stay quiet even after the incident window
expires, including alternating observations from tasks sharing a kind. Changed observations update
the saved reason and supersede pending recovery based on the earlier block. Capacity or quota
waits during recovery retain the fault reason;
their existing resume receipt, event and due time carry the wait. Escalation retains the fault
reason while its independently saved question carries the dilemma. The machine fault
ledger retains full details and keys records by the JSON-encoded pair `[project, kind]` (`null` for a projectless fault);
incident references include their owning project. Unscoped historical records remain evidence and do not suppress
notifications. Machine faults without a project notify registered `altitude`; when it is absent,
they only update the machine fault ledger. An L2 that meets an environment fault (a sandbox, host, or tool refusing
what the brief requires) reports it with `alt task block --fault` and takes the same path, so the
cause reaches L3 instead of sitting on Needs you as a question for the operator. The server delivers that message as a turn when L3 is free and an engine
is available; L3 records the learning on the incident and fixes the cause directly or creates one
ordinary task. An incident raised by that repair task (`--source recovery`) stays in the project's inbox instead
of waking L3 again. A task blocked before any launch goes back to the queue when it is resumed.
Incident records are evidence only and never create tasks, personas, or follow-up work.
Restart inventories and incidental events do not turn saved blockers into new failures. Every L3
turn receives this guidance, including resumed provider sessions. The originating L3 checks public
delivery evidence and relevant local observations that the cause is gone before the existing
reason-bearing resume or explicit provider handoff. Notification receipt, issue closure or unrelated restart is insufficient.
Coordinator messages to faulted tasks use the existing non-waking inbox marker; they remain in the
conversation and reach the worker on a later supported resume. Operator discussion still uses its
ordinary wake path. The original attempt, provider session, launch model, worktree and merge holds
remain under the existing dispatch and landing rules.
`alt task recheck-ci` registers one finite CI probe on an existing fault-blocked task through the
same project-bound coordinator transport. The task's `ci_recheck` record binds its block, attempt,
worker/session and lifecycle request identities, selected run, due time, budgets, evidence and L3
receipt. The existing tick/keyed executor owns IO; `resume_after` and `daemon_request` retain their
ordinary lifecycle meaning. A lifecycle change invalidates further probe actions.
The daemon prefers a relevant fresh run of the same workflow, branch, event and PR identity from
the latest twenty runs. Otherwise it submits one rerun of the selected project-origin run after
persisting its baseline attempt and submission intent. Restart and uncertain submission reconcile
attempt metadata without repeating the write. Reads stop after three failures, twenty-four rounds
or two hours after the due time. Fresh nonexpired, nonempty artifacts created during the observed
execution and at or after the scheduled check time establish an upload; step conclusions alone do not. Old-run reruns
retain their original workflow. Unchanged conclusions without upload evidence finish silently.
Changed evidence uses one `ci-recheck` row in the existing L3 queue, retained through handling.
Its task receipt stores the turn identity before execution; explicit successful terminal chat
evidence repairs an interrupted receipt after restart. Delivery has two attempts and a one-hour
deadline, including unavailable engines, with a visible terminal failure on exhaustion. Retry waits
do not hold ordinary chat. Queue and terminal-history read failures share the bounded delivery
storage budget. Each engine's existing transient execution boundary enforces the turn timeout
independently of altd, with five seconds to stop its process tree. A restart after provider execution
begins without terminal evidence ends visibly uncertain instead of launching overlapping handling.
No probe resumes a worker, resolves a question, or releases a merge hold.
Project-local repairs remain owned by the affected project. Its L3 reports Altitude implementation
defects with [`alt issue upstream`](CLI.md#upstream-altitude-defects), a create-only exception to the
project-local issue verbs. The daemon owns the product target seam: `ALTITUDE_UPSTREAM_ISSUE_REPOSITORY`
in altd's environment, defaulting to the installed Altitude checkout's GitHub origin. Resolution
uses neither the calling project's origin nor a registered `altitude` project's state. The caller
cannot override the destination, attach files, label, comment on, or close upstream issues.

The same issue parser and handler serve the project-bound broker/MCP transport and operator HTTP
API. Upstream bodies contain only caller-authored expected behavior, actual behavior, reproduction,
and optional version. The handler rejects recognizable credentials, home paths and private evidence
references before invoking `gh issue create`. It returns the confirmed URL or an actionable failure,
and records actor, title and URL in an `issue-upstream` event in the calling project. Incident evidence
files and conversations never supply public content. Reporting creates no tasks.
The reporting project's L3 does not repair Altitude; Altitude's operator/coordinator selects any
implementation separately. There is no automatic intake from issues.

Confirmed creation and verified linkage notify the registered local `altitude` development project
only when its Git origin matches the confirmed issue repository. Missing, removed, or nonmatching
projects remain issue-only. `server.notify_upstream_issue` checks the existing registration and origin
under the receiving project's activity guard; queue admission rechecks the same checkout under its
project lock. The fixed server message contains only the public URL and a statement that work decisions
belong to the receiving coordinator/operator. It carries no source project, incident, private evidence,
conversation, task instruction, or task association, and changes no receiving task or provider session.

`l3.queue_upstream_issue` atomically appends to the existing queue under the receiving project lock.
Pending rows and all retained `upstream-notification-received` project events deduplicate by receiving
project and normalized full issue URL, including reports from other source projects and daemon restart.
The queue claim records its event before removing the row, so the queue-to-chat gap cannot produce
another notification. `received` means the queue consumer claimed it, not that a model completed a turn;
an exit after dequeue retains the existing queue's delivery limits. Ordinary queue/chat surfaces show
the notification, with no new page or task lifecycle. A receipt grants no repair or resume authority.

`--incident <id>` binds reporting to a system incident in the calling project's index. System incidents
carry a `fault_key` to the existing source-project/kind ledger record; its `upstream` outcome retains
status, URL, reason, actor, timestamp and the incident that recorded it. Incident inspection projects
this shared outcome onto each linked incident. The identity is the existing fault kind, not inferred
semantic matching; repeated notifications and later incident windows retain the same linkage.
Unlinked historical incidents show missing delivery without inferred linkage or bulk backfill.

A short fault lock compares and saves the outcome before external IO. A persisted uncertain receipt
precedes creation, so interruption, timeout, nonzero exit or an unconfirmed response blocks another
create. Proven prepublication failures retain actionable failed status. Confirmed delivery returns
the known URL without publication. `--incident <id> --url <url>` verifies only that issue at the fixed
upstream target with a GitHub read and attaches it; it can resolve uncertainty or explicitly share a
known matching report across kinds. Failed verification retains the prior outcome. Finalization
compares the saved receipt so a late result cannot overwrite a concurrently verified link.
Outcome events stay in the originating project's log. `alt incident list`, project API incident rows,
`STATE.md`, and fault/restart coordinator messages expose status and gaps without granting reporting
authority, clearing a fault, assigning repair ownership, or retrying an uncertain result.
Publication is confirmed before notification is attempted. Its separate `notification` outcome records
`queued`, `received`, `unavailable`, or `failed` in the existing incident outcome and source project event
log. Queue failures retain publication success; a repeated confirmed incident command retries only the
notification. Reports without an incident still notify after success and expose queue failure as a
source-project FYI. Failed or uncertain publication and failed link verification never notify. There is
no automatic publication retry or historical notification backfill.

A merged Altitude change marks activation pending when the self-deploy fast-forward brings in loaded
backend paths (`altitude/`, `bin/`, `systemd/`) or tracked inputs to the served web bundle
(`web/src/`, `web/design/tokens.css`, `web/index.html`, `web/package.json`, `web/pnpm-lock.yaml`,
`web/tsconfig.json`, `web/vite.config.ts`), whether the fast-forward runs after a task lands or at the
next dispatch, or the regular thirty-second daemon tick discovers a merge while its worker still runs.
Web docs, design boards, the unused npm lockfile, and other non-build files do not
trigger activation. Hooks, personas, and templates are read per use and deploy with the pull itself.

The web app's restart banner sits above the header on every route while activation is pending: it
uses a compact phone summary with Details and the same available Restart action. Changed area,
file count, age and quiet-point wait reasons expand on request; activation and request failures
remain explicit. On desktop it says in words whether the backend, the web app, or both changed,
how many files landed and when, and
that Altitude restarts at the next quiet moment; it names any dispatch, L3 turn or report verification
in flight. Both engines launch L2 workers in independent transient user units outside altd's cgroup;
running and blocked workers survive activation and are adopted afterwards. Each worker unit and the
service retain `KillMode=control-group`, so stopping a worker takes all its descendants. An exited or
missing worker on a running task requires a report written since its latest launch or resume
or an explicit completion; without one it blocks with a system fault and incident. An explicit
question block remains waiting after worker exit and needs no completion report. Dispatch continues
while activation is pending. When those short windows are quiet, altd runs
the one guarded restart script as a transient user unit outside its own cgroup. It installs the
pnpm-locked dependencies, builds and validates the latest bundle in staging, rechecks the checkout and
quiet point, swaps the bundle, restarts safely, and verifies both API and UI; verification failure
restores the prior bundle. The banner's Restart button runs the same path sooner by hand: it appears
at that narrow quiet point, even while workers run, disappears once restart is under way (the banner then says so), and
the banner leaves when the new process answers with nothing pending. A restart that has not happened
ten minutes after it was requested is a system fault for L3, and the hold lifts. Dispatch, resume and
L3 turns wait only from the unit request until the replacement daemon is ready; report verification
also waits, leaving reports durable for the next tick. A shared activity lock fences these short
operations against the exclusive restart request, including the launch-to-binding race. Ordinary source changes never
start, stop, mask, unmask, or restart the service; a lifecycle action by hand needs separate
authorization and post-change health verification.

## Interfaces and storage

Task token accounting is passive. `usage.py` retains each recorded task owner identity before a
resume or recovery replaces it, and asks `engines.py` for normalized local observations. The daemon
collects at most once per ten seconds per active task; HTTP and CLI reads serve the persisted
`status.json.token_usage` snapshot and do no provider-log scanning. Engine adapters increment byte
cursors over complete JSONL records and retain numeric response/message identities for deduplication
in task-local `token-usage.json`. Discovery reads bounded provider metadata behind the engine seam.
Neither telemetry nor helper discovery creates managed sessions, model calls, incidents, holds, or
routing decisions. L3's project conversation is outside task accounting.

Task transitions refresh available evidence for reports and completion; archive retains both the
public snapshot and collector state before worktree cleanup. The worker-authored report's `spend`
does not supply token accounting. Task API, status JSON, report inspection JSON, and the task/report
views expose the same observation, including partial/unknown coverage and last checked, last counter,
and finalization times. Lost logs and collection failures retain earlier evidence, mark gaps, and
never prevent delivery. A final read drains only a bounded backlog; unread evidence stays partial.
See [the engine counting semantics](SESSION_LIFECYCLE.md#task-token-accounting).

The snapshot's `helpers` contains the observed unique identity count, known direct/descendant counts,
unclassified depth count, attributable request token sum, and all helper rows. It is independent of
the non-overlapping accounting `sessions`: an unsplit provider total can suppress overlapping rows
from that sum without suppressing their helper identities. Helper rows carry engine, native identity,
parentage kind, owning session, depth when known, and the owning session's recorded attempts. Per-helper
request counters and optional unsplit provider totals remain separate; neither is added again to
task totals. Missing native evidence yields null counts; a readable native source with no discovered
helpers yields an observed empty set with partial coverage. No spawn-completeness claim is made.

The web build uses pnpm's frozen lockfile and emits `web/dist/` through `make web`. `make check`
runs Python, web unit tests, typecheck/build and the separate Playwright `web/e2e/*.pw.ts` suite.
Python fixtures isolate runtime/provider homes and replace external engine execution and GitHub
responses. Core integration tests retain real routing, dispatch, task transitions, HTTP handlers,
file storage, locks and temporary Git repositories. Unexpected real provider/service execution
is refused by the test bootstrap. This is test scaffolding, with no production test mode.

Browser specs serve the candidate's built app through a disposable real Python HTTP handler on
an OS-selected loopback port. Fictional projects, tasks, history and session records give every
run stable data. Scripted engine replies exercise streaming, failures, retry and resume; API
overlays remain for named UI loading and transport-error states. The service fixture does not
start the production daemon or its timer and is cleaned up after each test. No routine test uses
the operator's running service or launches a real worker. Live-provider validation is deferred
under the operator's [testing policy](../AGENTS.md#checks);
the [coverage matrix](DEVELOPMENT.md#coverage-and-limits) identifies unproven external behavior.

The same browser specs run at 390×844 with mobile user agent and touch and at 1440×900. The
smoke spec reads the real route tree and checks content, assets, console/uncaught errors, API
failures and horizontal overflow. `walkthrough.ts` drives actions, asserts visible text/roles
appearing and disappearing, and saves named screenshots on passing and failing walkthroughs.
Traces are retained only on failure. Outputs stay under ignored `web/ui-artifacts/`; local landing
copies only the self-contained HTML report with its attachments alongside the candidate logs and
result record in the task folder. Raw results and browser configuration are not copied again.
Captures needed for review remain accessible outside Git.
The committed design tree holds maintained boards and their spec; review galleries and routine
renderings are not source artifacts. Curated documentation illustrations retain a maintained source.
Hosted CI and its artifact upload are suspended for this repository. Every local test, build and
candidate-identity step remains required. Bundled
Chromium runs headlessly with a temporary profile and its browser sandbox disabled inside the
worker sandbox. [Development and checks](DEVELOPMENT.md) documents installation, commands,
timings and candidate identity; [operations](OPERATIONS.md) covers service activation and mobile access.

[Release checkpoints](RELEASING.md) select an exact validated source SHA for an explicitly
published private-preview version and release notes. They add no runtime lifecycle state and
do not gate automatic activation of merged changes. The UI and testing rules remain in the
project instructions file, which both worker personas direct the task owner to read first.

The Python server owns state transitions and JSON APIs. The React app is one shell around four
pages, specified in `design/wireframes/SPEC.md`: Needs you at `/` (every decision across projects as
compact cards in one column, answered through `POST /api/decide`), the project page at
`/projects/<name>` (the §3.2 header with its status line and overflow menu, the L3 conversation, and
the work panel), the task conversation (including redirects from `/projects/<name>/decisions/<slug>`), and
Monitor. `/projects` and `/chat/<name>` redirect to the project
page, and with no managed project every project route shows First run, which lists the folders under
the configured roots and starts L3 for one through `POST /api/project/add`, staying up until L3's
first reply or the error row that stands in for it. At 1024px and wider the rail is 260px and the work
panel is 340px, inline at 1280px and wider and an overlay from the header's panel button below that;
narrower is the phone: one 54px identity/activity header and an 84px tab bar (Chat, Work, Needs you,
Monitor). The shell follows visual viewport height and offset, hiding bottom navigation during
detected software keyboard use and restoring it on dismissal, including when focus remains in the
field. Editable focus alone, toolbar motion and pinch zoom do not hide navigation; without sufficient
viewport evidence it remains reachable. Browser-managed safe areas remain intact, with any bottom
inset owned once by the visible dock. The project header opens the switcher sheet, and a task conversation
pushes over its tab with a back control. Those widths are named once, in `web/src/shell/breakpoints.ts`. The
selected project is browser state under `localStorage`, set by the rail, the switcher, a project
route, or a Needs you card; the theme (light by default, dark on request) persists the same way. The
rail's engine readout renders `GET /api/overview` `engines[]`, one row per configured engine with the
display name the engine seam gives, so the web code names no provider; the same read carries the
scan roots First run names and the operator's configured name. `POST /api/l3/start` runs the start
turn for a managed project whose L3 never ran, from the header's Start L3. The conversation is the only
way to create a task from the web: the L3 turn creates it through `alt task new`, and altd records
the slug on that turn's assistant row (`tasks: [slug]`), which the conversation renders as a task card
under the reply. Engine selection is in phone project details and the desktop composer's pill;
a non-Auto pin stays named in the phone header. It pins the project's L3 to one configured engine, named as
`engines[]` reports it, until set back to Auto; Auto uses project preference tiers, weekly headroom
within ties and the session continuity rule described above. A chat turn belongs to L3,
not to the page that started it: when the page leaves mid-stream, the turn finishes and its answer
lands in the history. The conversation component is keyed by project, like its query cache:
switching projects discards the unsent draft, pending bubble and local stream. Submitted-text recovery
retains its original conversation identity. Composer
unmount stops the recorder and releases its microphone tracks through the recorder's own stream;
pending transcription is cancelled and cannot update the destination draft. Outstanding
callbacks and cache updates retain the source project. The destination renders its own history,
queue and active turn; switching back reconstructs those server records, including Retry for a
failed turn, without restoring an unsent draft. `GET /api/chat` reports the server-owned active turn as a stable id, start time,
and trigger without copying its prompt, and it is the conversation's only authority: the page polls
it independently of open response streams and never infers a turn from `busy` or the last history row. A fresh mount or reconnect renders
the record as the typing indicator for a chat turn, or as the line "L3 is handling <what>" for a
server-triggered one; the tab that started the turn keeps its streamed reply instead. The stream's
first line names the turn (`{"turn": {id, started_at, trigger}}`) before any text, and the
history rows carry the same id, so the local rows stay until history owns the turn and a stored
assistant or error row wins over a raced active snapshot.

The conversation groups `chat.jsonl` rows by `turn_id` (rows without one, from before the id, by
adjacency). The operator's rows are bubbles on the right, L3's prose on the left, under day
dividers, with a row's time in the gutter on hover or a long press. A server-triggered turn (report
landed, block, incident, recovery, restart, or a system FYI row) folds to one centred 13px line: a
dot, red for an incident, a recovery, or a fault, the last paragraph of L3's reply, and Show. The
card behind Show carries what altd sent L3, L3's reply, and links to the task, to the report view
at `/projects/<name>/tasks/<slug>/report`, and to the digest when the task has one. altd writes the
landed-report prompt as a header of `Label: value` lines (Task, Verdict, Problems, Post-mortem
signals, PRs, Spend) followed by the instruction to read the full report with `alt task report`, so
the card shows the header as label/value rows and an older prompt as preformatted text. Consecutive
routine system turns between two operator messages fold to one line, "L3 handled N system events between
your messages", that expands to the list with each turn's own Show; a turn in progress reads "L3 is
handling <what>" with no Show, a failed one "L3 could not handle <what>" with its error behind Show.
A failed chat turn reads "L3 could not answer this turn." with Retry, which resends the same text.
Explicit L3 FYIs with `heads_up: true` stay outside those groups in chronological order, splitting
the routine runs before and after them. Their full concise text is visible in the existing compact
system line; Show opens the FYI card and task links, and Hide restores the line. Automatic FYIs and
historical rows without explicit selection remain eligible for grouping. Conversation loading,
cached-error display and following the latest messages use the same existing behavior.

`GET /api/project/<name>` includes `repository`, the GitHub HTTPS web URL derived from the deployment checkout's SSH or HTTPS `origin`, or `null` without a GitHub origin; the task PR chip links to `<repository>/pull/<n>` in a new tab when present and stays text otherwise.

`web/src/components/Prose.tsx` owns reference rendering for L3 and L2 replies, live session prose,
system summaries/cards, decision questions/recommendations/follow-ups, and report prose and fields.
Each view supplies its project's cached `repository` through `ProseRepository`; cross-project
decision cards read their own project. Plain `PR #250` and `pull request #250` use `/pull/250`;
`issue #247` and bare `#247` use `/issues/247`, which GitHub redirects for pull requests.
`owner/repo#247` overrides project context and works without project metadata. Targets are built
only from validated GitHub HTTPS repository paths and positive issue numbers. Existing Markdown
links and URLs are consumed before reference matching; inline code and shared backtick/tilde fence
boundaries exclude code in full prose, compact mirrors, and folded summaries. Anchors retain their
visible labels and use the app's focus styling, underlines, and new-tab `noopener noreferrer`
behavior. Rendering never changes stored messages or performs per-reference requests or model calls.
Missing/loading/failed repository metadata leaves unqualified references as text until available.
Both personas and every L3 turn's guidance preserve upstream identity in generated replies, briefs
and summaries as full URLs or `owner/repo#number`. The renderer keeps bare references local and does
not infer an upstream repository from ambiguous historical text.

A message sent while L3 is busy is queued, never refused: the composer stays open, the send control
keeps its arrow, the header names the active work, and the message shows as a muted queued row with
its run order and Remove until
its turn starts, when the row becomes the turn's bubble and typing indicator. Queue claim writes the
user history row and publishes the active record under the same lifecycle guard used by the API's
history/queue/active snapshot. Routing precedes claim; failed history admission restores the waiting
queue. The claimed turn reuses its admission and route choice, so the text stays visible through
handoff and is delivered once. A control takes Burak's chat back off the queue only while it
waits. Server-triggered work is also visible in its FIFO position but is not editable. The queue is a
file in the project directory, so a reload, another device and a restart all see the same pending
messages. Each turn drains it at its own boundary rather than at the next tick: consecutive chat
messages fold into one turn in arrival order, each on its own line, while server-triggered messages
keep their own turn, and nothing runs while a turn holds the project's L3 lock.

The project conversation and the task conversation use one
composer component, `web/src/components/Composer.tsx`, with no page-specific props.
The page owns its draft and submit function. A valid turn, terminal turn ID, queue receipt, or
saved task message establishes acceptance; HTTP stream headers alone do not. After acceptance,
a broken response stream resolves to the existing history refresh without restoring the draft or
inventing an assistant failure. Each local stream callback belongs to its own send, so an older
stream cannot alter a later queue request. A failed refresh is a read error, whose Retry only reads.
Explicit HTTP refusals restore the submitted text with "Not sent. Retry."; transport, malformed
receipt and server failures without acceptance evidence restore it with "Could not confirm delivery.
Check the conversation before sending again." and no send Retry. Recovery retains newly typed text
after the submitted text on a new line. Combined failed drafts remain unconfirmed if any send lacks a
receipt. No text matching or automatic resend infers delivery.
The composer keeps only submitted-text recovery in browser-tab `sessionStorage`, keyed by stable
project or project/task identity. Live request callbacks outlast component unmount and restore a
failure only to their original conversation. A receipt removes its request's recovery copy before
the answer finishes; older reads are cancelled before a late receipt updates the query cache.
A reload without a receipt restores pending text as unconfirmed, even if history now contains a
similar message. The operator checks history before choosing to send. Recovered text and later typed
or dictated edits remain recoverable while unconfirmed; editing a refused recovery returns it to an
ordinary draft. Sending explicitly replaces the recovery with the submitted request. Ordinary unsent
project-switch drafts are not persisted. Failure to save the initial recovery copy leaves the text
unsent with an inline error. No recovery record initiates delivery or provides a second server queue.
A failed recovery update keeps the latest text in the current browser document and displays a
keep-tab-open warning; it cannot promise that unwritten edits survive reload. Once storage accepts
updates again, returning to the conversation saves that recovery and clears the warning. Unread
browser evidence is not overwritten by a failed read.
The composer owns microphone
permission, MediaRecorder state, a 595-second client stop below the server's 600-second
decoded-audio limit, transcription, cancellation, and focus. A landed transcript is appended to the
draft with the cursor at the end and nothing else appears (issue #195): existing draft text is the
prefix, separated from dictated text by one space when it does not already end in whitespace. Its
send control is an arrow in an accent circle in every state, with no visible text and an accessible
name of "Send" ("Queue" while busy). Its states are the design spec's §3.6 table (idle, typing, sending at 60%, busy queueing, listening
with a live waveform and timer, transcribing, landed, denied, unavailable, refused), each walked at
phone and desktop widths in `web/e2e/conversation.pw.ts`. On phone text, mic and send share one row
with 44px controls in a 70px single-line dock. Drafts grow from 44px to the lesser of 120px and
25% of the usable visual viewport (at least 44px), then scroll internally. Routine phone hints
consume no row; relevant voice, permission and send errors remain visible. Desktop retains its
shortcut and delivery hints. Keyboard, draft and streaming changes keep bottom-follow when already
following and preserve the visible message and offset while reading older history. Sending resumes
following. Browser emulation verifies layout and application transitions; native mobile keyboard
behavior requires real phone acceptance. Decision and reason fields remain
ordinary form fields.

The L2 task's phone tabs replace the current router history entry and retain its location state;
the desktop live panel toggle stays local. `/live` remains addressable and selects the live view
on reload. Both task Back controls traverse the existing browser history when React Router's
entry index indicates an in-app predecessor. With no such predecessor, app Back replaces the
task entry with the owning project's L3 conversation. Browser Back remains native, and links to
other pages or tasks still push entries. `web/e2e/task-navigation.pw.ts` exercises Back and Forward
with real browser history at both viewports.

The task card (`web/src/components/TaskCard.tsx`, spec §3.5) is one component in two sizes: the
bordered card under an L3 reply that created the task and the row in the work panel. Its meta line
comes from the task's state and, for a queued task, from `GET /api/overview` `wip.waiting[].hold`,
the queue's own reason (the WIP limit, an engine hold, a restart in progress, a resume checkpoint,
or plain dispatch), so the card never names a file list. A task blocked waiting on L3 reads "Waits
for L3" with the running dot, and the rail's project dot counts it as running (`counts.waits_l3`);
only a decision in the queue turns either dot amber. The work panel (spec §3.7) reads the project's
tasks and the overview queue filtered to the project. **Current** contains every unfinished task
once as a compact status row; tasks done or rejected in the last seven days fold under **Done this
week**. A row with an open operator question shows **Needs you** and its question count alongside
independent execution or fault status, and opens the owning question's conversation anchor. Other
rows open the ordinary task conversation. Questions and answer controls live in global Needs you
and the owning chat. Partial or final answers update the same row; only completion or rejection
moves it out of Current. Saving an answer does not assert that the worker resumed.

Only global Needs you carries a numeric attention badge. Project rail and switcher rows retain
their state dots. The badge counts unresolved operator questions plus existing operational
attention items; Needs you and project summaries label questions and operational items separately.
Unknown overview reads never imply zero attention. Failed refreshes identify saved counts and
status as stale; Work links stay available, while Needs you disables answers until a fresh read.
App Back and browser history preserve the originating Work or Needs you view.

### From model judgment to a task question or preview

The L2 persona and repository instructions teach the owner when to ask a question, how to publish
visual evidence, and which commands are available. The model judges whether a decision is needed,
chooses the question/options and selects proposal files. It invokes `alt task block` through its
engine's execution tool, with ordinary command arguments and, for a preview, `--design-file` pointing
to an explicit JSON selection. There is no automatic interpretation of arbitrary model prose as
a question or an approval. The engine's own tool-call display is separate from the task dilemma
that Altitude renders in its web conversation.

`bin/alt` checks the role and owner environment; `tasks.block` verifies the current attempt under
the project lock. It captures selected preview files, publishes the question/revision in
`status.json`, and blocks the task through the existing transition. Question messages are projected
from those durable records into the same human conversation as `conversation.jsonl` replies.
The app reads `/api/task` for that conversation, `/api/overview` for unresolved operator questions
in Needs you, and the fixed preview API for the selected proposal. The CLI's design publication
response includes `design_url`; the task question exposes the same link. The detailed capture and
serving contract follows below, and [CLI examples](CLI.md#task-design-previews) show actual inputs.

```mermaid
sequenceDiagram
    participant Owner as L2 model
    participant CLI as Engine execution tool / alt CLI
    participant State as Task records and captured files
    participant App as Altitude API and browser
    participant Daemon as altd
    Owner->>CLI: block with question and optional design selection
    CLI->>State: Validate owner/attempt; save capture and question revision
    App->>State: Read conversation, question and fixed preview
    App->>State: Explicit decision OR typed message with viewed revision
    State->>Daemon: Durable inbox / resume request
    Daemon->>Owner: Deliver at checkpoint or resume the same session
    Owner->>CLI: Discuss, or resolve a clear answer citing its source message
```

Transport depends on the role. An L2 uses the `alt` CLI in its task environment through its engine's
execution tool. A Codex L3 uses the `altitude` coordinator stdio MCP tool: structured requests carry
`kind`, argument arrays and stdin, and its adapter forwards them to the project-bound altd socket.
A Claude L3's runtime command shims use that same broker. The broker fixes project authority and
checks allowed coordinator verbs. MCP transports coordinator requests; it does not own dilemma,
preview or decision state, and that L3 transport restriction does not apply to the L2 CLI path.

An explicit quick choice sends the question/revision (and group revision when applicable) to
`POST /api/decide`, which validates and saves the decision. Typed discussion uses
`POST /api/l2/message` and stays a message until the same owner judges it as a clear answer and
records `alt task resolve` against the original source message. Both use the durable inbox and
daemon wake mechanism described in [session lifecycle](SESSION_LIFECYCLE.md#messages-resume-and-stop).
Opening a preview performs reads only; neither viewing, a follow-up nor a resume accepts a proposal.

A dilemma (spec §3.8–3.10) lives in the owning task conversation. The task record's `questions`
contains versioned question text, zero to three explicit options and a recommendation key,
source/audience, stable ID and message anchor, and an open or resolved status. A question group
contains up to three independently answerable members, a group revision, and one stable discussion
anchor. `task_view` projects `question_group` with current member records, `question` as the first
open member (or latest receipt), and individual revision history;
`GET /api/overview` and the project view project the same unresolved operator questions. Question
state is independent of worker state: a discussion wake, capacity wait or ordinary resume never
records a decision. Existing stopped/fault cards link to their ordinary task controls; an operational
pause with no open question offers Resume through the existing daemon operation.
If a provider limit queues a fresh attempt, the existing dilemma remains answerable. Replies and
acceptance wait in the same inbox for normal dispatch; the fresh brief includes the current question
or its recorded resolution. A queued task without a question retains its ordinary initial state.

A direct L2 block publishes its question into that human thread. A block that publishes or revises
questions queues one L3 notification, including operator-directed blocks. The message names
open members, revisions and their required authority. Comparing existing question revisions keeps
unchanged re-parking quiet without another receipt or tracker. L3 can coordinate record-backed and
scope portions; notification does not approve operator decisions or change their audience.
An L3 escalation publishes the
actual dilemma and recommendation with L3 attribution, and supplies it to the owner's next normal
checkpoint without launching a worker just to announce it. Explicit `--recommendation`, `--label`
and `--why` fields name a single approach; `--questions-file` publishes a small group or explicit
quick alternatives. The model chooses the suitable form. Existing labelled recommendation prose is understood, but an
unmarked first option never becomes an acceptance button. Pending older blocks are materialized
before a resume can clear their operational block fields.

`POST /api/decide` takes `{project, slug, question_id, revision, option_key}` for an immediate choice;
omitting the key explicitly selects the recorded recommendation. A group sends
`{project, slug, group_id, group_revision, answers: [{question_id, revision, option_key}]}`.
Under the existing project lock, the whole batch is validated before any write. Only named members
close; omitted questions remain open. One ordinary operator message and the resolutions are saved
together, then delivered once through the existing inbox/resume path. Nothing is preselected in the
UI. An identical retry returns its saved receipt plus the current group and repairs interrupted
delivery; stale or conflicting submissions fail together. The UI updates Needs you and chat from
the authoritative group, then refreshes their reads.
**Decision recorded** means persisted; **Work resumed** requires observed running state.

Typed replies use `POST /api/l2/message`, optionally naming the viewed question/revision or
`group_id`/`group_revision` as context. The saved message retains the viewed member references so
one conversational answer can settle several questions independently.
The message endpoint returns the stored row even when its immediate resume wake fails; the saved
resume request remains due for the existing timer. L3 queue wake failures likewise retain the queue
receipt and defer to the timer. A restart race after stream headers returns the saved queued row.
The same L2 answers follow-ups, clarifies uncertainty, or uses [`alt task resolve`](CLI.md#conversational-decisions)
to record an actual decision against its original message. Task/attempt ownership and source-message
authority are checked at the existing command boundary; L3 prose cannot stand in for operator approval.
The owning L2 can explicitly record `--l3-authority` with specific evidence and rationale when an L3
answer settles an unnecessary escalation within existing delegated authority. The command requires an
authentic L3 task message naming the exact question revision; the owner judges whether authority applies.
The existing receipt keeps L3 attribution, source message, authority basis and recording owner/attempt,
and retries cannot replace that basis. The question's audience remains unchanged. Genuine operator
decisions still require original operator authority; ordinary messages and recommendations close nothing.
A partial answer retains only the relevant remaining question in a new revision, without inheriting
an unapproved recommendation. A change of direction can close the obsolete dilemma with its reason.
The resolution preserves the source, author, time and chosen scope, without accepting an abandoned
recommendation. A remainder retains the question's audience without changing independent worker,
capacity or fault-recovery state. Report handoff, rejection and completion close obsolete controls without accepting
their recommendations; report review can raise its own dilemma. Merge holds retain their own rules.

The shared question component appears on Needs you and at its conversation anchor. Single choices
act immediately. Group choices remain staged until **Send N answers**; **Use recommendations** is
available when no manual picks exist and answers only members with explicit recommendations.
Needs you keeps the task's purpose, complete question, concise recommendation and material
consequences visible before an answer. Owners write these for an operator deciding at a glance;
clipping long technical prose is not a substitute. Additional saved question detail opens in a
**More context** disclosure in the owning chat when it differs from the question, alongside its
surrounding reasoning, evidence and history. This
uses the existing question and conversation records without a second summary or decision store.
`/projects/<name>/tasks/<slug>?question=<id>&revision=<n>` focuses that question's group
and surrounding prose, suppressing the initial scroll to latest. Historical revisions remain
readable under **Earlier question**, opened automatically by an old-version link; stale controls
cannot act on a replacement. Following the bottom resumes ordinary chat
scrolling. Pending questions poll every two seconds, and new replies offer **Latest messages**
without moving a reader away from the question. Technical activity and reference links stay behind
**Activity & evidence** and the existing live session view. A saved decision URL redirects into this
conversation; no separate form, recipient selector or mirrored follow-up thread exists.

An FYI (`tasks.fyi`, exposed by `alt fyi [slug] "text"`) is a chat row
`{role: "system", trigger: "fyi", slug, text, by, heads_up}` in the project's conversation.
Internal calls default to `by: "altd"`; the CLI supplies its actual actor and `tasks.fyi` records
`heads_up: true` only for an explicit L3 actor. This selection field distinguishes deliberate
heads-ups from historical automatic calls that inherited `by: "l3"`; history is neither migrated
nor classified by text. The [L3 persona](../personas/l3.md) owns selection guidance.
There is no project inbox file and no `fyis` in the digest or overview.

`POST /api/transcribe` is a bounded adapter to the existing local speech service. It accepts the
browser's declared audio media type (AAC/mp4 on Safari; opus/webm and the other listed containers),
limits the upload to 16 MiB, and asks `ffmpeg` for at most 601 seconds of 16 kHz mono PCM so a decoded
clip over the 600-second product limit is rejected without unbounded output. Conversion lives in a
unique temporary directory. The adapter sends the WAV path through `/tmp/whisper-server.sock`,
falling back to the existing `127.0.0.1:8890` Whisper bridge, then removes the entire directory on
success or failure. It neither persists raw audio nor owns or starts a speech model.

Unreadable media, timeouts, and an unavailable Whisper service become concise client errors while
converter paths and diagnostics stay in the private server log. The composer announces recording
and transcribing, restores the editable field after cancel or error, and leaves the microphone as
progressive enhancement. Altitude's WireGuard origin is HTTPS on `10.88.0.1:8890`; the service reuses
the local-CA certificate whose SAN contains that address, which makes `getUserMedia` available to
Safari after the CA is trusted on the phone.

The same server serves each project's wireframe boards. `GET /design/<project>` redirects to
`/design/<project>/design/wireframes/index.html`, read from that project's own deployment checkout on
every request and sent uncached, so a merged board change needs no build step and no restart to be
visible. Only the `design/wireframes/` and `web/design/` subtrees are readable and only the
extensions a board needs; the resolved path must stay inside those subtrees, and a project without
`design/wireframes/index.html`, a directory, and anything outside the rule are one plain 404. The
tree is mirrored under the prefix because a board's stylesheet imports the build's design tokens two
levels up. `/api/project` reports that URL only when the boards exist, and the project header's
overflow menu turns it into Design boards, opening in a new tab. Any project with boards gets one; the
route knows nothing about this repository's own.

Pending task designs use captured screenshots and text, bound to the existing question revision.
The current L2 supplies an explicit selection through `alt task block --design-file`; ignored and
untracked files are supported without staging or committing them. The task and
attempt checks apply, and the CLI also binds the project. `tasks.py` reads only that task's registered
worktree under the project's `design/wireframes/` subtree, walking directory descriptors without
following symlinks. Publication accepts one UTF-8 `.md`/`.txt` proposal up to 64 KiB and one to twelve
PNG/JPEG screenshots, up to 8 MiB each and 32 MiB together. Raster signatures must match the selected
type. Directories, special files, traversal, symlinks and unsupported types refuse publication.
No directory is recursively published, and no submitted HTML, SVG, script or stylesheet is served.

The existing question stores the captured text, titles and a hashed manifest; content-named image
files live in its task folder's `designs/` directory and follow task archival. The task worktree can
change or disappear without changing that evidence. Repeating an identical publication retains its
question revision; changing any selected content or label advances it, preserving the prior question
and design. A normal block without design inputs retains the attached capture. There is no separate
review conversation, approval state or artifact registry.

`question_view` exposes `design_url` for **View preview · vN** in Needs you and the owning question.
The conversation's offscreen-question navigation also exposes the open question's attachment.
Within a group it follows an open member with a preview, then another open member, including after
partial answers. It uses that question's exact URL, never an earlier proposal's capture. Work reaches the same
question through its task row. Preview headings use the captured title to distinguish a proposal
from an implementation review. Opening a separate tab preserves the originating route and draft.
`/projects/<project>/tasks/<slug>/design/<question>/<revision>` opens in a browser tab with the saved
screenshots, full-size image links, explanation and **Back to question**. The page reads
`GET /api/design/<project>/<slug>/<question>/<revision>`; image bytes use
`/design/<project>/tasks/<slug>/<question>/<revision>/<content-hash>.png` (or `.jpg`). These reads
require a registered project, resolve the owning task and exact question revision, and verify the
saved content hashes. Raster responses use explicit image types, `nosniff`, a restrictive CSP and
no-store caching. Source paths and arbitrary task files are never URL inputs. Missing, altered,
unsupported or inaccessible evidence returns **Design unavailable**, with no fallback to another
version. Loading, Retry and Back remain in the ordinary preview page. Earlier captures identify
their revision and link back to its historical question; current question metadata is polled without
replacing the displayed capture. First acceptance also verifies the saved evidence; identical retries
of an already recorded decision retain their receipt. Viewing and follow-ups do not decide anything,
and neither design acceptance nor publication releases a merge hold.

The Monitor page reads `/api/monitor` and is display only: no hold, incident, route or follow-up
work is derived from it. `/api/monitor` answers with `seats`: one row per configured engine, in the
seam's order, as `{engine, label, quota}`, where the label is the seam's display name and the quota is
that seat's reading whole. Only the engine seam knows which reading belongs to which engine, so the
page ties no reading to an engine key and spells no provider. It sits in the shell's page container
and shows one seat card per row: either the five-hour and seven-day windows a statusline snapshot
names, or windows named by the length the seat reports. Each window renders independently, including
zero; an absent five-hour, seven-day, first or second window is explicitly named, without a meter.
Available windows show percent used, a meter with the 70% reserve line drawn, when it
resets in relative and clock terms, the plan where the seat names it, and how old the reading is. A
seat with no reading at all says so and carries the reading's own `why`, the one line that fixes it;
a reading older than the age the router itself trusts is stale: still shown, dimmed, and labelled. One
routing card answers which engine/model each project's L3 (its pin, or Auto) and a fresh L2 would get
for a turn started now, in `pick_engine`'s own words. It explains the selected tier, comparable or
unknown quota, continuity and skipped options; no eligible option gives an actionable installation,
authentication, reset or configuration explanation. The
sessions the monitor knows follow, each with its task, its engine and the model when the API reports
one, its context meter and the age of its snapshot. L2 rows carry their persisted `token_usage` in
the same read and offer **L2 usage details**: the task total, observed helper counts and attributable
tokens, then per-helper parentage and owner attempt context. All helper detail stays behind the
disclosure; expansion starts no collection or separate request. Archived evidence remains in task
and report reads. No session is one muted sentence. Loading is a
skeleton in the page's shape, and a failed read is one sentence with Retry.

The task page is the operator's conversation with the L2 beside the worker's live session
(design spec §3.10). Its desktop header carries the crumb back to the project, Reject with an inline confirm,
the title with its state dot, a muted
line (attempt, when the task started or finished, context used), and chips: the state, the model on
its engine as the engine seam reports them, the last PR with whether it merged and how the main run
concluded, and concise Merge held status. Complete block and merge reasons open in task details,
wrap without truncation and remain distinct when both apply. The conversation uses the project conversation's bubble, prose,
day-divider, and composer components: the operator's rows as bubbles and the L2's and L3's rows as
prose under day dividers, the question component at its recorded message anchor, and the composer
while the task is running, blocked, reported with open-PR owner evidence, or queued with an existing question. Waiting on L3 stays a
concise status with its complete reason in details; a fault retains a visible cause in red with
"L3 has been told". One replacing two-line public update sits above the composer, expands on
request, and separates prose age from
recorded activity. After 60 seconds without observed output it says no new activity; unavailable or
untimed evidence never becomes apparent progress. Stop is one click beside the composer and in
Live session at both viewports. It remains Stopping until termination is evidenced; failed or unknown
termination says Stop unconfirmed. Status rechecks read evidence without retrying Stop. After Stop,
Continue preserves the unsent draft; sending a correction explicitly resumes the saved session.
Desktop Escape applies only outside inputs, dialogs, recording, menus and overlays. The live
session panel is closed when entering a question. When opened, it is 480px inline at 1280px and
wider and an overlay from the header's panel button below that; it reads the worker's native session
and task-owned turn records together with Altitude's task events as one transcript: tinted prompt blocks, the
worker's prose, each tool call as one compact row with its output folded under it, task boundaries
as thin separators with subtle timestamps, hidden reasoning never shown, and Raw events behind a
toggle for the complete redacted records, the task's other operational events among them. A queued
task shows what it waits for in place of the session, a finished one says the session ended, and a
missing session file says so. On a phone one header carries Back, title, L2 state and independent
Merge held status. Its title and details button open metadata, tokens, full reasons, Reject with
confirmation and operational Resume. Stop and Continue stay directly accessible in both views.
Desktop keeps direct header actions. Two tabs, Conversation and Live session, switch the content
(`/live` selects the second); they stay visible
when software keyboard use hides bottom navigation. The composer sits above that navigation or
the keyboard. Details closes back to its opener without changing the draft or reading position.
Open questions retain their chat anchor and View question action, with no generic Resume; viewing
details never resolves a question or releases a merge hold. A done or rejected task is
read-only with the composer and activity preview gone. View switches preserve draft text and selection.
The read-only activity projection uses only the selected worker generation, existing redaction and
public output. Provider parsing stays in the engine seam; no summarizer, extra model instructions,
new archive or copied conversation replies supply the preview.

Runtime files live under `ALTITUDE_HOME`; a task is a directory a person can read. Source-controlled
personas, schemas, templates, and hooks describe current behaviour: `hooks/` holds the Git hooks
that `git_policy` installs into every managed repository, the Claude inbox hook, and the statusline
monitor. [AGENTS.md](../AGENTS.md) holds project and review rules; these current documentation pages
retain the system's operating decisions and rationale. Git history preserves completed migrations.
