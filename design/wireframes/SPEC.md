# Altitude UI specification

This document governs the UI. The boards beside it illustrate it; **where a board and this spec
disagree, the spec wins**, and a rule that is only visible on a board is not a rule until it is
written here. It is written for the L3 and L2 that implement it: every component lists its states,
every behaviour names the data it reads, and §7 cuts the work into slices with acceptance criteria.
Approved by the operator on 2026-09-05 (work panel beside the chat; decision page with follow-up to
the asker; system turns folded in the one L3 conversation).

Words used throughout: *the operator* is the person Altitude works for (the configured name is shown
where a name is shown); *L3* is a project's coordinator; *an L2* owns one task; *a turn* is one L3
prompt and its reply; *a system turn* is a turn the operator did not start.

## 1. Product statement and principles

Altitude is a conversation per project with a coordinator that turns the conversation into isolated,
reviewable work. The UI shows that and nothing else. Its four principles:

1. **Projects, not routes.** The rail lists projects. Everything inside a project is that project's:
   its conversation, its work, its decisions. Nothing mixes projects except Needs you.
2. **The conversation is the product.** The project page is the L3 chat. Work appears beside it,
   never instead of it. The chat reads like a conversation: no ids, paths, or JSON in what L3 says
   to the operator; system turns fold to one line each.
3. **A decision is answerable where it is shown, or one click from enough context.** Every card
   carries the asker's recommendation and two labelled options. More context opens a page with the
   reasoning, the trail, the evidence, and a follow-up composer addressed to the asker.
4. **Same components everywhere.** One composer (voice included), one bubble pair, one card, one
   state vocabulary, on phone and desktop. A component ships with all its states or not at all.

## 2. Information architecture

### 2.1 Routes

| Route | Page | Replaces |
| --- | --- | --- |
| `/` | Needs you: every decision across projects | the Inbox |
| `/projects/:name` | the project: L3 conversation, work panel | `/chat/:name` and the old project page |
| `/projects/:name/decisions/:slug` | the decision page for the blocked task `slug` | new |
| `/projects/:name/tasks/:slug` | the task page: L2 conversation, live session | unchanged |
| `/projects/:name/tasks/:slug/live` | the same page with the live session in front (phone tab) | unchanged |
| `/monitor` | Monitor | unchanged |
| `/projects`, `/chat/:name` | redirect to the first managed project, or to `/projects/:name` | the Projects list |

With no managed project every project route shows First run (§3.12). A decision is identified by its
task: today one blocked task carries one open question, and the route follows that.

### 2.2 Layouts

- **Desktop, width ≥ 1024.** Left rail 260px, always visible. Main pane fills the rest. The work
  panel is 340px: inline as a third column at ≥ 1280, otherwise an overlay from the right opened by
  the header's panel button (same content, scrim behind, Esc or the scrim closes it).
- **Phone, width < 1024.** Header 54px, content, composer where the page has one, tab bar 84px
  (Chat, Work, Needs you, Monitor). The phone is specified at portrait 390 wide; landscape is
  unsupported and has no rules of its own. Chat and Work are the selected project's; Needs you and Monitor
  are global. The header shows the project name with a chevron on project tabs and "Altitude" on
  global tabs, so scope is always readable. The project name opens the switcher sheet (§3.11).
- A decision page or a task page opened from a phone tab pushes over that tab with a back control
  and keeps the tab bar.
- No viewport ever scrolls horizontally; transcripts and tables scroll inside their own container.
- The shell fills the visual viewport and never scrolls or bounces: headers, the tab bar and composer stay docked while inner regions own native scrolling and bounce; the conversation shrinks above the keyboard and follows its newest row while the operator is at the bottom.
- Breakpoint constants live in one place in the web code and are the only place widths are named.

### 2.3 Scope rule

The selected project is a UI state persisted per browser (localStorage), set by the rail, the
switcher, or a project route. Needs you cards and rail badges are the only cross-project data on
screen. Opening a card selects its project.

## 3. Components

Each component lists its anatomy, its data, and its states. "Loading" is a skeleton in the
component's own shape, never a spinner over the page; "Error" is one sentence and a Retry that
repeats the read; "Empty" is a sentence in `--text-muted`, never a blank area.

### 3.1 Rail (desktop)

Anatomy, top to bottom: brand; **Needs you** with a count badge; "Projects" section head with **+**
(add a folder); one row per managed project with a state dot, the name, and a count badge;
"N folders not managed" line; engine readout; **Monitor**; the operator row with the configured
name and the theme toggle.

Data: `GET /api/overview` (`projects[].managed`, `projects[].counts`, `projects[].l3`, `queue`,
`quota`, and the second engine's windows). Engine names come from the engine seam; the rail never
hard-codes one, and one configured engine means one row.

| Element | States |
| --- | --- |
| Project row | selected (tint background, `--text-primary`); unselected (`--text-secondary`); hover (tint at half) |
| State dot | running (accent); waits for the operator (`--data-claimed`); blocked by a fault or stopped (`--danger`); idle, nothing active (`--text-muted` at 45%) |
| Count badge | decisions waiting in that project; hidden at zero |
| Needs you badge | decisions across projects; hidden at zero |
| Unmanaged folders line | N folders found under the configured root; click opens First run for the picked folder; hidden at zero |
| Engine readout | one row per engine: name, "N% of week", a 4px meter; the meter turns `--danger` past the 70% reserve line; "no reading" in muted text when `quota.known` is false; "reading 2h old" appended when `stale` |
| Operator row | name from configuration; theme toggle (light default, dark, persisted per browser) |

### 3.2 Project header

Anatomy: project name (18px, 600); status line; actions: work-panel toggle (tinted when the panel is
open, hidden at ≥ 1280 where the panel is inline), overflow menu.

Status line, composed left to right and separated by "·": "L3 answered N min ago on <engine>"
(from the last assistant chat row's `at` and `engine`); "N tasks in flight" (running + queued);
"N waits for your review" (decisions in this project; omitted at zero). While a turn runs the first
part reads "L3 is answering" (or "L3 is handling <what>" for a system turn, §4.1).

Overflow menu: **Reset L3 conversation** (confirm inline; `POST /api/l3/reset`), **Remove project**
(confirm inline; `POST /api/project/remove`), **Design boards** (present only when `GET /api/project/<name>`
reports a design URL; opens in a new tab).

States: normal; L3 never started ("L3 has not started" and a **Start L3** button); error reading
the project (status line shows the error sentence; the conversation still renders from cache).

### 3.3 Conversation

Anatomy: a single column, max 720px, bottom-anchored, newest last. Day dividers ("Today", a date).
Operator messages are right-aligned bubbles (`--bubble`, radius `--radius-bubble`, 15px). L3 replies
are left-aligned prose with no bubble (15px, line-height 1.65): paragraphs, lists, inline code,
links; no headings, no tables. A task card (§3.5) sits under an L3 reply whose turn created a task.
System turns render as system lines (§3.4). Hovering a row shows its time in the gutter; on the
phone a long-press shows it. The phone layout is portrait 390 wide only; landscape is unsupported.

Data: `GET /api/chat/<project>` → `history[]` rows `{at, role, text, trigger, engine, turn_id}`,
`active {id, started_at, trigger}`, `queued[]`. Rows with `trigger == "chat"` are the conversation;
every other trigger is a system turn. The assistant row of a turn that created tasks carries their
slugs (`tasks: [slug]`, added in slice 2) and the card reads the task from `GET /api/task/<project>/<slug>`.

States: loading (three prose-shaped skeleton rows); empty (First run copy when L3 never ran,
otherwise "Say what you want done. L3 answers or creates one task."); error ("Could not load the
conversation." and Retry, cached rows still shown); a turn in progress (§4.2); a queued message
(§4.2); a reply that failed ("L3 could not answer this turn." in muted text under the prompt, with
Retry that resends the same prompt).

### 3.4 System line

One line, centred, 13px `--text-muted`: a dot, the text, and **Show**. It stands for one system turn
or a run of them (§4.1). The dot is `--text-muted` for reports, restarts, and FYIs and `--danger`
for `incident`, `system-recovery`, and fault triggers.

Expanded: a card in the column with a header ("Report landed · <task title> · 09:14", **Hide**),
"What altd sent L3" as label/value rows when the prompt has structured fields (verdict, problems,
signals, PRs, spend) and as preformatted text otherwise, "L3 replied" with the full reply, and links:
**Open task**, **Full report** (the task's report view), **Digest** when the reply recorded one.
A group expands to a list of its turns, each with its own Show.

States: folded; expanded; in progress ("L3 is handling a landed report for <task>", no Show yet);
grouped (N turns); failed turn (the line reads "L3 could not handle <what>"; Show reveals the
prompt and the error).

### 3.5 Task card (inline and in the work panel)

Anatomy: state dot, title (600), meta line "<state> · <engine> · <age or wait>", chevron. Click
opens the task page.

States by task state: queued ("Queued · waits for a lease on <file>" or "waits for dispatch");
running ("Running · <model> on <engine> · started N min ago"); blocked on the operator ("Waits for
your answer", amber dot); blocked by a fault ("Blocked: <one sentence>", red dot); done ("Done ·
PR #N merged", shown under Done this week); rejected ("Rejected", under Done this week).

### 3.6 Composer

One composer everywhere (project chat, decision follow-up, task conversation). Anatomy: rounded
field (`--radius-composer`), placeholder naming the recipient ("Message L3 about <project>",
"Ask a follow-up before you decide", "Message the L2"); a left pill (engine pin on the L3 chat:
Auto or an engine name; recipient pill "To L3 / To the L2" on the decision page; none on the task
conversation); microphone button; send control. The send control is the arrow in an accent circle
in every state, with no visible text; its accessible name is "Send" ("Queue" while busy). A hint line under the field,
12px muted. Phone fields are 16px so iOS does not zoom.

Keyboard: Enter sends (while listening, stops, transcribes, and sends at once), Shift+Enter inserts
a newline, Ctrl/⌘+M starts the microphone or stops to the draft, Esc cancels a recording.

| State | What is on screen | What changes |
| --- | --- | --- |
| Idle | placeholder, mic, arrow disabled | typing enables the arrow |
| Typing | draft text, arrow enabled | Enter or the arrow: the draft becomes a bubble at once, the field clears |
| Sending | the bubble shows at 60% until the server accepts it | accepted: full opacity; refused: the bubble leaves, the draft returns, hint reads "Not sent. Retry." in `--danger` |
| Busy (L3 mid-turn) | the same arrow, enabled with a draft; hint reads "L3 is mid-turn · runs next" | the arrow appends to `queued[]`; a queued row appears under the conversation in muted text with **Remove** (`POST /api/chat/remove`) |
| Listening | Cancel, Stop, and the same arrow, live waveform and timer share one row without wrapping at 390px; the placeholder disappears and the draft stays as it was | Cancel or Esc: back to the previous state, nothing added; Stop or Ctrl/⌘+M: transcribe to the draft; the arrow or Enter: transcribe and send at once |
| Transcribing | the waveform freezes, "Transcribing…" in the hint, mic and arrow disabled, the field stays editable | after Stop: Landed; after Send: append the transcript to the draft and send through Typing → Sending (Busy queues); failure: hint reads "Could not transcribe. Typing works.", draft unchanged, nothing sent; empty transcript: send nothing, return to Idle or Typing |
| Landed | the transcript is appended to the draft, cursor at the end, arrow enabled; nothing else appears (no transcript box, issue #195) | the operator edits or sends as with a typed draft |
| Denied | mic shows disabled; hint reads "Microphone blocked in the browser. Typing works." | stays until the page reloads with permission |
| Unavailable | mic hidden; hint reads "Voice needs HTTPS" on an insecure origin, or nothing when the browser lacks recording | typing unaffected |
| Recipient pill | To L3 (default when L3 asked), To the L2 (default when the L2 flagged the operator) | changes where the follow-up goes (§4.3) |
| Engine pin | Auto, or an engine name | `POST /api/chat` carries the pin; it covers chat and system turns alike and stays until changed |

Voice is capped at ten minutes; the timer turns `--danger` in the last minute and Stop happens
automatically at the cap. Audio never becomes part of task or chat state.

### 3.7 Work panel

Anatomy: "Work" and "N active · N done this week"; **Needs you** (count) with compact decision
cards; **Active** (count) with task rows; **Done this week** folded to a count, expanding to rows.
On the phone it is the Work tab with the same sections.

Data: `GET /api/project/<name>` for the tasks, `GET /api/overview` `queue` filtered to the project.

States: loading (two card skeletons, three row skeletons); empty ("Nothing running. Ask L3 for
something."); a card selected (accent border, while its decision page is open); a row's task
just changed state (the row moves sections with a 200ms fade).

### 3.8 Decision card

Compact (panel, Needs you) and full (the decision page's top) share one anatomy: kind row (kind
label and age), question (600), why (the recommendation in one or two sentences), two option
buttons with the recommended one primary, **More context** link. Kind labels and colours:
**L3 asks** (`--accent-text`); **Ready for review** (`--data-claimed`, a green PR held for the
operator); **Stopped mid-task** and **Fault** (`--danger`). Cards on Needs you carry a project chip.

Data: `GET /api/overview` `queue[]` entries `{project, slug, kind, title, question, detail, asked,
options}` extended in slice 3 with `recommendation` (the recommended option and why, one or two
sentences), `asked_by` ("l3" or "l2"), and labelled `options` chosen by the asker. `POST /api/decide`
carries `{project, slug, option, note}`.

| State | On the card |
| --- | --- |
| Waiting | as above |
| Follow-up sent | a muted line under the why: "You asked: <text> · waiting for L3" (or the L2) |
| Answer arrived | the answer block, prefixed "L3:" or "The L2:", appended under the follow-up; the card grows; a further question is possible |
| Asked by an L2 | the kind row reads "The L2 asks", the follow-up defaults to the L2 |
| Deciding | both buttons disabled, the chosen one shows a spinner |
| Decided | the card collapses out (200ms); the task's row updates; nothing else appears |
| Failed | buttons re-enabled; one line: "Could not record the decision. Retry." |
| Stale | the task was resumed or rejected elsewhere (CLI, another window): the card leaves on the next poll with no message |

### 3.9 Decision page

Anatomy, top to bottom: crumb (back to where the page was opened from: the project or Needs you),
**Open task**, and the panel toggle; chips (project, kind, task title) with the age; the question
as the title; option buttons with an optional note field ("Add a note for the L2 (optional)", sent
with the decision); **Why L3 recommends <option>** with the reasoning; **Where this came from** as
a short timeline read from the task's events (the L2's block message, L3's escalation, "now: the
task is blocked until you choose"); **Evidence** as chips that open the task conversation, the live
session at the failing step, the evidence L3 cited (a PR, blocked dispatches, an issue), and the
decision in the record; the follow-up composer with the recipient pill; a hint: "Your question and
the answer appear here and on the card. The L2 stays blocked until you choose."

On the desktop the work panel stays open with the card selected. On the phone the page pushes over
the tab it was opened from.

States: loading; ready; follow-up in flight ("L3 is answering…" under the composer; the answer lands
in the timeline and on the card); already decided ("Decided N min ago: <option>" banner, options
gone, composer gone, the rest stays readable); task gone (archived: "This task was <archived state>."
and a link to the archive); error.

### 3.10 Task page

Desktop anatomy: header rows (crumb and actions; title; state chips: state, engine and model, PR
with checks state, hold reason); left the operator's conversation with the L2 (same bubbles and
composer as §3.3 and §3.6, placeholder "Message the L2"); right the live session panel (480px,
toggled by the header button) as a transcript: tinted prompt blocks, the worker's prose, one compact
row per tool call with output folded, separators at task boundaries, subtle timestamps, **Raw
events** behind a toggle. Actions **Stop** and **Reject** are quiet text buttons with an inline
confirm ("Stop this task? Its worker ends; the branch stays."); no browser dialogs. Below 1280px the
live session panel follows the §2.2 rule for the work panel: an overlay from the header's panel
button, scrim behind, Esc or the scrim closes it; the `live` route opens it on desktop too.

Phone (not drawn; this is the layout): header with back and the title; a state line; a two-tab row
**Conversation | Live session** (the `live` route selects the second); content; the composer pinned
above the tab bar on the Conversation tab.

Data: `GET /api/task/<project>/<slug>`, `GET /api/transcript/<project>/<slug>`, `POST /api/l2/message`, `POST /api/task/action`.

States: queued (a line "Waits for <lease or dispatch>" replaces the live panel); running; blocked on
the operator (the decision card inline at the top of the conversation); blocked by a fault (a red
line with the one-sentence reason and "L3 has been told"); done or rejected (read-only conversation,
composer gone, PR link in the header); live session connecting, streaming, ended, unavailable ("No
session file for this attempt"); message failed ("Not sent. Retry.").

### 3.11 Project switcher (phone)

A sheet from the header name: managed projects with dot and count, unmanaged folders, **Add a
folder**. Tapping a project selects it and closes the sheet; the Chat and Work tabs follow. Hidden
chevron and no sheet when exactly one project is managed and no folder is unmanaged.

### 3.12 First run

Shown on any project route when no project is managed: a centred card, "Altitude found N folders
under <root>", one row per folder with **Start L3**, and a path field for a folder elsewhere.
States: scanning; none found (the path field alone); starting ("L3 is starting…", then the project
page opens on its first reply); failed (one sentence and Retry).

### 3.13 Restart banner

On every route, above the header, when a merged change awaits activation: what changed in words and
"Altitude restarts at the next quiet moment". The **Restart** button appears only when nothing is
running (no worker, no report waiting, no L3 turn) and disappears when the restart is under way;
the banner leaves when the new process answers.

### 3.14 Monitor

Not redrawn. Content as approved on 2026-09-03: both seats' windows and reset times in human terms
with the 70% reserve line drawn, the age of each reading, where each role would go right now and
why, live sessions with their tasks. It reads `GET /api/monitor` and derives no action.

## 4. Behaviour rules

### 4.1 System turns fold

Every chat row whose `trigger` is not `chat` renders as a system line, never as bubbles. The line
text is the last paragraph of the turn's assistant row; while no assistant row exists the line reads
"L3 is handling <what>", where <what> comes from the trigger ("a landed report for <task>", "a
block on <task>", "a fault on <task>", "the restart"). Consecutive system turns with no `chat` row
between them collapse to one line: "L3 handled N system events between your messages", expanding
to the list. An FYI is a system line too (`trigger == "fyi"`, `role == "system"`, no reply). A
clean report closes without an L3 turn and produces no line; the task simply moves to Done this
week. This is a rendering rule over data the chat log already stores; slice 2 adds the two trims in
§5.2 so the expanded view reads well.

### 4.2 One conversation, in order

Messages sent while L3 is mid-turn queue and run at the next turn boundary in order; the composer
keeps the arrow and the queued rows sit under the conversation until they run. A running turn shows
either a system line in progress (§3.4) or, for a `chat` turn, a typing indicator under the
operator's bubble. `GET /api/chat` is the authority for what is running and what is queued; the UI
polls it and never guesses.

### 4.3 Follow-ups go to the asker

A follow-up from a decision page or card goes to whoever asked. **To L3**: a normal `chat` turn
whose prompt is the follow-up with the decision's slug attached, so L3 answers from the record; the
turn appears in the project conversation and its reply is mirrored on the card and the page. **To
the L2**: a task-conversation message (`POST /api/l2/message`); the blocked L2 answers there and the
answer is mirrored the same way. Mirroring reads the rows that carry the decision's slug; nothing is
copied. A follow-up never decides; the L2 stays blocked until an option is chosen.

### 4.4 Counts mean decisions

Every badge in the shell counts decisions waiting on the operator: per project on the rail row and
the Work tab, across projects on Needs you. Running tasks, FYIs, and queued messages are never
counted in a badge.

### 4.5 Copy

Sentence case everywhere; the only uppercase is a count badge. L3's chat text carries no ids, paths,
or JSON; identifiers live in chips, links, and the expanded system card. Say "the operator" in fixed
UI text and show the configured name where a name is shown. Engine and model names are whatever the
engine seam reports. Ages read "N min ago", "2h", "yesterday"; exact times appear on hover or in the
expanded card.

### 4.6 Nothing is inferred from a board

A behaviour the boards show but this document does not state is a question for the operator, asked
as one plain dilemma, not a guess. The answer is written here before it is implemented.

## 5. Data binding and backend notes

### 5.1 Reads and writes per surface

| Surface | Reads | Writes |
| --- | --- | --- |
| Rail, Needs you, badges | `GET /api/overview` | `POST /api/project/add`, `POST /api/project/remove` |
| Project conversation | `GET /api/chat/<project>` | `POST /api/chat` (message, queue, engine pin), `POST /api/chat/remove`, `POST /api/l3/reset` |
| Work panel | `GET /api/project/<name>`, `GET /api/overview` `queue` | `POST /api/decide` |
| Decision page | the same plus `GET /api/task/<project>/<slug>` (events for the timeline) | `POST /api/decide`, `POST /api/chat` or `POST /api/l2/message` for the follow-up |
| Task page | `GET /api/task/<project>/<slug>`, `GET /api/transcript/<project>/<slug>` | `POST /api/l2/message`, `POST /api/task/action` |
| Composer voice | `POST /api/transcribe` | none; audio is deleted after transcription |
| Monitor | `GET /api/monitor` | none |

### 5.2 Backend changes the design requires

Each is small, named here so the slices can lease it, and each is a decision-mandated change under
the working rules (the design decision of 2026-09-05).

1. **Shorter system prompts.** `report_turn` in `altitude/server.py` inlines up to 1,500 characters
   of report JSON into the prompt. The prompt keeps the structured fields (verdict, problems,
   signals, PRs, spend) and the report excerpt moves behind the task's report view, which the
   expanded system card links. The stored `user` row then reads as label/value rows.
2. **The line is the reply's last paragraph.** L3's reply to a system turn ends with the one or
   two plain sentences the persona already asks for; the fold shows that last paragraph. No new
   field.
3. **FYIs are chat rows.** `tasks.fyi` appends a chat row `{role: "system", trigger: "fyi", slug,
   text}` instead of a line in the project's `inbox.jsonl`; `digest.fyis` and the `fyis` field of
   `/api/overview` are deleted with the Inbox.
4. **Turns name the tasks they created.** The assistant chat row of a turn that created a task
   carries `tasks: [slug]`, written by the server when the turn's task creation lands.
5. **Decisions carry their labels.** `tasks.decisions` returns `recommendation`, `asked_by`, and
   the asker's labelled `options` (falling back to Resume and Reject for a block recorded without
   them); `POST /api/decide` accepts `option` and `note` and records both on the task.
6. **Follow-ups carry the slug.** A `chat` row created from a decision page stores the decision's
   slug so the page and the card can mirror the exchange.

Everything else is client rendering. No new daemon, no new store, no second conversation.

## 6. Tokens

`wireframes.css` imports `web/design/tokens.css` and adds, at its top, the departures below.
Slice 1 moves them into `tokens.css`; from then on the boards and the build share one file.

| Token | Value (light / dark) | Note |
| --- | --- | --- |
| `--radius-card` | 12px | was 8px |
| `--radius-control` | 10px | new: buttons, icon buttons, rail rows; `--radius-input` stays for plain inputs |
| `--radius-composer` | 24px | new |
| `--radius-bubble` | 18px | new: the operator's bubble |
| `--card` | `#ffffff` / `#243044` | new: raised card on `--surface` |
| `--bubble` | `#f1f5f9` / `#334155` | new: operator bubble, quiet chips |
| `--on-accent` | `#ffffff` | new: text on accent fills |
| `--shadow`, `--scrim` | see `wireframes.css` | new |

Type departures: conversation text is 15px (body stays 14px elsewhere); the project header title is
18px, not `--text-page-title`; meta runs 12–13px within the token's stated range; section heads are
14px semibold sentence case, and `--text-label` uppercase is used only for count badges. Everything
else (fonts, colours, spacing scale, 44px targets, contrast floor) is the token file as it stands.

## 7. Implementation slices

Each slice is one task with one L2, ships its docs in the same PR (`README.md`,
`docs/ARCHITECTURE.md`, `docs/SESSION_LIFECYCLE.md` wherever they describe the behaviour, and this
file if a rule changes), and walks every state in §3 on phone and desktop before it reports.
Order matters: each slice leaves the app usable.

| # | Slice | Acceptance |
| --- | --- | --- |
| 1 | **Shell.** Tokens (§6) into `tokens.css`; rail, phone header and tab bar, breakpoints (§2.2), scope rule (§2.3), routes and redirects (§2.1), First run (§3.12), theme toggle. The old Inbox and Projects pages are deleted; Needs you is the old Inbox's decision list restyled as §3.8 cards. | Every route renders in the new shell on 390 and 1440 with real data; the Inbox and Projects routes and their components are gone; `make test` and the web suite pass. |
| 2 | **Conversation.** §3.3, §3.4, §3.6 on the project page; fold and group (§4.1); queue (§4.2); backend notes 1, 2, 4. | A landed report shows as one line and expands to label/value rows; consecutive system turns group; Queue works mid-turn; voice lands in the draft with no transcript box; `Chat.tsx` and the old bubble meta line are gone. |
| 3 | **Work and decisions.** §3.5, §3.7, §3.8, §3.9; backend notes 3, 5, 6; FYIs fold into the chat and `inbox.jsonl` goes. | A decision is answerable from the panel and from Needs you; More context opens the page with timeline and evidence; a follow-up to L3 and to an L2 both mirror on the card; `digest.fyis` and the overview `fyis` field are deleted. |
| 4 | **Task page.** §3.10 on desktop and phone, Stop and Reject with inline confirm, live session panel toggle. | Both tabs work on the phone; a blocked task shows its card inline; Raw events stays behind its toggle. |
| 5 | **Monitor and banner** in the new shell (§3.13, §3.14). | Content unchanged, shell new, restart button appears only when nothing runs. |

Not drawn and not scheduled: settings, a Done view beyond the folded list, project removal beyond
the overflow menu. They are questions for the operator when they come up.
