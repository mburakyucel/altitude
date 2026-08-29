# Before building — a critical look at adopt vs fork vs build

*2026-08-29. Written before any code, at Burak's request to think critically rather than agreeably. Independent reviews from a Codex session and a separate Claude Code session are appended at the end.*

> **Outcome (Burak, same day):** read and rejected the two-week experiment and the tool evaluation — "I don't have time for this; the vision is clear." Decision 15 is settled: **build**. The analysis stays because the metrics in §2 are the instrumentation `altd` should collect, the staged order in §4 is the build order, and the suspicions in §5 are the persona-tuning checklist.

## 1. Are the viral tools actually beneficial?

Honest read of the evidence: **nobody has published a measured productivity gain for agent-orchestration tools; what is measurable is token consumption.** Gas Town's author calls his own system a "clown show" that burns enormous tokens; OpenClaw's 388k stars come from being a personal-assistant chat shell, not from coding organization; Paperclip sells the "AI company" fantasy (CEO agent hires managers). The dominant design goal across the viral tools is **more autonomy** — more agents, longer unattended loops, bigger swarms. That is the opposite axis from Burak's problem. His bottleneck is **organization**: too many things asking for attention at the wrong altitude. A tool that multiplies agents multiplies exactly that load unless it also filters. Very few tools filter; Paperclip's Decisions feed and Claude Code's "push only when action required" toggle are the only shipping examples found, and both are recent.

So the critical framing is: **the problem is an information-architecture problem, not an autonomy problem.** The fix is mostly discipline about *what reaches Burak*, and only secondarily software.

## 2. Evidence from Burak's own runs (career-platform, 2026-08-24 → 08-29)

Nine orchestrator runs. Their "Report for Burak" sections average ~12 bullets, the largest 31. The latest run (`orchestrator-study-wireframe-sync-20260828-0136.md`) put **14 numbered "wireframe concerns"** in front of Burak plus 26 report bullets. Classified by the Decision/FYI rule in `VISION.md`, roughly **two** needed him (whether "Up next" may auto-advance — a product behavior; whether the "Gap N" ranking may change the T7a membership rule); the other twelve are FYIs ("docked bar is sticky, not in-flow") or follow-up issues already filed (#126–#130). That is ~85% of the reading load removable by a classification rule that costs nothing to add to the orchestrator persona today. It also shows the L2s are *already* good at surfacing concerns — the missing piece is triage, not more reporting.

Baseline these this week, from the existing progress files and Burak's own log, before writing code:

| Metric | How to get it now | Continue only if, after 2 weeks of the discipline (§4 stage 0–1)… |
|---|---|---|
| Items put in front of Burak per landed PR, split Decision / FYI | count report bullets + concern lists per run; classify by hand | Decisions ≤ 3 per run and FYIs no longer require a reply |
| Interruptions per day (a session waiting on him) | `claude agents` "Needs input" count, sampled 3×/day; or count of "ask Burak" lines in progress files | drops by half, or the remaining ones are genuinely executive |
| Minutes/day in terminals babysitting sessions | honest self-log, one number a day | drops noticeably; if not, the software would not have fixed it either |
| Tasks in flight concurrently without losing track | count of open orchestrator progress files + open PRs | goes up without the interruption count going up |
| Tokens per landed PR | `total_cost_usd` / usage from session JSONL, per task | flat or down (validation adds cost; rotation and answered-from-docs should save more) |

## 3. Adopt, fork, or build — critically

**Adopt as-is.** Paperclip is the only candidate whose *purpose* overlaps (governance, attention feed). It would give an inbox and approvals on day one. Against: it optimizes for a company metaphor Burak does not want (CEO agents, hiring, org charts), its Claude adapter defaults to skipping permissions, it has no context hygiene (Burak's second-most-felt pain), and adopting a 79k-star project means inheriting its roadmap. Verdict: **evaluate for one afternoon, adopt only if the inbox feels right; do not build on it.** OpenClaw: no — size, security record, and its subscription path is documented as a text-only fallback. Native Claude Code features: **yes, adopt everything they offer** (Remote Control, `AskUserQuestion` on the phone, `claude agents`, cross-session messaging, http hooks, statusline context/quota) — they are free, first-party, and moving in this direction fast.

**Fork.** Worst of both worlds for anything large: a fork of Paperclip or OpenClaw means maintaining a big, fast-moving codebase without controlling it. Forking is only sensible for tiny things (claude-evolve's rule-fitness idea, claw-orchestrator's reviewer council), and there "borrow the idea" beats forking.

**Build.** The parts nobody has built are small and are the parts that matter here: Decision/FYI triage at L3, L3 answering L2 questions from project docs, rotation before degradation, cross-engine validation by default, rule provenance. Each is a persona rule, a schema, or a few hundred lines. The danger is not that building fails; it is that **building becomes the project** — a meta-tool that consumes the attention it was meant to save, in a space where the vendors are visibly moving up the stack (three of the primitives in `BUILDING-BLOCKS.md` shipped in the last ten weeks; a "manager of sessions" console and Dispatch already exist). A large custom product risks being obsolete by the time it is polished. A thin one — conventions plus a supervisor the size of `server.py` — does not.

Verdict: **build, but thin, staged, and measured; decide about "product" and "open source" only after it has run on three projects for a month.**

## 4. The staged plan, with the cheapest thing first

- **Stage 0 — discipline only, no software (1 day).** Add the Decision/FYI rule and the report contract to the orchestrator persona in career-platform: every "Report for Burak" ends with `## Decisions (need you)` (≤3, one question + default each) and `## FYI`. A cron/script aggregates those sections across projects into one page on the pocketbook server (and Kokoro reads the Decisions). **Zero model calls in the aggregator.** This alone tests the core hypothesis — that triage, not more agents, is the fix — against the metrics above.
- **Stage 1 — L3 as a persona (1–2 days).** Run L3 interactively under `claude remote-control` on career-platform with `personas/alt.md` and a state directory; Decisions as `AskUserQuestion`; L2s via `claude --bg`. Measure whether L3 answering L2 questions from `DECISIONS.md`/`ENGINEERING.md` removes interruptions and whether its digests are at the right altitude. This is the go/no-go on the model-based L3: if a deterministic inbox (stage 0) plus talking to L2s directly is already most of the gain, L3 is a nice-to-have and the product idea is weaker than it looks.
- **Stage 2 — only what stage 1 proved necessary.** Supervisor (rotation, quota pause, webhook), rule ledger, validation-by-default, the page. Each justified by a metric that did not move in stage 1.
- **Stage 3 — product/OSS decision.** Only if the residual is generic and three projects use it.

## 5. Where our own design deserves suspicion

- **A model-based L3 is a third place hallucinations can enter.** Its unique value (answering L2s from docs, altitude-correct summaries, the improvement loop) is real but unproven; stage 1 is designed to test it against a deterministic inbox, and the git-verification step keeps it honest.
- **Self-improvement is the most speculative idea here.** There is no evidence that agent-written rules improve outcomes over time; the provenance ledger makes it *auditable*, not *effective*. Expect modest gains; keep it low-frequency and gated at first.
- **Validation costs tokens on every task.** Burak has quota to spare, so the trade is fine — but keep the per-class opt-out and watch tokens-per-PR.
- **Rotation may become native.** Auto-compact and `PreCompact` hooks are improving; build rotation as a thin supervisor feature, not a framework.
- **The rule ledger can become ceremony.** Five fields, enforced by a CLI, one audit a week — no more.

## 6. Independent reviews

### 6.1 Claude Code review (headless session, 2026-08-29, brief: skeptical principal engineer)

What was adopted from it is recorded in `DECISIONS.md` #5 (revised), #16 (revised), #20, and `ROLES.md` (L2 question timing). Its recommendation to delay L3 for a two-week L2-only experiment was overruled by Burak's decision 15.

#### 1. Does it solve the stated problem?

Partly. The two mechanisms that cut load are in VISION "Why this and not just more orchestrators": L3 answering L2 questions from project docs, and one ranked inbox. The rest costs attention before it saves any.

Where the load goes: (a) persona tuning, which ARCHITECTURE "Honest risks" #1 admits is the product; (b) checking L3's judgment — every question it answers "from docs" was your decision; now a model makes it, and errors surface in merged PRs (§5's `qa.md` log is after the fact); (c) FYIs — "happened today: 9" (§6) is nine notifications, context-switching under another name; (d) `altd` itself — ten projects, ten sessions to keep alive and rotate.

Bluntly: the design fixes altitude by *routing* low-level questions through a new layer instead of *preventing* them with an L2 brief rule ("decide, record a deviation; questions only at proposal time"). That experiment appears in no doc.

#### 2. Build vs adopt vs fork

Adopt native Claude Code as-is for four weeks; build nothing. LANDSCAPE's scorecard says native covers A, B, C, H and the custom delta is "small". `claude agents` is the inbox (Needs input / Working / Ready for review), Remote Control + `AskUserQuestion` the tappable gate, `SendMessage` the bus. The one artifact worth writing is `personas/alt.md` on an ordinary interactive session — phase 0 minus the `cos` CLI.

Not Paperclip: `dangerouslySkipPermissions` default, manual session reset, org-chart metaphor — a different product; two evenings only to see a governance UI. Not OpenClaw, for LANDSCAPE's reasons. Not a fork of a 79k-star repo for one user. The design itself has the viral-tool smell it distrusts: many sessions, many FYIs, gains unmeasured.

#### 3. Top 5 flaws or risks

1. **L3 as a supervisor-kept stream-json session** (§3, §7; DECISIONS 16). RC on `-p --input-format stream-json` is "still to verify" (§6); `altd`'s keep-alive and rotation exist only to serve that choice. Fix: adopt DECISIONS 16's own fallback — L3 is an interactive `claude remote-control` session (GA), `STATE.md` its memory, `--resume` its restart.
2. **Checkpoint-and-exit per question** (§5; DECISIONS 16). Each mid-run question costs a handoff, an idle-stop, and a resume into a worktree with in-flight subagents lost — Paperclip's open stale-resume bug, reproduced by design. Fix: forbid mid-run questions in the brief.
3. **Ungated L3 answers** (§3, §5). The biggest claimed saving is the biggest new failure mode. Fix: for the first 20 tasks, every CoS-answered question is an FYI quoting the answer.
4. **Session multiplication on one seat** (§11; DECISIONS 13; risk #4). Proposal + critique + L2 + per-PR review + verification + audits + rotations ≈ 4–6 sessions per task. Exit code 2 mid-turn leaves half-written checkpoints "pause dispatch" cannot recover. Fix: validation opt-in by class; measure 5h/7d usage first.
5. **The gate is policy, not structure** (§2; DECISIONS 5). "A 'go' typed in chat is also honored", so "structurally impossible" holds only for high-impact classes. Fix: require `AskUserQuestion` for every approval; zero build.

#### 4. Over-engineered — cut from v1

- **Rule ledger, weekly audit, skill-usage counting** (§9; DECISIONS 10, 14). Best idea, second product: schema CLI, transcript mining, retro-tagging, PRs to `CLAUDE.md` that career-platform already classes high-impact, so they are Decisions anyway. Cut to `observations.md`, read weekly.
- **Supervisor-driven L2 rotation at 55/70%** (§10). L2s already survive compaction via the `SessionStart(compact)` hook; transcript-tailing rides an undocumented format (risk #6).
- **`altd`, the page, Kokoro, Telegram** (§6–7, phases 1–2). RC plus cron exists.
- **Engine adapter and Codex L2** (§8). One implementation is not an abstraction.
- **`cos` with 11 subcommands and three concurrent writers** (§2). A `status.json` per task covers phase 0.

#### 5. What is missing

- **The prevention experiment**: change the L2 brief before adding a layer above it.
- **Phase 0 exit criteria**. "Judge the altitude" needs a scorecard: escalated / answered / should-have-escalated, per task.
- **A kill criterion**. Principle 10 makes the tool reversible; nothing says when to reverse.
- **Recovery spec** for `altd` dying mid-dispatch or a missed webhook; Symphony's stateless recovery is cited, not adopted.
- **Budget unit**. Briefs say "token budget", flags say `--max-budget-usd`, the seat meters 5h/7d percent. Pick one.
- **Policy check** settled before phase 0, not left open.

#### 6. Measurable gains — baseline this week

Career-platform has 44 progress files (orchestrators dated Aug 24–28): nine `orchestrator-*` with "Decisions (recorded where)" and "Report for Burak" sections, nine review/integration files. Baseline:

1. **Questions to Burak per task, by altitude** — count from those sections; tag answerable-from-docs vs executive. Continue only if ≥50% were answerable from docs; below 30% the CoS cannot help.
2. **Wait time per question** — file timestamps and `gh pr view` created/merged. If the median is already minutes, gains exist only during commutes.
3. **Sessions and seat usage per task** — compactions/re-injections per orchestrator, end-of-day 5h/7d percent. Decides whether validation-by-default fits.
4. **Tasks started per week** — nine orchestrators in five days now. After four weeks: double, at equal-or-fewer questions per task, no rise in deviations.

#### 7. Verdict

Go-with-changes, where the changes are mostly deletions: run phase 0 as a persona on an interactive Remote Control session with `claude agents` — no `altd`, page, ledger, rotation, or second engine — for four weeks against the baselines above. The single most important change: fix altitude at L2 first (brief rule, questions only at proposal time) for two weeks *before* adding L3. If that works, the layer is unnecessary; if not, you will know exactly which questions L3 must answer.

### 6.2 Codex review (headless `codex exec`, 2026-08-29, same brief)

Adopted: `ARCHITECTURE.md` rewritten as a single authoritative v1 with deferred design in an appendix; L3 answers must cite the controlling decision and ambiguous/conflicting docs escalate; kill threshold tightened to 10%; a deterministic cross-project Decision queue with WIP limits; state invariants (single writer, atomic transitions, stable dispatch ids, reconciliation); validation tiered by class; a labeled gold set of past Decision/FYI items as a phase-0 build artifact. Its one-week L2-only control was overruled by decision 15, though the L2 proposal-time question rule ships in phase 0 regardless.

#### 1. Does it solve the stated problem? The author's bottleneck is ORGANIZATION (keeping ~10 parallel agent sessions coherent, being asked low-level questions, context-switching), not token quota. Does this design reduce that load, or does it add a layer that creates new coordination and new failure modes? Be concrete about where the load actually goes.

Partly. The useful mechanisms are `VISION.md` → “Two kinds of message” and `ROLES.md` → “The one rule that decides what reaches Burak”: batch questions, answer doc-settled ones below Burak, and separate Decisions from FYIs.

Everything else relocates load. Burak now calibrates L3, audits its misclassifications, resolves stale mirrored state, and diagnoses dispatch/resume failures. Ten L2s become ten L2s plus an L3, validators, and eventually `altd`. “Summaries of summaries” hides errors rather than eliminating them. Unless FYIs are batched and questions are prevented at L2, the new inbox is just ten terminals rendered differently.

#### 2. Build vs adopt vs fork.

Build only a thin policy layer on native Claude Code. `LANDSCAPE.md`’s “Scorecard” shows native already supplies session management, phone access, approvals, and cross-session messaging. Paperclip adds an unwanted company ontology, unsafe defaults, and weak context hygiene. OpenClaw brings enormous churn, security exposure, and subscription-policy ambiguity. Forking either inherits those liabilities.

The custom v1 should be only personas, the Decision/FYI contract, one canonical status view, and an interactive Remote-Control L3. Do not build a general orchestration platform until this proves it reduces attention. Viral activity is not evidence of useful throughput.

#### 3. Top 5 flaws or risks in the design as written (ARCHITECTURE.md / DECISIONS.md). For each: what breaks, when, and the cheapest fix.

1. **There are two incompatible architectures.** `ARCHITECTURE.md` “One paragraph” and §§2–8 require stream-json L3, `cos`, `altd`, mirrored artifacts, and an adapter; `DECISIONS.md` #16/#20 replace or defer them. Approval rules also conflict. Implementation will follow whichever paragraph somebody last read. Cheapest fix: rewrite `ARCHITECTURE.md` as the authoritative v1 and move deferred design to an appendix.

2. **L3 becomes an unchecked decision maker.** Under “The CoS session,” it interprets project docs and silently answers L2. Ambiguous or stale documentation then becomes merged behavior. Cheapest fix: every answer cites the controlling decision; conflicts always escalate; audit all answers for 20 tasks and require under 10% error—not the dangerously permissive 30% kill threshold.

3. **There is no portfolio coordinator.** One L3 per project does not prioritize or limit work across projects, although `VISION.md` promises one status view across them. Burak still reconciles several L3s. Cheapest fix: a deterministic global Decision queue and WIP limit, with no additional model.

4. **Recovery and concurrency are underspecified.** `ARCHITECTURE.md` §§1–2 allow state outside the repo, mirrored artifacts, multiple transition callers, webhooks, and direct L2 steering. A crash between dispatch and status update can duplicate work or lose the task. Cheapest fix: one canonical writer, atomic transitions, stable dispatch IDs, and periodic reconciliation against `claude agents`, git, and GitHub.

5. **Validation and self-improvement multiply activity.** §§9–11 add critics, reviewers, verifiers, audits, rotations, and rule PRs. Under quota pressure or false-positive reviews, work slows while dashboards look busy. Cheapest fix: deterministic report verification; independent PR review for M/L; proposal critique only for L.

#### 4. What is over-engineered — what would you cut from v1 and why.

Cut `altd` keep-alive and auto-rotation, the custom page, Telegram, audio, engine abstraction, the 11-command `cos` lifecycle, mirrored JSON/Markdown artifacts, automated rule audits, skill-usage mining, and quality heuristics. Native Remote Control, `claude agents`, one status file, a report schema, and a morning Decision digest are sufficient to test the thesis. The rule ledger is a second product.

#### 5. What is missing — what would you add before building anything.

Add a one-week L2-only control using `ROLES.md`’s proposal-time question rule; a historical gold set of questions with correct Decision/FYI classifications; a global attention and WIP budget; explicit state ownership and crash-reconciliation invariants; and resolution of the subscription-policy issue from `LANDSCAPE.md` → “Policy risk.” Decision #15’s refusal to experiment is not evidence that experimentation is unnecessary.

#### 6. Would you expect measurable productivity gains? Name 3–4 metrics the author should baseline THIS WEEK from the existing career-platform progress files before writing code, and the result that would justify continuing.

Baseline:

- Human questions per task: executive, answerable-from-docs, or mechanical.
- Human-facing report bullets/concerns per landed PR, split Decision/FYI.
- Blocked waiting time, cycle time, and concurrently active tasks.
- Agent activity per landed PR: sessions, review files, compactions/restarts, and JSONL tokens.

Continue after 20 comparable M/L tasks only if human questions and reading items fall at least 50%, blocked time falls 30%, throughput rises 25% without more missed decisions, L3’s wrong-answer rate stays below 10%, and agent activity per landed PR rises no more than 20%.

#### 7. Verdict in one paragraph: go / go-with-changes / don't build, and the single most important change.

**Go-with-changes.** Build a measured attention filter atop native features, not the control plane currently described. The single most important change is to eliminate low-level questions at L2—through better briefs, defaults, and proposal-time batching—before routing them through another agent.
