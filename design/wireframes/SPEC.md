# Altitude UI specification

The operator approved [conversation-first Needs you and L2 decisions](CONVERSATION_FIRST.md)
on 2026-09-08. The conversation-first boards define the decision experience; shared shell and
composer boards define their existing layout and input behavior.
The operator approved [image input](IMAGE_INPUT.md) on 2026-09-10 for project and task conversations.

The operator approved answer-in-place question responses on 2026-09-14 Pacific: **Other…** opens
a small text field, plain questions show the field directly, and preset/custom responses share
one conversational handoff. Question fields omit microphones; ordinary chat retains voice input.

The operator approved [compact mobile chat](#8-compact-mobile-chat) on 2026-09-09: shared compact phone
headers and composer, keyboard-dependent navigation, and disclosed task metadata and reasons.

The operator approved the Needs you / Work separation on 2026-09-10 Pacific: global Needs you
and the owning chat keep questions and quick answers; Work shows all current tasks as status rows,
and only global Needs you carries a numeric attention badge.

This document and the boards beside it are the UI's source of truth, for both visual design and
rules. They stay aligned when the build departs from them; an unresolved rule change is a question
for the operator (§4.6). It is written for the L3 and L2 that implement it: every component lists its states,
every behaviour names the data it reads, and §7 cuts the work into slices with acceptance criteria.
The project conversation, work panel and system-turn treatment are approved on 2026-09-05;
the owning L2 conversation handles dilemmas under the 2026-09-08 decision.

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
   carries an explicit recommended approach and one acceptance action. Opening it lands at the
   actual question and surrounding discussion in its owning L2 conversation.
4. **Same components everywhere.** One composer (voice included), one bubble pair, one card, one
   state vocabulary, on phone and desktop. A component ships with all its states or not at all.

### 1.1 Standing design tenet

Every design and feature iteration is **simple, elegant, polished, visually attractive, easy to
use, and intuitive**. People understand where to click and where to go from the design itself,
without feeling lost. This central project tenet guides future iterations as the visual direction
evolves.

In the existing design review and [phone and desktop walkthrough](../../AGENTS.md#ui), check that:

- Visual hierarchy makes the primary action clear; navigation and plain labels show where people
  are, where they can go, and what an action does.
- Complexity is restrained: each visible control and detail earns its place in the current task.
- Typography, spacing, alignment, colour, and component treatment have a consistent visual finish;
  interaction states and transitions feel complete and polished on phone and desktop.

Apply these expectations with the specified component states (§3) and existing accessibility
requirements, including accessible control names, minimum targets, and contrast (§6).

## 2. Information architecture

### 2.1 Routes

| Route | Page | Replaces |
| --- | --- | --- |
| `/` | Needs you: every decision across projects | the Inbox |
| `/projects/:name` | the project: L3 conversation, work panel | `/chat/:name` and the old project page |
| `/projects/:name/decisions/:slug` | redirect to the owning task conversation and current question anchor | redirect |
| `/projects/:name/tasks/:slug` | the task page: L2 conversation, live session | unchanged |
| `/projects/:name/tasks/:slug/live` | the same page with the live session in front (phone tab) | unchanged |
| `/projects/:name/tasks/:slug/report` | the task's full report, with its digest at `#digest` | new |
| `/monitor` | Monitor | unchanged |
| `/projects`, `/chat/:name` | redirect to the first managed project, or to `/projects/:name` | the Projects list |

With no managed project every project route shows First run (§3.12). A task owns its question or
small group; a question link names its durable ID and revision and focuses the owning chat.

### 2.2 Layouts

- **Desktop, width ≥ 1024.** Left rail 260px, always visible. Main pane fills the rest. The work
  panel is 340px: inline as a third column at ≥ 1280, otherwise an overlay from the right opened by
  the header's panel button (same content, scrim behind, Esc or the scrim closes it).
- **Phone, width < 1024.** Header 54px, content, composer where the page has one, tab bar 84px
  (Chat, Work, Needs you, Monitor). The phone is specified at portrait 390 wide; landscape is
  unsupported and has no rules of its own. Chat and Work are the selected project's; Needs you and Monitor
  are global. The header shows the project name with a chevron on project tabs and "Altitude" on
  global tabs, so scope is always readable. The project name opens the switcher sheet (§3.11).
- A task conversation opened from a phone tab pushes over that tab with a back control.
  Its Conversation/Live session tabs remain visible. The bottom tab bar hides only while software
  keyboard use is detected and returns when it closes, even when the input remains focused.
- No viewport ever scrolls horizontally; transcripts and tables scroll inside their own container.
- The shell fills the visual viewport and never scrolls or bounces. Headers, the tab bar and composer
  stay docked; content and transcripts own native scrolling and bounce inside their containers. The
  shell follows visual viewport height and offset. Editable focus plus substantial viewport
  contraction identifies keyboard use; focus alone, browser toolbar motion and pinch zoom do not.
  Hardware keyboards retain navigation. When viewport evidence is unavailable, navigation remains.
  Hidden navigation leaves focus and screen-reader traversal; a nonzero Needs you count remains
  reachable from the header. Draft, selection and reading position survive keyboard dismissal.
- Browser-managed safe areas remain intact. When navigation hides, the composer owns any applicable
  bottom inset once, without the navigation's reserved home-indicator gap above the keyboard.
- Breakpoint constants live in one place in the web code and are the only place widths are named.

### 2.3 Scope rule

The selected project is a UI state persisted per browser (localStorage), set by the rail, the
switcher, or a project route. Needs you is the cross-project attention inbox; Work belongs to the
selected project. Opening a question selects its project. Project navigation retains state dots;
the global Needs you badge is the only numeric attention badge.

Switching projects opens that project's conversation, retains its own unsent text and clears transient
composer/response state. Accepted turns and waiting messages remain owned by the source project;
switching back reads its saved history, queue and active turn (§3.3), alongside its retained draft.
Unsent project text stays in this client session across route remounts until reload. Clearing it stays cleared.

## 3. Components

Each component lists its anatomy, its data, and its states. "Loading" is a skeleton in the
component's own shape, never a spinner over the page; "Error" is one sentence and a Retry that
repeats the read; "Empty" is a sentence in `--text-muted`, never a blank area.

### 3.1 Rail (desktop)

Anatomy, top to bottom: brand; **Needs you** with a count badge; "Projects" section head with **+**
(add a folder); one row per managed project with a state dot and the name;
"N folders not managed" line; engine readout; **Monitor**; the operator row with the configured
name and the theme toggle.

Data: `GET /api/overview` (`projects[].managed`, `projects[].counts`, `projects[].l3`, `queue`,
`quota`, and the second engine's windows). Engine names come from the engine seam; the rail never
hard-codes one, and one configured engine means one row.

| Element | States |
| --- | --- |
| Project row | selected (tint background, `--text-primary`); unselected (`--text-secondary`); hover (tint at half) |
| State dot | running (accent: a running L2, a task blocked waiting on L3, or a landed report L3 is handling); waits for the operator (`--data-claimed`: a decision in the queue); blocked by a fault or stopped (`--danger`); idle, nothing active (`--text-muted` at 45%) |
| Needs you badge | unanswered operator questions plus operational attention items across projects; hidden at known zero; unknown or stale reads are explicit |
| Unmanaged folders line | N folders found under the configured root; click opens First run for the picked folder; hidden at zero |
| Engine readout | one row per engine: name, "N% of week", a 4px meter; the meter turns `--danger` past the 70% reserve line; "no reading" in muted text when `quota.known` is false; "reading 2h old" appended when `stale` |
| Operator row | name from configuration; theme toggle (light default, dark, persisted per browser) |

### 3.2 Project header

Desktop anatomy: project name (18px, 600); status line; actions: work-panel toggle (tinted when the panel is
open, hidden at ≥ 1280 where the panel is inline), permanent **Setup** status (§3.12), overflow menu.

Status line, composed left to right and separated by "·": "L3 answered N min ago on <engine>"
(from the last assistant chat row's `at` and `engine`); "N tasks in flight" (running + queued);
"N questions need you" and "N operational items" (labelled separately; omitted at zero). While a turn runs the first
part reads "L3 is answering" (or "L3 is handling <what>" for a system turn, §4.1).

The phone combines project identity and a short **L3 Ready / Answering / Handling** status in its
single 54px shell header. The project name opens the switcher. Details holds the last-answer age,
engine/model, task counts, Auto/engine selector and existing project actions; a non-Auto pin stays
named in the compact status. There is no second status row. Details uses a labelled sheet with
contained focus, Escape/outside dismissal where allowed, and focus returned to its opener. The
sheet scrolls inside the currently usable viewport above any keyboard; full names remain readable.
**Setup** remains discoverable in the project header on phone and desktop, including healthy
projects. It opens the same configuration checklist without replacing the conversation or draft.

Overflow menu: **Reset L3 conversation** (confirm inline; `POST /api/l3/reset`), **Remove project**
(confirm inline; `POST /api/project/remove`), **Design boards** (present only when `GET /api/project/<name>`
reports a design URL; opens in a new tab).

States: normal; L3 never started ("L3 has not started" and a **Start L3** button); error reading
the project (status line shows the error sentence; the conversation still renders from cache).

Removing a project means detaching its L3. The inline confirmation explains: finish or reject
unfinished tasks and wait for workers, L3 turns and task processing first; the repository,
remaining worktrees, saved history and queued messages stay on disk. Add the same folder and name
again to attach L3, restore its sessions and history and deliver waiting messages. No work is
implicitly stopped or reassigned. **Cancel**, Escape and outside dismissal close a confirmation
before submission. **Removing…** disables the confirmation and competing actions, with no
dismissal until the request completes. Errors/refusals appear inside the confirmation with Remove
and Cancel still available; a work refusal names the unfinished tasks.

Success removes the managed row and cached project views, selects a remaining project, and opens
Needs you, or First run when the last project leaves. A stale project, task or report URL shows
"Project not managed" with an Open projects link, or First run when no project remains. The folder
is still offered under its configured root; the path field supports folders elsewhere.
`ProjectLifecycleStates.html` illustrates the single removal-and-attachment flow at phone-sized
content widths. No separate detached-but-managed state exists.

### 3.3 Conversation

Anatomy: a single column, max 720px, bottom-anchored, newest last. Day dividers ("Today", a date).
Operator messages are right-aligned bubbles (`--bubble`, radius `--radius-bubble`, 15px). L3 replies
are left-aligned prose with no bubble (15px, line-height 1.65): paragraphs, lists, inline code,
links; no headings, no tables. A task card (§3.5) sits under an L3 reply whose turn created a task.
System turns render as system lines (§3.4). Hovering a row shows its time in the gutter; on the
phone a long-press shows it. The phone layout is portrait 390 wide only; landscape is unsupported.
The conversation scrolls inside the fixed shell, shrinks above the keyboard, and follows its newest
row while the operator is at the bottom; scrolling up leaves the reading position in place.
Keyboard transitions, draft growth and streaming preserve bottom-follow or, when reading older
messages, the same visible message and offset. Sending resumes following. Opening details keeps
the reading position; View question deliberately returns to the existing question anchor.

Shared prose links apply to L3/L2 conversations, live session prose, folded/expanded system replies,
decision questions, recommendations and follow-ups, and report notes, digest and prose fields.
`PR #250` / `pull request #250` target the project's `/pull/250`; `issue #247` / bare `#247`
target `/issues/247` (GitHub also resolves pull requests there). `owner/repo#247` targets that named
repository, including when the current project has no GitHub origin. Repository context comes
from `GET /api/project/<name>`, and each Needs you card uses its own project.

| Reference state | Appearance and interaction |
| --- | --- |
| Plain references, saved or arriving | Accent text with underline; the whole reference is a native link. No mention lookup or loading indicator. |
| Hover / keyboard focus / touch | Native pointer and link preview; the shared focus outline on keyboard focus. Enter or tap opens a new tab and leaves the conversation and draft in place. |
| Existing URL or Markdown link | Keeps its label and destination, with one anchor and the same external-link behavior. |
| Inline code / backtick or tilde fence | Code stays code, including unfinished fences while streaming; no reference links inside. Ordinary numbers remain text. |
| Repository loading, absent, invalid, or failed | Prose remains readable; unqualified references stay text. Once metadata arrives they become links. Explicit cross-repository references and existing links remain usable. |
| Empty prose | No links or extra controls; the containing view retains its empty state. |
| Listening / denied | Reference rendering adds no microphone or permission state; the composer's states in §3.6 apply. |
| After reload / project switch | Saved text is rendered with the displayed project's repository; no prior project's destination carries over. |

Phone (390×844) and desktop (1440×900) evidence: `web/e2e/prose-references.pw.ts` uses
`walkthrough.ts` and a disposable real service, with named metadata failure/delay overlays.

File references use ordinary accent-coloured underlined links, with no chip, icon, border or
background. Preserve the supplied absolute path, `file:///` URI or Markdown link label. Hover
exposes the full target; opening exposes it on both viewports. References outside code open a
separate read-only browser tab, retaining the conversation's scroll position and draft. Long paths
wrap. A Markdown angle-bracket destination or percent-encoded file URI supports spaces.

The reader shows filename, full selectable reference, Copy path and current contents. After task
archival it also identifies the current location. Markdown renders headings, paragraphs, lists,
bold, links and literal code blocks; a small Raw toggle exposes its exact source. `.txt` stays
plain text. Embedded HTML and images stay text; opening performs no command execution, automatic
external-resource fetch, download or native-app launch. The operator approved simple link styling
and bounded task-document access with rendered Markdown on 2026-09-15 Pacific.

| File state | Appearance and interaction |
| --- | --- |
| No reference / code | No new controls; inline and fenced code remain unchanged. |
| Hover / focus / touch | Ordinary link focus styling; keyboard activation or tap opens the same reader in another tab. |
| Loading / retry | Loading file; previous contents disappear while the new read is pending. |
| Readable Markdown / text | Rendered Markdown with Raw toggle, or plain text; commands remain selectable text. |
| Empty file | This file is empty; full target remains visible. |
| Missing / unreadable / denied / unsupported | File unavailable with the reason and Retry. The full target stays selectable and copyable. |
| Copy succeeds / clipboard denied | Path copied confirmation, or a manual-copy hint with selectable path. |
| Return to conversation | Closing the tab retains the source draft and reading position. |
| Listening / microphone denied | Existing composer behavior remains; file references neither start nor cancel recording. |

Access covers regular UTF-8 `.md`/`.txt` files directly in the selected registered project's task
folders, active or archived, up to 1 MiB. It includes unmentioned documents within existing private
web access. Other locations, subdirectories, symlinks, special files and foreign-host URIs are
refused. Actual phone/desktop journey evidence lives in `web/e2e/file-references.pw.ts`; review
captures stay in ignored artifacts.

Data: `GET /api/chat/<project>` → `history[]` rows `{at, role, text, trigger, engine, turn_id}`,
`active {id, started_at, trigger}`, `queued[]`. Rows with `trigger == "chat"` are the conversation;
every other trigger is a system turn. The assistant row of a turn that created tasks carries their
slugs (`tasks: [slug]`, added in slice 2) and the card reads the task from `GET /api/task/<project>/<slug>`.

States: loading (three prose-shaped skeleton rows); empty ("L3 has not started. Start L3 to begin
the conversation." beside the header's Start L3 when L3 never ran,
otherwise "Say what you want done. L3 answers or creates one task."); error ("Could not load the
conversation." and Retry, cached rows still shown); a turn in progress (§4.2); a queued message
(§4.2); a reply that failed ("L3 could not answer this turn." in muted text under the prompt, with
Retry that resends the same prompt).

Project switching uses the rail on desktop and the header switcher on phone. These states apply
on both, including switching back before or after a response finishes:

| State when leaving Alpha for Beta | What appears and disappears |
| --- | --- |
| Empty, loading or reading history | Beta loads or shows only its cached rows, queue and active turn; Alpha's rows leave. |
| Typing an unsent draft | Beta shows its own draft, initially empty. Returning to Alpha restores Alpha's text; copying and pasting between projects preserves both drafts. |
| Send pending or reply streaming | Alpha's local prompt, typing indicator and streamed text leave. The accepted turn finishes in Alpha; Beta can send independently. |
| Both projects have sent a turn | Each conversation shows only its own turn. Either completion order preserves the other project's draft and reply. |
| Late HTTP refusal or stream error | Beta's draft and send state stay its own; no Alpha error or Retry appears there. Submitted-text recovery returns only to Alpha, alongside its retained newer text. |
| Switch back to a failed accepted turn | Alpha's stored prompt and failed-turn Retry appear only in Alpha; Retry resends that prompt to Alpha. |
| Listening, Stop-to-edit transcription or microphone denied | Leaving stops recording, releases microphone tracks and cancels unsent transcription; late results cannot fill Beta's draft. The new composer has its own microphone state. |
| Send requested while recording | Transcription and send complete for Alpha even after navigation. Returning while pending shows its original text, Transcribing and Cancel; failure restores Alpha's text. Beta's draft and send remain independent. The same rule applies when leaving a task for project chat. |

`web/e2e/project-isolation.pw.ts` walks these transitions with fictional projects and a disposable
file-backed service; delayed refusals and microphone results are controlled browser overlays.

### 3.4 System line

One line, centred, 13px `--text-muted`: a dot, the text, and **Show**. It stands for one system turn
or a run of them (§4.1). The dot is `--text-muted` for reports, restarts, and FYIs and `--danger`
for `incident`, `system-recovery`, and fault triggers. A failed turn of another trigger keeps the
muted dot; its line carries the failure.

Expanded: a card in the column with a header ("Report landed · <task title> · 09:14", **Hide**),
"What altd sent L3" as label/value rows when the prompt has structured fields (verdict, problems,
signals, PRs, spend) and as preformatted text otherwise, "L3 replied" with the full reply, and links:
**Open task**, **Full report** (the task's report view), **Digest** when the reply recorded one.
The structured prompt's Task field supplies Open task and is omitted from the label/value rows.
A group expands to a list of its turns, each with its own Show.

States: folded; expanded; in progress ("L3 is handling a landed report for <task>", no Show yet);
grouped (N turns); failed turn (the line reads "L3 could not handle <what>"; Show reveals the
prompt and the error).

A selected L3 heads-up uses this same compact line, wrapping its full one- or two-sentence text
on phone and desktop. It stays outside routine groups (§4.1). No separate badge, dismiss control,
timer or notification surface appears.

| Heads-up state or action | What appears and disappears |
| --- | --- |
| Empty history | Conversation empty text; no heads-up or background group. |
| Initial loading | Conversation skeleton; no placeholder FYI. |
| Selected FYI arrives | Its full concise text and Show appear between the surrounding routine groups. A reader at the bottom follows; a reader above keeps their scroll position. |
| Show on heads-up | The line becomes its FYI card with full text, Hide and Open task when linked; routine groups stay folded. |
| Hide on heads-up | The card and its task link leave; the compact heads-up returns. |
| Show/Hide on routine group | Routine evidence appears/disappears; the selected heads-up remains outside it. |
| Conversation read fails | Error and Retry appear; any cached heads-up remains. A successful Retry removes the error. |
| Listening or microphone denied | Composer states in §3.6; the heads-up remains unchanged and requests no permission. |

`web/e2e/heads-up.pw.ts` walks the heads-up states, task navigation and scroll preservation at both
viewports; `conversation.pw.ts` covers the unchanged listening/denied composer states.

The report view has a back link to the task and a "Report" title. It reads the task's report and
shows plain sections when present: Landed (PRs, main checks and deploy), Review, Blocked, Decisions,
FYI, Follow-ups, Deviations, Spend, Report notes, and Digest. Report notes and the digest are prose;
Digest links land at its section. States: loading (a title-shaped skeleton); empty ("No report
yet."); error ("Could not load the report." and Retry).

### 3.5 Task card (inline and in the work panel)

Anatomy: state dot, title (600), meta line "<state> · <engine> · <age or wait>", chevron. Click
opens the task conversation, at its current operator question when one is open (§3.7).

States by task state: planned (a queued task with a planned wait: "Planned · waits for <reason>",
muted queue dot and a reason that wraps on phone and desktop); queued ("Queued · <hold>", where
the hold is the queue's own reason: "waits
for a slot · WIP limit N reached", "waits for an engine · <why>", "waits for the restart", "waits
for resume at <time>", or plain "waits for dispatch"; never a file lease, which the queue does not
hold; see [concurrency](../../docs/ARCHITECTURE.md#task-lifecycle)); running ("Running · <model> on <engine> · started N min ago"); blocked waiting
on L3 ("Waits for L3", the running dot: L3's answer is Altitude's own work, and the dot turns amber
only when L3 escalates to the operator; the rail's §3.1 dot follows the same rule); blocked on the
operator ("Needs you · N questions" plus independent execution status, amber dot); blocked by a
fault ("Blocked: <one sentence>", red dot); operator-stopped ("Stopped", red dot);
owner/daemon-parked without a question, fault or operator stop ("Paused", idle dot);
reported ("Report landed · waits for L3", running dot);
done ("Done · PR #N merged", shown under Done this week); rejected ("Rejected", under Done this
week).

Open operator questions remain visible while running or queued, independently of execution.
A fault keeps its red dot and cause even when a separate question also needs an answer. An
operational pause without a question uses its actual status, never an inferred request to decide.

The same card is the row in the work panel and the card under an L3 reply that created the task
(§5.2 note 4); a slug the project no longer lists renders as the row with the slug as its title.

### 3.6 Composer

One composer everywhere (project chat and task conversation). Anatomy: rounded
field (`--radius-composer`), placeholder naming the owner ("Message L3 about <project>",
"Message the L2"); a left pill (engine pin on L3 chat: Auto or an engine name; none on the task
conversation, and in project details on phone); Add images button; microphone button; send control. The send control is the arrow in an accent circle
in every state, with no visible text; its accessible name is "Send" ("Queue" while busy). A hint line under the field,
12px muted on desktop. Phone fields and messages are 16px; text, mic and send share one row with
44px control targets and a 70px single-line dock. No routine hint or engine toolbar adds a row on
phone. Relevant send/access errors and voice/denied/unavailable explanations remain visible and
announced. A draft starts at 44px and grows to the lesser of 120px and 25% of the usable visual
viewport, with a 44px minimum, then scrolls internally. Mic and send remain at the field's bottom.

Keyboard: Enter sends (while listening, stops, transcribes, and sends at once), Shift+Enter inserts
a newline while editing, Ctrl/⌘+M starts the microphone or stops to the draft, Esc cancels voice input.
During voice input, keyboard, paste and cut cannot mutate the text; selection and copying remain available.
`web/e2e/conversation.pw.ts` and `project-isolation.pw.ts` walk these states, delayed success,
failure/cancel, Stop versus Send, independent project drafts and navigation during transcription
at both phone and desktop widths.

| State | What is on screen | What changes |
| --- | --- | --- |
| Idle | placeholder, mic, arrow disabled | typing enables the arrow |
| Typing | draft text, arrow enabled | Enter or the arrow: the draft becomes a bubble at once, the field clears |
| Sending | the bubble shows at 60% until the server accepts it | accepted: full opacity; refused: the bubble leaves, the draft returns, hint reads "Not sent. Retry." in `--danger` |
| Accepted; stream or refresh interrupted | sent bubble or saved queue row; the composer stays cleared and newly typed text stays | refresh reconstructs history, active turn and queue by their IDs; read-error Retry only reads; no unsent Retry or invented answer failure |
| Delivery unconfirmed | submitted text followed by any newly typed draft on a new line; hint reads "Could not confirm delivery. Check the conversation before sending again." | no send Retry; the operator checks history before editing or sending; HTTP headers, server errors and matching text alone do not prove delivery |
| Busy (L3 mid-turn) | the same arrow, enabled with a draft; header names the active work and queued rows say what runs next; desktop retains its mid-turn hint | the arrow appends to `queued[]`; a queued row appears in the conversation in muted text with a 44px **Remove** target on phone (`POST /api/chat/remove`) |
| Opening microphone | "Opening microphone…" with an indeterminate spinner inside the composer box; existing text remains readable and read-only | Cancel or Esc restores editing; denial or failure preserves the draft |
| Listening | Read-only, selectable draft; "Listening… Stop to add text, or Send." with activity indicator inside the box. Cancel, Stop, arrow, waveform and timer share a separate row without wrapping at 390px | Cancel or Esc: back to editing, nothing added; Stop or Ctrl/⌘+M: transcribe to the draft; the arrow or Enter: transcribe and send at once |
| Transcribing | "Transcribing…" and an indeterminate spinner inside the box; draft stays readable and read-only, mic and arrow disabled, Cancel available. Desktop waveform and timer freeze | after Stop: Landed; after Send: append and send once through Typing → Sending (Busy queues); Cancel, failure or timeout restores editing and preserves the draft; failure: "Could not transcribe. Typing works."; empty transcript: send nothing, return to Idle or Typing |
| Landed | the transcript is appended to the draft, cursor at the end, arrow enabled; nothing else appears (no transcript box, issue #195) | the operator edits or sends as with a typed draft |
| Denied | mic shows disabled; hint reads "Microphone blocked in the browser. Typing works." | stays until the page reloads with permission |
| Unavailable | mic hidden; hint reads "Voice needs HTTPS" on an insecure origin, or nothing when the browser lacks recording | typing unaffected |
| Engine pin | Auto, or an engine name | `POST /api/chat` carries the pin; it covers chat and system turns alike and stays until changed |

Voice is capped just under ten minutes: the client stops at 9:55 to stay under the server’s ten-minute limit, and transcription times out after 60 seconds.
The timer turns `--danger` in the last minute. Audio never becomes part of task or chat state.
Explicit Send retains its transcription operation through route navigation in the current document;
returning to its source shows the pending text and Cancel. Stop-to-edit transcription cancels on leaving.
Reloading or closing the document ends client voice processing; it does not replay audio later.
An explicit refusal also preserves any newly typed draft after the refused text on a new line;
Retry submits that recoverable draft. Combined failed drafts stay unconfirmed if any send lacks a receipt,
so Retry cannot duplicate or discard uncertain text. A later callback from an earlier send cannot change a newer
pending message. `web/e2e/project-isolation.pw.ts`, `task-lifecycle.pw.ts` and `conversation.pw.ts`
walk accepted/interrupted, failed refresh, reconnect, queued, refused and unconfirmed states at both viewports.

Image input uses the same toolbar and adds no permanent row when empty. A conditional strip holds
up to four short previews with individually named 44px Remove targets. Native selection and desktop
image-file paste preserve ordinary text paste; image-only sends work. PNG/JPEG/static WebP limits
are four images, 10 MiB each, 20 MiB total, 25 megapixels and 8192 pixels per side. Selection remains
local until Send; leaving the conversation releases it and late results cannot fill another draft.

| Image state | Visible behavior and transition |
| --- | --- |
| Empty / cancel picker | Add images shares the existing controls; no strip or status row. Cancel changes nothing. |
| Selected / remove | Preview strip appears; last Remove removes it and keeps text. Invalid format, unreadable preview or size/count rejection names recovery inline and preserves other selections. |
| Sending | Pending bubble holds text and previews. Composer controls freeze for this submission; acceptance clears selection and releases the composer before the agent finishes. |
| Confirmed refusal | Pending bubble leaves; the full editable draft/selection returns with the server's reason. Removing or replacing images allows another send. No partial message is accepted. |
| Uncertain response | Pending bubble remains with Could not confirm send and Retry; controls stay frozen. Retry uses the original identity and content. Navigation remains available. |
| Queued / delivery failure | Images retain their caption. Project rows and unclaimed task messages offer Remove; removed task messages show a muted placeholder without images. Failed project-turn Retry uses saved images; task recovery keeps existing authority. |
| Sent / viewer | Thumbnails belong to the saved message. Open shows the full image in a modal with Fit/Zoom, Close and Escape; closing restores thumbnail focus. Archived tasks retain viewing without a composer. |
| Loading / missing / denied | Image-sized placeholder names loading or the error; Retry repeats only the private read, never the send. Text remains readable. |
| Capability unavailable | Add images explains why input is unavailable. Known converter refusal restores selections; removing them allows text. |
| Voice / scope changes | Listening/transcribing retain previews and lock text editing. Cancel preserves preexisting text; empty/failed transcription sends nothing. Explicit voice Send captures its images and completes for the original conversation after navigation. Other unsent image selection is released on leaving. |

The approved [image interaction contract](IMAGE_INPUT.md) explains retention and private
agent delivery. `web/e2e/image-input.pw.ts` drives these states with real storage/API and deterministic
engine fixtures at 390×844 and 1440×900; named screenshots live in `web/ui-artifacts/results/image-input*`.

### 3.7 Work panel

Work answers “What is happening in this project?” Anatomy: "Work" and "N current · N done this
week"; **Current** with every unfinished task once as a compact status row; **Done this week**
folded to a count, expanding to rows. On phone it is the selected project's Work tab with the
same sections. Task totals are labelled text, not attention badges. Work contains no question
body, recommendation or answer control.

Data: `GET /api/project/<name>` for the tasks, `GET /api/overview` `queue` filtered to the project.

Each row is one link with a chevron and an accessible name including its status. An open operator
question shows **Needs you · N questions** and opens the first current question's stable group
anchor in the owning chat. Execution stays secondary and independent: a running or queued worker
can still need an answer. Faults remain visible alongside any separate question. Other rows open
their ordinary task conversation and distinguish running, planned, queued, waiting on L3, operationally
paused, faulted and reported tasks. Operational controls stay in the task.

Planned rows use the existing queued treatment in Current, without a new panel or attention badge.
One short reason names the wait; one optional task dependency releases it only when archived done.
L3 or the operator can release either a free-text or named wait through the CLI with a recorded
reason. The row becomes Queued with its ordinary dispatch hold, then Running only when launched.
Opening it shows the conversation and reason; saved messages update the launch context without
releasing the task. There is no release button in Work or the task conversation.

| State or action | Visible behavior |
| --- | --- |
| Initial loading | Row skeletons; no guessed counts or empty-state sentence. |
| Empty | **No current tasks. Ask L3 to start something.** Recent completed work can still expand. |
| Initial read fails | A specific error with Retry replaces the affected content. Independently loaded task rows remain discoverable if only attention loading fails. |
| Cached read fails | Retain rows with a saved/stale notice and Refresh; links remain available. Destination reads govern answer controls. |
| Planned | Muted dot and **Planned · waits for …**; the complete reason wraps and the row opens its conversation. No worker or WIP slot is allocated. |
| Message before release | The message appears in the conversation; the wait remains. Existing composer listening, denied and send-error states apply. |
| Release, then launch | The same row shows Queued and its dispatch hold, then Running when observed; the planned reason disappears. |
| Answer, withdrawal or follow-up | The row keeps its order. Answers and withdrawals reduce its question count; a wake alone changes none. |
| Final answer | Attention leaves; the row shows observed execution or waiting status. Saving an answer cannot claim Running. |
| Done or rejected | The row leaves Current and enters Done this week once, with a 200ms fade. The disclosure shows or hides the most recently finished tasks (up to twenty) from the last seven days, with or without a PR, and is absent when none exist. A no-code completion also posts a task-linked FYI to the project conversation. |
| Open and Back | Waiting rows focus the owning question with live activity closed. App Back and browser Back/Forward preserve the originating Work or Needs you view (§3.9). |

Work and Needs you add no voice controls. Listening, transcribing, denied and send-failure states
remain in the owning conversation (§3.6); sending leaves no extra transcript field.

### 3.8 Decision card

Needs you answers “What needs my response?” It shows one compact card per unanswered group: task/project,
source, one question or up to three independent questions, and **Open L2 chat**. The model can ask
a plain question, give one recommended quick action, or offer two to three quick options with one
recommendation and concise rationale. All choices start unselected and remain staged until
**Send N answers**, including a single question. **Other…** reveals a small text field beneath that
member's choices; a plain question shows the field immediately. Presets, custom answers and follow-up
questions can be submitted together. Each question is a flat section with a divider, without a nested
panel. The field focuses when Other is selected; its full question stays directly above it. Empty
custom fields do not count as answers. Selecting a preset replaces that member's custom response.

The default content makes the task's user-facing purpose and actual choice clear at a glance.
Task titles and complete questions wrap without clipping; the question uses plain language,
options name concise actions, and the recommendation includes the material consequences needed
before answering. Owners rewrite long technical explanations around that decision. Full reasoning,
implementation detail, evidence and history remain accessible in the owning conversation; additional
saved question detail opens under **More context** there when it differs from the visible question.
Needs you groups all attention items into contiguous project sections. Each section starts with
the full owning project name as a heading; names wrap without clipping on phone and desktop,
including shared prefixes and unbroken names. Project order follows first appearance in the queue;
items keep their order within each project, and question groups stay together. This applies to
single-project and mixed-project queues, including questions, reviews, stops and faults. The selected
project never supplies an item's owner or filters the inbox. Saved reads retain the sections.
The task title is its own fully wrapping link below source/time and above the question.
`web/e2e/needs-ownership.pw.ts` walks ownership and navigation; `work-and-decisions.pw.ts`
walks empty, loading, saved/read errors, sending, sent, failed and denied states at both viewports.
Mechanical truncation or hiding a necessary
consequence does not satisfy concise presentation. Long questions still remain fully readable on
phone and desktop, with the same answer and revision semantics.
With no manual picks or open custom fields, **Use recommendations** stages only questions with an
explicit recommendation; **Send N answers** submits them. It never overrides a picked alternative.
The task title and card background open the same chat destination. Reference links remain ordinary
external links. Plain questions accept a typed answer in place; no inferred default exists. Operational stops and faults open the task's
ordinary controls. Discussions stay in chat, with no per-card follow-up fetch or mirrored exchange.
A dilemma remains answerable while a provider limit queues a fresh attempt: sending saves the
response, and the same chat queues replies with **Delivered when Altitude starts the L2.** The saved
question or receipt travels into the fresh brief. Queuing alone never closes a relevant question.

On receiving guidance, the owner checks each question before lengthy analysis. Harmless follow-ups
leave valid choices visible; a doubtful question is withdrawn promptly with a reason in chat. Other
questions stay answerable. When ready, the owner re-asks even unchanged wording, without waiting for
unrelated work. A review of completed work waits for the relevant revisions, checks and review.
Freshness is owner judgment, independent of worker status.
An answer settles only the stated choice; requested work remains required. Guidance may queue before
the owner's checkpoint; the owner considers an earlier answer with later guidance before acting.

The recommendation body and response controls are the same component as the one at the question's
message anchor in chat. Loading uses a skeleton with no inferred count; empty Needs you says
**Nothing needs you.** A read failure offers Retry. Cached failure keeps saved cards with an explicit
refresh notice and disabled sending. During submission, the control says **Sending…** and cannot be
repeated. Sent members leave the attention count; remaining members stay together. A brief **Sent to L2**
receipt links to chat, where each submitted response replaces its member's input and remains readable.
The card leaves Needs you when no members await a response. This records delivery, not agreement:
the L2 interprets presets and typed responses alike, records clear decisions, and discusses follow-ups.
Failure retains responses with Retry. Denied writes require a refreshed read. A changed question
clears only its own stale draft; independent drafts survive refresh and another member's submission.
The send row follows the questions in normal flow on phone and desktop and scrolls with them.
With the phone keyboard open, the focused answer stays unobscured; scroll to the end to send.
Question fields have no voice controls; ordinary chat keeps its existing voice states.
Withdrawal likewise removes only affected controls and updates counts; stale submissions fail.
Re-asking supplies fresh controls, retaining earlier history without carrying approval forward.

### 3.9 Open the owning L2 question

`/projects/:name/tasks/:slug?question=<id>&revision=<n>` opens the owning human conversation at
that durable question's group, with preceding explanation visible. Every member link focuses the
same stable group anchor; the conversation renders the group once. L3 escalation text stays attributed to
L3 and contains the actual dilemma and recommendation. Later technical events do not change the
anchor. The live session starts closed when entering a question; **Activity & evidence** reveals
technical event summaries and the link to the existing live view.

The question field and normal composer accept a follow-up, a simple answer such as “21 days”, or a nuanced decision.
There is no recipient selector, note form or extra confirmation. A follow-up wakes the owner to assess
the question (§3.8). The L2 records a clear decision against its source message;
the UI never treats sending as approval. A sent member keeps its receipt while the owner responds;
an explicit re-publication restores input with a fresh revision. Unchanged ordinary re-parking
does not ask the person to send again. Ambiguity is clarified in conversation. A partial answer
closes answered members and keeps only relevant unanswered members. A partially answered member
retains its remaining scope in a new revision. A single typed reply can answer the whole group.
If the chosen direction makes the
remainder unnecessary, close it with a short reason instead of leaving stale questions open.

A closed question retains its reason with **Decision recorded** or **Question closed**.
A withdrawn question occupies one muted **Question withdrawn** disclosure row, collapsed by default.
Expanding it reveals the question, owner's reason and former recommendation together, without answer
controls or a second recommendation disclosure. Collapsing it restores the compact audit trail;
independent open questions remain visible. Withdrawal records no operator decision or merge authority.
Recorded acceptance and execution are separate observations:
show **Waiting to resume** while waiting for capacity, and **Work resumed** only after observing the
worker running. An old question URL stays readable and links to the current revision when one exists.
Superseded versions fold under **Earlier question**; linking to an old version opens its history.
An unavailable question is explicit and keeps the ordinary task conversation accessible.

Phone and desktop walkthroughs cover fresh waiting, harmless follow-up, early withdrawal during
work, independent acceptance, unchanged and revised re-asking, repeated withdrawal, old links and
stale submissions. Empty/loading, recording, read/write error and denied states retain §3.8 behavior.
The composer keeps its existing listening/transcribing states; withdrawal adds no input or transcript.

Opening from Needs you or Work pushes one task entry and retains the origin tab. App Back uses the existing
history entry and falls back to the project for a direct link. Conversation/Live session switches
replace that entry and preserve the same-task draft. Leaving the task clears its draft; a late send
stays bound to its original task. A new reply does not pull the reader away from the question:
**Latest messages** follows the bottom, and **View question** returns to an offscreen open dilemma.
Pending-question reads poll every two seconds. Stale navigation refreshes before acceptance, and
every write names its exact question revision. Archived tasks retain history without a composer.

#### 3.9.1 Pending design preview

A question with saved design content has a **View preview · vN** link in Needs you and its owning
task conversation. When the open question is offscreen, the conversation's **View question** row
also exposes its preview, including on direct chat entry at the latest messages and after partial
answers. Question navigation follows an open group member with an attachment before another open
member. Work's task row opens the exact owning question.
Links open `/projects/:name/tasks/:slug/design/:questionId/:revision`
in another browser tab, leaving the original route and draft intact. Closing it returns to that view.
The page shows the captured title (identifying proposal or implementation review) and version,
named screenshots with **Full size** links, captured text and **Back to question**. Earlier proposal
attachments remain with their historical questions. Screenshot links open the fixed image in a browser tab for
native zoom. There is no added conversation, approval control or permanent task banner.

Each version contains explicitly selected PNG/JPEG screenshots and text. HTML simulations are shown
as captured states; active HTML is never embedded. Changing the working files does not change the
saved version. A replacement advances the existing question revision, and the prior preview is
labelled **Earlier version** with **Open current question**. **Back to question** still targets the
exact version inspected. Viewing, opening a full-size screenshot and sending a follow-up leave the
question unanswered. The existing decision controls record approval; merge holds remain unchanged.

Phone and desktop states are walked in `web/e2e/task-design.pw.ts`: no design means no link;
**Loading preview…** gives way to content; missing, changed, denied or failed reads show **Design
unavailable**, **Retry** and **Back to question**, with saved content hidden. An image starts at
**Loading screenshot…**; a failed image hides its preview and full-size control and shows
**Screenshot unavailable** with **Retry screenshot**. Recovery removes the error/loading text.
Earlier versions retain their original text and screenshots. Existing conversation listening and
decision states are reused; the viewer has no microphone, composer or empty publishing form.

### 3.10 Task page

The [L2 activity and steering agreement](l2-progress/PROPOSAL.md), with its
[maintained task states](TaskStates.html), is approved for #302 on 2026-09-09.
Its directly accessible Stop supersedes hiding Stop in the mobile header disclosure.

The task header includes **Observed tokens** in phone task details and directly on desktop,
also present in the report view. The folded token row shows the cumulative observed total (unknown when
unavailable), coverage, and collector freshness. Expanded details group engine and owner/delegated
session rows, or say **Provider total · helpers unsplit**, with inclusive input/output and available
cache-read, cache-write and reasoning subsets. Cache/reasoning fields are parts of input/output,
never additional totals. Attempts survive resume and engine handoff; project L3 work is excluded.
The display is separate from the context line and quota readouts and makes no cost claim.

Expanded details also show **L1 helpers observed**, the total unique observed count across recorded
owner attempts, direct/descendant counts when known, unknown depth, and attributable helper request
tokens. Each helper shows engine, native identity, spawning parent or owner linkage, owning L2
session, owner attempt context, counters/coverage and any separate unsplit provider total. Resumed
identities count once; unavailable token counters do not erase observed identities. Counts and
tokens are partial observations. An owner-linked helper does not imply a known direct spawn.

Data: `GET /api/task/<project>/<slug>` `token_usage`, retained on the archived task. Reads use the
daemon's saved observation; opening or polling the disclosure never starts collection or a model.
States: task loading uses the page skeleton; absent readings say **Token usage unknown**; live
counter updates keep the disclosure open; partial readings keep known counts and explain gaps;
checked time older than one minute says stale while live; provider counter time is shown separately;
collector errors retain prior numbers with unavailable/partial coverage; finalization shows retained
counts and its timestamp. Expand reveals rows and limitations; collapse removes them. Task read
failure keeps the existing error and Retry behavior. The disclosure is read-only, so listening and
permission prompts do not apply; unreadable local logs use the unavailable state. Expanded details
scroll within task details on phone. `web/e2e/task-usage.pw.ts` walks these states at both viewports.

Desktop anatomy: header rows (crumb and actions; title with state dot; a muted line; state chips:
state, engine and model, PR with checks state, Merge held when applicable); left the operator's conversation with
the L2 (same bubbles and composer as §3.3 and §3.6); right the live session panel (480px, toggled by
the header button). The muted line reads "attempt 1 · started 32 min ago · 18% of its context used"
when those values are available; a finished task reads "done 2h ago" or "rejected 2h ago". Engine
and model appear in their chip. The PR chip reads "PR #N merged · main checks passed" or its open
and check states, in danger tone when main checks failed. It links to the PR when the repository
URL is known, otherwise it is a plain chip. **Merge held** is concise and independent of execution
or question state. Its complete reason opens in task details and wraps without truncation.

**Stop** is directly accessible beside the composer and in Live session, on phone and desktop.
One click requests termination immediately. **Reject** is in phone task details and the desktop header,
with inline confirmation:
"Reject this task? Its worker ends and the task is archived." with "Reason (optional)", Reject and
Cancel. Stop appears while running; Reject appears while queued, running, blocked or reported.
An operationally blocked task without an open question also offers **Resume**, using the existing
daemon operation. The button becomes **Resuming…** during the request, then disappears when running.
A failed request leaves Resume available and places its error on a separate line under the actions,
including on the phone. Resuming an operational pause records no decision. A confirmed Stop instead
offers **Continue session** in the same place as Stop. It resumes without sending the unsent draft.
Sending a correction from this stopped view appends it after earlier held messages and resumes the
same saved session. A stale running view can only queue a message. Both continuation actions preserve
existing file edits, the session, attempt and model; capacity waits say **Waiting to resume**.

Stop replaces its button with **Stopping…** until termination is evidenced. The draft stays editable;
Send and Continue are unavailable while stopping or unconfirmed. Unknown or failed termination says
**Stop unconfirmed · The worker may still be running**, with **Check status** to read evidence.
Rechecking does not issue another Stop. An actual termination receipt or a conclusive current worker
status enables **Stopped** and Continue. Desktop Stop advertises **Esc**; the key stops only when no
input, composition, dialog, recording, menu or overlay owns it. Recording cancels and overlays close
before Escape can reach the worker. Phone always has the visible button.

One compact **Latest from L2** area above the composer shows the current worker's public text verbatim
after redaction, clamped to two lines. **Expand** reveals it; **Collapse** folds it. Each newer update
replaces the preview without moving or duplicating Conversation. Prose has its source age or **Time
unavailable**; a separate row shows recorded output and its age. After 60 seconds without output it
reads **Last update** and **No new activity for …**, with a neutral dot. Activity never proves useful
progress. **No public update yet.** covers empty commentary; **Activity unavailable** labels a retained
last known update and offers **Retry activity**. Generation changes clear previous words. Short
viewports fold the words and age until expanded, keeping activity and controls reachable. **View live
session** opens older output under existing retention. Questions, decisions and results stay durable;
there is no copied preview archive, duplicate reply, summarizer call or hidden reasoning.

Below 1280px the live session panel follows the §2.2 rule for the work panel: an overlay from the
header's panel button, scrim behind, Esc or the scrim closes it; the `live` route opens it on desktop
too. Phone anatomy: one 54px header with Back, title, concise L2 state and independent **Merge held**
status; a two-tab row **Conversation | Live session** (the `live` route selects the second); content;
activity, Stop and the shared compact composer on Conversation. Live session has the same
Stop/Continue controls. The title and details button open a scrollable sheet
with full title, attempt/context/tokens, PR/checks, complete block/hold reasons, and existing
Reject confirmation and operational Resume. Desktop retains its direct operational actions and
live-panel control while disclosing long reasons. Closing details restores the opener, draft,
selection and reading position. A failure remains visible, not only inside details.

Compact task states include **L2 · Running**, **L2 · Waits for L3**, **L2 · Needs your answer**,
**L2 · Blocked by a fault**, and **L2 · Paused**; **Merge held** can accompany any of these. Details
separates each full reason. Waiting on L3 adds no operator badge. An operator question remains at
its chat anchor with View question/Latest messages when applicable, and no generic Resume while
the question is open. A fault retains a visible short cause and **L3 has been told**. Operational
pauses without questions retain Resume/Reject. No disclosure or reply releases a merge hold.

Navigation states: Conversation and Live session are local views of the same task. Phone tab
switches replace its current history entry, keep the originating shell tab, and update the URL;
the desktop panel button adds no history. A `/live` deep link and reload select Live session on
phone and open the desktop panel. Back in the phone header and the desktop crumb both return to
the actual preceding in-app page, including its query string. With no in-app predecessor they
replace the task entry with the owning project's L3 conversation. Browser/system Back remains
native; Forward restores the task's latest URL, and other pages/tasks keep ordinary history.
On phone, Live session removes the conversation and composer; their draft and selection remain
when returning. A refused or uncertain send arriving while Live session is open restores its text
alongside newer draft edits; an accepted send remains sent. Conversation removes the live
panel. On desktop, closing the panel leaves the conversation visible. Navigation itself has no
loading, listening, denied or error state; destination reads and composers retain their states
specified here and in §3.6. `web/e2e/task-navigation.pw.ts` walks entry from L3 and Work, repeated
toggles, reload, Back, Forward and direct-live fallback on phone and desktop.

The conversation includes L3 messages as prose with a small "L3" label. Its composer says "Message
the L2"; the hint reads "Reaches the L2 at its next checkpoint." while running, "Delivered when
Altitude resumes the L2." while held for resume, and "Sending resumes the L2 with your message."
for another blocked task. Confirmed Stop reads **Send a correction to continue this session.** A
refused or unconfirmed send uses the shared composer states in §3.6, preserving newer draft edits.
Accepted messages stay sent through wake or refresh errors. The composer appears for running and
blocked tasks.
The existing message bubble shows **Queued · waiting for a checkpoint**, **Queued · held until you
continue**, **Delivered to session** only with handoff evidence, or **Delivery unconfirmed** when
evidence is missing. Each eligible queued operator bubble has **Remove**; quick-choice receipts and
messages already used by recorded decisions keep their evidence. **Removing…** disables removal until
the response; success replaces only that bubble's text with **Message removed** and **Removed · not
sent to the session**. Original text remains in durable evidence. Claim shows **Sending to session ·
cannot remove**, and uncertain handoff shows **Delivery unconfirmed · cannot remove**, with no Remove.
A prelaunch failure restores the queued controls. A refused removal refreshes delivery and names the
refusal beside that message; denied and unconfirmed requests show their own inline error. Saved or
loading reads disable removal. The empty queue has no removal control; listening and transcription
keep the existing composer behavior. Removal does not undo a lifecycle request or recorded decision.
`web/e2e/queued-messages.pw.ts` walks queue, removal, handoff, recovery and failure states on phone and
desktop; `l2-progress.pw.ts` covers listening, denied microphone and Stop states.
Delivery does not claim understanding or action. Finished conversations remain
readable with the activity area, composer and Stop gone.

The live transcript has tinted prompt blocks, the worker's prose, compact tool rows with folded
output, subtle timestamps, and the lifecycle boundaries the record supplies (state transitions,
stops, holds). A shell command's tool label is "$"; other rows use the recorded tool name, including
Edit for a file change. The hint reads "N lines", "running…", "error", or "no output"; write rows
carry no diff counts. **Raw events** toggles the transcript to the raw list; its hover title states
the server's redaction rule. There is no transcript search field. Footer states are "Following live
· new steps appear at the bottom", "Paused · Follow to catch up", "Session paused until the task
resumes", or "Session ended"; Pause and Follow control following while the worker runs.

Data: `GET /api/task/<project>/<slug>`, `GET /api/transcript/<project>/<slug>`,
`GET /api/overview` (engine labels, decision card and queue), `GET /api/project/<project>`
(repository URL), `POST /api/l2/message`, `POST /api/task/action`.

States: loading (header and conversation skeletons); error ("Could not load the task." and Retry);
planned (the wait reason replaces the live panel; the conversation accepts messages without release);
queued ("Waits for dispatch" or "Waits for resume" replaces the live panel); running; blocked on the
operator (the question inline at its recorded message anchor); blocked on L3 ("Waits for L3"
with the full reason in details); blocked by a fault (a red line with the first sentence, at most 100 characters,
and "L3 has been told"); held for resume (Queued chip, "Waits for resume · <reason>" in place of
the session); done or rejected (read-only conversation, composer gone, PR chip in the header).
Empty conversations read "No messages yet." on an active task and "No messages on this task." on
a finished one. The live session is connecting (skeleton and "Connecting to the session…";
recorded lifecycle boundaries stay visible), streaming, paused, ended, or unavailable ("No session
file for this attempt").

`web/e2e/l2-progress.pw.ts` walks phone and desktop with deterministic fixtures for both engines:
loading; replacing/expanded/quiet/untimed/unavailable activity; queued/unconfirmed/delivered steering;
direct Live session Stop; stopping with editable draft and racing messages held; stopped; correction
and capacity wait; continued session with preserved work; blocked question; finished; denied Stop;
short viewport; voice listening/cancel/dictation/denial/unavailable; input and overlay Escape ownership.
Each named state has a screenshot under `web/ui-artifacts/results/l2-progress*`.

### 3.11 Project switcher (phone)

A sheet from the header name: managed projects with dot and count, unmanaged folders, **Add a
folder**. Tapping a project selects it and closes the sheet; the Chat and Work tabs follow. Hidden
chevron and no sheet when exactly one project is managed and no folder is unmanaged.

### 3.12 First run and project setup

Shown on any project route when no project is managed: a centred card, "Altitude found N folders
under <root>", one row per folder with **Add project**, and a path field for a folder elsewhere.
States: scanning; none found (the path field alone); adding; failed (one sentence and Retry,
shown when registration is refused). Successful registration opens the project's Setup checklist
immediately. Closing the checklist never cancels accepted setup.
For a removed project with retained history, `POST /api/project/add` reports `restored: true`.
Its conversation and saved queue remain; setup reuses healthy configuration. Historical replies
or errors do not determine a new first conversation's outcome.

**Setup** stays in the project header with **Checking**, **Ready** or **Needs attention**; unavailable
reads say **Unavailable**, and non-Git projects say **Conversation ready**. It opens
a focused desktop overlay or full-height phone sheet with folder, repository, instructions, Git
guards and coordinator rows. Problems appear before healthy checks. Text and icons distinguish
pending/running, completed, reused, not applicable, failed and input-needed results. Completed rows
have green checks and readable labels. No percentage, simulated progress or second attention count
is introduced. A non-Git folder says conversation is available and Git tasks are unavailable.

The concise explanation reads: **Setup runs automatically. If a step fails, L3 can help.**
**Check again** refreshes observations. **Retry** repeats supported programmatic setup;
**Discuss with L3** opens the existing project conversation without sending or launching a new agent.
Notification, investigation and verified repair remain distinct. A step becomes complete only when
programmatic checks confirm it. Existing projects show newly applicable requirements without
reattachment. See [setup behavior](../../docs/SETUP.md#project-setup-and-repair) for responsibilities.

A custom-hook conflict offers **Review integration**, showing the original directory/events and
explaining that trusted custom hooks and their dependencies run with Altitude's Git permissions,
outside the task agent's sandbox. It explains **Use both hook sets** and
**Keep current setup**. Integration requires an explicit operator choice for the inspected hooks;
unsupported hook managers retain their configuration and offer discussion. Successful repair
removes its warning/action. Retry is unavailable while accepted work runs. Setup stays open at
completion, with **Open conversation** available.

Walkthrough states at phone and desktop: fresh registration, healthy/reused project, newly missing
requirement, stale guards, custom-hook review, active progress, interruption, failed/denied repair
and successful retry. Also cover empty folder discovery, initial loading, failed read with Retry,
offline observations and non-Git outcomes. Status changes use a polite live region, errors an alert,
and controls 44px targets. The sheet contains focus and scrolling, closes with Escape/Back, and
restores focus to Setup. Conversation/draft and reading position survive. Listening belongs to
the existing conversation opened by **Discuss with L3**, including its recording/cancel/error states.

### 3.13 Restart banner

On every route, above the phone header and first in the desktop main pane, when a merged change
awaits activation: "Merged changes to <what changed> are waiting to activate.", where what changed
is "the backend", "the web app", "the backend and the web app", or "Altitude"; the file count; and
"landed 2h ago" with the exact time on hover. The next line says
"Altitude restarts at the next quiet moment." and appends "Waiting for <list>." while it waits.

The quiet point has no dispatch or resume claim, L3 turn, or report verification in flight; running
workers do not hold activation. **Restart** appears when the waiting list is empty and no restart
is under way. Pressing it or receiving a recorded restart request removes the button and changes
the line to "Altitude is restarting…". A failed activation reads "Automatic activation did not
complete; L3 has the fault." The banner leaves when the new process answers without a pending
restart. Data: `GET /api/overview` `restart`; the button requests `POST /api/restart`.

On phone this is a compact summary with **Details** and the same available **Restart** action.
Changed area, file count, age and quiet-point wait reasons expand on request. A failed activation
or request remains explicit. The details can wrap and scroll; no failure or permitted restart
action is concealed by the compact presentation. Desktop retains the fuller summary.

### 3.14 Monitor

Anatomy: **Monitor** title; **Seats**, one card per configured engine in the API's order and under
its label; **Routing now**; **Sessions (N)**. Each seat shows the windows it reports, their
percentages and reset times in relative and clock terms, a meter with the 70% reserve line, the
plan when supplied, and "reading 3m old". Exact reading times appear on hover.

Routing rows show the role, project and pin separated by "·" ("L3 · <project> · Auto" or "L3 ·
<project> · pinned to <engine>"); the chosen engine is right-aligned in semibold, or "No engine"
in `--danger`, with the router's reason below. Rows wrap within the card. Session rows show their
kind and task, engine and model when supplied, context meter and recorded status, and a snapshot
age such as "3 min ago". Sessions are the process information on this page; there is no raw worker
process list.

Each L2 session adds one **L2 usage details** disclosure. Collapsed, it shows only the control;
helper counts, token totals and audit rows are absent. Expansion shows the shared task token readout
(§3.10), with **L1 helpers observed** first. Task/provider totals include helper accounting; the
helper sum is a separate attributable lower bound, never an additional total. Native identity,
parentage, owner attempt context and counters wrap on phone and desktop; expanded Monitor rows use
page scrolling. Collapse removes the details and a refreshed observation keeps the disclosure open.

Helper states: available request counters; partial discovery with counters missing for some helpers;
unknown evidence (**Helper evidence unavailable**, counts/tokens Unknown); observed empty (**No
helpers observed**, still partial, never zero spawned); unreadable collection (retained observations
and the collection-unavailable note). Whole-page loading hides disclosures behind the existing
skeleton; transport failure hides rows and offers Retry, which restores collapsed disclosures.
There is no separate expansion fetch/loading state, listening state or permission action. A denied
native source is unavailable evidence. `web/e2e/monitor-helpers.pw.ts` walks expansion/collapse and
these read states with named captures at 390×844 and 1440×900; existing monitor walks no sessions.

States: loading (seat, routing and session skeletons); error ("Could not read the monitor." and
Retry); no reading ("No reading." with the seat's sentence explaining what produces one); stale
(reading kept, amber Stale chip using `--chip-claimed-*`, meter at 50% opacity); one configured
engine (one seat card, no empty column); no routing ("No roles to route."); no sessions ("No live
sessions.").

Data: `GET /api/monitor` `seats[]` identifies each seat by its configured engine and supplies its
label, windows, plan and reading metadata; `routing[]` and `sessions[]` supply their rows.
L2 `sessions[].token_usage` is the same persisted accounting snapshot as task/status/report reads,
including `helpers`; opening Monitor starts no provider collection. Archived tasks retain the
breakdown on their task/report pages rather than appearing as live Monitor sessions.
`GET /api/overview` `engines[]` supplies routing and session engine labels. Monitor derives no action.

## 4. Behaviour rules

### 4.1 System turns fold

Every chat row whose `trigger` is not `chat` renders as a system line, never as bubbles. The line
text is the last paragraph of the turn's assistant row; while no assistant row exists the line reads
"L3 is handling <what>", where <what> comes from the trigger ("a landed report for <task>", "a
block on <task>", "a fault on <task>", "the restart"). An active turn stays outside the group so
its current handling line remains visible. A selected L3 FYI (`trigger == "fyi"`, `role == "system"`,
`heads_up == true`) also stays visible as its full concise text, splitting the routine runs around it.
Consecutive completed routine system turns with no chat, active turn or selected heads-up between
them collapse to one line: "L3 handled N system events between your messages", expanding to the list.
Other FYIs are ordinary system lines (`trigger == "fyi"`, `role == "system"`, no reply), eligible
for grouping, including automatic fault details and historical rows without explicit selection. A
clean report closes without an L3 turn and produces no line; the task simply moves to Done this
week. This is a rendering rule over data the chat log already stores; slice 2 adds the two trims in
§5.2 so the expanded view reads well.

### 4.2 One conversation, in order

Messages sent while L3 is mid-turn queue and run at the next turn boundary in order; the composer
keeps its accent circle with the arrow. Phone names the active work in the header and the run order
on queued rows; desktop also shows "L3 is mid-turn · runs next" under the field. Queued rows stay
inside the message area until they run, with Remove available while permitted. A running turn shows
either a system line in progress (§3.4) or, for a `chat` turn, a typing indicator under the
operator's bubble. `GET /api/chat` is the authority for what is running and what is queued; the UI
polls it and never guesses.

### 4.3 Discuss and decide in the owning conversation

Every dilemma opens its owning L2 chat. Follow-ups and unclear answers stay open; a clear decision
is sufficient for the L2 to record the source, outcome and scope and proceed. Closing an obsolete
dilemma records why it is unnecessary, without approving its abandoned recommendation. Partial
answers keep only relevant outstanding parts. Quick acceptance records the explicit chosen option;
grouped selections record only the named members and one normal operator message atomically, then
use the existing wake path. Neither a discussion wake
nor a generic resume authorizes implementation of the disputed approach or releases a merge hold.

### 4.4 One global attention count

Only global Needs you carries a numeric attention badge, across one or several projects. On phone
it appears in bottom navigation, or the header link while keyboard use hides that navigation; the
two are never visible together. Its unit
is one unanswered operator question plus each existing operational attention item. Two questions
on one task count as two; answering one reduces the badge by one. A known zero hides it. Project
rail/switcher rows retain their state dots, and Work has no attention badge. Task totals remain
labelled text. Running tasks, FYIs and queued messages do not add attention.

Needs you says **N questions across N projects**; operational items, when present, are named
separately. Project summaries use the same distinction. Loading and failed reads never imply
zero; cached counts are labelled stale until refreshed. Follow-ups retain counts, partial answers
remove only answered members, and final answers remove that task's card. Those changes do not
claim that its worker resumed.

### 4.5 Copy

Sentence case everywhere; the only uppercase is a count badge. L3's chat text carries no ids, paths,
or JSON; identifiers live in chips, links, and the expanded system card. Say "the operator" in fixed
UI text and show the configured name where a name is shown. Engine and model names are whatever the
engine seam reports. Ages read "N min ago", "2h", "yesterday"; exact times appear on hover or in the
expanded card.

### 4.6 Boards and spec stay aligned

The boards and this document describe the same visual design and rules. Accepted departures are
folded into the matching section and board. An unresolved change to an explicit rule is a question
for the operator, asked as one plain dilemma; the answer is recorded in both before implementation.
Keep the maintained set simple and current, replacing obsolete studies rather than accumulating
per-PR boards or galleries. Review captures and routine renderings stay outside Git; the
[project UI rule](../../AGENTS.md#ui) and [walkthrough guidance](../../docs/DEVELOPMENT.md#browser-walkthroughs)
retain phone/desktop state verification and accessible review evidence.

## 5. Data binding and backend notes

### 5.1 Reads and writes per surface

| Surface | Reads | Writes |
| --- | --- | --- |
| Rail, Needs you, badges | `GET /api/overview` | `POST /api/project/add`, `POST /api/project/remove` |
| Project conversation | `GET /api/chat/<project>` | `POST /api/chat` (message, queue, engine pin), `POST /api/chat/remove`, `POST /api/l3/reset` |
| Project setup | `GET /api/setup/<project>` | `POST /api/project/setup` (check, repair, operator-approved hook integration) |
| Work panel | `GET /api/project/<name>`, `GET /api/overview` `queue` | none; rows open the owning conversation |
| Needs you | `GET /api/overview` plus project reference context | `POST /api/decide` |
| Task page | `GET /api/task/<project>/<slug>` (questions and messages), transcript on demand | `POST /api/l2/message`, `POST /api/decide`, `POST /api/task/action` |
| Report view | `GET /api/task/<project>/<slug>` (structured report, report notes and digest) | none |
| Composer voice | `POST /api/transcribe` | none; audio is deleted after transcription |
| Monitor | `GET /api/monitor` | none |

The shell's one `GET /api/changes` stream refetches the mounted overview, monitor, project and task reads
when records change and on every (re)connect; polling continues underneath.

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
3. **FYIs are chat rows.** `tasks.fyi` appends `{role: "system", trigger: "fyi", slug, text, by,
   heads_up}`. Internal calls default to the daemon actor; explicit L3 calls through `alt fyi`
   record selection as `heads_up: true`. Ambiguous historical authorship does not imply selection.
   There is no project inbox file or `fyis` field in the digest or overview.
4. **Turns name the tasks they created.** The assistant chat row of a turn that created a task
   carries `tasks: [slug]`, written by the server when the turn's task creation lands.
5. **Dilemmas have durable identity.** Task `questions` contains ID/revision, message anchor,
   question/options/recommendation, source/audience and resolution. `question_group` projects up to
   three open members plus closed history, its revision and stable anchor. Task `question`, history and Needs you
   project the same source independently of worker state. Blocks and escalations publish attributable
   human context; the owner handoff names question and source-message IDs.
6. **Resolution cites actual authority.** `POST /api/decide` saves responses naming the exact current
   question/revision and an option or custom text, or a group revision and those member responses.
   The whole batch is validated before writing one operator message; it closes no decision.
   `response` exposes its text, time and source-message ID; attention excludes submitted members.
   `alt task resolve` cites original authority for an answer or supersession;
   `withdrawn` records only the owning L2's reason. Re-asking creates a new member without replacing
   independent questions. Stale/conflicting writes fail; identical retries
   reuse the receipt and repair interrupted delivery. A remainder publishes a new revision without
   an inherited default. L3-authored prose cannot impersonate operator approval. The existing task
   lock, inbox/resume and provider conversation remain the supporting machinery.

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
| 3 | **Work and decisions.** §3.5, §3.7, §3.8, §3.9; backend notes 3, 5, 6; FYIs fold into the chat and `inbox.jsonl` goes. | Needs you and the owning chat accept answers; Work lists all current tasks once and opens their conversations. The global badge reflects unanswered questions and operational items; `digest.fyis` and the overview `fyis` field are deleted. |
| 4 | **Task page.** §3.10 on desktop and phone, direct Stop, Reject with inline confirm, live session panel toggle. | Both tabs work on the phone; a blocked task shows its card inline; Raw events stays behind its toggle. |
| 5 | **Monitor and banner** in the new shell (§3.13, §3.14). | Seats, routing and sessions use the shell; Restart appears at the quiet point defined in §3.13. |

Not drawn and not scheduled: settings and a Done view beyond the folded list. They are questions
for the operator when they come up. Project removal (L3 detachment) uses the overflow menu (§3.2).

## 8. Compact mobile chat

The rules in §2.2/3.2/3.3/3.6/3.10/3.13/4.2 combine the phone's identity and status in a
54px header, use one 70px composer dock with integrated 44px mic/send controls, disclose routine
metadata and engine selection, and remove routine hints on phone. Hide the 84px bottom navigation
only during detected software-keyboard use; restore it on dismissal, retaining draft and selection.
Task Conversation/Live session tabs remain visible. A long draft grows to 120px or 25% of the
usable visual viewport (minimum 44px), then scrolls internally. Bottom-follow and older-message
anchoring survive changes in available height. Browser-managed safe areas remain intact.

Activity and nonzero decision counts remain reachable; actionable failures, question controls,
queue removal, voice guidance and available Restart remain explicit. A compact update summary
discloses detail. Task metadata, Reject with confirmation and operational Resume open in task
details. Stop and Continue stay directly accessible beside the composer and in Live session,
as specified in §3.10. §3.8–3.9 and CONVERSATION_FIRST.md decision semantics remain authoritative.

Task states include running+held, waits-for-L3, waits-for-L3+held, operator-question+held,
fault+held and operational pause. One compact status names waiting and merge state
separately; the complete reasons open in scrollable task details. The original question remains
in chat with View question/Latest messages when applicable. Faults retain a visible cause and
L3 notification. Open questions do not acquire a generic Resume, and merge restrictions never
acquire a release action. Desktop keeps directly available operational actions while disclosing
long reasons. Phone sheets fit above the keyboard; closing them preserves draft and reading
position. Application walkthroughs in `web/e2e/mobile-chat.pw.ts`, `task-details.pw.ts` and
`conversation.pw.ts` cover keyboard restoration, draft growth, scroll anchors and all six task-state
combinations at phone and desktop sizes. Named screenshots stay outside Git under `web/ui-artifacts/`.
Browser simulation establishes layout and application transitions; native keyboard, toolbar and
safe-area behavior require real-phone acceptance.
