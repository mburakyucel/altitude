# Conversation-first decisions

Burak settled this UX on 2026-09-08: support one question and a small group of independent
questions in the owning L2 chat. The model chooses a plain question, one recommended quick action,
or two to three quick options with one recommendation.

[Current boards](index.html) ·
[Current specification](SPEC.md#38-decision-card)

## The experience

Needs you is a compact list of unanswered questions, grouped by dilemma/task. Opening an item goes
to the relevant L2 discussion, including the question, recommendation and context brought through
L3. It never guesses that the last technical event is the question. The list and conversation use
the same question component. User bubbles and assistant messages remain prominent; technical
activity and evidence are available through the existing disclosure and live session.

The default question shows the task's purpose and actual choice with only the context and material
consequences needed to decide. Owners write concise plain-language questions and actionable
options; complete titles and questions remain readable rather than clipped. Detailed reasoning,
implementation terms and history stay in the owning conversation, with extra saved question detail
available through **More context** when it differs from the question. The task's title has its own
fully wrapping link above the question. Brevity never hides a consequence needed before answering.

The operator approved the Work separation on 2026-09-10 Pacific. Work shows each current project
task once as a compact status row, including tasks awaiting an answer. A waiting row opens this
same question in context; Work repeats no question body or quick-answer controls. Running, queued,
L3-waiting and reported tasks stay visible, with recent completed tasks under **Done this week**.
An open question stays visible independently of worker state. Partial answers update the row's
question count; a final answer leaves the row showing observed execution or waiting status.

Only global Needs you has a numeric attention badge. Project rail and switcher rows retain state
dots. The badge counts unanswered operator questions plus operational attention items, labelled
separately in summaries. Unknown reads never imply zero; cached failures identify saved status as
stale. On phone, the global badge is in bottom navigation, or its header link while keyboard use
hides that navigation. Back returns to the originating Work or Needs you view.

A single question's quick choices act immediately. For up to three independent questions, choices
start unselected. Pick answers, then **Send N answers** once. With no manual picks, **Use
recommendations** answers the members that have an explicit recommendation. It never overwrites a
picked alternative or answers a plain question. You can send fewer answers and leave the rest open.
Only actual picks receive selection styling. When one question remains, its choices act immediately
in both Needs you and chat. Longer groups scroll within the phone conversation.

The normal composer is always available on an active conversation: answer one or several questions,
propose an alternative, or ask a follow-up. Sending text only saves and delivers a message. The same
L2 interprets it in context, records an explicit decision against that message, or clarifies actual
ambiguity. There is no extra approval phrase, recipient selector, note form or blanket confirmation.
A follow-up can wake the L2 to answer but does not approve implementation of the disputed approach.

Answered members disappear from Needs you and retain their receipts in chat. Partially answered
scope leaves only the relevant remainder open. A new direction closes questions it makes
unnecessary with a short reason, without accepting their abandoned recommendations. Completion,
rejection and report handoff remove obsolete controls; the existing merge hold remains separate.

## Decision examples

| Example | Desktop | Phone |
| --- | --- | --- |
| Questions upfront in Needs you | [Open](ConversationFirstNeedsYou.html) | [Open](MobileConversationFirstNeedsYou.html) |
| One question with immediate quick choices | [Open](ConversationFirstQuestion.html) | [Open](MobileConversationFirstQuestion.html) |
| Grouped picks and one send | [Open](ConversationFirstGroup.html) | [Open](MobileConversationFirstGroup.html) |
| Follow-up exchange leaves questions open | [Open](ConversationFirstFollowup.html) | [Open](MobileConversationFirstFollowup.html) |
| Partial answer and irrelevant-question closure | [Open](ConversationFirstPartial.html) | [Open](MobileConversationFirstPartial.html) |
| Answers recorded and work resumed | [Open](ConversationFirstAccepted.html) | [Open](MobileConversationFirstAccepted.html) |

All content is fictional. The prototype demonstrates typed answers and follow-ups with deterministic
examples; the production L2 uses its ordinary judgment. Try “Could we roll back after day seven?”,
“Keep 14 days; use snapshots so region no longer matters.”, then “Release team”. A single question
also accepts “14 days”. The shared appendix covers input and recovery. Its captures and the real
application walkthroughs stay in ignored `web/ui-artifacts/`; see [development](../../docs/DEVELOPMENT.md#browser-walkthroughs).

## Navigation and states

| Situation | Visible behavior |
| --- | --- |
| Open any member | Focus the group's stable discussion anchor with preceding explanation. Back returns to the originating Needs you or project tab. |
| Later activity arrives | Keep the reading position; **Latest messages** follows the bottom and **View question** returns to an offscreen question. |
| Phone Conversation/Live switch | Replace the same history entry and preserve its draft. Leaving the task clears the draft. |
| Initial loading or read failure | Skeleton then Retry; no inferred count, enabled decision or writable composer from an unknown read. |
| Cached read failure | Retain saved content, show refresh notice and disable writing until a successful read. |
| Needs you empty | No cards or zero badge; calm **Nothing needs you.** message. |
| Recording choices | Disable actions and show **Recording…**; save receipts before removing answered members. |
| Choice write fails | Keep questions and explicit selections, show Retry. A lost response retries the same saved decision, without duplicate delivery. |
| Stale group/option | Reject the whole submission. Refresh current choices and clear old staged picks; never retarget an old click. |
| Send fails | Keep the unsent text and Retry in the familiar composer; no false saved answer. |
| Access denied | Disable sending and deciding together; explicit Refresh after access is restored. |
| Voice | Existing listening, cancel/stop, transcribing, unavailable and failure controls; sent text leaves no extra transcript box. |
| Discussion | Saved user message and L2 reply; unanswered members and Needs you count remain. |
| Decision saved, worker waiting | Saved receipt plus waiting status. **Work resumed** requires observed running state. |
| Resolved elsewhere or historical link | Readable receipt and reason, no obsolete action; link to a current revision if present. |
| Superseded versions | Fold under **Earlier question** so the current group stays prominent. An old-version link opens its exact history automatically. |
| Missing question or archived task | Explicit missing-question notice with ordinary conversation, or archived read-only history with no composer. |

<details>
<summary>Open the shared input and recovery states</summary>

These links select states in the same maintained desktop and phone boards.

| State | Desktop | Phone |
| --- | --- | --- |
| Loading Needs you | [Open](ConversationFirstStates.html?state=list-loading&reset) | [Open](MobileConversationFirstStates.html?state=list-loading&reset) |
| Needs you read failed | [Open](ConversationFirstStates.html?state=list-error&reset) | [Open](MobileConversationFirstStates.html?state=list-error&reset) |
| Cached Needs you, offline | [Open](ConversationFirstStates.html?state=list-offline&reset) | [Open](MobileConversationFirstStates.html?state=list-offline&reset) |
| Needs you during discussion | [Open](ConversationFirstStates.html?state=discussion&reset) | [Open](MobileConversationFirstStates.html?state=discussion&reset) |
| Loading the question | [Open](ConversationFirstStates.html?state=loading&reset) | [Open](MobileConversationFirstStates.html?state=loading&reset) |
| Read failed, retry | [Open](ConversationFirstStates.html?state=read-error&reset) | [Open](MobileConversationFirstStates.html?state=read-error&reset) |
| Offline with cached discussion | [Open](ConversationFirstStates.html?state=cached-error&reset) | [Open](MobileConversationFirstStates.html?state=cached-error&reset) |
| Recording quick acceptance | [Open](ConversationFirstStates.html?state=accepting&reset) | [Open](MobileConversationFirstStates.html?state=accepting&reset) |
| Acceptance failed | [Open](ConversationFirstStates.html?state=accept-error&reset) | [Open](MobileConversationFirstStates.html?state=accept-error&reset) |
| Acceptance denied | [Open](ConversationFirstStates.html?state=denied&reset) | [Open](MobileConversationFirstStates.html?state=denied&reset) |
| Sending a message | [Open](ConversationFirstStates.html?state=sending&reset) | [Open](MobileConversationFirstStates.html?state=sending&reset) |
| Message failed, draft retained | [Open](ConversationFirstStates.html?state=send-error&reset) | [Open](MobileConversationFirstStates.html?state=send-error&reset) |
| Message queued, L2 unavailable | [Open](ConversationFirstStates.html?state=waiting&reset) | [Open](MobileConversationFirstStates.html?state=waiting&reset) |
| L2 could not answer | [Open](ConversationFirstStates.html?state=reply-error&reset) | [Open](MobileConversationFirstStates.html?state=reply-error&reset) |
| Decision recorded, waiting to resume | [Open](ConversationFirstStates.html?state=accepted-waiting&reset) | [Open](MobileConversationFirstStates.html?state=accepted-waiting&reset) |
| Question without a recommendation | [Open](ConversationFirstStates.html?state=no-recommendation&reset) | [Open](MobileConversationFirstStates.html?state=no-recommendation&reset) |
| A simple typed answer | [Open](ConversationFirstStates.html?state=simple-input&reset) | [Open](MobileConversationFirstStates.html?state=simple-input&reset) |
| A reply arrives below the question | [Open](ConversationFirstStates.html?state=new-reply&reset) | [Open](MobileConversationFirstStates.html?state=new-reply&reset) |
| A newer question replaced this one | [Open](ConversationFirstStates.html?state=revised&reset) | [Open](MobileConversationFirstStates.html?state=revised&reset) |
| Question unavailable | [Open](ConversationFirstStates.html?state=missing&reset) | [Open](MobileConversationFirstStates.html?state=missing&reset) |
| Task archived, read only | [Open](ConversationFirstStates.html?state=archived&reset) | [Open](MobileConversationFirstStates.html?state=archived&reset) |
| Listening | [Open](ConversationFirstStates.html?state=listening&reset) | [Open](MobileConversationFirstStates.html?state=listening&reset) |
| Transcribing | [Open](ConversationFirstStates.html?state=transcribing&reset) | [Open](MobileConversationFirstStates.html?state=transcribing&reset) |
| Dictation in editable draft | [Open](ConversationFirstStates.html?state=dictated&reset) | [Open](MobileConversationFirstStates.html?state=dictated&reset) |
| Microphone denied | [Open](ConversationFirstStates.html?state=mic-denied&reset) | [Open](MobileConversationFirstStates.html?state=mic-denied&reset) |
| Transcription failed | [Open](ConversationFirstStates.html?state=voice-error&reset) | [Open](MobileConversationFirstStates.html?state=voice-error&reset) |
| Voice unavailable | [Open](ConversationFirstStates.html?state=voice-unavailable&reset) | [Open](MobileConversationFirstStates.html?state=voice-unavailable&reset) |

</details>

## Supporting machinery and removal

Individual question revisions stay in the existing task record. A small group stores member IDs,
revision and one stable conversation anchor. Overview, project and task views project the same
records; question state is independent of worker execution. Publication from L2 or L3 includes the
actual dilemma, explicit choices and source attribution in the owning conversation.

Quick submissions name exact member revisions and option keys. A group submission validates all
selected members under the existing project lock, then saves one normal operator message and its
receipts atomically. Identical retries retain the receipt and return current group state. Typed
replies retain viewed question references; the owning L2 cites the original message to resolve
answered or irrelevant scope. Its task/attempt and source authority are checked at the existing CLI
boundary. A coordinator relay cannot impersonate operator approval.

Existing inbox, wake, capacity and provider conversation rules deliver both kinds of answer. A
queued fresh attempt still receives the current questions or receipts. There is no second model
classifier, decision store, queue, conversation, daemon or provider reset.

Saved decision URLs redirect into the owning chat. Loading and input behavior reuse the familiar
shared components.

The process learning is recorded in [issue #271](https://github.com/mburakyucel/altitude/issues/271)
and the [project UX rules](../../AGENTS.md#ui): settle a major UX proposal with the user before finalizing
implementation and migrating tests. Broad positive feedback does not settle ongoing UX questions.
