# Improvement review: proposal

Status: proposal awaiting the operator's decision. Nothing here is built yet.

## For the operator

When you ask for it, a cheaper model reads what happened in this project's recent tasks and
conversations and suggests at most three improvements: some for the project itself (a flaky check,
a brief that keeps needing correction), some for how the agents work (an owner rule that makes them
ask you something you already settled, a step that wastes a review round). The suggestions arrive as
one card in project chat. Each says in plain words what keeps happening, where it was seen and what
would change it. **Make a task** hands it to L3, which opens an ordinary task (its normal proposal and
merge checkpoints still apply); **Dismiss** records that you do not want it, so later reviews do not
repeat it. When an accepted change lands, later reviews show whether the pattern actually went down.

Nothing starts work on its own, nothing leaves this machine except the model call itself, and the
reviewer can only read the summary it is handed: it has no tools, no files and no network.

### Decisions that are yours

| Decision | Recommendation | Alternatives |
| --- | --- | --- |
| Reviewer model and per-run ceiling | Claude Sonnet 5.5 at medium effort, hard stop at $0.50 API-equivalent per run (expected about $0.10–0.15) | Claude Haiku 5.5, under $0.01 a run, noisier suggestions; a Codex model is not offered (see [The reviewer](#the-reviewer)) |
| What it may read | Task records and the task and project conversations (who said what, questions, reviews, landings, reports) | Also short excerpts of the agents' own working logs around a detected problem (later increment, more insight, more private material sent to the model) |
| When it runs | Only when you ask ("run an improvement review" in project chat), one run per request | A weekly series you start once, four runs, ending after 30 days |

Approving a model and ceiling authorizes the runs you start with it. It does not start a separate
model-quality evaluation; that stays deferred under the 2026-09-08 decision.

## What it reads

Each run reads one project's own records, inside a window: from the end of the previous run (or 14
days back for the first) to now, at most 14 days. A run needs at least five tasks that finished in
the window; otherwise it says so without calling a model.

| Source | Used | Why |
| --- | --- | --- |
| Task records: state changes, questions and their revisions, answers, holds, reviews and their findings and assessments, landing and CI events, refusals, incidents, token usage, reports | Yes | Most patterns show up here as counted facts, cheaply and precisely |
| Task conversations: operator, L3 and owner messages | Yes, bounded | Repeated corrections and unclear replies need the words |
| Project chat: operator messages and L3 replies | Yes, bounded | Questions asked twice, corrections given to L3 |
| Owner working logs (the engine's session record, tool calls and outputs) | No, unless you choose the alternative above | Megabytes per task, full of file contents and command output; the counted facts already show where time went |
| L3's own session logs | No | Its chat replies are already in project chat |
| CI logs, validation logs, terminal output, images, voice audio | No | Large, and the failing check's name is already an event |
| Other projects' records | Never | Each run is confined to its own project |

The pilot's existing limit stays: nothing before 2026-09-22 07:00 UTC is read.

### Keeping the prompt small

The reviewer's whole prompt stays within 64 KiB, as the pilot's does. Code, not a model, builds it in
three layers, so no summarising model call adds cost or mistakes:

1. **Signals.** A fixed catalog of counted facts per task (below), computed from the records.
2. **Task sheets.** For each task, at most 1.5 KiB: title, outcome, durations, its signal counts, each
   question's first sentence with its revision count, and the operator's messages cut to 300
   characters each (at most five per task). Tasks are ranked by their signals; sheets are added in
   that order until 48 KiB.
3. **Coverage.** Every task that did not fit is still listed as one line (title and signal counts),
   and the prompt states what was left out. Project chat contributes the operator's messages in the
   window, cut the same way, within the remaining room.

The prompt also carries the titles of current tasks and of earlier suggestions with their outcome,
so the reviewer does not suggest work that already exists or was dismissed.

### Signals

Each signal is a count the code derives from existing records; each suggestion names the one it
would reduce (or none), which is what makes the before/after measure possible.

| Signal | Counted from |
| --- | --- |
| Question asked again | A question re-published with a new revision without the operator's input, or marked asked again |
| Approval refused | `merge-approval-refused`, `l2-action-refused`, `grant-refused` events |
| Operator correction | An operator message within a day after an owner or L3 reply or report on the same task |
| Review rounds | `review-requested` events beyond the first per subject; findings left open |
| CI and landing retries | `ci-recheck` events, reruns and repeated landing attempts on one PR |
| Resume or dispatch failure | `resume-failed`, `dispatch-failed`, faults |
| Report rewritten | `report-superseded` events |
| Escalation | `escalated` events |
| Time waiting on the operator | Hours a task spent waiting for the operator's answer |
| Spend | The task's recorded token totals |

## The two targets

Every suggestion names exactly one target, chosen by where its change would be made:

- **The project** (shown with the project's name): the project's product, code, tests, CI, docs or
  the way its tasks are briefed. Output is a candidate task or issue for this project.
- **Agent workflow**: how agents coordinate in every project: the persona instructions, Altitude's
  role rules, its commands, and its question, review and landing mechanics. Output is a candidate
  instruction or code change to Altitude.

The suggestion must also say what to change and where (for example "the owner instructions'
merge-approval paragraph"); a suggestion whose stated change is not in its target is dropped. On the
Altitude project both targets are Altitude's repository, but the label still tells you whether the
change is to this product or to how agents work.

### Evidence without copying transcripts

A suggestion cites references the code put into the prompt (a task, a question revision, an event,
a chat row), never quoted text, and the code drops any citation that was not in the prompt. In
the card these become links to the task conversations you already have. Anything published from a
suggestion (an issue, a PR, a commit) describes the pattern in general terms and names the private
review as "improvement review of <date>, suggestion <n>"; transcripts, quotes and private details stay
in the run's private record, under the existing publication rules.

## The reviewer

One fresh call per run through the engine seam (`engines.py`), with no tools, an empty working
directory, no extra directories, no hooks, plugins or MCP servers, a JSON response schema, the
engine's session timeout and a hard dollar stop. It cannot read files, run commands or reach anything
but its provider. Its answer is untrusted text: it is size-checked, schema-checked, checked for
secrets and local paths, and its citations are matched against the prompt before anything is shown.

Expected cost per run, from published API prices for a 64 KiB prompt (about 18,000 tokens) and up to
10,000 output tokens including reasoning:

| Model | Price per million tokens (in / out) | Per run | What is lost |
| --- | --- | --- | --- |
| Claude Haiku 5.5 | $0.10 / $0.50 | under $0.01 | Weaker judgement: more obvious or duplicate suggestions, more wrong targets |
| **Claude Sonnet 5.5 (recommended)** | $2 / $10 | about $0.10–0.15 | Close to the owners' model on reading and attribution; misses some subtle cross-task causes |
| Claude Opus 5.5 (the owners' model, for comparison) | $4 / $20 | about $0.25 | Not cheaper; listed only as the baseline |

On this machine the engines run on subscriptions, so these figures are the API-equivalent cost Altitude
already records for the pilot, not cash; they consume subscription allowance instead. Codex's models
(for example its "fast and affordable" model) record no dollar cost and draw on the Codex weekly
allowance. Altitude runs Codex with a command sandbox that can still read the disk, and no tool-free
Codex mode has been verified, so no Codex model qualifies as the reviewer until the engine seam proves one. If the chosen model is unavailable the run fails and says so; it
never switches to another model without your recorded decision.

## Run lifecycle

- **Start.** You ask in project chat; L3 starts the run citing your message, and Altitude checks that
  the message is yours, as it does for grants. The CLI start stays available to you. L3 cannot start a
  run on its own.
- **One run per start.** A start authorizes exactly one run (or the four of a weekly series, if you
  choose it). There is no renewal: another run needs another request.
- **What stops a run.** Its timeout, its dollar stop, an unavailable model, an invalid answer, or your
  Stop. Each is recorded and shown on the card's line; nothing retries automatically. A run interrupted
  by a restart becomes uncertain after its deadline, without replay, as in the pilot.
- **Ordinary work is never paused** by a run, a failure or a pending suggestion.

## The card in project chat

The run posts one card into project chat when it finishes, in conversation order. It is not a
question: no Needs you entry, no badge and no notification.

### States and exact text

| State | What appears |
| --- | --- |
| Running | A system line with a spinner dot: **Improvement review · reading 14 tasks from Oct 5–12**. No Show yet. |
| Finished with suggestions | The card (below). |
| Finished, nothing to suggest | A system line: **Improvement review · 14 tasks from Oct 5–12 · nothing to suggest** and **Show**, which opens the details. |
| Not enough new work | L3's ordinary reply to the request: fewer than five tasks finished since the last review. No model call, no line. |
| Failed | A system line with the danger dot: **Improvement review stopped · <reason>** and **Show** (reason examples: "the reviewer reached its $0.50 limit", "Claude Sonnet 5.5 is unavailable", "the reviewer's answer was not usable"). |
| Stopped by you | System line: **Improvement review stopped by you**. |

### The card

```
Improvement review                                   Oct 12, 09:40
14 tasks from Oct 5–12

[Agent workflow]
Owners ask again to approve a merge after a routine rebase
In 3 of 14 tasks the owner asked you to approve a merge again when only a
rebase onto main had changed the PR. One sentence in the owner instructions
would stop it.
Seen in  Release notes for 0.4 · Tidy the settings page · Add search to the task list
Measured by  approvals asked again · 3 in 14 tasks
[ Make a task ]  Dismiss

[altitude]
...

Claude Sonnet 5.5 · $0.11 · Details
```

- Header: **Improvement review** with the recorded time (system-line time format), then a muted line
  **<N> tasks from <window>**. The count of suggestions is not written; the reader sees them.
- Each suggestion, separated by a hairline: a target chip (**Agent workflow**, or the project's name),
  then the title in 15px semibold (at most 90 characters, wraps, never cut), then the explanation (at
  most 300 characters, two sentences: what happens and what would change it; wraps fully).
- **Seen in** lists at most three linked task titles (underlined; each opens that task conversation and
  Back returns here), then "and <n> more" as plain text. Titles wrap; they are never cut.
- **Measured by** names the signal and its count in the window, muted. Absent when the suggestion has no
  signal.
- Actions: **Make a task** (bordered primary button) and **Dismiss** (ghost button beside it).
- Footer, muted: model name · API-equivalent cost · **Details** (underlined). Details opens inline:
  what was read (tasks, chat window), what was left out and why, and the run's time.

| Action or state | What appears and disappears |
| --- | --- |
| Make a task pressed | Its label becomes a spinner in place, same width; Dismiss disables. |
| Accepted | Both buttons leave; a check and **Accepted · L3 is opening a task** appear. |
| Task opened | That line becomes the ordinary compact task card (§3.5) for the new task; when L3 attached the suggestion to an existing task or issue instead, the line names and links it. |
| Dismiss pressed | Spinner in place, as above. |
| Dismissed | The suggestion folds to its chip, title in muted text and **Dismissed**; its body, links and buttons leave. |
| Save failed | The buttons return; below them, in danger text: **Not saved. Try again.** It leaves on the next press. |
| Typed answer instead | You can reply in chat ("make a task for the first, but only for the owner"); L3 records it with your message and the card changes the same way. |
| Later run, accepted change landed | The new card opens with **Since earlier changes**: one row per measured accepted suggestion, e.g. **Approvals asked again · 3 in 14 tasks before → 0 in 11 since** with a link to the task that made the change. A row appears once five tasks have finished after the change; until then nothing is shown. |
| Same pattern seen again after a change | The suggestion carries a muted line **Seen again after <task title>** instead of being treated as new. |
| Conversation loading, read failure, listening, microphone denied | Unchanged conversation and composer behavior (SPEC §3.3, §3.6); the card asks for no permission. |

Phone (390 px) and desktop use the same card in the conversation column. On the phone the card is full
width, the buttons stay side by side (they fit at 390 px), and all text wraps.

## Acting on a suggestion

- **Make a task** records the acceptance as your act (the button press is authenticated like any
  answer) and queues one L3 turn about it. L3 treats the suggestion and its evidence as untrusted
  candidates: it checks current tasks and issues, then opens one ordinary task linked to the suggestion,
  steers an existing owner whose scope matches, or files an issue in general terms. Normal proposal,
  review and merge checkpoints apply to that work; an Agent workflow change to the personas or rules
  follows the existing guard-change holds.
- **Dismiss** records it; no L3 turn.
- A typed answer is ordinary chat; L3 records the outcome with `alt improve resolve`, citing your message.
- Undecided suggestions stay on their card; they are not repeated by later runs and do not expire into
  work.

## Closing the loop

When a suggestion is accepted, its signal's rate over the run's window is saved as the baseline. When
the linked task's change merges, later runs compute the same signal over the tasks that started after
the merge and show both on their card, once at least five such tasks have finished. Suggestions
without a counted signal are tracked by recurrence: later runs see accepted titles and report the
pattern again only with new evidence, shown as **Seen again after <task>**. These numbers are counts,
not proof of cause; the card shows them as observed.

## Where it lives

- `~/.altitude/<project>/improvements/<run>/`: the exact prompt, the raw answer and the run record
  (usage, cost, model, coverage, dropped suggestions and why). Private, never published, retained like
  task records.
- `<project>/improvements.json`: the runs, their suggestions, dispositions (who, when, from which message
  or button), linked tasks, baselines and later measurements.
- The card is a project chat row (`trigger: "improvement"`) that refers to its run; its state is read
  from `improvements.json`, so the conversation never stores a second copy of a decision.
- v1 covers the Altitude project. Other projects come later; their Agent workflow suggestions will then
  appear in the Altitude project's chat, because that is where Altitude itself is changed.

## Safety

- The reviewer: no tools, empty working directory, no hooks, plugins or MCP, schema-bound answer,
  timeout and dollar stop. No network beyond its provider.
- Its answer is untrusted: bounded, validated, citations must come from its prompt, secrets and paths
  refused, rendered as plain prose with Altitude-made links only.
- It writes nothing. Altitude writes the records; L3 acts only after your acceptance, and only through
  ordinary tasks.
- No agent reads transcripts ad hoc: reading session logs is reserved to this review. The L2 and L3
  personas say so, and an owner still reads its own task's logs to diagnose its own work.
- Tests use fictional project records and a scripted engine at the seam: packet bounds and ordering,
  each signal, the 64 KiB limit, dropped citations, oversized and secret-bearing answers, timeout, dollar
  stop, unavailable model with no fallback, start authority (operator message, CLI; L3 refused), stop,
  interrupted runs, accept and dismiss (button and typed), duplicate suppression, the L3 notice on
  accept, measurement after a merge, and the card's states on phone and desktop. The engine adapter test
  asserts the tool-free command line. No test calls a provider.

## What is removed

The conversation-audit pilot was never started on this installation. The improvement review replaces
it rather than running beside it: `audit.json` and its seven-day/fourteen-attempt scheduler, the
twice-daily trigger in the daemon tick, `engines.conversation_review` (which ran with the coordinator's
tools), the once-per-turn injection of candidates into L3's prompt, `alt audit`, the pilot sections of
the architecture, session lifecycle and CLI docs, and the L3 persona's "conversation-review candidates"
paragraph. Its useful parts carry over: the 64 KiB prompt, the 2026-09-22 cutoff, private packet and
result files, durable claim before the provider call, uncertainty without replay, and the engine-seam
model choice with recorded usage and cost.

## Rollout

Each increment is its own held PR that you review before it lands.

1. **First run (this week).** `improve.py` replaces the pilot: signals, task sheets and the bounded
   prompt; the tool-free reviewer in the engine seam with its dollar stop; start by your chat request or
   the CLI; the private record; the card in project chat with its suggestions, Details and the failed and
   nothing-to-suggest lines; typed answers recorded by L3. Docs: the architecture, session lifecycle, CLI
   and persona sections are rewritten in place of the pilot's. Then you run it once on this project.
2. **Buttons.** Make a task and Dismiss on the card with their pending, saved and failed states, and the
   L3 turn on acceptance.
3. **Measurement.** Baselines, the after-change counts and **Since earlier changes**, and **Seen again**.
4. **Only if you choose them:** the weekly series, the working-log excerpts, and other projects.

## Settled by the owner (redirect any of these)

- Code builds the prompt from counted signals; no model summarises transcripts first.
- The reviewer has no tools at all, not read-only tools.
- At most three suggestions per run.
- The card is posted directly from the validated answer, without a coordinator pre-screen turn; L3
  checks a suggestion only when you accept it. This keeps each run to one cheap call and shows you
  exactly what the reviewer found.
- Suggestions are not in Needs you and send no notification.
- The pilot is removed, not kept beside the new review.
