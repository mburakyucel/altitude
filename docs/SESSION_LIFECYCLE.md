# Engine and session lifecycle

The persistent L3 conversation coordinates the project across task lifetimes. Each L2 owns one
task, with its own durable conversation, isolated worktree and PR delivery. Questions go to L3
first unless explicitly flagged for the operator; reports needing judgment return to L3.
Mechanically clean deliveries can close automatically after verification without an L3 turn.

An [increment's brief](CLI.md#incremental-issue-delivery) identifies its acceptance, parent issue and
outstanding scope. Completing that increment completes the task; L2 supplies PR/acceptance evidence and
remaining work through replies and report follow-ups for L3 to record in the issue. Later tasks stay
within recorded authorization, and independent increments may run concurrently when dependencies permit.
Task archival and intake's issue snapshot imply no issue closure. Partial deliveries keep the parent
open; closure requires cumulative full-scope delivery and required operator acceptance through the
[reviewed closing relationship or L3 reconciliation](CLI.md#delivery-linked-issue-completion).
Merge holds remain in force. No periodic issue cleanup runs.
The operator can also steer an L2 directly while other tasks continue.

Altitude has one logical owner per task and replaceable physical workers. These are different
identities on purpose:

| Field | Meaning | Changes when |
| --- | --- | --- |
| `attempt` | one L2 attempt, counted from 1 | the task is dispatched again from the queue |
| `l2_engine` | provider for that attempt | only on a fresh attempt, never a transparent resume |
| `session_id` | provider conversation/thread | Codex keeps it across turns; Claude may return a replacement on resume |
| `agent_id` | current unit-owned CLI worker | every physical replacement |
| `routing` | one sentence saying why this engine was chosen | written once with fresh dispatch |
| `routing_pinned` | whether this attempt launched with an explicit task, turn or project pin | fresh dispatch; preserves strictness during quota/rejection recovery |
| `launch_model` | model override passed at launch, or null for the CLI default | fresh dispatch |
| `effort` | explicit task creation choice, or null for the engine's task default | task creation |
| `launch_effort` | resolved effort override passed to the worker, or null for native configuration | fresh dispatch; reused on resume |
| `engine_model` | model observed from the provider's turn | each worker/turn records its selection |
| `engine_reasoning_effort` | observed effort when supplied by the provider | with the model observation |

New tasks default to High on supporting engines; `--effort high|xhigh` chooses explicitly and
overrides native effort configuration. Explicit choices restrict Auto to supporting engines;
unsupported pins refuse. Engines without task effort support keep native behavior when the option
is omitted. The provider validates the selected model's compatibility at launch; an unsupported
effort response fails the task's launch or resumed turn without a silent downgrade or engine switch.
Messages and resumes keep the saved launch effort, attempt and owner conversation even if routing
or native configuration changes. Tasks created before this field exists retain native behavior,
including on resume; observations never become requested settings. A resumed turn's effort
observation starts unknown until its worker reports one. See [CLI precedence and evidence](CLI.md#task-reasoning-effort).

For first-run configuration, Auto preferences and explicit pins, see [setup](SETUP.md). The
[engine integration boundary](ARCHITECTURE.md#engine-integration-boundary) separates the supported
launchers from the broader extensibility direction; this page describes their current lifecycle.
[Operations](OPERATIONS.md) covers service activation, inspection and mobile voice checks.

## Repository instructions

Both roles explicitly read project rules before proceeding. Each fresh or resumed L2 worker receives
the absolute rule-file path in its worktree; every L3 turn receives the path in the registered checkout,
including start/restart and other server turns. L3's disposable cwd stays outside the repository, so
native checkout discovery is insufficient. The common engine boundary chooses root `AGENTS.md` when
present, otherwise `CLAUDE.md`, and directs the role to follow references/imports and applicable
directory instructions. It resolves the file on each turn so a resumed session sees a changed source.

Altitude maintains its own project rules only in [AGENTS.md](../AGENTS.md); root `CLAUDE.md` is the
native `@AGENTS.md` import. Managed repositories keep their own rules and need no file migration.
The global L2/L3 personas describe roles and retain explicit reading instructions without embedding
Altitude-specific rules. No session rotation or extra engine policy is needed for this reference.

The [L2](../personas/l2.md) and [L3](../personas/l3.md) personas own role judgment and essential
operating guidance; the [CLI reference](CLI.md) owns detailed command contracts. Global personas
do not depend on a managed project containing Altitude's reference docs. Project rules are
referenced each turn; personas come from the activated committed installation export, supplied
per invocation or at fresh session creation through the existing engine loading path.
A native resume can retain an earlier persona, so a merged persona edit alone does not establish that
an existing session has loaded it. The per-invocation adapter receives the current persona file on
fresh and resumed turns; the fresh-thread adapter embeds the current contents when starting a new
task or coordinator session, including context rotation, and retains them on native resume.
`tests/test_persona_loading.py` exercises real dispatch, owner resume, coordinator turns and rotation
with captured provider commands and prompts. It verifies which authoritative source is supplied,
not live-provider consumption or future model compliance. One-off steering remains in the task conversation.

Incident-prevention guidance follows these same activation limits. A merged persona correction is
available after normal committed-source activation, then effective when the invocation/session
loads it; native resumes can retain old instructions. L3 reports queued, merged and effective
separately and supplies relevant sourced steering to an existing affected owner when needed.

Codex's [native discovery](https://learn.chatgpt.com/docs/agent-configuration/agents-md) follows the
repository root through cwd; Claude's [native import](https://code.claude.com/docs/en/memory#agentsmd)
resolves `@AGENTS.md` relative to `CLAUDE.md`. Fixture tests verify the reference and prompt paths;
they do not prove that a live provider loads or follows the rules.

Repository validation uses deterministic fixtures at the external engine boundary: the
[core journey tests](DEVELOPMENT.md#coverage-and-limits) retain real task state, routing,
message/resume logic, API/storage and Git operations. Live-provider cases, including the real
tiny task, are deferred by the operator's 2026-09-08 decision. Local review evidence keeps named
phone/desktop screenshots and failure traces with the self-contained HTML report and check log;
see [artifact review](DEVELOPMENT.md#ci-and-candidate-identity). Passing fixtures does not establish
live CLI/authentication compatibility or host confinement. [Release checkpoints](RELEASING.md)
identify validated source versions without changing the automatic activation lifecycle.

The L2 learns its attempt from `ALTITUDE_ATTEMPT`. Replies, completion, and landing name it, so
a worker of an earlier attempt cannot act for the current one. `ALTITUDE_SESSION_KEY` (`project--slug-attempt`)
keys the edit-count telemetry across worker replacements.

## Fresh dispatch

```text
queued task
  ├─ fetch origin/main and create the isolated worktree from its immutable SHA
  ├─ WIP and Git isolation gates
  ├─ highest available preference tier, then weekly headroom (or explicit task/project pin)
  ├─ persist l2_engine + model + routing reason
  ├─ create the provider session in the isolated task worktree
  └─ bind its concrete session and worker → running
```

WIP defaults to 8 running tasks per project and 80 across the machine. Shared planned files do not
hold dispatch or resume. The brief names overlaps, asks the owner to rebase onto main before
landing, and keeps shared-doc edits in that task's own sections. Status shows expected files and
informational overlaps. Owners can edit newly needed files within the authorized objective without
another permission or resume. L3 and the operator can update the advisory list with `alt task paths`.
The owner selects files or hunks with `git add` and reviews `git diff --cached`; `alt land` commits
that index and leaves unstaged and untracked work intact. Before publication, the owner reviews all
outgoing commits and the complete PR diff for scope and privacy, including intermediate content.

Deployment staged, working and untracked content remains untouched by dispatch and resume.
Task CLI code, personas, hooks, templates and schemas come from the activated committed installation
export outside worker writable roots; fresh project work starts from fetched `origin/main`.
Deployment and activation errors remain visible independently of isolated task progress.

L3 or the operator can request `alt task preserve-checkout <slug> --reason "…"` for an unlaunched
blocked task. Altd requires dirty main exactly at fetched `origin/main`, preserves staged,
unstaged and untracked changes on a local `archive/checkout-<request-id>` branch, and records
`checkout_archive` and a `checkout-preserved` event with its branch and immutable snapshot SHA
before cleanup. The snapshot's parent retains staged content, including versions absent from the
working files; applying the complete snapshot flattens staging intent. Ignored files remain untouched.
Archives stay local until explicit operator removal and are never automatically pushed or deleted.
Legacy `preserved_checkout` stash SHAs and their stashes remain readable and recoverable. The operation uses
the existing reason-bearing daemon request and identity fence and leaves the task blocked;
an interrupted request refuses replay so it cannot accidentally archive later edits. Cleanup failure
retains the snapshot and fault. The owner receives the branch and SHA through status or an L3 message,
inspects and applies it in the isolated worktree,
reviews the changes and delivers a PR. See [the recovery procedure](CLI.md#dirty-checkout-recovery).

A project stores a WIP cap only when explicitly configured. The first registry load removes legacy
stored caps of 3 once and logs the migration, preserving approval and engine pins; later explicit
caps, including 3, persist. L3 can use `alt project set <name> --wip N --reason "…"` or
`--unset-wip --reason "…"` for its own project, with N from 1 to the machine cap. The CLI persists
the request and altd applies it on the next tick before capacity gates, with no task slot or restart
needed. One project-level event records actor, reason and outcome; retries reuse the receipt and
event while the configured WIP still matches. The operator sets the aggregate cap with
`alt machine set --wip N --reason "…"` or resets it to 80 with
`alt machine set --unset-wip --reason "…"`. Any positive integer is supported for the machine,
including values above 80. The same daemon settings implementation stores machine overrides and
receipts persistently and drains them before project ticks. Machine changes and project add/remove
are operator-only; an L2 cannot change scheduling limits. `alt machine show` displays active/default
values, project overrides, and pending/completed receipts. See [concurrency commands](CLI.md#concurrency-limits).

Lowering a project or aggregate cap preserves running workers and explicit project settings.
Dispatch and due resumes wait until the running counts are below both effective limits. A project
cap can exceed a machine cap lowered later; the aggregate gate still applies. Resetting a project
restores 8 even if the machine cap is lower. Changes need no task slot or service restart.

For projects that deploy from their checkout, the daemon tick and delivery path separately advance
clean main and report deployment failures. Backend, launch-source or tracked web input changes mark
activation pending; ordinary dispatch and resume do not move the deployment checkout.

L3 and fresh L2 dispatch use the same project Auto preference tiers. Set them with
`alt project set <name> --routing 'codex,claude:fable>claude:opus' --reason "…"`; commas tie
options, and `>` starts a lower-priority tier. The operator and project's L3 can change or unset this
operational setting; altd applies it on the next tick and records the reason without a restart.
The default ties Codex's default model and Claude Fable, with Opus as a lower-tier fallback;
`ALTITUDE_PRIMARY_ENGINE` chooses only the default tie order.

Auto selects from the highest tier with an eligible option. Within that tier it compares only named
seven-day Claude data with a Codex window whose reported duration is exactly seven days. A five-hour
window is an availability signal, not the preference score. Unknown or incomparable weekly data uses
configured tie order. L3 keeps the preceding engine/model when it remains in the selected tier,
unless another option has at least fifteen percentage points more weekly headroom; a higher eligible
tier takes precedence over continuity. Monitor uses this same policy for its project L3 and fresh L2
explanations.

A missing executable or exhausted short/weekly window excludes only affected options. Unknown
authentication, model access or quota remains eligible and is described as unverified. Altitude does
not infer subscription entitlement from a plan name or separate Fable and Opus allowances from an
account-wide meter. An explicit unavailable-model rejection excludes that model for thirty minutes;
an authentication rejection excludes the engine for thirty minutes. Retrying configured alternatives
is bounded and requires confirmation that no response output or tool effects occurred. A rejection
after work starts cannot silently replay the turn. If no option is eligible, the task stays queued
with an explanation to install/authenticate an engine, wait for the quota reset or change preferences.

Task or project engine/model pins override Auto and never silently fall back. They remain distinct
from preferences even when Auto selects the same model. [CLI examples](CLI.md#automatic-routing-preferences)
include an Opus-only account, omitted Fable and different preference orders.

Codex CLI 0.153.4's JSON stream identifies the thread but carries no model. altd reads the matching
`$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*-<thread-id>.jsonl` (default home `~/.codex`) and takes
`model` and `effort` from the first `turn_context` timestamped at or after this worker's launch.
This distinguishes a resumed turn from earlier turns in the same rollout. The worker record caches
the observation; daemon polling copies it into the task and live monitor snapshot. L3 records it
while its synchronous turn is running and keeps it in that engine's session record. The sandboxed
worker does not read the rollout for telemetry. Unavailable or incompletely flushed metadata remains
unknown and is retried; older tasks render without the fields. The observed model is never used to
pin a later launch: only the separately recorded launch override controls that choice.
Task details expose `engine_model` and `engine_reasoning_effort`; Monitor session rows
expose `engine`, `model` (omitted when unknown), and `engine_reasoning_effort`.

## Messages, resume, and stop

### Image delivery and recovery

Project and task messages may contain managed `images` references with an opaque ID, display name,
validated format/size/dimensions, integrity digest and original operator message/task provenance.
Image selection stays local until Send. Admission holds the project lock while normalizing files
and saving one durable record: the project queue row or task `status.json.image_messages` entry.
Task message reads and pending delivery project that entry; its delivered flag changes atomically
with resume claims. A same-ID HTTP retry returns the original receipt after delivery or archive,
and changed content under that identity is refused. Confirmed refusals restore the draft; uncertain
responses keep its controls frozen and retry the same submission without creating another message.

Each image-bearing project message keeps its own turn and caption. Its queue claim remains on disk
until human history and a terminal response are durable. After interruption, recovery preserves the
original message and shows an explicit delivery error; it does not execute that turn twice. Removing
a waiting image message records cancellation so a late admission retry cannot resurrect it. Saved
failed-turn Retry selects the same committed IDs with a new message identity. Missing bytes remain
an error with the text readable. Existing task resume/block/recovery authority still governs L2 retries.
Task images share individual message removal and Stop/Continue controls. Removed messages stay
out of checkpoint and resume batches; an accepted image correction retains the observed Stop identity.
Refused images leave a reported owner's report and state intact; accepted follow-ups use the same
open-PR continuation and preserve its review hold.

The engine seam accepts neutral resolved image inputs on fresh/resumed L3 and L2 calls. Native
multimodal payloads retain the selected provider, model and session. Image capability is checked
before admission and again at delivery; image failure does not select a different provider. Live
checkpoints that accept only text receive explicit native visual-read instructions and readable
canonical locations. Resume batches over native image limits use those readers for every image,
preserving message boundaries. Project session handoffs retain bounded source references and retrieval
instructions. Deterministic fixtures prove payloads and readable bytes, not live model compatibility.
Fresh L2 attempts, including explicit provider handoff, receive previously delivered image-message
captions and their canonical images as historical context. Larger sets use native visual readers;
the original conversation and delivered flags remain intact. Ordinary same-session resume carries
only its claimed pending messages.

L3 passes selected same-project committed IDs through `alt task new/message --image <id>`.
The task assignment or L3-authored message retains original provenance; it grants neither operator
approval nor new file scope. Images remain in project-managed storage across task archive and
worktree deletion. Conversation/task references protect them indefinitely under current retention;
unreferenced files are collected after 24 hours on the existing maintenance cadence.

### Messages and questions

Inbox reads tolerate a concurrent resume consuming the queued file. A missing inbox carries no
delivery proof; message labels still require the recorded handoff evidence described below.

Saved and arriving L3/L2 prose shares project-aware GitHub reference rendering across conversations,
live sessions, decisions, and reports. The UI resolves `PR #250`, `issue #247`, and bare `#247`
against `GET /api/project/<name>` repository metadata; `owner/repo#247` uses the named repository.
Replies, briefs and summaries preserve upstream URLs or qualified references. Every L3 turn receives
this guidance, including a resumed conversation; both role personas carry it. Ambiguous historical
references retain their text rather than acquiring a guessed upstream identity.
This is a read-time presentation of durable text: no message rewrite, provider request, or
per-mention lookup occurs. Existing links and code remain intact, and missing repository metadata
leaves unqualified references as text. Switching projects supplies the destination's repository.

A message from the operator (task page, chat through L3, or `alt task message`) is appended to the task's durable
conversation and to its inbox. Each file is published atomically under the project writer lock;
concurrent reads see complete messages. Nothing is killed. The engine seam supplies the inbox at a native hook
checkpoint when supported, or resumes the saved session after a clean CLI turn finishes. A failed
worker retains the fault path even with pending steering. For a blocked task the same locked append
records a due `resume_after` request, except when Stop holds the inbox. The L3
CLI returns without fetching or writing the deployment checkout; altd sees the durable request on its next tick
and its keyed resume runner coalesces a simultaneous API wake, retry, or available WIP slot. Before provider launch it
persists a cross-process claim and moves that claim's exact message batch out of the hook-visible inbox.
Each ordinary operator message offers Remove while the inbox still owns it. Removal and pickup share
the project lock: only the selected ID leaves pending input, and later arrivals remain separate for
the next checkpoint. The existing delivery metadata records removal while original conversation text
remains evidence; the bubble becomes Message removed. Removed text cannot authorize a new decision.
Quick-choice receipts and messages already used by recorded decisions cannot be removed. Cancellation
does not undo a resume request, Stop, fault or question. Claimed messages say Sending to session and
cannot be removed. A failure before launch restores removal; an attempted but unconfirmed handoff
retains Delivery unconfirmed and cannot be removed even when recovery restores the inbox batch.
A successful stdin handoff, matching session initialization
and bound replacement record delivery for that exact batch. A correlated native hook attachment also
proves handoff; inbox absence or new assistant output does not. Missing evidence says Delivery
unconfirmed, without recommending a duplicate send. The conversation keeps each message, and a message appended after that snapshot remains
for the running worker's next checkpoint. An explicit question block supersedes earlier wake requests:
older inbox messages remain available, but cannot resume that wait. A later message or explicit Resume
authorizes another turn. An L2's question goes to L3 first: L3's `alt task message` requests that
daemon resume, or `alt task escalate` turns it into a Needs you card for the operator. `--for-burak`
places operator questions there immediately and also notifies L3. A block that publishes or revises questions
queues one notification with open members, revisions and required authority; re-parking unchanged members
stays quiet. Notification lets L3 coordinate scope or record-backed portions without approving
operator-required proposal, security or product decisions.
For a faulted task, L3 messages remain non-waking discussion and verified recovery uses the explicit resume.

A reported task with an open PR retains its owner conversation. The ordinary composer and coordinator
Resume path validate a currently open recorded PR, retain the previous report and verifier result in
task events, and invalidate that report's completion authority before entering the existing pending
resume flow. The attempt, provider session, worktree, branch, PRs, expected files, holds and original source
messages remain attached to the same task. Each distinct send keeps its own conversation/inbox row;
the resume claim consumes one exact batch and leaves later sends for the next checkpoint. A refused
or uncertain provider launch uses the existing claim recovery, without duplicating the conversation.

Follow-ups also win against worker-exit/report-handoff races. Verification is bound to owner identity,
worker start, block identity and the follow-up timestamp; stale verification, fault effects, archive
callbacks and report-turn receipts cannot conclude resumed work. Resumed work writes a fresh report.
The old report file remains readable until replacement, with its historical copy retained in events.
Archived/done/rejected tasks remain read-only; archived restoration is not part of this lifecycle.

On phone and desktop the reported composer uses the ordinary empty, draft, sending, listening,
transcribing, denied and error states. A receipt clears the submitted draft and pending bubble;
capacity waits retain the composer. Refused sends restore recoverable text, while an unconfirmed
response keeps the recovery hint and never resends automatically. Merge-held status persists after
send and reload. `reported-continuation.pw.ts` walks continuation and refusal; the shared composer
and task lifecycle walkthroughs cover voice and accepted/unconfirmed transport recovery.
A task's versioned dilemma remains open independently of that wake and its worker state. Blocks and
L3 escalations publish one question or up to three independent questions into the owning human conversation;
the model chooses plain questions, one recommended action, or up to three explicit quick choices.
the inbox handoff names its stable ID/revision and the IDs of the actual source messages.
The owner or coordinator writes the actual choice in plain language with the task's user-facing
purpose and material consequences clear before answering. Detailed reasoning and history stay
accessible in the owning conversation; concise presentation changes no revision or authority rule.

Global Needs you and the owning L2 chat show questions and quick answers. Project Work retains
each unfinished task once in **Current**, with compact question and execution status; its waiting
row opens the owning question. A wake or capacity wait alone changes no question.
Answers and withdrawals reduce the question count without removing the task; closing the last removes
its attention label while the row shows the observed running or waiting state. Only completion
or rejection moves it to recent **Done this week** history. The global attention badge counts
open operator questions and operational items, named separately in summaries; project navigation
keeps state dots without another numeric attention badge. Unknown and stale reads stay explicit.

Quick acceptance names the question ID, revision and chosen option. Grouped answers name the group
ID/revision and each selected question/revision/option; the server validates the whole selection
before saving one operator message and its resolutions. Only selected questions close, and delivery
uses the existing inbox/resume path. Retries reuse the saved receipt and return current group state;
stale revisions cannot approve a replacement. A recorded decision may wait for capacity without
requiring a second answer. Typed replies are ordinary messages: the same L2 answers or clarifies,
then uses the cited-message [`resolve` operation](CLI.md#conversational-decisions) for a clear decision.
A typed group reply retains the viewed member references; the owner can cite that one message to
resolve several independent questions. A follow-up alone never resolves the dilemma. A partial answer leaves the relevant remainder open;
a changed direction can make the old question unnecessary and close it with a recorded reason.
Targeted guidance follows [L3 coordination judgment](../personas/l3.md#authority-and-coordination).
The owner applies
[decision guidance](../personas/l2.md#conversation-and-decisions) through the existing resolver.
A withdrawn question appears behind a compact **Question withdrawn** disclosure, showing its
question and reason without answer controls. It records owner judgment without an operator answer;
independent open questions and closed history remain. Supersession instead records the authoritative
source making the question obsolete. Queued guidance reaches the owner's next checkpoint.
For an unnecessary escalation that L3 settles within existing delegated authority, the L2 records
`--l3-authority` with specific evidence and rationale against the exact L3 task message and question
revision. The receipt attributes the answer to L3 and the authority assessment to its owning L2/attempt;
source and revision checks do not replace the owner's substantive judgment. Partial resolution preserves
the original audience and independent worker, capacity and fault state. Genuine operator choices still
need original operator authority, and neither this resolution nor its receipt releases a merge hold.
Report handoff closes the prior dilemma without accepting its approach;
the report review can raise its own question.
Neither operational resume nor closing an obsolete question approves its abandoned recommendation.

A design question can carry a saved screenshot-and-text proposal. The current owner publishes its
explicit worktree selection with `alt task block --design-file` in the ordinary question operation;
publication neither launches a worker nor releases a merge hold. Selected content, including
ignored or untracked captures, is copied without Git changes before
the question is persisted. Its fixed image files stay with the task through archival, and the
captured text and manifest stay in the question. **View preview · vN** appears in Needs you, the
owning question and its offscreen-question navigation. Work opens the owning question from its row.
The preview's saved title distinguishes proposal from implementation review; older attachments
remain with their historical questions. It opens in another tab, preserving the original view and
draft; **Back to question** opens the exact question/revision. Viewing creates no
message or decision. A replacement capture advances the question revision even when the question
text is unchanged, so stale controls and earlier conversational answers cannot accept it. First
acceptance checks saved content integrity; unavailable or altered evidence leaves the decision open.
Interactive wireframes are shown as captured states, and submitted HTML never executes. See
[selection bounds and owner commands](CLI.md#task-design-previews).

The provider conversation, attempt, engine and model remain under the ordinary continuity rules.
When those rules queue a fresh attempt after a provider limit, an existing dilemma still accepts
replies and explicit acceptance into the normal inbox. The fresh brief carries its current context;
dispatch and delivery use the existing paths, and the UI says the message waits for the L2 to start.
An FYI (`alt fyi [slug] "text"`) is a system row in the project's chat, not a task-state change.
The writer records explicit L3 selection as `heads_up: true`; these concise lines stay visible
between routine system groups. Internal calls default to the daemon actor, and automatic, L2 and
ambiguous historical FYIs remain eligible for grouping. Selection guidance lives in the
[L3 persona](../personas/l3.md), with the instruction loading limits described above.
On start, altd queues one message per project listing its active tasks, unresolved faults, and a bounded
upstream-report summary with confirmed links and missing/failed/uncertain gaps. L3
uses that inventory without repeating unchanged blocker nudges. The originating L3 checks public
delivery evidence and local observations that the actual cause is gone, then requests the existing
reason-bearing resume. Notification receipt, issue closure and unrelated restart never establish
repair. Coordinator messages to faulted tasks carry the existing non-waking inbox marker and leave
the saved block in place; they are readable in the conversation and delivered at a later supported
resume. Operator discussion retains its ordinary wake behavior. Explicit resume can requeue a
workerless `main-unpushed` task independently of deployment recovery. Fresh dispatch validates its
fetched base and isolated worktree; a restart does not repair deployment edits.

For a known external CI wait, L3 uses `alt task recheck-ci <slug> --run <id> --at <ISO-time> --reason "…"`.
The task-local record survives daemon restart and exposes the due probe or coordinator delivery in
task status. One selected same-project rerun is permitted when relevant fresh CI is unavailable;
submission intent precedes IO, and uncertain writes are reconciled through run attempts without
resubmission. Reads, polling and coordinator handling are finite. The queue row and turn identity
remain durable until successful terminal chat evidence or visible exhausted delivery. Provider
execution without terminal evidence after a restart ends uncertain without replay;
the engine's transient execution timeout survives daemon exit and stops its process tree.
Changed evidence reaches only the originating L3; unchanged observations remain silent. A changed block,
attempt or lifecycle request invalidates the probe. Fault, questions, hold and provider ownership
stay intact; probing does not resume implementation. See [bounds and evidence](CLI.md#durable-ci-recheck).

L3 applies an original operator merge decision to each held PR through
[`hold-merge --approval`](CLI.md#recorded-merge-approval), preserving the source, applicable question,
current PR/head and scope judgment in its receipt. It reviews corrections in both task and project
chat. Altd validates authority, question evidence, the current hold and assigned/adopted PR identity;
[the architecture](ARCHITECTURE.md#isolation-and-landing) specifies those boundaries.

Release changes the hold while preserving worker and question state. L3 verifies the receipt before
resuming a blocked owner, who completes review and current-candidate checks through `alt land --merge`.
A follow-up restores the original review requirement for its own scoped release; an explicit renewed
hold requires approval of that renewed requirement. Failed reconciliation follows L3's recovery path.

Voice capture does not add a lifecycle state. **Stop** transcribes the bounded recording into
the editable draft; the send arrow transcribes, appends and sends at once. Both sending paths use
the same chat or L2-message endpoint as typed text, so a busy L3 durably queues the combined text
and an L2 message follows the same checkpoint/resume rules. Cancel, permission denial and
transcription failure create no conversation or queue record and preserve the typed draft.
During microphone startup, listening and transcription, the shared text field is read-only and keeps
existing text visible, with an indeterminate activity indicator and status inside the composer box.
Cancel and timeout restore editing. Navigation cancels voice input; late results cannot fill or send
to another conversation, including after a new recording starts.

Message acceptance is separate from the answer or wake succeeding. A saved L2 message receipt,
L3 turn ID or queue receipt keeps the composer cleared, including after a broken stream or failed
refresh; newly typed text remains. Failed immediate wakes leave accepted messages and their due
requests with the existing timer. A restart race retains the queued receipt in the response.
Explicit refusal restores recoverable text with Retry. When transport or a server failure leaves
delivery unconfirmed, the composer preserves both submitted and newly typed text, asks the operator
to check the conversation, and offers no send Retry, including when another overlapping send is refused.
It never infers acceptance by matching text.
Submitted-text recovery belongs to the original project/task beyond a composer mount. Browser-tab
storage retains each outstanding submission and the recovered draft; a live receipt retires only
its own submission immediately. Navigation preserves late failure recovery, and reload without a
receipt restores text with the unconfirmed hint rather than replaying it. Newly typed or dictated
text survives recovery. Ordinary unsent project text stays independently in client memory across
project switches and route remounts until reload; manual clearing remains cleared. Task drafts and
image selection retain their existing lifetimes. If initial recovery
storage is unavailable, submission does not start and the editable text stays visible. Response
streams do not pause conversation polling, and receipt updates cancel older reads of their own
conversation before updating cached records. Later failed recovery writes retain edits for in-app
navigation, with a keep-tab-open warning until storage succeeds; reload cannot recover unwritten edits.

`dispatch.resume` is the only way a session is launched again, and altd owns it for message-triggered and
explicit resumes. `alt task resume`, `stop`, and `reject` require a reason and persist a task-local
`daemon_request`; the CLI process performs no Git or worker operation. One `daemon-request` event names
the task, operation, actor (`l3` or `burak`), reason, and request id. Altd executes the request, refuses
a changed state or worker/session identity, and makes a retry with the same reason and actor idempotent
while its terminal state/worker receipt still matches. A later lifecycle receives a new request and event.
The inbox and `resume_after` are the coordinator-to-daemon boundary: they survive coordinator exit and daemon
restart. The keyed runner is the in-process fast path; a durable claim is the cross-process fence. Its
`dispatching` marker also makes the independent restart guard wait. If altd restarts after the replacement worker
identity is saved but before task binding, it adopts that worker only while the claim still names the
current block. A block or escalation gives that wait a new identity; an older claim restores its message
batch and stops any known unowned replacement without clearing the newer question. Fresh dispatch binds
only while its task remains queued. If it cannot prove whether a provider launch
crossed an unexpected daemon exit, it reports a real recovery fault instead of risking a duplicate turn.

1. a task blocked before any launch goes back to the queue;
2. `resume_after` makes a message request or operational retry due; an exhausted window of a pinned engine or a
   WIP cap keeps the task blocked with a `waiting: …` reason until the request can run;
3. the owner's worktree path, branch and adopted ancestry are validated without a remote fetch or
   deployment gate, and a worker that is still live is stopped first;
   systemd's `inactive` result with exit code 4 confirms a collected transient unit has ended.
   Unknown or unavailable status refuses the launch and preserves the claimed inbox for recovery;
4. the attempt's original engine, provider conversation and recorded `launch_model` are resumed with the
   inbox text (or "Continue from your progress file."); changed Auto preferences and project defaults do not
   alter that attempt. The replacement worker is bound atomically; a superseded bind stops the unowned
   worker and keeps the newer question. Launch failures and uncertain worker ownership retain incident
   evidence without changing a newer question's wait. A genuine
   Git validation or relaunch fault restores the claimed batch, consumes only the generation it tried, and blocks
   normally until another explicit request. A newer message carries a newer generation and stays due. A
   coordinator filesystem restriction never reaches this trusted boundary.
   An explicit resume receipt also consumes only its original block and request; it cannot remove the
   timer for an answer that arrived after a newer question.

Fault bookkeeping checks its observed block, resume request, worker/session and Stop or daemon
operation before changing the task. A message accepted after a worker-exit block keeps its wake;
a replacement worker or newer question keeps ownership. The incident evidence remains recorded.
An engine failure with an older queued message still follows the fault path.
At clean no-code completion, the final task lock checks the inbox before archival. Accepted steering
continues the saved session and supersedes the earlier completion request, including when Send arrives
after the daemon first observed the worker exit.

**Stop** is directly accessible beside the composer and in Live session on phone and desktop.
It queues one operation in altd without confirmation, records the observed worker and Stop identity,
holds inbox delivery immediately, blocks the task and stops the worker with its descendants. The poll
never reads the exiting worker as a death or lets concurrent final output undo Stop. The page says
Stopping until termination is evidenced, and Stop unconfirmed if termination cannot be established.
Check status reads evidence; it does not repeat the Stop command. Drafts stay editable throughout.
Desktop Escape requests Stop only when no input, dialog, recording, menu or overlay owns it.

Earlier and racing messages stay held until a correction or Continue explicitly names the confirmed
Stop the page observed. A stale running tab can queue a message but cannot undo Stop. Continue keeps
the unsent draft; a correction is appended after held messages. The existing daemon resume preserves
the session, attempt, launch model and dirty worktree. Capacity holds remain Waiting to resume.
The continued turn supplies its own completion; a completion requested before Stop cannot finish
the replacement worker. Failed sends restore their text alongside newer draft edits even when
the operator switches to Live session before the response arrives. Accepted sends remain sent.
`alt task resume --reason …` is also an explicit continuation; Reject ends the task and removes its
worker in altd. Stop does not undo completed external effects. An L2 that blocks with
`--fault` takes the system-fault path instead of asking Burak: the task, incident, FYI and L3
notification all stay in its project. Incidents use the source project and kind for a 24-hour window;
another task newly blocked by that kind or changed same-kind details still notify its L3 with the
same incident reference within the window. Each task retains its full fault reason, so unchanged
saved observations stay quiet across restarts and later windows. A changed blocker supersedes a
resume based on older evidence; an unchanged observation preserves a supported recovery request.
Repair-task faults (`--source recovery`) never wake L3
again. Faults without a project notify registered `altitude`, or only update the machine fault
ledger if it is absent. A failed resume blocks the task with an incident and leaves the provider
conversation to its project's L3. A project's L3 reports an upstream Altitude defect through
[`alt issue upstream`](CLI.md#upstream-altitude-defects) with a fictional or redacted reproduction;
local incidents and project-local recovery work stay with the affected project. Reporting creates
an issue at the daemon's configured product target. Confirmed creation or verified linkage sends one
fixed public issue-link notification to registered local `altitude` when its Git origin matches that
target. Without a matching project, reporting remains issue-only. The notification moves no evidence
and creates, reuses, resumes, or coordinates no receiving-project task. The reporting L3 does not repair Altitude;
Altitude's operator/coordinator selects implementation separately. Normal issue verbs remain bound
to the calling project's origin and accept no repository override. A cross-provider
continuation is a deliberate, recorded fresh attempt based on saved work: when a worker exhausts an
allowance and the task has no explicit engine/model pin, an eligible configured alternative can receive
a new attempt briefed with the task's `progress.md`. A named model allowance excludes only that model.
No eligible alternative leaves the task blocked. Only a provider-reported reset schedules resumption;
unknown reset times create no timer. The existing thirty-minute routing observation expiry is an
availability recheck, not a reset claim. Ordinary resume preserves the provider conversation and launch model.

For an already exited owner blocked by worker death or a recognized usage limit, L3 or the operator
uses [`alt task handoff`](CLI.md#explicit-provider-handoff) with the observed attempt, target engine
and reason. The daemon fences worker/session and block identity, refuses live workers, in-flight claims,
finished tasks and decision-only waits, and checks task/project pins and target availability. It retains
the old worker records and requeues the same task with a one-attempt `next_engine`. Requeue clears the
current verification snapshot; prior reports and delivery history remain. Dispatch checks pins
and configured target options again, preserves committed and uncommitted work in the existing worktree,
validates isolation and adopted ancestry, and increments the attempt only when the fresh worker binds. The target is then
consumed. Task identity, PRs, expected files, saved messages, unanswered questions, decisions and merge holds
survive; continuation supplies no missing approval. L3 verifies activation and fresh task state before
requesting a live handoff, and observes the new running attempt before reporting recovery.

Claude resume uses foreground `claude -p --resume` inside the task's transient unit; Codex resume
uses `codex exec resume <thread-id> -` with the inbox on stdin from the same task worktree.
Both engines have one contract: the persona may invoke the scoped Altitude
CLI, and the backend applies the identity, clean-Git, isolation, and merge-policy checks relevant to each
command and effect boundary. Claude hooks add telemetry and inbox delivery; they are not the backend authority
check.
Landing fetches the base and validates the current PR base/head pair. An owner whose branch needs
updating runs `git rebase origin/main` in the worktree; a conflict they cannot resolve goes to L3
through an ordinary `alt task block`, without `--fault`.
The delivery gate requires Python, web, build and phone/desktop browser checks. Review captures stay
outside Git, accessible until review is complete under the [project UI rule](../AGENTS.md#ui).
This repository's temporary local policy runs `make check` on the current merge candidate through
`alt land`, including PR preparation without merging. Logs, the self-contained browser report and
candidate-bound results stay in the task's `local-checks/` folder, without duplicate raw attachments.
Suite timeouts preserve captured stdout and stderr in `check.log` and leave validation failed.
Only passing current evidence supplies the PR test
line. Historical hosted failures do not gate delivery; failed local checks, stale base/head,
required reviews and merge holds still block. Other projects retain their configured gate.
L3 verifies each blocked owner's remaining causes before resuming the existing session; policy
activation does not establish billing/artifact recovery or release an operator hold.
Fetch housekeeping may pack protected refs while local main is behind its fetched remote. The
hook permits unchanged logical tips and pruning of loose copies retained at the same packed tip;
actual unauthorized protected moves and deletions still refuse. Housekeeping does not advance main;
the supported guarded fast-forward performs that move.
Its real-Git validation runs with captured output and open caller stdin; fixture packing supplies
its own revision input so it cannot wait for the worker's input stream to close.
The shared CLI test fixture likewise supplies explicit empty input; the same open-input regression
checks issue-close CLI/API validation without changing the production command's input handling.

An owner assigned an existing external PR incorporates its history in the isolated task branch,
then uses [`alt land --adopt-pr N --expected-head SHA --reason "…"`](CLI.md#adopt-an-existing-pr).
Adoption records an immutable PR/head receipt and event, and status exposes the adopted PR.
After a verified history-preserving merge on main, the same task can explicitly select its next
assigned PR with that PR's observed head and authorization reason. The earlier receipt stays
unchanged in `adoption_history`; `adopted_pr` selects the active landing, ancestry and approval
target. Failed or repeated transitions do not overwrite earlier receipts or expand their authority.
A prior PR's recorded hold approval restores the original requirement for the next adoption unless
the operator subsequently released it for the task as a whole. L3 checks the original decision's
scope before releasing the new active PR; a restored hold retains its generation, while an explicit
renewed hold changes it and requires approval of that renewed requirement.
Landing and resume preserve the original head's ancestry. Commit messages, including historical
labels naming other tasks, carry no ownership authority; landing excludes other active tasks' PRs and branches.
The local task branch stays unchanged in identity while fast-forward pushes update the original
PR branch. Merge main when necessary to preserve the adopted
commits. No adopted push uses force, and adoption never expands to a later external head.
Checks bind to the current authoritative base/head and candidate, tolerating stale `baseRefOid`
metadata while refusing actual movement. Missing or skipped required checks remain blocked;
nonrequired skipped checks are ignored without interpreting workflow conditions. Failed, cancelled
and pending checks still block, including nonrequired checks. Unknown requiredness and ambiguous
candidate evidence refuse delivery; at least one hosted check must actually pass.
Review blockers, required checks and merge holds apply to the reused PR; the owner, active PR and hold are
checked again immediately before merge. Recorded operator approval matches the adopted PR's
number, URL and branch. A no-CI suite tests a two-parent candidate, and the GitHub merge retains
history without requesting deletion of the original branch. The normal report and archive path
verifies delivery; adoption grants no authority over another project's task.

### Continuing an active task after merge

Merging a PR leaves the task and its owner conversation available for further authorized work.
The next ordinary `alt land` keeps the isolated worktree and local branch, verifies the earlier
merge on main, and replays only follow-up work onto current main before opening another PR.
The task retains every delivery; no-work retries open nothing, including work already on main.
Each PR needs its own candidate checks, review and applicable hold release. Prior PR-specific
approval does not release the next PR's hold. See [continuation commands and recovery](CLI.md#continue-after-a-pr-merges).

The attempt, engine, launch model, provider conversation and durable messages do not change because
of a merge. Resume still uses the existing claim and Git isolation gates. A new resume claim discards
previous completion verification before launching the owner; a new delivery also invalidates it.
The current `delivery` timestamp joins worker launch/resume time when deciding report freshness.
Final reports cover every recorded PR and the current published work; historical success cannot
complete unpublished follow-up. A raced verification is refused if the delivery changed before
report handoff. Existing restart adoption and merge activation observe the continuing task normally.

## Engine containment

A Codex L2 runs in Codex's own workspace-write sandbox: the task worktree, its Git directories (the common
directory and the worktree's own metadata under `.git/worktrees/`), and the Altitude home are its writable roots, the network stays on for pushes, PRs, and tests, and the launch environment
carries the task identity. A Codex L3 turn uses a dedicated permission profile: it writes only one fresh per-turn
runtime directory; the deployment checkout and Altitude home are read-only, direct command networking and the
user-service bus are denied. A required stdio MCP adapter exposes one coordinator tool for `alt` verbs and
GitHub/service reads, forwarding argument arrays and stdin to the project's fixed altd socket. Every fresh
and resumed turn receives this configuration and the instruction to use the tool; shell wrappers remain
unreachable inside the native sandbox. The adapter starts isolated Python from protected source, never
loads code from the writable runtime, and has no shell execution operation. Its one tool is approved for
unattended use; altd still applies its project and actor authorization on every request. A Claude L3 turn has the same runtime cwd and uses
`--restricted`, `dontAsk`, no unattended permission
prompts, no Edit/Write/NotebookEdit tools, and native admission for trusted shim names. Both can read the checkout with Git
log/diff/show shims, including full patches and historical files with external diff/text-conversion helpers
disabled, and the altitude journal. Claude's runtime shims and the MCP tool send every `alt` invocation and fixed
GitHub/service read through the project-bound socket, where altd supplies the project, rejects path-shaped task ids and
daemon-side file inputs, and re-applies the L3 command door; GitHub reads cannot select another repository, and checkout, GitHub, and service
write commands are absent; `alt issue new` and `alt issue comment` publish requested backlog through altd after its private-evidence check.
`alt issue upstream --title "…" -` uses that same broker/MCP boundary on either engine, with a
create-only product target and a fictional/redacted JSON reproduction on stdin. No additional GitHub
write tool or cross-project task authority is granted. Altd validates the public fields and returns
an issue URL or an actionable failure; the successful receipt stays in the calling project's log.
For system incidents, `--incident <id>` retains delivery on the existing source-project/fault-kind
record and exposes it through incident inspection and coordinator state. A confirmed URL survives
repeated notifications, new incident windows and daemon restart. Uncertain is persisted before
creation and retained after interruption, timeout, nonzero exit or unconfirmed output; creation
cannot be repeated for that identity. The operator checks existing upstream issues, and L3 can
attach a verified match with `--incident <id> --url <url>`, which performs only a GitHub read.
Failed prepublication checks retain an actionable reason. No outcome resumes tasks or grants repair
ownership; historical backfill and uncertain-result retry remain separate operator decisions.
Recovery and prevention remain separate across sessions: L3 records recovery observations, the issue,
prevention disposition and owner/next action in existing incident evidence. `watch` retains pending
reporting, delivery or effectiveness; closure records verified prevention or an evidence-backed
no-change disposition. Incident reads load current Markdown status/evidence/cause, and coordinator
state shows the latest five records not closed with bounded evidence, including role-only failures.
Reporting success cannot hide pending prevention. Unreadable evidence remains explicit; these reads
create no tasks, notifications or publication retries. L3 gives a concise recovery/follow-through FYI
when evidence or action changes and keeps unchanged repeats quiet. The receiving development
coordinator triages public reports under its own authority, independently of the reporting host.
Local notification has a separate `queued`, `received`, `unavailable`, or `failed` outcome in incident
inspection and coordinator summaries. Publication success survives queue failure; repeating a confirmed
incident command retries notification without another GitHub creation. The receiving queue and retained
project event log deduplicate the full issue URL across source projects and restarts. A queue claim saves
its receipt before dequeue, preventing a second notification in the gap before chat is written. `received`
records that claim rather than model completion; abrupt exit after dequeue retains the queue's existing
delivery limits. The ordinary server-triggered queue/chat turn has no task association. A notification
does not prove repair, clear a blocker, alter a hold, or change an originating provider conversation.
L3 may use `alt issue close <number> --reason completed|not-planned` for requested closure or to
reconcile verified completion of an authorized delivery with `completed`, without another routine
operator request. Unrelated autonomous backlog cleanup remains unauthorized. It follows the same coordinator/socket boundary, publishes no text,
and records the actor, issue number, closure reason, and URL in the project log after GitHub succeeds.
L2 remains unable to mutate issues.
For authorized superseded-PR cleanup, L2 hands authorization and replacement-delivery evidence to L3.
L3 verifies scope and uses [`alt pr close <number>`](CLI.md#superseded-pr-closure) through its existing
project-bound transport; the operator can use the same verb through altd. Closure retains branches
and archives, verifies the resulting state, and records confirmed outcomes in the project log.
Already-closed and merged PRs return without mutation; uncertain results never imply completion.
Closing a PR does not finish, reject or resume its owner, release a hold, or prove activation.
Missing supported capabilities lead to scoped recommendations and authorized remediation under the
[L3 guidance](../personas/l3.md), with permission changes still requiring their applicable approval.
Claude's native Bash
sandbox is unavailable on this host because unprivileged bwrap namespaces cannot be created, so enabling its
hard-failure mode would prevent every headless L3 turn; the deny-by-default tool boundary and runtime cwd provide
Claude's confinement, while Codex retains its native filesystem sandbox.

The September 7 coordinator outage is verified with the real Codex Linux sandbox, not profile assertions:
`ALTITUDE_TEST_CODEX_SANDBOX=1 python3 -m unittest tests.test_l3_privilege` runs the broker transport tests
and a native sandbox probe for checkout/state/Git writes and direct socket/HTTP access. The opt-in requires
a working Codex installation and fails if sandbox initialization fails. This host-capability
probe makes no model calls and is separate from routine deterministic checks. Fresh live-session
acceptance through the production launcher is deferred under the testing policy; deterministic
transport and permission tests do not prove live-session confinement. Activation uses the normal
merged-change quiet point and its existing API/UI health verification.

An L3 session on either engine that predates this confinement policy is rotated before its next turn.
The common session save records the version, so each engine then resumes its own current conversation.
This also migrates legacy Codex conversations onto the MCP transport: changing launch configuration alone
does not replace their persisted developer instructions on resume. The next ordinary Altitude turn owns
this one-time rotation; activation needs no manual session reset or task action.

Both engines launch L2 workers through the same transient user-unit command builder, outside altd's
cgroup. Claude runs foreground `-p --output-format stream-json` inside its own unit, with its settings,
hooks, model pin and resumable session; launch and resume stop any daemon job still bound to the task name.
Each turn has a private worker record and output log, and Stop removes the unit's descendants.
Each Codex turn also uses this boundary,
because altd's own `NoNewPrivileges` hardening would stop its nested bwrap from starting.
The outer launcher alone receives the user-session bus;
the child starts from Altitude's clean environment plus the actor identity; stopping the unit stops the whole
process tree. Codex stdout JSONL is private task evidence: `thread.started.thread_id` is the session identity and
`turn.completed.usage` the latest reported usage. A turn that ends without a report, a block, or a completion
blocks the task with a system fault and incident carrying the engine's result error or stderr tail.

## L3 sessions and provider changes

The project header's **Remove project** action and `alt project remove <name>` detach L3 by
unregistering the project. Removal is permitted only after all tasks finish or are rejected and
their workers and operations have ended. An active L3 turn or report/timer operation must finish
first. The cross-process project activity lock applies to CLI and server turns alike; queued L3
messages remain saved and do not themselves prevent removal. No task is reassigned or stopped.

The app leaves the removed project and reconciles its selection; stale project/task/report routes
show an unmanaged state, or First run when nothing remains managed. The repository, remaining
worktrees, provider sessions, conversations, task archives and queue stay on disk. Adding the same
project name and repository attaches L3 again, restores history and delivers the waiting FIFO.
Normal provider selection and context rotation still apply. Reset rotates a session on its next
turn and does not remove a project from management.

L3 stores separate Claude and Codex session records. The conversation composer's engine pill pins
the project's L3 to one engine, for chat and server-triggered turns alike, until it is set back to
Auto; `alt chat --engine` pins one CLI turn. A pinned turn runs there or reports the hold, and never
falls back. A turn started from Chat finishes and is recorded even when the page that started it
leaves mid-stream. Switching projects mounts a separate conversation: draft, pending prompt,
streamed text and local errors leave the screen. Concurrent sends and late responses retain their
original project; returning reads that project's history, queue and active turn. A stored failed
turn offers Retry only in its owning conversation. Each project's unsent text returns across switches
and route remounts within the current client session.
Leaving a voice composer stops its recorder, releases microphone tracks and cancels transcription;
an outstanding microphone permission or transcription result cannot populate the new conversation.
L3 runs headless, so its only checkpoint is the turn boundary: a message Burak
sends while a turn is in flight is appended to the project's durable L3 queue and run there, never
injected into the running turn. The finishing turn drains the queue itself, one turn at a time and in
arrival order, batching consecutive chat rows for the same conversation while keeping system turns
and other conversations separate. Each waiting chat row remains individually removable until claim;
messages arriving after that snapshot wait for the next turn. A message queued but not started is not
a turn in flight, so it neither holds the quiet-point restart nor is lost by one. An Auto-selected turn resumes only the chosen provider's session;
choosing another configured model on that provider retains its conversation.

Every fresh session, whether from first use, reset, context rotation or a confinement policy change,
receives the project's latest 20 prior human chat messages from either provider, oldest first. A
replaced session's `last_turn` does not limit this context. Selection uses `user` and `assistant`
rows with trigger `chat` or no trigger, before applying the count limit: server reports, restart
turns, other system events and their replies cannot displace human discussion. The current turn's
rows are excluded. Messages are labeled historical context for the current request, rather than
new instructions, with at most 800 characters of each text and an explicit `[truncated]` marker
when longer. Task state remains in `STATE.md`; no tool evidence or generated summaries are replayed.

Every fresh or resumed L3 turn also names `alt l3 search "literal text"` for evidence outside the
handoff. Owners use the same lookup through their CLI, bound to their launch project alongside repository,
PR and tool-summary reads. Both roles share project inspection admission; mutation permissions stay separate.
The coordinator lookup uses its project-bound transport on either engine. It scans human project chat and active/archived task conversations, reports and digests;
no provider session identity limits the search. Original excerpts retain dates, attribution and
source references, with adjacent context and explicit result/text/output bounds. Search writes no
memory and performs no model calls. Empty evidence is `no_results`; unavailable evidence is an
error. The coordinator checks original conditions and later corrections and treats history as
evidence under current instructions and authoritative task records. See
[CLI semantics and limits](CLI.md#historical-evidence-search).

A resumed session keeps native continuity. When another provider handled intervening turns,
Altitude supplies only the cross-provider missed-message handoff: user/assistant rows
newer than the selected session's `last_turn` whose engine differs, up to 20 from the latest 60
log rows. It uses the same text bound and historical label. A fresh session receives only fresh
context, and a native resume with no missed rows receives neither block, avoiding duplicate
injection. A limit or access rejection after text or tool activity never causes the same turn
to be automatically replayed on another option because that could duplicate side effects. A confirmed
rejection before output or tool effects can try each remaining configured option at most once.
Provider selection changes
neither L3's project-level responsibility nor L2's end-to-end task ownership.

Every direct, queued, folded, or server-triggered L3 turn publishes one process-local active record
while it owns the project turn lock. The record contains only a stable turn id, its start time, and
trigger; the prompt remains in the normal private/history path. `GET /api/chat` is the UI authority
for this state. It snapshots history, the waiting queue and active record under one lifecycle guard.
Queued admission records the user text before publishing its turn through that same guard, so a
returning conversation sees either the queued text or its history row. Routing happens before claim,
and a failed history append restores the waiting queue. The initiating tab keeps its streamed
response and suppresses a duplicate indicator, while a newly
mounted or reconnected conversation reconstructs the typing indicator (a chat turn) or the "L3 is
handling <what>" line (a server-triggered turn) from the active record. The stream's first line
names the turn, and history rows carry the same turn id. After the stream ends, saved history
replaces local output; an active record restores the typing indicator, while a saved assistant or
error row suppresses any raced active snapshot. A
`finally` removes the record on every normal, provider-error, or exception path. If altd
fails, the in-process turn ends and its process-local record disappears with it, so the replacement
process cannot advertise stale work.

## Polling and cleanup

Claude jobs and Codex processes normalize to the same worker row: worker id, provider session id,
PID, state, status, detail, and latest usage. Polling follows the persisted `l2_engine`. A merged change
to Altitude's backend, launch source or served web bundle inputs activates at a narrow quiet point: no dispatch
marker or resume claim, L3 turn, or report verification in flight. Running and blocked workers do not
hold activation, and new dispatches continue while activation is pending. The regular thirty-second
tick discovers merged changes independently of worker completion. Dispatch, resume, L3 turns
and report verification wait only from the restart unit request until the replacement daemon is
ready; the ten-minute restart fault releases a stuck window. altd runs the guarded build-and-restart
script itself.
After an
`altd` restart, both engines are adopted from their private worker records, provider output and active units;
existing daemon jobs are observed through their active unit and session transcript until they finish or resume.
Both engines' units survive the service restart.
An ended or missing worker's report is current only when its mtime is at or after the latest launch
or resume timestamp, persisted on the task before the provider starts. A missing or stale report on a running task
without an explicit completion is a system fault
that blocks the task and files an incident; a current report goes to verification. Rejection and
post-merge cleanup use the same provider adapter.
An explicitly question-blocked task remains waiting when its worker exits or disappears; it needs no
completion report until a later authorized turn runs. A stale finished-worker observation cannot replace
a newer block with a worker-death fault.

A worker's PATH resolves `alt` to the deployment checkout's `bin/alt`, so each invocation uses the
current CLI; L2 commands and the inbox hook use locked durable state directly and keep working while
altd is down. The daemon reads durable completion and inbox records at the next checkpoint after startup.

## Live transcript

Conversation and Live session are local views of one L2 task. Phone tab changes replace the
current browser entry while retaining navigation state; the desktop panel toggle is local state.
The `/live` URL opens the live view on direct entry and reload. Browser Back and app Back return
to the preceding page after normal in-app entry. Without an in-app predecessor, app Back replaces
the task entry with its owning project's L3 conversation; browser Back follows the browser's own
history. Viewing, switching views, and leaving the page do not change the worker's lifecycle.

Phone Conversation and Live session tabs remain visible while typing hides the global bottom
navigation. Keyboard dismissal restores that navigation without clearing the draft or selection;
local view changes retain the draft, while leaving the task follows the existing discard rule.
The compact header names L2 activity and Merge held independently. Full block/hold reasons,
metadata, Reject with confirmation and operational Resume open in task details. Stop and Continue
remain directly accessible in both views on phone and desktop. Faults retain a visible cause and
the L3 notification. An open question stays at its conversation anchor with View question/Latest
messages and no generic Resume. Disclosure,
keyboard transitions and ordinary replies do not change decision or merge authority. Resizing
preserves bottom-follow or the older message being read, and sending resumes following.

Conversation shows a replacing two-line preview of the current worker's public words above its
composer. Expand reveals the full update after existing redaction. Prose uses its native source time
only when identifiable; untimed words say Time unavailable. Recorded output has a separate observed
age. No new output for 60 seconds reads as quiet, without inferring useful progress or failure.
Missing commentary, an empty source and an unavailable source are distinct. Resume clears the old
direction until the new worker emits public output. Questions, decisions and explicit results stay
durable in Conversation; older worker output remains only in Live session under existing retention.
No summarizer or duplicate reply is generated. View changes preserve the draft and selection;
blocked questions use the existing question conversation, and finished tasks remove the preview,
composer and Stop.

The task page's conversation is the operator's exchange with the L2. Its live session panel (the second
tab on a phone) reads like a Claude Code window: the engine's local session records and Altitude's task events project into one timeline
in time order, and the page renders it as a conversation. Prompts (the brief, a resume, a task message the
worker read at its checkpoint) appear as prompt blocks; the worker's replies as prose with code blocks; each
tool call as one compact row (`$ git status`, `Read altitude/tasks.py`, a Codex command or file change) with
its output folded under it; task boundaries (queued → running, stopped, resume held) as thin separators with
subtle timestamps. Every row carries its role (user, assistant, tool, system), and a tool result carries the
id of the call it answers, so the page nests output under the command that produced it. Codex does not echo
its prompt into the thread, so the projection shows the task's own record of it: the brief for the first
turn, the delivered task messages for a resumed one. Hidden model reasoning (Claude thinking blocks, Codex
reasoning items) is stripped before anything crosses the HTTP boundary and never appears in either mode; the
panel does not run a summarizer. Tool output is bounded in the transcript, and Raw events lists every redacted
record (credential-shaped keys and values replaced) as the escape hatch. The browser supplies no paths: the
server derives the files from the registered task's engine and session id, reading the provider's own session
store (Claude's project JSONL, or the stdout JSONL of every turn of the Codex thread, kept in the task
directory). A mismatch between the displayed session and the task fails closed and asks that viewer to refresh.

Altitude does not copy or checksum provider transcripts. The durable human record is the task conversation;
provider stores follow the provider's own retention, and archival moves the whole task directory, so Codex
worker records travel with it. Task state changes and stop/resume records are the timeline's boundary events. A
parser error or incomplete final JSONL record is displayed as viewer evidence and retried on the next poll; it
never changes task or worker state.

## Task token accounting

The task header and report retain cumulative **observed tokens** across recorded owner sessions,
resumes and engine handoffs. `token_usage` on the task holds the public accounting; the task folder's
`token-usage.json` holds engine cursors and numeric deduplication evidence. Both travel into archive,
so final accounting survives provider-log or worktree cleanup. Earlier attempts whose identities or
counters are unavailable remain partial. Queued/older tasks without readings say unknown, never zero.
The task API, Monitor's L2 session rows, `alt task status`, and `alt task report --json` expose the same snapshot independently
of agent-authored `report.json.spend`.

Input is inclusive of cache reads and writes exactly once. Output includes reasoning when the
provider reports it as a subset. The combined count is observed inclusive input plus output, across
sessions whose local records support attribution; it is neither context occupancy nor quota usage
nor a billing estimate. Each model request counts its supplied input again, including cached input;
this measures consumed tokens, not unique words in the conversation. Engines use their own tokenizers: adding observed counts is an activity
measure, not a comparable price or workload measure. Optional cache/reasoning counters remain
unknown when absent. If some contributors lack a counter, the known contributions are retained as
a partial lower bound; the total adds the available input and output contributions. It is unknown
only when neither has been observed. Incomplete fields and helper coverage remain explicit.

| Engine evidence | Counting semantics |
| --- | --- |
| Claude assistant records | One request per message ID, with maximum counters across repeated streamed blocks. Inclusive input adds uncached input, cache-read input and cache-creation input; cache-creation duration breakdowns are subsets, not extra tokens. Output is counted once per message. Synthetic limit records are excluded. |
| Codex response usage records | One increment per response ID, attributed to its recorded thread ID. Cache-read input is already within input; reasoning output is already within output. Repeated cumulative and turn snapshots are not added to the same response records. |
| Provider aggregates | Available older snapshots or owned turn results supply partial provider observations when request records are unavailable. They cannot claim an owner/helper split; overlapping helper observations are excluded. |

Native helper records require a task owner's recorded parentage. Shared response/message identities
deduplicate replayed history on resume and fork; copied records belonging to a different thread are
excluded. Available native children appear as delegated work, without creating a lifecycle role or
supervising helpers. Discovery cannot prove exhaustive helper coverage, so observed task totals may
be partial even when the owner's counters are current. Project-global L3 usage is not charged to a
task. Collection uses no model calls, summaries, live-agent experiments, or external export.

`token_usage.helpers` retains observed helper identities even when their counters are unavailable
or an ancestor's unsplit provider total covers them. `observed_count` counts distinct engine/native
identities across all recorded owner sessions; repeated observations and resuming the same identity
do not count another helper. Registered task owners are excluded, even when native records show a
prior parent. `direct_count` and `descendant_count` count known spawning depths; `unclassified_count`
counts owner-linked identities whose depth is unknown. Without evidence a count is null, not zero.
An empty observed set means **No helpers observed**, with partial coverage, not that no helpers spawned.

Codex thread parentage establishes direct and recursive descendant links. Claude helper files bind
`owner-session/agent-ID` to the recorded owner, including when its transcript has disappeared; that
directory alone does not establish spawning depth or cross-owner identity equivalence. Helpers that
leave no discoverable native record are unknown. The API's per-helper `owner_session_id`, `parentage`,
`depth`, and `attempts` expose those limits: attempts are the owning session's recorded attempt
context, not proof that the helper spawned or ran in each attempt.

Helper `total_tokens` sums only attributable request input/output, retaining unknown counters and
excluding replayed history. The helper summary sums these disjoint request observations as a lower
bound. A helper's available unsplit subtree `provider_total_tokens` is shown separately, never added
to its own counters or the helper sum. The task's provider total remains intact; the helper sum is
already covered by task accounting and is never an extra charge. Monitor, task and report disclosures
show the same audit, with all helper counts and per-helper details hidden when collapsed.

The daemon reads complete appended JSONL records in bounded batches, persists byte cursors, and
refreshes at report/finalization boundaries. An unfinished trailing record is retried; unread,
replaced, inaccessible or lost evidence produces a coverage gap rather than an invented count.
The UI distinguishes **checked** (collector time), **observed** (provider counter time), and
**finalized** (the retained completion observation). A live check older than a minute is stale;
finalized observations retain their timestamp rather than becoming live-stale. The detail disclosure
shows engine, owner/delegated/provider coverage, input/output and available cache/reasoning subsets.

## Context and prompt-cache evidence

Claude L2 settings explicitly supply `autoCompactWindow` from `config.AUTOCOMPACT_WINDOW` through
the engine boundary. Native compaction remains the engine's responsibility; Altitude keeps the
owner's small progress checkpoint for recovery and handoffs.

For Claude, context is the newest genuine assistant usage record: input plus cache-read plus
cache-creation tokens. Synthetic all-zero limit records are ignored. Codex task context is unknown:
`turn.completed.usage.input_tokens` measures cumulative consumption, not context occupancy. Its
cache counters are part of the separate task token observation.

Every quota and session figure carries the time it was observed, and age is reported rather than
hidden. Monitor displays each available usage window independently, including zero, and explicitly
names an absent window. Partial readings keep their available figures and reset times; only a seat
without any usage figure says "No reading." A quota snapshot older than thirty minutes — the age
at which the router stops routing on it — is stale: its figures are still shown and labelled, not
replaced by "unknown", which is reserved for having no reading at all. A session snapshot older than
five minutes while its worker is live is stale in the same way; an idle or finished worker is simply
as old as it says.

A provider session id records which provider conversation Altitude asks to resume. That documented
resume behavior does not prove a cache hit or imply any undocumented prompt-cache guarantee.
Altitude reports cache-token fields only when the provider emits them and otherwise makes no claim
about cache reuse. Physical worker replacement and provider-session continuity are therefore shown
as separate facts.
