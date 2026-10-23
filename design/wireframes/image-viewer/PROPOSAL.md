# Image viewer: proposal

Status: proposed for the operator's checkpoint. The boards beside this file are written by `gen.py`
(`python3 design/wireframes/image-viewer/gen.py`); review captures stay in the ignored `captures/`.

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
whatever the theme, so pictures read as they are. The picture is fitted whole and centred. The page
behind is inert.

**Top bar**, white text: on phone over a dark band that fades out below it, on desktop a solid dark band (90%), so it
stays readable over a light picture:

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

**Position.** With more than one picture, a quiet pill **2 / 5** (12.5px, tabular figures, white 85% on
white 14%) sits centred 16 px above the bottom edge (plus the phone's safe area). It is the polite live
region that announces the new position.

**Previous and next.** Round 44 px buttons (48 px on desktop), white chevrons on dark grey at 78% with a faint
white ring, so they read over light and dark pictures, 8 px from the edge on phone and 24 px on desktop,
vertically centred; hidden at the ends.

**Showing and hiding the bars (phone).** A single tap on the picture or the dark around it hides the top
bar, the count and the arrows together (150 ms fade), and the picture keeps the full screen; another tap
brings them back. Focusing any control also brings them back, so a keyboard or screen reader never meets
an invisible control. On desktop the bars always show, and a click on the dark space outside the picture
closes the viewer, as the backdrop does today.

**Feedback.** **Image copied**, **Could not copy the image** and **Could not share the image** replace the
count pill for two seconds (`role="status"`). A cancelled share says nothing. Messages render inside the
viewer, because the app's toasts sit under the modal.

## Zoom

| Input | What happens |
| --- | --- |
| Pinch (touch, trackpad) | Zooms continuously around the point between the fingers, and pans with them. Past the limits it gives a little and settles back on release. |
| Double-tap / double-click | At fit: zooms toward the point touched to the picture's actual pixels, kept between 2× and 4× the fitted size. Zoomed: back to fit. |
| Wheel (desktop) | Zooms toward the pointer. |
| **Zoom image** / **Fit image** | Fit ↔ the double-tap level, around the centre. |
| Keys | `+` / `=` zoom in by 1.5×, `-` zoom out, `0` fit. |

- **Fit** shows the whole picture and never enlarges it past its own pixels, so a small picture shows at
  actual size. On phone the fit area is the whole screen (the bars float over it and can be hidden); on
  desktop it is the screen less the top bar (56 px), the count's band (56 px) and 88 px at each side, so
  the arrows never cover a fitted picture.
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
- The neighbour is already loaded when the finger arrives (see Reading ahead).

## Opening and closing

- **Opening** grows the picture from its thumbnail into place (200 ms) while the dark fades in.
- **Closing** with **×**, Escape or a swipe down shrinks the picture back to the current picture's
  thumbnail when it is on screen, otherwise fades (180 ms), then returns to the opening place in history.
  Browser Back closes at once.
- **Swipe down** (at fit, finger or mouse): the picture follows the finger, shrinking to 85% at most,
  and the dark fades with the distance. Released past 120 px or flicked down, it closes; otherwise it
  springs back. A swipe up does nothing.
- Focus returns to the thumbnail that opened the viewer, or to the same picture's thumbnail when a
  queued row has been replaced (unchanged, issue #461).

**Reduced motion.** No growing, sliding, gliding or springing: opening, closing and changing picture are
instant, and a released gesture lands at once. Pictures still follow the fingers while touched.

## Reading ahead

This is browser-held data, so it follows the
[caching rule](../../../AGENTS.md#working-rules-for-every-pr) and its review.

- **What is kept.** The viewer reads each picture itself, with one private request, into memory and shows
  it from there. Once the current picture has loaded it also reads the previous and next ones, so a swipe
  shows the neighbour under the finger. It holds at most three pictures: the current one and its two
  neighbours. Share, Copy and Download use the same bytes, so they need no second request and work at
  once.
- **For how long.** Only while the viewer is open. Moving on releases any picture more than one step away;
  closing, Back, leaving the page and a new scope release all of them. Nothing is written to storage:
  no Cache API, local storage, service worker or HTTP cache (the image API stays `no-store`).
- **What invalidates it, and why it cannot be stale.** Every picture is immutable at its address (a chat
  image by its id, shown files, PR pictures and captures as fixed copies), so a held picture is the
  picture at that address; nothing in the viewer acts on it beyond showing, sharing, copying or saving.
  A device whose access is withdrawn keeps seeing a picture it already read until the viewer closes, as
  it keeps a thumbnail already on screen; the next open reads again and shows the denied state. Task and
  project isolation are unchanged: a scope change closes the viewer.
- **Failures.** A neighbour that fails shows the failed state when reached; **Retry** reads only that
  picture again. A read is cancelled when its picture leaves the window.
- **Evidence.** The phone walkthrough counts the image reads: one per picture shown or neighbouring,
  none after close; the unit tests check that at most three object URLs are live and all are revoked.

## Actions

- **Download image** saves the picture under its name.
- **Open in new tab** (desktop) opens the picture's own address in a new tab, where the browser's own
  viewer and Save work. A phone gets Share instead: a Home Screen app opens tabs outside its own session.
- **Share image** (phone, where the browser can share files) opens the system share sheet with the
  picture as a file: Save Image, Copy, Messages, AirDrop. Where it cannot (some Android browsers),
  Download takes its place.
- **Copy image** (desktop): above.
- Pictures go only where the operator sends them: Altitude uploads nothing, and every read stays on the
  project's own image API.

## States

| State | Phone | Desktop |
| --- | --- | --- |
| Open | Picture fitted, bars, count, arrows | Picture fitted, bar with actions, count, arrows |
| Bars hidden | Picture only | — |
| Loading | A white spinner at the centre; bars, count, arrows and Close work; actions wait | Same |
| Failed | Broken-image icon, **Image unavailable**, **Retry** (bordered, white); arrows and Close work; Zoom, Copy, Download and Share are hidden | Same |
| Zoomed | **Fit image** pressed (desktop); panning; no swipe between pictures | Grab cursor |
| Swiping | Two pictures side by side under the finger | Mouse drag, same |
| Closing by swipe | Picture smaller, the page showing through | Same |
| Copied / not copied / not shared | Message in the count's place for 2 s | Same |

## Thumbnails

**Chat images** (a message's own pictures, sent or on their way) keep their names beneath, as the SPEC
rule says, but show the picture rather than a letterboxed card:

- **One picture**: at its own aspect, kept between 9:16 and 16:9, inside 240 × 280 px (phone 200 × 240),
  cropped to that range from the top. A phone screenshot shows as a tall picture, not a stamp.
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
  image**. DOM order follows the screen: phone Close, Share/Download, Previous, Next; desktop Zoom, Copy,
  Download, Open in new tab, Close, Previous, Next.
- Everything has a keyboard path: Escape, ← →, `+ - 0`, arrows to pan when zoomed, Tab through controls.
- Every phone control is at least 44 px; icon-only controls carry their names and tooltips.

## Directions considered

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
  open the viewer with their context. The retry-by-query-string in the viewer goes: a retry is a new read.
- SPEC §3.9.1 **Gallery** and its state rows, §3.6 image rows, §3.8.6's gallery name and the captures
  description; `IMAGE_INPUT.md`'s viewer sentence; WALKTHROUGH's picture paragraph gains a viewer capture.
- Tests: `Images.test.tsx` covers zoom levels, keyboard, swipe and swipe-down by synthetic pointer
  events, actions, reading ahead and focus return; `image-viewer.pw.ts` walks open, pinch and
  double-tap zoom, pan, swipe, swipe down and close at both widths, with named captures, and the existing
  image, shown-file and capture walkthroughs follow the new controls.
