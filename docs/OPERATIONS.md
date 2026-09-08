# Operations

This is the runtime guide for an already configured installation. New users should start with
[setup](SETUP.md); contributors should use [development and checks](DEVELOPMENT.md).
The shipped [service unit](../systemd/altitude.service) is a maintainer deployment template:
its checkout path, PATH and tunnel binding need deliberate configuration for another machine.

## Runtime and inspection

`altitude/` is a standard-library Python package. `bin/alt` is the CLI; `personas/` contains the
project coordinator (L3) and task owner (L2) instructions, `schemas/` defines delivery reports,
and `hooks/` contains managed-repository Git hooks, inbox delivery and statusline telemetry.
`web/` builds into ignored `web/dist/`, served by the same Python process as the API.
See [architecture](ARCHITECTURE.md) for authorization and engine containment.

Runtime state lives under `ALTITUDE_HOME` (default `~/.altitude`): project configuration, active
and archived tasks, conversations, monitor snapshots, and private incident evidence. Keep it
out of source control. Provider session files have their engine's own retention policy.

```sh
bin/alt --project example task report example-task
bin/alt --project example task messages example-task --last 5
bin/alt --project example task events example-task --last 5
bin/alt --project example task status example-task --brief
bin/alt queue
bin/alt --project example repo
bin/alt --project example pr 42
bin/alt --project example l3 tools --days 7
bin/alt monitor
```

These illustrative inspection commands use a registered project, task and PR; substitute yours.
Add `--json` where supported for the full record. The [CLI reference](CLI.md) describes the
inspection and task lifecycle verbs.

Project verbs are `alt project add <name> [--path PATH] [--wip N]`, `list`, `discover`,
`remove <name>`, and `set <name> --wip N --reason "…"` (or `--unset-wip --reason "…"`).
Registration stores WIP only when supplied; otherwise the project inherits the default of 8.
L3 can set its own project's cap from 1 to the effective machine cap or unset it. The machine
defaults to 80; the operator can use `alt machine set --wip N --reason "…"` (including N above 80),
`alt machine set --unset-wip --reason "…"`, and `alt machine show` to set, reset and inspect it.
Machine changes, project add and remove are operator-only. See [concurrency examples](CLI.md#concurrency-limits).
A set persists a reason-bearing request that altd applies on its next tick,
without a PR, restart, or free task slot. The first registry load removes stored legacy caps of 3
once and logs the migration; approval and engine pins are preserved, and subsequent explicit
caps of 3 persist. Lowering a cap preserves running work and project overrides; new launches wait
until both caps have room. Parallel leases declare staging scope: overlapping files do not hold dispatch.
Owners rebase before landing and keep edits in shared documents to their own sections.

L3 and the operator file requested backlog through altd with `alt issue new --title "…" -`
or `alt issue comment <number> -` (body on stdin). L3 closes an issue only when the operator
requests it, using `alt issue close <number> --reason completed|not-planned`; closure publishes
no comment and records the actor, issue, reason and URL. L3 does not clean up the backlog
autonomously. L2 cannot mutate issues. See [GitHub issues](CLI.md#github-issues) for arguments
and the rule excluding home paths and private incident evidence from published text.

## Recovering dirty main

A dirty deployment checkout blocks normal and recovery task dispatch. Use the
[dirty-checkout recovery procedure](CLI.md#dirty-checkout-recovery): select an unlaunched blocked
task, pause edits to main, and request `alt task preserve-checkout <slug> --reason "…"` as L3 or
the operator. Altd preserves staged, unstaged and untracked files on a local archive branch, records
its branch and immutable snapshot SHA in `checkout_archive` before cleanup, and requires the checkout
to pass the normal guard. The snapshot's parent retains staged-only content. Ignored files
remain in place. This needs no task slot, service restart or privileged worker.

Inspect `alt task status <slug>` for the request result, branch and SHA before sending the owner
its reconciliation instructions. The owner inspects the two archive commits and applies the net
binary diff from `<SHA>~2` to `<SHA>` inside its isolated worktree, as the CLI procedure describes.
This flattens staging intent; staged content remains inspectable in `<SHA>^`. Review the contents
against the lease and publication rules and deliver through a PR. Resume other tasks separately
once the checkout is clean. Archives remain local until explicit operator removal; Altitude never
pushes or deletes them. An interrupted request is not replayed: inspect
`archive/checkout-<request-id>` and task events first, even if no task snapshot receipt exists.
Cleanup failures, ignored obstructions and submodule/nested-repository dirt keep dispatch blocked;
retain the snapshot and fault evidence. Legacy `preserved_checkout` stash SHAs stay readable and
apply with `git stash apply --index <SHA>` in the owner's worktree; existing stashes remain untouched.
Restart notices retain unresolved faults and require observed resolution before resume.

## Service lifecycle

[Versioned releases](RELEASING.md) are validated source checkpoints. They do not select the
deployed revision or delay activation. Recovery normally uses a checked revert/fix PR followed
by the activation path below; the release guide distinguishes web-bundle restoration from source
and runtime-state recovery. Never reset the deployment checkout to a release tag as a rollback.

Ordinary development and code agents must not start, stop, mask, unmask, or restart the service.
Altitude activates merged backend and web changes itself. The regular thirty-second tick discovers
merges even while their workers run. A self-deploy fast-forward marks activation
pending for loaded backend paths (`altitude/`, `bin/`, `systemd/`) or tracked web build inputs
(`web/src/`, `web/design/tokens.css`, `web/index.html`, `web/package.json`, `web/pnpm-lock.yaml`,
`web/tsconfig.json`, `web/vite.config.ts`). Web docs and other non-build files do not trigger it.
Dispatch continues while activation is pending. Once no dispatch or resume claim, L3 turn, or report
verification is in flight, `altd` runs the guarded restart script below as a transient user unit.
A restart that has not happened ten minutes after it was requested is a system fault for L3 and
the hold lifts.

An explicitly authorized operator can restart sooner by pressing Restart on the web app's banner
(shown only at the narrow quiet point, including while workers run) or running `make restart`
from the deployed primary checkout. The command refuses another clone/worktree, a non-exact or
dirty `main`, and an in-flight dispatch,
L3 turn, or report verification. Both engines run L2 workers in independent transient user units,
so workers survive and are adopted after restart. Dispatch and
L3 turns wait only from the restart request until the replacement daemon is ready.
It installs the locked web dependencies, builds and validates a staged bundle, swaps it into the
ignored runtime `web/dist`, restarts the user-level `altitude.service`, and waits for both its API and
web page to answer from a new process. The prior bundle is restored if verification fails. There is
no separate web service and no `sudo` is required. Node 22.22.2+ (22.x) or 24.15+ (24.x) and `pnpm` are required; dependency
retrieval may be needed when the local pnpm store is cold. Refresh the browser after it succeeds.

## Voice input on iPhone

Open your configured Altitude HTTPS URL through your private network. Safari exposes the microphone only in a
secure context, so the phone must trust the local CA used by Altitude's certificate; `/ca.crt` serves
that CA when it needs to be installed. The microphone button remains a typing-only hint on plain
HTTP or an unsupported browser.

For your own installation, set `ALTITUDE_HOST`/`ALTITUDE_PORT` to its private-network endpoint,
and use `alt tls-init --ip <private-address>` to create the local CA and server certificate.
`ALTITUDE_TLS_DIR` selects the certificate directory; `ALTITUDE_TLS=0` explicitly disables TLS.
Install the CA on the phone and enable its trust in Certificate Trust Settings. Arrange the
private tunnel and any firewall rule for your chosen interface/port separately. The shipped
service template's tunnel address and checkout path are not defaults to copy to another machine.

The browser records at most ten minutes as AAC/mp4 on iOS or opus/webm where available. Altitude
converts the upload with `ffmpeg` in a temporary directory and sends the resulting 16 kHz mono WAV
path to the existing local faster-whisper socket, with the loopback Whisper bridge as fallback. Raw
audio is deleted after every success or failure and is never part of task or chat state. A recording
becomes text through **Stop** (Ctrl/⌘+M), appending to the draft for editing, or the send arrow (Enter),
appending and sending at once (queued while L3 is busy). **Cancel** (Esc) discards the recording.
An empty transcript or transcription failure sends nothing and preserves the draft.

For a manual Safari check, open each of a project conversation, a task conversation, and a project
task's **Message L2** panel; record and stop; confirm the transcript is appended to the existing
draft and nothing else appears; record again and use the arrow to transcribe and send or queue at once;
then cancel a recording and deny microphone access once and confirm the
typed draft remains usable. If Safari reports that voice needs HTTPS, use your configured secure URL and verify the local CA is
enabled under Certificate Trust Settings.
