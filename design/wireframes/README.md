# Altitude console — wireframes (draft)

Static design-canvas artboards for the Altitude console, one file per board (`<Board>.dc.html`, the
career-platform convention without `support.js`). They are **drafts for review, not the build
reference** — every board says so with its `draft` pill. The spec the build consumes stays
`web/design/tokens.css` and the code under `web/`.

This first-draft checkpoint contains three boards. These files are not approval to implement the
design. Further boards and visual review belong in future, explicitly selected work rather than an
active agent task.

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
  spacing scale, the atoms (card, pill, buttons, field, label) and the data colours with the principle
  that text never wears one. The legend for the set.
- **Inbox.dc.html** — L3's high-level desktop overview: work that needs Burak, running tasks with one
  named L2 owner each, optional help/review, PR and check state, and concise FYIs. The answer strip
  keeps a human decision to one row while Open task chat opens the readable conversation for direct
  steering.
- **DecisionCard.dc.html** — a 700px task card shown at rest, with its readable task chat open, and in
  blocked and running states. It keeps ownership and the current question visible while technical
  detail remains subordinate. A task may show optional L1 help or independent review when the L2
  chose it; neither is a required stage.

## Checkpoint limitations

The first visual review found unproven worst-case copy, atom and token drift, render-harness blind
spots, and density/readability concerns. Reassess those concerns if this draft is selected for more
work. This checkpoint does not include `canvas.json`, mobile boards, or a complete route set.
