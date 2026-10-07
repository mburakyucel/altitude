"""Paths and settings. Everything runtime lives under ALTITUDE_HOME (default ~/.altitude)."""
from __future__ import annotations
import json
import fcntl
import logging
import functools
import os
import re
import shutil
import subprocess
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from . import platform

HOME = Path.home()
SOURCE = Path(__file__).resolve().parent.parent
RELEASE = json.loads((SOURCE / "release.json").read_text()) if (SOURCE / "release.json").is_file() else None
INSTALL_PREFIX = SOURCE.parent.parent if RELEASE is not None else None
INSTALL_CONFIG = Path(os.environ.get("ALTITUDE_CONFIG", HOME / ".config/altitude/install.json")).expanduser()
if RELEASE is not None and not platform.containerized() and INSTALL_CONFIG.exists():
    for key, value in json.loads(INSTALL_CONFIG.read_text()).get("environment", {}).items():
        if not isinstance(value, str) or not (key.startswith("ALTITUDE_") or key in ("PATH", "CLAUDE_BIN", "CODEX_BIN")):
            raise ValueError(f"invalid installation environment setting: {key}")
        os.environ.setdefault(key, value)
ROOT = Path(os.environ.get("ALTITUDE_HOME", HOME / ".altitude"))

# A test module can set ALTITUDE_HOME before its own Altitude import and still be too late: unittest discovery
# imports every test into one interpreter, so another module may already have frozen this module's paths against
# the live default.  Never let that ordering mistake reach the first filesystem write.  The normal full-suite
# bootstrap supplies a throwaway root (tests/test_000_state_isolation.py); this guard is the fail-closed backstop
# for a shuffled suite, a single test module, or any future runner that imports config first.
if "unittest" in sys.modules and ROOT.expanduser().resolve() == (HOME / ".altitude").resolve():
    raise RuntimeError(
        "refusing to use the live ~/.altitude state from a unittest process; "
        "set ALTITUDE_HOME to a throwaway directory before importing altitude"
    )
REPO = SOURCE.parent.parent if RELEASE is None and SOURCE.parent.name == ".altitude-source" else SOURCE
if RELEASE is None and SOURCE == REPO and (REPO / ".altitude-source/current").is_dir():
    SOURCE = (REPO / ".altitude-source/current").resolve()
# Incident issue target's initial value; the `incident_repository` machine setting replaces it (incident_repository()).
UPSTREAM_ISSUE_REPOSITORY = os.environ.get("ALTITUDE_UPSTREAM_ISSUE_REPOSITORY")
# The branch a source service runs and self-deploys: main unless the operator chooses another.
SOURCE_BRANCH = os.environ.get("ALTITUDE_SOURCE_BRANCH") or "main"
#: Altitude's own repository: the destination First run fills in when the operator turns incident publishing on.
ALTITUDE_REPOSITORY = "mburakyucel/altitude"
# A repository whose base commit ships this workflow requires its PR `check` on the exact candidate head.
PR_CHECK_WORKFLOW = ".github/workflows/self-hosted-checks.yml"
PR_CHECK_NAME = "check"
PERSONAS = SOURCE / "personas"
SCHEMAS = SOURCE / "schemas"
TEMPLATES = SOURCE / "templates"
WEB_DIST = REPO / "web" / "dist"
HOOKS = SOURCE / "hooks"
WORKTREE_ROOT = Path(".claude/worktrees")

CLAUDE_BIN = os.environ.get("CLAUDE_BIN", str(HOME / ".local/bin/claude"))
CODEX_BIN = os.environ.get("CODEX_BIN", "codex")


def network(environment) -> dict:
    """Where a server started with this environment listens and which HTTPS identity it serves."""
    return {"host": environment.get("ALTITUDE_HOST", "127.0.0.1"), "port": int(environment.get("ALTITUDE_PORT", "8890")),
            "public_host": environment.get("ALTITUDE_PUBLIC_HOST", "localhost"),
            "tls_dir": Path(environment.get("ALTITUDE_TLS_DIR", HOME / ".config/altitude/tls")).expanduser(),
            "tls": environment.get("ALTITUDE_TLS", "1") != "0"}


HOST, PORT, TLS_DIR, TLS = map(network(os.environ).get, ("host", "port", "tls_dir", "tls"))
PUBLIC_HOST = network(os.environ)["public_host"]
# The projects folder's initial value; `alt machine set --projects-folder` replaces it (project_roots()).
PROJECT_ROOTS = [Path(p).expanduser() for p in os.environ.get("ALTITUDE_ROOTS", str(HOME / "Projects")).split(":")]


def installation_environment() -> dict[str, str]:
    """Persist application choices, excluding transient actor/session authority."""
    return {"ALTITUDE_HOME": str(ROOT), "ALTITUDE_HOST": HOST, "ALTITUDE_PORT": str(PORT),
            "ALTITUDE_TLS_DIR": str(TLS_DIR), "ALTITUDE_TLS": "1", "PATH": subprocess_env().get("PATH", ""),
            "ALTITUDE_ROOTS": ":".join(map(str, PROJECT_ROOTS)),
            **{key: os.environ[key] for key in ("CLAUDE_BIN", "CODEX_BIN", "ALTITUDE_OPERATOR",
               "ALTITUDE_PRIMARY_ENGINE", "ALTITUDE_UPSTREAM_ISSUE_REPOSITORY") if key in os.environ}}

# Context lines per engine: Claude quality degrades past
# ~25–30% of the window in the operator's experience. Every Claude 5 alias Altitude uses (opus, fable, sonnet) reports a
# 1,000,000-token window (probed 2026-08-30: result JSON `modelUsage[..].contextWindow`), so the umbrella for all Claude
# sessions is **300k**: auto-compact there (explicit `autoCompactWindow` on every launch — never a percent override on
# top of the user's setting, which had them compacting at ~90k) and rotate the L3 there. Codex compacts at its own limit.
CONTEXT_WINDOW = 1_000_000        # Claude, tokens; used to turn usage into a percentage
AUTOCOMPACT_WINDOW = 300_000      # passed as Claude Code's `autoCompactWindow` on every launch
# Per engine: (warn fraction, act fraction, window). Warn colours the monitor and says "rotate next" for L3;
# act rotates a Claude L3 to a fresh session. Codex compacts natively at its limit; Altitude only watches.
CONTEXT_LINES = {"claude": (0.25, 0.30, CONTEXT_WINDOW), "codex": (0.80, 1.00, 256_000)}
# Default Claude models. Codex uses the Codex CLI's configured model unless the task overrides it.
MODELS = {"l3": "fable", "l2": "opus"}
ENGINES = ("claude", "codex")
CONVERSATION_AUDIT_PROJECT = "altitude"
CONVERSATION_AUDIT_REVIEWER = {"engine": "claude", "model": "claude-sonnet-5"}
#: Display names, the way the shell shows an engine; nothing outside the seam spells one.
ENGINE_LABELS = {"claude": "Claude", "codex": "Codex"}
#: The operator seam's initial display name; the `operator_name` machine setting replaces it (operator_name()).
OPERATOR = os.environ.get("ALTITUDE_OPERATOR")
#: The persisted authority identity in task records, events and messages, independent of the display name.
OPERATOR_ACTOR = "burak"
PRIMARY_DEFAULT_ENGINE = os.environ.get("ALTITUDE_PRIMARY_ENGINE", "codex")
# Ordered tiers; order within a tie settles unknown/equal weekly headroom. An option without a model uses the
# project's default model for that engine, then the role default (L2 Opus, L3 Fable). The lower tier keeps Opus,
# the generally available Claude model, below a rejected role default; a tier never repeats an option resolved above it.
AUTO_ROUTING = [[{"engine": engine, "model": None} for engine in sorted(ENGINES, key=lambda e: e != PRIMARY_DEFAULT_ENGINE)],
                [{"engine": "claude", "model": "opus"}]]
TASK_EFFORTS = ("native", "low", "medium", "high", "xhigh", "max", "ultra")
ENGINE_EFFORTS = {"claude": TASK_EFFORTS[:-1], "codex": TASK_EFFORTS}
MODEL_ALIASES = ("opus", "sonnet", "haiku", "fable")
ROLES = ("l3", "l2")


def role_setting(role: str, engine: str, kind: str) -> str:
    """Project registry key holding a role's default ``model`` or ``effort`` on one engine."""
    return f"{role}_{kind}" if engine == "claude" else f"{role}_{engine}_{kind}"


def model_family(name: str | None) -> str | None:
    """The Claude alias a model id or display name belongs to ("claude-fable-5-1", "Fable" -> "fable")."""
    words = re.findall(r"[a-z]+", str(name or "").lower())
    return next((alias for alias in MODEL_ALIASES if alias in words), None)


#: Every project default: registry key -> (role, engine, kind). Each is independent of the others.
DEFAULT_SETTINGS = {role_setting(role, engine, kind): (role, engine, kind)
                    for role in ROLES for engine in ENGINES for kind in ("model", "effort")}
#: A model choice tried ahead of a role's routing: New tasks for every project's L2, and each project's L3 choice.
CHOICE_SETTINGS = {"l2": "new_tasks", "l3": "l3_choice"}
PROJECT_SETTINGS = ("routing", "l2_preference", "l2_engine", "l3_engine", "l3_choice", *DEFAULT_SETTINGS)
WIP_PER_MACHINE = 80
L3_TURN_TIMEOUT = 900             # seconds
MACHINE_COMMAND_TIMEOUT = 600     # seconds; one command under a task's operator grant
L3_CODEX_TURN_TIMEOUT = 1200
AGENT_POLL_SECONDS = 30

PROJECTS_FILE = ROOT / "projects.json"
MONITOR_DIR = ROOT / "monitor"
INCIDENT_INDEX = ROOT / "incidents.jsonl"
DIGEST_FILE = ROOT / "DIGEST.md"


def task_effort(engine: str | None, effort: str | None, *, role: str = "l2") -> str | None:
    """Resolve intent; native model support and managed caps remain provider decisions."""
    if effort is not None and effort not in TASK_EFFORTS:
        raise ValueError(f"effort must be one of {', '.join(TASK_EFFORTS)}")
    if effort is not None and engine is not None and effort not in ENGINE_EFFORTS.get(engine, ()):
        raise ValueError(f"{ENGINE_LABELS.get(engine, engine)} does not support reasoning effort {effort}; choose a supported level or Native")
    if effort == "native":
        return None
    return effort or ("high" if role == "l2" and engine == "codex" else None)


def validate_project_default(setting: str, value) -> None:
    """A model is one alias or id; an effort must be one its own engine accepts."""
    role, engine, kind = DEFAULT_SETTINGS[setting]
    if kind == "effort":
        task_effort(engine, value, role=role)
    elif value is not None and not valid_model(value):
        raise ValueError("a default model is one alias or model id without spaces")


def valid_model(value) -> bool:
    return isinstance(value, str) and bool(value) and not any(c.isspace() for c in value)


EFFORT_LABELS = {"native": "Native", "xhigh": "Extra High"}


def effort_label(value: str | None) -> str:
    return EFFORT_LABELS.get(value or "native", (value or "").title())


def defaults_view(name: str) -> dict:
    """A project's settings page: Only pins, the L3 choice, routing, and one model/effort pair per role and engine."""
    from . import route
    entry = project(name)
    def field(role, engine, kind):
        key = role_setting(role, engine, kind)
        if kind == "model":
            return {"setting": key, "value": entry.get(key), "default": default_model(role, engine) or "CLI default",
                    "choices": list(MODEL_ALIASES) if engine == "claude" else []}
        return {"setting": key, "value": entry.get(key), "default": effort_label(task_effort(engine, None, role=role)),
                "choices": [{"value": v, "label": effort_label(v)} for v in ENGINE_EFFORTS[engine]]}
    return {"l3_engine": entry.get("l3_engine"), "l2_engine": entry.get("l2_engine"), "l3_choice": entry.get("l3_choice"),
            "l2_preference": entry.get("l2_preference"),
            "l3_unavailable": route.choice_unavailable("l3", entry.get("l3_choice"), entry), **choice_options(),
            "routing": format_routing(entry["routing"]) if "routing" in entry else None,
            "engines": [{"value": e, "label": ENGINE_LABELS[e], "efforts": list(ENGINE_EFFORTS[e]),
                         "routed": any(o["engine"] == e for tier in entry.get("routing", AUTO_ROUTING) for o in tier)}
                        for e in ENGINES],
            "roles": [{"role": role, "engines": [{"engine": engine, "label": ENGINE_LABELS[engine],
                                                   "model": field(role, engine, "model"),
                                                   "effort": field(role, engine, "effort")} for engine in ENGINES]}
                      for role in ROLES]}


def subprocess_env(environment: dict[str, str] | None = None) -> dict[str, str]:
    """Keep an explicit Node; otherwise expose the installed nvm default without shell profiles."""
    env = dict(os.environ if environment is None else environment)
    path = env.get("PATH", os.defpath)
    nvm = Path(env.get("NVM_DIR") or Path.home() / ".nvm") / "nvm.sh"
    if shutil.which("node", path=path) or not nvm.is_file():
        return env
    try:
        probe = subprocess.run(
            ["bash", "--noprofile", "--norc", "-c", '. "$1" --no-use && nvm which default', "nvm", str(nvm)],
            env={k: v for k, v in env.items() if k != "BASH_ENV"},
            capture_output=True, text=True, timeout=10, check=True,
        )
        node = Path(probe.stdout.strip())
        if not node.is_absolute() or not os.access(node, os.X_OK):
            raise OSError("nvm default does not name an executable Node")
        env["PATH"] = str(node.parent) + os.pathsep + path
    except (OSError, subprocess.SubprocessError) as exc:
        logging.getLogger(__name__).warning("nvm default unavailable; install a supported Node and enable its pnpm: %s", exc)
    return env


def machine_settings() -> dict:
    from . import state as S
    return S.read_json(ROOT / "settings.json", {})


def project_roots() -> list[Path]:
    """The folders First run lists the immediate subfolders of: the chosen projects folder, else ALTITUDE_ROOTS."""
    folder = machine_settings().get("projects_folder")
    if platform.containerized():
        root = Path(folder) if folder else platform.CONTAINER_PROJECTS
        platform.require_container_project(root, folder=True)
        return [root]
    return [Path(folder)] if folder else PROJECT_ROOTS


@functools.cache
def _git_name() -> str | None:
    try:
        name = subprocess.run(["git", "config", "--global", "user.name"], capture_output=True, text=True,
                              timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return name[:OPERATOR_NAME_LIMIT] or None


def operator_name() -> str | None:
    """The operator seam: the chosen name, else ALTITUDE_OPERATOR, else Git's user.name; None when none is known."""
    return machine_settings().get("operator_name") or OPERATOR or _git_name()


def operator_label() -> str:
    return operator_name() or "Operator"


OPERATOR_NAME_LIMIT = 80


def validate_operator_name(value) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not value.strip() or value != value.strip() or "\n" in value:
        raise ValueError("the name must be nonempty text on one line")
    if len(value) > OPERATOR_NAME_LIMIT:
        raise ValueError(f"the name must be at most {OPERATOR_NAME_LIMIT} characters")


def incident_repository() -> str | None:
    """Where system incidents become GitHub issues: the operator's choice (False keeps them here), else the environment."""
    settings = machine_settings()
    if "incident_repository" in settings:
        return settings["incident_repository"] or None
    return UPSTREAM_ISSUE_REPOSITORY


def validate_incident_repository(value) -> None:
    if value is None or value is False:
        return
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise ValueError("the incident repository must be a GitHub owner/name")


def validate_projects_folder(value) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError("the projects folder must be an absolute path")
    platform.require_container_project(Path(value), folder=True)
    if not Path(value).is_dir():
        raise ValueError(f"{value} is not a directory")
    if not os.access(value, os.R_OK | os.X_OK):
        raise ValueError(f"{value} is not readable")


def machine_wip() -> int:
    return machine_settings().get("wip", WIP_PER_MACHINE)


# The capability seam for speech: host voice runs the pinned speech model on this computer (`altitude/speech.py`)
# and is the default wherever that model can run; elsewhere the browser's own recognition is.
def voice_setting() -> dict:
    """The transcription backend: the saved host or browser choice, else host where the model can run."""
    value = machine_settings().get("voice")
    if value not in ("host", "browser"):
        from . import speech
        value = "host" if speech.manifest()[0] is not None else "browser"
    return {"backend": value}


def validate_voice(value) -> None:
    if value not in (None, "browser", "host"):
        raise ValueError("voice must be host or browser")


def validate_wip(value) -> None:
    if value is None:
        return
    if type(value) is not int or value < 1:
        raise ValueError("WIP must be a positive integer")


def parse_routing(value: str) -> list[list[dict]]:
    """Operator syntax: comma ties options, > starts a lower priority tier."""
    tiers, seen = [], set()
    for tier in value.split(">"):
        options = []
        for item in tier.split(","):
            engine, separator, model = item.strip().partition(":")
            if engine not in ENGINES or separator and not valid_model(model):
                raise ValueError("routing needs engine[:model] options, comma ties, and > between tiers")
            key = (engine, model or None)
            if key in seen:
                raise ValueError(f"duplicate routing option: {item.strip()}")
            seen.add(key)
            options.append({"engine": engine, "model": model or None})
        tiers.append(options)
    return tiers


def validate_preference(value) -> None:
    if value is not None and value not in ENGINES:
        raise ValueError(f"a provider preference is one of {', '.join(ENGINES)}, or unset for Auto")


def validate_engine_pin(value) -> None:
    if value is not None and value not in ENGINES:
        raise ValueError(f"an Only engine is one of {', '.join(ENGINES)}, or unset for Auto")


def parse_choice(value: str) -> dict:
    """The CLI spelling of a model choice: ``[engine][:model][@effort]``, or a Claude alias such as ``fable@high``."""
    head, _, effort = value.partition("@")
    engine, _, model = head.partition(":")
    if engine and engine not in ENGINES and not model:
        engine, model = ("claude", engine) if engine in MODEL_ALIASES else ("", engine)
    choice = {key: item for key, item in (("engine", engine), ("model", model), ("effort", effort)) if item}
    validate_choice(choice)
    return choice


def validate_choice(value) -> None:
    """A choice names an engine (optionally its model), an effort, or both; None is Auto."""
    if value is None:
        return
    if not isinstance(value, dict) or value.keys() - {"engine", "model", "effort"}:
        raise ValueError("a model choice has only an engine, a model and an effort")
    engine, model, effort = value.get("engine"), value.get("model"), value.get("effort")
    if not engine and not effort:
        raise ValueError("a model choice names an engine or an effort; unset it for Auto")
    if engine is not None and engine not in ENGINES:
        raise ValueError(f"engine must be one of {', '.join(ENGINES)}")
    if model is not None and (not engine or not valid_model(model)):
        raise ValueError("a chosen model is one alias or model id without spaces, on a named engine")
    if effort is not None and effort not in ENGINE_EFFORTS.get(engine, TASK_EFFORTS):
        raise ValueError(f"{ENGINE_LABELS.get(engine, 'Altitude')} does not support reasoning effort {effort}")


def choice_options() -> dict:
    """What a model choice offers: each Claude alias, every other engine's own default model, and each engine's efforts."""
    return {"models": [{"engine": "claude", "model": alias, "label": alias.title()} for alias in MODEL_ALIASES]
            + [{"engine": e, "model": None, "label": f"{ENGINE_LABELS[e]} default"} for e in ENGINES if e != "claude"],
            "efforts": {e: [{"value": v, "label": effort_label(v)} for v in ENGINE_EFFORTS[e] if v != "native"]
                        for e in ENGINES}}


def role_choice(role: str, project: dict) -> dict | None:
    """The choice tried ahead of this role's routing: New tasks is one installation setting, L3's is per project."""
    return machine_settings().get(CHOICE_SETTINGS[role]) if role == "l2" else project.get(CHOICE_SETTINGS[role])


def format_routing(tiers: list[list[dict]]) -> str:
    """The operator syntax parse_routing reads."""
    return ">".join(",".join(o["engine"] + (":" + o["model"] if o.get("model") else "") for o in tier) for tier in tiers)


def role_routing(role: str, project: dict) -> list[list[dict]]:
    """The project's Auto tiers for this role. An L2 provider preference lifts that engine's options, in
    their tier order, above every other option; the other engine stays below as its availability fallback."""
    tiers = project.get("routing", AUTO_ROUTING)
    prefer = project.get(f"{role}_preference")
    if prefer not in ENGINES:
        return tiers
    split = [[o for o in tier if (o["engine"] == prefer) == first] for first in (True, False) for tier in tiers]
    return [tier for tier in split if tier]


def default_model(role: str, engine: str, project: dict | None = None) -> str | None:
    """The project's default model on that engine, then the role default; Codex leaves it to its CLI."""
    return (project or {}).get(role_setting(role, engine, "model")) or (MODELS[role] if engine == "claude" else None)


def default_effort(role: str, engine: str, project: dict | None = None) -> str | None:
    """The project's requested effort for that role on that engine; None keeps the engine default."""
    return (project or {}).get(role_setting(role, engine, "effort"))


def pinned_option(role: str, project: dict, *, engine: str | None = None,
                  model: str | None = None) -> dict | None:
    """Explicit task or project engine/model pins; project default models are preferences, not pins."""
    if model and not engine:
        engine = "claude" if model in MODEL_ALIASES else project.get(f"{role}_engine")
        if not engine:
            raise ValueError("a model pin requires --engine for this model")
    engine = engine or project.get(f"{role}_engine")
    if not engine:
        return None
    if engine not in ENGINES:
        raise ValueError(f"engine must be one of {ENGINES}, not {engine!r}")
    return {"engine": engine, "model": model or default_model(role, engine, project)}


@contextmanager
def restart_lock(*, exclusive: bool = False):
    """Fence activation against daemon work and bounded validation, never detached workers.

    Shared holders are dispatch, review admission, L3, validation and report handling. The exclusive requester
    checks quiet and records requested_at before another holder can enter, across processes.
    """
    MONITOR_DIR.mkdir(parents=True, exist_ok=True)
    with (MONITOR_DIR / "restart.lock").open("a") as handle:
        try:
            fcntl.flock(handle, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


_provider_admitted = ContextVar("provider_admitted", default=None)


class AdmissionPaused(RuntimeError):
    """No provider was invoked; caller state must remain eligible for deliberate continuation."""


@contextmanager
def provider_admission():
    if _provider_admitted.get() == os.getpid():
        yield None
        return
    with platform.container_admission() as why:
        token = _provider_admitted.set(os.getpid() if not why else None)
        try:
            yield why
        finally:
            _provider_admitted.reset(token)


def admitted_provider(function):
    """Last common gate, including callers outside the daemon's ordinary work queues."""
    @functools.wraps(function)
    def admitted(*args, **kwargs):
        with provider_admission() as why:
            if why:
                raise AdmissionPaused(why)
            return function(*args, **kwargs)
    return admitted


def restart_in_progress() -> bool:
    from . import platform
    if platform.containerized():
        return False  # Image replacement owns activation; stale native receipts cannot fence admission.
    if RELEASE is not None:
        return (INSTALL_PREFIX / "pending.json").exists()
    from . import state as S
    pending = S.read_json(MONITOR_DIR / "restart-pending.json", {}) or {}
    return bool(pending.get("requested_at") and not pending.get("failed"))


def ensure_root() -> None:
    for d in (ROOT, MONITOR_DIR, ROOT / "hooks"):
        d.mkdir(parents=True, exist_ok=True)
    if not PROJECTS_FILE.exists():
        PROJECTS_FILE.write_text("{}\n")


def load_projects() -> dict:
    with projects_lock():
        return _load_projects()


@contextmanager
def projects_lock():
    """Serialize registry edits across projects without losing concurrent changes."""
    ensure_root()
    with open(ROOT / ".projects.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _load_projects() -> dict:
    """Read under projects_lock; project concurrency is not a setting."""
    try:
        projects = json.loads(PROJECTS_FILE.read_text() or "{}")
    except ValueError:
        return {}
    for entry in projects.values():
        entry.pop("wip", None)
    return projects


def save_projects(projects: dict) -> None:
    from .state import atomic_write
    atomic_write(PROJECTS_FILE, json.dumps(projects, indent=2, sort_keys=True) + "\n")


@contextmanager
def edit_projects():
    with projects_lock():
        projects = _load_projects()
        yield projects
        save_projects(projects)


def _write_project(name: str, entry: dict | None) -> None:
    """Caller holds the project lock; reload the shared registry so other projects' edits survive."""
    with edit_projects() as projects:
        if entry is None:
            projects.pop(name, None)
        else:
            projects[name] = entry


@contextmanager
def add_project(name: str, *, path=None, approval="default", l2_engine=None, l3_engine=None):
    """CLI/HTTP registration, including rollback if the caller's setup fails."""
    from . import state as S
    path = Path(path or (project_roots()[0] / name)).expanduser()
    platform.require_container_project(path)
    if not path.is_dir():
        raise ValueError(f"{path} is not a directory")
    entry = {"path": str(path), "approval": approval,
             **{key: value for key, value in {"l2_engine": l2_engine, "l3_engine": l3_engine}.items() if value}}
    with S.project_lock(name):
        registered = load_projects()
        # Runtime folders are named after projects, and a case-insensitive disk (macOS by default) holds one folder.
        clash = next((other for other in registered if other != name and other.casefold() == name.casefold()), None)
        if clash:
            raise ValueError(f"Project {clash} is already registered; project names must differ by more than case")
        previous = registered.get(name)
        _write_project(name, entry)
        try:
            yield entry
        except Exception:
            _write_project(name, previous)
            raise


class ProjectBusy(ValueError):
    """An operator lifecycle action must wait for the project's existing work."""


@contextmanager
def project_activity(name: str, *, exclusive: bool = False):
    """Fence removal against L3 turns and report/timer processing, including CLI processes."""
    directory = project_dir(name)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".activity.lock").open("a") as handle:
        try:
            fcntl.flock(handle, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def is_managed(name: str) -> bool:
    return name in load_projects()


@contextmanager
def _idle_project(name: str):
    from . import dispatch, engines, state as S
    # Removal previously unregistered running work and stopped altd from monitoring it.
    with project_activity(name, exclusive=True) as quiet:
        if not quiet:
            raise ProjectBusy("Project activity is still finishing. Wait for L3 and task processing, then try again.")
        with S.project_lock(name):
            entry = project(name)
            tasks = S.list_tasks(name, include_archive=True)
            opened = [t for t in tasks if t["state"] in S.OPEN_STATES]
            if opened:
                names = ", ".join(t["slug"] for t in opened[:3])
                raise ProjectBusy(f"Finish or reject the {len(opened)} unfinished task(s) first: {names}.")
            for task in tasks:
                if (task.get("dispatching") or task.get("resume_claim")
                        or (task.get("daemon_request") or {}).get("status") in ("pending", "executing")
                        or task.get("agent_id") and engines.worker_live(
                            dispatch.l2_engine(task), task, job_root=dispatch.l2_job_root(name, task["slug"]))):
                    raise ProjectBusy(f"The worker or task operation for {task['slug']} is still finishing. Try again after it ends.")
            yield entry


def remove_project(name: str) -> None:
    with _idle_project(name):
        _write_project(name, None)


def project(name: str) -> dict:
    p = load_projects().get(name)
    if not p:
        raise KeyError(f"unknown project {name!r}; register it first (alt project add)")
    return p


def project_path(name: str) -> Path:
    return Path(project(name)["path"]).expanduser()


def project_dir(name: str) -> Path:
    return ROOT / name


def discover_projects() -> list[dict]:
    """The immediate subfolders of the projects folder, marked managed/unmanaged, and managed projects elsewhere."""
    managed = load_projects()
    by_path = {str(Path(v["path"]).expanduser().resolve()): k for k, v in managed.items()}
    out, seen = [], set()
    for root in project_roots():
        try:
            children = sorted(root.iterdir())
        except OSError:  # a missing or unreadable projects folder lists nothing; managed projects remain
            continue
        for p in children:
            if not p.is_dir() or p.name.startswith("."):
                continue
            key = str(p.resolve())
            name = by_path.get(key)
            seen.add(name)
            out.append({"name": name or p.name, "folder": p.name, "path": str(p), "managed": name is not None,
                        "git": (p / ".git").exists(), **(managed.get(name, {}) if name else {})})
    for k, v in managed.items():  # managed projects outside the roots
        if k not in seen:
            out.append({"name": k, "folder": Path(v["path"]).name, "managed": True, "git": (Path(v["path"]) / ".git").exists(), **v})
    return out
