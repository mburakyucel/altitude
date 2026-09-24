"""Paths and settings. Everything runtime lives under ALTITUDE_HOME (default ~/.altitude)."""
from __future__ import annotations
import json
import fcntl
import logging
import os
import re
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

HOME = Path.home()
SOURCE = Path(__file__).resolve().parent.parent
RELEASE = json.loads((SOURCE / "release.json").read_text()) if (SOURCE / "release.json").is_file() else None
INSTALL_PREFIX = SOURCE.parent.parent if RELEASE is not None else None
INSTALL_CONFIG = Path(os.environ.get("ALTITUDE_CONFIG", HOME / ".config/altitude/install.json")).expanduser()
if RELEASE is not None and INSTALL_CONFIG.exists():
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
# Incident issue target, set only in altd's environment. Unset keeps incidents on this machine.
UPSTREAM_ISSUE_REPOSITORY = os.environ.get("ALTITUDE_UPSTREAM_ISSUE_REPOSITORY")
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
HOST = os.environ.get("ALTITUDE_HOST", "127.0.0.1")
PORT = int(os.environ.get("ALTITUDE_PORT", "8890"))
# The projects folder's initial value; `alt machine set --projects-folder` replaces it (project_roots()).
PROJECT_ROOTS = [Path(p).expanduser() for p in os.environ.get("ALTITUDE_ROOTS", str(HOME / "Projects")).split(":")]
TLS_DIR = Path(os.environ.get("ALTITUDE_TLS_DIR", HOME / ".config/altitude/tls")).expanduser()
TLS = os.environ.get("ALTITUDE_TLS", "1") != "0"


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
#: The operator seam: the one configured name the UI shows where a name is shown.
OPERATOR = os.environ.get("ALTITUDE_OPERATOR") or "Operator"
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
EFFORT_SETTINGS = ("l3_effort", "l2_effort")
MODEL_ALIASES = ("opus", "sonnet", "haiku", "fable")


def model_family(name: str | None) -> str | None:
    """The Claude alias a model id or display name belongs to ("claude-fable-5-1", "Fable" -> "fable")."""
    words = re.findall(r"[a-z]+", str(name or "").lower())
    return next((alias for alias in MODEL_ALIASES if alias in words), None)


def model_setting(role: str, engine: str) -> str:
    """Project registry key holding a role's default model on one engine."""
    return f"{role}_model" if engine == "claude" else f"{role}_{engine}_model"


MODEL_SETTINGS = tuple(model_setting("l2", engine) for engine in ENGINES)
PROJECT_SETTINGS = ("routing", *EFFORT_SETTINGS, *MODEL_SETTINGS)
WIP_PER_MACHINE = 80
L3_TURN_TIMEOUT = 900             # seconds
MACHINE_COMMAND_TIMEOUT = 600     # seconds; one command under a task's machine grant
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


def validate_project_effort(entry: dict, role: str, effort: str | None) -> None:
    if f"{role}_effort" not in EFFORT_SETTINGS:
        raise ValueError("effort role must be l3 or l2")
    pin = pinned_option(role, entry)
    task_effort(pin["engine"] if pin else None, effort, role=role)


def valid_model(value) -> bool:
    return isinstance(value, str) and bool(value) and not any(c.isspace() for c in value)


def validate_project_model(value) -> None:
    if value is not None and not valid_model(value):
        raise ValueError("a default model is one alias or model id without spaces")


def defaults_view(name: str) -> dict:
    """Requested project defaults for the settings UI: effort per role and the L2 model per engine."""
    entry = project(name)
    labels = {"native": "Native", "xhigh": "Extra High"}
    choices = []
    for value in TASK_EFFORTS:
        supported = [e for e in ENGINES if value in ENGINE_EFFORTS.get(e, ())]
        if supported:
            suffix = "" if len(supported) == len(ENGINES) else " (" + ", ".join(ENGINE_LABELS[e] for e in supported) + ")"
            choices.append({"value": value, "label": labels.get(value, value.title()) + suffix})
    return {"l3": entry.get("l3_effort"), "l2": entry.get("l2_effort"), "choices": choices,
            "defaults": {"l3": "Native", "l2": "; ".join(
                f"{ENGINE_LABELS[e]}: {task_effort(e, None) or 'native'}" for e in ENGINES)},
            "models": {e: {"label": ENGINE_LABELS[e], "value": entry.get(model_setting("l2", e)),
                           "default": default_model("l2", e) or "native",
                           "choices": list(MODEL_ALIASES) if e == "claude" else []} for e in ENGINES}}


def subprocess_env() -> dict[str, str]:
    """Keep an explicit Node; otherwise expose the installed nvm default without shell profiles."""
    env = dict(os.environ)
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
    return [Path(folder)] if folder else PROJECT_ROOTS


def validate_projects_folder(value) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError("the projects folder must be an absolute path")
    if not Path(value).is_dir():
        raise ValueError(f"{value} is not a directory")
    if not os.access(value, os.R_OK | os.X_OK):
        raise ValueError(f"{value} is not readable")


def machine_wip() -> int:
    return machine_settings().get("wip", WIP_PER_MACHINE)


# The capability seam for speech: the browser's own recognition needs nothing installed; the local
# speech service and a transcription endpoint are the machine's explicit choices.
VOICE_BACKENDS = ("browser", "local")
VOICE_DEFAULT_MODEL = "whisper-1"


def voice_setting() -> dict:
    """The transcription backend: browser recognition (default), the local speech service, or one endpoint."""
    value = machine_settings().get("voice", "browser")
    if isinstance(value, dict):
        return {"backend": "endpoint", "model": VOICE_DEFAULT_MODEL, **value}
    return {"backend": value}


def validate_voice(value) -> None:
    if value is None or value in VOICE_BACKENDS:
        return
    if not isinstance(value, dict) or not value or set(value) - {"url", "model", "key"}:
        raise ValueError("voice must be browser, local, or an endpoint with a URL")
    url = value.get("url")
    parts = urlsplit(url) if isinstance(url, str) else None
    if parts is None or parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("the voice endpoint must be an http(s) URL")
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        raise ValueError("the voice endpoint URL carries no credentials, query or fragment; give the key separately")
    for field in ("model", "key"):
        if field in value and (not isinstance(value[field], str) or not value[field].strip()):
            raise ValueError(f"the voice endpoint {field} must be nonempty text")


def public_voice(value):
    """The voice setting as records and readouts show it: an endpoint key is only ever 'set'."""
    if isinstance(value, dict) and "key" in value:
        return {**value, "key": "set"}
    return value


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


def default_model(role: str, engine: str, project: dict | None = None) -> str | None:
    """The project's default model on that engine, then the role default; Codex leaves it to its CLI."""
    return (project or {}).get(model_setting(role, engine)) or (MODELS[role] if engine == "claude" else None)


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
    """Fence the short activation windows (2026-09-07: running workers starved activation).

    Shared holders are dispatch, review admission, L3 and report handling, never detached workers. The exclusive
    requester checks quiet and records requested_at before another holder can enter, across processes.
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


def restart_in_progress() -> bool:
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
    if not path.is_dir():
        raise ValueError(f"{path} is not a directory")
    entry = {"path": str(path), "approval": approval,
             **{key: value for key, value in {"l2_engine": l2_engine, "l3_engine": l3_engine}.items() if value}}
    with S.project_lock(name):
        previous = load_projects().get(name)
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


def set_l3_engine(name: str, engine: str | None) -> dict:
    """Pin the project's L3 to one engine, or clear the pin with None; the next L3 turn follows it."""
    if engine and engine not in ENGINES:
        raise ValueError(f"engine must be one of {ENGINES}, not {engine!r}")
    from . import state as S
    with S.project_lock(name), edit_projects() as projects:
        if name not in projects:
            raise KeyError(f"unknown project {name!r}; register it first (alt project add)")
        task_effort(engine, projects[name].get("l3_effort"), role="l3")
        if engine:
            projects[name]["l3_engine"] = engine
        else:
            projects[name].pop("l3_engine", None)
        return projects[name]


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
