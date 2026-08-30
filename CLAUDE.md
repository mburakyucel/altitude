# CLAUDE.md — altitude

Instructions for agents working in this repo. Orientation: `README.md`; design and binding
contracts: `docs/` (start with `docs/VISION.md`, then `docs/ROLES.md` and `docs/ARCHITECTURE.md`).
The rules below are applied entries from the project ledger `docs/RULES.md`; each carries its
rule id so it stays auditable back to the incident that produced it.

## Dispatch and envelopes

- [R-002] Size the launch budget so the verification a brief mandates always fits: subagent_launches = expected implementers + 1 reserved for the reviewer (an S task with one L1 gets 3, not 2). The reserved launch is spent on nothing else, and capability probes are not launches. A reviewer that cannot run because the budget is full is a dispatch-time sizing bug, not a mid-run block.

## Shell and tools

- [R-003] Issue one plain command per Bash call. No &&, ;, pipes, cd, or $ inside heredocs - the Safety Net hooks and auto mode's command classifier reject those as unverifiable against the worktree boundary, and every refusal costs a turn. When you need composition, write a short script file and run it, or use the file tools instead.

## Briefs and mechanisms

- [R-005] A mechanism named in a request or brief (a lock to take, an ordering, a shared file to write) is a suggestion, not a spec: the request states the invariant and the evidence, and whoever implements verifies the mechanism against every path that reaches the new code before adopting it - for a lock, who already holds it on those paths and whether it is reentrant. A mechanism that fails the check is replaced and the failed check is recorded under Deviations; that is a documented default, not a question and not a block.
