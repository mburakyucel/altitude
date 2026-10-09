# Set up Altitude

The [container candidate](CONTAINERS.md) uses container-local tools, sign-ins and project volumes.
Native installation commands below do not install into that image. Linux container onboarding
passes on phone/desktop with real GitHub and one coding-engine sign-in. Credentials in the
persistent home survive the observed reboot and image replacement; see the
[live run and its limits](CONTAINERS.md#live-linux-run). Mac acceptance remains unverified.
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
release server inside the VM; its public run installs the latest release with the command below and
updates `v0.1.0-rc.2` to it, both from GitHub itself),
without establishing browser/device certificate trust, live provider readiness or a minimal OS install.
On an Apple silicon Mac, the [macOS VM run](DEVELOPMENT.md#macos-vm-run) checks in fresh, offline
macOS guests that the built `install.sh` stops with its documented fix for each missing prerequisite,
installs, updates, recovers and uninstalls, and that the service starts again at login after a restart.
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
  SSH or HTTPS Git credentials. For HTTPS with GitHub CLI credentials, run `gh auth setup-git`
  and verify `git fetch origin main` in the selected repository: CLI sign-in alone can succeed
  while Git authentication is still unset. In a container, run both commands inside its shell;
  see [container sign-in](CONTAINERS.md#first-use). Altitude's delivery path expects a clean primary `main`
  checkout with an `origin/main` branch and the project's applicable checks.
- At least one installed, authenticated **Codex or Claude Code CLI**, usable from the same
  Linux account that runs Altitude. Authenticate using the engine's native setup. CLI versions must support the headless,
  session and permission features in the [launcher](../altitude/engines.py); there is no tested
  version matrix yet. Codex task owners use the CLI's configured model unless an Auto option or
  explicit pin supplies one. Its coordinator loads your Codex configuration beneath Altitude's own
  settings and uses your configured model unless a route or model override supplies one. Configure Auto with the engine/models you intend to
  use; access to every default preference is not required.

## Install the application

On Linux x86_64 with Python 3.12 or newer and a systemd user manager, or on macOS 15 or newer on
Apple silicon with Python 3.12, Homebrew's OpenSSL 3 and a logged-in desktop session, one command
installs the latest published release as the account that will use Altitude:

```sh
curl --proto '=https' --tlsv1.2 -fsSL https://github.com/mburakyucel/altitude/releases/latest/download/install.sh | sh
```

The command downloads anonymously and needs no GitHub sign-in. `latest` names the
newest stable release and skips release candidates; to install one exact release,
replace `latest/download` with `download/<tag>`, for example `download/v0.1.0`. On the Mac, installation,
updates, rollback, uninstall and starting again at login pass in fresh macOS virtual machines; the
[roadmap](ROADMAP.md#native-macos-runtime) lists the native checks still open.

`v0.1.0-rc.1` cannot start its service: systemd refuses the working directory its unit names, so
installation fails at service start and leaves that unit and an interrupted activation behind.
Until they are cleared, `alt recover`, updates and uninstall stop at the refused unit. On a machine
that ran it, remove the unit and complete the activation's recovery, then install again; the
failed installation left no version to update, and configuration, TLS identity and data are kept:

```sh
systemctl --user disable altitude.service
rm ~/.config/systemd/user/altitude.service
systemctl --user daemon-reload
~/.local/bin/alt recover
```

`install.sh` belongs to one published release. It checks the machine first and stops with the fix
when something is missing: Linux x86_64 or a Mac with Apple silicon, not root, Python 3.12 or newer,
`curl`, a SHA-256 tool and `openssl`; on Linux `systemctl --user`, on a Mac macOS 15 or newer,
OpenSSL 3 ahead of macOS's LibreSSL on PATH and launchd's domain of the logged-in desktop session.
Releases before `v0.1.0` stop on a Mac before downloading anything. It then downloads that release's archive and `install.py`, checks
each against the SHA-256 written into the script when the release was built, runs
`install.py --archive … --sha256 …` and prints the address, the certificate fingerprint and the next
steps: put `~/.local/bin` on PATH, run `alt doctor`, trust the certificate and open the address.
Nothing is run from a download that does not match, and nothing runs as root. The script is one
function called on its last line, so a download cut off midway does nothing.

An installed copy checks for a newer release and offers it in the app, in `alt doctor` and through
`alt update` ([operations](OPERATIONS.md#installed-application-lifecycle)); nothing updates until you
confirm. A copy installed from a release candidate is offered newer candidates and stable releases;
a copy installed from a stable release is offered stable releases only.

The command trusts GitHub's HTTPS and the published, immutable release for `install.sh` itself.
Every release carries GitHub's release attestation for its files. Releases the release workflow
publishes from the public repository also attest each file's build provenance; `v0.1.0` and its
release candidates were published while the repository was private and have no build provenance.
To verify a release's script before running it, set `VERSION` to its tag:

```sh
VERSION=v0.1.0   # replace with the release's tag
curl --proto '=https' --tlsv1.2 -fsSLO "https://github.com/mburakyucel/altitude/releases/download/$VERSION/install.sh" &&
  gh release verify-asset "$VERSION" install.sh --repo mburakyucel/altitude &&
  sh install.sh
```

For a release with build provenance, `gh attestation verify install.sh --repo mburakyucel/altitude`
also checks that the release workflow built the file.

On a Mac the service is the LaunchAgent `~/Library/LaunchAgents/dev.altitude.altd.plist` of your
login, logging to `~/Library/Logs/altitude/altd.log`; the application lives in
`~/.local/share/altitude` and its settings in `~/.config/altitude`, as on Linux. A failed update
restores the previous version by itself. If an installation or update is interrupted (the Mac
sleeps, the terminal closes), `~/.local/bin/alt recover` finishes restoring the previous version; when
`alt` itself is missing, the release's `install.py` does the same with
`python3.12 install.py --recover`. Configuration, TLS identity and data are kept either way.
Linux and a Mac can also [run Altitude from a source checkout](#run-from-a-source-checkout).

Installation starts and enables an owned per-user service and prints its HTTPS URL and public
CA fingerprint. It refuses an existing customized service or conflicting `alt` launcher;
[switching from a source deployment](#one-service-per-account) is explicit. Keep `~/.local/bin` on your shell's PATH.
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

### Install by hand

The same installer runs by hand from the release files, for example on a machine without internet
access: download `install.py`, the versioned `.tar.gz` archive and its `.sha256` from the
[release](https://github.com/mburakyucel/altitude/releases), and verify the checksum's source; a
checksum from the same untrusted download does not establish authenticity.

```sh
python3.12 install.py --archive altitude-v0.1.0.tar.gz --sha256 '<release SHA-256>'
export PATH="$HOME/.local/bin:$PATH"
alt doctor
```

### Update from a release candidate

A copy installed from `v0.1.0-rc.2` follows stable releases only: it is offered `v0.1.0` and later
stable releases, and `alt update` installs the offered release, keeping configuration, TLS identity
and data.

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

### Run from a source checkout

A clone of this repository can run Altitude instead of a release: the service runs the checkout's
committed code and follows a branch of its `origin`, `main` by default, rather than published
releases. Point `origin` at your fork and check out your own branch to run your own changes. The
[platform table](ARCHITECTURE.md#linux-and-macos) lists what differs between Linux and a Mac.

#### Prerequisites from source

You need the [prerequisites](#prerequisites) above, and `python3` on PATH must be Python 3.12 or
newer, since `bin/alt` runs with it. Building the web app also needs a Node version that
`web/package.json` accepts and the pnpm it pins, through Corepack (`corepack enable pnpm`); the
service rebuilds the web app itself when it activates a new commit, so its PATH needs them too.
Playwright and its Chromium are needed only to run the [checks](DEVELOPMENT.md#local-checks).

#### Clone and build

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
certificate authority and server certificate in `ALTITUDE_TLS_DIR` (default
`~/.config/altitude/tls`), or keeps the identity already there, so devices that trust it keep that
trust. The service refuses to start without it. `alt doctor` then checks the prerequisites and
shows the certificate to [trust on each device](#trust-https-on-each-device).

#### Install the service

From the checkout, clean and on the branch the service will run:

```sh
make install-service
```

It installs Altitude's [Git guards](#project-setup-and-repair) in the checkout and starts the
service at `https://127.0.0.1:8890`:

- **Linux:** it copies `systemd/altitude.service` to `~/.config/systemd/user/`, then enables and
  starts it. The unit runs `main` with PATH `~/.local/bin:/usr/local/bin:/usr/bin:/bin`; nvm's
  default Node is found when Node is missing from that PATH ([noninteractive toolchain](DEVELOPMENT.md#noninteractive-toolchain)).
  Change the address, PATH or branch with a drop-in from `systemctl --user edit altitude`, for
  example `Environment=ALTITUDE_HOST=<private address>`, then `systemctl --user restart altitude`.
- **Mac:** it writes the LaunchAgent `~/Library/LaunchAgents/dev.altitude.altd.plist`, which
  logs to `~/Library/Logs/altitude/altd.log`. The agent listens on `ALTITUDE_HOST` from the
  command's environment (default `127.0.0.1`), keeps the PATH the command runs with, which must
  find OpenSSL 3, `gh`, the coding CLIs and pnpm, and runs the checked-out branch. Run it again to
  change any of them, for example `ALTITUDE_HOST=<private address> make install-service`.

The service starts only while the checkout is clean, on its branch and not ahead of
`origin/<branch>` ([`ALTITUDE_SOURCE_BRANCH`](#configuration-and-limits)). Each start runs the
committed code, exported under the ignored `.altitude-source/`. `alt service status` and
`alt service logs` read it. A phone or another computer needs a [private address](#configuration-and-limits);
the target prints the firewall command for `ufw`. Continue with [device trust](#trust-https-on-each-device),
[pairing](#pair-each-device) and [First run](#first-run-in-the-browser).

Both service definitions start with Altitude's sanitized incident reports going to its public
issue tracker (`ALTITUDE_UPSTREAM_ISSUE_REPOSITORY`). First run's **Report Altitude's own faults?**
step or **Settings → Incident reports** saves your own choice, including keeping them on this
computer ([incident publication](OPERATIONS.md#incident-publication)).

#### Updates from the branch

Register the checkout as the project named `altitude` (**Add project** on it in First run, or
`alt project add altitude --path "$PWD"` from the checkout) and the service follows its branch: every
thirty seconds it fast-forwards the checkout to `origin/<branch>`, then builds, restarts and verifies
itself at the next quiet point ([service lifecycle](OPERATIONS.md#service-lifecycle)). That project
also gets a coordinator conversation like any other; its tasks need push access to `origin`.
Without that registration, update by hand from the checkout:

```sh
git pull --ff-only
make restart
```

`make restart` rebuilds the web app, restarts the service and waits for its API and page to answer;
it refuses unless the checkout is exactly `origin/<branch>` and Altitude is idle. A bad commit is
fixed by a revert or fix on the branch, never by resetting the checkout
([source recovery](RELEASING.md#recovery)).

#### Differences from an installed release

- `alt update`, `alt recover`, `alt uninstall`, `alt service start` and `alt service stop` refuse a
  source deployment, and it neither checks for nor offers new releases.
- `~/.config/altitude/install.json` (`ALTITUDE_CONFIG`) does not apply: the service takes its
  settings from its unit or LaunchAgent and the environment.
- The checkout is a deployment, not a workspace. Its Git guards refuse commits and pushes to `main`,
  and the service refuses to start or update a dirty, diverged or ahead checkout; develop in another
  clone or a worktree, push there and let the service fast-forward.
- The service needs Node and pnpm to rebuild the web app, which a release does not.

#### One service per account

A source service and an installed release use the same service name, port 8890, `ALTITUDE_HOME`
(`~/.altitude`) and TLS directory, so an account runs one of them. `make install-service` replaces
an existing service definition without asking: run `alt uninstall` first to retire a release,
which keeps configuration, TLS identity and data for the source service to reuse. The installer
refuses an existing source service and an `alt` it did not write; remove the source service as
below and take its `bin` off PATH before installing a release. An older release may not read data
that newer source code wrote ([recovery](RELEASING.md#recovery)).

#### Remove a source service

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
core.hooksPath` in the checkout removes its Git guards, [removing the project](OPERATIONS.md#remove-a-project)
unregisters it, and the [trust steps](#verify-https-before-pairing) end with removing the CA from
each device.

### Trust HTTPS on each device

Trust Altitude's own certificate authority (CA) in each browser you use, including on the computer
hosting Altitude. `alt doctor` shows the URL, public `ca.crt` path, the
CA's name, expiry, SHA-256 fingerprint and what trusting it allows, all read from the certificate
itself, with these steps in short form (`trust_steps`); **Settings → Devices** shows the same
certificate facts on a device that already trusts Altitude.

Only `ca.crt` goes to a device, over any channel: cable, AirDrop, your own email or cloud, or
`alt tls-share` for a desktop or phone. Never transfer `ca.key` or `server.key`. The channel does not have to
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

#### Get the public certificate

**On the computer hosting Altitude:** run `alt doctor` in your terminal and use the public `ca.crt`
at the path it reports. Copy just that file to a convenient folder if your browser's file picker
cannot reach it. This works with the default loopback-only installation: sharing is unnecessary,
and there is no need to expose Altitude to the network. Continue with [Linux](#linux-desktop) or
[macOS](#macos-desktop) below.

**On another computer or a phone:** the service must already have a reachable private-network
HTTPS address configured. Use the sharing window below or transfer only `ca.crt` by another channel.
Keep `alt doctor` or an already-trusted Settings page available as the independent identity reference.

#### Share with a desktop or phone

On a device that already trusts Altitude, open **Settings → Devices** and choose **Set up a device** in the
Certificate card. Or, on the computer running Altitude, locally or over SSH, run:

```sh
alt tls-share
```

Both show a QR code for a ten-minute plain-HTTP link on the service's address, beside the CA's name
and SHA-256 fingerprint. Open the printed link on the destination desktop, or scan its QR code with
a phone. The page offers separate Linux, macOS, iPhone/iPad and Android instructions, an iPhone
profile download and a plain public certificate download. On the same device, choose **Open setup page** in Settings; it opens a
new tab while the original tab keeps the QR code, certificate details and sharing timer. Keep that
original Settings page open during setup. Close and expiry remove the setup link as well as the QR.
The link serves only that page and the public CA certificate, as a
configuration profile holding only the certificate or as the certificate file; it never serves a
key or Altitude itself. Settings shows the time left and **Close**; closing it, leaving the page or
the end of the ten minutes closes the link, and Ctrl-C closes the command's link sooner. A new
**Set up a device** replaces an earlier one. A firewall on that computer can block the link's port;
then use another channel.

`alt tls-share` reads the address, port and certificate folder the running Altitude service
recorded when it started, so the shell's own `ALTITUDE_HOST`, `ALTITUDE_PORT`, `ALTITUDE_TLS` and
`ALTITUDE_TLS_DIR` play no part. Before offering
anything it fetches the service's health over HTTPS, trusting only that folder's CA for the
service's address, and offers only a certificate the service proves it serves under. **Set up a device**
is opened by the service itself and offers the CA it serves under. Its QR code prints black on white
in any terminal, including Altitude's own.

Both stop with the reason when the service serves plain HTTP or listens only on loopback
(`ALTITUDE_HOST` must be the private-network address the destination device opens, which takes effect when the
service restarts). `alt tls-share` also stops when no service has recorded its address, or the
service does not answer or answers without proving that certificate. On a Mac, the source
service's LaunchAgent takes its address from `ALTITUDE_HOST` when it is installed:
`ALTITUDE_HOST=<address> make install-service`.

#### Check the desktop download before trusting it

Choose **Download the certificate** on the setup page and save `ca.crt`. On either Linux or macOS,
inspect the file before importing it. From the folder containing the file, run:

```sh
openssl x509 -in ca.crt -noout -subject -fingerprint -sha256
```

Compare the subject's name and the entire SHA-256 fingerprint with `alt doctor` on the hosting
computer or **Settings → Devices** in an already-trusted browser. The setup page is unauthenticated;
matching its own displayed fingerprint alone does not establish identity. The command reads the
certificate and installs nothing. Inspect `ca.crt` in a text editor as well: it must contain exactly
one `BEGIN CERTIFICATE` / `END CERTIFICATE` block and no other payload. A file checksum is not the
certificate fingerprint. If the name, fingerprint or contents differ, delete the download and stop.
[OpenSSL certificate inspection](https://docs.openssl.org/3.0/man1/openssl-x509/).

#### Linux desktop

After [checking the file](#check-the-desktop-download-before-trusting-it), import it in the browser
you will use:

- **Chrome/Chromium:** open `chrome://certificate-manager`, choose **Local certificates**, then
  **Custom → Installed by you**. Under **Trusted Certificates**, choose **Import** and select
  `ca.crt`. On builds with the older manager, open `chrome://settings/certificates` and use
  **Authorities → Import**, enabling trust for identifying websites. Do not use **Your certificates**,
  which is for client identities. Browser versions and distribution packaging can change labels;
  [Chrome's certificate manager](https://chromium.googlesource.com/chromium/src/+/main/net/data/ssl/chrome_root_store/faq.md)
  and [Chromium's Linux guidance](https://chromium.googlesource.com/chromium/src/+/HEAD/docs/linux/cert_management.md)
  describe the available managers; the [current manager's navigation](https://chromium.googlesource.com/chromium/src/+/HEAD/chrome/browser/resources/certificate_manager/local_certs_section.html.ts)
  and [control labels](https://chromium.googlesource.com/chromium/src/+/HEAD/chrome/app/certificate_manager.grdp)
  identify the import route.
- **Firefox:** open **Settings → Privacy & Security → Certificates → View Certificates →
  Authorities → Import**, select `ca.crt`, and enable **Trust this CA to identify websites**.
  Confirm with **OK**. Firefox on Linux can need this separate import even when another browser
  or the OS already trusts the CA. [Mozilla's manual import instructions](https://wiki.mozilla.org/CA/Changing_Trust_Settings).

Quit and reopen the browser, then [verify HTTPS before pairing](#verify-https-before-pairing).
Repeat for each browser/profile you intend to use; importing in one browser does not prove trust
in another. These steps do not require a system-wide Linux trust-store change.

#### macOS desktop

1. Download the plain `ca.crt` file, then [check its name, fingerprint and contents](#check-the-desktop-download-before-trusting-it).
2. Open **Keychain Access** using Spotlight. Select the **login** keychain for your account and
   drag `ca.crt` into it. Use **System** only if you deliberately want trust for all users and can
   authorize the Mac's administrator prompt. [Apple's import instructions](https://support.apple.com/guide/keychain-access/kyca2431/mac).
3. Double-click the imported certificate, expand **Trust**, and set **Secure Sockets Layer (SSL)**
   to **Always Trust**. Leave other uses at their defaults. Close the certificate window and
   authorize saving if macOS prompts. Safari and Chrome use this explicit SSL trust.
   [Apple's trust controls](https://support.apple.com/guide/keychain-access/kyca11871/mac) and
   [Chrome's macOS trust behavior](https://chromium.googlesource.com/chromium/src/+/main/net/data/ssl/chrome_root_store/faq.md).
4. Quit and reopen Safari or Chrome, then [verify HTTPS before pairing](#verify-https-before-pairing).

For **Firefox**, use the **Authorities → Import** steps in the Linux section with the same verified
file if it does not already trust the CA. Firefox normally recognizes roots from the **System**
keychain when **Allow Firefox to automatically trust third-party root certificates you install**
is enabled in Privacy & Security; do not assume a login-keychain import reaches Firefox.
[Mozilla's platform behavior](https://support.mozilla.org/en-US/kb/setting-certificate-authorities-firefox).

#### Set up a phone

Open the [sharing window](#share-with-a-desktop-or-phone), then follow the device steps. The link
is unauthenticated, so compare with the trusted terminal or Settings screen before installing.

**iPhone or iPad:**

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

On Android, tap **Download the Android certificate** in Chrome and install the file under Settings → Security → Encryption &
credentials → Install a certificate → **CA certificate** (names vary by device), comparing the
name and full fingerprint with the trusted terminal or Settings screen before trusting it; stop if you
cannot inspect them. Firefox for Android also needs its third-party CA setting.
[Android guidance](https://android.googlesource.com/platform/cts/+/35dfb1c0b8d%5E%21/).

#### Verify HTTPS before pairing

Open the exact HTTPS URL reported by `alt doctor` or the trusted Settings screen in a new private
or Incognito window in the browser you will use, and reload it. Check the scheme, address and port;
a second computer or phone needs the configured network address, not `localhost` or `127.0.0.1`.
The Altitude page must load without any certificate warning. Only then [pair the browser](#pair-each-device).
Verify the regular window as well before pairing there for everyday use; a private window's pairing
does not persist after it closes.

If a warning remains, stop before pairing. Check that this browser trusts the verified CA, the URL
matches the running service, the device clock is correct, and the CA has not expired or changed.
Record the OS/browser versions and exact warning for diagnosis; do not bypass it or disable checks.

The CA is valid for ten years; Altitude renews its one-year server certificate automatically and
reissues it when the listening address changes, so devices keep their trust. A device trusts again
only when the CA expires or is replaced, for example after a new installation or a lost key.

Open the exact HTTPS URL without a warning, in a new private window, and reload it before adding a home-screen app. Check
the installed app separately: a shortcut or cached page does not prove TLS works. Native Linux,
macOS and phone browser acceptance remains pending until observed on each platform; fixture and
emulated browser checks establish application behavior only. The independent macOS/iOS trust and
Home Screen acceptance in [#645](https://github.com/mburakyucel/altitude/issues/645) remains pending.
Remove this CA in the same browser/OS certificate manager when retiring
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

For an existing shortcut or installed app showing an older icon, or an iPhone tile showing a gray
letter instead of the Climb mark, follow the
[icon refresh steps](OPERATIONS.md#refreshing-home-screen-and-bookmark-icons).

### Alerts for new decisions

Needs you offers an alert for each new decision. Turn it on once per device and grant the browser's
notification permission there; trusted HTTPS is a prerequisite, so finish the step above first. An
iPhone shows the switch only for Altitude added to the Home Screen (Safari's Share → Add to Home
Screen, which uses the Altitude mark as its icon) and opened from there.

A decision appears in Needs you at once, but alerts only when it still needs you after L3 has had
its turn: while L3 reads the block that raised it, or the owner L3 answered resumes to settle it,
nothing alerts. A decision still waiting 15 minutes after it was asked alerts even if L3 or the owner
has stalled. When a decision is answered, withdrawn or superseded, its alert disappears from every
device on its own, whether Altitude is open or the phone is closed.

Turning the switch on also subscribes that device to its browser's push service, so a decision
reaches a closed phone. Altitude signs each push with a key it generates in `~/.altitude/push/` and
sends no payload, so the push service learns only that this device should wake; the device then asks
Altitude what is waiting. On your own network the alert names the project and task, and away from it
the alert says a decision is waiting and nothing more. A phone away from your network that already
shows an alert keeps it and adds none; an alert it could not clear there closes with the next alert it
receives or when you open Altitude on it. It needs outbound internet from altd; where a
push service is unreachable the switch says alerts arrive only while Altitude is open, which on a
phone means while it is on screen. Each alert opens that decision and carries no conversation text.
While a push service refuses Altitude's alerts, the line under the switch names it and the reason it
gave; [refused decision alerts](OPERATIONS.md#refused-decision-alerts) lists what each reason needs.
Declining permission, or a browser without notifications, leaves Needs you and typing unchanged.

## First run in the browser

After [pairing](#pair-each-device), with no project managed, the web app opens First run. Its five steps are skippable, go **‹ Back**
without saving, keep their place in the URL across reloads and are each a row in
**Settings → This machine** afterwards:

| Step | What it writes |
| --- | --- |
| **Your name**, filled in from `ALTITUDE_OPERATOR` or Git's global `user.name` | `operator_name` in `$ALTITUDE_HOME/settings.json`. Screens, agent prompts and incident sanitization use it; clearing it returns to the environment value or Git's name, and with neither, screens say “you”. |
| **What the agents need**: the GitHub CLI and a coding agent signed in, Git installed | Nothing. Each unmet check shows the command to run in a terminal on this computer, the install command for a missing tool or the sign-in command (`gh auth login`, the agent's own) for an installed one, with **Copy** and **Check again**. One signed-in agent is enough; the others read as optional. The browser never asks for a password or token. |
| **Report Altitude’s own faults?**, off by default | `incident_repository`: off keeps incidents on this computer; on stores the repository, Altitude's own filled in or a fork you name, after the signed-in GitHub CLI confirms it can see it. |
| **Voice** | Saves the speech backend when selected and starts host voice setup where available. Host voice is unavailable in a container; browser recognition remains an option in supported browsers. Skipping does not block project setup. |
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
the Codex coordinator loads your Codex configuration, but Altitude's settings replace its model provider
(Codex's built-in provider), permissions and approval policy, MCP servers (only Altitude's broker), hooks
and notification command. Other keys, such as reasoning or display preferences, apply as configured, and
`--strict-config` refuses a file with keys the CLI does not know.
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
