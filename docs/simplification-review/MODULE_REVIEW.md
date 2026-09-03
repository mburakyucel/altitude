# Module-by-module review inventory

This is a fact inventory, not a deletion plan or approved sequence. “Candidate” means saved local
work touches the module; it does not mean that work should be accepted. Every module is currently
`unreviewed`.

## How to continue

The module order and the decisions that gate each phase are in [DECISIONS.md](DECISIONS.md). The
grouping below is a fact inventory. For the module in hand, establish current behavior, callers,
durable records, external effects, tests, and user requirements before writing the target.
Do not start a phase whose gating decision is not recorded there.
If evidence shows that a cross-module boundary is unavoidable, stop and ask Burak to approve that
exact boundary before continuing.

Review states are: `unreviewed`, `fact pack ready`, `user decision recorded`, `implementation
approved`, `implemented`, and `merged`.

## Python production modules

| Module (main lines) | Current responsibility and key dependencies | Saved candidate status | Neutral review question | Review state |
| --- | --- | --- | --- | --- |
| `altitude/__init__.py` (2) | Package marker/version only. | Untouched. | What package identity or version behavior is required? | unreviewed |
| `altitude/state.py` (222) | Atomic text/JSON writes, project lock, task/event storage, `STATE.md`; called nearly everywhere. | Integration actively changes atomic writes/locking and partially adopts keyed JSONL. | What durability, concurrency, corruption-recovery, and compatibility guarantees are required? | unreviewed |
| `altitude/tasks.py` (454) | Task state graph, conversation fencing, report/block/resume/done/archive, FYIs, decisions, merge holds. Called by server, dispatch, actions, L3 actions, L1, incidents, digest, and `bin/alt`. | Lightly touched in integration; heavily touched in B3/helper/outcome WIPs. | Which states, fields, transitions, writers, and archived-data readers are required? | unreviewed |
| `altitude/config.py` (132) | Paths, binaries, limits, project registry/discovery. | Touched by Codex-only closure. | Which settings and discovery behaviors remain product or operational requirements? | unreviewed |
| `altitude/route.py` (146) | Weekly/short-window provider selection from Claude/Codex quota evidence. | Integration removes autonomous Claude eligibility. | Which providers, quota evidence, pins, fallbacks, and override rules are required? | unreviewed |
| `altitude/quota_codex.py` (207) | Starts Codex App Server briefly to read rate limits and persists observations. | Untouched. | What quota observations and collection lifecycle are required? | unreviewed |
| `altitude/l3.py` (275) | Project conversation, provider sessions, handoffs, selection, Claude/Codex turns, context rotation. | Substantially replaced by integrated durable Codex L3 ownership. | Which providers, session semantics, restart behavior, streaming, and durable evidence are required? | unreviewed |
| `altitude/l3_actions.py` (381) | Validates/idempotently journals Codex L3 task, issue, incident, and recovery actions. | Substantially changed in integration and B3 follow-ups. | Which L3 capabilities, trust boundaries, effects, retry rules, and audit records are required? | unreviewed |
| `altitude/dispatch.py` (1,512) | Worktree creation, issue intake, briefs, launch/resume, steering, WIP/leases, liveness, cleanup. | Small integration edit; largest disputed B3 rewrite. | For intake, worktrees, launch, steering, liveness, leases, and cleanup, what behavior and ownership are required? | unreviewed |
| `altitude/engines.py` (1,165) | Claude/Codex adapters, sandbox/cgroup, worker discovery, stop/resume, synchronous turns. | Large physical-transition and Codex-only changes; B3 also rewrites it. | What execution interface satisfies provider, containment, steering, reconnection, and process-ownership requirements? | unreviewed |
| `altitude/actions.py` (310) | Settles inert Codex L2 actions after worker exit; depends on tasks, dispatch, L1, land, and recovery. | Small integration edit; large helper/outcome WIPs. | Which worker results and trusted effects are required, and what retry/atomicity guarantees must they have? | unreviewed |
| `altitude/l1.py` (462) | Optional implementer/reviewer records, execution, patches/findings, status/wait. | Provider closure in integration; separate helper WIP. | Are helpers/reviewers product requirements; if so, what ownership, isolation, result, and lifecycle behavior is required? | unreviewed |
| `altitude/git_policy.py` (417) | Repository/base checks, task trailers, hook installation, protected refs, service preflight. | Production module untouched; candidate tests/deployment depend on it. | Which repository provenance, protected-ref, hook, and installation invariants are required? | unreviewed |
| `altitude/land.py` (687) | Commit/push/PR/check/local-suite/merge transaction and exact head/base fencing. | Small provider-capability edits in integration. | Which publication operations, checks, fallbacks, receipts, and merge controls are required? | unreviewed |
| `altitude/verify.py` (104) | Validates report claims, reviewer/Git/PR state, and spend. | Untouched. | Which claims require independent verification, from which authoritative sources, and when? | unreviewed |
| `altitude/github_intake.py` (81) | Finds the one issue a new task names and inlines it into `request.md` at creation, from the project's own repository only. | Phase 1d rewrite. | Settled: fetch once at creation; a failed fetch refuses the task; no snapshot file, no resume hydration. | implemented |
| `altitude/incidents.py` (316) | Global fault index and per-project incident creation/amendment/numbering. | Untouched. | Which incident evidence, indexing, retention, correction, and visibility behaviors are required? | unreviewed |
| `altitude/recovery.py` (418) | Global hold/fuse, attention/backoff, repair claim, launch permission, clearance. | Changed by L3 ownership; B3 adds fences. | Which recovery episode states, L3 decisions, launch restrictions, and clearance evidence are required? | unreviewed |
| `altitude/transcript.py` (184) | Live provider/event projection with redaction. | Bundle sync/validate/export and the per-event snapshot removed (phase 1b). Generation fencing is revisited in phase 3. | — | implemented |
| `altitude/status.py` (232) | Aggregates task, helper, report, PR, spend, and error facts for UI/CLI. | Untouched in integration; helper WIP changes it. | Which status facts and freshness/authority guarantees do users and callers require? | unreviewed |
| `altitude/monitor.py` (99) | Projects L3/L2 sessions, context, and quota. | Untouched in integration; older B3 touches it. | Which session, context, and quota observations are useful and sufficiently reliable? | unreviewed |
| `altitude/digest.py` (75) | Queue/FYI/WIP text and optional speech output. | Untouched. | Which digest and audio behaviors, if any, are still wanted? | unreviewed |
| `altitude/server.py` (859) | Scheduler, callbacks, recovery wakeup, HTTP APIs/views, static serving, TLS. | Touched across most integration modules. | Which scheduling, API, projection, serving, and lifecycle behaviors remain required? | unreviewed |

## Operator, build, and policy files

| Module | Current responsibility | Saved candidate status | Neutral review question | Review state |
| --- | --- | --- | --- | --- |
| `bin/alt` | Operator CLI, scoped worker commands, serve/TLS/transcript/service/Git guard entry points. | Expanded by manifest/deployment/L3 work; helper WIP changes it. | Which operator and worker commands, authorities, outputs, and recovery procedures are required? | unreviewed |
| `scripts/restart_altitude.py` | Rebuild/restart/health/provenance workflow. | Changed by B3/deployment ideas. | What drain, build, restart, rollback, and health guarantees are required? | unreviewed |
| `Makefile` | Test, web build, restart, and service-install entry points. | Mostly untouched. | Which developer/operator entry points are required? | unreviewed |
| `.github/workflows/remote-tests.yml` | Remote candidate tests and identity gates. | Integration adds exact web test/typecheck/build. | Which candidate identity and runtime/web checks must remote CI enforce? | unreviewed |
| `systemd/altitude.service` | User service definition and hardening. | Manifest/deployment logic inspects it. | Which process, dependency, containment, restart, and install properties are required? | unreviewed |
| `hooks/guard.py` (45) | Deny-list nudge for Bash in Altitude-owned sessions: service lifecycle, firewall, reserved ports, Altitude home, altd, protected-branch moves and hook bypasses. The installed Git hooks remain the mechanical guard. | Replaced the 1,003-line shell lexer (phase 1a). | — | implemented |
| `hooks/edit_count.py`, `statusline-monitor.sh` | Edit telemetry and statusline observation. | Candidate proposal mentions trimming. | Which user-facing signal, if any, still uses these outputs? | unreviewed |
| Git hooks (`pre-commit`, `pre-push`, `pre-merge-commit`, `reference-transaction`) | Invoke protected-ref/provenance policy. | Retained by candidate proposal. | Which protected-path/ref and provenance checks must run at each Git boundary? | unreviewed |

## Contracts, prompts, and artifacts

| Module/family | Current responsibility | Saved candidate status | Neutral review question | Review state |
| --- | --- | --- | --- | --- |
| `schemas/l3_action.json` | Model-authored Codex L3 coordination action. | Changed for Codex-only L3. | Which L3 results require structured validation, and which fields/effects are required? | unreviewed |
| `schemas/l2_action.json` | Codex L2 `publish`, `complete_no_code`, `block`, `request_helpers`, or `continue`, with a common message. | Outcome WIP replaces much of it. | Which L2 outcomes/actions, fields, and trust boundaries are required? | unreviewed |
| `schemas/report.json` | Current completion/blocked report. | Replacement proposed but not integrated. | Which worker claims and trusted verification/publication facts must a report carry? | unreviewed |
| `schemas/review.json` | Structured reviewer findings. | Mostly untouched. | Is independent structured review required, and what evidence/dispositions must it retain? | unreviewed |
| `personas/l3.md`, `l3_codex.md` | Provider-specific L3 behavior and authority. | Codex persona changed in integration. | Which responsibilities, authority, provider differences, and user-interaction rules belong in the persona? | unreviewed |
| `personas/l2.md`, `l2_codex.md` | Provider-specific end-to-end L2 contracts. | Codex-only/outcome work changes them. | Which L2 ownership, communication, implementation, publication, and provider rules are required? | unreviewed |
| `personas/l1.md`, `reviewer.md` | Helper/reviewer prompts. | Helper target would replace them. | If helper roles remain, which role boundaries and result contracts are required? | unreviewed |
| `templates/brief.md` | Per-task rendered ownership/scope/landing instructions. | Candidate indirectly depends on it. | Which task-specific facts must the brief contain, and which policy is authoritative elsewhere? | unreviewed |
| `templates/incident.md`, `templates/pr.md` | Incident and PR bodies. | Largely untouched. | Which rendered fields and consumers require these formats? | unreviewed |
| Runtime task/status/conversation/events/report/transcript/helper files | Operational data under `ALTITUDE_HOME`. | Manifest/preflight catalogues and proposed migrations cover them. | Which live/archive data exists, who writes/reads it, and what compatibility/retention is required? | unreviewed |

## Web modules

| Module | Current responsibility | Saved candidate status | Neutral review question | Review state |
| --- | --- | --- | --- | --- |
| `web/src/data/api.ts` (524) | Fetch/stream client, Zod schemas, query keys, mutations. | Candidate adds dormant typed contracts; outcome WIP edits it. | Which command/read/stream operations and validation/error semantics are required? | unreviewed |
| `web/src/data/useOptimisticMutation.ts` (60) | Optimistic cache update/rollback helper. | Candidate proposal mentions consolidation. | Which optimistic behaviors and rollback guarantees are required? | unreviewed |
| `web/src/data/Toast.tsx` (163) | Toast queue/rendering. | Untouched. | Which notifications and queue/accessibility behavior are required? | unreviewed |
| `web/src/routes/Chat.tsx` (241) | Streaming L3 project conversation. | Core surface retained in proposal. | Which L3 conversation, streaming, state, and authority facts must this surface expose? | unreviewed |
| `web/src/routes/Task.tsx` (285) | Task state, direct L2 chat, files/events/actions/live link. | Candidate proposal describes major changes. | Which task facts, L2 steering, decisions, and operational controls must this surface expose? | unreviewed |
| `web/src/routes/LiveSession.tsx` (44) | Opt-in provider/event transcript. | Concept retained in proposal. | Which exact transcript data, identity binding, diagnostics, and navigation are required? | unreviewed |
| `web/src/routes/Project.tsx` (392) | Project summary, direct task creation/dispatch/actions, state/events. | Proposal removes some bypassing controls. | Which project overview, creation, coordination, and operational actions are required here? | unreviewed |
| `web/src/routes/Projects.tsx` (211) | Project list/registration. | Mostly retained. | Which project discovery, registration, and summary behavior is required? | unreviewed |
| `web/src/routes/Inbox.tsx` (151) | Decisions and FYIs. | Concept retained in proposal. | Which attention items, state changes, and links are required? | unreviewed |
| `web/src/routes/Monitor.tsx` (151) | Session/quota/context monitoring. | Candidate proposal describes simplification. | Which observations are actionable, reliable, and useful to show? | unreviewed |
| `web/src/routes.tsx`, `main.tsx`, `shell/*`, `styles.css` | Routing, app/query bootstrap, navigation/theme/layout. | Mostly untouched. | Which routes, shared state, navigation, and layout behavior are required after product surfaces are reviewed? | unreviewed |

Review every route together with its adjacent tests. Tests are evidence for current invariants, not
proof that every invariant is still wanted. For removed behavior, record the product decision and
either delete its test or replace it with evidence for the recorded retained or changed behavior.
