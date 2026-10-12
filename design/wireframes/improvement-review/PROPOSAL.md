# Improvement review: proposal

Status: proposal awaiting the operator's decision. Nothing here is built yet.

## What it is for

Working with agents leaves friction behind. You answer something you already settled, correct the
same kind of brief twice, wait on a flaky check, or watch a review re-report findings already dealt
with. Each instance is small and easy to forget; the repetition is what costs you. Today the system
improves only when you notice a pattern and say so.

The improvement review notices for you. Once a week, when enough work has finished, it looks across a
project's recent tasks for friction that keeps coming back. It brings you at most three concrete fixes,
each saying what keeps happening, where and how often. One tap turns a fix into an ordinary task, and
once that fix lands, later reviews show whether the friction went down.

It is judged by one thing: whether what it brings is worth your attention. So it stays silent when it
has nothing worth bringing, never wakes your phone, pauses itself when you stop deciding, and keeps its
own record (how many suggestions became tasks) beside its switch.

## How it works

1. **You turn it on for a project** in that project's settings: **Weekly**, or **When I ask**. It is
   off until you do, because it sends parts of the project's conversations to a model and spends.
2. **It runs when there is something to look at.** A weekly review runs on the first morning a week
   after the previous one, once at least five tasks have finished and at least one of them shows
   counted friction. Otherwise it waits, costing and posting nothing. **Review now** runs one at once.
3. **Code builds the evidence; a cheaper model reads it.** Altitude counts friction from task records
   and adds short excerpts of the conversations around it. A Claude model with no tools reads that
   summary and proposes up to three fixes, each for the project itself or for how the agents work.
4. **L3 checks before you see anything.** It opens the cited records, drops what is wrong or already
   being handled, and posts the rest.
5. **You decide, when it suits you.** The suggestions appear as one card in the project's chat and as
   one item in Needs you, without an alert. **Make a task** hands one to L3 as an ordinary task, with
   its usual proposal and merge checkpoints; **Dismiss** records that you do not want it. Suggestions
   you leave undecided step aside when the next review arrives.
6. **It closes the loop.** After a fix lands, later reviews show the counted friction before and after.

Nothing starts work on its own. The reviewer has no tools, files or network beyond its one answer.

## When it runs

| Your question | Answer | Why |
| --- | --- | --- |
| Is it opt-in? | Yes, per project and off by default. Only you can turn it on, in the project's settings, after a sheet that says what is sent, to which model, and the cost limit. L3 and task owners cannot. | It sends conversation excerpts to a provider and spends allowance; that is your call for each project. |
| Does it run after every task? | No model call per task. Each finished task's counts are taken from its record when a review runs. | One task rarely shows a pattern. A call per task would spend on every task and post noise. |
| Is it periodic: daily or weekly? | Weekly at most, never daily. A weekly review becomes due seven days after the previous one started (or after you turned weekly on), and runs at the first morning check from then, at 08:00 local time beside the morning digest, so its card is there when your day starts. | A day rarely holds enough tasks to show repetition, and a daily card would ask for attention daily. Monthly would let friction run for four weeks. |
| What if little happened? | A due review runs only when at least five tasks have finished since the last one and at least one shows a counted signal. Otherwise it waits, checking each morning, with no model call and nothing posted. | A quiet week costs nothing and says nothing. |
| Can I run one now? | Yes: **Review now** in its settings, or ask L3 in chat. It needs five finished tasks but no counted signal. Only one review per project runs at a time. | You may want a look before a busy week, or after a change. |
| Does it ever stop itself? | Weekly reviews pause after two reviews in a row whose suggestions all went undecided. The settings say so, and **Resume** restarts them. | A review you are not reading should not keep spending or keep an item in Needs you. |
| What else delays it? | When Claude's weekly allowance is inside Altitude's reserve, a due review waits for the next morning. It never waits on, or pauses, ordinary work. | Ordinary work comes first. |

The three settings are:

- **Off** (the default): nothing is reviewed or sent; L3 says where to turn it on if you ask for a
  review.
- **When I ask:** runs only from **Review now** or a chat request.
- **Weekly:** also runs on the schedule above.

Its section in the project's settings shows the setting, when the next review will run (or why it is
waiting), **Review now**, what is sent and the limit, and the record of past reviews.

## Decisions that are yours

The design takes a recommended answer for each. Approving it approves these; any can be changed
here first.

| Decision | Recommendation | Alternatives |
| --- | --- | --- |
| When it runs | Off until you turn it on per project; then weekly when enough work has finished, or only when you ask | A review after every task; daily; a fixed weekly run whatever happened; monthly |
| What may be sent to the reviewer's provider | Counted facts from task records plus short paired excerpts of the task and project conversations (your message, the agent message it answered, the reply), each cut to 300 characters | Counted facts only, nothing quoted; or, in a later step, also short excerpts of the agents' working logs |
| Reviewer model and cost limit | Claude Sonnet 5.5 at medium effort: at most $0.27 a review, usually $0.08–0.12. Weekly, that is about $0.40–0.50 a month per project, at most $1.20 | Claude Haiku 5.5: at most $0.02 a review, noisier suggestions |
| Where suggestions reach you | One card in the project's chat and one item in Needs you, counted in its badge, with no alert | Chat only (easy to miss once it is weekly); or Needs you with an alert |

The conversations being reviewed were already processed by the engines that took part in them, but
sending them again to the reviewer is a new disclosure, so it needs your yes, given per project when you
turn reviews on. Approving a model and limit authorizes the reviews you turn on with it; it does not
start a separate model-quality evaluation, which stays deferred under the 2026-09-08 decision. Accepting
a suggestion authorizes its task, never publishing what the review read.

### Directions the independent reviews offered

- **Counted facts only.** A small private digest of well-defined event counts with exact links, no
  model at all, and you ask for tasks in ordinary chat. Nothing is disclosed and nothing is spent, but
  corrections that only show in words, and causes that span tasks, stay invisible.
- **Pilot first.** Keep the pilot's conversation sample, swap only its reviewer for the tool-free one,
  and let L3 present candidates in chat before any card or measurement exists.

The rollout takes the second direction's order (the first step is a chat message on request; the card,
settings and schedule follow once you have seen real suggestions) while keeping the task-record signals
the pilot lacked.

## What it reads

Each review reads one project's own records inside its window (see the run lifecycle): the tasks
that finished since the previous review, at most 30 days back. It needs at least five of them;
otherwise no model is called.

| Source | Used | Why |
| --- | --- | --- |
| Task records: state changes, questions with their revisions and answers, holds, reviews with findings and assessments, landing and CI events, refusals, incidents, token usage, reports | Yes, as counts and anchors | Most patterns are counted facts, cheap and exact |
| Task conversations | Yes, bounded paired excerpts | Corrections and unclear replies need the words and what they answered |
| Project chat | Yes, bounded paired excerpts | Questions asked twice, corrections given to L3 |
| Agents' working logs (engine session records: tool calls and output) | No, unless you choose it later | Megabytes per task, full of file contents and command output |
| L3's own session logs | No | Its replies are already in project chat |
| CI, validation and terminal logs, images, voice audio | No | Large; the failing check is already an event |
| Other projects | Never | A run is confined to its project |

### Building the prompt

Code builds the prompt; no summarising model call adds cost or mistakes. The whole prompt stays
within 64 KiB, as the pilot's does.

1. **Signals.** A fixed, versioned catalog of counted predicates per task (below).
2. **Task sheets**, ranked by their signals and added in that order until 48 KiB, each at most 3 KiB:
   title, outcome and durations; signal counts, each with the anchors that produced it; every question
   with its first sentence, revisions, how it was answered or resolved and by whom; each operator
   message as a paired excerpt (the message it answered, the operator's words, the next reply, 300
   characters each, at most four pairs); review findings' titles with their dispositions; and the
   report's verdict. Sheets are built from finished tasks, so later corrections are inside them.
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
| Questions re-asked without your input | A question member re-published, or re-parked as `asked_again`, with no operator message to that task since its previous revision |
| Approvals refused | `merge-approval-refused`, `l2-action-refused`, `grant-refused` or `machine-grant-refused` events |
| Your messages after a report | Operator messages on a task after its owner reported, before it closed |
| Review rounds beyond the first | `review-requested` events beyond the first per subject (proposal, changes) |
| Review findings open at merge | Findings with an `open` disposition when the task's PR merged |
| CI and landing retries | `ci-recheck` events, failed-check reruns and repeated landing attempts for one PR |
| Resume or dispatch failures | `resume-failed`, `dispatch-failed` events and faults |
| Reports rewritten | `report-superseded` events |
| Escalations | `escalated` events |
| Hours waiting on you | Time a task's open question was the operator's turn |
| Tokens used | The task's recorded token totals (not a cost) |

"Your messages after a report" counts messages, not corrections: whether a message corrected
something is the reviewer's reading, shown as such.

## The two targets

Every suggestion names exactly one target, chosen by where its change would be made:

- **The project** (chip shows the project's name): its product, code, tests, CI, docs or how its tasks
  are briefed. Leads to a project task or issue.
- **Agent workflow**: how agents coordinate everywhere: persona instructions, Altitude's role rules,
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

One fresh call per run through the engine seam (`engines.py`), using the isolation Altitude already
uses for its quota read: `--safe-mode` (no CLAUDE.md, skills, plugins, hooks or MCP),
`--no-session-persistence`, `--tools ""`, `--strict-mcp-config`, an empty temporary working directory,
no added directories and a JSON response schema. The adapter fails closed: if the session's own start
record lists any tool, or reports no model, the run stops before its answer is used.

**Cost limit.** With no tools there is exactly one model request, so the limit is set by its size, not
watched afterwards. Everything sent (the reviewer's instructions, which replace the engine's default
system prompt, the answer schema and the 64 KiB prompt) is at most 72 KiB, counted conservatively as
one token per byte: at most 74,000 input tokens, though typical text is about a quarter of that. Output,
including reasoning, is capped at 12,000 tokens through the engine's output-token setting, and Claude's
own `--max-budget-usd` is passed at the computed maximum. A model whose prices are not in the engine
seam cannot be chosen, so an unknown cost never runs. These bounds are tested as arguments with a
scripted engine; that the provider honours them is confirmed by the first real run's recorded usage.
Details shows the actual cost beside the limit, and a run that ever exceeds it says so on its line.
Expected and maximum per run for the reviewer, at published API prices:

| Model | Per million tokens (in / out) | Expected | Maximum | What is lost |
| --- | --- | --- | --- | --- |
| Claude Haiku 5.5 | $0.10 / $0.50 | under $0.01 | $0.02 | Weaker judgement: more obvious or duplicate proposals, more wrong targets for L3 to drop |
| **Claude Sonnet 5.5 (recommended)** | $2 / $10 | $0.08–0.12 | $0.27 | Close to the owners' model on reading and attribution; misses some subtle cross-task causes |
| Claude Opus 5.5 (the owners' model, for comparison) | $4 / $20 | $0.15–0.25 | $0.54 | Not cheaper; listed as the baseline |

These are the API-equivalent figures Altitude already records; on this machine the engines run on
subscriptions, so a run draws subscription allowance, not cash. The limit covers the reviewer. L3's
check is one ordinary coordinator turn, like answering a chat message, on L3's usual model; its usage
is recorded in the run and shown separately in Details, so the run's total is reviewer plus check.
Weekly on one project, the reviewer comes to about $0.40–0.50 a month, at most $1.20. Codex is not offered: its models record no dollar
cost, and Altitude runs Codex with a command sandbox that can still read the disk; no tool-free Codex
mode has been verified. If the chosen model is unavailable the run fails and says so; it never
switches model without your recorded decision.

## Run lifecycle

- **Start.** Three ways, each refused while the project's setting is **Off** or another review of the
  project is running:
  - **Weekly**, by Altitude's morning check when the review is due and its conditions hold. Its
    authority is your recorded setting.
  - **Review now** in the project's settings: your own authenticated press, sent with a request id; a
    repeat with the same id returns the same run.
  - **In chat**: you ask L3, and L3 starts it with `alt improve run --message <id>`. Altitude checks the
    message is yours, in that project's chat, and not used by an earlier run. L3 cannot start a review
    without your message, and never changes the setting.
- **States.** Reading (the reviewer call), checking (L3's turn), then exactly one ending: posted,
  nothing to suggest, stopped or failed. A run has one deadline, two hours from its start, covering
  both reading and checking.
- **Stop.** Works while reading or checking: **Stop** on the running line or in settings, or "stop the
  review" in chat (L3 runs `alt improve stop`). A stopped run's answer is discarded and never posted.
  Turning the setting to **Off** stops a running review the same way.
- **What else ends a run.** Reading: its timeout, an unavailable model, an isolation check that fails,
  or an answer that does not validate. Checking: L3 not posting before the deadline, whether its turn
  failed, was interrupted or is still queued; the candidates stay in the private record. Each ending is
  recorded and shown; nothing retries automatically. A failed weekly review is next due a week after it
  started; **Review now** runs one sooner. A run interrupted by a restart becomes
  uncertain after its deadline, without replay, as in the pilot.
- **Window.** Each review reads tasks that finished after the previous review's window ended (for the
  first, the last 14 days), at most 30 days back and never before 2026-09-22 07:00 UTC. A review that
  ends in nothing to suggest still advances the window; a stopped or failed one does not.
- Ordinary work is never paused by a run, a failure or a pending suggestion.

## Turning it on: the settings section

A new **Improvement review** section in the project's settings (`/settings/projects/<name>`), between
**Routing** and **Project**, follows the page's existing section form (heading, one muted line, a card).

- Heading line: **Finds friction that keeps recurring in this project's tasks and suggests fixes.**
- **Reviews**: a select with **Off**, **When I ask** and **Weekly**. Under its label, one line for the
  current state:

| State | Line under **Reviews** |
| --- | --- |
| Off | **Nothing is reviewed or sent.** |
| When I ask | **Runs when you press Review now or ask L3.** |
| Weekly, due later | **Next review Oct 19 morning, if 5 tasks have finished (3 so far).** |
| Weekly, due, waiting for work | **Waiting for 5 finished tasks (3 so far). Checked each morning.** |
| Weekly, due, waiting for a signal | **Waiting: no repeated friction in the 6 tasks since Oct 12.** |
| Weekly, waiting for allowance | **Waiting for Claude's weekly allowance; next try tomorrow morning.** |
| Weekly, paused | **Paused: you decided nothing in the last two reviews.** and an underlined **Resume** |
| Running | A working dot, **Reviewing 9 tasks** and an underlined **Stop** |

- **Review now**: a secondary button on its own row, with the line **Reads the tasks finished since
  Oct 12.** It is disabled while the setting is **Off**, with the line **Turn reviews on first.**, and
  while a review runs. Fewer than five finished tasks disables it with **Needs 5 finished tasks (3 so
  far).**
- **What is sent**, muted, no control: **Counted task facts and 300-character conversation excerpts ·
  Claude Sonnet 5.5 · at most $0.27 a review**.
- **Past reviews**, muted, once there is one: **4 reviews · 7 suggestions · 3 became tasks · 2
  dismissed**, and an underlined **Latest** opening the latest card in the project's chat.

**Turning it on.** Choosing **When I ask** or **Weekly** from **Off** opens a sheet before anything is
saved:

- Title: **Turn on improvement reviews for altitude?**
- Body: **Each review sends this project's counted task facts and short excerpts of its conversations
  (300 characters each) to Claude Sonnet 5.5, at most $0.27 a review from your Claude plan. Nothing is
  published, and nothing becomes work until you choose it.**
- Buttons: **Turn on** (primary) and **Cancel**. Cancel or Escape leaves the select on **Off**.

Switching between **When I ask** and **Weekly**, or to **Off**, saves at once without a sheet. A change
shows "Saving…" under the section with its controls disabled; a refusal shows the reason in red under the
section and the select returns to the saved value, as the page's other settings do. On the phone the
select wraps under its label at full width and the sheet is a bottom sheet with full-width buttons.

The setting is `improvement_review` (`off`, `on_request`, `weekly`) in the project's settings record.
Unlike routing, only the operator can change it: the web request from a paired device or
`alt project set <name> --improvement-review <value> --reason '…'` run as the operator; L3 and owners are
refused. Each change is recorded with who and when; turning it on records the sheet's text version as
the disclosure you agreed to. A later change to what is sent, the model or the limit turns the setting
back to **Off** until you agree to the new sheet.

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

One card per review, in the project's chat in conversation order, and the same card as one item in
Needs you under the project's section, the way an open question appears in both places. It is not a
question and never alerts a device. While any of its suggestions is undecided it counts as one item
in the Needs you badge, and the project's summary names it separately (**1 improvement review**).
Deciding in either place updates both. It leaves Needs you when every suggestion is decided, or when
the next review of the project posts; its undecided suggestions then fold to **Not decided** and are
offered again only if later work shows them again.

### States and exact text

| State | What appears |
| --- | --- |
| Running | System line with a working dot: **Improvement review · reading 14 tasks from Oct 5–12** and **Stop** (underlined). No Show. A weekly review shows the same line; nothing appears in Needs you until it posts. |
| Stop pressed | **Stop** becomes a spinner in place; then the line reads **Improvement review stopped by you**. If the stop cannot be saved: **Not stopped. Try again.** after the line, and Stop returns. |
| L3 checking | The same line reads **Improvement review · L3 is checking 3 suggestions**, with **Stop** as while reading. |
| Finished with suggestions | The card (below). |
| Nothing to suggest | System line **Improvement review · 14 tasks from Oct 5–12 · nothing to suggest** and **Show** (the details, including any L3 drops). |
| Not enough new work | A chat request gets L3's ordinary reply; Review now is disabled with its reason; a weekly review just waits. No line, no model call. |
| Failed | System line with the danger dot: **Improvement review stopped · <reason>** and **Show**. Reasons: "it went over its $0.27 limit", "Claude Sonnet 5.5 is unavailable", "the reviewer had tools available", "the reviewer's answer was not usable", "L3 did not finish checking", "it was interrupted by a restart". |

### The card

- Header: **Improvement review** and its time (system-line format); below, muted, **<N> tasks from
  <window>**. The number of suggestions is not written.
- Each suggestion, separated by a hairline: target chip (**Agent workflow**, or the project's name);
  title, 15 px semibold, at most 90 characters, wraps, never cut; explanation, at most 300 characters:
  what happens and what would change it, wraps fully.
- **Seen in**: up to three underlined links, each a task title opening its cited place (question,
  message or event). More citations show **<n> more** (underlined), which expands the rest in place.
- **Measured by <signal> · <count> in <N> tasks**, muted; absent when the suggestion has no signal.
- **Make a task** (filled primary) and **Dismiss** (ghost beside it).
- Footer, muted: model · reviewer cost · **Details** (underlined). Details opens in place: what was
  read, what was left out, what L3 dropped and why, the reviewer's cost against its limit and the
  check's usage, and when it ran.

| Action or state | What appears and disappears |
| --- | --- |
| Make a task pressed | Its label becomes a spinner, same width; Dismiss dims. |
| Accepted | Buttons leave; a check and **Accepted · L3 is opening a task**. |
| Task opened | That line becomes the ordinary compact task card (SPEC §3.5); if L3 attached it to an existing task or issue instead, the line names and links that. |
| Dismiss pressed, then dismissed | Spinner in place; then the suggestion folds to its chip, muted title and **Dismissed**. |
| Save failed | Buttons return; under them in danger text **Not saved. Try again.**, gone on the next press. |
| Typed answer | You reply in chat; L3 records it citing your message and the card changes the same way. |
| Later run, accepted change merged | The card opens with **Since earlier changes**: one row per measured accepted suggestion, e.g. **Questions re-asked without your input · 3 in 14 tasks before → 0 in 11 since**, linking the task that made the change. A row appears once five tasks started after the merge have finished. |
| Next review posted, suggestions left undecided | Each undecided suggestion folds to its chip, muted title and **Not decided**; the item leaves Needs you. |
| Pattern found again after a change | A muted **Seen again after <task>** line under the explanation. |
| Conversation loading, read failure, listening, microphone denied | Unchanged (SPEC §3.3, §3.6); the card asks for no permission. |

Phone (390 px) and desktop use the same card in the conversation column and in Needs you; on the phone
it is full width, the two buttons stay side by side at 44 px height, and all text wraps. In Needs you
the card's header links to the project's chat at the card.

## Decisions on suggestions

Each suggestion has at most one disposition: accepted or dismissed, with who, when and from what.

- **Buttons.** A press is the operator's own authenticated act, sent with a request id; a repeat with the
  same id returns the same receipt. A press on an already decided suggestion is refused and the card
  refreshes.
- **Typed answers.** `alt improve resolve <run>/<n> accepted|dismissed --message <id> --reason '…'`
  requires the operator's own project-chat message sent after the run's presentation receipt, and a
  number that receipt shows. When more than one run has undecided suggestions, an answer that does not
  name its review ("the one from Oct 12") is ambiguous: L3 asks rather than guesses. L3 records the scope
  it read from that message (for example "owner instructions only"); that message, not the suggestion,
  is the new task's source authority. The same message and outcome return the existing receipt; a
  different outcome for a decided suggestion is refused.
- **Changing your mind.** A dismissed suggestion can be accepted by a later message. An accepted one
  with a task cannot be dismissed; reject or stop its task instead.
- **One task per suggestion.** `alt task new --improvement <run>/<n>` refuses a suggestion that is not
  accepted or already has a task. Acceptance queues exactly one L3 turn.
- Undecided suggestions stay decidable on their card until the next review posts, then fold to **Not
  decided**. They never become work, and a later review offers one again only with new evidence.

## Closing the loop

Both sides of a measurement count finished tasks with the same predicate version. Before: the tasks
that finished in the window of the run whose suggestion was accepted, saved at acceptance. Since: the
tasks started after the linked change merged that have finished by the later run, shown once there are
five. Suggestions without a
signal are tracked by recurrence: later prompts list accepted titles, and the same pattern returns only
with new evidence, shown as **Seen again after <task>**. These are observed counts, not proof of cause,
and the card says nothing more than the counts.

The settings section's **Past reviews** line is the review's own measure: how many suggestions became
tasks and how many you dismissed. Two reviews in a row with nothing decided pause the weekly schedule.

## Where it lives

- `~/.altitude/<project>/improvements/<run>/`: the exact prompt, the raw answer and the run record
  (usage, cost, model, isolation check, coverage, validation drops, L3 drops). Private, never published,
  archived like task records.
- `<project>/improvements.json`: runs, suggestions, dispositions with their source (button request or
  message), linked tasks, baselines and later counts, the next due date and the pause.
- The setting lives with the project's other settings; its changes and the agreed disclosure version
  are in the project's settings events.
- The card is a project-chat row (`trigger: "improvement"`) naming its run; its state is read from
  `improvements.json`, so the conversation stores no second copy of a decision.
- This covers the Altitude project first. Other projects come later; their Agent workflow suggestions
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
  predicate and its version, paired excerpts and their cuts, sheet ranking and the 64 KiB bound,
  coverage, dropped citations, oversized and secret-bearing answers, the isolation check failing closed,
  token caps and budget arguments, unknown prices refused, timeout, unavailable model with no fallback,
  start authority (setting Off refused; weekly due rule with its five-task, signal, seven-day and
  allowance conditions; Review now with request-id replay; chat message, reuse refused; L3 alone
  refused; L3 and owners refused when changing the setting; a changed disclosure turning it off), the pause
  after two undecided reviews and Resume, the window advancing, Stop while reading and
  while checking, the check deadline, interrupted runs, posting with L3 drops and kept numbering, post
  refused for stopped, ended or late runs and replayed for the same post, typed answers before the
  receipt, for a dropped number, delayed past a later run, ambiguous across runs and replayed, button
  dispositions with replays and conflicts, one task per suggestion, measurement cohorts after merge,
  Not decided on the next post, the Needs you item and its count, and every chat, Needs you and settings
  state walked on phone and desktop.

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

Each step is its own held PR that you review before it lands. Automatic runs come only after you have
seen real suggestions on request.

1. **First run on request (this week).** Replace the pilot with `improve.py`: signals, task sheets and
   the prompt; the tool-free reviewer with its isolation check and cost limit; start by your chat
   message, Stop by chat, the deadline, and the private record. Your approval of this design is the
   disclosure consent for the Altitude project only; the setting does not exist yet. L3 checks the
   candidates and runs `alt improve post`, which posts them as an ordinary L3 chat message written by
   Altitude: a heading line naming the review and its window, each kept suggestion with its run
   number, target, title, explanation and an underlined link to every cited place (opening like any
   task link in chat; Back returns to the conversation), and the dropped numbers with L3's reasons.
   Endings are one short message each with the card's wording. You answer in words; L3 records them
   with `alt improve resolve` against that message. No new screen or component. Docs: the pilot's
   architecture, session lifecycle, CLI and persona sections are rewritten for the review.
   Acceptance: one real run on this project with its record, cost and your dispositions. About three to
   four days of work: the pilot's claim, timeout and record code is reused; new are the signals and
   sheets (the largest part), the reviewer adapter, four verbs (run, stop, post, resolve) and their
   tests.
2. **The card and Needs you.** The same post renders as the card in project chat and as one item in
   Needs you: the running line with Stop, Make a task and Dismiss, Details, Not decided, every state
   walked on phone and desktop.
3. **The switch and the schedule.** The settings section with **Off**, **When I ask** and **Weekly**, the
   sheet, **Review now**, the morning check with its conditions, the allowance wait and the pause after
   two undecided reviews. From here a chat request needs the setting on.
4. **Measurement.** Baselines, **Since earlier changes**, **Seen again** and the **Past reviews** line.
5. **Only if you choose them:** other projects (their Agent workflow suggestions then appear in the
   Altitude project's chat and Needs you, where Altitude itself is changed), working-log excerpts.

## Settled by the owner (redirect any of these)

- Code builds the prompt from counted predicates and paired excerpts; no model summarises transcripts.
- The reviewer has no tools at all, not read-only tools.
- At most three suggestions per review, and at most one review per project per week on the schedule.
- The schedule rides the existing morning check at 08:00 local time; there are no quiet-hours settings.
- L3 checks proposals before you see them, because the reviewer cannot open records or see current
  work; this costs one ordinary coordinator turn per review.
- Suggestions never wake a device. They count in the Needs you badge only while undecided and only
  until the next review.
- The pilot is removed, not kept beside the new review.
