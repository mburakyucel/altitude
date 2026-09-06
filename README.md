# Altitude

Altitude turns a project-level conversation into isolated, reviewable work without making Burak
manage agent plumbing. L3 is the project coordinator. Each active task has one directly reachable
L2 owner, and that L2 may work alone or delegate bounded slices to its engine's own subagents.

Start with [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the current system and
[`docs/ROADMAP.md`](docs/ROADMAP.md) for the remaining product work.
The approved wireframes for the simplified product's UI are in
[`design/wireframes/README.md`](design/wireframes/README.md).

The 2026-09 module-by-module simplification is complete. [`docs/SIMPLIFICATION.md`](docs/SIMPLIFICATION.md)
records Burak's paradigm decisions, the working rules that still apply to every PR, and what each
phase deleted.

## Current operating model

- L3 answers directly or creates one queued task, owned end-to-end by one L2, when concrete execution is warranted.
- Burak discusses roadmap and project direction with L3, and task-specific choices directly with
  the task's L2. Messages sent while L3 is busy queue and run at the next turn boundary, in order.
- Every code change uses an isolated worktree and branch, then a PR. The L2 may merge after the
  applicable checks and review unless an explicit merge hold says otherwise.
- Fresh L2 work and each L3 turn choose Claude Code or Codex weekly-first, record the reason, and
  preserve separate provider sessions; one provider's short-window limit does not freeze the other.
- L3 is read-only on the deployment checkout on both engines. Its runtime `alt` and narrow external
  reads cross its project's role-fenced altd socket; reason-bearing worker operations become durable requests that altd validates and executes.
- Resource usage is shown, never acted on: the Monitor page reports both seats' windows, their
  reset times and how old each reading is, and which engine each role would get right now and why.
- Deferred work is recorded in a GitHub issue and removed from the active task set. Completed and
  rejected tasks are archived immediately.
- An L2's question goes to L3 first, which answers from the record or escalates one plain dilemma;
  Burak sees only what L3 escalates or what the L2 flags for him.
- A system fault blocks only its own task, records private incident evidence, and leaves one
  message for the project's L3, which records the learning and fixes the cause directly or creates
  one ordinary task. An incident raised by that repair task goes to the Inbox instead of waking L3
  again.
- Every conversational composer has the same optional microphone control. Stopping a recording
  produces a transcript review without changing the draft; **Edit / insert** appends it to the
  editable draft and **Send** uses that composer's normal behavior, including Chat's **Queue** path
  while L3 is busy. Cancel, denial, and transcription failure leave typing and the draft available.

## Repository and runtime

`altitude/` is a standard-library Python package. `bin/alt` is the CLI and the only door a worker
has into Altitude: the backend validates every command against the task record under the project
lock. `personas/` contains the L2 and L3 roles, and `schemas/` defines code-delivery reports. `hooks/` holds the Git hooks installed into every managed
repository, the Claude inbox hook, and the statusline monitor. A Codex L2 runs in Codex's own
workspace-write sandbox and uses the same door. Claude and Codex L3 turns both run from fresh disposable
runtime directories with the deployment checkout and Altitude state read-only. Codex's L3 profile denies direct
command networking and the user-service bus, exposing only that project's role-fenced Unix socket. `web/` is the React UI built into
`web/dist/` for the Python server to serve.

Runtime state lives under `ALTITUDE_HOME` (default `~/.altitude`): project configuration, active
tasks, archived tasks, L3 and L2 conversations, monitor snapshots, and private
incident evidence. Runtime state is not source-controlled.

Useful inspection commands print a compact text view; add `--json` for the complete record. Task
status keeps its complete JSON view and adds `--brief` for compact orientation.

```sh
make test
make web
bin/alt --project <name> task report <slug>
bin/alt --project <name> task messages <slug> --last 5
bin/alt --project <name> task events <slug> --last 5
bin/alt --project <name> task status <slug> --brief
bin/alt queue
bin/alt --project <name> repo
bin/alt --project <name> pr <number>
bin/alt --project <name> l3 tools --days 7
```

[`docs/CLI.md`](docs/CLI.md) is the inspection and task-lifecycle reference. `alt monitor` remains the separate
quota and live-session view.

## Service lifecycle

Ordinary development and code agents must not start, stop, mask, unmask, or restart the service.
Altitude activates merged backend and web changes itself. A self-deploy fast-forward marks activation
pending for loaded backend paths (`altitude/`, `bin/`, `systemd/`) or tracked web build inputs
(`web/src/`, `web/design/tokens.css`, `web/index.html`, `web/package.json`, `web/pnpm-lock.yaml`,
`web/tsconfig.json`, `web/vite.config.ts`). Web docs and other non-build files do not trigger it. New
dispatches hold, and once no L2 is running, no report is waiting and no L3 turn is in flight, `altd`
runs the guarded restart script below as a transient user unit. A restart that has not happened ten
minutes after it was requested is a system fault for L3 and the hold lifts.

To restart sooner by hand, press Restart on the web app's restart-pending banner (shown
once nothing is running) or run `make restart` from the deployed primary checkout. The
command refuses another clone/worktree, a non-exact or dirty `main`, and active L2 or report work; a
blocked task whose Claude job sits idle does not hold it, since that job survives the restart and is
re-attached on resume.
It installs the locked web dependencies, builds and validates a staged bundle, swaps it into the
ignored runtime `web/dist`, restarts the user-level `altitude.service`, and waits for both its API and
web page to answer from a new process. The prior bundle is restored if verification fails. There is
no separate web service and no `sudo` is required. Node 22+ and `pnpm` are required; dependency
retrieval may be needed when the local pnpm store is cold. Refresh the browser after it succeeds.

## Voice input on iPhone

Open Altitude at `https://10.88.0.1:8890` through WireGuard. Safari exposes the microphone only in a
secure context, so the phone must trust the local CA used by Altitude's certificate; `/ca.crt` serves
that CA when it needs to be installed. The microphone button remains a typing-only hint on plain
HTTP or an unsupported browser.

The browser records at most ten minutes as AAC/mp4 on iOS or opus/webm where available. Altitude
converts the upload with `ffmpeg` in a temporary directory and sends the resulting 16 kHz mono WAV
path to the existing local faster-whisper socket, with the loopback Whisper bridge as fallback. Raw
audio is deleted after every success or failure and is never part of task or chat state. A recording
is not a message: review its text, then choose **Edit / insert**, **Send** (or **Queue** while L3 is
busy), or discard it.

For a manual Safari check, open each of Chat, a task Conversation, and a project task's **Message
L2** panel; record and stop; confirm the transcript appears separately from an existing draft; try
both insertion and Send/Queue; then deny microphone access once and confirm the typed draft remains
usable. If Safari reports that voice needs HTTPS, use the secure URL above and verify the local CA is
enabled under Certificate Trust Settings.
