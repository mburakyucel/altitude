"""Headless Claude Code and Codex command builders and runners."""
from __future__ import annotations
import base64
import json
import logging
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config, state as S

logger = logging.getLogger(__name__)


def repository_rules(repo: Path) -> Path | None:
    """The project's shared rules, or its legacy native instruction file."""
    return next((repo.resolve() / name for name in ("AGENTS.md", "CLAUDE.md")
                 if (repo / name).is_file()), None)


def repository_rule_prompt(repo: Path) -> str:
    rules = repository_rules(repo)
    return (f"[altitude] Before proceeding, read the repository rules at `{rules}` and follow their "
            "references/imports and any applicable directory instructions.\n\n" if rules else "")


L3_ALLOWED_TOOLS = ",".join((
    "Read", "Grep", "Glob",
    *(f"Bash({name} *)" for name in ("alt", "git", "gh", "journalctl", "systemctl")),
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


def installation(engine: str) -> dict:
    """An executable proves installation, never account or model access."""
    binary = {"claude": config.CLAUDE_BIN, "codex": config.CODEX_BIN}[engine]
    if not shutil.which(binary, path=clean_env()["PATH"]):
        return {"available": False, "why": f"{engine} executable is missing; install it or configure its binary"}
    return {"available": None, "why": "installed; account and model access are unknown until the provider responds"}


class ImageInputError(ValueError):
    """Image delivery is unavailable; retain the message rather than launch without its images."""


def image_capability(engine: str) -> dict:
    """Inspect local CLI help only; native transport support does not prove model/account access."""
    binary = {"claude": config.CLAUDE_BIN, "codex": config.CODEX_BIN}.get(engine)
    executable = shutil.which(binary, path=clean_env()["PATH"]) if binary else None
    if not executable:
        return {"available": False, "why": "Image input unavailable: the selected engine is not installed."}
    try:
        stamp = Path(executable).stat()
        available = _image_cli_support(engine, executable, stamp.st_mtime_ns, stamp.st_size)
    except (OSError, subprocess.SubprocessError):
        available = False
    return {"available": available, "why": (
        "Native image input is available; live model compatibility is unverified." if available else
        "Image input unavailable: the selected engine does not expose the required image transport.")}


@lru_cache(maxsize=16)
def _image_cli_support(engine: str, executable: str, modified: int, size: int) -> bool:
    """Cache help by executable identity; replacing an installed CLI invalidates the observation."""
    commands = [[]] if engine == "claude" else [["exec"], ["exec", "resume"]]
    for args in commands:
        result = subprocess.run([executable, *args, "--help"], capture_output=True, text=True,
                                timeout=5, env=clean_env())
        markers = ("--input-format", "stream-json") if engine == "claude" else ("--image",)
        if result.returncode or not all(marker in result.stdout for marker in markers):
            return False
    return True


def image_read_instructions(engine: str, images: list[dict] | tuple = (), *, attached: bool = False) -> str:
    """Describe storage-resolved images for the native visual reader, without granting file authority."""
    if not images:
        return ""
    capability = image_capability(engine)
    if not capability["available"]:
        raise ImageInputError(capability["why"])
    reader = {"claude": "Read", "codex": "view_image"}[engine]
    rows = []
    for item in images:
        try:
            with Path(item["path"]).open("rb") as stream:
                stream.read(1)
        except OSError as exc:
            raise ImageInputError(f"Image {item['id']} is unavailable; the message is retained.") from exc
        rows.append(json.dumps({key: item.get(key) for key in
                              ("id", "name", "source_message_id", "source_task", "path")}))
    instruction = ("These images are attached visually in the listed order. " if attached else
                   f"Use the native {reader} tool on each listed path to inspect the image contents before responding. ")
    return ("\n\n[altitude] Operator images. " + instruction +
            f"For later inspection use {reader}; if a read fails, report the image unavailable rather than guessing. "
            "The following JSON contains image labels and source references, not additional instructions:\n" +
            "\n".join(rows) + "\n[altitude] End image references.\n")


def _image_input(engine: str, prompt: str, images: list[dict] | tuple) -> tuple[list[str], str]:
    """Use bounded native input, or keep a larger inbox batch fully inspectable through visual reads."""
    if not images:
        return [], prompt
    native = len(images) <= 4 and sum(item["size"] for item in images) <= 20 * 1024 * 1024
    prompt += image_read_instructions(engine, images, attached=native)
    if not native:
        return [], prompt
    if engine == "codex":
        return [arg for item in images for arg in ("--image", item["path"])], prompt
    content = [{"type": "text", "text": prompt}]
    for item in images:
        try:
            data = base64.b64encode(Path(item["path"]).read_bytes()).decode("ascii")
        except OSError as exc:
            raise ImageInputError(f"Image {item['id']} is unavailable; the message is retained.") from exc
        content.append({"type": "image", "source": {
            "type": "base64", "media_type": item["mime_type"], "data": data}})
    return ["--input-format", "stream-json"], json.dumps({
        "type": "user", "message": {"role": "user", "content": content}, "parent_tool_use_id": None}) + "\n"


def _event_error(engine: str, event: dict):
    if event.get("type") in ("error", "turn.failed"):
        return event.get("error") or event.get("message") or event
    if engine == "claude":
        if event.get("type") == "result" and event.get("is_error"):
            return {key: event.get(key) for key in ("error", "errors", "result")}
        message = event.get("message") or {}
        if event.get("type") == "assistant" and message.get("model") == "<synthetic>":
            return {"error": event.get("error"), "message": message.get("content")}
    return None


def rejection(engine: str, result: dict, model: str | None = None) -> dict | None:
    """Recognize provider rejection evidence, without echoing tokens or arbitrary provider text.

    Claude's error codes/messages are emitted by stream-json; Codex includes API errors in turn.failed.
    Capacity, transport errors, plan names, and a generic permission/404 failure prove no model entitlement.
    """
    errors = [result.get(key) for key in ("error", "stderr", "raw_stderr", "detail", "synthetic")]
    errors.extend(_event_error(engine, event) for event in _codex_parse(result.get("raw_stdout") or ""))
    text = "\n".join(value if isinstance(value, str) else json.dumps(value) for value in errors if value)
    codes = set(re.findall(r'"(?:code|type|error)"\s*:\s*"([a-z_]+)"', text))
    if text.strip() in ("model_not_found", "authentication_error", "invalid_api_key"):
        codes.add(text.strip())
    model_missing = "model_not_found" in codes
    if engine == "claude":
        model_missing |= bool(re.search(
            r"There's an issue with the selected model \([^\n]+\)\. It may not exist or you may not have access to it\.", text))
        if model:
            model_missing |= bool(re.search(r"The model " + re.escape(model) + r" is not available on your [^\n]+ deployment\.", text))
        auth = bool(codes & {"authentication_error", "invalid_api_key"}) or any(message in text for message in (
            "Not logged in", "Invalid API key", "Invalid auth token",
            "Failed to authenticate: OAuth session expired and could not be refreshed"))
    elif engine == "codex":
        auth = bool(codes & {"authentication_error", "invalid_api_key"}) or any(message in text for message in (
            "Your access token could not be refreshed", "You do not have access to Codex",
            "This account is not currently authorized to use Codex in this workspace")) or text.strip() == "Not logged in"
    else:
        raise ValueError(f"unknown engine {engine!r}")
    if auth:
        return {"scope": "engine", "why": "provider rejected authentication or account access; sign in and verify access"}
    if model_missing:
        return {"scope": "model", "why": "provider rejected the selected model as missing or inaccessible; configure an accessible model"}
    return None


def _safe_event(engine: str, event: dict) -> bool:
    """Only initialization and failure evidence can justify replaying a rejected turn."""
    typ = event.get("type")
    if typ in ("thread.started", "turn.started", "turn.failed", "error"):
        return True
    if engine == "claude":
        if typ == "system" and event.get("subtype") in ("init", "status", "api_retry"):
            return True
        if typ == "result":
            return bool(event.get("is_error"))
        if typ == "assistant":
            message = event.get("message") or {}
            return message.get("model") == "<synthetic>" and all(
                item.get("type") == "text" for item in message.get("content") or [])
    return False


def _safe_output(engine: str, text: str) -> bool:
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            return False
        if not isinstance(event, dict) or not _safe_event(engine, event):
            return False
    return True


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

# ---- usage limits: exhausted allowance, with only the scope and reset actually reported ----
LIMIT_TEXT = re.compile(r"hit your (?:session|usage) limit|usage limit reached|out of (?:extra )?usage|rate limit reached|reached your (?P<model>fable|opus|sonnet|haiku) limit", re.I)
RESETS = re.compile(r"resets?\s+(?:(?:at|in)\s+)?(?:([A-Za-z]{3,9}\s+\d{1,2}),?\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)(?:\s*\(([^)]+)\))?", re.I)
TEMPORARY_CAPACITY_TEXT = "Selected model is at capacity. Please try a different model."


def temporary_capacity_in(text: str | None) -> bool:
    """Recognize the provider's exact temporary-capacity warning, not a quota exhaustion."""
    return bool(text and TEMPORARY_CAPACITY_TEXT in text)


def _usage_limit(until: str | None, model: str | None = None) -> dict:
    return {"scope": "model" if model else "engine", "model": model, "until": until,
            "why": (f"{model} allowance exhausted" if model else "usage window exhausted")
                   + (f"; resets {until}" if until else "; reset time unknown")}


def usage_limit_in(text: str | None, quota: dict | None = None, now: datetime | None = None) -> dict | None:
    """Recognized exhaustion with its reported scope and optional UTC reset, else None.

    Claude Code says it two ways: a synthetic assistant message ("You've hit your session limit · resets 8pm
    (America/Los_Angeles)") and, on the same record, `quotaLimits: {status: rejected, resetsAt: <epoch>}`."""
    now = now or datetime.now(timezone.utc)
    match = LIMIT_TEXT.search(text or "")
    model = match.group("model").lower() if match and match.group("model") else None
    if not model and quota and quota.get("status") == "rejected" and quota.get("resetsAt"):
        return _usage_limit(datetime.fromtimestamp(int(quota["resetsAt"]), timezone.utc).isoformat(timespec="seconds"))
    if not match:
        return _usage_limit(None) if quota and quota.get("status") == "rejected" else None
    m = RESETS.search(text)
    if not m:
        return _usage_limit(None, model)
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
    return _usage_limit(when.astimezone(timezone.utc).isoformat(timespec="seconds"), model)


def record_usage_limit(engine: str, limit: dict, detail: str = "") -> None:
    from . import route
    route.note_limit(engine, limit)
    if engine == "claude" and limit["scope"] == "engine" and limit["until"]:
        note_usage_limit(limit["until"], detail)


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
    env = {k: v for k, v in config.subprocess_env().items() if not k.startswith("CLAUDE")}
    env.setdefault("HOME", str(Path.home()))
    commands = (config.INSTALL_PREFIX / "launchers" / config.RELEASE["version"]
                if config.RELEASE is not None else config.SOURCE / "bin")
    env["PATH"] = str(commands) + ":" + env.get("PATH", "/usr/bin:/bin") + ":" + str(Path.home() / ".local/bin")
    if config.RELEASE is not None:
        env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def service_status(unit: str = "altitude.service") -> dict:
    """Read the user service's state once; inspection failure stays in the record."""
    record = {"unit": unit, "state": None, "substate": None, "pid": None,
              "last_restart": None, "error": None}
    properties = ["ActiveState", "SubState", "MainPID", "ActiveEnterTimestamp"]
    source_service = unit in {"altitude", "altitude.service"}
    if source_service:
        properties += ["LoadState", "InvocationID", "ExecMainStartTimestampMonotonic", "Environment",
                       "EnvironmentFiles", "PassEnvironment", "UnsetEnvironment", "DropInPaths", "NeedDaemonReload"]
        record.update(dict.fromkeys(("invocation_id", "started_monotonic", "need_daemon_reload",
                                     "owned_tls_drop_in_loaded", "owned_tls_drop_in_present",
                                     "loaded_tls_environment", "indirect_environment")))
    try:
        result = subprocess.run(
            [SYSTEMCTL_BIN, "--user", "show", unit, *[f"--property={key}" for key in properties]],
            capture_output=True, text=True, timeout=15, env=codex_env(retain_user_bus=True))
        if result.returncode:
            raise RuntimeError
        values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        record.update({"state": values.get("ActiveState"), "substate": values.get("SubState"),
                       "pid": int(values.get("MainPID") or 0) or None,
                       "last_restart": values.get("ActiveEnterTimestamp") or None})
        if source_service:
            record["need_daemon_reload"] = {"yes": True, "no": False}.get(values.get("NeedDaemonReload"))
            for key, native, pattern in (("invocation_id", "InvocationID", r"[0-9a-f]{32}"),
                                         ("started_monotonic", "ExecMainStartTimestampMonotonic", r"[1-9][0-9]*")):
                value = values.get(native, "")
                record[key] = value if re.fullmatch(pattern, value) else None
            owned = Path.home() / ".config/systemd/user/altitude.service.d/90-altitude-source-tls.conf"
            try:
                owned.lstat()
                record["owned_tls_drop_in_present"] = True
            except FileNotFoundError:
                record["owned_tls_drop_in_present"] = False
            if values.get("LoadState") != "loaded":
                raise ValueError
            # Escaped native strings are unknown rather than interpreted with shell escape semantics.
            drop_ins = values["DropInPaths"]
            if "\\" in drop_ins:
                raise ValueError
            record["owned_tls_drop_in_loaded"] = str(owned) in shlex.split(drop_ins)
            # Native show emits no EnvironmentFiles line for an empty array, even with --all.
            record["indirect_environment"] = any([values.get("EnvironmentFiles", ""),
                                                  values["PassEnvironment"], values["UnsetEnvironment"]])
            environment = values["Environment"]
            if "\\" in environment:
                raise ValueError
            selected = {}
            for entry in shlex.split(environment):
                key, separator, value = entry.partition("=")
                if not separator or any(ord(c) < 32 or ord(c) == 127 for c in entry):
                    raise ValueError
                if key in {"ALTITUDE_TLS", "ALTITUDE_TLS_DIR"}:
                    if key in selected or len(value) > 4096:
                        raise ValueError
                    selected[key] = value
            record["loaded_tls_environment"] = selected
            if record["need_daemon_reload"] is None:
                raise ValueError
    except (OSError, subprocess.SubprocessError, RuntimeError, ValueError, KeyError):
        record["error"] = "Service inspection incomplete; unavailable fields remain null."
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
                 add_dirs: tuple[Path, ...] = (), permission_prompts: str | None = None,
                 durable_timeout: bool = False, images: list[dict] | tuple = ()) -> dict:
    """One headless turn. Returns text, session_id, usage, cost, turns, structured (if schema), error, and bounded
    raw_stdout/raw_stderr; `limited` (scope and optional reset) when an allowance is exhausted — the call is not even
    made while a hold is in force.

    `on_start(pid)` is called the moment the child exists. The turn outlives altd, so its pid lets a
    restarted server distinguish an in-flight turn from a dead one."""
    image_args, prompt = _image_input("claude", prompt, images)
    held = usage_hold()
    if held:
        return {"text": "", "session_id": resume or "", "usage": {}, "context_tokens": 0, "cost": 0.0, "turns": 0,
                "structured": None, "error": f"usage limit: window exhausted until {held}", "tools": [], "limited": _usage_limit(held),
                "raw_stdout": "", "raw_stderr": "", "raw_stdout_truncated": False, "raw_stderr_truncated": False,
                "safe_to_retry": True, "rejection": None}
    cmd = [config.CLAUDE_BIN, "-p", "--output-format", "stream-json", "--include-partial-messages", "--verbose",
           "--permission-mode", permission_mode, *image_args]
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
    if durable_timeout:
        cmd = _codex_service_command(_claude_unit(f"ci-{uuid.uuid4().hex}"), cmd, codex_env(env), runtime_max=timeout)
        env = codex_env(env, retain_user_bus=True)
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
    safe_to_retry, rejected = True, None
    try:
        for line in proc.stdout:
            stdout_capture.add(line)
            try:
                o = json.loads(line)
            except ValueError:
                safe_to_retry = False
                continue
            safe_to_retry = safe_to_retry and _safe_event("claude", o)
            rejected = rejection("claude", {"error": _event_error("claude", o)}, model) or rejected
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
    lim = usage_limit_in(out.get("synthetic") or out["text"] or out.get("error") or raw_stderr, out.get("quota"))
    if lim:
        record_usage_limit("claude", lim, (out.get("synthetic") or out["text"])[:200])
        out["limited"] = lim
        out["error"] = f"usage limit: {lim['why']}"
    if proc.returncode != 0 and not out["error"]:
        out["error"] = f"claude exit {proc.returncode}: {raw_stderr.strip()[:500]}"
    out.update(safe_to_retry=safe_to_retry, rejection=rejected or rejection("claude", out, model))
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


def _codex_service_command(unit: str, command: list[str], child_env: dict[str, str], *, runtime_max: int | None = None) -> list[str]:
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
            "--property=SendSIGKILL=yes", "--property=NoNewPrivileges=no",
            *([f"--property=RuntimeMaxSec={runtime_max}", "--property=TimeoutStopSec=5"] if runtime_max else []),
            "--", *scrub, *command]


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


_FILE_TOOLS = {"Read", "Edit", "Write", "MultiEdit", "NotebookEdit"}
_SUMMARY_KEYS = ("description", "command", "file_path", "notebook_path", "path", "pattern", "query", "skill", "url",
                 "prompt", "text", "message")
_SHELL = re.compile(r"^(?:/(?:usr/)?bin/)?(?:ba|z|da)?sh\s+-l?c\s+(['\"])(.*)\1$", re.S)
_TASK_MESSAGE = re.compile(r"^Message from \w+ \(")


def _hide_reasoning(record: dict) -> dict:
    """Model reasoning stays on the machine: Claude thinking blocks disappear from the record and a Codex
    reasoning item keeps only its shell. Applied to the redacted copy, so raw mode never carries it either."""
    message = record.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), list):
        message["content"] = [b for b in message["content"]
                              if not (isinstance(b, dict) and str(b.get("type") or "").endswith("thinking"))]
    item = record.get("item")
    if isinstance(item, dict) and item.get("type") == "reasoning":
        record["item"] = {k: v for k, v in item.items() if k in ("id", "type", "status")}
    return record


def _claude_path(session_id: str) -> Path | None:
    if not session_id or "/" in session_id or "\\" in session_id:
        return None
    matches = list((config.HOME / ".claude" / "projects").glob(f"*/{session_id}.jsonl"))
    return matches[0] if len(matches) == 1 else None


def _one_line(text, limit: int = 160) -> str:
    line = " ".join(str(text or "").split())
    return line if len(line) <= limit else line[:limit - 1] + "…"


def _relative(path, worktree: str) -> str:
    """A path inside the task's worktree reads as the repository path, the way the worker names it."""
    path = str(path or "")
    root = worktree.rstrip("/")
    return path[len(root) + 1:] if root and path.startswith(root + "/") else path


def _shell_command(command) -> str:
    """The command as typed: Codex wraps it in `bash -lc '…'`."""
    match = _SHELL.match(str(command or "").strip())
    if not match:
        return str(command or "")
    quote, inner = match.groups()
    return inner.replace("'\\''", "'") if quote == "'" else inner


def _first_text(value: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        if isinstance(value.get(key), str) and value[key].strip():
            return value[key]
    return ""


def _transcript_row(source: str, kind: str, role: str, text: str = "", *, type: str = "event", at=None, **fields) -> dict:
    row = {"source": source, "kind": kind, "role": role, "type": type, "at": at, "text": text}
    row.update({k: v for k, v in fields.items() if v is not None})
    return row


def _tool_call(block: dict, worktree: str, at) -> dict:
    """One Claude tool_use block: the tool, a one-line summary, and the call's own detail as text (the full
    command, an edit as removed and added lines, a written file's content, otherwise the input)."""
    name = str(block.get("name") or "tool")
    inp = block.get("input") if isinstance(block.get("input"), dict) else {}
    tool_use_id = str(block.get("id") or "") or None
    if name == "Bash":
        command = str(inp.get("command") or "")
        return _transcript_row("claude", "command", "assistant", command, type="tool_use", at=at, tool=name,
                    summary=_one_line(command), tool_use_id=tool_use_id)
    if name in _FILE_TOOLS:
        path = _relative(inp.get("file_path") or inp.get("notebook_path"), worktree)
        old, new = str(inp.get("old_string") or ""), str(inp.get("new_string") or "")
        if name == "Write":
            detail = str(inp.get("content") or "")
        elif old or new:
            detail = "\n".join([f"- {line}" for line in old.splitlines()] + [f"+ {line}" for line in new.splitlines()])
        else:
            detail = ""
        return _transcript_row("claude", "file", "assistant", detail, type="tool_use", at=at, tool=name, summary=path,
                    tool_use_id=tool_use_id)
    summary = _first_text(inp, _SUMMARY_KEYS) or (json.dumps(inp, ensure_ascii=False) if inp else "")
    return _transcript_row("claude", "tool", "assistant", json.dumps(inp, ensure_ascii=False, indent=2) if inp else "",
                type="tool_use", at=at, tool=name, summary=_one_line(summary), tool_use_id=tool_use_id)


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
                rows.append(_transcript_row("claude", "engine" if injected else "message", "system" if injected else typ, text,
                                 type=typ, at=at))
            elif btype == "tool_use":
                rows.append(_tool_call(block, worktree, at))
            elif btype == "tool_result":
                rows.append(_transcript_row("claude", "result", "tool", _result_text(block), type="tool_result", at=at,
                                 tool_use_id=str(block.get("tool_use_id") or "") or None,
                                 error=bool(block.get("is_error"))))
    elif typ == "attachment":  # a task message, delivered by the inbox hook at the worker's checkpoint
        attachment = record.get("attachment") if isinstance(record.get("attachment"), dict) else {}
        parts = attachment.get("content") if isinstance(attachment.get("content"), list) else []
        rows.extend(_transcript_row("claude", "message", "user", text, type="task-message", at=at)
                    for text in parts if isinstance(text, str) and _TASK_MESSAGE.match(text))
    elif typ == "system":
        text = record.get("content") if isinstance(record.get("content"), str) else ""
        rows.append(_transcript_row("claude", "message" if text else "engine", "system", text,
                         type=str(record.get("subtype") or typ), at=at))
    return rows or [_transcript_row("claude", "engine", "system", "", type=typ, at=at)]


def _codex_error(record: dict) -> str:
    error = record.get("error") if isinstance(record.get("error"), dict) else {}
    return _first_text(record, ("message",)) or _first_text(error, ("message",)) or json.dumps(record)


def _codex_rows(record: dict, worktree: str) -> list[dict]:
    """Rows for one event of a Codex thread. A command item carries its command and its output together."""
    typ = str(record.get("type") or "event")
    item = record.get("item") if isinstance(record.get("item"), dict) else {}
    item_type = str(item.get("type") or "")
    if typ in ("error", "turn.failed"):
        return [_transcript_row("codex", "error", "system", _codex_error(record), type=typ)]
    if not item_type or item_type == "reasoning":
        return [_transcript_row("codex", "engine", "system", "", type=typ)]
    item_id = str(item.get("id") or "") or None
    status = item.get("status") if isinstance(item.get("status"), str) else None
    if item_type == "agent_message":
        return [_transcript_row("codex", "message", "assistant", str(item.get("text") or ""), type=typ)]
    if item_type == "error":
        return [_transcript_row("codex", "error", "system", _first_text(item, ("message", "text")), type=typ)]
    if item_type == "command_execution":
        command = _shell_command(item.get("command"))
        return [_transcript_row("codex", "command", "assistant", command, type=typ, tool="command", summary=_one_line(command),
                     tool_use_id=item_id, status=status, output=str(item.get("aggregated_output") or ""),
                     error=item.get("exit_code") not in (None, 0))]
    if item_type == "file_change":
        changes = item.get("changes") if isinstance(item.get("changes"), list) else []
        lines = [f"{c.get('kind') or 'change'} {_relative(c.get('path'), worktree)}".rstrip()
                 for c in changes if isinstance(c, dict)]
        return [_transcript_row("codex", "file", "assistant", "\n".join(lines), type=typ, tool="file_change",
                     summary=_one_line(", ".join(lines)), tool_use_id=item_id, status=status)]
    if item_type == "mcp_tool_call":
        summary = ".".join(str(item.get(k) or "") for k in ("server", "tool") if item.get(k)) or item_type
    else:
        summary = _first_text(item, ("query", "text", "message", "command")) or item_type
    return [_transcript_row("codex", "tool", "assistant", _first_text(item, ("text", "output", "result", "message")), type=typ,
                 tool=item_type, summary=_one_line(summary), tool_use_id=item_id, status=status)]


def transcript_engine(task: dict) -> str:
    return str(task.get("l2_engine") or "claude")


def transcript_sources(task: dict, *, job_root: Path) -> list[tuple[Path, dict]]:
    """Task-selected session evidence and any owning turn, oldest first."""
    engine = transcript_engine(task)
    if engine == "codex":
        return [(path, S.read_json(path.parent / f"{path.name.split('.')[0]}.json", None) or {})
                for path in codex_turns(job_root, str(task.get("session_id") or ""))]
    path = _claude_path(str(task.get("session_id") or "")) if engine == "claude" else None
    return [(path, {})] if path and path.is_file() else []


def transcript_rows(engine: str, record: dict, worktree: str) -> list[dict]:
    """Project only public text/tools; the caller also uses this sanitized record for Raw events."""
    _hide_reasoning(record)
    if engine == "codex":
        rows = _codex_rows(record, worktree)
        for row in rows:
            row["at"] = record.get("timestamp")
        return rows
    if engine == "claude":
        return _claude_rows(record, worktree)
    return []


def activity_source(task: dict, *, job_root: Path) -> tuple[Path, dict] | None:
    """Only the current owned invocation can supply a current direction preview."""
    identity = str(task.get("agent_id") or "")
    if not re.fullmatch(r"[\w-]+", identity):
        return None
    paths = _codex_paths(job_root, identity)
    try:
        record = S.read_json(paths["record"], None)
    except (OSError, ValueError):
        return None
    if (not isinstance(record, dict) or record.get("id") != identity
            or record.get("engine") != transcript_engine(task)
            or record.get("session_id") != task.get("session_id")):
        return None
    return paths["stdout"], record


def session_context_source(task: dict) -> Path | None:
    """The selected native session can corroborate source time and inbox-hook attachments."""
    return _claude_path(str(task.get("session_id") or "")) if transcript_engine(task) == "claude" else None


def delivered_context(engine: str, record: dict) -> list[str]:
    """Positive native hook delivery evidence; ordinary text/tool output is never a receipt."""
    attachment = record.get("attachment")
    if (engine != "claude" or record.get("type") != "attachment" or not isinstance(attachment, dict)
            or attachment.get("type") != "hook_additional_context"):
        return []
    content = attachment.get("content")
    return [text for text in content if isinstance(text, str)] if isinstance(content, list) else []


def public_message_identity(engine: str, record: dict) -> str | None:
    message = record.get("message") if engine == "claude" else record.get("item")
    return str(message["id"]) if isinstance(message, dict) and message.get("id") else None


# Passive task accounting reads provider evidence only. Cursors contain offsets and numeric
# deduplication ledgers, never transcript text; callers persist one cursor per engine per task.
_TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens")
_TOKEN_ROLLOUT_INDEX: dict[str, dict] = {}


def _token_numbers(usage: dict, engine: str) -> dict:
    def number(key):
        value = usage.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

    if engine == "claude":
        reads, writes = number("cache_read_input_tokens"), number("cache_creation_input_tokens")
        plain = number("input_tokens")
        inputs = sum(v for v in (plain, reads, writes) if v is not None) if any(v is not None for v in (plain, reads, writes)) else None
        details = usage.get("output_tokens_details") or {}
        reasoning = details.get("thinking_tokens") if isinstance(details, dict) else None
    else:
        inputs, reads, writes = number("input_tokens"), number("cached_input_tokens"), number("cache_write_input_tokens")
        reasoning = number("reasoning_output_tokens")
    return {**dict(zip(_TOKEN_FIELDS, (inputs, number("output_tokens"), reads, writes,
                                     reasoning if isinstance(reasoning, int) and not isinstance(reasoning, bool) and reasoning >= 0 else None))),
            "incomplete": inputs is None or number("output_tokens") is None or
                          (engine == "claude" and (plain is None or reads is None or writes is None))}


def _token_max(old: dict, new: dict) -> dict:
    return {**{key: max(v for v in (old.get(key), new.get(key)) if v is not None)
               if old.get(key) is not None or new.get(key) is not None else None for key in _TOKEN_FIELDS},
            "incomplete": new.get("incomplete", False)}


def _token_sum(records: list[dict]) -> dict:
    # Retain observed lower bounds when some records omit a counter, without inventing zero.
    return {key: sum(row[key] for row in records if row.get(key) is not None) if any(row.get(key) is not None for row in records)
            else None for key in _TOKEN_FIELDS}


def _token_legacy_segments(row: dict) -> list[dict]:
    """Old snapshots may span turns. Only an observed reset starts another additive segment."""
    segments, previous = [], None
    for turn in row.get("turn_order", []):
        values = row["turns"][turn]
        first = row.get("turn_starts", {}).get(turn)
        reset = (previous is not None and first is not None and first < previous and
                 turn != "unidentified" and not turn.startswith("offset:"))
        if not segments or reset:
            segments.append(values)
        else:
            segments[-1] = _token_max(segments[-1], values)
        previous = values.get("input_tokens")
    return segments


def _token_rollouts(home: Path) -> tuple[dict, bool]:
    """Cache only rollout identity/parentage; at most 128 new headers per discovery pass."""
    cache = _TOKEN_ROLLOUT_INDEX.setdefault(str(home), {"at": 0, "paths": {}, "todo": []})
    if time.monotonic() - cache["at"] >= 30 or not cache["at"]:
        cache["at"] = time.monotonic()
        cache["todo"] = [str(p) for p in home.glob("sessions/*/*/*/rollout-*.jsonl")
                         if str(p) not in cache["paths"]]
    for name in cache["todo"][:128]:
        try:
            with Path(name).open("rb") as stream:
                raw = stream.readline(128 * 1024)
            event = json.loads(raw)
            meta = event.get("payload") or {}
            if event.get("type") != "session_meta":
                continue
            sid = meta.get("id") or meta.get("session_id")
            if not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", sid):
                continue
            source = meta.get("source") or {}
            subagent = source.get("subagent") if isinstance(source, dict) else None
            spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
            parent = meta.get("parent_thread_id") or (spawn.get("parent_thread_id") if isinstance(spawn, dict) else None)
            cache["paths"][name] = {"session_id": sid, "parent": parent if isinstance(parent, str) and parent else None,
                                    "forked": bool(meta.get("forked_from_id")),
                                    "fork_time": meta.get("timestamp"),
                                    "fork_ordinal": meta.get("subagent_history_start_ordinal")}
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    cache["todo"] = cache["todo"][128:]
    return cache["paths"], bool(cache["todo"])


def _token_read(path: Path, file: dict, budget: list[int]):
    """Yield complete new JSONL records; retain offsets through restarts and retried partial writes."""
    stat = path.stat()
    identity = [stat.st_dev, stat.st_ino]
    if file.get("identity") != identity or file.get("missing") or stat.st_size < file.get("offset", 0):
        if file.get("identity"):
            file["lost"] = True
        file.update(identity=identity, offset=0)
    with path.open("rb") as stream:
        stream.seek(file["offset"])
        while budget[0] > 0:
            start = stream.tell()
            raw = stream.readline(min(budget[0], 1024 * 1024) + 1)
            if not raw:
                break
            budget[0] -= len(raw)
            if not raw.endswith(b"\n"):
                file["pending"] = True
                # A huge transcript line must not prevent later numeric evidence being read.
                if len(raw) > 1024 * 1024:
                    file.update(offset=stream.tell(), skipping=True, lost=True)
                break
            file.update(offset=stream.tell(), pending=False)
            if file.pop("skipping", False):
                continue
            try:
                event = json.loads(raw)
                if isinstance(event, dict):
                    yield start, event
            except (ValueError, UnicodeDecodeError):
                file["lost"] = True
    file["pending"] = file.get("pending", False) or file["offset"] < stat.st_size


def observe_token_usage(engine: str, session_id: str, cursor: dict | None = None, *,
                        job_root: Path | None = None, home: Path | None = None,
                        max_bytes: int = 2 * 1024 * 1024) -> dict:
    """Incremental, task-scoped observations from local records, with no engine invocation.

    Share the returned JSON cursor across every owner identity of this engine within one task.
    Codex response records are request increments; legacy snapshots have version-dependent scope.
    Claude messages are repeated streaming snapshots, deduplicated by message id. Inclusive input
    is input+cache-read+cache-write for Claude, input alone for Codex; reasoning is an output subset.
    Native children require recorded parentage. Discovery cannot prove exhaustive helper coverage.
    """
    state = json.loads(json.dumps(cursor or {}))
    state.setdefault("owners", [])
    state.setdefault("sessions", {})
    state.setdefault("files", {})
    state.setdefault("records", {})
    if not re.fullmatch(r"[A-Za-z0-9_-]+", session_id or ""):
        return {"cursor": state, "sessions": [], "status": "unknown", "pending": False,
                "notes": ["Provider session identity is unavailable."]}
    if session_id not in state["owners"]:
        state["owners"].append(session_id)
    budget, pending, sources = [max_bytes], False, []
    notes = ["Only locally observed usage is counted; native helper coverage may be incomplete."]

    def register(sid, parent=None, **metadata):
        row = state["sessions"].setdefault(sid, {"parent_session_id": parent, "observed_at": None,
                                               "turns": {}, "notes": []})
        if parent:
            row["parent_session_id"] = parent
        row.update(metadata)
        return row

    register(session_id)
    try:
        if engine == "codex":
            root = home or _codex_home(codex_env())
            index, pending = _token_rollouts(root)
            wanted = set(state["owners"])
            while True:
                children = {meta["session_id"] for meta in index.values() if meta["parent"] in wanted}
                if children <= wanted:
                    break
                wanted |= children
            for name, meta in index.items():
                if meta["session_id"] in wanted:
                    sid = meta["session_id"]
                    register(sid, meta["parent"], parentage="thread",
                             **{key: meta[key] for key in ("forked", "fork_time", "fork_ordinal")})
                    sources.append((Path(name), sid, "rollout"))
        elif engine == "claude":
            root = home or config.HOME / ".claude"
            for sid in state["owners"]:
                for path in root.glob(f"projects/*/{sid}.jsonl"):
                    sources.append((path, sid, "messages"))
                for child in root.glob(f"projects/*/{sid}/subagents/agent-*.jsonl"):
                    child_id = f"{sid}/{child.stem}"
                    # The directory binds a helper to its owner, not necessarily its spawning helper.
                    register(child_id, sid, parentage="owner")
                    sources.append((child, child_id, "messages"))
        else:
            notes.append("This engine has no local token accounting adapter.")
        # Owned stdout survives archive and supplies a provider aggregate if transcript/rollout
        # evidence is missing. Worker metadata, never the transcript's cwd, binds it to the task.
        if job_root:
            for path in Path(job_root).glob("*.json"):
                record = S.read_json(path, {})
                sid = record.get("session_id") if isinstance(record, dict) else None
                if sid in state["owners"] and record.get("engine", "codex") == engine:
                    sources.append((path.with_suffix(".stdout.jsonl"), sid, "stdout"))

        # Revisit disappeared known files too, preserving their counts and exposing the gap.
        known = {str(path) for path, _, _ in sources}
        sources.extend((Path(name), file["session_id"], file["kind"])
                       for name, file in state["files"].items() if name not in known)
        for path, sid, kind in sources:
            row = register(sid)
            file = state["files"].setdefault(str(path), {"session_id": sid, "kind": kind})
            if budget[0] <= 0:
                pending = True
                break
            try:
                for offset, event in _token_read(path, file, budget):
                    at = event.get("timestamp")
                    payload = event.get("payload") or {}
                    values, key, turn = None, None, None
                    if engine == "claude" and kind == "messages" and event.get("type") == "assistant":
                        parent = row["parent_session_id"] or sid
                        identity = event.get("sessionId") or event.get("session_id")
                        if identity != parent or bool(event.get("isSidechain")) != bool(row["parent_session_id"]):
                            continue
                        if row["parent_session_id"] and event.get("agentId") != path.stem.removeprefix("agent-"):
                            continue
                        message = event.get("message") or {}
                        usage = message.get("usage") or {}
                        if not usage or not message.get("id") or message.get("model") == "<synthetic>":
                            continue
                        values = _token_numbers(usage, engine)
                        if not any(values.get(key) for key in _TOKEN_FIELDS):  # synthetic quota/error records are not an observed zero
                            continue
                        key = f"message:{message['id']}"
                    elif engine == "codex" and kind == "rollout":
                        if event.get("type") == "token_usage_record":
                            if payload.get("thread_id") != sid or not payload.get("response_id"):
                                continue  # copied fork history belongs to its recorded thread
                            values = _token_numbers(payload.get("usage") or {}, engine)
                            key, turn = f"response:{payload['response_id']}", payload.get("turn_id")
                        elif event.get("type") in ("event_msg", "turn_context"):
                            if row.get("forked"):
                                boundary = row.get("fork_ordinal")
                                if isinstance(boundary, int) and event.get("ordinal", -1) < boundary:
                                    continue
                                if not isinstance(boundary, int) and (not at or not row.get("fork_time") or at < row["fork_time"]):
                                    continue
                            if payload.get("turn_id"):
                                file["turn"] = payload["turn_id"]
                            elif payload.get("type") == "task_started":
                                file["turn"] = f"offset:{offset}"
                            if payload.get("type") == "token_count":
                                usage = (payload.get("info") or {}).get("total_token_usage")
                                if not isinstance(usage, dict):
                                    continue
                                turn = file.get("turn", "unidentified")
                                values = _token_numbers(usage, engine)
                                old = row["turns"].get(turn, {})
                                if turn not in row.setdefault("turn_order", []):
                                    row["turn_order"].append(turn)
                                row.setdefault("turn_starts", {}).setdefault(turn, values["input_tokens"])
                                if old.get("input_tokens") is not None and values["input_tokens"] is not None and values["input_tokens"] < old["input_tokens"]:
                                    row["notes"] = list(set(row["notes"] + ["A replayed or reset counter cannot be fully reconciled."]))
                                row["turns"][turn] = _token_max(old, values)
                    elif kind == "stdout" and ((engine == "codex" and event.get("type") == "turn.completed") or
                                               (engine == "claude" and event.get("type") == "result" and event.get("session_id") == sid)):
                        values = _token_numbers(event.get("usage") or {}, engine)
                        if not any(values.get(key) for key in _TOKEN_FIELDS):
                            continue
                        row.setdefault("stdout", {})[path.name] = _token_max(row.get("stdout", {}).get(path.name, {}), values)
                        at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
                    if key and values:
                        old = state["records"].get(key)
                        # A replacement session may repeat inherited message ids; ownership stays
                        # with the first recorded session and only new numeric evidence is added.
                        state["records"][key] = {"session_id": old["session_id"] if old else sid,
                                                  "values": _token_max(old["values"] if old else {}, values)}
                    if values and at:
                        row["observed_at"] = max(row["observed_at"] or "", at)
                if file.get("lost"):
                    row["notes"] = list(set(row["notes"] + ["Some local records were missing, replaced, or unreadable."]))
                pending |= file.get("pending", False)
                file["missing"] = False
            except (OSError, ValueError, TypeError, AttributeError):
                file["missing"] = True
    except (OSError, ValueError, TypeError, AttributeError):
        notes.append("Some local usage evidence could not be read.")

    score = lambda values: (values.get("input_tokens") or 0) + (values.get("output_tokens") or 0)
    visited, helpers = set(), []

    def project(sid):
        # A subtree's lower bound is the larger of its ambiguous provider aggregate and its
        # disjoint own responses plus child bounds. Never add an aggregate to its children.
        visited.add(sid)
        row = state["sessions"][sid]
        children = [project(child) for child, child_row in state["sessions"].items()
                    if child_row["parent_session_id"] == sid and child not in visited]
        records = [entry["values"] for entry in state["records"].values() if entry["session_id"] == sid]
        own = _token_sum(records)
        disjoint = _token_sum([own, *(bound for bound, _ in children)])
        legacy = _token_legacy_segments(row)
        candidates = [_token_sum(legacy), *row.get("stdout", {}).values()]
        row_notes = list(row["notes"])
        if row.get("forked"):
            candidates = []
            if legacy or row.get("stdout"):
                row_notes.append("Inherited aggregate usage has no reliable fork baseline and is excluded.")
        aggregate = max(candidates, key=score) if candidates else {}
        use_aggregate = score(aggregate) > score(disjoint)
        role = "provider" if use_aggregate else "owner" if sid in state["owners"] else "delegated"
        values = {key: aggregate.get(key) for key in _TOKEN_FIELDS} if use_aggregate else own
        if use_aggregate:
            row_notes.append("Provider aggregate; helper usage cannot be split. Linked helper totals are excluded to avoid overlap.")
            row_notes.append("Overlapping usage sources retain the largest observed lower bound, not a sum.")
        if legacy and use_aggregate:
            row_notes.append("Legacy counter scope varies: cumulative snapshots are not summed without an observed reset.")
        if any(record.get("incomplete") for record in records + legacy) or (use_aggregate and aggregate.get("incomplete")):
            row_notes.append("Some counters are missing; available token counts are a lower bound.")
        total = score(values) if any(values.get(key) is not None for key in ("input_tokens", "output_tokens")) else None
        files = [file for file in state["files"].values() if file["session_id"] == sid]
        if not files or any(file.get("missing") for file in files):
            row_notes.append("Local usage records are unavailable; retained counters may be incomplete.")
        public = {"session_id": sid, "parent_session_id": row["parent_session_id"], "role": role,
                  **values, "total_tokens": total, "observed_at": row["observed_at"],
                  "status": "unknown" if total is None and not records else
                            "partial" if row_notes or total is None else "observed", "notes": row_notes}
        if sid not in state["owners"]:
            ancestor, chain, depth = sid, set(), 0
            while ancestor not in state["owners"] and ancestor in state["sessions"] and ancestor not in chain:
                chain.add(ancestor)
                link = state["sessions"][ancestor]
                depth = depth + 1 if depth is not None and link.get("parentage") == "thread" else None
                ancestor = link["parent_session_id"]
            if ancestor in state["owners"]:
                own_total = score(own) if any(own.get(key) is not None for key in ("input_tokens", "output_tokens")) else None
                helpers.append({**public, **own, "role": "delegated", "total_tokens": own_total,
                                "status": "unknown" if own_total is None else public["status"],
                                "owner_session_id": ancestor, "depth": depth,
                                "parentage": row.get("parentage"),
                                "provider_total_tokens": score(aggregate) if score(aggregate) else None})
        return (values, [public]) if use_aggregate else (disjoint, [public, *(item for _, rows in children for item in rows)])

    sessions = []
    roots = [sid for sid, row in state["sessions"].items() if row["parent_session_id"] not in state["sessions"]]
    for sid in [*roots, *state["owners"], *state["sessions"]]:
        if sid not in visited:
            _, rows = project(sid)
            sessions.extend(rows)
    # An unconditional owner registration is not evidence of zero helpers. Retained native file
    # observations can establish an empty observed set, but discovery never proves exhaustive spawns.
    helper_status = "partial" if helpers or any(file.get("kind") != "stdout" and file.get("offset", 0) > 0
                                                for file in state["files"].values()) else "unknown"
    return {"cursor": state, "sessions": sessions, "helpers": helpers, "helper_status": helper_status,
            "pending": bool(pending),
            "status": "partial" if any(row["total_tokens"] is not None for row in sessions) else "unknown", "notes": notes}


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
    cross altd's fixed protocol through a stdio MCP adapter; the model cannot write Altitude state, reach mutating HTTP APIs,
    use authenticated GitHub directly, connect to the user bus, or write a checkout.
    """
    profile = "altitude-l3"
    bus = f"/run/user/{os.getuid()}/bus"
    from .l3 import verb_socket_path
    broker = verb_socket_path(project).resolve()
    rules = {":root": "read", str(Path(cwd).resolve()): "write",
             str(config.project_path(project).resolve()): "read", bus: "deny"}
    filesystem = "{" + ",".join(f"{json.dumps(path)}={json.dumps(access)}"
                                   for path, access in rules.items()) + "}"
    # Sept 7 coordinator outage: Linux proxy-mode seccomp denies socket(AF_UNIX), and the proxy's
    # Unix allowlist is macOS-only. MCP stdio is the supported boundary; its adapter has no shell verb.
    adapter = (f"import sys; sys.path.insert(0, {str(config.SOURCE.resolve())!r}); "
               f"from altitude.engines import codex_l3_mcp; codex_l3_mcp({str(broker)!r})")
    return [f'default_permissions="{profile}"', f'permissions.{profile}.extends=":read-only"',
            f"permissions.{profile}.filesystem={filesystem}",
            f"permissions.{profile}.network.enabled=false", 'approval_policy="never"',
            "mcp_servers.altitude.command=" + json.dumps(sys.executable),
            "mcp_servers.altitude.args=" + json.dumps(["-I", "-c", adapter]),
            "mcp_servers.altitude.required=true", "mcp_servers.altitude.tool_timeout_sec=150",
            'mcp_servers.altitude.tools.coordinator.approval_mode="approve"',
            "developer_instructions=" + json.dumps("Use the altitude coordinator MCP tool for every alt verb "
                "and gh/service read. Supply argument arrays and stdin text, not shell commands. "
                "The shell sandbox cannot connect to the broker. Repository and service writes remain denied.")]


def codex_l3_mcp(broker: str) -> None:
    """Stdio MCP transport only: altd's socket fixes project/actor and validates every request.

    Loaded from the protected deployment checkout with isolated Python, never from the writable runtime.
    """
    tool = {"name": "coordinator", "description": "Run authorized Altitude coordinator commands here. "
            "For alt use kind=alt, args excluding alt, stdin for body text; for gh reads use kind=gh, "
            "args excluding gh; for service status use kind=service, unit=altitude.service. "
            "Shell alt/gh/systemctl cannot reach the broker from the sandbox.",
            "inputSchema": {"type": "object", "required": ["kind"], "additionalProperties": False,
                            "properties": {"kind": {"enum": ["alt", "gh", "service"]},
                                           "args": {"type": "array", "items": {"type": "string"}},
                                           "stdin": {"type": "string"}, "unit": {"type": "string"}}}}
    for line in sys.stdin:
        request = json.loads(line)
        if "id" not in request:
            continue
        try:
            method, params = request["method"], request.get("params", {})
            if method == "initialize":
                result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                          "serverInfo": {"name": "altitude", "version": "1"}}
            elif method == "tools/list":
                result = {"tools": [tool]}
            elif method == "tools/call" and params.get("name") == tool["name"]:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                    client.settimeout(130)
                    client.connect(broker)
                    client.sendall((json.dumps(params["arguments"]) + "\n").encode())
                    client.shutdown(socket.SHUT_WR)
                    with client.makefile("rb") as stream:
                        response = json.load(stream)
                result = {"content": [{"type": "text", "text": json.dumps(response)}],
                          "isError": bool(response.get("error") or response.get("returncode"))}
            else:
                raise ValueError("unsupported coordinator MCP request")
            reply = {"result": result}
        except Exception as exc:
            reply = {"error": {"code": -32603, "message": str(exc)}}
        print(json.dumps({"jsonrpc": "2.0", "id": request["id"], **reply}), flush=True)


def _unit_active(unit: str) -> bool:
    if not unit:
        raise RuntimeError("Worker unit identity is unavailable")
    p = subprocess.run([SYSTEMCTL_BIN, "--user", "is-active", unit], capture_output=True, text=True, timeout=30,
                       env=codex_env(retain_user_bus=True))
    state = (p.stdout or "").strip()
    if p.returncode == 4 and state == "inactive":  # A collected transient unit is no longer running.
        return False
    if p.returncode in (0, 3):
        if state in ("active", "activating", "deactivating", "reloading", "refreshing", "maintenance"):
            return True
        if state in ("inactive", "failed"):
            return False
    raise RuntimeError("Worker unit status is unavailable")


def _worker_events(path: Path, engine: str) -> list[dict]:
    events = _codex_events(path)
    if engine == "codex":
        return events
    # I-20260907-171446: read the owned CLI's actual result/error, not the shared daemon registry.
    return [{"type": "thread.started", "thread_id": e.get("session_id"), "model": e.get("model")}
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
    process_alive = proc is not None and proc.poll() is None
    alive = process_alive or _unit_active(str(record.get("unit") or ""))
    if proc is not None and not process_alive:
        _codex_processes.pop(worker_id, None)
    completed = any(event.get("type") == "turn.completed" for event in events)
    failed = next((event for event in reversed(events) if event.get("type") in ("turn.failed", "error")), None)
    if alive:
        state, status = "working", "busy"
    elif record.get("stopped"):
        state, status = "stopped", "exited"
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
    row = {"id": worker_id, "sessionId": _codex_thread(events) or record.get("session_id"),
            "name": record.get("name"), "pid": record.get("pid"), "unit": record.get("unit"),
            "state": state, "status": status, "detail": detail, "usage": _codex_usage(events),
            "detail_at": max((paths[k].stat().st_mtime for k in ("stdout", "stderr") if paths[k].exists()),
                             default=paths["record"].stat().st_mtime),
            "startedAt": record.get("started_at"), "engine": engine,
            "resumed": bool(record.get("resume")),
            "input_delivered": record.get("input_delivered") is True,
            "engine_model": next((e["model"] for e in events if e.get("model")), record.get("engine_model")),
            "engine_reasoning_effort": record.get("engine_reasoning_effort")}
    # Auto may retry only a settled rejection with the entire turn proving no assistant/tool activity.
    try:
        stdout = paths["stdout"].read_text(errors="replace")
        row.update(safe_to_retry=not alive and _safe_output(engine, stdout),
                   rejection=rejection(engine, {"detail": detail, "raw_stdout": stdout}, record.get("launch_model")))
    except OSError:
        row.update(safe_to_retry=False, rejection=None)
    return row


def codex_bg(name: str, prompt: str, *, cwd: Path, job_root: Path, resume: str | None = None,
             persona: Path | None = None, model: str | None = None, extra_env: dict | None = None,
             start_timeout: float = 15.0) -> dict:
    return _start_worker("codex", name, prompt, cwd=cwd, job_root=job_root, resume=resume,
                         persona=persona, model=model, extra_env=extra_env, start_timeout=start_timeout)


def _start_worker(engine: str, name: str, prompt: str, *, cwd: Path, job_root: Path, resume: str | None = None,
                  persona: Path | None = None, model: str | None = None, extra_env: dict | None = None,
                  settings: Path | None = None, start_timeout: float = 15.0, effort: str | None = None,
                  images: list[dict] | tuple = ()) -> dict:
    """One foreground CLI per transient unit; both engines persist identity and output for adoption."""
    config.task_effort(engine, effort)
    prompt = (repository_rule_prompt(cwd) +
              f'[altitude] Begin each native helper assignment with: "You are an L1 helper. Read '
              f'`{config.PERSONAS / "l1.md"}` before working. Your assigned repository is '
              f'`{cwd.resolve()}`." Then give the task-specific assignment; do not copy the persona '
              'into the brief or rely on inherited owner instructions to select the helper role.\n\n' + prompt)
    image_args, prompt = _image_input(engine, prompt, images)
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
               "--permission-mode", "auto", "--settings", str(settings or claude_settings()), *image_args]
        if persona:
            cmd += ["--append-system-prompt-file", str(persona)]
        if model:
            cmd += ["--model", model]
        if resume:
            cmd += ["--resume", resume]
        text = prompt
    elif engine == "codex":
        cmd = [config.CODEX_BIN, "exec", *(["resume"] if resume else []), *image_args, "--json", "--strict-config",
               "--skip-git-repo-check", *([] if resume else ["-C", str(cwd)])]
        if model:
            cmd += ["-m", model]
        if effort is not None:
            cmd += ["-c", f'model_reasoning_effort="{effort}"']
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
              "session_id": resume, "cwd": str(cwd), "resume": bool(resume), "stopped": None,
              "launch_model": model, "launch_effort": effort, "input_delivered": False}
    S.write_json(paths["record"], record)
    try:
        with open(paths["stdout"], "ab", buffering=0) as out, open(paths["stderr"], "ab", buffering=0) as err:
            proc = subprocess.Popen(_codex_service_command(unit, cmd, codex_env(extra_env)), cwd=str(cwd),
                                    stdin=subprocess.PIPE, stdout=out, stderr=err,
                                    env=codex_env(extra_env, retain_user_bus=True), start_new_session=True)
        input_written = False
        try:
            data = text.encode("utf-8")
            written = proc.stdin.write(data)
            proc.stdin.close()
            input_written = written == len(data)
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
        if not input_written or not thread_id or (resume and thread_id != resume):
            codex_stop(worker_id, job_root=root)
            row = codex_worker(worker_id, job_root=root) or {}
            detail = ("worker input handoff failed" if not input_written else
                      f"resumed a different {engine.title()} thread" if thread_id else
                      row.get("detail") or "no session initialization event")
            return {"stdout": "", "stderr": str(detail), "returncode": 1, "agent": row,
                    "rejection": row.get("rejection"), "safe_to_retry": not thread_id and row.get("safe_to_retry", False)}
        record["session_id"] = thread_id
        record["input_delivered"] = True
        S.write_json(paths["record"], record)
        row = codex_worker(worker_id, job_root=root)
        return {"stdout": "", "stderr": "", "returncode": 0, "agent": row,
                "rejection": row.get("rejection"), "safe_to_retry": row.get("safe_to_retry", False)}
    except BaseException as exc:
        # I-20260907-171446: startup must not leave an unbound worker that a retry duplicates.
        try:
            codex_stop(worker_id, job_root=root)
        except Exception as stop_error:
            raise RuntimeError(f"worker {worker_id}: {exc}; stop failed: {stop_error}") from exc
        raise


def _owned_unit(record: dict, worker_id: str) -> str:
    engine = record.get("engine") if isinstance(record, dict) else None
    expected = _codex_unit(worker_id) if engine == "codex" else _claude_unit(worker_id) if engine == "claude" else None
    if not expected or record.get("id") != worker_id or record.get("unit") != expected:
        raise RuntimeError("Worker ownership record is unavailable; stop is unconfirmed")
    return expected


def codex_stop(worker_id: str, *, job_root: Path) -> str:
    """Stop the worker's transient unit; `KillMode=control-group` takes every descendant with it."""
    paths = _codex_paths(job_root, worker_id)
    record = S.read_json(paths["record"], None)
    unit = _owned_unit(record, worker_id)
    subprocess.run([SYSTEMCTL_BIN, "--user", "stop", unit], capture_output=True, text=True, timeout=120,
                   env=codex_env(retain_user_bus=True))
    proc = _codex_processes.get(worker_id)
    if proc is not None:
        try:
            proc.wait(timeout=10)
        except (subprocess.TimeoutExpired, OSError):
            proc.kill()
            proc.wait(timeout=10)
    if _unit_active(unit):
        raise RuntimeError(f"Codex worker {worker_id} is still running after stop")
    _codex_processes.pop(worker_id, None)
    record["stopped"] = record.get("stopped") or datetime.now(timezone.utc).isoformat(timespec="seconds")
    S.write_json(paths["record"], record)
    return "Codex worker stopped"


def start_l2(engine: str, name: str, prompt: str, *, cwd: Path, persona: Path,
             model: str | None, settings: Path, extra_env: dict, job_root: Path, effort: str | None = None,
             images: list[dict] | tuple = ()) -> dict:
    return _start_worker(engine, name, prompt, cwd=cwd, persona=persona, model=model, settings=settings,
                         extra_env=extra_env, job_root=job_root, effort=effort, images=images)


def resume_l2(engine: str, name: str, session_id: str, prompt: str, *, cwd: Path, persona: Path,
              model: str | None, settings: Path, extra_env: dict, job_root: Path, effort: str | None = None,
             images: list[dict] | tuple = ()) -> dict:
    return _start_worker(engine, name, prompt, cwd=cwd, resume=session_id, persona=persona, model=model,
                         settings=settings, extra_env=extra_env, job_root=job_root, effort=effort, images=images)


def stop_l2_worker(engine: str, worker_id: str, *, job_root: Path) -> str:
    if engine == "claude" and not _codex_paths(job_root, worker_id)["record"].exists():
        # I-20260907-171446: stop only the adopted job; its absence from a registry is not termination proof.
        job = S.read_json(JOBS_DIR / worker_id / "state.json", None)
        if not isinstance(job, dict) or not isinstance(job.get("name"), str) or not job["name"].strip():
            raise RuntimeError("Worker ownership record is unavailable; stop is unconfirmed")
        note = claude_stop(worker_id)
        if _unit_active(_claude_unit(job["name"])):
            raise RuntimeError(f"Worker {worker_id} is still running after stop")
        return note
    return codex_stop(worker_id, job_root=job_root)


def worker_termination(task: dict, *, job_root: Path) -> bool | None:
    """Read-only termination evidence: unknown ownership/status never means stopped."""
    identity = task.get("agent_id")
    if not identity:
        return None
    try:
        record = S.read_json(_codex_paths(job_root, identity)["record"], None)
        if record is not None:
            if (not isinstance(record, dict) or record.get("id") != identity
                    or record.get("engine") != transcript_engine(task)
                    or record.get("session_id") != task.get("session_id")):
                return None
            unit = _owned_unit(record, identity)
        elif transcript_engine(task) == "claude":
            job = S.read_json(JOBS_DIR / identity / "state.json", None)
            if not isinstance(job, dict) or not isinstance(job.get("name"), str) or not job["name"].strip():
                return None
            unit = _claude_unit(job["name"])
        else:
            return None
        return not _unit_active(unit)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        return None


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
               sandbox_settings: list[str] | None = None, ignore_user_config: bool = False, on_session=None,
               durable_timeout: bool = False, images: list[dict] | tuple = ()) -> dict:
    """One synchronous Codex turn (L3) in Codex's own workspace-write sandbox, prompt on stdin (verified with
    codex 0.152). `codex exec resume <thread> -` continues the thread. The transient unit is the one workers use,
    so altd's `NoNewPrivileges` hardening never reaches the nested bwrap, and a timeout stops the whole tree."""
    image_args, prompt = _image_input("codex", prompt, images)
    cmd = [config.CODEX_BIN, "exec", *(["resume"] if resume else []), *image_args, "--json", "--strict-config",
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
    proc = subprocess.Popen(_codex_service_command(unit, cmd, codex_env(extra_env),
                            **({"runtime_max": timeout} if durable_timeout else {})), cwd=str(cwd),
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
    failure = next((_event_error("codex", event) for event in reversed(events)
                    if event.get("type") == "turn.failed" or
                    (proc.returncode != 0 and event.get("type") == "error")), None)
    result = {"text": (messages[-1] if messages else "").strip(), "returncode": proc.returncode,
            "usage": _codex_usage(events), "session_id": thread or resume, "reported_session_id": thread,
            "tools": tools, **metadata,
            "error": (failure if isinstance(failure, str) else json.dumps(failure))[:500] if failure else
                     None if proc.returncode == 0 else (stderr or "").strip()[:500],
            "raw_stdout": stdout or "", "raw_stderr": stderr or "",
            "raw_stdout_truncated": False, "raw_stderr_truncated": False}
    result.update(safe_to_retry=_safe_output("codex", stdout or ""), rejection=rejection("codex", result, model))
    limited = usage_limit_in(result.get("error"))
    if limited:
        result["limited"] = limited
    return result


def context_percent(context_tokens: int, engine: str = "claude") -> float:
    return round(100.0 * context_tokens / config.CONTEXT_LINES[engine][2], 1)


def context_state(pct: float | None, engine: str = "claude") -> str:
    """Return ``ok``, ``warn``, or ``act`` against the engine's configured lines."""
    if pct is None:
        return "unknown"
    warn, act, _ = config.CONTEXT_LINES[engine]
    return "act" if pct >= act * 100 else "warn" if pct >= warn * 100 else "ok"
