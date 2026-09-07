"""Headless Claude Code and Codex command builders and runners."""
from __future__ import annotations
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config, state as S

logger = logging.getLogger(__name__)

L3_ALLOWED_TOOLS = ",".join((
    "Read", "Grep", "Glob",
    "Bash(alt project set *)", "Bash(alt state *)", "Bash(alt task new *)", "Bash(alt task reject *)",
    "Bash(alt task report *)", "Bash(alt task messages *)", "Bash(alt task events *)",
    "Bash(alt task status *)", "Bash(alt task show *)", "Bash(alt task list *)", "Bash(alt task message *)",
    "Bash(alt task escalate *)", "Bash(alt task resume *)", "Bash(alt task stop *)",
    "Bash(alt task paths *)", "Bash(alt task hold-merge *)", "Bash(alt task done *)",
    "Bash(alt fyi *)", "Bash(alt decisions *)", "Bash(alt monitor *)", "Bash(alt queue *)",
    "Bash(alt repo *)", "Bash(alt pr *)", "Bash(alt l3 tools *)",
    "Bash(alt incident new *)", "Bash(alt incident amend *)", "Bash(alt incident list *)",
    "Bash(git log *)", "Bash(git diff --stat *)", "Bash(git show --stat *)",
    "Bash(gh pr view *)", "Bash(gh pr list *)", "Bash(gh pr diff *)", "Bash(gh pr checks *)",
    "Bash(gh issue list *)", "Bash(gh issue view *)", "Bash(gh run list *)", "Bash(gh run view *)",
    "Bash(gh run watch *)",
    "Bash(journalctl --user -u altitude*)", "Bash(systemctl --user status altitude*)",
))
_codex_processes: dict[str, subprocess.Popen] = {}

CODEX_SYSTEMD_PREFIX = "altitude-codex-"
SYSTEMD_RUN_BIN = shutil.which("systemd-run") or "systemd-run"
SYSTEMCTL_BIN = shutil.which("systemctl") or "systemctl"
ENV_BIN = shutil.which("env") or "/usr/bin/env"

# Claude's stream-json can be much larger than its final answer. Keep raw capture bounded while preserving evidence
# from both ends.
RAW_CAPTURE_CAP = 2 * 1024 * 1024
CODEX_PATCH_NOTE = (
    "[altitude] Host patch constraint: Do not call the custom `apply_patch` tool, because its filesystem verifier "
    "cannot create its bwrap namespace under this host's AppArmor policy. For every edit, call the shell command "
    "`apply_patch` through the exec tool and pass the patch on stdin; this stays inside the Codex workspace-write "
    "sandbox and its configured writable roots."
)


def cap_raw(data: bytes, cap: int, *, total: int | None = None) -> tuple[bytes, bool]:
    """Cap raw bytes, retaining the head and tail with an exact drop notice."""
    total = len(data) if total is None else total
    if total <= cap:
        return data, False
    dropped = total - cap
    while True:
        notice = f"\n\n[altitude: raw output truncated; {dropped} bytes dropped]\n\n".encode()
        kept = max(0, cap - len(notice))
        exact = total - kept
        if exact == dropped:
            break
        dropped = exact
    head = kept // 2
    tail = kept - head
    return data[:head] + notice + (data[-tail:] if tail else b""), True


class _BoundedRawCapture:
    """Collect at most ``cap`` bytes, retaining the head and tail with an exact drop notice."""

    def __init__(self, cap: int | None = None):
        self.cap = RAW_CAPTURE_CAP if cap is None else cap
        self.total = 0
        self.head = bytearray()
        self.tail = bytearray()
        self.head_limit = self.cap // 2
        self.tail_limit = self.cap - self.head_limit

    def add(self, text: str) -> None:
        data = text.encode("utf-8", errors="replace")
        self.total += len(data)
        room = self.head_limit - len(self.head)
        if room > 0:
            self.head.extend(data[:room])
            data = data[room:]
        if data:
            self.tail.extend(data)
            if len(self.tail) > self.tail_limit:
                del self.tail[:len(self.tail) - self.tail_limit]

    def render(self) -> tuple[str, bool]:
        data, truncated = cap_raw(bytes(self.head + self.tail), self.cap, total=self.total)
        return data.decode("utf-8", errors="replace"), truncated

# ---- usage limit: the subscription window closing is a timed hold, not a failure ----------
LIMIT_TEXT = re.compile(r"hit your (?:session|usage) limit|usage limit reached|out of (?:extra )?usage|rate limit reached", re.I)
RESETS = re.compile(r"resets?\s+(?:(?:at|in)\s+)?(?:([A-Za-z]{3,9}\s+\d{1,2}),?\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)(?:\s*\(([^)]+)\))?", re.I)
TEMPORARY_CAPACITY_TEXT = "Selected model is at capacity. Please try a different model."


def temporary_capacity_in(text: str | None) -> bool:
    """Recognize the provider's exact temporary-capacity warning, not a quota exhaustion."""
    return bool(text and TEMPORARY_CAPACITY_TEXT in text)


def usage_limit_in(text: str | None, quota: dict | None = None, now: datetime | None = None) -> str | None:
    """The UTC ISO time the window reopens if `text`/`quota` say it is exhausted, else None.

    Claude Code says it two ways: a synthetic assistant message ("You've hit your session limit · resets 8pm
    (America/Los_Angeles)") and, on the same record, `quotaLimits: {status: rejected, resetsAt: <epoch>}`."""
    now = now or datetime.now(timezone.utc)
    if quota and quota.get("status") == "rejected" and quota.get("resetsAt"):
        return datetime.fromtimestamp(int(quota["resetsAt"]), timezone.utc).isoformat(timespec="seconds")
    if not text or not LIMIT_TEXT.search(text):
        return None
    m = RESETS.search(text)
    if not m:
        return (now + timedelta(hours=1)).isoformat(timespec="seconds")  # no time given: hold an hour, then look again
    day, hour, minute, ampm, tz = m.groups()
    try:
        zone = ZoneInfo(tz) if tz else (datetime.now().astimezone().tzinfo or timezone.utc)
    except Exception:  # noqa: BLE001 — unknown zone name
        zone = timezone.utc
    local = now.astimezone(zone)
    when = local.replace(hour=int(hour) % 12 + (12 if ampm.lower() == "pm" else 0), minute=int(minute or 0), second=0, microsecond=0)
    if day:
        for fmt in ("%b %d", "%B %d"):
            try:
                d = datetime.strptime(day, fmt); when = when.replace(month=d.month, day=d.day); break
            except ValueError:
                continue
    if when <= local:
        when += timedelta(days=1)
    return when.astimezone(timezone.utc).isoformat(timespec="seconds")


def usage_limit_path() -> Path:
    return config.MONITOR_DIR / "usage-limit.json"


def note_usage_limit(until: str, detail: str = "") -> bool:
    """Record an exhausted window. True when the reset time is news, so callers post one FYI per window."""
    p = usage_limit_path(); p.parent.mkdir(parents=True, exist_ok=True)
    try:
        cur = json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        cur = {}
    if cur.get("until") == until:
        return False
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"until": until, "seen": datetime.now(timezone.utc).isoformat(timespec="seconds"), "detail": detail[:200]}))
    os.replace(tmp, p)
    return True


def usage_hold() -> str | None:
    """The reset time while the window is exhausted, else None. Dispatch and L3 turns check this first."""
    p = usage_limit_path()
    try:
        until = json.loads(p.read_text()).get("until") if p.exists() else None
    except (ValueError, OSError):
        return None
    if until and datetime.fromisoformat(until) > datetime.now(timezone.utc):
        return until
    return None


def claude_stop(agent_id: str) -> str:
    p = subprocess.run([config.CLAUDE_BIN, "stop", agent_id], capture_output=True, text=True, timeout=60, env=clean_env())
    if p.returncode:  # I-20260907-171446: never start a copy while an orphan owns the session.
        raise RuntimeError((p.stderr or p.stdout).strip())
    deadline = time.monotonic() + 5
    while any(row.get("id") == agent_id and row.get("state") not in ("done", "failed", "stopped")
              for row in claude_agents()):
        if time.monotonic() >= deadline:
            raise RuntimeError(f"daemon job {agent_id} is still running after stop")
        time.sleep(0.05)
    return (p.stdout or p.stderr).strip()


def clean_env() -> dict:
    """Nested launches need CLAUDE* unset (verified); keep PATH sane for systemd."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    env.setdefault("HOME", str(Path.home()))
    env["PATH"] = str(config.REPO / "bin") + ":" + env.get("PATH", "/usr/bin:/bin") + ":" + str(Path.home() / ".local/bin")
    return env


def service_status(unit: str = "altitude.service") -> dict:
    """Read the user service's state once; inspection failure stays in the record."""
    record = {"unit": unit, "state": None, "substate": None, "pid": None,
              "last_restart": None, "error": None}
    try:
        result = subprocess.run(
            [SYSTEMCTL_BIN, "--user", "show", unit, "--property=ActiveState",
             "--property=SubState", "--property=MainPID", "--property=ActiveEnterTimestamp"],
            capture_output=True, text=True, timeout=15, env=codex_env(retain_user_bus=True))
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout).strip() or f"exit {result.returncode}")
        values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        record.update({"state": values.get("ActiveState"), "substate": values.get("SubState"),
                       "pid": int(values.get("MainPID") or 0) or None,
                       "last_restart": values.get("ActiveEnterTimestamp") or None})
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as exc:
        record["error"] = str(exc)[:240]
    return record


def _tool(name: object, command: object = None) -> dict:
    entry = {"name": str(name or "tool")}
    if command is not None:
        entry["command"] = str(command)
    return entry


def codex_env(extra_env: dict | None = None, *, retain_user_bus: bool = False) -> dict:
    """Altitude's clean environment plus the task identity, the same a Claude worker gets.

    The user bus belongs to the outer ``systemd-run`` client only: a system service does not necessarily inherit the
    interactive session's bus variables, so the launcher synthesizes their canonical per-user values, and the
    command inside the transient unit starts without them.
    """
    env = clean_env()
    env.update(extra_env or {})
    env["TMPDIR"] = "/tmp"
    runtime_dir = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    if retain_user_bus:
        env["XDG_RUNTIME_DIR"] = runtime_dir
        env["DBUS_SESSION_BUS_ADDRESS"] = env.get("DBUS_SESSION_BUS_ADDRESS") or f"unix:path={runtime_dir}/bus"
    else:
        env.pop("XDG_RUNTIME_DIR", None)
        env.pop("DBUS_SESSION_BUS_ADDRESS", None)
    return env


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


def claude_print(prompt: str, *, cwd: Path, resume: str | None = None, persona: Path | None = None,
                 allowed_tools: str | None = None, tools: str | None = None, permission_mode: str = "auto",
                 schema: Path | None = None, model: str | None = None, max_turns: int | None = None,
                 settings: Path | None = None, extra_env: dict | None = None, on_text=None, on_start=None,
                 timeout: int = config.L3_TURN_TIMEOUT, restricted: bool = False,
                 add_dirs: tuple[Path, ...] = (), permission_prompts: str | None = None) -> dict:
    """One headless turn. Returns text, session_id, usage, cost, turns, structured (if schema), error, and bounded
    raw_stdout/raw_stderr; `limited` (a reset time) when the subscription window is exhausted — the call is not even
    made while a hold is in force.

    `on_start(pid)` is called the moment the child exists. The turn outlives altd, so its pid lets a
    restarted server distinguish an in-flight turn from a dead one."""
    held = usage_hold()
    if held:
        return {"text": "", "session_id": resume or "", "usage": {}, "context_tokens": 0, "cost": 0.0, "turns": 0,
                "structured": None, "error": f"usage limit: window exhausted until {held}", "tools": [], "limited": held,
                "raw_stdout": "", "raw_stderr": "", "raw_stdout_truncated": False, "raw_stderr_truncated": False}
    cmd = [config.CLAUDE_BIN, "-p", "--output-format", "stream-json", "--include-partial-messages", "--verbose",
           "--permission-mode", permission_mode]
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
    if max_turns:
        cmd += ["--max-turns", str(max_turns)]
    cmd += ["--settings", str(settings or claude_settings())]
    if resume:
        cmd += ["--resume", resume]
    env = clean_env()
    env.update(extra_env or {})
    # prompt goes through stdin: --allowedTools is variadic and would swallow a positional prompt
    proc = subprocess.Popen(cmd, cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env)
    if on_start:
        on_start(proc.pid)
    try:
        proc.stdin.write(prompt)
        proc.stdin.close()
    except (BrokenPipeError, OSError):
        pass
    stdout_capture, stderr_capture = _BoundedRawCapture(), _BoundedRawCapture()

    def drain_stderr() -> None:
        while chunk := proc.stderr.read(65536):
            stderr_capture.add(chunk)

    drain = threading.Thread(target=drain_stderr, daemon=True)
    drain.start()
    killer = threading.Timer(timeout, proc.kill)
    killer.start()
    out = {"text": "", "session_id": resume or "", "usage": {}, "context_tokens": 0, "cost": 0.0,
           "turns": 0, "structured": None, "error": None, "tools": []}
    parts: list[str] = []
    failure = None
    try:
        for line in proc.stdout:
            stdout_capture.add(line)
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if o.get("session_id"):
                out["session_id"] = o["session_id"]
            typ = o.get("type")
            if typ == "stream_event":
                delta = (o.get("event") or {}).get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    parts.append(delta["text"])
                    if on_text:
                        on_text(delta["text"])
            elif typ == "assistant":
                out["turns"] += 1
                msg = o.get("message") or {}
                u = msg.get("usage") or {}
                if u:
                    out["context_tokens"] = int(u.get("input_tokens", 0)) + int(u.get("cache_read_input_tokens", 0)) + int(u.get("cache_creation_input_tokens", 0))
                for c in msg.get("content") or []:
                    if c.get("type") == "tool_use":
                        inp = c.get("input") if isinstance(c.get("input"), dict) else {}
                        out["tools"].append(_tool(c.get("name"), inp.get("command")
                                                  if c.get("name") == "Bash" else None))
                    elif c.get("type") == "text" and msg.get("model") == "<synthetic>":
                        out["synthetic"] = (out.get("synthetic") or "") + str(c.get("text") or "")
                q = o.get("quotaLimits") or msg.get("quotaLimits")
                if isinstance(q, dict):
                    out["quota"] = q
            elif typ == "result":
                out["usage"] = o.get("usage") or {}
                out["cost"] = float(o.get("total_cost_usd") or 0)
                if o.get("structured_output") is not None:
                    out["structured"] = o["structured_output"]
                if o.get("is_error"):
                    out["error"] = str(o.get("result") or o.get("error") or "")[:500]
                elif not parts and o.get("result"):
                    parts.append(str(o["result"]))
        proc.wait()
    except BaseException as exc:
        failure = exc
        raise
    finally:
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
    lim = usage_limit_in(out.get("synthetic") or out["text"] or raw_stderr, out.get("quota"))
    if lim:
        note_usage_limit(lim, (out.get("synthetic") or out["text"])[:200])
        out["limited"] = lim
        out["error"] = f"usage limit: window exhausted until {lim}"
    if proc.returncode != 0 and not out["error"]:
        out["error"] = f"claude exit {proc.returncode}: {raw_stderr.strip()[:500]}"
    if schema and out["structured"] is None and out["text"]:
        try:
            out["structured"] = json.loads(out["text"])
        except ValueError:
            pass
    return out


def claude_agents() -> list[dict]:
    try:
        p = subprocess.run([config.CLAUDE_BIN, "agents", "--json", "--all"], capture_output=True, text=True,
                           timeout=60, env=clean_env())
        data = json.loads(p.stdout or "[]")
    except (subprocess.SubprocessError, ValueError, OSError) as e:
        # An empty fallback would read as "every L2 vanished"; fail loudly instead.
        raise RuntimeError(f"claude agents --json failed: {e}") from e
    if p.returncode != 0:
        raise RuntimeError(f"claude agents --json exit {p.returncode}: {(p.stderr or '')[-300:]}")
    if isinstance(data, dict):
        data = data.get("agents") or data.get("sessions") or []
    return data if isinstance(data, list) else []


def _codex_unit(worker_id: str) -> str:
    """A systemd-safe, collision-resistant transient service name."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "-", str(worker_id))
    return f"{CODEX_SYSTEMD_PREFIX}{safe}.service"


def _claude_unit(name: str) -> str:
    return f"altitude-claude-{uuid.uuid5(uuid.NAMESPACE_URL, name).hex}.service"


def _codex_service_command(unit: str, command: list[str], child_env: dict[str, str]) -> list[str]:
    """Run a turn in a user-manager-created transient service with its own cgroup.

    ``--wait --pipe`` keeps the launch synchronous while the user manager, rather than the hardened Altitude parent,
    creates the child. This lets nested bwrap initialize without weakening altd's ``NoNewPrivileges=yes`` boundary.
    Unlike a process group, the service cgroup retains descendants that call ``setsid`` or double-fork. The inner
    Codex sandbox supplies the PID namespace; keeping syscall filters off the outer service preserves nested bwrap.
    """
    # A transient service inherits the user manager's environment, not the launching client's. Clear it completely
    # and reconstruct only the already-sanitized child environment so task identity survives without ambient manager
    # credentials or control sockets crossing the boundary.
    scrub = [ENV_BIN, "-i", *(f"{key}={child_env[key]}" for key in sorted(child_env))]
    return [SYSTEMD_RUN_BIN, "--user", "--wait", "--pipe", f"--unit={unit}", "--quiet", "--collect",
            "--same-dir", "--expand-environment=no", "--property=KillMode=control-group",
            "--property=SendSIGKILL=yes", "--property=NoNewPrivileges=no", "--", *scrub, *command]


def _codex_paths(job_root: Path, worker_id: str) -> dict[str, Path]:
    root = Path(job_root)
    return {"record": root / f"{worker_id}.json", "stdout": root / f"{worker_id}.stdout.jsonl",
            "stderr": root / f"{worker_id}.stderr.log"}


def _codex_parse(text: str) -> list[dict]:
    events = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue  # an incomplete final record is read again next time
        if isinstance(event, dict):
            events.append(event)
    return events


def _codex_events(path: Path) -> list[dict]:
    try:
        return _codex_parse(path.read_text(errors="replace"))
    except OSError:
        return []


def codex_turns(job_root: Path, session_id: str) -> list[Path]:
    """The stdout JSONL of every turn of one thread under `job_root`, oldest first (one worker record per turn)."""
    turns = []
    for record_path in Path(job_root).glob("*.json"):
        record = S.read_json(record_path, None)
        if not isinstance(record, dict):
            continue
        stdout = _codex_paths(job_root, record_path.stem)["stdout"]
        thread = _codex_thread(_codex_events(stdout)) or record.get("session_id")
        if thread == session_id and stdout.is_file():
            turns.append((str(record.get("started_at") or ""), record_path.stem, stdout))
    return [path for _, _, path in sorted(turns)]


def _codex_thread(events: list[dict]) -> str | None:
    for event in events:
        if event.get("type") == "thread.started" and event.get("thread_id"):
            return str(event["thread_id"])
    return None


def _codex_usage(events: list[dict]) -> dict:
    for event in reversed(events):
        if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
            return dict(event["usage"])
    return {}


def _codex_session_model(session_id: str | None, started_at: str, home: Path) -> dict:
    """altd reads this turn's rollout context, never the requested/default model (CLI 0.153.4)."""
    if not session_id or not re.fullmatch(r"[A-Za-z0-9-]+", session_id):
        return {}
    try:
        for path in home.glob(f"sessions/*/*/*/rollout-*-{session_id}.jsonl"):
            with path.open() as rollout:
                for line in rollout:
                    if '"turn_context"' not in line:
                        continue
                    try:
                        event = json.loads(line)
                        if (event.get("type") != "turn_context" or
                                datetime.fromisoformat(event["timestamp"]) < datetime.fromisoformat(started_at)):
                            continue
                        context = event["payload"]
                        if isinstance(context.get("model"), str) and context["model"]:
                            return {"engine_model": context["model"],
                                    "engine_reasoning_effort": context.get("effort")}
                    except (ValueError, KeyError, TypeError):
                        continue  # a partially flushed rollout is retried on the next poll
    except OSError:
        pass  # optional telemetry must not stop a worker when its rollout is unavailable
    return {}


def _codex_home(env: dict) -> Path:
    return Path(env.get("CODEX_HOME") or Path(env.get("HOME") or config.HOME) / ".codex")


def _git_dirs(cwd: Path) -> list[Path]:
    """The Git directories a worker writes: the common directory (objects, refs) and, for a linked worktree, its
    own metadata under `.git/worktrees/<name>` (HEAD, index, FETCH_HEAD). Codex marks that metadata read-only
    unless it is a writable root of its own, which blocked task give-the-chat-section-its-own-scrollbar at
    `git fetch` on 2026-09-03 (codex 0.153)."""
    p = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir", "--git-dir"],
                       cwd=str(cwd), capture_output=True, text=True, timeout=30)
    if p.returncode != 0:
        raise RuntimeError(f"not a Git worktree: {cwd}: {(p.stderr or '').strip()[:200]}")
    return list(dict.fromkeys(Path(line.strip()) for line in p.stdout.splitlines() if line.strip()))


def codex_sandbox(cwd: Path, *, extra_roots: list[Path] = ()) -> list[str]:
    """Codex's own workspace-write sandbox is the turn's containment (`-c` overrides, verified with codex 0.152).

    Writable roots must exist because Codex bind-mounts them: the working directory, any extra root (a worker's
    Git directories so it can fetch, commit, and push), and the Altitude home so `alt` can record what the turn
    reports. Everything else is readable. Network stays on for `git push`, `gh`, and the repository's own tests.
    The sandboxed shell inherits the launch environment, so the identity variables reach `alt` unchanged.
    """
    roots = [Path(cwd).resolve(), *(Path(root).resolve() for root in extra_roots), config.ROOT.resolve()]
    return ['sandbox_mode="workspace-write"',
            "sandbox_workspace_write.writable_roots=" + json.dumps([str(root) for root in roots]),
            "sandbox_workspace_write.network_access=true", 'approval_policy="never"']


def codex_l3_permissions(cwd: Path, *, project: str) -> list[str]:
    """A Codex profile which denies L3 checkout/service writes and direct command networking.

    I-20260903-075410: L3 needs broad reads and one disposable writable cwd. ``alt`` plus GitHub/service reads
    cross altd's fixed Unix-socket protocol; the model cannot write Altitude state, reach mutating HTTP APIs,
    use authenticated GitHub directly, connect to the user bus, or write a checkout.
    """
    profile = "altitude-l3"
    bus = f"/run/user/{os.getuid()}/bus"
    from .l3 import verb_socket_path
    broker = verb_socket_path(project)
    rules = {":root": "read", str(Path(cwd).resolve()): "write",
             str(config.project_path(project).resolve()): "read", str(broker.resolve()): "read", bus: "deny"}
    filesystem = "{" + ",".join(f"{json.dumps(path)}={json.dumps(access)}"
                                   for path, access in rules.items()) + "}"
    sockets = "{" + ",".join((f"{json.dumps(str(broker.resolve()))}=\"allow\"",
                                f"{json.dumps(bus)}=\"deny\"")) + "}"
    return [f'default_permissions="{profile}"', f'permissions.{profile}.extends=":read-only"',
            f"permissions.{profile}.filesystem={filesystem}",
            "features.network_proxy=true",
            f"permissions.{profile}.network.enabled=true",
            f"permissions.{profile}.network.allow_local_binding=false",
            f"permissions.{profile}.network.allow_upstream_proxy=false",
            f"permissions.{profile}.network.unix_sockets={sockets}", 'approval_policy="never"']


def _unit_active(unit: str) -> bool:
    if not unit:
        return False
    p = subprocess.run([SYSTEMCTL_BIN, "--user", "is-active", unit], capture_output=True, text=True, timeout=30,
                       env=codex_env(retain_user_bus=True))
    return (p.stdout or "").strip() in ("active", "activating", "deactivating")


def _worker_events(path: Path, engine: str) -> list[dict]:
    events = _codex_events(path)
    if engine == "codex":
        return events
    # I-20260907-171446: read the owned CLI's actual result/error, not the shared daemon registry.
    return [{"type": "thread.started", "thread_id": e["session_id"], "model": e.get("model")}
            if e.get("type") == "system" and e.get("subtype") == "init" else
            {"type": "turn.failed" if e.get("is_error") else "turn.completed",
             "message": e.get("errors") or e.get("result") or e.get("subtype"), "usage": e.get("usage")}
            if e.get("type") == "result" else e for e in events]


def codex_worker(worker_id: str | None, *, job_root: Path) -> dict | None:
    """Read an owned CLI turn; the persisted engine selects its output format."""
    if not worker_id:
        return None
    paths = _codex_paths(job_root, worker_id)
    record = S.read_json(paths["record"], None)
    if not isinstance(record, dict):
        return None
    engine = record.get("engine", "codex")
    events = _worker_events(paths["stdout"], engine)
    if engine == "codex" and not record.get("engine_model"):
        metadata = _codex_session_model(_codex_thread(events), record["started_at"],
                                       Path(record.get("codex_home") or _codex_home(codex_env())))
        if metadata:
            record.update(metadata)
            S.write_json(paths["record"], record)
    proc = _codex_processes.get(worker_id)
    alive = proc.poll() is None if proc is not None else _unit_active(str(record.get("unit") or ""))
    if proc is not None and not alive:
        _codex_processes.pop(worker_id, None)
    completed = any(event.get("type") == "turn.completed" for event in events)
    failed = next((event for event in reversed(events) if event.get("type") in ("turn.failed", "error")), None)
    if record.get("stopped"):
        state, status = "stopped", "exited"
    elif alive:
        state, status = "working", "busy"
    elif completed:
        state, status = "done", "exited"
    else:
        state, status = "failed", "exited"
    detail = ""
    if failed:
        detail = str(failed.get("message") or failed.get("error") or failed)[:500]
    elif not alive and not completed:
        try:
            detail = (paths["stderr"].read_text(errors="replace")[-500:]
                      or paths["stdout"].read_text(errors="replace")[-500:] or "Worker exited without a result")
        except OSError:
            detail = "Worker exited without a result"
    return {"id": worker_id, "sessionId": _codex_thread(events) or record.get("session_id"),
            "name": record.get("name"), "pid": record.get("pid"), "unit": record.get("unit"),
            "state": state, "status": status, "detail": detail, "usage": _codex_usage(events),
            "detail_at": max((paths[k].stat().st_mtime for k in ("stdout", "stderr") if paths[k].exists()),
                             default=paths["record"].stat().st_mtime),
            "startedAt": record.get("started_at"), "engine": engine,
            "engine_model": next((e["model"] for e in events if e.get("model")), record.get("engine_model")),
            "engine_reasoning_effort": record.get("engine_reasoning_effort")}


def codex_bg(name: str, prompt: str, *, cwd: Path, job_root: Path, resume: str | None = None,
             persona: Path | None = None, model: str | None = None, extra_env: dict | None = None,
             start_timeout: float = 15.0) -> dict:
    return _start_worker("codex", name, prompt, cwd=cwd, job_root=job_root, resume=resume,
                         persona=persona, model=model, extra_env=extra_env, start_timeout=start_timeout)


def _start_worker(engine: str, name: str, prompt: str, *, cwd: Path, job_root: Path, resume: str | None = None,
                  persona: Path | None = None, model: str | None = None, extra_env: dict | None = None,
                  settings: Path | None = None, start_timeout: float = 15.0) -> dict:
    """One foreground CLI per transient unit; both engines persist identity and output for adoption."""
    if engine == "claude":
        # I-20260907-171446: retire daemon jobs bound to this name before launch or resume.
        for row in claude_agents():
            if row.get("name") == name and row.get("state") not in ("done", "failed", "stopped"):
                claude_stop(row["id"])
    worker_id = uuid.uuid4().hex
    unit = _codex_unit(worker_id) if engine == "codex" else _claude_unit(worker_id)
    root = Path(job_root)
    root.mkdir(parents=True, exist_ok=True)
    paths = _codex_paths(root, worker_id)
    if engine == "claude":
        cmd = [config.CLAUDE_BIN, "-p", "--output-format", "stream-json", "--verbose", "--name", name,
               "--permission-mode", "auto", "--settings", str(settings or claude_settings())]
        if persona:
            cmd += ["--append-system-prompt-file", str(persona)]
        if model:
            cmd += ["--model", model]
        if resume:
            cmd += ["--resume", resume]
        text = prompt
    elif engine == "codex":
        cmd = [config.CODEX_BIN, "exec", *(["resume"] if resume else []), "--json", "--strict-config",
               "--skip-git-repo-check", *([] if resume else ["-C", str(cwd)])]
        if model:
            cmd += ["-m", model]
        for setting in codex_sandbox(cwd, extra_roots=_git_dirs(cwd)):
            cmd += ["-c", setting]
        cmd += [resume, "-"] if resume else ["-"]
        text = prompt if resume else (
            ((Path(persona).read_text() + "\n\n") if persona else "") + CODEX_PATCH_NOTE + "\n\n" + prompt)
    else:
        raise ValueError(f"unknown L2 engine {engine!r}")
    record = {"id": worker_id, "name": name, "pid": None, "unit": unit, "engine": engine,
              "started_at": datetime.now(timezone.utc).isoformat(),
              "codex_home": str(_codex_home(codex_env(extra_env))),
              "session_id": resume, "cwd": str(cwd), "resume": bool(resume), "stopped": None}
    S.write_json(paths["record"], record)
    try:
        with open(paths["stdout"], "ab", buffering=0) as out, open(paths["stderr"], "ab", buffering=0) as err:
            proc = subprocess.Popen(_codex_service_command(unit, cmd, codex_env(extra_env)), cwd=str(cwd),
                                    stdin=subprocess.PIPE, stdout=out, stderr=err,
                                    env=codex_env(extra_env, retain_user_bus=True), start_new_session=True)
        try:
            proc.stdin.write(text.encode("utf-8"))
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        _codex_processes[worker_id] = proc
        record["pid"] = proc.pid
        S.write_json(paths["record"], record)
        deadline = time.monotonic() + start_timeout
        thread_id = None
        while not thread_id and time.monotonic() < deadline and proc.poll() is None:
            thread_id = _codex_thread(_worker_events(paths["stdout"], engine))
            if not thread_id:
                time.sleep(0.05)
        thread_id = thread_id or _codex_thread(_worker_events(paths["stdout"], engine))
        if not thread_id or (resume and thread_id != resume):
            codex_stop(worker_id, job_root=root)
            row = codex_worker(worker_id, job_root=root) or {}
            detail = (f"resumed a different {engine.title()} thread" if thread_id else
                      row.get("detail") or "no session initialization event")
            return {"stdout": "", "stderr": str(detail), "returncode": 1, "agent": row}
        record["session_id"] = thread_id
        S.write_json(paths["record"], record)
        return {"stdout": "", "stderr": "", "returncode": 0, "agent": codex_worker(worker_id, job_root=root)}
    except BaseException as exc:
        # I-20260907-171446: startup must not leave an unbound worker that a retry duplicates.
        try:
            codex_stop(worker_id, job_root=root)
        except Exception as stop_error:
            raise RuntimeError(f"worker {worker_id}: {exc}; stop failed: {stop_error}") from exc
        raise


def codex_stop(worker_id: str, *, job_root: Path) -> str:
    """Stop the worker's transient unit; `KillMode=control-group` takes every descendant with it."""
    paths = _codex_paths(job_root, worker_id)
    record = S.read_json(paths["record"], None)
    if not isinstance(record, dict):
        return "Codex worker record already absent"
    unit = str(record.get("unit") or "")
    subprocess.run([SYSTEMCTL_BIN, "--user", "stop", unit], capture_output=True, text=True, timeout=120,
                   env=codex_env(retain_user_bus=True))
    proc = _codex_processes.pop(worker_id, None)
    if proc is not None:
        try:
            proc.wait(timeout=10)
        except (subprocess.TimeoutExpired, OSError):
            proc.kill()
    if _unit_active(unit):
        raise RuntimeError(f"Codex worker {worker_id} is still running after stop")
    record["stopped"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    S.write_json(paths["record"], record)
    return "Codex worker stopped"


def start_l2(engine: str, name: str, prompt: str, *, cwd: Path, persona: Path,
             model: str | None, settings: Path, extra_env: dict, job_root: Path) -> dict:
    return _start_worker(engine, name, prompt, cwd=cwd, persona=persona, model=model, settings=settings,
                         extra_env=extra_env, job_root=job_root)


def resume_l2(engine: str, name: str, session_id: str, prompt: str, *, cwd: Path, persona: Path,
              model: str | None, settings: Path, extra_env: dict, job_root: Path) -> dict:
    return _start_worker(engine, name, prompt, cwd=cwd, resume=session_id, persona=persona, model=model,
                         settings=settings, extra_env=extra_env, job_root=job_root)


def stop_l2_worker(engine: str, worker_id: str, *, job_root: Path) -> str:
    if engine == "claude" and not _codex_paths(job_root, worker_id)["record"].exists():
        return claude_stop(worker_id)  # I-20260907-171446: never stop an adopted job's shared daemon.
    return codex_stop(worker_id, job_root=job_root)


def remove_l2_worker(engine: str, worker_id: str, *, job_root: Path) -> str:
    return stop_l2_worker(engine, worker_id, job_root=job_root)


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


def window_hold(engine: str) -> str | None:
    """Claude's exhausted window is a machine-wide hold file; Codex reports its limits per turn."""
    return usage_hold() if engine == "claude" else None


def worker(engine: str, task: dict, *, job_root: Path) -> dict | None:
    """Adopt owned turns from their unit record, or existing jobs from unit plus transcript."""
    row = codex_worker(task.get("agent_id"), job_root=job_root)
    if row or engine != "claude":
        return row
    # I-20260907-171446: pre-activation jobs keep their transcript; no daemon launch path remains.
    job = S.read_json(JOBS_DIR / str(task.get("agent_id")) / "state.json", {})
    if not job:
        return None
    unit = _claude_unit(job["name"])
    transcript = next((config.HOME / ".claude/projects").glob(f"*/{task['session_id']}.jsonl"), None)
    alive = _unit_active(unit) and transcript is not None
    state = job.get("state") if job.get("state") in ("done", "failed", "stopped") else "working" if alive else "failed"
    detail, at = claude_job_detail(task["agent_id"])
    return {"id": task["agent_id"], "sessionId": task["session_id"], "unit": unit,
            "state": state, "status": "busy" if state == "working" else "exited",
            "detail": detail, "detail_at": at.timestamp() if at else None}


def worker_detail(engine: str, row: dict | None) -> tuple[str, datetime | None]:
    """The worker's last words and when it said them, for capacity and usage-limit detection."""
    # I-20260907-171446: relative reset times belong to the output, including after adoption.
    at = (row or {}).get("detail_at")
    return str((row or {}).get("detail") or ""), datetime.fromtimestamp(at, timezone.utc) if at else datetime.now(timezone.utc)


def worker_live(engine: str, task: dict, *, job_root: Path) -> bool:
    row = worker(engine, task, job_root=job_root)
    return bool(row and row.get("state") == "working")


def codex_exec(prompt: str, *, cwd: Path, model: str | None = None, timeout: int = 900, effort: str | None = None,
               extra_env: dict | None = None, resume: str | None = None, on_start=None,
               sandbox_settings: list[str] | None = None, ignore_user_config: bool = False, on_session=None) -> dict:
    """One synchronous Codex turn (L3) in Codex's own workspace-write sandbox, prompt on stdin (verified with
    codex 0.152). `codex exec resume <thread> -` continues the thread. The transient unit is the one workers use,
    so altd's `NoNewPrivileges` hardening never reaches the nested bwrap, and a timeout stops the whole tree."""
    cmd = [config.CODEX_BIN, "exec", *(["resume"] if resume else []), "--json", "--strict-config",
           "--skip-git-repo-check", *([] if resume else ["-C", str(cwd)])]
    if ignore_user_config:
        cmd.append("--ignore-user-config")
    if model:
        cmd += ["-m", model]
    for setting in sandbox_settings if sandbox_settings is not None else codex_sandbox(cwd):
        cmd += ["-c", setting]
    if effort:
        cmd += ["-c", f'model_reasoning_effort="{effort}"']
    cmd += [resume, "-"] if resume else ["-"]
    unit = _codex_unit(f"sync-{uuid.uuid4().hex}")
    started_at = datetime.now(timezone.utc).isoformat()
    proc = subprocess.Popen(_codex_service_command(unit, cmd, codex_env(extra_env)), cwd=str(cwd),
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            env=codex_env(extra_env, retain_user_bus=True), start_new_session=True)
    if on_start:
        on_start(proc.pid)
    metadata = {}
    deadline = time.monotonic() + timeout
    def observe(stdout):
        nonlocal metadata
        thread = _codex_thread(_codex_parse(stdout or ""))
        metadata = _codex_session_model(thread, started_at, _codex_home(codex_env(extra_env)))
        if metadata and on_session:
            on_session({"session_id": thread, **metadata})
    try:
        pending_input = prompt
        while True:
            remaining = deadline - time.monotonic()
            try:
                stdout, stderr = proc.communicate(pending_input, timeout=remaining if metadata else min(0.5, remaining))
                break
            except subprocess.TimeoutExpired as exc:
                if time.monotonic() >= deadline:
                    raise
                pending_input = None
                observe((exc.output or b"").decode("utf-8", errors="replace"))
    except subprocess.TimeoutExpired:
        subprocess.run([SYSTEMCTL_BIN, "--user", "stop", unit], capture_output=True, text=True, timeout=120)
        proc.kill()
        proc.communicate()
        raise
    if not metadata:
        observe(stdout)
    events = _codex_parse(stdout or "")
    messages = [str((event.get("item") or {}).get("text") or "") for event in events
                if event.get("type") == "item.completed" and (event.get("item") or {}).get("type") == "agent_message"]
    tools = [_tool("Bash", (event.get("item") or {}).get("command")) for event in events
             if event.get("type") == "item.completed"
             and (event.get("item") or {}).get("type") == "command_execution"]
    thread = _codex_thread(events)
    return {"text": (messages[-1] if messages else "").strip(), "returncode": proc.returncode,
            "usage": _codex_usage(events), "session_id": thread or resume, "reported_session_id": thread,
            "tools": tools, **metadata,
            "error": None if proc.returncode == 0 else (stderr or "").strip()[:500],
            "raw_stdout": stdout or "", "raw_stderr": stderr or "",
            "raw_stdout_truncated": False, "raw_stderr_truncated": False}


def context_percent(context_tokens: int, engine: str = "claude") -> float:
    return round(100.0 * context_tokens / config.CONTEXT_LINES[engine][2], 1)


def context_state(pct: float | None, engine: str = "claude") -> str:
    """Return ``ok``, ``warn``, or ``act`` against the engine's configured lines."""
    if pct is None:
        return "unknown"
    warn, act, _ = config.CONTEXT_LINES[engine]
    return "act" if pct >= act * 100 else "warn" if pct >= warn * 100 else "ok"
