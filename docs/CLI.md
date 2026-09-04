# Altitude CLI

`bin/alt` is the one engine-neutral door into Altitude. `--project <name>` or
`ALTITUDE_PROJECT=<name>` selects a project. Commands that change task state are validated against the
task record and current attempt under the project lock.

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

## Task lifecycle

```text
alt task new --title <title> [--paths a.py,b/] [--hold-merge <reason>] -
alt task message <slug> <text>
alt task reply <text>
alt task block <slug> --reason <reason> [--for-burak | --fault]
alt task escalate <slug> --question <question>
alt task resume|stop <slug>
alt task hold-merge <slug> (--why <reason> | --off)
alt task done <slug> --digest <text>
alt task reject <slug> --reason <reason>
```

Repository changes use `alt land --message <message> [--merge]`. Project, incident, service, TLS,
and installation commands remain available through `bin/alt --help` and the relevant subcommand
help.
