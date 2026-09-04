# Engine and session lifecycle

Altitude has one logical owner per task and replaceable physical workers. These are different
identities on purpose:

| Field | Meaning | Changes when |
| --- | --- | --- |
| `attempt` | one L2 attempt, counted from 1 | the task is dispatched again from the queue |
| `l2_engine` | provider for that attempt | only on a fresh attempt, never a transparent resume |
| `session_id` | provider conversation/thread | Codex keeps it across turns; Claude may return a replacement on resume |
| `agent_id` | current Claude job or Codex OS worker | every physical replacement |
| `routing` | one sentence saying why this engine was chosen | written once with fresh dispatch |

The L2 learns its attempt from `ALTITUDE_ATTEMPT`. Replies, completion, and landing name it, so
a worker of an earlier attempt cannot act for the current one. `ALTITUDE_SESSION_KEY` (`project--slug-attempt`)
keys the edit-count telemetry across worker replacements.

## Fresh dispatch

```text
queued task
  ├─ self-deploy checkout fast-forwarded to origin/main
  ├─ WIP/lease and Git provenance gates
  ├─ weekly-first provider decision (or explicit task/project pin)
  ├─ persist l2_engine + model + routing reason
  ├─ create the provider session in the isolated task worktree
  └─ bind its concrete session and worker → running
```

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

## Messages, resume, and stop

A message from Burak (task page, chat through L3, or `alt task message`) is appended to the task's durable
conversation and to its inbox. Nothing is killed. A running Claude L2 receives the inbox at its next checkpoint:
the inbox hook returns it as additional context after a tool call, or as the reason to keep going when the
session is about to stop. A running Codex L2 receives it when its current turn ends and the message resumes the
thread with the inbox. For a blocked task the same locked append records a due `resume_after` request. The L3
CLI returns without fetching or writing the deployment checkout; altd sees the durable request on its next tick
and its keyed resume runner coalesces a simultaneous API wake, retry, or lease release. Before provider launch it
persists a cross-process claim and moves that claim's exact message batch out of the hook-visible inbox. Delivered
messages leave the inbox; the conversation keeps them, and a message appended after that snapshot remains
for the running worker's next checkpoint. An L2's block goes to L3 first: L3's `alt task message` requests that
daemon resume, or `alt task escalate` turns it into an Inbox card for Burak; `--for-burak` on the block skips L3.
On start, altd queues one message per project listing its active tasks, so L3 resumes what a fault had stopped.

Voice capture does not add a message or a lifecycle state. The browser keeps the typed draft while it
records, uploads the bounded clip for transcription, and shows the returned text separately. **Edit /
insert** changes only the local editable draft. The explicit **Send** action then calls the same Chat
or L2-message endpoint as typed text, so a busy L3 durably queues that combined text at the same turn
boundary and an L2 message follows the same checkpoint/resume rules. Cancel, discard, permission
denial, and transcription failure create no conversation or queue record.

`dispatch.resume` is the only way a session is launched again, and altd owns it for message-triggered resumes.
The inbox and `resume_after` are the coordinator-to-daemon boundary: they survive coordinator exit and daemon
restart. The keyed runner is the in-process fast path; a durable claim is the cross-process fence. Its
`dispatching` marker also makes the independent restart guard wait. If altd restarts after the replacement worker
identity is saved but before task binding, it adopts that worker. If it cannot prove whether a provider launch
crossed an unexpected daemon exit, it reports a real recovery fault instead of risking a duplicate turn.

1. a task blocked before any launch goes back to the queue;
2. `resume_after` makes a message request or operational retry due; an exhausted window of a pinned engine or a
   file lease keeps the task blocked with a `waiting: …` reason until the request can run;
3. a self-deploy checkout is fast-forwarded to `origin/main` on the same terms as a fresh dispatch, then
   worktree and commit provenance are validated, and a worker that is still live is stopped first;
4. the provider conversation is resumed with the inbox text (or "Continue from your progress file.") and the
   replacement worker is bound atomically; a bind failure stops the unowned worker and files a fault. A genuine
   provenance or relaunch fault restores the claimed batch, consumes only the generation it tried, and blocks
   normally until another explicit request. A newer message carries a newer generation and stays due. A
   coordinator filesystem restriction never reaches this trusted boundary.

**Stop** (task page, `alt task stop`) blocks the task first and then stops its worker, so the poll never reads
the exiting worker as a death. A message or Resume brings the same session back; Reject ends the task. An L2 that blocks with
`--fault` takes the system-fault path (incident, one L3 message) instead of asking Burak. A failed
resume blocks the task with an incident and leaves the provider conversation to L3. A cross-provider
continuation is a deliberate new attempt based on saved work, not a fake transcript resume: when a worker's
window runs out and the task is not pinned to an engine, Altitude removes the worker, requeues the task pinned
to the other engine, and the next dispatch briefs the fresh attempt with the task's `progress.md`.

Claude resume uses `claude --bg --resume`; Codex resume uses `codex exec resume <thread-id> -` with the inbox on
stdin from the same task worktree. Both engines have one contract: the persona may invoke the scoped Altitude
CLI, and the backend applies the identity, clean-Git, lease, provenance, and merge-policy checks relevant to each
command and effect boundary. Claude hooks add telemetry and inbox delivery; they are not the backend authority
check.

## Codex containment

A Codex L2 runs in Codex's own workspace-write sandbox: the task worktree, its Git directories (the common
directory and the worktree's own metadata under `.git/worktrees/`), and the Altitude home are its writable roots, the network stays on for pushes, PRs, and tests, and the launch environment
carries the task identity. A Codex L3 turn runs the same way from a disposable runtime directory under the
project's Altitude folder, reading the project checkout and reaching GitHub through `gh` like a Claude L3; both
engines use the same `alt` commands under the same backend checks, from one persona.

Each Codex turn runs in a transient user unit created by the user manager, because altd's own `NoNewPrivileges`
hardening would stop Codex's nested bwrap from starting. The outer launcher alone receives the user-session bus;
the child starts from Altitude's clean environment plus the actor identity; stopping the unit stops the whole
process tree. Codex stdout JSONL is private task evidence: `thread.started.thread_id` is the session identity and
`turn.completed.usage` the latest reported usage. A turn that ends without a report, a block, or a completion
blocks the task as ended without a report, exactly like a Claude session that exits early.

## L3 sessions and provider changes

L3 stores separate Claude and Codex session records. The Chat composer's engine choice pins the
project's L3 to Claude or Codex, for chat and server-triggered turns alike, until it is set back to
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

## Polling and cleanup

Claude jobs and Codex processes normalize to the same worker row: worker id, provider session id,
PID, state, status, detail, and latest usage. Polling follows the persisted `l2_engine`. A merged change
to Altitude's loaded backend or served web bundle inputs activates at the next quiet point: new
dispatches hold, and once no worker is running, no report is waiting and no L3 turn is in flight, altd
runs the guarded build-and-restart script itself.
After an
`altd` restart, Claude is rediscovered through its job registry and Codex through its private task
record and the state of its transient unit. A missing or failed worker without a valid completion is
a system fault, not “still running.” Rejection and post-merge cleanup use the same provider adapter.

## Live transcript

The task page's Conversation tab stays a concise Burak/L2 conversation. Its Live session tab reads like a
Claude Code window: the engine's local session records and Altitude's task events project into one timeline
in time order, and the page renders it as a conversation. Prompts (the brief, a resume, a task message the
worker read at its checkpoint) appear as prompt blocks; the worker's replies as prose with code blocks; each
tool call as one compact row (`$ git status`, `Read altitude/tasks.py`, a Codex command or file change) with
its output folded under it; task boundaries (queued → running, stopped, resume held) as thin separators with
subtle timestamps. Every row carries its role (user, assistant, tool, system), and a tool result carries the
id of the call it answers, so the page nests output under the command that produced it. Codex does not echo
its prompt into the thread, so the projection shows the task's own record of it: the brief for the first
turn, the delivered task messages for a resumed one. Hidden model reasoning (Claude thinking blocks, Codex
reasoning items) is stripped before anything crosses the HTTP boundary and never appears in either mode; the
tab does not run a summarizer. Tool output is bounded in the conversation, and Raw mode lists every redacted
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
