# Development and checks

Start with [CONTRIBUTING](../CONTRIBUTING.md) for scope and review expectations and
[setup](SETUP.md) for the supported runtime. The Python backend uses the standard library;
the React/TypeScript app uses the pnpm lockfile in `web/`.

## Local checks

From the repository root, with Python 3.12+, Node 22.22.2+ (22.x) or 24.15+ (24.x) and pnpm available on PATH:

```sh
make test
pnpm --dir web install --frozen-lockfile
pnpm --dir web test
pnpm --dir web build
```

Run the full Python and web unit suites on every PR. `make test` supplies throwaway Altitude
state through the test bootstrap. A single Python test module needs an explicit temporary root:

```sh
ALTITUDE_HOME="$(mktemp -d /tmp/altitude-test.XXXXXX)" python3 -m unittest tests.test_project_layers
```

Inside an Altitude worker, run the suite as `env -u ALTITUDE_ACTOR make test`: the Git-guard
installation test invokes the operator CLI against a temporary repository, and an inherited
worker identity correctly refuses that command. This environment adjustment is for the isolated
test suite only; operational commands retain the worker's identity.

The build includes TypeScript checking. `make web` is the build convenience target; it also
prepends a maintainer-specific Node installation if present, so the direct pnpm commands above
are clearer on a new machine. In a restricted worktree, use
`pnpm --dir web install --frozen-lockfile --store-dir /tmp/altitude-ui-pnpm-store` if the package
store is not writable. Do not change the lockfile to work around an installation failure.

## End-to-end validation

After a change to dispatch, engines, or landing, run one real tiny task through chat, task,
PR, checks, merge, and archive, as required by
[working rule 4](SIMPLIFICATION.md#working-rules-that-still-apply-to-every-pr).
L3 creates it from chat through the trusted `alt task new` door. The L2 delivers a useful small
change through the normal PR, full checks, review as appropriate, and merge path, respecting any
recorded merge hold. L3 finalizes and archives the task; the originating task records the task
identity, observed engine/model, PR, merge SHA, check results, and archive evidence.

This validates the observed path only. A documentation-only validation task does not change
dispatch, engines, or landing, so it does not require another live validation task.

## UI development

Against your own running backend on an unreserved port, a separate terminal can run:

```sh
ALTITUDE_DEV_API=http://127.0.0.1:18890 pnpm --dir web dev --host 127.0.0.1 --port 15173
```

Use the URL printed by Vite. Choose ports free on your machine. Vite serves the app and proxies
API requests; it does not launch agents or replace the backend. Follow the repository's UI
interaction-state rule in the project instructions when changing components.

## Browser walkthroughs

Set `UI_BASE_URL` explicitly on a new machine; the harness default is a maintainer-specific
HTTPS tunnel address, not a portable local target. For the example backend in the setup guide:

```sh
export UI_BASE_URL=http://127.0.0.1:18890
```

The UI suite uses Playwright from a plain shell on every engine. With Node 22.22.2+ (22.x) or 24.15+ (24.x) and pnpm available,
install once with `pnpm --dir web install --frozen-lockfile` (in a restricted worktree, add
`--store-dir /tmp/altitude-ui-pnpm-store` to keep the package store writable). `make ui` runs route
smoke and component walkthroughs headlessly at 390×844 and 1440×900 against the local service at
the configured service URL. Set `UI_BASE_URL` to target a throwaway altd on an unreserved port or a
Vite dev server; Vite's `ALTITUDE_DEV_API` points its API proxy at that service. The suite reads a
managed project and a real active or archived task; `UI_PROJECT` and `UI_TASK` select them when
needed. A throwaway service needs those records populated. Missing data fails explicitly, never
silently skips route coverage. It does not create projects, send messages, or dispatch tasks.

The harness prefers bundled Chromium (`channel: "chromium"`), with its browser sandbox disabled
inside the worker's filesystem sandbox. Some host AppArmor profiles deny installed Chrome network
sockets in that environment. Install the bundle once per Playwright version:

```sh
PLAYWRIGHT_BROWSERS_PATH="${ALTITUDE_HOME:-$HOME/.altitude}/browsers" pnpm --dir web exec playwright install chromium
make ui
```

`pnpm ui` and `make ui` share that writable cache across task worktrees; `PLAYWRIGHT_BROWSERS_PATH`
overrides it for installation and execution together. Installed Chrome (`channel: "chrome"`) is
the fallback only when the bundled browser is absent. Profiles are temporary; no visible desktop
window opens. The browser install must match the locked Playwright version.
Crashpad's writable configuration also stays under `web/ui-artifacts/browser-config/`.

`web/e2e/*.pw.ts` specs stay separate from the Vitest unit suite (`pnpm --dir web test`). Both
viewport projects run each spec; `walkthrough.ts` asserts appearances and removals and captures
named states. `project-menu.pw.ts` walks closed, open, reset confirmation, cancelled and dismissed
states without confirming a reset. Screenshots, traces and the HTML report stay under ignored
`web/ui-artifacts/`, grouped by spec and viewport. These contain real service data: keep them local
and reference the proving spec in the PR. For a human-requested headed run of one spec:
`make ui UI_ARGS='project-menu.pw.ts --project=desktop --headed'`. To view the saved report:
`pnpm --dir web exec playwright show-report ui-artifacts/report`.
The suite also checks browser-requested assets, including `/favicon.ico`; console errors are not
filtered out. The live service suite is the acceptance run.

See [operations](OPERATIONS.md) for service activation and mobile voice checks. Browser artifacts
contain the service's real data: share only deliberately fictional fixtures, never live task
conversations, session logs or private incident evidence.
