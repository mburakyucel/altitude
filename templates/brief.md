# Brief — {slug} ({class}) — {project}

**Goal:** {title}

**Definition of done:** R-014 currently prevents landing. Publish the PR, obtain a fresh clean review bound to its exact head, then write a blocked report with `Blocked: trusted remote landing integration pending` and stop. Local tests and generic GitHub check success are developer feedback only and cannot authorize merge or clean close. Do not claim merged/main/deployment completion until the base-attached `trusted-remote / evidence` consumer is integrated.

**Report schema:** `{report_schema}`

**Review/fix:** [R-016] For a fix round on an open PR, run `alt l1 run --cwd <the task worktree that holds the PR branch> --brief <fix brief>` with the PR number and findings named in the fix brief, then check the amended PR with `git diff --stat origin/main <head>` before requesting a fresh exact-head review.

[R-017] A post-fix review checks the dispositions of the previous round's findings and the fix diff for regressions. Only a new finding that defeats the definition of done or breaches R-006 blocks; every other new finding is recorded under **Follow-ups** with its file and line, and the L2 names or files the follow-up task in the report. One fix round per S task; a second round needs the L3's envelope answer, never a silent further reviewer.

**Change class / merge policy:** {merge_policy}

When the task carries a merge hold, preserve its reason as an additional blocker. R-014 already forbids every merge path; never treat `report ok`, a generic check, or the absence of checks as landing authority.

**Never (project never-list, from CLAUDE.md):** {never_list}

**Envelope (decision 31):** L1s in flight ≤ {l1_in_flight}; subagent launches ≤ {subagent_launches}; turns ≤ {max_turns}; verification: {verification}. Hitting a cap = checkpoint, write the report with `Blocked: envelope (needed N)`, stop. Caps are ceilings, not plans: start with **one** L1 and fan out only after it lands (decision 40).

**Questions:** none after this point. Take the documented default, record it under Deviations, continue if reversible; stop with `Blocked:` only for always-list items.

**Engines and tiers (decisions 38, 45):** L2 = you ({model}). L1 implementers and the reviewer run through `alt l1 run` — one plain command that makes the worktree, picks the engine (Claude or Codex) by remaining quota and returns JSON with the PR; {engine_line} Tier per sub-brief: coding at Opus/Codex by default; Fable only for novel, design-heavy or wide-blast-radius coding (`--engine claude --model fable`). Never spawn L1s with the Agent tool.

**Parallel work (decision 39):** your lease: {paths}. Other L2s running in this project right now: {leases}. Stay inside the lease and stop with `Blocked: lease` instead of racing another holder. Publish with `alt land --message "<msg>"`, obtain a fresh clean review bound to that exact PR head, optionally record durable intent with `alt task request-merge {slug} --pr <number>`, then write `Blocked: trusted remote landing integration pending` and stop. The daemon does not currently run a local gate, adopt, or merge. Never run `alt land --merge`, `gh pr merge`, or a GitHub merge API; local tests and generic GitHub checks cannot authorize landing.

**Context:** the proposal below is approved{approval_note}. Repo: `{repo}`. Task folder: `{task_dir}`. Worktree: this session runs in its own worktree/branch `{branch}`.

{checkpoint_guidance}

**Orientation:** use `alt task status {slug}` — one JSON with task state, worktree and branch, PRs and their checks, the `main` run, live envelope counts, leases and holds. Never read transcripts, hook counters (`~/.altitude/monitor/*.json`) or `claude agents` yourself.

---

{proposal}
