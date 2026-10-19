# Altitude overview

This page is the orientation read before changing Altitude. It summarizes the system and indexes the
detailed documents; [architecture](ARCHITECTURE.md) remains the detailed reference. The detailed
documents are long, so read the sections a change touches, by heading, rather than whole files.

## The system in brief

Altitude runs for one operator on one machine. Each registered project has a persistent conversation
with L3, the coordinator, which discusses direction, answers task questions from the record and
dispatches L2 task owners. Each L2 owns one task in an isolated worktree (`.claude/worktrees/<slug>`,
branch `worktree-<slug>`, created from fetched `origin/main`) and delivers through its own PR. It may
delegate bounded work to engine-native L1 helpers, which Altitude does not supervise. The
[personas](../personas/) hold each role's guidance; [AGENTS.md](../AGENTS.md) holds project policy.

The daemon, altd (the `altitude/` package), owns state transitions and JSON APIs and serves the built
React app from `web/dist` over HTTPS (`127.0.0.1:8890` by default, with an installation-local CA from
`tls.py`). `access.py` admits paired browsers and the CLI's machine key. `bin/alt` is the one door for
workers and the operator: each command is checked against the task record and current attempt under
the project lock. L3 is read-only on the deployment checkout and reaches altd through a project-bound
socket.

Runtime state lives under `ALTITUDE_HOME` (`~/.altitude`): task folders (`status.json`, events,
conversation, `report.json`, `progress.md`), each project's `chat.jsonl` and regenerated `STATE.md`,
machine settings and the fault ledger. `~/.config/altitude` holds installation, TLS and access records.

Seams keep operator-, provider- and machine-specific code in one place. The engine seam is
`altitude/engines/`, `route.py` (Auto tiers, quota, pins) and `config.py`; the platform seam is
`platform.py` (systemd user units and bubblewrap on Linux; a LaunchAgent, launchd jobs and Seatbelt profiles on
macOS 15 or newer). Optional capabilities such as voice degrade to an explicit unavailable state.

Tasks move `queued → running → reported/blocked → done`, or `rejected`; planned waits defer dispatch,
and one machine cap bounds running tasks. `alt land` commits the staged index, pushes, opens the PR and
joins an ordered merge line per repository. Merging needs the hosted `check` (the full `make check`)
on the current head with current main, plus every hold and review assessment. Faults block only their
own task, file a private incident and queue one L3 message. A merged change to Altitude activates at
the next quiet point through a guarded restart. The container deployment is a rootless Podman Linux
image still under validation, run on a Linux host or, through a Podman machine, from a Mac.

## Architecture sections

[ARCHITECTURE.md](ARCHITECTURE.md):

- [Container deployment boundary](ARCHITECTURE.md#container-deployment-boundary): Mac container-browser
  lane and rootless image, then the system overview diagram of L3, L2 and the fault and block flow.
- [Responsibilities](ARCHITECTURE.md#responsibilities): L3 and L2 duties, inbox, Stop, resume claims,
  handoff, L1 helpers, L3 context and search, model and effort, source exports, installs, updates,
  TLS and pairing.
  - [Linux and macOS](ARCHITECTURE.md#linux-and-macos): `platform.py` differences in service, jobs,
    Stop, confinement, installer and updates.
  - [Project setup](ARCHITECTURE.md#project-setup): setup receipts, Setup menu and Retry, Git guard
    repair and custom hooks.
- [Engine integration boundary](ARCHITECTURE.md#engine-integration-boundary): engine modules, routing,
  Auto tiers, quota reads, sign-in rejections, tool environment and known seam gaps.
  - [Adversarial review](ARCHITECTURE.md#adversarial-review): proposal and changes reviews, reviewer
    selection, captured snapshots, findings and merge-gate assessment.
- [Task lifecycle](ARCHITECTURE.md#task-lifecycle): state machine, planned waits, machine cap, routing
  preferences, project removal and `STATE.md`.
- [Isolation and landing](ARCHITECTURE.md#isolation-and-landing): worktrees and branches, cleanup, merge
  line, CI gate, report verification, issue closure, PR adoption, approvals, grants, validation and
  confinement.
  - [Task door](ARCHITECTURE.md#task-door): how a worker's verbs, landing, hooks and driver change records
    through altd.
  - [Operator terminal](ARCHITECTURE.md#operator-terminal): per-task and per-project shells, replay,
    `run` blocks and output reads.
- [Same-installation coordinator information](ARCHITECTURE.md#same-installation-coordinator-information):
  `alt project message` between local coordinators.
- [Faults](ARCHITECTURE.md#faults): two-tier faults, incidents, CI recheck, incident issue publication,
  activation and guarded restart.
- [Interfaces and storage](ARCHITECTURE.md#interfaces-and-storage): locks and file reads, then every
  interface subsection:
  [times](ARCHITECTURE.md#times),
  [improvement review](ARCHITECTURE.md#improvement-review),
  [image input](ARCHITECTURE.md#image-input),
  [task usage](ARCHITECTURE.md#task-usage),
  [validation and release](ARCHITECTURE.md#validation-and-release),
  [repairs](ARCHITECTURE.md#repairs),
  [web delivery](ARCHITECTURE.md#web-delivery) (asset and query caching),
  [conversations and navigation](ARCHITECTURE.md#conversations-and-navigation) (pages, First run, live
  changes, outbox, file reader),
  [message delivery and voice](ARCHITECTURE.md#message-delivery-and-voice) (chat queue, Send now,
  Composer, voice capture),
  [work and attention](ARCHITECTURE.md#work-and-attention) (task cards, work panel, Needs you),
  [operator steps](ARCHITECTURE.md#operator-steps) (a project command for the operator in Needs you),
  [from model judgment to a task question](ARCHITECTURE.md#from-model-judgment-to-a-task-question)
  (questions, decisions, review cards, FYIs),
  [voice backend](ARCHITECTURE.md#voice-backend) with its
  [operator-supplied service](ARCHITECTURE.md#operator-supplied-service) and
  [host voice](ARCHITECTURE.md#host-voice),
  [design evidence](ARCHITECTURE.md#design-evidence),
  [shown files](ARCHITECTURE.md#shown-files) and
  [monitor and live sessions](ARCHITECTURE.md#monitor-and-live-sessions).

## Other documents

- [Session lifecycle](SESSION_LIFECYCLE.md): engine sessions for L3 and L2.
  [Project setup and recovery](SESSION_LIFECYCLE.md#project-setup-and-recovery),
  [repository instructions](SESSION_LIFECYCLE.md#repository-instructions),
  [native helper instructions](SESSION_LIFECYCLE.md#native-helper-instructions),
  [adversarial review](SESSION_LIFECYCLE.md#adversarial-review),
  [fresh dispatch](SESSION_LIFECYCLE.md#fresh-dispatch),
  [messages, resume, and stop](SESSION_LIFECYCLE.md#messages-resume-and-stop),
  [engine containment](SESSION_LIFECYCLE.md#engine-containment),
  [L3 sessions and provider changes](SESSION_LIFECYCLE.md#l3-sessions-and-provider-changes),
  [polling and cleanup](SESSION_LIFECYCLE.md#polling-and-cleanup),
  [live transcript](SESSION_LIFECYCLE.md#live-transcript),
  [container continuation](SESSION_LIFECYCLE.md#container-continuation),
  [operator terminal](SESSION_LIFECYCLE.md#operator-terminal),
  [task token accounting](SESSION_LIFECYCLE.md#task-token-accounting),
  [context and prompt-cache evidence](SESSION_LIFECYCLE.md#context-and-prompt-cache-evidence).
- [CLI reference](CLI.md): `alt` command contracts.
  [Times](CLI.md#times), [inspection](CLI.md#inspection), [project heads-ups](CLI.md#project-heads-ups),
  [coordinator information messages](CLI.md#coordinator-information-messages),
  [superseded PR closure](CLI.md#superseded-pr-closure),
  [improvement suggestions](CLI.md#improvement-suggestions), [GitHub issues](CLI.md#github-issues)
  (including [incremental delivery](CLI.md#incremental-issue-delivery)),
  [task file lists](CLI.md#task-file-lists), [dirty-checkout recovery](CLI.md#dirty-checkout-recovery),
  [device pairing](CLI.md#device-pairing), [project lifecycle](CLI.md#project-lifecycle),
  [task lifecycle](CLI.md#task-lifecycle) (task verbs, `alt land`, reviews, `--show`, grants, validation).
- [Development and checks](DEVELOPMENT.md): the suite, harnesses and evidence.
  [Local checks](DEVELOPMENT.md#local-checks),
  [noninteractive toolchain](DEVELOPMENT.md#noninteractive-toolchain),
  [macOS private places](DEVELOPMENT.md#macos-private-places),
  [browser walkthroughs](DEVELOPMENT.md#browser-walkthroughs),
  [fictional browser validation](DEVELOPMENT.md#fictional-browser-validation),
  [validation runner](DEVELOPMENT.md#validation-runner),
  [validation captures](DEVELOPMENT.md#validation-captures),
  [validation environments](DEVELOPMENT.md#validation-environments),
  [device evidence](DEVELOPMENT.md#device-evidence),
  [coverage and limits](DEVELOPMENT.md#coverage-and-limits),
  [mutation run](DEVELOPMENT.md#mutation-run),
  [improvement review eval](DEVELOPMENT.md#improvement-review-eval),
  [installation lifecycle acceptance](DEVELOPMENT.md#installation-lifecycle-acceptance),
  [CI and candidate identity](DEVELOPMENT.md#ci-and-candidate-identity),
  [runtime evidence](DEVELOPMENT.md#runtime-evidence), [UI development](DEVELOPMENT.md#ui-development).
- [Operations](OPERATIONS.md): running a configured installation.
  [Runtime and inspection](OPERATIONS.md#runtime-and-inspection),
  [back up runtime state](OPERATIONS.md#back-up-runtime-state),
  [devices and lockout recovery](OPERATIONS.md#devices-and-lockout-recovery),
  [engine sign-in recovery](OPERATIONS.md#engine-sign-in-recovery),
  [decision alerts on a device](OPERATIONS.md#decision-alerts-on-a-device),
  [refused decision alerts](OPERATIONS.md#refused-decision-alerts),
  [remove a project](OPERATIONS.md#remove-a-project),
  [worktree and source export retention](OPERATIONS.md#worktree-and-source-export-retention),
  [incident publication](OPERATIONS.md#incident-publication),
  [recovering dirty main](OPERATIONS.md#recovering-dirty-main),
  [repairs](OPERATIONS.md#repairs),
  [configuration](OPERATIONS.md#configuration),
  [installed application lifecycle](OPERATIONS.md#installed-application-lifecycle),
  [run from a source checkout](OPERATIONS.md#run-from-a-source-checkout),
  [service lifecycle](OPERATIONS.md#service-lifecycle),
  [macOS privacy prompts](OPERATIONS.md#macos-privacy-prompts),
  [validation runs](OPERATIONS.md#validation-runs), [voice input](OPERATIONS.md#voice-input).
- [Setup](SETUP.md): installation to first conversation.
  [Prerequisites](SETUP.md#prerequisites), [install the application](SETUP.md#install-the-application),
  [trust HTTPS on each device](SETUP.md#trust-https-on-each-device),
  [pair each device](SETUP.md#pair-each-device),
  [first run in the browser](SETUP.md#first-run-in-the-browser),
  [your speech service](SETUP.md#your-speech-service),
  [register a project and start a conversation](SETUP.md#register-a-project-and-start-a-conversation),
  [when something does not work](SETUP.md#when-something-does-not-work).
- [Containers](CONTAINERS.md): the rootless Podman deployment candidate.
  [Boundary](CONTAINERS.md#boundary), [build and start](CONTAINERS.md#build-and-start--host-terminal),
  [first use](CONTAINERS.md#first-use), [lifecycle and recovery](CONTAINERS.md#lifecycle-and-recovery),
  [evidence](CONTAINERS.md#evidence).
- [Release checkpoints](RELEASING.md): [cadence and versions](RELEASING.md#cadence-and-versions),
  [candidate and release gates](RELEASING.md#candidate-and-release-gates),
  [pre-publication history audit](RELEASING.md#pre-publication-history-audit),
  [publish a release](RELEASING.md#publish-a-release),
  [build the release files](RELEASING.md#build-the-release-files), [recovery](RELEASING.md#recovery).
- [Roadmap](ROADMAP.md): open direction and gaps.
  [Container deployment](ROADMAP.md#container-deployment),
  [friends-and-family readiness priorities](ROADMAP.md#friends-and-family-readiness-priorities),
  [early-user onboarding and public release](ROADMAP.md#early-user-onboarding-and-public-release),
  [engines, platforms and distribution](ROADMAP.md#engines-platforms-and-distribution),
  [pending command-surface decision](ROADMAP.md#pending-command-surface-decision),
  [current product work](ROADMAP.md#current-product-work).
- [Walkthrough](WALKTHROUGH.md): an illustrated fictional project from direction to delivery.
  [Who carries the work](WALKTHROUGH.md#who-carries-the-work),
  [keep the project direction in one conversation](WALKTHROUGH.md#keep-the-project-direction-in-one-conversation),
  [steer a task without going through L3](WALKTHROUGH.md#steer-a-task-without-going-through-l3),
  [let L3 answer what the project already knows](WALKTHROUGH.md#let-l3-answer-what-the-project-already-knows),
  [bring delivery back to the project](WALKTHROUGH.md#bring-delivery-back-to-the-project),
  [see the features](WALKTHROUGH.md#see-the-features),
  [capture source and maintenance](WALKTHROUGH.md#capture-source-and-maintenance).
- [Wireframe spec](../design/wireframes/SPEC.md): the approved UI design and its standing tenet.
