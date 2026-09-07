# A short walkthrough

This is an illustrated text walkthrough with fictional data, not a live transcript or screenshot.
It uses the current project's conversation/work panel, task conversation/live session, and
Needs you views. The example's replies and decisions describe one possible run; the owner
chooses the execution approach for each task.

## 1. Discuss a change and see its work

Open the **field-notes** project. The project conversation sits beside its work panel on a
desktop; on a phone, use Chat and Work to move between them.

```text
field-notes · Project conversation

You: Our researchers need CSV exports of their notes. Keep the existing
     column names and avoid adding a dependency.

Coordinator: I'll create a task for CSV export with those constraints.

Work
  Add CSV export    running    [Open]
```

The coordinator may answer a question directly. When it creates work, one task owner receives
the brief and an isolated worktree/branch. Opening the task takes you to that owner's conversation.

## 2. Steer the owner and inspect the live session

```text
Add CSV export · Task

Conversation                          Live session
You: Keep the date column in UTC.      Prompt: Add CSV export…
Owner: I'll preserve UTC and cover     Owner: I'm checking the existing
       the export format in tests.           export helpers.
                                      $ git status       [output folded]
                                      Read export code   [output folded]
```

The desktop task page puts the conversation beside the session panel at wide widths; on a
phone it has **Conversation** and **Live session** tabs. The live panel shows the engine's
observed replies and tool activity. The task conversation is the durable place to direct the
owner. Messages reach the worker at its engine's next checkpoint, so a reply need not be instant.

## 3. Handle a decision across projects

Suppose the owner needs a product call about export scope and another project needs a rollout
decision. Questions the coordinator can answer from the record go there first. Questions needing
your judgment appear in **Needs you**, grouped by project, with links to more context:

```text
Needs you

field-notes
  Should CSV include archived notes?
  [Resume]  [Reject]  [More context]

release-board
  Is the preview ready for the pilot group?
  [Resume]  [Reject]  [More context]
```

Use **More context** to open the task and answer in its conversation, for example:
“Export active notes only; archived notes can wait.” Messaging a blocked owner requests its
resume with that answer. The card's current actions are **Resume** (continue from existing
context) and **Reject** (end the task); these are not custom product-choice buttons. A successful
action leaves the queue; a failed submission stays visible with Retry. The task page also shows
its waiting decision.

## 4. Follow the change to completion

The owner implements the export, runs the project's checks and appropriate review, and opens a
PR. The task links to the PR and reports its delivery status. With no merge hold, the owner can
merge after the required checks and review. With a hold, the PR stays for your review.

After successful verification, the task is archived with its conversation and report available.
You can return to the project conversation to discuss the next change.

This example is deliberately small enough to keep current by checking
[Project](../web/src/routes/Project.tsx), [Task](../web/src/routes/Task.tsx),
[Live session](../web/src/routes/LiveSession.tsx), and
[Needs you](../web/src/routes/NeedsYou.tsx) when these views change. It contains no real project
data, provider logs or screenshots. See [setup](SETUP.md) to try your own project.
