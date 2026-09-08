# Conversation-first decisions: implementation review

Six examples from the running application, with fictional tasks and deterministic L2 replies.
The application, API, storage, question resolution and session-resume path are real. No live-provider
test calls are involved. PR #264 is held for the operator's review and merge.

The [approved design](../../CONVERSATION_FIRST.md) describes the behavior. The remaining loading,
empty, failed, denied, stale, voice and navigation cases are covered in the normal Playwright artifacts,
primarily `web/e2e/conversation-decisions.pw.ts`, `task-page.pw.ts` and `task-lifecycle.pw.ts`.

## Needs you: accept or open the L2 conversation

![Desktop Needs you](desktop-needs-you.png)
<img src="phone-needs-you.png" width="390" alt="Phone Needs you">

## One question: choose an explicit answer immediately

![Desktop quick choices](desktop-single.png)
<img src="phone-single.png" width="390" alt="Phone quick choices">

## Three questions: pick answers together, then send once

Nothing is preselected. Picking an alternative replaces the group recommendation action with
`Send N answers`; only those picks are submitted.
Longer groups scroll inside the phone conversation; the initial view starts at their first question.

![Desktop grouped questions](desktop-group.png)
<img src="phone-group.png" width="390" alt="Phone grouped questions">

## Follow up: the recommendation remains unaccepted

![Desktop follow-up](desktop-followup.png)
<img src="phone-followup.png" width="390" alt="Phone follow-up">

## Partial answers: keep the choice already made and ask only what remains

Fourteen days was chosen explicitly, then the remaining region recommendation was accepted.
The plain report-owner question remains open. Needs you shows only that remaining question;
chat retains the receipts for the earlier answers.

![Desktop partial answers](desktop-partial.png)
<img src="phone-partial.png" width="390" alt="Phone partial answers">

## Answer the remaining question in chat and continue

The typed report-owner answer closes after the L2 records its meaning against the original message.
The same walkthrough also checks typed answers covering several questions and closing a question
made irrelevant by a changed direction.

![Desktop recorded answers](desktop-accepted.png)
<img src="phone-accepted.png" width="390" alt="Phone recorded answers">

Reproduce with `make check`, then `python3 design/wireframes/capture_conversation_first.py --implementation`.
