# README visuals

The README shows the actual built web app on desktop and phone together: a full desktop project
capture, three phone views (project, task and decision), a phone recording and an editable role
diagram. [The walkthrough](../../docs/WALKTHROUGH.md) carries the longer scenario and full-size
captures. The desktop image stays visible on phones; it is not replaced with a phone screenshot.

`fixtures.mjs` contains fictional Atlas and Harbor projects: conversations, three search-index
migration tasks, a live transcript, a rollback-retention decision and engine readings. None comes
from an operator's installation. `render.mjs` serves only the local production build on an ephemeral loopback port
and intercepts API requests with those fixtures. It does not run altd, launch agents or read
service state. The app's components and CSS render unchanged. Fixture replies are authored
examples, not agent output or evidence of a completed migration.

## Reproduce

Use the Node/pnpm versions and browser installation in [development](../../docs/DEVELOPMENT.md).
From the repository root:

```sh
pnpm --dir web install --frozen-lockfile
pnpm --dir web build
PLAYWRIGHT_BROWSERS_PATH="${ALTITUDE_HOME:-$HOME/.altitude}/browsers" node design/readme/render.mjs
```

The script requires the matching bundled Chromium. It uses the repository harness's sandbox
setting and a writable Crashpad configuration directory. These are documentation assets;
there is no additional app route or build dependency.

Renders go to ignored `web/ui-artifacts/readme/images/` at 1440×900 and 390×844, 2× pixel density.
Desktop shows project conversation/work, task conversation/session and Needs you. Phone adds separate
Work and Live session captures because those panes are separate tabs. Both runs open a task, send a
fixture message, check its appearance and composer clearing, open the rollback decision, select
seven days and send that answer. They assert the submitted question/revision and option, then
check that the question leaves Needs you. This does not claim the blocked agent has resumed.
Unexpected API requests, console errors and page overflow fail the run.
Supporting interaction captures stay in ignored `web/ui-artifacts/readme/`;
`README_EVIDENCE_DIR` overrides that directory. These are fixture UI checks, not live engine or
clean-machine setup acceptance.

Playwright records the phone sequence to `images/phone-walkthrough.webm`, pausing briefly at each
capture for readability. It is a silent recording of the real UI with fixture API responses;
no speech recognition or coding agent runs. The randomly named recording stays in the ignored
`video/` folder. The named video is the maintained documentation asset linked from the README.

The frozen timestamp and fixtures keep the scenario reproducible. Project and task captures show
parallel work; the phone project capture shows a recent conversation excerpt beginning at a complete
message. Quota numbers are illustrative.

## Maintain

- Edit `fixtures.mjs`, rerun the capture script, and inspect every changed image. Rebuild first
  when current main changes the UI; reconcile fixtures and walkthrough controls with the actual
  implementation and approved [specification](../wireframes/SPEC.md).
- Copy only reviewed illustrations needed by README or the walkthrough into `docs/images/`,
  replacing the matching files. Keep routine renders and before/after review evidence outside Git
  under the [project UI rule](../../AGENTS.md#ui); do not add a gallery for each PR.
- Edit `docs/images/orchestration.svg` and `orchestration-phone.svg` directly. These standalone
  vectors contain no external fonts, scripts or raster art. Keep their relationships and text
  equivalent; the phone composition stacks independent owners for legibility.
- Keep responsive diagram `<picture>` sources and image alt text in README and walkthrough. Keep
  desktop and phone app views visible together in README. Check GitHub's
  sanitized Markdown, image paths and anchors, then inspect both widths. Open the original images
  to review details that are small in a desktop README column.

The curated app captures use application source at `b69f9e5` and the fixtures in this directory;
they explain project orchestration, direct task steering, decisions and the separate phone panes.
The two orchestration SVGs explain the same role relationships in wide and narrow layouts,
including optional L1 helpers under each L2 owner. Task token readings are absent from this
fixture, so the app shows its unknown state. Current decision behavior lives in
the [decision boards and spec](../wireframes/CONVERSATION_FIRST.md), with implementation captures
in the ordinary ignored browser artifacts.
