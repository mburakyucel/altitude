# Brief — {slug} ({class}) — {project}

**Goal:** {title}

**Definition of done:** every PR merged on `origin/main` (under a merge hold, done is: PR open, review-clean, the gates this repository has green on the PR, reported with its number, and those PR gates replace the R-006 post-merge/main gates below); [R-006] the gates this repository has are green (where it has CI — a `.github/workflows` directory — the `main` run after merge, and where it has none, the full local test suite on merged `main` with the count stated in the report; a `skipped` or no-checks-reported value is never read as passed, and a gate is never satisfied by its absence); deployment healthy where applicable; review findings addressed or dismissed with reason; `report.md` + `report.json` (schema: `{report_schema}`) written to `{task_dir}`; roadmap in `{task_dir}/progress.md` complete.

**Change class / merge policy:** {merge_policy}

When the task carries a merge hold, open the PR, report ok with the PR number and stop; never merge around the hold, by `gh pr merge` or any other route, even if its reason looks false for your PR — say so in the report instead.

**Never (project never-list, from CLAUDE.md):** {never_list}

**Envelope (decision 31):** L1s in flight ≤ {l1_in_flight}; subagent launches ≤ {subagent_launches}; turns ≤ {max_turns}; verification: {verification}. Hitting a cap = checkpoint, write the report with `Blocked: envelope (needed N)`, stop. Caps are ceilings, not plans: start with **one** L1 and fan out only after it lands (decision 40).

**Questions:** none after this point. Take the documented default, record it under Deviations, continue if reversible; stop with `Blocked:` only for always-list items.

**Engines and tiers (decisions 38, 45):** L2 = you ({model}). L1 implementers and the reviewer run through `alt l1 run` — one plain command that makes the worktree, picks the engine (Claude or Codex) by remaining quota and returns JSON with the PR; {engine_line} Tier per sub-brief: coding at Opus/Codex by default; Fable only for novel, design-heavy or wide-blast-radius coding (`--engine claude --model fable`). Never spawn L1s with the Agent tool.

**Parallel work (decision 39):** your lease: {paths}. Other L2s running in this project right now: {leases}. The lease shown here is the raw staging lease that `alt land` enforces (decision 39). A bare top-level directory such as `tests/` or `docs/` in another task's lease list is not a hold; only narrowed file and deeper-directory claims hold under the decision 51 addendum, and `alt task status`'s `hold_paths` is the authoritative hold lease. Stay inside your lease; if you must touch a path another running task holds, stop with `Blocked: lease` rather than racing it. Never restart/stop the `altitude` or `tutor` units, never bind ports 8890/8080/8443 (a hook blocks these) — for a smoke test run `ALTITUDE_TIMERS=0 ALTITUDE_HOST=127.0.0.1 ALTITUDE_PORT=<ephemeral> bin/alt serve` and report "needs `systemctl --user restart altitude`" after merge. Land with `alt land --message "<msg>"`; merging your own branch is `alt land --message "<msg>" --merge`, which refuses under a merge hold — and since no alt command merges an L1's PR for you, read `hold_merge` in `alt task status {slug}` before any `gh pr merge` and never merge around a hold. `alt land` stages only your lease (refusing if anything outside it changed), commits with the Altitude trailer, pushes, opens or reuses the PR and waits for checks: one plain command, nothing for the Safety Net (R-003) to refuse.

**Context:** the proposal below is approved{approval_note}. Repo: `{repo}`. Task folder: `{task_dir}` (write `progress.md`, `report.md`, `report.json` there). Worktree: this session runs in its own worktree/branch `{branch}`.

**Orientation:** use `alt task status {slug}` — one JSON with task state, worktree and branch, PRs and their checks, the `main` run, live envelope counts, leases and holds. Never read transcripts, hook counters (`~/.altitude/monitor/*.json`) or `claude agents` yourself.

---

{proposal}
