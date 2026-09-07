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
  **“An AI development workspace for coordinating coding agents across projects, from conversation
  to checked PRs.”** Recommended topics: `ai-development`, `coding-agents`, `developer-tools`,
  `developer-workspace`, `git-worktrees`. These are recommendations, not applied settings.

Related implementation gaps to evaluate separately, without expanding the docs milestone:

- A portable installer/service template and clearer onboarding for existing Git hook systems.
- Configured installed-engine selection and readiness reporting; Auto currently reasons from
  quota, not binary/authentication availability. Single-engine use requires explicit project pins.
- An engine/access compatibility matrix, including role-model defaults, launch environment
  filtering and optional usage telemetry. Direct API and service-mediated access need verification.
- Additional CLI integrations and gradual removal of remaining engine assumptions outside the
  [integration boundary](ARCHITECTURE.md#engine-integration-boundary). OpenCode and Bedrock are
  candidates, not supported integrations or promised delivery dates.

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
