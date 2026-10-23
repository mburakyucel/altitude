# Routines that act

Status: the operator chose increment 1 on 2026-10-22, built as [Increment 1 in detail](#increment-1-in-detail)
describes; increments 2 and 3 stay proposals until each returns with its own held pull request. It extends the approved
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
| **Worktrees and branches** | Set aside a worktree under `.claude/worktrees`; delete a local branch | The [tidy rule](#the-tidy-rule) below: nothing is lost, nothing is in use, nothing changed in seven days | **Undo** puts it back exactly, ignored files included, for 14 days |
| **Commands you list** | Run one of the commands you wrote on its form, word for word, optionally with a short text on its input | It is on the list. The run cannot write or change a command, only choose to run one | It runs as you, with your access, recorded with its output; no undo, so list only commands safe to run whenever the run decides |
| **Pull requests** | Propose the change it made in its copy, with a title and why | At most three of its proposals open at a time | Each becomes a task held for your merge: a task owner checks it, it goes through review and the required checks, and nothing merges without you |
| **Your devices** | One alert to your devices, in plain words, led by the routine's name | At most one a run | You read a notification you did not need |

What each means for you day to day:

- **Worktrees and branches.** A weekly tidy clears the worktrees and branches finished work leaves behind,
  the helper copies Altitude's own cleanup never knew about included, and keeps anything with work in it.
  You read "Set aside 6 worktrees and deleted 2 branches" in chat and can put any one back with one press,
  or the whole run's with Undo all. A worktree's disk space comes back when its 14 days end.
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
  Altitude's own gates (merge holds, review findings, approvals), entering or printing credentials. Tidy
  grants the run no access to other tasks' worktrees: altd carries out its own cleanup rule only on worktrees
  that no task names as its own, never a task's, which task cleanup removes when the task ends. The CLI
  paragraph says so in increment 1.
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
  so a task reopened or a worktree touched meanwhile is kept. Every limit that protects your work is in
  that check, none only in the brief.
- **Commands run as `alt task run` runs them**: a transient unit outside every sandbox, at most 10 minutes,
  one at a time, within the machine's command places, recorded with output and exit status. A command is
  you in every way; the run chooses only which one and when, and passes it no text
  ([COMMANDS.md](COMMANDS.md)).
- **No loops.** No action sends an event or starts a routine; a pull request reaches your own merge
  scripts only after you merge it.

## How an action is recorded and undone

- Each request is a numbered **change** on the run: its kind, target, time, outcome (done, kept with the
  reason, not allowed, failed) and what Undo needs. altd writes a `routine-action` project event for each,
  and `routine-undo` for each undo.
- The run's page lists them under **What it changed**, in order, while it runs and after. Its report gains
  Altitude's own sentence ("Set aside 6 worktrees and deleted 2 branches."), built from the changes rather than the
  model's words, in chat, in Recent runs and in the report the next run reads.
- **Undo** is a press on the run's page (one row, or **Undo all** for the run), `alt agent undo` on your
  terminal, or L3 on your say. A set-aside worktree moves back to its path exactly. A deleted branch comes
  back at the commit altd recorded in the change; a hidden ref (`refs/altitude/undo/…`) only keeps that
  commit's objects, and Undo refuses if the ref no longer matches the record, since task workers can write
  refs. Undo is refused when it would lose something ("Not undone: a worktree is already at that path.").
- **Undo sticks.** What you put back is kept from that routine's later runs for as long as the run is kept
  (90 days, or longer while it is among the newest ten), so next week's run does not take it again.
- A command has no undo; its output is the record. A pull request is held for your merge, and its task can
  be stopped like any task. An alert has nothing to undo.

## The tidy rule

altd sets aside a worktree, or deletes a branch, only when all of these hold at the moment it acts. The
rule is code, shared with task cleanup where the two overlap; the brief can narrow it, never widen it.

**A worktree** is set aside when:

- it is registered with the project's repository and its folder is directly under `.claude/worktrees`;
  never main's checkout, never a run's copy;
- it is not locked and no task names it as its own: task cleanup removes those when the task ends, and reopening
  a task checks its branch out there again;
- no task that was open when it was made is still open, so a helper copy of a running or blocked task stays until
  that task ends;
- nothing in it changed for seven days: no commit, checkout or staging (its Git record), and no file in its
  folder, ignored files included;
- no program has its folder as its working folder (the platform seam: `/proc` on Linux, `lsof` on the Mac);
- it has no uncommitted change and no commit found on no branch or remote.

Set aside means `git worktree move` into `.claude/worktrees/.set-aside/<routine>-<run>-<change>/`, the same disk, so
nothing is copied and Undo moves it back exactly, ignored files (`.env`, local databases, installed
packages) included. After 14 days the tick deletes the folder and its Git record for good, and its row
reads "Removed for good Nov 9."; from then Undo restores it from its branch, without ignored files, and says
so. When Git refuses to remove it (something changed inside it meanwhile), it stays set aside with Git's reason
and its Undo.

**A branch** is deleted when:

- it is a local branch of the project's repository: never main or the default branch, never checked out in
  any worktree (a set-aside one included), never an open task's branch;
- its tip is on `origin/main`, or a pull request into main of this repository merged exactly that tip
  (looked up by branch on GitHub, before the project lock and used only while the tip is unchanged);
- it has not moved for seven days (its newest reflog entry, else its tip's commit time), so a branch you
  just made from main is kept.

## Increments

Each is its own held pull request that returns to you. The order recommended:

1. **The fence, the door and Worktrees and branches.** The permission on the form, the routine page,
   the routines list and offers; `alt act` with its records, events and Undo; the tidy rule; a built-in
   **Tidy worktrees and branches** routine, weekly, turned on through a sheet that says what it may change.
   Turning on or resuming a routine that may change things, and Run now on it, stay your press or your
   terminal; L3 may pause, stop and undo it on your say, which only lowers what it does.
   It is the operator's own example, wholly undoable, local, and adds no way out, so it proves the
   machinery at the lowest risk.
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
- **Tidy that Altitude does itself, with no model** (the review's alternative). Every rule above is
  altd's, so a built-in tidy could run on its schedule with the same card and Undo, no tokens and no
  prompt injection at all; the `alt act` door would arrive with commands, where it is needed. It is the
  safer tidy, but not a routine acting: your brief could not say what to keep, and increment 1 would not
  yet prove that a routine can act within its fence.
- **A first run that only previews** ("would remove 6 worktrees", then a press). Considered for trust;
  Undo covers the same need without a second press each time, and Run now is still the test.

## Decisions

The operator chose the first increment as recommended: the run asks with `alt act` and Altitude acts within the
fence, starting with Worktrees and branches and its built-in Tidy routine. The further permissions (commands you
list, your devices) and whether each pull request becomes a task held for your merge are decided with their
increments.

## Increment 1 in detail

Boards in this folder, written by `gen.py`: `DesktopActEdit`, `PhoneActEdit` (the form), `DesktopActAgent`,
`PhoneActAgent` (the routine page), `DesktopActRun`, `PhoneActRun` (a run that changed things),
`PhoneActTurnOn` (the built-in's sheet), `ActStates` and `PhoneActStates` (every state below at each width). The
walkthrough `web/e2e/routine-acting.pw.ts` walks them at both widths against real records and Git.

**The form.** After **What it does** comes **What it may change**: a bordered list with one row, a
checkbox (44px target on the phone), **Worktrees and branches** in semibold, and under it, muted: "Sets
aside worktrees and deletes branches nothing needs: no uncommitted change, nothing changed in seven days,
every commit kept on a branch or merged, and nothing open in them. Each has Undo on the run's page."
Unticked, the muted line under the list reads "Unticked, it only reports." and is absent when ticked. New
routines start unticked. A change to the permission saves a new version as any change does; a running run
keeps its own version, narrowed at once when the permission is taken away.

**The routines list.** A routine that may change things adds the permission after its triggers: "Mondays at
09:00 · Changes worktrees and branches · Found something 2 h ago". A routine that only reports reads as
today.

**The routine page.** **What it does** gains a line after the definition line: "May change worktrees and
branches, with Undo" in the primary text tone, or "Only reports" muted.

**The built-in.** **Tidy worktrees and branches** appears in Built in with "Mondays at 09:00 · Clears
worktrees and branches nothing needs, with Undo" and **Turn on**. Turn on opens a sheet, as the Weekly
review's terms do (bottom sheet at 390px, centred at 1440px): **Turn on Tidy worktrees and branches for
harbor?**, "Mondays at 09:00 it sets aside worktrees and deletes local branches of harbor that nothing
needs: no uncommitted change, nothing changed in seven days, every commit kept on a branch or merged, and
nothing open in them. Each is listed on its run's page with Undo. It never touches main, open tasks or
GitHub.", the primary **Turn on** and **Cancel** (focused). Cancel, Escape and the scrim change nothing.
Turn on closes the sheet and the row's Turn on spins until the routine reads as on, as the Weekly review's
does; a refusal shows "Not turned on: <reason>" under the row. Its brief asks the run to say in its report what it kept and why.

**The run.** The run's prompt lists what it may change and how to ask: `alt act set-aside-worktree PATH` and
`alt act delete-branch NAME`, each answering "Set aside …", "Deleted …", "Kept: <reason>." or "Not
allowed: <reason>.", and a list Altitude builds of the project's worktrees and branches with what its
records say about each (its task and state, last change, uncommitted changes, commits found nowhere else,
merged or not). A run without the permission is told it only reports; if it asks anyway, `alt act` answers
"Not allowed: this routine only reports; the operator can let it change worktrees and branches on its
form.".

**The run page.** Under the report (above the live session while it runs), a card **What it changed**, one
row per request in order. Its header row has **Undo all** at the right while two or more rows can be undone;
pressed, every such row shows its spinner, then reads as undone or refused on its own.

| Row | Line | Muted line | Control |
| --- | --- | --- | --- |
| Set aside a worktree | "Set aside worktree agent-7c41e2d0" | "Removed for good Nov 9 unless you undo it." | **Undo** |
| Deleted a branch | "Deleted branch upload-retry-fix" | "Merged by #214." or "Merged into main." | **Undo** |
| Removed for good | "Set aside worktree agent-7c41e2d0" | "Removed for good Nov 9. Undo restores it from its branch, without ignored files." | **Undo** |
| Still set aside | "Set aside worktree agent-7c41e2d0" | "Still set aside: <Git's reason>" | **Undo** |
| Kept | "Kept worktree add-offline-sync-for-the-mobile" | "It has uncommitted changes." / "It changed 2 days ago." / "It is task “…”’s worktree." / "Task “…” is still open." / "A program has it open." / "Its commits are on no branch." / "It has 2 commits found nowhere else." / "It moved 3 days ago." / "It is the main branch." / "You put it back Oct 19." | none |
| Not allowed | "Not allowed: delete branch upload-retry-fix" | "This routine only reports." / "This run used its 20 requests." | none |
| Failed | "Couldn't set aside worktree agent-7c41e2d0" | Git's message, one line | none |
| Undoing | the row's line | its muted line | Undo shows a spinner |
| Undone | the row's line, then " · Undone" | "Put back Oct 26. This routine leaves it alone from now on." | none |
| Undo refused | the row's line | "Not undone: a worktree is already at that path." in the danger tone | **Undo** returns |

A press refused as a whole (a run pruned meanwhile) says "Not undone: <reason>" under the card and returns every
Undo.

Names are cut in the middle at 40 characters with the full name on hover and for screen readers; rows wrap
on the phone, with Undo at the row's right at both widths (44px on the phone). With no requests, the card is
absent. Undo is offered while the run is kept; a pruned run takes its undo refs with it.

**In chat and Recent runs.** Altitude's sentence follows the run's summary: "**Tidy worktrees and branches**
found something: 6 worktrees and 2 branches nothing needs. Set aside 6 worktrees and deleted 2 branches.
Open". It counts only done changes ("Set aside 6 worktrees."); a run that changed anything shows its line
unless Tell me is Never. Undo writes no chat line.

**Offers.** An offer card that changes the permission shows it as its own line in the primary tone: "May
now change worktrees and branches" or "No longer changes worktrees and branches"; a new routine's card shows
"May change worktrees and branches" under its triggers. Only the press saves it.

**CLI.** `alt agent new|set --may worktrees|none`; `alt agent show AGENT --run N` lists the run's changes;
`alt agent undo AGENT --run N CHANGE|all`. L3 reads changes; on the operator's own message it may pause,
stop and undo a routine that may change things, but not turn it on, resume it or run it; its offers may
include `--may`.

### States

| Surface | State | What appears |
| --- | --- | --- |
| Form | Unticked / ticked / saving / refused | "Unticked, it only reports."; the row's line alone; Save's spinner; "Not saved: <reason>" and Retry, the tick kept |
| Routines list | May change / only reports | "· Changes worktrees and branches ·" after the triggers; as today |
| Routine page | May change / only reports | "May change worktrees and branches, with Undo"; "Only reports" muted |
| Built in | Row / sheet / turning on / refused | The row with Turn on; the sheet, Cancel focused; the sheet closed and the row's Turn on spinning; "Not turned on: <reason>" under the row |
| Run page | Running with changes / ended with changes / none | The card above the live session, growing; the card under the report; no card |
| Change row | Set aside / deleted / removed for good / kept / not allowed / failed / undoing / undone / undo refused | As the table above |
| Undo all | Shown / pressed | At the card's header while two or more rows can be undone; each row's spinner, then its own outcome |
| Chat | Changed something / changed nothing | The summary, then Altitude's sentence; the line as today |
| Offer | Permission added / removed / new routine with it | "May now change worktrees and branches"; "No longer changes worktrees and branches"; "May change worktrees and branches" |

### Mac

The "a program has it open" check sits behind the platform seam (the processes' working folders on Linux,
`lsof` on the Mac). The Mac owes on its verify issue: `alt act` admitted only from the run's own job under
Seatbelt, the run's own attempt to set aside a worktree or delete a branch refused by the sandbox, and the
open check finding a terminal in a worktree.
