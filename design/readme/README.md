# README visuals

The README combines an editable SVG explanation of project orchestration with captures of the
actual built web app. [The walkthrough](../../docs/WALKTHROUGH.md) carries the longer scenario.

`fixtures.mjs` contains fictional Atlas and Harbor projects: conversations, three search-index
migration tasks, a live transcript, decisions and engine readings. None comes from an operator's
installation. `render.mjs` serves only the local production build on an ephemeral loopback port
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

Captures go to `docs/images/` at 1440×900 and 390×844, 2× pixel density. Desktop shows project
conversation/work, task conversation/session, and decisions. Phone adds separate Work and Live
session captures. Both runs click through task/context links, send a fixture message, check its
appearance and composer clearing, and reject unexpected API requests, console errors and page
overflow. Supporting interaction captures stay in `/tmp/altitude-readme-evidence`;
`README_EVIDENCE_DIR` overrides that directory. These are fixture UI checks, not live engine or
clean-machine setup acceptance.

The frozen timestamp and fixtures keep the assets reproducible. The decisions capture is a
later snapshot: the backfill owner now waits for an operator retention decision. Project and
task captures show the earlier parallel-work snapshot; the phone project capture shows a recent
conversation excerpt beginning at a complete message. Quota numbers are illustrative.

## Maintain

- Edit `fixtures.mjs`, rerun the capture script, and inspect every changed image. Rebuild first
  when current main changes the UI; reconcile fixtures and walkthrough controls with the actual
  implementation and approved [specification](../wireframes/SPEC.md).
- Edit `docs/images/orchestration.svg` and `orchestration-phone.svg` directly. These standalone
  vectors contain no external fonts, scripts or raster art. Keep their relationships and text
  equivalent; the phone composition stacks independent owners for legibility.
- Keep responsive `<picture>` sources and image alt text in README and walkthrough. Check GitHub's
  sanitized Markdown, image paths and anchors, then inspect both widths. Open the original images
  to review details that are small in a desktop README column.

These captures describe the UI at `ddbad55` (merged #232). The richer decision page is approved
design but remains unmerged at this capture baseline. The current More context link opens the
task; Resume/Reject are lifecycle actions. No proposed UI is passed off as a screenshot.
