# CLAUDE.md — altitude

Start with `docs/ARCHITECTURE.md`. It records the current implementation, the agreed L3/L2/L1
operating model, and the boundaries that prevent collisions and recursive recovery.

## Stabilization hold

- The Altitude service is deliberately stopped and masked.
- Do not restart it, replay archived tasks, or resume the old orchestration/self-healing queue
  unless Burak explicitly asks.
- Treat `docs/ROLES.md`, `docs/KICKOFF.md`, `docs/DECISIONS.md`, and `docs/RULES.md` as
  historical inputs where they conflict with the current architecture.
- An incident or review finding does not authorize creating another task, rule, or agent session.

## Working contract

- L3 is Burak's project-level point of contact and uses model judgment to answer, ask, or delegate.
- One L2 owns a delegated task end-to-end and talks directly with Burak about task-specific work.
- L2 may implement directly or use L1 subagents at its discretion; L1 is never a mandatory stage.
- Do not force flexible requests through a programmatic request class, proposal/critic pipeline, or
  fixed subagent/reviewer count.
- Every code change uses an isolated branch/worktree and reaches main through a PR.
- The owning L2 may merge after appropriate checks and review; it holds when independent or user
  review is genuinely needed.
- During a system recovery, L3 pauses normal dispatch and owns operational actions. One recovery L2
  owns any code repair directly, without the ordinary multi-stage pipeline.

## Repository changes during the reset

Stay within the human-approved scope. Preserve unrelated and in-progress work. Prefer factual,
human-readable artifacts over new schemas or automation. Do not weaken checks or bypass the PR
boundary to make progress.
