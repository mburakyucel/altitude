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

STATES = ("queued", "running", "reported", "done", "rejected", "blocked")
OPEN_STATES = ("queued", "running", "reported", "blocked")


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _fsync_directory(directory: Path) -> None:
    """Flush the directory entry so a completed rename survives power loss."""
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


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
        _fsync_directory(path.parent)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: Path, default=None):
    """A missing file returns the default; corrupt state raises instead of hiding a fault."""
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


def session_key(project: str, slug: str, attempt: int) -> str:
    """One name per task attempt, shared by the L2's environment and the files written under it."""
    return f"{project}--{slug}-{attempt}"


def counts_path(project: str, task: dict) -> Path | None:
    """The edit counter `hooks/edit_count.py` keeps for the task's current attempt."""
    if not task.get("attempt"):
        return None
    return config.MONITOR_DIR / f"counts-{session_key(project, task['slug'], task['attempt'])}.json"


def slugify(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return s[:40].rstrip("-") or "task"


def require_task_slug(slug: str) -> str:
    """Accept only the flat identifiers created by ``slugify``.

    Task identifiers cross several CLI and daemon boundaries. Keeping the check in the path resolver means no
    caller can turn one project's lock into authority over a sibling project with ``..`` or a path separator.
    """
    if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?", slug):
        raise ValueError(f"invalid task slug {slug!r}")
    return slug


# ---- task folders -----------------------------------------------------------

def tasks_dir(project: str) -> Path:
    return config.project_dir(project) / "tasks"


def archive_dir(project: str) -> Path:
    return config.project_dir(project) / "archive"


def task_dir(project: str, slug: str) -> Path:
    slug = require_task_slug(slug)
    d = tasks_dir(project) / slug
    if d.is_dir():
        return d
    a = archive_dir(project) / slug
    return a if a.is_dir() else d


def status_path(project: str, slug: str) -> Path:
    return task_dir(project, slug) / "status.json"


class TaskNotFound(KeyError):
    """The resolved task status is absent, including an archive crossing its read."""


def load_task(project: str, slug: str) -> dict:
    t = read_json(status_path(project, slug))
    if not t:
        raise TaskNotFound(f"no task {slug!r} in {project!r}")
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
        f.flush()
        os.fsync(f.fileno())
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
    """Project-level events such as L3 rotations and incidents, not tied to a task."""
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
    from . import incidents
    tasks = list_tasks(project)
    by = {s: [t for t in tasks if t["state"] == s] for s in STATES}
    lines = [f"# STATE — {project}", "",
             f"*Regenerated {now()} from `status.json` files. Never edit by hand; never trust memory over this file.*", ""]
    pending = [(t, q) for t in tasks for q in t.get("questions", []) if q["status"] == "open"]
    legacy = [t for t in by["blocked"] if not t.get("questions") and not t.get("resume_after")]
    for audience, heading in (("operator", "Needs user input"), ("l3", "Needs L3 input")):
        lines += [f"## {heading}", ""]
        lines += ([f"- **{t['slug']}** ({age(t['updated'])}; question {q['id']} revision {q['revision']}): "
                   f"{q['question'][:200]}"
                   + (f" Recommended: {q['recommendation']['text'][:200]}" if q.get("recommendation") else "")
                   for t, q in pending if q["audience"] == audience]
                  + [f"- **{t['slug']}** ({age(t['updated'])}): {str(t.get('blocked_reason') or 'blocked')[:200]}"
                     for t in legacy if (t.get("waiting_on") == "l3") == (audience == "l3")]) or ["- none"]
        lines.append("")
    lines += ["", "## Tasks", ""]
    for s in ("blocked", "running", "reported", "queued"):
        ts = by[s]
        if not ts:
            continue
        lines.append(f"### {s} ({len(ts)})")
        for t in ts:
            extra = []
            if t.get("planned_wait"):
                extra.append("Planned: waits for " + t["planned_wait"]["reason"])
            if t.get("attempt"):
                extra.append(f"attempt {t['attempt']}")
            if t.get("prs"):
                extra.append("PRs " + ", ".join(str(p) for p in t["prs"]))
            if t.get("blocked_reason"):
                extra.append("blocked: " + t["blocked_reason"][:120])
            sp = t.get("spend") or {}
            if sp.get("turns"):
                extra.append(f"turns {sp['turns']}")
            lines.append(f"- **{t['slug']}** {t['title']} — {age(t['updated'])}" + (" — " + "; ".join(extra) if extra else ""))
        lines.append("")
    if summary := incidents.open_summary(project):
        lines += ["## Open incidents", "", summary, ""]
    text = "\n".join(lines) + "\n"
    atomic_write(config.project_dir(project) / "STATE.md", text)
    return text
