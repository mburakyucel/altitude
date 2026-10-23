# Agent chats: proposal

Status: proposed for the operator's checkpoint on 2026-10-23, revised after its design and security review.

## What it is for

Sometimes you want to work with a coding agent directly: ask why something is slow, try an idea, poke at a
failing test, tidy something on this computer. Today that means a task, whose owner exists to deliver a
pull request, or the project terminal, which is awkward on a phone. So you reach for another app.

An **agent chat** is that ad hoc session inside Altitude: a conversation with one of your coding agents
about the project's code, from any device, on your own plan. It looks and works like a task's
conversation, with the same composer, images and voice, and it picks up where it left off every time.
There is no brief, no proposal and no pull request. When something in a chat is worth delivering, **Make
a task** turns it into a task that carries the chat's changes and conversation.

Chats are a convenience beside orchestration, not a second product. They live in Work under their own
heading, below the work in flight, and the docs describe them in a line or two.

## Decisions that are yours

Approving the design approves these recommendations; any can be changed first.

| Decision | Recommendation | Alternatives |
| --- | --- | --- |
| How much access it has | A **Computer access** switch per chat, off by default. Off, the agent is boxed like a task worker. On, after a sheet that says what it means, it can change the files in your home folder and run your programs, still inside Altitude's sandbox, which keeps your sign-ins and keys, Altitude's records, your other projects and other tasks' copies hidden and your services out of reach. A chip shows it until you turn it off | **One message at a time:** a composer toggle gives the next reply that access and turns itself off when the reply ends; no switch to forget, but a longer job means turning it on for each message. **Unconfined, like a repair:** it could do anything you can, but the hidden places above would only be rules it is asked to follow |
| Where chats sit | **Agent chats** in Work after **Done this week**, before **Routines** and **Altitude repairs**, with **New chat** on its heading; not in the graph | **In the Chat tab:** a short list with L3 first and your chats under it, keeping Work about tasks, at the cost of a step before L3 and a change to navigation you know |
| Make a task | Altitude makes the task itself from your words and the chat: its changes move to the task, the conversation goes with it as context, and the chat goes on in a fresh copy of main | L3 writes the brief from the chat, which means L3 reads it |
| Where the agent works | Its own copy of the code (a worktree and branch made from main at the first message), where it can edit, build and run tests; your checkout is never touched | **Read-only on the project checkout:** no copy to keep, but it cannot try a change or run anything that writes, and Make a task has no changes to carry |

Decided myself, within the brief: a chat keeps its conversation and its engine session until you delete it,
and its copy until you archive it; chats never appear in Needs you, since you are the one driving and a
question from the agent is just its reply; a running reply shows in Monitor like a routine run; messages
sent while it answers wait and go together as its next message; no time limit on a reply, Stop ends it; no
device alert when a reply ends in this version; `alt chats list` and `alt chats show` read chats from your
terminal, and every change comes from the app.

## How it works

**Starting.** **New chat** in Work opens an empty chat. Nothing is kept until you send: the first message
makes the chat, names it from its first line (rename any time), picks the coding agent and model the way
New tasks does, makes its copy of main and starts the session. The composer's model pill changes the model
for later messages.

**Each message is one turn.** A chat runs on the recorder a repair from the app runs on: a job of its own
outside Altitude's service that runs the agent one turn at a time, sends your waiting messages as the next
turn, writes each line the engine reports with its time, and stops when you press Stop. A repair launches
the agent unconfined; a chat launches it inside Altitude's sandbox, with the roots its access names. The
recorder starts when you send and ends when no message waits. The steps appear as it works, folded under
one line ("Working · 2 min · 6 steps") with the latest step showing, and its answer follows as the reply.
When the turn ends, the steps fold to "Worked 3 min · 14 steps", which opens to Live session's rows.

**It survives restarts.** The recorder runs outside Altitude's service, so a restart mid-reply loses
nothing and the reply appears once Altitude is back. A recorder that ended while Altitude was down, before
the reply, shows the reply as cut off, with Retry. The session lives in the chat's own engine folder, so
the next message continues it whenever you come back. When a session cannot be resumed, because its engine
lost it or you chose another engine, a new session starts with the conversation so far given to it as
quoted context, and a line says so. The conversation itself is always Altitude's own record.

**The copy.** A chat's copy is its own worktree and `chat-<id>` branch in the project's worktree folder,
made from the fetched main. It stays as long as the chat is open, however long the chat is idle, so nothing
you were trying disappears; when main moves on, the agent merges it when you ask. The tidy routine and
end-of-task cleanup leave names starting `chat-` alone. Archive removes the copy; when it has changes, the
Archive sheet names them and offers **Make a task first**. An archived chat reads only; Reopen makes a
fresh copy of main and continues the same session; **Delete** removes the conversation, the session and
the chat's images for good.

**Make a task.** The menu's **Make a task…** asks for a title (the chat's name to start) and what the task
should deliver. The press commits the copy's changes on the chat's branch and pins that commit, then
Altitude makes the task directly, as `alt task new` does: your words are its brief, its branch starts from
the pinned commit (or from main when the chat changed nothing), and the conversation goes into the task's
folder as quoted context. The chat's copy then starts again from main, so the changes live in one place
and a second task never carries the first one's. The chat shows the task's card and goes on. The press is
available when no reply is running and comes from a paired browser only.

## Access

**Boxed (the default).** The agent writes its copy, the project's Git store as a task worker does, and its own
folders, keeps the network
for installs and tests, reaches no user services, and has no GitHub sign-in, since a chat publishes
nothing. Its `alt` admits only the read commands a task worker has.

**Computer access.** You turn it on from the chat's menu in a paired browser, after a sheet that says what
it means. From the next message until you turn it off, each turn runs in a wider sandbox: it can write
your home folder and run your programs, besides its own copy, and keeps the network. Altitude's sandbox
still hides your stored sign-ins and keys and the engines' own places (the places it hides from every
job), Altitude's home and configuration, the project's other worktrees and your other projects; your
project checkout stays read-only, and the user service manager and desktop session stay out of reach, so
it cannot stop Altitude or your services. It has no GitHub sign-in and cannot use `sudo`. Its rules ask it
to check with you before deleting anything outside its copy, installing software or changing settings
outside the project. The sheet says that the access lasts until you turn it off and that a web page or file
the agent reads could try to steer it. The chip and a system line ("Computer access on · by you") keep it
visible; turning it off applies from the next message. The container installation has no computer access:
its sheet says it isn't available there.

Computer access stays confinement, so a repair remains the one agent session outside it, and the
never-granted list holds as limits the sandbox enforces: sign-ins and keys, Altitude's own records and
gates, other projects and other tasks' copies are out of the agent's reach whatever it is told.

**Who can see and steer a chat.** Starting, messaging, renaming, Make a task, computer access, archiving,
reopening and deleting come from a paired browser only and refuse the machine key and every Altitude
agent, as repair routes do. Reading a chat takes a paired browser or your own terminal; L3, task workers
and other chats are refused, so what a chat saw never reaches an agent that did not see it.

## Where chats show

- **Work**, on the phone's Work tab and the desktop's work panel: **Agent chats** after Done this week,
  rows in the task row's anatomy, most recent first, **Archived · N** folded at the end. Work's counts and
  the project's tasks in flight leave chats out, and Graph shows only Current.
- **The chat page** (`/projects/:name/chats/:id`, Work lit on the phone): a task page's header with the
  **‹ Work** crumb, the chat's name, Stop while it answers and **⋯** (Rename, Make a task…, Computer
  access…, Archive); chips for "Answering", the model on its engine and "Computer access"; one column of
  your messages, each turn's folded steps and its reply; the composer.
- **Monitor**: a running reply is a session row ("Chat · <name>") with its context, after the project's
  tasks.
- Not in Needs you, not in the rail's project dot, not in STATE.md or L3's task lists.

## Engines and spend

New chats take the engine and model New tasks would, through `route.pick_engine`; each turn uses your plan
like a short task turn, and no API key or pay-per-use path is involved. Turns do not wait for a task slot,
since you are waiting for them; when the engine cannot run, the reply line gives its reason and the model
pill offers another. Engine differences stay behind the seam.

## API and command line

`GET /api/chats/<project>` lists a project's chats; `GET /api/chats/<project>/<id>` is one chat with its
conversation, running turn and steps; `POST /api/chats` takes `new`, `message`, `rename`, `stop`, `access`,
`task`, `archive`, `reopen` and `delete`, each with a request id whose repeat returns the first result.
Records live in `chats/<project>/<id>/` under Altitude's home, a folder every agent's sandbox hides: `chat.json` (name, engine, model, session,
copy, access, archived) and `conversation.jsonl`; images go to the project's image store. `alt chats list
[--archived] [--json]` and `alt chats show <chat> [--json]` read them and are in no agent's `alt`.

## What it reuses and removes

It adds a record kind and reuses the rest: the task page's header, conversation rows, composer and outbox;
Live session's rows; the repair recorder, whose confinement becomes a launch parameter; Altitude's sandbox
with wider roots; routing; the task worktree helpers; the repair routes' caller checks; task creation.
Nothing existing becomes unnecessary, so nothing is removed.

## Rollout

Three held pull requests in this task, each with tests, docs, phone and desktop walkthroughs and an
independent review:

1. **Agent chats.** The Work section, the chat page, new, rename, archive, reopen and delete, turns on the
   shared recorder that resume and survive restarts, the model pill, Stop, waiting messages, the Monitor
   row, `alt chats list|show`, and the docs with one README line.
2. **Make a task.** The sheet and press, the pinned commit, the task made from it and the chat's fresh copy.
3. **Computer access.** The switch, its sheet and chip, the wider sandbox on Linux and macOS, and a security
   review.

Deterministic tests cover: a chat's turns resume one session, a message during a turn waits and goes next,
Stop ends the job; a turn survives a service restart and a lost one reads cut off; an unresumable session
restarts with the conversation as quoted context; the copy is made from main, kept while open, removed on
archive only after the unsaved-changes answer, and ignored by tidy and task cleanup; Make a task pins one
commit per press, the task starts from it and the chat's copy starts again from main; every route refuses
agents, L3, the machine key and replays; no agent's sandbox shows the chats, apart from a turn's own engine folder; with computer access
the hidden places stay unreadable and the checkout read-only, only from a paired browser and only from the
next turn. Engine calls are fixtures at the engine seam; live engine behaviour and the Mac's native
confinement stay listed on the issue for the Mac installation.

## Screens

Fictional data. Phone 390×844, desktop 1440×900. Boards, written by `gen.py` beside this file:

- [Work with chats, phone](PhoneWork.html) and [with none yet](PhoneWorkEmpty.html)
- [Work with chats, desktop](DesktopWork.html)
- [A new chat, phone](PhoneNew.html)
- [A chat answering, phone](PhoneChat.html)
- [The chat's menu](PhoneMenu.html), [Computer access](PhoneAccess.html), [Make a task](PhoneTask.html),
  [Archive with changes](PhoneArchive.html), phone
- [A chat with computer access and a task made from it, desktop](Desktop.html)
- [Every state](States.html)

### Exact text and states

**Work's Agent chats section.** Heading "Agent chats" with the count of open chats, and **+ New chat**
(bordered pill, 44px touch target) at its right. Rows: dot, name on one line with an ellipsis, meta on its
own line: "Starting · Opus on Claude" and "Answering · Opus on Claude" (running dot); "Replied 12 min ago"
and "You wrote yesterday" (idle dot); "Computer access · replied 2 h ago" (idle dot, "Computer access" in
amber); "Couldn't answer · 1 h ago" (red dot). Ordered by the latest message either way. **Archived · N**
folds the archived rows, muted, meta "Archived Oct 22". With no chats the section is its heading and New
chat. While its read loads the section is absent; a failed read keeps saved rows, as Current does.

**The chat page.** Desktop header: **‹ Work**, the dot and name (wrapping), Stop (104px, Esc) while
answering, **⋯**; chips "Answering" (accent), "Opus on Claude", "Computer access" (amber). Phone header:
back, name on one line, status line "● Answering · Opus on Claude" or "● Opus on Claude" (with " · Computer
access" when on), Stop while answering, else **⋯**: while it answers, Rename and the menu wait for Stop or
the reply's end. A new chat's page reads "New chat" with the muted line "Works in its own copy of atlas's
code, made from main when you send." System lines: "Started · Opus on Claude · its own copy of main",
"Making its copy of main…", "Stopped by you · the command it was running was cut off", "Altitude restarted
· the reply continued", "New session on Codex · it was given this conversation so far", "Computer access on
· by you", "Computer access off · by you", "Task made by you · its changes moved to the task; a fresh copy
of main", "Archived by you", "Reopened · a fresh copy of main". Failures, red, with Retry: "Couldn't answer:
<the engine's reason>. Choose another model below, or Retry after it resets.", "The reply was cut off:
Altitude stopped while it ran." A waiting message: your bubble, muted, "Waits for the reply · Remove".
Composer: "Message the agent", images, voice, the model pill. Archived: no composer; "Archived · Oct 22",
**Reopen** and **Delete…**, which asks "Delete this chat?" ("Its conversation and session are removed for
good.", **Delete chat** in red, Cancel). Loading: prose-shaped skeleton rows; failed: "Couldn't load this
chat." and Retry.

**Menu** (phone bottom sheet, desktop popover from ⋯): Rename; Make a task… (disabled while answering,
described "Available when the reply ends"); Computer access… or "Computer access is on · Turn off";
Archive; then muted "Opus on Claude · started Oct 21 · its copy: 1 changed file". Rename edits the name in
place: 1–80 characters, Enter or Save saves, Escape or Cancel restores.

**Computer access sheet**: "Let this chat work on your computer?", "From your next message until you turn
it off, its agent can **read and change the files in your home folder** and run your programs, as well as
its own copy of the code. What it reads goes to Claude, and a web page or file it reads could try to steer
it.", "Your sign-ins and keys, Altitude's records, your other projects and other tasks' copies stay hidden
from it, and your services stay out of reach.", **Turn on computer access** (primary), **Cancel**; saving
"Turning on…"; in the container installation, in red, "Computer access isn't available in the container
installation."

**Make a task sheet**: "Make a task from this chat", "Title" (the chat's name), "What should it deliver?"
(grows with its text, placeholder "For example: ship the faster export with a test"), muted "Its changes
(1 file) move to the task, with this conversation as its context. The chat goes on in a fresh copy of
main." (without changes: "It starts from main, with this conversation as its context."), **Make task**
(disabled while either field is empty; "Making…"), Cancel. Then the system line and the task card under it.

**Archive sheet** (only when the copy has changes; otherwise Archive acts at once): "Archive this chat?",
"Its copy of the code has changes no task has: **1 changed file**. Archiving deletes them. The
conversation stays, and Reopen starts a fresh copy from main.", **Make a task first**, **Archive and delete
changes** (danger), Cancel.
