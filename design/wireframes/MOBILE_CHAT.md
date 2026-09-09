# Compact mobile chat

**Approved by the operator, 2026-09-09.** [Compare the layouts and task-status examples](mobile-chat/index.html).
Task message `3ad98be02f3647bea2aa32bf38cb3970` approves this reviewed design, including the
blocked/merge-held examples. The resulting implementation PR remains held for operator review.

Captured comparisons: [Reading](mobile-chat/captures/comparison-read.png) ·
[Typing](mobile-chat/captures/comparison-type.png) · [Busy + queued](mobile-chat/captures/comparison-busy.png) ·
[Update + error](mobile-chat/captures/comparison-error.png) ·
[L2 reading](mobile-chat/captures/comparison-task.png) · [L2 typing](mobile-chat/captures/comparison-task-type.png).

The operator asked for more message space, calling out the last-answer section, the row below the
textbox, and possibly hiding bottom navigation during keyboard use. The approved design combines the phone
headers, puts text and voice/send controls on one row, and hides the bottom navigation only while
the software keyboard occupies the viewport. It keeps readable text and increases the composer
buttons from 40px to 44px. Both L3 and L2 use the same composer and shell behavior.

## Illustrative measured space

| Like-for-like scene at 390×844 | Current messages | Proposed messages | Recovered |
| --- | ---: | ---: | ---: |
| L3 reading | 515px | 636px | **121px** |
| L3 typing, 334px keyboard | 181px | 386px | **205px** |
| L3 busy with queued message, keyboard | 181px | 386px | **205px** |
| Update ready + refused send, keyboard | 47.5px | 314px | **266.5px** |
| L2 reading | 424px | 592px | **168px** |
| L2 typing, keyboard | 90px | 342px | **252px** |

These compare the captured baseline app with the reviewed HTML/CSS design, not the implemented app.
They are the message scroll container's measured heights, not a count of readable lines. Both
sides include 20px vertical message padding; subtracting it from each leaves the same gain.
Queued messages and active-turn indicators occupy space inside that region. The proposal gives
the queue a clearer label and 44px Remove target, so its message-row height is not identical.

The baseline is the actual app at `a43f0ce24f153968745cc789433197e3d38b1cdf`, built and captured
with the repository's disposable acceptance service, fictional messages, deterministic overlays,
and bundled Chromium. The proposal is rendered HTML/CSS, with the same message text and 16px
message/input text. At 390×844 the current stack is 54px phone header + 57px project status row +
134px composer dock + 84px navigation. The proposed stack is 54px header + 70px dock + 84px
navigation; typing removes those 84px. L2 retains a 44px local tab row and folds its measured
103px metadata section (the current tab row also has a 1px border).

The update example preserves a visible Restart action and restored unsent draft on both sides.
The current 133.5px update banner becomes a 48px summary with Details and Restart. The proposed
send-error hint adds 24px. Longer actionable copy wraps and may need more height: these are measured
examples, not a guarantee for every task, language or font setting.

Current screenshots are under [mobile-chat/captures](mobile-chat/captures/); the interactive
proposal is [MobileChatProposal.html](MobileChatProposal.html). No operating-system chrome or safe
area is included in this CSS viewport measurement. The keyboard is a **334px illustration**, with
a simulated visual viewport of 510px. This does not establish native Safari/Android keyboard,
toolbar or scroll behavior. Real phone verification remains necessary during operator acceptance.

## Blocked and merge-held tasks

The operator's follow-up, relayed through L3 on 2026-09-08, explicitly includes the blocked notice
and merge-hold message above L2 chat. Moving the **hold reason into task details** and keeping a
compact task state were already proposed. The first review did **not** draw the blocked/merge-held
combinations, long reasons, or their expansion. The additional reviewed examples cover those gaps.
The operator's 2026-09-09 approval authorizes their implementation and retains the merge hold.

| Example | Compact status | Full reason and actions |
| --- | --- | --- |
| Running + merge held | **L2 · Running · Merge held** | Details shows the entire merge restriction. Stop/Reject keep their existing availability. A hold does not block sending a message. |
| Waiting on L3 | **L2 · Waits for L3** | The original L3 question stays in the conversation. Details shows its complete reason without another permanent paragraph above chat. An L3 question has no operator-answer badge. |
| Waiting on L3 + merge held | **L2 · Waits for L3 · Merge held** | Details separates **Waits for L3** and **Merge held**, each with its own complete reason. Neither state replaces the other. |
| Operator question + merge held | **L2 · Needs your answer · Merge held** | The original plain question stays in chat. **View question** reaches its anchor from offscreen or from details; ordinary replies remain available. There is no generic Resume while a question is open. |
| Fault + merge held | **L2 · Blocked by a fault · Merge held** | A visible notice names the failed verification browser and says **L3 has been told**. Details shows the full fault and merge reasons separately. Existing allowed Resume/Reject controls remain available. |
| Operational pause, no question | **L2 · Paused** | Full pause reason in details; the existing Resume/Reject actions remain available. Resume records no design decision. |

Phone status uses the existing 54px proposed header. Long reasons wrap in the scrollable details
sheet above the keyboard, with no truncation of their text. The title and ⋯ both open details.
The task's local tabs remain visible. On desktop the same compact status and reason disclosure
coexist with the rail, metadata, ordinary header actions, live-session control and composer hint;
desktop operational actions remain directly accessible. The desktop example also removes the
current long merge chip's horizontal clipping. Both desktop comparison views have the live panel
closed; its existing control remains available. No hold-release control is introduced.

Opening/closing either reason preserves the draft, selection and conversation position. Following
**View question** deliberately moves to its existing anchor; **Latest messages** returns to the
bottom. When present, the proposed question-jump control takes 44px and is included below. A
pending question stays open after reading details or drafting a reply. Whether an actual reply
settles it remains the existing owner/source-message decision; it never releases a merge hold.

| Long-reason example | Phone reading: current → proposed | Phone keyboard: current → proposed |
| --- | ---: | ---: |
| Running + merge held | 323.5 → 592px | 20 → 342px |
| Waiting on L3 | 338 → 592px | 20 → 298px |
| Waiting on L3 + merge held | 257 → 592px | 20 → 298px |
| Operator question + merge held | 343 → 592px | 20 → 298px |
| Fault + merge held | 257 → 543px | 20 → 293px |
| Operational pause | 377 → 592px | 43 → 342px |

Measurements use the same source build and fictional history as the initial comparisons, plus
the same long reasons and anchored question on both sides. The current keyboard cases hit the
20px minimum scroll-region height: its padding consumes that space, and overflowing chrome
overlaps the composer. Those 20px do not represent readable message text. In the proposed
blocked-plus-held case the recovered region is **335px while reading and 278px while typing**,
including its 44px question-jump control while typing. The fault notice remains 49px high.
The viewer includes the actual desktop baselines and proposed 1440×900 board as well; these are
layout examples, not native keyboard or production-implementation evidence.

`web/e2e/mobile-chat-proposal.pw.ts` uses `walkthrough.ts` against static fictional boards on an
OS-assigned loopback server. At 390×844 it walks all six cases collapsed/expanded/restored with
the keyboard closed and simulated open; at 1440×900 it walks the corresponding desktop states.
Named screenshots prove long reasons can be read, sheets fit the available viewport, question
navigation and existing action availability persist, and keyboard dismissal retains draft/focus/
selection without removing the hold. Artifacts are under `web/ui-artifacts/results/mobile-chat-proposal*`.
These proposal checks supplement the full application walkthroughs and `make check` required
for implementation; static board checks establish no API effects or native keyboard behavior.

## Approved behavior

| Surface | Stays visible | Expands on request / disappears |
| --- | --- | --- |
| L3 header | Project switcher, L3 Ready/Answering/Handling status, details button | Last answer age, engine/model, task counts and existing actions open in the details sheet. Routine status no longer has its own row. |
| Engine selection | A non-Auto pin stays named in the header status | Auto/engine selector moves to project details. The existing pin semantics stay the same. |
| L2 header | Back, title, L2 state and separate Merge held status; Conversation/Live session tabs | Full title, attempt/context/tokens, PR/checks, complete block/hold reasons, Stop/Reject/Resume and their existing confirmations open in phone task details. A fault or failed action remains an explicit visible notice. Desktop retains direct header actions. |
| Decisions | Existing question and quick actions at their conversation anchor; Latest messages/View question when applicable | No decision is inferred from a reply, a wake, or a generic resume. While navigation is hidden, its existing nonzero Needs you count is reachable in the header. No badge is added at zero. |
| Composer | Editable text, microphone and accent send arrow | The engine toolbar and routine hint consume no separate row on phone. Error, voice, access-denied and unavailable explanations appear only while relevant. |
| Navigation | Four existing tabs when keyboard is closed; task local tabs always | Bottom tabs leave only during detected software keyboard use. They return on keyboard dismissal, including while the field retains focus. |
| Update banner | Compact summary and Restart whenever the existing API allows it | Changed area, file count, age and wait reason open under Details. During restart the action leaves; successful activation removes the banner. Failure stays explicit with the existing controls. |

Project switcher, details and confirmations use the existing sheet/overlay pattern: labelled
dialog, focus contained, Escape/outside dismissal where currently allowed, and focus restored to
the opener. Task confirmations keep their existing copy and safeguards. A sheet is deliberate
temporary coverage of the conversation, not another permanent row. Long names may truncate in the
header; the complete title/name is accessible and shown in the disclosure. The screenshot's
Needs you variant illustrates a nonzero count; the like-for-like fixtures have none.
The sheet is bounded by the currently usable visual viewport, above any keyboard; it never draws
over the operating-system keyboard. If moving focus into the sheet dismisses the native keyboard,
the shell and navigation follow the resulting viewport change. The prototype demonstrates the
more constrained case, with the illustrative keyboard still present.

### Keyboard, draft and scrolling

- The shell follows the visual viewport height and offset. Keyboard detection combines an editable
  field with a substantial viewport contraction; focus alone does not hide navigation. Browser
  toolbar motion and pinch zoom must not be treated as a keyboard. A hardware keyboard leaves tabs
  visible. If the browser cannot identify a reduced viewport, retain reachable navigation rather
  than guessing. Implementation tunes detection against the existing viewport owner.
- Keyboard dismissal restores navigation with the draft and selection intact, even if the textarea
  stays focused. Dismissal does not send, clear or blur the draft as a product rule. Navigating to
  another project/task keeps the existing rule that leaving clears the unsent draft.
- A draft starts at one 44px input row. It grows to a maximum of 120px or 25% of the usable visual
  viewport, whichever is smaller, with a 44px minimum; it then scrolls internally. At 510px this is
  120px, leaving 310px for L3 messages with a long draft. Mic and send stay at the bottom of the
  field. Native textarea editing and selection remain available.
- At the bottom, maintain the newest message above the composer while the keyboard opens/closes,
  a draft grows or a reply streams. While reading older messages, retain the same visible message
  and its offset when the available height changes; never jump to the newest reply on resize.
  Sending explicitly resumes following. Keep the existing anchored-question context and jump
  controls, including their additional space when visible.
- Preserve browser-managed safe areas and existing bottom inset handling. With navigation hidden,
  the composer owns any applicable bottom inset once; do not leave the navigation's reserved
  26px gap above the keyboard. No new full-screen/PWA or viewport-meta behavior is proposed.

### Input and recovery states

| State | Appearance and transition |
| --- | --- |
| Empty / loading | Existing empty explanation or prose-shaped skeleton within the larger message area; disabled writing while the authoritative task read is unavailable. |
| Typing / sending | Same arrow; immediate 60% pending bubble and cleared field. Acceptance makes it solid; refusal restores the draft with the visible send-error hint. |
| Streaming / queued | Header names current work; the reply/typing indicator stays in the conversation. Queue remains ordered, with runs-next text and Remove on each removable message. Arrow accessible name is Queue while busy. |
| Listening | One row with Cancel, waveform/timer, Stop and Send, all targets at least 44px. Draft is retained. Status is announced and explained only during voice use. |
| Transcribing | Mic/send disabled, editable draft retained, visible Transcribing status; Cancel/Stop leave. Stop lands text in the ordinary draft; Send transcribes and uses the ordinary send/queue path. |
| Landed / cancelled | Transcript appends to the draft with no second transcript box. Cancel returns to the prior draft. Sending removes the draft and voice hint. |
| Microphone denied / unavailable | Mic disabled or hidden as today; concise reason says typing works. HTTPS/service errors stay explicit when relevant. No provider/service requirements change. |
| Conversation denied / read or write error | Keep the existing explicit explanation and Refresh/Retry, disable the same writes, retain readable cached content and unsent text. Never hide an actionable failure under generic details. |
| Done / rejected | Existing read-only history, composer absent. Header details still contain final delivery evidence. |

Keep 16px phone fields and message text, current contrast tokens, visible focus, named icon controls,
polite status announcements and reduced-motion behavior. Decorative dots never supply the only
status. Hidden navigation is removed from keyboard and screen-reader traversal. Zoom and large
text may wrap notices; reading and sheet content remain scrollable. Desktop keeps its rail, panel,
header metadata and useful shortcut hints; it uses the same composer states and semantics.

## Recorded decision and implementation verification

The operator approved this layout, including **hiding bottom navigation during software-keyboard
use** and **moving engine/task metadata and Stop/Reject behind details**.
The tradeoff is one extra tap to inspect or operate the task and dismissing the keyboard to switch
bottom tabs. The task's local view tabs stay visible. The added blocked/merge-held cases are part
of this same approved design. Approval of the implementation approach does not release the merge hold.

SPEC §2.2/3.2/3.3/3.6/3.10/3.13/4.2 and the matching boards describe this behavior. The implementation
ships README, architecture, lifecycle and Unreleased notes where applicable. Verification walks
phone and desktop empty/loading/typing/sending/streaming/queued/error,
voice/denied, details/actions, long drafts, older reading, anchored questions and keyboard restoration
with named screenshots via `walkthrough.ts`, then run `make check`. Browser assertions establish
layout and deterministic application transitions; a real phone checks the native keyboard limit.
The resulting green PR stays open for operator review and merge. No backend or decision redesign,
service operation, issue creation or unrelated navigation change is included.
