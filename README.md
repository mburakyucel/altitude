# Altitude

**You lead. Altitude orchestrates. Agents ship.**

Be the principal engineer. Set the roadmap. Own the architecture. Make the calls.

Altitude turns your Claude Code or Codex agents into an engineering team. Your project
orchestrator works through strategy with you and puts work in motion. Task owners carry it
through investigation, implementation, checks and pull requests: in parallel, each in its own
worktree, on your machine and your coding account.

**Steer from your phone, by voice.** Describe the next feature, settle a tradeoff or step into
any task. The work keeps going on your machine after you put the phone away.

**Early preview · Linux x86_64 · [Get started](#get-started)**

<img src="docs/images/project-desktop.png" alt="Desktop: discuss Atlas's architecture with L3 while three task owners work in parallel in the adjacent Work panel." width="1440">

*The actual app, with a fictional Atlas project. Set direction with L3, your project orchestrator;
follow parallel delivery alongside the conversation.*

## On your phone

**Talk to L3. Steer an owner. Make the call.** The same project, wherever you are.

<p>
  <a href="docs/images/project-phone.png"><img src="docs/images/project-phone.png" alt="Phone: speak or type to L3 in the Atlas project conversation." width="250"></a>
  <a href="docs/images/task-phone.png"><img src="docs/images/task-phone.png" alt="Phone: steer the compatibility task owner directly." width="250"></a>
  <a href="docs/images/decision-phone.png"><img src="docs/images/decision-phone.png" alt="Phone: choose a seven-day or thirty-day rollback window in Needs you." width="250"></a>
</p>

[Watch the phone walkthrough](docs/images/phone-walkthrough.webm) ·
[Explore the desktop and phone walkthrough](docs/WALKTHROUGH.md)

*Real interface, fictional data. The recording follows project chat, task steering and a decision.*

## One conversation. An engineering team behind it.

<picture>
  <source media="(max-width: 600px)" srcset="docs/images/orchestration-phone.svg">
  <img src="docs/images/orchestration.svg" alt="You set roadmap, architecture and executive decisions with L3. L3 orchestrates parallel L2 owners, each accountable for delivery and any L1 helpers. You can steer owners directly." width="1200">
</picture>

**Delegate the follow-through.** L3 works through architecture and priorities with you, briefs
task owners, tracks delivery and answers questions from decisions you've already made. Unresolved
choices reach **Needs you**; independent work keeps moving.

**Give every task an owner.** Each L2 owns an isolated worktree and delivery through a PR. It can
enlist L1 helpers and remains accountable for their work. Your project's checks and review govern
delivery; request a merge hold when you want the final say before merging.

**Take the controls at any depth.** Talk directly to an owner, inspect its live session or redirect
the work. Move between project strategy and implementation detail from the same browser, on your
computer or [your phone](docs/OPERATIONS.md#on-iphone).

One engine is enough, and each integration is
[replaceable by design](docs/ARCHITECTURE.md#engine-integration-boundary).

## Get started

Altitude runs for one person on a Linux x86_64 machine with a systemd user manager. Ubuntu 24.04
is the initial target; clean-machine and provider acceptance remain pending. You need Python 3.12+,
Git, OpenSSL, an authenticated GitHub CLI and one authenticated coding CLI. Agent work uses your
coding account's allowance and normal charges.

1. Obtain a trusted preview installer, archive and checksum from the maintainer. There is no public
   release yet.
2. Follow the [installation steps](docs/SETUP.md#install-the-application) and run `alt doctor`.
   The archive includes the CLI, daemon and web app; installation enables a per-user service and
   saves its tool PATH.
3. Follow the [certificate trust guide](docs/SETUP.md#trust-https-on-each-device), open the printed
   HTTPS URL and use [First run](docs/SETUP.md#first-run-in-the-browser) to add your project.

<details>
<summary>Ask your coding agent to help install</summary>

Paste into Claude Code or Codex on the machine that will run Altitude. This is assisted setup;
unattended installation is not yet validated.

```text
Help me install Altitude using https://github.com/mburakyucel/altitude/blob/main/docs/SETUP.md
and the instructions shipped with the selected version. Check prerequisites and my normal
engine/tool PATH first. Use only installer, archive and checksum sources I approve; verify
the checksum and stop if anything is unavailable. Ask before privileged commands or changes
to existing configuration, services or shell profiles. Keep localhost HTTPS and security and
authority safeguards. Leave authentication and certificate trust to me in my own terminal or
browser; never read or copy credentials or private keys. Stop and explain refused or failed
checks. Run alt doctor and report anything unverified. Do not discover repositories, register
projects or start tasks; I will choose my project in the app.
```

</details>

## Documentation

| Start here | Go deeper |
| --- | --- |
| [Setup](docs/SETUP.md) | [Architecture](docs/ARCHITECTURE.md) · [Session lifecycle](docs/SESSION_LIFECYCLE.md) |
| [Walkthrough](docs/WALKTHROUGH.md) | [CLI reference](docs/CLI.md) · [Operations](docs/OPERATIONS.md) |
| [Contributing](CONTRIBUTING.md) | [Development and checks](docs/DEVELOPMENT.md) |
| [Roadmap](docs/ROADMAP.md) | [Release checkpoints](docs/RELEASING.md) · [Changelog](CHANGELOG.md) |

## Feedback

[Open an issue](https://github.com/mburakyucel/altitude/issues/new/choose) with a bug, idea or
confusing part of setup. Keep examples fictional or redacted. Report vulnerabilities privately
using the [security policy](SECURITY.md).

Altitude is source-available under the [Functional Source License](LICENSE).
