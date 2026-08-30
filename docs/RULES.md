# Project rules — altitude

*Ledger (decision 14/32). Each entry: five fields + the rule text. Project scope: these apply to work in this repo only; global and stack rules live in `rules/`. Unlike those, project rules are **not** compiled into personas or briefs — an entry here is a record, and it only takes effect once its text is applied by hand to the file named in its `where:` field.*

## R-002 — reserve a launch for the mandated reviewer
- scope: project
- where: CLAUDE.md
- origin: I-004 (altitude)
- prevents: 
- effect: 
- status: probation
- text: Size the launch budget so the verification a brief mandates always fits: subagent_launches = expected implementers + 1 reserved for the reviewer (an S task with one L1 gets 3, not 2). The reserved launch is spent on nothing else, and capability probes are not launches. A reviewer that cannot run because the budget is full is a dispatch-time sizing bug, not a mid-run block.

## R-003 — One plain command per Bash call
- scope: project
- where: CLAUDE.md
- origin: I-002 (altitude)
- prevents: hook-refuses-compound-bash
- effect: 1-5 turns per task recovered (I-002: 5 turns; this-is-a-test-job-submitted-from-my-pho: 1 turn, recurrence 2026-08-30)
- status: probation
- text: Issue one plain command per Bash call. No &&, ;, pipes, cd, or $ inside heredocs - the Safety Net hooks and auto mode's command classifier reject those as unverifiable against the worktree boundary, and every refusal costs a turn. When you need composition, write a short script file and run it, or use the file tools instead.

## R-004 — Staged L tasks land stage by stage: stage 1 is its own PR, checkpoint until it merges
- scope: project
- where: L2 persona: the stage plan and merge-policy step for L tasks
- origin: I-018 (altitude)
- prevents: long-lived L branches that chase main and double-port files the branch retires (I-018)
- effect: one merge by Burak per stage PR instead of one at the end - the L2 exits at each stage PR and is resumed after the merge
- status: probation
- text: For an L task, stage 1 (the skeleton or riskiest slice) is its own PR: open it, checkpoint progress.md, report Blocked: stage 1 PR awaits merge, and exit - the L merge policy stands, Burak merges, and you are resumed on merged main. Later stages open their own PRs from merged main, each reviewable in one pass. Never stack stages on one branch: main drifts under it and every upstream edit to a file the branch retires must be ported twice.
