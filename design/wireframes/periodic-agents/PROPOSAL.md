# Scheduled agents: proposal

Status: proposed on 2026-10-19 for the operator's choice. Nothing is built. In the interface the
feature is called **Scheduled agents**; one of them is "an agent", and one time it works is "a run".

## What it is for

Some project work is the same question asked again and again: did a dependency get a security fix,
is CI getting flakier, which new issues need a label, what landed this week. Today each one waits for
you to remember to ask in chat. Altitude itself runs exactly one recurring agent, the Weekly
improvement review, in a shape nobody else can reuse.

A **scheduled agent** is a brief in your own words that Altitude runs on a schedule you choose, for
one project, and that tells you what it found. You write it once in the project's settings, or ask L3
in chat to set it up; you pause, change, run or retire it the same two ways. Anything you can ask a
task to look into, you can ask an agent to look into every morning.

## How it works

1. **You describe it.** A name, what it should do (a brief written as you would brief a task), when
   (days of the week and a time, or only when you press Run now), which model and effort, when to tell
   you in chat, and how long one run may take. L3 can fill in the same form for you from chat.
2. **Altitude starts it.** The service's own timer checks schedules on the tick that already runs the
   Weekly review, so nothing is installed in systemd or launchd and Linux and macOS behave the same.
   A run that came due while Altitude was down runs once at the next check; older missed runs are not
   caught up. A schedule never starts a second run while the first is still going.
3. **A run is a task.** Each run is an ordinary task created from the agent's brief: its own copy of
   the repository, the same sandbox, sign-in and model routing as every task owner, the same Stop and
   live session. It never asks you anything; if it cannot finish, it ends and says why.
4. **It reports only.** A run can read the project, build it and run its commands in its own copy.
   It cannot push, open pull requests, file issues, start tasks or ask for approvals: Altitude starts
   it without your GitHub sign-in, gives it read-only GitHub (the same relay L3 reads through) and
   refuses its landing and publishing verbs. Its words reach you only as its result.
5. **It tells you.** A run ends with one line and whether it found something. By default that line
   appears in the project's chat only when it found something; you can choose every run or never.
   The line is quiet: it never alerts a device and never counts in Needs you. The run's page keeps the
   full answer.
6. **You can see it.** The project's settings list its agents with their schedule and last result;
   each agent's page shows its recent runs. Monitor lists the agents across projects, and a running
   one appears among its sessions.

## Flexible without presets

There are no agent kinds. The brief is free text, and it may name anything in the repository: a
skill (`.claude/skills/triage`), a script, a make target, a checklist file. That is how you bring your
own agent: write the procedure where the project keeps its tools, then point an agent at it. The New
agent form offers three examples that fill in the form (a nightly dependency check, a weekly issue
triage, a daily summary of what landed); they are text you edit, not types.

Engine, model and effort use the same model button as tasks, so an agent can run on Codex or Claude,
on a small model for a cheap daily glance or a large one for a weekly deep look.

## Reports only, or allowed to act

| | Reports only (recommended default, and the only mode in the first step) | May act (a later step) |
| --- | --- | --- |
| Reads and runs commands in its own copy | Yes | Yes |
| GitHub | Reads only, through Altitude's relay | Your GitHub sign-in, as task owners have |
| Pull requests | No | May open one; it goes through review and checks and is **held for your merge** |
| Issues and tasks | No; it can say what it would file | Listed in its result for L3 to file or open on your say |
| Questions to you | Never | Never |

Reports only is enforced by Altitude, not only asked of the agent: the run starts without a GitHub
token and its door refuses `alt land`, grants, operator commands, reviews, validation and task or
issue creation. A model can still be wrong in what it reports; it cannot change anything outside its
own copy.

## What a run may spend

- **Your plan, never an API key.** Runs use the same sign-in as task owners; nothing is billed per
  use.
- **A limit per run.** Each agent has a time limit, 10, 20 (default) or 60 minutes from when its
  worker starts. At the limit Altitude stops the run the way Stop does and says so. Time is the cap
  because it is the same on every engine and you can reason about it; tokens are recorded and shown,
  not used as the cap, since most of a run's tokens are cached rereads that count very differently
  against a plan.
- **A ceiling per week you can read.** The agent's page says it plainly: "At most 5 runs a week, up
  to 20 minutes each." A 20-minute run is about the size of a small task (Altitude's smallest recent
  tasks made 50 to 80 model calls). How much of a weekly allowance one run takes is not something
  Altitude can measure per run, so each run's page and Monitor show its recorded tokens and minutes.
- **Ordinary work first.** A scheduled run is skipped, and says so on the agent's page and in Monitor,
  when its engine's weekly or five-hour window is at Altitude's reserve (90%, the same reserve the
  improvement review keeps) or unreadable. Run now is refused for the same reason. Runs share the
  machine's task cap with every other task.
- **Off is off.** Pausing an agent or retiring it stops its schedule at once; Stop ends a running run.

## L3 manages them on your say

L3 has `alt agent` verbs: list and show at any time; new, change, pause, resume, retire, run now and
stop only during the chat turn that answers your own message, which is recorded as the authority,
exactly as `alt improve run` works today. L3 never creates or changes an agent on its own judgement;
it can suggest one in conversation. Altitude, not L3, writes the confirmation line from the saved
record ("Created Dependency check: every day at 07:00 · tells you when it finds something · Edit"), so
what you read is what was saved.

## The Weekly improvement review

It stays its own feature and moves onto the same clock. Recommendation and reason:

- **Same clock.** Its Monday 08:00 claim becomes one case of the agents' schedule rule (the latest due
  time not yet claimed, never before it was turned on), so there is one scheduler, not two.
- **Separate feature.** The improvement review is deliberately not a task: it has no tools, reads a
  code-built summary of conversations under terms you agreed to, is proved by an eval, and uses far
  less of your plan than a small task. Making it a scheduled agent would either give a tool-using run your
  conversations or add a special runner to the general mechanism. Neither is simpler.
- **Later, one card.** When agents gain a "suggest tasks" result (step 2 below), it uses the
  improvement suggestion card, and the Weekly review can then appear in the agents list as a built-in
  entry. That is a later choice, not part of this one.

## Decisions that are yours

Approving the design approves the recommendations; any can be changed first.

| Decision | Recommendation | Alternatives |
| --- | --- | --- |
| What "flexible" means | A free brief that can name any skill, script or file in the repository; three editable examples | Fixed kinds with fields; or agents only as files in the repository (direction B) |
| What a run is | An ordinary task, so it can do anything a task can look into | A tool-free model call like the improvement review: cheaper, but cannot run a check |
| Act or report | Reports only, enforced; acting comes later as an explicit per-agent choice with PRs held for your merge | Allow acting from the start |
| Spend cap | Time per run (default 20 min) plus the 90% reserve skip; tokens shown, not capped | A token cap per run; or a weekly cap across a project's agents |
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

## Screens

Boards in this folder, at 390×844 (phone) and 1440×900 (desktop): `DesktopSettings`, `PhoneSettings`
(the section), `DesktopAgent`, `PhoneAgent` (one agent), `DesktopNew`, `PhoneNew` (the form),
`DesktopChat`, `PhoneChat` (what reaches chat), `DesktopMonitor`, `PhoneMonitor`, `PhoneRetire` (the
dialog), and `States` (every state below). `gen.py` writes them.

### The settings section

Project settings (`/settings/projects/<name>`) gain **Scheduled agents** (`#agents`) after Routing:
"Agents that run on a schedule you set and tell you what they find." over one card. Each agent is a
row that opens its page: the name in semibold, one muted line, and its switch at the right edge (on,
or paused). The muted line is the schedule, then the latest news:

- "Weekdays at 07:00 · Found something 2 h ago" (the result words are the run's outcome)
- "Every day at 06:30 · Running, 4 of 20 min" with a pulsing dot
- "Mondays at 08:00 · Paused"
- "Every day at 07:00 · Skipped today: Claude's allowance is inside the reserve"
- "When you run it · Never run"

Schedules read "Every day", "Weekdays", "Weekends", "Mondays", "Mon, Wed, Fri", "When you run it",
with the time in the machine's clock; when the browser's zone differs, the zone follows the time
("07:00 Istanbul"). A bordered **New agent** button ends the card. When agents have been retired, a
muted "2 retired" link under the card opens them read-only.

### One agent

`/settings/projects/<name>/agents/<id>`, with **‹ altitude** back to the section. The header is the
name with its switch. Under it, one status card:

- Idle: "Next: tomorrow at 07:00" with a bordered **Run now**.
- Running: a spinner, "Running · 4 of 20 min", an underlined **Open** to the run's page, and **Stop**.
- Paused: "Paused" with **Run now** still available.

Then the definition, edited in place: **Name**; **What it does** (a textarea; hint "Write it as you
would brief a task. It can use anything in the repository, such as a skill or script."); **When**
(seven day toggles M T W T F S S and a time; no day selected reads "Only when you run it"); **Tell me
in chat** (a segmented choice: When it finds something · Every run · Never); **Model** (the model
button of §3.6.1, Default to start); **Each run stops after** (10 min · 20 min · 1 hour); and a muted
row "Reports only: works in its own copy and can't push, open pull requests or file issues." Under the
fields, muted: "At most 5 runs a week, up to 20 minutes each, on your plan." **Save** and **Cancel**
appear only once a field differs from the saved agent, as in voice settings; a change to a running
agent applies from its next run.

**Recent runs** lists the last ten, newest first. A run row is the date and time, its outcome, its
minutes and tokens, and its one line beneath; it opens the run's page (the task page, whose last reply
holds the full answer). Outcomes: Found something · Nothing found · Couldn't finish · Stopped by you ·
Stopped at its limit · Skipped (the skip's reason as its line, no page). **Show older** loads ten more.

The page ends with **Retire agent** ("It won't run again. Past runs stay readable.") and an outlined
red **Retire…**, which opens a dialog: **Retire Dependency check?**, "It won't run again, from its
schedule or Run now. Its past runs stay readable under Retired.", **Cancel** (focused) and a red
**Retire**. A retired agent's page is read-only: no switch, fields as text, its runs.

### New agent

**New agent** opens `/settings/projects/<name>/agents/new`: the same fields, empty, with "Start from
an example:" and three underlined examples above the brief, which fill every field. Defaults: weekdays
at 07:00, When it finds something, Default model, 20 minutes. **Create agent** (filled) and
**Cancel**. With no name or brief, Create agent looks unavailable; pressed, it focuses the first empty
field and says "Give it a name and a brief.". Created, the page becomes the agent's page.

### In chat

Results are system lines (§3.4) in the project's conversation, at the time the run ended, in the
agent's name, with **Open** to the run's page:

- "Dependency check found something: 2 packages have security fixes (requests, urllib3)." accent dot
- "Dependency check found nothing: every dependency is current." only with Every run
- "Dependency check couldn't finish: `make audit` is not a target." danger dot, shown unless Never
- "Dependency check stopped at its 20-minute limit." shown unless Never

The run's line is at most 200 characters from its own summary, made plain as improvement lines are
(no links but Altitude's own). Skips never reach chat. Changes made through L3 add one line from the
record: "Created Dependency check: weekdays at 07:00 · tells you when it finds something · Edit",
"Paused Dependency check · Resume", "Retired Dependency check".

### Monitor

After Sessions, **Scheduled agents** lists every agent that is on or ran in the last seven days,
across projects, one row each: "<project> · <name>" with the next run right-aligned ("Next 07:00",
"Running 4 of 20 min", "Paused"), and the latest result muted under it ("Found something · 2 h ago ·
6 min · 4.1M tokens"); the row opens the agent's page. A running agent is also an L2 row in Sessions,
titled with its name. No agents: the section is absent.

### States

| Surface | State | What appears |
| --- | --- | --- |
| Section | Loading | A spinner named Loading agents… in the card |
| Section | Read failed | "Could not load agents." and Retry; other sections stay usable |
| Section | Empty | The section's line and **New agent** only |
| Switch | Saving / refused | The switch moves at once with Saving… under the row; refused: it returns, "Not saved: <reason>" and Retry |
| Run now | Pending / refused | "Starting…" in place until the run is read; refused: back to Run now with its reason under it ("It's already running.", "Claude's allowance is inside the reserve.") |
| Running | Stop pressed | "Stopping…" until the run's end is read |
| Fields | Saving / saved / refused | Save shows a spinner, then the saved values and a check named Saved; refused: "Not saved: <reason>", Retry, fields keep the edit |
| Runs | None yet | "No runs yet." |
| Retire | Pending / refused | "Retiring…" in the dialog's button; refused: the reason in red, Cancel works |
| Page | Retired | Read-only with "Retired Oct 19" in place of the switch |
| Chat | Each ending | The four lines above |

## Steps

1. **Reports-only agents (the first increment, held for your merge).** Everything above except
   acting: agents defined in project settings and through L3, schedules on the shared clock (the
   Weekly review moves onto it), runs as reports-only tasks with the time limit and reserve skip, chat
   lines, the agent page with its runs, Monitor's section, docs and tests.
2. **Suggest tasks.** A result that proposes up to three tasks on the improvement suggestion card,
   with Make a task and Dismiss.
3. **May act.** A per-agent choice to open pull requests (held for your merge) and to list issues for
   L3 to file.

Each later step comes back to you before it is built.

## How the first step is built

- **Record.** `<project>/agents.json` holds each agent (name, brief, days, time, model and effort,
  tell, limit, on, retired) with its changes (who, when, source message or press) and, per agent, the
  last claimed due time and recent skips. A run is a task carrying `agent: {id, due}`; its history is
  read from those tasks, so no second run store exists.
- **Clock.** One function returns the latest due time not yet claimed; `improve.weekly` uses it in
  place of its own Monday arithmetic, which is removed. The project tick claims due agents durably,
  then creates their tasks.
- **Run.** `tasks.new` with the agent's brief inside a run template and its model pin; the worker
  launches without the GitHub token and with the read-only `gh` relay; the task door refuses delivery,
  grant, review, validation, question and creation verbs for a run; a run ends with `alt task done
  --digest` and `--found` or `--nothing-found`. The daemon stops a run at its limit, and a block or
  fault ends it as Couldn't finish without an L3 recovery turn. Runs stay out of the Work panel and
  its archive; they are in Monitor and on the agent page.
- **Chat.** Archival's existing task-linked line becomes the agent's line, written by Altitude and
  posted only when Tell me allows.
- **Interfaces.** `alt agent list|show|new|set|pause|resume|retire|run|stop` on L3's socket and the
  operator's CLI; `GET /api/agents/<project>` and `POST /api/agents` presses with a request id from a
  paired device; Monitor's `agents` rows.
- **Docs.** ARCHITECTURE (a Scheduled agents section), CLI, OPERATIONS (configuring agents),
  SPEC §3.14 and §3.15, the L3 persona's verbs, the changelog.
- **Tests.** The schedule rule (due, missed, turned on late, paused, overlap, reserve), run creation and
  the refused verbs, the limit stop, result routing, L3 authority, the API, and phone and desktop
  walkthroughs of every state above with fictional fixtures; engine calls stay at the fixture seam.

### Mac verification

The schedule lives in the service's own timer, so no launchd job is added. The Mac still owes: a run
launches under Seatbelt without the GitHub token and reaches the read-only relay, and the limit stop
ends its job. These are listed in the PR body for L3 to file.
