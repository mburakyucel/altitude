"""Subscription CLIs driven headlessly (decision 1). Claude Code and Codex command builders + runners."""
from __future__ import annotations
import ctypes
import hashlib
import json
import logging
import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import tomllib
import uuid
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_POPEN_TYPE = subprocess.Popen

from . import config
from .alt_broker import FD_ENV as BROKER_FD_ENV, LOCK_FD_ENV as BROKER_LOCK_FD_ENV, SOCKET_ENV as BROKER_SOCKET_ENV

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

# A restricted Claude parent still needs its own subscription credential, but no
# model-controlled Bash process, hook, or script may inherit host/cloud/Git
# credentials. These names are also listed in each dispatch's OS sandbox
# settings so the boundary remains fail-closed if a future launcher grows a new
# environment source.
CLAUDE_DENIED_ENV = (
    "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AZURE_CLIENT_SECRET",
    "GOOGLE_APPLICATION_CREDENTIALS", "GH_TOKEN", "GITHUB_TOKEN",
    "GIT_ASKPASS", "SSH_ASKPASS", "SSH_AUTH_SOCK", "NPM_TOKEN",
    "PYPI_TOKEN", "DOCKER_AUTH_CONFIG",
)

CODEX_DENIED_ENV = (
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN",
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "AZURE_CLIENT_SECRET", "GOOGLE_APPLICATION_CREDENTIALS", "GH_TOKEN",
    "GITHUB_TOKEN", "GIT_ASKPASS", "SSH_ASKPASS", "SSH_AUTH_SOCK",
    "NPM_TOKEN", "PYPI_TOKEN", "DOCKER_AUTH_CONFIG",
)
CODEX_WORKER_HOMES = config.ROOT / "codex-workers"
CODEX_RUNTIME_ROOT = Path(os.environ.get(
    "ALTITUDE_CODEX_RUNTIME_ROOT",
    str(config.HOME / ".local" / "share" / "altitude" / "codex-runtime"),
))
_CODEX_PROFILE = "altitude"
_CODEX_ALLOWED_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
_PR_SET_CHILD_SUBREAPER = 36
_PID_NAMESPACE_BWRAP = Path("/usr/bin/bwrap")


def generation_isolation_command(argv: list[str]) -> list[str]:
    """Run one generation below Bubblewrap's PID-namespace reaper.

    This layer deliberately does not alter filesystem/network access; the
    engine's independent permission profile still owns those boundaries.  Its
    sole purpose is kernel-enforced process ownership: when the namespace
    reaper exits, Linux kills every descendant, including double-forked or
    ``setsid`` tools which escaped the wrapper's process group.  The reaper is
    intentionally allowed to outlive its launching daemon: its exact host PID
    is durable generation evidence, so a restarted daemon can adopt or stop it.
    """
    if sys.platform != "linux":
        raise RuntimeError("generation PID isolation requires Linux")
    try:
        info = _PID_NAMESPACE_BWRAP.stat()
    except OSError as exc:
        raise RuntimeError(f"generation PID isolation unavailable: {exc}") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError("generation PID isolation bwrap is not a trusted root-owned executable")
    return [str(_PID_NAMESPACE_BWRAP), "--unshare-user", "--unshare-pid",
            "--bind", "/", "/", "--proc", "/proc", "--dev-bind", "/dev", "/dev", "--", *argv]


def enable_generation_subreaper() -> None:
    """Make this per-generation wrapper adopt detached descendants until cleanup."""
    if sys.platform != "linux":
        raise RuntimeError("generation descendant ownership requires Linux")
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        err = ctypes.get_errno()
        raise RuntimeError(f"cannot establish generation subreaper: errno {err}")


def _proc_parent_identity(pid: int) -> tuple[str, int, str] | None:
    try:
        fields = Path(f"/proc/{int(pid)}/stat").read_text().rsplit(")", 1)[1].split()
        return fields[19], int(fields[1]), fields[0]
    except (OSError, ValueError, IndexError):
        return None


def generation_descendants(root_pid: int, root_start: str | int,
                           *, exclude_pid: int | None = None) -> dict[int, str] | None:
    """Return exact live descendants by PPID ancestry; ``None`` is indeterminate."""
    root = _proc_parent_identity(int(root_pid))
    if root is None or root[0] != str(root_start) or root[2] == "Z":
        return None
    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return None
    snapshot: dict[int, tuple[str, int, str]] = {}
    for entry in entries:
        if entry.name.isdigit():
            identity = _proc_parent_identity(int(entry.name))
            if identity is not None:
                snapshot[int(entry.name)] = identity
    descendants: dict[int, str] = {}
    for pid, (started, parent, state) in snapshot.items():
        if pid in (root_pid, exclude_pid) or state == "Z":
            continue
        seen: set[int] = set()
        ancestor = parent
        while ancestor > 0 and ancestor not in seen:
            if ancestor == root_pid:
                descendants[pid] = started
                break
            seen.add(ancestor)
            row = snapshot.get(ancestor)
            if row is None:
                break
            ancestor = row[1]
    return descendants


def reap_generation_descendants(root_pid: int, root_start: str | int,
                                *, grace: float = 0.5) -> dict[int, str] | None:
    """TERM/KILL exact generation descendants, including new process groups/sessions."""
    for sig, wait_for in ((signal.SIGTERM, grace), (signal.SIGKILL, max(grace, 0.5))):
        members = generation_descendants(root_pid, root_start)
        if members is None:
            return None
        if not members:
            return {}
        for pid, started in members.items():
            identity = _proc_parent_identity(pid)
            if identity is None or identity[0] != started or identity[2] == "Z":
                continue
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
            except OSError:
                return None
        deadline = time.monotonic() + wait_for
        while time.monotonic() < deadline:
            current = generation_descendants(root_pid, root_start)
            if current is None:
                return None
            if not current:
                return {}
            time.sleep(0.02)
    return generation_descendants(root_pid, root_start)


def claude_worker_settings(path: Path, *, cwd: Path, writable: bool,
                           broker_dir: Path | None = None,
                           git_read_paths: tuple[Path, ...] = (),
                           hooks: dict | None = None, env: dict | None = None) -> Path:
    """Write one fail-closed Claude worker policy, independent of user/project settings.

    ``--restricted`` confines file tools to ``cwd``; this policy separately
    confines Bash and every script it launches at the OS layer. The model can
    edit only its exact worktree (when ``writable``), can read but never mutate
    its generation's Git metadata, cannot read the host home/Altitude state, and
    has no network access. One pre-created, exact Unix socket is the sole
    precise exception used for privileged Altitude operations; the model never
    receives write authority over the socket's parent directory.
    """
    path, cwd = Path(path), Path(cwd).resolve()
    broker = Path(broker_dir).resolve() if broker_dir else None
    broker_endpoint = broker / "broker.fifo" if broker is not None else None
    git_paths = [Path(value).resolve() for value in git_read_paths if value]
    home = Path.home().resolve()
    uid_runtime = Path(f"/run/user/{os.getuid()}")
    allow_read = [str(cwd), *(str(value) for value in git_paths)]
    allow_write = [str(cwd)] if writable else []
    if broker is not None:
        # bin/alt imports only these two files before forwarding the request.
        allow_read.extend([
            str(broker), str(broker_endpoint), str((config.REPO / "bin" / "alt").resolve()),
            str((config.REPO / "altitude" / "__init__.py").resolve()),
            str((config.REPO / "altitude" / "alt_broker.py").resolve()),
        ])
    credential_files = [
        home / ".claude" / ".credentials.json", home / ".ssh",
        home / ".config" / "gh", home / ".gitconfig", home / ".netrc",
        home / ".aws", home / ".azure", home / ".config" / "gcloud",
        home / ".docker" / "config.json", config.ROOT,
    ]
    settings = {
        "autoCompactWindow": config.AUTOCOMPACT_WINDOW,
        "crossSessionInbound": "refuse",
        "permissions": {"deny": ["Agent", "Task", "SendMessage", "ListAgents", "WebFetch", "WebSearch"]},
        "sandbox": {
            "enabled": True,
            "failIfUnavailable": True,
            "autoAllowBashIfSandboxed": True,
            "allowUnsandboxedCommands": False,
            "network": {
                "strictAllowlist": True, "allowedDomains": [],
                "allowUnixSockets": ([str(broker_endpoint)] if broker_endpoint is not None else []),
                "allowAllUnixSockets": False,
            },
            "filesystem": {
                # Specific allows override the broad home/state denial. This
                # lets a worker see its checkout without exposing sibling
                # worktrees, credentials, or ~/.altitude.
                "denyRead": [str(home), str(uid_runtime)],
                "allowRead": list(dict.fromkeys(allow_read)),
                "denyWrite": list(dict.fromkeys([
                    str(home), str(uid_runtime), str(config.ROOT.resolve()),
                    *(str(value) for value in git_paths),
                    *(str(value) for value in (() if writable else (cwd,))),
                ])),
                "allowWrite": list(dict.fromkeys(allow_write)),
            },
            "credentials": {
                "files": [{"path": str(value), "mode": "deny"} for value in credential_files],
                "envVars": [{"name": name, "mode": "deny"} for name in CLAUDE_DENIED_ENV],
            },
        },
    }
    if hooks:
        settings["hooks"] = hooks
    if env:
        # Hook context is trusted input. It is not authority: model-facing
        # ``alt`` is still generation-fenced by the broker token.
        settings["env"] = {str(key): str(value) for key, value in env.items()}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2, sort_keys=True) + "\n")
    return path


def _restricted_claude_env(extra_env: dict | None) -> dict:
    trusted_parent_auth = {
        name: os.environ[name] for name in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")
        if os.environ.get(name)
    }
    env = clean_env()
    # Parent authentication remains available to Claude Code itself. The
    # runtime scrub + credentials policy removes it from all model subprocesses.
    env["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] = "1"
    env.update(extra_env or {})
    # Call-site context may not introduce *any* host publication/login
    # authority. Claude parent auth is restored only from the original trusted
    # service environment (or Claude reads its credential file itself).
    for name in CLAUDE_DENIED_ENV:
        env.pop(name, None)
    env.update(trusted_parent_auth)
    return env


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
                 timeout: int = config.L3_TURN_TIMEOUT, start_new_session: bool = False,
                 restricted: bool = False, generation_isolation: bool = False) -> dict:
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
    if restricted:
        cmd += ["--restricted", "--strict-mcp-config"]
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
    env = _restricted_claude_env(extra_env) if restricted else clean_env()
    if not restricted:
        env.update(extra_env or {})
    # prompt goes through stdin: --allowedTools is variadic and would swallow a positional prompt
    launch_cmd = generation_isolation_command(cmd) if generation_isolation else cmd
    if generation_isolation and not start_new_session:
        raise RuntimeError("generation PID isolation requires a private launch session")
    proc = subprocess.Popen(launch_cmd, cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
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
              settings: Path | None = None, extra_env: dict | None = None,
              allowed_tools: str | None = None, tools: str | None = None,
              restricted: bool = False) -> dict:
    """Start a background session (verified shape). Returns what `claude --bg` printed + the agent row."""
    cmd = [config.CLAUDE_BIN, "--bg", "--name", name, "--permission-mode", permission_mode]
    if restricted:
        cmd += ["--restricted", "--strict-mcp-config"]
    if worktree:
        cmd += ["-w", worktree]
    if persona:
        cmd += ["--append-system-prompt-file", str(persona)]
    if max_turns:
        cmd += ["--max-turns", str(max_turns)]
    if model:
        cmd += ["--model", model]
    if allowed_tools:
        cmd += ["--allowedTools", allowed_tools]
    if tools is not None:
        cmd += ["--tools", tools]
    cmd += ["--settings", str(settings or claude_settings())]  # decision 49: the 300k umbrella rides on every launch
    env = _restricted_claude_env(extra_env) if restricted else clean_env()
    if not restricted:
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
                     extra_env: dict | None = None, allowed_tools: str | None = None,
                     tools: str | None = None, restricted: bool = False) -> dict:
    cmd = [config.CLAUDE_BIN, "--bg", "--name", name, "--resume", session_id, "--permission-mode", permission_mode]
    if restricted:
        cmd += ["--restricted", "--strict-mcp-config"]
    if persona:
        cmd += ["--append-system-prompt-file", str(persona)]
    if max_turns:
        cmd += ["--max-turns", str(max_turns)]
    if allowed_tools:
        cmd += ["--allowedTools", allowed_tools]
    if tools is not None:
        cmd += ["--tools", tools]
    cmd += ["--settings", str(settings or claude_settings())]  # decision 49: the 300k umbrella rides on every launch
    env = _restricted_claude_env(extra_env) if restricted else clean_env()
    if not restricted:
        env.update(extra_env or {})
    p = subprocess.run(cmd + [prompt], cwd=str(cwd), capture_output=True, text=True, timeout=120, env=env)
    return {"stdout": p.stdout.strip(), "stderr": p.stderr.strip(), "returncode": p.returncode, "agent": find_agent(name)}


def claude_rm(agent_id: str) -> str:
    p = subprocess.run([config.CLAUDE_BIN, "rm", agent_id], capture_output=True, text=True, timeout=60, env=clean_env())
    return (p.stdout + p.stderr).strip()


class CodexPermissionProfileError(RuntimeError):
    """A Codex worker cannot be launched with a complete private permission profile."""


def _trusted_private_file(path: Path, *, limit: int) -> bytes:
    """Read one service-owned, non-symlink private file without a check/open race."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise CodexPermissionProfileError(f"trusted Codex file is unavailable: {path}: {exc}") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise CodexPermissionProfileError(f"trusted Codex file is not a service-owned regular file: {path}")
        if info.st_mode & 0o077:
            raise CodexPermissionProfileError(f"trusted Codex file is not private (mode {info.st_mode & 0o777:o}): {path}")
        if info.st_size < 2 or info.st_size > limit:
            raise CodexPermissionProfileError(f"trusted Codex file has an invalid size: {path}")
        chunks, remaining = [], limit + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > limit:
            raise CodexPermissionProfileError(f"trusted Codex file exceeds {limit} bytes: {path}")
        return data
    finally:
        os.close(descriptor)


def _private_dir(path: Path, mode: int = 0o700) -> Path:
    """Create or validate an exact service-owned directory and force the requested private mode."""
    try:
        path.mkdir(mode=mode, parents=False, exist_ok=True)
        info = path.lstat()
    except OSError as exc:
        raise CodexPermissionProfileError(f"cannot construct private Codex directory {path}: {exc}") from exc
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise CodexPermissionProfileError(f"private Codex path changed identity: {path}")
    try:
        path.chmod(mode)
    except OSError as exc:
        raise CodexPermissionProfileError(f"cannot secure private Codex directory {path}: {exc}") from exc
    return path


def _atomic_private_file(path: Path, data: bytes, mode: int = 0o600) -> None:
    """Replace one exact private file without following an attacker-controlled link."""
    temporary = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = None
    try:
        descriptor = os.open(temporary, flags, mode)
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short private-file write")
            view = view[written:]
        os.fsync(descriptor)
        os.fchmod(descriptor, mode)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path)
    except OSError as exc:
        raise CodexPermissionProfileError(f"cannot write private Codex file {path}: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            logger.exception("Failed to remove private Codex temporary file %s", temporary)


def _toml_string(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _trusted_codex_defaults() -> tuple[bytes, str, str | None]:
    """Copy auth authority, but import only the trusted model scalars from user config."""
    trusted = config.HOME / ".codex"
    auth = _trusted_private_file(trusted / "auth.json", limit=2 * 1024 * 1024)
    raw_config = _trusted_private_file(trusted / "config.toml", limit=1024 * 1024)
    try:
        auth_value = json.loads(auth)
        parsed = tomllib.loads(raw_config.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, TypeError, tomllib.TOMLDecodeError) as exc:
        raise CodexPermissionProfileError(f"trusted Codex auth/config is malformed: {exc}") from exc
    if not isinstance(auth_value, dict) or not isinstance(parsed, dict):
        raise CodexPermissionProfileError("trusted Codex auth/config must contain objects")
    model = parsed.get("model")
    effort = parsed.get("model_reasoning_effort")
    if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", model):
        raise CodexPermissionProfileError("trusted Codex config has no safe explicit default model")
    if effort is not None and (not isinstance(effort, str) or effort not in _CODEX_ALLOWED_EFFORTS):
        raise CodexPermissionProfileError("trusted Codex config has an unsupported reasoning effort")
    return auth, model, effort


def _canonical_permission_path(value: Path, *, must_be_directory: bool | None = None) -> Path:
    try:
        resolved = Path(value).expanduser().resolve(strict=True)
        info = resolved.stat()
    except OSError as exc:
        raise CodexPermissionProfileError(f"Codex permission path is unavailable: {value}: {exc}") from exc
    if must_be_directory is True and not stat.S_ISDIR(info.st_mode):
        raise CodexPermissionProfileError(f"Codex permission path is not a directory: {resolved}")
    if must_be_directory is False and not stat.S_ISREG(info.st_mode):
        raise CodexPermissionProfileError(f"Codex permission path is not a regular file: {resolved}")
    return resolved


def _private_codex_runtime() -> Path:
    """Copy the trusted CLI outside host ``~/.codex`` for sandbox re-exec.

    Codex's Linux sandbox re-executes the current binary inside Bubblewrap. A
    direct launch from the operator's package tree would therefore require
    granting model commands a host ``~/.codex`` path. One version-keyed private
    copy avoids that exception and is shared by isolated worker homes.
    """
    configured = str(config.CODEX_BIN)
    candidate = shutil.which(configured) if not Path(configured).is_absolute() else configured
    if not candidate:
        raise CodexPermissionProfileError(f"Codex executable is unavailable: {configured}")
    source = _canonical_permission_path(Path(candidate), must_be_directory=False)
    try:
        source_info = source.stat()
    except OSError as exc:
        raise CodexPermissionProfileError(f"Codex executable identity is unavailable: {exc}") from exc
    if source_info.st_uid not in (0, os.getuid()) or source_info.st_mode & 0o022:
        raise CodexPermissionProfileError("Codex executable is not trusted (owner/mode)")
    if source_info.st_size < 16 or source_info.st_size > 512 * 1024 * 1024:
        raise CodexPermissionProfileError("Codex executable has an invalid size")
    runtime_dir = Path(CODEX_RUNTIME_ROOT).expanduser()
    try:
        runtime_dir.mkdir(mode=0o711, parents=True, exist_ok=True)
    except OSError as exc:
        raise CodexPermissionProfileError(f"cannot construct Codex runtime directory {runtime_dir}: {exc}") from exc
    runtime_dir = _private_dir(runtime_dir.resolve(strict=True), 0o711)
    identity = f"{source_info.st_dev}:{source_info.st_ino}:{source_info.st_size}:{source_info.st_mtime_ns}"
    target = runtime_dir / ("codex-" + hashlib.sha256(identity.encode()).hexdigest()[:20])
    try:
        current = target.lstat()
    except FileNotFoundError:
        current = None
    except OSError as exc:
        raise CodexPermissionProfileError(f"Codex runtime copy is unavailable: {exc}") from exc
    if current is not None:
        if (not stat.S_ISREG(current.st_mode) or current.st_uid != os.getuid()
                or current.st_size != source_info.st_size or current.st_mode & 0o222):
            raise CodexPermissionProfileError("Codex runtime copy changed identity")
        if stat.S_IMODE(current.st_mode) != 0o555:
            try:
                target.chmod(0o555)
            except OSError as exc:
                raise CodexPermissionProfileError(f"cannot secure Codex runtime mode: {exc}") from exc
        return target
    temporary = runtime_dir / f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    source_fd = target_fd = None
    try:
        source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        check = os.fstat(source_fd)
        if (check.st_dev, check.st_ino, check.st_size, check.st_mtime_ns) != (
                source_info.st_dev, source_info.st_ino, source_info.st_size, source_info.st_mtime_ns):
            raise CodexPermissionProfileError("Codex executable changed while it was copied")
        target_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                            | getattr(os, "O_NOFOLLOW", 0), 0o555)
        copied = 0
        while chunk := os.read(source_fd, 1024 * 1024):
            view = memoryview(chunk)
            while view:
                written = os.write(target_fd, view)
                if written <= 0:
                    raise OSError("short Codex runtime write")
                view = view[written:]
            copied += len(chunk)
        if copied != source_info.st_size:
            raise CodexPermissionProfileError("Codex runtime copy was truncated")
        os.fsync(target_fd)
        os.fchmod(target_fd, 0o555)
        os.close(target_fd); target_fd = None
        os.replace(temporary, target)
    except CodexPermissionProfileError:
        raise
    except OSError as exc:
        raise CodexPermissionProfileError(f"cannot construct private Codex runtime: {exc}") from exc
    finally:
        if source_fd is not None:
            os.close(source_fd)
        if target_fd is not None:
            os.close(target_fd)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            logger.exception("Failed to remove Codex runtime temporary file %s", temporary)
    return target


def _codex_broker_paths(extra_env: dict | None) -> tuple[list[Path], list[Path], list[Path]]:
    """Grant only trusted forwarding code, never the inherited broker endpoint."""
    values = extra_env or {}
    if values.get(BROKER_SOCKET_ENV):
        raise CodexPermissionProfileError(
            "Codex broker path transport is forbidden; use a preconnected inherited capability")
    inherited = (values.get(BROKER_FD_ENV), values.get(BROKER_LOCK_FD_ENV))
    if any(inherited):
        if not all(inherited):
            raise CodexPermissionProfileError("Codex inherited broker environment is incomplete")
        code = [
            _canonical_permission_path(config.REPO / "bin" / "alt", must_be_directory=False),
            _canonical_permission_path(config.REPO / "altitude" / "__init__.py", must_be_directory=False),
            _canonical_permission_path(config.REPO / "altitude" / "alt_broker.py", must_be_directory=False),
        ]
        return code, [], []
    return [], [], []


def _validated_codex_broker_fds(extra_env: dict | None,
                                broker_fds: tuple[int, int] | None) -> tuple[int, ...]:
    """Bind inherited descriptor numbers to their exact environment identity."""
    values = extra_env or {}
    raw_connection = values.get(BROKER_FD_ENV)
    raw_lock = values.get(BROKER_LOCK_FD_ENV)
    if broker_fds is None:
        if raw_connection is not None or raw_lock is not None:
            raise CodexPermissionProfileError("Codex broker descriptors were not authorized for inheritance")
        return ()
    if (not isinstance(broker_fds, tuple) or len(broker_fds) != 2
            or any(type(value) is not int or value < 3 for value in broker_fds)
            or broker_fds[0] == broker_fds[1]):
        raise CodexPermissionProfileError("Codex broker descriptors are malformed")
    if str(raw_connection or "") != str(broker_fds[0]) or str(raw_lock or "") != str(broker_fds[1]):
        raise CodexPermissionProfileError("Codex broker descriptor environment does not match pass_fds")
    try:
        connection_info = os.fstat(broker_fds[0])
        lock_info = os.fstat(broker_fds[1])
    except OSError as exc:
        raise CodexPermissionProfileError(f"Codex broker descriptor is unavailable: {exc}") from exc
    if not stat.S_ISSOCK(connection_info.st_mode):
        raise CodexPermissionProfileError("Codex broker connection descriptor is not a socket")
    if not stat.S_ISREG(lock_info.st_mode) or lock_info.st_nlink != 0:
        raise CodexPermissionProfileError("Codex broker lock descriptor is not an anonymous regular inode")
    try:
        probe = socket.socket(fileno=os.dup(broker_fds[0]))
        try:
            probe.getpeername()
        finally:
            probe.close()
    except OSError as exc:
        raise CodexPermissionProfileError(f"Codex broker socket is not connected: {exc}") from exc
    return broker_fds


def _codex_profile_text(*, cwd: Path, writable: bool, read_paths: tuple[Path, ...],
                        write_paths: tuple[Path, ...], unix_sockets: tuple[Path, ...],
                        model: str, effort: str | None) -> str:
    access: dict[str, str] = {":minimal": "read"}
    # These explicit denials remain effective even if a future workspace root
    # is accidentally broadened. Exact runtime grants are narrower; inherited
    # brokers add no filesystem or network exception.
    # ``:minimal`` denies arbitrary host-home paths; exact regression coverage
    # includes an unrelated ``~/private-secret``.  A parent HOME denial cannot
    # be added here: Codex's Bubblewrap helper must traverse CODEX_HOME and most
    # Altitude workspaces also live below HOME.  Retain explicit credential and
    # state denials as defense in depth, then grant only the exact runtime,
    # workspace and broker paths below.
    for sensitive in (
        config.HOME / ".ssh", config.HOME / ".config" / "gh", config.HOME / ".claude",
        config.HOME / ".codex", config.HOME / ".altitude", config.ROOT,
    ):
        access[str(Path(sensitive).expanduser().resolve())] = "deny"
    for path in read_paths:
        access[str(path)] = "read"
    for path in write_paths:
        access[str(path)] = "write"
    lines = [
        f"model = {_toml_string(model)}",
        *( [f"model_reasoning_effort = {_toml_string(effort)}"] if effort else [] ),
        'approval_policy = "never"',
        "allow_login_shell = false",
        f"default_permissions = {_toml_string(_CODEX_PROFILE)}",
        "",
        "[features]",
        "apps = false",
        "browser_use = false",
        "browser_use_external = false",
        "computer_use = false",
        "image_generation = false",
        "multi_agent = false",
        "plugins = false",
        "remote_plugin = false",
    ]
    if unix_sockets:
        # Keep the proxy's own effective Unix-socket allowlist identical to
        # the permission-profile override.  Codex 0.151 requires the proxy
        # feature policy as well as the per-profile capability on Linux.
        lines += ["", "[features.network_proxy]", "enabled = true", "",
                  "[features.network_proxy.unix_sockets]"]
        lines.extend(f"{_toml_string(str(path))} = \"allow\"" for path in unix_sockets)
    else:
        lines.append("network_proxy = false")
    lines += [
        "",
        "[shell_environment_policy]",
        'inherit = "all"',
        "exclude = [" + ", ".join(_toml_string(name) for name in CODEX_DENIED_ENV) + "]",
        "",
        f"[permissions.{_CODEX_PROFILE}.filesystem]",
    ]
    lines.extend(f"{_toml_string(path)} = {_toml_string(mode)}" for path, mode in sorted(access.items()))
    lines += [
        "",
        f"[permissions.{_CODEX_PROFILE}.filesystem.\":workspace_roots\"]",
        f'"." = {"write" if writable else "read"!r}',
        "",
        f"[permissions.{_CODEX_PROFILE}.network]",
        # A Unix socket is a network capability in Codex permission profiles.
        # Socket-bearing workers enable command networking only behind the
        # proxy; an empty domain allowlist blocks external destinations.
        f"enabled = {'true' if unix_sockets else 'false'}",
    ]
    if unix_sockets:
        lines += ["", f"[permissions.{_CODEX_PROFILE}.network.unix_sockets]"]
        lines.extend(f"{_toml_string(str(path))} = \"allow\"" for path in unix_sockets)
    return "\n".join(lines) + "\n"


def codex_worker_home(*, cwd: Path, role: str, permission_id: str | None = None,
                      writable: bool = False, read_paths: tuple[Path, ...] = (),
                      write_paths: tuple[Path, ...] = (), unix_sockets: tuple[Path, ...] = (),
                      model: str | None = None, effort: str | None = None) -> tuple[Path, Path, str, str | None, str]:
    """Construct one persistent, host-private Codex home and its strict inline profile.

    The home holds only copied authentication, sessions and the generated
    policy. Model-controlled commands cannot read it because the profile denies
    the whole Altitude state root and grants only the exact workspace/broker
    exceptions.
    """
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", str(role)):
        raise CodexPermissionProfileError(f"invalid Codex permission role {role!r}")
    cwd = _canonical_permission_path(cwd, must_be_directory=True)
    reads = tuple(dict.fromkeys(_canonical_permission_path(path) for path in read_paths))
    writes = tuple(dict.fromkeys(_canonical_permission_path(path) for path in write_paths))
    sockets = tuple(dict.fromkeys(_canonical_permission_path(path) for path in unix_sockets))
    auth, configured_model, configured_effort = _trusted_codex_defaults()
    selected_model, selected_effort = model or configured_model, effort if effort is not None else configured_effort
    identity = f"{role}\0{permission_id or ''}\0{cwd}"
    digest = hashlib.sha256(identity.encode()).hexdigest()[:24]
    root = CODEX_WORKER_HOMES
    if root.exists():
        _private_dir(root, 0o711)
    else:
        try:
            root.mkdir(mode=0o700, parents=True)
        except OSError as exc:
            raise CodexPermissionProfileError(f"cannot construct Codex worker-home root {root}: {exc}") from exc
        _private_dir(root, 0o711)
    runtime = _private_codex_runtime()
    reads = tuple(dict.fromkeys((*reads, runtime)))
    home = _private_dir(root / f"{role}-{digest}")
    profile = _codex_profile_text(cwd=cwd, writable=writable, read_paths=reads,
                                  write_paths=writes, unix_sockets=sockets,
                                  model=selected_model, effort=selected_effort)
    _atomic_private_file(home / "auth.json", auth)
    _atomic_private_file(home / "config.toml", profile.encode())
    _atomic_private_file(home / f"{_CODEX_PROFILE}.config.toml", profile.encode())
    return home, runtime, selected_model, selected_effort, profile


def _codex_profile_overrides(profile: str) -> list[str]:
    """Render only the security-critical generated profile as CLI overrides.

    ``--ignore-user-config`` prevents user/project trust, plugins and MCP from
    entering the launch. Inline overrides work for both fresh and resumed exec,
    whose CLI surfaces differ in 0.151.
    """
    parsed = tomllib.loads(profile)
    features = parsed["features"]
    permission = parsed["permissions"][_CODEX_PROFILE]
    filesystem = permission["filesystem"]
    network = permission["network"]
    filesystem_inline = "{" + ",".join(
        f"{_toml_string(str(key))}={_toml_inline(value)}" for key, value in filesystem.items()
    ) + "}"
    network_inline = "{" + ",".join(
        f"{_toml_string(str(key))}={_toml_inline(value)}" for key, value in network.items()
    ) + "}"
    return [
        f'default_permissions={_toml_string(_CODEX_PROFILE)}',
        f'permissions.{_CODEX_PROFILE}.filesystem={filesystem_inline}',
        f'permissions.{_CODEX_PROFILE}.network={network_inline}',
        'approval_policy="never"', "allow_login_shell=false",
        "features.apps=false", "features.browser_use=false", "features.browser_use_external=false",
        "features.computer_use=false", "features.image_generation=false", "features.multi_agent=false",
        f"features.network_proxy={_toml_inline(features['network_proxy'])}",
        "features.plugins=false", "features.remote_plugin=false",
    ]


def _toml_inline(value) -> str:
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        return "{" + ",".join(f"{_toml_string(str(k))}={_toml_inline(v)}" for k, v in value.items()) + "}"
    raise CodexPermissionProfileError(f"unsupported generated Codex profile value: {value!r}")


def codex_session_roots() -> list[Path]:
    """Trusted interactive plus isolated worker rollout roots for monitor readers."""
    roots = [config.HOME / ".codex" / "sessions"]
    try:
        roots.extend(path for path in CODEX_WORKER_HOMES.glob("*/sessions") if path.is_dir())
    except OSError:
        pass
    return roots


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
               fault_context: dict[str, str] | None = None, permission_role: str = "validator",
               permission_id: str | None = None, permission_read_paths: tuple[Path, ...] = (),
               generation_isolation: bool = False,
               broker_fds: tuple[int, int] | None = None) -> dict:
    """Run one permission-profiled Codex exec turn, fresh or resumed, and parse its JSONL lifecycle events.

    The process is streamed so callers can durably record its PID and thread id before an altd restart. Raw stdout and
    stderr remain bounded evidence artifacts, independently of the lifecycle values parsed from the stream. Resume has
    its own CLI surface, so the generated profile is supplied through strict inline config on both paths. ``sandbox``
    remains an internal read/write intent for call-site compatibility; it is never emitted as the legacy ``-s`` flag.
    """
    if sandbox not in ("read-only", "workspace-write"):
        profile_error = CodexPermissionProfileError(f"unsupported Codex permission intent {sandbox!r}")
    else:
        profile_error = None
    broker_reads: list[Path] = []
    broker_writes: list[Path] = []
    broker_sockets: list[Path] = []
    inherited_fds: tuple[int, ...] = ()
    try:
        if profile_error is not None:
            raise profile_error
        unsupported_env = [key for key in (extra_env or {})
                           if not str(key).startswith("ALTITUDE_") and key not in CODEX_DENIED_ENV]
        if unsupported_env:
            raise CodexPermissionProfileError(f"unsupported extra env {unsupported_env!r}")
        for value in extra_config or []:
            key = value.partition("=")[0].strip()
            if (not key or key == "sandbox_mode" or key.startswith("sandbox_")
                    or key == "default_permissions" or key.startswith("permissions.")
                    or key.startswith("approval_policy") or key.startswith("mcp_servers.")
                    or key.startswith("plugins.") or key in ("features.network_proxy", "web_search")):
                raise CodexPermissionProfileError(f"unsafe Codex config override rejected: {key or value!r}")
        broker_reads, broker_writes, broker_sockets = _codex_broker_paths(extra_env)
        inherited_fds = _validated_codex_broker_fds(extra_env, broker_fds)
        home, runtime, model, effort, profile = codex_worker_home(
            cwd=Path(cwd), role=permission_role, permission_id=permission_id,
            writable=sandbox == "workspace-write",
            read_paths=tuple(permission_read_paths) + tuple(broker_reads),
            write_paths=tuple(broker_writes), unix_sockets=tuple(broker_sockets),
            model=model, effort=effort,
        )
        profile_configs = _codex_profile_overrides(profile)
    except (CodexPermissionProfileError, OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        from . import improve  # local: improve -> dispatch -> engines during module import
        fault_recorded = None
        try:
            improve.system_fault("codex-permissions", str(exc), **(fault_context or {}))
            fault_recorded = "codex-permissions"
        except Exception:  # noqa: BLE001 — fault persistence must not replace the deterministic gate failure
            logger.exception("Failed to record Codex permission-profile system fault")
        error = f"Codex permission profile failed closed: {exc}"
        if len(error) > 500:
            error = error[:245] + " ... " + error[-250:]
        return {"text": "", "structured": None, "returncode": 1, "engine_started": False,
                "fault_recorded": fault_recorded, "usage": {}, "session_id": resume, "error": error,
                "raw_stdout": "", "raw_stderr": "", "raw_stdout_truncated": False,
                "raw_stderr_truncated": False}
    with tempfile.NamedTemporaryFile("r", suffix=".out", delete=False) as outf:
        out_path = outf.name
    if resume:
        cmd = [str(runtime), "exec", "resume", "--json", "-o", out_path, "--skip-git-repo-check",
               "--ignore-user-config", "--ignore-rules", "--strict-config"]
    else:
        cmd = [str(runtime), "exec", "--json", "-o", out_path, "-C", str(cwd),
               "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules", "--strict-config"]
    if schema:
        cmd += ["--output-schema", str(schema)]
    if bypass_hook_trust:
        cmd += ["--dangerously-bypass-hook-trust"]
    cmd += ["-m", model]
    for kv in [*profile_configs, *(extra_config or [])]:
        cmd += ["-c", kv]
    if effort:
        cmd += ["-c", f'model_reasoning_effort={_toml_string(effort)}']
    if resume:
        cmd += [resume]
    cmd += [prompt]

    parent = clean_env()
    env = {key: parent[key] for key in ("HOME", "PATH", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR")
           if parent.get(key)}
    env.update(extra_env or {})
    for key in (*CODEX_DENIED_ENV, *unset_env):
        env.pop(key, None)
    env["CODEX_HOME"] = str(home)
    stdout_capture, stderr_capture = _BoundedRawCapture(), _BoundedRawCapture()
    usage: dict = {}
    messages: list[str] = []
    session_id = resume
    timed_out = [False]
    proc = None
    try:
        try:
            launch_cmd = generation_isolation_command(cmd) if generation_isolation else cmd
            if generation_isolation and not start_new_session:
                raise RuntimeError("generation PID isolation requires a private launch session")
            proc = subprocess.Popen(launch_cmd, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, env=env, start_new_session=start_new_session,
                                    pass_fds=inherited_fds)
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
