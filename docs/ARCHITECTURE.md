# Altitude architecture

## Container deployment boundary

The host launcher checks that Podman's runtime bus resolves to the local user's owned bus socket.
Disposable fixture runtimes retain private storage/process metadata and link only that same user
bus into their runtime directory. The host controller selects cgroupfs and distribution crun;
its system-bus address names an unavailable fictional endpoint. A delegated user service owns each
application lifetime. Disposable VM acceptance independently observes service-manager calls and
their senders, including forced cleanup. It allows systemd's verified same-user delegated process
attachment, which does not use polkit, and fails on unexpected management attempts. This is
validation of the selected runtime clients, not hostile-process confinement. The
[container evidence](CONTAINERS.md#evidence) records the tested boundaries.

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
based on its effect on their responsibilities, decisions or work.
Its task briefs convey the actual problem, intended outcome, acceptance and material project context;
brainstorming stays distinct from requirements. The [L2 persona](../personas/l2.md) owns investigation,
approach, relevant system implications, source assessment, questions and verified delivery within that outcome.
These responsibilities use existing task operations, with no broadcast, runtime classifier or
memory store. Project-local authority, proposal checkpoints, merge holds and verified recovery
remain governed by the existing boundaries.

L2 receives the request, repository context, expected files, worktree, branch, and merge policy, and chooses
the lightest useful execution shape. Its conversation with the operator is stored apart from tool logs, so
the operator messages it directly without routing through L3. Messages queue on the task and reach the
worker at its next checkpoint; an explicit Stop ends a worker. **Send now** on an inbox-owned operator
message interrupts current work through the same Stop path and resumes the saved session with that
message as its next input. The remaining inbox rows retain their order for later checkpoints. Task message writers hold the project
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
the UI shows Message removed, and CLI history/search retain an explicit removal marker. Removed text
cannot serve as a new decision source. Quick choices and messages
already cited by recorded decisions remain intact. Removal changes no Stop, question, fault or resume
request. Claimed messages say Sending to session and cannot be removed; a launch attempt retains
per-message uncertainty through failed-launch recovery. A released prelaunch claim becomes removable
again. Successful input handoff and matching session initialization record delivery for the exact
bound batch; native hook attachments can independently prove delivery.
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
`alt task handoff <slug> --engine <engine> --attempt <N> --reason '…'` uses this same coordinator
transport and daemon request for an exited owner blocked by worker death or a recognized usage
limit. It fences the observed attempt, worker/session and block, refuses live workers, active claims
and explicit pins, and requeues the same task. Its `next_engine` confines the next launch to that
engine's configured options and is consumed when dispatch binds the fresh attempt. Pins and
availability are checked again before execution and launch. `route.pick_task` supplies the same
target-aware availability and pin explanation to dispatch and the queue. A fresh attempt clears current
verification while retaining prior reports and delivery history. Ordinary resume remains unchanged.
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
Directories without a resolvable status record contribute no task evidence and produce `partial`
results with an unavailable-task count and up to twenty logical task references. Available chat and
valid task evidence remain searchable; records are neither repaired nor inferred from stray files.
Empty corpora and absent optional files produce `no_results` only when no task evidence is unavailable.
Every L3 turn advertises lookup, including native
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
a missing task returns HTTP 404 without a failure traceback. Archival only renames the live task
folder into the archive, so a status read that misses the live record reads the archived one; polls
and action responses crossing the move return the archived task and state.
Task status, documents and events share the archive lock while their snapshot is read.
`GET /api/monitor` and `alt monitor` list only sessions Altitude runs: each project's L3 and its
running, blocked or reported L2 tasks. Other sessions on the machine are not read. Session rows expose `model` beside `engine`, with `engine_reasoning_effort`
when available. An unknown Monitor model is an absent key rather than null.

`config.DEFAULT_SETTINGS` names one requested model and one requested effort per role and engine
(`l3_model`, `l3_effort`, `l2_model`, `l2_effort` on Claude; `l3_codex_model`, `l3_codex_effort`,
`l2_codex_model`, `l2_codex_effort` on Codex); each is independent of the others. Absence preserves
existing defaults and `native` requests no override. `config.task_effort` owns engine support and
resolution, and a setting accepts only its own engine's levels. `config.default_model` and
`config.default_effort` resolve an Auto option for the chosen role and engine: an option's own model,
then the project default, then the role default (Opus for a Claude L2, Fable for L3, the CLI's own model
on Codex). They are preferences: only `l2_engine`/`l3_engine` and task or turn pins force an engine.
Explicit task `effort` and `model` win over the defaults without rewriting them. Routing resolves effort
per option, excludes Auto candidates that cannot accept an explicit level and keeps pins strict.
Fresh dispatch saves that selection as `launch_effort` alongside `launch_model`; message/resume
reuses it without resolving current project defaults or copying observed provider values.
L3 resolves each turn, including same-conversation resumes, and saves requested `effort` and
`launch_effort` before calling the engine. Running calls retain their original selection.
Both roles clear observed `engine_reasoning_effort` for a new turn; absent observations stay unknown.
Model compatibility and provider caps remain native decisions. Effort failures do not trigger
application-side downgrading or engine fallback. No model capability catalog or session migration exists.

`GET /api/defaults/<project>` returns the L3 engine pin and each role's model/effort pair per engine
with its setting key, default and engine-owned choices; `POST /api/defaults` saves one setting through
the existing settings request/apply mechanism under project/registry locks. The project Settings page
applies its request immediately; CLI `alt project set` requests are applied on the daemon tick,
independently of worker capacity. A conflicting pending request refuses another save until applied.
Neither path mutates engine pins or running tasks. Native L1 helpers inherit or override effort
through their own engine controls; Altitude does not create helper workers or promise a uniform L1
override.

Global role responsibilities live in the personas; project policy lives in the repository's
instructions, with [AGENTS.md](../AGENTS.md) authoritative for Altitude. The shared engine boundary
names the repository rule file each turn; [instruction loading](SESSION_LIFECYCLE.md#repository-instructions)
describes selection, imports and activation limits. Brief boundary excerpts use the task worktree's
rule file. Managed projects retain their own policy.

Source deployments export their committed installation HEAD on successful service startup into the ignored
deployment-local `.altitude-source/<sha>` directory. `config.REPO` identifies the deployment checkout;
`config.SOURCE` identifies the activated source for CLI code, personas, hooks, templates and schemas.
These exports sit outside worker writable roots and remain available to existing workers. Managed
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
`installation.py` owns archive validation, activation receipts, recovery and retention;
`platform.py` is the platform seam: it owns the generated per-user daemon service, the jobs that run
workers, reviews, machine commands, updates and restarts, their status and stop, resource limits, and the
process and socket facts behind worker identity, terminal Stop and the terminal's agent check. On Linux
x86_64 the service is a systemd user unit and each job a transient user unit whose cgroup holds every
descendant. On macOS 15 or newer on Apple silicon the service is a LaunchAgent in the user's `gui` domain
(`~/Library/LaunchAgents/dev.altitude.altd.plist`, logging to `~/Library/Logs/altitude/`), and each job
is its own launchd job, so its processes share a kernel coalition that no `setsid`, double fork or
cleared environment leaves. The job's program is a supervisor outside any sandbox: it runs the command,
enforces the time limit, and when the command exits stops whatever remains in the coalition, records the
status and removes its own job; while the command runs it holds an idle-sleep assertion (a closed lid
still sleeps). A caller's input and output reach the job as `systemd-run --pipe` passes
them: a regular file or device by path, a pipe through a relayed FIFO, and piped input (always a whole
prompt) through a private file. Stop signals every coalition member, checking each one's start time and
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
timer thread: one anonymous request to GitHub's latest-release endpoint for the repository in
`release.json`, retried after an hour when offline. `update.json` in the runtime home keeps the
newest stable release found and any update the app started, and the daemon, the app's request and
the update itself change it under one lock; source deployments neither check nor record. The `update_check` machine setting turns the check off and hides what it found. The
overview's `update` field and `alt doctor` report the installed version and a newer one; an
interactive `alt` command prints one line about it at most once a day from that record, never to
agents. `POST /api/update` accepts only the exact newer version the record shows and starts
`alt update --version` in a job of its own (`platform.detach`), so the update outlives
the service restart it causes; it passes the operator terminal's request checks, and Altitude has
no login, so anyone who can open the page can start an update to that verified release, never a
downgrade or another build. An update that fails, or has not finished after thirty minutes, reads
as failed until retried; the installed version keeps running or is restored by activation recovery.
The page shows a failure only as "Run alt update in a terminal to see why."; causes, which can name
private paths, stay in the daemon log, the update unit's journal or the terminal.

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

Fresh defaults are HTTPS on `127.0.0.1:8890`. `tls.py` generates one installation-local CA and
server certificate in `~/.config/altitude/tls`, outside runtime/source/project writable roots,
with private directories and keys. A generated CA carries critical name constraints permitting only
loopback, private-network ranges, private names and the subtree of a DNS name configured at creation,
so device trust extends to no other public site. Existing unconstrained or external CAs keep their scope. Startup validates identity and reissues a managed server certificate
whose host no longer matches the bind address; the existing daily timer renews managed server
certificates within thirty days of expiry, retaining the CA/key.
Invalid TLS refuses startup or reports renewal failure without switching to HTTP. Each connection
completes its handshake on its own request thread within ten seconds, so a stalled client drops
only itself and never delays other requests. External
certificates are validated without replacement. Browser/device trust stays explicitly unknown
until the user imports the public CA and verifies it; `alt doctor`, the installer and Settings › Devices
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
only while sharing is open and disappears during closing or after expiry. `alt pair` takes its link
from the same discovery. Sharing refuses loopback-only addresses; a browser on the hosting computer
imports the public `ca.crt` reported by `alt doctor` directly. Trust installation stays a deliberate
operator action in the browser or OS. Remote binding and trust remain explicit;
HTTPS identifies Altitude, and pairing (below) decides who may use it. See
[setup](SETUP.md#trust-https-on-each-device).

Every request passes the same checks before routing. Without TLS the `Host` must be an address or
`localhost`: a DNS-rebinding page names its own host in both `Origin` and `Host`, which HTTPS refuses
at the certificate. Every POST must come from Altitude's own page or from a client that is no page:
`Sec-Fetch-Site`, when present, must be `same-origin` or `none`, and an `Origin` must name the
request's `Host`. The `alt` CLI sends neither header; another site open in the operator's browser
cannot make it send an action. The Vite dev proxy gives the dev page's own requests altd's origin and
leaves any other page's origin in place. A JSON body is at most 1 MiB; image messages and voice
recordings have their own limits. A client that sends no complete request line and headers within
thirty seconds loses its connection. An internal error's text reaches only the CLI and paired
devices; any other client gets a fixed message, and altd's log keeps the details. The web app's pages refuse to be framed by any page
(`frame-ancestors 'none'`).

Then `access.py` decides who is asking. The page and its files, `GET /api/health`,
`GET /api/access` and `POST /api/pair` answer anyone; every other request, including design boards,
needs a paired browser or this machine's key, and otherwise gets 401 with `"pair": true`. Everything
lives in `~/.config/altitude/access/` (mode 0700, beside the TLS material, outside every runtime,
source and project root). altd creates `machine.key` there when it starts; the `alt` CLI and the
restart script send it as `X-Altitude-Key`. The CLI finds altd through the service record: as the
service instance (`ALTITUDE_SERVICE=1`) starts serving, it writes `service.json` beside the key with
its PID, address, port, scheme and TLS directory, replacing the previous record. It decides where
`alt` sends the key and which CA it trusts, so it lives in this store rather than in the runtime home
that task folders share. `alt` reads it for its altd calls instead of its own launch environment, so a worker started before a restart onto
another certificate folder or port reaches the new service without relaunching. It verifies altd with
that folder's `ca.crt` (the system trust store when the folder has none) and refuses, naming the CA,
an altd that does not prove its identity; there is no unverified fallback, and a missing record is
named. Serve-only instances write no record. The key is compared in constant time. Only the operator's
account can read it, so it proves a caller runs on this machine as the operator; Altitude's workers
share that account, as they share its files. `alt pair` (operator only) writes one pairing code
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

### Linux and macOS

Everything above the platform seam is the same on both hosts. These are the behaviors that differ;
`platform.py` owns each one, and the sections named in the last column describe it.

| Area | Linux x86_64 | macOS 15+ on Apple silicon | Where |
| --- | --- | --- | --- |
| Service | systemd user unit; restarts on failure; runs while the user manager runs | LaunchAgent in the `gui` domain; restarts on failure; starts at login, runs with the screen locked, stops at logout (running before login is a later increment) | Setup, Operations |
| Service logs | the user journal | `~/Library/Logs/altitude/`, also for detached jobs such as updates | Operations |
| A job (worker, review, machine command, update) | transient user unit; systemd holds every descendant in its cgroup and enforces the time limit | its own launchd job; a supervisor enforces the time limit, holds an idle-sleep assertion, and stops the job's kernel coalition when the command exits | above |
| Stop | stop the unit; the cgroup takes every descendant; pidfd pins each signal | kill every coalition member, rechecking start time and coalition just before each signal (no process handle exists) | above |
| Claude confinement | Claude's permission boundary only | also Altitude's Seatbelt profile: writes only under its roots, signals only its own processes, no launchd control; a nested sandbox cannot start, so a browser the worker runs needs its own sandbox off (Chromium's crashes) | Isolation and landing |
| Codex confinement | Codex's own sandbox (bwrap) | Codex's own sandbox (Seatbelt); the two profiles cannot nest | Isolation and landing |
| Operator-grant commands | outside the worker sandbox with the user bus reachable, so a command can stop or reconfigure its own unit and its time limit | outside any sandbox with launchd reachable, so a command can signal its own supervisor; on both hosts the time limit bounds an ordinary command, not one that works against it | Isolation and landing |
| Terminal Close | the shell is a transient unit; stopping it hangs up its cgroup | the shell is a launchd job; stopping it hangs up its coalition | Operator terminal |
| Terminal agent check | `/proc/net/tcp` and cgroups | this user's processes' sockets (libproc) and job coalitions | Operator terminal |
| Image conversion memory cap | `RLIMIT_AS` | a watcher that kills the converter past its memory footprint | above |
| Native libraries, tools | system packages (apt) | Homebrew; `openssl` must be OpenSSL 3, not macOS's LibreSSL | Setup |
| Temporary directory in jobs | `/tmp` | the user's own `$TMPDIR` | above |
| Source-checkout deployment | systemd unit (`make install-service`); TLS drop-in; self-restart | LaunchAgent (`make install-service`); self-restart through the seam; TLS drop-in refused with an explicit message | Operations |
| L3 journal reading | `journalctl` shim | not available; L3 reads service status through the broker | Isolation and landing |

Case-insensitive project names are refused on both hosts, because macOS disks are case-insensitive by
default and runtime folders are named after projects.

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
than losing its wake. Retry rechecks completed effects before writing.
Pending, running, created, reused, not-applicable, failed and input-needed results come from
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
same bounded work. Configuration faults notify the existing L3; **Discuss with L3** opens its conversation
without sending a message or launching another repair agent. L3 investigates with its existing
tools and can request project-scoped daemon repair through `alt project setup`, independently of
worker dispatch. An agent's claim of repair does not complete a step: current checks verify it.
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

The [native browser runtime candidate](DEVELOPMENT.md#native-mac-browser-runtime-candidate) keeps
the existing execution contract. Its pinned Seatbelt patch adds only registration and lookup of
whole Chromium rendezvous service names with a positive decimal PID, for effective network-enabled
workspace-write process policies with both current-account user-manager path denies. All eligible
commands share that allowance; it is neither browser identity nor task isolation.
The guard excludes read-only, network-disabled, filesystem
helper and other narrower custom policies by comparing filesystem semantics to canonical
workspace-write plus the exact bus and systemd denies derived from the real account UID.
Its comparison operand follows the named compiler's omission of legacy skip-missing defaults;
supplied restrictions and emitted filesystem rules are unchanged.
Plain workspace-write without those denies has no added allowance.
A named custom profile with identical effective permissions has the same allowance. Existing file
roots, metadata protections, process/signal restrictions and service denies remain in force.
This is an unadopted preparation artifact, with separate installation, isolation acceptance and
operator merge gates; it grants no LaunchServices, WindowServer or host clipboard access.
The fictional recovery lane selects the locked headless shell only for project-isolation/draft and
file-reference/clipboard walkthroughs; the full Chromium suite remains the default and both lanes
run in required CI. Missing shell notification APIs do not narrow the primary suite or establish
native Mac parity.

Auto uses the highest-priority configured tier with an eligible option. The default ties Codex and
Claude on their role defaults, so a Claude L2 launches on Opus and L3 on Fable, with Opus below as
L3's fallback when Fable is rejected; a lower tier never repeats an option a higher tier already
resolved. `ALTITUDE_PRIMARY_ENGINE` chooses only the default tie order. Named, comparable seven-day account readings select within a tie. Unknown readings use
configured order, and L3 retains its current engine/model within that tier unless a competitor has
at least fifteen percentage points more weekly headroom. A higher available tier takes precedence.
Missing executables, exhausted windows and explicit provider rejections exclude the affected option;
unknown access remains eligible. Authentication rejections exclude the engine and model rejections
exclude only that model for thirty minutes. A current model-specific weekly reading at 100% also
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
Altitude supplies focused [role instructions](../personas/), repository context and delivery
boundaries. Execution strategy stays adaptable because a fixed sequence of stages and specialist
roles can outlive the model/tool assumptions behind it. Customization belongs in repository
instructions and the engines' native skills, hooks and agent facilities where appropriate.

Required background results belong to the active owner session. The existing native Stop hook
combines queued steering with an instruction to wait for in-flight tasks, using the engine's
background-task payload. It acts only while the task is running; explicit blocks and operator Stop
retain their exit paths. It creates no job registry, automatic retry or alternative completion path.
Report freshness and delivery verification remain authoritative; native coverage limits are explicit
in [polling and cleanup](SESSION_LIFECYCLE.md#polling-and-cleanup).

### Cross-engine review

`reviews.py` owns task-bound proposal and changes review records; the task remains owned by L2.
L2 proactively seeks adversarial review of complex proposals before code and complex implementations,
using judgment to keep simple work light. L2 requests through the CLI or the operator uses the two
subject entries in the task menu. Each existing entry opens saved evidence without invoking review;
reruns are explicit actions in details. A repeated request or source message reuses its receipt.
The owner prepares a committed checkpoint, identifies the exact original L2 proposal message for a
proposal review, and invokes the fixed daemon endpoint. Open questions do not gate either subject: a
request may continue a question-blocked owner solely for review while preserving every open question and
its original authority. This adds no implementation or merge authority, helper queue, provider handoff,
owner session or general command transport.

The engine and routing seams prefer an eligible configured option different from the owner, respecting
project choices and known quota exhaustion. Otherwise they select a separate same-engine invocation
under the same captured-input/read-only contract, with its fallback reason recorded and shown alongside
engine/model and account-allowance uncertainty before requesting and in saved evidence. An explicit
per-request engine/model selection replaces that preference with a single candidate under the same
eligibility, quota, rejection and capability checks; it is saved with the request, rechecked at run and
never substituted, and project defaults are unchanged. No engine switch occurs after launch. Fallback cannot bypass unavailable observation, cancellation or capacity. Admission
uses the existing machine launch lock: one reviewer machine-wide, with an additional machine slot,
no reservation while waiting for preparation and no automatic retry. L2 and reviewer instructions
set expectations for a relatively quick, focused review without a programmatic duration cutoff.
The command waits without a review deadline; streamed JSON whitespace detects disconnected callers.
Owner changes, cancellation and caller disconnect stop the run. Planned activation waits for in-flight
reviews to return their results; review admission shares the restart fence. Capacity contention leaves
an accepted request pending for a later explicit run. Unexpected restart reconciliation cancels an
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
`context.json` names the proposal message and points to the file. Identity and freshness hash the
logical context, independent of this representation; the input hash covers every captured file. Changes reviews capture the merge-candidate tree and diff; a branch that conflicts with main is
refused with its conflicted files. Missing proposal input prevents invocation. Image evidence needs a
textual account and is explicitly outside the text reviewer's coverage.
The adapter exposes only a fixed captured-file list/read/literal-search tool: no tests, shell,
connectors, network tools, mutation, nested helpers or task identity. Read and search deliver text
from a line/column cursor, at most 100 lines and 16 KiB per response; a truncated response names
the line and column to continue from, so a long line such as a JSON-encoded proposal is readable in
full. The result is the first complete JSON object in the reviewer's answer, fenced or bare; an
answer without one fails as invalid findings. Codex reaches it through its
code-mode host, the one native feature left enabled besides skipping host skill discovery: a
JavaScript tool bridge without filesystem, process or network globals, whose patch tool the read-only
sandbox rejects. Claude loads it from the command-line MCP configuration in restricted mode with no
built-in tools, no skills and no safe mode, which disables every MCP server. Both are provider
properties established by recorded reviewer runs, not by the suite. The adapter records the first
content-bearing read in the review runtime; a reviewer that read no content fails with that reason
instead of completing with no coverage. CLI-internal authentication uses
the configured account. Deterministic fixtures verify application behavior and adapter configuration;
live-provider compatibility remains unverified under the standing testing decision.

Failed review executions retain bounded sanitized stderr, launcher exit status and capture-completeness
evidence in the existing task-owned record. Recognized structured stdout error fields yield fixed
diagnostic categories, and a Claude result's allowlisted failure facts (subtype, terminal reason, API
error kind and status) locate failures no category recognizes; stdout transcripts and arbitrary error
prose are not copied into diagnostics.
Unknown, malformed, incomplete and truncated stdout remain explicit evidence states.
The [failure-evidence contract](SESSION_LIFECYCLE.md#cross-engine-review) describes bounds and privacy.
Review records retain original findings and separate owner dispositions for each subject. Exact
source/authority freshness and selected-input hashes are distinct. Changes assessment records the final
candidate and evidence for every finding; code, base or conversation changes require reassessment.
A finding the owner leaves `open` stays visibly unresolved, blocks merge and prevents replacing its
review; only evidence-backed fixed/dismissed outcomes clear an assessment. An additional review of the
same subject replaces nothing, so earlier reviews stay in the merge gate with their original authority.
Proposal assessment records the proposal identity and dispositions, including an explicitly selected
revised proposal when relevant. Later proposal/source/context changes require L2 assessment or deliberate review.
The UI distinguishes reviewed evidence from later L2 assessment; proposal evidence never accepts an
implementation. Request admission and final merge share a task lock. Landing checks every accepted
request for assessment or authorized withdrawal after other pre-merge checks, including changes requests
followed by a proposal request. Review cannot release an operator hold or replace required checks.
Only the operator can withdraw an operator request. No automatic rerun occurs after fixes.

## Task lifecycle

```text
queued   -> running | rejected
running  -> reported | blocked | rejected | done
blocked  -> running | reported | rejected
reported -> done | running | blocked | rejected
```

A no-code research or proposal task can go directly from `running` to `done/archive`; a git check
refuses that shortcut when the task branch changed. Code work uses the verified report path.
Archival posts a task-linked FYI with the digest to the project conversation, the coordinator's
handoff; findings stay in the task conversation. The project view's archive lists every done or
rejected task finished in the last seven days, newest first by finish time, whatever their slugs.

Queued tasks with `planned_wait: {reason, after}` are **Planned**: `reason` is one short wait and
`after` optionally names one existing task in this project. `alt task new --wait` or `--after`
creates the existing task record and written brief without a worker, worktree or WIP slot.
Dispatch skips it until that dependency is archived done, or L3/the operator explicitly clears
the wait with `alt task release <slug> --reason '…'`. An already archived-done dependency is satisfied at
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
`config.WIP_PER_MACHINE` (80). Fresh and resumed launches share this cap; blocked tasks consume no
capacity. One machine launch lock serializes admission through worker binding so simultaneous launches
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
screens say “you” and records use `config.operator_label()`.

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
above the others for fresh L2 only; L3 keeps the tiers as written. [CLI examples](CLI.md#automatic-routing-preferences) cover
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
based on the exact fetched `origin/main`. Dispatch and resume switch a clean worktree found on another
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
Invocations that merge or target this repository’s required PR check hold a separate `flock`
on `altitude-land.lock` in the repository's common Git directory, from before ownership reads and
fetch through checks and merge. All its worktrees share the lock; task-state locks remain short,
so messages and Stop stay available. Admission waits at most one hour, reports
the seconds waited, and refreshes ownership and holds before publication. Current main is merged
into the task branch before pushing when needed, preserving adopted ancestry and triggering fresh
head checks. A conflicting integration is aborted with local work retained for owner reconciliation.
For a completed review whose assessment is stale, the current L2's merging invocation retains the
turn while the owner explicitly reassesses the pinned candidate through the existing review command.
CI and assessment share the `--wait` deadline. Polls hold the task review lock only for local review
reads; network reads and sleeps leave review requests available. Assessment fetches preserve landing's
`FETCH_HEAD` receipt. A notice names every stale review and subject with the assessed/current values
of its changed evidence, so the owner posts explanations together before assessing each request.
Proposal and changes findings remain separate; a changes assessment does not retire a proposal.
Fresh context invalidation, including during final merge validation, uses the same wait and original
deadline. Final candidate, checks, ownership and hold validation repeats after assessment.
Missing or unfinished review, candidate
movement or ownership loss refuses. No review identity or disposition is automatically transferred.
The final review/context check precedes recorded approval application, preserving holds on review refusal.
The process owns the turn: return, exception or termination releases it without daemon recovery.
There is no persistent queue or FIFO guarantee. Dry runs and nonmerging preparation in other repositories do not take
the turn. External Git/GitHub writers, older landing code, self-hosted runner executions and
hand-run suites do not share it, so exact base/head refusals remain necessary.
This repository requires its self-hosted PR `check` to run the full `make check` suite. Owners
and helpers run relevant tests during development; landing does not repeat the full suite locally.
CI proves its tested merge tree equals the PR head tree. Landing requires that successful PR
check on the current head and verifies that the head includes current main. A branch missing
current main needs reconciliation and a fresh PR run on the new head. Altitude serializes final
validation and merge, rechecks identity and holds, and verifies the merged tree against the
tested tree. Missing, pending, failed or stale CI blocks; runner outages have no local bypass.
The gate governs Altitude merges; GitHub updates outside Altitude remain unprotected.
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
messages return to the ordinary resume path. Report application, verifier faults, stranded-report
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
adopted pushes never retry with force. Landing incorporates main with a merge commit, preserving
the adopted history. Adoption cannot be widened to a later external head.
The existing PR is reused, outstanding required reviews or requested changes and drafts block merge,
and the live task owner/hold/active receipt are rechecked before merging. The fetched base branch
is authoritative; lagging `baseRefOid` metadata does not replace it. Check evidence confirms the
current GitHub base target/head and exact candidate association, with real movement refusing merge.
Required checks from branch protection and active rules remain mandatory, including missing or
skipped checks. Completed skipped checks identified as nonrequired by GitHub are ignored without
interpreting workflow conditions. Failed, cancelled and pending checks still block, including
nonrequired checks; pending checks use the existing wait. Unknown requiredness or ambiguous
candidate association refuses delivery. At least one check must actually pass under the hosted gate.
For other projects without CI, the full local suite runs on a clean merge candidate:
one parent for squash delivery, two for adopted history. The suite is one `--test-cmd` argv
command run without a shell. Workflows on either pinned side keep the hosted gate, so a head that
deletes them cannot select the local suite. A dry run reports the fetched base, the exact head or
the staged tree, per-side workflow detection and the gate it would use, and names what only a real
landing settles; it commits, pushes, opens, tests and merges nothing. Adopted PRs use a
GitHub merge commit and request no branch deletion. See the [supported workflow](CLI.md#adopt-an-existing-pr)
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

A failing `git fetch origin main` for self-deploy raises `git_policy.FetchError`. The tick logs each
such failure and retries it on the next tick. It raises the `self-deploy` system fault only once fetches
have failed for five minutes without a success in between, carrying the latest fetch error. The
post-delivery fast-forward returns a note for a failed fetch and leaves the retry to the tick. Every
other self-deploy failure, including a dirty, diverged, ahead or off-main checkout and a failed
fast-forward, raises the fault immediately. The failure start is held in daemon memory, so a restart
starts a new grace period.

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
classifier, while altd refuses that call without a current grant; a Codex owner's sandbox is unchanged. A
granted command runs with the operator's access and is trusted to stay inside the approved purpose; the
record, not command parsing, makes it accountable. An operator's answer to the owner's purpose
question is recorded by the running owner from its task chat or by L3 or the operator and verified
against that question revision. Alternatively, L3 applies an original operator approval from the
same project's chat to an exact policy-purpose request: a unique, single-paragraph heading in the
registered checkout's `refs/heads/main:AGENTS.md`, pinned to its commit, must cite the approval and
match the current open owner-authored L3 question apart from whitespace. The standing path fences
the observed attempt and stores the original approval, policy text/commit and question revision;
task branches and dirty policy files supply no authority. L3 judges later corrections and revocations,
revokes active grants and assigns policy removal; neither shell commands nor chat consent are
classified automatically. Switching standing purposes requires revocation of the existing grant.
Either grant opens `POST /api/task/run` for the running owner's current attempt. altd writes the run's row, then
executes the command in a job of its own through `engines.machine_command`, with the service manager
reachable (no Seatbelt profile on macOS, since launchd refuses service control to sandboxed processes) and
the owner's task identity, one at a time, bounded by `MACHINE_COMMAND_TIMEOUT`. Because the command reaches
the service manager, it can also stop or reconfigure its own job: the time limit bounds an ordinary command,
not one that works against it. The unit writes its output and exit status to the task folder itself, and altd completes `machine.jsonl`
and adds a task event and a project event per command. The ledger is also the one-at-a-time record: at startup
altd follows every unfinished row's unit to its end (`server.settle_interrupted_machine_commands`) and completes it
from the saved status, or as uncertain without one, and the owner's CLI reconnects with its request id for the result. The owner, L3 and the operator can revoke
the grant; nobody can widen it. The endpoint shares the operator-trusted HTTP surface every worker
on this single-account host can reach; the task record and the per-command log are the boundary,
not caller identity. Both engines share the verb; only the launcher is host-specific.
Installation VMs, containers and sandboxed-browser checks need no grant: `POST /api/task/validate`
lets the running owner's current attempt, from a process in its own worker job, run one command
against its committed `HEAD` in a disposable rootless Podman container. altd starts that container
from its own deployed image, with fixed flags and limits, and keeps the runner's storage beside
Altitude's home, outside every worker's writable roots. The image prepares pinned Playwright
Chromium and WebKit with both sets of system libraries, so required emulated-iPhone validation
uses the supported runner without host package installation. Image corrections take effect after
normal source activation and require a successful browser check through that effective runner;
testing a nested candidate image alone does not establish recovery. The run is recorded like a machine run with
purpose `validation`, and results reach the task folder without following links. The worker's own
confinement is unchanged. The Settings switch **Validation runs** (on by default, kept in the runner's
storage so a worker cannot turn it back on) stops running runs and refuses new ones. At startup, before
admitting a run, altd stops abandoned runs, retains their logs and results, records them as interrupted
and removes their scratch files. Failed evidence delivery retains the original area and keeps the
runner closed for recovery. Validation holds the shared restart fence through its bounded execution,
evidence recording and cleanup, and refuses admission once restart is requested. See the
[validation runner](DEVELOPMENT.md#validation-runner).
Claude Code runs as a foreground CLI inside an independent job with Altitude's
hooks for inbox delivery and telemetry. On macOS that job also runs under Altitude's Seatbelt profile: it
may signal only processes in its own sandbox, never its supervisor, and write only under its worktree,
the worktree's Git directories, Altitude's home, Claude's own state, the GitHub CLI's configuration and
temporary directories; launchd refuses service control to any sandboxed process. A Claude L3 turn that
runs as altd's child rather than as a job starts under the same profile. On Linux Codex uses the native `altitude-task` profile, with explicit worktree, Git and Altitude-state
roots. The session bus and manager runtime directory (including its direct private socket) are denied;
other runtime-directory paths retain their policy. The generated profile also supplies provider-free
confinement checks. A task worker on either engine reaches GitHub with the operator's existing GitHub
CLI sign-in without reaching the keyring that holds it: the launcher, which still reaches the session
bus, reads the token with `gh auth token`, and the job receives it on the first line of its input,
which a fixed shell reader exports as `GH_TOKEN` before it starts the engine. The token never appears
among the job's settings (they form its command line), in a file or in a log; a launcher without a
sign-in starts the worker without one. The coordinator's GitHub reads stay on its relay. On macOS Codex keeps its native workspace-write sandbox inside the same job boundary
(the two Seatbelt profiles cannot nest). Both use the same door; private worker records and output identify both engines' sessions
after restart. Worker status accepts systemd's `is-active` result `inactive` with exit code 4 for a
collected transient unit as termination evidence; on macOS a job is active while launchd runs it or its
recorded coalition has members. Unknown states, bus failures and query timeouts
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
tools, and native admission for the trusted `alt`, `git`, `gh`, `journalctl` and `systemctl` shims.
The CLI, broker and read shims authorize their operations without a duplicate native verb list.
Claude's runtime shims and the MCP coordinator tool send `alt` invocations plus authenticated GitHub
and service-status reads through the project-bound Unix socket; altd supplies the project independently of the request,
re-applies the L3 command door, accepts only flat task identifiers and stdin, and exposes no direct GitHub or service write command.
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
is not enabled because this deployment host cannot create its required unprivileged bwrap namespace;
the permission boundary fails closed instead, while Codex retains its native filesystem sandbox.
`alt issue new --title '…' [--label …] -` and `alt issue comment <number> -` publish stdin through altd's login to the checkout-origin repository for L3 or the operator, refuse L2 and private evidence references under the AGENTS.md boundary, and record one project event with actor, title, and URL.
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
bytes, the next offset and whether older output was dropped, and `end` carries the final status
(`exited`, exit code and end reason: `exited`, `closed`, `task-finished`, `project-removed` or `failed` with
its `error`). A
reconnecting page resumes from its own offset. An ended terminal leaves the status at once (`none`), accepts no
more input, resize or close, and stays readable only by a stream naming its id, so a page attaching after a
failed start still reads why, until a new terminal opens, the task finishes or altd stops. `GET /api/terminal/<project>?task=` returns
the status: state (`none` or `running`), terminal id, the setting, folder and the foreground command.
`POST /api/terminal/<project>/{open,input,resize,close}` with `{task?, id, data?, cols?, rows?}`
drive it, and `POST /api/terminal/<project>/command` with `{task, id, text}` names the command the page is
about to type for the owner. Replies to accepted terminal POSTs are HTTP/1.1 with a length and keep their connection,
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
stayed quiet for 300 ms, re-reads the status and types the command through xterm's paste when no program
holds the foreground. It never sends Enter, and the server sees ordinary input. Before typing it into a task
terminal, the page names it with `command`, so altd follows that one command in memory: the first Enter the
operator types after it starts it, and a Ctrl+C before that Enter drops it. The command has finished once
the shell has held the foreground (`tcgetpgrp` equal to the shell's session) for a second after that Enter and
no process group that held the foreground since still exists, so a job suspended with Ctrl+Z or sent to the
background has not. The reader looks every 0.2 seconds, so a job suspended sooner than that after it starts
counts as finished. Then, or when the terminal ends first, `tasks.notify` leaves one Terminal notice in the
task inbox, on its own thread so the reader keeps draining output while it waits for the project lock. It
reaches the owner at its next checkpoint as any queued message does and wakes a blocked owner unless it is
stopped or faulted; a task no longer running or blocked, or on a later attempt than the one current when the
page named the command, gets none. It is no conversation entry and grants nothing. When naming the command
fails, the page still types it and asks the operator to reply in chat instead. altd does not see the command's exit status, so the notice sends the
owner to `alt task terminal` to verify; a shell builtin that waits for input without a child looks finished.
Project terminals send no notice. Input and output are never written anywhere; the notice holds only the command text. The task's `events.jsonl`, or the project's
`events.log` for a project terminal, records only `terminal` rows for `opened` and `closed`, with the
folder, and the reason and exit code on close.

A task's running owner reads its task terminal's output with `alt task terminal` (`POST /api/task/terminal`
with the task, project and attempt). The reply is the replay buffer as plain text (escape sequences removed,
each line as its last carriage return left it), whether earlier output was dropped, and the terminal's state:
`running`, `exited` with its exit code and reason, or `none`. altd answers only the task's current attempt
while it runs, and only when the client end of the connection is held by a process in that owner's worker job
(`terminal.owner_connection`, from the same process and socket facts as the agent check), so another agent that
holds this machine's key cannot read it. There is no owner path to input, resize, close or stream. The last
ended task terminal's output stays readable in altd's memory until a new terminal opens for the task, the task
finishes (the tick's sweep forgets it) or altd stops; nothing is written to disk. Project terminals have no
reader. The task terminal says "This task's owner can read this terminal's output." Anything the terminal
prints can reach the owner, its session record and its provider, where it stays after Altitude forgets it.
A password typed at a prompt that does not echo, such as `sudo`'s, is not in the output.

Every terminal request is refused unless it comes from a paired browser on Altitude's own page and
not from Altitude itself. Its reads and streams pass the same-page rule every POST passes (see
[Responsibilities](#responsibilities)), and its POST bodies must be `application/json`. This stops
another site open in the operator's browser from typing into a shell. Then `terminal.agent_connection`,
asked once per connection since a connection keeps its client socket, finds the client end of the TCP
connection in
`/proc/net/tcp{,6}` on Linux, or among this user's processes' sockets on macOS, in both address families
since an IPv6 socket can reach an IPv4 address. A
client whose row is missing is refused when its address belongs to this host (it can be bound). A
local client is allowed only when a process outside Altitude visibly holds
that socket and none of altd, anything altd started (a Claude L3 turn runs as altd's child in altd's
own cgroup) or any process in an `altitude*.service` unit (workers, reviews, machine commands, terminal
shells; on macOS the job coalition recorded under that unit name) does.
A holder whose descriptors or unit cannot be read identifies nothing, so an agent process that hides
its descriptors is refused. A client on another host is the operator's browser. The Vite dev server
does not proxy any path altd could route to the terminal (`terminalRequest` reads the raw path as
altd does, and also its decoded, dot-resolved form; a path with `;` parameters is not proxied), because altd would see the proxy as the client. This stops a worker from
using the terminal to leave its sandbox and bypass the operator-grant flow. The check has known
limits. A process an agent starts outside those units, through the user service manager or a
scheduler, is not recognized. A forwarder on this machine in front of altd (an SSH tunnel, a reverse
proxy, a container's published port) holds the socket altd sees, so a worker connecting through it
looks like the operator's browser, so the operator keeps the terminal off while one serves Altitude.
On Linux, Claude L2 workers have no OS sandbox, so they can already change the
operator's files directly. Every paired browser can use the terminal once it is
on. That is the same trust as its other
operator controls, and the Settings copy says so. That includes `sudo`: a paired browser whose user knows
the operator's password, or that uses a terminal while `sudo` still remembers an authentication there, can act
as root. A password travels from the browser over TLS to altd and into the pseudo-terminal like any other input;
Altitude stores nothing typed, and the operator's own shell keeps its history as it does in any terminal.

## Faults

A system fault is project-scoped and two-tier. Tier one is code: a temporary capacity stop is retried
with backoff; a usage-limit stop starts a fresh attempt on an eligible configured alternative from
the task's `progress.md`, or parks the task when no alternative is eligible or it is explicitly pinned.
The engine seam reports allowance scope and an optional reset. A named model allowance excludes only
that model, never unrelated models or engines. An unknown reset creates no resume timer or global
timed hold; the existing routing observation expires after thirty minutes without claiming a reset.
A provider-reported reset schedules resumption. Fresh attempts retain the existing worktree, including
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
task, project, logs and conversations never supply public content.

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
running and blocked workers survive activation and are adopted afterwards. Each worker unit and the
service retain `KillMode=control-group` on Linux, and on macOS Stop takes every member of the job's
coalition, so stopping a worker takes all its descendants. An exited or
missing worker on a running task requires a report written since its latest launch or resume
or an explicit completion; without one it blocks with a system fault and incident. An explicit
question block remains waiting after worker exit and needs no completion report. Dispatch continues
while activation is pending. When dispatch/resume claims, L3 turns, adversarial reviews, report
verification and bounded validation runs (including evidence recording and cleanup) are quiet, altd runs
the one guarded restart script as a transient user unit outside its own cgroup. It installs the
pnpm-locked dependencies, builds and validates the latest bundle in staging, rechecks the checkout and
quiet point, swaps the bundle, restarts safely, and verifies both API and UI; verification failure
restores the prior bundle and verifies recovery API/UI health from another new process, retaining
both the activation failure and recovery outcome. With no prior bundle, recovery is unavailable.
On macOS, service Stop/restart confirms coalition cleanup and waits up to 45 seconds for launchd
to remove the label before completing Stop or bootstrapping again; unreadable removal fails explicitly.
Independent worker jobs retain their own coalitions. Monitor's Restart button runs the same path sooner by hand: it appears
at that narrow quiet point, even while workers run, disappears once restart is under way, and
the notice leaves when the new process answers with nothing pending. A restart unit that fails files a
system fault naming its reason at once and, while its request is still pending, marks the record `failed`
with that `error`, so the hold lifts; a restart that has not happened ten minutes after it was requested (the unit died without
reporting) is the same fault. Dispatch, resume and
L3 turns wait only from the unit request until the replacement daemon is ready; report verification
also waits, leaving reports durable for the next tick. A shared activity lock fences these
operations and validation against the exclusive restart request, including admission races. Ordinary source changes never
start, stop, mask, unmask, or restart the service; a lifecycle action by hand needs separate
authorization and post-change health verification.

## Interfaces and storage

### Conversation-audit pilot

`audit.py` owns the finite pilot for the project named in `config.py`. The operator explicitly starts
it; installation/registration do not enable it. `audit.json` stores reviewer, expiry, attempts,
reviewed exchange identities and finding-supply receipts. Sessions wait twelve hours and four new
exchanges mature for thirty minutes. Failed/unanswered exchanges also mature, with incomplete and
in-flight evidence. Only human chat counts. Seven days or fourteen attempts ends the pilot.

The tick's keyed background executor owns packet construction and provider IO outside ordinary L3
locks and the timer thread. A short project lock reserves attempts before launch. Failures pause
the pilot; interrupted execution becomes uncertain after the deadline without automatic replay.
Stop prevents later sessions; an already claimed session can finish within its timeout. Project
activity holds preserve the existing detach boundary. None of these states pauses ordinary work.

Sampling reads at most 2 MiB of the original chat tail, selecting at most twenty unseen exchanges
within forty-eight hours, never before September 22, 2026 07:00 UTC. Byte offsets, turn IDs and dates
identify original rows. Complete fitting exchanges take precedence over oversized ones; omissions
and truncation remain explicit. Later context and four directly referenced task records carry
current ownership, decisions and canonical messages, including removal markers. The complete starting
prompt stays within 64 KiB. Sparse/overflowing evidence can expire without review.

`engines.conversation_review` uses a fresh native session, the ordinary coordinator tools/permissions,
an explicit model and the engine's ordinary session timeout, enforced for the process tree. The
attempt records that timeout so interrupted-run detection respects it. It does not resume or publish
an L3 turn. The review assignment leaves implementation, publication and operational decisions with
L3/L2. Tool reads and reasoning add usage within that deadline; byte/time bounds are not dollar caps.
Available native model, usage and API-equivalent cost are recorded; missing values remain unknown.
Provider options stay in the engine seam, with no fallback when the pinned reviewer is unavailable.

Private `audits/<id>/packet.json` and `result.json` retain each attempt. A bounded JSON response carries
at most three classified findings, expected/observed behavior, impact, uncertainty, corrections,
ownership and an original selected source. Only unresolved candidates enter the next project chat
turn, without another L3 queue event. Category/source fingerprints suppress repeated supply. The L3
turn identity proves supply, not handling; interrupted supply remains inspectable without automatic
replay. L3 verifies current tasks/issues and later evidence before action. No issue writer, task
dispatcher or instruction editor belongs to the reviewer. Unavailable optional audit evidence never
blocks ordinary conversation. Model-quality evaluation is separately authorized and reported;
deterministic fixtures establish application behavior only.

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
resume or recovery replaces it, and asks `engines.py` for normalized local observations. The daemon
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
Traces are retained only on failure. Outputs stay under ignored `web/ui-artifacts/`. The self-hosted
workflow retains logs and candidate identity through the runner's local evidence exporter. Failed
runs also retain the self-contained HTML report and attachments; passing runs keep small receipts.
GitHub artifact uploads, duplicate raw results and caches are excluded. A passing required check
with its console log is sufficient delivery evidence; owners retrieve a failed report only for
diagnosis or on a reviewer's request; L3 coordinates cleanup of unneeded exports when the runner's
budget refuses admission. See the
[retention and retrieval contract](DEVELOPMENT.md#ci-and-candidate-identity).
The committed design tree holds maintained boards and their spec; review galleries and routine
renderings are not source artifacts. Curated documentation illustrations retain a maintained source.
The required PR job runs every suite phase and preserves candidate identity. Bundled Chromium
runs headlessly with a temporary profile and its browser sandbox disabled inside the
worker sandbox. [Development and checks](DEVELOPMENT.md) documents installation, commands,
timings and candidate identity; [operations](OPERATIONS.md) covers service activation and mobile access.

That sandbox-disabled launch belongs only to Altitude's fictional local UI harness. The shared worker
launcher tells fresh and resumed owners to run verification that needs the browser's own sandbox
through `alt task validate`, where Playwright's Chromium keeps it, and to fault-block if the runner is
unavailable or the browser refuses its sandbox there. `make browser-sandbox` records the protections
Chromium keeps. See [browser verification](DEVELOPMENT.md#browser-verification).

[Release checkpoints](RELEASING.md) select an exact validated source SHA for an explicitly
published private-preview version and release notes. Pushing the approved tag runs the release
workflow, which checks that commit's main `check` run, builds and attests the archive, `install.py`
and the generated `install.sh`, and publishes them as the GitHub release that the one-command install
fetches. They add no runtime lifecycle state and do not gate automatic activation of merged changes. The UI and testing rules remain in the
project instructions file, which both worker personas direct the task owner to read first.

### Web delivery

altd serves the built app from `web/dist`. `index.html` is uncached, so each open learns the current
build; Vite names every file under `/assets/` by its content hash, and those files are cached as
immutable. A warm open therefore fetches only the page and its API reads, and a cold open, the first
on a device or the first after an update changes the app script, downloads the script and styles
before anything shows. Hashed scripts, styles, WebAssembly, word lists, SVG and JSON travel
gzip-encoded (`Vary: Accept-Encoding`) to browsers that accept gzip; altd compresses each once per
process. That cuts the cold download to about a third: in the fictional walkthrough on simulated
slow 4G the page appears in about 1.7 seconds instead of 4.4. The punctuation model's packed weights
barely compress and travel as stored. Opening requests no dictation asset; `web/e2e/first-open.pw.ts`
walks cold, warm and slow opens with dictation assets failing.

A page keeps running the build it opened with. Activation replaces `web/dist`, so a file that page
loads later, such as the terminal's code (the app's one lazy module), is gone and altd answers 404.
The terminal view then says whether the served page names another app script, meaning Altitude was
updated, or Altitude could not be reached (no answer within five seconds counts as unreachable), and offers
Reload. Only a failed load of that code shows this; an error inside a loaded terminal reaches the route's error page. It does not reload by itself, and says
that a reload clears unsent typed text, which lives in the page's memory. The browser keeps a failed
module import for the page's lifetime, so only a reload recovers. The terminal's code is one script
that its import alone requests: its stylesheet (about 1 KB compressed) travels with the app's, because
Vite also preloads a lazy module that has a stylesheet, and WebKit keeps a failed preload across a
reload, which would leave Reload broken. Earlier builds are not retained.

Every API reply is `no-store`. The app keeps what it has read in memory only (TanStack Query, per
project and task), re-reads a view on each open and on every change event, and drops it all on
re-pairing; nothing is prefetched or stored on the device. On a slow link the wait is therefore the
download of the view itself. The task view carries the task record, its decisions, review, messages,
documents and report, the newest 20 events, and the repository link for pull request links, so the
task page reads no project view. The record's review, decision and message-delivery ledgers, which
the view already presents, stay on the server. The overview, project, task and live-session views travel gzip-encoded
(`Vary: Accept-Encoding`) to browsers that accept it, four to five times smaller; a running task's
view and live session are re-read every two seconds. Replies that can carry a pairing code or key
(access, pairing, devices, alerts, terminal) always travel as stored. Compression lets someone who
watches the encrypted traffic, and can repeatedly plant chosen text in the same reply, infer secret
text in it from reply sizes; the operator accepted that for these four views on 2026-09-28.

### Conversations and navigation

The HTML links the approved Climb SVG favicon, a multi-size ICO fallback, the 180px Apple touch
icon and a web app manifest. The manifest defines the root identity/start URL/scope, standalone
display and ordinary 192/512px plus maskable 512px icons. Public metadata and icons are served
uncached by the existing static handler; the alert worker has no fetch handler or offline app
cache. Native icon copies can outlive a page reload; [operations](OPERATIONS.md#refreshing-home-screen-and-bookmark-icons)
describes refreshing shortcuts and installed apps.

The Python server owns state transitions and JSON APIs. The React app is one shell around four
pages, specified in `design/wireframes/SPEC.md`: Needs you at `/` (every decision across projects as
compact cards in one column, answered through `POST /api/decide`), the project page at
`/projects/<name>` (the §3.2 header with its status line and overflow menu, the L3 conversation, and
the work panel), the task conversation (including redirects from `/projects/<name>/decisions/<slug>`), and
Monitor. `/projects` and `/chat/<name>` redirect to the project
page, and with no managed project every project route shows First run: the steps name, what the
agents need, incident reports and projects, held in `?step=`, each skippable and a Settings row
afterwards. What the agents need reads `GET /api/prerequisites`, `installation.prerequisites()`: the
doctor's GitHub CLI check, each engine's local sign-in status through the engine seam
(`engines.sign_in`) and Git, as met, unmet with the terminal command to run (the install command from
`platform.INSTALL` or `engines.INSTALL` for a missing tool, else its sign-in command), or optional once one
engine is signed in. The projects step lists the folders under the projects folder
(`config.project_roots()`: the machine setting `projects_folder`, else `ALTITUDE_ROOTS`), changes
that folder in place and starts L3 for one or all of them through `POST /api/project/add`, in order,
staying up until L3's first reply or the error row that stands in for it. At 1024px and wider the rail is 260px and the work
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
projects folder First run names and the operator's configured name.

First run's folder browser reads `GET /api/folders?path=<absolute path>` (no path means the home
folder), one folder per request and only when the operator opens it; nothing is scanned, indexed or
kept. `server.folders` resolves the path and refuses one outside the home folder or through a hidden
(dot) folder, then returns `path`, `parts` (relative to home) and `readable`, plus one row per visible
subfolder whose resolved target stays inside home: `name`, `path`, the registered `project` for that
folder and whether it holds `.git`. It never returns file names, sizes or contents, and an unreadable
folder returns `readable: false`. The browser adds through the existing `POST /api/project/add`;
**Type a path instead** covers folders outside home. `POST /api/projects-folder` applies the
operator's choice through the machine settings request, like `alt machine set --projects-folder`;
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

Decision alerts (issue #221) ride that stream. `web/src/data/alerts.tsx` holds the per-device switch
under `altitude.alerts`, the permission states, and the keys already alerted under
`altitude.alerts.seen`; the shell watches the overview queue on every page. A newly published operator
question is shown through `web/public/sw.js`, the one service worker, whose registration Android
requires for a notification and whose click handler focuses the open app and routes it to the
decision. Content is the project and task name only. A decision visible in Needs you or its owning
task is recorded without an alert, and the recorded keys make refresh, reconnection and polling
repeat none. A key names the waiting decision, not its revision, so a block and the escalation that
republishes it alert once; a decision that is answered leaves the queue, and an ask that returns
alerts again. A tab with alerts on keeps its change stream while hidden; every other hidden tab still
closes it.

A device is also woken while Altitude is closed. `altitude/push.py` keeps the VAPID signing key under
`~/.altitude/push/` and the subscribed endpoints in `~/.altitude/push.json`, both written only by the
daemon; the switch registers its endpoint through `/api/alerts/subscription` and reads the public key
from `/api/alerts`. Each tick, a decision key that is newly waiting sends one empty, signed POST per
device: no payload, so no encryption library and no word of the decision leaves the machine. The
worker's `push` handler reads `/api/overview` and names the project and task, and shows that a
decision is waiting when the device cannot reach Altitude; a page already on screen alerts for itself,
so the worker stays quiet. An endpoint the service reports gone is dropped, a refusal is logged and
kept, and a machine without OpenSSL or outbound reach simply has no push, which the switch states.
EventSource retries a dropped connection after the stream's 3-second `retry`; a refused stream, such
as during daemon activation, reconnects after 5 seconds. A hidden tab closes its stream and reopens it,
refetching, when shown, so background tabs hold none of the browser's connections to altd. Ordinary 20-second polling, the 2-second
reads of running tasks and active conversations, and the conversation's own history reads continue
independently, and a comment line every 15 seconds keeps an idle stream open. `POST /api/l3/start` runs the start
turn for a managed project whose L3 never ran, from the header's Start L3. The conversation is the only
way to create a task from the web: the L3 turn creates it through `alt task new`, and altd records
the slug on that turn's assistant row (`tasks: [slug]`), which the conversation renders as a task card
under the reply. Engine selection is in the project's Settings page and the desktop composer's pill;
a non-Auto pin stays named in the phone header. It pins the project's L3 to one configured engine, named as
`engines[]` reports it, until set back to Auto; Auto uses project preference tiers, weekly headroom
within ties and the session continuity rule described above. A chat turn belongs to L3,
not to the page that started it: when the page leaves mid-stream, the turn finishes and its answer
lands in the history. The conversation component is keyed by project, like its query cache:
switching projects clears the pending bubble and local stream. Unsent text lives in the existing
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
assistant or error row wins over a raced active snapshot.

The conversation reads `/api/chat/<project>?limit=60`. The limit counts human chat rows and system
rows (server-triggered turns and FYIs) separately, so a burst of system events never pushes the latest
operator messages out of view; `chat.jsonl` keeps every row and `alt l3 search` reaches older ones.
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
keeps its arrow, the header names the active work, and the message shows as a muted queued row with
its run order, **Send now** and **Remove** until
its turn starts, when the row becomes the turn's bubble and typing indicator. Queue claim writes the
user history row and publishes the active record under the same lifecycle guard used by the API's
history/queue/active snapshot. Routing precedes claim; failed history admission restores the waiting
queue. The claimed turn reuses its admission and route choice, so the text stays visible through
handoff and is delivered once. A control takes the operator's chat back off the queue only while it
waits. Server-triggered work is also visible in its FIFO position but is not editable. The queue is a
file in the project directory, so a reload, another device and a restart all see the same pending
messages. Each turn drains it at its own boundary rather than at the next tick: consecutive text chat
messages for the same conversation fold into one turn in arrival order, each on its own line, while
image-bearing and server-triggered messages keep their own turn, and nothing runs while a turn holds the project's L3 lock.

**Send now** promotes only the selected operator row and gives it its own next turn. Other queued
rows keep their relative order and ordinary folding. Admission, removal and claim share the queue's
writer lock; retries reuse the selected row or its history receipt. An accepted Send now row remains
removable until claim, including when no engine is available after admission. Removal does not undo
an interruption already requested. The daemon requests interruption
of the captured active chat turn through the engine seam, retains partial output and session identity,
and records **Interrupted for a queued message**. Its turn lock remains held until the engine job and
its descendants have ended. A system turn finishes at its existing boundary to preserve notification,
CI and report delivery; the promoted row says **Runs next after system work**. System queue rows
cannot be promoted or removed. No available engine, an active chat still starting, or a launch pause
explains why delivery cannot start. Pending priority is durable, and a queued row still neither holds nor is lost by a quiet-point
restart. The browser requests this action by message ID; it never interrupts an engine itself.

In the task chat, the same control uses the existing durable Stop and resume operation, fences hook
pickup before interruption, and delivers only the selected inbox row through the usual resume claim
and handoff receipt. Like Stop, it cancels attached reviews and does not undo completed external
effects. Confirmed termination persists a due continuation; later launch holds show waiting to resume,
with Stop and Reject still available. A new question or fault supersedes the wake. An explicit Stop,
question wait, fault recovery or unavailable saved-session engine explains the required continuation,
answer or recovery instead of interrupting. Operator grants and merge holds keep their existing rules.

The project conversation and the task conversation use one
composer component, `web/src/components/Composer.tsx`, with no page-specific props.
The page owns its draft and submit function. A valid turn, terminal turn ID, queue receipt, or
saved task message establishes acceptance; HTTP stream headers alone do not. After acceptance,
a broken response stream resolves to the existing history refresh without restoring the draft or
inventing an assistant failure. Each local stream callback belongs to its own send, so an older
stream cannot alter a later queue request. A canonical assistant or error row for that turn replaces
its local stream even while the response remains open. A queue receipt establishes acceptance;
the source conversation refreshes its canonical snapshot even after navigation, rather than adding
the receipt back to the queue. The current send's accepted local preview remains until a successful
read or an identity-matched queue/history row owns its display. Local cache edits do not count as
reads. Navigation or a subsequent send replaces that local preview; accepted messages reconstruct
from the server on the next successful read. Failed reads show the conversation read error, whose
Retry only reads, including after navigation. They do not restore accepted text for sending again.
Explicit HTTP refusals restore the submitted text with "Not sent. Retry."; transport, malformed
receipt and server failures without acceptance evidence restore it with "Could not confirm delivery.
Check the conversation before sending again." and no send Retry. Recovery retains newly typed text
after the submitted text on a new line. Combined failed drafts remain unconfirmed if any send lacks a
receipt. No text matching or automatic resend infers delivery.
Task sends carry a UUID `request_id`, retained as the saved message ID. The conversation renders
the pending preview only while that ID is absent from polled messages, so the saved row owns its
display even before the POST completes. A late receipt preserves that row's canonical position and
delivery state, including explicit removal. Text recovery still follows the send response.
The composer keeps only submitted-text recovery in browser-tab `sessionStorage`, keyed by stable
project or project/task identity. Live request callbacks outlast component unmount and restore a
failure only to their original conversation. A receipt removes its request's recovery copy before
the answer finishes; older reads are cancelled before a late receipt updates the query cache.
A reload without a receipt restores pending text as unconfirmed, even if history now contains a
similar message. The operator checks history before choosing to send. Recovered text and later typed
or dictated edits remain recoverable while unconfirmed; editing a refused recovery returns it to an
ordinary draft. Sending explicitly replaces the recovery with the submitted request. Ordinary unsent
project text survives route remounts in client memory; it is not synchronized or stored across reloads.
Recovered text transfers back to recovery storage on unmount so returning restores it only once.
Failure to save the initial recovery copy leaves the text
unsent with an inline error. No recovery record initiates delivery or provides a second server queue.
A failed recovery update keeps the latest text in the current browser document and displays a
keep-tab-open warning; it cannot promise that unwritten edits survive reload. Once storage accepts
updates again, returning to the conversation saves that recovery and clears the warning. Unread
browser evidence is not overwritten by a failed read.
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
from a blob URL) that averages the microphone down to 16 kHz 16-bit samples. The composer shows
"Starting voice…" until the first samples arrive, then Listening. One request is in flight at a time,
carrying what gathered since the last answer (normally half a second, at most ten seconds); a request
lost on the network is repeated with the same number up to three times. Each answer replaces the words
after the typed draft. Stop releases the microphone and sends the rest as the final chunk; Transcribing
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
scripted recognizer tests establish ordering and text isolation only. The microphone
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
name of "Send" ("Queue" while busy). Its states are the design spec's §3.6 table (idle, typing, sending at 60% with a progress ring that settles in place on acknowledgement, busy queueing, listening
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
Image admission freezes that submission's controls until acceptance or confirmed refusal; an uncertain
response retains a pending bubble until polling observes its saved message and retries with the same identity while mounted. Submitted captions
use the existing text recovery; image bytes are not persisted in browser storage. Acceptance clears selection
and releases the composer before the agent finishes. Private thumbnails belong to the original saved
message. The shell owns one modal viewer per page (`ImageViewerHost`): it holds its own copy of the
opened image's bytes, so it outlives the queued row that opened it when history admission replaces
that row, and closes when the route changes. Image interaction states and boundaries are specified in
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
**Planned · waits for <reason>** with the muted queue dot; the reason wraps on phone and desktop.
Releasing it changes the same row to **Queued** with its ordinary dispatch hold, then **Running**
only when launched. Planned tasks stay in Current without adding attention or a separate panel.
A task blocked waiting on L3 reads "Waits for L3" with the running dot, and the rail's project dot
counts it as running (`counts.waits_l3`);
only a decision in the queue turns either dot amber. An owner/daemon park without a question, fault
or operator stop reads "Paused" with the idle dot. Stop evidence, not the block recorder, identifies
"Stopped". Queue and restart inventory labels share `tasks.block_status`; an unset wait owner is
a pause, never an inferred operator wait or attention item. The work panel (spec §3.7) reads the project's
tasks and the overview queue filtered to the project. **Current** contains every unfinished task
once as a compact status row; tasks done or rejected in the last seven days fold under **Done this
week**. A row with an open operator question shows **Needs you** and its question count alongside
independent execution or fault status, and opens the owning question's conversation anchor. Other
rows open the ordinary task conversation. Questions and answer controls live in global Needs you
and the owning chat. Partial or final answers update the same row; only completion or rejection
moves it out of Current. Saving an answer does not assert that the worker resumed.

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
preview or decision state, and that L3 transport restriction does not apply to the L2 CLI path.

Selected choices and custom responses send the question/revision (and group revision when applicable)
to `POST /api/decide`, which validates and saves one ordinary operator message. Ordinary chat uses
`POST /api/l2/message`. Both remain messages until the owner interprets a clear answer and records
`alt task resolve` against the original source message. Both use the durable inbox and
daemon wake mechanism described in [session lifecycle](SESSION_LIFECYCLE.md#messages-resume-and-stop).
Opening a preview performs reads only; neither viewing, a follow-up nor a resume accepts a proposal.

A dilemma (spec §3.8–3.10) lives in the owning task conversation. The task record's `questions`
contains versioned question text, zero to three explicit options and a recommendation key,
source/audience, stable ID and message anchor, and an open or resolved status. A question group
contains up to three open members plus closed history, a group revision, and one stable discussion
anchor. `task_view` projects `question_group` with member records, `question` as the first
open member (or latest receipt), and individual revision history;
`GET /api/overview` and the project view project the operator's turn: unresolved operator questions
asked since the operator last wrote to the task (`handed_back`), plus one `review` row for a held
delivery whose owner stopped in `blocked` or `reported` (#419). A held PR that Altitude has observed
closed without merging asks for no review (#575): an L2 `block` reads the held PR's state from the checkout origin's repository, and a
`CLOSED` result, like a verified `alt pr close`, appends one `pr-closed` task event. The delivery,
`prs` history and merge hold stay recorded. A later `delivery` or `pr-adopted` event for that PR, a
`pr-reopened` event from an owner block that reads it open again, or a new PR asks again. An
unreadable state leaves the recorded review shown and says so on stderr. Any operator message or
quick answer hands the task back; the next park by the L2 or L3 without a queued message returns the turn and
marks still-open questions `asked_again`. `tasks.block_status` gives the CLI list/status, queue and
restart notice one wait label (`<operator>'s turn · …`, `L2 replying to <operator>`,
`paused · fault …`, `stopped by <operator>`, `waiting on L3`, `paused`). Question
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
escalation away, and `waiting_on` names the operator only while one of the group's open members is
theirs. A block that publishes or revises
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

`POST /api/decide` takes `{project, slug, question_id, revision, option_key}` or an exclusive `text`
response. Omitting the key explicitly selects the recorded recommendation. A group sends
`{project, slug, group_id, group_revision, answers: [{question_id, revision, option_key|text}]}`;
each member supplies exactly one response kind. The existing project lock validates the entire batch
before saving one attributed operator message and durable delivery receipt. It records no decision.
Each submitted question projects `response: {text, at, message_id}` and stays semantically open until
the owner resolves it. Sent members leave Needs you; their **Sent to L2** receipts remain in the
owning group, alongside members still awaiting input. An identical retry reuses its receipt and repairs
interrupted delivery. Stale or conflicting submissions fail together. The response names the original
question revisions, so the owner cannot cite it to approve replacement wording. **Sent to L2** means
saved for delivery; **Work resumed** requires observed running state. A queued owner stays explicit.

Typed replies use `POST /api/l2/message`, optionally naming the viewed question/revision or
`group_id`/`group_revision` as context. The saved message retains the viewed member references so
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

### Voice backend

Voice transcription sits behind the capability seam as one machine setting, `voice`, in the private
settings file: `host` or `browser`. Without a saved choice the backend is `host` when this release's
speech manifest names a runtime this computer can run, and `browser` elsewhere (macOS for now).
`GET /api/voice` reports the backend, an opaque selection identity over the backend and, for `host`,
the pinned runtime, and `host`, where host voice stands on this computer. `alt machine set --voice`
requests a change through the same durable request that carries `wip`; `--unset-voice` returns to the
default.

`/settings` shows a compact Voice input summary under This machine and read-only connection details
(the browser's current origin/HTTPS state and configured operator). `/settings/voice` holds the two
choices, This computer and Browser recognition, each saved immediately. Project three-dot menus and the
desktop operator row open Settings. `POST /api/voice` saves `{backend, selection}` through the same
durable request/apply path as the CLI, with no restart or provider probe; a selection that no longer
matches the saved one is refused (409) and asks the person to reload settings. Failed saves leave the
persisted choice unchanged. Fresh settings reads update the form as well as the composer's selection.
First run offers the same choice as its Voice step (see the design spec §3.12); a setup failure there
never blocks onboarding.

Altitude sends no recording to a speech service of its own configuration. On start,
`dispatch.forget_speech_service()` deletes a stored speech-service setting and any pending or applied
`voice-request.json` naming one, with its key, under the projects lock, and appends one event without
URL or key; a machine with nothing stale is left untouched, so the step is idempotent. The machine then
uses the default. Phone access uses an explicitly configured private HTTPS address whose certificate
covers that address. Safari can use the microphone after the CA is trusted on the phone; typing
remains available without voice.

#### Host voice

With `host`, this computer transcribes: `altitude/speech.py` owns a pinned speech runtime and one
speech worker, and the page streams samples to it. The model is NVIDIA Parakeet TDT 0.6B v2 (English,
CC-BY-4.0) as the int8 ONNX export `istupakov/parakeet-tdt-0.6b-v2-onnx` at a pinned revision, run on
the CPU by the MIT `onnx-asr` decoder with onnxruntime and numpy. It always punctuates and capitalizes.

`platform.speech_runtime()` names the runtime this host and interpreter can run (Linux x86_64, glibc
2.28 or newer, CPython 3.12 or 3.13). Anything else, including macOS until native evidence is recorded,
reads as `unavailable` with its reason. `speech.status()` is `unavailable`, `absent`, `setting-up`
(with bytes done), `ready`, `failed` (with the reason) or `outdated`, with the download size.

Setup (`alt voice setup`, or Settings through `POST /api/voice/host` with `setup`) downloads the
manifest in `speech.py`: five model files from Hugging Face and three wheels from PyPI, about 698 MB.
Each file streams into `~/.altitude/speech/.staging` with a size cap and must match its pinned
SHA-256. The wheels are unpacked with the release installer's path rules (no absolute, parent,
backslash or link entries, a total size cap) into `site/`, not installed with pip. A completion record
naming the manifest digest is written last and the folder is moved to `runtime-<digest>` in one
rename; only a runtime whose record matches this release's manifest is used, and a changed manifest
reads as `outdated`. An exclusive file lock admits one setup across the daemon and the CLI. Cancel or
any failure deletes staging; a failure, not a cancel, is remembered for Settings until the next setup.
`remove` refuses during setup, stops the worker and deletes every runtime; the daemon also stops its
worker within a second when its runtime disappears under it (`alt voice remove`).

The worker (`altitude/speech_worker.py`) runs as `python -I` with the daemon's interpreter and a
minimal environment, imports only the runtime's unpacked wheels, and reads one JSON request per line:
`open`, `audio` (base64 samples), `finish` and `close`. It answers `ready` once the model is loaded,
then each changed recording's text so far, finishing recordings first. It commits text at pauses once
eight seconds are pending: the audio up to the middle of the last quiet run (quiet is relative to the
recording's own level) is transcribed once and kept; without a pause by twelve seconds, the words that
started at least a second before the end are kept, except the last. No read covers much more than
twelve seconds of audio. It exits on stdin EOF, so it ends with the daemon.

`speech.Host` supervises it. The first recording starts the worker, after checking that about 2.5 GB
of memory is available; every recording shares it, and it exits after fifteen minutes unused. Requests
reach it through a writer thread, so a worker that is loading or hung never holds up a request. A
watchdog kills it when it takes more than 30 seconds to load, then 10 seconds to answer new audio or
15 seconds to finish; every unfinished recording then fails with a plain reason, and the next recording
starts a fresh worker. Its stderr goes to `~/.altitude/speech/worker.log`; altd's log records starts,
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

Pending task designs use captured screenshots and text, bound to the existing question revision.
The current L2 supplies an explicit selection through `alt task block --design-file`, optionally
with a `--questions-file` naming exactly one new or existing open member. Other members stay unchanged;
ambiguous group attachment is refused. Ignored and untracked files need no Git changes. The task and
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
The conversation's offscreen-question jump reaches that question, where its preview opens.
Within a group it follows an open member with a preview, then another open member, including after
partial answers. It uses that question's exact URL, never an earlier proposal's capture. Work reaches the same
question through its task row. Preview headings use the captured title to distinguish a proposal
from an implementation review. Opening a separate tab preserves the originating route and draft.
`/projects/<project>/tasks/<slug>/design/<question>/<revision>` opens in a browser tab with the saved
screenshots, full-size image links, explanation and **Back to question**, which returns to
the exact captured question revision. Both **Back to question** and **Open current question**
replace the preview's history entry, so the task's Back control opens the owning project conversation.
Browser Back/Forward follows the remaining history; the originating tab and its draft stay intact.
The preview reads
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
at their recorded message anchor), a held review card when one waits, no open operator question links its PR and the PR is not already
approved since its hold, and the composer
while the task is running, blocked, reported with open-PR owner evidence, or queued before its first
dispatch or with an existing question. Waiting on L3 stays a
concise status with its complete reason in details; a fault retains a visible cause in red with
"L3 has been told". One replacing two-line public update sits at the end of the conversation's
scrolling column and expands on request. It appears only while both its public words and recorded
activity are less than 60 seconds old; missing, untimed and unavailable output leaves no preview.
Tool output alone does not keep stale prose visible. Updates and removal preserve an older-message
reader's position. Stop is one click in the task header at both viewports, serving Conversation and
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
toggle for the complete redacted records, the task's other operational events among them. A queued
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
details never resolves a question or releases a merge hold. A done or rejected task is
read-only with the composer and activity preview gone. Phone swipes left to Live session and right to
Conversation, without wrapping: both views sit on one track that follows the finger, settles into the
switch past half the width or a fling, springs back otherwise and resists past either end; reduced
motion switches instantly. Both phone views stay mounted and laid out, the inactive one invisible with
its transcript polling paused. Deliberate horizontal gestures exclude browser edges, selection,
controls, the composer, recording, dialogs and horizontally scrollable content; vertical scrolling
stays native. Tabs remain the accessible direct navigation. View switches preserve draft text,
selection, images, conversation position and live reading state without reopening the keyboard;
local history and deep links retain their navigation contract.
Scrolling up in Live session pauses following; Follow catches up to the newest output. Switching
views cancels unsent dictation and releases the microphone; an explicit voice Send completes for its
original conversation while hidden, without refocusing the composer.
The read-only activity projection uses only the selected worker generation, existing redaction and
public output. Provider parsing stays in the engine seam; no summarizer, extra model instructions,
new archive or copied conversation replies supply the preview. Conversation and Live session derive one
activity cue from its recorded output time: a pulsing dot within 60 seconds of output. Live session
keeps a still dot and "No new activity for …" after that, and no pulse when activity is unavailable
or the task is not running; Conversation hides the preview instead.

Runtime files live under `ALTITUDE_HOME`; a task is a directory a person can read. Source-controlled
personas, schemas, templates, and hooks describe current behaviour: `hooks/` holds the Git hooks
that `git_policy` installs into every managed repository and the Claude inbox hook.
[AGENTS.md](../AGENTS.md) holds project and review rules; these current documentation pages retain the system's operating decisions and rationale. Git history preserves completed migrations.
