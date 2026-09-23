# Set up an early private preview

Altitude targets one operator on a Linux x86_64 machine with a systemd user manager. The private
archive includes the CLI, daemon and built UI; Ubuntu 24.04 is the initial validation target.
Native macOS, Windows and genuine clean-machine/provider acceptance are not established.
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

Obtain `install.py`, the versioned `.tar.gz` archive and its SHA-256 checksum through the approved
private release channel. The example version below is a placeholder, not a published release.
Verify the source of the installer and checksum; a checksum from the same untrusted download
does not establish authenticity. Run these commands as the account that will use Altitude:

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

### Trust HTTPS on each device

Import only the printed `ca.crt` file and compare its SHA-256 fingerprint with the local installer
output. For another device, transfer it using a cable, verified AirDrop or an existing authenticated
file-transfer channel. Never transfer `ca.key` or `server.key`. Do not bypass a browser warning or
use HTTP to obtain the first trusted certificate. Trust grants the CA authority to identify sites.

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

Open the exact HTTPS URL without a warning and reload it before adding a home-screen app. Check
the installed app separately: a shortcut or cached page does not prove TLS works. A phone needs
the explicitly configured remote address, not `localhost`. Device/browser acceptance remains
pending until observed. Remove this CA in the same browser/OS certificate manager when retiring
the installation; on iOS remove its profile under General → VPN & Device Management. Do not
clear unrelated credentials. Uninstalling Altitude does not remove trust from your devices.

### Alerts for new decisions

Needs you offers an alert for each new decision. Turn it on once per device and grant the browser's
notification permission there; trusted HTTPS is a prerequisite, so finish the step above first. An
iPhone shows the switch only for Altitude added to the Home Screen and opened from there.

Turning the switch on also subscribes that device to its browser's push service, so a decision
reaches a closed phone. Altitude signs each push with a key it generates in `~/.altitude/push/` and
sends no payload, so the push service learns only that this device should wake; the device then asks
Altitude what is waiting. On your own network the alert names the project and task, and away from it
the alert says a decision is waiting and nothing more. It needs outbound internet from altd; where a
push service is unreachable the switch says alerts arrive only while Altitude is open, which on a
phone means while it is on screen. Each alert opens that decision and carries no conversation text.
A push service that refuses Altitude's default sender address takes one from `ALTITUDE_PUSH_CONTACT`.
Declining permission, or a browser without notifications, leaves Needs you and typing unchanged.

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
Its default ties Codex's default model and Claude Fable, with Claude Opus in the next tier. One
installed engine is enough. Availability of a model is unverified until supported evidence says
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
preferences. Closing the checklist leaves accepted work running. A CLI conversation
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
| `ALTITUDE_ROOTS` | Colon-separated parent folders scanned by First run, default `~/Projects`; `project add --path` also supports other folders. |
| `ALTITUDE_OPERATOR` | Name shown for the operator; defaults to “Operator”. |
| `ALTITUDE_HOST`, `ALTITUDE_PORT`, `ALTITUDE_TLS` | Default `127.0.0.1:8890` over HTTPS. Explicit source/development HTTP remains available; TLS failures never select it automatically. |
| `ALTITUDE_TLS_DIR` | Private certificates, default `~/.config/altitude/tls`, outside application/runtime/project writable roots. |
| `ALTITUDE_CONFIG` | Installed settings, default `~/.config/altitude/install.json`, outside application/runtime/project directories. CLI overrides are explicit; the generated service pins saved values against its inherited environment. Source checkouts ignore this file. |
| `CODEX_BIN`, `CLAUDE_BIN` | Engine executable locations. The default locations and role/model settings are in the engine configuration module. |
| `ALTITUDE_PUSH_CONTACT` | Address a push service may use to reach the sender of decision alerts, default `mailto:altitude@localhost`. Set a real `mailto:` address if a device's push service refuses that one. |
| `ALTITUDE_PRIMARY_ENGINE` | Tie order in the default Auto top tier; project `--routing` overrides those tiers. |

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

Remote access is explicit: configure a controlled private interface/address, matching certificate
host and firewall/network access. There is no application login layer; HTTPS authenticates the
server and encrypts traffic, not the person opening it. Existing explicitly configured addresses
and external certificate directories remain explicit choices. See [operations](OPERATIONS.md)
for update/recovery and source deployments. Voice needs `ffmpeg` and a compatible local speech
service; typing remains available without them. Installation downloads no models and makes no
paid provider calls. User conversations and tasks use the account's normal allowance/charges.

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
