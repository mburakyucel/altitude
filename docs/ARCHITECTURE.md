# Altitude architecture

## Container deployment boundary

The Mac actual-image browser validation entrypoint is the fixed `alt task validate --container-browser`
lane. The deployed controller owns a bounded throwaway Ubuntu guest under the task's standing VM
policy grant; candidate host commands are never accepted. Online rootless builders receive candidate
build inputs under a filter denying Mac/private addresses and IPv6; candidate guest administration
starts only after physical network cutoff. A non-root rootless browser container has no network interface or guest
administration credentials: bounded local fixture-control IPC and a fixed raw HTTPS socket relay
connect it to fictional application containers. Physical guest NAT cutoff, inspected container
configuration and a finite Chromium sandbox preflight establish the execution boundary. Validation's
existing queue, hour limit, disconnect/replacement cleanup and activation fence own that entire
lifetime. [Container browser validation](CONTAINERS.md#container-browser-validation) defines its
resource, identity and native evidence requirements. The Linux KVM browser route remains separate.

The host launcher checks that Podman's runtime bus resolves to the local user's owned bus socket.
Disposable fixture runtimes retain private storage/process metadata and link only that same user
bus into their runtime directory. The host controller selects cgroupfs and distribution crun;
its system-bus address names an unavailable fictional endpoint. A delegated user service owns each
application lifetime. Disposable VM acceptance independently observes service-manager calls and
their senders, including forced cleanup. It allows systemd's verified same-user delegated process
attachment, which does not use polkit, and fails on unexpected management attempts. This is
validation of the selected runtime clients, not hostile-process confinement. The
[container evidence](CONTAINERS.md#evidence) records the tested boundaries.

On a Mac the same launcher drives Altitude's own rootless Podman machine through Podman's
connection, and Podman inside that machine owns each container's lifetime; there is no Mac
supervisor or launchd job. Before its first Podman call a launcher process proves that the
`altitude` connection reaches that running rootless arm64 machine. Limits and recreation emptiness
are read from the container's scope inside the machine; a transfer's foreground client owns it and
its record binds to the machine's incarnation. [On a Mac](CONTAINERS.md#on-a-mac-podman-machine)
describes the route and its gaps.

The [container candidate](CONTAINERS.md) retains the user-manager and independent worker contract
inside a rootless Linux image. `platform.py` reads image identity outside writable volumes and owns
runtime/bootstrap operations. Its protected image-path check recognizes root ownership through a
worker's single-user namespace without accepting worker-writable identity. Marker and instance use
the same check; invalid identity fails closed. Container editing guidance uses bundled Python through
confined exec on launch and resume. Browser terminal, host speech and native activation/update routes
refuse every peer in that deployment. Source merges remain ordinary project delivery; application
activation uses image replacement. Bootstrap locks both persistent volumes before starting the user
manager. Immutable resources and persistent home/projects have separate lifetimes. The reusable
Linux image/launcher lanes establish startup, published local HTTPS, elevation-file inventory,
the native diagnostic permission matrix, restart/replacement/descendant cleanup and private
backup/restore. [Container evidence](CONTAINERS.md#evidence) names the tested runtime and limits.
The actual daemon's phone/desktop onboarding and queued task continuation pass with fictional
external engines. Provider-session confinement parity, real-device access and Mac runtime evidence
remain pending. Resume-claim ownership
uses platform process lifetime, boot and PID-namespace evidence instead of a bare PID check.
Container admission has one persistent receipt and a root-owned container-instance identity.
The platform seam serializes receipt changes and admitted-call leases; the config/engine seams
gate every provider launch, with early checks before queue/claim consumption. A replacement waits
for host-side Continue. Same-container restarts retain admission. Existing task holds, Stop and
reconciliation remain independent of this global gate. The read-only browser notice uses the
existing overview poll; it adds no retained browser state or host command route.

Altitude keeps a persistent project-level conversation with L3, the project's orchestrator. L3
discusses direction, architecture and priorities, dispatches directly reachable L2 task owners,
and receives their questions and reports needing follow-up. Several tasks can proceed in isolated
worktrees while that conversation continues. Model judgment favors small, safely mergeable increments
for large or complex issues when practical; code enforces task ownership, isolation, launch holds, and
the PR boundary. L3 maintains the issue breakdown from L2 replies and reports. Briefs name the increment,
acceptance, parent issue and remaining scope; completing a task need not complete that issue. See
[incremental delivery](CLI.md#incremental-issue-delivery).

```text
The operator
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
cannot launch subagents directly. L2 and L3 share one project-read command set in `bin/alt` for task
records, repository/PR evidence, history search, tool summaries and incidents. Mutation admission stays
role-specific. Owner repository, PR and history/tool reads require the launch project; global queue,
decisions, monitor and state views retain their existing admission. L3 turns persist bounded shell
command text with their tool evidence. Its process is read-only on the deployment
checkout on either engine; source changes always belong to one L2 worktree and PR.

The [L3 persona](../personas/l3.md) owns roadmap sequencing, targeted handoffs, durable feedback,
capability-gap recommendations and authorized continuation. L3 judges who needs context and when
based on its effect on their responsibilities, decisions or work. Active follow-through is the
default for unfinished authorized work: L3 checks ownership, readiness and dependencies, moves
ready delivery through its owner and continues authorized dependent work through supported operations.
Significant stalls receive a visible project-chat heads-up naming why and the next step/trigger
or exact missing operator action; legitimate waits and running workers need no repeated nudges.
Its task briefs convey the actual problem, intended outcome, acceptance and material project context;
brainstorming stays distinct from requirements. The [L2 persona](../personas/l2.md) owns investigation,
approach, relevant system implications, source assessment, questions and verified delivery within that outcome.
These responsibilities use existing task operations, with no broadcast, runtime classifier or
memory store. Project-local authority, proposal checkpoints, merge holds and verified recovery
remain governed by the existing boundaries.

L2 receives the request, repository context, expected files, worktree, branch, and merge policy, and chooses
the lightest useful execution shape. Its conversation with the operator is stored apart from tool logs, so
the operator messages it directly without routing through L3. Messages queue on the task and reach the
worker at its next checkpoint. A Claude owner's hook checkpoint names the operator's own messages, which reach
it only as a user turn, at the next session turn; a Codex owner's driver writes waiting messages into its
running turn as they arrive, with the exceptions [message delivery](#message-delivery-and-voice) names. An explicit Stop ends a worker. **Send now** on an inbox-owned operator
message hands the queued operator group to the running turn in arrival order, without stopping its
work (see [message delivery](#message-delivery-and-voice)). Each message keeps its own visible row and
delivery receipt; messages arriving after the claim wait for a later checkpoint. Task message writers hold the project
lock and atomically replace each conversation or inbox file, so concurrent readers see complete records.
Appending a message to a blocked
task also persists a due `resume_after` request, except non-waking coordinator discussion on a
faulted task and messages held by Stop. Stop records its identity when accepted, before termination,
so old or racing sends cannot restart the session. A correction must name the confirmed Stop it
observed; explicit Continue releases the same held inbox. An L3 CLI process stops there: altd coalesces that
request with timer and capacity-available wakes, then owns Git isolation validation and provider relaunch. A
durable resume claim fences competing wakes, holds service restart, and records the exact inbox batch and
replacement worker so a restarted daemon adopts rather than launches it again.
An unavailable worker-unit identity or status stays an unknown observation during polling.
Reconciliation retains the running task and its capacity reservation, records a deduplicated incident
for L3, and continues other tick work. Unknown status authorizes neither completion nor relaunch or
an unnamed stop; existing claims, sessions, inboxes and holds remain intact. Engine ownership and
platform termination checks remain the authority for recovery.
An inbox-owned operator message offers Remove until the exact batch is claimed. Cancellation uses
the same project lock as resume and hook pickup, records removal in the existing message delivery
metadata, and excludes only that ID from pending input. Original text stays in conversation evidence;
the message leaves the task conversation, and CLI history/search retain an explicit removal marker. Removed text
cannot serve as a new decision source. Quick choices and messages
already cited by recorded decisions remain intact. Removal changes no Stop, question, fault or resume
request. Claimed messages show faded and cannot be removed; a launch attempt retains
per-message uncertainty through failed-launch recovery. A released prelaunch claim becomes removable
again. Successful input handoff and matching session initialization record delivery for the exact
bound batch; native hook attachments can independently prove delivery.
Inbox absence alone stays unconfirmed. Reading an inbox concurrently consumed by a resume sees an
empty queue, without turning that absence into delivery proof. A clean worker turn with queued steering resumes the saved
session, as does a clean exit after the hook ended the turn for operator messages that were then removed.
A clean exit with only a message and nothing queued resumes once with a system nudge to block or report;
the next consecutive one is a dead-worker fault,
while engine failures and explicit question blocks retain their existing recovery paths.
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
`alt task handoff <slug> --engine <engine> --attempt <N> --reason '…'` uses this same coordinator
transport and daemon request for a running owner, an operator/L3-stopped owner, or one blocked by
worker death, a recognized usage limit or an engine refusal, or an unanswered question whose owner has exited without a recovery fault.
The running owner receives a native instruction to refresh
`progress.md` and write a request/attempt/worker-bound readiness file; no updated owner CLI is required.
The task page invokes that same request from its composer model button, which a usage-limit or refusal stop's
**Change model…** opens, with an atomic fence
on the observed attempt, worker/session and block. A conversation card exposes now/cancel while
preparing. Confirmation retains the observed identity even if polling sees a replacement; uncertain
responses require a status read, not a duplicate mutation. Task polling derives target eligibility
from the routing seam, and concise conversation outcomes retain full native checkpoint instructions
for the old worker. No browser storage or parallel transfer lifecycle is added.
The daemon waits without a timeout. Confirmed exit, a recorded usage limit or refusal, existing Stop or explicit
`--now` permits transfer using saved context. `--request <id> --cancel` keeps the current owner until
the stopping phase commits. Neither silence nor network failure forces a switch. The entire worker
job must be confirmed stopped before requeue. The durable request fences the observed attempt,
worker/session and block, refuses active claims/reviews and explicit pins, and retains its receipt
across restart. The requeue is saved just before that receipt; a task read in between shows the switch
done, by the rule the daemon finishes it with, so the page keeps its composer and any recording. Its `next_engine` confines the next launch to that
engine's configured options and is consumed when dispatch binds the fresh attempt. Pins and
availability are checked again before execution and launch. `route.pick_task` supplies the same
target-aware availability and pin explanation to dispatch and the queue. A fresh attempt clears current
verification while retaining prior reports and delivery history. Handoff ignores new-task model choices
and reports whether a fresh handoff note was obtained. The task composer extends this same receipt
with an explicit model/effort selection, including same-engine changes. Admission checks the displayed
default resolution and rejects an unchanged effective tuple. Requeue retains that selection for one
attempt; dispatch consumes it without turning it into a permanent policy pin. Explicit task/project
pins and unavailable targets still refuse. Ordinary resume remains unchanged.
The attempt number fences every L2 command to the current attempt: an L2 may reply, block, complete,
resolve a dilemma against its source message, and land only its own task.

Helpers are engine-native. L2 delegates bounded assignments and remains accountable for their results;
Altitude does not supervise helpers. The shared [L1 persona](../personas/l1.md) owns helper responsibilities.
Each L2 launch/resume supplies its activated absolute path for an explicit read instruction in every
native helper assignment. L2 adds context, allowed actions, exclusions and expected evidence, then
verifies the result. Repository discovery does not select the role. No helper registry or engine
configuration file is generated; [delivery and inheritance limits](SESSION_LIFECYCLE.md#native-helper-instructions)
describe both integrations. L2 retains its small `progress.md` checkpoint (goal, done, next, how to verify).

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
up to 20 human user/assistant rows, selected by the same trigger filter from the latest 60 human rows,
handled by a different provider after that session's `last_turn`. Fresh context and resumed handoff are mutually exclusive.

`alt l3 search` provides on-demand historical evidence through the same project-bound coordinator
transport and CLI role gate. `l3.search` scans existing human `chat.jsonl` rows, the shared
`tasks.task_messages` projection (including status-backed decision messages), and active/archived
`report.json` string values and `digest.md`. A record matches when it contains every word of the
query in any order, ignoring case and punctuation. Search retains the newest bounded matches and
returns original text with immediate neighbors, dates, roles/recorded authors and logical source
references that survive task archival. Report/digest authors are unknown
where unrecorded; their dates explicitly mean file modification, never decision time. Resolved
evidence paths stay within the selected project. No engine transcripts, generated summaries, index,
external service, model call or state mutation participate.

Search scans the full local corpus rather than a recent-message slice; its cost grows with that
corpus and the existing broker timeout applies. Results default to five, cap at twenty, retain at
most 1,200 characters per excerpt, and fit within 64 KiB of serialized output. Count/output omission
and character offsets disclose incomplete context; corrupt/unreadable evidence fails explicitly.
Directories without a resolvable status record contribute no task evidence and produce `partial`
results with an unavailable-task count and up to twenty logical task references. Available chat and
valid task evidence remain searchable; records are neither repaired nor inferred from stray files.
Empty corpora and absent optional files produce `no_results` only when no task evidence is unavailable.
Every L3 turn advertises lookup, including native
resumes and engine changes. Historical evidence is not new authority: current instructions and task
records govern, and the coordinator checks temporary conditions and later corrections before action.
See [source references and bounds](CLI.md#historical-evidence-search).

Every L3 turn's header also names Altitude's documentation folder (`docs=`): `docs/` of the activated
source or the installed release, which ships it. The coordinator answers how Altitude works from it
for any project. A Claude turn adds that folder beside the project's repository and the Altitude home
as a readable directory, and the Codex profile names it readable; neither engine can write it.

Tasks and L3 session records carry `engine_model` and `engine_reasoning_effort`. The engine adapter
reads Codex's rollout `turn_context` after the thread starts, recording the actual model and effort
for the current turn. altd reads the provider home; the sandboxed worker does not. Missing or delayed
rollouts leave the observation unknown and are retried while the turn runs. `engine_model` holds only
what the engine reported; the launch model and effort are kept separately and are what resumes ask for. Status, task
header chips, and the Monitor API expose the observation; old records remain readable.
`GET /api/task/<project>/<slug>` returns `engine_model` and `engine_reasoning_effort`;
a missing task returns HTTP 404 without a failure traceback. Archival only renames the live task
folder into the archive, so a status read that misses the live record reads the archived one; polls
and action responses crossing the move return the archived task and state; an event read reads
the archived log the same way, so the overview walking every task survives an archive mid-walk.
Task status, documents and events share the archive lock while their snapshot is read.
`GET /api/monitor` and `alt monitor` list only sessions Altitude runs: each project's L3 and its
running, blocked or reported L2 tasks and its running agents; a paired browser's `GET /api/monitor` also lists its
answering [agent chats](#agent-chats). Other sessions on the machine are not read. Session rows expose `model` beside `engine`, with `engine_reasoning_effort`
when available. An unknown Monitor model is an absent key rather than null.

The configuration seam resolves model and effort through the existing routing paths. Explicit
task/turn requests come first, then the role's choice (the project's coordinator choice or the
installation's New tasks choice) and routing models, then the project's own per-engine role
settings, then built-in role/provider defaults. Native helper settings and specialized improvement
review policy retain their existing scope. Engine choice is independent: Only-engine routing and
task/turn pins remain strict.

The coordinator, New tasks and task model buttons are the only model and effort editors
(`ModelMenu.tsx`, [model choices](CLI.md#model-choices)). The coordinator and New tasks buttons
save their choice through `POST /api/defaults` (`l3_choice`) and `POST /api/new-tasks`, each with
the `expected` value shown, so a stale write refuses instead of overwriting another window; the
task button drafts a selection and confirms it as an explicit handoff. No browser model or effort
cache is added. `GET /api/project-model-settings` lists each project's own per-engine model and
effort and its explicit routing models; `POST /api/project-model-settings/reset` clears one
editable row using `{id, expected}`. Settings shows these under **Project settings** with
**Reset**, only when any exist; routing models link to routing details, and custom tiers remain
editable with the `--routing` CLI option. Individual task pins remain outside this reset. Project
Settings exposes task routing and what the coordinator's last reply reported.
The CLI retains reason-bearing project settings requests; these apply on a daemon tick, independently
of worker capacity. A conflicting pending request refuses a save until applied.

Fresh task dispatch records resolved `launch_model` and `launch_effort`. Message/resume keeps those
saved values without re-resolving choices or project settings or copying observed provider values. Every role has
a built-in model on each engine, and every Codex launch passes its model and effort explicitly.
L3 resolves each turn, including same-conversation resumes; a running turn keeps its launch choice.
Inherited effort retains built-in behavior: native on Claude, High for Codex tasks and Medium
for Codex chat/reviews. Ordinary reviews use the chat role's effort rather than inheriting project
task effort overrides. Explicit `native` passes no
effort to Claude and Medium to Codex. Unsupported explicit efforts refuse; Auto
excludes engines that cannot accept an explicit level. Provider caps and model compatibility remain
native decisions; effort failures do not cause application-side downgrading.

Named model shortcuts are requests, not account discovery or verified entitlement. Unknown access
stays unverified; known rejections retain the saved request and explain availability. Shared quota
never establishes model access. Last-reply/task reported model and effort remain separate observations;
missing reports stay unknown. Changing defaults updates no CLI configuration and migrates no session.

Global role responsibilities live in the personas; project policy lives in the repository's
instructions, with [AGENTS.md](../AGENTS.md) authoritative for Altitude. The shared engine boundary
names the repository rule file each turn; [instruction loading](SESSION_LIFECYCLE.md#repository-instructions)
describes selection, imports and activation limits. Brief boundary excerpts use the task worktree's
rule file. Managed projects retain their own policy.

Source deployments export their committed installation HEAD on successful service startup into the ignored
deployment-local `.altitude-source/<sha>` directory. Task workers write the checkout's shared Git store, so
Altitude trusts its own code only through its copy of the repository beside its home
(`~/.altitude-repository`), a bare repository only altd writes. The copy is seeded once from the checkout's
`origin/<branch>` and afterwards fetched from the origin. The service starts, exports and self-deploys only
when the checkout's files are exactly a commit on the copy's branch, compared by content with the copy's
objects; self-deploy fast-forwards the checkout to the copy's fetched tip and checks it again. The guard lets
the branch move only to the checkout's cached `origin/<branch>`, and two fetches straddling a fresh merge can see
different tips, so self-deploy fetches the checkout once more when its origin ref differs from the copy's tip and
otherwise leaves the step to the next tick as a failed fetch, as it does when another fetch moves that ref on before
the branch moves. The fast-forward rebuilds the checkout's index from the verified commit, moves the branch through
the guard and only then writes the files, without another refusal; a refused move leaves the branch and files as
they were. `config.REPO` identifies the deployment checkout;
`config.SOURCE` identifies the activated source for CLI code, personas, hooks, templates and schemas.
These exports sit outside worker writable roots. The maintenance tick removes an export nothing names:
the running service's export, the target of `current` and every export named in the brief or session
settings of a task that is unfinished or not yet cleaned up stay, so live workers keep the export their persona,
driver and launch paths name; their `alt` and hooks run the activated export ([task door](#task-door)). Pruning
waits an hour after `current` moves, while the restart helper may still run from the previous export,
and skips a pass when a task's launch files cannot be read. Managed
Git guards use `.altitude-source/current/hooks`; setup and launch preflight refresh recognized
Altitude-owned paths from earlier exports, including skipped versions, and preserve custom hooks.
Task inputs use activated source independently of
uncommitted deployment files or newer code awaiting activation.
An unavailable project guard update reports a project fault without stopping service startup;
that project's launch waits for trusted guards while other projects continue.

Private archive installations use verified immutable `versions/<version>` trees and a `current`
link under `~/.local/share/altitude`. `release.json` binds the packaged CLI, daemon, built UI and
launch resources to a source commit and file hashes; no application Git checkout is required.
`config.SOURCE` pins the selected version for workers, while managed guards use installation-owned
`hooks` launchers that invoke `current/hooks` with the saved Python and configuration.
Consented custom-hook compositions and their receipts live in installation-owned `git-guards`,
outside immutable version trees; their launchers use the saved Python and `current` code.
Previous versions remain available to existing workers. Installation configuration lives in
`~/.config/altitude/install.json`, apart from runtime state and project worktrees. Explicit
CLI environment settings override saved values. The generated service pins its saved settings,
including binding and runtime paths, against ambient user-manager values. Source deployments ignore installed configuration.
New installation settings capture the discovered toolchain PATH, including a custom nvm default;
updates retain the saved environment.
`installation.py` owns archive validation, activation receipts, recovery and retention.
After a successful fresh activation and CA-verified readiness probe, it issues the first-browser
link through the existing access-code writer. Eligibility requires no previous application version,
no paired devices and no unexpired pairing code; device/code checks and issuance share the access
lock with manual issuance and redemption. A retained manual code stays intact. Reruns, updates,
rollback and recovery issue no code and retain access. Issuance failure reports the running
installation with manual recovery through `~/.local/bin/alt pair`. The successful installer result
and terminal output carry the link; configuration, activation/update receipts and diagnostics do
not retain its raw code. The shell's private result file is removed by its existing exit cleanup.
Trust guidance and the link lead the output; PATH setup and `alt doctor` are optional follow-up.
The installer launches no browser. In a terminal on the hosting computer, not over SSH, and for
Altitude's own CA, it runs `alt trust` before printing the link, which changes trust only after a yes
typed in that terminal. Terminal/OS link handlers open a browser on the viewing device, so an SSH link
using loopback refers to that device.

`platform.py` is the platform seam: it owns the generated per-user daemon service, the jobs that run
workers, reviews, machine commands, updates and restarts, their status and stop, resource limits, and the
process and socket facts behind worker identity, terminal Stop and the terminal's agent check. On Linux
the service is a systemd user unit and each job a transient user unit whose cgroup holds every
descendant. On macOS 15 or newer the service is a LaunchAgent in the user's `gui` domain
(`~/Library/LaunchAgents/dev.altitude.altd.plist`, logging to `~/Library/Logs/altitude/`), and each job
is its own launchd job, so its processes share a kernel coalition that no `setsid`, double fork or
cleared environment leaves. The job's program is a supervisor outside any sandbox: it runs the command,
enforces the time limit, and when the command exits stops whatever remains in the coalition, records the
status and removes its own job; while the command runs it holds an idle-sleep assertion (a closed lid
still sleeps). A caller's input and output reach the job as `systemd-run --pipe` passes
them: a regular file or device by path, output pipes through relayed FIFOs, and piped input (always a
whole prompt) through a private file that the supervisor removes once read and hands to the command as a
pipe, so a line one reader has taken cannot be read again. Stop signals every coalition member, checking each one's start time and
coalition again right before the signal since macOS has no process handle, until none is left. A member
that outlives the stop, or cannot be read, keeps the job's record, so the job stays active, as a unit whose
processes have not ended does, until they have gone. The seam
reads processes and sockets through libproc and sysctl, finds Homebrew's libraries, and replaces
`RLIMIT_AS`, which macOS rejects, with a watcher that kills a command past its memory footprint. The L3
service-inspection shims remain Linux-specific outside it, and `platform.source_service()` keeps the
source-checkout TLS drop-in to Linux, refusing it on macOS with an explicit message (a source service restarts
itself on both hosts through the seam);
`tests/test_project_layers.py` keeps that count from rising. A pending installation
receipt fences new work through the existing restart admission check until activation or recovery succeeds. Worker authority and
containment remain in the common engine contract. macOS runtime acceptance on a spare account
([roadmap](ROADMAP.md#native-macos-runtime)) remains pending.

An installed copy's daemon checks for a newer release at startup and every twelve hours, off the
timer thread: one anonymous request to GitHub for the repository in `release.json`, retried after
an hour when offline. A stable installation reads GitHub's latest release, so it follows stable
releases only; an installation from a release candidate reads the thirty newest releases and also
follows newer candidates. Drafts never count. `update.json` in the runtime home keeps the newest followed release found and
the latest update attempt and versions already attempted automatically. The daemon, the app's
request and the updater change this record under one lock; source deployments and containers
neither check nor record. The `update_check` machine setting turns the check off and hides what it found. The
overview's `update` field and `alt doctor` report the installed version and a newer one; an
interactive `alt` command prints one line about it at most once a day from that record, never to
agents. `POST /api/update` accepts only the exact newer version the record shows and starts
`alt update --version` in a job of its own (`platform.detach`), so the update outlives
the service restart it causes; it passes the operator terminal's request checks, and Altitude has
no login, so anyone who can open the page can start an update to that verified release, never a
downgrade or another build.

The `update_automatic` machine setting defaults to true. From the moment a release waits for its
automatic attempt, `config.activation_pending` is true, as for merged changes on a source install,
so new validation, adversarial review and improvement runs wait and those in flight drain. An open
browser terminal defers the update and lifts that fence until it closes. Once no terminal is open
and none of those runs is in flight, the daemon reserves the offered version under the terminal
registry's lock, so no terminal opens before the reservation refuses new ones, and starts the same
detached updater. The reservation fences new dispatch/resume work, L3 turns, report verification
and terminal creation; running workers continue. The detached updater waits up to
`UPDATE_DRAIN_SECONDS` for the exclusive restart gate, longer than an L3 turn's limit, so it does not
depend on a tick finding an instant with nothing in flight. Each tick records what the
offered version waits for in `update.json`, and `update_status` returns it as `waiting_for` for the
notice, Settings and `alt doctor`. Turning automatic updates off restores the confirm-and-update flow;
turning checks off also prevents automatic starts, without changing the saved automatic preference.
Neither setting cancels an admitted update. Sources and containers retain their existing lifecycle.

The installation lock covers the entire manual or detached update, including download and recovery.
A unique attempt identity reserves the interval before the detached child acquires that lock;
expired children cannot run or finish a later request. Each release gets one automatic attempt,
retained in `update.json`; explicit retries remain available. Failed activation restores the prior
version, or keeps the recovery receipt if recovery cannot finish. Reconciliation marks a reservation
failed when the current installed release fails verification, without changing its files or reporting
an upgrade. Other abandoned reservations become failed after thirty minutes only while the
installation lock is free and no activation receipt is pending. Age in the UI never releases a
live updater's fence. The app announces a successful automatic update with its installed version
and release notes after reconnecting.
A failed attempt records its cause, the failure's first line with the home folder shortened to `~`,
which the notice, Settings → About and `alt doctor` show beside Try again and Repair; an attempt
that stopped without one says "the updater stopped without saying why." The attempt also records
whether its cause passes on its own (a timeout, a refused or reset connection, a cut-off download, a
server error or rate limit; never a certificate failure) and whether it repeated a failed attempt for
the same release: the notice leads with Try again only for a passing cause on a first attempt, and
with Repair otherwise. Tracebacks stay in the update unit's log or the terminal. Detached updater failures settle their own reservation even when
preflight fails before activation.

Release verification checks every declared file's hash and refuses undeclared files. A downloaded or
built archive must match its manifest exactly. An installed release tree also accepts bytecode that an
interpreter generated for its declared Python modules (`<dir>/__pycache__/<module>.<tag>[.opt-N].pyc`,
and the temporary name Python writes before renaming it), because any process importing from the tree
without `-B` writes some, and `-I` ignores `PYTHONDONTWRITEBYTECODE` (#1029). Update, reconciliation,
recovery, service start and address changes therefore never fail on, move or repair such caches;
anything else extra, missing or changed in an installed tree still fails verification.

`source_tls.py` prepares an existing Linux source service for explicit TLS configuration.
The operator selects its existing certificate directory; native unit/process/listener evidence and
a verified HTTPS handshake bind that selection to the running source deployment. Check-only shows
the fixed TLS-directory override. Explicit apply writes only that owned drop-in, reloads the unit
definition and verifies the unchanged process and identity; failed verification restores the owned
override or reports unconfirmed recovery. The verified archive installer exposes the same operation
before installation. It adds no L2/L3 service authority, daemon endpoint or certificate migration.
Reload verification compares the configured executable/arguments and live PID, invocation and
main-start timestamp; resettable command-history metadata is not process identity. Failure messages
name changed fields and preserve both apply and recovery errors without exposing environment values.

Fresh defaults are HTTPS on `127.0.0.1:8890`. `tls.py` generates one installation-local CA, named
"Altitude CA" with a random four-character label so devices can tell installations apart, and a
server certificate in `~/.config/altitude/tls`, outside runtime/source/project writable roots,
with private directories and keys. A generated CA carries critical name constraints permitting only
loopback, private-network ranges, private names and the subtree of a DNS name configured at creation,
so device trust extends to no other public site. Existing unconstrained or external CAs keep their scope. Startup validates identity and reissues a managed server certificate
whose host no longer matches the bind address; the existing daily timer renews managed server
certificates within thirty days of expiry, retaining the CA/key.
`platform.openssl()` selects the TLS and notification-signing tool: OpenSSL on PATH on Linux;
OpenSSL 3 or newer on PATH or Homebrew's standard `openssl@3` keg locations on macOS. The bootstrap
checks the same locations before downloading; the verified Python installer checks again before
persistent installation changes. Startup, renewal and `alt doctor` use the same discovery with the
saved service PATH, without changing shell profiles or that PATH.
Invalid TLS refuses startup or reports renewal failure without switching to HTTP. Each connection
completes its handshake on its own request thread within ten seconds, so a stalled client drops
only itself and never delays other requests. External
certificates are validated without replacement. Browser/device trust stays explicitly unknown
until the user trusts the public CA and verifies it; `alt doctor`, the installer and Settings › Devices
report the CA's name, SHA-256 fingerprint, expiry and scope, read from the certificate's own name
constraints ("No limits" when it has none), with the URL and per-platform trust steps. A desktop or phone gets
the public CA through a share window (`tls.Share`): for ten minutes it answers plain HTTP on the
service's non-loopback address and an ephemeral port with a guided page, an iPhone configuration
profile whose only payload is the CA certificate (`com.apple.security.root`, identifiers derived
from the fingerprint so reinstalling replaces it), and the `ca.crt` bytes; nothing else, with no
Altitude API or page. It closes at its deadline or on demand and cuts any open connection. The
channel is unauthenticated, so the trusted screen that opened it shows the CA's name and
fingerprint to compare with the downloaded certificate before installing. The guided page separates
Linux browser imports, macOS Keychain trust, iPhone/iPad profiles and Android trust, with a common
warning-free HTTPS check before pairing. `altitude/qr.py` draws its link as a QR
code (byte mode, level M) for terminals and the browser. `alt tls-share` (operator only) opens a
window in the CLI process, not in altd. It takes the address, port and TLS directory from the
service record (below), not from the operator's shell. It fetches `/api/health` over HTTPS trusting
only that directory's CA and requires the answer from the process that wrote the record before
offering those bytes. Settings → Devices → **Set up a device** asks altd to open one window (`POST /api/devices/share`, refused to agents and
cross-site pages like the terminal); altd offers the `ca.crt` of the TLS directory it serves from,
keeps one window at a time, and closes it at its deadline or on `share-close` with its link, which
the page sends on Close (confirming only once it succeeds) or when it is left, including while the
window is still opening. The page keeps the window only in component state, never in a
query cache. **Open setup page** opens that same share URL in a new tab without opener access,
retaining the original Settings page, certificate facts and sharing timer. The link is available
only while sharing is open and disappears during closing or after expiry. `alt pair` takes its address
from the same discovery. Sharing refuses loopback-only addresses; the hosting computer uses `alt trust`.
Remote binding and trust remain explicit; HTTPS identifies Altitude, and pairing (below) decides who
may use it. See [setup](SETUP.md#trust-https-on-each-device).

`alt trust` (`tls.trust`, operator only) is the one place Altitude changes trust, for the hosting
computer's own account. It takes the service from its record, requires a generated (managed) CA and the
same HTTPS proof as `alt tls-share`, and works on a temporary copy of the proven bytes. The platform seam
lists the account's browser stores and checks and changes them without administrator rights: on macOS
the login keychain through `security` (`add-trusted-cert -r trustRoot -p ssl`, checked with
`verify-cert` against the server certificate, which macOS confirms in its own password window), on Linux
each NSS database (`~/.pki/nssdb`, snap Chromium's and every Firefox profile with a `cert9.db`, in the
classic, XDG, snap or Flatpak profile folder) through
`certutil -t C,,` (creating the one an installed Chromium-family browser reads but never creates), found by content because NSS keeps an earlier import's nickname; a Flatpak Chrome is
named for its own import. The CA is added as "<name> · <first eight fingerprint digits>", and each
change is checked afterwards rather than trusted from the tool's exit status. The question and answer
use the controlling terminal (`/dev/tty`, with earlier keystrokes discarded), so standard input, which a
piped installer fills with itself, never answers. It prints one undo command per store. `alt doctor` runs the same checks read-only for
`certificate_trust.this_computer` and its next step. The access view's `trust.command` tells the pairing
screen that a browser is on the hosting computer, outside a container, with a CA `alt trust` accepts, so
it leads with that command; the page itself never changes the computer.

The pairing screen checks that a device on the network trusts the installation's CA, not just the one
certificate a warning bypass let through (`tls.TrustCheck`). At startup and after each daily
certificate check, altd issues a second server certificate from the same CA and key with its own
serial, kept in memory. The screen posts `/api/trust` for a challenge; for the next five seconds,
every new connection from that client address completes its handshake with the second certificate,
since a browser may open several and use any. `GET /api/trust/<challenge>` answers trusted when it
arrives on such a connection after a full TLS 1.3 handshake. A browser that trusts the CA accepts
any certificate it issued. A browser that clicked past the first certificate's warning, or ignores
certificate errors, refuses the second with a certificate alert; altd then settles the challenge
that armed that connection as untrusted, disarms the address and answers on the next connection.
A refusal is final for its challenge. A browser that ignores certificate errors connects again
accepting the second certificate, possibly before altd has recorded the refusal, so an answer on
the second certificate first waits up to five seconds for the challenge's other handshakes to end;
one still open then gets a retry. Three attempts without an answer read Couldn't check: a failed
request alone never means untrusted. A request on a connection opened before the
challenge, including a TLS 1.2 or resumed one, gets a retry. altd issues no TLS session tickets,
so every TLS 1.3 connection presents a certificate. The screen offers the code only once the check
passes; the server does not yet refuse a pairing on it. With an external certificate, altd holds no
CA key for a second certificate, so the screen asks the person to confirm a warning-free Private tab.
In the container deployment, published-port forwarding gives every peer one address, so a check
arms and settles that shared address: while one device checks, a device that only clicked past the
warning fails its new connections for those seconds, and a device checking at the same moment can
read Not trusted yet until Check again.
An HTTPS loopback peer follows the same trust flow; being on the host is not evidence of browser
CA trust. A warning-bypassed page provides instructions but cannot authenticate itself. Before
pairing-code entry, the operator independently compares the CA name and full SHA-256 against the
hosting terminal, stops on mismatch, imports the matching CA, restarts the browser and reopens the
page or the original private link without a warning. The probe is a convenience rather than that security boundary;
the pairing API authenticates the code, not the browser's trust store. Plain HTTP is only a local
development flow. The screen downloads the public CA
from `/api/certificate/altitude.mobileconfig` (the iPhone/iPad profile) or
`/api/certificate/altitude.crt`.

Every request passes the same checks before routing. Without TLS the `Host` must be an address or
`localhost`: a DNS-rebinding page names its own host in both `Origin` and `Host`, which HTTPS refuses
at the certificate. Every POST must come from Altitude's own page or from a client that is no page:
`Sec-Fetch-Site`, when present, must be `same-origin` or `none`, and an `Origin` must name the
request's `Host`. The `alt` CLI sends neither header; another site open in the operator's browser
cannot make it send an action. The Vite dev server proxies only to the altd `ALTITUDE_DEV_API` names; it
gives the dev page's own requests that altd's origin and leaves any other page's origin in place. A JSON body is at most 1 MiB; image messages, voice
recordings and a task worker's own requests ([task door](#task-door)) have their own limits.
A client that sends no complete request line and headers within thirty seconds loses its connection. An internal error's text reaches only the CLI and paired
devices; any other client gets a fixed message, and altd's log keeps the details. The web app's pages refuse to be framed by any page
(`frame-ancestors 'none'`).

Then `access.py` decides who is asking. The page and its files, `GET /api/health`,
`GET /api/access`, `POST /api/pair`, the trust check and the public CA downloads answer anyone; every other request, including design boards,
needs a paired browser or this machine's key, and otherwise gets 401 with `"pair": true`. Everything
lives in `~/.config/altitude/access/` (mode 0700, beside the TLS material, outside every runtime,
source and project root). altd creates `machine.key` there when it starts; the `alt` CLI and the
restart script send it as `X-Altitude-Key`, each only to an altd that proves itself with the installation's CA
(the system trust store for an external certificate). The key is compared in constant time. Only the operator's
account can read it, and no Altitude sandbox can: the Codex coordinator and reviewer profiles
and Altitude's own sandbox around every other job deny the access and TLS folders (`platform.secret_places`), so
the key proves a caller is the operator's own CLI rather than a confined agent. The same rule hides the common
browsers' profile folders, which hold every site's sign-in, a paired device's cookie among them. `key.json` records the key's
generation: when altd starts with a key from an earlier generation, whose sandboxes could still read
this folder, it replaces the key, drops a pairing code in flight and notes the agent jobs then running
(task workers, coordinator turns and reviews); once all of them have ended, the tick replaces the key once
more (`access.settle`). Each replacement also ends open design-board links and the replay of a voice
recording in flight, which are signed with the key.

The CLI finds altd through the service record, `~/.config/altitude/service.json`, outside the hidden
folders so a sandboxed `alt` can read it: as the service instance (`ALTITUDE_SERVICE=1`) starts
serving, it writes its PID, address, port, scheme, TLS directory and the public CA certificate it
serves from (null for an external certificate), replacing the previous record. It decides where `alt`
sends its requests and which CA it trusts, so it lives with Altitude's private configuration rather
than in the runtime home that task folders share. `alt` reads it for its altd calls instead of its own
launch environment, so a worker started before a restart onto another certificate folder or port
reaches the new service without relaunching. It verifies altd with the recorded CA (the system trust
store when there is none) and refuses, naming the CA, an altd that does not prove its identity; there
is no unverified fallback, and a missing record is named. Serve-only instances write no record.
`alt pair` (operator only) writes one pairing code
straight to `devices.json`: eight characters from an alphabet without look-alikes, valid ten minutes,
stored as a SHA-256 hash. A new code replaces the old, and five wrong codes from anyone cancel it, so
pairing works over SSH, with any bind address and without a running page. Redeeming the code gives
the browser a random key in the `altitude_device` cookie (HttpOnly, SameSite=Strict, Secure over
HTTPS, 400 days); `devices.json` keeps only its hash with the device's name, read from the user agent
and whether the page runs as a Home Screen app, and the day it was last used. A device's cookie
renews on its first request each day. Removing a device in Settings deletes its row: its next
request gets 401, its open change, terminal and chat streams end within about a second, since each
re-checks the device every second, and its design-board links stop opening. A chat turn whose stream
ends this way still finishes and is logged. The file store needs only POSIX file locking and modes, so macOS
behaves the same. The web app shows only the pairing screen until `GET /api/access` reports the
browser paired, and returns to it on any 401.

The fresh install link carries that same single-use code in `#pair=`. Before router creation or
access/trust requests, the web entrypoint removes this dedicated fragment from the history entry,
including malformed links and visits by already-paired browsers; ordinary route anchors keep their
behavior. The gate keeps a pending code only in page memory, discarding it on success, refusal,
manual replacement or page disposal. Reload or browser restart requires reopening the original
terminal link while valid. It sends the code only in the existing same-origin `POST /api/pair`
JSON body, never in paths, queries, referrers, DOM text, browser storage, caches, logs or errors.
The originally clicked URL can reach terminal/OS handlers, browser history synchronization,
extensions or an initial TLS warning before cleanup; cleanup does not erase those systems' copies.

Automatic redemption waits for the managed-CA challenge on every HTTPS browser, the hosting computer's
included, or explicit Continue after the external-certificate Private-tab check.
Ordinary TLS verification precedes page load, and plain remote HTTP never redeems a link. An
unreachable access read holds the pending code for Retry; already-paired access discards it without
adding a device. A lost redemption response triggers an access recheck, never a blind retry of the
single-use code. Refused links return to the blank manual field with recovery through the installed
launcher. Successful redemption opens First run, or the existing app when projects exist. Server
authentication, origin checks, cookies and the manual code's lifetime/replacement policy are unchanged.

### Altitude's own agents

Altitude records what a paired device sends as the operator's, and grants, merges and answers rest on
it, so no agent may send it. Every POST without this machine's key that does not come through the
[task door](#task-door) is refused with 403, before its body is read, when it comes from one of Altitude's
own agents: chat and task messages, answers, decisions, task actions and reviews, settings, devices,
sign-in, trust, pairing, voice, repairs, agent chats and any route added later. The page's reads are not refused. The
machine key keeps its routes, since no sandbox can read it; the terminal, voice, device sharing, update
and validation controls refuse an agent even with it, and the repair routes refuse the key itself. One check decides, once per connection, for all of
them: `terminal.agent_connection`, which names the process that opened the connection's client end
([Operator terminal](#operator-terminal) describes how). A container installation infers nothing: its
clients arrive through the published port, and the browser that holds the cookie is on the host.

The check has known limits. A process running outside Altitude's units is not recognized, however it
was started, and can act with any credential it can read. A forwarder on this machine in front of altd
(an SSH tunnel, a reverse proxy, a container installation's published port, a dev server) holds the
socket altd sees, so a worker connecting through it looks like the operator's browser. A cookie copied
to another device is that device's. A worker whose engine runs without an OS sandbox can change the
operator's files directly. On Linux the check relies on workers being unable to move to another control
group, which their sandboxes mount read-only. Where nothing can be named, every local client is refused,
the operator's own browser on this computer included, while a browser on another device keeps working:
a host without cgroup2 at `/sys/fs/cgroup`, an altd in the root group, or a browser started in altd's
own control group, as from the terminal that runs `alt serve` in the foreground. A development backend
an agent starts refuses that agent's own browser the same way; the browser harness's services mark
their clients as the operator's (`web/e2e/service_support.py`).

### Linux and macOS

Everything above the platform seam is the same on both hosts. These are the behaviors that differ;
`platform.py` owns each one, and the sections named in the last column describe it.

| Area | Linux | macOS 15+ | Where |
| --- | --- | --- | --- |
| Service | systemd user unit; restarts on failure; runs while the user manager runs | LaunchAgent in the `gui` domain; restarts on failure; starts at login, runs with the screen locked, stops at logout; does not run before any login, when macOS has neither the login's job domain nor its keychain | Setup, Operations |
| Service logs | the user journal | `~/Library/Logs/altitude/`, also for detached jobs such as updates | Operations |
| A job (worker, review, machine command, update) | transient user unit; systemd holds every descendant in its cgroup and enforces the time limit | its own launchd job; a supervisor enforces the time limit, holds an idle-sleep assertion, and stops the job's kernel coalition when the command exits; launchd's SIGTERM at logout reaches the supervisor alone, which stops the whole job as Stop does | above |
| Stop | stop the unit; the cgroup takes every descendant; pidfd pins each signal | kill every coalition member, rechecking start time and coalition just before each signal (no process handle exists) | above |
| Task worker and Claude confinement | also Altitude's bubblewrap sandbox: writes only under its roots, never reads Altitude's credentials or the common browsers' profiles, sees only its own processes, an empty per-user runtime folder and its own `/tmp`, and, on Linux 6.12 or newer, no abstract socket it did not make; a sandbox cannot start inside it | also Altitude's Seatbelt profile: writes only under its roots, never reads macOS's [private places](DEVELOPMENT.md#macos-private-places), signals only its own processes, reaches Unix sockets only in its own folders, no launchd control, Simulator service, app launches or Apple Events; browser checks run in validation | Isolation and landing |
| Codex coordinator and review confinement | Codex's own sandbox (bwrap), denied the per-user runtime folder and the shared temporary folders | Codex's own sandbox (Seatbelt), which denies macOS's private places and admits neither the Simulator service, app launches nor Apple Events; the two profiles cannot nest | Isolation and landing |
| Candidate browser checks, both engines | `alt task validate` runs the disposable rootless container with Chromium's own sandbox (two layers) | `alt task validate` runs a job under the validation Seatbelt profile with Chromium's inner sandbox off because profiles cannot nest (one layer, fictional fixtures only); stock engines need no custom runtime | [Browser verification](DEVELOPMENT.md#browser-verification) |
| Operator-grant commands | outside the worker sandbox with the user bus reachable, so a command can stop or reconfigure its own unit and its time limit | outside any sandbox with launchd reachable, so a command can signal its own supervisor; on both hosts the time limit bounds an ordinary command, not one that works against it | Isolation and landing |
| Terminal Close | the shell is a transient unit; stopping it hangs up its cgroup | the shell is a launchd job; stopping it hangs up its coalition | Operator terminal |
| Agent check | the kernel's record of the control group that opened the socket (sock_diag), resolved under `/sys/fs/cgroup` | this user's processes' sockets (libproc) and job coalitions | Altitude's own agents, Operator terminal |
| Coordinator socket admission | in the per-user runtime folder; the peer's pidfd (Linux 6.5 or newer) and cgroup | beside Altitude's private configuration; the peer's audit token, pinned by its pid version, and its job coalition | Isolation and landing |
| Image conversion memory cap | `RLIMIT_AS` | a watcher that kills the converter past its memory footprint | above |
| Native libraries, tools | system packages (apt, dnf, pacman or zypper) | Homebrew; OpenSSL 3 from PATH or Homebrew's `openssl@3` | Setup |
| Temporary directory in jobs | the job's own folder in `/tmp`, removed by the user manager when the job ends | the job's own folder in `/tmp`, removed by the job's supervisor | above |
| Source-checkout deployment | systemd unit (`make install-service`) for the checkout at `~/Projects/altitude` on `main`; address, PATH and branch by drop-in; TLS drop-in; self-restart | LaunchAgent (`make install-service`) for the checkout wherever it is, taking the address, PATH and checked-out branch from the installing command; self-restart through the seam; TLS drop-in refused with an explicit message | Setup, Operations |
| Installer checks | not root, Python 3.12+, `curl`, a SHA-256 tool, `openssl`, `systemctl --user` | macOS 15+, not root, Python 3.12+, `curl`, a SHA-256 tool, OpenSSL 3 or newer on PATH or in standard Homebrew locations, launchd's domain of the logged-in desktop session; on both hosts each missing item stops with its fix, for the package manager found, before anything downloads, and a host other than Linux x86_64 or Apple silicon installs with one line saying it is not yet validated | Setup |
| Installed service | `~/.config/systemd/user/altitude.service`, enabled and started | `~/Library/LaunchAgents/dev.altitude.altd.plist`, bootstrapped into the `gui` domain; its definition sets `HOME`, and an installation under another `HOME` gets a label derived from that home | Setup, Operations |
| Update from the app | `alt update --version` in a transient user unit; its output is in the user journal | `alt update --version` as the launchd job `dev.altitude.job.altitude-update-<version>`, logging to `~/Library/Logs/altitude/altitude-update-<version>.log` | Operations |
| Failed update, interrupted activation | the previous version is restored and its unit restarted; `alt recover` (or `install.py --recover`) finishes an interrupted one | the same, restarting the LaunchAgent | Setup, Operations |
| Uninstall | stops, disables and removes the unit and launcher; settings, TLS identity and data stay | boots out and removes the LaunchAgent and launcher; launchd keeps its enable/disable record for the label; settings, TLS identity and data stay | Operations |
| L3 journal reading | `journalctl` shim | not available; L3 reads service status through the broker | Isolation and landing |
| Incident system facts | `os-release` name, kernel release, DMI vendor and product family (product name when the family is a placeholder) | macOS version, Darwin release, `hw.model` identifier | [Incident issues](CLI.md#incident-issues) |

Project identifiers are single folder names on both hosts. The shared path resolver validates them
before project storage or locks are accessed; registration validates before resolving a repository
folder, and monitor session keys use the same check. Empty names, `.` and `..`, path separators and
NUL are refused. Flat names retain their case, spaces, Unicode, dots and underscores. Validation does
not require registration, so initialization and detached project archives use the same resolver.
Names differing only by case are refused at registration on both hosts, because macOS disks are
case-insensitive by default and runtime folders are named after projects.

### Project setup

`project_setup.py` owns the concrete folder, repository, instructions, guards and coordinator
checks. Registration requests routine setup; maintenance and project reads inspect current
requirements for every registered project. There is no permanent onboarding-complete flag.
Existing configuration is reused. A non-Git folder supports conversation with Git tasks
unavailable; setup never initializes Git or creates or overwrites project instructions.

The daemon persists operation identity, progress and guard receipts in the project's `setup.json`.
Reads combine these records with current filesystem, hook and coordinator evidence, then reread
storage under a nonblocking operation-lock probe. Changed records trigger one fresh inspection;
an acquired lock stays held through that inspection so repair cannot invalidate its receipts.
A still-running record under the free lock exposes an interrupted runner; a busy runner remains
checking. Only the runner waits for the lock: a read in flight delays an accepted repair rather
than losing its wake, and a request accepted while the previous run is finishing runs before that
runner exits. Retry rechecks completed effects before writing.
Pending, running, waiting, created, reused, not-applicable, failed and input-needed results come from
observed work. A queued notification or saved start request never proves an agent is running.
Recorded task worktrees expose their own hook overrides; inherited healthy guards share the
project result. Launch repair checks only the project and the launching task's checkout.
Lock contention is a temporary launch hold: queued dispatch waits, and resume releases its unlaunched
claim without consuming the authorized request, restoring its message batch ahead of later arrivals.
The existing daemon scheduler retries the same request after release and rechecks guards, worktree
provenance and lifecycle fences before launching. Actual setup/provenance errors retain their fault path.

**Setup…** in the project menu opens these results and actions; the header shows a Setup status only
while the current observation needs attention (running, unmet or unreadable), with no stored
dismissal. Programmatic repair installs or
refreshes owned guards and establishes the coordinator command connection. **Retry** requests that
same bounded work. Only a repair that leaves its own Git guards unmet is Altitude's fault and files
an incident; guidance the operator acts on, such as a local repository without a GitHub remote (shown
with `gh repo create --source . --push`), is input-needed and files none. Configuration faults notify the
existing L3; **Ask the coordinator** opens its conversation
without sending a message or launching another repair agent. L3 investigates with its existing
tools and can request project-scoped daemon repair through `alt project setup`, independently of
worker dispatch. An agent's claim of repair does not complete a step: current checks verify it.
Until a coding tool the coordinator's routing may use is ready (`project_setup.missing_tool`: installed,
signed in through `engines.sign_in`, and on Linux the agent sandbox for an engine Altitude confines itself),
the coordinator step is waiting, names the missing step and links Coding tools; the first turn does not
run, `server._start_l3` records the start request instead of failing, and the header offers no Start L3.
Each maintenance tick starts an unclaimed first turn once the tool is ready, so setup continues by itself.
Failed or interrupted introductory calls wait for an explicit Retry, including during routine
guard maintenance; timer checks do not repeat model calls. The runner starts the introductory
workflow before completing its operation, and the coordinator reads as running while that workflow
is alive, so setup stays checking, and polls quickly, until the saved reply completes it. Opening the
conversation from Setup rereads its history.

Custom hooks remain intact. The operator can explicitly choose **Use both hook sets** for a
supported ordinary hook directory; the choice binds to the inspected hook configuration.
Its approved composition and original-hook hashes live outside worker-writable metadata in the
daemon-owned installation boundary. Changed custom hooks require a renewed choice.
The choice discloses the original directory/events and authorizes normal hook execution with
Altitude's Git permissions, outside the worker sandbox. Entrypoint hashes detect changed hooks;
they do not sandbox code, verify its dependencies, or eliminate a concurrent replacement race.
The managed composition preserves other hook events and gives both sets the same arguments and
input; either can reject an operation. Unsupported hook managers and relative custom hook selections remain actionable
conflicts. Routine repair and L3 cannot authorize composition. Task sessions, worktrees, questions
and merge holds survive checks and repair; only tasks whose cause is verified fixed are resumed.
See [the supported recovery procedure](CLI.md#project-setup-and-guard-recovery).

The [project seams rule](../AGENTS.md#seams) owns operator, engine and machine assumptions.
`tests/test_project_layers.py` ratchets names outside those boundaries and project policy in personas.

## Engine integration boundary

Altitude coordinates ongoing CLI agent sessions. Codex and Claude Code are the current
integrations; the project workflow centers on the coordinator, task owner, isolated worktree and
checked PR, independently of which integration executes a turn. Both roles use the project's Auto
preferences or explicit pins and can operate with one installed engine and one available model.
[Configuration](OPERATIONS.md#configuration) lists the settings an installation reads.

Owner launch and resume pre-approve `Bash(alt *)` in native auto mode, so Altitude's own
coordination commands do not depend on the permission classifier. The rule matches a plain `alt`
invocation only; a chained or redirected form, or a shell or interpreter command that writes a file
for `alt` to read, goes to the classifier, which can refuse it. The L2 persona therefore has owners
run each `alt` command alone, write its input files with the engine's own file-editing method, and
rerun a refused chained form plainly. Native deny/ask rules, worker confinement and Altitude's role,
task-attempt, merge-hold and operator-grant checks still apply. Other external commands retain
their native permission checks. A Codex owner asks for no approvals, so its coordination needs no
such rule. The allowance is shared by Linux and macOS launches; it
grants no additional filesystem or service access.
The native classifier reads user turns, never hook context, as the user's intent
([issue #612](https://github.com/mburakyucel/altitude/issues/612)). The operator's own task messages therefore
reach a running owner only as a user turn: the inbox hook names them in a waiting notice, and when the
owner stops with nothing else to deliver, the turn ends and altd resumes the same session with them as
the prompt, as the clean-turn resume does for messages an engine without hooks could not take into its turn. L3, server and other-owner
messages, tool output and files keep hook delivery. A resume prompt carries every waiting message with
its sender. Streamed launch input with images is marked as the human's, as a text prompt is.

A skill or custom command typed by name in a chat (`/name arguments`) reaches the session as part of
the message text, after the line naming its sender in a task and after the coordinator's turn header in
the project conversation, so the engine's own expansion of a leading slash never runs, and the
session's model decides whether to use it. A Claude owner loads the operator's skills and commands
through its linked folder and the project's skills from its worktree, and its model invokes the named one with the arguments that follow through Claude's Skill tool, on a new turn, in a
running one (Send now) and on a resumed session alike. Claude offers its model no skill marked
`disable-model-invocation`, so such a skill runs only when typed in a terminal. A Claude coordinator
runs in restricted mode, which loads none of the operator's or the project's skills or commands and has
no Skill tool, so the project conversation receives the name as words: the coordinator says it cannot
find it, or follows a project skill's file it finds by reading. Loading them there would change the
coordinator's confinement. A Codex task worker has the operator's skills and prompts linked into its
folder, and a Codex coordinator runs on the operator's own Codex folder; both receive the name as text.
Claude's behavior here is a provider property established by recorded runs on Linux with Claude Code
2.1, not by the suite; Codex's has no recorded run yet.

| Module | Integration responsibility |
| --- | --- |
| [`engines/__init__.py`](../altitude/engines/__init__.py) | The shared callable interface and engine selection used by Altitude's callers. |
| [`engines/common.py`](../altitude/engines/common.py) | Shared launch/resume/stop execution, worker ownership and output records, token accounting, and captured review transport. |
| [`engines/claude.py`](../altitude/engines/claude.py) | Native launch arguments, session/output parsing, model/context observations, quota and permission details. |
| [`engines/codex.py`](../altitude/engines/codex.py) | Native launch arguments, session/output parsing, model/context observations, account quota reader and permission details. |
| [`route.py`](../altitude/route.py) | Select an engine/model from explicit pins or ordered Auto tiers, installation, rejection and quota evidence; expose the same routing reasons to callers and Monitor. |
| [`config.py`](../altitude/config.py) | Engine names, executable paths, defaults, preference syntax and context settings, alongside runtime configuration. |

Callers import `altitude.engines` and use its callable interface. Each native module adapts the same
execution and authority contract. Shared code loads the module named after a `config.ENGINES` entry
and reaches native behavior only through the names every native module defines, never by comparing
engine names; `tests/test_engine_interface.py` checks both. Adding or dropping an engine changes its
module and the registry, with no provider policy system or base-class hierarchy.

The interface is internal and evolves with the integrations. Additional CLI engines such as
OpenCode are candidates for future support. Provider access is a separate integration concern:
subscriptions, direct API billing and services such as Bedrock are intended to fit the same
project workflow. Bedrock is an access service, not a CLI engine; neither OpenCode nor a Bedrock
setup is supported today. A new engine may
have different session, authentication, capability and usage-reporting models. Adapt the boundary
to preserve its native behavior rather than treating today's two launchers as a universal contract.

Stock engine execution and role permissions stay unchanged. Fictional candidate browser checks
use the existing [validation runner](DEVELOPMENT.md#fictional-browser-validation): Mac uses its
Seatbelt profile without Chromium's inner sandbox; Linux uses the container and Chromium sandbox.
The custom runtime patch and trial/restore mechanism are retired without ever selecting a custom
runtime. Native metadata enforcement remains separately tracked; deletion is not a stock repair.
The full Chromium suite remains the default and required CI preserves full-distribution features;
the locked headless-shell recovery lane covers only its supported fictional journeys.

Auto uses the highest-priority configured tier with an eligible option. The default ties Codex and
Claude on their role defaults, so a Claude L2 launches on Opus and L3 on Fable, with Opus below as
L3's fallback when Fable is rejected; a lower tier never repeats an option a higher tier already
resolved. `ALTITUDE_PRIMARY_ENGINE` chooses only the default tie order. Named, comparable seven-day account readings select within a tie. Unknown readings use
configured order, and L3 retains its current engine/model within that tier unless a competitor has
at least fifteen percentage points more weekly headroom. A higher available tier takes precedence.
Missing executables, exhausted windows and explicit provider rejections exclude the affected option;
unknown access remains eligible. A sign-in rejection is the CLI's own authentication code or an
HTTP 401, whatever its wording, or the CLI's sign-in prompt before any request. Authentication
rejections exclude the engine and model rejections exclude only that model for thirty minutes.
Signing in cures an authentication rejection at once, so it has its own record, which is the engine's
sign-in lapse: an operator coordinator message or Retry that finds no engine withdraws it for the
engine that turn then attempts, and each explicit task resume request withdraws it for the task's
engine. A credential still invalid records it again on that attempt, within the same lapse, and
automatic turns and resumes keep waiting. On an engine whose runs share the CLI's sign-in (Codex), the
quota refresh records a lapse when its read is refused for sign-in and its successful read ends it;
Claude's runs use Altitude's own sign-in, so saving a new one ends theirs. A
successful coordinator turn ends a lapse on either engine. Each project's
conversation gets one heads-up line per lapse naming the engine, what the turn did (answered on
another engine, waits, or stopped) and how to sign in again: Settings › Prerequisites for an engine
Altitude signs in itself, else the engine's sign-in command; failed turns show the
rejection's plain reason, never provider text. A current model-specific weekly reading at 100% also
excludes only that model. The router never derives model allowances from an
account-wide meter or subscription entitlement from a plan name. Safe pre-output rejection retries
are bounded to configured alternatives. Explicit pins never fall back, and an L2 resume retains its
attempt's engine, session and launch model. See [routing lifecycle](SESSION_LIFECYCLE.md#fresh-dispatch).

The engine boundary refreshes both seat readings every five minutes before dispatch. Native
headless `/usage` supplies live account rows and any model-specific weekly rows; the other native
reader uses `account/rateLimits/read`.
Statusline snapshots supply session display data, not routing quota. Failed reads replace previous
success with unknown; successful observations expire after thirty minutes. Collection neither
resumes task sessions nor changes routing preferences. See the [quota source and compatibility
limits](SESSION_LIFECYCLE.md#context-and-prompt-cache-evidence).
The native account reader retains each valid window independently, even when the other window or
its reset deadline is absent. A reported exhausted weekly or five-hour window excludes the seat,
with an unknown reset time when no deadline is provided.

`config.subprocess_env()` supplies the common tool environment for engine discovery/launch,
landing and restart builds. It preserves Node on PATH, otherwise asks installed nvm for its
default and prepends the returned executable's directory, including package-manager shims.
It reads `$NVM_DIR` or `~/.nvm` without shell profiles, provisioning or a version-selection policy.
Unavailable defaults leave other tools usable and are retried on the next call. Dependency installs
run inside `web` so Corepack resolves its `packageManager` pin; the frozen lockfile
and complete validation gate remain authoritative. See [toolchain setup](DEVELOPMENT.md#noninteractive-toolchain).

Current gaps are concrete: engine-specific references remain in session, dispatch, transcript and
telemetry code outside the seam. The launch environment filters
some engine variables and applies role/model/permission settings, so native access configurations
are not all passed through unchanged. The Codex coordinator uses `--ignore-user-config`, retaining
authentication while omitting user model/provider configuration; a project `l3_codex_model` can
supply a model override. Adding an engine still needs implementation and end-to-end
verification; it is not a registration-only plug-in operation. The [roadmap](ROADMAP.md#early-user-onboarding-and-public-release)
records these follow-up candidates without expanding this documentation milestone into a rewrite.

Engines retain responsibility for their execution tools, context management and native helpers.
Coordinator context observations use an individual request and its reported window, independently
of cumulative turn usage. An integration with native compaction retains its conversation at the
context threshold; only the integration needing managed rotation uses Altitude's threshold reset.
Explicit reset and confinement-policy changes retain their existing fresh-session behavior.
Every L2 launch and resume supplies the same task-local tool-cache environment on Linux and Mac.
GitHub CLI logs and package-tool caches live under `tool-cache` in the task's scratch folder (see
[task door](#task-door)); installed Corepack managers retain their original location. Tool-native
validation/invalidation rules apply, and live delivery checks remain authoritative. Archival removes
the scratch folder while retaining worker and review evidence. Its cleanup precedes
the terminal state change under the project lock; failure retains the task's prior state and location.
Deletion walks from the scratch folders' resolved parent through open directories without following
links, and removes only that task's folder relative to the held project-folder descriptor.
Rejection stops the attached worker through the engine seam before disposal, so no live writer
recreates the cache after archival.
[Development](DEVELOPMENT.md#local-checks)
describes frozen-install handling for worktrees with an existing pnpm store and native evidence limits.
Altitude supplies focused [role instructions](../personas/), repository context and delivery
boundaries. Execution strategy stays adaptable because a fixed sequence of stages and specialist
roles can outlive the model/tool assumptions behind it. Customization belongs in repository
instructions and the engines' native skills, hooks and agent facilities where appropriate.

Required background results belong to the active owner session. The existing native Stop hook
combines queued coordination with an instruction to wait for in-flight tasks, using the engine's
background-task payload. It acts only while the task is running; explicit blocks and operator Stop
retain their exit paths. It creates no job registry, automatic retry or alternative completion path.
Report freshness and delivery verification remain authoritative; native coverage limits are explicit
in [polling and cleanup](SESSION_LIFECYCLE.md#polling-and-cleanup).
The engine adapter accepts worker activity and completion only from the launched owner session.
Helper notifications on a shared transport cannot finish the owner, change its steering target or
become its output or usage. This uses native session identity, without supervising helper lifecycles;
genuine owner exits retain the shared recovery and descendant-cleanup contract.

### Adversarial review

`reviews.py` owns task-bound proposal and changes review records; the task remains owned by L2.
L2 proactively seeks adversarial review of complex proposals before code and complex implementations,
using judgment to keep simple work light. L2 requests through the CLI or the operator uses the
Proposal review and Implementation review boxes in task details. `view` gives each subject's latest
review and whether a new one can start; viewing never invokes review. Requests queue on any open
task: a waiting or blocked owner is woken, and a stopped or faulted task keeps the request for its next
resume. A repeated request or source message reuses its receipt.
The owner prepares a committed checkpoint, identifies the exact original L2 proposal message for a
proposal review, and invokes the fixed daemon endpoint. Open questions do not gate either subject: a
request may continue a question-blocked owner solely for review while preserving every open question and
its original authority. This adds no implementation or merge authority, helper queue, provider handoff,
owner session or general command transport.

The engine and routing seams prefer an eligible configured option different from the owner, respecting
project choices and known quota exhaustion. Otherwise they select a separate same-engine invocation
under the same captured-input/read-only contract, the ordinary path, with its fallback reason, engine/model
and account-allowance uncertainty saved with the review. An explicit
per-request engine/model selection replaces that preference with a single candidate under the same
eligibility, quota, rejection and capability checks; it is saved with the request, rechecked at run and
never substituted, and project defaults are unchanged. No engine switch occurs after launch. Fallback cannot bypass unavailable observation, cancellation or capacity. Admission
uses the existing machine launch lock: one reviewer machine-wide, with an additional machine slot,
no reservation while waiting for preparation and no automatic retry. L2 and reviewer instructions
set expectations for a relatively quick, focused review without a programmatic duration cutoff.
The command waits without a review deadline; streamed JSON whitespace detects disconnected callers.
Owner changes, cancellation and caller disconnect stop the run. Planned activation waits for in-flight
reviews to return their results; new review runs wait while activation, or an installed release's
offered automatic update, is pending. Capacity contention refuses
`run` and leaves the accepted request waiting for the reviewer, shown as such, until a later explicit run. Unexpected restart reconciliation cancels an
orphaned invocation rather than leaving it running without a result consumer.
The engine checks service inspection before launch. Its cleanup stops the independent reviewer unit
on interruption, including keyboard interruption and process exit, before reaping the launcher.
Unknown termination remains a fault with capacity reserved; launcher exit alone is not cleanup.
Adapters lacking the captured-input contract are unavailable.

Capture records committed head, fetched base, source tree, context identities and input
hashes. Raw Git blobs preserve exact bytes without export attributes; untracked files, symlinks and
submodules are excluded by refusal. A path over 2 MiB in either base or candidate stays out of the source
and diff, and the capture names it with its size. Both trees accept at most 10000 files and 64 MiB of
remaining content, so removed content is bounded before creating the diff. Context includes the brief,
request, original authority messages and corrections, decisions,
and default-all or selected L2 evidence; its captured `context.json` holds one copy of each input, so
the request is omitted when the brief contains it verbatim, and is bounded to 256 KiB: room for a long
task's complete authority and decisions plus selected evidence, well under the reviewer's 2 MiB file read. Nothing
is dropped or summarized; beyond the bound capture refuses with the size and whether mandatory records
alone exceed it. Proposal capture
also binds the exact original L2 message and text against the committed head's source tree, even before
code differs from main. That text is captured once, as `proposal.md` with its own 64 KiB bound, and
`context.json` names the proposal message and points to the file. A UI proposal's `--show` files add
the document as `design.md`, captured by the same checks as [shown files](#shown-files), with the images
named in `context.json` and copied into the snapshot's `design/` folder. The engine attaches them natively
to the reviewer's first prompt in that order: Codex with its image option, Claude as titled image blocks
in its stream-json input. The receipt keeps the shown set's identity, which the question shares when it
shows the same files, and names the images given; an engine CLI without image input gets none, and the capture's limitations say so. Identity and freshness hash the
logical context, independent of this representation; the input hash covers every captured file. Changes reviews capture the merge-candidate tree and diff. A request is recorded whether or not the
branch merges with main; a run on a branch that conflicts names the conflicted files to the owner and
leaves the review requested for the owner to reconcile and run again. Missing proposal input prevents invocation. Images attached to
messages need a textual account and stay outside the reviewer's coverage.
The adapter exposes only a fixed captured-file list/read/literal-search tool: no tests, shell,
connectors, network tools, mutation, nested helpers or task identity. Read and search deliver text
from a line/column cursor, at most 100 lines and 16 KiB per response; a truncated response names
the line and column to continue from, so a long line such as a JSON-encoded proposal is readable in
full. The result is the first complete JSON object in the reviewer's answer, fenced or bare; an
answer without one fails as invalid findings. Codex reaches it through its
code-mode host, the one native feature left enabled besides skipping host skill discovery: a
JavaScript tool bridge without filesystem, process or network globals, whose patch tool the read-only
sandbox rejects. Claude loads it from the command-line MCP configuration in restricted mode with no
built-in tools, no skills and no safe mode, which disables every MCP server; its prompt arrives as one
stream-json user line and its answer is the final stream-json result record. Review stdout keeps only
the records the result and its diagnostics read (Claude's result record, Codex's status events and
agent messages); tool calls and their results echo captured input and are dropped before the 2 MiB
stdout bound, so a long review cannot exhaust it. Both are provider
properties established by recorded reviewer runs, not by the suite. The result's `text` is a
one-sentence verdict the UI shows as the review's summary. The adapter records the first
content-bearing read in the review runtime; a reviewer that read no content fails with that reason
instead of completing with no coverage. Codex authenticates with the
CLI's configured account and Claude with Altitude's own sign-in in its review folder (see
[isolation and landing](#isolation-and-landing)). The reviewer environment keeps only home, login name, path,
locale and engine configuration directories; the login name lets a CLI find its macOS Keychain sign-in. Deterministic fixtures verify application behavior and adapter configuration;
live-provider compatibility remains unverified under the standing testing decision.

Failed review executions retain bounded sanitized stderr, launcher exit status and capture-completeness
evidence in the existing task-owned record. Recognized structured stdout error fields yield fixed
diagnostic categories, and a Claude result's allowlisted failure facts (subtype, terminal reason, API
error kind and status) locate failures no category recognizes; stdout transcripts and arbitrary error
prose are not copied into diagnostics.
Unknown, malformed, incomplete and truncated stdout remain explicit evidence states.
The [failure-evidence contract](SESSION_LIFECYCLE.md#adversarial-review) describes bounds and privacy.
Review records retain original findings and separate owner dispositions for each subject. Exact
source/authority freshness and selected-input hashes are distinct. Changes assessment records the final
candidate and evidence for every finding; code, base, proposal, request, brief, operator or L3 message
and decision changes require reassessment. The owner's own messages and the questions it asks or
withdraws carry no authority, so explaining an assessment never makes it stale; an answer it records
is the operator's or L3's decision and does.
A finding the owner leaves `open` stays visibly unresolved, blocks merge and prevents replacing its
review; only evidence-backed fixed/dismissed outcomes clear an assessment. An additional review of the
same subject replaces nothing, so earlier reviews stay in the merge gate with their original authority.
Proposal assessment records the proposal identity and dispositions, including an explicitly selected
revised proposal when relevant. Later proposal/source/context changes require L2 assessment or deliberate review.
The UI distinguishes reviewed evidence from later L2 assessment; proposal evidence never accepts an
implementation. Request admission and final merge share a task lock. Landing checks every accepted
request of the current delivery for assessment or authorized withdrawal after other pre-merge checks, including changes requests
followed by a proposal request. Review cannot release an operator hold or replace required checks.
Only the operator can withdraw an operator request. No automatic rerun occurs after fixes.

Each review belongs to one delivery, the work one merge closes, so a task that ships several PRs keeps
each step's reviews apart. The merge `alt land` records stamps the current delivery's reviews, replaced
ones included, with the merged head and PR number, once per merged head: they leave the gate, are never replaced or repeated, and keep their coverage
against the version that merged. A review the owner requests with `--next` while a PR waits is the
proposal review of the step after it: it stays out of that PR's gate and loses the mark when the PR
merges, joining the current delivery. A proposal request or skip without `--next` while a PR waits
belongs to that PR, and the command's `notice` says so. Replacement (`--previous` naming the latest review of its kind),
`--additional`, request waiting, skip and open findings all work within one delivery, and a question's
review status and task details' boxes read the latest unmerged review of its kind with its delivery's
open findings, so a later step's proposal never rests on an earlier step's review. A task with one PR is one delivery: its reviews work as before until its merge closes them, after which
its boxes start the next step if the task continues.

## Task lifecycle

```text
queued   -> running | rejected
running  -> reported | blocked | rejected | done
blocked  -> running | reported | rejected
reported -> done | running | blocked | rejected
done     -> blocked
```

A no-code research or proposal task can go directly from `running` to `done/archive`; a git check
refuses that shortcut when the task branch changed. Code work uses the verified report path.
`done -> blocked` is the operator's follow-up message reopening the task in place: its folder leaves the
archive, its earlier completion stays as one conversation row carrying its digest, and the ordinary
resume continues the same attempt and session, restoring a worktree retention removed (from the kept
branch, or from `origin/main` once the merged branch is gone). Only the operator's message reopens;
rejected tasks stay closed ([reopen a done task](CLI.md#reopen-a-done-task)).
Archival posts a task-linked FYI with the digest to the project conversation, the coordinator's
handoff; findings stay in the task conversation. The project view's archive lists every done or
rejected task finished in the last seven days, newest first by finish time, whatever their slugs.

Queued tasks with `planned_wait: {reason, after}` are **Planned**: `reason` is one short wait and
`after` optionally names one existing task in this project. Project and task reads add
`after_title` from the live or archived prerequisite; unavailable evidence remains null without
changing the saved wait. `alt task new --wait` or `--after`
creates the existing task record and written brief without a worker, worktree or WIP slot.
Dispatch skips it until that dependency is archived done, or L3/the operator explicitly clears
the wait with `alt task release <slug> --reason '…'`; a reopened dependency counts again once it finishes. An already archived-done dependency is satisfied at
creation; a merged PR alone, rejection, failure or a missing dependency does not release it.
Explicit release can override a named dependency early and records its reason. Messages remain
in the task inbox for launch and do not release it or replace the original brief's source authority.
The state digest includes every planned task and its reason; no separate planning store,
dependency graph or PR watcher exists.

Dispatch-ready queued tasks wait for machine capacity and engine availability gates. All projects
share one cap, defaulting to 80 running tasks across the machine. Overlapping declared paths are
information in task status and briefs; they do not hold dispatch or resume. One provider's quota does not
globally freeze the other. Blocked is a persisted wait/intervention state: an L2 question, a timed
operational hold, a worker failure, a verifier fault, or a report gap. An L2's question goes to L3
first, which answers from the record or escalates a dilemma to the operator. The durable dilemma
stays in Needs you while its answer is still needed, independently of the worker running or waiting. After a restart L3 receives the active tasks and resumes
faulted tasks only after verifying that their actual cause is gone. Operator-decided short-term
work waits as a planned task; GitHub issues hold the long-term backlog.

The aggregate gate reads `config.machine_wip()`, the persistent machine override or
`config.WIP_PER_MACHINE` (80). Fresh and resumed launches share this cap with running adversarial reviews
and [routine runs](#routines); blocked tasks consume no capacity. One machine launch lock serializes admission through worker binding so simultaneous launches
cannot overfill it. Eligible ready resumes across all registered projects precede fresh dispatch. Operator waits,
faults without verified recovery, future due times and unavailable engines reserve no slots and do not
hold eligible work. A resumed worker already launched and recorded in its recovery claim counts toward
capacity while its task binding is recovered; it remains the same worker. Existing sessions, merge holds,
planned dependencies and engine/quota gates remain authoritative. Status reports the machine limit and
running counts per project, with no project caps.
Task inspection computes admission waits for tasks without a launched worker; it does not
project the shared project queue hold onto individual tasks. Running workers retain their
independent merge holds, and blocked tasks retain their question and fault state.
Stored project `wip` overrides impose no limit, and project registration and settings expose no cap.
Resume readiness probes the existing setup lock without waiting; setup contention in one project
does not hold fresh work in another project, regardless of their daemon tick order.

`alt machine set --wip N --reason '…'` and `--unset-wip --reason '…'` use the settings request
and receipt implementation, with operator-only authority and a positive integer machine cap;
80 is a default, not a fixed ceiling. The machine override lives in `$ALTITUDE_HOME/settings.json`;
its `wip-request.json` and `events.jsonl` live alongside it. The machine event kind is `machine-set`.
Altd drains machine requests before project ticks, including when no projects are registered.
Identical pending requests and completed retries whose machine setting still matches reuse the
request and event. CLI and HTTP registration, removal, engine pins and operational settings serialize
registry writes under the project and registry locks.
Re-registering a project is the operator's deliberate act, and the last registry write wins.
`alt machine set --projects-folder PATH` and `--unset-projects-folder` use the same operator-only
request path for `projects_folder`, an existing absolute directory; `machine show` reports it.
The web app's machine settings write through the same request and `machine-set` event and answer
from `GET /api/machine` (`operator`, `incident_repository`, `altitude_repository`, `terminal`):
`POST /api/projects-folder`, `POST /api/operator-name` for `operator_name` (one line of at most 80
characters; empty clears it) and `POST /api/incident-reports` for `incident_repository` (`null`
stores `false`, publishing off; a repository is normalized to `owner/name` and saved only after
`gh repo view` in the signed-in GitHub CLI sees it). The operator seam is `config.operator_name()`:
the saved name, else `ALTITUDE_OPERATOR`, else Git's global `user.name` read once, else none, where
screens say “you” and records use `config.operator_label()`. Task records, messages, events and queue
rows store the operator's authority identity as the fixed token `config.OPERATOR_ACTOR`, independent of
the name; CLI text, queue wait kinds (`waiting-operator`) and screens render it through the label.

Settings take effect without a PR, service restart or free task slot. Lowering the cap preserves
running workers; launches wait until the machine running count falls below it. Reset removes the
machine override and restores its default. `alt machine show` inspects the active cap, default,
override and request receipt; see [inspect/set/reset examples](CLI.md#concurrency-limits).

Auto preference tiers use the same reason-bearing operational path:
`alt project set <name> --routing 'codex,claude:fable>claude:opus' --reason '…'`, or
`--unset-routing --reason '…'` to restore defaults. The operator and that project's L3 can change
them without a PR, restart or free task slot. Altd applies the request on its next tick and records
actor, reason and outcome. The project setting serves both L3 and fresh L2 routing; it changes no
explicit pin or existing L2 attempt. The L2 provider priority (`--l2-preference <engine>`, or
Auto with `--unset-l2-preference`, also in the project's Settings page) lifts one engine's options
above the others for fresh L2 only; L3 keeps the tiers as written. `--l2-engine` (Only) keeps tasks on
one engine and `--l3-choice` sets L3's model choice, its one engine setting. [CLI examples](CLI.md#automatic-routing-preferences) cover
single-model accounts, different orders and ties, and the provider priority.

Project removal is L3 detachment: one operator action through `config.remove_project`, shared by
HTTP and CLI. It unregisters an idle project and ends its coordination. Queued, running, blocked
and reported tasks must finish or be rejected first, including blocked tasks with stopped workers.
Archived records are also inspected for live workers, dispatch/resume claims and unfinished task
operations. A project activity file lock fences removal against L3 turns, broker calls, report
handling and timer processing across processes. Task creation and operator queue admission
recheck registration under the project state lock, so removal cannot abandon racing work.

Removed projects leave the managed list; their broker is closed (CLI removal is noticed by the
next tick, with broker calls refused immediately). Repository files, remaining worktrees and the
project's Altitude directory stay on disk. A name that differs from a registered project's only by
case is refused on every host, since both would share one runtime folder on a case-insensitive disk
(the macOS default). Registration with the same project name and repository
attaches L3 again and restores saved conversations, task archives, provider sessions and queued
messages. The startup path drains the saved FIFO before any introductory turn. Registration
reports restored conversation history so First run waits for successful registration and opens
that history without interpreting its old replies or errors as a fresh startup result. Reset
remains a separate session rotation within a managed project.

`STATE.md` is regenerated from active task records and the project's incidents that are not closed.
Incident reads project current status, evidence, cause and issue from the existing Markdown record,
excluding amendment history. Each open incident gets one line, newest first and at most ten: its
status and title with its issue link, **no issue**, or **issue pending** with the reason, followed
by up to 300 characters of current evidence; an unreadable record reads **evidence unavailable**
instead. Closed incidents and full evidence stay available through `alt incident list`.
No second prevention record or automatic action is created. Archived tasks and full incident history remain audit
evidence available through inspection commands.

## Isolation and landing

Each task uses the isolated worktree path `.claude/worktrees/<slug>` and branch `worktree-<slug>`,
based on the exact fetched `origin/main`, or, for a task made from an agent chat's changes, on the branch made at
its recorded start commit ([agent chats](#agent-chats)). Dispatch and resume switch a clean worktree found on another
branch back to its task branch and record a `task-branch-restored` event naming the branch it left, which
keeps its commits; a dirty worktree, detached HEAD or missing task branch faults `task-git-provenance`
with the Git step that recovers it. Ownership belongs to the task and PR; commit messages,
including historical labels, are ordinary text. Before mutation or publication, trusted landing
validates the current worker, registered checkout and repository, and excludes PRs or branches
owned by other active tasks. Protected branches require the guarded landing path. Landing
fetches the base, commits the selected index, pushes, opens the PR,
pins the current base/head pair, waits for configured checks, and merges only
when requested and allowed. After the push, the fetched branch tip must equal the pushed head;
a different tip refuses immediately. A lagging PR view is re-read every two seconds within a
30-second polling window before pinning that head. A task may carry an explicit merge hold for operator review.

A task worker writes its project's shared Git folder (objects, refs and its own worktree's state), so
altd, which runs Git outside any sandbox, takes every path from its own layout rather than from the
worktree's `.git` file or the task record: the worktree is `<repo>/.claude/worktrees/<slug>`, the common
folder `<repo>/.git` and the worktree's record `.git/worktrees/<slug>`. `git_policy.task_git` refuses
unless the common folder, `.git/worktrees` and the record are plain folders and the `.git` file names
that record, and Altitude's Git commands in a worktree run with `GIT_DIR`, `GIT_COMMON_DIR` and
`GIT_WORK_TREE` set to these paths and replacement objects off. Validation clones a task's commit
from the common folder. Workers on both engines keep the common folder's `config`, `config.worktree`,
`hooks`, `info`, `modules`, `worktrees`, `commondir` and `gitdir` and their record's `commondir`,
`gitdir` and `config.worktree` read-only (`git_policy.worker_git_access`): the repository's settings,
hooks and worktree records stay as the operator and Altitude wrote them. A worker's launch first creates
the optional parts a repository lacks (empty `hooks`, `info` and `modules` folders and empty `config.worktree`
files, which Git reads as absent), because a sandbox holds only an existing path read-only. Altitude's repository-level
Git commands outside a task worktree (`git_policy.git_env`) name the Git folder explicitly too, so a
redirection a worker plants in a shared folder does not steer them. Workers
create branches with `--no-track` and pass a one-off setting with `git -c`; landing pushes without
recording an upstream.

A finished task's worktree and branch are retained only while something needs them. Every maintenance
tick revisits each done or rejected task that is not yet cleaned up. Its worktree is removed once no
worker runs, the tree has no uncommitted changes and every commit in it is on a branch or remote. Its
branch is deleted once the tip is on `origin/main` or a verified PR merged it; a branch holding commits
found only on this machine stays and the `cleanup-worktree` event names it and its unmerged commit count.
A running worker or failed fetch retries on the next tick; any other refusal is a recorded note, not a
fault. A done task's cleanup then fast-forwards a self-deploying checkout.
Merging invocations share one ordered merge line per repository, first ready, first merged (#777). A
candidate publishes and runs its check once: current main is merged into the task branch before the
push when needed, preserving adopted ancestry, and a head already published with its own check is not
integrated again. While it waits for that check and for owner assessment, it is judged on its own head:
the head commit's check runs, bound to the PR, the hosted workflow and the head tree, and review
evidence by head, context and proposal. Main moving meanwhile neither interrupts nor stales it.
Nonmerging invocations stop there with the PR published. A ready candidate (passed check, current
reviews, no hold or a validated approval) joins the line and waits, reading only. The head of the line
holds the turn: it rechecks ownership, holds, approval and reviews, merges on its existing check when
it already contains current main, and otherwise integrates main once, pushes, waits for that fresh check
(and any reassessment the new head needs) and merges under the exact base/head checks. Only a merge
made outside the line moves main during a turn; the head then integrates again within it. A
conflicting integration is aborted with local work retained for owner reconciliation. Head or PR
identity movement refuses; adopted PR pushes remain fast-forward only. Task-state locks remain short,
so messages and Stop stay available.
The line lives on origin as `refs/altitude/merge-line`, a ref outside branches and tags pointing to a
parentless commit whose message lists the entries (PR, per-landing token, ready time, heartbeat).
Every change is a compare-and-swap push, so every owner on every installation shares one order. The
head heartbeats once a minute; any landing removes a head silent for five minutes and starts the next
entry's turn. The head's branch push travels atomically with its heartbeat, so a landing that lost its
turn cannot publish. A head paused for five minutes inside the merge call itself can still merge
its validated head out of order; this repository's strict up-to-date rule refuses it once main has
moved, and a turn that finds main moved integrates again. Re-running a PR's landing takes over its entry; the earlier landing ends unmerged.
`--wait` bounds each phase separately, an hour by default: the first check and assessment, time in the
line, and the turn. The wait polls after 15 seconds, doubling to once a minute. The account's
landings, workers and coordinator share one hourly GitHub GraphQL budget, so landing reads only the exact
check evidence through GraphQL, once per poll. The PR's state, refs and head come from GitHub's REST API,
which has its own quota; the line is read through Git. When that evidence reports no checks, a REST read
of the head's check runs and statuses must agree before the local-suite gate applies. GitHub refusing a
`gh` command for a rate limit is a wait within the same phase bound. The refused command did nothing,
so a minute later landing starts the candidate again: it re-reads the PR, checks, ownership, holds and
review before any merge. A refusal once the merge command has run first asks GitHub whether the PR
merged at the candidate head; if it did, landing records that merge instead of publishing again. A PR
it created is read back by its URL, never created twice. A refusal with a minute or less left in the
bound ends the landing. A Git command that reaches origin and fails on the network is tried again after
15 seconds, doubling to once a minute, within five minutes of its first failure, the time the line keeps a
silent head's turn, so a flaky connection neither ends a landing nor costs its place; refusals are not
retried. Polls hold the task review lock only for local review reads; network reads and sleeps leave
review requests available. A notice names every stale review and subject with the assessed/current values of its
changed evidence, so the owner assesses each request once; its own explanations keep them current.
Proposal and changes findings remain separate; a changes assessment does not retire a proposal.
Final candidate, checks, ownership and hold validation repeats after assessment.
Missing or unfinished review, head or PR identity movement or ownership loss refuses.
No review identity or disposition is automatically transferred.
The final review/context check precedes recorded approval application, preserving holds on review refusal.
A landing waiting on checks, assessment or the line ends with the owner session that started it: the
worker's end stops its job, and the landing handles that SIGTERM by leaving the line, the candidate
published and unmerged, and an automatic notice for the owner's next turn naming the PR and published
head. A landing killed outside its waits leaves its entry for at most five minutes at the head. Dry
runs and nonmerging invocations never join the line. CI runs and hand-run suites do not take part in
it; projects without hosted CI run their full local candidate suite inside the turn. This repository's
strict GitHub up-to-date required-check rule still guards every merge, including one made outside the
line. No GitHub settings change is needed.
This repository requires its GitHub-hosted PR `check` to run the full `make check` suite. Owners
and helpers run relevant tests during development; landing does not repeat the full suite locally.
CI proves its tested merge tree equals the PR head tree. Landing requires that successful PR
check on the current head and verifies that the head includes current main. A head missing
current main is integrated at its turn in the merge line and needs a fresh PR run on the new head.
Altitude serializes merges across installations, rechecks identity and holds, and verifies the merged
tree against the tested tree. Missing, pending, failed or stale CI blocks; CI outages have no local bypass.
The task hold and review protocol govern Altitude merges. Concurrent hosted runs are bounded by
GitHub capacity: 11 jobs per check against the plan's 20 concurrent jobs. A push to main whose tree a
same-repository PR run already tested in full, with the CI setup unchanged, reuses that run instead of
running the shards again. A new PR push cancels that PR's superseded check run; main and manual runs
are never cancelled.
See [policy, evidence and activation](DEVELOPMENT.md#ci-and-candidate-identity).
Planned file lists guide coordination without limiting edits or landing. The owner stages selected
files or hunks and reviews `git diff --cached`; `alt land` commits exactly that index, preserving
unstaged and untracked work. Before pushing, the owner reviews all outgoing commits and the complete
PR diff for scope and privacy, including intermediate content absent from the final tree.
Parallel tasks may edit shared paths; their briefs
name those paths and ask owners to rebase onto main before landing and keep shared-doc edits to
their own sections. For conflicts requiring manual reconciliation, the owner updates the task branch;
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
A task PR closed without merging is not reopened: the next `alt land` opens a fresh PR from the
task branch and records the closed PR as the preceding delivery evidence.

The task's `delivery` records the current PR number/head, base SHA, publication branch and precise
timestamp; a publication in progress has no number/head. `prs` retains every delivery number, and
`delivery` events retain publication receipts and the preceding PR/head/merge evidence. PR creation
is recorded before check polling. Each new PR runs the existing candidate checks and review/hold
gate; a PR-specific release restores the original hold requirement for the next PR. L3 judges
whether the original decision covers that PR before applying its own bound release. An explicit
renewed hold changes its generation; a later task-wide release stays effective. Adopted PR receipts remain in history when ordinary task work
continues after their merge. No-work retries preserve the current receipt and publish nothing.

Starting another delivery or claiming a resume invalidates previous completion verification.
Reported owners with recorded open-PR evidence expose the ordinary task composer and Resume action.
Message or coordinator-resume admission confirms that a recorded PR is still open through the existing
GitHub adapter, preserves the report and verification in a `report-superseded` event, and moves the
same owner into the existing blocked/resume path. No new attempt, provider, worktree or PR is
created, and merge holds and original decision evidence remain intact. A failed PR lookup refuses
the action before saving its text; after acceptance, wake failures retain the saved inbox request.
L3 can also message or resume a reported owner whose current verifier verdict is `contradicted`,
without an open PR. The verdict's owner identity and delivery must still match the task. This
coordinator correction uses the same report retirement and resume transaction after merged delivery;
it leaves the report file, decision evidence and unfinished acceptance with the original owner.
The ordinary operator composer and resume admission retain their open-PR requirement.

`report_after` marks follow-up work, and verification carries its owner identity, worker start and
block identity. Messages arriving at report handoff invalidate completion evidence; pending inbox
messages return to the ordinary resume path. A report whose sections do not have their schema's shapes,
such as PRs listed as bare numbers, is `contradicted` with each such field named before anything else is
read, so the owner corrects it like any other contradicted report; `alt task report` names the same fields
through the same check. The verifier reads each reported PR through GitHub's REST
API. If GitHub refuses that read for a rate limit, the task stays as it is and the next tick verifies again,
without a verifier fault. A read that gets no answer from GitHub (gh's time limit, or a connection, DNS, TLS or
gateway error) also waits for the next tick, and becomes a verifier fault only once the same worker's verification
has failed that way for five minutes; any other gh failure, such as bad credentials or unparseable output, faults
at once. Report application, verifier faults, stranded-report
recovery, automatic completion and report-turn receipts reject superseded work. A report that needs
L3 waits, with no turn or chat row, while routing has no available L3 option; the stranded-report scan
delivers it once one is. A report turn that runs and fails leaves `l3_handled` unset and retries after
1, 5, 15, then 60 minutes; a changed report owner is delivered at once. The existing report
file remains readable until replaced, but cannot verify a later continuation. The shared L2 persona
and runtime resume prompt require fresh verified reporting even when guidance is already incorporated
and no new work is needed. The owner preserves every delivery, exact remaining scope and holds;
chat acknowledgement does not complete a turn. Done/archived/rejected
tasks remain outside continuation: archived worktrees may have been removed, and restoring their
execution context and ownership requires a separate product decision.
Report freshness includes the current delivery timestamp. Verification requires all recorded PRs,
the current published head on GitHub and in the clean worktree, and reported merge SHAs naming
GitHub's merge commit in full or by a prefix of at least seven characters; a clean worktree
reconciled onto main after its merge also has no unpublished work. Pending work, older reports and
a raced delivery cannot complete the task. Completion verifies a reported delivery whose recorded
verdict is not ok against GitHub again and records the fresh verdict in a `report-reverified`
event, so a merged delivery reaches `done` without a new owner turn; an unmerged or mismatched
delivery still cannot complete. A review finding left `open` on an unblocked report is the owner
handing an observation to coordination: it blocks plain completion, and L3 completes the verified
delivery by naming where the finding is tracked (`--findings-tracked`), which the done event and
archived digest record for every open finding. Recorded deliveries use the report
path even when the local branch has no remaining diff. Merges continue activating independently
of owner completion through the existing deployment observation.

L3 or the operator can update expected files with `alt task paths`; it replaces the `paths` list
and records the actor and previous list. This is coordination metadata, not a permission grant or
resume action. Owners need no file-list update to finish the authorized objective, including recovery;
the objective and explicit exclusions remain binding.

Issue intake fetches a single explicit project-local issue once and retains its URL and acceptance
text in `request.md`. Agreeing local links and explicit `GitHub issue #N` references select that
parent; conflicting local references refuse intake. External issue URLs remain contextual brief
text and trigger no external fetch. Intake does not infer closure authority or scan backlog. The
owner compares the full issue scope and required acceptance with cumulative authorized deliveries. For a complete resolution it
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

For assigned existing external PRs, `alt land --adopt-pr N --expected-head SHA --reason '…'`
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
target changes refuse. Landing, resume ancestry validation and recorded hold approval use the active
receipt. The history grants no authority over unrelated PRs.
If recorded approval released the previous PR's hold, the next adoption restores the original
requirement for a separate PR-bound scope judgment and release. A later explicit task-wide release
remains effective.

The receipt binds PR number/URL, origin, base, original branch/head, actor, attempt, reason and time.
The original head must remain an ancestor during landing and resume.
The task keeps its local branch and publishes a fast-forward refspec to the original PR branch;
adopted pushes never retry with force. Landing incorporates main into the task branch with a merge
commit, preserving the adopted branch history. Adoption cannot be widened to a later external head.
The existing PR is reused, outstanding required reviews or requested changes and drafts block merge,
and the live task owner/hold/active receipt are rechecked before merging. The fetched base branch
is authoritative; lagging `baseRefOid` metadata does not replace it. Check evidence confirms the
current GitHub base target/head and exact candidate association, with real movement refusing merge.
Required checks from branch protection and active rules remain mandatory, including missing or
skipped checks. Completed check runs that GitHub identifies as nonrequired and that ended skipped,
neutral or cancelled are ignored without interpreting workflow conditions: they neither block nor
count as a pass, so a code-scanning summary that completes neutral before its analysis starts costs
no landing attempt. Failed and pending checks still block, including nonrequired checks; pending
checks use the existing wait. Unknown requiredness or ambiguous candidate association refuses
delivery. At least one check must actually pass under the hosted gate.
For other projects without CI, the full local suite runs on a clean single-parent squash
candidate, for adopted PRs too. The suite is one `--test-cmd` argv
command run without a shell. Workflows on either pinned side keep the hosted gate, so a head that
deletes them cannot select the local suite. A dry run reports the fetched base, the exact head or
the staged tree, per-side workflow detection and the gate it would use, and names what only a real
landing settles; it commits, pushes, opens, tests and merges nothing. Adopted PRs squash like
ordinary delivery and request no branch deletion. See the [supported workflow](CLI.md#adopt-an-existing-pr)
and [dry run and gate selection](CLI.md#dry-run-and-gate-selection).

Operator merge decisions originate in task chat, UI choices or project chat. The owner applies a
task-chat approval with `alt land --merge --approval`; L3 applies a project-chat approval through
[`hold-merge --approval`](CLI.md#recorded-merge-approval). Each cites the original source and current
PR/head with its scope judgment and reviews later corrections.
It interprets permission, conditions and revocation; altd validates provenance and recorded boundaries.
Design feedback, implementation-only permission and coordinator relays cannot authorize merge.

The shared source reader verifies original operator authorship and unique identity. Project sources
use original user/chat turn IDs. Missing, corrupt or duplicate evidence refuses release.

Under the project lock, altd verifies the latest hold event against task state and binds the current
hold ID to its original recorded requirement. Approval must follow that requirement. Explicit hold
changes create a fresh ID and matching event; interrupted writes and inconsistent evidence refuse.
A follow-up restores the original ID and needs its own release. L3 can apply the same source when
its scope covers that PR. Approval covers the approved outcome, not a commit: a new head from routine
integration with unchanged approved content keeps it, while current-candidate checks, review, provenance
and PR identity are still verified for that head.

Altd reads an open, non-draft, same-repository PR targeting main from the project's origin. The
publication branch and supplied head must match; recorded active/adopted PR identity also binds the
number, URL and branch. The owner reviews the integrated result and completes current-candidate checks.

One task write clears `hold_merge` and saves `merge_approval`: original source/author/time,
question/revision/option, hold ID/event/time, PR URL/head and scope reason. A `release-merge` event
carries that receipt; local refusals record `merge-approval-refused`. Release leaves worker and
question state intact. Direct `--off` is operator-only.

Fresh dispatch fetches `origin/main` and creates the isolated task worktree from that immutable SHA.
Resume validates the existing owner's worktree, branch and adopted ancestry without a remote fetch
or deployment gate. Neither operation moves deployment HEAD, index or working content. A project's
deployment advances separately after delivery and on daemon ticks, through the guarded fast-forward
of clean main. Dirty, diverged, ahead or off-main deployment remains untouched and reports its own
failure; otherwise valid isolated tasks continue. Publication retains its current-candidate checks,
ownership boundaries and review holds.

A failing `git fetch origin main` for self-deploy, or a checkout fetch that still disagrees with Altitude's copy
of its repository, raises `git_policy.FetchError`. The tick logs each
such failure, and each command of the tick that outlives its time limit on a busy host (#724), and
retries it on the next tick. It raises the system fault (`self-deploy`, or `tick` for the setup check
and the rest of the tick) only once the same step has failed for five minutes without a success in
between, carrying the latest error. The post-delivery fast-forward returns a note for a failed fetch
and leaves the retry to the tick. Every other self-deploy failure, including a dirty, diverged, ahead
or off-main checkout and a failed fast-forward, raises the fault immediately. The tick files an unchanged
refusal once and then waits: later ticks with the same error leave the fault record as it is, until the step
succeeds or its error changes. The failure start and the filed refusal are held in daemon memory, so a restart
starts a new grace period and files a remaining refusal again.

Base fetching tolerates one competing update of the same remote-tracking ref across linked
worktrees, including ordinary Git commands and landing fetches. `git_policy.fetch_origin` uses
Git's C-locale diagnostics to recognize the exact stale-old-value compare-and-swap error for its
requested ref, only when no other error or fatal diagnostic accompanies it. It performs one fresh
fetch and requires success before returning a commit SHA. Other fetch failures and any second
failure propagate normally; no task-fault retry or cached-ref success inference is involved.
This handles fetch-time contention without introducing a second Git lock protocol or changing
checkout guards, landing's current-candidate checks, or activation boundaries.

The reference-transaction hook allows writes that retain a protected ref's current logical tip,
including `pack-refs` writes whose old object ID is zero. Loose-ref pruning is allowed only when
its nonzero old tip matches both the current ref and its committed entry in the common
`packed-refs` file. Git's files backend prepares genuine packed deletions as zero-to-zero updates
before removing a loose copy; these remain blocked. Packing, repacking and fetch-triggered garbage
collection therefore preserve a lagging main and permit its subsequent guarded fast-forward.
Real-Git regressions exercise these transaction forms and deletion refusals in loose, packed and
mixed storage, including linked worktrees.
The automatic-GC fixture creates its two packs with `repack -a`, which owns its revision input;
the regression also runs with an open caller stdin and captured output as in local landing,
alongside real issue-close CLI/API validation. The shared CLI fixture supplies explicit empty input.

Dirty-checkout recovery uses `alt task preserve-checkout <slug> --reason '…'`, a durable daemon
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
The [recovery procedure](CLI.md#dirty-checkout-recovery) leaves resumption explicit. An unlaunched
task with a saved `main-unpushed` fault can requeue on explicit resume independently of deployment
recovery; its fresh dispatch still requires a fetched current base and a valid isolated worktree.

Every worker is an untrusted process in its worktree, whichever engine runs it. Its only door into
Altitude is the `alt` CLI; the backend validates each command against the task record under the
project lock. Neither engine's worker reaches the user service manager or sudo. Anything outside the
workspace that the operator permits (a service change, a deploy, a publication, an external write) runs
only under the one recorded [operator grant](CLI.md#operator-grant): altd executes each command as the
operator outside the worker sandbox and records it. The grant reaches the engines' own guards through the
engine seam: a Claude owner's session settings allow its own `alt task run` call ahead of the auto-mode
classifier, while altd refuses that call without a current grant; a Codex owner asks for no approvals. A
granted command runs with the operator's access and is trusted to stay inside the approved purpose; the
record, not command parsing, makes it accountable. An operator's answer to the owner's purpose
question is recorded by the running owner from its task chat or by L3 or the operator and verified
against that question revision. Alternatively, L3 applies an original operator approval from the
same project's chat to the owner's policy request: `alt task block --standing-policy <heading>` publishes
the paragraph under a unique, single-paragraph heading in the registered checkout's
`refs/heads/main:AGENTS.md` as an L3 question marked with that heading, and the grant requires that
current open marked question, the paragraph's citation of the approval, and a still-matching paragraph,
apart from whitespace, pinned to its commit. The standing path fences
the observed attempt and stores the original approval, policy text/commit and question revision;
task branches and dirty policy files supply no authority. L3 likewise grounds the owner's current open
unmarked L3 question in an earlier original operator message from project chat or the task's own chat that approved the step
before any question asked it; the grant's purpose is that whole message, so none of its conditions is
dropped. L3 judges later corrections, revocations and the message's conditions; neither shell commands
nor chat consent are classified automatically. A withdrawal names the approving message: it revokes
every grant in the project resting on it and, kept in the project's event log, refuses any later grant
citing it, while plain revocation stays cleanup. Switching standing purposes requires revocation of the
existing grant.
Either grant opens `POST /api/task/run` for the running owner's current attempt. altd writes the run's row, then
executes the command in a job of its own through `engines.machine_command`, with the service manager
reachable (no Seatbelt profile on macOS, since launchd refuses service control to sandboxed processes) and
the owner's task identity, one at a time, bounded by `MACHINE_COMMAND_TIMEOUT`. Because the command reaches
the service manager, it can also stop or reconfigure its own job: the time limit bounds an ordinary command,
not one that works against it. The unit writes its output and exit status to the task folder itself, and altd completes `machine.jsonl`
and adds a task event and a project event per command. The ledger is also the one-at-a-time record: at startup
altd follows every unfinished row's unit to its end (`server.settle_interrupted_machine_commands`) and completes it
from the saved status, or as uncertain without one, and the owner's CLI reconnects with its request id for the result. The owner, L3 and the operator can revoke
the grant; nobody can widen it. Both engines share the verb; only the launcher is host-specific.

The commands a task's owner sends to altd over HTTP (`POST /api/task/run`, `task/validate`,
`task/terminal`, `task/review/run` and its record changes through `task/door`) are its own task's door. altd
admits them by the caller, not by the machine key: the body's project, task and attempt must name a running task's
current attempt, and the client end of the connection must be held by a process in that attempt's worker job
(`terminal.owner_connection`). A record change is admitted for the current attempt also after the task left
running, while that worker job lasts, so its stop checkpoint and end-of-session notices still arrive. Any other
caller, including one holding the machine key, gets the same 403 before anything is queued or run, and the owner's
`alt` sends no key for these commands.
Ordinary candidate checks need no grant; the fixed Mac guest browser lane requires its standing VM-policy grant:
`POST /api/task/validate` lets the running owner's current attempt, from a process in its own worker
job, run one command against a throwaway clone of its committed `HEAD`. On Linux the command runs in a
disposable rootless Podman container that altd starts from its own deployed image, with fixed flags and
limits. On macOS it runs as a job under the platform's validation Seatbelt profile, stricter than a
worker's: it writes and reads only its own folders in the run area, reads nothing else in the operator's
home, mounted volumes or the shared temporary folders, reaches neither Altitude's port, other processes' Unix sockets,
the keychain, the Simulator service, LaunchServices nor Apple Events, and launchd refuses it service control. The runner's storage sits beside Altitude's home, outside every worker's writable roots. The image prepares pinned Playwright
Chromium and WebKit with both sets of system libraries, so required emulated-iPhone validation
uses the supported runner without host package installation. Image corrections take effect after
normal source activation and require a successful browser check through that effective runner;
testing a nested candidate image alone does not establish recovery. The run is recorded like a machine run with
purpose `validation`, its commit and tree, host OS and architecture and isolation, and results reach the
task folder without following links. Cleanup precedes the record, so a run whose cleanup did not finish
is recorded as `cleanup failed`, never as a success. The worker's own
confinement is unchanged. The Settings switch **Validation runs** (on by default, kept in the runner's
storage so a worker cannot turn it back on, and absent where this computer cannot run them) stops running
runs and refuses new ones. At startup, before
admitting a run, altd stops abandoned runs, retains their logs and results, records them as interrupted
and removes their scratch files. Failed evidence delivery retains the original area and keeps the
runner closed for recovery. Validation holds the shared restart fence through its bounded execution,
evidence recording and cleanup, and refuses admission while activation, or an installed release's offered
automatic update, is pending. On a Mac,
`--simulator` adds a disposable iOS Simulator iPhone (`altitude/simulator.py`): altd creates it in a
private device set in the run's area and removes it afterwards, and the run, which stays refused the
Simulator service, reaches only that phone's Safari pages through altd's filtering relay, and two fixed native
walks (Add to Home Screen, and a profile through Settings) that altd builds from its own code (`altitude/walks/`)
and runs on that phone with Apple's UI testing. See the
[validation runner](DEVELOPMENT.md#validation-runner).
Claude Code runs as a foreground CLI inside an independent job with Altitude's
hooks for inbox delivery and telemetry. That job, like every task worker's on either engine, runs under Altitude's
own sandbox, Seatbelt on macOS and bubblewrap on Linux (`platform.job_command`); a Codex worker runs `codex
app-server` with Codex's own sandbox off and approvals never, so the same sandbox confines both engines. A job in it
may signal only processes in its own sandbox, never its supervisor,
and write only under its own roots (a task worker's worktree, the worktree's Git directories except their
[read-only parts](#isolation-and-landing) and its scratch folder; a coordinator turn's runtime folder and Altitude's
home), the job's own engine folder and its own temporary folder, and never reads
Altitude's credentials, the common browsers' profiles, the operator's stored credentials (below), the
operator's own Codex folder, Claude folder and Claude state file (`engines.operator_places`), apart from the entries
its launch reopens read-only, the [repairs'](#repairs) records (`repairs/` in Altitude's home), or the
[agent chats](#agent-chats) (`config.CHATS`), apart from a chat turn's own engine and scratch folders. On macOS it
neither reads nor writes macOS's [private places](DEVELOPMENT.md#macos-private-places) or the launcher's job folders,
which hold each job's piped input and relay its output; launchd refuses service control to any sandboxed process. The Seatbelt profile also refuses
the per-user services that start programs as the operator's account outside it: Apple's Simulator service,
whose devices run what any client asks, and LaunchServices, which opens apps; it also refuses sending Apple
Events, which script running apps with only macOS's Automation consent in the way, and the keychain services, which
hand the sign-ins command-line tools keep there to any program that asks. It reaches Unix sockets only
inside its own roots and temporary folder, a coordinator turn's broker socket and the system's name and log services,
never one another program keeps in the shared temporary folders. On Linux the job sees only its own processes and a
device folder with its own terminals, and cannot write the control groups. Its per-user runtime folder is empty, so
the session bus, the user manager and the sockets the desktop session keeps there are out of reach; a coordinator
turn gets back its own folder and its project's broker socket. Its /tmp and /var/tmp are its own temporary folder.
It shares the network, which `alt` needs to reach altd, and with it the abstract Unix sockets; where the kernel
offers Landlock's abstract-socket scope (Linux 6.12 or newer), the sandbox starts the job's command under it, so the
job reaches only abstract sockets its own processes made. On an older kernel the job still starts, and the agent
sandbox rows in setup and `alt doctor` say abstract sockets stay reachable. Its
home is a fresh folder holding the home's entries read-only, so a new name there lasts only as long as the job. A
sandbox cannot start inside it where the distribution restricts user namespaces, as Ubuntu 24.04 does. Every Claude
L3 turn also runs as a job of its own under the same sandbox.

Every job, on either engine, has a temporary folder of its own as its TMPDIR: a fresh folder with an unpredictable
name in /tmp, short enough that a socket made in it stays within the platforms' path limits, so a job neither meets
nor leaves sockets and files in the shared temporary folders. The user manager (Linux) or the job's supervisor
(macOS) removes it when the job ends, also after an altd restart. A confined job thereby loses the desktop session:
its windows, the SSH and keyring agents, terminal multiplexers and editor servers.
Still reachable from a job: system-wide sockets under /run, among them a container engine's socket when the
operator's account may use it, and sockets kept inside the home folder; on Linux, abstract sockets for every job on
a kernel older than 6.12. A Codex coordinator turn's own profile refuses sockets in the shared temporary folders too.

The operator's stored credentials are the places in the home where other programs keep
sign-ins and keys (`platform.credential_places`): `~/.ssh`, `~/.gnupg`, the GitHub, hub and GitLab CLIs' folders,
Git's credential files, `~/.netrc`, the AWS, Azure and Google Cloud CLIs' folders, `~/.kube`, Docker's and Podman's
registry logins, the npm, PyPI, Cargo, RubyGems and Terraform token files, rclone's folder, Vault's token, the
1Password CLI's folder, `pass`'s store and the GNOME keyring's and macOS keychain's files, each at its default
location. Each is hidden from every job in Altitude's
sandbox whether present or not, also where it leads when it is a link, as the engines' places of the operator's own
are; a Codex coordinator's profile denies the ones present when its turn starts. Only the named files are hidden where
a folder also holds tools (`~/.docker`, `~/.cargo`). On Linux a place created while a job runs, inside a folder the
job's home shows read-only, stays readable to that job until it ends. Shell start-up files, Git's settings and shell
histories stay readable. A confined job therefore has no SSH key, GPG key, cloud or cluster sign-in, or registry
token of the operator's: it reaches no host over SSH and signs no commit, and the operator's private registries
named in the home answer it unauthenticated. On macOS a Codex coordinator runs under Codex's own profile, which
decides its keychain access; that turn has no network of its own. A task worker reaches GitHub over
HTTPS (`engines.GITHUB_GIT`): Git's environment rewrites a GitHub remote named `git@github.com:` or
`ssh://git@github.com/` to HTTPS and clears the operator's credential helpers for GitHub in favor of the GitHub
CLI, which signs in with the worker's `GH_TOKEN` and keeps its settings in the task's scratch folder
(`GH_CONFIG_DIR`). The worker's fetches and pushes, landing's among them, therefore carry the GitHub CLI sign-in's
permissions; an operator Git setting that sends GitHub's HTTPS address to SSH (`insteadOf` or `pushInsteadOf`)
takes precedence, and the worker's pushes then fail. Repairs run outside every sandbox and keep the operator's full
access.

Every Claude job Altitude starts has a Claude folder of its own (`CLAUDE_CONFIG_DIR`), so no job writes
the operator's `~/.claude`, `~/.claude.json` or the GitHub CLI's configuration, which another run or
Altitude's own `gh` calls load. Altitude's sandbox hides the operator's `~/.claude`, `~/.claude.json` and every
copy of it Claude leaves beside it, each also where it leads when it is a link, from every job it confines, on
either engine; on Linux the sandbox hides the copies present at launch, in a home that holds only the entries present
then, and on macOS also any made later. A Codex coordinator, which runs in Codex's own sandbox, has its profile deny
the ones present when its turn starts, apart from any inside a place the profile refuses whole; on Linux Codex
hides a file by covering it where it lies, so once the operator's own Claude replaces its state file during the turn,
the new file is readable there, while the folder, with the sign-in and conversations, stays hidden.
A Claude job's launch reopens read-only only what that job reads, named below; hook scripts, a status line command,
imports or a Claude program kept elsewhere in the operator's folder stay hidden, so a job runs without them. A task worker's folder is `l2-engine/claude` in its task folder, the one
part of Altitude's home the worker writes, and keeps the task's sessions across turns and after the task is
archived; a coordinator's is one per project in the persistent coordinator folder beside Altitude's private
configuration; a review's lives and ends with its runtime. The engine driver, already inside the job, creates
the folder's contents and links the operator's `settings.json`, `CLAUDE.md`, skills, agents, commands, plugins
and output styles into it, which the launch of a worker or coordinator reopens read-only; the folder's own
state file starts empty, and a review, whose Claude runs restricted, links nothing. A session resumed for the
first time in its folder is first copied there from the operator's folder, where Claude kept every session
before jobs had folders of their own; the launch reopens only that session's records for the copy. Workers read
the project's notes from the operator's folder (`autoMemoryDirectory`), which their launch reopens read-only. Every Claude job
signs in with Altitude's own subscription token (made by the sign-in below and kept in the private access
store), handed over in its launch input like the GitHub token below and exported by the driver as
`CLAUDE_CODE_OAUTH_TOKEN`, so a job never reads or refreshes the operator's sign-in; a review, which
runs without the driver, reads the same environment from the first line of its input. Without that
token Claude reports itself signed out, which is an ordinary [sign-in lapse](#engine-integration-boundary);
two weeks before its year ends, counted from when it was saved, each project's conversation gets one
heads-up line to renew it.
Altitude makes that sign-in itself (`engines/token_setup.py`) for an engine whose runs take a token from the
operator (`TOKEN_SETUP`), through `GET` and `POST /api/sign-in/<engine>` from a paired browser or with the
machine key (`alt sign-in`); workers cannot read the key. Starting runs the engine's setup command
(`claude setup-token`) as the session leader of a pseudo-terminal of altd's own, 4000 columns wide so nothing
wraps, in a fresh settings folder inside the private access store that is removed afterwards, with no sign-in
of its own in its environment and a browser command that does nothing. altd reads that screen through a small
terminal model and returns only the sign-in link it shows; finishing types the code the operator pasted
(printable characters only) and waits for the outcome. A token is accepted only when the command exits 0
after the code and exactly one distinct token of the engine's shape stood alone on a line; it goes straight
to `access.save_engine_token` and ends the sign-in lapse. Anything else fails with one of four fixed sentences
(code not accepted, expired after ten minutes, interrupted, not followed) and saves nothing, so an earlier
sign-in keeps working. One sign-in per engine is open at a time, held in altd's memory: starting another
replaces it; cancelling, failing or the ten minutes ending kill the command's whole process group, and altd's
exit hangs up its terminal. Nothing read from the screen is logged, recorded or returned except the link,
and a failed request answers with fixed text and logs only the exception's type.
Altitude reads a task's transcript, context and token usage in its job's folder, without following a
link the job planted there, and in the operator's folder for a session recorded before. Altitude's usage
readings and improvement reviews still run outside any job on the operator's sign-in.

Every Codex task worker likewise has a Codex folder of its own (`CODEX_HOME`), `l2-engine/codex` in its task
folder. The driver creates it inside the job and links the operator's `config.toml`, `AGENTS.md`,
`AGENTS.override.md`, rules, prompts and each skill into it; Codex writes its own system skills there. The operator's
Codex folder is hidden from every job in Altitude's sandbox: on Linux an empty folder stands in its place, and on
macOS the profile refuses it. Only what the worker's folder links to, the installed Codex program when the
standalone installer keeps it there, and the records of a conversation resumed for the first time in the worker's
folder come back, read-only; the driver copies those records in, with their helper threads, since Codex kept every
conversation in the operator's folder before workers had folders of their own. Token usage counts a copied record
once. The worker signs in with the operator's Codex account without reading their sign-in: altd reads the current
access token and account from the Codex sign-in file and hands them over in the launch input, and the driver passes
them to Codex over its app-server protocol (`account/login/start` with external ChatGPT tokens), never through
the environment; the refresh token stays in the operator's folder. When less than half of the token's lifetime
remains, altd first has Codex renew the sign-in outside any sandbox (`account/read` with `refreshToken`), under the
lock its usage readings hold, and reads the sign-in again inside it, so workers asking together cause one renewal
and a worker cannot force one. When the provider refuses a token during a turn, Codex asks its client for a fresh one;
the driver asks altd through the task door's `sign-in` request, which answers with the current token and is never
kept in the door's answer ledger. A sign-in kept in the system keychain or made with an API key cannot be handed
over; the worker then stops with a [sign-in lapse](#engine-integration-boundary) that says so. Codex marks this
hand-over as unstable, so a Codex release that changes it stops workers with a sign-in error rather than running
them under a looser sandbox. A Codex worker writes its worktree's `.codex` and `.agents` folders, as a Claude worker
writes `.claude`; another task does not load them.

A task worker on either engine reaches GitHub with the operator's existing GitHub
CLI sign-in without reaching the keyring that holds it: the launcher, which still reaches the session
bus, reads the token with `gh auth token`, and the job receives it in its launch input, which only the
job's engine driver reads. The driver exports it as `GH_TOKEN` to the engine it starts, which reads only
its own input pipe from the driver. Altitude never puts
the token among the job's settings (they form its command line) or in a log, and writes it to disk only
in the macOS launcher's private input copy until the job's supervisor starts, in a job folder no sandbox Altitude
applies can read. The engine and its tools
hold it as `GH_TOKEN`, so an engine's own record of its environment (a Codex shell snapshot) can hold it
too. A launcher without a sign-in starts the worker without one. The coordinator's GitHub reads stay on its relay. A Codex coordinator turn keeps
Codex's own sandbox inside the same job boundary (on macOS the two Seatbelt profiles cannot nest); its profile denies
the per-user runtime folder, the shared temporary folders apart from its own, Altitude's credentials, the operator's
stored credentials, the repairs' records and the agent chats present when its turn starts and, on macOS, the private places and the job folders. Both engines use the same door; private worker records and output identify both engines' sessions
after restart. Worker status accepts systemd's `is-active` result `inactive` with exit code 4 for a
collected transient unit as termination evidence; on macOS a job is active while launchd runs it or its
recorded coalition has members. Unknown states, bus failures and query timeouts
remain unavailable and refuse resume or stop confirmation; an active worker must be stopped before
its replacement launches. A turn that ends without a
report, a block, or a completion blocks the task with its result error or stderr tail, on either engine. A
Codex L3 uses a dedicated permission profile: only its fresh per-turn runtime directory is writable; the
deployment checkout and Altitude home are read-only, direct command networking and the user-service bus are denied,
and a required stdio MCP coordinator tool forwards structured requests to that project's role-fenced altd Unix socket.
The coordinator and captured-review adapters and external speech transcription child pass `-I -B` to
Python, preserving isolation and suppressing bytecode writes in the manifest-verified installation.
The adapter runs isolated Python from the protected deployment checkout, outside the command sandbox; it
executes no shell commands and grants no filesystem or service authority to the model. Only this tool is
approved for unattended use; altd continues to authorize each verb. Linux Codex proxy mode denies creation
of AF_UNIX sockets, and its proxy Unix allowlist is macOS-only, so a filesystem read rule cannot enable the
shell wrappers. The command sandbox keeps networking disabled. The common session confinement version rotates
legacy conversations once before their next turn, so a resumed conversation cannot retain the old transport
instructions after activation; current conversations then resume normally. A Claude L3 turn uses an equivalent runtime cwd,
`dontAsk` with unattended prompts denied, restricted settings, only Read/Grep/Glob/Bash, no editing
tools, and native admission for the trusted `alt`, `git`, `gh`, `journalctl` and `systemctl` shims.
The CLI, broker and read shims authorize their operations without a duplicate native verb list.
Claude's runtime shims and the MCP coordinator tool send `alt` invocations plus authenticated GitHub
and service-status reads through the project-bound Unix socket; altd supplies the project independently of the request,
re-applies the L3 command door, accepts only flat task identifiers and stdin, and exposes no direct GitHub or service write command.
The socket and every per-turn runtime directory live in the installation's coordinator folder (`platform.coordinator_directory`: `altitude-<installation id>` in the per-user runtime folder on Linux, `~/.config/altitude/<installation id>` on macOS), mode 0700 and outside every root a task worker may write; each project's socket is named by an 8-character id, so on macOS its path stays within the 103-byte socket limit under any home of up to 64 bytes. Each coordinator turn runs as a job of its own, whose unit altd records before launch; altd serves a connection only when the process at its other end is in that project's running turn's job (`platform.peer_groups`), so a task worker, another project's coordinator or the operator's shell is refused before its request is read.
Its [GitHub reads](CLI.md#coordinator-github-reads) admit any read-only `gh` command, including GET-only
`gh api`, against any repository altd's login can see, and refuse writes and browser launches. Content
read this way is untrusted evidence; retaining another private repository's content in this project's
private records is an intentional coordinator role rule, public publication needs separate authority,
and no taint enforcement is added.

For the main Altitude service, `engines.service_status` projects one fixed native `show` read into
process identity, definition-reload state, the two direct TLS environment assignments, a boolean
for indirect environment sources, and exact owned source-TLS drop-in membership. A metadata-only
disk read separately reports that fixed drop-in's presence. All admitted units also expose load state,
invocation/start/exit identity, native service result and main-process wait code/status, plus memory
current/peak accounting and high/max settings. These fixed scalar fields are filtered to known native
enums and numeric forms; workers request no environment or filesystem paths. Non-loaded units retain
load/process evidence with an error and null termination/resource fields. Absent/collected units,
unsupported properties and failed reads never become clean-exit or health evidence. Raw environment,
other drop-in paths, file contents and native diagnostics are not returned.
Native `show` omits the `EnvironmentFiles` line for an empty list; a successful loaded-service read
recognizes that convention. Other missing or ambiguous fields stay null; direct assignments do not
establish effective next-start TLS when indirect environment sources exist. The read neither compares a saved baseline nor certifies
restoration. [The response contract](CLI.md#loaded-service-evidence) describes recovery interpretation.
Native unit OOM results can support attribution while retained; signal/exit numbers or resource
snapshots alone cannot. This read recovers no collected history, identifies no host consumers,
and changes no retention, service control or recovery policy.
Git log/diff/show reads include full patches and historical files, disable external diff/text-conversion
helpers and reject output-file options. Git and journal shims retain their checkout/service targets. Claude's native Bash sandbox
is not enabled because it would start bubblewrap inside Altitude's own sandbox, which the host refuses;
Altitude's sandbox and the permission boundary confine Claude instead, while a Codex coordinator retains its native filesystem sandbox.
`alt issue new --title '…' [--label …] -` and `alt issue comment <number> -` publish stdin through altd's login to the checkout-origin repository for L3 or the operator, refuse L2 and private evidence references under the AGENTS.md boundary, and record one project event with actor, title, and URL.
`alt issue label <number> [--add <label>] [--remove <label>]` changes an issue's existing labels under the same boundary, publishes no text, and records the issue number and labels; L3 uses it for [pending device verification](DEVELOPMENT.md#pending-device-verification).
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
override. Incident issues are published by the daemon itself, never through this verb; their product
target and public content are described under [faults](#faults); private evidence stays in the calling project.

`alt pr close <number>` closes an explicitly selected superseded PR through the same project-bound
coordinator transport or the operator's `POST /api/pr/close` endpoint. `server.pr_close` fixes the
repository from the checkout origin, accepts a positive number and no body, and restricts actors
to L3 or the operator. The broker fixes L3; HTTP fixes operator and rejects actor/target overrides.
The command reads PR identity and state, closes only an open PR without branch deletion or comment,
then re-reads even after a failed or timed-out write. Verified closed/merged states return the URL
and an explicit outcome; already-closed and merged PRs need no mutation. Open or unreadable results
fail without a success event. Confirmed calls append actor, number, URL, state and outcome to the
existing `pr-close` project log. Repeated calls read current state; no registry or automatic retry runs.
L3 judges authorization and superseding delivery from the existing evidence; the command does not
prove replacement equivalence or select cleanup targets. L2 hands that evidence to L3 and gains no
PR-close authority. Task state, archives and merge holds retain their own lifecycle; a closed PR that
is a task's current PR gains a `pr-closed` task event, which retires its held-review card.
The [L3 persona](../personas/l3.md) directs capability-gap recommendations and authorized remediation;
that guidance grants no permission expansion by itself.

### Task door

A task worker's own processes change Altitude's records only through altd. Its `alt` verbs, its landing, its
checkpoint hooks and its engine driver each send one short request to `POST /api/task/door` (`altitude/door.py`).
altd admits a request as it admits the owner's other commands, from a process in the task's current worker job
carrying the task's current attempt (see [Isolation and landing](#isolation-and-landing)), and applies it as that task's L2, with the
project, task and attempt its record names. A request names its project and task, and altd refuses one for another.
A request connects to altd directly, past any proxy the worker's network settings name, because altd admits the
connecting process.
- **Verbs.** `alt` in the worker reads the content of each input file it names (`--file`, `--questions-file`) and of
  each file it shows (`--show`, with the document's own images), with its standard input, and sends them with its
  arguments. altd runs the verb as the task's L2 with that content and never opens a path the worker names.
  Reads, `alt land`, `alt task run`, `validate`, `terminal` and `review run` run in the worker or keep their own
  routes, and altd refuses them as door verbs; `alt task save
  report|progress|handoff` stores the owner's report, progress file and hand-off receipt in the task's folder.
- **Landing.** `alt land` commits, runs the local suite, pushes, waits for checks and merges inside the worker,
  under the project lock and the task's merge lock, whose leaf altd makes with each launch. Its record changes
  (adoption, delivery, merge approval, merged head and its end-of-session notice) are door requests.
- **Hooks.** The inbox hook asks altd for the checkpoint's delivery (`tasks.owner_checkpoint`), and the edit hook
  reports each edit (`state.count_edit`). Each waits at most a few seconds for an unreachable altd and then lets the
  worker go on, saying so on its own output; messages wait in the inbox for the next checkpoint. Once altd has a
  checkpoint, the hook waits for its answer for nearly its own time limit. A change that fails inside altd is a
  `hook` system fault.
- **Routine runs.** A [routine run](#routines)'s driver names its run (`ALTITUDE_AGENT_RUN`)
  instead of a task, and altd admits it from a process in that running run's worker job. Its door answers only
  the engine's sign-in (`project_agents.door`); a run has no `alt` verbs, records or Send now.
- **Driver.** The driver asks altd to take a Send now message from the worker's sends folder, to settle its
  outcome, and on an engine without hook checkpoints to claim the owner's waiting messages (`tasks.steer_waiting`).
  altd takes and settles only the message it handed that worker, in the sends folder its layout names, without
  following a link.

A worker outlives activation, so its clients follow the activated version rather than the one it launched
with: `alt` and both hooks start from the launch version's files and, once `current` names another version (a
source deployment's export or an installed version), run that version's same file in their place
(`door.follow_activation`). Each command a worker starts therefore sends its door requests, and its requests to
the owner's other routes, in the form the running altd reads; altd keeps no earlier forms. The worker's
environment, persona, brief and engine driver stay as launched. The driver and any command already running when
altd restarts, such as a landing waiting for its checks, keep the code they started with. An installed update
moves `current` just before it restarts the service, so a command started in that moment can meet the previous
altd once and be refused.

A request to the owner's routes is read up to 48 MiB, the largest files a worker shows sent as base64 with the
verb's own text, when a process in an open task's worker job holds its connection; altd decides that from the
connection before reading the body, and every other client keeps the ordinary 1 MiB. The worker's `alt` refuses a
larger request before sending it and names its size, so it never meets a closed connection.

Each request carries an identity. altd keeps its outcome in the task's `door/` folder for as long as its client may
ask again, so a repeat after a dropped answer or a restart gets the first answer rather than a second change; a
request a restart interrupted
is refused as unknown, and the owner checks the task before asking again. The client keeps asking while altd is
unreachable for up to ten minutes, which covers the restart that activates merged changes. altd's locks open for
reading, so a worker that cannot write the home still shares them. `tests/test_worker_writes.py` runs the owner's
verbs, both hooks and the driver on both engines, and `tests/test_land.py` a landing, with Altitude's home
read-only to them.

No worker's writable roots include Altitude's records. A worker writes its worktree, the Git parts
[isolation](#isolation-and-landing) allows, temporary folders and its own scratch folder,
`~/.altitude-scratch/<project>/<task>` (`ALTITUDE_SCRATCH`), for drafts, held patches, verb input files and tool
caches. altd makes it at launch beside the home and deletes it, without following links, when the task is done or
rejected; it never reads it. A Claude worker also writes its own [Claude folder](#isolation-and-landing) in its
task's engine records, which altd reads without following links. `tests/test_worker_roots.py` confines a process
to each engine's roots and finds every record refused, its own task's included, and every other task's Claude
folder, with the scratch folder and its own Claude folder writable.

### Operator terminal

`terminal.py` gives the operator one login shell per task worktree or project folder. It is off until
the operator turns on the `terminal` machine setting (`POST /api/terminal-access`, which uses the
same request and `machine-set` event as the other machine settings; turning it off closes every
open terminal, and an open in progress either registers before that or is refused). altd owns the
pseudo-terminal; the user service manager starts the login shell on it as a transient
`altitude-terminal-<id>.service` job (`platform.terminal_job`), as the operator and outside every worker
sandbox. The job is created by the manager rather than by altd, so the shell does not inherit altd's
`NoNewPrivileges=yes` and `sudo` asks for the operator's password in the terminal as it does in a desktop
terminal. altd keeps that hardening; worker and review jobs are confined by their own sandbox. The job is
`PartOf` Altitude's service, so the shell and its children live only as long as altd: there is no multiplexer
and no persistence. A launcher that cannot
start the job (no reachable user manager) ends the terminal as `failed` with the launcher's error, which the page
shows. On
macOS the job is a launchd job whose supervisor holds the shell on the pseudo-terminal and stops it when altd's
launcher has gone, and Close hangs up every member of the job's coalition. A task terminal opens in the task's worktree while the
task is neither done nor rejected; a project terminal opens in the registered project folder. The
tick's `terminal.sweep()` closes a task's terminal once the task is done, rejected or gone, and a
project's once it is no longer managed. Opening returns the running terminal when one exists.
The job's control group holds every process the shell starts, including those that leave its session
(`setsid`, daemons). Closing stops the job: SIGHUP to every process in it, then SIGKILL to whatever remains
after two seconds, including commands that ignore the hang-up. Close repeats the stop until the terminal
has ended and its close is recorded, which also covers a job the manager had not yet registered and leaves
nothing unrecorded when altd stops next, and reports an error when the job is still running ten seconds later. The terminal ends when its shell exits; the
manager then stops the job, which kills everything the terminal started that is still running. altd holds the
shell's side of the pseudo-terminal open for the terminal's life, so the end comes from the job, never from a
hang-up. A process started through the user manager or a scheduler from the terminal is outside the job and
escapes. The status names the foreground command when it is not the shell, so the page can confirm before
stopping it.

Output goes into a 256 KB replay buffer addressed by absolute offsets. `GET
/api/terminal/<project>/stream?task=&id=&offset=` is server-sent events: `output` events carry base64
bytes, the next offset, whether older output was dropped, `replay`, how many leading bytes are replayed, and
`pieces` (below), and
`end` carries the final status
(`exited`, exit code and end reason: `exited`, `closed`, `task-finished`, `project-removed` or `failed` with
its `error`). A
reconnecting page resumes from its own offset, and so does a page shown again after the background or the
back-forward cache, whose connection may have dropped without an error; when older output was dropped meanwhile it
starts its screen over from what was kept. The output a page catches up on as its stream opens, or starts over
from, is replayed, apart from the escape sequences after its last text that no page has been sent: a query there
(a shell asking at its start, a command that asked while no page was open) may still be waiting. A query sent to a
page whose connection had died without closing goes unanswered after the reconnect, so its program waits for its own
timeout or a key rather than receiving a second answer that could reach the prompt. Programs query the terminal (`gh` asks
its background colour, others the cursor's place), and xterm answers through the same channel as keys; the page
sends the answers to live output and drops those to replayed output, whose programs have moved on, so opening a
terminal never types at its prompt. Each write's callback runs right after its output is parsed, in the same
browser task, and takes what xterm emitted meanwhile; keys, pastes, the mouse and focus arrive in tasks of their
own and always reach the shell. Every page showing a terminal (a phone and a desktop) answers its live queries, and
a second answer would reach the program after it stopped reading, then the prompt as text. So each `output` event
also lists in `pieces` where the live output after its replay ends, piece by piece as altd read it from the shell,
and every page draws each piece as its own write; an `input` request that carries answers names them,
`answer: {page, end}`: the page's random id for its screen and the end offset of the piece it answered. altd
types the first page's answers to each piece, in as many requests as they take, and drops another page's; input without `answer` is always typed. No page holds a role, so a page left alone answers the next
piece at once. The page sends `resize` once a size change has settled and changed
the columns or rows, and again after each reconnect, since another page may have resized the shell. An ended terminal leaves the status at once (`none`), accepts no
more input, resize or close, and stays readable only by a stream naming its id, so a page attaching after a
failed start still reads why, until a new terminal opens, the task finishes or altd stops. `GET /api/terminal/<project>?task=` returns
the status: state (`none` or `running`), terminal id, the setting, folder and the foreground command.
`POST /api/terminal/<project>/{open,input,resize,close}` with `{task?, id, data?, cols?, rows?}`
drive it, and `POST /api/terminal/<project>/command` with `{task, id, text, bracketed}` has altd type a chat
command for the terminal's reader. Replies to accepted terminal POSTs are HTTP/1.1 with a length and keep their connection,
while every other altd reply closes its own, so typing reuses one connection instead of a new TCP and TLS handshake per
keystroke (an echo takes one network round trip), and altd sends small writes without Nagle's delay.
The page sends input in order, one request at a time, coalescing keys typed meanwhile and splitting a
long paste into 16 KiB pieces that never split a character; the phone Paste key pastes through
xterm, so a shell's bracketed paste holds pasted lines until Enter. Every request after `open` names the terminal `id`, and a stream stays with the terminal it
named, so a page still showing a replaced terminal cannot type into, resize, close or read its
successor. Input waits up to two seconds for a program that has stopped reading it, and gives up at
once when Close is asked for, so a full input queue never holds Close or the setting. Input that fails
may have arrived in part, so the page stops sending keys until the operator resumes typing. A chat
message's `run` block (one line with no control, invisible-formatting or line-separator character) offers
**Open in terminal** in its task or project conversation: the page holds the command in memory for that
terminal, never in the URL or history, shows the terminal and, once its screen has drawn output and
stayed quiet for 300 ms, hands it over with `command`. altd types it, framed as a paste when xterm reports the
shell's bracketed paste mode and never with Enter, and follows that one command in memory: the first Enter the
operator types after it starts it, and a Ctrl+C before that Enter drops it. altd types nothing, and says why,
while a program holds the foreground or it still follows a command it typed (waiting for that Enter or Ctrl+C, or
run and not yet finished), so opening the command again (a second tap, after a reload, from another page) never
joins a second copy to the line. A command typed in part when its write fails stays followed, as what reached the
line still waits for Enter or Ctrl+C. altd cannot see other edits, so a line the operator cleared by hand takes
Ctrl+C first. Input and the command's typing take the terminal's descriptor in turn, so no keystroke lands between
the check and the command. The command has finished once
the shell has held the foreground (`tcgetpgrp` equal to the shell's session) for a second after that Enter and
no process group that held the foreground since still exists, so a job suspended with Ctrl+Z or sent to the
background has not. The reader looks every 0.2 seconds, so a job suspended sooner than that after it starts
counts as finished. Then, or when the terminal ends first, one Terminal notice goes to the terminal's reader,
on its own thread so the reader keeps draining output while it waits for the project lock. For a task terminal,
`tasks.notify` leaves it in the task inbox: it reaches the owner at its next checkpoint as any queued message
does and wakes a blocked owner unless it is stopped or faulted; a task no longer running or blocked, or on a later
attempt than the one current when the page named the command, gets none. It is no conversation entry and grants
nothing. In the same change it marks each open question of the task whose `run` blocks (`tasks.run_commands`) hold
exactly that command with `ran` (`at`, the `command`, and `finished` or the terminal ended first; a terminal closed
before Enter on it keeps an earlier finished mark for it) and a `question-ran` event without the command, so
the question's card in Needs you and the conversation says the command ran and waits for the owner to check it, or
that the terminal closed first (spec §3.8.5). The question stays open with its id and revision; a later notice replaces the
mark, and asking it again publishes a new revision without it. For a project terminal, `l3.queue_message` queues it for the coordinator as a `terminal` system turn,
which the conversation shows as a system line; a project no longer managed gets none. When a command that looks
finished is an open [operator step](#operator-steps)'s, the same notice clears that step (`tasks.step_ran`) and says so. When naming the command
fails, the page still types it and asks the operator to reply in chat instead. altd does not see the command's exit status, so the notice sends the
reader to `alt task terminal` or `alt project terminal` to verify; a shell builtin that waits for input without a child looks finished.
A command the operator types without a `run` block sends no notice. Input and output are never written anywhere; the notice holds only the command text. The task's `events.jsonl`, or the project's
`events.log` for a project terminal, records only `terminal` rows for `opened` and `closed`, with the
folder, and the reason and exit code on close.

A task's running owner reads its task terminal's output with `alt task terminal` (`POST /api/task/terminal`
with the task, project and attempt). The reply is the replay buffer as plain text (escape sequences removed,
each line as its last carriage return left it), whether earlier output was dropped, and the terminal's state:
`running`, `exited` with its exit code and reason, or `none`. altd answers only the task's current attempt
while it runs, and only when the client end of the connection is held by a process in that owner's worker job
(`terminal.owner_connection`, from the same process and socket facts as the agent check), so another agent that
holds this machine's key cannot read it. The project's coordinator reads its project terminal's output, in the
same form, with `alt project terminal`, which altd answers only on that project's coordinator socket (as it does
`alt project message`): the socket's path, not the request, names the project, the ordinary CLI refuses the
verb, and the owner's HTTP reader names a task, so neither a task worker nor another project's coordinator reads
it. There is no reader path to input, resize, close or stream. The last ended terminal's output stays readable
in altd's memory until a new terminal opens there, its task finishes or its project is removed (the tick's sweep
forgets it) or altd stops; nothing is written to disk. Each terminal names its reader in one line above the screen:
"This task's owner and its AI provider can read this output." or "The coordinator and its AI provider can read this
output." Anything the terminal prints can reach the reader, its session record and its
provider, where it stays after Altitude forgets it. A password typed at a prompt that does not echo, such as
`sudo`'s, is not in the output. A desktop or SSH terminal has no reader.

Every terminal request is refused unless it comes from a paired browser on Altitude's own page and
not from Altitude itself. Its reads and streams pass the same-page rule every POST passes (see
[Responsibilities](#responsibilities)), and its POST bodies must be `application/json`. This stops
another site open in the operator's browser from typing into a shell. Then the [agent
check](#altitudes-own-agents) every action shares, `terminal.agent_connection`, asks the platform what
opened the client end of the TCP connection, in both address families since an IPv6 socket can reach an
IPv4 address. On Linux the kernel records the control group that created each socket (sock_diag), read
once and resolved to its path under `/sys/fs/cgroup`; handing the socket to another process or hiding
descriptors changes nothing. On macOS every one of this user's processes that holds the socket counts
(libproc), with its job coalition. The connection is Altitude's when any of them is altd, anything altd
started (on Linux altd's own control group or one below it) or anything in an `altitude*.service` unit
(workers, coordinator turns, reviews, machine commands, terminal shells; on macOS the job coalition
recorded under that unit name). A local client that names nothing is refused: no socket row, no recorded
group, the root group, a group that has gone, a host without cgroup2 at `/sys/fs/cgroup`, a failed
query, or on macOS a socket no visible process holds. A client on another host is the operator's
browser. The Vite dev server does not proxy any path altd could route to the terminal
(`terminalRequest` reads the raw path as altd does, and also its decoded, dot-resolved form; a path with
`;` parameters is not proxied), because altd would see the proxy as the client. This stops a worker from
using the terminal to leave its sandbox and bypass the operator-grant flow. The check's
[limits](#altitudes-own-agents) apply here too, so the operator keeps the terminal off while a forwarder
serves Altitude.
Every paired browser can use the terminal once it is
on. That is the same trust as its other
operator controls, and the Settings copy says so. That includes `sudo`: a paired browser whose user knows
the operator's password, or that uses a terminal while `sudo` still remembers an authentication there, can act
as root. A password travels from the browser over TLS to altd and into the pseudo-terminal like any other input;
Altitude stores nothing typed, and the operator's own shell keeps its history as it does in any terminal.

## Same-installation coordinator information

`alt project message` uses the existing per-project coordinator socket capability. The daemon
binds the sender to that socket and handles the verb directly, using the same exact argument parser
as CLI help. It adds no HTTP writer, file/record access capability, remote transport or automatic
task creation. This uses the existing local capability trust, not a new same-user authentication
scheme. The small workflow supports a development project and a separate installation-specific
test project without turning their separation into an orchestration platform; targets come from
existing project registration.

Recipient queue acceptance, project-log receipts and system chat rows carry structured sender,
recipient, exchange and summary metadata. Internal checkout bindings never enter prompts or chat
API projections. Source Sent acknowledges acceptance, not handling; recipient pending rows become
incoming history when supplied on an ordinary turn. Each information row stays separately folded
outside routine system groups, with only Show/Hide. Existing history search attributes it
as information; decision readers and human conversation handoffs exclude it.
Provider text/tool output, a completed turn or a participant-bound in-turn reply proves supply;
CLI launch alone does not. Incoming precedes its associated receiving turn and reply. Receipt failure reports
a separate visible warning while preserving the ordinary turn; a saved incoming row reconciles interrupted queue
removal, while information without a saved receipt remains pending and may repeat.
Non-reply text/tool output is acknowledged after provider return; a provider failure or daemon crash before saving proof
can repeat information. Delivery is at least once, and message/reply identities support careful triage.
Expanded diagnostic code is read-only; message prose does not inherit the receiving project's
terminal actions, file links or implicit issue-number links. Explicit HTTP/HTTPS destinations remain
clickable and visibly spelled out, including beside Markdown labels; rendering does not certify a host.

Both fields enforce size limits and refuse recognized credentials, private record/home paths,
recognizable transcripts and reserved evidence markers. No files or transcripts are gathered for
the sender. Unknown secrets and arbitrary prose transcripts remain the writing coordinator's
responsibility, and the receiving provider processes the text. Receiving coordinators triage under
their own project rules and operator authority. Public sanitized incident notifications and public
issue records retain their existing independent path. A returned merged fix is distinct from
verified activation before suggesting a retry.

## Faults

A system fault is project-scoped and two-tier. Tier one is code: a temporary capacity stop is retried
with backoff; a usage-limit stop or an engine refusal parks the task on its engine and session with
the reason, and only the operator's or L3's explicit handoff moves it to another model or engine.
The engine seam reports allowance scope and an optional reset. A named model allowance excludes only
that model, never unrelated models or engines. An unknown reset creates no resume timer or global
timed hold; the existing routing observation expires after thirty minutes without claiming a reset.
A provider-reported reset schedules resumption on the same engine. Fresh attempts retain the existing worktree, including
uncommitted work, and validate its checkout and adopted ancestry. Task conversations, worker evidence,
PRs, expected files, questions and merge holds remain. Tier two is L3: whatever remains blocks only its own
task, files private incident evidence (one incident per source project and fault kind per 24-hour
window), publishes its issue when a target is configured, and leaves an FYI and one message with the
incident and publication status in that same project's L3 queue; a repeat of that kind
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
L3 owns an actionable recovery step and recurrence prevention separately. Its
[role contract](../personas/l3.md#authority-and-coordination) requires an owned next step and trigger,
a meaningful finite observation during a justified wait, or an explicit capability/authority decision.
Existing task conversations and incident evidence carry this responsibility; no new timer or task
state enforces it. Irretrievable history remains unknown. L3 uses retained evidence and supported
reads for remaining questions, then exposes the narrow gap if they cannot establish recovery.
The owner investigates as part of its task, iterating non-invasive diagnosis without approval rounds;
a changed operational contract, missing access, material machine change, unapproved spend or explicit
restriction waits for its decision. Operator grants, fix scope and holds bind; no new privilege or
automatic fault retry is introduced.
Missing evidence and unrelated delivery establish no recovery. For newly investigated
incidents it judges whether the cause matches an existing issue and attaches it, records
the prevention disposition and owner/next action in incident evidence and on the issue,
and gives one concise recovery/follow-through FYI. `watch` retains pending reporting, delivery or
effectiveness; `closed` records verified prevention or an evidence-backed non-defect/no-change
disposition. Local recovery and confirmed publication alone establish neither. Unchanged repeats
reuse confirmed links and remain quiet. Historical visibility grants no bulk publication authority.
The development coordinator triages reports under its own project rules and assigns authorized
corrections to a matching owner or one concrete task; external notification grants no task authority.
Restart inventories and incidental events do not turn saved blockers into new failures. Every L3
turn receives this guidance, including resumed provider sessions. The originating L3 checks public
delivery evidence and relevant local observations that the cause is gone before the existing
reason-bearing resume or explicit provider handoff. Notification receipt, issue closure or unrelated restart is insufficient.
Coordinator messages to faulted tasks use the existing non-waking inbox marker; they remain in the
conversation and reach the worker on a later supported resume. Operator discussion still uses its
ordinary wake path. The original attempt, provider session, launch model, worktree and merge holds
remain under the existing dispatch and landing rules.
`alt task recheck-ci` registers one finite CI probe on a blocked task through the
same project-bound coordinator transport. The task's `ci_recheck` record binds its block, attempt,
worker/session and lifecycle request identities, selected run, due time, budgets, evidence and L3
receipt. The existing tick/keyed executor owns IO; `resume_after` and `daemon_request` retain their
ordinary lifecycle meaning. A lifecycle change invalidates further probe actions.
The daemon prefers a relevant fresh run of the same workflow, branch, event and PR identity from
the latest twenty runs. Otherwise a probe on a fault-blocked task submits one rerun of the selected
project-origin run after persisting its baseline attempt and submission intent; a probe on a task
blocked on a question (`wait`) only observes the run until it is terminal. Restart and uncertain submission reconcile
attempt metadata without repeating the write. Reads stop after three failures, twenty-four rounds
or two hours after the due time. Fresh nonexpired, nonempty artifacts created during the observed
execution and at or after the scheduled check time establish an upload; step conclusions alone do not. Old-run reruns
retain their original workflow. Every terminal probe result, including unchanged conclusions without
upload evidence, uses one `ci-recheck` row in the existing L3 queue, retained through handling.
Its task receipt stores the turn identity before execution; explicit successful terminal chat
evidence repairs an interrupted receipt after restart. Delivery has two attempts and a one-hour
deadline, including unavailable engines, with a visible terminal failure on exhaustion. Retry waits
do not hold ordinary chat. Queue and terminal-history read failures share the bounded delivery
storage budget. Each engine's existing transient execution boundary enforces the turn timeout
independently of altd, with five seconds to stop its process tree. A restart after provider execution
begins without terminal evidence ends visibly uncertain instead of launching overlapping handling.
No probe resumes a worker, resolves a question, or releases a merge hold.
L3 reconciles the next step when a finite probe ends and gives an evidenced heads-up for significant
stalled work. Repeated observations stay quiet; terminal evidence promises no further scheduled check.
Project-local repairs remain owned by the affected project. With publication configured,
`incidents.publish_issue` publishes each incident as one GitHub issue: system faults publish after
the fault lock is released, and `alt incident new` publishes after the record is written. The daemon owns the product target
seam, `server.issue_repository`: `config.incident_repository()`, the machine setting
`incident_repository` when saved (`false` is off), else `ALTITUDE_UPSTREAM_ISSUE_REPOSITORY` in
altd's environment, and nothing else; off keeps every incident on the machine as a pending record
with that reason. The issue carries the label `incident`, the sanitized title, expected and actual behavior,
the sanitized cause, a reproduction line that reads pending triage until L3 comments one, the
Altitude version and the incident marker (incident id plus an opaque project digest). Evidence,
task, project, logs and conversations never supply public content. On an installed release the
system facts also carry the `update` line from `update.json` (a newer followed release, up to date,
or not checked), read without a network request. When that record shows a newer release, automatic
publication holds the issue as `pending — held: …` because the fault may already be fixed, and the
fault FYI and L3 message tell the operator to update and retry. The fault ledger ties its incident
to the installed version, so any repeat after the update files and publishes a new incident. A held
record stays held; only `alt incident publish` files it.
Source checkouts and container images neither check for releases nor hold.

`incidents.sanitize` decodes the text and rewrites home paths, `.altitude` and incident file
references, long hex ids and UUIDs, email addresses, credentials and private key blocks, task
references, other managed project names and the configured operator name before anything is sent;
`incidents.check_public` then refuses the same private patterns the project-local issue verbs
refuse plus the operator name, so a sanitizer miss stays local. A per-project incident lock
serializes publication and amendment across the daemon and CLI, so a retry never races into a
second issue and an amendment never lands on a replaced snapshot. Any failure (unavailable GitHub, a missing label, a refused
body, an unconfigured target) leaves `issue: pending — <reason>` on the record and an
`incident-issue` project event; nothing retries on its own. `alt incident publish <id>` retries: it
lists the repository's `incident` issues and reuses the one whose body carries this incident's
marker before creating, so an interrupted create never produces two issues. A record whose issue is
already a URL returns it without GitHub.

`alt incident amend <id> --issue <url>` attaches an existing issue in the same repository after a
GitHub read verifies it, comments the occurrence there, and closes the issue this incident created
(identified by its marker) as a duplicate; a hand-written or previously attached issue is never
closed. `--status closed` comments the sanitized closure reason on the linked issue and closes the
issue this incident created when still open; an attached issue stays open for its other
occurrences. A GitHub failure leaves the incident unchanged so the closure can be repeated. The
`- issue:` bullet is added to a record filed before issues existed on first attachment.

An incident from another managed project notifies the registered local `altitude` development
project once, when its Git origin matches the issue repository. `l3.queue_upstream_issue`
atomically appends to the existing queue under the receiving project lock; pending rows and retained
`upstream-notification-received` project events deduplicate by receiving project and normalized full
issue URL across source projects and daemon restart. The fixed message contains only the public URL
and a statement that work decisions belong to the receiving coordinator/operator; it carries no
source project, incident, evidence, task instruction or task association, and changes no task.

A merged source-deployed Altitude change marks activation pending when the self-deploy fast-forward brings in loaded
backend and launch-source paths (`altitude/`, `bin/`, `systemd/`, `scripts/`, `personas/`, `hooks/`,
`templates/`, `schemas/`) or tracked inputs to the served web bundle
(`web/src/`, `web/design/tokens.css`, `web/index.html`, `web/package.json`, `web/pnpm-lock.yaml`,
`web/tsconfig.json`, `web/vite.config.ts`), whether the fast-forward runs after a task lands or the
regular thirty-second daemon tick discovers a merge while its worker still runs.
Web docs, design boards, the unused npm lockfile, and other non-build files do not
trigger activation. Launch-source changes become available through the activated committed export.
Until then, the checkout's `alt` talks to the older running service, so it sends a service field
only when its option is given: a new option reaches the service only when used, and handlers keep
refusing fields they do not know.

The web app shows a compact, dismissible update notice above the phone header and first in the
desktop main pane, except in Monitor where **Altitude update** owns the full status. **Details**
opens Monitor. The notice's browser-local dismissal records the pending head/since and failure
identity in `altitude.restart.dismissed`; polling, navigation, refresh, wait reasons and restart
progress do not repeat a dismissed notice. A new update or changed nonempty failure appears again;
clearing a failure does not. Unavailable browser storage limits dismissal to the mounted page.
The shared overview remains authoritative. Monitor shows the changed area, file count, age,
quiet-point waits and available Restart action independently of monitor readings, with explicit
loading, read failure, no-update and request-error states. Dismissal changes no scheduling,
authority or fault state. Every toast has a dismiss control; inline errors and task questions
retain their recovery and answer controls. Both engines launch L2 workers in independent jobs outside altd's own;
running and blocked workers survive activation and are adopted afterwards. A surviving worker's `alt` and
checkpoint hooks run the activated version from their next invocation, so what they send is what the running
altd reads ([task door](#task-door)). Each worker unit and the
service retain `KillMode=control-group` on Linux, and on macOS Stop takes every member of the job's
coalition, so stopping a worker takes all its descendants. A worker ends when its engine exits or
its latest turn fails with no activity since; an active job alone is not a live worker. An engine can stay
alive after a failed turn while a background command it started runs, so polling stops the job of a worker
whose latest turn failed: whatever it left running ends with it, and the ordinary limit, capacity or fault path follows.
A Send now message returned after that turn is not activity. A successful turn that waits on its own background work keeps running. An exited or
missing worker on a running task requires a report written since its latest launch or resume
or an explicit completion; without one it blocks with a system fault and incident. An explicit
question block remains waiting after worker exit and needs no completion report. Dispatch continues
while activation is pending. When dispatch/resume claims, L3 turns, adversarial reviews, report
verification and bounded validation runs (including evidence recording and cleanup) are quiet, altd runs
the one guarded restart script as a transient user unit outside its own cgroup. It installs the
pnpm-locked dependencies, builds and validates the checked-out head's bundle in staging, rechecks that the
checkout is clean, not ahead or diverged and still at that head (behind origin is fine: the deployment tick pends
the rest) and the quiet point, swaps the bundle, restarts safely, and verifies both API and UI within 45 seconds,
allowing each answer ten seconds on the scheme the service's environment names; verification failure
restores the prior bundle and verifies recovery API/UI health from another new process, retaining
both the activation failure and recovery outcome. The replacement daemon cleared the pending record when it
started and runs the new backend, so the failure records the attempt's web files as a `failed` pending
activation, which the notice and `alt repo` show, unless a later merge or request already recorded its own. With no prior bundle, recovery is unavailable.
On macOS, service Stop/restart confirms coalition cleanup and waits up to 45 seconds for launchd
to remove the label before completing Stop or bootstrapping again; unreadable removal fails explicitly.
Independent worker jobs retain their own coalitions. Monitor's Restart button runs the same path sooner by hand: it appears
at that narrow quiet point, even while workers run, disappears once restart is under way, and
the notice leaves when the new process answers with nothing pending. A transient refusal (no quiet-probe
answer in three tries, work claimed the quiet point, the checkout moved after the build began) withdraws the
request with its `error` and a five-minute `retry_at`, and altd requests the unit again at a later quiet point,
up to three retries. Any other failure, or a transient one past that bound, files a
system fault naming its reason at once and, while its request is still pending, marks the record `failed`
with that `error`, so the hold lifts; a restart that has not happened ten minutes after it was requested (the unit died without
reporting) is the same fault. Dispatch, resume and
L3 turns wait only from the unit request until the replacement daemon is ready; report verification
also waits, leaving reports durable for the next tick. A shared activity lock fences these
operations and validation against the exclusive restart request, including admission races. Ordinary source changes never
start, stop, mask, unmask, or restart the service; a lifecycle action by hand needs separate
authorization and post-change health verification.

## Interfaces and storage

The daemon never writes its records in the runtime root through a link: a planted link would let a
process that can write there have Altitude write outside its own reach. No task worker's writable roots include it. Every record
replacement, log append and lock in the runtime root, worker output and review snapshots go through
`storage.py`. It walks from the runtime root fixed at startup without following a link at any
component and opens a leaf only when it is one singly linked regular file, truncating only after
that check. Record replacement writes a new file and renames it over the leaf, so a planted leaf link
is replaced, never followed; a review snapshot creates every folder and file relative to its review
folder and refuses anything already there. A refused file is an error, not a fallback. Locks never
truncate; serialization waits for the current writer and the activity fence keeps shared/exclusive
nonblocking contention. New folders and files are private to the operator.

Reads that reach another engine or the task record use the same walk: the task's `brief.md` and
`request.md` for reviews and the transcript, task documents, invocation output and a failed worker's
error tail. Brief reads keep complete UTF-8 text and universal newlines; a live task without a brief
never borrows an archived one. Other runtime reads still open by path, image preparation still
writes by path inside its fresh staging folder in the project's `images` folder, a reviewer receives
its captured snapshot and working folder by path, and a worker can change the contents of any
regular record it can write.

### Times

Records, events and JSON carry every instant as ISO 8601 with an explicit offset (`+00:00`), and
text Altitude composes for a record or message, such as a block reason, an FYI or a waiting line,
writes its instants the same way. Nothing a person reads shows that form. The web app formats
instants in the browser's zone and locale through `web/src/data/observed.ts`, and its prose renderer
shows written UTC times in agent and system text (ISO instants, `HH:MMZ`, `HH:MM UTC`, a UTC clock
range) the same way, never adding a date the writer left out, outside code and paths; `bin/alt` human output passes through `state.localized`, which
shows ISO instants in the machine's zone. Formats are in [SPEC.md §4.5](../design/wireframes/SPEC.md#45-copy)
and [CLI times](CLI.md#times).

### Improvement review

The approved [improvement suggestions design](../design/wireframes/improvement-review/PROPOSAL.md)
reads one project's finished work and proposes at most three fixes. It covers the Altitude project,
where it is the built-in routine **Weekly improvement review** on the [Routines page](#routines). A
review runs on Monday mornings while it is on, when the operator presses Run now on its page, or when
they ask L3 in chat; each needs their agreement to the terms its sheet shows.

`improve.py` builds the evidence in code. Signals are exact, versioned predicates over existing task
records (`SIGNALS_VERSION`): question revisions with no operator or L3 message since the previous
one, refused merge approvals and grants, operator messages after a report, the third and later
completed review of a subject, findings the final report leaves open, CI rechecks, resume and
dispatch failures and system faults, reports superseded after verification contradicted them, and
escalations. A delivery is recorded for every pushed head and a message continuing a reported task
supersedes its report, so neither counts on its own; a question revised after a reply follows it up,
and a review re-run after a failed one, a review of new material (`additional`) and one review of the
fixes are the designed review path. The window is
tasks finished in the last 14 days, never before 2026-09-22 07:00 UTC and never older than 30 days.
Task sheets, ranked by signals and operator messages, carry title, outcome, durations and tokens,
signals with the places that produced them and what each event recorded (its reason or error, the
question an escalation handed on with the answer it got, or what verification found in a replaced
report), each question on one line with every revision's place, how many revisions followed a reply
and how it was settled, review findings with their dispositions, and up to four paired quotes (the
message answered, the operator's words, the reply, 300 characters each). The quotes are the operator
messages a signal counts, then those where the operator stepped in unasked, then answers to the owner's
questions, each kind spread over the conversation. Sheets are whole lines, at
most 3 KiB each and 48 KiB together: after the head and signal counts, the operator's two most telling
quotes take the room first, then what the signals' events recorded, then questions, then the other quotes,
then findings, and a last line counts what did not fit. Tasks that do not fit are listed
by counts. Operator chat turns fill the remaining room as paired quotes, followed by
current task titles and earlier suggestions with their outcome (their decision, lapse or L3's reason for
dropping them). The prompt is at most 64 KiB.

Every quoted or counted place carries a short alias (`[A12]`) mapped to a canonical place: a task
message, question revision, event, review finding, report finding or chat row byte offset. Only
aliases that reach the prompt are valid, and only the code writes them: square brackets in quoted
text become round ones, so a quote cannot forge a citation. `improve.validate` treats the answer as untrusted: at most
three suggestions, each with a target (`project` or `agents`), bounded text, a known signal or none,
and at least two citations from its own prompt; text that looks like a link, path or credential is
dropped. Dropped suggestions are kept with their reason.

`engines.improvement_review` is one tool-free Claude call on the plan: `--safe-mode`,
`--no-session-persistence`, `--tools ""`, `--strict-mcp-config`, the instructions as the system
prompt, the answer schema, the prompt on stdin, an empty temporary working directory, the output
capped at 12,000 tokens and Claude Code's budget stop at the call's token maximum priced with the
model's published rates from `config.IMPROVEMENT_MODELS`. Everything sent is at most 72 KiB, so a
call uses at most 86,000 tokens; the recorded usage marks a call that exceeds it. The call fails
closed unless its own start record lists only Claude Code's carrier for the schema-bound answer
(`StructuredOutput`), no MCP server, a model and no API key: the call runs without `ANTHROPIC_*`
variables, so it uses the signed-in plan and never pay-per-use credentials. A usage hold, unknown model, oversized prompt,
timeout, usage limit or unusable answer ends the call with its reason; nothing retries or switches
model. Codex is not offered: no tool-free Codex mode is verified, so without Claude the review is
unavailable.

The terms (`improve.terms`) are the sheet's title and text and the What is sent line, versioned by a
hash of that text with the signals version, the window's bounds, quote, sheet and prompt sizes, the
model and its token limits, so a change to what is read, the model or the limit is a new version. The agreement in `<project>/improvements.json` records
the version, `operator` and when; an agreement to an earlier version does not count. While Weekly is on
under an earlier version, the next tick turns it off and writes one chat line, "Weekly improvement
review paused: its terms changed.", whose Open goes to its page.
Weekly (`weekly`: on, when and by whom) and every change to it or the agreement (`changes`) live in the
same record.

A run starts in one of three ways, each with its authority, and a source starts one run: the same
source again returns it. `alt improve run` on L3's coordinator socket is admitted only during the chat
turn that answers the operator's own project-chat message, which is its source. Run now is
the operator's press with its request id. The Weekly run's source is its week: once 08:00 on Monday,
machine local time, has passed, the first tick that finds the week unclaimed claims it
(`improve.weekly`), so a week Altitude was down at 08:00 runs at its first check that week; a week it
missed entirely is not caught up, and Weekly turned on after that Monday's 08:00 first runs the next
Monday. Every start covers only the project whose origin is Altitude's repository, and is refused
without the agreement, while another run of the project is active, with fewer than five tasks
finished since the last review's window, while Claude's weekly or short window is at
`config.RESERVE` (90%) or more or is unknown (`route.reserve`), and while
Altitude waits to activate a change. A Monday that meets one of these is recorded as skipped with its
reason ("3 tasks had finished") and posts nothing; a pending activation instead waits for a later
tick. The claim is durable in `<project>/improvements.json` before the reviewer runs on a daemon thread
holding the restart lock as review runs do. Each run reads the tasks finished after the previous
posted or empty review's window (the first reads 14 days); a stopped or failed run does not advance
it. The exact prompt and raw answer stay private in `<project>/improvements/<run>/`.

A run is reading, then checking, then exactly one of posted, nothing, stopped or failed, with one
deadline two hours after its start. With suggestions, Altitude queues one L3 turn (trigger
`improvement-check`, with the run's deadline as its own, so it never runs later) naming only the run, so the unchecked wording never enters the conversation;
L3 reads the candidates with `alt improve status --run`, opens their cited places and posts with
`alt improve post <run> --keep … --drop n:reason`, naming every number once. Altitude, not L3, writes
the chat message from the validated wording: a heading with the review's date and window, each kept
suggestion under its run number with its target, explanation and a link to every cited task, then
L3's drops. Model and L3 text is made plain first (brackets, emphasis, code marks and fences, leading
slashes and issue numbers neutralized), so the only links are Altitude's own. The numbers shown and the
date are the run's receipt, the same post returns it and a different one is refused, as is a post for a
stopped, ended or late run. A run's one chat line, posted or ending, is an assistant row of its own turn
named by the run (trigger `improvement`); the record is saved first, and a retried post or the next
tick writes a line a restart interrupted, once. Keeping none ends the run with the nothing-to-suggest line. No suggestions, a
failed call, and a deadline passed while reading ("it was interrupted by a restart") or checking ("L3
did not finish checking") each end the run with one line. `alt improve stop` during the operator's chat
turn, or the Stop press, stops a reading or checking run: its answer is discarded, its queued check
removed and its line says the operator stopped it. Nothing retries.

`alt improve resolve <run>/<n> accepted|dismissed --reason` records the operator's typed answer during
the chat turn of their message, which must follow the post and name a number it showed. Undecided
becomes accepted or dismissed; dismissed and not decided (seven days after the post, computed on
read) become accepted; accepted is final. The same message and outcome return the existing receipt;
a change needs a newer operator message than the recorded answer's.
`alt task new --improvement <run>/<n>` creates the one task for an accepted suggestion and records it
on the task, so a second is refused.

The web reads the runs with the project (`improvements`, from `improve.view`), never from a copy in
chat. A reading or checking run is a line with Stop. An ending is a line with Show, opening its
details. A posted run is one card at its chat row: each shown suggestion with its target, explanation,
Seen in (each cited task once, opening in place, then the project chat), the count of its signal
across the run's tasks, and its state. Details name what was read and left out, L3's drops, the model,
its usage of the call's token limit and when it ran. The presses go to `POST /api/improve` with a
request id: Make a task (`accept`) and Dismiss on an undecided suggestion, Undo on a dismissed one
until it lapses, Make a task again (`retry`) and Stop, which names the run its line showed. A request id makes
one press in the project: the same press again returns its receipt and any other is refused. A press
the record no longer allows is refused (409) and the card shows the fresh record. Make a task records the
acceptance with the press as its authority and queues one L3 turn (trigger `improvement-task`) naming
only the suggestion; L3 checks its cited places and creates its task. While that turn, or the chat turn
of a typed acceptance, is queued or running (`l3.pending`), the suggestion reads "L3 is opening a
task"; when it ended without a task, it reads "Accepted · no task yet · Make a task", whose link (`retry`) queues the
same turn once more, refused when another retry queued it first. The overview lists under
`suggestions` every posted run with a suggestion that waits ([suggested tasks](#suggested-tasks)); Needs
you shows them under Suggestions, after what needs the operator and not counted with it, each heading
linking to its card in the project's chat. The states, presses, typed answers and task rule are the
shared [suggested tasks](#suggested-tasks) model.

The review is a built-in routine of Altitude's own project, read from the same record. `improve.agent`
is its Routines page entry in `GET /api/agents/<project>` (`weekly`): the first Built in row until the
record has an agreement, a Weekly switch or a run, then the first of the operator's routines, whose line
after "Mondays at 08:00" is its running line, Paused, "Paused: its terms changed", the latest ended run
or Never run; Work's Routines count includes it. `improve.page` is its page, `GET
/api/agents/<project>/weekly`: the status card (Not on yet with Turn on; the next Monday, with how many
more tasks must finish, and Pause; Paused or "Paused: its terms changed" with Resume; Run now beside
each, with the tasks it reads or the reason it cannot run; while a run is going, its line with Stop
only), the last ten ended runs and skipped weeks, newest first, each ended run opening its card or line
in chat (`#improvement-<run>`), and What it does: its purpose, What is sent and, once a review has
posted or found nothing, its totals: runs, the suggestions they showed, how many became tasks and how
many were dismissed, with Latest opening the latest card. The terms are out of date whenever the
agreement is not to the current version, whatever the switch says. Without the agreement to the
current terms, Turn on, Resume and Run now open the sheet first; Turn on sends the press with the
sheet's version (`terms`), which records the agreement and completes the press, and a version that
changed since is refused. Turn on, Pause and Resume are the `weekly` press and Run now the `run` press
on `POST /api/improve`; they come only from a paired device's cookie, never a client holding only the
machine key. Its runs are not routine runs: they use neither the routines' weekly budget nor their one run
at a time and never appear in Work's Current rows, and it has no Edit or Retire. `alt agent` neither
lists nor switches it. Projects other than Altitude have none.

`improve_eval.py` loads fictional scenarios from `tests/fixtures/improvement/` as ordinary task
folders and project chat and runs the same prompt builder and reviewer. Its offline part, in `make
check`, checks without a model that each signal fires on exactly its planted places and nothing
ordinary and that what fired reaches the prompt; its live part matches suggestions to planted patterns
by shared places and writes a blind grading sheet. [The eval](DEVELOPMENT.md#improvement-review-eval)
describes both.

### Routines

The approved [routines design](../design/wireframes/project-agents/PROPOSAL.md) lets the operator give a
project briefs that Altitude runs on a schedule, on events the operator sends or when asked
(`altitude/project_agents.py`). A run reports, and one whose routine may change things asks Altitude to make each
change ([routines that act](#routines-that-act)). A routine is a definition (name, brief of at most 8,000 characters, up to four
schedules of weekdays and a time, whether it runs when it's sent an event, Tell me, an optional model choice, a
limit of 10, 20 or 60 minutes per run, `may`, what it may change: empty, or `worktrees` for worktrees and
branches, and `commands`, up to ten commands of the operator's it may ask Altitude to run) with its state: new, on,
paused or retired.
`<project>/project-agents.json` holds the project's routines, every saved definition as a numbered version, every state
change with who made it and its source, the runs, L3's offers, the presses' receipts and the weekly budget. A run's
prompt, report and worker job live in `<project>/project-agents/<agent>/<n>/`. A run is never a task: nothing that
lists, counts or reports tasks sees it.

**Definitions.** Altitude ships built-in routines as `templates/project-agents/<key>.md`: a header of name, days,
time, Tell me, a one-line summary and, for one that may change things, `may`, then the brief. Turning one on copies it as a routine that is on, marked with
its key, and the Routines page lists only the built-ins without one. A routine the operator creates is saved new, not
on: it runs only with Run now until they turn it on. Every saved change is a new version; a save names the version
it edited and one made from an older version is refused, and each run records the version it ran.

**Schedules.** Schedules use this computer's clock (`altitude/schedule.py`, which the Weekly improvement review
shares). On each tick (`project_agents.tick`), a routine that is on claims each schedule's latest time
(`schedule.latest`) that is after the moment it was turned on or resumed and after its previous claim, so a time
missed while Altitude was off runs once when it returns, marked late after ten minutes, and older ones are not
caught up. A schedule a saved version adds or changes starts from the save, so a time already passed that day does
not fire. A claim becomes a waiting run, or a skip ("Its last run was still going") while the routine's previous run
waits or runs: a routine has at most one. While Altitude waits to activate a change, nothing is claimed.

**Events.** A routine with the event trigger runs on short texts the operator sends from this computer:
`alt agent send AGENT TEXT|- [--key KEY]`, a change of the operator's CLI only, refused on L3's socket, in task
workers and from paired devices; a routine run's one verb is `alt act`. Altitude reads no source of its own; the routine
page and [operations](OPERATIONS.md#sending-events-to-routines) show example scripts (`project_agents.recipes`) that send
pull request and issue numbers and the operator's own words. A send is one locked write: a key the routine already
took answers "Already received." before anything else; otherwise it is refused (routine not on, paused or retired, no
event trigger, text empty or over 4,000 characters, 50 events already waiting, or the week's budget short of its
limit) and keeps nothing, or the event (`{text, key, at}`) starts a waiting run, joins the waiting one, or waits in
the routine's `pending` while one runs. When a run ends, the routine's `pending` events become its next waiting run if it
is still on; a schedule that comes due while events wait takes them into its run. The routine keeps its latest 1,000
keys in `received` and its latest event, which Run now replays without its key, so a replay uses nothing up; `alt agent run AGENT TEXT` gives
one run a text of its own instead. Pause and Retire skip a run still waiting to start ("Paused before it started",
"Retired before it started"). Pause, Retire, pausing itself, a skip, a Stop before the run's worker starts and a run
that could not start drop the events no run read and give their keys back, so a script that sends the latest few
each time brings them in again.

**Starting.** A project starts one run at a time, its oldest waiting run first (`project_agents.start_next`, spawned
by the tick and by Run now), under the provider admission, the machine launch lock and the restart lock that task
launches take. A run starts only when the machine's running work (task slots held, adversarial reviews and
running routine runs) plus the queued tasks and ready resumes waiting for a slot is below `config.machine_wip()`, so
tasks always go first; a run that has waited six hours is skipped ("Didn't start within 6 hours"). At its start a run
is skipped when its engine is inside Altitude's reserve (`route.reserve`: the engine's weekly or short window at
`config.RESERVE` or more, or unknown, read as "Your <engine> plan is close to its weekly limit", "… short-term
limit" or "Your <engine> plan's use isn't known yet") or its whole limit does not fit the project's weekly budget (30 minutes to ten
hours, two hours by default), counted from Monday 00:00 on this computer's clock with a running run holding its whole
limit. Run now is refused with the same budget reason.

**The run.** The run's engine, model and effort come from the routine's model choice or else the New tasks choice
(`route.pick_engine`). It runs in its own detached checkout of the fetched main branch
(`.claude/worktrees/project-agent-<agent>-<n>`) as a worker job of its own kind (`engines.start_project_agent_run`):
the helper role, the repository's rules and the run template (`templates/project-agent-run.md`: the brief, what
fired it with the events it took quoted as data, oldest first, the previous report quoted as data, the routine's earlier
suggested tasks with what became of each, what the run may do, the limit, the report's shape and the room left for
suggested tasks). Unlike a task
worker it links nothing of the operator's engine folder (their settings, instructions, skills, plugins or rules),
runs in the engine's restricted mode where it has one (only Altitude's own settings apply, and its file tools stay
in its working directories), writes only its own checkout's Git state (no objects or branches of the shared
repository, `git_policy.worker_git_access(shared=False)`), receives no GitHub sign-in and no Send now, and its environment names actor `project-agent-run`, for which `alt`
admits only `alt act` and refuses every other verb. Its job ends at its limit (`platform.job_command` `runtime_max`), and the tick stops a run that
reaches its limit while Altitude is watching. Stop ends a waiting run at once and stops a running one's job; a run stopped while it
is starting ends at once, and its start stops the worker it made. A run still without a recorded worker ten minutes
after it started was interrupted by Altitude stopping, and the tick ends it as couldn't finish. While a run is
running, the tick also reads its session's context through the passive reader a running task's uses
(`usage.capture` with the run's folder, under the project lock) and keeps `context: {percent, at}` on the run, saved
only when the reading changed, for its row in [Monitor](#monitor-and-live-sessions).

**Reports.** The run's final answer (`engines.worker_answer`) is its report: a first line of `Found:`, `Nothing:`
or `Couldn't finish:` with one sentence of at most 200 characters, then the full report, at most 20,000 characters.
An answer in another shape couldn't finish ("Its answer was not in the report's shape"). A run that found something
may also [suggest tasks](#suggested-tasks), which leave its report. A run ends found
something, nothing found, couldn't finish, stopped by the operator, stopped at its limit, or skipped, and its copy
and scratch folder are removed. Its record keeps the outcome, minutes used, tokens and session. Its minutes, on
its row and against the budget, are at most its limit: the tick that notices its end stamps it, which can be hours
late after Altitude or the computer was down. Each ending
writes at most one assistant chat row (trigger `project-agent`) after the record is saved, once, as Tell me allows:
found and couldn't finish unless Tell me is never, nothing found and stopped only with every run; a run that took
events names them ("for “PR #809 merged”"). Three runs in a row on the routine's schedule or its events that couldn't
finish or stopped at their limit pause the routine, with its own line; Run now, Stop and skips do not count, and
turning it back on starts again from zero. A routine that paused itself waits in Needs you as a row of kind `agent`
(`project_agents.needs`, listed by `tasks.decisions` after the project's questions and steps) with no `alert_key`,
so it never alerts a device; Resume, a saved change, which makes it paused by the operator, or Retire clears it. Runs older than 90 days
beyond a routine's newest ten are removed.

**Authority.** The operator changes routines with presses on `POST /api/agents` from a paired device only, each
carrying a request id whose repeat returns the first receipt, and with `alt agent new|set|on|pause|resume|retire|run|stop`
on their CLI, which alone sends events (`alt agent send`). L3 reads them with `alt agent list|show`, and on the operator's own chat message can pause, resume,
turn on, retire, run or stop one; a routine that may change things only the operator turns on, resumes or runs, and
L3 may still pause, stop and undo it. It never writes a definition: `alt agent offer` drafts a new routine or a change as
a card under its reply, which only the operator's press saves (Create routine, Save change, or Edit first through the
form). An offer lapses after seven days, and a change offer is refused once the routine changed since it was drafted.

**Pages.** `GET /api/agents/<project>` is the Routines page (budget, routines, retired routines, built-ins not on,
offers, and in Altitude's own project the [Weekly improvement review](#improvement-review) as `weekly`); `/<agent>`
a routine's status with its note, actions, definition, recent runs and, with the event trigger, `send`: the command, naming the routine by its
shell-quoted name when `alt agent` resolves that name to it and it does not start with `-`, else by its id; the id
the example scripts use when the command names the name; and those scripts; `/weekly` the review's own page; `/<agent>/runs/<n>` a run's outcome, the events it took, the
version it ran, its report and what it changed; `/<agent>/runs/<n>/transcript` its session through the transcript
reader, read only. The project view carries `project_agents`: the runs waiting or running, shown in Work under
Current; the number of routines not retired, with the Weekly improvement review once it is the operator's; and the
offers.

### Routines that act

A routine whose definition has `may: ["worktrees"]` may ask Altitude to tidy its project's worktrees and branches
([proposal](../design/wireframes/project-agents/ACTING.md)). The run asks; Altitude acts: the run's box is
unchanged (its own copy, `worker_git_access(shared=False)`, no GitHub sign-in), so it cannot set aside a worktree
or delete a branch itself, and every change is Altitude's own, checked and carried out in altd.

**Asking.** The run's prompt says what it may change and lists the project's worktrees and branches with what
Altitude's records say of each (`tidy.inventory`: its task and state, last change, uncommitted work, commits found
nowhere else, merged or not). `alt act set-aside-worktree NAME` and `alt act delete-branch NAME` go through the
run's door, which admits only the run's own worker job (`project_agents.act`). Under the project lock, altd refuses
the request ("Not allowed: …") unless both the version the run ran with and the routine now allow it, so a
permission taken away during a run applies at once and one added applies from the next run; a run has 20 requests,
and the 21st is recorded refused once. Otherwise the tidy rule decides, and the run is answered "Set aside worktree
NAME. The operator can undo it until <day>.", "Deleted branch NAME. Merged into main.", "Kept: <reason>." or
"Failed: <Git's message>". A repeated request id returns its first answer.

**The tidy rule** (`altitude/tidy.py`) matches the name exactly against what Git lists and passes only its own
values to Git. A worktree directly under the project's worktree root, not a routine run's copy, is set aside only
when it is not locked, it is no task's own worktree (end-of-task cleanup removes those, and reopening a task checks
its branch out there again), it was not made while a task that is still open was, nothing in its record or folder changed for seven days, no program has it as its working folder
(`platform.folder_in_use`: the processes' working folders on Linux, `lsof` on the Mac; a check that cannot tell
keeps it), it has no uncommitted change and its commits are on a branch. A local branch is deleted only when it is
not main or the default branch, not checked out, not an open task's, has not moved for seven days, and its tip is
on `origin/main` or is exactly the head of a merged pull request into main from this repository. Anything the
operator put back from this routine is kept ("You put it back <day>."). Dispatch's own end-of-task cleanup shares
the uncommitted-work check (`tidy.unsaved`).

**Records and Undo.** Each request is a numbered change on its run (`changes`: kind, target, outcome done, kept,
refused or failed, reason, time) and a `routine-action` project event; an Undo is a `routine-undo` event naming who
undid it and from where. A set-aside worktree moves on the same disk into the worktree root's
`.set-aside/<agent>-<run>-<change>/` folder for 14 days, so Undo moves it back exactly, ignored files included;
the tick then removes it for good, after which Undo adds it again from its branch or commit without ignored files.
A deleted branch's commit is kept under `refs/altitude/undo/<agent>/<run>/<change>`, and Undo recreates the branch
only at the commit Altitude recorded, refusing when that ref no longer matches or the name is taken. Undo is
`POST /api/agents` action `undo` (`{agent, n, change}`, a number or `all`) or `alt agent undo`; each change
succeeds or is refused on its own, and a refused one keeps its Undo with the reason. Pruning a run removes its undo
refs.

**What the operator sees.** The run page lists What it changed, one row per request with Undo while it can be
undone; Altitude's own sentence of the done changes ("Set aside 2 worktrees and deleted 1 branch.") follows the
run's summary in chat, where a run that changed something speaks unless Tell me is never, in Recent runs and in the
next run's prompt. The built-in Tidy worktrees and branches (`templates/project-agents/tidy.md`) runs Mondays at
09:00 and is turned on through a sheet that states what it may change.

**Commands you list** ([proposal](../design/wireframes/project-agents/COMMANDS.md)). A routine's `commands` are up
to ten `{name, words}`: a name of lowercase letters, digits and dashes of at most 40 characters, and words of at most
2,000 characters. `alt act run NAME` asks for one by name through the run's door; a target that is not a command
name is refused without recording its text. Under the project lock altd records the request as a change of kind
`command` and refuses it ("Not allowed: …") unless the routine is not retired, has a command of that name with the
same words both now and in the version the run ran with, no other of the run's commands is running and the run has
requests left (the same 20). Otherwise it claims the job, saves the
row as running and, outside the lock, runs the words through `engines.machine_command`, the runner of `alt task
run`: the operator's login shell (`bash -lc`) in the project's folder as its own service-manager job, with
Altitude's clean environment, no run identity and empty input, ending after `config.MACHINE_COMMAND_TIMEOUT`. A
command runs as the operator, outside every sandbox, so the list is the fence: no text from the run reaches it, and
Altitude does not judge what it does; the form says so beside the list, and that the run's model reads each
command's words and output. The answer names how it ended (done, failed, timed_out, stopped, or not_started when the
job never opened its log, the one outcome after which the run may ask for it again) with the last 4,000 characters
of its output; the run page shows the kept log (`machine_output`). Exit 0 is done even when a stop arrived as it
ended. A repeated request id, or another request for a command that already started in this run, runs nothing and
answers with that command's result, waiting while it runs, so a run whose own tool call timed out asks again for
it. A run's box cannot write its run folder, where the job's log and exit file live.

A watcher stops the job (`machine_stop`) and records who in `stopping`, then `by`: `you` when the operator presses
Stop on its row (`POST /api/agents` action `stop_command`, `{agent, n, change}`, refused to L3), `run` when the run
ends or is stopping, and `changed` when the routine is retired or saved without that command or with other words.
After a restart the tick follows each running row whose job it does not already follow and
settles it from the job's exit file. Each request is a `routine-action` event with `what` `command`; command rows
have no Undo. Altitude's chat sentence names the commands that started ("Ran prune-images; clear-cache failed.").
Only the operator's form and CLI set commands: L3's offer for a routine keeps the commands it has and a new offer
has none, and L3 cannot turn on, resume or run a routine that may change things or has commands. A command's run
counts as the run's own time and takes no machine command slot, since the run already holds one.

### Suggested tasks

The [Weekly improvement review](#improvement-review) and [routine](#routines) runs both suggest tasks
for the operator to decide, through one model in `suggestions.py`; each owner keeps its runs in its own record. A
suggestion is named by its ref: `<run>/<n>` for a review and `agent:<agent>/<run>/<n>` for a routine run. A run
holds `suggestions`, L3's check as `post: {shown, drops, at}` and the operator's `decisions`, and nothing is
shown before the post. Both kinds share the states (undecided, accepted, dismissed, and not decided seven days
after the post), the presses on `POST /api/improve` with request ids whose receipts live in the record the ref
names, typed answers (`alt improve resolve REF`), the retry of Make a task and the one task per accepted suggestion (`alt task
new --improvement REF`, recorded on the task as `improvement`). A suggestion waits in Needs you while undecided,
or accepted without its task for seven days after the last press (Make a task, first or again); after that it shows
only where its run does. A task made from one carries `origin` in the project view's task rows, the routine's name or
"Weekly improvement review", which ends its card's line ("· from Review each merge").

A run of a routine that found something may end its answer with up to three `Suggest:` blocks, each a title line
and the explanation under it (`project_agents.report`). Altitude takes them out of the report, makes them plain
(no links, emphasis, code marks or headings, and text that looks like a credential as "[hidden]") and cuts them at
a word to 90 and 300 characters; blocks after Nothing or Couldn't finish are ignored. A routine has at most three
waiting (`project_agents.room`): a suggestion waits while L3 checks it and for seven days after the check, kept or
set aside, unless the operator decides it. A run keeps only as many as fit, and its template says the room left,
or to suggest none. A run with suggestions queues one L3 turn (trigger `improvement-check`) naming only the run;
L3 reads them with `alt agent show <agent> --run <n>` (`candidates`) and posts with `alt improve post
agent:<agent>/<run>`, naming every number once. The check has two hours from the run's end: its turn is queued
with that deadline (`l3.queue_locked` `until`), so the drain never runs it later, and the tick posts a check past it
with every suggestion set aside ("L3 did not finish checking") and removes its queued turn. A post
writes nothing to chat. Make a task queues an `improvement-task` turn written for a source that read the project
and the web: L3 briefs the task from what it verified, quoting the suggestion only as the claim that started it.
Both turns tell L3 to run only their one command, an instruction as L3's reading of issue text is; enforcing a
turn's commands is #1108.

The run page (`suggested` on `/<agent>/runs/<n>`) shows a run's suggestions as Suggested tasks under its report:
"L3 is checking N suggestions" while L3 checks, then the kept suggestions on the improvement card's suggestion
part, then Not offered · N folded with L3's reasons, or "Not offered · N · not checked in time". The overview's
`suggestions` lists routine runs with a waiting suggestion beside the review's cards, newest post first within each
project. Needs you shows each as a card headed by the routine's name and the run's time, with what fired it and
Open the run, never counted and never alerting a device, whatever Tell me says.

### Image input

Operator images belong to their durable project or task message. `images.py` validates PNG, JPEG
and static WebP, bounds encoded bytes and decoded dimensions, and normalizes orientation and color
into metadata-free PNG/JPEG using the optional local converter. Color conversion to sRGB detects
the local color library and runs in the same bounded child process. It reads RGB and grayscale ICC
profiles, PNG `cICP` tags for sRGB, Display P3 and BT.2020 colors with SDR transfers, and PNG gamma and
primaries, following PNG's precedence (`cICP`, `iCCP`, `sRGB`, then `gAMA`/`cHRM`); a `cICP` without an SDR
conversion defers to the next description, as for a decoder that does not read it. Color information
without an SDR conversion (HDR transfers, CMYK or Lab profiles, unreadable or oversized profiles) is
ignored: the image keeps its decoded pixels, whose colors may be approximate. A conversion stopped by
its memory, CPU or output bounds asks for a smaller image. Every canonical file is re-encoded from
raw pixels, so it carries no metadata or color tag and reads as sRGB. The shared limits are four images, 10 MiB each,
20 MiB total, 25 megapixels, 8192 pixels per side and a 28 MiB JSON request envelope.
Completed codec checks are cached by converter path and modification time. Probe timeouts, OS errors
and nonzero exits report unavailable for that attempt without caching the failure; later operations
probe again. A missing converter or a completed check lacking required codecs remains unavailable.

Canonical files and metadata live under the owning project's private runtime `images/`, outside
worktrees and static assets. Opaque IDs resolve only through that project's committed conversation,
queue or active/archived task references. Filenames are labels; requests cannot name filesystem
paths. `GET /api/images/<project>` reports capability and limits, optionally for `?task=<slug>`;
`GET /api/images/<project>/<id>` serves validated bytes with `no-store`, `nosniff` and same-origin
resource policy. This retains the existing private HTTP/network and OS-user boundary, without new
authentication, public hosting or storage infrastructure. Selected content reaches the chosen
provider as ordinary agent input. Original uploads and conversion intermediates are removed.

JSON response writes handle client disconnects across headers and body, including GET and POST
image error replies. Connected clients receive the same error status and JSON body; unrelated
server failures remain visible. A disconnected response closes the connection without another write.
Image capability reads take the existing project-removal lock and recheck registration inside it.
Removal that wins the lock yields the ordinary 403 image-access denial; a lookup holding the lock
finishes before removal. The lock is released before writing the response.

Image POSTs use the existing chat/message endpoints with `images: [{name, data}]` (base64) and a
UUID `request_id`. Saved-ID retries use `image_ids` instead of new uploads. The project lock fences
file publication and durable admission; a repeated identity returns its recorded receipt and cannot
replace text or images. Project queue/history and task `status.json.image_messages` own receipts;
the latter atomically projects the conversation and pending delivery, like question acceptance.
An image queue receipt arriving after its `request_id` appears in cached history does not append
another queued copy. History retains the accepted message while the receipt releases the composer.
Retained image claims are recovery records, not waiting messages: the queue projection excludes them
even after the active turn clears and before completion removes the claim.
The existing maintenance tick removes files unreferenced for 24 hours. Committed images follow
conversation retention, including archive, worktree cleanup and project detach/reattach.
Fresh L2 attempts carry delivered image-message captions and deduplicated canonical references from
the same task. Larger sets use native visual readers; history does not become pending delivery again.
See [image delivery and recovery](SESSION_LIFECYCLE.md#image-delivery-and-recovery).

### Task usage

Task token accounting is passive. `usage.py` retains each recorded task owner identity before a
resume or recovery replaces it, and asks the `engines` package for normalized local observations. The daemon
collects at most once per ten seconds per active task; HTTP and CLI reads serve the persisted
`status.json.token_usage` snapshot and do no provider-log scanning. Engine adapters increment byte
cursors over complete JSONL records and retain numeric response/message identities for deduplication
in task-local `token-usage.json`. Each session row also counts its distinct requests and carries its
newest own request's input as context; the snapshot's `context` is the current owner session's.
Discovery reads bounded provider metadata behind the engine seam.
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

### Validation and release

The web build uses pnpm's frozen lockfile and emits `web/dist/` through `make web`. `make check`
runs Python, web unit tests, typecheck/build and the separate Playwright `web/e2e/*.pw.ts` suite.
Python modules run in fresh interpreter shards concurrently with the ordered web phases. The
stdlib runner streams shard output and totals unittest outcomes; either branch failing fails the
gate. The full gate uses Node's process-available CPU count for both language budgets, including
container quotas: Python gets half (at least one), and CI browsers get one worker per CPU.
Both phases overlap fixture waits without changing resource quotas. Local browsers use two
workers. CI schedules individual browser tests across workers; local runs schedule by spec file.
Per-test temporary homes, ports and artifacts isolate concurrent checks without retries.
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

The separate [manual installation lifecycle workflow](DEVELOPMENT.md#installation-lifecycle-acceptance)
uses real packaged application services in a disposable Ubuntu account with fixture engines.
Dispatches run on main with a separate source input; this acceptance lane adds no PR or release gate.

The same browser specs run at 390×844 with mobile user agent and touch and at 1440×900. The
smoke spec reads the real route tree and checks content, assets, console/uncaught errors, API
failures and horizontal overflow. `walkthrough.ts` drives actions, asserts visible text/roles
appearing and disappearing, and saves named screenshots on passing and failing walkthroughs.
Traces are retained only on failure. Outputs stay under ignored `web/ui-artifacts/`. A failed
required check uploads the self-contained HTML report and its attachments as a seven-day GitHub
artifact; passing runs upload nothing. A passing required check with its console log is sufficient
delivery evidence; owners download a failed report only for diagnosis or on a reviewer's request.
See [CI and candidate identity](DEVELOPMENT.md#ci-and-candidate-identity).
The committed design tree holds maintained boards and their spec; review galleries and routine
renderings are not source artifacts. Curated documentation illustrations retain a maintained source.
The required PR job runs every suite phase and preserves candidate identity. Bundled Chromium
runs headlessly with a temporary profile and its browser sandbox disabled inside the
worker sandbox. [Development and checks](DEVELOPMENT.md) documents installation, commands,
timings and candidate identity; [operations](OPERATIONS.md) covers service activation and mobile access.

The sandbox-disabled configuration belongs only to Altitude's fictional UI harness. The L2 persona
gives owners the distinction between the approved Mac validation exception and Linux
dual-protection checks. `make ui-validate` repeats dependencies/build, finite full-Chromium preflight
and named phone/desktop journeys with exact launch evidence in disposable storage. The daemon
receipt identifies the tested tree and actual run profile; native context and cleanup evidence remain
required. No worker permission, service or engine selection change is involved. Missing protection
or failed preflight fault-blocks; a host grant does not substitute for browser acceptance. Fictional
success establishes neither private/deployed-content, native worker nor Simulator acceptance.
See [browser verification](DEVELOPMENT.md#browser-verification).

[Release checkpoints](RELEASING.md) select an exact validated source SHA for an explicitly
published private-preview version and release notes. Pushing the approved tag runs the release
workflow, which checks that commit's main `check` run, builds and attests the archive, `install.py`
and the generated `install.sh`, and publishes them as the GitHub release that the one-command install
fetches. They add no runtime lifecycle state and do not gate automatic activation of merged changes. The UI and testing rules remain in the
project instructions file, which both worker personas direct the task owner to read first.

### Repairs

A [repair](OPERATIONS.md#repairs) is the one agent session Altitude starts outside every sandbox,
and only the operator starts it: from a paired browser, or with `alt repair` in their own terminal,
or `install.py --repair`, which loads the installed release's verified `repair.py` when `alt` cannot
start. `repair.start` refuses when the actor is not the operator, when a task, attempt or door
variable is set, inside a repair, and when its process sits in any of Altitude's jobs
(`platform.process_table`, with the same unit names on both platforms): reviewers and coordinator
turns, which carry no actor, commands under an operator grant, and browser terminals, where a `run`
block runs on the operator's Enter whatever its quoting. The app's routes (`POST /api/repairs`,
`/api/repairs/<id>/message`, `answer`, `keep-going` and `stop`, and `GET /api/repairs/<id>`, which
carries what the agent ran and read) need a paired device's cookie and pass
the page's own-origin and [own agents](#altitudes-own-agents) checks; they refuse the machine key
outright, since the CLI, L3 and anything that read the key hold it. Each request carries an identity
the record keeps, so a double tap, a retry after a lost reply or a replay acts once.

Each repair keeps `repairs/<id>/repair.json` and `brief.md` under Altitude's home, the record changed
under its folder's `record.lock`. A terminal repair's record holds its route, engine, problem, failed
update, version, start, end, report, check and outcome. `repairs/lock` allows one repair at a time
across both routes, and `running.json` names the repair holding it: `alt repair` holds it exclusively
and passes the descriptor to the session; a repair from the app's recorder holds it for as long as it
runs. The kernel releases the lock when its holders end, however they end, so a crash, a killed
recorder or a reboot leaves none behind. A repair's steps and output are the operator's alone, so every job in
Altitude's sandbox and a Codex coordinator's profile hide `repairs/` ([isolation](#isolation-and-landing)); Altitude
makes the folder when it starts, so on Linux, where the sandbox hides a folder by covering it at launch, a repair
begun while a job runs stays hidden from that job too.

A terminal repair's session is `engines.interactive(engine, prompt)`, the engine's own CLI in its
normal mode with the operator's sign-in and settings, run with `ALTITUDE_REPAIR=<id>` in the repair's
folder. A repair from the app runs `engines.repair_session(engine, folder, model)`: the engine's
headless mode with its permission prompts and sandbox off, outside Altitude's sandbox, with deny rules
on its file tools for Altitude's keys, certificate keys and engine sign-ins where the engine has them
(a shell command still reaches them), and the sign-in Altitude's own runs use (for Claude, its own
configuration folder inside the repair's and the token read from its file at each turn). Only the
repair module builds this session, and every task worker and reviewer still launches inside
Altitude's sandbox (`tests/test_repair_app.py` keeps both so). The engine and model are chosen as for
a small task (`route.pick_engine("l2")`), and a repair counts toward no running-task limit.

`repair.start_app` writes the brief and the record, then copies `altitude/repair_job.py`, which uses
only Python's standard library, into the folder as `recorder.py` and starts it with
`platform.detach` as a job of its own (`altitude-repair-<id>`), outside the service and its unit, so
it survives the service's restart and a reinstall that replaces Altitude's files. The recorder takes
the lock, runs the agent one turn at a time with the turn's prompt on standard input, and appends each
line the engine writes, with the time it arrived, to `steps.jsonl`. The record's queued inputs are
the turns: the operator's problem, messages, answers and Keep going, each as a quoted prompt naming
the operator's words as theirs, never as Altitude's instructions; the first turn's session id resumes
the rest. The agent asks with `alt repair ask`, which records a question and ends its turn; the
answer queues as the next input. The recorder ends the session when the agent has reported, the
operator stopped it, or the deadline passed (two hours, plus an hour per Keep going): it stops the
agent, records the end and expires open questions; when it exits, the service manager stops every
process still in its job (the control group on Linux, the coalition on macOS). Keep going after the
time ran out starts a new recorder that resumes the same session; Continue repair after a report does
the same with the report kept as an earlier round. `repair.stop` asks the recorder through the record,
stops the job itself when the recorder does not answer, and records Stop unconfirmed while the job
still runs. `platform.user_jobs` lists user services and timers, launch agents and crontab lines before
and after a session; what appeared and still runs is the record's `left_running`. `alt install`,
`update`, `recover` and `service stop` inside a repair record that Altitude is restarting, and the
service records it back when it starts.

The service's tick runs `repair.check_ended`: every repair without an outcome that is not running
gets `installation.doctor()`, `installation.installed_release()` (the active version and its
manifest check) and the version the service runs, and `repair.outcome` sets `repaired`, `changed_by_hand`, `confirm`, `not_repaired` or
`no_report`; a repair whose recorder or process died before recording its end gets its end time then.
`GET /api/repairs` returns the running repair, the coding agent a new repair would use, and every
record without briefs with its status, which Work filters to the running repair and those ended
within 72 hours and Settings' All repairs lists whole; `GET /api/repairs/<id>` one record with its brief and, for a
repair from the app, its timeline (events, the operator's words, the agent's steps and words, its
questions and earlier rounds' reports), time left and whether Keep going applies. The overview
carries the running repair and the newest app repair's questions, open or expired within a day, for
the update notice and Needs you. Reads go
through `storage`, so a link planted in a repair's folder reads as no record or no brief. A change to a
repair publishes the change stream's repairs key, which refreshes the shell's repair reads.

### Agent chats

An agent chat is a conversation the operator drives with one of a project's coding agents, from a paired
browser, with no brief, proposal or pull request ([walkthrough](WALKTHROUGH.md#agent-chats)). Each chat
is one record, `~/.altitude-chats/<project>/<id>/chat.json` beside Altitude's home, changed under the project lock:
its name, the model choice and the engine, model and session it answers with, its copy's base commit,
whether and when it was archived, its messages, its turns and its system lines, each with a sequence
number that orders the conversation. The operator's images go to the project's image store with the
message that carries them, where the project's agents can read them as they read a task's, and `images` reads a
chat's record as it reads a task's.

Each message waits until the chat's previous turn ends; the messages waiting then go together as the
next turn. A thread of the service (`chats.run`, one per chat) claims them, picks the engine and model
the way New tasks does (`route.pick_engine("l2")` with the chat's choice), and starts the turn's
worker with `engines.start_chat_turn`: the engine's headless worker in a detached job of its own, as a
task's worker runs, inside Altitude's sandbox and restricted as a routine run is (no operator engine
links, no GitHub sign-in, no Send now), except that it writes the project's shared Git store as a task
worker does, objects and branches but not its settings, hooks or worktree records, so it can commit on its own branch. Its `alt` admits only the read commands a task worker has
(`ALTITUDE_ACTOR=chat`). Its persona is `personas/chat.md`. The chat's copy is a worktree on the
`chat-<id>` branch in the project's worktree folder, made with `--no-track` from the fetched main when
the first turn starts. A copy whose folder went missing comes back on its branch, keeping its commits;
main is fetched again only for a chat without a copy, and a copy main cannot be fetched for fails the turn
with Retry. Tidy and task cleanup leave `chat-<id>` copies alone. A turn takes no task slot and waits only
while Altitude restarts.

The first turn's session is the chat's session, and later turns resume it from the chat's own engine
folder (`~/.altitude-chats/<project>/<id>/l2-engine`). When the answering engine is not the one holding the session,
because the operator chose another model or a resume fails, the turn starts a new session given the
conversation so far as quoted evidence, and the conversation shows that line. The thread follows the
worker (`engines.owned_worker`), records the session's context, and records the turn's end: its
answer (`engines.worker_answer`), Stop, or why it could not answer, with Retry, which sends the same
messages again. A message sent as the thread ends is taken by that thread, which another could not start
beside it. The worker's job outlives a restart of the service: the service records a restart line
on each chat whose turn's worker is running, and its tick finds every chat with a turn running or messages waiting
(`chats.due`) and follows or starts it again. A turn whose worker is gone, or whose start a restart
interrupted, reads cut off.

Make a task (`chats.make_task`) makes a task from the chat directly, as `alt task new` does, with the operator
as its author: the operator's words are its request, and the conversation follows them as quoted evidence, which
issue intake does not read. When the copy has changes since its base, Altitude saves them as one commit on the
copy's last commit (`git add -A`, `write-tree`, `commit-tree`, so no project hook runs on the agent's content) and
`tasks.new` makes the task's branch, `worktree-<slug>`, at that commit before the task record exists and records it
as the task's `start`; the dispatcher checks the task out on that branch while it is still at `start` and refuses
it as an orphan otherwise. The copy and its branch are then removed, and the next turn makes a fresh copy of main,
so the changes live in the task alone. A chat without changes makes a task that starts from main and keeps its
copy. Make a task refuses while a turn runs or a message waits, and no turn starts while it runs; the conversation
records a line naming the task, which the page shows with the task's card.

Archive refuses while a turn runs or a message waits, and while the copy has changes (committed or
not, since its base) the request has not accepted; it then removes the copy, its branch and its
scratch folder and keeps the conversation and session. Reopen makes a fresh copy of main at the next
turn, which continues the same session and is told first that its earlier changes are gone. Delete
removes an archived chat's folder for good, its engine session with it, and anything left of its copy.

`GET /api/chats/<project>` lists a project's chats with their state and latest activity;
`GET /api/chats/<project>/<id>` is one chat with its conversation, each turn's steps (read from the
worker's events as a repair's are, `altitude/steps.py`), its copy's changed files and context.
`POST /api/chats/<project>` starts a chat with its first message, and
`/api/chats/<project>/<id>/{message,remove,retry,rename,model,stop,task,archive,reopen,delete}` change one.
Each request carries an identity the record keeps, so a repeat acts once. Every chat route takes a
paired device's cookie, passes the page's own-origin and [own agents](#altitudes-own-agents) checks and
refuses the machine key, as repair routes do: no agent reads or steers a chat through Altitude. Every job in
[Altitude's sandbox](#isolation-and-landing), L3's included, and a Codex coordinator's own profile hide the chats
folder (`config.CHATS`), which altd makes with its home, so no agent reads a chat's record or its turns' output; a
chat turn's job reopens only its own engine folder, which keeps its session, and its scratch folder. The Monitor rows
of answering chats go only to a paired browser, never to `alt monitor`. A chat's copy and branch are readable to the
project's other agents, as a task's are. Like a task worker, a chat's agent reads what the operator's account reads outside the
hidden places, other projects' code included, and reaches the network. The operator's terminal reads chats with `alt chats`. A chat's file changes
publish the change stream's project key, which refreshes its reads; a running turn is a Monitor
session row. Chats never appear in Needs you, the project's tasks in flight or L3's records.

### Web delivery

The web app presents routine progress with accessible visual status marks: loading and pending
actions use spinners, and saved/copied receipts use checks. Action labels retain their footprint
while pending. The operator's own saves (answers, merge approvals, messages) render their result at
the press: `web/src/data/pending.ts` keeps each write on its way beside the query cache and overlays
the overview, project and task reads with it, until the first read begun after Altitude's save shows
the record; a saved entry goes once every cached view has read past it or expired. A write with no
reply stays until a read of its task shows whether it was saved, read again after each later read that
works. A refused write stops overlaying and stays with its card until Retry or an edit, or a read of
its task that lists it as saved; a refused answer also goes once a read shows none of its questions
still waiting. Entries live only in memory, belong to their project, and go with it, with pairing and
with a reload. Alerts read the record without the overlay.
Settings saves (machine and update switches, Weekly suggestions, project routing and model picks, New
tasks, removing a device), suggestion Dismiss and Undo, and Skip review render the same way through
`web/src/data/saves.ts`: each save is a mutation keyed by the change, and the hooks of the reads it changes
(a setting's own read; the project and overview reads for a suggestion; the task, project and overview reads
for a skipped review) lay the saves still pending over the server's data, so every view shows the change.
The control holds still until the save lands; the save ends only after those reads in flight are cancelled
and the stored value is in the cache. A refused or lost save keeps overlaying until those reads are fetched
again, then offers Retry; `useRefused` keeps a change's latest refusal for every view, so a card that left at
the press comes back saying why, and a view shows it only while the read still lacks the change, so a save
Altitude kept despite a lost reply says nothing more. An overlay is idempotent: a skip takes its findings off
a count only while the read still counts that review. Nothing is kept beyond those mutations, which live in memory only. Voice choices move
**In use** at the press within the voice form, which already holds the saved choice it edits against.
Screen-reader names retain the status words. Unavailable terminal, voice and alert
controls disclose their reason on press without invoking the action. Authored blockers, errors,
setup instructions and resource/privacy consequences remain visible; task-page descriptions do
not repeat the state chip or question card. These presentation rules do not change delivery,
admission, routing or permission decisions.
The empty Live session panel retains its dispatch or resume explanation. Its waiting state also
keeps a queued or held task from reading a previous session before work starts or resumes.

altd serves the built app from `web/dist`. `index.html` is uncached, so each open learns the current
build; Vite names every file under `/assets/` by its content hash, and those files are cached as
immutable. A warm open therefore fetches only the page and its API reads, and a cold open, the first
on a device or the first after an update changes the app script, downloads the script and styles
before anything shows. Hashed scripts, styles, WebAssembly, word lists, SVG and JSON travel
gzip-encoded (`Vary: Accept-Encoding`) to browsers that accept gzip. Website files, including the
homepage and client-route index, are read through regular-file descriptors beneath the pinned
build directory. Component normalization uses held parent directories and judges the existing
private-place policy before metadata lookup. Inside-build links use relative targets, the canonical
build-root spelling or its verified configured spelling; other external absolute aliases and paths
that leave the build and return are refused without lookup. Active link expansions detect cycles
without limiting acyclic chains. Completed targets are reused only as request-local addresses,
with fresh private-policy, no-follow and identity checks, so branching acyclic aliases do not repeat
their expansions. Directory and file opens refuse replacement links. A request keeps its opened
build namespace if that build pathname changes. Root and client-route pages remain HTML and uncached; directly requested files
use their resolved target's type and cache policy. Compression uses the same verified descriptor.
The process keeps at most 64 compressed assets, checking each opened file's device, inode, size and
modification/change timestamps before reusing an encoding. Replacement or modification invalidates
that entry. This does not promise an atomic snapshot against a writer changing file contents in place.
Gzip cuts the cold download to about a third: in the fictional walkthrough on simulated
slow 4G the page appears in about 1.7 seconds instead of 4.4. The punctuation model's packed weights
barely compress and travel as stored. Opening requests no dictation asset; `web/e2e/first-open.pw.ts`
walks cold, warm and slow opens with dictation assets failing.

A page keeps running the build it opened with. Activation replaces `web/dist`, so a file that page
loads later, such as document formatting or the terminal's code, is gone and altd answers 404.
The terminal view then says whether the served page names another app script, meaning Altitude was
updated, or Altitude could not be reached (no answer within five seconds counts as unreachable), and offers
Reload. Only a failed load of that code shows this; an error inside a loaded terminal reaches the route's error page. It does not reload by itself, and says
that a reload clears unsent typed text, which lives in the page's memory. The browser keeps a failed
module import for the page's lifetime, so only a reload recovers. The terminal's code is one script
that its import alone requests: its stylesheet (about 1 KB compressed) travels with the app's, because
Vite also preloads a lazy module that has a stylesheet, and WebKit keeps a failed preload across a
reload, which would leave Reload broken. Earlier builds are not retained.

Document formatting has one shared deferred loader for the task-file reader, shown reader and
expanded system information messages. A rejected formatting import stays inside the document:
**Formatting unavailable**, the exact inert source text, and **Reload page** keep the surrounding
view usable. The shown reader also keeps its saved embedded images below the source, with their
existing gallery controls; nonembedded images retain their position. Remote URLs in source are
not fetched. Conversation-hosted readers warn that reloading clears text typed but not sent; the
separate file tab has no draft warning because reloading it leaves the conversation tab alone.
Closing a reader or hiding a system card keeps the unsent draft. A failed shared import also leaves
subsequent documents readable as source. Only the operator's explicit Reload page reloads the app;
API read failures retain their existing Retry, and errors inside a loaded renderer reach the route's
error page. Import rejection also covers a module that cannot finish initialization; an error
thrown while rendering a successfully loaded module propagates. No update or network cause is
inferred from import rejection. Document styles travel
with the initial app CSS, so no deferred stylesheet preload can strand formatting across a reload.
The formatting script stays deferred, preserving the initial download's performance limit.

Every API reply is `no-store`. The app keeps what it has read in memory only (TanStack Query, per
project and task), re-reads a view on each open and on every change event, and drops it all on
re-pairing; nothing is prefetched or stored on the device. On a slow link the wait is therefore the
download of the view itself. The task view carries the task record, its decisions, review, messages,
documents and report, and the repository link for pull request links, so the
task page reads no project view. The record's review, decision and message-delivery ledgers, which
the view already presents, stay on the server. The overview, project, task and live-session views travel gzip-encoded
(`Vary: Accept-Encoding`) to browsers that accept it, four to five times smaller; a running task's
view and live session are re-read every two seconds. Replies that can carry a pairing code or key
(access, pairing, devices, alerts, terminal) always travel as stored. Compression lets someone who
watches the encrypted traffic, and can repeatedly plant chosen text in the same reply, infer secret
text in it from reply sizes; the operator accepted that for these four views on 2026-09-28.

### Conversations and navigation

The HTML links the approved Climb SVG favicon, a multi-size ICO fallback, the 180px Apple touch
icon and a web app manifest. The bundle includes its IBM Plex fonts, so pages make no third-party
requests and never wait on one to render. The manifest defines the root identity/start URL/scope,
standalone display and ordinary 192/512px plus maskable 512px icons. Public metadata and icons are served
uncached by the existing static handler; the alert worker has no fetch handler or offline app
cache. Native icon copies can outlive a page reload; [operations](OPERATIONS.md#refreshing-home-screen-and-bookmark-icons)
describes refreshing shortcuts and installed apps.

The Python server owns state transitions and JSON APIs. The React app is one shell around four
pages, specified in `design/wireframes/SPEC.md`: Needs you at `/` (every decision across projects as
compact cards in one column, answered through `POST /api/decide`), the project page at
`/projects/<name>` (the §3.2 header with its status line and overflow menu, the L3 conversation, and
the work panel), the task conversation (including redirects from `/projects/<name>/decisions/<slug>`), and
Monitor. `/projects` and `/chat/<name>` redirect to the project
page, and with no managed project every project route shows First run: hosting, coding tools and
one project, held in `?step=` with Back and Continue controls. Numbered positions describe navigation,
not completion. Name, incident reporting and voice are optional Settings links. What the agents need
reads `GET /api/prerequisites`, `installation.prerequisites()`: the
doctor's GitHub CLI checks (the pull request fields `alt land` reads, from the list `gh pr view --json` prints
without a repository, sign-in or network request, then sign-in), each engine's local sign-in status through the
engine seam (`engines.sign_in`), on Linux the agent sandbox (`platform.sandbox_tool`, optional when a signed-in agent
brings its own sandbox) and Git, as met, unmet with the terminal command to run (the install command from
`platform.install_command` or `engines.install_command` for a missing tool or a GitHub CLI too old to land, else
its sign-in command, or, for an engine Altitude signs in itself, `sign_in`, which shows **Sign in** in its row
instead), or optional once one engine is signed in. With no engine installed, one row asks for at least
one coding agent and lists each engine's install command as an option. The projects step lists the folders under the projects folder
(`config.project_roots()`: the machine setting `projects_folder`, else `ALTITUDE_ROOTS`), changes
that folder in place and starts L3 for one or all of them through `POST /api/project/add`, in order,
staying up until L3's first reply or the error row that stands in for it. At 1024px and wider the rail is 260px and the work
panel is 340px, inline at 1280px and wider and an overlay from the header's panel button below that;
narrower is the phone: one 54px identity/activity header and an 84px tab bar (Chat, Work, Needs you,
Monitor). The shell follows visual viewport height and offset, hiding bottom navigation during
detected software keyboard use and restoring it on dismissal, including when focus remains in the
field. Editable focus alone, toolbar motion and pinch zoom do not hide navigation; without sufficient
viewport evidence it remains reachable. Leaving the app forgets detected keyboard use, each return (shown,
restored or focused) measures afresh, and a deep contraction without keyboard use, such as a stale height
reported on return from the background, leaves the shell filling the screen. Browser-managed safe areas
remain intact, with any bottom inset owned once by the visible dock. The project header opens the switcher sheet, and a task conversation
pushes over its tab with a back control. Those widths are named once, in `web/src/shell/breakpoints.ts`. The
selected project is browser state under `localStorage`, set by the rail, the switcher, a project
route, or a Needs you card; the theme (light by default, dark on request) persists the same way. The
rail's engine readout renders `GET /api/overview` `engines[]`, one row per configured engine with the
display name the engine seam gives, so the web code names no provider; the same read carries the
projects folder First run names and the operator's configured name.

First run's folder browser reads `GET /api/folders?path=<absolute path>` (no path means the home
folder), one folder per request and only when the operator opens it; nothing is scanned, indexed or
kept. Folder operations and website serving share the platform seam's descriptor traversal. It judges
each candidate against the [private policy](DEVELOPMENT.md#macos-private-places) before lookup,
holds accepted ancestors and verifies opened identities. Browser aliases may traverse a nonprivate
exterior location and return inside HOME; the final inspected address establishes containment and
hidden-folder filtering. Website aliases retain their stricter build anchors. Browsing returns
`path`, `parts` (relative to home) and `readable`, plus one row per visible
subfolder whose resolved target stays inside home and outside the private places: `name`, `path`, the registered `project` for that
folder and whether it holds `.git`. It never returns file names, sizes or contents, and an unreadable
folder returns `readable: false`. The browser adds through the existing `POST /api/project/add`;
**Type a path instead** covers folders outside home. `POST /api/projects-folder` applies the
operator's choice through the machine settings request, like `alt machine set --projects-folder`.
Selection, discovery, registered-address matching and child/`.git` metadata use the inspected
namespace. Registration checks final directory type without requiring final read/list/execute
permission; selection requires read/search. Caller-specific raw and canonical alias budgets remain
distinct, and completed-address reuse retains full logical expansion cost. Marker presence checks
never read marker contents; unavailable or private targets report no marker. Container containment
uses inspected candidate and volume addresses. Settings retain the selected spelling.
Later Git, hooks, engines, other runtime path uses and unrelated private-check consumers remain
outside this operation boundary; saved strings are not persistent safe capabilities.
TLS and installation location checks read the same `config.project_roots()`.

Task, decision and project reads refresh through one change stream per browser app. The shell opens
`GET /api/changes`, a server-sent event stream: once a second altd compares the identity, size and
modification time of the project registry and, per registered project, its hold, project log,
archive folder and each open task's `status.json` (which holds its questions) and `events.log`.
Records written by altd or any `alt` process therefore signal alike, and nothing is stored beyond the
connection. A `change` event carries `{"projects": [...]}`, the projects whose records moved (every
registered project when the registry did); the page refetches the mounted overview and monitor plus those projects'
project and task queries. The server reads its baseline before the response opens, and every open,
first or after a reconnect, refetches all of those queries, so a change between a snapshot and the
subscription is never lost. Events carry no records: repeated events only refetch canonical reads.

Decision alerts (issue #221) ride that stream. `web/src/data/alerts.tsx` holds the per-device setting
under `altitude.alerts`, which the Settings → Notifications switch turns on and the Needs you pill names, the permission states, and the keys already alerted under
`altitude.alerts.seen`; the shell watches the overview queue on every page. Only a newly published operator
question alerts: faults, stops and reviews stay in Needs you without one. Every alert is shown through
`web/public/sw.js`, the one service worker, whose registration Android requires for a notification and whose
click handler routes the open app to the decision before focusing it, or opens the decision when there is no
app or it cannot be focused. Content is the project and task name at most. `tasks.decisions()` gives each operator
question its `alert_key`, the one identity altd's wake, the page and the worker alert on: project, task and
question group, followed by the time of the operator's latest message on that task once there is one (the
task's `replied`, set with `handed_back` on every operator message and never cleared). A rewording, the
escalation that republishes a block, or a member L3 settles keeps the key, so the decision alerts once; a
decision asked again after the operator replied gets a new key and alerts again, even when the reply and the
new ask fall between two reads. The key comes from the task record that put the question on the operator's
turn, so a reply arriving during the read never lends its time to a question it hands back. `digest.queue()` marks a decision `alert_held` while its task
is still moving: L3 has that task's block notification queued or running (`l3.reading_blocks`), or its owner is
running, queued or due to resume, as after L3 answers it. `alt task block` queues that notification under the
lock that publishes the question, so no reader sees the question before L3 is due to read it. A held decision
shows in Needs you, is neither alerted nor recorded, and alerts once the task rests, or an hour after it was
asked (`digest.ALERT_HOLD`) so that an L3 or owner that cannot finish never hides it.

A device that push can wake is alerted by its worker alone, open page or not, so a decision never shows twice
there; on any other device the open page alerts, and a decision visible in Needs you or its owning task is
recorded without one. The recorded keys make refresh, reconnection and polling repeat none, and turning alerts
on records what already waits for the page and for the worker alike. While alerts are on, an open page closes
the banners whose key has left the queue, and the generic one, on each overview change, every 30 seconds and
on each return to the screen: WebKit ignores a close within 30 seconds of a banner appearing. A tab with
alerts on keeps its change stream while hidden; every other hidden tab still closes it.

A device is also woken while Altitude is closed. `altitude/push.py` keeps the VAPID signing key in
the access folder, which no Altitude sandbox can read, and the subscribed endpoints in `~/.altitude/push.json`, both written only by the
daemon; each start removes a file earlier releases left in Altitude's home and logs it; turning alerts on registers the device's endpoint through `/api/alerts/subscription` and reads the public key
from `/api/alerts`, and with alerts on subscribes and tells it again each time Altitude opens or returns to
the screen there, one step at a time with the switch. The answer says whether altd still held the endpoint
and whether that device's push service refuses Altitude's alerts; the page replaces a subscription made with
an old key, or one altd dropped, and tells altd to forget the endpoint it replaces. Each tick, a decision key that newly may alert is recorded as owed to every device
(`owes`), which is woken with one empty, signed POST, and logs `push wakes <n> device(s) for <m> new
decision(s)`; an owed device is tried each tick until its push service takes a wake for it, a decision
whose task is moving again stays owed until the task rests, and a decision settled first is owed no more, so
a device whose owed decisions were all settled is not woken. A wake's outcome applies only to the subscription
it was sent for: a device that subscribes again meanwhile starts clean. No payload is sent, so no
encryption library is needed and no word of the decision leaves the machine. Nothing else wakes a device:
Safari ends a subscription after three pushes that show no notification, WebKit keeps a banner it is asked to
close within 30 seconds, and iOS stacks a second banner for a tag rather than replacing the first, so a push
sent only to clear a banner leaves another one standing. The worker's `push` handler reads `/api/overview`,
closes the banners whose key is no longer waiting and the generic one, and names the project and task of
each new decision, one banner per question group, without sound while an Altitude window is on screen.
Because a re-asked decision carries a new key, the worker need not have seen it leave. Every push shows a
notification that says something true: out of reach of Altitude, "A decision needs you" with "Altitude is
out of reach, so this alert can't name it."; with nothing new, as when several wakes arrive together, it
silently names what still waits on the operator, leaving held decisions out: "<project> still needs a
decision" with the task name under that decision's own key, or "Still waiting for you" with one
"<project> · <task>" line per decision, opening Needs you; with only held decisions, "No decision needs
you now" / "L3 or the task's owner is handling it first."; with none, "No decision needs you now" / "It was
settled before this alert arrived." An answered banner
therefore closes when a page next reads the queue or the next alert wakes the device. An endpoint the
service reports gone (404, 410) or made with another key (Apple's 400 `VapidPkHashMismatch`) is dropped,
any other refusal is logged and kept for that device's line under the switch, and a machine without
OpenSSL or outbound reach simply has no push, which the switch states.

Each device chooses in Settings › Notifications what its banners reveal (project and task, the project
only, or only that a decision waits), how often it is alerted (every new decision, at most every 15
minutes, or at most once an hour) and whether alerts sound. The choices live on the device, under
`/preferences` in the worker's Cache Storage `altitude-alerts`, the one store both the page and
`web/public/sw.js` read, so a phone can stay discreet while a desktop names everything. A device that never
chose gets the project and task, every decision, with sound; a store that cannot be read or written names
nothing, so a storage failure never reveals more than was chosen nor leaves a push without its banner, and
Settings says so. With a pace chosen, several new
decisions read "<n> decisions need you" with one line each, as much as the device reveals, and open Needs
you; sound off makes every banner silent. Altitude learns only the pace, because it sends the wakes: the
subscription carries it, `POST /api/alerts/pace` changes it from Settings, and `push.json` keeps it per endpoint
(`every`) with when that device's push service last took a wake (`woken`). An owed device is woken only
once its pace has passed since then, so one wake covers what arrived meanwhile; Altitude takes a change
before the device saves it, and the subscription the page re-sends each time Altitude opens there carries the
device's pace, so the two copies converge. A device without push paces the open page the same way, holding fresh keys
unrecorded until `altitude.alerts.last` plus the pace has passed.
EventSource retries a dropped connection after the stream's 3-second `retry`; a refused stream, such
as during daemon activation, reconnects after 5 seconds. A hidden tab closes its stream and reopens it,
refetching, when shown, so background tabs hold none of the browser's connections to altd. Ordinary 20-second polling, the 2-second
reads of running tasks and active conversations, and the conversation's own history reads continue
independently, and a comment line every 15 seconds keeps an idle stream open. `POST /api/l3/start` runs the start
turn for a managed project whose L3 never ran, from the header's Start L3. The conversation is the only
way to create a task from the web: the L3 turn creates it through `alt task new`, and altd records
the slug on that turn's assistant row (`tasks: [slug]`), which the conversation renders as a task card
under the reply. An issue filed during a turn through `alt issue new`, or the issue an
`alt incident new|publish` newly created, is recorded the same way as `issues: [{title, url}]`; a turn
that fails after creating either carries them on its error row. When L3 thinks the work might not be necessary, its chat turn gives its reasoning and runs
`alt task offer '<title>'` instead of asking; altd keeps the title on that reply's assistant row
(`offer`) when the turn created no task, and the conversation draws the Create task button with the
title under the latest such reply (spec §3.3); the reply text carries no Create task wording of its own. A press posts `{project, offer_turn}` to `POST /api/chat`, and altd
queues the operator's ordinary chat message `Create task: <title>` carrying `offer_turn`; the conversation
shows that message, queued or in history, as the state of the button rather than as a message. altd accepts
the press only while that reply is the latest human-facing row and no operator message waits in the
queue; anything else is refused with 409 and the reason. A repeated press returns the message it already
queued. The turn that answers it gives `alt task new` the reply's turn id, which altd records on the
task as `offer_turn` and uses to refuse a second task for the same reply, so a retried, restarted or
repeated turn never duplicates it. Engine selection is in the project's Settings page and the desktop composer's pill;
a non-Auto pin stays named in the phone header. It pins the project's L3 to one configured engine, named as
`engines[]` reports it, until set back to Auto; Auto uses project preference tiers, weekly headroom
within ties and the session continuity rule described above. A chat turn belongs to L3,
not to the page that started it: when the page leaves mid-stream, the turn finishes and its answer
lands in the history. Its reply is everything the coordinator wrote in the turn, in order: a Claude
turn streams its text as written, each text after a tool call starting a new paragraph, and stores that
same text, taking from each finished message any text the stream did not carry (Claude can send a message
without streaming it), and keeping a message Claude rewrote after part of it streamed as it finished, which the
page shows once the turn ends; a Codex turn streams nothing and stores its messages joined the same way. The typing indicator
stays under the streamed words until the stream ends, and with no stream open, while `active` names the
turn. Each send carries a page-made UUID `request_id`, kept as the stored user row's
`request_id`, as the queue row's `id` and, once delivered from the queue, in that row's `queue_ids`.
Until the server shows it, the message and the reply it streams live in a project outbox in the client
query cache under `chat-outbox` and the project name, one entry per send id, with no timed eviction.
Leaving the conversation, for the phone's Work tab or another project, keeps the entry, and returning
shows it. An entry retires when the snapshot rendered lists its queued row or stores its turn's answer
or error, or when a successful server read that began after the send finished holds the turn its
stream named: finishing a send cancels reads already in flight, and the page's own cache edits are
not reads. Once the stream names a turn, that turn alone identifies the exchange, so after Send now
joins it the send's first answer cannot retire the reply still streaming. A refused or unconfirmed send
keeps its entry as the message's own failure, with no activity held: Retry replaces it under a new send
id, Edit drops it and returns its words to the composer, and an unconfirmed entry retires once a read
lists its send id. Until acceptance an entry also keeps the images as sent (for Retry and Edit) and the id
of its composer recovery copy, which it releases once accepted, edited or retired. Only the send
that starts an entry adds it; later callbacks update it only while it exists. The task conversation keeps
the same outbox under `task-outbox`, the project and the task, and each of its entries also records what
the message was sent to: the question or question group revision it answered and the stopped session it
continues. Its Retry sends there again and is offered only while a Send would go to the same place;
otherwise the message offers Edit only. Pairing clears every outbox with every query, and removing the
project drops its outboxes with its reads and deletes its composer recovery, stored and in the document; a voice
Send still transcribing for it then sends nothing and restores nothing. Unsent text lives in the existing
client query cache under `project-draft` and the project name, without timed eviction, until document
reload. Every draft change, including manual clearing and send admission, updates that entry.
Submitted-text recovery
retains its original conversation identity. Composer
unmount stops ordinary unsent recording and releases its microphone tracks through the recorder's own stream.
Explicit voice Send belongs to its original conversation beyond component unmount: its captured text,
images, destination and task reply context stay together until transcription and admission finish. A remounted source
composer shows the pending operation, while another conversation keeps its own draft. Outstanding
callbacks and cache updates retain the source project. The destination renders its own history,
queue and active turn; switching back reconstructs those server records, including Retry for a
failed turn, alongside its retained draft. `GET /api/chat` reports the server-owned active turn as a stable id, start time,
and trigger without copying its prompt, and it is the conversation's only authority: the page polls
it independently of open response streams and never infers a turn from `busy` or the last history row. A fresh mount or reconnect renders
the record as the typing indicator for a chat turn, or as the line "L3 is handling <what>" for a
server-triggered one; the tab that started the turn keeps its streamed reply instead. The stream's
first line names the turn (`{"turn": {id, started_at, trigger}}`) before any text, and the
history rows carry the same id, so the local rows stay until history owns the turn and a stored
assistant or error row wins over a raced active snapshot. An assistant row marked `interrupted`
renders only its partial reply, with no notice; with no partial text it renders nothing, and the
operator's next message follows directly under the stopped one.

The conversation reads `/api/chat/<project>?limit=60`. The limit counts human chat rows and system
rows (server-triggered turns and FYIs) separately, so a burst of system events never pushes the latest
operator messages out of view; `chat.jsonl` keeps every row and `alt l3 search` reaches older ones.
A link to an improvement review's card or line (`#improvement-<run>`, from its page's Recent runs, Latest
or Needs you) adds `&reach=<run>` while that run is one the project lists, and the history reads every
row back to that review's line, so the card comes into view however old it is.
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
the routine runs before and after them. So does a notable system turn, one during which L3 created a
task or filed a GitHub issue: its line ends with Open task or Open issue, and its card shows the
created task cards and titled issue links. Their full concise text is visible in the existing compact
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

`ProseScope` supplies the displayed project's identity independently of repository metadata.
Absolute paths, local file URIs and labeled Markdown file links outside code use the same ordinary
underlined link style and open `/projects/<project>/file?path=<reference>` in a separate tab.
The original target is retained, including URI encoding; labeled links expose it on hover and
the reader shows it in full. Bare paths with spaces use a Markdown angle-bracket destination or
a percent-encoded file URI. Stored messages remain unchanged and rendering performs no file reads.

`GET /api/files/<project>?path=<reference>` reads only regular UTF-8 `.md`/`.txt` files directly in
a registered project's active or archived task directories, up to 1 MiB. Original active-task paths
resolve through task identity after archival; the response identifies the current location too.
Task existence, path shape and the configured runtime root bind each read. Descriptor-based
no-follow directory/file reads refuse symlinks, traversal and special files, bound read size, and
reject files changed during reading. Foreign-host file URIs and other locations are refused.
There is no listing, recursive publication, native opener, execution or arbitrary filesystem route.
This uses existing private network/OS-user access: all eligible task-root documents are readable,
even without a conversation reference. Extension and location do not classify confidentiality;
trusted local processes can still copy or hard-link contents into an eligible document.

The file page shows the complete reference and Copy path, with current contents read on opening.
Markdown uses the shared inert prose renderer with document headings and a Raw toggle; text files
remain literal. HTML and images stay text, with no embedded resource fetch or command execution.
Loading, empty, missing, denied and unsupported states are explicit; Retry clears stale contents
while reading, and copy failure leaves the full path selectable. Reads are uncached. Closing the
tab preserves the source conversation and draft. This feature introduces no provider coupling.

### Message delivery and voice

A message sent while L3 is busy is queued, never refused: the composer stays open, the send control
keeps its arrow, the header names the active work, and the message shows as an outlined queued bubble
with a quiet × (**Remove**), followed by one **Send now** for the operator queue. Busy controls
show a spinner in place; unavailable controls reveal their reason on press. Removed messages leave
the conversation and announce removal accessibly. When a turn starts the row becomes its bubble
and typing indicator. Queue claim writes the
user history row and publishes the active record under the same lifecycle guard used by the API's
history/queue/active snapshot. Routing precedes claim; failed history admission restores the waiting
queue. The claimed turn reuses its admission and route choice, so the text stays visible through
handoff and is delivered once. A control takes the operator's chat back off the queue only while it
waits. Server-triggered work is also visible in its FIFO position but is not editable. The queue is a
file in the project directory, so a reload, another device and a restart all see the same pending
messages. Each turn drains it at its own boundary rather than at the next tick: consecutive text chat
messages for the same conversation fold into one turn in arrival order, each on its own line, while
image-bearing and server-triggered messages keep their own turn, and nothing runs while a turn holds the project's L3 lock.

An operator message whose turn finds no eligible engine, or whose every option refuses before provider
output, is kept rather than failed. Its user row stays in history under the turn id and no error row is
written; the queue keeps one chat row carrying that `turn_id`, its text and any images. `l3._keep` orders the
queue as an accepted Send now group, then kept messages in send order, then the rest. Kept rows fold like
any text chat row of their conversation, and while one waits for its retry time only Send now rows run, so
no message overtakes it. The claim logs only the rows not yet in history: kept rows alone publish the last
one's turn id without logging again, so the reply lands beneath it, and kept rows with newer rows run under
a new turn id that the newer bubbles end with. The page shows a kept message as an outlined bubble at its historical position rather
than in the queued list. Send now on any member promotes the whole group, kept row included, for boundary
delivery; a kept row never joins a running turn natively. A refused group retains all original queue
identities through subsequent retries, so its own bubbles are excluded from historical context and its
instructions arrive once; a direct turn's kept message has its turn id as its queue identity.

**Send now** promotes the whole queued operator group for that conversation in arrival order, without
stopping its work. The selected row identifies the group, not a message to move ahead of its neighbours.
Messages arriving after the claim remain queued. The Codex coordinator keeps its isolated
`codex exec --ignore-user-config` invocation: its group runs next after the current turn, with
the waiting reason available on demand. Claude project chat supports native delivery:
`engines.engine_driver` holds stream-json input open and supplies the group as a user line, with
running commands moved to the background. The request writes the group into the turn's `sends` folder; the driver claims
it by renaming and records the outcome as the file's final name. Once the engine confirms it, the reply so
far becomes that turn's answer, each message joins the history under its own turn id, and the rest of the reply
streams beneath them. Each message retains its own visible row and receipt. The claimed rows show
faded without **Remove** until then. Retries reuse the claim or the individual
history receipts, and only one group is sent at a time.

Historical assistant rows marked `interrupted` retain their partial reply, possibly empty.
Fresh-session context and `alt l3 search` results keep that marker; those
incomplete turns are never replayed.

A group the turn can no longer take, because it ended first, returns to the queue front and runs next
in arrival order. A group containing images, one sent during system work (**Runs next after system work**)
or while no turn runs (**Runs next**) also waits for next-turn delivery in arrival order; text never
overtakes an image in that group. System turns keep their existing boundary to
preserve notification, CI and report delivery. System queue rows cannot be promoted or removed. After a
turn, and at the next drain after a restart, each sent row settles from its outcome file: a message the
engine may have read is recorded once in the history and never runs again; one it never read runs next.
Each history row retains its delivery outcome; an uncertain acknowledgement displays an amber **!** and **Unconfirmed**
after settlement and reload.
No available engine, a restart in progress or a launch pause explains why it cannot be sent. There is
no timer or automatic hard interruption; task-chat Stop remains the explicit control that ends work.
The browser requests Send now by message ID.

In the task chat, the same control claims the queued removable operator group under the project lock,
records the claim on the task and writes the group in arrival order to the worker's `sends` folder.
Both task-owner engines use native delivery: stream-json user input with backgrounded commands, or
`codex app-server` with `turn/steer`, behind the engine seam.
The worker keeps running; its inbox hook neither announces the claimed messages nor ends the turn for
them. When the engine confirms the group, the driver records each message's delivery receipt on the
task itself, so a restart of altd loses nothing, and the rows leave the inbox. Each message retains its
own conversation row. A group returned at the turn's end stays queued for the next turn. After
a Stop, a lost job or the end of the task, the claim settles from the outcome file before any later turn
takes the inbox: a message the worker never read is delivered at the next turn, and one it may have read
is recorded as unconfirmed and never sent again. Images anywhere in the queued group keep the whole
group waiting for next-turn delivery in order. A question wait, a fault, a Stop, another owner action,
another group being sent, or a worker launched before this driver also keeps the group for the next
turn instead. Operator grants and merge holds keep their existing rules.
Codex has no hook checkpoint, so a Codex owner's driver makes the same claim by itself, without the
button: while its turn runs, every message waiting for the owner, from the operator, L3 or Altitude, is
written into the turn in arrival order up to the first message with images. After the engine refuses one,
the rest of that turn's messages wait for the next turn.

The project conversation and the task conversation use one
composer component, `web/src/components/Composer.tsx`, with no page-specific props.
The page owns its draft and submit function. A valid turn, terminal turn ID, queue receipt, or
saved task message establishes acceptance; HTTP stream headers alone do not. After acceptance,
a broken response stream resolves to the existing history refresh without restoring the draft or
inventing an assistant failure. Each local stream callback belongs to its own send, so an older
stream cannot alter a later queue request. A canonical assistant or error row for that turn replaces
its local stream even while the response remains open. A queue receipt establishes acceptance;
the source conversation refreshes its canonical snapshot even after navigation, rather than adding
the receipt back to the queue. Local cache edits do not count as reads. Failed reads show the conversation read error, whose
Retry only reads, including after navigation. They do not restore accepted text for sending again.
In both conversations an explicit HTTP refusal (an image converter refusal for image sends) marks the
message Not sent with the server's reason; transport, malformed receipt and server failures without
acceptance evidence mark it Unconfirmed, with Copy to draft and no send Retry. A failure that arrives
after acceptance is ignored. No text matching or automatic resend infers delivery.
Task sends carry a UUID `request_id`, retained as the saved message ID. The conversation renders
the pending message only while that ID is absent from polled messages, so the saved row owns its
display even before the POST completes. A late receipt preserves that row's canonical position and
delivery state, including explicit removal.
The composer keeps only submitted-text recovery in browser-tab `sessionStorage`, keyed by stable
project or project/task identity. Live request callbacks outlast component unmount and restore a
failure only to their original conversation. A receipt removes its request's recovery copy before
the answer finishes; older reads are cancelled before a late receipt updates the query cache.
Each conversation keeps one copy per failed message, marked refused or not, while the message shows it.
A reload or a new pairing without a receipt restores every remaining copy to the field as a draft in send
order ahead of the saved draft, as unconfirmed unless every copy was refused, even if history now
contains a similar message; a recovered draft sends wherever a typed one would. A send that fails while no
message shows it, because pairing again cleared the outbox while its request was out, returns to the field at once. The operator checks
history before choosing to send. Recovered text and later typed or dictated edits remain recoverable
while unconfirmed; editing a refused recovery, or clearing the field, returns it to an ordinary draft. Sending replaces the recovery the field showed with the submitted request; recovered words the field never
showed stay recoverable. Ordinary unsent
project text survives route remounts in client memory; it is not synchronized or stored across reloads.
Recovered text transfers back to recovery storage on unmount so returning restores it only once.
The browser document keeps the latest recovery record it read or wrote for each conversation, matched to the
stored bytes it last saw, so a later failed storage read or write uses that copy, while storage cleared or
replaced outside the document wins again and no field keeps a claim on the replaced words. Recovery is the recovered text's only owner: a task page's
Back/Forward memory keeps none of it, judged by the document's record of the recovered words each field
shows. Where the document has never read a conversation's recovery, words typed are an ordinary draft; the
stored words join it once readable and stay recoverable if it is sent.
Failure to save the initial recovery copy leaves the text
unsent with an inline error. No recovery record initiates delivery or provides a second server queue.
A failed recovery update keeps the latest text in the current browser document and displays a
keep-tab-open warning; it cannot promise that unwritten edits survive reload. Once storage accepts
updates again, returning to the conversation saves that recovery and clears the warning.
The shared textarea is read-only during microphone startup, listening and transcription; text remains
selectable and readable at both viewports. The voice status and indeterminate spinner appear inside
the composer box. Cancel remains available during transcription. Each cancelled microphone acquisition
or transcription keeps its cancellation identity, so late results cannot interfere with a new recording.
Stop transcribes for editing and cancels on leaving; Send retains its operation through navigation,
including leaving before the recorder emits its stop event. Failure returns the preexisting text to
the source conversation. Recovery arriving from an earlier send remains a separate unsent draft;
it is not appended to the captured voice Send. Image Retry retains the captured request context.
This client operation lasts within the current document; it adds no server-side queue of sends or
audio.
The composer reads the installation's voice backend and destination identity from `GET /api/voice` and
shows no microphone until it answers. With `host`, `hostCapture.ts` wraps streaming in the same
recorder shape: a dedicated audio graph, separate from the waveform, runs an audio worklet (loaded
from a blob URL once the graph's context is running) that averages the microphone down to 16 kHz
16-bit samples. The composer shows "Starting voice…" until the first samples arrive, then Listening.
One request is in flight at a time, carrying what gathered since the last answer (normally half a
second, at most ten seconds); a request lost on the network is repeated with the same number up to
three times. Each answer replaces the words after the typed draft. Stop releases the microphone and sends the rest as the final chunk; Transcribing
lasts until the final text lands. No samples within two seconds, an ended track, a worklet that cannot
load, a refusal or a lost connection stops the recording: the words already shown land in the draft
and the hint gives the reason. A 409 reads the backend again. Before setup, the microphone explains
the one-time download with a **Set up voice** link; when host voice cannot run here or its setup
failed, the microphone hides and the hint says why. With `browser`, the default, `recognition.ts` wraps the
browser's own `SpeechRecognition` in the recorder's shape (start, stop, state, one stop event).
After the previous capture's shutdown gate and microphone acquisition, the composer synchronously
creates and connects the waveform graph before starting either recognizer or recorder. React only
draws the prepared analyser. The composer closes each graph once on capture exit, including partial
setup and failed startup, without awaiting graph activation or `AudioContext.resume()`.
For English recognition it punctuates on the device: `web/src/punctuation` loads a bundled
punctuation and true-casing model (1-800-BAD-CODE `punctuation_fullstop_truecase_english`,
Apache-2.0, reduced to a 16k English vocabulary with 4-bit weights) on a minimal single-threaded
onnxruntime-web build, in a Web Worker, on the first capture of the page, never on opening. Vite
emits both under the hashed, immutable `/assets/`, so a device downloads the model once until the
model itself changes; words land as recognized while it downloads, and nothing is fetched in the
background; `scripts/punctuation` records how they are built from pinned upstream
sources.
Each finalized phrase is sent with up to sixteen earlier words as context; the model may revise the
last four earlier words (a phrase end becoming a comma) and earlier punctuation stays fixed. Output
is checked word by word: only letter case and one trailing `.` `,` or `?` may differ from the
recognized word, and words with inner marks (`U.S.`, `google.com`) are context only, so words are
never rewritten. A recognizer revision of a final phrase drops the punctuation from the first changed
word. Interim words show as heard. Stop releases the microphone when the recognizer ends, then waits
for the remaining punctuation up to ten seconds, or three while the model is still loading; words it
has not reached land as recognized and the composer says whether the model was loading or could not
run. A request the worker does not answer in ten seconds discards the worker, and the next capture
loads afresh. No text leaves the device and no
server component is involved.
The composer runs one capture state machine for every backend: recognized words appear after the
typed draft while listening (the field scrolls to keep the latest words in view once they pass its
height), the last phrase may change until final,
and the display alone paces them: `liveReveal.ts` reveals each update's text letter by letter at a
rate that finishes about when the next update is due (the recent update gaps, at least twelve letters
a second), keeps the revealed length through a revision so corrected words change in place, and shows
each update whole under reduced motion. Stop, Send and failures read the capture's own text, never the
revealed prefix, so pacing cannot drop or delay a word; the recognizer restarts when
the browser ends a session on silence (five immediate ends in a row are a failure, not a loop),
and Stop waits at most three seconds for the recognizer's last phrase, then for its punctuation, before
landing the words; no upload follows. A recognizer refusal is the denied state, and because a refusal can be temporary, the
next tap asks again; any other recognizer error is the failed state and keeps the words already shown.
Cancel, including cancelling a voice Send or leaving the composer, discards the words and restores
editing at once while it aborts the recognizer. That capture's microphone stream is released when
the recognizer reports its end, or after at most three seconds. Speech recognition is
one per page. The waveform stops drawing immediately, but closes its audio context only after
recognizer shutdown; the next capture in any composer shows "Opening microphone…" while both
recognizer shutdown and that asynchronous context close settle. Each wait is bounded at three
seconds: an unanswered browser close cannot disable all later microphone attempts until reload.
Cancel during either wait restores editing without opening another microphone. The browser manages the device audio
session for capture; Altitude does not override it. Cancel with the X returns focus to the microphone
button, so a phone keyboard does not open; Escape returns focus to the field. The recognizer
ignores callbacks after cancellation or its end, including while punctuation is pending.
Abandoned captures do not queue inference when a pending model load completes or apply late
punctuation output. Native-device capture and audio-session behavior require native evidence;
scripted recognizer tests establish ordering and text isolation only. An iOS 27 Safari report describes a recognizer that
hears only a tab's first session; later sessions get a live, unmuted microphone that delivers silence
until the tab is reopened ([WebKit bug 326069](https://bugs.webkit.org/show_bug.cgi?id=326069)); matching symptoms alone do not confirm a device's cause. A later recognition capture in the page
whose live, enabled, unmuted microphone gives exact digital silence to a running waveform graph for
three continuously observed seconds, before any words, therefore ends as a cancel with "The microphone went silent. Close and reopen Altitude to dictate again. Typing works."
A working microphone's noise floor is never exactly zero; any sound disarms the check. The microphone
diagnostic in **Settings → Voice input → Voice troubleshooting** is opt-in and page-local:
`voiceTrace.ts` retains up to 256 metadata-only events for ten minutes, including track
state, recognizer callbacks and one waveform state/signal-presence sample per second. It never
records speech, drafts, raw samples or device identifiers, uploads data, or changes capture behavior.
The operator explicitly views/copies the report; viewing stops collection and reload/clear deletes it.
The report identifies the loaded script basename and browser/Home Screen mode, without the origin
or conversation URL. The microphone
stream feeds the waveform and carries the same permission the recognizer needs. With `host`, the
composer streams samples as described under [host voice](#host-voice); a 409 from a server whose
backend or runtime changed shows the server's words and reads the backend again.
A successful Settings read or save updates the document's cached selection for the next capture;
saving cancels an older Settings query, and an earlier composer read cannot overwrite the saved
selection. Each recording holds its initial identity.
Words are never
simulated. The composer owns microphone
permission, capture state, a 595-second stop just under the ten-minute recording limit,
transcription, cancellation, and focus. A landed transcript is appended to the
draft with the cursor at the end and nothing else appears (issue #195): existing draft text is the
prefix, separated from dictated text by one space when it does not already end in whitespace. Its
send control is an arrow in an accent circle in every state, with no visible text and an accessible
name of "Send" ("Queue" while busy). Its states are the design spec's §3.6 table (idle, typing, sending at 60% that settles in place on acknowledgement, busy queueing, listening
with a live waveform and timer, transcribing, landed, denied, unavailable, refused), each walked at
phone and desktop widths in `web/e2e/conversation.pw.ts` (upload backends) and
`web/e2e/voice-recognition.pw.ts` (browser recognition). While listening, desktop keeps Cancel, a
168px waveform drawn at device pixel ratio, the timer, Stop and the arrow together at the right of
the control row; the phone waveform fills its row. Both widths stack the field over one control
row; phone controls are 44px and the row keeps its place in every state. Drafts grow from 44px to the lesser of 120px and
25% of the usable visual viewport (at least 44px), then scroll internally. Routine phone hints
consume no row; relevant voice, permission and send errors remain visible. Desktop retains its
shortcut and delivery hints. Keyboard, draft and streaming changes keep bottom-follow when already
following and preserve the visible message and offset while reading older history. Sending resumes
following. Pressing Send leaves focus in the field, so an open phone keyboard and hidden navigation stay
through each message. Browser emulation verifies layout and application transitions; native mobile keyboard
behavior requires real phone acceptance. Decision and reason fields remain
ordinary form fields.
The same composer adds a single image control and a conditional preview strip.
Each image is checked and read in page memory when chosen, so Send takes the selection at once. The
message carries its images from Send (its outbox entry keeps them for Retry and Edit until acceptance) and
the composer stays free. Submitted captions use the existing text recovery; image
bytes are not persisted in browser storage. Private thumbnails belong to the original saved
message. A saved message the page itself sent starts each thumbnail from the picture its outbox
entry holds, and keeps that picture only until the thumbnail's own read, which starts at once on screen or off, replaces it or
shows its error; a queued project message's turn row, which replaces its queued row, reads again. The shell owns one modal viewer per page (`ImageViewerHost`): it reads the opened picture's bytes
itself, so it outlives the queued row that opened it when history admission replaces that row, then
reads the previous and next pictures and holds at most those three in memory. Moving on releases a
picture more than one step away, a hidden page releases the neighbours, and closing or a route change
releases everything; nothing is written to browser storage. Image interaction states and boundaries are specified in
`design/wireframes/IMAGE_INPUT.md` and walked at both viewports by `web/e2e/image-input.pw.ts`.

The L2 task's phone tabs replace the current router history entry and retain its location state;
the desktop live panel toggle stays local. `/live` remains addressable and selects the live view
on reload. Both task Back controls traverse the existing browser history when React Router's
entry index indicates an in-app predecessor. With no such predecessor, app Back replaces the
task entry with the owning project's L3 conversation. Browser Back remains native, and links to
other pages or tasks still push entries. `web/e2e/task-navigation.pw.ts` exercises Back and Forward
with real browser history at both viewports.

### Work and attention

The task card (`web/src/components/TaskCard.tsx`, spec §3.5) is one component in two sizes: the
bordered card under an L3 reply that created the task and the row in the work panel. Its meta line
comes from the task's state and, for a queued task, from `GET /api/overview` `wip.waiting[].hold`,
the queue's own reason (a planned wait, the WIP limit, an engine hold, a restart in progress,
a resume checkpoint or plain dispatch), so the card never names a file list. A queued task with `planned_wait` reads
**Planned · Waiting for <prerequisite>.** with the muted queue dot; the explanation wraps on phone and desktop.
Releasing it changes the same row to **Queued** with its ordinary dispatch hold, then **Running**
only when launched. Planned tasks stay in Current without adding attention or a separate panel.
A task blocked waiting on L3 names the coordinator and the recorded prerequisite with the running dot, and the rail's project dot
counts it as running (`counts.waits_l3`);
only a decision in the queue turns either dot amber. An owner/daemon park without a question, fault
or operator stop explains the recorded reason, or its absence, with the idle dot. Stop evidence, not the block recorder, identifies
"Stopped". Queue and restart inventory labels share `tasks.block_status`; an unset wait owner is
a pause, never an inferred operator wait or attention item. The work panel (spec §3.7) reads the project's
tasks and the overview queue filtered to the project. **Current** contains every unfinished task
once as a compact status row; tasks done or rejected in the last seven days fold under **Done this
week**. A row with an open operator question shows **Needs you** and its question count alongside
independent execution or fault status, and opens the owning question's conversation anchor. Other
rows open the ordinary task conversation. Questions and answer controls live in global Needs you
and the owning chat. Partial or final answers update the same row; only completion or rejection
moves it out of Current. Saving an answer does not assert that the worker resumed.

On desktop, Work's List | Graph switch shows Current either as rows or as the graph
(`web/src/components/AgentGraph.tsx`, spec §3.7); the choice is a per-browser display preference
(`web/src/shell/workView.ts`, `altitude.workView`) and the phone always shows rows. The graph places
every current task and routine run on a ring around L3, the operator's turns first, and widens the panel
to the live session panel's 480px. Each node reads `cardView`, the task card's own facts, link and
accessible name, so a node and its row never disagree. Live cues need no read of their own: a running
task's halo and helper pips come from the row's `token_usage` (see [task usage](#task-usage)), counting
the owner session's and the current worker's helper sessions' newest requests within a minute, and only
while the reading is under 30 seconds old and the project read is fresh. Missing helper evidence shows
no pips and is named as unknown, never as zero.

Only global Needs you carries a numeric attention badge. Project rail and switcher rows retain
their state dots. The badge counts operator questions awaiting a response plus existing operational
attention items; Needs you and project summaries label questions and operational items separately.
Needs you groups items by their `project` into contiguous sections with fully wrapping project
headings on phone and desktop, including question groups, reviews, stops and faults. The selected
project's section leads when it has items; the other projects follow first appearance in the queue,
and items retain their order within each project. Each heading is a disclosure that collapses its
section to the heading and an item count and reopens it, by pointer or keyboard; cards stay mounted
while collapsed, so staged answers survive, and the state is page state rather than a setting. The
selected project never filters the inbox, and sections remain visible on saved reads after a failed
refresh.
Unknown overview reads never imply zero attention. Failed refreshes identify saved counts and
status as stale; Work links stay available, while Needs you disables answers until a fresh read.
App Back and browser history preserve the originating Work or Needs you view.

### Operator steps

A command only the operator can run for a project's own recovery, with no owning task to ask it, is an
operator step: the project's `steps.json` under `ALTITUDE_HOME`, written by `tasks.publish_step`. The
coordinator publishes one with `alt project step -`, which altd answers only on that project's coordinator
socket, as it does `alt project message`. Stdin is the plain question: its first line names the step and it
holds exactly one `run` block of one command line, split at fences as the page splits them
(`tasks.run_commands`); `--task <slug>` names each unfinished task waiting on it. Publishing the same command
again revises that step in place and adds the tasks it names. The step is a row in the overview queue and the project's decisions with no
`slug`, `kind` `step`, its `id` and the tasks it names, so Needs you shows it as a card in the project's section
with **Open in terminal** opening the project terminal (spec §3.8.4), and the badge, alerts and digest count
it. A task it names that is blocked or reported, without a fault, Stop or pending resume, reads the operator's
turn with the step's first line
(`tasks.block_status`), on its Work row and task page, instead of waiting on the coordinator; the task page
keeps the owner's open note to L3 out of sight meanwhile. The project terminal's notice that exactly that command
looks finished clears the step (`tasks.step_ran`) and asks the coordinator to verify the output and tell the
tasks it named; a terminal that ended first leaves the step open; `alt project step --withdraw <id> --reason '…'` clears one settled
another way, and removing the project withdraws its steps. The notice is queued before the step clears, so a
notice that fails leaves the step in Needs you. A stale Needs you read offers the command with Copy only. The
project's `events.log` records `step` rows for `asked`, `revised`, `ran` and `withdrawn`.
A step for an owner's own work stays on that task as its question, with its task terminal.

### From model judgment to a task question

The L2 persona and repository instructions teach the owner when to ask a question, how to publish
files, and which commands are available. The model judges whether a decision is needed,
chooses the question/options and names any files to show. It invokes `alt task block` through its
engine's execution tool, with ordinary command arguments and, for files, repeated `--show` paths. There is no automatic interpretation of arbitrary model prose as
a question or an approval. The engine's own tool-call display is separate from the task dilemma
that Altitude renders in its web conversation.

`bin/alt` checks the role and owner environment; `tasks.block` verifies the current attempt under
the project lock. It copies any shown files, publishes the question/revision in
`status.json`, and blocks the task through the existing transition. Question messages are projected
from those durable records into the same human conversation as `conversation.jsonl` replies.
The app reads `/api/task` for that conversation, `/api/overview` for unresolved operator questions
in Needs you, and the shown-files API for the files a question shows. The question's `shown` view
carries the reader link. The capture and serving contract is in [shown files](#shown-files), and
[CLI examples](CLI.md#showing-files) show actual inputs.

```mermaid
sequenceDiagram
    participant Owner as L2 model
    participant CLI as Engine execution tool / alt CLI
    participant State as Task records and captured files
    participant App as Altitude API and browser
    participant Daemon as altd
    Owner->>CLI: block with question and optional shown files
    CLI->>State: Validate owner/attempt; save capture and question revision
    App->>State: Read conversation, question and shown copies
    App->>State: Selected/custom responses or chat message with viewed revision
    State->>Daemon: Durable inbox / resume request
    Daemon->>Owner: Deliver at checkpoint or resume the same session
    Owner->>CLI: Discuss, or resolve a clear answer citing its source message
```

Transport depends on the role. An L2 uses the `alt` CLI in its task environment through its engine's
execution tool. A Codex L3 uses the `altitude` coordinator stdio MCP tool: structured requests carry
`kind`, argument arrays and stdin, and its adapter forwards them to the project-bound altd socket.
A Claude L3's runtime command shims use that same broker. The broker fixes project authority and
checks allowed coordinator verbs. MCP transports coordinator requests; it does not own dilemma,
shown-file or decision state, and that L3 transport restriction does not apply to the L2 CLI path.

Selected choices and custom responses send the question/revision (and group revision when applicable)
to `POST /api/decide`, which validates and saves one ordinary operator message. Ordinary chat uses
`POST /api/l2/message`. Both remain messages until the owner interprets a clear answer and records
`alt task resolve` against the original source message. Both use the durable inbox and
daemon wake mechanism described in [session lifecycle](SESSION_LIFECYCLE.md#messages-resume-and-stop).
Opening shown files performs reads only; neither viewing, a follow-up nor a resume accepts a proposal.

A dilemma (spec §3.8–3.10) lives in the owning task conversation. The task record's `questions`
contains versioned question text, zero to three explicit options and a recommendation key,
source/audience, stable ID and message anchor, and an open or resolved status. A question group
contains up to three open members plus closed history, a group revision, and one stable discussion
anchor. `task_view` projects `question_group` with member records, `question` as the first
open member (or latest receipt), and individual revision history;
`GET /api/overview` and the project view project the operator's turn: unresolved operator questions
asked since the operator last wrote to the task (`handed_back`), plus one `review` row for a held
delivery whose owner stopped in `blocked` or `reported` (#419). An open proposal question (marked
`"review": "proposal"`, or unmarked and showing files), the `review` row and an open operator question about
the held PR (marked `"review": "changes"`, or unmarked and naming it), which also carries the PR's `pr`, carry `review` from `reviews.status`: the latest proposal or implementation review's id,
state, who skipped it and why, finding and open counts, assessment and earlier-version coverage, read
from the saved reviews with the review card's own comparison; `state: none` when there is none (spec
§3.8.3). `alt task review skip` records the owner's reason as a withdrawn review that never ran. A held PR that Altitude has observed
closed without merging asks for no review (#575): an L2 `block` reads the held PR's state from the checkout origin's repository, and a
`CLOSED` result, like a verified `alt pr close`, appends one `pr-closed` task event. The delivery,
`prs` history and merge hold stay recorded. A later `delivery` or `pr-adopted` event for that PR, a
`pr-reopened` event from an owner block that reads it open again, or a new PR asks again. An
unreadable state leaves the recorded review shown and says so on stderr. Any operator message or
quick answer hands the task back; the next park by the L2 or L3 without a queued message returns the turn and
marks still-open questions `asked_again`. `tasks.block_status` gives the CLI list/status, queue and
restart notice one wait label (`<operator>'s turn · …`, naming a waiting task's lone freeform question by its
first line and counting others, `L2 replying to <operator>`, `paused · fault …`, `stopped by <operator>` or `stopped by L3`, `waiting on L3`, `paused`). Question
state is independent of worker state: a discussion wake, capacity wait or ordinary resume never
records a decision. On receiving guidance, the owner assesses each question before lengthy work.
Unaffected choices remain answerable; doubtful ones are withdrawn with a reason in chat and re-asked
when ready, even unchanged. Relevant revisions and checks precede a completed-work review;
independent work need not finish. Answers settle only their stated scope and preserve required work.
Guidance waits for the owner's checkpoint; it considers an earlier answer with later guidance before acting.
No message classifier or automatic invalidation supplies that judgment.
An open operator question naming or linking the held PR supplies its single response surface,
including a freeform question. The generated review card stays hidden until the owner resolves
that question, including while its submitted response waits for interpretation. This presentation
rule supplies no merge authority: an unrelated answer mentioning the PR never counts as approval,
and the held-review fallback returns after resolution when still needed. Independent questions and
ordinary chat remain available.
Existing stopped/fault cards link to their ordinary task controls; an operational
pause with no open question offers Resume through the existing daemon operation.
If a provider limit queues a fresh attempt, the existing dilemma remains answerable. Replies and
acceptance wait in the same inbox for normal dispatch; the fresh brief includes the current question
or its recorded resolution. A queued task without a question retains its ordinary initial state.

A direct L2 block publishes its question into that human thread. A published or reworded member takes
the block's audience; an unchanged operator member keeps the operator's, so re-parking never moves an
escalation away. Each block, escalation or resolution on a blocked task recomputes `waiting_on` from
the open members: the operator while one is theirs, L3 while only L3 members are open, and nobody once
none is. A block that publishes or revises questions, or a resolution that publishes a remaining part,
queues one L3 notification, including operator-directed ones. The message names
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

`POST /api/decide` takes `{project, slug, question_id, revision, option_key}` or an exclusive `text`
response. Omitting the key explicitly selects the recorded recommendation. A group sends
`{project, slug, group_id, group_revision, answers: [{question_id, revision, option_key|text}]}`;
each member supplies exactly one response kind. An optional `request_id`, a UUID the page makes per
send, becomes the saved message's id, so the page shows the answer under its final id from the press.
The existing project lock validates the entire batch before saving one attributed operator message and
durable delivery receipt. It records no decision.
Each submitted question projects `response: {text, at, message_id}` and stays semantically open until
the owner resolves it. Sent members leave Needs you; the owning chat shows a picked option as that
choice and typed words as the operator's bubble, each with its delivery state, alongside members still
awaiting input. An identical retry, with or without the same id, reuses its receipt and repairs
interrupted delivery. Stale or conflicting submissions fail together. The response names the original
question revisions, so the owner cannot cite it to approve replacement wording. A saved answer means
saved for delivery; **Work resumed** requires observed running state. A queued owner stays explicit.

Typed replies use `POST /api/l2/message`, optionally naming the viewed question/revision or
`group_id`/`group_revision` as context. A `request_id` names one message across both endpoints: a
repeated id returns the saved message, and the same id with different words is refused. The saved
message retains the viewed member references so
one conversational answer can settle several questions independently.
The message endpoint returns the stored row even when its immediate resume wake fails; the saved
resume request remains due for the existing timer. L3 queue wake failures likewise retain the queue
receipt and defer to the timer. A restart race after stream headers returns the saved queued row.
The same L2 answers follow-ups, clarifies uncertainty, or uses [`alt task resolve`](CLI.md#conversational-decisions)
to record an actual decision against its original message. `--disposition withdrawn` instead records
the owning L2's reason, without a source message, decision authority or remainder. Withdrawal closes
only the selected member, retains history and refuses stale acceptance. Re-asking uses a new member
without an ID in `block --questions-file` when independent questions remain; closed members do not
consume the three-open-question limit. Task/attempt ownership and source-message
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

The shared question component appears on Needs you and at its conversation anchor. While a group has
an open member, its closed members fold into one collapsed **N earlier questions** row, open when a
link targets one of them; an open member addressed to L3 reads **L3 is handling this**. Choices and custom
text remain staged until **Send N answers**, including a single member. The send row follows the
questions in normal flow and scrolls with them on phone and desktop. **Other…** opens that member's
field; a plain question shows the field directly. Question fields use text; ordinary chat retains voice.
An explicitly recommended choice has an accent border and a corner star (announced as "Recommended"), distinct from the pressed selection, and is never preselected.
Edits to independent members survive another member's response; changed revisions discard their own
stale choices without retargeting them. Explicitly republishing a responded member gives it a new revision
and fresh input. An unchanged ordinary re-park keeps its saved response and adds no new attention.
Needs you keeps the task's purpose, complete question, concise recommendation and material
consequences visible before an answer. Owners write these for an operator deciding at a glance;
clipping long technical prose is not a substitute. Question and review-card text with line breaks
renders through the reply prose renderer (`QuestionProse` in `web/src/components/Prose.tsx`):
paragraphs, lists, inline code and fenced code blocks, in Needs you and the owning chat; a one-line
question stays one compact line, as do recommendations, receipts, folded summaries and labels.
Additional saved question detail opens in a
**More context** disclosure in the owning chat when it differs from the question, alongside its
surrounding reasoning, evidence and history. This
uses the existing question and conversation records without a second summary or decision store.
`/projects/<name>/tasks/<slug>?question=<id>&revision=<n>` focuses that question's group
and surrounding prose, suppressing the initial scroll to latest. Historical revisions remain
readable under **Earlier question**, opened automatically by an old-version link; stale controls
cannot act on a replacement. A compact, muted **Question withdrawn** row expands to the original
question, owner's reason and former recommendation without answer controls. Following the bottom resumes ordinary chat
scrolling. Pending questions poll every two seconds, and new replies offer **Latest messages**
without moving a reader away from the question. Technical activity stays in the live session view.
A saved decision URL redirects into this
conversation; no separate form, recipient selector or mirrored follow-up thread exists.

An FYI (`tasks.fyi`, exposed by `alt fyi [slug] "text"`) is a chat row
`{role: "system", trigger: "fyi", slug, text, by, heads_up}` in the project's conversation.
Internal calls default to `by: "altd"`; the CLI supplies its actual actor and `tasks.fyi` records
`heads_up: true` only for an explicit L3 actor. This selection field distinguishes deliberate
heads-ups from historical automatic calls that inherited `by: "l3"`; history is neither migrated
nor classified by text. The [L3 persona](../personas/l3.md) owns selection guidance.
There is no project inbox file and no `fyis` in the digest or overview.

### Voice backend

Voice transcription sits behind the capability seam as one machine setting, `voice`, in the private
settings file: `host`, `browser`, or a tagged service object containing its URL, optional key/model
and an opaque random selection identity. Without a saved choice the backend is `host` when this release's
speech manifest names a runtime this computer can run, and `browser` elsewhere.
`GET /api/voice` reports the backend, an opaque selection identity over the backend and, for `host`,
the installation identity, and `host`, where host voice stands on this computer. Supported runtime
replacement preserves that identity; removal invalidates it. `alt machine set --voice`
requests a change through the same durable request that carries `wip`; `--unset-voice` returns to the
default.

`/settings` shows a compact Voice input summary under This machine and read-only connection details
(the browser's current origin/HTTPS state and configured operator). `/settings/voice` holds three
choices, This computer, Browser recognition and Your speech service, as cards whose Use buttons save at once; speech-service fields save explicitly. Project three-dot menus and the
desktop operator row open Settings. `POST /api/voice` saves `{backend, selection}` and, for service,
`url`, optional `key`/`model` and `remove_key`, from a paired same-origin page through the same
durable request/apply path as the CLI, with no restart or provider probe; a selection that no longer
matches the saved one is refused (409) and asks the person to reload settings. Failed saves leave the
persisted choice unchanged. Fresh settings reads update the form as well as the composer's selection.
First run links to optional **Settings → Voice input**; voice setup never blocks onboarding.

On start, `dispatch.forget_speech_service()` deletes only untagged legacy service settings and
receipts, including their keys. New deliberate service configurations carry `backend: service`
and survive startup. No legacy credentials are restored. Pending settings requests retain the key
only until apply completes; terminal receipts replace secret-bearing fields with a backend-only
projection. Events are projected before writing. A setting change compares the expected selection
under the settings lock, and replacing a service with host/browser deletes its details.
Phone access uses an explicitly configured private HTTPS address whose certificate
covers that address. Safari can use the microphone after the CA is trusted on the phone; typing
remains available without voice.

#### Operator-supplied service

Paired Settings reads expose URL/model/key-present, never the key. Machine output exposes the backend
only. A capture carries the selection its composer read; the server checks it against the saved
choice when the upload starts and again before forwarding `POST /api/voice/service`, and a transcript
the service has returned reaches the capture. A Settings read showing another selection invalidates pending captures.
Saving a changed URL/model/key changes the service selection identity independently of the built-in
installation identity; saving unchanged settings keeps it.

The browser retains raw mono 16 kHz signed 16-bit PCM in page memory until Stop/Send, at most
19,200,000 bytes per capture and two captures including a pending Send. The authenticated service
route creates WAV/multipart framing and sends one request to the exact saved endpoint, without
redirects, environment proxies, probes or retries. It accepts JSON text, limits the response to
1 MiB, limits concurrent requests to two, and bounds upload and upstream response separately to
120 seconds each; the outbound request runs in the daemon and is cut off 120 seconds after it starts,
apart from the system resolver's own name lookup limit. The key and audio never touch disk. HTTPS uses normal certificate verification.

Stop appends text to its draft; Send carries text/images and completion to its original conversation.
Cancellation and late results never alter another draft. Errors are sanitized and preserve typed
text/images; failed audio is discarded rather than replayed after an uncertain submission. Browser
pending work ends within 240 seconds; cancellation cannot recall audio already forwarded or ensure
immediate upstream cancellation. Service retention and charges belong to the chosen service.
The existing document settings cache remains advisory, and no persistent browser audio cache exists.

#### Host voice

With `host`, this computer transcribes: `altitude/speech.py` owns a pinned speech runtime and one
speech worker, and the page streams samples to it. The runtime lives in `~/.altitude-speech`, beside
Altitude's home rather than in it (`config.beside_root`, which validation's storage and the scratch folders also
use): the speech worker imports the runtime's code outside any sandbox, so no worker's writable roots contain it. At daemon start, `speech.remove_home_runtime()` deletes what
earlier releases kept in `~/.altitude/speech` without reusing or following any of it, leaving a
registered project's state that shares the name in place, and nothing it fails to delete stops the start;
when that folder held a finished runtime and host voice is selected, voice sets up again beside the home
as a startup automatic attempt. The model is NVIDIA Parakeet TDT 0.6B v2 (English,
CC-BY-4.0) as the int8 ONNX export `istupakov/parakeet-tdt-0.6b-v2-onnx` at a pinned revision, run on
the CPU by the MIT `onnx-asr` decoder with onnxruntime and numpy. It always punctuates and capitalizes.

`platform.speech_runtime()` names the runtime this host and interpreter can run: Linux x86_64 with
glibc 2.28 or newer, or macOS on Apple silicon, with CPython 3.12 or 3.13. Both use the same model,
decoder and library versions; only the onnxruntime and numpy wheels differ by host. onnxruntime stays at
1.28, the newest release whose library has no usage-reporting client. Anything else, such
as an Intel Mac, reads as `unavailable` with its reason. `speech.status()` is `unavailable`, `absent`, `setting-up`
(with bytes done), `ready`, `failed` (with the reason) or `outdated`, with the download size.

Setup (`alt voice setup`, or Settings through `POST /api/voice/host` with `setup`) downloads the
manifest in `speech.py`: five model files from Hugging Face and three wheels from PyPI, about 700 MB.
Each file streams into `~/.altitude-speech/.staging` with a size cap and must match its pinned
SHA-256. The wheels are unpacked with the release installer's path rules (no absolute, parent,
backslash or link entries, a total size cap) into `site/`, not installed with pip. A completion record
naming the manifest digest is written last and the folder is moved to `runtime-<digest>` in one
rename. Before promotion, the actual isolated worker loads the model and performs its warmup within
the load deadline. Completed runtimes explicitly supported by this release keep serving during an
update; unknown, incompatible or revoked runtimes read as `outdated` and cannot serve recordings.
Support is an explicit per-platform/interpreter digest list, never inferred from age or a completion
marker alone. Retained model files are hard-linked into the replacement and kept only after matching
the target size and SHA-256; wheel archives are downloaded and deleted after unpacking. There is no
download cache. Setup needs free disk space for the files it downloads plus the unpacked-library cap.
Superseded runtimes are removed after promotion; one that cannot be removed then is removed by the
next setup and never fails the finished update.

The support set retains the preceding Linux onnxruntime 1.22 manifests for CPython 3.12 and 3.13;
both load and warm up with the current worker and the exact pinned model. The supported-table test
derives their identities from those prior wheel checksums and the unchanged model/decoder/numpy.
There is no preceding macOS manifest for this transition, so no prior macOS runtime is admitted.
Future pin changes review and test their preceding platform manifests explicitly. The
[voice runtime replacement check](DEVELOPMENT.md#voice-runtime-replacement-check) runs a replacement
from an earlier runtime with the real worker on either host; on a Mac only its own processes admit that
runtime. This is fixed-model compatibility, not general support for arbitrary
ONNX graphs: clients provide bounded PCM, never graph attributes or tensors. A release removes a
digest if a relevant unsafe runtime is identified; an offline release cannot learn future revocations.

At daemon startup, selected, already-installed host voice gets one automatic attempt per target
manifest. First installation stays manual. The installation record persists the attempt, cancellation,
failure and stable selection identity. An interrupted attempt also requires explicit Retry; switching
to browser recognition cancels an automatic attempt. Availability (`ready` or `outdated`) is independent
of `host.update`: `preparing`, `downloading` with bytes, `waiting`, `checking`, `failed` or `cancelled`.
Settings shows phase/progress with Cancel or Retry; ready voice remains usable. Unsupported voice links
to the running update instead of offering a duplicate setup.

One file lock admits setup across CLI and daemon. Status reads probe it, and setup takes it, only under
the voice control lock, so a read never makes setup look already running. Workers inherit a shared use lock; verification,
promotion and cleanup acquire it exclusively. Active recordings finish before the idle worker exits
for the update. Finished response replay remains available. Staging temporarily uses another runtime's
disk space; preflight budgets downloads and bounded wheel expansion. Cancel or failure deletes staging
and leaves supported voice available. `alt voice cancel` and Settings cancel the shared attempt.
Remove records an invalidating tombstone first, cancels setup, drains worker use and deletes runtimes;
the daemon observes removal within a second. Successful promotion cleans superseded runtimes only
after their readers exit.
Removal invalidates completion before deleting a runtime's files, so interruption cannot leave a
partially deleted runtime usable. A later explicit setup can recover an interrupted removal. A lock
timeout restores availability without restoring old recording identity, and reports removal unfinished.

The worker (`altitude/speech_worker.py`) runs as `python -I` with the daemon's interpreter and a
minimal environment, imports only the runtime's unpacked wheels, runs the model on onnxruntime's CPU
provider on every host (onnxruntime would pick CoreML first on macOS), and reads one JSON request per line:
`open`, `audio` (base64 samples), `finish` and `close`. It answers `ready` once the model is loaded,
then each changed recording's text so far, finishing recordings first. It commits text at pauses once
eight seconds are pending: the audio up to the middle of the last quiet run (quiet is relative to the
recording's own level) is transcribed once and kept; without a pause by twelve seconds, the words that
started at least a second before the end are kept, except the last. No read covers much more than
twelve seconds of audio. It exits on stdin EOF, so it ends with the daemon.

`speech.Host` supervises it. The first recording starts the worker, after checking that about 2.5 GB
of memory is available (`platform.available_memory()`: MemAvailable on Linux, the kernel's available
share of physical memory on macOS); every recording shares it, and it exits after fifteen minutes unused. Requests
reach it through a writer thread, so a worker that is loading or hung never holds up a request. A
watchdog kills it when it takes more than 30 seconds to load, then 10 seconds to answer new audio or
15 seconds to finish; every unfinished recording then fails with a plain reason, and the next recording
starts a fresh worker. Its stderr goes to `~/.altitude-speech/worker.log`; altd's log records starts,
stops and reasons, never text.

The page's routes carry raw samples: `POST /api/voice/live` starts a recording for the current
`X-Voice-Selection` (refused unless the backend is `host` and the runtime ready) and answers its id;
`POST /api/voice/live/<id>/audio?seq=N&final=0|1` adds chunk `N`, at most ten seconds of 16 kHz 16-bit
mono samples, and answers the text so far at once, never waiting for inference; `final=1` waits for
the final text, and cancelling the recording ends that wait at once. Chunks are numbered from 0: a repeated number gets its first answer again without
adding audio, and a skipped one fails the recording. `POST /api/voice/live/<id>/cancel` discards it. A
recording belongs to the paired device that opened it; at most two are active (a third is busy, 429),
each up to ten minutes, and one without a request for 30 seconds is dropped. Audio stays in memory for
its recording only. The live and setup routes refuse other sites and Altitude's own agents, as the
terminal does; the daemon stops the worker on shutdown.

Every live and audio request carries the page's `X-Voice-Selection`; the server checks it against the
current setting, including the runtime digest, before reading audio (409 otherwise). An unknown
recording answers 410 and a recording opened by another paired device answers 403. Opening a recording
also answers `owner`, an opaque name for the paired device (an HMAC of its id under the machine key); a
replay presents it as `X-Voice-Owner`, so a browser paired again as another device during an outage is
refused (403) rather than continuing the recording.
`web/src/components/hostCapture.ts` keeps recording through a lost connection. Each recording's
samples stay in page memory only, up to its ten-minute limit, and at most two recordings are retained
at once; a third microphone tap asks the person to wait. Requests are sequential: a lost request is
sent again with the same number, bytes and `final` flag after a backoff of half a second doubling to
three seconds, so the server's repeat rule makes retries harmless. A 410 (the server restarted or
dropped the idle recording) opens a new recording and replays the retained samples from the start
while the selection is unchanged; a busy answer while reopening is retried. During replay the shown
words hold ("Catching up…") until the replay passes the point already shown and its transcript is as long,
because the host acknowledges audio before transcribing it; text never shrinks.
401, 403 and 409 end the recording and keep the words shown. After Stop or Send the page waits at most
two minutes for the connection ("Waiting for connection…", with Cancel); then it discards the audio,
keeps the words already shown and says the last words were not added, and a voice Send returns its
text unsent to the conversation it was sent from. A microphone that ends stops the recording and keeps
its words. Nothing is written to storage.

### Design evidence

The same server serves each project's wireframe boards. `GET /design/<project>` redirects a paired
browser to `/design/<project>/<pass>/design/wireframes/index.html`, read from that project's own deployment checkout on
every request and sent uncached, so a merged board change needs no build step and no restart to be
visible. Only the `design/wireframes/` and `web/design/` subtrees are readable and only the
extensions a board needs; the resolved path must stay inside those subtrees, and a project without
`design/wireframes/index.html`, a directory, and anything outside the rule are one plain 404. The
tree is mirrored under the prefix because a board's stylesheet imports the build's design tokens two
levels up. `/api/project` reports `/design/<project>` only when the boards exist, and the project header's
overflow menu turns it into Design boards, opening in a new tab. Any project with boards gets one; the
route knows nothing about this repository's own. Every file is served with
`Content-Security-Policy: sandbox allow-scripts`: a board's scripts run in an opaque origin, so
project content cannot act with Altitude's authority. From that origin the viewer cannot probe for a
listed board that is missing, so the board's frame shows the server's plain 404. A browser sends no
SameSite cookie with an opaque origin's requests, so the path carries `<pass>` instead: the paired
browser's device id and an HMAC of the project, that id and the day under the machine key. It opens
that project's boards alone, on the day it is made and the next, and only while the device stays
paired, so removing a device ends its links too. Only the redirect for a paired browser mints one,
and the boards are sent with `Referrer-Policy: no-referrer` so it stays off other sites.

### Shown files

An owner shows the operator worktree files with `--show` on `alt task reply`, `alt task block` or a
proposal review's `run` ([CLI](CLI.md#showing-files)). `tasks.capture_shown` reads only the named paths
inside the task's registered worktree, walking directory descriptors without following symlinks, and
the document's own local image references, which `tasks.shown_reference` resolves from the document's
folder or the worktree root as GitHub does. One `.md`/`.txt` document up to 1 MiB and up to 24 PNG,
JPEG, GIF or WebP images (8 MiB each, 32 MiB together) are accepted; raster signatures must match the
type, and the record keeps each image's width and height. Traversal, special files, symlinks and other
types refuse the whole set, so nothing is published. References that cannot be copied stay listed as
`not_shown`; remote images are never fetched.

The copies are content-named files in the task folder's `shown/` directory and follow task archival.
A save makes only that directory, never the task folder: one that crosses an archive writes into the moved
folder or, finding no live folder, saves nothing, so the merge card's unlocked first view cannot recreate an
archived task.
The record on the reply row or question revision holds paths, content names, sizes and dimensions, the
document's first heading and opening paragraph, and a hash of itself as its identity; it never holds
the document text. A question showing identical files keeps its revision, changed files advance it,
and the answer fence binds the files the operator saw. An unmarked question is a proposal approval when it
shows files, so its card carries the proposal review; in a group, `tasks.shown_question` gives the
files to the one member marked as the proposal, or with none to the one marked as the held PR's merge
question, and the other listed members publish without them. A review
whose captured `--show` set differs from the latest showing question's counts as covering an earlier
version. `require_shown` refuses both the
operator's answer and the owner's resolution while any copy is missing or altered, or for a question
saved with a design preview before shown files existed; the card disables that question's answers.

`message_views` and `question_view` project a `shown` view: the reader URL, whether every copy is
still present at its recorded size, the document's name, path, heading and excerpt, each image's URL,
name, dimensions and whether the document embeds it, and `newer_url`, the latest publication of the same
document path in the task (reply or question revision) when it is not this one. A question saved with
a design preview shows a closed card named by its title with no URL. The conversation renders a
document as a page card (cover picture, name, heading and excerpt, an **Earlier** chip when
`newer_url` is set) and images alone as up to four thumbnails, the fourth counting the rest; a set
whose copies are gone or resized is a card reading **Preview unavailable** that does not open (the card
checks presence and size; reads verify each hash, so changed content of the same size shows the
reader's unavailable state).

A held PR's merge card carries the pictures the PR changes. `tasks.pr_images` reads the current PR's
recorded head and base commit from its delivery and, once per head, lists the diff from their
merge base with Git's raw output: blob ids, modes and paths, with renames paired and no diff drivers or
textconv. It reads the blobs as Git objects from the project's repository, never the worktree, so later
edits, a moved branch or a link change nothing shown. Each picture passes the shown-image checks and
limits; another type, a link, a missing object or one past the limits is listed as `not_shown`. Copies
are content-named in the same `shown/` folder beside a hashed record, `pr-<number>-<head>.json`. A head
whose diff cannot be read saves nothing: its card shows the pictures as unavailable, and the next view
reads it again. `question_view` (for an open
question about the held PR) and `review_row` project `pr_images`: one entry per path with the change
(changed first, then added, then removed), the before and after copies' URLs and sizes, the `not_shown` paths and
whether every copy is present; a PR without pictures projects none. The shown-files API serves them at
`/api/shown/<project>/<slug>/pr/<number>/files/<name>` while the record matches the task's current PR,
one file at a time with the same headers. The pictures bind nothing: the approval's check of PR, hold
and readiness is unchanged.

The reader lives at `/projects/<project>/tasks/<slug>/view/<message>` for a reply and
`.../view/<question>/<revision>` for a question. Both routes render the task page itself, so the
conversation, its scroll position and any unsent message stay mounted underneath. Where the live
session panel sits inline (a window at least 1280 px wide), the reader takes the panel's place;
elsewhere it covers the screen and the task page underneath is inert. Close, Escape and browser Back
return to the conversation; **Newer** replaces the entry with the latest version. Opening from Needs
you keeps that page's unsent answers in `visitMemory.ts`, as other task links do, and its Back returns
there. The conversation's offscreen-question jump prefers an open member that shows files.
The reader reads `GET /api/shown/<project>/<slug>/<message>` or `.../<question>/<revision>`, which
returns the view with the document's text, and serves each listed file at `.../files/<content-name>`.
These reads are ordinary paired-device API reads: they resolve only that reply or question revision,
verify the record's identity and each file's size and hash, and answer anything else with a plain
404. Files are sent with their own image or text type, `nosniff`, `Content-Security-Policy:
default-src 'none'; sandbox`, `no-store` and `no-referrer`. Viewing is a read and changes no task
state; a follow-up is an ordinary message, so neither viewing, discussion nor a resume approves,
releases a hold or changes a question.

Markdown renders through `MarkdownDocument`, which the file reader and the coordinator's message cards
also use: GitHub-flavoured Markdown with raw HTML parsed and then sanitised to GitHub's allowlist,
narrowed further so that no script, style, frame, form, `picture`/`source` or event attribute
survives and images take only their source. After sanitising, an image that names a shown copy loads
that copy and opens the gallery; a remote image becomes a link with its alt text; any other image is
an **Image not shown** box with its path. Web and mail links open in a new tab without a referrer,
a `#heading` link scrolls within the document, and other relative links stay text with their target
as a title. Headings carry GitHub's `user-content-` anchors. The gallery (`ImageViewerHost`) adds one
history entry while open, so Back closes it; it moves between a set's images with buttons, arrow keys
and a finger-following swipe, zooms by pinch, double-tap, wheel and keys, and shows **Image unavailable**
with Retry when a copy fails to load ([SPEC §3.9.3](../design/wireframes/SPEC.md#393-image-viewer)).

Validation captures follow the same fixed-evidence path ([validation captures](DEVELOPMENT.md#validation-captures)).
`alt task reply --capture <run>` reads that run's GIFs from the task's own `validation/` evidence through
directory descriptors without following links, accepts only regular files that `capture.describe` parses as
one GIF within 1 MiB, 1024 px a side and 300 frames (at most 12), and saves content-named copies in the task
folder's `captures/` (at most 64 MiB per task) with their titles and shapes on the reply's conversation row.
The conversation shows **Watch capture** under that reply, opening
`/projects/<project>/tasks/<slug>/captures/<message>` in a new tab. The page reads
`GET /api/captures/<project>/<slug>/<message>`, which resolves only that L2 reply; each GIF is
`/api/captures/<project>/<slug>/<message>/<content-hash>.gif`, served to this machine or a paired browser
only when the reply lists it, its bytes match the hash and it still parses as a capture, as `image/gif` with
`nosniff`, a restrictive CSP, `no-referrer` and no-store caching. Anything else is **Capture unavailable**.

### Monitor and live sessions

The Monitor page reads `/api/monitor`; no hold, incident, route or follow-up work is derived from
those readings. Its separate update section reads the shared overview and offers the existing
quiet-point Restart action. `/api/monitor` answers with `seats`: one row per configured engine, in the
seam's order, as `{engine, label, quota}`, where the label is the seam's display name and the quota is
that seat's reading whole. Only the engine seam knows which reading belongs to which engine, so the
page ties no reading to an engine key and spells no provider. It sits in the shell's page container
and shows one seat card per row: either the five-hour and seven-day windows a native usage report
names, or windows named by the length the seat reports. Each window renders independently, including
zero; an absent five-hour, seven-day, first or second window is explicitly named, without a meter.
Available windows show percent used, a meter with the reserve line drawn at the payload's `reserve`
(`config.RESERVE`, where `route.reserve` skips optional work; the rail's readout rows carry it too), when
it resets in relative and clock terms, the plan where the seat names it, and how old the reading is. A
seat with no reading at all says so and carries the reading's own `why`, the one line that fixes it;
a reading older than the age the router itself trusts is stale: still shown, dimmed, and labelled. One
routing card answers which engine/model each project's L3 (its pin, or Auto) and a fresh L2 would get
for a turn started now, in `pick_engine`'s own words. It explains the selected tier, comparable or
unknown quota, continuity and skipped options; no eligible option gives an actionable installation,
authentication, reset or configuration explanation. The
sessions the monitor knows follow, each with its task, its engine and the model when the API reports
one, its context meter and the age of its snapshot. A running [routine](#routines) run is a
session row of kind `agent` after its project's tasks: its routine's name, run number, engine and model, the
context the project tick last read and when its session made that request (so a long tool call ages it), and its
minutes so far against its limit. L2 rows carry their persisted `token_usage` in
the same read and offer **L2 usage details**: the task total, observed helper counts and attributable
tokens, then per-helper parentage and owner attempt context. All helper detail stays behind the
disclosure; expansion starts no collection or separate request. Archived evidence remains in task
and report reads. No session is one muted sentence. Last, `agents` lists each routine that is
not retired and is on with a schedule or the event trigger, has a run waiting or running, paused itself, or ran in the last seven
days, plus the Weekly improvement review once it is the operator's on the same terms (`monitor.agents`,
`project_agents.monitor_agents`, `improve.monitor`): what comes next in the routine page's status order and the
latest run that ran. `clock` is the computer's zone, so the page names it after a schedule's time when the
browser is elsewhere. These reads write nothing, and the Weekly review is read only where its record exists.
Loading is a skeleton in the page's shape, and a failed read is one sentence with Retry.

The task page is the operator's conversation with the L2 beside the worker's live session
(design spec §3.10). Its compact desktop header puts the crumb back to the project, wrapping title
with its state dot, Reject with an inline confirm, details and live-panel controls in one row.
Attempt, when the task started or finished, current context and token usage open in Task details at
both viewports. A second wrapping row keeps chips visible: the state, the model on
its engine as the engine seam reports them, the last PR with whether it merged and how the main run
concluded, and concise Merge held status. Complete block and merge reasons open in task details,
wrap without truncation and remain distinct when both apply. The conversation uses the project conversation's bubble, prose,
day-divider, and composer components: the operator's rows as bubbles and the L2's rows as prose
under day dividers. Each message L3 sent the L2 is one compact line: "L3 ·" and the one-line
`summary` L3 supplied with `alt task message --summary`, or "L3 messaged the L2" for a message saved
without one, with an image count when it carries images. Its Show opens the complete stored text and
images in place; the task record, the L2's inbox and authority checks keep every word, and the summary
is stored beside the text as display metadata only. Then come the open question group at the end of the conversation (closed groups
at their recorded message anchor), a held review card when one waits, no open operator question is about its PR (marked as its merge question, or
unmarked and linking or naming it), all active reviews are completed and assessed with no open findings,
no L3 question or L3 wait remains, and the PR is not already
approved since its hold. The generated approval submits its PR and hold identity through the message
endpoint, which rechecks this readiness under the task lock before writing. Explicit operator
questions retain their existing response surfaces. The composer appears
while the task is running, blocked, reported with open-PR owner evidence, or queued before its first
dispatch or with an existing question. Waiting on L3 stays a
concise explanation with its complete reason in details. Task rows and pages share a read-only
explanation from current questions, fault, Stop and wait records. An interrupted session says it
ended before completion; an open coordinator question supplies the known prerequisite even when
the fault reason contains worker output. Other system faults remain a system problem without a
guessed cause. Raw diagnostics, identifiers and complete reasons stay in details and incident
evidence. Excerpts of authored wait prose are bounded; they do not claim recovery or schedule a retry.
Operator questions and merge holds stay independent of the explanation, and Stop confirmation
comes only from steering evidence. One replacing two-line public update sits at the end of the conversation's
scrolling column and expands on request. It appears only while both its public words and recorded
activity are less than 60 seconds old; missing, untimed and unavailable output leaves no preview.
Tool output alone does not keep stale prose visible. Updates and removal preserve an older-message
reader's position. Following stays at latest through preview expiration and replacement; React
commits restore the bottom before queued layout-induced scroll events can change following, while a
scroll up at an unchanged viewport height releases following before its own event arrives.
Stop is one click in the task header at both viewports, serving Conversation and
Live session with one action. Stop, Continue and Check status share its button treatment and position.
It remains Stopping until termination is evidenced; failed or unknown
termination says Stop unconfirmed. Status rechecks read evidence without retrying Stop. After Stop,
Continue preserves the unsent draft; sending a correction explicitly resumes the saved session.
Desktop Escape applies only outside inputs, dialogs, recording, menus and overlays. The live
session panel is closed when entering a question. When opened, it is 480px inline at 1280px and
wider and an overlay from the header's panel button below that; the overlay carries the task action
in its own header while its scrim blocks the page header. It reads the worker's native session
and task-owned turn records together with Altitude's task events as one transcript: tinted prompt blocks, the
worker's prose, each tool call as one compact row with its output folded under it, task boundaries
as thin separators, each row with its recorded time or "time unavailable", hidden reasoning never shown, and Raw events behind a
toggle for redacted records, the task's other operational events among them. The panel opens its
recent tail and loads older activity when the reader scrolls upward near the top. Each initial,
history and update reply contains at most 50 rows and 64 KiB of serialized JSON. Tool calls and
results fold on the server before paging; stable row identities are distinct from chronological
order, so late events and updates to old commands merge into their correct positions. Raw events
shows one preview per source record. Full record opens an explicit 4,000-character chunk, with
Show more for subsequent chunks; the complete redacted record remains accessible.
Translated item notifications record their local UTC receipt time in the engine adapter; native
session records retain their source timestamps. A folded command keeps its first recorded event
time through output updates, history and reconnect; a command whose start is untimed takes its
completion's recorded time. This is recorded event time, not elapsed duration
or an exact OS process-start time. Records without timing evidence remain unavailable; launch,
poll and file modification times never fill a command's missing timestamp.

Transcript polling sends a scoped epoch/version cursor and the oldest loaded order boundary.
The server retains only row signatures, order and change versions for active/recent viewer scopes.
Indexes expire after 60 idle seconds, checked on access, without evicting other active readers;
it retains no transcript bodies. Unchanged source fingerprints
avoid parsing and return a small response. Changed inputs rebuild the canonical projection.
Task-owned invocation records and stdout use a request-local reader in the engine seam.
The reader refuses a task or worker name that is not one path component before opening directories or
inspecting task metadata, as it refuses a linked entry; directory-relative no-follow lookups alone do
not constrain intermediate components.
It selects the registered project's live task directory, or its archive when the live `tasks`
parent or task entry is absent or an ordinary non-directory; linked entries refuse selection.
It pins the selected job directory. Historical discovery
parses each regular JSON record once and scans stdout incrementally through the initialization
header; current activity uses the validated worker identity without a header selection rule.
Projection and activity read complete stdout bytes through a regular no-follow descriptor relative
to that pinned directory. The post-projection fingerprint also compares opened runtime identities
before index publication; same-inode appends remain readable. A replaced JSON or stdout identity refuses the view before index
publication and makes activity unavailable; the next request rediscovers the source. The reader
retains no transcript body or descriptor per historical turn. External native sessions remain
distinct adapter-selected sources with their existing provenance boundary.
Deletion history is bounded; index loss, source replacement or an expired cursor requests bounded
reconciliation of a paused reader's loaded interval. A following reader reopens the recent tail
after a lost index or a long absence; older rows remain available by scrolling upward. The browser
preserves a paused reader's visible row while reconnecting. Source writes during a read leave its
fingerprint stale so the next poll includes them without failing the current bounded read.
Project/task/engine/session/attempt/mode define viewer isolation. Same-attempt worker resumes
refresh the projection without resetting reading position. Before a render changes the transcript,
the viewer captures native scrolling whose event has not arrived yet: a paused reader's scroll, or a
following reader's upward scroll, which stops following. That position and its upward-history
intent survive the refresh. Access and generation are checked
before and after reads. Transcript state never authorizes task actions.

Only the mounted viewer retains loaded rows and reading state; navigation, generation changes and
pairing loss cancel reads and discard them. There is no persistent browser storage or cross-task
prefetch. Hidden documents and inactive phone panes suspend polling. One request at a time serializes history and live updates. Continuations yield after two
requests so catch-up shares the connection with ordinary navigation. Loaded DOM and server metadata
grow with history and active viewer scopes; this is not a virtualized or constant-memory transcript.
Normal text/output previews use at most 4,000 characters and other strings at most 512, with further
shortening when necessary for the serialized byte budget. Raw disclosure supplies the complete record. A queued
task shows what it waits for in place of the session, a finished one says the session ended, and a
missing session file says so. On a phone one header carries Back, a bordered title dropdown, the task
action, L2 state and independent Merge held status. The title dropdown opens metadata, tokens, full
reasons, View question, Reject with confirmation and operational Resume. Stop and Continue stay
directly accessible in both views.
Desktop keeps direct header actions. Two tabs, Conversation and Live session, switch the content
(`/live` selects the second); they stay visible
when software keyboard use hides bottom navigation. The composer sits above that navigation or
the keyboard. Details closes back to its opener without changing the draft or reading position.
Open questions sit at the end of the chat with a conditional floating question jump. Latest floats
beside it when newer messages are offscreen; the question jump alone serves a shared bottom destination.
Both are labeled 44px controls above the composer and disappear when their destinations are visible.
Preview access stays in the question; tabs and the panel toggle provide live navigation without an
extra composer row. There is no generic Resume while an operator question is open; viewing
details never resolves a question or releases a merge hold. A rejected task is read-only with the
composer and activity preview gone; a done task keeps its composer, whose hint says that sending reopens it. Phone swipes move one tab at a time through
the tabs in their order, Conversation, Live session and Terminal when the task offers one, left to the
next and right to the previous, without wrapping: the views sit on one track that follows the finger,
settles into the switch past half the width or a fling, springs back otherwise and resists past either
end; a swipe landing during a settle is held until it lifts; reduced motion switches instantly. Conversation and Live session stay mounted and laid out, the
inactive one invisible with its transcript polling paused; a drag toward Terminal shows its loading
view, and the shell opens or reattaches only once the switch completes. The tab row stays in place
during the drag. Deliberate horizontal gestures exclude browser edges, selection, controls, the
composer, recording, dialogs, horizontally scrollable content and the terminal screen and key row;
vertical scrolling stays native. Tabs remain the accessible direct navigation. View switches preserve draft text,
selection, images, conversation position and live reading state without reopening the keyboard;
local history and deep links retain their navigation contract.
Scrolling up in Live session pauses following while updates keep arriving below; Follow catches up
to the newest output. Earlier-history loading and Retry appear inline at the top. A live-update
failure retains readable text with its error in the footer; reconnecting or catching up takes
precedence over Following/Paused. Prepends and changed rows preserve the visible row's pixel offset.
Switching
views cancels unsent dictation and releases the microphone; an explicit voice Send completes for its
original conversation while hidden, without refocusing the composer.
The read-only activity projection uses only the selected worker generation, existing redaction and
public output. Provider parsing stays in the engine seam; no summarizer, extra model instructions,
new archive or copied conversation replies supply the preview. Conversation and Live session derive one
activity cue from its recorded output time: a pulsing dot within 60 seconds of output. Live session
keeps a still dot and "No new activity for …" after that, and no pulse when activity is unavailable
or the task is not running; Conversation hides the preview instead.
The same projection carries `background`: the work the current worker keeps running beside its turn,
each item with its description, kind (command or agent), launching call, start, status (running,
completed, failed or stopped) and end. `engines.background_work` reads it from the engine's own records:
Claude's background-task start and end reports, including a running command that Send now moves to the
background, and a Codex command still in progress once the turn takes a further step (the model writes or
reasons, or starts another call after a call ended; parallel calls alone are not one). It holds every
running item, then the 20 most recent outcomes in the order they ended, and is `null` when a record is
unreadable or the engine reports no such work. Live session shows it as the **In the background**
tray under its stream while the task runs, and marks each item's call row in the stream (design spec
§3.10); Conversation and task rows do not use it.

Runtime files live under `ALTITUDE_HOME`; a task is a directory a person can read. Source-controlled
personas, schemas, templates, and hooks describe current behaviour: `hooks/` holds the Git hooks
that `git_policy` installs into every managed repository and the Claude inbox hook.
[AGENTS.md](../AGENTS.md) holds project and review rules; these current documentation pages retain the system's operating decisions and rationale. Git history preserves completed migrations.
