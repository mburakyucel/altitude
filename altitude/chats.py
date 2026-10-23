"""Agent chats: conversations the operator drives with one of the project's coding agents, from their browser, with no
brief, proposal or pull request (docs/ARCHITECTURE.md#agent-chats).

A chat is a record in the chats folder beside Altitude's home (`~/.altitude-chats/<project>/<id>/chat.json`), which
every agent's sandbox hides: its name, the engine and model it answers with, its engine session, its copy of the code and its conversation. Each message the operator sends waits until the
chat's previous turn ends, and the messages waiting then go together as its next turn: a worker job of the chat role,
restricted as a routine run is but committing on its own branch (`engines.start_chat_turn`), in the chat's own copy of
the project, a worktree on the `chat-<id>` branch made from the fetched main at its first turn, resuming the chat's one
session from its own engine folder. A thread of the service (`run`) starts each turn, follows its worker and records
the answer; the worker's job outlives a restart of the service, whose tick follows it again. Make a task (`make_task`)
makes a task from the conversation that starts from the copy's changes. A chat changes only from a paired browser and
reads only there and in the operator's terminal (`alt chats`).
"""
from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import config, engines, git_policy, images, route, state as S, steps as step_lines, usage

#: A chat's name, a message's text, the requests a chat remembers and a task's title.
NAME, TEXT, REQUESTS, TITLE = 80, 20000, 200, 120
#: How often a turn's thread reads its worker, and how often it records the session's context.
WATCH, CONTEXT = 1.0, 30.0
ID = re.compile(r"[0-9a-f]{12}")
ACTOR = "chat"
FROM_BROWSER = "Agent chats change only from your own browser."
PAGE_FROM_BROWSER = "An agent chat opens only in your own browser or terminal."
ANSWERING = ("starting", "answering")
#: Ahead of the first message after the copy was made again from main, so the session does not count on earlier work.
FRESH = ("(Altitude: your copy of the code was made again from main since your last turn; earlier changes and "
         "commits in it are gone.)\n\n")


class Refused(ValueError):
    """A change the chat does not allow now, as the page says it."""


class Missing(KeyError):
    """No chat by that reference."""

    def __str__(self) -> str:
        return str(self.args[0])


# ---- the record ---------------------------------------------------------------------------------------------------

def home(project: str) -> Path:
    return config.CHATS / config.require_project_name(project)


def folder(project: str, chat_id: str) -> Path:
    """A chat's own folder: its record (`chat.json`), its engine folder (`l2-engine`), which keeps its session, and
    its agent's scratch folder (`scratch`)."""
    if not isinstance(chat_id, str) or not ID.fullmatch(chat_id):
        raise Missing("There is no such chat.")
    return home(project) / chat_id


def worktree(project: str, chat_id: str) -> Path:
    return config.task_worktree(project, f"chat-{chat_id}")


def branch(chat_id: str) -> str:
    return f"chat-{chat_id}"


def _scratch(project: str, chat_id: str) -> Path:
    return folder(project, chat_id) / "scratch"


def load(project: str, chat_id: str) -> dict:
    record = S.read_json(folder(project, chat_id) / "chat.json", None)
    if not isinstance(record, dict):
        raise Missing("There is no such chat.")
    return record


def _save(project: str, record: dict) -> None:
    record["updated"] = S.now()
    S.write_json(folder(project, record["id"]) / "chat.json", record)


def records(project: str) -> list[dict]:
    root = home(project)
    if not root.is_dir():
        return []
    found = []
    for path in sorted(root.glob("*/chat.json")):
        record = S.read_json(path, None) if ID.fullmatch(path.parent.name) else None
        if isinstance(record, dict):
            found.append(record)
    return found


def _turn(record: dict) -> dict | None:
    """The chat's turn in progress, if one is."""
    return next((t for t in reversed(record["turns"]) if t["state"] in ANSWERING), None)


def _waiting(record: dict) -> list[dict]:
    return [m for m in record["messages"] if m.get("turn") is None]


def _next(record: dict) -> int:
    """The conversation's next place: messages, turns and system lines are shown in the order they were made."""
    record["seq"] = record.get("seq", 0) + 1
    return record["seq"]


def _event(record: dict, kind: str, **fields) -> None:
    record["events"].append({"at": S.now(), "kind": kind, "seq": _next(record), **fields})


def _seen(record: dict, request) -> bool:
    """Whether this request already acted on the chat; a repeated tap or a replayed request changes nothing."""
    _request(request)
    if request in record["requests"]:
        return True
    record["requests"][:] = [*record["requests"][-(REQUESTS - 1):], request]
    return False


def _request(request) -> str:
    if not isinstance(request, str) or not re.fullmatch(r"[0-9A-Za-z-]{8,64}", request):
        raise Refused("This request has no identity; reload the page.")
    return request


def _text(value) -> str:
    text = str(value or "").strip()
    if len(text) > TEXT:
        raise Refused(f"A message is at most {TEXT} characters.")
    return text


def _name_from(text: str) -> str:
    line = next((line.strip() for line in text.splitlines() if line.strip()), "") or "Images"
    return line if len(line) <= NAME else line[:NAME - 1].rstrip() + "…"


def _choice(value) -> dict | None:
    """The model pill's choice (`config.validate_choice`): an engine, its model, an effort, or None for New tasks'
    default."""
    try:
        config.validate_choice(value)
    except ValueError as exc:
        raise Refused(f"Choose a model Altitude offers: {exc}.") from None
    return value and {key: value.get(key) for key in ("engine", "model", "effort")}


def _images(project: str, message_id: str, uploads, image_ids) -> list[dict]:
    if uploads and image_ids:
        raise Refused("Select images or retry saved images, not both.")
    if uploads:
        return images.store(project, uploads, message_id=message_id)
    return images.lookup(project, image_ids) if image_ids else []


# ---- the operator's changes ---------------------------------------------------------------------------------------

def new(project: str, text, request, *, uploads=None, image_ids=None, choice=None) -> dict:
    """A chat made by its first message, named from its first line."""
    text = _text(text)
    if not text and not (uploads or image_ids):
        raise Refused("Write a message first.")
    choice, request = _choice(choice), _request(request)
    with S.project_lock(project):
        made = next((r for r in records(project) if r.get("request") == request), None)
        if made:
            return {"id": made["id"]}
        chat_id = uuid.uuid4().hex[:12]
        message = {"id": uuid.uuid4().hex, "at": S.now(), "text": text, "turn": None, "seq": 1}
        refs = _images(project, message["id"], uploads, image_ids)
        if refs:
            message["images"] = refs
        record = {"id": chat_id, "name": _name_from(text), "created": message["at"], "updated": message["at"],
                  "request": request, "choice": choice, "option": None, "engine": None, "model": None,
                  "session": None, "base": None, "archived": None, "messages": [message], "turns": [],
                  "events": [], "requests": [request], "seq": 1}
        folder(project, chat_id).mkdir(parents=True, exist_ok=True)
        _save(project, record)
    return {"id": chat_id}


def _change(project: str, chat_id: str, request, apply) -> dict:
    with S.project_lock(project):
        record = load(project, chat_id)
        if not _seen(record, request):
            apply(record)
        _save(project, record)
    return {"id": chat_id}


def _open(record: dict) -> None:
    if record["archived"]:
        raise Refused("This chat is archived. Reopen it first.")


def message(project: str, chat_id: str, text, request, *, uploads=None, image_ids=None) -> dict:
    """A message for the chat's next turn: now when no turn runs, else when the running one ends."""
    text = _text(text)
    if not text and not (uploads or image_ids):
        raise Refused("Write a message first.")

    def apply(record):
        _open(record)
        row = {"id": uuid.uuid4().hex, "at": S.now(), "text": text, "turn": None, "seq": _next(record)}
        refs = _images(project, row["id"], uploads, image_ids)
        if refs:
            row["images"] = refs
        record["messages"].append(row)
    return _change(project, chat_id, request, apply)


def remove(project: str, chat_id: str, message_id, request) -> dict:
    """Take back a message still waiting for its turn."""
    def apply(record):
        row = next((m for m in _waiting(record) if m["id"] == message_id), None)
        if not row:
            raise Refused("That message has already gone to the agent.")
        record["messages"].remove(row)
    return _change(project, chat_id, request, apply)


def retry(project: str, chat_id: str, request) -> dict:
    """Send again the messages of the chat's last turn, which could not answer or was cut off."""
    def apply(record):
        _open(record)
        last = record["turns"][-1] if record["turns"] else None
        if not last or last["state"] not in ("failed", "cut"):
            raise Refused("There is nothing to retry.")
        record["turns"].remove(last)
        for row in record["messages"]:
            if row.get("turn") == last["n"]:
                row["turn"] = None
    return _change(project, chat_id, request, apply)


def rename(project: str, chat_id: str, name, request) -> dict:
    name = " ".join(str(name or "").split())
    if not 1 <= len(name) <= NAME:
        raise Refused(f"A name is 1 to {NAME} characters.")
    return _change(project, chat_id, request, lambda record: record.update(name=name))


def set_choice(project: str, chat_id: str, choice, request) -> dict:
    """The model later turns answer with; None returns to New tasks' default."""
    choice = _choice(choice)

    def apply(record):
        _open(record)
        record["choice"] = choice
    return _change(project, chat_id, request, apply)


def stop(project: str, chat_id: str, request) -> dict:
    """End the running turn; messages waiting for it go next."""
    with S.project_lock(project):
        record = load(project, chat_id)
        if _seen(record, request):
            return {"id": chat_id}
        turn = _turn(record)
        if not turn:
            raise Refused("No reply is running.")
        turn["stop"] = True
        _save(project, record)
    if turn.get("worker"):  # one still starting is stopped once its worker is known (`_launch`)
        engines.stop_l2_worker(turn["engine"], turn["worker"], job_root=folder(project, chat_id) / "l2-engine")
    return {"id": chat_id}


def make_task(project: str, chat_id: str, title, text, request) -> dict:
    """Make a task from the chat: the operator's words are its brief, the conversation follows them as quoted context,
    and its branch starts from the copy's changes, committed as they are, or from main when the chat changed nothing.
    The copy then starts again from main at the next turn, so its changes live in the task alone."""
    from . import tasks
    title, text, request = " ".join(str(title or "").split()), _text(text), _request(request)
    if not 1 <= len(title) <= TITLE:
        raise Refused(f"A title is 1 to {TITLE} characters.")
    if not text:
        raise Refused("Say what the task should deliver.")
    key = (project, chat_id)
    with _running_lock:
        if key in _making:
            raise Refused("A task is already being made from this chat.")
        _making.add(key)
    try:
        with S.project_lock(project):
            record = load(project, chat_id)
            if request in record["requests"]:
                return {"id": chat_id}
            _open(record)
            if _turn(record) or _waiting(record):
                raise Refused("Make a task when the reply has ended.")
            moved = changes(project, chat_id)
        start = _snapshot(project, record) if moved else None  # no turn starts while it is made
        try:
            task = tasks.new(project, title, text, actor=config.OPERATOR_ACTOR, start=start,
                             context=_context(record, moved))
        except tasks.TransitionError as exc:
            raise Refused(f"The task could not be made: {exc}") from None
        with S.project_lock(project):
            record = load(project, chat_id)
            _seen(record, request)
            _event(record, "task", slug=task["slug"], moved=moved)
            if moved:
                record["base"] = None
            _save(project, record)
        if moved:
            _remove_copy(project, chat_id)
    finally:
        with _running_lock:
            _making.discard(key)
    return {"id": chat_id}


def _snapshot(project: str, record: dict) -> str:
    """The copy's work as one commit: its last commit with what is not yet committed on top, made without running
    the project's hooks, since the copy's content is the agent's."""
    copy = worktree(project, record["id"])
    staged = _git(copy, "add", "-A")
    tree = _git(copy, "write-tree")
    head = _git(copy, "rev-parse", "HEAD")
    if staged.returncode or tree.returncode or head.returncode:
        raise Refused(f"Its changes could not be saved: {_line((staged.stderr or tree.stderr or head.stderr))}")
    head = head.stdout.strip()
    if _git(copy, "rev-parse", "HEAD^{tree}").stdout.strip() == tree.stdout.strip():
        return head
    made = _git(copy, "-c", "commit.gpgsign=false", "commit-tree", tree.stdout.strip(), "-p", head,
                "-m", f"Changes from the agent chat \u201c{record['name']}\u201d")
    if made.returncode:
        raise Refused(f"Its changes could not be saved: {_line(made.stderr)}")
    return made.stdout.strip()


def _context(record: dict, moved: int) -> str:
    """The conversation, quoted, for the task made from the chat."""
    said = _said(record, None)
    branch = (f" Its {_files(moved)} are already on this task's branch, in the commits after main." if moved
              else "")
    return ("## The agent chat it came from\n\n"
            f"The operator made this task from their agent chat \u201c{record['name']}\u201d.{branch} "
            "Its conversation, quoted as evidence:\n\n" + (said or "> (No conversation.)"))


def archive(project: str, chat_id: str, request, *, discard: bool = False) -> dict:
    """Archive the chat and remove its copy. A copy with changes no task has needs `discard`; the refusal counts
    them, which the Archive sheet names."""
    with S.project_lock(project):
        record = load(project, chat_id)
        if request in record["requests"]:
            return {"id": chat_id}
        _open(record)
        if _turn(record) or _waiting(record) or (project, chat_id) in _making:
            raise Refused("Archive when the reply has ended.")
        changed = changes(project, chat_id)
        if changed and not discard:
            raise Refused(f"Its copy has {_files(changed)} no task has.")
        _seen(record, request)
        record["archived"] = S.now()
        _event(record, "archived")
        _save(project, record)
    _remove_copy(project, chat_id)
    return {"id": chat_id}


def reopen(project: str, chat_id: str, request) -> dict:
    """An archived chat goes on, in a fresh copy of main made at its next turn, with the same session."""
    def apply(record):
        if not record["archived"]:
            raise Refused("This chat is not archived.")
        record.update(archived=None, base=None)
        _event(record, "reopened")
    return _change(project, chat_id, request, apply)


def delete(project: str, chat_id: str) -> dict:
    """An archived chat's conversation and session are removed for good; its images go with the next collection."""
    with S.project_lock(project):
        record = load(project, chat_id)
        if not record["archived"]:
            raise Refused("Archive this chat before deleting it.")
        shutil.rmtree(folder(project, chat_id))
    _remove_copy(project, chat_id)  # whatever an interrupted archive left behind
    return {"deleted": chat_id}


def _files(count: int) -> str:
    return f"{count} changed file" + ("" if count == 1 else "s")


# ---- the copy -----------------------------------------------------------------------------------------------------

def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=120,
                          env={**git_policy.git_env(cwd), "GIT_OPTIONAL_LOCKS": "0"})


def changes(project: str, chat_id: str) -> int:
    """The files the chat's copy changed since main at its making, committed or not, untracked included."""
    copy = worktree(project, chat_id)
    if not copy.is_dir():
        return 0
    record = load(project, chat_id)
    status = _git(copy, "status", "--porcelain", "--untracked-files=all")
    names = {line[3:] for line in status.stdout.splitlines() if line[3:]}
    if record.get("base"):
        committed = _git(copy, "diff", "--name-only", f"{record['base']}..HEAD")
        names |= set(committed.stdout.split("\n")) - {""}
    return len(names)


def _make_copy(project: str, chat_id: str, base: str | None) -> str:
    """The chat's copy on its own branch: the branch again when only the folder went missing, so its commits stay;
    otherwise the fetched main, for a new chat or one reopened."""
    from . import dispatch
    repo = config.project_path(project)
    if base and not _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch(chat_id)}").returncode:
        _git(repo, "worktree", "prune")
        made = _git(repo, "worktree", "add", str(worktree(project, chat_id)), branch(chat_id))
        if made.returncode:
            raise RuntimeError(f"git worktree add failed: {(made.stderr or made.stdout).strip()[:300]}")
        return base
    with dispatch.publication_settlement(project):
        sha = git_policy.fetch_origin(repo, "main")
    copy = worktree(project, chat_id)
    _remove_copy(project, chat_id)
    copy.parent.mkdir(parents=True, exist_ok=True)
    made = _git(repo, "worktree", "add", "--no-track", "-B", branch(chat_id), str(copy), sha)
    if made.returncode:
        raise RuntimeError(f"git worktree add failed: {(made.stderr or made.stdout).strip()[:300]}")
    return sha


def _remove_copy(project: str, chat_id: str) -> None:
    """The copy, its branch and its scratch folder; the conversation and session stay."""
    repo, copy = config.project_path(project), worktree(project, chat_id)
    if copy.exists():
        _git(repo, "worktree", "remove", "--force", str(copy))
        shutil.rmtree(copy, ignore_errors=True)
        _git(repo, "worktree", "prune")
    _git(repo, "branch", "-D", branch(chat_id))
    shutil.rmtree(_scratch(project, chat_id), ignore_errors=True)


# ---- turns --------------------------------------------------------------------------------------------------------

_running: set[tuple[str, str]] = set()
#: Chats a task is being made from, whose next turn waits until it is made (`make_task`).
_making: set[tuple[str, str]] = set()
_running_lock = threading.Lock()


def _due(record: dict) -> bool:
    return not record["archived"] and bool(_turn(record) or _waiting(record))


def due(project: str) -> list[str]:
    """The project's chats whose turn a thread should follow or start: one in progress or messages waiting."""
    return [r["id"] for r in records(project) if _due(r)]


def run(project: str, chat_id: str) -> None:
    """Start the chat's next turn while messages wait and follow each until its worker ends. One thread per chat; the
    service's tick starts one again after a restart."""
    key = (project, chat_id)
    while True:
        with _running_lock:
            if key in _running:
                return
            _running.add(key)
        try:
            while True:
                turn = _turn(load(project, chat_id))
                if turn and turn.get("worker"):
                    _follow(project, chat_id, turn["n"])
                    continue
                if turn:  # claimed and never launched: a restart interrupted its start
                    _end(project, chat_id, turn["n"], "cut", None)
                    continue
                started = _start(project, chat_id)
                if not started:
                    break
        finally:
            with _running_lock:
                _running.discard(key)
        # A message sent as this thread ended found it still alive, so no other thread started for it: take it now.
        if started is None or not _due(load(project, chat_id)):
            return


def _option(record: dict, project: str) -> dict:
    """The engine, model and effort the turn uses: the one the chat answers with, unless its choice changed since."""
    if record.get("option") and record.get("option_for") == record["choice"]:
        return record["option"]
    choice = record["choice"] or {}
    picked = route.pick_engine("l2", forced=choice.get("engine"), model=choice.get("model"),
                               effort=choice.get("effort"), project=config.project(project))
    return {"engine": picked.get("engine"), "model": picked.get("model"), "effort": picked.get("effort")}


def _start(project: str, chat_id: str) -> bool | None:
    """Claim the waiting messages as the next turn and start its worker; False when nothing waits, None when the service
    is restarting, whose tick starts it afterwards."""
    with config.provider_admission() as held, config.restart_lock() as ready:
        if held or not ready or config.restart_in_progress():
            return None
        with S.project_lock(project):
            with _running_lock:
                making = (project, chat_id) in _making
            if making:  # Make a task's request starts it afterwards
                return None
            record = load(project, chat_id)
            waiting = _waiting(record)
            if record["archived"] or not waiting or _turn(record):
                return False
            option = _option(record, project)
            n = record["turns"][-1]["n"] + 1 if record["turns"] else 1
            for row in waiting:
                row["turn"] = n
            record["turns"].append({"n": n, "seq": _next(record), "state": "starting", "worker": None,
                                    "started": S.now(), "ended": None,
                                    "engine": option["engine"], "model": option["model"], "answer": None,
                                    "error": None})
            if not option["engine"]:
                _close(record["turns"][-1], "failed", "No engine is available to answer")
                _save(project, record)
                return True
            record.update(option=option, option_for=record["choice"])
            _save(project, record)
        try:
            launched = _launch(project, chat_id, n, option)
        except git_policy.FetchError as exc:  # the reply says so, and Retry tries again
            launched = {"worker": None, "error": _line(f"Its copy of main could not be made: {exc}")}
        except Exception as exc:
            _end(project, chat_id, n, "failed", _line(str(exc)))
            raise
    if not launched.get("worker"):
        _end(project, chat_id, n, "failed", launched["error"])
    return True


def _launch(project: str, chat_id: str, n: int, option: dict) -> dict:
    """Make the copy if the chat has none, then start the turn's worker: resuming the chat's session when the engine
    answering is the one that has it, otherwise a new session given the conversation so far."""
    record = load(project, chat_id)
    base = record.get("base")
    if not base or not worktree(project, chat_id).is_dir():
        base = _make_copy(project, chat_id, base)
        _record(project, chat_id, base=base)
    fresh = base != record.get("base") and any(t["n"] < n for t in record["turns"])
    sent = [m for m in record["messages"] if m.get("turn") == n]
    resume = record["session"] if record["engine"] == option["engine"] else None
    result = _turn_worker(project, chat_id, n, option, sent, resume, record, fresh)
    if resume and result.get("returncode") and not result.get("rejection"):
        resume = None  # the engine lost the session: a new one is given the conversation so far
        result = _turn_worker(project, chat_id, n, option, sent, None, record, fresh)
    worker = result.get("agent") or {}
    if result.get("rejection"):
        route.note_rejection(option, result["rejection"])
    if result.get("returncode") or not worker.get("id") or not worker.get("sessionId"):
        return {"worker": None, "error": _line(result.get("stderr") or worker.get("detail") or "its worker did not start")}
    with S.project_lock(project):
        current = load(project, chat_id)
        turn = next(t for t in current["turns"] if t["n"] == n)
        turn.update(worker=worker["id"], state="answering", engine_model=worker.get("engine_model"))
        if not resume:  # the turn's system line: the chat's first session, or a new one
            turn["opened"] = "new_session" if current["session"] else "started"
        current.update(engine=option["engine"], model=option["model"], session=worker["sessionId"],
                       engine_model=worker.get("engine_model"))
        stopped = bool(turn.get("stop"))
        _save(project, current)
    if stopped:  # Stop was pressed while it started
        engines.stop_l2_worker(option["engine"], worker["id"], job_root=folder(project, chat_id) / "l2-engine")
    return {"worker": worker["id"]}


def _turn_worker(project: str, chat_id: str, n: int, option: dict, sent: list[dict], resume: str | None,
                 record: dict, fresh: bool) -> dict:
    text = "\n\n".join(m["text"] for m in sent if m["text"]) or "(Images only.)"
    if fresh:
        text = FRESH + text
    if not resume and any(t["n"] < n for t in record["turns"]):
        text = _history(record, n) + text
    refs = [ref for m in sent for ref in m.get("images", [])]
    return engines.start_chat_turn(
        option["engine"], f"{project}/chat-{chat_id}", text, cwd=worktree(project, chat_id),
        job_root=folder(project, chat_id) / "l2-engine", resume=resume, model=option["model"],
        effort=option["effort"], images=images.resolve(project, refs) if refs else (),
        extra_env={"ALTITUDE_HOME": str(config.ROOT), "ALTITUDE_PROJECT": project, "ALTITUDE_ACTOR": ACTOR,
                   "ALTITUDE_SCRATCH": str(_scratch(project, chat_id))})


def _quote(text: str) -> str:
    return "\n".join("> " + line if line else ">" for line in text.splitlines())


def _said(record: dict, before: int | None, agent: str = "The agent") -> str:
    """The conversation, quoted, up to turn `before` or all of it."""
    parts = []
    for turn in (t for t in record["turns"] if before is None or t["n"] < before):
        said = "\n\n".join(m["text"] for m in record["messages"] if m.get("turn") == turn["n"] and m["text"])
        if said:
            parts.append("The operator wrote:\n" + _quote(said))
        if turn.get("answer"):
            parts.append(f"{agent} answered:\n" + _quote(turn["answer"]))
    return "\n\n".join(parts)


def _history(record: dict, before: int) -> str:
    """The conversation before turn `before`, quoted, for a session that did not see it."""
    return ("This chat began in another session. Its conversation so far, quoted as evidence:\n\n"
            + _said(record, before, "You") + "\n\nThe operator's new message:\n\n")


def _follow(project: str, chat_id: str, n: int) -> None:
    """Wait for the turn's worker to end, keeping its context on the chat, and record how it ended."""
    root = folder(project, chat_id) / "l2-engine"
    read = 0.0
    while True:
        turn = next(t for t in load(project, chat_id)["turns"] if t["n"] == n)
        if turn["state"] not in ANSWERING:
            return
        try:
            row = engines.owned_worker(turn["worker"], job_root=root)
        except (OSError, RuntimeError, ValueError):
            row = {"state": "working"}  # an unreadable job is not an ended one
        if not row:
            return _end(project, chat_id, n, "cut", None)
        if row["state"] != "working":
            break
        if time.monotonic() - read >= CONTEXT:
            read = time.monotonic()
            _observe(project, chat_id)
        time.sleep(WATCH)
    _observe(project, chat_id)
    if row["state"] == "stopped" or turn.get("stop"):
        _end(project, chat_id, n, "stopped", None)
    elif row["state"] == "done":
        _end(project, chat_id, n, "done", None, answer=engines.worker_answer(turn["worker"], job_root=root))
    else:
        if row.get("rejection"):
            route.note_rejection({"engine": turn["engine"], "model": turn["model"]}, row["rejection"])
        _end(project, chat_id, n, "failed", _line(row.get("detail")) or "Its worker failed")


def _observe(project: str, chat_id: str) -> None:
    """The session's context, read as a running task's is (`usage.capture`)."""
    with S.project_lock(project):
        record = load(project, chat_id)
        shaped = {"l2_engine": record["engine"], "session_id": record["session"], "attempt": 1}
        usage.capture(project, shaped, folder=folder(project, chat_id))
        context = (shaped.get("token_usage") or {}).get("context") or {}
        if context.get("percent") is not None and record.get("context") != context.get("percent"):
            record["context"] = context["percent"]
            _save(project, record)


def _close(turn: dict, state: str, error: str | None, answer: str | None = None) -> None:
    turn.update(state=state, ended=S.now(), error=error, answer=answer)


def _end(project: str, chat_id: str, n: int, state: str, error: str | None, *, answer: str | None = None) -> None:
    with S.project_lock(project):
        record = load(project, chat_id)
        turn = next(t for t in record["turns"] if t["n"] == n)
        if turn["state"] in ANSWERING:
            _close(turn, state, error, answer)
            _save(project, record)


def _record(project: str, chat_id: str, **fields) -> None:
    with S.project_lock(project):
        record = load(project, chat_id)
        record.update(fields)
        _save(project, record)


def _line(text: str | None) -> str:
    return " ".join(str(text or "").split())[:300]


def service_started() -> None:
    """A turn running when the service stopped goes on in its own job; its chat says Altitude restarted."""
    for project in config.load_projects():
        with S.project_lock(project):
            for record in records(project):
                if (_turn(record) or {}).get("worker"):  # one never launched is cut off (`run`)
                    _event(record, "restarted")
                    _save(project, record)


# ---- reading ------------------------------------------------------------------------------------------------------

_steps: dict[str, tuple[tuple, list[dict]]] = {}
_steps_lock = threading.Lock()


def _turn_steps(project: str, chat_id: str, turn: dict) -> list[dict]:
    """What the turn's agent said and did, from its worker's output, read again only when that output grew."""
    if not turn.get("worker"):
        return []
    root = folder(project, chat_id) / "l2-engine"
    try:
        info = (root / f"{turn['worker']}.stdout.jsonl").stat()
    except OSError:
        return []
    mark = (info.st_ino, info.st_size, info.st_mtime_ns, turn["state"])
    with _steps_lock:
        cached = _steps.get(turn["worker"])
        if cached and cached[0] == mark:
            return cached[1]
    rows = [row for event in engines.worker_events(turn["worker"], job_root=root)
            for row in engines.transcript_rows(turn["engine"], event, str(worktree(project, chat_id)))]
    found = step_lines.lines(rows, turn["state"] not in ANSWERING)
    answer = (turn.get("answer") or "").strip()
    if answer and found and found[-1]["kind"] == "say" and found[-1]["text"] == answer:
        found.pop()  # the reply shows it
    with _steps_lock:
        _steps[turn["worker"]] = (mark, found)
    return found


def _state(record: dict) -> str:
    turn = _turn(record)
    if record["archived"]:
        return "archived"
    if turn:
        return turn["state"]
    if _waiting(record):
        return "starting"
    last = record["turns"][-1] if record["turns"] else None
    return "failed" if last and last["state"] in ("failed", "cut") else "idle"


def _latest(record: dict) -> dict:
    """The newest of the operator's message and the agent's reply, which orders and describes the chat's row."""
    wrote = record["messages"][-1]["at"] if record["messages"] else record["created"]
    replied = max((t["ended"] for t in record["turns"] if t.get("ended") and t["state"] == "done"), default=None)
    return {"at": max(wrote, replied or ""), "by": "agent" if replied and replied >= wrote else "you"}


def summary(record: dict) -> dict:
    """A chat as Work's list shows it."""
    return {"id": record["id"], "name": record["name"], "state": _state(record), "engine": record["engine"],
            "model": record.get("engine_model") or record["model"], "archived": record["archived"],
            "created": record["created"], "latest": _latest(record)}


def listing(project: str) -> dict:
    rows = sorted((summary(r) for r in records(project)), key=lambda r: r["latest"]["at"], reverse=True)
    return {"chats": rows}


def view(project: str, chat_id: str) -> dict:
    """One chat's page: its row, its conversation as items in order (system lines, the operator's messages, each
    turn with its steps and reply), and its copy's changed files."""
    record = load(project, chat_id)
    items = [{"kind": "event", "event": e["kind"], **{k: v for k, v in e.items() if k != "kind"}}
             for e in record["events"]]
    items += [{"kind": "event", "event": t["opened"], "at": t["started"], "seq": t["seq"] - .5,
               "engine": t["engine"], "model": t.get("engine_model") or t["model"]}
              for t in record["turns"] if t.get("opened")]
    items += [{"kind": "you", "id": m["id"], "seq": m["seq"], "at": m["at"], "text": m["text"],
               "images": m.get("images", []), "waiting": m.get("turn") is None} for m in record["messages"]]
    items += [{"kind": "turn", "at": t["started"], **{k: t.get(k) for k in (
        "n", "seq", "state", "started", "ended", "engine", "model", "engine_model", "answer", "error", "stop")},
               "steps": _turn_steps(project, chat_id, t)} for t in record["turns"]]
    items.sort(key=lambda item: item["seq"])
    copy = not record["archived"] and worktree(project, chat_id).is_dir()
    return {**summary(record), "choice": record["choice"], "effort": (record.get("option") or {}).get("effort"),
            "items": items, "changes": changes(project, chat_id) if copy else None,
            "context": record.get("context")}


def monitor_sessions(project: str, *, now: datetime | None = None) -> list[dict]:
    """Each chat whose reply is running, as a Monitor session row."""
    now = now or datetime.now(timezone.utc)
    rows = []
    for record in records(project):
        turn = _turn(record)
        if turn and turn.get("worker"):
            minutes = int((now - datetime.fromisoformat(turn["started"])).total_seconds() // 60)
            rows.append({"kind": "chat", "project": project, "chat_id": record["id"], "name": record["name"],
                         "session_id": record["session"], "state": "running", "engine": turn["engine"],
                         "model": turn.get("engine_model") or turn["model"], "context_percent": record.get("context"),
                         "minutes": minutes})
    return rows
