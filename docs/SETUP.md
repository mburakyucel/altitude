# Set up Altitude

Altitude runs for one person on Linux 6.5 or newer with a systemd user manager, or on macOS 15 or newer. Follow
these steps top to bottom on the computer that will run it. To run it in a Linux container
instead, see [container deployment](CONTAINERS.md).

Optional [host voice](OPERATIONS.md#host-voice) needs a one-time download started by you. Once installed
and selected, it receives one automatic update attempt when a release changes its runtime. Supported
voice remains usable; failed or cancelled updates wait for your Retry in Settings.

## Prerequisites

- **Linux** 6.5 or newer with a working systemd user manager (`systemctl --user status` answers), or **macOS 15
  or newer** with your account logged in (the screen may stay locked).
- **Python 3.12 or newer, Git, `curl` and OpenSSL.** On a Mac, run
  `brew install python@3.12 openssl@3`; Git comes with the Xcode command line tools. Altitude
  finds Homebrew's `openssl@3` even when macOS's LibreSSL comes first on PATH; another OpenSSL 3
  must be on PATH. Linux uses OpenSSL on PATH. The installer stops before downloading if a required
  tool is missing.
- **bubblewrap on Linux** (`sudo apt install bubblewrap` on Ubuntu), the sandbox Altitude runs Claude agents in;
  `alt doctor` shows whether it is installed. macOS has its sandbox built in.
- **GitHub CLI (`gh`) 2.72 or newer.** Debian and Ubuntu package an older release that fails at the
  first landing, so install it from
  [GitHub's package repository](https://github.com/cli/cli/blob/trunk/docs/install_linux.md);
  Homebrew's `gh` is current. Sign in and let Git use that sign-in:

  ```sh
  gh auth login
  gh auth setup-git
  gh auth status
  ```

  Set Git's `user.name` and `user.email` too.
- **Codex or Claude Code**, installed and signed in with its own setup as the same account. One is
  enough. For a CLI outside its default location, set `CODEX_BIN` or `CLAUDE_BIN` to its absolute
  path before installing. Altitude's Claude runs use a sign-in of their own from your subscription,
  so your own Claude sign-in and settings stay yours: once installed, run `alt sign-in claude`, which
  opens Claude's sign-in in your browser and then asks you to paste the token it prints at a hidden
  prompt and press Enter on an empty line. Run it in a terminal of your own rather than one inside
  Altitude, whose output an agent can read. It lasts a year; two weeks before it ends, project chat reminds you to run it again.
- **A GitHub project** you can push to and open pull requests in, with a clean `main` checkout that
  tracks `origin/main`.

Install from a shell whose PATH finds your coding CLI and your project's build and test tools: the
service keeps that PATH. With nvm, see [noninteractive toolchain](DEVELOPMENT.md#noninteractive-toolchain).

## Install the application

Run this as the account that will use Altitude, not as root. The default starts with access only
on the hosting computer; other-device access is an optional choice afterwards:

```sh
curl --proto '=https' --tlsv1.2 -fsSL https://github.com/mburakyucel/altitude/releases/latest/download/install.sh | sh
```

The script checks the machine first and stops with the fix for anything missing. It downloads the
latest stable release anonymously, runs nothing that does not match the checksums built into it,
installs into your home folder and starts Altitude as a per-user service. It ends by printing the
service's state and where it runs, the certificate's name and full fingerprint, whether Altitude
keeps running after logout, the next steps and whether other devices can reach the address. On a
fresh installation without paired devices or an unexpired pairing code, it also prints a private
link for the first browser after verified HTTPS readiness. Before the link, in a terminal on the
computer running Altitude, the installer asks whether to
[trust the certificate on this computer](#on-the-computer-running-altitude); answer yes, restart the
browser and open the link. Otherwise [trust the certificate](#trust-https-on-each-device) by hand,
comparing its name and full fingerprint with the installer's output, then open that link: the
browser pairs automatically after its trust check and opens [First run](#first-run-in-the-browser). The link works once, for ten minutes. Keep it
private, including in terminal recordings and screenshots. If it expires or is refused, run
`~/.local/bin/alt pair` on the computer running Altitude and follow [manual pairing](#pair-each-device).
Closing the terminal or the browser leaves the service running.

The installer opens no browser and changes trust only after your yes. Clicking the link uses the terminal
or operating system's normal link handler, usually the default browser; some terminals require
a modifier click. Over SSH, that browser opens on the device viewing the terminal, where
`127.0.0.1` refers to that device, not the server, so the installer prints the tunnel to run on your own
computer first, `ssh -L 8890:127.0.0.1:8890 <this server>` with the installed port; the link then opens
there, and the installer does not offer to trust the certificate on the server. The installer creates no tunnel itself. Lasting remote use needs the configured reachable
address, its certificate trust and network access.

Reruns and updates retain paired devices and existing codes and print no new pairing link.
A fresh installation retaining an unexpired manual code leaves it intact; run
`~/.local/bin/alt pair` when ready to replace it. If link issuance fails after the service starts,
the installer reports the running installation and the same manual recovery command.

Terminal setup is optional before First run, which checks tools and sign-ins. If `alt` is absent
in a new shell, use `~/.local/bin/alt` and put `~/.local/bin` on PATH in your shell profile:

```sh
export PATH="$HOME/.local/bin:$PATH"
```

`alt doctor` and `alt service status` lead with whether the service runs, its actual address and
which devices can reach it, verified against the native process and TLS health. They separate the
saved configuration from shell overrides they ignore. `alt doctor` then names the one next step from
current state: until a browser is paired, `alt trust` outside an SSH session while one of this
computer's browsers does not trust the certificate, then the installer's private link while it still
works, else trusting the certificate and running `alt pair`; after that the first missing tool with
its command, such as a coding agent to install. It also checks tools and sign-ins, shows the command that fixes each
failure, names which of this computer's browser stores trust the certificate and counts paired
browsers; `--json` gives structured output for automation. Neither can see whether other devices
trust Altitude's certificate or whether a coding provider accepts your account.

The command trusts GitHub's HTTPS and the published release for `install.sh` itself. To verify the
script before running it with GitHub's release attestation, run the commands below; to install one
exact release, set `VERSION` to its tag from the [releases](https://github.com/mburakyucel/altitude/releases):

```sh
VERSION=$(gh release view --repo mburakyucel/altitude --json tagName --jq .tagName) &&
curl --proto '=https' --tlsv1.2 -fsSLO "https://github.com/mburakyucel/altitude/releases/download/$VERSION/install.sh" &&
  gh release verify-asset "$VERSION" install.sh --repo mburakyucel/altitude &&
  sh install.sh
```

Altitude installs newer releases by itself at a quiet moment; running tasks keep going. Turn
**Automatic updates** off in **Settings → About** to confirm each update yourself. If an
installation or update is interrupted, `alt recover` restores the previous version. `alt uninstall`
removes the service and the `alt` command and keeps your settings, certificates, paired browsers and
data, so installing again continues the same installation rather than starting a fresh first run. See the
[installed application lifecycle](OPERATIONS.md#installed-application-lifecycle) for all three.

### Optional other-device access

**Settings → Network** shows the daemon's hosting computer, URL and listening scope. A localhost
certificate URL is separate from listener exposure: a wildcard listener listens on all interfaces
even when its certificate URL is localhost. Neither proves another device can reach it. Container
listeners are inside the container; its host launcher controls port publishing. While Altitude
listens only on the hosting computer, the page says other devices cannot reach it yet and points
to changing the address; a VPN changes neither the address, certificate trust nor pairing.

For a packaged installation, choose a concrete private LAN/VPN address already assigned to the
hosting computer: loopback, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, the `100.64.0.0/10`
range many mesh VPNs use, or unique-local IPv6 (`fc00::/7`), the ranges Altitude's certificate
authority may certify. Preview the change, then explicitly apply it:

```sh
alt service configure --host <private-address>
alt service configure --host <private-address> --apply
```

Both IPv4 and IPv6 addresses use matching daemon listeners; IPv6 URLs enclose the address in brackets.
The preview shows current/proposed URLs, the retained CA fingerprint, certificate action and
`alt recover`. Apply retains the port, CA, keys, pairing and projects, updates the owned service
and verifies its HTTPS API and UI directly, without ambient proxies, if running. Failure restores the original configuration and
certificate. An interruption leaves a receipt for `alt recover`; a stopped service stays stopped.
External certificates must already cover the new address. Wildcard, public and link-local binds
are outside this operation. Preview and apply `--host 127.0.0.1` to return to local-only access.

On the same LAN, the other device must reach that private address and port. Away from home,
arrange a separate private VPN, such as [WireGuard](https://www.wireguard.com/quickstart/), first.
The host must be available. A VPN changes neither Altitude's bind address nor certificate trust
nor pairing. Address changes or lost VPN connectivity can make the URL unavailable. Source and
[container deployments](CONTAINERS.md) use their own deployment configuration, not this command.

### Keep running after logout

On Linux, installation turns on lingering for your account so the service keeps running after you
log out and starts at boot. Minimal and cloud images without polkit refuse that; installation then
completes and prints the one command to run:

```sh
sudo loginctl enable-linger <account>
```

`alt doctor` shows the state in its **runs without a login** row. `loginctl disable-linger` keeps
Altitude to your login sessions; updates and uninstall leave the setting as it is.

On a Mac, Altitude runs while you are logged in, with the screen locked too, and starts again at
your next login. macOS cannot run it before anyone logs in; with FileVault on, typing your password
at the startup screen is that login.

### Install by hand

Without internet access on the machine, download `install.py`, the `altitude-<tag>.tar.gz` archive
and its `.sha256` from the [release](https://github.com/mburakyucel/altitude/releases), take the
checksum from a source you trust, and run:

```sh
python3.12 install.py --archive altitude-<tag>.tar.gz --sha256 '<release SHA-256>'
export PATH="$HOME/.local/bin:$PATH"
alt doctor
```

### Installation with a coding agent

A coding agent on this machine can follow this guide with you, using the
[README prompt](../README.md#get-started). Sign in to GitHub and your coding CLI yourself, in your
own terminal; never paste credentials into the conversation or let the agent copy credential files.
Review any privileged command or change to existing settings before it runs. Trusting the
certificate and choosing a project stay your decisions.

## Trust HTTPS on each device

Altitude serves HTTPS with its own certificate authority (CA). Trust that CA in every browser you
use, including on the computer running Altitude. The installer shows the public `ca.crt` path,
CA name and SHA-256 fingerprint. `alt doctor` shows the path and the
CA's name, SHA-256 fingerprint, expiry and what trusting it allows; **Settings → Devices** shows the
same on a device that already trusts Altitude.

- Only `ca.crt` goes to another device, by any channel. Never copy `ca.key` or `server.key`.
- Before trusting a copy, compare its name and full SHA-256 fingerprint with the installer,
  `alt doctor` or **Settings → Devices** on a trusted device. Delete it if anything differs.
- Never click through a browser warning to reach Altitude.

A CA that Altitude generates can vouch only for loopback and private-network addresses, the private
names `localhost`, `.local`, `.internal` and `home.arpa`, and a DNS name configured when it was
created, with its subdomains. It cannot vouch for other public websites, but if its key leaked it
could impersonate other devices on your private network, such as a router's page. A CA supplied
from elsewhere, or created without these limits, keeps its own scope; read the scope `alt doctor`
reports before trusting a CA, where "No limits" means any website. The CA is valid for ten years,
and Altitude renews its server certificate by itself, so a device trusts it once.

### On the computer running Altitude

```sh
~/.local/bin/alt trust
```

It shows the certificate's name, full SHA-256 fingerprint and scope and the browser stores it would
change, then asks `Trust it? [y/N]` in the terminal; nothing changes unless you answer yes. Over SSH
it says that it changes the browsers of the computer running Altitude, not of the one you type on. It trusts
only the certificate Altitude created, as the running service proves it serves, and only to identify
websites, without administrator rights:

- **macOS:** your login keychain, which Safari, Chrome, Edge and Firefox read. macOS asks for your
  password in a window of its own; from a remote terminal it refuses, so use the
  [macOS steps](#macos-desktop) instead.
- **Linux:** each browser certificate database in your home folder: Chrome, Chromium, Edge and Brave
  share `~/.pki/nssdb`, snap Chromium keeps its own, and each Firefox profile has one. Writing them
  needs NSS's `certutil` (`libnss3-tools` on Ubuntu and Debian, `nss-tools` on Fedora); if it is
  missing, `alt trust` prints the command to install it, which you run yourself. Chrome reads
  `~/.pki/nssdb` without creating it, so `alt trust` creates it when Chrome, Chromium, Edge or Brave is
  installed. A Firefox never opened has no profile yet: open it once, then run `alt trust` again. The system-wide store needs
  administrator rights and these browsers do not read it, so it stays unchanged. A Flatpak Chrome
  and a Firefox profile with a Primary Password are named for [importing by hand](#linux-desktop).

Restart the browser afterwards. Run again, it changes nothing where the certificate is already
trusted. It prints the commands that undo it, one per store, for example:

```sh
security delete-certificate -Z <SHA-256> -t "$HOME/Library/Keychains/login.keychain-db"   # macOS
certutil -D -d sql:"$HOME/.pki/nssdb" -n '<name it printed>'                            # Linux
```

Removing the certificate in Keychain Access or the browser's certificate manager does the same. A
certificate Altitude did not create is trusted by hand, as below.

### Share with a desktop or phone

On the computer running Altitude without `alt trust`, use the `ca.crt` path the installer or
`alt doctor` reports and continue with [Linux](#linux-desktop) or [macOS](#macos-desktop). On
another computer, the **Pair this device** page shows the steps for its system and browser, with
a **Steps for** menu to pick another. Another device needs Altitude on a
[private-network address](#install-the-application). Open **Settings → Devices → Set up a device**
on a trusted device, or run this on the computer running Altitude, locally or over SSH:

```sh
alt tls-share
```

Both show a QR code and a link that works for ten minutes. It serves only a setup page with the
steps for each kind of device and the public certificate, never a key or Altitude itself. The page
cannot vouch for itself, so compare the certificate with the trusted screen before installing it.
If a firewall blocks the link, copy `ca.crt` by another channel.

### Check a downloaded certificate

From the folder holding `ca.crt`, run:

```sh
openssl x509 -in ca.crt -noout -subject -fingerprint -sha256
```

The name and the entire fingerprint must match the installer or `alt doctor` on the computer
running Altitude. Open the file in a text editor as well: it holds exactly one `BEGIN CERTIFICATE`
/ `END CERTIFICATE` block and nothing else.

### Linux desktop

After [checking the file](#check-a-downloaded-certificate), import it in each browser and profile
you use:

- **Chrome or Chromium:** open `chrome://certificate-manager` → **Local certificates** → **Custom →
  Installed by you** → **Trusted Certificates** → **Import**, and select `ca.crt`. On older builds,
  open `chrome://settings/certificates` → **Authorities** → **Import** and allow it to identify
  websites. Do not use **Your certificates**.
- **Firefox:** open **Settings → Privacy & Security → Certificates → View Certificates → Authorities
  → Import**, select `ca.crt` and tick **Trust this CA to identify websites**.

Quit and reopen the browser, then [verify HTTPS](#verify-https-before-pairing). For the first browser,
reopen the original private link from the installer; closing or reloading the page clears its code.
If it expires, run `~/.local/bin/alt pair` for a new code.

### macOS desktop

1. [Check the file](#check-a-downloaded-certificate).
2. Open **Keychain Access** and drag `ca.crt` into the **login** keychain.
3. Double-click the certificate, expand **Trust** and set **Secure Sockets Layer (SSL)** to
   **Always Trust**. Close the window and confirm with your password.
4. Quit and reopen Safari or Chrome, then [verify HTTPS](#verify-https-before-pairing).

Firefox reads this trust while **Allow Firefox to automatically trust third-party root certificates
you install** is on, the default in current versions; otherwise import the same file with the
Firefox steps above.

For the first browser, reopen the original private link from the installer after restarting;
the cleaned page address cannot restore its code. If it expires, run `~/.local/bin/alt pair`
for a new code.

### Set up a phone

The quickest way is pairing: run `alt pair` on the computer running Altitude and scan its QR code.
Safari warns that the connection is not private until the phone trusts Altitude; tap **Show
Details** → **visit this website**. The **Pair this device** screen walks the certificate steps
below and ticks **Trusted** once the phone really trusts it, then takes the code. To install the
certificate without pairing, open the [setup link](#share-with-a-desktop-or-phone).

**iPhone or iPad:**

1. Open the link in **Safari**, tap **Download the profile**, then **Allow**, and open Settings →
   **Profile Downloaded**. If it is missing, look under **General → VPN & Device Management**, or
   download again.
2. Before tapping **Install**, check that the profile holds only a **Certificate** whose name and
   SHA-256 (under **More Details**) match the trusted screen. If anything differs, tap **Remove**.
3. Tap **Install**, then turn the certificate on under **General → About → Certificate Trust
   Settings**. Installing the profile alone does not trust it.
4. Open the address in a new Private tab: it must load with no warning.

**Android:** in Chrome, tap **Download the Android certificate**, then install it under Settings →
Security → **Install a certificate → CA certificate** (wording varies by device), after comparing
its name and fingerprint with the trusted screen.

### Verify HTTPS before pairing

Open the exact HTTPS address from `alt doctor` in a new private window and reload. Another device
needs the private-network address, not `localhost` or `127.0.0.1`. Altitude must load with no
certificate warning. If a warning remains, do not pair: check that this browser trusts the checked
CA, that the address matches and that the device's clock is right.

Uninstalling Altitude leaves this trust on your devices. To retire an installation, remove the CA
in the same certificate manager, with the commands `alt trust` printed, or its profile on an
iPhone. A fresh start that creates a new CA needs `alt trust` and each device's trust again; remove
the old CA the same way.

## Pair each device

Altitude opens only in browsers you pair, including the one on the computer running it. The
fresh installer's private link pairs the first browser after certificate trust. An already-paired
browser opens the app without consuming the link. For another browser, or an expired, used or
invalid link, run this in a terminal on the computer running Altitude, locally or over SSH:

```sh
~/.local/bin/alt pair
```

It prints an eight-character code, the address with a QR code a phone can scan, and the
certificate's name and full fingerprint. Open the address on the device and follow **Pair this device**. A code
works once, for ten minutes; making a new code replaces the previous one, and five wrong attempts
cancel it. Once `~/.local/bin` is on PATH, the command is `alt pair`. A paired device stays paired
until it goes 400 days unused, its site data is cleared or you remove it in **Settings → Devices**,
where it can also make a code for
another device. Lost every paired browser? Run `~/.local/bin/alt pair` again
([lockout recovery](OPERATIONS.md#devices-and-lockout-recovery)).

**Home Screen app:** after the address loads without a warning, use Safari's **Share → Add to Home
Screen** on an iPhone, or Chrome's **Add to Home screen** on Android. If its icon shows a gray letter,
see [refreshing icons](OPERATIONS.md#refreshing-home-screen-and-bookmark-icons).

**Alerts:** turn on alerts in **Needs you** once per device and allow notifications, and a new
decision reaches the device even while Altitude is closed. On an iPhone this works only in the Home
Screen app. Alerts need outbound internet from the computer running Altitude and carry no
conversation text ([decision alerts](OPERATIONS.md#decision-alerts-on-a-device)).

## First run in the browser

After pairing, First run has three numbered position indicators, with Back and Continue controls.
Positions do not claim that skipped or unmet work is complete:

| Step | What it does |
| --- | --- |
| **Welcome** | Explains the actual hosting computer, service URL and local/private access. |
| **What the agents need** | Checks that GitHub CLI and a coding agent are signed in, and on Linux that the agent sandbox is installed, showing the command to run in a terminal for anything missing. With no coding agent installed, one row offers each agent's install command; one is enough. The browser never asks for a password or token. |
| **Add your projects** | Lists the folders inside your projects folder (`~/Projects` unless you pick another). **Add project** registers one and opens its Setup. |

Name, incident reporting and voice remain optional links to Settings. Skipping preference setup
does not write choices or mark tools ready. Tool checks show required, optional, unmet and failed
readings; refresh them after completing the displayed hosting-terminal commands. Registering a
project enables integration and starts its coordinator's introduction using the coding account's
allowance. Until a coding tool the coordinator may use is installed, signed in and, on Linux, has its
sandbox, Setup shows the coordinator **Waiting** with the step still missing and starts the introduction
by itself once it is ready. A local repository without a GitHub remote shows the command that
publishes it, `gh repo create --source . --push`, since agents deliver through pull requests.
Project Setup observes integration and tool readiness, then offers **Open conversation**;
that action focuses the composer without starting a task. Remaining blockers link to their fix.
A non-Git folder can be conversation-ready with a ready coding agent; Git/GitHub checks concern
Git task delivery. No task delivery is offered without a repository.

## Your speech service

To use a transcription server you run or choose, open **Settings → Voice input → Your speech
service** and press **Use your speech service**. Enter its complete transcription endpoint, for
example `https://speech.example.test/v1/audio/transcriptions`, and expand **Key or model
(optional)** if your server requires them. Choose **Use this service**. Saving does not contact the endpoint.
The computer running Altitude must be able to reach it; a loopback address refers to that computer,
not the phone. Voice remains optional and is configured in Settings.

The server accepts a multipart POST with `file` containing mono 16 kHz signed 16-bit PCM WAV,
`response_format=json`, an optional `model`, and optional `Authorization: Bearer` key. It returns
JSON with a string `text`. Configure your server's own compatible transcription route using its
documentation; Altitude neither installs nor operates that server. The example address is fictional.
Redirects and environment proxies are not used. HTTPS uses the computer's normal certificate trust;
HTTP is available with an explicit unencrypted-audio/key warning.

Audio passes through Altitude to that endpoint after you stop recording. Its privacy policy and
charges apply. Failed recordings are discarded, so record again; typed text and images remain.
See [service operation and limits](OPERATIONS.md#your-speech-service). Previously deleted service
credentials cannot be recovered and must be entered again.

## Register a project and start a conversation

Use a small GitHub project you are comfortable letting agents change through pull requests. Add it in
First run, or from a terminal:

```sh
alt project add example --path /absolute/path/to/example-project
```

Its **Setup** checklist, also under **Setup…** in the project's ⋯ menu, checks the folder, the Git
repository, the project's instructions file (`AGENTS.md`, otherwise `CLAUDE.md`), Altitude's Git
guards and the coordinator. Altitude fixes what it owns by itself. If the repository already has its
own Git hooks, **Review integration** lets you run both sets or keep yours; combine only hooks you
trust, since they run with Altitude's Git permissions ([project setup](ARCHITECTURE.md#project-setup)).

Open the project and send:

> Describe this project and suggest one small improvement. Let's discuss it before creating work.

The coordinator, shown as L3, replies. When you settle on a change, ask it to create a task, then
follow the task's owner (L2), its session and its pull request in the work panel. Ask for a merge
hold to review a result yourself before it merges. Agent work uses your coding account's allowance
and normal charges. Altitude picks engines automatically. To choose a model and effort yourself,
press the model and effort button where the work runs: under the chat's message box for the
coordinator, **New tasks** beside quota for new tasks in every project, or in a task to switch that
task. For example, pick **6.1 Sol** and **High** to run on Codex at High; no external CLI
configuration is needed. Coordinator and New tasks picks save at once; a task asks you to confirm
the switch first, since a fresh session continues from its saved work. Started tasks keep their
model and effort until you switch them, and a task that reaches its usage limit waits for the reset
or for your switch. A project's own settings appear in Settings under **Project settings**, where
**Reset** clears one.
Named choices and shared account allowance do not prove model access. Requested and reported
values remain distinct. See [model choices](CLI.md#model-choices) for precedence and timing.

## When something does not work

- **The page does not load:** check `alt service status`, `alt service logs`, the address and the
  certificate trust above. Never turn off HTTPS to get past a certificate error.
- **The installer or `alt doctor` refuses:** follow the fix it prints; settings are in
  [configuration](OPERATIONS.md#configuration).
- **The coordinator does not answer:** check that your coding CLI works in a terminal as this
  account, and the route explanation in the conversation. A lapsed sign-in is covered in
  [engine sign-in recovery](OPERATIONS.md#engine-sign-in-recovery).
- **A task cannot start or land:** read the reason it shows. It usually needs a clean `main` that
  matches `origin/main`, a signed-in GitHub CLI or passing checks; see
  [recovering dirty main](OPERATIONS.md#recovering-dirty-main).

[Open an issue](../README.md#feedback) with the command, your OS, Altitude's version and the error,
with private details removed.
