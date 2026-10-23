# Routines that act: proposal

Status: proposal for the operator's checkpoint, not approved. It extends the approved
[routines design](PROPOSAL.md), replacing its step 6 (#1109), on the operator's direction of 2026-10-22: a
routine is an agent with a reusable job that runs on a schedule or an event, and the job can be any action,
such as sending a notification or a report, cleaning up resources, or opening dependency-upgrade pull
requests as Dependabot does. You bring your own routines and decide what each may do.

## The problem in one paragraph

A run works alone: nobody watches it, it reads the web and it holds your code. Today that is safe because
it can only report. If it could do anything you can, one page it reads could steer it into deleting your
work or publishing something private, under your name, while you sleep. So the question is not whether a
routine may act but how you say what it may change, and how Altitude, not the model's goodwill, holds it
to that.

## The idea

**The run asks; Altitude acts.** A run stays boxed in exactly as it is today: its own throwaway copy, no
sign-ins, no write access to anything else. What changes is that it may ask Altitude to do something, one
request at a time, with one new command it alone has (`alt act`). Altitude does it only when:

1. the routine's **fence** allows that kind of action. You set the fence on the routine's form, it is part
   of the routine's saved definition, and only your press (or your own terminal) changes it, as only your
   press saves a routine today;
2. Altitude's own check of that particular action passes at the moment it acts, from its own records and
   from Git, never from what the run says about it;
3. the run is still within its limits: its time and at most 20 actions a run.

Everything else is refused with a plain reason the run reads at once. Every request, done or refused, is
recorded on the run, shown on its page and in the project's events, and comes with **Undo** where undo is
possible, or **waits for you** where it is not (a pull request waits for your merge).

This is the shape GitHub's agentic workflows call safe outputs (the agent stays read-only; separate,
capped steps perform what it asks for), applied with Altitude's own records and held steps. It reuses
what exists: the run's confinement and its door (today it answers only the engine sign-in), the routine's
versioned definition as the approval record, `alt task run`'s runner for commands, the task and its merge
gate for pull requests, and the task cleanup's own rules for what is safe to remove.

## What a routine may be allowed to do

Four kinds of permission cover the range the operator named. Each is a line in the fence; a routine with
none ticked only reports, as every routine does today.

| Permission | What the run can ask for | What Altitude checks | If it goes wrong |
| --- | --- | --- | --- |
| **Worktrees and branches** | Remove a worktree under `.claude/worktrees`; delete a local branch | Nothing is lost: the worktree has no uncommitted change, every commit is on a branch; the branch is merged into main or by a merged pull request. Nothing is in use: no open task or run owns it, no program has its folder open | **Undo** puts it back, exactly |
| **Commands you list** | Run one of the commands you wrote on its form, word for word, optionally with a short text on its input | It is on the list. The run cannot write or change a command, only choose to run one | It runs as you, with your access, recorded with its output; no undo, so list only commands safe to run whenever the run decides |
| **Pull requests** | Propose the change it made in its copy, with a title and why | At most three of its proposals open at a time | Each becomes a task held for your merge: a task owner checks it, it goes through review and the required checks, and nothing merges without you |
| **Your devices** | One alert to your devices, in plain words, led by the routine's name | At most one a run | You read a notification you did not need |

What each means for you day to day:

- **Worktrees and branches.** A weekly tidy removes the worktrees and branches finished work leaves behind,
  the helper copies Altitude's own cleanup never knew about included, and keeps anything with work in it.
  You read "Removed 6 worktrees and 2 branches" in chat and can put any one back with one press.
- **Commands you list.** This is how you bring your own: a script that frees memory or clears caches, one
  that posts a report to your team's channel or sends you a mail, one that prunes container images. You
  write the command once; the routine decides when it is needed (memory is high, a cache is large) and
  says why in its report. Because it runs as you, its cost is that you trust the command, not the run.
- **Pull requests.** A dependency routine finds an upgrade, makes the change in its copy and runs the
  tests there; Altitude opens a task from it that delivers a normal pull request through the normal merge
  gate, held for your merge. You see it in Needs you as any held pull request, marked "from Dependency
  check". Each costs a task's time on your plan, not only the run's.
- **Your devices.** A routine can reach your phone when something is urgent, not only quietly in chat.

Sending a report somewhere else (mail, a chat service) is a command you list, because only you know where
and with which sign-in; Altitude ships example scripts as it does for events.

### What never becomes a permission

- Anything on the never-granted list in [docs/CLI.md](../../../docs/CLI.md#operator-grant): loosening any
  sandbox, engine permission or Altitude guard, other tasks' worktrees or other projects' data, releasing
  Altitude's own gates (merge holds, review findings, approvals), entering or printing credentials.
- Merging anything, answering questions, approving, starting or steering tasks, messaging L3, changing
  routines or sending events. A routine never acts in your place in Altitude itself.
- Main, the project's own checkout, remote branches on GitHub, anything outside the routine's project.
- Commands the run writes. Only commands you wrote run outside its box.

## How the fence holds

- **The run's box is unchanged.** Its writable places stay its copy, its own Git worktree record, its
  scratch and engine folders. A run with every permission still cannot remove a worktree, delete a branch
  or run a command itself: the files and refs are read-only to it. The confinement tests prove this with
  and without the permission.
- **One verb, one door.** `alt act` is admitted only from the run's own worker job, as `alt task run` is
  admitted only from its owner's, and for no other actor. altd reads the fence from the routine's record
  at each request: the run gets the version it ran with, narrowed by any later change, so taking a
  permission away applies at once and adding one applies from the next run.
- **Altitude names the target.** A request names a worktree path, a branch or a command name; altd matches
  it exactly against what it enumerates itself (`git worktree list`, the repository's refs, the command
  list) and passes only its own values to Git, never the run's text to a shell.
- **The check runs at the moment of acting**, under the project lock that task cleanup and reopening take,
  so a task reopened or a worktree touched meanwhile is kept. Task cleanup and routine actions share one
  rule for "safe to remove", so the two can never disagree.
- **Commands run as `alt task run` runs them**: a transient unit outside every sandbox, at most 10 minutes,
  one at a time, within the machine's command places, recorded with output and exit status. `alt` inside
  one acts as the run, which has no verbs, so a command cannot reach Altitude or fire a routine.
- **No loops.** No action sends an event or starts a routine; a pull request reaches your own merge
  scripts only after you merge it.

## How an action is recorded and undone

- Each request is a numbered **change** on the run: its kind, target, time, outcome (done, kept with the
  reason, not allowed, failed) and what Undo needs. altd writes a `routine-action` project event for each,
  and `routine-undo` for each undo.
- The run's page lists them under **What it changed**, in order, while it runs and after. Its report gains
  Altitude's own sentence ("Removed 6 worktrees and 2 branches."), built from the changes rather than the
  model's words, in chat, in Recent runs and in the report the next run reads.
- **Undo** is a press on the run's page, or `alt agent undo` on your terminal, or L3 on your say. A removed
  worktree comes back on its branch; a deleted branch comes back at the same commit, which Altitude keeps
  under a hidden ref (`refs/altitude/undo/…`) until the run itself is pruned. Undo is refused only when
  undo itself would lose something ("Not undone: a worktree is already at that path.").
- A command has no undo; its output is the record. A pull request is held for your merge, and its task can
  be stopped like any task. An alert has nothing to undo.

## Security review questions

The proposal review should test, at least:

1. Can a run do any of the four without the permission, through `alt act`, the files it can write, Git, or
   a command it influences?
2. Can a run's request text change what altd does beyond choosing among targets altd itself enumerated
   (path tricks, symlinks, ref names that look like options, a command name that matches two)?
3. Is "nothing is lost, nothing is in use" sound: ignored files (build output, installed packages) are
   removed with a worktree; a program working in a worktree without its folder open; squash merges;
   detached worktrees; a task reopened at the same moment?
4. Do commands with optional input open a new way out (the text reaches wherever your command sends it)
   beyond the network a run already has?
5. Do pull requests from a run that read the web add risk beyond today's task flow, given the task owner
   reads the run's change and words as claims, the repository's publication rules apply, and the merge
   stays yours?
6. Does anything let a permission widen without your own press: L3's offers, a built-in's update, an
   edit made while a run is running?

## Increments

Each is its own held pull request that returns to you. The order recommended:

1. **The fence, the door and Worktrees and branches.** The permission on the form, the routine page and
   offers; `alt act` with its records, events and Undo; the shared safe-to-remove rule; a built-in **Tidy
   worktrees and branches** routine, weekly, turned on through a sheet that says what it may change. It is
   the operator's own example, wholly undoable, local, and adds no way out, so it proves the machinery at
   the lowest risk.
2. **Commands you list and alerts to your devices.** The widest range (memory, caches, reports and
   notifications anywhere), with example scripts.
3. **Pull requests, and issues** through the normal merge gate, Dependabot-style; deleting merged branches
   on GitHub joins it, since it needs your GitHub sign-in too.

The interface for each arrives with it; nothing is drawn or built before its increment.

## Alternatives considered

- **The run acts itself, with a wider box** (writable worktrees, your GitHub sign-in, a command allow-list
  checked by the sandbox). It feels most like "just an agent", but the fence becomes folders and tools
  rather than outcomes: a run allowed to write `.git` can rewrite main, and nothing could offer Undo.
- **Your operator grant, applied to a routine** (a purpose you approve; the run runs any shell command as
  you). It reuses the grant word for word, but the grant trusts a watched owner to keep to the purpose;
  an unwatched run reading the web is exactly the case it was never meant for. Commands you list keep its
  record and runner while removing the trust: only your words run.
- **Requests at the end of the run** (as suggestions are written) instead of a verb during it. One less
  door, but the run could not see an outcome (a command's output, a kept worktree's reason) and adjust.
- **A first run that only previews** ("would remove 6 worktrees", then a press). Considered for trust;
  Undo covers the same need without a second press each time, and Run now is still the test.

## Decisions that are yours

| Decision | Recommendation | Alternatives |
| --- | --- | --- |
| How a routine acts | The run asks with `alt act`; Altitude acts within the fence | A wider box for the run; or your grant applied to a run |
| The permissions | Worktrees and branches, commands you list, pull requests, your devices | Fewer, or more fixed kinds in place of commands |
| First increment | The fence with Worktrees and branches and its built-in Tidy routine | Commands first; or pull requests first |
| Pull requests | Each becomes a task held for your merge | Altitude opens the pull request itself, held, with no task owner |

## Increment 1 in detail

Boards in this folder, written by `gen.py`: `DesktopActEdit`, `PhoneActEdit` (the form), `DesktopActAgent`,
`PhoneActAgent` (the routine page), `DesktopActRun`, `PhoneActRun` (a run that changed things),
`PhoneActTurnOn` (the built-in's sheet) and `ActStates` (every state below at both widths).

**The form.** After **What it does** comes **What it may change**: a bordered list with one row, a
checkbox (44px target on the phone), **Worktrees and branches** in semibold, and under it, muted: "Removes
worktrees and branches nothing needs: no uncommitted change, every commit kept on a branch or merged, and
nothing open in them. Each has Undo on the run's page." Unticked, the muted line under the list reads
"Unticked, it only reports." and is absent when ticked. New routines start unticked. A change to the
permission saves a new version as any change does; a running run keeps its own version, narrowed at once
when the permission is taken away.

**The routine page.** **What it does** gains a line after the definition line: "May change worktrees and
branches, with Undo" in the primary text tone, or "Only reports" muted. The routines list is unchanged.

**The built-in.** **Tidy worktrees and branches** appears in Built in with "Mondays at 09:00 · Removes
worktrees and branches nothing needs, with Undo" and **Turn on**. Turn on opens a sheet, as the Weekly
review's terms do (bottom sheet at 390px, centred at 1440px): **Turn on Tidy worktrees and branches for
harbor?**, "Every Monday at 09:00 it removes worktrees and local branches of harbor that nothing needs: no
uncommitted change, every commit kept on a branch or merged, and nothing open in them. Each removal is
listed on its run's page with Undo. It never touches main, open tasks or GitHub.", **Cancel** (focused)
and the primary **Turn on**. Cancel, Escape and the scrim change nothing. Its brief tells the run to keep
anything changed in the last seven days and to say in its report what it kept and why.

**The run.** The run's prompt lists what it may change and how to ask: `alt act remove-worktree PATH` and
`alt act delete-branch NAME`, each answering "Removed …", "Kept: <reason>." or "Not allowed: <reason>.",
and a list Altitude builds of the project's worktrees and branches with what its records say about each
(its task and state, uncommitted changes, commits found nowhere else, last commit, merged or not). A run
without the permission is told it only reports; if it asks anyway, `alt act` answers "Not allowed: this
routine only reports; the operator can let it change worktrees and branches on its form.".

**The run page.** Under the report (above the live session while it runs), a card **What it changed**, one
row per request in order:

| Row | Line | Muted line | Control |
| --- | --- | --- | --- |
| Removed a worktree | "Removed worktree agent-7c41e2d0" | "Its branch worktree-agent-7c41e2d0 keeps every commit." | **Undo** |
| Deleted a branch | "Deleted branch upload-retry-fix" | "Merged by #214." or "Merged into main." | **Undo** |
| Kept | "Kept worktree add-offline-sync-for-the-mobile" | "It has uncommitted changes." / "Task “…” is still open." / "A program has it open." / "Its commits are on no branch." / "Branch … has commits found nowhere else." | none |
| Not allowed | "Not allowed: delete branch upload-retry-fix" | "This routine only reports." / "This run used its 20 changes." | none |
| Failed | "Couldn't remove worktree agent-7c41e2d0" | Git's message, one line | none |
| Undoing | the row's line | its muted line | Undo shows a spinner |
| Undone | the row's line, then " · Undone" | "Put back Oct 26, 09:40." | none |
| Undo refused | the row's line | "Not undone: a worktree is already at that path." in the danger tone | **Undo** returns |

Names are cut in the middle at 40 characters with the full name on hover and for screen readers; rows wrap
on the phone, with Undo at the row's right at both widths (44px on the phone). With no requests, the card is
absent. Undo is offered while the run is kept; a pruned run takes its undo refs with it.

**In chat and Recent runs.** Altitude's sentence follows the run's summary: "**Tidy worktrees and branches**
found something: 6 worktrees and 2 branches nothing needs. Removed 6 worktrees and 2 branches. Open". It
counts only done changes ("Removed 6 worktrees. Kept 1."); a run that changed anything shows its line unless
Tell me is Never. Undo writes no chat line.

**Offers.** An offer card that changes the permission shows it as its own line in the primary tone: "May
now change worktrees and branches" or "No longer changes worktrees and branches"; a new routine's card shows
"May change worktrees and branches" under its triggers. Only the press saves it.

**CLI.** `alt agent new|set --may worktrees|none`; `alt agent show AGENT --run N` lists the run's changes;
`alt agent undo AGENT --run N CHANGE`. L3 reads changes and may undo on the operator's own message; its
offers may include `--may`.

### States

| Surface | State | What appears |
| --- | --- | --- |
| Form | Unticked / ticked / saving / refused | "Unticked, it only reports."; the row's line alone; Save's spinner; "Not saved: <reason>" and Retry, the tick kept |
| Routine page | May change / only reports | "May change worktrees and branches, with Undo"; "Only reports" muted |
| Built in | Row / sheet / turning on / refused | The row with Turn on; the sheet, Cancel focused; a spinner in the sheet's Turn on; the sheet closes and "Not turned on: <reason>" under the row |
| Run page | Running with changes / ended with changes / none | The card above the live session, growing; the card under the report; no card |
| Change row | Done / kept / not allowed / failed / undoing / undone / undo refused | As the table above |
| Chat | Changed something / changed nothing | The summary, then Altitude's sentence; the line as today |
| Offer | Permission added / removed / new routine with it | "May now change worktrees and branches"; "No longer changes worktrees and branches"; "May change worktrees and branches" |

### Mac

The "a program has it open" check sits behind the platform seam (the processes' working folders on Linux,
`lsof` on the Mac). The Mac owes on its verify issue: `alt act` admitted only from the run's own job under
Seatbelt, the run's own attempt to remove a worktree or delete a branch refused by the sandbox, and the open
check finding a terminal in a worktree.
