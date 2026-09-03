# Low-level flows

This document separates executable `main` flows from unmerged candidate flows. Function names are
stable search keys; line numbers should be taken from the named commit because the candidate
branches changed them substantially.

## A. Executable flows on `main`

### A1. Project chat and L3 action

```text
POST /api/chat
  -> server.Handler.do_POST
  -> l3.turn(project, text, trigger="chat")
       -> route.pick_engine("l3")
       -> append human record to chat.jsonl
       -> Claude path:
            -> engines.claude_print(... resume=<provider session>)
                 -> model may invoke scoped `alt` commands directly
            -> persist provider session/context/usage in l3.json
            -> append assistant record to chat.jsonl
       -> Codex path:
            -> l3._codex_turn -> engines.codex_exec(... resume=<thread>)
            -> validate thread identity, result, and empty containment unit
            -> l3_actions.apply(structured, action_id=<turn identity>)
                 -> journal/claim exact action
                 -> _execute: no-op | task create/done/block/resume/FYI/merge hold
                              | issue draft/approval | incident create/amend
                 -> finish journal entry
            -> persist provider session/context/usage in l3.json
            -> append assistant record to chat.jsonl
```

Durability and effects are split: chat/session state is written by `l3.py`; structured action
idempotence is a separate journal in `l3_actions.py`; task mutations ultimately call task/dispatch
functions. Claude and Codex use different trust boundaries.

### A2. New task and fresh L2 dispatch

```text
L3 action, Claude scoped command, or Project page `action: "new"`
  -> tasks.new -> task status.json = queued; append event; regenerate STATE.md
  -> server.dispatch_waiting (periodic or scheduled)
       -> dispatch.wip_hold + recovery.dispatch_hold
       -> dispatch.run(project, slug)
            -> git_policy.fetch_and_require_exact_base (which fetches origin)
            -> dispatch._task_worktree / _validate_task_worktree
            -> route.pick_engine("l2") and persist routing
            -> build and persist brief/session settings
            -> engines.start_l2
                 Claude: claude_bg
                 Codex: codex_bg in sandbox + transient cgroup
            -> tasks.dispatch binds dispatch_id/session_id/agent_id/l2_token -> running
```

The provider is fixed for the attempt. The task worktree is isolated, but dispatch, Git fetch,
provider launch, and task binding cross several modules and persistence boundaries.

### A3. User message to a running L2

```text
POST /api/l2/message
  -> server.Handler.do_POST
  -> dispatch.message_l2
       -> acquire task-specific _resume_lock
       -> load task; validate displayed dispatch/session/engine when supplied
       -> tasks.append_task_message (durable conversation JSONL)
       -> for running task, call _resume_session_locked directly
            -> validate exact task/worker snapshot
            -> publication_settlement lock around fetch/base/worktree provenance checks
            -> release publication_settlement lock
            -> reread identity
            -> engines.stop_l2_worker(current agent_id)
            -> prove prior worker no longer live
            -> engines.resume_l2(selected provider, existing session_id, prompt)
            -> bind replacement agent/session if task identity is unchanged
            -> stop unowned replacement on a lost bind race
```

This is stop-and-replace steering. It creates a new OS/provider invocation even though the logical
provider conversation normally continues.

### A4. User answer to a blocked L2

```text
dispatch.message_l2 (already holding _resume_lock)
  -> _resume_blocked_locked
       -> if held: retain blocked question and persist resume_answer/resume_prefix/resume_after
       -> otherwise: call _resume_session_locked directly
       -> after successful replacement: tasks.resume -> running

server.tick
  -> dispatch.resume_due
       -> choose due blocked tasks in deterministic order
       -> resume_blocked -> acquire _resume_lock -> _resume_blocked_locked
       -> retry the stored answer when WIP/recovery/provider gates permit
```

### A5. Codex L2 completion and trusted action settlement

```text
Codex worker exits with bounded events, answer, and l2_action JSON
  -> server.tick -> dispatch.poll -> server.on_l2_finished
  -> actions.process_l2
       -> validate schema and exact dispatch/session/worker identity
       -> require contained unit empty
       -> _claim action in task.pending_action
       -> post the action's common human-facing message
       -> one of:
            publish        -> land.land through trusted host
                              -> synthesize report.json from trusted landing result + claimed outcome
            complete_no_code -> record completion request; finalize after worker exit
            request_helpers  -> l1.start, l1.wait, then resume same L2
            block          -> tasks.block
            continue       -> dispatch.resume_session
       -> _clear exact pending action
```

The current `_resume_same` followed by `_clear` is a multi-write sequence. Recovery can retain a
pending action and retry it later.

### A6. Claude L2 completion

```text
Claude worker uses persona + scoped `alt` commands while running
  -> backend applies the identity, clean-Git, lease, provenance, and merge-hold checks relevant to each command
  -> report or landing artifacts are written during the live session

server.tick -> dispatch.poll -> server.on_l2_finished
  -> verify.verify
  -> tasks.report or block
  -> server.report_turn
```

This direct contract is materially different from the inert Codex action broker.

### A7. Helper flow

```text
L2 request or Codex action
  -> l1.start
       -> validate parent L2 capability and recovery launch permit
       -> create run record and optional helper worktree
       -> spawn Claude/Codex helper
  -> l1.exec_run / provider process
       -> capture bounded raw output, patch/findings, PID/job/session metadata
  -> l1.wait / l1.status
       -> project compact result to parent L2
```

Current ownership uses a mix of run records, PIDs, Claude job rows, Codex worker artifacts, and
timestamps. Helpers do not own the parent task, but the implementation has its own lifecycle in
addition to L2 ownership.

### A8. Publication and task completion

```text
Claude scoped CLI or Codex trusted action
  -> land.land
       -> resolve current task/publisher and merge hold
       -> inspect changes against task lease
       -> commit with task provenance trailer
       -> push with remote-tip lease
       -> create/read PR
       -> revalidate exact PR head/base
       -> required remote checks, or isolated local-suite fallback when policy allows
       -> optional merge with exact-head fence

worker completion with report.json
  -> server.on_l2_finished
       -> verify.verify
       -> tasks.report or tasks.block
       -> server.report_turn
            -> mechanically clean report: tasks.done -> archive
            -> otherwise: L3 report-landed turn decides done/block/resume

interrupted report adjudication
  -> server.resume_stranded_reports -> server.report_turn

no-code completion only
  -> record completion_requested
  -> after worker exit: tasks.finalize_completion -> done/archive

server tick after archived completion
  -> dispatch.cleanup_after_done
       -> cleanup owned worker/worktree/branch when proven safe
       -> dispatch.pull_after_done
```

### A9. Fault flow

```text
faulting boundary
  -> incidents.system_fault(kind, detail, project/task)
       -> T.block(task) when it is queued, running, or reported
       -> deduplicated fault/index evidence (one incident per kind per day)
       -> T.fyi to the Inbox
       -> l3.queue_message unless the task is a repair task (source recovery)

server.tick
  -> drain_hook_faults
  -> l3.deliver_queued when the queue is non-empty, L3 is free, and an engine is available
       -> l3.turn(trigger="incident")
            -> Claude: direct scoped `alt` commands
            -> Codex: one bounded action through l3_actions.apply
```

A task blocked before any launch is queued again by `alt task resume`.

### A10. Restart flow

```text
make restart
  -> scripts/restart_altitude.py
       -> verify installed checkout and exact Git provenance
       -> stage web bundle: pnpm install, TypeScript build, Vite build, bundle validation
       -> reverify checkout
       -> refuse dispatch-in-progress, running/reported task, live blocked worker, or busy L3
       -> swap staged bundle and restart the user service
       -> verify new process, API, and SPA; restore prior bundle on failure
```

The command is intentionally separate from ordinary source merge. It refuses a restart unless its
idle and provenance preconditions are proven. It builds/typechecks the web bundle but does not run
the Python or web test suites.

## B. Clean accumulated candidate: `simplify/integration` at `ce7c46a`

The clean candidate does **not** yet replace L2 ownership. Its active behavior changes include
Codex-only autonomous execution, durable L3 ownership, stronger base I/O, and runtime manifest
surfaces. It also adds an exact-web-CI workflow definition that is dormant until it reaches `main`.
Other large additions are incompletely adopted foundations or dormant validators.

### B1. Durable L3 turn ownership

```text
L3 input
  -> persist exact L3 operation intent and recovery epoch
  -> short compare-and-swap claims physical stage
  -> start one contained Codex unit outside the state lock
  -> bind exact provider thread/worker receipt
  -> spool bounded worker output and persist ownership receipts
  -> observe result
  -> prove unit empty
  -> settle one structured action and project session/chat state

restart/retry
  -> reconcile the persisted exact operation and unit
  -> never infer ownership from PID alone or relaunch an ambiguous crossed boundary
```

This implementation is concentrated in the candidate versions of `l3.py`, `engines.py`,
`l3_actions.py`, `recovery.py`, and `server.py`. Candidate `l3.turn` does not pass through the
current HTTP `on_text` callback, so current token-by-token L3 streaming is lost in this implementation.

### B2. Foundations present but not fully adopted

- `contracts.py`: closed pure validators for future records and read models.
- durable `state.py` primitives: stricter JSON, fsync/readback, and writer inventory tests.
- shared physical transition in `engines.py`.
- `legacy_preflight.py`: callable offline validation not adopted into an activation flow.
- `deployment.py`: dormant deployment record and qualification logic.

These increase candidate size while the current writers/readers remain. Review each only with its
actual callers, any proposed adopter, and any code proposed for removal.

### B3. Active manifest and preflight surfaces

```text
bin/alt serve -> server.main -> manifest.runtime_manifest -> startup public manifest
GET /api/manifest -> return startup public manifest
bin/alt manifest -> manifest.runtime_manifest -> print read-only inspection
bin/alt preflight -> legacy_preflight.offline_preflight -> print read-only validation
```

The startup capture and HTTP/CLI inspection paths are active in the candidate. Offline preflight is
callable but is not an activation/cutover gate. `deployment.py` has no production caller.

### B4. Codex-only autonomous capability closure

```text
route.pick_engine(role)
  -> mark providers outside config.AUTONOMOUS_ENGINES unavailable
  -> choose Codex or return an engine hold

fresh L2 dispatch
  -> reject a forced disabled provider with engines.require_autonomous_engine
  -> after intake/Git/worktree preparation, route.pick_engine("l2")
  -> recheck engines.require_autonomous_engine before persisting/launching

L3 turn -> route.pick_engine("l3") -> Codex-owned L3 path or hold
L1/reviewer launch -> route.pick_engine(role) -> engines.require_autonomous_engine -> launch or hold
Claude print/start/resume entry points -> engines.require_autonomous_engine("claude") -> capability error
```

Read/stop/remove paths for legacy Claude evidence remain. This is candidate behavior, not an
approved provider decision.

## C. Saved B3 experiment: `simplify/phase1b3-integration`

The branch contains `7db0a3b` plus preservation commit `ea62105`. Its low-level steering flow is:

```text
append task conversation message
  -> install exact owner successor request
  -> persist stop intent for current owner generation
  -> stop and reconcile current unit to terminal + empty
  -> gate recovery/WIP admission
  -> promote successor to a new owner operation
  -> repeat Git preparation state
  -> create/bind a new physical generation
  -> invoke `codex exec resume` against the same provider thread
```

For a nonterminal current owner, this makes conversation steering a persisted logical-owner
successor transaction and repeats preparation in a new owner operation. A recorded objection says
that is too much machinery for sequential conversation steering. The WIP is not mergeable pending
the module review; the objection is review evidence, not an approved replacement architecture.

## D. Unselected App Server option

The installed Codex supports `turn/steer`, `turn/start`, `turn/interrupt`, and `thread/resume` via
App Server. No Altitude implementation exists. Before choosing it, a module review must answer:

- one shared daemon or one task-scoped process;
- exact sandbox/read/write/network policy parity;
- whether concurrent L2 threads are isolated adequately;
- reconnect and event replay after Altitude or App Server restarts;
- how an accepted steer is distinguished from a lost JSON-RPC response;
- how provider turn/thread IDs map to task records and transcript retention;
- how version/protocol changes are qualified while App Server remains experimental;
- which current engine, owner, resume, cgroup, and recovery code is deleted.

Until those questions have evidence, this remains a review candidate only.
