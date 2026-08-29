# Vision — an executive layer above orchestrator agents

*Status: brainstorm draft, 2026-08-29 — ideas captured here are inputs to sort, not settled choices (see `DECISIONS.md`). First customer: Burak. Possible open-source project later if it proves useful.*

## The problem

Burak ships a lot of software with coding agents (Claude Code and Codex, both on subscription seats). The models are good enough that most of the code is not reviewed line by line; review is reserved for critical paths. The working pattern that evolved is: for each task, open an **orchestrator session** that stays at a high level, holds the plan and outcomes, and delegates the actual work to worktree subagents (see `~/Projects/career-platform/docs/ENGINEERING.md` → "Orchestrator sessions"). To stay efficient, he runs many of these in parallel — often ten at once — and context-switches between them.

That works, but it has hit a ceiling, and the ceiling is not tokens. There is unused quota, and more can be bought. The ceiling is **Burak's ability to keep things organized**: ten orchestrators each surface their own questions, each at a different altitude, each demanding that he re-load that project's context in his head before he can answer. Much of what they ask is below the level he actually cares about (CDK details, low-level implementation choices). The result is exhausting context-switching, slower decisions than the agents are capable of consuming, and a real cap on how many ideas can be in flight — while there is a long backlog of ideas that never get started because there is no time to babysit them.

Put differently: the role Burak wants to play is **principal engineer / executive** — set direction, pick between architectures, accept or reject proposals, ask "why" — but the tooling forces him to also be the **engineering manager** of every orchestrator: tracking which is waiting on what, keeping their context alive, remembering where each one left off, translating their low-level questions back up to decisions.

## The goal

Add **one more layer of abstraction** above the orchestrators: a long-lived, per-project **chief-of-staff agent** that Burak talks to, and that manages the orchestrators so he does not have to.

The levels, in the vocabulary we will use everywhere (L1 changes code, L2 orchestrates a task, L3 coordinates the project):

| Level | Role | Who | Holds |
|---|---|---|---|
| Burak | Sets direction, makes architecture and product decisions, approves proposals, asks follow-ups | Principal / executive | The vision; a few decisions per day |
| **L3 — coordinator** (*chief of staff*) | One per project, long-lived. Turns an executive request into a research → proposal → approval → execution loop; dispatches L2s; digests their reports; routes Burak's feedback to the right L2; keeps the project's state; improves the project's rules and skills over time | The new agent | Project state, open decisions, an index of the archive — never the details |
| **L2 — orchestrator** | One per task. Plans the task, delegates to L1s, verifies outcomes via git/gh, writes the report | Existing pattern (Claude Code or Codex session) | One task's plan and outcomes |
| **L1 — worker** | Does the actual work in a worktree, opens the PR | Existing pattern (subagents) | One change |

The canonical interaction, which every design decision should be checked against:

1. Burak, from his phone or laptop, tells the career-platform chief of staff: *"Implement observability."*
2. The chief of staff does **not** start implementing. It spawns a **research/proposal agent** scoped to that project, which reads the codebase and docs and produces a proposal (options, trade-offs, a recommendation, cost/risk).
3. The chief of staff returns a **brief, high-level, architecture-level summary** — text, or audio for the commute — with the one or two decisions that actually need Burak. Burak can ask follow-ups ("why not X?", "explain the tracing choice in two sentences"), push back, or tweak.
4. Burak says **go**. The chief of staff writes the brief, spawns an **orchestrator session** for the task, and gets out of the way.
5. When the orchestrator's report lands (PRs merged, what deviated, what is blocked), the chief of staff digests it into a short status, surfaces only what needs a decision, and **archives the rest**. Its own context does not grow with the work it dispatched.

Burak's involvement per task shrinks to: the request, one round of reading/asking, one approval, and one status read. Everything below that is somebody else's job.

**Two kinds of message, never mixed.** Everything L3 sends is either a **Decision** — something only Burak can settle, phrased as one question with a default and the one or two facts that matter — or an **FYI** — visibility into what is happening so nothing drifts out of control, but not gated on him. Concretely, for career-platform:

- *Decision:* "Observability: L2 proposes OpenTelemetry traces + four metrics (p95 latency per procedure, model-call spend per tenant, judge queue depth, error rate) shipped to CloudWatch; ~$X/month at current volume; alternative was Datadog at ~$Y. Go with CloudWatch?" — the architecture, the numbers, the choice. Nothing about which files.
- *FYI:* "Study index page wasn't following the wireframe; PR #119 fixes the tier labels, chip colors and breadcrumb; merged, main green." — what changed and why, in two lines.
- *FYI, would have been a Decision if the answer were different:* "New feature lands as a new endpoint on the existing Lambda API — no new service." Had it needed Fargate or any new piece of infrastructure, that is a Decision, explained in the same three sentences.

**Feedback goes to the right level.** Burak normally talks to L3, which routes feedback to the correct L2 (or answers it from project docs). But he can also see any L2's status and message it directly when he wants to steer a task himself; L3 stays informed either way.

**The system watches its own context and quality.** Burak should never again check a context meter. L3 and the supervisor track every session's context usage and rotate an L2 to a fresh session from its checkpoint before quality degrades, catch the signs of degradation (retry loops, reports that contradict git, spend past estimate) and act on them, and tell Burak in an FYI line. If the same problem keeps recurring, that becomes a rule or a skill change.

**Validation happens without being asked.** Every proposal is critiqued by an independent session (ideally the other engine) before Burak sees it; every L2 PR gets an independent review before it merges; every report is verified against git before it is digested. Burak reads reviewed proposals and verified status, never raw claims.

**L3 improves the project as it goes.** It watches how work actually happens — what gets asked repeatedly, where tokens are burned, where L2s are inconsistent, which skills are never used — and proposes changes to the project's `CLAUDE.md`, rules, and skills: create a skill when the same procedure keeps recurring, tighten a rule when L2s keep getting it wrong, retire a skill nobody uses. These land as ordinary PRs and are reported as FYIs; only changes to guardrails and never-lists are Decisions. **Every rule carries its provenance**: an id in the file, and a ledger entry recording the incident that caused it (with a link to the evidence), the failure it prevents, and how to verify it is working — so rules can be audited for staleness, recurrence, and relevance later, nobody has to sit down and write instructions by hand, and no agent can add an unverifiable one.

## Why this and not "just more orchestrators"

- **Decisions at the right altitude.** Orchestrators ask questions at the level of the task. The chief of staff answers the ones that have a project-level answer already (from `DECISIONS.md`, `ENGINEERING.md`, prior proposals) and escalates only genuinely executive ones. That is the single biggest reduction in Burak's load.
- **One inbox per project instead of ten terminals.** Status, proposals, and questions arrive in one place, ranked, in a form readable on a phone in a minute.
- **Context hygiene is designed in, not improvised.** Today each session's context is preserved by hand (progress files, compaction hooks, re-reading). The chief of staff owns explicit state: a small live working set, periodic checkpoints, and an archive of finished work it can search but does not carry.
- **Parallelism becomes cheap for the human.** Ten projects × several tasks in flight is fine when each one costs one approval and one status read.

## Why not a fully autonomous "run the company" agent

This is explicitly **not** an OpenClaw-style always-on agent that is told to "build the product" and left alone. Burak does not trust models to run a complex system unsupervised, and does not want to. The design keeps him in control at the points that matter:

- Nothing is executed without an explicit go on a proposal (the gate can be relaxed per project for routine work — that is a setting, not the default).
- High-impact classes of change (paid-API behavior, IAM, spend guardrails, migrations, deploy workflows, the rules themselves) remain "open the PR and stop" no matter what any agent decides — the same never-list career-platform already enforces.
- The chief of staff summarizes and proposes; it does not decide architecture. When it is unsure whether something is executive-level, it asks.

Control also has to be *usable*: approvals and questions must be answerable from a phone in a sentence, not by reading a diff.

## Principles

1. **Subscription engines under the hood.** Claude Code and Codex seats are far cheaper than API pricing and Burak has both. The system drives them headlessly (`claude -p` / `codex exec` and equivalents) rather than calling model APIs directly wherever the tooling permits. Whatever runs the chief of staff itself may also be one of these sessions.
2. **Provider-agnostic at the boundary.** Which engine runs an orchestrator is a per-task detail (Codex reviews Claude's PRs today and vice versa). The chief of staff speaks a small, engine-neutral contract: a brief in, a report out.
3. **Reuse the patterns that already work.** Briefs, progress files, "Report for Burak" sections, worktree-per-task, PR-only landing, never-lists, compaction hooks re-injecting progress files — these are proven across career-platform and will be the orchestrator contract, not reinvented.
4. **Small live context, durable state on disk.** The chief of staff's memory is files: project state, open decisions, an archive index. It can be restarted or its context refreshed at any time and lose nothing. Finished work goes to the archive; the live context holds only what is in flight.
5. **Executive-readable output.** Summaries are few dense sentences leading with the decision needed; deeper explanation is one follow-up away, never forced. Audio is a first-class rendering for the commute (local Kokoro TTS already exists, as does the phone-facing WireGuard server).
6. **Self-hosted, reachable over the tunnel.** Same posture as the pocketbook: runs on the always-on PC, reachable from the phone through WireGuard, no third parties in the loop unless one is chosen deliberately (e.g., a Telegram bot as a convenience channel).
7. **Decisions and FYIs are different channels.** A Decision waits for Burak; an FYI never does. Mixing them is how attention gets spent on things that did not need it — the exact failure this project exists to fix.
8. **The system is responsible for its own context and quality.** Context thresholds, rotation from checkpoints, degradation signals, and independent validation are built-in defaults, not things the human requests.
9. **The coordinator maintains the project's operating rules.** Skills, `CLAUDE.md`, and briefs are living artifacts L3 is responsible for improving from observed behavior, with Burak informed and able to veto.
10. **Spend is proportional to return.** Tokens are not the bottleneck and a lot of work landed is worth a lot of tokens — but no task may consume the seat for little return. The failure mode is the *agent bomb*: an orchestrator that writes ten items and then spawns ten agents to verify them, each doing its own research, until the whole 5-hour window is gone. Fan-out, verification, and retries are sized to the task class up front, capped by mechanism (not by hope), and a task that hits its envelope stops and reports rather than continuing. Verification is batched — one verifier per artifact class, never one per item.
10. **Reversible steps first.** Every stage of the loop leaves artifacts in the project repo (proposal doc, brief, report) so that the system can be abandoned at any point and the projects are no worse off.

## Non-goals (for now)

- Replacing Claude Code or Codex, or building a new coding agent.
- Code review or low-level quality gates — those stay with the orchestrators/workers and CI.
- Running unattended 24/7 with no human gate.
- Multi-user / team features. First customer is one person; open-sourcing is a later decision, so keep the design generic but do not build for it yet.
- A polished GUI. A phone-readable page or chat channel that works is the bar.

## What "working" looks like (success criteria)

- Burak can start a non-trivial task on any project **from his phone in under a minute** and get a proposal back without opening a terminal.
- He reads a proposal and makes the go/no-go **in under five minutes**, with follow-up questions answered at the altitude he asked them.
- He can have **ten or more tasks in flight across projects** and know, from one status view, what is waiting on him and what is not.
- The chief of staff's context does **not** grow with completed work; restarting it loses nothing.
- Burak never checks a context meter and never asks for a review: sessions are rotated before they degrade, and proposals/PRs/reports reach him already validated.
- Everything the system does is visible in the target repo as ordinary files/PRs; nothing depends on the tool staying alive.
- Over weeks, the project's skills/rules measurably improve (fewer repeated questions, lower tokens per task) because L3 maintains them — and Burak learns about each change from a two-line FYI, not by finding it.

## Open questions for Burak (to be settled in `DECISIONS.md`)

- Product name. Working name for the role: *chief of staff*. Candidate project names to pick from later.
- Where the chief of staff runs: a Claude Code session with a persona (cheapest to build), a Codex session, or a small custom process that only orchestrates and uses either engine for the thinking.
- Phone channel: the pocketbook-style page over WireGuard, a Telegram/Discord bot, Claude Code's own remote/mobile features, or a combination.
- Default approval policy per project (everything gated vs. routine work auto-approved).
- How much of the proposal step should be a fresh research agent vs. the chief of staff answering from project docs when the answer is obvious.

## Related docs

- `BUILDING-BLOCKS.md` — what Claude Code and Codex already provide, verified, and what that settles.
- `LANDSCAPE.md` — what existing tools do and don't solve; nothing does end-to-end; build on native Claude Code, evaluate Paperclip, borrow from OpenClaw.
- `ARCHITECTURE.md` — the proposed system.
- `DECISIONS.md` — proposed decisions with trade-offs, open questions, and the keep/adjust/defer assessment of this brainstorm.
