# Brief — {slug} — {project}

**Goal:** {title}

**Task:** {engine} ({model}) owns this in branch `{branch}` of `{repo}`; task folder `{task_dir}`, read-only:
its records change through `alt`. Write drafts, patches and logs in your scratch folder `{scratch}`.
Expected files: {paths}. Other current tasks: {leases}.{overlaps}
The repository's Git settings, hooks and worktree records are read-only: create branches with `--no-track` and
pass a one-off setting with `git -c`.

**Delivery:** {merge_policy} Code delivery saves a schema-valid `report.json` (`{report_schema}`) with
`alt task save report FILE`; no-code work uses `alt task done`. Never restart or stop Altitude's own service or any managed
project's service, and never bind their reserved ports.

**Request:**

---

{request}
