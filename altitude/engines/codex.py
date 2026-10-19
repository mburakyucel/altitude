"""Codex launch, permissions, wire protocol, account quota and native observations."""
from __future__ import annotations
import base64
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
from datetime import datetime, timezone
from pathlib import Path
from .. import config, git_policy, platform, state as S, storage


from . import common


CODEX_UNIT_PREFIX = "altitude-codex-"


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
    stopping its commands. The driver prepares the job's own Codex folder (`_own_folder`) and signs Codex in with the
    access token in its launch input (`worker_sign_in`), over the app server's own protocol, never its environment.
    Codex asks for a fresh token when the provider refuses one; the driver asks altd through the task door. Every
    other request the server makes of its client is refused, as `codex exec` refuses them.
    Codex has no hook checkpoint, so an owner's driver steers its task's waiting messages into the turn as they
    arrive; once the server refuses one, the rest wait for the next turn with it."""

    def __init__(self, spec: dict, env: dict):
        _own_folder(Path(env["CODEX_HOME"]), Path(spec["operator"]), spec.get("copies") or ())
        super().__init__(spec, env)
        self.replies: dict[int, dict] = {}
        self.arrived = threading.Condition(self.lock)
        self.ids = iter(range(1, 1 << 62))
        self.turn: str | None = None
        self.steers: dict[int, str] = {}
        self.held = False
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
                self.held = True
                return "returned"
            self.steers[identity] = message["id"]
            return None

    def collect(self) -> None:
        task = self.spec.get("task")
        with self.lock:
            steering = bool(task and self.turn) and not (self.closed or self.held)
        if not steering:
            return
        try:
            from .. import door, tasks
            if tasks.steer_ready(task["project"], task["slug"], self.spec.get("worker_id")):
                door.call("steer", {})  # altd claims them and hands them to this driver's sends
        except Exception as exc:  # noqa: BLE001 — the messages keep waiting in the inbox for the next turn
            self.held = True
            print(f"altitude-driver: waiting messages stay for the next turn: {exc}", file=sys.stderr, flush=True)

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
            if "id" in message and message.get("method") == "account/chatgptAuthTokens/refresh":
                threading.Thread(target=self.refresh, args=(message["id"],), daemon=True).start()
            elif "id" in message and "method" in message:
                with self.lock:
                    if not self.closed:
                        self.write({"id": message["id"], "error": {
                            "code": -32601, "message": "Altitude answers no client requests"}})
            elif "id" in message:
                with self.lock:
                    steered = self.steers.pop(message["id"], None)
                    if steered is None:
                        self.replies[message["id"]] = message
                    elif "error" in message:
                        self.held = True
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

    def refresh(self, identity) -> None:
        """Answer Codex's request for a fresh access token with the one altd holds now (`worker_sign_in`)."""
        try:
            from .. import door
            token = door.call("sign-in", {}, wait=60)
            reply = {"result": {"accessToken": token["access_token"], "chatgptAccountId": token["account_id"],
                                "chatgptPlanType": None}}
        except Exception as exc:  # noqa: BLE001 — Codex ends the turn with the provider's refusal
            reply = {"error": {"code": -32603, "message": f"{SIGNED_OUT}{exc}"}}
        with self.lock:
            if not self.closed:
                self.write({"id": identity, **reply})

    def sign_in(self) -> None:
        token = self.spec.get("sign_in") or {}
        if not token.get("access_token"):
            raise RuntimeError(f"{SIGNED_OUT}{token.get('error') or 'Codex is signed out.'}")
        try:
            self.call("account/login/start", {"type": "chatgptAuthTokens", "accessToken": token["access_token"],
                                              "chatgptAccountId": token["account_id"], "chatgptPlanType": None})
        except _Refused as exc:
            raise RuntimeError(f"{HAND_OVER_REFUSED}{exc}") from None

    def start(self, watcher: threading.Thread) -> None:
        reader = threading.Thread(target=self.read, daemon=True)
        reader.start()
        try:
            self.call("initialize", {"clientInfo": {"name": "altitude", "version": "1"},
                                     "capabilities": {"experimentalApi": True}})
            with self.lock:
                self.write({"method": "initialized"})
            self.sign_in()
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


def _rollouts(home: Path, session_id: str | None = None) -> list[Path]:
    """The conversation records in a Codex folder (`sessions/YYYY/MM/DD/rollout-*.jsonl`), of one thread when named,
    listed as `storage.names` lists, so never through a link a job planted in its own folder."""
    sessions, suffix = home / "sessions", f"-{session_id}.jsonl" if session_id else ".jsonl"
    return [folder / name for year in storage.names(sessions) for month in storage.names(sessions / year)
            for day in storage.names(sessions / year / month) for folder in [sessions / year / month / day]
            for name in storage.names(folder) if name.startswith("rollout-") and name.endswith(suffix)]


def _token_rollouts(home: Path) -> tuple[dict, bool]:
    """Cache only rollout identity/parentage; at most 128 new headers per discovery pass."""
    cache = _TOKEN_ROLLOUT_INDEX.setdefault(str(home), {"at": 0, "paths": {}, "todo": []})
    if time.monotonic() - cache["at"] >= 30 or not cache["at"]:
        cache["at"] = time.monotonic()
        cache["todo"] = [str(p) for p in _rollouts(home) if str(p) not in cache["paths"]]
    for name in cache["todo"][:128]:
        try:
            with os.fdopen(storage.read_open(Path(name)), "rb") as stream:
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
        for path in _rollouts(home, session_id):
            with os.fdopen(storage.read_open(path)) as rollout:
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
        for path in _rollouts(home, session_id):
            with os.fdopen(storage.read_open(path)) as rollout:
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


def denied() -> tuple[str, ...]:
    """What every Codex profile Altitude sets refuses: the places no sandbox Altitude applies reaches (the per-user
    runtime folder on Linux, the job folders on macOS), macOS's private places, Altitude's credentials and the shared
    temporary folders. A narrower entry reopens a folder inside them: a coordinator's working folder in the runtime
    folder, and the job's own temporary folder, its TMPDIR (`:tmpdir`), which `platform.job_command` makes in /tmp."""
    return (*map(str, platform.job_control_paths()), *platform.private_places(), *platform.secret_places(),
            ":slash_tmp", os.path.realpath("/var/tmp"))


#: What a job's own Codex folder links to in the operator's, read-only in the job: their settings, instructions,
#: rules and prompts. Their skills are linked one by one (`SKILLS`), since Codex adds its bundled ones to that folder.
LINKED = ("config.toml", "AGENTS.md", "AGENTS.override.md", "rules", "prompts")
SKILLS = "skills"
#: How a worker's turn says that Altitude holds no Codex sign-in to hand it, read as a sign-in lapse
#: (`rejection_flags`), and how it says that Codex refused the sign-in Altitude hands it.
SIGNED_OUT = "Codex sign-in unavailable to Altitude's workers: "
HAND_OVER_REFUSED = "Codex refused the sign-in Altitude hands its workers: "
KEYCHAIN = ("Codex keeps its sign-in in the system keychain, which Altitude cannot hand to its workers; set "
            'cli_auth_credentials_store = "file" in Codex\'s config.toml and sign in again with `codex login`.')
API_KEY = "Codex is signed in with an API key; Altitude's Codex workers need a ChatGPT sign-in (`codex login`)."


def operator_home() -> Path:
    """The operator's own Codex folder, resolved: their sign-in, settings and conversations."""
    return Path(os.path.realpath(_codex_home(common.launch_env())))


def operator_places() -> tuple[str, ...]:
    """What no job in Altitude's sandbox sees (`platform.job_command`): the operator's Codex folder, with its sign-in,
    conversations and the control socket Codex's background service opens there."""
    return (str(operator_home()),)


def _program(operator: Path) -> list[Path]:
    """The entry of the operator's Codex folder holding the installed program, where the standalone installer puts
    it, which a Codex job reads."""
    found = os.path.realpath(shutil.which(config.CODEX_BIN) or config.CODEX_BIN)
    try:
        return [operator / Path(found).relative_to(operator).parts[0]]
    except (ValueError, IndexError):
        return []


def _first_resume(home: Path, operator: Path, resume: str | None) -> list[Path]:
    """The records of a conversation, and of its helper threads, that a job resumes for the first time in its own
    folder: in the operator's folder, where Codex kept every conversation before jobs had folders of their own."""
    if not resume or not re.fullmatch(r"[A-Za-z0-9-]+", resume) or _rollouts(home, resume):
        return []
    index, pending = _token_rollouts(operator)
    while pending:
        index, pending = _token_rollouts(operator)
    wanted = {resume}
    while True:
        children = {meta["session_id"] for meta in index.values() if meta["parent"] in wanted}
        if children <= wanted:
            break
        wanted |= children
    return sorted(Path(name) for name, meta in index.items() if meta["session_id"] in wanted)


def _own_folder(home: Path, operator: Path, copies) -> None:
    """Prepare a job's own Codex folder from inside the job, as Claude's, so Altitude never writes where the job can.

    It links the operator's settings, instructions, rules, prompts and skills, and copies in the records of a
    conversation it resumes for the first time (`_first_resume`), which the job reads where the operator's folder
    is otherwise hidden."""
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    for name in LINKED:
        if os.path.lexists(operator / name) and not os.path.lexists(home / name):
            os.symlink(operator / name, home / name)
    if (operator / SKILLS).is_dir():
        (home / SKILLS).mkdir(exist_ok=True)
        for entry in os.scandir(operator / SKILLS):
            if not entry.name.startswith(".") and not os.path.lexists(home / SKILLS / entry.name):
                os.symlink(entry.path, home / SKILLS / entry.name)
    for name in copies:
        target = home / Path(name).relative_to(operator)
        if not os.path.lexists(target):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(name, target)


_SIGN_IN = threading.Lock()


def _claims(token: str) -> dict:
    try:
        part = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError, TypeError):
        return {}
    return claims if isinstance(claims, dict) else {}


def _stale(token: str) -> bool:
    """Whether less than half of an access token's lifetime remains, or an hour when it does not say when it began."""
    claims = _claims(token)
    expires, issued = claims.get("exp"), claims.get("iat")
    if not isinstance(expires, (int, float)):
        return False
    left = expires - time.time()
    return left < ((expires - issued) / 2 if isinstance(issued, (int, float)) else 3600)


def _stored_sign_in() -> dict:
    """The access token and account of the operator's Codex sign-in, or why there is none to hand a worker."""
    operator = operator_home()
    try:
        stored = json.loads((operator / "auth.json").read_text())
    except FileNotFoundError:
        try:
            import tomllib
            store = tomllib.loads((operator / "config.toml").read_text()).get("cli_auth_credentials_store")
        except (OSError, ValueError):
            store = None
        return {"error": KEYCHAIN if store in ("keyring", "auto") else "Codex is signed out: sign in with `codex login`."}
    except (OSError, ValueError) as exc:
        return {"error": f"Codex's sign-in file is unreadable: {exc}"}
    tokens = stored.get("tokens") if isinstance(stored, dict) else None
    token = tokens.get("access_token") if isinstance(tokens, dict) else None
    if not isinstance(token, str) or not token:
        return {"error": API_KEY if isinstance(stored, dict) and stored.get("OPENAI_API_KEY") else
                "Codex is signed out: sign in with `codex login`."}
    account = tokens.get("account_id") or (_claims(token).get("https://api.openai.com/auth") or {}).get(
        "chatgpt_account_id")
    if not isinstance(account, str) or not account:
        return {"error": "Codex's sign-in names no ChatGPT account: sign in again with `codex login`."}
    return {"access_token": token, "account_id": account}


def worker_sign_in() -> dict:
    """What a Codex worker signs in with: the operator's current access token and account, never the refresh token,
    which stays in their Codex folder. When less than half the token's lifetime remains, Codex, outside every
    sandbox, refreshes the sign-in first (`account/read`); one lock covers this and the quota readings, which also
    run Codex on that folder, and the sign-in is read again inside it, so workers asking together cause one refresh
    and a worker cannot force one. `error` says why there is no token to hand over."""
    with _SIGN_IN:
        found = _stored_sign_in()
        if found.get("access_token") and _stale(found["access_token"]):
            try:
                _talk(20.0, _REFRESH)
            except Exception:  # noqa: BLE001 — a token still valid serves; an expired one lapses at the provider
                pass
            found = _stored_sign_in()
    return found


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
             str(config.project_path(project).resolve()): "read", **dict.fromkeys(denied(), "deny")}
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
def codex_exec(prompt: str, *, cwd: Path, unit: str, model: str | None = None, timeout: int = 900,
               effort: str | None = None,
               extra_env: dict | None = None, resume: str | None = None, on_start=None,
               sandbox_settings: list[str], ignore_user_config: bool = True, on_session=None,
               images: list[dict] | tuple = ()) -> dict:
    """One synchronous Codex turn (L3) in Codex's own sandbox under `sandbox_settings`, prompt on stdin (verified
    with codex 0.152). `codex exec resume <thread> -` continues the thread. The turn runs as the job `unit`, so altd's
    `NoNewPrivileges` hardening never reaches the nested bwrap, and a timeout stops the whole tree.
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
    for setting in sandbox_settings:
        cmd += ["-c", setting]
    if effort:
        cmd += ["-c", f'model_reasoning_effort="{effort}"']
    cmd += [resume, "-"] if resume else ["-"]
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
    # Every message the turn wrote, in order and a paragraph each, as a Claude turn keeps its text around tool calls.
    text = "\n\n".join(message.strip() for message in common._agent_messages(events) if message.strip())
    result = _codex_outcome(events, proc.returncode, stdout, stderr, text=text,
                            resume=resume, model=model, metadata=metadata)
    result["context"] = _codex_session_context(result.get("reported_session_id"), started_at,
                                              _codex_home(common.launch_env(extra_env)))
    return result


def prepare_worker(name, prompt, *, cwd, scratch, job_root, persona, model, settings, effort, resume, images):
    """A Codex worker in Altitude's sandbox, as a Claude worker, with Codex's own sandbox off and approvals never: its
    own Codex folder (`l2-engine/codex`) beside the same roots, and the operator's Codex folder hidden from it except
    the installed program, the entries its folder links to and a conversation it resumes for the first time. Its
    launch input carries the sign-in (`worker_sign_in`)."""
    cmd = [config.CODEX_BIN, "app-server", "--strict-config", "-c", 'default_permissions=":danger-full-access"',
           "-c", 'approval_policy="never"']
    if effort is not None:
        cmd += ["-c", f'model_reasoning_effort="{effort}"']
    git_writable, read_only = git_policy.worker_git_access(cwd)
    home, operator = Path(job_root) / "codex", operator_home()
    copies = _first_resume(home, operator, resume)
    reopened = (*_program(operator), *(operator / name for name in (*LINKED, SKILLS) if os.path.lexists(operator / name)),
                *copies)
    text = (Path(persona).read_text() + "\n\n" if persona and not resume else "") + prompt
    content = common._engine_input("codex", text, images)
    return (cmd, content, (Path(cwd), *git_writable, scratch, home), (*read_only, *reopened), {"CODEX_HOME": str(home)},
            {"operator": str(operator), "copies": list(map(str, copies)), "sign_in": worker_sign_in()})


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


#: Codex's own refresh of the operator's sign-in (`worker_sign_in`).
_REFRESH = (_REQUESTS[0], {"jsonrpc": "2.0", "id": 2, "method": "account/read", "params": {"refreshToken": True}})


def _talk(timeout: float, requests: tuple = _REQUESTS) -> list[str]:
    """Send the two requests and return stdout through response 2, ignoring notifications."""
    proc = _spawn()
    lines: list[str] = []
    try:
        if proc.stdin is None or proc.stdout is None:
            raise RuntimeError("app-server pipes unavailable")
        wire = b"".join((json.dumps(request, separators=(",", ":")) + "\n").encode() for request in requests)
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
        with _SIGN_IN:
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


def token_sources(home, owners, register, job_root):
    """A thread's records in the job's own Codex folder, and in the operator's only for a thread the job's folder does
    not hold: one recorded before jobs had folders of their own. A copied record counts once."""
    sources, pending, claimed = [], False, set()
    for root in [home] if home else [*([Path(job_root) / "codex"] if job_root else []), operator_home()]:
        index, more = _token_rollouts(Path(root))
        pending = pending or more
        wanted = set(owners)
        while True:
            children = {meta["session_id"] for meta in index.values() if meta["parent"] in wanted}
            if children <= wanted:
                break
            wanted |= children
        found = set()
        for name, meta in index.items():
            sid = meta["session_id"]
            if sid in wanted and sid not in claimed:
                found.add(sid)
                register(sid, meta["parent"], parentage="thread",
                         **{key: meta[key] for key in ("forked", "fork_time", "fork_ordinal")})
                sources.append((Path(name), sid, "rollout"))
        claimed |= found
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
    auth |= any(re.match(rf"unexpected status 401\b|Not logged in$|{re.escape(SIGNED_OUT)}", message)
                for message in said) or bool(re.search(
        r"Your access token could not be refreshed|You do not have access to Codex|"
        r"This account is not currently authorized to use Codex in this workspace", text))
    return auth, model_missing


def worker_events(path):
    return common._read_events(path)


INSTALL_COMMAND = 'npm install -g @openai/codex'
SIGN_IN = (('login', 'status'), 'codex login')
TOKEN_SETUP = None  # Altitude's runs use the CLI's own sign-in
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


def session_context_source(session_id, job_root):
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


def review_launch_env(runtime):
    return {}


CONFINEMENT = 'no Codex sandbox inside it'


def delivered_context(record):
    return []


def observe_worker_model(record, events, record_path):
    if not record.get("engine_model"):
        metadata = _codex_session_model(common._event_session(events), record["started_at"],
                                       Path(record.get("codex_home") or _codex_home(common.launch_env())))
        if metadata:
            record.update(metadata)
            S.write_json(record_path, record)
