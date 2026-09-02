# Reproducible baseline and target roster

## Counting contract

The baseline is commit `97e11979bdc0814ad5067eab717f999d1c251437`. Counts use the checked-in
bytes at that commit and GNU `wc -l` (newline characters), not formatted or generated output. Run
`git diff --exit-code` first, check out that exact commit in a disposable worktree, and pass each
newline-separated roster below to `wc -l`. The sum printed by `wc` is the recorded metric.

Classification is closed:

- permanent runnable source is the backend/CLI/hook/restart roster, non-test web source roster, and
  web build/config roster;
- personas, schemas, templates, service/CI/build support, and tests are reported separately and
  cannot absorb runnable behavior;
- `web/pnpm-lock.yaml`, generated `web/dist`, dependencies, bytecode, screenshots, and runtime state
  are excluded; web test files and `schemas/fixtures/projections.v1.json` are test source reported
  separately, while `web/README.md` is explicitly classified as non-runtime documentation;
- candidate discovery recursively scans every regular file under runtime-consumed `web/src`,
  `web/public`, and `web/design`, plus every regular web-root file except the named pnpm lockfile;
  suffixes never decide whether an asset or config is inventoried;
- a new file is classified by what executes or consumes it, not its directory or extension; a file
  serving two categories goes in the stricter runnable category;
- a renamed/split file remains counted; deletion is the only way it leaves a roster; and
- temporary real-state importers are reported separately by file/line and are excluded from the
  permanent target only while they have no normal-runtime selector and a named post-activation
  deletion PR. They may not be deleted before the successful production cutover receipt.

Every implementation PR reports baseline-parent and candidate counts using this same contract. A
reviewer rejects an unclassified new production path.

## Baseline permanent runnable source

### Backend, CLI, hooks, and restart: 10,695 lines / 32 files

```text
altitude/__init__.py
altitude/actions.py
altitude/config.py
altitude/digest.py
altitude/dispatch.py
altitude/engines.py
altitude/git_policy.py
altitude/github_intake.py
altitude/incidents.py
altitude/l1.py
altitude/l3.py
altitude/l3_actions.py
altitude/land.py
altitude/monitor.py
altitude/quota_codex.py
altitude/recovery.py
altitude/route.py
altitude/server.py
altitude/state.py
altitude/status.py
altitude/tasks.py
altitude/transcript.py
altitude/verify.py
bin/alt
hooks/edit_count.py
hooks/guard.py
hooks/pre-commit
hooks/pre-merge-commit
hooks/pre-push
hooks/reference-transaction
hooks/statusline-monitor.sh
scripts/restart_altitude.py
```

### Non-test web source: 2,683 lines / 15 files

```text
web/src/data/Toast.tsx
web/src/data/api.ts
web/src/data/useOptimisticMutation.ts
web/src/main.tsx
web/src/routes.tsx
web/src/routes/Chat.tsx
web/src/routes/Inbox.tsx
web/src/routes/LiveSession.tsx
web/src/routes/Monitor.tsx
web/src/routes/Project.tsx
web/src/routes/Projects.tsx
web/src/routes/Task.tsx
web/src/shell/AppShell.tsx
web/src/shell/theme.tsx
web/src/styles.css
```

### Web build/config source: 195 lines / 5 files

```text
web/design/tokens.css
web/index.html
web/package.json
web/tsconfig.json
web/vite.config.ts
```

Their individual baseline counts are 82, 32, 38, 19, and 24 lines in roster order.

The permanent runnable baseline is therefore **13,573 lines / 52 files**.

## Separately reported baseline categories

- Personas/schemas/templates: **496 lines / 13 files** (`personas/*.md`, top-level
  `schemas/*.json`, `templates/*.md`).
- Service/CI/build support: **134 lines / 3 files** (`systemd/altitude.service`,
  `.github/workflows/remote-tests.yml`, `Makefile`).
- Python tests: **10,258 lines / 54 files** (`tests/*.py` at the baseline commit).
- Web tests/harness: **1,097 lines / 10 files** (the eight `*.test.ts[x]` files,
  `web/src/test/render.tsx`, and `web/src/vitest.setup.ts`).
- Baseline execution: **489 Python tests and 39 web tests**, plus web typecheck and production
  build. Test source may grow; removing tests does not improve a production metric.

## Normative permanent target roster

This is the permanent target ownership roster. A candidate may retain an equivalent current filename
only when it preserves the listed single responsibility; it must explain any additional file and
remain under the **43-file** and line budgets. The target below has **43 permanent runnable files**.

### Backend/CLI/hook/restart target: 24 files

```text
altitude/__init__.py
altitude/brokers.py                 # replaces parallel actions + l3_actions
altitude/commands.py                # six closed in-process command domains
altitude/config.py
altitude/contracts.py
altitude/deployment.py
altitude/dispatch.py
altitude/engines.py
altitude/git_policy.py
altitude/incidents.py               # structured evidence; intake folded into task command
altitude/l1.py
altitude/l3.py
altitude/manifest.py
altitude/projections.py             # replaces monitor + status + digest read projections
altitude/recovery.py
altitude/routing.py                 # replaces route + quota_codex
altitude/server.py
altitude/settlement.py              # replaces land + verify
altitude/state.py
altitude/tasks.py                   # includes immutable GitHub intake
altitude/transcript.py
bin/alt
hooks/git-boundary                  # one installed dispatcher for four Git hook names
scripts/restart_altitude.py
```

### Web target: 14 files

```text
web/src/data/Toast.tsx
web/src/data/api.ts                 # includes the small optimistic-mutation helper
web/src/data/contracts.ts
web/src/main.tsx
web/src/routes.tsx
web/src/routes/Chat.tsx
web/src/routes/LiveSession.tsx
web/src/routes/Monitor.tsx
web/src/routes/Project.tsx
web/src/routes/Projects.tsx
web/src/routes/Task.tsx
web/src/shell/AppShell.tsx
web/src/shell/theme.tsx
web/src/styles.css
```

### Web build/config target: 5 files

The five baseline web build/config files remain. Dependency lockfiles remain excluded generated
resolution input, but changes to them are still reviewed and tested.

### Persona/schema/template target: 10 files

```text
personas/l1.md
personas/l2.md
personas/l3.md
personas/reviewer.md
schemas/actions.json
schemas/outcome.json
schemas/review.json
templates/brief.md
templates/incident.md
templates/pr.md
```

Rendered-prompt snapshots must prove consolidation preserves provider restrictions before the
separate `l2_codex.md`/`l3_codex.md` files retire. Pending actions drain or migrate before the two
old action schema files retire. Trusted receipt schemas remain Python-owned and never enter model
action/outcome JSON.

`hooks/guard.py` and `hooks/statusline-monitor.sh` are temporary migration checks, not permanent
target files. They remain through the first successful ActivationReceipt for the legacy-empty proof
and are deleted with retired Claude settings in PR 10B. The four native Git hooks are installed as
links or copies of the one `hooks/git-boundary` dispatcher; installation parity is a merge gate. If
parity fails, migration stops for redesign rather than adding four permanent source stubs.

The roster deliberately removes digest/TTS, edit counting, durable Inbox, duplicate brokers,
duplicate monitor/status projections, separate quota/router modules, separate land/verify modules,
public task-intake duplication, and four copy-pasted Git-hook source stubs. It adds only the canonical
command, contract, deployment, manifest, and projection boundaries required by the target design.
Line budgets still control: meeting 43 files does not excuse oversized replacements.
