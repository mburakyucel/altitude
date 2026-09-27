# Changelog

Release notes describe user-visible behavior, compatibility and recovery. The project is an early
preview; see [release checkpoints](docs/RELEASING.md). An Unreleased entry is not a published release.

## Unreleased

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
