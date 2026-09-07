# Altitude

**An AI development workspace — a control center for your AI development team.**

Altitude is a workspace for running AI coding agents across your software projects. Discuss what
you want to build, coordinate work through review and merge, and stay directly involved wherever
your judgment is needed. Project conversations, task ownership, live agent sessions, and decisions
come together in one place.

It is built for software engineers who already use coding agents and care about architecture,
code quality, and what lands in their repositories. As work spans tasks and projects, Altitude
helps you keep track of direction, ongoing changes, and decisions without juggling every session
yourself. The current focus is **one operator managing projects on one Linux machine**.

**Early private preview.** Setup is still manual in places. Start with the
[setup guide](docs/SETUP.md) or the [short walkthrough](docs/WALKTHROUGH.md).

## What working with Altitude feels like

For example, you want to add CSV export to a project:

1. **Discuss the change in the project conversation.** Explain the user need and constraints to
   the project's coordinator. It can answer directly or create a task when there is work to do.
2. **A task gets one owner and an isolated workspace.** The owner investigates the repository
   and chooses how to implement the change, including whether helpers or a proposal would help.
3. **Follow or steer the work.** Open the task to message its owner directly and inspect its
   live session. “Keep the existing column names” stays with the task as durable conversation.
4. **Find the decision that needs you.** **Needs you** gathers questions across projects. Open
   the task for context and give a product answer in its conversation; the card also offers
   Resume or Reject when that is the action you need.
5. **Follow delivery through a PR.** The owner runs the project's checks and appropriate review,
   then lands through the guarded PR path. A merge hold keeps it for your review; otherwise the
   owner can merge when ready. The verified report and conversation preserve the outcome.

This is one example, with the execution approach chosen for the work. The
[illustrated text walkthrough](docs/WALKTHROUGH.md) follows fictional projects through the
project conversation, direct task conversation/live session, and cross-project decisions view.
The UI and technical docs call the project coordinator **L3** and the task owner **L2**.

## Why the coordination stays small

- **Clear responsibility, flexible execution.** One coordinator per project and one owner per
  task keep it clear who to talk to. The owner chooses investigation, decomposition, delegation,
  and implementation strategy. A fixed sequence of stages and specialist roles would encode
  assumptions that age as agents improve.
- **Engineering judgment stays close to the work.** Set direction at the project level, steer
  an individual task, or respond to decisions across projects. Isolated worktrees, declared file
  scope, applicable checks, and the PR boundary make changes reviewable.
- **Build on the agents' capabilities.** CLI engines supply their tools, sessions, context
  management, and native helper facilities. Altitude adds focused coordinator/owner instructions
  and delivery boundaries. Repository instructions, native skills, hooks, and agent facilities
  are the place to customize how work gets done within those boundaries.
- **Keep engine and access choices separate from the workflow.** The organizing idea is ongoing
  CLI sessions behind replaceable integrations. Improvements in an engine should improve the
  workspace without requiring another coordination mechanism for each new capability.

## Engines today and the integration direction

**Codex and Claude Code are the current integrations.** You can pin both project roles to one
installed, authenticated engine; two subscriptions are not a prerequisite. Auto routing uses
reported quota windows, so single-engine setup needs explicit pins today.

The [engine boundary](docs/ARCHITECTURE.md#engine-integration-boundary) centers on
[`altitude/engines.py`](altitude/engines.py) for launching, resuming, stopping and observing
sessions, [`altitude/route.py`](altitude/route.py) for selection and usage windows, and
[`altitude/config.py`](altitude/config.py) for engine settings. It is an internal integration
boundary, not a plug-in API that accepts any CLI unchanged.

The intended design lets your choice of agent, model provider, subscription, direct API billing,
or access service vary independently of the project workflow. **OpenCode and access through
services such as Bedrock are future integration candidates, not supported setup paths.** Each
integration may need different session, authentication, capability and usage models; it should
preserve the engine's native behavior rather than force every engine into today's interface.
[Current gaps and release prerequisites](docs/ROADMAP.md#early-user-onboarding-and-public-release)
are tracked separately from this documentation milestone.

## Available now

| Capability | What you can do |
| --- | --- |
| Multiple projects | Keep project conversations and active work together; move between repositories in one workspace. |
| Direct conversations | Talk to the coordinator or a task owner; queued messages reach ongoing sessions at engine-specific checkpoints. |
| Parallel work | Run tasks in separate Git worktrees and branches with one accountable owner each; shared-file changes still need reconciliation. |
| Delivery visibility | Follow PRs, check outcomes, merge holds, reports and archived task conversations. |
| Cross-project decisions | Use Needs you to find questions escalated for your judgment, with links back to the task. |
| Routing and usage | Inspect engine selection reasons, reported usage windows and sessions; missing or stale telemetry is shown explicitly. |
| Task tokens | Follow cumulative locally observed input/output tokens, expand engine and owner/helper breakdowns, and retain the final observation with the archived task. |
| Desktop and mobile web | Use the desktop conversation/work panels or phone tabs. Remote access needs a configured private network; voice also needs browser support and a local transcription service. |

Task token accounting reads existing local engine records without model calls. Input includes cache
reads and writes once; output includes any reported reasoning subset. These are observed token
counts, separate from context occupancy, quota percentages and billing. The task header and report
show coverage and freshness: missing records stay unknown or partial, and native helpers are counted
only when local parentage supports attribution. Provider aggregates that cannot split helper usage
say so. See [counting semantics and limits](docs/SESSION_LIFECYCLE.md#task-token-accounting).

## Get started

The current runtime targets Linux with a working **systemd user manager**, Git and authenticated
GitHub CLI, Python (3.12 in CI), Node 22.22.2+ (22.x) or 24.15+ (24.x), pnpm, and at least one compatible authenticated coding
CLI. Managed projects use a clean primary `main` checkout, `origin/main`, and GitHub PR delivery.
There is no packaged installer or verified native macOS/Windows runtime yet.

With repository access:

```sh
git clone git@github.com:mburakyucel/altitude.git
cd altitude
pnpm --dir web install --frozen-lockfile
pnpm --dir web build
```

Then follow [setup: register a project and start a conversation](docs/SETUP.md#register-a-project-and-start-a-conversation)
for engine pins, Git guards and the foreground localhost command. That guide names the manual
steps and machine-specific defaults; the shipped service unit needs adaptation for another host.
Altitude is still being prepared for invited collaborators. No open-source license has been
selected, and this milestone does not change repository visibility.

## Project faults

A task's system fault blocks that task and keeps its incident evidence, FYI and coordinator
notification in the owning project. Repeated fault kinds are deduplicated within each project;
another affected task still gets a coordinator notification. Repair-task faults do not wake the
coordinator again. Machine faults without a project notify the registered `altitude` project, or
remain in the machine fault ledger when it is absent.

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
| [Example walkthrough](docs/WALKTHROUGH.md) | [Engine and session lifecycle](docs/SESSION_LIFECYCLE.md) |
| [Contributing](CONTRIBUTING.md) | [Development and checks](docs/DEVELOPMENT.md) |
| [CLI usage](docs/CLI.md) | [Service operations and mobile access](docs/OPERATIONS.md) |
| [Roadmap and release prerequisites](docs/ROADMAP.md) | [Design boards and UI specification](design/wireframes/README.md) |

For an installed service, [operations](docs/OPERATIONS.md#service-lifecycle) documents automatic
activation and the operator's `make restart` command. Browser target and installation instructions
are in [development and checks](docs/DEVELOPMENT.md#browser-walkthroughs).

The [simplification record](docs/SIMPLIFICATION.md) explains the project's design decisions and
review rules. [Pull requests](https://github.com/mburakyucel/altitude/pulls) show current changes;
[issue #219](https://github.com/mburakyucel/altitude/issues/219) tracks this onboarding milestone
and the remaining repository-presentation work.

## Feedback

Invited collaborators can [open an issue](https://github.com/mburakyucel/altitude/issues/new/choose)
with what they tried, expected behavior, actual behavior, and a small reproducible example.
Setup friction and confusing product language are useful feedback too. Keep examples fictional
or redacted; send security-sensitive details privately to the maintainer through your invitation
channel. See [contributor guidance](CONTRIBUTING.md) before proposing implementation work.
