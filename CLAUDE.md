# Working in Altitude

Read `README.md` and `docs/ARCHITECTURE.md` before changing behavior. They are the active system
description; Git history is the archive.

`docs/SIMPLIFICATION.md` records Burak's six paradigm decisions (2026-09-02) and the working rules
for every PR: deletion first, every added check names the incident it prevents, the six review
questions answered in the PR body, full suites. Ask Burak only for a decision not recorded there.

## Roles

- L3 is Burak's project-level point of contact. It uses judgment to answer, coordinate, or create
  one task owned by one L2.
- L2 owns a task end-to-end and talks directly with Burak about task-specific questions. It may
  implement directly or delegate bounded slices to its engine's own subagents; Altitude does not
  track them, and delegation never transfers ownership.

## Boundaries

- Keep one active task in one isolated worktree and branch. Never develop in the deployment
  checkout, reuse another task's branch, or bypass the PR path.
- Preserve unrelated and in-progress work. Stay inside the request and declared lease.
- Ship the docs with the change: a PR that changes behavior updates `README.md`,
  `docs/ARCHITECTURE.md`, and `docs/SESSION_LIFECYCLE.md` wherever they describe that behavior, in
  the same PR and in present tense. Include those files in the lease.
- Use `alt task status` for orientation and `alt land` for the guarded commit, push, PR, checks,
  and optional merge path.
- Do not turn an incident or review finding into another task or session. Record evidence;
  L3 and Burak decide any later work.
- If work should be deferred, create or update a GitHub issue and reject/archive the active task.
- Never publish credentials, tokens, private incident evidence, or security-sensitive operational
  details.

## Faults and service state

A system fault blocks only its own task, files an incident, and leaves one message for the
project's L3, whether altd detected it or an L2 reported it with `alt task block --fault`. L3 fixes
the cause or creates the one repair task; Burak is not the one to repair the machine. There is no global hold: other tasks keep running. Resume a task blocked by a fault
once its cause is fixed.

Do not start, stop, mask, unmask, or restart the service as part of ordinary work. A lifecycle
action requires separate explicit authorization and post-change health verification.
