# Project agents: proposal

Status: proposed on 2026-10-19 for the operator's choice. Nothing is built. In the interface the
feature is called **Agents**; one of them is "an agent", and one time it works is "a run". This
replaces the earlier scheduled-only proposal: a schedule is one kind of trigger, not the definition.

## What it is for

Some project work is the same question asked again and again, on a clock or after something happens:
did a dependency get a security fix, does each merged pull request leave its change tested, what did
this week's incidents have in common, does the suite still pass on a clean checkout. Today each waits
for you to remember to ask in chat. Altitude itself runs one recurring agent, the Weekly improvement
review, in a shape nobody else can reuse.

## The shape

An **agent** is a brief in your own words, one or more **triggers**, and limits. When a trigger fires,
Altitude starts a **run**: a sandboxed worker that does the brief and ends with a **report**, one line
and a full answer. You watch a run live as you watch a task, and you create, change, pause, run again
and retire agents in the app or by asking L3. That is the whole model; everything below is how it
stays safe, cheap and quiet.

- **Triggers.** Run now, always. A schedule: days and a time, several per agent. Something that happens
  in Altitude: a pull request merges, a task finishes, an incident is filed. Something on GitHub: an
  issue is opened, checks fail on the main branch, a release is published.
- **Inputs.** A run reads the brief, what fired it (the merged pull request, the incident) and the
  report of its own previous run, so it can say what changed instead of repeating itself.
- **Report.** "Found something", "Nothing found" or "Couldn't finish", one line, the full answer.
- **Limits.** One run at a time per agent, a time limit per run, a weekly budget per project, and the
  plan reserve every Altitude feature keeps for your own work.

## Triggers

| Trigger | What Altitude sees today | Arrives in |
| --- | --- | --- |
| **Run now** | A press on the agent's page, or asking L3 in chat | Step 1 |
| **On a schedule** | Its own 30-second timer, the one the Weekly review uses | Step 1 |
| **A pull request merges** | Altitude's own merges, at the moment they land | Step 2 |
| **A task finishes** | Every task archived as done | Step 2 |
| **An incident is filed** | Every incident Altitude records | Step 2 |
| **An issue is opened**, **checks fail on the main branch**, **a release is published** | Nothing today: Altitude has no GitHub webhooks and watches no issues. It reads checks only for its own pull requests | Step 3, through one read-only GitHub reader per project |

Decided myself: a device pairing is not a trigger, since it belongs to the machine, not a project; a
chat message is not a trigger, since an agent run on every message would duplicate L3 and spend
constantly. Asking L3 "run the dependency check" is Run now.

**Overlap.** An agent never runs twice at once. A schedule that comes due during a run is skipped and
says so. Events that arrive during a run wait, and the next run gets all of them together ("For PR
#810, #811 and #812"), so a burst of merges costs one more run, not three. A project runs one agent at
a time, and only in a free task slot with tasks served first, so agents never crowd out your work.

**Missed time.** A schedule missed while Altitude was off runs once when it comes back, marked late
("07:00, ran at 09:14"); older ones are not caught up. An event is never lost while Altitude runs: it is recorded with the agent before
anything starts. Each trigger fires once: a run is keyed by the agent and what fired it, so a restart
cannot start the same run twice.

**No loops.** Agent runs are not tasks for the purpose of triggers: a run finishing does not fire "a
task finishes", and nothing a run does creates an event.

## Lifecycle

| Agent | Meaning |
| --- | --- |
| **On** | Its triggers fire |
| **Paused** | Nothing fires; Run now still works. Paused by you, by L3 on your say, or by itself after three runs in a row couldn't finish |
| **Retired** | Gone from lists; its runs stay readable under Retired |

| Run | Meaning |
| --- | --- |
| **Waiting** | Fired, waiting for a free task slot (the machine's task cap) |
| **Running** | Its worker is working; Stop ends it |
| **Found something** / **Nothing found** | Finished with its report |
| **Couldn't finish** | The worker failed, or the brief could not be done (`make audit` is not a target) |
| **Stopped by you** / **Stopped at its limit** | Ended by Stop or by its time limit |
| **Skipped** | Not started, with the reason: the budget was spent, the plan was inside the reserve, it was still running |

**Failure and retry.** A failed run is not retried automatically: its next trigger is the retry, and
Run now is a manual one. Three runs in a row that couldn't finish or stopped at their limit pause the
agent (Stop and skips do not count), so a broken brief
or a lapsed sign-in stops costing time; it says so in chat, and from step 2 it waits in Needs you
until you resume, edit or retire it. A system fault still files its incident for L3 as every fault
does.

**Versions.** Every change to an agent is kept: who, when, from which message or press, and the brief
as it was. Each run records the version it ran, and its page shows that brief.

**Trying one.** Run now is the test: it runs the brief once, before or between triggers, and never uses
up a schedule time or an event. For an event agent it replays the latest matching event (the last
merged pull request).

**History.** An agent's page lists its runs, newest first. A run is kept as a finished task is, with
its report; its working copy is removed when it ends and it never pushes a branch.

## What a run may do

A run is a task worker with the helper role (`personas/l1.md`), not the task owner's: its own copy of
the repository, the same sandbox, model routing, machine task cap, Stop and live session as every task,
and a result to return instead of proposals, questions, pull requests or reports to manage.

**Reports only, enforced.** A run reads the project, builds it and runs its commands in its own copy.
It has the network, so it can read public pages and fetch dependencies. It has none of your sign-ins:

- the GitHub token task owners get is not passed to it;
- the places on this computer that hold sign-ins (SSH keys, the GitHub command's own sign-in file,
  git's stored credentials, the engines' sign-ins) are hidden from it, as from every sandboxed job: a
  separate change already under way hides them for all of them, and step 1 depends on it;
- its door admits an allow-list, reading its own status and returning its report, and refuses every
  other `alt` verb, including ones added later. It cannot push, open pull requests, file issues or
  incidents, start tasks, ask questions, message L3 or request approvals.

A model can be wrong in what it reports; it cannot change anything outside its own copy with your
identity. A credential kept somewhere unusual on this computer is the remaining gap.

**Event text is data.** A run receives what fired it quoted as data after its brief, never as
instructions. In steps 1 and 2 that text comes from Altitude and your own tasks. GitHub text (step 3)
can be written by anyone, and an agent that holds your private code, reads a stranger's words and can
reach the internet can be talked into sending one to the other. So by default a GitHub event fires only
when its author has write access to the repository, and a run fired by anyone else's text runs without
the network.

| | Reports only (recommended default; the only mode until step 5) | May act (step 5) |
| --- | --- | --- |
| Reads and runs commands in its own copy | Yes | Yes |
| GitHub | Public pages; from step 3, a read-only reader | Your sign-in, as task owners have |
| Pull requests | No | May open one: it goes through review and checks and is **held for your merge** |
| Issues and tasks | No; it can say what it would file (step 4: as suggestions) | Listed for L3 to file on your say |
| Questions to you | Never | Never |

## What a run may spend

- **Your plan, never an API key.** Runs use the sign-in task workers use; nothing is billed per use.
- **A budget per project.** **Agents may use up to 2 hours a week** (30 min, 1, 2, 5 or 10 hours). Every
  run's minutes count, Run now included; once spent, runs are skipped until Monday and say why. This is
  the one number that caps what agents take from your week.
- **A limit per run.** 10, 20 (default) or 60 minutes, counted from when its worker starts; at the limit
  Altitude stops it as Stop does. Time is the cap because it reads the same on every engine; tokens are
  recorded and shown, not capped, since most are cached rereads that count very differently against a
  plan.
- **Your work first.** Before a run starts, Altitude checks the engine routing chose for it: if that
  engine's weekly or five-hour window is at the reserve (90%, the improvement review's) or unreadable,
  the run is skipped and says so.
- **Off is off.** Pause stops triggers at once, Stop ends a run, Retire ends the agent.

## When a run deserves your attention

Agents are quiet by design. Each agent's **Tell me in chat** is When it finds something (default),
Every run, or Never. A run's line in the project's chat is a system line: it never alerts a device and
never counts in Needs you. Couldn't finish and Stopped at its limit appear unless Tell me is Never;
skips never do. Needs you shows an agent only when it needs a decision from you: from step 2 when it has
paused itself, from step 4 its suggestions (uncounted, as improvement suggestions are), and from step 5
its held pull requests, as review cards.

## Where agents appear

| Surface | What it shows | Step |
| --- | --- | --- |
| **Work** (the project's task list) | A running or waiting run is a row in **Current** ("Agent run · PR #812 · 2 of 20 min"), opening the run. After Done this week, a folded **Agents · 4 · 1 running** opens to one row per agent and links to All agents and New agent | 1 |
| **Agents page** | `/projects/<name>/agents`: every agent with its triggers and latest news, the weekly budget, New agent, Retired | 1 |
| **Agent page** | Status and Run now, recent runs, what it does and its triggers with Edit, Retire | 1 |
| **Run page** | The task page of the run: its live session while running, then its report and the brief version it ran | 1 |
| **Project chat** | Quiet result lines; Altitude-written lines for every change L3 makes, including the brief | 1 |
| **Monitor** | **Agents** across projects with what comes next and the latest result; a running run among Sessions | 1 |
| **Needs you** | Paused itself (step 2), suggestions (step 4), held pull requests (step 5) | 2, 4, 5 |
| **Project settings** | Nothing: agents are the project's work, not its configuration | – |

## L3 manages them on your say

L3 has `alt agent` verbs: list and show at any time; new, change, pause, resume, retire, run and stop
only in the chat turn answering your own message, which is recorded as the authority, as `alt improve
run` works today. L3 can suggest an agent in conversation but never creates or changes one on its own
judgement, and result lines reach its conversation as plain text written by Altitude, so a run's words
cannot start or change agents. Altitude, not L3, writes the line confirming a change, from the saved
record and with the brief: "Created Dependency check · weekdays at 07:00 · tells you when it finds
something", the brief's first two lines with **Show all**, and **Edit**.

## The Weekly improvement review

It stays its own feature and moves onto the same clock: its Monday 08:00 claim becomes one case of the
schedule rule (the latest due time not yet claimed, never before it was turned on, waiting while
Altitude activates a change), and its existing tests for a late turn-on, a missed week and a pending
activation pass unchanged. It is not made an agent because it is deliberately not a task: it has no
tools, reads a code-built summary of conversations under terms you agreed to, is proved by an eval, and
costs far less than a run. When agents gain suggestions (step 4), they use its suggestion card, and it
can then appear in the agents list as a built-in entry.

## What we considered from elsewhere

Managing agents that wake up on their own is a different problem from steering one task, and schedulers
and agent platforms have met most of it already. What they taught this design, with the source each
point was read in:

| Question | What others do | Here |
| --- | --- | --- |
| A trigger fires while a run is going | Temporal offers six overlap policies, Skip by default ([schedules](https://docs.temporal.io/schedule)); Kubernetes allows, forbids or replaces ([CronJob](https://kubernetes.io/docs/concepts/workloads/controllers/cron-jobs/)); GitHub keeps one running and one pending ([concurrency](https://docs.github.com/en/actions/writing-workflows/choosing-what-your-workflow-does/control-the-concurrency-of-workflows-and-jobs)) | Schedules skip; events wait and go together into one next run |
| Agents crowding out other work | GitHub's agentic workflows allow one agent job per engine at a time ([concurrency](https://github.github.com/gh-aw/reference/concurrency/)) | One agent run per project, only in a free task slot, tasks first, plus the weekly budget and the reserve |
| Missed or doubled runs | Kubernetes warns a job may run zero or two times ([CronJob](https://kubernetes.io/docs/concepts/workloads/controllers/cron-jobs/)); Dagster skips a run key already used ([sensors](https://dagster.io/docs/guides/automate/sensors)) | A run is keyed by agent and trigger; one late catch-up, marked late; no backfill |
| Failure | Devin does not retry, the next trigger does ([automations](https://docs.devin.ai/product-guides/automations)); Temporal can pause a schedule on failure ([schedules](https://docs.temporal.io/schedule)) | No automatic retry; three failures in a row pause the agent and tell you |
| Memory between runs | Temporal hands the last result to the next run ([schedules](https://docs.temporal.io/develop/python/schedules)); Cursor keeps a memories file but warns about untrusted input ([automations](https://cursor.com/docs/cloud-agent/automations)) | The previous report only; a notes file the run writes is left out, since injected text there would steer every later run |
| What an agent may do | GitHub's agentic workflows run the agent read-only and let separate, capped jobs perform what it asks for ([safe outputs](https://github.github.com/gh-aw/reference/safe-outputs/)); Claude's GitHub Action has no shell or GitHub access until granted ([docs](https://code.claude.com/docs/en/github-actions)) | Reports only, enforced; later actions go through Altitude's own held steps |
| Text from strangers | Simon Willison's "lethal trifecta": private data, untrusted content and a way out must not meet in one agent ([post](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/)); GitHub's agentic workflows sanitize event text and fire only for writers by default ([triggers](https://github.github.com/gh-aw/reference/triggers/)) | Event text is data; GitHub events fire for writers by default, and others' text runs offline |
| Loops | Claude's GitHub Action ignores bot actors ([docs](https://code.claude.com/docs/en/github-actions)) | Nothing a run does fires a trigger |
| Changed instructions | LangSmith versions every edit of an assistant ([assistants](https://docs.langchain.com/langsmith/assistants)) | Every change kept; each run names the version it ran |
| Trying one first | GitHub's staged mode previews without acting ([staged](https://github.github.com/gh-aw/reference/staged-mode/)); Dagster previews a sensor tick ([testing](https://dagster.io/docs/guides/automate/sensors/testing-sensors)) | Run now, which never uses up a schedule time or an event, replays the latest event |
| Attention | Codex shows an unread mark when a run needs you ([automations](https://learn.chatgpt.com/docs/automations?surface=app)); Devin mails always, on failure or on success ([automations](https://docs.devin.ai/product-guides/automations)) | A chat line when something was found or failed; Needs you only for decisions |
| Where agents are defined | GitHub's agentic workflows are markdown files in the repository ([overview](https://github.github.com/gh-aw/introduction/overview/)); Cursor and Devin keep them in the app | In Altitude's records, so a merged change or an event cannot rewrite an agent's instructions (direction C keeps files as the alternative) |

Considered and left out: automatic retries (they spend your plan and repeat work), an expiry date per
agent as GitHub's `stop-after` offers (the budget and Pause cover a forgotten agent), a turn cap beside
the time limit (time reads the same on every engine), and an alert when an expected run did not happen
(skips already say why, and Monitor shows what comes next).

## Directions

**A. Agents live with the project's work (recommended).** As drawn: runs appear in Work while they
run, the agents sit folded at the end of Work and on their own page, results come to chat, and Monitor
carries the cross-project view. Agents are something a project does, so they live where you look at
what it is doing, folded so they never compete with current work.

**B. Agents in project settings.** The same pages, reached from the project's settings instead of Work;
runs stay out of Work and show only in Monitor and chat. Quieter, and close to where the Weekly review
lives today, but a running agent is invisible from the project and agents read as configuration you
set once, which event-driven agents are not.

**C. Agents as files in the repository.** Each agent is a file such as `.altitude/agents/deps.md`:
triggers and limits in a short header, the brief below. Versioned, reviewable and copied between
projects with the code, the strongest "bring your own". But every change is a commit and a merge, L3
cannot pause or adjust one in a turn, and what runs is whatever is on the main branch. The app could
list, pause and run them. A later step can export an agent to a file and import one, without making
files the source of truth.

## Decisions that are yours

Approving the design approves the recommendations; any can be changed first.

| Decision | Recommendation | Alternatives |
| --- | --- | --- |
| What an agent is | A free brief, triggers and limits; it can name any skill, script or file in the repository; three editable examples | Fixed kinds with fields; or files in the repository (C) |
| What a run is | A sandboxed helper worker with your sign-ins withheld and an allow-listed door | A tool-free model call like the improvement review: cheaper, cannot run a check |
| Act or report | Reports only until step 5, then a per-agent choice with pull requests held for your merge | Allow acting sooner |
| Spend | A weekly budget per project (2 h), a limit per run (20 min), the 90% reserve | Per-run limit only; or a token cap |
| Attention | Chat lines only when it found something or failed; Needs you only for decisions | Every run in chat; or a daily digest line |
| Where they live | With the project's work (A) | Project settings (B) |
| Weekly review | Separate, sharing the clock | A built-in agent now |
| Order of steps | Schedule and Run now first; Altitude events; GitHub; suggestions; acting | Events in step 1 |

## Screens

Boards in this folder at 390×844 (phone) and 1440×900 (desktop), written by `gen.py`: `DesktopWork`,
`PhoneWork` (the project's task list), `DesktopAgents`, `PhoneAgents` (all agents), `DesktopAgent`,
`PhoneAgent` (one agent), `DesktopEdit`, `PhoneEdit` (the form), `DesktopChat`, `PhoneChat`,
`DesktopMonitor`, `PhoneMonitor`, `PhoneRetire`, and every state at both widths: `States` and
`PhoneStates`. The boards draw the full design; the steps say when each part arrives.

### Work

Work keeps answering "what is happening in this project?". A waiting or running run is a row in
**Current** with the ordinary dot and chevron: the agent's name, then "Agent run · PR #812 · 2 of 20
min" or "Agent run · waiting for a free task slot"; it counts as current and in flight like any task
and opens the run's page. Finished runs do not enter Done this week; their history is on the agent's
page. After Done this week, a fold **Agents · 4 · 1 running** (closed by default, as Done this week)
opens to one row per agent, its trigger and latest news ("Weekdays at 07:00 · found something 2 h
ago", "When a pull request merges · running", "Fridays at 17:00 · paused"), each opening the agent's
page, then **All agents** · **New agent**. With no agents the fold is absent and New agent is on the
Agents page and in the project menu (**Agents…**).

### All agents

`/projects/<name>/agents`, with **‹ harbor** back to the project and the title **Agents** over "Each
one runs a brief you wrote when its trigger fires, and tells you what it found." One card: a
whole-row link per agent with a chevron, the name in semibold and one muted line, its triggers then the
latest news, with a cue before it when one applies (a pulsing dot running, a pause mark paused):

- "Weekdays at 07:00 · Found something 2 h ago"
- "When a pull request merges · Running, 2 of 20 min"
- "Mondays at 08:00 · Waiting for a free task slot"
- "Fridays at 17:00 · Paused"
- "When you run it · Never run"

Several triggers read "Weekdays at 07:00 and when a pull request merges"; more than two read "3
triggers". Schedules read "Every day", "Weekdays", "Weekends", "Mondays", "Mon, Wed, Fri", with the
time in the machine's clock and its zone when the browser's differs ("07:00 Istanbul"). Below the
rows, **Agents may use up to** with a select (30 min · 1 h · 2 h · 5 h · 10 h a week) and "1 h 10 min
used this week", then a bordered **New agent**. A muted "2 retired" link opens the retired agents as
rows ("Retired Oct 19 · 23 runs"), each opening its read-only page.

### One agent

`/projects/<name>/agents/<id>`, **‹ harbor** back, the agent's name as the title. In order:

1. **Status card.** On: "Next: tomorrow at 07:00" (a schedule) or "Runs when a pull request merges"
   (events only) with **Run now** and **Pause**. Running: a spinner, "Running · 4 of 20 min", an
   underlined **Open** to the run, and **Stop**. Waiting: "Waiting for a free task slot" with **Stop**.
   Paused: a pause mark and "Paused" (or "Paused itself after 3 failed runs"), with
   **Resume** and **Run now**. Pause and Resume answer at once; the line changes with them.
2. **Recent runs.** The last ten, newest first: the outcome in semibold, date and time, minutes and
   tokens, and its line beneath, opening the run's page. An event run's line starts with what fired it
   ("For PR #810, #811 and #812: …"). Skipped runs show their reason and have no page or chevron. Dots:
   accent for found, danger for couldn't finish and limit, muted otherwise. **Show older** loads ten
   more. None: "No runs yet."
3. **What it does.** The brief clamped to three lines with **Show all**, then "Weekdays at 07:00 · Tells
   you when it finds something · Default model · Stops after 20 min", and a bordered **Edit**.
4. **Retire agent**: "It won't run again. Past runs stay readable." and an outlined red **Retire…**.

### The form

**Edit** opens `/projects/<name>/agents/<id>/edit`; **New agent** opens `/projects/<name>/agents/new`,
empty, with "Start from an example:" and three underlined examples that fill every field. Fields:
**Name**; **What it does** (hint "Write it as you would brief a task. It can use anything in the
repository, such as a skill or script."); **Runs when**; **Tell me in chat** (When it finds something ·
Every run · Never; segmented on desktop, stacked on phone); **Model** (the model button of §3.6.1);
**Each run stops after** (10 min · 20 min · 1 hour). **Save** or **Create agent**, and **Cancel**.

**Runs when** is a bordered list, one trigger per row with an icon (a clock for a schedule, a bolt for
an event), its words and a Remove button (44px on phone). A schedule row opens to seven day toggles
(M T W T F S S, 40px targets on phone, on one line) and a time; an event row reads "A pull request merges" over
"Its run reads that pull request.". **+ Add a trigger** opens a menu: On a schedule; In Altitude: A pull
request merges · A task finishes · An incident is filed; On GitHub: An issue is opened · Checks fail on
the main branch · A release is published. The menu lists only what the installed step supports. With
an event trigger, the hint "Events that arrive during a run go to its next run together." follows the
list. With no trigger the list reads "Only when you run it". New agents start with a weekday 07:00
schedule.

Without a name or brief, Create agent looks unavailable and, pressed, focuses the first empty field
with "Give it a name and a brief.". Leaving with unsaved changes asks "Discard your changes?" with
**Keep editing** (focused) and **Discard**. A change to a running agent applies from its next run.

Retire opens a dialog: **Retire Dependency check?**, "It won't run again, from its triggers or Run
now. Its past runs stay readable under Retired.", **Cancel** (first, focused) and a red **Retire**. A
retired agent's page has its title, "Retired Oct 19", its runs and its definition as text.

### In chat

Result lines are system lines (§3.4) at the time the run ended, led by the agent's name, with **Open**:

- "**Dependency check** found something: 2 packages have security fixes (requests, urllib3)." accent dot
- "**Review each merge** found something on PR #809: the upload limit moved without its test."
- "**Dependency check** found nothing: every dependency is current." only with Every run
- "**Dependency check** couldn't finish: `make audit` is not a target." danger dot, unless Never
- "**Dependency check** stopped at its 20-minute limit." danger dot, unless Never
- "**Dependency check** stopped by you." only with Every run
- "**Dependency check** paused itself: 3 runs in a row couldn't finish." danger dot, always

The line is at most 200 characters from the run's own summary, made plain as improvement lines are.
Changes made through L3 add one line from the record: Created (with the brief), "Changed Dependency
check: runs at 06:00 · Edit" (a changed brief shows its first two lines), "Paused Dependency check ·
Resume", "Retired Dependency check". L3's own reply stays short, since the line carries the details.

### Monitor

After Sessions, **Agents (N)** lists every agent that is on or ran in the last seven days, across
projects, one row each with a chevron: "<project> · <name>" with what comes next right-aligned ("Next
tomorrow 07:00", "Running 2 of 20 min", "Waiting for a slot", "Paused", "On merges") and the latest
result muted beneath ("Found something on PR #809 · yesterday · 6 min · 3.9M tokens"). A running run is
also a Sessions row "<project> / <name> · agent". The section shares Monitor's loading and failed read;
no agents, no section.

### States

| Surface | State | What appears |
| --- | --- | --- |
| Agents page | Loading / read failed | A spinner named Loading agents…; "Could not load agents." and Retry |
| Agents page | Empty | The line, the budget select and **New agent** |
| Budget | Saving / refused / spent | The choice shows at once with Saving…; refused: back to the saved value, "Not saved: <reason>" and Retry; spent: "2 h used this week · agents wait until Monday" in amber |
| Status | Run now pending / refused | "Starting…" until the run is read; refused: Run now again with its reason ("This week's budget is used.") |
| Status | Waiting / Stop pressed | "Waiting for a free task slot" with Stop; "Stopping…" until the end is read |
| Status | Pause pending / refused / paused itself | The line and button change at once; refused: they return with "Not saved: <reason>" and Retry; paused itself: its reason in the line |
| Runs | None / several events | "No runs yet."; one run's line names every event it took |
| Form | Saving / refused / unsaved / incomplete | Save shows a spinner; refused keeps the edit with "Not saved: <reason>" and Retry; the Discard dialog; "Give it a name and a brief." |
| Triggers | Add / none | The trigger menu; "Only when you run it" |
| Retire | Pending / refused | "Retiring…" with Cancel unavailable; refused: the reason under the buttons, Cancel works |
| Page | Retired | Title, "Retired Oct 19", runs, definition as text |
| Work | Run waiting / running / no agents | Rows in Current; the Agents fold absent |
| Chat | Each ending | The lines above |

## Steps

Each step is its own held pull request and comes back to you before it is built.

1. **Agents on a schedule or run by hand, reporting only (this task's increment).** The Agents page,
   agent page and form with schedule triggers; Run now from the page and through L3; runs as helper
   workers with your sign-ins withheld and an allow-listed door; the previous report as input; brief
   versions; the weekly budget, per-run limit and reserve; pausing itself after three failures, said in
   chat; Work's run rows and Agents fold; chat lines; Monitor's section; docs and tests.
2. **Altitude events.** A pull request merges, a task finishes, an incident is filed; events that wait
   and go together; Run now on the latest event; the paused-itself item in Needs you.
3. **GitHub.** One read-only GitHub reader per project, run by Altitude with your sign-in and never
   inside a run, polling for opened issues, failing checks on the main branch and releases; the same
   reader gives runs read-only access to private issues and pull requests. Events fire only for authors
   with write access unless you widen it, and a run fired by anyone else's text has no network. It is a
   new security boundary, so it gets its own review.
4. **Suggestions.** A run may propose up to three tasks, shown on the improvement suggestion card in
   chat and under Suggestions in Needs you, with Make a task and Dismiss; tasks made from one show
   "from <agent>" in Work. The Weekly review appears among the agents as a built-in entry.
5. **Acting.** A per-agent choice to open pull requests, each held for your merge as a review card, and
   to list issues for L3 to file.

## How the first step is built

- **Record.** `<project>/agents.json` holds each agent (name, brief, triggers, model and effort, tell,
  limit, state, the reason it paused), the weekly budget, every change with the brief as it was (who,
  when, source message or press), and per trigger the last claimed due time. A run is a task carrying
  `agent: {id, version, trigger}` with the trigger's key; its history is read from those tasks, so no
  second run store exists.
- **Clock.** One function returns the latest due time not yet claimed; `improve.weekly` uses it in place
  of its own Monday arithmetic, which is removed. The project tick claims due triggers durably, then
  creates their runs; while Altitude waits to activate a change, a claim waits for a later tick, as
  Weekly's does. A claim for an agent already running is recorded as a skip.
- **Run.** `tasks.new` with the run template (`templates/agent-run.md`: the brief, the trigger, the
  previous report, the result contract, the limit) and the agent's model pin. The worker launches with
  the L1 persona and no GitHub token, in the sandbox every task uses, whose credential hiding comes
  from the separate change it depends on. The task door admits only `alt task status` and `alt task done --digest
  --found|--nothing-found` for a run. Dispatch checks the budget and the routed engine's reserve before
  launch (`route.improvement_reserve` becomes one engine-parameterised check, used by both). The daemon
  stops a run at its limit. Runs are marked as runs in `alt task list` and L3's state digest, enter
  Work's Current only while waiting or running, archive like finished tasks and push no branch.
- **Chat.** Archival's task-linked line becomes the agent's line, written by Altitude and posted as Tell
  me allows.
- **Interfaces.** `alt agent list|show|new|set|pause|resume|retire|run|stop` on L3's socket and the
  operator's CLI; `GET /api/agents/<project>` and `POST /api/agents` presses with a request id from a
  paired device; Monitor's `agents` rows; the project view's run rows and agents fold.
- **Docs.** ARCHITECTURE (a Project agents section), CLI, OPERATIONS (configuring agents), SPEC (Work,
  a new Agents section, Monitor, chat lines), the L3 persona's verbs, the changelog.
- **Tests.** The schedule rule (due, missed, turned on late, paused, still running, activation wait)
  with Weekly's cases unchanged; budget and reserve skips on each engine; run creation and its inputs;
  the door's allow-list (every other verb refused, enumerated from the parser); no GitHub token in the
  launch; the limit stop; pausing after three failures; versions;
  result routing; L3 authority; the API; and phone and desktop walkthroughs of every step-1 state with
  fictional fixtures; engine calls stay at the fixture seam.

### Mac verification

The schedule lives in the service's own timer, so no launchd job is added. The Mac still owes: a run
launches under Seatbelt with no GitHub token in its job's input, its door refuses a verb outside the allow-list, and the limit stop ends its job. These are
listed in the pull request for L3 to file.
