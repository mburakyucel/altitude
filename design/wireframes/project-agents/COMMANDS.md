# Routines that act: commands you list

Status: proposal for the second step of [ACTING.md](ACTING.md), which the operator asked for on 2026-10-23
after merging the first. The boards are `DesktopCmdEdit`, `PhoneCmdEdit`, `DesktopCmdRun`, `PhoneCmdRun`
and `CmdStates` beside this file; this text outranks them.

## The idea

The first step built the general parts of a routine that acts: the permission on its form, the run asking
Altitude with `alt act`, Altitude's own check, the record on the run's page and the limits. This step adds
the generic action: **commands you list**. You write a command once on a routine's form; the routine
decides when it is needed and asks Altitude to run it; Altitude runs your exact words, as you, and records
what happened. Freeing memory, pruning container images, clearing a cache, posting a report to your team's
channel or mailing you one: anything a command can do, without a model ever typing a command under your
name.

## What the run can ask, and what Altitude checks

- **One request**: `alt act run NAME`, with text on its input for a command that takes the routine's text
  (`… | alt act run post-report`). The run names a command; it cannot write, change or add arguments to one.
- **Altitude runs it only when** the command is on the routine's form with the same name and the same text
  both in the version the run started with and now, the routine is not retired, and the run has requests
  left (the 20 a run the first step set, shared by every kind of action). Text sent to a command that does
  not take it is refused too. Otherwise it is refused with a plain reason the run reads at once.
- **How it runs**: through the runner `alt task run` uses for an operator grant: your login shell
  (`bash -lc`), your access, the project's folder as its working folder, outside the run's box, as its own
  job that stops after 10 minutes. One command at a time per run; the run waits for its result.
- **Its input**: only a command you mark **Takes the routine's text** receives it: at most 4,000 characters
  on its standard input, never as arguments or environment. Every other command's input is empty. This is
  the one way anything from the run reaches a command, so it is off unless you choose it for a command
  built to read a message, such as one that posts a report.
- **What it carries**: nothing of the run's. The job starts from your login environment without the run's
  door, session or files. It is you in every other way: `alt` inside it acts as you, as it does in your
  own terminal. That is why only commands you wrote run, word for word.
- **What comes back**: the exit status and the last 4,000 characters of its output go back to the run, so it
  can say in its report what happened or try something else. The run's page keeps the last 16 KB.

## Who can set commands

Only your press on the form, or `alt agent set` in your own terminal, adds, changes or removes a command,
as only your press saves a routine. L3's offers cannot carry commands: an offer for a routine that has
commands keeps them as they are. As with worktrees and branches, L3 cannot turn on, resume or Run now a
routine that has commands; it can pause, stop and undo.

## What you see

**The form.** Under **What it may change**, after Worktrees and branches, a **Commands** list:

- Each command is a row: its name in bold, its text below in monospace (wrapping, never cut), and a
  **Remove** button (aria-label "Remove NAME"). Pressing a row opens it for editing in place.
- **Add a command** opens two fields and a tick: **Name** (hint: "Lowercase letters, digits and dashes; the
  routine asks for it by this name."), **Command** (a monospace text area; hint: "Runs as you in harbor's
  folder for up to 10 minutes, whenever the routine decides.") and **Takes the routine's text**, unticked
  (hint: "It reads a message from the routine on its input. Never tick it for a command that runs what it
  reads."). A row whose command takes the text says so under its command: "Takes the routine's text".
- Empty, the list shows only **Add a command**. At ten commands the button goes.
- Errors appear under their field when Save is pressed: "Give it a name.", "Use lowercase letters, digits
  and dashes.", "Another command has that name.", "Write the command.", "Keep it under 2,000 characters."

**The routines list and the routine page.** The list line names what a routine may do after its
triggers: "Changes worktrees and branches", "Runs 2 commands", or both joined by " · ". The routine page's
line under its definition reads "May run prune-images and post-report" (with "May change worktrees and
branches, with Undo" before it when both apply), or "Only reports".

**A run's page.** **What it changed** lists each command request in order, with the first step's rows:

| State | Row | Note |
| --- | --- | --- |
| Done | "Ran prune-images" | "Finished in 12 s." with an **Output** fold showing the kept output in monospace |
| Failed | "Ran post-report" | "Exited with 1 after 3 s." in the danger tone, with **Output** |
| Timed out | "Ran prune-images" | "Stopped after 10 min." in the danger tone, with **Output** |
| Running | "Running prune-images" | a spinner; the row settles when it ends |
| Couldn’t start | "Couldn’t run prune-images" | "Altitude could not start it, so nothing ran." |
| Not allowed | "Not allowed: run prune-images" | "This routine has no command by that name." / "This command changed after the run started." / "This command does not take the routine’s text." / "This run used its 20 requests." |

A command row has no Undo, and Undo all skips it. Stopping the run stops its running command too.

**Chat.** Altitude's sentence after the run's summary adds the commands: "Ran prune-images and
post-report." A failed one reads "post-report failed."

## Records

Each request is a numbered change on the run (`kind` "command", `target` its name, `outcome` done,
failed or refused, with `exit`, `seconds`, `timed_out`) and a `routine-action` project event with `what`
"command". The command's own record follows a machine command's: its unit, log and exit file in the run's
folder, settled after a restart of Altitude as `alt task run`'s are.

## Decisions

Decided myself, open to change:

- The routine's text reaches only a command ticked for it. Sent to every command, it could steer one that
  happens to read its input (a script that prompts, `ssh`, a database shell) with your access; the tick is
  one more control on the form, and the safe default is worth it.
- Ten commands a routine, 2,000 characters each, 10 minutes each; output kept 16 KB on the page, 4,000
  characters to the run.
- No built-in routine uses commands in this step; the docs give three example commands instead.
- Alerts to your devices, which ACTING.md paired with commands, wait for their own step.

For you: what a command prints goes back to the run and on to its engine's provider, as everything the
run reads does, so a command should not print secrets.

## Mac

The runner already runs on the Mac as a launchd job. Owed on the Mac's verify issue: a routine's command
runs outside the run's Seatbelt box with the operator's access, and stopping the run stops it.
