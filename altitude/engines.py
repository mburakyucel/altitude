"""Headless Claude Code and Codex command builders and runners."""
from __future__ import annotations
import ast
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config, state as S

logger = logging.getLogger(__name__)
_codex_processes: dict[str, subprocess.Popen] = {}

CODEX_SYSTEMD_PREFIX = "altitude-codex-"
SYSTEMD_RUN_BIN = shutil.which("systemd-run") or "systemd-run"
SYSTEMCTL_BIN = shutil.which("systemctl") or "systemctl"
ENV_BIN = shutil.which("env") or "/usr/bin/env"

_CODEX_SECRET_ENV = re.compile(
    r"(?:^|_)(?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|PRIVATE_KEY|ACCESS_KEY|SESSION_KEY|CREDENTIAL)(?:$|_)",
    re.I,
)
_CODEX_CONTROL_ENV = ("DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR")
_CODEX_SENSITIVE_ENV = {
    "GH_TOKEN", "GITHUB_TOKEN", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "SSH_AUTH_SOCK", "SSH_AGENT_PID", "GIT_ASKPASS",
    "GIT_SSH_COMMAND",
}
_CODEX_SAFE_ENV = {
    "HOME", "PATH", "USER", "LOGNAME", "SHELL", "LANG", "LANGUAGE", "TERM", "COLORTERM", "TZ",
    "CODEX_HOME", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "NO_COLOR",
    "ALTITUDE_HOME", "ALTITUDE_PROJECT", "ALTITUDE_TASK", "ALTITUDE_ACTOR", "ALTITUDE_SESSION_KEY",
    "ALTITUDE_ATTEMPT",
}

# Claude's stream-json can be much larger than its final answer. Keep raw capture bounded while preserving evidence
# from both ends.
RAW_CAPTURE_CAP = 2 * 1024 * 1024
CODEX_PATCH_NOTE = (
    "[altitude] Host patch constraint: Do not call the custom `apply_patch` tool, because its filesystem verifier "
    "cannot create its bwrap namespace under this host's AppArmor policy. For every edit, call the shell command "
    "`apply_patch` through the exec tool and pass the patch on stdin; this stays inside the Codex workspace-write "
    "sandbox and its configured writable roots."
)


def codex_isolation_config(cwd: Path, *, writable: bool = True,
                           readable_roots: list[Path] | None = None) -> list[str]:
    """Suppress mutable user/project/plugin sources; managed host policy remains authoritative.

    User config and rules are ignored by command-line flags, and plugins are disabled. Do not synthesize a dynamic
    ``projects.<path>.trust_level`` override: the Codex strict schema rejects that mutable map before it can report a
    thread identity. Hooks are disabled for these headless turns; the Codex permission profile and
    whole-turn containment, not shell-text automation, are the write and process boundaries. Host-managed
    requirements remain authoritative.
    """
    profile = "altitude_worker" if writable else "altitude_reader"
    access = "write" if writable else "read"
    # Start from Codex's maintained workspace baseline so its own executable/runtime remain available, then deny the
    # broad root and reopen only minimal runtime paths plus the effective workspace roots. Coordinators narrow those
    # roots to read; every worker keeps Git/Codex metadata read-only.
    parent = ":workspace"
    codex_binary = Path(shutil.which(config.CODEX_BIN) or config.CODEX_BIN).resolve()
    runtime_dir = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}").resolve()
    extra_reads = "".join(f", {json.dumps(str(Path(root).resolve()))}=\"read\""
                          for root in [codex_binary, *(readable_roots or [])])
    filesystem = ('{ ":root"="deny", ":minimal"="read", '
                  f'":workspace_roots"={{ "."="{access}", ".git"="read", ".codex"="read" }}'
                  f', {json.dumps(str(runtime_dir))}="deny"{extra_reads} }}')
    return ["features.hooks=false", "features.plugins=false",
            "features.remote_plugin=false", "features.apps=false", "features.multi_agent=false",
            "features.goals=false", 'web_search="disabled"', 'approval_policy="never"',
            "shell_environment_policy.ignore_default_excludes=false",
            f"default_permissions=\"{profile}\"", f"permissions.{profile}.extends=\"{parent}\"",
            f"permissions.{profile}.filesystem={filesystem}"]


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
    return (p.stdout or p.stderr).strip()


def clean_env() -> dict:
    """Nested launches need CLAUDE* unset (verified); keep PATH sane for systemd."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    env.setdefault("HOME", str(Path.home()))
    env["PATH"] = str(config.REPO / "bin") + ":" + env.get("PATH", "/usr/bin:/bin") + ":" + str(Path.home() / ".local/bin")
    return env


def codex_env(extra_env: dict | None = None, *, retain_user_bus: bool = False) -> dict:
    """Build the host environment for Codex without passing control capabilities or ambient credentials.

    A contained launch retains the user bus only in the outer ``systemd-run`` client. System services do not
    necessarily inherit the interactive session's bus variables, so the trusted launcher synthesizes their canonical
    per-user values when absent. The command executed inside the transient service gets both variables explicitly
    unset by :func:`_codex_service_command` before Codex starts.
    """
    source = clean_env()
    source.update(extra_env or {})
    env = {key: value for key, value in source.items()
           if key in _CODEX_SAFE_ENV or key.startswith("LC_")}
    if retain_user_bus:
        runtime_dir = source.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        env["XDG_RUNTIME_DIR"] = runtime_dir
        env["DBUS_SESSION_BUS_ADDRESS"] = (source.get("DBUS_SESSION_BUS_ADDRESS")
                                           or f"unix:path={runtime_dir}/bus")
    env["TMPDIR"] = "/tmp"
    for key in list(env):
        if key in _CODEX_SENSITIVE_ENV or _CODEX_SECRET_ENV.search(key):
            env.pop(key, None)
    if not retain_user_bus:
        for key in _CODEX_CONTROL_ENV:
            env.pop(key, None)
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
                 timeout: int = config.L3_TURN_TIMEOUT) -> dict:
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
                        out["tools"].append(c.get("name"))
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


def _run_cli(cmd: list[str], *, cwd: Path, env: dict) -> subprocess.CompletedProcess:
    """Run one CLI command with a bounded wait; a timeout kills it and keeps its output."""
    proc = subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env)
    try:
        stdout, stderr = proc.communicate(timeout=120)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        stdout, stderr = proc.communicate()
        exc.stdout, exc.stderr = stdout, stderr
        raise
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


def claude_bg(name: str, prompt: str, *, cwd: Path, worktree: str | None = None, persona: Path | None = None,
              permission_mode: str = "auto", max_turns: int | None = None, model: str | None = None,
              settings: Path | None = None, extra_env: dict | None = None) -> dict:
    """Start a background session (verified shape). Returns what `claude --bg` printed + the agent row."""
    before = {row.get("id") for row in claude_agents() if row.get("name") == name and row.get("id")}
    cmd = [config.CLAUDE_BIN, "--bg", "--name", name, "--permission-mode", permission_mode]
    if worktree:
        cmd += ["-w", worktree]
    if persona:
        cmd += ["--append-system-prompt-file", str(persona)]
    if max_turns:
        cmd += ["--max-turns", str(max_turns)]
    if model:
        cmd += ["--model", model]
    cmd += ["--settings", str(settings or claude_settings())]
    env = clean_env()
    env.update(extra_env or {})
    p = _run_cli(cmd + [prompt], cwd=cwd, env=env)
    row = _new_claude_agent(name, before)
    return {"stdout": p.stdout.strip(), "stderr": p.stderr.strip(), "returncode": p.returncode, "agent": row}


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


def find_agent(name: str | None = None, agent_id: str | None = None, session_id: str | None = None) -> dict | None:
    for a in claude_agents():
        if agent_id and a.get("id") == agent_id:
            return a
        if session_id and a.get("sessionId") == session_id:
            return a
        if name and a.get("name") == name:
            return a
    return None


def _new_claude_agent(name: str, before: set[str]) -> dict | None:
    """Resolve only the concrete worker created after this launch, never an old same-name job."""
    rows = [row for row in claude_agents()
            if row.get("name") == name and row.get("id") and row.get("id") not in before]
    live = [row for row in rows
            if row.get("state") not in ("failed", "done", "stopped") and row.get("status") != "exited"]
    candidates = live or rows
    return max(candidates, key=lambda row: str(row.get("startedAt") or ""), default=None)


def claude_resume_bg(name: str, session_id: str, prompt: str, *, cwd: Path, persona: Path | None = None,
                     permission_mode: str = "auto", max_turns: int | None = None, model: str | None = None,
                     settings: Path | None = None,
                     extra_env: dict | None = None) -> dict:
    before = {row.get("id") for row in claude_agents() if row.get("name") == name and row.get("id")}
    cmd = [config.CLAUDE_BIN, "--bg", "--name", name, "--resume", session_id, "--permission-mode", permission_mode]
    if persona:
        cmd += ["--append-system-prompt-file", str(persona)]
    if max_turns:
        cmd += ["--max-turns", str(max_turns)]
    if model:
        cmd += ["--model", model]
    cmd += ["--settings", str(settings or claude_settings())]
    env = clean_env()
    env.update(extra_env or {})
    p = _run_cli(cmd + [prompt], cwd=cwd, env=env)
    return {"stdout": p.stdout.strip(), "stderr": p.stderr.strip(), "returncode": p.returncode,
            "agent": _new_claude_agent(name, before)}


def claude_rm(agent_id: str) -> str:
    p = subprocess.run([config.CLAUDE_BIN, "rm", agent_id], capture_output=True, text=True, timeout=60, env=clean_env())
    return (p.stdout + p.stderr).strip()


class CodexSandboxPreflightError(RuntimeError):
    """A workspace-write turn cannot start because its host sandbox cannot safely write every promised root."""

    def __init__(self, roots: list[str], detail: str):
        self.roots = list(roots)
        detail = str(detail).strip() or "unknown bwrap failure"
        self.detail = detail if len(detail) <= 500 else detail[:245] + " ... " + detail[-250:]
        super().__init__(f"Codex sandbox preflight failed for {', '.join(self.roots)}: {self.detail}")


class CodexContainmentError(RuntimeError):
    """A Codex turn cannot start or finish without a proven-empty transient cgroup."""


def _codex_unit(worker_id: str) -> str:
    """A systemd-safe, collision-resistant transient service name."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "-", str(worker_id))
    return f"{CODEX_SYSTEMD_PREFIX}{safe}.service"


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


def _systemd_unit_properties(unit: str) -> dict[str, str]:
    """Read the security-relevant state of one transient user unit, failing closed with no user bus."""
    cmd = [SYSTEMCTL_BIN, "--user", "show", unit, "--property=LoadState", "--property=ActiveState",
           "--property=SubState", "--property=ControlGroup"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                                env=codex_env(retain_user_bus=True))
    except (OSError, subprocess.SubprocessError) as exc:
        raise CodexContainmentError(f"cannot inspect Codex containment unit {unit}: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "systemctl show failed").strip()
        # A collected transient unit is expected to disappear. This response proves the user manager was reached;
        # connection/permission failures remain hard failures.
        if "could not be found" in detail.lower() or "not found" in detail.lower():
            return {"LoadState": "not-found", "ActiveState": "inactive", "SubState": "dead",
                    "ControlGroup": ""}
        raise CodexContainmentError(f"cannot inspect Codex containment unit {unit}: {detail[:500]}")
    props: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            props[key] = value
    required = {"LoadState", "ActiveState", "ControlGroup"}
    if not required.issubset(props):
        raise CodexContainmentError(f"incomplete systemd state for Codex containment unit {unit}")
    return props


def _cgroup_unpopulated(control_group: str) -> bool:
    """Prove a v2 cgroup and all descendants are empty; a removed cgroup is also empty."""
    if not control_group:
        return True
    relative = Path(control_group.lstrip("/"))
    if ".." in relative.parts:
        return False
    events = Path("/sys/fs/cgroup") / relative / "cgroup.events"
    try:
        values = dict(line.split(None, 1) for line in events.read_text().splitlines() if len(line.split(None, 1)) == 2)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return values.get("populated") == "0"


def _codex_unit_empty(unit: str) -> bool:
    props = _systemd_unit_properties(unit)
    if props.get("LoadState") == "not-found":
        return True
    if props.get("ActiveState") not in ("inactive", "failed"):
        return False
    return _cgroup_unpopulated(props.get("ControlGroup") or "")


def _wait_codex_unit_empty(unit: str, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if _codex_unit_empty(unit):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def _stop_codex_unit(unit: str, timeout: float = 5.0) -> None:
    """Stop every process in the transient service, escalating to cgroup-wide SIGKILL if needed."""
    try:
        stopped = subprocess.run([SYSTEMCTL_BIN, "--user", "stop", "--no-block", unit],
                                 capture_output=True, text=True,
                                 timeout=timeout, env=codex_env(retain_user_bus=True))
    except (OSError, subprocess.SubprocessError) as exc:
        raise CodexContainmentError(f"cannot stop Codex containment unit {unit}: {exc}") from exc
    if stopped.returncode != 0 and not _codex_unit_empty(unit):
        detail = (stopped.stderr or stopped.stdout or "systemctl stop failed").strip()
        raise CodexContainmentError(f"cannot stop Codex containment unit {unit}: {detail[:500]}")
    if _wait_codex_unit_empty(unit, timeout):
        return
    if _codex_unit_empty(unit):
        return
    killed = subprocess.run([SYSTEMCTL_BIN, "--user", "kill", "--kill-who=all", "--signal=SIGKILL", unit],
                            capture_output=True, text=True, timeout=timeout,
                            env=codex_env(retain_user_bus=True))
    if killed.returncode != 0 and _codex_unit_empty(unit):
        return
    if killed.returncode != 0 or not _wait_codex_unit_empty(unit, timeout):
        detail = (killed.stderr or killed.stdout or "unit remained populated").strip()
        raise CodexContainmentError(f"Codex containment unit {unit} did not empty: {detail[:500]}")


def codex_containment_empty(worker_id: str, *, job_root: Path) -> bool:
    """True only when a durable background worker's exact systemd service is proven inactive and empty."""
    try:
        record = S.read_json(_codex_paths(job_root, worker_id)["record"], None)
    except (OSError, ValueError):
        logger.exception("Could not read Codex containment record for worker %s", worker_id)
        return False
    if not isinstance(record, dict) or not record.get("unit"):
        return False
    try:
        return _codex_unit_empty(str(record["unit"]))
    except CodexContainmentError:
        logger.exception("Could not prove Codex containment empty for worker %s", worker_id)
        return False


def codex_probe_roots(cwd: Path, extra_config: list[str] | None) -> list[str]:
    """Return every root a workspace-write override promises; malformed overrides are not promises.

    Non-existent roots stay in the list so the in-sandbox write fails closed instead of silently narrowing access.
    """
    base = Path(cwd).resolve()
    roots = [str(base)]
    for override in extra_config or []:
        key, separator, value = override.partition("=")
        if not separator or key.strip() != "sandbox_workspace_write.writable_roots":
            continue
        try:
            parsed = ast.literal_eval(value.strip())
        except (SyntaxError, ValueError):
            logger.warning("Codex sandbox preflight ignored unparseable writable-roots override: %r", override)
            continue
        if isinstance(parsed, (list, tuple)):
            roots.extend(str((base / root).resolve()) for root in parsed if isinstance(root, str))
    return list(dict.fromkeys(roots))


def _codex_network_access(extra_config: list[str] | None) -> bool:
    """Return the effective workspace-write network setting from Codex's TOML-style overrides."""
    network_access = False
    for override in extra_config or []:
        key, separator, value = override.partition("=")
        if not separator or key.strip() != "sandbox_workspace_write.network_access":
            continue
        normalized = value.strip().lower()
        if normalized in {"true", "false"}:
            network_access = normalized == "true"
    return network_access


def codex_sandbox_preflight(cwd: Path, extra_config: list[str] | None = None, timeout: int = 15) -> None:
    """Prove the requested Linux sandbox can create, sync, and remove a sentinel in every writable root.

    `codex sandbox` cannot express these inline roots reliably, so probe the capability Codex depends on directly.
    Other platforms and hosts without bwrap stay available with one warning.
    """
    if not sys.platform.startswith("linux"):
        logger.warning("Codex sandbox preflight skipped: platform is not Linux")
        return
    bwrap = shutil.which("bwrap")
    if not bwrap:
        logger.warning("Codex sandbox preflight skipped: bwrap is not resolvable")
        return
    roots = codex_probe_roots(cwd, extra_config)
    sentinel = f".altitude-codex-write-probe-{uuid.uuid4().hex}"
    script = """set -eu
name=$1
shift
for root do
    probe=$root/$name
    { printf '%s\\n' altitude-codex-write-probe > "$probe" && sync "$probe" && rm -f "$probe"; } || {
        status=$?
        printf 'codex sandbox preflight failed for root: %s\\n' "$root" >&2
        exit "$status"
    }
done
"""
    cmd = [bwrap, "--dev-bind", "/", "/", "--unshare-user"]
    if not _codex_network_access(extra_config):
        cmd.append("--unshare-net")
    cmd += ["--die-with-parent", "/bin/sh", "-c", script, "altitude-codex-write-probe", sentinel, *roots]
    unit = _codex_unit(f"preflight-{uuid.uuid4().hex}")
    launcher_env = codex_env(retain_user_bus=True)
    contained_cmd = _codex_service_command(unit, cmd, codex_env())
    detail = ""
    try:
        probe = subprocess.run(contained_cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                               env=launcher_env)
        try:
            empty = _wait_codex_unit_empty(unit)
        except CodexContainmentError as exc:
            empty = False
            detail = str(exc)
        if probe.returncode == 0 and empty:
            return
        if not empty:
            try:
                _stop_codex_unit(unit)
            except CodexContainmentError as exc:
                detail = str(exc)
            detail = detail or f"Codex sandbox preflight service {unit} remained populated"
        else:
            detail = probe.stderr or f"bwrap exited {probe.returncode} without stderr"
    except subprocess.TimeoutExpired as exc:
        try:
            _stop_codex_unit(unit)
        except CodexContainmentError as stop_exc:
            detail = str(stop_exc)
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr
        detail = detail or stderr or f"bwrap timed out after {timeout} seconds"
    except OSError as exc:
        detail = f"{type(exc).__name__}: {exc}"
    cleanup_errors = []
    for root in roots:
        try:
            (Path(root) / sentinel).unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            cleanup_errors.append(f"{root}: {type(exc).__name__}: {exc}")
    if cleanup_errors:
        detail = f"{detail.rstrip()}; cleanup failed: {'; '.join(cleanup_errors)}"
    raise CodexSandboxPreflightError(roots, detail)


def _pid_start(pid: int) -> str | None:
    """Linux process start tick, used to avoid signaling a recycled pid."""
    try:
        fields = Path(f"/proc/{int(pid)}/stat").read_text().split()
        return None if fields[2] == "Z" else fields[21]
    except (OSError, ValueError, IndexError):
        return None


def _codex_paths(job_root: Path, worker_id: str) -> dict[str, Path]:
    root = Path(job_root)
    return {
        "record": root / f"{worker_id}.json",
        "stdout": root / f"{worker_id}.stdout.jsonl",
        "stderr": root / f"{worker_id}.stderr.log",
        "answer": root / f"{worker_id}.answer.md",
    }


def _terminate_spawned_codex(proc: subprocess.Popen, unit: str) -> None:
    """Fail-closed cgroup cleanup before a Codex worker has a durable owner."""
    containment_error = None
    try:
        _stop_codex_unit(unit)
    except CodexContainmentError as exc:
        containment_error = exc
    # The systemd-run wrapper is not the security boundary, but reaping it avoids a local zombie after the unit is
    # empty and is useful when systemd-run itself failed before creating the service.
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
    else:
        proc.wait(timeout=1)
    if containment_error is not None:
        raise containment_error


def _codex_events(path: Path) -> list[dict]:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return []
    events = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


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


def _codex_action(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def codex_worker(worker_id: str | None, *, job_root: Path) -> dict | None:
    """Return one Codex process as the same normalized row shape used for Claude workers."""
    if not worker_id:
        return None
    paths = _codex_paths(job_root, worker_id)
    record = S.read_json(paths["record"], None)
    if not isinstance(record, dict):
        return None
    events = _codex_events(paths["stdout"])
    session_id = _codex_thread(events) or record.get("session_id")
    pid = record.get("pid")
    unit = str(record.get("unit") or "")
    containment_error = ""
    try:
        empty = bool(unit) and _codex_unit_empty(unit)
    except CodexContainmentError as exc:
        # Unknown containment state must never look completed: the trusted broker independently checks the same
        # durable unit before any action, and dispatch keeps this row in-flight until state is provable.
        empty = False
        containment_error = str(exc)
    alive = not empty
    completed = next((event for event in reversed(events) if event.get("type") == "turn.completed"), None)
    failed = next((event for event in reversed(events)
                   if event.get("type") in ("turn.failed", "error")), None)
    if record.get("stopped"):
        state, status = "stopped", "exited"
    elif alive:
        state, status = "working", "busy"
    elif completed:
        state, status = "done", "exited"
    else:
        state, status = "failed", "exited"
    if empty and worker_id in _codex_processes:
        proc = _codex_processes.pop(worker_id)
        try:
            proc.wait(timeout=0)
        except (subprocess.TimeoutExpired, OSError):
            pass
    detail = ""
    if containment_error:
        detail = containment_error[:500]
    elif failed:
        detail = str(failed.get("message") or failed.get("error") or failed)[:500]
    elif not alive and not completed:
        try:
            detail = paths["stderr"].read_text(errors="replace")[-500:]
        except OSError:
            detail = "Codex worker exited without turn.completed"
    return {
        "id": worker_id, "sessionId": session_id, "name": record.get("name"), "pid": pid, "unit": unit,
        "state": state, "status": status, "detail": detail, "usage": _codex_usage(events),
        "startedAt": record.get("started_at"), "engine": "codex", "action": _codex_action(paths["answer"]),
    }


def codex_bg(name: str, prompt: str, *, cwd: Path, job_root: Path, resume: str | None = None,
             persona: Path | None = None, model: str | None = None, extra_env: dict | None = None,
             start_timeout: float = 15.0) -> dict:
    """Start one detached Codex L2 turn and wait boundedly for its stable thread identity."""
    extra_config = [*codex_isolation_config(cwd)]
    if not resume:
        codex_sandbox_preflight(Path(cwd), extra_config)

    worker_id = uuid.uuid4().hex
    unit = _codex_unit(worker_id)
    root = Path(job_root); root.mkdir(parents=True, exist_ok=True)
    paths = _codex_paths(root, worker_id)
    schema = config.SCHEMAS / "l2_action.json"
    if resume:
        cmd = [config.CODEX_BIN, "exec", "resume", "--json", "--strict-config", "-o", str(paths["answer"]),
               "--output-schema", str(schema), "--skip-git-repo-check", "--ignore-user-config",
               "--ignore-rules"]
    else:
        cmd = [config.CODEX_BIN, "exec", "--json", "--strict-config", "-o", str(paths["answer"]),
               "--output-schema", str(schema), "-C", str(cwd),
               "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules"]
    if model:
        cmd += ["-m", model]
    for setting in extra_config:
        cmd += ["-c", setting]
    if resume:
        cmd += [resume, "-"]
        launch_prompt = prompt
    else:
        launch_prompt = (((Path(persona).read_text() + "\n\n") if persona else "") + CODEX_PATCH_NOTE
                         + "\n\n" + prompt)
        cmd += ["-"]
    env = codex_env(extra_env, retain_user_bus=True)
    child_env = codex_env(extra_env)
    # Persist the unguessable unit before crossing the spawn boundary. If altd dies between systemd-run and the PID
    # update, the record still has the exact cgroup identity needed to stop every descendant.
    record = {"id": worker_id, "name": name, "pid": None, "pid_start": None, "unit": unit,
              "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "session_id": resume, "cwd": str(cwd), "resume": bool(resume), "stopped": None}
    S.write_json(paths["record"], record)
    out = open(paths["stdout"], "ab", buffering=0)
    err = open(paths["stderr"], "ab", buffering=0)
    try:
        proc = subprocess.Popen(_codex_service_command(unit, cmd, child_env), cwd=str(cwd),
                                stdin=subprocess.PIPE,
                                stdout=out, stderr=err, env=env, start_new_session=True)
        try:
            proc.stdin.write(launch_prompt.encode("utf-8")); proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
    finally:
        out.close(); err.close()
    pid_start = _pid_start(proc.pid)
    if not pid_start:
        _terminate_spawned_codex(proc, unit)
        raise RuntimeError("Codex worker started without a stable process identity")
    try:
        record.update({"pid": proc.pid, "pid_start": pid_start})
        S.write_json(paths["record"], record)
        _codex_processes[worker_id] = proc
        deadline = time.monotonic() + start_timeout
        thread_id = None
        while time.monotonic() < deadline:
            events = _codex_events(paths["stdout"])
            thread_id = _codex_thread(events)
            if thread_id:
                break
            if proc.poll() is not None:
                break
            time.sleep(0.05)
        if not thread_id or (resume and thread_id != resume):
            try:
                codex_stop(worker_id, job_root=root)
            except Exception:  # noqa: BLE001 — report the identity failure, not a secondary stop failure
                pass
            row = codex_worker(worker_id, job_root=root) or {}
            detail = row.get("detail") or ("resumed a different Codex thread" if thread_id else "no thread.started event")
            return {"stdout": "", "stderr": str(detail), "returncode": 1, "agent": row}
        record["session_id"] = thread_id
        S.write_json(paths["record"], record)
        return {"stdout": "", "stderr": "", "returncode": 0,
                "agent": codex_worker(worker_id, job_root=root)}
    except BaseException:
        _codex_processes.pop(worker_id, None)
        _terminate_spawned_codex(proc, unit)
        raise


def codex_stop(worker_id: str, *, job_root: Path) -> str:
    paths = _codex_paths(job_root, worker_id)
    record = S.read_json(paths["record"], None)
    if not isinstance(record, dict):
        return "Codex worker record already absent"
    unit = str(record.get("unit") or "")
    if not unit:
        raise CodexContainmentError(f"Codex worker {worker_id} has no durable containment unit")
    _stop_codex_unit(unit)
    if not _codex_unit_empty(unit):
        raise CodexContainmentError(f"Codex worker {worker_id} containment unit is still populated")
    proc = _codex_processes.pop(worker_id, None)
    if proc is not None:
        try:
            proc.wait(timeout=1)
        except (subprocess.TimeoutExpired, OSError):
            pass
    record["stopped"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    S.write_json(paths["record"], record)
    return "Codex worker stopped"


def start_l2(engine: str, name: str, prompt: str, *, cwd: Path, persona: Path,
             model: str | None, settings: Path, extra_env: dict, job_root: Path) -> dict:
    if engine == "claude":
        return claude_bg(name, prompt, cwd=cwd, persona=persona, permission_mode="auto", model=model,
                         settings=settings, extra_env=extra_env)
    if engine == "codex":
        return codex_bg(name, prompt, cwd=cwd, persona=persona, model=model, extra_env=extra_env,
                        job_root=job_root)
    raise ValueError(f"unknown L2 engine {engine!r}")


def resume_l2(engine: str, name: str, session_id: str, prompt: str, *, cwd: Path, persona: Path,
              model: str | None, settings: Path, extra_env: dict, job_root: Path) -> dict:
    if engine == "claude":
        return claude_resume_bg(name, session_id, prompt, cwd=cwd, persona=persona, settings=settings,
                                model=model, extra_env=extra_env)
    if engine == "codex":
        return codex_bg(name, prompt, cwd=cwd, resume=session_id, model=model, extra_env=extra_env,
                        job_root=job_root)
    raise ValueError(f"unknown L2 engine {engine!r}")


def stop_l2_worker(engine: str, worker_id: str, *, job_root: Path) -> str:
    return claude_stop(worker_id) if engine == "claude" else codex_stop(worker_id, job_root=job_root)


def remove_l2_worker(engine: str, worker_id: str, *, job_root: Path) -> str:
    if engine == "claude":
        return claude_rm(worker_id)
    return codex_stop(worker_id, job_root=job_root)


def codex_exec(prompt: str, *, cwd: Path, schema: Path | None = None, sandbox: str = "read-only",
               model: str | None = None, timeout: int = 900, extra_config: list[str] | None = None,
               effort: str | None = None, extra_env: dict | None = None,
               fault_context: dict[str, str] | None = None, resume: str | None = None,
               on_start=None, contain: bool | None = None,
               readable_roots: list[Path] | None = None) -> dict:
    """Codex headless (L3 turns) — verified: needs stdin closed, -o for
    the answer. `extra_config` are `-c key=value` overrides (sandbox network, writable roots). Token usage comes from
    the `turn.completed` events on stdout. Workspace-write turns are contained by default; ``contain=True`` also
    places a read-only coordinator turn in a transient cgroup before it may return trusted actions."""
    try:
        effective_config = [*(extra_config or []), *codex_isolation_config(
            cwd, writable=sandbox == "workspace-write", readable_roots=readable_roots)]
    except RuntimeError as exc:
        return {"text": "", "structured": None, "returncode": 1, "engine_started": False,
                "fault_recorded": None, "usage": {}, "error": str(exc),
                "raw_stdout": "", "raw_stderr": "", "raw_stdout_truncated": False,
                "raw_stderr_truncated": False}
    if sandbox == "workspace-write":
        try:
            codex_sandbox_preflight(cwd, effective_config)
        except CodexSandboxPreflightError as exc:
            from . import incidents  # local import avoids the incident/engine module cycle
            fault_recorded = None
            try:
                incidents.system_fault("codex-sandbox", f"roots={exc.roots!r}; {exc.detail}",
                                     **(fault_context or {}))
                fault_recorded = "codex-sandbox"
            except Exception:  # noqa: BLE001 — fault persistence must not replace the deterministic gate failure
                logger.exception("Failed to record Codex sandbox preflight system fault")
            error = str(exc)
            if len(error) > 500:
                error = error[:245] + " ... " + error[-250:]
            # Callers already persist and stamp ordinary failures; returning that contract avoids duplicate faults
            # and stranded L3 turns while still guaranteeing Codex was never invoked.
            return {"text": "", "structured": None, "returncode": 1, "engine_started": False,
                    "fault_recorded": fault_recorded,
                    "usage": {}, "error": error,
                    "raw_stdout": "", "raw_stderr": "", "raw_stdout_truncated": False,
                    "raw_stderr_truncated": False}
    with tempfile.NamedTemporaryFile("r", suffix=".out", delete=False) as outf:
        out_path = outf.name
    if resume:
        cmd = [config.CODEX_BIN, "exec", "resume", "--json", "--strict-config", "-o", out_path, "--skip-git-repo-check",
               "--ignore-user-config", "--ignore-rules"]
    else:
        cmd = [config.CODEX_BIN, "exec", "--json", "--strict-config", "-o", out_path, "-C", str(cwd),
               "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules"]
    if schema:
        cmd += ["--output-schema", str(schema)]
    if model:
        cmd += ["-m", model]
    for kv in effective_config:
        cmd += ["-c", kv]
    if effort:
        cmd += ["-c", f'model_reasoning_effort="{effort}"']
    contained = sandbox == "workspace-write" if contain is None else contain
    unit = _codex_unit(f"sync-{uuid.uuid4().hex}") if contained else None
    containment_error = None
    try:
        env = codex_env(extra_env, retain_user_bus=bool(unit))
        child_env = codex_env(extra_env)
        argv = cmd + ([resume, prompt] if resume else [prompt])
        contained_argv = _codex_service_command(unit, argv, child_env) if unit else argv
        if on_start:
            proc = subprocess.Popen(contained_argv, cwd=str(cwd), stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL, env=env,
                                    start_new_session=True)
            on_start(proc.pid)
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    if unit:
                        _stop_codex_unit(unit)
                finally:
                    proc.kill(); stdout, stderr = proc.communicate()
                raise
            p = subprocess.CompletedProcess(contained_argv, proc.returncode, stdout, stderr)
        else:
            try:
                p = subprocess.run(contained_argv, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                                   stdin=subprocess.DEVNULL, env=env)
            except subprocess.TimeoutExpired:
                if unit:
                    _stop_codex_unit(unit)
                raise
        if unit:
            try:
                if not _wait_codex_unit_empty(unit):
                    _stop_codex_unit(unit)
                if not _codex_unit_empty(unit):
                    raise CodexContainmentError(f"Codex containment unit {unit} remained populated after the turn")
            except CodexContainmentError as exc:
                containment_error = str(exc)
        text = Path(out_path).read_text() if Path(out_path).exists() else ""
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass
    structured = None
    try:
        structured = json.loads(text)
    except ValueError:
        pass
    usage, messages, session_id, reported_session_id = {}, [], resume, None
    for line in (p.stdout or "").splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "thread.started" and ev.get("thread_id"):
            session_id = reported_session_id = str(ev["thread_id"])
        elif ev.get("type") == "turn.completed" and isinstance(ev.get("usage"), dict):
            usage = dict(ev["usage"])
        elif ev.get("type") == "item.completed" and (ev.get("item") or {}).get("type") == "agent_message":
            messages.append(str((ev["item"] or {}).get("text") or ""))
    if not text.strip() and messages:  # no -o file (or empty): the last agent message is the answer
        text = messages[-1]
    if containment_error:
        # Never expose an actionable answer while a descendant could still be running. Raw streams remain local
        # diagnostic evidence, while L3 sees a deterministic engine failure.
        text, structured = "", None
    return {"text": text.strip(), "structured": structured,
            "returncode": 1 if containment_error else p.returncode, "usage": usage,
            "session_id": session_id, "reported_session_id": reported_session_id,
            "unit": unit, "containment_empty": (not containment_error) if unit else None,
            "error": containment_error or (None if p.returncode == 0 else p.stderr.strip()[:500]),
            "raw_stdout": p.stdout or "", "raw_stderr": p.stderr or "",
            "raw_stdout_truncated": False, "raw_stderr_truncated": False}


def context_percent(context_tokens: int, engine: str = "claude") -> float:
    return round(100.0 * context_tokens / config.CONTEXT_LINES[engine][2], 1)


def context_state(pct: float | None, engine: str = "claude") -> str:
    """Return ``ok``, ``warn``, or ``act`` against the engine's configured lines."""
    if pct is None:
        return "unknown"
    warn, act, _ = config.CONTEXT_LINES[engine]
    return "act" if pct >= act * 100 else "warn" if pct >= warn * 100 else "ok"
