# Changelog

Release notes describe user-visible behavior, compatibility and recovery. The project is an early
preview; see [release checkpoints](docs/RELEASING.md). An Unreleased entry is not a published release.

## Unreleased

- Adversarial review is one request and one result. Task details shows a Proposal review and an
  Implementation review box with the latest verdict and a single Request, Review again or Try again
  button; the conversation shows one card per kind with earlier iterations inside it. Requests queue
  on any open task and wake a waiting L2, the operator skips a review without giving a reason, and
  same-engine review is presented as the ordinary path. The `retry` and `rerun` review actions are
  removed: a repeat is a `request` naming the review it replaces.

## v0.1.1 — 2026-10-09

Patch release of the early preview: alpha quality, with rough edges and the known limitations below.
It runs on Linux x86_64 with a systemd user service (target: Ubuntu 24.04) and on macOS 15 or newer
on Apple silicon, the two platforms the early preview supports. Install it with
`curl --proto '=https' --tlsv1.2 -fsSL https://github.com/mburakyucel/altitude/releases/latest/download/install.sh | sh`
([setup](docs/SETUP.md#install-the-application)).

Updating from `v0.1.0`: the installation offers this release in the app, in `alt doctor` and through
`alt update`. Configuration, TLS identity and data are kept; nothing needs migrating.

Conversations and tasks:

- The coordinator creates a task in the same turn when a message makes clear that work should
  happen, and asks first only when it is unclear whether any work is wanted. Open product choices go
  to the owner's proposal checkpoint.
- Findings or actions a task produces for you reach project chat with their exact steps, also when
  the task finishes without a PR.
- Messages you send in a task's chat while its Claude owner is working reach it as your own next
  turn, so it acts on them as your instructions.
- On a phone, swiping sideways on a task page moves one tab at a time through Conversation, Live
  session and Terminal (when the task offers one).
- Command rows in the live session start right after the `$`, so phones show more of each command.
- Decision alerts wait while L3 or the task's owner is still handling a decision, and alert once the
  task rests with the decision still open, or 15 minutes after it was asked. Needs you shows the
  decision at once. A banner for a decision that was answered, withdrawn or superseded closes on
  each device that can reach Altitude to refresh its decisions.
- Task owners run Altitude's own `alt` coordination commands without a Claude Code permission
  prompt; Altitude still enforces each role's authority, merge holds and operator grants.
- Codex receives long prompts reliably; a prompt larger than the pipe buffer could stall a turn
  until its time limit.
- Task briefs tell owners never to restart Altitude's or any managed project's service without
  naming one installation's services.

Phones and devices:

- The pairing field formats the code as it is typed: letters are uppercased, other characters are
  ignored and the dash appears after four characters. A pasted or autofilled code with or without
  its dash pairs the same way.
- Setup explains a Home Screen tile showing a gray letter instead of Altitude's icon: the iPhone does
  not trust that installation's certificate authority.

Landing:

- `alt land` waits for a required check that has not registered on the candidate yet, instead of
  stopping as if it were skipped.
- `alt land` accepts GitHub-managed scans on the candidate commit, such as CodeQL's default setup. A
  failing scan blocks the merge and a pending one keeps the wait going; a scan never stands in for
  the required `check`.
- `alt land --merge` squashes an adopted PR into one commit on main, as it does every other PR, so
  landing works where the repository allows only squash merges. The adopted branch keeps its history
  and is not deleted.

Documentation and project rules:

- The README describes macOS support and recent features, with captures of the current interface,
  and setup documents running Altitude from a source checkout.
- Altitude's repository rules treat everything pushed or published as public: commits, branch
  names, PR and issue text, release notes and attached evidence carry no personal or private
  information. The L2 and L3 personas point owners and coordinators to a repository's own rule for
  public content.
- Every release increments PATCH; MINOR increments only on the operator's explicit instruction
  ([cadence and versions](docs/RELEASING.md#cadence-and-versions)).

For contributors:

- Every pull request, push to main and manual dispatch runs the required `check` on a GitHub-hosted
  runner with a read-only token and no secrets; no workflow runs on the maintainer's machine. A
  failed run keeps its browser report as a seven-day artifact, and release publication requires
  the tagged commit's successful push run of the same workflow (#469).
- The required `check` runs `make check` as ten parallel shards on GitHub-hosted runners and passes
  only when every shard passes; `make check-python SHARD=i/N` and `make check-web SHARD=i/N` run one
  shard locally.
- The complete `make check` passes in a macOS validation run.
- `make installation-vm BASELINE=v0.1.0-rc.2 PUBLIC=1` installs `releases/latest` and updates a
  published release candidate against GitHub itself in a fresh Ubuntu 24.04 virtual machine.
- The iOS Simulator lane checks that Safari trusts a certificate chain from Altitude's own generator
  without a warning.
- A delta history audit skips recorded heads its clone lacks, and `docs/RELEASING.md` explains how
  to cover GitHub's pull-request refs.
- The web app keeps one lockfile, `web/pnpm-lock.yaml`; the development-only `undici` and
  `source-map-js` move to patched releases.

Known limitations:

- Native macOS confirmation is still open: a physical second Mac, other macOS versions,
  logout/login, sleep, confinement, Stop and restart adoption on a spare account (#225),
  certificate trust in browsers and on physical devices (#645) and the download from GitHub itself
  are not verified. The installer needs Homebrew's `python@3.12` and `openssl@3`; voice on a Mac
  uses the browser's recognition.
- On Linux, a clean physical machine is not verified, and the update from `v0.1.0` to this release
  has not been run in a virtual machine.
- The Linux container deployment remains a candidate under validation; on a Mac it is unverified
  (#643). See [its limits](docs/CONTAINERS.md).
- Live engine providers are not tested; engine behavior rests on deterministic fixtures.
- Adding a phone (certificate profile, QR scan, Home Screen app) is walked in browsers and the iOS
  Simulator, not yet on a physical iPhone or Android device (#645).
- What a coordinator or owner reads from a terminal reaches its AI provider. Use a desktop or SSH
  terminal for work neither should see.

Recovery: a failed activation restores the previous version and keeps configuration, TLS identity
and data; `alt recover` (or the release's `python3.12 install.py --recover`) completes an interrupted
one. A faulty release is followed by a new version; tags and release files are never replaced.

## v0.1.0 — 2026-10-08

First stable release of the early preview: alpha quality, with rough edges and the known limitations
below. "Stable" means it is GitHub's latest release, which the one-line install and stable
installations' update check follow; it is not a stability promise. It runs on Linux x86_64 with a
systemd user service (target: Ubuntu 24.04) and, experimentally, on macOS 15 or newer on Apple
silicon. Install it with
`curl --proto '=https' --tlsv1.2 -fsSL https://github.com/mburakyucel/altitude/releases/latest/download/install.sh | sh`
([setup](docs/SETUP.md#install-the-application)). It was published while the repository was
private, so its files carry no build attestation.

Known limitations:

- macOS is experimental. Installation, updates, rollback of a failing update, uninstall and starting
  again at login pass in fresh, offline macOS 26.6 virtual machines and under a throwaway home on
  one Mac. A physical second Mac, other macOS versions, logout/login, sleep, confinement, Stop and
  restart adoption on a spare account (#225), certificate trust in browsers and on devices (#645)
  and the download from GitHub itself are not verified. The installer needs Homebrew's `python@3.12`
  and `openssl@3`; voice on a Mac uses the browser's recognition.
- On Linux, the one-line install from GitHub and the update from `v0.1.0-rc.2` to this release
  through its own update check and `alt update`, with rollback of a failing update, pass in a fresh
  Ubuntu 24.04 virtual machine. A clean physical machine is not verified.
- The Linux container deployment remains a candidate under validation; on a Mac it is unverified
  (#643). See [its limits](docs/CONTAINERS.md).
- Live engine providers are not tested; engine behavior rests on deterministic fixtures.
- Adding a phone (certificate profile, QR scan, Home Screen app) is walked in browsers and the iOS
  Simulator, not yet on a physical iPhone or Android device (#645).
- What a coordinator or owner reads from a terminal reaches its AI provider. Use a desktop or SSH
  terminal for work neither should see.

Updating from a release candidate:

- An installation from `v0.1.0-rc.2` follows stable releases and is offered this release; run
  `alt update` ([setup](docs/SETUP.md#update-from-a-release-candidate)). Configuration, TLS identity
  and data are kept, and the Linux service unit is unchanged.
- A machine whose `v0.1.0-rc.1` installation failed at service start has no installed version to
  update: clear its refused service unit, run `alt recover` as setup describes, then install this
  release.
- Operator grants recorded with `alt task machine` are not carried over; record them again with
  `alt task grant` when a task needs one.
- `alt install-statusline` is removed. If you ran it, restore Claude Code's own `statusLine` in
  `~/.claude/settings.json`; the replaced command is in that file's `env.ALTITUDE_ORIG_STATUSLINE`.
- The first maintenance after updating also removes clean worktrees of rejected tasks and deletes
  their merged branches; commits that exist only on this machine are kept.

Recovery: a failed activation restores the previous version and keeps configuration, TLS identity
and data; `alt recover` (or the release's `python3.12 install.py --recover`) completes an interrupted
one. A faulty release is followed by a new version; tags and release files are never replaced.

Installation and updates:

- `install.sh` installs on a Mac with Apple silicon and macOS 15 or newer, without administrator
  rights. Before downloading it checks for Python 3.12, OpenSSL 3 ahead of macOS's LibreSSL on PATH
  and a logged-in desktop session, and prints the fix for anything missing. The service is the
  LaunchAgent `dev.altitude.altd` of your login and starts again when you log in; updates, rollback,
  `alt recover` and uninstall work as on Linux.
- An installation from a stable release is offered newer stable releases only; one from a `-rc.N`
  candidate is also offered newer candidates. Drafts are never offered.
- A fresh installation keeps the installing shell's `HTTPS_PROXY`, `NO_PROXY` and `SSL_CERT_FILE`, so
  update checks and updates work behind an HTTPS proxy; it then trusts that proxy for the release
  checksums too.
- The service starts without looking up its own address's name, which stalled a start or update on
  a machine without network access.
- `alt` finds the running service through the record it writes when it starts, so a shell's own
  TLS settings no longer make `alt` refuse.

Phones, devices and voice:

- **Settings → Devices → Set up a device** guides certificate trust on Linux, macOS, iPhone/iPad and
  Android. It shows a QR code for a ten-minute setup link with a countdown, which a phone scans or
  opens itself; the phone gets a guided page, an iPhone configuration profile holding only
  Altitude's public CA, and the fingerprint to compare before pairing. `alt tls-share` prints the
  same QR code.
- Dictated words flow into the message box at a steady pace.
- Chat accepts images with Display P3, BT.2020, grayscale or PNG gamma color information and converts
  them to sRGB; images whose color has no conversion, such as HDR, upload as decoded.

Conversations and tasks:

- **Send now** delivers a queued message next, in project and task chats.
- A **Models** dialog chooses the model and effort for L3 (per project) and for new tasks (all
  projects). Settings is organized by destination, and **Remove project** moved from the ⋯ menu to the
  project's settings, in a dialog that explains what is kept and offers Cancel first. An earlier L3
  engine choice becomes the project's L3 "Only" engine.
- Task details show the current context size and "N tokens processed · M model requests", with what
  those figures do and do not mean.
- Live activity loads older entries as you scroll up and keeps your place through refreshes.
- Paused and blocked tasks say why in plain language; question cards show only the operator's open
  questions; design previews carry their proposal's title, and Back from a preview no longer loops.
- After a provider rejects a sign-in, signing in again and pressing **Retry** or resuming the task
  clears the hold at once.
- Monitor lists only the sessions Altitude runs.

Coordinator, owners and grants:

- One operator grant covers anything the operator permits a task to do, such as a service change, a
  deploy or a release publication. `alt task grant` records the operator's yes to a stated purpose,
  each command runs as the operator through `alt task run` and is recorded, a command's result
  survives an Altitude restart, and revoking the grant stops a running command. L3 can apply the
  same yes to another task with the identical purpose.
- The coordinator can run read-only `gh` commands against any repository your GitHub login can see,
  read its project terminal, and hear when a command it handed you finishes there. Coordinators of
  projects on the same installation can exchange messages, which show folded in both chats.
- L3 follows authorized work and explains a stalled task. A task whose PR closed without merging can
  deliver a fresh PR, and landing waits through the fresh required check.
- Owners run their candidates' checks in disposable validation runs (`alt task validate`): a Linux
  container with VMs and sandboxed browsers, a validation profile on a Mac, and a disposable iOS
  Simulator iPhone, with optional screen captures linked from chat.
- Failed independent reviews keep sanitized diagnostics, and incident issues get a System section and
  a clean failure summary.
- Finished tasks' worktrees are swept whether the task was done or rejected.

macOS from a source checkout:

- Altitude runs from a source checkout on a Mac: `make install-service` makes it a LaunchAgent of
  your login, each agent job runs as its own launchd job that Stop ends completely, and Claude Code
  jobs may write only in their task's worktree and their own state.
- Security fixes for task jobs on a Mac, whose profiles also refuse Simulator and app launches.
  Claude reviews on a Mac find their Keychain sign-in, and task files open on a Mac without
  listing hidden folders.

Linux container (candidate):

- A rootless Podman container packages the release with real user services and persistent home and
  project volumes; replacement pauses new AI work until you continue on the host. The image pins a
  GitHub CLI that can open PRs, and one live Linux run records sign-in, an owner-created PR and
  issue, and authentication across restart and image replacement. See
  [the container boundary and validation limits](docs/CONTAINERS.md).

## v0.1.0-rc.2 — 2026-09-29

Second release candidate of the early preview, for Linux x86_64 with a systemd user service
(target: Ubuntu 24.04). It fixes installation: `v0.1.0-rc.1` cannot start its service. Install it
from this release's `install.sh`; the one-line install and `alt update` use stable releases only.

Known limitations: installation, update, failed-update recovery, uninstall and reboot pass in a
disposable Ubuntu 24.04 virtual machine, but a clean physical machine and updating from one
published release to another are not yet verified; live engine providers are not tested; on macOS
`install.sh` stops before downloading anything.

Recovery: a `v0.1.0-rc.1` installation failed at service start and left its refused service unit
and an interrupted activation, which block `alt recover`, updates and uninstall. Remove that unit,
run `alt recover`, then run this release's `install.sh`; configuration, TLS identity and data are
kept (see [setup](docs/SETUP.md#install-the-application)). A failed activation restores the
previous version; `alt recover` completes an interrupted one. A faulty release is followed by a new
version.

- Installation starts its service under systemd: the generated unit names its working directory
  as a plain path, which systemd 255 (Ubuntu 24.04) accepts, including paths with spaces, quotes
  and percent signs. `v0.1.0-rc.1` wrote that path in quotes, so systemd refused the unit and
  installation failed at service start.
- `alt tls-share` sets up a phone's HTTPS trust without another computer or AirDrop: for ten minutes
  it offers the certificate at a plain-HTTP link on the configured network address and prints what
  the phone must match before tapping Install: a lone certificate, its real name and its SHA-256.
  The installer, `alt doctor` and **Settings → Devices** show the certificate's name, expiry and
  what trusting it allows, read from the certificate, including "No limits" for an unconstrained
  CA. Altitude's HTTPS address no longer serves `/ca.crt`. `alt tls-share` and `alt pair` read the
  running service's address, port and certificate folder, so an ordinary shell reaches it.
- Voice to text runs on this computer by default where its speech model can run (Linux x86_64):
  it transcribes English speech on the CPU with NVIDIA's Parakeet TDT 0.6B v2 (CC-BY-4.0), and words
  appear in the draft about a second behind speech. First run offers the one-time download of about
  698 MB as its **Voice to text** step; it needs about 2.5 GB of free memory while in use.
  Elsewhere, including macOS for now, the browser's own recognition is the default; a saved choice
  is kept, and **Settings → Voice input** offers This computer and Browser recognition. Dictation
  keeps recording through a lost connection and its words catch up; audio stays in the page's
  memory only.
- The speech-service option is removed, with `/api/transcribe`, `--voice-model` and
  `--voice-key-file`. On first start the stored service URL and key are deleted and the machine uses
  the default; `alt machine set --voice` accepts `host` or `browser`.
- **Setup…** lives in the project's ⋯ menu with its status; the header shows Setup only while it
  needs attention.
- The in-app terminal accepts `sudo`, and on phone its key row ends with an **Enter** key that runs
  the typed line without the soft keyboard. When you run a command a task owner handed you in its
  task terminal, the owner is told once it finishes, and it can read that terminal's output.
- Decision alerts reach Apple devices; a push service that refuses alerts is named, with its
  reason, under the Needs you alert switch.
- In a task conversation, each message from L3 folds to one line with its summary and **Show**.
- A page opened before an update offers **Reload** when the terminal's code is gone, instead of an
  application error.
- Opening the app and task chats downloads less: app assets and views are sent gzip-encoded, and
  replies that can carry pairing codes or keys stay uncompressed.
- Reviews: an owner can record a finding as unresolved, which keeps merging refused; changes review
  runs while unrelated questions stay open; an additional review leaves earlier open findings in
  the merge gate. A held PR closed without merging stops asking for merge review.
- A resume's reason reaches the resumed owner, and a failed fetch of main raises a self-deploy fault
  only after five minutes without a successful fetch.

## v0.1.0-rc.1 — 2026-09-28

First release candidate of an early private preview, for Linux x86_64 with a systemd user service
(target: Ubuntu 24.04). Install it from this release's `install.sh`; the one-line install and
`alt update` use stable releases only.

Known limitations: fresh-machine installation and updating from one published release to another
are covered by deterministic tests only; live engine providers are not tested; on macOS `install.sh`
stops before downloading anything.

Recovery: a failed activation restores the previous version and keeps configuration, TLS identity
and data; `alt recover` completes an interrupted one. A faulty release is followed by a new version.

- Altitude opens only in browsers you pair. Run `alt pair` on the computer running Altitude, locally
  or over SSH, and type the code it prints on the device's **Pair this device** screen, or open the
  link it prints there. **Settings → Devices** lists paired devices, removes one at once and makes a
  code for another. After updating, every browser, including the one on that computer, pairs once;
  the `alt` CLI keeps working without a step. Internal error details show only in paired browsers
  and the CLI, and a connection that stays silent for thirty seconds is closed.

- A command an agent needs you to run arrives in chat as a command block with **Copy** and **Open in
  terminal**. Open in terminal shows that task's or project's terminal with the command typed at the
  prompt; nothing runs until you press Enter, and you can edit or clear it first. If a program is
  running in the terminal, nothing is typed and you get Copy instead. Agents mark these commands with a
  `run` code fence holding one line; every other code block now has a Copy button and is never an action.

- Reopening a terminal right after it closed shows the new shell instead of sometimes staying on
  "Loading the terminal…".

- English dictation gets sentence punctuation and capitals in any browser that runs WebAssembly
  in a worker. A small model bundled with Altitude runs on the device's CPU, adds only punctuation and
  capitals, and never changes your words. Each device downloads it (about 23 MB) from your Altitude
  server the first time you dictate. Where it is still loading or cannot run, the words land as
  recognized and the composer says so.

- `alt task reply` accepts its task's slug before the text, as `alt task block` and the other
  owner verbs do, so `alt task reply "$ALTITUDE_TASK" - <<'EOF'` reads stdin; `--file -` reads
  stdin for every text-taking verb.

- **Settings → Voice input** offers Browser recognition and **Your speech service**: any
  OpenAI-compatible `/v1/audio/transcriptions` server on this computer, your network or a hosted
  provider. It asks only for the URL; a key and model sit behind **Hosted provider? Add a key or
  model**, and **How to run one** links a worked whisper.cpp example. Errors name the service's URL.
  The `local` backend, its private socket/bridge protocol and the `WHISPER_SOCKET`/`WHISPER_BRIDGE`
  variables are removed, and voice no longer needs `ffmpeg`. A machine set to `local` now uses
  browser recognition; point it at an OpenAI-compatible server with
  `alt machine set --voice http://127.0.0.1:<port>/v1/audio/transcriptions`.

- The terminal answers faster and gets out of the way. Keystrokes reuse one connection, so an echo
  takes one network round trip instead of about three (at a 40 ms phone link, 132 ms becomes 49 ms).
  Showing the terminal opens it; the amber note, the folder lines and the Open step are gone, and a
  dim first line says where it runs as you. When the shell exits, or the terminal ends any other way,
  the view returns to Live session or the project, with a short notice unless it was your Close or a
  clean exit. Ctrl+V pastes, Ctrl+C copies selected text, the phone key row has Paste, and losing the
  connection shows a badge without resizing the shell. An ended terminal keeps no output.

- A held PR's **Approve merge** card stays beside an open question that names the PR unless that
  question offers its own options, so the operator always has a one-tap approval.

- A task whose clean worktree was left on another branch resumes on its task branch instead of
  pausing with a `task-git-provenance` fault; the task's events name the branch it left, which keeps
  its commits. Uncommitted changes, a detached HEAD or a missing task branch still pause the task,
  with the Git step that recovers it (#524).

- Altitude acts only on requests from its own page or the `alt` CLI: another site open in your
  browser cannot send it an action or frame it, and project design boards run without Altitude's
  authority. Over plain HTTP, Altitude answers only its address or `localhost`. Request bodies have a
  size limit.

- Work's **Done this week** lists every task finished in the last seven days, newest first, instead
  of at most twenty with the oldest on top.

- Altitude has a mark: an A whose left side climbs in three steps to the summit, on the accent tile.
  It appears beside "Altitude" in the desktop rail and the phone header's global tabs, as the
  browser-tab icon, as the iPhone Home Screen icon and in the README.

- A newly generated certificate authority vouches only for loopback, private-network addresses,
  private names (`.local`, `.internal`, `home.arpa`) and a DNS name configured when it is created,
  so trusting it on a device cannot expose public websites. Existing certificate authorities and
  external certificates keep their scope. Changing `ALTITUDE_HOST` reissues the server certificate
  at the next start under the same authority, so trusted devices need no new step. The installer
  and `alt doctor` print the URL, `ca.crt` path, SHA-256 fingerprint and short trust steps for
  Linux browsers, Mac, iPhone/iPad and Android; doctor's `certificate_trust` is now an object.

- First run, shown while no project is managed, walks four skippable steps on phone and desktop:
  your name (filled in from `ALTITUDE_OPERATOR` or Git's `user.name`), what the agents need (the
  doctor checks with the terminal command to run and **Check again**; the browser never takes a
  password or token), incident reports (off until turned on, then Altitude's repository or a fork
  you name) and your projects (change the projects folder in place, then **Add project** or
  **Add all**). Each step is a row in **Settings → This machine**. `ALTITUDE_OPERATOR` and
  `ALTITUDE_UPSTREAM_ISSUE_REPOSITORY` become initial values that a saved choice replaces; with no
  name, screens say “you” (#482).

- Each project sets its default model and reasoning effort for L3 and L2 separately on every engine,
  so changing L3 on one engine leaves L3 on the other engine and both L2 pairs alone.
  **Settings → This project**, opened from the project's three dots, holds these defaults, the L3
  engine pin and the last L3 turn's requested and reported effort. The project menu now holds actions
  only, and the phone **Project details** sheet is gone. `alt project set` adds `--l3-model`,
  `--l3-codex-model`, `--l3-codex-effort` and `--l2-codex-effort`. `--l3-effort` and `--l2-effort`
  now set the Claude defaults only. `GET/POST /api/defaults` replaces `/api/effort` and `/api/model`.
  A task's own `--effort`/`--model` still wins without changing the defaults.

- **Choose a folder elsewhere…** in First run browses folders on the computer running Altitude,
  starting at your home folder and staying inside it, and adds the current folder with one action;
  typing a path remains. Listings show folder names only, one folder at a time when you open it.
  The projects folder First run lists is a machine setting: **Settings → Projects folder** or
  `alt machine set --projects-folder PATH`, with `ALTITUDE_ROOTS` as its initial value (#479).

- Consecutive voice settings saves use the acknowledged selection immediately and preserve the
  next credential edit when a cache notification arrives later.

- **Settings → Voice input** selects the existing browser, local speech service or custom endpoint
  for every project. The compact overview opens a separate voice page; endpoint credentials stay
  write-only. Changes apply to the next recording, and a changed destination cannot reroute an
  unfinished upload (#317).
- The phone composer stacks like desktop and standard chat apps: the text field spans the top and
  Add images, microphone, send and the recording controls sit in their own row beneath it, in every
  state, so the send button no longer jumps beside the text when dictation lands.
- The phone swipe between Conversation and Live session follows your finger: the incoming view slides
  in as you drag, a release past half the screen or a flick completes the switch, a shorter drag
  springs back, and either end resists instead of wrapping. Reduced motion switches instantly.

- Clickable controls read as clickable: the task menu's **Review proposal** and **Review changes**
  entries, **View question**, **Discuss with L3**, the update banner's **Details** and the image
  viewer's controls are bordered buttons instead of plain text. The design tenet names recognisable
  affordance as a first-order requirement and the design review checklist checks it.

- `alt land` re-reads GitHub's PR view within a 30-second polling window after its own push until the view names
  the pushed head, instead of aborting on the stale head it replaced. A tip on `origin/<branch>`
  that the landing did not push still refuses before checks (#480).

- Voice input works out of the box through the browser's own speech recognition, with words
  appearing while you speak. `alt machine set --voice browser|local|<url>` selects the browser,
  the local speech service or an OpenAI-compatible transcription endpoint; the endpoint key comes
  from a file or stdin and stays in the private settings file. The desktop recording controls sit together at the right of the
  composer row with a crisp waveform; the phone row is unchanged (#317).

- While listening, the composer field follows the recognized words once they pass its height, so
  the latest words stay in view on phone and desktop (#317).

- The coordinator's runtime `alt` shim reads stdin only when an argument is `-`, so a verb whose
  text is an argument returns immediately even when the tool harness leaves stdin open
  (I-20260924-054556).

- `alt task status` names the merged commit's own push-triggered main run or none; the
  `alt land --merge` result no longer carries a `main_run` field, which named GitHub's latest main
  run or a hand-dispatched workflow instead (#476).

- Altitude is licensed under the Functional Source License (`FSL-1.1-ALv2`): any use except a
  competing commercial product, converting to Apache-2.0 two years after each release. The release
  archive ships the license and third-party notices; contributions require the CLA (#219).

- PR checks keep running once the repository is public: the owner's own branches keep the required
  self-hosted `check`, every other pull request runs the same suite on a GitHub-hosted runner without
  touching the owner's machine, and `alt land` requires the check wherever the base ships its
  workflow instead of naming one repository (#469).

- Incident issues publish only to the repository named by `ALTITUDE_UPSTREAM_ISSUE_REPOSITORY` in
  altd's environment. A fresh installation keeps incidents on the machine and shows that reason
  in `alt incident list`; the release metadata and source origin are no longer targets (#470).

- CLI help and errors say "the operator" instead of a person's name; `alt task block --for-burak`
  is `--for-operator` (#470).

- The repository is ready for public contributors: a [security policy](SECURITY.md) with private
  vulnerability reporting and scope, contributor guidance and issue templates written for fork
  contributors, and preview wording that no longer assumes invited collaborators (#219).

- Auto refreshes account quota without an interactive session, using native live usage reports.
  Missing or failed readings remain unknown; stale readings never become fresh by being reread.

- Setup reads crossing repair completion refresh the completed operation and verified Git guard
  receipts together; interrupted repairs remain distinguishable (#429). A retry accepted during
  a status read runs as soon as the read finishes instead of waiting for periodic maintenance.

- Fresh L2 activity previews scroll with chat and disappear after 60 seconds without fresh public
  output. Missing or unavailable output leaves no box; recorded output remains in Live session.

- Desktop chats use compact headers, with wrapping task titles and directly accessible actions.
  Task metadata and token usage open in Task details on phone and desktop.

- L3 keeps stalled authorized work actionable with an owned next step, a justified finite observation
  or a concrete decision when missing historical evidence prevents verified recovery (#386).

- Coordinator service reads expose bounded native worker termination and memory evidence;
  missing/collected units and unsupported fields remain unknown, with no service-control access (#384).

- Delayed image admission receipts preserve the accepted history row without adding a queued copy.
  Task reads crossing archival return ordinary not-found responses instead of failure tracebacks.

- Native helpers share a concise L1 persona, referenced explicitly in their assignments on both
  engines. L2 supplies task-specific scope, verifies results and retains delivery accountability.

- Launches, landing and restart builds discover the installed nvm default when Node is absent
  from PATH. Candidate installs run inside the web project so Corepack uses its pinned pnpm (#368).
- Private Linux x86_64 archives include the CLI, daemon and built UI, with per-user installation,
  prerequisite inspection and recoverable versioned updates. Uninstall preserves user data and
  referenced hooks. Fresh defaults use localhost HTTPS and a separate installation-local CA with
  explicit device trust and server-certificate renewal. Native macOS and clean-machine/provider
  acceptance remain pending; source deployments retain explicit lifecycle and network choices (#350).

- Model allowance exhaustion is recognized without inventing a reset time. Coordinators can use
  `alt task handoff` to continue an exited, fault-blocked owner as a fresh attempt on another
  configured engine, preserving saved work, task history, PRs, questions and merge holds (#310).
- The coordinator reconciles recorded UI merge choices and conversational reaffirmations using
  original-message citations, current question/hold evidence and the unchanged PR/head. Semantic
  interpretation belongs to L3; the daemon checks provenance and scope, records the release and
  leaves resume and normal checked landing to the owner (#294).

- Pending task designs can be reviewed from their conversation before merge: a versioned browser
  preview shows saved screenshots and proposal text, with a return to the existing question.
  Captures are confined to the owning task, active HTML is excluded, and replacement designs require
  a new question revision without releasing merge holds.

- Add private screenshot/photo input to project and task chat, compact previews and full-image
  viewing, durable retries and same-project image handoff to the assigned task owner.

- Protected Git hooks allow reference packing, loose-copy pruning and fetch garbage collection
  while main lags origin/main, preserving its tip and subsequent permitted fast-forward. Genuine
  unauthorized protected branch moves and deletions remain blocked (#291).
- Landing accepts a nonrequired skipped deployment with immutable condition
  `github.event_name != 'pull_request'` for an associated `pull_request` run. Required checks,
  exact candidate/source validation and at least one applicable passing check remain mandatory (#288).
- Mobile L3 and L2 chat use one compact header and composer, with bottom navigation hidden during
  detected software keyboard use and restored on dismissal. Task details hold metadata, full
  blocked/merge-hold reasons and existing actions; concise status, actionable failures and pending
  questions remain accessible. Drafts and reading position survive keyboard and details transitions.
- Full Python, web, build and phone/desktop browser checks run with disposable fictional state
  and deterministic external-engine fixtures. Core task delivery, messaging/resume and failure
  paths have programmatic integration evidence; routine checks make no model calls.
- Daily private-preview readiness and as-needed patch releases define candidate validation, versioning, notes and
  recovery. Version publication is explicit; merged changes continue activating automatically.
- Live-provider testing, including the real tiny validation task, is deferred under the operator's
  testing policy. See the [coverage limits](docs/DEVELOPMENT.md#coverage-and-limits).

- One command installs the latest published release on Linux:
  `curl --proto '=https' --tlsv1.2 -fsSL https://github.com/mburakyucel/altitude/releases/latest/download/install.sh | sh`.
  The script checks the machine and names the fix for anything missing, downloads the release's
  archive and installer, runs them only when they match the checksums built into the script, and
  prints the address, certificate fingerprint and next steps. On macOS it stops before downloading
  and reports what it found. Pushing an approved `v0.*` tag publishes the release: a workflow
  checks the commit's main `check` run and its dated changelog section, then builds, attests and
  uploads the archive, `install.py`, `install.sh` and `SHA256SUMS`.

- `alt update` with no arguments updates an installed Altitude to the newest stable published
  release: it looks the release up on GitHub, downloads the archive and its checksum, and applies
  them through the same verification, quiet-point activation and rollback as an archive update. It
  does nothing when the installed version is current. `--version` installs a named newer release;
  `--archive` with `--sha256` still installs a local archive. Registered projects keep current Git
  guards and dispatch on the updated version.

- An installed Altitude tells you when a newer stable release is published. The daemon asks GitHub
  twice a day (one anonymous request; **Check for new versions** in Settings turns it off). The app
  shows a dismissible notice with What’s new and **Update**, which after a confirm installs exactly
  that version through the same verified `alt update`, restoring the running version if it fails.
  Settings › This machine shows the version and the `alt update` command, `alt doctor` reports it,
  and a terminal `alt` command mentions it at most once a day. Nothing updates on its own.
