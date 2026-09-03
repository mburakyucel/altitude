# Candidate and preserved work

At checkpoint creation these branches were local. On 2026-09-02 all branches listed here were
pushed to the private `mburakyucel/altitude` repository; no relevant simplification work remains
local-only.

## Publication and review surfaces

All five draft PRs below were **closed on 2026-09-02** after review; see [DECISIONS.md](DECISIONS.md)
and each PR's closing comment. Their branches remain on origin as history only.

| PR | Scope | State |
| --- | --- | --- |
| [#139](https://github.com/mburakyucel/altitude/pull/139) | Accumulated `simplify/integration` candidate against `main` | draft; do not merge wholesale |
| [#142](https://github.com/mburakyucel/altitude/pull/142) | B3 L2-ownership experiment against `simplify/integration` | draft; recorded steering objection |
| [#138](https://github.com/mburakyucel/altitude/pull/138) | Managed-helper experiment against `simplify/integration` | draft; incomplete and conflicting |
| [#140](https://github.com/mburakyucel/altitude/pull/140) | Outcome-settlement experiment against `simplify/integration` | draft; incomplete and conflicting |
| [#141](https://github.com/mburakyucel/altitude/pull/141) | Separate deferred durability work against `main` | draft; review one owning module at a time |

The component and superseded branches are also exact remote refs. They do not each have another PR:
their reviewable code is represented by the aggregate or WIP PR above, and opening duplicate PRs
would multiply overlapping merge candidates. PR
[#143](https://github.com/mburakyucel/altitude/pull/143) merged this documentation checkpoint.

## 1. Clean accumulated checkpoint — not approved

```text
branch:   simplify/integration
commit:   ce7c46a56fd5940c0fd38d63c817d0c71279738b
worktree: /tmp/altitude-simplify-integration
base:     main at 97e11979bdc0814ad5067eab717f999d1c251437
status:   clean, no upstream
net:      67 files, +17,241/-1,053
```

The range is linear and contains these exact commits:

| # | Commit | Purpose | Size |
| ---: | --- | --- | ---: |
| 1 | `57886cf` | Initial seven-file comprehensive proposal | +3,421 |
| 2 | `c416e1f` | First reviewed proposal revision | +1,266/-1,447 |
| 3 | `91188a8` | Reorder migration around provable ownership | +158/-217 |
| 4 | `6dce69b` | Mark the then-reviewed proposal accepted; add baseline/target | +709/-231 |
| 5 | `e3d865d` | Durable state primitives and tests | +1,341/-8 |
| 6 | `0337e84` | Dormant Python/TypeScript boundary contracts and fixtures | +972 |
| 7 | `7315291` | Exact web-candidate remote CI gate | +177 |
| 8 | `ea05a5b` | Shared physical ownership foundation | +1,064/-7 |
| 9 | `768a47c` | Worker-outcome observation contract additions | +382/-40 |
| 10 | `116d256` | Close autonomous execution paths to Codex | +905/-250 |
| 11 | `f95b5f6` | Runtime manifest and legacy preflight | +3,884/-6 |
| 12 | `ff1fba0` | Reconcile two integrated writer-inventory assertions | +2/-2 |
| 13 | `f38c9c9` | Dormant deployment baseline | +652 |
| 14 | `a1d42e8` | Dormant deployment qualification authority | +1,058/-26 |
| 15 | `16270e9` | Architecture/preflight clarity corrections | +401/-152 |
| 16 | `2758d8b` | Durable Codex L3 turn ownership | +3,063/-1,001 |
| 17 | `ce7c46a` | L3 recovery-gap fixes | +143/-23 |

### Candidate as implemented, not as a proposed target

“Active” below means reachable if this candidate source is run. Nothing in this table is deployed
on `main`, and the local branch has no remote counterpart.

| Branch/commit | Modules | Adoption and production callers | Durable records / external effects | Current code deleted | Review state |
| --- | --- | --- | --- | --- | --- |
| `e3d865d` plus later integration fixes | `state.py`, tests | Active changes to `atomic_write` and `project_lock`; keyed JSONL is adopted by candidate L3 chat and recovery clearance, but the general state migration is incomplete. | Existing JSON/text files gain stronger local durability; keyed JSONL adds in-file advisory locking, unterminated-tail truncation, stable-key dedupe/conflict rejection, and file/parent fsync. | No broad legacy writer deletion. | unreviewed |
| `0337e84`, `768a47c` | `contracts.py`, TS contracts, fixtures | Dormant production boundary contracts; no runtime Python importer and no live web API caller. | None outside tests/fixtures. | None. | unreviewed |
| `7315291` | remote workflow, web tests/config | Dormant candidate-source workflow definition. Because `pull_request_target` uses the base branch's workflow, this local candidate did not run its added web job; static tests cover the definition. It would become active only after reaching `main`. | No remote check for this local candidate. | None. | unreviewed |
| `ea05a5b`, then `2758d8b` | `engines.py`, `l3.py` | Physical-transition primitives are actively called by candidate L3 ownership; L2 still uses legacy ownership. | L3 operation/physical receipts and contained process effects. | Some old L3 turn handling, but no L2 process machinery. | unreviewed |
| `116d256` | config/route/L3/L2/L1/personas/tests | Active closure of autonomous execution paths to Codex. Claude adapters remain for read/stop/remove and legacy compatibility; every Claude print/start/resume entry fails the capability gate. | Changes provider selection and launches. | Some autonomous Claude branches; not the complete Claude implementation. | unreviewed |
| `f95b5f6`, `16270e9` | `manifest.py`, `legacy_preflight.py`, `server.py`, `bin/alt` | Startup manifest capture is active in `server.main`; `/api/manifest` exposes its public projection; `bin/alt manifest` and offline `preflight` are callable. Preflight is not wired into activation. | Startup manifest plus read-only inspection output; preflight performs no cutover. | None. | unreviewed |
| `f38c9c9`, `a1d42e8` | `deployment.py`, tests | Dormant by asserted design: no production importer/caller. | Would write deployment journal/qualification records if adopted; currently none through production. | None. | unreviewed |
| `2758d8b`, `ce7c46a` | `l3.py`, `l3_actions.py`, `engines.py`, `recovery.py`, `server.py` | Active candidate replacement for Codex L3 turn ownership and recovery reconciliation. | L3 operation records, physical receipts, action journal, chat/session projection, contained Codex unit. | Replaces much of current Codex L3 turn flow; current HTTP token streaming is also lost because `on_text` is not forwarded. | unreviewed |

Not present on this clean checkpoint: an L2 ownership change, an active-turn steering change,
adopted helper ownership, changed outcome/publication settlement, UI trimming, or legacy deletion.

The clean status proves only that the files are committed. It does not prove that this aggregate is
the right architecture or is merge-ready.

## 2. Preserved branches

| Branch | Tip | Classification | Notes |
| --- | --- | --- | --- |
| `docs/comprehensive-simplification-proposal` | `6dce69b` | historical proposal checkpoint | The proposal was called accepted before the later steering challenge; re-review it. |
| `simplify/architecture-clarity` | `b94f74e` | historical docs branch | Superseded by the integrated documentation sequence. |
| `simplify/phase0b-manifest` | `43c40a1` | component source branch | Earlier manifest/preflight source; integration contains a rebased/evolved form. |
| `simplify/phase0c-durable-io` | `0881aba` | component source branch | Earlier durable-state source; integration contains a rebased/evolved form. |
| `simplify/phase0d-contracts` | `fcb40c8` | component source branch | Earlier contracts source; integration contains a rebased/evolved form. |
| `simplify/phase0d1-outcome-observations` | `3325682` | component source branch | Outcome observation work; compare against integrated `768a47c`. |
| `simplify/phase0e-web-ci` | `ad3c780` | component source branch | Exact web-CI source; compare against integrated `7315291`. |
| `simplify/phase1a-claude` | `c0fa0e3` | component source branch | Codex-only closure source; compare against integrated `116d256`. |
| `simplify/phase1b1-physical` | `d2a4dd8` | component source branch | Shared physical transition source; compare against integrated `ea05a5b`. |
| `simplify/phase1b2-l3-ownership` | `c2606e6` | component source branch | L3 ownership source; integration contains follow-up fixes. |
| `simplify/phase1b3-integration` | `ea62105` | saved WIP with recorded steering objection | Two commits atop integration; see below. |
| `simplify/phase1b3-l2-ownership` | `a36fdcb` | superseded first-pass WIP | Preserved with explicit WIP commit; do not replay wholesale. |
| `simplify/phase1b3-l2-ownership-v2` | `d08fb3d` | superseded B3 attempt | Superseded by the later saved `simplify/phase1b3-integration` attempt, not by clean integration. |
| `simplify/phase1b4-helper` | `e5899e7` | stale ancestry; review evidence unavailable | Based directly at integrated `ff1fba0`, before deployment/clarity and L3/B3 ownership; reconstruct rather than replay. |
| `simplify/phase1c1-outcomes` | `5d0ce9c` | WIP marked unreviewed by its preservation commit | Direct parent is `f38c9c9`, before qualification/clarity/L3; reconstruct rather than replay. |
| `simplify/phase2a-deployment` | `2fe78b8` | component source branch | Earlier dormant deployment baseline. |
| `simplify/phase2b-qualification` | `f3407a9` | component source branch | Earlier dormant deployment qualification. |
| `safety-phase0c-deferred-domain-20260902` | `1304168` | safety preservation branch | Separate deferred durability work; inspect independently before reuse. |

### B3 steering-objection details

```text
branch: simplify/phase1b3-integration
base:   simplify/integration at ce7c46a
tip:    ea621055af47e383ac880479b1cff47c664cdbbd
range:  7db0a3b + ea62105
net:    36 files, +4,344/-1,577 relative to integration
status: clean after explicit preservation commit
```

- `7db0a3b` (`refactor: adopt durable L2 owner transition`) is +1,966/-1,535 across 29 files.
- `ea62105` (`wip: preserve unapproved L2 owner hardening`) is +2,545/-209 across 23 files.
- It implements an embedded owner operation, preparation and physical-transition receipts,
  background continuation, recovery fencing, and extensive hostile tests.
- When steering a nonterminal current owner, it persists a successor, stops the current worker,
  promotes the successor, repeats preparation, and creates a new physical generation. A terminal
  owner's next request is installed directly. The former interaction model has a recorded objection
  and is not mergeable pending the module review, regardless of how many race tests pass.

### Other WIP preservation commits

- `a36fdcb938ba45e013f3a31f4dbf00009fea6287` preserves the dirty older B3 first pass:
  30 files, +2,073/-1,358.
- `5d0ce9c2b60f3df0a37b0a3b9d944ac40e9a6e42` preserves the dirty C1 outcome draft:
  14 files, +767/-411.

The recorded parent-to-tip patches currently pass `git diff --check` for both preservation tips.
That is formatting evidence only.

### WIP implementation map

| Branch | Modules | Executable adoption | Durable records and effects | Review state |
| --- | --- | --- | --- | --- |
| `simplify/phase1b3-integration` | L2 ownership across `dispatch`, `engines`, `tasks`, `actions`, `server`, UI/contracts | Wired as an L2 ownership replacement on top of clean integration; nonterminal steering follows the successor flow above. | Owner operation, preparation/physical receipts, worker/result evidence, task/UI projections. | unreviewed; recorded steering objection; not mergeable |
| `simplify/phase1b3-l2-ownership` and `-v2` | Earlier versions of the same boundary | Superseded source material; do not infer current behavior from them. | Earlier owner/physical record drafts. | unreviewed and superseded |
| `simplify/phase1b4-helper` | `l1`, `actions`, `dispatch`, `tasks`, `status`, CLI | Dormant adoption foundation on `ff1fba0`: its helper aggregate/physical reconciler exists, but public action/CLI launch paths refuse before writing because the required B3 claimant/broker is absent. | Existing helper-bundle family, physical receipts, prompt/patch/result markers if later adopted; no production launch caller on this branch. | review evidence unavailable; stale ancestry |
| `simplify/phase1c1-outcomes` | `actions`, `contracts`, `dispatch`, `tasks`, `server`, L2 schema/persona, web contracts | Wired into server/action settlement, but processing requires B3 `require_owner_result`/claimant APIs absent from its `f38c9c9` parent and therefore refuses instead of settling a normal Codex result. | Adds task-local `outcomes.jsonl` plus `status.json.outcome_ref`; replaces pending-action/fallback identity paths in this draft. | preservation commit marks it unreviewed; incomplete ancestry |

These descriptions are call-path facts at the recorded tips, not endorsements. Reconstruct any
approved change from `main` after its owning module review; do not combine incompatible WIP tips.

## 3. Validation evidence actually available

The worktrees contain source, tests, proposal assertions, and migration gates. They do **not**
contain durable per-commit test logs or signed/structured reviewer reports establishing the result
of every previously reported run. Therefore:

- do not rely on conversational claims that a given count passed;
- rerun the relevant focused tests from the exact branch tip under isolated `ALTITUDE_HOME`;
- run the whole Python suite and web test/typecheck/build before considering a module merge;
- record the command, commit, environment, counts, and output artifact in the new review record;
- separately test the user-level flow the module changes.

The stop/successor design objection is independently reproducible from the saved source and
documentation, so it does not depend on a lost review transcript. It remains input to the next
module review rather than authority for a replacement design.

## 4. Suggested inspection commands

```bash
git log --reverse --oneline main..simplify/integration
git diff --stat main..simplify/integration
git diff main..simplify/integration -- altitude/state.py tests/test_durable_state.py
git diff simplify/integration..simplify/phase1b3-integration -- altitude/dispatch.py
git show --stat ea62105
git show --stat a36fdcb
git show --stat 5d0ce9c
```

Use a fresh branch from `main` for each accepted module. Copy or reconstruct only the reviewed
pieces; do not use the 17-commit integration branch as the automatic base for later work.
