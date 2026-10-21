# Development and checks

Start with [CONTRIBUTING](../CONTRIBUTING.md) for scope and review expectations and
[setup](SETUP.md) for the supported runtime. Python uses the standard library; the web app uses
its pnpm lockfile. Routine checks use deterministic fixtures, make no model calls, and do not
use the operator's service or runtime records.

## Local checks

`python3 -m unittest tests.test_static_files tests.test_server_head tests.test_task_files`
checks website bytes and response metadata, index/SPA and inside-build links, plain/gzip replacement
races, missing assets, malformed paths, regular-file refusals and descriptor cleanup. Fictional
instrumentation raises on outside/private metadata attempts, covering traversal, aliases and
replacement. Resolved components classify missing assets; absent indexes are 503 and unsafe indexes
are 404. Positive 64-link chains, contextual hardlinked links and completed repeated visits preserve
link behavior; self/mutual/expanding cycles are refused. Pinned-build replacements retain complete
original bytes/headers without consulting the replacement. A bounded branching-alias case checks
complete successful responses and operation counts, while reused-address replacements are refused.
The checks warm the 64-entry compression cache and verify replacement and modification invalidation.
The terminal and document-update fixtures share independent-inode build copying; concurrent snapshot
creation/update leaves the original build's
bytes and signature unchanged, including while gzip is running. The Linux-only
execute-only-directory case requires an unprivileged process and proves directory listing is denied
while readable assets and the task-file search helper remain accessible; a root run is not evidence.

Contributors work from a repository checkout; the private archive is for running the application.
With repository access, clone it, then use the commands below from its root.
Owners and helpers run tests relevant to their changes during development. The required PR
check runs the full suite; a local full run is available for investigation but is not a landing prerequisite.
A Claude owner or helper on Linux runs these inside Altitude's bubblewrap sandbox, as on macOS inside its
Seatbelt profile: tools write their caches to the task's scratch folder, `/tmp` and `TMPDIR` are the job's own
temporary folder, gone when the job ends, the desktop session's sockets (its agents, display and editor servers) are
out of reach, and a program that starts a sandbox or container of its own (Chromium's sandbox, rootless Podman)
cannot start there, so those checks run through `alt task validate`. The sandbox's own socket cases
(`tests/test_session_sockets.py`) need real bubblewrap, which starts neither there, nor in the validation container,
which cannot mount a fresh /proc, nor on the hosted runner, whose AppArmor refuses it user namespaces; they skip in
those places, and `make sandbox-vm` runs them in a throwaway VM ([local VM run](#local-vm-run)).

Use Python 3.12+, Node 22.22.2+ (22.x), 24.15+ (24.x) or 26+, and the pnpm version pinned in
`web/package.json`. From the repository root:

```sh
pnpm --dir web install --frozen-lockfile
PLAYWRIGHT_BROWSERS_PATH="${ALTITUDE_HOME:-$HOME/.altitude}/browsers" pnpm --dir web exec playwright install chromium
make check
```

For a deliberately separate foreground preview, build with `pnpm --dir web build`, put `bin/`
on PATH, select unused runtime and TLS directories outside project/source roots, and choose a free
port with `ALTITUDE_PORT`. Run `alt tls-init`, follow the [certificate trust guide](SETUP.md#trust-https-on-each-device),
then `alt serve` with the same environment. It still needs the systemd user manager, or on macOS the
logged-in launchd domain, for workers.
Do not bind an existing service's reserved port or use its runtime state for a preview.
To run a checkout as the operator's service instead, follow [run from a source checkout](OPERATIONS.md#run-from-a-source-checkout).

`make check` runs the full Python suite alongside the ordered web unit, TypeScript/build,
full Chromium browser suite and headless-shell recovery lane. Each phase retains
wall/user/system timing through the standard-library `scripts/time_command.py`,
without GNU-specific `time` options. Each summary is written as one block so parallel
branches do not interleave it; the command waits for both branches and fails if either fails. A failed web prerequisite stops its
dependent phases. Python's stdlib `tests/run_parallel.py` deals the sorted individual tests across
fresh interpreters, so a long module spreads over all of them, using half the available CPUs (at
least one). The full gate obtains that budget from Node's `availableParallelism()` for both
languages: container CPU quotas may be smaller than Python's affinity mask. The runner itself
remains stdlib-only; standalone invocations default to half the affinity mask, or one worker where
affinity is unavailable. Each process streams prefixed verbose unittest output, including skip
reasons; the final summary totals all processes for landing. Test names flush before execution so
incomplete runs identify each process's last started test.
`python3 tests/run_parallel.py --workers N` selects a worker count for focused measurement.
`make check-python` and `make check-web` each run one branch of the gate; `SHARD=i/n` runs only
the i-th of n disjoint slices of every phase that has tests (Python, Vitest and both Playwright
lanes), and `WORKERS=N` sets the Python process count. The hosted check runs its slices this way.
Serial `python3 -m unittest discover -v tests` and `make test` remain available.
Dependency and browser installation are explicit prerequisites, so a warm run need not
fetch packages. L2 workers on either host export writable npm, pnpm, pip and XDG tool-cache
paths under `tool-cache` in their task's scratch folder (`~/.altitude-scratch/<project>/<task>`). XDG supplies the GitHub CLI's downloaded CI-log
cache too. Corepack keeps its original installed-manager location through `COREPACK_HOME`;
the pinned pnpm must already be available there. A missing manager remains a toolchain
prerequisite. When a worktree's existing `node_modules` uses another pnpm store, run
`pnpm --dir web install --force --frozen-lockfile` once to reinstall from the task's store;
ordinary frozen installs then reuse it. This replaces the shared temporary-store workaround.
Each task starts with an empty package store, so its first install downloads the locked dependencies
and requires registry/network access; package downloads are not shared between tasks. The forced
frozen reinstall also requires those packages in the task store or network access. It changes no lockfile.
Tool caches last through task resumptions and are removed with the scratch folder before task archival; they are not
archived as review evidence. Existing package-tool content/version checks and GitHub CLI log
cache rules apply; delivery still reads current candidate checks. The tools create their own
directories within the scratch folder. Outside an L2 worker, select a writable
store explicitly if your shell's defaults are restricted. Do not alter the lockfile to work around an installation failure. On a clean
Linux CI host, `playwright install --with-deps chromium` also installs browser OS dependencies.

Rejection confirms worker termination before cache disposal. If cleanup is refused, completion
or rejection retains its prior state instead of claiming success. The owner repairs only its
task's scratch folder: unlink a replaced link there without following its target, or restore
write/search permissions on task-owned folders in it, then retry the same action.
Never change unrelated target data or confinement. A terminated owner requests that bounded
repair through L3; an unavailable safe repair remains an explicit task-local dependency.
The folder holding the scratch folders may itself be a symlink; its resolved location anchors cleanup,
while links below it are refused.

Runtime files a test writes (monitor, incident ledgers and worker jobs) live under `self.runtime_tmp`,
inside the test runtime root, because runtime writes refuse paths outside it.
`tests/test_runtime_storage.py` plants leaf links, hard links and linked folders under every runtime
writer and reader and checks that nothing outside the root changes or leaks.
`tests/test_invocation_reader.py` checks that malformed task and worker names are refused before any
directory or record is opened.

On macOS the suite runs natively with Homebrew's `python@3.12`, `node@24`, `openssl@3`, `ffmpeg`,
`lcms2`, `webp` (Homebrew's `ffmpeg` decodes WebP but cannot encode it, so the image fixtures use
`cwebp`) and `bash` (macOS's own bash 3.2 has no bracketed paste, which the terminal walkthroughs
check), with `/opt/homebrew/opt/python@3.12/libexec/bin`, `/opt/homebrew/opt/node@24/bin` and
`/opt/homebrew/bin` ahead of `/usr/bin` on PATH. Routine terminal tests replace the service-manager
launch/stop at the platform seam with real throwaway shells in separate POSIX sessions. Cleanup
reads libproc and session membership on Mac, or procfs on Linux, without executing `ps`; zombies
and vanished processes are omitted, and a confirmed member that cannot be read fails cleanup.
The native platform probe separately covers job coalitions. The
walkthroughs press the platform's own editing keys (`ControlOrMeta`), so select-all, copy and paste
are Cmd shortcuts on a Mac while Ctrl+C still interrupts the shell. Cases whose fixtures stand in
for systemd set `host = "linux"`; on a Mac such a case reads its own process from a procfs fixture,
and the systemd-run and Podman stand-ins run on the suite's interpreter rather than `/usr/bin/python3`,
whose xcrun shim reports a validation run's read-only lookup cache in the logs they write.
The container archive cases skip where Python has no Linux extended attributes: the archive runs in
the Linux image.
`tests/test_platform_darwin.py` covers the macOS side of the platform seam with fixtures on any host.
`python3 scripts/platform_probe.py` checks the same mechanisms natively on either host with
throwaway jobs: piped and file output, logged and detached jobs, Stop of descendants that leave by
`setsid`, double fork, a cleared environment or continued forking, cleanup when the main process
exits, the time limit, also after its owner exits, macOS confinement (including the Simulator
service, a fixture app opened through LaunchServices, Seatbelt's verdict on sending an Apple
Event, which must be refused, and a read of a [private place](#macos-private-places)) and supervisor
tampering, process and socket facts, and the memory limit. `--service` adds a throwaway user service's start,
restart and stop. It prints one JSON row per check and exits non-zero when one fails.

Individual commands remain useful for focused development:

```sh
make test
pnpm --dir web test
pnpm --dir web build
make ui
```

The Python target clears `ALTITUDE_ACTOR` only for the isolated test process, allowing the
Git-guard installation test's operator CLI to act on its temporary repository. Operational
commands keep their worker identity. `tests.support` must be imported before application modules:
it supplies throwaway runtime/provider homes and refuses unexpected external engine/service
execution. Local Git repositories, scripted child processes and loopback HTTP remain available.
A focused module can be run with `env -u ALTITUDE_ACTOR python3 -m unittest tests.test_isolation`;
modules use the same bootstrap. The tests do not run the production daemon or its timer.

The fixture bootstrap discards `ALTITUDE_SOURCE_BRANCH` before config imports and redirects worker
tool-cache variables into its throwaway home. Source-launch/resume integration uses a fixture boot
identity with real PID/lifetime observations: production reads boot identity in the unconfined
daemon, while a native worker may be denied `kern.bootsessionuuid`. Kernel-reader tests supply
their own observations and retain explicit failure coverage; this fixture changes no production
identity or confinement policy. Reproduce these affected journeys with:

```sh
env -u ALTITUDE_ACTOR python3 -m unittest -v tests.test_session_processes tests.test_terminal tests.test_launch_source tests.test_task_tool_cache tests.test_engines tests.test_toolchain
```

Native Codex evidence establishes these deterministic task/fixture and tool-cache entry points
on the recorded Mac OS/architecture/revision only. Claude-native execution and the full protected
native browser suite need their own recorded runs. Until that evidence exists, a Mac owner records
the missing native journeys and uses the required Linux candidate CI for `make check` delivery
evidence. The [Mac validation lane](#macos-validation-runs) supplies a separate fictional
application environment; it does not establish native worker or browser-sandbox acceptance.
Browser recovery and native-runtime adoption stay with their owners. No overall macOS support
follows from fixture passes.

The broader native Python suite also has uncovered fixture gaps: other resume integrations still
read the confined kernel boot identity, Linux process fixtures access `/proc`, and container archive
fixtures require Linux extended-attribute APIs and filename behavior. Admission/queue assertions
can fail after those fixture errors leave a resume claim pending. These are not a native full-suite
pass; retain the failing module and traceback alongside the focused command above. Container
acceptance is tracked under #643 and native runtime/fixture acceptance under #225 and #617.
Required Linux candidate CI remains the full-suite delivery gate while those native gaps are open.

Clean-close report cases join their real turn-boundary L3 queue drain before inspecting faults
or releasing case patches and project registration. The command socket is a fixture because
scripted L3 turns do not call daemon verbs. An event-controlled regression holds a drain failure
until the join and checks its exact fault; background faults are neither filtered nor discarded.

Incident IDs are project-local, so the public issue marker adds a project digest.
`test_incident_issues.py` drives publication, retry, attachment and closure through the fake `gh`
shim, whose `issues.json` holds every issue, body, comment and close reason for assertions.
Independent Git fixtures created with `make_repo` use separate parent directories because each
bare `origin.git` lives beside its checkout. The fault/recovery reporting test gives its two
initial commits distinct timestamps so shared origins cannot hide behind identical commits.
The automatic-GC fixture uses `git repack -a` without `-d` to retain two packs and trigger real
fetch housekeeping. Unlike `pack-objects --all`, it supplies its own revision input rather than
waiting for the caller's stdin to close. Its auxiliary loose refs are created in one real Git
transaction, keeping repeated hook startup out of the bounded probe under concurrent suite load.
A bounded subprocess regression holds stdin open while
capturing stdout/stderr, matching landing's inherited-input condition, and checks the same real
packing, pruning, protected-tip and fast-forward assertions. The shared CLI fixture supplies empty
input explicitly; the same regression includes real issue-close CLI/API validation so it cannot
wait on the worker's input stream either. Tests that submit a body supply that input themselves.

## Noninteractive toolchain

Install a Node version supported by the project's `package.json` and enable its Corepack pnpm
shim (`corepack enable pnpm`). Corepack reads the `packageManager` pin from its working directory;
run package commands from `web` for this repository. Dependency installation stays frozen.

Altitude preserves a Node executable already on PATH, including an explicit project selection.
If Node is absent, launches, landing subprocesses and restart builds ask the installed nvm for
`nvm which default` and prepend that executable's directory. nvm lives at `$NVM_DIR` or
`~/.nvm`; its default must name an installed supported version with the pnpm shim enabled.
Resolution loads only `nvm.sh --no-use`, without interactive profiles or `BASH_ENV`, and does
not install tools, change aliases or choose versions from repository files. A missing or broken
default leaves the inherited environment available to other tools; failed resolution is logged.
Recovery is rechecked on the next invocation, without a cached failure.

An explicit unsupported Node or missing pnpm shim remains a setup error: select the supported
toolchain and enable its shim. Other installation managers continue to supply their normal PATH.
Ordinary shell commands such as `make check` use that shell's PATH; the shared discovery applies
when Altitude launches the process. Updated landing code resolves tools on each invocation.
Existing workers retain their environment and committed CLI export until resumed; normal
committed-source activation applies the correction to future launches/resumes without rotating
sessions or changing merge holds. Recovery of a worker still using the earlier CLI requires
the verified toolchain on its PATH or an ordinary resume after activation.

`test_toolchain.py` exercises minimal PATH, custom/default nvm locations, explicit Node precedence,
unavailable/default recovery, engine discovery, fresh/resumed worker execution and failed worker
results. `test_land.py` uses real Git repositories with fictional GitHub responses to check
the delivery gate and candidate identity.

## macOS private places

macOS keeps some places private and asks the operator before any process reads them: Desktop,
Documents, Downloads, Pictures (with the Photos library), Music, Movies, Mail, Messages, Safari,
Calendars, Reminders, Contacts, other apps' data (`Library/Containers`, `Library/Group Containers`),
iCloud Drive and file providers, the consent records themselves, and mounted volumes under `/Volumes`
and `/Network`. It names the program launchd started, so a worker's search of the home folder once
asked the operator about "Python 3.12". Altitude never needs these places, and a server must never ask.

`platform.private_places()` lists them (empty on Linux), each home place through the resolved home and
through `/System/Volumes/Data`. `/Volumes` is refused whole, including the startup disk's link there;
sandboxed code reaches the startup disk by its own paths. A sandbox's refusal comes before macOS's consent check, so a refused
read asks the operator nothing. Every sandbox Altitude applies refuses them as its last file rule, so no
root it admits reopens one: the worker profile, the validation profile, and Codex's coordinator
permissions. The same rule refuses secrets on both hosts (`platform.secret_places()`):
Altitude's access store and TLS folder, so no confined agent reads the machine key, paired devices or
the certificate keys, and the common browsers' profile folders that exist (`platform.BROWSER_PROFILES`),
so none copies a site's sign-in. Playwright's own browsers, under the cache folder, stay visible. Altitude code that runs outside a sandbox (altd, the coordinator launch,
doctor, the installer, the speech runtime) follows one rule: it never lists, walks or searches the home
folder or mounted volumes, and it starts no child in the home folder; it reads only its own named paths.
Folder selection, browsing, discovery and initial registration inspect paths through the platform
seam's shared descriptor traversal. It judges each candidate against the private policy before
metadata lookup and pins accepted ancestors; directory type, permissions, listing and `.git`
presence use that same namespace. Readable typed selections outside HOME remain supported, and
browser aliases may traverse a nonprivate exterior location before returning inside HOME. Container
containment compares inspected candidate and volume addresses. Registration checks final type
without requiring final read/list/execute access; selection requires read/search access.
Each caller retains its original raw/canonical alias budget, including logical expansion costs on
completed-target reuse and a separate directory-plus-marker budget. Website traversal retains its
strict build anchors, completed-address work bound and long-chain support. The server copy is removed.
These operation checks do not cover later Git, hook, engine or other runtime uses of saved path strings,
regular content modified in place or unrelated `platform.private` consumers. They do not establish a
global native privacy boundary.
`tests/test_folder_namespace.py`, `tests/test_folders.py`, `tests/test_static_files.py` and
`tests/test_platform_darwin.py` cover these contracts with fictional homes and deterministic replacements;
the [native probe](#local-checks) checks that a sandboxed read of the Desktop or `/Volumes` fails with
"operation not permitted" and that macOS's privacy log records no request for either (macOS logs a request even when it
already holds an answer); it reads only after Seatbelt's own verdict refuses the read, and otherwise fails
without reading.

## Browser walkthroughs

Build the candidate app before `make ui`; `make check` does this in order. Every test uses a
fictional populated project in a disposable Python service on an OS-selected loopback port.
The service serves this checkout's `web/dist` and real application HTTP handlers. No live project,
service URL or active worker is needed. Tests clean up their owned service processes and temporary
storage, with bounded termination. Never point routine acceptance at operator data.
Cleanup releases fixture gates and finishes HTTP requests and background work before closing L3
broker sockets; unfinished work or logged background failures fail the walkthrough. A service that
does not start within ten seconds, or exits while starting, fails with that startup error and its
output. Playwright starts a test's service before evaluating its skip conditions, so a test that
only one project runs is tagged `@phone-only` or `@desktop-only`, which the other project excludes.
The README service seeds its example records without flushing each to disk, so two services
starting together on a loaded runner stay inside that start limit.
The Weekly improvement review walkthrough releases a held press only after it reaches the route.
The image stream/admission walkthrough holds a real admission receipt until a history poll shows
its image, then holds later reads while verifying that receipt cannot recreate a queued copy.
The image selection/viewer/reload journey holds completion after the active turn clears and before
its retained claim is removed. Phone and desktop keep exactly one saved image and no queued copy.
The HTTP regression also forces this boundary after a failed turn, retaining immutable receipts,
non-removability and visible errors while recovery clears the claim.
The task-read regressions archive a real task between the live status lookup and its read: the
crossing poll and task action response return the archived record, a missing task returns ordinary
404, and corrupt state still logs a failure.
Document and event reads hold the archive lock; concurrent archive coverage verifies both snapshots.
Question-response, failed-setup and first-conversation retry checkpoints join their owned workflows before observing the
scripted outcome, so concurrent scheduling cannot inspect a half-finished resume or introduction.
The change-stream outage fixture reports its captured shutdown targets; connection cleanup cannot
erase them from the count before the response reaches the browser assertion.
The lost-queue-receipt test waits for the initial streamed reply before installing its overlay,
so the intended queued request is the only send it interrupts.
The live-loading measurement waits for the service to finish the reads its navigations abandoned
before measuring the warm open and the task switch, so those reads neither share the next phase's
CPU nor outlive the test.
The phone swipe walkthrough touches a view only once its switch has committed. A completed swipe
commits its view together with its URL; a tab click commits in a transition, so the walkthrough
waits for the previous view to leave, because Playwright counts a view beside the screen as visible.
The reattachment walkthrough forces an overlapping browser overview read while registration fails.
It releases the read after the error appears and keeps its route handler until teardown; removing
the last page route also continues held requests, so their callbacks cannot continue them again.
Terminal walkthroughs dismiss a notice in the state that shows it, before a fresh shell can outlast
its dwell. The lost-receipt walkthrough reloads only after the aborted send has settled from the
record. The project-isolation queued send intercepts the next `/api/chat` only after the running
request's partial reply streams, so the intercept cannot catch the running request.
Voice walkthroughs that WebKit also runs override `getUserMedia` on `MediaDevices.prototype`: WebKit
can collect the `navigator.mediaDevices` wrapper together with an override defined on it.
The push-wake walkthroughs read the worker's banners only once the browser records the push event
as completed: Chromium drops a banner's record when `getNotifications()` runs while it is being shown.

Both projects run headlessly: phone at 390×844 with touch/mobile user agent and desktop at
1440×900. With `CI` set, Playwright uses one worker per available CPU; local runs use two workers.
Python and browser phases overlap fixture waits. CPU and memory quotas
remain unchanged. Retries remain zero and CI refuses focused-only tests. Each service has its own
temporary homes and OS-assigned port; screenshots use per-test output paths.
Service termination finishes registering an accepted request before joining request threads,
so a signal cannot interrupt thread startup and leave cleanup joining an unstarted thread.
CI schedules individual tests across workers, so long spec files cannot hold one worker while
others sit idle. Local runs retain file-level scheduling.
Install the Chromium build matching the locked Playwright version. The browser cache
can be shared across worktrees through `PLAYWRIGHT_BROWSERS_PATH`; use the same value for install
and execution. Only this fictional local UI harness disables Chromium's own sandbox inside the worker
filesystem sandbox; profiles/configuration remain temporary or under
ignored `web/ui-artifacts/`. Missing browsers fail with installation guidance.
Incident I-20260907-041446 records an installed Chrome AppArmor network restriction in that worker
context. The full harness uses locked bundled Chromium in Altitude home's shared `browsers/` directory;
a missing bundle is a failed prerequisite. This historical observation does not diagnose other hosts
or establish that bundled Chromium supports its own sandbox inside a worker.

`make ui-shell` repeats the project-isolation/draft and file-reference/clipboard walkthroughs in
Playwright's locked headless shell at both viewports. The required check runs this lane after the
unchanged full browser suite. `pnpm ui:shell` selects it explicitly; output and HTML reports live in
`web/ui-artifacts/shell-results/` and `report/shell/` beside the full suite's retained report.
Both distributions use the same fictional services, permissions and temporary storage. The shell
lacks notification APIs needed by the alert
walkthroughs; it is a recovery path for these named journeys, not full browser parity. The primary
suite retains its full Chromium channel and all existing tests, workers, retries and gates.

`web/e2e/fixtures.ts` starts the disposable services. `acceptance-service.py` supplies fictional
tasks/history for route and component walkthroughs. Project-isolation and project-lifecycle
services exercise real streaming/queue/history and removal/reattachment; task-lifecycle specs
exercise real message/decision effects. Fixture providers replace external calls at the engine
boundary. HTTP overlays are reserved for named loading, transport failure and presentation states;
an intercepted success response alone is not evidence of a backend transition. Each test's browser
context fails any request beyond loopback at once, so no walkthrough waits on the internet. That
route also keeps request interception on, so an overlay that expires or is removed while the app
sends its next request cannot strand that request in Chromium; `route-overlays.pw.ts` guards this.

Loading walkthroughs scope assertions to their owning region; the Send now case holds independent
Conversation and Work reads together so both loading states are present on desktop.
In-process CLI fixtures capture the CLI's print sink so concurrent daemon diagnostics stay outside
the command's JSON output.

Chromium supplies a synthetic microphone and its permission for browser walkthroughs;
no test requests a physical microphone. On Linux the walkthrough browser also ignores the host's ALSA
configuration and plays to Chromium's fake output device, as on hosted runners: the host's sound
server plugin intermittently crashed Chromium's audio service, which ends the microphone.
Fixture services choose host voice. Composer voice journeys (conversation, project isolation, task
lifecycle, L2 progress, image input, file references, adversarial review, reported continuation)
overlay `/api/voice` and `/api/voice/live` with the fixture host in `web/e2e/hostVoice.ts`: the
page's real audio worklet hears Chromium's fake microphone, and the fixture can hold, replace or fail the final words, drop the connection or forget
the recording, so no speech model runs. Image-input voice journeys also hold `AudioContext.resume()`
pending: an output-device/renderer failure cannot strand their microphone fixture there. The cancel,
transcription, image send, navigation and denied states run at both viewports.
`voice-recognition.pw.ts` overlays `GET /api/voice` with `browser` and installs a page-level fake
`SpeechRecognition` it drives itself (Playwright's Chromium has no vendor recognition), walking
words while listening, landed, Send at once, cancel, failed, denied and no-recognizer states at
both viewports, and asserts that nothing reaches `/api/voice/live`. Vitest uses `FakeSpeechRecognition` from `voiceTest.ts` the same way.
Its Chromium silent-restart walk uses a tone followed by exactly silent synthetic streams and a scripted recognizer in project and task composers, checking the hint, draft, microphone focus and released tracks at both viewports. It establishes application detection, not native Safari recovery.
`host-voice.pw.ts` overlays `/api/voice` with `host` and answers `/api/voice/live` from a page-level
fixture, so no model runs; the page's real audio worklet turns the synthetic microphone into 16 kHz
chunks. It walks starting, live words (flowing in frame by frame, and at once under reduced motion), transcribing, landed, cancel, Send, stopped, busy, needs
setup, setup progress, ready and unavailable at both viewports. `tests/test_speech.py` runs the
supervisor against a fake worker process (load failure, hangs, crashes) and setup against a fake
download of small checksummed files; nothing downloads the real model.

`walkthrough.ts` drives actions, asserts text/roles appearing and disappearing, and saves named
screenshots. Route smoke checks real route discovery, content, assets, console/API failures and
horizontal overflow. Assertions validate behavior; no model browser agent or repeated AI image
review runs. For a human-requested headed walkthrough:

```sh
make ui UI_ARGS='project-menu.pw.ts --project=desktop --headed'
pnpm --dir web exec playwright show-report ui-artifacts/report
```

Named walkthrough screenshots and HTML reports live under ignored `web/ui-artifacts/`, grouped
by spec and viewport. Walkthrough screenshots remain on passing and failing tests; automatic
screenshots and traces are retained on failure. Passing tests discard their traces. The local report
includes attached walkthrough screenshots and failure traces in `report/data/`, with the trace viewer
alongside them. Under `CI`, walkthroughs still capture each state but do not attach it, so a failed
run's report holds the failure screenshots and traces in a few megabytes instead of every passing
state. Keep that whole report directory together when opening or sharing it.
Test artifacts contain only fictional test data. Keep actual service captures, session logs,
conversations and private incident evidence out of shared artifacts.

Review-only comparisons, proposal captures, implementation galleries and routine renderings stay
outside Git under the [project UI rule](../AGENTS.md#ui). Link the relevant evidence from the PR and
keep it accessible until review finishes. Use the local HTML report above or retained task artifacts;
if an upload is unavailable, provide the ignored evidence through an accessible review location.
Passing tests do not replace a pending visual review or release a merge hold. An owner can show
ignored or untracked captures and specs to the operator with [`--show`](CLI.md#showing-files).

Wireframe renders stay in ignored `design/wireframes/shots/`. The
[documentation renderer](../design/readme/README.md) also writes to ignored artifacts; only selected
illustrations that explain current behavior belong in `docs/images/`. Maintain the spec and useful
boards in place, removing obsolete review scaffolding and its generators/references together.

### Loading and caching evidence

`web/e2e/document-update.pw.ts` walks all three document entry points across replacement of the
actual built JS/CSS served by a disposable real server. Fixture controls use PUT requests, leaving
the application's GET and POST dispatchers real. Separate script, stylesheet and combined removal cases
establish readable-source recovery, saved-image access, draft preservation, repeated
document entry and explicit whole-page reload at phone and desktop widths. Styles load with the
app, so removal of an old stylesheet leaves the open page's formatting usable. Connection-drop
fixtures keep the browser cache enabled and verify same-script reload without module preloads.
Content transport abort overlays keep API Retry, whose successful read uses real API/storage;
offline content reads resume on reconnect. A successful
module whose renderer throws reaches the route error page at each entry point, rather than being
misclassified as a load failure. `file-references.pw.ts` and `shown-files.pw.ts` cover the readers'
loading, empty, denied, unavailable and image states; `app-update.pw.ts` retains terminal recovery,
and `first-open.pw.ts` keeps the cold/warm/slow opening checks and compressed-script limit.
Run these committed candidates through `alt task validate -- make ui-validate` and the separate
emulated-iPhone WebKit lane. Their fictional network failures establish application behavior, not
the response or network/TLS cause of a private browser incident.

Caching and prefetch changes follow the [project rule](../AGENTS.md#working-rules-for-every-pr): a
reviewed proposal, then a reviewed implementation. Measure before choosing. `web/e2e/first-open.pw.ts`
is the example: it opens its own browser context, because the harness's request interception turns
off the HTTP cache, and records each request's encoded size, encoding and cache use through the
Chrome DevTools Protocol, with `Network.emulateNetworkConditions` for slow links. Wait for a landmark
and the script's finished load, never network idle: the change stream stays open. Report cold, warm
and slow opens separately, and keep simulated links distinct from the operator's real connection.

`web/e2e/live-loading.pw.ts` measures cold/warm live entry, unchanged and changed updates, raw entry,
and navigation with 100, 5,000 and 20,000 varied fictional records on simulated slow 4G. It snapshots
the finite set of requests associated with each observation, records finished and canceled transfers
separately, and checks the recent landmark in the viewport. `live-history.pw.ts` walks upward loading,
failure/retry, complete traversal, late activity and old command results, raw disclosure, reconnect
anchoring and cancellation at both viewports. These use real task storage and HTTP handlers with
fictional projections at the engine seam. Loading/error overlays model transport conditions only.
Run committed candidates through `alt task validate -- make ui-validate`: Linux keeps Chromium's own
sandbox; the approved Mac fictional harness uses the existing Seatbelt runner. Retain reports and measurements.
The emulated iPhone lane remains separate from physical iOS momentum/rubber-band and native macOS
acceptance. Loaded history has proportional DOM/memory cost; recent-first loading is not virtualization.

## Fictional browser validation

`alt task validate -- make ui-validate` repeats the full fictional phone/desktop browser
suite against the task's committed candidate. `UI_ARGS=project-menu.pw.ts` selects a focused
journey, and `CAPTURE=1` with it keeps each selected journey as a [capture](#validation-captures). The command installs frozen dependencies and locked Chromium into the disposable run,
builds the web app, then runs a finite blank/local-fictional full-Chromium preflight before journeys.
It keeps stage logs, browser/version/executable digest, effective shared launch options, reports
and named attachments in `VALIDATION_RESULTS`; the daemon retains them privately on the task.
Profile/config/cache and package-manager storage belong to the run and its cleanup.
On Mac, `MAC_CHROMIUM_TMPDIR` directs Chromium's native temporary-directory lookup into the
same short owned folder: its pinned Apple implementation uses this override rather than `TMPDIR`.

Stock worker permissions and installed engines stay unchanged. Mac validation uses its existing
Seatbelt profile with Chromium's inner sandbox disabled, solely for this approved local-fictional
harness. This sacrifices separate renderer protection; shared Mach and network exposure and absent
CPU/memory/process quotas remain limitations. It is neither private/live-content verification nor
native worker, installation, Simulator or GUI acceptance. Linux validation keeps the container
and Chromium sandbox. No new operator-facing mode, runtime patch or lifecycle trial is installed.
The custom runtime preparation and trial route are retired, having never been adopted.

A pass requires the actual daemon receipt's candidate tree/profile identity, native job context
where applicable, finite browser evidence and successful process/area cleanup. The environment
marker alone is not isolation proof. Record deny/allow probes and any uncovered native boundary
explicitly. Failure ends the lane with evidence; no permission expansion or outside-runner retry
substitutes for acceptance. Full-browser failure with headless-shell success leaves full-browser
features and original phone acceptance pending. Simulator/device and phone journey acceptance
remain with their existing owners.

## Validation runner

`alt task validate [--kvm] [--publish PORT] [--simulator] -- COMMAND` runs one validation command for the calling
task's owner against its unmerged committed candidate, isolated from the operator's runtime: a
disposable rootless Podman container on Linux, a job under the validation Seatbelt profile on macOS.
The owner needs no machine grant, and ordinary worker confinement stays unchanged: altd, not the
worker, starts the run. altd accepts the request only from a process in the task's current worker
job. `make installation-vm`, `make browser-sandbox` and `make ui-simulator` call it automatically inside a task.

Owners use it to check a candidate before merging and to iterate without the operator: commit,
run, read the output and results, revise, commit and run again. Nothing is merged, deployed or
selected as the operator's runtime. For example, the dispatch and review journeys with deterministic
fixture engine processes, on either host:

```sh
alt task validate -- python3 -m unittest tests.test_container_workflow tests.test_direct_dispatch tests.test_review_engines
```

On Linux, direct process-test suites need a child process and a reaper. Running unittest as
container PID 1 makes a fixture's parent PID 1 look like the application and leaves killed
orphan processes unreaped ([runner evidence](https://github.com/mburakyucel/altitude/issues/905)).
This invocation passes the installation/TLS/platform suites in the same confined image, with
the original assertions and runner protections; it establishes fixture behavior, not native Mac
acceptance. The wrapper reaps adopted children while waiting for unittest and returns its exit status:

```sh
alt task validate -- python3 -c 'import os, subprocess, sys
child = subprocess.Popen(sys.argv[1:])
while True:
    pid, status = os.wait()
    if pid == child.pid:
        child.returncode = os.waitstatus_to_exitcode(status)
        sys.exit(child.returncode)
' python3 -m unittest tests.test_openssl tests.test_tls tests.test_push \
    tests.test_installation_lifecycle_harness tests.test_installation tests.test_platform_darwin \
    tests.test_release_publish tests.test_release_archive tests.test_project_layers
```

- **Image.** altd builds the only image from its own deployed `scripts/validation.Containerfile`
  (Ubuntu 24.04 with QEMU, nested Podman, Node and pinned Playwright Chromium and WebKit, including
  both browsers' system libraries). The image is tagged by the file's hash and is never built from
  a candidate. Keep the Playwright pin aligned with `web/pnpm-lock.yaml` when upgrading browsers.
- **Candidate.** The run sees a throwaway clone of the worktree's committed `HEAD` (at `/work` in the
  container), with the worktree's `origin/*` branches and tags. Its `origin` is the project's GitHub
  remote when it has one, otherwise the project's local Git folder. Uncommitted edits and the worktree
  itself stay out, and Git hooks are not run. The command starts in the clone; `VALIDATION_RESULTS`
  names its results folder and `ALTITUDE_VALIDATION=1` is set.
- **Container.** Every run uses fixed flags. The command runs as the image's non-root user,
  mapped to the operator's account with `keep-id`. Networking uses `slirp4netns` with the host's
  loopback unreachable. The container gets `/dev/fuse` and `/dev/net/tun`, plus `/dev/kvm` with
  `--kvm`, and has no host mounts beyond the clone, `/results` and a copy-on-write view of the
  cached Ubuntu cloud image. `--publish` forwards a container port to a free `127.0.0.1` port,
  never Altitude's own.
- **Limits.** One run at a time per machine, for up to an hour. Each run gets 8 GiB of memory without
  swap, 4096 tasks and four CPUs, and needs 20 GiB free for the runner. Disk use is not otherwise
  bounded.
- **Waiting and stopping.** A request that finds the machine busy waits for it, admitted in arrival
  order, for up to 70 minutes (one run's hour and its cleanup, the client's budget); after that it is
  refused with what still holds the machine. While it waits, `alt task validate` prints the task whose
  run holds the machine, when that run's limit ends and how many requests are ahead, and once it is
  admitted it says so with its own run's limit, so its last line never reads as waiting while the run
  goes on. Every check is repeated at admission. altd checks each request's connection every two
  seconds: a client that stops, is interrupted or loses its connection leaves the line, or, once admitted, has its run stopped and
  recorded as `stopped`. Stopping ends the run's processes (on Linux, SIGTERM and SIGKILL five seconds
  later), and cleanup follows, so the machine is usually free for the next request within a few seconds. The
  client ends when the process that started it ends, because an engine can cancel a command yet leave
  the command itself running and connected. A newer request from the same task replaces that task's
  earlier request the same way: a waiting one leaves the line, and an admitted run is stopped and
  recorded as `replaced`, naming when the newer request arrived. An owner that cancelled a run can
  therefore start its corrected command at once.
- **Machine runs.** Commands under an operator grant (`alt task run`) wait in a line of their own,
  apart from this slot: at most a quarter of the computer's cores run at once (at least one; two on
  an 8-core Mac), and `alt machine show` reports the number as `machine_runs`. A command beyond
  that waits in arrival order for up to 30 minutes, printing the commands that hold the places,
  when their limits end and how many requests are ahead, then that it was admitted. A client that
  stops or loses its connection leaves the line, or has its command stopped with that reason recorded, which frees its place.
  A command still running when altd restarts keeps a place until it ends. A task still runs one
  command at a time.
- **Storage.** The image, Podman's storage, the cloud-image cache and each run's area live in
  `~/.altitude-validation`, beside Altitude's home rather than in it and outside every folder a task
  worker writes, so no path a worker replaced becomes a mount or a place the run writes. On Linux,
  Podman's temporary folder is there too. Podman expects that folder to be emptied by every restart, as
  its default in the user's runtime directory is, and only then sets up again, including where rootless
  Podman records the user's pause process. The runner therefore empties it at its first Podman call
  after each restart, and the first run after a restart needs no operator step.
- **Results.** Regular files that the command writes to `$VALIDATION_RESULTS` are copied to the task
  folder's `validation/<n>/`, up to 256 MiB, and the run's output to `validation/<n>.log`. altd reaches
  that folder from Altitude's home without following links. Across the whole results tree, JSON/JSONL
  receipts are copied first, then LOG/TXT logs and Markdown failure contexts such as Playwright's
  `error-context.md`, then other artifacts such as traces and images,
  all sharing the same budget. Each group visits files before subfolders, in name order. Evidence must
  itself fit the limit; links and files that exceed the remaining budget are skipped and listed without
  truncation. The run's container or processes, clone and area are removed afterwards, including after a
  timeout or a stop. Cleanup of the processes and of every folder the candidate could write comes
  before the record: a run whose cleanup does not finish ends as `cleanup failed`, with the first entry
  that stayed and why, and keeps its area. The runner's own files in the area (the run's identity,
  delivery receipt, log and exit status) sit outside the candidate's folders and go after the record.
  Activation waits through execution, evidence recording and cleanup; validation admission turns runs
  away while activation is pending, including a request already waiting for the machine, so the
  quiet point opens after the run in flight. A request waiting for the machine does not hold activation.
  The line lives in altd, which restarts to activate, so `alt task validate` waits through the restart
  instead: it says Altitude restarts soon to apply an update, asks again every two seconds for up to the
  line's own wait, and joins the new line once altd is back. Requests that waited across a restart
  join it in the order they ask again, not their earlier arrival order.
- **Record.** Each run is recorded on its task like a [machine run](CLI.md#operator-grant), with
  purpose `validation`, the command, commit and tree, the host's OS and architecture, what isolated it
  (the image tag, or `seatbelt:` and the profile's digest), exit, how it ended and any cleanup failure.
  Only `ended: exit` with exit 0 is a pass; the CLI prints these facts and exits 1 for any other ending. A run that altd did not
  see end, including an expired run left by a host reboot or unexpected daemon exit, is stopped and
  recorded as interrupted at the next start. Its log and results are copied before scratch files are
  removed. A completed evidence copy is reused if ledger recording was interrupted. If evidence cannot
  be copied, the record names its original paths and the run area is retained for recovery. The runner
  admits no run until earlier areas are removed. Each start and each later request while an area
  remains retry its removal once the area's processes have stopped, so a leftover that becomes
  removable reopens the runner without a restart. Until then the request is refused with the area,
  entry or processes that stayed and why, on Linux with the cleanup's last output line, where Podman
  says why it could not run, also in altd's log.
- **Switch.** Settings → **Validation runs** is on after install. Turning it off stops a running
  run, including one admitted but not yet started, and refuses new ones. The switch is kept in the
  runner's storage, where a worker cannot turn it back on.

The container runner is available on Linux x86_64 with `podman` and `slirp4netns`. KVM needs the
operator's account to hold `/dev/kvm`, as it does during a desktop login. Options the fixed container
does not offer still need an [operator grant](CLI.md#operator-grant).

### macOS validation runs

On a Mac, where Podman would need a virtual machine of its own and there is no KVM, the same verb
runs the command as a launchd job under `platform.validation_profile`, a Seatbelt profile stricter
than a worker's:

- **Writes** only the run's own folders (clone, results, a home, and a short temporary folder
  `/private/tmp/av-<id>` that is its `TMPDIR`) and devices. The runner's files beside them in the area
  stay out of reach, and a suite that creates files directly in `/tmp` must use `TMPDIR` instead.
- **Reads** nothing in the operator's home or the shared temporary folders (`/private/tmp`,
  `/private/var/tmp` and the user's temporary and cache folder) except its own folders: Altitude's
  home and records, credentials, engine and GitHub sign-ins, checkouts including the deployment
  checkout, and other processes' temporary files and caches stay out; only xcrun's lookup cache is
  readable, never writable, so `/usr/bin`'s xcrun shims stay fast. Mounted volumes and the other
  [private places](#macos-private-places) stay out too. Toolchains outside the home
  (`/opt/homebrew`, `/usr`, the developer directory) stay readable. `PATH` keeps altd's entries
  outside the home, with the active developer directory's `usr/bin` just ahead of `/usr/bin`, so
  `git` (and `python3` when no earlier entry has one) run without the shims, which report the
  read-only cache as an `error:` on their stderr. The read-only task-file API traverses ancestors
  from `/` using search-only directory descriptors on Mac, without enumerating hidden folders.
  Every component still refuses symlinks; final status/document reads stay descriptor-relative
  and require ordinary file access. `tests/test_task_files.py` exercises this API inside a run.
- **Network** reaches the internet and loopback, except Altitude's own port on any address. Unix
  sockets are reachable at any depth in each of its own folders, such as the coordinator verb broker
  a suite binds under `TMPDIR`, and for the system's name resolution and log; other processes'
  sockets, such as an SSH agent, stay out. The run shares the host's loopback, so a
  server it starts binds a free port.
- **Keychain** lookups are refused, and launchd refuses service control to every sandboxed process,
  so a run cannot start, stop or change a service. It signals only its own processes.
- **Simulator and apps** stay out of reach: Apple's Simulator service and LaunchServices start
  programs as the operator's account outside any sandbox, and Apple Events script running apps with
  only macOS's Automation consent in the way, so the profile refuses all three, as a worker's does. A
  run cannot create, boot or run programs in a Simulator device, open an app or script one. With
  `--simulator`, altd gives the run one iPhone's Safari through a relay instead
  ([iOS Simulator runs](#ios-simulator-runs)).
- **Environment** is exactly `HOME`, `TMPDIR`, `PATH`, `LANG`, `ALTITUDE_VALIDATION` and
  `VALIDATION_RESULTS`, plus `SIMULATOR_INSPECTOR` and `SIMULATOR_HTTPS` with `--simulator`; nothing of altd's
  environment crosses.

The run time limit, one-run-at-a-time, free disk check, switch, restart fence, record and cleanup
are shared with Linux; there is no memory, process or CPU limit. altd removes the run's folders as the
operator's account, first giving every folder in them its owner's permissions back, so a folder a
suite left without write permission (such as protected-package fixtures) does not
stop cleanup. Links are removed, never followed. `--kvm` and `--publish` are refused.
`python3 scripts/platform_probe.py --only validation-confinement` checks the profile natively: a
fixture run in the home writes and reads only its own folder and is refused the home, the shared
temporary folders, a stand-in for Altitude's port, other sockets, the keychain, launchd and its
supervisor, a fixture app opened through LaunchServices, Apple Events, the Simulator service, the Desktop and
mounted volumes, while system
files, name resolution and its socket, other loopback ports, Git and its own sockets work, including
one nested in a later folder, as a run's temporary folder is. `--only simulator-escapes`, which runs only when named, as the
operator outside any sandbox (in a task, with `alt task run` under an operator grant), boots an
iPhone in a private Simulator device set, as the operator boots one, and removes the set afterwards.
Outside any profile, its Web Inspector socket must answer and a program spawned in it must write in
the home folder; from the worker profile and the validation profile, both must be out of reach. It
records whether Xcode's device services list devices from each and, where launching Settings on that
iPhone through them works outside, requires that launch to be out of reach from both profiles. They
see only the default device set, not the probe's private one, so on Xcode 27 that launch stays
uncovered and the row says so.

A process under the profile cannot apply another Seatbelt profile, so these do not run in a macOS
validation run: browsers that keep their own sandbox (see [browser verification](#browser-verification)),
Altitude's own worker confinement, and launchd jobs. Candidate application journeys there use the
suites' local-process job fixtures, and suite cases replace worker confinement at the platform seam,
so the full suite has the same result in a validation run, inside a confined worker and on Linux.
The exception is the read-only-home cases (`tests/test_worker_writes.py`, `tests/test_worker_roots.py`
and the read-only landing case), which confine a child with a stand-in for the worker's sandbox:
Landlock on Linux, a second Seatbelt profile on macOS. They skip, naming the reason, where the stand-in
cannot start: on a Linux kernel without Landlock, and in a macOS validation run or confined worker. On
the Mac they run only from an unconfined shell, such as the operator's terminal or the
[macOS VM run](#macos-vm-run)'s `read-only-home` phase.
A macOS run establishes the candidate's behavior on this Mac under those fixtures; it does not
establish native launchd/Seatbelt worker behavior, installation or provider compatibility.
The approved fictional browser lane below establishes only the journeys actually run.

The complete required suite runs in a Mac validation run with its dependencies in the run's own folders:

```sh
alt task validate -- sh -c 'export PLAYWRIGHT_BROWSERS_PATH="$HOME/browsers" MAC_CHROMIUM_TMPDIR="$TMPDIR" ALTITUDE_UI_BROWSER_CONFIG="$HOME/config" npm_config_cache="$TMPDIR/npm" XDG_CACHE_HOME="$HOME/cache" && (cd web && pnpm install --frozen-lockfile --store-dir "$TMPDIR/pnpm-store" && pnpm exec playwright install chromium chromium-headless-shell) && make check'
```

A Mac shared with running tasks runs the suite several times slower than an idle one, and workers' and
runs' timers are coalesced there: a 16 ms sleep has been measured at over 100 ms. Suite time limits are
therefore failure bounds that end a stall, never budgets a passing test has to meet: Vitest allows 30 s
per test and 10 s per `findBy`/`waitFor`, and walkthrough gestures carry their own touch times. A case
whose outcome depends on elapsed time holds its clock still and moves it itself, as the landing
contention cases and the alert pace case do, and computes relative times when it runs. Headless
Chromium there has no display to pace its GPU compositor, which stops animation frames after the first,
so `web/playwright.config.ts` composites in software on macOS, as Chromium does on Linux.

### iOS Simulator runs

`alt task validate --simulator -- COMMAND` on a Mac gives the run a disposable iPhone in the iOS
Simulator, which altd creates, owns and removes. `make ui-simulator` uses it inside a task to walk the
phone UI in iOS Safari, Add to Home Screen and the device setup profile. `STEPS` runs only the steps it
names, so one step can be repeated or skipped without editing the script:

```sh
make ui-simulator                               # inside a task: alt task validate --simulator -- sh -c 'make web && make ui-simulator'
make ui-simulator STEPS="profile home-screen"   # only these steps, from navigation dictation terminal https profile home-screen
```

- **Phone.** Before the command starts, altd creates one iPhone in a private device set in the run's
  area, outside the folders the run can write, and boots it headless. It picks the newest available iOS
  runtime and an iPhone of the newest generation it supports, the shortest-named model, by detection. The
  operator's own Simulator devices are never listed or touched. A boot takes about 35 seconds and the
  phone's data about 3 GB, inside the runner's 20 GiB free-disk check. Creating the phone, booting it
  and reading its inspector socket share one five-minute limit, so a step that a busy host slows can
  use the time the others left.
- **HTTPS identity.** Once the phone is up, altd makes the run its own CA and server certificate with
  the generator `alt tls-init` uses (`tls.fixture`), for `127.0.0.1` and `localhost`. It trusts that CA
  as a root in the run's phone only, with `simctl keychain add-root-cert`, and deletes the CA's key, so
  nothing else is ever signed with it. The run finds the CA's certificate and the server's certificate
  and key in the folder named by `SIMULATOR_HTTPS`, in its own `TMPDIR`. The operator's CA, the Mac's
  keychains and other Simulator devices are never touched; the CA goes with the phone.
- **Relay.** The run still cannot reach the Simulator service. It reaches the phone's Safari through a
  Unix socket in its own `TMPDIR`, named by `SIMULATOR_INSPECTOR`. altd relays Safari's Web Inspector
  protocol (the binary-plist protocol Safari's Develop menu uses) and filters it: only the web pages of
  Safari and of the Home Screen app (`com.apple.webapp`, which runs every web app added from Safari) are
  listed, under the same rules, other inspectable processes in the phone are hidden, a request about anything else (another
  process, a page not listed, an automation session) closes the connection, and a page whose address is on
  Altitude's port is hidden and closes any connection inspecting it. A run holds at most four connections
  at once, and a message that stops arriving partway closes its connection after 30 seconds. The page's
  address is read from Safari's listing, which updates shortly after a navigation, so a page can answer
  briefly before its connection closes (under a second: 0.3 to 0.8 s measured here). Simulator Safari
  holds no pairing, and Altitude answers an unpaired browser only with its page, files, health, access
  status and pairing. A request of the relay's own, `_rpc_altitudeOpenURL:`, opens an `http(s)` loopback
  address with a port, never Altitude's, in Safari: that is how a run puts its first page on the phone.
  Safari runs as the operator's account outside the run's profile, like a browser on this Mac: its pages
  reach the internet and loopback, and it has no Altitude pairing. The phone's inspector can drop a
  connection opened in the moment another one closes; a script that reconnects waits a second or two
  first.
- **Native walks.** Safari's own menus, the Home Screen and Settings are out of the inspector's reach.
  The relay's other request, `_rpc_altitudeWalk:`, asks for one of two fixed walks at an address the
  relay would open: `home-screen`, Add to Home Screen and the app it opens, or `profile`, a device
  setup page's profile through Settings. altd runs them with Apple's UI testing (XCUITest). At the
  first walk it copies the walks' source, one Swift file and a minimal Xcode project in
  `altitude/walks/`, from its own deployed code into the run's area, outside the run's folders, and builds
  it there; it never builds the candidate's copy. It then runs one walk at a time with `xcodebuild`
  pointed at the run's private device set, so the walk can reach no other phone. A walk writes each step
  as it ends, completed or not reachable with the reason, with a screenshot. altd returns the record and
  screenshots to the run; for a Home Screen walk it adds the web clip iOS made, read from the run's phone's
  data: the clip's title, address and full-screen setting and its stored icon. After each walk altd closes
  Safari and Settings, so neither keeps a connection to the run's pages. The Home Screen app a walk opened
  stays open for the run to inspect, and altd closes it before the next walk; a run that inspects it leaves
  its page before stopping the service it serves, since an open app keeps the change stream open. The walks wait
  for each control a bounded time and unwrap no missing value, so the test runner ends its walk rather
  than crashing. A walk has seven minutes; past that altd interrupts `xcodebuild` as Ctrl-C does, so it
  stops its runner itself. A runner that crashes, does not finish or does not build ends the walk as a
  recorded failure with `xcodebuild`'s error lines.
- **Removal.** When the command ends, including after a failure, a timeout or a stop, altd keeps one
  screenshot of the whole screen as the task folder's `validation/<n>.simulator.png`, then shuts down
  and deletes every device in the run's set and removes the set. A set that stays keeps the run area,
  ends the run as `cleanup failed` and keeps new runs refused until a later request or start removes
  it. A phone that does not start is removed and the request is refused with Simulator's error, before
  any run is recorded.
- **Capture.** With `--capture` (`make ui-simulator CAPTURE=1`), altd also records the screen from just
  before the command starts and keeps it as `validation/<n>.simulator.gif`; the recording stops when the
  command ends, forcefully after 10 seconds (see [validation captures](#validation-captures)).
- **Record.** The run's record and status line name the Xcode version, the iOS runtime and build,
  the iPhone model, Safari's version, the screenshot and any capture. The record also keeps both
  certificates' subject, issuer, validity, key, signature, extensions and SHA-256 (`certificates`). On Linux, or on a Mac without Xcode or an iOS
  Simulator runtime, `--simulator` is refused with the reason.

`scripts/ios_simulator.py` is the walkthrough. It serves the built app with the fixture engines and
fictional data of `web/e2e/acceptance-service.py` on loopback, pairs Safari as the fixture's device and
finds the fixture project's work and a task in it. It then runs these steps in this order, all of them
unless `STEPS` (its `--steps`) names some, separated by spaces or commas:

- `navigation`: the project's work in the phone layout, a task and Back (`01-work.png`, `02-task.png`,
  `03-back.png`).
- `dictation`: voice input's restart after the X (below).
- `terminal`: a task terminal and the project terminal of `web/e2e/terminal-service.py`, a real shell on a
  pseudo-terminal, which pairs Safari as its own device (cookies belong to the host whatever its port, so
  the step pairs Safari back afterwards). A focus with a user gesture stands in for the tap that opens the
  soft keyboard, an input event for typing and synthetic touches for a drag. The task terminal opens at its
  prompt with the keyboard closed; once the keyboard opens, the visual viewport shrinks, the shell gets
  fewer rows in one resize, and the prompt and the key row stay above the keyboard; typed text reaches the
  shell; a drag scrolls back, output written meanwhile leaves the earlier lines in place, and typing returns
  the screen to the prompt; a reload with the keyboard open reattaches to the same shell and replays its
  output at the prompt, with the keyboard closed; the key row's Enter with the keyboard closed sends Return
  and leaves it closed; Altitude restarting under the page shows that the terminal closed while the
  connection was lost; and the project terminal's prompt and key row stay above the keyboard
  (`terminal-1-opened.png` to `terminal-12-project-keyboard.png`). The requests the restart cuts off are the
  only console errors it accepts. A finger's tap, the keyboard's own Return, Paste's permission prompt,
  rotation, the app switcher and the lock screen are beyond the inspector.
- `https`: a page over HTTPS on another loopback port with the run's certificate, through the serving
  context Altitude's server loads, which Safari must load as a secure context with no certificate
  warning (`05-https.png`). As a control, a fetch from a second identity of the same generator, which
  the phone does not trust, must be refused for its certificate: Safari logs a message about that
  request and the server receives a certificate alert, and both are recorded. Safari's message about
  that one request is the only console error the walkthrough accepts. A pass shows that iOS accepts the
  generated chain once its CA is trusted.
- `profile`: the [device setup page](SETUP.md#share-with-a-desktop-or-phone), as `alt tls-share` offers
  it, for a new CA from the same generator, whose key is deleted; the page must name the CA and its
  SHA-256 (`06-setup.png`). The profile walk then taps **Download the profile** and **Allow** in Safari,
  opens **Profile Downloaded** in Settings, reads the profile's name and, under **More Details**, the
  certificate's SHA-256, taps **Install** past the unsigned-profile warning and its confirmation, and
  turns the CA on under **General › About › Certificate Trust Settings**, past the root certificate
  warning. The name and SHA-256 must be the served CA's and the CA turned on must be it. The CA also
  issued the [pairing screen's trust check](ARCHITECTURE.md) its second certificate before its key was
  deleted. Altitude served with that CA's certificate (`web/e2e/trust-service.py`, which treats the
  phone's loopback connection as another device's) must then load as a secure context with no warning,
  its trust check must answer Trusted with no request of Safari's failing (`profile-trusted.png`), and
  a code must pair the phone (`profile-paired.png`). The phone never trusted that CA before, as the
  `https` control shows for another CA of the generator.
- `home-screen`: the Home Screen walk at the app's root and at the task's address. It opens the address
  in Safari, taps **Share** in the page menu, **Add to Home Screen** and **Add**, finds the icon on the
  Home Screen and opens it.
  The sheet must offer the title Altitude with **Open as Web App** on. The web clip must have the title
  Altitude, the manifest's start address (`/`, from either address) and full screen. Its icon, which iOS
  stores re-encoded, must show the approved Climb `apple-touch-icon.png` pixel for pixel; both files'
  SHA-256 are recorded. The icon must open the app with Safari not in front, already paired with no
  new device: iOS copies Safari's cookies to a web app when it adds it, so the app keeps Safari's
  pairing. Safari's pairing cookie lasts as long as a paired device's, as the server sets it, so it
  survives the walks closing Safari. The walkthrough then inspects the Home Screen app the task's walk
  left open, which must be standalone (`navigator.standalone`), runs the voice journey below there as well
  (`home-voice-…` snapshots, `home-dictation.json`, `home-voice-report.json`, the app's console in
  `home-console.log`) and leaves the app, also after a failure; a console error in the app fails the step.

The profile and Home Screen steps record a row per walk step and per check of what it showed:
completed, not reachable with the reason, or failed with what differed, and a failed `runner` row when
the walk's runner crashed, did not finish or did not build, even after its last step. Their screenshots are
`profile-<step>.png`, `home-root-<step>.png` and `home-task-<step>.png`, with the icon as the Home
Screen draws it (`…-home-screen-icon.png`) and as iOS stores it (`…-web-clip-icon.png`). The
walkthrough also keeps Safari's console (`console.log`), the fixture service's log, the steps and the
browser's user agent, viewport and speech-recognition support (`walkthrough.json`) in
`$VALIDATION_RESULTS/simulator`. A step that does not reach its state, horizontal overflow, a console
error, a walk row that is not completed or a fixture service that does not end cleanly fails it. The
walkthrough leaves the app only between its requests, since Safari logs a request cut off by leaving as
a console error, and leaves it before the native walks, which close Safari.

The voice journey walks the restart after the X of [issue
698](https://github.com/mburakyucel/altitude/issues/698) with browser recognition. It starts voice
troubleshooting diagnostics in Settings; then in the project's composer and a task's, without
reloading, it types a draft and three times taps the microphone, receives words and cancels with the
X, with a snapshot per state (`voice-1-` to `voice-4-`). Each round passes when the words and a
moving waveform appear (a tall bar, then a changed frame from a tone that swells twice a second),
the X restores the typed draft, focus stays on the microphone and the viewport keeps its height, and
the recognizer, stream and waveform audio context are released. Focusing the field afterwards must
shrink the viewport for the keyboard, so these checks can see one (`dictation.json`). The diagnostic
report (`voice-report.json`) must show six captures, each with a waveform signal in at least two
samples, and none of the draft's or dictated words. A round that stops keeps the page state
(`dictation-failure.json`) and the report so far.

Simulator Safari's microphone request and speech recognizer stop at native permission dialogs that
the relay cannot answer, and granting them would record this Mac's room and send it to Apple. The
journey therefore uses a tone from Safari's own audio engine as the microphone and a scripted
recognizer. It establishes the composer's capture lifecycle, waveform graph, timers, focus and
keyboard behavior in iOS Safari, but not native audio capture, the native recognizer or spoken
words. Those remain a physical-iPhone observation with the diagnostics on. The tone is never exactly
silent, so the walk does not reach the silent-microphone stop that answers iOS 27's once-per-tab
recognizer; unit tests and the Chromium silent-restart walkthrough cover that stop.

The inspector steps' taps are page events marked as user gestures; the walks' taps are UI testing's
touches on the Simulator's screen. The lane establishes iOS Safari's rendering, layout and WebKit APIs
in a phone-layout journey, and Add to Home Screen and the profile's download, install and trust, on the
Simulator's iOS version; it is not physical-iPhone acceptance (see [device evidence](#device-evidence)).
A phone reaching Altitude by its private address, the profile that `alt tls-share` serves on the
operator's network, a passcode prompt during Install (the Simulator's phone has no passcode), a finger's
touch and other iOS versions stay physical-iPhone observations. The relay depends on Safari's
unpublished inspector protocol, and the walks on the labels and identifiers of Safari's and Settings'
controls, so an Xcode or iOS update can break them; the run then fails with the versions recorded and
the step not reached. `tests/test_simulator.py` covers the relay's filtering, the walk request, the
walks' build and their failures, the device lifecycle and its recovery with a fixture `xcrun` and
inspector; a real phone is recorded evidence from a Mac.

### Browser verification

Worker confinement and Chromium's own sandbox are separate protections. Candidate browser checks
use `alt task validate -- make ui-validate`. On Mac, the approved fictional harness uses the runner's
existing Seatbelt protection without Chromium's inner sandbox; on Linux it uses the container and
`web/playwright.validation.config.ts` with `chromiumSandbox:true`. Playwright defaults that option
to false. `web/playwright.config.ts` owns the fictional harness used by required CI and Mac
validation. E2e specs never set their own `launchOptions` or sandbox flags; the shared configuration
owns them and `tests/test_repository_instructions.py` enforces this boundary. Evidence identifies
which configuration actually ran. Use only blank/local fictional content with finite execution,
disposable storage and cleanup in the approved Mac lane.

Checks requiring both protections use the Linux container; Mac cannot nest another Seatbelt profile
inside its validation profile. Neither fictional Mac success nor Linux results establishes the
original native-worker browser context, deployed/private-content or Simulator acceptance.

Actual-image container browser checks from an Apple silicon Mac use the fixed
`alt task validate --container-browser` lane, not `container_vm.py --browser` on the outer Mac.
The deployed controller owns a bounded throwaway Linux guest and a rootless, non-root network-none
Chromium container. Guest NAT is physically disconnected before candidate execution. Local sockets
carry fixed fixture actions and raw published HTTPS; fresh browser trust contains only the public
fictional CA. The receipt records actual guest/container/browser identities, finite dual-protection
preflight, network/TLS/IPC controls, journeys and cleanup. The current standing VM-policy grant is
required, but browser acceptance still belongs to validation. See
[container browser validation](CONTAINERS.md#container-browser-validation).

`make browser-sandbox` is the Linux dual-protection check. It launches Chromium this way against a local fictional page and
writes `browser-sandbox.json` to the run's results: browser version, launch options,
`chrome://sandbox` and each renderer's user, seccomp mode and user/PID namespaces. It passes only when
Chromium reports itself adequately sandboxed and every renderer runs as a non-root user under
seccomp, in its own namespaces. A successful launch alone does not prove every protection a task
needs. Keep the isolation evidence the task requires.

WebKit's finite prerequisite check opens a local fictional page with the emulated iPhone settings
and records the Playwright/browser versions, executable, non-root user and page result:

```sh
alt task validate -- sh -c 'cd web && pnpm install --frozen-lockfile && node ../scripts/webkit_smoke.mjs /results/webkit-smoke.json && node ../scripts/browser_sandbox.mjs /results/browser-sandbox.json'
```

Run both checks when changing the runner image. A candidate image can be built and tested
with nested Podman inside a validation run; this does not replace the deployed runner image.
After source delivery and normal activation, repeat both checks through `alt task validate` and
retain its commit, image tag, log and result paths before declaring runner recovery. Linux desktop
WebKit emulation does not establish native macOS or iOS acceptance.

Never disable worker/runner confinement or add ad hoc sandbox-bypass flags or chmod/chown a SUID
helper. Chromium's inner sandbox is disabled only by the approved fictional harness configuration.
If the required runner/protections are unavailable or browser preflight fails, retain evidence and
block with `--fault`; no operator grant supplies outside-worker browser acceptance. Altitude's
fictional exception grants no authority for another project's checks.

The queued-message **Send now** walkthrough uses real queue/task storage with a deterministic engine
turn that takes the message in, in `web/e2e/send-now.pw.ts`. Run the focused candidate with:

```sh
alt task validate -- make ui-validate UI_ARGS=send-now.pw.ts
```

The state walkthrough covers loading, queued controls, sending into the current turn, the split reply,
delivered and removed rows, denied and unconfirmed requests, unavailable delivery, waiting for the
current chat turn and waiting for system work.
`tests/test_engine_driver.py` runs the real engine driver against `tests/fake_engine.py`, a fixture
speaking both engines' streaming protocols: delivery with a command in flight, the turn-end race, a
message after the job ended, and receipts written without altd. Python fixtures additionally cover
claims, Stop recovery and exactly-once delivery across restart. The emulated iPhone lane remains separate evidence.

## Validation captures

A lane records nothing unless asked. With `CAPTURE=1` it keeps a few small looping GIFs with that run's
evidence, so the operator can watch what it did from the task conversation:

| Lane | Request | Capture |
| --- | --- | --- |
| iOS Simulator | `make ui-simulator CAPTURE=1` (`alt task validate --simulator --capture`) | The iPhone's whole screen from just before the command starts to its end, recorded by altd with `simctl io recordVideo`, as `validation/<n>.simulator.gif` beside the screenshot |
| VM installation | `make installation-vm CAPTURE=1` (`--capture`) | An accelerated replay of the lane's progress lines and harness logs, each line at the time it arrived and led by its real elapsed time, in an 80×24 terminal that fills and then clears like a pager, under a top line naming the commit and current step, as `captures/installation-vm.gif` in the results |
| Browser walkthroughs | `make ui-validate UI_ARGS=spec.pw.ts CAPTURE=1`; also `make ui` / `make ui-ios` | Playwright's video of each selected journey, one GIF per journey and viewport, in the results' `captures/` (`web/ui-artifacts/captures/` or `web/ui-artifacts/ios/captures/` outside the runner) with `captures.json` naming each outcome |

The browser lanes refuse `CAPTURE=1` without `UI_ARGS`, so a whole-suite run never records hundreds of
journeys, and `make check` never records. `altitude/capture.py` makes every capture the same way: 4 frames a
second, unchanged frames dropped, a still screen held at most 2 s, at most 60 s of playback (a longer run is
sped up evenly), 64 colours without dithering, phone captures 390 px wide and desktop ones 800 px. A capture
is at most 1 MiB, 1024 px a side and 300 frames, and a run keeps at most 12; one over budget is re-encoded
once at three-quarter size and otherwise not kept. Measured phone and desktop journeys are 0.3–0.6 MiB for
about ten seconds.

`ffmpeg` is an optional capability found on `PATH` (altd's own for the Simulator, the run's for the other
lanes); the validation image installs it. A capture never decides a lane: a recording, conversion or budget
failure is the capture's own outcome (`none: ffmpeg unavailable` in the run record's `simulator.capture`,
`vm.json`'s `capture` or `captures.json`) beside the lane's unchanged exit and evidence. Recordings and frames
stay in the run's own area and are removed with it; Playwright's HTML report keeps each journey's WebM like
any Playwright video, and the per-test copies are removed after conversion.

`alt task reply --capture <n>` attaches run `<n>`'s captures to the owner's reply. Each is read from the task's
own `validation/` evidence without following links and must parse as one GIF within the limits above; the
reply keeps content-named copies in the task's `captures/` folder, at most 64 MiB per task, so later edits to
the run folder change nothing it shows. The conversation shows **Watch capture · title** (or **Watch N
captures**) under the reply, opening a page in a new tab where each GIF loops at its recorded size with its
title, size and length; a missing or altered file shows **Capture unavailable** with Retry. Captures hold
only the lanes' fictional data, never enter Git or a PR diff, and are removed or archived with the task's
other evidence.

## Validation environments

Each environment establishes one kind of evidence; running more of them does not widen what any one
proves. A result names its environment, entry point, tested revision, OS and architecture, and is
kept with the PR or task that ran it. An environment that was not run is a missing result, not a
pass. Only `make check` gates delivery; the others are run for the changes they cover. Each entry
point is one command an owner runs against its candidate, leaving pass/fail evidence; a gap names what
blocks automating it.

| Environment | Entry point | Establishes | Does not establish | Status |
| --- | --- | --- | --- | --- |
| Linux CI container | `make check` ([required PR check](#ci-and-candidate-identity)) | Application, API/storage integration, systemd unit-file parsing (`systemd-analyze verify`, without systemd running) and phone/desktop browser flows with fixture engines | Clean-host installation, user services, reboot, native macOS, container deployment | In use |
| Disposable Linux VM | `make installation-vm` ([local VM run](#local-vm-run), in a task through the [validation runner](#validation-runner)) | Fresh install, user-service start, update, failed-update recovery, the service running on after its SSH login closes and starting after a restart with nobody logged in, uninstall, and the built or published `install.sh` through its public command against a release server inside the guest, including an update from a published release (`BASELINE`) and an installation over a failed one (`RECOVERY`); with `PUBLIC`, setup's command and the installed daemon's update lookup and `alt update` against GitHub itself; with `TRUST`, the installer's consented certificate trust opening its private link in Playwright's Chromium and Firefox without a warning; on Ubuntu 24.04 x86_64 | Desktop login/logout, distribution or snap browsers, a physical machine, the app's Update button from a published release, storage migration (no application state is created), other distributions | In use |
| Disposable Linux sandbox VM | `make sandbox-vm` ([local VM run](#local-vm-run), in a task through the [validation runner](#validation-runner)) | Altitude's bubblewrap sandbox hiding a fictional runtime folder, shared temporary folder and abstract socket while keeping the job's own sockets and a coordinator's broker, hiding an engine's folders and files of the operator's own apart from what the job's launch reopens, and hiding fictional stored credentials while a worker's Git and GitHub CLI resolve the task's token for GitHub, offline, with real bubblewrap under Ubuntu's AppArmor profile and Landlock's abstract-socket scope, on Ubuntu 24.04 x86_64 | A desktop session's own programs, a Codex coordinator's own sandbox, macOS | In use |
| Disposable macOS VM | `make installation-macos-vm` ([macOS VM run](#macos-vm-run), in a task through `alt task run` under the [standing VM lane approval](../AGENTS.md#vm-lane-runs-on-this-mac)) | The built `install.sh` through its public command in fresh macOS 26 arm64 guests with automatic login and no network: its refusals with their documented fixes for missing Python, absent OpenSSL 3 and an account with no desktop session; discovery with stock LibreSSL first and Homebrew OpenSSL installed; the [macOS installation lane](#macos-installation-lane)'s lifecycle under a throwaway HOME, including automatic updates, the automatic-updates-off confirmation and recovery from a failed automatic update; and the account's own installation, its printed private link pairing a browser once, a second run of the public command keeping that browser and the certificate, its HTTPS health, `alt doctor`'s next step through first run and a project waiting for a coding tool with no incident, `alt trust` from an SSH session refused for the login keychain with no trust setting added, the SHA-256 undo command removing the certificate, `alt trust` on the desktop through macOS's password window trusting the authority for SSL only and its printed undo removing that, Safari refusing the address before and pairing through the pair screen, a printed link and a link macOS opens after, with used and malformed links refused and no code left in its address or history, the service moved to a VPN-range address with Safari trusting and pairing there and back, its LaunchAgent started again by the automatic login after a restart, and uninstall; with `--phase online`, in a guest that keeps its network, Homebrew's Python 3.13 and Chrome and Firefox from their publishers, the latest published release installed from GitHub itself with its service on Python 3.13, and Chrome and Firefox refusing Altitude's address before `alt trust` on the desktop and opening it after; the confinement probe's refusal of private places with no consent request, in a guest whose Python was never asked; the job probe's launchd jobs, whose Stop and time limits end every descendant, a pseudo-terminal's child among them, and whose idle-sleep assertion lasts as long as the job, a confined job reaching sockets only in its own `TMPDIR`, which goes when it ends, and the awake-time clock their limit's wait counts; interrupted mutation runs leaving no scratch folder or process behind; the suite's read-only-home cases, each confining a child with Altitude's Seatbelt profile or a Codex coordinator's own; a source checkout cloned from an origin in the guest, whose first start creates Altitude's own copy of its repository and runs from it, and whose service deploys a newer main pushed to that origin through the copy; the installed service with a fictional engine and GitHub CLI: a coordinator's Stop, a worker ended at its failed turn, unknown worker status, a validation run and a granted command across the service's restart, revocation, an answer reused for another task, a second command waiting for the machine-run place, Settings' Claude sign-in with a fictional setup screen saving its token privately and leaving nothing behind after finishing, cancelling or a restart, an incident held behind a newer release, the release lookup's 12-hour schedule and once-a-day notice on the guest's moved clock, an operator's command waiting in Needs you, as Safari renders it, until the project terminal Safari opened runs it to its end, a paired cookie admitted from Safari and refused from a worker's job, a Codex worker signing in with a handed-over token and refreshing it through the task door, repairs from the account's shell and the installer with their outcome in Settings, refused from the page terminal, and repairs from the page in launchd jobs that outlive a restart and a reinstall and end at Stop, their deadline and logout, an automatic update waiting for a validation run, holding new runs until it has restarted and installing itself once the run ends, and for an open project terminal until it closes, with Safari's notice and Settings naming what it waits for, its installation and its result, a worker's scratch folder removed at completion, a coordinator channel refusing task workers and other projects' coordinators, and logout and login without a restart; with `PUBLISHED`, a published release's own daemon, with an outside process's caches in its release, installing a later published or source-built release and keeping the state made through the app, a project and a planned task's conversation among it | A physical second Mac, other macOS versions, other Apple chips, an actual sleep (the guest refuses it; the `real-sleep` row crosses one on the Mac itself), an iPhone's trust, a schedule's real elapsed hours, a release candidate's lookup | In use on Apple silicon |
| Hosted installation workflow | `installation-lifecycle.yml` ([lifecycle acceptance](#installation-lifecycle-acceptance)) | The same harness on GitHub's Ubuntu 24.04 runners | As for the VM | Not executed: hosted-runner spending limit |
| Validation container | `alt task validate -- COMMAND` ([validation runner](#validation-runner)); `make browser-sandbox` | A committed candidate's command in a disposable rootless Podman container, including nested rootless containers and Playwright's Chromium with its own sandbox | Running Altitude itself in a container, other hosts' kernels or Podman versions, native macOS | In use on Linux x86_64 |
| macOS validation run | `alt task validate -- COMMAND` on a Mac ([macOS validation runs](#macos-validation-runs)) | A committed candidate's command on macOS under the validation profile, with fictional state and fixture engines: the Python suites and application journeys with local-process jobs | Native launchd jobs and worker confinement, browsers with their own sandbox, installation, provider compatibility | Implemented; native acceptance is recorded on the delivering PR |
| Container deployment | `make container-vm RESULTS=dir`; `scripts/container_vm.py RESULTS --image-workflow [--native-sandbox-binary PATH]` or `--browser` through the validation runner | Actual rootless launcher/image, quotas, published local HTTPS, Stop/restart/replacement, a handed-over Altitude sign-in kept across replacement, interrupted-build and supervisor cleanup, private backup/restore and failure cleanup, neighboring-container isolation and service-manager attempt detection; separate image profile/workflow/recovery fixtures | Physical-device routing/trust, real authentication/provider-session compatibility, Mac, native installation | Ubuntu 24.04 amd64 launcher/backup lanes pass; actual-daemon phone/desktop onboarding and task lane passes; [coverage and limits](CONTAINERS.md#evidence) |
| macOS installation lane | `make installation-mac RESULTS=dir` ([macOS installation lane](#macos-installation-lane), in a task through `alt task run` under an operator grant) | A built release's `install.sh` (prerequisites, download through a local release server, checksum, tampered-download refusal), its LaunchAgent and HTTPS health, `alt doctor`, update detection by the installed daemon (app notice, `alt` notice, doctor), `alt update`, the app's Update button as a detached launchd job, failed-update rollback and uninstall retention, on this Mac under a throwaway home | A fresh Mac or another account, login/logout, reboot, browser/device CA trust, the download from GitHub itself, the 12-hour schedule and once-a-day notice timing, provider compatibility | In use on macOS Apple silicon |
| Native macOS | Owned by the macOS runtime work ([roadmap](ROADMAP.md#native-macos-runtime)) | macOS service lifecycle, confinement and Safari | Other macOS versions or architectures | Not established; the [macOS VM run](#macos-vm-run)'s `jobs` phase runs native launchd jobs in a guest, its runtime phases drive an installed service with a fictional engine and its `login` phase drives Safari in a guest; no automated lane runs browsers on this Mac itself |
| Phone browsers | See [device evidence](#device-evidence) | Per class | Per class | Emulated WebKit in use; iOS Simulator on a Mac through `make ui-simulator`; physical checks by arrangement |

A real Claude owner in a container on this machine starts without a sign-in step of its own: after
`scripts/container.py start`, the owner runs `scripts/container.py sign-in claude --name NAME` under the
[standing container approval](../AGENTS.md#container-operations-on-this-machine), which covers that copy of
the sign-in Altitude already uses here into a throwaway test container ([first use](CONTAINERS.md#first-use)). Codex still signs in inside each fresh
container, because its login renews itself and is never copied.

The container gate removes its verified private containers, volumes and images before asking Podman
to retire that store's pause process with `system migrate`. It never applies this operation to a
shared store. Cleanup failure retains the runtime directory and fails the gate; the helper does not
identify pause processes by executable name, which differs between Podman installations.

Container admission regressions run with
`python3 -m unittest tests.test_container_admission tests.test_installed_runtime tests.test_resume_priority`.
They cover receipt/instance recovery, pause/launch races, lease release after process exit, caller
refusals and retained task Stop/holds using real storage/Git and fixture engines. `container.pw.ts`
walks the read-only recovery notice through admitted, replacement-paused, continued and unavailable
identity states using fictional API/storage. Neither suite establishes native image or deployment
browser acceptance; rerun the image gate on the exact candidate under authorized runtime access.
Its `--lifecycle` option adds finite native job/descendant, daemon restart, same-container restart
and retained-volume replacement checks. It uses fictional data and never invokes an engine.
The `--workflow` lane uses a deterministic CLI at the engine seam and real platform jobs,
project registration, coordinator conversation, Git guards/worktrees and task records. It checks
Stop, queued steering, replacement admission and continuation of the same saved session and draft.
Its synchronous application driver owns a separate fictional state directory inside the persistent
home; the image daemon serves readiness with its default fresh state. This lane does not establish
daemon scheduling, browser onboarding, interrupted-launch recovery or real provider compatibility.
The native image profile probe also runs `alt task status` for a fictional held task and edits a
workspace file with bundled Python under the generated task profile. It verifies protected image
identity and denied writes to its files and ancestors, alongside the existing denied-root/socket
matrix. Identity unit cases cover namespace ownership, invalid markers and native authority refusal;
launch/resume fixtures cover the container editing instruction. These checks make no provider calls.

Run `python3 -m unittest tests.test_container_workflow` before the authorized image gate: it runs
the same workflow and CLI with a local-process platform adapter, without host services. Workspace
results do not establish native systemd or image support; those require the gate's retained results.
The `--recovery` lane injects resume claims through the real claim API, exits their owning process,
then replaces the container. A prelaunch claim restores its message before explicit continuation;
an uncertain launch records a recovery fault without replay. Workspace tests simulate the lost
process lifetime; the native lane reads actual lifetime evidence. Neither simulates a real provider
having acted before the interruption, nor establishes automatic recovery of a running task.

## Device evidence

Device results name their evidence class; a result in one class never stands in for another.

| Class | Runs | Establishes | Does not establish |
| --- | --- | --- | --- |
| Chromium phone/desktop | `make check` (required) | Application behavior, layouts and interaction states on both viewports | Any Safari or iOS behavior |
| Emulated iPhone WebKit | `make ui-ios` (opt-in) | The same walkthroughs in Playwright's WebKit engine with iPhone metrics, touch and user agent | iOS Safari, Home Screen mode, real microphone/speech, icon selection or certificate trust |
| iOS Simulator on a Mac | `make ui-simulator` (opt-in, [iOS Simulator runs](#ios-simulator-runs)) | iOS Safari's rendering, layout, viewport and WebKit APIs (such as `webkitSpeechRecognition`) in a scripted phone-layout journey with fixture engines; iOS accepting a CA and server certificate from Altitude's generator over HTTPS without a warning once the CA is trusted, for a loopback address; Add to Home Screen at the root and a nested address: the sheet's title, the stored icon against the approved one pixel for pixel, the start address, the app opening on its own, paired with the cookies iOS copies from Safari, and the voice journey in it; the setup page's profile through Download, Allow, Profile Downloaded, its name and SHA-256, Install and Certificate Trust Settings, then HTTPS without a warning; on the recorded Xcode, iOS runtime and iPhone model | A finger's touch, pairing a Home Screen app added before Safari was paired, real audio capture or dictation, a passcode during Install, a private-network address, the operator's own CA and network, other iOS versions; physical-iPhone acceptance |
| Physical iPhone | Operator observation; [voice troubleshooting](OPERATIONS.md#on-iphone) reports | Native capture, trust, installed icon, Home Screen lifecycle | Other devices or OS versions |

Run the emulated iPhone lane for changes to phone-facing behavior such as voice, pairing, Home
Screen metadata or phone layout. It is outside `make check` and PR gates. Task owners use the
prepared validation image, which supplies Chromium, WebKit and both sets of system libraries;
downloading WebKit in a worker does not install its Linux libraries. Commit the candidate first:

```sh
alt task validate -- sh -c '(cd web && pnpm install --frozen-lockfile && pnpm build) && make ui-ios; result=$?; cp -r web/ui-artifacts/ios /results/ios; exit "$result"'
```

`web/playwright.ios.config.ts` runs one project named `phone`, so specs keep their phone layout and
`@phone-only` walkthroughs, with WebKit's mock microphone granted. Results and the HTML report stay
under ignored `web/ui-artifacts/ios/`; record the WebKit version from the report with any result.
Tests tagged `@chromium` need a harness capability this WebKit build lacks and run only in the
required projects: the `Notification` API, the
`clipboard-write` permission, a CDP session (manifest parsing, touch-drag swipes, transfer sizes and cache hits), or a replaceable
`navigator.mediaDevices.getUserMedia`. Give a new walkthrough that tag only for one of these
reasons. Where only one step needs Chromium, the step checks `browserName` and the rest of the test
still runs: route smoke omits its wheel overscroll, which mobile WebKit does not support, and brand
metadata omits Chromium's manifest parser.

Two observed engine differences matter when reading voice results: this WebKit build has no
`SpeechRecognition`, and an `AudioContext` created after an awaited microphone
request starts `suspended` in WebKit but `running` in Chromium. Both are engine observations, not
iOS results.

### Pending device verification

Each Altitude installation runs from its own machine and records; GitHub issues are what they share.
Native behavior that only the other installation can confirm stays on the change's own issue as a
checklist, labeled for the installation that owes it:

| Label | Owed by | Covers |
| --- | --- | --- |
| `verify:macos` | The Mac installation | launchd services and jobs, Seatbelt confinement, installation and updates on macOS, the container deployment on a Mac, a physical iPhone |
| `verify:linux` | The Linux installation | systemd services, Linux confinement, installation and updates on Linux, the Linux container deployment |

Browser and UI behavior relies on the required CI (Chromium on both viewports) and the
[emulated iPhone lane](#device-evidence), so it gets no label. The owner's PR references the issue
without a closing keyword and lists each check: what to run, the evidence that counts and what stays
unproven until then. Its coordinator comments the checklist on the issue, filing one when the change
has none, and adds the label:

```sh
alt issue label 225 --add verify:macos
```

The owing installation's coordinator lists its open items when the operator asks:

```sh
gh issue list --label verify:macos --state open
```

On the operator's go it starts a task for them and comments the task's evidence on the issue. Once no
check remains it removes the label (`alt issue label 225 --remove verify:macos`) and closes the issue
as completed when nothing else is open in it; an umbrella such as #225 keeps its remaining scope. A
failed check stays open on the issue with its evidence until a fix is verified.

### Voice runtime replacement check

`scripts/voice_replacement.py` runs host voice through a runtime update with the real speech worker and
model, on either host, through the validation runner:

```sh
alt task validate -- python3.12 scripts/voice_replacement.py
```

It downloads the candidate's pinned runtime and an earlier one, the same pins with onnxruntime 1.22.0,
from their public sources (about 720 MB), checking each file's SHA-256, and
serves them from a loopback fixture that can fail a download or hold it open. On Linux the earlier
runtime is the one the release supports. On a Mac, where no earlier runtime has shipped, it is a real
earlier wheel that only the script's own processes admit to the support set. The release's setup code
installs it, and `speech.Host` drives the real worker, as the daemon does:

- an automatic replacement at startup with a recording started while it downloads, and the page's
  Retry with a recording already open: host voice stays selected and ready, the recording keeps its
  final answer and replays it, the earlier runtime is deleted only after its worker exits, only the
  replacement's wheels are downloaded while the checked model files are reused, and no staging remains;
- a failed automatic replacement and a cancelled Retry: the earlier runtime keeps serving and staging
  is removed;
- after each, a fresh process starting voice as the daemon does starts no update, downloads nothing
  and records on the runtime it should, read from the worker's mapped and open files.

`voice-replacement.json` lists the checks, digests, transfer sizes, host and limits, and
`voice-replacement.log` the run; nothing else stays. Recordings are synthetic silence, which shows
the runtime works, not microphone capture or word accuracy, and no installed service restarts.
`tests/test_voice_replacement.py` runs the same check against fixture runtimes in `make check` and
holds the earlier runtime to the support set, so a pin change also updates it.

## Coverage and limits

Review evidence by user journey and failure mode. Full suites are required; a line-coverage number
or a test that only restates mocked calls cannot establish confidence. Add regressions that fail
when the observable behavior breaks. Keep the expected result independent of the implementation.
The periodic [mutation run](#mutation-run) shows where the core modules' tests would miss wrong code.

| Journey | Programmatic evidence | Boundary / remaining limit |
| --- | --- | --- |
| Private archive lifecycle | `test_installation.py`: real archives/checksums, immutable versions, install/update/uninstall retention, failed/interrupted recovery, conflicting ownership, stopped service and unsafe archive refusal. | Native service calls use deterministic fixtures; [manual installation acceptance](#installation-lifecycle-acceptance) exercises native service operations separately. An unrun workflow establishes no host evidence. |
| Connected browser onboarding and update recovery | `onboarding.pw.ts`, `pairing.pw.ts`, `trust.pw.ts`, `setup.pw.ts`, `update-recovery.pw.ts`: real application storage/API, host trust checks, optional preferences, observed readiness, failed automatic receipt → cache-only manual repair → explicit browser retry → succeeded fixture activation. `test_address_configuration.py` injects loss after each persisted/native boundary and checks ownership, rollback, stopped state, retained CA and strict cache recovery. `test_hosting.py` binds real TLS/PID health to native fixture identity and separates listener/URL scope. | Native service/download effects remain fixtures. Manual recovery and explicit retry do not establish an unattended native automatic upgrade. Record the automatic flag, terminal receipt, new release health and replacement native PID, with preserved CA/pairing/projects, separately on the change's acceptance issue. |
| Installed task inputs | `test_installed_runtime.py`: no-checkout configuration, real Git guards, dispatch/resume ownership and hold retention, missing-guard refusal, installed/source separation. | Engine execution is deterministic; no live provider or native Mac confinement evidence. |
| Local HTTPS | `test_tls.py`, `test_https_server.py`: real OpenSSL identities and TLS handshakes, hostname/trust failure, permissions, renewal, key mismatch and unchanged external certificates. | Local CA validation does not prove OS/browser trust. Physical desktop/mobile browser and home-screen-app trust need separate evidence. |
| Source TLS preparation | `test_source_tls.py`: real TLS identity and isolated files, check-only, owned override/reload verification, reset command-history metadata, actual identity/command drift, private failure diagnostics and recovery; `test_release_archive.py` verifies the standalone archive path before installation. Worker command tests preserve role denial. | Native unit/process evidence is simulated; the operator must verify actual source preservation before activation. No production services or trust stores are changed by tests. |
| Task delivery | `tests/test_offline_journeys.py`, `test_lifecycle.py`, `test_direct_l2_completion.py`: dispatch/worktree, report and archive; no-code completion refuses unlanded code or active workers. | Worker execution and GitHub responses are scripted; actual provider/GitHub permissions are unproven. |
| Trusted launch inputs | `test_launch_source.py`, `test_git_policy_integration.py`: committed installation export, activated helper paths, dirty staged/working/untracked deployment preservation, fresh base, owned resume and independent deployment failures. | Real Git and deterministic engine fixtures establish application behavior; live provider loading and host confinement remain separate evidence. |
| Repository rules | `test_repository_instructions.py`: fresh/resumed L2 and L3 turns on both engines, the owner's one-line resume text, the persona's browser verification rules, scratch cwd, shared import target, AGENTS-only and legacy CLAUDE-only projects, rule-source migration and boundary excerpts. | Fixtures establish emitted paths and file resolution, not live provider native loading or adherence. |
| Orientation | `test_doc_links.py`: relative links and anchors resolve, the required reads (`AGENTS.md`, `README.md`, `docs/OVERVIEW.md`) each stay under one tool read, and the overview links every documentation section. | GitHub's anchor rules are approximated for the headings in use; the size limit is a byte estimate of the smallest engine's tool-output cap. |
| Messaging/resume | `test_task_chat.py`, `test_chat_queue.py`, `test_resume_hold.py`: durable inbox, same session, late messages, concurrent wakes and recovery claims. Browser task lifecycle checks API persistence and terminal states. | No real provider turn or process recovery is launched. |
| Planned work | `planned-tasks.pw.ts`: phone/desktop waiting, saved brief updates, explicit release under capacity, archived-done dependency release and real Git dispatch; send denial and Work loading/error/empty states. | Worker I/O is deterministic; named HTTP overlays establish read presentation states only. |
| Operator image input | `test_images.py`, `test_image_chat.py`, `test_image_delivery.py`: real raster conversion/storage/HTTP, admission retry, caption ordering, checkpoint/resume restoration, handoff and archive/isolation. `image-input.pw.ts` walks selection/removal, voice/paste, loading, refusals, uncertain retry, sent viewer and missing/denied content on phone/desktop. | Native payloads and readable canonical bytes use engine fixtures; physical phone picker, clipboard permissions and live account/model image compatibility are unverified. |
| CI recovery | `test_ci_recheck.py`, `test_ci_recheck_delivery.py`: due probes, question-blocked observation-only waits, single rerun intent, uncertain writes, fresh artifacts, finite reads/delivery, queue/turn crashes, stale lifecycle and preserved controls on both engine seams. | GitHub responses and engine calls are fixtures; real quota recovery needs fresh uploaded artifact evidence. |
| Adversarial review | `test_review_engines.py`, `test_reviews.py`, `test_review_interfaces.py`: the captured-input adapter over real stdio (list/read/search, long-line continuation, refused private paths, read marker), the first JSON object accepted from a prose-wrapped answer and no object refused, both engines' confinement flags, unit lifecycle without a duration cutoff, cancellation, unknown termination, and a reviewer that read nothing failing instead of completing with no coverage. Startup/nonzero/capture-overflow fixtures retain bounded sanitized stderr and exit status. Structured stdout-only failures with empty stderr retain fixed error categories in owner-readable receipts across explicit retries for both engines; an unrecognized Claude result retains its allowlisted failure facts there. Unknown, malformed, incomplete and truncated output stays explicit; private prose/source and unknown values are omitted. Fixtures preserve original task ownership through cancellation/retry and keep successful reviews intact. | Fixture processes stand in for the CLIs; whether the installed Codex exposes MCP tools through its code-mode host, and whether the installed Claude connects the adapter without safe mode while loading no hooks, plugins or instructions, is established by recorded reviewer runs, not by the suite. Failure diagnostics do not establish provider compatibility, historical crash cause or recovery; live-provider testing remains deferred. |
| Improvement review | `test_improvement_review.py`: every signal predicate on fictional records, the window and its limits, the 64 KiB prompt with 3 KiB sheets and counted overflow, operator-only chat pairs, citations limited to places in the prompt, untrusted answers (unknown places, bad targets and signals, paths and credentials, too many suggestions), the reviewer's flags and token limit, isolation failing closed, refusals before launch, timeouts, usage limits and unusable answers; the offline eval (every signal on exactly its planted places and nothing ordinary, reaching its task's sheet beside the operator's quotes; every scenario consistent, bounded and reached by its patterns); a full sheet keeping the operator's words in whole lines; one-to-one matching. `test_improvement_runs.py`: the run lifecycle and its lines, stop, post receipts, typed answers, the card's view and presses through the HTTP server (replayed and refused requests, the task turn, opening and Try again, lapse), the overview's suggestions and Past suggestions, and a chat read reaching back to an older review's line. `Improvement.test.tsx` and `improvement-suggestions.pw.ts` walk the card, endings, running line, Details, Make a task, Dismiss and Undo, failed presses, Try again, Stop and Needs you's Suggestions, and `improvement-suggestions.pw.ts` Past suggestions with Latest, on phone and desktop. `test_weekly_review.py`: the agreement and its terms version, Weekly, the Monday claim, a week caught up or skipped and why, a change of terms pausing it, Built in and yours from each record shape, the terms out of date when paused before a change, never switched or turned off by it, every status line, Run now reason and Recent runs row, the Agents view and Work's count, and the page and switch and run presses through the server, admitted only from a paired device. `WeeklyReview.test.tsx` and `weekly-review.pw.ts` walk Built in and the sheet with Cancel, Escape and Turn on, the page's loading and failed read, Run now, Starting…, running with Stop only, Stopping…, Pause and Resume, every refusal, the unavailable reason, a skipped week, changed terms and the chat's terms line on phone and desktop, and `weekly-review.pw.ts` every kind of run with the totals and Latest, and an older run opening its line in chat past the conversation the chat loads at first. | The reviewer CLI is scripted; whether a model finds planted friction is the [eval](#improvement-review-eval), run by hand on the plan. |
| Routing/failures | `test_route.py`, `test_direct_dispatch.py`, `test_temporary_capacity.py`, composed journeys: pins, availability, fallback, backoff and failure without duplicate completion. | Deterministic availability/quota input does not prove real authentication, entitlement or current provider compatibility. |
| Landing | `test_land.py`, `test_land_contention.py`, `test_git_policy_integration.py`: real Git/bare origins, concurrent owner processes (merge-line order, at most two checks per candidate, held waiters, stale heads, takeover fencing, timeouts, terminated holders), ordinary and assigned histories without trailer repair, selected index content, unrelated working edits, task/PR ownership, adopted ancestry, holds, failed/skipped/absent checks, changing base/head, candidate validation and cleanup. | Fake GitHub cannot establish remote API/permission/check-association compatibility. Owners review outgoing history and diffs for scope and privacy; tests do not establish semantic content screening. |
| Merge approval | `test_recorded_merge_approval.py`, `test_merge_approval_journey.py`: original task/project/UI authority, intervening discussion, Git integration and scoped follow-up delivery; invalid sources, renewed holds and wrong PR identity refuse. | Scope, revocation and conditions are explicit coordinator judgments in fixtures; these tests do not establish live model interpretation. |
| Isolation | `test_isolation.py`, `test_codex_door.py`, `test_l3_privilege.py`, `test_service_lifecycle.py`, `test_worker_git_folders.py`: isolated storage, ownership, denied paths, read-only task Git settings and records, layout-derived Git folders, source deployment through Altitude's repository copy, and simulated restart/adoption. | Test guards prevent accidental external effects; they are not a security sandbox for hostile test code. Actual OS confinement/service restart is separate host evidence. |
| API/UI streaming and projects | `project-isolation.pw.ts`, `project-lifecycle.pw.ts`: concurrent streams in both completion orders, accepted/refused errors and retry, queue/history, cross-project ownership and reattachment/session retention. | External engine output is deterministic. |
| Phone/desktop states | `smoke.pw.ts`, task/navigation, work/decision, conversation, monitor, usage and restart specs: empty/loading/failed/denied/pending/terminal states, scrolling/navigation and visible removals. | Some states use explicit HTTP overlays. Chromium phone emulation and the opt-in [emulated iPhone WebKit lane](#device-evidence) are not physical Safari/microphone validation; restart banner assertions do not restart a service. |

Source-deployment startup exports committed installation HEAD under ignored `.altitude-source/<sha>` in the
deployment checkout, outside worker writable roots. Task helpers use the activated source;
exports remain available to existing workers. Changes to code, personas, hooks, templates, schemas
and scripts require normal activation. Tests use disposable repositories and state for this path;
ordinary dispatch and resume never clean, stash or reset deployment edits.
Archive installations instead pin their immutable version's resources. Build archives through
[the release builder](RELEASING.md#build-the-release-files); the deterministic suite never installs into
the operator's home, modifies OS trust or runs user services. The separate manual installation harness
uses a disposable account and real user service. A phone viewport is not physical phone TLS acceptance.

Detached-project reads currently return HTTP 500 with an unknown-project error while the UI
shows “Project not managed.” The removal scenario asserts those exact responses and permits
only their matching server tracebacks after detach; all other unexpected HTTP/background
exceptions fail fixture teardown. Improving that existing API status mapping is outside this
testing change, and is not claimed as fixed by its passing browser checks.

The operator's 2026-09-08 decision defers live-provider cases and the earlier real tiny-task
requirement after dispatch/engine/landing changes. Do not launch a model-using validation task,
add a live CI job, or create follow-up backlog for that deferred work. The current acceptance
policy uses comprehensive deterministic module/integration tests and core browser flows; it does
not claim those fixtures prove live provider compatibility. Record any additional uncovered
behavior in the task report. The native sandbox probe in `test_l3_privilege.py` is an existing
explicit host-capability check with no model request; it is outside default test applicability.

The operator approved the live part of the [improvement review eval](#improvement-review-eval) on
2026-10-12 as a by-hand run on the plan within a stated usage ceiling; it is not part of CI.

The operator separately authorizes one [live Linux container run](CONTAINERS.md#live-linux-run)
on 2026-10-06. Its real sign-in, onboarding and single-task evidence do not change the fixture-based
CI policy or authorize repeated provider runs.

Image integration fixtures require the detected local `ffmpeg` converter (`ffmpeg` on Debian/Ubuntu).
Color tests use the detected system `liblcms2` library (`liblcms2-2` on Debian/Ubuntu) for bounded
color conversion; production reports unavailable profile conversion explicitly when it is absent.
Inputs are limited to ordinary static raster images. Synthetic fixtures cover each color description
(RGB and gray ICC, `cICP`, `sRGB`, `gAMA`/`cHRM` and their precedence) and the unconvertible ones that
keep decoded pixels. A 12-megapixel Display P3 photo converts under the real process limits. Decoder
wall/CPU/memory/output bounds, orientation, alpha/color parity and intermediate cleanup have real
conversion fixtures. The service
and Python harnesses replace image capability checks and native execution at the engine seam.

## Mutation run

`make mutation RESULTS=dir` measures how well the fast unit tests guard the code where a silent
regression costs most: task records (`altitude/state.py`), the functions that move a task between
states in `altitude/tasks.py`, Git policy, the task door and landing. It copies the working tree,
without ignored files, to a scratch folder, installs mutmut from the hash-pinned
`scripts/mutation-requirements.txt` into a scratch virtual environment, and makes one small wrong
edit at a time: a flipped comparison, a dropped call, a changed constant, string or return value.
For each edit it runs the selected tests that reach the edited function. The module and test lists
sit at the top of `scripts/mutation.py`. The run needs `python3 -m venv`, `ps` and the package index.
On macOS the [macOS VM run](#macos-vm-run)'s `mutation` phase shows interrupted runs cleaning up.
The tests' temporary folders sit inside its scratch folder, which it removes at the end with any
process the tests left behind. They run with `no_proxy=*`: mutmut runs them in a forked process,
where macOS aborts the proxy lookup urllib otherwise makes. On a 24-CPU Linux machine the first
full run took about seven hours, nearly five of them for `altitude/land.py`, whose every surviving
edit reruns some two hundred Git-backed tests, so start a full run overnight; task records and
transitions alone take about 75 minutes, and the task door 51 minutes.
It is a measurement, not a check: `make check` and the required CI never run it, and its counts gate
nothing.

`RESULTS/report.md` has one row per module, then each surviving edit's diff, headed by its mutant's
name, grouped by function; `RESULTS/mutmut.log` keeps mutmut's own output.

- **Killed**: a test failed with the edit in place, so the tests guard that code.
- **Survived**: every selected test that reaches the function still passed.
- **No tests**: no selected test calls the function in the test process. mutmut sees calls only
  there, and child processes a test starts (`bin/alt`, Git hooks) run the original code, so a
  function reached only through them counts here too.
- **Timeout**: the edit made the tests run far longer than usual, usually a loop that no longer
  ends; read it as killed.
- **Other**: mutmut did not settle the edit (not checked, suspicious, crashed); the report names
  each one. A nonzero count means that module's counts are incomplete.

Survivors are relative to the listed test files: a test elsewhere in the suite may already catch
one. Before calling a survivor a gap, apply its diff by hand and run `make check-python`. Then
sort it:

- **Real gap**: the edit changes observable behavior (a refused transition allowed, a check skipped,
  a wrong state, path or exit status recorded) and nothing notices. Add a regression test that fails
  on that edit through the module's behavior, not its implementation, and rerun to see it killed.
- **Equivalent**: the edit cannot change behavior, such as a boundary that never occurs, a default
  every caller overrides, or a timeout constant a fixture replaces. Leave it.
- **Noise**: wording of log lines and messages. Test it only where the exact text is the behavior,
  such as a refusal the operator reads.

`MODULES="altitude/door.py"` (any of the five, space-separated) limits a rerun to those modules, to
check that new tests kill the survivors they were written for.

A survivor count is not a target; the worst real gaps become ordinary test work in their own tasks.

## Improvement review eval

The eval measures whether the improvement review finds recurring friction, on fictional projects
before any real conversation is read
([design](../design/wireframes/improvement-review/PROPOSAL.md#proving-it-works)). Scenarios are
fictional projects in `tests/fixtures/improvement/` ([format](../tests/fixtures/improvement/README.md)),
in three sets: `develop/` for writing the reviewer's instructions, including every set already run live,
`heldback/` for the reported numbers, and `scale/` for held-back projects at a real project's volume (one
planted, one control). Each command
loads the scenarios into its own throwaway Altitude home and refuses the real one.

The offline part runs without a model and is part of `make check`
(`test_improvement_review.py`):

```sh
ALTITUDE_HOME="$(mktemp -d)" python3 -m altitude.improve_eval offline
```

It prints one line per signal: places planted in `signals.json`, how many fired, how many reached
their own task's sheet under their alias, ordinary places that fired, and planted scenario patterns
whose places reach their prompt. It fails when a signal misses a planted place or fires on ordinary
work (repeated pushes, a report continued by a message, a question revised after an operator or L3
reply, a second review, a review re-run after a failed one or of new material), when a prompt or sheet is over its bound, when a long conversation's sheet
quotes no operator message, or when a scenario's key does not fit its records.

The live part calls the reviewer on the plan and runs by hand:

```sh
export ALTITUDE_HOME="$(mktemp -d)"
python3 -m altitude.improve_eval show <develop-scenario>        # the prompt a develop scenario builds
python3 -m altitude.improve_eval run heldback,scale --models sonnet --ceiling <yardstick> --out <private-dir>
```

`run` calls the reviewer for every scenario of the named sets (`--only` limits it), model
(`--models`, default all offered) and repeat (`--repeats`, default 3) and stops before the next call's
maximum could pass the ceiling, in the engine's reported-cost yardstick; an incomplete run never
passes. It writes `calls.jsonl`, `run.json` and `grading.json`: every kept suggestion, shuffled and
without its model, beside its scenario's key and the text of its cited places. A blind grader, given
only `grading.json`, writes `grades.json` by its rubric. Then a fresh `ALTITUDE_HOME`:

```sh
python3 -m altitude.improve_eval score --out <private-dir>
```

`score` writes `score.json`: per model, each run's share of counted and shared-cause patterns caught
and of words-only patterns caught (counting only patterns whose places reached the prompt), wrong
suggestions on the controls and elsewhere, invalid answers, tokens, per signal the planted patterns
caught and the suggestions naming it graded wrong, the counts-alone column and the recommendation,
the least plan use that clears the bar. Only a complete run of the held-back set, with the scale set at
most beside it, every scenario of the sets named and at least three repeats each, can pass a model; anything less reports `full_matrix`
false and no recommendation. A pattern is caught when the matcher pairs a suggestion with it by at
least two shared planted places and the grader marks it caught. The output directory is private
evidence; the PR carries the table.

## Installation lifecycle acceptance

First-browser install-link evidence connects a disposable verified archive's fresh activation,
CA-verified readiness and actual `install.sh` output to the built app on fixture HTTPS with a
trusted generated CA, real access storage and API. Native service operations use fixtures;
the journey must reach First run through one redemption and no second CLI command. Close the
page/browser context after certificate setup and reopen the original terminal link in a fresh
trusted context; reloading the cleaned address does not recover its code.

`install-pair-https.pw.ts` uses the actual result renderer from `install.sh` and the built SPA in
the verified fixture archive; bootstrap prerequisites/downloads remain covered by release tests.
Its shared `playwright.config.ts` fixture imports only the public fictional CA into a temporary Linux NSS
database using `certutil` (`libnss3-tools`), keeps the runner's shared launch options and sandbox
policy, and removes the owned browser/home at cleanup. A separate untrusted context refuses the
same certificate; verification stays enabled. This Chromium-specific trust lane is tagged
`@chromium` and establishes Linux fixture HTTPS, not OS/device trust. `install-pair.pw.ts` walks
the same presentation and real pairing API/storage at both viewports and in emulated iPhone
WebKit using named trust-response overlays on the fictional loopback service.

Coverage includes no link before successful readiness; failed issuance with a running installation;
fresh activation retaining a manual code or paired devices; access-lock concurrency with manual
issuance/redemption; reruns, updates and recovery retaining access; single-use, expired, malformed
and denied links; lost replies and access rechecks; fragment cleanup before router and access reads;
no secret request outside the pair JSON body; reload/Back; remote untrusted/unknown/trusted states,
external-certificate Continue, local exemption and plain remote HTTP refusal. Expiry and issuance
failure exercise the displayed `~/.local/bin/alt pair` recovery with that directory absent from PATH.
Walk loading, unreachable/Retry, trust instructions and checks, Pairing…, success/already-paired,
every refusal and manual recovery on phone and desktop, plus `make ui-ios`. Pairing has no listening
state. Retain state captures in ignored review artifacts and report runs and gaps explicitly.

Fixture and browser evidence does not establish native installation, OS URL handlers, physical-phone
trust or provider compatibility. Native Linux/macOS first-browser installation checks stay on
[#926](https://github.com/mburakyucel/altitude/issues/926), with `verify:linux` and `verify:macos`;
the upgrade prevention and acceptance scope on [#912](https://github.com/mburakyucel/altitude/issues/912)
remains separate. Candidate acceptance requires a recorded run and its results.

`.github/workflows/installation-lifecycle.yml` is an independent, manually dispatched Ubuntu 24.04
x86_64 workflow. It is outside `make check`, PR triggers and release gates; its failure or pending
state does not hold other tasks. The required PR `check` and all review requirements
remain unchanged. Run it from **main**, selecting the source to package separately:

```sh
gh workflow run installation-lifecycle.yml --ref main -f source_ref=<commit-or-ref>
gh run list --workflow installation-lifecycle.yml
gh run view <run-id> --log
gh run download <run-id> --dir /tmp/altitude-installation-evidence
```

`source_ref` defaults to `main` and resolves to an exact commit before building. The dispatch ref
owns GitHub's check association; checking out `source_ref` does not move that association. Never
dispatch directly on an open task branch: `alt land` rejects a `workflow_dispatch` check on its
candidate even if that job is optional, skipped or successful. Dispatching on main keeps the run
outside the task candidate. The early main-ref check reports misuse but cannot detach that check;
after an accidental task-ref dispatch, push a new reviewed commit before landing.
`tests/test_installation_ci.py` exercises real Git and landing with
separate fixture GitHub run inventory and PR checks: failed, running and queued installation runs
on main leave a successful required PR check mergeable; an absent or unsuccessful required check
still blocks. This fixture does not establish live GitHub association behavior.
Task status selects push-triggered main runs; `tests/test_task_status.py` covers ignoring a failed
manual run on the same merged commit. Release publication selects its existing required workflow.

The workflow builds actual archives with `scripts/build_release.py`, using synthetic versions
`v0.0.0-rc.1` and `v0.0.0-rc.2` from the same source commit. These exercise version switching,
not migration between released source revisions. The harness installs into a new disposable user's
fresh home, starts its real per-user service, and checks HTTPS using the generated CA on a dynamic
loopback port. It verifies health version/commit, native service PID and served web asset hashes,
runs the packaged `alt doctor`, updates, and activates a deliberately failing `v0.0.0-rc.3`
derivative whose startup exits. The derivative's manifest and archive checksums are recomputed,
so the recovery case reaches real failed activation rather than stopping at checksum rejection.
The injected entry point records its daemon invocation before exiting, so recovery proof does not
depend on user-journal permissions. Recovery restores the healthy candidate. Uninstall retains
configuration, TLS identity and fictional history/project files. Doctor distinguishes expected
unauthenticated GitHub findings and configured engine executables with unknown access from
installation failures. Engine authentication/execution is not accepted by this test; any background
engine probes reach only the fixture. No live provider request or operator state is used.
The harness comes from the workflow's main commit; the archive builder and application come from
`source_ref`. Results name both commits and the source tree. Older incompatible installer interfaces
can fail this current harness. Installation calls the archive's standalone `install.py`; the `bootstrap` phase below, which the
local VM run uses, exercises the `install.sh` download, checksum and Python-discovery path.

The job is bounded to 20 minutes and the lifecycle invocation to eight minutes. Cleanup traps retain
allowlisted diagnostics and remove the throwaway account. Results identify source commit, synthetic
versions, archive hashes and runner environment, with command/service diagnostics and failure output.
The workflow retains evidence for seven days; TLS private keys and runtime archives are excluded.
Inspect the failed stage and diagnostics before rerunning. Missing results, timeout, or an unavailable
user manager are unverified acceptance, not a passing lifecycle. A nonzero cleanup exit also fails
the job even when the lifecycle assertions passed. The workflow has not executed: its first
dispatch was refused before runner startup by the account's hosted-runner spending limit, and
`release.yml` uses the same hosted runner type. The [local VM run](#local-vm-run) executes the same
harness without GitHub runners. Native lifecycle acceptance for a candidate needs a recorded run and
its results; source tests alone do not establish it.

On a disposable Ubuntu 24.04 VM with Python 3.12+, Git, OpenSSL, GitHub CLI and a working systemd
user manager, use the same entry point with two release-builder output directories. Each contains
`install.py`, its application archive and `.sha256` file. Both manifests must match the supplied
full source commit. Use absolute paths readable by the harness:

```sh
sudo bash scripts/test_installation_lifecycle.sh --disposable-vm \
  /absolute/baseline /absolute/candidate /absolute/results "$SOURCE_COMMIT"
```

This command creates and removes a local account and its service; use only a disposable VM, never
an operator machine or deployment. Every phase but `reboot-install` turns lingering on for its new
account before it starts, so that account needs no login. A trailing `reboot-install` phase instead
logs in to a new account over SSH, as on a host reached only that way, and installs the candidate
there, which must turn lingering on. The harness closes that login, waits until the account has no
session and logind's stop delay has passed, and checks that the same user manager still serves the
same healthy service; after the VM restarts, `reboot-verify` checks that the user manager started the
installation with nobody logged in, then uninstalls it and removes the account. Each
phase writes to its own subdirectory of the results. The `bootstrap` phase runs the built
`install.sh` through its public `curl … | sh` command in a new account. For the test, the release's
host name resolves to the guest's loopback, where the account serves the release over HTTPS with a
throwaway certificate authority that only this test's `curl` trusts. A download altered by one byte
must be refused with nothing installed; the unaltered one must install a healthy service, which is
then uninstalled. The `update` phase installs the baseline while the account's server answers for
GitHub's release lookup and release downloads: the release host and its `api.` host resolve to the
guest's loopback, and the account's user manager gives the service and its update job the throwaway
authority as `SSL_CERT_FILE`. A release-candidate baseline reads a listing that initially names the
baseline and an ignored draft; a stable baseline reads GitHub's latest release. The server first
publishes the baseline's next patch or release candidate, a copy of the candidate whose startup exits. The
baseline's daemon must try it automatically, record the attempt failed and automatic, and restore itself to a
healthy baseline with fictional data, settings and TLS identity unchanged; two more forced release checks must
keep that same failed attempt and download nothing. An outside process then imports from the installed
baseline with `-I` and without `-B`, writing Python caches into it, and the server publishes the candidate.
The baseline's daemon must install it automatically with no browser terminal open,
record success and serve the candidate's healthy version with a new PID, leaving those caches in place. Only a
published baseline from v0.8.0 or older, whose own check refuses them with its earlier error, gets none, which
its results record as a limit; any other refusal fails the run. The harness then turns
automatic updates off and publishes a newer synthetic release; a paired app **Update** request
installs it explicitly. Each update, the failed one included, downloads only its archive and checksum, once. The baseline must have
automatic updates (`v0.1.2` or later). The hosts
entries and unprivileged-port setting these phases need are restored afterwards. The `public-install`
and `public-update` phases need a guest that reaches GitHub and two published releases, the candidate
being the one `releases/latest` names. `public-install` runs setup's command for `releases/latest`
after checking that the `install.sh` it serves is the candidate's. `public-update` installs the
baseline with the same command for its tag, waits for its daemon's own release lookup to offer the
candidate in `alt doctor` and the terminal's notice, runs `alt update` and must keep settings, TLS
identity and data; an update whose startup fails must then restore the candidate.
The `trust` phase runs the built `install.sh` through its public command like `bootstrap`, but in a
terminal of the account's own, after Firefox's first start has made its profile. The terminal answers
yes to the installer's offer to trust Altitude's certificate, which must report trusting Chromium's
database (created, since Chromium reads it but never creates it) and the Firefox profile's. The
installer's private link must then open First run in Chromium and a second link from `alt pair` in
Firefox, while both browsers with fresh stores refuse the certificate. Its browsers are Playwright's
builds, so it does not establish distribution or snap browsers. Retain its results before discarding the VM. The hosted workflow
runs none of these phases and does not prove a minimal OS install or login/logout behavior, device CA trust,
a download from GitHub's published release, native confinement or provider compatibility. The [macOS installation lane](#macos-installation-lane) covers the Mac; this Linux
evidence is partial acceptance toward #226 and does not close it or establish public readiness.

### Local VM run

`scripts/installation_vm.py` runs the same harness on a Linux x86_64 host with KVM, at no cost and
without GitHub runners. It needs `qemu-system-x86`, `qemu-utils` and `cloud-image-utils` (installed
once by the machine's administrator) and read/write access to `/dev/kvm`. One command builds both
synthetic versions, `v0.0.0-rc.1` and `v0.1.0-rc.1`, from a committed revision (default: HEAD, which refuses uncommitted edits to tracked
files), runs every phase
and leaves the evidence in the results directory. The release builds and the published-release
downloads take the GitHub repository from the checkout's `origin` remote, which the validation
runner's copy carries; a non-GitHub origin stops the run:

```sh
make installation-vm RESULTS=/tmp/altitude-vm SOURCE=origin/main
```

`BASELINE=<tag>` makes a published release the baseline: the runner downloads every asset the
release lists anonymously, checks each against the release's `SHA256SUMS` and the archive's declared
version and commit against the tag on GitHub, and builds only the candidate from `SOURCE`,
under the next minor version on the baseline's release channel, so the update is never a downgrade
and the baseline follows it. Every phase then installs the published files, `install.sh` included,
and updates from them to the candidate; in the `update` phase the published release's own daemon
tries the failing release, restores itself and then installs the candidate automatically. The guest stays
offline, so the published files run exactly, but its own anonymous download from GitHub does not:

```sh
make installation-vm RESULTS=/tmp/altitude-vm SOURCE=origin/main BASELINE=v0.1.2
```

`RECOVERY=1` with a published `BASELINE` runs only the `recovery` phase: the published release's
installation must fail and leave an interrupted activation that the candidate's installer refuses.
The phase then runs the cleanup the [`v0.1.0-rc.2` notes](../CHANGELOG.md#v010-rc2--2026-09-29) give for that machine
(disable and remove the unit, reload systemd, `alt recover`), and the candidate's installer run over
what is left must start the service and keep its settings, TLS identity and fictional data, then
uninstall. `v0.1.0-rc.1` is such a
baseline, since systemd refuses the unit it writes:

```sh
make installation-vm RESULTS=/tmp/altitude-vm SOURCE=origin/main BASELINE=v0.1.0-rc.1 RECOVERY=1
```

`PUBLIC=1` with a published `BASELINE` builds nothing and runs only the `public-install` and
`public-update` phases: the candidate is the stable release GitHub's `releases/latest` redirects to
(a release candidate there stops the run), downloaded and checked like the baseline. The guest keeps
its online card. Instead of unplugging it, the runner adds guest routes that refuse the card's
gateway (this host's loopback), this host's own network address, and private, shared and link-local
networks; the card's own subnet stays reachable for its gateway and name server. Before the phases
the internet must answer and neither of the host's listeners may. Each phase's own downloads and the
installed daemon's release lookup then go to GitHub:

```sh
make installation-vm RESULTS=/tmp/altitude-vm BASELINE=v0.1.0-rc.2 PUBLIC=1
```

`TRUST=1` builds both versions from `SOURCE` and runs only the `trust` phase. While the guest is still
online, the runner downloads the `playwright-core` package `web/pnpm-lock.yaml` pins, checks it against
the lock's integrity, and copies it into the guest with this host's Node.js. The guest then installs
Playwright's Chromium and Firefox with their system libraries, NSS's `certutil`, and a `chromium`
command on its PATH, as an installed Chromium has. The online card is then unplugged as usual, and
`guest-browsers.log` keeps the installation's output:

```sh
make installation-vm RESULTS=/tmp/altitude-vm SOURCE=origin/main TRUST=1
```

The runner downloads the current Ubuntu 24.04 cloud image, checks its signed checksum with the
installed Ubuntu cloud-image keyring and caches it under `~/.cache/altitude-installation-vm`. Each
run boots a copy-on-write overlay with 2 CPUs, 4 GiB of memory and a 12 GiB disk, logs in with a
per-run SSH key over a loopback-only port, and deletes the overlay, key and seed afterwards, also
when the run is stopped. The guest has two network cards on separate QEMU user networks. One is
online only while cloud-init installs Git, GitHub CLI and OpenSSL, and is then unplugged. The other
is restricted to the SSH forward. The online card has no IPv6. Before the harness starts, the runner
probes the internet and listeners it opens on the host's loopback and on its network address (the one
its default route leaves from). Through the online card all three must answer and through the
restricted card the host must not; after unplugging, nothing may answer. Any other outcome, or a
probe that cannot run, stops the run. After the lifecycle passes, the runner runs `bootstrap`, `update` and `reboot-install`, restarts the VM, checks that it is still isolated and runs `reboot-verify`. Results hold the harness evidence and build logs plus `vm.json` (source
commit, published baseline release with its commit and checksums when used, whether it was a recovery or public run, with
`PUBLIC` the release `latest` named with its commit and checksums and the networks the guest refuses, harness commit and whether its scripts were modified, image and signature, QEMU version, guest OS and kernel, probe outcomes, each phase's exit) and the VM console
and QEMU logs; `harness.log` and the `harness-*.log` files are written as the phases run. The runner prints each stage with its
elapsed time; after the first image download, a run takes about three and a half minutes, two of them
while the restarted guest waits for its unplugged card. Inside a task, `make installation-vm` runs this
through the [validation runner](#validation-runner) with KVM. The committed `SOURCE` (default `HEAD`)
is built inside the container, results go to the task folder's `validation/<n>/`, and the cloud image
is cached in `~/.altitude-validation/cache`. The runner never touches
the host's Altitude service, trust stores or network configuration. `CAPTURE=1` adds an accelerated replay
of the run as `captures/installation-vm.gif` ([validation captures](#validation-captures)).

`make sandbox-vm` (`scripts/sandbox_vm.py`) boots the same kind of guest to run the sandbox's socket cases
(`tests/test_session_sockets.py`) with real bubblewrap. While online, the guest installs bubblewrap, Ubuntu's own
AppArmor profile that admits it to user namespaces as on an Ubuntu desktop, the hardware-enablement kernel, which
offers Landlock's abstract-socket scope, and Git and the GitHub CLI. It then restarts on that kernel, loses its online card and runs the module from
the committed `SOURCE`. `sandbox-vm.json` records the guest and kernel, the bubblewrap and Landlock versions and the
module's summary and every skip reason. Every case runs there except the Seatbelt-only ones, which skip as `macOS only`;
a case skipped for any other reason fails the run, as does a run where no case ran. Inside a task it runs through the
validation runner with KVM, like `make installation-vm`:

```sh
make sandbox-vm RESULTS=/tmp/altitude-sandbox-vm
```

### macOS installation lane

`scripts/installation_mac.py` runs the lifecycle on a Mac with macOS 15 or newer,
Homebrew's Python 3.12 and OpenSSL 3 installed, Git, GitHub CLI and `pnpm`. The clean environment
keeps stock LibreSSL first on PATH so installation, startup and updates exercise OpenSSL discovery:

```sh
make installation-mac RESULTS=/tmp/altitude-mac SOURCE=origin/main
```

It builds `v0.0.1` from the committed `SOURCE` (default `HEAD`) and runs the harness's `mac`
phase as the running account under a throwaway `HOME` in the user temporary folder, with a clean
environment. An installation under any `HOME` other than the account's own gets a LaunchAgent label
derived from that home (`platform.service_label()`), so the lane's service and jobs never address
the account's `dev.altitude.altd`. The service listens on a free loopback port. The phase serves
ordinary stable releases, as users receive them, from a local HTTPS proxy that answers only for `github.com` and `api.github.com`, with a
throwaway certificate authority; the installing shell's `HTTPS_PROXY` and `SSL_CERT_FILE` point at
it and the installation keeps them, so no request reaches GitHub. It then:

- runs the built `install.sh` through its public `curl … | sh` command: a download altered by one
  byte is refused with nothing installed, and the unaltered one installs the LaunchAgent and a
  service whose HTTPS health on the generated CA reports the release's version and commit, with
  `alt doctor` passing apart from its expected findings;
- pairs a fixture device, then gives the stopped service a leaf certificate one day from expiry,
  issued by the release under the retained CA and server key; with stock LibreSSL still first on the
  saved service PATH, the service's own startup renews it with the OpenSSL 3 it discovers, keeping
  the CA, key and pairing;
- binds a finite throwaway coordinator socket before address transactions; the disposable Mac VM
  home stays in the launcher's guest-owned shared tree so macOS's Unix socket pathname limit is met;
- records one fictional project without task dispatch; previews and
  applies IPv4/IPv6 loopback changes while running and stopped, verifies actual HTTPS/native PID,
  later-start loading and preserved CA/keys/pairing/registration; process loss after the durable
  settings write leaves a receipt that `alt recover` restores;
- publishes `v0.0.2`; the daemon installs it automatically with no browser terminal open, and
  the overview reports its successful automatic installation;
- turns automatic updates off, publishes `v0.0.3`, checks its overview, terminal notice and
  doctor offer while the same daemon keeps running `v0.0.2` with no new attempt, and installs it
  with `alt update`;
- publishes `v0.0.4`; a paired device's Update button starts the detached
  `dev.altitude.job.altitude-update-…` launchd job, which activates it and is removed;
- turns automatic updates on and publishes `v0.0.5`, whose startup exits; automatic activation
  restores `v0.0.4`. Two subsequent checks preserve the failed attempt and make no further download;
- imports from the installed release in an outside process run with `-I` and without `-B`, which
  writes Python caches into it, and publishes healthy `v0.0.6`; the daemon installs it automatically
  with a replacement PID and `automatic=true`, keeping settings, TLS identity, pairing and the project
  registration, and leaves the outgoing release's caches where they were;
- uninstalls: the LaunchAgent, its launchd job and the update job are gone, settings, TLS identity
  and fictional history and project files remain.

The lane makes the next check due by rewriting `update.json` under its lock instead of waiting 12
hours, and removes the once-a-day notice marker between versions. Afterwards, also after a failure, a
timeout or a stop, the runner stops every process the phase started, boots out what remains of the lane's own labels, copies the evidence into
`RESULTS` and deletes the throwaway home. Results hold `build.log`, `lifecycle.log`, the harness's
`result.json` and `mac.json` (source and harness commits, macOS version and chip, the account
service's state before and after, each step's outcome and the cleanup). A lock refuses a second run
while one is active.

launchd refuses service control from a sandboxed process, so the lane runs outside the worker
sandbox: in a terminal, or in a task through `alt task run` under an
[operator grant](CLI.md#operator-grant) whose bounds are this command. launchd keeps the
enable/disable record of the lane's one label, which only an administrator can clear. The lane does
not establish a fresh Mac or another account, login/logout, reboot, browser or device CA trust, the
download from GitHub itself or the real check and notice schedule.

### macOS VM run

`scripts/installation_macos_vm.py` runs the built `install.sh` in fresh macOS guests on an Apple
silicon Mac through Apple's Virtualization framework, at no cost and without root. It needs Xcode's
command line tools, which compile and locally sign its Swift helper `scripts/macos_vm.swift` with the
virtualization entitlement, Python 3.12, `pnpm` with the web packages already in its store (the
release build runs offline) and GitHub CLI, which it copies into the guests; the `source` phase also copies Homebrew's
Node with the kegs it links, the pnpm version `web/package.json` pins from Corepack's cache and the pnpm store. One command builds the guests once and runs every phase against a committed
revision (default: HEAD):

```sh
make installation-macos-vm RESULTS=/tmp/altitude-macos-vm SOURCE=origin/main
```

`installation_macos_vm.py image` builds two guests from Apple's restore image for the newest macOS
this Mac supports, recording its version, build, address and SHA-256. `fresh` is a new installation:
the runner writes its one administrator account, automatic login and a relay from the
Virtualization framework's host-guest socket to sshd onto the stopped guest's disk, skipping Setup
Assistant, then a first login gives those files root's ownership and installs a per-image SSH key.
`prerequisites` is a copy-on-write clone to which the guest itself, online, adds Apple's command
line tools, Homebrew and Homebrew's `python@3.12` and `openssl@3`. The restore image is deleted once
`fresh` is built. `image --step` does only the next step; each step finishes within ten minutes.

`installation_macos_vm.py run RESULTS [--phase PHASE]` builds `v0.0.1` from the committed revision
and runs each phase in its own copy-on-write clone with 4 CPUs, 4 GiB of memory and a 64 GiB sparse
disk, reached over SSH through the host-guest socket only. Before the installer runs, the runner
probes the internet (`www.apple.com`) and a listener it opens on the guest network's gateway on this
Mac: both must answer, and after it unplugs the guest's only network card neither may. A probe that
neither reaches its destination nor finds it unreachable, for example one that cannot run or fails
TLS, stops the run, and the probes repeat at the end. The release is served
on the guest's loopback over HTTPS with a throwaway certificate authority that the public command's
shell trusts, with `github.com` resolving to the loopback.

- `fresh`: the public command must stop for the missing Python.
- `prerequisites`: with only the OpenSSL CLI temporarily hidden in the disposable guest, it must
  stop for missing OpenSSL 3 and name `brew install openssl@3` without asking for a profile change.
  After restoring that CLI, a second account with the same shell setup and no desktop session must be stopped for the missing
  session. Each refusal must name its documented fix and leave the size and modification time of
  every file outside `Library` and in the `Library` folders an installation writes, and the
  account's launchd jobs, unchanged.
- `lifecycle`: with Homebrew's OpenSSL installed, stock LibreSSL first on PATH and GitHub CLI in place, the guest's account runs the
  [macOS installation lane](#macos-installation-lane)'s `mac` phase under a throwaway HOME, as
  `installation_mac.py` does: install, the daemon's automatic installation of a newer release with
  no browser terminal open and the overview reporting it, update detection and notice with automatic
  updates off and the release waiting for confirmation, `alt update`, the Update button, the service's
  renewal of a near-expiry certificate with the OpenSSL it discovers, recovery from
  an automatic update whose startup exits and uninstall. When it fails, launchd's record of the job, the guest's
  busiest processes, a sample of the service's process and its open files are kept.
- `login`: the account installs through the public command into its own home. The printed private
  link must name the printed address; at that origin, on the generated authority, its page loads
  and the page's own pairing request with the link's code pairs the browser once, while a second
  use and a well-formed unknown code are told the code expired or was already used. Running the
  public command again must print no link and keep the certificate authority, server key and
  certificate and the paired browser. Its LaunchAgent
  `dev.altitude.altd` must run from `~/Library/LaunchAgents`, answer HTTPS health with the release's
  version and commit on the generated authority and pass `alt doctor`; `alt --version` must print the
  release. Doctor's next step must follow real state: the printed link before pairing, then the first
  missing tool, signing in to the GitHub CLI, with no agent sandbox check and one coding-agent
  requirement naming every agent's install command, as the app's prerequisites list also shows.
  After the guest restarts with
  its card still unplugged, the automatic login must start the service again with a new process and
  the browser stays paired. A fictional Git project added through the app must then show its
  coordinator waiting for a coding tool, with setup continuing by itself and no incident filed; once
  the browser is unpaired, doctor's next step is certificate trust and `alt pair`. From the SSH
  session, with the account's desktop logged in, `alt trust` answered `y` must name the SSH session,
  fail for the login keychain with the message pointing to the Mac's own desktop or the pair page's
  Mac steps and add no trust setting (`trust-ssh`); macOS keeps the certificate itself in the
  keychain, untrusted. That certificate must then be removed by `security delete-certificate -Z
  <SHA-256> -t`, the form of the undo command `alt trust` prints (`undo-sha256`). Then the account's
  desktop is used as a person uses it (`trust-desktop`): the runner clicks and types on the guest's
  screen and keyboard through `scripts/macos_vm.swift`, and opens commands in Terminal there. Safari,
  driven through its own `safaridriver` in the desktop session, must first show the address as not
  private. `alt trust` in Terminal, answered `y`, must raise macOS's password window; clicked and given
  the automatic login's password, which the runner reads from the guest and removes from the evidence,
  it must leave the authority as the one trust setting, trusted for SSL only. Safari must then open
  Altitude's page at the printed link's address, whose pair screen's own check reads Trusted
  (`pair-safari`). The printed link with a fresh code from `alt pair` must pair Safari as one new
  device and leave no code in its address, and Back must return to the page before the link. In new
  windows the installer's own link, used earlier, must read expired or already used and a malformed
  one not valid, and another fresh link opened by macOS (`open`) must pair its browser too. Then
  `alt service configure --host 100.64.0.10 --apply` must move the service to an address in the shared
  VPN range, which the runner assigns to the guest's loopback in place of a VPN's interface, verified,
  with the server certificate reissued under the same authority (`address-vpn`). There Safari must
  trust it, pair with a fresh code as one new device and show the address on Settings › Network as a
  selected private address; moving back to `127.0.0.1` the same way must keep every device. The
  printed undo, run in Terminal and answered in the password window, must leave the trust settings as
  before; over SSH macOS refuses it, since removing a trust setting needs that window. Uninstall must
  then remove the LaunchAgent and stop the service.
- `privacy`: the account, logged in and outside any sandbox, runs the revision's own
  `scripts/platform_probe.py --only confinement validation-confinement` with Homebrew's Python 3.12,
  which nothing in the guest has ever asked for access to a private place. Both rows must pass, their
  details stating that the Desktop and mounted volumes are refused with no consent request in the
  privacy log. A Mac whose Python already holds an answer cannot show that the profile, not the
  stored answer, refused.
- `jobs`: the account, logged in and outside any sandbox, runs the revision's own probe job rows
  (`job-pipe`, `job-file-output`, `exit-cleanup`, `stop-descendants`, `time-limit`,
  `limit-after-owner-exit`, `sleep-hold`, `awake-clock`, `logged-job`, `detached-job`, `launch-input` and
  `job-temporary`)
  as real launchd jobs in its own domain, which launchd refuses a worker sandbox. Every row must pass:
  Stop ends each descendant, including one that leaves by `setsid`, double fork or a cleared environment
  or keeps forking, before the job reads inactive, and the time limit holds also after its owner exits.
  While a job runs, `pmset -g assertions` lists an idle-sleep assertion made on behalf of its supervisor,
  gone once the job ends. `awake-clock` is supporting evidence, not a job across sleep: the clock the
  supervisor's limit waits on, `Popen.wait`'s monotonic clock, is macOS's awake-time clock, which stops
  while the Mac sleeps, as systemd's limit does. The guest itself cannot sleep (macOS under the Virtualization framework
  refuses `pmset sleepnow`), so `awake-clock`, which starts no job, also runs on this Mac through
  `alt task validate -- python3 scripts/platform_probe.py --only awake-clock`: on a Mac that has slept
  since it started, the limit's clock has left out the hours slept, which the row reports. A job across
  an actual sleep is `real-sleep`, which runs only when named, on this Mac itself, after the operator
  schedules a wake as root: `sudo pmset schedule wake "$(date -v+150S '+%m/%d/%Y %H:%M:%S')" &&
  python3 scripts/platform_probe.py --only real-sleep`. It refuses to start unless `pmset -g sched`
  lists a wake within five minutes (`pmset relative wake` is not listed there). A job with a
  60-second limit starts, `pmset sleepnow` sleeps the Mac until the wake, and the Mac must have slept
  at least 30 seconds and the job's whole tree must stop at its limit counted in awake time. Plugging
  in a cable during the sleep wakes the Mac early, and the row then fails as not slept. The
  `launch-input` job, confined and launched as a review is, takes its own folder and a fictional
  sign-in from its input's first line, reads but cannot write a home settings file linked into that
  folder, and cannot list the launcher's job folders or read another job's saved launch input there.
  The confined `job-temporary` job reaches a socket in its own `TMPDIR` and is refused, with EPERM, a
  fixture socket another process keeps in `/private/tmp`; its supervisor has removed that `TMPDIR` by
  the time the job's command returns.
- `read-only-home`: the account, logged in and outside any sandbox, runs the revision's own
  read-only-home suite cases (`tests.test_worker_writes`, `tests.test_worker_roots`, the read-only
  landing case, `tests.test_session_sockets.TestOperatorsClaudeFolder` and the Codex coordinator's
  real-sandbox cases in `tests.test_l3_privilege`) with Homebrew's Python 3.12. Every case must run and
  pass, none skipped: each confines a child with a Seatbelt profile that keeps Altitude's home
  read-only, the roots case with the roots each engine's worker launch names; only the scratch folder
  and the worker's own engine folder may be written there. The launcher's job folders, and a job's
  input saved there, can be neither read nor written, and neither engine's worker lists a browser's
  profile folder, by the home or through `/System/Volumes/Data`. The operator's Claude and Codex
  folders and Claude state file, under a home whose name has a dash, a dot and a space, give EPERM to
  every read, write and listing apart from the entries a job's own folder links to, also when the
  Claude folder is a link to another folder and for a state-file copy made after the job started. A
  Codex coordinator runs under Codex's own sandbox, so the runner copies this Mac's Codex CLI into the
  guest with an empty settings folder and no sign-in, and the opt-in `ALTITUDE_TEST_CODEX_SANDBOX=1`
  cases run that sandbox with a coordinator's permissions, asking no model: it refuses the operator's
  Claude folder and state file, a fixture socket in `/private/tmp` and a browser's profile folder by
  both names. The suite's home lies outside the temporary folders the profiles admit, as in a
  validation run. A Mac without the Codex CLI stops the phase.
- `mutation`: the [mutation run](#mutation-run) needs `ps`, which no sandbox here allows. The runner
  downloads its hash-pinned packages on this Mac, and the account installs them from those files in the
  offline guest, in the revision's files made a Git checkout. `tests/macos_guest_mutation.py` starts
  two runs of `altitude/door.py` one after the other and interrupts each with SIGINT once mutmut runs.
  Before each interruption it plants a process outside mutmut whose command names the run's scratch
  folder, as a test's leftover does. After each run, its scratch folder, mutmut and the planted process
  must be gone, with nothing in `ps`'s listing naming that folder, and the second run must use a new
  folder. A whole run is not made in the guest: it measures the same platform-neutral tests as on
  Linux, and the one macOS-only failure a whole run found is the abort described under
  [mutation run](#mutation-run).
- `source`: the account sets up a [source checkout](OPERATIONS.md#run-from-a-source-checkout) as
  documented, with this Mac's Node and pnpm in place: it clones the revision from an origin in the
  guest, made from a bundle of its history, runs `make web` and `alt tls-init`, and `make
  install-service` must start `dev.altitude.altd` from `~/Library/LaunchAgents`, answering HTTPS
  health as a source install. Altitude's copy of its repository, `~/.altitude-repository`, must not
  exist before that first start and afterwards hold the revision, fetched from the same origin, with
  the service running its export under `.altitude-source/`. The checkout is then registered as the
  project `altitude` and a newer main, a commit adding a file under `scripts/` and a mark to the
  web app's page, is pushed to the origin. Within seven minutes the service must have fast-forwarded
  the checkout through the copy, built the web app and restarted into the newer main: a new process,
  with the checkout, the copy and the export its startup activated all at the new commit, serving
  the page with the mark the first start did not serve. The service's logs are kept.

The runtime phases `workers`, `grants`, `incident`, `update`, `terminal`, `session`, `agents`, `repairs` and `app-repairs` install the release into the account's own home through
the public command, with `tests/macos_guest_engine.py`, a scripted fictional Codex CLI, as its only engine, signed in with a fictional token, and the
tests' fake GitHub CLI first on its saved PATH, and register a fictional project. `tests/macos_guest_runtime.py` then
drives each row as the account against the installed service: the operator acts through `alt`, the coordinator
through a turn the service runs for the operator's chat message, sent to `/api/chat` with the CLI's machine key as
the page sends it (`alt chat` runs its turn outside the service, whose coordinator socket serves only its own turn),
and a worker through the `FIXTURE-RUN:` lines of its brief or message, which the fictional engine runs inside the
worker's own job and sandbox. No provider is reached. `update`, `terminal`, `agents`, `repairs` and `app-repairs` first run `alt trust` on the
account's desktop through macOS's password window, as the installer offers it, since Safari's WebDriver cannot
accept a certificate Safari does not trust; their rows then read Altitude's page in a Safari window paired with a
fresh `alt pair` code.

- `workers`: the coordinator stops a running task, which leaves it with the coordinator and no operator question
  (`coordinator-stop`, #779); a worker whose turn failed while a background command lingers is ended with its job
  and that command (`failed-turn`, #782); a worker whose job name cannot be read after a restart reads status
  unknown with a worker-status fault while the task keeps its worker (`status-unknown`, #678); and a validation
  run interrupted by the service's restart is recorded as interrupted with its evidence kept (`validation-restart`,
  #650).
- `grants`: revoking a running granted command stops its launchd job, also across the service's restart, and the
  coordinator applies one task's answer to another task's identical purpose (`grants`, #656, #668); with the
  guest's one machine-run place, a second granted command waits for the first (`contention`, #728). Then, with the
  tests' fictional `claude setup-token` screen as the Claude CLI, Settings' sign-in started through
  `/api/sign-in/claude` must show the sign-in link and, given the code, save the token at mode 0600 with this
  sign-in's save time in Altitude's access folder, where the setup command ran with its own settings folder and
  no token; the Claude row of Settings › Prerequisites reads "Claude signed in". The token appears in no answer
  and no service log, and no `claude.setup.*` folder, setup command or process the command started remains
  after that sign-in, after one cancelled part-way, or after one interrupted by the service's restart (`sign-in`,
  #1025).
- `incident`: on a release behind a newer one, a new incident is held without calling GitHub and publishing it
  later creates the issue; on the latest release it is created directly, both naming the installed version
  (`incident`, #767). The release check reads `api.github.com` on the guest's loopback. Then the guest's own
  clock moves forward, with nothing in Altitude's records rewritten: two minutes before the next lookup is due
  the daemon has not looked, just after it finds the newer release, 12 hours after its last lookup (a failed
  startup lookup is first retried an hour later); a terminal shows the newer release's notice once, not again
  that day, and again the next day (`schedule`, #226, #731). The row runs last, since the clock stays moved.
- `update`: the guest's installed release, relabelled one patch version newer and otherwise unchanged, is offered
  for automatic installation while a task's validation run holds the machine. `alt doctor` must read `Update:
  Altitude <newer> installs automatically at the next quiet moment; waiting for the validation run of
  fictional/<task>.`, Safari's update notice `Altitude <newer> installs automatically at the next quiet moment.
  Waiting for the validation run of fictional/<task>.` and Settings' version row `<installed> · <newer> waits for
  the validation run of fictional/<task>`; another task's `alt task validate` is told Altitude restarts soon to
  apply an update and runs only after the restart. Within 45 seconds of the run's end the update starts; the
  newer archive is served as a named pipe that holds its download until Safari's notice reads `Installing Altitude
  <newer>… Altitude restarts when it is ready.`; meanwhile a new task stays queued and the operator's chat message
  is queued without a coordinator turn. Released, the update succeeds and the service restarts into the
  newer version while the validating task's worker stays in its turn, Safari's notice reads `Updated to
  <newer>.`, and the queued task and chat message start (`quiet-point`, #1003).
- `terminal`: the coordinator hands the operator a fictional command naming a blocked task: it must wait in
  Needs you (`/api/overview`) as that task's turn, and Safari must render it: Needs you's `1 step in
  fictional` with the command's card, its Open in terminal and `This leaves Needs you once the command
  finishes in the project terminal.`, the task's Work row `Your turn · <the step's first line>` and its page
  `Your turn · a command from L3`. Open in terminal, clicked in Safari, opens the project terminal with the
  command typed at the prompt, and Enter, typed there, runs it to its end; its notice must clear the step, so
  Needs you reads `Nothing needs you.`, start a coordinator turn and leave the task no longer the operator's
  turn on its Work row. Each state is kept as a screenshot. A second step's terminal, closed before its
  command ends, must leave that step waiting (`operator-step`, #1017). The project terminal, open in Safari
  and hidden behind another tab for 90 seconds while its shell writes, must take up the same shell once shown
  again, each hidden line on screen once and no Reconnecting left, and its prompt must take typing
  (`terminal-return`, #1071). Then, with the project terminal open, a newer release is offered for automatic installation: `alt doctor`, Safari's notice and Settings' version
  row must name `the browser terminal open in fictional`, and nothing starts or restarts for over a minute while
  it stays open; within 45 seconds of its closing the update starts, succeeds and the service restarts into the
  newer version (`terminal-hold`, #1003).
- `session`: a worker saves a draft from its scratch folder, shows a worktree file and completes; once the
  task is done its scratch folder must be gone with its progress kept, and a command it left running must write
  nothing afterwards, in Altitude's home or a recreated scratch folder (`scratch`, #958). With a second project
  registered, a coordinator turn of the fictional Codex CLI must run from a fresh folder in the coordinator folder,
  which only the account may open, be admitted to its own project's channel and refused at the other project's,
  while a task worker's connection to its own project's is refused by Altitude's sandbox; each channel's path stays within macOS's 103 bytes
  (`coordinator-channel`, #943). Then ending the login
  session without a restart stops the service and the automatic login starts it again
  (`logout-login`, #731); a login that does not return is recorded as unavailable with its reason. Sleep is
  the `jobs` phase's `sleep-hold` and `awake-clock` rows, and `real-sleep` on the Mac itself.
- `agents`: a POST from Safari on the account's desktop, outside Altitude's jobs, is admitted, while the same
  paired cookie sent by a command in a worker's job is refused with 403 and `Altitude's own agents cannot act
  with a paired device's sign-in.`; that worker lists a fictional Firefox profile folder neither through the
  home nor through `/System/Volumes/Data` (`agent-refusal`, #1053). A Codex worker in Altitude's Seatbelt,
  with Codex's own sandbox off, must sign in with the fictional access token and account Altitude hands it
  over the app server's protocol and run `git status` in its worktree, with the refresh token in neither its
  environment nor its own Codex folder and the operator's sign-in file refused. With the operator's token
  then past half its life, the worker's own request for a fresh one goes through the task door: altd has the
  fictional CLI refresh the operator's sign-in outside every sandbox and hands the worker the new token
  (`codex-sign-in`, #1058). A real provider's refresh and token lifetime need a real sign-in.
- `repairs`: in a repair, the fictional CLI's first turn also follows the `FIXTURE-RUN:` lines of the problem
  its brief quotes. `alt repair` in the account's own shell, outside Altitude's jobs, must start the CLI in the
  terminal with the brief's prompt as its one argument, the brief holding Altitude's records; once its report
  is recorded and it ends, the service's check sets Repaired or Confirm, which Settings' About shows on the
  repair's row from a terminal, with its page (`repair-terminal`, #1052). `alt repair` typed in the project's
  page terminal, a launchd job of its own, must be refused with `Only you can start a repair, from your own
  terminal.`, also under `env -i`, where only the job's coalition names it, and start nothing
  (`repair-page-terminal`). `python3.12 install.py --repair`, with the release's own installer, must start a
  repair of the installed version (`repair-installer`).
- `app-repairs`: a repair started from Safari's page must run the CLI headless with full access in a launchd
  job of its own, resume its session by id for a message, and keep its job, the recorder and a background
  command of the agent's running through a restart of the service and a reinstall (`alt update --archive`
  of a relabelled copy). Stop must end every process in the job, and Left running, on the page Safari
  renders, must name the launch agent the agent started outside it (`repair-app`, #1065). A repair whose
  deadline has passed ends with its job's processes (`repair-deadline`), and one running at logout ends with
  the session, recorded as interrupted, after which `alt repair` starts again (`repair-logout`). The real
  CLIs' headless full access and resume need a real sign-in.
- `online`, only when named with `--phase online`: by the operator's decision of 2026-10-19, the one
  phase whose guest keeps its network, for the checks that need the internet. The probes must find the
  internet and this Mac reachable at the start. The guest itself downloads Homebrew's `python@3.13`, and Google Chrome and Firefox
  from their publishers, each of which must pass `codesign --verify --deep` with its publisher's
  team and Gatekeeper's `spctl --assess`. The latest published release then installs through the public command from GitHub
  itself, with the guest's own certificate store, and its service must run on Python 3.13, read from
  the Python framework its process loaded. On the account's desktop, Chrome (headless, `--dump-dom`) and
  Firefox (headless, through its own Marionette protocol), each with a new profile, must refuse
  Altitude's address for its certificate before `alt trust` and open Altitude's page after it, once
  `alt trust` is answered `y` and macOS's password window is given the account's password (#1006, #225,
  #226). The clone is deleted afterwards like every other.

`PUBLISHED=<baseline>..<candidate>` names two published release tags and runs only the `published`
phase. `PUBLISHED=<baseline>` alone names only the published baseline: the candidate is then built
from `SOURCE` under the baseline's next minor version, as the [Linux VM run's](#local-vm-run)
`BASELINE` does, so the published release's own update code can be shown before a later release
exists. Its isolated environment puts Homebrew OpenSSL first for baselines whose shipped installer
requires that order; it does not establish discovery with LibreSSL first:

```sh
make installation-macos-vm RESULTS=/tmp/altitude-macos-vm PUBLISHED=v0.3.0..v0.4.0
make installation-macos-vm RESULTS=/tmp/altitude-macos-vm PUBLISHED=v0.9.0 SOURCE=origin/main
```

The runner downloads each published release's assets anonymously on this Mac, as the Linux VM run's
`BASELINE` does: each must match the release's `SHA256SUMS`, and its archive must declare the version
and commit the tag names. The files reach the offline guest over the host-guest socket. Under a
throwaway HOME, as in `lifecycle`, a server on the guest's loopback answers GitHub's release lookup
and downloads with the baseline's and candidate's files. The baseline installs through its own public
command; a device pairs and the operator's name is saved through the app's own requests, beside
fictional data, and the baseline's own code adds a fictional project with a planned task, which waits
and never runs, and an operator message on it. An outside process then imports from the installed baseline with `-I` and without
`-B`, writing Python caches into it. The baseline's own daemon must then find the candidate, install
it automatically at a quiet point, leave those caches where they were and download only its archive
and checksum. Only a baseline from v0.8.0 or older, whose own check refuses the caches with its
earlier error, updates without them, which its results record as a limit. The candidate must answer
health with its own version and commit and pass `alt doctor`, recognize the same paired device, show
the saved name, read the project's registration and the task's title, request, state, wait and conversation
exactly as the baseline did, and keep configuration, TLS identity and data unchanged before uninstall. A baseline
from before v0.7.0 must also have its machine key replaced (#947); a later one keeps it. Fictional
files of an earlier voice runtime planted in `~/.altitude/speech` must be gone once the candidate has
started (#940). Before uninstall, the task is rejected and the project removed through the app, since
uninstall keeps the application while a project is registered.

`RESULTS/macos-vm.json` records the source and harness commits, this Mac's macOS version, free disk
and load average before and after, the guest's sizing, each image's restore image, macOS build,
prerequisites and disk footprint, and per phase the probe outcomes, every attempt's and step's exit
and result, the guest's load average, the disk the clone took and the run time; `privacy` and `jobs` add the probe's rows and
the guest's Python, `source` its two commits and the Node kegs it added, `online` the published release, the
service's Python framework and each browser's version, publisher team and download size, the runtime phases each
row's result with the fixture's turns, the service's logs and the task records kept beside it, and a `PUBLISHED` run each published release's tag, commit and checksums and a built candidate's version and commit. Each step's full output, the
listing each refusal is compared against, the lifecycle's own results and the guest helper's log stay beside it. Everything the runner writes
lives in `~/.cache/altitude-installation-vm/macos`: the helper, the release build and its temporary files, the images (about 26 GiB for macOS
26.6.2) and per-run clones (about 1 GiB at most), deleted after each phase, also after a failure or a
stop. One guest runs at a time, and the runner stops whenever less than 10 GiB of disk would stay
free. Building the images takes about fifteen minutes after the restore image's download. On an
unloaded Mac `fresh` and `prerequisites` take under half a minute each, `lifecycle` about five
minutes, `login` just over three, `privacy` under one, `mutation` about one and a half, `workers` about five and a half, `grants` about four,
`update` about seven, `terminal` about five and a half, `incident`, `session` and `source` about two, `published` about one and a half and `online` about four; the release build adds about one. `installation_macos_vm.py clean` deletes everything in its folder.

Virtual machines cannot start inside a task's sandbox or a [macOS validation
run](#macos-validation-runs). Inside a task, an owner runs `installation_macos_vm.py` with
`alt task run` under an [operator grant](CLI.md#operator-grant): `image --step` while images are
missing, under a grant naming that build, then `run`, under one L3 records from the
[standing VM lane approval](../AGENTS.md#vm-lane-runs-on-this-mac) on request, with `--phase` splitting the phases across commands when the
Mac is busy so each finishes within the command limit, keeping `RESULTS` in the task folder. The runner never touches the host's Altitude
service, LaunchAgents, keychains, trust stores or network configuration.

## CI and candidate identity

Text parsing keeps complete fictional output and span fixtures in `tests/fixtures/text-parity.json`.
`tests/test_text_parsing.py` checks option and recommendation forms, malformed delimiters, whitespace,
duplicate keys and precedence, failure-summary outputs and JSON-prefix spans, plus shared serial
recognition, redaction and publication rejection.
Its long-input probes run the actual parsers in separate Python children with a two-second deadline
per child. Parser changes preserve the accepted language and full input; they do not truncate text
or relax publication checks.

`.github/workflows/hosted-checks.yml` runs `make check` for every pull request to main, every push
to main and manual dispatches as parallel `shard` jobs, each on its own GitHub-hosted
`ubuntu-24.04` runner: `make check-python SHARD=i/4` and `make check-web SHARD=i/6`, with
Python using all of its runner's CPUs. Each shard verifies the exact candidate, then installs
`ffmpeg` and `liblcms2-2` for the image fixtures and the frozen web dependencies and Chromium from
`web/`, where Corepack selects the pinned pnpm. The workflow lists each suite's slices `1/n`
through `n/n` and the runners split their own sorted test lists, so every test runs exactly once;
`tests/test_ci_workflow.py` checks that the matrix covers every `make check` branch and
`tests/test_parallel_checks.py` that Python slices partition the suite. Every shard finishes even
when another fails. The required job `check` waits for all of them, always runs, and passes only
when every shard succeeded: a failed, cancelled or skipped shard fails it. Typecheck/build runs
in every web shard because each one's browser walkthroughs need the build.
A push to main first runs the read-only `reuse` job (`scripts/ci_reuse.py`, #777). Merging an
up-to-date PR pushes the tree its PR run just tested in full, so when exactly one merged PR from a
branch of this repository has the pushed commit as its merge commit, every PR that branch opened
targets main, the pushed tree equals that PR's head tree, the push changes none of the files that
decide how CI runs (`.github/`, `Makefile`, `tests/run_parallel.py`, `web/package.json`, the web
lockfile, the TypeScript, Playwright and Vite configs, the timing and reuse scripts), and the newest `pull_request` run of this workflow file for that head and branch passed every
shard and its `check` job in its latest attempt, the shards are skipped and `check` passes, linking
that run in its summary. Any miss, lookup error or incomplete listing runs every shard as on pull
requests and manual runs; shards are skipped only when the `reuse` job succeeded naming a run, and
`check` accepts skipped shards only with that evidence. The run keeps its
workflow, event, head and conclusion, and the `check` job release publication requires, so task status,
report verification and release read it as before. `tests/test_ci_reuse.py` covers each condition on
real Git with fixture API answers.
A newer push cancels the superseded run for that PR. Main and manual runs are never cancelled.
Each full check uses 11 jobs against the plan's 20 concurrent-job limit, so concurrent PR publication
does not imply many full checks can execute at once; a reused main run uses two short jobs.
Every run, whether from a fork or a repository branch, has a read-only token and no secrets, and no
workflow runs on the maintainer's machine. Standard hosted runners are free for public
repositories. GitHub's fork-workflow approval setting requires approval for all outside
contributors, so every outside contributor's run starts only once the maintainer approves it.
The PR `check` is required by `alt land` in every repository whose base commit ships that workflow;
owners and helpers run relevant tests during development instead of repeating a full local suite at
every landing. Python, web unit tests, typecheck/build and both browser viewports must execute and
pass. No unrun or failed phase is green; live-provider validation stays deferred. `alt land` lands
only its own repository branches, so a fork's run is contributor feedback: after review, Altitude
integrates the contribution on a repository branch, where the same check runs on the integrated
head before the merge. `.github/workflows/release.yml` runs only for a pushed `v0.*` tag and
publishes that checked commit's release; see [publish a release](RELEASING.md#publish-a-release).

CI verifies that it tests the exact commit and, for a pull request, that GitHub's merge commit has
the event's base and head as parents, that the head includes that base and that the tested merge
tree equals the head tree. Landing requires the specific successful PR
job on the current head and verifies that current main is an ancestor of that head at merge time.
Merges go through one ordered [merge line](CLI.md#concurrent-landings) per repository, first ready,
first merged; publication, first checks and assessment run side by side. A waiting candidate keeps
its green check and is not pushed while main moves; only the head of the line integrates main, runs
one fresh check and merges, so a candidate uses at most two checks (#777). Concurrent worktrees can race to update a remote-tracking ref;
landing repeats that specific failed fetch once and requires a successful fresh fetch, sharing
dispatch's collision classification. Other fetch failures still refuse delivery.
Landing fetches only the requested branches, without tags, and reports a collision retry.
`tests/test_fetch_concurrency.py` forces this race during a real Git transfer.
`tests/test_land_contention.py` drives real competing
landing processes, Git and fixture reviewers: three ready candidates merging in order with at most
two checks each, a candidate on current main merging on its existing check, a green candidate merging
while another owner's check is pending (for more than ten minutes on a scaled landing clock), main
moving during a first check, an assessment or the head's fresh check, a held candidate staying
untouched in line, a foreign head without a heartbeat being removed, a takeover fencing the earlier
landing's push, concurrent nonmerging publications, timeout, termination, ownership loss and
material-edit refusal. Proposal followed by implementation review
reports both stale assessments together; messages during admission, hosted CI or local validation
require explicit assessment in the same landing invocation. Invalid dispositions
leave the candidate unmerged and its operator hold intact, and final validation does not restart the
assessment deadline. `tests/test_reviews.py` checks the review identity and changed evidence in refusals.
Original review receipts remain unchanged. Fixtures establish the application protocol, not live engine support for
background tool sessions or provider compatibility.
CI runs outside the command. Other installations share the line through its ref on origin. If an
outside merge moves main during the head's fresh check, the head integrates it again within its turn.
Ownership, reviews, holds, recorded approval and issue-closure
gates repeat for each candidate. Adopted PR pushes remain fast-forward only. Head or PR identity
movement refuses; no stale check or review assessment transfers. A later invocation can reuse the
successful head when main is already an
ancestor of it: the merge still has the identical tested tree. Ordinary competing merges introduce
commits outside the head and require integration, a push and fresh checks on the new head.
Landing verifies the merged tree against the tested tree; commit metadata can differ.
Missing, pending, failed, skipped, cancelled, stale or unrelated required runs block. A required run
that has not registered on the head yet keeps the bounded wait going; one still absent at the bound
reports `missing`.
GitHub-managed scans, such as CodeQL default setup, run on the candidate commit with the `dynamic`
event and no pull request. They are bound by that commit alone, never qualify as the PR `check`, and
count like every other check on the candidate: a failing scan blocks, a pending one keeps the wait
going, and a nonrequired one that ends neutral, skipped or cancelled is ignored. CodeQL counts an
alert as new in a PR when a step of its data flow lies in the changed code, so an alert already open
on main can fail a PR that never touches its line: a browser-walkthrough fixture server subclassing
`server.Handler` adds a request source. Fix real defects at their sinks and
document genuine false positives for operator dismissal without changing accepted behavior.
Every other workflow run must come from a push or pull request event of this PR's branch.
Review and task/UX holds remain enforced by Altitude. This repository's strict GitHub up-to-date
required-check rule rejects a merge if a merge made outside the line moves main between final
validation and merge; the head then integrates again within its turn. No repository move, plan upgrade or settings change
is needed. GitHub web/API merges do not enforce Altitude's task holds or review protocol.

Each CodeQL alert dismissed on GitHub records its reason. CodeQL's Python path check recognizes a
normalize-then-prefix test, a comparison with a constant and known library sanitizers as validation,
not checks inside Altitude's own functions: project and task names pass `config.require_project_name`
and `state.require_task_slug`, the `storage` layer opens one checked component at a time without
following links, and other names match a fixed pattern or a stored record. A path alert whose data
passes one of these is a false positive whose dismissal names that check; a path that reaches the
filesystem without one is a defect fixed through these helpers. No workflow restores an Actions cache:
the [installation lifecycle](#installation-lifecycle-acceptance) workflow runs candidate code in main's
cache scope.

Other projects retain their configured hosted/no-CI gates. Shared hosted-check handling ignores
completed nonrequired skips without parsing workflow conditions; failed or pending checks still
block, and at least one hosted check must actually pass. Requiredness and candidate association
must be established. Their merge line orders cooperating landings that share the origin;
protection against outside writers requires a strict GitHub up-to-date rule.
Projects without CI retain the full local candidate suite and `--test-cmd`
(one argv command, see [dry run and gate selection](CLI.md#dry-run-and-gate-selection));
neither provides an outage bypass for this repository.

A failed web shard uploads its self-contained browser HTML report, with its screenshots and failure
traces, as the seven-day artifact `browser-report-<job index>-<attempt>`; passing shards upload nothing. A passing
required check on the current head, with its GitHub console log, is sufficient delivery evidence;
`alt land` verifies the head, tree and base ancestry. Owners download a failed report into their
task folder only to diagnose a failed run or when a reviewer asks, match its run URL and attempt to
the candidate, and open it with `pnpm --dir web exec playwright show-report /path/to/report`.

An owner reruns a failed flaky shard itself on the same head (`gh run rerun --failed <run>`); a
recheck only observes. An owner keeps a bounded CI wait in its active session. If it cannot obtain
the required result, it records the run and missing evidence, explicitly blocks and asks L3 for the existing finite
[`recheck-ci`](CLI.md#durable-ci-recheck). No run means trigger recovery, not an invented run ID.
GitHub Actions outages pause delivery until verified recovery and fresh CI. A probe
does not resume the owner, settle a question or release a hold; L3 owns that reconciliation.
The existing GitHub artifact-capacity probe covers the failed-report uploads.

## Runtime evidence

Use `make check` for per-phase wall/user/system timings and retain the runner summaries with the
source SHA and tool versions. Separate dependency/browser installation from warm execution.
The serial baseline is CI run 35065148992: 13 min 54 s
on four CPUs, including Python at 4 min 15 s and 316 browser tests at 9 min 12 s with two workers.
The measured reference is CI run 35076675654 on 2026-09-16, completing the concurrent gate in 3 min 51 s at
eight process-available CPUs: Python 149.54 s (1,371 tests, one optional native probe skipped,
four processes), web tests 13.54 s (360 passed), typecheck/build 4.82 s and browsers 212.58 s
(324 passed, eight workers, zero retries). Phase wall times overlap and must not be added.
CI allocations vary with available capacity. Worker counts follow the process's available CPUs,
not a fixed container size or host-wide count. This reference uses head `998da21`; it is not
acceptance evidence for a later revision. PR evidence records the current source, allocation,
timings and complete results. A hosted `ubuntu-24.04` runner has four CPUs for a public
repository, so each shard uses four browser workers or four Python processes. Run local timing
measurements one at a time and retain scoped memory and termination observations alongside timings.

A warm local implementation run on 2026-09-08, Linux, Python 3.12.3, Node 22.22.2 and pnpm
10.34.5 measured the following; PR/check artifacts identify the validated source revision.

| Phase | Executed result | Wall time |
| --- | --- | --- |
| Python | 634 discovered: 633 passed, one optional native host probe skipped | 75.46 s |
| Web unit tests | 181 passed in 15 files | 4.81 s |
| Typecheck/build | Passed; existing large-bundle advisory | 2.97 s |
| Browser | 111 passed: 56 phone and 55 desktop; no skips | 119.39 s |
| Full warm gate | All required phases passed | 202.66 s |

The desktop project excludes the tagged phone-only header assertion from applicability. These
are measurements, not runtime deadlines or evidence that every external integration is tested.
Cold dependency/browser setup and hosted-runner timings are separate and appear in CI logs.

## UI development

For interactive development, run your own backend with temporary state on an unreserved port
and point Vite at it explicitly. Vite proxies APIs; it does not launch or isolate the backend:

```sh
ALTITUDE_DEV_API=http://127.0.0.1:18890 pnpm --dir web dev --host 127.0.0.1 --port 15173
```

Use free ports on your machine and follow the component interaction-state rule in the project
instructions. The backend refuses actions from Altitude's own agents
([Altitude's own agents](ARCHITECTURE.md#altitudes-own-agents)), so a backend and browser an agent
starts this way can read but not act; an agent's walkthroughs use the browser harness. [Operations](OPERATIONS.md) covers separately authorized service and physical-device
checks. These development URLs do not select the automated test target.
