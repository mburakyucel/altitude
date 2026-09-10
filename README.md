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
**performance baseline**. Each gets its own L2, brief, file scope and worktree. The performance
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

One active task can deliver several PRs. When authorized work remains after a merge, the same owner
continues in its existing conversation and worktree and runs `alt land` again. It puts only the
follow-up changes onto current main and opens another PR, with its own checks and review holds.
Running it without new work creates nothing. Earlier deliveries remain recorded, and the final
report covers all of them. See [continuation after merge](docs/CLI.md#continue-after-a-pr-merges).

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

When the operator replies **Good to merge** or **You can merge it** directly after an owner's PR
presentation, the coordinator can apply that recorded approval through the daemon's
[`hold-merge --approval` command](docs/CLI.md#recorded-merge-approval). The daemon checks the message,
current hold and unchanged PR before recording the release. Case, surrounding whitespace and a final
period or exclamation mark are accepted; questions, conditions and extra prose are refused.
The owner then rechecks and lands normally.

## How the work stays coherent

- **Project continuity.** One persistent L3 conversation holds direction across tasks. Discuss
  tradeoffs, change priorities, or return after delivery; follow-up and escalations feed back
  into that conversation.
- **Compiled handoffs and durable feedback.** L3 briefs and steers owners from the relevant discussion,
  including corrections and uncertainty, with operator decisions distinct from its recommendations.
  Clearly reusable feedback leads to an instruction change through the task/PR path; one-off steering
  stays scoped. L3 reports what is queued, merged and effective. Rules stay in their appropriate
  project or role source; see the [L3 persona](personas/l3.md).
- **Direct ownership.** One L2 owns each task end to end. Message it directly, inspect its live
  session, and follow its PR and report. Messages queue for the engine's next checkpoint.
  An explicit question block survives worker exit and restart; older queued messages do not
  resume it. A later message or explicit Resume brings the session back.
- **Independent execution.** Owners choose how to investigate, implement and use native helpers.
  Worktrees isolate changes; file leases bound staging; checks and PRs make delivery reviewable.
  Shared-file changes still need rebasing and reconciliation by their owners.
- **Selective attention.** Needs you collects unresolved dilemmas across projects. Each item
  shows one question or a small group together. The model can ask a plain question, offer one
  recommended quick action, or offer two to three choices with a recommendation. A single quick
  choice takes one click; grouped selections send together, with nothing preselected. Open the item to
  discuss the actual question in its owning L2 conversation. A follow-up leaves it open; a clear
  answer is enough for the L2 to record your decision and continue. Answering part of a group leaves
  only its unanswered, relevant questions open. A changed direction can close
  a question that is no longer relevant, with the reason retained in chat. L3 handles
  questions the record settles and receives faults for recovery, and its FYIs are system rows in
  the project's conversation. Monitor shows engine routing, usage windows and observed sessions,
  including missing or stale readings. Each usage window appears independently: an absent window
  is explicit, zero remains a reading, and available figures stay visible when stale.
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

Project conversations keep their own history and waiting messages. Switching projects clears the
unsent draft and local reply state; a turn already sent finishes in its original project. Returning
to that project shows its saved history and any active turn. Retry sends to the displayed project.
Switching also stops voice recording and releases the microphone; a late transcription cannot fill
the destination draft.

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
The task owner inspects and applies the changes within its recorded lease in its isolated worktree
and delivers through a PR. Missing scope goes through an ordinary L2 block to L3. L3 assigns the
complete lease with `alt task paths`, verifies it in task status, and messages the same owner to
resume; L2 checks the recorded scope before applying work. See [file leases](docs/CLI.md#task-file-leases).
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
CI attempts to upload fictional reports and screenshots; upload failures remain visible and do
not fail the test gate. Downloadable artifacts may be unavailable even when all tests pass.
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
