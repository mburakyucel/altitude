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

### Conversation-audit pilot

After rollout approval, the operator explicitly starts the seven-day pilot:

```sh
alt --project altitude audit start --reason 'Approved conversation-review rollout'
alt --project altitude audit status
alt --project altitude audit stop --reason 'End the pilot early'
```

Start/Stop are operator-only; L3 and task owners can read their project's status.
`start --engine <engine> --model <model>` pins a reviewer independently of ordinary routing; both
overrides are required together. Defaults live in the engine configuration seam. Missing reviewer
access never selects another model. Start does not approve a held PR, grant evaluation permission
or change billing. An existing pilot cannot renew/reset through Start. Failed/uncertain runs remain
paused for coordinator assessment. Stop prevents new sessions while a claimed one retains its timeout.

Status includes expiry, reviewer, attempts, native usage/cost when available, failures/unavailability
and supplied-to-L3 turn identities. Native cost is API-equivalent telemetry, not cash charged or
subscription quota. Private `<project>/audits/<id>/` retains packets/responses; `audit.json` holds the
bounded pilot record. Chat byte-offset references identify original JSONL rows with turn ID/date;
task references name canonical message IDs. None of this evidence belongs in public issues.

Sessions run at most twice daily, twelve hours apart, after four new eligible exchanges. Each has
the engine's ordinary session timeout, a starting prompt of at most 64 KiB and at most three findings. The forty-eight-hour
sample starts no earlier than September 22, 2026 07:00 UTC, includes twenty exchanges/four directly
related tasks, and records omitted/incomplete coverage. No new activity means no call. Seven days or
fourteen attempts ends the pilot. Only new unresolved candidates reach L3 on its next project chat
turn; unchanged/owned/corrected findings create no additional notifications. Ordinary work continues.

`alt task status <slug>` and `alt task report <slug> --json` include `token_usage`: the daemon's
persisted local token observation, independent of the report's agent-authored spend. It contains
inclusive input/output and their processed total, distinct request counts, the current owner
session's `context`, optional cache/reasoning subsets, session rows with
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
that same record. Its `main_run` is the push-triggered main run of the newest merged commit, or null
with a recorded error while that run does not exist yet; a hand-dispatched run on the same commit
never qualifies. Status and `alt task list` carry one `waiting` label, which `alt queue` shows as a blocked task's reason: the operator's
turn (open questions or a held review-ready PR), `L2 replying to <operator>` after they wrote,
`paused · fault …`, `stopped by <operator>`, `waiting on L3` or `paused`. `alt monitor` reports each
seat's quota and model allowances, the routing each role would get now and live provider sessions; `alt decisions` reports what waits for the operator, including held
reviews.

`alt l3 tools` groups the shell commands persisted with recent L3 turns. Commands outside the `alt`
door appear first so recurring inspection pipelines are easy to replace with known verbs.

An L3 process is read-only on the deployment checkout and Altitude home. Native command admission names
trusted shims; the CLI, broker and shims authorize their operations. Git log/diff/show include full patches
and historical files, with external diff/text-conversion helpers disabled and output-file options refused.
The altitude user journal is also readable. Runtime shims carry every `alt` invocation plus
[read-only `gh` commands](#coordinator-github-reads) and altitude service status over that project's
same-user altd Unix socket. The socket fixes the project independently of request data. The broker re-applies the
L3 command door and accepts flat task identifiers and stdin rather than `--file`. Source editing, Git writes, direct GitHub mutations, service control,
direct command networking, and cross-project task verbs are unavailable.

Input bodies go on stdin with `-` (`task new … -`, `task message <slug> -`, `issue new … -`,
`issue comment <number> -`, `--questions-file -`); the `alt` shim reads stdin only when an argument is `-`,
so every other argument, including long text, never waits on an open stdin.

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

## Coordinator information messages

Inside a registered project's coordinator, send deliberately written local diagnostic information:

```sh
alt project message peer 'The local probe failed before provider execution; review fixture-review.' --summary 'Local probe result' --request-id probe-1
alt project message source 'The fix is merged; activation is still pending.' --summary 'Fix status' --request-id reply-1 --reply-to <exchange-id>
```

The coordinator socket fixes the sender. The ordinary CLI, task owners and HTTP clients cannot send
these messages. `--request-id` is a stable identifier per sender/recipient pair (1–100 letters,
digits, dots, underscores or hyphens); retry an interrupted acknowledgement with the same identifier
and identical arguments.
The acknowledgement supplies the exchange identifier for a reply. A reply must address the other
participant of an incoming exchange. Both projects must remain registered to the same resolved
checkouts; changed registration refuses reuse and leaves pending information unsupplied. These rows
stay visible without expiration or a removal control; reattaching the same checkout permits supply.
For diagnostic text beginning with a dash, put options before `--`, then the literal text:
`alt project message peer --summary 'Flag refusal' --request-id probe-2 -- '-p flag rejected'`.

Text is limited to 4 KiB and the summary to one plain line of 100 characters. Local incident IDs,
task slugs, review IDs and public issue/PR URLs may cross. Files, attachments, private record/home
paths, recognized credentials and recognizable conversation transcripts are refused. Diagnostic
code excerpts are allowed. Unknown secrets and prose transcripts cannot be classified universally:
the coordinator writes and checks the sanitized text. The recipient's configured provider processes
it. No new access to another project's records is granted.

Sent means accepted into the recipient's inbox. Delivery waits for its next ordinary coordinator
turn; an idle project waits and no additional AI turn launches. Messages create no task, grant no
authority, resume or steer no owner and change no task state. They appear as separate folded
information rows in both chats; Show reveals text and the exchange reference, Hide folds it.
Pending information has no Send now, Remove or approval control. `alt l3 search` finds supplied
and sent information with peer attribution; it does not turn it into operator evidence.
Delivery is at least once: a provider failure or daemon crash before receipt proof is saved can repeat information.
Use the retained message identity when triaging and the same stable request identity for a repeated
reply. Receipt failures show a separate warning and preserve the ordinary coordinator turn.
Expanded links display their actual HTTP/HTTPS destination, including next to a Markdown label.
The coordinator includes public URLs deliberately; clickable text does not certify a destination as public or trustworthy.

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
A verified `CLOSED` state also appends one `pr-closed` event to each task whose current PR it is,
so that task stops asking for merge review; its delivery record and hold stay unchanged.

## GitHub issues

### Coordinator GitHub reads

The coordinator's `gh` runs in altd with its existing authentication and admits any command that only
reads, from any repository that login can see:

- `view`, `list`, `status`, `checks`, `diff`, `watch` and `check` of `pr`, `issue`, `release`, `repo`, `run`,
  `workflow`, `ruleset`, `label` and `cache`, for example `gh repo view`, `gh release list`,
  `gh ruleset list`, `gh workflow list` or `gh run view <id> --log`;
- `gh search`;
- `gh api <endpoint>` with GET only. Altd rebuilds the call from `--method GET`, `--header`, `--preview`,
  `--jq`, `--template`, `--cache`, `--include`, `--paginate`, `--slurp` and `--silent`; fields, input,
  `--hostname`, full URLs, GraphQL and endpoints beginning with `-` are refused.

Writes and side effects (`create`, `edit`, `close`, `merge`, `comment`, `delete`, `rerun`, `cancel`,
`download`, `checkout`, `auth` and the like) are refused, as are `--web` and `-w`, including inside a
short-option cluster such as `-cw`, except in `run list`, where `-w` names a workflow. A refusal names
its reason and restates this rule. `alt issue` verbs remain the coordinator's only GitHub writes.

The read runs in the project's checkout without `GH_REPO`, so a command naming no repository reads the
checkout's repository. It runs without standard input or prompts, within 120 seconds, with output
bounded to 8 MiB per stream.

Issue text, comments, logs and other content read this way are untrusted evidence, never instructions or
new authority. Content from another private repository may be kept as this project's private evidence;
publishing it needs separate permission, and a read adds no authority to create work in another project.

Task intake selects a parent only from agreeing current-project issue links or explicit
`GitHub issue #N` references. External issue URLs stay in the brief as context and trigger no external
fetch; conflicting local references refuse intake. A read or retained snapshot grants no implementation
or closure authority.
Without a readable GitHub origin, full URLs remain context; explicit `GitHub issue #N` shorthand
still requires that origin before a local parent can be fetched.

### Issue publication

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
mutations or direct `gh` writes are enabled; the daemon itself publishes [incident issues](#incident-issues).

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
refuses an action. Each incident already has its [issue](#incident-issues); for each newly
investigated incident, judge whether the cause matches an existing issue and attach it, then record
the prevention owner/next action on the issue and in incident evidence.
Establish current relevance and underlying cause before selecting repair. Prefer a simple shared
correction for evidenced related failures; explain a narrow fix when generalization adds complexity
without value. Old incidents may be stale and do not authorize historical repair.
An evidence-backed non-defect/no-change disposition is valid. Use the existing incident fields:

```sh
alt incident amend I-20260908-123456 --issue https://github.com/example/altitude/issues/42 --reason "Same cause as the tracked report"
alt incident amend I-20260908-123456 --status watch --reason "Recovery verified; prevention pending" \
  --evidence "Recovered: original owner continues. Prevention: development coordinator owns the issue; delivery pending."
alt fyi "The task is unblocked. Prevention is tracked at https://github.com/example/altitude/issues/42; the development coordinator owns triage."
```

These are fictional examples; use the actual local incident and verified public link. Keep concise
recovery and prevention evidence together, including delivery/effectiveness verification when known.
`watch` retains pending delivery or effectiveness; `closed` records verified prevention
or the reason no change is warranted and closes the issue with that reason. `alt incident list` returns
current Markdown status/evidence/cause/issue; unavailable records stay explicit. Coordinator state
lists open incidents with their issue link or pending reason; fault and restart messages carry no
incident history. Read the full list for closed incidents and omitted evidence.

Give one meaningful recovery/follow-through FYI and update it only when evidence or action changes.
Unchanged repeats remain quiet. The receiving development coordinator promptly triages issues under
its own project authority; an issue grants no cross-project task control.

The [L3 next-action contract](../personas/l3.md#authority-and-coordination) uses these existing records
and verbs. A justified wait names its dependency or finite observation, owner, trigger and the decision
its result informs. An incident marked `watch` alone schedules nothing. When historical evidence is
irretrievable, record that limit, use retained evidence for specific remaining questions and expose
any capability or authority gap through `alt task escalate <slug> --question '…'`. Escalation keeps
the fault reason and merge hold; it supplies no recovery authority. An already-authorized capability
correction follows the existing task/PR path. A changed operational contract requires its decision
before execution. The owner investigates as part of its task: relevant, non-invasive diagnosis
proceeds iteratively on its judgment without a plan to approve or per-round permission, and an
authorized investigation continues through failed attempts until it has a result or reaches a
genuinely new boundary. A question arises only for access the owner lacks, a material machine or
service change, unapproved spend, a live-provider test or an explicit restriction. Runaway work
shows in the task's live activity, token usage and L3's stalled-work observation rather than through
per-attempt approval. Diagnosis does not expand fix scope, operator grants or
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

A `task-git-provenance` fault for a task worktree on the wrong branch occurs only when altd cannot
switch it back itself: the worktree has uncommitted changes, is on a detached HEAD or its task branch is
missing. The fault message names the Git step. L3 sends that step to a recovery task, never to the
operator, and resumes the owner after verifying the worktree is on `worktree-<slug>`.

### Incident issues

Every incident is one GitHub issue in the Altitude repository. Filing an incident creates it:

```sh
alt incident new --title "Fictional resume defect" --what "The toy task stayed blocked after resume" \
  --evidence "events.log 10:15 resume request; task status unchanged" --cause "not yet analysed"
alt incident publish I-20260908-123456
alt incident amend I-20260908-123456 --issue https://github.com/example/altitude/issues/42 --reason "Same cause"
alt incident amend I-20260908-123456 --status closed --reason "Prevention merged and verified"
```

The issue carries the label `incident` (create it once in the repository), the sanitized title,
expected and actual behavior, the sanitized root cause, a reproduction line that reads pending
triage until L3 adds a fictional or redacted reproduction in a comment, a System section, and
the incident id with an opaque project digest as its marker. A system fault's actual behavior is its
summary: the fault kind, the step that failed when the kind alone does not say it, and the last
error line from the worker or Altitude, where an error inside a JSON event or printed mapping counts
and raw stream chunks and event lines never do. An incident L3 files reports its own what-happened
text. The System section lists what the record captured when the incident was filed: platform, OS
name and version, kernel, architecture, machine model (the Mac model identifier, or the DMI vendor
and product family on Linux), Altitude version (with the release commit) and deployment kind
(source checkout, installed release or container image), for an installed release the `update` line
from the daemon's last release check (`v0.1.1 available`, `up to date`, or `not checked` when the
check is off or has not run), and for an incident with a task the engine
with its CLI version and the worker's confinement. Host and account names, home paths, addresses,
serial numbers and hardware UUIDs are never collected. Publication decodes the text, then
rewrites home paths, `.altitude` and incident file references, long hex ids and UUIDs, email
addresses, IP and MAC addresses other than loopback, credentials, private key blocks and values
named as serial numbers, task references, the names of other managed projects, this machine's host
and account names in any letter case (outside the container, whose names are the image's, and
except a name that is a word of the OS name, such as a cloud image's `ubuntu`) and the configured
operator name to `[path]`, `[id]`, `[email]`, `[address]`, `[REDACTED]`, `[task]`, `[project]`,
`[host]`, `[user]` and "the operator", then applies the same private-evidence, network-address,
serial-number and credential refusal as project-local issues plus the operator name and this
machine's host and account names. Evidence, task, project, message ids, logs, transcripts and the fault
ledger never leave the machine. System faults publish after their fault lock is released; the FYI
and L3 message name the issue. One publisher or amender runs per project at a time, so a retry
cannot race the daemon into a second issue.

The record's `- issue:` bullet holds the URL. When publication fails (GitHub unreachable, the
label missing, a refused body, an unconfigured target) it holds `pending — <reason>` and the
project log gets an `incident-issue` event; nothing retries on its own. `alt incident publish <id>`
retries: it reuses the repository's `incident` issue whose body carries this incident's marker
before creating, so an interrupted create never produces two issues. A record whose issue is
already a URL returns it without GitHub.

An installed release whose last check found a newer release holds the issue instead: the fault may
already be fixed. The record holds `pending — held: reported on v0.1.0 while v0.1.1 is available;
update first`, the project log's `incident-issue` event has status `held`, and the fault FYI and L3
message open with "Altitude v0.1.0 is installed and v0.1.1 is available: update …, then retry."
The fault ledger ties its incident to the installed version, so any repeat after updating, an
unchanged blocker included, files a new incident on the new version and publishes it. The held
record stays held; only `alt incident publish <id>` files it, for example when the update itself
fails. Source checkouts and container
images record no `update` line and never hold. `alt incident list`, project API incident rows and
`STATE.md` show the link or the pending reason; `alt incident list` and the API rows also carry the
record's `summary` and `system`, so the coordinator sees them before publication.

`--issue <url>` attaches an existing issue in the same repository when L3 judges the cause is the
same: altd verifies it with a GitHub read, comments the occurrence there, and closes the issue this
incident created as a duplicate with a comment naming the kept one. A hand-written or previously
attached issue is never closed. A URL outside the repository or an unverifiable issue is refused and
the record stays unchanged. `--status closed` comments the sanitized closure reason on the linked
issue and closes the issue this incident created as completed when still open; an attached issue
tracks other occurrences and stays open for `alt issue close`. A GitHub failure leaves the incident
open so the closure can be repeated. A pending issue needs no GitHub call to close the incident.

The target repository is the machine setting **Settings → Incident reports** saves (a First run
step), else `ALTITUDE_UPSTREAM_ISSUE_REPOSITORY` in **altd's environment**: a GitHub
`owner/repository` or GitHub repository URL. Off is the default for every installation: incidents
stay on the machine, every record's issue bullet reads
`pending — incidents stay on this machine until incident reports are turned on in Settings …`, and
`alt incident list` shows that reason. Turning reports on and running `alt incident publish <id>`
publishes an earlier record. An invalid target is a pending reason with configuration
instructions; there is no fallback to the incident project's repository, the release metadata or
the Altitude checkout's origin. An incident filed in another managed project sends the registered local `altitude`
project one fixed issue-link notification when its Git origin matches the issue repository. The
receiving queue and chat show the public URL and nothing else; it creates, reuses, resumes or
coordinates no task. Pending rows and retained receipts deduplicate the full URL across source
projects and restarts. L3 runs the incident verbs through its coordinator transport, for example
`{"kind":"alt","args":["incident","publish","I-20260908-123456"]}`; L2 workers file incidents only
through `alt task block --fault`. Bare `#number` in replies identifies the calling project's
repository; incident issues are named by their full URL.

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
   `alt task message reconcile-edits "Inspect archive <branch> at <SHA>; apply the reviewed snapshot in your task worktree using the CLI recovery procedure and deliver through your PR." --summary "Reconcile the archived edits"`
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

For Altitude defects, the originating L3 checks public delivery evidence and local observations
that the actual cause is gone before `alt task resume <slug> --reason '<verified fix and observation>'`.
Notification receipt, issue closure and unrelated restart do not establish repair. Saved unchanged
blockers do not generate repeated recovery nudges; new affected tasks, new blockers and changed
details remain actionable. `alt task message` from L3 to a faulted task records non-waking discussion;
use the explicit reason-bearing resume after verification. Operator messages retain their ordinary
discussion wake. Resume preserves the original attempt/session/model and the guarded landing path,
including any merge hold.

## Device pairing

`alt pair` prints a one-time code that pairs one browser with Altitude, the running service's
address to open on that browser (or the reason there is none), a QR code of that address when a
phone can reach it, and the CA's name and the last eight pairs of its SHA-256 to compare before
installing it. The code never appears in a link. It writes the code straight to the private access store, so it
works over SSH and without a browser; only the operator runs it, and L2 and L3 are refused. A code
works once, for ten minutes; a new code cancels the previous one and five wrong codes cancel it.
Every other `alt` command that calls altd sends the machine key from the same store. See
[pair each device](SETUP.md#pair-each-device) and [lockout recovery](OPERATIONS.md#devices-and-lockout-recovery).

`alt tls-share` (operator only) reads the running service's address, port and certificate folder
from the record the service wrote when it started, and checks over HTTPS that the
service proves its identity with that folder's CA. It then offers that public CA certificate to a
desktop or phone for ten minutes at a plain-HTTP link on the service's non-loopback address, and prints the
link as a QR code (black on white, legible in any terminal) with the CA's name, scope, expiry and
SHA-256 fingerprint to compare with the downloaded certificate before installing it. The link serves only a guided page,
an iPhone configuration profile holding only the certificate, and the certificate file; it closes
when the time is up or on Ctrl-C. Settings → Devices → **Set up a device** opens the same kind of link
from the service, with **Open setup page** for the current device and a QR/link for another one.
The page guides Linux browser imports, macOS Keychain trust, iPhone/iPad profiles and Android trust.
Both sharing routes refuse loopback-only service addresses; on the hosting computer, use the public
`ca.crt` path reported by `alt doctor` directly. Neither route installs trust or changes network
exposure. Verify the exact HTTPS URL without a warning before pairing.
See [certificate setup](SETUP.md#trust-https-on-each-device).

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
(80), and `request.status` showing whether a change is still `pending` or has completed. It also
reports `machine_runs`, how many [granted commands](#operator-grant) run at once on this computer: a
quarter of its cores, at least one, not a setting.
Project registration and settings expose no concurrency cap. Stored project `wip` overrides
impose no limit.

Machine caps accept positive integers with no fixed ceiling of 80. Zero, negatives, fractions,
booleans and nonnumeric values are rejected. Reset removes the override and restores 80.

### Voice backend

The same request path selects the transcription backend every composer uses:

```sh
alt machine set --voice host --reason 'Transcribe on this computer'
alt machine set --voice browser --reason 'Recognize in the browser'
alt machine set --unset-voice --reason 'Back to the default'
```

`--voice` accepts `host` or `browser`. Without a saved choice, the backend is `host` where this computer
can run the speech model and `browser` elsewhere; `machine show` reports the backend in effect. See
[voice input](OPERATIONS.md#voice-input) for what each backend needs and where audio goes.

`alt voice status` reports whether [host voice](OPERATIONS.md#host-voice) can run here and its setup
state. `alt voice setup` downloads and checks the speech model and its runtime once (about 698 MB),
printing progress and the final state; `alt voice remove` stops dictation and deletes them. Setup
and removal are the operator's; agents are refused.

### Projects folder

First run lists the folders directly inside one projects folder. `ALTITUDE_ROOTS` is its initial
value (default `~/Projects`); this replaces it with one existing absolute folder, as **Settings →
Projects folder** does:

```sh
alt machine set --projects-folder ~/code --reason 'Projects live in ~/code'
alt machine set --unset-projects-folder --reason 'Back to ALTITUDE_ROOTS'
```

`machine show` reports the active `projects_folder`.

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
counts running tasks and active adversarial reviewers, excluding engines' native helpers and L3 turns.
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
options, and `>` starts a lower-priority tier. An omitted model uses the project's
[default model](#default-models) for that role and engine, then the role default.
Empty tiers/options, duplicate options and unknown engines are rejected. The daemon applies the
request on its next tick and records actor, reason and outcome. No PR, restart, UI setting or free
task slot is needed. `alt project list` shows the stored override; `alt monitor` explains each
project's L3 and fresh L2 choice, including its tier, skipped options and unknown quota.

Auto uses the highest tier with an eligible option. Only comparable, named seven-day account
readings select by headroom within a tie; short windows only determine availability. Unknown or
incomparable weekly readings use configured tie order. An L3 engine/model still eligible in that
tier stays unless a competing option has at least fifteen percentage points more weekly headroom.
Default preferences tie Codex and Claude on their role defaults, so a Claude L2 launches on Opus
and L3 on Fable, and put Opus below them as L3's fallback when Fable is rejected. A lower tier never
repeats an option a higher tier already resolved, so for L2 the Codex tie is the only fallback.
`ALTITUDE_PRIMARY_ENGINE` chooses only the default tie order. A project override replaces the
whole preference list, and `--unset-routing` restores those defaults.

The L2 provider priority chooses which engine fresh L2 attempts try first without pinning it:

```sh
alt project set example --l2-preference claude --reason 'Use Claude more for L2'
alt project set example --unset-l2-preference --reason 'Back to Auto'
```

The same choice is **Tasks** under **Routing** (Auto, Prefer or Only each engine) in the project's
Settings page. A preference moves every option on that engine above the other options, keeping their tier
order: with the default tiers, Prefer Claude tries Claude on Opus before Codex, and Prefer Codex
tries Codex before Claude. The preferred engine is chosen whenever it is eligible; the other engine remains
its fallback under the usual installation, quota and rejection handling. It applies only to fresh
L2 attempts and their queue and Monitor explanations, which read "Auto tier N (prefers <engine>)".
L3 keeps the tiers as written, explicit task and project pins still win, and running or resumed
attempts keep their engine. With custom routing the preference reorders only the options that
routing lists, and an engine it omits stays unused. Auto (unset) is the routing tiers alone: the
Settings page says whether that is the default distribution or names the custom routing.

**Only** keeps a role on one engine, the Settings page's Only <engine> routing:

```sh
alt project set example --l2-engine codex --reason 'Keep tasks on Codex'
alt project set example --unset-l3-engine --reason 'Let routing choose L3'
```

### Model choices

A model choice is tried ahead of a role's routing tiers: **New tasks** for every project's fresh L2
attempts, and each project's **L3** choice for its L3 turns. These are the Models dialog's two tabs
(the [UI spec](../design/wireframes/SPEC.md#361-models-dialog)). A choice is `[engine][:model][@effort]`;
a Claude alias alone names Claude:

```sh
alt machine set --new-tasks fable@high --reason 'Fable for new work this week'
alt machine set --new-tasks @max --reason 'Max effort on whichever engine Auto picks'
alt machine set --unset-new-tasks --reason 'Back to Auto'
alt project set example --l3-choice codex@low --reason 'Light coordination on Codex'
alt project set example --unset-l3-choice --reason 'Back to Auto'
```

Precedence, highest first: a task's or turn's explicit engine, model or effort (`alt task new
--engine/--model/--effort`, `alt chat --engine`); a project's Only engine, which keeps its engine and
uses the choice's model and effort only when they are on it; the choice; then the routing tiers with
each project's Auto defaults. A choice whose engine or model is unavailable (missing CLI, exhausted
window or a recent rejection) leaves the tiers to pick meanwhile, and its controls say so. An effort
alone applies to whichever engine the tiers pick that accepts it; an engine that does not keeps its
default. New tasks applies to tasks that start from then on, including queued ones; started tasks and
resumes keep their model and effort. L3 uses its choice from the next turn. Reviewer selection and
explicit engine handoff ignore choices. Routing explanations read "chosen for new tasks" or "chosen
for L3". New tasks is a machine setting, so only the operator sets it; the L3 choice is a project
setting.

The daemon collects account quota every five minutes without an interactive session. The native
headless usage reader requires CLI 2.1.277+ with a subscription login and structured live account
rows; unavailable or failed reads remain unknown. Thirty-minute-old observations are stale. Monitor
explains unknown quota, and Auto continues to use configured tie order until weekly readings are
comparable. See [source compatibility and verification limits](SESSION_LIFECYCLE.md#context-and-prompt-cache-evidence).

| Intended preference | `--routing` value |
| --- | --- |
| Claude-only account with Opus available | `'claude:opus'` |
| Codex first; Opus only when Codex is unavailable | `'codex>claude:opus'` |
| Fable and Codex tied; prefer Fable when weekly quota is unknown | `'claude:fable,codex>claude:opus'` |
| Same tie; prefer Codex when weekly quota is unknown | `'codex,claude:fable>claude:opus'` |
| Prefer Opus first, then Codex | `'claude:opus>codex'` |

A missing CLI, exhausted window or known access rejection excludes the affected options; unknown
access or quota remains eligible. No plan name implies model entitlement, and a shared account
meter does not supply separate Fable/Opus allowances. A model-specific weekly row from the native
reader that shows 100% excludes only that model while the reading is current and before its reported reset; `alt monitor` shows these rows
under each seat's `models`. If L3's Fable rejects access while Codex is
absent, the default policy tries Opus after confirming no output or tool effects occurred; an L2
already on Opus has no lower Claude option. A model
rejection excludes that model for thirty minutes; an authentication rejection excludes the engine
for thirty minutes. A rejection of an unresolved native default is scoped to that role, since
the two launchers can use different default models. Each alternative is tried at most once per dispatch or turn. When none is
eligible, the explanation identifies installation, authentication, reset or configuration actions.

Preferences are distinct from explicit pins. `alt task new --engine claude --model opus …` pins
one task; project `--l2-engine`/`--l3-engine` pins (Only) and `alt chat --engine …` take precedence
over Auto and never silently fall back. An explicit model pin
also remains strict. A project default model is a preference, not a pin: it names the model an
Auto option on that engine uses and leaves the engine choice to the tiers. Changing preferences does not unpin them or change a running L2: resume keeps
that attempt's engine, provider session and recorded launch model. A quota fallback is a recorded
fresh attempt from `progress.md`. L3 retains a separate provider conversation per engine, including
when its chosen model changes; crossing providers supplies missed human conversation without
replaying tool logs. See [session lifecycle](SESSION_LIFECYCLE.md#messages-resume-and-stop).

## Task lifecycle

```text
alt task new --title <title> [--wait <reason> | --after <task>] [--effort <level>] [--paths a.py,b/] [--hold-merge <reason>] [--image <id>] -
alt task release <slug> --reason <reason>
alt task message <slug> <text>|- [--file <path>] [--image <id>] [--summary <line>]
alt task reply [<slug>] <text>|- [--file <path>] [--capture <run>]
alt task block <slug> --reason <question> [--recommendation <approach> --label <action> --why <reason>] [--for-operator | --fault]
alt task escalate <slug> --question <question> [--recommendation <approach> --label <action> --why <reason>]
alt task resume|stop <slug> --reason <reason>
alt task hold-merge <slug> --why <reason>  # the operator alone may use --off
alt task grant <slug> --approval <message-id> --question <id> --revision <n> --reason <why>
alt task grant <slug> --standing-policy <heading> --source project --approval <message-id> --question <id> --revision <n> --attempt <n> --reason <why>
alt task grant <slug> --from-task <earlier-slug> --approval <message-id> --question <id> --revision <n> --attempt <n> [--source task|project] --reason <why>
alt task grant <slug> --revoke --reason <why>
alt task run <slug> <command>
alt task terminal [<slug>] [--json]
alt project terminal [--json]  # the coordinator, through its project socket
alt task offer <title>  # the coordinator, through its project socket, during a chat turn
alt task done <slug> --digest <text> [--findings-tracked <reference>]
alt task reject <slug> --reason <reason>
```

`-`, `--file -` or no text reads the message from stdin, so a quoted heredoc such as
`alt task reply - <<'EOF'` keeps amounts such as $1.20, quotes and line breaks literal; a single-quoted
argument suffices for one line. A reply always goes to the current task; like the sibling verbs it may
lead with that task's slug (`alt task reply "$ALTITUDE_TASK" -`), and any other extra argument is
refused. `--capture <run>` attaches that validation run's [captures](DEVELOPMENT.md#validation-captures)
as fixed copies; the reply shows **Watch capture** under its text. Without `--questions-file`, a block's reason is its question: a different reason revises the
open question, and the saved reason re-parks it unchanged.

Repository changes use `alt land --message <message> [--merge]`. Project, incident, service, TLS,
and installation commands remain available through `bin/alt --help` and the relevant subcommand
help.

L3's `alt task offer '<title>'`, during a chat turn, ends its reply with Create task and that title for
the operator (up to 100 characters on one line) instead of a question such as "Shall I queue a task?".
A reply that creates a task carries the task card instead. A press arrives as the operator's
chat message `Create task: <title>`, which the conversation shows on the button rather than as a message; the `alt task new` that turn runs is bound to the offering reply,
and a second creation for the same reply is refused, so the turn creates the task from the reply and the
conversation without asking again. [Conversations](ARCHITECTURE.md#conversations-and-navigation)
describes when the press is accepted.

L3's `alt task message` requires `--summary`: one plain line, up to 100 characters, saying what the
message is about. The task conversation shows it as the message's folded row, and Show opens the
original text; the summary is stored beside that text and changes nothing the owner receives. Only
L3's messages carry a summary.

`alt task message <slug> 'Resolve the conflicts and retain the review hold.' --summary 'Resolve conflicts, keep the review hold'` continues a reported
owner whose recorded PR is still open. `alt task resume <slug> --reason 'Continue the existing PR'`
is the equivalent coordinator/operator continuation without a new conversation message. Both use
the daemon's existing resume path and retain the attempt, provider session, worktree, branch, PR,
expected files and all holds. PR lookup failure or a closed PR refuses admission. A saved message or
queued resume receipt means accepted work; inspect `alt task status <slug>` for observed running
state or a capacity/recovery wait. Repeating the same outstanding coordinator resume request reuses
its receipt. Separate messages remain separate, even when their text matches.

L3 also uses `alt task message <slug> 'Correct the report and retain the pending acceptance.' --summary 'Correct the report'`
or `alt task resume <slug> --reason 'Correct the contradicted report'` when the current verifier
verdict is `contradicted`, including after every delivery PR is merged. This correction path checks
the recorded report owner and delivery, requires the existing owner session and worktree, and needs
no open PR or GitHub lookup. It leaves the report file for its owner to correct. L3 checks task status
for the queued handoff and eventual running state; the owner records unresolved acceptance in the
fresh report's `blocked` string and retains or re-parks its question. This is coordinator report
recovery; ordinary operator message/resume admission still requires an open PR for reported tasks.
`task block` belongs to the owner; L3 uses `task stop` only for a running worker, not to return a report.

The prior report and verification remain in `report-superseded` task events. The current owner must
recheck delivery and write a fresh report before completion, including a replay/no-change turn.
Preserve all previous deliveries, exact remaining scope and holds; chat acknowledgement is not
completion. Old verifier or archive callbacks cannot finish its
continuation. Done, archived and rejected tasks cannot be resumed or messaged through this path.
Archived restoration remains a separate product decision because execution context may be removed.
The maintenance tick removes a done or rejected task's worktree and merged branch under the
[retention rule](OPERATIONS.md#worktree-and-source-export-retention).

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

### Default models

Set the model a fresh launch uses on each engine, per role and independently of engine selection:

```sh
alt project set example --l2-model fable --reason 'Use Fable for Claude-routed tasks in this project'
alt project set example --l2-codex-model gpt-6-astra --reason 'Name the Codex model instead of its CLI default'
alt project set example --l3-model opus --reason 'Coordinate on Opus'
alt project set example --unset-l2-model --reason 'Restore Opus'
alt project list
```

Each role has one flag per engine: `--l3-model`/`--l2-model` for Claude (registry keys `l3_model`,
`l2_model`) and `--l3-codex-model`/`--l2-codex-model` for Codex (`l3_codex_model`, `l2_codex_model`);
`--unset-…` restores the default. A value is one alias or model id without spaces; Altitude validates
nothing else and passes it to the engine, which reports an inaccessible model as a routing rejection.
The Claude aliases `opus`, `sonnet`, `haiku` and `fable` resolve to the current model of that family in
the Claude CLI, so new models arrive with CLI updates without an Altitude change; a specific id can
always be typed. Codex uses the model configured in its CLI unless a default or pin names one. Neither
CLI exposes a model list, so Altitude offers the aliases as suggestions and does not discover models.

Precedence for a fresh launch is the task's `alt task new --model …` or turn pin, then the model named in
the selected routing option, then the project default for that role and engine, then the role default:
Opus for a Claude L2, Fable for a Claude L3 and the CLI default for Codex. L2 defaults apply at
dispatch, including queued tasks; messages and resumes keep the attempt's saved `launch_model`. L3
defaults apply from its next turn. A default never pins an engine: with the default tiers a project
whose Claude default is Fable still ties Codex first. The operator and the project's L3 set these
through the same reason-bearing requests as effort; L2 cannot.

### Task reasoning effort

Set independent defaults per role and engine without changing engine/model selection:

```sh
alt project set example --l3-effort low --reason 'Light coordination on Claude'
alt project set example --l3-codex-effort medium --reason 'Medium coordination on Codex'
alt project set example --l2-effort high --reason 'High effort for Claude task owners'
alt project set example --l2-codex-effort native --reason 'Use native Codex task configuration'
alt project set example --unset-l3-effort --reason 'Restore the existing L3 default'
alt project list
```

`--l3-effort`/`--l2-effort` hold the Claude defaults (registry keys `l3_effort`, `l2_effort`) and
`--l3-codex-effort`/`--l2-codex-effort` the Codex defaults (`l3_codex_effort`, `l2_codex_effort`).
Each accepts only the levels its engine supports. The operator and the project's L3 use these
reason-bearing settings requests; L2 cannot change project defaults. Altd applies them on its next
tick, without a free worker slot or service restart. **Settings → This project** provides the same
defaults with immediate saves: `GET /api/defaults/<project>` returns each role's model/effort pair per
engine with its setting key, default and choices, and `POST /api/defaults`
`{"project", "setting", "value" | null}` saves one. Default restores existing behavior: native, except
High for a Codex L2. Native explicitly requests no Altitude override, including for a Codex L2.

Levels are `native`, `low`, `medium`, `high`, `xhigh` (Extra High), `max`, and `ultra`.
The current adapters accept Low through Max on both engines and Ultra on Codex. Engine support
does not guarantee support by every selected model/client or bypass provider-managed effort caps.
Unsupported pins refuse; Auto excludes unsupported engines and explains unavailable candidates.
Effort does not create an engine/model pin. Higher effort can use more time and tokens.

`alt task new --title "Investigate a difficult failure" --effort xhigh --paths src/,tests/ -`
overrides the project defaults for that task without changing them; it is how L3 asks for more effort
for one L2. Precedence is explicit task effort, then the project default for the routed engine, then
the existing engine default, then native configuration when no override exists. Fresh attempts
resolve at dispatch, including queued tasks; messages/resumes reuse their saved launch override.
L3 resolves its project default for the engine of each turn, including in its existing conversation.
A turn already running finishes with its original selection. No existing L2 session is migrated or
edited. Model-specific incompatibility stays a launch/turn failure with its diagnostic, without an
application-side downgrade or engine fallback.

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
When altd claims a resume requested with `task resume --reason`, the reason joins the task conversation
once as a message from its actor (L3's folds as "Resumed the task") and reaches the resumed owner after
the pending inbox rows, marked as the resume reason with its actor and time. A claim that fails before launch returns it to the
inbox with the rest of the batch. A message wake and the task page's Resume and Continue buttons,
which send fixed text rather than an authored reason, add nothing.
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
alt task message fix-layout 'Match the spacing shown in this screenshot.' --summary 'Match the screenshot spacing' --image <image-id>
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

`block` is the current L2's question to L3; its operator flag uses the operator audience. Each block
sets the audience of the members it publishes or rewords: re-parking an unchanged operator question
keeps it the operator's, and a member reworded without the flag, such as a wait on L3 or an external
event, leaves the operator's turn. The task waits on the operator only while an open member is theirs. L3 can
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
An open operator question linking or naming the held PR replaces its generated review card,
including a freeform question and one with a submitted response awaiting owner interpretation.
After resolution the fallback returns if merge approval is still needed. A held PR closed without
merging asks for no review: `alt task block` by the owner reads the PR's state from the checkout origin's repository and records the closure
as a `pr-closed` task event, or a later reopening as `pr-reopened`; when that read fails, the block
still lands, the review stays shown and stderr says the state was unavailable. This display rule neither
classifies the answer as approval nor changes the quick-option requirement for a changes review.
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
  [--remaining <still-relevant-question> [--for-operator]] \
  [--recommendation <approach> --label <action> --why <reason>]

alt task resolve <slug> --question <id> [--revision <n>] --disposition withdrawn --reason <why>
```

`--revision` defaults to the question's current revision.

`--source task` (default) cites a durable task message ID. `--source project` cites the original
operator chat `turn_id`, available from the project's recorded chat; an L3-authored relay is not an
operator source. The existing CLI door checks owning task and attempt, and the resolution checks
source provenance and timing. An operator message sent after the question was first asked can settle
its current revision, including after re-publication; the owner judges whether it still answers the
question. A message tagged to another question refuses. L3 sources must name the exact revision. An L3 answer, including L3's resume reason, can settle an L3-audience question; a resume reason
never answers an operator question or approves a merge. For an
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
revision containing only the relevant unanswered parts. Like a block, the remainder asks L3 unless
`--for-operator` names it plainly the operator's; it does not inherit the original audience, and it
queues L3's notification at once, so parking it later with its own text publishes nothing again.
It changes no independent worker, capacity or fault state and has no inherited recommendation;
provide a recommendation only when it applies to the remaining question. On a blocked task, each
resolution recomputes `waiting_on` from the open members: an open operator member means waiting on
the operator, only L3 members means waiting on L3, and none means not waiting. Harmless follow-ups require
no resolution operation. A repeated identical resolution, including the remainder's audience, reuses its record; stale or conflicting
resolutions are refused. Neither this command nor ordinary resume releases a merge hold.

The [owner's decision guidance](../personas/l2.md#conversation-and-decisions) governs assessment
of new guidance, withdrawal and re-asking. `withdrawn` records L2 judgment without `--message`,
`--source`, `--l3-authority`, `--remaining`, `--for-operator` or recommendation fields. It removes that member's
controls and retains its history; it grants no approval and discards no work. No message classifier
or automatic withdrawal is involved.

When a provider limit queues a fresh attempt, existing question replies and quick acceptance wait
in the normal inbox. The new owner receives the still-open questions in its brief; semantic
resolution remains an operation of the running or blocked owning L2.

### Concurrent landings

`alt land --merge` takes the repository turn before fetching, publishing or checking CI, prints
when waiting and when its turn starts, and returns seconds waited as `waited` (zero without a
wait). This preserves shared candidate admission from I-20260923-062538 while CI runs the suite.
Keep the command and owner session alive; ordinary contention needs no L3 landing-window request.
Dry runs and invocations without `--merge` never take the turn. Admission waits at most
3600 seconds. The shared CI and owner-assessment wait that follows has the
same 3600-second bound, so a merging candidate keeps the turn while its fresh required check is
queued or running; `--wait` only shortens it. GitHub registers
a head's checks one at a time, so a required check absent from the head is waited for within the same
bound rather than read as skipped; landing names it and the checks that have registered, such as a
skipped nonrequired job. Landing prints the remaining bound when it first sees pending or
unregistered required checks. A required check that never registers within the bound ends the wait as
`missing` with that observation and does not merge. An admission timeout refuses without selecting
a candidate or publishing changes; retry explicitly when ready.

Each admitted invocation rechecks ownership and holds, fetches current main, and merges it into
the task branch when needed before pushing and checking the fresh candidate. This preserves
adopted history. Conflicts abort integration and retain local work for the owner; dirty edits are
not stashed. If only main moves after publication, the merging command incorporates it, pushes a
new head and waits for fresh checks within the original publication deadline. It checks current
ownership and the adopted target before integrating and publishing each candidate, and repeats review, hold, approval and
`--closes-issue` gates; review assessments never transfer
automatically. Head or PR identity movement refuses instead of retrying. Adopted PRs retain their
history and accept only fast-forward pushes. Required checks and original approval sources still
govern delivery.
Failure or cancellation releases the turn; the next owner proceeds with its own candidate.
Task messages and Stop remain available. Repeating a completed merge creates no duplicate PR.

When integration or task context makes completed review assessments stale, a merging L2 invocation
keeps its turn while the owner explicitly assesses the pinned candidate. CI and assessment share the same `--wait`
deadline; failed or unavailable checks end the wait. Keep landing alive in a native background/tool
session and read its partial output. Inspect the printed head/base and current conversation, post any
explanation for all affected reviews, then run `alt task review assess --review-id <id> --file <assessment.json>`
in a separate command for each. The notice lists all stale review IDs and subjects together, with the
assessment timestamp and assessed/current values of each changed head, base, tree, proposal or context
hash. Context includes the request, brief, messages and decisions; inspect the current task conversation
when its hash changes.
Assess every stale request, including proposals, before posting further explanations, and collect the
original landing result. A changes assessment does not retire a proposal assessment or its findings.
No assessment or finding disposition is carried forward automatically. If code needs edits or another
review, cancel landing and prepare a new candidate. Missing or unfinished review, changed local/remote
head or PR identity, and lost ownership refuse; `--wait 0` and operator-run landings refuse stale assessment
immediately. Timeout or termination releases the turn with the pushed candidate retained and unmerged.
Context changes detected during final merge validation use the same assessment wait and original
deadline, without releasing the repository turn. Final review/context, CI, holds and approval checks
run again after assessment; review refusal leaves the merge hold intact.

The turn is a process-owned repository lock, not a durable or FIFO queue. CI runs and a `make check`
run by hand outside
`alt land` do not share it; a hand run without `CI` set uses two browser workers. External
writers, older landing versions and other installations do not share it. This repository's strict
GitHub up-to-date required-check rule closes the race between final validation and merge across
installations; the local lock alone does not. A base-only refusal repeats integration and checks
within the original deadline; stale evidence never authorizes a merge. No GitHub setting change is
needed. Nonmerging CI can run alongside a merging candidate, bounded by GitHub capacity: each check
uses 11 jobs against the plan's 20 concurrent-job limit. A newer PR push cancels that PR's superseded
check run; main and manual runs are never cancelled.

After its push, landing pins the candidate from the fetched `origin/<branch>` tip, which must be
the revision it pushed; any other tip is a head the landing did not push and refuses at once
(`head moved from the pushed revision`). GitHub's PR view lags a push for a moment, so a view that
still names an earlier head is re-read every 2 seconds within a 30-second polling window until it
names the pushed revision (#480); existing Git/GitHub command timeouts apply to each read. A view
that never converges refuses with the head it reports. Checks still have
to pass on that exact pushed head with current main included.

### This repository's required PR check

Owners and helpers run relevant tests during development. The GitHub-hosted PR `check` runs
the full suite. Use the existing commands:

```sh
git add <selected-files>
git diff --cached
alt land --message "fix: describe the change" --pr-body-file /tmp/pr.md
alt land --message "fix: describe the change" --merge
```

Landing publishes the PR and waits for its required `check` on the current head; it does not
run the full suite locally. The task branch includes current main. A branch missing current main
is integrated automatically by `--merge`, followed by a push and fresh PR checks on the new head.
Final validation and merge are serialized across Altitude owners sharing the common Git directory;
the merged tree must equal the tested tree. Failed, pending, missing, skipped,
cancelled, stale or unrelated required runs block. GitHub-managed scans on the head, such as CodeQL
default setup, need no PR identity but must pass like any other check: a failing scan blocks and a
pending one is waited for. `--test-cmd` supplies no bypass for this gate.

After a bounded CI wait, retain the run and missing evidence, explicitly block and ask L3 for a
[durable CI recheck](#durable-ci-recheck). A missing run needs trigger recovery, not an
invented run ID. GitHub Actions outages pause merges until verified recovery and fresh checks.
Reviews and live merge holds remain mandatory. Opening a held PR does not authorize its merge.
GitHub's strict required-check rule protects this repository's base race; Altitude's task holds and
review protocol govern its own merges. Other repositories keep their hosted/no-CI
behavior and local command choice. See [evidence and activation](DEVELOPMENT.md#ci-and-candidate-identity).

### Dry run and gate selection

`alt land --dry-run` reports what a real landing of the worktree would pin and judge, and
commits, pushes, opens, tests and merges nothing. It fetches the base and the branch tip as an
ordinary landing does, keeps the staged index intact, records no adoption and releases no hold.
The result keeps `checks: "dry-run"`, `head: null` and `local_tests: null`, because no head is
pushed and no suite runs, and adds `prospective`:

```json
"prospective": {
  "base": "<fetched origin/main commit>",
  "head": "<HEAD commit, or null while changes are staged>",
  "tree": "<the tree the head would carry: HEAD's, or the staged index>",
  "gate": "github-actions | local-suite",
  "required_pr_check": false,
  "workflows": {"base": true, "head": false},
  "local_suite": ["make", "test"],
  "undetermined": ["..."]
}
```

`gate` is `github-actions` when this repository's required PR check applies or either side
carries `.github/workflows`; otherwise `local-suite`, and `local_suite` shows the exact argv
the suite would run. `undetermined` names what only a real landing settles: with staged
changes the head commit is created at landing and only its tree is known; a HEAD that lacks
current main is integrated into a new head under `--merge` and cannot satisfy the required PR
check as it is. Continuation after a merged PR and check evidence already published on the PR
are also settled only when landing. A dry run never proves that tests passed or that a merge
is authorized.

Workflow detection inspects both pinned sides. Workflows on the base keep the hosted gate for
every PR, so a branch that deletes `.github/workflows` shows `workflows.head: false` and still
`gate: github-actions`; with no hosted run it lands as `skipped`, not through the local suite.
Retiring a project's CI is a project decision made outside `alt land`; it does not arrive
through a PR that removes the workflows.

`--test-cmd` is one command. Landing splits it into argv with shell quoting rules and runs it
without a shell on the merge candidate, so `&&`, `;` and redirections are literal arguments to
the first program. Prefer one entry point that runs the full suite, or name the shell explicitly:

```sh
alt land --message "fix: describe the change" --merge --test-cmd "make check"
alt land --message "fix: describe the change" --merge --test-cmd 'sh -c "pnpm typecheck && pnpm test"'
```

The command must exit 0 and print a readable passing-test count. It runs only under the
`local-suite` gate and supplies no bypass for hosted checks.

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

A PR merged on GitHub after later pushes to its branch carries a newer head than the recorded
delivery, which verification refuses until `alt land` runs again. That retry records the merged head
as the current delivery when it is the recorded PR from the recorded branch, the recorded head is an
ancestor of the merged head, and the merge commit is on current main. The delivery event names the
replaced head, merge commit and PR URL. A different PR or branch, or a recorded head outside the
merged history, is refused and the record stays unchanged; a record that already matches is left as
it is. The owner then refreshes `report.json` for the reconciled delivery.

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

A task PR closed without merging stays closed. The next `alt land` from the same task opens a fresh
PR from the same branch with its own title and description, replacing the closed PR's remote commits
under the recorded-tip lease; the owner rebases or resets the branch first so it carries only
the intended work. The closed PR stays in `prs` and in the delivery event, and the fresh PR becomes
the current delivery with its own checks, review and hold release. A closed PR whose branch is
not the task's is refused.

Task `prs` and delivery events preserve earlier PR/head/merge evidence. The current `delivery`
receipt is recorded before waiting for checks; an unpublished delivery cannot complete the task.
The landing result names no main run: the merged commit's push-triggered run rarely exists at that
moment, and `alt task status` resolves the run for the merged commit itself once it does.
The final `report.json` includes every delivery in `landed.prs` and current validation evidence.
Reports predating the current delivery, omitting earlier PRs or leaving unpublished work are
refused. Finish through the verified report path when all agreed work is done; a merge alone
does not require a new task or complete the current one. `alt task done` verifies a reported
delivery whose recorded verdict is not ok against GitHub again before completing, so a merged
delivery whose report only abbreviated the merge SHA completes without resuming its owner; the
refusal names the remaining problems. A review finding the owner leaves `open` on an unblocked report
is one such problem: it belongs to coordination, and L3 completes the task once the finding is tracked
elsewhere with `alt task done <slug> --digest '…' --findings-tracked '#461'`. The reference must
name a real tracking record such as the issue coordination filed; the done event and archived digest
list every open finding with that reference, so nothing is dropped silently. The flag accepts only
that one problem on a delivery whose merges and heads verify, refuses a report without open findings,
and is not available to the owner.

### Adversarial review

L2 proactively requests independent adversarial review for complex proposals before code and complex
implementations; simple work stays light by judgment. Task details shows a **Proposal review** and an
**Implementation review** box with the latest review's verdict and counts, and one **Request**,
**Review again** or **Try again** button when a new review can start; the conversation shows one card
per kind ([design](../design/wireframes/SPEC.md#adversarial-review)). Viewing a review never invokes a
reviewer. The reviewer is a separate invocation with the captured-input/read-only contract. It uses an
eligible alternate configured engine when there is one and otherwise the task's own engine, the
ordinary path. Engine/model, any fallback reason and account-allowance uncertainty are saved with each
review and shown under its technical details. A request waits on any open task, whatever L2 is doing: a
waiting or blocked L2 is woken, and a stopped or faulted task keeps the request for its next resume.
One reviewer runs machine-wide, using one additional machine slot; `run` refuses while another review
is running, and the request waits for L2 to run it once the reviewer is free. L2 names acceptance
criteria and key risks for focused adversarial review; the reviewer reports a one-sentence verdict,
findings and coverage gaps without a duration cutoff. A reviewer that never reads its captured input
fails with that reason rather than completing with no coverage. L2 observes the run and can cancel if
it gets stuck or goes off scope. No suitable engine means explicitly unavailable; there is no automatic
retry or engine switch after launch. Fallback cannot bypass unavailable observation or cancellation.
Reviewers use native reasoning defaults; the project's L2 owner effort setting does not change
reviewer eligibility.
`--engine` and `--model` select the reviewer for one request without changing project defaults or
other launches. The selection is the only candidate: installation, account/model rejection, quota and
captured-input capability checks still apply, unknown allowance stays explicit, and an unavailable
selection refuses the request or fails the run instead of substituting another reviewer. A bare model
name needs `--engine` unless the engine seam recognizes it. The saved review keeps the requested
`selection` beside the effective engine/model. A selection belongs to one request: a repeat without
one routes automatically and shows that reviewer. A repeat or replacement keeps the prior
focus unless it names a new one; an operator request's focus always stays and owner focus is added to it. Naming a request that is still waiting to run with `--previous` and a different
selection replaces it: the new request keeps an operator requester's authority and the replaced one is
recorded as withdrawn with its replacement.
Saved review receipts remain readable after a project is detached; new review is unavailable.
Service inspection must work before launch; failure refuses the invocation without spending a review.
Use the supported review endpoint so observation and cancellation remain available. Launcher exit alone
does not prove reviewer termination. If inspection fails, preserve the receipt and block for recovery.
Live-provider testing requires a separate operator decision; no new live trial or retry is authorized.

```sh
alt task review status
alt task review request --subject changes --focus "Challenge the changed task and landing journeys"
alt task review request --subject proposal --focus "Challenge the proposed authority and failure handling"
# For a request made in chat, retain its original authority and deduplicate it:
alt task review request --source-message <message-id>
# Commit the intended checkpoint first; uncommitted tracked changes cannot be captured.
alt task review run --review-id <id>
# A proposal review requires the exact original L2 proposal message:
alt task review run --review-id <proposal-review-id> --proposal-message <message-id>
alt task review assess --review-id <id> --file /tmp/assessment.json
# Select a deliberately revised proposal when assessing its later version:
alt task review assess --review-id <proposal-review-id> --proposal-message <revised-message-id> --file /tmp/assessment.json
alt task review cancel --review-id <id> --reason "The owner needs to stop"
alt task review withdraw --review-id <id> --reason "Why this L2-requested review is unnecessary"
# Repeat a finished review; --previous names the review it replaces:
alt task review request --subject changes --previous <id> --focus "Review the later revision"
# Review new material while earlier reviews and their open findings stay in the merge gate:
alt task review request --subject proposal --additional --focus "Challenge the security addendum"
# Select the reviewer for one request, or re-select a request still waiting to run:
alt task review request --subject proposal --engine <engine> --model <model> --focus "Challenge the wording"
alt task review request --previous <waiting-id> --model <model>
```

Task defaults to `ALTITUDE_TASK`; an explicit task follows the action. `--subject` defaults to
`changes`. Commands fence mutations to the current owner attempt. Operator requests can only be
skipped by the operator's **Skip review**, which asks no reason; L2's withdrawal of its own request
names one. Open questions do not prevent either subject: a request can continue a
question-blocked owner solely to prepare, run and assess review. Every open question, its revision, its card
and any merge hold stay unchanged, and implementation or merge still needs its own approval. Because review
freshness covers the task conversation and decisions, a later answer or resolution needs reassessment before merge.
L2 selects the original proposal message; missing concrete proposal input prevents reviewer invocation.
Changes review captures the task branch merged onto current `origin/main`. When the branch conflicts
with main, `request`, `run` and `assess` refuse with the conflicted files; reconcile the branch with
main and commit before retrying.
`run` is a fixed daemon operation, not an operator grant. It accepts repeated `--context-message`
IDs to select L2 proposal/test evidence; original operator/L3 messages and later corrections remain
included. Default capture includes all L2 messages. The captured context holds one copy of each input and
fails explicitly beyond 256 KiB, naming its size; when authority, corrections and decisions alone exceed
the bound, the refusal says selection cannot help and the owner reports a capture fault. A changes review
that needs an approved proposal selects that L2 message with `--context-message`. A proposal review
captures the exact proposal text separately as `proposal.md`, bounded to 64 KiB, so the proposal never
competes with retained authority.
For image context, supply an L2 textual account and select that message explicitly; the capture
records that original image bytes are not reviewed. The reviewer cannot run tests.
The snapshot holds the candidate's ordinary tracked files and the patch from `origin/main`; links and
special entries fail the capture. A path over 2 MiB in either tree stays out of the source and patch; the
captured context and the review record's `omitted` list name each by path and size. Snapshots whose
remaining files exceed 64 MiB per tree or 10000 files fail explicitly.
The receipt retains selected message IDs, source/candidate identities and captured-input hashes;
proposal evidence also binds the original proposal message and its exact captured text.

Assessment JSON contains `reason` and `dispositions`, one entry per finding:
`{"finding_id":"F1","disposition":"fixed","reason":"Evidence for the fix"}`; `dismissed` and `open`
also require evidence. `open` records a finding the owner honestly leaves unresolved: the assessment is
saved and shown with its unresolved findings, but it does not clear the review. Landing refuses to
merge while any current assessment has an open finding, and a new request cannot replace that review
until a later `assess` by the current owner gives evidence-backed `fixed`/`dismissed` outcomes. That
assessment then faces the ordinary freshness checks; withdrawal authority is unchanged.
`--additional` requests another review of the subject that replaces none, for example of a proposal
addendum. It needs every current review finished and assessed, cannot name `--previous`, and has its
own focus and requester. Earlier reviews keep their open findings, requester, focus and withdrawal
authority, and still block merge however the additional review turns out. Repeating the
additional review with `--previous` replaces only it.
With no findings, use an empty array and an assessment reason. Commit fixes before
assessing; post the outcome explanation before assessment so it is included in the final context.
For a held PR, reassess after reading the operator's merge approval, including any conditions.
This updates L2's assessment without another reviewer invocation.
Code, base or subsequent conversation changes require changes reassessment before merge. During
[concurrent landing](#concurrent-landings), the current owner can assess the pinned integrated candidate
while the original landing command retains its turn; this invokes no reviewer. Later
proposal/source/context changes require proposal assessment or deliberate new review; optional
`assess --proposal-message` identifies a deliberately revised proposal. Every accepted request must be
assessed or authorized for withdrawal before merge, including earlier changes requests. Later L2
assessment stays separate from original findings; proposal evidence never establishes implementation
acceptance, transfers ownership or access, or releases an approval question or merge hold.

### Task design previews

Before requesting visual approval, the current L2 publishes the proposal's selected screenshots and
explanation with its ordinary question. The resulting **View preview · saved title** link in Needs you and the task
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
**View preview · Compact chat proposal** entry is rendered in Needs you and beside the question in
its conversation. The offscreen question jump returns to that entry. The link and viewer heading use
the captured title, not the question revision as a proposal version. Publication follows
the ordinary L3-first audience rules unless explicitly directed to the operator.

The question contains the captured text and titles, and the task retains the selected image bytes.
Source edits and worktree cleanup leave the saved version intact. Repeating identical inputs keeps
the revision; publishing changed screenshots, text or labels advances the same pending question.
For an existing design question, use its same reason and add `--design-file`; its choices stay intact
when replacement recommendation flags are omitted. An ordinary block with no design file parks the
existing question and retains its saved preview.

Old links retain their captured title and content, say **Earlier preview** when superseded, and
identify a newer question when one exists. Missing or
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
previous PR is merged and its merge commit is on current main. It retains
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
default `make test` is unsuitable; it runs as [one argv command](#dry-run-and-gate-selection)
on the exact squash merge candidate. The live task
owner and merge hold are rechecked before merging. The original branch receives only fast-forward
pushes; rejected pushes never retry with force. `--merge` squashes the PR into one commit on main,
as ordinary landing does, and requests no branch deletion; the PR keeps its original commits.
Host-side branch deletion settings remain the repository operator's policy.

`--merge` incorporates current main into the task branch while holding the repository turn. If
that integration conflicts, reconcile manually while preserving the adopted commits with a merge
commit:

```sh
git fetch origin main
git merge --no-ff origin/main -m "Merge main for validation"
```

Resolve conflicts in the task worktree, rerun applicable checks and review, and land again.
Landing pins current origin main and the PR head and confirms GitHub's authoritative base target.
Base-only movement repeats integration and fresh checks within the original deadline; head or PR
identity movement refuses. A lagging PR `baseRefOid` alone does not block that pair.
Task merge holds, recorded operator approval and the normal report/archive workflow also apply
to adopted PRs.

### Recorded merge approval

The owner applies the operator's approval from its own task chat while landing:

```text
alt land --merge --approval <message-id> --message <summary>
```

The owner judges the message's scope from its words, conditions, later corrections and the actual
integrated result. Approval covers the approved outcome, not a commit: a new head from routine integration
(current main, mechanical conflict resolution) with unchanged approved content needs no new approval.
Explicit exact-version or other unmet conditions, revocation, a renewed hold or a material departure
from what was approved need the operator again. After checks pass and just before merge,
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

### Operator grant

A worker's own shell covers builds, tests and installs inside its workspace. Anything else the
operator asks a task to do and permits, such as changing a service unit, reloading or restarting it,
installing a user-level toolchain, deploying, publishing a release or package, or writing to an external
service, runs under one operator grant: the operator's yes to a stated purpose, after which the owner runs
each command as the operator with `alt task run`. Disposable installation VMs, containers,
browser and other candidate checks run through [validation runs](#validation-runs) instead,
with no grant. The approved Mac fictional harness uses the existing Seatbelt runner without
Chromium's inner sandbox; dual-protection checks use the Linux container. A grant never substitutes
an outside-worker browser run or changes worker permissions. Host
container deployment work uses the [standing container approval](../AGENTS.md#container-operations-on-this-machine):

```text
alt task grant <slug> --approval <message-id> --question <id> --revision <n> [--source task|project] --reason <why>
alt task grant <slug> --standing-policy <heading> --source project --approval <message-id> --question <id> --revision <n> --attempt <n> --reason <why>
alt task grant <slug> --from-task <earlier-slug> --approval <message-id> --question <id> --revision <n> --attempt <n> [--source task|project] --reason <why>
alt task grant <slug> --revoke --reason <why>
alt task run <slug> <command>
```

A granted command runs with the operator's access, including their user services and their GitHub
login. altd records every command but does not judge intent from shell text, so the grant trusts the
owner to stay inside the approved purpose. No grant is asked or recorded for loosening confinement
(a worker sandbox, engine permission settings, Altitude's guards or hooks, the operator's user-level
engine settings), for other tasks' worktrees or other projects' data, for releasing Altitude's own
gates (merge holds, review findings, proposal checkpoints keep their own answers), or for entering or
printing credentials, which stays a step in the operator's [task terminal](#reading-the-task-terminal).
Owners and L3 keep to this list, and the per-command record makes any departure visible.

The grant reaches each engine's own guard through the engine seam. A Claude owner's session
settings carry one allow rule, `Bash(alt task run <slug> *)`, which Claude Code resolves before its
auto-mode classifier, so a granted command is not judged again; without a current grant altd refuses
the call, so the rule adds nothing, and a grant or revocation takes effect in the running session.
A Codex owner's sandbox is unchanged: its shell reaches altd as for any `alt` verb, and the granted
command runs outside the sandbox, so the worker's own shell still never reaches the user service bus.
Whether Claude Code admits a particular call is observed on use, not by the provider-free tests; when
a guard still refuses, the owner puts that one command in a `run` block for the operator's terminal
rather than rewording it. Rules Altitude does not control, such as a repository ruleset or billing, are
reported as the operator's remaining step.

Without applicable standing project approval, the owner asks the operator once per purpose,
in a plain question naming the purpose and its bounds
(what may run and what may not, limits, cleanup, verification and when the purpose ends), and
resolves the operator's answer with `alt task resolve`. Whoever records the grant cites that same
message after judging that the answer is a yes: the running owner for its own current attempt from a
task-chat answer, as it applies a merge approval, or L3 or the operator from either chat (`--source
project` for project chat, after which L3 resumes the owner). The rest is mechanical: the cited message must be the operator's own and must have answered the current
revision of that operator question with no remainder. The grant binds to the task's current
attempt; the owner, L3 or the operator may revoke it. Success stores `grant` (id, purpose,
answer, approval, question/revision, attempt, actor, time) and a `grant` event; refusals
record `grant-refused` and change nothing. Each command's row names the grant id it ran under;
recording the same answer again keeps that id. Revocation records `grant-revoke` and refuses every
later command; altd asks the service manager to stop a command still running under the revoked grant
on every poll until it has ended, including after a restart. Whatever the command already did stays
done, and its row says the grant was revoked while it ran.

The operator's yes to a purpose also covers another task of the same project that needs that
identical purpose, for example when the task that asked has closed and a new task carries on its
remaining work. Only L3 applies it, with `--from-task <earlier-slug>` naming the task whose question
the operator answered and `--attempt` naming this task's current attempt. The same mechanical checks
run against that earlier question and answer; the grant records the purpose as the operator approved
it and `from_task`, binds this task's current attempt and is revoked as usual. L3 judges later
corrections and whether the new work is the same purpose; any narrower or wider purpose is asked again.

For standing approval, the owner copies the complete paragraph under **Container operations on this
machine** in the project's committed `AGENTS.md` into an L3-directed `alt task block --reason`
(without `--for-operator`). Task-specific steps, cleanup and verification belong in the preceding
reply. L3 reads the original approval and later corrections, then records the grant with
`--standing-policy 'Container operations on this machine'`, the paragraph's approval ID, the current
question/revision and `--attempt`. No new operator answer is required. The owner cannot self-record
a standing grant. L3 resumes the owner with the recorded purpose; the owner resolves that dependency
from L3's message using the ordinary question flow.

The grant reads only `refs/heads/main:AGENTS.md` in the registered project checkout, pinned to a
commit. The heading must be unique and contain exactly one paragraph citing the backtick-quoted
approval ID. That ID must name an original operator message in the same project's chat. The current
open question must be owner-authored, directed to L3 and match the paragraph's raw Markdown apart
from whitespace. Missing or ambiguous policy, another project's approval, a stale attempt/revision,
and a wider or paraphrased purpose refuse. The record and event retain the policy file, heading,
commit and full text alongside the original operator approval, exact question and L3 rationale.
This checks the recorded purpose; it does not classify shell commands or infer consent from prose.

One grant occupies the task's grant slot. Revoke it before switching to or from a different standing
purpose; the standing grant grants the entire policy scope, while its `--reason` retains the task's
plan and limits. L3 stops applying a revoked approval immediately, revokes active grants with
`--revoke`, and assigns removal of the policy paragraph. Existing grants do not automatically reread
policy or interpret later chat. Widening a standing policy is a security decision under the project's
review rules; another project needs its own operator approval and committed policy.

The purpose grant covers iteration until the purpose is done: run, inspect, correct and retest,
including after a failed attempt, without approval for each command or attempt. One command at a time
is an execution limit. The grant records the purpose as approved, no narrower; a one-run limit exists
only when the operator's answer sets one, because a single run is itself the risk. A materially
different access, service change, spend or live-provider test still needs its own answer. Retain
evidence and revoke the grant when done.

`alt task run` is the current owner's verb for its own task. altd accepts it only from a process in
that owner's current worker job, as it does for `alt task validate`, so another agent holding the
machine key cannot run commands under this task's grant. It records the run in `machine.jsonl`
first, then runs the command as the operator in a transient user unit outside every worker sandbox,
in the task worktree, through a login shell, with the user service manager reachable and the
owner's task identity in the environment, so `alt` inside the command acts as that L2. One command
runs at a time per task, for at most `MACHINE_COMMAND_TIMEOUT` (600 seconds), and at most a quarter
of the computer's cores (at least one) run at once across the machine; `alt machine show` reports
that number as `machine_runs`. A command that finds every place taken waits its turn in arrival
order, for up to 30 minutes, and prints what it waits for on standard error: the commands holding
the places, when their limits end and how many requests are ahead. Stopping or interrupting the CLI,
or losing its connection, takes the command out of the line, or stops it and records why, and frees
its place ([machine runs](DEVELOPMENT.md#validation-runner)). The unit itself
writes its output to `<unit>.log` and its exit status to `<unit>.exit` in the task folder, so the
result outlives altd. The CLI prints the output and a status line, then exits with the command's
status (124 on timeout). Each run completes its `machine.jsonl` row, with the finish time the unit
recorded, and adds a `machine-run` task event and a `machine-run` project event with the command,
unit, exit status and purpose. When Altitude restarts during a command, including one the command
restarts itself, the next altd follows every unfinished row's unit to its end, stopping it if its
grant was revoked meanwhile, and completes the row and events from that saved status; the CLI tags its call with a request id, reconnects with it
and prints the same command's result without running it again. A missing grant, a non-running
task, a stale attempt, a grant from an earlier attempt or a revoked grant refuses with the reason,
as does a request id from an earlier attempt. A unit stopped at the limit is reported as a timeout,
and any other end without an exit status, including one that happened unseen while altd restarted,
as an explicit uncertainty with its reason, never as success. When altd cannot be reached at all,
the CLI fails at once. `alt task status <slug> --brief` shows the active purpose.

The door is altd's operator-trusted HTTP surface, which every worker on this single-account host
can reach, the same surface that answers questions and posts messages. altd checks the task record,
not which local process calls; the grant record and its per-command log are the boundary.

### Validation runs

```text
alt task validate [--kvm] [--publish PORT] [--simulator [--capture]] -- <command>
```

The current owner runs one command against a throwaway clone of its task's committed `HEAD`, isolated
from the operator's runtime: a disposable rootless container that altd starts from its own image on
Linux, and a job under the validation sandbox profile on macOS. No grant is needed; the Settings switch
**Validation runs** turns the verb off for the whole computer. On Linux, `--kvm` adds `/dev/kvm`, and
`--publish` forwards a container port to a free loopback port and prints it; a macOS run refuses both
and binds free loopback ports itself. On a Mac, `--simulator` adds a disposable iOS Simulator iPhone
whose Safari, and two fixed native walks, the command reaches through the relay socket in `$SIMULATOR_INSPECTOR`
and which trusts the run's own HTTPS certificate in `$SIMULATOR_HTTPS`, kept as a screenshot and removed when the run ends ([iOS Simulator runs](DEVELOPMENT.md#ios-simulator-runs)); `--capture`
also records its screen as `validation/<n>.simulator.gif` ([validation captures](DEVELOPMENT.md#validation-captures)).
The command starts in the clone, and whatever it writes to
`$VALIDATION_RESULTS` (`/results` in the container) is copied to the task folder's `validation/<n>/`,
and its output to `validation/<n>.log`. The run is recorded in `machine.jsonl` with purpose
`validation`, its commit and tree, the host's OS and architecture, what isolated it, how it ended and
whether cleanup finished, and adds the same `machine-run` events as `alt task run`. The CLI prints the
output and a status line with these, and exits with the command's status (124 on timeout) only when
the run also ended cleanly; a stop, failure or failed cleanup exits 1. altd accepts the request only
from a process in the task's current worker job. A non-running task, a stale attempt, another caller,
a turned-off switch, missing KVM, a host without an iOS Simulator for `--simulator` or low disk refuses with the
reason. One run uses the machine at a time: a request that finds it busy waits its turn in arrival order, for up to
70 minutes, and prints what it waits for (the task whose run holds the machine, when that run's limit ends and how
many requests are ahead) on standard error. Stopping or interrupting the command, or losing its connection, takes
the request out of the line or stops its run at once, recorded as `stopped`, and frees the machine. The
[validation runner](DEVELOPMENT.md#validation-runner) describes the isolation, its limits and cleanup.

### Reading the task terminal

`alt task terminal` prints the current owner's own task terminal output: a status line (`terminal running`,
or `terminal ended` with its reason and exit code, and whether earlier output was dropped), then up to the
last 256 KB the terminal printed, as plain text. `--json` prints the record. It needs no grant and reads
only: nothing it does types into, resizes or closes the terminal. altd answers only the running task's
current attempt, and only a connection made from a process in that owner's own worker job, so another
task's agent cannot read it. The last ended terminal's output stays readable until a new terminal opens
for the task, the task finishes or Altitude restarts; after a restart the command says no output is
available. Output the owner reads becomes part of its session and provider record; save only what the
task's evidence needs.

When the operator opens the owner's `run` command in the task terminal, altd tells the owner how it went
with a Terminal notice at its next checkpoint, waking it when blocked: the command looks finished (the shell
held the foreground again for a second after Enter on it, and no job it started is suspended or in the
background), or the terminal ended before the command ran or finished. Only the attempt that handed the
command hears about it. The notice names the command and is a prompt to check, not proof that it ended: it
carries no exit status, and a command waiting for input, such as `read`, can look finished. The owner reads the
output with `alt task terminal` and verifies that the command ended and how. Ctrl+C before Enter drops the command without a notice. The
notice is not a chat message and grants no approval, access or authority; a stopped or faulted task keeps
it for its next resume.

### Reading the project terminal

`alt project terminal` prints the project terminal's output for the project's coordinator, in the same form and
with the same limits as `alt task terminal`: a status line, then up to the last 256 KB as plain text, or the
record with `--json`; it never types into, resizes or closes the terminal. altd answers it only on the project's
coordinator socket, so the ordinary CLI, task owners and other projects' coordinators cannot read it. Output stays
readable until a new project terminal opens, the project is removed or Altitude restarts.

When the operator opens the coordinator's project-chat `run` command in the project terminal, altd follows it as
it does an owner's and queues a Terminal notice as a coordinator turn once the command looks finished or the
terminal ended first. The notice is the same prompt to check, naming `alt project terminal`; a command the
operator types without a `run` block sends none. Output the coordinator reads becomes part of its session and
provider record.
