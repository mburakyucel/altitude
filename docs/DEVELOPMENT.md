# Development and checks

Start with [CONTRIBUTING](../CONTRIBUTING.md) for scope and review expectations and
[setup](SETUP.md) for the supported runtime. Python uses the standard library; the web app uses
its pnpm lockfile. Routine checks use deterministic fixtures, make no model calls, and do not
use the operator's service or runtime records.

## Local checks

Use Python 3.12+, Node 22.22.2+ (22.x) or 24.15+ (24.x), and the pnpm version pinned in
`web/package.json`. From the repository root:

```sh
pnpm --dir web install --frozen-lockfile
PLAYWRIGHT_BROWSERS_PATH="${ALTITUDE_HOME:-$HOME/.altitude}/browsers" pnpm --dir web exec playwright install chromium
make check
```

`make check` times the full Python suite, web unit suite, TypeScript/build and all isolated browser
specs. Python prints each test name so an incomplete run identifies the last test it started.
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

Incident IDs are project-local. Cross-project authority fixtures select a foreign incident ID
absent from the local project, rather than assuming creation order makes IDs globally unique.
`test_upstream_issues.py` controls the incident allocator's clock to exercise both same-second
allocation and a second boundary while retaining real incident storage and authority checks.
Independent Git fixtures created with `make_repo` use separate parent directories because each
bare `origin.git` lives beside its checkout. The fault/recovery reporting test gives its two
initial commits distinct timestamps so shared origins cannot hide behind identical commits.
The automatic-GC fixture uses `git repack -a` without `-d` to retain two packs and trigger real
fetch housekeeping. Unlike `pack-objects --all`, it supplies its own revision input rather than
waiting for the caller's stdin to close. A bounded subprocess regression holds stdin open while
capturing stdout/stderr, matching landing's inherited-input condition, and checks the same real
packing, pruning, protected-tip and fast-forward assertions. The shared CLI fixture supplies empty
input explicitly; the same regression includes real issue-close CLI/API validation so it cannot
wait on the worker's input stream either. Tests that submit a body supply that input themselves.

## Browser walkthroughs

Build the candidate app before `make ui`; `make check` does this in order. Every test uses a
fictional populated project in a disposable Python service on an OS-selected loopback port.
The service serves this checkout's `web/dist` and real application HTTP handlers. No live project,
service URL or active worker is needed. Tests clean up their owned service processes and temporary
storage, with bounded termination. Never point routine acceptance at operator data.

Both projects run headlessly: phone at 390×844 with touch/mobile user agent and desktop at
1440×900. Install the Chromium build matching the locked Playwright version. The browser cache
can be shared across worktrees through `PLAYWRIGHT_BROWSERS_PATH`; use the same value for install
and execution. Chromium's own sandbox is disabled inside the worker filesystem sandbox because
of the documented host browser restriction; profiles/configuration remain temporary or under
ignored `web/ui-artifacts/`. Missing browsers fail with installation guidance.

`web/e2e/fixtures.ts` starts the disposable services. `acceptance-service.py` supplies fictional
tasks/history for route and component walkthroughs. Project-isolation and project-lifecycle
services exercise real streaming/queue/history and removal/reattachment; task-lifecycle specs
exercise real message/decision effects. Fixture providers replace external calls at the engine
boundary. HTTP overlays are reserved for named loading, transport failure and presentation states;
an intercepted success response alone is not evidence of a backend transition.

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
screenshots and traces are retained on failure. Passing tests discard their traces. The report
includes attached screenshots and failure traces in `report/data/`, with the trace viewer alongside
them. Keep that whole report directory together when opening or sharing it.
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

## Coverage and limits

Review evidence by user journey and failure mode. Full suites are required; a line-coverage number
or a test that only restates mocked calls cannot establish confidence. Add regressions that fail
when the observable behavior breaks. Keep the expected result independent of the implementation.

| Journey | Programmatic evidence | Boundary / remaining limit |
| --- | --- | --- |
| Task delivery | `tests/test_offline_journeys.py`, `test_lifecycle.py`, `test_direct_l2_completion.py`: dispatch/worktree, report and archive; no-code completion refuses unlanded code or active workers. | Worker execution and GitHub responses are scripted; actual provider/GitHub permissions are unproven. |
| Repository rules | `test_repository_instructions.py`: fresh/resumed L2 and L3 turns on both engines, scratch cwd, shared import target, AGENTS-only and legacy CLAUDE-only projects, rule-source migration and boundary excerpts. | Fixtures establish emitted paths and file resolution, not live provider native loading or adherence. |
| Messaging/resume | `test_task_chat.py`, `test_chat_queue.py`, `test_resume_hold.py`: durable inbox, same session, late messages, concurrent wakes and recovery claims. Browser task lifecycle checks API persistence and terminal states. | No real provider turn or process recovery is launched. |
| Operator image input | `test_images.py`, `test_image_chat.py`, `test_image_delivery.py`: real raster conversion/storage/HTTP, admission retry, caption ordering, checkpoint/resume restoration, handoff and archive/isolation. `image-input.pw.ts` walks selection/removal, voice/paste, loading, refusals, uncertain retry, sent viewer and missing/denied content on phone/desktop. | Native payloads and readable canonical bytes use engine fixtures; physical phone picker, clipboard permissions and live account/model image compatibility are unverified. |
| CI recovery | `test_ci_recheck.py`, `test_ci_recheck_delivery.py`: due probes, single rerun intent, uncertain writes, fresh artifacts, finite reads/delivery, queue/turn crashes, stale lifecycle and preserved controls on both engine seams. | GitHub responses and engine calls are fixtures; real quota recovery needs fresh uploaded artifact evidence. |
| Routing/failures | `test_route.py`, `test_direct_dispatch.py`, `test_temporary_capacity.py`, composed journeys: pins, availability, fallback, backoff and failure without duplicate completion. | Deterministic availability/quota input does not prove real authentication, entitlement or current provider compatibility. |
| Landing | `test_land.py`, `test_git_policy_integration.py`: real Git/bare origins, selected index content, unrelated working edits, ownership/holds, failed/skipped/absent checks, changing base/head, candidate validation and cleanup. | Fake GitHub cannot establish remote API/permission/check-association compatibility. |
| Merge approval | `test_recorded_merge_approval.py`, `test_merge_approval_journey.py`: original task/project/UI authority, intervening discussion, Git integration and scoped follow-up delivery; invalid sources, renewed holds and wrong PR identity refuse. | Scope, revocation and conditions are explicit coordinator judgments in fixtures; these tests do not establish live model interpretation. |
| Isolation | `test_isolation.py`, `test_codex_door.py`, `test_l3_privilege.py`, `test_service_lifecycle.py`: isolated storage, ownership, denied paths and simulated restart/adoption. | Test guards prevent accidental external effects; they are not a security sandbox for hostile test code. Actual OS confinement/service restart is separate host evidence. |
| API/UI streaming and projects | `project-isolation.pw.ts`, `project-lifecycle.pw.ts`: concurrent streams in both completion orders, accepted/refused errors and retry, queue/history, cross-project ownership and reattachment/session retention. | External engine output is deterministic. |
| Phone/desktop states | `smoke.pw.ts`, task/navigation, work/decision, conversation, monitor, usage and restart specs: empty/loading/failed/denied/pending/terminal states, scrolling/navigation and visible removals. | Some states use explicit HTTP overlays. Chromium phone emulation is not physical Safari/microphone validation; restart banner assertions do not restart a service. |

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

## CI and candidate identity

The operator's 2026-09-09 Pacific decision suspends hosted CI for this repository while billing
and artifact capacity are unavailable. The workflow is removed; Git history retains its source.
The repository-specific `LOCAL_CHECK_REPOSITORY` in the trusted `config.py` selects mandatory
local `make check` through the existing landing candidate mechanism. Other projects retain their
gates. No runner installation, new service, billing change or generic bypass flag is involved.

`alt land` pins the current base and head, constructs the candidate for the selected merge method,
installs frozen web dependencies using the shared Altitude-home pnpm store, then runs the full
suite with `CI=true`, retaining the test runners' refusal of focused-only tests. Install the
supported tools and matching shared Chromium first. Candidate `install.log`, `check.log`, the
self-contained `ui-artifacts/report/` and `result.json` stay in the task's `local-checks/<candidate>/`
folder, including failed-test evidence. Raw `results/` and browser configuration are not copied;
their attached screenshots and failure traces are already in the report. Installation or test
failures before a report exists retain the available logs and result record. Open the retained
report with `pnpm --dir web exec playwright show-report /path/to/ui-artifacts/report`.
Results name command, exit status, base, head, candidate SHA and tree.
On suite timeout, `check.log` retains captured stdout and stderr before the timeout diagnostic;
an incomplete encoded character is replaced so it cannot prevent evidence retention. The result
remains failed with no exit status or passing-test count, even if an earlier phase passed.
The synthetic commit's metadata differs from the eventual GitHub commit; the tree and bound
base/head identify the tested merge content. A local pass updates the PR with
`Tests: make check passed locally (<candidate SHA>)` plus its base/head. A stale pair, failed test
or failed prerequisite cannot authorize a merge. Python, web, build and both browser viewports
remain required. No unrun or failed phase is green; live-provider validation stays deferred.

Historical hosted failures on open PRs are retained but do not gate this explicitly selected
local policy. Branch protection and active rules are still inspected: required hosted checks
must be removed by the operator before local delivery. Outstanding reviews and task/UX merge
holds remain enforced. Local logs do not claim GitHub billing or artifact capacity recovered.

The first transition PR needs a one-time operator bootstrap: the pre-transition trusted landing
code inspects workflows on both base and head and has no local-policy override. Prepare and
review the PR through `alt land`, run full local checks on its exact current merge candidate,
and publish that evidence through `alt land --pr-body-file`. The operator disables repository
Actions in Settings → Actions → General, removes any required hosted checks shown in the current
rule inventory, then merges that exact reviewed PR through GitHub. Recheck base/head immediately
before merging; changed tips require fresh tests. Do not run the candidate's modified landing code
against live state. Verify the merged tree, main ancestry and normal automatic activation.

L3 retains resume authority. Existing owners rebase onto the activated main, retain their sessions
and PRs, and run their own full candidate checks. CI-only blockers can then proceed; real local
test/provider blockers and pending UX acceptance remain unresolved. L3 reconciles obsolete hosted
workflow prerequisite and artifact-recovery dependencies, including PR #315, without silently
discarding agreed work or closing its issues. Finite CI probes do not authorize owner recovery.

To restore CI, obtain an operator decision and deliver a reviewed PR reverting this policy
transition, reconciling the historical workflow with the prerequisite work in PR #315 and any
later changes. Run the same full local gate before that merge. The operator then re-enables
Actions and restores only the required checks recorded in the pre-disable rule inventory.
Verify a fresh main run actually executes Python, web, build and both browser viewports before
relying on hosted delivery; artifact recovery needs fresh uploaded evidence separately.
Preserve failure-only traces and all named screenshots when restoring artifact upload: package
the self-contained HTML report and necessary logs once, excluding duplicate raw results, with
the previous three-day hosted retention. Verify report attachments and failure traces open from
the downloaded artifact. These are restoration requirements, not an active hosted workflow or
completed hosted verification; local evidence remains accessible until review is complete.
[Release readiness](RELEASING.md) still binds validation to a final main SHA.

## Runtime evidence

Use `make check` for per-phase wall/user/system timings and retain the runner summaries with the
source SHA and tool versions. Separate dependency/browser installation from warm execution.
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
