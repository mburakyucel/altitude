# Working in Altitude

Read `README.md` and `docs/ARCHITECTURE.md` before changing behavior. They are the active system
description; Git history is the archive.

`docs/SIMPLIFICATION.md` records Burak's eight paradigm decisions (2026-09-02 through 2026-09-04) and the working rules
for every PR: deletion first, every added check names the incident it prevents, the seven review
questions answered in the PR body, full suites. Ask Burak only for a decision not recorded there.

This file is the project layer: Altitude's own rules, owned by the operator of this repository. The
personas in `personas/` are the global layer — how anyone works under Altitude on any project — and
carry nothing project-specific. Rules like the seams rule, the review questions, and deletion first
belong here and in `docs/SIMPLIFICATION.md`, never in a persona.

## Seams

Altitude is built for one operator on one machine, and everything that encodes that operator, their
providers, or their hardware sits behind a named seam (decision 8). The operator seam is one
configured name and role: personas, docs, and UI text say "the operator" or read the configured name.
The engine seam is `altitude/engines.py`, `altitude/route.py`, and `altitude/config.py`: no other
file spells a provider name or assumes a given engine exists, Altitude runs with any single engine
alone, and adding or removing an engine touches only the seam. The capability seam is local services
(the speech socket, `ffmpeg`, a GPU): optional, detected, and degrading to an explicit unavailable
state, as voice input does. New code obeys the rule; existing code migrates only when a PR already
touches it, never as its own project. `tests/test_project_layers.py` ratchets the counts so mentions
outside a seam can only fall.

## Roles

- L3 is Burak's project-level point of contact. It uses judgment to answer, coordinate, or create
  one task owned by one L2. Its process is read-only on the deployment checkout on both engines;
  it changes operational state only through the documented `alt` verbs, while source changes belong
  to an L2 worktree and PR.
- L2 owns a task end-to-end. Its block goes to L3 first; it flags a block for Burak only when the
  brief says so or the call is plainly his. Burak can still steer it directly in the task
  conversation. It may implement directly or delegate bounded slices to its engine's own subagents;
  Altitude does not track them, and delegation never transfers ownership.

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

## Checks

Every PR runs `make check`: the full Python and web suites, typecheck/build and isolated
headless browser flows. Tests keep application logic, state transitions and API/storage integration
real, replacing external engine calls at the engine seam with deterministic fixtures. They must
not launch real providers, operate user services or use the operator's runtime state. Coverage
evidence names user journeys and failure modes, not just line counts. Live-provider testing is
deferred by the operator's 2026-09-08 decision, including the real tiny-task requirement; see
`docs/DEVELOPMENT.md` and SIMPLIFICATION working rule 4. New gaps stay explicit in the report.

## UI

Every design and feature iteration follows the standing project tenet: simple, elegant, polished,
visually attractive, easy to use, and intuitive. The design itself makes clear where to click and
where to go. Apply the concise [design review expectations](design/wireframes/SPEC.md#11-standing-design-tenet)
alongside the interaction-state walkthrough below.

From the wireframe-implementation phase on, a PR that implements or changes a UI component ships
with that component's interaction states specified — empty, loading, listening, error, denied, and
what appears and disappears after each action — and walked through on phone and desktop before the
work is done. Use the Playwright harness in `web/e2e/`: `make ui` runs the same specs headlessly at
390×844 (phone, touch and mobile user agent) and 1440×900 (desktop), against disposable services
with fictional records and deterministic engine fixtures.
Use `walkthrough.ts` to open the route, drive each action, assert visible text/roles that appear
and disappear, and save a named screenshot for each state; `project-menu.pw.ts` is the example.
The PR body lists the states walked at each viewport and the spec or screenshot folder that proves
each state. Artifacts are under `web/ui-artifacts/` and retained briefly in CI; see development docs for setup.
The harness uses the locked bundled Chromium under the Altitude home's shared `browsers/` directory,
with Chromium's sandbox disabled inside the worker sandbox. Incident I-20260907-041446: this
host's installed Chrome AppArmor profile denies network sockets there. Install the matching
bundle once as development docs describe; a missing browser is a failed prerequisite.
Review checks the states, not only the happy path; a state that is only described is
not walked through. Functionality-first UI was acceptable before the boards were approved and is
not now. The failure this prevents: after a voice message is sent the transcript box stays on
screen, and that box should not exist at all (issue #195).

## Faults and service state

A system fault blocks only its own task, files an incident, and leaves one message for the
project's L3, whether altd detected it or an L2 reported it with `alt task block --fault`. L3 fixes
the cause through a trusted `alt` verb or creates the one repair task; it never edits source or runs Git in the deployment checkout, and Burak is not the one to repair the machine. There is no global hold: other tasks keep running. After a restart L3 receives the list of active tasks and resumes the ones blocked by a fault the
restart fixed.

Altitude restarts itself at the next narrow quiet point after a merged change to its own code (no
dispatch or resume claim, report verification, or L3 turn in flight; running workers do not hold it).
New dispatches continue while activation is pending and wait only during the requested restart window.
Do not start, stop,
mask, unmask, or restart the service as part of ordinary work. A lifecycle action by hand requires
separate explicit authorization and post-change health verification.
