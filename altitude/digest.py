"""Cross-project user-input queue, WIP summary, and digest text."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import config, dispatch, l3, route, state as S, tasks as T

# L3 reads a block notification in about a minute (95% within five), and an owner it answers resumes to
# resolve the question; a decision still waiting after this alerts even while either of them stalls.
ALERT_HOLD = timedelta(minutes=15)

def queue() -> list[dict]:
    """Every decision on the operator's turn, oldest first. Needs you shows each at once; one whose task is
    still moving (L3 reading its block notification, or its owner running or due to resume) carries
    `alert_held` and wakes no device until the task rests or ALERT_HOLD passes, so a member L3 settles
    never alerts."""
    items = []
    since = datetime.now(timezone.utc) - ALERT_HOLD
    for p in config.load_projects():
        moving = l3.reading_blocks(p) | {t["slug"] for t in S.list_tasks(p)
                                         if t["state"] == "running" or t.get("resume_after")}
        for row in T.decisions(p):
            if row["slug"] in moving and row.get("asked") and datetime.fromisoformat(row["asked"]) > since:
                row["alert_held"] = True
            items.append(row)
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
        kind, label = T.block_status(project, task)
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
        lines += [f"{len(q)} item(s) need input:"] + [f"- [{i['project']}] {i['slug']}: {i['question'][:200]}" for i in q]
    else:
        lines.append("No decisions waiting.")
    w = wip()
    lines.append(f"Running: {w['machine']} L2 task(s) — " + ", ".join(f"{p} {n}" for p, n in w["per_project"].items() if n) + ".")
    if w["waiting"]:
        lines += [f"- {x['project']}/{x['slug']}: {x['hold']}" for x in w["waiting"]]
    txt = "\n".join(lines) + "\n"
    S.atomic_write(config.DIGEST_FILE, txt)
    return txt
