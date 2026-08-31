# Report

## Landed

Record PR numbers and merge SHAs, `main` run ids and results, and deploy status.

## Review

Record every finding with its tag, severity, summary, disposition, and reason. If independent review
was not needed, say so here and use an empty `review` list in `report.json`. Disposition is `fixed`,
`dismissed`, or `open`. Use `open` only when **Blocked** is non-empty, and preserve the finding text so
a resumed L2 can continue from it.

## Deviations

Record every choice the brief did not cover and the reversible default taken.

## Decisions

Record questions for Burak only when blocked.

## FYI

Record items L3 should know.

## Blocked

Record the exact missing item, or leave empty.

## Follow-ups

Record work deliberately left out.

## Spend

Record turns, subagent launches (zero is valid), retries, reverts, and model tiers.
