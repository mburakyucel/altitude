# Roadmap

## Simplification (complete)

The module-by-module simplification finished on 2026-09-03; [SIMPLIFICATION.md](SIMPLIFICATION.md)
holds the decisions, the working rules that still apply, and the deletion ledger. Service lifecycle
stays separate from ordinary source work and requires explicit authorization.

## Current product work

The product redesign, approved on 2026-09-05, replaced the 2026-09-03 wireframes under
`design/wireframes/`; its `SPEC.md` governs the UI and lists the implementation slices, each one
task. The durable backlog is GitHub issues selected by Burak. The current priorities are:

- expose a clear project overview of active work and items that need Burak;
- make direct task conversation with the owning L2 simple and readable;
- keep incident evidence and operational recovery visible without turning them into recursive
  workflows;
- validate the guarded landing path and recovery behavior in normal use before adding more
  automation.

New architecture or feature proposals start as conversation or a GitHub issue. L3 decides whether
to answer directly or delegate one L2; no backlog item is pulled or implemented autonomously.
