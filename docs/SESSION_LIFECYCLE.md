# Engine and session lifecycle

## Live L2 transcript

The task page stays a concise Burak/L2 conversation. Its opt-in **Live session** route projects the engine's local
JSONL and Altitude's task events into one engine-neutral timeline; it does not run a summarizer and never claims
hidden reasoning. Tool output is bounded in the default timeline and complete observable records can be expanded
in raw mode. Raw mode remains local and redacts credential-shaped keys and values before data crosses the HTTP
boundary. The browser supplies no paths: the server derives transcript files only from the registered task and its
recorded engine, worker, and session identities.

## Durable transcript bundles

Every L2 lifecycle boundary snapshots the current attempt beneath the task's `transcripts/` directory. Each
attempt is separate and contains a versioned `manifest.json`, a stable ordered `events.jsonl`, and redacted
provider-native JSONL. Canonical events retain join keys for the task, dispatch/attempt, session, worker, task
message, and helper where available; report or digest checksums link the terminal outcome. Worker replacement
and resume stay in the same attempt, while redispatch records `previous_attempt` and starts a new directory.
The snapshot is best-effort at early creation and incomplete provider records are retried at the next boundary,
so interruption, cancellation, containment faults, and malformed final actions retain evidence produced so far.

`alt --project PROJECT transcript export SLUG FILE.tar.gz` creates a self-contained private archive; after
extracting it, `alt transcript validate DIRECTORY` verifies its schema, ordering, and SHA-256 checksums without
an Altitude home or provider session store. Export is an access-controlled local operator action. Credential-
shaped keys and values and encrypted content are removed from both canonical and provider-native copies. Bundles
are private by default and are never automatically added to future model context.

Transcript retention follows task retention: archival moves the entire task directory, so worker/worktree cleanup
cannot remove it. The default is indefinite retention. Operators may delete an archived task's transcript only
under their external retention policy; deletion is intentionally not coupled to routine cleanup. Schema readers
reject unknown versions, and future L3, L1, reviewer, and recovery bundles can use the same manifest/event contract
with a different `level` and `role`.

Each read and steering request carries the project, task, dispatch generation, engine, and displayed session. A
mismatch fails closed and asks the viewer to refresh, so steering cannot land on a replacement L2. Steering ends
the current physical worker turn and resumes the same logical engine conversation in a newly owned worker; Codex
normally retains its thread id. Historical Claude session ids remain evidence, but disabled Claude work is not
resumed. Altitude records the old and new
worker/session identities and renders resume, replacement, compaction, engine-change, and recovery events as
boundaries underneath the same logical task dispatch. A parser error or incomplete final JSONL record is displayed
as viewer evidence and retried on the next poll; it never changes task or worker state.

Altitude has one logical owner per task and replaceable physical workers. These are different
identities on purpose:

| Field | Meaning | Changes when |
| --- | --- | --- |
| `dispatch_id` | one L2 attempt | the task is dispatched again as a new attempt |
| `l2_engine` | provider for that attempt | only on a fresh attempt, never a transparent resume |
| `session_id` | provider conversation/thread | Codex keeps it across turns; historical Claude replacements remain evidence |
| `agent_id` | current provider worker, including a legacy Claude job | every physical replacement |
| `l2_token` | backend ownership fence for the logical L2 attempt | stable for the attempt; old workers are stopped before replacement |
| `routing` | reason plus quota evidence used at launch | written once with fresh dispatch |

## Fresh dispatch

```text
queued task
  ├─ recovery/WIP/lease and Git provenance gates
  ├─ closed-capability and weekly-quota decision (or explicit enabled pin)
  ├─ persist l2_engine + model + reason + raw quota evidence
  ├─ create the provider session in the isolated task worktree
  └─ bind its concrete session and worker → running
```

Routing may display historical Claude quota observations, but the closed autonomous capability set
contains only Codex. A fresh turn therefore selects Codex when its named weekly/short
windows permit it and otherwise stays held. A Claude pin is explicitly unavailable and never silently
falls back. Weekly-first comparison remains defined for a future provider only after its complete
supervision capability has been proven and deliberately enabled.

Altitude does not infer separate Fable and Opus allowances from an account-wide meter. A model pin
is honored inside the selected provider; model switching requires explicit observable policy rather
than a guessed quota relationship.

## Message and resume

Under the task's resume lock Altitude first validates the exact dispatch/session/state/worker snapshot
and the selected provider's closed autonomous capability. A disabled provider returns a synchronous
provider hold without changing task state, conversation, events, incidents, recovery, or workers;
scheduled retries leave the existing durable retry in place. For an enabled provider Altitude then:

1. appends Burak's message to the durable human conversation with the addressed snapshot;
2. validates the task worktree/commit provenance;
3. stops the current physical worker and confirms it is no longer live;
4. resumes the task's already-selected provider conversation;
5. atomically binds the Codex replacement worker while retaining the exact thread id.

Altitude never starts the replacement before stopping the old writer. A failed stop starts nothing.
A failed resume leaves the provider conversation and task evidence available for L3 recovery. A
cross-provider continuation is a deliberate new attempt based on saved work, not a fake transcript
resume.

Claude `--bg` launch/resume is disabled for L2, L3, L1, and helpers. Its short launcher and job row do
not prove descendant ownership or cgroup emptiness, and a genuine supervised resume/result-spool test
could not be completed locally without spending a provider turn. The existing backend authority checks,
hooks, guardrails, status inspection, and legacy-worker stop/remove adapters remain; a legacy Claude
worker is never stopped merely to attempt a now-disabled replacement. Its task mutation and publication
boundaries check the target provider before any model-controlled actor environment. No actor variable,
TTY, or same-user CLI token is claimed as operator authentication: the accepted stopped-production
activation gate must prove every legacy Claude unit/process empty before this source runs. Read-only
inspection and physical stop/remove cleanup remain available while the stopped state is reconciled.

Codex uses `codex exec resume <thread-id> <prompt>` from the same task worktree. Codex stdout JSONL
is private task evidence; `thread.started.thread_id` is the session identity and
`turn.completed.usage` is the latest reported usage. A Codex L2 receives no Altitude control
capability. It runs with an explicit permission profile that denies the filesystem by default,
allows the task worktree, and keeps the Git common directory and Altitude state outside its writable
surface. Hosted tools and model-command network access are disabled. The inner sandbox hides host PIDs, and the
complete turn, including descendant processes, is placed in a transient user cgroup. Altitude will not interpret the
result until that containment unit is empty.

A Codex L2's final response is a strict, inert action object. After worker exit, the trusted broker
validates the object against the current dispatch, session, worker, lease, and recovery state. The
broker—not the model process—may then post the human-facing message, land a PR, complete a no-code
task, block, continue the same thread, or launch optional helpers. A held action is durable and does
not spend another model turn merely to wait for recovery.

## L3 sessions and provider identity

L3 retains separate historical Claude and Codex session records, but new autonomous turns select and
resume only Codex. The disabled Claude record is neither migrated into Codex nor replayed; durable
human conversation remains the explicit handoff boundary.

A Codex L3 turn uses the same containment and inert-result pattern, but its filesystem view is
read-only with respect to durable inputs: it may read the compact Altitude state and the selected project checkout,
and may write only to an inert disposable runtime directory required by the Codex CLI. Its trusted
broker applies at most the validated project-coordination action. Provider selection changes neither
L3's project-level responsibility nor L2's end-to-end
task ownership.

For contained Codex turns, the user DBus and runtime directory exist only in the outer `systemd-run` launcher and
are unset before Codex starts. The child receives an allowlisted environment; ambient tokens, API keys, SSH agents,
Git credential helpers, and the L2 capability are absent. A deterministic host canary checks that its inner sandbox
cannot see or signal a known host PID. A model-requested GitHub issue cannot contain synthesized private context: the broker
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

After changing enabled provider launch, runtime, or session-resume integration, validate Altitude with one
tiny real Codex task through the complete L3 → L2 → PR → required checks → merge path. Afterward, the
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
