# Altitude console — wireframes (draft)

Static design-canvas artboards for the Altitude console, one file per board (`<Board>.dc.html`, the
career-platform convention without `support.js`). They are **drafts for review, not the build
reference** — every board says so with its `draft` pill. The spec the build consumes stays
`web/design/tokens.css` and the code under `web/`.

This preserved checkpoint contains **3 of the 12 planned boards**. The originating task is paused;
these files are not approval to implement the design. The remaining nine boards and the findings
from the first visual review are deferred rather than kept alive as agent tasks.

The boards derive their colours, type scale, radii, target size, sidebar width, drawer width, and
spacing scale from `web/design/tokens.css`. Layout and annotation dimensions are board-local, and
the deliberate value exceptions are listed below; this checkpoint has not had final token-
conformance review. Typefaces are IBM Plex Sans (UI) and IBM Plex Mono (ids, timers, quotes, and
wireframe callouts), loaded from Google Fonts inside each board's `<helmet>`. There is no shared
stylesheet, build step, or JavaScript, but a faithful render still depends on those web fonts.

## Render

    design/wireframes/shots.sh

renders every `*.dc.html` to `design/wireframes/shots/<Board>.png` with the machine's Chrome
(`/usr/bin/google-chrome`; override with `CHROME=/path`). Desktop boards render at 1440×900; a board
whose file name starts with `Mobile` renders at 390×844. `shots/` is gitignored — look at the PNGs,
do not commit them. The script only proves that Chrome wrote a non-empty PNG. It does not certify
font loading, a clean browser console, token fidelity, accessibility, or absence of visual clipping.

## Value exceptions

- `rgb(220 38 38 / 0.4)` — the blocked card's border: `--danger` at 40 %, the same `border-danger/40`
  the build draws (Inbox).
- `#0f172a` as a fill with `#ffffff` text — the tooltip drawn on the DecisionCard board. The values
  are `--text-primary` and `--surface`; the roles are borrowed because a native tooltip cannot be
  drawn on a static board.
- `opacity: 0.6` on a disabled button — the build's `.btn:disabled` (`web/src/styles.css`), not a
  token.
- Wireframe callouts (the rail, the captions) are `--accent-text` in the mono face at `--text-meta`;
  the target-min box on the Brand board is a dashed `--accent` outline. Annotation, not UI.

## Boards

- **Brand.dc.html** — the token sheet: both palettes as labelled swatches with name and hex, the type
  scale at size with token, px and role, radii, the 44px target, the sidebar and drawer widths, the
  spacing scale, the atoms (card, pill, buttons, field, label) and the data colours with the rule
  that text never wears one. The legend for the set.
- **Inbox.dc.html** — the Inbox at desktop width inside the shell: three decisions (an ordinary
  proposal, a blocked task with the danger-tinted border, one from another project), then Running,
  then FYI. The one risk: the question wears the card-title role (17px semibold) and the task title
  recedes into a 13px eyebrow, so the three questions are the three heaviest lines on the screen;
  and the answer strip is one 44px row — Why, a single-line feedback field, the options — so the
  whole queue plus Running and FYI fit one screen. Why: the console's one job is answering, and the
  build's textarea-then-buttons stack spends 60px per card on a note that is usually not written.
- **DecisionCard.dc.html** — the card at 700px: at rest with each part called out on a rail beside
  it, with Why open (ids and file names live there, as links to the ledger), and the Revise pair —
  disabled with its tooltip until feedback is typed, live once it is. The one risk: the callouts sit
  off the card so the card on the board is exactly what ships, and the feedback rule is shown as a
  before/after pair instead of prose.

## Checkpoint limitations

The first visual review recorded 7 major and 5 minor findings. They include unproven worst-case card
copy, atom and token drift, deliberate layout differences that need clearer annotation, render-
harness blind spots, and several density/readability concerns. Those findings and the missing nine
boards should be reconsidered together after the system simplification review. This checkpoint does
not include `canvas.json`, the mobile boards, or a complete route set.
