# Baseline system audit

> **Status: non-normative baseline evidence at commit
> `97e11979bdc0814ad5067eab717f999d1c251437`, not active architecture.**
>
> This document records the system observed at the start of the comprehensive-simplification
> proposal. It does not replace [the active architecture](../../ARCHITECTURE.md), change a policy,
> authorize implementation, or describe a future design. “Current” below means current at that pinned
> baseline, not the later candidate branch. Later proposal documents evaluate changes.

## Scope and method

The audit follows a request from project-level conversation through task creation, L2 execution,
optional helpers, publication, verification, closeout, and recovery. It also inventories the files
and external records that participate in those flows.

The current system has a clear product model:

- Burak speaks with one L3 coordinator about the project.
- L3 either answers directly or creates one task.
- One L2 owns that task end-to-end and speaks directly with Burak about it.
- The L2 may work directly or use optional bounded L1 implementers and reviewers.
- Code reaches `main` through an isolated worktree, task branch, pull request, and guarded landing
  path.
- Fault evidence is separate from task creation, while a recovery fuse prevents new work from
  starting during an unsafe episode.

That model was implemented at the pinned baseline. The complexity described here is mostly below it: provider-specific
execution, repeated ownership fencing, duplicated outcome state, broad recovery escalation, and
post-completion maintenance.

## System at a glance

```text
Burak
  |
  | project conversation
  v
L3 turn --------------------------------------------------------------+
  |                                                                  |
  | answer directly                                                  | recovery attention
  | or create one queued task                                        |
  v                                                                  |
request.md + status.json + events.log                                |
  |                                                                  |
  | issue hydration, recovery/WIP/lease/provider/Git gates            |
  v                                                                  |
isolated worktree + branch + one logical L2 attempt                   |
  |                                                                  |
  +---- direct implementation                                        |
  +---- 0..N bounded L1 patches/reviews                               |
  +---- direct Burak <-> L2 steering via resumed provider session     |
  |                                                                  |
  v                                                                  |
guarded commit -> push -> PR -> pinned checks/local suite -> merge    |
  |                                                                  |
  v                                                                  |
report.json -> verifier -> reported/blocked -> close/archive          |
  |                                                                  |
  +---- transcript snapshot                                           |
  +---- worktree/worker cleanup                                       |
  +---- deployment checkout fast-forward / restart-pending            |
                                                                     |
system_fault -> evidence + incident + FYI -> global recovery fuse ----+
```

The daemon's periodic control loop is
[`server.tick`](../../../altitude/server.py#L428). It wakes recovery L3, retries held Codex actions,
polls workers, resumes stranded reports, retries timed resumes, dispatches queued tasks, performs
post-done cleanup, and produces the morning digest.

### User-facing interfaces

Altitude currently has three control surfaces over the same Python library and file-backed state:

- The React single-page application is routed by
  [`web/src/routes.tsx`](../../../web/src/routes.tsx#L23). It exposes Inbox, Projects, project detail,
  task detail, opt-in live session, project L3 chat, and Monitor pages.
- The standard-library HTTP server in [`server.Handler`](../../../altitude/server.py#L502) serves the
  built SPA and JSON/NDJSON endpoints. GET views are assembled by `overview`, `project_view`, and
  `task_view`; POST handlers delegate task/project/L2/L3 mutations to the same production modules used
  by the CLI.
- [`bin/alt`](../../../bin/alt) is the local/operator and agent CLI. It imports the library directly;
  it does not call the HTTP API.

[`web/src/data/api.ts`](../../../web/src/data/api.ts#L37) is the browser's sole API layer. It uses
lenient Zod schemas at server-response boundaries, React Query with 20-second polling, optimistic
mutations for ordinary POSTs, and a dedicated NDJSON reader for streamed L3 chat at
[`streamChat`](../../../web/src/data/api.ts#L477). Polling pauses globally while a chat stream is
open. The task page sends the displayed dispatch/session/engine generation with direct L2 messages;
the server remains the authority that accepts or rejects that snapshot.

## Identities and ownership

The system deliberately distinguishes a logical task owner from physical provider processes. The
current fields live primarily in each task's `status.json`.

| Field | Current meaning | Lifetime |
| --- | --- | --- |
| `slug` | Stable task directory and user-visible identity. | Whole task, including archive. |
| `attempt` | Numeric dispatch generation. | Increments on fresh redispatch. |
| `dispatch_id` | Logical L2 attempt, normally `<slug>-<attempt>`. | Stable across steering/resume; changes on a fresh attempt. |
| `l2_engine` | Provider selected for this attempt. | Stable for the attempt. |
| `engine_model` | Concrete model selected for the attempt. | Stable for the attempt. |
| `session_id` | Provider conversation/thread requested on resume. | Usually stable for Codex; Claude may report a replacement. |
| `agent_id` | Current physical Claude job or Codex worker. | Changes on every physical replacement. |
| `l2_token` | Capability proving logical L2 ownership to the control plane. | Stable for the attempt. |
| `worktree` | Registered task checkout. | Normally stable for the task. |
| `branch` | Registered task branch. | Normally `worktree-<slug>`. |
| `paths` | Declared staging scope and scheduling lease input. | Mutable, including during recovery of a blocked task. |
| `hold_merge` | Explicit exception to merge-by-default. | Mutable by Burak/L3. |

The distinction between `dispatch_id`, `session_id`, and `agent_id` is an essential current
invariant, not duplicate naming. A human message or trusted action must address the current logical
attempt and must not be delivered to a replacement generation. The implementation repeats that
comparison in several places, including
[`tasks.append_task_message`](../../../altitude/tasks.py#L32),
[`dispatch._require_resume_snapshot`](../../../altitude/dispatch.py#L373), lifecycle transitions in
[`tasks.py`](../../../altitude/tasks.py#L259), and Codex action claiming in
[`actions.py`](../../../altitude/actions.py#L56).

## End-to-end current flows

### 1. Project conversation and L3 turns

The browser posts project chat to the HTTP handler in
[`server.Handler.do_POST`](../../../altitude/server.py#L646). The handler rejects an empty message or
an already-busy L3, opens an NDJSON response stream, and calls
[`l3.turn`](../../../altitude/l3.py#L104).

`l3.turn` performs the following current sequence:

1. Acquire a per-project in-process lock, serializing L3 turns in this daemon.
2. Run an optional precheck used by recovery claims.
3. Select Claude or Codex through [`route.pick_engine`](../../../altitude/route.py#L104).
4. Regenerate `STATE.md` from active task `status.json` records.
5. Load `l3.json`, including a separate session record per provider.
6. Rotate the selected provider session when its configured context threshold is reached.
7. Add a bounded handoff containing only human chat missed while the other provider handled L3.
8. Append the current user message to project `chat.jsonl`.
9. Invoke the selected provider.
10. Persist the provider session, context, routing evidence, and assistant/error chat record.

Claude L3 receives [`personas/l3.md`](../../../personas/l3.md) and a limited direct tool contract. It
can use the `alt` CLI to create and coordinate tasks. Contained Codex L3 receives
[`personas/l3_codex.md`](../../../personas/l3_codex.md), read-only durable inputs, a disposable writable
runtime, and [`schemas/l3_action.json`](../../../schemas/l3_action.json). Its result is not executed by
the model process. After containment is empty,
[`l3._codex_turn`](../../../altitude/l3.py#L193) hashes the turn identity and passes the inert envelope
to [`l3_actions.apply`](../../../altitude/l3_actions.py#L364).

The L3 action broker journals one action before side effects. It accepts task lifecycle operations,
merge holds, private GitHub-issue drafts and approvals, incident operations, and recovery hold/clear
requests. A turn may request at most one trusted action. The human-facing `message` remains separate
from the action list, so a conversational answer may have no action.

### 2. Task creation

Task creation converges on [`tasks.new`](../../../altitude/tasks.py#L145), whether called by Claude's
CLI, the Codex L3 broker, the task API, or an operator.

The function currently:

- validates source/provider/model values;
- allocates a unique slug from the title;
- claims the single repair slot first when `source == "recovery"`;
- creates the active task directory;
- writes the exact request to `request.md`;
- initializes `status.json` in `queued` state;
- appends a `new` lifecycle event; and
- regenerates `STATE.md`.

The initial task record includes both requested provider fields (`engine`, `model`) and later-selected
provider fields (`l2_engine`, `engine_model`, `routing`). It also initializes dispatch/session/worker,
worktree/branch, PR, spend, verification, path lease, and merge-hold fields.

Codex L3's `new_task` path performs an additional authorization step in
[`l3_actions._execute`](../../../altitude/l3_actions.py#L267): if the title or request references a
GitHub issue, the exact current user message must authorize that issue number/repository. Claude's
direct CLI path relies on its persona and backend issue validation rather than this current-message
broker check.

### 3. GitHub issue hydration

An explicit GitHub issue is represented first as text in the task title/request. The issue is not
fetched during `tasks.new`. It is hydrated during dispatch by
[`github_intake.ensure_snapshot`](../../../altitude/github_intake.py#L123).

The intake module currently:

- parses one unambiguous issue reference;
- resolves the registered project's GitHub origin;
- binds shorthand references to that repository;
- hashes the exact task request;
- reuses an existing task-owned snapshot only when its schema, request hash, repository, number, and
  content checksum still match;
- otherwise invokes `gh issue view` in the trusted control plane;
- validates the returned number, canonical URL, fields, size, and control characters; and
- writes `github-issue.json` under the task directory.

[`dispatch.build_brief`](../../../altitude/dispatch.py#L170) appends a rendered, explicitly untrusted
issue section to the L2 brief. A second path,
[`dispatch._issue_resume_prompt`](../../../altitude/dispatch.py#L390), hydrates issue context for a
legacy/resumed task when the marker is absent from `brief.md` and the task has not recorded
`github_issue_context_delivered`.

### 4. Queuing, admission, and fresh dispatch

[`server.dispatch_waiting`](../../../altitude/server.py#L321) scans queued tasks in task-list order.
It asks [`dispatch.wip_hold`](../../../altitude/dispatch.py#L899) whether the task may proceed.

Current admission considers:

- the global recovery fuse, with an exception for the one claimed recovery task;
- overlapping narrowed path leases held by running tasks or blocked tasks with pending resumes;
- per-project running-task WIP; and
- machine-wide running-task WIP.

A file-lease or recovery hold is treated as task-specific so the loop may skip that task and reach a
different task, especially the claimed repair. A WIP hold stops the project's queue pass. The daemon
also writes a project `hold.json` display cache.

[`dispatch.run`](../../../altitude/dispatch.py#L241) then repeats eligibility checks around operations
that may take network time. Its current sequence is:

1. Verify `queued`, reject a recent `dispatching` claim, and recheck WIP/recovery/leases.
2. Hydrate any GitHub issue snapshot.
3. Enter the per-project
   [`publication_settlement`](../../../altitude/dispatch.py#L65) lock.
4. Fetch `origin/main` and require the deployment checkout to be clean, on `main`, and exactly equal
   to the fetched remote through
   [`git_policy.fetch_and_require_exact_base`](../../../altitude/git_policy.py#L208).
5. Create or validate `.claude/worktrees/<slug>` at that immutable origin SHA on
   `worktree-<slug>`.
6. Recheck the task under the project lock.
7. Select the provider/model and persist the routing evidence plus a `dispatching` timestamp.
8. Allocate the new `dispatch_id` and `l2_token`.
9. Build `brief.md` and provider `settings.json`.
10. Start the worker through [`engines.start_l2`](../../../altitude/engines.py#L991), with the final
    spawn inside [`recovery.launch_permission`](../../../altitude/recovery.py#L364).
11. Require a concrete worker and provider session identity.
12. Persist the complete running ownership generation through
    [`tasks.dispatch`](../../../altitude/tasks.py#L216).

The task worktree validator requires the exact conventional path and branch, checks every task
commit after the fetched base for the exact `Altitude-Task: <project>/<slug>` trailer, and requires a
clean tree for fresh dispatch. A resume uses the same path/branch/commit checks but permits
uncommitted work.

The `publication_settlement` lock is current behavior added to keep a provenance gate from observing
the interval after GitHub accepts a merge but before self-deploy fast-forwards the primary checkout.
It is shared by fresh dispatch, resume, and Codex publication settlement.

### 5. Brief and execution contract

[`dispatch.build_brief`](../../../altitude/dispatch.py#L170) renders
[`templates/brief.md`](../../../templates/brief.md). The brief materializes:

- one L2 owner's identity, provider, model, worktree, and branch;
- merge-by-default or an explicit `hold_merge` exception;
- the current task path lease and other active leases;
- a provider-specific completion contract;
- a provider-specific direct-conversation contract;
- a provider-specific publication contract;
- selected `Never` statements from the repository's `CLAUDE.md`; and
- the exact task request plus any trusted issue snapshot.

Claude and Codex receive the same ownership model but different control capabilities:

- [`personas/l2.md`](../../../personas/l2.md) lets Claude use scoped Altitude CLI commands, write
  `report.json`, and invoke `alt land` directly. Backend identity, lease, Git, and merge checks remain
  authoritative.
- [`personas/l2_codex.md`](../../../personas/l2_codex.md) lets Codex edit/test ordinary worktree files
  but forbids Altitude mutation, Git metadata mutation, hosted tools, and command network access. It
  must return one inert [`l2_action`](../../../schemas/l2_action.json).

### 6. Engine execution and containment

[`engines.py`](../../../altitude/engines.py) is the provider boundary.

For Claude it:

- builds a controlled settings file with command guards and edit telemetry;
- launches or resumes `claude` background jobs;
- reads the Claude job registry;
- stops/removes jobs; and
- parses session/context/quota evidence.

For Codex it:

- builds an explicit deny-by-default permission profile;
- preflights the effective sandbox roots;
- strips ambient credentials, user-bus state, and unsafe environment variables from the child;
- places the full process tree in an unguessable transient user service/cgroup;
- records the unit before crossing the spawn boundary;
- waits for a stable thread identity;
- validates PID start identity and cgroup emptiness;
- stores stdout/stderr, answer, and worker record under task `l2-engine/`; and
- exposes a normalized worker row only when containment state can be determined.

Detached Codex L2 turns use [`engines.codex_bg`](../../../altitude/engines.py#L880). Synchronous
contained L3/L1/reviewer turns use [`engines.codex_exec`](../../../altitude/engines.py#L1023). Both
feed a provider-neutral subset (`id`, `sessionId`, `state`, `status`, `detail`, usage) to dispatch and
monitoring, although those consumers still branch on provider in several places.

### 7. Direct task conversation and steering

Burak's task-specific message enters through `/api/l2/message` and reaches
[`dispatch.message_l2`](../../../altitude/dispatch.py#L654). The browser supplies the generation it
was displaying. The current sequence is:

1. Take the task's cross-process `.resume.lock`.
2. Load the task and reject a stale dispatch, session, or engine view.
3. Require a current dispatch and provider session.
4. Append the message durably to `conversation.jsonl` through
   [`tasks.append_task_message`](../../../altitude/tasks.py#L32), including the addressed generation.
5. Resume a blocked L2 or replace the running L2 worker with the message as the next prompt.

[`dispatch._resume_session_locked`](../../../altitude/dispatch.py#L413) is the core replacement
transaction. It:

- validates the caller's exact state/dispatch/session/worker snapshot;
- migrates legacy tasks that have no `l2_token`;
- enters the publication-settlement lock and reruns the deployment-checkout and task-worktree Git
  gates;
- rereads task ownership after the network round trip;
- checks the recovery fuse before stopping a healthy worker;
- hydrates missing issue context;
- stops the old worker and proves it stopped where the provider supports that query;
- checks recovery again at the final launch boundary;
- starts the same provider conversation;
- requires a concrete new worker/session;
- atomically binds that replacement only if ownership is still unchanged; and
- stops an unowned replacement if the bind loses a race.

If the recovery hold appears after the old worker is stopped, the exact resume prompt is stored in
`resume_answer`, `resume_prefix`, `resume_exact_prompt`, and `resume_after`; the task becomes blocked
instead of losing the continuation. [`dispatch.resume_due`](../../../altitude/dispatch.py#L690)
later retries due resumes in deterministic oldest-first order.

For an already-blocked task, [`dispatch.resume_blocked`](../../../altitude/dispatch.py#L650) preserves
the original question in `blocked_question`, records a deferred answer when another hold exists,
replaces the worker when admitted, transitions back to `running`, and removes the temporary resume
fields.

### 8. Optional L1 implementers and reviewers

The L2 may invoke zero, one, or several helpers. Claude uses the CLI; contained Codex returns a
`request_helpers` action handled by [`actions._helpers`](../../../altitude/actions.py#L208).

[`l1.start`](../../../altitude/l1.py#L119) currently:

- accepts only implementer or reviewer roles;
- requires the exact current L2 `dispatch_id` and capability;
- rejects launch under recovery;
- serializes helper-name allocation in task `l1/.lock`;
- routes the helper provider independently, subject to quota;
- requires a nonempty implementer sublease within the parent task lease;
- validates the parent branch, repository common directory, fetched origin, parent SHA, and task
  provenance trailers;
- creates a child worktree/branch for a normal implementer run;
- writes the complete prompt and persistent run record; and
- starts a detached `alt l1 _exec` wrapper behind the same recovery launch barrier.

[`l1.exec_run`](../../../altitude/l1.py#L277) invokes the selected provider and always tries to close
the run record. An implementer must leave HEAD at the recorded parent, may change only its sublease,
and returns no commit or PR. The wrapper captures a binary patch in task `l1/`. A reviewer receives
[`personas/reviewer.md`](../../../personas/reviewer.md), is read-only, and returns structured findings
under [`schemas/review.json`](../../../schemas/review.json).

The L2 sees compact result metadata, local evidence paths, findings, and bounded inline patch data.
No patch is applied automatically. This preserves the current invariant that an L1 result is input
to the one owning L2 rather than a transfer of ownership.

### 9. L2 terminal actions

Claude and Codex currently terminate a turn differently.

Claude may:

- send a human reply through `alt task reply`;
- block through the CLI;
- complete a no-code task through `alt task done`; or
- use `alt land` and write a schema-valid
  [`report.json`](../../../schemas/report.json) in the task directory.

Contained Codex returns exactly one of `publish`, `complete_no_code`, `block`, `request_helpers`, or
`continue`. [`actions.process_l2`](../../../altitude/actions.py#L251) first proves containment empty,
claims the exact action in `status.json.pending_action`, rechecks recovery, posts the human message
once, and then applies the action in the trusted process.

The Codex action journal is part of `status.json`, not a separate file. It records the action, current
dispatch/session/worker identity, claim time, and whether the human message was posted. A recovery
hold leaves it durable for [`server.resume_pending_actions`](../../../altitude/server.py#L415), so the
action can be retried without spending another model turn.

Both provider paths use a two-phase no-code completion concept. The current L2 records
`completion_requested` with its ownership generation and digest; only after the physical worker is
settled does [`tasks.finalize_completion`](../../../altitude/tasks.py#L360) recheck that the task branch
has no code changes and archive it. The Codex broker can finalize immediately because it is called
only after contained exit; the Claude path is noticed later by the server poller.

### 10. Guarded publication

Claude calls `alt land` from its worktree. Codex `publish` is applied by
[`actions._publish`](../../../altitude/actions.py#L182), which supplies trusted task authority to the
same [`land.land`](../../../altitude/land.py#L536) function.

The current publication sequence is:

1. Resolve the actual repository root and symbolic branch.
2. Refuse a detached head or protected/base branch.
3. Resolve the task from explicit project/environment/branch naming.
4. Read and enforce the live merge hold.
5. Fence automated publication to the current running L2 dispatch and capability.
6. Fetch `origin/main` and the current remote task-branch tip.
7. Reject any existing task commit without the exact provenance trailer.
8. Resolve the current task lease and reject an empty lease.
9. Parse every changed path, including rename/copy endpoints, and reject anything outside the lease
   before staging.
10. Look up the branch PR before committing; refuse an already closed or merged PR with new work.
11. Stage literal pathspecs, commit with the task trailer, and push.
12. On one specific non-fast-forward path, permit one audited `--force-with-lease` retry against the
    exact pre-commit remote tip.
13. Create or reuse the PR.
14. Pin the GitHub-reported and freshly fetched base/head pair through
    [`land._snapshot_pair`](../../../altitude/land.py#L314).
15. Read checks only while both refs still name that pair.
16. Wait for pending checks up to the configured deadline.
17. If merge was requested and checks pass, merge with GitHub's head-match fence.
18. If the exact pair configures no GitHub Actions workflows, create a synthetic merge-candidate
    worktree, run the full local suite, recheck the pair and absence of checks, then merge.

[`land._checks_state`](../../../altitude/land.py#L274) classifies GitHub buckets as pass, fail,
pending, skipped, or none. A mixture containing a skipped check is not a passing publication gate.
The no-CI path is implemented by
[`land._merge_on_local_suite`](../../../altitude/land.py#L499). The final squash merge uses
[`land._merge`](../../../altitude/land.py#L472), verifies GitHub's resulting PR state, fetches main,
and records the most recent main-branch workflow row when available.

For a merging Codex action, `_publish` holds `publication_settlement` across `land.land` and a
`pull_after_done` call in a `finally` block. The fast-forward therefore runs even when `land` raises
after GitHub may have accepted the merge. This settlement precedes a same-thread correction resume.

### 11. Polling and worker completion

[`dispatch.poll`](../../../altitude/dispatch.py#L926) scans active task records and obtains the current
worker from the persisted `l2_engine` adapter. It writes a derived `monitor/live-<project>--<slug>.json`
snapshot and emits finished items for:

- a report that appeared after an idle block;
- temporary provider capacity;
- subscription-window exhaustion;
- a missing or failed worker with no report;
- a done/exited worker;
- a report plus idle worker; or
- a worker idle longer than the input threshold.

[`server.on_l2_finished`](../../../altitude/server.py#L65) fences the polled snapshot against the live
task generation before acting. It then:

- finalizes a pending no-code completion;
- converts capacity and usage limits into blocked tasks with retry times;
- clears the prior `l3_handled` marker for a new result;
- turns prolonged idle into a user-facing block and FYI;
- turns a dead worker into a block and system fault;
- brokers a contained Codex action; and
- otherwise invokes the report verifier.

Malformed/refused Codex actions are normally returned to the same provider thread as a correction
prompt. If recovery appears after the settled worker is stopped, the exact correction prompt becomes
the durable pending resume described above.

### 12. Report verification

[`verify.verify`](../../../altitude/verify.py#L30) wraps
[`verify._verify`](../../../altitude/verify.py#L39), converting verifier-tool faults into a `fault`
verdict plus a system fault.

The verifier currently:

- requires `report.json`;
- checks the required top-level report keys;
- rejects open review findings on an unblocked report;
- reads each reported PR from GitHub and compares its state with the report;
- checks reported main-run conclusions when it can read a numeric run ID;
- requires at least one PR unless the report is blocked;
- derives post-task signals from deviations, blocks, open findings, retries, and reverts; and
- combines model-reported spend with hook edit counts.

The verified object is stored again in `status.json.verified` by
[`tasks.report`](../../../altitude/tasks.py#L237), with the current attempt number. Verified PR numbers
are also unioned into `status.json.prs`, and the task moves to `reported`.

A report with an explicit block is first moved to `reported`, then to `blocked`. Other verifier
problems still reach the report-closeout path after their verdict is stored.

### 13. Closeout and archival

[`server.report_turn`](../../../altitude/server.py#L194) has two current paths.

The mechanical clean-close path rereads the on-disk report and live task under the project lock. It
requires:

- verifier verdict `ok` with no problems or signals;
- no report decisions, block, FYIs, or follow-ups;
- deploy status `healthy` or `not-applicable`;
- every report PR marked merged;
- at least one well-shaped successful main run;
- every review finding fixed or dismissed;
- live task state `reported`; and
- no live merge hold.

When all conditions hold, the daemon writes a generated digest, moves the task to `done`, archives
the task directory, emits an FYI, and stamps `l3_handled`.

Otherwise it runs an L3 `report-landed` turn with a bounded report excerpt. L3 may complete, block,
resume, or record relevant evidence. `l3_handled` is stamped only after the L3 turn completes.
[`server.resume_stranded_reports`](../../../altitude/server.py#L287) searches `reported` or `blocked`
tasks with `report.json` but no stamp and reruns that closeout. It contains a special promotion from
`blocked` back to `reported` when the stored verification attempt and latest state event prove that a
clean report was blocked from `running` by an interrupted closeout.

[`tasks.done`](../../../altitude/tasks.py#L321) performs the terminal state transition, optionally
writes `digest.md`, snapshots the transcript, and renames the whole task directory from `tasks/` to
`archive/`. `state.task_dir` transparently resolves archived tasks, so later cleanup and event writes
continue to use the archived record.

### 14. Post-done cleanup and self-deploy

The daemon continues to process archived `done` tasks without `status.json.cleaned` in
[`server.tick`](../../../altitude/server.py#L428). It calls
[`dispatch.cleanup_after_done`](../../../altitude/dispatch.py#L1163).

Cleanup is a second, conservative workflow. It currently:

- reads active and archived task records to reconstruct persisted ownership of L2 and L1 paths;
- defers any helper record without a `done` stamp;
- fetches `origin/main` and enumerates Git worktrees;
- refuses a path owned by another unfinished task;
- refuses locked or live worktrees;
- pins task-branch and main refs;
- accepts direct ancestry or proves squash-equivalent path content;
- for squash equivalence, requires a verified merged-PR receipt matching the exact branch and tip;
- rejects ref movement after proof;
- inventories tracked, untracked, and ignored files;
- permits forced removal of a dirty L1 worktree only when its exact current binary diff reproduces
  the one captured patch;
- removes the provider worker only after the corresponding worktree is eligible;
- removes the worktree;
- compare-and-deletes the branch; and
- appends cleanup audit events and notes.

Many inability-to-prove cases are reported through `incidents.system_fault`, which currently also
activates global recovery.

[`dispatch.pull_after_done`](../../../altitude/dispatch.py#L1119) is attempted after normal cleanup and
several fail-closed cleanup exits. For a self-deploy project, it requires the deployment checkout to
be clean and on `main`, fast-forwards it to `origin/main`, and records `restart-pending.json` plus an
FYI when runtime-loaded paths changed. It never restarts the service.

For merging Codex publication, this same function is also called synchronously inside the
publication-settlement lock before any correction resume. The later cleanup path may call it again;
an already-current checkout makes that a no-op.

### 15. Explicit service restart

Source publication and process restart are separate current workflows. A normal L2, landing path,
self-deploy fast-forward, cleanup pass, or recovery clearance does not restart the service.

An explicitly authorized operator runs `make restart`, which invokes
[`scripts/restart_altitude.py`](../../../scripts/restart_altitude.py). The script currently:

1. Reads the installed user service's working directory and environment.
2. Requires execution from that exact deployed checkout and against the same `ALTITUDE_HOME`.
3. Fetches and requires a clean, exact `main == origin/main` checkout.
4. Refuses a dispatch in progress, `running`/`reported` task, blocked live worker, or active L3 turn.
5. Runs locked `pnpm` installation, TypeScript build, and Vite build into a staging directory.
6. Validates that the staged index names present JavaScript/assets.
7. Moves the previous `web/dist` to a rollback location and publishes the staging directory.
8. Calls `systemctl --user restart altitude.service`.
9. Requires a new main PID, a valid `/api/overview`, and an SPA root page within the health timeout.
10. Restores the prior bundle and attempts to restart it if publication or health verification fails.

The tracked [`systemd/altitude.service`](../../../systemd/altitude.service) uses `Restart=on-failure`,
`RestartSec=20`, `KillMode=control-group`, the deployed checkout as `WorkingDirectory`, and
`NoNewPrivileges=yes`. The service owns the daemon and descendants as one control group; provider
session records/worktrees, not surviving processes, are the restart boundary.

### 16. Recovery and incident flow

[`incidents.system_fault`](../../../altitude/incidents.py#L50) is the common current fault entry point.
It keeps one deduplicated record per fault kind per 24-hour window and then, for every call:

1. Updates global `monitor/faults.json`.
2. Calls [`recovery.hold`](../../../altitude/recovery.py#L72).
3. Targets the `altitude` project for recovery attention when it is registered, otherwise the
   originating project.
4. Durably requests one L3 recovery turn before best-effort incident/FYI enrichment.
5. Creates at most one incident per kind/window.
6. Links the incident into the recovery fault record.
7. Emits a system-fault FYI.

`recovery.hold` publishes `monitor/recovery-hold.json` before waiting for the launch barrier. It then
briefly acquires the barrier so any launcher already past permission settles before the call returns.
Queued launchers see the published hold when they acquire the barrier.

The recovery hold contains:

- one episode identity;
- a bounded, kind-deduplicated fault list;
- an optional single repair claim; and
- one L3-attention record with project, revision, claim lease, attempts, backoff, and handled
  revision.

[`server.run_recovery_turn`](../../../altitude/server.py#L359) atomically claims a due attention item,
runs an L3 turn with a current-claim precheck, records bounded retry/backoff after failure, and marks
the exact revision handled after success. A recovery task must be explicitly created by L3 or Burak
with `source=recovery`; only that claimed task bypasses `recovery.dispatch_hold`.

[`recovery.clear`](../../../altitude/recovery.py#L373) accepts only L3 or Burak, requires a reason,
appends a clearance record, removes the active hold, and removes project `hold.json` files that mirror
the recovery reason. It does not restart or unmask the service.

## Persistent artifacts and sources of truth

Runtime state is file-backed under `ALTITUDE_HOME` (normally `~/.altitude`), with provider-native
records outside it where the provider owns them. “Source” below means the current code treats the
artifact as authoritative for that fact; “derived” means it can be reconstructed or is a display/audit
projection.

### Global runtime artifacts

| Artifact | Writer(s) | Current meaning | Authority |
| --- | --- | --- | --- |
| `projects.json` | project API / `alt project` | Registered project paths, approval/merge policy, WIP, provider pins, optional model/test settings. | Source for project registration and project policy. |
| `monitor/recovery-hold.json` | `recovery.py` | Active recovery episode, fault summary, L3 attention, repair claim. | Source for whether launches are globally held. |
| `monitor/recovery-hold.lock` | `recovery.py` | Cross-process serialization for hold record changes. | Synchronization artifact. |
| `monitor/recovery-launch.lock` | `recovery.py` | Linearizes hold publication and the final worker-spawn boundary. | Synchronization artifact. |
| `monitor/recovery-clearances.jsonl` | `recovery.clear` | Append-only explicit clearance audit. | Audit source. |
| `monitor/faults.json` | `incidents.system_fault` | Per-kind occurrence count, recent detail, incident link, project/task. | Fault-deduplication source. |
| `monitor/faults.lock` | `incidents.py` | Serializes fault dedupe/update. | Synchronization artifact. |
| `incidents.jsonl` | `incidents.py` | Global incident index. | Index; incident Markdown is the detailed record. |
| `monitor/statusline-*.json` | statusline hook | Claude interactive context and quota snapshots. | Provider observation; stale after 30 minutes for quota routing. |
| `monitor/quota-codex.json` | quota refresher | Codex duration-labelled quota observation. | Provider observation; stale after 30 minutes. |
| `monitor/live-<project>--<slug>.json` | `dispatch.poll` | Last normalized worker status and idle timestamp. | Derived except for persisted `idle_since`. |
| `monitor/counts-<project>--<dispatch>.json` | edit hook | Edit count for one L2 attempt. | Telemetry evidence. |
| `monitor/hook-faults.log` | command hooks | Faults that hooks cannot send directly to the daemon. | Drain queue, not durable after successful drain. |
| `monitor/restart-pending.json` | `dispatch.pull_after_done` | Deployed runtime code is older than fast-forwarded source checkout. | Current restart-needed marker. |
| `DIGEST.md` | `digest.text` | Generated cross-project digest. | Derived. |
| `digest.wav` | `digest.speak` | Generated speech rendering. | Derived. |
| source checkout `web/dist/` | `make web` / `make restart` staging | Built SPA served by the Python process; hashed assets are immutable-cached and `index.html` is no-store. | Deploy artifact derived from `web/src/`. |
| user service manager state | systemd and restart script | Current service PID, active/sub-state, working directory, environment, and whole-control-group ownership. | External process authority. |

### Per-project runtime artifacts

| Artifact | Writer(s) | Current meaning | Authority |
| --- | --- | --- | --- |
| `<project>/.lock` | `state.project_lock` users | One cross-process writer lock for project task state. | Synchronization artifact. |
| `<project>/.publication-settlement.lock` | dispatch/publication | Prevents provenance gates from observing the post-merge/pre-fast-forward interval. | Synchronization artifact. |
| `<project>/STATE.md` | `state.regen_state_md` | Compact L3 memory generated from active task status records. | Derived; explicitly not hand-edited. |
| `<project>/l3.json` | `l3.py` | Per-provider session identities/context plus legacy top-level display mirror. | Source for L3 resume selection. |
| `<project>/chat.jsonl` | `l3.py` | Human-facing project L3 conversation and errors. | Conversation source. |
| `<project>/events.log` | project event writers | Project-level rotations, incidents, recovery handling. | Append-only audit. |
| `<project>/hold.json` | dispatch queue | Last project-visible WIP/lease/recovery hold reason. | Derived display cache; recovery truth is global hold. |
| `<project>/inbox.jsonl` | `tasks.fyi` | User-facing FYIs, including system-fault and closeout notices. | Inbox source. |
| `<project>/incidents/I-NNN.md` | `incidents.py` | Detailed incident evidence and amendment history. | Detailed incident source. |
| `<project>/incidents.jsonl` | `incidents.py` | Per-project incident index. | Index. |
| `<project>/l3-actions/<sha>.json` | Codex L3 broker | Claimed/completed/failed inert action journal. | Idempotency source for Codex L3 actions. |
| `<project>/github-issue-drafts/<id>.json` | Codex L3 broker | Private issue draft and publication state. | Source for two-step issue publication. |
| `<project>/l3-codex-runtime/` | Codex L3 launcher | Disposable writable working directory needed by the contained CLI. | Runtime scratch, not durable project truth. |
| `<project>/tasks/` | task lifecycle | Active task directories. | Active task namespace. |
| `<project>/archive/` | `tasks.done`/`reject` | Terminal task directories moved intact from `tasks/`. | Terminal task namespace and retained evidence. |

### Per-task runtime artifacts

| Artifact | Writer(s) | Current meaning | Authority |
| --- | --- | --- | --- |
| `status.json` | task, dispatch, server, broker, cleanup modules | Current lifecycle, owner generation, engine/model/routing, worktree/branch, leases/holds, waits, action journal, verification, spend, cleanup. | Primary current task state. |
| `request.md` | `tasks.new` | Exact task request supplied at creation. | Task-intent source. |
| `github-issue.json` | `github_intake.ensure_snapshot` | Immutable checked external issue snapshot bound to request/repository. | External issue-context source. |
| `brief.md` | `dispatch.run` or explicit CLI | Materialized L2 contract and request for an attempt. | Launch input; partly derived from task/project policy. |
| `settings.json` | `dispatch.session_settings` | Claude hooks/environment/autocompact settings for the task. | Launch configuration. |
| `events.log` | lifecycle modules | Append-only operational events. | Audit source; not the current-state source. |
| `conversation.jsonl` | Burak/L2 message writers | Human-facing direct task conversation, generation-fenced. | Conversation source. |
| `progress.md` | L2 | Optional checkpoint for long or blocked work. | Agent-maintained working note, not control-plane truth. |
| `report.json` | Claude L2 or Codex broker | Claimed delivery outcome: PRs, runs, deploy, review, block, decisions, FYIs, follow-ups, deviations, spend. | Outcome claim; GitHub and verifier may contradict it. |
| `digest.md` | closeout | Terminal human-readable digest. | Terminal summary. |
| `.resume.lock` | dispatch/message paths | Cross-process serialization for physical L2 replacement. | Synchronization artifact. |
| `helper-request-*.md` | Codex action broker | Materialized bounded helper briefs. | Helper launch input. |
| `l1/.lock` | `l1.start` | Serializes helper identity allocation. | Synchronization artifact. |
| `l1/<name>.json` | `l1.py` | Helper provider, PID, worktree, parent, sublease, result/evidence. | Helper-run source. |
| `l1/<name>.prompt.md` | `l1.py` | Exact helper prompt. | Helper launch evidence. |
| `l1/<name>.patch` | `l1.exec_run` | Captured implementer binary patch. | Patch artifact. |
| `l1/<name>.stdout/.stderr/.log` | helper wrapper | Bounded local provider/wrapper evidence. | Diagnostic evidence. |
| `l2-engine/<worker>.json` and streams/action | Codex L2 launcher | Durable worker/unit/PID/thread record, native output, stderr, inert answer. | Codex worker source. |
| `transcripts/<dispatch>/manifest.json` | `transcript.sync` | Versioned bundle metadata and checksums. | Portable snapshot manifest. |
| `transcripts/<dispatch>/events.jsonl` | `transcript.sync` | Canonical projection of platform, conversation, and provider events. | Derived durable snapshot. |
| `transcripts/<dispatch>/native-*.jsonl` | `transcript.sync` | Redacted provider-native records retained per attempt. | Durable snapshot of otherwise ephemeral provider evidence. |

### Provider and repository artifacts

| Artifact | Current meaning | Authority |
| --- | --- | --- |
| `~/.claude/jobs/<agent>/state.json` | Claude physical job, session, worktree branch, state/detail. | Claude worker observation. |
| `~/.claude/projects/*/<session>.jsonl` | Claude provider conversation/events. | Provider-native session evidence. |
| `.claude/worktrees/<slug>` | Isolated L2 checkout. | Actual task files and Git worktree state. |
| `.claude/worktrees/<slug>-<helper>` | Isolated L1 implementer checkout. | Actual helper files until patch capture/cleanup. |
| `refs/heads/worktree-<slug>` | L2 task branch. | Git source for task commits. |
| `refs/heads/l1/<slug>-<name>` | L1 implementer branch, expected not to move from its parent. | Git identity for helper worktree. |
| `origin/main` | Fetched base authority for task provenance and publication. | Git base source. |
| GitHub PR | Published branch/base/head state and merge result. | External publication authority. |
| GitHub check rollup and Actions runs | CI observations. | External CI authority, interpreted by several current modules. |
| local `core.hooksPath` plus tracked hooks | Protected-branch and provenance enforcement for arbitrary Git commands. | Repository guard configuration. |

## State machines and wait semantics

The explicit task state graph is defined in [`tasks.TRANSITIONS`](../../../altitude/tasks.py#L16):

```text
queued -> running -> reported -> done/archive
   |         |           |
   |         +-> blocked-+-> running
   |         |           +-> rejected/archive
   |         +-> done/archive (no-code only for L2)
   +--------------------------> rejected/archive
```

`blocked` currently represents several operationally different conditions:

- a genuine question for Burak;
- temporary provider capacity with a retry time;
- provider subscription-window exhaustion;
- a recovery-held exact pending resume;
- a recovery-held trusted Codex action;
- an idle L2 assumed to need input;
- a dead worker;
- a verifier fault;
- a report-declared block, including merge hold/publication incompleteness; or
- an L3 closeout decision.

The distinction is encoded through optional `status.json` fields and reason text rather than separate
states. In particular:

- `resume_after` makes a block an automatic wait and causes it to retain a file lease;
- `resume_answer`, `resume_prefix`, and `resume_exact_prompt` carry the continuation;
- `blocked_question` preserves the original user question while another hold replaces the displayed
  reason;
- `pending_action` distinguishes a trusted Codex action from a user decision; and
- absence of `resume_after` and `pending_action` makes `tasks.decisions` expose the block as user
  input.

## Module responsibility and dependency map

The following map covers production modules directly participating in the audited flow. “Depends
on” lists the important in-repository dependencies, not standard-library imports.

| Module | Current responsibility | Important dependencies and overlap |
| --- | --- | --- |
| [`config.py`](../../../altitude/config.py) | Runtime/source paths, project registry, engine/model defaults, WIP, timeouts, context thresholds, service/TLS paths. | Imported by nearly every module; mixes runtime location, product policy, and provider defaults. |
| [`state.py`](../../../altitude/state.py) | Atomic writes, project lock, task/archive lookup, task/project event logs, generated `STATE.md`. | Depends on config; `append_event` imports transcript and triggers a full snapshot, so the lowest storage layer reaches upward into dispatch/provider projection. |
| [`tasks.py`](../../../altitude/tasks.py) | Lifecycle transitions, task conversation, dispatch binding, report state, no-code completion, archive, spend, inbox/FYI, decisions, merge holds. | Depends on state/config; locally imports engines/transcript/recovery and invokes Git directly for no-code proof. It is both lifecycle core and a coordinator of external concerns. |
| [`l3.py`](../../../altitude/l3.py) | Serialized L3 chat, per-provider sessions, routing, cross-provider handoff, Codex broker integration. | Depends on engines, route, state, l3_actions; regenerates state and writes chat/session files itself. |
| [`l3_actions.py`](../../../altitude/l3_actions.py) | Validates, journals, and applies contained Codex L3 actions. Also owns issue-draft publication and special lease persistence on task resume. | Depends on dispatch, engines, GitHub intake, incidents, recovery, state, tasks; repeats worker liveness and generation checks. |
| [`github_intake.py`](../../../altitude/github_intake.py) | Parse/authorize issue references, resolve project repository, fetch/validate/checksum task-owned issue snapshots. | Depends on config, engine-clean environment, state; called both before launch and on resume. |
| [`dispatch.py`](../../../altitude/dispatch.py) | Worktree factory/validator, publication settlement, brief/settings, fresh launch, steering/resume, lease syntax/scheduling, worker polling, cleanup, self-deploy. | Depends on engines, Git policy, GitHub intake, recovery, routing, state, tasks. Cleanup locally imports land internals; this is the widest current responsibility set. |
| [`engines.py`](../../../altitude/engines.py) | Claude and Codex invocation, environment filtering, sandbox preflight, containment units, worker normalization, stop/remove, context/limit parsing. | Depends on config/state and locally incidents; provider-neutral wrappers coexist with provider branches in callers. |
| [`route.py`](../../../altitude/route.py) | Weekly-first engine selection from normalized Claude/Codex quota observations, with task/project pin support. | Depends on monitor, engines, config, state; stores raw evidence in routing result. |
| [`actions.py`](../../../altitude/actions.py) | Trusted contained-Codex L2 action claim, message, publication, helpers, block/continue/no-code handling and retry. | Depends on dispatch, engines, land, l1, recovery, state, tasks; synthesizes the same report schema Claude writes. |
| [`l1.py`](../../../altitude/l1.py) | Optional implementer/reviewer admission, provider route, sublease/worktree, detached execution, raw evidence, patch/review capture, wait/status. | Depends on dispatch internals, engines, Git policy, incidents, recovery, route, state, tasks; shares worktree and cleanup concepts with dispatch. |
| [`land.py`](../../../altitude/land.py) | Current-publisher fence, scoped stage/commit, provenance, remote-tip lease, push, PR, pinned check/local-suite gate, optional merge. | Depends on dispatch for lease parsing and task resolution support, Git policy, config, state; supplies publication facts later re-read by verify/status/cleanup. |
| [`verify.py`](../../../altitude/verify.py) | Contradiction-check L2 report against GitHub PR/run state, derive signals/spend. | Depends on config, engines, state; its `gh` helper is also reused by status. |
| [`git_policy.py`](../../../altitude/git_policy.py) | Inspect/fetch base, require checkout exactness, validate task trailers, install/verify/enforce protected Git hooks. | Mostly standalone; the same strict base helper is used for task dispatch/resume and deployment-oriented preflight. |
| [`recovery.py`](../../../altitude/recovery.py) | Global hold, launch barrier, fault coalescing, L3 attention claim/backoff, single repair claim, explicit clearance audit. | Depends on config/state; called from dispatch, engines/L1 launch paths, incidents, brokers, server. |
| [`incidents.py`](../../../altitude/incidents.py) | Fault dedupe, global recovery activation, incident allocation/rendering/indexing/amendment, FYI enrichment. | Depends on state/tasks and locally dispatch/recovery; a common fault call affects both evidence and global launch policy. |
| [`transcript.py`](../../../altitude/transcript.py) | Live generation-fenced transcript projection, redaction, portable per-attempt snapshot, checksum validation/export. | Depends on dispatch to locate Codex records and state to locate events/tasks; called upward from state events and task archival. |
| [`status.py`](../../../altitude/status.py) | Fault-tolerant L2/operator orientation: repository, task, hooks, leases, helpers, report, PR checks, exact main run. | Depends on dispatch, Git policy, l1, state, verify; independently aggregates publication evidence. |
| [`monitor.py`](../../../altitude/monitor.py) | Cross-provider session/context/quota view for UI and routing. | Depends on engines/config/state and reads provider-native stores plus derived live/count files. |
| [`server.py`](../../../altitude/server.py) | HTTP/static UI, timer/thread orchestration, worker-result classification, closeout, recovery wake, cleanup. | Imports almost all orchestration modules; contains the highest-level implicit workflows. |
| [`digest.py`](../../../altitude/digest.py) | Cross-project decision/FYI/WIP digest and optional local speech rendering. | Depends on tasks/state/config; speech failure enters incidents and therefore global recovery. |
| [`bin/alt`](../../../bin/alt) | CLI facade for project/task/helper/land/recovery/incident/server operations. | Usually delegates to modules; the `task paths` command directly mutates `status.json` instead of using a shared task operation. |
| [`scripts/restart_altitude.py`](../../../scripts/restart_altitude.py) | Operator-only deployed-checkout, idle-work, staged-web, service-restart, health, and rollback transaction. | Depends on config, dispatch/engine liveness, Git policy, recovery/state, systemd, pnpm, and HTTP health. Intentionally outside ordinary task flow. |
| [`systemd/altitude.service`](../../../systemd/altitude.service) and [`Makefile`](../../../Makefile) | Installed process ownership/restart policy and operator entry points for test, web build, restart, and install. | Define the process boundary around the Python server and all descendants. |
| [`web/src/data/api.ts`](../../../web/src/data/api.ts) | Browser fetch wrapper, response schemas, polling queries, optimistic mutations, L3 chat streaming. | Mirrors server response shapes leniently; is the sole browser/server seam. |
| [`web/src/routes.tsx`](../../../web/src/routes.tsx) and [`AppShell.tsx`](../../../web/src/shell/AppShell.tsx) | SPA route tree, persistent navigation, quota summary, toast host. | Depend on shared overview data and route components. |
| [`Inbox.tsx`](../../../web/src/routes/Inbox.tsx), [`Projects.tsx`](../../../web/src/routes/Projects.tsx), and [`Project.tsx`](../../../web/src/routes/Project.tsx) | Cross-project decisions/WIP/FYIs, project registration, project L3/task/archive/incident/hold views and task actions. | Consume derived server views; do not own lifecycle policy. |
| [`Task.tsx`](../../../web/src/routes/Task.tsx) and [`LiveSession.tsx`](../../../web/src/routes/LiveSession.tsx) | Direct L2 conversation and generation display, task files/events/actions, generation-fenced live provider timeline. | Send task generation to mutation/transcript APIs; server derives all filesystem paths. |
| [`Chat.tsx`](../../../web/src/routes/Chat.tsx) and [`Monitor.tsx`](../../../web/src/routes/Monitor.tsx) | Streaming project L3 conversation; provider quota/session/context display. | Depend on project chat/session records and monitor's normalized observations. |

### Dependency concentration

The current central dependency shape is approximately:

```text
config <- state <- tasks
   ^        ^       ^
   |        |       |
engines   recovery  |
   ^        ^       |
   +--- dispatch ---+
          ^  ^
          |  +---- l1
          +------- land
          +------- actions (Codex L2 broker)

l3 -> route + engines + l3_actions
l3_actions -> dispatch + tasks + recovery + incidents + github_intake

server -> l3 + dispatch + actions + verify + tasks + recovery + incidents
```

There are intentional local imports to avoid import cycles, but current behavior still crosses layer
boundaries. Examples include state calling transcript sync, dispatch cleanup calling `land._changes`,
status calling `l1._alive`, L1 calling `dispatch._norm`, and dispatch calling task-private `_move` for
an atomic deferred resume.

## Schema, persona, hook, and template responsibilities

| Artifact | Current role |
| --- | --- |
| [`schemas/l3_action.json`](../../../schemas/l3_action.json) | Strict contained Codex L3 envelope. One nullable-field action object covers task, issue, incident, and recovery operations. |
| [`schemas/l2_action.json`](../../../schemas/l2_action.json) | Strict contained Codex L2 terminal intent plus human message, helper requests, and optional publication outcome metadata. |
| [`schemas/report.json`](../../../schemas/report.json) | Code-delivery report consumed by verifier and closeout. Claude authors it; Codex broker synthesizes it. |
| [`schemas/review.json`](../../../schemas/review.json) | Findings-only reviewer result with stable tags, severity, file/line, claim, and fix. |
| [`personas/l3.md`](../../../personas/l3.md) | Claude L3 judgment, direct task delegation, recovery, backlog, and CLI behavior. |
| [`personas/l3_codex.md`](../../../personas/l3_codex.md) | Same project role expressed through a contained, read-only, inert-action contract. |
| [`personas/l2.md`](../../../personas/l2.md) | Claude task-owner contract: direct conversation, optional helpers, direct `alt land`, and report/no-code closeout. |
| [`personas/l2_codex.md`](../../../personas/l2_codex.md) | Same task-owner role under filesystem/network/Git restrictions and an inert terminal action. |
| [`personas/l1.md`](../../../personas/l1.md) | Bounded non-publishing implementer/investigator contract. |
| [`personas/reviewer.md`](../../../personas/reviewer.md) | Independent read-only structured review contract. |
| [`templates/brief.md`](../../../templates/brief.md) | Per-attempt materialization of ownership, done, judgment, conversation, scope, isolation, and publication rules. |
| [`templates/pr.md`](../../../templates/pr.md) | Minimal task/branch/base/message/file PR body. |
| [`hooks/guard.py`](../../../hooks/guard.py) | Claude-side command guard for protected operations, task identity, services/ports, and other host commands. It is defense in depth, not backend authority. |
| [`hooks/edit_count.py`](../../../hooks/edit_count.py) | Passive edit telemetry keyed to project/dispatch. |
| Git hooks in [`hooks/`](../../../hooks) | Protected-branch commit/push/reference enforcement and provenance checks for arbitrary Git invocations. |

## Essential current invariants

The following invariants are enforced by multiple current code paths and explain much of the
non-negotiable complexity.

### Ownership and concurrency

1. Exactly one logical L2 attempt owns a task at a time.
2. A stale dispatch/session/worker cannot speak, mutate task state, launch a helper, publish, or bind
   a replacement worker.
3. Steering stops the old physical worker before starting its replacement; two writers must not
   overlap in one worktree.
4. A replacement worker that cannot be bound to the still-current generation is stopped.
5. Provider identity is stable inside a dispatch attempt; cross-provider continuation is not treated
   as a transparent resume.
6. Human task messages and operational/provider logs remain separate artifacts.

### Repository and publication safety

7. Code work occurs in the registered isolated task worktree and conventional task branch.
8. Automated publishers must present the current L2 dispatch and capability.
9. Every task commit after the fetched base carries exactly one matching `Altitude-Task` trailer.
10. Protected base branches cannot be committed/pushed through ordinary agent commands.
11. Publication stages only the task lease and refuses out-of-lease changes before staging.
12. A force push is limited to one audited `--force-with-lease` retry against a previously observed
    exact remote tip.
13. Check/local-test decisions apply to one pinned base/head pair and are invalidated if either ref
    moves.
14. A merge hold is read from live task state and cannot be bypassed by a stale brief.
15. A no-code completion is refused when the worktree is dirty or the task branch differs from
    `origin/main`.

### Helper ownership

16. Only the current owning L2 capability may launch helpers.
17. A write-capable L1 has a nonempty sublease inside the parent task lease.
18. An L1 implementer cannot move the recorded parent HEAD, publish, or transfer task ownership.
19. A captured patch is accepted only after path and parent validation; the L2 chooses whether to
    integrate it.

### Codex security boundary

20. Contained Codex cannot write Altitude state or the Git common directory and cannot use model
    command network/hosted tools.
21. The worker unit is persisted before launch and must be proven empty before any inert action is
    interpreted.
22. Ambient credentials, agents, user bus, and scoped control capabilities are absent from the model
    child environment except for the minimum explicitly allowed launcher context.
23. The trusted broker, not the Codex process, performs task mutation, helper launch, publication,
    issue publication, and recovery changes.
24. GitHub issue publication from contained L3 is a private draft followed by an exact, draft-specific
    approval message and a secret-shaped-content refusal.

### Recovery

25. The recovery hold is visible before the launch barrier drains, closing the publish/launch race.
26. Fresh L2s, resumed L2s, and L1 helpers all cross the same final recovery launch gate.
27. Only one explicitly claimed recovery repair task bypasses an active episode.
28. A recovery fault does not itself create repair tasks or recursive model sessions.
29. A failed recovery L3 turn retains one durable attention item with bounded backoff.
30. Only Burak or L3 can explicitly clear the fuse with a recorded reason.
31. Service restart/unmask is outside ordinary task publication and recovery clearance.

### Audit and retention

32. Current task state is atomically rewritten; lifecycle and conversations are append-only.
33. Terminal tasks move intact to archive rather than losing their evidence.
34. Portable transcript bundles are private, redacted, checksummed, attempt-specific, and not
    automatically injected into future model context.

### Service lifecycle

35. An ordinary source merge or self-deploy fast-forward never restarts the service.
36. An operator restart runs only from the exact installed checkout, refuses active work, publishes a
    validated staged web bundle, requires a new process and healthy API/UI, and retains rollback
    behavior on failure.

## Observed contradictions and complexity pressure points

This section identifies current pressure points only. It deliberately does not select or prescribe a
future implementation.

### Deployment checkout and task checkout are coupled

Task dispatch/resume needs an immutable remote base and a valid isolated task worktree, but both paths
currently call `fetch_and_require_exact_base`, which also requires the deployment checkout itself to
be clean, on `main`, and exactly current. Self-deploy changes that checkout after merge. The current
system therefore has a dedicated publication-settlement lock whose purpose is to hide the
post-merge/pre-fast-forward interval from otherwise strict task provenance gates.

This is visible in the dependency chain:

```text
Codex publish -> GitHub merge -> pull_after_done(primary checkout)
                         ^             |
                         +-- publication_settlement --+
                                                       |
fresh dispatch / resume -> exact-primary-checkout gate-+
```

The invariant “task starts from exact fetched `origin/main`” and the stronger condition “deployment
checkout is already exactly `origin/main`” are represented by the same helper at these boundaries.

### Fault evidence and global admission policy are one operation

Every `incidents.system_fault` call currently activates the same global recovery fuse. Call sites
include containment and launch failures, but also one-task verifier failures, task-worktree
provenance problems, transcript/hook/tick errors, numerous cleanup inability-to-prove cases, and
digest speech failure. The evidence taxonomy (`kind`) does not alter launch impact.

As a result, failures in post-done maintenance or peripheral observability can have the same current
admission effect as inability to prove a Codex containment unit empty.

### `blocked` is both a user state and an operational scheduler state

The single `blocked` state is interpreted by inspecting reason text and optional fields. User
questions, provider capacity, quota reset, recovery-held actions, deferred resumes, dead workers,
verifier faults, and report blocks all share it. Queue, decision, lease, UI, recovery, and closeout
code each reconstruct the subtype independently.

### Path scope has two current semantics

`status.json.paths` is both:

- a publication boundary enforced by `land`; and
- a concurrency lease used by dispatch admission.

The concurrency view removes broad top-level directory claims through `dispatch.narrow`, while the
publication view retains them. Free-form entries accept brace groups and trailing annotations that
are parsed later. An empty path list allows dispatch and worktree edits but prevents `alt land`.
Path updates also have two mutation paths: Codex L3 resume performs strict normalization, conflict
checking, generation fencing, persistence, and rollback; `alt task paths` directly replaces the
list under the project lock.

### Provider roles are shared, terminal contracts are not

Claude and Codex implement the same L3/L2 responsibilities, but their control paths differ:

- Claude L3/L2 can invoke scoped CLI mutations; Codex returns inert actions.
- Claude writes `report.json`; Codex's broker synthesizes it from an action and `land` result.
- Claude no-code completion waits for the poller to observe exit; Codex finalizes inside the
  post-containment broker.
- Claude and Codex workers are normalized at the engine boundary, while dispatch, polling,
  monitoring, transcripts, cleanup, and liveness still contain provider branches.

The paired personas repeat role policy while differing primarily in provider capability text.

### Publication facts are stored and interpreted repeatedly

PR identity/state appears in:

- GitHub;
- `land.land`'s return object;
- `report.json.landed.prs`;
- `status.json.verified.prs`;
- `status.json.prs`;
- optional L1 result records; and
- status's branch-based PR discovery.

Main-run identity appears in the land result, report, verifier query, and status query. The merge
path records the latest main-branch run after merge, while status searches for a run whose `headSha`
matches the newest merge commit.

Check interpretation also differs: `land._checks_state` treats a skipped bucket as a non-pass, while
[`status._PASSED`](../../../altitude/status.py#L18) includes `SKIPPED`. `verify._verify` checks reported
PR merge state and reported run conclusions but does not independently apply land's complete
base/head check classification. Mechanical clean-close then evaluates report and verifier fields
again.

### Report closeout is an implicit second state machine

The combination of task `reported`/`blocked`, `report.json`, `status.json.verified`, `l3_handled`,
`pending_action`, and `completion_requested` determines whether a task is awaiting a worker, verifier,
L3, retry, or archive. `resume_stranded_reports` relies on the latest state event's `frm` value and
the stored verification attempt to repair an interrupted `blocked` closeout back to `reported`.

The clean-close predicate is embedded in `server.report_turn`, separate from report schema
validation, `verify._verify`, landing checks, and status aggregation.

### Automatic cleanup is a second publication-proof system

Cleanup does not merely remove an old directory. It independently reconstructs task/helper ownership,
pins refs, checks ancestry, proves squash equivalence, looks up a matching merged PR receipt,
reproduces L1 patches, queries live providers, inspects ignored/untracked files, and compare-deletes
refs. Its failure paths produce system faults and therefore may affect global dispatch.

Cleanup is also where ordinary done tasks trigger self-deploy, while the Codex merge path now invokes
self-deploy synchronously before a possible correction resume. The same deployment action therefore
participates in both publication settlement and later maintenance.

### Current task facts have several derived mirrors

Examples include:

- provider sessions in `l3.json.sessions` plus top-level last-engine mirror fields;
- current worker data in provider records, `status.json`, and `monitor/live-*.json`;
- recovery state in global `recovery-hold.json` plus per-project `hold.json`;
- task outcome in `report.json`, `status.json.verified`, `status.json.prs`, digest, events, and FYIs;
- fault evidence in `faults.json`, the recovery episode, incident Markdown, per-project/global
  incident indexes, task/project events, and inbox FYIs.

Some copies serve audit, privacy, or stable display needs; others are caches whose staleness and
precedence have to be understood by readers.

### Transcript durability is coupled to every event append

[`state.append_event`](../../../altitude/state.py#L122) calls
[`transcript.sync`](../../../altitude/transcript.py#L199) after every durable lifecycle boundary.
`sync` rereads task events, conversation, and every discoverable provider-native stream, reconstructs
canonical events, rewrites native snapshots and checksums, and copies current report/digest outcomes.
The lifecycle event remains primary if sync fails, but a low-level event append now invokes a
provider-aware archival projection.

### Issue intake spans creation, dispatch, and resume

The task record initially contains only issue reference text. Trusted issue content is fetched later
during dispatch. Resume contains a legacy/missing-context delivery path and an additional digest field
in `status.json`. The Codex L3 broker also performs current-user authorization before task creation,
while Claude L3 has a different trust route.

### Ownership fencing is essential but repeated

The current generation tuple is compared in task messages, lifecycle transitions, path persistence,
resume, Codex action claim, no-code completion, helper launch, publisher authorization, worker finish,
and L3 task operations. The compared subset varies by operation: some use state/dispatch/session/agent,
some add `l2_token`, and some compare a persisted pending-action identity. This repetition supports a
real invariant but increases the number of subtly different race checks.

### Cross-layer private helpers indicate responsibility overlap

Current examples include:

- `dispatch._defer_stopped_resume` calling `tasks._move` to keep state and resume payload atomic;
- cleanup importing `land._changes` to reproduce worktree parsing;
- status calling `l1._alive`;
- L1 and landing calling dispatch's private path normalizer;
- state locally importing transcript after each event; and
- incidents locally importing dispatch timing and recovery policy.

These calls are functional, but they make the true ownership of task state, leases, Git status,
liveness, evidence, and recovery policy less local than the module names imply.

### File-backed state has mixed corruption behavior

`state.read_json` fails loudly on corrupt JSON, and task/report readers often turn that into a fault.
Some append-only readers (`read_events`, L3 chat history, incident indexes) skip malformed records.
`config.load_projects` returns an empty registry on corrupt JSON. The system therefore has no single
current corruption policy across authoritative state, audit logs, configuration, and derived caches.

## Current mechanisms by architectural purpose

This final index distinguishes complexity that directly supports the desired product behavior from
complexity that currently surrounds it. It remains descriptive; it does not decide what a later
proposal will retain.

| Desired mechanism | Current supporting code/artifacts | Complexity attached to it today |
| --- | --- | --- |
| L3 as project-level conversational coordinator | `l3.py`, L3 personas, L3 chat/session files, `STATE.md` | Dual provider sessions, handoffs, legacy mirrors, direct-CLI versus inert-action control. |
| One L2 owner per task | task state machine, dispatch identity, capability, brief/personas | Ownership comparison repeated across modules. |
| Direct Burak/L2 steering | task conversation, resume lock, stop-before-start replacement | Git/deployment/recovery gates are rerun for every steering turn. |
| Flexible direct small-task execution | L2 personas permit direct implementation and optional helpers | Empty/predictive path lease can allow work but stop publication later. |
| Optional bounded L1 help | `l1.py`, L1/reviewer personas, patch/review schemas | Separate worktrees, run records, raw evidence, subleases, provider paths, and complex later cleanup. |
| Isolated code work | registered worktrees/branches and task provenance | Task gates also depend on deployment-checkout exactness and settlement. |
| PR/check/merge boundary | `git_policy.py`, `land.py`, Git hooks, report verifier | Publication facts and check policy are reexpressed by land, report, verifier, status, closeout, cleanup. |
| Merge hold for Burak | live `status.json.hold_merge`, brief, land fence | Hold also becomes report/closeout block information. |
| No-code direct completion | Git clean/diff proof plus post-exit finalization | Provider-specific completion timing and temporary `completion_requested`. |
| Quota-aware provider independence | `route.py`, quota monitors, per-provider sessions | Substantial provider adapter, containment, action-broker, and handoff surface. |
| Contained Codex execution | sandbox profiles, transient units, empty-cgroup proof, inert schemas/brokers | Security complexity is distributed across engines, dispatch, actions, L3 actions, monitoring, and transcripts. |
| Recovery safety | global fuse, launch barrier, one repair claim, L3 attention/backoff | All system-fault kinds currently have the same global admission consequence. |
| Durable/private audit evidence | task/project events, conversations, incidents, transcript bundles | Several facts are copied into indexes, caches, snapshots, FYIs, and terminal summaries. |
| Safe resource reclamation | post-done cleanup and PR/squash/patch proofs | Cleanup reproduces ownership and publication proof and participates in recovery/self-deploy. |

This inventory remains baseline evidence only. Candidate behavior is authoritative only when executable
source, its tests, [the active architecture](../../ARCHITECTURE.md), and
[session lifecycle](../../SESSION_LIFECYCLE.md) agree. Deployment still requires a separate successful
activation receipt.
