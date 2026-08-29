"""Paths and settings. Everything runtime lives under ALTITUDE_HOME (default ~/.altitude)."""
from __future__ import annotations
import json
import os
from pathlib import Path

HOME = Path.home()
ROOT = Path(os.environ.get("ALTITUDE_HOME", HOME / ".altitude"))
REPO = Path(__file__).resolve().parent.parent          # this checkout: personas/, rules/, schemas/, templates/, web/
PERSONAS = REPO / "personas"
RULES = REPO / "rules"
SCHEMAS = REPO / "schemas"
TEMPLATES = REPO / "templates"
WEB = REPO / "web"
HOOKS = REPO / "hooks"

CLAUDE_BIN = os.environ.get("CLAUDE_BIN", str(HOME / ".local/bin/claude"))
CODEX_BIN = os.environ.get("CODEX_BIN", "codex")
HOST = os.environ.get("ALTITUDE_HOST", "10.88.0.1")
PORT = int(os.environ.get("ALTITUDE_PORT", "8890"))
PROJECT_ROOTS = [Path(p).expanduser() for p in os.environ.get("ALTITUDE_ROOTS", str(HOME / "Projects")).split(":")]

CONTEXT_WINDOW = 200_000          # tokens; used to turn usage into a percentage
CONTEXT_WARN = 0.55               # decision 12
CONTEXT_ACT = 0.70
QUOTA_RESERVE = 0.70              # decision 31: hold dispatch when the 5h window is past this
WIP_PER_PROJECT = 3               # decision 21
WIP_PER_MACHINE = 8
L3_TURN_TIMEOUT = 900             # seconds
AGENT_POLL_SECONDS = 30

PROJECTS_FILE = ROOT / "projects.json"
MONITOR_DIR = ROOT / "monitor"
INCIDENT_INDEX = ROOT / "incidents.jsonl"
DIGEST_FILE = ROOT / "DIGEST.md"


def ensure_root() -> None:
    for d in (ROOT, MONITOR_DIR, ROOT / "hooks"):
        d.mkdir(parents=True, exist_ok=True)
    if not PROJECTS_FILE.exists():
        PROJECTS_FILE.write_text("{}\n")


def load_projects() -> dict:
    ensure_root()
    try:
        return json.loads(PROJECTS_FILE.read_text() or "{}")
    except ValueError:
        return {}


def save_projects(projects: dict) -> None:
    from .state import atomic_write
    atomic_write(PROJECTS_FILE, json.dumps(projects, indent=2, sort_keys=True) + "\n")


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
