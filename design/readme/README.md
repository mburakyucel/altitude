# README visuals

The README shows the built web app on desktop and phone together: a full desktop project capture,
three phone views (project, task and decision), a phone recording and an editable role diagram.
[The walkthrough](../../docs/WALKTHROUGH.md) carries the longer scenario and full-size captures.
The desktop image stays visible on phones; it is not replaced with a phone screenshot.

The captures come from an ordinary [browser walkthrough](../../docs/DEVELOPMENT.md#browser-walkthroughs):
`web/e2e/readme.pw.ts` against `web/e2e/readme-service.py`, a disposable service with real handlers and
storage seeded with an example Atlas project. It holds a project conversation, three search-index
migration tasks, the compatibility owner's live session and a rollback-retention decision; a second
project, Harbor, fills the project list. Engine processes are fixtures and every message is authored
example text, never agent output or an operator's data. The app's components and CSS render unchanged.

The journey runs at both widths in every `make check`: it opens the project, the phone Work tab, the
compatibility task and its live session, sends a task message and checks that the composer clears, then
answers the decision with seven days from Needs you and checks that it leaves the list. Page errors and
horizontal overflow fail it.

## Reproduce

Render the captures from the committed candidate in a validation run:

```sh
alt task validate -- sh -c 'ALTITUDE_README_IMAGES="$VALIDATION_RESULTS/images" make ui-validate UI_ARGS=readme.pw.ts'
```

The run's `images/` folder in the task's validation evidence receives `project`, `task` and `decision`
captures for each viewport, plus phone `work` and `session` captures, at 1440×900 and 390×844 with 2× pixel
density, and `phone-walkthrough.webm`, a silent recording of the phone journey that pauses briefly at
each capture. No speech recognition or coding agent runs.

## Maintain

- Edit the service or the journey, render again and inspect every changed image. Reconcile the
  journey with the current interface and the approved [specification](../wireframes/SPEC.md).
- Copy only reviewed illustrations needed by README or the walkthrough into `docs/images/`,
  replacing the matching files. Keep routine renders and before/after review evidence outside Git
  under the [project UI rule](../../AGENTS.md#ui); do not add a gallery for each PR.
- Edit `docs/images/orchestration.svg` and `orchestration-phone.svg` directly. These standalone
  vectors contain no external fonts, scripts or raster art. Keep their relationships and text
  equivalent; the phone composition stacks independent owners for legibility.
- Keep responsive diagram `<picture>` sources and image alt text in README and walkthrough. Keep
  desktop and phone app views visible together in README. Check GitHub's sanitized Markdown, image
  paths and anchors, then inspect both widths. Open the original images to review details that are
  small in a desktop README column.

Task token readings are absent from the example, so the app shows its unknown state. Current
decision behavior lives in the [decision boards and spec](../wireframes/CONVERSATION_FIRST.md).
