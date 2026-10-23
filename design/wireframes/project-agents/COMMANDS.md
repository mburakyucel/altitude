# Routines that act: commands you list

Status: proposal for the second step of [ACTING.md](ACTING.md), which the operator asked for on 2026-10-23
after merging the first; revised after three design reviews. The boards are `DesktopCmdEdit`, `PhoneCmdEdit`,
`DesktopCmdRun`, `PhoneCmdRun`, `CmdStates` and `PhoneCmdStates` beside this file; this text outranks them.

## The idea

The first step built the general parts of a routine that acts: the fence on its form, the run asking
Altitude with `alt act`, Altitude's own check, the record on the run's page and the limits. This step adds
the generic action: **commands you list**. You write a command once on a routine's form; the routine
decides when it is needed and asks Altitude to run it by name; Altitude runs your exact words, as you, and
records what happened. Freeing memory, pruning container images, clearing a cache: anything a command can
do, without a model ever typing a command under your name or passing one a word.

## What a command is

A listed command runs as you, outside every sandbox, as a command under an operator grant does, so it can
do anything you can, Altitude's own `alt` included. Nothing confines a program running as you from inside,
so the fence is the list itself and what the run can pass in:

- **The run chooses only which command and when, once each a run.** It sends a name and nothing else: no
  arguments, no input, no environment. A command's input is empty. Altitude starts it from your login
  environment without the run's door, session or files.
- **The words are yours; what they call is as you keep it.** A command starts in the project's folder and
  reads its files and settings as they are then: a script, `make` or `npm run` runs that file as the folder
  holds it, and a tool reads the folder's own settings (an `.npmrc`, a `Makefile`). The run cannot write
  that folder, but a merged change can. The form says so, and a command that should not follow the project
  can name a script outside it or `cd` elsewhere first.
- **Its words and what it prints go to the run,** and on to its model's provider, as everything the run reads
  does: the run's prompt lists each command with its words, so a secret written into one is read too. The form
  says so.
- **The never-granted list is yours to keep** ([docs/CLI.md](../../../docs/CLI.md#operator-grant)), as it is
  for a grant's purpose: list no command that loosens confinement, releases Altitude's gates (merging,
  approving, answering), reaches other tasks' worktrees, prints credentials, or starts, steers or changes a
  routine or task. Altitude does not check what a command does: it records each one, and the run's page shows
  what ran and what it printed.

## What the run can ask, and what Altitude checks

- **One request**: `alt act run NAME`. A name that is not lowercase letters, digits and dashes of at most
  40 characters is refused without recording its text: its row reads "Not allowed: a command", so the run's free
  text never appears.
- **Altitude runs it only when** a command of that name with the same words is on the routine both in the
  version the run started with and now, the routine is not retired, the command has not run in this run
  yet, and the run has requests left (the 20 a run the first step set, shared by every kind of action).
  Otherwise it is refused with a plain reason the run reads at once: no command of that name on the routine
  now, one whose words differ from the version the run started with (or that it did not have), a retired
  routine, another of its commands still running, or no requests left. Asking again for a command that already
  started in this run runs nothing and adds no row: Altitude answers with that command's result, waiting while it
  runs, so a run whose own call ended first still learns how it went.
- **How it runs**: through the runner `alt task run` uses for an operator grant: your login shell
  (`bash -lc`), the project's folder as its working folder, as its own job that stops after 10 minutes. One
  command at a time per run; the run waits for its result. Its time counts toward the run's own limit. A job
  that never opened its log never started, and only then is the command not counted as run.
- **What comes back**: the exit status and the last 4,000 characters of its output go back to the run, so it
  can say in its report what happened. The run's page keeps the last 16 KB, shown once the command ends.
- **What stops it**: **Stop** on its row (the run goes on and reads that you stopped it), stopping the run,
  the run reaching its limit, retiring the routine, or saving the routine without that command or with
  different words. Each stops the job by its name, after a restart of Altitude too, and is recorded.

## Who can set commands

Your press on the form, or `alt agent new` and `alt agent set` in your terminal, add, change or remove
commands: `--command NAME='WORDS'`, once for each, gives the whole list, and `--no-commands` empties it. L3's
offers cannot carry commands: an offer for a routine that has commands keeps them as they are. As with
worktrees and branches, L3 cannot turn on, resume or Run now a routine that has commands; it can pause, stop
and undo.

A command you list is you, so one that runs `alt` changes routines and answers questions as your terminal
would; Altitude does not tell it apart. That is why the list above is yours to keep.

## What you see

**The form.** **What it may change** is the whole fence. After Worktrees and branches, inside the same
section, comes **Commands**:

- **Commands** is a smaller sub-label inside the section, not a heading of its own.
- Each command is a row: its name in bold, its words below in monospace, and two labelled buttons, **Edit**
  and **Remove** (aria-labels "Edit NAME" and "Remove NAME"), 44 px tall on the phone. Words wrap only at
  spaces; a single word longer than the line breaks anywhere.
- Under the rows, whenever one is listed, a note reads: "These run as you, outside Altitude's sandbox,
  whenever the routine decides. Altitude doesn't check what they do, and its model reads them and what they
  print."
- **Remove** takes the row away at once, as removing a trigger does; nothing changes until Save, and Cancel
  keeps it.
- **Add a command** opens a draft at the end of the list; **Edit** opens that row in place as a draft with
  its name and words. A draft has **Name** (hint: "Lowercase letters, digits and dashes; the routine asks
  for it by this name.") and **Command** (a monospace text area; hint: "Starts in harbor's folder, with its
  files as they are then. Up to 10 minutes, once a run at most."), then **Done** and **Cancel**, both
  secondary buttons, so Save stays the form's one primary action. Done checks the draft and shows errors
  under its fields: "Give it a name.", "Use lowercase letters, digits and dashes.", "Another command has that
  name.", "Write the command.", "Keep it under 2,000 characters." Cancel puts the row back as it was. While a
  draft is open, Add a command and the other rows' Edit and Remove are hidden.
- **Save** with a draft open saves nothing and the line beside it reads "Finish or cancel the command
  first." The form's own Cancel leaves without saving anything, drafts and Done commands included.
- At ten commands, Add a command gives way to the hint "Ten commands at most."
- The hint under the section reads "Nothing ticked or listed: it only reports." and shows only then.
- When saving would stop a command that is running now, the line beside Save reads "Saving stops
  prune-images, which is running."

**The routines list and the routine page.** The list line names what a routine may do after its
triggers: "Changes worktrees and branches", "Runs 2 commands", or both joined by " · ". The routine page's
lines under its definition read "May change worktrees and branches, with Undo" and "May run as you:
prune-images and clear-cache", each when it applies, or "Only reports".

**A run's page.** **What it changed** lists each command request in order, with the first step's rows:

| State | Row | Note |
| --- | --- | --- |
| Running | "Running prune-images" | "For 2 min." and a **Stop** button (aria "Stop prune-images"), 44 px tall on the phone; pressed, it reads "Stopping…" and is disabled until the row ends |
| Done | "Ran prune-images" | "Finished in 12 s." with an **Output** fold (44 px tall on the phone) showing the kept output in monospace |
| Failed | "Ran clear-cache" | "Exited with 1 after 3 s." in the danger tone, with **Output** |
| Timed out | "Ran prune-images" | "Stopped after 10 min." in the danger tone, with **Output** |
| Stopped | "Ran prune-images" | "You stopped it after 2 min." / "Stopped with the run after 4 min." / "Stopped when you changed the routine." with **Output** |
| Couldn’t start | "Couldn’t run prune-images" | "Altitude could not start it, so nothing ran." |
| Not allowed | "Not allowed: run prune-images" | "This routine has no command by that name." / "This command changed after the run started." / "This routine is retired." / "Another of its commands is still running." / "This run used its 20 requests." |
| Not a name | "Not allowed: a command" | "The run asked for something that is not a command name." |

A command row has no Undo, and Undo all skips it. A Stop that arrives after the command ended changes
nothing; the row already shows how it ended, and a command that exited 0 just as Stop arrived reads as done.

**Chat.** Altitude's sentence after the run's summary adds the commands that started: "Ran prune-images and
clear-cache." With a failure, a timeout or a start that failed: "Ran prune-images; clear-cache failed." With
a stop: "Ran prune-images; clear-cache was stopped." A refused request is on the run's page only.

## Records

Each request is a numbered change on the run (`kind` "command", `target` its name, `outcome` done, failed,
timed_out, stopped, not_started or refused, with `exit`, `seconds` and, when stopped, `by`: you, the run,
or a changed routine) and a `routine-action` project event with `what` "command". Each command's own record
follows a machine command's: its unit, log and exit file in the run's folder, settled after a restart of
Altitude as `alt task run`'s are.

## Decisions

Decided myself, open to change:

- No text from the run reaches any command. Text sent into a program running as you could steer it (a
  script that prompts, `ssh`, a mail command taking recipients from its input) or carry the repository
  out. So sending a report on to mail or a chat service, which ACTING.md listed as a command, moves to the
  step with alerts to your devices, where Altitude delivers what the run wrote itself, without a shell.
- Each command at most once a run: a run steered by what it read cannot repeat a command that is not safe
  to run twice, and no per-command setting is needed.
- Listed commands follow the operator grant's rule for the never-granted list, as the brief asks the fence
  to reuse the grant model, rather than a check Altitude could not enforce on a program running as you.
- Ten commands a routine, 2,000 characters each, 10 minutes each; output kept 16 KB on the page, 4,000
  characters to the run.
- No built-in routine uses commands in this step; the docs give three example commands instead.

- Commands start in the project's folder, as the hint says, rather than an empty one: `make clean` or
  `./scripts/tidy.sh` is the common case, and a command that should not follow the project names a path or
  `cd`s elsewhere.

## Mac

The runner already runs on the Mac as a launchd job. Owed on the Mac's verify issue: a routine's command
runs outside the run's Seatbelt box with the operator's access and empty input, and Stop on its row,
stopping the run and changing the routine each stop it.
