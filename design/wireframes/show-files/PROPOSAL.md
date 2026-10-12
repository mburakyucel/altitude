# Show files to the operator

An owner shows the operator files from its task worktree, rendered in Altitude, while the work is in
progress: a README draft as GitHub will show it, a changed doc, a screenshot. The operator opens it
from the task conversation on phone or desktop, reads it, and replies in the same conversation.
Showing is a read. It answers no question, releases no hold and changes no question state.

## One mechanism

The design preview becomes the general mechanism instead of gaining a sibling. Today it is bound to a
question, reads only `design/wireframes/`, takes a JSON selection, and shows one text plus screenshots.
After this change:

- **What is shown** is a fixed copy of named worktree files: at most one document (`.md` or `.txt`)
  and images (PNG, JPEG, GIF or WebP). A markdown document's own relative images inside the worktree
  are copied with it, so they render where the document places them.
- **Where it is attached** is either an owner reply (`alt task reply --show PATH`) or a question
  (`alt task block --show PATH`). The proposal review takes the same `--show PATH` list
  (`alt task review run --subject proposal --show PATH`), so the reviewer reads what the operator sees.
- **How it is read** is one reader for both, with markdown rendered like GitHub.

Removed: the `--design-file` JSON selection (on `block` and `review run`), the `design/wireframes/`
path restriction, the 64 KiB text limit, and the separate markdown-lite document mode of the reply
renderer. The task-document reader (links to `.md` files in task folders) uses the same markdown
renderer as the reader.

Validation-run captures (`alt task reply --capture RUN`) keep their own GIF page in this change: they
are task-folder evidence with playback details, not worktree files. Folding them into the reader is a
possible later step.

## Owner command

```sh
alt task reply "$ALTITUDE_TASK" --show README.md --show docs/images/overview.png - <<'EOF'
The README draft is ready to read. It now opens with the two-minute tour.
EOF
```

- `--show PATH` repeats. Paths are relative to the task worktree, anywhere inside it.
- One document at most: nonempty UTF-8 `.md` or `.txt`, up to 1 MiB.
- Up to 24 images, 8 MiB each, 32 MiB together, counting the document's own images.
  Image bytes must match their type (PNG, JPEG, GIF, WebP); SVG, HTML, scripts and styles are refused.
- A `--show` path is written plainly: `..`, `.`, empty parts, absolute paths, symlinks anywhere on the
  path, special files and files outside the worktree are refused, as today. Every read walks from the
  worktree root by open directory handles without following links, as the design preview does.
- A markdown document's images are found from `![alt](path)`, reference definitions
  (`[id]: path`) and HTML `<img src="path">`. Each reference is percent-decoded, its `?query` and
  `#fragment` dropped, then resolved: a relative path from the document's folder (`../images/a.png`
  from `docs/guide.md` is `images/a.png`), a path starting with one `/` from the worktree root. The
  normalized result must stay inside the worktree; it is then read by the same no-link walk.
  References with a scheme (`https:`, `data:`, `file:` …), protocol-relative `//host/…` paths, and
  references that escape the worktree are not copied. A local image that is missing, escapes, or has
  an unsupported type does not stop publication; the command lists it under `not_shown` and the
  reader marks its place. Remote images are never fetched, at publication or in the reader.
- Only the current owner attaches files, only on replies to the operator (not `--to l3`).
- The reply's output includes `view_url`. Inputs may be untracked; publication stages nothing.

On a question, `--show` replaces `--design-file` and keeps today's design-question rules: one
question, current owner only; a changed file set advances the question revision and an identical one
keeps it; answers bind to the revision whose files the operator saw; a first acceptance refuses missing
or altered saved files; and the question rests on a proposal review (or a recorded review skip), as a
design question does today. Files that only illustrate go on a reply, which carries none of these
rules. A design
spec embeds its screenshots inline (`![Phone, empty state](captures/phone-empty.png)`), so each
screenshot appears beside the text that describes it.

## In the conversation

Shown files appear as what they are, under the owner's reply text and inside a question card (in the
conversation and in Needs you). There is no text link.

**Images only.** Thumbnails in one row, the way the operator's own photos appear in chat: 96 px tall
on phone, 120 px on desktop, rounded, each cropped to its own aspect up to 4:3. At most three show;
a fourth tile reads **+N** over a dimmed thumbnail. Tapping any tile opens the gallery at that image
(the **+N** tile at the fourth).

**Gallery.** Altitude's existing full-screen image viewer gains previous and next: arrow buttons at
the sides on desktop (and the keyboard arrows), a horizontal swipe and subtle side chevrons on phone, and a quiet **2 / 5**
count in its header. It shows the images of one shown set in order (extra images first, then the
document's copied images in document order); the operator's own chat images use the same viewer
with their message's images. Opening it adds one history entry: Back, Escape or **Close image**
returns to the exact place it was opened from (the conversation or the reader at the same scroll
position), and the draft is untouched.

**A document** (with or without images) is one page card, as wide as a reply (full width on phone,
at most 420 px on desktop), bordered and with a press state:

- a cover strip, 120 px tall, cropped from the document's first copied image (or the first shown
  image) when there is one;
- a document icon and the file name in bold (`README.md`), and a chevron at the right edge;
- two muted lines: the document's first heading, an em dash, then its first paragraph as plain text,
  cut with an ellipsis.

The whole card is one control (accessible name "Open README.md"). The heading and excerpt are taken
at publication, so the card needs no download.

**Versions.** When a later reply in the task shows the same document (same worktree path), the older
card's cover turns greyscale at reduced opacity and gains a small **Earlier** chip in its top-right
corner; its name, excerpt and chevron keep full contrast, because it still opens. The newest card
has no chip.

## Reader

Tapping a card opens the reader. Its address is
`/projects/:name/tasks/:slug/view/:messageId` for a reply and
`/projects/:name/tasks/:slug/view/:questionId/:revision` for a question; the task page stays mounted
underneath, so its scroll position and unsent draft are untouched.

**Desktop** (a main area at least 960 px wide): the reader is a panel on the right of the task page,
about 55% of the width, beside the conversation, which narrows but stays live: the operator reads and
types a reply at the same time. Opening another card swaps the panel's content; the card whose document is open carries an accent border. The panel header holds
the file name, a **Newer** pill when a later version exists, and a close **×**. Escape or × closes
it. Opened from Needs you, the task page opens with the panel already showing.

**Phone** (and narrow desktop windows): the reader covers the screen. Its header holds a close **×**
on the left, the file name, and the **Newer** pill on the right; the tab bar is hidden while reading.
Close, the browser's Back or the system back gesture returns to the conversation at the same place,
with the draft intact.

**Newer** opens the latest version in place (replacing the history entry). Opened by a pasted link or
in a new tab, with no app history behind it, close and Back open the task conversation. There is no meta line, no
path line and no Raw toggle: the reader is the document.

Content, top to bottom:

1. Images named with `--show` that the document does not embed, each full width with rounded
   corners. Tapping one opens the gallery at that image.
2. The document, rendered as GitHub does: headings, paragraphs, emphasis, lists and task lists,
   block quotes, tables, fenced code, horizontal rules, links, inline images and GitHub's safe HTML
   subset (for example `<p align="center">`, `<details>`, `<img>`). A `.txt` document shows as wrapped
   monospaced text.

Inside the document:

- A copied image renders at its natural width, narrowed to the column, with a small expand icon in
  its bottom-right corner; the image is a button (accessible name "Open image overview.png") that
  opens the gallery at that image. Desktop shows the zoom-in cursor. A copied image inside a link
  keeps the link, as GitHub does, and has no expand icon.
- A remote image is not loaded: a small image icon and its alt text (or its host) form an underlined
  link to the link around it (a badge's page) or else to the image, in a new tab.
- A local image that was not copied is an outlined box with a broken-image icon and its path in
  muted monospace.
- A heading link (`#install`) scrolls within the reader; a web link opens in a new tab; a relative
  link to another file is plain text with its target as the tooltip.
- Wide tables and code scroll sideways inside their own block; the reader never scrolls sideways.
  Code blocks have no Copy or terminal action.

## States (phone and desktop)

| Where | State | What shows |
| --- | --- | --- |
| Card | Ready | Cover, icon, name, chevron, excerpt. |
| Card | Cover or thumbnail loading | A flat grey block of the same size; no text. |
| Card | Cover failed | The card shows without its cover strip; it still opens. |
| Card | Thumbnail failed | The tile shows a broken-image icon; the tile is still the one control, and tapping it retries the load (accessible name "Retry image overview.png"). No control sits inside another. |
| Card | Earlier | Greyscale, faded cover with the **Earlier** chip; name, excerpt and chevron at full contrast. |
| Card | Saved files missing | The card stays, without cover or chevron, with a broken-document icon; it is not a control. Its excerpt line reads **Preview unavailable**. |
| Reader | Loading | A skeleton of the page: a heading bar, five text lines and an image block; static under reduced motion. Accessible name "Loading README.md". |
| Reader | Ready | Header and document as above. |
| Reader | Image loading | A grey block at the image's saved size. |
| Reader | Image failed | An outlined box with a broken-image icon, **Image unavailable** and a bordered **Retry**. |
| Reader | Earlier | **Newer** pill in the header. |
| Gallery | Open | Image fitted to the screen, **2 / 5**, Zoom image, Close image, previous and next (hidden at the ends). |
| Reader | Unavailable | A broken-document icon, **Preview unavailable**, "It could not be loaded. Try again, or ask in the conversation.", a bordered **Retry**; the header's × still closes. |
| Reader | Denied | A lock icon, **Preview unavailable**, "This device has no access right now.", a bordered **Retry**. |

Saved files missing for a card is known from the conversation data (the server checks the saved files
when it lists the reply), so the operator never opens a dead preview. This also covers previews
published before this change.

The desktop panel shows the same reader states inside the panel, with the conversation beside it
unchanged. Every control on phone has a target of at least 44 px.

The page has no approval control or banner; the question card keeps its own controls.

## Rendering boundary

The reader renders a markdown document in the browser with one fixed pipeline: parse markdown with
GitHub extensions (`remark-gfm`); convert to HTML nodes keeping embedded HTML; parse that HTML
(`rehype-raw`); sanitize with GitHub's schema (`rehype-sanitize`), further narrowed: no `srcset`,
`<picture>`, `<source>`, `<video>`, `<audio>`, `<iframe>`, `<form>`, `<input>` other than task-list
checkboxes (disabled), `style` or event attributes; then render React elements. After sanitizing, each
`<img>` source is resolved with the same rule as publication. A source naming a copied image becomes
that image's saved address on this connection; a remote source becomes the alt-text link described
above; anything else becomes the **Image not shown** box. No other image source survives, so the page
can load only saved images. Link targets keep `http:`, `https:`, `mailto:` and `#fragment`; other
schemes render as plain text. The renderer loads only with the reader and the task-document reader.

Acceptance includes an adversarial fixture document, rendered on phone and desktop in both the reader
and the task-document reader: `<script>`, `onerror`, `javascript:` links, `style`, `srcset`,
`data:` and protocol-relative images, a remote badge, `<iframe>` and `<form>`. The walkthrough fails
on any request leaving the Altitude origin or any executed script.

## Fixed copies and access

- Publication copies the exact bytes. The document and images are saved by content hash in the task
  folder; the reply or question records only their names, paths and sizes, so a 1 MiB document never
  enters the conversation record, L3's context or a review's captured context. Later worktree edits,
  commits or cleanup do not change what the operator sees.
- Each publication is its own version: a card always opens its own copy, and a newer reply showing
  the same document marks the older card **Earlier**.
- The reader's data and images are served only to paired devices, like the rest of the task. Images
  are served with `nosniff` and a sandboxing content policy. Missing or altered saved bytes show
  **Preview unavailable**; nothing else is ever served at that address.
- Viewing records nothing. Approval still happens through the question's own controls.

## Earlier previews

Design previews published before this change keep their question text, answers and approvals. Their
card shows **Preview unavailable** and does not open: they hold the text inside the question record, a
shape the new reader does not read. Keeping them readable would need a permanent second reader for
the old shape, which the project's deletion-first rule excludes. On this machine they are about a
hundred question revisions, all decided except one open question in a running task; an owner whose
open question carries an old preview republishes it with `--show`, which advances its revision.

## What the operator decides

1. Visual attachments in the conversation (thumbnails and a page card) that open a reader: a panel
   beside the conversation on desktop, full screen on phone.
2. One mechanism: the design preview widened, its JSON selection removed.
3. Markdown rendered like GitHub with its safe HTML subset, using a widely used open-source renderer
   loaded only with the reader.
4. Earlier design previews show **Preview unavailable** rather than keeping a reader for their old
   format.
