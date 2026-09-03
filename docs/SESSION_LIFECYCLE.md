# Engine and session lifecycle

> **Scope:** This document describes executable `main` after phase 7 of the module-by-module
> simplification (Chat-only intake and the Live session tab, 2026-09-03). See the
> [2026-09-02 review checkpoint](simplification-review/README.md).

## Live L2 transcript

The task page's Conversation tab stays a concise Burak/L2 conversation. Its Live session tab projects the engine's
local JSONL and Altitude's task events into one engine-neutral timeline; it does not run a summarizer and never
claims hidden reasoning. Tool output is bounded in the default timeline and complete observable records can be
expanded in raw mode. Raw mode remains local and redacts credential-shaped keys and values before data crosses the
HTTP boundary. The browser supplies no paths: the server derives transcript files only from the registered task's
engine and session id.

## Transcript retention

The live view reads the provider's own session store (Claude's project JSONL, or the stdout JSONL of every turn of
the Codex thread, kept in the task directory) together with Altitude's task events. Altitude does not copy or checksum provider
transcripts. The durable human record is the task conversation; provider stores follow the provider's own
retention, and archival moves the whole task directory, so Codex worker records travel with it.

Transcript reads from the Live Session view carry the project, task, engine, and displayed session. A mismatch
fails closed and asks that viewer to refresh. Messages are not fenced to a displayed session: they queue on the
task and reach whichever worker owns it at its next checkpoint (see "Messages, resume, and stop"). Task state
changes and stop/resume records are the timeline's boundary events. A parser error or incomplete final JSONL
record is displayed as viewer evidence and retried on the next poll; it never changes task or worker state.

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
  ├─ WIP/lease and Git provenance gates
  ├─ weekly-first provider decision (or explicit task/project pin)
  ├─ persist l2_engine + model + routing reason
  ├─ create the provider session in the isolated task worktree
  └─ bind its concrete session and worker → running
```

Routing compares only named seven-day Claude data with a Codex window whose reported duration is
exactly seven days. A five-hour window is an availability signal, not the main preference score.
Unknown or incomparable weekly data uses the configured default, currently Codex, and records that
fact. An exhausted short or weekly window rules out only that provider. If both are unavailable,
the task stays queued. An explicit provider pin never silently falls back.

Altitude does not infer separate Fable and Opus allowances from an account-wide meter. A model pin
is honored inside the selected provider; model switching requires explicit observable policy rather
than a guessed quota relationship.

## Messages, resume, and stop

A message from Burak (task page, chat through L3, or `alt task message`) is appended to the task's durable
conversation and to its inbox. Nothing is killed. A running Claude L2 receives the inbox at its next checkpoint:
the inbox hook returns it as additional context after a tool call, or as the reason to keep going when the
session is about to stop. A running Codex L2 receives it when its current turn ends and the message resumes the
thread with the inbox. A blocked task resumes at once with the message. Delivered messages leave the inbox; the conversation keeps them.

`dispatch.resume` is the only way a session is launched again:

1. a task blocked before any launch goes back to the queue;
2. an exhausted window of a pinned engine or a file lease keeps the task blocked with `resume_after` set and a
   `waiting: …` reason; the tick retries when it is due;
3. worktree and commit provenance are validated, and an idle worker that is still live is stopped first;
4. the provider conversation is resumed with the inbox text (or "Continue from your progress file.") and the
   replacement worker is bound atomically; a bind failure stops the unowned worker and files a fault.

**Stop** (task page, `alt task stop`) blocks the task first and then stops its worker, so the poll never reads
the exiting worker as a death. A message or Resume brings the same session back; Reject ends the task. A failed
resume blocks the task with an incident and leaves the provider conversation to L3. A cross-provider
continuation is a deliberate new attempt based on saved work, not a fake transcript resume: when a worker's
window runs out and the task is not pinned to an engine, Altitude removes the worker, requeues the task pinned
to the other engine, and the next dispatch briefs the fresh attempt with the task's `progress.md`.

Claude resume uses `claude --bg --resume`; Codex resume uses `codex exec resume <thread-id> -` with the inbox on
stdin from the same task worktree. Both engines have one contract: the persona may invoke the scoped Altitude
CLI, and the backend applies the identity, clean-Git, lease, provenance, and merge-policy checks relevant to each
command and effect boundary. Claude hooks add telemetry and inbox delivery; they are not the backend authority
check.

A Codex L2 runs in Codex's own workspace-write sandbox: the task worktree, its Git common directory, and the
Altitude home are its writable roots, the network stays on for pushes, PRs, and tests, and the launch environment
carries the task identity. The turn runs in a transient user unit because altd's `NoNewPrivileges` hardening would
stop Codex's nested bwrap from starting; stopping the unit stops the whole process tree. Codex stdout JSONL is
private task evidence: `thread.started.thread_id` is the session identity and `turn.completed.usage` the latest
reported usage. A turn that ends without a report, a block, or a completion blocks the task as ended without a
report, exactly like a Claude session that exits early.

## L3 sessions and provider changes

L3 stores separate Claude and Codex session records. A quota-selected turn resumes only the chosen
provider's session. When the other provider handled intervening chat, Altitude supplies the missed
human conversation as a small explicit handoff; it does not replay tool logs or invent a shared
provider transcript. A Claude limit after text or tool activity never causes the same turn to be
automatically replayed on Codex because that could duplicate side effects.

A Codex L3 turn runs `codex exec` in Codex's workspace-write sandbox from a disposable runtime directory under
the project's Altitude folder: it writes only there and to the Altitude home, reads the project checkout, and
reaches GitHub through `gh` like Claude L3. Both engines use the same `alt` commands under the same backend
checks, from one persona. Provider selection changes neither L3's project-level responsibility nor L2's
end-to-end task ownership.

For Codex turns, the user DBus and runtime directory exist only in the outer `systemd-run` launcher and are unset
before Codex starts; the child receives Altitude's clean environment plus the actor identity.

## Polling, restart, and cleanup

Claude jobs and Codex processes normalize to the same worker row: worker id, provider session id,
PID, state, status, detail, and latest usage. Polling follows the persisted `l2_engine`. After an
`altd` restart, Claude is rediscovered through its job registry and Codex through its private task
record and the state of its transient unit. A missing or failed worker without a
valid completion is a system fault, not “still running.” Rejection and post-merge cleanup use the
same provider adapter.

Merging Python changes and restarting the service are separate operations. A source merge can mark a
restart pending, but it never stops the running service by itself; the web app's banner offers the
restart once nothing is running, and Burak presses it.

After changing provider launch, runtime, or session-resume integration, validate Altitude with one
tiny real task through the complete L3 → L2 → PR → required checks → merge path. Afterward, the
operator must verify that no worker remains.

## Context and prompt-cache evidence

For Claude, context is the newest genuine assistant usage record: input plus cache-read plus
cache-creation tokens. Synthetic all-zero limit records are ignored. For Codex, the latest
`turn.completed.usage.input_tokens` drives an approximate percentage against the configured context
window; reported `cached_input_tokens` is retained separately.

A provider session id records which provider conversation Altitude asks to resume. That documented
resume behavior does not prove a cache hit or imply any undocumented prompt-cache guarantee.
Altitude reports cache-token fields only when the provider emits them and otherwise makes no claim
about cache reuse. Physical worker replacement and provider-session continuity are therefore shown
as separate facts.
