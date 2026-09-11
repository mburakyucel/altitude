# Working in Altitude

Read `README.md` and `docs/ARCHITECTURE.md` before changing behavior. They are the active system
description; Git history is the archive.

`AGENTS.md` is the authoritative project rule source for both engines; `CLAUDE.md` imports it.
Current system behavior and useful rationale live in the linked documentation. Ask the operator
only for a decision not recorded in these rules, the docs, or the task conversation.

This file is the project layer: Altitude's own rules, owned by the operator of this repository. The
personas in `personas/` are the global layer — how anyone works under Altitude on any project — and
carry nothing project-specific. Rules like the seams rule, the review questions, and deletion first
belong here, never in a persona.

## Standing tenet

Simplicity, clarity and elegance guide everything that happens in Altitude. Use judgment to favor
approaches that are easy to understand and maintain. Present decisions and explanations concisely,
making the choice, recommendation and material consequences clear. When friction recurs, simplify
the underlying approach instead of accumulating exceptions, redundant checks or unnecessary procedure.

## Seams

Altitude is built for one operator on one machine, and everything that encodes that operator, their
providers, or their hardware sits behind a named seam. The operator seam is one
configured name and role: personas, docs, and UI text say "the operator" or read the configured name.
The engine seam is `altitude/engines.py`, `altitude/route.py`, and `altitude/config.py`: no other
file spells a provider name or assumes a given engine exists, Altitude runs with any single engine
alone, and adding or removing an engine touches only the seam. The capability seam is local services
(the speech socket, `ffmpeg`, a GPU): optional, detected, and degrading to an explicit unavailable
state, as voice input does. New code obeys the rule; existing code migrates only when a PR already
touches it, never as its own project. `tests/test_project_layers.py` ratchets the counts so mentions
outside a seam can only fall.

Both engines use one execution and authority contract. Adapt that common contract when an
integration conflicts with an engine's native operating model; do not build a second engine-specific
policy system. Engine-native customization stays in skills, hooks, and agent definitions.

## Roles

- L3 is Burak's project-level point of contact. It uses judgment to answer, coordinate, or create
  one task owned by one L2. Its process is read-only on the deployment checkout on both engines;
  it changes operational state only through the documented `alt` verbs, while source changes belong
  to an L2 worktree and PR.
- L2 owns a task end-to-end. Its block goes to L3 first; it flags a block for Burak only when the
  brief says so or the call is plainly his. Burak can still steer it directly in the task
  conversation. It may implement directly or delegate bounded slices to its engine's own subagents;
  Altitude does not track them, and delegation never transfers ownership.

For large or ambiguous implementation, L2's first output is a short proposal and it blocks for the
operator's go before writing code. L3 dispatches concrete requests without a separate scoping or research stage.
Module-level calls belong to the implementing session and are reported in the PR.
Prefer small, safely mergeable increments for large or complex issues as described in the
[delivery guidance](docs/CLI.md#incremental-issue-delivery). Splitting work does not bypass unresolved
operator decisions, this proposal checkpoint, merge holds, or the deletion-first/no-dormant-code rule.
A bounded rollout needs agreement for the concrete feature; discussion of disabled paths or limited
development/admin exposure grants no new privilege model, generic flag framework, or dormant foundations.

## Boundaries

- Keep one active task in one isolated worktree and branch. Never develop in the deployment
  checkout, reuse another task's branch, or bypass the PR path.
- Preserve unrelated and in-progress work. Stay inside the request and declared lease.
- Ship the docs with the change: a PR that changes behavior updates `README.md`,
  `docs/ARCHITECTURE.md`, and `docs/SESSION_LIFECYCLE.md` wherever they describe that behavior, in
  the same PR and in present tense. Include those files in the lease.
- Merge when applicable checks and appropriate review pass, unless a recorded hold applies. Major
  UX changes, cost-accruing infrastructure, identity, or security changes hold for operator review.
  A hold report explains the strategy and decisions made. Explicit task authorization governs the hold.
- Authorized implementation whose cumulative delivered evidence fully satisfies an identified project
  issue, including required operator acceptance, closes it through the
  reviewed PR's native closing relationship and `alt land --closes-issue N`; verify closure after merge.
  Partial work, design-only progress, pending operator acceptance, and unrelated mentions leave issues
  open. Missing links on already merged complete deliveries go to L3 for evidence-based reconciliation;
  L2 has no direct issue-write authority. See [delivery rules](docs/CLI.md#delivery-linked-issue-completion).
- Use `alt task status` for orientation and `alt land` for the guarded commit, push, PR, checks,
  and optional merge path.
- Do not turn an incident or review finding into another task or session. Record evidence;
  L3 and Burak decide any later work.
- If agreed task scope must be deferred, route it to L3 for authorized issue tracking and task rejection/archive.
  Completing an agreed increment completes that task; outstanding parent-issue scope stays recorded and open.
- Never publish credentials, tokens, private incident evidence, or security-sensitive operational
  details.

## Checks

The operator's 2026-09-09 Pacific decision temporarily suspends hosted CI for this repository.
`alt land` runs `make check` on the exact current merge candidate and records its base, head,
candidate SHA, tree and local evidence. A passing run adds one truthful test line to the PR;
failed tests or changed tips block merge. Historical hosted failures do not gate this project's
delivery. Existing review and UX/operator holds still apply. Other projects keep their existing
gates. See [local delivery and CI restoration](docs/DEVELOPMENT.md#ci-and-candidate-identity).

Every PR runs `make check`: the full Python and web suites, typecheck/build and isolated
headless browser flows. Tests keep application logic, state transitions and API/storage integration
real, replacing external engine calls at the engine seam with deterministic fixtures. They must
not launch real providers, operate user services or use the operator's runtime state. Coverage
evidence names user journeys and failure modes, not just line counts. Live-provider testing is
deferred by the operator's 2026-09-08 decision, including the real tiny-task requirement; see
`docs/DEVELOPMENT.md` and the working rules below. New gaps stay explicit in the report.
Live-provider testing requires a future operator decision; it creates no unattended suite, follow-up
validation task, or release prerequisite.

## Working rules for every PR

1. Deletion first. A PR reduces production lines, or is a bug fix under fifty lines. No foundations,
   no dormant modules, no compatibility readers, no "temporary" code. A decision-mandated feature is
   the stated exception.
2. Every defensive check that is added names the incident it prevents in the PR body. A check that
   defends against Altitude's own design is removed with the design.
3. The PR body answers, in at most fifteen lines: what user-visible or safety behaviour the module
   provides; which callers, records, external effects, and tests depend on it; whether the behaviour
   is still wanted; whether it can be expressed with fewer owners, states, artifacts, or compatibility
   paths; what is removed; what test or end-to-end observation proves parity; and what in the change
   works only for this operator, this subscription mix, or this machine, and which seam holds it —
   could an engine be dropped tomorrow by touching only the seam? "Simpler" is not evidence on its
   own, and an existing test is not evidence that a mechanism is still wanted.
4. Full Python and web suites, typecheck/build and isolated phone/desktop browser checks on every
   PR (`make check`). Changes to dispatch, engines or landing include deterministic integration
   evidence for affected task, message/resume, failure and delivery paths. The operator's
   2026-09-08 decision defers live-provider validation and the real tiny-task requirement; fakes
   establish application behavior, not live CLI/authentication or provider compatibility.

## UI

Every design and feature iteration follows the standing project tenet: simple, elegant, polished,
visually attractive, easy to use, and intuitive. The design itself makes clear where to click and
where to go. Apply the concise [design review expectations](design/wireframes/SPEC.md#11-standing-design-tenet)
alongside the interaction-state walkthrough below.

Keep wireframes lean: maintain the current approved design in the spec and useful boards, folding
accepted changes into them instead of accumulating per-PR galleries. Review-only before/after
comparisons, proposal captures, implementation screenshot galleries, and routine test renderings stay
outside version control and out of the committed PR diff. Use ignored review/CI artifacts (see
[development](docs/DEVELOPMENT.md#browser-walkthroughs)), or the task design preview once delivered and
verified with ignored/untracked inputs. Keep evidence accessible until its review is complete and
link it from the PR; a missing upload does not waive review or an existing merge hold. Curated
documentation illustrations may stay when they explain current behavior and have a maintained source.

From the wireframe-implementation phase on, a PR that implements or changes a UI component ships
with that component's interaction states specified — empty, loading, listening, error, denied, and
what appears and disappears after each action — and walked through on phone and desktop before the
work is done. Use the Playwright harness in `web/e2e/`: `make ui` runs the same specs headlessly at
390×844 (phone, touch and mobile user agent) and 1440×900 (desktop), against disposable services
with fictional records and deterministic engine fixtures.
Use `walkthrough.ts` to open the route, drive each action, assert visible text/roles that appear
and disappear, and save a named screenshot for each state; `project-menu.pw.ts` is the example.
The PR body lists the states walked at each viewport and the spec or screenshot folder that proves
each state. Artifacts are under `web/ui-artifacts/`; local landing retains candidate evidence in the
task folder. See development docs for setup.
The harness uses the locked bundled Chromium under the Altitude home's shared `browsers/` directory,
with Chromium's sandbox disabled inside the worker sandbox. Incident I-20260907-041446: this
host's installed Chrome AppArmor profile denies network sockets there. Install the matching
bundle once as development docs describe; a missing browser is a failed prerequisite.
Review checks the states, not only the happy path; a state that is only described is
not walked through. Functionality-first UI was acceptable before the boards were approved and is
not now. The failure this prevents: after a voice message is sent the transcript box stays on
screen, and that box should not exist at all (issue #195).

For a major UX change, settle the user-facing decisions before finalizing implementation or
migrating tests. Present a concise proposal and a small set of reviewable wireframes; discuss
unresolved behavior with the operator at that level. Positive overall feedback does not settle
questions the operator is still clarifying. Record the agreed scope and behavior before proceeding.
Independent code review and passing checks support implementation review; they do not replace the
operator's UX decision. Check the durable task conversation before moving past a review checkpoint.

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
