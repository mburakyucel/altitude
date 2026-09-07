# Altitude

Altitude turns a project-level conversation into isolated, reviewable work without making Burak
manage agent plumbing. L3 is the project coordinator. Each active task has one directly reachable
L2 owner, and that L2 may work alone or delegate bounded slices to its engine's own subagents.

Start with [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the current system and
[`docs/ROADMAP.md`](docs/ROADMAP.md) for the remaining product work.
The product design, boards plus the spec that governs them, is in
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
- Tasks dispatch up to 8 running per project by default and 10 across the machine, subject to
  engine availability. Leases declare staging scope; overlapping tasks may run together. Briefs
  name shared paths and ask owners to rebase onto main before landing and edit only their own
  sections in shared docs. `alt task status --brief` shows informational overlaps; `alt land`
  refuses changes outside the declared lease.
- Fresh L2 work and each L3 turn choose Claude Code or Codex weekly-first, record the reason, and
  preserve separate provider sessions; one provider's short-window limit does not freeze the other.
  Codex model and reasoning effort are read from the running turn's rollout by altd and recorded on
  the task or L3 session. Explicit model pins remain separate from this observation; otherwise the
  CLI selects its model. Task status and header chips show the recorded name.
- L3 is read-only on the deployment checkout on both engines. Its runtime `alt` and narrow external
  reads cross its project's role-fenced altd socket; reason-bearing worker operations become durable requests that altd validates and executes.
- Resource usage is shown, never acted on: the Monitor page shows one seat card per configured
  engine with its windows, their reset times, the 70% reserve line and how old each reading is;
  which engine each role would get right now and why; and the sessions the monitor knows with their
  tasks, each with its engine and the model when the API reports one.
- The web UI is one shell. On a desktop a rail carries Needs you with its count, one row per
  managed project with a state dot and its count of waiting decisions, the folders not yet managed,
  one readout row per configured engine, Monitor, and the operator row with the theme toggle. On a
  phone a 54px header and a four-tab bar (Chat, Work, Needs you, Monitor) replace it. `/` is Needs
  you, every decision across projects as cards; `/projects/<name>` is the project's L3 conversation
  with its work panel; `/projects/<name>/tasks/<slug>` is the task page, the operator's conversation
  with the L2 beside the worker's live session read as a transcript, Stop and Reject behind an inline
  confirm, and on a phone the Conversation and Live session tabs;
  `/projects/<name>/tasks/<slug>/report` is the task's report view, linked from a landed report's
  card in the conversation; with no managed project every project route shows First run, which
  starts L3 for a folder. The selected project persists per
  browser, and every badge counts decisions only.
- Deferred work is recorded in a GitHub issue and removed from the active task set. Completed and
  rejected tasks are archived immediately.
- An L2's question goes to L3 first, which answers from the record or escalates one plain dilemma;
  Burak sees only what L3 escalates or what the L2 flags for him.
- A system fault blocks only its own task, records private incident evidence, and leaves one
  message for the project's L3, which records the learning and fixes the cause directly or creates
  one ordinary task. An incident raised by that repair task goes to Needs you instead of waking L3
  again.
- The project conversation shows the operator's messages as bubbles and L3's replies as prose under
  day dividers. A turn altd triggered (a landed report, a block, an incident, a recovery, a restart)
  folds to one muted line, red-dotted for an incident, a recovery, or a fault, with **Show** opening
  what altd sent L3, L3's reply, and links to the task, its full report, and its digest; a run of
  them between two operator messages folds to one line that expands to the list.
- Every conversation uses one composer with the same optional microphone control and an arrow in
  an accent circle for sending in every state, with no visible Send or Queue label. While listening,
  **Cancel** discards the recording, **Stop** transcribes into the editable draft, and the arrow
  transcribes, appends to the draft, and sends at once through the normal path. While L3 is busy the
  arrow queues, and the hint reads "L3 is mid-turn · runs next". Cancel, denial, and transcription
  failure leave typing and the draft available.

## Repository and runtime

`altitude/` is a standard-library Python package. `bin/alt` is the CLI and the only door a worker
has into Altitude: the backend validates every command against the task record under the project
lock. `personas/` contains the L2 and L3 roles, and `schemas/` defines code-delivery reports. `hooks/` holds the Git hooks installed into every managed
repository, the Claude inbox hook, and the statusline monitor. A Codex L2 runs in Codex's own
workspace-write sandbox and uses the same door. Claude and Codex L3 turns both run from fresh disposable
runtime directories with the deployment checkout and Altitude state read-only. Codex's L3 profile denies direct
command networking and the user-service bus, exposing only that project's role-fenced Unix socket. `web/` is the React UI built into
`web/dist/` for the Python server to serve. That server also serves any project's wireframe
boards read-only from the project's own checkout at `/design/<project>`, which the project header's
overflow menu offers as Design boards when the boards exist.

Runtime state lives under `ALTITUDE_HOME` (default `~/.altitude`): project configuration, active
tasks, archived tasks, L3 and L2 conversations, monitor snapshots, and private
incident evidence. Runtime state is not source-controlled.

Useful inspection commands print a compact text view; add `--json` for the complete record. Task
status keeps its complete JSON view and adds `--brief` for compact orientation.

```sh
make test
make web
make ui
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

Project verbs are `alt project add <name> [--path PATH] [--wip N]`, `list`, `discover`,
`remove <name>`, and `set <name> --wip N --reason "…"` (or `--unset-wip --reason "…"`).
Registration stores WIP only when supplied; otherwise the project inherits the default of 8.
L3 can set its own project's cap from 1 to the machine cap of 10 or unset it; add and remove are
operator-only. A set persists a reason-bearing request that altd applies on its next tick, without
a PR, restart, or free task slot. The first registry load removes stored legacy caps of 3 once and
logs the migration; approval and engine pins are preserved, and subsequent explicit caps of 3 persist.

The UI suite uses Playwright from a plain shell on every engine. With Node 22+ and pnpm available,
install once with `pnpm --dir web install --frozen-lockfile` (in a restricted worktree, add
`--store-dir /tmp/altitude-ui-pnpm-store` to keep the package store writable). `make ui` runs route
smoke and component walkthroughs headlessly at 390×844 and 1440×900 against the local service at
`https://10.88.0.1:8890`. Set `UI_BASE_URL` to target a throwaway altd on an unreserved port or a
Vite dev server; Vite's `ALTITUDE_DEV_API` points its API proxy at that service. The suite reads a
managed project and a real active or archived task; `UI_PROJECT` and `UI_TASK` select them when
needed. A throwaway service needs those records populated. Missing data fails explicitly, never
silently skips route coverage. It does not create projects, send messages, or dispatch tasks.

The harness prefers bundled Chromium (`channel: "chromium"`), with its browser sandbox disabled
inside the worker's filesystem sandbox. Incident I-20260907-041446 identifies the installed Chrome
AppArmor profile denying network sockets there. Install the bundle once per Playwright version:

```sh
PLAYWRIGHT_BROWSERS_PATH="${ALTITUDE_HOME:-$HOME/.altitude}/browsers" pnpm --dir web exec playwright install chromium
make ui
```

`pnpm ui` and `make ui` share that writable cache across task worktrees; `PLAYWRIGHT_BROWSERS_PATH`
overrides it for installation and execution together. Installed Chrome (`channel: "chrome"`) is
the fallback only when the bundled browser is absent. Profiles are temporary; no visible desktop
window opens. See [Playwright browser setup](https://playwright.dev/docs/browsers).
Crashpad's writable configuration also stays under `web/ui-artifacts/browser-config/`.

`web/e2e/*.pw.ts` specs stay separate from the Vitest unit suite (`pnpm --dir web test`). Both
viewport projects run each spec; `walkthrough.ts` asserts appearances and removals and captures
named states. `project-menu.pw.ts` walks closed, open, reset confirmation, cancelled and dismissed
states without confirming a reset. Screenshots, traces and the HTML report stay under ignored
`web/ui-artifacts/`, grouped by spec and viewport. These contain real service data: keep them local
and reference the proving spec in the PR. For a human-requested headed run of one spec:
`make ui UI_ARGS='project-menu.pw.ts --project=desktop --headed'`. To view the saved report:
`pnpm --dir web exec playwright show-report ui-artifacts/report`.
The Vite target also checks browser-requested assets: its missing `/favicon.ico` currently reports
a console 404. The live service suite is the acceptance run; console errors are not filtered out.

## Service lifecycle

Ordinary development and code agents must not start, stop, mask, unmask, or restart the service.
Altitude activates merged backend and web changes itself. The regular thirty-second tick discovers
merges even while their workers run. A self-deploy fast-forward marks activation
pending for loaded backend paths (`altitude/`, `bin/`, `systemd/`) or tracked web build inputs
(`web/src/`, `web/design/tokens.css`, `web/index.html`, `web/package.json`, `web/pnpm-lock.yaml`,
`web/tsconfig.json`, `web/vite.config.ts`). Web docs and other non-build files do not trigger it.
Dispatch continues while activation is pending. Once no dispatch or resume claim, L3 turn, or report
verification is in flight, `altd` runs the guarded restart script below as a transient user unit.
A restart that has not happened ten
minutes after it was requested is a system fault for L3 and the hold lifts.

To restart sooner by hand, press Restart on the web app's restart banner (the button shows only
at that narrow quiet point, including while workers run, and goes once restart is under way) or run `make restart` from the
deployed primary checkout. The
command refuses another clone/worktree, a non-exact or dirty `main`, and an in-flight dispatch,
L3 turn, or report verification. Both engines run L2 workers in independent transient user units,
so workers survive and are adopted after restart. Dispatch and
L3 turns wait only from the restart request until the replacement daemon is ready.
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
becomes text through **Stop** (Ctrl/⌘+M), appending to the draft for editing, or the send arrow (Enter),
appending and sending at once (queued while L3 is busy). **Cancel** (Esc) discards the recording.
An empty transcript or transcription failure sends nothing and preserves the draft.

For a manual Safari check, open each of a project conversation, a task conversation, and a project
task's **Message L2** panel; record and stop; confirm the transcript is appended to the existing
draft and nothing else appears; record again and use the arrow to transcribe and send or queue at once;
then cancel a recording and deny microphone access once and confirm the
typed draft remains usable. If Safari reports that voice needs HTTPS, use the secure URL above and verify the local CA is
enabled under Certificate Trust Settings.
