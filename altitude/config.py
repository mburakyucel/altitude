"""Paths and settings. Everything runtime lives under ALTITUDE_HOME (default ~/.altitude)."""
from __future__ import annotations
import json
import fcntl
import logging
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

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
# Product issue target, set only in altd's environment. Unset uses this installation's origin.
UPSTREAM_ISSUE_REPOSITORY = os.environ.get("ALTITUDE_UPSTREAM_ISSUE_REPOSITORY")
# Operator decision 2026-09-09 Pacific: this project's hosted CI is suspended.
# Remove this exception when restoring its workflow; other projects retain their gates.
LOCAL_CHECK_REPOSITORY = "mburakyucel/altitude"
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
PROJECT_ROOTS = [Path(p).expanduser() for p in os.environ.get("ALTITUDE_ROOTS", str(HOME / "Projects")).split(":")]
TLS_DIR = Path(os.environ.get("ALTITUDE_TLS_DIR", HOME / ".config/altitude/tls")).expanduser()
TLS = os.environ.get("ALTITUDE_TLS", "1") != "0"


def installation_environment() -> dict[str, str]:
    """Persist application choices, excluding transient actor/session authority."""
    return {"ALTITUDE_HOME": str(ROOT), "ALTITUDE_HOST": HOST, "ALTITUDE_PORT": str(PORT),
            "ALTITUDE_TLS_DIR": str(TLS_DIR), "ALTITUDE_TLS": "1", "PATH": os.environ.get("PATH", ""),
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
#: Display names, the way the shell shows an engine; nothing outside the seam spells one.
ENGINE_LABELS = {"claude": "Claude", "codex": "Codex"}
#: The operator seam: the one configured name the UI shows where a name is shown.
OPERATOR = os.environ.get("ALTITUDE_OPERATOR") or "Operator"
PRIMARY_DEFAULT_ENGINE = os.environ.get("ALTITUDE_PRIMARY_ENGINE", "codex")
# Ordered tiers; order within a tie settles unknown/equal weekly headroom.
AUTO_ROUTING = [[{"engine": PRIMARY_DEFAULT_ENGINE, "model": "fable" if PRIMARY_DEFAULT_ENGINE == "claude" else None},
                 {"engine": "claude" if PRIMARY_DEFAULT_ENGINE != "claude" else "codex",
                  "model": "fable" if PRIMARY_DEFAULT_ENGINE != "claude" else None}],
                [{"engine": "claude", "model": "opus"}]]
# L3 effort remains native; new L2 tasks explicitly override native effort configuration.
CODEX_EFFORT = {"l3": None}
TASK_EFFORTS = ("high", "xhigh")
MODEL_ALIASES = ("opus", "sonnet", "haiku", "fable")
WIP_PER_PROJECT = 8
WIP_PER_MACHINE = 80
L3_TURN_TIMEOUT = 900             # seconds
L3_CODEX_TURN_TIMEOUT = 1200
AGENT_POLL_SECONDS = 30

PROJECTS_FILE = ROOT / "projects.json"
MONITOR_DIR = ROOT / "monitor"
INCIDENT_INDEX = ROOT / "incidents.jsonl"
DIGEST_FILE = ROOT / "DIGEST.md"


def task_effort(engine: str | None, effort: str | None) -> str | None:
    """Validate the task choice; model support is decided by the native provider at launch."""
    if effort is not None and effort not in TASK_EFFORTS:
        raise ValueError("task effort must be high or xhigh")
    if effort is not None and engine is not None and engine != "codex":
        raise ValueError(f"{engine} does not support task reasoning effort; omit --effort or select a supporting engine")
    return (effort or "high") if engine == "codex" else effort


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


def machine_wip() -> int:
    return machine_settings().get("wip", WIP_PER_MACHINE)


def project_wip(name: str) -> int:
    return project(name).get("wip", WIP_PER_PROJECT)


def validate_wip(value, *, project: bool = False) -> None:
    if value is None:
        return
    if type(value) is not int or value < 1:
        raise ValueError("WIP must be a positive integer")
    if project and value > machine_wip():
        raise ValueError(f"project WIP must be between 1 and {machine_wip()} (the configured machine cap)")


def parse_routing(value: str) -> list[list[dict]]:
    """Operator syntax: comma ties options, > starts a lower priority tier."""
    tiers, seen = [], set()
    for tier in value.split(">"):
        options = []
        for item in tier.split(","):
            engine, separator, model = item.strip().partition(":")
            if engine not in ENGINES or separator and (not model or any(c.isspace() for c in model)):
                raise ValueError("routing needs engine[:model] options, comma ties, and > between tiers")
            key = (engine, model or None)
            if key in seen:
                raise ValueError(f"duplicate routing option: {item.strip()}")
            seen.add(key)
            options.append({"engine": engine, "model": model or None})
        tiers.append(options)
    return tiers


def default_model(role: str, engine: str) -> str | None:
    return MODELS[role] if engine == "claude" else None


def pinned_option(role: str, project: dict, *, engine: str | None = None,
                  model: str | None = None) -> dict | None:
    """Launch overrides are separate from Auto preferences and observed session models."""
    overrides = {"claude": project.get(f"{role}_model"), "codex": project.get(f"{role}_codex_model")}
    if model and not engine:
        engine = "claude" if model in MODEL_ALIASES else project.get(f"{role}_engine")
        if not engine:
            raise ValueError("a model pin requires --engine for this model")
    engine = engine or project.get(f"{role}_engine")
    if not engine and any(overrides.values()):
        choices = [key for key, value in overrides.items() if value]
        if len(choices) != 1:
            raise ValueError(f"set {role}_engine to disambiguate the project's model pins")
        engine = choices[0]
    if not engine:
        return None
    if engine not in ENGINES:
        raise ValueError(f"engine must be one of {ENGINES}, not {engine!r}")
    return {"engine": engine, "model": model or overrides.get(engine) or default_model(role, engine)}


@contextmanager
def restart_lock(*, exclusive: bool = False):
    """Fence the short activation windows (2026-09-07: running workers starved activation).

    Shared holders are dispatch, L3 and report handling, never the detached workers. The exclusive
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
    """Serialize registry edits across projects: simultaneous cap changes must not lose either edit."""
    ensure_root()
    with open(ROOT / ".projects.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _load_projects() -> dict:
    """Read under projects_lock; retire baked-in 3s once, preserving later explicit choices."""
    try:
        projects = json.loads(PROJECTS_FILE.read_text() or "{}")
    except ValueError:
        return {}
    marker = ROOT / ".project-wip-default-migrated"
    if not marker.exists():
        migrated = [name for name, entry in projects.items() if entry.get("wip") == 3]
        for name in migrated:
            projects[name].pop("wip")
        if migrated:
            save_projects(projects)
            logging.getLogger(__name__).warning("Removed legacy project WIP 3 override: %s", ", ".join(migrated))
        from .state import atomic_write
        atomic_write(marker, "Legacy WIP defaults migrated; explicit overrides now persist.\n")
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
def add_project(name: str, *, path=None, approval="default", wip=None, **pins):
    """CLI/HTTP registration, including rollback if the caller's setup fails."""
    from . import state as S
    path = Path(path or (PROJECT_ROOTS[0] / name)).expanduser()
    if not path.is_dir():
        raise ValueError(f"{path} is not a directory")
    validate_wip(wip, project=True)
    entry = {"path": str(path), "approval": approval, **({"wip": wip} if wip is not None else {}),
             **{key: value for key, value in pins.items() if value}}
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
    """Every folder under the roots, marked managed/unmanaged."""
    managed = load_projects()
    by_path = {str(Path(v["path"]).expanduser().resolve()): k for k, v in managed.items()}
    out, seen = [], set()
    for root in PROJECT_ROOTS:
        if not root.is_dir():
            continue
        for p in sorted(root.iterdir()):
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
