# Brief — {slug} ({class}) — {project}

**Goal:** {title}

**Definition of done:** every PR merged on `origin/main`; `main` run green; deployment healthy where applicable; review findings addressed or dismissed with reason; `report.md` + `report.json` (schema: `{report_schema}`) written to `{task_dir}`; roadmap in `{task_dir}/progress.md` complete.

**Change class / merge policy:** {merge_policy}

**Never (project never-list, from CLAUDE.md):** {never_list}

**Envelope (decision 31):** L1s in flight ≤ {l1_in_flight}; subagent launches ≤ {subagent_launches}; turns ≤ {max_turns}; verification: {verification}. Hitting a cap = checkpoint, write the report with `Blocked: envelope (needed N)`, stop. Caps are ceilings, not plans: start with **one** L1 and fan out only after it lands (decision 40).

**Questions:** none after this point. Take the documented default, record it under Deviations, continue if reversible; stop with `Blocked:` only for always-list items.

**Model tiers (decision 38):** L2 = you ({model}). L1s are a per-sub-brief decision: **Opus for coding by default; Fable for novel, design-heavy or wide-blast-radius coding** (no pattern in the repo to copy, or many callers / user-visible); **Sonnet for pure research and documentation sub-briefs** (reading docs, surveys, changelogs). Reviewer = Opus or the other engine. Never Fable for research or docs.

**Parallel work (decision 39):** your lease: {paths}. Other L2s running in this project right now: {leases}. Stay inside your lease; if you must touch a path another running task holds, stop with `Blocked: lease` rather than racing it. Never restart/stop the `altitude` or `tutor` units, never bind ports 8890/8080/8443 (a hook blocks these) — for a smoke test run `ALTITUDE_TIMERS=0 ALTITUDE_HOST=127.0.0.1 ALTITUDE_PORT=<ephemeral> bin/alt serve` and report "needs `systemctl --user restart altitude`" after merge.

**Context:** the proposal below is approved{approval_note}. Repo: `{repo}`. Task folder: `{task_dir}` (write `progress.md`, `report.md`, `report.json` there). Worktree: this session runs in its own worktree/branch `{branch}`.

---

{proposal}
