# Engine and session lifecycle

Altitude has one logical owner per task and replaceable physical workers. These are different
identities on purpose:

| Field | Meaning | Changes when |
| --- | --- | --- |
| `attempt` | one L2 attempt, counted from 1 | the task is dispatched again from the queue |
| `l2_engine` | provider for that attempt | only on a fresh attempt, never a transparent resume |
| `session_id` | provider conversation/thread | Codex keeps it across turns; Claude may return a replacement on resume |
| `agent_id` | current unit-owned CLI worker | every physical replacement |
| `routing` | one sentence saying why this engine was chosen | written once with fresh dispatch |
| `launch_model` | model override passed at launch, or null for the CLI default | fresh dispatch |
| `engine_model` | model observed from the provider's turn | each worker/turn records its selection |
| `engine_reasoning_effort` | observed effort when supplied by the provider | with the model observation |

For first-run configuration and explicit single-engine pins, see [setup](SETUP.md). The
[engine integration boundary](ARCHITECTURE.md#engine-integration-boundary) separates the supported
launchers from the broader extensibility direction; this page describes their current lifecycle.
[Operations](OPERATIONS.md) covers service activation, inspection and mobile voice checks.

The L2 learns its attempt from `ALTITUDE_ATTEMPT`. Replies, completion, and landing name it, so
a worker of an earlier attempt cannot act for the current one. `ALTITUDE_SESSION_KEY` (`project--slug-attempt`)
keys the edit-count telemetry across worker replacements.

## Fresh dispatch

```text
queued task
  ├─ self-deploy checkout fast-forwarded to origin/main
  ├─ WIP and Git provenance gates
  ├─ weekly-first provider decision (or explicit task/project pin)
  ├─ persist l2_engine + model + routing reason
  ├─ create the provider session in the isolated task worktree
  └─ bind its concrete session and worker → running
```

WIP defaults to 8 running tasks per project and 10 across the machine. Shared lease paths do not
hold dispatch or resume. The brief names overlaps, asks the owner to rebase onto main before
landing, and keeps shared-doc edits in that task's own sections. Status shows the lease and
informational overlaps; the lease remains the staging boundary enforced by `alt land`.

A project stores a WIP cap only when explicitly configured. The first registry load removes legacy
stored caps of 3 once and logs the migration, preserving approval and engine pins; later explicit
caps, including 3, persist. L3 can use `alt project set <name> --wip N --reason "…"` or
`--unset-wip --reason "…"` for its own project, with N from 1 to the machine cap. The CLI persists
the request and altd applies it on the next tick before capacity gates, with no task slot or restart
needed. One project-level event records actor, reason and outcome; retries reuse the receipt and
event while the configured WIP still matches. Project add and remove remain operator-only.

When the project deploys from its own checkout, dispatch moves that checkout to `origin/main` before
the provenance gate reads it, and announces activation pending if the pull carried loaded backend code
or a tracked web build input. Only a clean checkout on main that is strictly behind moves; every other
state still refuses the dispatch.

Routing compares only named seven-day Claude data with a Codex window whose reported duration is
exactly seven days. A five-hour window is an availability signal, not the main preference score.
Unknown or incomparable weekly data uses the configured default, currently Codex, and records that
fact. An exhausted short or weekly window rules out only that provider. If both are unavailable,
the task stays queued. An explicit provider pin never silently falls back. An L3 turn stays on the
engine that ran the previous one unless that engine is unavailable or the other has fifteen points more
weekly headroom, so the transcript and its prompt cache stay warm instead of alternating between two
close quotas.

Altitude does not infer separate Fable and Opus allowances from an account-wide meter. A model pin
is honored inside the selected provider; model switching requires explicit observable policy rather
than a guessed quota relationship.

Codex CLI 0.153.4's JSON stream identifies the thread but carries no model. altd reads the matching
`$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*-<thread-id>.jsonl` (default home `~/.codex`) and takes
`model` and `effort` from the first `turn_context` timestamped at or after this worker's launch.
This distinguishes a resumed turn from earlier turns in the same rollout. The worker record caches
the observation; daemon polling copies it into the task and live monitor snapshot. L3 records it
while its synchronous turn is running and keeps it in that engine's session record. The sandboxed
worker does not read the rollout for telemetry. Unavailable or incompletely flushed metadata remains
unknown and is retried; older tasks render without the fields. The observed model is never used to
pin a later launch: only the separately recorded launch override controls that choice.
Task details expose `engine_model` and `engine_reasoning_effort`; Monitor session rows
expose `engine`, `model` (omitted when unknown), and `engine_reasoning_effort`.

## Messages, resume, and stop

A message from Burak (task page, chat through L3, or `alt task message`) is appended to the task's durable
conversation and to its inbox. Nothing is killed. A running Claude L2 receives the inbox at its next checkpoint:
the inbox hook returns it as additional context after a tool call, or as the reason to keep going when the
session is about to stop. A running Codex L2 receives it when its current turn ends and the message resumes the
thread with the inbox. For a blocked task the same locked append records a due `resume_after` request. The L3
CLI returns without fetching or writing the deployment checkout; altd sees the durable request on its next tick
and its keyed resume runner coalesces a simultaneous API wake, retry, or available WIP slot. Before provider launch it
persists a cross-process claim and moves that claim's exact message batch out of the hook-visible inbox. Delivered
messages leave the inbox; the conversation keeps them, and a message appended after that snapshot remains
for the running worker's next checkpoint. An L2's block goes to L3 first: L3's `alt task message` requests that
daemon resume, or `alt task escalate` turns it into a Needs you card for the operator; `--for-burak` on the block skips L3.
On start, altd queues one message per project listing its active tasks, so L3 resumes what a fault had stopped.

Voice capture does not add a message or a lifecycle state. The browser keeps the typed draft while it
records, uploads the bounded clip for transcription, and appends the returned text to the local
editable draft; nothing else appears. The explicit **Send** action then calls the same chat or
L2-message endpoint as typed text, so a busy L3 durably queues that combined text at the same turn
boundary and an L2 message follows the same checkpoint/resume rules. Cancel, permission denial, and
transcription failure create no conversation or queue record.

`dispatch.resume` is the only way a session is launched again, and altd owns it for message-triggered and
explicit resumes. `alt task resume`, `stop`, and `reject` require a reason and persist a task-local
`daemon_request`; the CLI process performs no Git or worker operation. One `daemon-request` event names
the task, operation, actor (`l3` or `burak`), reason, and request id. Altd executes the request, refuses
a changed state or worker/session identity, and makes a retry with the same reason and actor idempotent
while its terminal state/worker receipt still matches. A later lifecycle receives a new request and event.
The inbox and `resume_after` are the coordinator-to-daemon boundary: they survive coordinator exit and daemon
restart. The keyed runner is the in-process fast path; a durable claim is the cross-process fence. Its
`dispatching` marker also makes the independent restart guard wait. If altd restarts after the replacement worker
identity is saved but before task binding, it adopts that worker. If it cannot prove whether a provider launch
crossed an unexpected daemon exit, it reports a real recovery fault instead of risking a duplicate turn.

1. a task blocked before any launch goes back to the queue;
2. `resume_after` makes a message request or operational retry due; an exhausted window of a pinned engine or a
   WIP cap keeps the task blocked with a `waiting: …` reason until the request can run;
3. a self-deploy checkout is fast-forwarded to `origin/main` on the same terms as a fresh dispatch, then
   worktree and commit provenance are validated, and a worker that is still live is stopped first;
4. the provider conversation is resumed with the inbox text (or "Continue from your progress file.") and the
   replacement worker is bound atomically; a bind failure stops the unowned worker and files a fault. A genuine
   provenance or relaunch fault restores the claimed batch, consumes only the generation it tried, and blocks
   normally until another explicit request. A newer message carries a newer generation and stays due. A
   coordinator filesystem restriction never reaches this trusted boundary.

**Stop** (task page, `alt task stop --reason …`) runs in altd, blocks the task first, and then stops its worker,
so the poll never reads the exiting worker as a death. A message or `alt task resume --reason …` brings the same
session back; `alt task reject --reason …` ends the task and removes its worker in altd. An L2 that blocks with
`--fault` takes the system-fault path (incident, one L3 message) instead of asking Burak. A failed
resume blocks the task with an incident and leaves the provider conversation to L3. A cross-provider
continuation is a deliberate new attempt based on saved work, not a fake transcript resume: when a worker's
window runs out and the task is not pinned to an engine, Altitude removes the worker, requeues the task pinned
to the other engine, and the next dispatch briefs the fresh attempt with the task's `progress.md`.

Claude resume uses `claude --bg --resume`; Codex resume uses `codex exec resume <thread-id> -` with the inbox on
stdin from the same task worktree. Both engines have one contract: the persona may invoke the scoped Altitude
CLI, and the backend applies the identity, clean-Git, staging-scope, provenance, and merge-policy checks relevant to each
command and effect boundary. Claude hooks add telemetry and inbox delivery; they are not the backend authority
check.
Landing fetches the base and validates the current PR base/head pair. An owner whose branch needs
updating runs `git rebase origin/main` in the worktree; a conflict they cannot resolve goes to L3
through an ordinary `alt task block`, without `--fault`.

## Engine containment

A Codex L2 runs in Codex's own workspace-write sandbox: the task worktree, its Git directories (the common
directory and the worktree's own metadata under `.git/worktrees/`), and the Altitude home are its writable roots, the network stays on for pushes, PRs, and tests, and the launch environment
carries the task identity. A Codex L3 turn uses a dedicated permission profile: it writes only one fresh per-turn
runtime directory; the deployment checkout and Altitude home are read-only, direct command networking and the
user-service bus are denied. A required stdio MCP adapter exposes one coordinator tool for `alt` verbs and
GitHub/service reads, forwarding argument arrays and stdin to the project's fixed altd socket. Every fresh
and resumed turn receives this configuration and the instruction to use the tool; shell wrappers remain
unreachable inside the native sandbox. The adapter starts isolated Python from protected source, never
loads code from the writable runtime, and has no shell execution operation. Its one tool is approved for
unattended use; altd still applies its project and actor authorization on every request. A Claude L3 turn has the same runtime cwd and uses
`--restricted`, `dontAsk`, no unattended permission
prompts, no Edit/Write/NotebookEdit tools, and an exact allowlist. Both can read the checkout with Git
log/diff-stat/show-stat shims and the altitude journal. Claude's runtime shims and the MCP tool send every `alt` invocation and fixed
GitHub/service read through the project-bound socket, where altd supplies the project, rejects path-shaped task ids and
daemon-side file inputs, and re-applies the L3 command door; GitHub reads cannot select another repository, and checkout, GitHub, and service
write commands are absent; `alt issue new` and `alt issue comment` publish requested backlog through altd after its private-evidence check.
L3 may use `alt issue close <number> --reason completed|not-planned` only for operator-requested closure,
never autonomous backlog cleanup. It follows the same coordinator/socket boundary, publishes no text,
and records the actor, issue number, closure reason, and URL in the project log after GitHub succeeds.
L2 remains unable to mutate issues. Claude's native Bash
sandbox is unavailable on this host because unprivileged bwrap namespaces cannot be created, so enabling its
hard-failure mode would prevent every headless L3 turn; the deny-by-default tool boundary and runtime cwd provide
Claude's confinement, while Codex retains its native filesystem sandbox.

The September 7 coordinator outage is verified with the real Codex Linux sandbox, not profile assertions:
`ALTITUDE_TEST_CODEX_SANDBOX=1 python3 -m unittest tests.test_l3_privilege` runs the broker transport tests
and a native sandbox probe for checkout/state/Git writes and direct socket/HTTP access. The opt-in requires
a working Codex installation and fails if sandbox initialization fails. A live acceptance run additionally
starts a fresh L3 through the production launcher, observes a successful coordinator tool call and a
cross-project refusal, and checks repository and user-bus denials from that session's shell. Activation
uses the normal merged-change quiet point, followed by the same fresh-session acceptance check.

An L3 session on either engine that predates this confinement policy is rotated before its next turn.
The common session save records the version, so each engine then resumes its own current conversation.
This also migrates legacy Codex conversations onto the MCP transport: changing launch configuration alone
does not replace their persisted developer instructions on resume. The next ordinary Altitude turn owns
this one-time rotation; activation needs no manual session reset or task action.

Both engines launch L2 workers through the same transient user-unit command builder, outside altd's
cgroup. Claude runs foreground `-p --output-format stream-json` inside its own unit, with its settings,
hooks, model pin and resumable session; launch and resume stop any daemon job still bound to the task name.
Each turn has a private worker record and output log, and Stop removes the unit's descendants.
Each Codex turn also uses this boundary,
because altd's own `NoNewPrivileges` hardening would stop its nested bwrap from starting.
The outer launcher alone receives the user-session bus;
the child starts from Altitude's clean environment plus the actor identity; stopping the unit stops the whole
process tree. Codex stdout JSONL is private task evidence: `thread.started.thread_id` is the session identity and
`turn.completed.usage` the latest reported usage. A turn that ends without a report, a block, or a completion
blocks the task with a system fault and incident carrying the engine's result error or stderr tail.

## L3 sessions and provider changes

The project header's **Remove project** action and `alt project remove <name>` detach L3 by
unregistering the project. Removal is permitted only after all tasks finish or are rejected and
their workers and operations have ended. An active L3 turn or report/timer operation must finish
first. The cross-process project activity lock applies to CLI and server turns alike; queued L3
messages remain saved and do not themselves prevent removal. No task is reassigned or stopped.

The app leaves the removed project and reconciles its selection; stale project/task/report routes
show an unmanaged state, or First run when nothing remains managed. The repository, remaining
worktrees, provider sessions, conversations, task archives and queue stay on disk. Adding the same
project name and repository attaches L3 again, restores history and delivers the waiting FIFO.
Normal provider selection and context rotation still apply. Reset rotates a session on its next
turn and does not remove a project from management.

L3 stores separate Claude and Codex session records. The conversation composer's engine pill pins
the project's L3 to one engine, for chat and server-triggered turns alike, until it is set back to
Auto; `alt chat --engine` pins one CLI turn. A pinned turn runs there or reports the hold, and never
falls back. A turn started from Chat finishes and is recorded even when the page that started it
leaves mid-stream. L3 runs headless, so its only checkpoint is the turn boundary: a message Burak
sends while a turn is in flight is appended to the project's durable L3 queue and run there, never
injected into the running turn. The finishing turn drains the queue itself, one turn at a time and in
arrival order; a message queued but not started is not a turn in flight, so it neither holds the
quiet-point restart nor is lost by one. A quota-selected turn resumes only the chosen provider's session. When the other provider handled intervening chat, Altitude supplies the missed
human conversation as a small explicit handoff; it does not replay tool logs or invent a shared
provider transcript. A Claude limit after text or tool activity never causes the same turn to be
automatically replayed on Codex because that could duplicate side effects. Provider selection changes
neither L3's project-level responsibility nor L2's end-to-end task ownership.

Every direct, queued, folded, or server-triggered L3 turn publishes one process-local active record
while it owns the project turn lock. The record contains only a stable turn id, its start time, and
trigger; the prompt remains in the normal private/history path. `GET /api/chat` is the UI authority
for this state. It snapshots the waiting queue and active record under one lifecycle guard, so claiming
a queued row and publishing its turn cannot expose an idle state between them. The initiating tab keeps its streamed response and suppresses a duplicate indicator, while a newly
mounted or reconnected conversation reconstructs the typing indicator (a chat turn) or the "L3 is
handling <what>" line (a server-triggered turn) from the active record. The stream's first line
names the turn, and assistant or error history rows carry the same turn id, so live local output
remains until terminal history replaces it and suppresses any raced active snapshot. A
`finally` removes the record on every normal, provider-error, or exception path. If altd
fails, the in-process turn ends and its process-local record disappears with it, so the replacement
process cannot advertise stale work.

## Polling and cleanup

Claude jobs and Codex processes normalize to the same worker row: worker id, provider session id,
PID, state, status, detail, and latest usage. Polling follows the persisted `l2_engine`. A merged change
to Altitude's loaded backend or served web bundle inputs activates at a narrow quiet point: no dispatch
marker or resume claim, L3 turn, or report verification in flight. Running and blocked workers do not
hold activation, and new dispatches continue while activation is pending. The regular thirty-second
tick discovers merged changes independently of worker completion. Dispatch, resume, L3 turns
and report verification wait only from the restart unit request until the replacement daemon is
ready; the ten-minute restart fault releases a stuck window. altd runs the guarded build-and-restart
script itself.
After an
`altd` restart, both engines are adopted from their private worker records, provider output and active units;
existing daemon jobs are observed through their active unit and session transcript until they finish or resume.
Both engines' units survive the service restart.
An ended or missing worker's report is current only when its mtime is at or after the latest launch
or resume timestamp, persisted on the task before the provider starts. A missing or stale report
without an explicit completion is a system fault
that blocks the task and files an incident; a current report goes to verification. Rejection and
post-merge cleanup use the same provider adapter.

A worker's PATH resolves `alt` to the deployment checkout's `bin/alt`, so each invocation uses the
current CLI; L2 commands and the inbox hook use locked durable state directly and keep working while
altd is down. The daemon reads durable completion and inbox records at the next checkpoint after startup.

## Live transcript

The task page's conversation is the operator's exchange with the L2. Its live session panel (the second
tab on a phone) reads like a Claude Code window: the engine's local session records and Altitude's task events project into one timeline
in time order, and the page renders it as a conversation. Prompts (the brief, a resume, a task message the
worker read at its checkpoint) appear as prompt blocks; the worker's replies as prose with code blocks; each
tool call as one compact row (`$ git status`, `Read altitude/tasks.py`, a Codex command or file change) with
its output folded under it; task boundaries (queued → running, stopped, resume held) as thin separators with
subtle timestamps. Every row carries its role (user, assistant, tool, system), and a tool result carries the
id of the call it answers, so the page nests output under the command that produced it. Codex does not echo
its prompt into the thread, so the projection shows the task's own record of it: the brief for the first
turn, the delivered task messages for a resumed one. Hidden model reasoning (Claude thinking blocks, Codex
reasoning items) is stripped before anything crosses the HTTP boundary and never appears in either mode; the
panel does not run a summarizer. Tool output is bounded in the transcript, and Raw events lists every redacted
record (credential-shaped keys and values replaced) as the escape hatch. The browser supplies no paths: the
server derives the files from the registered task's engine and session id, reading the provider's own session
store (Claude's project JSONL, or the stdout JSONL of every turn of the Codex thread, kept in the task
directory). A mismatch between the displayed session and the task fails closed and asks that viewer to refresh.

Altitude does not copy or checksum provider transcripts. The durable human record is the task conversation;
provider stores follow the provider's own retention, and archival moves the whole task directory, so Codex
worker records travel with it. Task state changes and stop/resume records are the timeline's boundary events. A
parser error or incomplete final JSONL record is displayed as viewer evidence and retried on the next poll; it
never changes task or worker state.

## Context and prompt-cache evidence

For Claude, context is the newest genuine assistant usage record: input plus cache-read plus
cache-creation tokens. Synthetic all-zero limit records are ignored. For Codex, the latest
`turn.completed.usage.input_tokens` drives an approximate percentage against the configured context
window; reported `cached_input_tokens` is retained separately.

Every quota and session figure carries the time it was observed, and age is reported rather than
hidden. A quota snapshot older than thirty minutes — the age at which the router stops routing on
it — is stale: its figures are still shown and labelled, not replaced by "unknown", which is
reserved for having no reading at all. A session snapshot older than five minutes while its worker
is live is stale in the same way; an idle or finished worker is simply as old as it says.

A provider session id records which provider conversation Altitude asks to resume. That documented
resume behavior does not prove a cache hit or imply any undocumented prompt-cache guarantee.
Altitude reports cache-token fields only when the provider emits them and otherwise makes no claim
about cache reuse. Physical worker replacement and provider-session continuity are therefore shown
as separate facts.
