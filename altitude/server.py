"""altd — the Altitude web/API server and task timers."""
from __future__ import annotations
import argparse
import json
import mimetypes
import os
import re
import socket
import socketserver
import ssl
import stat
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import wave
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote, unquote

from . import config, digest, dispatch, engines, git_policy, incidents, l3, monitor, quota_codex, route, state as S, tasks as T, transcript, verify

LOG = config.ROOT / "altd.log"
_bg: dict[str, threading.Thread] = {}
_bg_guard = threading.Lock()
_l3_drain_requested: set[str] = set()
_l3_verb_brokers: dict[str, "_L3VerbServer"] = {}
_l3_verb_broker_guard = threading.Lock()
CAPACITY_RETRY_DELAYS = (30, 60, 120, 300, 600, 900)
L3_VERB_MAX_REQUEST = 4 << 20
L3_VERB_MAX_OUTPUT = 8 << 20
L3_GH_READS = {
    ("pr", "view"), ("pr", "list"), ("pr", "diff"), ("pr", "checks"),
    ("issue", "list"), ("issue", "view"),
    ("run", "list"), ("run", "view"), ("run", "watch"),
}
L3_TASK_TARGETS = {
    "reject", "escalate", "events", "messages", "report", "show", "resume", "message", "stop",
    "paths", "hold-merge", "done", "status", "preserve-checkout",
}

# The wireframe boards of any project that has them, served read-only from its own checkout so the
# browser always shows what is on main. The tree is mirrored under the prefix rather than flattened:
# `wireframes.css` imports the build's `web/design/tokens.css` from two levels up, and a board
# without its tokens is not the board.
DESIGN_ROUTE = "design"
DESIGN_ENTRY = "design/wireframes/index.html"
DESIGN_TREES = ("design/wireframes", "web/design")
DESIGN_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".woff2": "font/woff2",
}


def design_viewer_url(project: str) -> str | None:
    """Where this project's wireframe viewer is served, or None when the project has no boards."""
    try:
        root = config.project_path(project)
    except (KeyError, OSError):
        return None
    return f"/{DESIGN_ROUTE}/{quote(project)}/{DESIGN_ENTRY}" if (root / DESIGN_ENTRY).is_file() else None


# A phone records AAC/mp4 (Safari) or opus/webm (Chromium). Altitude only adapts those containers
# to the path-based protocol of the existing local faster-whisper server; it owns no speech model.
VOICE_MAX_BODY = 16 << 20
VOICE_MAX_SECONDS = 600
VOICE_TYPES = {
    "audio/mp4": ".m4a",
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mpeg": ".mp3",
    "audio/aac": ".aac",
    "audio/x-m4a": ".m4a",
}
VOICE_SOCKET = os.environ.get("WHISPER_SOCKET", "/tmp/whisper-server.sock")
VOICE_BRIDGE = os.environ.get("WHISPER_BRIDGE", "127.0.0.1:8890")


class VoiceInputError(RuntimeError):
    """A safe, useful voice-input error that may cross the HTTP boundary."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def _whisper_connection() -> socket.socket | None:
    """Reach the desktop Whisper socket directly, or its existing loopback bridge."""
    direct = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        direct.connect(VOICE_SOCKET)
        return direct
    except OSError:
        direct.close()
    try:
        host, port = VOICE_BRIDGE.rsplit(":", 1)
        return socket.create_connection((host, int(port)), timeout=5)
    except (OSError, TypeError, ValueError):
        return None


def _wav_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as audio:
        rate = audio.getframerate()
        return audio.getnframes() / rate if rate else 0.0


def transcribe_voice(raw: bytes, content_type: str) -> str:
    """Convert one bounded browser recording, ask local Whisper for text, and retain no audio."""
    media_type = content_type.split(";", 1)[0].strip().lower()
    extension = VOICE_TYPES.get(media_type)
    if extension is None:
        raise VoiceInputError("This browser's recording format is not supported.", 415)

    try:
        with tempfile.TemporaryDirectory(prefix="altitude-voice-") as work:
            source = Path(work) / f"recording{extension}"
            wav = Path(work) / "recording-16k.wav"
            source.write_bytes(raw)
            try:
                converted = subprocess.run(
                    [
                        "ffmpeg", "-nostdin", "-y", "-loglevel", "error", "-i", str(source),
                        "-t", str(VOICE_MAX_SECONDS + 1), "-ar", "16000", "-ac", "1",
                        "-acodec", "pcm_s16le", "-f", "wav", str(wav),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=45,
                )
            except FileNotFoundError as exc:
                raise VoiceInputError("Voice transcription is unavailable on this host.", 503) from exc
            except subprocess.TimeoutExpired as exc:
                raise VoiceInputError("The recording took too long to prepare. Try a shorter clip.", 504) from exc
            if converted.returncode != 0 or not wav.is_file():
                raise VoiceInputError("The recording could not be read. Try recording it again.", 422)
            try:
                seconds = _wav_seconds(wav)
            except (OSError, EOFError, wave.Error) as exc:
                raise VoiceInputError("The recording could not be read. Try recording it again.", 422) from exc
            if seconds > VOICE_MAX_SECONDS:
                raise VoiceInputError(f"Recordings are limited to {VOICE_MAX_SECONDS // 60} minutes.", 413)

            upstream = _whisper_connection()
            if upstream is None:
                raise VoiceInputError(
                    "Voice transcription is temporarily unavailable. You can keep typing and try again.", 503
                )
            try:
                upstream.settimeout(120)
                upstream.sendall(str(wav).encode())
                upstream.shutdown(socket.SHUT_WR)
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = upstream.recv(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > (1 << 20):
                        raise VoiceInputError("Voice transcription returned an invalid response.", 502)
                    chunks.append(chunk)
            except socket.timeout as exc:
                raise VoiceInputError("Transcription took too long. Try again.", 504) from exc
            except OSError as exc:
                raise VoiceInputError(
                    "Voice transcription is temporarily unavailable. You can keep typing and try again.", 503
                ) from exc
            finally:
                upstream.close()

            text = b"".join(chunks).decode("utf-8", "replace").strip()
            if text.startswith("ERROR:"):
                raise VoiceInputError(
                    "Voice transcription is temporarily unavailable. You can keep typing and try again.", 503
                )
            if not text:
                raise VoiceInputError("No speech was detected. Your draft is unchanged.", 422)
            return text
    except VoiceInputError:
        raise
    except OSError as exc:
        raise VoiceInputError("Voice transcription is unavailable on this host.", 503) from exc


def log(msg: str) -> None:
    line = f"{S.now()} {msg}\n"
    try:
        with open(LOG, "a") as f:
            f.write(line)
    except OSError:
        pass
    print(line, end="", flush=True)


def spawn(key: str, fn, *a) -> bool:
    """Run fn in a named background thread unless one with that key is already running."""
    with _bg_guard:
        t = _bg.get(key)
        if t and t.is_alive():
            return False

        def run():
            try:
                fn(*a)
            except Exception as e:  # noqa: BLE001
                log(f"[{key}] failed: {e}\n{traceback.format_exc()}")
                if not isinstance(e, (dispatch.DispatchFailure, dispatch.ResumeFailure)):
                    parts = key.split(":")
                    incidents.system_fault(f"workflow:{parts[0]}", f"{key}: {e}", project=parts[1] if len(parts) > 1 else None,
                                           task=parts[2] if len(parts) > 2 else None)
        t = threading.Thread(target=run, name=key, daemon=True)
        _bg[key] = t
        t.start()
        return True


# ---- workflows the timers and buttons trigger --------------------------------

def request_task_resume(project: str, slug: str, *, due: bool = True) -> bool:
    """Wake one daemon-side resume; message, timer and capacity requests coalesce on the same key."""
    if due and slug not in dispatch.resume_due(project):
        return False
    return spawn(f"resume:{project}:{slug}", dispatch.resume, project, slug)


def request_daemon_task_operation(project: str, slug: str, operation: str, reason: str, *, actor: str) -> dict:
    """Persist an operator request before scheduling its one daemon-side runner."""
    result = dispatch.request_task_operation(project, slug, operation, reason, actor=actor)
    if result.get("queued"):
        spawn(f"task-operation:{project}:{slug}", dispatch.run_task_operation, project, slug)
    return result


def l3_verb_request(project: str, request: dict) -> dict:
    """Execute one role-fenced ``alt`` verb or fixed read for a confined L3."""
    with config.project_activity(project) as ready:
        if not ready or not config.is_managed(project):
            raise ValueError("This project is not managed. Add its folder again to attach L3.")
        return _l3_verb_request(project, request)


def _l3_verb_request(project: str, request: dict) -> dict:
    config.project(project)  # the per-project socket binds the authority; request JSON cannot select it
    kind = request.get("kind")
    if kind == "alt":
        args = request.get("args")
        stdin = request.get("stdin") or ""
        if (not isinstance(args, list) or len(args) > 128
                or any(not isinstance(arg, str) or len(arg) > 16384 for arg in args)
                or not isinstance(stdin, str) or len(stdin.encode()) > L3_VERB_MAX_REQUEST):
            raise ValueError("invalid alt verb arguments")
        project_options = {"--p", "--pr", "--pro", "--proj", "--proje", "--projec", "--project"}
        file_options = {"--f", "--fi", "--fil", "--file"}
        if any(arg.split("=", 1)[0] in project_options | file_options
               or arg.startswith("-p") and not arg.startswith("--") for arg in args):
            raise ValueError("the L3 socket fixes the project and accepts input only on stdin")
        _validate_l3_alt_args(args)
        if args[:2] == ["task", "hold-merge"] and any(arg.split("=", 1)[0] == "--approval" for arg in args[3:]):
            options = merge_approval_parser().parse_args(args[2:])
            receipt = apply_recorded_merge_approval(project, **vars(options))
            return {"returncode": 0, "stdout": json.dumps(receipt) + "\n", "stderr": ""}
        if args[:1] == ["issue"]:
            options = vars(issue_parser().parse_args(args[1:]))
            options.pop("text", None)
            url = issue_write(project, body=stdin, actor="l3", **options)
            return {"returncode": 0, "stdout": url + "\n", "stderr": ""}
        env = engines.clean_env()
        env.update({"ALTITUDE_ACTOR": "l3", "ALTITUDE_PROJECT": project, "ALTITUDE_HOME": str(config.ROOT)})
        try:
            result = subprocess.run([str(config.REPO / "bin" / "alt"), *args], input=stdin,
                                    cwd=str(config.project_path(project)), env=env,
                                    capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as exc:
            return {"returncode": 1, "stdout": "", "stderr": f"alt verb failed: {exc}\n"}
        if args[:2] == ["task", "new"] and result.returncode == 0:
            _note_created_task(project, result.stdout or "")
        return {"returncode": result.returncode, "stdout": _l3_bounded(result.stdout or ""),
                "stderr": _l3_bounded(result.stderr or "")}
    if kind == "service":
        unit = str(request.get("unit") or "")
        if not re.fullmatch(r"altitude(?:[-@.][A-Za-z0-9_.@-]+)*", unit):
            raise ValueError("only altitude user-service status is readable")
        return engines.service_status(unit)
    if kind != "gh":
        raise ValueError("unknown L3 read")
    args = request.get("args")
    if (not isinstance(args, list) or len(args) < 2 or len(args) > 64
            or any(not isinstance(arg, str) or len(arg) > 4096 for arg in args)):
        raise ValueError("invalid gh read arguments")
    redirected = any(
        arg in ("--repo", "-R") or arg.startswith(("--repo=", "-R="))
        or (arg.startswith("-R") and len(arg) > 2)
        or "://" in arg or "/" in arg
        for arg in args[2:]
    )
    if (tuple(args[:2]) not in L3_GH_READS
            or any(arg == "--web" or arg.startswith("--web=") for arg in args[2:])
            or redirected):
        raise ValueError("L3 may only use the documented gh read commands")
    env = engines.clean_env()
    env.pop("GH_REPO", None)  # cwd plus rejected repo selectors binds reads to this project's checkout
    env["GH_PAGER"] = "cat"
    try:
        result = subprocess.run(["gh", *args], cwd=str(config.project_path(project)), env=env,
                                capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"returncode": 1, "stdout": "", "stderr": f"gh read failed: {exc}\n"}

    return {"returncode": result.returncode, "stdout": _l3_bounded(result.stdout or ""),
            "stderr": _l3_bounded(result.stderr or "")}


def _note_created_task(project: str, stdout: str) -> None:
    """Attach the task `alt task new` just printed to the L3 turn that created it, so the turn's assistant
    chat row names it (`tasks: [slug]`, SPEC.md §5.2 note 4) and the page can show the task under the reply."""
    try:
        slug = json.loads(stdout).get("slug")
    except (ValueError, AttributeError):
        slug = None
    if isinstance(slug, str) and slug:
        l3.note_task(project, slug)


def _validate_l3_alt_args(args: list[str]) -> None:
    """Reject path-shaped identifiers before altd invokes the ordinary CLI parser."""
    if len(args) >= 3 and args[:1] == ["task"] and args[1] in L3_TASK_TARGETS:
        S.require_task_slug(args[2])
    if args[:2] == ["task", "block"]:
        raise ValueError("L3 must use task stop --reason for a running worker")
    if args[:2] == ["task", "hold-merge"] and "--off" in args[3:]:
        raise ValueError("only Burak may release a merge hold")
    if args[:1] == ["fyi"] and len(args) >= 3:
        S.require_task_slug(args[1])
    if args[:2] == ["incident", "amend"]:
        if len(args) < 3 or not re.fullmatch(r"I-\d{8}-\d{6}(?:-\d+)?", args[2]):
            raise ValueError("invalid incident id")
    if args[:2] == ["incident", "new"]:
        tasks = [args[i + 1] for i, arg in enumerate(args[:-1]) if arg == "--task"]
        tasks += [arg.split("=", 1)[1] for arg in args if arg.startswith("--task=")]
        for slug in tasks:
            S.require_task_slug(slug)


def merge_approval_parser() -> argparse.ArgumentParser:
    """Exact coordinator-only grammar; this mode executes in altd, never a CLI subprocess."""
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            raise ValueError(f"alt task hold-merge: {message}")
    parser = Parser(allow_abbrev=False, add_help=False)
    parser.add_argument("slug")
    parser.add_argument("--approval", required=True)
    parser.add_argument("--pr-number", dest="pr", required=True, type=int)
    parser.add_argument("--head", required=True)
    parser.add_argument("--reason", required=True)
    return parser


def apply_recorded_merge_approval(project: str, slug: str, approval: str, pr: int, head: str, reason: str) -> dict:
    """I-20260907-205556: bind durable operator approval to the checkout-origin PR before releasing a hold."""
    S.require_task_slug(slug)
    if (pr < 1 or not re.fullmatch(r"[0-9a-f]{32}", approval)
            or not re.fullmatch(r"[0-9a-f]{40}", head) or not reason.strip()):
        raise ValueError("approval requires a message id, positive PR number, full head SHA, and reason")
    from . import github_intake
    owner, repository = github_intake.project_repo(project)
    url = f"https://github.com/{owner}/{repository}/pull/{pr}"
    env = engines.clean_env()
    env.pop("GH_REPO", None)
    result = subprocess.run(["gh", "pr", "view", str(pr), "--repo", f"{owner}/{repository}", "--json",
                             "number,url,state,isDraft,isCrossRepository,baseRefName,headRefName,headRefOid,updatedAt"],
                            cwd=config.project_path(project), env=env, capture_output=True, text=True, timeout=30)
    pull = json.loads(result.stdout) if result.returncode == 0 else None
    if not isinstance(pull, dict) or pull.get("number") != pr or pull.get("url") != url:
        raise ValueError("approval PR could not be read from the project origin")
    try:
        return T.apply_merge_approval(project, slug, approval, pull, head=head, reason=reason, actor="l3")
    except T.TransitionError as exc:
        raise ValueError(str(exc)) from exc


def _l3_bounded(value: str) -> str:
    raw = value.encode(errors="replace")
    if len(raw) <= L3_VERB_MAX_OUTPUT:
        return value
    return raw[:L3_VERB_MAX_OUTPUT].decode(errors="replace") + "\n[output truncated by altd]\n"


class _L3VerbHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        data = bytearray()
        while len(data) <= L3_VERB_MAX_REQUEST and b"\n" not in data:
            chunk = self.request.recv(min(65536, L3_VERB_MAX_REQUEST + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        try:
            if len(data) > L3_VERB_MAX_REQUEST:
                raise ValueError("L3 verb request is too large")
            request = json.loads(bytes(data).split(b"\n", 1)[0])
            if not isinstance(request, dict):
                raise ValueError("L3 verb request must be an object")
            response = l3_verb_request(self.server.project, request)
        except (ValueError, KeyError, TypeError) as exc:
            response = {"error": str(exc)[:300]}
        except Exception as exc:  # noqa: BLE001 — a broker failure returns no daemon internals
            log(f"L3 verb broker failed: {exc}\n{traceback.format_exc()}")
            response = {"error": "verb unavailable"}
        self.request.sendall((json.dumps(response) + "\n").encode())


class _L3VerbServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, path: str, project: str):
        self.project = project
        super().__init__(path, _L3VerbHandler)


def start_l3_verb_broker(project: str, path: Path | None = None) -> _L3VerbServer:
    """Bind one project-scoped L3 capability socket; refuse non-socket path collisions."""
    config.project(project)
    path = Path(path or l3.verb_socket_path(project))
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        mode = None
    if mode is not None:
        if not stat.S_ISSOCK(mode):
            raise RuntimeError(f"L3 verb socket path is not a socket: {path}")
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(0.2)
            probe.connect(str(path))
        except OSError:
            path.unlink()
        else:
            raise RuntimeError(f"L3 verb socket is already active: {path}")
        finally:
            probe.close()
    broker = _L3VerbServer(str(path), project)
    path.chmod(0o600)
    broker.socket_path = path
    threading.Thread(target=broker.serve_forever, name="l3-verb-broker", daemon=True).start()
    return broker


def stop_l3_verb_broker(broker: _L3VerbServer) -> None:
    path = broker.socket_path
    broker.shutdown()
    broker.server_close()
    try:
        if path.is_socket():
            path.unlink()
    except FileNotFoundError:
        pass


def ensure_l3_verb_broker(project: str) -> _L3VerbServer:
    """Create the project's broker once, including for a project registered after altd started."""
    with _l3_verb_broker_guard:
        if not config.is_managed(project):
            raise ValueError("This project is not managed. Add its folder again to attach L3.")
        broker = _l3_verb_brokers.get(project)
        if broker is None:
            broker = start_l3_verb_broker(project)
            _l3_verb_brokers[project] = broker
        return broker


def remove_l3_verb_broker(project: str) -> None:
    # A delayed removal must not close the broker a concurrent re-registration has reused.
    with S.project_lock(project):
        if config.is_managed(project):
            return
        with _l3_verb_broker_guard:
            broker = _l3_verb_brokers.pop(project, None)
            if broker is not None:
                stop_l3_verb_broker(broker)


def stop_l3_verb_brokers() -> None:
    with _l3_verb_broker_guard:
        brokers = list(_l3_verb_brokers.values())
        _l3_verb_brokers.clear()
    for broker in reversed(brokers):
        stop_l3_verb_broker(broker)


def request_l3_drain(project: str) -> bool:
    """Ask the project's one drain loop to run. A request that arrives while the loop is finishing
    is remembered, so a turn boundary cannot miss a message queued at the same instant."""
    if not config.is_managed(project):
        return False
    key = f"l3-queue:{project}"
    with _bg_guard:
        _l3_drain_requested.add(project)
        current = _bg.get(key)
        if current and current.is_alive():
            return False
    return spawn(key, drain_l3_queue, project)


def drain_l3_queue(project: str) -> None:
    """Run the messages waiting for L3, one turn at a time, until the queue is empty. Every server-side
    L3 turn asks for a drain when it ends, so a message queued while L3 was busy runs at the turn
    boundary rather than at the next tick. Requests coalesce into this keyed loop instead of nesting
    another; if another turn owns L3, that turn's completion makes the next request."""
    key = f"l3-queue:{project}"
    while True:
        with _bg_guard:
            _l3_drain_requested.discard(project)
        while True:
            with config.project_activity(project) as ready:
                if not ready or not config.is_managed(project):
                    break
                ensure_l3_verb_broker(project)
                delivered = l3.deliver_queued(project)
            if not delivered:
                break
        with _bg_guard:
            if project in _l3_drain_requested and not l3.busy(project):
                continue
            _l3_drain_requested.discard(project)
            if _bg.get(key) is threading.current_thread():
                _bg.pop(key, None)
            return


def server_l3_turn(project: str, prompt: str, **kwargs) -> dict:
    """Run one server-owned turn and request its queue drain at the turn boundary, success or error."""
    try:
        with config.project_activity(project) as ready:
            if not ready or not config.is_managed(project):
                return {"error": "This project is not managed. Add its folder again to attach L3.", "completed": False}
            ensure_l3_verb_broker(project)
            return l3.turn(project, prompt, **kwargs)
    finally:
        request_l3_drain(project)


def start_l3(project: str) -> None:
    if l3.queued(project):
        request_l3_drain(project)
        return
    # The start reply is a conversation with Burak, not a turn log.
    server_l3_turn(project, "You have just been started for this project. Read the state file and the repo's README/CLAUDE.md (skim), "
                            "then answer in a few plain sentences: what this project is, what is in flight, and what you would need from Burak. "
                            "Keep operational details in the task record rather than dumping them into chat. Run no other commands.",
                   trigger="start")


def restart_notice() -> None:
    """One message per project with active tasks: L3 resumes what a fault had stopped and leaves Burak's to him."""
    for project in config.load_projects():
        if not config.is_managed(project):
            continue
        active = [t for t in S.list_tasks(project) if t["state"] in ("running", "blocked", "reported")]
        if not active:
            continue
        lines = []
        for t in active:
            tag = (f"fault {t['fault']}" if t.get("fault") else f"waiting on {t.get('waiting_on', 'burak')}"
                   if t["state"] == "blocked" else t["state"])
            lines.append(f"- {t['slug']}: {t['state']} ({tag}); {T.short_reason(t.get('blocked_reason') or t.get('title') or '')}")
        l3.queue_message(project, "Altitude restarted with the code now on main. Its active tasks:\n" + "\n".join(lines)
                         + "\n\nCheck each with `alt task status <slug>`. A restart does not resolve checkout faults. "
                         "Resume only after observing that the cause is gone (`alt task resume <slug> --reason \"<observed fix>\"`); leave a task waiting on "
                         "Burak to him; a running task keeps "
                         "its worker. Reply in two or three plain sentences.", trigger="restart")
        log(f"[{project}] restart notice queued for L3 ({len(active)} active tasks)")


def on_l2_finished(project: str, item: dict) -> None:
    with config.project_activity(project) as attached, config.restart_lock() as ready:
        if attached and config.is_managed(project) and ready and not config.restart_in_progress():
            _on_l2_finished(project, item)


def _on_l2_finished(project: str, item: dict) -> None:
    t = item["task"]
    slug = t["slug"]
    with S.project_lock(project):
        live = S.load_task(project, slug)
        snapshot = (t.get("state"), t.get("agent_id"))
        current = (live.get("state"), live.get("agent_id"))
    if current != snapshot:
        log(f"[{project}/{slug}] ignored stale finished worker snapshot {snapshot} → {current}")
        return
    t = live  # include completion/action fields that may have landed after poll took its worker snapshot

    def block_snapshot(reason: str, *, actor: str = "altd", updates: dict | None = None) -> dict:
        return T.block(project, slug, reason, actor=actor, expected_state=t.get("state"), updates=updates)

    if t.get("completion_requested"):
        a = item.get("agent") or {}
        if a.get("state") == "working" or a.get("status") in ("busy", "idle"):
            raise RuntimeError(f"{project}/{slug}: completion reached finished handling while its L2 is still live")
        T.finalize_completion(project, slug)
        log(f"[{project}/{slug}] no-code completion finalized after the L2 worker exited")
        return
    if item.get("rejection"):
        engine = dispatch.l2_engine(t)
        option = {"engine": engine, "model": t.get("launch_model"), "role": "l2"}
        route.note_rejection(option, item["rejection"])
        why = item["rejection"]["why"] + ". Check engine authentication/model access or change Auto routing."
        block_snapshot(why)
        pinned = t.get("routing_pinned") or config.pinned_option("l2", config.project(project), engine=t.get("engine"), model=t.get("model"))
        switch = route.pick_engine("l2", project=config.project(project)) if not pinned else {"engine": None}
        if switch.get("engine") and item.get("safe_to_retry") and not (item.get("agent") or {}).get("resumed"):
            engines.remove_l2_worker(engine, t.get("agent_id"), job_root=dispatch.l2_job_root(project, slug))
            T.requeue(project, slug, clear_worker=True, reason=why + " Fresh Auto attempt from saved progress.")
        else:
            T.fyi(project, slug, why + " The existing attempt and conversation are retained.", actor="altd")
        return
    if item.get("capacity"):
        # Provider capacity is local to this task/model, unlike an exhausted subscription window or a system fault.
        # Keep its logical L2 identity and retry the same conversation after bounded exponential-ish backoff.
        retry = max(0, int(t.get("capacity_retries") or 0)) + 1
        delay = CAPACITY_RETRY_DELAYS[min(retry - 1, len(CAPACITY_RETRY_DELAYS) - 1)]
        until = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat(timespec="seconds")
        engine = t.get("l2_engine") or "claude"
        model = t.get("engine_model") or "provider default"
        updates = {"capacity_retries": retry, "resume_after": until}
        try:
            block_snapshot(f"{engine} model {model} is temporarily at capacity; "
                           f"Altitude retries this same L2 after {until}", updates=updates)
        except T.TransitionError:
            log(f"[{project}/{slug}] capacity result lost a concurrent lifecycle race; ignored")
            return
        log(f"[{project}/{slug}] {engine}/{model} temporarily at capacity → retry {retry} after {delay}s")
        return
    if item.get("limited"):  # park until the window reopens, or start a fresh attempt on the other engine
        until, a = item["limited"], item.get("agent") or {}
        engine = t.get("l2_engine") or "claude"
        # Claude's hold file is consumed only by Claude turns. A Codex limit must not freeze Claude work.
        news = (engines.note_usage_limit(until, f"L2 {a.get('id', '')} of {slug}")
                if engine == "claude" else True)
        try:
            block_snapshot(f"usage limit: the subscription window is exhausted, resets {until} — "
                           "Altitude resumes this L2 itself after that", updates={"resume_after": until})
        except T.TransitionError:
            log(f"[{project}/{slug}] usage-limit result lost a concurrent lifecycle race; ignored")
            return
        route.note_limit(engine, until)
        pinned = t.get("routing_pinned") or config.pinned_option("l2", config.project(project), engine=t.get("engine"), model=t.get("model"))
        switch = route.pick_engine("l2", project=config.project(project)) if not pinned else {"engine": None}
        if switch.get("engine"):
            other = switch["engine"]
            engines.remove_l2_worker(engine, t.get("agent_id"), job_root=dispatch.l2_job_root(project, slug))
            T.requeue(project, slug, clear_worker=True,
                      reason=f"{engine} window exhausted until {until}; fresh attempt on {other} from saved progress")
            T.fyi(project, slug, f"{engine} usage window hit (resets {until}). {slug} continues as a fresh attempt "
                                 f"on {other} from its progress file.", actor="altd")
            log(f"[{project}/{slug}] L2 hit the usage limit → requeued for {other}")
            return
        if news:
            T.fyi(project, slug, f"{engine} usage window hit. This L2 resumes after {until}.", actor="altd")
        log(f"[{project}/{slug}] L2 hit the usage limit → blocked until {until}")
        return
    with S.project_lock(project):  # a new report: whatever L3 did with the previous one no longer counts
        t0 = S.load_task(project, slug)
        t0["l3_handled"] = None
        t0.pop("capacity_retries", None)
        S.save_task(project, t0)
    if item.get("needs_input"):
        a = item.get("agent") or {}
        reason = "L2 is idle without a report — probably waiting for permission or an answer; message the L2 directly."
        try:
            block_snapshot(reason)
        except T.TransitionError:
            log(f"[{project}/{slug}] idle result lost a concurrent lifecycle race; ignored")
            return
        T.fyi(project, slug, f"{slug}: L2 idle {dispatch.IDLE_NEEDS_INPUT_SECONDS}s without finishing — needs input? attach {a.get('id', '')}")
        log(f"[{project}/{slug}] L2 idle → blocked (needs input)")
        return
    if item.get("died"):
        a = item.get("agent") or {}
        engine = t.get("l2_engine") or "claude"
        try:
            block_snapshot(f"L2 session ended without a fresh report (Altitude fault, not the L2's) — Resume from the card "
                           f"re-attaches its transcript (agent {a.get('id', '')})")
        except T.TransitionError:
            log(f"[{project}/{slug}] dead-worker result lost a concurrent lifecycle race; ignored")
            return
        incidents.system_fault("l2-died", f"L2 worker {a.get('id', '')} (attempt {t.get('attempt')}) ended without a fresh report: "
                               f"{engine} worker state={a.get('state', 'absent')}; {item.get('detail') or a.get('detail') or ''}",
                               project=project, task=slug)  # I-20260907-171446: preserve the worker's failure reason.
        log(f"[{project}/{slug}] L2 died → blocked; fault raised")
        return
    v = verify.verify(project, slug)
    log(f"[{project}/{slug}] L2 finished; verdict {v['verdict']}; problems {v['problems']}")
    T.set_spend(project, slug, **{k: val for k, val in v.get("spend", {}).items() if val is not None})
    if v["verdict"] == "fault":
        T.block(project, slug, f"verifier fault (Altitude, not the L2): {v.get('fault')}")
        log(f"[{project}/{slug}] verifier fault → blocked; fault raised")
        return
    if v["verdict"] == "missing":
        T.block(project, slug, "L2 session ended without a report (report.json missing)")
    elif v["verdict"] == "blocked":
        T.report(project, slug, v)
        T.block(project, slug, (v.get("report") or {}).get("blocked") or "blocked (see report)")
    else:
        T.report(project, slug, v)
    report_turn(project, t, v)


def report_fields(slug: str, v: dict) -> str:
    """The verifier's verdict as "Label: value" lines: task, verdict, problems, signals, PRs, spend."""
    def listed(items) -> str:
        return "; ".join(str(item) for item in items) if items else "none"
    landed = ((v.get("report") or {}).get("landed") or {}) if isinstance(v.get("report"), dict) else {}
    merged = {pr.get("number"): pr.get("merged") for pr in (landed.get("prs") or []) if isinstance(pr, dict)}
    prs = ", ".join(f"#{n} {'merged' if merged.get(n) else 'open'}" if n in merged else f"#{n}"
                    for n in (v.get("prs") or []))
    spend = v.get("spend") or {}
    spent = ", ".join(f"{val} {key.replace('_', ' ')}" for key, val in spend.items() if val not in (None, "", 0, {}))
    return "\n".join((f"Task: {slug}", f"Verdict: {v.get('verdict')}", f"Problems: {listed(v.get('problems'))}",
                       f"Post-mortem signals: {listed(v.get('signals'))}", f"PRs: {prs or 'none'}",
                       f"Spend: {spent or 'none recorded'}"))


def report_turn(project: str, t: dict, v: dict) -> None:
    with config.project_activity(project) as attached, config.restart_lock() as ready:
        if attached and config.is_managed(project) and ready and not config.restart_in_progress():
            _report_turn(project, t, v)


def _report_turn(project: str, t: dict, v: dict) -> None:
    """Close a mechanically clean report, otherwise run the L3's report-landed turn.

    The clean-close gate uses the on-disk report and live task state. It requires an ok verifier with no problems or
    signals, no merge hold, only merged PRs, at least one well-shaped successful main run, a healthy or not-applicable
    deploy, only fixed or dismissed review findings, and no decisions, blocks, FYIs, follow-ups, or post-mortem work.
    Any malformed, corrupt, stale, or raced state fails closed to L3; corrupt JSON also raises a system
    fault. `l3_handled` is stamped only when the turn returns, so a turn that altd's restart cut short is re-run by
    `resume_stranded_reports` instead of leaving the task waiting for nobody.
    """
    slug = t["slug"]
    if v.get("verdict") == "ok" and not v.get("problems") and not v.get("signals"):
        report_error = task_error = None
        try:
            with S.project_lock(project):
                try:
                    live = S.load_task(project, slug)
                except ValueError as e:
                    task_error = e
                    live = {}
                    report = None
                else:
                    try:
                        report = S.read_json(S.task_dir(project, slug) / "report.json", {})
                    except ValueError as e:
                        report_error = e
                        report = None
        except (KeyError, OSError):
            live, report = {}, None
        if task_error is not None:
            incidents.system_fault("task-json", f"{project}/{slug}: {task_error}", project=project, task=slug)
        if report_error is not None:
            incidents.system_fault("report-json", f"{project}/{slug}: {report_error}", project=project, task=slug)
        if isinstance(report, dict):
            landed = report.get("landed") or {}
            if not isinstance(landed, dict):
                landed = {}
            deploy = str(landed.get("deploy") or "")
            deploy_status = (deploy.split(maxsplit=1) or [""])[0].rstrip(":")
            prs = landed.get("prs") or []
            runs = landed.get("main_runs") or []
            review = report.get("review") or []
            live_hold_merge = live.get("hold_merge")
            if (not report.get("decisions") and not report.get("blocked") and not report.get("fyi")
                    and not report.get("follow_ups") and deploy_status in ("healthy", "not-applicable")
                    and isinstance(prs, list) and all(isinstance(pr, dict) and pr.get("merged") is True for pr in prs)
                    and isinstance(runs, list) and bool(runs)
                    and all(isinstance(run, dict) and isinstance(run.get("id"), str) and run.get("id")
                            and run.get("conclusion") == "success" for run in runs)
                    and isinstance(review, list)
                    and all(isinstance(item, dict) and item.get("disposition") in ("fixed", "dismissed") for item in review)
                    and live.get("state") == "reported" and not live_hold_merge):
                pr_text = ", ".join("PR #{} ({})".format(pr.get("number"), pr.get("title") or "untitled") for pr in prs) or "No PRs recorded"
                run_text = ", ".join("{}: {}".format(run.get("id"), run.get("conclusion")) for run in runs) or "none recorded"
                fixed = sum(item.get("disposition") == "fixed" for item in review)
                dismissed = sum(item.get("disposition") == "dismissed" for item in review)
                clean_digest = (f"No decisions. {pr_text} merged. Main runs: {run_text}. Deploy: {deploy}. "
                                f"Review findings: {fixed} fixed, {dismissed} dismissed.")
                try:
                    T.done(project, slug, actor="altd", digest=clean_digest)
                except T.TransitionError:
                    log(f"[{project}/{slug}] clean close lost the state race → L3 turn")
                else:
                    T.fyi(project, slug, f"{slug}: closed by altd without an L3 turn — nothing to judge: verifier verdict ok; "
                          f"hold_merge unset; PRs merged: {pr_text}; "
                          f"main runs: {run_text}; deploy: {deploy}; no decisions, blocked items, FYIs, follow-ups, or "
                          "post-mortem signals.", actor="altd")
                    with S.project_lock(project):
                        t2 = S.load_task(project, slug); t2["l3_handled"] = S.now(); S.save_task(project, t2)
                    log(f"[{project}/{slug}] clean report closed by altd; no L3 turn")
                    return
    # Report details belong in the task record, not a turn-log reply. The prompt is label/value rows the
    # conversation's expanded system card shows as they are (SPEC.md §3.4); the report itself stays behind
    # `alt task report` and the task's report view.
    header = (f"Report landed for {slug}.\n" + report_fields(slug, v) + "\n\n"
              f"Read the full report with `alt task report {slug}`. "
              "Handle the report: write a concise digest and use `alt task done`, or block/resume with the exact gap; "
              "record an incident only when its evidence will help a later recovery or diagnosis. An incident never creates "
              "a repair task or healing workflow. "
              "Put ids, slugs, file names, code, and spend figures in the task record — the card `--detail`, "
              "the digest, the FYI, or the task folder — not in the reply text. Close with at most two plain sentences saying what happened "
              "and whether anything waits on Burak.")
    res = server_l3_turn(project, header, trigger="report-landed")
    if not (res or {}).get("completed") or (res or {}).get("error"):
        detail = (res or {}).get("error") or "L3 turn did not complete"
        log(f"[{project}/{slug}] report turn unfinished: {detail}")  # not stamped: stranded-report retry owns it
        return
    try:
        with S.project_lock(project):
            t2 = S.load_task(project, slug); t2["l3_handled"] = S.now(); S.save_task(project, t2)
    except (KeyError, OSError, ValueError) as e:
        log(f"[{project}/{slug}] L3 turn completed but l3_handled could not be stamped: {e}")


def resume_stranded_reports(project: str) -> None:
    """Reports that landed (state reported/blocked with report.json) but whose L3 turn never finished get it again."""
    for t in S.list_tasks(project):
        if t["state"] not in ("reported", "blocked") or t.get("l3_handled"):
            continue
        report_path = S.task_dir(project, t["slug"]) / "report.json"
        if not report_path.exists():
            continue
        key = f"finished:{project}:{t['slug']}"
        with _bg_guard:
            if (_bg.get(key) or threading.Thread()).is_alive():
                continue
        v = t.get("verified") or {"verdict": "missing", "problems": ["no verified report on the task"], "signals": [],
                                  "spend": {}, "prs": t.get("prs", []), "report": {}}
        try:
            report = S.read_json(report_path)
        except (OSError, ValueError) as e:
            log(f"[{project}/{t['slug']}] cannot read stranded report: {e}")
            report = None
        last_block = next((ev for ev in reversed(S.read_events(project, t["slug"]))
                           if ev.get("kind") == "state" and ev.get("to") == "blocked"), None)
        if (t["state"] == "blocked" and v.get("verdict") == "ok" and isinstance(report, dict)
                and not report.get("blocked") and "attempt" in v and v.get("attempt") == t.get("attempt")
                and last_block and last_block.get("frm") == "running"):
            try:
                t = T.report(project, t["slug"], v, expected_state="blocked",
                             expected_attempt=t.get("attempt"), expected_block_from="running")
            except (T.TransitionError, KeyError) as e:
                log(f"[{project}/{t['slug']}] stranded report promotion skipped after a concurrent change: {e}")
                continue
        log(f"[{project}/{t['slug']}] report turn resumed (previous run did not finish)")
        spawn(key, report_turn, project, t, v)


def dispatch_waiting(project: str) -> None:
    if config.restart_in_progress():
        return
    for t in S.list_tasks(project):
        if t["state"] != "queued":
            continue
        hold = dispatch.wip_hold(project, t)
        if hold:
            S.write_json(config.project_dir(project) / "hold.json", {"at": S.now(), "reason": hold})
            return
        try:
            res = dispatch.run(project, t["slug"])
            log(f"[{project}/{t['slug']}] dispatched attempt {res['attempt']} agent={res['agent'].get('id') if res.get('agent') else None}")
        except dispatch.DispatchFailure as e:
            log(f"[{project}/{t['slug']}] {e}")
        except T.TransitionError as e:
            log(f"[{project}/{t['slug']}] dispatch refused: {e}")
        except Exception as e:  # noqa: BLE001
            log(f"[{project}/{t['slug']}] dispatch failed: {e}")
            dispatch.record_dispatch_failure(project, t["slug"], e)
    (config.project_dir(project) / "hold.json").unlink(missing_ok=True)


def drain_hook_faults() -> None:
    """Hooks run inside L2 sessions and cannot reach the server: they append to monitor/hook-faults.log; the tick raises them."""
    p = config.MONITOR_DIR / "hook-faults.log"
    if not p.exists():
        return
    lines = [ln for ln in p.read_text().splitlines() if ln.strip()]
    p.unlink()
    for ln in lines[-20:]:
        incidents.system_fault("hook", ln[:400])


def tick() -> None:
    try:
        quota_codex.refresh_if_due()
    except Exception as e:  # noqa: BLE001
        log(f"[quota-codex] refresh failed: {e}")
    drain_hook_faults()
    for project in list(_l3_verb_brokers):
        if not config.is_managed(project):
            remove_l3_verb_broker(project)
    for project in list(config.load_projects()):
        with config.project_activity(project) as ready:
            if ready and config.is_managed(project):
                tick_project(project)
    try:
        auto_restart()
    except Exception as e:  # noqa: BLE001
        log(f"auto-restart: {e}\n{traceback.format_exc()}")
    morning_digest()


def tick_project(project: str) -> None:
    # Decision 11: a sole running worker's merge must activate without another dispatch or report.
    try:
        with dispatch.publication_settlement(project):
            dispatch.self_deploy_fast_forward(project)
    except (git_policy.GitPolicyError, subprocess.SubprocessError, OSError) as e:
        incidents.system_fault("self-deploy", f"{project}: {e}", project=project)
    try:
        dispatch.run_project_settings(project)
        if l3.queue_path(project).exists():
            request_l3_drain(project)
        for slug in dispatch.pending_task_operations(project):
            spawn(f"task-operation:{project}:{slug}", dispatch.run_task_operation, project, slug)
        for item in dispatch.poll(project):
            spawn(f"finished:{project}:{item['task']['slug']}", on_l2_finished, project, item)
        resume_stranded_reports(project)
        for slug in dispatch.resume_due(project):
            request_task_resume(project, slug)
        dispatch_waiting(project)
        for t in S.list_tasks(project, include_archive=True):
            if t["state"] == "done" and not t.get("cleaned"):
                notes = dispatch.cleanup_after_done(project, t)
                deferred = any(note.startswith(("deferred ", "skipped ", "could not ")) for note in notes)
                if not deferred:
                    with S.project_lock(project):
                        t2 = S.load_task(project, t["slug"]); t2["cleaned"] = S.now(); S.save_task(project, t2)
                S.append_event(project, t["slug"], "cleanup", notes=notes)
                log(f"[{project}/{t['slug']}] cleanup{' deferred' if deferred else ''}: {notes}")
    except Exception as e:  # noqa: BLE001
        log(f"[{project}] tick failed: {e}\n{traceback.format_exc()}")
        incidents.system_fault("tick", f"{project}: {e}", project=project)


_last_digest_day = [None]


def morning_digest() -> None:
    now = datetime.now()
    if now.hour >= 8 and _last_digest_day[0] != now.date():
        _last_digest_day[0] = now.date()
        digest.text()


def timer_loop() -> None:
    while True:
        try:
            tick()
        except Exception as e:  # noqa: BLE001
            log(f"tick: {e}\n{traceback.format_exc()}")
            try:
                incidents.system_fault("tick", str(e))
            except Exception as e2:  # noqa: BLE001 — the fault channel itself is broken: the journal is the last resort
                log(f"tick: could not record fault: {e2}")
        time.sleep(config.AGENT_POLL_SECONDS)


# ---- HTTP -------------------------------------------------------------------

class _HeadWriter:
    """Pass GET's headers through while dropping its entity body."""

    def __init__(self, wfile):
        self._wfile = wfile
        self.drop = False

    def __getattr__(self, name):
        return getattr(self._wfile, name)

    def write(self, data):
        return len(data) if self.drop else self._wfile.write(data)

    def flush(self):
        return self._wfile.flush()


class Handler(BaseHTTPRequestHandler):
    server_version = "altd/0.1"

    _seen_clients: set = set()

    def log_message(self, fmt, *args):  # quieter: one line per new client address, nothing per request
        ip = self.client_address[0]
        if ip not in self._seen_clients:
            self._seen_clients.add(ip)
            log(f"first request from {ip}: {self.command} {self.path}")

    def end_headers(self) -> None:
        super().end_headers()
        if self.command == "HEAD" and getattr(self, "_head", False):
            self.wfile.drop = True

    def do_HEAD(self) -> None:
        wfile = self.wfile
        self.wfile = _HeadWriter(wfile)
        self._head = True
        try:
            self.do_GET()
        finally:
            self._head = False
            self.wfile = wfile

    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path, ctype: str | None = None) -> None:
        if not path.exists():
            self._json({"error": "not found"}, 404)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype or mimetypes.guess_type(str(path))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _plain(self, text: str, code: int) -> None:
        body = f"{text}\n".encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _design(self, parts: list[str]) -> None:
        """A project's wireframe boards, read from its own checkout on every request: the viewer
        shows what is on main, with nothing to rebuild after a merge. Two subtrees of that checkout
        are readable, only the listed extensions, and nothing is cached, so an edit that lands is the
        edit the browser draws. A project without boards, a directory, and an escape attempt are all
        the same plain 404."""
        project = unquote(parts[1]) if len(parts) > 1 else ""
        entry = design_viewer_url(project)
        if entry is None:
            return self._plain("not found", 404)
        if len(parts) == 2:  # the stable per-project link; the boards' relative imports need the depth
            return self._redirect(entry)
        root = config.project_path(project).resolve()
        try:
            resolved = (root / unquote("/".join(parts[2:]))).resolve()
        except (OSError, ValueError):  # embedded NUL and friends
            return self._plain("not found", 404)
        ctype = DESIGN_TYPES.get(resolved.suffix.lower())
        readable = any(resolved.is_relative_to((root / tree).resolve()) for tree in DESIGN_TREES)
        if ctype is None or not readable or not resolved.is_file():
            return self._plain("not found", 404)
        data = resolved.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _static(self, raw_path: str) -> None:
        """The built SPA (web/dist): hashed /assets/* immutable, index.html no-store, and any
        other GET falls back to index.html so client-side routes deep-link. A missing build is
        an explicit 503 naming `make web`, never a silent fallback."""
        dist = config.WEB_DIST.resolve()
        index = dist / "index.html"
        if not index.is_file():
            return self._json({"error": "web UI not built: web/dist is missing — run `make web` first"}, 503)
        rel = unquote(raw_path).lstrip("/")
        try:
            resolved = (dist / rel).resolve() if rel else index
        except (OSError, ValueError):  # embedded NUL and friends
            return self._json({"error": "not found"}, 404)
        if not resolved.is_relative_to(dist):  # traversal (incl. percent-encoded) and symlink escapes
            return self._json({"error": "not found"}, 404)
        if not resolved.is_file():
            if resolved.relative_to(dist).parts[:1] == ("assets",):
                # a miss under the hashed build output is a stale index, not a client route:
                # serving index.html there hands JS/CSS a text/html body (MIME parse error)
                return self._json({"error": "not found"}, 404)
            resolved = index  # SPA fallback: /projects/x, /chat/y, ... render client-side
        data = resolved.read_bytes()
        immutable = resolved != index and resolved.relative_to(dist).parts[:1] == ("assets",)
        ctype = "text/html; charset=utf-8" if resolved.suffix == ".html" else (
            mimetypes.guess_type(str(resolved))[0] or "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=31536000, immutable" if immutable else "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    def _transcribe_voice(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._json({"error": "invalid recording size"}, 400)
        if length <= 0:
            return self._json({"error": "expected an audio recording"}, 400)
        if length > VOICE_MAX_BODY:
            return self._json({"error": "recording is too large"}, 413)
        content_type = self.headers.get("Content-Type") or ""
        if content_type.split(";", 1)[0].strip().lower() not in VOICE_TYPES:
            return self._json({"error": "This browser's recording format is not supported."}, 415)
        raw = self.rfile.read(length)
        if len(raw) != length:
            return self._json({"error": "the recording upload was incomplete"}, 400)
        try:
            text = transcribe_voice(raw, content_type)
        except VoiceInputError as exc:
            log(f"voice transcription failed ({exc.status}): {type(exc.__cause__).__name__ if exc.__cause__ else str(exc)}")
            return self._json({"error": str(exc)}, exc.status)
        except Exception as exc:  # noqa: BLE001 — internals stay in the private log, never the response
            log(f"voice transcription failed: {exc!r}\n{traceback.format_exc()}")
            return self._json(
                {"error": "Voice transcription is temporarily unavailable. You can keep typing and try again."},
                503,
            )
        log(f"voice transcription: {len(text)} characters from {length} uploaded bytes")
        return self._json({"text": text})

    def _stream_open(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

    def _stream_send(self, obj: dict) -> None:
        data = (json.dumps(obj, default=str) + "\n").encode()
        self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
        self.wfile.flush()

    def _stream_close(self) -> None:
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def do_GET(self) -> None:
        u = urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]
        q = parse_qs(u.query)
        try:
            if parts and parts[0] == "ca.crt":  # the local CA, for installing on a phone once
                return self._file(config.TLS_DIR / "ca.crt", "application/x-x509-ca-cert")
            if parts and parts[0] == DESIGN_ROUTE:
                return self._design(parts)
            if not parts or parts[0] != "api":
                return self._static(u.path)
            api = parts[1] if len(parts) > 1 else ""
            if api == "overview":
                return self._json(overview())
            if api == "project" and len(parts) > 2:
                return self._json(project_view(parts[2]))
            if api == "task" and len(parts) > 3:
                return self._json(task_view(parts[2], parts[3]))
            if api == "transcript" and len(parts) > 3:
                try:
                    return self._json(transcript.view(
                        parts[2], parts[3],
                        engine=q.get("engine", [""])[0], session_id=q.get("session_id", [""])[0],
                        cursor=int(q.get("cursor", ["0"])[0]), raw=q.get("raw", ["0"])[0] == "1"))
                except (KeyError, transcript.TranscriptAccessError):
                    return self._json({"error": "transcript unavailable for this task generation"}, 404)
            if api == "monitor":
                return self._json({"seats": route.seats(), "routing": monitor.routing(),
                                   "sessions": monitor.sessions()})
            if api == "digest":
                return self._json({"text": digest.text()})
            if api == "chat" and len(parts) > 2:
                project = parts[2]
                lifecycle = l3.chat_state(project, int(q.get("limit", ["60"])[0]))
                return self._json({**lifecycle, "l3": l3.info(project),
                                   "engine": config.project(project).get("l3_engine")})
            return self._json({"error": "unknown api"}, 404)
        except (ssl.SSLError, BrokenPipeError, ConnectionResetError) as e:  # the client left mid-response (a phone's audio player, a closed tab): not a fault
            log(f"GET {self.path}: client went away ({type(e).__name__}: {e})")
            return
        except Exception as e:  # noqa: BLE001
            log(f"GET {self.path}: {e}\n{traceback.format_exc()}")
            return self._json({"error": str(e)}, 500)

    def do_POST(self) -> None:
        u = urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]
        try:
            api = parts[1] if len(parts) > 1 and parts[0] == "api" else ""
            if api == "transcribe":
                return self._transcribe_voice()
            o = self._body()
            if api == "issue":
                try:
                    if o.keys() - {"project", "operation", "body", "title", "labels", "number", "reason", "actor"}:
                        raise ValueError("alt issue: unsupported fields")
                    url = issue_write(o["project"], o.get("operation"), o.get("body", ""), actor="operator",
                                      title=o.get("title", ""), labels=o.get("labels"), number=o.get("number"),
                                      reason=o.get("reason"))
                    return self._json({"url": url})
                except (ValueError, OSError, subprocess.SubprocessError) as exc:
                    return self._json({"error": str(exc)}, 400)
            if api == "project" and len(parts) > 2 and parts[2] == "add":
                name = o["name"]
                restoring = name not in config.load_projects() and bool(l3.chat_history(name, 1))
                try:
                    with config.add_project(name, path=o.get("path"), approval=o.get("approval") or "default",
                                            wip=o.get("wip")) as entry:
                        ensure_l3_verb_broker(name)
                except ValueError as exc:
                    return self._json({"error": str(exc)}, 400)
                except (OSError, RuntimeError) as exc:
                    return self._json({"error": f"cannot establish the L3 verb boundary: {exc}"}, 500)
                S.regen_state_md(name)
                spawn(f"start:{name}", start_l3, name)
                return self._json({"ok": True, "project": entry, "restored": restoring})
            if api == "project" and len(parts) > 2 and parts[2] == "remove":
                try:
                    config.remove_project(o["name"])
                except config.ProjectBusy as exc:
                    return self._json({"error": str(exc)}, 409)
                remove_l3_verb_broker(o["name"])
                return self._json({"ok": True})
            if api == "decide":
                # SPEC.md §5.2 note 5: the option (a label, key, or index) and the note are recorded on the
                # task before the lifecycle acts; the L2 reads the choice when it resumes.
                project, slug = o["project"], o["slug"]
                t = S.load_task(project, slug)
                if t["state"] != "blocked":
                    return self._json({"error": "only blocked tasks need a user decision"}, 409)
                try:
                    decision = T.decide(project, slug, o.get("option"), o.get("note"))
                except T.TransitionError as exc:
                    return self._json({"error": str(exc)}, 400)
                if decision["key"] == "reject":
                    request_daemon_task_operation(project, slug, "reject",
                                                  decision["note"] or "rejected by Burak", actor="burak")
                else:
                    if decision["message"]:
                        # Persist the answer without its ordinary timer wake; the following durable request
                        # becomes the only resume owner before a runner can start.
                        T.message(project, slug, "burak", decision["message"], wake_blocked=False)
                    request_daemon_task_operation(project, slug, "resume",
                                                  decision["message"] or "resumed by Burak", actor="burak")
                return self._json({"ok": True, "queued": True, "decision": decision,
                                   "state": S.load_task(project, slug)["state"]})
            if api == "task" and len(parts) > 2 and parts[2] == "action":
                project, slug, action = o["project"], o["slug"], o["action"]
                reason = o.get("reason") or f"{action} by Burak"
                if action == "reject":
                    request_daemon_task_operation(project, slug, "reject", reason, actor="burak")
                elif action == "done":
                    T.done(project, slug, actor="burak")
                elif action == "stop":
                    request_daemon_task_operation(project, slug, "stop", reason, actor="burak")
                elif action == "dispatch":
                    spawn(f"dispatch:{project}", dispatch_waiting, project)
                else:
                    return self._json({"error": f"unknown task action {action}"}, 400)
                return self._json({"ok": True, "state": S.load_task(project, slug)["state"]})
            if api == "l2" and len(parts) > 2 and parts[2] == "message":
                project, slug = o["project"], o["slug"]
                text = str(o.get("text") or "").strip()
                if not text:
                    return self._json({"error": "empty task message"}, 400)
                try:
                    message = T.message(project, slug, "burak", text)
                except T.TransitionError as exc:
                    return self._json({"error": str(exc)}, 409)
                if S.load_task(project, slug).get("state") == "blocked":
                    request_task_resume(project, slug)
                return self._json({"ok": True, "message": message})
            if api == "l3" and len(parts) > 2 and parts[2] == "reset":
                l3.reset(o["project"], "reset from the page"); return self._json({"ok": True})
            if api == "l3" and len(parts) > 2 and parts[2] == "start":
                name = config.project(o["project"]) and o["project"]
                return self._json({"ok": True, "started": spawn(f"start:{name}", start_l3, name)})
            if api == "l3" and len(parts) > 2 and parts[2] == "engine":
                engine = o.get("engine") or None
                if engine and engine not in config.ENGINES:
                    return self._json({"error": f"engine must be one of {', '.join(config.ENGINES)}"}, 400)
                config.set_l3_engine(o["project"], engine)
                return self._json({"ok": True, "engine": engine})
            if api == "chat" and len(parts) > 2 and parts[2] == "remove":
                project = o["project"]
                if not l3.drop_queued(project, str(o.get("id") or "")):
                    return self._json({"error": "that message has already started"}, 409)
                return self._json({"ok": True, "queued": l3.queued(project)})
            if api == "chat":
                project, text = o["project"], (o.get("text") or "").strip()
                if not config.is_managed(project):
                    return self._json({"error": "This project is not managed. Add its folder again to attach L3."}, 409)
                if not text:
                    return self._json({"error": "empty"}, 400)
                # A follow-up from a decision page names the decision's task (SPEC.md §5.2 note 6).
                try:
                    slug = S.require_task_slug(o["slug"]) if o.get("slug") else None
                except ValueError as exc:
                    return self._json({"error": str(exc)}, 400)
                if l3.busy(project) or config.restart_in_progress():
                    # Burak types faster than L3 answers. The message waits for the turn boundary in the
                    # durable queue instead of bouncing off a busy L3; the running turn drains it there.
                    try:
                        row = l3.queue_message(project, text, trigger="chat", role="burak", slug=slug)
                    except ValueError as exc:
                        return self._json({"error": str(exc)}, 409)
                    request_l3_drain(project)
                    return self._json({"queued": row})
                self._stream_open()
                gone: list[BaseException] = []

                def send(t: str) -> None:
                    # The turn owns its answer, not the page that started it. 2026-09-03 07:54Z: Burak refreshed
                    # Chat mid-turn; the write error unwound the turn, the answer was never logged and the
                    # session bookkeeping was skipped. A lost client ends the stream and nothing else.
                    if gone:
                        return
                    try:
                        self._stream_send({"t": t})
                    except (ssl.SSLError, BrokenPipeError, ConnectionResetError) as e:
                        gone.append(e)
                        log(f"POST {self.path}: client went away mid-turn ({type(e).__name__}: {e}); the turn continues")

                def started(_pid) -> None:
                    # The page keys its pending bubble on the turn id from here on, so a poll that already
                    # shows the server's own rows for this turn never doubles them (SPEC.md §4.2).
                    if gone:
                        return
                    try:
                        self._stream_send({"turn": l3.active(project)})
                    except (ssl.SSLError, BrokenPipeError, ConnectionResetError) as e:
                        gone.append(e)
                        log(f"POST {self.path}: client went away as the turn started ({type(e).__name__}: {e})")

                res = server_l3_turn(project, text, trigger="chat", on_text=send, on_start=started,
                                     **({"slug": slug} if slug else {}))
                if gone:
                    return
                self._stream_send({"done": {k: res.get(k) for k in (
                    "session_id", "context_percent", "turns", "cost", "error", "engine", "turn_id")}})
                self._stream_close()
                return
            if api == "restart":
                status = restart_status()
                if not status:
                    return self._json({"error": "no restart is pending"}, 409)
                if status["waiting_for"]:
                    return self._json({"error": "restart waits for " + ", ".join(status["waiting_for"])}, 409)
                try:
                    return self._json(restart_service())
                except RestartBusy as exc:
                    return self._json({"error": str(exc)}, 409)
            return self._json({"error": "unknown api"}, 404)
        except (ssl.SSLError, BrokenPipeError, ConnectionResetError) as e:  # the client left mid-response (a phone's audio player, a closed tab): not a fault
            log(f"POST {self.path}: client went away ({type(e).__name__}: {e})")
            return
        except Exception as e:  # noqa: BLE001
            log(f"POST {self.path}: {e}\n{traceback.format_exc()}")
            try:
                self._json({"error": str(e)}, 500)
            except Exception:  # noqa: BLE001
                pass


def restart_status() -> dict | None:
    """Pending backend/web activation plus what the page's early Restart button waits for."""
    pending = S.read_json(config.MONITOR_DIR / dispatch.RESTART_PENDING)
    if not pending:
        return None
    return {**pending, "waiting_for": restart_waiting_for()}


def restart_waiting_for(*, check_activity: bool = True) -> list[str]:
    projects = list(config.load_projects())
    waiting = [f"{p}/{t['slug']}" for p in projects for t in S.list_tasks(p)
               if t.get("dispatching") or t.get("resume_claim")]
    waiting += [f"{p} L3" for p in projects if l3.busy(p)]
    if check_activity:
        with config.restart_lock(exclusive=True) as quiet:
            if not quiet:
                waiting.append("dispatch, L3 turn or report verification in flight")
    return waiting


RESTART_GRACE_SECONDS = 600  # the restart unit builds the web bundle first; the old process is gone well within this


def auto_restart() -> None:
    """Activate merged backend or web changes at the quiet point (Burak, 2026-09-03: a merged fix is not a fix
    until the deployed service and bundle contain it). Decision 11: only dispatch, L3 and report handling
    hold activation; detached running workers survive it. The unit rechecks before touching the service."""
    status = restart_status()
    if not status or status.get("failed"):
        return
    flag = config.MONITOR_DIR / dispatch.RESTART_PENDING
    pend = S.read_json(flag, {}) or {}
    requested = pend.get("requested_at")
    if requested:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(requested)).total_seconds()
        if age < RESTART_GRACE_SECONDS:
            return  # the restart unit is still building and swapping the bundle
        pend["failed"] = S.now()
        S.write_json(flag, pend)
        detail = (f"restart unit {pend.get('unit')} did not restart the service within "
                  f"{RESTART_GRACE_SECONDS // 60} minutes; see journalctl --user -u {pend.get('unit')}")
        log(f"auto-restart: {detail}; new dispatches resume")
        incidents.system_fault("restart", detail)
        return
    if status["waiting_for"]:
        return
    try:
        res = restart_service()
    except RestartBusy:
        return  # work claimed the quiet point before the restart requester
    except RuntimeError as e:
        pend["failed"] = S.now()
        S.write_json(flag, pend)
        log(f"auto-restart could not start: {e}; new dispatches resume")
        incidents.system_fault("restart", f"auto-restart could not start: {e}")
        return
    log(f"quiet point: restarting for {len(pend.get('files', []))} changed file(s) via unit {res['unit']}")


class RestartBusy(RuntimeError):
    pass


def restart_service() -> dict:
    with config.restart_lock(exclusive=True) as quiet:
        if not quiet or restart_waiting_for(check_activity=False):
            raise RestartBusy("restart waits for dispatch, L3 turn or report verification")
        flag = config.MONITOR_DIR / dispatch.RESTART_PENDING
        pend = S.read_json(flag, {}) or {}
        if pend.get("requested_at") and not pend.get("failed"):
            return {"ok": True, "unit": pend.get("unit")}
        # Publish before releasing the gate, including the manual button path: no launch can race the unit.
        res = _request_restart_unit()
        pend.pop("failed", None)
        pend.update({"requested_at": S.now(), "unit": res["unit"]})
        S.write_json(flag, pend)
        return res


def _request_restart_unit() -> dict:
    """Run the operator restart script as a transient user unit: outside altd's cgroup, it survives the restart."""
    unit = f"altitude-restart-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    cmd = [engines.SYSTEMD_RUN_BIN, "--user", "--collect", "--quiet", f"--unit={unit}", "--same-dir",
           f"--setenv=PATH={os.environ.get('PATH', '')}", "--",
           sys.executable, str(config.REPO / "scripts" / "restart_altitude.py")]
    res = subprocess.run(cmd, cwd=str(config.REPO), capture_output=True, text=True, timeout=30)
    if res.returncode != 0:
        raise RuntimeError(f"systemd-run refused the restart unit: {(res.stderr or res.stdout).strip()[:300]}")
    log(f"guarded activation requested → unit {unit}; follow it with: journalctl --user -u {unit}")
    return {"ok": True, "unit": unit}


def overview() -> dict:
    projects = config.discover_projects()
    for p in projects:
        if p["managed"]:
            ts = S.list_tasks(p["name"])
            p["counts"] = {s: sum(1 for t in ts if t["state"] == s) for s in S.STATES}
            p["counts"]["fault"] = sum(1 for t in ts if t.get("fault"))  # the rail's danger dot
            # A block waiting on L3 is Altitude's wait, so the rail dot keeps running (SPEC.md §3.1).
            p["counts"]["waits_l3"] = sum(1 for t in ts if t["state"] == "blocked" and not t.get("fault")
                                          and not t.get("resume_after") and t.get("waiting_on") == "l3")
            p["l3"] = l3.info(p["name"])
            p["hold"] = S.read_json(config.project_dir(p["name"]) / "hold.json")
    return {"projects": projects, "queue": digest.queue(), "wip": digest.wip(), "quota": monitor.quota(),
            "engines": route.engine_readouts(), "roots": [home_relative(r) for r in config.PROJECT_ROOTS],
            "operator": config.OPERATOR, "restart": restart_status(), "now": S.now()}


def home_relative(path: Path) -> str:
    """A folder as First run names it: `~/Projects`, never the whole home path."""
    try:
        return "~/" + path.expanduser().relative_to(Path.home()).as_posix()
    except ValueError:
        return str(path)


def repository_url(origin: str) -> str | None:
    """The GitHub web URL for an HTTPS or SSH origin, otherwise None."""
    match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
                         r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?", origin.strip(), re.I)
    return f"https://github.com/{match[1]}/{match[2]}" if match else None


ISSUE_CLOSE_REASONS = ("completed", "not-planned")


def issue_parser() -> argparse.ArgumentParser:
    """One exact grammar for the operator CLI and the project-bound L3 socket."""
    class Parser(argparse.ArgumentParser):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **{**kwargs, "allow_abbrev": False})

        def error(self, message):
            raise ValueError(f"alt issue: {message}")

        def exit(self, status=0, message=None):
            raise ValueError(message or "use alt issue new/upstream --title TITLE -, comment NUMBER -, or close NUMBER --reason completed|not-planned")

    parser = Parser(prog="alt issue", add_help=False)
    commands = parser.add_subparsers(dest="operation", required=True)
    new = commands.add_parser("new")
    new.add_argument("--title", required=True)
    new.add_argument("--label", action="append", dest="labels")
    upstream = commands.add_parser("upstream")
    upstream.add_argument("--title", required=True)
    comment = commands.add_parser("comment")
    comment.add_argument("number", type=int)
    for command in (new, comment, upstream):
        command.add_argument("text", choices=["-"])
    close = commands.add_parser("close")
    close.add_argument("number", type=int)
    close.add_argument("--reason", required=True, choices=ISSUE_CLOSE_REASONS)
    return parser


def upstream_issue_repository() -> str:
    """The installation's product seam, independent of the calling project's registry or origin."""
    target = config.UPSTREAM_ISSUE_REPOSITORY
    if target is None:
        try:
            origin = subprocess.run(["git", "remote", "get-url", "origin"], cwd=config.REPO,
                                    capture_output=True, text=True, timeout=10)
            target = repository_url(origin.stdout) if origin.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            target = None
    elif re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", target):
        target = "https://github.com/" + target
    repository = repository_url(target or "")
    if not repository:
        raise ValueError("alt issue upstream: operator must configure ALTITUDE_UPSTREAM_ISSUE_REPOSITORY "
                         "in altd as the Altitude GitHub owner/repository, or install from a GitHub origin")
    return repository


def upstream_issue_body(body: str) -> str:
    """Accept only a deliberately authored reproduction; never read local incident/session evidence."""
    try:
        report = json.loads(body)
    except ValueError:
        report = None
    required = {"expected", "actual", "reproduction"}
    if (not isinstance(report, dict) or not required <= report.keys()
            or report.keys() - required - {"version"}
            or any(not isinstance(value, str) or not value.strip() for value in report.values())):
        raise ValueError("alt issue upstream: stdin must be a fictional/redacted JSON object with nonempty "
                         "expected, actual, reproduction strings and optional version; no evidence attachments")
    return "\n\n".join(f"## {label}\n{report.get(key, 'unknown')}" for key, label in (
        ("expected", "Expected behavior"), ("actual", "Actual behavior"),
        ("reproduction", "Fictional/redacted reproduction"), ("version", "Altitude version"))) + "\n"


def issue_write(project: str, operation: str, body: str, *, actor: str,
                title: str = "", labels: list[str] | None = None, number: int | None = None,
                reason: str | None = None) -> str:
    """Altd owns project-local issues and the create-only upstream reporting exception."""
    if actor not in ("l3", "operator"):
        raise ValueError("alt issue: not available to an L2 worker")
    if operation not in ("new", "comment", "close", "upstream"):
        raise ValueError("alt issue: only new, comment, close, and upstream are available")
    creating = operation in ("new", "upstream")
    if (not isinstance(body, str) or not isinstance(title, str)
            or labels is not None and (not isinstance(labels, list) or any(not isinstance(x, str) for x in labels))):
        raise ValueError("alt issue: body, title, and labels must be text")
    if creating and not title.strip():
        raise ValueError(f"alt issue {operation}: title is required")
    if operation in ("comment", "close") and (type(number) is not int or number < 1):
        raise ValueError(f"alt issue {operation}: a positive issue number is required")
    if (not creating and (title or labels) or creating and number is not None
            or operation == "upstream" and labels is not None
            or operation != "close" and reason is not None):
        raise ValueError("alt issue: fields do not match the operation")
    if operation == "close" and (reason not in ISSUE_CLOSE_REASONS or body):
        raise ValueError("alt issue close: --reason completed|not-planned is required; no body is accepted")
    if operation == "upstream":
        body = upstream_issue_body(body)
    # Private incident evidence boundary: local evidence never leaves the machine in a public issue.
    public_text = unquote("\n".join([body, title, *(labels or [])]))
    home = str(Path.home()) + "/"
    absolute_paths = re.findall(r"/[^\s`'\"<>\[\]{}()]+", public_text)
    if (home in public_text
            or any((os.path.normpath("/" + path.lstrip("/")) + "/").startswith(home) for path in absolute_paths)
            or re.search(r"(?:/home/|/Users/|~/|\$HOME/|[A-Z]:\\Users\\)|"
                         r"\bI-\d{8}-\d{6}(?:-\d+)?\.md\b|\bincidents(?:/|\.jsonl\b)|"
                         r"\b(?:conversation|inbox|faults|chat)\.jsonl?\b|\.altitude/", public_text, re.I)):
        raise ValueError("Private incident evidence boundary: an issue is public; home paths and private incident evidence must stay on this machine")
    if re.search(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|"
                 r"AKIA[A-Z0-9]{16})\b|-----BEGIN [\w ]*PRIVATE KEY-----|"
                 r"\b(?:authorization\s*[:=]\s*(?:bearer|basic)\s+|"
                 r"(?:[\w-]*(?:token|password|secret|api[_-]?key))[\"']?\s*[:=]\s*[\"']?)"
                 r"(?!\[REDACTED\]|<REDACTED>)[A-Za-z0-9_+/.-]{8,}|"
                 r"https?://[^\s/@:]+:[^\s/@]+@", public_text, re.I):
        raise ValueError("Private credential boundary: redact credentials and tokens before publishing an issue")
    checkout = config.project_path(project)
    if operation == "upstream":
        repository = upstream_issue_repository()
    else:
        origin = subprocess.run(["git", "remote", "get-url", "origin"], cwd=checkout,
                                capture_output=True, text=True, timeout=10)
        repository = repository_url(origin.stdout) if origin.returncode == 0 else None
    if not repository:
        raise ValueError("alt issue: checkout origin must identify a GitHub repository")
    args = ["gh", "issue", "create" if creating else operation]
    if creating:
        args += [f"--title={title}", *(f"--label={label}" for label in labels or [])]
    else:
        args.append(str(number))
    args += ["--repo", repository]
    args += ["--reason", reason.replace("-", " ")] if operation == "close" else ["--body-file", "-"]
    env = engines.clean_env()
    env.pop("GH_REPO", None)
    try:
        result = subprocess.run(args, input=body, cwd=checkout, env=env,
                                capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        if operation != "upstream":
            raise
        raise ValueError("alt issue upstream: GitHub request unavailable or timed out. Operator: check for an "
                         "existing report before retrying; verify altd's gh installation, authentication "
                         f"and access to {repository}.") from exc
    if result.returncode:
        if operation == "upstream":
            raise ValueError("alt issue upstream: GitHub refused the report. Operator: check for an existing "
                             "report before retrying; verify altd's gh authentication and issue access "
                             f"to {repository}.")
        raise ValueError("alt issue: " + " ".join((result.stderr or "gh failed").split()))
    url = f"{repository}/issues/{number}" if operation == "close" else result.stdout.strip()
    if operation == "upstream" and not re.fullmatch(re.escape(repository) + r"/issues/[1-9]\d*", url, re.I):
        raise ValueError("alt issue upstream: GitHub returned no confirmed issue URL. Operator: check for the "
                         f"report before retrying at {repository}/issues")
    with S.project_lock(project):
        S.project_log(project, f"issue-{operation}", actor=actor,
                      title=title if creating else f"Issue #{number}", url=url,
                      **({"number": number, "reason": reason} if operation == "close" else {}))
    return url


def project_view(name: str) -> dict:
    proj = config.project(name)
    origin = subprocess.run(["git", "remote", "get-url", "origin"], cwd=config.project_path(name),
                            capture_output=True, text=True, timeout=10)
    live = {s["slug"]: s for s in monitor.sessions() if s.get("kind") == "l2" and s.get("project") == name}
    tasks = []
    for t in S.list_tasks(name):
        d = S.task_dir(name, t["slug"])
        prog = (d / "progress.md").read_text()[-1500:] if (d / "progress.md").exists() else ""
        tasks.append({**t, "live": live.get(t["slug"]), "progress_tail": prog, "has": {f: (d / f"{f}.md").exists() for f in ("request", "brief", "report", "digest", "progress")}})
    order = {"blocked": 0, "running": 1, "reported": 2, "queued": 3}
    tasks.sort(key=lambda t: (order.get(t["state"], 9), t["updated"]))
    return {"name": name, "config": proj, "l3": l3.info(name), "busy": l3.busy(name), "tasks": tasks,
            "design_viewer": design_viewer_url(name), "repository": repository_url(origin.stdout),
            "archive": [{k: t.get(k) for k in ("slug", "state", "title", "updated", "prs")} for t in S.list_tasks(name, True) if t["state"] in ("done", "rejected")][-20:],
            "decisions": T.decisions(name), "log": S.read_project_log(name, 40),
            "incidents": [r for r in incidents.index() if r["project"] == name][-10:], "hold": S.read_json(config.project_dir(name) / "hold.json"),
            "state_md": (config.project_dir(name) / "STATE.md").read_text() if (config.project_dir(name) / "STATE.md").exists() else ""}


def task_view(project: str, slug: str) -> dict:
    t = S.load_task(project, slug)
    d = S.task_dir(project, slug)
    files = {f: (d / f"{f}.md").read_text() for f in ("request", "brief", "report", "digest", "progress") if (d / f"{f}.md").exists()}
    return {**t, "files": files, "messages": T.task_messages(project, slug),
            "events": S.read_events(project, slug),
            "report_json": S.read_json(d / "report.json"), "live": next((s for s in monitor.sessions() if s.get("kind") == "l2" and s.get("slug") == slug and s.get("project") == project), None)}


def install_statusline() -> dict:
    """Wrap the global statusline so interactive sessions feed the quota monitor. Edits ~/.claude/settings.json."""
    settings = Path.home() / ".claude" / "settings.json"
    cur = S.read_json(settings, {}) or {}
    sl = cur.get("statusLine") or {}
    wrapper = str(config.HOOKS / "statusline-monitor.sh")
    if sl.get("command") == wrapper:
        return {"ok": True, "already": True}
    orig = sl.get("command")
    cur["statusLine"] = {"type": "command", "command": wrapper}
    if orig:
        cur.setdefault("env", {})["ALTITUDE_ORIG_STATUSLINE"] = orig
    S.write_json(settings, cur)
    return {"ok": True, "wrapped": orig}


def main(host: str | None = None, port: int | None = None) -> None:
    config.ensure_root()
    if os.environ.get("ALTITUDE_SERVICE"):  # only the systemd instance clears the restart-pending flag
        try:
            git_policy.service_preflight(config.REPO)
            git_policy.require_hooks_installed(config.REPO)
        except git_policy.GitPolicyError as e:
            log(f"service startup refused: {e}")
            raise SystemExit(1) from e
    host = host or config.HOST
    port = port or config.PORT
    try:
        srv = ThreadingHTTPServer((host, port), Handler)
    except OSError as e:
        # No silent fallback to loopback: exit non-zero and let systemd retry when the tunnel is ready.
        log(f"cannot bind {host}:{port} ({e}); exiting so the unit restarts (RestartSec)")
        raise SystemExit(1)
    srv.daemon_threads = True
    try:
        for project in config.load_projects():
            if config.is_managed(project):
                ensure_l3_verb_broker(project)
    except (OSError, RuntimeError) as e:
        stop_l3_verb_brokers()
        srv.server_close()
        log(f"cannot bind the L3 verb broker ({e}); refusing to start without the confinement boundary")
        raise SystemExit(1) from e
    scheme = "http"
    crt, key = config.TLS_DIR / "server.crt", config.TLS_DIR / "server.key"
    if config.TLS and crt.is_file() and key.is_file():
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(crt, key)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        scheme = "https"
    elif config.TLS:
        log(f"no certificate in {config.TLS_DIR} — serving plain http (run `alt tls-init` for https)")
    # Decision 11: do not release waiting launches if the replacement cannot bind its API or brokers.
    if os.environ.get("ALTITUDE_SERVICE"):
        (config.MONITOR_DIR / dispatch.RESTART_PENDING).unlink(missing_ok=True)
    if os.environ.get("ALTITUDE_TIMERS", "1") != "0":
        restart_notice()
        threading.Thread(target=timer_loop, name="timers", daemon=True).start()
    else:
        log("timers disabled (ALTITUDE_TIMERS=0): serve-only instance, no polling/dispatch — for smoke tests against a shared ALTITUDE_HOME")
    log(f"altd listening on {scheme}://{host}:{port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        stop_l3_verb_brokers()


def tls_init(ip: str | None = None) -> dict:
    """Self-signed local CA + server certificate for the WireGuard address (same recipe as the pocketbook's make-certs.sh:
    EC P-256, CA 10 years, server cert 397 days because iOS rejects longer). Idempotent for the CA."""
    d = config.ROOT / "tls" if config.TLS_DIR == config._POCKETBOOK_TLS and not (config._POCKETBOOK_TLS / "ca.key").exists() else config.TLS_DIR
    d.mkdir(parents=True, exist_ok=True)
    ip = ip or config.HOST
    run = lambda *a: subprocess.run(list(a), cwd=str(d), check=True, capture_output=True, text=True)  # noqa: E731
    if not (d / "ca.crt").exists():
        run("openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", "ca.key")
        run("openssl", "req", "-x509", "-new", "-key", "ca.key", "-sha256", "-days", "3650", "-out", "ca.crt",
            "-subj", "/CN=Altitude local CA", "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    run("openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", "server.key")
    run("openssl", "req", "-new", "-key", "server.key", "-subj", "/CN=altitude", "-out", "server.csr")
    ext = d / "server.ext"
    ext.write_text(f"basicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=IP:{ip},IP:127.0.0.1,DNS:localhost\n")
    run("openssl", "x509", "-req", "-in", "server.csr", "-CA", "ca.crt", "-CAkey", "ca.key", "-CAcreateserial", "-days", "397", "-sha256",
        "-out", "server.crt", "-extfile", str(ext))
    (d / "server.csr").unlink(missing_ok=True); ext.unlink(missing_ok=True)
    for f in ("ca.key", "server.key"):
        (d / f).chmod(0o600)
    end = subprocess.run(["openssl", "x509", "-enddate", "-noout", "-in", str(d / "server.crt")], capture_output=True, text=True).stdout.strip()
    return {"dir": str(d), "ip": ip, "server_cert": end, "phone": f"open http://{ip}:{config.PORT}/ca.crt once (with ALTITUDE_TLS=0) or install ca.crt by other means, then trust it in the phone's certificate settings"}
