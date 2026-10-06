# Operations

The [container lifecycle](CONTAINERS.md#lifecycle-and-recovery) uses host image replacement instead
of native application updates or source activation. Browser terminal and host voice are explicitly
unavailable. The recorded Ubuntu fixture passes launcher lifecycle, private backup/recovery and
phone/desktop onboarding, registered-project backup and matching-image recovery. The version-swap
fixture uses one application schema; arbitrary migrations, Mac and final delivery acceptance remain
pending. Real-provider and physical-device compatibility are unverified.
The host launcher exposes `status`, `pause` and `continue`. Replacement pauses new provider work;
Continue releases eligible queued work without clearing task holds. Pause leaves Stop and already
admitted work available. Stop the controller for a consistent backup; status alone cannot certify one.

This is the runtime guide for an already configured installation. New users should start with
[setup](SETUP.md); contributors should use [development and checks](DEVELOPMENT.md).
The shipped [service unit](../systemd/altitude.service) is a maintainer deployment template:
its checkout path, PATH and tunnel binding need deliberate configuration for another machine.
On a Mac, `make install-service` writes the same settings as a LaunchAgent for the checkout
(`scripts/source_launch_agent.py`), with `ALTITUDE_HOST` and the PATH it runs with (the address defaults to
`127.0.0.1`) and, when the checkout is not on `main`, that branch as `ALTITUDE_SOURCE_BRANCH`. Its PATH needs `pnpm` (`corepack enable pnpm`), since a self-restart
rebuilds the web app.
Private archives generate their own user service; they do not install that source template.

## Runtime and inspection

`altitude/` is a standard-library Python package. `bin/alt` is the CLI; `personas/` contains the
project coordinator (L3) and task owner (L2) instructions, `schemas/` defines delivery reports,
and `hooks/` contains managed-repository Git hooks, and inbox delivery.
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

Project verbs include `alt project add <name> [--path PATH]`, `list`, `discover`,
`remove <name>` and `set <name>` for routing preferences and the L2 provider priority. All projects share one machine cap,
defaulting to 80; the operator can use `alt machine set --wip N --reason '…'` (including N above 80),
`alt machine set --unset-wip --reason '…'`, and `alt machine show` to set, reset and inspect it.
Machine changes, project add and remove are operator-only. See [concurrency examples](CLI.md#concurrency-limits).
A set persists a reason-bearing request that altd applies on its next tick,
without a PR, restart, or free task slot. Stored project caps impose no limit. Lowering the machine
cap preserves running work; launches wait until it has room. Blocked tasks consume no capacity, and
eligible resumes across all projects take available slots before fresh work. Planned file lists
guide coordination; overlapping files do not hold dispatch.
Owners rebase before landing and keep edits in shared documents to their own sections.

Inspect routing, usage windows and observed sessions in **Monitor**. Missing or stale readings
remain explicit. **Settings → This project**, opened from the project's three dots, sets independent
model and effort defaults for L3 and L2 on each engine. These settings preserve running task attempts;
L3 defaults apply on its next turn, while L2 defaults apply on fresh dispatch. Unset effort is native
except High for a Codex L2; **Native** explicitly requests no effort override. Higher effort can use more
time and tokens. The CLI reference covers [routing preferences](CLI.md#automatic-routing-preferences),
[model defaults](CLI.md#default-models) and [effort selection](CLI.md#task-reasoning-effort),
including the same settings from the terminal.

A step only the operator can take arrives in chat as a command block. **Open in terminal** types it at
the prompt of that task's or project's terminal without pressing Enter; read it, then press Enter, edit
it or clear it. A plain code block (a command for another machine) has Copy only. If a program is
running in the terminal, nothing is typed and a notice offers Copy instead. On Linux, `sudo` in the
terminal asks for your password there, as in a desktop terminal. If the terminal cannot start, it shows
why and closes; a desktop or SSH terminal on this computer runs the same command.

A task's owner can read its task terminal's output, and the task terminal says so. When you press
Enter on a step its owner handed you, Altitude tells the owner once the command looks finished (or the
terminal ended first), and the owner reads the result to check that it did and continues; no reply is needed unless the terminal
says Altitude couldn't tell the owner. The owner reads what the terminal printed, never what
you typed at a hidden password prompt, and cannot type into or close the terminal. Output stays readable
until a new terminal opens for the task, the task finishes or Altitude restarts; if a restart lost it,
tell the owner what the command printed. Use a project terminal or a desktop terminal for work the owner
should not see: what the owner reads also reaches its provider.

L3 and the operator file requested backlog through altd with `alt issue new --title '…' -`
or `alt issue comment <number> -` (body on stdin). Authorized complete deliveries use reviewed
PR closing links; when an already merged delivery lacks its link, L3 verifies the issue's full
scope and uses `alt issue close <number> --reason completed` without another routine request.
Other closures require the operator's request; `not-planned` records work the operator decides
not to pursue. Closure publishes no comment and records the actor, issue, reason and URL.
L3 does not select or clean up unrelated backlog autonomously. L2 routes direct closure evidence
through its task reply and report follow-ups and cannot mutate issues directly. See
[GitHub issues](CLI.md#github-issues) for arguments and the rule excluding home paths and private
incident evidence from published text.

## Devices and lockout recovery

**Settings → Devices** lists every paired browser with the day it paired and was last used. **Remove**
asks once, then signs that browser out at once: its next request shows the pairing screen and its
open streams end. Removing the browser you are using returns it to the pairing screen too.

Nothing on the network can unlock Altitude. If no paired browser is at hand, open a shell on the
computer running Altitude, locally or over SSH, and run `alt pair`; it writes a fresh code directly
to `~/.config/altitude/access/`, so it works whatever address Altitude listens on and whether or not
a browser is open. To sign out every browser, remove `~/.config/altitude/access/devices.json`. Back up
the folder only to a place as private as `~/.config`.

## Refused decision alerts

A push service that refuses Altitude's alert keeps its device subscribed, and altd tries that device
again on each tick while a decision waits, even one another device already took, so a fix on either
side takes effect without another step. altd records the refusal and the short reason code the
service gave in `~/.altitude/push.json`, logs it once as `push to <host> refused with
<status> <reason>` and logs `push to <host> delivered again` once a push gets through. Until then,
the line under the alert switch in Needs you names that push service and its reason, and that device
alerts only while Altitude is open.

- `403 BadJwtToken`: the service rejects the signed sender token, most often its contact address.
  Set `ALTITUDE_PUSH_CONTACT` to a `mailto:` address with a real domain (see [configuration](SETUP.md#configuration-and-limits)).
- `403 VapidPkHashMismatch` or another refusal of the key: turn alerts off and on again on that
  device, which subscribes it with the key Altitude signs with.
- `404` or `410`: the subscription has ended; altd drops it and the device subscribes again when
  alerts are next turned on there.

## Remove a project

In the project's **More actions** menu, **Remove project** detaches its coordinator and stops
Altitude management. Finish or reject unfinished tasks, then wait for their workers and any
coordinator turn. The repository, remaining worktrees, history, provider sessions and queued
messages stay on disk. Add the same folder and project name again to restore the history and
resume waiting messages. Removing the last project opens First run; otherwise a remaining
project is selected. `alt project remove <name>` uses the same checks. See
[project lifecycle](CLI.md#project-lifecycle).

## Incident publication

Altitude records its own failures as sanitized incidents under the runtime directory. They stay
there until **Settings → Incident reports** (also a First run step) turns publishing on for a
GitHub `owner/repository`, Altitude's own filled in or a fork you name, or
`ALTITUDE_UPSTREAM_ISSUE_REPOSITORY` in altd's environment names one for a non-interactive
install. Every incident then becomes one sanitized issue there, and `alt incident list` shows the
link or the pending reason. The saved setting outranks the environment, including when it turns
publishing off. Altitude's repository is public, so its issues are public; a fork you manage keeps
them under your control. An installed application keeps the environment value in its saved settings, so `alt install` from a shell where
it is exported carries it into the service; a source deployment sets it in the user service unit's
`Environment=`, as the checked-in `systemd/altitude.service` does for the maintainer's development
installation; copy the unit and run `systemctl --user daemon-reload` so the next restart applies
it. See [incident issues](CLI.md#incident-issues).

## Recovering dirty main

A dirty deployment checkout leaves isolated dispatch and resume available; deployment failures
remain visible separately. To reconcile its edits, use the
[dirty-checkout recovery procedure](CLI.md#dirty-checkout-recovery): select an unlaunched blocked
task, pause edits to main, and request `alt task preserve-checkout <slug> --reason '…'` as L3 or
the operator. Altd preserves staged, unstaged and untracked files on a local archive branch, records
its branch and immutable snapshot SHA in `checkout_archive` before cleanup, and requires the checkout
to pass the normal guard. The snapshot's parent retains staged-only content. Ignored files
remain in place. This needs no task slot, service restart or privileged worker.

Inspect `alt task status <slug>` for the request result, branch and SHA before sending the owner
its reconciliation instructions. The owner inspects the two archive commits and applies the net
binary diff from `<SHA>~2` to `<SHA>` inside its isolated worktree, as the CLI procedure describes.
This flattens staging intent; staged content remains inspectable in `<SHA>^`. Review the contents
against the authorized objective and publication rules and deliver through a PR. Resume blocked tasks
explicitly; isolated work does not require checkout cleanup. Archives remain local until explicit
operator removal; Altitude never
pushes or deletes them. An interrupted request is not replayed: inspect
`archive/checkout-<request-id>` and task events first, even if no task snapshot receipt exists.
Cleanup failures, ignored obstructions and submodule/nested-repository dirt keep preservation blocked;
retain the snapshot and fault evidence. Legacy `preserved_checkout` stash SHAs stay readable and
apply with `git stash apply --index <SHA>` in the owner's worktree; existing stashes remain untouched.
Restart notices retain unresolved faults and require observed resolution before resume.

## Installed application lifecycle

These are operator lifecycle actions for a packaged installation, not commands for ordinary code
agents or a source deployment:

```sh
alt doctor
alt service status
alt service logs
alt update
```

The daemon makes the same lookup at startup and every twelve hours and records what it finds.
`alt doctor`, Settings › This machine and a notice in the app show a newer stable release, and an
`alt` command you run in a terminal prints one line about it at most once a day. The app's
**Update** button, after a confirm, runs `alt update --version` for exactly the version it shows,
in a job of its own so the update survives the restart. **Check for new versions** in Settings
turns the lookup off. Nothing updates on its own.

`alt update` asks GitHub for the newest stable release of the repository the installed release
was built from (one anonymous request to `api.github.com`) and does nothing when the installed
version is current or newer. Otherwise it downloads that release's archive and its `.sha256`
from the release, then installs it exactly as an archive update does. `alt update --version
v0.1.1` installs a named newer published release, including a `-rc.N` candidate, without the
lookup; it refuses an older version, which `alt recover` restores. Every download hop stays on
HTTPS, the verified archive must be the requested version, and the version is compared with the
installed one again under the installation lock, so a concurrent update cannot cause a downgrade.
`alt update --archive altitude-v0.1.1.tar.gz --sha256 '<release SHA-256>'` installs an archive
you already have, with no network access. Installation checks the archive and manifest before
selecting an immutable version. Activation waits for dispatch,
resume, L3 and report verification to be quiet, then verifies the selected version/commit, native
service PID, HTTPS health and built UI. An already stopped installation stays stopped on update.
Use `alt service start` or `alt service stop` only when deliberately changing its lifecycle;
independent task workers are not stopped with the daemon. On a Mac the service is the LaunchAgent
`dev.altitude.altd`: `alt service stop` boots it out until the next login or `alt service start`,
and `alt service logs` reads `~/Library/Logs/altitude/altd.log`; an in-app update logs to
`altitude-update-<version>.log` beside it. Projects continue using ordinary checked
PR delivery; a managed source clone does not update the installed application. Project Git guards
point at installation-owned `hooks` launchers that run the `current` version, and every dispatch
and resume repairs and rechecks them, so registered projects dispatch on the updated version
without reinstalling guards.
Lifecycle commands reject shell overrides that disagree with the owned service's saved runtime,
binding, TLS or project-root settings. Remove the named overrides before retrying.

Failed activation restores the previous version and service. If interrupted or recovery remains
incomplete, use `alt recover`; if the launcher is unavailable, use the same trusted installer:
`python3.12 install.py --recover` (with `--prefix` when a custom prefix was selected). A recovery
receipt remains until restoration succeeds and prevents new dispatch/coordinator work while activation is unverified. Changed service ownership or unconfirmed stop refuses
further mutation. Retain the failing archive/version and sanitized error for diagnosis.

`alt uninstall` stops/removes only the owned service and launcher. It refuses unfinished tasks
that still own worker inputs. Registered projects keep installed hook resources; otherwise
application versions are removed. Configuration, certificate trust, histories, provider sessions
and project worktrees remain. Updates do not prune prior versions. Removing retained data or
device trust is a separate deliberate action, not part of uninstall or recovery. Code restoration
does not roll back data; incompatible state needs the [release recovery procedure](RELEASING.md#recovery).

## Service lifecycle

[Versioned releases](RELEASING.md) are validated checkpoints. For source deployments, they do not select the
deployed revision or delay activation. Recovery normally uses a checked revert/fix PR followed
by the activation path below; the release guide distinguishes web-bundle restoration from source
and runtime-state recovery. Never reset the deployment checkout to a release tag as a rollback.

Ordinary development and code agents must not start, stop, mask, unmask, or restart the service.
Source-deployed Altitude activates merged backend and web changes itself. The regular thirty-second tick discovers
merges even while their workers run. A failed fetch of `origin/main` is logged and retried on the next
tick, and becomes a `self-deploy` system fault only after five minutes without a successful fetch.
Other self-deploy refusals fault immediately. A self-deploy fast-forward marks activation
pending for loaded backend paths (`altitude/`, `bin/`, `systemd/`) or tracked web build inputs
(`web/src/`, `web/design/tokens.css`, `web/index.html`, `web/package.json`, `web/pnpm-lock.yaml`,
`web/tsconfig.json`, `web/vite.config.ts`). Web docs and other non-build files do not trigger it.
Dispatch continues while activation is pending. Once no dispatch or resume claim, L3 turn,
adversarial review, validation run, or report verification is in flight, `altd` runs the guarded
restart script below as a transient user unit. Validation holds the quiet point through its
one-hour execution limit, evidence recording and cleanup. New validation runs wait once restart is requested.
A failing restart unit files a system fault naming its reason for L3 immediately. While its
request is still pending it also records the reason as `error` in `monitor/restart-pending.json`
and marks it `failed`, and the hold lifts. A restart that has not happened
ten minutes after it was requested is the same fault, for a unit that died without reporting. The
transient unit is collected once it exits, so `systemctl --user status` reports it not-found;
`journalctl --user -u <unit>` keeps its output. A failed activation retries automatically after the
next merge that needs activation, or sooner from the Restart button.

An explicitly authorized operator can restart sooner by pressing Restart on the web app's banner
(shown only at the narrow quiet point, including while workers run) or running `make restart`
from the deployed primary checkout. The command refuses another clone/worktree, a non-exact or
dirty `main`, and an in-flight dispatch,
L3 turn, adversarial review, validation run, or report verification. Both engines run L2 workers in independent transient user units,
so workers survive and are adopted after restart. Dispatch and
L3 turns wait only from the restart request until the replacement daemon is ready.
It installs the locked web dependencies, builds and validates a staged bundle, swaps it into the
ignored runtime `web/dist`, restarts the user-level `altitude.service`, and waits for both its API and
web page to answer from a new process. The prior bundle is restored if verification fails. There is
no separate web service and no `sudo` is required. Node 22.22.2+ (22.x) or 24.15+ (24.x) and `pnpm` are required; dependency
retrieval may be needed when the local pnpm store is cold. Refresh the browser after it succeeds.

## Preserve source TLS before upgrading

An existing source deployment that relies on implicit certificate discovery needs an explicit TLS
setting before upgrading. Keep its current certificate directory and trust; this is not an archive
migration. Run as the operator from a supported Linux host with visibility into its own `/proc`
process/socket records. This operation handles legacy external certificate pairs, a concrete IP binding and a direct
source `bin/alt serve` unit (optionally invoked with Python 3). Managed Altitude CA directories,
wildcard/DNS bindings, environment files and foreign drop-ins require separate reconciliation.

Obtain the reviewed installer, archive and verified checksum, then inspect:

```sh
python3.12 install.py --archive altitude-v0.1.0-rc.2.tar.gz --sha256 '<release SHA-256>' \
  --prepare-source-tls /absolute/path/to/existing-certificates
```

The version is illustrative. The command verifies the archive and checks the active source unit,
effective process settings, listener ownership and the exact served certificate. It prints the
single service override without writing it. After reviewing that output, repeat with `--apply`.
With a CLI containing this operation, the equivalent is:

```sh
alt service prepare-tls --directory /absolute/path/to/existing-certificates
alt service prepare-tls --directory /absolute/path/to/existing-certificates --apply
```

Apply reloads the unit definition and verifies the loaded TLS-directory setting, unchanged daemon
PID and HTTPS identity. It does not restart the service, change binding or certificates, install
the archive, or alter device trust. Changed/ambiguous ownership or identity refuses preparation;
failed reload/verification restores only the unchanged owned override and reports any unconfirmed
restoration. Existing custom overrides need deliberate reconciliation. Save the successful result
privately and verify normal activation after the separately authorized upgrade. L2 and L3 cannot
run preparation, and a passing fixture test is not evidence that a production unit is prepared.
Verification compares the configured command and live process identity; command execution-history
timestamps can reset during a definition reload. On failure, the message names changed fields
without printing environment values and includes the original failure if restoration is uncertain.
Do not retry an uncertain restoration until the loaded unit and running identity are inspected.
If interrupted after writing the override, inspect that file and any other pending unit changes
before running `systemctl --user daemon-reload` from the operator terminal. This reloads definitions
without restarting services. Repeat check-only preparation and then `--apply`; an ambiguous state
never counts as successful preservation.

## Validation runs

Task owners run installation VMs, containers and sandboxed-browser checks through the
[validation runner](DEVELOPMENT.md#validation-runner): one disposable rootless Podman container at a
time, started by altd as the operator's account. Its image, image layers and the cached Ubuntu cloud
image live in `~/.altitude-validation`, beside Altitude's home. A run needs 20 GiB free there, and its own area is removed
when it ends; the first run builds the image, which takes several minutes. Settings → **Validation
runs** turns the runner off: a running run stops and its container and scratch files are removed
after its log and results are retained. Each run appears on its task as a machine run with purpose
`validation`. Activation waits for admitted validation to finish recording evidence. After an
unexpected daemon exit or host reboot, startup stops abandoned runs, retains their logs and results,
and records them as interrupted. If copying evidence fails, the ledger names the original paths
in the runner area; that area stays intact and new runs stay refused pending recovery.

### Remote macOS validation

`alt task validate --target macos -- COMMAND` uses the same task ledger after the separate
[Linux relay and Mac guest setup](SETUP.md#remote-macos-validation). The submitter is Linux; the
executor is a disposable offline macOS guest on the operator's Apple-silicon Mac. Machine setup
and native acceptance remain pending. The command needs no new per-run grant once scoped setup
and isolation verification are complete. It does not authorize a live-provider test.

Mac host setup makes its dedicated account restrictions effective on fresh SSH connections
immediately. The endpoint can answer status/result/cancel while submissions remain disabled by
`/Library/Application Support/AltitudeValidation/state/off`. Under the scoped setup grant, the
Mac owner verifies [native preflight](SETUP.md#remote-macos-validation), reserves the first-run
window with L3, and removes that marker as the configured runner. Populate the following local
variables privately from the installed configuration; never paste their values into evidence:

```sh
sudo -u "$VALIDATION_RUNNER" "$VALIDATION_PYTHON" -I -B -c 'from pathlib import Path; Path("/Library/Application Support/AltitudeValidation/state/off").unlink()'
```

For a bounded preflight window, restore the marker immediately after the selected run is admitted,
including on failure; the admitted run's status, results and cancellation remain available:

```sh
sudo -u "$VALIDATION_RUNNER" "$VALIDATION_PYTHON" -I -B -c 'import os; fd = os.open("/Library/Application Support/AltitudeValidation/state/off", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600); os.close(fd)'
```

The Mac owner runs these effects in a recorded finite command with a `finally` cleanup. After
successful native acceptance, routine enablement uses the same removal under the recorded setup
authority. This marker is a runner-owned admission switch, not a single-use command permit or a
security boundary against the trusted runner or administrator. Existing task authorization still
governs every submitted command; a reserved preflight window does not expand that scope.

Settings → **Validation runs** also refuses new Mac submissions and requests cancellation of a
running Mac job. Cancellation is confirmed only after the remote supervisor and VM stop and their
scratch mounts/clones are removed. Loss of the route, sleep, missing login or an unavailable SSH
service cannot confirm cancellation. The pending run identity blocks further Mac submissions;
Linux validation remains usable. After a daemon restart or a subsequent Mac request, reconciliation
asks about that same run, retrieves matching results and acknowledges their digest. It never starts
a replacement run to resolve uncertainty. An interrupted task row stays interrupted; later evidence
appears separately in its validation artifacts and reconciliation event.

Keep the laptop reachable and its dedicated runner logged in for native jobs; a locked screen may
remain locked. The runner neither wakes the laptop nor bypasses FileVault/login. If native job
termination, scratch detachment or result acknowledgement cannot be established, L3 owns a scoped
recovery task. Do not erase admission records or manually claim success to make another run start.
Inspect only the recorded run's job, private state and mount identity under the applicable grant;
leave everyday services and unrelated mounts alone. Native recovery still needs observed evidence.

Successful acknowledgement removes retained remote evidence. Unacknowledged evidence has an
admission storage budget; exhausting it reports unavailable until reconciliation. Task artifacts
follow normal task retention. Template disks and offline dependencies stay installed until an
authorized refresh/removal. Refresh only from reviewed dependencies: provision the guest while
trusted, shut it down, replace the protected template and recompute manifest/dependency fingerprints.
A candidate with different manifests or missing offline packages receives `template refresh needed`;
do not enable guest networking or update a template from candidate code.

Credential rotation is scoped machine work on both hosts. First close Mac admission with the
off marker above, then use existing validation controls to cancel or finish any active run and
retrieve/acknowledge its evidence. Keep admission off through the entire rotation; status probes
remain available. The setup tools assume the trusted runner/admin preserves this marker.

1. Prepare a replacement dedicated identity privately. On the Mac, check and apply a
   [host-key `stage` plan](SETUP.md#remote-macos-validation) with
   `python3 scripts/setup_validation_mac.py --plan PRIVATE_JSON --apply` under the scoped grant.
   This adds only the public key and retains the old key and exact source restriction.
2. On Linux, use `python3 scripts/setup_validation_relay.py --rotate --config PRIVATE_JSON`
   with the replacement identity and unchanged endpoint/account/host pin. It authenticates a
   fixed status probe before switching configuration, verifies again after switching and retains
   the previous identity for rollback. It never changes the Mac key file.
3. Verify and record a status roundtrip through the actual Altitude daemon and relay, plus worker
   refusal. A direct administrator SSH probe alone does not establish daemon admission. If this
   fails, retain both Mac public keys, keep admission off, and recover using the previous identity.
4. Only after that observed verification, apply the Mac host-key `retire` plan naming the exact
   old and staged replacement public keys; then run the Linux installer's `--retire-old` action.
   The Mac installer verifies the two local rows, not the cross-host observation. Reopen admission
   only within the remaining scoped authority.

To revoke remote access, close admission and reconcile active runs and retained evidence first,
then apply the Mac host-key `revoke` plan. It empties only this installation's key file, preserves
the off marker and leaves SSH configuration/services alone. Revocation does not terminate an
existing SSH session; reconciliation precedes removal. Pin and verify a changed Mac host key
independently; never disable host-key checking. Retire the installed relay/verifier socket units and Mac-only validation SSH
rule, installed code, state and copied template only after admission is disabled and remote termination,
cleanup and evidence retrieval are confirmed. Preserve preexisting accounts, templates, unrelated SSH rules and Altitude
services. Addresses, keys and private configuration stay off task chat, PRs, logs and reports.

## Voice input

Dictation turns speech into draft text in every conversation. One machine setting selects how; typing
is never affected.

| `alt machine set --voice …` | How it works | Words appear | Where audio goes | Needs |
| --- | --- | --- | --- | --- |
| `host` (default where it can run) | [Host voice](#host-voice): the browser streams the microphone to this computer, which transcribes it with its own speech model after a one-time setup. | while you speak, with punctuation and capitals; the last words may still change | to this computer only, over Altitude's own connection; kept in memory for the recording, never stored | `alt voice setup`, Linux x86_64 with Python 3.12 or 3.13, about 2.5 GB free memory while dictating |
| `browser` (default elsewhere, including macOS for now) | The browser's speech recognition (Safari on iPhone and Mac, Chrome, Edge). The server is not involved. | while you speak; the last phrase may still change until it is final | Safari recognizes on the device when it can, otherwise through Apple; Chrome and Edge send audio to Google or Microsoft speech services. Firefox and Chromium builds without a vendor key have no recognition and show a typing hint. | HTTPS and a supported browser |

```sh
alt machine show
alt machine set --voice browser --reason 'Recognize in the browser'
alt machine set --unset-voice --reason 'Back to the default'
```

The default follows what this computer can run: host voice where its model runs, browser recognition
elsewhere. A saved choice stays until you change it; host voice that is not set up, failed or cannot
run keeps its choice and says why. First run offers the one-time download (**Set up voice**, **Use
browser recognition instead** or **Skip**); after Skip, the microphone offers the download.

The setting lands in the private `~/.altitude/settings.json` through the machine-settings request.
**Settings → Voice input** edits the same setting: **This computer** and **Browser recognition**
save immediately. A Settings read or save updates the next capture in that browser document. Other
documents and CLI changes are picked up when opening Settings, on reload or after a recording is
refused for a changed setting. Recordings identify their backend and host runtime; changing either
cannot silently reroute unfinished audio. Altitude keeps no recordings.

Altitude no longer sends recordings to a speech service of your own. On the first start after an
update, it deletes a saved speech-service URL and key from the settings and request files and logs only
that the option was removed; the voice setting then follows the default.

### Host voice

Host voice runs NVIDIA's Parakeet TDT 0.6B v2 speech model (English, CC-BY-4.0) on this computer's
CPU. Set it up once, from first run, **Settings → Voice input → This computer → Set up voice** or:

```sh
alt voice setup    # downloads about 698 MB once, checks every file, then reports "ready"
alt voice status   # unavailable (with why), absent, setting-up, ready, failed (with why) or outdated
alt voice remove   # stops dictation and frees the disk space
alt machine set --voice host --reason 'Transcribe on this computer'
```

Setup downloads the model from Hugging Face and its runtime (onnxruntime, numpy and the onnx-asr
decoder) from PyPI, pinned by checksum in the Altitude release, into `~/.altitude/speech`. Nothing is
installed elsewhere and Altitude's own Python stays standard-library only. Settings shows progress and
**Cancel setup**; a failed setup shows its reason and **Retry**. An Altitude release that pins a new
runtime shows "Voice needs an update" with the same button. Only the operator runs `alt voice setup`
and `alt voice remove`; Altitude's agents cannot.

The first recording starts a speech process in about 1–3 seconds; speech during startup is kept. While
you dictate it uses about 1.5–2 GB of memory and under one CPU core; it exits after fifteen minutes
without voice. It needs about 2.5 GB of free memory to start, and says so when there is less. Words
appear about a second behind speech; Stop or Send lands the final text in about half a second. The
phone sends about 1.9 MB of audio per minute of speech. Host voice runs on Linux x86_64 with glibc 2.28
or newer and Python 3.12 or 3.13; elsewhere, including macOS for now, Settings and the composer say it
is not available and why. At most two recordings run at once; a third device is told voice is busy.

A recording survives a lost connection. The microphone keeps recording and the composer says
"Connection lost — still recording. Your words will catch up."; the page keeps the recording's audio
in its memory (at most ten minutes, about 19 MB, and at most two recordings: one sending, one
recording) and repeats each unanswered request with the same audio until it is answered. When this
computer no longer knows the recording (it dropped it after 30 seconds without audio, or Altitude
restarted), the page opens a new one and sends the whole recording again, as long as the voice setting
and runtime are unchanged; the words shown hold still under "Catching up…" until the replay reaches
them. Stop or Send while the connection is down shows "Waiting for connection…" with Cancel. After two
minutes the page gives up: the words shown land in the draft with "Couldn't reach this computer: your
recording's last words weren't added.", and a voice Send returns unsent to its own conversation's
draft. The audio is never written anywhere; a reload loses it.

When a recording stops early for another reason (the speech process fails, this computer refuses the
recording, or the microphone gives no audio), the words already shown stay in the draft and the
composer says "Voice stopped: <reason>. Typing works." A screen lock or call that takes the microphone
stops the recording as Stop would, landing what was recorded. The speech process's own errors go to
`~/.altitude/speech/worker.log`, and starts, stops and their reasons to altd's log.

For a device check, dictate on the iPhone in Safari and as a Home Screen app, and on a desktop
browser: a first capture, repeated Cancel and restart, Stop, Send, and a screen lock or call during a
recording; then quiet speech, background noise and one long minute without pausing, checking that no
words are lost where the text commits. For connection loss, turn on airplane mode for a few seconds
while dictating and again before Stop, and confirm the words catch up.

### Refreshing home-screen and bookmark icons

Altitude serves its HTML, manifest and icons with `Cache-Control: no-store`; the decision-alert
service worker does not intercept page or icon requests. Browsers and operating systems can still
keep their own installed-app or bookmark icon copies. A page reload is not proof that those copies
have changed, and Altitude does not promise an automatic native icon refresh.

After the updated build is active, open and reload the trusted Altitude HTTPS URL in the browser.
If an iOS Home Screen shortcut still has the old icon, remove that shortcut and add it again from
Safari's Share menu. On Android, remove the old shortcut, or uninstall the installed web app, then
add/install it again from Chrome. If a bookmark retains the old icon after revisiting the page,
recreate that bookmark. Reopen the added app and check decision-alert permission/subscription on
that device; reinstalling may reset it. Do not clear unrelated browser data or certificate trust.

The delivered browser checks verify served files and icon geometry at phone and desktop sizes.
Native iOS/Safari and Android/Chrome installation and refresh behavior require device observation;
browser fixtures do not establish it.

### On iPhone

Open your configured Altitude HTTPS URL through your private network. Safari exposes the microphone only in a
secure context, so the phone must trust the local CA used by Altitude's certificate. Follow the
[phone setup](SETUP.md#set-up-a-phone): **Add a phone** in Settings → Devices on a device that
already trusts Altitude, or `alt tls-share` on the computer running Altitude, shows a QR code that
offers the certificate for ten minutes, and the phone checks its name and SHA-256 before installing. The microphone button remains a typing-only hint on plain
HTTP or an unsupported browser. Safari's Share → Add to Home Screen gives Altitude a Home Screen
icon with its mark; decision alerts on iPhone need Altitude opened from there.

For your own installation, set `ALTITUDE_HOST`/`ALTITUDE_PORT` to its private-network endpoint.
On the next start Altitude reissues its server certificate for that address under the same CA, so
trusted devices need no new step. A generated CA refuses public addresses and names.
`ALTITUDE_TLS_DIR` selects a private certificate directory separate from runtime/project data.
Install the CA on the phone with **Add a phone** or `alt tls-share`, which reads these settings from
the running service's record rather than from the shell, and enable its trust in Certificate Trust Settings. Arrange the
private tunnel and any firewall rule for your chosen interface/port separately, and bind Altitude to
that interface directly. A forwarder on this machine in front of Altitude (an SSH tunnel, a reverse
proxy, a container's published port) hides which process connects, so the terminal cannot tell an
agent behind it from your browser; keep the terminal off while one serves Altitude. The shipped
service template's tunnel address and checkout path are not defaults to copy to another machine.
Generated server certificates renew automatically while the original CA remains valid; an expired
or replaced CA needs explicit new trust on every device. External certificate pairs are not renewed
or overwritten. A new certificate folder, CA, address or port reaches running task workers when the
service next starts: their `alt` calls follow the address and CA the service records, so they need
no relaunch and no per-command setting. Existing configured TLS paths and exposure remain operator choices.

With the `browser` backend, recognized words appear in the draft while you speak, and the field keeps
the latest words in view. English dictation gets sentence punctuation and capitals on every browser
from a small model bundled with Altitude that runs in the page on the device's CPU: no install, GPU,
service or cost, and the text never leaves the device for it. The device downloads the model (about
23 MB) from your Altitude server the first time you dictate and keeps it cached. Each phrase is
punctuated once the recognizer finalizes it; the phrase still being recognized shows as heard. The
model adds only `.` `,` `?` and capitals; it never changes, adds or removes a word, leaves words
such as `U.S.` or `google.com` exactly as recognized, and slips now and then (a capital on a common
noun; numbers stay as words). **Stop** or Send releases the microphone and waits for the last
phrase's punctuation under the transcribing status: at most ten seconds, or three while the model is
still downloading. Words it has not reached land as recognized and the composer says why: "Added
without punctuation: still loading" or, when the model cannot run (for example Safari before
16.4), "Added without punctuation: this browser could not run it". Other recognition languages keep
the recognizer's own formatting. Audio is never part of task or chat state. With either backend a
recording becomes text through
**Stop** (Ctrl/⌘+M), landing in the draft for editing, or the send arrow (Enter), landing and
sending at once (queued while L3 is busy). **Cancel** (Esc) discards it. An empty transcript or a
transcription failure sends nothing and preserves the draft.

For a manual Safari check with either backend, open each of a project conversation, a task conversation, and a project
task's **Message L2** panel; record and stop; confirm the transcript is appended to the existing
draft and nothing else appears; record again and use the arrow to land and send or queue at once;
then cancel a recording and deny microphone access once and confirm the
typed draft remains usable. If Safari reports that voice needs HTTPS, use your configured secure URL and verify the local CA is
enabled under Certificate Trust Settings.

For a cancel/restart failure, note the device, OS/browser version, and whether Altitude is in a
browser tab or a Home Screen app. In a project and task conversation, keep a short typed draft,
dictate, tap X, then tap the microphone and speak again; repeat three times. X preserves the typed
draft and leaves the keyboard closed. A restart waits for recognition to end and for the waveform
audio context to close before acquiring audio, with at most three seconds for each wait. Record
whether it stays on **Opening microphone…**,
shows **Listening…** with a moving or flat waveform, or reports an error, and whether words appear.
Cancel remains available during shutdown waits. Compare one Stop → microphone cycle with X →
microphone. If the failure occurs in an already-open tab, preserve the observations and draft before
one reload and repeat: this distinguishes the loaded client from the currently served build; a
reload is a diagnostic comparison, not successful restart acceptance. Native success requires
repeated capture and transcription on the affected device without further reloads; Chromium's
scripted recognition and synthetic audio do not establish that result.

For a silent restart, **Settings → Voice input → Voice troubleshooting** offers **Start diagnostics**.
Return to the conversation without reloading, reproduce once, then return to **View report** and
**Copy report**. Paste the report into the owning task conversation. It includes the loaded asset
name, browser version, Home Screen mode, microphone track states, recognizer events and waveform
audio-context state/timing. It contains no recordings, speech, drafts, device identifiers or server
addresses. Collection is opt-in, keeps the latest 256 events in page memory, stops after ten minutes,
and sends nothing automatically. View report stops collection; Clear report or reloading deletes it.
If copying is denied, the selectable report provides a manual fallback. This evidence distinguishes
silent input from a suspended waveform graph; it does not itself prove a native-browser cause.
Read the graph state and whether its clock advances before interpreting signal presence; signal
is omitted while the graph is not running. State samples occur once per second while the waveform
draws, so brief transitions or a stopped drawing loop require further investigation. A dropped-event
count identifies truncated reports; start a fresh report and reproduce briefly when it is nonzero.
For browser recognition with successful graph setup, `waveform.connected` precedes `recognizer.start`;
graph setup errors are reported separately. A silent restart with that ordering, an advancing graph clock and live unmuted
tracks still needs native capture investigation; successful scripted ordering checks do not prove
device recovery. Compare both signal and recognized words across repeated restarts.
