"""Claude Code launch, permissions, wire protocol and native observations."""
from __future__ import annotations
import base64
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from .. import config, platform, state as S


from . import common


def _claude_quota() -> dict:
    """Read live-only /usage rows (CLI 2.1.277+), never cached control-API windows."""
    from .. import route
    unknown = {"known": False, "why": "No live plan quota: /usage requires CLI 2.1.277+ and subscription login"}
    try:
        version = subprocess.run([config.CLAUDE_BIN, "--version"], capture_output=True, text=True,
                                 timeout=5, check=True, env=common.clean_env())
        release = re.match(r"(\d+)\.(\d+)\.(\d+)", version.stdout)
        if not release or tuple(map(int, release.groups())) < (2, 1, 277):
            return unknown
        with tempfile.TemporaryDirectory(prefix="altitude-usage-") as empty:  # never the home, with its private places
            result = subprocess.run(
                [config.CLAUDE_BIN, "--print", "/usage", "--output-format", "stream-json", "--verbose",
                 "--safe-mode", "--no-session-persistence", "--tools", "", "--strict-mcp-config"],
                capture_output=True, text=True, timeout=20, check=True, env=common.clean_env(), cwd=empty)
        reading, models = {}, {}
        for event in map(json.loads, result.stdout.splitlines()):
            if event.get("type") == "result" and event.get("is_error"):
                return _signed_out(result, unknown)
            if event.get("type") != "assistant" or not event.get("usage_report"):
                continue
            rows = (event["usage_report"].get("rate_limits") or {}).get("limits") or []
            for row in rows:
                label = ((row.get("scope") or {}).get("model") or {}).get("display_name") \
                    if row.get("kind") == "weekly_scoped" else None
                window = {"session": "five_hour", "weekly_all": "seven_day"}.get(row.get("kind"))
                if window is None and not isinstance(label, str):
                    continue  # surface-scoped and unknown meters are neither account nor model allowances
                used = route._number(row.get("percent"))
                if (used is None or isinstance(row.get("percent"), bool) or window in reading or label in models
                        or "severity" not in row or "is_active" not in row):
                    return unknown
                reset = row.get("resets_at")
                reset = datetime.fromisoformat(reset).timestamp() if reset else None
                if window:
                    reading.update({window: used, f"{window}_resets": reset})
                else:
                    models[label] = {"model": config.model_family(label), "label": label,
                                     "seven_day": used, "seven_day_resets": reset}
        if models:
            reading["models"] = list(models.values())
        return {"known": True, "at": time.time(), **reading} if reading else unknown
    except subprocess.CalledProcessError as exc:
        return _signed_out(exc, unknown)
    except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
        return unknown


def _signed_out(process, unknown: dict) -> dict:
    """A failed usage read that shows a sign-in rejection carries it, so the refresh can record the lapse."""
    rejected = common.rejection("claude", {"raw_stdout": process.stdout or "", "raw_stderr": process.stderr or ""})
    return {**unknown, "rejection": rejected} if rejected and rejected.get("sign_in") else unknown


L3_ALLOWED_TOOLS = ",".join((
    "Read", "Grep", "Glob",
    *(f"Bash({name} *)" for name in ("alt", "git", "gh", "journalctl", "systemctl")),
))


IMPROVEMENT_INPUT_TOKENS, IMPROVEMENT_OUTPUT_TOKENS = 74_000, 12_000


IMPROVEMENT_SENT_BYTES = 72 * 1024


def improvement_budget(model: str) -> float:
    """Claude Code's budget stop for one review: its token maximum priced at the model's published rates."""
    if model not in config.IMPROVEMENT_MODELS:
        raise ValueError(f"no usage figures for {model}")
    prompt_price, output_price = config.IMPROVEMENT_MODELS[model]
    return round((IMPROVEMENT_INPUT_TOKENS * prompt_price + IMPROVEMENT_OUTPUT_TOKENS * output_price) / 1e6, 4)


@config.admitted_provider
def improvement_review(prompt: str, instructions: str, schema: dict, *, model: str, timeout: int) -> dict:
    """One tool-free Claude call with no project, settings or session; fails closed unless its start record proves it.

    The answer is untrusted data for the caller to validate. Usage is the plan's; cost is only the yardstick."""
    result = {"model": model, "engine_model": None, "usage": None, "cost": None, "answer": None, "error": None}
    schema_text = json.dumps(schema, separators=(",", ":"))
    if len((prompt + instructions + schema_text).encode()) > IMPROVEMENT_SENT_BYTES:
        return {**result, "error": "its prompt exceeds the usage limit"}
    try:
        budget = improvement_budget(model)
    except ValueError as exc:
        return {**result, "error": str(exc)}
    if common.installation("claude").get("available") is False or common.usage_hold():
        return {**result, "error": f"{config.model_label(model)} is unavailable"}
    cmd = [config.CLAUDE_BIN, "--print", "--output-format", "stream-json", "--verbose", "--safe-mode",
           "--no-session-persistence", "--tools", "", "--strict-mcp-config", "--model", model,
           "--system-prompt", instructions, "--json-schema", schema_text, "--max-budget-usd", str(budget)]
    # The operator runs the review on the plan only: no API key, token or endpoint override reaches the call.
    env = {**{k: v for k, v in common.clean_env().items() if not k.startswith("ANTHROPIC_")},
           "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(IMPROVEMENT_OUTPUT_TOKENS)}
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="altitude-improve-") as empty:
        try:
            done = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, env=env, cwd=empty)
        except subprocess.TimeoutExpired:
            return {**result, "error": "it timed out", "duration_seconds": round(time.monotonic() - started, 3)}
        except OSError as exc:
            return {**result, "error": str(exc)}
    result["duration_seconds"] = round(time.monotonic() - started, 3)
    isolated, text, quota = False, "", None
    for line in done.stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            # The review reads private conversation quotes; a session that could act on them is never used.
            # Its only tool is Claude Code's own carrier for the schema-bound answer.
            isolated = event.get("tools") == ["StructuredOutput"] and not event.get("mcp_servers") \
                and bool(event.get("model")) and event.get("apiKeySource") == "none"
        elif event.get("type") == "assistant":
            message = event.get("message") or {}
            if message.get("model") and message["model"] != "<synthetic>":
                result["engine_model"] = message["model"]
            text += "".join(str(c.get("text") or "") for c in message.get("content") or [] if c.get("type") == "text")
            quota = event.get("quotaLimits") or message.get("quotaLimits") or quota
        elif event.get("type") == "result":
            result["usage"] = event.get("usage") or None
            if event.get("total_cost_usd") is not None:
                result["cost"] = float(event["total_cost_usd"])
            result["answer"] = event.get("structured_output")
            if event.get("is_error"):
                result["error"] = str(event.get("result") or event.get("subtype") or "it failed")[:300]
    limit = common.usage_limit_in(text or result["error"] or done.stderr, quota)
    if limit:
        common.record_usage_limit("claude", limit, text[:200])
        return {**result, "answer": None, "error": "it went over its usage limit"}
    if not isolated:
        return {**result, "answer": None, "error": "it could not run isolated"}
    if result["error"] is None and (done.returncode or result["answer"] is None):
        result["error"] = "its answer was not usable"
    usage = result["usage"] or {}
    used = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens",
                                                 "cache_creation_input_tokens", "output_tokens"))
    result["tokens"], result["over_limit"] = used, used > IMPROVEMENT_INPUT_TOKENS + IMPROVEMENT_OUTPUT_TOKENS
    return result


def claude_stop(agent_id: str) -> str:
    p = subprocess.run([config.CLAUDE_BIN, "stop", agent_id], capture_output=True, text=True, timeout=60, env=common.clean_env())
    if p.returncode:  # I-20260907-171446: never start a copy while an orphan owns the session.
        raise RuntimeError((p.stderr or p.stdout).strip())
    deadline = time.monotonic() + 5
    while any(row.get("id") == agent_id and row.get("state") not in ("done", "failed", "stopped")
              for row in claude_agents()):
        if time.monotonic() >= deadline:
            raise RuntimeError(f"daemon job {agent_id} is still running after stop")
        time.sleep(0.05)
    return (p.stdout or p.stderr).strip()


class _ClaudeDriver(common._Driver):
    """Claude reads stream-json input: a message written mid-turn joins the turn at its next tool boundary, and
    `background_tasks` (Ctrl+B over the wire) moves running commands to the background so that comes promptly.
    `--replay-user-messages` echoes each accepted line; the input closes after a result with nothing unacknowledged,
    and Claude then finishes what it has accepted and exits."""

    def __init__(self, spec: dict, env: dict):
        super().__init__(spec, env)
        self.waiting: dict[str, str | None] = {}
        self.ended_at: float | None = None

    def line(self, content: list, message_id: str | None) -> None:
        identity = str(uuid.uuid4())
        self.write({"type": "user", "message": {"role": "user", "content": content}, "parent_tool_use_id": None,
                    "origin": {"kind": "human"}, "uuid": identity, "priority": "next"})
        self.waiting[identity] = message_id

    def deliver(self, message: dict) -> str | None:
        """Settled later by its echo."""
        with self.lock:
            if self.closed:
                return "returned"
            try:
                self.line([{"type": "text", "text": message["text"]}], message["id"])
                self.write({"type": "control_request", "request_id": uuid.uuid4().hex,
                            "request": {"subtype": "background_tasks"}})
            except OSError:
                return None if message["id"] in self.waiting.values() else "returned"
            return None

    def unsettled(self) -> list[str]:
        pending = [value for value in self.waiting.values() if value]
        self.waiting.clear()
        return pending

    def acknowledge(self, identity: str | None) -> None:
        with self.lock:
            message_id = self.waiting.pop(identity, None)
            if self.ended_at is not None and not self.waiting:
                self.close()
        if message_id:
            self.settle(message_id, "delivered")

    def ended(self) -> None:
        with self.lock:
            self.ended_at = time.monotonic()
            # The result proves the first prompt was read, whether or not its echo came first.
            self.waiting = {key: value for key, value in self.waiting.items() if value}
            if not self.waiting:
                self.close()

    def expire(self) -> None:
        """A written message whose echo never comes cannot hold the job open after its turn."""
        with self.lock:
            if self.closed or self.ended_at is None or time.monotonic() - self.ended_at < common.SEND_ECHO_WAIT:
                return
            self.close()
            late = self.unsettled()
        for message_id in late:
            self.settle(message_id, "unconfirmed")

    def start(self, watcher: threading.Thread) -> None:
        with self.lock:
            self.line(self.spec["input"], None)
            if self.sends is None:
                self.close()  # nothing can join this turn
        watcher.start()
        timer = threading.Thread(target=self.tick, daemon=True)
        timer.start()
        for raw in self.child.stdout:
            try:
                event = json.loads(raw)
            except ValueError:
                event = None
            if not isinstance(event, dict):
                self.emit(raw)
                continue
            kind = event.get("type")
            if kind == "user" and event.get("isReplay"):
                self.acknowledge(event.get("uuid"))
                continue
            if kind == "control_response":
                continue
            if kind == "control_request":
                # Input stays open, so a request Claude makes of its host is refused rather than left waiting.
                with self.lock:
                    if not self.closed:
                        self.write({"type": "control_response", "response": {
                            "subtype": "error", "request_id": event.get("request_id"),
                            "error": "Altitude answers no host requests"}})
                continue
            if event.get("session_id"):
                self.session_id = event["session_id"]
            self.emit(raw)
            if kind == "result":
                self.ended()

    def tick(self) -> None:
        while not self.finished:
            self.expire()
            time.sleep(common.SEND_POLL)


def _claude_writable(*roots: Path) -> tuple[Path, ...]:
    """Where a Claude job may write when the platform confines its files (macOS): its roots, Claude's own state
    (sessions, settings, and the account file with its atomic replacements) and the GitHub CLI's configuration.
    Claude's permission boundary still decides within them."""
    home = Path.home()
    return (*roots, Path(os.environ.get("CLAUDE_CONFIG_DIR") or home / ".claude"), Path(f"{home / '.claude.json'}*"),
            home / ".config/gh")


def claude_settings() -> Path:
    """The settings every Claude launch without a per-dispatch file gets: auto-compact at the configured window,
    stated explicitly rather than inherited from ~/.claude/settings.json. Rewritten when the number changes."""
    p = config.ROOT / "claude-settings.json"
    want = {"autoCompactWindow": config.AUTOCOMPACT_WINDOW}
    try:
        cur = json.loads(p.read_text())
    except (OSError, ValueError):
        cur = None
    if cur != want:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(want, indent=2) + "\n")
    return p


@config.admitted_provider
def claude_print(prompt: str, *, cwd: Path, resume: str | None = None, persona: Path | None = None,
                 allowed_tools: str | None = None, tools: str | None = None, permission_mode: str = "auto",
                 schema: Path | None = None, model: str | None = None, max_turns: int | None = None,
                 effort: str | None = None,
                 settings: Path | None = None, extra_env: dict | None = None, on_text=None, on_start=None,
                 timeout: int = config.L3_TURN_TIMEOUT, restricted: bool = False,
                 add_dirs: tuple[Path, ...] = (), permission_prompts: str | None = None,
                 durable_timeout: bool = False, images: list[dict] | tuple = (),
                 sends: Path | None = None, on_send=None) -> dict:
    """One headless turn. Returns text, session_id, usage, cost, turns, structured (if schema), error, and bounded
    raw_stdout/raw_stderr; `limited` (scope and optional reset) when an allowance is exhausted — the call is not even
    made while a hold is in force.

    A message placed in `sends` joins the running turn (see `_Driver`); `on_send(message_id, outcome, text)` is called
    at that point of the turn with the reply streamed since the previous one, and `text` holds only what follows.

    `on_start(pid)` is called the moment the child exists. The turn outlives altd, so its pid lets a
    restarted server distinguish an in-flight turn from a dead one."""
    config.task_effort("claude", effort, role="l3")
    content = common._engine_input("claude", prompt, images)
    held = common.usage_hold()
    if held:
        return {"text": "", "session_id": resume or "", "usage": {}, "context_tokens": 0, "cost": 0.0, "turns": 0,
                "structured": None, "error": f"usage limit: window exhausted until {held}", "tools": [], "limited": common._usage_limit(held),
                "raw_stdout": "", "raw_stderr": "", "raw_stdout_truncated": False, "raw_stderr_truncated": False,
                "safe_to_retry": True, "rejection": None}
    cmd = [config.CLAUDE_BIN, "-p", "--input-format", "stream-json", "--replay-user-messages",
           "--output-format", "stream-json", "--include-partial-messages", "--verbose", "--permission-mode", permission_mode]
    if permission_prompts:
        cmd += ["--permission-prompts", permission_prompts]
    if restricted:
        cmd.append("--restricted")
    if add_dirs:
        cmd += ["--add-dir", *(str(path) for path in add_dirs)]
    if persona:
        cmd += ["--append-system-prompt-file", str(persona)]
    if allowed_tools:
        cmd += ["--allowedTools", allowed_tools]
    if tools is not None:
        cmd += ["--tools", tools]
    if schema:  # the flag takes the JSON text itself, not a path
        cmd += ["--json-schema", Path(schema).read_text() if Path(schema).exists() else str(schema)]
    if model:
        cmd += ["--model", model]
    if effort is not None:
        cmd += ["--effort", effort]
    if max_turns:
        cmd += ["--max-turns", str(max_turns)]
    cmd += ["--settings", str(settings or claude_settings())]
    if resume:
        cmd += ["--resume", resume]
    env = common.clean_env()
    env.update(extra_env or {})
    if effort is not None:
        env.pop("CLAUDE_CODE_EFFORT_LEVEL", None)
    writable = _claude_writable(Path(cwd), config.ROOT)
    unit = _claude_unit(f"sync-{uuid.uuid4().hex}")
    driver = common._driver_command()
    if durable_timeout or sends is not None:
        driver = platform.job_command(unit, driver, common.codex_env(env), runtime_max=timeout, writable=writable)
        env = common.codex_env(env, retain_user_bus=True)
    else:
        driver = platform.confined(driver, writable)
    # prompt goes through the driver's input: --allowedTools is variadic and would swallow a positional prompt
    proc = subprocess.Popen(driver, cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env)
    if on_start:
        on_start(proc.pid)
    common._feed(proc.stdin, common._driver_input({"engine": "claude", "command": cmd, "input": content,
                                     "sends": str(sends) if sends is not None else None}))
    stdout_capture, stderr_capture = common._BoundedRawCapture(), common._BoundedRawCapture()

    def drain_stderr() -> None:
        while chunk := proc.stderr.read(65536):
            stderr_capture.add(chunk)

    drain = threading.Thread(target=drain_stderr, daemon=True)
    drain.start()
    killer = threading.Timer(timeout, proc.kill) if not durable_timeout and sends is None else None
    if killer:
        killer.start()
    out = {"text": "", "session_id": resume or "", "usage": {}, "context_tokens": 0, "cost": 0.0,
           "turns": 0, "structured": None, "error": None, "tools": []}
    parts: list[str] = []
    split = False
    failure = None
    safe_to_retry, rejected = True, None
    try:
        for line in proc.stdout:
            stdout_capture.add(line)
            try:
                o = json.loads(line)
            except ValueError:
                safe_to_retry = False
                continue
            safe_to_retry = safe_to_retry and common._safe_event("claude", o)
            rejected = common.rejection("claude", {"error": common._event_error("claude", o)}, model) or rejected
            if o.get("session_id"):
                out["session_id"] = o["session_id"]
            typ = o.get("type")
            if typ == "altitude.send":
                if on_send:
                    on_send(o.get("message_id"), o.get("outcome"), "".join(parts).strip())
                if o.get("outcome") != "returned":
                    parts, split = [], True
            elif typ == "stream_event":
                delta = (o.get("event") or {}).get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    parts.append(delta["text"])
                    if on_text:
                        on_text(delta["text"])
            elif typ == "assistant":
                out["turns"] += 1
                msg = o.get("message") or {}
                if msg.get("model") and msg["model"] != "<synthetic>":
                    out["engine_model"] = msg["model"]
                u = msg.get("usage") or {}
                if u:
                    out["context_tokens"] = int(u.get("input_tokens", 0)) + int(u.get("cache_read_input_tokens", 0)) + int(u.get("cache_creation_input_tokens", 0))
                for c in msg.get("content") or []:
                    if c.get("type") == "tool_use":
                        inp = c.get("input") if isinstance(c.get("input"), dict) else {}
                        out["tools"].append(common._tool(c.get("name"), inp.get("command")
                                                  if c.get("name") == "Bash" else None))
                    elif c.get("type") == "text" and msg.get("model") == "<synthetic>":
                        out["synthetic"] = (out.get("synthetic") or "") + str(c.get("text") or "")
                q = o.get("quotaLimits") or msg.get("quotaLimits")
                if isinstance(q, dict):
                    out["quota"] = q
            elif typ == "result":
                if isinstance(o.get("result"), str):
                    out["final_text"] = o["result"]
                out["usage"] = o.get("usage") or {}
                out["cost"] = float(o.get("total_cost_usd") or 0)
                if o.get("total_cost_usd") is not None:
                    out["reported_cost"] = float(o["total_cost_usd"])
                if o.get("structured_output") is not None:
                    out["structured"] = o["structured_output"]
                if o.get("is_error"):
                    out["error"] = str(o.get("result") or o.get("error") or "")[:500]
                elif not parts and not split and o.get("result"):
                    parts.append(str(o["result"]))
        proc.wait()
    except BaseException as exc:
        failure = exc
        raise
    finally:
        if killer:
            killer.cancel()
        proc.stdout.close()
        drain.join(timeout=2)
        proc.stderr.close()
        drain.join(timeout=2)
        if failure is not None:
            failure.raw_stdout, failure.raw_stdout_truncated = stdout_capture.render()
            failure.raw_stderr, failure.raw_stderr_truncated = stderr_capture.render()
    raw_stdout, raw_stdout_truncated = stdout_capture.render()
    raw_stderr, raw_stderr_truncated = stderr_capture.render()
    out.update({"raw_stdout": raw_stdout, "raw_stderr": raw_stderr,
                "raw_stdout_truncated": raw_stdout_truncated, "raw_stderr_truncated": raw_stderr_truncated})
    out["text"] = "".join(parts).strip()
    lim = common.usage_limit_in(out.get("synthetic") or out["text"] or out.get("error") or raw_stderr, out.get("quota"))
    if lim:
        common.record_usage_limit("claude", lim, (out.get("synthetic") or out["text"])[:200])
        out["limited"] = lim
        out["error"] = f"usage limit: {lim['why']}"
    if proc.returncode != 0 and not out["error"]:
        out["error"] = f"claude exit {proc.returncode}: {raw_stderr.strip()[:500]}"
    out.update(safe_to_retry=safe_to_retry, rejection=rejected or common.rejection("claude", out, model))
    if schema and out["structured"] is None and out["text"]:
        try:
            out["structured"] = json.loads(out["text"])
        except ValueError:
            pass
    return out


def claude_agents() -> list[dict]:
    try:
        p = subprocess.run([config.CLAUDE_BIN, "agents", "--json", "--all"], capture_output=True, text=True,
                           timeout=60, env=common.clean_env())
        data = json.loads(p.stdout or "[]")
    except (subprocess.SubprocessError, ValueError, OSError) as e:
        # An empty fallback would read as "every L2 vanished"; fail loudly instead.
        raise RuntimeError(f"claude agents --json failed: {e}") from e
    if p.returncode != 0:
        raise RuntimeError(f"claude agents --json exit {p.returncode}: {(p.stderr or '')[-300:]}")
    if isinstance(data, dict):
        data = data.get("agents") or data.get("sessions") or []
    return data if isinstance(data, list) else []


def _claude_unit(name: str) -> str:
    return f"altitude-claude-{uuid.uuid5(uuid.NAMESPACE_URL, name).hex}.service"


_FILE_TOOLS = {"Read", "Edit", "Write", "MultiEdit", "NotebookEdit"}


_SUMMARY_KEYS = ("description", "command", "file_path", "notebook_path", "path", "pattern", "query", "skill", "url",
                 "prompt", "text", "message")


_TASK_MESSAGE = re.compile(r"^Message from \w+ \(")


def _claude_path(session_id: str) -> Path | None:
    if not session_id or "/" in session_id or "\\" in session_id:
        return None
    matches = list((config.HOME / ".claude" / "projects").glob(f"*/{session_id}.jsonl"))
    return matches[0] if len(matches) == 1 else None


def _tool_call(block: dict, worktree: str, at) -> dict:
    """One Claude tool_use block: the tool, a one-line summary, and the call's own detail as text (the full
    command, an edit as removed and added lines, a written file's content, otherwise the input)."""
    name = str(block.get("name") or "tool")
    inp = block.get("input") if isinstance(block.get("input"), dict) else {}
    tool_use_id = str(block.get("id") or "") or None
    if name == "Bash":
        command = str(inp.get("command") or "")
        return common._transcript_row("claude", "command", "assistant", command, type="tool_use", at=at, tool=name,
                    summary=common._one_line(command), tool_use_id=tool_use_id)
    if name in _FILE_TOOLS:
        path = common._relative(inp.get("file_path") or inp.get("notebook_path"), worktree)
        old, new = str(inp.get("old_string") or ""), str(inp.get("new_string") or "")
        if name == "Write":
            detail = str(inp.get("content") or "")
        elif old or new:
            detail = "\n".join([f"- {line}" for line in old.splitlines()] + [f"+ {line}" for line in new.splitlines()])
        else:
            detail = ""
        return common._transcript_row("claude", "file", "assistant", detail, type="tool_use", at=at, tool=name, summary=path,
                    tool_use_id=tool_use_id)
    summary = common._first_text(inp, _SUMMARY_KEYS) or (json.dumps(inp, ensure_ascii=False) if inp else "")
    return common._transcript_row("claude", "tool", "assistant", json.dumps(inp, ensure_ascii=False, indent=2) if inp else "",
                type="tool_use", at=at, tool=name, summary=common._one_line(summary), tool_use_id=tool_use_id)


def _result_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for part in content:
        if isinstance(part, dict):
            parts.append(part["text"] if isinstance(part.get("text"), str) else f"[{part.get('type') or 'block'}]")
    return "\n".join(parts)


def _claude_rows(record: dict, worktree: str) -> list[dict]:
    """Rows for one record of Claude's session JSONL, one per content block in the order the model wrote them."""
    typ = str(record.get("type") or "event")
    at = record.get("timestamp")
    message = record.get("message") if isinstance(record.get("message"), dict) else {}
    content = message.get("content")
    rows: list[dict] = []
    if typ in ("user", "assistant"):
        blocks = content if isinstance(content, list) else [{"type": "text", "text": content}] if isinstance(content, str) else []
        injected = typ == "user" and bool(record.get("isMeta") or record.get("isCompactSummary"))
        for block in blocks:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":  # injected context (caveats, skill text, a compaction summary) is not a prompt
                text = str(block.get("text") or "")
                rows.append(common._transcript_row("claude", "engine" if injected else "message", "system" if injected else typ, text,
                                 type=typ, at=at))
            elif btype == "tool_use":
                rows.append(_tool_call(block, worktree, at))
            elif btype == "tool_result":
                rows.append(common._transcript_row("claude", "result", "tool", _result_text(block), type="tool_result", at=at,
                                 tool_use_id=str(block.get("tool_use_id") or "") or None,
                                 error=bool(block.get("is_error"))))
    elif typ == "attachment":  # a task message, delivered by the inbox hook at the worker's checkpoint
        attachment = record.get("attachment") if isinstance(record.get("attachment"), dict) else {}
        parts = attachment.get("content") if isinstance(attachment.get("content"), list) else []
        rows.extend(common._transcript_row("claude", "message", "user", text, type="task-message", at=at)
                    for text in parts if isinstance(text, str) and _TASK_MESSAGE.match(text))
    elif typ == "system":
        text = record.get("content") if isinstance(record.get("content"), str) else ""
        rows.append(common._transcript_row("claude", "message" if text else "engine", "system", text,
                         type=str(record.get("subtype") or typ), at=at))
    return rows or [common._transcript_row("claude", "engine", "system", "", type=typ, at=at)]


_BACKGROUND_OUTCOMES = {"completed": "completed", "failed": "failed", "killed": "stopped", "stopped": "stopped"}


def _epoch_ms(value) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()


JOBS_DIR = config.HOME / ".claude" / "jobs"   # the Claude harness's background-job state, keyed by agent id


def claude_job_detail(agent_id: str | None) -> tuple[str, datetime | None]:
    """What a Claude worker last said about itself (`~/.claude/jobs/<id>/state.json` detail) and when — the limit
    message lands here, and "resets 8pm" only means something relative to the moment it was written."""
    if not agent_id:
        return "", None
    p = JOBS_DIR / str(agent_id) / "state.json"
    try:
        st = json.loads(p.read_text())
        return (str(st.get("error") or st.get("detail") or "") if isinstance(st, dict) else ""), datetime.fromtimestamp(p.stat().st_mtime, timezone.utc)
    except (OSError, ValueError):
        return "", None


CLAUDE_RESULT_FACTS = {
    "subtype": ("success", "error_during_execution", "error_max_turns", "error_max_budget_usd",
                "error_max_structured_output_retries"),
    "terminal_reason": ("blocking_limit", "rapid_refill_breaker", "prompt_too_long", "image_error", "model_error",
                        "api_error", "malformed_tool_use_exhausted", "aborted_streaming", "aborted_tools",
                        "stop_hook_prevented", "hook_stopped", "tool_deferred", "max_turns", "background_requested",
                        "completed", "budget_exhausted", "structured_output_retry_exhausted",
                        "tool_deferred_unavailable", "turn_setup_failed"),
    "api_error": ("max_output_tokens", "dlp_request_denied", "claude_code_version_too_old", "safety_monitor_blocked",
                  "effort_requires_thinking", "advisor_incompatible", "tool_history_mismatch",
                  "autocompact_thrashing", "pdf_too_large", "pdf_password_protected", "media_removed", "no_response",
                  "tls_untrusted_ca", "gateway_content_type", "provider_credentials", "gateway_signin_required",
                  "gateway_session_expired", "api_key_auth_disabled", "org_disabled_credential",
                  "invalid_credential_header", "model_requires_usage_credits", "long_context_credits_required",
                  "consent_unanswered", "no_allowed_fallback", "model_substitution_disabled", "field_not_granted",
                  "usage_limit_reached"),
}


def _review_result_facts(record: dict) -> dict:
    """#665: the fixed failure facts a Claude result carries, so a failure that matches no category still says
    where it ended (setup, API status, error kind) without its prose."""
    facts = {key: (record[key] if record[key] in allowed else "unknown")
             for key, allowed in CLAUDE_RESULT_FACTS.items() if isinstance(record.get(key), str)}
    status = record.get("api_error_status")
    if type(status) is int and 100 <= status <= 599:
        facts["api_error_status"] = status
    if type(record.get("num_turns")) is int and record["num_turns"] >= 0:
        facts["num_turns"] = min(record["num_turns"], 1000)
    if type(record.get("duration_api_ms")) in (int, float):
        facts["api_contacted"] = record["duration_api_ms"] > 0
    return facts


def prepare_worker(name, prompt, *, cwd, persona, model, settings, effort, resume, images):
    cmd = [config.CLAUDE_BIN, "-p", "--input-format", "stream-json", "--replay-user-messages",
           "--output-format", "stream-json", "--verbose", "--name", name,
           "--permission-mode", "auto", "--allowedTools", "Bash(alt *)",
           "--settings", str(settings or claude_settings())]
    if persona:
        cmd += ["--append-system-prompt-file", str(persona)]
    if model:
        cmd += ["--model", model]
    if effort is not None:
        cmd += ["--effort", effort]
    if resume:
        cmd += ["--resume", resume]
    text = prompt
    content = common._engine_input("claude", text, images)
    # I-20260907-171446: valid input precedes retiring an existing named job.
    for row in claude_agents():
        if row.get("name") == name and row.get("state") not in ("done", "failed", "stopped"):
            claude_stop(row["id"])
    return cmd, content, _claude_writable(Path(cwd), *common._worktree_git_dirs(cwd), config.ROOT)


worker_unit_name = _claude_unit


def image_input(prompt, images, native):
    content = [{"type": "text", "text": prompt}]
    for item in images if native else ():
        try:
            data = base64.b64encode(Path(item["path"]).read_bytes()).decode("ascii")
        except OSError as exc:
            raise common.ImageInputError(f"Image {item['id']} is unavailable; the message is retained.") from exc
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": item["mime_type"], "data": data}})
    return content


def transcript_sources(task, *, job_root):
    path = _claude_path(str(task.get("session_id") or ""))
    return [(path, {})] if path and path.is_file() else []


def transcript_rows(record, worktree):
    message = record.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), list):
        message["content"] = [b for b in message["content"]
                              if not (isinstance(b, dict) and str(b.get("type") or "").endswith("thinking"))]
    return _claude_rows(record, worktree)


def background_items(records, items, entry, end):
    called: dict[str, str] = {}
    for record in records:
        subtype = record.get("subtype")
        content = (record.get("message") or {}).get("content") if isinstance(record.get("message"), dict) else None
        if record.get("type") == "assistant" and isinstance(content, list) and isinstance(record.get("timestamp"), str):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id"):
                    called.setdefault(str(block["id"]), record["timestamp"])
        if record.get("type") != "system":
            continue
        if subtype == "background_tasks_changed" and isinstance(record.get("tasks"), list):
            listed = [dict(task, is_backgrounded=True) for task in record["tasks"]
                      if isinstance(task, dict) and True not in (task.get("awaited"), task.get("ambient"))]
            current = {str(task.get("task_id")) for task in listed}
            for item in items.values():
                if item["background"] and item["status"] == "running" and item["id"] not in current:
                    item["gone"] = True
        elif subtype in ("task_started", "task_updated", "task_notification"):
            listed = [record]
        else:
            continue
        for task in listed:
            if not task.get("task_id"):
                continue
            item = entry(str(task["task_id"]))
            patch = task.get("patch") if isinstance(task.get("patch"), dict) else {}
            if isinstance(task.get("description"), str) and task["description"].strip():
                item["label"] = common._one_line(task["description"], 120)
            if isinstance(task.get("task_type"), str):
                item["kind"] = "command" if task["task_type"] == "local_bash" else "agent"
            if isinstance(task.get("tool_use_id"), str) and task["tool_use_id"]:
                item["call"] = task["tool_use_id"]
            item["quiet"] = item["quiet"] or True in (task.get("awaited"), task.get("ambient"))
            if True in (task.get("is_backgrounded"), patch.get("is_backgrounded")):
                item.update(background=True, gone=False)
            outcome = _BACKGROUND_OUTCOMES.get(str(patch.get("status") or task.get("status") or ""))
            if outcome:
                end(item, outcome, _epoch_ms(patch.get("end_time")))
    for item in items.values():
        item["started_at"] = called.get(item["call"] or "")


def review_command(snapshot, runtime, model, images, effort, server):
    # No safe mode: it disables every MCP server, the captured adapter included (I-20260924-080541).
    # Restricted mode ignores user/project/local settings (hooks, plugins), strict MCP configuration
    # admits only the adapter, and "" tools with disabled skills leave captured_input alone.
    return [config.CLAUDE_BIN, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose", "--restricted",
            "--strict-mcp-config", "--mcp-config", json.dumps({"mcpServers": {"captured": server}}),
            "--tools", "", "--allowedTools", "mcp__captured__captured_input", "--permission-mode", "dontAsk",
            "--permission-prompts", "none", "--disable-slash-commands", "--no-chrome", "--no-session-persistence",
            "--settings", "{}", *(["--model", model] if model else []), *(["--effort", effort] if effort else [])]


def review_input(prompt, images):
    content = [{"type": "text", "text": prompt}]
    for index, (title, path) in enumerate(images, 1):
        content += [{"type": "text", "text": f"Screenshot {index}: {title}"},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png" if path.suffix == ".png" else "image/jpeg",
                                                 "data": base64.b64encode(path.read_bytes()).decode("ascii")}}]
    return json.dumps({"type": "user", "message": {"role": "user", "content": content}, "parent_tool_use_id": None}) + "\n"


def token_numbers(usage):
    def number(key):
        value = usage.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

    reads, writes = number("cache_read_input_tokens"), number("cache_creation_input_tokens")
    plain = number("input_tokens")
    inputs = sum(v for v in (plain, reads, writes) if v is not None) if any(v is not None for v in (plain, reads, writes)) else None
    details = usage.get("output_tokens_details") or {}
    reasoning = details.get("thinking_tokens") if isinstance(details, dict) else None
    return {**dict(zip(common._TOKEN_FIELDS, (inputs, number("output_tokens"), reads, writes,
                                     reasoning if isinstance(reasoning, int) and not isinstance(reasoning, bool) and reasoning >= 0 else None))),
            "incomplete": inputs is None or number("output_tokens") is None or
                          (plain is None or reads is None or writes is None)}


def token_sources(home, owners, register):
    sources, pending = [], False
    root = home or config.HOME / ".claude"
    for sid in owners:
        for path in root.glob(f"projects/*/{sid}.jsonl"):
            sources.append((path, sid, "messages"))
        for child in root.glob(f"projects/*/{sid}/subagents/agent-*.jsonl"):
            child_id = f"{sid}/{child.stem}"
            # The directory binds a helper to its owner, not necessarily its spawning helper.
            register(child_id, sid, parentage="owner")
            sources.append((child, child_id, "messages"))
    return sources, pending


def token_event(path, sid, kind, row, file, event, offset):
    at = event.get("timestamp")
    payload = event.get("payload") or {}
    values, key, turn = None, None, None
    if kind == "messages" and event.get("type") == "assistant":
        parent = row["parent_session_id"] or sid
        identity = event.get("sessionId") or event.get("session_id")
        if identity != parent or bool(event.get("isSidechain")) != bool(row["parent_session_id"]):
            return None, None, at
        if row["parent_session_id"] and event.get("agentId") != path.stem.removeprefix("agent-"):
            return None, None, at
        message = event.get("message") or {}
        usage = message.get("usage") or {}
        if not usage or not message.get("id") or message.get("model") == "<synthetic>":
            return None, None, at
        values = token_numbers(usage)
        if not any(values.get(key) for key in common._TOKEN_FIELDS):  # synthetic quota/error records are not an observed zero
            return None, None, at
        key = f"message:{message['id']}"
    elif kind == "stdout" and (event.get("type") == "result" and event.get("session_id") == sid):
        values = token_numbers(event.get("usage") or {})
        if not any(values.get(key) for key in common._TOKEN_FIELDS):
            return None, None, at
        row.setdefault("stdout", {})[path.name] = common._token_max(row.get("stdout", {}).get(path.name, {}), values)
        at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    return values, key, at


def context_window():
    return config.CONTEXT_WINDOW


def event_error(event):
    if event.get("type") in ("error", "turn.failed"):
        return event.get("error") or event.get("message") or event
    if event.get("type") == "result" and event.get("is_error"):
        return {key: event.get(key) for key in ("error", "errors", "result", "api_error_status")}
    message = event.get("message") or {}
    if event.get("type") == "assistant" and message.get("model") == "<synthetic>":
        return {"error": event.get("error"), "message": message.get("content")}
    return None


def rejection_flags(text, said, model, auth, model_missing):
    model_missing |= bool(re.search(
        r"There's an issue with the selected model \([^\n]+\)\. It may not exist or you may not have access to it\.", text))
    if model:
        model_missing |= bool(re.search(r"The model " + re.escape(model) + r" is not available on your [^\n]+ deployment\.", text))
    auth |= any(re.match(r"Failed to authenticate|Not logged in|Invalid (?:API key|auth token)", message)
                for message in said)
    return auth, model_missing


def safe_event(event):
    typ = event.get("type")
    if typ in ("thread.started", "turn.started", "turn.failed", "error"):
        return True
    if typ == "system" and event.get("subtype") in ("init", "status", "api_retry"):
        return True
    if typ == "result":
        return bool(event.get("is_error"))
    if typ == "assistant":
        message = event.get("message") or {}
        return message.get("model") == "<synthetic>" and all(
            item.get("type") == "text" for item in message.get("content") or [])
    return False


def worker_events(path):
    events = common._codex_events(path)
    # I-20260907-171446: read the owned CLI's actual result/error, not the shared daemon registry.
    return [{"type": "thread.started", "thread_id": e.get("session_id"), "model": e.get("model")}
            if e.get("type") == "system" and e.get("subtype") == "init" else
            {"type": "turn.failed" if e.get("is_error") else "turn.completed",
             "message": e.get("errors") or e.get("result") or e.get("subtype"), "usage": e.get("usage")}
            if e.get("type") == "result" else e for e in events]


INSTALL_COMMAND = 'npm install -g @anthropic-ai/claude-code'
SIGN_IN = (('auth', 'status'), 'claude auth login')
IMAGE_READER = 'Read'
EFFORT_ENV = "CLAUDE_CODE_EFFORT_LEVEL"
NATIVE_SEND = True
AUTO_ROTATION = True

def binary():
    return config.CLAUDE_BIN


def session_timeout():
    return config.L3_TURN_TIMEOUT


def image_cli_support(executable):
    result = subprocess.run([executable, *[], "--help"], capture_output=True, text=True, timeout=5, env=common.clean_env())
    return not result.returncode and all(marker in result.stdout for marker in ('--input-format', 'stream-json'))


Driver = _ClaudeDriver


def legacy_worker(task):
    # I-20260907-171446: pre-activation jobs keep their transcript; no daemon launch path remains.
    job = S.read_json(JOBS_DIR / str(task.get("agent_id")) / "state.json", None)
    if job is None:
        return None
    # #676: missing legacy unit identity is unknown, just as it is for owned CLI records.
    if not isinstance(job, dict) or not isinstance(job.get("name"), str) or not job["name"].strip():
        raise RuntimeError("Worker unit identity is unavailable")
    unit = _claude_unit(job["name"])
    transcript = next((config.HOME / ".claude/projects").glob(f"*/{task['session_id']}.jsonl"), None)
    alive = platform.job_active(unit, common.codex_env(retain_user_bus=True)) and transcript is not None
    state = job.get("state") if job.get("state") in ("done", "failed", "stopped") else "working" if alive else "failed"
    detail, at = claude_job_detail(task["agent_id"])
    return {"id": task["agent_id"], "sessionId": task["session_id"], "unit": unit,
            "state": state, "status": "busy" if state == "working" else "exited",
            "detail": detail, "detail_at": at.timestamp() if at else None}


def stop_legacy_worker(worker_id):
    # I-20260907-171446: stop only the adopted job; its absence from a registry is not termination proof.
    job = S.read_json(JOBS_DIR / worker_id / "state.json", None)
    if not isinstance(job, dict) or not isinstance(job.get("name"), str) or not job["name"].strip():
        raise RuntimeError("Worker ownership record is unavailable; stop is unconfirmed")
    note = claude_stop(worker_id)
    if platform.job_active(_claude_unit(job["name"]), common.codex_env(retain_user_bus=True)):
        raise RuntimeError(f"Worker {worker_id} is still running after stop")
    return note


def legacy_unit(task):
    job = S.read_json(JOBS_DIR / task["agent_id"] / "state.json", None)
    if not isinstance(job, dict) or not isinstance(job.get("name"), str) or not job["name"].strip():
        return None
    return _claude_unit(job["name"])


def review_native(executable):
    try:
        result = subprocess.run([executable, "--help"], capture_output=True, text=True, timeout=5, env=common._review_env())
        flags = ("--restricted", "--strict-mcp-config", "--tools", "--permission-prompts", "--input-format")
        if result.returncode or not all(flag in result.stdout for flag in flags):
            return False, (), False
        return True, (), True
    except (OSError, subprocess.SubprocessError):
        return False, (), False


def review_kept(record):
    return record.get("type") == "result"


def review_errors(record):
    messages = []
    if record.get("is_error") is not True:
        return messages, False
    values = record.get("errors", [])
    if isinstance(values, list):
        messages.extend(value for value in values if isinstance(value, str))
    if isinstance(record.get("result"), str):
        messages.append(record["result"])
    if record.get("subtype") == "error_max_turns":
        messages.append("maximum turns reached")
    return messages, True


def review_output(stdout):
    result = next((record for record in reversed([json.loads(line) for line in stdout.splitlines() if line.strip()])
                   if record.get("type") == "result"), {})
    if result.get("is_error"):
        return "", {}, True
    text = result.get("result", "")
    usage = result.get("usage") or {}
    return text, usage, False


def public_message_identity(record):
    message = record.get("message")
    return str(message["id"]) if isinstance(message, dict) and message.get("id") else None


def review_writable(runtime):
    return _claude_writable(Path(runtime))


CONFINEMENT = 'Claude permission rules'
JOB_PROFILE = True


def delivered_context(record: dict) -> list[str]:
    """Positive native hook delivery evidence; ordinary text/tool output is never a receipt."""
    attachment = record.get("attachment")
    if (record.get("type") != "attachment" or not isinstance(attachment, dict)
            or attachment.get("type") != "hook_additional_context"):
        return []
    content = attachment.get("content")
    return [text for text in content if isinstance(text, str)] if isinstance(content, list) else []


def observe_worker_model(record, events, record_path):
    pass  # The initialization event already reports the model.
