# Improvement review: proposal

Status: the operator approved this design and its eval on 2026-10-12. The first rollout step is
built: the reviewer and its eval on fictional projects; nothing reads a real conversation yet. The
rollout below builds the rest in held steps. In the interface the feature is called **Improvement
suggestions**, so it is never confused with the code and proposal reviews that Needs you already
shows; this document calls one run "a review".

## What it is for

Working with agents leaves friction behind. You answer something you already settled, correct the
same kind of brief twice, wait on a flaky check, or watch a review re-report findings already dealt
with. Each instance is small and easy to forget; the repetition is what costs you. Today the system
improves only when you notice a pattern and say so.

The improvement review notices for you. Once a week, when enough work has finished, it looks across a
project's recent tasks for friction that keeps coming back and brings you at most three concrete fixes,
each saying what keeps happening, where and how often. One tap turns a fix into an ordinary task; once
the fix lands, later reviews show whether that friction came back.

It is judged by one thing: whether what it brings is worth your attention. So it is optional in every
sense: it never wakes your phone, never adds to the Needs you count, lets undecided suggestions lapse
after a week, and keeps its own record (how many suggestions became tasks) beside its switch, so you
can tell when it has stopped earning its place.

## How it works

1. **You choose it per project**, in that project's settings: turn on **Weekly**, or press **Get
   suggestions now**. The first time, a sheet says what is sent, to which model, and how much of your plan
   it uses; it is off until you agree, because it sends parts of the project's conversations to a model.
2. **Weekly means Monday morning.** At the 08:00 morning check each Monday, a review runs if at least
   five tasks have finished since the last one. Otherwise it skips that week silently and costs
   nothing.
3. **Code builds the evidence; a cheaper model reads it.** Altitude counts friction from task records
   and adds short quotes of the conversations around it. A Claude model with no tools reads that
   summary and proposes up to three fixes, each for the project itself or for how the agents work.
4. **L3 checks before you see anything.** It opens the cited records, drops what is wrong or already
   being handled, and posts the rest.
5. **You decide when it suits you.** The suggestions appear as one card in the project's chat and
   under **Suggestions** in Needs you, below anything that does need you. **Make a task** hands one to
   L3 as an ordinary task, with its usual proposal and merge checkpoints; **Dismiss** records that you
   do not want it. Suggestions left undecided for a week lapse.
6. **It closes the loop.** Later reviews say when a pattern you made a task for comes back, and show
   counts before and after once enough work has finished.

Nothing starts work on its own. The reviewer has no tools, files or network beyond its one answer.

## When it runs

| Your question | Answer | Why |
| --- | --- | --- |
| Is it opt-in? | Yes, per project and off by default. Only you can turn on **Weekly** or press **Get suggestions now**, and the first time either shows what is sent and how much of your plan it uses. L3 and task owners cannot turn it on. | It sends conversation quotes to a provider and spends allowance; that is your call for each project. |
| Does it run after every task? | No. Nothing runs per task; each finished task's counts are read from its record when a review runs. | One task rarely shows a pattern. A model call per task would use your plan on every task and post noise. |
| Is it periodic: daily or weekly? | Weekly at most, on Monday mornings at the 08:00 check that already writes the morning digest, so the card is there when your week starts. | A day rarely holds enough tasks to show repetition, and a daily card would ask for attention daily. Monthly would let friction run for four weeks. |
| What if little happened? | A Monday review runs only when at least five tasks have finished since the last review. Otherwise that week is skipped: no model call, nothing posted. | A quiet week costs nothing and says nothing. Five tasks is the least that can show something repeating. |
| Can I run one now? | Yes: **Get suggestions now** in the project's settings, or ask L3 in chat. Same five-task minimum. One review per project runs at a time. | You may want a look before a busy week, or after a change. |
| What if I stop looking? | Nothing piles up. Suggestions never count toward Needs you and lapse after a week; turning **Weekly** off stops the schedule. The settings show how many past suggestions became tasks. | A feature you ignore should cost you no attention and little of your plan, and show you that it is being ignored. |
| What else delays it? | When Claude's weekly allowance is inside Altitude's reserve, the Monday review is skipped and **Get suggestions now** is refused with that reason. It never waits on, or pauses, ordinary work. | Ordinary work comes first. |

## Decisions that are yours

The design takes a recommended answer for each. Approving it approves these; any can be changed
first.

| Decision | Recommendation | Alternatives |
| --- | --- | --- |
| When it runs | Off until you turn it on per project; then Monday mornings when five tasks have finished, plus whenever you ask | A review after every task; daily; monthly; only when you ask |
| What may be sent to the reviewer's provider | Counts from task records plus short paired quotes of the task and project conversations (your message, the agent message it answered, the reply), each cut to 300 characters | Counts only, nothing quoted; or, in a later step, also short excerpts of the agents' working logs |
| Reviewer model and its limit | Claude Sonnet 5.5 at medium effort on your Claude plan: at most 86,000 tokens a review, usually about 25,000, plus one ordinary L3 turn to check. Weekly, far less of your plan than one ordinary task | Claude Haiku 5.5: uses the least of your plan, noisier suggestions |
| Where suggestions reach you | One card in the project's chat and a **Suggestions** section in Needs you, not counted and never alerting | Counted in the Needs you badge; or chat only |
| Proving it works | An eval on fictional projects with planted friction before any real conversation is read (below), on your plan: expected about a third of what this feature's design conversation used, at most about as much; a later rerun after a prompt, signal or model change at most a quarter of that limit | Judge it only by real suggestions and your dispositions |

The conversations being reviewed were already processed by the engines that took part in them, but
sending them again to the reviewer is a new disclosure, so it needs your yes, given per project on the
sheet. Approving a model and limit authorizes the reviews you turn on with it. The eval below is the one
live-provider evaluation this design asks for; it is run by hand, never in CI or unattended, and the
2026-09-08 deferral of live-provider testing otherwise stands. Accepting a
suggestion authorizes its task, never publishing what the review read.

### Directions the independent reviews offered

- **Counts only, the model on tap.** Code counts friction weekly at no cost and with nothing disclosed;
  the morning digest gains one line ("4 signs of repeated friction in 9 tasks · Get suggestions"), and
  the model runs only when you tap. No schedule to explain; a nudge you ignore yields nothing.
- **Counts only, no model.** A small private digest of well-defined event counts with exact links, and
  you ask for tasks in ordinary chat. Nothing is disclosed or spent, but corrections that only show in
  words, and causes that span tasks, stay invisible.
- **Pilot first.** Keep the pilot's conversation sample, swap only its reviewer for the tool-free one,
  and let L3 present candidates in chat before any card or schedule exists.

The rollout takes the pilot-first order (the first step is a chat message on request; the card,
settings and schedule follow once you have seen real suggestions) while keeping the task-record counts
the pilot lacked. The reviews' other proposals are adopted: no friction gate on the schedule, no
self-pausing, suggestions outside the Needs you count and lapsing after a week.

## What it reads

Each review reads one project's own records inside its window (see the run lifecycle): the tasks
that finished since the previous review, at most 30 days back. It needs at least five of them;
otherwise no model is called.

| Source | Used | Why |
| --- | --- | --- |
| Task records: state changes, questions with their revisions and answers, holds, reviews with findings and assessments, landing and CI events, refusals, incidents, token usage, reports | Yes, as counts and anchors | Most patterns are counted facts, cheap and exact |
| Task conversations | Yes, bounded paired quotes | Corrections and unclear replies need the words and what they answered |
| Project chat | Yes, bounded paired quotes | Questions asked twice, corrections given to L3 |
| Agents' working logs (engine session records: tool calls and output) | No, unless you choose it later | Megabytes per task, full of file contents and command output |
| L3's own session logs | No | Its replies are already in project chat |
| CI, validation and terminal logs, images, voice audio | No | Large; the failing check is already an event |
| Other projects | Never | A run is confined to its project |

### Building the prompt

Code builds the prompt; no summarising model call adds cost or mistakes. The whole prompt stays
within 64 KiB, as the pilot's does.

1. **Signals.** A fixed, versioned catalog of counted predicates per task (below).
2. **Task sheets**, ranked by their signals and added in that order until 48 KiB, each at most 3 KiB:
   title, outcome and durations; signal counts, each with the anchors that produced it and what each event recorded (its reason or
   error, or what verification found in a replaced report); each operator
   message as a paired excerpt (the message it answered, the operator's words, the next reply, 300
   characters each, at most four pairs); every question on one line with its revisions, latest first
   sentence and how it was answered or resolved and by whom; review findings' titles with their
   dispositions; and the report's verdict. A sheet is whole lines: the operator's words come before
   questions and findings when room runs short, and a last line counts what did not fit. Sheets are
   built from finished tasks, so later corrections are inside them.
3. **Coverage.** Tasks that did not fit are listed by title and counts, and the prompt says what was
   left out. Project chat adds paired excerpts the same way within the remaining room.
4. **Context.** Titles of current tasks and of earlier suggestions with their outcome, so the reviewer
   does not repeat dismissed or existing work.

The reviewer returns at most three proposals, each with: target, title, what was observed (with
anchors), what would change it and where, the signal it would reduce (or none), and its uncertainty.

### Signals

Each signal is an exact predicate over existing records, versioned so measurements only compare like
with like. The card names the signal itself, never a narrower claim the predicate cannot tell apart.

| Signal (card wording) | Predicate |
| --- | --- |
| Questions re-asked without your input | A question's new revision with no operator message to that task since its previous revision |
| Approvals refused | `merge-approval-refused` and `grant-refused` events |
| Your messages after a report | Operator messages on a task after its owner reported, before it closed |
| Review rounds beyond the first | `review-requested` events beyond the first per subject (proposal, changes) |
| Review findings open at completion | Findings the owner's final report leaves `open` |
| CI rechecks | `ci-recheck` events (a delivery is recorded for every pushed head, so pushes never count) |
| Resume or dispatch failures | `resume-failed` and `dispatch-failed` events and system faults |
| Reports rewritten after verification contradicted them | `report-superseded` events whose replaced report verification had contradicted (a message continuing a reported task supersedes its report, so other supersessions are ordinary) |
| Escalations | `escalated` events |
| Hours waiting on you | Time a task's open question was the operator's turn |
| Tokens used | The task's recorded token totals (not a cost) |

"Your messages after a report" counts messages, not corrections: whether a message corrected
something is the reviewer's reading, shown as such.

## The two targets

Every suggestion names exactly one target, chosen by where its change would be made:

- **The project** (chip shows the project's name): its product, code, tests, CI, docs or how its tasks
  are briefed. Leads to a project task or issue.
- **How agents work**: how agents coordinate everywhere: persona instructions, Altitude's role rules,
  its commands, and its question, review and landing mechanics. Leads to an instruction or code change
  to Altitude, under the existing holds for guard and rule changes.

The suggestion names what to change and where (for example "the owner instructions' merge-approval
paragraph"); one whose stated change is outside its target is dropped. On the Altitude project both
targets are the same repository; the chip still tells you whether the change is to the product or to
how agents work.

### Evidence without copying transcripts

Suggestions cite anchors the code placed in the prompt (a question revision, a message, an event),
never quoted text; citations not in the prompt are dropped. In the card each citation opens its exact
place: a question opens the task conversation at that question revision, a message at that message,
an event at the task's Activity & evidence. Anything published later (an issue, a PR, a commit)
describes the pattern in general terms and names the source only as "improvement review of <date>,
suggestion <n>"; quotes and private details stay in the run's private record, under the existing
publication rules.

## The reviewer

One fresh call per run through the engine seam (`engines/`), using the isolation Altitude already
uses for its quota read: `--safe-mode` (no CLAUDE.md, skills, plugins, hooks or MCP),
`--no-session-persistence`, `--tools ""`, `--strict-mcp-config`, an empty temporary working directory,
no added directories and a JSON response schema. The adapter fails closed: if the session's own start
record lists any tool other than Claude Code's carrier for the schema-bound answer, reports no
model or reports an API key in use, the run stops before its answer is used. The call runs without
`ANTHROPIC_*` variables, so it always uses the signed-in plan.

**Runs on your plan.** Every call runs through the Claude Code login on your subscription, like every
task; nothing uses the API or an API key, and nothing is billed. Claude Code also reports each call's
size as what it would cost at API prices. Altitude uses that figure only as a yardstick for limits and
records; the interface shows tokens and never shows dollars.

**Usage limit.** With no tools there is exactly one model request, so the limit is set by its size, not
watched afterwards. Everything sent (the reviewer's instructions, which replace the engine's default
system prompt, the answer schema and the 64 KiB prompt) is at most 72 KiB, counted conservatively as
one token per byte: at most 74,000 input tokens, though typical text is about a quarter of that. Output,
including reasoning, is capped at 12,000 tokens through the engine's output-token setting, so a run uses
at most 86,000 tokens. As a second stop, Claude Code's own budget option is set to that same maximum in
its yardstick; a model the engine seam has no figures for cannot be chosen. These bounds are tested as
arguments with a scripted engine; that the provider honours them is confirmed by the first real run's
recorded usage. Details shows the tokens used beside the limit, and a run that ever exceeds it says so
on its line. The models use different amounts of your plan for the same tokens:

| Model | Plan use for the same tokens | What is lost |
| --- | --- | --- |
| Claude Haiku 5.5 | About a twentieth of Sonnet's | Weaker judgement: more obvious or duplicate proposals, more wrong targets for L3 to drop |
| **Claude Sonnet 5.5 (recommended)** | The reference | Close to the owners' model on reading and attribution; misses some subtle cross-task causes |
| Claude Opus 5.5 (the owners' model, for comparison) | About twice Sonnet's | Not lighter; listed as the baseline |

The ratios follow Claude's published prices. The limit covers the reviewer. L3's check is one ordinary
coordinator turn, like answering a chat message, on L3's usual model; its usage is recorded in the run
and shown separately in Details. A weekly review on one project uses far less of the plan than one
ordinary task: a task routinely reads millions of tokens, a review at most 86,000. Codex is not
offered: Altitude runs Codex with a command sandbox that can still read the disk, and no tool-free
Codex mode has been verified. If the chosen model is unavailable the run fails and says so; it never
switches model without your recorded decision.

## Run lifecycle

- **Agreement.** A project's reviews need your agreement to the sheet's current text, recorded with
  who, when and the text's version. A later change to what is sent, the model or the limit is a new
  version: **Weekly** turns off, and the next **Weekly** or **Get suggestions now** shows the new sheet.
  Chat names that change in one system line: **Improvement suggestions are off until you agree to the
  new terms in settings.**
- **Start.** Three ways, each refused without agreement, while another review of the project runs, with
  fewer than five tasks finished since the last review, or with Claude's allowance inside Altitude's
  reserve:
  - **Weekly**, by the morning check at 08:00 on Monday in the machine's local time zone. Each week,
    Monday to Sunday, has at most one scheduled run, claimed durably before it starts. If Altitude was
    down at 08:00, the first check later that same week runs it; missed weeks are never caught up, and
    turning **Weekly** on mid-week first runs the next Monday. Its authority is your recorded switch
    and agreement. A week whose run fails a condition is skipped and recorded; nothing is posted.
  - **Get suggestions now** in the project's settings: your own authenticated press, sent with a
    request id; a repeat with the same id returns the same run.
  - **In chat**: you ask L3, and L3 starts it with `alt improve run` while answering that message.
    Altitude takes the message from the running chat turn, checks it is yours, in that project's chat,
    and returns the same run when it already started one. A refusal comes back as
    L3's reply with the reason. L3 cannot start a review without your message, and cannot agree or
    switch **Weekly** on.
- **States.** Reading (the reviewer call), checking (L3's turn), then exactly one ending: posted,
  nothing to suggest, stopped or failed. A run has one deadline, two hours from its start, covering
  both reading and checking.
- **Stop.** Works while reading or checking: **Stop** on the running line in chat or settings, or "stop
  the suggestions" in chat (L3 runs `alt improve stop`). A stopped run's answer is discarded and never
  posted.
- **What else ends a run.** Reading: its timeout, an unavailable model, an isolation check that fails,
  or an answer that does not validate. Checking: L3 not posting before the deadline, whether its turn
  failed, was interrupted or is still queued; the candidates stay in the private record. Each ending is
  recorded and shown; nothing retries automatically. A run interrupted by a restart becomes uncertain
  after its deadline, without replay, as in the pilot.
- **Window.** Each review reads tasks that finished after the previous review's window ended (for the
  first, the last 14 days), never before 2026-09-22 07:00 UTC. Tasks older than 30 days are not read;
  Details names how many. A review that ends with nothing to suggest still advances the window; a
  stopped or failed one does not.
- Ordinary work is never paused by a run, a failure or a pending suggestion.

## Turning it on: the settings section

A new **Improvement suggestions** section in the project's settings (`/settings/projects/<name>`),
between **Routing** and **Project**, follows the page's existing form: heading, one muted line, a card.

- Heading line: **Finds friction that keeps coming back in this project's tasks and suggests fixes.**
- **Weekly**: a switch, saved in place like the Terminal switch. Its line:

| State | Line under **Weekly** |
| --- | --- |
| Off | **Every Monday morning, when at least 5 tasks have finished.** |
| On | **Next: Monday, Oct 19.** With fewer than five tasks so far: **Next: Monday, Oct 19, if 2 more tasks finish.** |
| On, last Monday skipped | **Skipped Oct 12: 3 tasks had finished. Next: Monday, Oct 19, if 2 more tasks finish.** |
| Running | A working dot, **Reading 9 tasks** and an underlined **Stop** |
| Off by a change of terms | **Off: the terms changed. Turn on to see them.** |

- **Get suggestions now**: a secondary button with the line **Reads the 6 tasks finished since Oct 12.**
  While a review runs, the button is replaced by the running line above, so only one place shows it.
  With fewer than five finished tasks, or the allowance inside the reserve, it looks disabled; its
  reason (**Needs 5 finished tasks since Oct 12; 3 so far.** or **Claude's weekly allowance is inside
  Altitude's reserve.**) is its accessible description and appears under it on press, as SPEC §3.8.1
  does for an unavailable alert switch.
- **What is sent**, muted, no control: **Counts and short chat quotes · Claude Sonnet 5.5 on your plan ·
  capped per run, plus one L3 check**.
- **Past suggestions**, muted, once there is one run: **4 runs · 7 suggestions · 3 became tasks · 2
  dismissed**, and an underlined **Latest** that opens the latest card in the project's chat.

**The sheet.** Turning **Weekly** on, or pressing **Get suggestions now**, without agreement to the
current terms opens a sheet before anything is saved or run:

- Title: **Turn on improvement suggestions for altitude?**
- Body: **Claude Sonnet 5.5 reads counts from this project's tasks and short quotes from its chats (up
  to 300 characters each). It runs on your Claude plan, capped at far less per run than an ordinary
  task; one ordinary L3 turn then checks what it finds. With Weekly on, this happens Monday mornings
  once 5 tasks have finished. Nothing is published, and nothing becomes work until you choose it.**
- Buttons: **Turn on** (primary) and **Cancel**. **Turn on** records the agreement, then completes what
  was pressed (the switch turns on, or the run starts). Cancel or Escape changes nothing.

A switch change shows "Saving…" under the section with its controls disabled; a refusal shows the reason
in red under the section and the switch returns to the saved value, as the page's other settings do. On
the phone the sheet is a bottom sheet with full-width buttons.

Weekly and the agreed terms version live in the project's improvement record beside its runs, so the
Monday claim reads the switch and the agreement under the same lock that records the run. Unlike
routing, only the operator can change them, with the web request from a paired device; no CLI or
coordinator verb does, so L3 and owners cannot. Each change is recorded with who and when.

## Checking and posting

When the reviewer's answer validates, Altitude queues one L3 turn with the proposals as untrusted
candidates. L3 opens each cited anchor, checks current tasks, issues and earlier dispositions, and
posts what survives with `alt improve post <run> --keep <n,…> --drop <n>:<reason>`. Altitude, not L3,
writes what is posted, from the reviewer's validated wording. Suggestions keep their run numbers, so
dropping suggestion 2 shows 1 and 3. The posted row is the run's presentation receipt: its id, the
numbers shown and the date the run is named by ("the review of Oct 12"). `post` refuses a run that is
stopped, ended or past its deadline; repeating the same post returns the same receipt; a different post
for a posted run is refused. Dropped proposals and L3's reasons appear under **Details**. If L3 keeps
none, the run ends with the nothing-to-suggest line.

## Where suggestions appear

One card per review, in the project's chat in conversation order. While any of its suggestions is
undecided, the same card also appears in Needs you, in a muted **Suggestions** section after
everything that needs you, grouped under project headings like the rest of the page. It is not a
question: it never alerts a device, never adds to the Needs you count or badge, and never changes the
summary line. With no questions, held reviews or operational items anywhere, Needs you says **Nothing
needs you.** with the **Suggestions** section below it, and the badge stays hidden.
Deciding in either place updates both. Two cards on one project stand newest first.

A suggestion lapses seven days after its card posted, and only then: it folds to **Not decided**. A card
leaves Needs you when nothing on it is undecided and no accepted suggestion is still waiting for its
task; the last card leaving leaves **Nothing needs you.** alone, or the empty page as before.

### States and exact text

| State | What appears |
| --- | --- |
| Running | System line with a working dot: **Improvement suggestions · reading 14 tasks from Oct 5–12** and **Stop** (underlined). No Show. A Monday review shows the same line; nothing appears in Needs you until it posts. |
| Stop pressed | **Stop** becomes a spinner in place; then the line reads **Improvement suggestions stopped by you**. If the stop cannot be saved: **Not stopped. Try again.** after the line, and Stop returns. |
| L3 checking | The same line reads **Improvement suggestions · L3 is checking 3**, with **Stop** as while reading. |
| Finished with suggestions | The card (below). |
| Nothing to suggest | System line **Improvement suggestions · 14 tasks from Oct 5–12 · nothing to suggest** and **Show** (the details, including any L3 drops). |
| Not started | A Monday skip shows only in settings. **Get suggestions now** is disabled with its reason. A chat request gets L3's reply with the reason. No line, no model call. |
| Failed | System line with the danger dot: **Improvement suggestions failed · <reason>** and **Show**. Reasons: "it went over its usage limit", "Claude Sonnet 5.5 is unavailable", "it could not run isolated", "its answer was not usable", "L3 did not finish checking", "it was interrupted by a restart". |
| Terms changed | System line **Improvement suggestions are off until you agree to the new terms in settings.** with an underlined **Settings**. |

### The card

- Header: **Improvement suggestions** and its time (system-line format); below, muted, **<N> tasks from
  <window>**. The card's date names it in typed answers ("the Oct 12 suggestions"). The number of suggestions is not written. In Needs you the header is an underlined link
  to the card in the project's chat.
- Each suggestion, separated by a hairline: target chip (**How agents work**, or the project's name);
  title, 15 px semibold, at most 90 characters, wraps, never cut, after its run number (**1.**, **3.**:
  the same numbers as the first step's message, kept when L3 drops one); explanation, at most 300 characters:
  what happens and what would change it, wraps fully.
- **Seen in**: up to three underlined links, each a task title opening its cited place (question,
  message or event). More citations show **<n> more** (underlined), which expands the rest in place.
- The count, muted, in the signal's own words: **<Signal> in <n> of <N> tasks**, e.g. **Questions
  re-asked without your input in 3 of 14 tasks**; absent when the suggestion has no signal.
- **Make a task** (bordered, accent text) and **Dismiss** (ghost beside it). No filled buttons, so the
  card never competes with a question above it.
- Footer: an underlined **Details**, opening in place: what was read and left out, what L3 dropped and
  why, the model, the reviewer's tokens against its limit, the check's tokens, and when it ran.

| Action or state | What appears and disappears |
| --- | --- |
| Make a task pressed | Its label becomes a spinner, same width; Dismiss dims. |
| Accepted | Buttons leave; a check and **Accepted · L3 is opening a task**. The card stays in Needs you until the task exists. |
| Task not opened | If L3's turn fails, is interrupted or ends without a task: **Accepted · no task yet** and an underlined **Try again**, which queues the same single turn; one task per suggestion still holds, so no retry can make a second. |
| Task opened | That line becomes the ordinary compact task card (SPEC §3.5); if L3 attached it to an existing task or issue instead, the line names and links that. |
| Dismiss pressed, then dismissed | Spinner in place; then the suggestion folds to its chip, muted title, **Dismissed** and an underlined **Undo**. |
| Undo pressed | The suggestion opens again with both buttons. Undo is offered until the suggestion lapses. |
| Save failed | Buttons return; under them in danger text **Not saved. Try again.**, gone on the next press. |
| Typed answer | You reply in chat; L3 records it citing your message and the card changes the same way. |
| Lapsed | The suggestion folds to its chip, muted title and **Not decided**; the card leaves Needs you once nothing on it is undecided. |
| Pattern found again after a task for it | A muted **Seen again after <task>** line under the explanation. |
| Later run, enough work since a change | The card opens with **Since earlier changes**: one row per accepted suggestion with a signal, e.g. **Questions re-asked without your input: 3 of 14 tasks before, 0 of 11 since**, linking the task that made the change. |
| Conversation loading, read failure, listening, microphone denied | Unchanged (SPEC §3.3, §3.6); the card asks for no permission. |

Phone (390 px) and desktop use the same card in the conversation column and in Needs you; on the phone
it is full width, the two buttons stay side by side at 44 px height, and all text wraps. Each step's
PR walks every state above at both widths.

## Decisions on suggestions

A suggestion is undecided, accepted, dismissed or not decided, with who, when and from what for each
change. The allowed changes:

| From | To | How |
| --- | --- | --- |
| Undecided | Accepted or dismissed | A button, or your typed answer |
| Undecided | Not decided | Seven days after posting |
| Dismissed | Undecided | **Undo**, until it would have lapsed |
| Dismissed or not decided | Accepted | Your typed answer naming it ("make a task for the Oct 12 merge-approval one") |
| Accepted | (none) | Final: reject or stop its task instead |

- **Buttons.** A press is the operator's own authenticated act, sent with a request id; a repeat with the
  same id returns the same receipt. A press the table does not allow is refused and the card refreshes.
- **Typed answers.** `alt improve resolve <run>/<n> accepted|dismissed --reason '…'`, run while L3
  answers it, requires the operator's own project-chat message sent after the run's presentation receipt, and a
  number that receipt shows. When the answer does not say which run it means and more than one could
  fit, L3 asks rather than guesses. L3 records the scope it read from that message (for example "owner
  instructions only"); that message, not the suggestion, is the new task's source authority. The same
  message and outcome return the existing receipt; a change the table does not allow is refused, and L3
  replies with the suggestion's current state.
- **One task per suggestion.** `alt task new --improvement <run>/<n>` refuses a suggestion that is not
  accepted or already has a task. Acceptance queues exactly one L3 turn.
- Not decided suggestions never become work on their own; a later review offers one again only with new
  evidence.

## Closing the loop

Every later review lists accepted suggestions in its prompt. When the same pattern returns with new
evidence after the task for it finished, the card says **Seen again after <task>**.

Before and after counts come only once there is enough work to mean something: both sides count
finished tasks with the same predicate version, before being the tasks in the window of the run whose
suggestion was accepted (saved at acceptance), and since being the tasks started after the linked change
merged that have finished, shown once there are ten. These are observed counts, not proof of cause,
and the card says nothing more than the counts.

The settings section's **Past suggestions** line is the feature's own measure: how many suggestions
became tasks and how many you dismissed.

## Proving it works

Before the review reads any real conversation, it runs against fictional projects whose friction is
known, to show what it catches, whether what it suggests is right, what it invents, and which model is
worth its cost. This is a small eval: a fixed set of scenarios, an answer key, a grading rubric and a
table. It shows the review can find planted friction and stay quiet when there is none; with this few
scenarios it is a smoke test, not proof that it generalises.

**Scenarios.** Each scenario is a fictional project of 8 to 20 finished tasks, written as a compact
scenario file that the loader turns into ordinary task and chat records. The eval therefore runs the
same signals, sheets, prompt and reviewer as a real review; nothing in the review knows it is an eval.
A scenario holds:

- **Planted patterns**, each with its key: the anchors it was planted in, its target, the problem and
  the change that would remove it. Three kinds:
  - *Counted*: friction a signal fires on, such as a question re-asked without the operator's input
    in three tasks, or the same check rerun in four.
  - *Words-only*: friction no signal counts, such as the operator correcting the same kind of brief in
    different words across three tasks, placed in quiet tasks that compete with louder ones for room
    in the prompt.
  - *Shared cause*: different symptoms with one cause across tasks, beside a plausible wrong
    explanation that the evidence rules out, such as owners asking how to run the tests and CI failing
    on the command they guessed, while the CI runner was also slow that week.
- **Decoys** that must not be suggested: a one-off that looks like a pattern, a re-asked question the
  operator did answer in between, a pattern a current task already handles, one dismissed in an
  earlier review, and friction that was justified (a hold the operator asked for).

There are ten scenarios: six with one to three planted patterns among decoys (about fifteen patterns,
at least a third words-only and three shared-cause), two of decoys only, and two clean projects with
nothing to suggest. Two of the planted scenarios are larger than the prompt's room, so some evidence is
cut; the key records which planted anchors reached the prompt, and a pattern whose evidence was cut is
reported as a loss in the prompt builder, not counted against the model. A third set holds three
projects at a real project's volume (a control and two planted), with ordinary pushes, continued
reports, second reviews and long conversations at the rates real tasks show, so ordinary work fires
signals and fills the prompt beside the planted friction. All of it is fiction, written to the
repository's rules for examples.

**Grading.** Code checks what it can; judgement is graded against a rubric:

- Code validates each answer, checks that every citation is an anchor the prompt contained, and
  proposes matches: each suggestion is paired with at most one planted pattern, by the most shared
  planted anchors.
- Every suggestion is then graded on the rubric: is the observation supported by its citations, is the
  cause and target right, and would the stated change remove the friction? A suggestion matched to a
  pattern and passing all three is **caught**. One that fails, cites decoys, repeats handled or
  dismissed work, or appears on a clean project is **wrong**. One matching no pattern but passing the
  rubric is **unplanted but sound**, since fiction can hold friction nobody planted.
- The grader is a helper that did not write the prompt and sees each answer with its model's name
  replaced by a code. The owner checks every case where the grade disagrees with the code's match, and
  the table links a sample of graded suggestions for you to check.
- **Counts alone** is a column beside the models: for each pattern, whether a signal fired on its
  anchors. It costs nothing and shows what the model adds over counting.

**Honest numbers.** A separate helper writes the scenarios from a brief naming only the pattern kinds,
the keys sit in their own files, and the set is split in two halves that each hold a clean project, a
decoys-only project and three planted ones with words-only and shared-cause patterns. One half is for
developing the prompt (on Sonnet only); the other is held back and run once at the end for the
reported numbers. If the prompt is revised after the held-back run, the next reported run uses fresh
held-back scenarios written the same way, since the old half has become tuning data. Results show
counts ("caught 5 of 7"), not percentages.

**Runs and plan use.** The held-back half runs three times on each model the engine seam offers the
reviewer (today Claude Haiku 5.5, Sonnet 5.5 and Opus 5.5), because one prompt can give different
answers: 45 calls. With prompt development (at most 20 Sonnet calls), the eval runs on your Claude plan
like other work. In plan terms it is expected to use about a third of what this feature's design
conversation used, and at most about as much: in Claude Code's API-price yardstick, about $6 expected
and an $18 ceiling. Before every call the harness adds that call's maximum to what is used and stops if
it would pass the ceiling; an eval stopped short is reported as incomplete, never as passing.

**The bar** for reading real conversations, on the held-back half, per model:

- In each of the three runs, it catches at least four in five counted and shared-cause patterns whose
  evidence reached the prompt, and at least half the words-only ones.
- Across all runs, its clean project and decoys-only project get no suggestion graded wrong.
- Elsewhere, at most one suggestion in six is wrong.
- At most one of its fifteen answers fails validation; a failed answer counts as a run that caught
  nothing.

The recommendation is the model using the least of your plan that clears the bar; Sonnet stays the
default only if Haiku does not. If none clears it, you get the results instead of the next step, and
decide whether the prompt is revised and measured again on fresh held-back scenarios. The
controls are reported on their own rows, so a quiet failure on them is never averaged away.

**What it does not prove.** Fiction is cleaner than real work, and fifteen patterns are few. Your
decisions on the first real suggestions are the measure that counts.

**Afterwards.** The scenarios, keys and matcher stay in the repository. `make check` runs the offline
eval with no model: every scenario loads with a consistent key and builds a bounded prompt its planted
patterns reach, and in a signals project beside ordinary activity each signal fires on exactly its
planted places and on nothing ordinary, and reaches its own task's sheet under its alias, where the
operator's words are still quoted. The code's matching and validation are tested with scripted
answers. The live eval runs only by hand, by the owner of a later change to the prompt, signals or
model, on the held-back and real-volume sets and the chosen model: on Sonnet at most a quarter of the
eval's ceiling. Its table, with results per signal, goes in that PR, and a failure it finds becomes a
new scenario.

**Its own small harness.** What is under test is Altitude's prompt builder and its tool-free call
through the engine seam on the plan. Open-source eval frameworks such as Inspect or promptfoo would
need an adapter around that same call and add a dependency, for a few dozen calls graded by a rubric.
The harness is a scenario loader, a loop, the matcher and a grading sheet. If evals later need many
more models or scenarios, Inspect is the one to adopt.

## Where it lives

- `~/.altitude/<project>/improvements/<run>/`: the exact prompt, the raw answer and the run record
  (usage, model, isolation check, coverage, validation drops, L3 drops). Private, never published,
  archived like task records.
- `<project>/improvements.json`: runs, suggestions, dispositions with their source (button request or
  message), linked tasks, baselines, later counts and the Monday skips.
- The setting lives with the project's other settings; its changes and the agreed terms version are in
  the project's settings events.
- The card is a project-chat row (`trigger: "improvement"`) naming its run; its state is read from
  `improvements.json`, so the conversation stores no second copy of a decision.
- The eval: scenarios and keys in `tests/fixtures/improvement/`, the loader, loop and matcher beside
  the review; each live eval's answers and grades in the running task's folder, its table in its PR.
- This covers the Altitude project first. Other projects come later; their How agents work suggestions
  will then appear in the Altitude project's chat and Needs you, where Altitude itself is changed.

## Safety

- The reviewer: tool-free isolation checked on its own start record, empty working directory, schema-
  bound answer, token caps and a budget at the computed maximum, timeout. No network but its provider.
- Its answer is untrusted: bounded, validated, citations must come from its prompt, secrets and local
  paths refused, rendered as plain prose with only Altitude-made links. L3 checks before you see it.
- It writes nothing. Altitude writes the records; L3 acts only after your acceptance, through ordinary
  tasks. Nothing auto-dispatches.
- Reading transcripts is reserved to this review: the L2 and L3 personas say no agent reads session
  logs or other tasks' conversations ad hoc, except L3 opening a run's cited anchors and an owner
  reading its own task's logs to diagnose its own work.
- Tests use fictional project records and a scripted engine at the seam, without providers: each signal
  predicate and its version, paired quotes and their cuts, sheet ranking and the 64 KiB bound,
  coverage, dropped citations, oversized and secret-bearing answers, the isolation check failing closed,
  token caps and budget arguments, unknown prices refused, timeout, unavailable model with no fallback;
  start authority (no agreement refused; the Monday check with its five-task and allowance conditions,
  the weekly claim, a run later in the week after downtime, no catch-up across weeks, turning Weekly
  on mid-week, recorded skips; Get suggestions now with request-id replay; a chat
  message, reuse refused; L3 alone refused; L3 and owners refused when switching or agreeing; changed
  terms turning Weekly off with its chat line), the window advancing and its 30-day limit, Stop while
  reading and while checking, the check deadline, interrupted runs, posting with L3 drops and kept
  numbering, post refused for stopped, ended or late runs and replayed for the same post, typed answers
  before the receipt, for a dropped number, delayed past a later run, ambiguous across runs and
  replayed, every allowed and refused change in the decisions table including Undo and late
  acceptance, button replays and conflicts, lapsing at seven days only, an accepted suggestion whose task turn fails and its Try again,
  suggestions-only Needs you with the badge hidden, one task per
  suggestion, measurement cohorts after merge, the Needs you Suggestions section leaving the count and
  badge unchanged, and every chat, Needs you and settings state walked on phone and desktop. The
  eval's scenarios load, their counted patterns fire and decoys do not, the key records what reached
  the prompt, and matching, validation and the usage stop handle scripted answers correctly.

## What is removed

The conversation-audit pilot was never started on this installation. The improvement review replaces
it: `audit.json` and its seven-day/fourteen-attempt scheduler and hard-coded project, the twice-daily
trigger in the daemon tick, `engines.conversation_review` (which ran with the coordinator's tools), the once-per-turn
injection of candidates into L3's prompt, `alt audit`, the pilot sections of the architecture, session
lifecycle and CLI docs, and the L3 persona's "conversation-review candidates" paragraph. Carried over
and covered by the same tests: the 64 KiB prompt, the 2026-09-22 cutoff, private prompt and answer
files, a durable claim before the provider call, uncertainty without replay, and the engine-seam model
choice with recorded usage and cost.

## Rollout

Each step is its own held PR that you review before it lands. Nothing reads a real conversation until
the eval has shown a model that clears the bar, and Monday runs come only after you have seen real
suggestions on request.

1. **The reviewer, proven on fiction (this week).** `improve.py` with the signals, task sheets and
   prompt; the tool-free reviewer with its isolation check and usage limit; the scenarios, keys,
   matcher and grading sheet; the pilot removed. The live eval runs within the limit you approve, and the PR carries its
   table and a model recommendation. No real conversation is read. About three to four days: the
   signals and sheets are the largest part, the scenarios about a day.
2. **First run on request.** Start by your chat message, Stop by chat, the deadline and the private
   record. Your approval of this design is the agreement for the Altitude project only; the settings
   section does not exist yet. L3 checks the candidates and runs `alt improve post`, which posts them as
   an ordinary L3 chat message written by Altitude: a heading line naming the review and its window,
   each kept suggestion with its run number, target, title, explanation and an underlined link to every
   cited place (opening like any task link in chat; Back returns to the conversation), and the dropped
   numbers with L3's reasons. Endings are one short message each with the card's wording. You answer
   in words; L3 records them with `alt improve resolve` against that message. No new screen or
   component. Docs: the pilot's architecture, session lifecycle, CLI and persona sections are rewritten
   for the review. Acceptance: one real run on this project with its record, usage and your
   dispositions.
3. **The card and Needs you.** The same post renders as the card in project chat and under
   **Suggestions** in Needs you: the running line with Stop, Make a task, Dismiss and Undo, Details,
   lapsing, every state walked on phone and desktop.
4. **Settings and Mondays.** The settings section with **Weekly**, **Get suggestions now**, the sheet
   and its recorded agreement, the Monday check and its skips. From here a chat request needs the
   agreement.
5. **Measurement.** **Seen again**, **Since earlier changes** and the **Past suggestions** line.
6. **Only if you choose them:** other projects (their How agents work suggestions then appear in the
   Altitude project's chat and Needs you, where Altitude itself is changed), working-log excerpts, and
   the later directions below.

### Later directions

- **Owners noting friction.** An owner's report gains an optional line for friction it hit and could
  not fix in its task (a confusing rule, a tool that failed). The next review reads those lines beside
  the counts, at no extra model cost, and the eval gains scenarios for them. Recommended first, since
  owners see friction no record shows.
- **L3 starting a review on its own judgment**, for example after an incident. Not recommended: it
  would use your plan and send quotes without your yes for that run, and **Weekly** already covers the
  schedule. Asking L3 in chat already starts one (step 2).

## Settled by the owner (redirect any of these)

- Code builds the prompt from counted predicates and paired quotes; no model summarises transcripts.
- The reviewer has no tools at all, not read-only tools.
- At most three suggestions per review; at most one scheduled review per project per week, on Mondays
  at the existing 08:00 morning check. No quiet-hours setting and no weekday choice.
- Five finished tasks is the minimum for any review; there is no friction threshold, because the signal
  counts are evidence for the reviewer, not a gate.
- L3 checks proposals before you see them, because the reviewer cannot open records or see current
  work; this costs one ordinary coordinator turn per review.
- Suggestions never wake a device or count in Needs you, and lapse after seven days.
- The feature is called Improvement suggestions in the interface.
- The pilot is removed, not kept beside the new review.
- The eval is Altitude's own small harness: code validates and matches, a blind helper grades against
  a rubric, and a held-back half gives the reported numbers.
