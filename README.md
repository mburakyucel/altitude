# <img src="web/public/altitude-mark.svg" alt="" width="32" height="32"> Altitude

## Be the lead, not the bottleneck.

Altitude runs Claude Code and Codex like an engineering team on your computer.

- **You make the executive decisions; Altitude handles the rest.** Only the calls that need you
  reach you, wherever you are.
- **A coordinator runs your project with you** and answers the agents from what you have decided.
- **Every task gets its own agent,** from idea to production, around the clock.
- **It heals and improves itself,** so the work keeps moving and keeps getting better.

**Early preview · Linux · macOS · [Get started](#get-started)**

<img src="docs/images/opening.webp" alt="The project view on a computer, with the coordinator's plan and twenty-three tasks at work. On a phone in front of it, the one question that needs you is answered with its recommended seven days, and on the computer that task goes back to work." width="1200">

## Work at any altitude

<img src="docs/images/altitude.svg" alt="You make the executive decisions. Below you, the coordinator (L3) orchestrates the project: it plans with you, starts tasks and answers from your decisions. Each task has its own agent (L2) that takes it from idea to production, and each task agent runs its own helper agents (L1) for focused work such as reading API docs, running tests or profiling latency." width="1000">

Altitude works in levels, and you choose yours: steer the whole project with the coordinator, or step
straight into any task.

One engine is enough, and each integration is
[replaceable by design](docs/ARCHITECTURE.md#engine-integration-boundary).

## What you get

- **Self-healing.** When something breaks, it gets repaired and the work picks up where it stopped.
- **Self-improving.** Altitude learns from its own finished work and fixes what slowed it down.
- **Parallel without collisions.** Many tasks at once, each on its own copy of the code.
- **Balanced quotas.** Each task goes to the engine that has used less of its week, so both are used evenly.
- **Steer mid-flight.** Watch any agent live, redirect it in a sentence or take the terminal.
- **Nothing ships unchecked.** Independent review and your own checks stand before every merge, and
  the final say is yours whenever you want it.
- **Decide from anywhere.** Your phone alerts you when a decision needs you; answer with a tap or
  your voice.
- **Power with a fence.** Agents leave their sandbox only for a purpose you approve.

<img src="docs/images/balance.svg" alt="Two subscriptions, Claude and Codex, each with a meter for the share of its week used. New tasks arrive one at a time, each goes to the engine marked as less used, and that engine's meter fills, so both rise evenly until a new week empties them." width="760">

[See it all in the desktop and phone walkthrough](docs/WALKTHROUGH.md#see-the-features)

## Get started

Altitude runs for one person on Linux with a systemd user manager or on macOS 15 or newer; releases
are validated on Ubuntu 24.04 x86_64 and Apple silicon Macs. You need Python 3.12+, Git, OpenSSL (on
a Mac, Homebrew's `openssl@3`), an authenticated GitHub CLI 2.72 or newer ([setup](docs/SETUP.md))
and one authenticated coding CLI. Agent work uses your coding account's allowance and normal charges.

1. Install the latest release as the account that will use Altitude:

   ```sh
   curl --proto '=https' --tlsv1.2 -fsSL https://github.com/mburakyucel/altitude/releases/latest/download/install.sh | sh
   ```

   Releases are early previews: expect rough edges, and read the
   [release notes](CHANGELOG.md) for known limitations. The script checks the machine, runs
   nothing it downloads unless it matches the release's checksums and prints the next steps. The
   [installation steps](docs/SETUP.md#install-the-application) explain what it trusts and how to
   verify a release's script before running it.
2. Put `~/.local/bin` on your PATH and run `alt doctor`. Installation enables a per-user service.
3. Trust Altitude's certificate on each device with the
   [certificate guide for Linux, macOS and phones](docs/SETUP.md#trust-https-on-each-device), then
   run `alt pair` and open its address or QR code on your phone to
   [pair it](docs/SETUP.md#pair-each-device). [First run](docs/SETUP.md#first-run-in-the-browser)
   adds your project.

<details>
<summary>Ask your coding agent to help install</summary>

Paste into Claude Code or Codex on the machine that will run Altitude. This is assisted setup;
unattended installation is not yet validated.

```text
Help me install Altitude using https://github.com/mburakyucel/altitude/blob/main/docs/SETUP.md
and the instructions shipped with the selected version. Check prerequisites and my normal
engine/tool PATH first. Install only from the project's published GitHub release, as that guide
describes, and stop if a download or checksum check fails. Ask before privileged commands or changes
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
| [Setup](docs/SETUP.md) · [Run from source](docs/OPERATIONS.md#run-from-a-source-checkout) | [Architecture](docs/ARCHITECTURE.md) · [Session lifecycle](docs/SESSION_LIFECYCLE.md) |
| [Walkthrough](docs/WALKTHROUGH.md) | [CLI reference](docs/CLI.md) · [Operations](docs/OPERATIONS.md) |
| [Contributing](CONTRIBUTING.md) | [Development and checks](docs/DEVELOPMENT.md) |
| [Roadmap](docs/ROADMAP.md) | [Release checkpoints](docs/RELEASING.md) · [Changelog](CHANGELOG.md) |

## Feedback

[Open an issue](https://github.com/mburakyucel/altitude/issues/new/choose) with a bug, idea or
confusing part of setup. Keep examples fictional or redacted. Report vulnerabilities privately
using the [security policy](SECURITY.md).

Altitude is source-available under the [Functional Source License](LICENSE).
