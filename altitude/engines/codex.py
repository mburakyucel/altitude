"""Codex launch, permissions, wire protocol, account quota and native observations."""
from __future__ import annotations
import json
import math
import selectors
import os
import re
import stat
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from .. import config, platform, state as S


from . import common


CODEX_UNIT_PREFIX = "altitude-codex-"


CODEX_PATCH_NOTE = (
    "[altitude] Host patch constraint: Do not call the custom `apply_patch` tool, because its filesystem verifier "
    "cannot create its bwrap namespace under this host's AppArmor policy. For every edit, call the shell command "
    "`apply_patch` through the exec tool and pass the patch on stdin; this stays inside the Codex workspace-write "
    "sandbox and its configured writable roots."
)


CODEX_CONTAINER_PATCH_NOTE = (
    "[altitude] Container editing contract (supersedes any earlier host patch constraint): "
    "Edit files with bundled `python3` through the exec tool, inside the existing workspace sandbox "
    "and its allowed writable roots. This image does not provide the shell `apply_patch` command. "
    "Do not call the custom `apply_patch` tool or bypass confinement."
)


def _camel_keys(value):
    if isinstance(value, dict):
        return {re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower(): _camel_keys(item) for key, item in value.items()}
    return value


def _codex_item(item: dict) -> dict | None:
    """One app-server ThreadItem in the `codex exec --json` form; other kinds are not part of the transcript."""
    kind = item.get("type")
    status = re.sub(r"(?<!^)(?=[A-Z])", "_", str(item.get("status") or "")).lower() or None
    base = {"id": item.get("id"), **({"status": status} if status else {})}
    if kind == "agentMessage":
        return {**base, "type": "agent_message", "text": item.get("text") or ""}
    if kind == "reasoning":
        return {**base, "type": "reasoning", "text": "\n".join(str(part) for part in item.get("summary") or [])}
    if kind == "commandExecution":
        return {**base, "type": "command_execution", "command": item.get("command") or "",
                "aggregated_output": item.get("aggregatedOutput") or "", "exit_code": item.get("exitCode")}
    if kind == "fileChange":
        return {**base, "type": "file_change", "changes": [
            {"path": change.get("path"), "kind": (change.get("kind") or {}).get("type")
             if isinstance(change.get("kind"), dict) else change.get("kind")} for change in item.get("changes") or []]}
    if kind == "mcpToolCall":
        return {**base, "type": "mcp_tool_call", "server": item.get("server"), "tool": item.get("tool"),
                "arguments": item.get("arguments"), "result": item.get("result"), "error": item.get("error")}
    if kind == "webSearch":
        return {**base, "type": "web_search", "query": item.get("query")}
    return None


class _Refused(RuntimeError):
    """The Codex server answered a request with an error."""


class _Ended(RuntimeError):
    """The Codex server exited before answering; its status and error output say why."""


class _CodexDriver(common._Driver):
    """Codex runs as `codex app-server` for one turn: `turn/steer` adds a message to the running turn without
    stopping its commands. Every request the server makes of its client is refused, as `codex exec` refuses them."""

    def __init__(self, spec: dict, env: dict):
        super().__init__(spec, env)
        self.replies: dict[int, dict] = {}
        self.arrived = threading.Condition(self.lock)
        self.ids = iter(range(1, 1 << 62))
        self.turn: str | None = None
        self.steers: dict[int, str] = {}
        self.completed = False
        self.usage: dict = {}
        self.model: str | None = None
        self.announced = False
        self.done, self.eof = threading.Event(), threading.Event()

    def call(self, method: str, params: dict) -> dict:
        """Called without the lock; the reader thread delivers the reply."""
        with self.lock:
            identity = next(self.ids)
            self.write({"id": identity, "method": method, "params": params})
            while identity not in self.replies and not self.eof.is_set():
                self.arrived.wait(1)
            reply = self.replies.pop(identity, None)
        if reply is None:
            raise _Ended(f"{method}: the Codex server ended before replying")
        if "error" in reply:
            raise _Refused(f"{method}: {(reply['error'] or {}).get('message') or reply['error']}")
        return reply.get("result") or {}

    def deliver(self, message: dict) -> str | None:
        """Settled by the server's answer where it arrives, so the turn's output after it follows the marker."""
        with self.lock:
            if self.closed or not self.turn:
                return "returned"
            identity = next(self.ids)
            try:
                self.write({"id": identity, "method": "turn/steer", "params": {
                    "threadId": self.session_id, "expectedTurnId": self.turn, "clientUserMessageId": message["id"],
                    "input": [{"type": "text", "text": message["text"], "text_elements": []}]}})
            except OSError:
                return "returned"
            self.steers[identity] = message["id"]
            return None

    def unsettled(self) -> list[str]:
        """Written, but the server ended before answering."""
        pending = list(self.steers.values())
        self.steers.clear()
        return pending

    def status(self) -> int:
        """The server exits cleanly once its input closes, so an unfinished turn sets the job's status."""
        return self.child.returncode or (0 if self.completed else 1)

    def opened(self) -> None:
        """`thread.started` follows the turn's acceptance of its input, so a launch that sees it knows the prompt
        reached the engine, as `codex exec` reports only after reading it."""
        with self.lock:
            announce, self.announced = not self.announced, True
        if announce:
            self.emit({"type": "thread.started", "thread_id": self.session_id,
                       **({"model": self.model} if self.model else {})})

    def notify(self, method: str, params: dict) -> None:
        # The server also streams native helper threads. Only the owner's notifications
        # describe this worker's activity, steering target, usage and completion (#873).
        if not self.session_id or params.get("threadId") != self.session_id:
            return
        if method == "turn/started":
            with self.lock:
                self.turn = (params.get("turn") or {}).get("id") or self.turn
            self.opened()
            self.emit({"type": "turn.started"})
        elif method in ("item/started", "item/completed"):
            item = _codex_item(params.get("item") or {})
            if item:
                # The notification has no wall clock. Record its receipt here, once, rather
                # than assigning a poll/launch time when a viewer reads the saved event.
                self.emit({"type": method.replace("/", "."), "item": item,
                           "timestamp": datetime.now(timezone.utc).isoformat()})
        elif method == "thread/tokenUsage/updated":
            self.usage = _camel_keys((params.get("tokenUsage") or {}).get("total") or {})
        elif method == "error" and not params.get("willRetry"):
            self.emit({"type": "error", "message": (params.get("error") or {}).get("message") or "Codex error"})
        elif method == "turn/completed":
            turn = params.get("turn") or {}
            self.completed = turn.get("status") == "completed"
            if self.completed:
                self.emit({"type": "turn.completed", "usage": self.usage})
            else:
                error = turn.get("error") or {}
                self.emit({"type": "turn.failed", "error": {"message": error.get("message") or
                                                             f"Codex turn {turn.get('status') or 'ended'}"}})
            with self.lock:
                self.turn = None
            self.done.set()

    def read(self) -> None:
        for raw in self.child.stdout:
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            if "id" in message and "method" in message:
                with self.lock:
                    if not self.closed:
                        self.write({"id": message["id"], "error": {
                            "code": -32601, "message": "Altitude answers no client requests"}})
            elif "id" in message:
                with self.lock:
                    steered = self.steers.pop(message["id"], None)
                    if steered is None:
                        self.replies[message["id"]] = message
                if steered:  # a refused steer found no active turn, or another one
                    self.settle(steered, "returned" if "error" in message else "delivered")
                with self.lock:
                    self.arrived.notify_all()
            elif message.get("method"):
                self.notify(message["method"], message.get("params") or {})
        self.eof.set()
        self.done.set()
        with self.lock:
            self.arrived.notify_all()

    def start(self, watcher: threading.Thread) -> None:
        reader = threading.Thread(target=self.read, daemon=True)
        reader.start()
        try:
            self.call("initialize", {"clientInfo": {"name": "altitude", "version": "1"}})
            with self.lock:
                self.write({"method": "initialized"})
            options = {"model": self.spec.get("model"), "approvalPolicy": "never"}
            options = {key: value for key, value in options.items() if value}
            if self.session_id:
                opened = self.call("thread/resume", {"threadId": self.session_id, **options})
            else:
                opened = self.call("thread/start", {"cwd": self.spec.get("cwd"), **options})
            self.session_id = (opened.get("thread") or {}).get("id") or self.session_id
            self.model = opened.get("model")
            started = self.call("turn/start", {"threadId": self.session_id, "input": self.spec["input"]})
            with self.lock:
                self.turn = self.turn or (started.get("turn") or {}).get("id")
            self.opened()
            watcher.start()
            self.done.wait()
        except _Ended:
            pass
        except (OSError, RuntimeError) as exc:
            self.emit({"type": "error", "message": str(exc)})
        finally:
            with self.lock:
                deadline = time.monotonic() + common.SEND_ECHO_WAIT
                while self.steers and not self.eof.is_set():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    self.arrived.wait(remaining)
                self.close()
            reader.join()


def _codex_unit(worker_id: str) -> str:
    """A collision-resistant job name, safe for the service manager."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "-", str(worker_id))
    return f"{CODEX_UNIT_PREFIX}{safe}.service"


def codex_turns(job: int, root: Path, session_id: str):
    """The stdout JSONL of every turn of one thread under `job_root`, oldest first (one worker record per turn)."""
    from . import invocation
    turns = []
    for name in os.listdir(job):
        if not name.endswith(".json"):
            continue
        worker = name[:-5]
        source = invocation.invocation(job, root, worker)
        if source is None:
            continue
        stdout_name = f"{worker}.stdout.jsonl"
        try:
            info = os.stat(stdout_name, dir_fd=job, follow_symlinks=False)
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(info.st_mode):
            raise invocation.InvocationRefused("invocation stdout refused")
        # The initialization record identifies the thread; do not parse the whole transcript
        # merely to discover which sources the live viewer must subsequently project.
        thread = source.discover_stdout(header=True) or source.record.get("session_id")
        if thread == session_id:
            turns.append((str(source.record.get("started_at") or ""), worker, source))
    return [source for _, _, source in sorted(turns)]


def _codex_error(record: dict) -> str:
    error = record.get("error") if isinstance(record.get("error"), dict) else {}
    return common._first_text(record, ("message",)) or common._first_text(error, ("message",)) or json.dumps(record)


def _codex_rows(record: dict, worktree: str) -> list[dict]:
    """Rows for one event of a Codex thread. A command item carries its command and its output together."""
    typ = str(record.get("type") or "event")
    item = record.get("item") if isinstance(record.get("item"), dict) else {}
    item_type = str(item.get("type") or "")
    if typ in ("error", "turn.failed"):
        return [common._transcript_row("codex", "error", "system", _codex_error(record), type=typ)]
    if not item_type or item_type == "reasoning":
        return [common._transcript_row("codex", "engine", "system", "", type=typ)]
    item_id = str(item.get("id") or "") or None
    status = item.get("status") if isinstance(item.get("status"), str) else None
    if item_type == "agent_message":
        return [common._transcript_row("codex", "message", "assistant", str(item.get("text") or ""), type=typ)]
    if item_type == "error":
        return [common._transcript_row("codex", "error", "system", common._first_text(item, ("message", "text")), type=typ)]
    if item_type == "command_execution":
        command = common._shell_command(item.get("command"))
        return [common._transcript_row("codex", "command", "assistant", command, type=typ, tool="command", summary=common._one_line(command),
                     tool_use_id=item_id, status=status, output=str(item.get("aggregated_output") or ""),
                     error=item.get("exit_code") not in (None, 0))]
    if item_type == "file_change":
        changes = item.get("changes") if isinstance(item.get("changes"), list) else []
        lines = [f"{c.get('kind') or 'change'} {common._relative(c.get('path'), worktree)}".rstrip()
                 for c in changes if isinstance(c, dict)]
        return [common._transcript_row("codex", "file", "assistant", "\n".join(lines), type=typ, tool="file_change",
                     summary=common._one_line(", ".join(lines)), tool_use_id=item_id, status=status)]
    if item_type == "mcp_tool_call":
        summary = ".".join(str(item.get(k) or "") for k in ("server", "tool") if item.get(k)) or item_type
    else:
        summary = common._first_text(item, ("query", "text", "message", "command")) or item_type
    return [common._transcript_row("codex", "tool", "assistant", common._first_text(item, ("text", "output", "result", "message")), type=typ,
                 tool=item_type, summary=common._one_line(summary), tool_use_id=item_id, status=status)]


_TOKEN_ROLLOUT_INDEX: dict[str, dict] = {}


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


def _codex_session_context(session_id: str | None, started_at: str, home: Path) -> dict | None:
    """Newest own request in this invocation; a turn aggregate is never context occupancy."""
    if not session_id or not re.fullmatch(r"[A-Za-z0-9-]+", session_id):
        return None
    latest, window = None, None
    try:
        for path in home.glob(f"sessions/*/*/*/rollout-*-{session_id}.jsonl"):
            with path.open() as rollout:
                for line in rollout:
                    if not line.endswith("\n") or not any(kind in line for kind in ('"token_usage_record"', '"token_count"')):
                        continue
                    try:
                        event = json.loads(line)
                        if datetime.fromisoformat(event["timestamp"]) < datetime.fromisoformat(started_at):
                            continue
                        payload = event.get("payload") or {}
                        usage = None
                        if event.get("type") == "token_usage_record" and payload.get("thread_id") == session_id:
                            usage = payload.get("usage")
                        elif event.get("type") == "event_msg" and payload.get("type") == "token_count":
                            info = payload.get("info") or {}
                            value = info.get("model_context_window")
                            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                                window = value
                            usage = info.get("last_token_usage")
                        if isinstance(usage, dict) and not common._token_numbers(usage, "codex")["incomplete"]:
                            candidate = {"tokens": usage["input_tokens"], "observed_at": event["timestamp"]}
                            if latest is None or candidate["observed_at"] >= latest["observed_at"]:
                                latest = candidate
                    except (ValueError, KeyError, TypeError, AttributeError):
                        continue
    except OSError:
        return None
    return {**latest, "window": window,
            "percent": round(100.0 * latest["tokens"] / window, 1) if window else None} if latest else None


def _codex_home(env: dict) -> Path:
    return Path(env.get("CODEX_HOME") or Path(env.get("HOME") or config.HOME) / ".codex")


def codex_sandbox(cwd: Path, *, extra_roots: list[Path] = ()) -> list[str]:
    """The task's native permissions, shared by launch and the provider-free sandbox diagnostic.

    Writable roots must exist because Codex bind-mounts them: the working directory, any extra root (a worker's
    Git directories so it can fetch, commit, and push), and the Altitude home so `alt` can record what the turn
    reports. The workspace base retains protected configuration paths and temporary directories.
    Network stays on for `git push`, `gh`, and the repository's own tests; user-manager sockets and macOS's private
    places stay denied.
    The sandboxed shell inherits the launch environment, so the identity variables reach `alt` and a worker's
    GitHub token (`github_token`) reaches `gh` unchanged.
    """
    roots = dict.fromkeys([Path(cwd).resolve(), *(Path(root).resolve() for root in extra_roots), config.ROOT.resolve()])
    profile = "altitude-task"
    workspace_roots = "{" + ",".join(f"{json.dumps(str(root))}=true" for root in roots) + "}"
    denied = ",".join(f'{json.dumps(str(path))}="deny"' for path in (*platform.job_control_paths(),
                                                                       *platform.private_places()))
    return [f'default_permissions="{profile}"', f'permissions.{profile}.extends=":workspace"',
            f"permissions.{profile}.workspace_roots={workspace_roots}",
            f'permissions.{profile}.filesystem={{":root"="read",{denied}}}',
            f"permissions.{profile}.network.enabled=true", 'approval_policy="never"']


def codex_l3_permissions(cwd: Path, *, project: str) -> list[str]:
    """A Codex profile which denies L3 checkout/service writes and direct command networking.

    I-20260903-075410: L3 needs broad reads and one disposable writable cwd. ``alt`` plus GitHub/service reads
    cross altd's fixed protocol through a stdio MCP adapter; the model cannot write Altitude state, reach mutating HTTP APIs,
    use authenticated GitHub directly, connect to the user bus, or write a checkout.
    """
    profile = "altitude-l3"
    from ..l3 import verb_socket_path
    broker = verb_socket_path(project).resolve()
    rules = {":root": "read", str(Path(cwd).resolve()): "write",
             str(config.project_path(project).resolve()): "read",
             **{str(path): "deny" for path in (*platform.job_control_paths(), *platform.private_places())}}
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
            "mcp_servers.altitude.args=" + json.dumps(["-I", "-B", "-c", adapter]),
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


def codex_bg(name: str, prompt: str, *, cwd: Path, job_root: Path, resume: str | None = None,
             persona: Path | None = None, model: str | None = None, extra_env: dict | None = None,
             start_timeout: float = 15.0) -> dict:
    return common._start_worker("codex", name, prompt, cwd=cwd, job_root=job_root, resume=resume,
                         persona=persona, model=model, extra_env=extra_env, start_timeout=start_timeout)


def _codex_outcome(events: list[dict], returncode: int, stdout: str, stderr: str, *, text: str,
                   resume: str | None, model: str | None, metadata: dict) -> dict:
    tools = [common._tool("Bash", (event.get("item") or {}).get("command")) for event in events
             if event.get("type") == "item.completed"
             and (event.get("item") or {}).get("type") == "command_execution"]
    thread = common._event_session(events)
    failure = next((common._event_error("codex", event) for event in reversed(events)
                    if event.get("type") == "turn.failed" or
                    (returncode != 0 and event.get("type") == "error")), None)
    result = {"text": text.strip(), "returncode": returncode,
              "usage": common._event_usage(events), "session_id": thread or resume, "reported_session_id": thread,
              "tools": tools, **metadata,
              "error": (failure if isinstance(failure, str) else json.dumps(failure))[:500] if failure else
                       None if returncode == 0 else (stderr or "").strip()[:500],
              "raw_stdout": stdout or "", "raw_stderr": stderr or "",
              "raw_stdout_truncated": False, "raw_stderr_truncated": False}
    result.update(safe_to_retry=common._safe_output("codex", stdout or ""), rejection=common.rejection("codex", result, model))
    limited = common.usage_limit_in(result.get("error"))
    if limited:
        result["limited"] = limited
    return result


@config.admitted_provider
def codex_exec(prompt: str, *, cwd: Path, model: str | None = None, timeout: int = 900, effort: str | None = None,
               extra_env: dict | None = None, resume: str | None = None, on_start=None,
               sandbox_settings: list[str] | None = None, ignore_user_config: bool = True, on_session=None,
               images: list[dict] | tuple = ()) -> dict:
    """One synchronous Codex turn (L3) in Codex's own workspace-write sandbox, prompt on stdin (verified with
    codex 0.152). `codex exec resume <thread> -` continues the thread. The transient unit is the one workers use,
    so altd's `NoNewPrivileges` hardening never reaches the nested bwrap, and a timeout stops the whole tree.
    Send now stays queued for the next coordinator turn; this invocation has no interrupt channel."""
    content = common._engine_input("codex", prompt, images)
    prompt = content[0]["text"]
    image_args = [arg for item in content[1:] for arg in ("--image", item["path"])]
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
    proc = subprocess.Popen(platform.job_command(unit, cmd, common.launch_env(extra_env),
                            runtime_max=timeout),
                            cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=common.launch_env(extra_env, retain_user_bus=True), start_new_session=True)
    # The writer owns stdin, so the polling communicate() below only reads.
    prompt_input, proc.stdin = proc.stdin, None
    threading.Thread(target=common._feed, args=(prompt_input, prompt), daemon=True).start()
    if on_start:
        on_start(proc.pid)
    metadata = {}
    deadline = time.monotonic() + timeout
    def observe(stdout):
        nonlocal metadata
        thread = common._event_session(common._parse_events(stdout or ""))
        metadata = _codex_session_model(thread, started_at, _codex_home(common.launch_env(extra_env)))
        if metadata and on_session:
            on_session({"session_id": thread, **metadata})
    try:
        while True:
            remaining = deadline - time.monotonic()
            try:
                stdout, stderr = proc.communicate(timeout=remaining if metadata else min(0.5, remaining))
                break
            except subprocess.TimeoutExpired as exc:
                if time.monotonic() >= deadline:
                    raise
                observe((exc.output or b"").decode("utf-8", errors="replace"))
    except subprocess.TimeoutExpired:
        platform.job_stop(unit)
        proc.kill()
        proc.communicate()
        raise
    if not metadata:
        observe(stdout)
    events = common._parse_events(stdout or "")
    result = _codex_outcome(events, proc.returncode, stdout, stderr, text=(common._agent_messages(events) or [""])[-1],
                            resume=resume, model=model, metadata=metadata)
    result["context"] = _codex_session_context(result.get("reported_session_id"), started_at,
                                              _codex_home(common.launch_env(extra_env)))
    return result


def prepare_worker(name, prompt, *, cwd, persona, model, settings, effort, resume, images):
    cmd = [config.CODEX_BIN, "app-server", "--strict-config"]
    if effort is not None:
        cmd += ["-c", f'model_reasoning_effort="{effort}"']
    for setting in codex_sandbox(cwd, extra_roots=common._git_dirs(cwd)):
        cmd += ["-c", setting]
    in_container = platform.containerized()
    patch_note = CODEX_CONTAINER_PATCH_NOTE if in_container else CODEX_PATCH_NOTE
    text = ((patch_note + "\n\n" if in_container else "") + prompt) if resume else (
        ((Path(persona).read_text() + "\n\n") if persona else "") + patch_note + "\n\n" + prompt)
    content = common._engine_input("codex", text, images)
    return cmd, content, None


worker_unit_name = _codex_unit


def image_input(prompt, images, native):
    return [{"type": "text", "text": prompt, "text_elements": []},
            *({"type": "localImage", "path": item["path"]} for item in images if native)]


def transcript_sources(task, *, job, root):
    return [(source, source.record) for source in codex_turns(job, root, str(task.get("session_id") or ""))]


def transcript_rows(record, worktree):
    rows = _codex_rows(record, worktree)
    for row in rows:
        row["at"] = record.get("timestamp")
    return rows


def background_items(records, items, entry, end):
    seen: set[str] = set()
    returned = False  # a call ended since the newest call started: the next call is a further step
    for record in records:
        item = record.get("item") if isinstance(record.get("item"), dict) else {}
        identity = str(item.get("id") or "")
        if record.get("type") not in ("item.started", "item.completed") or not identity:
            continue
        if identity not in seen:
            seen.add(identity)
            if returned or item.get("type") in ("agent_message", "reasoning"):
                for other in items.values():
                    if other["status"] == "running":
                        other["background"] = True
            returned = False
        if record["type"] == "item.completed":
            returned = True
        if item.get("type") != "command_execution":
            continue
        at = record.get("timestamp") if isinstance(record.get("timestamp"), str) else None
        command = entry(identity, label=common._one_line(common._shell_command(item.get("command")), 120), call=identity, started_at=at)
        if record["type"] == "item.completed" and item.get("status") in ("completed", "failed", "declined"):
            end(command, "completed" if item.get("status") == "completed" and item.get("exit_code") in (None, 0)
                else "failed", at)


def review_command(snapshot, runtime, model, images, effort, server):
    executable = Path(shutil.which(config.CODEX_BIN) or config.CODEX_BIN)
    stat = executable.stat()
    _, features, _ = common._review_native("codex", str(executable), stat.st_mtime_ns, stat.st_size)
    filesystem = {":root": "deny", ":minimal": "read", str(snapshot): "read", str(runtime): "read"}
    settings = ['default_permissions="captured-review"', 'approval_policy="never"', 'web_search="disabled"',
                'permissions.captured-review.network.enabled=false', "project_doc_max_bytes=0",
                'shell_environment_policy.inherit="none"', "mcp_servers={}",
                'permissions.captured-review.filesystem={' + ",".join(f"{json.dumps(k)}={json.dumps(v)}" for k, v in filesystem.items()) + "}",
                "mcp_servers.captured.command=" + json.dumps(server["command"]),
                "mcp_servers.captured.args=" + json.dumps(server["args"]), "mcp_servers.captured.required=true",
                'mcp_servers.captured.tools.captured_input.approval_mode="approve"',
                # Codex reaches MCP tools only through its code-mode host: a JavaScript tool bridge without
                # filesystem, process or network globals whose write tool the read-only sandbox rejects. With the
                # host disabled, captured_input is unreachable and the turn still completes (I-20260924-080541).
                *(f"features.{feature}=" + ("true" if feature in ("skip_host_skill_discovery", "code_mode_host") else "false")
                  for feature in features)]
    return [config.CODEX_BIN, "exec", "--json", "--strict-config", "--ignore-user-config", "--ignore-rules",
            "--ephemeral", "--skip-git-repo-check", "-C", str(runtime), *(["-m", model] if model else []),
            *(f"--image={path}" for _, path in images),
            *(["-c", f'model_reasoning_effort="{effort}"'] if effort else []),
            *(arg for setting in settings for arg in ("-c", setting)), "-"]


_REQUESTS = (
    {"jsonrpc": "2.0", "id": 1, "method": "initialize",
     "params": {"clientInfo": {"name": "altitude", "title": "altitude", "version": "0"}}},
    {"jsonrpc": "2.0", "id": 2, "method": "account/rateLimits/read", "params": {}},
)
_MAX_OUTPUT_BYTES = 4 * 1024 * 1024


def _spawn():
    return subprocess.Popen(
        [config.CODEX_BIN, "app-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )


def _talk(timeout: float) -> list[str]:
    """Send the two requests and return stdout through response 2, ignoring notifications."""
    proc = _spawn()
    lines: list[str] = []
    try:
        if proc.stdin is None or proc.stdout is None:
            raise RuntimeError("app-server pipes unavailable")
        wire = b"".join((json.dumps(request, separators=(",", ":")) + "\n").encode() for request in _REQUESTS)
        proc.stdin.write(wire)
        proc.stdin.flush()
        deadline = time.monotonic() + timeout
        buffered = b""
        total_bytes = 0
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TimeoutError("Codex rate-limit read timed out")
                chunk = os.read(proc.stdout.fileno(), 65536)
                if not chunk:
                    if buffered:
                        lines.append(buffered.decode(errors="replace"))
                    code = proc.poll()
                    suffix = f" with status {code}" if code is not None else ""
                    raise RuntimeError(f"app-server exited before response{suffix}")
                total_bytes += len(chunk)
                if total_bytes > _MAX_OUTPUT_BYTES:
                    raise RuntimeError(
                        f"Codex app-server output exceeded {_MAX_OUTPUT_BYTES} bytes"
                    )
                buffered += chunk
                while b"\n" in buffered:
                    raw, buffered = buffered.split(b"\n", 1)
                    line = raw.decode(errors="replace")
                    lines.append(line)
                    try:
                        message = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(message, dict) and message.get("id") == 2:
                        return lines
    finally:
        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
            try:
                proc.wait()
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            pass
        if proc.stdout is not None:
            try:
                proc.stdout.close()
            except Exception:  # noqa: BLE001
                pass


def _unknown(why: str) -> dict:
    return {"known": False, "why": why[:300]}


def _epoch_iso(value) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(float(value), timezone.utc).replace(microsecond=0).isoformat()


def read_quota(timeout: float = 20.0) -> dict:
    """Return the account-wide Codex quota, or an explicit unknown result on every probe failure."""
    try:
        lines = _talk(timeout)
    except FileNotFoundError:
        return _unknown("Codex binary not found")
    except (TimeoutError, subprocess.TimeoutExpired):
        return _unknown("Codex rate-limit read timed out")
    except Exception as exc:  # noqa: BLE001
        return _unknown(f"Codex app-server failed: {exc}")

    response = None
    for line in lines:
        try:
            message = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(message, dict) and message.get("id") == 2:
            response = message
            break
    if response is None:
        return _unknown("Codex rate-limit response missing")
    if response.get("error") is not None:
        error = response["error"]
        message = error.get("message") if isinstance(error, dict) else str(error)
        # The refusal itself, for sign-in recognition only; the refresh never persists it.
        return {**_unknown(f"Codex JSON-RPC error: {message or error}"), "error": error}

    result = response.get("result")
    limits = result.get("rateLimits") if isinstance(result, dict) else None
    limits = limits if isinstance(limits, dict) else {}
    windows, errors = {}, []
    for name in ("primary", "secondary"):
        windows.update({f"{name}_used": None, f"{name}_resets": None, f"{name}_window_minutes": None})
        window = limits.get(name)
        used = window.get("usedPercent") if isinstance(window, dict) else None
        if used is None:
            errors.append(f"Codex response missing rateLimits.{name}.usedPercent")
            continue
        try:
            percent = float(used)
            if not math.isfinite(percent) or not 0 <= percent <= 100:
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            errors.append(f"Codex response has invalid {name} usedPercent: {used!r}")
            continue
        resets_at = window.get("resetsAt")
        try:
            resets = _epoch_iso(resets_at)
        except (TypeError, ValueError, OverflowError, OSError):
            errors.append(f"Codex response has invalid {name} resetsAt: {resets_at!r}")
            continue
        windows.update({f"{name}_used": percent, f"{name}_resets": resets,
                        f"{name}_window_minutes": window.get("windowDurationMins")})

    if not any(windows[f"{name}_used"] is not None for name in ("primary", "secondary")):
        return _unknown(errors[0])

    return {
        "known": True,
        **windows,
        "plan_type": limits.get("planType"),
        "read_at": S.now(),
    }


def token_numbers(usage):
    """Response records are request increments; inclusive input is the reported input alone."""
    inputs, outputs, reads, writes, reasoning = (common._token_count(usage.get(key)) for key in (
        "input_tokens", "output_tokens", "cached_input_tokens", "cache_write_input_tokens", "reasoning_output_tokens"))
    return {**dict(zip(common._TOKEN_FIELDS, (inputs, outputs, reads, writes, reasoning))),
            "incomplete": inputs is None or outputs is None}


def token_sources(home, owners, register):
    sources, pending = [], False
    root = home or _codex_home(common.launch_env())
    index, pending = _token_rollouts(root)
    wanted = set(owners)
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
    return sources, pending


def token_event(path, sid, kind, row, file, event, offset):
    at = event.get("timestamp")
    payload = event.get("payload") or {}
    values, key, turn = None, None, None
    if kind == "rollout":
        if event.get("type") == "token_usage_record":
            if payload.get("thread_id") != sid or not payload.get("response_id"):
                return None, None, at  # copied fork history belongs to its recorded thread
            values = token_numbers(payload.get("usage") or {})
            key, turn = f"response:{payload['response_id']}", payload.get("turn_id")
        elif event.get("type") in ("event_msg", "turn_context"):
            if row.get("forked"):
                boundary = row.get("fork_ordinal")
                if isinstance(boundary, int) and event.get("ordinal", -1) < boundary:
                    return None, None, at
                if not isinstance(boundary, int) and (not at or not row.get("fork_time") or at < row["fork_time"]):
                    return None, None, at
            if payload.get("turn_id"):
                file["turn"] = payload["turn_id"]
            elif payload.get("type") == "task_started":
                file["turn"] = f"offset:{offset}"
            if payload.get("type") == "token_count":
                window = (payload.get("info") or {}).get("model_context_window")
                if isinstance(window, int) and not isinstance(window, bool) and window > 0:
                    row["context_window"] = window
                usage = (payload.get("info") or {}).get("total_token_usage")
                if not isinstance(usage, dict):
                    return None, None, at
                turn = file.get("turn", "unidentified")
                values = token_numbers(usage)
                old = row["turns"].get(turn, {})
                if turn not in row.setdefault("turn_order", []):
                    row["turn_order"].append(turn)
                row.setdefault("turn_starts", {}).setdefault(turn, values["input_tokens"])
                if old.get("input_tokens") is not None and values["input_tokens"] is not None and values["input_tokens"] < old["input_tokens"]:
                    row["notes"] = list(set(row["notes"] + ["A replayed or reset counter cannot be fully reconciled."]))
                row["turns"][turn] = common._token_max(old, values)
    elif kind == "stdout" and (event.get("type") == "turn.completed"):
        values = token_numbers(event.get("usage") or {})
        if not any(values.get(key) for key in common._TOKEN_FIELDS):
            return None, None, at
        row.setdefault("stdout", {})[path.name] = common._token_max(row.get("stdout", {}).get(path.name, {}), values)
        at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
    return values, key, at


def context_window():
    return None


def rejection_flags(text, said, model, auth, model_missing):
    auth |= any(re.match(r"unexpected status 401\b|Not logged in$", message) for message in said) or bool(re.search(
        r"Your access token could not be refreshed|You do not have access to Codex|"
        r"This account is not currently authorized to use Codex in this workspace", text))
    return auth, model_missing


def worker_events(path):
    return common._read_events(path)


INSTALL_COMMAND = 'npm install -g @openai/codex'
SIGN_IN = (('login', 'status'), 'codex login')
IMAGE_READER = 'view_image'
EFFORT_ENV = None
NATIVE_SEND = False
AUTO_ROTATION = False  # the engine compacts its own conversation
WINDOW_HOLD = False  # limits are reported per turn
NESTED_ENV_PREFIXES = ()
REVIEW_ENV = ("CODEX_HOME",)
TRANSCRIPT_IN_JOB = True
STATUS_EVENTS = ()


def worker_record(env):
    """Where the worker's rollout records live, for later model observation."""
    return {"codex_home": str(_codex_home(env))}


def session_context_source(session_id):
    return None


def event_error(event):
    return None


def safe_event(event):
    return False


def review_input(prompt, images):
    return prompt  # images attach by flag


def review_result_facts(records):
    return {}


def legacy_worker(task):
    return None


def legacy_unit(task):
    return None


def stop_legacy_worker(worker_id):
    raise RuntimeError("Worker ownership record is unavailable; stop is unconfirmed")

def binary():
    return config.CODEX_BIN


def session_timeout():
    return config.L3_CODEX_TURN_TIMEOUT


def image_cli_support(executable):
    result = subprocess.run([executable, "app-server", "--help"], capture_output=True, text=True, timeout=5, env=common.clean_env())
    return not result.returncode and all(marker in result.stdout for marker in ('--strict-config',))


Driver = _CodexDriver


def review_native(executable):
    try:
        result = subprocess.run([executable, "exec", "--help"], capture_output=True, text=True, timeout=5, env=common._review_env())
        flags = ("--ignore-user-config", "--ignore-rules", "--strict-config", "--ephemeral")
        if result.returncode or not all(flag in result.stdout for flag in flags):
            return False, (), False
        features = subprocess.run([executable, "features", "list"], capture_output=True, text=True, timeout=5, env=common._review_env())
        names = tuple(line.split()[0] for line in features.stdout.splitlines() if line.split())
        if features.returncode or not {"shell_tool", "apps", "plugins", "multi_agent", "skip_host_skill_discovery"}.issubset(names):
            return False, (), False
        return True, names, "--image" in result.stdout
    except (OSError, subprocess.SubprocessError):
        return False, (), False


def review_kept(record):
    item = record.get("item")
    return not isinstance(item, dict) or item.get("type") == "agent_message"


def review_errors(record):
    messages = []
    if record.get("type") not in ("turn.failed", "error"):
        return messages, False
    error = record.get("error") if record["type"] == "turn.failed" else record
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        messages.append(error["message"])
    return messages, True


def review_output(stdout):
    events = common._parse_events(stdout)
    messages = [event["item"].get("text", "") for event in events if event.get("type") == "item.completed"
                and (event.get("item") or {}).get("type") == "agent_message"]
    text = messages[-1] if messages else ""
    usage = common._event_usage(events)
    if any(event.get("type") == "turn.failed" for event in events):
        return "", usage, True
    return text, usage, False


def public_message_identity(record):
    message = record.get("item")
    return str(message["id"]) if isinstance(message, dict) and message.get("id") else None


def review_writable(runtime):
    return None


CONFINEMENT = 'Codex sandbox'
JOB_PROFILE = False


def delivered_context(record):
    return []


def observe_worker_model(record, events, record_path):
    if not record.get("engine_model"):
        metadata = _codex_session_model(common._event_session(events), record["started_at"],
                                       Path(record.get("codex_home") or _codex_home(common.launch_env())))
        if metadata:
            record.update(metadata)
            S.write_json(record_path, record)
