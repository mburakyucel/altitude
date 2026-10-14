# Improvement suggestions eval scenarios

Fictional projects with known friction, used by `altitude/improve_eval.py` to measure the improvement
review before it reads any real conversation ([design](../../../design/wireframes/improvement-review/PROPOSAL.md#proving-it-works)).
Everything here is fiction and public: invented project names, people-free roles, no real paths,
hosts, accounts or quotes.

`develop/` holds the scenarios used while writing the reviewer's prompt; `heldback/` holds the ones
run once for the reported numbers. Each has one clean project, one decoys-only project and three
planted ones. `scale/` holds projects at a real project's volume: ordinary pushes, continued reports,
second reviews and long conversations at the rates real tasks show, so prompts overflow and signals
fire on ordinary work; two are planted and one is a control. Each scenario is a pair: `<name>.json`
(the records) and `<name>.key.json` (the answer key, never shown to the reviewer).

`signals.json` is the offline eval's project: one task per signal kind beside ordinary activity that
must not count, and one long task whose sheet must still quote the operator. Its key lists, per
signal, the places that must fire (`fire`), the places that must fire nothing (`quiet`) and the tasks
whose sheet must quote the operator (`quoted`).

## Scenario file

```json
{
  "project": "lumen",
  "now": "2026-10-19T08:00:00+00:00",
  "tasks": [
    {
      "slug": "fix-safari-voice",
      "title": "Fix voice input on Safari",
      "state": "done",
      "created": "2026-10-06T09:00:00+00:00",
      "reported": "2026-10-06T15:00:00+00:00",
      "finished": "2026-10-06T16:00:00+00:00",
      "verdict": "delivered",
      "tokens": 2400000,
      "messages": [
        {"id": "m1", "at": "2026-10-06T09:05:00+00:00", "role": "l2", "text": "..."},
        {"id": "m2", "at": "2026-10-06T09:20:00+00:00", "role": "operator", "text": "..."}
      ],
      "questions": [
        {"id": "q1", "revision": 1, "asked": "...", "audience": "operator", "text": "...",
         "asked_again": false,
         "resolution": {"disposition": "answered", "at": "...", "by": "operator", "message": "m2"}}
      ],
      "events": [{"id": "e1", "at": "...", "kind": "ci-recheck", "reason": "..."}],
      "reviews": [
        {"id": "r1", "subject": "changes", "requested": "...",
         "findings": [{"id": "F1", "severity": "medium", "title": "..."}],
         "dispositions": [{"finding_id": "F1", "disposition": "fixed", "reason": "..."}]}
      ],
      "report_review": [{"summary": "...", "severity": "low", "disposition": "open", "reason": "..."}]
    }
  ],
  "current": [{"slug": "export-work-list", "title": "Export the work list", "state": "running"}],
  "chat": [{"id": "c1", "at": "...", "role": "operator", "text": "...", "turn": "t1"}],
  "earlier": [{"title": "Owners re-run reviews after a clean rebase", "outcome": "dismissed"}]
}
```

- Times are ISO 8601 with an offset. A finished task has `state` `done` or `rejected` and a
  `finished` time inside the 14 days before `now`; `reported` is optional (when the owner reported).
- Message `role`: `operator`, `l2` (the task's owner), `l3` (the coordinator) or `system`. Chat `role`:
  `operator` or `l3`; an operator message and L3's reply share a `turn`.
- Question `revision` starts at 1; a re-asked question is a second entry with the same `id` and
  `revision` 2, and the earlier revision's `resolution` is `superseded`. `asked_again: true` marks a
  question re-parked after an operator reply without a new revision. `audience` is `operator` or `l3`;
  resolution `by` is `operator`, `l2` or `l3`, and `message` names the message that answered it.
- Event `kind` is one Altitude records: `merge-approval-refused`, `grant-refused`, `ci-recheck`,
  `delivery` (with `number`, one per pushed PR head), `resume-failed`, `dispatch-failed`,
  `report-superseded` (a message continued a reported task; optional `contradicted`, the problem
  verification found in the replaced report; Altitude records no reason), `escalated`, `fault` (with
  `fault`, the fault's kind) or `fyi` (with `text`).
  The loader adds the task's own `new`, `state` and `review-requested` events.
- `current` tasks are open work the review must not repeat; `earlier` lists suggestions from earlier
  reviews with their outcome (`accepted` or `dismissed`).

## Key file

```json
{
  "expect": "suggestions",
  "patterns": [
    {"id": "P1", "kind": "words", "target": "agents", "signal": null,
     "anchors": ["fix-safari-voice:m2", "export-work-list:m4", "chat:c7"],
     "problem": "The operator keeps asking owners to put the outcome first in replies.",
     "fix": "The owner instructions ask for the outcome in the first sentence of every reply."}
  ],
  "decoys": [
    {"id": "D1", "type": "one-off", "anchors": ["fix-safari-voice:e1"],
     "why": "A single CI recheck after a provider outage."}
  ]
}
```

- `expect` is `suggestions` or `nothing` (clean and decoys-only projects).
- Pattern `kind`: `counted` (a signal fires on it), `words` (only the conversation shows it) or
  `shared` (different symptoms with one cause; add `wrong_explanation`, the plausible cause the
  evidence rules out). `target`: `project` or `agents` (how agents work). `signal`: the signal key at
  least two of its places fire, or `null` when none does; results are reported per signal.
- Anchors name planted places: `<slug>:<message id>`, `<slug>:<question id>@<revision>`,
  `<slug>:<event id>`, `<slug>:<review id>` (the review's request), `<slug>:<review id>/<finding id>`,
  `<slug>:report/<index>` (a final report finding) or `chat:<chat id>`. Every pattern has at
  least three.
- Decoy `type`: `one-off`, `answered` (re-asked after an operator answer in between), `handled` (a
  current task covers it), `dismissed` (an earlier suggestion was dismissed) or `justified` (friction
  the operator asked for).
