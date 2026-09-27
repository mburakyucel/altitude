# Altitude UI specification

The operator approved [conversation-first Needs you and L2 decisions](CONVERSATION_FIRST.md)
on 2026-09-08. The conversation-first boards define the decision experience; shared shell and
composer boards define their existing layout and input behavior.
The operator approved [image input](IMAGE_INPUT.md) on 2026-09-10 for project and task conversations.

The operator approved answer-in-place question responses on 2026-09-14 Pacific: **Other…** opens
a small text field, plain questions show the field directly, and preset/custom responses share
one conversational handoff. Question fields omit microphones; ordinary chat retains voice input.

The operator approved the compact task header and phone swipes (§3.10) on 2026-09-23: uniform
header actions, a title dropdown for details, and conditional floating question/latest jumps. On
2026-09-24 the operator refined the swipes to track the finger continuously (§3.10 Navigation states).

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
without feeling lost. Intuitiveness is a first-order requirement, not a finish: anything clickable
is immediately recognisable as clickable at rest, before hover or focus. This central project tenet
guides future iterations as the visual direction evolves.

In the existing design review and [phone and desktop walkthrough](../../AGENTS.md#ui), check that:

- Visual hierarchy makes the primary action clear; navigation and plain labels show where people
  are, where they can go, and what an action does.
- Every clickable control reads as clickable at rest. The only or primary action in an area is a
  bordered or filled button; a borderless ghost button sits only beside a visible bordered or
  filled action; a text link is underlined. Plain text beside muted meta lines is not a control.
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
| `/projects/:name/tasks/:slug/terminal` | the same page with the task's terminal in front (phone tab, desktop panel view) | new |
| `/projects/:name/terminal` | the project with its folder's terminal in the right panel; full screen on phone | new |
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
  are global. The header shows the project name with a chevron on project tabs and the mark (22px, §3.1)
  with "Altitude" on global tabs, so scope is always readable. The project name opens the switcher sheet (§3.11).
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
  Inline text controls (Show, fold summaries, the alerts switch, back links) keep their line and
  reach 44px touch targets.
- Ordinary use never changes page scale: focusing, typing, switching fields, sending and dismissing the
  keyboard keep the zoom level, so touch layouts render fields at 16px. Manual pinch zoom stays
  available everywhere, including mocks and design previews.
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

The brand is the Altitude mark, 24px, then "Altitude" in card-title type. The mark is an A whose left
side climbs in three steps (L1, L2, L3) to the summit, drawn as a white line on an `--accent` tile
with a 7/32 corner radius, so it follows the theme. It is decoration beside the name, not a link.
`web/src/shell/BrandMark.tsx` draws it in the app. `web/public/altitude-mark.svg` is the same drawing
in the light accent, used as the browser-tab icon and in the README. `web/public/apple-touch-icon.png`
renders it at 180px with square corners, because iOS rounds Home Screen icons itself. The ICO
fallback contains 16, 32 and 48px versions of the rounded SVG tile for browser/bookmark surfaces.
`web/public/manifest.webmanifest` names Altitude, uses `/` as its identity, start URL and scope,
and requests standalone display. Its ordinary 192/512px icons use the rounded tile; its separate
512px maskable icon has an opaque accent background and the same glyph scaled to 80% about the
center. All white strokes stay inside the centered 40%-radius safe circle so launcher masks can
crop only background. Installed icons retain the light accent in either app theme. No offline
mode is implied. `web/e2e/brand.pw.ts` walks both themes and previews ordinary, Apple and masked
icons at phone/desktop widths; native launcher behavior is not simulated as device acceptance.

Data: `GET /api/overview` (`projects[].managed`, `projects[].counts`, `projects[].l3`, `queue`,
`quota`, and the second engine's windows). Engine names come from the engine seam; the rail never
hard-codes one, and one configured engine means one row.

| Element | States |
| --- | --- |
| Project row | selected (tint background, `--text-primary`); unselected (`--text-secondary`); hover (tint at half) |
| State dot | running (accent: a running L2, a task blocked waiting on L3, or a landed report L3 is handling); waits for the operator (`--data-claimed`: a decision in the queue); blocked by a fault or stopped (`--danger`); idle, nothing active (`--text-muted` at 45%) |
| Needs you badge | unanswered operator questions plus operational attention items across projects; hidden at known zero; unknown or stale reads are explicit |
| Unmanaged folders line | N folders found in the projects folder; click opens First run for the picked folder; hidden at zero |
| Engine readout | one row per engine: name, "N% of week", a 4px meter; the meter turns `--danger` past the 70% reserve line; "no reading" in muted text when `quota.known` is false; "reading 2h old" appended when `stale` |
| Operator row | name from configuration; theme toggle (light default, dark, persisted per browser) |

### 3.2 Project header

Desktop anatomy: project name (18px, 600); status line; actions: permanent **Setup** status (§3.12),
**Terminal** (pressed while the project terminal shows, §3.10), work-panel toggle (tinted when the panel is
open, hidden at ≥ 1280 where the panel is inline), overflow menu. On phone **Terminal** is an icon
button before the three dots.
Vertical padding is 6px; text and control sizes retain their normal dimensions.

Status line, composed left to right and separated by "·": "L3 answered N min ago on <engine>"
(from the last assistant chat row's `at` and `engine`); "N tasks in flight" (running + queued);
"N questions need you" and "N operational items" (labelled separately; omitted at zero). While a turn runs the first
part reads "L3 is answering" (or "L3 is handling <what>" for a system turn, §4.1).

The phone combines project identity and a short **L3 Ready / Answering / Handling** status in its
single 54px shell header. The project name opens the switcher; a non-Auto pin stays named in the
compact status. There is no second status row. The three dots open the same project actions menu as
desktop, above any keyboard. The engine pin, the last L3 turn and model/effort defaults live on the
project's Settings page (§3.15).
**Setup** remains discoverable in the project header on phone and desktop, including healthy
projects. It opens the same configuration checklist without replacing the conversation or draft.

Overflow menu: **Settings…** (opens Settings with this project under **This project**), **Reset L3 conversation** (confirm inline; `POST /api/l3/reset`), **Remove project**
(confirm inline; `POST /api/project/remove`), **Design boards** (present only when `GET /api/project/<name>`
reports a design URL; opens in a new tab).

The menu holds actions only; no engine, model or effort control appears in it. Opening it focuses
the first item; arrow keys, Home and End move between items; an inline confirmation takes focus and
**Cancel** returns it to its item; Escape closes the menu and returns focus to the three dots.

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
the reading position; the question jump deliberately returns to the open question at the end.

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

One line, centred, 13px `--text-muted`: a dot, the turn's recorded time, the text, and **Show**. The
time reads "14:05", dated ("Sep 16, 14:05") when not today, exact on hover; a turn without one reads
**time unavailable**. A group line shows its latest turn's time. It stands for one system turn
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

#### Cross-engine review in the task conversation

The existing task menu offers **Review proposal** and **Review changes**, one bordered button per subject.
An existing request changes its entry to **View proposal review** or **View changes review**, with
Requested, In progress, Complete, Earlier version, Failed or Cancelled underneath. Opening any existing
review shows saved status, findings and L2 dispositions in the conversation and never invokes a reviewer,
including L2-initiated reviews. **Review again**, **Review latest** and **Retry** are explicit actions
inside details. A new revision requires the previous request to be addressed. A proposal review remains available when changes
review is requested. No permanent review button, card, tab or separate reviewer conversation appears.

Before requesting, show the selected engine/model, configured allowance (including unknown), focused
read-only scope and merge wait. Prefer an eligible alternate engine; otherwise label the separate
same-engine invocation with its fallback reason, also retained in saved evidence. No duration selector,
programmatic deadline, automatic retry or engine switch follows launch. Capacity or unavailable
observation/cancellation cannot be bypassed by fallback.

L2 proactively seeks adversarial review for complex proposals before code and complex implementations;
simple work stays light by judgment. For proposal review L2 selects the exact original proposal message;
the operator sees its link and version, with no message-ID form. Missing proposal input is explained
before invoking a reviewer. Review can continue L2 while an approval question is open solely to prepare,
run and assess the proposal review; the original question stays open. Captured text and committed source
define coverage; images require a textual account and remain explicitly outside text review coverage.
Proposal findings never imply implementation acceptance. Later proposal/source/context changes show their
coverage and need L2 assessment or a deliberate new review; pending changes requests remain visible.

A compact attributed system row tracks requested/running/completed/failed/cancelled/withdrawn state.
L2 explains useful findings and fixes in ordinary prose. **Review details** reveals original findings,
L2 dispositions, subject, selected proposal/context IDs and exact checkpoint evidence. It starts folded;
collapsing removes details. Current coverage, earlier work and later L2 assessment are distinguished even when
folded. No findings never means permission to merge. Failure keeps the request unresolved and exposes
explicit retry or authorized skip; uncertain termination retains capacity and explains recovery.

Empty history adds no conversation row. Loading/saving disables repeats. Unavailable explains why in
the menu; denied/uncertain delivery uses inline feedback and saved-status refresh. Menu dismissal,
details expansion and request delivery preserve the draft and reading position. Listening and voice
submission retain the composer journey. `cross-engine-review.pw.ts` walks these states at phone and
desktop widths; `cross-engine-review-integration.pw.ts` walks real persisted request, alternate-engine
fixtures, immutable snapshot/result, L2 initiation, failure/retry, unavailable, staleness and dispositions.

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
operator ("Your turn · N questions", "Your turn · review PR #N", or both, plus "Waiting for you",
amber dot); replying to the operator ("L2 replying to you", running dot); running ("L2 working");
blocked by a fault ("Paused · <one sentence>", red dot); operator-stopped ("Stopped by you", red dot);
owner/daemon-parked without a question, review, fault or operator stop ("Paused", idle dot);
reported ("Report landed · waits for L3", running dot, or "waiting for you" with a held review);
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
"Message the L2"); a left pill (engine pin on desktop L3 chat: Auto or an engine name; none on the
task conversation or on phone, where the pin is in the project's Settings page); Add images button; microphone button; send control. The send control is the arrow in an accent circle
in every state, with no visible text; its accessible name is "Send" ("Queue" while busy). A hint line under the field,
12px muted on desktop. The structure is the same on both widths: the field spans the box on top
and the controls (pill, Add images, microphone, send, and the recording cluster while listening or
transcribing) sit in one row beneath it, in every state. Phone fields and messages are 16px with
44px control targets; the row keeps its place through recording and landing, so the send control
never moves beside the text. No routine hint or engine toolbar adds a row on phone. Relevant send/access errors and voice/denied/unavailable explanations remain visible and
announced. A draft starts at 44px and grows to the lesser of 120px and 25% of the usable visual
viewport, with a 44px minimum, then scrolls internally. The control row stays under the field as it grows.
Pressing Send leaves focus in the field, so an open phone keyboard stays open with navigation hidden.

Keyboard: Enter sends (while listening, stops, transcribes, and sends at once), Shift+Enter inserts
a newline while editing, Ctrl/⌘+M starts the microphone or stops to the draft, Esc cancels voice input.
During voice input, keyboard, paste and cut cannot mutate the text; selection and copying remain available.
`web/e2e/conversation.pw.ts` and `project-isolation.pw.ts` walk these states, delayed success,
failure/cancel, Stop versus Send, independent project drafts and navigation during transcription
at both phone and desktop widths; `mobile-chat.pw.ts` walks the task page's pending and settled bubble.

| State | What is on screen | What changes |
| --- | --- | --- |
| Idle | placeholder, mic, arrow disabled | typing enables the arrow |
| Typing | draft text, arrow enabled | Enter or the arrow: the draft becomes a bubble at once, the field clears |
| Sending | the bubble is in the conversation at once at 60%, a small progress ring beside it, until the server acknowledges it (stream accepted, queued receipt or stored row); the task page's receipt line reads "Sending…" | accepted: the same bubble settles to full opacity in place over 240ms, the ring leaves, and the stored copy replaces it without a duplicate row, re-layout or scroll jump; refused: the bubble leaves, the draft returns, hint reads "Not sent. Retry." in `--danger` |
| Accepted; stream or refresh interrupted | sent bubble or saved queue row; the composer stays cleared and newly typed text stays | refresh reconstructs history, active turn and queue by their IDs; read-error Retry only reads; no unsent Retry or invented answer failure |
| Delivery unconfirmed | submitted text followed by any newly typed draft on a new line; hint reads "Could not confirm delivery. Check the conversation before sending again." | no send Retry; the operator checks history before editing or sending; HTTP headers, server errors and matching text alone do not prove delivery |
| Busy (L3 mid-turn) | the same arrow, enabled with a draft; header names the active work and queued rows say what runs next; desktop retains its mid-turn hint | the arrow appends to `queued[]`; a queued row appears in the conversation in muted text with a 44px **Remove** target on phone (`POST /api/chat/remove`) |
| Opening microphone | "Opening microphone…" with an indeterminate spinner inside the composer box; existing text remains readable and read-only. A restart waits for recognizer shutdown (at most three seconds), followed by completion of the waveform audio context's asynchronous close, before opening another microphone | Cancel or Esc restores editing and prevents the waiting attempt from opening audio later; denial or failure preserves the draft |
| Listening | Read-only, selectable draft; "Listening… Stop to add text, or Send." with activity indicator inside the box. With the browser backend, recognized words appear after the draft while speaking and the last phrase may still change; English phrases gain punctuation and capitals once final, while the phrase being heard shows as heard; once the text passes the field's height, the field follows the latest words. Cancel, Stop, arrow, waveform and timer share one control row: on desktop they sit together at the right beside the engine pill with a crisp 168px waveform; at 390px the waveform fills the row without wrapping | Cancel or Esc: back to editing, nothing added; the X leaves focus on the microphone so no phone keyboard opens, and Esc returns focus to the field; Stop or Ctrl/⌘+M: land (browser) or transcribe (server backends) to the draft; the arrow or Enter: land or transcribe, then send at once |
| Transcribing | "Transcribing…" and an indeterminate spinner inside the box; draft stays readable and read-only, mic and arrow disabled, Cancel available. Desktop waveform and timer freeze. Server backends transcribe the upload here; the browser backend only waits, at most three seconds, for the recognizer's last phrase and then, at most ten seconds (three while the model still loads), for its punctuation | after Stop: Landed; after Send: append and send once through Typing → Sending (Busy queues); Cancel, failure or timeout restores editing and preserves the draft; failure: "Could not transcribe. Typing works.", and a recognizer error keeps the words already shown; empty transcript: send nothing, return to Idle or Typing |
| Landed | the transcript is appended to the draft, cursor at the end, arrow enabled; nothing else appears (no transcript box, issue #195). When English punctuation did not finish, the words land as heard and a muted hint stays until the next capture or send: "Added without punctuation: still loading. Next time it will be ready." while the model is still downloading, otherwise "Added without punctuation: this browser could not run it." | the operator edits or sends as with a typed draft |
| Denied | mic stays available; hint reads "Microphone blocked in the browser. Typing works." (microphone or recognizer refused) | the hint stays until the next tap, which asks the browser again; a lasting block shows the hint again |
| Unavailable | mic hidden; hint reads "Voice needs HTTPS" on an insecure origin, "This browser has no speech recognition. Typing works." when the browser backend has no recognizer, or nothing when a server backend's browser lacks recording; no mic until the installation's backend is known | typing unaffected |
| Engine pin | Auto, or an engine name | `POST /api/chat` carries the pin; it covers chat and system turns alike and stays until changed |

Voice is capped just under ten minutes: the client stops at 9:55 to stay under the server’s ten-minute limit, and transcription times out after 60 seconds.
The timer turns `--danger` in the last minute. Audio never becomes part of task or chat state.

The installation's voice backend (`GET /api/voice`, set with `alt machine set --voice`) decides how
words arrive: `browser` (the default, no setup) runs the browser's own speech recognition and shows
words while speaking; `local` and an OpenAI-compatible endpoint upload the recording after Stop or
Send and show Transcribing. Words are never simulated: only recognition that produces them
progressively shows them progressively. `VoiceStates.html` shows each state at desktop and phone width.
Browser recognition of English is punctuated on the device by a bundled model that adds only
`.` `,` `?` and capitals; dictated words are never rewritten. Other languages keep the recognizer's
formatting.
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
| Sent / viewer | Thumbnails belong to the saved message. Open shows the full image in a modal with Fit/Zoom, Close and Escape; closing restores thumbnail focus, or focuses the same image's stored thumbnail once history admission has replaced a queued opener. Archived tasks retain viewing without a composer. |
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
| Done or rejected | The row leaves Current and enters Done this week once, with a 200ms fade. The disclosure shows or hides every task finished in the last seven days, newest first, with or without a PR, and is absent when none exist. A no-code completion also posts a task-linked FYI to the project conversation. |
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
including shared prefixes and unbroken names. The selected project's section comes first whenever
it has items, on entry and when the selection changes; the other projects follow first appearance
in the queue. Items keep their order within each project, and question groups stay together. This
applies to single-project and mixed-project queues, including questions, reviews, stops and faults.
The selected project never supplies an item's owner or filters the inbox. Saved reads retain the
sections. Each heading is a disclosure button with a small chevron: a click, Enter or Space
collapses that section to its heading and a count (**2 questions · 1 stopped task**) and reopens it.
Sections start open; collapsing keeps staged answers and changes no question, and the state lasts
for the page visit, with no setting.
The task title is its own fully wrapping link below source/time and above the question.
`web/e2e/needs-ownership.pw.ts` walks ownership and navigation; `work-and-decisions.pw.ts`
walks empty, loading, saved/read errors, sending, sent, failed and denied states at both viewports.
Mechanical truncation or hiding a necessary
consequence does not satisfy concise presentation. Long questions still remain fully readable on
phone and desktop, with the same answer and revision semantics.
Question and review-card text renders like a reply (§3.3): a one-line question stays one bold line,
and line breaks bring separate paragraphs, lists, inline code and a fenced command in a distinct
mono code block the operator can copy. The first paragraph keeps the headline weight. Recommendations,
receipts, folded summaries, list rows, toasts and labels keep their one-line form.
`conversation-decisions.pw.ts` walks a multi-paragraph question with a command block in Needs you
and the owning chat at both viewports.
Each explicitly recommended choice has an accent border and text with a small star badge on its
top-right corner (announced and titled "Recommended"), so the cue never narrows the label. It is never
preselected; the operator's pick adds the tinted fill and ring. **Send N answers** submits picks.
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

The recommendation body and response controls are the same component as the open question at the
end of the chat. Loading uses a skeleton with no inferred count; empty Needs you says
**Nothing needs you.** A read failure offers Retry. Cached failure keeps saved cards with an explicit
refresh notice and disabled sending. During submission, the control says **Sending…** and cannot be
repeated. Sending anything hands the turn back: the task's card leaves Needs you and the badge drops,
though every question stays open in the record. A brief **Sent to L2** receipt links to chat. When
the owner parks again, still-needed questions return labelled **asked again**; a revised question
is new. This records delivery, not agreement:
the L2 interprets presets and typed responses alike, records clear decisions, and discusses follow-ups.
Failure retains responses with Retry. Denied writes require a refreshed read. A changed question
clears only its own stale draft; independent drafts survive refresh and another member's submission.
The send row follows the questions in normal flow on phone and desktop and scrolls with them.
With the phone keyboard open, the focused answer stays unobscured; scroll to the end to send.
Question fields have no voice controls; ordinary chat keeps its existing voice states.
Withdrawal likewise removes only affected controls and updates counts; stale submissions fail.
Re-asking supplies fresh controls, retaining earlier history without carrying approval forward.

#### 3.8.1 Decision alerts

Under the Needs you heading, one switch offers **Alert me about new decisions** and reads **Alerts on**
once permission is granted. It is set per device, because each browser grants its own permission, and
its line below states the reach honestly: a device the browser's push service can wake alerts with
Altitude closed, and says that away from your network the alert names nothing; a device that cannot be
woken alerts only while Altitude is open, which on a phone means while it is on screen. A browser that cannot show notifications disables the switch and says so;
refused permission says the browser's settings block alerts and how to allow them again. Every state
leaves Needs you, its cards and all typing untouched.

Each newly published operator question alerts once, titled with the project and carrying the task name
only — never question, conversation or incident text. Activating it opens that decision in the running
app. Faults, stopped tasks, reviews and completed work stay in Needs you without an alert. A grouped
ask alerts once. A decision already on screen, in Needs you or its owning task, is recorded without
alerting. Refreshing, reconnecting, polling and other tasks' activity repeat nothing, and turning the
switch on never announces what is already waiting.

### 3.9 Open the owning L2 question

`/projects/:name/tasks/:slug?question=<id>&revision=<n>` opens the owning human conversation at
that durable question's group. The open group is the last thing in the conversation, right above
the composer, and scrolls with the messages; it is never pinned. When the owner writes after asking,
the group moves to the end again. Every member link focuses it; closed groups stay at their original
message. L3 escalation text stays attributed to L3 and contains the actual dilemma and recommendation. The live session starts closed when entering a question; **Activity & evidence** reveals
technical event summaries and the link to the existing live view.

The question field and normal composer accept a follow-up, a simple answer such as “21 days”, or a nuanced decision.
There is no recipient selector, note form or extra confirmation. A follow-up hands the turn back and
wakes the owner to assess the question (§3.8); the group becomes **Sent · the L2 has your reply.** The L2 records a clear decision against its source message;
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
**Latest ↓** follows the bottom, and the **N questions ↓** button returns to an offscreen open
question. These conditional buttons float above the composer (§3.10).
Pending-question reads poll every two seconds. Stale navigation refreshes before acceptance, and
every write names its exact question revision. Archived tasks retain history without a composer.

#### 3.9.1 Pending design preview

A question with saved design content has a **View preview · vN** link in Needs you and its owning
question. When the open question is offscreen, use its floating question jump, then View preview.
Question navigation follows an open group member with an attachment before another open member,
including after partial answers. Work's task row opens the exact owning question.
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

The [maintained task states](TaskStates.html) describe activity and steering. Task actions stay
directly accessible in the compact header, with one consistent button treatment.

Task details includes **Observed tokens** on phone and desktop,
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
scroll within task details at both viewports. `web/e2e/task-usage.pw.ts` walks these states.

Desktop anatomy: one row with crumb, wrapping 18px title/state dot and direct actions, then a
wrapping chip row (state, engine and model, PR with checks state, Merge held when applicable).
Header vertical padding is 8px. Task-header commands, live-toolbar commands and floating jumps
share a visible border, subtle fill, corner radius and typography, with at least 44px touch targets.
Stop, Continue and Check status share one 104px-wide header button; its position and treatment stay
fixed across state changes. Tabs retain an active underline to identify navigation.
Left is the operator's conversation with
the L2 (same bubbles and composer as §3.3 and §3.6); right the live session panel (480px, toggled by
the header button). Task details contains the muted line "attempt 1 · started 32 min ago · 18% of its context used"
when those values are available; a finished task reads "done 2h ago" or "rejected 2h ago". Engine
and model appear in their chip. The PR chip reads "PR #N merged · main checks passed" or its open
and check states, in danger tone when main checks failed. It links to the PR when the repository
URL is known, otherwise it is a plain chip. **Merge held** is concise and independent of execution
or question state. Its complete reason opens in task details and wraps without truncation.
`web/e2e/header-density.pw.ts` measures long-title reading space at 1440×900, 1366×768 and
1024×768, plus the 390×844 phone layout. Details, navigation, scrolling and draft
restoration remain covered alongside the task state and composer walkthroughs.

**Stop** is directly accessible in the task header on phone and desktop, serving both views.
One click requests termination immediately. **Reject** is in phone task details and the desktop header,
with inline confirmation:
"Reject this task? Its worker ends and the task is archived." with "Reason (optional)", Reject and
Cancel. Stop appears while running; Reject appears while queued, running, blocked or reported.
An operationally blocked task without an open question also offers **Resume**, using the existing
daemon operation. The button becomes **Resuming…** during the request, then disappears when running.
A failed request leaves Resume available and places its error on a separate line under the actions,
including on the phone. Resuming an operational pause records no decision. A confirmed Stop instead
offers **Continue** in the same place as Stop. It resumes without sending the unsent draft.
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

One compact L2 activity preview sits at the end of the conversation's scrolling column. It appears
only while both public words and recorded output are less than 60 seconds old. Stale, missing,
untimed and unavailable output leaves no box; tool output alone never revives stale prose. The preview
scrolls away with chat, and updates or expiry never pull an older-message reader to the bottom.
It opens with **Working · output 12 sec ago** and a softly pulsing accent dot (steady under reduced
motion), with **Expand** on the right. The current worker's public text appears verbatim after
redaction, clamped to two lines, with its source time in the §3.4 format, including on short viewports.
**Expand** reveals it; **Collapse** folds it. Each newer update replaces the preview without
duplicating Conversation. Activity never proves useful progress. Generation changes clear previous
words. Live session retains quiet, missing, untimed and unavailable activity status. Phone tabs and
the desktop panel toggle open older output under existing retention; Activity & evidence also keeps
its contextual live link. Conversation has no separate live-session/action row above the composer.
Questions, decisions and results stay durable;
there is no copied preview archive, duplicate reply, summarizer call or hidden reasoning.

Below 1280px the live session panel follows the §2.2 rule for the work panel: an overlay from the
header's panel button, scrim behind, Esc or the scrim closes it; the `live` route opens it on desktop
too. Its header carries Stop/Continue/Check status while the scrim blocks the task header.
Phone anatomy: one compact header with Back, a bordered title dropdown with down-chevron,
Stop/Continue/Check status, concise L2 state and independent **Merge held** status; a full-width
two-tab row **Conversation | Live session** (the `live` route selects the second); content;
scrolling fresh activity and the shared compact composer on Conversation. The header action remains
visible in either view and while typing. The title dropdown opens a scrollable sheet
with full title, attempt/context/tokens, PR/checks, complete block/hold reasons, and existing
View question, Reject confirmation and operational Resume. A long phone title opens in full in
details. Desktop retains its direct operational actions and
live-panel control while disclosing long reasons. Closing details restores the opener, draft,
selection and reading position. A failure remains visible, not only inside details.

Compact task states use the §3.5 labels (**L2 working**, **Waits for L3**, **Your turn · …**,
**L2 replying to you**, **Paused · fault**, **Paused**, **Stopped by you**); **Merge held** can
accompany any of these. Details separates each full reason. Waiting on L3 adds no operator badge.
An operator question sits at the end of the chat with no generic Resume while the question is open.
When it is offscreen, **1 question ↓** (or its count) floats above the composer. **Latest ↓** appears
when newer messages are below the reader. If both lead to the bottom, show only the question jump.
Each hides when its destination is visible. These labeled, keyboard-accessible 44px buttons occupy
the conversation's lower corner without reducing its viewport. View preview stays in the question
and Needs you; View question stays in task details, useful from Live session. A held review-ready PR whose owner has stopped
(#419) shows **Your turn · review before merge** at the end of the chat and in Needs you:
**Approve merge** sends the operator's own message "Approved: merge PR #N." and the L2 merges
with it after fresh checks of the current head (the chat then shows "Sent · the L2 has your reply."); **View PR #N** opens it; asking below discusses it. A later,
unrelated question never hides that review; an open operator question with options that links or names
the PR is that review, so the separate card stays away until the question closes. A freeform question
naming the PR only discusses it, so the card and its **Approve merge** stay. After **Approve merge**, a later park
on another dependency shows that wait and no card (the CLI and queue add "PR #N approved"), including after routine integration
gives the PR a new head; a new hold or a later operator message naming the PR brings the card back. A fault retains a visible short cause and **L3 has been told**. Operational
pauses without questions retain Resume/Reject. No disclosure or reply releases a merge hold.

Navigation states: Conversation and Live session are local views of the same task. On phone they
sit side by side on one track. A deliberate horizontal swipe is interactive from its first
horizontal movement: the outgoing view slides with the finger and the incoming view is on screen
beside it, proportionally to the drag. Release past half the width, or a fling of at least 0.4 px/ms
in the drag's direction, completes the switch with a short settle; otherwise, including a fling
back toward the start, the track springs back. Left opens Live session; right returns to Conversation;
past either end the track gives a little with rubber-band resistance and never switches. Under
reduced motion nothing moves with the finger and a release past the same thresholds switches at once.
Accessible labeled tabs remain the direct alternative. Swipes do not start from the composer or form
controls, and leave browser-edge Back, text selection, recording, dialogs and horizontally scrollable
session content alone. Vertical scrolling stays native. Desktop keeps simultaneous panes.
Phone tab and swipe switches replace its current history entry, keep the originating shell tab, and update the URL;
the desktop panel button adds no history. A `/live` deep link and reload select Live session on
phone and open the desktop panel. Back in the phone header and the desktop crumb both return to
the actual preceding in-app page, including its query string. With no in-app predecessor they
replace the task entry with the owning project's L3 conversation. Browser/system Back remains
native; Forward restores the task's latest URL, and other pages/tasks keep ordinary history.
On phone both views stay mounted and laid out; the inactive view is invisible and untouchable, not
removed, so draft text, selection, images, conversation reading position and live transcript reading
state survive switches natively and a drag reveals the view as it was. An inactive Live session does
not poll its transcript; the drag that reveals it starts the transcript, so the incoming view shows
its real content when it has rendered before and its Connecting skeleton during the drag and settle
otherwise, then its content. The Conversation's initial, empty and error states read the same at any
offset, and a drag never wakes its composer. Memory matches the desktop's simultaneous panes; only
the inactive phone view's transcript polling and reading bookkeeping pause. Returning does not open
the keyboard automatically; touch fields retain their 16px sizing and ordinary use never changes
page scale (§2.2).
Switching to Live session cancels unsent dictation and releases the microphone. Explicit voice Send
continues transcription and submission for its original task while hidden, without refocusing its
composer; Escape in Live session does not cancel that submitted message.
A refused or uncertain send arriving while Live session is open restores its text
alongside newer draft edits; an accepted send remains sent. On desktop, closing the panel leaves
the conversation visible. Navigation itself has no
loading, listening, denied or error state; destination reads and composers retain their states
specified here and in §3.6. `web/e2e/task-navigation.pw.ts` walks entry from L3 and Work, repeated
toggles, reload, Back, Forward and direct-live fallback on phone and desktop. `web/e2e/task-swipe.pw.ts`
walks the phone gesture: idle, drag started, half-way with the incoming view loading and rendered,
release completing, springing back, resistance past either end and reduced motion, plus the
gesture exclusions and retained reading state.

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
output, and the lifecycle boundaries the record supplies (state transitions, stops, holds). Every
row shows its recorded time in the §3.4 format, or **time unavailable**. The **Activity & evidence**
list prefixes each task event with its time the same way. A shell command's tool label is "$"; other rows use the recorded tool name, including
Edit for a file change. The hint reads "N lines", "running · 4 min" (time since the call while the
worker runs; "running…" when the call is untimed), "error", or "no output"; write rows
carry no diff counts. **Raw events** toggles the transcript to the raw list; its hover title states
the server's redaction rule. There is no transcript search field. Footer states are "Following live
· new steps appear at the bottom", "Paused · Follow to catch up", "Session paused until the task
resumes", or "Session ended"; scrolling up pauses following, and Follow returns to the newest output.
Pause also stops following while the worker runs. Paused position and expanded tool output survive
phone view switches. While
running, the same activity line sits under the footer without the words, and the header
dot pulses only while that cue shows recent output; quiet, unavailable, waiting and ended sessions
keep it still.

Data: `GET /api/task/<project>/<slug>`, `GET /api/transcript/<project>/<slug>`,
`GET /api/overview` (engine labels, decision card and queue), `GET /api/project/<project>`
(repository URL), `POST /api/l2/message`, `POST /api/task/action`.

States: loading (header and conversation skeletons); error ("Could not load the task." and Retry);
planned (the wait reason replaces the live panel; the conversation accepts messages without release);
queued ("Waits for dispatch" or "Waits for resume" replaces the live panel); running; blocked on the
operator (the question at the end of the chat); blocked on L3 ("Waits for L3"
with the full reason in details); blocked by a fault (a red line with the first sentence, at most 100 characters,
and "L3 has been told"); held for resume (Queued chip, "Waits for resume · <reason>" in place of
the session); done or rejected (read-only conversation, composer gone, PR chip in the header).
Empty conversations read "No messages yet." on an active task and "No messages on this task." on
a finished one. The live session is connecting (skeleton and "Connecting to the session…";
recorded lifecycle boundaries stay visible), streaming, paused, ended, or unavailable ("No session
file for this attempt").

`web/e2e/l2-progress.pw.ts` walks phone and desktop with deterministic fixtures for both engines:
loading; replacing/expanded/expired/missing/untimed/unavailable previews and scrolling with older-message
position preserved; the shared cue and record times in both
views, a long call without output, reduced motion, an untimed row, unavailable activity and an ended session; queued/unconfirmed/delivered steering;
direct Live session Stop; stopping with editable draft and racing messages held; stopped; correction
and capacity wait; continued session with preserved work; blocked question; finished; denied Stop;
short viewport; voice listening/cancel/dictation/denial/unavailable; input and overlay Escape ownership.
Each named state has a screenshot under `web/ui-artifacts/results/l2-progress*`.

#### Terminal

The operator's own shell, for occasional commands; the conversations stay the main flow and agents
never see it. A task with a worktree offers it as the phone's third tab, **Terminal**, after
Conversation and Live session; swiping stays between those two. On desktop the task panel's header
becomes a **Live session | Terminal** switch, and the panel toggle and overlay behave as for Live
session. The project header's **Terminal** shows the project folder's terminal in the right panel
(overlay below the inline width) and presses again to hide it; on phone it opens full screen with
Back, a "Terminal · <project> · project folder" title and **Close**.

Anatomy, top to bottom: the view's own header and nothing else above the screen — desktop task: the
switch with a bordered **Close** at its right; desktop project: "Terminal" and **Close**; phone task:
the tab row, whose active Terminal tab carries a × (**Close terminal**); phone project: Back, the
title and **Close**. Then any notice and the dark screen filling the rest (edge to edge on phone),
whose first line, dimmed, says "Runs as you in <folder>"; on phone a key row of Esc, Tab, a sticky
Ctrl (pressed state), the four arrows and **Paste** (reads the clipboard), each an equal-width 44px
target. Showing the view opens the shell, or attaches to the running one; there is no Open step. The
terminal keeps running when the page leaves; returning replays up to 256 KB. When the shell ends,
however it ends, the view returns to where the operator was — Live session on a task, the project for
the project terminal — and keeps no output; the next visit opens a fresh shell.

On desktop, Ctrl+V (Cmd+V) pastes and Ctrl+C with text selected copies (Cmd+C on a Mac); without a
selection Ctrl+C interrupts. Escape and Tab belong to the shell, also when the panel is an overlay.

| State | What appears and what actions do |
| --- | --- |
| Off | "Terminal is off", what it does, **Open Settings** (returns here with Back, which opens the shell). |
| Starting | Skeleton lines and "Starting the terminal…". |
| Running | The screen with the cursor focused; **Close** / ×; the phone key row. |
| Restart pending | A grey note above the screen: "Altitude restarts at its next quiet point to apply an update. This terminal will close then." |
| Reconnecting | A small "Reconnecting…" badge over the screen's top right, so the shell keeps its size; it disappears when output resumes and missed output appears. |
| Typing stopped | Input failed (a program not reading it, Altitude unreachable), so part of it may not have arrived: an amber alert "Typing stopped: <reason> Part of what you typed may not have arrived; check the screen." with **Resume typing**. Keys typed meanwhile are dropped, not queued. |
| Close with a running command | "Close the terminal?" card naming the command that will be stopped, **Close** (primary) and **Cancel**. Close without a running command acts at once. |
| Closed by the operator, or clean exit | The view returns; no notice. |
| Exit with a failure code | The view returns; toast "Terminal closed · exit code N". |
| Closed elsewhere (another tab or device, or the setting turned off) | The view returns; toast "The terminal was closed elsewhere." |
| Task finished / project unmanaged | The view returns; toast "The task finished, so its terminal closed." / "The project is no longer managed, so its terminal closed." The task's Terminal tab disappears. |
| Ended while disconnected (an Altitude restart) | The view returns; toast "The terminal closed while the connection was lost." |
| Could not read, start or refused | "Couldn't read the terminal" or "Couldn't open a terminal", the server's reason (a missing folder, the setting off, an agent request refused) and **Retry**, shown at once. |

Walkthrough: `web/e2e/terminal.pw.ts` at 390×844 and 1440×900 (the project terminal at 1100 wide, as
an overlay) walks every state above against real shells, plus tab completion, copy and paste, with
the agent check and the restart notice as fixtures.

### 3.11 Project switcher (phone)

A sheet from the header name: managed projects with dot and count, unmanaged folders, **Add a
folder**. Tapping a project selects it and closes the sheet; the Chat and Work tabs follow. Hidden
chevron and no sheet when exactly one project is managed and no folder is unmanaged.

### 3.12 First run and project setup

Shown on any project route when no project is managed: a centred card with four steps, **Your
name**, **Prerequisites**, **Incident reports** and **Projects**. Desktop shows a numbered stepper
(done steps ticked); phone shows "Step N of 4 · Title" and keeps the step's buttons in a sticky bar
above the tab bar. The step is `?step=` so reload and browser Back keep the place. The name and
incident steps have **Skip**, prerequisites always allow **Continue anyway**, and every step but the
first has **‹ Back**, which saves nothing. Each step is also
a Settings row (§3.15), and the name reads “you” wherever none is known.

- **Welcome to Altitude** (name): one field filled in from the saved name, `ALTITUDE_OPERATOR` or
  Git's `user.name`; **Continue** saves it (empty clears it). Failure: the server sentence under the
  field, the step stays.
- **What the agents need**: one row per doctor check (GitHub CLI, each coding agent, Git), a green
  tick when met, a red mark when unmet, a hollow mark when optional (another agent is signed in).
  An unmet row explains itself and shows the terminal command with **Copy** (then Copied); no field
  asks for a password or token. **Check again** reads again (Checking…). The primary action reads
  **Continue** when nothing is unmet, else a secondary **Continue anyway**. States: checking, read
  failed (one sentence).
- **Report Altitude’s own faults?**: two radios, **Keep incidents on this computer** (default) and
  **Also publish them as GitHub issues**, which reveals **Repository** filled in with Altitude's
  repository and the note that a fork receives them instead. The note **What leaves this computer**
  states the sanitization and whether the repository is public. Publishing reads **Save and
  continue** (Checking… while the GitHub CLI confirms the repository); a refusal shows the server
  sentence under the choice and keeps the step. Keeping incidents local saves nothing.
- **Add your projects**: a **Projects folder** card with **Change…**, which opens the folder browser
  with **Use "<folder>"** in place; with two or more folders, "N folders" and **Add all N**; one row per
  folder directly inside it with **Add project**; then "A project can live anywhere: **Choose a
  folder elsewhere…**". States: looking for folders; none found (a "No folders in <projects folder>
  yet" card, pointing at Change… and Choose a folder elsewhere…); adding ("Adding 2 of 3…"); failed
  (one sentence with **Retry**, or **Retry Add all** when nothing was added; a later refusal opens
  the added project with a toast naming the folder left out). Adding opens the first project's Setup.

The rail's **Add a folder** dialog and the phone switcher sheet show only the folder list: "Altitude
found N folders in <projects folder>", the rows and **Choose a folder elsewhere…**; with none found,
a link to change the projects folder in Settings and the folder browser already open.
Walkthroughs: `web/e2e/onboarding.pw.ts` at 390×844 and 1440×900.

The folder browser is an inline panel in the same card, the rail dialog or the phone switcher sheet,
never a second dialog. It is labelled "Folders on the computer running Altitude", starts at **Home**
and shows breadcrumbs whose earlier parts go back up. Rows are the current folder's visible
subfolders, sorted by name, with **Project** and **git** tags; a row opens that folder. The footer has
**Type a path instead**, **Cancel** (absent when the browser is the only way forward) and **Add
"<folder>"** for the current folder, disabled at Home. States: loading (skeleton rows); empty ("No
folders inside X", Add stays available); unreadable ("Altitude can't open X: your account can't read
it", Add disabled, breadcrumbs work); listing failed (server explanation and Retry); adding (spinner
and "Adding project…", then Setup). **Type a path instead** swaps the list for a path field with
**Browse folders instead**. Walkthroughs: `web/e2e/folder-browser.pw.ts` at 390×844 and 1440×900. Successful registration opens the project's Setup checklist
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

Above the phone header and first in the desktop main pane, an undismissed pending update shows
one compact row: **Update ready · Details · ×**. During activation the summary reads
**Altitude is restarting…**; a failure reads **Activation failed**. Details opens Monitor, where
**Altitude update** retains the complete status and permitted action after dismissal. Monitor
has no duplicate banner. The notice stays in normal layout flow, with 44px touch targets, and
never overlays conversation, composer, navigation or keyboard controls.

Close has the accessible name **Dismiss update notice**. Dismissal is presentation only and
persists in this browser across polling, navigation and refresh for the update's head/since.
Wait-list and requested-at changes do not reshow it. A changed head/since or new nonempty failure
identity shows a new notice; an unchanged or cleared failure does not repeat one. If browser
storage is unavailable, Close still hides it for the mounted page.

Monitor says "Merged changes to <what changed> are waiting to activate.", naming the backend,
web app, both, or Altitude, with file count and age (exact time on hover). The next line says
"Altitude restarts at the next quiet moment." and appends "Waiting for <list>." when needed.

The quiet point has no dispatch or resume claim, L3 turn, adversarial review, or report verification in flight; running
workers do not hold activation. **Restart** appears when the waiting list is empty and no restart
is under way. Pressing it or receiving a recorded restart request removes the button and changes
the line to "Altitude is restarting…". A failed activation reads "Automatic activation did not
complete; L3 has the fault." The notice leaves when the new process answers without a pending
restart. Data: `GET /api/overview` `restart`; the button requests `POST /api/restart`.

Update status loads independently of Monitor readings, with **Loading update status…**, a read
error with Retry, or **No update pending.** Request errors remain beside the action. Details wrap
and use normal page scrolling on both viewports. Pending, waiting, restarting, activation failure,
request denial, loading, read error, empty, dismissed and new-event states are walked on phone
and desktop in `web/e2e/restart-banner.pw.ts`, including removals after actions.

All banner notices and toasts offer an accessible close control. Toast timers, hover/focus pause
and action controls remain available. Inline form/transport errors, task faults, task questions
and conversation navigation are contextual state or actions, not dismissible banner notices;
their existing recovery, answer and navigation controls remain visible.

### 3.14 Monitor

Anatomy: **Monitor** title; **Altitude update** (§3.13); **Seats**, one card per configured engine in the API's order and under
its label; **Routing now**; **Sessions (N)**. Each seat shows the windows it reports, their
percentages and reset times in relative and clock terms, a meter with the 70% reserve line, the
plan when supplied, and "reading 3m old". Exact reading times appear on hover.
Below a hairline, **Model allowances** lists each model the seat's routing can launch, as
"<model> · 7-day": its own meter, percentage and reset ("reset time not reported" when absent) when
the provider sends a model-specific row, otherwise "No reading for this model. The shared windows
don't show whether it is available." An active rejection adds "Unavailable: <reason>" in `--danger`.
Model rows share the seat's age and stale treatment; a seat without named models shows no list, and
a reading with model rows but no account windows says "No account windows reported." instead of "No reading."

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
label, windows, plan and reading metadata, and `models[]` with each routed model's own reading and rejection; `routing[]` and `sessions[]` supply their rows.
L2 `sessions[].token_usage` is the same persisted accounting snapshot as task/status/report reads,
including `helpers`; opening Monitor starts no provider collection. Archived tasks retain the
breakdown on their task/report pages rather than appearing as live Monitor sessions.
`GET /api/overview` `engines[]` supplies routing and session engine labels. Monitor derives no action.

### 3.15 Settings and Voice input

Settings at `/settings` opens from **Settings…** in the project's three-dot menu on both widths,
or the desktop rail's operator row. The row highlights on every Settings route; theme switching
remains independently accessible. Phone keeps a labelled Back button and the existing four tabs.
A direct overview visit returns to `/projects`; entry from another view returns there.

Under **This machine**, one bordered **Voice input** row shows the saved backend and a chevron.
Its whole area opens `/settings/voice`; no backend options or credentials occupy the overview.
A **Projects folder** row shows the current folder and opens `/settings/projects-folder`: an
explanation that First run offers the folders directly inside it, the current value and the §3.12
folder browser with **Use "<folder>"** (Home allowed). Saving shows Saving…, then Saved. with the
new folder; a failure shows the server explanation and Retry. First run reads the change at once.
**Your name** shows the name, or "Not set · screens say “you”", and opens `/settings/name`;
**Prerequisites** opens `/settings/prerequisites`; **Incident reports** shows "Published to
<repository>" or "Kept on this computer" and opens `/settings/incident-reports`. Each page is the
First run step's content with **Save** in place of the step buttons (Prerequisites has **Check
again** only) and shows Saved. after a save.
A **Terminal** switch row (off after install) says "Anyone who can open Altitude can run commands as
you on this computer. Terminals close when Altitude restarts or when you turn this off." It saves on
change, disables itself while saving and shows the server's reason under the copy on failure; turning
it off closes every open terminal.
The overview also shows read-only address and HTTPS details.
Voice input has a labelled **Settings** back button at both widths. It returns
to the overview even on a direct visit; browser Back retains normal history. The phone header stays
visible while the content scrolls. Opening a Settings page does not change a setting or probe a service.

The voice page offers Browser recognition (default) and Your speech service. Descriptions state
where audio goes and any setup or charges. Browser saves immediately; Your speech service opens a
**Service URL** form with **Save service**, a line naming the OpenAI-compatible
`/v1/audio/transcriptions` endpoint and a **How to run one** link to the setup docs. Model and key
stay behind **Hosted provider? Add a key or model** and are shown directly once a key or a
non-default model is saved. Back discards unsaved service edits. Saving stays on the page. Keys are
write-only: **Key set · never shown** has a **Replace** control; a blank replacement removes it.
Editing the URL clears retained-key selection; no stored key follows a new destination. A
successful save updates only the next capture. Uploads bind to their original backend/destination
and refuse a changed selection before forwarding audio. The overview row names the service's host.

| State | What appears and what actions do |
| --- | --- |
| Loading | Loading settings…; no selected default or editable controls. |
| Read failed | Could not load settings and Retry; typing elsewhere is unaffected. |
| Saved browser | Chosen radio, Saved.; service form absent. |
| Service editing | URL required; hosted link reveals optional model/key; explicit save; overview still reflects persisted choice. |
| Saving | Saving… and disabled controls until the request answers. |
| Failed/denied save | Server explanation and Retry; draft fields and saved choice preserved. A changed backend or URL offers Reload settings; concurrent model/key edits use last-writer semantics. |
| Saved service | Saved.; key entry clears and becomes Key set when configured. Returning shows Your speech service · host. |

Under **This project**, opened from a project, one row names that project and opens
`/settings/projects/<name>`; a direct visit lists every managed project under **Projects** instead.
The project page has a labelled **Settings** back button and three cards. **L3 engine** holds the
Auto/engine pin (the same pin as the desktop composer pill) and the last L3 turn: engine, observed
model, requested effort and the effort the engine reported, each saying "not reported" when unknown.
**L3 · project conversation** and **L2 · task owners** each hold one row per engine with **Model**
and **Effort**: the model is free text with alias suggestions and a "Default: <model>" placeholder,
saved on Enter or leaving the field, restored by Escape and cleared to Default when empty; effort
offers "Default (<level>)", Native and only the levels that engine accepts, saved on choice. Each
field saves alone and shows its own status; changing one pair never changes another. Copy says L3
changes apply from its next turn, L2 defaults apply to fresh attempts while started tasks keep
theirs, a choice made for one launch wins, and choices request rather than confirm what the engine
used. At 390px each row stacks model above effort.

| State | What appears and what actions do |
| --- | --- |
| Loading | Loading settings…; no fields. |
| Read failed | Could not load settings and Retry. |
| Saving | That field is disabled with Saving…; other fields stay editable. |
| Saved | That field shows the persisted value and Saved. |
| Failed/denied save | Server explanation and Retry save; the field keeps the saved value. |
| Not started | L3 engine card says L3 has not started. |

Maintained boards: Settings/MobileSettings, VoiceSettings/MobileVoiceSettings and SettingsStates.
Application walkthroughs: `web/e2e/voice-settings.pw.ts` at 390×844 and 1440×900, including navigation,
typed draft preservation, all three choices, key replacement/removal and loading/saving/failure.
`web/e2e/project-settings.pw.ts` walks the menu entry, This project row, independent saves, reload
persistence, restoring Default, the engine pin, loading/read failure and saving/denied states at both
sizes, with the project draft preserved.
Composer listening, denied, unavailable, cancellation and transcript states remain §3.6.

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

Every dilemma opens its owning L2 chat. Follow-ups and unclear answers stay open in the record and
hand the turn back to the owner, which answers and re-parks what remains; a clear decision
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
is one operator question on the operator's turn, one held review, plus each existing operational
attention item. Two questions on one task count as two; any reply to that task removes both until
its owner asks again. A known zero hides it. Project
rail/switcher rows retain their state dots, and Work has no attention badge. Task totals remain
labelled text. Running tasks, FYIs and queued messages do not add attention.

Needs you says **N questions across N projects**; reviews and operational items, when present,
are named separately. Project summaries use the same distinction. Loading and failed reads never imply
zero; cached counts are labelled stale until refreshed. A follow-up, partial answer or final answer
removes that task's card until its owner re-parks. Those changes do not claim that its worker resumed.

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
when records change and on every (re)connect; polling continues underneath. A tab with decision alerts
on (§3.8.1) keeps that stream while it is out of sight, which is how a new decision reaches an alert.

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
compact header, stack the composer's field over one row of 44px controls, disclose routine
metadata and engine selection, and remove routine hints on phone. Hide the 84px bottom navigation
only during detected software-keyboard use; restore it on dismissal, retaining draft and selection.
Task Conversation/Live session tabs remain visible. A long draft grows to 120px or 25% of the
usable visual viewport (minimum 44px), then scrolls internally. Bottom-follow and older-message
anchoring survive changes in available height. Browser-managed safe areas remain intact.

Activity and nonzero decision counts remain reachable; actionable failures, question controls,
queue removal, voice guidance and available Restart remain explicit. A compact update summary
discloses detail. Task metadata, Reject with confirmation and operational Resume open in task
details. Stop and Continue stay directly accessible in the task header from either view,
as specified in §3.10. §3.8–3.9 and CONVERSATION_FIRST.md decision semantics remain authoritative.

Task states include running+held, waits-for-L3, waits-for-L3+held, operator-question+held,
fault+held and operational pause. One compact status names waiting and merge state
separately; the complete reasons open in scrollable task details. The open question remains
at the end of the chat with floating question/latest jumps when applicable. Faults retain a visible cause and
L3 notification. Open questions do not acquire a generic Resume, and merge restrictions never
acquire a release action. Desktop keeps directly available operational actions while disclosing
long reasons. Phone sheets fit above the keyboard; closing them preserves draft and reading
position. Application walkthroughs in `web/e2e/mobile-chat.pw.ts`, `task-details.pw.ts` and
`conversation.pw.ts` cover keyboard restoration, draft growth, scroll anchors and all six task-state
combinations at phone and desktop sizes. Named screenshots stay outside Git under `web/ui-artifacts/`.
Browser simulation establishes layout and application transitions; native keyboard, toolbar and
safe-area behavior require real-phone acceptance.
