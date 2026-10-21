# Agent graph: proposal

Status: proposed for the operator's choice. The boards beside this file are written by `gen.py`
(`python3 design/wireframes/agent-graph/gen.py`); review captures stay outside Git.

## What it is for

Work lists the project's current tasks as rows. That answers "what is each task doing", but not "how
much is in flight right now, who is waiting on whom, and where are the helpers". The graph is a second
way to read the same Current section: the coordinator in the middle, every current task around it,
the helpers each task is running as small satellites, live. Each node opens what its row opens.

The list stays the default and the only view on a phone. The graph is a desktop choice remembered by
this browser.

## Directions

| Direction | What it shows well | Why not first |
| --- | --- | --- |
| **Constellation** (recommended, `DesktopGraph.html`) | How much is in flight at once, who waits on whom (dependency arrows, amber operator waits), helpers per task, live activity. Every label sits in the same place for every node, so it reads the same with three tasks or thirteen. | It does not show progress along the lifecycle; the list never did either. |
| **Altitude map** (`AltAltitudeMap.html`) | The roles as bands: you, the coordinator, tasks, helpers. | Its horizontal "progress" axis has no source: Altitude records states, not how far a task has got, so nodes would glide on invented data. Helpers in their own band lose which task they belong to. |
| **Pipeline** (`AltPipeline.html`) | Bottlenecks as crowded columns. | Altitude's states are queued, running, blocked/reported and done; review and merge happen inside a running task, so Review and Merge columns sit almost always empty and everything crowds into Working. At 480px four columns leave titles two words wide. |

## Where it sits

- **Work's head** gains a two-button switch, **List** and **Graph**, right-aligned beside "Work" and its
  counts, on desktop only (§2.2 width ≥ 1024). A segmented control: a bordered group of two
  buttons with icons, the chosen one filled with the accent tint (`aria-checked`, `role="radio"` in a
  `radiogroup` named "Show work as"). Phone Work has no switch and always shows the list.
- **The graph replaces only the Current section.** Altitude repairs, **Done this week** and
  **Agents · N** stay below it, unchanged. Counts in the head are unchanged.
- **Width.** With Graph chosen the work panel is 480px, the task page's live-session width, inline
  at ≥ 1280 and the existing overlay below 1280 (`DesktopOverlay.html`); with List it stays 340px.
  The conversation keeps at least 540px at 1280, as the task page's does.
- **Remembered per browser.** `localStorage` key `altitude.workView`, value `graph`; absent or any
  other value means List. Nothing else is stored; it is a display preference, never data acted on.
  If storage is unavailable the switch still works for the page's lifetime (as the theme does).

## The constellation

**Layout.** The coordinator is a 60px filled accent disc reading **L3**, centred. Tasks are 40px
nodes evenly spaced clockwise from the top, in the list's order, along a ring of radius 150px around
it. Past seven tasks the ring stretches into a racetrack (two straight sides joined by the same
half circles) just long enough to keep 132px between neighbours, so labels never overlap; the graph
grows taller and the panel scrolls (`States.html`, thirteen tasks). A thin hairline spoke joins the
coordinator to each task.

**A node** is a disc ringed and lightly filled in its state colour, with its label centred under it:

- Title: 12.5px semibold, at most two lines, then an ellipsis.
- State line: 11.5px, at most two lines, then an ellipsis. It is the row's attention text up to its
  first " · " when the row shows one ("Your turn", amber, semibold), otherwise the row's meta up to its
  first " · ", ": " or ". " ("L2 working", "Planned", "Queued", "Waiting to resume", "Waiting for the
  coordinator", "The task session ended before completion", "Stopped", "Agent run"). A fault's state
  line is red. A task running helpers adds " · 2 helpers".
- Labels have a background in the panel colour, so a spoke never runs through text.

The full row text (title, attention, meta) is the link's accessible name and its tooltip; nothing
needs hover to be understood.

**State colours** are the list's dots, from the same `taskCardFacts` and `turnLabel`: running accent,
your turn amber (with a count badge of open questions at the node's top right; a step shows the amber
node without a number), fault or operator's Stop red, queued/planned/paused muted with a dashed ring.
An agent run is the same node with rounded-square corners and opens its run page.

**Live activity.** A running task whose worker recorded output in the last 60 seconds (the rule the
task page's "Working · output N sec ago" uses) wears a soft halo that breathes (1.6s, the existing
pulse). The coordinator breathes while L3 answers or handles something, and its state line is the
header's compact word: **Answering**, **Handling …**, **Busy**, **Ready**.

**Helpers.** Each helper the task's worker is running is a 14px accent dot on the node's upper rim
(spread 45° apart), with a 28px hit area. A helper that ends turns green (done), red (failed) or muted
(stopped) and leaves 60 seconds later, as the live session's **In the background** tray does. A
helper opens the task's Live session (`/projects/:name/tasks/:slug/live`), where its call row and
output are; there is no separate helper page. Its accessible name is "Helper of <task>: <description>,
running 4 min" or "…, done".

**Dependencies.** A planned task waiting for another current task gets a dashed muted arrow from the
prerequisite to it, bowed toward the centre so it leaves each node on its inner side, away from the
labels. When the prerequisite is not current, no arrow is drawn; the state line still says Planned
and the accessible name carries the reason. Releasing the task removes the arrow, as the list's
"Planned" leaves.

**Operator waits.** An amber node, its "Your turn" line and question badge; the coordinator keeps no
separate "you" node, since Needs you remains the place to answer.

**Clicks.** A task node opens exactly what its row opens (the owning question's anchor when it is your
turn, else the task conversation), with the same Back behaviour (§3.9). An agent run opens its run
page. The coordinator node is a button that focuses the project composer (closing the overlay below
1280): L3 has no page of its own and its conversation is beside the graph.

## States (`States.html`)

| State | Visible behaviour |
| --- | --- |
| Loading | One skeleton disc where the coordinator goes; no guessed nodes or counts. |
| No current work | The idle coordinator (**Ready**) and the list's own line, **No current tasks. Ask L3 to start something.** |
| Read fails, nothing saved | The list's error and **Retry**; no graph. |
| Saved after a failed refresh | Nodes stay and stay clickable; the head says "· saved" and the list's notice offers Retry. Halos and helpers drop until a fresh activity read, so a stale read never claims something is working. |
| A task arrives or leaves | It fades in at its slot (200ms) and the others glide to theirs (300ms); a finished task fades out and **Done this week** counts it, as the list's 200ms move does. |
| A helper finishes | Its dot changes colour, then fades out after 60 seconds. |
| Keyboard | Tab follows the list's order: the switch, the coordinator, then each task and its helpers. Enter opens; the focus ring is the app's. |
| Reduced motion | No breathing, glide or fade: a live node shows a static halo, nodes appear and leave in place. |
| Long title | Two lines and an ellipsis; the whole title is in the accessible name. |
| Many tasks | The racetrack grows; the panel scrolls. |
| Dark | Same tokens (`?dark` on every board). |

## Data: one new read

Everything except live activity and helpers comes from what Work already reads: `/api/project/:name`
rows (state, questions, `planned_wait.after`, fault, steering), `/api/overview` (decisions, queue
holds) and `/api/chat/:name` (L3 active/busy). The project page does not receive helpers or output
times today: they are per task, read by the task page from the worker's session.

The graph adds **`GET /api/project/:name/activity`**: for each running task of the project,
`{slug, generation, output_at, helpers}`. `output_at` is the worker's last recorded output time;
`helpers` lists the helper agents the engine seam reports for the current worker, `{id, label,
status, started_at, ended_at}`, running ones plus those that ended in the last 60 seconds, or `null`
when the engine reports none. Today the Claude seam reports helpers (foreground and background); the
Codex seam reports none, so a Codex task shows no satellites rather than a guess. The read uses the
same session reader as the task page's activity and the same task-access checks; it writes nothing.

The graph polls it every 5 seconds while the graph is on screen and a task is running, and not at
all in List or with the panel closed. It is ordinary in-memory query data for this project, dropped
with the page, never persisted; a failed read drops halos and helpers rather than showing old ones.

## Testing and docs

- Unit: the racetrack layout (spacing, growth), node facts reuse, reduced-motion classes.
- Browser (`web/e2e/agent-graph.pw.ts`, phone and desktop): List is the default; the switch shows the
  graph and is remembered across reload; phone shows no switch; a fixture of several tasks (running
  with helpers, your turn, planned after another, fault, queued, an agent run) renders each node with
  the right state and link; Tab order and Enter; reduced motion leaves nothing animating; a failed
  activity read; named captures of the graph for review.
- Server: the activity read for a Claude worker with helpers, a Codex worker, a non-running task, an
  unreadable session, and access refusal.
- Docs: SPEC §3.7 gains the graph; `docs/ARCHITECTURE.md` work and attention describes it and the
  read; CHANGELOG Unreleased.
