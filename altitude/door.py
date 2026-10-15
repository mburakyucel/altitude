"""The task door: how a task worker's own processes change Altitude's records.

A worker's `alt`, its landing, its checkpoint hooks and its engine driver run inside the worker's job. Each record change
they make is one short HTTPS request to altd, which admits it only from a process in the task's current worker job
and applies it as that task's L2, with the project, task and attempt its record names. The client sends the content
of every input file; altd never opens a path a worker names. Each request carries an identity, so a client asking
again after altd restarted gets the first answer instead of a second change (docs/ARCHITECTURE.md#task-door).
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from . import config, state as S

ENDPOINT = "task/door"
#: How long a client keeps asking while altd is unreachable, as during a restart that activates merged changes.
WAIT = 600
#: One request's own bound: the longest record change is an `alt` verb that reads GitHub, as `task done` does.
TIMEOUT = 150
#: A worker's request bound: the largest files it shows (`tasks.SHOWN_TOTAL_LIMIT` of images and one document) in
#: base64, with room for the verb's own text and input files.
MAX_REQUEST = 48 << 20
MAX_OUTPUT = 1 << 20
#: Where altd keeps each request's outcome: in the task's folder, which archiving moves with the task.
LEDGER = "door"
UNKNOWN = ("Altitude restarted while this request ran, so whether it took effect is unknown. Check the task with "
           "`alt task status` before asking again.")


class DoorError(RuntimeError):
    """altd refused the request or could not be reached."""


# --- The worker's end ----------------------------------------------------------------------------------------------

def follow_activation(script: str) -> None:
    """Run the activated version of `script`, a worker's `alt` or checkpoint hook, in place of this process once
    Altitude has activated another version than the worker launched with. A worker outlives activation, so each
    command it starts afterwards sends its requests in the form the running altd reads."""
    if os.environ.get("ALTITUDE_ACTOR") != "l2" or os.environ.get("ALTITUDE_DOOR") == "1":
        return
    path = Path(script).resolve()
    version = path.parent.parent
    if version.parent.name == ".altitude-source":
        current = version.parent / "current"         # a source deployment's export
    elif (version / "release.json").is_file():
        current = version.parent.parent / "current"  # an installed version
    else:
        return                                        # a development checkout
    target = current / path.relative_to(version)
    if current.resolve() != version and target.is_file():
        os.execv(sys.executable, [sys.executable, "-B", str(target), *sys.argv[1:]])


def request_body(request: dict) -> bytes:
    """A request to altd as sent, refused here when it is larger than altd reads."""
    data = json.dumps(request).encode()
    if len(data) > MAX_REQUEST:
        raise DoorError(f"this request is {len(data) / (1 << 20):.0f} MiB, more than the {MAX_REQUEST >> 20} MiB altd "
                        "takes from a task; send fewer or smaller files")
    return data


def call(kind: str, fields: dict, *, wait: float = WAIT, timeout: float = TIMEOUT) -> dict:
    """Ask altd to make one record change for this worker's own task and return its answer. A request altd never
    received, or whose answer a restart cut off, is asked again with the same identity until `wait` runs out."""
    import http.client
    import ssl
    import urllib.error
    import urllib.request

    from . import platform, tls
    try:
        data = request_body({"project": os.environ["ALTITUDE_PROJECT"], "slug": os.environ["ALTITUDE_TASK"],
                             "attempt": os.environ["ALTITUDE_ATTEMPT"], "kind": kind, "request": uuid.uuid4().hex,
                             **fields})
    except KeyError as exc:
        raise DoorError(f"the task door serves a task's own L2: {exc.args[0]} is not set") from None
    deadline = time.monotonic() + wait
    while True:
        try:
            found = tls.service()
            url = f"https://localhost:{found['port']}" if platform.containerized() else found["url"]
            request = urllib.request.Request(f"{url}/api/{ENDPOINT}", data=data,
                                             headers={"Content-Type": "application/json"})
            context = ssl.create_default_context(cadata=found["ca"]) if found["tls"] else None
            with urllib.request.urlopen(request, context=context, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                error = json.load(exc).get("error") or str(exc)
            except ValueError:
                error = str(exc)
            raise DoorError(error) from None
        except (tls.TLSFailure, OSError, http.client.HTTPException, ValueError) as exc:
            if isinstance(getattr(exc, "reason", None), ssl.SSLCertVerificationError) or time.monotonic() >= deadline:
                raise DoorError(f"altd unavailable: {exc}") from None
            time.sleep(min(2, max(0, deadline - time.monotonic())))


def encode(data: bytes) -> str:
    return base64.b64encode(data).decode()


def decode(sent: object) -> dict[str, bytes] | None:
    """Files a worker's `alt` sent as `{path: base64}`, as bytes."""
    if sent is None:
        return None
    if not isinstance(sent, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in sent.items()):
        raise ValueError("Sent files are a map of paths to their content.")
    return {path: base64.b64decode(data, validate=True) for path, data in sent.items()}


# --- altd's end ----------------------------------------------------------------------------------------------------

_running: dict[tuple[str, str, str], threading.Event] = {}
_running_lock = threading.Lock()


def once(project: str, slug: str, request: str, apply) -> dict:
    """Apply one request at most once. Its outcome is kept in the task's folder: a repeat waits for a request still
    running here and returns a finished one's answer; one a restart interrupted is refused, since its effect is
    unknown."""
    if not isinstance(request, str) or not request.isalnum() or not 8 <= len(request) <= 64:
        raise ValueError("A request identity is required.")
    key = (project, slug, request)
    path = S.task_dir(project, slug) / LEDGER / f"{request}.json"
    while True:
        with _running_lock:
            running = _running.get(key)
            if running is None:
                saved = _saved(path)
                if saved is not None:
                    if "error" in saved:
                        raise ValueError(saved["error"])
                    if "answer" not in saved:
                        raise ValueError(UNKNOWN)
                    return saved["answer"]
                _running[key] = threading.Event()
                break
        running.wait()
    try:
        _forget(path.parent)
        S.write_json(path, {"at": S.now()})
        try:
            answer = apply()
        except Exception as exc:
            S.write_json(path, {"at": S.now(), "error": str(exc)})
            raise
        S.write_json(path, {"at": S.now(), "answer": answer})
        return answer
    finally:
        with _running_lock:
            _running.pop(key).set()


def _forget(ledger) -> None:
    """Drop outcomes no client asks about any more: a client stops asking `WAIT` after its first try, plus one
    request's `TIMEOUT`. The worker can write its task's folder, so nothing here follows a link."""
    from . import storage
    horizon = time.time() - 2 * (WAIT + TIMEOUT)
    try:
        with storage.runtime_directory(storage.runtime_parts(ledger) or [], create=True) as folder:
            for entry in os.scandir(folder):
                try:
                    if entry.stat(follow_symlinks=False).st_mtime < horizon:
                        os.unlink(entry.name, dir_fd=folder)
                except FileNotFoundError:
                    pass
    except FileNotFoundError:
        return


def _saved(path) -> dict | None:
    from . import storage
    try:
        saved = json.loads(storage.read_text(path))
    except FileNotFoundError:
        return None
    return saved if isinstance(saved, dict) else {}


def handle(project: str, slug: str, attempt: int, body: dict) -> dict:
    """Apply one admitted request from the task's current worker job, once. Identity comes from the admission; the
    body names only the change."""
    fields = {key: value for key, value in body.items() if key not in ("project", "slug", "attempt", "kind", "request")}
    return once(project, slug, body.get("request"), _change(project, slug, attempt, body.get("kind"), fields))


def _change(project: str, slug: str, attempt: int, kind: object, fields: dict):
    """The change a request of `kind` makes, checked before it runs."""
    if kind == "alt":
        if fields.keys() - {"args", "stdin", "files", "shown"}:
            raise ValueError("Unsupported alt verb fields.")
        return lambda: run_alt(project, slug, attempt, fields.get("args"), fields.get("stdin", ""),
                               fields.get("files", {}), fields.get("shown", {}))
    if kind == "land":
        from . import land
        if fields.keys() - {"op", "fields"}:
            raise ValueError("Unsupported landing fields.")

        def change() -> dict:
            try:
                return {"result": land.record(project, slug, attempt, fields.get("op"), fields.get("fields"))}
            except land.LandError as exc:
                raise ValueError(str(exc)) from None
        return change
    from . import tasks as T
    if kind == "inbox":
        background = fields.get("background", [])
        if (fields.keys() - {"stop", "background"} or not isinstance(background, list) or len(background) > 64
                or any(not isinstance(item, str) or len(item) > 128 for item in background)):
            raise ValueError("Unsupported checkpoint fields.")
        return lambda: {"text": _hook(project, slug, "inbox", lambda: T.owner_checkpoint(
            project, slug, stop=fields.get("stop") is True, background=background)) or ""}
    if kind == "count":
        if fields.keys() - {"path"} or not isinstance(fields.get("path"), (str, type(None))):
            raise ValueError("Unsupported edit count fields.")

        def count() -> dict:
            _hook(project, slug, "edit count", lambda: S.count_edit(project, S.load_task(project, slug), fields.get("path")))
            return {}
        return count
    if kind == "steer":
        if fields:
            raise ValueError("Unsupported steering fields.")

        def steer() -> dict:
            task = S.load_task(project, slug)
            T.steer_waiting(project, slug, task["agent_id"], _sends(project, task))
            return {}
        return steer
    message = fields.get("message")
    if not isinstance(message, str) or not message.replace("-", "").isalnum() or len(message) > 64:
        raise ValueError("A message identity is required.")
    if kind == "claim":
        if fields.keys() - {"message"}:
            raise ValueError("Unsupported claim fields.")
        return lambda: {"message": _claim(project, slug, message)}
    if kind == "settle":
        outcome, session = fields.get("outcome"), fields.get("session")
        if (fields.keys() - {"message", "outcome", "session"} or outcome not in ("delivered", "returned", "unconfirmed")
                or not isinstance(session, (str, type(None)))):
            raise ValueError("Unsupported settlement fields.")

        def settle() -> dict:
            task = S.load_task(project, slug)
            if _handed(task, message):
                with _sends_folder(project, task) as folder:
                    try:
                        os.rename(f"{message}.writing", f"{message}.{outcome}", src_dir_fd=folder, dst_dir_fd=folder)
                    except FileNotFoundError:
                        pass  # settled already, by `recover_send`
            T.settle_send_now(project, slug, message, outcome, agent_id=task["agent_id"], session_id=session)
            return {}
        return settle
    raise ValueError("Unknown task door request.")


def _handed(task: dict, message: str) -> bool:
    """Whether altd handed this message to the task's current worker: a driver takes and settles only those."""
    claim = task.get("send_now") or {}
    return claim.get("agent_id") == task.get("agent_id") and message == claim.get("id")


def _sends(project: str, task: dict):
    from . import dispatch, engines
    sends = engines.worker_sends(task["agent_id"], job_root=dispatch.l2_job_root(project, task["slug"]))
    if sends is None:
        raise ValueError("This worker has no message driver.")
    return sends


@contextlib.contextmanager
def _sends_folder(project: str, task: dict):
    """The worker's sends folder, reached without following a link the worker may have placed in its task folder."""
    from . import storage
    with contextlib.ExitStack() as stack:
        try:
            folder = stack.enter_context(storage.runtime_directory(storage.runtime_parts(_sends(project, task)) or []))
        except OSError as exc:
            raise ValueError(f"This worker's sends folder is unavailable: {exc.strerror}") from None
        yield folder


def _claim(project: str, slug: str, message: str) -> dict | None:
    """Take the message altd handed the driver to write into the running turn; a repeat returns the same message."""
    from . import storage
    task = S.load_task(project, slug)
    if not _handed(task, message):
        return None
    with _sends_folder(project, task) as folder:
        try:
            os.rename(f"{message}.json", f"{message}.writing", src_dir_fd=folder, dst_dir_fd=folder)
        except FileNotFoundError:
            pass  # withdrawn by `recover_send`
        try:
            with storage.regular_reader(folder, f"{message}.writing") as fd, open(fd, closefd=False) as stream:
                return json.loads(stream.read())
        except FileNotFoundError:
            return None


def _hook(project: str, slug: str, name: str, apply):
    """A checkpoint hook's change. Its failure is Altitude's to repair, raised as a system fault; the hook goes on."""
    try:
        return apply()
    except Exception as exc:  # noqa: BLE001
        from . import incidents
        incidents.system_fault("hook", f"{name} {project}/{slug}: {exc}"[:400])
        return None


def run_alt(project: str, slug: str, attempt: int, args: object, stdin: object, files: object,
            shown: object) -> dict:
    """Run one `alt` verb as the task's L2. The verb reads its input and shown files from what the worker sent,
    never from disk."""
    if (not isinstance(args, list) or len(args) > 128 or any(not isinstance(a, str) or len(a) > 16384 for a in args)
            or not isinstance(stdin, str) or not all(
                isinstance(sent, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in sent.items())
                for sent in (files, shown))):
        raise ValueError("invalid alt verb request")
    env = _verb_env(project, slug, attempt)
    try:
        result = subprocess.run([sys.executable, "-B", str(config.SOURCE / "bin" / "alt"), *args],
                                input=json.dumps({"stdin": stdin, "files": files, "shown": shown}), cwd=str(config.ROOT), env=env,
                                capture_output=True, text=True, timeout=TIMEOUT - 20)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"returncode": 1, "stdout": "", "stderr": f"alt verb failed: {exc}\n"}
    return {"returncode": result.returncode, "stdout": _bounded(result.stdout), "stderr": _bounded(result.stderr)}


def _verb_env(project: str, slug: str, attempt: int) -> dict[str, str]:
    from . import engines
    env = engines.clean_env()
    for name in [name for name in env if name.startswith("ALTITUDE_")]:
        env.pop(name)
    env.update({"ALTITUDE_ACTOR": "l2", "ALTITUDE_PROJECT": project, "ALTITUDE_TASK": slug,
                "ALTITUDE_ATTEMPT": str(attempt), "ALTITUDE_HOME": str(config.ROOT), "ALTITUDE_DOOR": "1"})
    return env


def _bounded(text: str) -> str:
    data = (text or "").encode()
    return text if len(data) <= MAX_OUTPUT else data[:MAX_OUTPUT].decode(errors="ignore") + "\n[output truncated]\n"

