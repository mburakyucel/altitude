# Altitude

Altitude is an AI development workspace for directing software projects with coding agents.
You focus on architecture, priorities and tradeoffs. Agents take responsibility for the work
from investigation through implementation, checks and pull requests.

Each project has an ongoing conversation with a coordinator. It assigns tasks, answers questions
from recorded decisions and brings unresolved choices back to you. You can get into the details
at any time: talk directly to a task owner, inspect its live session or redirect its work.

Work from your computer or phone. Dictate what you want to change, answer questions and follow
progress in the browser while agents run on your machine.
[Set up phone access and voice](docs/OPERATIONS.md#on-iphone) with private HTTPS and a supported browser.

Codex and Claude Code are supported today; one is enough. Their integrations are
[replaceable by design](docs/ARCHITECTURE.md#engine-integration-boundary).

**Early preview · Linux x86_64 · [Get started](#get-started)**

<picture>
  <source media="(max-width: 600px)" srcset="docs/images/project-phone.png">
  <img src="docs/images/project-desktop.png" alt="Atlas project conversation: agreed migration constraints alongside three active tasks." width="1440">
</picture>

*The actual app with fictional data. L3 is the project coordinator; L2 agents own tasks.
[Explore the walkthrough](docs/WALKTHROUGH.md).*

## How it works

Suppose you are changing a search service's index format. Agree the API contract and rollout
constraints with the coordinator, then ask it to assign the independent work: client compatibility,
a resumable backfill and performance checks. Each task gets an owner and an isolated Git worktree;
owners can delegate bounded work to helpers.

Open the compatibility task to say, “Keep pagination tokens valid across the cutover.” Your message
goes directly to its owner. The coordinator answers a retry question from the agreed contract;
how long to keep the old index needs your cost and rollback judgment, so it comes to **Needs you**.
Other tasks can continue.

Owners deliver through pull requests with the project's checks and review. Ask for a merge hold
when you want to review before merging; otherwise owners can merge when those requirements are met.
Results inform the next discussion in the project conversation.

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
