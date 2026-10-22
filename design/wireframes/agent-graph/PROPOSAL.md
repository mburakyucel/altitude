# Agent graph: proposal

Status: the operator chose the constellation on 2026-10-22; it is built and folded into
[SPEC §2.2 and §3.7](../SPEC.md#37-work-panel). The boards beside this file are written by `gen.py`
(`python3 design/wireframes/agent-graph/gen.py`); review captures stay outside Git.

## What it is for

Work lists the project's current tasks as rows. That answers "what is each task doing", but not at a
glance "how much is in flight, who waits on whom, and which tasks have helpers working". The graph is a
second way to read the same Current section: every current task on a ring around the coordinator,
your turns first, the helpers each task is running as small pips, live. Each node opens what its row
opens.

The list stays the default and the only view on a phone. The graph is a desktop choice remembered by
this browser.

## Directions

The constellation was chosen over three others: live cues added to the list (cheapest, but no picture
of the whole), an altitude map (its progress axis would rest on data Altitude does not record) and a
pipeline (Review and Merge columns would sit empty, since both happen inside a running task).

## Where it sits

- **Work's head** gains a two-button switch, **List** and **Graph**, right-aligned beside "Work" and its
  counts, on desktop only (§2.2 width ≥ 1024). A segmented control: a bordered group named "Show
  work as" of two toggle buttons with icons, the pressed one filled with the accent tint. Phone Work
  has no switch and always shows the list.
- **The graph replaces only the Current section.** Altitude repairs, **Done this week** and
  **Agents · N** stay below it, unchanged. The head's counts are unchanged.
- **Width.** With Graph chosen the work panel is 480px, the task page's live-panel width (§2.2 gains
  this): inline at ≥ 1280, the existing overlay from 1024 to 1279 (`DesktopOverlay.html`). With List it
  stays 340px. The conversation keeps at least 540px at 1280, as on the task page, and reflows at once
  when the switch changes, without animation. 340px was tried: two-line labels of neighbouring nodes
  collide.
- **Remembered per browser.** `localStorage` key `altitude.workView`, value `graph`; absent or any other
  value means List. Nothing else is stored; it is a display preference, never data acted on. If
  storage is unavailable the switch still works for the page's lifetime, as the theme does.

## The constellation

**Layout.** The coordinator sits in the middle as a quiet outlined disc reading **L3**, with the
header's compact word under it: **Answering**, **Handling …**, **Busy**, **Ready** or **Not started**. It is a label,
not a control: L3's conversation is already beside the graph. Tasks are 40px nodes spaced clockwise
from the top in the list's order, except that your-turn tasks come first, so the top of the ring is
where you are needed. Up to seven tasks sit on a 150px circle; past that the ring stretches into a
racetrack (two straight sides joined by the same half circles) just long enough to keep 132px between
neighbours, so labels never overlap; the graph grows taller and the panel scrolls (`States.html`,
thirteen tasks). A hairline spoke joins the coordinator to each task.

**A node** is a disc ringed and lightly filled in its state colour, with its label centred under it:

- Title: 12.5px semibold, at most two lines, then an ellipsis, ending in a small chevron that marks
  the node as a link at rest.
- State line: 11.5px, wrapping, in the list's exact words. A row with attention text shows it whole
  (**Your turn · 1 question**, amber semibold); otherwise the row's meta up to its first " · " ("L2
  working", "Planned", "Queued", "Agent run"), or a fault's whole first sentence in red ("The task
  session ended before completion"). A task with active helpers adds " · 2 helpers active".
- Labels have a background in the panel colour, so a spoke never runs through text.

The full row text (title, attention, meta, helpers) is the link's accessible name and its tooltip;
nothing needs hover to be understood.

**State colours** are the list's dots, from the same `taskCardFacts` and `turnLabel`: running accent,
your turn amber (with a count badge of open questions at the node's top right; a step shows the amber
node without a number), fault or operator's Stop red, queued/planned/paused muted with a dashed ring.
An agent run is the same node with rounded-square corners and opens its run page; it has no halo or
helpers, since nothing records its requests.

**Live activity.** A running task whose worker made a model request in the last 60 seconds (the rule
the task page's activity line uses) wears a soft halo that breathes (the existing 1.6s pulse). The
coordinator breathes while L3 answers or handles something.

**Helpers.** Each helper of the current worker that made a model request in the last 60 seconds is a
10px accent pip on the node's upper rim, and the state line counts them ("2 helpers active"). Pips are
part of the task's link, not separate controls; the task's Live session shows each helper's work.
When the engine gives no helper evidence for a task, it shows no pips and no count, and the accessible
name says "helper activity unknown"; the graph never guesses zero.

**Dependencies.** A planned task waiting for another current task gets a dashed arrow from the
prerequisite to it, along the line between them and bowed toward the centre, clear of the pips and
labels. When the prerequisite is not current, no arrow is drawn; the state line still says Planned
and the accessible name carries the reason. Releasing the task removes the arrow, as the list's
"Planned" leaves.

**Operator waits.** An amber node with its "Your turn" line and question badge, first on the ring;
Needs you remains the place to answer.

**Clicks.** A task node opens exactly what its row opens (the owning question's anchor when it is your
turn, else the task conversation), with the same Back behaviour (§3.9). An agent run opens its run
page.

## States (`States.html`)

| State | Visible behaviour |
| --- | --- |
| Loading | One skeleton disc where the coordinator goes; no guessed nodes or counts. |
| No current work | The coordinator (**Ready**) and the list's own line, **No current tasks. Ask L3 to start something.** |
| Read fails, nothing saved | The list's error and **Retry**; no graph. |
| Saved after a failed refresh | Nodes stay and open; the list's notice offers Retry. No halo, pip or L3 word claims anything is live. |
| Activity data ages | Halos and pips show only while the task's activity reading is under 30 seconds old, so an old reading never claims work is happening. |
| A task arrives or leaves | An arriving task fades in at its slot (200ms) and the others glide to theirs (300ms); a finished task leaves the ring and **Done this week** counts it. |
| Keyboard | Tab: the switch, then each task in ring order. Enter opens; the focus ring is the app's. The coordinator is not a stop. |
| Reduced motion | No breathing, glide or fade: a live node keeps a static halo, nodes appear and leave in place. |
| Long title | Two lines and an ellipsis; the whole title is in the accessible name. |
| Helper activity unknown | No pips or count; the accessible name says so. |
| Many tasks | The racetrack grows; the panel scrolls. |
| Dark | Same tokens (`?dark` on every board). |

## Data: nothing new

Everything comes from what Work already reads and the change stream already refreshes:
`/api/project/:name` rows (state, questions, `planned_wait.after`, fault, steering), `/api/overview`
(decisions, queue holds) and `/api/chat/:name` (L3 active/busy). Live activity and helpers come from
each row's existing `token_usage`, which the service already collects incrementally about every ten
seconds for running tasks: the owner session's and each helper session's newest request time
(`observed_at`), the helper's owning session, and whether helper evidence exists at all. Both engines
report helper sessions there. No new server field, read or poll, and no cached data beyond the
project query the list already uses.

## Testing and docs

- Unit: the racetrack layout (spacing, growth), node facts and ordering, activity and helper rules
  (fresh, aged, unknown, other session), reduced-motion classes.
- Browser (`web/e2e/agent-graph.pw.ts`, phone and desktop): List is the default; the switch shows the
  graph and is remembered across reload; phone shows no switch; a fixture of several tasks (running
  with helpers, your turn, planned after another, fault, queued, an agent run) renders each node with
  the right state, link and accessible name; Tab order and Enter; reduced motion leaves nothing
  animating; saved after a failed refresh; named captures of the graph.
- Docs: SPEC §2.2 and §3.7 gain the graph and its width; `docs/ARCHITECTURE.md` work and attention
  describes it; CHANGELOG Unreleased. The alternatives' boards leave once a direction is chosen.
