# Security policy

Altitude runs coding agents with write access to your repositories, lands their changes through
pull requests, and serves a web UI that acts with the operator's authority. Reports that let
someone cross those boundaries are welcome.

## Report privately

Use GitHub's private vulnerability reporting for this repository:
[Report a vulnerability](https://github.com/mburakyucel/altitude/security/advisories/new). The
report reaches only the maintainer, who enables the form in the repository's security settings.
If the form is unavailable, open an ordinary issue saying only that you have a security report and
how the maintainer can reach you; put no details in the issue.

Include the commit or installed version, the setup you used, the steps that reproduce the
problem and what an attacker gains. Use fictional projects and sanitized output; do not attach
credentials, tokens, live conversations or another person's data. Do not test against
installations you do not own.

The maintainer aims to acknowledge a report within seven days, agree a disclosure timeline with
you and credit you in the fix unless you prefer otherwise. There is no bug bounty. Only the
current `main` branch and the latest release receive fixes.

## Scope

Altitude assumes one operator on one machine. The web app opens only in browsers the operator paired
with a one-time code from `alt pair`, and the `alt` CLI proves it runs as the operator by reading a
private key file. Processes running as the operator's account, including Altitude's own workers,
share that account's authority. Each task's worker is confined to its own worktree and task folder,
and every code change goes through a checked PR.

In scope:

- Escaping worker confinement: writing outside a task's worktree and task folder, reaching another
  task's session or state, or affecting the operator's services.
- Reaching the operator's terminal from a worker or from another site open in the operator's
  browser, or recovering what was typed or shown in it.
- Bypassing delivery authority: merging around a hold, review or required check, forging an
  operator approval or acting with a role's authority without its credential.
- Using the web server or its API without a paired browser or the machine key: guessing or reusing a
  pairing code, keeping access after a device is removed, or making a paired browser act for
  another site.
- Installer, update and archive verification: accepting a tampered archive, unsafe extraction or
  unintended file permissions.
- Git guard weaknesses that allow protected-branch moves or deletions the guards claim to block.
- Prompt injection through repository, issue or PR content that leads an agent past Altitude's
  own boundaries above. The general susceptibility of models to instructions in content is not
  itself a vulnerability in Altitude.

Out of scope:

- Vulnerabilities in the coding-agent CLIs, GitHub, Python, Node or third-party packages; report
  those upstream, and tell the maintainer when Altitude's use of them makes the problem reachable.
- Actions available to someone who already controls the operator's machine account.
- Exposure created by deliberately binding the server to an address the setup documentation
  warns against, or by disabling documented sandbox or certificate checks.
- Resource exhaustion by the operator's own agents on the operator's own machine.
