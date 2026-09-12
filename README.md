# Altitude

**Persistent project orchestration for coding agents.**

Altitude gives each software project an ongoing conversation with **L3, its project
orchestrator**. Discuss architecture, priorities and what should happen next. L3 dispatches
**L2 task owners**, each responsible for concrete work from its brief through checks and a PR.
You can talk directly to any owner while the work runs.

Several tasks can move forward in isolated worktrees while L3 keeps the project direction in
view. Questions go to L3 first: it answers from the brief, repository docs and recorded decisions,
and brings you the calls that need your judgment. Task reports preserve delivery outcomes;
reports needing follow-up return to the project conversation, where you decide what comes next.

**Engine-agnostic by design.** Codex and Claude Code are supported today; further CLI engines are
part of the [roadmap](docs/ROADMAP.md#engines-platforms-and-distribution). Each integration connects
the engine's native sessions and tools to the same project and task workflow.

<picture>
  <source media="(max-width: 600px)" srcset="docs/images/orchestration-phone.svg">
  <img src="docs/images/orchestration.svg" alt="You discuss direction with L3 and can steer each L2 directly. L3 dispatches task owners in separate worktrees. Owners return questions and results to L3; only decisions needing you are escalated. Each code task delivers through checks and a PR." width="960">
</picture>

## One project, several fronts of work

You are moving **Atlas**, a search service, to a versioned index. Existing clients must keep
working, backfills must resume safely, and search latency must stay within budget.

In the project conversation, agree the API contract and rollout constraints with L3. Then ask it
to dispatch the independent work: **client compatibility**, **resumable backfill**, and a
**performance baseline**. Each gets its own L2, brief, expected files and worktree. The performance
owner can measure the current system while the other two implement against the agreed contract.
L3 uses the results to coordinate the next rollout task.

<picture>
  <source media="(max-width: 600px)" srcset="docs/images/project-phone.png">
  <img src="docs/images/project-desktop.png" alt="Atlas project conversation: the operator sets index-migration constraints, L3 describes three independent tasks, and the desktop work panel shows their active work." width="1440">
</picture>

*The actual web app, rendered with fictional projects, messages and session data. This is an
illustrative engineering scenario, not a recorded delivery. [Open the captures and walkthrough](docs/WALKTHROUGH.md).*

[Full-size desktop](docs/images/project-desktop.png) · [Phone](docs/images/project-phone.png)

Open the compatibility task and tell its L2, “Keep pagination tokens valid across the cutover.”
Its durable conversation sits beside the live engine session. Meanwhile, the backfill owner's
retry question goes to L3, which answers from the agreed idempotency rule. A different question
— whether to retain the old index for seven or thirty days — needs your cost and rollback
judgment, so L3 escalates it to **Needs you**. The other work can continue.

The owners deliver separate, checked PRs. A merge hold leaves a PR for your review; otherwise an
owner can merge after the applicable checks and review. L3 can inspect the reports and handle
follow-up, so the next discussion can address rollout readiness with the work in view.

This repository temporarily uses verified local `make check` runs for PR delivery under the
operator's 2026-09-09 Pacific decision. Landing tests the current merge candidate, retains its
evidence, including captured output on timeout, and adds the passing candidate SHA to the PR.
Timed-out validation keeps the gate failed. Hosted CI is suspended; review and merge
holds still apply. Other projects keep their own gates. See
[local validation and CI restoration](docs/DEVELOPMENT.md#ci-and-candidate-identity).

One active task can deliver several PRs. When authorized work remains after a merge, the same owner
continues in its existing conversation and worktree and runs `alt land` again. It puts only the
follow-up changes onto current main and opens another PR, with its own checks and review holds.
Running it without new work creates nothing. Earlier deliveries remain recorded, and the final
report covers all of them. See [continuation after merge](docs/CLI.md#continue-after-a-pr-merges).

A reported owner with an open PR remains reachable in its task conversation. Send a follow-up to
continue that owner's session, attempt and worktree with the same PR, objective and review holds.
L3 can also request continuation with `alt task resume <slug> --reason "…"`. The previous report
stays in task history; resumed work needs a fresh report. A saved message is not proof that the
worker has restarted: capacity and recovery waits remain visible. Done, archived and rejected tasks
remain read-only; reopening their lifecycle is a separate, unsettled product decision.
An exited worker whose transient unit has been collected can resume once systemd confirms it is
inactive; unavailable or ambiguous status keeps the task blocked to prevent overlapping workers.

Large or complex issues can move through small, reviewable increments that keep supported user journeys
working. L3 records the breakdown and delivery evidence in the issue; each task completes its agreed
increment, and follow-up work can come later within the operator's authorization. The parent stays open
until cumulative delivery satisfies its full scope and required acceptance. See
[incremental delivery](docs/CLI.md#incremental-issue-delivery) and
[issue closure and reconciliation](docs/CLI.md#delivery-linked-issue-completion); merge holds still apply.

An assigned task can also [adopt an existing PR](docs/CLI.md#adopt-an-existing-pr) created outside
Altitude: `alt land --adopt-pr <number> --expected-head <full-sha> --reason "…" --message "…"`.
The owner inspects and incorporates its history in the task worktree first. Adoption records that
specific PR and original head; later task commits retain their provenance trailers. Updates go to
the original PR branch through fast-forward pushes, and a checked, reviewed `--merge` preserves
commit history without requesting branch deletion. An explicitly assigned next PR can be adopted
after the previous merge and its preserved history are verified on main; earlier receipts remain
immutable. Checks bind to the current base and head. A skipped job is exempt only when authoritative
workflow evidence proves it inapplicable and it is not required. Supported conditions are the
[main-push conjunction](docs/CLI.md#adopt-an-existing-pr) and `github.event_name != 'pull_request'`,
the latter only for a `pull_request` run. Task scope and merge holds still apply.

The coordinator can reconcile an operator's recorded merge decision, including a task UI choice
and a later conversational reaffirmation, through the daemon's
[`hold-merge --approval` command](docs/CLI.md#recorded-merge-approval). L3 cites the original messages
and question revision and judges that they authorize this merge without unresolved conditions or
revocation. Original project-chat decisions use the same path, independently for each named task PR.
L3 reviews later operator messages in both conversations. Explicitly delegated rebasing or overlap
resolution retains the original authority and adds the owner's current PR/head presentation;
it grants no scope expansion. The daemon verifies those sources, the current hold and PR before recording
the release. Meaning remains model judgment; accepting a design or discussing a PR alone never
releases its hold. The owner then rechecks and lands normally.

## How the work stays coherent

- **Project continuity.** One persistent L3 conversation holds direction across tasks. Discuss
  tradeoffs, change priorities, or return after delivery; follow-up and escalations feed back
  into that conversation.
- **Compiled handoffs and durable feedback.** L3 proactively informs affected active owners when
  project direction, decisions or another task's findings or delivery change their work. Targeted updates
  cite sources, superseded context and uncertainty, distinguishing proposals from verified delivery
  and operator decisions from recommendations. Owners reassess their plans and close obsolete questions
  against the original authority, keeping remaining operator choices visible.
  L3 carries out supported coordination and already-authorized continuation handoffs, preserving
  scope, evidence, holds and existing owner sessions without another administrative approval.
  Feedback about system or role behavior leads to a durable instruction change through the task/PR path
  unless scoped to a session or task; tentative suggestions and one-off exceptions stay scoped.
  L3 reports what is queued, merged and effective. Rules stay in their appropriate
  project or role source; see the [L3 persona](personas/l3.md).
- **Direct ownership.** One L2 owns each task end to end. Message it directly, inspect its live
  session, and follow its PR and report. A replacing public update and recorded activity show what
  is visible from the current worker. Messages queue for the engine's next checkpoint, with delivery
  labeled only when evidenced. Stop holds queued messages until an explicit correction or Continue
  resumes the saved session; file edits and the draft remain intact.
  Steering accepted before a clean completion is finalized keeps the owner reachable for the next turn.
  An explicit question block survives worker exit and restart; older queued messages do not
  resume it. A later message or explicit Resume brings the session back.
- **Independent execution.** Owners choose how to investigate, implement and use native helpers.
  Worktrees isolate changes; planned files guide coordination. Owners select and review the staged
  changes that `alt land` commits; checks and PRs make delivery reviewable.
  Shared-file changes still need rebasing and reconciliation by their owners.
- **Selective attention.** Needs you collects unresolved dilemmas across projects. Each item
  makes the task's purpose and actual choice clear with one question or a small group together,
  concise actions, and the material consequences needed to answer. Detailed reasoning and history
  stay accessible in the owning conversation. The model can ask a plain question, offer one
  recommended quick action, or offer two to three choices with a recommendation. A single quick
  choice takes one click; grouped selections send together, with nothing preselected. Open the item to
  discuss the actual question in its owning L2 conversation. A follow-up leaves it open; a clear
  answer is enough for the L2 to record your decision and continue. Answering part of a group leaves
  only its unanswered, relevant questions open. A changed direction can close
  a question that is no longer relevant, with the reason retained in chat. L3 handles
  questions the record settles and receives context when a block publishes or revises operator-directed questions,
  so it can coordinate scope or record-backed portions while operator approvals remain visible.
  Re-parking unchanged questions does not repeat the notification. The owner can close an unnecessary escalation by citing L3's answer
  and recording why existing authority settles it. Genuine operator decisions stay open, and merge
  holds retain their separate approval rules. L3 receives faults for recovery. Its selected heads-ups
  stay visible as compact lines in the project's conversation while routine events stay grouped
  behind Show. Monitor shows engine routing, usage windows and observed sessions,
  including missing or stale readings. Each usage window appears independently: an absent window
  is explicit, zero remains a reading, and available figures stay visible when stale.
- **Project work at a glance.** Work lists every current project task once, including tasks
  awaiting your answer, running, queued or waiting on L3. Compact status rows open the owning
  conversation at its question when one needs you; questions and quick answers live in Needs you
  and that chat. Recent completed tasks stay under **Done this week**. Only global Needs you has
  an attention badge: unanswered questions plus operational attention items, labelled separately
  in summaries. Answering changes attention immediately; execution status changes when observed.
  Unknown or stale reads are explicit, and Back returns to the originating Work or Needs you view.
- **Task tokens.** Follow cumulative locally observed input/output tokens, expand engine and
  owner/helper breakdowns, and retain the final observation with the archived task.

Task token accounting reads existing local engine records without model calls. Input includes cache
reads and writes once; output includes any reported reasoning subset. These are observed token
counts, separate from context occupancy, quota percentages and billing. The task header and report
show coverage and freshness: missing records stay unknown or partial, and native helpers are counted
only when local parentage supports attribution. Provider aggregates that cannot split helper usage
say so. See [counting semantics and limits](docs/SESSION_LIFECYCLE.md#task-token-accounting).

**L1** means an engine-native helper used by an L2, not a separately managed Altitude role. The L2
remains accountable; delegation suits bounded independent work and is optional for small tasks.
Expand **L2 usage details** in Monitor to see observed unique helpers across recorded attempts,
their attributable tokens, and per-helper identity and owner attempt context. Direct helpers and
descendants are distinguished when native parentage supports it; otherwise depth stays unknown.
Counts include observed identities without token counters and remain partial, never a definitive
total spawned. The same breakdown stays available in task and report details after archival.

## Configure concurrency

Running tasks default to **8 per project and 80 across the machine**. Both limits are persistent
settings. Inspect active limits, defaults, overrides and pending requests with `alt machine show`:

```sh
alt machine show
alt project set example --wip 12 --reason 'Allow more parallel tasks in this project'
alt machine set --wip 120 --reason 'Allow more parallel tasks across this machine'
alt project set example --unset-wip --reason 'Restore the project default of 8'
alt machine set --unset-wip --reason 'Restore the machine default of 80'
```

The operator can change both limits; a project's L3 can change its own project limit. Machine
changes are operator-only. Altd applies requests on its next tick without a free task slot or
service restart. Existing explicit project caps persist. Lowering either limit lets running work
continue and holds new launches until capacity is available. The machine default of 80 is
configurable above 80. See [concurrency commands and validation](docs/CLI.md#concurrency-limits).

## Engines that can evolve with the work

Altitude supplies project coordination, task ownership and delivery boundaries. The CLI engine
supplies execution tools, context management and native subagents; repository instructions,
skills and hooks shape how it works. Execution strategy stays with the owner rather than a
prescribed sequence of specialist stages.

Repository rules stay with each project. Altitude's authoritative rules are in [AGENTS.md](AGENTS.md);
`CLAUDE.md` imports that file. Both engines receive an explicit instruction-file path on fresh and
resumed L2/L3 turns, including L3's scratch directory outside the checkout. Projects with only
`CLAUDE.md` remain supported without changing their files. Global personas contain role guidance;
they do not carry Altitude's own project rules into other repositories.

Both project roles can use one installed engine with Auto. Configure project preference tiers with
`alt project set <name> --routing 'codex,claude:fable>claude:opus' --reason '…'`: commas tie options,
and `>` puts the next tier below them. Auto chooses the highest available tier, compares meaningful
weekly headroom within a tie, and falls back when an option is missing or exhausted. For a
Claude-only account with Opus, use `--routing 'claude:opus'`. Unknown access or quota stays unknown;
it does not mean unavailable or imply a subscription entitlement. Explicit engine/model pins stay
strict, and routing changes preserve running task attempts and their provider conversations.
See [routing configuration and examples](docs/CLI.md#automatic-routing-preferences).

Choose task reasoning depth at creation with `alt task new --effort high|xhigh …`.
New tasks on a supporting engine default to **High**; choose **Extra High** for selected difficult
work. The launch choice overrides native effort configuration and persists across messages and
resumes. Existing sessions retain their launch behavior. Task status distinguishes requested,
launched and observed effort; [effort selection](docs/CLI.md#task-reasoning-effort) explains support
and failure handling.

An exhausted model allowance excludes only that model. A reported reset schedules a retry;
an unknown reset stays unknown. Unpinned owners can continue on an eligible alternative as a
fresh attempt from their saved work. For owners already blocked by an exited worker, L3 can
request an explicit [provider handoff](docs/CLI.md#explicit-provider-handoff). The same task keeps
its worktree, edits, PRs, questions, history and merge holds; ordinary Resume retains its session.

Additional engines, including **OpenCode as a candidate**, require integration and verification
of their session, permission, authentication and usage behavior. The architecture is intended to
accommodate different model providers and billing/access arrangements too.
See the [engine boundary](docs/ARCHITECTURE.md#engine-integration-boundary) for current integration
limits, and the [roadmap](docs/ROADMAP.md#engines-platforms-and-distribution) for engines, macOS
support and installable distribution.

## Get started

**Early private preview for invited engineers.** Altitude currently runs for one operator on a
Linux machine with a systemd user manager. Setup uses a source checkout and manual configuration;
native macOS support and packaged installation are planned. Desktop and phone layouts are
available; remote phone access needs your private network and HTTPS setup.

You need Git, authenticated GitHub CLI, Python 3.12, Node 22.22.2+ (22.x) or 24.15+ (24.x), pnpm,
and one supported, authenticated coding CLI. Managed projects use `main`, `origin/main` and
GitHub PR delivery. With repository access:

```sh
git clone git@github.com:mburakyucel/altitude.git
cd altitude
pnpm --dir web install --frozen-lockfile
pnpm --dir web build
```

Follow [setup: register a project and start a conversation](docs/SETUP.md#register-a-project-and-start-a-conversation)
for routing preferences, project Git guards and a foreground localhost server. The guide distinguishes
source-checked commands from the remaining clean-machine setup verification. No open-source
license has been selected; public release is a separate milestone.

Git guards allow reference packing and fetch housekeeping while local main waits to fast-forward
to fetched `origin/main`. Packing preserves branch tips; unauthorized protected branch moves and
deletions remain blocked.

## Project conversations

Phone chat keeps project/task identity and a short activity status in one header, with text,
microphone and send controls together in a compact composer. Last-answer time, engine selection,
task metadata and operational actions open in details. Blocked and merge-held status stay distinct;
full reasons are available there, while actionable failures and the original question remain visible.
Bottom navigation hides during detected software keyboard use and returns on dismissal. Drafts,
selection and older-message reading position survive the change; task Conversation/Live session
tabs remain available. Desktop keeps its rail, metadata, direct task actions and shortcut hints.

PR and issue references in L3 and L2 prose, decisions, and reports are clickable, including saved
messages. `PR #250` and `pull request #250` open the project's pull request; `issue #247` and
`#247` use GitHub's issue route, which also resolves pull requests. `owner/repo#247` names its own
repository. Links open in a new tab. Existing links and code stay intact; unqualified references
stay text when the project's GitHub repository is unavailable.

Generated replies, briefs and summaries preserve upstream references as full URLs or
`owner/repo#number`. Bare references keep their local meaning; ambiguous historical text is not
assigned a guessed upstream repository.

Within an L2 task, Conversation and Live session are local views. Switching between them adds no
browser history entries. Browser Back and the task's Back control return to the preceding page;
on direct entry, the app Back control opens the owning project's L3 conversation. A `/live` link
opens the live session, including after reload.

An L2 question with attached evidence shows **View preview · vN** in Needs you and its owning
conversation. When the open question scrolls out of view, the chat's question navigation keeps
its preview reachable. Work opens that same question from the task row. The preview opens saved
screenshots and text in another browser tab, leaving the original view and draft in place;
closing that tab returns there. **Back to question** opens the exact discussion and decision controls.
The captured title identifies a design proposal or implementation review without relabelling old evidence.
Each version keeps its captured content; a replacement advances the question revision and earlier
links remain identifiable. Interactive wireframes are represented by screenshots of their states;
submitted HTML does not execute. Viewing or discussing a proposal leaves its question open, and
accepting a design leaves any merge hold intact. See [publishing a task design](docs/CLI.md#task-design-previews).
The [model-to-UI flow](docs/ARCHITECTURE.md#from-model-judgment-to-a-task-question-or-preview)
explains how owners invoke the CLI, how coordinator transports differ, and where questions,
previews and decisions persist.

Above the task composer, the latest public L2 words appear in an expandable two-line preview with
their own timestamp. Recorded activity has a separate age; quiet, missing and unavailable evidence
are explicit. Older output stays in Live session under existing retention, without duplicate replies
or model-generated summaries. Stop is directly accessible in both views on phone and desktop.
Desktop Escape stops only when no input, dialog, recording, menu or overlay owns the key. Stopping
keeps the draft editable; only confirmed termination enables correction or Continue. Continue keeps
the unsent draft, while sending a correction resumes with earlier queued messages followed by that
correction. Switching views preserves the draft and selection. Finished tasks are read-only.

Project conversations keep their own history and waiting messages. Switching projects clears the
unsent draft and local reply state; a turn already sent finishes in its original project. Returning
to that project shows its saved history and any active turn. Retry sends to the displayed project.
Switching also stops voice recording and releases the microphone; a late transcription cannot fill
the destination draft.

Submitted text awaiting confirmation stays recoverable in its original conversation across
navigation and reload in the same browser tab. A receipt clears that recovery copy immediately;
without one, the existing refusal or unconfirmed-delivery hint accompanies the recovered text.
Recovery never resends automatically or infers delivery from matching text. If the browser cannot
save a recovery copy, the message remains in the composer and is not submitted. Ordinary unsent
drafts still clear when switching projects. If a later recovery update fails, the latest text stays
available across in-app navigation and the composer asks you to keep the tab open until it can save.
Conversation polling continues while replies stream,
and a queued message moves into history as part of the server's guarded turn admission.

An accepted project or task message stays sent if its response stream, a later refresh, or the
immediate worker wake fails. The composer stays cleared and keeps any new draft. A refused send
restores recoverable text with Retry; an unconfirmed delivery preserves the text and asks you to
check the conversation before sending again. A failed assistant answer belongs to the sent turn.

Every fresh L3 provider session receives the project's latest 20 prior operator and assistant chat
messages, oldest first, as labeled historical context. This includes discussion with the same
provider before rotation. Server-triggered reports, restarts and other system events do not consume
those slots; the current turn is excluded. Each message includes at most 800 characters of text,
with longer text marked `[truncated]`. Resumed sessions keep native conversation history and receive
only a bounded handoff of messages missed while another provider handled L3. No tool
transcripts or generated summaries are replayed.

For decisions beyond that handoff, L3 uses [`alt l3 search "literal text"`](docs/CLI.md#historical-evidence-search).
It searches the project's human conversation and active/archived task conversations, reports and
digests, returning original excerpts, dates, speaker attribution and stable source references.
Matching records appear newest first with adjacent context; clipped text and omitted results are
explicit. Missing evidence returns no result. Historical evidence preserves context for judgment;
current instructions and task records govern action. Lookup makes no model calls and writes no memory.

## Project faults

When uncommitted changes block task dispatch, L3 or the operator can use
[`alt task preserve-checkout <slug> --reason "…"`](docs/CLI.md#dirty-checkout-recovery)
for a blocked task that has never launched. The daemon preserves staged, unstaged and untracked
changes on a uniquely named local archive branch and records its immutable snapshot SHA. The
snapshot's parent retains staged content; applying the complete snapshot flattens staging intent.
The task owner inspects and applies authorized changes in its isolated worktree, selects what to
stage, and delivers through a PR. Expected files are [coordination guidance](docs/CLI.md#task-file-lists).
Archives remain local until explicit operator removal; Altitude never pushes or deletes them.
Existing stash records and stashes remain readable and recoverable. Ordinary dispatch still
requires clean main at `origin/main`; a restart does not clear an unresolved checkout fault.

A task's system fault blocks that task and keeps its incident evidence, FYI and coordinator
notification in the owning project. Unchanged saved blockers stay quiet across restarts and incident
windows. Another affected task, a new blocker, or changed details still gets a coordinator notification.
Repair-task faults do not wake the
coordinator again. Machine faults without a project notify the registered `altitude` project, or
remain in the machine fault ledger when it is absent.

The originating coordinator checks public delivery evidence and local observations that the actual
cause is gone before requesting a reason-bearing resume of the original session. Notification receipt,
issue closure and unrelated restarts do not establish repair. Coordinator messages to faulted tasks
remain readable without waking them; direct operator discussion remains available. Landing checks
and merge holds still apply.

For an external CI wait, L3 records one bounded [CI recheck](docs/CLI.md#durable-ci-recheck) on the
existing fault-blocked task. Status names its next action and time. The daemon follows relevant fresh
CI or submits one selected run rerun, preserves uncertain submission evidence, and delivers changed
results durably to that project's L3. Artifact capacity needs fresh uploaded artifacts; a passing run
with a tolerated upload error does not prove recovery. The probe leaves owner resumption to L3.

A project's L3 reports an upstream Altitude defect with
[`alt issue upstream`](docs/CLI.md#upstream-altitude-defects), supplying a fictional or redacted
reproduction. The affected project's incident evidence, tasks and coordinator conversation stay in
that project. After confirmed creation or verified linkage, a registered local `altitude` development
project receives one fixed issue-link notification when its Git origin matches the upstream repository.
Installations without that matching project remain issue-only. Altitude's operator/coordinator selects
any implementation separately; reporting never creates, reuses, or resumes a receiving-project task.

For a system incident, include `--incident <id>` to track a confirmed upstream URL or a missing,
failed, or uncertain delivery with an actionable reason. `alt incident list`, coordinator state,
and restart summaries expose the gaps. Known links survive repeat faults and restarts; an uncertain
attempt blocks another creation until the operator checks existing issues. The coordinator can
attach a verified match with `alt issue upstream --incident <id> --url <url>`. Reporting remains
explicitly authorized; historical publication/backfill is a separate decision.
Local notification status is separate from publication status in incident inspection and coordinator
summaries. The receiving queue and chat carry only the fixed public link, with no private evidence or
task association. Repeating a confirmed incident command retries a failed notification without posting
another issue; the receiving project's queue and retained event receipts deduplicate the full issue URL
across source projects and restarts.

## Remove a project

In the project's **More actions** menu, **Remove project** detaches L3 and stops Altitude
management. Finish or reject unfinished tasks and wait for their workers and any L3 turn first.
The repository, remaining worktrees, history, provider sessions and queued messages stay on disk.
Add the same folder and project name again to attach L3, restore its history and resume waiting
messages. Removing the last project opens First run; otherwise a remaining project is selected.
`alt project remove <name>` uses the same checks. See [project lifecycle](docs/CLI.md#project-lifecycle).

## Documentation

| Start here | Go deeper |
| --- | --- |
| [Setup](docs/SETUP.md) | [Architecture and engine boundary](docs/ARCHITECTURE.md) |
| [Rendered walkthrough](docs/WALKTHROUGH.md) | [Engine and session lifecycle](docs/SESSION_LIFECYCLE.md) |
| [Contributing](CONTRIBUTING.md) | [Development and checks](docs/DEVELOPMENT.md) |
| [Release checkpoints](docs/RELEASING.md) | [Changelog](CHANGELOG.md) |
| [CLI usage](docs/CLI.md) | [Service operations and mobile access](docs/OPERATIONS.md) |
| [Roadmap and release prerequisites](docs/ROADMAP.md) | [Design boards and UI specification](design/wireframes/README.md) |

For an installed service, [operations](docs/OPERATIONS.md#service-lifecycle) documents automatic
activation and the operator's `make restart` command. Browser target and installation instructions
are in [development and checks](docs/DEVELOPMENT.md#browser-walkthroughs).

Every PR runs `make check`: Python, web tests, typecheck/build and phone/desktop browser flows
against isolated fictional state, with external engines replaced by deterministic fixtures.
Hosted CI is suspended. Local landing retains a self-contained HTML report with named walkthrough
screenshots and failure traces, plus check logs; raw attachments are not copied again.
See [local delivery evidence](docs/DEVELOPMENT.md#ci-and-candidate-identity).
Review captures stay in ignored artifacts and may be linked from PRs; maintained design boards and
curated documentation illustrations describe the current product. See the [UI rules](AGENTS.md#ui).
These repeated checks make no model calls. Live-provider validation is deferred; the
[coverage matrix](docs/DEVELOPMENT.md#coverage-and-limits) records what the tests establish.
Daily preview readiness checkpoints and as-needed releases select a validated source version and curated notes;
the operator decides whether to publish it. Merged changes continue activating automatically.

The [project rules](AGENTS.md#working-rules-for-every-pr) govern implementation and review;
the architecture and lifecycle pages describe the current system. [Pull requests](https://github.com/mburakyucel/altitude/pulls) show current changes;
[issue #219](https://github.com/mburakyucel/altitude/issues/219) tracks this onboarding milestone
and the remaining repository-presentation work.

## Feedback

Invited collaborators can [open an issue](https://github.com/mburakyucel/altitude/issues/new/choose)
with what they tried, expected behavior, actual behavior, and a small reproducible example.
Setup friction and confusing product language are useful feedback too. Keep examples fictional
or redacted; send security-sensitive details privately to the maintainer through your invitation
channel. See [contributor guidance](CONTRIBUTING.md) before proposing implementation work.

Managed projects' L3s can use `alt issue upstream --title "…" -` through their coordinator transport.
The JSON body describes expected and actual behavior, a fictional/redacted reproduction, and the
version if known. Altd resolves the product repository from its installation origin or the operator's
`ALTITUDE_UPSTREAM_ISSUE_REPOSITORY` setting; callers cannot choose a destination or other upstream
action. See [the command and MCP example](docs/CLI.md#upstream-altitude-defects).
