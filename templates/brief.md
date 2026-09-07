# Brief — {slug} — {project}

**Goal:** {title}

**Owner:** one L2 ({engine}, {model}) owns this task end-to-end in `{branch}`. Implement directly, or delegate
bounded slices to your engine's own subagents when that materially helps. Any delegated result is input that
you own and integrate; it is not a transfer of responsibility.

**Definition of done:** every code change goes through a PR; {merge_policy} The applicable repository
checks pass and the merged result is verified where relevant. {completion_contract}
Where the repository has no CI, run the full local test suite on the exact merge candidate.

**Judgment-based execution:** use the lightest sound approach. Direct implementation is normal. Delegate
bounded parallel work, focused research, or an independent perspective only when useful.
Review is optional unless risk, uncertainty, or this brief requires it; appropriate testing is always
required.

**Direct task conversation:** Burak's messages queue on the task and reach you in this same provider session.
{conversation_contract}

**Hard boundaries:** {never_list}

**Ownership and isolation:** repository `{repo}`; task folder `{task_dir}`; worktree branch `{branch}`;
lease `{paths}`. Other current task leases: {leases}. Stay within your lease. Never restart or stop the
`altitude` or `tutor` services and never bind their reserved ports. {publication_contract}

**Request:**

---

{request}
