# Images in project and task conversations

The operator approved this interaction and bounded storage/delivery policy on 2026-09-10. The shared
[composer specification](SPEC.md#36-composer) lists the interaction states; the maintained
application walkthrough is `web/e2e/image-input.pw.ts`. Review captures and the approved proposal
prototype stay outside Git. The shared compact mobile layout follows [SPEC §8](SPEC.md#8-compact-mobile-chat).

## Interaction

One Add images control shares the existing composer controls, alongside text, mic and Send on phone.
Native selection and desktop image-file
paste add screenshots/photos to one short preview strip; ordinary text paste is unchanged. Each
preview has a named 44px Remove target. Four previews fit at phone width, and removing the last
one removes the entire strip. Empty composers have no attachment row or permanent limits banner.
Unsent selection stays in the mounted conversation; leaving releases it, and late reads or uploads cannot
fill another project's/task's draft. Nothing is uploaded until Send.
Submitted captions share the existing text recovery across navigation and reload. Image bytes
and immutable image retries ordinarily remain in the mounted conversation; after leaving, check saved history
and reselect images if the message was not accepted.

The operator can send images alone or with typed/dictated text. Voice Cancel preserves current
edits and selected images; Stop appends the transcript; voice Send transcribes and sends them
together. Empty or failed transcription sends nothing, including selected images. No separate
transcript box appears. The image strip adds about one thumbnail's height to the composer.
An explicit voice Send captures its images before transcription. That operation completes for the
original project or task after route navigation; returning while it is pending shows its status.
It remains client-side within the current document, without an audio replay or persistent image queue.

Send shows a pending bubble and freezes that submission's text/image/microphone controls until
admission is known. Acceptance clears the selection and releases the composer before the agent
finishes. A confirmed refusal removes the pending bubble and restores the editable draft with the
reason. An uncertain response keeps the pending bubble and frozen submission; Retry uses the same
identity and content. Navigation remains available. No artificial percentage implies upload progress.

Saved thumbnails stay with their caption and open in a full-image modal on phone/desktop, with
Fit/Zoom, scrolling, Close/Escape and focus return. A viewer opened on a queued project image stays
open, with its zoom, while history admission replaces the queued row; closing it then focuses that
image's stored thumbnail, or its Retry or placeholder while the new row reads. Leaving the page
closes the viewer. Loading, missing and denied reads keep an
image-sized placeholder, readable message text and Retry that only repeats the read. A queued
project image retains its own caption and Remove until delivery starts. Unclaimed task image messages
offer the same Remove as text; removal replaces the caption and thumbnails with Message removed and
excludes that message from later delivery. Failed project-turn Retry
uses saved images; task recovery retains its existing authority. Archived conversations retain
viewing and remove the composer.

## Validation, storage and access

- Accept PNG, JPEG and static WebP: at most four images, 10 MiB each and 20 MiB total per message,
  25 megapixels and 8192 pixels per side, with a 28 MiB JSON envelope. Browser checks give early
  feedback; server content validation and bounded decoding remain authoritative.
- Reject animation, malformed/unsupported formats and oversized encoded or canonical output.
  Orient images correctly, remove metadata and normalize supported RGB ICC profiles to sRGB.
  Alpha is retained for PNG/WebP. HDR declarations, non-RGB ICC, profiles over 4 MiB and non-sRGB
  gamma/chromaticity without an ICC profile require an exported sRGB copy. Optional converter
  capability is detected and absence has an explicit unavailable state.
- Admission accepts the whole text/image set or refuses it. Client-rejected items disappear while
  other selections remain. Server rejection restores the full editable selection with its reason.
  No partially accepted message or file set appears. Original names are escaped display labels.
- Canonical files live under the owning project's private managed runtime, outside worktrees and
  static/design trees. Opaque IDs carry original operator message/task provenance, format,
  dimensions, size and integrity digest. No caller supplies a filesystem upload/download path.
- Reads require a registered owning project and committed conversation/task references. Responses
  use validated MIME, `no-store`, `nosniff` and same-origin resource policy. Missing/foreign/denied
  content returns bounded errors without filesystem details. Detailed diagnostics remain private.
- This uses existing private HTTP/network and OS-user access, with no new authentication,
  encryption-at-rest system, public hosting, GitHub image publication or infrastructure. The
  selected image reaches the chosen agent's provider as conversation input. Logs/traces/output
  artifacts are not automatically ingested.

Selection/removal before Send creates no server copy. Intermediate originals are discarded after
normalization. The project lock serializes file publication with durable message/queue admission.
Same-ID retries return the original receipt and cannot replace its content. Interrupted or cancelled
uploads with no durable reference are collected after 24 hours on the existing maintenance cadence,
including startup; cleanup failure retains files and does not stall unrelated work.

Committed images follow conversation retention without automatic expiry. They survive task archive,
worktree cleanup and project detach/reattach. A queued message's removal makes otherwise unreferenced
files eligible for cleanup, while a task assignment or retained conversation still protects them.
There is no per-image deletion or storage dashboard; retained storage grows with accepted messages.

## Delivery and verification

Neutral image references travel with the durable message through queue/history, task conversation,
pending delivery and resume claims/restoration. Image-bearing project rows run as separate turns;
retained claims recover interrupted delivery visibly without executing the same turn twice.
Native image inputs retain the selected engine, model and session. Capability is checked again at
delivery; missing content or unsupported transport never becomes text-only success or causes an
image-driven provider switch. Text-only live checkpoints use validated readable local locations
and explicit native visual-read instructions. Larger task resume batches keep every image available
through those readers. Bounded project session handoffs preserve sources and retrieval instructions.
Fresh task attempts also carry delivered image-message captions and references as historical context;
ordinary resume retains its existing pending-message behavior.

L3 gives its assigned L2 selected committed same-project images through repeatable
`alt task new/message --image <id>`. Assignments retain original provenance and actual image access.
The relay is L3-authored steering; it grants no new task authority, file lease or merge approval.

`make check` exercises real storage, API and state transitions with deterministic native engine
fixtures, including bytes/payloads, refusal, uncertainty, queue ordering, busy checkpoints,
resume restoration, handoff, isolation, archive and cleanup. The browser walkthrough drives all
shared image states at 390×844 and 1440×900 with named screenshots under `web/ui-artifacts/`.
Local CLI/schema inspection supports feasibility; live provider/model compatibility remains
unverified under the recorded 2026-09-08 testing decision. Phone emulation does not establish
physical device picker/clipboard behavior.

This delivers the operator image-input increment, including project chat, of
[altitude issue #230](https://github.com/mburakyucel/altitude/issues/230). Agent-produced evidence,
general downloadable deliverables and a results/report area remain outside scope and keep the
issue open. Shared components retain the approved compact mobile layout.
