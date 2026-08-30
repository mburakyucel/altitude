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
- effect: one short-lived branch per stage instead of one branch chasing main (I-018: 2 of 10 launches and about 40 of 220 turns spent on chases).
- revised: 2026-08-30, I-025 (the original text assumed the L merge policy that decision 48 retired)
- status: probation
- text: For an L task, stage 1 (the skeleton or riskiest slice) is its own PR: open it, get it reviewed, and merge it under decision 48 before any fan-out, then start every later stage from merged main in its own PR, each reviewable in one pass. Never stack stages on one branch: main drifts under it and every upstream edit to a file the branch retires must be ported twice. Only when the brief marks the task held do you stop at the open stage-1 PR, as the hold mechanism says, and continue after Burak merges it.

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
- where: personas/l3.md section Style, altitude/intake.py, altitude/server.py
- origin: I-021 (altitude)
- prevents: chat-doubles-as-turn-log
- effect: the chat is usable as the entry point again (his words, 2026-08-30 05:24Z)
- status: probation
- text: Chat is a conversation with Burak on his phone, not a log. A chat reply answers what he asked in a few plain sentences: what changed and what he needs to do, nothing else. No ids, slugs, file paths, rule or decision numbers or spend figures in chat text: those go in FYIs, digests, the detail of a card and the task folder. Server-triggered turns (proposal-ready, report-landed, reconcile, audit, backlog) are not chat: their output is the card, the digest or the FYI, and the closing text is at most two plain sentences saying what happened and whether anything waits on him.

## R-010 — Codex L2 dispatch proves both write roots
- scope: project
- where: CLAUDE.md
- origin: I-030 (altitude)
- prevents:
- effect:
- status: probation
- text: Before a Codex L2 task starts, dispatch must verify that the session can write and remove a probe in both its assigned worktree and task folder. A failed probe raises one system fault and holds the task without consuming the task's retry or turn envelope.

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

## R-014 — The test command is unittest, named in every sub-brief
- scope: project
- where: the owning section
- origin: I-063 (altitude)
- prevents:
- effect:
- status: probation
- text: This repository's test gate is python3 -m unittest discover tests (the Makefile's make test); pytest is not installed on this host. Every L2 roadmap and every L1 sub-brief names that command verbatim, and a sub-brief that names any other test runner is corrected before launch, not after a failed run.

## R-016 — A fix round on an open PR runs the L1 in the task worktree with --cwd
- scope: project
- where: personas/l2.md (the L1 launch paragraph and the fix-round step) and templates/brief.md (the review and fix sentence)
- origin: I-070 (altitude)
- prevents: a fix-round launch spent on an L1 that cannot reach the PR branch, followed by the L2 patching the PR itself with no re-review
- effect: one sentence in the L2 persona and one in the brief template
- status: probation
- text: alt l1 run cuts a fresh worktree from origin/main, so a sub-brief that asks an L1 to push to an existing PR branch cannot be satisfied and the launch is lost. A fix round on an open PR is launched as alt l1 run --cwd <the task worktree that holds the PR branch> --brief <fix brief>: with --cwd the launcher skips worktree and branch creation and the L1 amends the PR in place. The fix brief names the PR number and the findings to address, and the L2 checks the amended PR with git diff --stat origin/main <head> before merging.

## R-017 — A post-fix review converges: it checks the previous findings and the fix diff; new findings block only when they defeat the definition of done
- scope: project
- where: personas/l2.md §How you work; the reviewer sub-brief in templates/brief.md
- origin: I-073 (altitude)
- prevents: S tasks looping through fresh full reviews past their envelope while a mergeable PR waits
- effect: post-fix reviews converge on the fix; secondary findings become follow-up tasks instead of extra rounds
- status: probation
- text: A post-fix review checks the dispositions of the previous round's findings and the fix diff for regressions. A new finding blocks the merge only if it defeats the definition of done or breaches a guardrail (R-006); every other new finding is recorded with its file and line under Follow-ups, the L2 names or files the follow-up task in the report, and the PR merges (decision 48). One fix round per S task; a second round needs the L3's envelope answer, never a silent further reviewer.
