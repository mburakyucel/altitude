# Altitude architecture

Altitude turns a project-level conversation into isolated, reviewable work. It has a small
coordination layer, one owner per task, and mechanical safety rails: model judgment chooses how much
decomposition a request needs; code enforces task ownership, isolation, launch holds, and the PR
boundary.

```text
Burak
  ├─ project direction and roadmap ───────────────► L3
  │                                                  │
  │                                                  └─ coordinates one task
  └─ task questions and steering ◄───────────────► L2 owner
                                                     ├─ may implement directly
                                                     └─ may delegate to its engine's own subagents

L2 worktree/branch ─► checks/review ─► PR ─► merge ─► archive task
system fault ─► blocked task + private incident ─► one queued L3 message
L2 block ─► one queued L3 message ─► L3 answers (task resumes) or escalates (a Needs you card for the operator)
```

## Responsibilities

L3 maintains the project-level conversation, sees the active task set, and decides whether a
request can be answered directly or needs an L2. It does not run a mandatory planning pipeline and
cannot launch subagents directly. Its routine inspection goes through compact `alt` verbs for task
reports, recent messages and events, queue waits, repository/service state, PRs, and its own recent
shell commands. L3 turns persist bounded shell command text with their tool evidence, so repeated
ad-hoc commands are visible and can become stable verbs. Its process is read-only on the deployment
checkout on either engine; source changes always belong to one L2 worktree and PR.

L2 receives the request, repository context, lease, worktree, branch, and merge policy, and chooses
the lightest useful execution shape. Its conversation with Burak is stored apart from tool logs, so
Burak messages it directly without routing through L3. Messages queue on the task and reach the
worker at its next checkpoint; an explicit Stop aborts a worker. Appending a message to a blocked
task also persists a due `resume_after` request. An L3 CLI process stops there: altd coalesces that
request with timer and capacity-available wakes, then owns Git provenance validation and provider relaunch. A
durable resume claim fences competing wakes, holds service restart, and records the exact inbox batch and
replacement worker so a restarted daemon adopts rather than launches it again.
Explicit `alt task resume`, `stop`, and `reject` calls also stop in the CLI after persisting one
`daemon-request` event with the task, actor, operation, and required reason. Altd checks the recorded
state and worker/session identity, refuses a stale target, and treats a retry of the same completed
request as idempotent while the terminal receipt still matches; an intervening lifecycle gets a new
identity-fenced request before altd relaunches, stops, or removes a worker.
The attempt number fences every L2 command to the current attempt: an L2 may reply, block, complete,
and land only its own task.

Helpers are engine-native. The L2 may delegate bounded slices to its engine's own subagents
(Claude Code's Agent tool, Codex's equivalent); Altitude does not track them, and ownership never
transfers. Helper customization lives in engine-native files (agent definitions, skills, hooks). The
L2 persona carries brief delegation and context-hygiene guidance and asks for a small `progress.md`
(goal, done, next, how to verify) refreshed at milestones, never kept as a log.

L2 and L3 can run on Claude Code or Codex. Fresh L2 dispatch records one provider choice and keeps
that provider for the attempt. L3 keeps a separate resumable conversation on each provider. See
[Session lifecycle](SESSION_LIFECYCLE.md) for identity, routing, messages, containment, and context.

Tasks and L3 session records carry `engine_model` and `engine_reasoning_effort`. The engine adapter
reads Codex's rollout `turn_context` after the thread starts, recording the actual model and effort
for the current turn. altd reads the provider home; the sandboxed worker does not. Missing or delayed
rollouts leave the observation unknown and are retried while the turn runs. The launch model pin is
kept separately, so recording a default does not turn it into an override on resume. Status, task
header chips, and the Monitor API expose the observation; old records remain readable.
`GET /api/task/<project>/<slug>` returns `engine_model` and `engine_reasoning_effort`;
`GET /api/monitor` session rows expose `model` beside `engine`, with `engine_reasoning_effort`
when available. An unknown Monitor model is an absent key rather than null.

Both roles read two layers of rules. The personas in `personas/` are the global layer: how anyone
works under Altitude on any project, carrying nothing project-specific. The repository's own
instructions file — `CLAUDE.md`, or `AGENTS.md` where an engine reads that instead — is the project
layer, owned by the operator of that repository and read first. Altitude's own `CLAUDE.md` is simply
the project file of the project being built.

Everything that encodes the operator, their providers, or their hardware sits behind a named seam.
The operator seam is one configured name and role, so personas, docs, and UI text say "the operator"
or read the configured name. The engine seam is `engines.py`, `route.py`, and `config.py`: engine-specific
code lives there and nowhere else, Altitude runs with any single engine alone, and adding or removing an
engine touches only those three modules. The capability seam is the local services — the speech socket,
`ffmpeg`, a GPU — each optional, detected, and degrading to an explicit unavailable state.
`tests/test_project_layers.py` holds the per-file counts of provider and operator names outside the
seams as a ratchet that can only fall.

## Task lifecycle

```text
queued   -> running | rejected
running  -> reported | blocked | rejected | done
blocked  -> running | reported | rejected
reported -> done | running | blocked | rejected
```

A no-code research or proposal task can go directly from `running` to `done/archive`; a git check
refuses that shortcut when the task branch changed. Code work uses the verified report path.

Queued tasks wait for WIP and engine availability gates. The default caps are 8 running tasks per
project and 10 across the machine. Overlapping declared paths are information in task status and
briefs; they do not hold dispatch or resume. One provider's quota does not
globally freeze the other. Blocked is a persisted wait/intervention state: an L2 question, a timed
operational hold, a worker failure, a verifier fault, or a report gap. An L2's question goes to L3
first, which answers from the record or escalates one plain dilemma to the operator; only a block flagged
for the operator or an escalation is a Needs you card. After a restart L3 receives the active tasks and resumes
the ones a fault had stopped. Deferral is not an active
state: durable future work belongs in a GitHub issue, and the task exits the active set.

Project registration stores `wip` only when explicitly supplied; the gate reads that override or
`config.WIP_PER_PROJECT`. On the first registry load, a one-time migration removes stored caps equal
to the legacy default of 3 and logs the affected projects, preserving approval and engine pins.
`alt project set <name> --wip N --reason "…"` and `--unset-wip --reason "…"` are available to
the operator and that project's L3; add and remove remain operator-only. Caps range from 1 to
`WIP_PER_MACHINE`. Altd applies the durable `wip-request.json` before task dispatch on its next tick,
regardless of task capacity, and records one `project-set` event with project, actor, reason, request
id and outcome in the project's `events.jsonl`. Identical pending requests and completed retries
whose WIP receipt still matches reuse the request and event. CLI and HTTP registration, removal,
engine pins and WIP changes serialize registry writes under the project and registry locks.
Re-registering a project is the operator's deliberate act, and the last registry write wins.

`STATE.md` is regenerated from active task records and contains only work relevant to the next L3
turn. Archived tasks and incident history remain available as audit evidence without being loaded
into L3 context.

## Isolation and landing

Each task uses the isolated worktree path `.claude/worktrees/<slug>` and branch `worktree-<slug>`,
based on the exact fetched `origin/main`. Commits require the task provenance trailer. Protected
branches cannot be updated outside the guarded landing path. The trusted landing code validates the
staging lease and repository, fetches the base, commits, pushes, opens the PR, pins the current
base/head pair, waits for configured checks, and merges only
when requested and allowed. A task may carry an explicit merge hold for Burak review.
The lease limits which changes can be staged. Parallel tasks may edit shared paths; their briefs
name those paths and ask owners to rebase onto main before landing and keep shared-doc edits to
their own sections. If main moves, the owner runs `git rebase origin/main` in the task worktree;
an unresolved conflict is an ordinary `alt task block` to L3, never a system fault. Landing does
not resolve conflicts automatically.

A project that deploys from its own checkout keeps that checkout at `origin/main`. Dispatch and
daemon-side resume fast-forward it before the provenance gate reads it, so a PR another task merged
while it was still running no longer refuses every launch in the window until that task's report
lands. A sandboxed coordinator never performs this fetch on behalf of a message. The move is the
same guarded fast-forward that runs after a task lands, and it happens only when the checkout is
clean, on main, and strictly behind: a dirty, diverged, ahead, or off-main checkout still refuses,
unchanged and untouched.

Every worker is an untrusted process in its worktree, whichever engine runs it. Its only door into
Altitude is the `alt` CLI; the backend validates each command against the task record under the
project lock. Claude Code runs as a background job with Altitude's hooks for inbox delivery and
telemetry. Codex keeps its native workspace-write sandbox as containment and uses the same door;
Altitude reads its thread and usage from the worker's stdout JSONL. A turn that ends without a
report, a block, or a completion blocks the task as ended without a report, on either engine. A
Codex L3 uses a dedicated permission profile: only its fresh per-turn runtime directory is writable; the
deployment checkout and Altitude home are read-only, direct command networking and the user-service bus are denied,
and only that project's role-fenced altd Unix socket is reachable. A Claude L3 turn uses an equivalent runtime cwd,
`dontAsk` with unattended prompts denied, restricted settings, only Read/Grep/Glob/Bash, no editing
tools, and exact read/`alt` command rules. Runtime shims send every `alt` invocation plus authenticated GitHub
and service-status reads through the project-bound Unix socket; altd supplies the project independently of the request,
re-applies the L3 command door, accepts only flat task identifiers and stdin, and exposes no write-shaped
GitHub or service operation. Read-only Git and journal shims resolve against the deployment checkout. Claude's native Bash sandbox
is not enabled because this deployment host cannot create its required unprivileged bwrap namespace;
the permission boundary fails closed instead, while Codex retains its native filesystem sandbox.

## Faults

A system fault is project-scoped and two-tier. Tier one is code: a temporary capacity stop is retried
with backoff; a usage-window stop starts a fresh attempt on the other engine from the task's
`progress.md`, or parks a task pinned to one engine until its window reopens; each writes one task
event. A Claude usage-window stop is recorded once for the machine, because the subscription is
machine-wide; Codex reports its limits per turn. Tier two is L3: whatever remains blocks only its own
task, files private incident evidence (one incident per fault kind per day), and leaves one message
in the project's L3 queue; a repeat of that kind blocking another task adds one line for L3, not a
new incident. An L2 that meets an environment fault (a sandbox, host, or tool refusing
what the brief requires) reports it with `alt task block --fault` and takes the same path, so the
cause reaches L3 instead of sitting on Needs you as a question for the operator. The server delivers that message as a turn when L3 is free and an engine
is available; L3 records the learning on the incident and fixes the cause directly or creates one
ordinary task. An incident raised by that repair task (`--source recovery`) goes to Needs you instead
of waking L3 again. A task blocked before any launch goes back to the queue when it is resumed.
Incident records are evidence only and never create tasks, personas, or follow-up work.

A merged Altitude change marks activation pending when the self-deploy fast-forward brings in loaded
backend paths (`altitude/`, `bin/`, `systemd/`) or tracked inputs to the served web bundle
(`web/src/`, `web/design/tokens.css`, `web/index.html`, `web/package.json`, `web/pnpm-lock.yaml`,
`web/tsconfig.json`, `web/vite.config.ts`), whether the fast-forward runs after a task lands or at the
next dispatch. Web docs, design boards, the unused npm lockfile, and other non-build files do not
trigger activation. Hooks, personas, and templates are read per use and deploy with the pull itself.

The web app's restart banner sits above the header on every route while activation is pending: it
says in words whether the backend, the web app, or both changed, how many files landed and when, and
that Altitude restarts at the next quiet moment; while something runs it names what it waits for. New
dispatches hold, and once no L2 is running, no report is waiting and no L3 turn is in flight, altd runs
the one guarded restart script as a transient user unit outside its own cgroup. It installs the
pnpm-locked dependencies, builds and validates the latest bundle in staging, rechecks the checkout and
quiet point, swaps the bundle, restarts safely, and verifies both API and UI; verification failure
restores the prior bundle. The banner's Restart button runs the same path sooner by hand: it appears
only while nothing is running, disappears once the restart is under way (the banner then says so), and
the banner leaves when the new process answers with nothing pending. A restart that has not happened
ten minutes after it was requested is a system fault for L3, and the hold lifts. Ordinary source changes never
start, stop, mask, unmask, or restart the service; a lifecycle action by hand needs separate
authorization and post-change health verification.

## Interfaces and storage

The web build uses pnpm's frozen lockfile and emits `web/dist/` through `make web`. `make test`
runs Python unit and integration tests with throwaway state; `pnpm --dir web test` runs the web
unit suite. `make ui` runs the separate Playwright `web/e2e/*.pw.ts` suite against a running
service (`UI_BASE_URL`, default `https://10.88.0.1:8890`), including a Vite server with its API proxy
or an independently populated throwaway altd. The smoke spec reads the route tree in
`web/src/routes.tsx`, resolves dynamic parameters from real managed-project and task records, and
checks rendered content, console/uncaught errors, API failures and viewport horizontal overflow.
The same specs run at 390×844 with mobile user agent and touch and at 1440×900. Component specs use
`walkthrough.ts` for actions, visible text/role assertions for appearances and removals, and named
screenshots; `project-menu.pw.ts` demonstrates confirmation and cancellation without mutating state.
Screenshots, traces and the HTML report live in ignored `web/ui-artifacts/`. Bundled Chromium runs
headlessly with a temporary profile and its browser sandbox disabled inside the worker sandbox.
`pnpm ui` sets `PLAYWRIGHT_BROWSERS_PATH` to the Altitude home's shared `browsers/` directory unless
overridden; one install serves every worktree using that browser version. Installed Chrome is the
fallback only when the bundle is absent (its host profile denies networking in incident
I-20260907-041446). README documents setup,
target overrides and the human's headed mode. The UI rule stays in the project instructions file,
which both worker personas direct the task owner to read first.

The Python server owns state transitions and JSON APIs. The React app is one shell around four
pages, specified in `design/wireframes/SPEC.md`: Needs you at `/` (every decision across projects as
compact cards grouped by project, answered through `POST /api/decide`), the project page at
`/projects/<name>` (the §3.2 header with its status line and overflow menu, the L3 conversation, and
the work panel), the task page, and Monitor. `/projects` and `/chat/<name>` redirect to the project
page, and with no managed project every project route shows First run, which lists the folders under
the configured roots and starts L3 for one through `POST /api/project/add`, staying up until L3's
first reply or the error row that stands in for it. At 1024px and wider the rail is 260px and the work
panel is 340px, inline at 1280px and wider and an overlay from the header's panel button below that;
narrower is the phone: a 54px header and an 84px tab bar (Chat, Work, Needs you, Monitor), where
the header names the selected project and opens the switcher sheet, and a task page pushes over its
tab with a back control. Those widths are named once, in `web/src/shell/breakpoints.ts`. The
selected project is browser state under `localStorage`, set by the rail, the switcher, a project
route, or a Needs you card; the theme (light by default, dark on request) persists the same way. The
rail's engine readout renders `GET /api/overview` `engines[]`, one row per configured engine with the
display name the engine seam gives, so the web code names no provider; the same read carries the
scan roots First run names and the operator's configured name. `POST /api/l3/start` runs the start
turn for a managed project whose L3 never ran, from the header's Start L3. Chat is the only way to
create a task from the web: the L3 turn creates it through `alt task new`. The composer's engine choice pins the
project's L3 to Claude or Codex until set back to Auto; on Auto the weekly quota decides, and a turn
stays on the previous engine unless the other has clearly more headroom. A chat turn belongs to L3,
not to the page that started it: when the page leaves mid-stream, the turn finishes and its answer
lands in the history. `GET /api/chat` reports the server-owned active turn as a stable id, start time,
and trigger without copying its prompt. Chat renders that record as one thinking bubble on a fresh
mount or reconnect; a tab already showing the same live streamed response suppresses the extra
bubble. It never infers a turn indefinitely from `busy` or the last history role. The stream's
completion and terminal history rows carry the turn id, so local output stays until history owns it
and a completed assistant or error row wins over a raced active snapshot.

A message sent while L3 is busy is queued, never refused: the composer stays open, the Send button
reads Queue, and the message shows in the transcript as queued until its turn starts, when the queue
row becomes the active thinking bubble. The API snapshots the queue and active record under the same
lifecycle guard, so that handoff cannot appear as an idle gap. A control takes Burak's chat back off the queue only while it
waits. Server-triggered work is also visible in its FIFO position but is not editable. The queue is a
file in the project directory, so a reload, another device and a restart all see the same pending
messages. Each turn drains it at its own boundary rather than at the next tick: consecutive chat
messages fold into one turn in arrival order, each on its own line, while server-triggered messages
keep their own turn, and nothing runs while a turn holds the project's L3 lock.

Chat, the task Conversation tab, and the project task card's quick-message panel use one controlled
voice-capable composer. The routes retain ownership of their draft and normal submit function; the
shared composer owns microphone permission, MediaRecorder state, a 595-second client stop below the
server's 600-second decoded-audio limit, transcription,
transcript review, cancellation, and focus restoration. A transcript stays separate until Burak
chooses **Edit / insert** or explicitly sends it. Existing draft text is the prefix, separated from
dictated text by one space when it does not already end in whitespace. Decision and reason fields
remain ordinary form fields.

`POST /api/transcribe` is a bounded adapter to the existing local speech service. It accepts the
browser's declared audio media type (AAC/mp4 on Safari; opus/webm and the other listed containers),
limits the upload to 16 MiB, and asks `ffmpeg` for at most 601 seconds of 16 kHz mono PCM so a decoded
clip over the 600-second product limit is rejected without unbounded output. Conversion lives in a
unique temporary directory. The adapter sends the WAV path through `/tmp/whisper-server.sock`,
falling back to the existing `127.0.0.1:8890` Whisper bridge, then removes the entire directory on
success or failure. It neither persists raw audio nor owns or starts a speech model.

Unreadable media, timeouts, and an unavailable Whisper service become concise client errors while
converter paths and diagnostics stay in the private server log. The composer announces recording
and transcribing, restores the editable field after cancel or error, and leaves the microphone as
progressive enhancement. Altitude's WireGuard origin is HTTPS on `10.88.0.1:8890`; the service reuses
the local-CA certificate whose SAN contains that address, which makes `getUserMedia` available to
Safari after the CA is trusted on the phone.

The same server serves each project's wireframe boards. `GET /design/<project>` redirects to
`/design/<project>/design/wireframes/index.html`, read from that project's own deployment checkout on
every request and sent uncached, so a merged board change needs no build step and no restart to be
visible. Only the `design/wireframes/` and `web/design/` subtrees are readable and only the
extensions a board needs; the resolved path must stay inside those subtrees, and a project without
`design/wireframes/index.html`, a directory, and anything outside the rule are one plain 404. The
tree is mirrored under the prefix because a board's stylesheet imports the build's design tokens two
levels up. `/api/project` reports that URL only when the boards exist, and the project header's
overflow menu turns it into Design boards, opening in a new tab. Any project with boards gets one; the
route knows nothing about this repository's own.

The Monitor page reads `/api/monitor` and is display only: no hold, incident, route or follow-up
work is derived from it. It sits in the shell's page container and shows one seat card per configured
engine, in the seam's order and under the display name the overview's `engines[]` rows give, so the
page spells no provider: Claude's five-hour and seven-day windows from the statusline snapshot, and
the Codex seat's windows named by the length the provider reports, a window the provider does not
report shown as absent rather than zero — each with percent used, a meter with the 70% reserve line
drawn, when it resets in relative and clock terms, the plan where the provider names it, and how old
the reading is. A seat with no reading at all says so and carries the one line that fixes it; a
reading older than the age the router itself trusts is stale: still shown, dimmed, and labelled. One
routing card answers which engine each project's L3 (its pin, or Auto) and a fresh L2 would get for a
turn started now, in `pick_engine`'s own words, including the case where no engine is available. The
sessions the monitor knows follow, each with its task, its engine and the model when the API reports
one, its context meter and the age of its snapshot; no session is one muted sentence. Loading is a
skeleton in the page's shape, and a failed read is one sentence with Retry.

A task has two views. Its header keeps the task's own state (slug, attempt, worktree, branch) and
its worker's (engine, model, session, context percent with its state and observation age, turns,
reported subagent launches) in two labelled groups, so lifecycle and session facts are never read as
one thing; a live worker whose snapshot ages out is labelled stale by the same rule the Monitor page
uses. The Conversation
tab is the human-readable Burak/L2 exchange; the Live session tab reads the worker's own session log
(Claude's session JSONL, or every turn of the Codex thread) together with Altitude's task events as a
Claude Code window: prompts, replies, and each tool call as one row with its output folded under it,
task boundaries as thin separators, hidden reasoning never shown, and Raw mode for the complete redacted
records. Operational events remain an audit detail.

Runtime files live under `ALTITUDE_HOME`; a task is a directory a person can read. Source-controlled
personas, schemas, templates, and hooks describe current behaviour: `hooks/` holds the Git hooks
that `git_policy` installs into every managed repository, the Claude inbox hook, and the statusline
monitor. [SIMPLIFICATION.md](SIMPLIFICATION.md) records why the system has this shape.
