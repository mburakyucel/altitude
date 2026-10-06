# Set up Altitude

The [container candidate](CONTAINERS.md) uses container-local tools, sign-ins and project volumes.
Native installation commands below do not install into that image. Linux container onboarding
passes with fictional engines on phone/desktop; actual Mac and account sign-in compatibility remain
unverified.
After container replacement, the browser explains that new AI work is paused and shows the host
Continue command. It retains messages and setup requests until that action; an ordinary restart
of the same container retains its previous admission. See [container recovery](CONTAINERS.md#lifecycle-and-recovery).

Altitude targets one operator on a Linux x86_64 machine with a systemd user manager, or on a Mac with
Apple silicon running macOS 15 or newer. The release archive includes the CLI, daemon and built UI;
Ubuntu 24.04 is the initial validation target. The macOS runtime is implemented and its native
acceptance on a spare account is pending ([roadmap](ROADMAP.md#native-macos-runtime)); Windows and
genuine clean-machine/provider acceptance are not established.

The optional [installation lifecycle workflow](DEVELOPMENT.md#installation-lifecycle-acceptance)
exercises the packaged application on disposable Ubuntu 24.04 GitHub runners with fictional data
and fixture engines. It has not executed on GitHub; the same harness runs in a
[local VM](DEVELOPMENT.md#local-vm-run) or on any disposable developer VM. Neither is an
installation command for your own machine. It covers real user-service activation, HTTPS, update/recovery and uninstall retention (the local VM
also checks the service starts again after a restart and runs the built `install.sh` against a
release server inside the VM),
without establishing browser/device certificate trust, live provider readiness or a minimal OS install.
See the [walkthrough](WALKTHROUGH.md) for the experience and [coverage limits](DEVELOPMENT.md#coverage-and-limits).

## Prerequisites

- Linux with a working systemd **user** manager (`systemctl --user status`) and support for the
  selected engine's sandbox. Both task integrations launch through transient user units, even
  with a foreground Altitude server. Ubuntu 24.04/Python 3.12 is the CI environment; a broader
  compatibility matrix is not established.
- Or macOS 15 or newer on Apple silicon, with the account logged in (the screen may stay locked).
  The service is a LaunchAgent of your login and needs no administrator rights; it starts at login,
  so a Mac that restarts waits for one login, and running before any login is a later increment.
  Put Homebrew's `openssl@3` ahead of `/usr/bin` on PATH (`brew install openssl@3`): macOS's own
  LibreSSL cannot check a certificate's host name. Python 3.12 comes from `brew install python@3.12`
  or python.org, Git from the Xcode command line tools. Each task job runs as its own launchd job,
  also with a foreground Altitude server.
- Python 3.12 or newer, Git, GitHub CLI (`gh`) and OpenSSL on PATH. The archive needs no Node,
  package manager, application source checkout or UI build. The backend uses Python's standard library.
- Access to this repository and to a GitHub project you can fetch, push and open PRs in.
  Authenticate GitHub CLI, verify `gh auth status`, and configure Git name/email and your
  SSH or HTTPS Git credentials. Altitude's delivery path expects a clean primary `main`
  checkout with an `origin/main` branch and the project's applicable checks.
- At least one installed, authenticated **Codex or Claude Code CLI**, usable from the same
  Linux account that runs Altitude. Authenticate using the engine's native setup. CLI versions must support the headless,
  session and permission features in the [launcher](../altitude/engines.py); there is no tested
  version matrix yet. Codex task owners use the CLI's configured model unless an Auto option or
  explicit pin supplies one. Its coordinator runs with user configuration ignored and uses the CLI
  default unless a model override is supplied. Configure Auto with the engine/models you intend to
  use; access to every default preference is not required.

## Install the application

On Linux x86_64 with Python 3.12 or newer and a systemd user manager, one command installs a
published release as the account that will use Altitude. The preview has no stable release yet, so
install the newest release candidate from its own tag:

```sh
curl --proto '=https' --tlsv1.2 -fsSL https://github.com/mburakyucel/altitude/releases/download/v0.1.0-rc.2/install.sh | sh
```

`v0.1.0-rc.1` cannot start its service: systemd refuses the working directory its unit names, so
installation fails at service start and leaves that unit and an interrupted activation behind.
Until they are cleared, `alt recover`, updates and uninstall stop at the refused unit. On a machine
that ran it, remove the unit and complete the activation's recovery, then run the command above;
configuration, TLS identity and data are kept:

```sh
systemctl --user disable altitude.service
rm ~/.config/systemd/user/altitude.service
systemctl --user daemon-reload
~/.local/bin/alt recover
```

`install.sh` belongs to one published release. It checks the machine first and stops with the fix
when something is missing: Linux x86_64, not root, Python 3.12 or newer, `curl`, a SHA-256 tool,
`openssl` and `systemctl --user`. It then downloads that release's archive and `install.py`, checks
each against the SHA-256 written into the script when the release was built, runs
`install.py --archive … --sha256 …` and prints the address, the certificate fingerprint and the next
steps: put `~/.local/bin` on PATH, run `alt doctor`, trust the certificate and open the address.
Nothing is run from a download that does not match, and nothing runs as root. The script is one
function called on its last line, so a download cut off midway does nothing. Once a stable
release exists, `https://github.com/mburakyucel/altitude/releases/latest/download/install.sh`
names the newest one; `latest` skips release candidates.

The command trusts GitHub's HTTPS and the published, immutable release for `install.sh` itself.
Releases that the release workflow publishes from the public repository attest every release file.
A release published while the repository was private, including `v0.1.0-rc.1`, has no attestation,
so this check applies to releases published once it is public. To verify an attested release's script before running it, set `VERSION`
to its tag:

```sh
VERSION=v0.1.0   # an attested release
curl --proto '=https' --tlsv1.2 -fsSLO "https://github.com/mburakyucel/altitude/releases/download/$VERSION/install.sh" &&
  gh attestation verify install.sh --repo mburakyucel/altitude &&
  sh install.sh
```

On macOS the command stops before downloading anything and reports the macOS version, chip and
Python it found: macOS installation waits for native acceptance ([#551](https://github.com/mburakyucel/altitude/issues/551)).
A Mac runs Altitude from a source checkout instead: build the web app, run `bin/alt tls-init`, then
`make install-service`, which installs a LaunchAgent of your login ([operations](OPERATIONS.md)).

The same installer runs by hand from the release files, for example offline or with a private
archive: download `install.py`, the versioned `.tar.gz` archive and its `.sha256` from the release,
and verify the checksum's source; a checksum from the same untrusted download does not establish
authenticity.

```sh
python3.12 install.py --archive altitude-v0.1.0-rc.2.tar.gz --sha256 '<release SHA-256>'
export PATH="$HOME/.local/bin:$PATH"
alt doctor
```

Installation starts and enables an owned per-user service and prints its HTTPS URL and public
CA fingerprint. It refuses an existing customized service or conflicting `alt` launcher;
migrating a source deployment is explicit. Keep `~/.local/bin` on your shell's PATH.
An initial custom `--prefix` must be empty and must not end in whitespace or a backslash; updates
retain customized hook launchers and refuse to overwrite them. Resolve the named ownership conflict before retrying.
`alt doctor` distinguishes configured executable paths, tested local checks and unknown access.
It checks GitHub authentication without a provider request; repository permissions, model access
and each browser's certificate trust remain separately unverified. Follow its actionable failures.
One engine suffices; optional voice, GPU and telemetry do not block typing.

The installation saves its discovered toolchain PATH for service startup. Include the engine and
project test tools before installing; updates preserve the saved environment.
For engines outside their default locations, the existing `CODEX_BIN` or `CLAUDE_BIN` settings
select absolute paths. [Configuration](#configuration-and-limits) describes saved settings.
For nvm installations, Altitude discovers the installed default when Node is absent from PATH;
enable its Corepack pnpm shim for project builds. See [noninteractive toolchain setup](DEVELOPMENT.md#noninteractive-toolchain).

### Installation with a coding agent

An agent running on your machine can help follow this guide, check prerequisites and run the
installer. This is assisted setup, not a validated unattended installation. Use the instructions
shipped with the version you select; the repository's current guide may describe a newer version.
Have the agent check your normal engine and project-tool environment before installing, since
the service saves that PATH. Missing preview artifacts or failed checks need resolution, not a
workaround that disables a safeguard.

Complete authentication in your own terminal or browser; do not paste credentials into the agent
conversation or let it copy credential files. Review any proposed privileged changes or replacement
of existing settings. Certificate trust is your decision: compare the printed fingerprint and
follow the device instructions below. Keep localhost HTTPS and the documented authority settings.
Finish with `alt doctor`, then register your chosen project yourself. Installation help does not
authorize discovering repositories or starting work in them.

The [README prompt](../README.md#get-started) provides a short starting point for this assistance.

### Trust HTTPS on each device

Trusting Altitude's own certificate authority (CA) once on each device removes the browser warning
for good: the device then treats Altitude like any trusted site. `alt doctor` shows the URL, the
CA's name, expiry, SHA-256 fingerprint and what trusting it allows, all read from the certificate
itself, with these steps in short form (`trust_steps`); **Settings → Devices** shows the same
certificate facts on a device that already trusts Altitude.

Only `ca.crt` goes to a device, over any channel: cable, AirDrop, your own email or cloud, or
`alt tls-share` for a phone. Never transfer `ca.key` or `server.key`. The channel does not have to
be trusted; the check before installing is what counts. Confirm the file holds only that certificate,
with the expected name and SHA-256 fingerprint, and delete it if anything differs. Never click through
a browser warning to reach Altitude. Trust grants the CA authority to identify sites, and its name is
whatever it was created with, not necessarily "Altitude".
A CA that Altitude generates covers only loopback, private-network addresses (10/8, 172.16/12,
192.168/16, 100.64/10, IPv6 unique-local), the private names `localhost`, `.local`, `.internal` and
`home.arpa`, and a DNS name configured when it was created, including that name's subdomains. It
cannot vouch for other public websites; if its key leaked it could still impersonate other
private-network devices, such as a router page, or names under a configured public domain. A CA
created without these limits, or supplied externally, keeps its original scope. Altitude shows that
scope as read from the certificate: "No limits" when it has none, and "Any website name" or "any IP
address" for a type of name its limits leave open.

#### Set up a phone

On a device that already trusts Altitude, open **Settings → Devices** and tap **Add a phone** in the
Certificate card. Or, on the computer running Altitude, locally or over SSH, run:

```sh
alt tls-share
```

Both show a QR code for a ten-minute plain-HTTP link on the service's address, beside the CA's name
and SHA-256 fingerprint. Scan it with the phone's camera: the page it opens shows the same name and
fingerprint, an iPhone profile download, a plain certificate download for Android and other
devices, and the steps below. On the same device, tap **Open setup page** in Settings; it opens a
new tab while the original tab keeps the QR code, certificate details and sharing timer. Keep that
original Settings page open during setup. Close and expiry remove the setup link as well as the QR.
The link serves only that page and the public CA certificate, as a
configuration profile holding only the certificate or as the certificate file; it never serves a
key or Altitude itself. Settings shows the time left and **Close**; closing it, leaving the page or
the end of the ten minutes closes the link, and Ctrl-C closes the command's link sooner. A new
**Add a phone** replaces an earlier one. A firewall on that computer can block the link's port;
then use another channel.

`alt tls-share` reads the address, port and certificate folder the running Altitude service
recorded when it started, so the shell's own `ALTITUDE_HOST`, `ALTITUDE_PORT`, `ALTITUDE_TLS` and
`ALTITUDE_TLS_DIR` play no part. Before offering
anything it fetches the service's health over HTTPS, trusting only that folder's CA for the
service's address, and offers only a certificate the service proves it serves under. **Add a phone**
is opened by the service itself and offers the CA it serves under. Its QR code prints black on white
in any terminal, including Altitude's own.

Both stop with the reason when the service serves plain HTTP or listens only on loopback
(`ALTITUDE_HOST` must be the private-network address the phone opens, which takes effect when the
service restarts). `alt tls-share` also stops when no service has recorded its address, or the
service does not answer or answers without proving that certificate. On a Mac, the source
service's LaunchAgent takes its address from `ALTITUDE_HOST` when it is installed:
`ALTITUDE_HOST=<address> make install-service`.

The link is unauthenticated, so the check against the trusted screen is what counts. On an iPhone or
iPad:

1. Open the share link in **Safari**, even if scanning the QR code opened another browser.
   Tap **Download the profile**, then **Allow**. After the download completes, open Settings →
   **Profile Downloaded**. Scanning the QR code alone does not download a profile.
2. Before tapping **Install**, check that it contains only a **Certificate** with the name shown on
   the trusted screen, and that **More Details** → that certificate shows the same SHA-256. If
   anything differs, tap **Remove** and stop: someone else answered the link. If the certificate
   details cannot be viewed, stop before installing and report what the phone shows.
3. Tap **Install**, then turn the certificate on under Settings → General → About → **Certificate
   Trust Settings**. Installing the profile alone does not enable TLS trust.
   [Apple guidance](https://support.apple.com/en-us/102390).
4. Open the HTTPS address in a new Private tab. It must load with no warning; then
   [pair](#pair-each-device) and add the Home Screen app.

**No Profile Downloaded?** The shortcut appears after a profile download; it is not a permanent
Settings item. Check **Settings → General → VPN & Device Management** for profiles as well.
[Apple deletes an uninstalled profile after eight minutes](https://support.apple.com/en-us/102400).
If no profile is present, return to the share page in Safari and download again; open a new sharing
window if the ten-minute link has closed. If no **Allow** prompt appears or the download fails,
report the browser, iOS version and exact message. A working download link does not establish that
iOS accepted a profile. Do not change certificate trust or disable device protections to diagnose this.

Safari may remember an earlier "visit this website" exception, which can hide missing trust in an
ordinary tab. Settings → Safari → **Clear History and Website Data** removes it, and also signs out
every site and unpairs Safari.

On Android, tap **Download the certificate** in Chrome and install the file under Settings → Security → Encryption &
credentials → Install a certificate → **CA certificate** (names vary by device), comparing the
fingerprint where the device shows it. Firefox for Android also needs its third-party CA setting.
[Android guidance](https://android.googlesource.com/platform/cts/+/35dfb1c0b8d%5E%21/).

#### Other devices

- **Linux Chrome/Chromium:** import the CA as a trusted website authority in the browser's
  certificate manager (`chrome://certificate-manager` in current Chrome). **Firefox:** Settings →
  Privacy & Security → Certificates → View Certificates → Authorities → Import; enable website
  trust. Firefox on Linux may need this separate import even when the OS already trusts the CA.
  [Chromium guidance](https://chromium.googlesource.com/chromium/src/+/HEAD/docs/linux/cert_management.md),
  [Firefox guidance](https://wiki.mozilla.org/CA/Changing_Trust_Settings).
- **Mac clients:** import the CA in Keychain Access and set its SSL trust explicitly. Safari and
  Chrome honor that setting; Firefox normally imports trusted roots from the System keychain,
  otherwise use its Authorities import.
  [Apple guidance](https://support.apple.com/en-gb/guide/keychain-access/kyca11871/mac),
  [Firefox platform behavior](https://support.mozilla.org/en-US/kb/setting-certificate-authorities-firefox).

The CA is valid for ten years; Altitude renews its one-year server certificate automatically and
reissues it when the listening address changes, so devices keep their trust. A device trusts again
only when the CA expires or is replaced, for example after a new installation or a lost key.

Open the exact HTTPS URL without a warning, in a new private window, and reload it before adding a home-screen app. Check
the installed app separately: a shortcut or cached page does not prove TLS works. A phone needs
the explicitly configured remote address, not `localhost`. Device/browser acceptance remains
pending until observed. Remove this CA in the same browser/OS certificate manager when retiring
the installation; on iOS remove its profile under General → VPN & Device Management. Do not
clear unrelated credentials. Uninstalling Altitude does not remove trust from your devices.

### Pair each device

Altitude opens only in browsers you pair, including the one on the computer running it. An
unpaired browser shows **Pair this device**. In a terminal on that computer, locally or over SSH,
run:

```sh
alt pair
```

It prints an eight-character code and a `/pair?code=…` link to the running service's address.
Type the code on the device, or open the link there; when the service cannot be found it prints the
code with the reason instead of a link. A code works once, for ten minutes; a new code cancels the previous one and five
wrong codes cancel it. A paired device stays paired for 400 days of disuse and renews while you use
it. A paired device can also make a code in **Settings → Devices** for another one. On an iPhone,
a Home Screen app added after Safari is paired may start already paired: iOS can copy Safari's
cookies into it, and both then share one entry in **Settings → Devices**, so removing that entry
signs out both. A Home Screen app that shows **Pair this device** pairs on its own and appears as
"Home Screen app on iPhone". Clearing a browser's site data unpairs it.

### Home-screen app and bookmarks

Open the trusted HTTPS URL and reload before adding Altitude. On iOS, use Safari's Share → Add to
Home Screen; on Android, use Chrome's menu → Add to Home screen / Install app (wording varies).
The Apple touch icon, browser/bookmark icons and manifest app icons use the approved Climb mark.
The manifest opens the app at `/` in a standalone window where supported. Altitude still needs a
connection to its server; installation adds no offline mode.

For an existing shortcut or installed app showing an older icon, follow the
[icon refresh steps](OPERATIONS.md#refreshing-home-screen-and-bookmark-icons).

### Alerts for new decisions

Needs you offers an alert for each new decision. Turn it on once per device and grant the browser's
notification permission there; trusted HTTPS is a prerequisite, so finish the step above first. An
iPhone shows the switch only for Altitude added to the Home Screen (Safari's Share → Add to Home
Screen, which uses the Altitude mark as its icon) and opened from there.

Turning the switch on also subscribes that device to its browser's push service, so a decision
reaches a closed phone. Altitude signs each push with a key it generates in `~/.altitude/push/` and
sends no payload, so the push service learns only that this device should wake; the device then asks
Altitude what is waiting. On your own network the alert names the project and task, and away from it
the alert says a decision is waiting and nothing more. It needs outbound internet from altd; where a
push service is unreachable the switch says alerts arrive only while Altitude is open, which on a
phone means while it is on screen. Each alert opens that decision and carries no conversation text.
While a push service refuses Altitude's alerts, the line under the switch names it and the reason it
gave; [refused decision alerts](OPERATIONS.md#refused-decision-alerts) lists what each reason needs.
Declining permission, or a browser without notifications, leaves Needs you and typing unchanged.

## First run in the browser

After [pairing](#pair-each-device), with no project managed, the web app opens First run. Its four steps are skippable, go **‹ Back**
without saving, keep their place in the URL across reloads and are each a row in
**Settings → This machine** afterwards:

| Step | What it writes |
| --- | --- |
| **Your name**, filled in from `ALTITUDE_OPERATOR` or Git's global `user.name` | `operator_name` in `$ALTITUDE_HOME/settings.json`. Screens, agent prompts and incident sanitization use it; clearing it returns to the environment value or Git's name, and with neither, screens say “you”. |
| **What the agents need**: the GitHub CLI and a coding agent signed in, Git installed | Nothing. Each unmet check shows the command to run in a terminal on this computer, the install command for a missing tool or the sign-in command (`gh auth login`, the agent's own) for an installed one, with **Copy** and **Check again**. One signed-in agent is enough; the others read as optional. The browser never asks for a password or token. |
| **Report Altitude’s own faults?**, off by default | `incident_repository`: off keeps incidents on this computer; on stores the repository, Altitude's own filled in or a fork you name, after the signed-in GitHub CLI confirms it can see it. |
| **Add your projects** | `projects_folder` when **Change…** picks another folder, and one registration per **Add project** or **Add all**. Only the folders directly inside the projects folder are listed; nothing is created, cloned or scanned. Adding opens the project's Setup and ends First run. |

The environment variables in [configuration](#configuration-and-limits) remain the
non-interactive path: an install that sets them has the name and incident repository in place
before anyone opens the browser, and a saved choice replaces them.

## Register a project and start a conversation

Use a clean primary checkout of a small GitHub project you are comfortable giving the agent
write access to through tasks. Replace the path below with your project; this does not create or
clone one. Read its instructions file (`AGENTS.md` when present, otherwise `CLAUDE.md`), follow its references/imports, and
record its build/test and delivery expectations there if they are not already documented.

```sh
cd /absolute/path/to/example-project

# Registers this existing folder; the running daemon performs routine setup.
alt project add example --path "$PWD"
alt project list

alt service status
```

Open the project's **Setup** checklist to inspect its results. Altitude automatically configures
missing or stale guards that it owns. If another hook system is present, **Review integration**
offers a supported way to use both sets or leaves the current setup intact for discussion.
Auto skips missing CLIs and known exhausted or rejected options.
Its default ties Codex and Claude on their role defaults (a Claude L2 on Opus, L3 on Fable), with
Claude Opus in the next tier as L3's fallback. One installed engine is enough. Availability of a model is unverified until supported evidence says
otherwise; a subscription's plan name is not evidence of model access.

For an account with only Claude Opus, set that preference from another terminal using the same PATH
and Altitude home while the server is running:

```sh
alt project set example --routing 'claude:opus' --reason 'Use the model available on this account'
```

The daemon applies the setting on its next tick. This reason-bearing command is also available to
the project's L3; no restart is needed. See [CLI routing examples](CLI.md#automatic-routing-preferences)
for tied options, fallback tiers, unavailable Fable and strict one-off pins.

Open **https://127.0.0.1:8890/projects/example**, or the URL printed by your installation. Send:

> Describe this project and suggest one small improvement. Let's discuss it before creating work.

You should see your message and the coordinator's reply. The UI calls the coordinator L3. Once
you choose a concrete change, ask it to create a task; use the work panel to open the task, talk
to its owner (L2), and follow its session and PR. You can ask for a merge hold when you want to
review the result before merge. Engine calls use your authenticated account and can consume its
allowance or incur its normal charges.

Register before starting a foreground development server when you want to use `alt chat` immediately:
startup creates the project's coordinator broker. Web conversations also create the broker on
demand, so a project registered after startup can start from the web app. Its First run / Add
project flow registers the folder and opens Setup immediately, using Auto and the project's
preferences. Its folder browser shows folders on the computer running Altitude, whichever device
displays the page. Browsing starts at your home folder and stays inside it, judged after following
links; hidden folders are left out. Each request lists one folder you opened: subfolder names, whether
one is already a project and whether it holds a Git repository, never file names, sizes or contents.
Altitude reads only what your account can read; an unreadable folder shows as such. Folders outside
home are added by typing their path or with `alt project add --path`. Closing the checklist leaves accepted work running. A CLI conversation
also works from another terminal with
the same PATH and Altitude home while the server is running:

```sh
alt --project example chat "Describe this project and suggest one small improvement."
```

## Project setup and repair

**Altitude performs routine setup automatically. If a step fails, L3 helps investigate, and
Altitude checks the result before marking it complete.** **Setup…** in the project's ⋯ menu opens
the checklist on phone and desktop at any time. The project header shows a **Setup** status only while
setup needs attention: it is running, a current requirement is missing or failed, or setup could not
be read. A ready project's header stays quiet. The checklist shows the latest observations, with
**Check again** for a fresh check and relevant actions on incomplete rows.

| Step | What happens |
| --- | --- |
| Project folder | Register the selected folder or reuse its existing registration and history. |
| Git repository | Detect an existing repository and task-delivery prerequisites. A non-Git folder supports conversation with Git tasks unavailable. |
| Project instructions | Show the existing instruction file selected by the rule loader, or explain that none exists. Setup creates or overwrites no instructions. |
| Git guards | Install missing owned guards, refresh stale Altitude guards, or show the custom-hook integration choice. Non-Git folders show Not applicable. |
| Coordinator | Establish the command connection; show the first conversation's actual progress or reuse the existing conversation. |

The checklist distinguishes pending, running, completed, already configured, not applicable and
failed/input-needed outcomes. It reports creation only after an actual write; detection is reuse.
Ready describes these project checks, not every future remote operation or model's authentication.
Setup never initializes Git. Optional capabilities such as voice do not prevent readiness.

Existing projects receive the same current checks as new projects. Missing requirements introduced
by an update appear in the project header without detach/reattach or repeating healthy work. Routine maintenance and
launch checks refresh recognized owned guard paths to the active source; saved task-worktree
overrides receive the same checks. Failed or interrupted introductory
agent calls wait for an explicit Retry; routine maintenance does not repeat them. Refresh, reconnection
and interruption retain operation records; observations reconcile completed writes before retry.
When repair completes during a check, its completed operation and verified receipts refresh together.
A retry accepted while a check is in progress runs as soon as that check finishes.
Unconfirmed results remain unknown until checked.

**Retry** requests the supported programmatic operation again. L3 receives configuration faults and can
investigate with its existing tools or request bounded daemon repair, even when no task can launch.
**Discuss with L3** opens the existing project conversation; it does not send a message or start
another agent. Code fixes follow the usual task/PR process. L3's explanation cannot turn a row
green: programmatic checks verify the actual result.

For supported ordinary custom-hook directories, **Review integration → Use both hook sets** is
an explicit operator choice. Original hook files remain intact; both sets receive the same input
and either can reject the Git operation. **Keep current setup** leaves the conflict unresolved.
Review the displayed hook directory and events. Only combine trusted hooks: they and programs
they call run with Altitude's Git permissions, outside the task agent's sandbox. File checks
detect changed hook scripts; they do not establish the safety of their dependencies.
Changed custom hooks require a renewed choice. Unsupported hook managers and relative custom
hook selections stay unchanged with an explanation and discussion action. Routine
repair cannot make this choice for you. See the
[coordinator repair command and verification procedure](CLI.md#project-setup-and-guard-recovery).

## Configuration and limits

| Setting | Purpose |
| --- | --- |
| `ALTITUDE_HOME` | Runtime state directory, default `~/.altitude`; use the same value for CLI and server. Keep it out of Git. |
| `ALTITUDE_ROOTS` | Initial projects folder(s), colon separated, default `~/Projects`: First run lists the folders directly inside. **Settings → Projects folder** or `alt machine set --projects-folder PATH` replaces it with one folder without a restart; `--unset-projects-folder` returns to this value. `project add --path` also supports other folders. |
| `ALTITUDE_OPERATOR` | Initial name shown for the operator; unset falls back to Git's global `user.name`, and with neither, screens say “you”. First run or **Settings → Your name** stores a name that replaces it; clearing that name returns to this value. |
| `ALTITUDE_HOST`, `ALTITUDE_PORT`, `ALTITUDE_TLS` | Default `127.0.0.1:8890` over HTTPS. Explicit source/development HTTP remains available; TLS failures never select it automatically. |
| `ALTITUDE_TLS_DIR` | Private certificates, default `~/.config/altitude/tls`, outside application/runtime/project writable roots. |
| `ALTITUDE_SOURCE_BRANCH` | The branch a source service runs and self-deploys, default `main`. The service refuses to start unless its checkout is clean, on this branch and not ahead of `origin/<branch>`; task worktrees still branch from `main`. On a Mac, `make install-service` sets it to the checked-out branch when that is not `main`. |
| `ALTITUDE_CONFIG` | Installed settings, default `~/.config/altitude/install.json`, outside application/runtime/project directories. CLI overrides are explicit; the generated service pins saved values against its inherited environment. Source checkouts ignore this file. |
| `CODEX_BIN`, `CLAUDE_BIN` | Engine executable locations. The default locations and role/model settings are in the engine configuration module. |
| `ALTITUDE_PUSH_CONTACT` | Address a push service may use to reach the sender of decision alerts, default `mailto:altitude@example.com`, which names no one. Set your own `mailto:` address if a device's push service refuses that one. |
| `ALTITUDE_PRIMARY_ENGINE` | Tie order in the default Auto top tier; project `--routing` overrides those tiers. |
| `ALTITUDE_UPSTREAM_ISSUE_REPOSITORY` | Initial GitHub `owner/repository` that receives Altitude's own sanitized incident issues, for a non-interactive install. Unset by default: incidents stay on this machine. First run or **Settings → Incident reports** replaces it, including turning publishing off; see [incident publication](OPERATIONS.md#incident-publication). |
| `alt machine set --voice` | Transcription backend: `host` (this computer transcribes live after `alt voice setup`; the default where its model runs) or `browser` (no setup; the default elsewhere, including macOS for now). See [voice input](OPERATIONS.md#voice-input). |

Quota telemetry is optional. The Monitor shows missing or stale readings rather than assuming
zero usage. The daemon reads both accounts itself: Codex through its app-server integration and
Claude through its headless usage command. Neither needs an interactive session or a change to your
global CLI settings. Unknown readings leave options eligible in Auto and for explicit pins. Within a tied tier, Auto uses
configured order when weekly readings are unknown or incomparable, retaining a current L3 option
in that tier. It never invents separate model allowances from a shared account reading.

Single-engine Auto and explicit pins are supported; arbitrary provider/access configurations are not verified.
In particular, the launcher filters some engine environment variables and supplies role settings;
the Codex coordinator ignores user configuration, including custom provider settings in that file.
Do not assume an interactive API/Bedrock configuration transfers unchanged to a launched session.
See the [engine boundary and gaps](ARCHITECTURE.md#engine-integration-boundary).

The installer generates the user service from this installation's paths. It keeps immutable
application versions under `~/.local/share/altitude`, configuration/TLS under `~/.config/altitude`,
runtime state under `ALTITUDE_HOME`, and project checkouts/worktrees in their existing locations.
Keep those directories separate. Updates retain previous versions and settings.

Remote access is explicit: bind the specific private interface/address your devices reach (a
wildcard bind is certified for `localhost` only) and arrange firewall/network access. There is no application login layer; HTTPS authenticates the
server and encrypts traffic, not the person opening it. Existing explicitly configured addresses
and external certificate directories remain explicit choices. See [operations](OPERATIONS.md)
for update/recovery and source deployments. Voice input runs on this computer by default where its
speech model can run: first run offers the one-time download of about 698 MB, checked against the
release (**Set up voice**, **Use browser recognition instead** or **Skip**; see [host voice](OPERATIONS.md#host-voice)).
Elsewhere, including macOS for now, voice uses the browser's own speech recognition, with English
punctuated on the device by a model bundled in the archive (see [voice input](OPERATIONS.md#voice-input)).
Typing remains available without either.
Choose **Settings → Voice input** from a project’s three dots (or the desktop operator row).
The overview shows the saved backend; the Voice input page holds its two choices, **This computer**
(with **Set up voice** and its progress) and **Browser recognition**, each saved immediately.
Installation itself downloads no models and makes no paid provider calls. User conversations and tasks use the account's normal allowance/charges.

## Remote macOS validation

The [remote validation target](DEVELOPMENT.md#remote-macos-validation) is an opt-in Linux submitter
and Apple-silicon Mac executor. Its code and deterministic fixtures do not establish installed or
native-verified service. Installation requires its own scoped machine authority; proposal or merge
approval does not grant it. No paid runner, general remote shell or live-provider test is included.
The Mac's existing Altitude can own preparation and verification, so the operator handles only
administrator authentication and unavoidable physical/guest setup interactions.

Prepare these inputs before installation:

- A private route between the machines, verified Mac host key, and a dedicated SSH key used only
  by this relay. Restrict its Mac authorization to the Linux source and the fixed forced command;
  password/interactive authentication, forwarding, PTY and arbitrary commands are unavailable.
- A dedicated **standard** Mac runner account with an active GUI login, separate from everyday
  data. Existing Remote Login/private routing must be authorized separately if absent. FileVault
  unlock after reboot and first host/guest GUI login may need the operator at the laptop.
- Administrator-owned canonical Python and pinned `macosvm` executables, enough storage for the
  guest's full logical disk plus 2 GiB, and a shut-down offline macOS template. Its single disk is
  at most 64 GiB; it has at most four vCPUs/8 GiB RAM and no network/audio/serial/host shares.
  Setup Assistant or licensing steps may require the operator; neither runtime bypasses them.
- Inside that template, a fictional standard guest user with GUI login and no sudo privileges,
  reviewed Python/Git/Node/pnpm/browser tools, the suite's image converter/color libraries
  (`ffmpeg`, `lcms2`, `webp` on macOS), and writable disposable caches. Provision exact
  offline packages/browser binaries and record dependency fingerprints from reviewed main. No
  coding credentials, personal data or operator account state belong in the template.

The Linux installer is `python3 scripts/setup_validation_relay.py --install --config PRIVATE_JSON`,
executed only as the separately authorized administrator action. Without `--install` it makes no
changes. Its private JSON contains `daemon_uid`, `daemon_cgroup`, `daemon_executable`, `endpoint`,
`account`, `identity_file` and `known_hosts`; never paste their values into task evidence. It copies
reviewed code/configuration into administrator-owned locations, creates the distinct relay identity,
and installs the relay plus keyless identity-verifier socket/service units. The verifier runs as
altd's UID without capabilities, remote networking or a key. Admission requires kernel peer-process
handles and authenticated manager evidence; unavailable evidence refuses the request. The setup
trusts administrator-maintained OS Python and its system packages. Reinstallation refreshes only
the relay/verifier services after active transfers finish; it does not restart Altitude.
[Architecture](ARCHITECTURE.md) states the daemon/admin trust boundary.

Prepare the Mac and its guest with `scripts/setup_validation_mac.py`. Its read-only entry points are:

```sh
python3 scripts/setup_validation_mac.py --source-digest
python3 scripts/setup_validation_mac.py --plan PRIVATE_JSON
```

The private, mode-0600 plan uses `version: 1`, `mode` (`host` or `guest`), `account`, canonical
`python` and the reviewed code-set `source_sha256`. A host plan also names `template`, its manifest
`template_sha256`, the dedicated Ed25519 `public_key` file and restricted private `source_network`
(exactly one source address, with a /32 or /128 prefix).
A guest plan adds `candidate` (the reviewed dependency checkout), `fingerprints`, `store`, `browsers`
and trusted tool `path`. Fingerprints include at least `web/package.json` and `web/pnpm-lock.yaml`,
plus every other manifest governing provisioned dependencies. Keep all plan values local.

Adding `--apply` to the checked plan is the separately authorized administrator action. The installer
accepts a fresh destination only: it neither refreshes nor replaces an existing installation. It
copies reviewed code and the pinned host template, or installs the guest LaunchDaemon. Host setup
adds an account-specific SSH include and verifies its effective restrictions while preserving
existing configuration. It does not create accounts, enable/reload SSH, establish networking or
start/restart services. Host admission starts disabled until scoped native preflight succeeds;
the guest LaunchDaemon becomes eligible at the next guest boot. Refresh/removal needs its own
reviewed scoped procedure, with no implicit overwrite or automatic activation.

The protected Mac configuration is
`/Library/Application Support/AltitudeValidation/broker.json`: version, dedicated runner UID,
private state/template paths and the canonical installed Python. The state directory is runner-owned
0700 beneath administrator-owned parents. Configuration, imported code, interpreter, template and
VM executable are administrator-owned and not group/other writable. The SSH entrypoint accepts only
`altitude-validation-v1`; it never takes a caller-selected configuration, template or executable.
The template manifest records exact file and VM-executable SHA-256 identities.

Inside the guest, `/Library/Application Support/AltitudeValidation/guest.json` records the fictional
user, trusted tool PATH, dependency fingerprints and offline store/browser paths. An installed root
LaunchDaemon invokes the protected guest supervisor with isolated Python. The supervisor mounts
the two input/results shares with root UID/GID mapping, drops candidate execution to the fictional
user's GUI context and keeps its receipt/log separate from candidate-writable artifacts.

Before enabling real submissions, record native preflight for daemon admission and worker/child
refusal, manager/socket spoofing and identity drift refusal, SSH restrictions, offline guest network
and filesystem boundaries, root-only receipt/log shares, Chromium's own sandbox, effective resource
limits, interruption/expiry cleanup and result acknowledgement. Then record one exact-revision
Linux-to-Mac command and an unavailable-Mac attempt. Neither run nor preflight is recorded yet.
The physical laptop and ARM64 container gaps remain explicit in the
[validation matrix](DEVELOPMENT.md#validation-environments). Dependency refresh, credential rotation
and removal follow [operations](OPERATIONS.md#remote-macos-validation) under their scoped authority.

## When something does not work

- **The page does not load:** inspect `alt service status` and `alt service logs`, the printed HTTPS
  address and certificate trust. Do not disable TLS to bypass trust, hostname or expiry errors.
- **The first conversation fails:** verify the chosen CLI works as this user, its binary path
  and model configuration, and the systemd user manager. For immediate `alt chat` use, register
  before foreground startup; a web conversation can start its coordinator broker on demand.
- **No configured option is available:** inspect the route explanation. Install or authenticate an
  intended CLI, wait for a reported quota reset, or set an available preference. Explicit model/access
  rejections are remembered for thirty minutes; model rejections affect only that model and
  authentication rejections affect that engine. A strict pin must itself become usable or be changed.
- **A task cannot start or land:** inspect its reason, the clean `main`/`origin/main` checkout,
  Git guards, GitHub authentication and applicable check results. Do not bypass a guard.
  For dirty main, L3 or the operator explicitly requests `alt task preserve-checkout <slug> --reason '…'`
  for an unlaunched blocked task. A local archive branch retains the working snapshot and its staged
  parent; task status records the branch and SHA. Review/apply in the owner's isolated worktree and
  deliver through a PR; applying the complete snapshot flattens staging intent. Ignored files stay
  untouched. Archives are never automatically pushed or deleted, and legacy stash records remain
  recoverable. See [Recovering dirty main](OPERATIONS.md#recovering-dirty-main) for inspection,
  interruption handling and the separate resume step.
- **Usage is unknown:** inspect Monitor's explanation and the optional telemetry setup above.

Report setup friction with the command, OS/architecture, application version/commit, engine/browser
versions and sanitized error through
[Feedback](../README.md#feedback). These commands are checked against the CLI/source and repository
tests; installation on a second clean machine remains an explicit
[onboarding follow-up](ROADMAP.md#early-user-onboarding-and-public-release).
