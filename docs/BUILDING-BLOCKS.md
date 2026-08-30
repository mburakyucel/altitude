# Building blocks — what Claude Code / Codex already give us

*Verified against the official docs on 2026-08-29 (Claude Code 2.1.251, Codex CLI 0.151.0 installed locally). Items marked ⚠ were reported by a research pass but not yet exercised by hand — confirm before depending on them. The landscape pass (`LANDSCAPE.md`) added the "Newer than the first pass" section below.*

The single most important constraint first: **the Claude Agent SDK bills through an API key only; it does not run on a Pro/Max subscription** (https://code.claude.com/docs/en/agent-sdk/overview). Since the whole point is to burn subscription quota rather than API dollars, the chief of staff cannot be an Agent SDK program. It has to be built on **headless Claude Code (`claude -p`)**, which runs on the subscription seat — the pocketbook server (`~/Projects/system-design/audio/server.py`, `run_claude` / `run_claude_stream`) already does exactly this from a phone-facing HTTP server with resumable sessions. The same holds on the Codex side with `codex exec` (see below). The custom part of the system is therefore a **thin process/state manager around subscription-billed CLI sessions**, not a model-calling program.

## Headless Claude Code (`claude -p`) — the engine interface

https://code.claude.com/docs/en/headless.md · https://code.claude.com/docs/en/cli-reference.md

| Need | Flag / mechanism |
|---|---|
| One-shot or streamed run | `-p`, `--output-format text\|json\|stream-json`, `--include-partial-messages` |
| **Long-lived bidirectional session** (chief of staff itself) | `--input-format stream-json` + `--output-format stream-json`: the process stays up, messages go in on stdin, events come out on stdout |
| Resume / branch a session | `--resume <id\|name>`, `--continue`, `--fork-session`, `--session-id <uuid>` |
| Persona without losing defaults | `--append-system-prompt-file <path>` (voice-tutor already uses this) |
| Structured result | `--output-format json --json-schema <schema>` → proposals and reports as validated JSON |
| Define subagents per run | `--agents '<json>'` |
| Isolation per task | `--worktree <branch>` (`-w`) |
| Guardrails | `--allowedTools`, `--permission-mode auto\|acceptEdits\|dontAsk\|plan\|bypassPermissions`, `--max-turns N` |
| Model / effort per role | `--model`, `--effort low…max` |
| Clean CI-style run | `--bare` (no hooks/skills/CLAUDE.md) |
| Phone steering of a local session | `--remote-control` / `--rc` |
| Chat-app channel into a session | `--channels plugin:telegram@claude-plugins-official` (research preview) |
| Subagent transcript in the stream | `--forward-subagent-text` |

Exit codes: 0 ok, 1 failure, 2 partial (rate-limit/auth mid-run), 143 SIGTERM. `json` output includes `total_cost_usd` and the session id. Stdin capped at 10 MB — pass files by path.

**Nesting limit that matters:** in-process subagents nest at most 3 deep (`CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`), 20 concurrent (`CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`). Chief of staff → orchestrator → worker would exhaust it. This settles a design question: **orchestrators are separate `claude -p` processes, not subagents of the chief of staff.** That is what we want anyway — separate session files, separate context, independently resumable, and the chief of staff never sees their transcripts.

## Background sessions — both CLIs now supervise their own processes (checked locally)

This changes how much of the "process manager" we have to write. Both CLIs on this machine already run sessions detached, list them, and let you attach, read logs, and message them:

- **Claude Code 2.1.251:** `claude --bg` starts a session in the background and prints an id; `claude agents [--json] [--cwd <path>] [--all]` lists active (and, with `--all`, completed) sessions for scripting, and can *dispatch* new sessions with a default `--model` / `--effort` / `--permission-mode` / `--agent`; `claude attach <id>`, `claude logs <id>`, `claude stop`, `claude rm <id>`, `claude respawn [--all]`. `claude project purge` wipes a project's state. `--replay-user-messages` exists for stream-json acknowledgment.
- **Codex 0.151.0:** a shared local **app-server daemon** with `codex agents` (browse all sessions), `codex queue --thread <uuid|name> --message <text>` (**inject a message into an existing session**), `codex exec --json --output-schema <file> -o <last-message-file>` (structured headless runs), `codex exec resume <id>` / `codex exec fork <id>`, `--ephemeral`, `-C <dir>`, `--add-dir`; `codex remote-control start|stop|pair` (experimental app-server remote control), `codex exec-server` (standalone service), `codex app-server generate-json-schema` (typed protocol for driving it programmatically).

So the supervisor does not need to babysit PIDs: it starts orchestrators as background sessions of the respective CLI, records the session id in the task's state file, and later asks the CLI (`claude agents --json`, `codex agents`) whether they are alive, reads `claude logs <id>` on failure, and nudges them with `SendMessage` / `codex queue`. What is still ours: the state model, the personas, the report contract, and the phone surface.

## Sessions, context, state

https://code.claude.com/docs/en/sessions.md

- Transcripts live at `~/.claude/projects/<dir-slug>/<session-id>.jsonl`. `CLAUDE_CODE_PROJECT_DIR_NAME` / `CLAUDE_CONFIG_DIR` let a manager pin where a session's files go — useful to keep chief-of-staff sessions out of the project's own session list.
- Compaction: `/compact [focus]`, auto-compact (`autoCompactWindow`), and a **"Compact Instructions"** section in CLAUDE.md that survives compaction. Career-platform's `SessionStart(matcher: compact)` hook that re-injects recent progress files is the working pattern for "state on disk outranks the summary".
- `/context` shows what is eating the window.
- **Cross-session messaging exists**: `ListAgents` lists "other local Claude sessions on this machine" and `SendMessage` targets them by name or session id — a chief of staff can nudge a running orchestrator without a custom bus. ⚠ Verify delivery semantics for `-p` sessions.

## Phone access

- **Remote Control** (https://code.claude.com/docs/en/remote-control.md): `claude --remote-control [name]` keeps the session local (files, MCP, hooks on the PC) and makes it steerable from claude.ai/code or the Claude mobile app's Code tab. Since Aug 2026 every machine running `claude remote-control` appears as a **device card** on the phone; sessions can be started from there. `PushNotification` delivers to the phone while RC is connected. Caveat: permission prompts block an unattended RC session unless `--permission-mode auto`/`dontAsk`. ⚠ Whether a `-p --input-format stream-json` session can also be RC'd was reported yes; confirm.
- **Channels** (https://code.claude.com/docs/en/channels.md): Telegram / Discord / iMessage (macOS) / fakechat plugins, MCP-based, research preview. Messages arrive as channel events; Claude replies through the channel's `reply` tool; sender allowlist. Channels that declare `permissionRelay` can forward permission prompts to the phone. Two-way and good enough for "approve / ask a follow-up" from a chat app.
- **Claude Code on web / mobile** (https://code.claude.com/docs/en/mobile.md): cloud sessions on Anthropic VMs or a self-hosted environment (public beta, org setting); cannot target this PC except through Remote Control. No documented cloud↔local teleport.
- **Our own channel**: the pocketbook pattern — HTTP server bound to the WireGuard address, a page on the phone, `claude -p` behind it. Already built, already trusted, zero third parties, and the only option that renders audio (Kokoro) and custom status views.

## Notifications, files, background work

https://code.claude.com/docs/en/tools-reference.md

`PushNotification` (desktop; phone via RC), `SendUserFile` (render/attach a file to the user's device), `Monitor` (watch a command/log/file and wake Claude on changes), `SendMessage`/`ListAgents` (cross-session), `Workflow` (fan-out orchestration with `agent()`/`parallel()`/`pipeline()`/`phase()`), `RemoteTrigger` (manage cloud Routines).

## Hooks

https://code.claude.com/docs/en/hooks-guide.md — `SessionStart`, `SessionEnd`, `PreCompact`, `PostCompact`, `PreToolUse`, `PostToolUse`, `Stop`, `SubagentStop`, `Notification`, `Idle`, `TaskCreated`/`TaskCompleted` (veto with exit 2), plus prompt- and agent-based hooks. A `Stop`/`SessionEnd` hook can `curl` a webhook — that is how an orchestrator process can announce "report ready" to the chief of staff without polling.

**`Notification` for permission prompts — verified in the 2.1.251 binary on 2026-08-30 (I-064).** The hook's stdin is `{hook_event_name: "Notification", message, title?, notification_type}` and its `matcher` is tested against `notification_type` (values include `permission_prompt`, `worker_permission_prompt`, `idle_prompt`, `auth_success`, `elicitation_dialog`, `agent_needs_input`, `agent_completed`). A permission prompt that stays open for 6 s emits `notification_type: "permission_prompt"` with `message: "Claude needs your permission to use <Tool>"` — the prompt §Phone access says blocks an unattended session. That is the mechanism `hooks/permission_prompt_fault.py` builds on: a `PreToolUse(Bash)` capture plus this notification turn a residual prompt into a counted `permission_denials` and a `permission-prompt` system fault, without granting, denying or retrying anything. `CLAUDE_CODE_DISABLE_PERMISSION_PROMPT_NOTIFY_HOOKS` would silence the event, which is one more reason `engines.clean_env()` strips every `CLAUDE*` variable before a launch. `PermissionDenied` and `PermissionRequest` also exist in the binary; neither is verified here and nothing depends on them.

## Subagents, teams, workflows (inside one session)

- Subagents: `.claude/agents/*.md` with `model`, `tools`, `permissionMode`, `maxTurns`, `isolation: worktree`, `background: true` (https://code.claude.com/docs/en/sub-agents.md). This is the worker layer as career-platform already uses it.
- Agent Teams (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`): peer teammates with a shared task list; experimental, no resume with teammates. Not for the backbone.
- Workflows: scripted fan-out up to 16 concurrent agents; good inside an orchestrator for parallel research/verification, not for the control plane.

## Routines / scheduling

`/schedule` creates cloud Routines (cron, one-off, API `/fire`, GitHub events) that run in Anthropic's cloud or a self-hosted environment — **not on this PC**, and they need a claude.ai login (counts against subscription). `/loop` is local and in-session. Neither is needed for v1; a cron on the PC that pokes the chief of staff covers "morning status".

## Codex side (subscription-billed as well)

Verified from `codex --help` / `codex exec --help` (0.151.0): `codex exec [PROMPT]` is the headless mode; `--json` streams JSONL events; `--output-schema <file>` enforces a JSON Schema on the final message and `-o <file>` writes it; `resume <id>` / `fork <id>` give resumable threads; `-C <dir>` / `--add-dir` scope the workspace; `-s <sandbox>` and the approval flags (`--full-auto`, `--dangerously-bypass-approvals-and-sandbox`) set the guardrail level; `-m <model>` and `-p <profile>` pick the engine; `--ephemeral` skips persistence. `codex review` runs a headless code review (the cross-review step career-platform already does by hand). Structured output + resumable threads + `codex queue` means Codex can fill the orchestrator role behind the same contract as Claude Code, and can also be the *proposal reviewer* (one engine proposes, the other critiques) cheaply.

**Permissions (I-064, 2026-08-30):** `codex exec` takes no per-launch rules file — its approvals come from the sandbox/approval flags (`-s`, `--full-auto`) and `~/.codex/config.toml`, and there is nothing Altitude can render per launch the way it renders Claude's `--settings` `permissions.allow` block. Codex sessions are therefore deliberately unchanged by the Claude allowlist in `altitude/permissions.py`; the Codex-side merge refusals recorded in I-064 remain an open item on that engine.

## API-side facts relevant to the summarizer

- Models: `claude-fable-5` (1M ctx), `claude-opus-5`, `claude-sonnet-5`, `claude-haiku-4-5-20251001`. Under the subscription these are picked with `--model`; no direct API calls planned.
- **Anthropic offers no TTS.** Kokoro on `127.0.0.1:8880` (voice-tutor / `kokoro.service`) is the audio renderer.
- Voice mode in the Claude mobile app is for claude.ai chats, not Code sessions.

## Newer than the first pass (from the landscape research, same day)

- **Remote Control is GA** (https://code.claude.com/docs/en/remote-control): `claude remote-control --spawn worktree --capacity 32` lets the phone start sessions on this PC; **permission prompts and `AskUserQuestion` stay open on the phone until answered**, with push. This is a tappable, structured approval gate — the phase-1 Decision surface. Needs a claude.ai login (no API key / `ANTHROPIC_BASE_URL` proxy). `AskUserQuestion` is not available inside subagents: the top-level L3 session asks it. Served sessions stay resumable ~4h after the server exits.
- **`claude agents`** is a console with *Needs input / Working / Ready for review* buckets and voice dispatch (https://code.claude.com/docs/en/agent-view); `claude --bg --name <x>`. **Background sessions idle-stop after ~1h unless pinned** — an L2 that needs a Decision must checkpoint and exit, to be resumed with the answer.
- **Cross-session messaging is GA** (https://code.claude.com/docs/en/cross-session-messaging): `ListAgents`/`SendMessage`, `notify_when_idle`, inbound policy `hold|accept|refuse`, works across machines through Remote Control. Resolves the ⚠ above.
- **Hooks**: 33 events; handler types `command | http | mcp_tool | prompt | agent` — a Stop/SubagentStop hook can POST to `altd` natively; `PreCompact` can archive the transcript first (https://code.claude.com/docs/en/hooks).
- Headless also has `--max-budget-usd` and `--restricted`; `.claude/rules/` holds modular rule files (a natural home for ledger-projected rules); `/goal` runs a stop-hook loop until a goal is met; `/rewind`; `claude -p "…" --cloud <id>` steers cloud sessions.
- **Claude Dispatch** (phone → Desktop app; pushes when it "needs your go-ahead"; Pro/Max; Desktop must stay open) — a fallback gate, not the plan (https://support.claude.com/en/articles/13947068).
- **Codex**: `codex app-server` is JSON-RPC over stdio/WebSocket with `thread/start|resume|fork|archive|compact/start|goal/set`, `turn/start|steer|interrupt`, and server-initiated `item/commandExecution/requestApproval`, `item/fileChange/requestApproval`, `tool/requestUserInput` that can be forwarded to a phone and answered later (https://learn.chatgpt.com/docs/app-server). Hooks GA (2026-05-14) with a Claude-Code-compatible schema. Official **codex-plugin-cc** (https://github.com/openai/codex-plugin-cc) is the Claude-parent → Codex-worker pattern with background jobs. Codex Remote pairs only with an awake desktop app, so Codex never fronts the phone. `codex mcp-server` is deprecated in favor of app-server.
- **Policy** (settle before building — see `LANDSCAPE.md`): Anthropic bars third-party harnesses from claude.ai OAuth (2026-04-04) and the SDK terms bar claude.ai login for third-party products. Driving the official logged-in `claude` binary headlessly for personal use — what the pocketbook already does and what Paperclip's `claude_local` does — is not that case on its face, but Burak should confirm against his plan's current terms; the engine adapter keeps an API-key or Codex fallback possible per role.

## Verified by hand on this machine (2026-08-29 smoke test)

- `claude --bg` **conflicts with `-p`**; the task is the positional argument: `claude --bg --name <name> [-w <slug>] --append-system-prompt-file <persona> --permission-mode auto --max-turns N '<brief text>'`. It prints `backgrounded · <id> · <name>` and returns immediately.
- Without `-w`, `--bg` **auto-creates a worktree** at `<repo>/.claude/worktrees/<slug-derived-from-task>` and runs there; pass `-w <slug>` to name it (`ARCHITECTURE.md` uses the task slug).
- `claude agents --json --all` returns, per session: `id` (short, for `attach/logs/rm`), `name`, `sessionId` (uuid, for `--resume`), `cwd` (the worktree), `startedAt`, `status` (`busy|idle`), `state` (`done` when finished). Interactive sessions appear too (no `id`). This is everything `reconcile.sh` needs.
- `-n, --name <name>` is a general session flag (display name; usable in `/resume` and `SendMessage`).
- `codex exec` **waits on stdin** unless it is closed: always run it with `< /dev/null` from scripts.
- Nested launches from inside a Claude session need the `CLAUDE*` environment variables unset (the pocketbook server does this too).

## What this settles

1. **Engine = subscription CLIs driven headlessly.** No Agent SDK, no raw API calls, for either the chief of staff or the orchestrators.
2. **Chief of staff = a long-lived `claude -p --input-format stream-json` session** (or a Codex equivalent) with a persona file and a project state directory, kept alive by a small supervisor process.
3. **Orchestrators = separate headless processes** per task, in a worktree, reporting through files in the repo plus a Stop-hook webhook.
4. **Phone = one of two paths, both cheap:** (a) Remote Control + Claude app — nothing to build, and `AskUserQuestion` held open on the phone gives a structured, tappable approval gate; no audio, no status board; (b) the pocketbook pattern over WireGuard, which we already own and which can render status boards, proposals, and spoken summaries. They are not exclusive.
