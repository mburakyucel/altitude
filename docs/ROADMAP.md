# Altitude roadmap

*Reset 2026-08-31 to follow the operating model in `docs/ARCHITECTURE.md` and the
current GitHub backlog. This is a sequence of outcomes, not a mandatory workflow or
implementation schema.*

## Target operating contract

GitHub issues will be the durable backlog. Burak selects an issue or asks L3 to create
or delegate work; L3 will not autonomously drain the issue list. Ordinary conversation
will not automatically become backlog, and deferred or completed work will not remain
in Altitude's active task view. Issues #106 and #107 implement these target behaviors;
this roadmap does not claim that the current runtime already enforces them.

Every code change uses an isolated branch or worktree and reaches main through a PR.
The architecture leaves planning depth, delegation, subagent use, and appropriate
review depth to agent judgment within the ownership, isolation, durability, landing,
and recovery boundaries.

## Current state: stabilization

Altitude remains stopped and masked. The previous live task, session, rule, incident,
and worktree state has been archived and removed from live scheduling. Archived tasks
are evidence, not a queue: they will not be replayed or resumed automatically.

PR #112 landed a bounded service/process-ownership improvement. It is useful progress,
but it does not satisfy the complete restart boundary or authorize a restart.

The remaining stabilization work is intentionally reductive:

- keep the active GitHub backlog limited to work that still matches the agreed
  architecture;
- evaluate preserved branches and large prior changes as parts bins, extracting only
  small, aligned, reviewable slices rather than reviving them wholesale;
- classify and remove obsolete incidents, rules, personas, schemas, hooks, and other
  mechanisms while preserving relevant evidence in the stabilization archive and Git
  history;
- keep remote CI to one base-owned exact-candidate Python check rather than restoring
  the removed cgroup, manifest, and artifact gate;
- make documentation describe implemented behavior and clearly distinguish it from
  target behavior;
- finish with a clean live working set and explicit keep, defer, or remove decisions
  for retained mechanisms.

Trimming is based on the agreed architecture, not line-count targets. No component is
removed merely because it is complex, and this roadmap does not decide individual
deletions in advance.

## Minimum restart boundary

Issues #106 through #109 are the restart-critical implementation set. They may be
delivered as focused PRs and do not imply a fixed internal schema or mandatory agent
pipeline.

| Issue | Required outcome |
|---|---|
| #106 | One unambiguous mutation authority, current task ownership/generation, a clean active working set, and no autonomous backlog draining. |
| #107 | Flexible L3 coordination, one directly reachable end-to-end L2 per delegated task, and optional L1 use at L2's discretion. |
| #108 | L3-controlled operational recovery, one direct recovery L2 when code is needed, and no recursive incident/rule/healing-task creation. |
| #109 | A simple PR-only landing boundary with isolated ownership, exact-change evidence, appropriate checks/review, no unchecked local fallback, and stale-publisher protection. The remote check has been reduced to a base-owned exact-candidate Python run; the remaining landing controls stay in scope. |

Implementing and verifying the required outcomes of #106 through #109 is necessary but
not sufficient. Before Altitude is unmasked for normal operation, controlled
verification must demonstrate that:

- ordinary dispatch can be paused while recovery is in progress;
- archived work cannot silently return to scheduling;
- current owners can publish while stale or superseded generations cannot;
- L3 can reconcile and recover the service without creating another healing task;
- one recovery L2 can work directly without entering the old multi-stage pipeline;
- service and worker startup, shutdown, restart, and cleanup leave no ambiguous live
  descendants.

Offline and isolated checks come first. If verification requires a bounded live
unmask/start/restart rehearsal, that rehearsal itself requires Burak's explicit prior
authorization and runs with ordinary dispatch disabled. Only a separate explicit
restart decision after the full verification authorizes unmasking and starting the
service for normal operation.

## Deferred, user-selected work

The following issues remain useful but are not restart blockers and should not start
automatically:

- #104 — reassess the preserved wireframe draft after simplification;
- #105 — design bounded capabilities after the architecture and landing boundary are
  settled;
- #12 — add passive, human-readable task and session resource visibility after the
  ownership and conversation skeleton exists;
- #2 — pilot the simplified system on a real project after restart readiness and an
  explicit user decision.

These items can be refined, implemented, or closed through normal L3 conversation and
direct L2 ownership when Burak selects them.

## Direction after restart

Use the real-project pilot to judge whether Altitude is making work easier to direct,
safer to integrate, and simpler to recover. Add product surface or automation only in
response to demonstrated needs. When a mechanism no longer supports the operating
model, preserve the useful evidence and remove it from the active system rather than
creating another compensating layer.
