# Altitude UI specification

The operator approved [conversation-first Needs you and L2 decisions](CONVERSATION_FIRST.md)
on 2026-09-08. The conversation-first boards define the decision experience; shared shell and
composer boards define their existing layout and input behavior.

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
- A task conversation opened from a phone tab pushes over that tab with a back control
  and keeps the tab bar.
- No viewport ever scrolls horizontally; transcripts and tables scroll inside their own container.
- The shell fills the visual viewport and never scrolls or bounces. Headers, the tab bar and composer
  stay docked; content and transcripts own native scrolling and bounce inside their containers. The
  shell follows changes to the visual viewport, including the phone keyboard.
- Breakpoint constants live in one place in the web code and are the only place widths are named.

### 2.3 Scope rule

The selected project is a UI state persisted per browser (localStorage), set by the rail, the
switcher, or a project route. Needs you cards and rail badges are the only cross-project data on
screen. Opening a card selects its project.

Switching projects opens that project's conversation and discards the unsent draft and transient
composer/response state. Accepted turns and waiting messages remain owned by the source project;
switching back reads its saved history, queue and active turn (§3.3). No draft is saved on leaving.

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
| State dot | running (accent: a running L2, a task blocked waiting on L3, or a landed report L3 is handling); waits for the operator (`--data-claimed`: a decision in the queue); blocked by a fault or stopped (`--danger`); idle, nothing active (`--text-muted` at 45%) |
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
| Typing an unsent draft | Beta's composer is empty. Returning to Alpha does not restore its unsent draft. |
| Send pending or reply streaming | Alpha's local prompt, typing indicator and streamed text leave. The accepted turn finishes in Alpha; Beta can send independently. |
| Both projects have sent a turn | Each conversation shows only its own turn. Either completion order preserves the other project's draft and reply. |
| Late HTTP refusal or stream error | Beta's draft and send state stay its own; no Alpha error or Retry appears there. An unaccepted draft is not saved after leaving. |
| Switch back to a failed accepted turn | Alpha's stored prompt and failed-turn Retry appear only in Alpha; Retry resends that prompt to Alpha. |
| Listening, transcribing or microphone denied | Leaving stops recording, releases microphone tracks and cancels transcription; late results cannot fill Beta's draft. The new composer has its own microphone state. |

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

The report view has a back link to the task and a "Report" title. It reads the task's report and
shows plain sections when present: Landed (PRs, main checks and deploy), Review, Blocked, Decisions,
FYI, Follow-ups, Deviations, Spend, Report notes, and Digest. Report notes and the digest are prose;
Digest links land at its section. States: loading (a title-shaped skeleton); empty ("No report
yet."); error ("Could not load the report." and Retry).

### 3.5 Task card (inline and in the work panel)

Anatomy: state dot, title (600), meta line "<state> · <engine> · <age or wait>", chevron. Click
opens the task page.

States by task state: queued ("Queued · <hold>", where the hold is the queue's own reason: "waits
for a slot · WIP limit N reached", "waits for an engine · <why>", "waits for the restart", "waits
for resume at <time>", or plain "waits for dispatch"; never a file lease, which the queue does not
hold; see [concurrency](../../docs/ARCHITECTURE.md#task-lifecycle)); running ("Running · <model> on <engine> · started N min ago"); blocked waiting
on L3 ("Waits for L3", the running dot: L3's answer is Altitude's own work, and the dot turns amber
only when L3 escalates to the operator; the rail's §3.1 dot follows the same rule); blocked on the
operator ("Waits for your answer", amber dot, red when the task was stopped mid-task); blocked by a
fault ("Blocked: <one sentence>", red dot); reported ("Report landed · waits for L3", running dot);
done ("Done · PR #N merged", shown under Done this week); rejected ("Rejected", under Done this
week).

The same card is the row in the work panel and the card under an L3 reply that created the task
(§5.2 note 4); a slug the project no longer lists renders as the row with the slug as its title.

### 3.6 Composer

One composer everywhere (project chat and task conversation). Anatomy: rounded
field (`--radius-composer`), placeholder naming the owner ("Message L3 about <project>",
"Message the L2"); a left pill (engine pin on L3 chat: Auto or an engine name; none on the task
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
| Engine pin | Auto, or an engine name | `POST /api/chat` carries the pin; it covers chat and system turns alike and stays until changed |

Voice is capped just under ten minutes: the client stops at 9:55 to stay under the server’s ten-minute limit, and transcription times out after 60 seconds.
The timer turns `--danger` in the last minute. Audio never becomes part of task or chat state.

### 3.7 Work panel

Anatomy: "Work" and "N active · N done this week"; **Needs you** (count) with compact decision
cards; **Active** (count) with task rows; **Done this week** folded to a count, expanding to rows.
On the phone it is the Work tab with the same sections.

Data: `GET /api/project/<name>` for the tasks, `GET /api/overview` `queue` filtered to the project.

States: loading (two card skeletons, three row skeletons); empty ("Nothing running. Ask L3 for
something."); a row's task
just changed state (the row moves sections with a 200ms fade). A task with a card under Needs you
has no row under Active; "N active" counts the rows. Done this week holds the tasks done or
rejected in the last seven days and is hidden when there are none.

### 3.8 Decision card

Needs you and the project work panel show one compact card per unresolved dilemma: task/project,
source, one question or up to three independent questions, and **Open L2 chat**. The model can ask
a plain question, give one recommended quick action, or offer two to three quick options with one
recommendation and concise rationale. Single-question choices act immediately. Group choices start
unselected; only actual picks have selection styling and remain staged until **Send N answers**.
When only one question remains, its quick choices act immediately in both list and chat.
With no manual picks, **Use recommendations**
answers only questions with an explicit recommendation; it never overrides a picked alternative.
The task title and card background open the same chat destination. Reference links remain ordinary
external links. Plain questions use chat; no inferred default exists. Operational stops and faults open the task's
ordinary controls. Discussions stay in chat, with no per-card follow-up fetch or mirrored exchange.
A dilemma remains answerable while a provider limit queues a fresh attempt: acceptance records the
choice, and the same chat queues replies with **Delivered when Altitude starts the L2.** The saved
question or receipt travels into the fresh brief. Queuing alone never closes a relevant question.

The recommendation body and acceptance action are the same component as the one at the question's
message anchor in chat. Loading uses a skeleton with no inferred count; empty Needs you says
**Nothing needs you.** A read failure offers Retry. Cached failure keeps saved cards with an explicit
refresh notice and disabled acceptance. During acceptance, the control says **Recording…** and
cannot be repeated. Saved answers disappear from the card and update counts; unresolved members
remain together. The card disappears when none remain. A brief
**Decision recorded** receipt links to chat. Failure keeps the question with Retry. Denied writes
require a refreshed read; a changed question requires reviewing its current revision.

### 3.9 Open the owning L2 question

`/projects/:name/tasks/:slug?question=<id>&revision=<n>` opens the owning human conversation at
that durable question's group, with preceding explanation visible. Every member link focuses the
same stable group anchor; the conversation renders the group once. L3 escalation text stays attributed to
L3 and contains the actual dilemma and recommendation. Later technical events do not change the
anchor. The live session starts closed when entering a question; **Activity & evidence** reveals
technical event summaries and the link to the existing live view.

The normal composer accepts a follow-up, a simple answer such as “14 days”, or a nuanced decision.
There is no recipient selector, note form or extra confirmation. A follow-up can wake the owner to
answer while the dilemma remains open. The L2 records a clear decision against its source message;
the UI never treats sending as approval. Ambiguity is clarified in conversation. A partial answer
closes answered members and keeps only relevant unanswered members. A partially answered member
retains its remaining scope in a new revision. A single typed reply can answer the whole group.
If the chosen direction makes the
remainder unnecessary, close it with a short reason instead of leaving stale questions open.

A resolved question retains its history and reason with **Decision recorded** or **Question closed**;
its obsolete acceptance disappears. Recorded acceptance and execution are separate observations:
show **Waiting to resume** while waiting for capacity, and **Work resumed** only after observing the
worker running. An old question URL stays readable and links to the current revision when one exists.
Superseded versions fold under **Earlier question**; linking to an old version opens its history.
An unavailable question is explicit and keeps the ordinary task conversation accessible.

Opening from Needs you pushes one task entry and retains the origin tab. App Back uses the existing
history entry and falls back to the project for a direct link. Conversation/Live session switches
replace that entry and preserve the same-task draft. Leaving the task clears its draft; a late send
stays bound to its original task. A new reply does not pull the reader away from the question:
**Latest messages** follows the bottom, and **View question** returns to an offscreen open dilemma.
Pending-question reads poll every two seconds. Stale navigation refreshes before acceptance, and
every write names its exact question revision. Archived tasks retain history without a composer.

### 3.10 Task page

The existing task header includes a compact **Observed tokens** disclosure on phone and desktop,
also present in the report view. The folded row shows the cumulative observed total (unknown when
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
scroll within the header on phone. `web/e2e/task-usage.pw.ts` walks these states at both viewports.

Desktop anatomy: header rows (crumb and actions; title with state dot; a muted line; state chips:
state, engine and model, PR with checks state, hold reason); left the operator's conversation with
the L2 (same bubbles and composer as §3.3 and §3.6); right the live session panel (480px, toggled by
the header button). The muted line reads "attempt 1 · started 32 min ago · 18% of its context used"
when those values are available; a finished task reads "done 2h ago" or "rejected 2h ago". Engine
and model appear in their chip. The PR chip reads "PR #N merged · main checks passed" or its open
and check states, in danger tone when main checks failed. It links to the PR when the repository
URL is known, otherwise it is a plain chip. A hold reads "Merge held · <reason>".

Actions **Stop** and **Reject** are quiet text buttons with inline confirmation; no browser dialogs.
Stop asks "Stop this task? Its worker ends; the branch stays." with Stop and Cancel. Reject asks
"Reject this task? Its worker ends and the task is archived." with "Reason (optional)", Reject and
Cancel. Stop appears while running; Reject appears while queued, running, blocked or reported.
An operationally blocked task without an open question also offers **Resume**, using the existing
daemon operation. The button becomes **Resuming…** during the request, then disappears when running.
A failed request leaves Resume available and places its error on a separate line under the actions,
including on the phone. Resuming an operational pause records no decision.

Below 1280px the live session panel follows the §2.2 rule for the work panel: an overlay from the
header's panel button, scrim behind, Esc or the scrim closes it; the `live` route opens it on desktop
too. Phone anatomy: header with back and the title; a dot-separated state line and Stop/Reject at its
end, confirmation below it; a two-tab row **Conversation | Live session** (the `live` route selects
the second); content; the composer pinned above the tab bar on the Conversation tab.

Navigation states: Conversation and Live session are local views of the same task. Phone tab
switches replace its current history entry, keep the originating shell tab, and update the URL;
the desktop panel button adds no history. A `/live` deep link and reload select Live session on
phone and open the desktop panel. Back in the phone header and the desktop crumb both return to
the actual preceding in-app page, including its query string. With no in-app predecessor they
replace the task entry with the owning project's L3 conversation. Browser/system Back remains
native; Forward restores the task's latest URL, and other pages/tasks keep ordinary history.
On phone, Live session removes the conversation and composer; Conversation removes the live
panel. On desktop, closing the panel leaves the conversation visible. Navigation itself has no
loading, listening, denied or error state; destination reads and composers retain their states
specified here and in §3.6. `web/e2e/task-navigation.pw.ts` walks entry from L3 and Work, repeated
toggles, reload, Back, Forward and direct-live fallback on phone and desktop.

The conversation includes L3 messages as prose with a small "L3" label. Its composer says "Message
the L2"; the hint reads "Reaches the L2 at its next checkpoint." while running, "Delivered when
Altitude resumes the L2." while held for resume, and "Sending resumes the L2 with your message."
for another blocked task. A failed send restores the draft and replaces the hint with "Not sent.
Retry." The composer appears for running and blocked tasks; finished conversations stay readable.

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
queued ("Waits for dispatch" or "Waits for resume" replaces the live panel); running; blocked on the
operator (the question inline at its recorded message anchor); blocked on L3 ("Waits for L3's
answer · <reason>" under the chips); blocked by a fault (a red line with the one-sentence reason
and "L3 has been told"); held for resume (Queued chip, "Waits for resume · <reason>" in place of
the session); done or rejected (read-only conversation, composer gone, PR chip in the header).
Empty conversations read "No messages yet." on an active task and "No messages on this task." on
a finished one. The live session is connecting (skeleton and "Connecting to the session…";
recorded lifecycle boundaries stay visible), streaming, paused, ended, or unavailable ("No session
file for this attempt").

### 3.11 Project switcher (phone)

A sheet from the header name: managed projects with dot and count, unmanaged folders, **Add a
folder**. Tapping a project selects it and closes the sheet; the Chat and Work tabs follow. Hidden
chevron and no sheet when exactly one project is managed and no folder is unmanaged.

### 3.12 First run

Shown on any project route when no project is managed: a centred card, "Altitude found N folders
under <root>", one row per folder with **Start L3**, and a path field for a folder elsewhere.
States: scanning; none found (the path field alone); starting ("L3 is starting…", then the project
page opens on its first reply); failed (one sentence and Retry).
For a removed project with retained history, `POST /api/project/add` reports `restored: true`.
First run waits for the registration response, then opens the saved conversation; historical
replies or errors do not determine the new start's outcome. Its saved queue resumes normally.

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
its current handling line remains visible. Consecutive completed system turns with no `chat` row
between them collapse to one line: "L3 handled N system events between your messages", expanding
to the list. An FYI is a system line too (`trigger == "fyi"`, `role == "system"`, no reply). A
clean report closes without an L3 turn and produces no line; the task simply moves to Done this
week. This is a rendering rule over data the chat log already stores; slice 2 adds the two trims in
§5.2 so the expanded view reads well.

### 4.2 One conversation, in order

Messages sent while L3 is mid-turn queue and run at the next turn boundary in order; the composer
keeps its accent circle with the arrow and shows "L3 is mid-turn · runs next" under the field; the
queued rows sit under the conversation until they run. A running turn shows
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
| Work panel | `GET /api/project/<name>`, `GET /api/overview` `queue` | `POST /api/decide` |
| Needs you | `GET /api/overview` plus project reference context | `POST /api/decide` |
| Task page | `GET /api/task/<project>/<slug>` (questions and messages), transcript on demand | `POST /api/l2/message`, `POST /api/decide`, `POST /api/task/action` |
| Report view | `GET /api/task/<project>/<slug>` (structured report, report notes and digest) | none |
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
5. **Dilemmas have durable identity.** Task `questions` contains ID/revision, message anchor,
   question/options/recommendation, source/audience and resolution. `question_group` projects up to
   three current members, its revision and stable anchor. Task `question`, history and Needs you
   project the same source independently of worker state. Blocks and escalations publish attributable
   human context; the owner handoff names question and source-message IDs.
6. **Resolution cites actual authority.** `POST /api/decide` accepts the exact current question/revision
   and explicit option, or a group revision and selected member/option references. The whole batch
   is validated before writing. `alt task resolve` cites an original operator message and records
   the chosen scope or why a question is obsolete. Stale/conflicting writes fail; identical retries
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
| 3 | **Work and decisions.** §3.5, §3.7, §3.8, §3.9; backend notes 3, 5, 6; FYIs fold into the chat and `inbox.jsonl` goes. | A recommendation is accepted from the panel or Needs you; its question opens the owning L2 chat, where follow-ups and decisions remain; `digest.fyis` and the overview `fyis` field are deleted. |
| 4 | **Task page.** §3.10 on desktop and phone, Stop and Reject with inline confirm, live session panel toggle. | Both tabs work on the phone; a blocked task shows its card inline; Raw events stays behind its toggle. |
| 5 | **Monitor and banner** in the new shell (§3.13, §3.14). | Seats, routing and sessions use the shell; Restart appears at the quiet point defined in §3.13. |

Not drawn and not scheduled: settings and a Done view beyond the folded list. They are questions
for the operator when they come up. Project removal (L3 detachment) uses the overflow menu (§3.2).
