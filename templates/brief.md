# Brief — {slug} — {project}

**Goal:** {title}

**Owner:** one L2 ({model}) owns this task end-to-end in `{branch}`. You may implement directly or use
zero, one, or several L1s when that materially helps. {engine_line} Any L1 result is input that you own
and integrate; it is not a transfer of responsibility.

**Definition of done:** every code change goes through a PR; {merge_policy} The applicable repository
checks pass, the merged result and deployment are verified where relevant, material findings are
addressed or dismissed with a reason, and `progress.md`, `report.md`, and schema-valid `report.json`
(`{report_schema}`) are complete in `{task_dir}`. Where the repository has no CI, run the full local test suite on merged `main`
and state the result. Skipped or absent required checks are not success.

**Judgment-based execution:** use the lightest sound approach. Direct implementation is normal. Use
L1s for bounded parallel work, focused research, or an independent perspective only when useful.
Review is optional unless risk, uncertainty, or this brief requires it; appropriate testing is always
required.

**Direct task conversation:** Burak's task-specific steering arrives in this session. Reply in plain
language with `alt task reply "<message>"`. Ask him directly only when the choice cannot safely be made
from this brief and the repository. Before stopping for an answer, checkpoint `progress.md`, record the
exact block, and send the question through `alt task reply`.

**Hard boundaries:** {never_list}

**Ownership and isolation:** repository `{repo}`; task folder `{task_dir}`; worktree branch `{branch}`;
lease `{paths}`. Other current task leases: {leases}. Stay within your lease. If an active task owns an
overlapping narrowed path, stop with `Blocked: lease` rather than racing it. Never restart or stop the
`altitude` or `tutor` services and never bind their reserved ports. Every code change uses the isolated
branch and a PR. Land with `alt land --message "<message>"`; use `--merge` only when allowed. Always
read the live `hold_merge` value before merging and never merge around the hold.

**Request:**

---

{request}
