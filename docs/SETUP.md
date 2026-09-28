# Set up Altitude

Altitude targets one operator on a Linux x86_64 machine with a systemd user manager. The release
archive includes the CLI, daemon and built UI; Ubuntu 24.04 is the initial validation target.
Native macOS, Windows and genuine clean-machine/provider acceptance are not established.

The optional [installation lifecycle workflow](DEVELOPMENT.md#installation-lifecycle-acceptance)
exercises the packaged application on disposable Ubuntu 24.04 GitHub runners with fictional data
and fixture engines. Its first hosted execution remains pending until results are recorded.
The same harness runs on a disposable developer VM; it is not an installation command for your
own machine. It covers real user-service activation, HTTPS, update/recovery and uninstall retention,
without establishing browser/device certificate trust, live provider readiness or a minimal OS install.
See the [walkthrough](WALKTHROUGH.md) for the experience and [coverage limits](DEVELOPMENT.md#coverage-and-limits).

## Prerequisites

- Linux with a working systemd **user** manager (`systemctl --user status`) and support for the
  selected engine's sandbox. Both task integrations launch through transient user units, even
  with a foreground Altitude server. Ubuntu 24.04/Python 3.12 is the CI environment; a broader
  compatibility matrix is not established.
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
curl --proto '=https' --tlsv1.2 -fsSL https://github.com/mburakyucel/altitude/releases/download/v0.1.0-rc.1/install.sh | sh
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
Releases that the release workflow publishes from the public repository attest every release file,
so you can check a script's build provenance with the GitHub CLI before running it.
`v0.1.0-rc.1` was published while the repository was private and has no attestation.

```sh
curl --proto '=https' --tlsv1.2 -fsSLO https://github.com/mburakyucel/altitude/releases/download/<version>/install.sh &&
  gh attestation verify install.sh --repo mburakyucel/altitude &&
  sh install.sh
```

On macOS the command stops before downloading anything and reports the macOS version, chip and
Python it found; the native macOS runtime is not delivered yet ([#225](https://github.com/mburakyucel/altitude/issues/225)).

The same installer runs by hand from the release files, for example offline or with a private
archive: download `install.py`, the versioned `.tar.gz` archive and its `.sha256` from the release,
and verify the checksum's source; a checksum from the same untrusted download does not establish
authenticity.

```sh
python3.12 install.py --archive altitude-v0.1.0-rc.1.tar.gz --sha256 '<release SHA-256>'
export PATH="$HOME/.local/bin:$PATH"
alt doctor
```

Installation starts and enables an owned per-user service and prints its HTTPS URL and public
CA fingerprint. It refuses an existing customized service or conflicting `alt` launcher;
migrating a source deployment is explicit. Keep `~/.local/bin` on your shell's PATH.
An initial custom `--prefix` must be empty; updates retain customized hook launchers and refuse
to overwrite them. Resolve the named ownership conflict before retrying.
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
for good: the device then treats Altitude like any trusted site. The installer and `alt doctor` print
the URL, the `ca.crt` path, its SHA-256 fingerprint and these steps in short form (`trust_steps`).

Import only `ca.crt` and compare its SHA-256 fingerprint with the installer or `alt doctor` output.
For another device, transfer it using a cable, verified AirDrop or an existing authenticated
file-transfer channel. Never transfer `ca.key` or `server.key`. Do not bypass a browser warning or
use HTTP to obtain the first trusted certificate. Trust grants the CA authority to identify sites.
A CA that Altitude generates covers only loopback, private-network addresses (10/8, 172.16/12,
192.168/16, 100.64/10, IPv6 unique-local), the private names `localhost`, `.local`, `.internal` and
`home.arpa`, and a DNS name configured when it was created, including that name's subdomains. It
cannot vouch for other public websites; if its key leaked it could still impersonate other
private-network devices, such as a router page, or names under a configured public domain. A CA
created without these limits, or supplied externally, keeps its original scope.

- **Linux Chrome/Chromium:** import the CA as a trusted website authority in the browser's
  certificate manager (`chrome://certificate-manager` in current Chrome). **Firefox:** Settings →
  Privacy & Security → Certificates → View Certificates → Authorities → Import; enable website
  trust. Firefox on Linux may need this separate import even when the OS already trusts the CA.
  [Chromium guidance](https://chromium.googlesource.com/chromium/src/+/HEAD/docs/linux/cert_management.md),
  [Firefox guidance](https://wiki.mozilla.org/CA/Changing_Trust_Settings).
- **Mac clients:** import the CA in Keychain Access and set its SSL trust explicitly. Safari and
  Chrome honor that setting; Firefox normally imports trusted roots from the System keychain,
  otherwise use its Authorities import. This is client guidance, not native Mac runtime support.
  [Apple guidance](https://support.apple.com/en-gb/guide/keychain-access/kyca11871/mac),
  [Firefox platform behavior](https://support.mozilla.org/en-US/kb/setting-certificate-authorities-firefox).
- **iPhone/iPad:** install the certificate profile in Settings, then enable its root under
  General → About → Certificate Trust Settings. Installing the profile alone does not enable TLS
  trust. [Apple guidance](https://support.apple.com/en-us/102390).
- **Android:** Settings → Security → Encryption & credentials → Install a certificate →
  **CA certificate**; names vary by device. Select the transferred public CA and confirm trust.
  [Android guidance](https://android.googlesource.com/platform/cts/+/35dfb1c0b8d%5E%21/).

The CA is valid for ten years; Altitude renews its one-year server certificate automatically and
reissues it when the listening address changes, so devices keep their trust. A device trusts again
only when the CA expires or is replaced, for example after a new installation or a lost key.

Open the exact HTTPS URL without a warning and reload it before adding a home-screen app. Check
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

It prints an eight-character code and a `/pair?code=…` link. Type the code on the device, or open
the link there. A code works once, for ten minutes; a new code cancels the previous one and five
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
A push service that refuses Altitude's default sender address takes one from `ALTITUDE_PUSH_CONTACT`.
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
Altitude checks the result before marking it complete.** The project header's permanent **Setup**
status opens its checklist on phone and desktop. It shows the latest observations, with **Check
again** for a fresh check and relevant actions on incomplete rows.

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
by an update appear without detach/reattach or repeating healthy work. Routine maintenance and
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
| `ALTITUDE_CONFIG` | Installed settings, default `~/.config/altitude/install.json`, outside application/runtime/project directories. CLI overrides are explicit; the generated service pins saved values against its inherited environment. Source checkouts ignore this file. |
| `CODEX_BIN`, `CLAUDE_BIN` | Engine executable locations. The default locations and role/model settings are in the engine configuration module. |
| `ALTITUDE_PUSH_CONTACT` | Address a push service may use to reach the sender of decision alerts, default `mailto:altitude@localhost`. Set a real `mailto:` address if a device's push service refuses that one. |
| `ALTITUDE_PRIMARY_ENGINE` | Tie order in the default Auto top tier; project `--routing` overrides those tiers. |
| `ALTITUDE_UPSTREAM_ISSUE_REPOSITORY` | Initial GitHub `owner/repository` that receives Altitude's own sanitized incident issues, for a non-interactive install. Unset by default: incidents stay on this machine. First run or **Settings → Incident reports** replaces it, including turning publishing off; see [incident publication](OPERATIONS.md#incident-publication). |
| `alt machine set --voice` | Transcription backend: `browser` (default, no setup) or the URL of your OpenAI-compatible speech service, with an optional model and key for hosted providers. See [voice input](OPERATIONS.md#voice-input). |

Quota telemetry is optional. The Monitor shows missing or stale readings rather than assuming
zero usage. Codex readings come from its app-server integration. For Claude usage readings,
`alt install-statusline` installs a global CLI statusline hook and an interactive session supplies
the snapshot; inspect your existing settings before choosing that optional installation. Unknown
readings leave options eligible in Auto and for explicit pins. Within a tied tier, Auto uses
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
for update/recovery and source deployments. Voice input works out of the box through the browser's
own speech recognition, with English punctuated on the device by a model bundled in the archive; a
speech service of your own is optional (see [voice input](OPERATIONS.md#voice-input)). Typing
remains available without either.
Choose **Settings → Voice input** from a project’s three dots (or the desktop operator row).
The overview shows the saved backend; the Voice input page holds its options. Browser recognition
saves immediately. **Your speech service** requires the URL of an OpenAI-compatible
`/v1/audio/transcriptions` endpoint, then **Save service**; a hosted provider's key and model sit
behind **Hosted provider? Add a key or model**. [Your speech service](OPERATIONS.md#your-speech-service)
has a worked example of running one on this computer; the service's own setup and charges apply.
Installation downloads no models and makes no paid provider calls. User conversations and tasks use the account's normal allowance/charges.

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
