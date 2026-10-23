# Chats: proposal

Status: proposed for the operator's checkpoint on 2026-10-23.

## What it is for

Sometimes you want to work with a coding agent directly: ask why something is slow, try an idea, poke at a
failing test, tidy something on this computer. Today that means a task, whose owner exists to deliver a
pull request, or the project terminal, which is awkward on a phone. So you reach for another app.

A **chat** is that ad hoc session inside Altitude: a conversation with one of your coding agents about
the project's code, from any device, on your own plan. It looks and works like a task's conversation,
with the same composer, images and voice, and it picks up where it left off every time. There is no
brief, no proposal and no pull request. When something in a chat is worth delivering, **Make a task**
hands it to L3, which starts a task from the chat and its changes.

Chats are a convenience beside orchestration, not a second product. They live in Work under their own
heading, below the work in flight, and the docs describe them in a line or two.

## Decisions that are yours

Approving the design approves these recommendations; any can be changed first.

| Decision | Recommendation | Alternatives |
| --- | --- | --- |
| Where the agent works | Its own copy of the code (a worktree and branch made from main at the first message), where it can edit, build and run tests; your checkout is never touched | **Read-only on the project checkout:** no copy to keep, but it cannot try a change or run anything that writes, and Make a task has no changes to carry |
| How much access it has | A **Full access** switch per chat, off by default: off, the agent is boxed like a task worker; on, after a sheet that says what it means, it works as you outside the sandbox from the next message, with a chip on the chat until you turn it off | **Every chat as you from the start:** nothing to switch, but every chat, including one reading an untrusted web page, can do anything you can. **Never:** boxed only; anything outside its copy stays a command for your terminal |
| What a chat keeps | The conversation always; the engine session for as long as the chat is open; the copy until you archive the chat | Remove an idle chat's copy after 30 days without a message, keeping the conversation |
| Where chats sit in Work | **Chats** after **Done this week**, before **Routines** and **Altitude repairs**, with **New chat** on its heading; not in the graph | Directly under **Current**, above Done this week; or as their own tab beside Chat and Work |
| Make a task | One press asks L3, which writes the brief from the chat and starts the task from the chat's changes; the chat goes on | Altitude makes the task itself from your words, without L3, as a quick task with the chat attached |
| The command line | `alt chat list` and `alt chat show` read chats, for you and L3; every change comes from the app | The full set (`new`, `send`, `rename`, `archive`) from your terminal too, refused for a chat with full access |

Decided myself, within the brief: chats never appear in Needs you, since you are the one driving and a
question from the agent is just its reply; a chat's running reply shows in Monitor like a routine run;
messages sent while it answers wait and go together as its next message; no time limit on a reply, Stop
ends it; no device alert when a reply ends in this version.

## How it works

**Starting.** **New chat** in Work opens an empty chat. Nothing is kept until you send: the first message
makes the chat, names it from its first line (rename any time), picks the coding agent and model the way
New tasks does, makes its copy of main and starts the session. The composer's model pill changes the model
for later messages.

**Each message is one turn.** Altitude runs the agent in its own job, resuming the chat's session with
your message, exactly as it resumes a task's owner. The steps it takes appear as it works, folded under
one line ("Working · 2 min · 6 steps") with the latest step showing, and its answer follows as the reply.
When the turn ends, the steps fold to "Worked 3 min · 14 steps", which opens to Live session's rows. A
message sent while it answers waits under the reply and goes as the next turn; Stop ends the turn at once.

**It survives restarts.** The turn's job runs outside Altitude's service, so when Altitude restarts
mid-reply the agent keeps working and the reply appears once Altitude is back. A turn whose job ended
while Altitude was down without an answer says it was cut off, with Retry. The session lives in the
chat's own engine folder, so the next message continues it whenever you come back. When a session cannot
be resumed, because its engine lost it or you chose another engine, a new session starts with the
conversation so far given to it as quoted context, and a line says so. The conversation itself is always
Altitude's own record.

**The copy.** A chat's copy is its own worktree and `chat-<id>` branch under the project's worktree folder,
made from the fetched main. It stays as long as the chat is open, however long the chat is idle, so
nothing you were trying disappears. Archive removes it; when it has changes no task has, the Archive sheet
names them and offers **Make a task first**. An archived chat keeps its conversation and reads only; Reopen
makes a fresh copy of main and continues the same session. The tidy routine and end-of-task cleanup leave
chat copies alone.

**Make a task.** The menu's **Make a task…** asks what the task should deliver. The press saves the chat's
uncommitted changes as one commit on its branch and sends L3 the request in project chat, as the Create
task button does. L3 reads the chat, writes the brief and creates the task with the chat named as its
source; the task's branch starts from that commit, or from main when the chat changed nothing. The chat
shows the task's card and goes on independently. It is available when no reply is running.

## Access

**Off (the default).** The agent is boxed like a task worker: it writes only its copy, its branch and its
own folders, keeps the network for installs and tests, reaches no user services, and has no GitHub
sign-in, since a chat publishes nothing. Its `alt` admits only the read commands a task worker has.

**Full access.** You turn it on from the chat's menu in a paired browser, after a sheet that says what it
means. From the next message, each turn runs as you outside every sandbox, through the engine seam the way
a repair from the app runs: it can read your files, change anything you could, use your services and your
GitHub login, and what it reads goes to the coding agent. It still starts in its own copy. Its rules say
never to merge, approve, grant or answer for you, never to read or print sign-ins and keys, and to leave
other projects alone; the engine's deny rules refuse the clearest cases, its `alt` refuses those verbs
under the chat's name, and routes that refuse Altitude's own agents refuse it. At full access these are
rules it follows, not limits Altitude can enforce, and the sheet says so. Turning it off applies from the
next message. The chip and a system line ("Full access on · by you") keep it visible.

This changes one rule: a repair is today the one agent session that runs outside confinement. Approving
full access adds a second, written exactly: a chat runs unconfined only after you switch it on for that
chat from a paired browser, never through a grant, a `run` block, a standing policy, L3 or an agent's
request. The never-granted list is unchanged.

**Who can steer a chat.** Starting, messaging, renaming, archiving, reopening, Make a task and Full access
come from a paired browser only and refuse Altitude's own agents and the machine key, as repair routes do,
so nothing that holds the key can drive an agent with your access. L3 reads chats to make a task from one;
it never sends to them.

## Where chats show

- **Work**, on the phone's Work tab and the desktop's work panel: **Chats** after Done this week, rows in
  the task row's anatomy, most recent first, **Archived · N** folded at the end. Work's counts and the
  project's tasks in flight leave chats out, and Graph shows only Current.
- **The chat page** (`/projects/:name/chats/:id`, Work lit on the phone): a task page's header with the
  **‹ Work** crumb, the chat's name, Stop while it answers and **⋯** (Rename, Make a task…, Full access…,
  Archive); chips for "Answering", the model on its engine and "Full access"; one column of your messages,
  each turn's folded steps and its reply; the composer.
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
conversation, running turn and steps; `POST /api/chats` takes `new`, `message`, `rename`, `stop`,
`access`, `task`, `archive` and `reopen`, each with a request id whose repeat returns the first result.
Records live in `<project>/chats/<id>/` under Altitude's home: `chat.json` (name, engine, model, session,
copy, access, archived) and `conversation.jsonl`; images go to the project's image store. `alt chat list
[--archived] [--json]` and `alt chat show <chat> [--json]` read them; `alt task new --from-chat <chat>` is
L3's half of Make a task.

## What it reuses and removes

It adds a record kind and reuses the rest: the task page's header, conversation rows, composer and outbox;
Live session's rows; the worker job with its restart adoption and session resume; routing; the task
worktree helpers and tidy's exclusions; the repair's full-access session and route checks; the Create
task press. Nothing existing becomes unnecessary, so nothing is removed.

## Rollout

Three held pull requests in this task, each with tests, docs, phone and desktop walkthroughs and an
independent review:

1. **Chats.** The Work section, the chat page, new, rename, archive and reopen, turns that resume and
   survive restarts, the model pill, Stop, waiting messages, the Monitor row, `alt chat list|show`, and
   the docs with one README line.
2. **Make a task.** The sheet and press, L3's `--from-chat`, and the task starting from the chat's commit.
3. **Full access.** The switch, its sheet and chip, unconfined turns through the engine seam, the rule in
   AGENTS.md, and a security review.

Deterministic tests cover: a chat's turns resume one session, a message during a turn waits and goes
next, Stop ends the job; a turn survives a service restart and a lost one reads cut off; an unresumable
session restarts with the conversation as quoted context; the copy is made from main, kept while open,
removed on archive only after the unsaved-changes answer, and ignored by tidy and task cleanup; Make a
task commits once per press and the task starts from it; every write route refuses agents, L3, the machine
key and replays; a boxed chat cannot write outside its copy; full access only from a paired browser and
only from the next turn. Engine calls are fixtures at the engine seam; live engine behaviour and the Mac's
native confinement stay listed on the issue for the Mac installation.

## Screens

Fictional data. Phone 390×844, desktop 1440×900. Boards, written by `gen.py` beside this file:

- [Work with chats, phone](PhoneWork.html) and [with none yet](PhoneWorkEmpty.html)
- [Work with chats, desktop](DesktopWork.html)
- [A new chat, phone](PhoneNew.html)
- [A chat answering, phone](PhoneChat.html)
- [The chat's menu](PhoneMenu.html), [Full access](PhoneAccess.html), [Make a task](PhoneTask.html),
  [Archive with changes](PhoneArchive.html), phone
- [A chat with full access and a task made from it, desktop](Desktop.html)
- [Every state](States.html)

### Exact text and states

**Work's Chats section.** Heading "Chats" with the count of open chats, and **+ New chat** (bordered
pill, 44px touch target) at its right. Rows: dot, name on one line with an ellipsis, meta on its own line:
"Starting · Opus on Claude" and "Answering · Opus on Claude" (running dot); "Replied 12 min ago", "You
wrote yesterday" and "Full access · replied 2 h ago" (idle dot); "Couldn't answer · 1 h ago" (red dot).
Ordered by the latest message either way. **Archived · N** folds the archived rows, muted, meta "Archived
Oct 22". With no chats the section is its heading and New chat. While its read loads the section is
absent; a failed read keeps saved rows, as Current does.

**The chat page.** Desktop header: **‹ Work**, the dot and name (wrapping), Stop (104px, Esc) while
answering, **⋯**; chips "Answering" (accent), "Opus on Claude", "Full access" (amber). Phone header: back,
name on one line, status line "● Answering · Opus on Claude" or "● Opus on Claude" (with " · Full access"
when on), Stop while answering, else **⋯**. A new chat's page reads "New chat" with the muted line "Works
in its own copy of atlas's code, made from main when you send." System lines: "Started · Opus on Claude ·
its own copy of main", "Making its copy of main…", "Stopped by you · the command it was running was cut
off", "Altitude restarted · the reply continued", "New session on Codex · it was given this conversation
so far", "Full access on · by you", "Full access off · by you", "You asked L3 for a task · “<first
words>”", "Archived by you", "Reopened · a fresh copy of main". Failures, red, with Retry: "Couldn't
answer: <the engine's reason>. Choose another model below, or Retry after it resets.", "The reply was cut
off: Altitude stopped while it ran." A waiting message: your bubble, muted, "Waits for the reply ·
Remove". Composer: "Message the chat", images, voice, the model pill. Archived: no composer; "Archived ·
Oct 22" and **Reopen**. Loading: prose-shaped skeleton rows; failed: "Couldn't load this chat." and Retry.

**Menu** (phone bottom sheet, desktop popover from ⋯): Rename; Make a task… (disabled while answering,
described "Available when the reply ends"); Full access… or "Full access is on · Turn off"; Archive; then
muted "Opus on Claude · started Oct 21 · its copy: 1 changed file". Rename edits the name in place:
1–80 characters, Enter or Save saves, Escape or Cancel restores.

**Full access sheet**: "Give this chat full access?", "From your next message, its agent works **as you,
outside the sandbox**: it can read any of your files, change anything you could, and use your services
and GitHub sign-in. What it reads goes to Claude.", "It still works in its own copy of the code and is
told never to merge, approve or answer for you in Altitude, or to touch sign-ins and keys. At full access
those are rules it follows, not limits Altitude can enforce.", **Turn on full access** (primary),
**Cancel**; saving "Turning on…"; refused, Altitude's reason in red.

**Make a task sheet**: "Make a task from this chat", "What should the task deliver?" (grows with its
text, placeholder "For example: ship the faster export with a test"), muted "L3 writes the brief from this
chat and starts the task from this chat's changes (1 file). The chat stays as it is." (without changes:
"…and starts the task from main."), **Ask L3 for the task** (disabled while empty; "Sending…"), Cancel.
Then the system line and, once L3 makes it, the task card under it.

**Archive sheet** (only when the copy has changes no task has; otherwise Archive acts at once): "Archive
this chat?", "Its copy of the code has changes no task has: **1 changed file**. Archiving deletes them.
The conversation stays, and Reopen starts a fresh copy from main.", **Make a task first**, **Archive and
delete changes** (danger), Cancel.
