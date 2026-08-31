"""Paths and settings. Everything runtime lives under ALTITUDE_HOME (default ~/.altitude)."""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

HOME = Path.home()
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
REPO = Path(__file__).resolve().parent.parent          # this checkout: personas/, rules/, schemas/, templates/, web/
PERSONAS = REPO / "personas"
RULES = REPO / "rules"
SCHEMAS = REPO / "schemas"
TEMPLATES = REPO / "templates"
WEB = REPO / "web"
WEB_DIST = REPO / "web" / "dist"
HOOKS = REPO / "hooks"

CLAUDE_BIN = os.environ.get("CLAUDE_BIN", str(HOME / ".local/bin/claude"))
CODEX_BIN = os.environ.get("CODEX_BIN", "codex")
HOST = os.environ.get("ALTITUDE_HOST", "10.88.0.1")
PORT = int(os.environ.get("ALTITUDE_PORT", "8890"))
PROJECT_ROOTS = [Path(p).expanduser() for p in os.environ.get("ALTITUDE_ROOTS", str(HOME / "Projects")).split(":")]
# TLS (decision 34): reuse the pocketbook's local CA + server cert for 10.88.0.1 when present (the phone already trusts it);
# otherwise `alt tls-init` makes an equivalent pair under ~/.altitude/tls. ALTITUDE_TLS=0 forces plain http.
_POCKETBOOK_TLS = HOME / ".local/state/tutor/tls"
TLS_DIR = Path(os.environ.get("ALTITUDE_TLS_DIR", str(_POCKETBOOK_TLS if (_POCKETBOOK_TLS / "server.crt").exists() else ROOT / "tls"))).expanduser()
TLS = os.environ.get("ALTITUDE_TLS", "1") != "0"

# Context lines per engine (decision 12, settled 2026-08-30; numbers fixed by decision 49): Claude quality degrades past
# ~25–30% of the window in Burak's experience. Every Claude 5 alias Altitude uses (opus, fable, sonnet) reports a
# 1,000,000-token window (probed 2026-08-30: result JSON `modelUsage[..].contextWindow`), so the umbrella for all Claude
# sessions is **300k**: auto-compact there (explicit `autoCompactWindow` on every launch — never a percent override on
# top of the user's setting, which had them compacting at ~90k) and rotate the L3 there. Codex compacts at its own limit.
CONTEXT_WINDOW = 1_000_000        # Claude, tokens; used to turn usage into a percentage
CONTEXT_WARN = 0.25               # Claude: warn line (monitor colour, "rotate next" for L3) — 250k
CONTEXT_ACT = 0.30                # Claude: act line — L3 rotates to a fresh session; sessions auto-compact here — 300k
AUTOCOMPACT_WINDOW = int(CONTEXT_WINDOW * CONTEXT_ACT)   # 300_000, passed as Claude Code's `autoCompactWindow`
CONTEXT_WINDOW_CODEX = 256_000
CONTEXT_WARN_CODEX = 0.80
CONTEXT_ACT_CODEX = 1.00          # native auto-compact at the limit; Altitude only watches
# Model tiers (decision 38, Burak 2026-08-30): judgement at the top (L3 = Fable, low volume), coding at least Opus,
# Fable for the hard coding (L2 per task, L1 per sub-brief — dynamic), research/docs on Sonnet or Opus, never Fable.
MODELS = {"l3": "fable", "l2": "opus", "l2_hard": "fable", "l1": "opus", "l1_hard": "fable",
          "reviewer": "opus", "proposal": "opus", "research": "sonnet",
          "l1_codex": None, "reviewer_codex": None}   # None = the Codex CLI's configured default model
ENGINES = ("claude", "codex")
# Reasoning effort per Codex role (`-c model_reasoning_effort=`); None = the Codex CLI's configured default
# (~/.codex/config.toml: gpt-5.6-sol, xhigh as of 2026-08-30). Claude effort comes from ~/.claude/settings.json
# `modelSettings` (fable xhigh, opus high) — it applies to every session Altitude launches.
CODEX_EFFORT = {"implementer": None, "reviewer": None, "critic": None, "proposal": None, "sizer": None, "l3": None}
L1_DEFAULT_ENGINE = os.environ.get("ALTITUDE_L1_ENGINE", "codex")   # decision 45: when neither quota is known, Codex carries coding (Burak 2026-08-30)
L1_TIMEOUT = 3600                # one L1 run, either engine
L1_MAX_TURNS = 80                # Claude L1s
MODEL_ALIASES = ("opus", "sonnet", "haiku", "fable")
CONTEXT_LINES = {"claude": (CONTEXT_WARN, CONTEXT_ACT, CONTEXT_WINDOW), "codex": (CONTEXT_WARN_CODEX, CONTEXT_ACT_CODEX, CONTEXT_WINDOW_CODEX)}
QUOTA_RESERVE = 0.70              # decision 31: hold dispatch when the 5h window is past this
WIP_PER_PROJECT = 3               # decision 21
WIP_PER_MACHINE = 10              # Burak 2026-08-30: 10 concurrent tasks (decision 51 addendum)
SESSIONS_PER_MACHINE = 24         # decision 39: live Claude sessions (L2s + their L1s) across all projects; 10 L2s + their L1s + L3/critic turns
SERVICE_PORTS = (8890, 8080, 8443)  # altd, pocketbook — never bound by an L2/L1 (hooks/guard.py)
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
