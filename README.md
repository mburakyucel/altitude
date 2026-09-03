# Altitude

Altitude turns a project-level conversation into isolated, reviewable work without making Burak
manage agent plumbing. L3 is the project coordinator. Each active task has one directly reachable
L2 owner, and that L2 may work alone or delegate bounded slices to its engine's own subagents.

Start with [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the current system and
[`docs/ROADMAP.md`](docs/ROADMAP.md) for the remaining product work. The preserved UI draft is in
[`design/wireframes/README.md`](design/wireframes/README.md).

The comprehensive simplification is being redone module by module from `main`. Burak's paradigm
decisions, the working rules, and the phase order are in
[`docs/simplification-review/DECISIONS.md`](docs/simplification-review/DECISIONS.md); the
[review checkpoint](docs/simplification-review/README.md) records the facts they were based on. The
five `simplify/*` draft PRs are closed and no `simplify/*` branch is a source for new work.

## Current operating model

- L3 answers directly or creates one queued task, owned end-to-end by one L2, when concrete execution is warranted.
- Burak discusses roadmap and project direction with L3, and task-specific choices directly with
  the task's L2.
- Every code change uses an isolated worktree and branch, then a PR. The L2 may merge after the
  applicable checks and review unless an explicit merge hold says otherwise.
- Fresh L2 work and each L3 turn choose Claude Code or Codex weekly-first, record the reason, and
  preserve separate provider sessions; one provider's short-window limit does not freeze the other.
- Deferred work is recorded in a GitHub issue and removed from the active task set. Completed and
  rejected tasks are archived immediately.
- A system fault blocks only its own task, records private incident evidence, and leaves one
  message for the project's L3, which records the learning and fixes the cause directly or creates
  one ordinary task. An incident raised by that repair task goes to the Inbox instead of waking L3
  again.

## Repository and runtime

`altitude/` is a standard-library Python package. `bin/alt` is the CLI, `personas/` contains the
L2 and L3 roles for each engine, and `schemas/` defines broker actions and code-delivery reports.
`hooks/` supplies Claude-side message delivery and telemetry. Codex uses explicit permission profiles and whole-process-tree containment, then returns
an inert action for trusted backend validation. `web/` is the React UI built into `web/dist/` for
the Python server to serve.

Runtime state lives under `ALTITUDE_HOME` (default `~/.altitude`): project configuration, active
tasks, archived tasks, L3 and L2 conversations, monitor snapshots, and private
incident evidence. Runtime state is not source-controlled.

Useful commands:

```sh
make test
make web
make restart
bin/alt --project <name> state
bin/alt --project <name> task status <slug>
```

## Service lifecycle

The controlled architecture-cutover restart was completed and smoke-verified on 2026-08-31.
Ordinary development and code agents must not start, stop, mask, unmask, or restart the service.
Any later lifecycle change requires separate explicit authorization and post-change health checks.

For an operator-authorized restart, press Restart on the web app's restart-pending banner (shown
once nothing is running) or run `make restart` from the deployed primary checkout. The
command refuses another clone/worktree, a non-exact or dirty `main`, and active L2 or report work.
It installs the locked web dependencies, builds and validates a staged bundle, swaps it into the
ignored runtime `web/dist`, restarts the user-level `altitude.service`, and waits for both its API and
web page to answer from a new process. The prior bundle is restored if verification fails. There is
no separate web service and no `sudo` is required. Node 22+ and `pnpm` are required; dependency
retrieval may be needed when the local pnpm store is cold. Refresh the browser after it succeeds.
