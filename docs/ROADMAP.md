# Roadmap

## Current runtime stabilization

The earlier stabilization cutover and its separately authorized controlled restart are complete.
This statement does not approve the later comprehensive simplification proposal or its local
implementation branches. Service lifecycle remains separate from ordinary source work and requires
explicit authorization.

## Comprehensive simplification review

The comprehensive simplification is being redone module by module from `main` under the decisions
and phase order in [DECISIONS.md](simplification-review/DECISIONS.md). The five draft PRs from the
earlier attempt are closed. The [review checkpoint](simplification-review/README.md) records the
facts the decisions were based on.

## Current product work

The durable backlog is GitHub issues selected by Burak. The current priorities are:

- reassess the preserved wireframe draft against the simplified task and communication model;
- expose a clear project overview of active work and items that need Burak;
- make direct task conversation with the owning L2 simple and readable;
- keep incident evidence and operational recovery visible without turning them into recursive
  workflows;
- validate the guarded landing path and recovery behavior in normal use before adding more
  automation.

New architecture or feature proposals start as conversation or a GitHub issue. L3 decides whether
to answer directly or delegate one L2; no backlog item is pulled or implemented autonomously.
