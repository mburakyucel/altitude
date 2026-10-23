# Routines that act: commands you list

Status: proposal for the second step of [ACTING.md](ACTING.md), which the operator asked for on 2026-10-23
after merging the first; revised after its design review. The boards are `DesktopCmdEdit`, `PhoneCmdEdit`,
`DesktopCmdRun`, `PhoneCmdRun`, `CmdStates` and `PhoneCmdStates` beside this file; this text outranks them.

## The idea

The first step built the general parts of a routine that acts: the fence on its form, the run asking
Altitude with `alt act`, Altitude's own check, the record on the run's page and the limits. This step adds
the generic action: **commands you list**. You write a command once on a routine's form; the routine
decides when it is needed and asks Altitude to run it by name; Altitude runs your exact words, as you, and
records what happened. Freeing memory, pruning container images, clearing a cache: anything a command can
do, without a model ever typing a command under your name. A routine can also **send its report on**
through one command you write, such as a post to your team's channel.

## What a command is

A listed command runs as you, outside every sandbox, so it can do anything you can, Altitude's own `alt`
included, as in your own terminal. Nothing confines a program running as you from inside, so the fence is
the list itself and what the run can pass in:

- **The run chooses only which command and when.** It sends a name and nothing else: no arguments, no
  input, no environment. A command's input is empty. Altitude starts it from your login environment without
  the run's door, session or files.
- **The words are yours; what they call is as you keep it.** A command that runs a script, `make` or
  `npm run` runs that file as the project's folder holds it then. The run cannot write that folder, but a
  merged change can. The form's hint says so, and a command that should not follow the project can name a
  script outside it.
- **Starting another routine, merging or answering in Altitude** happens only when you list a command that
  does it; that is your decision, written on the form. No action Altitude itself takes sends an event or
  starts a routine, as ACTING.md says.

## What the run can ask, and what Altitude checks

- **One request**: `alt act run NAME`. A name that is not lowercase letters, digits and dashes of at most
  40 characters is refused before it is recorded, so the run's free text never appears as a row.
- **Altitude runs it only when** a command of that name with the same words is on the routine both in the
  version the run started with and now, the routine is not retired, and the run has requests left (the 20
  a run the first step set, shared by every kind of action). Otherwise it is refused with a plain reason
  the run reads at once.
- **How it runs**: through the runner `alt task run` uses for an operator grant: your login shell
  (`bash -lc`), the project's folder as its working folder, as its own job that stops after 10 minutes. One
  command at a time per run; the run waits for its result. Its time counts toward the run's own limit.
- **What comes back**: the exit status and the last 4,000 characters of its output go back to the run, so it
  can say in its report what happened or try something else. The run's page keeps the last 16 KB.
- **What stops it**: **Stop** on its row (the run goes on and reads that you stopped it), stopping the run,
  the run reaching its limit, retiring the routine, or saving the routine without that command or with
  different words. Each stops the job by its name, after a restart of Altitude too, and is recorded.

## Sending the report on

Under **Tell me in chat**, an optional **Also send it with** field holds one command, which the run cannot
ask for. Whenever chat tells you about a run, after the run has ended, Altitude runs it once with that
message (Altitude's sentence and the run's summary, at most 4,000 characters) on its input, through the same
runner and limits. The run never reaches it, and it has no way to choose where the message goes unless the
command reads its destination from its input, which the hint warns against. With **Never**, nothing is sent.

## Who can set commands

Only your press on the form, or `alt agent set` in your own terminal, adds, changes or removes a command or
the report command, as only your press saves a routine. L3's offers cannot carry them: an offer for a
routine that has commands keeps them as they are. As with worktrees and branches, L3 cannot turn on,
resume or Run now a routine that has commands; it can pause, stop and undo.

## What you see

**The form.** **What it may change** is the whole fence. After Worktrees and branches, inside the same
section, comes **Commands**:

- Each command is a row: its name in bold, its words below in monospace, and two labelled buttons, **Edit**
  and **Remove** (aria-labels "Edit NAME" and "Remove NAME"), 44 px tall on the phone. Words wrap only at
  spaces; a single word longer than the line breaks anywhere. Removing takes effect on Save, as every form
  change does; Cancel keeps it.
- **Add a command** (or Edit) opens the row as a draft: **Name** (hint: "Lowercase letters, digits and
  dashes; the routine asks for it by this name.") and **Command** (a monospace text area; hint: "Runs as you
  in harbor's folder for up to 10 minutes, whenever the routine decides, with the scripts it calls as they
  are then."), then **Done** and **Cancel**. Done checks the draft and shows errors under its fields:
  "Give it a name.", "Use lowercase letters, digits and dashes.", "Another command has that name.", "Write
  the command.", "Keep it under 2,000 characters." While a draft is open, Add a command is hidden; at ten
  commands it goes.
- The hint under the section reads "Nothing ticked or listed: it only reports." and shows only then.

**Also send it with**, under Tell me in chat: a monospace field, empty by default, with the hint "Runs as
you with each message chat gets on its input. It must not take where to send from that input."

**The routines list and the routine page.** The list line names what a routine may do after its
triggers: "Changes worktrees and branches", "Runs 2 commands", or both joined by " · ". The routine page's
lines under its definition read "May change worktrees and branches, with Undo", "May run prune-images and
clear-cache", and "Sends its report on with a command", each when it applies, or "Only reports".

**A run's page.** **What it changed** lists each command request in order, with the first step's rows:

| State | Row | Note |
| --- | --- | --- |
| Running | "Running prune-images" | "For 2 min." and a **Stop** button (aria "Stop prune-images") |
| Done | "Ran prune-images" | "Finished in 12 s." with an **Output** fold showing the kept output in monospace |
| Failed | "Ran clear-cache" | "Exited with 1 after 3 s." in the danger tone, with **Output** |
| Timed out | "Ran prune-images" | "Stopped after 10 min." in the danger tone, with **Output** |
| Stopped | "Ran prune-images" | "You stopped it after 2 min." / "Stopped with the run after 4 min." / "Stopped when you changed the routine." with **Output** |
| Couldn’t start | "Couldn’t run prune-images" | "Altitude could not start it, so nothing ran." |
| Not allowed | "Not allowed: run prune-images" | "This routine has no command by that name." / "This command changed after the run started." / "This run used its 20 requests." |
| Not a name | "Not allowed: a command" | "The run asked for something that is not a command name." |
| Report sent | "Sent the report on" | "Finished in 1 s." with **Output**; failed: "Exited with 22 after 2 s." in the danger tone |

A command row has no Undo, and Undo all skips it.

**Chat.** Altitude's sentence after the run's summary adds the commands: "Ran prune-images and
clear-cache." With a failure: "Ran prune-images; clear-cache failed."

## Records

Each request is a numbered change on the run (`kind` "command", `target` its name, `outcome` done, failed,
timed_out, stopped, not_started or refused, with `exit`, `seconds` and, when stopped, `by`: you, the run,
or a changed routine) and a `routine-action` project event with `what` "command". Sending the report on is
a change of `kind` "report" with the same fields. Each command's own record follows a machine command's:
its unit, log and exit file in the run's folder, settled after a restart of Altitude as `alt task run`'s
are.

## Decisions

Decided myself, open to change:

- No text from the run reaches any command. Text sent into a program running as you could steer it (a
  script that prompts, `ssh`, a mail command taking recipients from its input) or carry the repository
  out, 20 times a run. The one use that needs text, sending a report on, is Altitude's step after the run,
  with a command the run cannot call.
- Ten commands a routine, 2,000 characters each, 10 minutes each; output kept 16 KB on the page, 4,000
  characters to the run.
- No built-in routine uses commands in this step; the docs give three example commands instead.
- Alerts to your devices, which ACTING.md paired with commands, wait for their own step.

For you: what a command prints goes back to the run and on to its engine's provider, as everything the
run reads does, so a command should not print secrets.

## Mac

The runner already runs on the Mac as a launchd job. Owed on the Mac's verify issue: a routine's command
runs outside the run's Seatbelt box with the operator's access, and Stop on its row, stopping the run and
changing the routine each stop it.
