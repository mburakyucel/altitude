# Roadmap

## Friends-and-family readiness priorities

The operator's September 14, 2026 direction selects a low-friction private trial on friends'
own machines. Readiness tracker #350 owns
delivery/acceptance links; selected work remains subject to proposal, security, UX and merge holds.

| Priority | Scope and owner/dependency |
| --- | --- |
| P0 selected | #349/#348 project Setup, Git guards and scoped repair stay with `make-project-setup-visible-and-repairabl`; its design/merge holds remain independent. |
| P0, Linux increment delivered | #225 native runtime, #226 installation/update lifecycle and private-trial #219 onboarding: PR #356 delivers the Linux archive. The native macOS runtime is implemented with `macos-support-native-runtime-behind-the`; its acceptance and remaining work are documented below. |
| P0 before access sharing | Reconcile #233's recorded history audit and changes after its cutoff; resolve material exposure findings privately. Audit/publication authority stays separate from runtime installation. |
| P1 after core installation | #231 private backup/staged restore and #224 remaining continuity/reboot acceptance. Neither authorizes live-provider tests or production lifecycle operations. |
| Reassess only if trial-blocking | #222/#334 confusion observed during the trial; no broad redesign prerequisite. Composer/drafts stay with `preserve-project-drafts-and-make-voice-p`. |

L3 routes subsequent issue intake using these priorities, recorded ownership, dependencies and
verified remaining acceptance. Keep related work with its owner; do not start duplicate tasks or
drain unrelated backlog. Pending implementation is not delivery. Parent issues remain open until
their full acceptance, including required operator review, is verified. Public release, license
and visibility decisions remain separate. Private-trial readiness
requires exact OS/architecture, application/engine versions and observed compatibility evidence;
fixtures do not establish fresh-machine or live-provider success.

### Portable installation: delivered and remaining

PR #356 delivers the Linux CLI, daemon and
built UI archive, localhost HTTPS with explicit certificate trust, prerequisite/readiness checks,
recoverable updates and uninstall with retained user data. Independent review and the exact-candidate
suite pass: 1,342 Python tests, 343 web tests, typecheck/build and 316 phone/desktop browser cases;
one optional host probe is skipped. Deployment is verified. These checks do not prove a fresh-machine
installation, native Mac behavior or engine authentication.

The manually dispatched [Linux installation lifecycle harness](DEVELOPMENT.md#installation-lifecycle-acceptance)
adds packaged installation, real per-user service/HTTPS checks, update, failed activation recovery and
uninstall retention on disposable Ubuntu 24.04 runners. Its first hosted execution remains pending
until a run's artifact/source identity and results are recorded. The synthetic version pair uses
one source commit, so it supplies no cross-release migration evidence. Runs are independent of
other owners' delivery and the required PR gate. This is partial #226 acceptance; the parent stays
open, and macOS remains with `macos-support-native-runtime-behind-the`. Minimal OS installation,
reboot/login/logout, browser/device CA trust, public-download bootstrap, native confinement and
live-provider compatibility remain outside this harness's evidence.

The private-trial path is Linux-only until the macOS evidence below is recorded. The following
remaining acceptance stays under #225/#226/#219/#350; those issues stay open:

- **Native macOS runtime and installation:** the common engine/authority contract runs on a per-user
  LaunchAgent, with each job its own launchd job (coalition Stop, a supervisor-held time limit),
  Altitude's Seatbelt profile around Claude, Codex's own sandbox, and the installer's macOS branch.
  macOS 15 and 26 on Apple silicon remain targets, not supported platforms, until the evidence below
  is recorded. Running before any login (boot mode) is a separate, later increment.
- **Native evidence:** on a disposable Mac account, record exact OS/chip, application, Python and
  engine versions. Verify allowed writes and denied writes outside the task, ordinary and detached
  descendant Stop, timeout after owner exit, restart/adoption without duplicate workers, preserved
  sessions/messages/holds, and login/logout behavior. Record reboot evidence separately. Fixtures
  alone cannot prove these guarantees; weaker confinement or a new privilege model needs review.
  Probe and `scripts/platform_probe.py` runs on the operator's Mac (macOS 26.6.2, Apple M3) cover the
  mechanisms; the spare-account run with fixture engines remains.
- **Install-to-first-task acceptance:** verify a clean-machine install, explicit OS/browser trust,
  one-engine readiness, project Setup/guards, first conversation, isolated task, Stop/resume and
  checked PR delivery. Cover failed update/rollback and uninstall retention on each claimed platform;
  keep untested rows and actionable failures explicit in the onboarding/compatibility documentation.
- **Trusted first connection:** trusting Altitude's own CA once per device is the supported path to a
  warning-free connection. Generated CAs are limited to private addresses and names, startup reissues
  the server certificate for a changed bind address, and the installer and `alt doctor` print the CA
  fingerprint and per-device steps. Tests use disposable identities and in-process handshakes; trust
  on real Linux Chrome/Firefox, Mac, iPhone and Android browsers remains unobserved until recorded.

Live-provider testing retains its separate standing deferral. Project Setup/guards and
composer/drafts retain their existing ownership; future integration consumes those interfaces.
Public-release/history-audit work and P1 backup/continuity remain separately sequenced above.

### Native macOS runtime

The operator's September 25, 2026 direction makes macOS a priority target alongside Linux: every
platform change ships for both behind `altitude/platform.py` or names its macOS gap (see
[AGENTS.md](../AGENTS.md#seams)). Task `macos-support-native-runtime-behind-the` owns #225's audit,
the launchd and confinement proposal held for operator review, and the runtime increments. #527 moved
host mechanisms into the seam; the macOS host behind it is implemented. Two operator decisions of
September 27, 2026 shape it: machine-grant commands run outside Seatbelt, because launchd refuses
service control to every sandboxed process, and terminal shells run as launchd jobs, because macOS
hides the environment of its own binaries from the mark that finds what a terminal started. The
operator runs each increment's native validation on their own Apple silicon Mac; that
evidence is recorded before README, setup or this roadmap call macOS supported. No paid runner is
authorized. Native probes make no provider calls or model downloads.

## Early-user onboarding and public release

The next milestone is making the repository public. The
README-first milestone of issue #219 provides positioning, a fictional [walkthrough](WALKTHROUGH.md),
[setup](SETUP.md), [contributor guidance](../CONTRIBUTING.md) and a feedback entry point, and the
issue is linked from the [README](../README.md#documentation). The repository-hygiene milestone
adds the [security policy](../SECURITY.md) with private vulnerability reporting and scope, a
contribution process for fork contributors, bug and idea templates, and wording that stays true
once the repository is public.

Remaining before public release:

- The license is decided and in place: `FSL-1.1-ALv2` in [LICENSE](../LICENSE), the
  [third-party notices](../THIRD_PARTY_NOTICES.md) ship in the archive, and contributors accept
  the [CLA](../CLA.md). A CLA-checking bot is an optional repository setting the maintainer may add.
- Maintainer actions on release day, in this order: review the
  [history audit](https://github.com/mburakyucel/altitude/issues/233) result and its latest delta,
  restore GitHub Actions billing so hosted jobs start, make the self-hosted runner admit the
  public repository's owner runs, apply the repository description and topics below and flip
  visibility. Then enable private vulnerability reporting, review branch protection, add a tag
  ruleset reserving `v*` tags to the maintainer and confirm fork-workflow approval; the free plan
  offers rulesets and these settings only for public repositories. Immutable releases are on
  ([publish a release](RELEASING.md#publish-a-release)).
- Recommended description: **“Persistent project orchestration for coding agents: project direction,
  directly reachable task owners, isolated worktrees and checked PRs.”** Recommended topics:
  `ai-development`, `coding-agents`, `developer-tools`, `developer-workspace`, `git-worktrees`.
  These are recommendations, not applied settings.
- Required PR checks for a public repository, including fork PRs:
  [#469](https://github.com/mburakyucel/altitude/issues/469). Installation-neutral defaults:
  [#470](https://github.com/mburakyucel/altitude/issues/470).
- Validate the documented path on a second clean machine, including engine authentication,
  sandbox/user-service support, Git guards, first conversation and one checked task delivery.

## Engines, platforms and distribution
[Release checkpoints](RELEASING.md) use versioned source snapshots, curated
[release notes](../CHANGELOG.md), full deterministic candidate checks and documented recovery.
During active preview work, readiness is checked daily and useful fixes can release several times
per day after validation; publication is explicit and does not gate source-deployment activation.
Live-provider testing is deferred by the operator's 2026-09-08 decision. The
[coverage matrix](DEVELOPMENT.md#coverage-and-limits) keeps provider/host compatibility and other
remaining validation limits explicit; this does not establish clean-machine or public readiness.

The direction is the same project and task workflow across CLI engines and supported machines.
The current [setup](SETUP.md) supplies a private Linux x86_64 archive; Ubuntu 24.04 is its initial
target. The native macOS 15/26 Apple-silicon runtime is implemented; its validation remains as described
above. The common confinement contract still needs proof before any support claim.

| Work | Intended outcome | Current boundary |
| --- | --- | --- |
| Additional CLI engines | Integrate candidates such as **OpenCode**, preserving native tools, sessions, context and helpers. | Codex and Claude Code work today. Each added engine needs launch/resume/stop, permissions, authentication and optional usage observations implemented and verified through task delivery. |
| Engine readiness and access | Select from installed, authenticated engines; document verified model-provider, subscription and API/access configurations. | One engine suffices with Auto or an explicit pin. Local readiness distinguishes configured, unknown and tested; it makes no provider request. Launch environment and role-model settings constrain configuration inheritance. |
| [macOS runtime · #225](https://github.com/mburakyucel/altitude/issues/225) | Native OS/service integration with verified start, task execution, stop/resume, restart/adoption and shutdown. | Implemented behind the platform seam; spare-account acceptance (install, confinement, Stop, restart/adoption, update/rollback, logout/login, reboot, sleep) is pending. |
| [Installable daemon and updates · #226](https://github.com/mburakyucel/altitude/issues/226) | A packaged CLI, daemon and built web app, onboarding, per-user service, versioned updates and recoverable uninstall on supported Linux and macOS. | Linux installs with one command from a published release, uses localhost HTTPS, explicit CA trust and retained data, and offers verified in-app and CLI updates. Native Mac and clean-machine acceptance remain pending; this increment does not close the parent. |

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
