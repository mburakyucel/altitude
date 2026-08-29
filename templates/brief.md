# Brief — {slug} ({class}) — {project}

**Goal:** {title}

**Definition of done:** every PR merged on `origin/main`; `main` run green; deployment healthy where applicable; review findings addressed or dismissed with reason; `report.md` + `report.json` (schema: `{report_schema}`) written to `{task_dir}`; roadmap in `{task_dir}/progress.md` complete.

**Change class / merge policy:** {merge_policy}

**Never (project never-list, from CLAUDE.md):** {never_list}

**Envelope (decision 31):** L1s in flight ≤ {l1_in_flight}; subagent launches ≤ {subagent_launches}; turns ≤ {max_turns}; verification: {verification}. Hitting a cap = checkpoint, write the report with `Blocked: envelope (needed N)`, stop.

**Questions:** none after this point. Take the documented default, record it under Deviations, continue if reversible; stop with `Blocked:` only for always-list items.

**Model tiers:** L2 = you; L1 = Opus-class for novel or wide-blast-radius sub-briefs, Sonnet-class for mechanical ones; reviewer on the other engine when available.

**Context:** the proposal below is approved{approval_note}. Repo: `{repo}`. Task folder: `{task_dir}` (write `progress.md`, `report.md`, `report.json` there). Worktree: this session runs in its own worktree/branch `{branch}`.

---

{proposal}
