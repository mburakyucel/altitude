# Project agents: proposal

Status: approved by the operator on 2026-10-19 with one addition: Altitude ships built-in agents,
including the Weekly improvement review, that you turn on with one press. Steps 1, 2 ([The Weekly
improvement review](#the-weekly-improvement-review)) and 3 ([Monitor](#monitor-step-3)) are built. In
the interface the feature is called **Agents**; one of them is "an agent", and one time it works is "a run". This
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
and retire agents in the app or by asking L3. A run is not a task: it has its own page under its agent,
and never counts among your tasks. That is the whole model; everything below is how it
stays safe, cheap and quiet.

- **Triggers.** Run now, always. A schedule: days and a time, several per agent. Something that happens
  in Altitude: a pull request merges, a task finishes, an incident is filed. Something on GitHub: an
  issue is opened, checks fail on the main branch, a release is published.
- **Inputs.** A run reads the brief, what fired it (the merged pull request, the incident) and the
  report of its own previous run, so it can say what changed instead of repeating itself.
- **Report.** "Found something", "Nothing found" or "Couldn't finish", one line, the full answer.
- **Limits.** One agent run at a time per project, after your tasks; a time limit per run; a weekly
  budget per project; and the plan reserve every Altitude feature keeps for your own work.

## Triggers

| Trigger | What Altitude sees today | Arrives in |
| --- | --- | --- |
| **Run now** | A press on the agent's page, or asking L3 in chat | Step 1 |
| **On a schedule** | Its own 30-second timer, the one the Weekly review uses | Step 1 |
| **A pull request merges** | Altitude's own merges, at the moment they land | Step 4 |
| **A task finishes** | Every task archived as done | Step 4 |
| **An incident is filed** | Every incident Altitude records | Step 4 |
| **An issue is opened**, **checks fail on the main branch**, **a release is published** | Nothing today: Altitude has no GitHub webhooks and watches no issues. It reads checks only for its own pull requests | Step 5, through one read-only GitHub reader per project |

Decided myself: a device pairing is not a trigger, since it belongs to the machine, not a project; a
chat message is not a trigger, since an agent run on every message would duplicate L3 and spend
constantly. Asking L3 "run the dependency check" is Run now.

**Overlap.** An agent has at most one run waiting or running. A schedule that comes due meanwhile is
skipped and says so. Events that arrive meanwhile join it if it is still waiting, or wait for the next
run, which gets all of them together ("For PR #810, #811 and #812"), so a burst of merges costs one more
run, not three. A project runs one agent at a time, and only in a free task slot with tasks served
first, so agents never crowd out your work. Waiting runs start oldest first, and one that has waited
six hours is skipped ("no free slot for 6 hours"), so a 07:00 check never starts at midnight.

**Missed time.** A schedule missed while Altitude was off runs once when it comes back, marked late
("07:00, ran at 09:14"); older ones are not caught up. An event is never lost while Altitude runs: it is recorded with the agent before
anything starts. Each trigger fires once: a run is keyed by the agent and what fired it, so a restart
cannot start the same run twice.

**No loops.** An event that comes from an agent run never fires an agent: a run finishing is not "a
task finishes", the incident a failed run files does not fire "an incident is filed", and from step 7
the merge of a pull request a run opened does not fire "a pull request merges". Each event records
where it came from, and each step that adds a trigger tests this rule for it.

## Lifecycle

| Agent | Meaning |
| --- | --- |
| **On** | Its triggers fire |
| **Paused** | Nothing fires; Run now still works. New agents start here; paused by you, by L3 on your say, or by itself after three runs in a row couldn't finish |
| **Retired** | Gone from lists; its runs stay readable under Retired |

| Run | Meaning |
| --- | --- |
| **Waiting** | Fired, waiting for a free task slot or for the project's other agent run to finish |
| **Running** | Its worker is working; Stop ends it |
| **Found something** / **Nothing found** | Finished with its report |
| **Couldn't finish** | The worker failed, or the brief could not be done (`make audit` is not a target) |
| **Stopped by you** / **Stopped at its limit** | Ended by Stop or by its time limit |
| **Skipped** | Not started, with the reason: the budget could not fit its limit, the plan was inside the reserve, its last run was still going, no slot for six hours |

**Failure and retry.** A failed run is not retried automatically: its next trigger is the retry, and
Run now is a manual one. Three triggered runs in a row that couldn't finish or stopped at their limit
pause the agent (Run now, Stop and skips do not count), so a broken brief
or a lapsed sign-in stops costing time; it says so in chat, and from step 4 it waits in Needs you
until you resume, edit or retire it. A system fault still files its incident for L3 as every fault
does.

**Versions.** Every change to an agent is kept: who, when, from which message or press, and the brief
as it was. Each run records the version it ran, and its page shows that brief.

**Trying one.** A new agent starts paused, so a half-written brief never fires at 07:00: its page
offers **Run now** and **Turn on**. Run now is the test: it runs the brief once, before or between
triggers, and never uses up a schedule time or an event. For an event agent it replays the latest
matching event (the last merged pull request). When you ask L3 for an agent that should start at once,
its offer says so and Create turns it on.

**History.** An agent's page lists its runs, newest first. A run keeps its report and session record
under its agent, as improvement runs are kept; its working copy is removed when it ends and it never
pushes a branch. Runs older than 90 days are removed, newest ten always kept.

## What a run may do

A run is launched by the same machinery as a task worker, with the helper role (`personas/l1.md`):
its own copy of the repository, the same sandbox, model routing, machine task cap, Stop and live
session. It is not a task, so nothing that reads tasks sees it: your task list and counts, the Weekly
review's look at finished tasks, task recovery and delivery checks stay as they are. Its result is its
final answer in a fixed shape, read by Altitude when it ends, as a review's is.

**Reports only, enforced.** A run reads the project, builds it and runs its commands in its own copy.
It has the network, as tasks do, so it can read public pages and fetch dependencies, and because the
engine itself needs it: the sandbox cannot let the engine reach its provider and block everything else.
It holds only what it needs:

- the engine sign-in Altitude gives every job, which it must have to work;
- no GitHub token, and none of your engine settings, plugins, connectors or skills folders: it starts
  restricted, as reviews do, with only the repository;
- no other sign-ins on this computer (SSH keys, the GitHub command's sign-in file, git's stored
  credentials): a separate change already under way hides them from every sandboxed job, and step 1
  merges after it;
- no `alt` verbs at all: its door answers only the engine sign-in some engines ask for, so it cannot
  push, open pull requests, file issues, start tasks, ask questions, message L3 or change agents.

What remains is the risk every task carries today, sharper because nobody watches a run: a page it
reads could talk it into sending the project's code, or the engine sign-in it holds, somewhere else.
It cannot act anywhere with your identity, and a credential kept somewhere unusual on this computer
is the remaining gap.

**Event text is data.** A run receives what fired it quoted as data after its brief, never as
instructions. In steps 1 to 4 that text comes from Altitude and your own tasks. GitHub text (step 5)
can be written by anyone, and an agent that holds your private code, reads a stranger's words and can
reach the internet can be talked into sending one to the other. So by default a GitHub event fires only
when its author has write access to the repository, and its run receives that author's text alone, not
comments by others; failing checks and releases come from the repository itself. Running a stranger's
text with no network beyond the engine's provider would need a new sandbox mode, so step 5 decides
whether to build one or keep strangers' text out.

| | Reports only (recommended default; the only mode until step 7) | May act (step 7) |
| --- | --- | --- |
| Reads and runs commands in its own copy | Yes | Yes |
| GitHub | Public pages; from step 5, a read-only reader | Your sign-in, as task owners have |
| Pull requests | No | May open one: it goes through review and checks and is **held for your merge** |
| Issues and tasks | No; it can say what it would file (step 6: as suggestions) | Listed for L3 to file on your say |
| Questions to you | Never | Never |

## What a run may spend

- **Your plan, never an API key.** Runs use the sign-in task workers use; nothing is billed per use.
- **A budget per project.** **Agents may use up to 2 hours a week** (30 min, 1, 2, 5 or 10 hours),
  counted from Monday 00:00 on this computer's clock. Every run's minutes count, Run now included. A run
  starts only if its whole limit fits what is left, so the budget is never overshot; otherwise it is
  skipped and says why. Each project has its own, so a project's agents cannot spend another's week.
- **A limit per run.** 10, 20 (default) or 60 minutes, counted from when its worker starts; at the limit
  Altitude stops it as Stop does. Time is the cap because it reads the same on every engine; tokens are
  recorded and shown, not capped, since most are cached rereads that count very differently against a
  plan.
- **Your work first.** Before a run starts, Altitude checks the engine routing chose for it: if that
  engine's weekly or five-hour window is at the reserve (90%, the improvement review's) the run is
  skipped and says so. A window Altitude cannot read also skips, as the Weekly review does: Altitude
  reads Codex's windows only once Codex has been used, so an agent pinned to Codex may skip
  ("Couldn't read Codex's plan use") until then.
- **Off is off.** Pause stops triggers at once, Stop ends a run, Retire ends the agent.

## When a run deserves your attention

Agents are quiet by design. Each agent's **Tell me in chat** is When it finds something (default),
Every run, or Never. A run's line in the project's chat is a system line: it never alerts a device and
never counts in Needs you. Couldn't finish and Stopped at its limit appear unless Tell me is Never;
skips never do. Needs you shows an agent only when it needs a decision from you: from step 4 when it has
paused itself, from step 6 its suggestions (uncounted, as improvement suggestions are), and from step 7
its held pull requests, as review cards.

## Where agents appear

| Surface | What it shows | Step |
| --- | --- | --- |
| **Work** (the project's task list) | A running or waiting run is a row in **Current** ("Agent run · PR #812 · 2 of 20 min"), opening the run. After Done this week, one row **Agents · 4** opens the Agents page; it lists no agents, so nothing is shown twice | 1 |
| **Project menu** | **Agents…**, always, so the page is reachable before the first agent exists | 1 |
| **Agents page** | `/projects/<name>/agents`: every agent with its triggers and latest news, the weekly budget, New agent, Retired, then **Built in** agents to turn on | 1 |
| **Agent page** | Status and Run now, recent runs, what it does and its triggers with Edit, Retire | 1 |
| **Run page** | Its live session while running, then its report, what fired it and the brief version it ran | 1 |
| **Project chat** | Quiet result lines; an offer card for every agent L3 would create or change, which only your press saves | 1 |
| **Monitor** | A running run among **Sessions**; **Agents** across projects with the latest result and what comes next | 3 |
| **Needs you** | Paused itself (step 4), suggestions (step 6), held pull requests (step 7) | 4, 6, 7 |
| **Project settings** | Nothing: agents are the project's work, not its configuration | – |

## L3 manages them on your say

L3 reads agents and runs at any time. In the chat turn answering your own message, which is recorded
as the authority as `alt improve run` works today, it may pause, resume, retire, run now and stop: each
only lowers or spends within the limits you set.

Creating an agent or changing what it does (its brief, triggers, model, limit or Tell me) is only ever
an **offer**: L3's reply carries a card Altitude draws from the stored definition, with the name, the
triggers, the brief (three lines and **Show all**) and **Create agent** or **Save change**, plus **Edit
first**, which opens the form filled in. Your press saves exactly what the card shows; L3 cannot save it
for you. So text a run reported, which L3 may read while answering you, can at worst draft an offer you
see and decline, never an agent that runs unattended. An offer you leave lapses after seven days.

## Built-in agents

Altitude ships ready-made agents, so a project's Agents page is never empty. Each is a brief Altitude
wrote, with a schedule and Tell me already chosen; one press of **Turn on** makes it one of your
agents, on, and from then it is yours to edit, pause or retire like any other. Retiring it returns it
to Built in. Step 1 ships three, all reports only:

| Built-in agent | Runs | What it does |
| --- | --- | --- |
| **Dependency check** | Weekdays at 07:00 | Looks for security fixes and major releases of what the project uses; tells you when it finds one |
| **Build health** | Mondays at 08:00 | Runs the project's documented checks on a clean copy and says what broke or got slower |
| **What landed** | Fridays at 17:00 | Sums up what merged this week and anything left unfinished; tells you every run |

Their briefs live in Altitude's repository and change only with a reviewed release. Turning one on
copies the brief as it is then, so a later release never rewrites an agent you have.

## The Weekly improvement review

By the operator's decision of 2026-10-19 the Weekly review becomes a built-in agent of Altitude's own
project (step 2), where it runs today. Its section in the project's settings is removed; everything it
held moves to the Agents page and the review's own agent page. Other projects are unchanged.

Its run stays its own kind: a tool-free reading of a code-built summary of conversations under the
terms you agreed to, far cheaper than an agent run, with its suggestion cards in chat and Needs you
unchanged. It does not count toward the agents' weekly budget or their one run at a time, and it never
appears in Work's Current rows; its running line in chat already shows it. Its Monday 08:00 claim is
already one case of the shared schedule rule (step 1). It has no Edit or Retire, because its brief and
model are Altitude's and proved by its eval. Turning it on and off stays yours alone, as it is today: L3's
`alt improve` verbs are unchanged, `alt agent` neither lists nor switches it, and L3's persona sends you to
its page. When agents gain suggestions
(step 6), they use its card.

**On the Agents page** (States 39 to 42):

- **Built in, until it is yours.** The first Built in row: **Weekly improvement review** over "Mondays at
  08:00 · Suggests fixes for friction that keeps coming back in this project's tasks" with **Turn on**.
  Turn on opens its terms sheet (PhoneWeeklyTerms: a bottom sheet at 390px with full-width buttons, Turn
  on above Cancel; State 40: centered at 1440px): **Turn on the Weekly improvement review for altitude?**, the sheet's existing
  text, **Cancel** (focused) and the primary **Turn on**. Cancel, Escape and the scrim change nothing.
  Turn on closes the sheet, agrees to those terms and turns it on; the row's Turn on shows its spinner
  until the list reads it as yours, and a refusal returns the button with "Not turned on: <reason>"
  under the row ("The terms changed while the sheet was open; read them again.").
- **Yours** once it was ever turned on or has ever run (an agreement given earlier through Get
  suggestions now counts). It is then the first row of your agents: **Weekly improvement review** over
  "Mondays at 08:00 · <news>" with a chevron opening its page. The news is "Reading 6 tasks" or "L3 is
  checking 3" with the running dot, "Paused" or "Paused: its terms changed" with the pause mark, else the
  latest ended run as "3 suggestions 2 d ago", "Nothing to suggest 9 d ago", "Couldn't finish 2 d ago" or
  "Stopped by you 1 d ago", else "Never run". Work's **Agents · N** counts it.

**Its page**, `/projects/altitude/agents/weekly` (DesktopWeekly, PhoneWeekly, States 43 to 50): the back
button names the project and the title is **Weekly improvement review**. Loading shows a spinner named
"Loading Weekly improvement review…"; a failed read says "Could not load the Weekly improvement review."
with **Retry**.

- **Status card.** On and idle: "Next: Monday, Oct 26" (", if 2 more tasks finish" while fewer than five
  have) with **Run now** and **Pause**, and Run now's line under it: "Reads the 6 tasks finished since
  Oct 19." When Run now cannot run it looks disabled and has no line under the card; its reason ("Run now
  needs 5 finished tasks since Oct 19; 3 so far." or "Claude's allowance is inside Altitude's reserve.")
  is its accessible description and shows under the card when pressed. Run now pressed shows "Starting…"
  in the button with Pause unavailable until the run is read; a refusal returns the buttons with its
  reason in red ("Another review just ended; ask again."). Running: "Reading 6 tasks" or "L3 is checking
  3" with a spinner and only **Stop**; Pause returns when the run ends, so Pause never changes a running
  line. Stop shows "Stopping…" until the end is read, or returns with "Not stopped. Try again." Paused:
  "Paused" with **Resume** and **Run now**. Pause and Resume change the line and buttons at once; a
  refusal returns them with "Not saved: <reason>" and **Retry**.
- **Terms.** Whenever the record's agreement is not to the current terms, whatever its switch says (paused
  by you before the terms changed, never switched, or turned off by the change), the line reads "Paused:
  its terms changed" and Resume and Run now open the terms sheet first; its Turn on agrees and completes
  the press. Once agreed, the line no longer mentions terms.
- **Recent runs**, newest first, the last ten, no Show older. "3 suggestions" (or "1 suggestion") with the
  found dot, its start "Oct 19, 08:00" and "4 min", over the suggestion titles joined by "; " in at most
  two lines; "Nothing to suggest" over "6 tasks read."; "Couldn't finish" with the failure dot over its
  reason; "Stopped by you" over "It was reading 7 tasks."; "Skipped" over "3 tasks had finished; it needs
  5." Each ended run opens its card or line in the project's chat (`/projects/altitude#improvement-<run>`),
  with a chevron; a skip has no page and no chevron. None: "No runs yet."
- **What it does.** "Finds friction that keeps coming back in this project's tasks and suggests fixes,
  as cards in chat you can make into tasks.", then muted what is sent ("Counts and short chat quotes ·
  Claude Sonnet 5.5 on your plan · capped per run, plus one L3 check") and, once a run has ended with a
  card or nothing to suggest, its totals: "4 runs · 7 suggestions · 3 became tasks · 2 dismissed ·
  Latest", Latest underlined and opening the latest card (absent when no run had one).

**In chat** (State 51): when its terms change, the quiet line "**Weekly improvement review** paused: its
terms changed. Open" replaces "Improvement suggestions are off until you agree to the new terms in
settings.", Open going to its page. A refusal that named the settings now says "Agree to the Weekly
improvement review's terms on the Agents page first.", true whether it is still in Built in or already
yours. Suggestion cards, ending lines and Needs you are
otherwise unchanged; ending lines gain the anchor Recent runs opens.

### How step 2 is built

- **Record.** Unchanged: the improvement record keeps the agreement, the Weekly switch (Pause and
  Resume are its off and on) and the runs. It is yours when the record has an agreement, a switch or a
  run; its terms are out of date whenever the agreement is not to the current version.
- **Views.** `improve.settings` is replaced by the review's list row (or Built in row) and its page,
  read from the same record. The page reuses the step-1 agent page's parts (status card, run rows,
  loading and failure) with the terms sheet as one gate on Turn on, Resume and Run now. `GET /api/agents/<project>` carries the row for Altitude's own project,
  `GET /api/agents/<project>/weekly` the page. Presses keep `POST /api/improve` (the switch with
  the terms version, Run now with it, Stop) from a paired device.
- **Removed.** The settings section (`ImprovementSettings.tsx`, its SPEC §3.15 bullet, the `#improvement`
  settings anchor and their tests); the sheet moves to the agent page.
- **Docs.** SPEC §3.15 and §3.17, ARCHITECTURE's improvement review and project agents, CLI's
  improvement suggestions, the L3 persona (its page, not settings; the switch is yours), the changelog.
- **Tests.** Yours and Built in from each record shape; terms out of date when paused before a change,
  never switched, or turned off by it, and cleared once agreed; every status line, Run now reason and
  Recent runs row; the switch and Run now through the sheet, stale terms refused; the API; and phone and desktop
  walkthroughs of States 39 to 51, the sheet and the page with fictional fixtures. No native behavior changes, so
  the Mac owes nothing new.

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
| Text from strangers | Simon Willison's "lethal trifecta": private data, untrusted content and a way out must not meet in one agent ([post](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/)); GitHub's agentic workflows sanitize event text and fire only for writers by default ([triggers](https://github.github.com/gh-aw/reference/triggers/)) | Event text is data; GitHub events fire for writers by default, and step 5 decides whether strangers' text may reach a run |
| Loops | Claude's GitHub Action ignores bot actors ([docs](https://code.claude.com/docs/en/github-actions)) | An event that comes from a run fires no agent |
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
run, one **Agents · 4** row at the end of Work and **Agents…** in the project menu open their page,
results come to chat, and Monitor carries the cross-project view. Agents are something a project does,
so they live where you look at what it is doing, in one row that never competes with current work.

**A′. Menu only (the design review's alternative).** The same, without the row in Work: agents are
reached from the project menu, chat lines and Monitor. Work stays purely about current work, at the
cost of agents being harder to find for someone who has never made one.

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
| What a run is | A sandboxed helper worker, kept under its agent rather than as a task, with your sign-ins withheld and no `alt` verbs | A task (reuses the task page, but every task reader would need to skip runs); or a tool-free model call like the improvement review: cheaper, cannot run a check |
| Act or report | Reports only until step 7, then a per-agent choice with pull requests held for your merge | Allow acting sooner |
| Spend | A weekly budget per project (2 h), a limit per run (20 min), the 90% reserve | Per-run limit only; or a token cap |
| Attention | Chat lines only when it found something or failed; Needs you only for decisions | Every run in chat; or a daily digest line |
| Where they live | With the project's work (A) | Menu only (A′); project settings (B) |
| How L3 creates or changes one | An offer card your press saves | L3 saves it on your say, with a confirming line |
| Weekly review | A built-in agent of Altitude's own project (your decision), keeping its own kind of run | – |
| Built-in agents | Dependency check, Build health and What landed, off until turned on | None |
| Order of steps | Schedule, Run now and built-ins first; the Weekly review; Monitor; Altitude events; GitHub; suggestions; acting | Events in step 1 |

## Screens

Boards in this folder at 390×844 (phone) and 1440×900 (desktop), written by `gen.py`: `DesktopWork`,
`PhoneWork` (the project's task list), `DesktopAgents`, `PhoneAgents` (all agents), `DesktopAgent`,
`PhoneAgent` (one agent), `DesktopRun`, `PhoneRun` (one run), `DesktopWeekly`, `PhoneWeekly`, `PhoneWeeklyTerms` (the
Weekly review as a built-in agent, its terms sheet), `DesktopEdit`, `PhoneEdit` (the form),
`DesktopChat`, `PhoneChat`, `DesktopMonitor`, `PhoneMonitor`, `DesktopNeeds`, `PhoneNeeds` (Needs you with an agent
that paused itself), `PhoneRetire`, and every state at both
widths: `States` and `PhoneStates`. The boards draw the full design; the steps say when each part
arrives.

### Work

Work keeps answering "what is happening in this project?". A waiting or running run is a row in
**Current** with the ordinary dot and chevron: the agent's name, then "Agent run · PR #812 · 2 of 20
min", "Agent run · waiting for a free task slot" or "Agent run · waiting for Review each merge"; it opens
the run's page. Runs are not tasks: the header's "N tasks in flight" and Done this week leave them out,
and a finished run leaves Work, its history being on the agent's page. After Done this week, one row
**Agents · 4** with a chevron opens the Agents page; it names no agents, so nothing in Current is said
twice. With no agents the row is absent, and the project menu's **Agents…**, after **Project
settings…**, is always there.

### All agents

`/projects/<name>/agents`, with **‹ harbor** back to the project and the title **Agents** over "Each
one runs a brief when its trigger fires, and tells you what it found." One card: a
whole-row link per agent with a chevron, the name in semibold and one muted line, its triggers then the
latest news, with a cue before it when one applies (a pulsing dot running, a pause mark paused):

- "Weekdays at 07:00 · Found something 2 h ago"
- "When a pull request merges · Running, 2 of 20 min"
- "Mondays at 08:00 · Waiting for Review each merge"
- "Fridays at 17:00 · Paused"
- "When you run it · Never run"

Several triggers read "Weekdays at 07:00 and when a pull request merges"; more than two read "3
triggers". Schedules read "Every day", "Weekdays", "Weekends", "Mondays", "Mon, Wed, Fri", with the
time in the machine's clock and its zone when the browser's differs ("07:00 Istanbul"). Below the
rows, **Agents may use up to** with a select (30 min · 1 h · 2 h · 5 h · 10 h a week) and "1 h 10 min
used this week", then a bordered **New agent**; with no agents of your own the card holds only these.
A muted "2 retired" link opens the retired agents as rows ("Retired Oct 19 · 23 runs"), each opening
its read-only page.

**Built in** follows, over "Ready to turn on; each becomes yours to change.": one row per built-in
agent not yet turned on, its name in semibold, "Mondays at 08:00 · Runs the project's checks on a clean
copy and says what broke" muted beneath, and a bordered **Turn on**. Pressed, the button spins until
the agent reads as on, then the row leaves Built in and appears among your agents; refused, the button
returns with the reason under the line ("Not turned on: the agents changed in another window."). With every
built-in turned on, the group is absent.

### One agent

`/projects/<name>/agents/<id>`, **‹ harbor** back, the agent's name as the title. In order:

1. **Status card.** On: "Next: tomorrow at 07:00" (a schedule) or "Runs when a pull request merges"
   (events only) with **Run now** and **Pause**. Running: a spinner, "Running · 4 of 20 min", an
   underlined **Open** to the run, **Stop** and **Pause** (Pause lets the run finish and fires nothing
   after it). Waiting: "Waiting for a free task slot" or "Waiting for Review each merge" with **Stop**.
   Paused: a pause mark and "Paused" (or "Paused itself after 3 failed runs"), with **Resume** and
   **Run now**. New: "Not on yet" with **Run now** and **Turn on**. Pause, Resume and Turn on answer at
   once; the line changes with them.
2. **Recent runs.** The last ten, newest first: the outcome in semibold, date and time, minutes and
   tokens, and its line beneath, opening the run's page. An event run's line starts with what fired it
   ("For PR #810, #811 and #812: …"). Skipped runs show their reason and have no page or chevron. Dots:
   accent for found, danger for couldn't finish and limit, muted otherwise. **Show older** loads ten
   more. None: "No runs yet."
3. **What it does.** The brief clamped to three lines with **Show all**, then "Weekdays at 07:00 · Tells
   you when it finds something · Default model · Stops after 20 min", and a bordered **Edit**.
4. **Retire agent**: "It won't run again. Past runs stay readable." and an outlined red **Retire…**.

### One run

`/projects/<name>/agents/<id>/runs/<n>`, **‹ Dependency check** back, the title "Run · Today 07:00".
A line says what fired it ("On its weekday schedule", "Run now by you", "For PR #812"), the model, and
minutes and tokens once it has ended. While running: "Running · 4 of 20 min" with **Stop**, then the
live session as a task's (the same transcript component, read only). Ended: the outcome in semibold
with its dot, then the full report as text (its one-line summary is what lists and chat show); under
it, folded, **Brief it ran**, the version as it was then, and **Session**, the transcript. A run has no
message box, review boxes, terminal or questions.

### The form

**Edit** opens `/projects/<name>/agents/<id>/edit`; **New agent** opens `/projects/<name>/agents/new`,
empty (ready-made briefs are the built-in agents). Fields:
**Name**; **What it does** (hint "Write it as you would brief a task. It can use anything in the
repository."); **Runs when**; **Tell me in chat** (When it finds something · Every run · Never;
segmented on desktop, stacked on phone); **Model** (the model button of §3.6.1); **Each run stops
after** (10 min · 20 min · 1 hour). **Save** or **Create agent**, and **Cancel**. A new agent is saved
paused and its page opens, with Run now and Turn on.

**Runs when** is a bordered list, one trigger per row with an icon (a clock for a schedule, a bolt for
an event), its words and a Remove button (44px on phone). A schedule row opens to seven day toggles
(M T W T F S S, 40px targets on phone, on one line) and a time; with no day on, the row says "Pick at
least one day." and Save waits for one. An event row reads "A pull request merges" over "Its run reads
that pull request.". **+ Add a trigger** opens a menu: On a schedule; In Altitude: A pull request
merges · A task finishes · An incident is filed; On GitHub: An issue is opened · Checks fail on the
main branch · A release is published. Until events arrive (step 4) it is **+ Add a schedule**, with no
menu. With an event trigger, the hint "Events that arrive during a run go to its next run together."
follows the list. Removing the last trigger leaves "Only when you run it". New agents start with a
weekday 07:00 schedule.

Without a name or brief, Create agent looks unavailable and, pressed, focuses the first empty field
with "Give it a name and a brief.". Leaving with unsaved changes asks "Discard your changes?" with
**Keep editing** (focused) and **Discard**. A change to a running agent applies from its next run.

Retire opens a dialog: **Retire Dependency check?**, "It won't run again, from its triggers or Run
now. Its past runs stay readable under Retired.", **Cancel** (first, focused) and a red **Retire**. A
retired agent's page has its title, "Retired Oct 19", its runs and its definition as text.

### In chat

Result lines are system lines (§3.4) at the time the run ended, one line on desktop, led by the
agent's name, with **Open**:

- "**Dependency check** found something: 2 packages have security fixes." accent dot
- "**Review each merge** found something on PR #809: the upload limit moved without its test."
- "**Dependency check** found nothing." only with Every run
- "**Dependency check** couldn't finish: `make audit` is not a target." danger dot, unless Never
- "**Dependency check** stopped at its 20-minute limit." danger dot, unless Never
- "**Dependency check** stopped by you." only with Every run
- "**Dependency check** paused itself: 3 runs in a row couldn't finish." danger dot, always

The text after the name is at most 120 characters from the run's own summary, made plain as improvement
lines are; the report itself is one press away.

When you ask L3 for an agent or a change, its short reply carries the **offer card** (see L3 manages
them): a bordered card under the reply with "New agent" or "Change to Dependency check" in muted text,
the name in semibold, the triggers and Tell me on one muted line, the brief clamped to three lines with
**Show all**, and **Create agent** (primary) or **Save change**, then **Edit first**. A change shows
only what changes ("Runs at 06:00 instead of 07:00"; a new brief in full). Pressed: the button spins,
then the card reads "Created · Open" or "Saved · Open" in place. Refused (the agent changed since):
"Not saved: Dependency check changed since this offer." and the card's buttons go. Lapsed after seven
days: "This offer lapsed." with no buttons. Pause, resume, retire and stop through L3 add one quiet
line from the record: "Paused Dependency check · Resume", "Retired Dependency check".

### Monitor (step 3)

Monitor answers "what is Altitude doing, and what will it do next?" across projects. Agents join it
in two places, at the end of the page, and change nothing else on it: the order stays Altitude update,
Seats, Routing now, Sessions, then Agents (`DesktopMonitor`, `PhoneMonitor` draw the whole page).

**Sessions.** A running run is a row among the L3 and L2 sessions, after its project's tasks, drawn
as they are: the chip **Agent**, the title "<project> / <agent name>" (one line, truncated with an
ellipsis, the full title on hover), the age of its latest reading right-aligned ("just now", "2 min
ago"), then one muted line "<engine> · <model> · context 6% · 4 of 20 min" and the context meter.
Before the first reading of its context the line says "context unknown", with no age and no meter,
as a session's does. A reading older than five minutes is stale as a running task's is: the meter
at half opacity and "Stale" for screen readers. The row is a reading, like the other session rows,
not a link; its agent's row below opens the agent. The Sessions count includes it. A waiting run
is not a session: it shows only in Agents. The Weekly improvement review's run is one engine call
rather than a session, so it never appears here; its row below says Running.

**Agents (N).** After Sessions, a section with the heading "Agents (N)" lists, across projects,
every agent that is not retired and is any of: on with a schedule; waiting or running; paused itself
(until you resume or retire it); or had a run in the last seven days. In Altitude's own project the
Weekly improvement review is listed once it is yours, on the same terms. An agent with no schedule
that has not run for seven days, or one you paused that has not run for seven days, drops off. Projects come in Monitor's project order, each
project's agents in its Agents page's order (the Weekly review first). No such agent, no section and
no heading. One card holds the rows, separated by hairlines; each row is a whole-row link to the
agent's page with a chevron at the right, centred on the row:

- the title "<project> / <agent name>" in semibold, wrapping when long, named as its session row
  names it;
- what comes next, the first that applies, in the order the agent page's status uses:

  | When | Line | Agent page's status |
  | --- | --- | --- |
  | A run is running | pulsing dot, "Running" | Running · 4 of 20 min |
  | A run is waiting | "Waiting" | Waiting for a free task slot |
  | Not on yet (you ran it by hand) | "Not on yet" | Not on yet |
  | Paused itself | pause mark, "Paused itself" | Paused itself after 3 runs in a row couldn't finish |
  | Paused by you | pause mark, "Paused" | Paused |
  | Weekly review, terms out of date | pause mark, "Paused: its terms changed" | the same |
  | Its project's weekly budget is used, and it has a schedule | "Waits until Monday" | Next: … (the Agents page's budget line says it) |
  | It has a schedule | "Next tomorrow at 07:00", "Next today at 17:00", "Next Friday at 07:00" | Next: tomorrow at 07:00 |
  | No schedule | "Only when you run it" | Only when you run it |

  Times are the computer's, with its zone after the time when the browser is in another ("07:00
  Istanbul"), as on the Agents page. The Monitor's lines are these short forms, not the page's
  sentences;
- "Last: <outcome> · <when> · <minutes> min · <tokens> tokens", muted and wrapping: the latest run
  that ran (as the Agents page's news counts it, so skips never replace a result), its parts the run
  lacks left out ("Last: Found something · 2 h ago · 6 min · 3.9M tokens", "Last: Couldn't finish ·
  yesterday · 1 min", "Last: 3 suggestions · 2 d ago · 4 min · 1.2M tokens"). Skips stay in the
  agent page's Recent runs. With none: "No runs yet."

On desktop, what comes next sits right-aligned on the title's first line and never wraps; "Last:"
is the line beneath. On the phone every row has the same three parts stacked: the title, then what
comes next in the secondary tone, then "Last:" muted, so no row's shape depends on how long its
name is.

Monitor reads both sections in its one read, so they share its skeleton while loading and its
"Could not read the monitor." with Retry; there is no separate loading or error for agents. The page
keeps its refresh, so a run that starts or ends moves between Sessions and Agents on the next read.
A running agent shows in both places on purpose: the session row says how its session is doing, the
agent row what the agent does next.

### How step 3 is built

- **Readings.** The project tick (every 30 seconds) already looks at each running run's worker. It
  also reads the run's session through the passive reader a running task's uses: `usage.capture`
  takes the folder its cursor and the job live in, which defaults to the task's, and the tick passes
  the run's folder and a run-shaped record as `project_agents.session` builds one. The tick keeps
  `context: {percent, at}` on the run, saved under the project lock only when the reading changed.
  The run's final tokens stay as `reconcile` records them at its end.
- **Views.** `monitor.sessions()` adds a row of kind `agent` per running run: project, agent id and
  name, run number, engine, model, context and its reading time, limit and minutes so far.
  `monitor.agents()` returns the Agents rows: project, id, name, what comes next and its cue, and the
  latest run that ran (outcome, end time, minutes, tokens). It reuses `project_agents` and `improve`
  helpers (`_active`, the schedule's next time, the budget, the news's latest run), and the
  agent page's status order. `GET /api/monitor` gains `agents` and the computer's clock. A read writes
  nothing; the Weekly review's row is read only where its record exists, so no other project asks
  git anything.
- **Removed.** The unused `agents` placeholder in the web's Monitor schema.
- **Docs.** SPEC §3.14 (the Agent session row and the Agents section), ARCHITECTURE's Monitor and
  project agents, the changelog.
- **Tests.** Which agents are listed (on with or without a schedule, waiting, running, paused itself
  past seven days, paused by you, ran within and beyond seven days, retired, the Weekly review yours
  or not, its terms changed); the order of what comes next when several apply (paused with a run
  running, new with Run now pending, paused with the budget used); every line including the budget
  and the zone; the latest line for each outcome, skips never shown, missing minutes or tokens and no
  runs; the session row with and without a context reading and stale; the reading kept on the run
  and saved only when it changed; the API; and phone and desktop walkthroughs of the Monitor states
  below with fictional fixtures. No native behavior changes, so the Mac owes nothing new.

### Events (step 4)

An agent can start when something happens in the project's Altitude, not only on a clock. The
triggers are a pull request merging, a task finishing and an incident being filed. Nothing else on
the page moves: an event agent is listed, run, paused and retired as a scheduled one is.

**What fires.** Altitude records each of these events once, in the project's own log, at the moment it happens:

| Trigger | Fires when | Its run reads | Short form |
| --- | --- | --- | --- |
| **A pull request merges** | Altitude merges a task's pull request, whoever started the landing | The number, title and link, the task it delivered, and the merged change in its own copy | "PR #812" |
| **A task finishes** | A task is marked done, by its owner, L3, Altitude or you (a rejected task does not fire) | The task's title and the summary of its final report | the task's title in quotes, "“Fix the login timeout”" |
| **An incident is filed** | Altitude, L3 or a task's owner files an incident in this project | Its title, tags and the incident text | "incident “Disk nearly full”" |

A run receives this text quoted as data after its brief, as with every event. Each piece is bounded:
a title to 200 characters, a report summary to 2,000 and an incident's text to 4,000. Merges made
on GitHub by hand, outside Altitude, do not fire; that is step 5's GitHub reader. Events fire only
agents that were on when the event happened. A paused agent ignores them, and so does a new one not
yet on; resuming never replays what happened while it was paused. While the project's weekly budget
cannot fit an agent's run limit, its events are passed over the same way, so a spent week never
fills Recent runs with skips; the status and Monitor already say "Waits until Monday".

**No loops.** Every event records where it came from, and one that comes from an agent run never
fires an agent or counts as the latest for Run now. In this step runs cannot merge, finish tasks or
file incidents. The remaining path is Altitude's own work for a run: when starting, stopping or
ending a run fails anywhere, in the launch, its copy, its sign-in or the agents' tick, the fault is
filed as an incident like any other and is recorded as coming from that run. Steps 6 and 7 mark the
tasks and pull requests a run proposes or opens the same way. Event runs count toward pausing
itself, so even a fault outside this rule stops after three runs.

**Together.** An event finds the agent in one of three states:

- With no run waiting or running, the event starts a waiting run.
  Such runs count toward pausing itself as scheduled runs do, so three event runs in a row that
  couldn't finish pause the agent.
- With a run waiting, the event joins that run.
- With a run running, the event waits for the next run.

When the running run ends, the agent's next run starts at once with every event that waited, so a
burst of five merges during a twenty-minute run costs one more run, not five. Pausing or retiring
the agent drops the events that waited. A run skipped at its start (the budget, the reserve, six
hours without a slot) is skipped with its events, and its line names them. A schedule that comes
due while events wait still fires as before; the events join its run.

**Run now** on an agent with an event trigger replays the latest matching event, without using it
up. Its run page says "Run now by you · For PR #812, the latest", and its line in Recent runs starts
"For PR #812: ". With none yet, the run starts without an event and says so in its input and on its
run page: "Run now by you · No pull request has merged yet", "… No task has finished yet", "… No
incident has been filed yet". An agent with several event triggers replays the latest of any of
them. For an agent whose triggers are all events, before any has happened, the status card says
so under its line, muted: "No pull request has merged yet, so Run now runs without one."

**The form.** **+ Add a trigger** replaces + Add a schedule. It opens a menu under the button:

- **On a schedule**;
- a muted "In Altitude" group label, then **A pull request merges**, **A task finishes** and
  **An incident is filed**.

An event already in the list is left out of the menu. Choosing one adds its row after the schedules,
in the menu's order, and focus moves to that row's Remove button. Escape or a press outside closes
the menu and returns focus to + Add a trigger. It is a menu under the button at both widths, as the
project menu is, its items at least 44px tall on the phone. The menu's "On GitHub" group arrives with step 5;
until then it is absent, not greyed out. Each event row has the bolt icon, its words, one muted line
beneath, and Remove (44px on the phone):

- "A pull request merges" over "Its run reads that pull request."
- "A task finishes" over "Its run reads that task's report."
- "An incident is filed" over "Its run reads that incident."

With any event row, the hint "Events that arrive during a run go to its next run together." follows
the list.

**Words.** A trigger reads "When a pull request merges", "When a task finishes" or "When an
incident is filed". The rules for combining them are unchanged:

- Two triggers join with "and": "Weekdays at 07:00 and when a pull request merges". Two events
  join with "or": "When a pull request merges or a task finishes".
- More than two triggers read "3 triggers".

These words appear in four places:

- the Agents page's line;
- What it does;
- the offer card;
- Monitor's next column, for an agent with no schedule ("When a pull request merges", "2 triggers"),
  in place of "Only when you run it".

An offered change says "Also runs when a task finishes" or "No longer runs when a pull request
merges".

**Status card.** An agent that is on with events and no schedule reads "Runs when a pull request
merges" (or "Runs when a pull request merges or a task finishes", or "Runs when a pull request
merges, a task finishes or an incident is filed") with Run now and Pause. With a schedule too, it
keeps "Next: tomorrow at 07:00", since the definition below names the events. While a run is
running and events wait, a muted line under the status line, before the buttons, names them: "PR
#813 waits for the next run.", "PR #813 and #814 wait for the next run.", "4 events wait for the next
run." On the phone the buttons sit beneath, as the status card already stacks there.

**Naming the events.** One rule names a run's events everywhere:

- Up to three of one kind are listed: "PR #810, #811 and #812", "“Fix the login timeout”".
- Otherwise the first is named and the rest counted: "PR #810 and 3 more", "“Fix the login
  timeout” and 2 more".

The name appears in several places:

- the run's line in Recent runs ("For PR #810, #811 and #812: …", as drawn);
- Work's run row ("Agent run · PR #812 · 2 of 20 min");
- the chat line ("found something on PR #809: …");
- a skip line ("For PR #812 · No free slot for 6 hours").

A run page whose run took more than one event lists each event under its line, muted, one per line:

- "PR #811 merged · Raise the retry limit"
- "Task finished · Fix the login timeout"
- "Incident filed · Disk nearly full"

A schedule run that events joined reads "On its weekday schedule · For PR #812".

**Needs you.** When an agent pauses itself, a card waits in Needs you under its project, after that
project's questions. The card reads:

- the kind label "Agent paused itself", in the danger tone, with its age;
- the agent's name as the card's title, a link to its page with a chevron after it, so it reads
  as the way in;
- "3 runs in a row couldn't finish. The last: `make audit` is not a target." The last reason is
  taken from that run's line, with "stopped at its 20-minute limit" for a limit stop;
- a bordered **Resume**, and the underlined link "Open the last run", so you can read what broke
  before resuming.

The card counts in the Needs you summary ("1 question · 1 paused agent in harbor") and in the badge.
Like a stopped task or a fault, it never alerts a device.

Pressing Resume shows a spinner in the button until the agent reads as on; the card then leaves.
Refused (the agent was retired in another window meanwhile), the button returns with "Not resumed:
Flaky tests is retired." beneath, and the card leaves on the next read. The card also leaves when the agent
is edited (saving a change makes it paused by you, so the status reads "Paused") or retired. Run now
on its page does not clear it.

### How step 4 is built

- **Sources.** The project's `events.log`, which incidents already write to, is the one event
  source. `land` writes `merged` (number, title, link, task) when it records a task's merged head,
  and only when that head is new. This happens in Altitude for an owner's landing and in your own
  command for yours. `tasks` writes `task-done` (slug, title) when a task enters done. `incidents`
  already writes `incident` (id, title, tags), now right after the incident file, before its index
  rows. Each line is appended before the record that says it happened is saved, so a retry after a
  crash writes it again and the reader drops the duplicate by its key; nothing is lost between the
  two writes. Each line carries `origin` when a run caused it: `incidents.system_fault` reads it
  from the context `project_agents` sets around its own work for a run (start, stop, end, the
  agents' tick), so a fault filed by any shared code underneath is marked too.
- **Reading.** The project tick reads the log's new complete lines from an offset kept in
  `project-agents.json`, with the log's file identity. A project with no offset starts at the log's
  end, so history never fires; a log that shrank or is another file (restored, re-added) starts again
  at its end, and the daemon log says so.
  For each event and each agent on since before it, the tick does one of three things: it creates a
  waiting run, adds the event to the agent's waiting run, or adds it to the agent's `pending`. It
  saves the offset and those changes in one write under the project lock. So a restart re-reads only
  what was not saved, and each event reaches each agent once, keyed by the agent and the event (PR
  number and head, task slug, incident id), checked against the keys on its runs and `pending`.
  While Altitude waits to activate a change, the tick does not read, as with claims. Lines with an
  `origin` are passed over, and so are events for an agent whose limit the week's budget cannot
  fit.
- **Definition.** It gains `events`, a list from `merged`, `done` and `incident`, each at most once.
  Agents saved before this step have none. `definition()`, `triggers()`, `_changes()`, offers and
  `alt agent offer` accept and say it; the built-in agents keep their schedules.
- **Runs.** A run gains `events`, each event's quoted fields as recorded. When a run ends, `reconcile`
  starts the agent's next run with its `pending` events if the agent is still on; Pause and Retire
  clear `pending`. `_end` counts event runs, and schedule runs events joined, toward pausing itself;
  Run now and skips still do not count. Run now finds the latest matching event without an `origin`
  by reading the log back from its end. The run
  template quotes the events as data after the brief; a task's report summary and an incident's text
  are read when the run is created and kept on it, bounded.
- **Needs you.** `T.decisions`' queue gains a row of kind `agent` for each agent paused itself, with
  the agent's id and name and the last run's number and reason, and no `alert_key`, so it never
  alerts. Rows without a slug are no longer taken for operator steps: `isStep` and its callers check
  `kind === "step"`. The card is its own component beside `StepCard`, grouped by `agent:<id>`, its
  title opening the agent page; `attentionSummary` counts "paused agent". Resume presses the
  existing `resume` change. Saving a
  definition on an agent paused itself marks it paused by you.
- **Removed.** The step-1 **+ Add a schedule** button and its branch in the form and the boards.
- **Docs.** SPEC (the form's triggers, the agent and run pages, Needs you), ARCHITECTURE's project
  agents (events, the offset, no loops), CLI (`events` in a definition), the changelog.
- **Tests.**
  - Each source writes its line: a merge, a task done by each route, an incident, and none for a
    rejected task; a crash between the line and its record, then the retry, reaches each agent once.
  - The tick reads from the offset, does not fire history after the upgrade, starts again at the end
    of a shrunk or replaced log, and loses nothing and repeats nothing when the save fails and the
    tick runs again.
  - Paused, new and retired agents ignore events, and an agent turned on later ignores earlier ones.
  - Joining a waiting run, pending events during a run and the next run starting with them, a
    schedule run joined, and skips carrying their events.
  - An incident filed during a run's start, stop or end, from shared code too, never fires and is
    never Run now's latest; three failing event runs pause the agent; a spent budget passes events
    over.
  - Run now with and without a latest event.
  - The input quoted and bounded, and every words rule and name form.
  - The Needs you row, its count beside an operator step's in the same project, no alert, Resume,
    edit and retire clearing it.
  - The API and the offer.
  - Phone and desktop walkthroughs of the states below with fictional fixtures.
  - No native behavior changes, so the Mac owes nothing new.

### States

| Surface | State | What appears |
| --- | --- | --- |
| Agents page | Loading / read failed | A spinner named Loading agents…; "Could not load agents." and Retry |
| Agents page | No agents of your own | The line, the budget select and **New agent**, then every built-in agent |
| Built in | Turning on / refused / Altitude's project | A spinner in Turn on; the button returns with its reason; the Weekly review listed first, its Turn on opening its terms (step 2, [above](#the-weekly-improvement-review)) |
| Budget | Saving / refused / spent | The choice shows at once with Saving…; refused: back to the saved value, "Not saved: <reason>" and Retry; spent: "2 h used this week · agents wait until Monday" in amber |
| Status | New / Run now pending / refused | "Not on yet" with Run now and Turn on; "Starting…" until the run is read; refused: Run now again with its reason ("Only 12 min of this week's budget is left.") |
| Status | Waiting / running / Stop pressed | "Waiting for a free task slot" or "Waiting for Review each merge" with Stop; Running with Stop and Pause; "Stopping…" until the end is read |
| Status | Pause pending / refused / paused itself | The line and button change at once; refused: they return with "Not saved: <reason>" and Retry; paused itself: its reason in the line |
| Runs | None / several events / late / skipped | "No runs yet."; one run's line names every event it took; "Oct 13, 07:00, ran at 09:14"; "No free slot for 6 hours" with no chevron |
| Run page | Running / ended / couldn't finish / loading / read failed | The live session and Stop; the report with Brief it ran and Session folded; the reason in the report's place; a spinner; "Could not load this run." and Retry |
| Form | Saving / refused / unsaved / incomplete / no day | Save shows a spinner; refused keeps the edit with "Not saved: <reason>" and Retry; the Discard dialog; "Give it a name and a brief."; "Pick at least one day." |
| Triggers | Add / none | The trigger menu (from step 4; before it, + Add a schedule); "Only when you run it" |
| Triggers | Menu / event rows / every event added | On a schedule, then In Altitude's three events, Escape returning focus; each event row with its line and the hint after the list; the menu holds only On a schedule |
| Status | Events only / none yet / events waiting | "Runs when a pull request merges"; "No pull request has merged yet, so Run now runs without one."; "PR #813 waits for the next run." under Running |
| Runs | Several events / Run now replay / none yet / skipped with events | "For PR #810, #811 and #812: …"; the run page's "Run now by you · For PR #812, the latest" and "… No pull request has merged yet"; "For PR #812 · No free slot for 6 hours" |
| Run page | Several events | One muted line per event under what fired it |
| Needs you | Paused itself / resuming / refused / cleared | The card with its title link, Resume and Open the last run, counted, no alert; a spinner in Resume; "Not resumed: Flaky tests is retired."; gone after Resume, a saved edit or Retire |
| Offer | Shown / saving / created / refused / lapsed | The card; a spinner in the button; "Created · Open"; "Not saved: …"; "This offer lapsed." |
| Retire | Pending / refused | "Retiring…" with Cancel unavailable; refused: the reason under the buttons, Cancel works |
| Page | Retired | Title, "Retired Oct 19", runs, definition as text |
| Work | Run waiting / running / no agents | Rows in Current; the Agents row absent, Agents… in the menu |
| Chat | Each ending | The lines above |
| Monitor | Loading / read failed | Monitor's skeleton; "Could not read the monitor." and Retry, for both sections |
| Monitor | Run running / no reading yet / stale | The Agent session row with its meter; "context unknown", no age and no meter; past five minutes the meter at half opacity |
| Monitor | No agents / only retired or quiet ones | No Agents section and no heading |
| Monitor | Each next / each latest | The lines [above](#monitor-step-3), the first that applies; "No runs yet." when none ran |

## Steps

Each step is its own held pull request and comes back to you before it is built.

1. **Agents on a schedule or run by hand, reporting only (this task's increment).** The three built-in
   agents with Turn on; the record and its
   versions; schedule triggers on the shared clock with **+ Add a schedule**; Run now; runs under their
   agent with the run page, the previous report as input, the restricted launch and no `alt` verbs; the
   weekly budget, per-run limit and reserve; pausing itself after three failures, said in chat; the
   Agents page, agent page, form and retire; Work's run rows and Agents row, the menu's Agents…; chat
   lines; L3's verbs and offer cards; docs and tests. It merges after the separate change that hides
   sign-ins from sandboxed jobs.
2. **The Weekly review as a built-in agent** of Altitude's own project, its settings section moved to
   its agent page.
3. **Monitor.** Runs among Sessions and the Agents section.
4. **Altitude events.** A pull request merges, a task finishes, an incident is filed, with the trigger
   menu; events that wait and go together; Run now on the latest event; the paused-itself item in Needs
   you.
5. **GitHub.** One read-only GitHub reader per project, run by Altitude with your sign-in and never
   inside a run, polling for opened issues, failing checks on the main branch and releases; the same
   reader gives runs read-only access to private issues and pull requests. Events fire only for authors
   with write access, and whether strangers' text can ever reach a run is decided here. It is a new
   security boundary, so it gets its own review.
6. **Suggestions.** A run may propose up to three tasks, shown on the improvement suggestion card in
   chat and under Suggestions in Needs you, with Make a task and Dismiss; tasks made from one show
   "from <agent>" in Work.
7. **Acting.** A per-agent choice to open pull requests, each held for your merge as a review card, and
   to list issues for L3 to file.

## How the first step is built

- **Record.** `<project>/project-agents.json` holds each agent (name, brief, triggers, model and effort,
  tell, limit, state, the reason it paused), the weekly budget, every change with the brief as it was
  (who, when, source message or press), per trigger the last claimed due time, and the runs (id,
  version, trigger key, state, times, usage, report line). Each run's report, prompt and session
  folder live in `project-agents/<id>/<run>/`. The code says "project agent" throughout, never a bare
  "agent", which already names a task's worker session and the engines' own agent definitions.
- **Built-ins.** `templates/project-agents/<key>.md` holds each built-in agent: a short header (name,
  schedule, Tell me) over its brief. Turn on copies it into the record with `builtin: <key>`, which
  only hides that key from Built in while the agent is not retired.
- **Clock.** One function returns the latest due time not yet claimed; `improve.weekly` uses it in place
  of its own Monday arithmetic, which is removed. The project tick claims due triggers durably, then
  creates their runs; while Altitude waits to activate a change, a claim waits for a later tick, as
  Weekly's does. A claim for an agent with a run waiting or running is recorded as a skip.
- **Run.** A worker job of its own kind (`project-agent-run`), launched by the dispatcher's worker path
  with the run template (`templates/project-agent-run.md`: the brief, the trigger, the previous report,
  the result shape, the limit), the L1 persona, the agent's model pin, Claude restricted as reviews run
  and nothing of the operator's engine folder linked, and no GitHub token. It counts toward the
  machine's task cap and starts only in a free slot, tasks first. Its door answers only the engine
  sign-in. Dispatch checks the budget against the run's whole limit and the routed engine's reserve
  before launch (`route.improvement_reserve` becomes one engine-parameterised check, used by both). The
  daemon stops a run at its limit; its final answer is read and validated as its report.
- **Chat.** The result line is written by Altitude from the run record and posted as Tell me allows.
  `alt agent offer` stores a definition on L3's reply; the press, a paired-device request with an id,
  saves exactly that definition.
- **Interfaces.** `alt agent list|show|offer|pause|resume|retire|run|stop` on L3's socket;
  `alt agent new|set` for the operator's own CLI only; `GET /api/agents/<project>` and `POST /api/agents`
  presses with a request id from a paired device; the project view's run rows and Agents row.
- **Docs.** ARCHITECTURE (a Project agents section), CLI, OPERATIONS (configuring agents), SPEC (Work,
  the project menu, a new Agents section, chat lines and the offer card), the L3 persona's verbs, the
  changelog.
- **Tests.** The schedule rule (due, missed, turned on late, paused, waiting, running, activation wait)
  with Weekly's cases unchanged; budget fit and reserve skips on each engine, unknown readings included;
  the wait order and six-hour skip; run creation and its inputs; the launch's restricted settings, empty
  linked folder and missing GitHub token; the door refusing every request but sign-in; the limit stop;
  pausing after three triggered failures and not after Run now; versions; built-ins turned on, hidden
  while active and back after Retire; that task lists, counts, the
  Weekly review's finished tasks and recovery never see a run; offers (saved only by the press, refused
  when stale, lapsed); L3 authority; the API; and phone and desktop walkthroughs of every step-1 state
  with fictional fixtures; engine calls stay at the fixture seam.

### Mac verification

The schedule lives in the service's own timer, so no launchd job is added. The Mac still owes: a run
launches under Seatbelt with no GitHub token in its job's input and nothing of the operator's engine
folder linked, its door refuses everything but sign-in, and the limit stop ends its job. These are
listed in the pull request for L3 to file.
