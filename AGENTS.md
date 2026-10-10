# Working in Altitude

Read `README.md` for the public introduction and `docs/ARCHITECTURE.md` for the active system
description before changing behavior. Follow the relevant lifecycle, setup and operations links
there; Git history is the archive.

`AGENTS.md` is the authoritative project rule source for both engines; `CLAUDE.md` imports it.
Current system behavior and useful rationale live in the linked documentation. Ask the operator
only for a decision not recorded in these rules, the docs, or the task conversation.

This file owns Altitude's project policy. The [L1](personas/l1.md), [L2](personas/l2.md) and
[L3](personas/l3.md) personas own global role responsibilities on any project. Project rules, including seams,
review questions and deletion first, stay here.

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
alone, and adding or removing an engine touches only the seam. The capability seam is optional
services (a configured speech service, `ffmpeg`, a GPU): detected or configured, and degrading to an
explicit unavailable state, as voice input does. New code obeys the rule; existing code migrates
only when a PR already touches it, never as its own project. `tests/test_project_layers.py`
ratchets the counts so mentions outside a seam can only fall.

Linux and macOS are both target platforms, and the platform seam is `altitude/platform.py`. Every
change ships for macOS too, with host differences behind that seam. A change is confirmed on a
platform only by recorded evidence from a run there; Linux delivery does not finish macOS
confirmation. Owners run their candidate's checks on the Mac with `alt task validate`. For what a
[macOS validation run](docs/DEVELOPMENT.md#macos-validation-runs) does not establish, owners name missing
macOS confirmation in their PR and report and send L3 a tracking row for the relevant issue: #643
for containers, #225 for native runtime, or #551 for installation. L3 maintains those rows.
Linux merge checks and holds are unchanged. By the operator's decision of 2026-10-09, the early
preview supports Linux x86_64 and Apple silicon macOS; the [roadmap](docs/ROADMAP.md#native-macos-runtime)
lists the native confirmation still open.

Both engines use one execution and authority contract. Adapt that common contract when an
integration conflicts with an engine's native operating model; do not build a second engine-specific
policy system. Engine-native customization stays in skills, hooks, and agent definitions.

## Roles

The personas define [L2 task ownership and decisions](personas/l2.md) and
[L3 coordination, feedback and recovery](personas/l3.md). L3 dispatches concrete requests without
a separate scoping or research stage; implementation choices belong to the owner.

L2 blocks for the operator's go on a short proposal before writing code only when a material product/UX,
security, spend or paradigm choice is unsettled, or the brief asks for it. Otherwise it replies with a
one-paragraph plan and proceeds; the operator can redirect at any checkpoint.
Module-level calls belong to the implementing session and are reported in the PR.
Prefer small, safely mergeable increments for large or complex issues as described in the
[delivery guidance](docs/CLI.md#incremental-issue-delivery). Splitting work does not bypass unresolved
operator decisions, this proposal checkpoint, merge holds, or the deletion-first/no-dormant-code rule.
A bounded rollout needs agreement for the concrete feature; discussion of disabled paths or limited
development/admin exposure grants no new privilege model, generic flag framework, or dormant foundations.

## Boundaries

- Follow the [L2 scope and publication boundaries](personas/l2.md#scope-and-evidence): one task,
  isolated worktree/branch and PR, explicit scope/exclusions, unrelated-work preservation and
  review of all outgoing history. L3 remains read-only on the deployment checkout and changes
  operational state only through documented `alt` verbs.
- Ship the docs with the change: update the linked system documentation wherever it describes
  changed behavior, including architecture, session lifecycle, setup and operations as applicable.
  Update `README.md` when its public introduction, workflow or getting-started guidance changes.
  Keep these updates in the same PR and in present tense.
- Major UX changes, cost-accruing infrastructure, identity, or security changes hold for operator review.
  A hold report explains the strategy and decisions made. Explicit task authorization governs the hold.
- [L2 delivery and completion](personas/l2.md#delivery-and-completion) owns landing, holds,
  reporting, continuation and full-scope issue completion; the [CLI reference](docs/CLI.md#delivery-linked-issue-completion)
  describes the commands. Deferred task scope goes to L3 for authorized tracking and rejection/archive.
- The repository is public, and so is everything pushed or published: commits, branch names, PR titles
  and bodies, issue text and comments, release notes, and screenshots, GIFs or evidence attached to
  public records. None of it contains personal or private information: other people's names or contact
  details, email addresses beyond the maintainer's chosen commit identity, hostnames, home directories
  and local paths, device identifiers, local network addresses, credentials or tokens, chat transcripts,
  private incident evidence, security-sensitive operational details, or content copied from private
  records and task folders. Examples and reproductions are fictional or redacted. Private context a
  change needs stays in the task record; the public text describes it in general terms.

## Checks

Validation is automated and reusable first: an owner repeats the relevant [validation
environment](docs/DEVELOPMENT.md#validation-environments) against its own candidate with one command
and keeps its pass/fail evidence, so the operator does not run validation by hand. Reports name
acceptance that no run covered and the concrete blocker for each gap.

Owners and helpers run tests relevant to their changes during development. This repository's
required PR `check` runs the full `make check` suite as parallel shards on GitHub-hosted runners
and passes only when every shard passes. `alt land`
requires a successful run for the current head and tested tree, with current main included in that head. A branch missing
current main needs reconciliation and fresh PR checks on the new head. Final validation and merges
are serialized across Altitude owners; CI waits are not. Missing, pending, failed or stale CI blocks delivery,
including during CI outages; there is no local
bypass. Review and UX/operator holds still apply. GitHub's strict up-to-date required-check rule
protects this repository across installations; Altitude's task holds and review protocol govern
its own merges. Other projects keep their existing gates. See
[CI and candidate identity](docs/DEVELOPMENT.md#ci-and-candidate-identity).

The required suite includes Python and web tests, typecheck/build and isolated phone/desktop
headless browser flows. Changes to dispatch, engines or landing include deterministic integration
evidence for affected task, message/resume, failure and delivery paths.
Tests keep application logic, state transitions and API/storage integration
real, replacing external engine calls at the engine seam with deterministic fixtures. They must
not launch real providers, operate user services or use the operator's runtime state. Coverage
evidence names user journeys and failure modes, not just line counts. Live-provider testing is
deferred by the operator's 2026-09-08 decision, including the real tiny-task requirement; see
`docs/DEVELOPMENT.md`. Fixtures establish application behavior, not live CLI/authentication or
provider compatibility. New gaps stay explicit in the report.
Live-provider testing requires a future operator decision; it creates no unattended suite, follow-up
validation task, or release prerequisite.

## Working rules for every PR

1. Deletion first. Remove what the change makes unnecessary in the same PR, and prefer removing a
   mechanism over adding one. No foundations, no dormant modules, no compatibility readers, no
   "temporary" code.
2. Every defensive check that is added names the incident it prevents in the PR body. A check that
   defends against Altitude's own design is removed with the design.
3. Describe finished behavior directly in concise present tense, without old-versus-new narrative.
   Keep the PR body short: behavior, material boundaries or tradeoffs, what is removed, and
   validation. Link detailed review evidence instead of pasting boilerplate.
   Review asks: is the behavior wanted; which callers, records, external effects and tests depend on it;
   can fewer owners, states, artifacts or compatibility paths express it; what is removed and what
   proves parity; which operator/provider/machine assumptions remain, behind which seam, and could
   an engine be dropped by changing only that seam? Existing tests alone do not justify behavior.
4. Caching, prefetching and data retained in the browser or on the device need an independently
   reviewed proposal before code and an independent implementation review before merge: what is kept,
   for how long, what invalidates it, and how stale data can never be acted on as current, across
   task/project isolation, reconnects and re-pairing. Evidence: [loading and caching](docs/DEVELOPMENT.md#loading-and-caching-evidence).

## UI

Every design and feature iteration follows the standing project tenet: simple, elegant, polished,
visually attractive, easy to use, and intuitive. Intuitiveness is a first-order requirement, not a
finish: the design itself makes clear where to click and where to go, and anything clickable is
immediately recognisable as clickable. Apply the concise [design review expectations](design/wireframes/SPEC.md#11-standing-design-tenet)
alongside the interaction-state walkthrough below.

Keep wireframes lean: maintain the current approved design in the spec and useful boards, folding
accepted changes into them instead of accumulating per-PR galleries. Review-only before/after
comparisons, proposal captures, implementation screenshot galleries, and routine test renderings stay
outside version control and out of the committed PR diff. Use ignored review/CI artifacts (see
[development](docs/DEVELOPMENT.md#browser-walkthroughs)), or the task design preview once delivered and
verified with ignored/untracked inputs. Keep evidence accessible until its review is complete and
link it from the PR; a missing upload does not waive review or an existing merge hold. Curated
documentation illustrations may stay when they explain current behavior and have a maintained source.

A UI component change specifies and walks its interaction states — empty, loading, listening,
error, denied, and what appears/disappears after actions — on phone and desktop. Follow the
[browser walkthroughs](docs/DEVELOPMENT.md#browser-walkthroughs) for the harness, viewports,
fictional fixtures, named screenshots and browser prerequisites; `project-menu.pw.ts` is the example.
The PR lists states walked at each viewport and links their spec or screenshot evidence.
Describing a state is not walking it. This prevents the issue #195 failure: the transcript box
remaining after sending a voice message.
Phone-facing changes also run the opt-in emulated iPhone lane (`make ui-ios`); its results are
desktop WebKit evidence, not iOS acceptance (see [device evidence](docs/DEVELOPMENT.md#device-evidence)).

Every task that changes the UI gets a design review before implementation: its proposal review,
by another engine unless none is eligible, which judges the design against this tenet and the
interaction states and offers up to two alternative directions. A design proposal saves its captures
beside a written spec precise enough to review against (states, exact text and line formats, links,
wrapping and truncation, phone and desktop), and the review reads that spec. The owner shows the
operator the review's alternatives alongside its proposal; the operator chooses.

For a major UX change, settle the user-facing decisions before finalizing implementation or
migrating tests. Present a concise proposal and a small set of reviewable wireframes; discuss
unresolved behavior with the operator at that level. Positive overall feedback does not settle
questions the operator is still clarifying. Record the agreed scope and behavior before proceeding.
Independent code review and passing checks support implementation review; they do not replace the
operator's UX decision. Check the durable task conversation before moving past a review checkpoint.

## Faults and service state

Follow [L3 recovery responsibilities](personas/l3.md#recovery-and-incident-issues): faults stay
task-local, L3 owns repair through supported verbs or one repair task, and resumes only verified
recovery. Other tasks keep running; the operator is not assigned routine machine repair.

Every incident is filed as a sanitized GitHub issue. For newly investigated incidents and incoming
issues, L3 promptly triages system and role/procedure corrections, attaches the incident to an
existing issue when the cause is shared, and assigns straightforward or important authorized fixes
to an existing matching owner or one concrete task. Record the prevention owner and next action on
the issue and in incident evidence; if deferred, name the reason and next decision. Closing an
incident closes the issue it created. This is targeted incident follow-through, not automatic backlog draining.
An issue notification grants no cross-project task authority. New material choices still
follow the proposal, security and UX checkpoints above.

Altitude restarts itself at the next narrow quiet point after a merged change to its own code (no
dispatch or resume claim, adversarial review, validation run, report verification, or L3 turn in flight;
validation holds through its bounded execution and evidence recording; running workers themselves do not hold it).
New dispatches continue while activation is pending and wait only during the requested restart window;
new validation and review runs wait from the moment activation is pending.
Do not start, stop, mask, unmask, or restart the service as part of ordinary work. A lifecycle action by hand requires
separate explicit authorization and post-change health verification; a recorded operator grant whose purpose
names the service is that authorization for its owner, and every command under it is recorded on the task.

### Container operations on this machine

The operator's project-chat approval of 2026-10-06 18:24 UTC (`c6f1ba6f5929`) stands until the
operator revokes it: L3 records a container-scoped operator grant on an owner's request without
another operator question. Its scope is the Linux container deployment on this machine:
`scripts/container.py` operations, rootless Podman, the volumes, images and containers the launcher
creates, and anything executed inside those containers. It excludes the operator's native Altitude
installation and service, host trust stores and network configuration, host credential directories,
and every other host resource the launcher did not create. It grants nothing for other projects.
