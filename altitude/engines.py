"""Subscription CLIs driven headlessly (decision 1). Claude Code and Codex command builders + runners."""
from __future__ import annotations
import ast
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import uuid
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_POPEN_TYPE = subprocess.Popen

from . import config

logger = logging.getLogger(__name__)

# Claude's stream-json can be much larger than its final answer. Keep raw capture bounded while preserving evidence
# from both ends; L1 applies the same default cap to the artifacts it exposes.
RAW_CAPTURE_CAP = 2 * 1024 * 1024
CODEX_PATCH_NOTE = (
    "[altitude] Host patch constraint: the custom and shell `apply_patch` verifier can be scoped to the service "
    "checkout and may not see this task worktree. If that happens, apply a unified patch with the shell command "
    "`git apply` from this worktree. This edits files only; it does not bypass Git provenance hooks, commit, push, "
    "or change refs."
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

# ---- usage limit (decision 44): the subscription window closing is a hold with a reset time, not a failure ----------
LIMIT_TEXT = re.compile(r"hit your (?:session|usage) limit|usage limit reached|out of (?:extra )?usage|rate limit reached", re.I)
RESETS = re.compile(r"resets?\s+(?:(?:at|in)\s+)?(?:([A-Za-z]{3,9}\s+\d{1,2}),?\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)(?:\s*\(([^)]+)\))?", re.I)


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
    """The reset time while the window is exhausted, else None. Dispatch, proposals and L3 turns check this first."""
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
    note = (p.stdout or p.stderr).strip()
    if p.returncode != 0:
        raise RuntimeError(f"claude stop {agent_id} failed: {note[:300] or f'exit {p.returncode}'}")
    return note


def clean_env() -> dict:
    """Nested launches need CLAUDE* unset (verified); keep PATH sane for systemd."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}
    env.setdefault("HOME", str(Path.home()))
    env["PATH"] = str(config.REPO / "bin") + ":" + env.get("PATH", "/usr/bin:/bin") + ":" + str(Path.home() / ".local/bin")  # I-021: `alt` in every session
    return env


def claude_settings() -> Path:
    """The settings every Claude launch without a per-dispatch file gets (decision 49): auto-compact at the 300k umbrella,
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
                 timeout: int = config.L3_TURN_TIMEOUT, start_new_session: bool = False) -> dict:
    """One headless turn. Returns text, session_id, usage, cost, turns, structured (if schema), error, and bounded
    raw_stdout/raw_stderr; `limited` (a reset time) when the subscription window is exhausted — the call is not even
    made while a hold is in force.

    `on_start(pid)` is called the moment the child exists. The turn outlives altd (systemd KillMode=process,
    ce856bb), so its pid is the only evidence a *restarted* altd has that the turn is still running (I-011)."""
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
    cmd += ["--settings", str(settings or claude_settings())]  # decision 49: the 300k umbrella rides on every launch
    if resume:
        cmd += ["--resume", resume]
    env = clean_env()
    env.update(extra_env or {})
    # prompt goes through stdin: --allowedTools is variadic and would swallow a positional prompt
    proc = subprocess.Popen(cmd, cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env, start_new_session=start_new_session)
    private_group = bool(start_new_session and isinstance(proc, _POPEN_TYPE))
    leader_start = None
    if private_group:
        identity = _process_group_identity(proc.pid)
        if not identity or identity[1] != proc.pid:
            proc.kill()
            proc.wait()
            raise RuntimeError(f"Claude pid {proc.pid} did not establish its private process group")
        leader_start = identity[0]
    if on_start:
        try:
            on_start(proc.pid)
        except BaseException:
            terminated = _terminate_spawned_process(proc, private_group=private_group,
                                                    leader_start=leader_start)
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                stream.close()
            if not terminated:
                raise RuntimeError(f"could not reap Claude process group {proc.pid} after on_start failed")
            raise
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
    timed_out = [False]
    termination_failed = [False]
    termination_lock = threading.Lock()

    def terminate() -> bool:
        with termination_lock:
            return _terminate_spawned_process(proc, private_group=private_group,
                                              leader_start=leader_start)

    def kill_for_timeout() -> None:
        timed_out[0] = True
        termination_failed[0] = not terminate()

    killer = threading.Timer(timeout, kill_for_timeout)
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
        if not _finish_spawned_process(proc, private_group=private_group, leader_start=leader_start):
            termination_failed[0] = True
    except BaseException as exc:
        failure = exc
        killer.cancel()
        if not terminate():
            failure = RuntimeError(f"could not reap Claude process group {proc.pid} after stream failure")
            failure.__cause__ = exc
            raise failure
        raise
    finally:
        killer.cancel()
        if killer.is_alive():
            killer.join()
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
    if termination_failed[0]:
        raise RuntimeError(f"could not reap timed-out Claude process group {proc.pid}")
    lim = usage_limit_in(out.get("synthetic") or out["text"] or raw_stderr, out.get("quota"))
    if lim:
        note_usage_limit(lim, (out.get("synthetic") or out["text"])[:200])
        out["limited"] = lim
        out["error"] = f"usage limit: window exhausted until {lim}"
    if timed_out[0]:
        out["error"] = f"Claude turn timed out after {timeout}s"
    elif proc.returncode != 0 and not out["error"]:
        out["error"] = f"claude exit {proc.returncode}: {raw_stderr.strip()[:500]}"
    if schema and out["structured"] is None and out["text"]:
        try:
            out["structured"] = json.loads(out["text"])
        except ValueError:
            pass
    return out


def claude_bg(name: str, prompt: str, *, cwd: Path, worktree: str | None = None, persona: Path | None = None,
              permission_mode: str = "auto", max_turns: int | None = None, model: str | None = None,
              settings: Path | None = None, extra_env: dict | None = None) -> dict:
    """Start a background session (verified shape). Returns what `claude --bg` printed + the agent row."""
    cmd = [config.CLAUDE_BIN, "--bg", "--name", name, "--permission-mode", permission_mode]
    if worktree:
        cmd += ["-w", worktree]
    if persona:
        cmd += ["--append-system-prompt-file", str(persona)]
    if max_turns:
        cmd += ["--max-turns", str(max_turns)]
    if model:
        cmd += ["--model", model]
    cmd += ["--settings", str(settings or claude_settings())]  # decision 49: the 300k umbrella rides on every launch
    env = clean_env()
    env.update(extra_env or {})
    p = subprocess.run(cmd + [prompt], cwd=str(cwd), capture_output=True, text=True, timeout=120, env=env)
    row = find_agent(name)
    return {"stdout": p.stdout.strip(), "stderr": p.stderr.strip(), "returncode": p.returncode, "agent": row}


def claude_agents() -> list[dict]:
    try:
        p = subprocess.run([config.CLAUDE_BIN, "agents", "--json", "--all"], capture_output=True, text=True,
                           timeout=60, env=clean_env())
        data = json.loads(p.stdout or "[]")
    except (subprocess.SubprocessError, ValueError, OSError) as e:
        # decision 36: an empty list would read as "every L2 vanished" and block every running task — fail loudly instead
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


def claude_resume_bg(name: str, session_id: str, prompt: str, *, cwd: Path, persona: Path | None = None,
                     permission_mode: str = "auto", max_turns: int | None = None, settings: Path | None = None,
                     extra_env: dict | None = None) -> dict:
    cmd = [config.CLAUDE_BIN, "--bg", "--name", name, "--resume", session_id, "--permission-mode", permission_mode]
    if persona:
        cmd += ["--append-system-prompt-file", str(persona)]
    if max_turns:
        cmd += ["--max-turns", str(max_turns)]
    cmd += ["--settings", str(settings or claude_settings())]  # decision 49: the 300k umbrella rides on every launch
    env = clean_env()
    env.update(extra_env or {})
    p = subprocess.run(cmd + [prompt], cwd=str(cwd), capture_output=True, text=True, timeout=120, env=env)
    return {"stdout": p.stdout.strip(), "stderr": p.stderr.strip(), "returncode": p.returncode, "agent": find_agent(name)}


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
    Other platforms and hosts without bwrap are outside I-030's failure mode and stay available with one warning.
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
    detail = ""
    try:
        probe = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
        if probe.returncode == 0:
            return
        detail = probe.stderr or f"bwrap exited {probe.returncode} without stderr"
    except subprocess.TimeoutExpired as exc:
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr
        detail = stderr or f"bwrap timed out after {timeout} seconds"
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


def _process_group_identity(pid: int) -> tuple[str, int] | None:
    """Return (start_time, process_group) for a live non-zombie process."""
    try:
        fields = Path(f"/proc/{int(pid)}/stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            return None
        return fields[19], int(fields[2])
    except (OSError, ValueError, IndexError):
        return None


def process_group_members(pgid: int, *, exclude_pid: int | None = None) -> dict[int, str]:
    """Snapshot exact live identities in one owned process group."""
    members: dict[int, str] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == exclude_pid:
            continue
        identity = _process_group_identity(pid)
        if identity and identity[1] == int(pgid):
            members[pid] = identity[0]
    return members


def reap_process_group_members(pgid: int, *, exclude_pid: int | None = None, grace: float = 1.0) -> bool:
    """Reap an owned wrapper group's descendants without signaling the wrapper itself."""
    members = process_group_members(pgid, exclude_pid=exclude_pid)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid, started in members.items():
            if _process_group_identity(pid) == (started, int(pgid)):
                try:
                    os.kill(pid, sig)
                except ProcessLookupError:
                    pass
                except OSError:
                    return False
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            live = {pid: start for pid, start in members.items()
                    if _process_group_identity(pid) == (start, int(pgid))}
            if not live:
                return not process_group_members(pgid, exclude_pid=exclude_pid)
            time.sleep(0.02)
        members = live
    return not process_group_members(pgid, exclude_pid=exclude_pid)


def _terminate_spawned_process(proc, *, private_group: bool, leader_start: str | None,
                               grace: float = 1.0) -> bool:
    """Stop one child and prove its private group has no executable survivors.

    A ``Popen`` leader remains waitable (and therefore its PID cannot be reused) until this
    helper reaps it. That makes the captured start identity plus ``pgid == pid`` sufficient
    ownership proof even when the leader exits before a SIGTERM-ignoring tool descendant.
    """
    if not private_group:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        except OSError:
            return False
        try:
            proc.wait()
        except (OSError, subprocess.SubprocessError):
            return False
        return True

    pgid = int(proc.pid)
    identity = _process_group_identity(pgid)
    if identity is not None and identity != (str(leader_start), pgid):
        return False
    if leader_start is None:
        return False

    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            pass
        except OSError:
            return False
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if not process_group_members(pgid):
                try:
                    proc.wait()
                except (OSError, subprocess.SubprocessError):
                    return False
                return not process_group_members(pgid)
            time.sleep(0.02)

    # Resnapshot and target exact member identities once more. This closes the narrow fork
    # race between the group-wide signals and the emptiness check, while remaining PID-safe.
    empty = reap_process_group_members(pgid, grace=grace)
    try:
        proc.wait()
    except (OSError, subprocess.SubprocessError):
        return False
    return empty and not process_group_members(pgid)


def _finish_spawned_process(proc, *, private_group: bool, leader_start: str | None,
                            exit_grace: float = 0.25) -> bool:
    """Let a completed CLI leader exit naturally, then prove its whole group empty."""
    if not private_group:
        try:
            proc.wait()
            return True
        except (OSError, subprocess.SubprocessError):
            return False
    expected = (str(leader_start), int(proc.pid))
    deadline = time.monotonic() + exit_grace
    while time.monotonic() < deadline and _process_group_identity(proc.pid) == expected:
        time.sleep(0.01)
    return _terminate_spawned_process(proc, private_group=True, leader_start=leader_start)


def codex_exec(prompt: str, *, cwd: Path, schema: Path | None = None, sandbox: str = "read-only",
               model: str | None = None, timeout: int = 900, extra_config: list[str] | None = None,
               effort: str | None = None, extra_env: dict | None = None, resume: str | None = None,
               on_start=None, on_text=None, on_session=None, bypass_hook_trust: bool = False,
               unset_env: tuple[str, ...] = (), start_new_session: bool = True,
               fault_context: dict[str, str] | None = None) -> dict:
    """Run one preflighted Codex exec turn, fresh or resumed, and parse its JSONL lifecycle events.

    The process is streamed so callers can durably record its PID and thread id before an altd restart. Raw stdout and
    stderr remain bounded evidence artifacts, independently of the lifecycle values parsed from the stream. Resume has
    its own CLI surface: sandbox mode is a config override and ``cwd`` remains the process working directory.
    Workspace-write turns prove every promised writable root before the engine process is launched.
    """
    if sandbox == "workspace-write":
        try:
            codex_sandbox_preflight(cwd, extra_config)
        except CodexSandboxPreflightError as exc:
            from . import improve  # local: improve -> dispatch -> engines during module import
            fault_recorded = None
            try:
                improve.system_fault("codex-sandbox", f"roots={exc.roots!r}; {exc.detail}",
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
                    "fault_recorded": fault_recorded, "usage": {}, "session_id": resume, "error": error,
                    "raw_stdout": "", "raw_stderr": "", "raw_stdout_truncated": False,
                    "raw_stderr_truncated": False}
    with tempfile.NamedTemporaryFile("r", suffix=".out", delete=False) as outf:
        out_path = outf.name
    if resume:
        cmd = [config.CODEX_BIN, "exec", "resume", "--json", "-o", out_path, "--skip-git-repo-check"]
        configs = [f'sandbox_mode="{sandbox}"', *(extra_config or [])]
    else:
        cmd = [config.CODEX_BIN, "exec", "--json", "-o", out_path, "-s", sandbox, "-C", str(cwd),
               "--skip-git-repo-check"]
        configs = list(extra_config or [])
    if schema:
        cmd += ["--output-schema", str(schema)]
    if bypass_hook_trust:
        cmd += ["--dangerously-bypass-hook-trust"]
    if model:
        cmd += ["-m", model]
    for kv in configs:
        cmd += ["-c", kv]
    if effort:
        cmd += ["-c", f'model_reasoning_effort="{effort}"']
    if resume:
        cmd += [resume]
    cmd += [prompt]

    env = clean_env()
    for key in unset_env:
        env.pop(key, None)
    env.update(extra_env or {})
    stdout_capture, stderr_capture = _BoundedRawCapture(), _BoundedRawCapture()
    usage: dict = {}
    messages: list[str] = []
    session_id = resume
    timed_out = [False]
    proc = None
    try:
        try:
            proc = subprocess.Popen(cmd, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, env=env, start_new_session=start_new_session)
        except OSError as exc:
            return {"text": "", "structured": None, "returncode": 127, "usage": {}, "session_id": session_id,
                    "error": f"codex exec could not start: {exc}"[:500], "raw_stdout": "", "raw_stderr": "",
                    "raw_stdout_truncated": False, "raw_stderr_truncated": False}

        private_group = bool(start_new_session and isinstance(proc, _POPEN_TYPE))
        leader_start = None
        if private_group:
            identity = _process_group_identity(proc.pid)
            if not identity or identity[1] != proc.pid:
                proc.kill()
                proc.wait()
                raise RuntimeError(f"Codex pid {proc.pid} did not establish its private process group")
            leader_start = identity[0]
        termination_failed = [False]
        termination_lock = threading.Lock()

        def terminate() -> bool:
            with termination_lock:
                return _terminate_spawned_process(proc, private_group=private_group,
                                                  leader_start=leader_start)

        if on_start:
            try:
                on_start(proc.pid)
            except BaseException:
                terminated = terminate()
                for stream in (proc.stdout, proc.stderr):
                    stream.close()
                if not terminated:
                    raise RuntimeError(f"could not reap Codex process group {proc.pid} after on_start failed")
                raise

        def drain_stderr() -> None:
            while chunk := proc.stderr.read(65536):
                stderr_capture.add(chunk)

        def kill_for_timeout() -> None:
            timed_out[0] = True
            termination_failed[0] = not terminate()

        drain = threading.Thread(target=drain_stderr, daemon=True)
        drain.start()
        killer = threading.Timer(timeout, kill_for_timeout)
        killer.start()
        failure = None
        try:
            for line in proc.stdout:
                stdout_capture.add(line)
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("type") == "thread.started":
                    session_id = (event.get("thread_id") or event.get("threadId")
                                  or event.get("session_id") or session_id)
                    if session_id and on_session:
                        on_session(str(session_id))
                elif event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
                    for key, value in event["usage"].items():
                        usage[key] = usage.get(key, 0) + (value or 0)
                elif (event.get("type") == "item.completed"
                      and (event.get("item") or {}).get("type") == "agent_message"):
                    message = str((event.get("item") or {}).get("text") or "")
                    messages.append(message)
                    if message and on_text:
                        on_text(message)
            if not _finish_spawned_process(proc, private_group=private_group,
                                           leader_start=leader_start):
                termination_failed[0] = True
        except BaseException as exc:
            failure = exc
            killer.cancel()
            if not terminate():
                failure = RuntimeError(f"could not reap Codex process group {proc.pid} after stream failure")
                failure.__cause__ = exc
                raise failure
            raise
        finally:
            killer.cancel()
            if killer.is_alive():
                killer.join()
            proc.stdout.close()
            drain.join(timeout=2)
            proc.stderr.close()
            if failure is not None:
                failure.raw_stdout, failure.raw_stdout_truncated = stdout_capture.render()
                failure.raw_stderr, failure.raw_stderr_truncated = stderr_capture.render()

        if termination_failed[0]:
            raise RuntimeError(f"could not reap Codex process group {proc.pid}")
        text = Path(out_path).read_text() if Path(out_path).exists() else ""
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass

    raw_stdout, raw_stdout_truncated = stdout_capture.render()
    raw_stderr, raw_stderr_truncated = stderr_capture.render()
    structured = None
    try:
        structured = json.loads(text)
    except ValueError:
        pass
    if not text.strip() and messages:  # no -o file (or empty): the last agent message is the answer
        text = messages[-1]
    if timed_out[0]:
        error = f"codex exec timed out after {timeout}s"
    elif proc.returncode != 0:
        error = raw_stderr.strip()[:500] or f"codex exit {proc.returncode}"
    else:
        error = None
    # Usage is cumulative accounting for this exec/resume process, not live context-window occupancy.
    return {"text": text.strip(), "structured": structured, "returncode": proc.returncode, "usage": usage,
            "session_id": session_id, "error": error, "raw_stdout": raw_stdout, "raw_stderr": raw_stderr,
            "raw_stdout_truncated": raw_stdout_truncated, "raw_stderr_truncated": raw_stderr_truncated}


def context_percent(context_tokens: int, engine: str = "claude") -> float:
    try:
        value = 100.0 * max(0, int(context_tokens)) / config.CONTEXT_LINES[engine][2]
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return 0.0
    return min(100.0, round(value, 1))


def context_state(pct: float | None, engine: str = "claude") -> str:
    """ok | warn | act against the engine's lines (decision 12)."""
    if pct is None:
        return "unknown"
    warn, act, _ = config.CONTEXT_LINES[engine]
    return "act" if pct >= act * 100 else "warn" if pct >= warn * 100 else "ok"
