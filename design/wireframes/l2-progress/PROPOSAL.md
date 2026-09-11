# L2 activity and steering agreement

The operator approved the full experience for [issue #302](https://github.com/mburakyucel/altitude/issues/302)
on 2026-09-09 in task message `1e51deec5f994149a85048eb8035bac4`. The delivered PR remains held for
operator review and merge. The agreement covers both configured engines through one interface.

Current examples live in the maintained [desktop task](../Task.html), [phone conversation](../MobileTask.html),
[phone Live session](../MobileTaskLive.html) and [interaction states](../TaskStates.html) boards.
[SPEC §3.10](../SPEC.md#310-task-page) specifies the complete behavior.

## Agreed behavior

- One replaceable two-line preview above the composer shows existing public worker words verbatim
  after redaction. Expand reveals the full update. Prose age and observed activity age stay separate;
  60 seconds without output is quiet. Missing, untimed and unavailable evidence remain explicit.
- Questions, decisions and explicit replies/results remain durable in Conversation. Older worker
  output stays in Live session under existing retention. No duplicate replies, preview archive,
  hidden reasoning, summarizer calls or instructions to generate redundant updates are introduced.
- Send queues steering without interrupting. The original bubble shows queued, delivered to session
  or delivery unconfirmed. Delivery means evidenced handoff, not proof of understanding or action.
- One-click Stop stays beside the composer and in Live session on phone and desktop. This decision
  supersedes hiding Stop in phone details; metadata and Reject retain the compact mobile disclosure.
  Desktop Escape stops only when no input, composition, recording, dialog, menu or overlay owns it.
- Stopping preserves an editable draft and disables Send/Continue until termination is evidenced.
  Stop unconfirmed offers a status recheck. Stop retains existing edits and completed effects.
- After confirmed Stop, Continue resumes the saved session without sending the draft. Sending a
  correction resumes with earlier held messages in order, followed by that correction. Stop holds
  racing sends until an explicit continuation observes that Stop. Capacity waits remain visible.
- Resume clears old direction until new public output arrives. View changes preserve the draft and
  selection. Questions stay open until resolved; finished tasks remove activity, composer and Stop.

## Integration limits and evidence

Both integrations run foreground CLI workers. Their existing checkpoints differ: one can attach
inbox context at tool/stop hooks, while the other resumes its native thread after the current CLI
turn ends. Ordinary steering can therefore wait through a long call or turn. Stop terminates the
worker and descendants; continuation starts a replacement process against the saved session,
not a suspended tool instruction. Missing or mismatched session history fails explicitly.

Source timestamps are used only when identifiable. Newly observed records can establish output
activity; opening old history cannot make its prose current. Provider parsing and launch differences
stay at the engine seam, without a provider migration or separate authority contract.

`web/e2e/l2-progress.pw.ts` and deterministic engine integration tests establish application behavior,
message/Stop races, delivery evidence and saved-session continuity. Required `make check` runs on the
exact landing candidate. Live-provider validation remains deferred; deterministic fixtures do not
establish native provider or real-phone compatibility. Review captures stay in ignored artifacts;
the original proposal captures remain preserved in the task's `review-captures/` folder outside Git.
