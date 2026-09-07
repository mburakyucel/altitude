"""Cross-project user-input queue, WIP summary, and digest text."""
from __future__ import annotations

from . import config, dispatch, state as S, tasks as T

def queue() -> list[dict]:
    items = []
    for p in config.load_projects():
        items += T.decisions(p)
    items.sort(key=lambda i: i.get("asked") or "")
    return items


def fyis(limit: int = 30) -> list[dict]:
    out = []
    for p in config.load_projects():
        out += T.inbox(p, limit)
    out.sort(key=lambda i: i["at"], reverse=True)
    return out[:limit]


def wip() -> dict:
    per = {p: sum(1 for t in S.list_tasks(p) if t["state"] == "running") for p in config.load_projects()}
    return {"per_project": per, "machine": sum(per.values()), "limit_project": config.WIP_PER_PROJECT, "limit_machine": config.WIP_PER_MACHINE,
            "waiting": [{"project": p, "slug": t["slug"], "why": "dispatch" if t["state"] == "queued" else "resume"}
                        for p in config.load_projects() for t in S.list_tasks(p)
                        if t["state"] == "queued" or (t["state"] == "blocked" and t.get("resume_after"))]}


def _waiting(project: str, task: dict, restart: dict | None) -> dict:
    reason, kind = "", "checkpoint"
    if task["state"] == "blocked" and not task.get("resume_after"):
        who = task.get("waiting_on") or "burak"
        kind = f"waiting-{who}"
        reason = f"waiting on {'Burak' if who == 'burak' else 'L3'}: {task.get('blocked_reason') or 'blocked'}"
    elif restart and restart.get("requested_at") and not restart.get("failed"):
        kind = "restart"
        reason = f"restart in progress since {restart['requested_at']}"
    else:
        reason = dispatch.wip_hold(project, task) or ""
        if reason.startswith("WIP limit"):
            kind = "wip"
        elif task.get("resume_after"):
            kind, reason = "checkpoint", f"resume checkpoint {task['resume_after']}"
        else:
            reason = "ready for dispatch"
    return {"project": project, "slug": task["slug"], "state": task["state"],
            "age": S.age(task.get("updated") or task.get("created") or ""),
            "kind": kind, "reason": reason, "holder": None, "files": []}


def queue_status() -> dict:
    """Every running task and every queued/blocked task with its single current wait."""
    restart = S.read_json(config.MONITOR_DIR / dispatch.RESTART_PENDING, None)
    running, waiting = [], []
    for project in config.load_projects():
        for task in S.list_tasks(project):
            if task["state"] == "running":
                since = task.get("dispatched") or task.get("updated") or task.get("created") or ""
                running.append({"project": project, "slug": task["slug"], "age": S.age(since), "since": since})
            elif task["state"] in ("queued", "blocked"):
                waiting.append(_waiting(project, task, restart))
    return {"running": running, "waiting": waiting, "restart_pending": restart}


def text() -> str:
    q = queue()
    lines = ["# Altitude digest", ""]
    if q:
        lines += [f"{len(q)} task(s) need input:"] + [f"- [{i['project']}] {i['slug']}: {i['question'][:200]}" for i in q]
    else:
        lines.append("No decisions waiting.")
    w = wip()
    lines.append(f"Running: {w['machine']} L2 task(s) — " + ", ".join(f"{p} {n}" for p, n in w["per_project"].items() if n) + ".")
    if w["waiting"]:
        lines.append("Waiting for a slot: " + ", ".join(f"{x['project']}/{x['slug']}" for x in w["waiting"]) + ".")
    f = fyis(8)
    if f:
        lines += ["", "Recent FYIs:"] + [f"- [{i['project']}] {i['text'][:200]}" for i in f]
    txt = "\n".join(lines) + "\n"
    S.atomic_write(config.DIGEST_FILE, txt)
    return txt
