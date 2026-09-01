# Altitude

Altitude turns a project-level conversation into isolated, reviewable work without making Burak
manage agent plumbing. L3 is the project coordinator. Each active task has one directly reachable
L2 owner, and that L2 may work alone or use optional L1 implementers and reviewers.

Start with [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the current system and
[`docs/ROADMAP.md`](docs/ROADMAP.md) for the remaining product work. The preserved UI draft is in
[`design/wireframes/README.md`](design/wireframes/README.md).

## Current operating model

- A request becomes a queued task owned end-to-end by one L2.
- Burak discusses roadmap and project direction with L3, and task-specific choices directly with
  the task's L2.
- Every code change uses an isolated worktree and branch, then a PR. The L2 may merge after the
  applicable checks and review unless an explicit merge hold says otherwise.
- Deferred work is recorded in a GitHub issue and removed from the active task set. Completed and
  rejected tasks are archived immediately.
- A system fault records private evidence and activates the recovery fuse. L3 coordinates
  operational recovery and may delegate one explicit recovery L2; incidents never create work or
  sessions recursively.

## Repository and runtime

`altitude/` is a standard-library Python package. `bin/alt` is the CLI, `personas/` contains the
three execution roles plus the optional reviewer, `schemas/` defines L2/reviewer reports,
`hooks/` enforces repository boundaries and records counters, and `web/` is the React UI built
into `web/dist/` for the Python server to serve.

Runtime state lives under `ALTITUDE_HOME` (default `~/.altitude`): project configuration, active
tasks, archived tasks, L3 and L2 conversations, monitor snapshots, recovery state, and private
incident evidence. Runtime state is not source-controlled.

Useful commands:

```sh
make test
make web
bin/alt --project <name> state
bin/alt --project <name> task status <slug>
```

## Service lifecycle

The controlled architecture-cutover restart was completed and smoke-verified on 2026-08-31.
Ordinary development and code agents must not start, stop, mask, unmask, or restart the service.
Any later lifecycle change requires separate explicit authorization and post-change health checks.
