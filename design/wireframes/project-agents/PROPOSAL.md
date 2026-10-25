# Routines: proposal

Status: approved by the operator on 2026-10-19 with one addition: Altitude ships built-in routines,
including the Weekly improvement review, that you turn on with one press. Steps 1 to 5 are built
([The Weekly improvement review](#the-weekly-improvement-review), [Monitor](#monitor-step-3), [Events you
send](#events-you-send-step-4) and [Suggestions](#suggestions-step-5)); the rollout converged after step 5,
and step 6, acting, is [Routines that act](ACTING.md) (#1109). How each step is built lives in `docs/ARCHITECTURE.md` and SPEC §3.17. In
the interface the feature is called **Routines**; one of them is "a routine", and one time it works is "a run". This
replaces the earlier scheduled-only proposal: a schedule is one kind of trigger, not the definition.

## What it is for

Some project work is the same question asked again and again, on a clock or after something happens:
did a dependency get a security fix, does each merged pull request leave its change tested, what did
this week's incidents have in common, does the suite still pass on a clean checkout. Today each waits
for you to remember to ask in chat. Altitude itself runs one recurring routine, the Weekly improvement
review, in a shape nobody else can reuse.

## The shape

An **routine** is a brief in your own words, one or more **triggers**, and limits. When a trigger fires,
Altitude starts a **run**: a sandboxed worker that does the brief and ends with a **report**, one line
and a full answer. You watch a run live as you watch a task, and you create, change, pause, run again
and retire routines in the app or by asking L3. A run is not a task: it has its own page under its routine,
and never counts among your tasks. That is the whole model; everything below is how it
stays safe, cheap and quiet.

- **Triggers.** Run now, always. A schedule: days and a time, several per routine. An event you send it
  from your own scripts on this computer (step 4), such as a merged pull request your script noticed.
- **Inputs.** A run reads the brief, what fired it (the events you sent) and the
  report of its own previous run, so it can say what changed instead of repeating itself.
- **Report.** "Found something", "Nothing found" or "Couldn't finish", one line, the full answer.
- **Limits.** One routine run at a time per project, after your tasks; a time limit per run; a weekly
  budget per project; and the plan reserve every Altitude feature keeps for your own work.

## Triggers

| Trigger | What Altitude sees today | Arrives in |
| --- | --- | --- |
| **Run now** | A press on the routine's page, or asking L3 in chat | Step 1 |
| **On a schedule** | Its own 30-second timer, the one the Weekly review uses | Step 1 |
| **When it's sent an event** | Whatever your own scripts send it with `alt agent send`: a merge, an issue, a failing check, anything they can notice. Altitude ships examples, not a list of happenings | Step 4 |

By the operator's decision of 2026-10-20 there are no built-in happenings to choose from: what
matters differs by project and person, so you send events from your own scripts, from this computer
only for now (other machines: #1105). A chat message is not a trigger, since a routine run on every
message would duplicate L3 and spend constantly. Asking L3 "run the dependency check" is Run now.

**Overlap.** A routine has at most one run waiting or running. A schedule that comes due meanwhile is
skipped and says so. Events that arrive meanwhile join it if it is still waiting, or wait for the next
run, which gets all of them together ("For “PR #810 merged” and 2 more"), so a burst of events costs one
more run, not three. A project runs one routine at a time, and only in a free task slot with tasks served
first, so routines never crowd out your work. Waiting runs start oldest first, and one that has waited
six hours is skipped ("didn't start within 6 hours"), so a 07:00 check never starts at midnight.

**Missed time.** A schedule missed while Altitude was off runs once when it comes back, marked late
("07:00, ran at 09:14"); older ones are not caught up. An event is recorded with the routine before
its send answers, and one that no run reads gives its key back, so a script's next send brings it in
again. Each trigger fires once: a run is keyed by the routine and what fired it, so a restart
cannot start the same run twice.

**No loops.** Only you send events, from your own shell or scripts: task workers, L3 and routine runs
cannot send one, so no run sends an event itself. A chain through your own scripts goes through you:
from step 6, a pull request a routine opens reaches a merge recipe only once you merge it.

## Lifecycle

| Routine | Meaning |
| --- | --- |
| **On** | Its triggers fire |
| **Paused** | Nothing fires; Run now still works. New routines start here; paused by you, by L3 on your say, or by itself after three runs in a row couldn't finish |
| **Retired** | Gone from lists; its runs stay readable under Retired |

| Run | Meaning |
| --- | --- |
| **Waiting** | Fired, waiting for a free task slot or for the project's other routine run to finish |
| **Running** | Its worker is working; Stop ends it |
| **Found something** / **Nothing found** | Finished with its report |
| **Couldn't finish** | The worker failed, or the brief could not be done (`make audit` is not a target) |
| **Stopped by you** / **Stopped at its limit** | Ended by Stop or by its time limit |
| **Skipped** | Not started, with the reason: the budget could not fit its limit, the plan was inside the reserve, its last run was still going, it did not start within six hours |

**Failure and retry.** A failed run is not retried automatically: its next trigger is the retry, and
Run now is a manual one. Three triggered runs in a row that couldn't finish or stopped at their limit
pause the routine (Run now, Stop and skips do not count), so a broken brief
or a lapsed sign-in stops costing time; it says so in chat, and from step 4 it waits in Needs you
until you resume, edit or retire it. A system fault still files its incident for L3 as every fault
does.

**Versions.** Every change to a routine is kept: who, when, from which message or press, and the brief
as it was. Each run records the version it ran, and its page shows that brief.

**Trying one.** A new routine starts paused, so a half-written brief never fires at 07:00: its page
offers **Run now** and **Turn on**. Run now is the test: it runs the brief once, before or between
triggers, and never uses up a schedule time or an event. For an event routine it replays the latest
event it received. When you ask L3 for a routine that should start at once,
its offer says so and Create turns it on.

**History.** A routine's page lists its runs, newest first. A run keeps its report and session record
under its routine, as improvement runs are kept; its working copy is removed when it ends and it never
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
  push, open pull requests, file issues, start tasks, ask questions, message L3 or change routines.

What remains is the risk every task carries today, sharper because nobody watches a run: a page it
reads could talk it into sending the project's code, or the engine sign-in it holds, somewhere else.
It cannot act anywhere with your identity, and a credential kept somewhere unusual on this computer
is the remaining gap.

**Event text is data.** A run receives what fired it quoted as data after its brief, never as
instructions. That text comes from Altitude, your own tasks and the events your scripts send, so
you choose what a run reads. A routine that holds your private code, reads a stranger's words and can
reach the internet can be talked into sending one to the other. Anyone can write a public issue's
title, so the examples send numbers ("PR #812 merged") and the run reads the public page if it needs
more; the routine page says why. Running a
stranger's text with no network beyond the engine's provider would need a new sandbox mode, which no
step builds.

| | Reports only (recommended default; the only mode until step 6) | May act (step 6) |
| --- | --- | --- |
| Reads and runs commands in its own copy | Yes | Yes |
| GitHub | Public pages | Your sign-in, as task owners have |
| Pull requests | No | May open one: it goes through review and checks and is **held for your merge** |
| Issues and tasks | No; it can say what it would file (step 5: as suggestions) | Listed for L3 to file on your say |
| Questions to you | Never | Never |

## What a run may spend

- **Your plan, never an API key.** Runs use the sign-in task workers use; nothing is billed per use.
- **A budget per project.** **Routines may use up to 2 hours a week** (30 min, 1, 2, 5 or 10 hours),
  counted from Monday 00:00 on this computer's clock. Every run's minutes count, Run now included. A run
  starts only if its whole limit fits what is left, so the budget is never overshot; otherwise it is
  skipped and says why. Each project has its own, so a project's routines cannot spend another's week.
- **A limit per run.** 10, 20 (default) or 60 minutes, counted from when its worker starts; at the limit
  Altitude stops it as Stop does. Time is the cap because it reads the same on every engine; tokens are
  recorded and shown, not capped, since most are cached rereads that count very differently against a
  plan.
- **Your work first.** Before a run starts, Altitude checks the engine routing chose for it: if that
  engine's weekly or five-hour window is at the reserve (90%, the improvement review's) the run is
  skipped and says so. A window Altitude cannot read also skips, as the Weekly review does: Altitude
  reads Codex's windows only once Codex has been used, so a routine pinned to Codex may skip
  ("Your Codex plan's use isn't known yet.") until then.
- **Off is off.** Pause stops triggers at once, Stop ends a run, Retire ends the routine.

## When a run deserves your attention

Routines are quiet by design. Each routine's **Tell me in chat** is When it finds something (default),
Every run, or Never. A run's line in the project's chat is a system line: it never alerts a device and
never counts in Needs you. Couldn't finish and Stopped at its limit appear unless Tell me is Never;
skips never do. Needs you shows a routine only when it needs a decision from you: from step 4 when it has
paused itself, from step 5 its suggestions (uncounted, as improvement suggestions are), and from step 6
its held pull requests, as review cards.

## Where routines appear

| Surface | What it shows | Step |
| --- | --- | --- |
| **Work** (the project's task list) | A running or waiting run is a row in **Current** ("Routine run · “PR #812 merged” · 2 of 20 min"), opening the run. After Done this week, one row **Routines · 4** opens the Routines page; it lists no routines, so nothing is shown twice | 1 |
| **Project menu** | **Routines…**, always, so the page is reachable before the first routine exists | 1 |
| **Routines page** | `/projects/<name>/agents`: every routine with its triggers and latest news, New routine, Retired, **Built in** routines to turn on, then the weekly budget | 1 |
| **Routine page** | Status and Run now, recent runs, what it does and its triggers with Edit, Retire | 1 |
| **Run page** | Its live session while running, then its report, what fired it and the brief version it ran | 1 |
| **Project chat** | Quiet result lines; an offer card for every routine L3 would create or change, which only your press saves | 1 |
| **Monitor** | A running run among **Sessions**; **Routines** across projects with the latest result and what comes next | 3 |
| **Needs you** | Paused itself (step 4), suggestions (step 5), held pull requests (step 6) | 4, 5, 6 |
| **Project settings** | Nothing: routines are the project's work, not its configuration | – |

## L3 manages them on your say

L3 reads routines and runs at any time. In the chat turn answering your own message, which is recorded
as the authority as `alt improve run` works today, it may pause, resume, retire, run now and stop: each
only lowers or spends within the limits you set.

Creating a routine or changing what it does (its brief, triggers, model, limit or Tell me) is only ever
an **offer**: L3's reply carries a card Altitude draws from the stored definition, with the name, the
triggers, the brief (three lines and **Show all**) and **Create routine** or **Save change**, plus **Edit
first**, which opens the form filled in. Your press saves exactly what the card shows; L3 cannot save it
for you. So text a run reported, which L3 may read while answering you, can at worst draft an offer you
see and decline, never a routine that runs unattended. An offer you leave lapses after seven days.

## Built-in routines

Altitude ships ready-made routines, so a project's Routines page is never empty. Each is a brief Altitude
wrote, with a schedule and Tell me already chosen; one press of **Turn on** makes it one of your
routines, on, and from then it is yours to edit, pause or retire like any other. Retiring it returns it
to Built in. Step 1 ships three, all reports only:

| Built-in routine | Runs | What it does |
| --- | --- | --- |
| **Dependency check** | Weekdays at 07:00 | Looks for security fixes and major releases of what the project uses; tells you when it finds one |
| **Build health** | Mondays at 08:00 | Runs the project's documented checks on a clean copy and says what broke or got slower |
| **What landed** | Fridays at 17:00 | Sums up what merged this week and anything left unfinished; tells you every run |

Their briefs live in Altitude's repository and change only with a reviewed release. Turning one on
copies the brief as it is then, so a later release never rewrites a routine you have.

## The Weekly improvement review

By the operator's decision of 2026-10-19 the Weekly review becomes a built-in routine of Altitude's own
project (step 2), where it runs today. Its section in the project's settings is removed; everything it
held moves to the Routines page and the review's own routine page. Other projects are unchanged.

Its run stays its own kind: a tool-free reading of a code-built summary of conversations under the
terms you agreed to, far cheaper than a routine run, with its suggestion cards in chat and Needs you
unchanged. It does not count toward the routines' weekly budget or their one run at a time, and it never
appears in Work's Current rows; its running line in chat already shows it. Its Monday 08:00 claim is
already one case of the shared schedule rule (step 1). It has no Edit or Retire, because its brief and
model are Altitude's and proved by its eval. Turning it on and off stays yours alone, as it is today: L3's
`alt improve` verbs are unchanged, `alt agent` neither lists nor switches it, and L3's persona sends you to
its page. When routines gain suggestions
(step 5), they use its card.

**On the Routines page** (States 39 to 42):

- **Built in, until it is yours.** The first Built in row: **Weekly improvement review** over "Mondays at
  08:00 · Suggests fixes for friction that keeps coming back in this project's tasks" with **Turn on**.
  Turn on opens its terms sheet (PhoneWeeklyTerms: a bottom sheet at 390px with full-width buttons, Turn
  on above Cancel; State 40: centered at 1440px): **Turn on the Weekly improvement review for altitude?**, the sheet's existing
  text, **Cancel** (focused) and the primary **Turn on**. Cancel, Escape and the scrim change nothing.
  Turn on closes the sheet, agrees to those terms and turns it on; the row's Turn on shows its spinner
  until the list reads it as yours, and a refusal returns the button with "Not turned on: <reason>"
  under the row ("The terms changed while the sheet was open; read them again.").
- **Yours** once it was ever turned on or has ever run (an agreement given earlier through Get
  suggestions now counts). It is then the first row of your routines: **Weekly improvement review** over
  "Mondays at 08:00 · <news>" with a chevron opening its page. The news is "Reading 6 tasks" or "L3 is
  checking 3" with the running dot, "Paused" or "Paused: its terms changed" with the pause mark, else the
  latest ended run as "3 suggestions 2 d ago", "Nothing to suggest 9 d ago", "Couldn't finish 2 d ago" or
  "Stopped by you 1 d ago", else "Never run". Work's **Routines · N** counts it.

**Its page**, `/projects/altitude/agents/weekly` (DesktopWeekly, PhoneWeekly, States 43 to 50): the back
button names the project and the title is **Weekly improvement review**. Loading shows a spinner named
"Loading Weekly improvement review…"; a failed read says "Could not load the Weekly improvement review."
with **Retry**.

- **Status card.** On and idle: "Next: Monday, Oct 26" (", if 2 more tasks finish" while fewer than five
  have) with **Run now** and **Pause**, and Run now's line under it: "Reads the 6 tasks finished since
  Oct 19." When Run now cannot run it looks disabled and has no line under the card; its reason ("Run now
  needs 5 finished tasks since Oct 19; 3 so far." or "Your Claude plan is close to its weekly limit.")
  is its accessible description and shows under the card when pressed. Run now pressed shows "Starting…"
  in the button with Pause unavailable until the run is read; a refusal returns the buttons with its
  reason in red ("Another review just ended; ask again."). Running: "Reading 6 tasks" or "L3 is checking
  3" with a spinner and only **Stop**; Pause returns when the run ends, so Pause never changes a running
  line. Stop shows "Stopping…" until the end is read, or returns with "Not stopped." Paused:
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
improvement review's terms on the Routines page first.", true whether it is still in Built in or already
yours. Suggestion cards, ending lines and Needs you are
otherwise unchanged; ending lines gain the anchor Recent runs opens.

## What we considered from elsewhere

Managing routines that wake up on their own is a different problem from steering one task, and schedulers
and agent platforms have met most of it already. What they taught this design, with the source each
point was read in:

| Question | What others do | Here |
| --- | --- | --- |
| A trigger fires while a run is going | Temporal offers six overlap policies, Skip by default ([schedules](https://docs.temporal.io/schedule)); Kubernetes allows, forbids or replaces ([CronJob](https://kubernetes.io/docs/concepts/workloads/controllers/cron-jobs/)); GitHub keeps one running and one pending ([concurrency](https://docs.github.com/en/actions/writing-workflows/choosing-what-your-workflow-does/control-the-concurrency-of-workflows-and-jobs)) | Schedules skip; events wait and go together into one next run |
| Routines crowding out other work | GitHub's agentic workflows allow one agent job per engine at a time ([concurrency](https://github.github.com/gh-aw/reference/concurrency/)) | One routine run per project, only in a free task slot, tasks first, plus the weekly budget and the reserve |
| Missed or doubled runs | Kubernetes warns a job may run zero or two times ([CronJob](https://kubernetes.io/docs/concepts/workloads/controllers/cron-jobs/)); Dagster skips a run key already used ([sensors](https://dagster.io/docs/guides/automate/sensors)) | A run is keyed by routine and trigger; one late catch-up, marked late; no backfill |
| Failure | Devin does not retry, the next trigger does ([automations](https://docs.devin.ai/product-guides/automations)); Temporal can pause a schedule on failure ([schedules](https://docs.temporal.io/schedule)) | No automatic retry; three failures in a row pause the routine and tell you |
| Memory between runs | Temporal hands the last result to the next run ([schedules](https://docs.temporal.io/develop/python/schedules)); Cursor keeps a memories file but warns about untrusted input ([automations](https://cursor.com/docs/cloud-agent/automations)) | The previous report only; a notes file the run writes is left out, since injected text there would steer every later run |
| What a routine may do | GitHub's agentic workflows run the agent read-only and let separate, capped jobs perform what it asks for ([safe outputs](https://github.github.com/gh-aw/reference/safe-outputs/)); Claude's GitHub Action has no shell or GitHub access until granted ([docs](https://code.claude.com/docs/en/github-actions)) | Reports only, enforced; later actions go through Altitude's own held steps |
| Text from strangers | Simon Willison's "lethal trifecta": private data, untrusted content and a way out must not meet in one agent ([post](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/)); GitHub's agentic workflows sanitize event text and fire only for writers by default ([triggers](https://github.github.com/gh-aw/reference/triggers/)) | Event text is data and comes only from your own scripts (step 4); strangers' text reaches a run only on public pages it reads, and no step adds a reader of it |
| Loops | Claude's GitHub Action ignores bot actors ([docs](https://code.claude.com/docs/en/github-actions)) | Only you send events; runs cannot |
| Changed instructions | LangSmith versions every edit of an assistant ([assistants](https://docs.langchain.com/langsmith/assistants)) | Every change kept; each run names the version it ran |
| Trying one first | GitHub's staged mode previews without acting ([staged](https://github.github.com/gh-aw/reference/staged-mode/)); Dagster previews a sensor tick ([testing](https://dagster.io/docs/guides/automate/sensors/testing-sensors)) | Run now, which never uses up a schedule time or an event, replays the latest event |
| Attention | Codex shows an unread mark when a run needs you ([automations](https://learn.chatgpt.com/docs/automations?surface=app)); Devin mails always, on failure or on success ([automations](https://docs.devin.ai/product-guides/automations)) | A chat line when something was found or failed; Needs you only for decisions |
| Where routines are defined | GitHub's agentic workflows are markdown files in the repository ([overview](https://github.github.com/gh-aw/introduction/overview/)); Cursor and Devin keep them in the app | In Altitude's records, so a merged change or an event cannot rewrite a routine's instructions (direction C keeps files as the alternative) |

Considered and left out: automatic retries (they spend your plan and repeat work), an expiry date per
routine as GitHub's `stop-after` offers (the budget and Pause cover a forgotten routine), a turn cap beside
the time limit (time reads the same on every engine), and an alert when an expected run did not happen
(skips already say why, and Monitor shows what comes next).

## Directions

**A. Routines live with the project's work (recommended).** As drawn: runs appear in Work while they
run, one **Routines · 4** row at the end of Work and **Routines…** in the project menu open their page,
results come to chat, and Monitor carries the cross-project view. Routines are something a project does,
so they live where you look at what it is doing, in one row that never competes with current work.

**A′. Menu only (the design review's alternative).** The same, without the row in Work: routines are
reached from the project menu, chat lines and Monitor. Work stays purely about current work, at the
cost of routines being harder to find for someone who has never made one.

**B. Routines in project settings.** The same pages, reached from the project's settings instead of Work;
runs stay out of Work and show only in Monitor and chat. Quieter, and close to where the Weekly review
lives today, but a running routine is invisible from the project and routines read as configuration you
set once, which event-driven routines are not.

**C. Routines as files in the repository.** Each routine is a file such as `.altitude/agents/deps.md`:
triggers and limits in a short header, the brief below. Versioned, reviewable and copied between
projects with the code, the strongest "bring your own". But every change is a commit and a merge, L3
cannot pause or adjust one in a turn, and what runs is whatever is on the main branch. The app could
list, pause and run them. A later step can export a routine to a file and import one, without making
files the source of truth.

## Decisions that are yours

Approving the design approves the recommendations; any can be changed first.

| Decision | Recommendation | Alternatives |
| --- | --- | --- |
| What a routine is | A free brief, triggers and limits; it can name any skill, script or file in the repository; three editable examples | Fixed kinds with fields; or files in the repository (C) |
| What a run is | A sandboxed helper worker, kept under its routine rather than as a task, with your sign-ins withheld and no `alt` verbs | A task (reuses the task page, but every task reader would need to skip runs); or a tool-free model call like the improvement review: cheaper, cannot run a check |
| Act or report | Reports only until step 6, then a per-routine choice with pull requests held for your merge | Allow acting sooner |
| Spend | A weekly budget per project (2 h), a limit per run (20 min), the 90% reserve | Per-run limit only; or a token cap |
| Attention | Chat lines only when it found something or failed; Needs you only for decisions | Every run in chat; or a daily digest line |
| Where they live | With the project's work (A) | Menu only (A′); project settings (B) |
| How L3 creates or changes one | An offer card your press saves | L3 saves it on your say, with a confirming line |
| Weekly review | A built-in routine of Altitude's own project (your decision), keeping its own kind of run | – |
| Built-in routines | Dependency check, Build health and What landed, off until turned on | None |
| Order of steps | Schedule, Run now and built-ins first; the Weekly review; Monitor; events you send; suggestions; acting | Events in step 1 |

## Screens

Boards in this folder at 390×844 (phone) and 1440×900 (desktop), written by `gen.py`: `DesktopWork`,
`PhoneWork` (the project's task list), `DesktopAgents`, `PhoneAgents` (all routines), `DesktopAgent`,
`PhoneAgent` (one routine), `DesktopRun`, `PhoneRun` (one run), `DesktopWeekly`, `PhoneWeekly`, `PhoneWeeklyTerms` (the
Weekly review as a built-in routine, its terms sheet), `DesktopEdit`, `PhoneEdit` (the form),
`DesktopChat`, `PhoneChat`, `DesktopMonitor`, `PhoneMonitor`, `DesktopNeeds`, `PhoneNeeds` (Needs you with a routine
that paused itself), `PhoneRetire`, and every state at both
widths: `States` and `PhoneStates`. The boards draw the full design; the steps say when each part
arrives.

### Work

Work keeps answering "what is happening in this project?". A waiting or running run is a row in
**Current** with the ordinary dot and chevron: the routine's name, then "Routine run · “PR #812 merged” · 2 of
20 min", "Routine run · waiting to start" or "Routine run · waiting while Review each merge runs"; it opens
the run's page. Runs are not tasks: the header's "N tasks in flight" and Done this week leave them out,
and a finished run leaves Work, its history being on the routine's page. After Done this week, one row
**Routines · 4** with a chevron opens the Routines page; it names no routines, so nothing in Current is said
twice. With no routines the row is absent, and the project menu's **Routines…**, after **Project
settings…**, is always there.

### All routines

`/projects/<name>/agents`, with **‹ harbor** back to the project and the title **Routines** over "Autonomous agents, scheduled or event-driven." One card: a
whole-row link per routine with a chevron, the name in semibold and one muted line, its triggers then the
latest news, with a cue before it when one applies (a pulsing dot running, a pause mark paused):

- "Weekdays at 07:00 · Found something 2 h ago"
- "When it's sent an event · Running, 2 of 20 min"
- "Mondays at 08:00 · Waiting while Review each merge runs"
- "Fridays at 17:00 · Paused"
- "Only when you run it · Never run"

Several triggers read "Weekdays at 07:00 and when it's sent an event"; more than two read "3
triggers". Schedules read "Every day", "Weekdays", "Weekends", "Mondays", "Mon, Wed, Fri", with the
time in the machine's clock and its zone when the browser's differs ("07:00 Istanbul"). The card holds
only routine rows and is absent with no routines. A bordered **New routine** sits under it, outside the card.
A muted "2 retired" link opens the retired routines as rows ("Retired Oct 19 · 23 runs"), each opening
its read-only page.

**Built in** follows, over "Ready to turn on; each becomes yours to change.": one row per built-in
routine not yet turned on, its name in semibold, "Mondays at 08:00 · Runs the project's checks on a clean
copy and says what broke" muted beneath, and a bordered **Turn on**. Pressed, the button spins until
the routine reads as on, then the row leaves Built in and appears among your routines; refused, the button
returns with the reason under the line ("Not turned on: the routines changed in another window."). With every
built-in turned on, the group is absent.

**Weekly budget** comes last, a section of its own: **Routines may use up to** with a select (30 min · 1 h ·
2 h · 5 h · 10 h a week) and "1 h 10 min used this week" under it. Spent, the used line turns amber
("2 h used this week"), and an amber line under the page's intro says it once, on a phone's first
screen: "This week’s budget is used: no new runs until Monday unless you raise it."

### One routine

`/projects/<name>/agents/<id>`, **‹ harbor** back, the routine's name as the title. In order:

1. **Status card.** On: "Next: tomorrow at 07:00" (a schedule) or "Runs when it's sent an event"
   (events only) with **Run now** and **Pause**. Running: a spinner, "Running · 4 of 20 min", an
   underlined **Open** to the run, **Stop** and **Pause** (Pause lets the run finish and fires nothing
   after it). Waiting: "Waiting to start" or "Waiting while Review each merge runs" with **Stop**.
   Paused: a pause mark and "Paused" (or "Paused itself after 3 failed runs"), with **Resume** and
   **Run now**. New: "Not on yet" with **Run now** and **Turn on**. Pause, Resume and Turn on answer at
   once; the line changes with them.
2. **Recent runs.** The last ten, newest first: the outcome in semibold, date and time, minutes and
   tokens, and its line beneath, opening the run's page. An event run's line starts with what fired it
   ("For “PR #810 merged” and 2 more: …"). Skipped runs show their reason and have no page or chevron. Dots:
   accent for found, danger for couldn't finish and limit, muted otherwise. **Show older** loads ten
   more. None: "No runs yet."
3. **What it does.** The brief clamped to three lines with **Show all**, then "Weekdays at 07:00 · Tells
   you when it finds something · Default model · Stops after 20 min", and a bordered **Edit**.
4. **Retire routine**: its heading and an outlined red **Retire…**; the dialog says what retiring does.

### One run

`/projects/<name>/agents/<id>/runs/<n>`, **‹ Dependency check** back, the title "Run · Today 07:00".
A line says what fired it ("On its schedule", "Requested by you", "For “PR #812 merged”"), the model, and
minutes and tokens once it has ended. While running: "Running · 4 of 20 min" with **Stop**, then the
live session as a task's (the same transcript component, read only). Ended: the outcome in semibold
with its dot, then the full report as text (its one-line summary is what lists and chat show); under
it, folded, **Brief it ran**, the version as it was then, and **Session**, the transcript. A run has no
message box, review boxes, terminal or questions.

### The form

**Edit** opens `/projects/<name>/agents/<id>/edit`; **New routine** opens `/projects/<name>/agents/new`,
empty (ready-made briefs are the built-in routines). Fields:
**Name**; **What it does** (hint "Write it as you would brief a task. It can use anything in the
repository."); **Runs when**; **Tell me in chat** (When it finds something · Every run · Never;
segmented on desktop, stacked on phone); **Model** (the model button of §3.6.1); **Each run stops
after** (10 min · 20 min · 1 hour). **Save** or **Create routine**, and **Cancel**; for a new routine, a muted
"It starts off: try it with Run now, then turn it on." under them. A new routine is saved paused and its
page opens, with Run now and Turn on.

**Runs when** is a bordered list, one trigger per row with an icon (a clock for a schedule, a bolt for
an event), its words and a Remove button (44px on phone). A schedule row opens to seven day toggles
(M T W T F S S, 40px targets on phone, on one line) and a time; with no day on, the row says "Pick at
least one day." and Save waits for one. The event row reads "When it's sent an event" over "You send
it from this computer; its page shows how.". **+ Add a trigger** opens a menu: On a schedule · When
it's sent an event; with the event row in the list it adds a schedule at once. With the event trigger, the hint
"Events that arrive during a run go to its next run together." follows the list. Removing the last trigger leaves "Only when you run it". New routines start with a
weekday 07:00 schedule.

Without a name or brief, Create routine looks unavailable. Pressed, it puts "Give it a name." or "Say what
it does." under each missing field, in the danger tone and linked to the field (`aria-describedby`), and
focuses the first missing field; each line goes once its field is filled. Leaving with unsaved changes
asks "Discard your changes?" with **Keep editing** (focused) and **Discard**. A change to a running routine
applies from its next run.

Retire opens a dialog: **Retire Dependency check?**, "It won't run again, from its triggers or Run
now. Its past runs stay readable under Retired.", **Cancel** (first, focused) and a red **Retire**. A
retired routine's page has its title, "Retired Oct 19", its runs and its definition as text.

### In chat

Result lines are system lines (§3.4) at the time the run ended, one line on desktop, led by the
routine's name, with **Open**:

- "**Dependency check** found something: 2 packages have security fixes." accent dot
- "**Review each merge** found something for “PR #809 merged”: the upload limit moved without its test."
- "**Dependency check** found nothing." only with Every run
- "**Dependency check** couldn't finish: `make audit` is not a target." danger dot, unless Never
- "**Dependency check** stopped at its 20-minute limit." danger dot, unless Never
- "**Dependency check** stopped by you." only with Every run
- "**Dependency check** paused itself: 3 runs in a row couldn't finish." danger dot, always

The text after the name is at most 120 characters from the run's own summary, made plain as improvement
lines are; the report itself is one press away.

When you ask L3 for a routine or a change, its short reply carries the **offer card** (see L3 manages
them): a bordered card under the reply with "New routine" or "Change to Dependency check" in muted text,
the name in semibold, the triggers and Tell me on one muted line, the brief clamped to three lines with
**Show all**, and **Create routine** (primary) or **Save change**, then **Edit first**. A change shows
only what changes ("Runs at 06:00 instead of 07:00"; a new brief in full). Pressed: the button spins,
then the card reads "Created · Open" or "Saved · Open" in place. Refused (the routine changed since):
"Not saved: Dependency check changed since this offer." and the card's buttons go. Lapsed after seven
days: "This offer lapsed." with no buttons. Pause, resume, retire and stop through L3 add one quiet
line from the record: "Paused Dependency check · Resume", "Retired Dependency check".

### Monitor (step 3)

Monitor answers "what is Altitude doing, and what will it do next?" across projects. Routines join it
in two places, at the end of the page, and change nothing else on it: the order stays Altitude update,
Seats, Routing now, Sessions, then Routines (`DesktopMonitor`, `PhoneMonitor` draw the whole page).

**Sessions.** A running run is a row among the L3 and L2 sessions, after its project's tasks, drawn
as they are: the chip **Routine**, the title "<project> / <routine name>" (one line, truncated with an
ellipsis, the full title on hover), the age of its latest reading right-aligned ("just now", "2 min
ago"), then one muted line "<engine> · <model> · context 6% · 4 of 20 min" and the context meter.
Before the first reading of its context the line says "context unknown", with no age and no meter,
as a session's does. A reading older than five minutes is stale as a running task's is: the meter
at half opacity and "Stale" for screen readers. The row is a reading, like the other session rows,
not a link; its routine's row below opens the routine. The Sessions count includes it. A waiting run
is not a session: it shows only in Routines. The Weekly improvement review's run is one engine call
rather than a session, so it never appears here; its row below says Running.

**Routines (N).** After Sessions, a section with the heading "Routines (N)" lists, across projects,
every routine that is not retired and is any of: on with a schedule or the event trigger; waiting or running; paused itself
(until you resume or retire it); or had a run in the last seven days. In Altitude's own project the
Weekly improvement review is listed once it is yours, on the same terms. A routine with no schedule
that has not run for seven days, or one you paused that has not run for seven days, drops off. Projects come in Monitor's project order, each
project's routines in its Routines page's order (the Weekly review first). No such routine, no section and
no heading. One card holds the rows, separated by hairlines; each row is a whole-row link to the
routine's page with a chevron at the right, centred on the row:

- the title "<project> / <routine name>" in semibold, wrapping when long, named as its session row
  names it;
- what comes next, the first that applies, in the order the routine page's status uses:

  | When | Line | Routine page's status |
  | --- | --- | --- |
  | A run is running | pulsing dot, "Running" | Running · 4 of 20 min |
  | A run is waiting | "Waiting" | Waiting to start |
  | Not on yet (you ran it by hand) | "Not on yet" | Not on yet |
  | Paused itself | pause mark, "Paused itself" | Paused itself after 3 runs in a row couldn't finish |
  | Paused by you | pause mark, "Paused" | Paused |
  | Weekly review, terms out of date | pause mark, "Paused: its terms changed" | the same |
  | Its project's weekly budget is used, and it has a schedule | "Weekly budget used" | Next: … (the Routines page says the budget is used) |
  | It has a schedule | "Next tomorrow at 07:00", "Next today at 17:00", "Next Friday at 07:00" | Next: tomorrow at 07:00 |
  | No schedule | "Only when you run it" | Only when you run it |

  Times are the computer's, with its zone after the time when the browser is in another ("07:00
  Istanbul"), as on the Routines page. The Monitor's lines are these short forms, not the page's
  sentences;
- "Last: <outcome> · <when> · <minutes> min · <tokens> tokens", muted and wrapping: the latest run
  that ran (as the Routines page's news counts it, so skips never replace a result), its parts the run
  lacks left out ("Last: Found something · 2 h ago · 6 min · 3.9M tokens", "Last: Couldn't finish ·
  yesterday · 1 min", "Last: 3 suggestions · 2 d ago · 4 min · 1.2M tokens"). Skips stay in the
  routine page's Recent runs. With none: "No runs yet.", except under a first run that is running or
  waiting, which shows no line.

On desktop, what comes next sits right-aligned on the title's first line and never wraps; "Last:"
is the line beneath. On the phone every row has the same three parts stacked: the title, then what
comes next in the secondary tone, then "Last:" muted, so no row's shape depends on how long its
name is.

Monitor reads both sections in its one read, so they share its skeleton while loading and its
"Could not read the monitor." with Retry; there is no separate loading or error for routines. The page
keeps its refresh, so a run that starts or ends moves between Sessions and Routines on the next read.
A running routine shows in both places on purpose: the session row says how its session is doing, the
routine row what the routine does next.

### Events you send (step 4)

By the operator's decision of 2026-10-20, Altitude does not decide which happenings matter. Projects
and people differ too much, and a fixed list (merges, finished tasks, incidents, GitHub's issues)
would fit Altitude's own project and few others. So a routine has one more trigger, **When it's sent an
event**. It runs on what you send it, from your own scripts on this computer, with examples to start
from. There is no catalogue of ready-made triggers to install.

**Sending.** One command on this computer sends a routine a short text:

```sh
alt --project harbor agent send 3f9a2c1b0e4d "PR #812 merged"
```

- The routine is named by its id, or by its name when exactly one routine that is not retired has it. Its
  page shows the command with its name, quoted for the shell (`'Review each merge'`), or its id when
  another routine that is not retired shares the name or the name starts with `-`.
- The text is at most 4,000 characters, read from the argument or, with `-`, from stdin.
- `--key pr-812` makes a send idempotent: an event whose key the routine already took is ignored
  ("Already received."). So a script can send everything it sees each time it runs, with no record
  of its own. A routine keeps its latest 1,000 keys for as long as it exists.
- A send answers at once. The key is checked first:
  - "Already received.", with exit status 0;
  - "Sent to Review each merge.", with exit status 0;
  - otherwise "Not sent: <reason>." with exit status 1, as when the routine is paused, not on yet,
    retired, has no event trigger, the text is too long, 50 events already wait for its next run,
    or the week's budget cannot fit its run limit.
- A refused send keeps nothing, not even its key, so the script's next send gets it in. An event that
  no run reads gives its key back too: one dropped by Pause or Retire, including the routine pausing
  itself, and one whose run is skipped or stopped before it starts. A script that sends the latest few each time therefore loses
  nothing while the routine is paused, and the first time it runs after you turn the routine on, the
  latest few arrive together in one run.
- To try a routine with an event before it is on, `alt agent run AGENT [TEXT|-]` gives Run now a text
  of your own; that run reads it as its one event.
- Only you can send. The command runs only in your own shell or a script you run, with this
  computer's key, as `alt agent new` does. Task workers, L3 and routine runs have no `send`, so a
  routine run can never fire a routine, and nothing new is reachable from the network. A web address
  for other machines is tracked as #1105.

**Together.** An event finds the routine in one of three states:

- With no run waiting or running, the event starts a waiting run.
- With a run waiting, the event joins that run.
- With a run running, the event waits for the next run.

When the running run ends, the next run waits with every event that waited, so a burst of five sends
during a twenty-minute run costs one more run, not five. At most 50 events wait or join one run.
Pausing or retiring the routine drops the events that waited. A run skipped at its start (the reserve, six hours without starting) is
skipped with its events, and its line names them. A schedule that comes due while events wait still
fires; the events join its run. Event runs count toward pausing itself as scheduled runs do.

**What a run reads.** Every event the run took, in the order they arrived, each with the time it came.
The text is quoted as data after the brief, never as instructions, as a run reads everything that
fired it. You decide what text a run sees. A run holds your code and has the network, so the
examples send numbers and your own words, never text anyone else can write, such as an issue's
title. Docs and the routine page say so in one line.

**Run now** on a routine with the event trigger replays the latest event it received, without using it
up. Its run page says "Requested by you · For “PR #812 merged”, the latest". With none yet, the run
starts without an event, and the status card says so beforehand, muted under its line: "No event
yet, so Run now runs without one."

**The form.** **+ Add a trigger** replaces + Add a schedule. It opens a menu under the button at
both widths, as the project menu does, with items at least 44px tall on the phone: **On a schedule**
and **When it's sent an event**. Choosing the second adds its row after the schedules, and focus
moves to the row's Remove button. Once the list has the event row, + Add a trigger adds a schedule
directly, with no menu. Escape or a press outside closes
the menu and returns focus to the button. The event row has the bolt icon, "When it's sent an event"
over "You send it from this computer; its page shows how.", and Remove (44px on the phone). With it,
the hint "Events that arrive during a run go to its next run together." follows the list.

**The routine page.** With the event trigger, What it does gains **Send it an event**, after the
definition line and before Edit:

- "From a script or your terminal on this computer:" over the command in a code block with **Copy**,
  the text a placeholder ("what happened");
- when the command uses the name, a muted line under it: "The examples use its id, 553012b713c4, so
  they keep working after a rename.";
- the muted line "To try it first, use `agent run` in place of `agent send`: it starts a run now,
  even before the routine is on.";
- **Examples**, folded, opening three short recipes. Each has a muted line saying what it does with
  **Copy** at its right, over its code in a block that keeps its lines and scrolls sideways rather
  than wrapping:
  - "Each merged pull request: a script for cron every 10 minutes": a comment with its crontab line,
    a `PATH` line that finds `alt` and `gh` from cron on Linux and the Mac, and a loop that lists the
    numbers of the ten pull requests merged or updated last with `gh` and sends each as "PR #812 merged" with
    `--key pr-812`, discarding the answer when it is sent;
  - "Each new issue: a script for cron every 15 minutes": the same for the ten newest issues, open or closed, "Issue #42
    opened" with `--key issue-42`;
  - "When a command of yours fails": `make bench >/dev/null 2>&1 || alt … "Benchmarks failed on $(git rev-parse --short HEAD)"`.
  The recipes use the routine's id, and the project's repository where they need one. A refused send
  writes its reason to standard error, which cron mails or logs as it is set up.
- a muted line: "Send numbers and your own words, not text anyone else can write: a run has your
  code and the network."

**Words.** The trigger reads "When it's sent an event":

- the Routines page's line: "Weekdays at 07:00 and when it's sent an event";
- the status card: "Runs when it's sent an event" for a routine with no schedule;
- What it does and the offer card ("Also runs when it's sent an event", "No longer runs when it's
  sent an event");
- Monitor's next column for a routine with no schedule, in place of "Only when you run it".

More than two triggers still read "3 triggers".

**Naming the events.** An event is named by its text's first line, in quotes and cut at 60
characters with an ellipsis. One run's events read "“PR #812 merged”" or "“PR #810 merged” and 2
more". That name appears:

- in its Recent runs line ("For “PR #810 merged” and 2 more: …");
- in Work's run row ("Routine run · “PR #812 merged” · 2 of 20 min");
- in the chat line ("found something for “PR #809 merged”: …");
- in a skip line ("For “PR #806 merged” · Didn't start within 6 hours.").

A run page lists each event it took under its line, muted, one per line: its time and its first
line. A text given to `alt agent run` reads "Requested by you · For “PR #812 merged”, your text". While a run is running and events wait, a muted line under the status line, before the
buttons, says "1 event waits for the next run." or "3 events wait for the next run.". On the phone
the buttons sit beneath, as the status card already stacks there.

**Needs you.** When a routine pauses itself, a card waits in Needs you under its project, after that
project's questions:

- the kind label "Routine paused itself", in the danger tone, with its age;
- the routine's name as the card's title, a link to its page with a chevron after it;
- "3 runs in a row couldn't finish. The last: `make audit` is not a target." The reason comes from
  the last run's line, or "stopped at its 20-minute limit" for a limit stop;
- a bordered **Resume**, and the underlined link "Open the last run", to read what broke before
  resuming.

The card counts in the Needs you summary ("1 question · 1 paused routine in harbor") and in the badge.
Like a stopped task or a fault, it never alerts a device. Pressing Resume shows a spinner in the
button until the routine reads as on, and then the card leaves. If the routine was retired in another
window, the button returns with "Not resumed: Flaky tests is retired." beneath, and the card leaves
on the next read. The card also leaves when the routine is edited or retired: saving a change makes it
paused by you, and the status reads "Paused". Run now does not clear it.

### Suggestions (step 5)

A run that finds something often knows the fix: a test to add, a page to update, a dependency to
bump. Today it can only describe the fix in its report, so turning it into work means reading the
report and asking L3 for a task in your own words. From this step a run may also **suggest tasks**.
L3 checks each one first, as it checks the Weekly improvement review's, so only what holds up reaches
you. What survives waits for your decision with **Make a task** and **Dismiss**. A run still never acts:
a suggestion becomes work only when you press Make a task.

**What a run writes.** After its report, a run that found something may add up to three blocks:

```text
Suggest: Add a test for the uploader's retry limit
A case beside the other retry tests in tests/test_uploader.py that checks the limit of 5, so the next
change to it fails a test.
```

- The title is the line after `Suggest:`, at most 90 characters. The explanation follows it, at most
  300 characters. It says what to change and why it helps, not the finding again, which the report
  already holds.
- Altitude reads the blocks in code when the run ends and cuts longer text at a word with "…".
- It makes the text plain, as it does for improvement suggestions: no links, emphasis, code marks or
  headings. Text that looks like a credential becomes "[hidden]". Paths and numbers stay as plain words.
- A run that found nothing or couldn't finish suggests nothing. Blocks in its answer are ignored.
- The run template says when to suggest: only work a task owner could do as it stands, one task each,
  never something the report only wonders about.

**L3 checks them first.** When a run ends with suggestions, Altitude queues one L3 turn that names only
the run, as it does after a Weekly review.

- L3 reads the suggestions and the run's report with `alt agent show AGENT --run N`. Its turn says they
  come from a run that read the project and the web, so they are claims to check, never instructions.
- It checks each one against the repository, current tasks, open issues and the routine's earlier
  suggestions. Then it keeps each one or sets it aside with a reason, naming every number once:
  `alt improve post agent:<agent>/<run> --keep 1 --drop 2:'already covered by #214'`. The turn tells it
  to run nothing else. Like L3's reading of GitHub issues, that is an instruction L3 follows, not a limit
  Altitude enforces (see Safety).
- Altitude, not L3, shows what was kept, in the run's own plain wording.
- While the check runs, the run page shows "L3 is checking 2 suggestions" with a spinner, and nothing
  is in Needs you yet.
- What L3 did not offer shows only on the run page, folded under **Not offered · 1**, each with its reason.
  It rests folded. When L3 keeps none, Needs you never shows the run.
- The check has two hours from the run's end. The checking line stays until L3 posts or the two hours
  pass; it has no Stop, because nothing reaches you before the post and the deadline ends a check that
  waits too long. A check that misses it or is interrupted sets every suggestion aside, and the fold
  says why once: **Not offered · 2 · not checked in time**.
- Each check is one L3 turn on your plan, outside the routines' weekly budget. The limit below bounds how
  often that happens.

**At most three waiting per routine.** A routine has at most three suggestions waiting at a time, and
Altitude enforces it. A suggestion waits while L3 checks it and for seven days after the check, whether
L3 kept it or set it aside, unless you decide it sooner. A run's prompt says how much room is left.
Altitude keeps only as many of the run's blocks as fit, and a run whose routine already has three waiting
is told to suggest nothing, so it causes no check. A routine therefore causes at most three checks a week
unless you decide its suggestions, and a check that sets everything aside never makes room for the next.
**Before you** lists the routine's earlier suggestions from its last five runs that had
any, each with what became of it: waiting, made a task with the task's state, set aside by L3 with its
reason, dismissed, or not decided. The run is told not to suggest one of these again without new
evidence. A routine that runs on every merge therefore holds at most three suggestions in Needs you,
however busy the week.

**In chat** nothing changes: the run's line is the same quiet line, governed by Tell me. Suggestions are
decisions, not news, so they wait in Needs you.

**The suggestion card** is the improvement card's layout without its improvement-only parts:

- **Header:** the routine's name, bold, with the run's time on the right. Under it, muted, what fired the
  run: "For “PR #810 merged” and 2 more", "On its schedule" or "Requested by you". The finding
  is not repeated; the run page holds it.
- **Each kept suggestion**, separated by a hairline: its number and title (15px semibold, wraps, never
  cut), then its explanation, then **Make a task** (bordered, accent text) and **Dismiss** (ghost). There
  is no target chip, no "Seen in" and no count: one run is the evidence.
- **Footer:** an underlined **Open the run**.

The card appears in two places:

- **In Needs you**, under **Suggestions**, once L3 has kept at least one, while any suggestion on it is
  undecided, or accepted without its task for up to seven days. It sits newest first among the project's improvement cards.
  It is never counted and never alerts a device. It shows whatever Tell me says, because a suggestion
  waits for a decision, while Tell me governs news.
- **On the run page**, as a **Suggested tasks** card under the report: the checking line while L3
  checks, then the kept suggestions with the same buttons and states, then **Not offered · N** folded,
  and no footer. It loads and fails with the run page.

Deciding in one place updates the other. The form's Tell me row has one muted line under its choices:
"Suggested tasks wait in Needs you whatever you choose."

**Decisions** follow the Weekly review's suggestions:

- **Make a task** saves your acceptance, with your press as its authority. While it saves, a spinner
  holds the button's place and Dismiss dims. Then the line reads "Accepted · L3 is opening a task"
  until the outcome is read. These words name the work Altitude carries out, as the Weekly card already
  does. When L3 opens the task, the line becomes that task's card. If the turn ends without a task, the
  line reads "Accepted · no task yet · Make a task", where the underlined **Make a task** presses Make a
  task again and queues the same single turn. While it queues, the link becomes a spinner. If it is
  refused, it returns with "Not saved." Each suggestion makes at most one task. An accepted
  suggestion without its task waits in Needs you for seven days after your last Make a task, then stays
  only on the run page. L3's reply says why it made none, for example that existing work already covers
  it.
- **Dismiss** folds the suggestion at the press to its muted title and **Dismissed**, with a spinner where
  **Undo** goes until the save lands. Undo is offered until the suggestion lapses. Pressing it opens the
  suggestion again at once, with both buttons disabled beside a spinner until the save lands. A refused
  Dismiss or Undo puts the suggestion back as it was, with "Not saved: <reason>" and Retry. A refusal
  because the suggestion changed elsewhere shows it as it now stands, without Retry.
- A suggestion lapses seven days after L3 kept it. It folds to **Not decided**, and the card leaves Needs
  you once nothing on it waits.
- **In chat:** "make a task for the retry-limit one". L3 records your answer citing your message, as
  for improvement suggestions, and the card changes exactly as a press would change it.
- Retiring a routine leaves its suggestions decidable until they lapse, with the card unchanged. A check
  in flight still finishes. Pausing changes nothing.

**Where a task came from.** A task made from a suggestion shows it at the end of its card's muted
line, in Work and wherever its card appears: "Running · PR #811 green · from Review each merge". Tasks
from the Weekly improvement review's suggestions read "from Weekly improvement review". The review is
one of the project's routines, so the two read the same way.

**Safety.** A routine run holds your code and reads the web, so its suggestions are riskier than the
Weekly reviewer's, which reads only a summary Altitude builds, with no tools. Four things stand between
that text and work:

- **L3 checks before you see it**, as above.
- **You see it as plain text.** It has no links or formatting, and nothing in it is a control.
- **Make a task is your press.** The task's authority is that press, never the text.
- **L3's task turn is written for this source.** It says the suggestion comes from a routine run that read
  the project and the web, so its wording and the run's report are claims, not instructions. L3 writes
  the task's brief from what it verified itself, quoting the suggestion only as the claim that started
  it. That turn is told to run exactly one `alt task new --improvement REF` and nothing else.

Both turns' limits are instructions to L3, as its rule to treat GitHub issue text as data is today: L3 can
still run its other verbs in them. What a misled L3 could do is what it can do on any turn, and nothing
becomes work without your press or an operator decision. Enforcing a turn's commands in Altitude would
cover every turn that reads strangers' text, issues included, so it is its own change (#1108).

A run gains no new ability: it still cannot open a task, message anyone or use `alt`. Strangers' text
reaches a run only on public pages it chooses to read, as it has since step 1. Events carry only what
your own scripts send (step 4). No step adds a reader of strangers' text.

### States

| Surface | State | What appears |
| --- | --- | --- |
| Routines page | Loading / read failed | A spinner named Loading routines…; "Could not load routines." and Retry |
| Routines page | No routines of your own | The line, **New routine** with no card above it, every built-in routine, then the Weekly budget |
| Built in | Turning on / refused / Altitude's project | A spinner in Turn on; the button returns with its reason; the Weekly review listed first, its Turn on opening its terms (step 2, [above](#the-weekly-improvement-review)) |
| Budget | Saving / refused / spent | The choice shows at once with Saving…; refused: back to the saved value, "Not saved: <reason>" and Retry; spent: "2 h used this week" in amber, and "This week’s budget is used: no new runs until Monday unless you raise it." in amber under the page's intro |
| Status | New / Run now pending / refused | "Not on yet" with Run now and Turn on; "Starting…" until the run is read; refused: Run now again with its reason ("Only 12 min of this week's budget is left.") |
| Status | Waiting / running / Stop pressed | "Waiting to start" or "Waiting while Review each merge runs" with Stop; Running with Stop and Pause; "Stopping…" until the end is read |
| Status | Pause pending / refused / paused itself | The line and button change at once; refused: they return with "Not saved: <reason>" and Retry; paused itself: its reason in the line |
| Runs | None / several events / late / skipped | "No runs yet."; one run's line names every event it took; "Oct 13, 07:00, ran at 09:14"; "Didn't start within 6 hours" with no chevron |
| Run page | Running / ended / couldn't finish / loading / read failed | The live session and Stop; the report with Brief it ran and Session folded; the reason in the report's place; a spinner; "Could not load this run." and Retry |
| Form | Saving / refused / unsaved / incomplete / no day | Save shows a spinner; refused keeps the edit with "Not saved: <reason>" and Retry; the Discard dialog; "Give it a name." / "Say what it does." under each missing field, focus on the first; "Pick at least one day." |
| Triggers | Add / none | The trigger menu; "Only when you run it" |
| Triggers | Menu / event row / event added | On a schedule and When it's sent an event, Escape returning focus; the event row with its line and the hint after the list; + Add a trigger adds a schedule directly |
| Status | Events only / none yet / events waiting | "Runs when it's sent an event"; "No event yet, so Run now runs without one."; "3 events wait for the next run." under Running |
| Send it an event | Command / examples | The command with Copy, by quoted name or by id; "The examples use its id, …" when it uses the name; the line about trying it; Examples folded, then three recipes, each its line with Copy over its code, and the line about others' text |
| Runs | Several events / Run now replay / skipped with events | "For “PR #810 merged” and 2 more: …"; the run page's "Requested by you · For “PR #812 merged”, the latest"; "For “PR #806 merged” · Didn't start within 6 hours." |
| Run page | Events | One muted line per event, its time and first line, under what fired it |
| Needs you | Paused itself / resuming / refused / cleared | The card with its title link, Resume and Open the last run, counted, no alert; a spinner in Resume; "Not resumed: Flaky tests is retired."; gone after Resume, a saved edit or Retire |
| Run page | Checking / none kept / check unfinished | "L3 is checking 2 suggestions" with a spinner until the post or the deadline, nothing in Needs you; only Not offered · 2, folded, opening to each title with its reason, Needs you never shows the run; "Not offered · 2 · not checked in time", folded, opening to the titles |
| Suggestion card | Needs you / run page / routine retired | Under Suggestions once kept, uncounted, newest first, whatever Tell me says; Suggested tasks under the report with Not offered · N folded, loading and failing with the page; unchanged and decidable |
| Suggestion | Make a task pressed / accepted / task opened | A spinner in Make a task, Dismiss dimmed; "Accepted · L3 is opening a task"; the task's card |
| Suggestion | No task yet / Make a task pressed again / refused | "Accepted · no task yet · Make a task"; a spinner in place of the link; "Not saved." |
| Suggestion | Dismissed / Undo pressed / refused / lapsed / typed answer | Folded with a spinner, then Dismissed and Undo; open with both buttons disabled beside a spinner; back as it was with "Not saved: <reason>" and Retry; "Not decided"; the same change a press makes |
| Form | Tell me / new routine | "Suggested tasks wait in Needs you whatever you choose." under the choices; "It starts off: try it with Run now, then turn it on." under the buttons |
| Work | A task from a suggestion | "Running · PR #811 green · from Review each merge" |
| Offer | Shown / saving / created / refused / lapsed | The card; a spinner in the button; "Created · Open"; "Not saved: …"; "This offer lapsed." |
| Retire | Pending / refused | "Retiring…" with Cancel unavailable; refused: the reason under the buttons, Cancel works |
| Page | Retired | Title, "Retired Oct 19", runs, definition as text |
| Work | Run waiting / running / no routines | Rows in Current; the Routines row absent, Routines… in the menu |
| Chat | Each ending | The lines above |
| Monitor | Loading / read failed | Monitor's skeleton; "Could not read the monitor." and Retry, for both sections |
| Monitor | Run running / no reading yet / stale | The Routine session row with its meter; "context unknown", no age and no meter; past five minutes the meter at half opacity |
| Monitor | No routines / only retired or quiet ones | No Routines section and no heading |
| Monitor | Each next / each latest | The lines [above](#monitor-step-3), the first that applies; "No runs yet." when none ran, and no line under a first run that is running or waiting |

## Steps

Each step was its own held pull request and came back to you before it was built.

1. **Routines on a schedule or run by hand, reporting only.** The three built-in
   routines with Turn on; the record and its
   versions; schedule triggers on the shared clock with **+ Add a schedule**; Run now; runs under their
   routine with the run page, the previous report as input, the restricted launch and no `alt` verbs; the
   weekly budget, per-run limit and reserve; pausing itself after three failures, said in chat; the
   Routines page, routine page, form and retire; Work's run rows and Routines row, the menu's Routines…; chat
   lines; L3's verbs and offer cards; docs and tests.
2. **The Weekly review as a built-in routine** of Altitude's own project, its settings section moved to
   its routine page.
3. **Monitor.** Runs among Sessions and the Routines section.
4. **Events you send.** The one event trigger, sent from your own scripts on this computer with `alt agent
   send`, with examples on the routine page; the trigger menu; events that wait and go together; Run now on
   the latest event; the paused-itself item in Needs you. By the operator's decision of 2026-10-20 there
   is no built-in list of happenings and no GitHub reader of Altitude's own; events from other machines
   are #1105.
5. **Suggestions.** A run may propose up to three tasks. L3 checks them first, and those it keeps wait
   on the improvement suggestion card under Suggestions in Needs you, with Make a task and Dismiss;
   tasks made from one show "from <routine>" in Work.
6. **Acting.** [Routines that act](ACTING.md) replaces this step: a routine may ask Altitude to make each
   change within what its form allows. Its first increment, Worktrees and branches with Undo, is built; pull
   requests and the rest follow as its later increments (#1109).

## Mac verification

The schedule lives in the service's own timer, so no launchd job is added. The Mac still owes, on #1098:
a run launches under Seatbelt with no GitHub token in its job's input and nothing of the operator's engine
folder linked, its door refuses everything but sign-in, the limit stop ends its job, and schedules read
the Mac's local time.
