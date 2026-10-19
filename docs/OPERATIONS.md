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
A source deployment runs as the user service `make install-service` installs from the checkout: the
shipped [service unit](../systemd/altitude.service) on Linux, or a LaunchAgent written by
`scripts/source_launch_agent.py` on a Mac. [Run from a source checkout](#run-from-a-source-checkout)
describes its setup, settings and removal. Releases generate their own user service instead.

## Runtime and inspection

`altitude/` is a standard-library Python package. `bin/alt` is the CLI; `personas/` contains the
project coordinator (L3) and task owner (L2) instructions, `schemas/` defines delivery reports,
and `hooks/` contains managed-repository Git hooks, and inbox delivery.
`web/` builds into ignored `web/dist/`, served by the same Python process as the API.
See [architecture](ARCHITECTURE.md) for authorization and engine containment.

Runtime state lives under `ALTITUDE_HOME` (default `~/.altitude`): project configuration, active
and archived tasks, conversations, monitor snapshots, and private incident evidence. Keep it
out of source control and [back it up](#back-up-runtime-state) privately. Provider session files have
their engine's own retention policy.

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
`remove <name>` and `set <name>` for routing preferences, the L2 provider priority, the task Only engine and
the L3 model choice. All projects share one machine cap,
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
on Claude, and High for a Codex L2 and Medium for other Codex roles; **Native** asks Claude for its own
level and sends Codex Medium. Higher effort can use more
time and tokens. The CLI reference covers [routing preferences](CLI.md#automatic-routing-preferences),
[model defaults](CLI.md#default-models) and [effort selection](CLI.md#task-reasoning-effort),
including the same settings from the terminal.

A step only the operator can take arrives in chat as a command block. **Open in terminal** types it at
the prompt of that task's or project's terminal without pressing Enter; read it, then press Enter, edit
it or clear it with Ctrl+C. A plain code block (a command for another machine) has Copy only. If a program is
running in the terminal, or a command it typed there still waits for Enter, nothing is typed and a notice offers
Copy instead, so opening a command twice never types it twice. On Linux, `sudo` in the
terminal asks for your password there, as in a desktop terminal. If the terminal cannot start, it shows
why and closes; a desktop or SSH terminal on this computer runs the same command.

A task's owner can read its task terminal's output, and the project's coordinator its project terminal's;
each terminal says who. When you press Enter on a step the owner or coordinator handed you, Altitude tells
it once the command looks finished (or the terminal ended first), and it reads the result to check that it
did and continues; no reply is needed. Meanwhile the owner's
question card, in Needs you and the task conversation, says **Ran in the terminal** and that it waits for the
owner to check it, or that the terminal closed before the command finished, so you can open it again; the question
stays open until the owner settles it. It reads what the
terminal printed, never what you typed at a hidden password prompt, and cannot type into or close the
terminal. Output stays readable until a new terminal opens there, its task finishes or Altitude restarts;
if a restart lost it, tell the owner or coordinator what the command printed. Use a desktop or SSH terminal
for work neither should see: what they read also reaches their provider.

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

## Back up runtime state

Code and lasting decisions live in Git and GitHub. `ALTITUDE_HOME` holds the work in flight:
project registration and settings, coordinator chats, active and archived tasks with their
conversations, questions, reports and evidence, and incident notes. A copy of it lets you carry on
after a lost disk or on a new computer. Altitude takes no backups itself; copy the folder with ordinary
tools into a private place your existing backup already covers, such as a Time Machine or iCloud Drive
folder on a Mac or an external drive on Linux. Never commit it to a repository: it holds private chats
and evidence. Encrypt the file before it leaves storage you trust.

```sh
(umask 077 && tar -czf ~/Backups/altitude-2026-10-10.tar.gz -C ~ .altitude)
tar -tzf ~/Backups/altitude-2026-10-10.tar.gz > /dev/null && echo complete
```

The first command writes an archive only you can read; the second reads the whole archive and fails
on a truncated or damaged file. Both assume the default `ALTITUDE_HOME`. The voice runtime lives beside it in
`~/.altitude-speech` and downloads again when needed, and Altitude's copy of its repository in
`~/.altitude-repository` is fetched again from the origin, so the archive leaves both out, as it does task
workers' drafts and tool caches in `~/.altitude-scratch`, deleted when each task ends. Records are replaced whole, so a copy taken while
Altitude runs holds intact files, but activity during the copy can leave one record without its
counterpart, such as an incident index line without its incident. For an exact copy, take it while
no task runs and the coordinator is idle. Task folders keep their check evidence, so
`du -sh ~/.altitude` shows how large the archive can be.

To inspect a backup without touching live state, unpack it into a new scratch folder and read it
there; nothing runs from that folder:

```sh
mkdir /tmp/altitude-check && tar -xzf ~/Backups/altitude-2026-10-10.tar.gz -C /tmp/altitude-check
cat /tmp/altitude-check/.altitude/projects.json
ls /tmp/altitude-check/.altitude/example/tasks /tmp/altitude-check/.altitude/example/archive
```

Putting a copy in place is a separate, deliberate step: with the [service](#service-lifecycle)
stopped, move any current `~/.altitude` aside and unpack the copy into the home folder. Clone each
project's repository to the same path, because the copy records each project's folder. The copy does
not hold provider sign-ins, Codex's own session files, GitHub sign-in, the certificates and
paired devices under `~/.config/altitude`, or repositories, worktrees and uncommitted work. Sign in
again, pair devices again and push any task branch you want to keep before moving. A restored task
keeps its conversation and records, with its Claude session, which lives in the task's folder; a
Codex session returns only if Codex's own folder was copied too. Tasks that were running come back without their workers; nothing is restarted
or replayed by the copy itself.

## Devices and lockout recovery

**Settings → Devices** lists every paired browser with the day it paired and was last used. **Remove**
asks once, then signs that browser out at once: its next request shows the pairing screen and its
open streams end. Removing the browser you are using returns it to the pairing screen too.

Nothing on the network can unlock Altitude. If no paired browser is at hand, open a shell on the
computer running Altitude, locally or over SSH, and run `alt pair`; it writes a fresh code directly
to `~/.config/altitude/access/`, so it works whatever address Altitude listens on and whether or not
a browser is open. To sign out every browser, remove `~/.config/altitude/access/devices.json`. Back up
the folder only to a place as private as `~/.config`.

## Engine sign-in recovery

When an engine's login expires or is revoked, Altitude recognizes it from the CLI's own sign-in
failure, whatever its wording, and the coordinator answers on the other configured engine. Project
chat says so once per lapse, for example "Claude's sign-in has lapsed, so Codex is answering for now.
Sign in again from Settings › Prerequisites." With no other engine, or with
the project set to one engine, the line says your message waits, and it sends once the engine is
signed in again. Codex runs use the CLI's own sign-in, which the five-minute usage check also reads,
so a lapse it sees moves the next message before any turn fails, and its next successful reading ends
the lapse. Claude runs use Altitude's own sign-in, made with **Sign in** on the Claude row of Settings ›
Prerequisites (or `alt sign-in claude`), which lasts a year and which the usage check does not read. Each project's chat says once, two weeks before the year ends,
that it needs renewing; a missing or ended one shows as a lapse on the first Claude run, and saving a
new one ends it. A sign-in that fails says why in one sentence and changes nothing, so an earlier
sign-in keeps working; **Try again** starts afresh. "Altitude couldn't follow Claude's sign-in" means
the Claude CLI's setup screen changed in a way this Altitude version does not read, and it needs an
update.

Task owners hold with "provider rejected authentication or account access; sign in, then Retry or
Resume". Sign in (Claude's **Sign in** in Settings › Prerequisites, or `codex login`), then press
**Resume** on a held task, or **Retry** on a coordinator turn that stopped. That attempt launches;
once an engine call succeeds, held tasks and report turns on the engine continue on their next tick.
While the login is still invalid, the attempt records the rejection again and automatic retries keep
waiting for up to thirty minutes. The CLI cannot refresh a revoked sign-in itself. A usage limit is
not cleared this way; it waits for its reset.

## Decision alerts on a device

A subscribed phone or browser is woken once for each new decision that still needs you after L3 has had
its turn and the owner has stopped, or an hour after it was asked if either is still busy, and again when a
decision is asked again after you reply; a rewording while it waits wakes nothing. The banner reads
"<project> needs a decision" with the task name, and tapping it opens that decision. Away from Altitude's
network it reads "A decision needs you" and says Altitude is out of reach. A wake that finds nothing new
names what still waits, silently ("<project> still needs a decision", or "Still waiting for you" with one
line per decision); a decision settled in the moment before the device read it shows "No decision needs you
now". PR reviews, faults and stops stay in Needs you
without waking a device. No push is sent only to clear a banner, because Safari shows something for every
push: an answered banner closes when Altitude is next open on that device or the next alert arrives. altd
logs each wake as `push wakes <n> device(s) for <m> new decision(s)`.

## Refused decision alerts

A subscription the push service will never take again ends on Altitude's side: altd drops it from
`~/.altitude/push.json` and logs `push to <host> ended with <status> <reason>: dropped`. Each time
Altitude opens or returns to the screen on a device with alerts on, the device makes sure push can
wake it: it tells altd about its subscription again, makes a new one in place of one altd dropped or
one made with a key Altitude no longer signs with, telling altd to forget the endpoint it replaces,
and tries again if subscribing failed last time. Nobody touches the switch.

- `404` or `410`: the subscription has ended.
- `400 VapidPkHashMismatch` (Apple): the subscription was made with another signing key. On other
  services the device notices the old key itself when Altitude next opens there.

Any other refusal keeps its device subscribed, and altd tries that device again on each tick until a
push gets through, even for a decision another device already took, so a fix on either side takes
effect without another step. altd records the refusal and the short reason code the service gave,
logs it once as `push to <host> refused with <status> <reason>` and logs `push to <host> delivered
again` once a push gets through. Until then, the line under the alert switch on that device alone
names its push service and the reason, says that it gets alerts only while Altitude is open and
what to do:

- `401` or `403`, such as `403 BadJwtToken`: the service rejects the signed sender token, most often
  its contact address. Set `ALTITUDE_PUSH_CONTACT` to a `mailto:` address with a real domain (see
  [configuration](#configuration)).
- Anything else, such as `429` or `503`: nothing; the line says Altitude keeps trying.

## Remove a project

In the project's **More actions** menu, **Remove project** detaches its coordinator and stops
Altitude management. Finish or reject unfinished tasks, then wait for their workers and any
coordinator turn. The repository, remaining worktrees, history, provider sessions and queued
messages stay on disk. Add the same folder and project name again to restore the history and
resume waiting messages. Removing the last project opens First run; otherwise a remaining
project is selected. `alt project remove <name>` uses the same checks. See
[project lifecycle](CLI.md#project-lifecycle).

## Worktree and source export retention

Altitude removes what it created for finished work; the operator does not clean these by hand.
For each done or rejected task, the maintenance tick removes `.claude/worktrees/<slug>` once no worker
runs, the tree is clean and its commits are on a branch or remote, and deletes `worktree-<slug>` once
its commits are on `origin/main` or a verified PR merged it. A branch with commits found only on this
machine stays; `alt task events <slug> --json` shows the `cleanup-worktree` event with its reason and
unmerged commit count. Uncommitted changes or commits on no branch keep the worktree with a recorded
reason. Inspect them in that worktree, then remove it with `git worktree remove` once nothing is needed.

A source deployment removes `.altitude-source/<sha>` exports that nothing names: the running service's
export, the target of `current` and exports named by an unfinished or not yet cleaned-up task's brief
or session settings stay. Pruning starts an hour after an activation and waits while a task's launch
files cannot be read. `altd.log` records each removal as `[source] removed source export <sha>`.
Other entries under `.altitude-source`, such as `git-guards`, are never touched.

## Incident publication

Altitude records its own failures as sanitized incidents under the runtime directory. They stay
there until **Settings → Incident reports** (also a First run step) turns publishing on for a
GitHub `owner/repository`, Altitude's own filled in or a fork you name, or
`ALTITUDE_UPSTREAM_ISSUE_REPOSITORY` in altd's environment names one for a non-interactive
install. Every incident then becomes one sanitized issue there, and `alt incident list` shows the
link or the pending reason. An issue publishes the failure summary and a System section (platform,
OS, kernel, architecture, machine model, Altitude version and deployment kind, whether a newer
release was known, and the failed worker's engine, CLI version and confinement); the raw worker
output, evidence and host identities stay in the local record. A fault on an installed copy that is
behind a newer release keeps its issue on the machine, held with "update first": update, and if the
fault comes back it is filed on the new version. `alt incident publish <id>` files a held issue
anyway, for example when the update itself fails. The saved setting outranks the environment, including when it turns
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

## Repairs

When something does not work and Altitude cannot fix it on its own, such as a failed update, a
service that does not come back or a page that does not load, run this in a terminal on this
computer:

```sh
alt repair
alt repair --problem 'The page does not load on my phone'
```

It needs neither the service nor the page. It prints what it found ("Altitude 0.8.0 · service
running · the update to 0.9.0 failed"), writes a brief and starts your coding agent's interactive
session in that terminal, in its own folder under Altitude's home, as you and with your full access
while it works. The CLI runs in its normal mode, with your own sign-in and settings, so it asks you
before each step unless those settings allow it. The brief holds what you wrote, the update failure
Altitude recorded, its rules, how this installation is laid out, the known symptoms and Altitude's
records (`alt doctor` and the last day's system faults), quoted as evidence and never as
instructions. Its rules are instructions the session follows, not limits Altitude enforces: act on
this installation only; never sign in, read or print sign-in state or Altitude's keys, or change
certificate trust; never approve, merge, grant or answer anything for you; ask before deleting,
installing or changing anything outside Altitude; restart the service only with a health check
afterwards and leave managed projects' services alone; leave nothing running; report honestly.

Inside the session `alt` offers only inspection and the installation's own commands (`doctor`,
`service`, `update`, `recover`, `install`, `monitor`, `queue` and `repair report`); task decisions,
chat and approvals stay yours. The session ends its work with

```sh
alt repair report --wrong '…' --changed '…' --remains '…' [--try '…']
```

and you end the session as you would any other. Once it has ended, the service checks the result
with `alt doctor` and the installed release's manifest, at its next tick or its next start, and sets
the outcome:

- **Repaired:** for a failed update, the target version is installed and running, its files match the
  release and every check passes.
- **Confirm yourself:** every check passes, but a check cannot prove a problem you described is gone,
  or the installed files differ from the release because they were changed by hand.
- **Not repaired:** a check fails, or the update's target version is not installed or not running.
- **The repair ended without a report** when the session ended, crashed or its terminal closed before
  it reported.

Settings → About lists the three most recent repairs with their outcome, and **All repairs** lists
every one; each repair's page shows its report, Altitude's check and its brief. Its steps stay in that
terminal's history. One repair runs at a time on this computer: a second `alt repair` says "A repair
is already running. Stop it there first." The lock belongs to `alt repair` and its session, so a crash,
closed terminal or restart never leaves one behind, and a repair counts as running until its session,
and anything it left running from that terminal, has ended.

Only you start a repair, from your own terminal. Anything running in one of Altitude's jobs refuses
("Only you can start a repair, from your own terminal."): its agents, coordinator and reviewers, a
command under an operator grant or a standing policy, and Altitude's terminal on the page, where a
`run` block an agent wrote would run on your Enter.
When `alt` itself cannot start, the release's standalone installer starts the installed release's
own repair from its verified files: `python3.12 install.py --repair [--prefix PATH]`. A repair needs
an installed coding agent; it cannot repair a deleted installation, which goes back to
[setup](SETUP.md).

## Configuration

An installed release saves these settings from the shell that installed it in
`~/.config/altitude/install.json`, and its service keeps them; a source service takes them from its
unit or LaunchAgent and the environment. Application versions live under `~/.local/share/altitude`,
configuration and certificates under `~/.config/altitude`, runtime state under `ALTITUDE_HOME`, and
project checkouts stay where they are. Keep those directories separate.

| Setting | Purpose |
| --- | --- |
| `ALTITUDE_HOME` | Runtime state directory, default `~/.altitude`; use the same value for CLI and server. Keep it out of Git. |
| `ALTITUDE_ROOTS` | Initial projects folder(s), colon separated, default `~/Projects`: First run lists the folders directly inside. **Settings → Projects folder** or `alt machine set --projects-folder PATH` replaces it with one folder without a restart; `--unset-projects-folder` returns to this value. `project add --path` also supports other folders. |
| `ALTITUDE_OPERATOR` | Initial name shown for the operator; unset falls back to Git's global `user.name`, and with neither, screens say “you”. First run or **Settings → Your name** stores a name that replaces it; clearing that name returns to this value. |
| `ALTITUDE_HOST`, `ALTITUDE_PORT`, `ALTITUDE_TLS` | Default `127.0.0.1:8890` over HTTPS. For other devices, bind the specific private address they reach; a wildcard bind is certified for `localhost` only. Explicit source/development HTTP remains available; TLS failures never select it automatically. |
| `ALTITUDE_TLS_DIR` | Private certificates, default `~/.config/altitude/tls`, outside application/runtime/project writable roots. |
| `ALTITUDE_SOURCE_BRANCH` | The branch a source service runs and self-deploys, default `main`. The service refuses to start unless its checkout is on this branch and its files are exactly a commit on it in Altitude's copy of the repository; task worktrees still branch from `main`. On a Mac, `make install-service` sets it to the checked-out branch when that is not `main`. |
| `ALTITUDE_CONFIG` | Installed settings, default `~/.config/altitude/install.json`, outside application/runtime/project directories. CLI overrides are explicit; the generated service pins saved values against its inherited environment. Source checkouts ignore this file. |
| `CODEX_BIN`, `CLAUDE_BIN` | Engine executable locations. The default locations and role/model settings are in the engine configuration module. |
| `ALTITUDE_PUSH_CONTACT` | Address a push service may use to reach the sender of decision alerts, default `mailto:altitude@example.com`, which names no one. Set your own `mailto:` address if a device's push service refuses that one. |
| `ALTITUDE_PRIMARY_ENGINE` | Tie order in the default Auto top tier; project `--routing` overrides those tiers. |
| `ALTITUDE_UPSTREAM_ISSUE_REPOSITORY` | Initial GitHub `owner/repository` that receives Altitude's own sanitized incident issues, for a non-interactive install. Unset by default: incidents stay on this machine. First run or **Settings → Incident reports** replaces it, including turning publishing off; see [incident publication](#incident-publication). |
| `alt machine set --voice` | Transcription backend: `host` (this computer transcribes live after `alt voice setup`; the default where its model runs) or `browser` (no setup; the default elsewhere). See [voice input](#voice-input). |

Single-engine Auto and explicit pins are supported; arbitrary provider and access configurations are
not verified. The launcher filters some engine environment variables and supplies role settings, and
the Codex coordinator ignores user configuration, including custom provider settings, so an
interactive API or Bedrock configuration may not carry over to a launched session. See the
[engine boundary](ARCHITECTURE.md#engine-integration-boundary).

## Installed application lifecycle

These are operator lifecycle actions for a packaged installation, not commands for ordinary code
agents or a source deployment. Run lifecycle CLI commands in a desktop or SSH terminal; a browser
terminal ends when the daemon restarts. Use the app's detached Update action from the browser.

```sh
alt doctor
alt service status
alt service logs
alt update
```

The daemon makes the same lookup at startup and every twelve hours and records what it finds.
`alt doctor`, Settings → About and a notice in the app show a newer release, and an
`alt` command you run in a terminal prints one line about it at most once a day. The app's
**Automatic updates** switch is on by default. While a release waits for its automatic attempt,
new validation, adversarial review and improvement runs wait, as they do for a source activation,
and their owners hear that Altitude restarts soon to apply an update and to ask again after it. Once
the ones in flight have finished and no browser terminal is open, the daemon starts the update. From
that request on, new dispatches, resumes, coordinator turns, report verification and browser
terminals wait too, and the update activates as soon as the turns and claims in flight end, within
twenty-five minutes or it fails and the previous version keeps running. Commands under an operator
grant hold no quiet point. An open browser terminal defers the update and lifts that wait until it
closes. Until the update starts, the notice, Settings → About and `alt doctor` name what it waits
for, from the daemon's last thirty-second check, for example "the validation run of
atlas/check-the-lane" or "the browser terminal open in atlas"; once it starts, the notice shows it
installing until Altitude restarts. Running task workers keep running. The page reconnects after
restart and shows the installed version and release notes. Close browser terminals to allow an
automatic update; ordinary desktop terminals do not hold it.

Turn **Automatic updates** off in Settings to return to prompting. The **Update** button, after
confirmation, runs `alt update --version` for exactly the version shown, in an independent job
that survives the service restart. **Check for new versions** turns off both lookup and automatic
starts, retaining the automatic preference. Neither switch cancels an update already started.
Each version gets one automatic attempt. Failure restores the previous installation through
activation recovery and offers explicit retry; a failed recovery retains its receipt for `alt recover`.
**Try again** in Settings → About is an explicit retry, including after dismissing the failure
banner. It does not turn the failed attempt into an automatic success or clear automatic-attempt
history. A newer offered release remains eligible for its own automatic attempt.
A failed attempt shows its cause in the notice, Settings → About and `alt doctor`, for example the
archive checksum that did not match or the file an installed release should not have; the update's
log or terminal keeps the details. An installed release that fails verification settles its update
attempt as failed with that cause; reconciliation does not repair or ignore unexpected installed files.
Other abandoned jobs are marked failed after thirty minutes only when the installation lock is free; a
slow live update keeps its admission fence. Failed preflight ends the detached updater's own
reservation. Manual updates and activation share that lock through download, verification, activation
and recovery, so automatic work cannot overlap them.

#### Generated-cache recovery

Any process that imports from the installed release without `-B`, such as a script or probe run
with `-I`, writes Python caches into it. Altitude treats bytecode generated for the release's declared
modules as derived from the release: updates, automatic ones included, recovery and service start
neither fail on these caches nor move them. Anything else extra, missing or changed in the installed
release still fails verification and needs diagnosis, and a downloaded release must match its
manifest exactly.

An installation on v0.8.0 or older runs its own update code, which refuses such caches. Its first
update to a release with this behavior can fail with "the only extra files look like generated
Python caches"; repair it once, then every later update has the fix. On v0.7.0 or v0.8.0, run the
line the error names, back to back so that no review or coordinator turn re-creates the caches in
between:

```sh
alt recover --generated-bytecode && alt update
```

It verifies every declared file, moves only the caches into a private backup under the application
prefix, then updates. An older CLI without that option uses the current release's independently
verified standalone `install.py` with the matching archive and checksum from the same release:

```sh
python3 -B install.py --archive altitude-<tag>.tar.gz --sha256 <trusted-sha256> --repair-generated-bytecode \
  && ~/.local/bin/alt update
```

This imports only the checksum/manifest-verified repair code and repairs the existing installation;
it does not activate the supplied archive. Both leave service state, settings, certificate identity,
pairing, projects and automatic-attempt history unchanged, and record what moved in the backup.

Uninstall retains configuration, identity and runtime data, so reinstall reuses them. It is not a
fresh reset. No reset command is provided: use a disposable installation/home for clean validation;
do not erase registered repositories or retained work. A genuinely new identity needs its new CA
verified and trusted on every device; trust in an older CA does not transfer.

Source deployments and containers keep their source/image lifecycle; the
[changelog](../CHANGELOG.md) notes how each release updates from earlier ones. The release lookup
is one anonymous GitHub request every twelve hours; installation also downloads the release archive
and checksum. Automatic updates trust the same release publisher to supply daemon code with
repository access; turn them off if each release needs your review.

`alt update` makes one anonymous request to `api.github.com` for the repository the installed
release was built from and picks the newest release the installation follows. A stable copy follows
stable releases only: it asks for GitHub's latest release, which is never a draft or prerelease. A
copy installed from a `-rc.N` candidate also follows newer candidates: it reads the thirty newest
releases and takes the highest version that is not a draft. It does nothing when no followed
release is newer than the installed version. Otherwise it downloads that release's archive and its `.sha256`
from the release, then installs it exactly as an archive update does. `alt update --version
<version>` installs a named newer published release, including a `-rc.N` candidate, without the
lookup; it refuses an older version, which `alt recover` restores. Every download hop stays on
HTTPS, the verified archive must be the requested version, and the version is compared with the
installed one again under the installation lock, so a concurrent update cannot cause a downgrade.
`alt update --archive altitude-<version>.tar.gz --sha256 '<release SHA-256>'` installs an archive
you already have, with no network access. The service keeps the `HTTPS_PROXY`, `NO_PROXY` and
`SSL_CERT_FILE` settings of the shell that installed it, so the lookup and downloads work behind an
HTTPS proxy. Installation checks the archive and manifest before
selecting an immutable version. Activation waits for dispatch,
resume, L3 and report verification to be quiet, then verifies the selected version/commit, native
service PID, HTTPS health and built UI. An already stopped installation stays stopped on update.
Use `alt service start` or `alt service stop` only when deliberately changing its lifecycle;
independent task workers are not stopped with the daemon. On Linux the user manager runs the service
after logout and at boot only while the account lingers; a first installation turns lingering on and
`alt doctor` reports it ([keep running after logout](SETUP.md#keep-running-after-logout)). On a Mac
Altitude runs only while you are logged in and is back after a restart once someone logs in. The service is the LaunchAgent
`dev.altitude.altd` (an installation under another `HOME` gets a label derived from that home, so it
never addresses the account's own service): `alt service stop` boots it out until the next login or `alt service start`,
and `alt service logs` reads `~/Library/Logs/altitude/altd.log`; an in-app update logs to
`altitude-update-<attempt>.log` beside it. Projects continue using ordinary checked
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

`alt uninstall` stops/removes only the owned service and launcher; on a Mac launchd keeps its
disabled record for the label. It refuses unfinished tasks
that still own worker inputs. Registered projects keep installed hook resources; otherwise
application versions are removed. Configuration, certificate trust, histories, provider sessions
and project worktrees remain. On Linux it leaves the account's lingering on and says so;
`loginctl disable-linger` turns it off when nothing else needs it. Updates do not prune prior versions. Removing retained data or
device trust is a separate deliberate action, not part of uninstall or recovery. Code restoration
does not roll back data; incompatible state needs the [release recovery procedure](RELEASING.md#recovery).

## Run from a source checkout

A clone of this repository can run Altitude instead of a release: the service runs the checkout's
committed code and follows a branch of its `origin`, `main` by default, rather than published
releases. Point `origin` at your fork and check out your own branch to run your own changes. The
[platform table](ARCHITECTURE.md#linux-and-macos) lists what differs between Linux and a Mac.

You need the [setup prerequisites](SETUP.md#prerequisites), and `python3` on PATH must be Python
3.12 or newer, since `bin/alt` runs with it. Building the web app also needs a Node version that
`web/package.json` accepts and the pnpm it pins, through Corepack (`corepack enable pnpm`); the
service rebuilds the web app itself when it activates a new commit, so its PATH needs them too.

### Clone and build

On Linux the service unit runs the checkout at `~/Projects/altitude`, so clone it there; on a Mac
any location works.

```sh
git clone https://github.com/mburakyucel/altitude.git ~/Projects/altitude
cd ~/Projects/altitude
make web
export PATH="$PWD/bin:$PATH"
alt tls-init
```

`make web` installs the locked web dependencies and builds `web/dist`. Add the checkout's `bin` to
PATH in your shell profile as well, so `alt` is this checkout's CLI; do not link it into
`~/.local/bin`, which belongs to an installed release. `alt tls-init` creates Altitude's
certificate authority and server certificate in `ALTITUDE_TLS_DIR`, or keeps the identity already
there, so devices that trust it keep that trust. The service refuses to start without it.

### Install the service

From the checkout, clean and on the branch the service will run:

```sh
make install-service
```

It installs Altitude's Git guards in the checkout and starts the service at `https://127.0.0.1:8890`:

- **Linux:** it copies `systemd/altitude.service` to `~/.config/systemd/user/`, then enables and
  starts it. The unit runs `main` with PATH `~/.local/bin:/usr/local/bin:/usr/bin:/bin`; nvm's
  default Node is found when Node is missing from that PATH ([noninteractive toolchain](DEVELOPMENT.md#noninteractive-toolchain)).
  Change the address, PATH or branch with a drop-in from `systemctl --user edit altitude`, for
  example `Environment=ALTITUDE_HOST=<private address>`, then `systemctl --user restart altitude`.
- **Mac:** it writes the LaunchAgent `~/Library/LaunchAgents/dev.altitude.altd.plist`, which
  logs to `~/Library/Logs/altitude/altd.log`. The agent listens on `ALTITUDE_HOST` from the
  command's environment (default `127.0.0.1`), keeps the PATH the command runs with, which must
  find `gh`, the coding CLIs and pnpm, and runs the checked-out branch. Run it again to change any
  of them, for example `ALTITUDE_HOST=<private address> make install-service`.

The service starts only while the checkout is on its branch and its files are exactly a commit on that
branch in Altitude's copy of the repository, `~/.altitude-repository`
([`ALTITUDE_SOURCE_BRANCH`](#configuration)). Altitude creates the copy from the checkout's
`origin/<branch>` on first start and fetches it from the origin afterwards; only Altitude writes it.
Each start runs the committed code, exported from the copy under the ignored `.altitude-source/`. `alt service status` and `alt service logs` read it.
A phone or another computer needs a private address; the target prints the firewall command for
`ufw`. Continue with [device trust](SETUP.md#trust-https-on-each-device),
[pairing](SETUP.md#pair-each-device) and [First run](SETUP.md#first-run-in-the-browser).

Both service definitions start with Altitude's sanitized incident reports going to its public
issue tracker (`ALTITUDE_UPSTREAM_ISSUE_REPOSITORY`). First run's **Report Altitude's own faults?**
step or **Settings → Incident reports** saves your own choice, including keeping them on this
computer ([incident publication](#incident-publication)).

### Updates from the branch

Register the checkout as the project named `altitude` (**Add project** on it in First run, or
`alt project add altitude --path "$PWD"` from the checkout) and the service follows its branch: every
thirty seconds it fast-forwards the checkout to `origin/<branch>`, then builds, restarts and verifies
itself at the next quiet point ([service lifecycle](#service-lifecycle)). That project also gets a
coordinator conversation like any other; its tasks need push access to `origin`. Without that
registration, update by hand from the checkout:

```sh
git pull --ff-only
make restart
```

`make restart` rebuilds the web app, restarts the service and waits for its API and page to answer;
it refuses unless the checkout is exactly `origin/<branch>` and Altitude is idle. A bad commit is
fixed by a revert or fix on the branch, never by resetting the checkout
([source recovery](RELEASING.md#recovery)).

### Differences from an installed release

- `alt update`, `alt recover`, `alt uninstall`, `alt service start` and `alt service stop` refuse a
  source deployment, and it neither checks for nor offers new releases.
- `~/.config/altitude/install.json` (`ALTITUDE_CONFIG`) does not apply: the service takes its
  settings from its unit or LaunchAgent and the environment.
- The checkout is a deployment, not a workspace. Its Git guards refuse commits and pushes to `main`,
  and the service refuses to start or update a dirty, diverged or ahead checkout; develop in another
  clone or a worktree, push there and let the service fast-forward.
- The service needs Node and pnpm to rebuild the web app, which a release does not.

### One service per account

A source service and an installed release use the same service name, port 8890, `ALTITUDE_HOME`
(`~/.altitude`) and TLS directory, so an account runs one of them. `make install-service` replaces
an existing service definition without asking: run `alt uninstall` first to retire a release,
which keeps configuration, TLS identity and data for the source service to reuse. The installer
refuses an existing source service and an `alt` it did not write; remove the source service as
below and take its `bin` off PATH before installing a release. An older release may not read data
that newer source code wrote ([recovery](RELEASING.md#recovery)).

### Remove a source service

Finish or stop running tasks first; their workers run in their own jobs and outlive the service.
On Linux:

```sh
systemctl --user disable --now altitude
rm ~/.config/systemd/user/altitude.service
systemctl --user daemon-reload
```

On a Mac:

```sh
launchctl bootout gui/$(id -u)/dev.altitude.altd
rm ~/Library/LaunchAgents/dev.altitude.altd.plist
```

The checkout, `ALTITUDE_HOME`, the TLS identity and device trust stay. `git config --unset
core.hooksPath` in the checkout removes its Git guards, [removing the project](#remove-a-project)
unregisters it, and each device's certificate manager removes the CA
([setup](SETUP.md#verify-https-before-pairing)).

## Service lifecycle

[Versioned releases](RELEASING.md) are validated checkpoints. For source deployments, they do not select the
deployed revision or delay activation. Recovery normally uses a checked revert/fix PR followed
by the activation path below; the release guide distinguishes web-bundle restoration from source
and runtime-state recovery. Never reset the deployment checkout to a release tag as a rollback.

Ordinary development and code agents must not start, stop, mask, unmask, or restart the service.
Source-deployed Altitude activates merged backend and web changes itself. The regular thirty-second tick discovers
merges even while their workers run. A failed fetch of `origin/main`, or a Git call that outlives its
time limit on a busy host, is logged and retried on the next tick, and becomes a system fault only after
the same step has failed for five minutes without a success. Report verification treats a GitHub read that
times out or cannot connect the same way.
Other self-deploy refusals, such as a dirty checkout, fault immediately and once; the tick then waits
for the checkout to change. A fault reading "fast-forward failed after moving the branch" (a full disk or a
timeout while writing files) means the branch already names the new commit while its files do not: restoring the
files to the branch's commit lets the next tick continue, and that move's activation is not marked, so the backend
needs a restart once the checkout is clean. A self-deploy fast-forward marks activation
pending for loaded backend paths (`altitude/`, `bin/`, `systemd/`) or tracked web build inputs
(`web/src/`, `web/design/tokens.css`, `web/index.html`, `web/package.json`, `web/pnpm-lock.yaml`,
`web/tsconfig.json`, `web/vite.config.ts`). Web docs and other non-build files do not trigger it.
Dispatch, L3 turns and report verification continue while activation is pending; new validation runs and
adversarial review runs are refused until the restart, so the long work in flight drains instead of being
replaced; `alt task validate` waits through the restart and asks again. Once no dispatch or resume claim, L3
turn, adversarial review, validation run, or report verification is in flight, `altd` runs the guarded restart
script below as a transient user unit. Validation holds the quiet point through its one-hour execution limit,
evidence recording and cleanup, so a busy machine activates within one validation run. Monitor names the
validation run and review tasks it waits for.
The unit refuses for one of two kinds of reason. A refusal a later quiet point can clear (the quiet
probe gets no answer in three tries of ten seconds each, work claimed the quiet point first, or the
checkout moved after the build began) withdraws the request and records the reason as `error`, a
`retries` count and a `retry_at` time five minutes later in `monitor/restart-pending.json`; the hold
lifts and `altd` requests the unit again at the first quiet point after that time, up to three
retries, without a fault. Any other refusal, or one more transient refusal after the third retry,
files a system fault naming its reason for L3 immediately; while its request is still pending it also
records the reason as `error` and marks it `failed`, and the hold lifts. A restart that has not happened
ten minutes after it was requested is the same fault, for a unit that died without reporting. The
transient unit is collected once it exits, so `systemctl --user status` reports it not-found;
`journalctl --user -u <unit>` keeps its output. `alt repo` shows the pending files with the retry or
failure and the last error. A failed activation is requested again after the next merge that needs
activation, or sooner from the Restart button, with its full retry allowance.

An explicitly authorized operator can restart sooner by pressing Restart on the web app's banner
(shown only at the narrow quiet point, including while workers run) or running `make restart`
from the deployed primary checkout. The command refuses another clone/worktree, a dirty, ahead,
diverged or other-branch `main`, a checkout that moves after its build begins, and an in-flight dispatch,
L3 turn, adversarial review, validation run, or report verification. A `main` behind `origin/main` activates the
head it built, and the deployment tick fast-forwards and marks the rest pending. Both engines run L2 workers in independent transient user units,
so workers survive and are adopted after restart. Dispatch and
L3 turns wait only from the restart request until the replacement daemon is ready.
It installs the locked web dependencies, builds and validates a staged bundle, swaps it into the
ignored runtime `web/dist`, restarts the user-level `altitude.service`, and waits up to 45 seconds for both
its overview API and web page to answer from a new process, allowing each answer ten seconds. It probes on
the address and scheme in the service's own environment, so a failure names the real cause, such as an
HTTPS timeout. On macOS, Stop and restart confirm that the service's processes
end and its launchd label disappears before completing Stop or bootstrapping a replacement; removal
has a 45-second limit and unreadable state fails explicitly. Independent worker jobs retain their
own coalitions. The prior bundle is restored if verification fails, then recovery restarts the service
and verifies API and UI health from another new process. The failure retains the activation error
and states whether recovery is verified or fails; restored files alone do not establish healthy
recovery. The replacement backend keeps running either way, so the failure leaves the attempt's web build
inputs in `monitor/restart-pending.json`, marked `failed` with the error; `alt repo` and the web app's
notice show the served web app is older than main, and Restart or the next merge activates it. A merge or
request recorded meanwhile keeps its own record, whose activation builds the current bundle.
Without a prior bundle, it reports that recovery is unavailable. There is
no separate web service and no `sudo` is required. Node 22.22.2+ (22.x), 24.15+ (24.x) or 26+ and `pnpm` are required; dependency
retrieval may be needed when the local pnpm store is cold. Refresh the browser after it succeeds.

Deterministic restart fixtures establish removal ordering, worker isolation and activation/rollback
health handling. Native prevention acceptance separately requires a successful guarded activation
with a new service PID, API/UI health and worker/session survival. A running service or an absent
pending flag establishes neither the cause of a prior bootstrap failure nor that acceptance.

## macOS privacy prompts

Altitude never needs access to Photos, Desktop, Documents, Downloads, Music and media, Contacts,
Calendars, Reminders, removable or network volumes, or Full Disk Access, and it never asks for them:
every sandbox it applies refuses those [private places](DEVELOPMENT.md#macos-private-places), and its
own code stays out of them. A projects folder or project cannot live in one. If macOS shows a prompt
such as "python3.12 would like to access…" or "Python 3.12 would like to access…" while Altitude runs,
click **Don't Allow**; nothing in Altitude depends on the answer.

To review or remove a permission given earlier, open **System Settings → Privacy & Security** and check
**Files & Folders**, **Photos**, **Media & Apple Music**, **Contacts**, **Calendars**, **Reminders** and
**Full Disk Access**. Turn off each switch next to `python3.12` or **Python**; under Full Disk Access,
select it and click **−**. `tccutil` is not needed and is best avoided: these prompts name a Python
program rather than an app bundle, and `tccutil reset` without a bundle identifier clears that
permission for every app on the Mac.

## Validation runs

Task owners run installation VMs, containers and ordinary sandboxed-browser checks through the
[validation runner](DEVELOPMENT.md#validation-runner): one disposable rootless Podman container at a
time, started by altd as the operator's account. Its image, image layers and the cached Ubuntu cloud
image live in `~/.altitude-validation`, beside Altitude's home. A run needs 20 GiB free there, and its own area is removed
when it ends; the first run builds the image, which takes several minutes. Settings → **Validation
runs**, shown only where this computer can run them, turns the runner off: a running run stops and its container and scratch files are removed
after its log and results are retained. Each run appears on its task as a machine run with purpose
`validation`. Activation waits for admitted validation to finish recording evidence, and new runs are
refused while activation or an offered [automatic update](#installed-application-lifecycle) is pending. After an
unexpected daemon exit or host reboot, startup stops abandoned runs, retains their logs and results,
and records them as interrupted. The runner lets its rootless Podman set up again after a restart itself,
so a reboot needs no operator step. If copying evidence fails, the ledger names the original paths
in the runner area; that area stays intact and new runs stay refused pending recovery. A run area
that cleanup cannot remove also keeps new runs refused; each later request retries its removal and
its refusal names what stayed and why, including Podman's own error when Podman could not run.

Fictional browser checks use `alt task validate -- make ui-validate`: the existing Mac Seatbelt
runner with the approved inner-browser-sandbox exception, or the Linux container with Chromium's
sandbox. The command installs tools only in the disposable run and records finite preflight,
journey and cleanup evidence. Stock workers and executable selection stay unchanged. This route
grants no native service operation, Simulator/GUI capability or private/live-content acceptance.
See [fictional browser validation](DEVELOPMENT.md#fictional-browser-validation).

On a Mac, `--simulator` runs add a disposable iOS Simulator iPhone that altd creates in the run's area,
about 3 GB while the run lasts, and removes when it ends, after keeping a screenshot
([iOS Simulator runs](DEVELOPMENT.md#ios-simulator-runs)). The operator's own Simulator devices are not
touched. A phone that cannot be removed keeps its run area and new runs refused, like other leftovers;
each later request and the next start retry its removal.

## Voice input

Dictation turns speech into draft text in every conversation. One machine setting selects how; typing
is never affected.

| Voice choice | How it works | Words appear | Where audio goes | Needs |
| --- | --- | --- | --- | --- |
| Your speech service (Settings) | [A compatible endpoint you configure](#your-speech-service) | after you stop | through this computer to the exact saved endpoint; its policy and charges apply | a reachable compatible endpoint, optional key/model, microphone permission |
| `host` (default where it can run) | [Host voice](#host-voice): the browser streams the microphone to this computer, which transcribes it with its own speech model after a one-time setup. | while you speak, with punctuation and capitals; the last words may still change | to this computer only, over Altitude's own connection; kept in memory for the recording, never stored | `alt voice setup`, Linux x86_64 or a Mac with Apple silicon, Python 3.12 or 3.13, about 2.5 GB free memory while dictating |
| `browser` (default elsewhere) | The browser's speech recognition (Safari on iPhone and Mac, Chrome, Edge). The server is not involved. | while you speak; the last phrase may still change until it is final | Safari recognizes on the device when it can, otherwise through Apple; Chrome and Edge send audio to Google or Microsoft speech services. Firefox and Chromium builds without a vendor key have no recognition and show a typing hint. | HTTPS and a supported browser |

```sh
alt machine show
alt machine set --voice browser --reason 'Recognize in the browser'
alt machine set --unset-voice --reason 'Back to the default'
```

The default follows what this computer can run: host voice where its model runs, browser recognition
elsewhere. A saved choice stays until you change it; host voice that is not set up, failed or cannot
run keeps its choice and says why. First run links optional voice preferences to Settings; the
microphone also offers setup when the selected hosting-computer runtime is absent.

The setting lands in the private `~/.altitude/settings.json` through the machine-settings request.
**Settings → Voice input** edits the same setting as three cards: **This computer**, **Browser
recognition** and **Your speech service**. Each card's **Use …** button saves that choice at once;
**This computer** can be chosen once its voice is ready. Leaving a saved speech service asks first,
because switching removes its saved details. A speech service's fields save with **Use this service**
or **Save**. Setup, update and removal actions are immediate and never change the choice. A Settings read or save updates the next capture in that browser document. Other
documents and CLI changes are picked up when opening Settings, on reload or after a recording is
refused for a changed setting. Recordings identify their backend and host runtime; changing either
cannot silently reroute unfinished audio. Altitude keeps no recordings.

### Your speech service

Choose a compatible endpoint in [Voice settings](SETUP.md#your-speech-service). Recordings stay in
page memory until Stop or Send, then pass through the computer running Altitude to the selected
service. Stop adds words to the draft; Send transcribes and sends to its original conversation.
There are no live partial words, automatic endpoint probes, fallback destinations or recording
retries. The service's own retention policy and any charges apply; Altitude keeps no audio on disk.

Changing the endpoint requires entering its key again. A saved key is never shown; leave the field
blank to retain it for the same exact address, or choose **Remove saved key**. Saving a switch to
another backend deletes the address, key and model. The private machine settings and a pending
settings request contain the key, so protect backups of Altitude's data as credentials. Completed
receipts and event/status output omit service details.

Recordings are limited to ten minutes (about 19.2 MB), with at most two retained captures. Upload
has two minutes and transcription has a separate two minutes. A slow link or server can exceed
those limits. On failure the recording is discarded; typed text and images stay, and a failed Send
returns unsent to its original conversation. Reconnect and record again. Retrying an uncertain
upload could duplicate processing or charges, so Altitude does not replay it. Cancellation discards
the local result but cannot recall audio already received by the service.

Authentication errors link to Voice settings; check the saved key. For unavailable or timed-out
services, check reachability from the Altitude computer and the complete endpoint address. For
invalid or empty responses, check the model and compatible response format. Saving changed service
details or another choice invalidates unfinished recordings; audio is never silently routed to a new
destination. Saving unchanged details leaves them valid.
Typing remains usable after every failure.
Malformed saved service settings remain unavailable without choosing another backend. Re-enter
the service details in Voice settings, or explicitly save a different voice choice.

### Host voice

Host voice runs NVIDIA's Parakeet TDT 0.6B v2 speech model (English, CC-BY-4.0) on this computer's
CPU. Set it up once, from **Settings → Voice input → This computer → Set up voice** or:

```sh
alt voice setup    # downloads about 700 MB once, checks every file, then reports "ready"
alt voice status   # unavailable (with why), absent, setting-up, ready, failed (with why) or outdated
alt voice remove   # stops dictation and frees the disk space
alt machine set --voice host --reason 'Transcribe on this computer'
```

Setup downloads the model from Hugging Face and its runtime (onnxruntime, numpy and the onnx-asr
decoder) from PyPI, pinned by checksum in the Altitude release, into `~/.altitude-speech`, beside
Altitude's home and outside the folders task workers may write. Nothing is installed elsewhere and
Altitude's own Python stays standard-library only. Settings shows progress and
**Cancel setup**; a failed setup shows its reason and **Retry**. At startup after a release changes
the runtime, Altitude makes one automatic update attempt for already-installed, selected host voice.
A runtime that earlier releases kept in `~/.altitude/speech` is deleted at startup, not reused; when
host voice is selected, it sets up again in `~/.altitude-speech`, one download of about 700 MB.
Supported voice stays ready while Settings shows update progress. The update waits for recordings to
finish, checks that the replacement model runs, then switches and removes superseded files. An unsafe
or incompatible runtime cannot keep serving; Settings and the composer say that it needs an update.

Unchanged model files are reused after checksum verification, without a second copy on disk. Updates
download the missing files (about 40 MB for a library-only update, up to about 700 MB) and need free
space for those files and their unpacked libraries. Cancel, failure, interrupted setup or switching to browser
recognition leaves Retry to you, including after restart. `alt voice cancel` also cancels the update.
First installation stays manual. Only the operator invokes setup, cancel and removal; agents cannot.
Removing voice cancels any update, stops dictation and deletes its runtime files.
If removal is interrupted, **Set up voice** restores it; the displayed full-download estimate is an
upper bound, since any surviving verified model files are reused.

The first recording starts a speech process in about 1–3 seconds; speech during startup is kept. While
you dictate it uses about 1.3–2 GB of memory and one to two CPU cores; it exits after fifteen minutes
without voice. It needs about 2.5 GB of free memory to start, and says so when there is less. Words
appear about a second behind speech; Stop or Send lands the final text in about half a second. The
phone sends about 1.9 MB of audio per minute of speech. Host voice runs on Linux x86_64 with glibc 2.28
or newer and on Macs with Apple silicon, with Python 3.12 or 3.13; elsewhere, such as an Intel Mac,
Settings and the composer say it is not available and why. At most two recordings run at once; a third device is told voice is busy.

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
`~/.altitude-speech/worker.log`, and starts, stops and their reasons to altd's log.

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

A gray tile showing a letter instead of the Climb mark means iOS could not fetch the icon, most often
because the phone does not trust that installation's certificate authority. Safari still opens
the page after "visit this website", but iOS fetches the Home Screen icon in a separate request that
refuses the untrusted certificate silently. Every Altitude installation creates its own CA, so
trusting one installation, for example a Linux host, does not trust another, such as a Mac. Open the
address in a new Private tab: if Safari reports the connection as not private,
[set up the phone](SETUP.md#set-up-a-phone) for this installation, confirm the Private tab loads
without a warning, then remove the shortcut and add it again. Each generated CA is named "Altitude
CA" with its own four-character label, which `alt pair` and Settings → Devices show; turn on only
that entry in Certificate Trust Settings. CAs created before the label all share the name "Altitude
local CA" and keep it. Turn on only the one from this installation's verified profile; when such
entries cannot be told apart, remove the Altitude profiles under General → VPN & Device Management,
then install and verify each installation's profile again through its own setup.

The delivered browser checks verify served files and icon geometry at phone and desktop sizes.
Native iOS/Safari and Android/Chrome installation and refresh behavior require device observation;
browser fixtures do not establish it.

### Desktop certificate trust

On the computer hosting Altitude, `alt trust` adds its own CA to this account's browser stores,
the login keychain on a Mac or the Chrome and Firefox NSS databases on Linux, for websites only,
after showing its name, full SHA-256 and scope and asking in the terminal; it prints one undo
command per store. `alt doctor` names which stores trust it and offers `alt trust` until a browser
is paired. A store it cannot write (no `certutil`, a remote Mac terminal, a Flatpak Chrome, a Firefox
Primary Password) stays a step by hand from the
[Linux and macOS trust guide](SETUP.md#trust-https-on-each-device), which also covers every other
desktop; the pair page shows the same steps for the visitor's system and browser. Uninstalling
leaves this trust; a new CA needs it again. For another desktop at an already-configured network
address, **Settings → Devices → Set up a device** or `alt tls-share`
offers the public certificate for ten minutes. Open the link on that computer; no camera is needed.
Compare the downloaded certificate's full SHA-256 with the trusted terminal or Settings screen,
then deliberately import it in the browser or macOS Keychain as the guide describes. Restart the
browser and verify the exact HTTPS URL in a fresh private window without a warning before pairing.
Sharing changes neither host trust stores nor the service's network exposure.

### On iPhone

Open your configured Altitude HTTPS URL through your private network. Safari exposes the microphone only in a
secure context, so the phone must trust the local CA used by Altitude's certificate. Follow the
[phone setup](SETUP.md#set-up-a-phone): **Set up a device** in Settings → Devices on a device that
already trusts Altitude, or `alt tls-share` on the computer running Altitude, shows a QR code that
offers the certificate for ten minutes, and the phone checks its name and SHA-256 before installing. The microphone button remains a typing-only hint on plain
HTTP or an unsupported browser. Safari's Share → Add to Home Screen gives Altitude a Home Screen
icon with its mark; decision alerts on iPhone need Altitude opened from there.

For your own installation, set `ALTITUDE_HOST`/`ALTITUDE_PORT` to its private-network endpoint.
On the next start Altitude reissues its server certificate for that address under the same CA, so
trusted devices need no new step. A generated CA refuses public addresses and names.
`ALTITUDE_TLS_DIR` selects a private certificate directory separate from runtime/project data.
Install the CA on the phone with **Set up a device** or `alt tls-share`, which reads these settings from
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

An iOS 27 Safari report describes speech recognition hearing only a tab's first dictation ([WebKit bug 326069](https://bugs.webkit.org/show_bug.cgi?id=326069)): later ones get
a microphone that delivers silence, after X and Stop alike. Altitude notices that exact silence after three
seconds, stops listening, keeps the draft and says "The microphone went silent. Close and reopen Altitude to dictate again. Typing works." Closing
and reopening the tab restores one more dictation in that report; a page reload does not. This is a candidate cause of a silent restart, not a diagnosis of every affected device. Host
voice, where it runs, does not use Safari's recognizer.

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
