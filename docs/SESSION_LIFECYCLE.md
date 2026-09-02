# Engine and session lifecycle

> **Candidate-source status.** This describes the current integration candidate; production
> remains stopped and frozen at `97e1197`, with no ActivationReceipt. Claude rows below are read-only
> legacy evidence/cleanup behavior. New autonomous L3, L2, L1, and helper turns are Codex-only.
> Durable physical-turn ownership is active for L3; L2-owner and helper adoption remain planned.

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
mismatch fails closed and asks the viewer to refresh, so steering cannot land on a replacement L2. Enabled steering
ends the current physical Codex turn and resumes that thread in a newly owned worker. Historical Claude session and
worker ids remain read-only observation/stop/cleanup evidence and cannot be resumed or converted into Codex. Altitude
records the old and new Codex worker/session identities and renders resume, replacement, compaction, and recovery
events as boundaries underneath the same logical task dispatch. A parser error or incomplete provider-native JSONL record is
displayed as viewer evidence and retried on the next poll; it never changes task or worker state.

Altitude has one logical owner per task and replaceable physical workers. These are different
identities on purpose:

| Field | Meaning | Changes when |
| --- | --- | --- |
| `dispatch_id` | one L2 attempt | the task is dispatched again as a new attempt |
| `l2_engine` | selected engine or retained legacy provider | new attempts select Codex; a legacy Claude value is inert evidence |
| `session_id` | provider conversation/thread | Codex keeps it across turns; a legacy Claude id is observation only |
| `agent_id` | current Codex OS worker or retained legacy Claude job | every enabled Codex physical replacement |
| `l2_token` | backend ownership fence for the logical L2 attempt | stable for the attempt; old workers are stopped before replacement |
| `routing` | reason plus quota evidence used at launch | written once with fresh dispatch |

## Fresh dispatch

```text
queued task
  ├─ recovery/WIP/lease and Git provenance gates
  ├─ weekly-first Codex observation and configured model choice
  ├─ persist l2_engine + model + reason + raw quota evidence
  ├─ create the provider session in the isolated task worktree
  └─ bind its concrete session and worker → running
```

Routing uses Codex's reported seven-day allowance as its primary capacity score; the five-hour
window is an availability signal. Unknown telemetry is recorded as uncertainty and remains eligible,
while an actual quota/capacity launch failure creates a Codex-local hold. An explicit legacy Claude
pin fails closed and never falls through or becomes eligible.

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
4. resumes the task's already-selected Codex conversation;
5. atomically binds the Codex replacement worker while retaining the exact thread id.

This is the existing L2 path. It does not claim the new shared durable physical-transition
adoption; that remains planned for Phase 1B.3.

Altitude never starts the replacement before stopping the old writer. A failed stop starts nothing.
A failed resume leaves the Codex thread and task evidence available for recovery. No cross-provider
continuation exists in the current target.

Claude launch/resume is disabled for L2, L3, L1, and helpers. Legacy hooks and backend validation
remain only until stopped-state reconciliation and post-activation deletion; neither is a callable
worker or operator-authentication path. Activation must prove every legacy Claude unit/process empty;
read-only inspection and exact stop/cleanup remain available until that proof.

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

## L3 sessions and physical turns

Autonomous L3 is Codex-only. Every accepted message installs one closed current-turn operation in
`l3.json` before chat append, unit launch, or model work. A resumed Codex thread still gets a new
physical generation and deterministic managed unit; the previous unit must be proven empty first.
The process-local lock only reduces contention. Project-lock compare-and-swap elects the only launcher and every
later receipt across service processes; there is no long-held turn
flock. `l3.json` and its exact receipts are authoritative. Every operation names the exact service instance; a
replacement may reconcile delivery only after manager proof and a persisted handoff. The timer only schedules the
existing deduplicated background reconcile, so planned launch or slow delivery never holds up the timer loop.

A Codex L3 turn uses the same containment and inert-result pattern, but its filesystem view is
read-only with respect to durable inputs: it may read one bounded host-generated redacted projection and the
selected project checkout, but not `ALTITUDE_HOME`,
and may write only to an inert disposable scratch directory required by the Codex CLI. The trusted
wrapper writes the intent-bound result marker outside that model-writable scratch. Only after the exact
result is observed and the managed unit is proven empty may the broker append the keyed response and
apply at most one validated project-coordination action. A replacement may start the exact operation only while
it remains safely pre-effect at `planned`; once `prior_stopped` is durable, ambiguity refuses rather than relaunches.
Recovery turns bind the episode, permit revision, and claim; ordinary turns bind the canonical inactive epoch.
Both are rechecked before message, launch, result persistence, result delivery, and each enabled local action.

Codex writes each generation's final answer and JSON events to a predeclared host spool outside scratch. The
event header binds that spool to the persisted intent. If the wrapper dies before creating the result marker, a
replacement waits for unit emptiness and accepts only one complete, valid turn; partial evidence fails inertly.
This inspection also precedes the otherwise ambiguous `prior_stopped` decision: an exact completed spool repairs
forward without relaunch, no spool remains `ownership_uncertain`, and oversized, partial, or mismatched evidence
fails inertly. The spool is recovery evidence, not a second result authority, and is removed only after terminal
delivery retires.

Answer and event caps are enforced while Codex is still producing them. Overflow, timeout, or drain failure
terminates Codex; the managed wrapper exits and the exact outer generation must then be stopped/proven empty before
failure settlement. An interrupted non-reconcilable action remains `applying` and fences the current turn. B2 has
no disposition/reset API: Phase 3 must add the trusted command before activation. Remote issue publication, task
resume, recovery repair delegation, and L3 recovery hold/clear are rejected before claim until 1C.4/B3/Phase 3G
supplies their final boundaries.

The target L3 prompt receives one generation-keyed redacted projection: bounded recent human chat;
each active task's title, state, blocked reason, direct question, merge hold, and publication
references; and the current recovery epoch. It excludes raw logs, provider-native stores, host/home
paths, secrets, credentials, and broker capabilities. A stale projection generation is refused.

For contained Codex turns, the user DBus and runtime directory exist only in the outer `systemd-run` launcher and
are unset before Codex starts. The child receives an allowlisted environment; ambient tokens, API keys, SSH agents,
Git credential helpers, and the L2 capability are absent. A deterministic host canary checks that its inner sandbox
cannot see or signal a known host PID. A model-requested GitHub issue cannot contain synthesized private context: the broker
stores only the exact current chat message, with a title quoted from it, as a private draft. Publication remains
dormant until Phase 1C.4 supplies exact-repository replay and a trusted approval command.

## Polling, restart, and cleanup

Enabled Codex processes expose the persisted worker row and exact managed-unit ownership evidence. Polling follows
the persisted `l2_engine`; a disabled legacy Claude row remains inert evidence and cannot be launched or silently
treated as absent. Read-only inspection may rediscover it only for exact stop/cleanup. A missing or failed enabled
worker without a valid completion is a system fault, not “still running.”

Merging Python changes and restarting the service are separate operations. A source merge can mark a
restart pending, but it never stops the running service by itself.

After changing enabled provider launch, runtime, or session-resume integration, validate source
integration in a disposable installation with one tiny real Codex task through the complete
L3 → L2 → PR → required checks → merge path, then prove no worker remains. This test does not update,
activate, or restart production. Production execution requires a separately authorized activation
and a successful ActivationReceipt.

## Context and prompt-cache evidence

Legacy Claude context/cache records are read-only observation, stop, and cleanup evidence; they do
not describe an active launch or resume mechanism. For Codex, the latest
`turn.completed.usage.input_tokens` drives an approximate percentage against the configured context
window; reported `cached_input_tokens` is retained separately.

A Codex session id records which thread Altitude asks to resume; a legacy Claude session id is
read-only evidence. Documented Codex resume behavior does not prove a cache hit or imply any
undocumented prompt-cache guarantee.
Altitude reports cache-token fields only when the provider emits them and otherwise makes no claim
about cache reuse. Physical worker replacement and provider-session continuity are therefore shown
as separate facts.
