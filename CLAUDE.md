# CLAUDE.md — altitude

Instructions for agents working in this repo. Orientation: `README.md`; design and binding
contracts: `docs/` (start with `docs/VISION.md`, then `docs/ROLES.md` and `docs/ARCHITECTURE.md`).
The rules below are applied entries from the project ledger `docs/RULES.md`; each carries its
rule id so it stays auditable back to the incident that produced it.

## Dispatch and envelopes

- [R-002] Size the launch budget so the verification a brief mandates always fits: subagent_launches = expected implementers + 1 reserved for the reviewer (an S task with one L1 gets 3, not 2). The reserved launch is spent on nothing else, and capability probes are not launches. A reviewer that cannot run because the budget is full is a dispatch-time sizing bug, not a mid-run block.

## Shell and tools

- [R-003] Issue one plain command per Bash call. No &&, ;, pipes, cd, or $ inside heredocs - the Safety Net hooks and auto mode's command classifier reject those as unverifiable against the worktree boundary, and every refusal costs a turn. When you need composition, write a short script file and run it, or use the file tools instead.
