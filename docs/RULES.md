# Project rules — altitude

*Ledger (decision 14/32). Each entry: six fields + the rule text. Project scope: these apply to work in this repo only; global and stack rules live in `rules/`. Unlike those, project rules are **not** compiled into personas or briefs — an entry here is a record, and it only takes effect once its text is applied by hand to the file named in its `where:` field.*

## R-002 — reserve a launch for the mandated reviewer
- scope: project
- where: CLAUDE.md
- origin: I-004 (altitude)
- prevents: s-envelope-no-slack-for-mandated-reviewer
- effect: S envelopes sized implementers+1, so the mandated reviewer always runs; verify: no `Blocked: envelope (needed N)` on a reviewer (I-004: reviewer refused, PR merged by hand after a waiver)
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

## R-005 — A mechanism named in a request or brief is a suggestion the implementer verifies at the call sites
- scope: project
- where: CLAUDE.md (new section: Briefs and mechanisms); the L3 side applies immediately to requests this L3 writes
- origin: I-019 (altitude)
- prevents: request-prescribes-unverified-mechanism
- effect: one L1 build round and one reviewer launch per recurrence (#37: 2 rounds, 3/3 launches, 34/40 turns)
- status: probation
- text: A mechanism named in a request or brief (a lock to take, an ordering, a shared file to write) is a suggestion, not a spec: the request states the invariant and the evidence, and whoever implements verifies the mechanism against every path that reaches the new code before adopting it - for a lock, who already holds it on those paths and whether it is reentrant. A mechanism that fails the check is replaced and the failed check is recorded under Deviations; that is a documented default, not a question and not a block.

## R-006 — The definition of done names only gates the repository has
- scope: project
- where: templates/brief.md (the Definition of done line)
- origin: I-020 (altitude)
- prevents: brief-names-gate-the-repo-lacks
- effect: one recorded deviation and a not-applicable argument per task in this repo (#37, #38), plus the risk of checks skipped read as green
- status: probation
- text: The definition of done names only gates the repository has. Where the repo has CI (a .github/workflows directory), the main run is green after merge. Where it has none, the full local test suite runs on merged main and the report states the count, and a checks value of skipped or no-checks-reported is never read as passed. A gate is never satisfied by its absence.

## R-007 — Chat is a conversation with Burak, never the turn log
- scope: project
- where: personas/l3.md section Style, and the server turn prompts that ask the L3 to report in N sentences (altitude/l3.py or wherever they are built)
- origin: I-021 (altitude)
- prevents: chat-doubles-as-turn-log
- effect: the chat is usable as the entry point again (his words, 2026-08-30 05:24Z)
- status: probation
- text: Chat is a conversation with Burak on his phone, not a log. A chat reply answers what he asked in a few plain sentences: what changed and what he needs to do, nothing else. No ids, slugs, file paths, rule or decision numbers or spend figures in chat text: those go in FYIs, digests, the detail of a card and the task folder. Server-triggered turns (proposal-ready, report-landed, reconcile, audit, backlog) are not chat: their output is the card, the digest or the FYI, and the closing text is at most two plain sentences saying what happened and whether anything waits on him.

## R-012 — A resumed worker needs a fresh report generation
- scope: project
- where: CLAUDE.md
- origin: I-032 (altitude)
- prevents:
- effect:
- status: probation
- text: When a blocked L2 is resumed, record the resume generation or timestamp and ignore every report artifact older than it; both Claude and Codex resume regressions must prove the old blocked report cannot be ingested again.

## R-013 — A merge hold is not the L2's to lift
- scope: project
- where: CLAUDE.md
- origin: I-060 (altitude)
- prevents:
- effect:
- status: probation
- text: When the task carries a merge hold (hold_merge set), the L2 opens the PR, reports ok with the PR number and stops. It never merges around the hold - not with gh pr merge, not by any other route - even when the hold's stated reason looks false for its PR; it says so in the report and the L3 releases the hold. Merging under a hold is a breach, not a deviation.
