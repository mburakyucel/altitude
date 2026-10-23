# Image viewer: proposal

Status: approved by the operator on 2026-10-23 with the bars always showing, and folded into
[SPEC §3.9.3](../SPEC.md#393-image-viewer), which outranks this record. The boards beside this file are written by
`gen.py` (`python3 design/wireframes/image-viewer/gen.py`); review captures stay in the ignored `captures/`.

## What it is for

Pictures arrive everywhere in Altitude: screenshots the operator sends from the phone, an owner's shown
images and documents, a held PR's before/after pictures and a validation run's captures. Today they all
open in one plain modal: text buttons for one zoom step, no pinch or double-tap, a swipe that jumps with
no motion, no way to save or share, and task captures that do not open at all. The operator mostly looks
at screenshots on the phone, so the viewer should feel like the phone's own Photos app, and as finished
on desktop.

This keeps what works: one viewer for the whole app, opening adds one history entry so Back, Escape and
Close return to the exact place, focus goes back to the thumbnail, a failed picture offers Retry, and the
SPEC rules for pending and saved chat thumbnails stand.

## The viewer

**Stage.** The viewer covers the whole screen on phone and desktop, in near-black (`#0b0d12`, 97%)
whatever the theme, so pictures read as they are. The page behind is inert.

**Bars always show.** A top bar and a bottom band frame the picture, which fits whole in the space between
them, so the viewer's own controls never sit on a screenshot's header or composer and every control stays
where a finger, keyboard or screen reader expects it. While zoomed, the picture pans under the bars.

**Top bar**, a solid dark band (90%), white text:

| | Phone (390) | Desktop (1440) |
| --- | --- | --- |
| Left | **×** Close image (44 px) | Name over context |
| Middle | Name over context | — |
| Right | **Share** where the browser can share files, else **Download** (44 px) | **Zoom image**, **Copy image**, **Download image**, **Open in new tab**, a hairline divider, **×** Close image (40 px each) |

- **Name**: 15px, 600, one line, cut with an ellipsis. **Context**: 12.5px at 70% white, one line,
  ellipsis; it says where the picture came from, in the conversation's own words:
  - chat images: the sender and the time, **You · 3 min ago**, **L2 · 12:04**, **L3 · yesterday**
    (the time is the message's, as the conversation writes it);
  - an owner's shown images: **L2 · 3 min ago**, or the question's **Shown with a question**;
  - document images and a document's extra images: the document's name, **README.md**;
  - a held PR's pictures: the side, then the card, **Before · Pictures in this PR** (so the side
    survives a long name, as SPEC §3.8.6 requires);
  - captures: **Validation run 4**;
  - a picked image in the composer: **Not sent yet**.
- Desktop buttons are icons (magnifier plus/minus, two squares, down arrow into a tray, arrow out of a
  box, ×) with a tooltip equal to their accessible name. Zoom is a toggle: **Zoom image** with
  `aria-pressed="false"` at fit, **Fit image** when zoomed.
- **Copy image** appears only where the browser can write images to the clipboard, and not for GIF
  captures (a copy would hold only the first frame). It copies a PNG.
- A picked image (not sent yet) shows no actions: it is the operator's own file.

**Bottom band** (56 px plus the phone's safe area): with more than one picture, a quiet pill **2 / 5**
(12.5px, tabular figures, white 85% on white 14%) centred in it, the polite live region that announces
the new position. A separate `role="status"` node takes the pill's place for two seconds to say **Image
copied** (with a check, as copied receipts do), **Could not copy the image** or **Could not share the
image**; the count is not re-announced when it returns. A cancelled share says nothing. Messages render
inside the viewer, because the app's toasts sit under the modal.

**Previous and next.** Round 44 px buttons (48 px on desktop), white chevrons on dark grey at 78% with a
faint white ring, so they read over light and dark pictures, 8 px from the edge on phone and 24 px on
desktop, vertically centred; hidden at the ends.

## Gestures

One gesture owns each touch, decided by the first 10 px of movement:

| Starts as | At fit | Zoomed |
| --- | --- | --- |
| Mostly sideways | Swipe between pictures | Pan |
| Mostly downward | Swipe down to close | Pan |
| Mostly upward | Nothing | Pan |
| Two fingers | Pinch zoom | Pinch zoom |
| Tap | Nothing | Nothing |
| Second tap within 300 ms and 24 px | Zoom toward the point | Back to fit |

- A single tap does nothing, so nothing waits to tell it from a double-tap.
- **Desktop click** on the dark space outside the picture closes the viewer, as the backdrop does today,
  but not when it ends a drag (more than 6 px of movement) and never on the picture, where a
  double-click zooms.
- The viewer stops the browser's own pinch-zoom, scrolling and pull-to-refresh inside it
  (`touch-action: none` on the stage, `overscroll-behavior: contain`). Safari's edge swipe outside a Home
  Screen app goes Back, which closes the viewer as Back always does.

## Zoom

| Input | What happens |
| --- | --- |
| Pinch (touch, or a trackpad pinch) | Zooms continuously around the point between the fingers, and pans with them. Past the limits it gives a little and settles back on release. |
| Double-tap / double-click on the picture | At fit: zooms toward the point to the picture's actual pixels, kept between 2× and 4× the fitted size. Zoomed: back to fit. |
| Mouse wheel (desktop) | Zooms toward the pointer, one step per notch. A trackpad's two-finger scroll pans while zoomed and does nothing at fit; a wheel notch is told from it by its size (a line step, or a whole step of 50 px or more with no sideways part). |
| **Zoom image** / **Fit image** | Fit ↔ the double-tap level, around the centre. |
| Keys | `+` / `=` zoom in by 1.5×, `-` zoom out, `0` fit. |

- **Fit** shows the whole picture between the bars and never enlarges it past its own pixels, so a small
  picture shows at actual size. On desktop the fit area also keeps 88 px at each side, so the arrows never
  cover a fitted picture; on phone the arrows sit over its edges.
- **Most zoom** is the larger of 4× fit and 2× actual pixels.
- **Panning** while zoomed: drag with a finger or the mouse. Edges hold: a side of the picture never
  leaves its screen edge, and an axis that fits stays centred. A flick glides and slows to a stop. Arrow
  keys pan by 15% of the screen.
- Cursor (desktop): zoom-in over a fitted picture, grab and grabbing while zoomed.
- Changing picture, resizing the window or turning the phone returns to fit.

## Moving between pictures

- **Swipe** (at fit): the picture follows the finger and its neighbour slides in beside it with a 16 px
  gap. Releasing past a third of the width, or a quick flick, carries on to the neighbour (250 ms,
  ease-out); otherwise it springs back. At the first and last picture the strip resists (it moves a third
  as far) and springs back. A mouse drag does the same on desktop.
- **Zoomed**, one finger pans and never changes picture, as today.
- **Arrows**, ← and → keys (at fit; zoomed they pan) and Previous/Next slide to the neighbour (220 ms).
- The neighbour is already read when the finger arrives (see Reading ahead).

## Opening and closing

- **Opening** grows the thumbnail's own picture into place (200 ms) while the dark fades in, so nothing
  flashes; the viewer's full picture replaces it when its read finishes. A thumbnail still loading opens
  to the spinner.
- **Closing** with **×**, Escape or a swipe down shrinks the picture back to the current picture's
  thumbnail when it is on screen, otherwise fades (180 ms), then returns to the opening place in history.
  Browser Back closes at once.
- **Swipe down** (at fit, finger or mouse): the picture follows the finger, shrinking to 85% at most,
  and the dark and the bars fade with the distance. Released past 120 px or flicked down, it closes; otherwise it
  springs back.
- Focus returns to the thumbnail that opened the viewer, or to the same picture's thumbnail when a
  queued row has been replaced (unchanged, issue #461).

**Reduced motion.** No growing, sliding, gliding or springing: opening, closing and changing picture are
instant, and a released gesture lands at once. Pictures still follow the fingers while touched.

## Reading ahead

This is browser-held data, so it follows the
[caching rule](../../../AGENTS.md#working-rules-for-every-pr) and its review.

- **What is kept.** The viewer reads each picture itself, with one private request, into memory as
  encoded bytes, and shows it from there. It reads its own copy rather than borrowing a thumbnail's,
  because a thumbnail's row can be replaced while the viewer stays open (issue #461). Once the current
  picture has loaded it also reads the previous and next ones, so a swipe shows the neighbour under the
  finger. It holds at most three pictures. Only the current picture is decoded for display; a neighbour
  is decoded when a swipe starts, so even 25-megapixel pictures keep one full decode in memory at rest.
  Share, Copy and Download use the held bytes, so they need no second request.
- **For how long.** Only while the viewer is open. Moving on releases any picture more than one step away;
  closing, Back, leaving the page and a new scope release all of them. When the page is hidden (the app
  sent to the background, the page kept for Back-Forward), the neighbours are released and read again
  on return; the current picture stays so the screen is unchanged. Nothing is written to storage: no Cache
  API, local storage, service worker or HTTP cache (the image API stays `no-store`).
- **What invalidates it, and why it cannot be stale.** Every picture is immutable at its address (a chat
  image by its id, shown files, PR pictures and captures as fixed copies), so a held picture is the
  picture at that address; nothing in the viewer acts on it beyond showing, sharing, copying or saving.
  A device whose access is withdrawn, or that is paired again, keeps seeing, sharing and saving a picture
  it had already read until the viewer closes, exactly as it keeps a thumbnail already on screen; the
  next open reads again and shows the denied state. Task and project isolation are unchanged: a scope
  change closes the viewer.
- **Failures.** A neighbour that fails shows the failed state when reached; **Retry** reads only that
  picture again. A read is cancelled when its picture leaves the window.
- **Evidence.** The phone walkthrough counts image reads (one per picture shown or neighbouring, none
  after close, neighbours again after the page is hidden and shown) and opens an 8192-pixel picture; the
  unit tests check that at most three pictures are held and every object URL is revoked.

## Actions

- Actions need the picture's bytes in hand, so while it loads they show disabled (dimmed, not removed);
  a failed picture hides them.
- **Download image** saves the held bytes under the picture's name.
- **Open in new tab** (desktop) opens the picture's own address in a new tab, where the browser's own
  viewer and Save work. A phone gets Share instead: a Home Screen app opens tabs outside its own session.
- **Share image** (phone, where the browser can share files) opens the system share sheet with the
  picture as a file built at the press from the held bytes, so the press keeps its user activation:
  Save Image, Copy, Messages, AirDrop. Where it cannot (some Android browsers), Download takes its place.
- **Copy image** (desktop) writes a PNG; a JPEG or WebP is converted on the way, inside the clipboard
  item's promise, so Safari accepts it.
- Pictures go only where the operator sends them: Altitude uploads nothing, and every read stays on the
  project's own image API.

## States

| State | Phone | Desktop |
| --- | --- | --- |
| Open | Picture fitted between the bars, count, arrows | Picture fitted, bar with actions, count, arrows |
| Loading | The thumbnail's picture, or a white spinner; bars, count, arrows and Close work; Share or Download disabled | Same; Zoom, Copy, Download and Open disabled |
| Failed | Broken-image icon, **Image unavailable**, **Retry** (bordered, white); arrows and Close work; Share or Download hidden | Same; Zoom, Copy, Download and Open hidden |
| Zoomed | Panning under the bars; no swipe between pictures | **Fit image** pressed; grab cursor |
| Swiping | Two pictures side by side under the finger | Mouse drag, same |
| Closing by swipe | Picture smaller, the page showing through | Same |
| Copied / not copied / not shared | Message in the count's place for 2 s | Same |

## Thumbnails

**Chat images** (a message's own pictures, sent or on their way) keep their names beneath, as the SPEC
rule says, but show the picture rather than a letterboxed card:

- **One picture**: at its own aspect, kept between 9:16 and 16:9, inside 240 × 280 px (phone 200 × 240),
  cropped to that range from the top. A phone screenshot shows as a tall picture, not a stamp.
- The shape comes from the picture's size: a saved image's recorded width and height, and for a picked
  or sending picture the size read when it was picked, so Sending, the held picture and the saved
  thumbnail share one layout and nothing jumps.
- **Two to four**: tiles 112 px tall (phone 96), each at its own aspect between 3:4 and 4:3, wrapping;
  four on a phone make a 2 × 2 grid.
- Name: 12px muted, one line under the tile, cut to its width.
- **Loading**: a flat grey block at the tile's size; the name already beneath; "Loading image…" for
  screen readers only. No layout jump when it arrives.
- **Failed**: the tile, bordered, with a broken-image icon, the reason on one line (**Access denied**,
  **Image unavailable**, **Could not load**, `role="alert"`) and a **Retry** pill with a 44 px target.

**An owner's shown images** keep their row of up to three tiles plus **+N**, at 96/120 px. Their failed
tile changes to the chat tile's (icon, **Image unavailable**, **Retry**), so every failed thumbnail reads
the same.

**Every thumbnail and document picture** shares one press state (dims to 85% and scales to 98%) and one
focus ring, and opens the viewer from where it sits.

**Document and PR pictures** keep their expand badge; the badge is now the same on documents, PR frames
and captures (28 px, rounded 8, dark at 62%, white expand arrows).

**Task captures** open in the viewer too: each GIF in the captures page is a button ("Open capture
Desktop send") with the expand badge, opening the run's captures as one set at that capture, context
**Validation run 4**. While loading, a grey block at the capture's saved size (no jump); on failure the
document's failed box: broken-image icon, **Capture unavailable**, **Retry**.

**Composer**: tapping a picked image's thumbnail opens it in the viewer (context **Not sent yet**, no
actions), so a phone can check the screenshot before sending. The remove × stays its own button.

## Accessibility

- The viewer is a modal dialog named after the picture ("Image export.png"); focus starts on **Close
  image**. No control is ever hidden while the picture shows, so swipe navigation in a screen reader
  reaches them all. DOM order follows the screen: phone Close, Share/Download, Previous, Next; desktop Zoom, Copy,
  Download, Open in new tab, Close, Previous, Next.
- Everything has a keyboard path: Escape, ← →, `+ - 0`, arrows to pan when zoomed, Tab through controls.
- Every phone control is at least 44 px; icon-only controls carry their names and tooltips.

## Directions considered

- **Immersive phone viewer (tap hides the bars).** The picture uses the whole screen and a tap hides the
  bars, as Photos does. Gives a phone screenshot about 15% more height, but a single tap must wait about
  300 ms to be told from a double-tap, the viewer's bar sits on the screenshot's own header until hidden,
  and hidden controls need extra care for screen readers. Pinch and double-tap already give full-screen
  detail.
- **Two deliveries.** The viewer, zoom, actions and thumbnails first; the finger-following swipe, reading
  ahead and the grow/shrink transitions second. Smaller steps and a smaller first caching review, but
  the first step's swipe jumps without the neighbour, which feels unfinished.

- **Keep the card dialog, add gestures.** Least change, but a bordered card on the phone wastes the
  edges and looks unlike the phone's own viewer.
- **A gallery library (PhotoSwipe).** Mature gestures, but it owns its own DOM, history and failure
  states, so the app's history entry, focus return, Retry and private reads would be rebuilt around it.
  The gestures here are a few well-known calculations kept in one component.
- **A filmstrip of thumbnails under the picture (desktop).** Useful for long sets, but Altitude's sets are
  small (four chat images, a few shown files); the count is enough.

## What it changes

- `MessageImages.tsx` keeps the host and the chat thumbnails; the viewer moves to `ImageViewer.tsx`, with
  its own reads, zoom, swipe and actions. Openers pass the context line and carry `data-image`, so the
  viewer can grow from and shrink to them and return focus.
- `Shown.tsx`, `PrPictures.tsx`, `ShownReader.tsx`, `TaskCaptures.tsx` and the composer's picked images
  open the viewer with their context. A picked image's preview carries the width and height read when it
  was picked. The retry-by-query-string in the viewer goes: a retry is a new read.
- SPEC §3.9.1 **Gallery** and its state rows, §3.6 image rows, §3.8.6's gallery name and the captures
  description; `IMAGE_INPUT.md`'s viewer sentence; WALKTHROUGH's picture paragraph gains a viewer capture.
- Tests: `Images.test.tsx` covers zoom levels, keyboard, swipe and swipe-down by synthetic pointer
  events, actions, reading ahead and focus return; `image-viewer.pw.ts` walks open, pinch and
  double-tap zoom, pan, swipe, swipe down and close at both widths, with named captures, and the existing
  image, shown-file and capture walkthroughs follow the new controls.
