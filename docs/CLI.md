# Altitude CLI

`bin/alt` is the one engine-neutral door into Altitude. `--project <name>` or
`ALTITUDE_PROJECT=<name>` selects a project. Commands that change task state are validated against the
task record and current attempt under the project lock.

Start with [setup](SETUP.md) to build the app, install project Git guards, register a project
with Auto preferences or explicit pins, and start a conversation. `alt project add` registers state; it does
not install Git guards. [Operations](OPERATIONS.md) covers project settings and service lifecycle.

## Inspection

`alt task status <slug>` and `alt task report <slug> --json` include `token_usage`: the daemon's
persisted local token observation, independent of the report's agent-authored spend. It contains
inclusive input/output and their observed total, optional cache/reasoning subsets, session rows with
engine/attempt/owner or delegated attribution, coverage notes, and checked/observed/finalized times.
These commands do not rescan provider logs. Missing counters remain null or partial; archived tasks
retain their final observation. See [token semantics](SESSION_LIFECYCLE.md#task-token-accounting).

These commands are read-only. They print compact text unless `--json` is present:

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
that same record. `alt monitor` reports quota and live provider sessions; `alt decisions` reports
tasks waiting for Burak.

`alt l3 tools` groups the shell commands persisted with recent L3 turns. Commands outside the `alt`
door appear first so recurring inspection pipelines are easy to replace with known verbs.

An L3 process is read-only on the deployment checkout and Altitude home. Its direct shell reads are limited
to Git log/diff-stat/show-stat and the altitude user journal. Runtime shims carry every `alt` invocation plus
`gh pr` view/list/diff/checks, GitHub issue/run inspection, and altitude service status over that project's
same-user altd Unix socket. The socket fixes the project independently of request data. The broker re-applies the
L3 command door, accepts flat task identifiers and stdin rather than `--file`, and binds GitHub reads to the project's
repository; source editing, Git writes, direct GitHub mutations, service control, direct command networking, and cross-project verbs are unavailable.

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
PRs, lease, progress, messages, provider history, questions and merge holds remain. Prior reports remain
history; the fresh attempt establishes its own current verification. A queued receipt
establishes only admission; recovery requires observing a new running attempt. Ordinary `task resume`
keeps its provider session and launch model. Handoff supplies no approval for pending decisions or holds.

Recognized model allowance exhaustion is scoped to that model. Unknown reset times stay unknown and
create no resume timer; only a reported reset schedules one. The routing observation's thirty-minute
expiry permits later availability checks without claiming that an allowance reset. Recovery changes no
project routing, purchases no credits and implements no usage reset. After a repair, L3 verifies its
activation and each owner's current status before requesting handoff; completed owners need no transfer.

### Durable CI recheck

L3 or the operator records one probe against an existing fault-blocked task in its project:

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
of that workflow, branch, event and PR. Otherwise it reruns the selected run once. Freshness uses
the scheduled check time, so evidence from before the intended wait does not satisfy it. Altd saves
the baseline attempt and intent before submission, then reads attempt metadata to reconcile uncertain
writes without blind resubmission. Reruns use their original workflow; they do not adopt a new base
workflow. Each API call has a twenty-second limit; reads run at five-minute intervals and stop at
three failures, twenty-four rounds or two hours after the scheduled time. Missing artifacts remain
unverified, including on a green run with a tolerated upload error. Fresh nonexpired, nonempty
artifacts from the observed execution establish successful upload, not another owner's candidate readiness.

Unchanged results finish silently with evidence in status. Changed results or exhausted probes reach
the originating L3 through a retained queue row, with at most two handling attempts and a one-hour
delivery deadline; queue/history IO also has two attempts. Terminal chat evidence repairs a
crash after handling. If provider execution began but no terminal evidence survives, delivery ends
uncertain without replay. Its execution timeout survives daemon exit and stops the process tree
with a five-second grace period. Failed delivery remains visible in status; it starts no new repair task.
Ordinary task controls remain available and invalidate stale probe actions. Questions, faults and
merge holds retain their meaning. L3 verifies actual repair before separately resuming affected owners.
Promise follow-through only after status shows a saved next action and time; a terminal record with
no next action is not scheduled monitoring.

### Historical evidence search

`alt l3 search "index migration" --limit 10 --json` searches the selected project's human
conversation, active and archived task conversations (including decision/acceptance messages),
report string fields, and completion digests. L3 uses its usual runtime command or coordinator MCP
request `{"kind":"alt","args":["l3","search","index migration","--json"]}`. The socket fixes the
project; the operator CLI can select one through the usual `--project` option. L2's command authority
is unchanged. Source paths resolving outside the project are refused.

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

An empty corpus, absent optional conversation/report files or no match yields `status: no_results`,
an empty result list and an explicit no-evidence message. Corrupt or unreadable evidence returns an
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
authorization; the [owner instructions](../personas/l2.md#incremental-delivery) define acceptance and
safe delivery. A task can complete its agreed increment while the parent issue retains outstanding
scope. Altitude's [project rules](../AGENTS.md#roles) retain proposal checkpoints and implementation
constraints. Closure follows the cumulative evidence rules below.

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
outcomes. Coordinator state and fault/restart messages summarize fault-kind counts and up to five
outcomes, showing gaps first. Inspect the full list for the remaining rows.

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

Confirmed links are reused without another creation, including after restart or a later incident
window for the same source project/fault kind. Different kinds share a report only through an
explicit verified link. The daemon saves uncertainty before publication: a timeout, interrupted
request, nonzero GitHub exit or unconfirmed response may have created the issue and prevents another
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

## Task file leases

`alt task paths <slug> <complete-comma-separated-lease>` is available to the operator and the
project's L3 through its coordinator transport, and denied to L2. It replaces the complete lease
in any task state, so include existing paths that still belong to the task. Status exposes the
saved lease; the `paths` event records actor, previous paths and assigned paths. Assignment alone
does not resume a task or change its owner, attempt, session, worktree, branch or merge hold.

For a fictional `demo` recovery task missing `docs/archive/`:

1. **L2:** inspect `alt task status "$ALTITUDE_TASK"`, checkpoint `progress.md`, then run
   `alt task block "$ALTITUDE_TASK" --reason "Please add docs/archive/ to my lease to review the preserved documentation."`
   and stop before editing or applying that scope. This goes to L3 without `--fault` or an
   operator escalation flag. L2 cannot claim paths itself and must not use `alt land --paths`
   to bypass the recorded lease.
2. **L3:** inspect `alt task status recover-demo` and the scope request through the project's
   coordinator transport. For authorized scope and an empty lease, run
   `alt task paths recover-demo docs/archive/`. If the task also needs its existing `README.md`
   scope, use `alt task paths recover-demo README.md,docs/archive/`. A failed assignment leaves
   the request blocked; a decision beyond the authorized scope follows the normal escalation path.
3. **L3:** inspect `alt task status recover-demo` again. Only after the required lease is recorded,
   send `alt task message recover-demo "docs/archive/ is recorded in your lease; continue reviewing in your worktree."`.
   The message requests the normal daemon resume of the same owner session.
4. **L2:** on resume, verify the recorded lease in `alt task status "$ALTITUDE_TASK"` and resolve
   the scope question against L3's reply with `alt task resolve` before continuing. Work stays in
   the same task worktree and goes through the usual staging lease, checks, hold and PR path.

## Dirty-checkout recovery

`alt task preserve-checkout <slug> --reason "…"` asks altd to preserve the selected project's dirty
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
4. Inspect the intended reconciliation scope and the task's lease. L3 or the operator assigns
   any missing scope with `alt task paths reconcile-edits <complete-comma-separated-lease>`,
   retaining existing required paths, then verifies the recorded lease in task status.
5. Give the owner the branch, SHA and reconciliation scope with
   `alt task message reconcile-edits "Inspect archive <branch> at <SHA>; apply the reviewed snapshot in your task worktree using the CLI recovery procedure and deliver through your PR."`
   This message requests resume. The owner verifies its lease and inspects the snapshot before
   applying, staging and publishing it; later missing scope uses the [L2-to-L3 route](#task-file-leases).
   Resume any other blocked task separately with `alt task resume <slug> --reason "Checkout is clean after preservation"`.

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
conflicts in the task worktree and commit only reviewed, leased changes through the normal PR path.

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
A restart alone does not resolve the fault, and unsuccessful resume leaves it reported.

For upstream defects, the originating L3 checks public delivery evidence and local observations
that the actual cause is gone before `alt task resume <slug> --reason "<verified fix and observation>"`.
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

### Concurrency limits

Defaults are **8 running L2 tasks per project and 80 across the machine**. The operator can inspect
both scopes, including active values, defaults, explicit overrides and pending/completed requests:

```sh
alt machine show
alt project list
alt project set example --wip 12 --reason 'Allow more parallel tasks in this project'
alt machine set --wip 120 --reason 'Allow more parallel tasks across this machine'
alt project set example --unset-wip --reason 'Restore the project default of 8'
alt machine set --unset-wip --reason 'Restore the machine default of 80'
```

`machine show` reports the effective machine `wip`, its `override` (null when inherited), `default`
(80), `default_project` (8), and each project's effective `wip` and stored override. `request.status`
shows whether a change is still `pending` or has completed. `project list` shows stored project
configuration; a missing `wip` inherits 8. Registration accepts an explicit cap with
`alt project add example --path /path/to/repo --wip 12`.

Machine caps accept positive integers with no fixed ceiling of 80. Project caps accept positive
integers up to the **currently effective** machine cap, so raise the machine cap first and wait for
`machine show` to report it active before setting a larger project cap. Zero, negatives, fractions,
booleans and nonnumeric values are rejected. Reset removes the selected override; existing explicit
project caps remain respected, including an explicit 8 or 3.

The operator runs machine commands from their own terminal. A project's L3 can set/reset its own
project cap through its existing `alt project set` transport. L2 cannot change either cap; L3 cannot
change the machine cap. A nonempty reason is required. The CLI queues one durable daemon request;
altd applies it on its next tick, without a PR, service restart or free task slot. Repeat inspection
to confirm `request.status: done` and the active value. Identical retries reuse the existing receipt
and audit event while the stored setting still matches. The machine override is in
`$ALTITUDE_HOME/settings.json`; requests and audit events use the same operational settings protocol
as project caps. Use these commands to change settings.

Lowering a cap lets running work continue. Queued tasks and due resumes wait until running counts
are below both the project and machine caps. A lowered machine cap does not rewrite existing project
overrides; it bounds their aggregate launches. A project reset restores 8 even when the machine cap
is smaller. These limits count running tasks, not the engines' native helpers or L3 turns.

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
alt task new --title <title> [--paths a.py,b/] [--hold-merge <reason>] -
alt task message <slug> <text>
alt task reply <text>
alt task block <slug> --reason <question> [--recommendation <approach> --label <action> --why <reason>] [--for-burak | --fault]
alt task escalate <slug> --question <question> [--recommendation <approach> --label <action> --why <reason>]
alt task resume|stop <slug> --reason <reason>
alt task hold-merge <slug> --why <reason>  # Burak alone may use --off
alt task done <slug> --digest <text>
alt task reject <slug> --reason <reason>
```

Repository changes use `alt land --message <message> [--merge]`. Project, incident, service, TLS,
and installation commands remain available through `bin/alt --help` and the relevant subcommand
help.

For L3 and shell callers, `resume`, `stop`, and `reject` append one task-local daemon request and one
`daemon-request` event containing the task, operation, actor, reason, and request id. Altd performs the
worker or session effect, refuses a changed state or identity, and makes an identical retry idempotent.
A repeated reason after a genuine later lifecycle creates a new request against that lifecycle's identity.
A message to a blocked task uses its durable inbox and `resume_after` handoff instead of launching a
worker in the caller. Coordinator messages to faulted tasks stay non-waking; verified recovery uses
the explicit reason-bearing resume. L3 cannot call `task block` directly: an L2 blocks itself with its attempt fence,
while L3 uses reason-bearing `task stop` so altd blocks the task and stops the same observed worker.

### Conversational decisions

`block` is the current L2's question to L3; its operator flag uses the operator audience. L3 can
`escalate` the actual dilemma and explicit recommendation. Both publish into the owning L2 human
conversation with their source attribution. The model chooses a plain question, one recommended
quick action, or two to three explicit options with one recommendation. A fault is operational and
uses `--fault`, without inventing a recommended choice.

For up to three independent questions upfront, pass `--questions-file <file>` to `block` or
`escalate`. Use `--questions-file -` with JSON on stdin when calling through the L3 broker; the
broker never reads a server file supplied by the caller. The single `--reason` / `--question`
flags remain available for one question. A grouped payload has this form:

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
stays together while its members are discussed, answered or revised.
The limit is three total members in that group; answered members retain their receipts. Start the
next group after the current relevant questions settle. Dependent questions wait for their prerequisites.
For an unchanged existing question, omitted options preserve its saved choices; `options: []`
explicitly removes them. Changed question text with omitted options becomes a plain question.

The worker handoff names the pending question ID/revision and each task message ID. The owning L2
judges the reply in context: discuss a follow-up, clarify genuine uncertainty, or record a clear
decision and continue. No special approval phrase or extra confirmation is required.
One typed reply can answer several members. Its saved question references name what the operator
was viewing; cite the same message in a separate `resolve` call for each answered or obsolete member.
Only unresolved, still-relevant questions remain in Needs you. Quick selections can also be sent
together as one batch; a stale member prevents the whole batch from writing.
After answering a follow-up, checkpoint and park with the same `block --reason` text. Omitting
replacement recommendation fields keeps the saved question and approach. Parking or revising an
unresolved operator dilemma preserves its required decision-maker.

```text
alt task resolve <slug> --question <id> --revision <n> --message <source-id> \
  --source task|project --disposition answered|superseded --reason <chosen-scope-or-closure-reason> \
  [--l3-authority <specific-evidence-and-rationale>] \
  [--remaining <still-relevant-question>] [--recommendation <approach> --label <action> --why <reason>]
```

`--source task` (default) cites a durable task message ID. `--source project` cites the original
operator chat `turn_id`, available from the project's recorded chat; an L3-authored relay is not an
operator source. The existing CLI door checks owning task and attempt, and the resolution checks
source provenance and question/revision. An L3 answer can settle an L3-audience question. For an
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
provide a recommendation only when it applies to the remaining question. Follow-ups alone require
no resolution operation. A repeated identical resolution reuses its record; stale or conflicting
resolutions are refused. Neither this command nor ordinary resume releases a merge hold.

When L3 sends a material direction or delivery update, reassess the plan and each open member against
its sources. Cite the original operator message for an operator decision relayed through L3; request
missing evidence from L3. Record obsolete members as `superseded` with the specific changed direction
and reason, retaining unanswered relevant members. Proposed or ongoing work is not verified delivery.
Continue within authorized scope; a new operator choice or scope expansion still needs a decision.

When a provider limit queues a fresh attempt, existing question replies and quick acceptance wait
in the normal inbox. The new owner receives the current question or receipt in its brief; semantic
resolution remains an operation of the running or blocked owning L2.

### This repository's temporary local gate

The operator's 2026-09-09 Pacific policy selects verified local checks for this repository through
the trusted configuration seam. Use the existing commands:

```sh
alt land --message "fix: describe the change" --pr-body-file /tmp/pr.md
alt land --message "fix: describe the change" --merge
```

Each invocation tests its actual current merge candidate with `make check`; `--test-cmd` cannot
replace that command here. Frozen web dependencies are installed in the candidate first; supported
tooling and the matching shared Chromium remain prerequisites. `checks` is `local-pass` or
`local-fail`, and `local_tests` binds the result to base/head, candidate SHA, tree and retained
evidence directory. Successful current validation updates the PR body with one passing-test line.
The synthetic commit can differ from GitHub's final commit metadata; compare its tree and bound
parents to verify delivery. A new base or head needs a new run.

Hosted failures on existing PRs do not supply this gate's verdict. Required hosted branch checks
still refuse local delivery until the operator removes them. Reviews and live task merge holds
remain mandatory. Opening a held PR runs validation without merging; later landing revalidates.
Other repositories keep their hosted/no-CI behavior and local command choice. See
[bootstrap, owner recovery and restoration](DEVELOPMENT.md#ci-and-candidate-identity).

### Continue after a PR merges

An active task can deliver more than one PR. Its owner continues authorized work in the same
conversation, isolated worktree and local branch, then uses the usual command:

```sh
alt land --message "fix: finish the remaining work" --pr-body-file /tmp/next-pr.md
alt land --message "fix: finish the remaining work" --merge
```

Landing verifies the earlier merge on fetched main, commits leased edits, and puts only follow-up
commits onto current main before opening a fresh PR. This handles squash history and a deleted
remote branch. Previously published changes are not duplicated. An unchanged retry reports
`checks: merged` as historical delivery, never a new check pass, and creates nothing. If main
already contains the follow-up, no extra PR is needed and the current receipt is reconciled.

Every PR requires its own current checks and appropriate review. A recorded approval releasing
one PR's hold restores that hold for the next PR; a later explicit task-wide release remains
effective. Other unanswered questions, scope and ownership remain intact. Existing external PR
adoption remains supported, and further ordinary work after an adopted merge uses the task branch
while preserving its adoption receipt in history.

If updating onto main conflicts or fails, landing aborts the rebase and keeps the committed work
on the task branch. The refusal names the exact rebase command for the owner to resolve in that
same worktree. Follow-up merge commits require manual reconciliation first because replaying them
could drop merge-resolution edits. Unseen remote changes also require incorporation before
continuation. Failures after reconciliation or push can be retried with `alt land`.

Task `prs` and delivery events preserve earlier PR/head/merge evidence. The current `delivery`
receipt is recorded before waiting for checks; an unpublished delivery cannot complete the task.
The final `report.json` includes every delivery in `landed.prs` and current validation evidence.
Reports predating the current delivery, omitting earlier PRs or leaving unpublished work are
refused. Finish through the verified report path when all agreed work is done; a merge alone
does not require a new task or complete the current one.

### Adopt an existing PR

The task owner or operator can explicitly adopt an assigned, open, same-repository PR targeting
main. The task keeps its isolated worktree and `worktree-<slug>` branch. Its lease must include
the original committed paths and the complete PR diff, as well as task edits. Adoption refuses
another task's branch, foreign task trailers, unrelated local history and a PR already adopted
by another active task. Fork PRs are not supported.

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
  --message "docs: reconcile proposal" --paths docs/proposal.md --dry-run
alt land --adopt-pr 42 --expected-head <full-observed-head-sha> \
  --reason "This task is assigned to reconcile the existing proposal" \
  --message "docs: reconcile proposal" --paths docs/proposal.md
```

Use the actual full SHA observed from the PR. The head must agree with origin and be an ancestor
of local HEAD; already-present task-owned additions are allowed. If the local task branch has
diverged, incorporate the inspected PR with a merge commit carrying the exact
`Altitude-Task: <project>/<slug>` trailer. Never rewrite the original history. Add any reconciliation
edits before landing; `alt land` stages only the lease and adds the trailer to its commit.

The explicit command records the active immutable `adopted_pr` receipt and `pr-adopted` event before
publication, visible through `alt task status` and `alt task events`. `--dry-run` fetches and
validates the PR but records nothing and stages/pushes nothing. After adoption, ordinary
`alt land --message "…" [--merge]` reuses that PR and its original branch. Supply `--paths` again
if it overrides the task's declared lease. Retrying adoption with the same original PR/head is
idempotent; selecting another original head for that PR is refused. Later unowned commits cannot be adopted by
repeating the command. If origin moves, inspect and incorporate only changes belonging to this
task; unrelated history requires a separate ownership decision, not a broader adoption receipt.

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
explicit task authorization, its full lease, applicable checks and review; holds remain in force.
A recorded approval of the previous PR restores that review hold for the next adoption. An explicit
later task-wide `hold-merge --off` remains effective; earlier PR approval cannot release the new hold.

Complete the repository's applicable review before `--merge`. Drafts, requested changes and
outstanding required reviews block adopted merges. Checks must belong to the pinned base/head
candidate. Required checks, including those specified by active branch rules, must pass; missing,
ambiguous, unrelated or required skipped checks are not green. A nonrequired skipped Actions job
can be excluded only when its executed immutable workflow unambiguously identifies the job and
proves its complete condition false for the associated PR event. The supported conditions are
`github.event_name == 'push' && github.ref == 'refs/heads/main'` for `pull_request` or
`pull_request_target`, and `github.event_name != 'pull_request'` only for `pull_request`.
The latter is true for `pull_request_target` and cannot exempt its skipped jobs. Both conditions
accept surrounding `${{ ... }}` and spaces around operators; unsupported expressions or
ambiguous job/source mappings stay blocked. These rules apply to ordinary and adopted landing.
At least one applicable check must succeed.
Where no CI is configured, use `--test-cmd "<full suite>"` if the
default `make test` is unsuitable; it runs on the exact two-parent merge candidate. The live task
owner and merge hold are rechecked before merging. The original branch receives only fast-forward
pushes; rejected pushes never retry with force. `--merge` uses a merge commit and requests no
branch deletion, so the repository must permit that merge method. Host-side branch deletion
settings remain the repository operator's policy.

When main advances, preserve the adopted commits with a task-owned merge commit:

```sh
git fetch origin main
git merge --no-ff origin/main -m "Merge main for validation" \
  -m "Altitude-Task: <project>/<slug>"
```

Resolve conflicts in the task worktree, rerun applicable checks and review, and land again.
Landing pins current origin main and the PR head, confirms GitHub's authoritative base target,
and refuses actual base/head movement. A lagging PR `baseRefOid` alone does not block that pair.
Task merge holds, recorded operator approval and the normal report/archive workflow also apply
to adopted PRs.

If a task is already blocked on unowned PR history before its first adoption, resume still refuses
that history. The operator can run the adoption command above from the task's registered worktree
in their own shell, with `ALTITUDE_PROJECT=<project> ALTITUDE_TASK=<slug>` selecting the task.
Adoption records the receipt without changing the blocked state; the coordinator then requests
`alt task resume <slug> --reason "Existing PR adoption is recorded"`. A blocked worker cannot
land, and coordinators cannot invoke landing. Activation alone does not adopt existing history.

### Recorded merge approval

L3 can apply an existing operator authorization through its project-bound daemon connection:

```text
alt task hold-merge <slug> --approval <message-id> --presentation <owner-message-id> --latest-operator <message-id> --question <question-id> --revision <n> --pr-number <number> --head <full-sha> --reason <interpretation>
```

Read `alt task messages <slug> --json` (increase `--last` to include the original sources),
`alt task status <slug>` and `alt pr <number> --json`. L3 interprets the original operator decision
and all later operator messages, including reaffirmations or corrections. The source must authorize
this merge: design or implementation acceptance, ordinary discussion, ambiguity, unresolved conditions
and revocation do not qualify. `--reason` records why the sources authorize this exact merge and how
later context affects it. Meaning is model judgment, as with conversational question resolution;
the daemon validates original authority and binding, not arbitrary prose semantics. Never substitute
agent-authored text for an operator source or infer merge authority merely because a question resolved.

`--approval` identifies the original operator task message or saved UI-choice message.
`--presentation` names the owner's earlier message containing this canonical GitHub PR URL alone.
`--latest-operator` cites the latest operator message reviewed; it equals `--approval` when there is
no later message. A later reaffirmation stays separate from the original decision, even when attached
to a superseded question about mechanical recovery. It does not accept that question's recommendation.
`--question` and `--revision` are required together when the approval has question context; omit both
only for an approval without question context. For a question answer used as approval, the daemon
requires that question's current revision, operator audience and answered resolution from the same
original task message, with no remaining question. UI acceptance must match its recorded option.
The presentation precedes the question and approval. For a clear typed answer to an open question,
the owner records its decision with `alt task resolve` first. Messages in between, such as an operator
request for a plain explanation and the owner's reply without the URL, do not break the citation:
L3 reads them and still cites the original presentation.

Task chat automatically attaches previously resolved questions to later messages too. If the cited
question resolved before a fresh PR presentation, a subsequent conversational merge approval can
use that still-current revision as context only. Its earlier answer, whether answered or superseded,
is preserved and grants no merge permission. The new operator message supplies the authority;
the receipt records `question_context_only: true` and no selected option. A stale viewed revision
or question resolved after the presentation cannot use this path.

The daemon checks the current hold generation and reads the PR from the project's origin repository.
It requires an open, non-draft, same-repository PR targeting main on the publication branch at the
supplied head. The last recorded task PR must match when present; adopted PR identity also remains
binding. Explicit canonical PR URLs in the approval context may name only this PR. GitHub's `updatedAt`
must be strictly before the presentation, and the presentation after the hold. Missing/corrupt evidence,
a revised approval question, an operator message after the cited latest message, a renewed hold or any
later PR update refuses release. Even a later PR comment or description edit invalidates the evidence;
a reaffirmation does not refresh an older presentation or hold generation.

Success returns a receipt with original approval and latest operator message ids/times, question/revision
and option when present, presentation id, prior hold/event, PR URL/head, interpretation reason and actual
actor `l3`. The receipt is stored as `merge_approval` on the task and in a
`release-merge` event. Inspect `alt task show <slug>` and `alt task events <slug> --json` to confirm
the release before resuming a blocked owner. The operation neither resumes nor merges; the owner
rechecks and runs `alt land --merge`. With no active hold a repeated call refuses without another
release. A failure to apply already recorded approval follows the fault-recovery path; L3 repairs the
machinery without requesting repeated operator approval or manual hold repair.
Ordinary `--off` remains operator-only, and `--approval` cannot be combined with it or
`--why`. Standalone CLI execution of approval mode is refused; L3 uses its existing daemon transport.
