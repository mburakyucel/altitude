"""The L3 coordinator: one serialized turn, with a resumable session per provider."""
from __future__ import annotations
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config, engines, route, state as S, transcript

_locks: dict[str, threading.Lock] = {}
_active: dict[str, dict] = {}
#: Slugs of the tasks each running turn created through the daemon's `alt task new`, by turn id; the
#: turn's assistant row carries them as `tasks` (SPEC.md §5.2 note 4) and the entry goes with the turn.
_created: dict[str, list[str]] = {}
_lifecycle_guards: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_turn_local = threading.local()

L3_CONFINEMENT_VERSION = 1
L3_TOOLS = "Read,Grep,Glob,Bash"
ALLOWED_TOOLS = engines.L3_ALLOWED_TOOLS


def _write_executable(path: Path, text: str) -> None:
    if not path.exists() or path.read_text() != text:
        S.atomic_write(path, text)
    path.chmod(0o700)


def verb_socket_path(project: str) -> Path:
    """One capability socket per project; the pathname, not model-supplied JSON, binds its authority."""
    name = hashlib.sha256(project.encode()).hexdigest()[:20]
    return config.ROOT / "l3-verbs" / f"{name}.sock"


def _remove_runtime(runtime: Path) -> None:
    """Remove only the daemon-created per-turn directory, never a path an old turn can retarget."""
    try:
        if runtime.is_symlink():
            runtime.unlink()
        else:
            shutil.rmtree(runtime)
    except FileNotFoundError:
        pass


def _l3_runtime(project: str, engine: str) -> Path:
    """A disposable cwd with narrow read shims; the deployment checkout is never a working directory.

    Claude's deny-by-default permission rules approve only these command names. The shims keep variable Git and
    journal arguments read-only while still resolving them against the project named in the turn header. Codex has
    the same cwd and shims in addition to its native filesystem sandbox.
    """
    parent = config.project_dir(project)
    parent.mkdir(parents=True, exist_ok=True)
    # I-20260903-075410: a fresh unpredictable directory is created by altd after the previous turn exits.
    # Reusing a model-writable pathname would let one turn replace it with a checkout symlink for the next.
    runtime = Path(tempfile.mkdtemp(prefix=f"l3-{engine}-", dir=parent))
    bindir = runtime / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    repo = config.project_path(project).resolve()
    real_git = shutil.which("git") or "/usr/bin/git"
    real_journalctl = shutil.which("journalctl") or "/usr/bin/journalctl"
    python = sys.executable
    _write_executable(bindir / "git", f'''#!{python}
import os, sys
args = sys.argv[1:]
allowed = bool(args) and args[0] in ("log", "diff", "show")
if args and args[0] in ("diff", "show"):
    allowed = allowed and any(arg == "--stat" or arg.startswith("--stat=") for arg in args[1:])
blocked = any(arg == "-o" or arg.startswith(("--output", "--ext-diff", "--textconv")) for arg in args[1:])
if not allowed or blocked:
    print("git: L3 checkout access is read-only; use log, diff --stat, or show --stat", file=sys.stderr)
    raise SystemExit(77)
os.execv({json.dumps(real_git)}, [{json.dumps(real_git)}, "--no-pager", "-c", "diff.external=", "-C",
         {json.dumps(str(repo))}, *args])
''')
    _write_executable(bindir / "journalctl", f'''#!{python}
import os, re, sys
args = sys.argv[1:]
unit = args[2] if len(args) >= 3 and args[:2] == ["--user", "-u"] else ""
rest = args[3:]
safe = {{"--no-pager", "-r", "--reverse", "-f", "--follow"}}
pairs = {{"-n", "--lines", "--since", "--until", "-o", "--output"}}
i = 0
while i < len(rest):
    if rest[i] in safe:
        i += 1
    elif rest[i] in pairs and i + 1 < len(rest):
        i += 2
    else:
        print("journalctl: L3 may only read the altitude user journal", file=sys.stderr)
        raise SystemExit(77)
if not re.fullmatch(r"altitude(?:[-@.][A-Za-z0-9_.@-]+)*", unit):
    print("journalctl: L3 may only read the altitude user journal", file=sys.stderr)
    raise SystemExit(77)
os.execv({json.dumps(real_journalctl)}, [{json.dumps(real_journalctl)}, *args])
''')
    broker = verb_socket_path(project).resolve()
    _write_executable(bindir / "alt", f'''#!{python}
import json, socket, sys
request = {{"kind": "alt", "args": sys.argv[1:],
           "stdin": sys.stdin.read(2 << 20)}}
try:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(130)
        client.connect({json.dumps(str(broker))})
        client.sendall((json.dumps(request) + "\\n").encode())
        client.shutdown(socket.SHUT_WR)
        data = bytearray()
        while chunk := client.recv(65536):
            data.extend(chunk)
    response = json.loads(data)
except Exception as exc:
    print(f"alt: altd verb broker unavailable: {{exc}}", file=sys.stderr)
    raise SystemExit(1)
if response.get("error"):
    print("alt: " + str(response["error"]), file=sys.stderr)
    raise SystemExit(1)
sys.stdout.write(str(response.get("stdout") or ""))
sys.stderr.write(str(response.get("stderr") or ""))
raise SystemExit(int(response.get("returncode") or 0))
''')
    _write_executable(bindir / "gh", f'''#!{python}
import json, socket, sys
request = {{"kind": "gh", "args": sys.argv[1:]}}
try:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(130)
        client.connect({json.dumps(str(broker))})
        client.sendall((json.dumps(request) + "\\n").encode())
        client.shutdown(socket.SHUT_WR)
        data = bytearray()
        while chunk := client.recv(65536):
            data.extend(chunk)
    response = json.loads(data)
except Exception as exc:
    print(f"gh: altd verb broker unavailable: {{exc}}", file=sys.stderr)
    raise SystemExit(1)
if response.get("error"):
    print("gh: " + str(response["error"]), file=sys.stderr)
    raise SystemExit(1)
sys.stdout.write(str(response.get("stdout") or ""))
sys.stderr.write(str(response.get("stderr") or ""))
raise SystemExit(int(response.get("returncode") or 0))
''')
    _write_executable(bindir / "systemctl", f'''#!{python}
import json, re, socket, sys
args = sys.argv[1:]
rest = [arg for arg in args[3:] if arg != "--no-pager"]
unit = args[2] if len(args) >= 3 else ""
if args[:2] != ["--user", "status"] or rest or not re.fullmatch(r"altitude(?:[-@.][A-Za-z0-9_.@-]+)*", unit):
    print("systemctl: L3 may only read altitude user-service status", file=sys.stderr)
    raise SystemExit(77)
try:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(20)
        client.connect({json.dumps(str(broker))})
        request = {{"kind": "service", "unit": unit}}
        client.sendall((json.dumps(request) + "\\n").encode())
        client.shutdown(socket.SHUT_WR)
        data = bytearray()
        while chunk := client.recv(65536):
            data.extend(chunk)
    record = json.loads(data)
except Exception as exc:
    print(f"systemctl: altd verb broker unavailable: {{exc}}", file=sys.stderr)
    raise SystemExit(1)
if record.get("error"):
    print("systemctl: " + str(record["error"]), file=sys.stderr)
    raise SystemExit(1)
print(f"{{unit}}: {{record.get('state') or '?'}}/{{record.get('substate') or '?'}} PID {{record.get('pid') or '-'}}")
''')
    return runtime


def _l3_env(project: str, runtime: Path) -> dict[str, str]:
    env = {"ALTITUDE_ACTOR": "l3", "ALTITUDE_PROJECT": project, "ALTITUDE_HOME": str(config.ROOT),
           "PATH": str(runtime / "bin") + os.pathsep + engines.clean_env()["PATH"]}
    remote = subprocess.run([shutil.which("git") or "git", "remote", "get-url", "origin"],
                            cwd=str(config.project_path(project)), capture_output=True, text=True, timeout=15)
    match = re.search(r"github\.com[/:]([^/]+/[^/]+?)(?:\.git)?$", remote.stdout.strip()) if not remote.returncode else None
    if match:
        env["GH_REPO"] = match.group(1)
    return env


def lock(project: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(project, threading.Lock())


def _lifecycle_guard(project: str) -> threading.Lock:
    with _locks_guard:
        return _lifecycle_guards.setdefault(project, threading.Lock())


def info_path(project: str) -> Path:
    return config.project_dir(project) / "l3.json"


def info(project: str) -> dict:
    return S.read_json(info_path(project), {}) or {}


def save_info(project: str, data: dict) -> None:
    S.write_json(info_path(project), data)


def chat_log(project: str, role: str, text: str, **meta) -> None:
    path = config.project_dir(project) / "chat.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as stream:
        stream.write(json.dumps({"at": S.now(), "role": role, "text": text, **meta}, sort_keys=True) + "\n")


def chat_history(project: str, limit: int | None = 60) -> list[dict]:
    path = config.project_dir(project) / "chat.jsonl"
    if not path.exists():
        return []
    result = []
    lines = path.read_text().splitlines()
    for line in (lines[-limit:] if limit is not None else lines):
        try:
            result.append(json.loads(line))
        except ValueError:
            pass
    return result


def _tool_log(items: list) -> list[dict]:
    """The bounded tool evidence stored in chat.jsonl, including shell text."""
    out = []
    for item in items[:40]:
        if isinstance(item, dict):
            entry = {"name": str(item.get("name") or "tool")}
            if item.get("command") is not None:
                entry["command"] = transcript._shell_command(item["command"])[:200]
        else:  # legacy engine results carried only a tool name
            entry = {"name": str(item or "tool")}
        out.append(entry)
    return out


def _command_verb(command: str) -> tuple[str, bool]:
    words = command.strip().split()
    if not words or words[0] != "alt":
        return (words[0] if words else "shell"), True
    i = 1
    while i < len(words) and words[i].startswith("-"):
        i += 2 if words[i] in ("--project", "-p") else 1
    if i >= len(words):
        return "alt", False
    verb = f"alt {words[i]}"
    if words[i] in ("task", "incident", "l3", "project") and i + 1 < len(words):
        verb += f" {words[i + 1]}"
    return verb, False


def tool_summary(project: str, days: int = 7) -> dict:
    """Commands recorded on recent L3 assistant turns, grouped with ad-hoc verbs first."""
    days = max(0, int(days))
    since = datetime.now(timezone.utc) - timedelta(days=days)
    counts: dict[tuple[bool, str], Counter] = {}
    for row in chat_history(project, None):
        try:
            at = datetime.fromisoformat(str(row.get("at") or ""))
        except ValueError:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if at < since or row.get("role") != "assistant":
            continue
        for tool in row.get("tools") or []:
            command = tool.get("command") if isinstance(tool, dict) else None
            if not command:
                continue
            verb, ad_hoc = _command_verb(str(command))
            counts.setdefault((ad_hoc, verb), Counter())[str(command)] += 1
    groups = [{"verb": verb, "ad_hoc": ad_hoc, "count": sum(commands.values()),
               "commands": [{"command": command, "count": count}
                            for command, count in commands.most_common()]}
              for (ad_hoc, verb), commands in sorted(
                  counts.items(), key=lambda item: (not item[0][0], -sum(item[1].values()), item[0][1]))]
    return {"project": project, "days": days, "since": since.isoformat(timespec="seconds"), "groups": groups}


def busy(project: str) -> bool:
    return lock(project).locked()


def active(project: str) -> dict | None:
    """The minimal public identity of this process's running turn, never its private prompt."""
    with _lifecycle_guard(project):
        turn = _active.get(project)
        return dict(turn) if turn else None


def note_task(project: str, slug: str) -> bool:
    """Record that the project's running L3 turn created `slug`; false when no turn is running (a task
    created from the CLI outside a turn belongs to no chat row)."""
    with _lifecycle_guard(project):
        turn = _active.get(project)
        if not turn:
            return False
        _created.setdefault(turn["id"], []).append(slug)
        return True


def _created_meta(project: str, turn_id: str) -> dict:
    """The `tasks` field for the assistant row of `turn_id`, taken once; empty when it created none."""
    with _lifecycle_guard(project):
        slugs = _created.pop(turn_id, None)
    return {"tasks": slugs} if slugs else {}


def chat_state(project: str, limit: int = 60) -> dict:
    """History, queue, and lifecycle fields from one turn-boundary snapshot."""
    turn_lock = lock(project)
    with _lifecycle_guard(project):
        turn = _active.get(project)
        with S.project_lock(project):
            waiting = _queue_rows(queue_path(project))
        return {"history": chat_history(project, limit), "queued": waiting,
                "active": dict(turn) if turn else None, "busy": turn_lock.locked()}


@contextmanager
def _active_turn(project: str, trigger: str, claim=None):
    with config.restart_lock() as ready:
        if not ready or config.restart_in_progress():
            yield None
            return
        with _publish_active_turn(project, trigger, claim) as turn:
            yield turn


@contextmanager
def _publish_active_turn(project: str, trigger: str, claim=None):
    turn = {"id": uuid.uuid4().hex[:12], "started_at": S.now(), "trigger": trigger}
    lifecycle_guard = _lifecycle_guard(project)
    with lifecycle_guard:
        claimed = claim is None or claim()
        if claimed:
            _active[project] = turn
    if not claimed:
        yield None
        return
    try:
        yield turn
    except Exception as exc:
        chat_log(project, "error", f"L3 turn failed: {exc}", trigger=trigger, turn_id=turn["id"])
        raise
    finally:
        with lifecycle_guard:
            if _active.get(project, {}).get("id") == turn["id"]:
                _active.pop(project, None)
            _created.pop(turn["id"], None)


@contextmanager
def _turn_scope(project: str, trigger: str):
    """Adopt a queue handoff owned by this thread, or acquire a direct turn normally."""
    claimed = getattr(_turn_local, "claimed", None)
    if claimed and claimed["project"] == project and claimed["trigger"] == trigger:
        yield claimed["turn"]
        return
    with lock(project), _active_turn(project, trigger) as turn:
        yield turn


def queue_path(project: str) -> Path:
    return config.project_dir(project) / "l3-queue.jsonl"


def _queue_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _write_queue(path: Path, rows: list[dict]) -> None:
    if rows:
        S.atomic_write(path, "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
    else:
        path.unlink(missing_ok=True)


def queued(project: str) -> list[dict]:
    """The messages waiting for L3, oldest first. A queued message is dropped or run, never edited."""
    with S.project_lock(project):
        return _queue_rows(queue_path(project))


def queue_message(project: str, text: str, *, trigger: str, role: str = "server") -> dict:
    """Leave one message for the project's L3; the server delivers it as a turn once L3 is free. The
    returned row carries the id that drops it again and its position in the queue."""
    path = queue_path(project)
    row = {"at": S.now(), "id": uuid.uuid4().hex[:12], "trigger": trigger, "role": role, "text": text}
    with S.project_lock(project):
        waiting = len(_queue_rows(path))
        with open(path, "a") as stream:
            stream.write(json.dumps(row, sort_keys=True) + "\n")
    return {**row, "position": waiting + 1}


def drop_queued(project: str, message_id: str) -> bool:
    """Drop one of Burak's chat messages that has not started. Server work is not editable."""
    path = queue_path(project)
    with S.project_lock(project):
        rows = _queue_rows(path)
        rest = [row for row in rows
                if row.get("id") != message_id or row.get("trigger") != "chat" or row.get("role") != "burak"]
        if len(rest) == len(rows):
            return False
        _write_queue(path, rest)
        return True


def deliver_queued(project: str) -> dict | None:
    """Run the oldest queued message as one L3 turn, folding the chat messages that follow it into that
    same turn so the operator's consecutive messages are read together, each on its own line and in arrival
    order. Nothing runs while L3 is busy or no engine is available."""
    path = queue_path(project)
    if not path.exists() or not _select(project).get("engine"):
        return None
    turn_lock = lock(project)
    if not turn_lock.acquire(blocking=False):
        return None
    try:
        while True:
            with S.project_lock(project):
                rows = _queue_rows(path)
            if not rows:
                return None
            take = 1
            if rows[0].get("trigger") == "chat":
                while take < len(rows) and rows[take].get("trigger") == "chat":
                    take += 1
            selected = rows[:take]
            selected_ids = [row.get("id") for row in selected]

            def claim() -> bool:
                with S.project_lock(project):
                    current = _queue_rows(path)
                    if [row.get("id") for row in current[:take]] != selected_ids:
                        return False
                    _write_queue(path, current[take:])
                    return True

            trigger = selected[0].get("trigger") or "queued"
            with _active_turn(project, trigger, claim=claim) as active_turn:
                if active_turn is None:  # activation or a removed row leaves the durable queue for the next tick
                    return None
                _turn_local.claimed = {"project": project, "trigger": trigger, "turn": active_turn}
                try:
                    return turn(project, "\n\n".join(row["text"] for row in selected), trigger=trigger)
                finally:
                    del _turn_local.claimed
    finally:
        turn_lock.release()


def _header(project: str, trigger: str, fresh: bool) -> str:
    directory = config.project_dir(project)
    lines = [f"[altitude] project={project} trigger={trigger} state_file={directory / 'STATE.md'} "
             f"tasks_dir={directory / 'tasks'} repo={config.project_path(project)}"]
    if fresh:
        lines.append("[altitude] Fresh provider session. Read the state file first; it is durable project memory.")
    return "\n".join(lines) + "\n\n"


def _handoff(history: list[dict], engine: str, since: str | None) -> str:
    """Only the chat missed while another provider owned L3, never a synthetic full transcript replay."""
    missed = [item for item in history if item.get("at") and (not since or item["at"] > since)
              and item.get("role") in ("user", "assistant") and item.get("engine") != engine]
    if not missed:
        return ""
    lines = [f"- {item['role']}: {str(item.get('text') or '')[:800]}" for item in missed[-20:]]
    return "[altitude] Cross-provider chat missed by this session (oldest first):\n" + "\n".join(lines) + "\n\n"


def _select(project: str, engine: str | None = None) -> dict:
    """The engine for one turn: Burak's choice for this turn, else the project pin, else the quota route."""
    proj = config.project(project)
    forced = engine or proj.get("l3_engine")
    held = engines.usage_hold()
    if held and not forced:
        choice = route.pick_engine("l3", forced="codex")
        if choice.get("engine"):
            choice["why"] = f"Claude short-window hold until {held}; " + choice["why"]
        return choice
    choice = route.pick_engine("l3", forced=forced, current=info(project).get("engine_last"))
    if engine and choice.get("engine"):
        choice["why"] = "chosen by Burak for this turn"
    return choice


def turn(project: str, prompt: str, *, trigger: str = "chat", engine: str | None = None,
         on_text=None, on_start=None, model: str | None = None) -> dict:
    """Run one L3 turn. `engine` pins this turn; otherwise the project pin or the weekly quota selects
    a provider. Each provider resumes only its own transcript."""
    requested = engine
    with _turn_scope(project, trigger) as active_turn:
        if active_turn is None:
            row = queue_message(project, prompt, trigger=trigger, role="burak" if trigger == "chat" else "server")
            return {"queued": row, "error": "Altitude is restarting; the turn is queued", "completed": False}
        turn_id = active_turn["id"]
        choice = _select(project, requested)
        if not choice.get("engine"):
            return {"text": "", "session_id": "", "usage": {}, "context_tokens": 0, "cost": 0.0,
                    "turns": 0, "structured": None, "error": f"engine hold: {choice['why']}", "tools": [],
                    "skipped": False, "completed": False, "_turn_started_at": None, "routing": choice,
                    "turn_id": turn_id}
        engine = choice["engine"]
        proj = config.project(project)
        S.regen_state_md(project)
        inf = info(project)
        sessions = inf.setdefault("sessions", {})
        session = sessions.setdefault(engine, {})
        sid = session.get("session_id")
        over = (session.get("context_percent") or 0) >= config.CONTEXT_LINES[engine][1] * 100
        confinement_changed = (engine == "claude" and bool(sid)
                               and session.get("confinement_version") != L3_CONFINEMENT_VERSION)
        fresh = not sid or session.get("rotate_next", False) or over or confinement_changed
        if fresh and sid:
            rotate_reason = "L3 confinement policy changed" if confinement_changed else (
                session.get("rotate_reason") or "context threshold")
            S.project_log(project, "l3-rotate", engine=engine, old=sid,
                          reason=rotate_reason)
            session.update({"session_id": None, "rotate_next": False, "rotate_reason": None,
                            "context_percent": 0, "rotated_from": sid, "rotated_at": S.now()})
            inf.update({"session_id": None, "rotate_next": False, "rotate_reason": None,
                        "context_percent": 0, "rotated_from": sid, "rotated_at": session["rotated_at"]})
            save_info(project, inf)
            sid = None
        history = chat_history(project, 60)
        handoff = _handoff(history, engine, session.get("last_turn"))
        turn_started_at = active_turn["started_at"]
        chat_log(project, "user", prompt, trigger=trigger, engine=engine, at=turn_started_at, turn_id=turn_id)
        if engine == "codex":
            res = _codex_turn(project, prompt, trigger, turn_started_at, turn_id, choice, inf, session, fresh,
                              handoff, model=model, on_start=on_start)
        else:
            text = _header(project, trigger, fresh) + handoff + prompt
            runtime = _l3_runtime(project, "claude")
            try:
                res = engines.claude_print(
                    text, cwd=runtime, resume=None if fresh else sid,
                    persona=config.PERSONAS / "l3.md", allowed_tools=ALLOWED_TOOLS, tools=L3_TOOLS,
                    permission_mode="dontAsk", permission_prompts="none", restricted=True,
                    add_dirs=(config.project_path(project), config.ROOT),
                    model=model or proj.get("l3_model") or config.MODELS["l3"], on_text=on_text, on_start=on_start,
                    extra_env=_l3_env(project, runtime))
            finally:
                _remove_runtime(runtime)
            res.update({"skipped": False, "_turn_started_at": turn_started_at, "engine": "claude",
                        "routing": choice})
            # A provider-limit result can follow tool side effects. Never replay such a turn automatically elsewhere.
            if (res.get("limited") and not res.get("text") and not res.get("tools")
                    and not requested and not proj.get("l3_engine")):
                fallback = route.pick_engine("l3", forced="codex")
                if fallback.get("engine"):
                    fallback["why"] = f"Claude window closed before producing output; {fallback['why']}"
                    codex_session = sessions.setdefault("codex", {})
                    codex_handoff = _handoff(history, "codex", codex_session.get("last_turn"))
                    return _codex_turn(project, prompt, trigger, turn_started_at, turn_id, fallback, inf, codex_session,
                                       not codex_session.get("session_id"), codex_handoff, model=None,
                                       on_start=on_start)
            if res.get("error") and not res.get("session_id"):
                chat_log(project, "error", res["error"], trigger=trigger, engine="claude", turn_id=turn_id)
                res["turn_id"] = turn_id
                return res
            pct = engines.context_percent(res.get("context_tokens", 0), "claude")
            session.update(engine_model=model or proj.get("l3_model") or config.MODELS["l3"],
                           engine_reasoning_effort=None)
            _save_session(inf, session, "claude", res.get("session_id"), pct, fresh,
                          res.get("cost", 0.0), res.get("usage") or {}, choice)
            session["confinement_version"] = L3_CONFINEMENT_VERSION
            save_info(project, inf)
            chat_log(project, "assistant", res.get("text") or (res.get("error") or ""), trigger=trigger,
                     engine="claude", context_percent=pct, turns=res.get("turns"),
                     tools=_tool_log(res.get("tools") or []), turn_id=turn_id, **_created_meta(project, turn_id))
            S.regen_state_md(project)
            res.update({"context_percent": pct, "completed": True, "turn_id": turn_id})
        return res


def _save_session(inf: dict, session: dict, engine: str, sid: str | None, pct: float,
                  fresh: bool, cost: float, usage: dict, choice: dict) -> None:
    act = config.CONTEXT_LINES[engine][1] * 100
    session.update({"session_id": sid, "turns": (0 if fresh else int(session.get("turns") or 0)) + 1,
                    "context_percent": pct, "last_turn": S.now(), "last_cost": cost,
                    "started": session.get("started") if not fresh else S.now(),
                    "context_state": engines.context_state(pct, engine), "rotate_next": pct >= act,
                    "rotate_reason": f"context {pct}% ≥ act line {int(act)}%" if pct >= act else None,
                    "usage": usage})
    inf.update({"engine_last": engine, "session_id": sid, "context_percent": pct,
                "engine_model": session.get("engine_model"),
                "engine_reasoning_effort": session.get("engine_reasoning_effort"),
                "turns": session["turns"], "last_turn": session["last_turn"], "last_cost": cost,
                "routing": choice, "rotate_next": session["rotate_next"],
                "rotate_reason": session["rotate_reason"]})


def _codex_turn(project: str, prompt: str, trigger: str, turn_started_at: str, turn_id: str, choice: dict,
                inf: dict, session: dict, fresh: bool, handoff: str, *, model: str | None, on_start=None) -> dict:
    """One Codex L3 turn from a disposable runtime directory: the same persona and daemon `alt` door as Claude,
    inside Codex's own sandbox (writes only in that one runtime; the checkout and Altitude home are readable)."""
    proj = config.project(project)
    sid = None if fresh else session.get("session_id")
    body = _header(project, trigger, fresh) + handoff + prompt
    if fresh:
        body = ((config.PERSONAS / "l3.md").read_text() + "\n\n"
                + f"[altitude] Engine: Codex — {choice['why']}.\n\n" + body)
    runtime = _l3_runtime(project, "codex")

    def record_session(metadata):
        if sid and metadata.get("session_id", sid) != sid:
            return  # a mismatched resume must not replace the recorded conversation
        session.update(metadata)
        inf.update(metadata, engine_last=choice["engine"])
        save_info(project, inf)

    record_session({"engine_model": None, "engine_reasoning_effort": None})
    S.project_log(project, "l3-codex", reason=choice["why"], trigger=trigger, resume=bool(sid))
    try:
        result = engines.codex_exec(
            body, cwd=runtime, timeout=config.L3_CODEX_TURN_TIMEOUT, model=model or proj.get("l3_codex_model"),
            effort=config.CODEX_EFFORT.get("l3"), resume=sid, on_start=on_start,
            extra_env=_l3_env(project, runtime),
            sandbox_settings=engines.codex_l3_permissions(runtime, project=project),
            ignore_user_config=True, on_session=record_session)
    finally:
        _remove_runtime(runtime)
    reported_sid = result.get("reported_session_id") or result.get("session_id")
    if result.get("engine_model"):
        record_session({"session_id": reported_sid, "engine_model": result["engine_model"],
                        "engine_reasoning_effort": result.get("engine_reasoning_effort")})
    identity_error = None
    if not reported_sid:
        identity_error = "Codex L3 turn did not report a thread identity"
    elif sid and reported_sid != sid:
        identity_error = f"Codex L3 resume returned a different thread than {sid}"
    usage = result.get("usage") or {}
    tokens = int(usage.get("input_tokens", 0) or 0)
    out = {"text": str(result.get("text") or ""), "session_id": reported_sid or sid or "",
           "usage": usage, "context_tokens": tokens, "cost": 0.0, "turns": 1, "structured": None,
           "error": identity_error or result.get("error"), "tools": result.get("tools") or [],
           "skipped": False, "completed": False,
           "_turn_started_at": turn_started_at, "engine": "codex", "routing": choice, "turn_id": turn_id}
    pct = engines.context_percent(tokens, "codex") if tokens else 0.0
    if identity_error or (result.get("error") and not out["text"]):
        if reported_sid and not identity_error:
            _save_session(inf, session, "codex", reported_sid, pct, fresh, 0.0, usage, choice)
            save_info(project, inf)
        chat_log(project, "error", f"codex L3 turn failed: {out['error']}", trigger=trigger,
                 engine="codex", turn_id=turn_id)
        return out
    _save_session(inf, session, "codex", out["session_id"], pct, fresh, 0.0, usage, choice)
    save_info(project, inf)
    chat_log(project, "assistant", out["text"], trigger=trigger, engine="codex",
             context_percent=pct, cache_tokens=usage.get("cached_input_tokens"), tools=_tool_log(out["tools"]),
             turn_id=turn_id, **_created_meta(project, turn_id))
    S.regen_state_md(project)
    out.update({"context_percent": pct, "completed": True})
    return out


def reset(project: str, reason: str = "manual") -> None:
    inf = info(project)
    sessions = inf.setdefault("sessions", {})
    engine = inf.get("engine_last") or "claude"
    sessions.setdefault(engine, {}).update({"rotate_next": True, "rotate_reason": reason})
    inf.update({"rotate_next": True, "rotate_reason": reason})
    save_info(project, inf)
