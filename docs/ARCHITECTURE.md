# Altitude architecture

Altitude keeps a persistent project-level conversation with L3, the project's orchestrator. L3
discusses direction, architecture and priorities, dispatches directly reachable L2 task owners,
and receives their questions and reports needing follow-up. Several tasks can proceed in isolated
worktrees while that conversation continues. Model judgment chooses how much
decomposition a request needs; code enforces task ownership, isolation, launch holds, and the PR
boundary.

```text
Burak
  ├─ project direction and roadmap ───────────────► L3
  │                                                  │
  │                                                  └─ dispatches L2 task owners
  └─ task questions and steering ◄───────────────► L2 owner
                                                     ├─ may implement directly
                                                     └─ may delegate to its engine's own subagents

L2 worktree/branch ─► checks/review ─► PR ─► merge ─► archive task
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

L2 receives the request, repository context, lease, worktree, branch, and merge policy, and chooses
the lightest useful execution shape. Its conversation with Burak is stored apart from tool logs, so
Burak messages it directly without routing through L3. Messages queue on the task and reach the
worker at its next checkpoint; an explicit Stop aborts a worker. Appending a message to a blocked
task also persists a due `resume_after` request. An L3 CLI process stops there: altd coalesces that
request with timer and capacity-available wakes, then owns Git provenance validation and provider relaunch. A
durable resume claim fences competing wakes, holds service restart, and records the exact inbox batch and
replacement worker so a restarted daemon adopts rather than launches it again.
Explicit `alt task resume`, `stop`, and `reject` calls also stop in the CLI after persisting one
`daemon-request` event with the task, actor, operation, and required reason. Altd checks the recorded
state and worker/session identity, refuses a stale target, and treats a retry of the same completed
request as idempotent while the terminal receipt still matches; an intervening lifecycle gets a new
identity-fenced request before altd relaunches, stops, or removes a worker.
The attempt number fences every L2 command to the current attempt: an L2 may reply, block, complete,
and land only its own task.

Helpers are engine-native. The L2 may delegate bounded slices to its engine's own subagents
(Claude Code's Agent tool, Codex's equivalent); Altitude does not track them, and ownership never
transfers. Helper customization lives in engine-native files (agent definitions, skills, hooks). The
L2 persona carries brief delegation and context-hygiene guidance and asks for a small `progress.md`
(goal, done, next, how to verify) refreshed at milestones, never kept as a log.

L2 and L3 can run on Claude Code or Codex. Fresh L2 dispatch records one provider choice and keeps
that provider for the attempt. L3 keeps a separate resumable conversation on each provider. See
[Session lifecycle](SESSION_LIFECYCLE.md) for identity, routing, messages, containment, and context.

Tasks and L3 session records carry `engine_model` and `engine_reasoning_effort`. The engine adapter
reads Codex's rollout `turn_context` after the thread starts, recording the actual model and effort
for the current turn. altd reads the provider home; the sandboxed worker does not. Missing or delayed
rollouts leave the observation unknown and are retried while the turn runs. The launch model pin is
kept separately, so recording a default does not turn it into an override on resume. Status, task
header chips, and the Monitor API expose the observation; old records remain readable.
`GET /api/task/<project>/<slug>` returns `engine_model` and `engine_reasoning_effort`;
`GET /api/monitor` session rows expose `model` beside `engine`, with `engine_reasoning_effort`
when available. An unknown Monitor model is an absent key rather than null.

Both roles read two layers of rules. The personas in `personas/` are the global layer: how anyone
works under Altitude on any project, carrying nothing project-specific. The repository's own
instructions file — `CLAUDE.md`, or `AGENTS.md` where an engine reads that instead — is the project
layer, owned by the operator of that repository and read first. Altitude's own `CLAUDE.md` is simply
the project file of the project being built.

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
project and 10 across the machine. Overlapping declared paths are information in task status and
briefs; they do not hold dispatch or resume. One provider's quota does not
globally freeze the other. Blocked is a persisted wait/intervention state: an L2 question, a timed
operational hold, a worker failure, a verifier fault, or a report gap. An L2's question goes to L3
first, which answers from the record or escalates one plain dilemma to the operator; only a block flagged
for the operator or an escalation is a Needs you card. After a restart L3 receives the active tasks and resumes
the ones a fault had stopped. Deferral is not an active
state: durable future work belongs in a GitHub issue, and the task exits the active set.

Project registration stores `wip` only when explicitly supplied; the gate reads that override or
`config.WIP_PER_PROJECT`. On the first registry load, a one-time migration removes stored caps equal
to the legacy default of 3 and logs the affected projects, preserving approval and engine pins.
`alt project set <name> --wip N --reason "…"` and `--unset-wip --reason "…"` are available to
the operator and that project's L3; add and remove remain operator-only. Caps range from 1 to
`WIP_PER_MACHINE`. Altd applies the durable `wip-request.json` before task dispatch on its next tick,
regardless of task capacity, and records one `project-set` event with project, actor, reason, request
id and outcome in the project's `events.jsonl`. Identical pending requests and completed retries
whose WIP receipt still matches reuse the request and event. CLI and HTTP registration, removal,
engine pins and operational settings serialize registry writes under the project and registry locks.
Re-registering a project is the operator's deliberate act, and the last registry write wins.

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

`STATE.md` is regenerated from active task records and contains only work relevant to the next L3
turn. Archived tasks and incident history remain available as audit evidence without being loaded
into L3 context.

## Isolation and landing

Each task uses the isolated worktree path `.claude/worktrees/<slug>` and branch `worktree-<slug>`,
based on the exact fetched `origin/main`. Commits require the task provenance trailer. Protected
branches cannot be updated outside the guarded landing path. The trusted landing code validates the
staging lease and repository, fetches the base, commits, pushes, opens the PR, pins the current
base/head pair, waits for configured checks, and merges only
when requested and allowed. A task may carry an explicit merge hold for Burak review.
The lease limits which changes can be staged. Parallel tasks may edit shared paths; their briefs
name those paths and ask owners to rebase onto main before landing and keep shared-doc edits to
their own sections. If main moves, the owner runs `git rebase origin/main` in the task worktree;
an unresolved conflict is an ordinary `alt task block` to L3, never a system fault. Landing does
not resolve conflicts automatically.

Operator authority also travels through a recorded task reply. L3's project-bound
`alt task hold-merge <slug> --approval <message-id> --pr-number <number> --head <sha> --reason <reason>`
executes directly in altd. The daemon reads the checkout-origin PR, then validates and releases the
hold under the project lock. The latest operator conversation message must be exactly `Good to merge`,
directly following an L2 message containing that PR's canonical URL and no other PR URL. Worker and
coordinator text cannot supply operator authority. The current hold generation must precede that
presentation; GitHub's PR update timestamp must also precede it. A renewed hold, later operator
message, missing evidence, or any later PR update refuses release. GitHub must report an open,
non-draft, same-repository PR targeting main, with the task's branch and the caller's observed head.

The hold generation is its latest `hold-merge` event, or the creation event for an initial hold;
hold changes and their events serialize under the same lock. Each hold change stores a fresh
`hold_merge_id` with its state and event; a write interrupted before its matching event refuses
approval even when the reason repeats. Approval validation reads events
strictly, so corrupt evidence cannot hide a later hold. One atomic task write clears `hold_merge`
and stores `merge_approval`, recording the actual coordinator actor, operator message, presentation,
prior hold generation, PR URL/head and reason. A `release-merge` event carries the same receipt;
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

Dirty-checkout recovery uses `alt task preserve-checkout <slug> --reason "…"`, a durable daemon
request available to the operator and the project's L3 for an unlaunched blocked task. Under the
publication and project locks, altd requires dirty main exactly at fetched `origin/main`, then
preserves tracked/index and untracked changes with `git stash push --include-untracked`. The
request ID identifies its stash even when another worktree changes the shared stash stack. The
task's `preserved_checkout` and `checkout-preserved` event retain its immutable commit ID, actor
and reason, including when Git saves the stash but fails during cleanup. Ignored files stay in
place. Submodule or nested-repository dirt can keep the checkout blocked. Altd never drops the
stash, commits the edits, or resumes the task as part of preservation. An interrupted executing
request refuses replay and names its stash marker for inspection. The task owner applies the
snapshot in its own worktree, reviews its lease and publication scope, and uses the normal PR path.
The [recovery procedure](CLI.md#dirty-checkout-recovery) requires a separate resume after the
checkout passes the guard. Workerless `main-unpushed` tasks retain their fault and blocked reason
when a resume still fails that guard; a failed message wake leaves the inbox intact and does not
retry until another wake is requested.

Every worker is an untrusted process in its worktree, whichever engine runs it. Its only door into
Altitude is the `alt` CLI; the backend validates each command against the task record under the
project lock. Claude Code runs as a foreground CLI inside an independent transient unit with Altitude's
hooks for inbox delivery and telemetry. Codex keeps its native workspace-write sandbox inside the same
unit boundary and uses the same door; private worker records and output identify both engines' sessions
after restart. A turn that ends without a
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
`alt issue new --title "…" [--label …] -` and `alt issue comment <number> -` publish stdin through altd's login to the checkout-origin repository for L3 or the operator, refuse L2 and private evidence references under the CLAUDE.md boundary, and record one project event with actor, title, and URL.
`alt issue close <number> --reason completed|not-planned` uses the same boundary for operator-requested
closure; L3 does not close backlog autonomously. The CLI and coordinator share an exact parser; the
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
with backoff; a usage-window stop starts a fresh attempt on an eligible configured alternative from
the task's `progress.md`, or parks the task until its window reopens when no alternative is eligible
or it is explicitly pinned; each writes one task
event. A Claude usage-window stop is recorded once for the machine, because the subscription is
machine-wide; Codex reports its limits per turn. Tier two is L3: whatever remains blocks only its own
task, files private incident evidence (one incident per source project and fault kind per 24-hour
window), and leaves an FYI and one message in that same project's L3 queue; a repeat of that kind
blocking another task in the project adds one line for L3, not a new incident. The machine fault
ledger keys records by the JSON-encoded pair `[project, kind]` (`null` for a projectless fault);
incident references include their owning project. Unscoped historical records remain evidence and do not suppress
notifications. Machine faults without a project notify registered `altitude`; when it is absent,
they only update the machine fault ledger. An L2 that meets an environment fault (a sandbox, host, or tool refusing
what the brief requires) reports it with `alt task block --fault` and takes the same path, so the
cause reaches L3 instead of sitting on Needs you as a question for the operator. The server delivers that message as a turn when L3 is free and an engine
is available; L3 records the learning on the incident and fixes the cause directly or creates one
ordinary task. An incident raised by that repair task (`--source recovery`) stays in the project's inbox instead
of waking L3 again. A task blocked before any launch goes back to the queue when it is resumed.
Incident records are evidence only and never create tasks, personas, or follow-up work.
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
and records only actor, title and URL in an `issue-upstream` event in the calling project. It reads no
incidents or conversations, transfers no fault evidence, queues no L3 messages and creates no tasks.
The reporting project's L3 does not repair Altitude; Altitude's operator/coordinator selects any
implementation separately. There is no automatic intake from issues.

A merged Altitude change marks activation pending when the self-deploy fast-forward brings in loaded
backend paths (`altitude/`, `bin/`, `systemd/`) or tracked inputs to the served web bundle
(`web/src/`, `web/design/tokens.css`, `web/index.html`, `web/package.json`, `web/pnpm-lock.yaml`,
`web/tsconfig.json`, `web/vite.config.ts`), whether the fast-forward runs after a task lands or at the
next dispatch, or the regular thirty-second daemon tick discovers a merge while its worker still runs.
Web docs, design boards, the unused npm lockfile, and other non-build files do not
trigger activation. Hooks, personas, and templates are read per use and deploy with the pull itself.

The web app's restart banner sits above the header on every route while activation is pending: it
says in words whether the backend, the web app, or both changed, how many files landed and when, and
that Altitude restarts at the next quiet moment; it names any dispatch, L3 turn or report verification
in flight. Both engines launch L2 workers in independent transient user units outside altd's cgroup;
running and blocked workers survive activation and are adopted afterwards. Each worker unit and the
service retain `KillMode=control-group`, so stopping a worker takes all its descendants. An exited or
missing worker requires a report written since its latest launch or resume; without one it blocks
with a system fault and incident, rather than replaying an older report. Dispatch continues
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
under the operator's [testing decision](SIMPLIFICATION.md#working-rules-that-still-apply-to-every-pr);
the [coverage matrix](DEVELOPMENT.md#coverage-and-limits) identifies unproven external behavior.

The same browser specs run at 390×844 with mobile user agent and touch and at 1440×900. The
smoke spec reads the real route tree and checks content, assets, console/uncaught errors, API
failures and horizontal overflow. `walkthrough.ts` drives actions, asserts visible text/roles
appearing and disappearing, and saves named screenshots. Screenshots, traces and reports stay
under ignored `web/ui-artifacts/` and are retained briefly as fictional CI artifacts. Bundled
Chromium runs headlessly with a temporary profile and its browser sandbox disabled inside the
worker sandbox. [Development and checks](DEVELOPMENT.md) documents installation, commands,
timings and CI identity; [operations](OPERATIONS.md) covers service activation and mobile access.

[Release checkpoints](RELEASING.md) select an exact validated source SHA for an explicitly
published private-preview version and release notes. They add no runtime lifecycle state and
do not gate automatic activation of merged changes. The UI and testing rules remain in the
project instructions file, which both worker personas direct the task owner to read first.

The Python server owns state transitions and JSON APIs. The React app is one shell around four
pages, specified in `design/wireframes/SPEC.md`: Needs you at `/` (every decision across projects as
compact cards in one column, answered through `POST /api/decide`), the project page at
`/projects/<name>` (the §3.2 header with its status line and overflow menu, the L3 conversation, and
the work panel), the decision page at `/projects/<name>/decisions/<slug>`, the task page, and
Monitor. `/projects` and `/chat/<name>` redirect to the project
page, and with no managed project every project route shows First run, which lists the folders under
the configured roots and starts L3 for one through `POST /api/project/add`, staying up until L3's
first reply or the error row that stands in for it. At 1024px and wider the rail is 260px and the work
panel is 340px, inline at 1280px and wider and an overlay from the header's panel button below that;
narrower is the phone: a 54px header and an 84px tab bar (Chat, Work, Needs you, Monitor), where
the header names the selected project and opens the switcher sheet, and a task or decision page
pushes over its tab with a back control. Those widths are named once, in `web/src/shell/breakpoints.ts`. The
selected project is browser state under `localStorage`, set by the rail, the switcher, a project
route, or a Needs you card; the theme (light by default, dark on request) persists the same way. The
rail's engine readout renders `GET /api/overview` `engines[]`, one row per configured engine with the
display name the engine seam gives, so the web code names no provider; the same read carries the
scan roots First run names and the operator's configured name. `POST /api/l3/start` runs the start
turn for a managed project whose L3 never ran, from the header's Start L3. The conversation is the only
way to create a task from the web: the L3 turn creates it through `alt task new`, and altd records
the slug on that turn's assistant row (`tasks: [slug]`), which the conversation renders as a task card
under the reply. The composer's engine pill pins the project's L3 to one configured engine, named as
`engines[]` reports it, until set back to Auto; Auto uses project preference tiers, weekly headroom
within ties and the session continuity rule described above. A chat turn belongs to L3,
not to the page that started it: when the page leaves mid-stream, the turn finishes and its answer
lands in the history. The conversation component is keyed by project, like its query cache:
switching projects discards the draft, pending bubble, stream and composer error state. Composer
unmount stops the recorder and releases its microphone tracks through the recorder's own stream;
pending transcription is cancelled and cannot update the destination draft. Outstanding
callbacks and cache updates retain the source project. The destination renders its own history,
queue and active turn; switching back reconstructs those server records, including Retry for a
failed turn, without restoring an unsent draft. `GET /api/chat` reports the server-owned active turn as a stable id, start time,
and trigger without copying its prompt, and it is the conversation's only authority: the page polls
it and never infers a turn from `busy` or the last history row. A fresh mount or reconnect renders
the record as the typing indicator for a chat turn, or as the line "L3 is handling <what>" for a
server-triggered one; the tab that started the turn keeps its streamed reply instead. The stream's
first line names the turn (`{"turn": {id, started_at, trigger}}`) before any text, and the terminal
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
system turns between two operator messages fold to one line, "L3 handled N system events between
your messages", that expands to the list with each turn's own Show; a turn in progress reads "L3 is
handling <what>" with no Show, a failed one "L3 could not handle <what>" with its error behind Show.
A failed chat turn reads "L3 could not answer this turn." with Retry, which resends the same text.

`GET /api/project/<name>` includes `repository`, the GitHub HTTPS web URL derived from the deployment checkout's SSH or HTTPS `origin`, or `null` without a GitHub origin; the task PR chip links to `<repository>/pull/<n>` in a new tab when present and stays text otherwise.

A message sent while L3 is busy is queued, never refused: the composer stays open, the send control
keeps its arrow, the hint reads "L3 is mid-turn · runs next", and the message shows as a muted queued row with Remove until
its turn starts, when the row becomes the turn's bubble and typing indicator. The API snapshots the queue and active record under the same
lifecycle guard, so that handoff cannot appear as an idle gap. A control takes Burak's chat back off the queue only while it
waits. Server-triggered work is also visible in its FIFO position but is not editable. The queue is a
file in the project directory, so a reload, another device and a restart all see the same pending
messages. Each turn drains it at its own boundary rather than at the next tick: consecutive chat
messages fold into one turn in arrival order, each on its own line, while server-triggered messages
keep their own turn, and nothing runs while a turn holds the project's L3 lock.

The project conversation, the task page's conversation, and the decision page's follow-up use one
composer component, `web/src/components/Composer.tsx`, with no page-specific props.
The page owns its draft and its submit function, and a submit that throws is a refused send: the
bubble leaves, the draft returns, and the hint reads "Not sent. Retry." The composer owns microphone
permission, MediaRecorder state, a 595-second client stop below the server's 600-second
decoded-audio limit, transcription, cancellation, and focus. A landed transcript is appended to the
draft with the cursor at the end and nothing else appears (issue #195): existing draft text is the
prefix, separated from dictated text by one space when it does not already end in whitespace. Its
send control is an arrow in an accent circle in every state, with no visible text and an accessible
name of "Send" ("Queue" while busy). Its states are the design spec's §3.6 table (idle, typing, sending at 60%, busy queueing, listening
with a live waveform and timer, transcribing, landed, denied, unavailable, refused), each walked at
phone and desktop widths in `web/e2e/conversation.pw.ts`. Decision and reason fields remain
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
or plain dispatch), so the card never names a file lease. A task blocked waiting on L3 reads "Waits
for L3" with the running dot, and the rail's project dot counts it as running (`counts.waits_l3`);
only a decision in the queue turns either dot amber. The work panel (spec §3.7) reads the project's
tasks and the overview queue filtered to the project: the queue's decisions as compact cards under
Needs you, every other active task as a row under Active, and the tasks done or rejected in the last
seven days folded under Done this week; a task that changes section fades in where it now belongs.

A decision (spec §3.8, §3.9) is one card wherever it appears. `GET /api/overview` `queue[]` carries,
per blocked task waiting on the operator, `kind` (`asks`, `stopped`, or `fault`), `asked_by` (`l3`
for an escalation, `l2` for a block the L2 flagged for the operator), the question, labelled
`options`, the `recommendation` (the option and why), `asked`, and `since`, the time the current
block began. Until escalations carry structured fields, `tasks.parse_dilemma` reads the options and
the recommendation from the question text ("Option A:" or "A:" prefixes, "(recommended)", "I
recommend A"), and a block recorded without options offers Resume and Reject. `POST /api/decide`
takes `{project, slug, option, note}`, where `option` is the key, the label, or the index; the server
records `{key, option, note, at, by}` as the task's `decision` and a `decided` event, then rejects the
task (the note as the reason) or resumes it with a task message that names the choice and the note,
so the L2 reads the answer at its next checkpoint. The decision page reads the task's events for its
timeline and evidence; a follow-up to L3 is a chat turn posted with the decision's `slug`, which the
server stores on the turn's rows and reports on the active turn, and a follow-up to the L2 is a task
message; the card and the page mirror the rows that carry the slug or sit in the task conversation
since `since`, so nothing is copied. An FYI (`tasks.fyi`) is a chat row `{role: "system", trigger:
"fyi", slug, text}` in the project's conversation; there is no project inbox file and no `fyis` in the
digest or the overview.

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

The Monitor page reads `/api/monitor` and is display only: no hold, incident, route or follow-up
work is derived from it. `/api/monitor` answers with `seats`: one row per configured engine, in the
seam's order, as `{engine, label, quota}`, where the label is the seam's display name and the quota is
that seat's reading whole. Only the engine seam knows which reading belongs to which engine, so the
page ties no reading to an engine key and spells no provider. It sits in the shell's page container
and shows one seat card per row: either the five-hour and seven-day windows a statusline snapshot
names, or windows named by the length the seat reports, a window the seat does not report shown as
absent rather than zero — each with percent used, a meter with the 70% reserve line drawn, when it
resets in relative and clock terms, the plan where the seat names it, and how old the reading is. A
seat with no reading at all says so and carries the reading's own `why`, the one line that fixes it;
a reading older than the age the router itself trusts is stale: still shown, dimmed, and labelled. One
routing card answers which engine/model each project's L3 (its pin, or Auto) and a fresh L2 would get
for a turn started now, in `pick_engine`'s own words. It explains the selected tier, comparable or
unknown quota, continuity and skipped options; no eligible option gives an actionable installation,
authentication, reset or configuration explanation. The
sessions the monitor knows follow, each with its task, its engine and the model when the API reports
one, its context meter and the age of its snapshot; no session is one muted sentence. Loading is a
skeleton in the page's shape, and a failed read is one sentence with Retry.

The task page is the operator's conversation with the L2 beside the worker's live session
(design spec §3.10). Its header carries the crumb back to the project, the quiet Stop and Reject
actions with an inline confirm in place of any browser dialog, the title with its state dot, a muted
line (attempt, when the task started or finished, context used), and chips: the state, the model on
its engine as the engine seam reports them, the last PR with whether it merged and how the main run
concluded, and the merge-hold reason. The conversation uses the project conversation's bubble, prose,
day-divider, and composer components: the operator's rows as bubbles and the L2's and L3's rows as
prose under day dividers, the decision card at the top while the task waits on
the operator, and the composer while the task is running or blocked; a block waiting on L3 and a
fault each read as one line under the chips, the fault in red with "L3 has been told". The live
session panel is 480px inline at 1280px and wider and an overlay from the header's panel button
below that; it reads the worker's own session log (Claude's session JSONL, or every turn of the
Codex thread) together with Altitude's task events as one transcript: tinted prompt blocks, the
worker's prose, each tool call as one compact row with its output folded under it, task boundaries
as thin separators with subtle timestamps, hidden reasoning never shown, and Raw events behind a
toggle for the complete redacted records, the task's other operational events among them. A queued
task shows what it waits for in place of the session, a finished one says the session ended, and a
missing session file says so. On a phone the header carries Back and the title, a state line holds
the chips and the actions, and two tabs, Conversation and Live session, switch the content (`/live`
selects the second) with the composer pinned above the tab bar. A done or rejected task is
read-only with the composer gone.

Runtime files live under `ALTITUDE_HOME`; a task is a directory a person can read. Source-controlled
personas, schemas, templates, and hooks describe current behaviour: `hooks/` holds the Git hooks
that `git_policy` installs into every managed repository, the Claude inbox hook, and the statusline
monitor. [SIMPLIFICATION.md](SIMPLIFICATION.md) records why the system has this shape.
