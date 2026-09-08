# Roadmap

## Early-user onboarding and public release

The repository is an early private preview for invited engineers. The README-first milestone of
issue #219 provides positioning, a fictional [walkthrough](WALKTHROUGH.md),
[setup](SETUP.md), [contributor guidance](../CONTRIBUTING.md), and a feedback entry point. It
does not complete the entire issue or make the repository publicly available open-source software.
The issue is linked from the [README](../README.md#documentation).

Remaining repository-presentation work under #219:

- The maintainer chooses a license; record that decision and add its license text before public
  release. No license is selected by this documentation milestone.
- The maintainer decides repository visibility and release timing separately.
- Review the repository and history for material unsuitable for public release, and establish a
  public security-reporting/contact path and contribution process.
- Validate the documented path on a second clean machine, including engine authentication,
  sandbox/user-service support, Git guards, first conversation and one checked task delivery.
- Apply repository metadata through an authorized maintainer operation. Recommended description:
  **“Persistent project orchestration for coding agents: project direction, directly reachable task
  owners, isolated worktrees and checked PRs.”** Recommended topics: `ai-development`, `coding-agents`, `developer-tools`,
  `developer-workspace`, `git-worktrees`. These are recommendations, not applied settings.

## Engines, platforms and distribution
Private-preview [release checkpoints](RELEASING.md) use versioned source snapshots, curated
[release notes](../CHANGELOG.md), full deterministic candidate checks and documented recovery.
During active preview work, readiness is checked daily and useful fixes can release several times
per day after validation; publication is explicit and does not gate automatic activation.
Live-provider testing is deferred by the operator's 2026-09-08 decision. The
[coverage matrix](DEVELOPMENT.md#coverage-and-limits) keeps provider/host compatibility and other
remaining validation limits explicit; this does not establish clean-machine or public readiness.

The direction is the same project and task workflow across CLI engines and supported machines.
These are intended capabilities, with no promised dates; the current setup remains the Linux
source installation documented in [SETUP.md](SETUP.md).

| Work | Intended outcome | Current boundary |
| --- | --- | --- |
| Additional CLI engines | Integrate candidates such as **OpenCode**, preserving native tools, sessions, context and helpers. | Codex and Claude Code work today. Each added engine needs launch/resume/stop, permissions, authentication and optional usage observations implemented and verified through task delivery. |
| Engine readiness and access | Select from installed, authenticated engines; document verified model-provider, subscription and API/access configurations. | Auto reasons from quota, so single-engine use requires explicit pins. Launch environment and role-model settings constrain configuration inheritance. **Bedrock is a provider-access service**, to evaluate separately from CLI engines. |
| [macOS runtime · #225](https://github.com/mburakyucel/altitude/issues/225) | Native OS/service integration with verified start, task execution, stop/resume, restart/adoption and shutdown. | Current service units and process/sandbox facilities assume Linux. |
| [Installable daemon and updates · #226](https://github.com/mburakyucel/altitude/issues/226) | A packaged CLI, daemon and built web app, onboarding, per-user service, versioned updates and recoverable uninstall on supported Linux and macOS. | Users currently clone source, build the app and adapt configuration. The packaging/update architecture still needs an operator decision. |

Engine work belongs at the [integration boundary](ARCHITECTURE.md#engine-integration-boundary),
with remaining assumptions outside it moved as those files are touched. Platform support and
distribution are related but distinct: the installer depends on working lifecycle semantics on
each OS. Clean-machine checks must cover authentication, existing Git hooks, first conversation
and a checked task delivery. These projects are backlog, not prerequisites for reading the product
walkthrough or claims of support already shipped.

## Simplification (complete)

The module-by-module simplification finished on 2026-09-03; [SIMPLIFICATION.md](SIMPLIFICATION.md)
holds the decisions, the working rules that still apply, and the deletion ledger. Service lifecycle
stays separate from ordinary source work and requires explicit authorization.

## Current product work

Project L3s can report Altitude defects now through
[`alt issue upstream`](CLI.md#upstream-altitude-defects). Reporting is create-only, uses the
installation's product target and a fictional/redacted reproduction, and leaves local evidence and
recovery in the affected project. Altitude's operator/coordinator selects implementation separately;
there is no automatic upstream issue intake or cross-project repair.

The product redesign, approved on 2026-09-05, replaced the 2026-09-03 wireframes under
`design/wireframes/`; its `SPEC.md` governs the UI and lists the implementation slices, each one
task. The durable backlog is GitHub issues selected by the operator. The current priorities are:

- expose a clear project overview of active work and items that need the operator;
- make direct task conversation with the owning L2 simple and readable;
- keep incident evidence and operational recovery visible without turning them into recursive
  workflows;
- validate the guarded landing path and recovery behavior in normal use before adding more
  automation.

New architecture or feature proposals start as conversation or a GitHub issue. L3 decides whether
to answer directly or delegate one L2; no backlog item is pulled or implemented autonomously.
