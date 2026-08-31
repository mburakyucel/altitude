# CLAUDE.md — altitude

Instructions for agents working in this repo. Orientation: `README.md`; design and binding
contracts: `docs/` (start with `docs/VISION.md`, then `docs/ROLES.md` and `docs/ARCHITECTURE.md`).
The rules below are applied entries from the project ledger `docs/RULES.md`; each carries its
rule id so it stays auditable back to the incident that produced it.

## Dispatch and envelopes

- [R-002] Size the launch budget so the verification a brief mandates always fits: subagent_launches = expected implementers + 1 reserved for the reviewer (an S task with one L1 gets 3, not 2). The reserved launch is spent on nothing else, and capability probes are not launches. A reviewer that cannot run because the budget is full is a dispatch-time sizing bug, not a mid-run block.
- [R-010] Before any L2 task starts, dispatch must verify its exact worktree capability and generation-fenced checkpoint broker without granting raw task-folder access. A failed probe raises one system fault and holds the task without consuming the task's retry or turn envelope.
- [R-012] When a blocked L2 is resumed, record the resume generation or timestamp and ignore every report artifact older than it; both Claude and Codex resume regressions must prove the old blocked report cannot be ingested again.
- [R-013] A task merge hold is an additional blocker whose reason must survive into the report. R-014 is currently stricter and forbids every automated landing path even without a task hold; no `report ok`, generic check, or L3 action lifts either restriction.

## Shell and tools

- [R-003] Issue one plain command per Bash call. No &&, ;, pipes, cd, or $ inside heredocs - the Safety Net hooks and auto mode's command classifier reject those as unverifiable against the worktree boundary, and every refusal costs a turn. When you need composition, write a short script file and run it, or use the file tools instead.

## Briefs and mechanisms

- [R-005] A mechanism named in a request or brief (a lock to take, an ordering, a shared file to write) is a suggestion, not a spec: the request states the invariant and the evidence, and whoever implements verifies the mechanism against every path that reaches the new code before adopting it - for a lock, who already holds it on those paths and whether it is reentrant. A mechanism that fails the check is replaced and the failed check is recorded under Deviations; that is a documented default, not a question and not a block.
- [R-006] The definition of done names only gates the repository has, and a gate is never satisfied by its absence. While R-014 bootstrap is incomplete, neither local tests, generic checks, nor a missing check authorize landing; agents stop at a reviewed PR with the trusted-remote integration blocker.
- [R-014] Authoritative test evidence is a successful, base-attached `trusted-remote / evidence` run whose immutable artifact binds the exact base/candidate pair, current PR identity, run and attempt, check-run id, workflow SHA, and request nonce. A candidate-head check alone is never evidence. Local `make test` is developer-only and never authorizes a merge or appears in an automated landing fallback. Until the base-trusted remote harness is bootstrapped and landing integration is merged, agents stop before landing rather than substituting a local or absent gate.
