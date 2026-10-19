# Scheduled agents: proposal

Status: proposed on 2026-10-19 for the operator's choice, revised after its independent design
review the same day. Nothing is built. In the interface the feature is called **Scheduled agents**;
one of them is "an agent", and one time it works is "a run".

## What it is for

Some project work is the same question asked again and again: did a dependency get a security fix,
is the build getting slower, does the test suite still pass on a clean checkout, what landed this
week. Today each one waits for you to remember to ask in chat. Altitude itself runs exactly one
recurring agent, the Weekly improvement review, in a shape nobody else can reuse.

A **scheduled agent** is a brief in your own words that Altitude runs on a schedule you choose, for
one project, and that tells you what it found. You write it once in the project's settings, or ask L3
in chat to set it up; you pause, change, run or retire it the same two ways. Anything you can ask a
helper to look into in the repository, you can ask an agent to look into every morning.

## How it works

1. **You describe it.** A name, what it should do (a brief written as you would brief a task), when
   (days of the week and a time, or only when you press Run now), which model and effort, when to tell
   you in chat, and how long one run may take. L3 can fill in the same form for you from chat.
2. **Altitude starts it.** The service's own timer checks schedules on the tick that already runs the
   Weekly review, so nothing is installed in systemd or launchd and Linux and macOS behave the same.
   A run that came due while Altitude was down runs once at the next check; older missed runs are not
   caught up. A schedule never starts a second run while the first is still running or waiting.
3. **A run is a task worker with a narrow door.** Each run is a task record created from the brief:
   its own copy of the repository, the same sandbox, sign-in, model routing and machine task cap as
   every task, the same Stop and live session. Its worker gets the helper role (`personas/l1.md`) with
   the run as its assignment, not the task owner's: it has no proposals, questions, pull requests or
   reports to manage, only a result to return.
4. **It reports only.** A run reads the project, builds it and runs its commands in its own copy.
   Altitude starts it without your GitHub sign-in, and its door admits an allow-list: reading its own
   status and returning its result. Every other `alt` verb is refused, including ones added later.
   It cannot push, open pull requests, file issues or incidents, start tasks, ask questions, message
   L3 or request approvals. Its words reach you only as its result.
5. **It tells you.** A run ends with one line and whether it found something. By default that line
   appears in the project's chat only when it found something or could not finish; you can choose every
   run or never. The line is quiet: it never alerts a device and never counts in Needs you. The run's
   page keeps the full answer.
6. **You can see it.** The project's settings list its agents with their schedule and latest news;
   each agent's page leads with its status and recent runs. Monitor lists the agents across projects,
   and a running one appears among its sessions.

## Flexible without presets

There are no agent kinds. The brief is free text, and it may name anything in the repository: a
skill (`.claude/skills/audit`), a script, a make target, a checklist file. That is how you bring your
own agent: write the procedure where the project keeps its tools, then point an agent at it. The New
agent form offers three examples that fill in the form (a nightly dependency check, a weekly build
health check, a daily summary of what landed); they are text you edit, not types.

Engine, model and effort use the same model button as tasks, so an agent can run on Codex or Claude,
on a small model for a cheap daily glance or a large one for a weekly deep look.

## Reports only, or allowed to act

| | Reports only (recommended default, the only mode in the first step) | May act (a later step) |
| --- | --- | --- |
| Reads and runs commands in its own copy | Yes | Yes |
| GitHub | None in the first step; public web pages only. Reading private issues and pull requests comes with its own reviewed step | Your GitHub sign-in, as task owners have |
| Pull requests | No | May open one; it goes through review and checks and is **held for your merge** |
| Issues and tasks | No; it can say what it would file | Listed in its result for L3 to file or open on your say |
| Questions to you | Never | Never |

Reports only is enforced by Altitude, not only asked of the agent: no GitHub token reaches the run,
and its door admits only its allow-list. A model can still be wrong in what it reports; it cannot
change anything outside its own copy. Agents that need private GitHub reads, such as an issue
triage, wait for the later step that gives runs a reviewed read-only GitHub path.

## What a run may spend

- **Your plan, never an API key.** Runs use the same sign-in as task workers; nothing is billed per
  use.
- **A budget per project.** The section has one setting: **Agents may use up to 2 hours a week**
  (30 min, 1, 2, 5 or 10 hours). Every run's minutes count, Run now included; once the week's budget
  is spent, runs are skipped until Monday and say why. This is the one number that caps what agents
  take from your week.
- **A limit per run.** Each agent stops after 10, 20 (default) or 60 minutes of work, counted from
  when its worker starts. At the limit Altitude stops the run the way Stop does and says so. Time is
  the cap because it is the same on every engine and easy to reason about; tokens are recorded and
  shown, not capped, since most of a run's tokens are cached rereads that count very differently
  against a plan. A 20-minute run is about the size of a small task (Altitude's smallest recent tasks
  made 50 to 80 model calls); how much of a weekly allowance one run takes is not something Altitude
  can measure per run, so each run shows its recorded minutes and tokens.
- **Ordinary work first.** When a run is about to start, Altitude checks the engine routing chose for
  it: if that engine's weekly or five-hour window is at Altitude's reserve (90%, the reserve the
  improvement review keeps) or unreadable, the run is skipped and says so. Run now is refused for the
  same reason.
- **Off is off.** Pause stops an agent's schedule at once; Stop ends a running run; retiring ends it
  for good.

## L3 manages them on your say

L3 has `alt agent` verbs: list and show at any time; new, change, pause, resume, retire, run now and
stop only during the chat turn that answers your own message, which is recorded as the authority,
exactly as `alt improve run` works today. L3 never creates or changes an agent on its own judgement;
it can suggest one in conversation. Agent result lines in chat are written by Altitude and made plain,
and L3 acts on agents only for your own message, so a run's words cannot start or change agents.

Altitude, not L3, writes the confirmation line from the saved record, and it carries the brief, so
what you read is exactly what will run: "Created Dependency check · weekdays at 07:00 · tells you when
it finds something", then the brief's first two lines with **Show all**, and **Edit**.

## The Weekly improvement review

It stays its own feature and moves onto the same clock. Recommendation and reason:

- **Same clock.** Its Monday 08:00 claim becomes one case of the agents' rule: the latest due time not
  yet claimed, never one before the schedule was turned on, and waiting for a later tick while
  Altitude waits to activate a change. Its existing tests for a late turn-on, a missed week and a
  pending activation stay as they are and must pass unchanged. One scheduler, not two.
- **Separate feature.** The improvement review is deliberately not a task: it has no tools, reads a
  code-built summary of conversations under terms you agreed to, is proved by an eval, and uses far
  less of your plan than a small task. Making it a scheduled agent would either give a tool-using run
  your conversations or add a special runner to the general mechanism. Neither is simpler.
- **Later, one card.** When agents gain a "suggest tasks" result (step 2 below), it uses the
  improvement suggestion card, and the Weekly review can then appear in the agents list as a built-in
  entry. That is a later choice, not part of this one.

## Decisions that are yours

Approving the design approves the recommendations; any can be changed first.

| Decision | Recommendation | Alternatives |
| --- | --- | --- |
| What "flexible" means | A free brief that can name any skill, script or file in the repository; three editable examples | Fixed kinds with fields; or agents only as files in the repository (direction B) |
| What a run is | A task worker with the helper role and a narrow door, so it can run the project's own checks | A tool-free model call like the improvement review: cheaper, but cannot run a check |
| Act or report | Reports only, enforced, with no GitHub access in the first step; acting comes later as an explicit per-agent choice with pull requests held for your merge | Allow acting from the start |
| Spend cap | A weekly time budget per project (default 2 hours), a time limit per run (default 20 minutes) and the 90% reserve skip | Per-run limit only; or a token cap per run |
| When it speaks in chat | Only when it found something or could not finish | Every run; or never in chat (its page only) |
| Weekly improvement review | Stays separate, shares the clock | Becomes a built-in agent now |
| Where you manage them | Project settings, an agent page per agent, and L3 in chat (direction A) | A Scheduled section in the Work panel (direction C) |

## Directions

**A. Agents in project settings (recommended).** As described above. Agents are configuration you
touch rarely, so they live with the project's other settings; what they find comes to you in chat,
where you already look, and only when it matters. Monitor carries the cross-project view.

**B. Agents as files in the repository.** Each agent is a file such as `.altitude/agents/deps.md`:
schedule, model and result in a short header, the brief below. Agents are versioned, reviewable and
copy straight into another project, which is the strongest form of "bring your own". But every change
is a commit: L3 cannot pause or adjust one in a turn, a change needs a task and a merge, and the
schedule a run uses is whatever is on the main branch. The app could only list them and pause them.

**C. Agents beside the work.** The same agents, listed in a **Scheduled** section at the end of the
Work panel with next and last run, Run now and pause in place. They are always in view, but recurring
background checks then compete with the work you are steering, on every visit, for a feature you
change perhaps once a month.

The independent review offered two directions, and this revision adopts both: the agent page leads
with results and keeps the definition behind **Edit**; and the first step gives runs no GitHub access,
an allow-listed door, and one weekly budget per project that Run now counts against. Its trade-off
stays: agents that need private GitHub reads wait for a later step, and one busy agent can use up the
project's budget for the rest.

## Screens

Boards in this folder, at 390×844 (phone) and 1440×900 (desktop): `DesktopSettings`, `PhoneSettings`
(the section), `DesktopAgent`, `PhoneAgent` (one agent), `DesktopEdit`, `PhoneEdit` (the form, for a
new agent or Edit), `DesktopChat`, `PhoneChat` (what reaches chat), `DesktopMonitor`, `PhoneMonitor`,
`PhoneRetire` (the dialog), and the states at both widths: `States` (desktop) and `PhoneStates`.
`gen.py` writes them.

### The settings section

Project settings (`/settings/projects/<name>`) gain **Scheduled agents** (`#agents`) after Routing:
"Agents that run on a schedule you set and tell you what they find." over one card. Each agent is a
whole-row link with a chevron: the name in semibold and one muted line, the schedule then the latest
news, with a cue before it when one applies (a pulsing dot while running, a pause mark while paused):

- "Weekdays at 07:00 · Found something 2 h ago"
- "Every day at 06:30 · Running, 4 of 20 min"
- "Mondays at 08:00 · Paused"
- "Fridays at 17:00 · Skipped Fri: the week's budget was used"
- "When you run it · Never run"

Schedules read "Every day", "Weekdays", "Weekends", "Mondays", "Mon, Wed, Fri", "When you run it",
with the time in the machine's clock; when the browser's zone differs, the zone follows the time
("07:00 Istanbul"). Pausing is on the agent's page, so a row has one target. Below the rows,
**Agents may use up to** with a select (30 min · 1 h · 2 h · 5 h · 10 h a week) and its muted line
"1 h 10 min used this week", then a bordered **New agent**. When agents have been retired, a muted
"2 retired" link under the card opens `/settings/projects/<name>/agents/retired`: the retired agents
as rows ("Retired Oct 19 · 23 runs"), each opening its read-only page.

### One agent

`/settings/projects/<name>/agents/<id>`, with **‹ harbor** back to the section and the agent's name
as the title. In order:

1. **Status card.** Idle: "Next: tomorrow at 07:00" with **Run now** and **Pause**. Running: a
   spinner, "Running · 4 of 20 min", an underlined **Open** to the run's live page, and **Stop**.
   Waiting: "Waiting for a free task slot" with **Stop**. Paused: a pause mark and "Paused", with
   **Resume** and **Run now**. Pause and Resume answer at once (§1.1); the line changes with them.
2. **Recent runs.** The last ten, newest first. A run row is its outcome in semibold, the date and
   time, its minutes and tokens, and its one line beneath, opening the run's page (the task page,
   whose last reply holds the full answer). Outcomes: Found something · Nothing found · Couldn't
   finish · Stopped by you · Stopped at its limit · Skipped (the reason as its line; no page, no
   chevron). Dots: accent for found, danger for couldn't finish and limit, muted otherwise. **Show
   older** loads ten more. No runs: "No runs yet."
3. **Definition.** A card summarising what will run: the brief clamped to three lines with **Show
   all**, then "Weekdays at 07:00 · Tells you when it finds something · Default model · Stops after
   20 min", and a bordered **Edit**.
4. **Retire agent**: "It won't run again. Past runs stay readable." and an outlined red **Retire…**.

**Edit** opens `/settings/projects/<name>/agents/<id>/edit`, the same form as New agent filled in:
**Name**; **What it does** (a textarea; hint "Write it as you would brief a task. It can use anything
in the repository, such as a skill or script."); **When** (seven day toggles M T W T F S S, each a 40px
target on phone, and a time; no day selected reads "Only when you run it"); **Tell me in chat** (When
it finds something · Every run · Never; a segmented choice on desktop, three stacked choices on
phone); **Model** (the model button of §3.6.1, Default to start); **Each run stops after** (10 min ·
20 min · 1 hour). **Save** (filled) and **Cancel**. A change to a running agent applies from its next
run. Leaving with unsaved changes asks "Discard your changes?" with **Keep editing** (focused) and
**Discard**.

Retire opens a dialog: **Retire Dependency check?**, "It won't run again, from its schedule or Run
now. Its past runs stay readable under Retired.", **Cancel** (first, focused) and a red **Retire**. A
retired agent's page has no status card, Edit or Retire: its title, "Retired Oct 19", its runs and
its definition as text.

### New agent

**New agent** opens `/settings/projects/<name>/agents/new`: the form, empty, with "Start from an
example:" and three underlined examples under Name, which fill every field. Defaults: weekdays at
07:00, When it finds something, Default model, 20 minutes. **Create agent** (filled) and **Cancel**.
With no name or brief, Create agent looks unavailable; pressed, it focuses the first empty field and
says "Give it a name and a brief.". Created, the page becomes the agent's page.

### In chat

Results are system lines (§3.4) in the project's conversation, at the time the run ended, led by the
agent's name, with **Open** to the run's page:

- "**Dependency check** found something: 2 packages have security fixes (requests, urllib3)." accent dot
- "**Dependency check** found nothing: every dependency is current." only with Every run
- "**Dependency check** couldn't finish: `make audit` is not a target." danger dot, unless Never
- "**Dependency check** stopped at its 20-minute limit." danger dot, unless Never
- "**Dependency check** stopped by you." only with Every run

The run's line is at most 200 characters from its own summary, made plain as improvement lines are
(no links but Altitude's own). Skips never reach chat. Changes made through L3 add one line from the
record: the Created line above (with the brief), "Changed Dependency check: runs at 06:00 · Edit"
naming what changed (a changed brief shows its first two lines with Show all), "Paused Dependency
check · Resume", "Retired Dependency check". L3's own reply around it stays short, since the line
carries the details.

### Monitor

After Sessions, **Scheduled agents** lists every agent that is on or ran in the last seven days,
across projects, one row each with a chevron: "<project> · <name>" with what comes next right-aligned
("Next today 07:00", "Next Fri 17:00", "Running 4 of 20 min", "Waiting for a slot", "Paused"), and the
latest result muted under it ("Found something · 2 h ago · 6 min · 4.1M tokens"); the row opens the
agent's page. A running agent is also an L2 row in Sessions, titled "<project> / <name> · scheduled".
The section reads with the Monitor page, so it shares its loading skeleton and its failed read; no
agents, no section.

### States

| Surface | State | What appears |
| --- | --- | --- |
| Section | Loading | A spinner named Loading agents… in the card |
| Section | Read failed | "Could not load agents." and Retry; other sections stay usable |
| Section | Empty | The section's line, the budget select and **New agent** |
| Budget | Saving / refused | The select shows the choice at once with Saving…; refused: back to the saved value, "Not saved: <reason>" and Retry |
| Budget | Spent | The used line reads "2 h used this week · agents wait until Monday" in amber |
| Status | Run now pending / refused | "Starting…" in place until the run is read; refused: Run now again with its reason under it ("Claude's allowance is inside the reserve.", "This week's budget is used.") |
| Status | Waiting for a slot | "Waiting for a free task slot" with Stop |
| Status | Pause / Resume pending | The line and button change at once; refused: they return, "Not saved: <reason>" and Retry |
| Status | Stop pressed | "Stopping…" until the run's end is read |
| Edit | Saving / refused | Save shows a spinner; saved returns to the agent's page; refused: "Not saved: <reason>", Retry, the edit kept |
| Edit | Leaving unsaved | The Discard dialog |
| New | Missing name or brief | Create agent unavailable; pressed, "Give it a name and a brief." |
| Runs | None yet | "No runs yet." |
| Retire | Pending / refused | "Retiring…" in the dialog's button with Cancel unavailable; refused: the reason in red under the buttons, Cancel works |
| Page | Retired | Title, "Retired Oct 19", runs, definition as text |
| Chat | Each ending | The lines above |

## Steps

1. **Reports-only agents (the first increment, held for your merge).** Everything above except
   acting: agents defined in project settings and through L3, schedules on the shared clock (the
   Weekly review moves onto it), runs as allow-listed helper workers with no GitHub token, the weekly
   budget, the per-run limit and reserve skip, chat lines, the agent page with its runs, Monitor's
   section, docs and tests.
2. **Suggest tasks.** A result that proposes up to three tasks on the improvement suggestion card,
   with Make a task and Dismiss.
3. **Read GitHub, then act.** A reviewed read-only GitHub path for runs; then a per-agent choice to
   open pull requests (held for your merge) and to list issues for L3 to file.

Each later step comes back to you before it is built.

## How the first step is built

- **Record.** `<project>/agents.json` holds each agent (name, brief, days, time, model and effort,
  tell, limit, paused, retired), the project's weekly budget, every change (who, when, source message
  or press) and, per agent, the last claimed due time and recent skips. A run is a task carrying
  `agent: {id, due}`; its history is read from those tasks, so no second run store exists.
- **Clock.** One function returns the latest due time not yet claimed; `improve.weekly` uses it in
  place of its own Monday arithmetic, which is removed. The project tick claims due agents durably,
  then creates their tasks; while Altitude waits to activate a change, the claim waits for a later
  tick, as Weekly's does.
- **Run.** `tasks.new` with the run template (`templates/agent-run.md`: the brief, the result
  contract, the limit) and the agent's model pin. The worker launches with the L1 persona, without
  the GitHub token. The task door admits only `alt task status` and `alt task done --digest
  --found|--nothing-found` for a run, refusing every other verb by default. Dispatch checks the
  budget and the routed engine's reserve before launch (`route.improvement_reserve` becomes one
  engine-parameterised reserve check, used by both). A run queued at its next due time makes that due
  time a skip ("the earlier run was still waiting"). The daemon stops a run at its limit. A run's worker
  failure ends it as Couldn't finish; a system fault still files its incident for L3 as every fault
  does, but nothing resumes the run, since the next due time is its retry. Runs stay out of the Work
  panel and its archive and are marked as runs in `alt task list` and L3's state digest; their
  folders archive and their worktrees are cleaned like every finished task's, and they push no
  branch.
- **Chat.** Archival's existing task-linked line becomes the agent's line, written by Altitude and
  posted only when Tell me allows.
- **Interfaces.** `alt agent list|show|new|set|pause|resume|retire|run|stop` on L3's socket and the
  operator's CLI; `GET /api/agents/<project>` and `POST /api/agents` presses with a request id from a
  paired device; Monitor's `agents` rows.
- **Docs.** ARCHITECTURE (a Scheduled agents section), CLI, OPERATIONS (configuring agents),
  SPEC §3.14 and §3.15, the L3 persona's verbs, the changelog.
- **Tests.** The schedule rule (due, missed, turned on late, paused, overlap, activation wait) with
  Weekly's existing cases unchanged; budget and reserve skips on each engine; run creation, the door's
  allow-list (every other verb refused, enumerated from the parser) and no GitHub token in the launch;
  the limit stop; result routing; L3 authority; the API; and phone and desktop walkthroughs of every
  state above with fictional fixtures; engine calls stay at the fixture seam.

### Mac verification

The schedule lives in the service's own timer, so no launchd job is added. The Mac still owes: a run
launches under Seatbelt with no GitHub token in its job's input, its door refuses a verb outside the
allow-list, and the limit stop ends its job. These are listed in the PR body for L3 to file.
