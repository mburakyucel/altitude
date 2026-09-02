# Working in Altitude

Read `README.md` and `docs/ARCHITECTURE.md` before changing behavior. They are the active system
description; Git history is the archive.

The comprehensive simplification is paused for a module-by-module review. Before using any
`simplify/*` branch or changing architecture, read
`docs/simplification-review/README.md`. No accumulated candidate or WIP branch is an approved
destination architecture, and no module may be adopted merely because it was previously described
as reviewed.

No next simplification module has been selected. Ask Burak which module to review next. Do not
implement or merge it until Burak's decision and the exact scope are recorded in the checkpoint.

## Roles

- L3 is Burak's project-level point of contact. It uses judgment to answer, coordinate, or create
  one task owned by one L2.
- L2 owns a task end-to-end and talks directly with Burak about task-specific questions. It may
  implement directly or launch zero, one, or several bounded L1s or an optional reviewer.
- L1 implements or investigates only its bounded brief. The reviewer independently evaluates a
  bounded result. Neither owns the parent task.

## Boundaries

- Keep one active task in one isolated worktree and branch. Never develop in the deployment
  checkout, reuse another task's branch, or bypass the PR path.
- Preserve unrelated and in-progress work. Stay inside the request and declared lease.
- Use `alt task status` for orientation and `alt land` for the guarded commit, push, PR, checks,
  and optional merge path.
- Do not turn an incident or review finding into another task or session. Record evidence;
  L3 and Burak decide any later work.
- If work should be deferred, create or update a GitHub issue and reject/archive the active task.
- Never publish credentials, tokens, private incident evidence, or security-sensitive operational
  details.

## Recovery and service state

A recovery hold blocks ordinary fresh and resumed launches. Only one explicitly claimed recovery
task can run while it is active. Clearing the hold requires a recorded reason after the system is
stable.

The controlled architecture-cutover restart is complete. Do not start, stop, mask, unmask, or
restart the service as part of ordinary work. Any lifecycle action requires separate explicit
authorization and post-change health verification.
