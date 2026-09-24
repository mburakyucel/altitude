# Altitude CLI

`bin/alt` is the one engine-neutral door into Altitude. `--project <name>` or
`ALTITUDE_PROJECT=<name>` selects a project. Commands that change task state are validated against the
task record and current attempt under the project lock.

This page describes command contracts and examples. Global role responsibilities live in the
[L2](../personas/l2.md) and [L3](../personas/l3.md) personas; Altitude's own policy lives in
[AGENTS.md](../AGENTS.md). [Session lifecycle](SESSION_LIFECYCLE.md#repository-instructions)
describes how those instructions reach each engine.

Start with [setup](SETUP.md) to build the app, register a project with Auto preferences or explicit
pins, and start a conversation. Registration requests daemon-owned setup, including routine Git
guard installation or refresh. [Operations](OPERATIONS.md) covers project settings and service lifecycle.

## Inspection

`alt task status <slug>` and `alt task report <slug> --json` include `token_usage`: the daemon's
persisted local token observation, independent of the report's agent-authored spend. It contains
inclusive input/output and their observed total, optional cache/reasoning subsets, session rows with
engine/attempt/owner or delegated attribution, coverage notes, and checked/observed/finalized times.
These commands do not rescan provider logs. Missing counters remain null or partial; archived tasks
retain their final observation. See [token semantics](SESSION_LIFECYCLE.md#task-token-accounting).

L2 and L3 share task status/show/report/messages/events/list, repository/PR reads, history search,
tool summaries and incident listing. Owner repository, PR and history/tool reads require the launch
project. Mutation permissions stay separate; L2 does not gain global queue, decisions, monitor or
state views. Repository inspection includes only the selected project's fault counters, alongside
the installation's service/activation summary. The inspection commands below print compact text unless `--json` is present:

```text
alt task report <slug> [--json]
alt task messages <slug> [--last N] [--json]
alt task events <slug> [--last N] [--json]
alt queue [--json]
alt repo [--json]
alt pr <number> [--json]
alt l3 tools [--days N] [--json]
alt l3 search <literal-text> [--limit N] [--json]
```

`alt task status <slug> --brief` prints at most ten orientation lines. `alt task status <slug>` and
its explicit `--json` form return the complete status record. `alt task show <slug>` is an alias for
that same record. Status and `alt task list` carry one `waiting` label, which `alt queue` shows as a blocked task's reason: the operator's
turn (open questions or a held review-ready PR), `L2 replying to <operator>` after they wrote,
`paused · fault …`, `stopped by <operator>`, `waiting on L3` or `paused`. `alt monitor` reports quota
and live provider sessions; `alt decisions` reports what waits for the operator, including held
reviews.

`alt l3 tools` groups the shell commands persisted with recent L3 turns. Commands outside the `alt`
door appear first so recurring inspection pipelines are easy to replace with known verbs.

An L3 process is read-only on the deployment checkout and Altitude home. Native command admission names
trusted shims; the CLI, broker and shims authorize their operations. Git log/diff/show include full patches
and historical files, with external diff/text-conversion helpers disabled and output-file options refused.
The altitude user journal is also readable. Runtime shims carry every `alt` invocation plus
`gh pr` view/list/diff/checks, GitHub issue/run inspection, and altitude service status over that project's
same-user altd Unix socket. The socket fixes the project independently of request data. The broker re-applies the
L3 command door, accepts flat task identifiers and stdin rather than `--file`, and binds GitHub reads to the project's
repository; source editing, Git writes, direct GitHub mutations, service control, direct command networking, and cross-project verbs are unavailable.

### Loaded service evidence

The coordinator tool reads `{"kind":"service","unit":"altitude.service"}`. The same record is
available under `service` in `alt repo --json` (coordinator tool:
`{"kind":"alt","args":["repo","--json"]}`). The status shell shim retains its compact process line.
After normal activation, the main-service record includes:

| Field | Evidence |
| --- | --- |
| `pid`, `last_restart`, `invocation_id`, `started_monotonic` | Native process identity and start observations; compare with a prior observation for continuity. |
| `need_daemon_reload` | Native definition-reload requirement: true, false, or null when unknown. |
| `owned_tls_drop_in_loaded` | Exact owned `90-altitude-source-tls.conf` path appears in loaded drop-ins. |
| `owned_tls_drop_in_present` | That fixed source-service drop-in exists on disk, including a symlink; contents and ownership are not verified. |
| `loaded_tls_environment` | Only direct `ALTITUDE_TLS` and `ALTITUDE_TLS_DIR` assignments; `{}` means both unset, an empty string stays empty, and null means unknown. |
| `indirect_environment` | Any environment-file, pass-environment or unset-environment setting exists; true or null leaves effective next-start selection unresolved. |

Unknown booleans remain null, never false. Native read or parsing failures return a fixed `error`
without raw diagnostics and retain evidence already obtained. Unsupported native escaping, malformed
assignments and duplicate TLS keys remain unknown. Unset values do not prove application defaults
or the running certificate. Native `show` omits `EnvironmentFiles` when its list is empty; after a
successful loaded-service read this means no environment files. Other missing properties stay unknown.
These are read-time observations, not an atomic disk/manager snapshot.
A removed disk file can remain loaded with `need_daemon_reload=true`; an unchanged active PID does
not prove restoration. Compare loaded settings, reload state and process identity with the known
pre-change baseline. The read performs no reload, apply or lifecycle action and accepts no property
or file selectors.

### Worker termination and resource evidence

The same coordinator read accepts an admitted Altitude worker unit, for example
`{"kind":"service","unit":"altitude-worker-fixture.service"}`. One fixed native `show` call
returns the existing `unit`, `state`, `substate`, `pid`, `last_restart`, and `error` fields plus
the following evidence for both workers and the main service. Worker reads request no environment
or filesystem paths; main-service TLS fields retain their separate filtering above.

| Field | Evidence |
| --- | --- |
| `load_state` | Native load state; `not-found` means absent or collected, not a clean exit. Null means unknown. |
| `invocation_id` | Native invocation identity when retained; compare with the worker's known invocation. |
| `started_monotonic`, `exited_monotonic` | Main-process start/exit microseconds since boot, as decimal strings; zero/unset becomes null. Compare only within the same boot. |
| `result` | Native service result such as `oom-kill`, `signal`, `exit-code`, `timeout`, `resources` or `success`; `success` alone does not establish an observed exit. |
| `exec_main_code`, `exec_main_status` | Native main-process wait code and status as decimal strings: code `1` means exited (status is exit code), `2` killed, `3` core dumped (status is signal number). Unset code makes both null. |
| `memory_current`, `memory_peak` | Available unit memory accounting in bytes as decimal strings, including zero; native `[not set]` becomes null. |
| `memory_high`, `memory_max` | Unit memory throttle/hard-limit settings in bytes as decimal strings, or `infinity`. These do not describe ancestor limits or host capacity. |

Non-loaded units retain process state and load evidence with an explicit `error`; termination and
resource fields stay null. Missing, unsupported, empty or unrecognized properties stay null;
a successful read with null fields does not prove availability or health. Command failure,
timeout, denied access or an unavailable native manager returns the fixed inspection error and
unknown evidence, without raw diagnostics. There is no alternate host inspection on unsupported platforms.

A retained `oom-kill` result supports native unit-level OOM attribution. A main-process signal 9
or exit code 137 alone does not establish OOM, its origin, or which descendant failed. Memory
snapshots, peaks and cumulative OOM counters do not prove a historical kill or cleared pressure.
Collected-unit history cannot be reconstructed by this read. It enumerates no host consumers,
reads no journal or cgroup files, and changes no unit retention, limits, service state or resume
policy. L3 still verifies the actual cause and recovery before resuming work.

### Explicit provider handoff

L3 or the operator can continue an exited owner as a fresh attempt on another configured engine:

```sh
alt task status blocked-owner --json
alt task handoff blocked-owner --engine <engine> --attempt <observed-attempt> --reason 'Continue the existing authorized work on this engine'
alt task status blocked-owner --json
```

The coordinator transport takes the same argument list:
`{"kind":"alt","args":["task","handoff","blocked-owner","--engine","<engine>","--attempt","2","--reason","Continue the authorized work"]}`.
The CLI persists a daemon request; it launches no worker. The task must have a prior attempt and
worker/session identity and be blocked by worker death (`l2-died`) or a recognized usage limit.
The daemon checks the attempt, worker/session and block again. Live workers, active lifecycle claims,
decision-only waits, reported/completed tasks, explicit task/project pins and unavailable or unconfigured
targets refuse. A changed block or replacement worker invalidates the request. An identical retry reuses
its receipt while the resulting lifecycle still matches.

Successful handoff queues the same task for ordinary dispatch, constrained to the requested engine's
configured options for one fresh attempt. Configuration and availability are checked again at launch;
an unavailable target does not fall through to another engine, and the queue shows that target's
availability or pin conflict using the same routing decision as dispatch. The worktree, uncommitted edits, branch,
PRs, expected files, progress, messages, provider history, questions and merge holds remain. Prior reports remain
history; the fresh attempt establishes its own current verification. A queued receipt
establishes only admission; recovery requires observing a new running attempt. Ordinary `task resume`
keeps its provider session and launch model. Handoff supplies no approval for pending decisions or holds.

Recognized model allowance exhaustion is scoped to that model. Unknown reset times stay unknown and
create no resume timer; only a reported reset schedules one. The routing observation's thirty-minute
expiry permits later availability checks without claiming that an allowance reset. Recovery changes no
project routing, purchases no credits and implements no usage reset. After a repair, L3 verifies its
activation and each owner's current status before requesting handoff; completed owners need no transfer.

### Durable CI recheck

L3 or the operator records one probe against a blocked task in its project. A fault-blocked task gets
a recovery probe; a task blocked on a question while its required check queues or runs gets a wait,
which only observes that check until it is terminal:

```sh
alt task recheck-ci blocked-owner --run 12345 --at 2026-09-10T02:00:00Z --reason 'Verify artifact upload after the external accounting wait'
alt task status blocked-owner --brief
alt task status blocked-owner --json
```

The coordinator tool uses `{"kind":"alt","args":["task","recheck-ci","blocked-owner","--run","12345",
"--at","2026-09-10T02:00:00Z","--reason","Verify artifact upload after the external accounting wait"]}`.
Choose a due time within seven days and an existing CI run in this project's GitHub repository.
The saved `ci_recheck` names its status, next action/time, evidence and delivery receipt. Identical
registration retries return the same receipt; another active probe refuses replacement. L2 asks L3
to register the probe. Registration starts no worker and needs no free worker slot.

At the due time, altd prefers relevant live or fresh completed CI among twenty recent executions
of that workflow, branch, event and PR. Otherwise a recovery probe reruns the selected run once, and a
wait reports the selected run as it is. Freshness uses
the scheduled check time, so evidence from before the intended wait does not satisfy it. Altd saves
the baseline attempt and intent before submission, then reads attempt metadata to reconcile uncertain
writes without blind resubmission. Reruns use their original workflow; they do not adopt a new base
workflow. Each API call has a twenty-second limit; reads run at five-minute intervals and stop at
three failures, twenty-four rounds or two hours after the scheduled time; the exhausted evidence names
the next action. Missing artifacts remain
unverified, including on a green run with a tolerated upload error. Fresh nonexpired, nonempty
artifacts from the observed execution establish successful upload, not another owner's candidate readiness.

Every terminal result, including unchanged evidence or an exhausted probe, reaches the originating
L3 through one retained queue row, with at most two handling attempts and a one-hour
delivery deadline; queue/history IO also has two attempts. Terminal chat evidence repairs a
crash after handling. If provider execution began but no terminal evidence survives, delivery ends
uncertain without replay. Its execution timeout survives daemon exit and stops the process tree
with a five-second grace period. Failed delivery remains visible in status; it starts no new repair task.
Ordinary task controls remain available and invalidate stale probe actions. Questions, faults and
merge holds retain their meaning. L3 verifies actual repair before separately resuming affected owners.
Promise follow-through only after status shows a saved next action and time; a terminal record with
no next action is not scheduled monitoring.
L3 reconciles a concrete next step and gives a concise evidenced heads-up when significant work
remains blocked. Repeated observations of the same completed probe stay quiet.

### Historical evidence search

`alt l3 search "index migration" --limit 10 --json` searches the selected project's human
conversation, active and archived task conversations (including decision/acceptance messages),
report string fields, and completion digests. L3 uses its usual runtime command or coordinator MCP
request `{"kind":"alt","args":["l3","search","index migration","--json"]}`. The socket fixes the
project; L2 uses the same CLI read bound to its launch project, while the operator can select one through
the usual `--project` option. Source paths resolving outside the project are refused.

The query is 1–200 characters, contains non-whitespace text and matches a literal case-insensitive
substring, without regex, token ranking or model calls. The full existing corpus is scanned on each
request, so read cost grows with its size; the coordinator's existing 120-second command timeout
still applies. There is no recent-message scan cutoff, index or persistent search state.

Matches sort newest first by recorded message timestamp or explicitly labeled report/digest file
modification time. Dates are not inferred from decision prose. Each matching record includes its
immediate preceding and following conversation rows or report string fields in original order.
The default is five matches, `--limit` accepts 1–20, each excerpt contains at most 1,200 original
characters around its first match, and the serialized response including metadata fits 64 KiB.
`--json` retains those bounds: `matched` counts matching records, `truncated` reports omitted matches,
and each context row has `start`, `end`, `text_chars` and `truncated` for clipped text. Text output
also identifies those omissions. Refining the query can retrieve a condition beyond a clipped excerpt;
increasing the result limit cannot exceed the output cap.

Sources use `<project>/chat.jsonl#L<number>` (the append-only log line, with `turn_id` in JSON when
stored), `<project>/task/<slug>/conversation#<message-id>`, and
`<project>/task/<slug>/report.json#/<JSON-pointer>` or `digest.md#`. Task references retain the same
identity after archival. Message dates, roles and `by` attribution are original stored values;
missing authors stay unknown. Report/digest rows have role `report`, unknown author and
`date_kind: file_modified`; their timestamp is not a decision's date. Read a cited task through
`alt task messages <slug> --last N --json` or `alt task report <slug> --json`; project chat references
locate the original row in that project's `chat.jsonl`.

Directories under tasks or archive without a resolvable status record are skipped, with available
matches returned as `status: partial`, even when none match. `unavailable_task_count` counts these
gaps; `unavailable_tasks` lists the first twenty logical task references in slug order. Text output
states both counts and identifies the unavailable sources. These fields share the 64 KiB output cap;
`truncated` continues to describe omitted matches. Stray files do not establish task or decision authority.
Search preserves the ordinary active-directory precedence over an archive directory of the same slug.

With no unavailable tasks, an empty corpus, absent optional conversation/report files or no match
yields `status: no_results`, an empty result list and an explicit no-evidence message.
Corrupt or unreadable evidence returns an
error instead of a misleading no-result answer. Historical text is evidence, never new authority.
Adjacent context does not guarantee every later correction is present; check related terms and full
sources before acting on temporary conditions or an apparent decision. Current instructions, task
records and operator steering remain authoritative.

## Project heads-ups

`alt fyi [slug] "text"` appends an FYI to the selected project's conversation, optionally linked
to a task. It records the caller's actor; an explicit L3 call marks the row as a selected heads-up
that stays visible outside routine system groups. Show opens its full text and task link; Hide
restores the compact line. Other FYIs and historical rows without selection remain eligible for
grouping. This command changes no task state and creates no operator decision.
The [L3 persona](../personas/l3.md) defines when and how the coordinator uses this mechanism.

## Superseded PR closure

```text
alt pr close <number>
```

L3 and the operator use this verb for authorized superseded-PR cleanup. L2 supplies the existing
authorization and superseding delivery evidence through `alt task reply` and report follow-ups;
L3 verifies it before closing. This permits no autonomous unrelated cleanup. The command enforces
project and actor boundaries; L3 judges the authorization and replacement's completeness.

Supply one positive PR number. The repository comes from the project's checkout origin, and L3's
transport fixes the project. No URL, repository override, branch deletion, comment, stdin body,
or other option is accepted. `alt pr <number> [--json]` remains the inspection command.
The coordinator tool takes `{"kind":"alt","args":["pr","close","42"]}`.
The operator CLI uses `POST /api/pr/close` with `{"project":"<name>","number":42}`;
an optional `body` must be empty. HTTP fixes actor `operator`; the coordinator fixes `l3`.

The JSON result contains `number`, `url`, verified `state` (`CLOSED` or `MERGED`), and `outcome`
(`closed`, `already-closed`, or `merged`). Already-closed and merged targets return without mutation.
An open target receives one close attempt without deleting its branch, followed by a state read,
including after a failed or timed-out write. A still-open PR fails; an unreadable result is
unconfirmed and names the target URL for inspection before retrying. Each invocation reads current
state; no mutation retry loop runs. A confirmed closed state establishes the result, not which
concurrent actor caused it. Confirmed calls record a `pr-close` project event with actor and the
result fields; failed or unconfirmed calls record no success. Branches, checkout archives, task
ownership and merge holds remain intact. Closure alone proves neither delivery nor activation.

## GitHub issues

```text
alt issue new --title <title> [--label <label>] -
alt issue comment <number> -
alt issue close <number> --reason completed|not-planned
```

L3 and the operator use these verbs through altd; L2 cannot mutate issues directly. L3 files requested
backlog, maintains authorized increment breakdowns, and closes issues for requested closure or verified
completion of authorized delivery, without another routine request. This does not authorize unrelated
autonomous backlog cleanup.
The repository comes from the selected project's checkout origin; issue numbers must be positive
integers, and URLs or repository overrides are refused. The L3 socket fixes the project.

New and comment read their public body from stdin; titles, labels, and bodies must exclude home paths
and private incident evidence; recognizable credentials and tokens are rejected too. Close requires `--reason completed` for finished work or
`--reason not-planned` for work the operator decides not to pursue. Altd maps the latter to GitHub's
`not planned` reason. Close accepts no stdin body, title, labels, or comment option; a separately
requested public explanation uses `alt issue comment` and its existing evidence check. No other issue
mutations or direct `gh` writes are enabled except the create-only upstream report below.

The coordinator MCP tool takes, for example,
`{"kind":"alt","args":["issue","close","42","--reason","completed"]}`.
The operator CLI uses `POST /api/issue` with
`{"project":"<name>","operation":"close","number":42,"reason":"completed"}`;
`body` may be omitted or empty. Both paths apply the same operation validation and return the issue
URL. The socket fixes actor `l3`; HTTP fixes actor `operator`. A successful closure appends an
`issue-close` project event with actor, number, reason, title, and URL; failed calls record no success.

### Incremental issue delivery

L3 maintains the authorized issue breakdown through `alt issue comment <number> -`, with its public
body on stdin, using L2's `alt task reply` and report `follow_ups` for progress and delivery evidence.
The [coordinator instructions](../personas/l3.md) define breakdown contents, increment briefs and
authorization; the [owner instructions](../personas/l2.md#scope-and-evidence) define acceptance and
safe delivery. A task can complete its agreed increment while the parent issue retains outstanding
scope. Altitude's [project rules](../AGENTS.md#roles) retain proposal checkpoints and implementation
constraints. Closure follows the cumulative evidence rules below.

Coordination and subsequent-step tracking follow the
[L3 authority rules](../personas/l3.md#authority-and-coordination). Existing task operations preserve
owner sessions, recovery conditions and holds.

### Delivery-linked issue completion

When cumulative authorized deliveries satisfy an identified issue's full scope and any required
operator acceptance, write `Closes #42` in the completing delivery's reviewed PR body and land with:

```sh
alt land --message "fix: satisfy the issue" --pr-body-file /tmp/pr.md --closes-issue 42
alt land --message "fix: satisfy the issue" --closes-issue 42 --merge
```

`--closes-issue` is a repeatable positive issue number in the project's repository. It asserts the
owner's verified full-scope resolution; it validates the link, and does not insert a keyword or infer
scope from the task. Repeat the flag on every invocation, including a resumed merge. GitHub's PR body
retains the link when reused without a replacement body. A replacement body must retain every intended
closing keyword. Landing checks `closingIssuesReferences` and the repository's actual default branch
after creation/edit and again before merge; missing links, a same-number link to another repository,
or a nondefault target refuse landing. Supply one closing keyword per issue. A retry on an already
merged PR also validates the declared links and routes a missing historical link to L3. See
[GitHub's supported closing relationship](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue).

`Addresses #42` is a mention, not a closing link. Partial work, design-only progress and explicitly
pending operator acceptance use mentions with the remaining scope explained and omit `--closes-issue`.
L3 records delivered PR evidence and remaining scope in the open issue. Rejected or unmerged PRs
do not complete issues. Merge holds and all applicable checks still apply. Verify the issue state
after merge and record the evidence in the task report's `fyi`.

For an already merged delivery with a missing link, L2 sends `alt task reply` and a report `follow_ups`
entry naming the issue, merged PR and proof that all acceptance scope is satisfied, requesting
`alt issue close 42 --reason completed`. L3 verifies that evidence and uses the documented issue verb
in the owning project. An issue umbrella remains open if only a part shipped. This reconciliation
completes authorized work; it grants no automatic backlog intake or general L2 issue-write authority.

### Incident recovery and prevention

L3 owns the next recovery action, preserving original ownership, sessions, scope, failed-check
evidence, machine authority and holds. It verifies recovery before resume and separately assesses
recurrence, including role/procedure failures when a tool correctly
refuses an action. For each newly investigated actionable Altitude defect, promptly create/reuse a
sanitized upstream issue and record the prevention owner/next action or concrete reporting failure.
Establish current relevance and underlying cause before selecting repair. Prefer a simple shared
correction for evidenced related failures; explain a narrow fix when generalization adds complexity
without value. Old incidents may be stale and do not authorize historical repair.
An evidence-backed non-defect/no-change disposition is valid. Use the existing incident fields:

```sh
alt incident amend I-20260908-123456 --status watch --reason "Recovery verified; prevention pending" \
  --evidence "Recovered: original owner continues. Prevention: https://github.com/example/altitude/issues/42; development coordinator owns triage; delivery pending."
alt fyi "The task is unblocked. Prevention is tracked at https://github.com/example/altitude/issues/42; the development coordinator owns triage."
```

These are fictional examples; use the actual local incident and verified public link. Keep concise
recovery and prevention evidence together, including delivery/effectiveness verification when known.
`watch` retains pending reporting, delivery or effectiveness; `closed` records verified prevention
or the reason no change is warranted. Closure alone proves neither. `alt incident list` returns
current Markdown status/evidence/cause; unavailable records stay explicit. Coordinator state shows
the latest five incidents not closed, with evidence limited to 600 characters and marked truncation,
separately from upstream report outcomes. Read the full list for omitted evidence.

Give one meaningful recovery/follow-through FYI and update it only when evidence or action changes.
Unchanged repeats reuse confirmed links and remain quiet. Failed publication retains a concrete
next action; uncertain publication is reconciled by verified linkage without duplicate creation.
The receiving development coordinator promptly triages under its own project authority; public
reporting grants no cross-project task control. Historical visibility authorizes no bulk backfill.

The [L3 next-action contract](../personas/l3.md#authority-and-coordination) uses these existing records
and verbs. A justified wait names its dependency or finite observation, owner, trigger and the decision
its result informs. An incident marked `watch` alone schedules nothing. When historical evidence is
irretrievable, record that limit, use retained evidence for specific remaining questions and expose
any capability or authority gap through `alt task escalate <slug> --question '…'`. Escalation keeps
the fault reason and merge hold; it supplies no recovery authority. An already-authorized capability
correction follows the existing task/PR path. A changed operational contract requires its decision
before execution. The owner investigates as part of its task: relevant, non-invasive diagnosis
proceeds iteratively on its judgment without a plan to approve or per-round permission. A question
arises only for access the owner lacks, a material machine or service change, unapproved spend, a
live-provider test or an explicit restriction. Diagnosis does not expand fix scope, machine access or
merge permission. Unavailable history and unrelated successful checks never establish recovery.

For fictional Atlas tasks whose original worker units were collected, the procedure is:

| Scenario | Evidence and executable next step |
| --- | --- |
| Launch failed before a session or worktree exists | Retain the original task and failed launch record. Inspect retained launch output and supported worker evidence for a specific unresolved question. If recovery cannot be established, L3 escalates on that task only the concrete boundary, such as an exception to verified-recovery-before-resume. Do not create a replacement owner. |
| Validation failed on an existing session and held PR | Retain the exact failed candidate, check logs, session and hold. Review retained failures for an actionable cause. If supported reads cannot establish recovery, the same owner investigates the failing check. Machine changes or access the owner lacks need their applicable authority; prior passing checks and a collected owner unit do not explain failed descendants. |

Neither case has a meaningful timer merely because time can pass. `recheck-ci` can rerun a failed run;
it is not passive host observation. L3 reconciles the owner's findings; an inconclusive result informs
the owner's next step. Automatic fault retries, sandbox bypass, service control, live-provider tests
and check bypass gain no authority from diagnosis. Full delivery checks and all holds still apply.

### Upstream Altitude defects

```sh
alt issue upstream --incident I-20260908-123456 --title "Fictional resume defect" - <<'JSON'
{
  "expected": "The fictional Atlas task resumes once.",
  "actual": "The task remains blocked.",
  "reproduction": "Create a toy project, block its task, then request resume.",
  "version": "example-build-123"
}
JSON
```

Use the actual local system incident ID from `alt incident list`. The incident ID is tracking
metadata and is never added to the public body. Reports unrelated to a system incident can omit it.

L3 can explicitly report an Altitude defect from any managed project. The command creates only a
GitHub issue in the installation's Altitude issue repository. After confirmed creation or verified
linkage, it sends one fixed issue-link notification to registered local `altitude` if that project's
Git origin matches the upstream repository. Without a matching project it remains issue-only. It does
not fix Altitude, create a recovery task, or copy local incidents or conversations. Altitude's
operator/coordinator selects implementation separately. Ordinary recovery for the project's own
problems stays in that project. Reporting does not enable automatic issue-to-task intake.

Stdin is a JSON object with nonempty `expected`, `actual`, and `reproduction` strings, plus optional
`version` (an Altitude release or commit if known; omission publishes `unknown`). Other fields are
refused. Author a small fictional or redacted reproduction, never paste operational logs, incident
evidence or conversation transcripts. All public fields, including the title and version, pass the
same home-path, private-evidence and credential checks as ordinary issues. These checks catch
recognizable patterns; the author must still redact private or security-sensitive details.
Use `[REDACTED]` for a credential placeholder.

The product target seam is `ALTITUDE_UPSTREAM_ISSUE_REPOSITORY` in **altd's environment**, an
operator-configured GitHub `owner/repository` or GitHub origin URL. Unset uses the installed
Altitude source checkout's GitHub origin. A fork installation can configure its intended upstream
there. An invalid or unavailable target fails with configuration instructions, with no fallback to
the reporting project's repository. L3 cannot change this setting through the reporting verb;
neither request fields nor the caller's environment choose the target. No `--repo`, `--target`,
`--project`, file attachment, label, issue number, upstream comment or closure is accepted.

The coordinator MCP tool receives argument arrays and a JSON string on stdin:

```json
{"kind":"alt","args":["issue","upstream","--title","Fictional resume defect","-"],"stdin":"{\"expected\":\"Resume once\",\"actual\":\"Still blocked\",\"reproduction\":\"Create a toy project, block its task, request resume\"}"}
```

The runtime `alt` shim uses the same project-bound broker. The operator CLI uses `POST /api/issue`
with `project`, `operation: "upstream"`, `title` and `body` (the same JSON string); L2 issue writes
remain denied. Success returns the issue URL and writes one `issue-upstream` event with actor,
title and URL in the calling project's log, without the report body. Failures name configuration,
GitHub authentication/access, or an unconfirmed result without echoing GitHub's private error output.
Use the returned full URL or `owner/repo#number` whenever mentioning the upstream issue in replies,
briefs or summaries. Bare `#number` identifies the calling project's repository; do not infer an
upstream repository for ambiguous historical text.

With `--incident`, the outcome is durable: `confirmed` carries a URL; `missing`, `failed`, and
`uncertain` carry an actionable reason. `alt incident list` and project API incident rows include an
`upstream` object with status, URL and reason, plus actor, timestamp and source incident for recorded
outcomes. Coordinator state lists open incidents with their link or gap; fault and restart messages
carry no incident history. Inspect the full list for closed incidents and reasons.

The separate `upstream.notification` object reports `queued` (accepted by the local queue), `received`
(claimed by its consumer), `unavailable` (no matching registered development project), or `failed`
(local project/queue access failed), with a target/message ID or an actionable reason. These outcomes
also appear in coordinator summaries and source-project `upstream-notification` events. The receiving
project's existing queue/chat shows the fixed public link without a source conversation, private
evidence, task instructions, or receiving task association. Notification cannot create, reuse, resume,
or coordinate its tasks; receiving L3/operator chooses any work under its own authority.

Notification deduplication uses receiving project plus normalized full issue URL across source projects
and restarts, retaining consumed receipts in the existing project event log. `received` records the
queue claim, not model completion; an exit after dequeue retains the queue's ordinary delivery limits.
Queue failure never changes confirmed publication to failed publication. For a tracked report, repeat
the confirmed incident command to retry notification only; no second issue is posted. An untracked
report still returns its successful URL and leaves a local FYI on queue failure; do not repeat creation.
No historical notification backfill runs when a development project is registered later.

Confirmed links are reused for the same incident without another creation, including after restart.
L3 investigates whether separate incidents share a cause, then uses `--url` to attach a matching
issue or the ordinary create form to report a different defect. A shared fault kind does not select
an issue. Outcomes remain attributed to the source incident in their receipt, including existing
confirmed and uncertain publications; no historical bulk repair runs. The daemon saves uncertainty
before publication: a timeout, interrupted request, nonzero GitHub exit or unconfirmed response may
have created the issue and prevents another
create. Local validation, configuration or executable failures are `failed`; a caller can explicitly
try again after correcting those prepublication failures. No outcome triggers an automatic retry.

The operator checks existing upstream issues after uncertainty. To attach a known match:

```sh
alt issue upstream --incident I-20260908-123456 --url https://github.com/example/altitude/issues/42
```

The URL must name an issue in the configured product repository. This form accepts no title or body;
altd verifies the URL with a GitHub read before recording it. Failure leaves the earlier status intact,
and an existing confirmed link cannot be replaced with a different URL. A late publication result
cannot overwrite a concurrently verified link. HTTP uses the same optional `incident` and `url`
fields. L3's general GitHub read broker remains project-local; this operation reads only the supplied
upstream issue. Missing status does not authorize publication. Historical incidents without a tracked
fault identity remain visibly missing; bulk publication/backfill and uncertain-result creation retries
require a separate operator decision and are not supported by this operation.

## Task file lists

Task `paths` lists expected files for coordination; the objective and explicit exclusions define
the owner's authorized scope, including newly needed files.
`alt task paths <slug> <comma-separated-paths>` lets L3 or the operator replace this advisory list;
status and the `paths` event retain the plan.

Select files or hunks with `git add`, inspect `git diff --cached`, then run `alt land`. Landing
commits exactly the selected index. Unstaged and untracked edits stay in the worktree, including
working versions that differ from staged content. Before publication, review all outgoing commits
and the complete PR diff for authorized scope and private content, including intermediate changes.

## Dirty-checkout recovery

Ordinary dispatch and resume preserve deployment staged, working and untracked content. Fresh task
worktrees use freshly fetched `origin/main`; owned resume validates its existing worktree without a
remote fetch or deployment gate. Deployment and activation failures remain separately visible.
Preservation is an explicit recovery action, independent of otherwise valid isolated work.

`alt task preserve-checkout <slug> --reason '…'` asks altd to preserve the selected project's dirty
main checkout for an existing blocked task that has never launched. It is available to the
operator and project-bound L3, and denied to L2. It needs neither a worker nor a free task slot.
The CLI queues a durable request; `alt task status <slug>` shows `daemon_request.status`, its
outcome note, and `checkout_archive: {"branch": "archive/checkout-<request-id>", "sha": "<SHA>"}`
once the snapshot is saved. `alt task events <slug> --json` includes the request's actor/reason and
the `checkout-preserved` branch and immutable SHA. Branches are local, uniquely named and never
overwritten, automatically pushed or deleted. Retention ends only with explicit operator removal.

L3 or the operator performs these steps for a fictional `example` project with dirty main exactly
at `origin/main`. L3 uses its project-bound coordinator transport without a project flag; the
operator's shell selects the project by inserting `--project example` after `alt` in these commands:

1. Stop editing that checkout during preservation. Inspect `alt repo` and the
   intended task with `alt task status reconcile-edits`. Use an existing
   unlaunched blocked task; `--source recovery` controls fault notifications, not Git privileges.
2. Run `alt task preserve-checkout reconcile-edits --reason "Preserve existing edits for review in the task PR"`.
3. Wait for `alt task status reconcile-edits` to show the completed request and
   its `checkout_archive` branch and SHA. A successful preservation leaves clean main at `origin/main`
   and keeps the task blocked. Snapshot commits live only on the local archive branch.
4. Give the owner the branch, SHA and authorized reconciliation scope with
   `alt task message reconcile-edits "Inspect archive <branch> at <SHA>; apply the reviewed snapshot in your task worktree using the CLI recovery procedure and deliver through your PR."`
   For a faulted task, this L3 message records scope without waking it; request
   `alt task resume reconcile-edits --reason "Archive and reconciliation scope verified"` separately.
   The owner inspects the snapshot before applying authorized changes and selecting what to publish.

In the owner's isolated worktree, inspect and apply using the recorded immutable SHA:

```sh
git log --oneline -2 <SHA>
git diff --stat <SHA>~2 <SHA>
git diff <SHA>~2 <SHA>       # complete working changes
git diff <SHA>~2 <SHA>^     # staged content, including versions overwritten or deleted in working files
archive_patch="$(mktemp /tmp/checkout-review.XXXXXX)"
git diff --binary <SHA>~2 <SHA> > "$archive_patch"
git apply --check --index "$archive_patch"
git apply --index "$archive_patch"
```

Review scope before applying; inspect individual staged versions
with `git show <SHA>^:<path>`. The archive has two ordinary commits: the staged checkpoint on
original main, then the working snapshot. Applying the net diff preserves final file content and
flattens staging intent; staged-only versions remain available in the parent commit. Reconcile
conflicts in the task worktree and commit only reviewed, authorized changes through the normal PR path.

Git preserves staged and unstaged content, tracked deletions and untracked files. Ignored files
remain in place; an ignored file obstructing a tracked path refuses cleanup with the archive retained.
Dirty submodules and changed gitlinks/nested repositories refuse preservation.
Off-main, ahead, behind or diverged checkouts are refused without preservation. This command does
not reconcile local commits. L2 never gains permission to write the deployment checkout.

If cleanup fails, the branch and SHA remain recorded and the task stays blocked.
If altd exits while the request executes, it refuses automatic replay and names the request ID;
inspect `git log archive/checkout-<request-id>` and task status/events before deciding whether to
retry with a fresh reason. The branch can exist even if interruption prevented writing the task
receipt. Main may be partially cleaned; preserve the archive and inspect both before proceeding.
Inspection uses L3's existing read-only Git door and grants no checkout writes.

Legacy `preserved_checkout` string SHAs identify retained stashes. They remain unchanged even when
the task creates an archive; inspect with `git stash show --include-untracked <SHA>` and apply with
`git stash apply --index <SHA>` in the owner's worktree. For an interrupted legacy request, inspect
`git log -g --format='%H %gs' refs/stash` for its request ID. Existing stashes are never silently
deleted or converted. Never use `stash pop`, `stash drop`, `reset --hard` or `clean` as a recovery
shortcut; archive or stash removal requires an explicit operator action.
An unlaunched task with a saved `main-unpushed` fault can requeue through explicit reason-bearing
resume independently of deployment recovery. Restart alone does not recover or discard edits.

For upstream defects, the originating L3 checks public delivery evidence and local observations
that the actual cause is gone before `alt task resume <slug> --reason '<verified fix and observation>'`.
Notification receipt, issue closure and unrelated restart do not establish repair. Saved unchanged
blockers do not generate repeated recovery nudges; new affected tasks, new blockers and changed
details remain actionable. `alt task message` from L3 to a faulted task records non-waking discussion;
use the explicit reason-bearing resume after verification. Operator messages retain their ordinary
discussion wake. Resume preserves the original attempt/session/model and the guarded landing path,
including any merge hold.

## Project lifecycle

`alt project remove <name>` is operator-only. Removing a project from Altitude means detaching its
L3: it unregisters an idle project and stops coordination. Finish or reject queued, running,
blocked and reported tasks first, and wait for live workers, launch/resume operations, L3 turns
and task processing to finish. A refusal leaves registration and work intact.

The repository, remaining worktrees, conversations, provider sessions, task archives and queued
L3 messages stay on disk. `alt project add <name> --path <same-repository>` attaches L3 again and
restores that history; queued messages become eligible for delivery again. The same removal is in
the project's **More actions** menu (`POST /api/project/remove` with `{"name":"<name>"}`).
The existing folder-add flow (`POST /api/project/add`) attaches L3 again. `alt l3-reset` remains a
separate conversation reset; it marks a session for rotation without disabling coordination.

### Project setup and guard recovery

Inspect current setup or request supported repair for a registered project:

```sh
alt project setup example
alt project setup example --repair --reason 'Repair the observed Git guard configuration'
alt project setup example
```

These commands use the running daemon. The read reports current checks, active source, operation
state and affected tasks. `--repair` requires a reason and requests bounded programmatic setup;
inspect again until the operation has a verified result. Healthy configuration is reused.
The operator and that project's L3 can request repair; L3 uses its existing command broker,
so no worker launch or checkout-write privilege is needed. L2 cannot request this operation.
The UI's **Setup**, **Check again** and **Retry** expose the same observations and operations.

Routine repair installs missing guards when no custom hook owner exists and refreshes recognized
Altitude guards to the active source, including stale paths from skipped upgrades. Saved
task-worktree overrides receive their own observed rows and repair. Foreign,
inherited and default-directory hooks stay intact. Only the operator can choose **Use both hook
sets** in the UI after inspecting a supported integration; the choice is checked against that
configuration before writing. Unsupported hook managers require discussion. The legacy
`install-git-guards` command is not the coordinator recovery route.

For a source-update guard failure, the originating project's coordinator:

1. Inspects the active source and Setup result with `alt project setup <name>`, alongside the
   affected task's reason and delivered fix evidence. A merged PR alone does not prove activation.
2. Requests the scoped repair above. If refused, records the exact refusal; neither another task
   nor a direct checkout mutation bypasses it. Custom-hook integration waits for the operator.
3. Rechecks setup and its verified guard result. Confirms the active fix's enforcement evidence
   and that the observed stale path or other actual cause is gone. Unknown or failed results stay
   unresolved; notification delivery and an unrelated restart do not prove repair.
4. Resumes only tasks blocked by that fixed cause using
   `alt task resume <slug> --reason '<verified fix and local observation>'`.

This preserves sessions, worktrees, questions and merge holds. No project detach/reattach, task
rejection or manual service lifecycle action is part of recovery. Each coordinator verifies its
own project; delivery in this repository does not establish another project's recovery.

### Concurrency limits

All projects share one limit, defaulting to **80 running L2 tasks across the machine**. Inspect
its active value, default, explicit override and pending/completed request:

```sh
alt machine show
alt machine set --wip 120 --reason 'Allow more parallel tasks across this machine'
alt machine set --unset-wip --reason 'Restore the machine default of 80'
```

`machine show` reports the effective machine `wip`, its `override` (null when inherited), `default`
(80), and `request.status` showing whether a change is still `pending` or has completed.
Project registration and settings expose no concurrency cap. Stored project `wip` overrides
impose no limit.

Machine caps accept positive integers with no fixed ceiling of 80. Zero, negatives, fractions,
booleans and nonnumeric values are rejected. Reset removes the override and restores 80.

The operator runs machine commands from their own terminal. Neither L2 nor L3 can change the
machine cap. A nonempty reason is required. The CLI queues one durable daemon request;
altd applies it on its next tick, without a PR, service restart or free task slot. Repeat inspection
to confirm `request.status: done` and the active value. Identical retries reuse the existing receipt
and audit event while the stored setting still matches. The machine override is in
`$ALTITUDE_HOME/settings.json`; requests and audit events use the operational settings protocol.
Use these commands to change settings.

Lowering the cap lets running work continue. Fresh and resumed launches wait until the aggregate
running count falls below it. Blocked tasks consume no capacity. Eligible ready resumes take available
capacity before fresh launches across all projects. Operator waits, faults without verified recovery,
future due times, busy project setup and unavailable engines reserve no slots and do not hold eligible work. The limit
counts running tasks, excluding engines' native helpers and L3 turns.
`alt task status` reports pending admission waits in `wip_hold`; running tasks have none.
Its `hold` field stays empty because project queue observations are not individual task holds.
`blocked_reason`, `waiting_on`, `fault` and `hold_merge` retain the task's actual recorded holds.

### Automatic routing preferences

The operator and a project's L3 can set that project's shared Auto policy for L3 turns and fresh
L2 attempts through the existing operational settings command:

```sh
alt project set example --routing 'codex,claude:fable>claude:opus' --reason 'Prefer these peers; keep Opus as fallback'
alt project set example --unset-routing --reason 'Restore default Auto preferences'
```

Quote the policy so the shell does not interpret `>`. Options use `engine[:model]`; commas tie
options, and `>` starts a lower-priority tier. An omitted model uses the engine's role default.
Empty tiers/options, duplicate options and unknown engines are rejected. The daemon applies the
request on its next tick and records actor, reason and outcome. No PR, restart, UI setting or free
task slot is needed. `alt project list` shows the stored override; `alt monitor` explains each
project's L3 and fresh L2 choice, including its tier, skipped options and unknown quota.

Auto uses the highest tier with an eligible option. Only comparable, named seven-day account
readings select by headroom within a tie; short windows only determine availability. Unknown or
incomparable weekly readings use configured tie order. An L3 engine/model still eligible in that
tier stays unless a competing option has at least fifteen percentage points more weekly headroom.
Default preferences tie Codex's default model with Claude Fable and put Opus below them;
`ALTITUDE_PRIMARY_ENGINE` chooses only the default tie order. A project override replaces the
whole preference list, and `--unset-routing` restores those defaults.

The daemon collects account quota every five minutes without an interactive session. The native
headless usage reader requires CLI 2.1.277+ with a subscription login and structured live account
rows; unavailable or failed reads remain unknown. Thirty-minute-old observations are stale. Monitor
explains unknown quota, and Auto continues to use configured tie order until weekly readings are
comparable. See [source compatibility and verification limits](SESSION_LIFECYCLE.md#context-and-prompt-cache-evidence).

| Intended preference | `--routing` value |
| --- | --- |
| Claude-only account with Opus available | `'claude:opus'` |
| Fable unavailable; prefer Codex with Opus as fallback | `'codex>claude:opus'` |
| Fable and Codex tied; prefer Fable when weekly quota is unknown | `'claude:fable,codex>claude:opus'` |
| Same tie; prefer Codex when weekly quota is unknown | `'codex,claude:fable>claude:opus'` |
| Prefer Opus first, then Codex | `'claude:opus>codex'` |

A missing CLI, exhausted window or known access rejection excludes the affected options; unknown
access or quota remains eligible. No plan name implies model entitlement, and a shared account
meter does not supply separate Fable/Opus allowances. If Fable rejects access while Codex is absent,
the default policy can try Opus after confirming no output or tool effects occurred. A model
rejection excludes that model for thirty minutes; an authentication rejection excludes the engine
for thirty minutes. A rejection of an unresolved native default is scoped to that role, since
the two launchers can use different default models. Each alternative is tried at most once per dispatch or turn. When none is
eligible, the explanation identifies installation, authentication, reset or configuration actions.

Preferences are distinct from explicit pins. `alt task new --engine claude --model opus …` pins
one task; project `--l2-engine`/`--l3-engine` pins, the composer's L3 engine choice and
`alt chat --engine …` take precedence over Auto and never silently fall back. An explicit model pin
also remains strict. Changing preferences does not unpin them or change a running L2: resume keeps
that attempt's engine, provider session and recorded launch model. A quota fallback is a recorded
fresh attempt from `progress.md`. L3 retains a separate provider conversation per engine, including
when its chosen model changes; crossing providers supplies missed human conversation without
replaying tool logs. See [session lifecycle](SESSION_LIFECYCLE.md#messages-resume-and-stop).

## Task lifecycle

```text
alt task new --title <title> [--wait <reason> | --after <task>] [--effort <level>] [--paths a.py,b/] [--hold-merge <reason>] [--image <id>] -
alt task release <slug> --reason <reason>
alt task message <slug> <text>|- [--file <path>] [--image <id>]
alt task reply <text>|- [--file <path>]
alt task block <slug> --reason <question> [--recommendation <approach> --label <action> --why <reason>] [--for-burak | --fault]
alt task escalate <slug> --question <question> [--recommendation <approach> --label <action> --why <reason>]
alt task resume|stop <slug> --reason <reason>
alt task hold-merge <slug> --why <reason>  # Burak alone may use --off
alt task machine <slug> --grant --approval <message-id> --question <id> --revision <n> --reason <why>
alt task machine <slug> --revoke --reason <why>
alt task run <slug> <command>
alt task done <slug> --digest <text>
alt task reject <slug> --reason <reason>
```

`-` (or no text) reads the message from stdin, so a quoted heredoc such as `alt task reply - <<'EOF'`
keeps amounts such as $1.20, quotes and line breaks literal; a single-quoted argument suffices for
one line. Without `--questions-file`, a block's reason is its question: a different reason revises the
open question, and the saved reason re-parks it unchanged.

Repository changes use `alt land --message <message> [--merge]`. Project, incident, service, TLS,
and installation commands remain available through `bin/alt --help` and the relevant subcommand
help.

`alt task message <slug> 'Resolve the conflicts and retain the review hold.'` continues a reported
owner whose recorded PR is still open. `alt task resume <slug> --reason 'Continue the existing PR'`
is the equivalent coordinator/operator continuation without a new conversation message. Both use
the daemon's existing resume path and retain the attempt, provider session, worktree, branch, PR,
expected files and all holds. PR lookup failure or a closed PR refuses admission. A saved message or
queued resume receipt means accepted work; inspect `alt task status <slug>` for observed running
state or a capacity/recovery wait. Repeating the same outstanding coordinator resume request reuses
its receipt. Separate messages remain separate, even when their text matches.

The prior report and verification remain in `report-superseded` task events. The current owner must
recheck delivery and write a fresh report before completion, including a replay/no-change turn.
Preserve all previous deliveries, exact remaining scope and holds; chat acknowledgement is not
completion. Old verifier or archive callbacks cannot finish its
continuation. Done, archived and rejected tasks cannot be resumed or messaged through this path.
Archived restoration remains a separate product decision because execution context may be removed.

An L2 block that publishes or revises questions queues one coordinator notification with the open group,
including operator-directed blocks. The operator flag places those decisions in Needs you without waiting for L3; it does not
hide their context from L3. The notification names open members, revisions and required authority so
L3 can settle record-backed or scope portions while operator approvals remain open. Re-parking
unchanged members queues nothing new. Faults keep their existing separate incident/notification path.

### Planned tasks

Create decided short-term work with a written brief on stdin and one wait:

```sh
alt task new --title "Enable the merge CI gate" --wait "PR #376 and the browser fix to land" -
alt task new --title "Follow up after parallel checks" --after parallelize-deterministic-checks-operato -
alt task release enable-the-merge-ci-gate --reason "Both prerequisite merges verified"
```

`--wait` supplies one line of 1–160 characters. The mutually exclusive `--after` names one existing
task in the same project and derives the reason from its name. Work labels these queued task
records **Planned · waits for …** and the state digest includes their reason every turn. They
consume no WIP slot and create no worker or worktree before release. Issues remain the long-term
backlog; planned tasks hold work already decided by the operator.

A named dependency releases the task automatically only when archived done; an already
archived-done dependency is satisfied immediately. A merged PR alone, rejection, failure or a
missing dependency does not satisfy it. L3 or the operator can explicitly release either kind
with `alt task release <slug> --reason '…'`; the recorded reason also explains an early override
of a named dependency. Release restores ordinary dispatch eligibility, subject to the existing
capacity, engine and other launch gates, without releasing merge holds.

Messages refresh a planned task's context without releasing it. They accompany the original
brief when the owner starts; that brief's source authority remains intact. There is one reason
and at most one named dependency, with no dependency graph or PR watcher. For issue #380, either
`parallelize-deterministic-checks-operato` (PR #376) or `make-the-two-browser-walkthroughs-that-f`
alone fits `--after`; their joint prerequisite uses `--wait` and explicit release after both merges
are verified. Creating that planned task neither enables CI nor closes the issue.

### Task reasoning effort

Set independent project defaults without changing engine/model selection:

```sh
alt project set example --l3-effort medium --reason 'Use medium effort for coordination'
alt project set example --l2-effort high --reason 'Use high effort for fresh task attempts'
alt project set example --l2-effort native --reason 'Use native task configuration'
alt project set example --unset-l3-effort --reason 'Restore the existing L3 default'
alt project set example --unset-l2-effort --reason 'Restore the existing L2 default'
alt project list
```

The operator and the project's L3 use these reason-bearing settings requests; L2 cannot change
project defaults. Altd applies them on its next tick, without a free worker slot or service restart.
Project details provides the same independent defaults with immediate saves (desktop: More actions).
Default restores existing behavior: L3 native; L2 High on Codex and native on Claude. Native explicitly
requests no Altitude override, including for L2 engines with a High default.

Levels are `native`, `low`, `medium`, `high`, `xhigh` (Extra High), `max`, and `ultra`.
The current adapters accept Low through Max on both engines and Ultra on Codex. Engine support
does not guarantee support by every selected model/client or bypass provider-managed effort caps.
Unsupported pins refuse; Auto excludes unsupported engines and explains unavailable candidates.
Effort does not create an engine/model pin. Higher effort can use more time and tokens.

`alt task new --title "Investigate a difficult failure" --effort xhigh --paths src/,tests/ -`
overrides the project L2 default for that task. Precedence is explicit task effort, project L2
effort, existing engine default, then native configuration when no override exists. Fresh attempts
resolve at dispatch, including queued tasks; messages/resumes reuse their saved launch override.
L3 resolves its project default at each turn, including in its existing conversation. A turn already
running finishes with its original selection. No existing L2 session is migrated or edited.
Legacy tasks without an effort field keep native behavior unless a project default applies at a fresh
launch; legacy resumes with no saved effort remain native. Model-specific incompatibility stays a
launch/turn failure with its diagnostic, without an application-side downgrade or engine fallback.

`alt task status <slug>` exposes `effort` (explicit request, null if omitted), `launch_effort`
(the actual launch override, null for native configuration), and `engine_reasoning_effort`
(the provider observation, null until reported for the turn). Worker records retain launch effort
as evidence. An observation may differ from the selection and never replaces it. Message/resume
preserves the attempt, owner conversation and saved override through provider configuration and
routing changes. Deterministic fixtures verify arguments, state and failure behavior; live-provider
compatibility remains deferred under the repository testing policy.

L3 session records likewise separate requested `effort`, `launch_effort` and observed
`engine_reasoning_effort`. A provider may cap a requested level without reporting the applied level;
missing observations stay unknown.

L1 helpers remain engine-native. Codex supports inherited effort, a native subagent default and
spawn/custom-agent overrides ([native controls](https://learn.chatgpt.com/docs/agent-configuration/subagents)).
Claude helpers inherit session effort unless their agent definition overrides it
([agent effort](https://code.claude.com/docs/en/sub-agents)); provider caps still apply.
Altitude supplies no global L1 effort selector or helper orchestration. These native settings are
intent, not evidence that every helper used the requested level.

### Lifecycle requests

For L3 and shell callers, `resume`, `stop`, and `reject` append one task-local daemon request and one
`daemon-request` event containing the task, operation, actor, reason, and request id. Altd performs the
worker or session effect, refuses a changed state or identity, and makes an identical retry idempotent.
A repeated reason after a genuine later lifecycle creates a new request against that lifecycle's identity.
A message to a blocked task uses its durable inbox and `resume_after` handoff instead of launching a
worker in the caller. Coordinator messages to faulted tasks stay non-waking; verified recovery uses
the explicit reason-bearing resume. L3 cannot call `task block` directly: an L2 blocks itself with its attempt fence,
while L3 uses reason-bearing `task stop` so altd blocks the task and stops the same observed worker.

### Image handoffs

Attach operator screenshots/photos in project or task chat. To give an assigned L2 the visual
context discussed with L3, repeat `--image <committed-image-id>` on `alt task new` or
`alt task message`. IDs appear beside the image's original message in agent context and in JSON
conversation reads. Selection is limited to four images and 20 MiB total per assignment/message.
The daemon accepts only existing committed images from the current project; there is no path upload,
arbitrary download or cross-project relay. For example:

```sh
alt task message fix-layout 'Match the spacing shown in this screenshot.' --image <image-id>
```

The task receives the actual managed image content through its engine adapter and retains the
source operator-message reference. An L3 relay remains L3 steering: it does not grant a lease,
resolve an operator question or release a merge hold. Existing resume/recovery authority applies.
Archive and worktree cleanup retain the image with the conversation.

### Conversational decisions

Before republishing a question or preview, the owner reads original answers and later guidance.
Wording-only clarification of approved scope belongs in the conversation/docs without a new approval
checkpoint. If republication races an answer, the owner compares scope, cites the original source
in its reply/checkpoint, and withdraws only the redundant current question with that citation in
`--reason`. Withdrawal does not accept a stale answer against a new revision. Materially revised
decisions still require their own answer; original authority, existing receipts and merge holds
remain intact. A later reaffirmation already recorded needs no further reconciliation.

`block` is the current L2's question to L3; its operator flag uses the operator audience. L3 can
`escalate` the actual dilemma and explicit recommendation. Both publish into the owning L2 human
conversation with their source attribution. The model chooses a plain question, one recommended
quick action, or two to three explicit options with one recommendation. A fault is operational and
uses `--fault`, without inventing a recommended choice.

Write `--reason` / `--question` as the short, plain-language decision itself. The task title gives
its user-facing purpose; `--recommendation` names the chosen action, `--label` is its concise button
label, and `--why` gives the material consequence or tradeoff needed before answering. Keep each
group member's `question`, option labels/text and `why` equally focused. Put full reasoning,
implementation detail, evidence and history in ordinary replies in the owning task conversation
(`alt task reply` for L2, `alt task message` for L3). Existing saved question detail remains readable
there. Rewrite around the choice rather than relying on automatic shortening, and never hide a
consequence needed for an informed answer.

The model chooses the question, then invokes the CLI through its engine's execution tool. A minimal
ordinary dilemma is:

```sh
alt task block "$ALTITUDE_TASK" --reason 'Should the old index remain available for fourteen days?'
```

This writes a durable plain question for L3 triage; escalation or the operator-audience flag in the
synopsis brings it to Needs you. It is Altitude's task UI, separate from the engine's own tool-call
display. [Architecture and transport flow](ARCHITECTURE.md#from-model-judgment-to-a-task-question-or-preview)
explains the owner CLI and coordinator MCP/broker paths.

For up to three independent questions upfront, pass `--questions-file <file>` to `block` or
`escalate`. Use `--questions-file -` with JSON on stdin when calling through the L3 broker; the
broker never reads a server file supplied by the caller. The single `--reason` / `--question`
flags ask one question with at most one recommended action; the operator can select only that
action or answer freely, so their text never enumerates alternatives. Offer several choices as
grouped options instead, keeping question text and selectable options in agreement. A grouped
payload has this form:

```json
{"questions":[
  {"question":"How long should we retain the old index?","options":[
    {"key":"seven","label":"7 days","text":"Keep it for seven days."},
    {"key":"fourteen","label":"14 days","text":"Keep it for fourteen days."}
  ],"recommended_key":"seven","why":"Covers the rollback window."},
  {"question":"Who should receive the report?"}
]}
```

Options have stable keys, short button labels and explicit answer text. A question may omit options;
multiple options require a recommendation key. Nothing becomes an operator answer by default.
Include an existing question `id` to revise that member. Omitted unresolved members remain open;
close obsolete questions explicitly rather than dropping them from a later publication. A group
allows three open members and retains closed history. To re-ask a withdrawn question while others
remain open, publish a new member without an `id`, even for identical wording; this preserves the
independent questions. Dependent questions wait for their prerequisites.
For an unchanged existing question, omitted options preserve its saved choices; `options: []`
explicitly removes them. Changed question text with omitted options becomes a plain question.

The worker handoff names the pending question ID/revision and each task message ID. The owning L2
judges the reply in context: discuss a follow-up, clarify genuine uncertainty, or record a clear
decision and continue. No special approval phrase or extra confirmation is required.
The question UI sends presets and **Other…** text through the same conversational handoff, including
plain questions with no recommendations. **Sent to L2** means a response is saved; the owner still
judges its meaning and uses `resolve` for an actual decision. Submitted members leave the attention
count. To explicitly ask a responded member again, include its ID in `--questions-file`; publication
creates a new revision and restores its answer field even when wording is unchanged. Ordinary
unchanged re-parking retains the response. Independent unanswered members remain available.
One typed reply can answer several members. Its saved question references name what the operator
was viewing; cite the same message in a separate `resolve` call for each answered or obsolete member.
Only questions still awaiting an operator response remain in Needs you. Quick selections can also be sent
together as one batch; a stale member prevents the whole batch from writing.
After a harmless follow-up, checkpoint and park with the still-valid `block --reason` text. Omitted
recommendation fields preserve its approach; parking or revision preserves its required authority.

```text
alt task resolve <slug> --question <id> [--revision <n>] --message <source-id> \
  --source task|project --disposition answered|superseded --reason <chosen-scope-or-closure-reason> \
  [--l3-authority <specific-evidence-and-rationale>] \
  [--remaining <still-relevant-question>] [--recommendation <approach> --label <action> --why <reason>]

alt task resolve <slug> --question <id> [--revision <n>] --disposition withdrawn --reason <why>
```

`--revision` defaults to the question's current revision.

`--source task` (default) cites a durable task message ID. `--source project` cites the original
operator chat `turn_id`, available from the project's recorded chat; an L3-authored relay is not an
operator source. The existing CLI door checks owning task and attempt, and the resolution checks
source provenance and timing. An operator message sent after the question was first asked can settle
its current revision, including after re-publication; the owner judges whether it still answers the
question. A message tagged to another question refuses. L3 sources must name the exact revision. An L3 answer can settle an L3-audience question. For an
unnecessary operator escalation already settled within delegated L3 authority, the owner adds
`--l3-authority` with the specific brief/rule/recorded-decision evidence and why it applies. This requires
an authentic L3 task message bound to that exact question revision. The owner judges the substance;
the command does not classify prose or prove the claim. Its receipt retains L3 attribution, source,
authority basis and recording owner/attempt; a retry cannot substitute another rationale.
The audience stays as recorded. Genuine operator approvals, taste, spend, paradigm and security
choices require the original operator source; an L3 recommendation or discussion cannot supply it.

Use `answered` for the settled approach. Use `superseded` when authoritative changed direction makes
the question irrelevant; the reason names that change, without claiming acceptance of the old
recommendation. With `--remaining`, the operation preserves the resolved scope and publishes a new
revision containing only the relevant unanswered parts. That remainder keeps its audience without
changing independent worker, capacity or fault state and has no inherited default;
provide a recommendation only when it applies to the remaining question. Harmless follow-ups require
no resolution operation. A repeated identical resolution reuses its record; stale or conflicting
resolutions are refused. Neither this command nor ordinary resume releases a merge hold.

The [owner's decision guidance](../personas/l2.md#conversation-and-decisions) governs assessment
of new guidance, withdrawal and re-asking. `withdrawn` records L2 judgment without `--message`,
`--source`, `--l3-authority`, `--remaining` or recommendation fields. It removes that member's
controls and retains its history; it grants no approval and discards no work. No message classifier
or automatic withdrawal is involved.

When a provider limit queues a fresh attempt, existing question replies and quick acceptance wait
in the normal inbox. The new owner receives the still-open questions in its brief; semantic
resolution remains an operation of the running or blocked owning L2.

### Concurrent landings

`alt land` takes the repository turn when it merges or targets this repository's required PR
check, with or without `--merge`. It waits before fetching, publishing or checking CI, prints
when waiting and when its turn starts, and returns seconds waited as `waited` (zero without a
wait). This preserves shared candidate admission from I-20260923-062538 while CI runs the suite.
Keep the command and owner session alive; ordinary contention needs no L3 landing-window request.
Admission waits at most 3600 seconds, independently of `--wait`, which still bounds hosted-check
polling. A timeout refuses without selecting a candidate or publishing changes; retry explicitly
when ready.

Each admitted invocation rechecks ownership and holds, fetches current main, and merges it into
the task branch when needed before pushing and checking the fresh candidate. This preserves
adopted history. Conflicts abort integration and retain local work for the owner; dirty edits are
not stashed. Required checks, review and original approval sources still govern delivery.
Failure or cancellation releases the turn; the next owner proceeds with its own candidate.
Task messages and Stop remain available. Repeating a completed merge creates no duplicate PR.

The turn is a process-owned repository lock, not a durable or FIFO queue. Dry runs and nonmerging preparation
in other repositories do not wait for it. The containerized
self-hosted runner and a `make check` run by hand outside `alt land` do not share it; a hand run
without `CI` set uses two browser workers. External writers and older landing versions
can still change refs: stale base/head evidence refuses merge and is never reused or retried
automatically. Only invocations using this installed version share serialization.

### This repository's required PR check

Owners and helpers run relevant tests during development. The self-hosted PR `check` runs
the full suite. Use the existing commands:

```sh
git add <selected-files>
git diff --cached
alt land --message "fix: describe the change" --pr-body-file /tmp/pr.md
alt land --message "fix: describe the change" --merge
```

Landing publishes the PR and waits for its required `check` on the current head; it does not
run the full suite locally. The task branch includes current main. A branch missing current main
needs reconciliation, a push and fresh PR checks on the new head. Final validation and merge are
serialized across Altitude owners; the merged tree must equal the tested tree. Failed, pending, missing, skipped,
cancelled, stale or unrelated required runs block. `--test-cmd` supplies no bypass for this gate.

After a bounded CI wait, retain the run and missing evidence, explicitly block and ask L3 for a
[durable CI recheck](#durable-ci-recheck). A missing run needs trigger/runner recovery, not an
invented run ID. Runner or storage outages pause merges until verified recovery and fresh checks.
Reviews and live merge holds remain mandatory. Opening a held PR does not authorize its merge.
GitHub updates outside Altitude remain unprotected. Other repositories keep their hosted/no-CI
behavior and local command choice. See [evidence and activation](DEVELOPMENT.md#ci-and-candidate-identity).

### Continue after a PR merges

An active task can deliver more than one PR. Its owner continues authorized work in the same
conversation, isolated worktree and local branch, then uses the usual command:

```sh
git add <selected-files>
git diff --cached
alt land --message "fix: finish the remaining work" --pr-body-file /tmp/next-pr.md
alt land --message "fix: finish the remaining work" --merge
```

Landing verifies the earlier merge on fetched main, commits selected staged edits, and puts only follow-up
commits onto current main before opening a fresh PR. This handles squash history and a deleted
remote branch. Previously published changes are not duplicated. An unchanged retry reports
`checks: merged` as historical delivery, never a new check pass, and creates nothing. If main
already contains the follow-up, no extra PR is needed and the current receipt is reconciled.

Every PR requires its own current checks and appropriate review. A recorded approval releasing
one PR's hold restores the original requirement for the next PR. L3 applies a separate release
after judging whether the original decision covers that PR; an actual renewed hold needs its own
approval. A later explicit task-wide release remains effective. Other unanswered questions, scope
and ownership remain intact. Existing external PR
adoption remains supported, and further ordinary work after an adopted merge uses the task branch
while preserving its adoption receipt in history.

If updating onto main conflicts or fails, landing aborts the rebase and keeps the committed work
on the task branch. Dirty working edits can prevent rebasing; landing preserves them without
auto-stashing. The refusal names the exact rebase command for the owner to resolve in that
same worktree. Follow-up merge commits require manual reconciliation first because replaying them
could drop merge-resolution edits. Unseen remote changes also require incorporation before
continuation. Failures after reconciliation or push can be retried with `alt land`.

Task `prs` and delivery events preserve earlier PR/head/merge evidence. The current `delivery`
receipt is recorded before waiting for checks; an unpublished delivery cannot complete the task.
The final `report.json` includes every delivery in `landed.prs` and current validation evidence.
Reports predating the current delivery, omitting earlier PRs or leaving unpublished work are
refused. Finish through the verified report path when all agreed work is done; a merge alone
does not require a new task or complete the current one. `alt task done` verifies a reported
delivery whose recorded verdict is not ok against GitHub again before completing, so a merged
delivery whose report only abbreviated the merge SHA completes without resuming its owner; the
refusal names the remaining problems.

### Task design previews

Before requesting visual approval, the current L2 publishes the proposal's selected screenshots and
explanation with its ordinary question. The resulting **View preview · vN** link in Needs you and the task
conversation opens a browser tab over Altitude's normal connection. Use a title that identifies
whether the captures show a proposal or an implementation review. Phone and desktop readers can
inspect the screenshots at full size and use **Back to question** for feedback or the existing quick
answer. A local filesystem link is not a review entry.

Create a selection JSON file, for example `design/wireframes/review.json`:

```json
{
  "title": "Compact chat proposal",
  "proposal": "design/wireframes/PROPOSAL.md",
  "images": [
    {"title": "Phone reading", "path": "design/wireframes/captures/phone-reading.png"},
    {"title": "Phone typing", "path": "design/wireframes/captures/phone-typing.png"}
  ]
}
```

Then publish it with the question:

```sh
alt task block "$ALTITUDE_TASK" --reason 'Approve the compact chat proposal?' \
  --recommendation 'Use the layout and behavior shown in the saved proposal.' \
  --label 'Use this design' --why 'Keeps more of the conversation visible.' \
  --design-file design/wireframes/review.json
```

`--design-file` requires the current L2's own project, task and attempt. It accompanies one ordinary
question or a `--questions-file` selecting exactly one new or existing open member; other members
stay unchanged. It cannot accompany `--fault` or an ambiguous multi-member selection. Select paths
relative to the recorded task worktree under `design/wireframes/`: one nonempty UTF-8 `.md`/`.txt`
file up to 64 KiB and one to twelve
PNG/JPEG screenshots up to 8 MiB each, 32 MiB total. Titles have 1–160 characters, and the selection
JSON is at most 64 KiB. Only named files are copied. Symlinks, traversal, special files and unsupported
types are refused. Use the existing browser harness to capture interactive wireframe states;
submitted HTML, SVG, JavaScript and CSS are not preview inputs.

Inputs may be ignored or untracked: capture does not stage or commit them. Keep review screenshots
in ignored `design/wireframes/captures/` or `design/wireframes/shots/`; the explicit selection can
publish them before any repository commit. Follow the [capture guidance](../AGENTS.md#ui).

The response includes `design_url`, for example
`/projects/example/tasks/chat-layout/design/<question-id>/1`. This is a path on the current Altitude
connection, not a filesystem path or an external upload. Use the returned reference; the same
**View preview · v1** entry is rendered in Needs you, beside the question in its conversation and
in the open question's navigation when it is offscreen. Publication follows
the ordinary L3-first audience rules unless explicitly directed to the operator.

The question contains the captured text and titles, and the task retains the selected image bytes.
Source edits and worktree cleanup leave the saved version intact. Repeating identical inputs keeps
the revision; publishing changed screenshots, text or labels advances the same pending question.
For an existing design question, use its same reason and add `--design-file`; its choices stay intact
when replacement recommendation flags are omitted. An ordinary block with no design file parks the
existing question and retains its saved preview.

Old links remain bound to the old capture and identify a newer question when one exists. Missing or
altered saved content shows **Design unavailable**, with Retry and Back to question; it never serves
different content at that version. First acceptance refuses unavailable evidence. Feedback remains a
normal message, and explicit acceptance uses the existing question/revision checks. Neither viewing,
publication nor design acceptance releases a merge hold. A held implementation PR becomes available
only after merge and normal activation; its owner reports that availability before another owner
is asked to publish working proposal links in its existing session.

To restore a missing or damaged regular capture, the same owner republishes the original selection.
Verified source bytes restore the same content hash without changing the question revision; an actual
content change creates a replacement revision. Symlinked saved paths still refuse publication.

### Adopt an existing PR

The task owner or operator can explicitly adopt an assigned, open, same-repository PR targeting
main. The task keeps its isolated worktree and `worktree-<slug>` branch. The owner reviews the
original commits and complete PR diff for assignment scope and private content. Landing refuses
PRs and branches owned by other active tasks. Commit messages need no labels or repair, including
historical labels naming other tasks. Fork PRs are not supported.

From the task worktree, inspect the existing PR and its full history. For a fictional PR #42
on `proposal/external`:

```sh
gh pr view 42 --json url,headRefName,headRefOid,baseRefName,isCrossRepository
git fetch origin main proposal/external
git log --oneline origin/main..origin/proposal/external
git diff origin/main...origin/proposal/external
git merge --ff-only origin/proposal/external
alt land --adopt-pr 42 --expected-head <full-observed-head-sha> \
  --reason "This task is assigned to reconcile the existing proposal" \
  --message "docs: reconcile proposal" --dry-run
alt land --adopt-pr 42 --expected-head <full-observed-head-sha> \
  --reason "This task is assigned to reconcile the existing proposal" \
  --message "docs: reconcile proposal"
```

Use the actual full SHA observed from the PR. The head must agree with origin and be an ancestor
of local HEAD; already-present additions within the assignment are allowed. If the local task branch
has diverged, incorporate the inspected PR with a merge commit. Preserve the original history.
Select reconciliation edits with `git add` and inspect `git diff --cached`; `alt land` commits that index.

The explicit command records the active immutable `adopted_pr` receipt and `pr-adopted` event before
publication, visible through `alt task status` and `alt task events`. `--dry-run` fetches and
validates the PR but records nothing and stages/pushes nothing. After adoption, ordinary
`alt land --message '…' [--merge]` reuses that PR and its original branch. Retrying adoption with the same original PR/head is
idempotent; selecting another original head for that PR is refused. If origin moves, inspect and
incorporate only changes belonging to this task; the original receipt remains unchanged.

For a task explicitly assigned several existing PRs, finish the active PR before adopting the next.
Fetch main, incorporate the next inspected PR without rewriting history, then repeat `--adopt-pr`
with its number, full observed head and a reason naming its assignment. Landing verifies the
previous PR is merged and its original and final heads are preserved on current main. It retains
the previous receipt unchanged in `adoption_history` and selects the new `adopted_pr` under the
project lock. `alt task show` and `alt task events` retain the audit trail. No receipt editing is
needed. Refused adoption validation leaves the active receipt unchanged. Once adoption is recorded,
publication, check or hold failures retain the new active receipt for retry. Earlier receipts cannot
be reactivated.
Ordinary subsequent landing and recorded hold approval target the active PR. Each PR still requires
explicit task authorization, applicable checks and review; holds remain in force.
A recorded approval of the previous PR restores that review hold for the next adoption. An explicit
later task-wide `hold-merge --off` remains effective. L3 can apply the original decision to the new
active PR only when that decision covers its work; restoration does not invent a renewed restriction.

Complete the repository's applicable review before `--merge`. Drafts, requested changes and
outstanding required reviews block adopted merges. Checks must belong to the pinned base/head
candidate. Required checks, including those specified by active branch rules, must pass; missing,
ambiguous, unrelated or required skipped checks are not green. Completed skipped checks identified
as nonrequired by GitHub are ignored, regardless of workflow condition or check provider. Altitude
does not prove why they skipped; a job that must execute needs to be required. Failed, cancelled
and pending checks still block, including nonrequired checks; pending checks use the normal wait.
Unknown requiredness remains blocked. These rules apply to ordinary and adopted landing.
At least one hosted check must actually succeed; entirely skipped CI cannot use the no-CI fallback.
Where no CI is configured, use `--test-cmd "<full suite>"` if the
default `make test` is unsuitable; it runs on the exact two-parent merge candidate. The live task
owner and merge hold are rechecked before merging. The original branch receives only fast-forward
pushes; rejected pushes never retry with force. `--merge` uses a merge commit and requests no
branch deletion, so the repository must permit that merge method. Host-side branch deletion
settings remain the repository operator's policy.

`--merge` incorporates current main while holding the repository turn. If that integration
conflicts, reconcile manually while preserving the adopted commits with a merge commit:

```sh
git fetch origin main
git merge --no-ff origin/main -m "Merge main for validation"
```

Resolve conflicts in the task worktree, rerun applicable checks and review, and land again.
Landing pins current origin main and the PR head, confirms GitHub's authoritative base target,
and refuses actual base/head movement. A lagging PR `baseRefOid` alone does not block that pair.
Task merge holds, recorded operator approval and the normal report/archive workflow also apply
to adopted PRs.

### Recorded merge approval

The owner applies the operator's approval from its own task chat while landing:

```text
alt land --merge --approval <message-id> --message <summary>
```

The owner judges that the message approves the current scope. After checks pass and just before merge,
landing checks under the project lock and the owner's publication fence that it is the operator's
original task message, sent after the current hold generation, and that the PR is the task's open,
ready, same-repository PR targeting main at the candidate head. Refusals keep the hold.

L3 applies an approval given in project chat (or on the owner's behalf) through its project-bound
daemon connection:

```text
alt task hold-merge <slug> --approval <message-id> --source task|project --pr-number <number> --head <full-sha> --reason <interpretation>
```

Read `alt task messages <slug> --json`, `alt task status <slug>` and `alt pr <number> --json`,
plus the original project conversation and later corrections in both chats. Increase `--last` or use
historical lookup as needed. L3 records why the source authorizes this PR's outcome in `--reason`.
It judges scope, conditions and revocation; design feedback, implementation-only permission and
coordinator relays cannot authorize merge. Relevant corrections require action, including a renewed
hold when appropriate. Routine integration stays within the original decision's scope.

`--source task` (default) cites the original operator message or saved UI choice with its 32-character
ID. `--source project` cites an original user/chat turn's 12-character ID in this project. Missing,
corrupt, duplicate or non-operator sources refuse.

Altd verifies the current hold and the project's open, non-draft PR targeting main at the supplied
head. Task publication branch and recorded active/adopted PR identity must match. Explicit hold changes
create a new generation and require approval after that requirement arose. Each held follow-up needs
its own release; L3 can cite the original decision when its actual scope covers that PR.

Success stores `merge_approval` and a `release-merge` event with source/author/time, hold
ID/event/time, PR URL/head, scope reason and actor (`l2` or `l3`). Local validation
refusals retain the hold and record `merge-approval-refused`. Inspect `alt task show <slug>` and
`alt task events <slug> --json` before resuming a blocked owner. The owner completes review and
current-candidate checks through `alt land --merge`; release itself preserves worker and question state.
Failed reconciliation follows L3's recovery path.

Approval mode requires an active hold and runs through L3's daemon transport. Ordinary `--off` is
operator-only; approval mode cannot combine with it or `--why`.

### Machine access

A worker's own shell covers builds, tests and installs inside its workspace. A change the workspace
or sandbox cannot make, such as a service unit, a reload/restart or a user-level toolchain, runs
under a machine grant:

```text
alt task machine <slug> --grant --approval <message-id> --question <id> --revision <n> [--source task|project] --reason <why>
alt task machine <slug> --revoke --reason <why>
alt task run <slug> <command>
```

The owner asks the operator a plain question naming the purpose and its verification and resolves
the operator's answer with `alt task resolve`. L3 or the operator then records the grant citing that
same message after judging that the answer is a yes; the owner cannot record its own. The rest is
mechanical: the cited message must be the operator's own and must have answered the current
revision of that operator question with no remainder. The grant binds to the task's current
attempt; the owner, L3 or the operator may revoke it. Success stores `machine_access` (purpose,
answer, approval, question/revision, attempt, actor, time) and a `machine-grant` event; refusals
record `machine-grant-refused` and change nothing.

The purpose grant covers relevant run/inspect/adjust iteration without approval for each command;
one command at a time is an execution limit. Broader access or purpose still needs its own authority,
and explicit one-run restrictions remain binding. Retain evidence and revoke the grant when done.

`alt task run` is the current owner's verb for its own task. altd records the run in `machine.jsonl`
first, then runs the command as the operator in a transient user unit outside every worker sandbox,
in the task worktree, through a login shell, with the user service manager reachable and the
owner's task identity in the environment, so `alt` inside the command acts as that L2. One command
runs at a time per task, for at most `MACHINE_COMMAND_TIMEOUT` (600 seconds); the unit itself
appends output to `machine.log` in the task folder and records the exit status, so a command that
restarts Altitude keeps its row and unit. The CLI prints the output and a status line, then exits
with the command's status (124 on timeout). Each run completes its `machine.jsonl` row and adds a
`machine-run` task event and a `machine-run` project event with the command, unit, exit status and
purpose. A missing grant, a non-running task, a stale attempt, a grant from an earlier attempt or a
revoked grant refuses with the reason; no exit status within the limit is reported as a timeout or
an explicit uncertainty, never as success. `alt task status <slug> --brief` shows the active purpose.

The door is altd's operator-trusted HTTP surface, which every worker on this single-account host
can reach, the same surface that answers questions and posts messages. altd checks the task record,
not which local process calls; the grant record and its per-command log are the boundary.
