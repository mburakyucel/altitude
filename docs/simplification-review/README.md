# Simplification review checkpoint — 2026-09-02

This directory is the handoff for a new, module-by-module review of Altitude. It records facts and
open questions; it does **not** approve the comprehensive proposal or any unmerged implementation.

## Start here

The authoritative runtime source at this checkpoint is:

```text
branch: main
commit: 97e11979bdc0814ad5067eab717f999d1c251437
remote: origin/main at the same commit when this checkpoint was created
worktree: /home/burakyucel/Projects/altitude
```

`main` was clean. The Altitude service was stopped before the simplification implementation work
and was not restarted during this checkpoint.

Read these documents in order:

1. [Current system](CURRENT_SYSTEM.md) — what `main` actually does.
2. [Low-level flows](FLOWS.md) — call paths and durable/effect boundaries in current and candidate code.
3. [Candidate work](CANDIDATE_WORK.md) — exact local branches, commits, validation evidence, and recorded objections.
4. [Module review inventory](MODULE_REVIEW.md) — the one-module-at-a-time review queue.

The existing [architecture](../ARCHITECTURE.md) and
[session lifecycle](../SESSION_LIFECYCLE.md) remain descriptions of `main`, not of the unmerged
simplification branches.

## What this checkpoint changed

This branch changes documentation only: `README.md`, `CLAUDE.md`, the three canonical documents
under `docs/`, and the six files in this directory. It changes no production code, merges no
implementation, and did not start Altitude. Some files record private operational and security
boundaries; do not publish this branch without Burak reviewing that disclosure.

## Current decision status

No implementation from the comprehensive simplification is approved for merge under the new
module-by-module review requirement. In particular:

- `simplify/integration` is a clean local checkpoint, but its 67-file, +17,241/-1,053 aggregate
  must be decomposed and re-evaluated one module at a time.
- `simplify/phase1b3-integration` is saved WIP with a recorded stop/promote steering objection. For
  a nonterminal current owner, it persists a logical successor, repeats preparation, and promotes a
  new physical generation. It is not mergeable pending module review.
- the older B3, helper, and outcome branches are preserved only as source material. They must not
  be replayed or merged wholesale.
- the Codex App Server `turn/steer` idea is an **unimplemented alternative for evaluation**, not an
  architectural decision. Its experimental protocol, isolation, reconnection, delivery ambiguity,
  and process ownership must be reviewed before any code is written.

## Fresh checkpoint validation

These commands were run on this docs-only branch from `main` on 2026-09-02:

| Command | Result |
| --- | --- |
| `python3 -m unittest discover tests` in the restricted workspace sandbox | 489 discovered; 483 passed and the six loopback HTTP cases failed to create a socket with `PermissionError` |
| `python3 -m unittest tests.test_server_head` with loopback permission | 6/6 passed |
| `pnpm install --frozen-lockfile` | lockfile current; 171 packages reused from the local store |
| `pnpm test` | 8 files, 39/39 tests passed |
| `pnpm typecheck` | passed |
| `pnpm build` | passed; 238 modules transformed |
| `git diff --cached --check` after staging all checkpoint files | passed |
| `python3 -m json.tool docs/simplification-review/checkpoint.json` | passed |

The split Python run is recorded rather than hidden: the first command's six errors were caused by
the execution sandbox forbidding loopback socket creation, and the same six tests passed when that
environment restriction was removed. These results validate `main` plus documentation only; they
do not validate any `simplify/*` implementation branch. This table is the durable summary; raw
command-output artifacts from these runs were not preserved.

## Safe continuation commands

Inspect the current runtime:

```bash
cd /home/burakyucel/Projects/altitude
git status --short --branch
git rev-parse HEAD
```

Inspect the clean accumulated candidate without changing `main`:

```bash
cd /tmp/altitude-simplify-integration
git status --short --branch
git log --reverse --oneline main..HEAD
git diff --stat main..HEAD
```

Inspect the saved B3 WIP with the recorded steering objection:

```bash
cd /tmp/altitude-phase1b3-integration
git status --short --branch
git log --oneline simplify/integration..HEAD
```

If `/tmp` worktrees no longer exist, recreate a disposable worktree from a saved branch, for
example:

```bash
git -C /home/burakyucel/Projects/altitude worktree add \
  /tmp/altitude-review-b3 simplify/phase1b3-integration
```

Do not start by rebasing, squashing, deleting branches, or replaying the integration range.

**No next module has been selected and no simplification target is approved. Ask Burak which module
to review next. Do not implement or merge a module until its decision and exact scope are recorded.**
The grouping in [Module review inventory](MODULE_REVIEW.md) is informational, not an approved sequence.

## Review rule

For each module, answer in this order:

1. What user-visible or safety behavior does the current module provide?
2. Which callers, durable records, external effects, and tests depend on it?
3. Is the behavior still wanted?
4. If yes, can it be expressed with fewer owners, states, artifacts, or compatibility paths?
5. What code, if any, can be removed without losing required behavior?
6. What test or end-to-end observation proves parity?

“Simpler” is not sufficient evidence on its own. Likewise, an existing test is not sufficient
evidence that a mechanism remains desirable.
