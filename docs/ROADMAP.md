# Roadmap

## Container deployment

Linux-first rootless container support retains the full Linux/Mac/onboarding objective. The actual
native task/coordinator sandbox, launcher lifecycle/recovery and private backup/restore gates pass
on the recorded Linux tuple. Actual-daemon phone/desktop onboarding, task Stop/resume and queued
continuation after replacement pass with fictional engines. Real Mac ARM64 evidence remains open.
The [candidate build and bootstrap gate](CONTAINERS.md) are under validation. Containerized Linux does
not establish native macOS, Xcode or iOS build support. No container-security advantage is assumed.
The candidate implements host-side continuation after replacement, preserving ordinary restart
admission and task holds. Real provider/authentication, physical-device routing/trust, maximum-size
backup throughput, final full-scope review and required delivery checks remain explicit gaps.

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

Automated pre-merge candidate verification is the main priority (operator direction of October 7,
2026): owners verify their own committed candidates before merging and iterate without the operator
running validation or deploying each experiment. Validation work favors reusable automated entry
points that owners run against their candidates over steps the operator repeats by hand; each
[validation environment](DEVELOPMENT.md#validation-environments) names what it does not establish,
and remaining manual steps name their reason. On the Mac, [validation runs](DEVELOPMENT.md#macos-validation-runs)
cover candidate suites and fixture journeys, and the [installation lane](DEVELOPMENT.md#macos-installation-lane)
covers installation, update detection, update, rollback and uninstall; native launchd jobs and worker
confinement and browsers with their own sandbox still have no automated Mac lane.

### Portable installation: delivered and remaining

PR #356 delivers the Linux CLI, daemon and
built UI archive, localhost HTTPS with explicit certificate trust, prerequisite/readiness checks,
recoverable updates and uninstall with retained user data. Independent review and the exact-candidate
suite pass: 1,342 Python tests, 343 web tests, typecheck/build and 316 phone/desktop browser cases;
one optional host probe is skipped. Deployment is verified. These checks do not prove a fresh-machine
installation, native Mac behavior or engine authentication.

The manually dispatched [Linux installation lifecycle harness](DEVELOPMENT.md#installation-lifecycle-acceptance)
adds packaged installation, real per-user service/HTTPS checks, update, failed activation recovery and
uninstall retention on disposable Ubuntu 24.04 machines; the local VM run also restarts the machine and
checks that the service starts again unattended, and runs the built `install.sh` through its public
command against a release server inside the guest. Its hosted workflow has not executed; the
[local VM run](DEVELOPMENT.md#local-vm-run) executes the same harness on the development host, and
acceptance for a release needs a recorded run for its source commit. The synthetic version pair uses
one source commit, so it supplies no cross-release migration evidence. Runs are independent of
other owners' delivery and the required PR gate. This is partial #226 acceptance; the parent stays
open. The [macOS installation lane](DEVELOPMENT.md#macos-installation-lane) runs the same lifecycle on
the Mac under a throwaway home. Minimal OS installation,
login/logout, browser/device CA trust, download from GitHub's published release, native confinement and
live-provider compatibility remain outside this harness's evidence.

From `v0.1.0` a release also installs on a Mac, which stays experimental until the native evidence
below is recorded. The following remaining acceptance stays under #225/#226/#219/#350; those issues
stay open:

- **Native macOS runtime and installation:** the common engine/authority contract runs on a per-user
  LaunchAgent, with each job its own launchd job (coalition Stop, a supervisor-held time limit),
  Altitude's Seatbelt profile around Claude, Codex's own sandbox, and the installer's macOS branch.
  It is on `main` (#570), Altitude runs from source on the operator's Mac, and `v0.1.0` installs there
  with one command (#551). macOS 15 and 26 on Apple silicon remain experimental targets, not
  supported platforms, until the evidence below is recorded.
  Running before any login (boot mode) is a separate, later increment.
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

macOS is a target alongside Linux: every change ships for both behind `altitude/platform.py`.
Confirmation requires recorded evidence from a run on each platform; Linux delivery leaves macOS
confirmation open until that evidence exists. Owners run their candidate's checks on the Mac with
[`alt task validate`](DEVELOPMENT.md#macos-validation-runs). For what those runs do not establish,
owners name the missing confirmation in their PR and report and send L3 a row for #643
(containers), #225 (native runtime), or #551 (installation); L3 maintains those rows. Linux merge
checks and holds stay unchanged (see [AGENTS.md](../AGENTS.md#seams)).
#527 moved host mechanisms into the seam, and #570 put the
macOS host behind it on `main`; Altitude runs from source on the operator's Mac. The
[installation lane](DEVELOPMENT.md#macos-installation-lane) checks install and `alt doctor`, update
detection, CLI and app update, failed-update rollback and uninstall keeping data (#551). The native
lifecycle checks still pending are adoption across an update with running tasks, logout/login,
reboot then login and sleep past a deadline, on a spare account. The [macOS VM run](DEVELOPMENT.md#macos-vm-run) repeats that lifecycle in fresh,
offline macOS guests on that Mac, with the public command's refusals and the service starting again at
the automatic login after a restart; a physical second Mac, browser/device certificate trust and a
download from GitHub remain. Two operator decisions of
September 28, 2026 shape it: operator-grant commands run unsandboxed, as on Linux, because launchd
refuses service control to every sandboxed process, and each terminal shell runs as its own launchd
job, because macOS hides the environment of its own binaries from the mark that finds what a terminal
started. Native validation runs on the operator's Apple silicon Mac, as tasks on its Altitude
instance; that evidence is recorded before README, setup or this roadmap call macOS supported. No paid runner is
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
The current [setup](SETUP.md) supplies one release archive for Linux x86_64, with Ubuntu 24.04 as its
initial target, and experimentally for Apple silicon Macs. The native macOS 15/26 Apple-silicon runtime is implemented; its validation remains as described
above. The common confinement contract still needs proof before any support claim.

| Work | Intended outcome | Current boundary |
| --- | --- | --- |
| Additional CLI engines | Integrate candidates such as **OpenCode**, preserving native tools, sessions, context and helpers. | Codex and Claude Code work today. Each added engine needs launch/resume/stop, permissions, authentication and optional usage observations implemented and verified through task delivery. |
| Engine readiness and access | Select from installed, authenticated engines; document verified model-provider, subscription and API/access configurations. | One engine suffices with Auto or an explicit pin. Local readiness distinguishes configured, unknown and tested; it makes no provider request. Launch environment and role-model settings constrain configuration inheritance. |
| [macOS runtime · #225](https://github.com/mburakyucel/altitude/issues/225) | Native OS/service integration with verified start, task execution, stop/resume, restart/adoption and shutdown. | Implemented behind the platform seam; spare-account acceptance (install, confinement, Stop, restart/adoption, update/rollback, logout/login, reboot, sleep) is pending. |
| [Installable daemon and updates · #226](https://github.com/mburakyucel/altitude/issues/226) | A packaged CLI, daemon and built web app, onboarding, per-user service, versioned updates and recoverable uninstall on supported Linux and macOS. | Linux and macOS (from `v0.1.0`) install with one command from a published release, use localhost HTTPS, explicit CA trust and retained data, and offer verified in-app and CLI updates; fresh Linux and macOS virtual machines and a throwaway home on the Mac prove the lifecycle. A physical clean machine remains pending. |

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
