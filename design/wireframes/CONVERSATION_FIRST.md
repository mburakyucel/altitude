# Conversation-first decisions — proposal for review

**Pending the operator’s design approval.** This is the first milestone of the conversation-first
Needs you task. It proposes changes to SPEC §3.8–3.10, §4.3 and §5; those approved sections continue
to describe production until this proposal is accepted and implemented. No production behavior
changes in this PR. The existing boards remain intact.

[Open the review](conversation-first/index.html) · [All boards](index.html) ·
[Phone prototype](MobileConversationFirstNeedsYou.html) ·
[Desktop prototype](ConversationFirstNeedsYou.html)

The recommendation is simple: **Needs you is a short list of questions. Each question opens the
owning L2 conversation.** Accept the recommendation directly, or reply in that conversation.
The L2 handles a clear typed decision without another confirmation. A follow-up keeps the
question open while the L2 answers.

The fictional example is Atlas’s index rollout. The L2 recommends keeping the old index for seven
days; the operator asks about rollback and can choose fourteen days instead. L3’s escalation
context appears in the L2 thread, attributed to L3. No content comes from a real task or session.

## Review the experience

The HTML boards use the existing tokens, shared board generator and zoomable viewer. They open
from disk and have a small scripted prototype for the listed examples. Prototype transitions
illustrate the proposed behavior; they do not call an API, classify arbitrary answers or run a
provider. The phone keyboard is an illustration. Voice controls simulate the listed states without
opening a microphone. Screenshots in the review folder are committed so this review is available
from the PR without a local server or a merge.

| Step | What to review | Desktop / phone |
| --- | --- | --- |
| 1 | One card, a question, a short recommendation and **Use 7 days & resume**. The question/title and **Open L2 chat** open the same destination. | [Desktop](ConversationFirstNeedsYou.html) · [Phone](MobileConversationFirstNeedsYou.html) |
| 2 | Open at the durable question, with the preceding L2 explanation and L3 escalation context. A later technical event stays behind **Activity & evidence**. The same acceptance component sits within the conversation. | [Desktop](ConversationFirstQuestion.html) · [Phone](MobileConversationFirstQuestion.html) |
| 3 | Type “Could we roll back after day seven?” and send. The L2 answers; the question and Needs you count remain open. The list stays compact, with a quiet discussion status rather than a mirrored exchange. | [Desktop](ConversationFirstFollowup.html) · [Phone](MobileConversationFirstFollowup.html) |
| 4 | “14 days” directly answers the retention question and is enough. A nuanced answer such as “Keep it for 14 days, then delete it. Go ahead.” also sends once; no note form or second confirmation appears. The phone composer remains above the keyboard. | [Desktop](ConversationFirstAlternative.html) · [Phone](MobileConversationFirstAlternative.html) |
| 5 | “Maybe two weeks, but I’m unsure about cost.” leaves the choice open. The L2 addresses the uncertainty and clarifies in chat. Meaning is assessed in context by the owning L2, not by keyword matching. | [Desktop](ConversationFirstClarify.html) · [Phone](MobileConversationFirstClarify.html) |
| 6 | A normal operator message, a quiet **Decision recorded** receipt and the L2’s plain acknowledgement replace the open recommendation control. **Work resumed** appears only after the worker actually resumes. | [Desktop](ConversationFirstAccepted.html) · [Phone](MobileConversationFirstAccepted.html) |
| 7 | Quick acceptance from Needs you removes the card and decrements the count after the server saves the choice. A brief receipt links to the conversation. Zero decisions shows the calm empty state. | [Desktop](ConversationFirstEmpty.html) · [Phone](MobileConversationFirstEmpty.html) |
| 8 | A stale link shows what was decided elsewhere, by whom and when. It keeps the historical discussion readable and offers no obsolete acceptance. | [Desktop](ConversationFirstStale.html) · [Phone](MobileConversationFirstStale.html) |

The prototype accepts the exact example sentences above, plus “Use 14 days.” Other input stays editable with a
prototype hint. The actual implementation uses the existing L2’s conversational judgment, with no
second model or form-based intent selection. The prototype keeps only its own fictional decision
and same-task draft in browser session storage, so Back/Forward shows that saved outcome. Choosing
a scene in the review hub resets the example. Voice simulates the fourteen-day example.

## Interaction rules

**One acceptance component.** A question and at most two short sentences of recommendation, plus
one primary action naming its effect. Needs you adds the task/project label and a link to chat.
Chat supplies surrounding messages and uses the same body and button. No recommendation means
no invented default: show the question and open chat. Discuss alternatives in the composer;
existing explicit Stop/Reject task controls retain their own semantics and confirmation rules.

**One place to talk.** User bubbles and L2 prose use the familiar L3 conversation treatment and
composer, including voice. L3 contributions have a small L3 attribution. There is no recipient
selector. Technical events and reference links are available in a disclosure; Live session stays
available using its existing task view, closed initially when entering a decision. Expanding
activity reveals its rows; collapsing removes them from view. Existing clickable PR/issue links
remain links. Provider transcripts and hidden reasoning do not become the human conversation.

**A question remains open while discussing it.** Sending a follow-up saves the normal message
and wakes the same L2 to answer at its ordinary checkpoint. Needs you still counts the question;
it may say that the L2 is replying or waiting for capacity. The L2 can investigate and explain
the choice, but waits for authorization before implementing the disputed approach. Merely waking
the worker, a generic operational resume, a tool event or a chat acknowledgement never resolves it.

**A clear decision is enough.** “14 days” in direct response to the retention question resolves it once the owning
L2 records the chosen approach against that operator message. The L2 acknowledges the specific
action in prose and continues. The message shows as sent while the L2 processes it; the card
leaves only after the durable resolution, not an optimistic browser guess. A real ambiguity gets
one focused question in the conversation. There is no blanket confirmation for typed answers.

**Acceptance and execution are different observations.** Quick acceptance saves the decision and
normal operator message before requesting the existing wake. Controls disable during the write.
After persistence, Needs you clears and chat shows **Decision recorded**. A capacity or launch
problem says **Waiting to resume** and retains the receipt; it never invites a second decision.
**Work resumed** requires the normal worker-state observation. An already running L2 receives the
decision at its next checkpoint; acceptance does not stop or replace its worker.

**Navigation lands at the named question.** The target is
`/projects/:name/tasks/:slug?question=<id>` (and its revision when linking a historical version).
Load the durable anchor plus nearby discussion, focus the question without activating its button,
and suppress the normal initial scroll-to-latest. Later transcript output cannot move that target.
An arriving reply offers **Latest messages** when below the current reading position; following
the bottom resumes normal chat behavior. Scrolling away leaves a compact **View question** link
when an unresolved question is offscreen. This is an anchor, not a second pinned decision form.

Opening a Needs you item selects its project and pushes one task entry. Back returns to the
originating list or project, preserving its scroll position and query. Conversation/Live session
switches keep the existing local history behavior. A direct task link’s app Back opens its project;
browser Back remains native. Returning via Back or Forward refreshes the question state before
enabling acceptance. The prototype’s app Back demonstrates same-origin history and uses Needs you
as its standalone fallback; production retains the existing project fallback.

Same-task navigation preserves an unsent draft. Leaving the task discards its unsent draft and
transient voice state under the existing composer rules. A pending send remains tied to its source
task; navigating away does not reroute it. Closing a disclosure or changing the live view adds no
browser-history entry. A resolved active task keeps its ordinary composer; an archived task is
read-only. The phone shell tracks the visual viewport so the question history scrolls above the
composer and keyboard without horizontal scrolling.

## States and transitions

The [review page](conversation-first/index.html#states) links every state directly at both sizes.
They are query-selected views of `ConversationFirstStates.html` and its phone board. All share
the same rendered components, rather than independent state illustrations.

| State / trigger | Visible result and removal | Recovery or next action |
| --- | --- | --- |
| Needs you loading / read failure | Card-shaped skeleton; no count inferred as zero. Failure replaces skeleton with a sentence. | Retry the read. |
| Needs you empty | No cards or zero badge; “Nothing needs your decision.” | Open an existing project normally. |
| Needs you during discussion | Card and count stay. One quiet status; the exchange remains in chat. | Open the same question. |
| Chat loading / first read failure | Question/prose skeletons; send and acceptance unavailable. Failure exposes Retry. | Retry the read, not the decision. |
| Cached read failure / offline list | Saved content remains labelled as stale/offline; acceptance and sending disabled. | Refresh before applying a choice. |
| Quick acceptance pending | Primary action reads “Recording…”; duplicate input disabled. | Await the response. |
| Acceptance failed | Error beside the same recommendation; buttons return. No success receipt. | Retry checks the same question and reuses a saved receipt if the response was lost. |
| Write denied | Readable discussion with a clear refusal; send and acceptance disabled together. Typed text cannot bypass a denied approval. | Reconnect and refresh the authoritative state before writing. |
| Typed input / phone keyboard | Editable draft and enabled arrow; chat shrinks above keyboard. Empty/whitespace draft disables Send. | Send once; keyboard dismisses on successful send. |
| Message sending / failed | Pending bubble during send; on failure it leaves and the exact draft stays. | Retry from that draft, bound to the same task. |
| Message saved, capacity unavailable | Saved bubble and waiting status; question still open. No duplicate unsent draft. | Existing resume path delivers when available. |
| L2 reply failed | Original message retained, error at that turn, question open. | Retry the reply using the same message; no duplicate user row. |
| Accepted, capacity unavailable | Recorded answer, count cleared, no accept action; explicit waiting status. | Ordinary worker recovery; no re-approval. |
| No recommendation | Question and composer, no default acceptance. | Ask the L2 or type a decision. |
| Updated recommendation | Earlier question remains readable; obsolete action disappears. Updated question/action is explicitly labelled. | Review and accept the new revision; never retarget an old click. |
| Resolved elsewhere | Historical question/outcome, source and time; no accept action. | Continue discussion in the active task. |
| Question missing | Explicit unavailable message; no fabricated latest-event anchor. | Open the task conversation or return. |
| Archived | Saved conversation and resolution remain; composer and acceptance gone. | Back to the origin/project. |
| Listening | Waveform/timer with Cancel, Stop and arrow; typed draft preserved internally. | Cancel drops audio; Stop puts transcription into the draft; arrow transcribes and sends once. |
| Transcribing / dictation landed | Transcribing status disables competing sends. Landed text occupies only the editable draft; no transcript box. | Edit or send. |
| Microphone denied / unavailable | One specific hint; typed draft and typing remain available. No recording indicator. | Type, or retry microphone after permission changes. |
| Transcription failed | Error hint and original typed draft; no sent bubble. | Type or record again. |

These states introduce no alternate recipient or decision composer. Existing permission boundaries
remain authoritative. “Discuss” never grants implementation or merge authority.

## Smallest supporting architecture

The current `tasks.decisions()` filters on a blocked worker and `resume_after`; `resume()` clears
the block fields. A message can therefore remove an unanswered question today. Moving the link
alone is insufficient. The proposal separates a pending human question from worker execution
using the task’s existing durable record, conversation and events.

1. **One pending question on the task.** Give it a stable ID, conversation anchor, explicit question,
   recommendation and short rationale, source, and a revision. Keep unresolved/resolved state
   independent of running/blocked. Use existing task events/conversation for previous versions and
   outcomes. No new store, queue, conversation, daemon or lifecycle redesign.
2. **Publish the dilemma into the L2 thread.** A direct block records its question there. L3
   escalation records the actual escalated dilemma and recommendation as attributed context, not
   just a replacement block reason. Its referenced prior discussion and handoff are delivered to
   the owner’s next ordinary turn without waking it merely to announce an escalation. This context
   is sufficient even when the raw transcript has many later tool events.
3. **Use one resolution operation.** Quick acceptance and an L2-recorded conversational decision
   both name the question/revision and original operator message. Record the chosen approach,
   actor/source and time before continuing. The quick path writes a normal operator message;
   the conversational path cites the message already saved. Both keep a durable receipt. A worker
   resolution verb extends the existing task command surface and validates ownership/attempt,
   referenced message and its actual authority under the same rules as quick acceptance. A denied
   send creates no authorizing message. This uses the existing authority boundary, not a new user
   permission model, and does not parse model prose as a hidden command channel.
4. **Keep interpretation with the owner.** Deliver the pending-question context and operator text
   through the existing inbox and checkpoint/resume path. The same L2 answers, clarifies or records
   a clear decision. There is no extra classifier, provider call, conversation or engine setting.
   This is semantic judgment, not a claim that filesystem permissions distinguish research from
   implementation. Once resolved, the record makes the authorization and action inspectable.
5. **One read and write authority.** Overview and task responses project the same question record.
   Under the existing task/project lock, a stale question or recommendation revision cannot be
   accepted; a retry of a completed acceptance returns the saved receipt without a duplicate
   message or wake. Refresh both query views after a write and on navigation/poll. Revisions are
   required by the brief’s stale-action case, not a new general mutation framework.
6. **Preserve provenance and continuity.** A decision relayed through L3 cites the operator’s
   actual source message; L3-authored text stays attributed to L3. A coordinator resolution based
   on already recorded task authority names that basis rather than inventing operator approval.
   Unproven relays remain discussion. A generic resume cannot resolve a human question. Rejection
   or completion records a terminal disposition and removes its stale action. Existing merge holds
   still require their existing approval/release path. Ordinary wake retains the attempt’s engine,
   model and provider conversation; no reset, forced stop, cross-provider switch or second session.

The implementation will touch `tasks.py`, the existing task API/message path, decision data and
components, and task navigation. The worker-facing resolution command also needs a small
`bin/alt` change plus its CLI reference; those two paths need adding to this task’s lease after
design approval. Owner instructions can be supplied with the question handoff within the existing
task machinery; this proposal does not rewrite the global personas or unrelated lifecycle policy.

## What disappears

- The dedicated `Decision.tsx` page, its recommendation/options form, optional note field and
  L2/L3 recipient selector. Old `/decisions/:slug` links redirect to the owning task conversation.
- The competing timeline/evidence workspace. Task conversation and the existing live view hold
  those details, behind the normal disclosure/control.
- Mirrored follow-up threads on cards and the page, their per-card task/project-chat fetches, and
  timestamp-window guesses about which L2 reply belongs to which decision.
- “First option means recommendation” fallback. An accept button requires an explicit approach.
- Decision-page-specific follow-up instructions and routing, where no other caller needs them.
  Keep general task-linked project conversation references and the related clickable-link work.

The small additions are the question identity/anchor, a durable cited-message resolution and a
shared question component. Production line reduction is assessed when implementation replaces
the current machinery; this wireframe PR adds design artifacts only.

## Validation and checkpoint

`web/e2e/conversation-first-wireframes.pw.ts` opens the real generated HTML on a disposable
loopback server. It drives the scripted example inputs, acceptance, Back/Forward and evidence
disclosure, checks visible additions/removals and captures the main flow and every listed state at
390×844 and 1440×900, plus the main boards in dark. It validates a proposal, not backend behavior.
Run it with `pnpm --dir web ui conversation-first-wireframes.pw.ts`; evidence is under the normal
`web/ui-artifacts/results/` folder. `python3 design/wireframes/capture_conversation_first.py` copies
only this walkthrough’s fictional main-flow and state PNGs into the committed review gallery.
`make check` remains the PR gate.

After approval, update the main spec and behavior docs in this same task/PR, implement the accepted
design, and add deterministic API/storage tests for discussion, explicit decisions, source-role
provenance, duplicate/stale acceptance, escalation anchoring and worker continuity. Walk the real
app at both sizes using the existing harness. No live-provider validation. The implementation PR
remains held for the operator’s review and merge.

**Design decision requested:** approve this direction for implementation, or identify the changes
needed to the flow. The task blocks at this checkpoint because the brief explicitly requires the
operator’s wireframe review before production implementation.
