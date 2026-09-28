# Development and checks

Start with [CONTRIBUTING](../CONTRIBUTING.md) for scope and review expectations and
[setup](SETUP.md) for the supported runtime. Python uses the standard library; the web app uses
its pnpm lockfile. Routine checks use deterministic fixtures, make no model calls, and do not
use the operator's service or runtime records.

## Local checks

Contributors work from a repository checkout; the private archive is for running the application.
With repository access, clone it, then use the commands below from its root.
Owners and helpers run tests relevant to their changes during development. The required PR
check runs the full suite; a local full run is available for investigation but is not a landing prerequisite.

Use Python 3.12+, Node 22.22.2+ (22.x) or 24.15+ (24.x), and the pnpm version pinned in
`web/package.json`. From the repository root:

```sh
pnpm --dir web install --frozen-lockfile
PLAYWRIGHT_BROWSERS_PATH="${ALTITUDE_HOME:-$HOME/.altitude}/browsers" pnpm --dir web exec playwright install chromium
make check
```

For a deliberately separate foreground preview, build with `pnpm --dir web build`, put `bin/`
on PATH, select unused runtime and TLS directories outside project/source roots, and choose a free
port with `ALTITUDE_PORT`. Run `alt tls-init`, follow the [certificate trust guide](SETUP.md#trust-https-on-each-device),
then `alt serve` with the same environment. It still needs the systemd user manager for workers.
Do not bind an existing service's reserved port or use its runtime state for a preview.

`make check` runs the full Python suite alongside the ordered web unit, TypeScript/build and
isolated browser phases. Each phase retains `/usr/bin/time -p` wall/user/system output, written
as one block so the parallel branches never interleave it; the command waits for both branches and fails if either fails. A failed web prerequisite stops its
dependent phases. Python's stdlib `tests/run_parallel.py` distributes whole test modules across
fresh interpreters, using half the available CPUs (at least one). The full gate obtains that
budget from Node's `availableParallelism()` for both languages: container CPU quotas may be
smaller than Python's affinity mask. The runner itself remains stdlib-only; standalone invocations
default to half the affinity mask, or one worker where affinity is unavailable. Each shard streams prefixed
verbose unittest output, including skip reasons; the final summary totals all shards for landing.
Test names flush before execution so incomplete runs identify each shard's last started test.
`python3 tests/run_parallel.py --workers N` selects a worker count for focused measurement.
Serial `python3 -m unittest discover -v tests` and `make test` remain available.
Dependency and browser installation are explicit prerequisites, so a warm run need not
fetch packages. In a restricted worktree add `--store-dir /tmp/altitude-ui-pnpm-store` to the
frozen install. Do not alter the lockfile to work around an installation failure. On a clean
Linux CI host, `playwright install --with-deps chromium` also installs browser OS dependencies.

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

## Browser walkthroughs

Build the candidate app before `make ui`; `make check` does this in order. Every test uses a
fictional populated project in a disposable Python service on an OS-selected loopback port.
The service serves this checkout's `web/dist` and real application HTTP handlers. No live project,
service URL or active worker is needed. Tests clean up their owned service processes and temporary
storage, with bounded termination. Never point routine acceptance at operator data.
Cleanup releases fixture gates and finishes HTTP requests and background work before closing L3
broker sockets; unfinished work or logged background failures fail the walkthrough.
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
Question-response and failed-setup checkpoints join their owned workflows before observing the
scripted outcome, so concurrent scheduling cannot inspect a half-finished resume or introduction.
The change-stream outage fixture reports its captured shutdown targets; connection cleanup cannot
erase them from the count before the response reaches the browser assertion.
The lost-queue-receipt test waits for the initial streamed reply before installing its overlay,
so the intended queued request is the only send it interrupts.
The reattachment walkthrough forces an overlapping browser overview read while registration fails.
It releases the read after the error appears and keeps its route handler until teardown; removing
the last page route also continues held requests, so their callbacks cannot continue them again.

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
context. The harness uses locked bundled Chromium in Altitude home's shared `browsers/` directory;
a missing bundle is a failed prerequisite. This historical observation does not diagnose other hosts
or establish that bundled Chromium supports its own sandbox inside a worker.

`web/e2e/fixtures.ts` starts the disposable services. `acceptance-service.py` supplies fictional
tasks/history for route and component walkthroughs. Project-isolation and project-lifecycle
services exercise real streaming/queue/history and removal/reattachment; task-lifecycle specs
exercise real message/decision effects. Fixture providers replace external calls at the engine
boundary. HTTP overlays are reserved for named loading, transport failure and presentation states;
an intercepted success response alone is not evidence of a backend transition. Request
interception stays on for each test's browser context, so an overlay that expires or is removed
while the app sends its next request cannot strand that request in Chromium; `route-overlays.pw.ts`
guards this.

Chromium supplies a synthetic microphone and its permission for browser walkthroughs;
no test requests a physical microphone. Image-input voice journeys retain real capture and
MediaRecorder while holding `AudioContext.resume()` pending: an output-device/renderer
failure cannot strand their microphone fixture in `AudioContext.resume()`. They assert the
listening phase before the recorded interval; the Stop control also exists during startup.
The cancel, transcription, image send, navigation and denied states run at both viewports.
Fixture services set the `local` voice backend so those journeys keep the upload path;
`voice-recognition.pw.ts` overlays `GET /api/voice` with `browser` and installs a page-level fake
`SpeechRecognition` it drives itself (Playwright's Chromium has no vendor recognition), walking
words while listening, landed, Send at once, cancel, failed, denied and no-recognizer states at
both viewports. Vitest uses `FakeSpeechRecognition` from `voiceTest.ts` the same way.

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
Passing tests do not replace a pending visual review or release a merge hold. The task design preview
is an option only after its delivery and ignored/untracked input handling are verified.

Wireframe renders stay in ignored `design/wireframes/shots/`. The
[documentation renderer](../design/readme/README.md) also writes to ignored artifacts; only selected
illustrations that explain current behavior belong in `docs/images/`. Maintain the spec and useful
boards in place, removing obsolete review scaffolding and its generators/references together.

### Loading and caching evidence

Caching and prefetch changes follow the [project rule](../AGENTS.md#working-rules-for-every-pr): a
reviewed proposal, then a reviewed implementation. Measure before choosing. `web/e2e/first-open.pw.ts`
is the example: it opens its own browser context, because the harness's request interception turns
off the HTTP cache, and records each request's encoded size, encoding and cache use through the
Chrome DevTools Protocol, with `Network.emulateNetworkConditions` for slow links. Wait for a landmark
and the script's finished load, never network idle: the change stream stays open. Report cold, warm
and slow opens separately, and keep simulated links distinct from the operator's real connection.

## Browser verification and recovery

Worker confinement and the browser's own sandbox are separate protections. Worker launch and the
fictional UI suite do not establish a supported path for verification requiring both. The shared
launcher tells every fresh/resumed owner to check that capability before dependent deployment
verification. This is an owner procedure, not automatic capability detection or permission to deploy.

Within the task's diagnostic authority, preflight the intended executable in the intended worker,
using blank or local fictional content, a finite timeout, and disposable writable profile, config
and cache directories. Keep the browser sandbox enabled: Playwright's
[`chromiumSandbox`](https://playwright.dev/docs/api/class-browsertype#browser-type-launch-option-chromium-sandbox)
defaults to false and must be explicitly true for this requirement. Record the browser/version,
launch options, exit/result and cleanup in private task evidence. A successful launch alone does not
prove every required browser protection; retain the isolation evidence the task requires before
claiming verification. Do not navigate to a deployment while this prerequisite is unavailable.

Issue #441 reports system Chrome rejecting its
SUID helper and read-only crash storage before navigation. The reporting Altitude version and host
permissions are unknown. A bounded current-worker check with disposable writable storage reproduces
the helper rejection; locked bundled Chromium also refuses with `No usable sandbox`. Neither emits
the reported read-only crash-storage error. This establishes unavailable launches in that worker,
not a host package defect or a universal browser limitation. No sandbox-preserving path is established.

Linux [user namespaces](https://man7.org/linux/man-pages/man7/user_namespaces.7.html) translate file
ownership through UID/GID mappings; unmapped owners can appear as overflow IDs (`nobody:nogroup`).
Namespace-visible ownership, mode 4755 and a single-ID mapping cannot establish actual host ownership.
Writable crash storage addresses a separate prerequisite and does not repair browser isolation.
Do not chmod/chown the helper, add sandbox-disabling flags or weaken worker confinement in response.

On unavailable capability, checkpoint the failed launch and remaining verification, reply, and use
`alt task block "$ALTITUDE_TASK" --fault --reason "Browser sandbox capability unavailable; L3 must establish a supported path preserving required browser and worker protections before verification resumes."`
L3 owns recovery under the [existing procedure](../personas/l3.md#recovery-and-upstream-reporting).
If host facts are necessary, request a bounded diagnostic purpose through the existing operator
question, resolution and L3 machine-grant workflow. A diagnostic grant authorizes neither host repair
nor verification outside worker confinement. Recovery requires evidence in the intended worker;
without it, retain the capability block and present the exact remaining decision. Altitude's local
fictional harness exception grants no authority for another project's verification.

## Validation environments

Each environment establishes one kind of evidence; running more of them does not widen what any one
proves. A result names its environment, entry point, tested revision, OS and architecture, and is
kept with the PR or task that ran it. An environment that was not run is a missing result, not a
pass. Only `make check` gates delivery; the others are run for the changes they cover.

| Environment | Entry point | Establishes | Does not establish | Status |
| --- | --- | --- | --- | --- |
| Linux CI container | `make check` ([required PR check](#ci-and-candidate-identity)) | Application, API/storage integration and phone/desktop browser flows with fixture engines | Clean-host installation, user services, reboot, native macOS, container deployment | In use |
| Disposable Linux VM | `scripts/installation_vm.py` ([local VM run](#local-vm-run)) | Fresh install, user-service start, update, failed-update recovery, service start after a restart, uninstall, and the built `install.sh` through its public command against a release server inside the guest, on Ubuntu 24.04 x86_64 | Login/logout, the download from GitHub's published release, cross-release migration, other distributions | In use through an owner's machine grant |
| Hosted installation workflow | `installation-lifecycle.yml` ([lifecycle acceptance](#installation-lifecycle-acceptance)) | The same harness on GitHub's Ubuntu 24.04 runners | As for the VM | Not executed: hosted-runner spending limit |
| Container deployment | Owned by the container runtime work | Running Altitude itself in a container | Native installation | Not an entry point yet |
| Native macOS | Owned by the macOS runtime work ([roadmap](ROADMAP.md#native-macos-runtime)) | macOS service lifecycle, confinement, installation and Safari | Other macOS versions or architectures | Not established; remote runs from Linux wait on verified native support |
| Phone browsers | See [device evidence](#device-evidence) | Per class | Per class | Emulated WebKit in use; Simulator and physical checks by arrangement |

## Device evidence

Device results name their evidence class; a result in one class never stands in for another.

| Class | Runs | Establishes | Does not establish |
| --- | --- | --- | --- |
| Chromium phone/desktop | `make check` (required) | Application behavior, layouts and interaction states on both viewports | Any Safari or iOS behavior |
| Emulated iPhone WebKit | `make ui-ios` (opt-in) | The same walkthroughs in Playwright's WebKit engine with iPhone metrics, touch and user agent | iOS Safari, Home Screen mode, real microphone/speech, icon selection or certificate trust |
| iOS Simulator on a Mac | Not set up | Safari tab and Home Screen behavior, icon choice, separate Safari/Home Screen storage | Real audio capture, device certificate trust |
| Physical iPhone | Operator observation; [voice troubleshooting](OPERATIONS.md#on-iphone) reports | Native capture, trust, installed icon, Home Screen lifecycle | Other devices or OS versions |

Run the emulated iPhone lane for changes to phone-facing behavior such as voice, pairing, Home
Screen metadata or phone layout. It is outside `make check` and PR gates. Install WebKit into the
same browser cache as Chromium; on Linux it also needs WebKit's system libraries, which
`playwright install-deps webkit` installs with administrator rights (Ubuntu 24.04 desktop
typically lacks only `libevent-2.1-7t64` and `libavif16`). Workers cannot install them.

```sh
PLAYWRIGHT_BROWSERS_PATH="${ALTITUDE_HOME:-$HOME/.altitude}/browsers" pnpm --dir web exec playwright install webkit
pnpm --dir web build
make ui-ios
```

`web/playwright.ios.config.ts` runs one project named `phone`, so specs keep their phone layout and
`@phone-only` walkthroughs, with WebKit's mock microphone granted. Results and the HTML report stay
under ignored `web/ui-artifacts/ios/`; record the WebKit version from the report with any result.
Tests tagged `@chromium` need a harness capability this WebKit build lacks and run only in the
required projects: `MediaRecorder` (voice upload journeys), the `Notification` API, the
`clipboard-write` permission, a CDP session (manifest parsing, touch-drag swipes, transfer sizes and cache hits), or a replaceable
`navigator.mediaDevices.getUserMedia`. Give a new walkthrough that tag only for one of these
reasons. Where only one step needs Chromium, the step checks `browserName` and the rest of the test
still runs: route smoke omits its wheel overscroll, which mobile WebKit does not support, and brand
metadata omits Chromium's manifest parser.

Two observed engine differences matter when reading voice results: this WebKit build has no
`SpeechRecognition` or `MediaRecorder`, and an `AudioContext` created after an awaited microphone
request starts `suspended` in WebKit but `running` in Chromium. Both are engine observations, not
iOS results.

## Coverage and limits

Review evidence by user journey and failure mode. Full suites are required; a line-coverage number
or a test that only restates mocked calls cannot establish confidence. Add regressions that fail
when the observable behavior breaks. Keep the expected result independent of the implementation.

| Journey | Programmatic evidence | Boundary / remaining limit |
| --- | --- | --- |
| Private archive lifecycle | `test_installation.py`: real archives/checksums, immutable versions, install/update/uninstall retention, failed/interrupted recovery, conflicting ownership, stopped service and unsafe archive refusal. | Native service calls use deterministic fixtures; [manual installation acceptance](#installation-lifecycle-acceptance) exercises native service operations separately. An unrun workflow establishes no host evidence. |
| Installed task inputs | `test_installed_runtime.py`: no-checkout configuration, real Git guards, dispatch/resume ownership and hold retention, missing-guard refusal, installed/source separation. | Engine execution is deterministic; no live provider or native Mac confinement evidence. |
| Local HTTPS | `test_tls.py`, `test_https_server.py`: real OpenSSL identities and TLS handshakes, hostname/trust failure, permissions, renewal, key mismatch and unchanged external certificates. | Local CA validation does not prove OS/browser trust. Physical desktop/mobile browser and home-screen-app trust need separate evidence. |
| Source TLS preparation | `test_source_tls.py`: real TLS identity and isolated files, check-only, owned override/reload verification, reset command-history metadata, actual identity/command drift, private failure diagnostics and recovery; `test_release_archive.py` verifies the standalone archive path before installation. Worker command tests preserve role denial. | Native unit/process evidence is simulated; the operator must verify actual source preservation before activation. No production services or trust stores are changed by tests. |
| Task delivery | `tests/test_offline_journeys.py`, `test_lifecycle.py`, `test_direct_l2_completion.py`: dispatch/worktree, report and archive; no-code completion refuses unlanded code or active workers. | Worker execution and GitHub responses are scripted; actual provider/GitHub permissions are unproven. |
| Trusted launch inputs | `test_launch_source.py`, `test_git_policy_integration.py`: committed installation export, activated helper paths, dirty staged/working/untracked deployment preservation, fresh base, owned resume and independent deployment failures. | Real Git and deterministic engine fixtures establish application behavior; live provider loading and host confinement remain separate evidence. |
| Repository rules | `test_repository_instructions.py`: fresh/resumed L2 and L3 turns on both engines, scratch cwd, shared import target, AGENTS-only and legacy CLAUDE-only projects, rule-source migration and boundary excerpts. | Fixtures establish emitted paths and file resolution, not live provider native loading or adherence. |
| Messaging/resume | `test_task_chat.py`, `test_chat_queue.py`, `test_resume_hold.py`: durable inbox, same session, late messages, concurrent wakes and recovery claims. Browser task lifecycle checks API persistence and terminal states. | No real provider turn or process recovery is launched. |
| Planned work | `planned-tasks.pw.ts`: phone/desktop waiting, saved brief updates, explicit release under capacity, archived-done dependency release and real Git dispatch; send denial and Work loading/error/empty states. | Worker I/O is deterministic; named HTTP overlays establish read presentation states only. |
| Operator image input | `test_images.py`, `test_image_chat.py`, `test_image_delivery.py`: real raster conversion/storage/HTTP, admission retry, caption ordering, checkpoint/resume restoration, handoff and archive/isolation. `image-input.pw.ts` walks selection/removal, voice/paste, loading, refusals, uncertain retry, sent viewer and missing/denied content on phone/desktop. | Native payloads and readable canonical bytes use engine fixtures; physical phone picker, clipboard permissions and live account/model image compatibility are unverified. |
| CI recovery | `test_ci_recheck.py`, `test_ci_recheck_delivery.py`: due probes, question-blocked observation-only waits, single rerun intent, uncertain writes, fresh artifacts, finite reads/delivery, queue/turn crashes, stale lifecycle and preserved controls on both engine seams. | GitHub responses and engine calls are fixtures; real quota recovery needs fresh uploaded artifact evidence. |
| Adversarial review | `test_review_engines.py`, `test_reviews.py`, `test_review_interfaces.py`: the captured-input adapter over real stdio (list/read/search, long-line continuation, refused private paths, read marker), the first JSON object accepted from a prose-wrapped answer and no object refused, both engines' confinement flags, unit lifecycle without a duration cutoff, cancellation, unknown termination, and a reviewer that read nothing failing instead of completing with no coverage. | Fixture processes stand in for the CLIs; whether the installed Codex exposes MCP tools through its code-mode host, and whether the installed Claude connects the adapter without safe mode while loading no hooks, plugins or instructions, is established by recorded reviewer runs, not by the suite. |
| Routing/failures | `test_route.py`, `test_direct_dispatch.py`, `test_temporary_capacity.py`, composed journeys: pins, availability, fallback, backoff and failure without duplicate completion. | Deterministic availability/quota input does not prove real authentication, entitlement or current provider compatibility. |
| Landing | `test_land.py`, `test_land_contention.py`, `test_git_policy_integration.py`: real Git/bare origins, concurrent owner processes (merging and required-check turns, timeouts, terminated holders), ordinary and assigned histories without trailer repair, selected index content, unrelated working edits, task/PR ownership, adopted ancestry, holds, failed/skipped/absent checks, changing base/head, candidate validation and cleanup. | Fake GitHub cannot establish remote API/permission/check-association compatibility. Owners review outgoing history and diffs for scope and privacy; tests do not establish semantic content screening. |
| Merge approval | `test_recorded_merge_approval.py`, `test_merge_approval_journey.py`: original task/project/UI authority, intervening discussion, Git integration and scoped follow-up delivery; invalid sources, renewed holds and wrong PR identity refuse. | Scope, revocation and conditions are explicit coordinator judgments in fixtures; these tests do not establish live model interpretation. |
| Isolation | `test_isolation.py`, `test_codex_door.py`, `test_l3_privilege.py`, `test_service_lifecycle.py`: isolated storage, ownership, denied paths and simulated restart/adoption. | Test guards prevent accidental external effects; they are not a security sandbox for hostile test code. Actual OS confinement/service restart is separate host evidence. |
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

Image integration fixtures require the detected local `ffmpeg` converter (`ffmpeg` on Debian/Ubuntu).
RGB ICC tests use the detected system `liblcms2` library (`liblcms2-2` on Debian/Ubuntu) for bounded
color conversion; production reports unavailable profile conversion explicitly when it is absent.
Inputs are limited to ordinary static raster images. HDR declarations, non-RGB ICC profiles,
profiles over 4 MiB and non-sRGB gamma/chromaticity
without an ICC profile require an exported sRGB copy. Decoder wall/CPU/memory/output bounds,
orientation, alpha/color parity and intermediate cleanup have real conversion fixtures. The service
and Python harnesses replace image capability checks and native execution at the engine seam.

## Installation lifecycle acceptance

`.github/workflows/installation-lifecycle.yml` is an independent, manually dispatched Ubuntu 24.04
x86_64 workflow. It is outside `make check`, PR triggers and release gates; its failure or pending
state does not hold other tasks. The required self-hosted PR `check` and all review requirements
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
an operator machine or deployment. A trailing `reboot-install` phase installs the baseline in a new
account and leaves it running; after the VM restarts, `reboot-verify` checks that the user manager
started the same installation without a login, then uninstalls it and removes the account. Each
phase writes to its own subdirectory of the results. The `bootstrap` phase runs the built
`install.sh` through its public `curl … | sh` command in a new account. For the test, the release's
host name resolves to the guest's loopback, where the account serves the release over HTTPS with a
throwaway certificate authority that only this test's `curl` trusts. A download altered by one byte
must be refused with nothing installed; the unaltered one must install a healthy service, which is
then uninstalled. The hosts entry and unprivileged-port setting it needs are restored afterwards.
Retain its results before discarding the VM. The hosted workflow
runs neither phase and does not prove a minimal OS install or login/logout behavior, browser/device CA trust,
a download from GitHub's published release, native confinement or provider compatibility. There is no browser test
in this harness. Native macOS installation remains with `macos-support-native-runtime-behind-the`;
this Linux evidence is partial acceptance toward #226 and does not close it or establish public readiness.

### Local VM run

`scripts/installation_vm.py` runs the same harness on a Linux x86_64 host with KVM, at no cost and
without GitHub runners. It needs `qemu-system-x86`, `qemu-utils` and `cloud-image-utils` (installed
once by the machine's administrator) and read/write access to `/dev/kvm`. Build the two directories
from a clean committed source, then pass them with that commit:

```sh
python3 scripts/build_release.py --version v0.0.0-rc.1 --output /tmp/altitude-vm/baseline
python3 scripts/build_release.py --version v0.0.0-rc.2 --output /tmp/altitude-vm/candidate
python3 scripts/installation_vm.py /tmp/altitude-vm/baseline /tmp/altitude-vm/candidate \
  /tmp/altitude-vm/results "$(git rev-parse HEAD)"
```

The runner downloads the current Ubuntu 24.04 cloud image, checks its signed checksum with the
installed Ubuntu cloud-image keyring and caches it under `~/.cache/altitude-installation-vm`. Each
run boots a copy-on-write overlay with 2 CPUs, 4 GiB of memory and a 12 GiB disk, logs in with a
per-run SSH key over a loopback-only port, and deletes the overlay, key and seed afterwards, also
when the run is stopped. The guest has two network cards on separate QEMU user networks. One is
online only while cloud-init installs Git, GitHub CLI and OpenSSL, and is then unplugged. The other
is restricted to the SSH forward. Before the harness starts, the runner probes the internet and a
listener it opens on the host's loopback. Through the online card both must answer and through the
restricted card the host must not; after unplugging, nothing may answer. Any other outcome, or a
probe that cannot run, stops the run. After the lifecycle passes, the runner runs `bootstrap` and
`reboot-install`, restarts the VM, checks that it is still isolated and runs `reboot-verify`. Results hold the harness evidence plus `vm.json` (source
commit, image and signature, QEMU version, guest OS and kernel, probe outcomes, each phase's exit) and the VM console
and QEMU logs; `harness.log` and the `harness-*.log` files are written as the phases run. The runner prints each stage with its
elapsed time; after the first image download, a run takes about three and a half minutes, two of them
while the restarted guest waits for its unplugged card. An Altitude worker cannot
launch VMs from its sandbox; an owner runs this through a
recorded [machine grant](CLI.md#machine-access) whose purpose names these VMs. The runner never touches the
host's Altitude service, trust stores or network configuration.

## CI and candidate identity

The self-hosted `.github/workflows/self-hosted-checks.yml` runs `make check` for the repository
owner's pull requests from branches in this repository, pushes to main and manual dispatches on
the dedicated ephemeral-container runner (label `altitude-ci-docker`). Its PR `check` is required
by `alt land` in every repository whose base commit ships that workflow; owners and helpers run
relevant tests during development instead of repeating a full local suite at every landing.
Python, web unit tests, typecheck/build and both browser viewports must execute and pass.
No unrun or failed phase is green; live-provider validation stays deferred.
`.github/workflows/release.yml` runs only for a pushed `v0.*` tag and publishes that checked commit's
release; see [publish a release](RELEASING.md#publish-a-release).

Every other pull request, from a fork or from an author other than the repository owner, runs the
same `make check` through `.github/workflows/hosted-checks.yml` on a GitHub-hosted runner with a
read-only token and no secrets, so untrusted code never executes on the owner's machine. A failed
hosted run uploads its browser HTML report as a seven-day artifact. That job is contributor
feedback, not the landing gate: after review, Altitude integrates the contribution on a
repository branch, where the required self-hosted `check` runs before the merge. GitHub's
fork-workflow approval setting (require approval for first-time contributors) stays on once the
repository is public. A fork of this repository without its own self-hosted runner leaves the
self-hosted job queued for its own owner's pull requests and relies on the hosted workflow.

CI records its event base, head, candidate SHA and tree, verifying that the head includes that base
and that the tested merge tree equals the head tree. Landing requires the specific successful PR
job on the current head, verifies that current main is an ancestor of that head, and serializes
publication, CI waiting and merge across Altitude owners, including nonmerging invocations.
For reviewed merging candidates, CI and explicit owner reassessment share one bounded wait while
the same process retains the repository turn. `tests/test_land_contention.py` drives real competing
landing processes, Git and fixture reviewers through main integration, in-turn assessment, fresh
required checks, timeout, termination, ownership loss and material-edit refusal. Proposal followed by
implementation review reports both stale assessments together; messages during admission, hosted CI
or local validation require explicit assessment in the same landing invocation. Invalid dispositions
leave the candidate unmerged and its operator hold intact, and final validation does not restart the
assessment deadline. `tests/test_reviews.py` checks the review identity and changed evidence in refusals.
Original review receipts remain unchanged. Fixtures establish the application protocol, not live engine support for
background tool sessions or provider compatibility.
Runner executions outside the command do not share its turn. Any base or head movement after candidate pinning
refuses the merge. A later invocation can reuse the successful head when main is already an
ancestor of it: the merge still has the identical tested tree. Ordinary competing merges introduce
commits outside the head and require reconciliation, a push and fresh checks on the new head.
Landing verifies the merged tree against the tested tree; commit metadata can differ.
Missing, pending, failed, skipped, cancelled, stale or unrelated required runs block.
Review and task/UX holds remain enforced. The operator chooses Altitude-only enforcement without
a GitHub plan upgrade: GitHub web/API merges and other updates outside Altitude remain unprotected.
All main updates must use `alt land` for its guarantee.

Other projects retain their configured hosted/no-CI gates. Shared hosted-check handling ignores
completed nonrequired skips without parsing workflow conditions; failed or pending checks still
block, and at least one hosted check must actually pass. Requiredness and candidate association
must be established. Projects without CI retain the full local candidate suite and `--test-cmd`
(one argv command, see [dry run and gate selection](CLI.md#dry-run-and-gate-selection));
neither provides an outage bypass for this repository.

CI retains evidence on the runner host without GitHub artifact uploads. The workflow writes
`ci-check.log` and `ci-result.json` directly in `RUNNER_TEMP`; the runner's completion hook copies
those receipts before clearing temporary files. Its existing bounded exporter retains them before
removing the disposable container. Failed runs also retain the self-contained browser HTML report
with its screenshots and failure traces. Passing runs keep only small logs and identity receipts.
Duplicate raw results and caches are excluded; early failures keep available diagnostics.

A passing required check on the current head, with its GitHub console log, is sufficient delivery
evidence; `alt land` verifies the head, tree and base ancestry. Owners retrieve the local export
(browser HTML report, screenshots, traces) into their task folder only to diagnose a failed run or
when a reviewer asks, match its run URL, attempt, head and tree to the candidate, and open it with
`pnpm --dir web exec playwright show-report /path/to/report`. When the export is unreadable from the
task sandbox (issue #380), the owner says so in the report and continues with the console log.

The runner limits evidence to 256 MiB per job and 4 GiB for this repository. It reserves the
per-job limit for each of its four slots, so it admits a job only while the full retained size is at
most 3 GiB; otherwise it logs "Evidence budget reached" and every queued job waits. Private runner
diagnostics count toward that size but are unreadable to owners, so a worker-readable total is a
lower bound. Owners can read exports but not remove them. An owner that needs a failed run's report
for review copies it into its task. When admission refuses, L3 coordinates the cleanup: a read-only
inventory of completed exports whose PR has merged or closed, or whose main run a later green main
superseded, with no open task or incident relying on them; their receipts and console logs copied
into the recovery task; and an operator machine grant to measure the full size as the CI account,
remove exactly that list and verify that the runner starts queued jobs. Never remove active jobs,
open PRs' failures or the only copy of evidence awaiting review. A budget refusal requires bounded
cleanup and verification, not a quota increase. Task evidence stays accessible through review. No new paid
storage, public report server or host mount into Altitude runtime is required. Runner credentials
and other projects' evidence remain outside the report access path. GitHub still supplies checks
and console logs; browser reports are read locally instead of downloaded from GitHub.

An owner keeps a bounded CI wait in its active session. If it cannot obtain the required result,
it records the run and missing evidence, explicitly blocks and asks L3 for the existing finite
[`recheck-ci`](CLI.md#durable-ci-recheck). No run means trigger/runner recovery, not an invented
run ID. Runner outages pause delivery until verified recovery and fresh CI. A probe
does not resume the owner, settle a question or release a hold; L3 owns that reconciliation.
The existing GitHub artifact-capacity probe remains for projects using hosted artifacts.

### Gate activation

The transition PR uses trusted landing's existing full local candidate gate as well as a fresh PR
run. Verify the installed local exporter and owner access before relying on report retention.
Actions is already enabled; no plan upgrade or branch protection change is required. Verify the
runner admits PR events and a fresh PR run executes Python, web, build and both browser viewports.
A bounded failing PR revision demonstrates retrieval of browser reports and traces after container
removal; a passing revision demonstrates the small receipt/log export. Restore a passing revision
before review. Runner configuration changes require scoped machine authority.
Known browser/setup flakes require verified correction before activation; fewer duplicate full
runs do not establish a fix. L3 owns prerequisite coordination.

The operator reviews the green PR, L3 records its hold release, and the owner uses trusted
`alt land --merge`. Normal source activation applies the gate. L3 coordinates existing owners
using older committed exports so later mergers share the serialization contract, preserving
their sessions, PRs and holds. Verify fresh main checks, tested/merged tree equality and a live
CI-only delivery before declaring the rollout complete. Do not run candidate landing code against
live state to bootstrap its own authority.
[Release readiness](RELEASING.md) still binds validation to a final main SHA.

## Runtime evidence

Use `make check` for per-phase wall/user/system timings and retain the runner summaries with the
source SHA and tool versions. Separate dependency/browser installation from warm execution.
The serial baseline supplied for this change is self-hosted run 35065148992: 13 min 54 s
on four CPUs, including Python at 4 min 15 s and 316 browser tests at 9 min 12 s with two workers.
The measured reference is self-hosted run 35076675654 on 2026-09-16, completing the concurrent gate in 3 min 51 s at
eight process-available CPUs: Python 149.54 s (1,371 tests, one optional native probe skipped,
four processes), web tests 13.54 s (360 passed), typecheck/build 4.82 s and browsers 212.58 s
(324 passed, eight workers, zero retries). Phase wall times overlap and must not be added.
CI allocations vary with available capacity. Worker counts follow the process's available CPUs,
not a fixed container size or host-wide count. This reference uses head `998da21`; it is not
acceptance evidence for a later revision. PR evidence records the current source, allocation,
timings and complete results. Run local candidate checks and self-hosted measurements sequentially
when they share a host, and retain scoped memory and termination observations alongside timings.

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
instructions. [Operations](OPERATIONS.md) covers separately authorized service and physical-device
checks. These development URLs do not select the automated test target.
