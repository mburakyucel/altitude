"""altd — the Altitude web/API server and task timers."""
from __future__ import annotations
import argparse
import base64
import gzip
import hashlib
import ipaddress
import json
import mimetypes
import os
import re
import select
import socket
import socketserver
import ssl
import stat
import subprocess
import sys
import threading
import time
import traceback
import uuid
from collections import OrderedDict
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from http.cookies import CookieError, SimpleCookie
import http.server
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote, unquote

from . import access, chats, config, digest, dispatch, door, engines, git_policy, images, improve, incidents, installation, l3, line, monitor, platform, project_agents, project_setup, push, qr, repair, reviews, route, schedule, speech, state as S, storage, suggestions, tasks as T, terminal, tls, transcript, validation, verify


class ThreadingHTTPServer(http.server.ThreadingHTTPServer):
    def __init__(self, server_address, handler, bind_and_activate=True):
        self.address_family = socket.AF_INET6 if ":" in server_address[0] else socket.AF_INET
        super().__init__(server_address, handler, bind_and_activate)

    def server_bind(self) -> None:
        # http.server names itself by a reverse lookup of the address, which held a Mac guest without a network in mDNS
        # past the installation's health deadline; nothing reads that name.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


LOG = config.ROOT / "altd.log"
_bg: dict[str, threading.Thread] = {}
_bg_guard = threading.Lock()
_l3_drain_requested: set[str] = set()
_l3_verb_brokers: dict[str, "_L3VerbServer"] = {}
_l3_verb_broker_guard = threading.Lock()
CAPACITY_RETRY_DELAYS = (30, 60, 120, 300, 600, 900)
# (project, slug) → (report owner, failed report turns, monotonic retry time); a restart retries at once.
_report_retries: dict[tuple[str, str], tuple[str, int, float]] = {}
REPORT_RETRY_DELAYS = (60, 300, 900, 3600)
L3_VERB_MAX_REQUEST = 4 << 20
L3_VERB_MAX_OUTPUT = 8 << 20
L3_VERB_REFUSED = "this socket serves only the project's running coordinator turn"
#: The commands a task's owner runs through altd. Its worker job, not this machine's key, admits them.
TASK_DOOR = (["api", "task", "run"], ["api", "task", "validate"], ["api", "task", "terminal"],
             ["api", "task", "review", "run"], ["api", "task", "door"])
TASK_DOOR_REFUSED = "Only the running owner of this task, from its own worker, may use this command."
#: Altitude records what a paired device's sign-in sends as the operator's, so no agent may send it.
AGENT_REFUSED = "Altitude's own agents cannot act with a paired device's sign-in."
# The coordinator's `gh` only reads, from any repository its login can see; `alt issue` verbs are its only writes.
L3_GH_GROUPS = {"pr", "issue", "release", "repo", "run", "workflow", "ruleset", "label", "cache"}
L3_GH_VERBS = {"view", "list", "status", "checks", "diff", "watch", "check"}
L3_GH_RULE = (
    "The coordinator's gh only reads, from any repository its login can see: view/list/status/checks/diff/watch/"
    "check of pr, issue, release, repo, run, workflow, ruleset, label and cache; gh search; and gh api GET of a "
    "REST endpoint path, without fields, input or another host. Writes, downloads, auth and --web or -w "
    "(outside run list) are refused; use alt issue to write.")
L3_TASK_TARGETS = {
    "handoff", "release",
    "reject", "escalate", "events", "messages", "report", "show", "resume", "message", "stop",
    "paths", "hold-merge", "done", "status", "preserve-checkout", "recheck-ci",
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
# Shown images keep their checked type; a shown document crosses only inside its set's JSON.
SHOWN_TYPES = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}


def design_viewer_url(project: str) -> str | None:
    """The stable link to this project's wireframe viewer, or None when the project has no boards. Opening it
    from a paired browser redirects to that browser's read pass (`design_entry`)."""
    try:
        root = config.project_path(project)
    except (KeyError, ValueError, OSError):
        return None
    if not (root / DESIGN_ENTRY).is_file():
        return None
    return f"/{DESIGN_ROUTE}/{quote(project)}"


def design_entry(project: str, device_id: str) -> str:
    return f"/{DESIGN_ROUTE}/{quote(project)}/{access.design_pass(project, device_id)}/{DESIGN_ENTRY}"


# Hashed build assets that compress well travel gzip-encoded to browsers that accept it: a cold open
# after each update downloads the app script in a third of the bytes (docs/ARCHITECTURE.md#web-delivery).
# The punctuation model's packed weights barely compress and travel as they are.
GZIP_ASSETS = {".js", ".mjs", ".css", ".wasm", ".tsv", ".svg", ".json"}


_gzip_cache: OrderedDict[Path, tuple[tuple[int, ...], bytes]] = OrderedDict()
_gzip_guard = threading.Lock()


def _gzipped(path: Path, stream) -> bytes:
    """Cache at most 64 encodings, bound to the verified open file's identity and signature."""
    def signature():
        info = os.fstat(stream.fileno())
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns

    with _gzip_guard:
        before = signature()
        cached = _gzip_cache.get(path)
        if cached is not None and cached[0] == before:
            _gzip_cache.move_to_end(path)
            return cached[1]
        data = gzip.compress(stream.read(), compresslevel=9, mtime=0)
        if signature() != before:
            raise ValueError("asset changed while it was read")
        _gzip_cache[path] = (before, data)
        _gzip_cache.move_to_end(path)
        if len(_gzip_cache) > 64:
            _gzip_cache.popitem(last=False)
        return data



def _accepts_gzip(header: str | None) -> bool:
    """Whether Accept-Encoding allows gzip: its own quality, else the wildcard's (RFC 9110 §12.5.3)."""
    qualities = {}
    for part in (header or "").split(","):
        name, _, params = part.partition(";")
        quality = params.strip().lower().removeprefix("q=")
        try:
            qualities[name.strip().lower()] = float(quality) if quality else 1.0
        except ValueError:
            qualities[name.strip().lower()] = 0.0
    return qualities.get("gzip", qualities.get("*", 0.0)) > 0


# Host voice streams samples to `altitude/speech.py`, which transcribes them on this computer; browser
# recognition never uploads.
BODY_LIMIT = 1 << 20  # every other JSON request; image messages, voice audio and task door requests have their own


class VoiceInputError(RuntimeError):
    """A safe, useful voice-input error that may cross the HTTP boundary."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def _voice_selection(setting: dict) -> str:
    """Supported runtime replacement preserves replay; backend changes and removal invalidate it."""
    if setting["backend"] == "service":
        return setting["selection"]
    identity = speech.installation_id() if setting["backend"] == "host" else ""
    return hashlib.sha256(json.dumps([setting["backend"], identity]).encode()).hexdigest()


def voice_view(device: str | None = None) -> dict:
    setting = config.voice_setting()
    view = {"backend": setting["backend"], "selection": _voice_selection(setting), "host": speech.status()}
    if setting.get("error"):
        view["error"] = setting["error"]
    if device is not None and setting["backend"] == "service" and not setting.get("error"):
        view["service"] = {"url": setting["url"], "model": setting.get("model", ""),
                           "key_present": bool(setting.get("key"))}
    return view


def save_voice(body: dict) -> dict:
    """Apply the operator's voice selection through the same durable request as the CLI."""
    if body.keys() - {"backend", "selection", "url", "key", "model", "remove_key"}:
        raise ValueError("Unsupported voice settings fields.")
    with config.projects_lock():
        saved = config.machine_settings().get("voice")
        current = config.voice_setting()
    if body.get("selection") != _voice_selection(current):
        raise VoiceInputError("Voice settings changed. Reload settings and try again.", 409)
    backend = body.get("backend")
    if backend == "service":
        if not isinstance(body.get("url"), str) or not isinstance(body.get("remove_key", False), bool):
            raise ValueError("Enter a complete http:// or https:// transcription URL.")
        value = {"backend": "service", "url": body["url"].strip(), "selection": uuid.uuid4().hex}
        for field in ("key", "model"):
            if field in body:
                if not isinstance(body[field], str):
                    raise ValueError(f"Enter a valid speech service {field}.")
                if body[field].strip():
                    value[field] = body[field].strip()
        if body.get("remove_key"):
            value.pop("key", None)
        elif not value.get("key") and current.get("url") == value["url"] and current.get("key"):
            value["key"] = current["key"]
        config.validate_voice(value)
    elif backend in ("browser", "host") and not body.keys() - {"backend", "selection"}:
        value = backend
    else:
        raise ValueError("Choose this computer or browser recognition.")
    # An unchanged save keeps the selection that pending recordings carry.
    if (value == {**current, "selection": value["selection"]} if isinstance(value, dict)
            else current == {"backend": value}):
        return voice_view()
    # Apply checks the nonce under its projects lock too, including after a daemon crash/replay.
    expected = config.voice_expected(saved)
    dispatch.request_setting(None, "voice", value, "Voice input settings", actor=config.OPERATOR_ACTOR,
                             expected=expected)
    result = dispatch._run_setting(None, "voice")
    if result["status"] != "done":
        if result.get("changed"):
            raise VoiceInputError("Voice settings changed. Reload settings and try again.", 409)
        raise ValueError(result["note"])
    return voice_view()


def image_capability(project: str, slug: str | None = None, engine: str | None = None) -> dict:
    """Image input follows this conversation's current engine (a task's, an agent chat's `engine`, else L3's) and the
    optional local converter."""
    config.project(project)
    state = images.capability()
    if slug:
        try:
            task = S.load_task(project, S.require_task_slug(slug))
        except (ValueError, FileNotFoundError):
            raise images.ImageError("Image access denied.", 403)
        engine = task.get("l2_engine") or task.get("engine")
        choice = {"engine": engine} if engine else route.pick_engine("l2", project=config.project(project))
    elif engine in config.ENGINES:
        choice = {"engine": engine}
    else:
        choice = l3._select(project)
    if state["available"]:
        native = engines.image_capability(choice.get("engine"))
        state = {"available": native["available"], "reason": native.get("why")}
    return {**state, "max_count": images.MAX_IMAGES, "max_bytes": images.MAX_BYTES,
            "max_total_bytes": images.MAX_TOTAL_BYTES, "max_pixels": images.MAX_PIXELS,
            "max_dimension": images.MAX_SIDE}


def require_image_capability(project: str, slug: str | None = None, *, engine: str | None = None) -> None:
    capability = image_capability(project, slug, engine)
    if not capability["available"]:
        raise images.ImageError(capability.get("reason") or "Image input unavailable.")


def log(msg: str) -> None:
    line = f"{S.now()} {msg}\n"
    try:
        storage.append(LOG, line)
    except (OSError, ValueError):
        pass
    print(line, end="", flush=True)


#: The daemon's host voice recordings and speech worker.
SPEECH = speech.Host(log)


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


def request_daemon_task_operation(project: str, slug: str, operation: str, reason: str, *, actor: str,
                                  generation: object = T._UNSET, stop_id: object = T._UNSET,
                                  **options) -> dict:
    """Persist an operator request before scheduling its one daemon-side runner."""
    observed = {key: value for key, value in (("generation", generation), ("stop_id", stop_id)) if value is not T._UNSET}
    result = dispatch.request_task_operation(project, slug, operation, reason, actor=actor, deliver_reason=False,
                                             **observed, **options)
    if result.get("queued"):
        try:
            spawn(f"task-operation:{project}:{slug}", dispatch.run_task_operation, project, slug)
        except Exception as exc:
            log(f"[{project}/{slug}] task operation saved; immediate wake failed: {exc}")
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
        if args[:2] == ["project", "message"]:
            if stdin:
                raise ValueError("project messages accept deliberately written literal text, not stdin")
            options = l3.project_message_parser().parse_args(args[2:])
            result = l3.project_message(project, options.target, options.text, summary=options.summary,
                                        request_id=options.request_id, reply_to=options.reply_to)
            return {"returncode": 0, "stdout": json.dumps(result) + "\n", "stderr": ""}
        if args[:2] == ["task", "offer"]:
            if stdin:
                raise ValueError("alt task offer takes its title as one argument, not stdin")
            result = l3.note_offer(project, l3.offer_parser().parse_args(args[2:]).title)
            return {"returncode": 0, "stdout": json.dumps(result) + "\n", "stderr": ""}
        if args[:1] == ["agent"]:
            options = project_agents.parser().parse_args(args[1:])
            source = "l3"
            if options.action not in ("list", "show"):
                source = l3.chat_turn(project)  # an offer is drawn under this reply; a change is the operator's say
                if options.action != "offer" and not any(
                        row["id"] == source for row in T._decision_messages(project, "", "project")):
                    raise ValueError("this applies only to the operator's own chat message")
            result = project_agents.verb(project, options, stdin, by="l3", source=source)
            if options.action == "run":
                spawn(f"project-agents:{project}", project_agents.start_next, project)
            return {"returncode": 0, "stdout": json.dumps(result) + "\n", "stderr": ""}
        if args[:1] == ["improve"]:
            if stdin:
                raise ValueError("alt improve takes its arguments, not stdin")
            result = improvement_verb(project, improve.parser().parse_args(args[1:]))
            return {"returncode": 0, "stdout": json.dumps(result) + "\n", "stderr": ""}
        if args[:2] == ["project", "step"]:
            result = l3.step_verb(project, args[2:], stdin)
            return {"returncode": 0, "stdout": json.dumps(result) + "\n", "stderr": ""}
        if args[:2] == ["project", "terminal"]:
            if stdin or args[2:] not in ([], ["--json"]):
                raise ValueError("alt project terminal: the only option is --json")
            record = coordinator_terminal_output(project)
            text = json.dumps(record) if args[2:] else terminal.describe(record, "project")
            return {"returncode": 0, "stdout": text + "\n", "stderr": ""}
        project_options = {"--p", "--pr", "--pro", "--proj", "--proje", "--projec", "--project"}
        file_options = {"--f", "--fi", "--fil", "--file"}
        if any(arg.split("=", 1)[0] in project_options | file_options
               or arg.startswith("-p") and not arg.startswith("--") for arg in args):
            raise ValueError("the L3 socket fixes the project and accepts input only on stdin")
        for index, arg in enumerate(args):
            # argparse abbreviations must obey the same stdin-only boundary as the full flag.
            flag, _, inline = arg.partition("=")
            if flag.startswith("--q") and "--questions-file".startswith(flag) and flag != "--question":
                value = inline if "=" in arg else (args[index + 1] if index + 1 < len(args) else None)
                if value != "-":
                    raise ValueError("L3 questions-file input is accepted only on stdin (-)")
        _validate_l3_alt_args(args)
        if args[:2] == ["project", "setup"]:
            options = project_setup.parser().parse_args(args[2:])
            if options.name != project or stdin:
                raise ValueError("The setup command is bound to this project's coordinator; no input body is accepted.")
            if options.repair:
                if not options.reason or not options.reason.strip():
                    raise ValueError("Setup repair requires --reason.")
                result = request_project_setup(project, "repair", actor="l3", reason=options.reason)
            else:
                if options.reason is not None:
                    raise ValueError("--reason applies to --repair.")
                result = project_setup.observe(project)
            return {"returncode": 0, "stdout": json.dumps(result) + "\n", "stderr": ""}
        if args[:2] == ["pr", "close"]:
            if len(args) != 3:
                raise ValueError("alt pr close requires one positive PR number and no options")
            result = pr_close(project, int(args[2]), actor="l3", body=stdin)
            return {"returncode": 0, "stdout": json.dumps(result) + "\n", "stderr": ""}
        if args[:2] == ["task", "hold-merge"] and any(arg.split("=", 1)[0] == "--approval" for arg in args[3:]):
            options = merge_approval_parser().parse_args(args[2:])
            receipt = apply_recorded_merge_approval(project, **vars(options))
            return {"returncode": 0, "stdout": json.dumps(receipt) + "\n", "stderr": ""}
        if args[:1] == ["issue"]:
            options = vars(issue_parser().parse_args(args[1:]))
            options.pop("text", None)
            url = issue_write(project, body=stdin, actor="l3", **options)
            if options["operation"] == "new":
                l3.note_created(project, "issues", {"title": options["title"], "url": url})
            return {"returncode": 0, "stdout": url + "\n", "stderr": ""}
        env = engines.clean_env()
        env.update({"ALTITUDE_ACTOR": "l3", "ALTITUDE_PROJECT": project, "ALTITUDE_HOME": str(config.ROOT)})
        env.pop("ALTITUDE_OFFER_TURN", None)  # only the running turn below binds a creation to a reply
        if args[:2] == ["task", "new"] and (offer := l3.active_offer(project)):
            env["ALTITUDE_OFFER_TURN"] = offer  # a turn answering a Create task press creates that reply's one task
        try:
            result = subprocess.run([sys.executable, "-B", str(config.SOURCE / "bin" / "alt"), *args], input=stdin,
                                    cwd=str(config.project_path(project)), env=env,
                                    capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as exc:
            return {"returncode": 1, "stdout": "", "stderr": f"alt verb failed: {exc}\n"}
        if result.returncode == 0:
            _note_created(project, args, result.stdout or "")
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
    command = _l3_gh_command(args)
    env = engines.clean_env()
    env.pop("GH_REPO", None)  # a read naming no repository resolves from this project's checkout
    env.update({"GH_PAGER": "cat", "GH_PROMPT_DISABLED": "1"})
    try:
        result = subprocess.run(["gh", *command], cwd=str(config.project_path(project)), env=env,
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"returncode": 1, "stdout": "", "stderr": f"gh read failed: {exc}\n"}

    return {"returncode": result.returncode, "stdout": _l3_bounded(result.stdout or ""),
            "stderr": _l3_bounded(result.stderr or "")}


def improvement_verb(project: str, options: argparse.Namespace) -> dict:
    """`alt improve` for the project's L3. Starting, stopping and recording a decision need the operator's own
    chat message, the one the running turn answers; checking and posting are L3's."""
    if options.action == "status":
        return improve.status(project, options.run)
    if options.action == "post":
        return suggestions.post(project, options.run, options.keep, options.drop)
    message = l3.chat_turn(project)
    if not any(row["id"] == message for row in T._decision_messages(project, "", "project")):
        raise ValueError("this applies only to the operator's own chat message")
    if options.action == "stop":
        return improve.stop(project, message)
    if options.action == "resolve":
        return suggestions.resolve(project, options.ref, options.outcome, message, options.reason)
    receipt = improve.start(project, message)
    if not receipt.get("replayed"):
        spawn(f"improve:{project}", improve.execute, project, receipt["run"])
    return receipt


def _l3_gh_command(args: list[str]) -> list[str]:
    """Admit a gh command that only reads, without opening a browser or prompting."""
    def refuse(reason: str):
        raise ValueError(f"L3 gh read refused: {reason}. {L3_GH_RULE}")

    if args[0] == "api":
        return _l3_gh_api(args[1:], refuse)
    if args[0] != "search" and (args[0] not in L3_GH_GROUPS or args[1] not in L3_GH_VERBS):
        refuse(f"gh {args[0]} {args[1]} is not a read")
    for arg in args[2:]:
        # Short options may cluster (`-cw`); `-w` is --web except in `run list`, where it is --workflow.
        if (arg.partition("=")[0] == "--web"
                or args[:2] != ["run", "list"] and re.match(r"-[A-Za-z]*w", arg)):
            refuse(f"{arg} opens a browser")
    return list(args)


def _l3_gh_api(args: list[str], refuse) -> list[str]:
    """Rebuild a GET from validated options, so gh never sees a body, a method or another host."""
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            refuse(f"gh api {message}")
    parser = Parser(prog="gh api", allow_abbrev=False, add_help=False)
    parser.add_argument("endpoint")
    parser.add_argument("-X", "--method", type=str.upper, choices=["GET"])
    parser.add_argument("-H", "--header", action="append", default=[])
    parser.add_argument("-p", "--preview", action="append", default=[])
    parser.add_argument("-q", "--jq")
    parser.add_argument("-t", "--template")
    parser.add_argument("--cache")
    parser.add_argument("-i", "--include", action="store_true")
    for flag in ("--paginate", "--slurp", "--silent"):
        parser.add_argument(flag, action="store_true")
    options = parser.parse_args(args)
    # `gh api -- -XPOST` would rebuild into a flag; GraphQL can carry a mutation in its query.
    if "://" in options.endpoint or re.match(r"-|/?graphql\b", options.endpoint):
        refuse("gh api reads one REST endpoint path on github.com")
    return ["api", options.endpoint, "--method=GET",
            *(f"--header={value}" for value in options.header), *(f"--preview={value}" for value in options.preview),
            *(f"--{name}={value}" for name in ("jq", "template", "cache")
              if (value := getattr(options, name)) is not None),
            *(f"--{name}" for name in ("include", "paginate", "slurp", "silent") if getattr(options, name))]


def _note_created(project: str, args: list[str], stdout: str) -> None:
    """Attach the task `alt task new` created, or the issue `alt incident new|publish` filed, to the L3 turn that ran
    it, so the turn's chat row names it (SPEC.md §4.1, §5.2 note 4). Finding an incident's existing issue files none."""
    if args[:2] not in (["task", "new"], ["incident", "new"], ["incident", "publish"]):
        return
    try:
        printed = json.loads(stdout)
    except ValueError:
        return
    if not isinstance(printed, dict):
        return
    if args[0] == "task" and isinstance(printed.get("slug"), str) and printed["slug"]:
        l3.note_created(project, "tasks", printed["slug"])
    elif args[0] == "incident" and printed.get("created") is True and isinstance(printed.get("issue"), str):
        title = next((row.get("title") for row in incidents.index(project) if row.get("id") == printed.get("id")), None)
        l3.note_created(project, "issues", {"title": title or "Incident", "url": printed["issue"]})


def _validate_l3_alt_args(args: list[str]) -> None:
    """Reject path-shaped identifiers before altd invokes the ordinary CLI parser."""
    if len(args) >= 3 and args[:1] == ["task"] and args[1] in L3_TASK_TARGETS:
        S.require_task_slug(args[2])
    if args[:2] == ["task", "block"]:
        raise ValueError("task block is owner-only; L3 uses task stop --reason for a running worker, "
                         "or task message / task resume --reason to return a reported owner's "
                         "current contradicted report for correction")
    if args[:2] == ["task", "hold-merge"] and "--off" in args[3:]:
        raise ValueError("only the operator may release a merge hold")
    if args[:1] == ["fyi"] and len(args) >= 3:
        S.require_task_slug(args[1])
    if args[:2] in (["incident", "amend"], ["incident", "publish"]):
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
    parser.add_argument("--source", choices=("task", "project"), default="task")
    parser.add_argument("--pr-number", dest="pr", required=True, type=int)
    parser.add_argument("--head", required=True)
    parser.add_argument("--reason", required=True)
    return parser


def apply_recorded_merge_approval(project: str, slug: str, approval: str, pr: int, head: str, reason: str,
                                 source: str = "task") -> dict:
    """I-20260907-205556: bind durable operator approval to the checkout-origin PR before releasing a hold."""
    S.require_task_slug(slug)
    if (pr < 1 or source not in ("task", "project")
            or not re.fullmatch(r"[0-9a-f]{12}" if source == "project" else r"[0-9a-f]{32}", approval)
            or not re.fullmatch(r"[0-9a-f]{40}", head) or not reason.strip()):
        raise ValueError("approval requires a source message id, positive PR number, full head SHA, and reason")
    from . import github_intake
    owner, repository = github_intake.project_repo(project)
    url = f"https://github.com/{owner}/{repository}/pull/{pr}"
    env = engines.clean_env()
    env.pop("GH_REPO", None)
    result = subprocess.run(["gh", "pr", "view", str(pr), "--repo", f"{owner}/{repository}", "--json",
                             "number,url,state,isDraft,isCrossRepository,baseRefName,headRefName,headRefOid"],
                            cwd=config.project_path(project), env=env, capture_output=True, text=True, timeout=30)
    pull = json.loads(result.stdout) if result.returncode == 0 else None
    if not isinstance(pull, dict) or pull.get("number") != pr or pull.get("url") != url:
        raise ValueError("approval PR could not be read from the project origin")
    try:
        return T.apply_merge_approval(project, slug, approval, pull, head=head, reason=reason, actor="l3",
                                      source=source)
    except T.TransitionError as exc:
        raise ValueError(str(exc)) from exc


def _l3_bounded(value: str) -> str:
    raw = value.encode(errors="replace")
    if len(raw) <= L3_VERB_MAX_OUTPUT:
        return value
    return raw[:L3_VERB_MAX_OUTPUT].decode(errors="replace") + "\n[output truncated by altd]\n"


class _L3VerbHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        # Every process runs as the operator's account, so the socket's mode admits task workers too: the kernel's
        # account of who connected decides, and a refused request is read only to be discarded.
        unit = l3.turn_unit(self.server.project)
        admitted = bool(unit) and unit in platform.peer_groups(self.request)
        data = bytearray()
        while len(data) <= L3_VERB_MAX_REQUEST and b"\n" not in data:
            chunk = self.request.recv(min(65536, L3_VERB_MAX_REQUEST + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        try:
            if not admitted:
                raise ValueError(L3_VERB_REFUSED)
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
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
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
    try:
        return spawn(key, drain_l3_queue, project)
    except Exception as exc:  # #298: a failed wake cannot refuse a durable queued message; the timer retries.
        log(f"[{project}] L3 queue wake deferred: {exc}")
        return False


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
    with config.provider_admission() as held:
        if held:
            project_setup.save(project, start_requested=True)
            return  # Setup maintenance retries this unclaimed first turn after host continuation.
        _start_l3(project)


def _start_l3(project: str) -> None:
    if ((project_setup.read(project).get("intro") or {}).get("state") not in ("failed", "running")
            and (l3.info(project).get("turns") or any(row.get("role") == "assistant" for row in l3.chat_history(project)))):
        if l3.has_queued_turn(project):
            request_l3_drain(project)
        return
    if project_setup.missing_tool(project):
        # Setup shows the first conversation waiting and starts it once a coding tool is ready; a failure it
        # supersedes is cleared, so the start continues by itself.
        project_setup.save(project, start_requested=True, intro=None)
        return
    # The start reply belongs to the operator's conversation.
    project_setup.save(project, intro={"state": "running", "at": S.now()})
    result = server_l3_turn(project, "You have just been started for this project. Read the repository rules named in this turn, the state file, and the repo's README (skim), "
                            "then answer in a few plain sentences: what this project is, what is in flight, and what you would need from the operator. "
                            "Keep operational details in the task record rather than dumping them into chat. Run no other commands.",
                   trigger="start")
    project_setup.save(project, intro={"state": "complete" if result.get("completed") else "failed",
                                      "at": S.now(), "error": result.get("error")})


def request_project_setup(project: str, action: str, *, actor: str, expected: str | None = None,
                          reason: str = "Project setup") -> dict:
    result = project_setup.request(project, action, actor=actor, expected=expected, reason=reason)
    try:
        spawn(f"setup:{project}", run_project_setup, project)
    except Exception as exc:
        log(f"[{project}] setup request saved; immediate wake failed: {exc}")
    return result


def run_project_setup(project: str) -> None:
    """Run accepted setup operations until none is newly pending. A request accepted while the previous run
    is finishing finds that run alive, so the run takes it up before it exits instead of losing its wake."""
    key = f"setup:{project}"
    while True:
        seen = (project_setup.read(project).get("operation") or {}).get("id")
        project_setup.run(project)
        with _bg_guard:
            operation = project_setup.read(project).get("operation") or {}
            if operation.get("state") == "pending" and operation.get("id") != seen:
                continue
            if _bg.get(key) is threading.current_thread():
                _bg.pop(key, None)
            return


def restart_notice() -> None:
    """Give L3 the active tasks and their explicit waits after a restart."""
    if platform.containerized():
        return  # #543: daemon startup neither activates main nor authorizes an image recovery turn.
    for project in config.load_projects():
        if not config.is_managed(project):
            continue
        active = [t for t in S.list_tasks(project) if t["state"] in ("running", "blocked", "reported")]
        if not active:
            continue
        lines = []
        for t in active:
            tag = T.wait_label(project, t) or t["state"]
            lines.append(f"- {t['slug']}: {t['state']} ({tag}); {T.short_reason(t.get('blocked_reason') or t.get('title') or '')}")
        l3.queue_message(project, "Altitude restarted with the code now on main. Its active tasks:\n" + "\n".join(lines)
                         + "\n\nCheck each with `alt task status <slug>`. A restart does not resolve checkout faults. "
                         "Resume only after observing that the cause is gone (`alt task resume <slug> --reason \"<observed fix>\"`); leave a task waiting on "
                         "the operator to them; a running task keeps "
                         "its worker. Reply in two or three plain sentences.", trigger="restart")
        log(f"[{project}] restart notice queued for L3 ({len(active)} active tasks)")


def on_l2_finished(project: str, item: dict) -> None:
    with config.project_activity(project) as attached, config.restart_lock() as ready:
        if attached and config.is_managed(project) and ready and not config.restart_in_progress():
            _on_l2_finished(project, item)


def _on_l2_finished(project: str, item: dict) -> None:
    t = item["task"]
    slug = t["slug"]
    agent = item.get("agent") or {}
    clean_exit = agent.get("state") == "done" and not agent.get("detail")
    worker_outcome = any(item.get(key) for key in ("died", "capacity", "limited", "rejection", "needs_input"))
    with S.project_lock(project):
        live = S.load_task(project, slug)
        if (live.get("daemon_request") or {}).get("status") in ("pending", "executing"):
            return  # The queued operator action owns this worker's exit.
        snapshot = (t.get("state"), T.report_owner(t))
        current = (live.get("state"), T.report_owner(live))
        if (t.get("state") == live.get("state") == "running"
                and not worker_outcome
                and all(t.get(key) == live.get(key) for key in ("attempt", "agent_id", "session_id", "worker_started_at"))
                and any(row.get("wake", True) for row in T.pending(project, slug))):
            T.continue_report(project, live, actor="altd", reason="Follow-up messages await the owner", check_pr=False)
            return
    if current != snapshot:
        log(f"[{project}/{slug}] ignored stale finished worker snapshot {snapshot} → {current}")
        return
    t = live  # include completion/action fields that may have landed after poll took its worker snapshot
    if t.get("stop_id"):
        return  # #302: the explicit Stop owns this worker, including its concurrent final result.

    def block_snapshot(reason: str, *, actor: str = "altd", updates: dict | None = None,
                       resume_pending: bool = False) -> dict:
        return T.block(project, slug, reason, actor=actor, expected_state=t.get("state"), updates=updates,
                       resume_pending=resume_pending,
                       expected_agent_id=t.get("agent_id"), expected_session_id=t.get("session_id"),
                       expected_block_id=t.get("block_id"),
                       expected_owner=None if worker_outcome else T.report_owner(t))

    if t.get("completion_requested") and (not worker_outcome or (item.get("died") and clean_exit)):
        a = item.get("agent") or {}
        if a.get("state") == "working" or a.get("status") in ("busy", "idle"):
            raise RuntimeError(f"{project}/{slug}: completion reached finished handling while its L2 is still live")
        try:
            T.finalize_completion(project, slug, expected_agent_id=t.get("agent_id"),
                                  expected_session_id=t.get("session_id"), expected_block_id=t.get("block_id"))
        except T.TransitionError:
            current = S.load_task(project, slug)
            if (all(current.get(key) == t.get(key) for key in ("state", "agent_id", "session_id", "block_id"))
                    and not current.get("stop_id")
                    and (current.get("daemon_request") or {}).get("status") not in ("pending", "executing")):
                raise  # An unchanged owner failed completion validation, rather than losing a race.
            log(f"[{project}/{slug}] completion lost a concurrent lifecycle race; ignored")
            return
        log(f"[{project}/{slug}] no-code completion finalized after the L2 worker exited")
        return
    if item.get("rejection"):
        # A refusal stops the task where it is; only the operator's Change model… moves it to another engine.
        route.note_rejection({"engine": dispatch.l2_engine(t), "model": dispatch.launched(t)[0]}, item["rejection"])
        why = item["rejection"]["why"] + ". Check engine authentication/model access, or change this task's model."
        block_snapshot(why, updates={"engine_refusal": item["rejection"]["why"]})
        T.fyi(project, slug, why + " The existing attempt and conversation are retained.", actor="altd")
        return
    if item.get("capacity"):
        # Provider capacity is local to this task/model, unlike an exhausted subscription window or a system fault.
        # Keep its logical L2 identity and retry the same conversation after bounded exponential-ish backoff.
        retry = max(0, int(t.get("capacity_retries") or 0)) + 1
        delay = CAPACITY_RETRY_DELAYS[min(retry - 1, len(CAPACITY_RETRY_DELAYS) - 1)]
        until = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat(timespec="seconds")
        engine = t.get("l2_engine") or "claude"
        model = t.get("engine_model") or t.get("launch_model") or "provider default"
        updates = {"capacity_retries": retry, "resume_after": until}
        try:
            block_snapshot(f"{engine} model {model} is temporarily at capacity; "
                           f"Altitude retries this same L2 after {until}", updates=updates)
        except T.TransitionError:
            log(f"[{project}/{slug}] capacity result lost a concurrent lifecycle race; ignored")
            return
        log(f"[{project}/{slug}] {engine}/{model} temporarily at capacity → retry {retry} after {delay}s")
        return
    if item.get("limited"):
        limit = item["limited"]
        until, engine = limit["until"], dispatch.l2_engine(t)
        why = "usage limit: " + limit["why"]
        try:
            block_snapshot(why, updates={"resume_after": until, "usage_limit": limit})
        except T.TransitionError:
            log(f"[{project}/{slug}] usage-limit result lost a concurrent lifecycle race; ignored")
            return
        engines.record_usage_limit(engine, limit, f"L2 {t.get('agent_id', '')} of {slug}")
        waiting = f"This L2 resumes after {until}." if until else "No automatic resume is scheduled; L3 must arrange recovery."
        T.fyi(project, slug, f"{why}. {waiting}", actor="altd")
        log(f"[{project}/{slug}] {why} → blocked")
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
        detail = (f"L2 worker {a.get('id', '')} (attempt {t.get('attempt')}) ended without a fresh report: "
                  f"worker state={a.get('state', 'absent')}; {item.get('detail') or a.get('detail') or ''}")
        try:
            blocked = block_snapshot(f"system fault [l2-died]: {detail}",
                                     resume_pending=clean_exit)
        except T.TransitionError:
            log(f"[{project}/{slug}] dead-worker result lost a concurrent lifecycle race; ignored")
            return
        if blocked.get("resume_after"):  # a queued message, a released turn or the one nudge continues the session
            log(f"[{project}/{slug}] L2 turn ended without a report → resuming: {blocked.get('blocked_reason')}")
            request_task_resume(project, slug)
        else:
            incidents.system_fault("l2-died", detail, project=project, task=slug,
                                   expected_block_id=blocked.get("block_id"), expected_task=blocked,
                                   step=f"the L2 worker run (attempt {t.get('attempt')})")
            log(f"[{project}/{slug}] L2 died → blocked; fault raised")
        return
    step = f"[{project}/{slug}] report verification of the worker started {t.get('worker_started_at')}"
    try:
        v = verify.verify(project, slug)
    except verify.Unreachable as e:
        if _retry_next_tick(step, e):
            return  # GitHub did not answer; the next tick hands the finished worker back for verification.
        v = verify.fault(project, slug, e, T.report_owner(t))
    _failing_since.pop(step, None)
    log(f"[{project}/{slug}] L2 finished; verdict {v['verdict']}; problems {v['problems']}")
    if v["verdict"] == "rate-limited":
        return  # The task stays as it is; the next tick hands the finished worker back for verification.
    with S.project_lock(project):
        live = S.load_task(project, slug)
        if (live.get("daemon_request") or {}).get("status") in ("pending", "executing"):
            return
        if (live.get("state") == t.get("state") and all(live.get(key) == t.get(key)
                for key in ("attempt", "agent_id", "session_id", "worker_started_at")) and any(
                row.get("wake", True) for row in T.pending(project, slug))):
            T.continue_report(project, live, actor="altd", reason="Follow-up messages await the owner", check_pr=False)
            return
        if T.report_owner(live) != T.report_owner(t) or live.get("state") != t.get("state"):
            return
        live.setdefault("spend", {}).update({k: val for k, val in v.get("spend", {}).items() if val is not None})
        S.save_task(project, live)
    try:
        if v["verdict"] == "fault":
            block_snapshot(f"verifier fault (Altitude, not the L2): {v.get('fault')}")
            return
        if v["verdict"] == "missing":
            t = block_snapshot("L2 session ended without a report (report.json missing)")
        else:
            t = T.report(project, slug, {**v, "owner": T.report_owner(t)}, expected_state=t.get("state"))
            if v["verdict"] == "blocked":
                t = block_snapshot((v.get("report") or {}).get("blocked") or "blocked (see report)")
    except T.TransitionError:
        return  # A follow-up won report admission; the ordinary resume timer owns it.
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
    `resume_stranded_reports` instead of leaving the task waiting for nobody. While L3 has no available engine the
    report waits without a turn or chat row; a turn that runs and fails is retried after a growing delay.
    """
    slug = t["slug"]
    owner = T.report_owner(t)
    try:
        current_owner = T.report_owner(S.load_task(project, slug))
    except (KeyError, OSError, ValueError):
        current_owner = owner  # The existing corrupt-report/status path below records the fault.
    if owner != current_owner:
        return
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
                    if T.report_owner(live) != owner:
                        return
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
            incidents.system_fault("report-json", f"{project}/{slug}: {report_error}", project=project, task=slug,
                                   expected_owner=owner)
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
                    T.done(project, slug, actor="altd", digest=clean_digest,
                           expected_state="reported", expected_owner=owner)
                except T.TransitionError:
                    log(f"[{project}/{slug}] clean close lost the state race")
                    if S.load_task(project, slug).get("report_after") != owner.get("report_after"):
                        return
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
              "and whether anything waits on the operator.")
    key, identity = (project, slug), json.dumps(owner, sort_keys=True)

    def unfinished(detail: str) -> None:  # not stamped: the stranded-report scan retries after the delay
        previous = _report_retries.get(key)
        failures = previous[1] + 1 if previous and previous[0] == identity else 1
        delay = REPORT_RETRY_DELAYS[min(failures, len(REPORT_RETRY_DELAYS)) - 1]
        _report_retries[key] = (identity, failures, time.monotonic() + delay)
        log(f"[{project}/{slug}] report turn unfinished: {detail}; retry in {delay}s")

    try:
        res = server_l3_turn(project, header, trigger="report-landed") or {}
    except Exception as exc:
        unfinished(str(exc))
        raise
    if res.get("held"):
        log(f"[{project}/{slug}] report waits for L3: {res['error']}")  # the next scan retries it
        return
    if not res.get("completed") or res.get("error"):
        unfinished(res.get("error") or "L3 turn did not complete")
        return
    _report_retries.pop(key, None)
    try:
        with S.project_lock(project):
            t2 = S.load_task(project, slug)
            if T.report_owner(t2) == owner:
                t2["l3_handled"] = S.now()
                S.save_task(project, t2)
    except (KeyError, OSError, ValueError) as e:
        log(f"[{project}/{slug}] L3 turn completed but l3_handled could not be stamped: {e}")


def resume_stranded_reports(project: str) -> None:
    """Reports that landed (state reported/blocked with report.json) but whose L3 turn never finished get it again."""
    lifecycle = platform.container_lifecycle()
    if lifecycle is not None and not lifecycle['ready']:
        return  # Keep reports due, without spawning/logging a refused turn every tick (#543).
    for t in S.list_tasks(project):
        if t["state"] not in ("reported", "blocked") or t.get("l3_handled"):
            continue
        report_path = S.task_dir(project, t["slug"]) / "report.json"
        if not report_path.exists():
            continue
        if t.get("report_after") and not T.report_current(t, report_path):
            continue
        last_block = next((ev for ev in reversed(S.read_events(project, t["slug"]))
                           if ev.get("kind") == "state" and ev.get("to") == "blocked"), None)
        if t["state"] == "blocked" and last_block and last_block.get("by") == "l2":
            continue  # The owner's own block, even beside a report file, is a question, not a landed report.
        retry = _report_retries.get((project, t["slug"]))
        if (retry and retry[0] == json.dumps(T.report_owner(t), sort_keys=True)
                and time.monotonic() < retry[2]):
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
    # Keep a replacement's queued work untouched without logging a refused
    # launch per task on every timer tick. Dispatch still rechecks under its lease.
    lifecycle = platform.container_lifecycle()
    if config.restart_in_progress() or lifecycle is not None and not lifecycle['ready']:
        return
    queued = [T.release_dependency(project, t["slug"]) if t.get("planned_wait") else t
              for t in S.list_tasks(project) if t["state"] == "queued"]
    for t in queued:
        if t["state"] != "queued":
            continue
        if t.get("planned_wait"):
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


def tick() -> None:
    try:
        engines.refresh_quotas()
    except Exception as e:  # noqa: BLE001
        log(f"[quota] refresh failed: {e}")
    dispatch.run_settings()
    terminal.sweep()
    access.settle()
    for project in list(_l3_verb_brokers):
        if not config.is_managed(project):
            remove_l3_verb_broker(project)
    for project in list(config.load_projects()):
        with config.project_activity(project) as ready:
            if ready and config.is_managed(project):
                tick_project(project)
    try:
        for note in dispatch.prune_source_exports():
            log(f"[source] {note}")
    except Exception as e:  # noqa: BLE001
        log(f"[source] export pruning failed: {e}")
    try:
        auto_restart()
    except Exception as e:  # noqa: BLE001
        log(f"auto-restart: {e}\n{traceback.format_exc()}")
    try:
        auto_update()
    except Exception as e:  # noqa: BLE001 — the attempt is already marked failed; the cause stays in this log
        log(f"auto-update: {e}\n{traceback.format_exc()}")
    # Off the timer thread: five unreachable devices must not delay dispatch, resumes or the digest.
    spawn("push", push.notify, log)
    if config.RELEASE is not None:
        spawn("update-check", installation.check_for_update)
    spawn("repair-check", repair.check_ended)
    morning_digest()


TICK_GRACE_SECONDS = 300
_failing_since: dict[str, float] = {}  # a tick step that keeps failing → monotonic time its failures began
_refused: dict[str, str] = {}  # a tick step that keeps refusing → the fault detail already filed for it


def _retry_next_tick(step: str, exc: BaseException) -> bool:
    """Whether `step` failing with `exc` is only logged: a fetch (#602), a command that timed out on a busy host
    (#724), or a GitHub read the report verifier got no answer to (I-20261018-220735), often succeeds on a later
    tick, so it is retried until the same step has kept failing for
    TICK_GRACE_SECONDS. Past that the caller files a system fault. A step that succeeds pops its `_failing_since`."""
    since = _failing_since.setdefault(step, time.monotonic())
    if time.monotonic() - since >= TICK_GRACE_SECONDS:
        return False
    log(f"{step} failed; retrying next tick: {exc}")
    return True


def self_deploy(project: str) -> None:
    # Activation: a sole running worker's merge must activate without another dispatch or report.
    step = f"[{project}] self-deploy"
    try:
        with dispatch.publication_settlement(project):
            dispatch.self_deploy_fast_forward(project)
    except (git_policy.FetchError, subprocess.TimeoutExpired) as e:
        if not _retry_next_tick(step, e):
            incidents.system_fault("self-deploy", f"{project}: {e}", project=project)
        return
    except (git_policy.GitPolicyError, subprocess.SubprocessError, OSError) as e:
        # #961: a refusal the tick cannot clear, such as a dirty checkout, is filed once and then waits for a change.
        if _refused.get(step) != str(e):
            incidents.system_fault("self-deploy", f"{project}: {e}", project=project)
            _refused[step] = str(e)
        _failing_since.pop(step, None)
        return
    _failing_since.pop(step, None)
    _refused.pop(step, None)


def tick_project(project: str) -> None:
    try:
        project_setup.maintain(project)
        _failing_since.pop(f"[{project}] setup check", None)
    except (OSError, ValueError, RuntimeError) as exc:
        log(f"[{project}] setup check unavailable: {exc}")
    except subprocess.TimeoutExpired as exc:
        if not _retry_next_tick(f"[{project}] setup check", exc):
            incidents.system_fault("tick", f"{project}: {exc}", project=project)
    self_deploy(project)
    try:
        images.collect(project)
    except (images.ImageError, OSError) as e:
        log(f"image cleanup deferred for {project}: {e}")
    try:
        dispatch.run_settings(project)
        for task in S.list_tasks(project):
            if (task.get("ci_recheck") or {}).get("status") in ("pending", "probing", "notifying"):
                spawn(f"ci-recheck:{project}:{task['slug']}", dispatch.run_ci_recheck, project, task["slug"])
        if l3.queue_path(project).exists():
            request_l3_drain(project)
        for slug in dispatch.pending_task_operations(project):
            spawn(f"task-operation:{project}:{slug}", dispatch.run_task_operation, project, slug)
        for item in dispatch.poll(project):
            spawn(f"finished:{project}:{item['task']['slug']}", on_l2_finished, project, item)
        resume_stranded_reports(project)
        improve.reconcile(project)
        if receipt := improve.weekly(project):
            spawn(f"improve:{project}", improve.execute, project, receipt["run"])
        for slug in dispatch.resume_due(project):
            request_task_resume(project, slug)
        dispatch_waiting(project)
        if project_agents.tick(project):
            spawn(f"project-agents:{project}", project_agents.start_next, project)
        for chat_id in chats.due(project):
            spawn(f"chat:{project}:{chat_id}", chats.run, project, chat_id)
        for t in S.list_tasks(project, include_archive=True):
            if t["state"] not in S.OPEN_STATES and not t.get("cleaned"):
                notes = dispatch.cleanup_task(project, t)
                deferred = any(note.startswith("deferred ") for note in notes)
                if not deferred:
                    with S.project_lock(project):
                        t2 = S.load_task(project, t["slug"])
                        if t2["state"] not in S.OPEN_STATES:  # a reopened task is cleaned after it finishes again
                            t2["cleaned"] = S.now(); S.save_task(project, t2)
                S.append_event(project, t["slug"], "cleanup", notes=notes)
                log(f"[{project}/{t['slug']}] cleanup{' deferred' if deferred else ''}: {notes}")
        _failing_since.pop(f"[{project}] tick", None)
    except Exception as e:  # noqa: BLE001
        if isinstance(e, subprocess.TimeoutExpired) and _retry_next_tick(f"[{project}] tick", e):
            return
        log(f"[{project}] tick failed: {e}\n{traceback.format_exc()}")
        incidents.system_fault("tick", f"{project}: {e}", project=project)


_last_digest_day = [None]


def morning_digest() -> None:
    now = datetime.now()
    if now.hour >= 8 and _last_digest_day[0] != now.date():
        _last_digest_day[0] = now.date()
        digest.text()


# The TLS alerts (as OpenSSL names them) a client sends when it does not accept the certificate it was served.
CERTIFICATE_ALERTS = ("ALERT_UNKNOWN_CA", "ALERT_BAD_CERTIFICATE", "ALERT_CERTIFICATE_UNKNOWN",
                      "ALERT_CERTIFICATE_EXPIRED", "ALERT_UNSUPPORTED_CERTIFICATE")


def timer_loop(tls_context: ssl.SSLContext | None = None, tls_host: str | None = None) -> None:
    next_tls_check = time.monotonic() + 86400
    while True:
        if tls_context is not None and time.monotonic() >= next_tls_check:
            try:
                tls.check(tls_host, context=tls_context)
            except (tls.TLSFailure, OSError) as exc:
                log(f"HTTPS renewal failed; the active certificate is retained: {exc}")
            try:
                if TRUST is not None:  # the trust check's certificate ages with the served one
                    TRUST.context = tls.probe_context(tls_host) or TRUST.context
            except (tls.TLSFailure, OSError) as exc:
                log(f"The pairing trust check's certificate was not renewed; the current one is retained: {exc}")
            next_tls_check = time.monotonic() + 86400
        try:
            tick()
            _failing_since.pop("tick", None)
        except Exception as e:  # noqa: BLE001
            if not (isinstance(e, subprocess.TimeoutExpired) and _retry_next_tick("tick", e)):
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


TLS_HANDSHAKE_SECONDS = 10
REQUEST_READ_SECONDS = 30  # a client silent this long before its request line and headers loses the connection
INTERNAL_ERROR = "Altitude hit an internal error; its log has the details."  # what an unpaired client sees
UNPAIRED = "Pair this device to use Altitude."


class Handler(BaseHTTPRequestHandler):
    server_version = "altd/0.1"
    # Small writes (a terminal's echo, a stream's event) leave at once instead of waiting for the last ACK.
    disable_nagle_algorithm = True
    #: Terminal replies keep their connection for the next keystroke; every other reply closes it.
    _keep_open = False
    #: Whether this connection's client is one of Altitude's own agents, once a terminal request has asked.
    _agent: bool | None = None

    _seen_clients: set = set()
    # One handler serves every request on a keep-alive connection, so `_admit` resets these per request.
    _device: dict | None = None
    _machine = False
    _set_cookie: str | None = None

    def handle(self) -> None:
        # I-20260924-205802: a handshake on the accept thread let one stalled client time out every request,
        # including activation's quiet check. Each connection completes its own handshake, bounded, here.
        self._probed = False
        if isinstance(self.connection, ssl.SSLSocket):
            armed = TRUST and TRUST.probe(self.client_address[0])
            if armed:
                self.connection.context = TRUST.context
            refused = False
            try:
                self.connection.settimeout(TLS_HANDSHAKE_SECONDS)
                self.connection.do_handshake()
                self.connection.settimeout(None)
            except OSError as exc:
                # A browser that does not trust the CA ends the trust check's handshake with a certificate alert;
                # an abandoned spare connection just closes and a cancelled one sends another alert, which say
                # nothing about trust.
                refused = any(alert in (getattr(exc, "reason", None) or "") for alert in CERTIFICATE_ALERTS)
                return  # a failed or abandoned handshake drops only this connection, as accept did
            finally:
                if armed:
                    TRUST.settled(armed, refused)
            # Only a full TLS 1.3 handshake on the second certificate shows the browser checked it.
            self._probed = bool(armed) and self.connection.version() == "TLSv1.3" and not self.connection.session_reused
        super().handle()

    def handle_one_request(self) -> None:
        # One thread serves each connection, so a client that stays silent (idle keep-alive, a request that
        # never finishes) would hold its thread forever. Only reading the request line and headers is bounded;
        # the handler clears the timeout and a stream keeps its own loop.
        self.connection.settimeout(REQUEST_READ_SECONDS)
        super().handle_one_request()

    def parse_request(self) -> bool:
        parsed = super().parse_request()
        self.connection.settimeout(None)
        return parsed

    def log_message(self, fmt, *args):  # quieter: one line per new client address, nothing per request
        ip = self.client_address[0]
        if ip not in self._seen_clients:
            self._seen_clients.add(ip)
            log(f"first request from {ip}: {getattr(self, 'command', None)} {getattr(self, 'path', '')}")

    def end_headers(self) -> None:
        if self._set_cookie:
            self.send_header("Set-Cookie", self._set_cookie)
            self._set_cookie = None
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

    def _json(self, obj, code: int = 200, compress: bool = False) -> None:
        """A JSON reply. `compress` is for the large read-only views of a task, project or session, which
        carry no credentials; replies that can carry a pairing code or key never do (docs/ARCHITECTURE.md#web-delivery)."""
        body = json.dumps(obj, default=str).encode()
        encoded = compress and _accepts_gzip(self.headers.get("Accept-Encoding"))
        if encoded:
            body = gzip.compress(body, compresslevel=6, mtime=0)
        if self._keep_open:  # an HTTP/1.1 reply with its length keeps the connection in every browser
            self.protocol_version = "HTTP/1.1"
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if encoded:
                self.send_header("Content-Encoding", "gzip")
            if compress:
                self.send_header("Vary", "Accept-Encoding")
            self.end_headers()
            self.wfile.write(body)
            if self._keep_open:
                self.close_connection = False
        except (ssl.SSLError, BrokenPipeError, ConnectionResetError) as exc:
            # Error replies run inside exception handlers, outside the route's disconnect catcher.
            self.close_connection = True
            log(f"{self.command} {self.path}: client went away ({type(exc).__name__}: {exc})")
        finally:
            if self._keep_open:  # the connection's next request is read and answered as HTTP/1.0 again
                del self.protocol_version
                self._keep_open = False

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
        the same plain 404. Each file is served in a sandbox, so a board's scripts cannot act as Altitude, and
        its path carries the read pass (`access.design_pass`) that lets the sandboxed boards load their files."""
        requested = unquote(parts[1]) if len(parts) > 1 else ""
        # The registered name, never the request's own text, names the project from here on: it reaches a redirect header.
        project = next((name for name in config.load_projects() if name == requested), "")
        if design_viewer_url(project) is None:
            return self._plain("not found", 404)
        if len(parts) == 2:  # the stable per-project link; the boards' relative imports need the depth
            if self._device is None:
                return self._plain("Open the boards from a paired browser.", 403)
            return self._redirect(design_entry(project, self._device["id"]))
        if not access.design_pass_valid(project, parts[2]):
            return self._plain("not found", 404)
        root = config.project_path(project).resolve()
        try:
            resolved = (root / unquote("/".join(parts[3:]))).resolve()
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
        self.send_header("X-Content-Type-Options", "nosniff")
        # Boards are project content: their scripts run in an opaque origin, without Altitude's authority.
        self.send_header("Content-Security-Policy", "sandbox allow-scripts")
        self.send_header("Referrer-Policy", "no-referrer")  # the path's read pass stays on this machine's pages
        self.end_headers()
        self.wfile.write(data)

    def _shown(self, parts: list[str]) -> None:
        """A reply's or question revision's shown files, or the pictures of a task's current PR: the record lists them
        by digest, and only those saved bytes cross this route. `project/slug/reply`, `project/slug/question/revision`
        or `project/slug/pr/number`, then `files/<name>` for one file."""
        asset = unquote(parts[-1]) if len(parts) > 2 and parts[-2] == "files" else None
        try:
            target = [unquote(part) for part in (parts[:-2] if asset is not None else parts)]
            if len(target) not in (3, 4):
                raise ValueError("shown files unavailable")
            project, slug, identity, *revision = target
            revision = int(revision[0]) if revision else None
            task, shown = T.shown_target(project, slug, identity, revision)
            if asset is None:
                if identity == "pr":
                    raise ValueError("a PR's pictures are read one file at a time")
                document = shown.get("document")
                text = T.shown_file(project, slug, shown, document["name"]).decode("utf-8") if document else None
                view = T.shown_view(project, slug, shown, identity, revision, T.shown_versions(project, slug, task))
                if not view["available"]:
                    raise ValueError("shown files unavailable")
                return self._json({**view, "document": {**view["document"], "text": text,
                                                        "markdown": document["name"].endswith(".md")} if document else None})
            if asset.rsplit(".", 1)[-1] not in SHOWN_TYPES:
                raise ValueError("documents are read through their shown set")
            data = T.shown_file(project, slug, shown, asset)
        except (T.TransitionError, OSError, ValueError, KeyError, TypeError, UnicodeError):
            if asset is not None:
                return self._plain("Preview unavailable", 404)
            return self._json({"error": "Preview unavailable"}, 404)
        self.send_response(200)
        self.send_header("Content-Type", SHOWN_TYPES[asset.rsplit(".", 1)[1]])
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; sandbox")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)

    def _captures(self, parts: list[str]) -> None:
        """A reply's validation captures: the reply lists them by digest, and only those saved bytes cross this route."""
        asset = unquote(parts[3]) if len(parts) == 4 else None
        try:
            if len(parts) not in (3, 4):
                raise ValueError("capture unavailable")
            project, slug, identity = [unquote(part) for part in parts[:3]]
            row = T.task_captures(project, slug, identity)
            if asset is None:
                prefix = f"/api/captures/{quote(project, safe='')}/{slug}/{identity}"
                return self._json({"run": row.get("capture_run"), "at": row.get("at"),
                    "conversation_url": f"/projects/{quote(project, safe='')}/tasks/{slug}",
                    "captures": [{**{key: item[key] for key in ("title", "bytes", "width", "height", "frames", "seconds")},
                                  "url": f"{prefix}/{item['name']}"} for item in row["captures"]]})
            data = T.capture_image(project, slug, row, asset)
        except (T.TransitionError, OSError, ValueError, KeyError, TypeError):
            if asset is not None:
                return self._plain("Capture unavailable", 404)
            return self._json({"error": "Capture unavailable"}, 404)
        self.send_response(200)
        self.send_header("Content-Type", "image/gif")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; sandbox")
        self.send_header("Referrer-Policy", "no-referrer")
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
        try:
            places = platform.private_places()
            configured = config.WEB_DIST
            if not configured.is_absolute():
                configured = Path.cwd() / configured
            configured = Path("/", *configured.parts[1:])
            with ExitStack() as opened:
                filesystem = os.open("/", platform.directory_search_access() | os.O_DIRECTORY | os.O_NOFOLLOW)
                opened.callback(os.close, filesystem)
                located = opened.enter_context(
                    platform.namespace_path(filesystem, Path("/"), Path(*configured.parts[1:]), places, (Path("/"),)))
                normalized, root, info = located.relative, located.fd, located.info
                if root is None or info is None or not stat.S_ISDIR(info.st_mode):
                    return self._json({"error": "web UI not built: web/dist is missing — run `make web` first"}, 503)
                dist = Path("/") / normalized
                anchors = (dist, configured)
                located = opened.enter_context(
                    platform.namespace_path(root, dist, Path("index.html"), places, anchors))
                parent, index_relative, info = located.parent, located.relative, located.info
                index = dist / index_relative
                try:
                    page = opened.enter_context(platform.namespace_file(parent, index_relative, info))
                except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
                    return self._json({"error": "web UI not built: web/dist is missing — run `make web` first"}, 503)
                rel = unquote(raw_path).lstrip("/")
                html = not rel
                if html:
                    stream, resolved, relative = page, index, index_relative
                else:
                    located = opened.enter_context(
                        platform.namespace_path(root, dist, Path(rel), places, anchors))
                    parent, relative, info = located.parent, located.relative, located.info
                    resolved = dist / relative
                    try:
                        stream = opened.enter_context(platform.namespace_file(parent, relative, info))
                    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
                        if relative.parts[:1] == ("assets",):
                            # Missing resolved build assets are stale imports, never client-side routes.
                            return self._json({"error": "not found"}, 404)
                        stream, resolved, relative, html = page, index, index_relative, True
                immutable = not html and resolved != dist / "index.html" and relative.parts[:1] == ("assets",)
                compressible = immutable and resolved.suffix in GZIP_ASSETS
                encoded = compressible and _accepts_gzip(self.headers.get("Accept-Encoding"))
                data = _gzipped(resolved, stream) if encoded else stream.read()
                ctype = "text/html; charset=utf-8" if html or resolved.suffix == ".html" else (
                    mimetypes.guess_type(str(resolved))[0] or "application/octet-stream")
        except (OSError, ValueError, RuntimeError):
            return self._json({"error": "not found"}, 404)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if encoded:
            self.send_header("Content-Encoding", "gzip")
        if compressible:
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("Cache-Control", "public, max-age=31536000, immutable" if immutable else "no-store")
        # Another site cannot frame Altitude to steer the operator's clicks.
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(data)

    def _body(self, *, max_bytes: int | None = None) -> dict:
        """The request's JSON body. A request to a task's own routes may carry the files it shows when a process in a
        task's worker job holds its connection; every other client keeps the ordinary bound."""
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise images.ImageError("Invalid message size.", 400)
        limit = (door.MAX_REQUEST if BODY_LIMIT < n <= door.MAX_REQUEST
                 and [p for p in urlparse(self.path).path.split("/") if p] in TASK_DOOR
                 and worker_connection(self.client_address, self.connection.getsockname()) else BODY_LIMIT)
        if n < 0 or n > (max_bytes or limit):
            self.close_connection = True
            raise images.ImageError("Message is too large. Use up to 4 images, 10 MB each and 20 MB total."
                                    if max_bytes is not None else "Request is too large.", 413)
        raw = self.rfile.read(n)
        if max_bytes is not None and len(raw) != n:
            raise images.ImageError("The message upload was incomplete.", 400)
        try:
            value = json.loads(raw or b"{}")
        except ValueError:
            if max_bytes is not None:
                raise images.ImageError("The message could not be read.", 400)
            return {}
        if max_bytes is not None and not isinstance(value, dict):
            raise images.ImageError("Expected a message object.", 400)
        return value

    def _image_origin(self) -> None:
        origin = self.headers.get("Origin")
        if (self.headers.get("Sec-Fetch-Site") == "cross-site"
                or origin and urlparse(origin).netloc != self.headers.get("Host")):
            raise images.ImageError("Image access denied.", 403)

    def _image_request(self, body: dict) -> dict:
        if self.headers.get_content_type() != "application/json":
            raise images.ImageError("Image messages require JSON input.", 415)
        try:
            request_id = uuid.UUID(body.get("request_id", "")).hex
        except (ValueError, TypeError, AttributeError):
            raise images.ImageError("Image messages require a valid submission identity.", 400)
        if body.get("images") and body.get("image_ids"):
            raise images.ImageError("Select images or retry saved images, not both.", 400)
        data = {key: value for key, value in body.items() if key != "request_id"}
        return {"request_id": request_id, "request_digest": hashlib.sha256(
            json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "uploads": body.get("images"), "image_ids": body.get("image_ids")}

    def _images(self, parts: list[str], query: dict) -> None:
        self._image_origin()
        project = unquote(parts[2]) if len(parts) > 2 else ""
        if not config.is_managed(project):
            raise images.ImageError("Image access denied.", 403)
        if len(parts) == 3:
            with S.project_lock(project):
                if not config.is_managed(project):
                    raise images.ImageError("Image access denied.", 403)
                capability = image_capability(project, query.get("task", [None])[0], query.get("engine", [None])[0])
            return self._json(capability)
        if len(parts) != 4:
            raise images.ImageError("Image unavailable.", 404)
        with S.project_lock(project):
            data, meta = images.read(project, unquote(parts[3]))
        self.send_response(200)
        self.send_header("Content-Type", meta["mime_type"])
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.end_headers()
        self.wfile.write(data)

    def _host_voice(self, parts: list[str], query: dict) -> None:
        """Host voice: `live` starts a recording, `live/<id>/audio?seq=N&final=0|1` adds samples and answers the
        text so far, `live/<id>/cancel` discards it; `host` sets up, cancels setup or removes the runtime. The
        live routes carry raw samples, so they are read here rather than as JSON."""
        denied = self._terminal_denied(json_body=False, subject="Voice")
        if denied:
            self.close_connection = True
            return self._json({"error": denied}, 403)
        device = self._device["id"] if self._device else None
        try:
            if parts == ["api", "voice", "host"]:
                action = self._body().get("action")
                if action == "setup":
                    SPEECH.start_setup()
                elif action == "cancel":
                    SPEECH.cancel_setup()
                elif action == "remove":
                    SPEECH.remove()
                else:
                    return self._json({"error": "Choose setup, cancel or remove."}, 400)
                return self._json(voice_view(device))
            self._keep_open = True  # one request every half second: no fresh handshake for each
            if len(parts) == 5 and parts[2] == "live" and parts[4] == "cancel":
                self._body()
                SPEECH.close(parts[3], device)
                return self._json({"ok": True})
            if parts != ["api", "voice", "live"] and (len(parts) != 5 or parts[2] != "live" or parts[4] != "audio"):
                return self._json({"error": "unknown api"}, 404)
            # A reconnecting page replays its recording only into the backend and runtime it started with.
            setting = config.voice_setting()
            if setting["backend"] != "host" or self.headers.get("X-Voice-Selection") != _voice_selection(setting):
                self.close_connection = True  # an audio body stays unread
                raise speech.SpeechError("Voice settings changed. Record again with the new setting.", 409)
            if parts == ["api", "voice", "live"]:
                self._body()
                owner = access.voice_owner(device)
                replaying = self.headers.get("X-Voice-Owner")
                if replaying is not None and replaying != owner:
                    raise speech.SpeechError("Voice stopped: this recording belongs to another device.", 403)
                return self._json({**SPEECH.open(device), "owner": owner})
            try:
                length = int(self.headers.get("Content-Length") or 0)
                seq = int((query.get("seq") or [""])[0])
            except ValueError:
                raise speech.SpeechError("Voice received a malformed recording.", 400) from None
            final = (query.get("final") or ["0"])[0] == "1"
            if length < 0 or length > speech.CHUNK_LIMIT or seq < 0:
                self.close_connection = True
                raise speech.SpeechError("Voice received a malformed recording.", 400)
            pcm = self.rfile.read(length)
            if len(pcm) != length:
                raise speech.SpeechError("Voice stopped: the recording upload was incomplete.", 400)
            return self._json(SPEECH.audio(parts[3], device, seq, pcm, final))
        except speech.SpeechError as exc:
            return self._json({"error": str(exc)}, exc.status)

    def _service_voice(self) -> None:
        """Forward one finished capture to the saved service it was recorded for."""
        denied = self._page_denied(json_body=False, subject="Voice")
        if denied or not self._device:
            self.close_connection = True
            return self._json({"error": denied or "Pair this device to use your speech service."}, 403)
        acquired = False

        def current() -> dict:
            setting = config.voice_setting()
            if setting["backend"] != "service" or setting["selection"] != self.headers.get("X-Voice-Selection"):
                raise speech.ServiceError("selection_changed")
            if setting.get("error"):
                raise speech.ServiceError("service_response")
            return setting
        try:
            current()
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                raise speech.ServiceError("service_response") from None
            if not 0 < length <= speech.SERVICE_PCM_LIMIT or length % 2 or self.headers.get("Transfer-Encoding"):
                raise speech.ServiceError("service_response")
            acquired = speech.SERVICE_SLOTS.acquire(blocking=False)
            if not acquired:
                raise speech.ServiceError("service_unavailable")
            # read1 plus an absolute deadline prevents a slow trickle from holding a service slot.
            until = time.monotonic() + speech.SERVICE_UPLOAD_SECONDS
            pcm = bytearray()
            while len(pcm) < length:
                remaining = until - time.monotonic()
                if remaining <= 0:
                    raise speech.ServiceError("service_unavailable")
                self.connection.settimeout(remaining)
                chunk = self.rfile.read1(min(65536, length - len(pcm)))
                if not chunk:
                    raise speech.ServiceError("service_unavailable")
                pcm.extend(chunk)
            # Settings saved during the upload refuse it before anything is forwarded.
            answer, code = {"text": speech.transcribe_service(current(), bytes(pcm))}, 200
        except (TimeoutError, OSError):
            error = speech.ServiceError("service_unavailable")
            answer, code = {"error": str(error), "code": error.code}, 502
            self.close_connection = True
        except speech.ServiceError as exc:
            answer, code = {"error": str(exc), "code": exc.code}, 409 if exc.code == "selection_changed" else 502
            self.close_connection = True
        finally:
            if acquired:
                speech.SERVICE_SLOTS.release()
            try:
                self.connection.settimeout(None)
            except OSError:
                pass
        return self._json(answer, code)

    def _cross_site(self) -> bool:
        """Whether another site or page, not Altitude's own page, sent this request. A client that is no page
        at all (the `alt` CLI) sends neither header."""
        origin = self.headers.get("Origin")
        scheme = "https" if isinstance(self.connection, ssl.SSLSocket) else "http"
        return (self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none")
                or bool(origin) and origin != f"{scheme}://{self.headers.get('Host')}")

    def _refused(self) -> str | None:
        """Why a request is refused before routing. Over plain HTTP a DNS-rebinding page names its own host in
        both Origin and Host (HTTPS refuses it at the certificate), so without TLS Altitude answers only an
        address or localhost. Every action must come from Altitude's own page: a page on another site can make
        the operator's browser send a request it cannot read the answer to."""
        host = urlparse(f"//{self.headers.get('Host') or ''}").hostname or ""
        if not isinstance(self.connection, ssl.SSLSocket) and host != "localhost":
            try:
                ipaddress.ip_address(host)
            except ValueError:
                return "Over plain HTTP, open Altitude at its address or localhost."
        if self.command == "POST" and self._cross_site():
            return "Requests must come from Altitude's own page."
        return None

    def _admit(self, parts: list[str]) -> bool:
        """Whether this request may proceed: the page, its files, health and pairing are open to anyone;
        everything else needs this machine's key (the `alt` CLI) or a paired device's cookie. A device's
        cookie is renewed, at most daily, while it is used."""
        self._machine = access.is_machine(self.headers.get(access.KEY_HEADER))
        try:
            jar = SimpleCookie(self.headers.get("Cookie") or "")
        except CookieError:
            jar = SimpleCookie()
        key = jar[access.COOKIE].value if access.COOKIE in jar else None
        self._device = access.device(key)
        self._set_cookie = None
        if self._device and access.renew(self._device):
            self._set_cookie = self._device_cookie(key)
        if not parts or parts[0] not in ("api", DESIGN_ROUTE):
            return True
        if len(parts) == 2 and (self.command, parts[1]) in (("GET", "health"), ("HEAD", "health"), ("GET", "access"),
                                                             ("HEAD", "access"), ("POST", "pair"), ("POST", "trust")):
            return True
        if parts[0] == "api" and len(parts) == 3 and self.command in ("GET", "HEAD") and parts[1] in ("trust", "certificate"):
            return True
        if parts[0] == DESIGN_ROUTE and len(parts) > 3 and access.design_pass_valid(unquote(parts[1]), parts[2]):
            return True
        return self._machine or self._device is not None

    def _detail(self, exc: Exception) -> str:
        """An internal error's text, which can name private paths: the CLI and paired devices read it, so a
        fault stays visible to the operator, and any other client gets a fixed message."""
        return str(exc) if self._machine or self._device is not None else INTERNAL_ERROR

    def _still_admitted(self) -> bool:
        """Whether a long stream may go on: a device revoked while it streams loses the stream too."""
        return self._machine or self._device is not None and access.known(self._device["id"])

    def _device_cookie(self, key: str, max_age: int = access.COOKIE_SECONDS) -> str:
        secure = "; Secure" if isinstance(self.connection, ssl.SSLSocket) else ""
        return f"{access.COOKIE}={key}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Strict{secure}"

    def _task_owner(self, o: dict, *, running: bool = True) -> bool:
        """Whether this connection comes from the current worker job of the task attempt the body names, running unless
        `running` is false: the whole admission of the task door, decided before anything else reads the task, so
        every other caller, with or without this machine's key, gets one refusal that tells nothing about tasks.
        A record change is admitted after the task left running, as when the worker's stop checkpoint follows its
        block, while that attempt's worker job lasts."""
        try:
            if "run" in o:
                config.project(o["project"])
                run = project_agents.door_run(o["project"], o["run"])
                return terminal.owner_connection(self.client_address, self.connection.getsockname(),
                                                 project_agents.door_unit(o["project"], run))
            project, slug, attempt = o["project"], o["slug"], o["attempt"]
            if type(attempt) not in (str, int):
                return False
            config.project(project)
            task = S.load_task(project, slug)
            return ((not running or task.get("state") == "running") and str(task.get("attempt")) == str(attempt)
                    and task_owner_connection(project, slug, task, self.client_address, self.connection.getsockname()))
        except (KeyError, TypeError, ValueError, OSError, RuntimeError):  # RuntimeError: no worker record to match
            return False

    def _local(self) -> bool:
        """Whether the request comes from the computer running Altitude itself."""
        try:
            address = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            return False
        return (getattr(address, "ipv4_mapped", None) or address).is_loopback

    def _trust_view(self) -> dict:
        """What the pairing screen needs to walk a device through trusting this installation's certificate."""
        local = self._local()
        return {"local": local, "https": isinstance(self.connection, ssl.SSLSocket),
                "check": TRUST is not None and isinstance(self.connection, ssl.SSLSocket),
                # alt trust on the hosting computer trusts only Altitude's own CA, outside a container.
                "command": local and TRUST is not None and not platform.containerized(),
                "guide": tls.setup_guide(),
                "certificate": {"name": TRUST.name} if TRUST else None}

    def _certificate_download(self, name: str) -> None:
        """The public CA certificate a device installs to trust Altitude: the iPhone/iPad profile or the file."""
        downloads = {
            "altitude.mobileconfig": ("application/x-apple-aspen-config", 'attachment; filename="altitude.mobileconfig"'),
            "altitude.crt": ("application/x-x509-ca-cert", 'attachment; filename="altitude.crt"'),
        }
        ca = config.TLS_DIR / "ca.crt"
        if name not in downloads or not config.TLS or not ca.exists():
            return self._json({"error": "not found"}, 404)
        content_type, disposition = downloads[name]
        try:
            body = ca.read_bytes()
            data = tls.profile(body, tls.identity(ca)) if name.endswith(".mobileconfig") else body
        except (tls.TLSFailure, OSError, ValueError) as exc:
            return self._json({"error": f"Could not read the certificate: {self._detail(exc)}"}, 409)
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", disposition)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _pair(self, body: dict) -> None:
        name = access.device_name(self.headers.get("User-Agent") or "", body.get("standalone") is True)
        try:
            key, device = access.redeem(body.get("code"), name)
        except access.AccessError as exc:
            return self._json({"error": str(exc)}, exc.status)
        log(f"paired a device: {name}")
        self._set_cookie = self._device_cookie(key)
        return self._json({"device": {k: device[k] for k in ("id", "name", "paired", "used")}})

    def _sign_in(self, engine: str, body: dict | None) -> None:
        """Altitude's own sign-in on an engine (`engines.token_setup`), for a paired browser or this computer's `alt`.
        Every outcome is the sign-in's own view or a fixed sentence: nothing read from the engine's screen, and no
        exception's text, reaches the response or the log."""
        setup = engines.token_setup
        try:
            if body is None:
                return self._json(setup.view(engine))
            action = body.get("action") if isinstance(body, dict) else None
            if action == "start" and body.keys() == {"action"}:
                return self._json(setup.start(engine))
            if action == "finish" and body.keys() == {"action", "attempt", "code"} and isinstance(body["attempt"], str):
                return self._json(setup.finish(engine, body["attempt"], body["code"]))
            if action == "cancel" and body.keys() <= {"action", "attempt"}:
                return self._json(setup.cancel(engine, body.get("attempt")))
            return self._json({"error": "Unknown sign-in request."}, 400)
        except setup.SignInError as exc:
            return self._json({"error": str(exc)}, 409)
        except Exception as exc:  # noqa: BLE001 - the type alone: its text could carry what the screen showed
            log(f"sign-in request failed: {type(exc).__name__}")
            return self._json({"error": "The sign-in failed. Try again."}, 500)

    def _devices_post(self, action: str, body: dict) -> None:
        if action == "code":
            return self._json({**access.issue_code(), **pairing_directions()})
        if action in ("share", "share-close"):
            denied = self._terminal_denied(json_body=True, subject="Set up a device")
            if denied:
                return self._json({"error": denied}, 403)
            if action == "share-close":
                if body.keys() - {"link"} or not isinstance(body.get("link"), str):
                    return self._json({"error": "Name the link to close."}, 400)
                close_share(body["link"])
                return self._json({"closed": True})
            try:
                return self._json(open_share())
            except (tls.TLSFailure, OSError) as exc:
                return self._json({"error": str(exc)}, 409)
        if action != "revoke" or not isinstance(body.get("id"), str):
            return self._json({"error": "unknown api"}, 404)
        try:
            access.revoke(body["id"])
        except access.AccessError as exc:
            return self._json({"error": str(exc)}, exc.status)
        log("revoked a paired device")
        current = self._device and self._device["id"]
        if current == body["id"]:
            current, self._set_cookie = None, self._device_cookie("", 0)
        return self._json({"devices": access.devices(), "current": current})

    def _terminal_denied(self, *, json_body: bool, subject: str = "Terminal") -> str | None:
        """Why a terminal or update request is refused: a cross-site page (both run commands, so a page
        elsewhere must not be able to start them) or one of Altitude's own agents."""
        unavailable = platform.container_unavailable(subject)
        if unavailable:
            return unavailable
        return self._page_denied(json_body=json_body, subject=subject)

    def _page_denied(self, *, json_body: bool, subject: str) -> str | None:
        """Page-only operator actions share origin and agent authority without assuming a native host."""
        if self._cross_site() or json_body and self.headers.get_content_type() != "application/json":
            return f"{subject} requests must come from Altitude's own page."
        if self._from_agent():
            return f"{subject} requests from Altitude's own agents are refused."
        return None

    def _repair_denied(self, *, json_body: bool, subject: str = "Repair") -> bool:
        """Whether this request is not from a paired browser's own page. A request with the machine key, which the CLI,
        L3 and anything that read it hold, is refused like one from Altitude's agents: a repair's page carries what an
        agent with the operator's full access ran and read, and only that page steers it; an agent chat's is the
        operator's own conversation with its agent, which no other agent reads."""
        if self.headers.get(access.KEY_HEADER) is not None or not self._device or self._page_denied(
                json_body=json_body, subject=subject):
            self.close_connection = True
            return True
        return False

    def _repair_post(self, parts: list[str], o: dict) -> None:
        """Start, message, answer, keep going and stop (`_repair_denied`); each request names itself, so a repeated tap
        or a replayed request acts once."""
        if self._repair_denied(json_body=True):
            return self._json({"error": repair.FROM_BROWSER}, 403)
        try:
            if len(parts) == 2:
                continues = o.get("continues")
                return self._json(repair.start_app(o.get("problem"), o.get("request"),
                                                   continues if isinstance(continues, str) else None))
            action = parts[3] if len(parts) == 4 else ""
            if action == "message":
                return self._json(repair.message(parts[2], o.get("text"), o.get("request")))
            if action == "answer":
                return self._json(repair.answer(parts[2], o.get("question"), o.get("answer"), o.get("request")))
            if action == "keep-going":
                return self._json(repair.keep_going(parts[2], o.get("request")))
            if action == "stop":
                return self._json(repair.stop(parts[2], o.get("request")))
            return self._json({"error": "not found"}, 404)
        except repair.Refused as exc:
            return self._json({"error": str(exc), **({"running": exc.running} if exc.running else {})}, 409)
        except KeyError:
            return self._json({"error": "That repair is not on this computer."}, 404)

    def _chat_get(self, parts: list[str]) -> None:
        """A project's agent chats, or one chat's page, for a paired browser only (`_repair_denied`)."""
        if self._repair_denied(json_body=False, subject="Agent chat"):
            return self._json({"error": chats.PAGE_FROM_BROWSER}, 403)
        project = unquote(parts[2])
        if not config.is_managed(project):
            return self._json({"error": "There is no such project."}, 404)
        try:
            return self._json(chats.listing(project) if len(parts) == 3 else chats.view(project, parts[3]))
        except chats.Missing as exc:
            return self._json({"error": str(exc)}, 404)

    def _chat_post(self, parts: list[str], o: dict) -> None:
        """New, message, remove, retry, rename, model, stop, archive, reopen and delete, from a paired browser only
        (`_repair_denied`); each request names itself, so a repeated tap or a replayed request acts once. A message
        starts the chat's next turn when none is running (`chats.run`)."""
        if self._repair_denied(json_body=True, subject="Agent chat"):
            return self._json({"error": chats.FROM_BROWSER}, 403)
        project = unquote(parts[2]) if len(parts) > 2 else ""
        if not config.is_managed(project):
            return self._json({"error": "There is no such project."}, 404)
        uploads, image_ids, request = o.get("images"), o.get("image_ids"), o.get("request")
        try:
            if len(parts) == 3:
                if uploads or image_ids:
                    require_image_capability(project, engine=(o.get("choice") or {}).get("engine"))
                result = chats.new(project, o.get("text"), request, uploads=uploads, image_ids=image_ids,
                                   choice=o.get("choice"))
                spawn(f"chat:{project}:{result['id']}", chats.run, project, result["id"])
                return self._json(chats.view(project, result["id"]))
            chat_id, action = parts[3], parts[4] if len(parts) == 5 else ""
            if action == "message":
                if uploads or image_ids:
                    require_image_capability(project, engine=chats.load(project, chat_id)["engine"])
                result = chats.message(project, chat_id, o.get("text"), request, uploads=uploads, image_ids=image_ids)
            elif action == "remove":
                result = chats.remove(project, chat_id, o.get("message"), request)
            elif action == "retry":
                result = chats.retry(project, chat_id, request)
            elif action == "rename":
                result = chats.rename(project, chat_id, o.get("name"), request)
            elif action == "model":
                result = chats.set_choice(project, chat_id, o.get("choice"), request)
            elif action == "stop":
                result = chats.stop(project, chat_id, request)
            elif action == "archive":
                result = chats.archive(project, chat_id, request, discard=o.get("discard") is True)
            elif action == "reopen":
                result = chats.reopen(project, chat_id, request)
            elif action == "delete":
                return self._json(chats.delete(project, chat_id))
            else:
                return self._json({"error": "not found"}, 404)
            if action in ("message", "retry", "stop"):
                spawn(f"chat:{project}:{chat_id}", chats.run, project, chat_id)
            return self._json(chats.view(project, result["id"]))
        except chats.Refused as exc:
            return self._json({"error": str(exc)}, 409)
        except chats.Missing as exc:
            return self._json({"error": str(exc)}, 404)

    def _from_agent(self) -> bool:
        """Whether this connection comes from one of Altitude's own agents. One connection keeps one client socket, so
        its first request decides for the rest. A container installation infers nothing: its clients reach it through
        the published port, and the operator's browser, which holds the device cookie, is on the host."""
        if self._agent is None:
            self._agent = not platform.containerized() and terminal.agent_connection(
                self.client_address, self.connection.getsockname())
        return self._agent

    def _terminal_get(self, parts: list[str], q: dict) -> None:
        denied = self._terminal_denied(json_body=False)
        if denied:
            return self._json({"error": denied}, 403)
        project, slug = unquote(parts[2]), (q.get("task") or [None])[0]
        if len(parts) == 3:
            return self._json(terminal.status(project, slug))
        if len(parts) == 4 and parts[3] == "stream":
            return self._terminal_stream(project, slug, (q.get("id") or [None])[0], int((q.get("offset") or ["0"])[0]))
        return self._json({"error": "unknown api"}, 404)

    def _terminal_stream(self, project: str, slug: str | None, ident: str | None, offset: int) -> None:
        """Server-sent output of terminal `ident` from `offset`: `output` events carry base64 bytes and the
        next offset; `end` carries how it ended. The page reconnects with its own offset after a lost
        connection."""
        try:
            term = terminal.stream(project, slug, ident)
        except terminal.TerminalError as exc:
            return self._json({"error": str(exc)}, exc.status)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        if self.command == "HEAD":
            return
        quiet, first = 0.0, True
        while True:
            if select.select([self.connection], [], [], 0)[0] and not self.connection.recv(1) or not self._still_admitted():
                return
            # The first read takes, without waiting, what the page has not drawn: the output it catches up on.
            data, offset, missed, ended, replay, pieces = terminal.read(
                term, offset, 0 if first else TERMINAL_WAIT_SECONDS, first)
            first = False
            if data or missed:
                payload = json.dumps({"offset": offset, "data": base64.b64encode(data).decode(), "missed": missed,
                                      "replay": replay, "pieces": pieces})
                self.wfile.write(f"event: output\ndata: {payload}\n\n".encode())
                quiet = 0.0
            elif not ended:
                quiet += TERMINAL_WAIT_SECONDS
                if quiet < CHANGE_KEEPALIVE_SECONDS:
                    continue
                self.wfile.write(b": keepalive\n\n")
                quiet = 0.0
            if ended:
                self.wfile.write(f"event: end\ndata: {json.dumps(terminal.view(term))}\n\n".encode())
                self.wfile.flush()
                return
            self.wfile.flush()

    def _update_post(self, parts: list[str], body: dict) -> None:
        """The app's Update button and the update switches, behind the terminal's request checks."""
        denied = self._terminal_denied(json_body=True, subject="Update")
        if denied:
            return self._json({"error": denied}, 403)
        try:
            if parts in (["api", "update-check"], ["api", "update-automatic"]):
                if body.keys() - {"enabled"} or not isinstance(body.get("enabled"), bool):
                    return self._json({"error": "Choose on or off."}, 400)
                setting = parts[1].replace("-", "_")
                view = _save_machine(setting, body["enabled"], f"{setting.replace('_', ' ').capitalize()} "
                                     + ("on" if body["enabled"] else "off"))
                return self._json({**view, "update": installation.update_status()})
            if parts != ["api", "update"] or body.keys() - {"version"} or not isinstance(body.get("version"), str):
                return self._json({"error": "Name the version to install."}, 400)
            return self._json({"update": installation.request_update(body["version"])})
        except installation.UpdateRefused as exc:
            return self._json({"error": str(exc)}, 409)
        except Exception as exc:  # noqa: BLE001 — record, lock and launch failures name private paths; they stay in the log
            log(f"update request failed: {exc!r}")
            return self._json({"error": "Altitude could not complete the update request. Repair finds out why: Settings → About, or alt repair in a terminal."}, 503)

    def _in_line(self, call) -> None:
        """`alt task validate` and `alt task run`: `call(waiting, gone)` answered as JSON. A client that accepts NDJSON
        hears what a busy machine makes its request wait for and when it is admitted, one line at a time, then gets
        the answer as the last line. Once its request is read, the client sends nothing more: a connection at its end, or failed, means the
        client closed it or went away, which takes a waiting request out of the line and stops an admitted run
        (docs/DEVELOPMENT.md#validation-runner)."""
        streaming, started = "application/x-ndjson" in (self.headers.get("Accept") or ""), False

        def waiting(text: str) -> None:
            nonlocal started
            if not streaming:
                return
            if not started:
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                started = True
            self.wfile.write(json.dumps({"waiting": text}).encode() + b"\n")

        def gone() -> bool:
            # Only what arrives is read: TLS handles its own records (a key update reads as no data), the end of the
            # connection reads as empty, and anything else the client sends is dropped.
            try:
                poller = select.poll()
                poller.register(self.connection, select.POLLIN)
                if not poller.poll(0):
                    return False
                self.connection.settimeout(0)
                try:
                    return not self.connection.recv(65536)
                finally:
                    self.connection.settimeout(None)
            except (ssl.SSLWantReadError, ssl.SSLWantWriteError, BlockingIOError):
                return False
            except (OSError, ValueError):
                return True
        try:
            answer, code = call(waiting, gone), 200
        except PermissionError as exc:
            answer, code = {"error": str(exc)}, 403
        except (ValueError, KeyError, OSError, RuntimeError) as exc:
            answer, code = {"error": str(exc)}, 400
        if not started:
            return self._json(answer, code)
        try:
            self.wfile.write(json.dumps(answer, default=str).encode() + b"\n")
        except OSError as exc:
            log(f"{self.command} {self.path}: client went away ({type(exc).__name__}: {exc})")

    def _improve_post(self, body: dict) -> None:
        """The operator's presses on improvement suggestions: Stop on the running line, Make a task, Dismiss, Undo
        and Try again on the card; the Weekly switch (Turn on, Pause and Resume) and Run now on the review's agent
        page, with `terms` when the sheet's Turn on agreed. Each carries a request id, so a repeated press returns its
        first receipt."""
        try:
            if body.keys() - {"project", "action", "ref", "request_id", "on", "terms"} \
                    or body.get("action") not in ("stop", "accept", "dismiss", "undo", "retry", "weekly", "run"):
                raise ValueError("Unsupported improvement request.")
            project, action, ref = str(body.get("project")), body["action"], str(body.get("ref"))
            config.project(project)
            request_id = uuid.UUID(str(body.get("request_id"))).hex
            if action in ("weekly", "run"):
                # Agreeing sends the project's conversations to a provider: only the operator's paired device may, never
                # a client holding the machine key. Every POST already comes from Altitude's own page (_refused).
                if self._device is None:
                    return self._json({"error": "Improvement suggestions are turned on only from a paired device."}, 403)
                terms = body.get("terms")
                if terms is not None and not isinstance(terms, str) or action == "weekly" and not isinstance(body.get("on"), bool):
                    raise ValueError("Unsupported improvement request.")
                if action == "weekly":
                    return self._json({"ok": True, **improve.set_weekly(project, body["on"], agree=terms)})
                result = improve.run_now(project, request_id, agree=terms)
                if not result.get("replayed"):
                    spawn(f"improve:{project}", improve.execute, project, result["run"])
                return self._json({"ok": True, **result})
            if action == "stop":
                result = improve.stop(project, request_id, ref)
            elif action == "retry":
                result = suggestions.retry(project, ref, request_id)
            else:
                result = suggestions.decide(project, ref, action, request_id)
        except KeyError as exc:
            return self._json({"error": exc.args[0]}, 404)
        except ValueError as exc:
            return self._json({"error": str(exc)}, 409)
        if action in ("accept", "retry"):
            request_l3_drain(project)
        return self._json({"ok": True, **result})

    def _agents_get(self, parts: list[str], q: dict) -> None:
        """The Agents page, an agent's page, a run's page and a run's session (`project_agents`), and the Weekly
        improvement review's page (`improve.page`)."""
        project = parts[0]
        try:
            config.project(project)
            if len(parts) == 1:
                return self._json(project_agents.view(project))
            agent = parts[1]
            if parts[1:] == ["weekly"]:
                if (weekly := improve.page(project)) is None:
                    return self._json({"error": "This project has no Weekly improvement review."}, 404)
                return self._json(weekly)
            if len(parts) == 2:
                return self._json(project_agents.agent_view(project, agent, runs=max(1, int(q.get("runs", ["10"])[0]))))
            if len(parts) in (4, 5) and parts[2] == "runs" and parts[3].isdigit():
                n = int(parts[3])
                if len(parts) == 4:
                    return self._json(project_agents.run_view(project, agent, n))
                if parts[4] == "transcript":
                    return self._json(transcript.view(
                        project, f"project-agent-{agent}-{n}", run=(agent, n),
                        engine=q.get("engine", [""])[0], session_id=q.get("session_id", [""])[0],
                        attempt=int(q.get("attempt", ["0"])[0]), raw=q.get("raw", ["0"])[0] == "1",
                        mode=q.get("mode", ["initial"])[0], cursor=q.get("cursor", [""])[0],
                        before=q.get("before", [""])[0], lower=q.get("lower", [""])[0],
                        after=q.get("after", [""])[0], record=q.get("record", [""])[0],
                        offset=int(q.get("offset", ["0"])[0])), compress=True)
        except transcript.TranscriptAccessError:
            return self._json({"error": "transcript unavailable for this run"}, 404)
        except KeyError as exc:
            return self._json({"error": str(exc) if isinstance(exc, project_agents.Missing)
                               else "Project is not managed."}, 404)
        except ValueError as exc:
            return self._json({"error": str(exc)}, 400)
        return self._json({"error": "unknown api"}, 404)

    def _agents_post(self, body: dict) -> None:
        """The operator's presses on project agents, from a paired device only: each saves one change
        (`project_agents.change`) and carries a request id, so a repeated press returns its first receipt."""
        if self._device is None:
            return self._json({"error": "Routines are changed only from a paired device."}, 403)
        try:
            project, action = str(body.get("project")), body.get("action")
            config.project(project)
            request_id = uuid.UUID(str(body.get("request_id"))).hex
            fields = {key: value for key, value in body.items() if key not in ("project", "action", "request_id")}
            result = project_agents.change(project, str(action), fields, by="operator", source=f"press:{request_id}",
                                           request_id=request_id)
        except KeyError as exc:
            return self._json({"error": str(exc) if isinstance(exc, project_agents.Missing)
                               else "Project is not managed."}, 404)
        except ValueError as exc:
            return self._json({"error": str(exc)}, 409)
        if action == "run":
            spawn(f"project-agents:{project}", project_agents.start_next, project)
        return self._json({"ok": True, **result})

    def _validation_post(self, body: dict) -> None:
        """The validation switch, behind the terminal's request checks: an agent cannot turn its own runner back on."""
        denied = self._terminal_denied(json_body=True, subject="Validation")
        if denied:
            return self._json({"error": denied}, 403)
        if body.keys() - {"enabled"} or not isinstance(body.get("enabled"), bool):
            return self._json({"error": "Choose on or off."}, 400)
        try:
            validation.set_enabled(body["enabled"])
        except OSError as exc:
            return self._json({"error": f"Could not save the validation switch: {exc.strerror}"}, 400)
        log(f"validation runs turned {'on' if body['enabled'] else 'off'} by the operator")
        return self._json(machine_view())

    def _terminal_post(self, parts: list[str], body: dict) -> None:
        denied = self._terminal_denied(json_body=True)
        if denied:
            return self._json({"error": denied}, 403)
        # Typing sends one request per keystroke or burst: a fresh TCP and TLS handshake for each would put two
        # more round trips before every echo.
        self._keep_open = True
        if parts == ["api", "terminal-access"]:
            if body.keys() - {"enabled"} or not isinstance(body.get("enabled"), bool):
                return self._json({"error": "Choose on or off."}, 400)
            try:
                view = _save_machine("terminal", body["enabled"], "Terminal on" if body["enabled"] else "Terminal off")
            except (ValueError, T.TransitionError) as exc:
                return self._json({"error": str(exc)}, 400)
            if not body["enabled"]:
                try:
                    terminal.close_all()
                except terminal.TerminalError as exc:
                    return self._json({"error": f"Terminal is off, but a terminal is still running. {exc}"}, exc.status)
            return self._json(view)
        if len(parts) != 4 or parts[3] not in ("open", "input", "command", "resize", "close"):
            return self._json({"error": "unknown api"}, 404)
        project, action, slug, ident = unquote(parts[2]), parts[3], body.get("task"), body.get("id")
        if slug is not None and not isinstance(slug, str):
            return self._json({"error": "Name the task as text."}, 400)
        if action != "open" and not isinstance(ident, str):
            return self._json({"error": "Name the terminal."}, 400)
        try:
            if action == "open":
                return self._json(terminal.open_terminal(project, slug))
            if action == "input":
                terminal.write(project, slug, ident, body.get("data"), body.get("answer"))
            elif action == "command":
                terminal.hand(project, slug, ident, body.get("text"), body.get("bracketed"))
            elif action == "resize":
                terminal.resize(project, slug, ident, body.get("cols"), body.get("rows"))
            else:
                terminal.close(project, slug, ident=ident)
            return self._json({"ok": True})
        except terminal.TerminalError as exc:
            return self._json({"error": str(exc)}, exc.status)

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

    def _changes(self) -> None:
        """The web shell's change stream: a `change` event names the projects whose records moved.

        The baseline is read before the response opens, so a client that refreshes once its stream
        opens cannot miss a change made between that refresh and the subscription."""
        marks = change_marks()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        if self.command == "HEAD":
            return
        self.wfile.write(f"retry: {CHANGE_RETRY_MS}\n\n".encode())
        self.wfile.flush()
        quiet = 0.0
        while True:
            # The wait doubles as disconnect detection: a closed tab reads as end of stream.
            if select.select([self.connection], [], [], CHANGE_SECONDS)[0] and not self.connection.recv(1):
                return
            if not self._still_admitted():
                return
            current = change_marks()
            changed = sorted(name for name in marks.keys() | current.keys() if marks.get(name) != current.get(name))
            if "" in changed:  # a registry edit (engine pin, WIP cap) can change any project's view
                changed = sorted(current)
            marks, quiet = current, quiet + CHANGE_SECONDS
            if changed:
                projects = json.dumps({"projects": [name for name in changed if name and name != REPAIRS_MARK]})
                self.wfile.write(f"event: change\ndata: {projects}\n\n".encode())
            elif quiet >= CHANGE_KEEPALIVE_SECONDS:
                self.wfile.write(b": keepalive\n\n")
            else:
                continue
            self.wfile.flush()
            quiet = 0.0

    def do_GET(self) -> None:
        refused = self._refused()
        if refused:
            return self._json({"error": refused}, 403)
        u = urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]
        q = parse_qs(u.query)
        if not self._admit(parts):
            return self._json({"error": UNPAIRED, "pair": True}, 401)
        try:
            if parts and parts[0] == DESIGN_ROUTE:
                return self._design(parts)
            if not parts or parts[0] != "api":
                return self._static(u.path)
            api = parts[1] if len(parts) > 1 else ""
            if api == "health":
                release = config.RELEASE or {}
                return self._json({"version": release.get("version"), "commit": release.get("commit"),
                                   "pid": os.getpid()})
            if api == "files":
                if self.command != "GET":
                    return self._json({"error": "Use GET to read a document."}, 405)
                try:
                    query = parse_qs(u.query, keep_blank_values=True, strict_parsing=True,
                                     errors="strict", max_num_fields=1)
                except ValueError:
                    return self._json({"error": "Choose one file reference."}, 400)
                if (len(parts) != 3 or u.path != "/api/files/" + parts[-1]
                        or set(query) != {"path"} or len(query["path"]) != 1 or not query["path"][0]):
                    return self._json({"error": "Choose one file reference."}, 400)
                try:
                    return self._json(T.task_file(unquote(parts[2]), query["path"][0]))
                except T.TaskFileError as exc:
                    return self._json({"error": str(exc)}, exc.status)
            if api == "shown":
                return self._shown(parts[2:])
            if api == "captures":
                return self._captures(parts[2:])
            if api == "images":
                return self._images(parts, q)
            if api == "access":
                return self._json({"paired": self._machine or self._device is not None,
                                   "device": self._device and self._device["name"], "trust": self._trust_view()})
            if api == "trust" and len(parts) == 3:
                if TRUST is None:
                    return self._json({"error": "Altitude cannot check certificate trust here."}, 409)
                outcome = TRUST.confirm(parts[2], self.client_address[0], self._probed)
                if outcome == "unknown":
                    return self._json({"error": "That check has expired. Check again."}, 404)
                # A retry is an ordinary answer, so browsers log no failed request for it.
                return self._json({"retry": True} if outcome == "retry" else {"trusted": outcome == "trusted"})
            if api == "certificate" and len(parts) == 3:
                return self._certificate_download(parts[2])
            if api == "devices" and len(parts) == 2:
                return self._json({"devices": access.devices(), "current": self._device and self._device["id"],
                                   "certificate": certificate_view()})
            if api == "overview":
                return self._json(overview(), compress=True)
            if api == "voice":
                return self._json(voice_view(self._device["id"] if self._device else None))
            if api == "machine":
                return self._json(machine_view())
            if api == "prerequisites":
                return self._json({"items": installation.prerequisites()})
            if api == "repairs" and len(parts) == 2:
                return self._json(repair.listing())
            if api == "chats" and len(parts) in (3, 4):
                return self._chat_get(parts)
            if api == "repairs" and len(parts) == 3:
                if self._repair_denied(json_body=False):
                    return self._json({"error": repair.PAGE_FROM_BROWSER}, 403)
                try:
                    return self._json(repair.view(parts[2]))
                except KeyError:
                    return self._json({"error": "That repair is not on this computer."}, 404)
            if api == "sign-in" and len(parts) == 3:
                return self._sign_in(parts[2], None)
            if api == "folders":
                try:
                    return self._json(folders((q.get("path") or [None])[0]))
                except FolderError as exc:
                    return self._json({"error": str(exc)}, exc.status)
            if api == "changes":
                return self._changes()
            if api == "terminal" and len(parts) > 2:
                return self._terminal_get(parts, q)
            if api == "alerts":
                try:  # without a key the page keeps alerting while it is open, and says so
                    return self._json({"key": push.public_key()})
                except push.PushFailure as exc:
                    return self._json({"key": None, "why": str(exc)})
            if api == "setup" and len(parts) == 3:
                try:
                    return self._json(project_setup.observe(parts[2]))
                except KeyError:
                    return self._json({"error": "Project is not managed."}, 404)
            if api == "project" and len(parts) > 2:
                return self._json(project_view(parts[2]), compress=True)
            if api == "agents" and len(parts) > 2:
                return self._agents_get(parts[2:], q)
            if parts == ["api", "project-model-settings"]:
                return self._json({"rows": config.project_model_settings()})
            if api == "defaults" and len(parts) == 3:
                try:
                    return self._json(config.defaults_view(parts[2]))
                except KeyError:
                    return self._json({"error": "Project is not managed."}, 404)
            if api == "task" and len(parts) > 3:
                try:
                    return self._json(task_view(parts[2], parts[3]), compress=True)
                except S.TaskNotFound:
                    return self._json({"error": "Task is not available."}, 404)
            if api == "transcript" and len(parts) > 3:
                try:
                    return self._json(transcript.view(
                        parts[2], parts[3],
                        engine=q.get("engine", [""])[0], session_id=q.get("session_id", [""])[0],
                        attempt=int(q.get("attempt", ["0"])[0]), raw=q.get("raw", ["0"])[0] == "1",
                        mode=q.get("mode", ["initial"])[0], cursor=q.get("cursor", [""])[0],
                        before=q.get("before", [""])[0], lower=q.get("lower", [""])[0],
                        after=q.get("after", [""])[0], record=q.get("record", [""])[0],
                        offset=int(q.get("offset", ["0"])[0])), compress=True)
                except (KeyError, transcript.TranscriptAccessError):
                    return self._json({"error": "transcript unavailable for this task generation"}, 404)
                except ValueError as exc:
                    return self._json({"error": str(exc)}, 400)
            if api == "monitor":
                return self._json({"seats": route.seats(), "reserve": config.RESERVE, "routing": monitor.routing(),
                                   "sessions": monitor.sessions(chats_too=bool(self._device) and self.headers.get(
                                       access.KEY_HEADER) is None),
                                   "agents": monitor.agents(), "clock": schedule.zone()})
            if api == "digest":
                return self._json({"text": digest.text()})
            if api == "chat" and len(parts) > 2:
                project = parts[2]
                config.project(project)  # a removed or unknown project's conversation is refused
                lifecycle = l3.chat_state(project, int(q.get("limit", ["60"])[0]), q.get("reach", [None])[0])
                return self._json({**lifecycle, "l3": l3.info(project)})
            return self._json({"error": "unknown api"}, 404)
        except images.ImageError as exc:
            return self._json({"error": str(exc)}, exc.status)
        except (ssl.SSLError, BrokenPipeError, ConnectionResetError) as e:  # the client left mid-response (a phone's audio player, a closed tab): not a fault
            log(f"GET {self.path}: client went away ({type(e).__name__}: {e})")
            return
        except Exception as e:  # noqa: BLE001
            log(f"GET {self.path}: {e}\n{traceback.format_exc()}")
            return self._json({"error": "Image temporarily unavailable." if len(parts) > 1 and parts[1] == "images" else self._detail(e)}, 500)

    def _task_door(self, o: dict):
        """A record change from a task worker's own processes (`door`), applied as the admitted task's L2, or a
        project agent run's sign-in."""
        try:
            if "run" in o:
                return self._json(project_agents.door(o["project"], o["run"], o))
            return self._json(door.handle(o["project"], o["slug"], int(o["attempt"]), o))
        except (T.TransitionError, ValueError, KeyError, TypeError) as exc:
            return self._json({"error": str(exc)}, 409)

    def _review_run(self, body):
        streamed = False

        def heartbeat():
            nonlocal streamed
            try:
                if not streamed:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.close_connection = True
                    streamed = True
                # JSON whitespace keeps the wait connected without a review-duration deadline.
                self.wfile.write(b" ")
                self.wfile.flush()
                return True
            except OSError:
                return False

        try:
            review = reviews.run(body["project"], body["slug"], body["review_id"], actor="l2",
                                 expected_attempt=int(body["attempt"]), context_ids=body.get("context_ids"),
                                 proposal_id=body.get("proposal_id"), show=door.decode(body.get("show")),
                                 on_wait=heartbeat)
            result, code = {"ok": True, "review": review}, 200
        except (T.TransitionError, ValueError, KeyError) as exc:
            result, code = {"ok": False, "error": str(exc)}, 409
        if streamed:
            self.wfile.write(json.dumps(result).encode())
            return
        return self._json(result, code)

    def do_POST(self) -> None:
        refused = self._refused()
        if refused:
            self.close_connection = True  # the unread body cannot be mistaken for the next request
            return self._json({"error": refused}, 403)
        u = urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]
        owned = parts in TASK_DOOR
        if not self._admit(parts) and not owned:
            self.close_connection = True
            return self._json({"error": UNPAIRED, "pair": True}, 401)
        if not self._machine and not owned and self._from_agent():
            self.close_connection = True
            return self._json({"error": repair.FROM_BROWSER if parts[:2] == ["api", "repairs"]
                               else chats.FROM_BROWSER if parts[:2] == ["api", "chats"] else AGENT_REFUSED}, 403)
        image_submission = False
        try:
            api = parts[1] if len(parts) > 1 and parts[0] == "api" else ""
            if api == "files":
                return self._json({"error": "Use GET to read a document."}, 405)
            if parts == ["api", "voice", "service"]:
                return self._service_voice()
            if api == "voice" and len(parts) > 2:
                return self._host_voice(parts, parse_qs(u.query))
            o = self._body(max_bytes=images.MAX_BODY if api in ("chat", "chats", "l2") else None)
            if owned and not self._task_owner(o, running=parts != ["api", "task", "door"]):
                return self._json({"error": TASK_DOOR_REFUSED}, 403)
            image_submission = api in ("chat", "chats", "l2") and bool(o.get("images") or o.get("image_ids"))
            if api in ("terminal", "terminal-access"):
                return self._terminal_post(parts, o)
            if parts == ["api", "pair"]:
                return self._pair(o)
            if parts == ["api", "trust"]:
                if TRUST is None or not isinstance(self.connection, ssl.SSLSocket):
                    return self._json({"error": "Altitude cannot check certificate trust here."}, 409)
                return self._json({"challenge": TRUST.arm(self.client_address[0])})
            if api == "devices" and len(parts) == 3:
                return self._devices_post(parts[2], o)
            if api == "sign-in" and len(parts) == 3:
                return self._sign_in(parts[2], o)
            if api in ("update", "update-check", "update-automatic"):
                return self._update_post(parts, o)
            if api == "repairs":
                return self._repair_post(parts, o)
            if api == "chats" and 3 <= len(parts) <= 5:
                return self._chat_post(parts, o)
            if parts == ["api", "validation-access"]:
                return self._validation_post(o)
            if parts == ["api", "task", "door"]:
                return self._task_door(o)
            if parts == ["api", "task", "review", "run"]:
                try:
                    if o.keys() - {"project", "slug", "attempt", "review_id", "context_ids", "proposal_id", "show"}:
                        raise ValueError("Unsupported review execution fields.")
                    if not isinstance(o.get("attempt"), (str, int)) or isinstance(o["attempt"], bool):
                        raise ValueError("The current L2 attempt is required.")
                    return self._review_run(o)
                except (T.TransitionError, ValueError, KeyError) as exc:
                    return self._json({"error": str(exc)}, 409)
            if parts == ["api", "task", "review"]:
                try:
                    if o.keys() - {"project", "slug", "action", "request_id", "review_id", "reason", "focus", "subject"}:
                        raise ValueError("Unsupported review fields.")
                    project, slug, action = o["project"], o["slug"], o["action"]
                    if action == "request":
                        if not isinstance(o.get("request_id"), str) or not o["request_id"].strip():
                            raise ValueError("A review request identity is required.")
                        # A repeat names the review it replaces, so a stale target is refused rather than duplicated.
                        review = reviews.request(project, slug, actor=T.OPERATOR_MESSAGE_ROLE,
                                                 request_id=o["request_id"], focus=o.get("focus", ""), previous=o.get("review_id"),
                                                 subject=o.get("subject"))
                    elif action in ("cancel", "withdraw"):
                        operation = reviews.cancel if action == "cancel" else reviews.withdraw
                        review = operation(project, slug, o["review_id"], actor=T.OPERATOR_MESSAGE_ROLE,
                                           reason=o.get("reason", ""))
                    else:
                        raise ValueError("Unknown review action.")
                except (T.TransitionError, ValueError, KeyError) as exc:
                    return self._json({"error": str(exc)}, 409)
                try:
                    if S.load_task(project, slug).get("state") == "blocked":
                        request_task_resume(project, slug)
                except Exception as exc:  # #298: a durable request survives an immediate wake failure.
                    log(f"[{project}/{slug}] review wake deferred: {exc}")
                return self._json({"ok": True, "review": review})
            if parts == ["api", "task", "terminal"]:
                try:
                    if o.keys() - {"project", "slug", "attempt"}:
                        raise ValueError("alt task terminal: unsupported fields")
                    return self._json(owner_terminal_output(o["project"], o["slug"], o.get("attempt"),
                                                            self.client_address, self.connection.getsockname()))
                except PermissionError as exc:
                    return self._json({"error": str(exc)}, 403)
                except (ValueError, KeyError, OSError, RuntimeError) as exc:
                    return self._json({"error": str(exc)}, 400)
            if parts in (["api", "task", "validate"], ["api", "task", "run"]):
                peer, local = self.client_address, self.connection.getsockname()

                def owner(task: dict) -> bool:
                    return task_owner_connection(o["project"], o["slug"], task, peer, local)

                def call(waiting, gone) -> dict:
                    if parts[-1] == "run":
                        if o.keys() - {"project", "slug", "attempt", "command", "request"}:
                            raise ValueError("alt task run: unsupported fields")
                        return run_machine_command(o["project"], o["slug"], o.get("attempt"), o.get("command"),
                                                   o.get("request"), owner=owner, waiting=waiting, gone=gone)
                    if o.keys() - {"project", "slug", "attempt", "command", "kvm", "publish", "simulator", "capture", "container_browser"}:
                        raise ValueError("alt task validate: unsupported fields")
                    return validation.run(o["project"], o["slug"], o.get("attempt"), o.get("command"),
                                          kvm=o.get("kvm", False), publish=o.get("publish"),
                                          simulator=o.get("simulator", False), capture=o.get("capture", False),
                                          container_browser=o.get("container_browser", False),
                                          owner=owner, waiting=waiting, gone=gone)
                return self._in_line(call)
            if parts == ["api", "pr", "close"]:
                try:
                    if o.keys() - {"project", "number", "body"}:
                        raise ValueError("alt pr close: unsupported fields")
                    return self._json(pr_close(o.get("project"), o.get("number"), actor="operator",
                                               body=o.get("body", "")))
                except (ValueError, KeyError, OSError, subprocess.SubprocessError) as exc:
                    return self._json({"error": str(exc)}, 400)
            if api == "issue":
                try:
                    if o.keys() - {"project", "operation", "body", "title", "labels", "number", "reason", "add", "remove", "actor"}:
                        raise ValueError("alt issue: unsupported fields")
                    url = issue_write(o["project"], o.get("operation"), o.get("body", ""), actor="operator",
                                      title=o.get("title", ""), labels=o.get("labels"), number=o.get("number"),
                                      reason=o.get("reason"), add=o.get("add"), remove=o.get("remove"))
                    return self._json({"url": url})
                except (ValueError, OSError, subprocess.SubprocessError) as exc:
                    return self._json({"error": str(exc)}, 400)
            if parts == ["api", "project", "setup"]:
                try:
                    if o.keys() - {"project", "action", "expected", "reason"}:
                        raise ValueError("Unsupported setup fields.")
                    return self._json(request_project_setup(o["project"], o["action"], actor="operator", expected=o.get("expected"),
                                                           reason=o.get("reason") or "Project setup"))
                except PermissionError as exc:
                    return self._json({"error": str(exc)}, 403)
                except (ValueError, KeyError) as exc:
                    return self._json({"error": str(exc)}, 409)
            if parts == ["api", "defaults"]:
                try:
                    if o.get("setting") not in set(config.PROJECT_SETTINGS) - {"routing"}:
                        raise ValueError("unknown project default")
                    dispatch.request_setting(o["project"], o["setting"], o.get("value"), "Settings", actor=config.OPERATOR_ACTOR,
                                             **({"expected": o["expected"]} if "expected" in o else {}))
                    result = dispatch._run_setting(o["project"], o["setting"])
                    if result.get("changed"):
                        return self._json({"error": CHANGED_ELSEWHERE, "changed": True}, 409)
                    if result["status"] != "done":
                        raise ValueError(result["note"])
                    if l3.queue_path(o["project"]).exists():
                        request_l3_drain(o["project"])  # a message waiting for an engine tries the changed choice now
                    return self._json(config.defaults_view(o["project"]))
                except (ValueError, KeyError, T.TransitionError) as exc:
                    return self._json({"error": str(exc)}, 400)
            if parts == ["api", "project-model-settings", "reset"]:
                try:
                    result = reset_project_model_setting(o)
                    if result.get("changed"):
                        return self._json({"error": CHANGED_ELSEWHERE, "changed": True}, 409)
                    return self._json({"rows": config.project_model_settings()})
                except (ValueError, KeyError, T.TransitionError) as exc:
                    return self._json({"error": str(exc)}, 400)
            if parts == ["api", "new-tasks"]:
                try:
                    dispatch.request_setting(None, "new_tasks", o.get("value"), "Settings", actor=config.OPERATOR_ACTOR,
                                             **({"expected": o["expected"]} if "expected" in o else {}))
                    result = dispatch._run_setting(None, "new_tasks")
                    if result.get("changed"):
                        return self._json({"error": CHANGED_ELSEWHERE, "changed": True}, 409)
                    if result["status"] != "done":
                        raise ValueError(result["note"])
                    return self._json(new_tasks_view())
                except (ValueError, T.TransitionError) as exc:
                    return self._json({"error": str(exc)}, 400)
            if parts == ["api", "projects-folder"]:
                try:
                    return self._json(save_projects_folder(o))
                except (ValueError, T.TransitionError) as exc:
                    return self._json({"error": str(exc)}, 400)
            if parts in (["api", "operator-name"], ["api", "incident-reports"]):
                try:
                    return self._json((save_operator_name if parts[1] == "operator-name" else save_incident_reports)(o))
                except (ValueError, T.TransitionError) as exc:
                    return self._json({"error": str(exc)}, 400)
            if parts == ["api", "voice"]:
                denied = self._page_denied(json_body=True, subject="Voice")
                if denied or o.get("backend") == "service" and not self._device:
                    return self._json({"error": denied or "Pair this device to configure your speech service."}, 403)
                try:
                    save_voice(o)
                    return self._json(voice_view(self._device["id"] if self._device else None))
                except VoiceInputError as exc:
                    return self._json({"error": str(exc)}, exc.status)
                except (ValueError, KeyError, T.TransitionError) as exc:
                    return self._json({"error": str(exc)}, 400)
            if api == "project" and len(parts) > 2 and parts[2] == "add":
                try:
                    name = config.require_project_name(o["name"])
                    restoring = name not in config.load_projects() and bool(l3.chat_history(name, 1))
                    with config.add_project(name, path=o.get("path"), approval=o.get("approval") or "default") as entry:
                        pass
                except ValueError as exc:
                    return self._json({"error": str(exc)}, 400)
                except (OSError, RuntimeError) as exc:
                    return self._json({"error": f"cannot register the project: {exc}"}, 500)
                S.regen_state_md(name)
                project_setup.registered(name, start=True)
                request_project_setup(name, "repair", actor="altd")
                return self._json({"ok": True, "project": entry, "restored": restoring})
            if api == "project" and len(parts) > 2 and parts[2] == "remove":
                try:
                    config.remove_project(o["name"])
                except config.ProjectBusy as exc:
                    return self._json({"error": str(exc)}, 409)
                remove_l3_verb_broker(o["name"])
                return self._json({"ok": True})
            if api == "improve":
                return self._improve_post(o)
            if api == "agents":
                return self._agents_post(o)
            if api == "decide":
                project, slug = o["project"], o["slug"]
                try:
                    send = {"request_id": uuid.UUID(o["request_id"]).hex} if "request_id" in o else {}
                except (ValueError, TypeError, AttributeError):
                    return self._json({"error": "Answers require a valid submission identity."}, 400)
                try:
                    if "answers" in o or "group_id" in o or "group_revision" in o:
                        result = T.accept_questions(project, slug, o.get("group_id"), o.get("group_revision"), o.get("answers"), **send)
                    else:
                        if "text" in o and "option_key" in o:
                            raise T.TransitionError("send a quick option or custom text, not both")
                        result = T.accept_question_result(project, slug, o.get("question_id"), o.get("revision"),
                                                          o.get("option_key"), **({"text": o["text"]} if "text" in o else {}), **send)
                except T.TransitionError as exc:
                    return self._json({"error": str(exc)}, 409)
                if S.load_task(project, slug).get("state") == "blocked":
                    request_task_resume(project, slug)
                return self._json({"ok": True, "queued": True, **result,
                                   "response": result["question"]["response"],
                                   "state": S.load_task(project, slug)["state"]})
            if api == "task" and len(parts) > 2 and parts[2] == "action":
                project, slug, action = o["project"], o["slug"], o["action"]
                reason = o.get("reason") or f"{action} by the operator"
                try:
                    if action == "handoff":
                        observed = o.get("observed")
                        if not isinstance(observed, dict) or not isinstance(o.get("engine"), str):
                            return self._json({"error": "Refresh the task and choose a target engine."}, 400)
                        request_daemon_task_operation(
                            project, slug, "handoff", reason, actor=config.OPERATOR_ACTOR,
                            engine=o["engine"], expected_attempt=observed.get("attempt"), handoff_observed=observed,
                            selection=o.get("selection"), handoff_default=o.get("default_model"))
                    elif action in ("handoff-now", "handoff-cancel"):
                        dispatch.handoff_control(project, slug, str(o.get("request_id") or ""),
                                                 action.removeprefix("handoff-"), actor=config.OPERATOR_ACTOR,
                                                 reason=reason)
                        if action == "handoff-now":
                            spawn(f"task-operation:{project}:{slug}", dispatch.run_task_operation, project, slug)
                    elif action == "reject":
                        request_daemon_task_operation(project, slug, "reject", reason, actor=config.OPERATOR_ACTOR)
                    elif action == "resume":
                        request_daemon_task_operation(project, slug, "resume", reason, actor=T.OPERATOR_MESSAGE_ROLE,
                                                      stop_id=o.get("stop_id"))
                    elif action == "done":
                        T.done(project, slug, actor=config.OPERATOR_ACTOR)
                    elif action == "stop":
                        request_daemon_task_operation(project, slug, "stop", reason, actor=config.OPERATOR_ACTOR,
                                                      generation=o.get("generation"))
                    elif action == "dispatch":
                        spawn(f"dispatch:{project}", dispatch_waiting, project)
                    else:
                        return self._json({"error": f"unknown task action {action}"}, 400)
                except T.TransitionError as exc:
                    return self._json({"error": str(exc)}, 409)
                return self._json({"ok": True, "state": S.load_task(project, slug)["state"]})
            if api == "l2" and len(parts) > 2 and parts[2] == "send-now":
                project, slug = o["project"], o["slug"]
                try:
                    return self._json(dispatch.request_send_now(project, slug, str(o.get("id") or "")))
                except T.TransitionError as exc:
                    return self._json({"error": str(exc)}, 409)
            if api == "l2" and len(parts) > 2 and parts[2] == "remove":
                try:
                    T.remove_message(o["project"], o["slug"], str(o.get("id") or ""))
                except T.TransitionError as exc:
                    return self._json({"error": str(exc)}, 409)
                return self._json({"ok": True})
            if api == "l2" and len(parts) > 2 and parts[2] == "message":
                project, slug = o["project"], o["slug"]
                text = str(o.get("text") or "").strip()
                image_args = self._image_request(o) if o.get("images") or o.get("image_ids") else {}
                if not text and not image_args:
                    return self._json({"error": "empty task message"}, 400)
                if image_args:
                    existing = next((row for row in T.task_messages(project, slug)
                                     if row["id"] == image_args["request_id"]), None)
                    if not existing:
                        require_image_capability(project, slug)
                elif "request_id" in o:
                    try:
                        image_args = {"request_id": uuid.UUID(o["request_id"]).hex}
                    except (ValueError, TypeError, AttributeError):
                        return self._json({"error": "Task messages require a valid submission identity."}, 400)
                try:
                    message = T.message(project, slug, T.OPERATOR_MESSAGE_ROLE, text,
                                        question_id=o.get("question_id"), revision=o.get("revision"),
                                        group_id=o.get("group_id"), group_revision=o.get("group_revision"),
                                        stop_id=o.get("stop_id"), review_number=o.get("review_pr"),
                                        review_hold_id=o.get("review_hold_id"), **image_args)
                except T.TransitionError as exc:
                    return self._json({"error": str(exc)}, 409)
                try:
                    if S.load_task(project, slug).get("state") == "blocked":
                        request_task_resume(project, slug)
                except Exception as exc:  # #298: acceptance is durable; the timer retries its saved resume request.
                    log(f"[{project}/{slug}] message wake deferred: {exc}")
                message["delivery"] = {"state": "queued", "at": None, "removable": False}
                return self._json({"ok": True, "message": message})
            if parts == ["api", "alerts", "subscription"]:
                try:
                    endpoint = str(o.get("endpoint") or "")
                    return self._json(push.forget(endpoint) if o.get("remove")
                                      else push.subscribe(endpoint, o.get("every", 0)))
                except push.PushFailure as exc:
                    return self._json({"error": str(exc)}, 400)
            if parts == ["api", "alerts", "pace"]:
                try:
                    return self._json(push.pace(str(o.get("endpoint") or ""), o.get("every")))
                except push.PushFailure as exc:
                    return self._json({"error": str(exc)}, 400)
            if api == "l3" and len(parts) > 2 and parts[2] == "reset":
                l3.reset(o["project"], "reset from the page"); return self._json({"ok": True})
            if api == "l3" and len(parts) > 2 and parts[2] == "start":
                name = config.project(o["project"]) and o["project"]
                return self._json({"ok": True, "started": spawn(f"start:{name}", start_l3, name)})
            if api == "chat" and len(parts) > 2 and parts[2] == "send-now":
                project = o["project"]
                try:
                    result = l3.send_now(project, str(o.get("id") or ""))
                except ValueError as exc:
                    return self._json({"error": str(exc)}, 409)
                request_l3_drain(project)
                return self._json(result)
            if api == "chat" and len(parts) > 2 and parts[2] == "remove":
                project = o["project"]
                if not l3.drop_queued(project, str(o.get("id") or "")):
                    return self._json({"error": "that message has already started"}, 409)
                return self._json({"ok": True, "queued": l3.queued(project)})
            if api == "chat":
                project, text = o["project"], (o.get("text") or "").strip()
                if not config.is_managed(project):
                    return self._json({"error": "This project is not managed. Add its folder again to attach L3."}, 409)
                if "offer_turn" in o:
                    # Create task under a reply (SPEC.md §3.3): Altitude writes the message; the page sends no text.
                    try:
                        result = l3.queue_offer(project, str(o.get("offer_turn") or ""))
                    except ValueError as exc:
                        return self._json({"error": str(exc)}, 409)
                    request_l3_drain(project)
                    return self._json(result)
                image_args = self._image_request(o) if o.get("images") or o.get("image_ids") else {}
                if not text and not image_args:
                    return self._json({"error": "empty"}, 400)
                # The page's send id rides on the stored message or queue row, so its bubble reconciles by identity.
                send_id = {}
                if not image_args and "request_id" in o:
                    try:
                        send_id = {"request_id": uuid.UUID(o["request_id"]).hex}
                    except (ValueError, TypeError, AttributeError):
                        return self._json({"error": "Chat messages require a valid submission identity."}, 400)
                # A follow-up from a decision page names the decision's task (SPEC.md §5.2 note 6).
                try:
                    slug = S.require_task_slug(o["slug"]) if o.get("slug") else None
                except ValueError as exc:
                    return self._json({"error": str(exc)}, 400)
                if image_args:
                    try:
                        with S.project_lock(project):
                            existing = l3.image_receipt(project, image_args["request_id"], image_args["request_digest"])
                        if not existing:
                            require_image_capability(project)
                        row = l3.queue_message(project, text, trigger="chat", role=T.OPERATOR_MESSAGE_ROLE,
                                               slug=slug, **image_args)
                    except images.ImageError:
                        raise
                    except ValueError as exc:
                        return self._json({"error": str(exc)}, 409)
                    request_l3_drain(project)
                    return self._json({"queued": row, "accepted": True})
                if l3.busy(project) or config.restart_in_progress():
                    # The operator types faster than L3 answers. The message waits for the turn boundary in the
                    # durable queue instead of bouncing off a busy L3; the running turn drains it there.
                    try:
                        row = l3.queue_message(project, text, trigger="chat", role=T.OPERATOR_MESSAGE_ROLE, slug=slug,
                                               **send_id)
                    except ValueError as exc:
                        return self._json({"error": str(exc)}, 409)
                    request_l3_drain(project)
                    return self._json({"queued": row})
                self._stream_open()
                gone: list[BaseException] = []

                def emit(event: dict) -> None:
                    # The turn owns its answer, not the page that started it. 2026-09-03 07:54Z: the operator refreshed
                    # Chat mid-turn; the write error unwound the turn, the answer was never logged and the
                    # session bookkeeping was skipped. A lost client ends the stream and nothing else.
                    if gone:
                        return
                    try:
                        self._stream_send(event)
                    except OSError as e:
                        gone.append(e)
                        log(f"POST {self.path}: client went away mid-turn ({type(e).__name__}: {e}); the turn continues")

                def send(t: str) -> None:
                    emit({"t": t})

                def started(_pid) -> None:
                    # The page keys its pending bubble on the turn id from here on, so a poll that already
                    # shows the server's own rows for this turn never doubles them (SPEC.md §4.2).
                    emit({"turn": l3.active(project)})

                def split(text: str, delivery: dict) -> None:
                    # A Send now message the turn took in ends the reply so far; the reply continues under its turn.
                    emit({"turn": l3.active(project), "user": text, "delivery": delivery})

                over = threading.Event()

                def watch() -> None:
                    # A device removed mid-turn loses its open answer within a second, even while the turn is
                    # silent; the turn itself goes on.
                    while not over.wait(CHANGE_SECONDS):
                        if not self._still_admitted():
                            gone.append(PermissionError("device revoked"))
                            try:
                                self.connection.shutdown(socket.SHUT_RDWR)
                            except OSError:
                                pass
                            return

                threading.Thread(target=watch, daemon=True).start()
                try:
                    res = server_l3_turn(project, text, trigger="chat", on_text=send, on_start=started, on_split=split,
                                         **({"slug": slug} if slug else {}), **send_id)
                finally:
                    over.set()
                if gone:
                    return
                self._stream_send({"queued": res["queued"]} if res.get("queued") else {"done": {k: res.get(k) for k in (
                    "session_id", "context_percent", "turns", "cost", "error", "engine", "turn_id")}})
                self._stream_close()
                return
            if api == "restart":
                if platform.containerized():
                    return self._json({"error": platform.IMAGE_MANAGED}, 409)
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
        except images.ImageError as exc:
            return self._json({"error": str(exc)}, exc.status)
        except (ssl.SSLError, BrokenPipeError, ConnectionResetError) as e:  # the client left mid-response (a phone's audio player, a closed tab): not a fault
            log(f"POST {self.path}: client went away ({type(e).__name__}: {e})")
            return
        except Exception as e:  # noqa: BLE001
            log(f"POST {self.path}: {e}\n{traceback.format_exc()}")
            try:
                self._json({"error": "Could not confirm image send. Retry this submission." if image_submission else self._detail(e)}, 500)
            except Exception:  # noqa: BLE001
                pass


def restart_status() -> dict | None:
    """Pending backend/web activation plus what the page's early Restart button waits for."""
    if platform.containerized() or config.RELEASE is not None:
        return None  # Installed archives activate only through the explicit update transaction.
    pending = S.read_json(config.MONITOR_DIR / dispatch.RESTART_PENDING)
    if not pending:
        return None
    return {**pending, "waiting_for": restart_waiting_for()}


def restart_waiting_for(*, check_activity: bool = True) -> list[str]:
    projects = list(config.load_projects())
    waiting = [f"{p}/{t['slug']}" for p in projects for t in S.list_tasks(p)
               if t.get("dispatching") or t.get("resume_claim")]
    waiting += [f"{p} L3" for p in projects if l3.busy(p)]
    waiting += [f"adversarial review for {task}" for task in reviews.running()]
    if held := validation.holder():
        waiting.append(f"validation run for {held}")
    if check_activity and not waiting:
        with config.restart_lock(exclusive=True) as quiet:
            if not quiet:
                waiting.append("dispatch, L3 turn or report verification in flight")
    return waiting


RESTART_GRACE_SECONDS = 600  # the restart unit builds the web bundle first; the old process is gone well within this


def auto_restart() -> None:
    """Activate merged backend or web changes at the quiet point (operator, 2026-09-03: a merged fix is not a fix
    until the deployed service and bundle contain it). Activation: dispatch, L3, review, validation and report handling
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
                  f"{RESTART_GRACE_SECONDS // 60} minutes; see {platform.job_logs_hint(str(pend.get('unit')))}")
        log(f"auto-restart: {detail}; new dispatches resume")
        incidents.system_fault("restart", detail)
        return
    if status["waiting_for"]:
        return
    retry_at = pend.get("retry_at")
    if retry_at and datetime.fromisoformat(retry_at) > datetime.now(timezone.utc):
        return  # a transient refusal pauses before its retry (I-20261010-214654)
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


def auto_update() -> None:
    """Install the offered release when automatic updates are on (operator, 2026-10-09: a fix should not wait for
    every installation to update by hand). While it is offered, new validation, review and improvement runs wait
    (`config.activation_pending`), so those in flight drain; then the request fences dispatch, resume, L3 and report
    work, and the detached update activates once what runs has finished (#1003: waiting for an instant with nothing
    in flight lost to back-to-back work). The restart ends an open terminal, so one defers it; detached workers
    survive it. Each version is tried once."""
    if platform.containerized() or config.RELEASE is None:
        return
    version = installation.automatic_update()
    if not version:
        return
    with terminal.held_open() as terminals:
        waiting = [f"the browser terminal open in {where}" for where in terminals]
        waiting += [f"the adversarial review of {task}" for task in reviews.running()]
        waiting += [f"the improvement review of {project}" for project in config.load_projects() if improve.reading(project)]
        if held := validation.holder():
            waiting.append(f"the validation run of {held}")
        if not waiting:
            try:
                installation.request_update(version, automatic=True)
            except installation.UpdateRefused:
                return   # `alt update`, recovery or activation already runs
    installation.record_update_wait(version, waiting)
    if not waiting:
        log(f"installing Altitude {version} automatically once work in flight finishes")


def restart_service() -> dict:
    platform.require_native_application()
    if config.RELEASE is not None:
        raise RuntimeError("Installed releases use alt update; source activation is unavailable")
    with config.restart_lock(exclusive=True) as quiet:
        if not quiet or restart_waiting_for(check_activity=False):
            raise RestartBusy("restart waits for dispatch, L3 turn, adversarial review, validation or report verification")
        flag = config.MONITOR_DIR / dispatch.RESTART_PENDING
        pend = S.read_json(flag, {}) or {}
        if pend.get("requested_at") and not pend.get("failed"):
            return {"ok": True, "unit": pend.get("unit")}
        # Publish before releasing the gate, including the manual button path: no launch can race the unit.
        res = _request_restart_unit()
        if pend.pop("failed", None):
            pend.pop("retries", None)  # a parked activation requested again gets its full retry bound
        pend.pop("retry_at", None)
        pend.update({"requested_at": S.now(), "unit": res["unit"]})
        S.write_json(flag, pend)
        return res


def _request_restart_unit() -> dict:
    """Run the operator restart script as a detached job: outside altd's own, it survives the restart."""
    unit = f"altitude-restart-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    cmd = platform.detached_job_command(unit, [sys.executable, str(config.SOURCE / "scripts" / "restart_altitude.py")],
                                        path=os.environ.get("PATH", ""))
    res = subprocess.run(cmd, cwd=str(config.REPO), capture_output=True, text=True, timeout=30)
    if res.returncode != 0:
        raise RuntimeError(f"the service manager refused the restart unit: {(res.stderr or res.stdout).strip()[:300]}")
    log(f"guarded activation requested → unit {unit}; follow it with: {platform.job_logs_hint(unit)}")
    return {"ok": True, "unit": unit}


CHANGE_SECONDS = 1.0
CHANGE_KEEPALIVE_SECONDS = 15.0
# How long a terminal stream waits for output before checking that its page is still connected.
TERMINAL_WAIT_SECONDS = 1.0
CHANGE_RETRY_MS = 3000


#: The change stream's key for repairs, which no project name can take; it refreshes the shell, not a project.
REPAIRS_MARK = " repairs"


def repair_marks(mark) -> tuple:
    """A repair's record moves the overview: the one running now, and the newest one, whose questions Needs you shows."""
    newest = max(repair.home().glob("*/repair.json"), key=lambda path: path.parent.stat().st_ctime, default=None) \
        if repair.home().is_dir() else None
    return mark(repair.home() / "running.json"), newest and mark(newest)


def change_marks() -> dict[str, tuple]:
    """What `/api/changes` compares each second: identity, size and modification time of the registry ("") and,
    per registered project, its hold, project log, archive folder, every open task's status (which
    holds its questions) and event log, and every agent chat's record. Writers are the daemon and `alt` processes alike, so the files
    are the signal."""
    def mark(path: Path):
        try:
            st = path.stat()
        except OSError:  # absent, or archived between listing and reading
            return None
        return st.st_ino, st.st_mtime_ns, st.st_size  # atomic replacement always changes the inode

    marks: dict[str, tuple] = {"": (mark(config.PROJECTS_FILE),), REPAIRS_MARK: repair_marks(mark)}
    for project in config.load_projects():
        d = config.project_dir(project)
        rows = [mark(d / "hold.json"), mark(d / "events.log"), mark(S.archive_dir(project))]
        rows += [(td.name, mark(td / "status.json"), mark(td / "events.log")) for td in sorted(S.tasks_dir(project).glob("*"))]
        rows += [("chat", path.parent.name, mark(path)) for path in sorted(chats.home(project).glob("*/chat.json"))]
        marks[project] = tuple(rows)
    return marks


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
    # Improvement suggestions waiting for the operator: listed after Needs you's questions, never counted with them.
    # Each project's cards, the Weekly review's and its agents' runs alike, newest first.
    waiting = [{"project": p["name"], **card} for p in projects if p["managed"]
               for card in sorted([*improve.waiting(p["name"]), *project_agents.waiting(p["name"])],
                                  key=lambda card: card["posted"], reverse=True)]
    return {"projects": projects, "queue": digest.queue(), "suggestions": waiting, "wip": digest.wip(), "quota": monitor.quota(),
            "lifecycle": platform.container_lifecycle(),
            "deployment": "container" if platform.containerized() else "native",
            "engines": route.engine_readouts(), "new_tasks": new_tasks_view(),
            "roots": [home_relative(r) for r in config.project_roots()],
            "operator": config.operator_name(), "restart": restart_status(),
            "update": installation.update_status(), "repair": repair.attention(), "now": S.now()}


CHANGED_ELSEWHERE = "Changed in another window."


def reset_project_model_setting(body: dict) -> dict:
    """Clear one project's own model and effort for a role and engine, as Settings' Reset asks."""
    if "expected" not in body:
        raise ValueError("the previously read model and effort are required")
    row = next((row for row in config.project_model_settings() if row["id"] == body.get("id")), None)
    if row is None:
        return {"changed": True}
    if not row["editable"]:
        raise ValueError("Explicit routing models are edited with the project routing setting.")
    project, setting = json.loads(row["id"])
    dispatch.request_setting(project, setting, None, "Settings", actor=config.OPERATOR_ACTOR, expected=body["expected"])
    result = dispatch._run_setting(project, setting)
    if result.get("changed"):
        return result
    if result.get("status") != "done":
        raise ValueError(result.get("note") or "Setting was not saved.")
    if l3.queue_path(project).exists():
        request_l3_drain(project)
    return result


def new_tasks_view() -> dict:
    """The New tasks choice beside the quota: its value, why it cannot start now, and the projects whose
    Only engine keeps their tasks elsewhere."""
    choice = config.machine_settings().get("new_tasks")
    return {"value": choice, "unavailable": route.choice_unavailable("l2", choice),
            "only": [{"project": name, "engine": entry["l2_engine"]}
                     for name, entry in sorted(config.load_projects().items()) if entry.get("l2_engine")],
            **config.choice_options()}


def home_relative(path: Path) -> str:
    """A folder as First run names it: `~/Projects`, never the whole home path."""
    try:
        relative = path.expanduser().relative_to(config.HOME)
        return "~/" + relative.as_posix() if relative.parts else "~"
    except ValueError:
        return str(path)


class FolderError(ValueError):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def folders(raw: str | None) -> dict:
    """List one held folder; intermediate aliases may leave HOME safely, final browsing stays inside."""
    try:
        with platform.inspected_path(platform.CONTAINER_PROJECTS if platform.containerized() else config.HOME,
                                     limit=None, real=False, open_leaf=False) as anchor:
            home = anchor.address
            target = Path(raw).expanduser() if raw else home
            if not target.is_absolute():
                raise FolderError("Choose an absolute folder path.", 400)
            with platform.inspected_path(target, limit=platform.folder_link_limit(normalize=True),
                                         real=False, open_leaf=False) as inspected:
                target = inspected.address
                if not target.is_relative_to(home) or any(p.startswith(".") for p in target.relative_to(home).parts):
                    raise FolderError("Choose a folder inside the container projects volume." if platform.containerized()
                                      else "Browsing stays inside your home folder. Type the path to add a folder elsewhere.", 403)
                if not platform.directory_type(inspected):
                    raise FolderError("This folder no longer exists.", 404)
                view = {"path": str(target), "parts": list(target.relative_to(home).parts), "readable": True,
                        "folders": [], **({"location": "container"} if platform.containerized() else {})}
                try:
                    with platform.directory_listing(inspected) as directory:
                        names = sorted(os.listdir(directory.fd), key=str.lower)
                        managed = config.managed_addresses(config.load_projects())
                        for name in names:
                            if name.startswith("."):
                                continue
                            try:
                                # Browser parent is canonical; the child operation resets its alias budget.
                                with platform.inspected_child(directory, name, inherited_links=0,
                                                              open_leaf=False) as child:
                                    if not platform.directory_type(child):
                                        continue
                                    resolved = child.address
                                    if resolved.is_relative_to(home) and not any(p.startswith(".") for p in resolved.relative_to(home).parts):
                                        view["folders"].append({"name": name, "path": str(target / name),
                                                                "project": managed.get(str(resolved)),
                                                                "git": platform.git_marker(child)})
                            except (OSError, ValueError):
                                continue
                except PermissionError:
                    return {**view, "readable": False}
                return view
    except FolderError:
        raise
    except platform.PrivatePath:
        raise FolderError(config.PRIVATE_FOLDER, 403) from None
    except (OSError, ValueError):
        raise FolderError("This folder no longer exists.", 404) from None


def save_projects_folder(body: dict) -> dict:
    """Apply the operator's projects folder through the same durable request as `alt machine set`."""
    if body.keys() - {"path"}:
        raise ValueError("Unsupported projects folder fields.")
    path = body.get("path")
    value = None
    if isinstance(path, str) and path.strip():
        expanded = os.path.expanduser(Path(path))
        if expanded.startswith("~"):
            raise RuntimeError("Could not determine home directory.")
        value = str(Path(expanded))
    _save_machine("projects_folder", value, "Projects folder")
    return {"roots": [home_relative(r) for r in config.project_roots()]}


def machine_view() -> dict:
    """The operator's name, incident publication, the terminal, validation and update-check switches, as First run and
    Settings show them."""
    try:
        network = {**tls.address_view(_located()), "state": "running"}
    except tls.TLSFailure as exc:
        network = {"error": str(exc)}  # only the Network card reports it; the rest of the machine view still loads
    return {"lifecycle": platform.container_lifecycle(), "operator": config.operator_name(), "incident_repository": config.incident_repository(),
            "network": network,
            "altitude_repository": config.ALTITUDE_REPOSITORY, "terminal": terminal.enabled(),
            "terminal_unavailable": platform.container_unavailable("Terminal"),
            "container_shell": platform.container_shell_command(),
            "validation": validation.enabled(), "validation_unavailable": platform.validation_unavailable(),
            "deployment": "container" if platform.containerized() else "native",
            "update_check": not platform.containerized() and config.machine_settings().get("update_check") is not False,
            "update_automatic": not platform.containerized() and config.machine_settings().get("update_automatic") is not False}


def _save_machine(setting: str, value, reason: str) -> dict:
    dispatch.request_setting(None, setting, value, reason, actor=config.OPERATOR_ACTOR)
    result = dispatch._run_setting(None, setting)
    if result["status"] != "done":
        raise ValueError(result["note"])
    return machine_view()


def save_operator_name(body: dict) -> dict:
    if body.keys() - {"name"}:
        raise ValueError("Unsupported name fields.")
    name = body.get("name")
    value = " ".join(name.split()) or None if isinstance(name, str) else name
    return _save_machine("operator_name", value, "Operator name")


def save_incident_reports(body: dict) -> dict:
    """Turn incident publication off, or on for a GitHub repository the signed-in GitHub CLI can see."""
    if body.keys() - {"repository"}:
        raise ValueError("Unsupported incident report fields.")
    repository = body.get("repository")
    if repository is None:
        return _save_machine("incident_repository", False, "Incident reports off")
    if not isinstance(repository, str):
        raise ValueError("Name a GitHub repository as owner/name.")
    text = repository.strip()
    url = repository_url(text) or (repository_url("https://github.com/" + text) if "/" in text else None)
    if not url:
        raise ValueError("Name a GitHub repository as owner/name, for example your fork of Altitude.")
    name = url.removeprefix("https://github.com/")
    try:
        seen = subprocess.run(["gh", "repo", "view", name, "--json", "nameWithOwner"], capture_output=True,
                              text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"Could not check {name} with the GitHub CLI: {exc}") from exc
    if seen.returncode != 0:
        raise ValueError(f"The signed-in GitHub CLI cannot see {name}. Check the name, or sign in with gh auth login.")
    return _save_machine("incident_repository", name, "Incident reports on")


def repository_url(origin: str) -> str | None:
    """The GitHub web URL for an HTTPS or SSH origin, otherwise None."""
    match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
                         r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?", origin.strip(), re.I)
    return f"https://github.com/{match[1]}/{match[2]}" if match else None


MACHINE_COMMAND_LIMIT = 16384
MACHINE_POLL_SECONDS = 1


def _machine_rows(runs: Path) -> list[dict]:
    return [json.loads(line) for line in runs.read_text().splitlines() if line.strip()] if runs.exists() else []


MACHINE_LINE = line.Line(config.MACHINE_RUNS, "a machine-run place")   # concurrent `alt task run` commands
MACHINE_REVOKED = "the grant was revoked while this ran, so Altitude stopped it; effects it already had remain"
MACHINE_CLIENT_GONE = f"{line.CLIENT_GONE}, so Altitude stopped it; effects it already had remain"
_client_stopped: dict[str, str] = {}   # unit → why a client awaiting a command from before a restart stopped it


def run_machine_command(project: str, slug: str, attempt: object, command: object, request: object, *,
                        owner=lambda task: False, waiting=lambda text: None, gone=lambda: False) -> dict:
    """One command under the task's recorded operator grant, executed by altd outside the worker sandbox.

    Only the running owner's current attempt may call it, from its own worker job (`owner(task)`), so another agent
    holding this machine's key cannot run commands under this task's grant, and only while a grant is recorded. The
    command, unit, exit status and output land in the task folder (`machine.jsonl` and the unit's own log), the task
    events and the project log, so the operator can read exactly what ran under their grant. `request` names the caller's
    command: calling again with it, after a restart ended the connection, waits for that command's result
    instead of running it again. A command that finds every machine-run place taken waits its turn, telling
    `waiting(text)` what it waits for; `gone()` says its client stopped, which takes it out of the line or stops it.
    """
    S.require_task_slug(slug)
    if not isinstance(command, str) or not command.strip() or len(command) > MACHINE_COMMAND_LIMIT:
        raise ValueError(f"alt task run: supply one non-empty command of at most {MACHINE_COMMAND_LIMIT} characters")
    if not isinstance(request, str) or not re.fullmatch(r"[0-9a-f]{32}", request):
        raise ValueError("alt task run: name the request with 32 lowercase hexadecimal characters")
    folder = S.task_dir(project, slug)
    runs = folder / "machine.jsonl"

    def admit(start: bool) -> tuple[dict, dict | None, dict | None]:
        """The task, the request's earlier row and, when `start`, the row it records; refuses a request that may
        not run."""
        with S.project_lock(project):
            task = S.load_task(project, slug)
            if task.get("state") != "running" or str(task.get("attempt")) != str(attempt):
                raise PermissionError("alt task run: only the running owner's current attempt may run machine commands")
            if not owner(task):
                raise PermissionError("alt task run: only this task's owner may run its granted commands")
            rows = _machine_rows(runs)
            earlier = next((r for r in rows if r.get("request") == request), None)
            if earlier is not None and earlier.get("attempt") != task.get("attempt"):
                raise PermissionError("alt task run: this request belongs to an earlier attempt")
            if earlier is not None:
                if earlier["command"] != command:
                    raise ValueError("alt task run: this request already ran a different command")
                return task, earlier, None
            grant = task.get("grant")
            if grant and T.grant_withdrawn(project, grant):
                raise PermissionError("alt task run: the operator withdrew the approval this grant rests on")
            if not grant:
                raise PermissionError("alt task run: this task has no operator grant; ask the operator for access for "
                                      "a concrete purpose, resolve their answer, then record it with "
                                      "alt task grant")
            if grant.get("attempt") != task.get("attempt"):
                raise PermissionError("alt task run: the operator grant belongs to an earlier attempt; ask again")
            if any(r["finished"] is None for r in rows):  # one at a time keeps the record readable
                raise ValueError("alt task run: one command at a time; the previous command is still running")
            if not start:
                return task, None, None
            # The row exists before the unit starts, so a command that restarts altd keeps its number and unit.
            sequence = len(rows) + 1
            row = {"n": sequence, "request": request, "attempt": task.get("attempt"), "purpose": grant["purpose"],
                   "granted": grant["id"], "command": command,
                   "unit": engines.machine_unit(project, slug, sequence), "exit": None, "timed_out": False,
                   "started": datetime.now(timezone.utc).isoformat(), "finished": None,
                   "error": "still running or interrupted with altd"}
            T._append_jsonl(runs, row)
            return task, None, row

    task, earlier, _ = admit(False)
    if earlier is None:
        with MACHINE_LINE.turn(f"the command of {project}/{slug} holds a place", config.MACHINE_COMMAND_TIMEOUT,
                               command="alt task run", wait=config.MACHINE_COMMAND_WAIT, watch=MACHINE_POLL_SECONDS,
                               waiting=waiting, gone=gone):
            task, earlier, row = admit(True)   # the task, its grant and its commands may have changed while it waited
            if row is not None:
                return _start_machine_command(project, slug, task, row, gone)
    return _await_machine_row(project, slug, earlier["n"], gone)


def _start_machine_command(project: str, slug: str, task: dict, row: dict, gone) -> dict:
    """Run the command whose row was just recorded and settle it, stopping it when its grant is revoked or its client
    goes away."""
    folder, finished, stopped = S.task_dir(project, slug), threading.Event(), []

    def stop_when_revoked() -> None:  # the launcher waits for the job, so revocation and the client are watched beside it
        while not finished.wait(MACHINE_POLL_SECONDS):
            if not stopped and _grant_revoked(project, slug, row):
                stopped.append(MACHINE_REVOKED)
            elif not stopped and gone():
                stopped.append(MACHINE_CLIENT_GONE)
            if stopped:
                engines.machine_stop(row["unit"])  # again each poll: the job may not exist yet, or a stop may fail

    watcher = threading.Thread(target=stop_when_revoked, name="machine-client", daemon=True)
    watcher.start()
    try:  # a launch that fails still settles its row, so it never holds the next command
        launch_error = engines.machine_command(row["command"], folder=folder, unit=row["unit"],
                                               cwd=config.task_worktree(project, slug) if task.get("worktree") else config.project_path(project),
                                               timeout=config.MACHINE_COMMAND_TIMEOUT,
                                               identity=dispatch.l2_env(project, slug, task["attempt"]))
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        launch_error = str(exc)[:300]
    finally:
        finished.set()
        watcher.join()   # it reads the client's connection, which the answer is written to next
    return settle_machine_command(project, slug, row, watched=True, launch_error=launch_error,
                                  stopped=stopped[0] if stopped else None)


def _grant_revoked(project: str, slug: str, row: dict) -> bool:
    """The grant a command runs under is no longer the task's current grant."""
    try:
        grant = S.load_task(project, slug).get("grant") or {}
        return grant.get("id") != row.get("granted") or T.grant_withdrawn(project, grant)
    except (OSError, ValueError):
        return False


def settle_machine_command(project: str, slug: str, row: dict, *, watched: bool,
                           launch_error: str | None = None, stopped: str | None = None) -> dict:
    """Follow the row's unit to its end and complete its row, task event and project log entry once; return the
    completed row with the unit's output. The altd that started the command settles it, and the next altd settles
    one that a restart interrupted, stopping it if its grant was revoked meanwhile; `stopped` says why its altd
    already stopped it. The row is written last, so a
    restart between the writes settles it again, and the task event, found by unit, is not repeated."""
    folder = S.task_dir(project, slug)
    outcome = engines.machine_outcome(folder, row["unit"], row["started"], watched=watched,
                                      timeout=config.MACHINE_COMMAND_TIMEOUT, launch_error=launch_error,
                                      poll=MACHINE_POLL_SECONDS, stopped=stopped,
                                      stop=lambda: MACHINE_REVOKED if _grant_revoked(project, slug, row)
                                      else _client_stopped.pop(row["unit"], None))
    runs = folder / "machine.jsonl"
    with S.project_lock(project):
        rows = _machine_rows(runs)
        current = next(r for r in rows if r["n"] == row["n"])
        if current["finished"] is None:
            current.update(outcome)
            if not any(e["kind"] == "machine-run" and e.get("unit") == current["unit"]
                       for e in S.read_events(project, slug)):
                S.project_log(project, "machine-run", slug=slug, command=current["command"], unit=current["unit"],
                              exit=current["exit"], timed_out=current["timed_out"])
                S.append_event(project, slug, "machine-run", actor="l2", **current)
            S.atomic_write(runs, "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    return {**current, **engines.machine_output(folder, current["unit"])}


def _await_machine_row(project: str, slug: str, sequence: int, gone=lambda: False) -> dict:
    """The row once it is complete, or as it stands when the command's limit has long passed. A client that goes
    away meanwhile has the command stopped by whoever settles it, with that reason."""
    folder = S.task_dir(project, slug)
    deadline = time.monotonic() + config.MACHINE_COMMAND_TIMEOUT + 90
    while True:
        row = next(r for r in _machine_rows(folder / "machine.jsonl") if r["n"] == sequence)
        if row["finished"] is not None or time.monotonic() >= deadline:
            return {**row, **engines.machine_output(folder, row["unit"])}
        if gone():
            _client_stopped[row["unit"]] = MACHINE_CLIENT_GONE
            engines.machine_stop(row["unit"])
            gone = lambda: False   # noqa: E731 — stop once; the settler records the end
        time.sleep(MACHINE_POLL_SECONDS)


def settle_interrupted_machine_commands() -> list[threading.Thread]:
    """Follow every machine command a stopped altd left running, so its row and event get its real result. A
    ledger that cannot be read is logged and left as it is; it never stops altd from starting."""
    threads = []
    for project in config.load_projects():
        for runs in [*S.tasks_dir(project).glob("*/machine.jsonl"), *S.archive_dir(project).glob("*/machine.jsonl")]:
            try:
                # the validation runner records its own interrupted runs (validation.reconcile)
                rows = [row for row in _machine_rows(runs) if row["finished"] is None
                        and not row["unit"].startswith(validation.UNIT_PREFIX)]
            except (OSError, ValueError, KeyError, TypeError) as exc:
                log(f"machine commands: cannot read {runs}: {exc}")
                continue
            for row in rows:
                # It keeps a place, beyond the line's places if need be, taken before altd serves any request.
                held = ExitStack()
                place = held.enter_context(MACHINE_LINE.turn(f"the command of {project}/{runs.parent.name} holds a "
                                                             "place", None, command="alt task run", force=True))
                MACHINE_LINE.ends(place, config.MACHINE_COMMAND_TIMEOUT - int(
                    (datetime.now(timezone.utc) - datetime.fromisoformat(row["started"])).total_seconds()))
                thread = threading.Thread(target=_settle_interrupted, args=(project, runs.parent.name, row, held),
                                          name=f"machine-{row['unit']}", daemon=True)
                thread.start()
                threads.append(thread)
    return threads


def _settle_interrupted(project: str, slug: str, row: dict, held: ExitStack) -> None:
    """Settle a command that runs on from before a restart, then free the machine-run place it `held`."""
    with held:
        settle_machine_command(project, slug, row, watched=False)


def worker_connection(peer: tuple, local: tuple) -> bool:
    """Whether a process in the current worker job of some open task holds this connection: decided before its body
    names a task, so only a worker's request is read past the ordinary bound."""
    for project in config.load_projects():
        for task in S.list_tasks(project):
            try:
                if task["state"] in S.OPEN_STATES and task_owner_connection(project, task["slug"], task, peer, local):
                    return True
            except (KeyError, TypeError, ValueError, OSError, RuntimeError):  # RuntimeError: no worker record
                continue
    return False


def task_owner_connection(project: str, slug: str, task: dict, peer: tuple, local: tuple) -> bool:
    """Whether this connection comes from a process in the task's current worker job, so another agent holding this
    machine's key cannot act as the owner."""
    return bool(task.get("agent_id")) and terminal.owner_connection(
        peer, local, engines.worker_unit(task["agent_id"], job_root=dispatch.l2_job_root(project, slug)))


def owner_terminal_output(project: str, slug: str, attempt: object, peer: tuple, local: tuple) -> dict:
    """The task terminal's output for the task's running owner: read-only, and only to a connection from a process in
    that owner's current worker job, so another agent holding this machine's key cannot read it."""
    unavailable = platform.container_unavailable("Terminal")
    if unavailable:
        raise PermissionError(unavailable)
    S.require_task_slug(slug)
    task = S.load_task(project, slug)
    if task.get("state") != "running" or str(task.get("attempt")) != str(attempt) or not task.get("agent_id"):
        raise PermissionError("alt task terminal: only the running owner's current attempt may read its terminal")
    if not task_owner_connection(project, slug, task, peer, local):
        raise PermissionError("alt task terminal: only this task's owner may read its terminal")
    return terminal.output(project, slug)


def coordinator_terminal_output(project: str) -> dict:
    """The project terminal's output for the project's coordinator. Only the project-bound coordinator socket calls
    this, so neither a task worker nor another project's coordinator can read it."""
    unavailable = platform.container_unavailable("Terminal")
    if unavailable:
        raise ValueError(unavailable)
    return terminal.output(project, None)


def pr_close(project: str, number: int, *, actor: str, body: str = "") -> dict:
    """Close an explicitly selected project PR; verify state without deleting its branch."""
    if actor not in ("l3", "operator"):
        raise ValueError("alt pr close: only L3 and the operator may close PRs")
    if type(number) is not int or number < 1 or body != "":
        raise ValueError("alt pr close: a positive PR number is required; no body is accepted")
    checkout = config.project_path(project)
    origin = subprocess.run(["git", "remote", "get-url", "origin"], cwd=checkout,
                            capture_output=True, text=True, timeout=10, env=git_policy.git_env(checkout))
    repository = repository_url(origin.stdout) if origin.returncode == 0 else None
    if not repository:
        raise ValueError("alt pr close: checkout origin must identify a GitHub repository")
    url = f"{repository}/pull/{number}"
    env = engines.clean_env()
    env.pop("GH_REPO", None)

    def read_state() -> str:
        try:
            result = subprocess.run(["gh", "pr", "view", str(number), "--repo", repository,
                                     "--json", "number,url,state"], cwd=checkout, env=env,
                                    capture_output=True, text=True, timeout=30)
            record = json.loads(result.stdout) if result.returncode == 0 else None
            if (isinstance(record, dict) and type(record.get("number")) is int and record["number"] == number
                    and isinstance(record.get("url"), str) and record["url"].lower() == url.lower()
                    and record.get("state") in ("OPEN", "CLOSED", "MERGED")):
                return record["state"]
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
        raise ValueError(f"alt pr close: PR state unconfirmed at {url}; inspect access and state before retrying")

    before = read_state()
    if before == "OPEN":
        try:
            subprocess.run(["gh", "pr", "close", str(number), "--repo", repository], cwd=checkout,
                           env=env, input="", capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError, UnicodeError):
            pass  # A failed or timed-out write can still have reached GitHub; the read owns the verdict.
        after = read_state()
        if after == "OPEN":
            raise ValueError(f"alt pr close: PR remains open at {url}; inspect GitHub access before retrying")
    else:
        after = before
    result = {"number": number, "url": url, "state": after,
              "outcome": "merged" if after == "MERGED" else "already-closed" if before == "CLOSED" else "closed"}
    with S.project_lock(project):
        S.project_log(project, "pr-close", actor=actor, **result)
    if after == "CLOSED":
        for task in S.list_tasks(project):
            T.record_pr_state(project, task["slug"], number, after, by=actor)
    return result


ISSUE_CLOSE_REASONS = ("completed", "not-planned")


def issue_parser() -> argparse.ArgumentParser:
    """One exact grammar for the operator CLI and the project-bound L3 socket."""
    class Parser(argparse.ArgumentParser):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **{**kwargs, "allow_abbrev": False})

        def error(self, message):
            raise ValueError(f"alt issue: {message}")

        def exit(self, status=0, message=None):
            raise ValueError(message or "use alt issue new --title TITLE -, comment NUMBER -, "
                                        "label NUMBER --add|--remove LABEL, or close NUMBER --reason completed|not-planned")

    parser = Parser(prog="alt issue", add_help=False)
    commands = parser.add_subparsers(dest="operation", required=True)
    new = commands.add_parser("new")
    new.add_argument("--title", required=True)
    new.add_argument("--label", action="append", dest="labels")
    comment = commands.add_parser("comment")
    comment.add_argument("number", type=int)
    for command in (new, comment):
        command.add_argument("text", choices=["-"])
    close = commands.add_parser("close")
    close.add_argument("number", type=int)
    close.add_argument("--reason", required=True, choices=ISSUE_CLOSE_REASONS)
    label = commands.add_parser("label")
    label.add_argument("number", type=int)
    label.add_argument("--add", action="append")
    label.add_argument("--remove", action="append")
    return parser


def issue_repository() -> str:
    """Where incident issues go: the operator's explicit choice, never a repository this installation did not name.

    Publication is off until the operator turns it on in Settings or sets ALTITUDE_UPSTREAM_ISSUE_REPOSITORY;
    Altitude's own repository is only the value the form fills in, never an unconsented default.
    """
    target = config.incident_repository()
    if target is None:
        raise ValueError("incidents stay on this machine until incident reports are turned on in Settings "
                         "(or ALTITUDE_UPSTREAM_ISSUE_REPOSITORY names the GitHub owner/repository that receives them)")
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", target):
        target = "https://github.com/" + target
    repository = repository_url(target)
    if not repository:
        raise ValueError("the incident repository must name a GitHub owner/repository or GitHub repository URL")
    return repository


def issue_write(project: str, operation: str, body: str, *, actor: str, title: str = "",
                labels: list[str] | None = None, number: int | None = None, reason: str | None = None,
                add: list[str] | None = None, remove: list[str] | None = None) -> str:
    """Altd owns project-local issues; incident issues come from `incidents.publish_issue`."""
    if actor not in ("l3", "operator"):
        raise ValueError("alt issue: not available to an L2 worker")
    if operation not in ("new", "comment", "close", "label"):
        raise ValueError("alt issue: only new, comment, label, and close are available")
    creating, labelling = operation == "new", operation == "label"
    if (not isinstance(body, str) or not isinstance(title, str)
            or any(names is not None and (not isinstance(names, list) or any(not isinstance(x, str) for x in names))
                   for names in (labels, add, remove))):
        raise ValueError("alt issue: body, title, and labels must be text")
    if creating and not title.strip():
        raise ValueError(f"alt issue {operation}: title is required")
    if not creating and (type(number) is not int or number < 1):
        raise ValueError(f"alt issue {operation}: a positive issue number is required")
    if (not creating and (title or labels) or creating and number is not None or operation != "close" and reason is not None
            or not labelling and (add or remove)):
        raise ValueError("alt issue: fields do not match the operation")
    if operation == "close" and (reason not in ISSUE_CLOSE_REASONS or body):
        raise ValueError("alt issue close: --reason completed|not-planned is required; no body is accepted")
    if labelling and (not (add or remove) or body):
        raise ValueError("alt issue label: --add or --remove LABEL is required; no body is accepted")
    # Private incident evidence boundary: local evidence never leaves the machine in a public issue.
    incidents.check_public("\n".join([body, title, *(labels or []), *(add or []), *(remove or [])]))
    checkout = config.project_path(project)
    origin = subprocess.run(["git", "remote", "get-url", "origin"], cwd=checkout,
                            capture_output=True, text=True, timeout=10, env=git_policy.git_env(checkout))
    repository = repository_url(origin.stdout) if origin.returncode == 0 else None
    if not repository:
        raise ValueError("alt issue: checkout origin must identify a GitHub repository")
    args = ["gh", "issue", "create" if creating else "edit" if labelling else operation]
    if creating:
        args += [f"--title={title}", *(f"--label={label}" for label in labels or [])]
    else:
        args.append(str(number))
    args += ["--repo", repository]
    if labelling:
        args += [*(f"--add-label={label}" for label in add or []), *(f"--remove-label={label}" for label in remove or [])]
    else:
        args += ["--reason", reason.replace("-", " ")] if operation == "close" else ["--body-file", "-"]
    env = engines.clean_env()
    env.pop("GH_REPO", None)
    result = subprocess.run(args, input=body, cwd=checkout, env=env, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise ValueError("alt issue: " + " ".join((result.stderr or "gh failed").split()))
    url = f"{repository}/issues/{number}" if operation in ("close", "label") else result.stdout.strip()
    with S.project_lock(project):
        S.project_log(project, f"issue-{operation}", actor=actor,
                      title=title if creating else f"Issue #{number}", url=url,
                      **({"number": number, "reason": reason} if operation == "close" else
                         {"number": number, "add": add or [], "remove": remove or []} if labelling else {}))
    return url


def task_wait_view(project: str, task: dict) -> dict:
    """Resolve a planned prerequisite's title without changing the saved wait."""
    wait = task.get("planned_wait")
    if not wait:
        return task
    title = None
    if wait.get("after"):
        try:
            title = S.load_task(project, wait["after"]).get("title")
        except S.TaskNotFound:
            pass
    return {**task, "planned_wait": {**wait, "after_title": title}}


def project_view(name: str) -> dict:
    proj = config.project(name)
    week = (datetime.now(timezone.utc) - timedelta(days=7)).replace(microsecond=0).isoformat()  # S.now()'s form
    live = {s["slug"]: s for s in monitor.sessions() if s.get("kind") == "l2" and s.get("project") == name}
    tasks = []
    for t in S.list_tasks(name):
        t = task_wait_view(name, t)
        d = S.task_dir(name, t["slug"])
        prog = (d / "progress.md").read_text()[-1500:] if (d / "progress.md").exists() else ""
        tasks.append({**t, "live": live.get(t["slug"]), "progress_tail": prog, "origin": suggestions.origin(name, t.get("improvement")), "has": {f: (d / f"{f}.md").exists() for f in ("request", "brief", "report", "digest", "progress")}})
    order = {"blocked": 0, "running": 1, "reported": 2, "queued": 3}
    tasks.sort(key=lambda t: (order.get(t["state"], 9), t["updated"]))
    return {"name": name, "config": proj, "l3": l3.info(name), "busy": l3.busy(name), "tasks": tasks,
            "design_viewer": design_viewer_url(name), "repository": project_repository(name),
            # Done this week, newest first (#296: by finish time, never by slug).
            "archive": sorted(({**{k: t.get(k) for k in ("slug", "state", "title", "updated", "prs")},
                                "origin": suggestions.origin(name, t.get("improvement"))} for t in S.list_tasks(name, True)
                               if t["state"] in ("done", "rejected") and (t["updated"] or "") >= week),
                              key=lambda t: t["updated"], reverse=True),
            "decisions": T.decisions(name), "log": [event for event in S.read_project_log(name, 40)
                                                  if event.get("kind") != "project-message-received"],
            "incidents": incidents.index(name)[-10:], "hold": S.read_json(config.project_dir(name) / "hold.json"),
            "improvements": improve.view(name), "project_agents": project_agents.project_summary(name),
            "state_md": (config.project_dir(name) / "STATE.md").read_text() if (config.project_dir(name) / "STATE.md").exists() else ""}


def project_repository(name: str) -> str | None:
    """The project's GitHub page, or None when a detached, moved or unreadable checkout has none to give."""
    try:
        origin = subprocess.run(["git", "remote", "get-url", "origin"], cwd=config.project_path(name),
                                capture_output=True, text=True, timeout=2, env=git_policy.git_env(config.project_path(name)))
    except (KeyError, OSError, subprocess.SubprocessError, git_policy.GitPolicyError):
        return None
    return repository_url(origin.stdout)


# The record's own ledgers behind the view's review, decisions and message deliveries stay on the
# server: they made a long task's reply several times larger, re-sent on every change.
TASK_VIEW_OMITTED = ("reviews", "question_groups", "message_deliveries")


def task_transfer_view(project: str, task: dict) -> dict:
    """One snapshot supplies the exact task-only choices the operator can confirm."""
    current = task.get("l2_engine") or task.get("engine")
    request = task.get("daemon_request") or {}
    active = request.get("status") in ("pending", "executing")
    settings = config.load_projects().get(project)
    configured = (dict.fromkeys(option["engine"] for tier in config.role_routing("l2", settings)
                                for option in tier) if settings else {})
    if task.get("state") not in ("running", "blocked") or not task.get("attempt") or not task.get("l2_engine"):
        configured = {}
    targets = []
    catalog = config.choice_options()
    current_model, current_effort = dispatch.launched(task) if task.get("l2_engine") else (None, None)
    for engine in configured:
        names = {row["model"]: row["label"] for row in catalog["models"]
                 if row["engine"] == engine and row["model"]}
        for tier in config.role_routing("l2", settings):
            for option in tier:
                if option["engine"] == engine and option.get("model"):
                    names.setdefault(option["model"], config.model_label(option["model"]))
        if engine == current and current_model:
            names.setdefault(current_model, config.model_label(current_model))
        default_model = config.default_model("l2", engine, settings)
        default_effort = config.task_effort(engine, config.default_effort("l2", engine, settings))
        models = ([{"value": "default", "model": default_model,
                    "label": "Default · " + names.get(default_model, config.model_label(default_model))}]
                  if default_model else [])
        models += [{"value": model, "model": model, "label": label} for model, label in names.items()]
        for model in models:
            reason = None
            try:
                if active:
                    raise T.TransitionError("A task action is already in progress.")
                dispatch._require_handoff(project, task, engine, task.get("attempt"),
                                          {"model": model["model"], "effort": default_effort}, allow_unchanged=True)
            except T.TransitionError as exc:
                reason = dispatch.handoff_reason(str(exc))
            model.update(available=reason is None, reason=reason)
        available = any(model["available"] for model in models)
        reason = None if available else (models[0]["reason"] if models else "No named model is configured.")
        targets.append({"engine": engine, "label": config.ENGINE_LABELS.get(engine, engine),
                        "available": available, "reason": reason, "models": models,
                        "default_effort": default_effort,
                        "efforts": [{"value": value, "label": config.effort_label(value)}
                                    for value in config.ENGINE_EFFORTS[engine]]})
    eligible = any(target["available"] for target in targets)
    preparation = False
    if configured and not dispatch.engine_stopped(task) and T.stopped_by(task) not in dispatch.DAEMON_REQUEST_ACTORS:
        worker = engines.worker(dispatch.l2_engine(task), task, job_root=dispatch.l2_job_root(project, task["slug"]))
        preparation = not (worker and worker.get("state") in ("done", "failed", "stopped"))
    transfer = None
    if request.get("operation") == "handoff":
        transfer = {key: request.get(key) for key in ("id", "status", "phase", "engine", "at", "note", "now", "checkpoint", "result_state", "selection")}
        # The daemon saves the requeued task before the request's receipt and finishes it by this same rule, so a read
        # in between shows the switch done rather than a queued task that cannot take a message or keep a recording.
        if active and dispatch._handoff_requeued(task, request):
            transfer["status"] = "done"
        transfer["target_label"] = config.ENGINE_LABELS.get(request.get("engine"), request.get("engine"))
        # Who asked, and the coordinator's own words, so a switch never reads as caused by opening the chooser.
        transfer["by"] = "operator" if request.get("actor") == config.OPERATOR_ACTOR else request.get("actor")
        transfer["reason"] = request.get("reason") if transfer["by"] != "operator" else None
    return {"current_engine": current, "current_label": config.ENGINE_LABELS.get(current, current),
            "current_model": current_model,
            "current_model_label": config.model_name(current, current_model) if current_model else None,
            "current_effort": current_effort, "current_effort_label": config.effort_label(current_effort or "native"),
            "targets": targets, "eligible": eligible,
            "reason": None if eligible else (targets[0]["reason"] if targets else "No other engine is configured."),
            "quota_limited": bool(task.get("usage_limit")),
            "refused": task.get("engine_refusal") if task.get("state") == "blocked" else None, "preparation": bool(preparation), "request": transfer,
            "resume_hold": (dispatch.resume_engine_hold(task)
                            if task.get("state") == "blocked" and task.get("usage_limit") else None),
            "observed": {key: task.get(key) for key in ("attempt", "agent_id", "session_id", "block_id")}}


def task_model_view(task: dict) -> dict | None:
    """The engine, model and effort a task runs with, in its chooser's words: the engine's report once it arrives,
    else the launch values Altitude passed."""
    engine = task.get("l2_engine")
    if not engine:
        return None
    launched, effort = dispatch.launched(task)
    seen, seen_effort = task.get("engine_model"), task.get("engine_reasoning_effort")
    name = lambda model: config.model_name(engine, model) if model else None
    chosen = [name(task.get("model")), config.effort_label(task["effort"]) if task.get("effort") else None]
    return {"engine": config.ENGINE_LABELS.get(engine, engine), "model": name(seen or launched),
            "effort": config.effort_label(seen_effort or effort),
            "chosen": " · ".join(filter(None, chosen)) or None,
            "launched": f"{name(launched) or 'engine default model'} · {config.effort_label(effort)}",
            "reported": (f"{name(seen) or 'model not reported'} · "
                         f"{config.effort_label(seen_effort) if seen_effort else 'effort not reported'}"
                         if seen or seen_effort else None)}


def task_view(project: str, slug: str) -> dict:
    questions = T.question_views(project, slug)
    with S.project_lock(project):
        t = task_wait_view(project, S.load_task(project, slug))
        d = S.task_dir(project, slug)
        report = S.read_json(d / "report.json")
        files = {f: (d / f"{f}.md").read_text() for f in ("request", "brief", "report", "digest", "progress") if (d / f"{f}.md").exists()}
        events = S.read_events(project, slug)
    try:
        activity = transcript.activity(project, slug)
    except transcript.TranscriptAccessError:
        activity = {"generation": t.get("agent_id"), "state": "unavailable", "commentary": None,
                    "observation": None, "delivered": [], "background": None, "error": "Activity is unavailable for this task."}
    if activity["generation"] != t.get("agent_id"):
        activity = {"generation": t.get("agent_id"), "state": "unavailable", "commentary": None,
                    "observation": None, "delivered": [], "background": None, "error": "The worker changed. Refresh this task."}
    return {**{k: v for k, v in t.items() if k not in TASK_VIEW_OMITTED},
            "transfer": task_transfer_view(project, t), "running_model": task_model_view(t),
            "repository": project_repository(project),
            "can_continue": T.reported_continuable(t, report),
            "question": questions[-1] if questions else None, "questions": questions,
            "question_group": T.question_group_view(project, t),
            "files": files, "messages": [{**row, "text": row.get("display_text", row["text"])}
                                         for row in T.message_views(project, slug, activity["delivered"])],
            "activity": activity, "review": reviews.view(project, slug),
            "steering": T.steering_view(t, events, job_root=d / "l2-engine"),
            "report_json": report, "live": next((s for s in monitor.sessions() if s.get("kind") == "l2" and s.get("slug") == slug and s.get("project") == project), None)}


def main(host: str | None = None, port: int | None = None) -> None:
    global TRUST
    config.ensure_root()
    dispatch.forget_speech_service()
    reinstall_voice = speech.remove_home_runtime()
    push.remove_legacy_key(log)
    if os.environ.get("ALTITUDE_SERVICE"):  # only the service instance clears the restart-pending flag
        try:
            git_policy.activate_source()
        except git_policy.GitPolicyError as e:
            log(f"service startup refused: {e}")
            raise SystemExit(1) from e
    host = host or config.HOST
    port = port or config.PORT
    try:
        access.prepare()
    except OSError as exc:
        log(f"cannot prepare the private access store ({exc}); refusing to start")
        raise SystemExit(1) from exc
    try:
        certificate_host = config.PUBLIC_HOST if platform.containerized() else host
        context = tls.check(certificate_host) if config.TLS else None
        probe = tls.probe_context(certificate_host) if context is not None else None
        TRUST = tls.TrustCheck(probe, tls.identity(config.TLS_DIR / "ca.crt")["name"]) if probe is not None else None
    except (tls.TLSFailure, OSError) as exc:
        log(f"HTTPS startup refused: {exc}")
        raise SystemExit(1) from exc
    try:
        srv = ThreadingHTTPServer((host, port), Handler)
    except OSError as e:
        # No silent fallback to loopback: exit non-zero and let the service manager retry when the tunnel is ready.
        log(f"cannot bind {host}:{port} ({e}); exiting so the unit restarts (RestartSec)")
        raise SystemExit(1)
    srv.daemon_threads = True
    config.HOST, config.PORT = host, srv.server_port
    try:
        if context is not None:
            srv.socket = context.wrap_socket(srv.socket, server_side=True, do_handshake_on_connect=False)
        for project in config.load_projects():
            if config.is_managed(project):
                ensure_l3_verb_broker(project)
        if os.environ.get("ALTITUDE_SERVICE"):  # clients reach the service it records, not their launch settings
            tls.publish({"host": host, "public_host": certificate_host, "port": srv.server_port,
                         "tls": context is not None, "tls_dir": config.TLS_DIR})
    except (OSError, RuntimeError) as e:
        stop_l3_verb_brokers()
        srv.server_close()
        log(f"cannot initialize HTTPS, the L3 verb broker or the service record ({e}); refusing to start")
        raise SystemExit(1) from e
    scheme = "https" if context is not None else "http"
    # Activation: do not release waiting launches if the replacement cannot bind its API or brokers.
    if os.environ.get("ALTITUDE_SERVICE") and config.RELEASE is None:
        (config.MONITOR_DIR / dispatch.RESTART_PENDING).unlink(missing_ok=True)
    if os.environ.get("ALTITUDE_TIMERS", "1") != "0":
        SPEECH.update_installed(reinstall_voice)
        restart_notice()
        settle_interrupted_machine_commands()
        repair.service_started()
        chats.service_started()
        threading.Thread(target=timer_loop, args=(context, certificate_host), name="timers", daemon=True).start()
        threading.Thread(target=validation.reconcile, name="validation-reconcile", daemon=True).start()
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
        try:
            terminal.close_all()
        except terminal.TerminalError as exc:
            log(f"terminal: {exc}")  # each terminal's job is PartOf the service, which stops it too
        SPEECH.shutdown()


TRUST: tls.TrustCheck | None = None  # the pairing screen's certificate trust check; None without a CA key


def _located() -> dict:
    """This service's network settings with the name devices open: its certified name, not the address it binds."""
    return tls.located({"host": config.HOST, "port": config.PORT, "tls": config.TLS, "tls_dir": config.TLS_DIR})


def service_address() -> str | None:
    try:
        return _located()["url"]
    except tls.TLSFailure:
        return None


def pairing_directions() -> dict:
    """What a new device needs beside its pairing code: the HTTPS address to open, as a QR code when a phone can
    reach it, and the certificate whose name and full SHA-256 it checks before installing."""
    address, rows = service_address(), None
    try:
        tls.phone_address(_located())
        rows = ["".join("1" if dark else "0" for dark in row) for row in qr.matrix(address)]
    except tls.TLSFailure:
        pass  # plain HTTP or an address only this computer can open: no QR code a phone could use
    view = certificate_view()
    return {"address": address if config.TLS else None, "qr": rows,
            "certificate": {"name": view["name"], "sha256": tls.fingerprint_rows(view["sha256"])}
            if view and "sha256" in view else None}


def certificate_view() -> dict | None:
    """The CA a device trusts, as Settings › Devices shows it for device setup; None without HTTPS or
    without a CA file of its own."""
    ca = config.TLS_DIR / "ca.crt"
    if not config.TLS or not ca.exists():
        return None
    try:
        authority = tls.identity(ca)
    except (tls.TLSFailure, OSError) as exc:
        return {"error": str(exc)}
    return {**authority, "scope": tls.describe_scope(authority["scope"])}


_SHARE: tls.Share | None = None
_SHARE_LOCK = threading.Lock()


def open_share() -> dict:
    """Settings › Devices › Set up a device: one share window of the CA this service serves under, replacing an
    earlier one, with its QR code and what the device must match."""
    global _SHARE
    found = tls.located({"host": config.HOST, "port": config.PORT, "tls": config.TLS, "tls_dir": config.TLS_DIR})
    ca = config.TLS_DIR / "ca.crt"
    with _SHARE_LOCK:
        if _SHARE is not None:
            _SHARE.close()
        tls.phone_address(found)
        _SHARE = tls.Share(found, ca.read_bytes(), tls.identity(ca))
        window = _SHARE
    log("opened a ten-minute certificate share for device setup")
    return {"link": window.link, "seconds": round(window.remaining()), "name": window.authority["name"],
            "sha256": window.authority["sha256"],
            "qr": ["".join("1" if dark else "0" for dark in row) for row in qr.matrix(window.link)]}


def close_share(link: str) -> None:
    """Close the share window at `link` early; a page closing a window another page replaced leaves it open."""
    with _SHARE_LOCK:
        if _SHARE is not None and _SHARE.link == link:
            _SHARE.close()


def tls_init(ip: str | None = None) -> dict:
    return tls.initialize(ip)
