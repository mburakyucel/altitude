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
L2 block ─► one queued L3 message ─► L3 answers (task resumes) or escalates (Inbox card for Burak)
```

## Responsibilities

L3 maintains the project-level conversation, sees the active task set, and decides whether a
request can be answered directly or needs an L2. It does not run a mandatory planning pipeline and
cannot launch subagents directly. Its routine inspection goes through compact `alt` verbs for task
reports, recent messages and events, queue waits, repository/service state, PRs, and its own recent
shell commands. L3 turns persist bounded shell command text with their tool evidence, so repeated
ad-hoc commands are visible and can become stable verbs. When code changes are needed, one L2 owns them.

L2 receives the request, repository context, lease, worktree, branch, and merge policy, and chooses
the lightest useful execution shape. Its conversation with Burak is stored apart from tool logs, so
Burak messages it directly without routing through L3. Messages queue on the task and reach the
worker at its next checkpoint; an explicit Stop aborts a worker. Appending a message to a blocked
task also persists a due `resume_after` request. An L3 CLI process stops there: altd coalesces that
request with timer and lease-release wakes, then owns Git provenance validation and provider relaunch. A
durable resume claim fences competing wakes, holds service restart, and records the exact inbox batch and
replacement worker so a restarted daemon adopts rather than launches it again.
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

## Task lifecycle

```text
queued   -> running | rejected
running  -> reported | blocked | rejected | done
blocked  -> running | reported | rejected
reported -> done | running | blocked | rejected
```

A no-code research or proposal task can go directly from `running` to `done/archive`; a git check
refuses that shortcut when the task branch changed. Code work uses the verified report path.

Queued tasks wait for WIP, lease, and engine availability gates. One provider's quota does not
globally freeze the other. Blocked is a persisted wait/intervention state: an L2 question, a timed
operational hold, a worker failure, a verifier fault, or a report gap. An L2's question goes to L3
first, which answers from the record or escalates one plain dilemma to Burak; only a block flagged
for Burak or an escalation is an Inbox card. After a restart L3 receives the active tasks and resumes
the ones a fault had stopped. Deferral is not an active
state: durable future work belongs in a GitHub issue, and the task exits the active set.

`STATE.md` is regenerated from active task records and contains only work relevant to the next L3
turn. Archived tasks and incident history remain available as audit evidence without being loaded
into L3 context.

## Isolation and landing

Each task uses the isolated worktree path `.claude/worktrees/<slug>` and branch `worktree-<slug>`,
based on the exact fetched `origin/main`. Commits require the task provenance trailer. Protected
branches cannot be updated outside the guarded landing path. The trusted landing code validates the
lease and repository, commits, pushes, opens the PR, waits for configured checks, and merges only
when requested and allowed. A task may carry an explicit merge hold for Burak review.

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
Codex L3 turn is contained the same way from a disposable runtime directory under the project's
Altitude folder.

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
cause reaches L3 instead of sitting in the Inbox as a question for Burak. The server delivers that message as a turn when L3 is free and an engine
is available; L3 records the learning on the incident and fixes the cause directly or creates one
ordinary task. An incident raised by that repair task (`--source recovery`) goes to the Inbox instead
of waking L3 again. A task blocked before any launch goes back to the queue when it is resumed.
Incident records are evidence only and never create tasks, personas, or follow-up work.

A merged Altitude change marks activation pending when the self-deploy fast-forward brings in loaded
backend paths (`altitude/`, `bin/`, `systemd/`) or tracked inputs to the served web bundle
(`web/src/`, `web/design/tokens.css`, `web/index.html`, `web/package.json`, `web/pnpm-lock.yaml`,
`web/tsconfig.json`, `web/vite.config.ts`), whether the fast-forward runs after a task lands or at the
next dispatch. Web docs, design boards, the unused npm lockfile, and other non-build files do not
trigger activation. Hooks, personas, and templates are read per use and deploy with the pull itself.

The web app's banner names backend changes, web changes, or both. New dispatches hold, and once no L2
is running, no report is waiting and no L3 turn is in flight, altd runs the one guarded restart script
as a transient user unit outside its own cgroup. It installs the pnpm-locked dependencies, builds and
validates the latest bundle in staging, rechecks the checkout and quiet point, swaps the bundle,
restarts safely, and verifies both API and UI; verification failure restores the prior bundle. The
banner's Restart button runs the same path sooner by hand. A restart that has not happened ten minutes
after it was requested is a system fault for L3, and the hold lifts. Ordinary source changes never
start, stop, mask, unmask, or restart the service; a lifecycle action by hand needs separate
authorization and post-change health verification.

## Interfaces and storage

The Python server owns state transitions and JSON APIs. The React app provides Inbox, Projects,
Chat, and Monitor navigation plus project/task detail routes. Chat is the only way to create a task
from the web: the L3 turn creates it through `alt task new`. The composer's engine choice pins the
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
shared composer owns microphone permission, MediaRecorder state, a 115-second client stop below the
server's 120-second decoded-audio limit, transcription,
transcript review, cancellation, and focus restoration. A transcript stays separate until Burak
chooses **Edit / insert** or explicitly sends it. Existing draft text is the prefix, separated from
dictated text by one space when it does not already end in whitespace. Decision and reason fields
remain ordinary form fields.

`POST /api/transcribe` is a bounded adapter to the existing local speech service. It accepts the
browser's declared audio media type (AAC/mp4 on Safari; opus/webm and the other listed containers),
limits the upload to 12 MiB, and asks `ffmpeg` for at most 121 seconds of 16 kHz mono PCM so a decoded
clip over the 120-second product limit is rejected without unbounded output. Conversion lives in a
unique temporary directory. The adapter sends the WAV path through `/tmp/whisper-server.sock`,
falling back to the existing `127.0.0.1:8890` Whisper bridge, then removes the entire directory on
success or failure. It neither persists raw audio nor owns or starts a speech model.

Unreadable media, timeouts, and an unavailable Whisper service become concise client errors while
converter paths and diagnostics stay in the private server log. The composer announces recording
and transcribing, restores the editable field after cancel or error, and leaves the microphone as
progressive enhancement. Altitude's WireGuard origin is HTTPS on `10.88.0.1:8890`; the service reuses
the local-CA certificate whose SAN contains that address, which makes `getUserMedia` available to
Safari after the CA is trusted on the phone.

The Monitor page reads `/api/monitor` and is display only: no hold, incident, route or follow-up
work is derived from it. It shows both seats side by side — Claude's five-hour and seven-day
windows from the statusline snapshot, and the Codex seat's windows named by the length the provider
reports, a window the provider does not report shown as absent rather than zero — each with percent
used, when it resets in relative and clock terms, the plan where the provider names it, and how old
the reading is. A figure with no reading at all is unknown and carries the one line that fixes it; a
reading older than the age the router itself trusts is stale: still shown, dimmed, and labelled. One
routing card answers which engine each project's L3 (its pin, or Auto) and a fresh L2 would get for
a turn started now, in `pick_engine`'s own words, including the case where no engine is available.

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
