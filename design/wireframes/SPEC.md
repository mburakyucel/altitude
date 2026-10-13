# Altitude UI specification

Container deployment retains the existing First run and Settings layouts. It labels folder paths
as container-volume paths and distinguishes host shell commands from tools/sign-ins inside the
container. Terminal and image-managed version rows explain unavailability without enable/update
switches; the host-voice choice is unavailable with its reason. Browser and external-service voice
remain available. Empty folders, outside-volume refusal, retry and reload use the existing form
states. Actual-daemon Linux phone/desktop onboarding passes with fictional external engines;
Mac and real-device acceptance remain open. Walkthroughs live in `web/e2e/container.pw.ts`,
`scripts/container_browser.mjs` and ignored runner artifacts.

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

In the cross-engine design review of every UI proposal and the [phone and desktop walkthrough](../../AGENTS.md#ui), check that:

- Visual hierarchy makes the primary action clear; navigation and plain labels show where people
  are, where they can go, and what an action does.
- Every clickable control reads as clickable at rest. The only or primary action in an area is a
  bordered or filled button; a borderless ghost button sits only beside a visible bordered or
  filled action; a text link is underlined. Plain text beside muted meta lines is not a control.
- Complexity is restrained: each visible control and detail earns its place in the current task.
- State shows visually before it is written: a control changes in place, and an icon, colour or
  motion says that something is working, waiting, done or failed. Text appears only where a cue
  cannot carry the meaning, such as an error's reason. No line restates what the screen already shows.
- The UI never tells people what they cannot do. Unavailable controls look unavailable; their
  reason appears on demand or on press. Accessible names, busy states and descriptions retain meaning.
- An empty Live session panel retains its dispatch or resume explanation: there is no activity
  cue yet, and a held task does not read its previous session until it resumes.
- Typography, spacing, alignment, colour, and component treatment have a consistent visual finish;
  interaction states and transitions feel complete and polished on phone and desktop.

Apply these expectations with the specified component states (§3) and existing accessibility
requirements, including accessible control names, minimum targets, and contrast (§6).

Routine loading uses a compact spinner; a pending action replaces its label in place without
changing its width. Saved and copied receipts use a check. Accessible status names retain the
words, and reduced motion keeps the progress mark static. Primary task state names, authored
blockers, failures, setup instructions and resource/privacy consequences remain readable.
Decision dispositions, approval consequences and quantitative reading ages also stay readable;
a generic check or dim cue cannot distinguish their meanings. Live regions are not marked busy;
busy action containers and existing parent status regions retain their own accessible names.
Unavailable terminal, voice and alert controls reveal their reason on press and expose it as an
accessible description; pressing them never performs the unavailable action. Task pages omit
sentences repeating their state chip, question card or composer. Live session follow state uses
its existing control and a quiet dot; recorded historical boundaries stay readable.

## 2. Information architecture

The container recovery notice uses the shell's existing status-banner treatment on phone and
desktop. While globally paused it says new AI work is paused, messages/task requests stay queued,
and Stop remains available. It labels the displayed Continue command as a host action and offers
no browser mutation button. An unavailable instance shows repair guidance instead of a command.
The notice is absent while loading, in native mode, and after the overview reports admitted work;
no dismissal or additional browser storage is introduced. Container deployment walkthroughs remain
distinct from the fictional local UI harness.

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
  available everywhere, including mocks and the reader.
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
"N folders not managed" line; engine readout; **New tasks**; **Monitor**; the operator row with the
configured name and the theme toggle.

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
| New tasks | "New tasks · Auto", "New tasks · Fable · High", or "New tasks · Fable unavailable · Auto meanwhile" with a chevron, under the engine readout because quota belongs to the account; tinted while a choice is active; opens the Models dialog on its Tasks tab (§3.6.1); disabled while the overview loads |
| Operator row | name from configuration; theme toggle (light default, dark, persisted per browser) |

### 3.2 Project header

Desktop anatomy: project name (18px, 600); status line; actions: **Setup** status while setup needs attention (§3.12),
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
desktop, above any keyboard. L3's model choice is the button under the message box (§3.6.1); default
models, routing and removal live on the project's Settings page (§3.15).
A healthy project's header holds no Setup control. **Setup…** in the overflow menu always opens the
configuration checklist without replacing the conversation or draft; the header shows Setup only
while setup needs attention (§3.12).

Overflow menu: **Project settings…** (opens `/settings/projects/<name>`), **Setup…** with the current
setup status in muted text (§3.12), **Reset L3 conversation…** (confirm inline; `POST /api/l3/reset`),
**All settings…** (opens Settings with this project under **This project**), **Design boards ↗**
(present only when `GET /api/project/<name>` reports a design URL; opens in a new tab). The menu
holds no destructive item, so no tap there can remove a project; **Remove project** is on the
project's Settings page (§3.15).

The menu holds actions only; no engine, model or effort control appears in it. Opening it focuses
the first item; arrow keys, Home and End move between items; an inline confirmation takes focus and
**Cancel** returns it to its item; Escape closes the menu and returns focus to the three dots.

States: normal; L3 never started ("L3 has not started" and a **Start L3** button); error reading
the project (status line shows the error sentence; the conversation still renders from cache).

Removing a project means detaching its L3 (§3.15 Remove project). Success removes the managed row and
cached project views, selects a remaining project, and opens Needs you, or First run when the last
project leaves. A stale project, task or report URL shows
"Project not managed" with an Open projects link, or First run when no project remains. The folder
is still offered under its configured root; the path field supports folders elsewhere.
`ProjectLifecycleStates.html` illustrates the single removal-and-attachment flow at phone-sized
content widths. No separate detached-but-managed state exists.

### 3.3 Conversation

Anatomy: a single column, max 720px, bottom-anchored, newest last. Day dividers ("Today", a date).
Operator messages are right-aligned bubbles (`--bubble`, radius `--radius-bubble`, 15px). L3 replies
are left-aligned prose with no bubble (15px, line-height 1.65): paragraphs, lists, inline code,
links; no headings, no tables. A task card (§3.5) sits under an L3 reply whose turn created a task,
and Create task (below) under the latest reply that offered one. An improvement review shows at its
run's row as a line or one card, and a running review as a line with Stop at the end
([improvement suggestions](improvement-review/PROPOSAL.md#where-suggestions-appear)). When a change of
terms turns Weekly off, one line says so and links to the project's settings (§3.15).
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

#### Chat commands

A step only the operator can take (handing over private files, an admin step needing their password)
arrives as a command: a fence whose info string is exactly `run`, holding one line. Its block shows
the whole command in monospace, wrapped and never shortened, over a row with **Copy** and **Open in
terminal** (primary, terminal icon). Open in terminal shows the terminal of the conversation the message
is in — the task's terminal on a task page (phone Terminal tab, desktop panel's Terminal view), the
project folder's terminal from project chat — and types the command at its prompt with the cursor at the
end. It never presses Enter: the operator runs, edits or clears the line. The command reaches the terminal
only through page memory, never the URL or history, so a link, reload, Back or Forward types nothing.
Every other fence (`sh`, bare) is code with **Copy** at its top right and is never an action; a command
for another machine uses one. Inline code is unchanged. Only conversation replies and question cards in
a task or project conversation offer Open in terminal; elsewhere (Live session, Needs you, reports) a
`run` block has Copy only. The file reader's documents keep plain code with no controls.

| State | What appears and what actions do |
| --- | --- |
| Command | The block with Copy and Open in terminal. |
| Plain code block | Code with Copy; no action. |
| Copied / refused | Copy reads "Copied" for two seconds; a browser that refuses it reads "Couldn't copy", and the text stays selectable. |
| Not one safe line | A `run` fence with more than one line, or a control, invisible-formatting or line-separator character (tab, escape, zero-width, direction override): shown verbatim with Copy and "Not offered for the terminal: <reason>." |
| No terminal here | A task that is finished, rejected or has no worktree: Copy and "This task has no terminal now." |
| Tap | The terminal view appears, opening its shell or attaching to the running one (§3.10). |
| Typed | Once the screen has drawn output and stayed quiet for 300 ms (the prompt), the page re-reads the terminal; with no program in the foreground it types the command as a paste and focuses the screen. |
| A program is running | The terminal names a foreground program (vim, a build): nothing is typed; a notice above the screen, "<program> is running, so the command wasn't typed.", with **Copy command** and ×. The same notice explains a shell that shows no settled prompt within five seconds of the tap (nothing drawn, output that keeps coming, or a check that answers late), a failed check or stopped typing. A shell builtin reading input (`read`) is not a foreground program, so the command is typed into it; nothing presses Enter. |
| Reader not told | The page first tells Altitude the command so the terminal's reader (a task's owner, or the coordinator for a project terminal) hears once it has run. When that fails, the command is still typed and the notice reads "Altitude couldn't tell the task's owner to watch this command, so reply in chat once it has run." (in a project terminal, "the coordinator"), with **Copy command** and ×. |
| Terminal is off / couldn't open | The terminal's own card (§3.10); the command is dropped, so turning it on or Retry opens a plain shell. |
| Enter | Only the operator's Enter runs it: the keyboard's, or on phone the key row's **Enter** (§3.10), which runs the typed command without opening the soft keyboard. |

Phone (390×844) and desktop (1440×900) evidence: `web/e2e/run-in-terminal.pw.ts` walks these states
against real shells from saved task and project messages.

File references use ordinary accent-coloured underlined links, with no chip, icon, border or
background. Preserve the supplied absolute path, `file:///` URI or Markdown link label. Hover
exposes the full target; opening exposes it on both viewports. References outside code open a
separate read-only browser tab, retaining the conversation's scroll position and draft. Long paths
wrap. A Markdown angle-bracket destination or percent-encoded file URI supports spaces.

The reader shows filename, full selectable reference, Copy path and current contents. After task
archival it also identifies the current location. Markdown renders as in the shown-files reader
(§3.9.1), where no image is copied, so local images are **Image not shown** boxes and remote ones are
links; a small Raw toggle exposes its exact source. `.txt` stays plain text. Opening performs no
command execution, automatic external-resource fetch, download or native-app launch. The operator approved simple link styling
and bounded task-document access with rendered Markdown on 2026-09-15 Pacific.

| File state | Appearance and interaction |
| --- | --- |
| No reference / code | No new controls; inline and fenced code remain unchanged. |
| Hover / focus / touch | Ordinary link focus styling; keyboard activation or tap opens the same reader in another tab. |
| Loading / retry | Loading file; previous contents disappear while the new read is pending. |
| Readable Markdown / text | Rendered Markdown with Raw toggle, or plain text; commands remain selectable text. |
| Formatting code unavailable | Exact wrapped source remains readable under **Formatting unavailable** and a bordered **Reload page** button. No draft warning: this is a separate tab. Raw retains its pressed state and hides the formatting notice; returning to formatting shows it again while the shared import remains failed. |
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

Entrance: a row that first appears while the conversation is on screen (the operator's bubble at Send,
a reply's first words, a system line, a task card, a queued message, a message from another device)
fades in and rises 4px over 160ms ease-out. It takes its full height at once, so following the bottom
is not delayed. Rows present when the conversation opens or is returned to, older history, a message
changing state or place (a sending bubble replaced by its stored or queued copy, a queued message
taken by the turn) and a streaming reply growing have no entrance; identity is the send id or turn id,
decided once per mounted view. Leaving is immediate. Under reduced motion rows appear at once.

Project switching uses the rail on desktop and the header switcher on phone. These states apply
on both, including switching back before or after a response finishes:

| State when leaving Alpha for Beta | What appears and disappears |
| --- | --- |
| Empty, loading or reading history | Beta loads or shows only its cached rows, queue and active turn; Alpha's rows leave. |
| Typing an unsent draft | Beta shows its own draft, initially empty. Returning to Alpha restores Alpha's text; copying and pasting between projects preserves both drafts. |
| Send pending or reply streaming | Alpha's local prompt, typing indicator and streamed text leave. The accepted turn finishes in Alpha; Beta can send independently. |
| Both projects have sent a turn | Each conversation shows only its own turn. Either completion order preserves the other project's draft and reply. |
| Late HTTP refusal or stream error | Beta's draft and send state stay its own; no Alpha error or Retry appears there. The failure shows only on Alpha's message. |
| Switch back to a failed accepted turn | Alpha's stored prompt and failed-turn Retry appear only in Alpha; Retry resends that prompt to Alpha. |
| Listening, Stop-to-edit transcription or microphone denied | Leaving stops recording, releases microphone tracks and cancels unsent transcription; late results cannot fill Beta's draft. The new composer has its own microphone state. |
| Send requested while recording | Transcription and send complete for Alpha even after navigation. Returning while pending shows its original text, Transcribing and Cancel; failure restores Alpha's text. Beta's draft and send remain independent. The same rule applies when leaving a task for project chat. |

`web/e2e/project-isolation.pw.ts` walks these transitions with fictional projects and a disposable
file-backed service; delayed refusals and microphone results are controlled browser overlays.

#### Create task

When L3 is unsure whether the operator wants work started, its reply offers it instead of asking
(boards `ReplyTask`, `MobileReplyTask` and their state sheets). The reply ends with one quiet outlined
button, **Create task**, led by a plus, then the task's short title in 13px muted text. The title is
the button's accessible description. The button is 34px tall; on phone its touch area extends to 44px.
Pressing sends L3 the instruction `Create task: <title>` as the operator's next chat message, but the
conversation shows no message: the button changes in place, and its icon and colour say where the press
stands. The draft, images and voice input in the composer stay untouched. L3 creates the task from its
reply and the conversation without asking again, and its answer carries the task card. There is no
form, dialog or second copy of the reply.

| State | The button |
| --- | --- |
| Reply without an offer | Absent: ordinary answers, reports, system lines and replies that created a task show nothing. |
| Offered | Plus, Create task, and the title, under the latest reply only. |
| Working | Accent fill and a spinner, from the press until L3 answers; it ignores presses and keeps focus. "Create task sent" is announced once Altitude saves the press. L3's typing indicator follows. |
| Waiting for L3 | A clock while the press waits behind L3's current work, with × (Remove, 44px target) inside the pill. Remove brings the offer back. A press kept while no engine can run (§4.2) shows the clock without ×, as a kept message cannot be removed. The press is not listed among queued messages. |
| Task created | Green fill, check, "Task created"; L3's answer below carries the task card. It stays in the history. |
| Answered without a task | Muted check: L3 answered the press without creating a task. |
| Not sent | Altitude refused the press or did not save it: the offer stays with a short danger note (`role=alert`): Altitude's reason, "Not sent" or, when the conversation cannot be read, "Not confirmed". Pressing again is safe. |
| L3 could not answer | Danger outline, retry icon, "Retry": it sends the same press and never makes a second task. No failed-turn line. |
| Answered another way | Any message the operator sends or queues, typed, spoken or with images, retires the offer. A stale window's press is refused with "The conversation has moved on, so this was not sent." |

Decision selections (§3.8.2) use the same pill. A press runs as its own L3 turn, never folded into typed messages queued with it. Once its reply has left
the loaded history, a press reads as its message, with the ordinary queue controls or failed-turn Retry.

`web/e2e/create-task.pw.ts` walks these states at both widths against a disposable service running the
real coordinator verbs, queue and task store; lost and refused responses are browser overlays.

### 3.4 System line

One line, centred, 13px `--text-muted`: a dot, the turn's recorded time, the text, and **Show**. The
time reads "14:05", dated ("Sep 16, 14:05") when not today, exact on hover; a turn without one reads
**time unavailable**. A group line shows its latest turn's time. It stands for one system turn
or a run of them (§4.1). The dot is `--text-muted` for reports, restarts, and FYIs and `--danger`
for `incident`, `system-recovery`, and fault triggers. A failed turn of another trigger keeps the
muted dot; its line carries the failure.

What altd sends L3 is written for L3: its ids, revisions, authority notes and instructions never
appear in the conversation. Expanded: a card in the column with a header ("Report landed · <task
title> · 09:14", **Hide**), "What altd sent L3" as label/value rows only when the prompt has
structured fields (verdict, problems, signals, PRs, spend), "L3 replied" with the full reply, and links:
**Open task**, **Full report** (the task's report view), **Digest** when the reply recorded one.
The structured prompt's Task field supplies Open task and is omitted from the label/value rows.
A group expands to a list of its turns, each with its own Show.

States: folded; expanded; in progress ("L3 is handling a landed report for <task>", no Show yet);
grouped (N turns); failed turn (the line reads "L3 could not handle <what>"; Show reveals the
error); queued (below).

A queued notice, one altd has written for L3 that has not run yet (block, report, fault, restart, CI
recheck, terminal, upstream issue, owner message or any other trigger), is the same line in the queued rows (§4.2)
with an outlined dot in its tone and no Show: **<kind> · <task title> · <need>**. The need is the first
sentence of the task's open Needs you question, as plain words of at most 90 characters, or a report's
verdict; a notice with neither reads kind and task alone, and one without a task reads its kind. The line
ends with **Needs you**, opening that question, while the task has one, else **Open task**. A long title
wraps; it is never cut. `web/e2e/system-rows.pw.ts` walks queued, handled, card and Needs you states
on phone and desktop.

A selected L3 heads-up uses this same compact line, wrapping its full one- or two-sentence text
on phone and desktop. It stays outside routine groups (§4.1). No separate badge, dismiss control,
timer or notification surface appears.

When the engine L3 would answer on (its choice, else the engine of its last reply) is at a usage limit,
one heads-up says what happens to the operator's message: "Claude's usage limit is reached until 11:00
PM, so Codex is answering for now." (a model's limit on the same engine names the models: "Fable's usage
limit is reached until 11:00 PM, so Opus is answering for now."); with nothing able to run, "Claude's
usage limit is reached until 11:00 PM and no other engine can answer, so your message waits until
then." or "Claude and Codex's usage limits are reached, so your message waits until 11:00 PM." (the
earliest reported reset; with none reported, "until it resets" / "until one resets"). The time shows in the
reader's local time. A line appears when its text changes and is not repeated while its limit lasts; it
stays where it was written, so a waiting message's answer can appear above it. The waiting message keeps its muted **Send now**; picking another
L3 choice sends it at once. `web/e2e/usage-limit.pw.ts` walks these states on phone and desktop.

A notable turn, a system turn during which L3 created a task or filed a GitHub issue (§4.1), uses the
same compact line outside routine groups: a dot, the time, the reply's last paragraph, which names what
happened and the task or issue it produced in one or two plain sentences, then one link and **Show**.
The link is **Open task** (that task's page) when the turn created exactly one task, else **Open issue**
(the issue on GitHub, a new tab) when it filed exactly one issue and created no task; with several, the
line has no link and the card lists them. The text wraps in full on phone and desktop; it is never cut.
A turn whose reply failed after creating something stays notable: its line reads "L3 could not handle
<what>" with the same link. Its card is the ordinary system card plus an **L3 created** label listing
the created tasks as task cards (§3.5) and each filed issue as a link named by its title; the footer's
**Open task** still opens the task the turn was about. The dot keeps its trigger's tone. It is information, never a question: no
Needs you entry, badge or notification.

| Heads-up state or action | What appears and disappears |
| --- | --- |
| Empty history | Conversation empty text; no heads-up or background group. |
| Initial loading | Conversation skeleton; no placeholder FYI. |
| Selected FYI arrives | Its full concise text and Show appear between the surrounding routine groups. A reader at the bottom follows; a reader above keeps their scroll position. |
| Show on heads-up | The line becomes its FYI card with full text, Hide and Open task when linked; routine groups stay folded. |
| Hide on heads-up | The card and its task link leave; the compact heads-up returns. |
| Show/Hide on routine group | Routine evidence appears/disappears; the selected heads-up remains outside it. |
| Notable turn between routine turns | Its line, with Open task or Open issue and Show, splits the routine run; each side folds and counts only its own turns, and a single routine turn reads as its own plain line. |
| Show on notable turn | The line becomes its card: L3's full reply (or its error), then L3 created with each created task's card and each filed issue's titled link, and the footer links; routine groups stay folded. Hide restores the line. |
| Notable turn with several outcomes | The line has no Open link, only Show; the card lists every task and issue. |
| Notable turn whose reply failed | "L3 could not handle <what>", with Open task or Open issue and Show; the card shows the error and what was created. |
| Open task on notable turn | The created task's page opens; Back returns to the conversation. |
| Conversation read fails | Error and Retry appear; any cached heads-up remains. A successful Retry removes the error. |
| Listening or microphone denied | Composer states in §3.6; the heads-up remains unchanged and requests no permission. |

`web/e2e/heads-up.pw.ts` walks the heads-up and notable-turn states, task navigation and scroll
preservation at both viewports; `conversation.pw.ts` covers the unchanged listening/denied composer states.

Coordinator information messages use separate system lines outside routine groups: **sender →
recipient · summary · Sent/Incoming**, plus **Show**. Sent acknowledges inbox acceptance; pending
information says **Queued · next ordinary turn**. Show replaces the line with a Coordinator message
card containing direction, summary, the exchange reference and full diagnostic text; Hide restores
the line. Diagnostic code stays read-only; link destinations appear literally, including beside
Markdown labels, without inferred
task, file, issue or terminal actions. Reply rows reverse direction on the same exchange. No send,
reply, approval or removal control is added to this information surface.

| Information state | What appears and disappears |
| --- | --- |
| Empty / initial loading | Existing empty text / skeleton; no placeholder exchange. |
| Accepted | Source Sent row and one folded recipient inbox row appear. |
| Show / Hide | Full text and exchange reference appear / disappear. |
| Supplied on an ordinary turn | Inbox row leaves; one incoming history row appears before its receiving turn and reply. |
| Receipt failure | Separate visible warning; the ordinary answer remains. Information without saved proof may repeat. |
| Reply | A separately folded reverse-direction row joins the same exchange. |
| Sensitive text refused | No message or inbox row appears. |
| Registration changed | Pending line says Registration changed · not supplied; no action is offered. |
| Read error | Existing error and Retry; already loaded rows retain their existing read-state behavior. |
| Listening / denied | Existing composer behavior in §3.6; information rows request no permission. |

`web/e2e/project-messages.pw.ts` specifies phone/desktop and emulated iPhone states. The unchanged
listening/denied behavior remains covered by `conversation.pw.ts`; these messages have no composer.

The report view has a back link to the task and a "Report" title. It reads the task's report and
shows plain sections when present: Landed (PRs, main checks and deploy), Review, Blocked, Decisions,
FYI, Follow-ups, Deviations, Spend, Report notes, and Digest. Report notes and the digest are prose;
Digest links land at its section. States: loading (a title-shaped skeleton); empty ("No report
yet."); error ("Could not load the report." and Retry).

#### Adversarial review

Task details shows two bordered boxes, **Proposal review** and **Implementation review**, with no
wrapper around them. Each box shows its latest review: a state icon, "<name> · <state>" (requested by
you or L2, in progress, stopping, done, didn't finish, skipped), the reviewer's one-sentence verdict,
counts and **View**. With no review yet it says "Not reviewed yet", or why review is unavailable. One
button starts the next review when one can start: **Request**, **Review again** after an assessed
review with nothing open, or **Try again** after one that didn't finish. There is no button while that
kind is queued, running or has open findings, including an earlier review's. Finished tasks show the boxes without buttons, and hide a
kind that was never reviewed. The reviewer is a separate invocation; same-engine review is the
ordinary path, and an alternate engine is used when one is configured.

Requests queue on any open task whatever L2 is doing. A waiting or blocked L2 is woken; a stopped or
faulted task is not resumed, and its request waits for the next resume. Requesting closes task
details and preserves the draft, reading position and listening state.

The conversation shows one card per kind, at the latest request's anchor; earlier iterations fold
inside it. The card's sentence reads:

| State | Sentence |
| --- | --- |
| Requested by you | Queued. L2 starts it after its current step. / Queued. L2 starts it when it resumes. |
| Requested by L2 | L2 asked for a review of its proposal (implementation) and starts it shortly. |
| Waiting for the reviewer slot | Waiting for the reviewer: another review is running on this machine. |
| In progress | Reviewing the proposal (implementation)…, with **Stop** and "Requested by L2/you". |
| Didn't finish | The failure, or "Stopped before it finished.", with **Try again**. |
| Done | The verdict, then counts: "2 findings, both resolved", "3 findings · 2 open, blocks merge", "L2 is responding", "Review 2", "earlier version", "1 open in an earlier review, blocks merge". |
| Skipped | "Skipped by you" or "Skipped by L2". |

The chevron opens the card: each finding with **Open**, **Fixed**, **Dismissed** or **New**, its
severity, body, location and L2's answer; what the reviewer did not cover; earlier reviews as one line
each, which opens to that review's own findings, footer and Skip review, since a review an additional
review left in the merge gate still blocks merge; and a footer naming the reviewer ("<engine> · same engine as the task" or "alternate engine"),
**Technical details** and **Skip review**. Technical details shows the model, any fallback reason,
focus, the captured proposal and the reviewed and assessed checkpoints. Skip review asks for
confirmation without a reason; the review stops blocking merge and its findings stay visible. **View**
in task details, and a question's review box (§3.8.3), open and focus the card. Open findings block merge; no findings never means
permission to merge.

Loading and saving disable the buttons. Unavailable explains why in the box; denied and uncertain
delivery use inline feedback with a saved-status refresh. `adversarial-review.pw.ts` walks these states
at phone and desktop widths; `adversarial-review-integration.pw.ts` walks real persisted requests,
same-engine and alternate-engine fixtures, L2 requests, failure and try again, unavailable,
earlier-version coverage and dispositions.

### 3.5 Task card (inline and in the work panel)

Anatomy: state dot, title (600), meta line "<state> · <engine> · <age or wait>", chevron. Click
opens the task conversation, at its current operator question when one is open (§3.7).

States by task state: planned (a queued task with a planned wait: "Planned · Waiting for <reason>.",
muted queue dot and a reason that wraps on phone and desktop); queued ("Queued · <hold>", where
the hold is the queue's own reason: "waits
for a free task slot", "waits for an available coding engine", "waits for Altitude to restart", "waits
for resume at <time>", or plain "waits for dispatch"; never a file lease, which the queue does not
hold; see [concurrency](../../docs/ARCHITECTURE.md#task-lifecycle)); running ("Running · <model> on <engine> · started N min ago"); blocked waiting
on L3 ("Waiting for the coordinator: <short prerequisite>.", the running dot: L3's answer is Altitude's own work, and the dot turns amber
only when L3 escalates to the operator; the rail's §3.1 dot follows the same rule); blocked on the
operator ("Your turn · N questions", "Your turn · review PR #N", or both, plus a sentence explaining the answer or review wait,
amber dot); replying to the operator ("L2 replying to you", running dot); running ("L2 working");
blocked by a fault (a short explanation of the interruption and the known coordinator prerequisite, red dot);
operator-stopped (confirmed Stop explains Continue; a stop request alone points to the task for confirmation, red dot);
owner/daemon-parked without a question, review, fault or operator stop (a short reason, or "Work is paused; no reason is recorded.", idle dot);
reported ("Waiting for the coordinator to check the task’s report.", running dot, or a review wait);
done ("Done · PR #N merged", shown under Done this week); rejected ("Rejected", under Done this
week).

Open operator questions remain visible while running or queued, independently of execution.
A fault keeps its red dot and cause even when a separate question also needs an answer. An
operational pause without a question uses its actual status, never an inferred request to decide.
Rows and pages share the same read-only explanation. Authored prerequisite excerpts use at most
120 characters, excluding diagnostic output and source identifiers; full reasons remain in Task
details. A worker death reads **The task session ended before completion.** plus its current
coordinator prerequisite, or **Waiting for the coordinator to check the blocker.** Other faults
say **A system problem paused work.** without inventing a cause. No explanation claims a restart,
recovery or retry without a corresponding record. Named planned prerequisites use their task title
when available and explicitly identify a prerequisite task when only its slug remains.

The same card is the row in the work panel and the card under an L3 reply that created the task
(§5.2 note 4); a slug the project no longer lists renders as the row with the slug as its title.

### 3.6 Composer

One composer everywhere (project chat and task conversation). Anatomy: rounded
field (`--radius-composer`), placeholder naming the owner ("Message L3 about <project>",
"Message the L2"); on the project chat, the **L3** button on phone and desktop ("L3 · Auto",
"L3 · Fable · Low"; none on the task conversation, because a started task keeps its model, §3.6.1; on
phone it steps aside while recording and transcribing so the waveform fills the row); Add images button; microphone button; send control. The send control is the arrow in an accent circle
in every state, with no visible text; its accessible name is "Send" ("Queue" while busy). A hint line under the field,
12px muted on desktop. The structure is the same on both widths: the field spans the box on top
and the controls (L3 button, Add images, microphone, send, and the recording cluster while listening or
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
at both phone and desktop widths; `mobile-chat.pw.ts` walks the task page's pending and settled bubble, and
`task-message-states.pw.ts` walks the task conversation's message states.

| State | What is on screen | What changes |
| --- | --- | --- |
| Idle | placeholder, mic, arrow disabled | typing enables the arrow |
| Typing | draft text, arrow enabled | Enter or the arrow: the draft becomes a bubble at once, the field clears |
| Sending | the bubble is in the conversation at once at 60%, with no other cue, until the server acknowledges it (stream accepted, queued receipt or stored row); screen readers hear "Sending" | accepted: the same bubble settles to full opacity in place over 240ms, and the stored copy replaces it without a duplicate row, re-layout or scroll jump; leaving the conversation (the phone's Work tab, another project) and returning shows each sent bubble and its streaming reply until the server's record has it, one bubble per send even for identical words |
| Accepted; stream or refresh interrupted | sent bubble or saved queue row; the composer stays cleared and newly typed text stays | refresh reconstructs history, active turn and queue by their IDs; read-error Retry only reads; no unsent Retry or invented answer failure |
| Not sent | the bubble stays in place at full strength with a red **!** in its gutter; under it, right aligned, `Not sent: <reason>.` (`role="alert"`) then **Retry** · **Edit**; the composer is idle | Retry sends the same words again in place with a new send id, back to Sending; Edit removes the bubble and puts its words (and images) in the field ahead of anything typed since, on a new line, with focus at the end |
| Not sent, its question or stop changed (task conversation) | as Not sent, with **Edit** only | the message answered a question or question group that has since been revised, continued a stopped session that has since changed, or went to the running session that has since stopped; Edit lets the operator reread and send again from the field |
| Unconfirmed | the bubble stays at full strength with an amber **!**; under it `Unconfirmed` (`role="status"`) then **Copy to draft**; the composer is idle | no send Retry; Copy to draft puts the words in the field the same way and keeps the message; a read that shows its send id makes it the ordinary sent or queued message; HTTP headers, server errors and matching text alone do not prove delivery |
| Busy (L3 mid-turn) | the same arrow, enabled with a draft; header names the active work; desktop retains its mid-turn hint | the arrow appends to `queued[]`; the message joins the queued group (§4.2) as an outlined bubble |
| Opening microphone | "Opening microphone…" with an indeterminate spinner inside the composer box; existing text remains readable and read-only. A restart waits for recognizer shutdown (at most three seconds), followed by the waveform audio context's asynchronous close (at most three more seconds), before opening another microphone. Its waveform graph connects before capture starts, without waiting for graph activation | Cancel or Esc restores editing and prevents the waiting attempt from opening audio later; denial or failure preserves the draft |
| Listening | Read-only, selectable draft; "Listening… Stop to add text, or Send." with activity indicator inside the box. With the browser backend, recognized words appear after the draft while speaking and the last phrase may still change; English phrases gain punctuation and capitals once final, while the phrase being heard shows as heard; once the text passes the field's height, the field follows the latest words. With either backend, new words flow in letter by letter at a steady pace timed to finish as the next update arrives (about speaking pace, faster while catching up); a revised word changes in place without the text backing up, and under reduced motion each update appears at once. Stop, Send and a recording that stops early use every recognized word, including any still flowing in. Cancel, Stop, arrow, waveform and timer share one control row: on desktop they sit together at the right beside the engine pill with a crisp 168px waveform; at 390px the waveform fills the row without wrapping | Cancel or Esc: back to editing, nothing added; the X leaves focus on the microphone so no phone keyboard opens, and Esc returns focus to the field; Stop or Ctrl/⌘+M: land the words in the draft; the arrow or Enter: land them, then send at once; a later browser dictation in the page whose live microphone stays exactly silent for three seconds before any words (iOS 27 Safari, [WebKit bug 326069](https://bugs.webkit.org/show_bug.cgi?id=326069)): back to editing with nothing added, focus on the microphone and "The microphone went silent. Close and reopen Altitude to dictate again. Typing works." |
| Transcribing | "Transcribing…" and an indeterminate spinner inside the box; draft stays readable and read-only, mic and arrow disabled, Cancel available. Desktop waveform and timer freeze. Host voice finishes its last words here; the browser backend only waits, at most three seconds, for the recognizer's last phrase and then, at most ten seconds (three while the model still loads), for its punctuation | after Stop: Landed; after Send: append and send once through Typing → Sending (Busy queues); Cancel, failure or timeout restores editing and preserves the draft; failure: "Could not transcribe. Typing works.", and a recognizer error keeps the words already shown; empty transcript: send nothing, return to Idle or Typing |
| Landed | the transcript is appended to the draft, cursor at the end, arrow enabled; nothing else appears (no transcript box, issue #195). When English punctuation did not finish, the words land as heard and a muted hint stays until the next capture or send: "Added without punctuation: still loading. Next time it will be ready." while the model is still downloading, otherwise "Added without punctuation: this browser could not run it." | the operator edits or sends as with a typed draft |
| Denied | mic stays available; hint reads "Microphone blocked in the browser. Typing works." (microphone or recognizer refused) | the hint stays until the next tap, which asks the browser again; a lasting block shows the hint again |
| Unavailable | mic hidden; hint reads "Voice needs HTTPS" on an insecure origin, "This browser has no speech recognition. Typing works." when the browser backend has no recognizer, or nothing when host voice's browser lacks audio capture; no mic until the installation's backend is known | typing unaffected |
| Host voice | Starting: "Starting voice…" with the spinner until the microphone delivers audio; then Listening as above, with words from this computer appearing about a second behind speech (the last words may still change). Stop or the arrow: Transcribing for about half a second while the host finishes. Not set up: the mic stays; a tap shows "Voice needs a one-time download on this computer. **Set up voice**" (or "an update"), linking to Settings → Voice input; while setting up, "Voice is being set up on this computer. Typing works."; after a failed setup, "Voice setup did not finish. **Retry in Settings**. Typing works." Cannot run here: mic hidden, "Voice isn't available on this computer: <reason>. Typing works." | a recording that stops early (connection lost, speech process stopped, microphone interrupted or silent, busy on another device) keeps the words already shown, restores editing and says "Voice stopped: <reason>. Typing works." |
| Host voice, connection lost | Listening continues: "Connection lost — still recording. Your words will catch up." and the timer keeps running; the recording stays in page memory only. When the page reconnects to a restarted server, "Catching up…" while the recording is replayed; the words already shown stay until the replay passes them, so text never shrinks. After Stop or the arrow while offline: "Waiting for connection…" with the spinner and Cancel. A third recording while two are still sending: "Voice is still sending an earlier recording. Typing works." | reconnect: the words catch up and Stop or Send completes as usual; a voice Send completes in the conversation it was sent from even after navigating away, and a Stop is cancelled on leaving; Cancel discards the recording and keeps the words shown; after two minutes without a connection: the words already shown stay, "Couldn't reach this computer: your recording's last words weren't added." and a voice Send returns its text unsent to its own conversation's draft |
| Engine pin | Auto, or an engine name | `POST /api/chat` carries the pin; it covers chat and system turns alike and stays until changed |

Voice is capped just under ten minutes: the client stops at 9:55 to stay under the server’s ten-minute limit.
The timer turns `--danger` in the last minute. Audio never becomes part of task or chat state.

The installation's voice backend (`GET /api/voice`, set with `alt machine set --voice`) decides how
words arrive: `host` (the default where this computer can run the speech model) streams the
microphone to this computer, which transcribes it with its own speech model and shows words while
speaking; `browser` (no setup; the default elsewhere, macOS for now) runs the browser's own speech
recognition and shows words while speaking. Words are never simulated: only recognition that produces them
progressively shows them progressively. `VoiceStates.html` shows each state at desktop and phone width.
Browser recognition of English is punctuated on the device by a bundled model that adds only
`.` `,` `?` and capitals; dictated words are never rewritten. Other languages keep the recognizer's
formatting.
Explicit Send retains its transcription operation through route navigation in the current document;
returning to its source shows the pending text and Cancel. Stop-to-edit transcription cancels on leaving.
Reloading or closing the document ends client voice processing; it does not replay audio later.
The reason is the server's own message, first letter lowercased, with one final
period (`Not sent: no L3 for this project.`), or `Not sent.` without one. The actions are underlined link
buttons separated by a middle dot; the line wraps between words and keeps its actions together, within
90% of the column. A message whose saved images cannot return to the picker offers Retry only. A failed or
unconfirmed message is not activity, so the next message can be sent at once, and leaving and returning
keeps it. The browser tab keeps one recovery copy per not-sent or unconfirmed message, keyed by its send
id; Retry replaces it, Edit and a read that shows the send id remove it, Copy to draft keeps it. After a
reload, or after pairing again (which clears the messages on their way), the copies no message shows return
to the field as a draft in send order, each on its own line, ahead of any draft, with one hint: "Could not
confirm delivery. Check the conversation before sending again." when any copy is unconfirmed, otherwise
"Not sent."; a send that fails after pairing again cleared its message returns the same way at once. Editing
a not-sent draft, or clearing the field, removes the hint and leaves an ordinary draft. Image
messages recover their caption only. Nothing is sent without the operator pressing Send,
and a recovered draft goes wherever a typed one would. Removing a project in this browser drops its copies.
A later callback from an earlier send cannot change a newer pending message.

On the task conversation each message also keeps what it was sent to: the question or question group
revision it answered and the stopped session it continues. Retry sends there again, and is offered only
while a Send would go to the same place; otherwise the message offers Edit only. Retry waits while Stop is in
progress. While the page has lost access ("This page lost access. **Refresh**" above the conversation) or
the task no longer takes messages, failed messages keep their line without actions; Refresh brings them
back. `web/e2e/project-isolation.pw.ts`, `task-lifecycle.pw.ts`, `conversation.pw.ts` and
`task-message-states.pw.ts` walk accepted/interrupted, failed refresh, reconnect, queued, refused,
changed-target and unconfirmed states at both viewports.

Image input uses the same toolbar and adds no permanent row when empty. A conditional strip holds
up to four short previews with individually named 44px Remove targets. Native selection and desktop
image-file paste preserve ordinary text paste; image-only sends work. PNG/JPEG/static WebP limits
are four images, 10 MiB each, 20 MiB total, 25 megapixels and 8192 pixels per side. Selection remains
local until Send; leaving the conversation releases it and late results cannot fill another draft.

| Image state | Visible behavior and transition |
| --- | --- |
| Empty / cancel picker | Add images shares the existing controls; no strip or status row. Cancel changes nothing. |
| Selected / remove | Preview strip appears; last Remove removes it and keeps text. Invalid format, unreadable preview or size/count rejection names recovery inline and preserves other selections. |
| Sending | Pending bubble holds text and previews. The selection clears at Send and the composer stays free. |
| Confirmed refusal | No partial message is accepted. The message shows Not sent with its previews; Retry sends the saved images again and Edit returns the caption to the field and the images to the picker. |
| Uncertain response | The message shows Unconfirmed with its previews and Copy to draft (the caption); a read that shows it settles it. Navigation remains available. |
| Queued / delivery failure | Images retain their caption. Project rows and unclaimed task messages offer Remove; removed task messages show a muted placeholder without images. Failed project-turn Retry uses saved images; task recovery keeps existing authority. |
| Sent / viewer | Thumbnails belong to the saved message. Open shows the full image in a modal with Fit/Zoom, Close and Escape; closing restores thumbnail focus, or focuses the same image's stored thumbnail once history admission has replaced a queued opener. Archived tasks retain viewing without a composer. |
| Loading / missing / denied | Image-sized placeholder names loading or the error; Retry repeats only the private read, never the send. Text remains readable. |
| Capability unavailable | Add images explains why input is unavailable. Known converter refusal restores selections; removing them allows text. |
| Voice / scope changes | Listening/transcribing retain previews and lock text editing. Cancel preserves preexisting text; empty/failed transcription sends nothing. Explicit voice Send captures its images and completes for the original conversation after navigation. Other unsent image selection is released on leaving. |

The approved [image interaction contract](IMAGE_INPUT.md) explains retention and private
agent delivery. `web/e2e/image-input.pw.ts` drives these states with real storage/API and deterministic
engine fixtures at 390×844 and 1440×900; named screenshots live in `web/ui-artifacts/results/image-input*`.

### 3.6.1 Models dialog

One dialog chooses a model and effort ahead of routing, for two scopes, each where it lives. The **L3**
button under the message box describes who answers what is typed there and opens the dialog on
**L3 · <project> only**; the **New tasks** control beside the quota (rail, phone Monitor and Work) opens
it on **Tasks · All projects**. Outside a project only the Tasks tab exists. Each tab's label says how
far it reaches; the tabs are a tab list, and arrow keys, Home and End switch them. The dialog's name
includes the open tab. On phone it is a bottom sheet; on desktop a centered dialog.

Each tab starts with **In use: <choice>** and its scope: "who answers you in <project>'s chat", or
"every project; tasks that start from now, including queued ones. Started tasks keep theirs." The
models are a radio group: **Auto** (project routing and defaults), each alias and engine default the
engine seam names with its engine, and **Other model…** with an engine and an exact model id (no
spaces). **Effort** is a radio group of Default and the levels the chosen model's engine accepts; Auto
offers every engine's levels and requests that effort on whichever engine routing picks. Focus starts
on the current choice. The dialog holds one unsaved choice, owned by the open tab: switching tabs or
closing drops it, and the tab shows its saved value again.

**Use for L3 in <project>** or **Use for all new tasks** saves the model and effort together, only for
that tab, and closes the dialog; the closed control then shows the new value. It is enabled only for
a changed, valid choice. **Back to Auto**, shown while a choice is saved, removes it in one tap.
The L3 tab says "Applies from L3's next reply." with the last reply's reported model and effort. The
Tasks tab says that each project's Auto picks while the chosen model is unavailable, lists every
project whose **Only <engine>** routing keeps its tasks elsewhere ("harbor runs tasks only on Codex,
so it keeps its Codex model. Change", linking to its Routing), and ends with the hint "For one task,
tell L3: “use Opus at Max for this”." With a saved L3 choice, the L3 tab adds "When <model> is
unavailable, Auto answers instead."

| State | What appears and what actions do |
| --- | --- |
| Loading | The closed control is disabled; an opened tab says Loading models…. |
| Read failed | "Could not load models" with Retry. |
| Saving | **Use** holds its width and shows a spinner with accessible name Saving…; choices and the other tab are locked. |
| Save failed | The server's reason and Retry on the tab that saved; In use keeps the earlier value. |
| Changed elsewhere | "Changed in another window." with Reload, which drops the draft and shows the current value. A save compares against the value the draft started from, so a refresh while editing does not hide another window's change. |
| Unavailable | The closed control reads "Fable unavailable · Auto meanwhile"; the tab names the reason. |

Escape, × and, on phone, the scrim close the dialog and return focus to the control that opened it.
Data: `GET /api/overview` `new_tasks` and `POST /api/new-tasks`; `GET /api/defaults/<project>`
`l3_choice` and `POST /api/defaults` with setting `l3_choice`. Both writes send the value shown as
`expected`, so a change made elsewhere is refused instead of overwritten. `web/e2e/models.pw.ts` walks
these states at 390×844 and 1440×900. Board: ModelsStates.

### 3.7 Work panel

Work answers “What is happening in this project?” Anatomy: "Work" and "N current · N done this
week"; **Current** with every unfinished task once as a compact status row; **Done this week**
folded to a count, expanding to rows. On phone it is the selected project's Work tab with the
same sections, under one **New tasks** line (§3.6.1). Task totals are labelled text, not attention badges. Work contains no question
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
refresh notice and disabled sending. During submission, **Send N answers** keeps its label with a
spinner and cannot be repeated. Sending anything hands the turn back: the task's card leaves Needs you
and the badge drops, though every question stays open in the record. The chat shows each answer as
§3.8.2 describes. When
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
woken alerts only while Altitude is open, which on a phone means while it is on screen. While a push
service refuses Altitude's alerts, the line names that service and the reason it gave, says its device
gets no alerts and offers turning alerts off and on there to subscribe it again; the
line returns to the reach once a push gets through. An unavailable alert control looks disabled and exposes the browser limitation or permission recovery instructions as its accessible description and on press. Every state
leaves Needs you, its cards and all typing untouched.

Each newly published operator question alerts once, titled with the project and carrying the task name
at most — never question, conversation or incident text. What a banner names, how often alerts come and
whether they sound follow the device's choices in Settings (§3.15); the formats below are the default,
project and task. A question asked again after the operator replied
alerts again, the same way; a rewording, an escalation or L3 settling one member while it waits does not. Activating it opens that decision in the running
app. Faults, stopped tasks, reviews and completed work stay in Needs you without an alert. A grouped
ask alerts once. On a device without push, a decision already on screen, in Needs you or its owning task,
is recorded without alerting; a device push wakes gets the alert without sound while Altitude is on screen.
Refreshing, reconnecting, polling and other tasks' activity repeat nothing, and turning the switch on never
announces what is already waiting. A question shows in Needs you at once but alerts only once L3 has had
its turn and the owner has stopped with it still open (or an hour after it was asked). No alert is ever a
bare "Altitude": out of reach it reads **A decision needs you** / *Altitude is out of reach, so this alert
can't name it.*, and a decision settled just before the device read it shows **No decision needs you now**.
A wake that finds nothing new never says so: it names, silently, what still waits on the operator, leaving
out decisions L3 or the owner is handling first. One decision reads **<project> still needs a decision** /
*<task>* and opens it, replacing that decision's banner where the platform replaces by tag; several read
**Still waiting for you** with one *<project> · <task>* line each, oldest first, and open Needs you. With only
held decisions it reads **No decision needs you now** / *L3 or the task's owner is handling it first.*
An alert whose decision is answered, withdrawn or superseded closes when Altitude is next open on that
device or the next alert arrives.

With **Project only**, every banner keeps its title and drops the task: one decision reads **<project>
needs a decision** (or **still needs a decision**) with no body, and **Still waiting for you** lists one
project per line. With **Only that a decision waits**, one decision reads **A decision needs you** (or
**A decision still needs you**) with no body, and **Still waiting for you** reads *<n> decisions*. With a
pace, decisions that arrived meanwhile come in one banner, **<n> decisions need you**, with one line each as
the device reveals (none when it reveals nothing), opening Needs you; one alone is named as usual. The out
of reach and settled banners are the same in every mode. Sound off makes every banner silent.

#### 3.8.2 Decision selections

A press on a decision shows as the action it took, in the Create task pill (§3.3), never as a message
the operator typed (boards `DecisionChoiceStates`, `MobileDecisionChoiceStates`). The record is the
same: each press saves the operator message it always saved, and the owner cites that message.

In the conversation, a quick answer or an approval sits on the operator's side as a pill with the
option's own label ("7 days", "Approve merge") and, beneath it in 13px muted text, the question it
answers or the PR it approves. Answers sent together show one pill each. An **Other…** answer keeps a
bubble with its words and its question beneath; a typed reply, with or without question context, and
any message with images keep their ordinary bubble. A pill follows its message's delivery record and
claims no more than Altitude observed:

| State | The pill |
| --- | --- |
| Waiting for the L2 | Clock. While Altitude can still remove the message (an approval queued for a checkpoint), × (Remove, 44px target) sits inside the pill and **Send now** below it; a quick answer is never removable. |
| Sending to session | Spinner while the L2's session takes the message. |
| Sent | Grey check once the session has the message. |
| Delivery unconfirmed | Clock with **Delivery unconfirmed** beneath; no check and no ×. |
| Decision recorded | Green fill and check once the owner records a decision citing this message. |

A message is a quick answer when a question's saved response names it and the response text equals one
of that question's options; an **Other…** answer worded exactly like an option shows as that option.
A message is an approval when its text is exactly the one the merge check accepts ("Approved: merge PR
#N.", optionally with "at" and a short head), so those words typed in the composer show as the action
too. Both come from the saved conversation, so a reload, another device and a mixed batch look the same.

On a card, in Needs you and the chat, **Approve merge** is the primary pill and changes in place: accent
fill and a spinner while sending, keeping focus and ignoring presses; then a clock while the message
waits for the L2. A refused or failed send turns it into a red **Retry** with **Not sent**
(`role=alert`), and a denied write disables it with "You cannot approve here." **View PR #N** and the
hint stay beside it. Saving hands the turn back and the card leaves Needs you. Where a card stays in
view with a saved answer (the owning chat after re-parking, or a member answered in another window),
an option shows its pill and a typed answer its words under its delivery state, until the owner
records the decision.

`web/e2e/decision-selections.pw.ts` walks these states at both widths against real question, review
and delivery records, with a refused send as a browser overlay.

#### 3.8.3 Review status

A question that asks the operator to approve a proposal or a merge shows its review in its own small
box, so whether it was reviewed is visible in Needs you and the conversation without task details:

| Question | Box |
| --- | --- |
| An open proposal approval: a question showing files, or one its owner marks as a proposal | **Proposal review** |
| The held PR's **Review before merge** card, or an open operator question about the held PR (§3.10) | **Implementation review** |
| Any other question, and a resolved or withdrawn one | none |

The box sits under the question, the files it shows or the merge card's detail, above the recommendation. Its
anatomy follows the task card (§3.5): a 16px state icon, the review's name (14px, 600) over its state
(13px, muted), and a chevron; bordered, card radius, at least 44px tall, full card width up to 480px.
Its accessible name is "<name> · <state>". The state reads the latest review of that kind:

| Review | State | Icon |
| --- | --- | --- |
| None recorded | not done | amber !, dashed border, no chevron, not a link |
| Requested / running | requested / in progress | clock / spinner |
| Done, no findings | done, no findings | green check |
| Done, L2 has not answered its findings | done, N findings pending | amber ! |
| Done, findings still open in a current review | done, N open findings | amber ! |
| Done, every finding fixed or dismissed | done, N findings resolved | green check |
| Failed or stopped | didn’t finish | amber ! |
| Skipped by L2 as not needed | skipped by L2 · <its reason> | grey dashed circle with a dash |
| Skipped by the operator | skipped by you | grey dashed circle with a dash |
| Requested, running, failed or skipped while an earlier review of the kind has open findings | the state, then " · N open findings" | amber ! |
| Any of these covering an earlier version | the state, then " · earlier version" | amber ! |

"Earlier version" is the review card's own comparison (§3.4), so the box and the card agree. The box
never requests a review and has no ids. It opens `/projects/:name/tasks/:slug?review=<id>`, which
scrolls to the review card, opens and focuses it, adding one history entry. Back (browser, phone header
or desktop crumb) returns to Needs you with that card in view, or to the conversation with the question
or merge card in view and focused; a question revised meanwhile returns at its latest revision, even when the
conversation's link named an older one, and a
card that has left Needs you, or a merge card that has gone, leaves the page where it was. Modified clicks open the review without recording a return.
`web/e2e/review-status.pw.ts` walks Needs you and the conversation at both widths: resolved, open,
the longest state, no review, a skip by the owner and no box, each review and Back, and Back after a revision.

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
While a group has an open member, its closed and withdrawn members fold into one collapsed
**N earlier questions** row above it, so the card leads with what is open; a link to one of them opens
the row. An open member waiting on L3 carries **L3 is handling this** and no controls.
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

#### 3.9.1 Shown files

An owner shows the operator files from its task worktree (`--show`, [CLI](../../docs/CLI.md#showing-files)):
at most one document (`.md` or `.txt`) and images. They appear as what they are, under the owner's reply
text or inside a question card in the conversation and Needs you, never as a text link. Showing is a
read: it answers no question, releases no hold and changes no question state. Boards:
[`show-files/`](show-files/) (this section outranks them).

**Images only.** Thumbnails in one row: 96 px tall on phone, 120 px on desktop, rounded, each cropped
to its own aspect between 3:4 and 4:3. At most three show; a fourth tile reads **+N** over a dimmed
thumbnail. Each tile is a button ("Open image timeline.png", the fourth "Open N more images") that
opens the gallery at that image.

**A document** is one page card, full width on phone and at most 420 px on desktop, bordered with a
press state: a 120 px cover strip cropped from the document's first copied image (or the first shown
image) when there is one; a document icon, the file name in bold and a chevron; and two muted lines,
the document's first heading, an em dash and its first paragraph, cut with an ellipsis. The whole card
is one link ("Open README.md"). While the reader shows its document on desktop the card carries an
accent border.

**Versions.** When a later reply or question in the task shows the same document path, the older
card's cover turns greyscale at reduced opacity with an **Earlier** chip in its top-right corner; name,
excerpt and chevron keep full contrast, because it still opens. A card without a cover carries the
chip after its name.

**Reader.** A card opens `/projects/:name/tasks/:slug/view/:messageId` (a reply) or
`.../view/:questionId/:revision` (a question). The task page stays mounted underneath, so its scroll
position and unsent message are untouched. On a desktop wide enough for the inline live panel
(1280 px, §3.10) the reader is a panel of about 55% beside the conversation, in the live panel's place,
and the conversation stays usable; its header holds a document icon, the file name, a **Newer** pill
when a later version exists, and a close **×**. On phone and narrower desktops it covers the screen,
with **×** at the left, the name and **Newer**; the page beneath is inert. Close, Escape and browser
Back return to the conversation (or Needs you, when opened there); with no app history behind it,
close opens the task conversation. **Newer** opens the latest version in place, replacing the history
entry. There is no meta line, path line or Raw toggle: the reader is the document.

Content: images named with `--show` that the document does not embed, each full width with rounded
corners and an expand badge, then the document. Markdown renders as GitHub does, with its safe HTML
subset (`<p align="center">`, `<details>`, `<img>`); a `.txt` document is wrapped monospaced text.
Inside it:

- A copied image renders at its natural width, narrowed to the column, with an expand badge; it is a
  button ("Open image phone.png") that opens the gallery. Inside a link it keeps the link and has no badge.
- A remote image is never loaded: an image icon and its alt text (or host) link to the surrounding
  link or the image, in a new tab.
- A local image that was not copied is an outlined box, **Image not shown**, with its path in muted
  monospace.
- `#heading` links scroll within the reader; web and mail links open a new tab; a relative link to
  another file is plain text with its target as the tooltip.
- Wide tables and code scroll inside their own block; the reader never scrolls sideways. Code has no
  Copy or terminal action. No script, style, frame or form from the document runs or renders.

The formatting recovery notice is left aligned with 8 px gaps and 20 px before source text. In an
expanded system information message it also has 16 px above it, separating it from card metadata;
the same notice and inert source retain Hide and the conversation draft. Reload page explicitly
reloads the whole page, clearing unsent text only on that action. The button has a 44 px phone touch
target. Content-read failures keep their existing Retry, and unrelated renderer errors retain the
route's error page. Document styles load with the app; formatting code remains deferred.

**Gallery.** The image viewer shows one set's images in order (extra images, then the document's in
document order), with a quiet **2 / 5** count in its header, **Zoom image** and **Close image**, and
previous and next arrows at the sides (hidden at the ends). Arrow keys and a horizontal swipe move
between images; while zoomed a swipe pans instead. The operator's own chat images use the same viewer.
Opening adds one history entry and moving adds none, so Back, Escape or **Close image** always closes
the gallery to the exact place it was opened from, and the next Back closes the reader.

| Where | State | What shows |
| --- | --- | --- |
| Card | Ready | Cover, icon, name, chevron, excerpt. |
| Card | Cover or thumbnail loading | A flat grey block of the same size; no text. |
| Card | Cover failed | The card without its cover strip; it still opens. |
| Card | Thumbnail failed | The tile becomes a bordered button with a broken-image icon over a circular retry icon ("Retry image timeline.png"), its only control. |
| Card | Earlier | Greyscale, faded cover with **Earlier**; name, excerpt and chevron at full contrast. |
| Card | Saved files missing or resized | No cover or chevron, a broken-document icon and **Preview unavailable** under the name; not a control. Its question's answers are disabled until the owner shows the files again; the conversation stays open. Previews saved before shown files existed show the same card with their title. A copy whose content changed at its saved size opens to the reader's Unavailable state. |
| Reader | Loading | A page skeleton (heading bar, text lines, image block), static under reduced motion; "Loading README.md". |
| Reader | Ready | Header and document. |
| Reader | Formatting code unavailable | Header and Close remain. **Formatting unavailable**, "Reloading clears text you have typed but not sent.", and a bordered **Reload page** button precede exact wrapped monospace source. Saved embedded images follow as full-width gallery controls; extra images keep their position. The conversation stays mounted and its draft survives Close. Nothing reloads automatically. |
| Reader | Image loading / failed | A grey block at the saved size; on failure an outlined box with a broken-image icon, **Image unavailable** and **Retry**. |
| Reader | Earlier | **Newer** in the header. |
| Reader | Unavailable | A broken-document icon, **Preview unavailable**, "It could not be loaded. Try again, or ask in the conversation." and **Retry**; × still closes. |
| Reader | Denied | A lock icon, **Preview unavailable**, "This device has no access right now." and **Retry**. |
| Gallery | Open | The image fitted, count, Zoom image, Close image, previous and next. |
| Gallery | Image loading | The spinner where the image goes; count, arrows and Close stay usable. |
| Gallery | Image failed | A broken-image icon, **Image unavailable** and **Retry**; arrows and Close stay usable, Zoom is hidden. |
| Gallery | Zoomed | **Fit image**; a swipe pans; the arrows change image and reset zoom. |

Every phone control is at least 44 px. The reader has no approval control or banner; the question
card keeps its own. Phone and desktop states are walked in `web/e2e/shown-files.pw.ts`, including an
adversarial document in the reader and the file reader that fails on any executed script or request
leaving Altitude's origin.

#### 3.9.2 Validation captures

An L2 reply with attached validation captures shows an underlined **Watch capture · title** link (or
**Watch N captures**) under its text, at least 44 px tall on phone. It opens
`/projects/:name/tasks/:slug/captures/:messageId` in another browser tab, leaving the conversation and
draft intact. The page shows **← Back to conversation**, **Captures from validation run N**, when the
reply attached them, and each capture as its title, size, length and frame count above the GIF looping
at its recorded size, narrowed to the page. A reply without captures has no link.

Phone and desktop states are walked in `web/e2e/captures.pw.ts`: **Loading captures…** gives way to
content; an unknown, denied or failed read shows **Capture unavailable** with **Retry** and Back. A
capture starts at **Loading capture…**; a missing or altered one hides its meta and shows **Capture
unavailable.** with **Retry capture**, and recovery removes the error.

### 3.10 Task page

The [maintained task states](TaskStates.html) describe activity and steering. Task actions stay
directly accessible in the compact header, with one consistent button treatment.

Task details includes **Tokens processed** on phone and desktop,
also present in the report view. Above it, Task details shows **Current context** (Context at last
request once the task stops): tokens, share of the window and observation age, or "unavailable · no
reliable reading for the current session". The folded token row shows the cumulative processed total
(unknown when unavailable), model requests, coverage, and collector freshness. Expanded details first
explain that each request re-sends the conversation, so processed input is mostly cached and is not
a bill, quota use or the current context; then **Input processed**, its cache subsets, **Output
generated**, reasoning and **Model requests**. Engine and owner/delegated session rows, or **Provider
total · helpers unsplit**, follow. Cache/reasoning fields are parts of input/output, never additional
totals. Attempts survive resume and engine handoff; project L3 work is excluded.
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
the header button). Task details contains the muted line "attempt 1 · started 32 min ago"
when those values are available; a finished task reads "done 2h ago" or "rejected 2h ago". The model
chip says only what is known: "Requested · Opus 5 on Claude · Max" until the engine reports, then
what it reported ("Opus 5 on Claude · High"), with "(requested Max)" when the levels differ. Task
details holds **Model and effort**: Requested (what L3 set for this task, or the project choice and
defaults), Launched, Engine reports ("not reported" when absent) and routing's recorded reason, with
"Messages and resumes keep this model and effort; to redo the work on another one, ask L3." The PR chip reads "PR #N merged · main checks passed" or its open
and check states, in danger tone when main checks failed. It links to the PR when the repository
URL is known, otherwise it is a plain chip. **Merge held** is concise and independent of execution
or question state. Its complete reason opens in task details and wraps without truncation.
`web/e2e/header-density.pw.ts` measures long-title reading space at 1440×900, 1366×768 and
1024×768, plus the 390×844 phone layout. Details, navigation, scrolling and draft
restoration remain covered alongside the task state and composer walkthroughs.

**Stop** is directly accessible in the task header on phone and desktop, serving both views.
One click requests termination immediately. **Reject** is in phone task details and the desktop header,
with inline confirmation:
"Reject this task? Its worker ends and the task is archived." with "Reason (optional)", then
**Cancel**, focused, and a red **Reject task**; Escape cancels. Stop appears while running; Reject appears while queued, running, blocked or reported.
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

Work the worker keeps running beside its stream (a command or helper it put in the background, a
running command that **Send now** moved to the background, or a command its turn moved past) shows in **In the background**, a tray pinned under the Live session
stream while the task runs. It does not scroll with the stream and appears only while something runs
or ended less than 60 seconds ago; a session without background work shows no tray. A 12px muted
**In the background** heading leads one row per item: a status dot (accent while running, green
done, red failed, muted stopped; never pulsing, since the pulse means recent output), the tool name
in the stream's row style (`$` for a command, **Agent** for a helper), the engine's own description
(or the command) in 13px, wrapping to two lines and then ending in an ellipsis, with the full text as
its hover title, and the status right-aligned in 12px tabular muted text: "running · 4 min", then
"done in 4 min", "failed after 4 min" (red) or "stopped after 4 min"; without a recorded start it
reads "running", "done", "failed" or "stopped". Running items come first, oldest first, then
outcomes, newest first. More than three rows fold under **Show all 6** (a 44px link), which becomes
**Show fewer**; the expanded list scrolls inside the tray at no more than 30% of the viewport height,
so the stream keeps its room. An outcome leaves the tray 60 seconds after it ended, and one without
a recorded end time never enters it; the item's own call row in the stream keeps the outcome: that
row's hint reads "background" while it runs, then "background · done", "background · failed" (the
row in danger tone) or "background · stopped", in place of its output count. The tray is otherwise
not interactive. The engine seam reports each item from the current worker's records only; work the
engine stops listing without reporting an end leaves the tray with no outcome, and an unreadable
source or an engine that reports none shows no tray and no row hint. Conversation, its activity
preview and task rows are unchanged. `web/e2e/live-background.pw.ts` walks no background work,
running beside the stream, a helper done, a failed outcome, several folded, Show all with a long
label in a bounded tray, Show fewer and outcomes leaving after a minute, for each configured engine
at phone and desktop width.

Below 1280px the live session panel follows the §2.2 rule for the work panel: an overlay from the
header's panel button, scrim behind, Esc or the scrim closes it; the `live` route opens it on desktop
too. Its header carries Stop/Continue/Check status while the scrim blocks the task header.
Phone anatomy: one compact header with Back, a bordered title dropdown with down-chevron,
Stop/Continue/Check status, concise L2 state and independent **Merge held** status; a full-width
two-tab row **Conversation | Live session** (the `live` route selects the second); content;
scrolling fresh activity and the shared compact composer on Conversation. The header action remains
visible in either view and while typing. Nothing sits between the header and the tab row (desktop:
the chip row), so the conversation starts right under them. The title dropdown opens a scrollable sheet
with full title, attempt, state chips, then the complete block, start-condition and hold reasons, then
context/tokens, model, and existing View question, Reject confirmation and operational Resume. A long
phone title opens in full in details. Desktop retains its direct operational actions and
live-panel control while disclosing long reasons. Closing details restores the opener, draft,
selection and reading position. A failure remains visible, not only inside details: when the state dot
is red (**Work interrupted**, rejected) the phone state label is red and the desktop state chip uses the
danger tone, on every view and at any scroll position.

What the task waits on ends the conversation: one line after the last message and every card that
ends the chat (review cards, the question card, **Your turn · review before merge**), immediately
before the collapsed **Activity & evidence**, so at the latest messages it is the last content line.
It reads the state dot, 8px gap, then the reason in the 13px meta size and secondary colour (danger red
on a fault), wrapping in full; the text is the shared excerpt of at most 120 characters. Because the
header names the state, the line gives only the reason: an L3 wait or pause reads its recorded reason
("Wait for the required checks on the approved pull request, then merge it."), a fault "The task
session ended before completion. Waiting for the coordinator to check the blocker." (or "A system
problem paused work. …", with "Coordinator needed: …" when L3 holds a question), a planned task
"Waiting for “Publish the installation package” to finish.", a queued or resume-held task its start
condition. There is no line while running or finished, for a pause without a displayable reason (the
recorded reason, if any, is in details), for Stop states (the steering notice and Continue say them),
for operator questions (the turn card says them), or while L3's open question card shows without a fault,
since that card carries the same reason; with a fault the red line follows the card. The line is a
status region; it updates in place as polling changes the task, never moves an older-message reader,
and scrolls away with the chat while the header keeps the state. `web/e2e/task-room.pw.ts` walks these
states at phone and desktop width: latest messages, reading older, details with reasons first, and
details closed at the same reading position.

Compact task states use the §3.5 labels (**L2 working**, **Waiting for coordinator**, **Your turn · …**,
**L2 replying to you**, **Work interrupted**, **Paused**, **Stopped by you**, **Stopped by coordinator**); **Merge held** can
accompany any of these. Details separates each full reason. Waiting on L3 adds no operator badge.
An operator question sits at the end of the chat with no generic Resume while the question is open.
When it is offscreen, **1 question ↓** (or its count) floats above the composer. **Latest ↓** appears
when newer messages are below the reader. If both lead to the bottom, show only the question jump.
Each hides when its destination is visible. These labeled, keyboard-accessible 44px buttons occupy
the conversation's lower corner without reducing its viewport. Shown files stay in the question
and Needs you; View question stays in task details, useful from Live session. A held review-ready PR whose owner has stopped
(#419) shows **Your turn · review before merge** at the end of the chat and in Needs you:
**Approve merge** sends the operator's own message "Approved: merge PR #N." and the L2 merges
with it after fresh checks of the current head (the press shows as its pill, §3.8.2, and the chat then shows "Sent · the L2 has your reply."); **View PR #N** opens it; asking below discusses it. A later,
unrelated question never hides that review. An open operator question about the PR supplies its single
response surface instead: one its owner marks as the merge question, or an unmarked one that links or
names the PR, with quick options or a freeform field. Beside **Send answers** it offers **View PR #N**,
as the card does. While the PR waits, Altitude refuses an owner's unmarked question with choices, so a
question never offers **Approve merge** beside the card. The separate card stays
away until the owner resolves the question, including while a submitted response waits for interpretation.
Resolution restores the fallback when merge approval is still needed; a PR mention alone grants no
approval. Independent questions and ordinary chat remain available. After **Approve merge**, a later park
on another dependency shows that wait and no card (the CLI and queue add "PR #N approved"), including after routine integration
gives the PR a new head; a new hold or a later operator message naming the PR brings the card back. A fault keeps its red header state and red end-of-chat explanation of the interruption and its known wait. Operational
pauses without questions retain Resume/Reject. No disclosure or reply releases a merge hold.

Navigation states: Conversation, Live session and, for a task with a worktree, Terminal are local
views of the same task. On phone they sit side by side on one track in their tab order. A deliberate
horizontal swipe is interactive from its first horizontal movement: the outgoing view slides with the
finger and the neighbouring view is on screen beside it, proportionally to the drag, while the tab row
stays in place. Release past half the width, or a fling of at least 0.4 px/ms in the drag's direction,
completes the switch with a short settle; otherwise, including a fling back toward the start, the
track springs back. Left moves one tab on (Conversation → Live session → Terminal); right moves one
tab back; past either end the track gives a little with rubber-band resistance and never switches. A
swipe that lands while the track still settles moves nothing until it lifts, and the browser's own
horizontal swipe never takes it Back. Under
reduced motion nothing moves with the finger and a release past the same thresholds switches at once.
Accessible labeled tabs remain the direct alternative. Swipes do not start from the composer or form
controls, and leave browser-edge Back, text selection, recording, dialogs, horizontally scrollable
session content and the terminal screen and key row alone; on Terminal a swipe starts from the notes
and cards around the screen. Vertical scrolling stays native. Desktop keeps simultaneous panes.
Phone tab and swipe switches replace its current history entry, keep the originating shell tab, and update the URL;
the desktop panel button adds no history. A `/live` deep link and reload select Live session on
phone and open the desktop panel. Back in the phone header and the desktop crumb both return to
the actual preceding in-app page, including its query string. With no in-app predecessor they
replace the task entry with the owning project's L3 conversation. Browser/system Back remains
native; Forward restores the task's latest URL, and other pages/tasks keep ordinary history.
On phone Conversation and Live session stay mounted and laid out; the inactive view is invisible and
untouchable, not removed, so draft text, selection, images, conversation reading position and live transcript reading
state survive switches natively and a drag reveals the view as it was. An inactive Live session does
not poll its transcript; the drag that reveals it starts the transcript, so the incoming view shows
its real content when it has rendered before and its Connecting skeleton during the drag and settle
otherwise, then its content. The Conversation's initial, empty and error states read the same at any
offset, and a drag never wakes its composer. A drag toward Terminal shows the terminal's loading view;
the shell opens, or attaches to the running one, only once the switch completes, so a drag that
springs back starts nothing and opens no keyboard. Memory matches the desktop's simultaneous panes; only
the inactive phone view's transcript polling and reading bookkeeping pause. Returning does not open
the keyboard automatically; touch fields retain their 16px sizing and ordinary use never changes
page scale (§2.2).
Switching to Live session cancels unsent dictation and releases the microphone. Explicit voice Send
continues transcription and submission for its original task while hidden, without refocusing its
composer, also when it finishes after the operator returns; Escape in Live session does not cancel that
submitted message.
A refused or uncertain send arriving while Live session is open shows on its message, which is there
on return, and leaves newer draft edits alone; an accepted send remains sent. On desktop, closing the panel leaves
the conversation visible. Navigation itself has no
loading, listening, denied or error state; destination reads and composers retain their states
specified here and in §3.6. `web/e2e/task-navigation.pw.ts` walks entry from L3 and Work, repeated
toggles, reload, Back, Forward and direct-live fallback on phone and desktop. `web/e2e/task-swipe.pw.ts`
walks the phone gesture: idle, drag started, half-way with the incoming view loading and rendered,
release completing, springing back, resistance past either end and reduced motion, plus the
gesture exclusions and retained reading state; `web/e2e/terminal.pw.ts` walks the three-tab swipe to
Terminal and back, its loading view half-way, end resistance and a swipe on terminal text that stays.

A message L3 sent the L2 is coordination, not conversation with the operator: it reads as one
left-aligned muted line with a dot, **L3 ·** and the one-line summary L3 wrote when sending (for
example "L3 · Resolve conflicts, keep the review hold"), "· N images" when it carries images, and
**Show** (44px target on phone). A message saved without a summary reads **L3 messaged the L2**. A
summary longer than the phone width wraps; it is never cut. Show opens the complete original message
in place, with its links and images, and becomes **Hide**; the time sits in the gutter like any row.
Opening one stops bottom-following so the reader keeps their place. The stored message is never
shortened: it is the L2's input and authority evidence. Questions L3 brings to the operator keep
their question cards. `web/e2e/task-page.pw.ts` walks summarised and unsummarised rows folded, open
and folded again on phone and desktop.
The composer says "Message
the L2"; the hint reads "Reaches the L2 at its next checkpoint." while running, "Delivered when
Altitude resumes the L2." while held for resume, and "Sending resumes the L2 with your message."
for another blocked task. Confirmed Stop reads **Send a correction to continue this session.** A
refused or unconfirmed send shows on its message as in §3.6, with Retry only to the place it went.
Accepted messages stay sent through wake or refresh errors. The composer appears for running and
blocked tasks.
Queued operator messages form a group of outlined bubbles, 6px apart (a decision selection shows its delivery on its pill instead, §3.8.2), each with a quiet ×
(**Remove**) in the gutter. One bordered **Send now** follows the group. Its accessible description is
**Joins the current turn without stopping its work.** It hands the queued removable operator group
to the running turn in arrival order; each message retains its receipt and later arrivals stay outside
the claim. Quick-choice receipts and messages used by recorded decisions keep their evidence.
A pressed × spins until success removes its bubble and announces removal. Original text stays in
the record. A claimed message is faded (on its way) with no ×; a delivered message is a plain filled
bubble. Uncertain handoff shows an amber **!** and **Unconfirmed**. Screen readers hear queued and
sending states, and confirmed receipts retain screen-reader-only “Delivered”. Each remove control has its message as an accessible description. A prelaunch failure restores queued controls. A failed
removal shows `Not removed: <reason>.` and **Retry** under the bubble only once a read after it still lists the message;
a raced pickup shows the message's own state instead.
Saved or loading reads disable actions. Pending Send now holds a spinner in place and ignores
repeated delivery requests. Unavailable Send now looks unavailable and reveals the server's reason
on press. The server receipt establishes delivery; the UI never moves messages optimistically.
The empty queue has no queued controls; listening and transcription
keep the existing composer behavior. Removal does not undo a lifecycle request or recorded decision.
`web/e2e/queued-messages.pw.ts` walks queue, removal, handoff, recovery and failure states on phone and
desktop; `send-now.pw.ts` walks immediate delivery, ordering and its row states;
`l2-progress.pw.ts` covers listening, denied microphone and Stop states.
Delivery does not claim understanding or action. Finished conversations remain
readable with the activity area, composer and Stop gone.

The live transcript has tinted prompt blocks, the worker's prose, compact tool rows with folded
output, and the lifecycle boundaries the record supplies (state transitions, stops, holds). Every
row shows its recorded time in the §3.4 format, or **time unavailable**. The **Activity & evidence**
list prefixes each task event with its time the same way. A shell command's tool label is "$"; other rows use the recorded tool name, including
Edit for a file change. Short tool names share a fixed label column; the "$" takes only its own
width, so the command starts right after it. The hint reads "N lines", "running · 4 min" (time since the call while the
worker runs; "running…" when the call is untimed), "error", or "no output"; write rows
carry no diff counts. **Raw events** toggles the transcript to the raw list; its hover title states
the server's redaction rule. There is no transcript search field. Accessible footer states are "Following live
· new steps appear at the bottom", "Paused · Follow to catch up", "Session paused until the task
resumes", or "Session ended"; scrolling up pauses following, and Follow returns to the newest output.
The panel starts with recent activity. Scrolling upward near the top loads older rows into the same
transcript, preserving the first visible row and its pixel offset. There is no paging toolbar.
An upward wheel, touch or keyboard gesture also loads history when the recent content is shorter
than the panel. A completed page needs fresh upward intent; opening the panel does not cascade
through history. A spinner named **Loading earlier activity…** appears above existing rows. Failure
keeps the transcript and shows **Could not load earlier activity.** with a 44px Retry control there.
The exhausted top says **Beginning of session**. Neither history status nor updates move focus.

Pause also stops following while the worker runs; new activity continues arriving below. Live read
failures retain the text and replace the footer with **Could not update the session.** and Retry.
**Reconnecting to the session…** and **Catching up…** likewise take precedence over Following/Paused.
A following reader returns to the recent tail after a long absence or a lost server index; a paused
reader keeps their position while the loaded history reconciles. Earlier rows remain accessible by
scrolling upward. Background browser tabs suspend reads and refresh when shown again.
Raw events opens its own recent tail with the same scrolling. Each source record has a bounded
preview and **Full record** disclosure: **Loading record…**, Cancel, a local error and Retry, then
**Show more** for another chunk or **Hide full record**. Closing or leaving cancels its request.
Changing task, session, attempt or representation discards the previous viewer's rows and reading
state. Paused position and expanded tool output survive
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
operator (the question at the end of the chat); blocked on L3 ("Waiting for coordinator"
and the short prerequisite, with the full reason in details); blocked by a fault (a red explanation
from the same projection as task rows, never a raw-log excerpt); held for resume (Queued chip, the queue's recorded wait in place of
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
short viewport; voice listening/cancel/dictation/denial/unavailable; a committed voice Send landing in Live session or
after returning, with focus left alone; input and overlay Escape ownership.
Each named state has a screenshot under `web/ui-artifacts/results/l2-progress*`.

#### Terminal

The operator's own shell, for occasional commands; the conversations stay the main flow and agents
never see it. A task with a worktree offers it as the phone's third tab, **Terminal**, after
Conversation and Live session; swiping reaches it from Live session (§3.10 Navigation states). On desktop the task panel's header
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
target, and last a wider accent-filled **Enter** (return-arrow icon) that sends the Return key, as
the keyboard's would, for a typed command, a `read` or a password prompt. Enter leaves focus where it
is, so the soft keyboard does not open for it. Desktop has no key row: its keyboard's Enter does the
same. Showing the view opens the shell, or attaches to the running one; there is no Open step. The
terminal keeps running when the page leaves; returning replays up to 256 KB. When the shell ends,
however it ends, the view returns to where the operator was — Live session on a task, the project for
the project terminal — and keeps no output; the next visit opens a fresh shell.

The screen scrolls through its output and scrollback like any other content: a finger dragged over it
on phone (a quick swipe flings on and slows to a stop), the wheel or trackpad on desktop. Scrolling
back sends nothing to the shell and leaves focus where it is, so the soft keyboard does not open for
it. Scrolled back, new output leaves the earlier lines in place; back at the bottom the screen follows
new output again, and typing or any key row key returns it to the prompt. A program's full-screen view
(an editor, a pager) has no scrollback: a drag leaves it as it is, and the program's own keys move it.

On desktop, Ctrl+V (Cmd+V) pastes and Ctrl+C with text selected copies (Cmd+C on a Mac); without a
selection Ctrl+C interrupts. Escape and Tab belong to the shell, also when the panel is an overlay.

| State | What appears and what actions do |
| --- | --- |
| Off | "Terminal is off", what it does, **Open Settings** (returns here with Back, which opens the shell). |
| Starting | Skeleton lines and "Starting the terminal…". |
| Running | The screen with the cursor focused; **Close** / ×; the phone key row. |
| Running, reader note | Also a grey note above the screen: "This task's owner can read this terminal's output, and what it reads reaches its AI provider." A project terminal names the coordinator instead: "The coordinator can read this terminal's output, …". |
| Restart pending | A grey note above the screen: "Altitude restarts at its next quiet point to apply an update. This terminal will close then." |
| Reconnecting | A small "Reconnecting…" badge over the screen's top right, so the shell keeps its size; it disappears when output resumes and missed output appears. |
| Typing stopped | Input failed (a program not reading it, Altitude unreachable), so part of it may not have arrived: an amber alert "Typing stopped: <reason> Part of what you typed may not have arrived; check the screen." with **Resume typing**. Keys typed meanwhile are dropped, not queued. |
| Close with a running command | "Close the terminal?" card naming the command that will be stopped, **Close** (primary) and **Cancel**. Close without a running command acts at once. |
| Closed by the operator, or clean exit | The view returns; no notice. |
| Exit with a failure code | The view returns; toast "Terminal closed · exit code N". |
| Closed elsewhere (another tab or device, or the setting turned off) | The view returns; toast "The terminal was closed elsewhere." |
| Task finished / project unmanaged | The view returns; toast "The task finished, so its terminal closed." / "The project is no longer managed, so its terminal closed." The task's Terminal tab disappears. |
| Ended while disconnected (an Altitude restart) | The view returns; toast "The terminal closed while the connection was lost." |
| Could not start (no reachable service manager) | The view returns; failure toast "The terminal could not start: <reason>". |
| Chat command | Opened from a `run` block in its conversation: the command typed at the prompt, not run; or a notice with Copy when a program holds the foreground (§3.3 Chat commands). |
| Could not read, start or refused | "Couldn't read the terminal" or "Couldn't open a terminal", the server's reason (a missing folder, the setting off, an agent request refused) and **Retry**, shown at once. |
| Terminal code missing | The page predates an update, whose activation removed the terminal code this page would load: "Altitude was updated", "This page is from the earlier version. Reload to open the terminal." If Altitude cannot be reached: "Couldn't load the terminal", "Check the connection to Altitude, then reload." Both add "The shell keeps running. Reloading clears text you have typed but not sent." and **Reload**; nothing reloads by itself. Walkthrough: `web/e2e/app-update.pw.ts`. |

Walkthrough: `web/e2e/terminal.pw.ts` at 390×844 and 1440×900 (the project terminal at 1100 wide, as
an overlay) walks every state above against real shells, plus tab completion, copy and paste, and
scrolling back by touch or wheel while output arrives, with the agent check and the restart notice as
fixtures.

### 3.11 Project switcher (phone)

A sheet from the header name: managed projects with dot and count, unmanaged folders, **Add a
folder**. Tapping a project selects it and closes the sheet; the Chat and Work tabs follow. Hidden
chevron and no sheet when exactly one project is managed and no folder is unmanaged.

### 3.12 First run and project setup

Shown on any project route when no project is managed: a centred card with five steps, **Your
name**, **Prerequisites**, **Incident reports**, **Voice** and **Projects**. Desktop shows a numbered stepper
(done steps ticked); phone shows "Step N of 5 · Title" and keeps the step's buttons in a sticky bar
above the tab bar. The step is `?step=` so reload and browser Back keep the place. The name,
incident and voice steps have **Skip**, prerequisites always allow **Continue anyway**, and every step but the
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
- **Voice to text**: where this computer can run the speech model, "Voice to text runs on this
  computer. It needs a one-time download of about 698 MB." with **Set up voice** (saves This computer
  and starts the download, which continues in the background), **Use browser recognition instead** and
  **Skip**; once set up or setting up, the step says so and offers **Continue**. Where it cannot run,
  "Voice to text can’t run on this computer: <reason>." with **Use browser recognition** (or
  **Continue** when that is already the choice). A failed save shows the server sentence and keeps the
  step; a setup that later fails is retried from Settings and never blocks first run.
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
subfolders, sorted by name (on a Mac, without the folders macOS keeps private, such as Desktop and Documents), with **Project** and **git** tags; a row opens that folder. The footer has
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

**Setup…** in the project menu reads **Checking**, **Ready** or **Needs attention**; unavailable reads say
**Unavailable**, and non-Git projects say **Conversation ready**. The header shows the same status as a
**Setup · <status>** control only while setup needs attention: setup is running, a current requirement
is missing or failed (including one an update introduces), or the read failed. A first read that is
still loading and a ready project keep the header quiet. Opened from the header, the control stays
until focus leaves it, so closing the checklist returns focus there; after resolution it then
disappears. There is no dismissal or seen-state: the current checks alone decide. Both open
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
reattachment. See [project setup](../../docs/ARCHITECTURE.md#project-setup) for responsibilities.

A custom-hook conflict offers **Review integration**, showing the original directory/events and
explaining that trusted custom hooks and their dependencies run with Altitude's Git permissions,
outside the task agent's sandbox. It explains **Use both hook sets** and
**Keep current setup**. Integration requires an explicit operator choice for the inspected hooks;
unsupported hook managers retain their configuration and offer discussion. Successful repair
removes its warning/action. Retry is unavailable while accepted work runs. Setup stays open at
completion, with **Open conversation** available.

Walkthrough states at phone and desktop: fresh registration, healthy/reused project, newly missing
requirement, stale guards, custom-hook review, active progress, interruption, failed/denied repair
and successful retry. Also cover the quiet healthy header, menu access, the header appearing for a new
requirement and leaving after repair, empty folder discovery, initial loading, failed read with Retry,
offline observations and non-Git outcomes. Status changes use a polite live region, errors an alert,
and controls 44px targets. The sheet contains focus and scrolling, closes with Escape/Back, and
restores focus to the Setup control or menu button it was opened from. Conversation/draft and reading position survive. Listening belongs to
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
web app, both, or Altitude, with file count and age (exact time on hover). An unavailable **Restart** reveals the quiet-point rule and waiting list on press, with an accessible description.

The quiet point has no dispatch or resume claim, L3 turn, adversarial review, validation run, or report verification in
flight; running workers do not hold activation. The waiting list names claimed tasks, `<project> L3`, and the task of
each adversarial review or validation run. **Restart** is actionable when the waiting list is empty and no restart
is under way. Pressing it or receiving a recorded restart request replaces the button with a spinner
named "Altitude is restarting…". A failed activation reads "Automatic activation did not
complete; L3 has the fault." Its waiting list stays visible as the retry's blocker. The notice leaves when the new process answers without a pending
restart. Data: `GET /api/overview` `restart`; the button requests `POST /api/restart`.

Update status loads independently of Monitor readings, with a spinner named **Loading update status…**, a read
error with Retry, or **No update pending.** Request errors remain beside the action. Details wrap
and use normal page scrolling on both viewports. Pending, waiting, restarting, activation failure,
request denial, loading, read error, empty, dismissed and new-event states are walked on phone
and desktop in `web/e2e/restart-banner.pw.ts`, including removals after actions.

**New version (installed copies).** When the overview's `update` names a newer followed release,
automatic updates show **Altitude <version> will install automatically at the next quiet point,
when no browser terminal is open.** with **What’s new** and ×. When automatic updates are off or
that version has already been attempted, the row reads **Altitude <version> is available. What’s
new · Update · ×**. What’s new opens the release page. Update turns the row into a confirm: "Install Altitude
<version>? Altitude checks the download, then restarts. If <version> does not start, <current>
comes back." with **Install <version>** and **Cancel**. Install shows Starting…, then the row reads
**Installing Altitude <version>… Altitude restarts when it is ready.** with no actions until the
new version answers. A successful automatic update shows **Updated to <version>.** with **What’s
new** and ×. A refused request keeps the confirm and shows the server's
reason. A failed update reads "The update to <version> did not finish. <reason> Altitude <current>
keeps running." with **Try again** and ×. Close (**Dismiss new version notice**) hides that version,
or that failure or installed-version notice, in this browser until a different notice appears.
Dismissal affects presentation only; Settings retains a failed update's retry. Source deployments
and containers never show it. Data: `GET /api/overview` `update`; Install requests `POST /api/update` with the
version. Source deployment, available, confirm, refused, installing, failed, dismissed failure,
dismissed version, newer version, automatic pending, saving, disabled, denied and updated states are walked on phone and desktop in
`web/e2e/update-notice.pw.ts`.

All banner notices and toasts offer an accessible close control. Toast timers, hover/focus pause
and action controls remain available. Inline form/transport errors, task faults, task questions
and conversation navigation are contextual state or actions, not dismissible banner notices;
their existing recovery, answer and navigation controls remain visible.

### 3.14 Monitor

Anatomy: **Monitor** title; on phone the **New tasks** control (§3.6.1) beside the same quota;
**Altitude update** (§3.13); **Seats**, one card per configured engine in the API's order and under
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
in `--danger`, with the router's reason below. Rows wrap within the card. Sessions are only the ones
Altitude runs: each project's coordinator (L3) and its live task owners (L2). Session rows show their
kind and project or task, engine and model when supplied, context meter and recorded status, and a snapshot
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

Voice input includes a collapsed **Voice troubleshooting** disclosure. It explains metadata-only,
on-device collection and the Start → reproduce → View report flow. Start diagnostics opts in for
up to ten minutes; Stop diagnostics freezes collection. View report stops collection and shows a
labelled read-only text area with Copy report; clipboard denial leaves the text selectable. Clear
report deletes the in-memory evidence. No audio or conversation text is collected or automatically
sent. Collection survives in-app navigation and ends on reload. Controls and report work at both
phone and desktop widths.

Settings at `/settings` opens from **All settings…** in the project's three-dot menu on both widths,
or the desktop rail's operator row; **Project settings…** opens that project's page directly. The row highlights on every Settings route; theme switching
remains independently accessible. Phone keeps a labelled Back button and the existing four tabs.
A direct overview visit returns to `/projects`; entry from another view returns there.

The overview groups rows by where a setting applies, in two columns on desktop and one on phone.
**Your name** comes first: the name, or "Not set · screens say “you”", opening `/settings/name`.
**This project**, present only when Settings opens from a project, has one row naming it with its L3
choice and routing ("L3: Fable · Low · tasks prefer Codex"), opening `/settings/projects/<name>`.
**Models** holds **New tasks**, the same value and Models dialog as the control beside the quota
(§3.6.1). **Projects** holds **All projects** ("2 projects · folder ~/Projects"), opening
`/settings/projects`: every managed project, each opening its page, then **Projects folder**.
**Voice** holds **Voice input**. **Notifications** holds this device's decision alerts (below).
**Devices and access** holds Devices, the Terminal switch and the
read-only Network row (address and HTTPS). **Coding agents** holds Prerequisites, the Validation
runs switch and Incident reports. **About** holds the Version rows on an installed copy. Every row
that opens a page has a chevron; switches save in place.

**Notifications** is set on each device. Its first row, **Decision alerts**, is a switch with the same
line as the Needs you switch (§3.8.1); where alerts cannot be on, the switch looks disabled and pressing
it reveals that line. While alerts are on, three rows follow, saving in place:

| Row | Line under the label | Control |
| --- | --- | --- |
| **Show** | the banner title in quotes, for the first managed project: “atlas needs a decision” and the task name / “atlas needs a decision” / “A decision needs you” | select: Project and task · Project only · Only that a decision waits |
| **How often** | only with a pace: "Decisions in between arrive together in the next alert." | select: Every new decision · At most every 15 minutes · At most once an hour |
| **Sound** | none | switch |

Turning alerts off removes the three rows; turning them on restores the device's saved choices. A change
shows at once, with the controls disabled and "Saving…" under the group until it is saved; a refusal shows
Altitude's reason in red under the group and the control returns to the saved choice. A device whose choices
cannot be read shows, in place of the rows, "This browser cannot keep alert choices here, so alerts name
nothing." in red. On phone a select wraps under its label at full width. The pace limits how often Altitude
sends to the device: the first decision after a quiet spell alerts at once.

One bordered **Voice input** row shows the saved backend and a chevron.
Its whole area opens `/settings/voice`; no backend options or credentials occupy the overview.
The **Projects folder** row shows the current folder and opens `/settings/projects-folder`: an
explanation that First run offers the folders directly inside it, the current value and the §3.12
folder browser with **Use "<folder>"** (Home allowed). Saving shows a spinner, then a check named Saved.; a failure shows the server explanation and Retry. First run reads the change at once.
**Prerequisites** opens `/settings/prerequisites`; **Incident reports** shows "Published to
<repository>" or "Kept on this computer" and opens `/settings/incident-reports`. Each page is the
First run step's content with **Save** in place of the step buttons (Prerequisites has **Check
again** only) and shows a check named Saved. after a save.
A **Devices** row shows how many devices are paired and opens `/settings/devices`: a list of paired
browsers, each with its name ("Safari on iPhone", "Home Screen app on iPhone", "Chrome on Mac"), a
**This device** badge beside the current one, and "Paired <date> · last used <date>". **Remove** asks
once in the row ("It will need a new code to open Altitude again.", or "This browser will need…" for
the current one) with **Cancel**, focused, and a red **Remove device**; Escape cancels. Removing the
current device shows §3.16. Below,
**Pair another device** makes a code: the code large in monospace, "Works once, for the next 10
minutes. On the other device, open <HTTPS address> and type it." (without HTTPS: "type it on the Pair this
device screen."), the address's QR code with "Scan with a phone to open Altitude.", "Certificate "<name>" —
SHA-256 ends with <last 8 pairs>" (monospace) and **Make a new code**. The code never appears in a link.
With HTTPS, a **Certificate** card follows: "Set up HTTPS trust on Linux, macOS, iPhone, iPad or Android.
Open a setup link and QR code here, or run `alt tls-share` on the computer running
Altitude.", the primary **Set up a device** button, "Before trusting the downloaded certificate, check that its
name and SHA-256 match these.", then Name, SHA-256 (monospace, four rows of eight pairs, as iOS groups
them), Trusting it allows (the scope read from the certificate, "No limits: …" for an unconstrained
CA) and Expires. An unreadable certificate shows "Could not read the certificate: <reason>" in red;
without HTTPS or a CA file the card is absent.

| Set up a device state | What appears and what actions do |
| --- | --- |
| Ready | **Set up a device**. |
| Opening | **Opening…**, disabled. |
| Open | In place of the button: the QR code (232 px, black on white with its quiet zone), "Open setup on this device, enter the link on another computer, or scan the QR with a phone. Keep this Settings page open while downloading.", a primary **Open setup page** link, the address in small monospace, "Closes in 9:41" counting down each second and **Close**. Opening setup creates a new tab without opener access; the original page keeps the QR, timer, name and SHA-256 for the check. |
| Closing | **Closing…**, disabled, while the service closes the link; **Open setup page** is absent. |
| Close failed | The QR code stays with **Close** enabled for a retry and, in red, "The link is still open: <reason>". |
| Closed | A confirmed **Close**, or the end of the ten minutes, closes the link: the QR code, timer and link disappear; **Set up a device** returns with "The link is closed.". A new window replaces an earlier one, and leaving the page, even while it is opening, closes the link. |
| Refused | The service's reason in red under **Set up a device**, such as a loopback-only or plain-HTTP service. |

The setup page is served by the share link itself, light or dark with the device:
**Set up this device for Altitude**, deliberate trust guidance, and a grey card with the CA name
and SHA-256 in four monospace rows. It requires comparison with the trusted original Settings page
or terminal, because the HTTP page alone proves no identity. Bordered navigation links jump to
**Linux**, **macOS**, **iPhone or iPad** and **Android**. The desktop download section offers the
public `ca.crt`, its read-only OpenSSL fingerprint command, and a single-certificate contents check.
Linux instructions cover current/older Chromium certificate managers and Firefox Authorities;
macOS uses the login keychain with explicit SSL trust, plus Firefox's separate import where needed.
**iPhone or iPad** has a full-width blue **Download the profile** and four numbered steps (Allow and
Close; Settings › Profile Downloaded, check the certificate name and More Details SHA-256, Install
with the passcode, or Remove; Certificate Trust Settings; open the HTTPS address in a new Private tab
with no warning, then pair), and **Android** has **Download the Android certificate** and two steps.
The final section verifies the exact HTTPS URL without a warning before pairing, distinguishing
host-local and remote addresses and private-window pairing. It names no trust step as automatic.
`web/e2e/certificate.pw.ts` walks the card, every sharing state, platform navigation, downloads,
identity checks and verification instructions at both widths. Listening is inapplicable to this flow.
A **Terminal** switch row (off after install) says "Every paired browser can run commands as you
on this computer. Terminals close when Altitude restarts or when you turn this off." It saves on
change, disables itself while saving and shows the server's reason under the copy on failure; turning
it off closes every open terminal.
A **Validation runs** switch row (on after install) follows it and says "Agents test installs,
containers and browsers in throwaway containers on this computer, and each run is recorded on its task.
Turning this off stops a running one." It saves like Terminal; turning it off stops the running run and
refuses new ones. Where Terminal or the validation runner is unavailable the switch looks off and disabled; its accessible description and a press reveal "Not available here: <reason>." `web/e2e/validation-switch.pw.ts` walks on, off, a refused change and
unavailable at both widths.
An installed copy adds a **Version** row: the installed version, with "· <version> is available · What’s new" with the copyable `alt update` command. A **Check for
new versions** switch (on after install) says "Twice a day Altitude asks GitHub for the latest
release. Nothing else is sent. Turning this off also stops automatic updates." An **Automatic
updates** switch (on after install) says "Install new versions at the next quiet point, when no
browser terminal is open. Turn this off to be asked before installing." Both save on change,
disable while saving, show **Saving…**, and retain the prior setting with an inline error if denied.
Turning checks off hides the newer release and notice and disables Automatic updates, retaining
its saved preference for when checks resume. A failed update retains **Try again** in About even
after dismissing its banner. Source deployments omit these rows; containers show their image-managed
version without update switches.
Voice input has a labelled **Settings** back button at both widths. It returns
to the overview even on a direct visit; browser Back retains normal history. The phone header stays
visible while the content scrolls. Opening a Settings page does not change a setting or probe a service.

The voice page offers This computer, Browser recognition and Your speech service with explicit
Save/Cancel. A radio changes only the draft; switching away from a saved service uses **Save and remove
service**, deleting its URL/key/model. Built-in setup/remove/cancel actions are immediate, independent
of the draft choice; Cancel discards the form choice without cancelling a download. Setup does not
automatically select the built-in backend.
Descriptions state where audio goes and any setup. This computer shows its setup below the choice:
"Needs a one-time download of about 698 MB, checked against this release." with **Set up voice**;
while setting up, a progress bar, "Setting up… X MB of Y MB" and **Cancel setup**; when ready,
"While you dictate, the speech process uses about 2 GB of memory." and **Remove voice** in a red
outline; a failed setup shows its reason with **Retry**. Supported installed voice stays available while
an automatic replacement shows "Preparing update…", "Updating voice · X MB of Y MB" with a progress
bar, "The update will finish after dictation.", or "Checking update…" with an inline spinner.
**Cancel update** sits beside **Remove voice**. Cancellation shows "Update cancelled." and an outlined
**Retry update**; failure shows "Update did not finish. <reason>" and a filled **Retry update**.
Failure stays muted when voice is ready, red when an update is required. Unsupported installed voice
says "Voice needs an update."; its composer links **View update** while updating, **Update voice**
otherwise. Ready composers retain normal dictation. Buttons disable during actions and show the
pending action without shifting width; denied actions keep authoritative state and show the error.
The model's credit line (NVIDIA Parakeet TDT 0.6B v2, CC-BY-4.0) stays visible. When
this computer cannot run it, the choice is disabled and **Set up voice** reveals why on press, with an accessible description. A successful save updates only the
next capture; a recording keeps the selection it started with and stops when it changes.

| State | What appears and what actions do |
| --- | --- |
| Loading | A spinner named Loading settings…; no selected default or editable controls. |
| Read failed | Could not load settings and Retry; typing elsewhere is unaffected. |
| Saved browser | Chosen radio and a check named Saved. |
| Saved host | Chosen radio, a check named Saved. and the setup panel in its current state; while setting up, progress refreshes every second. The overview reads This computer, with " · not set up" until ready. |
| Saving | A spinner named Saving… and disabled choices until the request answers. |
| Failed/denied save | Server explanation and Retry; the saved choice is preserved. A choice changed elsewhere offers Reload settings. |

**Your speech service** says “Words appear after you stop. Audio passes through this computer to
the service you choose. Its privacy policy and charges apply.” It shows **Transcription URL**, a full
endpoint input, and “Use a compatible audio transcription endpoint reachable from the computer
running Altitude.” **How to run one** links to setup guidance. **Key or model (optional)** expands an
API key password field and Model. Saved keys are not returned: the placeholder says “Key saved —
leave blank to keep”, with a 44px **Remove saved key** label. Changing the address clears the key
association and shows a muted key hint, “Enter a key for the new address, if needed.” HTTP endpoints
show “This connection is unencrypted. Audio and any key travel over HTTP.” before saving.

First save is **Save and use service**, later edits **Save changes**, alongside **Cancel**. Invalid
URLs show “Enter a complete http:// or https:// transcription URL.” The form says once, “If
transcription fails, record again. Recordings are not kept.” Phone fields fill the card and scroll
naturally; desktop keeps the 680px maximum. Controls have 44px targets. Hints associate with their
fields; errors announce without moving focus. No endpoint probing or autosave occurs.

The service composer keeps microphone/image and recording controls. A 44px underlined destination
link shows **Voice: speech.example.test**, wrapping the hostname/port without exposing a path or key.
Stop releases the microphone and shows **Transcribing…** with Cancel; Send shows **Transcribing to
send…**. Pending service transcription hides waveform/timer and disables editing/microphone/send.
Success removes transient UI. Errors keep typed text/images and offer one Voice settings link,
omitting the separate destination link. Failed audio is discarded; no recording retry appears.
Switching conversations cancels unsent recording; an explicit Send completes only for its captured
conversation. This service presentation does not change live host/browser recognition.
An interrupted upload says “Transcription did not finish. Check your connection and service
address.” with the Voice settings link. Malformed saved configuration says “Your speech service
settings are invalid. Re-enter them in Voice settings.” in Settings; the composer replaces the
last words with the same Settings link and keeps the microphone closed until repaired.

The project page `/settings/projects/<name>` has a labelled **Settings** back button, the line
"Applies to this project only. A model or effort L3 sets for one task wins over these." and its
sections, model choices in the order they are applied:

- **L3**: the closed L3 choice ("Fable · Low", "Auto", or "Fable unavailable · Auto meanwhile" with
  the reason), and what L3's last reply
  reported ("L3 has not replied yet" before one). **Back to Auto** while a choice is saved, and
  **Change…**, which opens the Models dialog on its L3 tab.
- **Auto defaults**: one row per role and engine ("Tasks · Codex", "L3 · Claude") with **Model** and
  **Effort**, used under Auto and as the fallback. The model is free text with alias suggestions and
  a "Default: <model>" placeholder, saved on Enter or leaving the field, restored by Escape and
  cleared to Default when empty; effort offers "Default (<level>)", Native and only the levels that
  engine accepts, saved on choice. Each field saves alone with its own status. While New tasks holds
  a choice the copy names it. At 390px each row stacks model above effort.
- **Routing** (`#routing`): **Tasks** is Auto, Prefer <engine> or Only <engine>; L3 has no routing
  row, its choice in the **L3** section being its one engine setting. The copy names the order Auto
  tries (the default weekly-headroom split or the custom routing), that Only keeps tasks on that engine
  even with a model choice and waits while it is unavailable, and when a preferred engine is missing
  from custom routing. A change saves on choice
  with the value shown as `expected`; one changed elsewhere says "Changed in another window." and
  the page shows the current routing.
- **Improvement suggestions** (`#improvement`, the Altitude project only;
  [design](improvement-review/PROPOSAL.md#turning-it-on-the-settings-section)): "Finds friction that
  keeps coming back in this project's tasks and suggests fixes." over one card. **Weekly** is a switch
  whose line reads "Every Monday morning, when at least 5 tasks have finished." when off, "Next:
  Monday, Oct 19." when on (", if 2 more tasks finish" while fewer than five have), "Skipped Oct 12: 3
  tasks had finished. Next: …" after a skipped Monday, and "Off: the terms changed. Turn on to see
  them." **Get suggestions now** is a secondary button beside "Reads the 6 tasks finished since Oct
  12."; with too few tasks or Claude's allowance inside the reserve it looks disabled and its reason
  ("Needs 5 finished tasks since Oct 12; 3 so far.") is its accessible description, shown under it on
  press. While a review runs, its line ("Reading 6 tasks") with an underlined **Stop** replaces the
  button. A muted row says what is sent: "Counts and short chat quotes · Claude Sonnet 5.5 on your
  plan · capped per run, plus one L3 check". Once a review has posted or found nothing, a muted last
  row sums them: "4 runs · 7 suggestions · 3 became tasks · 2 dismissed · Latest", Latest underlined
  and opening the latest card in the project's chat (absent when no review had a card). Before the operator agrees to the current terms, turning
  Weekly on or pressing Get suggestions now opens a sheet (bottom at 390px with full-width buttons,
  Turn on above Cancel; centered at 1440px): **Turn on improvement suggestions for <name>?**, what the
  model reads and how much of the plan it uses, then **Cancel** (focused) and the primary **Turn on**,
  which agrees and completes the press. Cancel, Escape and the scrim change nothing. A switch change
  shows Saving… under the card with the controls disabled; a refusal shows its reason in red and the
  switch returns to its saved value. When the terms change, chat says "Improvement suggestions are off
  until you agree to the new terms in settings.", linking here.
- **Project**: **Setup** with its status, opening §3.12, and **Remove project** ("Detach L3. Files
  and history stay; its settings here don't.") with an outlined red **Remove…**. This section stays
  usable when the model settings cannot be read.

**Remove…** opens a centered dialog at both widths: **Remove <name> from
Altitude?**, "L3 is detached and Altitude stops managing this folder.", then what stays on disk (the
repository, worktrees, history and queued messages), what is not kept (its settings here, such as
models and routing), how to undo (add the same folder as <name> again to reattach L3 with its
history), and that unfinished tasks and a running L3 reply must finish first. **Cancel** comes first
and has focus; Escape, × and the scrim cancel. The red **Remove <name>** sends `POST
/api/project/remove`.

| Remove state | What appears and what actions do |
| --- | --- |
| Removing | "Removing… Closing doesn't cancel removal."; both buttons disabled. |
| Removed | §3.2's success navigation. |
| Refused | The server's reason in red, such as unfinished tasks; the project stays and Cancel works. |
| Response lost | "Checking whether it was removed…" while the project list is read again: a project that is gone counts as removed; otherwise "Couldn't confirm removal; <name> is still in Altitude." with **Retry**. |
| List unreadable too | "Couldn't confirm removal, and the project list could not be read." with **Check again**, which reads the list again; removal is offered again only once the project is known to be present. |

| Project page state | What appears and what actions do |
| --- | --- |
| Loading | A spinner named Loading settings…; the Project section is usable. |
| Read failed | Could not load settings and Retry; the Project section is usable. |
| Saving | That field or select is disabled with a spinner named Saving…; other fields stay editable. |
| Saved | That field shows the persisted value and a check named Saved. |
| Failed/denied save | Server explanation and Retry save; the field keeps the saved value. |

Maintained boards: Settings/MobileSettings, VoiceSettings/MobileVoiceSettings and SettingsStates.
Application walkthroughs: `web/e2e/voice-settings.pw.ts` at 390×844 and 1440×900, including navigation,
typed draft preservation, all three choices, key replacement/removal and loading/saving/failure.
`web/e2e/project-settings.pw.ts` walks the menu entries, the This project row, the grouped overview,
All projects, independent default saves, reload persistence, restoring Default, routing (Prefer,
Only, a change made elsewhere), loading/read failure and saving/denied states at both sizes, with the
project draft preserved. `web/e2e/project-lifecycle.pw.ts` walks the Remove dialog's cancel, refused,
lost-response and removed outcomes. `web/e2e/improvement-settings.pw.ts` walks the Improvement
suggestions section: the sheet, Cancel, Escape, Turn on and saving, Weekly on and off, running with
Stop, a refusal, a skipped week, the unavailable reason and the change-of-terms line;
`web/e2e/improvement-suggestions.pw.ts` walks Past suggestions with Latest.
`web/e2e/models.pw.ts` walks §3.6.1.
Composer listening, denied, unavailable, cancellation and transcript states remain §3.6.

### 3.16 Pair this device

An unpaired browser sees one centred card instead of the app, at every route: the Altitude mark and
**Pair this device** with three items, each with a mark (✓ done, ○ to do, a spinner while checking, ! needs
attention). The code never rides in a URL. Pairing opens the route the browser asked for.

1. **HTTPS address**: "You opened Altitude's HTTPS address.", or on the computer running Altitude over plain
   HTTP "This is the computer running Altitude." Plain HTTP elsewhere fails with "This Altitude serves plain
   HTTP. Pair on the computer running it."; the other items are absent.
2. **Trust Altitude's certificate** with a pill: Not trusted yet, Checking…, Trusted or Couldn't check. The
   computer running Altitude is Trusted at once. Elsewhere the item shows instructions for the device:
   iPhone and iPad (including iPadOS reporting a Mac with touch) get **Download the profile**, the Profile
   Downloaded check (one Certificate named "<CA name>", SHA-256 ending with the 8 pairs `alt pair` shows,
   otherwise Remove and stop), the Certificate Trust Settings switch and "Come back here; this checks
   itself."; Android gets **Download the certificate** and the CA certificate install; other browsers get
   **Download the certificate**, the name and SHA-256 check, importing it as a trusted authority and
   restarting the browser, and a **Setup guide** link. The page never shows the SHA-256 itself: the
   reference is the computer. The check runs as the page opens, whenever it becomes visible again and on
   **Check again**; Trusted collapses the instructions. With an externally supplied certificate Altitude
   cannot check: "Altitude can't check this automatically. Open this address in a new Private tab; if it
   loads without a warning, tap Continue." and **Continue** marks it done.
3. **Pair**, once the certificate is trusted: "Enter the code from `alt pair`, or from Settings › Devices on
   a paired device.", a large monospace **Pairing code** field (one-time-code autofill), a
   full-width **Pair** button, disabled while the field is empty, and "Each code works once, for 10
   minutes. This device stays paired until you remove it in Settings."

| State | What appears and what actions do |
| --- | --- |
| Loading | The mark alone while Altitude answers whether this browser is paired. |
| Unreachable | "Could not reach Altitude." and Retry. |
| Local | Completed step marks, a check named Trusted, and the Pair item. |
| Plain HTTP | ! with the plain-HTTP sentence; no other items. |
| Not trusted yet | ○ with the device's instructions, its reason ("Not trusted yet. The usual missing step is the switch in Certificate Trust Settings." on iPhone/iPad, "…Install the certificate as a CA certificate." on Android, "…Import the certificate as a trusted authority, then restart the browser." elsewhere) and **Check again**; no Pair item. |
| Checking | A spinner named Checking… and a disabled **Check again**. |
| Couldn't check | ! and "Couldn't check." with **Check again**, never phrased as untrusted. |
| External certificate | ○ with the Private tab sentence and **Continue**. |
| Trusted | A check named Trusted, instructions collapsed, the Pair item with an empty field; Pair disabled. |
| Typing | The field owns the dash: it keeps letters and digits, uppercased, up to eight, and shows `ABCD-` once four are typed. A typed dash or space is ignored; deleting the dash deletes the fourth character. A pasted `abcd 2345` or `ABCD-2345` shows `ABCD-2345`. An edit inside the code keeps the caret at the edit. |
| Pairing | Pairing… and a disabled field and button. |
| Wrong code | "That code is not right. N tries left." under the field; typing clears it. |
| Cancelled or used code | "Too many wrong codes, so this one is cancelled. Make a new one." or "This code has expired or was already used. Make a new one." |
| Removed | Any 401 returns here with "This device is no longer paired. Pair it again to continue." above the items. |

Application walkthroughs: `web/e2e/pairing.pw.ts` at 390×844 and 1440×900 walks every state above: the
local states and pairing with a real code, Settings › Devices with a new code, Remove with Cancel, removing
the current device, and with answered access and trust replies a device on the network not trusted,
checking, trusted and paired, Couldn't check, plain HTTP with and without an address and an external
certificate; at phone width an iPhone's profile instructions, and its Safari and Home Screen app pairing as
separate devices. `web/e2e/trust.pw.ts` walks Chromium over real HTTPS past the certificate warning, and
ignoring certificate errors, to Not trusted yet with no Pair item.

## 4. Behaviour rules

### 4.1 System turns fold

Every chat row whose `trigger` is not `chat` renders as a system line, never as bubbles. The line
text is the last paragraph of the turn's assistant row; while no assistant row exists the line reads
"L3 is handling <what>", where <what> comes from the trigger ("a landed report for <task>", "a
block on <task>", "a fault on <task>", "the restart"). An active turn stays outside the group so
its current handling line remains visible. A selected L3 FYI (`trigger == "fyi"`, `role == "system"`,
`heads_up == true`) also stays visible as its full concise text, splitting the routine runs around it.
So does a notable turn, one whose assistant or error row names tasks it created (`tasks`) or issues it
filed (`issues`, §3.4); altd records both from the verbs L3 ran during the turn, so L3 need not remember
to announce it. A turn that only resumed, messaged or answered stays routine.
Consecutive completed routine system turns with no chat, active turn, selected heads-up or notable
turn between them collapse to one line: "L3 handled N system events between your messages", expanding to the list.
Other FYIs are ordinary system lines (`trigger == "fyi"`, `role == "system"`, no reply), eligible
for grouping, including automatic fault details and historical rows without explicit selection. A
clean report closes without an L3 turn and produces no line; the task simply moves to Done this
week. This is a rendering rule over data the chat log already stores; slice 2 adds the two trims in
§5.2 so the expanded view reads well.

### 4.2 One conversation, in order

Messages sent while L3 is mid-turn queue and run at the next turn boundary in order; the composer
keeps its accent circle with the arrow. Phone names the active work in the header; desktop retains
its mid-turn hint. Queued operator messages appear as outlined bubbles, 6px apart, with a quiet ×
(**Remove**) each and one bordered **Send now** following the group. System rows keep their own
position and have no operator actions. Send now promotes the operator group in arrival order;
each message keeps its bubble and receipt, and later arrivals stay outside the claim.
Send now shows a spinner in its label's place while its request is out, and a clock once the server
holds the press; messages it hands over are faded (on their way) with no × until the turn takes them.
Boundary delivery retains the outlined queue; pressing the busy control reveals its waiting reason.
Once the turn takes the messages in, the reply so far ends, each message appears as its own operator
bubble, and the rest of the reply streams beneath the group. Historical interrupted replies retain
partial text as an ordinary reply with no notice; an empty interrupted reply leaves no row, so the
two operator bubbles sit back to back (8px apart, the first bubble's time beside it).
Uncertain engine acknowledgement shows an amber **!** and **Unconfirmed** after settlement and reload;
it never causes automatic replay. Groups with images or without an active chat turn run at a turn
boundary; Remove stays available until claim. A pressed × spins in place while its request is out;
the outcome comes from the server: removed, the bubble leaves and the rows below close up; taken by the
turn first ("already started"), the next read shows it faded or sent with no failure line; any other
failure shows `Not removed: <reason>.` and **Retry** under the bubble only once a read after it still
lists the message, so a removal whose answer was lost never reads as failed. A failed Send now leaves
the control ready with the reason under the group (`You do not have permission to send this message now.`,
the server's reason, or `Send now unconfirmed. Check this message’s status before trying again.`) until a
read shows the press held. These lines and the spinners do not survive leaving or a reload; pressing
again is safe. `web/e2e/message-states.pw.ts` walks each of these message states, Edit, leaving and
returning, Retry while reading above the bottom, and the entrance at both widths.
A message retained because no engine can run stays outlined in its historical position, without ×,
and joins newer queued messages in order after recovery. Its reply appears beneath that bubble,
or beneath the last newer message. Older retained messages appear in the
queue list when their history is outside the loaded window. The queue action remains available for
retained messages. Create task presses keep their own control rather than a separate Send now.
While a claimed group waits, its action stays with that group, before any later arrivals.
Send now never stops running work; task-chat Stop remains the hard stop. Unavailable actions reveal
the server's reason on press. Both controls have 44px phone targets; screen readers retain their
names, busy state and delivery statuses.
A running turn shows
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
labelled text. Running tasks, FYIs, queued messages and improvement cards listed under Suggestions do not
add attention.

Needs you says **N questions across N projects**; reviews and operational items, when present,
are named separately. Project summaries use the same distinction. Loading and failed reads never imply
zero; cached counts are labelled stale until refreshed. A follow-up, partial answer or final answer
removes that task's card until its owner re-parks. Those changes do not claim that its worker resumed.

### 4.5 Copy

Sentence case everywhere; the only uppercase is a count badge. L3's chat text carries no ids, paths,
or JSON; identifiers live in chips, links, and the expanded system card. Say "the operator" in fixed
UI text and show the configured name where a name is shown. Engine and model names are whatever the
engine seam reports.

Every time reads in the reader's own zone and locale, through one formatter; no screen, line or title
shows an ISO string, `Z`, `UTC` or an offset. Ages read "just now", "N min ago", "2h", "yesterday",
"3d". A record's time reads "14:05", dated ("Sep 16, 14:05") when not today, with the year when not
this one; a day divider reads "Today", "Yesterday", "Thu, Oct 8"; a date alone reads "Sep 24, 2036".
The exact time ("Oct 10, 2026, 4:58:42 PM PDT") appears on hover or in the expanded card.

Agent and system text keeps its own words, with written UTC times shown in local time outside code
spans, code blocks, links and paths, never adding a date the writer left out. A dated time (an ISO instant with `Z`
or an offset, a date and UTC clock, a month day and UTC clock such as "Oct 10, 23:58Z") reads as a
clock on the local day of the text's own recorded time and dated otherwise, with the exact time on
hover, seconds included. A UTC clock ("23:58Z", "23:58 UTC", "23:58 GMT") or range ("07:20–07:45 UTC", "07:20/07:45
UTC") stays a clock with no date, its hover the clock with the zone ("3:58:00 PM PST"); its offset is
the one at the occurrence nearest the text's own time, and a range's end is the first after its start.
A list of clocks sharing one zone ("18:43, 19:05 UTC"), a clock without a zone, a zone name such as
"PDT", and a date alone stay as written. Converted times look like the surrounding text and never
wrap inside.

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
| Project conversation | `GET /api/chat/<project>` | `POST /api/chat` (message, queue, Create task press), `POST /api/chat/remove`, `POST /api/l3/reset` |
| Models and settings | `GET /api/overview` `new_tasks`, `GET /api/defaults/<project>` | `POST /api/new-tasks`, `POST /api/defaults` (choice, defaults, routing; each with `expected`) |
| Project setup | `GET /api/setup/<project>` | `POST /api/project/setup` (check, repair, operator-approved hook integration) |
| Work panel | `GET /api/project/<name>`, `GET /api/overview` `queue` | none; rows open the owning conversation |
| Needs you | `GET /api/overview` plus project reference context | `POST /api/decide` |
| Task page | `GET /api/task/<project>/<slug>` (questions and messages), transcript on demand | `POST /api/l2/message`, `POST /api/decide`, `POST /api/task/action` |
| Report view | `GET /api/task/<project>/<slug>` (structured report, report notes and digest) | none |
| Host voice | `POST /api/voice/host` (setup, cancel, remove), `POST /api/voice/live`, `/api/voice/live/<id>/audio`, `/cancel` | the speech model under `~/.altitude/speech`; audio stays in memory for the recording only |
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
4. **Turns name what they created.** The assistant chat row of a turn that created a task carries
   `tasks: [slug]`, written by the server when the turn's task creation lands; one that filed GitHub
   issues through `alt issue new` or an `alt incident new|publish` that created the issue carries
   `issues: [{title, url}]`. A turn that fails after creating them carries both on its error row.
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
7. **Replies can offer a task.** L3's `alt task offer '<title>'` during a chat turn puts `offer:
   <title>` on that turn's assistant row when it created no task. A press posts `{project,
   offer_turn}` to `POST /api/chat`, which queues the operator message `Create task: <title>` with
   `offer_turn` while the reply is still the latest and nothing of the operator's waits; the task that
   turn creates records `offer_turn`, and a second one for the same reply is refused.

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
