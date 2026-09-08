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

## GitHub issues

```text
alt issue new --title <title> [--label <label>] -
alt issue comment <number> -
alt issue close <number> --reason completed|not-planned
```

L3 and the operator use these verbs through altd; L2 cannot mutate issues. L3 files requested backlog
and closes an issue only when the operator asks for that closure, never as autonomous backlog cleanup.
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

### Upstream Altitude defects

```sh
alt issue upstream --title "Fictional resume defect" - <<'JSON'
{
  "expected": "The fictional Atlas task resumes once.",
  "actual": "The task remains blocked.",
  "reproduction": "Create a toy project, block its task, then request resume.",
  "version": "example-build-123"
}
JSON
```

L3 can explicitly report an Altitude defect from any managed project. The command creates only a
GitHub issue in the installation's Altitude issue repository; it does not fix Altitude, create a
recovery task, wake Altitude's L3, or copy local incidents or conversations. Altitude's
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
After a timeout or unconfirmed response, the operator checks the target's issues before retrying to
avoid duplicates; L3's GitHub read broker remains project-local.

## Dirty-checkout recovery

`alt task preserve-checkout <slug> --reason "…"` asks altd to preserve the selected project's dirty
main checkout for an existing blocked task that has never launched. It is available to the
operator and project-bound L3, and denied to L2. It needs neither a worker nor a free task slot.
The CLI queues a durable request; `alt task status <slug>` shows `daemon_request.status`, its
outcome note, and `checkout_archive: {"branch": "archive/checkout-<request-id>", "sha": "<SHA>"}`
once the snapshot is saved. `alt task events <slug> --json` includes the request's actor/reason and
the `checkout-preserved` branch and immutable SHA. Branches are local, uniquely named and never
overwritten, automatically pushed or deleted. Retention ends only with explicit operator removal.

For a fictional `example` project with dirty main exactly at `origin/main`:

1. Stop editing that checkout during preservation. Inspect `alt --project example repo` and the
   intended task with `alt --project example task status reconcile-edits`. Use an existing
   unlaunched blocked task; `--source recovery` controls fault notifications, not Git privileges.
2. Run `alt --project example task preserve-checkout reconcile-edits --reason "Preserve existing edits for review in the task PR"`.
3. Wait for `alt --project example task status reconcile-edits` to show the completed request and
   its `checkout_archive` branch and SHA. A successful preservation leaves clean main at `origin/main`
   and keeps the task blocked. Snapshot commits live only on the local archive branch.
4. Give the owner the branch, SHA and reconciliation scope with
   `alt --project example task message reconcile-edits "Inspect archive <branch> at <SHA>; apply the reviewed snapshot in your task worktree using the CLI recovery procedure and deliver through your PR."`
   This message requests resume. Ensure the task's lease covers the intended changes using
   `alt task paths` if needed. The owner inspects the snapshot before staging and publishing it.
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
alt task block <slug> --reason <reason> [--for-burak | --fault]  # current L2 only
alt task escalate <slug> --question <question>
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
worker in the caller. L3 cannot call `task block` directly: an L2 blocks itself with its attempt fence,
while L3 uses reason-bearing `task stop` so altd blocks the task and stops the same observed worker.

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

The explicit command records one immutable `adopted_pr` receipt and `pr-adopted` event before
publication, visible through `alt task status` and `alt task events`. `--dry-run` fetches and
validates the PR but records nothing and stages/pushes nothing. After adoption, ordinary
`alt land --message "…" [--merge]` reuses that PR and its original branch. Supply `--paths` again
if it overrides the task's declared lease. Retrying adoption with the same original PR/head is
idempotent; selecting another head or PR is refused. Later unowned commits cannot be adopted by
repeating the command. If origin moves, inspect and incorporate only changes belonging to this
task; unrelated history requires a separate ownership decision, not a broader adoption receipt.

Complete the repository's applicable review before `--merge`. Drafts, requested changes and
outstanding required reviews block adopted merges. Configured checks must pass; absent or skipped
configured checks are not green. Where no CI is configured, use `--test-cmd "<full suite>"` if the
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
alt task hold-merge <slug> --approval <message-id> --pr-number <number> --head <full-sha> --reason <reason>
```

Read `alt task messages <slug> --json` for the durable message id and `alt pr <number> --json`
for the current PR head. The latest operator message must be exactly `Good to merge`, directly
after the owner's message containing the canonical GitHub PR URL and no other PR URL. The daemon
checks the current hold generation and reads the PR from the project's origin repository. It
requires an open, non-draft, same-repository PR targeting main on the task branch at the supplied
head, with GitHub's `updatedAt` strictly before the presentation and the presentation after the hold.
Missing or corrupt evidence, a later operator message, a renewed hold, or a later PR update refuses
release. This is deliberately conservative: even a later PR comment or description edit invalidates
this evidence path. Arbitrary approval wording is not interpreted.

Success returns a receipt with the approval message id/time, presentation id, prior hold/event,
PR URL/head and actual actor `l3`. The receipt is stored as `merge_approval` on the task and in a
`release-merge` event. Inspect `alt task show <slug>` and `alt task events <slug> --json` to confirm
the release before resuming a blocked owner. The operation neither resumes nor merges; the owner
rechecks and runs `alt land --merge`. With no active hold a repeated call refuses without another
release. Ordinary `--off` remains operator-only, and `--approval` cannot be combined with it or
`--why`. Standalone CLI execution of approval mode is refused; L3 uses its existing daemon transport.
