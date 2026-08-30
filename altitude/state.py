"""Files, one writer, atomic. Task folders, status.json, events.log, STATE.md regeneration."""
from __future__ import annotations
import fcntl
import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import config

STATES = ("requested", "proposed", "approved", "running", "reported", "done", "rejected", "parked", "blocked")
OPEN_STATES = ("requested", "proposed", "approved", "running", "reported", "blocked")
CLASSES = ("S", "M", "L")


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def atomic_write(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: Path, default=None):
    """Missing file → default. A corrupt file raises (decision 36): silently defaulting would hide a real fault."""
    try:
        text = Path(path).read_text()
    except FileNotFoundError:
        return default
    try:
        return json.loads(text) if text.strip() else default
    except ValueError as e:
        raise ValueError(f"corrupt JSON in {path}: {e}") from e


def write_json(path: Path, obj) -> None:
    atomic_write(path, json.dumps(obj, indent=2, sort_keys=True) + "\n")


@contextmanager
def project_lock(project: str):
    """One writer per project across processes (server and `alt` CLI)."""
    d = config.project_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    with open(d / ".lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def slugify(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:40].rstrip("-") or "task"


# ---- task folders -----------------------------------------------------------

def tasks_dir(project: str) -> Path:
    return config.project_dir(project) / "tasks"


def archive_dir(project: str) -> Path:
    return config.project_dir(project) / "archive"


def task_dir(project: str, slug: str) -> Path:
    d = tasks_dir(project) / slug
    if d.is_dir():
        return d
    a = archive_dir(project) / slug
    return a if a.is_dir() else d


def status_path(project: str, slug: str) -> Path:
    return task_dir(project, slug) / "status.json"


def load_task(project: str, slug: str) -> dict:
    t = read_json(status_path(project, slug))
    if not t:
        raise KeyError(f"no task {slug!r} in {project!r}")
    return t


def save_task(project: str, task: dict) -> None:
    task["updated"] = now()
    write_json(status_path(project, task["slug"]), task)


def list_tasks(project: str, include_archive: bool = False) -> list[dict]:
    out = []
    dirs = [tasks_dir(project)] + ([archive_dir(project)] if include_archive else [])
    for d in dirs:
        if not d.is_dir():
            continue
        for td in sorted(d.iterdir()):
            t = read_json(td / "status.json")
            if t:
                out.append(t)
    return out


def append_event(project: str, slug: str, kind: str, **data) -> dict:
    ev = {"at": now(), "kind": kind, **data}
    p = task_dir(project, slug) / "events.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        f.write(json.dumps(ev, sort_keys=True) + "\n")
    return ev


def read_events(project: str, slug: str) -> list[dict]:
    p = task_dir(project, slug) / "events.log"
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def project_log(project: str, kind: str, **data) -> None:
    """Project-level events (ideas, rotations, audits, holds) — not tied to a task."""
    p = config.project_dir(project) / "events.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        f.write(json.dumps({"at": now(), "kind": kind, **data}, sort_keys=True) + "\n")


def read_project_log(project: str, limit: int = 200) -> list[dict]:
    p = config.project_dir(project) / "events.log"
    if not p.exists():
        return []
    lines = p.read_text().splitlines()[-limit:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


# ---- STATE.md: the L3's memory, regenerated from status.json ----------------

def age(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return "?"
    s = int(time.time() - dt.timestamp())
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


def regen_state_md(project: str) -> str:
    tasks = list_tasks(project)
    by = {s: [t for t in tasks if t["state"] == s] for s in STATES}
    lines = [f"# STATE — {project}", "",
             f"*Regenerated {now()} from `status.json` files. Never edit by hand; never trust memory over this file.*", ""]
    pending = [t for t in by["proposed"] if t.get("decision")]
    lines += ["## Decisions waiting on Burak", ""]
    lines += [f"- **{t['slug']}** ({t['class']}, {age(t['updated'])}): {t['decision'].get('question', '')[:200]}" for t in pending] or ["- none"]
    lines += ["", "## Tasks", ""]
    for s in ("blocked", "running", "reported", "approved", "proposed", "requested", "parked"):
        ts = by[s]
        if not ts:
            continue
        lines.append(f"### {s} ({len(ts)})")
        for t in ts:
            extra = []
            if t.get("dispatch_id"):
                extra.append(t["dispatch_id"])
            if t.get("prs"):
                extra.append("PRs " + ", ".join(str(p) for p in t["prs"]))
            if t.get("blocked_reason"):
                extra.append("blocked: " + t["blocked_reason"][:120])
            sp = t.get("spend") or {}
            if sp.get("turns"):
                extra.append(f"turns {sp['turns']}/{(t.get('envelope') or {}).get('max_turns', '?')}")
            lines.append(f"- **{t['slug']}** [{t['class']}] {t['title']} — {age(t['updated'])}" + (" — " + "; ".join(extra) if extra else ""))
        lines.append("")
    done = [t for t in list_tasks(project, include_archive=True) if t["state"] in ("done", "rejected")][-10:]
    lines += ["## Recently finished", ""] + ([f"- {t['slug']} [{t['class']}] {t['state']} — {t['title']}" for t in done] or ["- none"])
    inc = config.project_dir(project) / "incidents.jsonl"
    if inc.exists():
        recent = [json.loads(l) for l in inc.read_text().splitlines()[-5:] if l.strip()]
        lines += ["", "## Recent incidents", ""] + [f"- {i.get('id')}: {i.get('title', '')[:120]} → {i.get('rule') or 'incident-only'}" for i in recent]
    text = "\n".join(lines) + "\n"
    atomic_write(config.project_dir(project) / "STATE.md", text)
    return text
