# A project through several fronts of work

Atlas, a search service moving to a versioned index, carries this example. The captures show the
current interface with example data.

## Who carries the work

You set direction and make decisions that need your judgment. Each project has a persistent
coordinator (L3); each task has an owner (L2) responsible for investigation through delivery.
An owner can delegate bounded work to native helpers (L1) and remains responsible for the result.
You can talk to an owner directly as well as through the project conversation.

<img src="images/altitude.svg" alt="You make the executive decisions. Below you, the coordinator (L3) orchestrates the project: it plans with you, starts tasks and answers from your decisions. Each task has its own agent (L2) that takes it from idea to production, and each task agent runs its own helper agents (L1) for focused work such as reading API docs, running tests or profiling latency." width="1000">

## Keep the project direction in one conversation

In **Atlas**, discuss the migration with L3, the project's persistent orchestrator. Preserve the
v1 API and pagination contract, make backfill retries safe, and keep p95 latency below 200 ms.
Atlas already has twenty tasks in flight. After agreeing those constraints, ask L3 to dispatch three
more for the migration:

| L2 owner | Its scope | Why it can proceed now |
| --- | --- | --- |
| Client compatibility | API responses, generation-bound cursors and contract tests | Implements against the agreed interface. |
| Resumable backfill | Revision-aware upserts, checkpoints and retry tests | Uses the agreed document/revision contract. |
| Performance baseline | Replay harness and current-index latency measurements | Establishes a baseline before the implementation changes land. |

L3 creates concrete briefs; Altitude dispatches the tasks subject to capacity and engine
availability. Each L2 investigates and owns its work end to end, choosing its implementation
and any native helpers. Coordination of dependencies is L3/owner judgment; this example does
not imply an automatic dependency scheduler. Rollout waits for the results.

<picture>
  <source media="(max-width: 600px)" srcset="images/project-phone.png">
  <img src="images/project-desktop.png" alt="Atlas: agreed migration constraints in the L3 conversation alongside twenty-three active tasks and 128 finished this week." width="1440">
</picture>

[Full-size desktop](images/project-desktop.png) · [Phone conversation](images/project-phone.png)

On desktop the work panel sits beside the project conversation. On phone, **Chat** and **Work**
show the same project in separate tabs. [Open the phone Work capture](images/work-phone.png).

## Steer a task without going through L3

Open **Preserve client compatibility** and tell its L2:

> Keep pagination tokens valid across the cutover. Clients must not restart an in-flight search.

The owner describes its generation-bound cursor design in the task conversation. The adjacent
**Live session** shows its observed replies and tool activity, with command output folded.
Messages sent here are durable and reach the worker at its engine's next checkpoint; the
conversation remains available separately from the engine transcript.

Your own skills and custom commands work here as in your terminal: type `/release-notes v2` and a
Claude owner runs that skill with `v2`, whether it is yours or the project's. The project
conversation runs Claude's coordinator in a restricted mode that does not load them, so use them in a
task. [Engine integration](ARCHITECTURE.md#engine-integration-boundary) describes the limits.

<picture>
  <source media="(max-width: 600px)" srcset="images/task-phone.png">
  <img src="images/task-desktop.png" alt="The compatibility task: direct steering and the owner's response beside the live session, which shows cursor inspection and contract-test commands." width="1440">
</picture>

[Full-size desktop](images/task-desktop.png) · [Phone conversation](images/task-phone.png)

On phone, use **Conversation** and **Live session** to switch panes.
[Open the phone session capture](images/session-phone.png). The capture journey also sends a
follow-up, checks that its bubble appears, and verifies that the composer clears.

For the occasional command you want to run yourself, turn on **Settings → This machine →
Terminal**. The task's panel then switches between **Live session** and **Terminal** (a third tab
on phone) and opens a shell as you in its worktree, outside the owner's sandbox. The project
header's **Terminal** opens one in the project folder. Each terminal survives navigation and lost
connections, and ends when you close it, the task finishes, the setting goes off or Altitude
restarts. Only its opening and closing are recorded. [Operator terminal](ARCHITECTURE.md#operator-terminal)
describes its lifetime and how Altitude refuses its own agents.

## Let L3 answer what the project already knows

The backfill owner asks whether a retried batch may rewrite a document. L3 answers from the
agreed contract: upsert by document ID and source revision, preserving any newer revision.
Its message requests the blocked owner's resume. You do not need to repeat that decision.

Later the owner needs a retention policy: keep the old index for seven or thirty days. L3 can
recommend seven days for the pilot, but the storage cost and rollback window need your judgment.
It escalates that question. The other task owners can continue while this one waits.

**Needs you** gathers unanswered operator questions across projects. Open a question to read its
context and discuss it in the owning L2 conversation. Pick choices, then use **Send N answers**
to submit them; even a single choice stays staged until you send it. You can also type,
“Keep the old index for seven days; include that limit in the rollout notes.” The owner records
that decision against your message. A follow-up keeps the question open, and a saved answer does
not by itself mean work has resumed or a merge hold has been released.

When the owner shows you a document or screenshots, such as a proposal or a README draft, the
question or reply carries a card for them. Tap it to read the document beside the conversation, or
over it on a phone; pictures open full screen. Closing returns you to the question with your unsent
answer intact, and an older version is marked **Earlier** with a link to the newer one.

<picture>
  <source media="(max-width: 600px)" srcset="images/decision-phone.png">
  <img src="images/decision-desktop.png" alt="Needs you: L3 brings the old-index retention decision to the operator, with seven-day and thirty-day choices." width="1440">
</picture>

[Full-size desktop](images/decision-desktop.png) · [Phone decision](images/decision-phone.png) ·
[Watch the phone walkthrough](images/phone-walkthrough.webm)

The silent recording follows the real interface through the project, work, task and live-session
views, sends a task message, then selects and sends the seven-day answer. Owner replies are
example text; it demonstrates the interaction, not live agents or voice recognition.

The [current decision boards](../design/wireframes/CONVERSATION_FIRST.md) illustrate these states;
[phone/desktop walkthroughs](DEVELOPMENT.md#browser-walkthroughs) retain implementation evidence
outside Git.

## Bring delivery back to the project

Each owner runs the project's checks and appropriate review, and delivers through its own PR.
A merge hold leaves a checked PR for your review; otherwise the owner can merge when ready.
Verification retains the report and conversation in the archive. Reports needing judgment go
to L3; a mechanically clean delivery can close automatically without another L3 turn.
Back in the project conversation, discuss whether the compatibility, backfill and performance
results justify a rollout task, or whether one needs further work first.

The scope and ordering here are an example of engineering judgment, not a prescribed pipeline.
For session identity and engine-specific message timing, see [lifecycle](SESSION_LIFECYCLE.md).
To try a project, follow [setup](SETUP.md).

## See the features

The README's features in the same Atlas project. On a computer the task view and the terminal show the
desktop with the phone in front, and each other picture shows two phone screens side by side; on a phone
each shows the one screen that proves it.

**Start a task from the conversation.** The coordinator's reply offers a task; one press creates it.

<picture>
  <source media="(max-width: 600px)" srcset="images/feature-create-phone.webp">
  <img src="images/feature-create.webp" alt="The coordinator answers that searches started at the index switch can come back empty and offers Create task for holding cursors during the switch; after the press, the reply shows Task created and the coordinator answers that it started the task, with the new task's card." width="860">
</picture>

**Approve the design before it is built.** The synonyms editor's owner shows its proposal with the other
engine's review, which fixed two problems and offered an alternative.

<picture>
  <source media="(max-width: 600px)" srcset="images/feature-proposal-phone.webp">
  <img src="images/feature-proposal.webp" alt="Needs you asks whether to build the synonyms editor as shown, with its design document, the proposal review's three resolved findings and Approve proposal recommended; beside it, the review's findings: two fixed and one alternative direction." width="860">
</picture>

**Work in the task.** The task view holds the owner's conversation and its open question beside the live
session.

<picture>
  <source media="(max-width: 600px)" srcset="images/feature-task-phone.webp">
  <img src="images/feature-task.webp" alt="The typo tolerance task on a computer: the owner's updates, your message keeping product codes exact and the owner's question whether long words allow one typo or two, beside the live session's replies and commands; on a phone in front, the same conversation." width="860">
</picture>

**Steer mid-flight.** Your message to the compatibility owner beside its live session.

<picture>
  <source media="(max-width: 600px)" srcset="images/session-phone.png">
  <img src="images/feature-live.webp" alt="The compatibility task's live session: the owner's replies and the commands it ran, with their output folded." width="860">
</picture>

**Take the terminal.** The billing owner hands you a step only you can take; it waits typed in the
task's terminal until you press Enter.

<picture>
  <source media="(max-width: 600px)" srcset="images/feature-terminal-phone.webp">
  <img src="images/feature-terminal.webp" alt="The billing task on a computer: the owner's message handing you make deploy ENV=staging with Copy and Open in terminal, beside the task's terminal with the command typed at the prompt, waiting for Enter; on a phone in front, the same terminal." width="860">
</picture>

**Nothing ships unchecked.** The independent review's two findings, both fixed, beside the pull
request waiting for your merge.

<picture>
  <source media="(max-width: 600px)" srcset="images/feature-review-phone.webp">
  <img src="images/feature-review.webp" alt="The per-tenant limits task's implementation review by the other engine: a burst at the window edge and a missing Retry-After, each marked fixed with the owner's fix." width="860">
</picture>

**Self-healing.** A machine problem pauses the sign-on task; the coordinator repairs it and the task
resumes.

<picture>
  <source media="(max-width: 600px)" srcset="images/feature-heal-phone.webp">
  <img src="images/feature-heal.webp" alt="The coordinator's reply in the project conversation: it restarted the sign-on task's test identity provider, checked a login end to end and resumed the task." width="860">
</picture>

**Self-improving.** Altitude reviews its own finished work and suggests fixes; one tap makes a fix a
task.

<picture>
  <source media="(max-width: 600px)" srcset="images/feature-improve-phone.webp">
  <img src="images/feature-improve.webp" alt="Improvement suggestions from finished work, each citing the tasks it came from; the first is accepted and the coordinator is opening a task." width="860">
</picture>

## Capture source and maintenance

[Example data and capture instructions](../design/readme/README.md) explain how to reproduce the
images. The browser walkthrough renders [Project](../web/src/routes/Project.tsx),
[Task](../web/src/routes/Task.tsx), [Live session](../web/src/routes/LiveSession.tsx) and
[Needs you](../web/src/routes/NeedsYou.tsx) against a seeded disposable service, with no app styling
overrides or private service data.
Desktop images are 1440×900 and phone images 390×844, rendered at 2× for readable enlargement.
Open any image for its full resolution. Responsive picture sources keep phone text readable
when this page is viewed at narrow widths.
