# Development and checks

Start with [CONTRIBUTING](../CONTRIBUTING.md) for scope and review expectations and
[setup](SETUP.md) for the supported runtime. Python uses the standard library; the web app uses
its pnpm lockfile. Routine checks use deterministic fixtures, make no model calls, and do not
use the operator's service or runtime records.

## Local checks

Use Python 3.12+, Node 22.22.2+ (22.x) or 24.15+ (24.x), and the pnpm version pinned in
`web/package.json`. CI uses Node 22.22.2. From the repository root:

```sh
pnpm --dir web install --frozen-lockfile
PLAYWRIGHT_BROWSERS_PATH="${ALTITUDE_HOME:-$HOME/.altitude}/browsers" pnpm --dir web exec playwright install chromium
make check
```

`make check` times the full Python suite, web unit suite, TypeScript/build and all isolated browser
specs. Dependency and browser installation are explicit prerequisites, so a warm run need not
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

Screenshots, traces and HTML reports live under ignored `web/ui-artifacts/`, grouped by spec and
viewport. CI artifacts contain only fictional test data. Keep actual service captures, session
logs, conversations and private incident evidence out of shared artifacts.

## Coverage and limits

Review evidence by user journey and failure mode. Full suites are required; a line-coverage number
or a test that only restates mocked calls cannot establish confidence. Add regressions that fail
when the observable behavior breaks. Keep the expected result independent of the implementation.

| Journey | Programmatic evidence | Boundary / remaining limit |
| --- | --- | --- |
| Task delivery | `tests/test_offline_journeys.py`, `test_lifecycle.py`, `test_direct_l2_completion.py`: dispatch/worktree, report and archive; no-code completion refuses unlanded code or active workers. | Worker execution and GitHub responses are scripted; actual provider/GitHub permissions are unproven. |
| Repository rules | `test_repository_instructions.py`: fresh/resumed L2 and L3 turns on both engines, scratch cwd, shared import target, AGENTS-only and legacy CLAUDE-only projects, rule-source migration and boundary excerpts. | Fixtures establish emitted paths and file resolution, not live provider native loading or adherence. |
| Messaging/resume | `test_task_chat.py`, `test_chat_queue.py`, `test_resume_hold.py`: durable inbox, same session, late messages, concurrent wakes and recovery claims. Browser task lifecycle checks API persistence and terminal states. | No real provider turn or process recovery is launched. |
| Routing/failures | `test_route.py`, `test_direct_dispatch.py`, `test_temporary_capacity.py`, composed journeys: pins, availability, fallback, backoff and failure without duplicate completion. | Deterministic availability/quota input does not prove real authentication, entitlement or current provider compatibility. |
| Landing | `test_land.py`, `test_git_policy_integration.py`: real Git/bare origins, leases/holds, failed/skipped/absent checks, changing base/head, candidate validation and cleanup. | Fake GitHub cannot establish remote API/permission/check-association compatibility. |
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

## CI and candidate identity

`.github/workflows/remote-tests.yml` runs the full gate on same-repository PRs and main pushes.
It retains the check name **Remote tests / Python** for existing consumers, but its fixed commands
also run web tests, build and browsers. The base-owned PR workflow validates the event's head,
base and repository, checks out the exact candidate and verifies its SHA. Repository credentials
are not persisted. Tests/install run in a clean environment with disposable homes, no provider
credentials, pinned tooling and frozen dependencies. No candidate Makefile controls CI commands.

The job attempts to retain logs, timings and fictional Playwright artifacts for three days,
including failure runs. Only **Upload fictional test results** has `continue-on-error: true`:
upload failures remain visible but do not fail the test job. Downloadable evidence may be
unavailable; use the job's test output and exact candidate identity when assessing its result.
Python, web, build, phone/desktop checks and candidate validation remain required.
The job has a finite timeout; browser assertions and fixture shutdown also have deadlines.
A failed, canceled, absent or skipped required phase cannot be reported as green. A deliberately
optional native host probe is identified separately from required deterministic cases.

A workflow-changing PR runs the workflow already on its base. Its first rollout therefore needs
full local candidate validation and workflow review, followed by verification that the expanded
main-push job actually ran. Record these facts separately; an old Python-only result is not
remote browser evidence. On later PRs the expanded base workflow runs all phases. Before landing,
reconcile moving main and validate the exact merge candidate; CI's PR head is not a synthetic
merge tree. [Release readiness](RELEASING.md) binds evidence to a final main SHA.

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
