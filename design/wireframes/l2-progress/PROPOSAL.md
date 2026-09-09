# L2 activity and steering

**Operator approved — 2026-09-09.** This records the UX agreement for the full feature in
[issue #302](https://github.com/mburakyucel/altitude/issues/302), not a separate design delivery.
Task message `1e51deec5f994149a85048eb8035bac4` accepts both choices below. Implementation is
authorized; the delivered PR stays held for operator review and merge.

[Review phone and desktop](index.html) · [Interactive board](board.html) ·
[Current UI specification](../SPEC.md#310-task-page)

## Agreed experience

Keep Conversation and Live session. Place one compact area directly above the task composer:

- **Latest from L2:** the latest available public assistant text, verbatim after existing redaction.
  Two lines by default; **Expand** shows the rest. A newer update replaces it without adding a chat
  reply or moving the conversation. Questions, decisions and explicit replies/results keep their
  existing durable place in Conversation.
- A separate fact such as **Tool output observed · 8 sec ago** shows recorded activity. The sentence
  has its own age. After 60 seconds without observed output, use **Last update** and **No new activity
  for …** with a neutral dot. A quiet worker is not declared stuck or productive. No public text:
  **No public update yet.** Failed evidence read: **Activity unavailable**, with the last known
  update visibly labeled and a read Retry. Missing timestamps say **Time unavailable**; observing old
  history on page open never makes its prose “just now.”
- **View live session** opens the existing transcript. **Stop** remains directly reachable in both
  views on phone and desktop. It requests Stop immediately without a confirmation step. Reject
  keeps its confirmation. The preview is replaced rather than copied into durable conversation;
  earlier output remains available only under the existing session-record retention, with no new
  archive, summaries, periodic model calls, or extra instructions to make L2 repeat itself. Public
  assistant text can include a final message: clear the preview on actual completion, without
  guessing its phase from prose. Never promote it into another durable reply.

**Send** saves one steering message and labels it **Queued · waiting for a checkpoint**. It does
not interrupt a running worker. An evidenced handoff changes the label to **Delivered to session**;
this means supplied to the session, not proof the model understood or acted on it. Missing evidence
stays **Delivery unconfirmed**. Labels sit beneath the operator's existing bubble, not in a second
queue transcript. Acceptance by Altitude and delivery to the worker are distinct.

**Stop → Stopping… → Stopped.** Keep the draft editable, but prevent Send/Continue during an
unconfirmed stop. Do not report Stopped just because the task's state is blocked: wait for worker
termination evidence. A failure says **Stop unconfirmed · The worker may still be running** and
offers a status recheck. Existing file edits and completed external effects remain; Stop does not
undo them or suspend a tool midway for later instruction-by-instruction continuation.

After Stop, **Send a correction to continue this session** appears beneath the composer; the arrow
sends it and resumes. **Continue session** resumes without sending the unsent draft. Previously
queued messages remain held until one of these explicit actions and are delivered in original order,
followed by the new correction. There is no new queue editor or silent discard. Stop wins over an
older queued message or a send already in flight; a send overlapping the stop remains queued and
requires an explicit continuation after Stop completes. Another device must first observe that
stopped generation and explicitly send a correction or Continue; an ordinary send from a stale
running tab remains queued. “Waiting to resume” can precede “Running”; capacity does not become a false success.

The preview clears its old direction on resume and waits for new public output. Old words stay in
Live session. Stop, continuation, details and local view changes preserve the current task draft and
selection; leaving the task/reloading retains the existing draft policy. Unanswered questions stay
open through any operational wake. A blocked question replaces the activity area with the existing
question conversation. Finished tasks keep the result and remove the composer and Stop.

**Desktop Escape:** invokes Stop only when no dialog, recording, text input, composition,
menu or overlay owns the key. Escape in a draft does nothing to the worker; recording cancels,
dialog/menu/overlay closes first. The visible Stop is always the alternative. No phone keyboard
shortcut is required. A small Esc hint beside Stop advertises the shortcut on desktop.

## Recorded decisions

Ordinary Send queuing, saved-session continuity on both engines, accurate delivery evidence,
no summarizer calls and deferred live-provider validation are fixed task constraints. They do not
need another approval. The operator also accepted these two independent UX choices:

1. The replaceable two-line preview, separate activity/age, and existing session-record
   retention, with durable questions/results kept in Conversation.
2. One-click Stop beside the composer and in Live session on phone and desktop, holding
   queued messages until explicit continuation, and the scoped desktop Escape behavior above.
   **This agreement supersedes the mobile proposal to hide Stop on phone.**
   Reject and metadata may remain behind the header disclosure; the mobile task's other choices
   remain separate. Both question resolutions cite the operator's task message above.

## Both engines: verified feasibility and limits

Local source checked at `1e1f3ae957199f5e4985471f62e6fb41e86c39f4`; no live provider calls.
The same interface and common contract cover both configured engines. Provider differences remain
behind `altitude/engines.py`. Display names in production come from the seam.

| Existing integration | Evidence and implication |
| --- | --- |
| Foreground CLI workers, not App Server | `engines.py:1154` launches one CLI process in a transient worker unit. Input is supplied once. There is no shared live message-injection or native pause channel. A provider migration is unnecessary for this proposal. |
| Public output already exists | `transcript.py:223,264` normalizes assistant text and tool events, with reasoning removed and secrets redacted at `:72`. Reuse that public projection; provider parsing touched by this work moves behind the seam. |
| Checkpoints differ | One adapter supplies inbox context after a tool call or at its stop hook; the other waits for the current CLI turn to end and resumes its native thread (`tasks.py:97`, `hooks/inbox.py:19`, `dispatch.py:572`). Thus normal steering can wait through a long tool call or an entire turn; Stop remains the common immediate intervention. |
| Freshness is not uniform | Some source records have event timestamps; other stdout records are ordered at turn start without individual times (`transcript.py:312`). Use source time when present or **Observed … ago** for newly appended stable events after a known baseline. Untimed existing prose says **Time unavailable**, including after reconnect/restart; file modification time describes output activity, not the selected sentence's age. No-output and unreadable-output remain distinct. |
| Stop and continuity exist | `dispatch.py:886`, `engines.py:1238` stop the whole worker and descendants. Resume retains the engine, launch model, session and dirty worktree (`dispatch.py:803,836`, `engines.py:1217`). A replacement process resumes recorded history; unavailable/mismatched history is an explicit failure, not a fresh conversation disguised as continuity. |

Required fixes within this feature, before claiming these interactions work:

- **Reliable steering at turn completion.** The documented turn-boundary path is not established
  by the current complete application flow: an ordinary worker exit without a report can become
  `l2-died` before queued steering resumes (`dispatch.py:1140`, `server.py:698`). Existing
  `test_chat_queue.py:171` bypasses that path. Add a deterministic full completion-to-resume journey,
  preserving genuine errors and explicit question blocks.
- **Stop holds older messages.** The current inbox-only wake can resume an explicitly stopped
  task (`dispatch.py:896,927`). Bind the stop boundary to existing operation/message identities;
  a request predating Stop cannot restart the worker.
- **Delivery needs evidence.** A claim removes messages before provider launch; the hook consumes
  inbox before returning context (`tasks.py:217,286,323`). Neither absence nor a claim means Received.
  Correlate message IDs with confirmed session handoff and retain an unconfirmed state when proof
  is missing. Hook source attachments can corroborate the original message IDs; resumes require
  successful stdin handoff, expected session initialization and worker binding for the exact
  claimed batch. A swallowed broken pipe cannot establish handoff. Reuse current
  task/inbox/operation records. Existing `daemon_request` phases provide
  stop/resume receipts (`dispatch.py:130,272`); no second task lifecycle or policy system is needed.

## Interaction-state walkthrough contract

The static prototype illustrates these states at 390×844 and 1440×900. Its test advances fictional
observations explicitly; it does not prove backend behavior. Implementation uses the same state
walkthrough with disposable real storage/API and deterministic engine fixtures.

| State | Visible and removed; action/outcome |
| --- | --- |
| Running | Latest public text, its age, observed activity, Live session and Stop; no synthetic conversation reply. New output replaces the preview. |
| Loading / no commentary | Reading activity / No public update yet; no invented intent. Stop remains reachable from authoritative running state. |
| Quiet / unavailable | Old words and age remain clearly old; unavailable adds read Retry. No activity pulse or new timestamps from polling. |
| Queued / delivered / unconfirmed | One operator bubble gains its receipt. Claim, launch failure or missing proof cannot mark it delivered. Refresh never resends. |
| Stopping / stop error / denied | Draft retained and editable; Send cannot resume early. Stop operation completion, not blocked state alone, enables continuation. Failed/denied actions never claim success. |
| Stopped / waiting to resume / resumed | Stop disappears; Continue and correction available. Waiting describes capacity; new output replaces stale direction only after actual resume. |
| Blocked / finished | Existing question and discussion / durable result. Current activity removed. Finished also removes composer and Stop; outstanding question is never auto-accepted. |
| Draft / listening / denied / unavailable voice | Existing shared composer behavior, kept separate from worker Stop. Escape cancels recording first; dictation returns to the draft without another transcript box. |
| Live session / details / confirmation | Opening/closing preserves draft and selection. Live view has Stop. Dialog or overlay owns Escape before task interruption. |
| Short viewport | Preview folds to title, observed activity and controls; Expand reveals the public words. Header/composer/navigation retain their current layout. The optional compact-header study is explicitly pending elsewhere. |

Verification after agreement: both engine fixtures; redaction/reasoning exclusion; repeated and
missing/partial/corrupt records; quiet output; session replacement; claimed versus handed-off
messages; exact batches and late arrivals; stop/send/completion races; retry and duplicate Stop;
same-session resume with dirty edits; refused late sends after finish/reject; questions and merge
holds surviving wakes. Run `make check`, all required CI, and phone/desktop UI walkthroughs.
Live-provider and real-phone compatibility remain outside deterministic evidence under the existing
testing decision; operator UX acceptance still gates delivery and merge.

## Coordination and delivery

The `redesign-mobile-chat-to-give-messages-mo` proposal is still pending, including moving Stop/Reject
into a header disclosure. These boards preserve the current shell and include its compact-header
study only as an optional comparison. This task's accepted steering decision supersedes
hiding Stop on phone: Stop remains directly accessible beside the composer and in Live session.
Reject and metadata may remain behind the header disclosure. This decision does not approve that
task's other header, composer, keyboard navigation, or hold presentation choices. L3 coordinates
the shared outcome and landing order. L3 background FYIs
remain in their own task.

[PR #300](https://github.com/mburakyucel/altitude/pull/300) is open at this checkpoint. Preserve its
saved-message receipts, uncertain-send handling and draft recovery; rebase/reconcile its final
delivery before implementation landing. CI repair stays with its current owner.

Implement this as one feature, then update README, architecture, lifecycle and the
agreed SPEC in the same held PR. No scope is deferred and no native provider migration is proposed.
The current lease covers the anticipated implementation; if hook changes require `hooks/inbox.py`,
request that exact path from L3 before editing. Issue #302 stays open during proposal and pending
operator acceptance. Closing linkage belongs only to a delivery that satisfies the full accepted scope.

These new proposal files are hand-authored and do not replace the generated approved boards.

Review evidence: all four executions of `web/e2e/l2-progress-proposal.pw.ts` pass (two scenarios
at each viewport); `git diff --check` passes. Eight primary captures are in `captures/`, with named
supplemental states under `web/ui-artifacts/results/l2-progress-proposal*`. Regenerate the primary
captures with `CAPTURE_L2_PROPOSAL=1 pnpm --dir web ui l2-progress-proposal.pw.ts`. Independent
read-only review checked the integration feasibility and proposal; its message-delivery and
stop/continue wording findings are corrected. Full application checks remain for implementation.
