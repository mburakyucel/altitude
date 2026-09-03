# Wireframes for the simplified product

Burak approved this wireframe set on 2026-09-03 (#166). It records the accepted design direction;
implementation work still starts only with Burak's explicit go.

Every board is one static HTML file that shares `wireframes.css`, which imports the build's own
`web/design/tokens.css`. There is no build step and no script; the only network fetch is the IBM
Plex web font, with the token file's system fallback when it is offline. Nothing here touches
`web/`.

## What each board shows

Each route has a desktop board (1440×900) and an iPhone board (`Mobile*.html`). A DRAFT pill and
an annotation strip run across the top of every board: why the layout is the way it is, and what
the primary action is. Numbered callouts in the strip point at the same numbers on the board. The
strip and the callouts are annotation, not UI.

| Board | Files | What it shows | Flow |
| --- | --- | --- | --- |
| Inbox | `Inbox.html`, `MobileInbox.html` | Only what needs Burak: a question from L3 with its recommendation, and a green PR held for his review. FYIs from L3 sit below as a quiet list. | Answer a decision; open a held PR |
| Projects | `Projects.html`, `MobileProjects.html` | One card per managed project with what waits on Burak first, then the active work with state dots. Unmanaged folders wait below with Start L3. | See what is running; start L3 on a folder |
| Chat | `Chat.html`, `MobileChat.html` | The Burak↔L3 conversation as a script: plain short replies, no ids or paths. Server-triggered turns fold into one line. Send is primary; the engine pin sits beside it with Auto as default. | Ask L3 for work or an answer |
| Project page | `Project.html`, `MobileProject.html` | L3 in one sentence, then what needs Burak, then active work as task cards with owner, state, and PR. Folded sections for done work and links. | Follow one project; open a task |
| Task page, Conversation | `TaskConversation.html`, `MobileTaskConversation.html` | The header says who works on the task, for how long, and where its PR stands. Tabs separate the Burak↔L2 exchange from the worker's session. Stop and Reject are quiet. | Ask or steer the owning L2 |
| Task page, Live session | `TaskLive.html`, `MobileTaskLive.html` | The engine session as a Claude Code style transcript: tinted prompt blocks, the worker's prose, one compact row per tool call with its output folded beneath, thin separators for turns and Altitude's boundaries, subtle timestamps. Raw events stay one click away. | Watch the session under the task |
| Monitor | `Monitor.html`, `MobileMonitor.html` | Quota per engine in human terms: what is used, when it resets, and where new work goes, with the 70% reserve line drawn on each gauge. Sessions show context as room used and the point where it compacts. | Check headroom |
| Restart pending | `RestartPending.html`, `MobileRestartPending.html` | The Inbox route with the banner: what changed in words, and a Restart button that appears only once nothing runs. | Restart after a merge |

The desktop sidebar carries the Inbox count and a small engine readout on every route, so the
answer to "is anything waiting, and do we have quota" never needs a page change. On the phone the
same four routes are a tab bar; the Inbox tab carries the count.

## How to render

```
design/wireframes/shots.sh
```

The script renders every board with the Chrome on this machine (`CHROME=/path/to/chrome` overrides
it) into `design/wireframes/shots/`, which is gitignored. Desktop boards render at 1440×900: a 64px
strip above an 836px app. Mobile boards render at 390×960: a 116px strip above an exact 390×844
iPhone screen with its status bar, Dynamic Island, curved corners, and the 34px home area. The
brief's 390×844 is that phone screen; the strip sits outside it so annotation never takes phone
pixels. Open any `.html` file in a browser to read it without rendering.

## Values reused and where the boards depart from them

The boards take their type scale, radii, colours, spacing, the 44px target minimum, and the 232px
sidebar from `web/design/tokens.css`, light theme only. Deliberate departures:

- **White on the accent.** Filled buttons, the DRAFT pill, and callouts use `#fff`, as the build's
  `.btn-primary` does; the token file has no on-accent colour.
- **Sentence-case section headings.** The build's `.label` is 11px uppercase. The boards use 14px
  semibold sentence case for section heads ("Needs you", "Active work") and keep uppercase only for
  the DRAFT pill and count badges. Chip and tab labels are 13px, the token `--text-meta`, not the
  11px label size.
- **Amber for "held for your review".** The review card's rule, its chip, and the `held` state dot
  borrow `--data-claimed` and the `chip-claimed` pair. The build uses those for claimed items;
  here they mean "waits on Burak but nothing is wrong", so red stays for blocked and stopped.
- **Ink as fill.** The Dynamic Island, the home indicator, and the status glyphs use
  `--text-primary` as a fill colour.
- **16px fields on the phone.** Every mobile input is 16px so iOS does not zoom on focus; the
  desktop composer keeps `--text-body`.
- **A fade instead of a hard cut.** Where a transcript scrolls off the top, the boards fade it out
  over the first 36px.

## Assumptions where the record is silent

- **A held PR is an Inbox card.** Today the Inbox lists only blocked tasks, and every card offers
  Resume and Reject. The boards assume each escalation carries its own option labels and L3's
  recommendation, and that a green PR held for Burak appears as a card with "Open PR" and "Ask
  the L2".
- **Server-triggered L3 turns fold.** Chat shows them as one muted line with a Show link, so the
  transcript stays a conversation.
- **The Live session tab is a transcript.** It follows Burak's 2026-09-03 requirement: prompt
  blocks, assistant prose, one row per tool call with output collapsed, separators for task
  boundaries, subtle timestamps, phone friendly. Today's JSON event cards stay behind Raw events.
- **Content is realistic, not recorded.** The quota numbers, the context percentages, and the
  altitude tasks are the readings from the morning of 2026-09-03. The voice-tutor project, the two
  unmanaged folders, and the exact wording of the exchanges are illustrative.
