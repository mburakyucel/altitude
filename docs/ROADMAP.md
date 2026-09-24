# Roadmap

## Friends-and-family readiness priorities

The operator's September 14, 2026 direction selects a low-friction private trial on friends'
own machines. Readiness tracker #350 owns
delivery/acceptance links; selected work remains subject to proposal, security, UX and merge holds.

| Priority | Scope and owner/dependency |
| --- | --- |
| P0 selected | #349/#348 project Setup, Git guards and scoped repair stay with `make-project-setup-visible-and-repairabl`; its design/merge holds remain independent. |
| P0, Linux increment delivered | #225 native runtime, #226 installation/update lifecycle and private-trial #219 onboarding: PR #356 delivers the Linux archive. Remaining work is documented below for L3's later intake; native Mac implementation and host evidence are deferred. |
| P0 before access sharing | Reconcile #233's recorded history audit and changes after its cutoff; resolve material exposure findings privately. Audit/publication authority stays separate from runtime installation. |
| P1 after core installation | #231 private backup/staged restore and #224 remaining continuity/reboot acceptance. Neither authorizes live-provider tests or production lifecycle operations. |
| Reassess only if trial-blocking | #222/#334 confusion observed during the trial; no broad redesign prerequisite. Composer/drafts stay with `preserve-project-drafts-and-make-voice-p`. |

L3 routes subsequent issue intake using these priorities, recorded ownership, dependencies and
verified remaining acceptance. Keep related work with its owner; do not start duplicate tasks or
drain unrelated backlog. Pending implementation is not delivery. Parent issues remain open until
their full acceptance, including required operator review, is verified. Public release, license,
visibility, invitations and security-contact decisions remain separate. Private-trial readiness
requires exact OS/architecture, application/engine versions and observed compatibility evidence;
fixtures do not establish fresh-machine or live-provider success.

### Portable installation: delivered and remaining

PR #356 delivers the Linux CLI, daemon and
built UI archive, localhost HTTPS with explicit certificate trust, prerequisite/readiness checks,
recoverable updates and uninstall with retained user data. Independent review and the exact-candidate
suite pass: 1,342 Python tests, 343 web tests, typecheck/build and 316 phone/desktop browser cases;
one optional host probe is skipped. Deployment is verified. These checks do not prove a fresh-machine
installation, native Mac behavior or engine authentication.

The operator's September 16, 2026 direction finishes the installation task after this handoff and
defers physical Mac testing. The private-trial path remains Linux-only. L3 retains the following
remaining acceptance under #225/#226/#219/#350; those issues stay open:

- **Native macOS runtime and installation:** implement the common engine/authority contract with
  native per-user service lifecycle, confinement and installed update/recovery behavior. macOS 15
  and 26 on Apple silicon are proposed targets, not supported platforms. No Mac runtime is delivered.
- **Native evidence:** on a disposable Mac account, record exact OS/chip, application, Python and
  engine versions. Verify allowed writes and denied writes outside the task, ordinary and detached
  descendant Stop, timeout after owner exit, restart/adoption without duplicate workers, preserved
  sessions/messages/holds, and login/logout behavior. Record reboot evidence separately. Fixtures
  alone cannot prove these guarantees; weaker confinement or a new privilege model needs review.
- **Install-to-first-task acceptance:** verify a clean-machine install, explicit OS/browser trust,
  one-engine readiness, project Setup/guards, first conversation, isolated task, Stop/resume and
  checked PR delivery. Cover failed update/rollback and uninstall retention on each claimed platform;
  keep untested rows and actionable failures explicit in the onboarding/compatibility documentation.

The operator offers a Mac for later testing, with timing still deferred; no immediate Mac use or
paid runner is authorized. Native probes make no provider calls or model downloads. Live-provider
testing retains its separate standing deferral. Project Setup/guards and composer/drafts retain
their existing ownership; future integration consumes those interfaces. Public-release/history-audit
work and P1 backup/continuity remain separately sequenced above.

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
per day after validation; publication is explicit and does not gate source-deployment activation.
Live-provider testing is deferred by the operator's 2026-09-08 decision. The
[coverage matrix](DEVELOPMENT.md#coverage-and-limits) keeps provider/host compatibility and other
remaining validation limits explicit; this does not establish clean-machine or public readiness.

The direction is the same project and task workflow across CLI engines and supported machines.
The current [setup](SETUP.md) supplies a private Linux x86_64 archive; Ubuntu 24.04 is its initial
target. Native macOS 15/26 Apple-silicon implementation and validation remain deferred as described
above. The common confinement contract still needs proof before any support claim.

| Work | Intended outcome | Current boundary |
| --- | --- | --- |
| Additional CLI engines | Integrate candidates such as **OpenCode**, preserving native tools, sessions, context and helpers. | Codex and Claude Code work today. Each added engine needs launch/resume/stop, permissions, authentication and optional usage observations implemented and verified through task delivery. |
| Engine readiness and access | Select from installed, authenticated engines; document verified model-provider, subscription and API/access configurations. | One engine suffices with Auto or an explicit pin. Local readiness distinguishes configured, unknown and tested; it makes no provider request. Launch environment and role-model settings constrain configuration inheritance. |
| [macOS runtime · #225](https://github.com/mburakyucel/altitude/issues/225) | Native OS/service integration with verified start, task execution, stop/resume, restart/adoption and shutdown. | Current service units and process/sandbox facilities assume Linux. |
| [Installable daemon and updates · #226](https://github.com/mburakyucel/altitude/issues/226) | A packaged CLI, daemon and built web app, onboarding, per-user service, versioned updates and recoverable uninstall on supported Linux and macOS. | Linux archive installation uses localhost HTTPS, explicit CA trust and retained data. Native Mac and clean-machine acceptance remain pending; this increment does not close the parent. |

Engine work belongs at the [integration boundary](ARCHITECTURE.md#engine-integration-boundary),
with remaining assumptions outside it moved as those files are touched. Platform support and
distribution are related but distinct: the installer depends on working lifecycle semantics on
each OS. Clean-machine checks must cover authentication, existing Git hooks, first conversation
and a checked task delivery. Selected platform/distribution work proceeds under #350; additional
engines remain backlog. None of this makes the product walkthrough a compatibility claim.

## Pending command-surface decision

The operator decides whether to remove retained commands without current callers:
`task paths|brief|list`, `fyi`, `decisions`, `digest`, `monitor`, `dispatch`, `verify`, `poll`,
`chat`, `l3-reset`, and `incident list`. Their retention is not authorization for unattended cleanup.
Current project and review rules live in [AGENTS.md](../AGENTS.md); completed migrations live in Git history.

## Current product work

Every incident on any managed project becomes a sanitized [incident issue](CLI.md#incident-issues)
at the installation's product target; local evidence and recovery stay in the affected project.
Altitude's operator/coordinator selects implementation separately; there is no automatic issue-to-task
intake or cross-project repair.

The [UI specification](../design/wireframes/SPEC.md) governs the design and lists the implementation
slices, each one task. The durable backlog is GitHub issues selected by the operator. The current priorities are:

- expose a clear project overview of active work and items that need the operator;
- make direct task conversation with the owning L2 simple and readable;
- keep incident evidence and operational recovery visible without turning them into recursive
  workflows;
- validate the guarded landing path and recovery behavior in normal use before adding more
  automation.

New architecture or feature proposals start as conversation or a GitHub issue. L3 decides whether
to answer directly or delegate one L2; no backlog item is pulled or implemented autonomously.
