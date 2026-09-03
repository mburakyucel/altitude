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
| `altitude/tasks.py` (408) | Task state graph, conversation record and per-task inbox, report/block/resume/done/archive, FYIs, decisions, merge holds. Called by server, dispatch, actions, L3 actions, incidents, digest, and `bin/alt`. | Lightly touched in integration; heavily touched in B3/helper/outcome WIPs. | Which states, fields, transitions, writers, and archived-data readers are required? | identity and messaging implemented (phase 3); rest unreviewed |
| `altitude/config.py` (132) | Paths, binaries, limits, project registry/discovery. | Touched by Codex-only closure. | Which settings and discovery behaviors remain product or operational requirements? | trimmed (phase 6): unread `WEB`, `SERVICE_PORTS`, and the per-engine warn/act constants folded into `CONTEXT_LINES` |
| `altitude/route.py` (121) | Weekly/short-window provider selection from Claude/Codex quota evidence; returns `{engine, why}` and the task keeps only `why`. | Phase 1e trim. | Settled for now: weekly-first, pins honoured, no fallback when the pin is exhausted. Decision 6 may revisit routing when the engine contract lands. | trimmed (phase 1e); rest unreviewed |
| `altitude/quota_codex.py` (207) | Starts Codex App Server briefly to read rate limits and persists observations. | Untouched. | What quota observations and collection lifecycle are required? | unreviewed |
| `altitude/l3.py` (275) | Project conversation, provider sessions, handoffs, selection, Claude/Codex turns, context rotation. | Substantially replaced by integrated durable Codex L3 ownership. | Which providers, sessions, handoffs, and rotation rules are required? | phase 5c: the Codex turn is a plain sandboxed `codex exec` with the shared persona and `alt` door; no broker |
| `altitude/l3_actions.py` (350) | Validated and journaled Codex L3 actions. | Substantially changed in integration and B3 follow-ups. | Which L3 capabilities, trust boundaries, effects, retry rules, and audit records are required? | deleted (phase 5c): Codex L3 uses the `alt` door |
| `altitude/dispatch.py` (837) | Worktree creation, briefs, launch, queued resume and stop, WIP/leases, liveness, cleanup. | Small integration edit; largest disputed B3 rewrite. | For intake, worktrees, launch, steering, liveness, and leases, what behavior and ownership are required? Cleanup is settled: phase 1c replaced the 500-line proof chain with one rule (branch on origin/main or its verified PR merged at the tip; keep if dirty or the L2 is live). | cleanup (phase 1c) and steering (phase 3) implemented; rest unreviewed |
| `altitude/engines.py` (1,165) | Claude/Codex adapters, worker discovery, stop/resume, synchronous turns. | Large physical-transition and Codex-only changes; B3 also rewrites it. | What execution interface satisfies provider, containment, steering, reconnection, and process-ownership requirements? | phase 5: two thin adapters; Codex runs in its own sandbox inside a transient user unit; the permission profile, preflight, containment proof, and environment allowlist are gone |
| `altitude/actions.py` (202) | Settled inert Codex L2 actions after worker exit. | Small integration edit; large helper/outcome WIPs. | Which worker results and trusted effects are required, and what retry/atomicity guarantees must they have? | deleted (phase 5b): the Codex L2 uses the `alt` door |
| `altitude/l1.py` | Optional implementer/reviewer records, execution, patches/findings, status/wait. | Deleted in phase 4 (decision 5): helpers are engine-native. | — | deleted (phase 4) |
| `altitude/git_policy.py` (417) | Repository/base checks, task trailers, hook installation, protected refs, service preflight. | Production module untouched; candidate tests/deployment depend on it. | Which repository provenance, protected-ref, hook, and installation invariants are required? | trimmed (phase 6): compatibility aliases and the never-used `head=` parameters removed |
| `altitude/land.py` (683) | Commit/push/PR/check/local-suite/merge transaction and exact head/base fencing. | Small provider-capability edits in integration. | Which publication operations, checks, fallbacks, receipts, and merge controls are required? | trimmed (phase 6): the undeclared-lease and not-prefetched branches were unreachable once a task record became mandatory |
| `altitude/verify.py` (104) | Validates report claims, reviewer/Git/PR state, and spend. | Untouched. | Which claims require independent verification, from which authoritative sources, and when? | kept as is (phase 6) |
| `altitude/github_intake.py` (81) | Finds the one issue a new task names and inlines it into `request.md` at creation, from the project's own repository only. | Phase 1d rewrite. | Settled: fetch once at creation; a failed fetch refuses the task; no snapshot file, no resume hydration. | implemented |
| `altitude/incidents.py` | Global fault index and per-project incident creation/amendment. | Phase 2: `system_fault` blocks the faulting task, files one incident per kind per day, and queues one L3 message; a repair task's fault reaches the Inbox only. | — | settled (phases 1e, 2) |
| `altitude/recovery.py` | Global hold/fuse, attention/backoff, repair claim, launch permission, clearance. | Deleted in phase 2 (decision 4). | — | deleted (phase 2) |
| `altitude/transcript.py` (175) | Live provider/event projection with redaction. | Bundle sync/validate/export and the per-event snapshot removed (phase 1b). Session fencing by engine and session id (phase 3). | — | implemented |
| `altitude/status.py` | Aggregates task, report, PR, spend, and error facts for UI/CLI. | Untouched in integration; helper WIP changes it. | Which status facts and freshness/authority guarantees do users and callers require? | unreviewed |
| `altitude/monitor.py` (99) | Projects L3/L2 sessions, context, and quota. | Untouched in integration; older B3 touches it. | Which session, context, and quota observations are useful and sufficiently reliable? | unreviewed |
| `altitude/digest.py` (47) | Queue/FYI/WIP text. | Phase 1e: speech output dropped. | Text digest only; audio was decided out in DECISIONS.md phase 1e. | implemented |
| `altitude/server.py` (804) | Scheduler, callbacks, recovery wakeup, HTTP APIs/views, static serving, TLS. | Touched across most integration modules. | Which scheduling, API, projection, serving, and lifecycle behaviors remain required? | trimmed (phase 6): the unused `/api/install-statusline` route removed; HEAD support and the operator APIs kept |

## Operator, build, and policy files

| Module | Current responsibility | Saved candidate status | Neutral review question | Review state |
| --- | --- | --- | --- | --- |
| `bin/alt` | Operator CLI, scoped worker commands, serve/TLS/transcript/service/Git guard entry points. | Expanded by manifest/deployment/L3 work; helper WIP changes it. | Which operator and worker commands, authorities, outputs, and recovery procedures are required? | repaired (phase 6): `alt project` and `alt state` parsers restored; the operator subcommands without a caller in code or docs (`task paths|brief|list`, `fyi`, `decisions`, `digest`, `monitor`, `dispatch`, `verify`, `poll`, `chat`, `l3-reset`, `incident list`) stay until Burak decides |
| `scripts/restart_altitude.py` | Rebuild/restart/health/provenance workflow. | Changed by B3/deployment ideas. | What drain, build, restart, rollback, and health guarantees are required? | unreviewed |
| `Makefile` | Test, web build, restart, and service-install entry points. | Mostly untouched. | Which developer/operator entry points are required? | unreviewed |
| `.github/workflows/remote-tests.yml` | Remote candidate tests and identity gates. | Integration adds exact web test/typecheck/build. | Which candidate identity and runtime/web checks must remote CI enforce? | unreviewed |
| `systemd/altitude.service` | User service definition and hardening. | Manifest/deployment logic inspects it. | Which process, dependency, containment, restart, and install properties are required? | unreviewed |
| `hooks/guard.py` | Deny-list nudge for Bash in Altitude-owned sessions. | Deleted in phase 5 (decision 6); the installed Git hooks are the mechanical guard, the persona carries the rest. | — | deleted (phase 5) |
| `hooks/inbox.py` (33) | PostToolUse/Stop hook: hands the task inbox to a running Claude L2 at its next checkpoint (phase 3). | — | — | implemented |
| `hooks/edit_count.py`, `statusline-monitor.sh` | Edit telemetry and statusline observation. | Candidate proposal mentions trimming. | Which user-facing signal, if any, still uses these outputs? | unreviewed |
| Git hooks (`pre-commit`, `pre-push`, `pre-merge-commit`, `reference-transaction`) | Invoke protected-ref/provenance policy. | Retained by candidate proposal. | Which protected-path/ref and provenance checks must run at each Git boundary? | unreviewed |

## Contracts, prompts, and artifacts

| Module/family | Current responsibility | Saved candidate status | Neutral review question | Review state |
| --- | --- | --- | --- | --- |
| `schemas/l3_action.json` | Model-authored Codex L3 coordination action. | Changed for Codex-only L3. | Which L3 results require structured validation, and which fields/effects are required? | deleted (phase 5c) |
| `schemas/l2_action.json` | Codex L2 `publish`, `complete_no_code`, `block`, or `continue`, with a common message. | `request_helpers` and `helpers` removed in phase 4. | Which L2 outcomes/actions, fields, and trust boundaries are required? | deleted (phase 5b) |
| `schemas/report.json` | Current completion/blocked report. | Replacement proposed but not integrated. | Which worker claims and trusted verification/publication facts must a report carry? | unreviewed |
| `schemas/review.json` | Structured reviewer findings. | Deleted in phase 4 (decision 5). | — | deleted (phase 4) |
| `personas/l3.md` | One L3 contract for both engines. | Codex persona changed in integration. | Which responsibilities, authority, provider differences, and user-interaction rules belong in the persona? | one persona per role (phase 5c; `l3_codex.md` deleted) |
| `personas/l2.md` | One end-to-end L2 contract for both engines. | Codex-only/outcome work changes them. | Which L2 ownership, communication, implementation, publication, and provider rules are required? | one persona per role (phase 5b; `l2_codex.md` deleted) |
| `personas/l1.md`, `reviewer.md` | Helper/reviewer prompts. | Deleted in phase 4 (decision 5); `personas/l2*.md` carry the delegation guidance. | — | deleted (phase 4) |
| `templates/brief.md` | Per-task rendered ownership/scope/landing instructions. | Candidate indirectly depends on it. | Which task-specific facts must the brief contain, and which policy is authoritative elsewhere? | unreviewed |
| `templates/incident.md`, `templates/pr.md` | Incident and PR bodies. | Largely untouched. | Which rendered fields and consumers require these formats? | unreviewed |
| Runtime task/status/conversation/events/report/transcript files | Operational data under `ALTITUDE_HOME`. | Manifest/preflight catalogues and proposed migrations cover them. | Which live/archive data exists, who writes/reads it, and what compatibility/retention is required? | unreviewed |

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
