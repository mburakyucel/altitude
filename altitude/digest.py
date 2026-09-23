"""Cross-project user-input queue, WIP summary, and digest text."""
from __future__ import annotations

from . import config, dispatch, route, state as S, tasks as T

def queue() -> list[dict]:
    items = []
    for p in config.load_projects():
        items += T.decisions(p)
    items.sort(key=lambda i: i.get("asked") or "")
    return items


def wip() -> dict:
    """The running counts and every task waiting for a slot, each with its hold (the queued task card names
    it, SPEC.md §3.5): the WIP limit, engine availability, a pending activation, or a resume checkpoint."""
    per = {p: sum(dispatch.occupies_slot(t) for t in S.list_tasks(p)) for p in config.load_projects()}
    restart = S.read_json(config.MONITOR_DIR / dispatch.RESTART_PENDING, None)
    return {"per_project": per, "machine": sum(per.values()),
            "limit_machine": config.machine_wip(),
            "waiting": [{"project": p, "slug": t["slug"], "why": "planned" if t.get("planned_wait") else "dispatch" if t["state"] == "queued" else "resume",
                         "hold": _waiting(p, t, restart)["reason"]}
                        for p in config.load_projects() for t in S.list_tasks(p)
                        if t["state"] == "queued" or (t["state"] == "blocked" and t.get("resume_after"))]}


def _waiting(project: str, task: dict, restart: dict | None) -> dict:
    reason, kind = "", "checkpoint"
    if task.get("planned_wait"):
        kind, reason = "planned", f"waits for {task['planned_wait']['reason']}"
    elif task["state"] == "blocked" and not task.get("resume_after"):
        kind, label = T.block_status(task)
        reason = f"{label}: {task.get('blocked_reason') or 'blocked'}"
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
            choice = route.pick_task(config.project(project), task)
            if choice.get("engine"):
                reason = "ready for dispatch"
            else:
                kind, reason = "engine", f"engine hold: {choice['why']}"
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
        lines += [f"- {x['project']}/{x['slug']}: {x['hold']}" for x in w["waiting"]]
    txt = "\n".join(lines) + "\n"
    S.atomic_write(config.DIGEST_FILE, txt)
    return txt
