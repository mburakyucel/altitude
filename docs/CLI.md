# Altitude CLI

`bin/alt` is the one engine-neutral door into Altitude. `--project <name>` or
`ALTITUDE_PROJECT=<name>` selects a project. Commands that change task state are validated against the
task record and current attempt under the project lock.

Start with [setup](SETUP.md) to build the app, install project Git guards, register a project
with explicit engine pins, and start a conversation. `alt project add` registers state; it does
not install Git guards. [Operations](OPERATIONS.md) covers project settings and service lifecycle.

## Inspection

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
and private incident evidence. Close requires `--reason completed` for finished work or
`--reason not-planned` for work the operator decides not to pursue. Altd maps the latter to GitHub's
`not planned` reason. Close accepts no stdin body, title, labels, or comment option; a separately
requested public explanation uses `alt issue comment` and its existing evidence check. No other issue
mutations or direct `gh` writes are enabled.

The coordinator MCP tool takes, for example,
`{"kind":"alt","args":["issue","close","42","--reason","completed"]}`.
The operator CLI uses `POST /api/issue` with
`{"project":"<name>","operation":"close","number":42,"reason":"completed"}`;
`body` may be omitted or empty. Both paths apply the same operation validation and return the issue
URL. The socket fixes actor `l3`; HTTP fixes actor `operator`. A successful closure appends an
`issue-close` project event with actor, number, reason, title, and URL; failed calls record no success.

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
