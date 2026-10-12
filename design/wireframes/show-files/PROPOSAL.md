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
- **How it is read** is one viewer page for both, with markdown rendered like GitHub.

Removed: the `--design-file` JSON selection (on `block` and `review run`), the `design/wireframes/`
path restriction, the 64 KiB text limit, and the separate markdown-lite document mode of the reply
renderer. The task-document reader (links to `.md` files in task folders) uses the same markdown
renderer as the viewer.

Validation-run captures (`alt task reply --capture RUN`) keep their own GIF page in this change: they
are task-folder evidence with playback details, not worktree files. Folding them into the viewer is a
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
  viewer marks its place. Remote images are never fetched, at publication or in the viewer.
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

## Conversation entry

An owner reply with shown files has one underlined link under its text:

- **View README.md** for a document (the document's file name),
- **View overview.png** for one image alone,
- **View 3 images** for several images without a document.

A question card in the conversation and in Needs you shows the same link for its files. The link is at
least 44 px tall on phone. It opens the viewer in the same tab. A reply without files has no link.

## Viewer

Route: `/projects/:name/tasks/:slug/view/:messageId` for a reply and
`/projects/:name/tasks/:slug/view/:questionId/:revision` for a question.

From top to bottom:

1. **Back.** Desktop: **← Back** above the heading. Phone: the header's back control. Back returns to
   the place the viewer was opened from (the conversation at that reply or question, or Needs you)
   with any unsent reply or answer still in place. Without app history (a pasted link or a new tab)
   Back opens the task conversation.
2. **Heading.** The title (the link's name: `README.md`, `overview.png`, `3 images`) as the page
   heading. Below it one meta line: the document's worktree path when it differs from the title
   (`docs/CLI.md`), then **shown 2 hours ago** (exact time on hover), joined by ` · `. The path wraps
   at any character on phone.
3. **Earlier version.** When a later reply in this task showed the same document (the same worktree
   path), a status line reads **Earlier version.** followed by the link **Open latest**. A replaced question
   keeps today's **Earlier preview.** with **Open current question**.
4. **Images the document does not place.** Each image named with `--show` that the document does not
   embed appears as a figure: its file name as the caption, **Full size ↗** at the caption's end, and
   the image narrowed to the page. Selecting the image or Full size opens the saved image in a new
   browser tab for native zoom.
5. **The document.** A markdown document renders as GitHub does: headings, paragraphs, emphasis,
   lists and task lists, block quotes, tables, fenced code, horizontal rules, links, inline images and
   GitHub's safe HTML subset (for example `<p align="center">`, `<details>`, `<img>`). Scripts,
   styles, event attributes, forms and frames never render. A **Raw** toggle above the document
   switches to the exact source text and back. A `.txt` document shows as wrapped monospaced text
   with no toggle.

Inside the document:

- A heading link (`#install`) scrolls within the page.
- A web link opens in a new tab.
- A relative link to another file reads as plain text with its target as the tooltip; only shown
  files are reachable.
- An embedded image that was copied renders at its natural width, narrowed to the page, with an
  underlined **Full size ↗** link under its right edge (a 44 px target on phone) that opens the saved
  image in a new tab. The image itself is not a link. A copied image inside a link (`[![a](x.png)](url)`)
  keeps the link on the image as GitHub does and has no Full size link.
- A remote image is not loaded: its alt text (or its host when there is no alt text) is an underlined
  link that opens, in a new tab, the link around the image when it has one (a badge's page), else the
  image itself.
- A local image that was not copied shows a muted outlined box with a crossed-out image icon:
  **Image not shown · path**.
- Wide tables and code blocks scroll sideways inside their own block on phone; the page never
  scrolls sideways, and the block's own scrollbar shows that more is to the side. Code blocks have no
  Copy or terminal action.

**Raw**, **Retry** and **Retry image** are bordered buttons (Raw takes the accent tint while pressed),
at least 44 px tall on phone. The phone header keeps the task title and state with its back chevron,
and the tab bar stays.

The page has no composer, microphone, approval control or banner. Replying happens in the
conversation after Back.

## States (phone and desktop)

| State | What shows |
| --- | --- |
| Loading | **Loading preview…** with the spinner; no heading. |
| Ready, document only | Heading, meta line, Raw toggle, rendered document. |
| Ready, images only | Heading, meta line, one figure per image; no Raw toggle. |
| Ready, document and extra images | Heading, meta line, figures, then Raw toggle and document. |
| Raw on | The toggle is pressed; the source text replaces the rendering; scroll position resets to the document's top. |
| Image loading | The figure shows **Loading image…**; its Full size link is hidden. |
| Image failed | **Image unavailable.** with **Retry image**; the figure's image and Full size are hidden. Retry returns to loading. |
| Inline image not shown | Outlined box **Image not shown · path** in the image's place. |
| Earlier version | **Earlier version.** **Open latest** under the meta line. |
| Unavailable | Heading **Preview unavailable**; text "This saved preview could not be loaded. Return to the conversation for an update, or try again."; **Retry**; Back. |
| Denied | Heading **Preview unavailable**; text "Access to this preview is unavailable. Retry after access is restored."; **Retry**; Back. |

The viewer replaces today's **Design unavailable** with **Preview unavailable**, **Loading screenshot…**
with **Loading image…** and **Screenshot unavailable** / **Retry screenshot** with **Image
unavailable.** / **Retry image**, since the same page now shows any image.

## Rendering boundary

The viewer renders a markdown document in the browser with one fixed pipeline: parse markdown with
GitHub extensions (`remark-gfm`); convert to HTML nodes keeping embedded HTML; parse that HTML
(`rehype-raw`); sanitize with GitHub's schema (`rehype-sanitize`), further narrowed: no `srcset`,
`<picture>`, `<source>`, `<video>`, `<audio>`, `<iframe>`, `<form>`, `<input>` other than task-list
checkboxes (disabled), `style` or event attributes; then render React elements. After sanitizing, each
`<img>` source is resolved with the same rule as publication. A source naming a copied image becomes
that image's saved address on this connection; a remote source becomes the alt-text link described
above; anything else becomes the **Image not shown** box. No other image source survives, so the page
can load only saved images. Link targets keep `http:`, `https:`, `mailto:` and `#fragment`; other
schemes render as plain text. The renderer loads only with the viewer and the task-document reader.

Acceptance includes an adversarial fixture document, rendered on phone and desktop in both the viewer
and the task-document reader: `<script>`, `onerror`, `javascript:` links, `style`, `srcset`,
`data:` and protocol-relative images, a remote badge, `<iframe>` and `<form>`. The walkthrough fails
on any request leaving the Altitude origin or any executed script.

## Fixed copies and access

- Publication copies the exact bytes. The document and images are saved by content hash in the task
  folder; the reply or question records only their names, paths and sizes, so a 1 MiB document never
  enters the conversation record, L3's context or a review's captured context. Later worktree edits,
  commits or cleanup do not change what the operator sees.
- Each publication is its own version: a reply's link always opens that reply's copy, and a newer
  reply showing the same document marks the older one **Earlier version**.
- The viewer's data and images are served only to paired devices, like the rest of the task. Images
  are served with `nosniff` and a sandboxing content policy. Missing or altered saved bytes show
  **Preview unavailable**; nothing else is ever served at that address.
- Viewing records nothing. Approval still happens through the question's own controls.

## Earlier previews

Design previews published before this change keep their question text, answers and approvals, but
their **View** link opens **Preview unavailable**: they hold the text inside the question record, a
shape the new viewer does not read. Keeping them readable would need a permanent second reader for
the old shape, which the project's deletion-first rule excludes. On this machine they are about a
hundred question revisions, all decided except one open question in a running task; an owner whose
open question carries an old preview republishes it with `--show`, which advances its revision.

## What the operator decides

1. One mechanism (the design preview widened and its JSON selection removed) rather than a separate
   show verb beside it.
2. Markdown rendered like GitHub with its safe HTML subset, using a widely used open-source renderer
   loaded only by the viewer, instead of extending Altitude's own small reply formatter.
3. Same-tab viewer with Back, matching today's preview, rather than a new tab.
4. Earlier design previews become unavailable rather than keeping a reader for their old format.
