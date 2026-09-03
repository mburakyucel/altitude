# Engine and session lifecycle

> **Scope:** This document describes executable `main` at commit `97e1197`, including its current
> stop-and-replace steering behavior. It does not describe the unmerged simplification branches or
> an App Server steering design. See the
> [2026-09-02 review checkpoint](simplification-review/README.md).

## Live L2 transcript

The task page stays a concise Burak/L2 conversation. Its opt-in **Live session** route projects the engine's local
JSONL and Altitude's task events into one engine-neutral timeline; it does not run a summarizer and never claims
hidden reasoning. Tool output is bounded in the default timeline and complete observable records can be expanded
in raw mode. Raw mode remains local and redacts credential-shaped keys and values before data crosses the HTTP
boundary. The browser supplies no paths: the server derives transcript files only from the registered task and its
recorded engine, worker, and session identities.

## Transcript retention

The live view reads the provider's own session store (Claude's project JSONL, or the Codex worker's stdout JSONL
kept in the task directory) together with Altitude's task events. Altitude does not copy or checksum provider
transcripts. The durable human record is the task conversation; provider stores follow the provider's own
retention, and archival moves the whole task directory, so Codex worker records travel with it.

Transcript reads and steering from the Live Session view carry the project, task, dispatch generation, engine, and
displayed session. A mismatch fails closed and asks that viewer to refresh. The normal Task and Project composers
currently omit those optional displayed-generation fields, so they address the generation that is current when the
server acquires the task lock; stale-page rejection is not guaranteed on those two surfaces. Steering then ends
the current physical worker turn and resumes the same logical engine conversation in a newly owned worker; Codex
normally retains its thread id, while Claude retains its resumable session id. Altitude records the old and new
worker/session identities in the `resumed` event. The reader recognizes additional compatibility event names, but
this baseline has no producer for separate replacement, compaction, engine-change, or recovery boundary events. A parser error or incomplete final JSONL record is displayed
as viewer evidence and retried on the next poll; it never changes task or worker state.

Altitude has one logical owner per task and replaceable physical workers. These are different
identities on purpose:

| Field | Meaning | Changes when |
| --- | --- | --- |
| `dispatch_id` | one L2 attempt | the task is dispatched again as a new attempt |
| `l2_engine` | provider for that attempt | only on a fresh attempt, never a transparent resume |
| `session_id` | provider conversation/thread | Codex keeps it across turns; Claude may return a replacement on resume |
| `agent_id` | current Claude job or Codex OS worker | every physical replacement |
| `l2_token` | backend ownership fence for the logical L2 attempt | stable for the attempt; old workers are stopped before replacement |
| `routing` | one sentence saying why this engine was chosen | written once with fresh dispatch |

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

## Message and resume

Burak's message is first appended to the task's durable human conversation with its `dispatch_id`
and `session_id`. The surrounding resume operation separately validates current task state and
worker identity; those values are not fields in the message row. Under the task's resume lock Altitude then:

1. validates that snapshot and the task worktree/commit provenance;
2. stops the current physical worker and confirms it is no longer live;
3. resumes the task's already-selected provider conversation;
4. atomically binds the replacement worker (and Claude's replacement session id, when it changes).

Altitude never starts the replacement before stopping the old writer. A failed stop starts nothing.
A failed resume blocks the task and leaves the provider conversation and task evidence to L3. A
cross-provider continuation is a deliberate new attempt based on saved work, not a fake transcript
resume.

Claude resume uses `claude --bg --resume`. Its L2 contract is direct: the persona may invoke the
scoped Altitude CLI, while the backend applies the identity, clean-Git, lease, provenance, and merge
policy checks relevant to each command and effect boundary. Claude hooks add command guardrails
and telemetry; they are not the backend authority check.

Codex uses `codex exec resume <thread-id> <prompt>` from the same task worktree. Codex stdout JSONL
is private task evidence; `thread.started.thread_id` is the session identity and
`turn.completed.usage` is the latest reported usage. A Codex L2 receives no Altitude control
capability. It runs with an explicit permission profile that denies the filesystem by default,
allows the task worktree, and keeps the Git common directory and Altitude state outside its writable
surface. Hosted tools and model-command network access are disabled. The inner sandbox hides host PIDs, and the
complete turn, including descendant processes, is placed in a transient user cgroup. Altitude will not interpret the
result until that containment unit is empty.

A Codex L2's final response is a strict, inert action object. After worker exit, the trusted broker
validates the object against the current dispatch, session, worker, and lease state. The
broker—not the model process—may then post the human-facing message, land a PR, complete a no-code
task, block, continue the same thread, or launch optional helpers.

## L3 sessions and provider changes

L3 stores separate Claude and Codex session records. A quota-selected turn resumes only the chosen
provider's session. When the other provider handled intervening chat, Altitude supplies the missed
human conversation as a small explicit handoff; it does not replay tool logs or invent a shared
provider transcript. A Claude limit after text or tool activity never causes the same turn to be
automatically replayed on Codex because that could duplicate side effects.

A Codex L3 turn uses the same containment and inert-result pattern, but its filesystem view is
read-only with respect to durable inputs: the full Altitude runtime root (`ALTITUDE_HOME`) and the
selected project checkout are readable roots, while only an inert disposable runtime directory is writable.
The prompt normally directs it to compact state, but the sandbox does not narrow reads to that file. Its trusted
broker applies at most the validated project-coordination action. Claude L3 retains its direct CLI
contract. Provider selection changes neither L3's project-level responsibility nor L2's end-to-end
task ownership.

For contained Codex turns, the user DBus and runtime directory exist only in the outer `systemd-run` launcher and
are unset before Codex starts. The child receives an allowlisted environment; ambient tokens, API keys, SSH agents,
Git credential helpers, and the L2 capability are absent. The inner sandbox hides host PIDs; this baseline has no
deterministic host-PID canary. A model-requested GitHub issue cannot contain synthesized private context: the broker
stores only the exact current chat message, with a title quoted from it, as a private draft. Publication requires a
second exact, draft-specific approval message from Burak, and secret-shaped content remains a hard refusal.

## Polling, restart, and cleanup

Claude jobs and Codex processes normalize to the same worker row: worker id, provider session id,
PID, state, status, detail, and latest usage. Polling follows the persisted `l2_engine`. After an
`altd` restart, Claude is rediscovered through its job registry and Codex through its private task
record plus validated PID start time and containment unit. A missing or failed worker without a
valid completion is a system fault, not “still running.” Rejection and post-merge cleanup use the
same provider adapter.

Merging Python changes and restarting the service are separate operations. A source merge can mark a
restart pending, but it never stops the running service by itself.

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
