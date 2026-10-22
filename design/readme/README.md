# README visuals

The README shows the built web app on desktop and phone together: an opening loop of the desktop
project view with the phone answering a decision in front of it, the "work at any altitude" drawing
of the coordinator, task agents and helpers, an animation of new tasks balanced across two engines, and the
"how it works" drawing of the pieces around altd, which the overview also shows.
[The walkthrough](../../docs/WALKTHROUGH.md) carries the longer scenario, full-size captures and the
feature pictures. The README's pictures are the same at every width; it is laid out for a computer screen first.

The captures come from an ordinary [browser walkthrough](../../docs/DEVELOPMENT.md#browser-walkthroughs):
`web/e2e/readme.pw.ts` against `web/e2e/readme-service.py`, a disposable service with real handlers and
storage seeded with an example Atlas project at working scale. It holds a project conversation,
twenty-three tasks in flight split between both engines (three of them for a search-index migration),
128 tasks finished this week, the compatibility owner's live session and a rollback-retention decision;
a second project, Harbor, fills the project list. Engine processes are fixtures and every message is authored
example text, never agent output or an operator's data. The app's components and CSS render unchanged.

The journey runs at both widths in every `make check`: it opens the project, the phone Work tab, the
compatibility task and its live session, sends a task message and checks that the composer clears, then
answers the decision with seven days from Needs you and checks that it leaves the list. A second test,
on the desktop run only, answers the same decision on a phone page while a desktop page shows the task
resume. Page errors and horizontal overflow fail the journey.

The feature pictures come from `web/e2e/readme-features.pw.ts` against `web/e2e/readme-features-service.py`, which
seeds the same Atlas example and adds seven states: the coordinator's latest reply offering a task, which the
journey's press creates through the coordinator's real verbs with a scripted engine; the synonyms editor owner's
design question with its document and a proposal review by the other engine (two fixed findings and one
alternative); the typo tolerance owner's open question beside its live session; the per-tenant limits pull request
held for the operator after an independent review with two fixed findings; a deploy command the billing owner hands
to the task's terminal after its signing work (a real shell with no profile and a fixed prompt); the sign-on task
paused by a machine problem until the coordinator repairs and resumes it; and improvement suggestions in an
Altitude project. The journey also runs at both widths in every `make check`.

## Reproduce

Render the captures from the committed candidate and compose them in a validation run:

```sh
alt task validate -- sh -c 'ALTITUDE_README_IMAGES="$VALIDATION_RESULTS/images" make ui-validate UI_ARGS="readme.pw.ts readme-features.pw.ts" && python3 design/readme/compose.py "$VALIDATION_RESULTS/images" "$VALIDATION_RESULTS/composed"'
```

The run's `images/` folder in the task's validation evidence receives `project`, `task` and `decision`
captures for each viewport, plus phone `work` and `session` captures, at 1440×900 and 390×844 with 2× pixel
density, the opening loop's `opening-*` frames, the feature states (`create`, `proposal`, `task-view`, `review`,
`terminal`, `heal` and `improve` captures), and `phone-walkthrough.webm`, a silent recording of the phone journey
that pauses briefly at each capture. No speech recognition or coding agent runs.

## Opening loop

The README opens with `docs/images/opening.webp`, a looping animated WebP of three moments from the
journey's second test: the phone's Needs you shows the backfill question, the recommended seven days is
chosen, and after sending, the phone clears while the desktop Work list, scrolled to its end, shows the
task back at work.
That test drives a desktop page and a phone page against the same service and checks each moment.

`design/readme/hero.html` holds the layout: the desktop view on a 2240×1360 canvas with the phone in
front of its sidebar. `design/readme/compose.py` fills it and writes the loop with the feature pictures into
the run's `composed/` folder. It renders the layouts with Playwright's Chromium headless shell under its own
sandbox, which the validation run provides, and encodes them with ffmpeg.

## Feature pictures

The walkthrough's [See the features](../../docs/WALKTHROUGH.md#see-the-features) shows eight features as
`docs/images/feature-*.webp`. The task view and the terminal, `feature-task.webp` and `feature-terminal.webp`,
are desktop and phone captures in the opening loop's layout on a 2060×1240 canvas; each other picture is two phone captures side by side, each cropped to its top or
bottom, on `design/readme/pair.html`'s 1720×1200 canvas. At phone widths each shows the one capture that
proves it, `feature-*-phone.webp` (the live session uses `session-phone.png`). The same `compose.py` run
writes them after the opening loop.

## Balanced quotas

The README's `docs/images/balance.svg` is a looping vector animation with no script, so GitHub plays it in
an image. Its meters fill with use and read "N% of week", as the app's engine meters do: each new task goes
to the engine marked "less used" and that meter fills, until a new week empties both. Its engine figures and
task names are illustrative, not a capture. `python3 design/readme/balance.py` writes it.

## Maintain

- Edit the service or the journey, render again and inspect every changed image. Reconcile the
  journey with the current interface and the approved [specification](../wireframes/SPEC.md).
- Copy only reviewed illustrations needed by README or the walkthrough from the run's `images/` and
  `composed/` folders into `docs/images/`,
  replacing the matching files. Keep routine renders and before/after review evidence outside Git
  under the [project UI rule](../../AGENTS.md#ui); do not add a gallery for each PR.
- Change the quota animation in `design/readme/balance.py` and write it again; never edit `balance.svg` by hand.
- Edit `docs/images/altitude.svg` and `docs/images/system.svg` directly. These standalone vectors use system
  fonts and contain no scripts or raster art; each is the same drawing at every width, so keep its type large
  enough to read in a desktop README column. Keep `system.svg`'s labels in step with the
  [architecture](../../docs/ARCHITECTURE.md) and its alt text in README and the overview in step with it.
- Keep image alt text in README and walkthrough. Keep desktop and phone app views visible together
  in README. Check GitHub's sanitized Markdown, image
  paths and anchors, then inspect both widths. Open the original images to review details that are
  small in a desktop README column.

Task token readings are absent from the example, so the app shows its unknown state. Current
decision behavior lives in the [decision boards and spec](../wireframes/CONVERSATION_FIRST.md).
